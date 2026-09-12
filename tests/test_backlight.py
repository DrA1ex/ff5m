## Behavioral tests for the backlight command line.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import contextlib
import importlib.util
import io
import pathlib
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]
BACKLIGHT = ROOT / ".py" / "backlight.py"


def load_backlight():
    spec = importlib.util.spec_from_file_location("backlight", BACKLIGHT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BacklightArgumentsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backlight = load_backlight()

    def test_no_argument_queries_current_brightness(self):
        self.assertIsNone(self.backlight.parse_args([]))

    def test_numeric_brightness_accepts_complete_range(self):
        self.assertEqual(self.backlight.parse_args(["0"]), 0.0)
        self.assertEqual(self.backlight.parse_args(["37.5"]), 37.5)
        self.assertEqual(self.backlight.parse_args(["100"]), 100.0)

    def test_invalid_brightness_is_rejected_by_cli_parser(self):
        for value in ("invalid", "-1", "101", "nan"):
            with self.subTest(value=value):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        self.backlight.parse_args([value])

    def test_numeric_main_applies_parsed_brightness(self):
        with mock.patch.object(self.backlight, "backlight_enable") as enable:
            with mock.patch.object(self.backlight, "backlight_set") as set_value:
                self.backlight.main(["37.5"])

        enable.assert_called_once_with()
        set_value.assert_called_once_with(37.5)


if __name__ == "__main__":
    unittest.main()
