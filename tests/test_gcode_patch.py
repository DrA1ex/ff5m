## Tests for Forge-X changes to Klipper's G-code parser.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import importlib.util
import pathlib
import unittest


MODULE_PATH = (pathlib.Path(__file__).parents[1] / ".py" / "klipper" /
               "patches" / "gcode.py")
SPEC = importlib.util.spec_from_file_location("forge_x_gcode", MODULE_PATH)
GCODE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GCODE)


class RecordingMutex:
    def __init__(self):
        self.entries = 0

    def __enter__(self):
        self.entries += 1

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class ImmediateCommandDispatchTest(unittest.TestCase):
    def make_dispatch(self):
        dispatch = GCODE.GCodeDispatch.__new__(GCODE.GCodeDispatch)
        dispatch.mutex = RecordingMutex()
        dispatch.immediate = []
        dispatch.normal = []
        dispatch.run_script_from_command = dispatch.immediate.append
        dispatch._process_commands = (
            lambda lines, need_ack=False: dispatch.normal.extend(lines))
        return dispatch

    def test_immediate_only_script_never_enters_mutex(self):
        dispatch = self.make_dispatch()

        dispatch.run_script("  M108  \n\n")

        self.assertEqual(dispatch.immediate, ["  M108  "])
        self.assertEqual(dispatch.normal, [])
        self.assertEqual(dispatch.mutex.entries, 0)

    def test_multiple_immediate_commands_are_not_skipped(self):
        dispatch = self.make_dispatch()

        dispatch.run_script("M108\nBEEP\nG28\nTONE S=1")

        self.assertEqual(dispatch.immediate,
                         ["M108", "BEEP", "TONE S=1"])
        self.assertEqual(dispatch.normal, ["G28"])
        self.assertEqual(dispatch.mutex.entries, 1)


if __name__ == "__main__":
    unittest.main()
