import pcbnew
import wx
import os
import re
import sys
import json
import logging
from copy import deepcopy
from .deserialize import deserialize
# from .serialize import *
from .kle_placer_utils import Keyboard, read_file, sort_keys_kle_placer, min_x_y, check_multilayout_keys

class KeyAutoPlaceDialog(wx.Dialog):
    def __init__(self, parent, title, caption):
        style = wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER
        super(KeyAutoPlaceDialog, self).__init__(parent, -1, title, style=style)

        # File select
        layout_select_box = wx.BoxSizer(wx.HORIZONTAL)

        text = wx.StaticText(self, -1, "Select kle json file:")
        layout_select_box.Add(text, 0, wx.LEFT|wx.RIGHT|wx.ALIGN_CENTER_VERTICAL, 5)

        layout_file_picker = wx.FilePickerCtrl(self, -1)
        layout_select_box.Add(layout_file_picker, 1, wx.EXPAND|wx.ALL, 5)

        # Key format
        key_format_box = wx.BoxSizer(wx.HORIZONTAL)

        key_annotation_label = wx.StaticText(self, -1, "Key annotation format string:")
        key_format_box.Add(key_annotation_label, 1, wx.LEFT|wx.RIGHT|wx.ALIGN_CENTER_VERTICAL, 5)

        key_annotation_format = wx.TextCtrl(self, value='SW{}')
        key_format_box.Add(key_annotation_format, 1, wx.EXPAND|wx.ALL, 5)

        # Stab format
        stab_format_box = wx.BoxSizer(wx.HORIZONTAL)

        stabilizer_annotation_label = wx.StaticText(self, -1, "Stabillizer annotation format string:")
        stab_format_box.Add(stabilizer_annotation_label, 1, wx.LEFT|wx.RIGHT|wx.ALIGN_CENTER_VERTICAL, 5)

        stabilizer_annotation_format = wx.TextCtrl(self, value='S{}')
        stab_format_box.Add(stabilizer_annotation_format, 1, wx.EXPAND|wx.ALL, 5)

        # Diode format
        diode_format_box = wx.BoxSizer(wx.HORIZONTAL)

        diodeAnnotationLabel = wx.StaticText(self, -1, "Diode annotation format string (NOT IMPLEMENTED YET):")
        diode_format_box.Add(diodeAnnotationLabel, 1, wx.LEFT|wx.RIGHT|wx.ALIGN_CENTER_VERTICAL, 5)

        diode_annotation_format = wx.TextCtrl(self, value='D{}')
        diode_format_box.Add(diode_annotation_format, 1, wx.EXPAND|wx.ALL, 5)

        # Final setup of box
        box = wx.BoxSizer(wx.VERTICAL)

        box.Add(layout_select_box, 0, wx.EXPAND|wx.ALL, 5)
        box.Add(key_format_box, 0, wx.EXPAND|wx.ALL, 5)
        box.Add(stab_format_box, 0, wx.EXPAND|wx.ALL, 5)
        box.Add(diode_format_box, 0, wx.EXPAND|wx.ALL, 5)

        buttons = self.CreateButtonSizer(wx.OK|wx.CANCEL)
        box.Add(buttons, 0, wx.EXPAND|wx.ALL, 5)

        self.SetSizerAndFit(box)
        self.layout_file_picker = layout_file_picker
        self.key_annotation_format = key_annotation_format
        self.stabilizer_annotation_format = stabilizer_annotation_format
        self.diode_annotation_format = diode_annotation_format

    def get_layout_path(self):
        return self.layout_file_picker.GetPath()

    def get_key_annotation_format(self):
        return self.key_annotation_format.GetValue()

    def get_stabilizer_annotation_format(self):
        return self.stabilizer_annotation_format.GetValue()

    def get_diode_annotation_format(self):
        return self.diode_annotation_format.GetValue()


class BoardModifier():
    def __init__(self, logger, board):
        self.logger = logger
        self.board = board

    def mm_to_nm(v):
        return int(v * 1000000)
    
    def nm_to_mm(v):
        return v / 1000000.0

    def get_footprint(self, reference, required=True):
        self.logger.info("Searching for {} footprint".format(reference))
        footprint = self.board.FindFootprintByReference(reference)
        if footprint == None and required:
            self.logger.error("Footprint not found")
            raise Exception("Cannot find footprint {}".format(reference))
        return footprint

    def set_position(self, footprint, position):
        self.logger.info("Setting {} footprint position: {}".format(footprint.GetReference(), position))
        footprint.SetPosition(position)

    def set_relative_position_mm(self, footprint, referencePoint, direction):
        position = pcbnew.wxPoint(referencePoint.x + pcbnew.FromMM(direction[0]), referencePoint.y + pcbnew.FromMM(direction[1]))
        self.set_position(footprint, position)

    def rotate(self, footprint, rotationReference, angle):
        self.logger.info("Rotating {} footprint: rotationReference: {}, rotationAngle: {}".format(footprint.GetReference(), rotationReference, angle))
        footprint.Rotate(rotationReference, angle * -10)


class KeyPlacer(BoardModifier):
    def __init__(self, logger, board, layout):
        super().__init__(logger, board)
        self.layout: Keyboard = layout
        self.key_distance = 19050000
        self.current_key = 1
        self.current_diode = 1
        self.reference_coordinate = pcbnew.wxPoint(pcbnew.FromMM(25), pcbnew.FromMM(25))

    def get_current_key(self, key_format, stabilizer_format):
        key = self.get_footprint(key_format.format(self.current_key))

        # in case of perigoso/keyswitch-kicad-library, stabilizer holes are not part of of switch footprint and needs to be handled
        # separately, check if there is stabilizer with id matching current key and return it
        # stabilizer will be None if not found
        stabilizer = self.get_footprint(stabilizer_format.format(self.current_key), required=False)
        self.current_key += 1

        return key, stabilizer

    def get_current_diode(self, diode_format):
        diode = self.get_footprint(diode_format.format(self.current_diode))
        self.current_diode += 1
        return diode

    def squish_kbd_multilayout(self):
        kbd = deepcopy(self.layout)
        ml_keys = check_multilayout_keys(kbd)

        # This list will replace kbd.keys later
        # It is a list with only the keys to be included in the info.json
        temp_layout = [] 
        # Add non-multilayout keys to the list for now
        for key in [k for k in kbd.keys if k not in ml_keys]:
            temp_layout.append(key)


        # Generate a dict of all multilayouts
        # E.g. Used to test and figure out the multilayout value with the maximum amount of keys
        ml_dict = {}
        for key in [k for k in kbd.keys if k in ml_keys]:
            ml_ndx = int(key.labels[3])
            ml_val = int(key.labels[5])

            # Create dict with multilayout index if it doesn't exist
            if not ml_ndx in ml_dict.keys():
                ml_dict[ml_ndx] = {}

            # Create dict with multilayout value if it doesn't exist
            # Also create list of keys if it doesn't exist
            if not ml_val in ml_dict[ml_ndx].keys():
                ml_dict[ml_ndx][ml_val] = []

            # Add key to dict if not in already
            if not key in ml_dict[ml_ndx][ml_val]:
                ml_dict[ml_ndx][ml_val].append(key)


        # Iterate over multilayout keys
        for key in [k for k in kbd.keys if k in ml_keys]:
            # WIP: Be able to configure this
            ml_ndx = int(key.labels[3])
            ml_val = int(key.labels[5])

            # list of all amount of keys over all val options
            ml_val_length_list = [len(ml_dict[ml_ndx][i]) for i in ml_dict[ml_ndx].keys() if isinstance(i, int)]
            max_val_len = max(ml_val_length_list) # maximum amount of keys over all val options
            current_val_len = len(ml_dict[ml_ndx][ml_val]) # amount of keys in current val
            current_is_max = max_val_len == current_val_len

            # If all multilayout values/options have the same amount of keys
            all_same_length = len(set(ml_val_length_list)) == 1

            if not "max" in ml_dict[ml_ndx].keys():
                if all_same_length:
                    ml_dict[ml_ndx]["max"] = 0 # Use the default
                elif current_is_max:
                    ml_dict[ml_ndx]["max"] = ml_val

            # If the current multilayout value/option isn't default,
            if ml_val > 0:
                # Check if there is an offsets dict
                if not "offsets" in ml_dict[ml_ndx].keys():
                    ml_dict[ml_ndx]["offsets"] = {}

                # Check if the offset for this multilayout value has been calculated yet.
                if not ml_val in ml_dict[ml_ndx]["offsets"].keys():
                    # If not, calculate and set the offset
                    xmin, ymin = min_x_y(ml_dict[ml_ndx][0])
                    x, y = min_x_y(ml_dict[ml_ndx][ml_val])

                    ml_x_offset = xmin - x
                    ml_y_offset = ymin - y

                    ml_dict[ml_ndx]["offsets"][ml_val] = (ml_x_offset, ml_y_offset)
                else:
                    # If so, just get the offset from ml_dict
                    ml_x_offset, ml_y_offset = ml_dict[ml_ndx]["offsets"][ml_val]
                
                # Offset the x and y values
                key.x += ml_x_offset
                key.y += ml_y_offset

            # Add the key to the final list
            temp_layout.append(key)

        # Offset all the remaining keys (align against the top left)
        x_offset, y_offset = min_x_y(temp_layout)
        for key in temp_layout:
            key.x -= x_offset
            key.y -= y_offset
            
            if key.rotation_angle:
                key.rotation_x -= x_offset
                key.rotation_y -= y_offset

        # Override primary layout with temporary layout
        self.layout.keys = temp_layout

        # Sort keys based on the centers of each key (by default it sorts with the top left corner)
        sort_keys_kle_placer(self.layout.keys)

    def Run(self, key_format, stabilizer_format, diode_format):

        ### First, check all the multilayouts and squish all the same multilayouts into the same position on top of one another. ###

        self.squish_kbd_multilayout()

        ### Now begin the placement of all keys based on new layout. ###

        # Set the origin as the middle of the first key
        first_key = self.get_footprint(key_format.format(1))
        first_key_pos = pcbnew.wxPoint((first_key.GetPosition().x) - ((self.key_distance * self.layout.keys[0].x) + (self.key_distance * self.layout.keys[0].width // 2)),
                (first_key.GetPosition().y) - ((self.key_distance * self.layout.keys[0].y) + (self.key_distance * self.layout.keys[0].height // 2)))
        self.reference_coordinate = first_key_pos

        column_switch_pads = {}
        row_diode_pads = {}

        for key in self.layout.keys:
            switch_footprint, stabilizer = self.get_current_key(key_format, stabilizer_format)

            width = key.width
            height = key.height
            angle = key.rotation_angle

            position = pcbnew.wxPoint((self.key_distance * key.x) + (self.key_distance * width // 2),
                (self.key_distance * key.y) + (self.key_distance * height // 2)) + self.reference_coordinate
            
            self.set_position(switch_footprint, position)
            if stabilizer:
                self.set_position(stabilizer, position)
                
                # NOT NEEDED FOR MARBASTLIB
                # # recognize special case of of ISO enter: 
                # width2 = key.width2
                # height2 = key.height2
                # if width == 1.25 and height == 2 and width2 == 1.5 and height2 == 1:
                #     stabilizer.SetOrientationDegrees(90)

            # # ROTATION SUPPORT NOT IMPLEMENTED YET
            # if angle != 0:
            #     # rotationReference = self.board.GetDesignSettings().GetAuxOrigin()
            #     # rotationReference = position - pcbnew.wxPoint((self.key_distance * width // 2), (self.key_distance * height // 2))
            #     rotationReference = pcbnew.wxPoint((self.key_distance * key.rotation_x), (self.key_distance * key.rotation_y)) + self.reference_coordinate
            #     # rotationReference = pcbnew.wxPoint((self.key_distance * key.x),
            #     #     (self.key_distance * key.y)) + self.reference_coordinate

            #     self.rotate(switch_footprint, rotationReference, angle)

            #     if stabilizer:
            #         self.rotate(stabilizer, rotationReference, angle)
                
            #     # # DEBUG:
            #     # self.set_position(switch_footprint, rotationReference)

            # DIODE SUPPORT NOT IMPLEMENTED YET
            # diode_footprint = self.get_current_diode(diode_format)
            # self.set_relative_position_mm(diode_footprint, position, [5.08, 3.03]) # be able to configure this


class KLEPlacerAction(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "KLE Placer"
        self.category = "Utility"
        self.description = "Places switches down in a kicad project based on "
        self.show_toolbar_button = True # Optional, defaults to False
        # self.icon_file_name = os.path.join(os.path.dirname(__file__), 'icon.png') # Optional

    def Initialize(self):
        self.board = pcbnew.GetBoard()

        # go to the project folder - so that log will be in proper place
        os.chdir(os.path.dirname(os.path.abspath(self.board.GetFileName())))

        # Remove all handlers associated with the root logger object.
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)

        # set up logger
        logging.basicConfig(level=logging.DEBUG,
                            filename="keyautoplace.log",
                            filemode='w',
                            format='%(asctime)s %(name)s %(lineno)d: %(message)s',
                            datefmt='%H:%M:%S')
        self.logger = logging.getLogger(__name__)
        self.logger.info("Plugin executed with python version: " + repr(sys.version))


    def Run(self):
        self.Initialize()

        pcbFrame = [x for x in wx.GetTopLevelWindows() if x.GetName() == 'PcbFrame'][0]

        dlg = KeyAutoPlaceDialog(pcbFrame, 'Title', 'Caption')
        if dlg.ShowModal() == wx.ID_OK:
            
            layout_path = dlg.get_layout_path()
            if layout_path:
                self.layout = deserialize(json.loads(read_file(layout_path)))
            
                self.logger.info("User layout: {}".format(self.layout))
                placer = KeyPlacer(self.logger, self.board, self.layout)
                placer.Run(dlg.get_key_annotation_format(), dlg.get_stabilizer_annotation_format(), dlg.get_diode_annotation_format())

        dlg.Destroy()
        logging.shutdown()