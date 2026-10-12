## Pre-print file details from slicer headers and footers.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import pathlib
import tempfile
import threading
import unittest

from tests.test_feather_model_preview import PLUGINS
from feather.gcode_info import read_gcode_info
from feather.previews import PreviewCancelled


class GcodeInfoTest(unittest.TestCase):
    def read(self, text):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "part.gcode"
            path.write_text(text)
            return read_gcode_info(path, threading.Event())

    def test_orca_footer_and_header_are_read_around_large_body(self):
        info = self.read("; total layer number: 100\n" + "G1 X20\n" * 50000 +
                         "; estimated printing time (normal mode) = 1h 2m 3s\n"
                         "; filament used [g] = 12.5\n; filament_type = PLA\n")
        self.assertEqual(info["seconds"], 3723)
        self.assertEqual(info["layers"], 100)
        self.assertEqual(info["filament"], "12.5 G PLA")

    def test_cura_seconds_dimensions_and_filament_length(self):
        info = self.read(";TIME:120\n;LAYER_COUNT:8\n;MINX:-5\n;MAXX:15\n"
                         ";MINY:10\n;MAXY:40\n;MINZ:0.2\n;MAXZ:1.8\n"
                         ";filament used = 2000mm\n")
        self.assertEqual(info["seconds"], 120)
        self.assertEqual(info["layers"], 8)
        self.assertEqual(info["dimensions"], "20x30x1.6 MM")
        self.assertEqual(info["filament"], "2.00 M")

    def test_legacy_extrusion_time_and_model_size(self):
        info = self.read("; OrcaSlicer estimated extrusion time = 2d 1h 4m\n"
                         "; model_size = 20, 30, 40\n")
        self.assertEqual(info["seconds"], 176640)
        self.assertEqual(info["dimensions"], "20x30x40 MM")

    def test_missing_or_invalid_fields_stay_unknown(self):
        text = ";TIME:nope\n;LAYER_COUNT:unknown\n;MINX:0\n;MAXX:nan\nG1 X10\n"
        info = self.read(text)
        self.assertEqual(info["size"], len(text.encode()))
        self.assertIsNone(info["seconds"])
        self.assertIsNone(info["layers"])
        self.assertIsNone(info["dimensions"])
        self.assertEqual(info["filament"], "--")

    def test_cancelled_read_has_no_result(self):
        with tempfile.NamedTemporaryFile() as stream:
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(PreviewCancelled):
                read_gcode_info(stream.name, cancel)
