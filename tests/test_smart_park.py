## Tests for Smart Park motion safety.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).parents[1]


class SmartParkSafetyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.smart_park = (
            ROOT / "KAMP" / "Smart_Park.cfg").read_text(encoding="utf-8")
        base = (ROOT / "macros" / "base.cfg").read_text(encoding="utf-8")
        cls.move_safe = base.split(
            "[gcode_macro MOVE_SAFE]", 1)[1].split(
            "[gcode_macro _CLIENT_LINEAR_MOVE]", 1)[0]

    def test_smart_park_only_moves_through_move_safe(self):
        executable_lines = [
            line.strip() for line in self.smart_park.splitlines()
            if line.strip() and not line.lstrip().startswith(("#", "{%"))
        ]
        self.assertFalse(any(re.match(r"G[01]\\b", line)
                             for line in executable_lines))
        self.assertEqual(
            sum(line.startswith("MOVE_SAFE ") for line in executable_lines), 3)
        self.assertIn("ABSOLUTE=1", self.smart_park)

    def test_smart_park_uses_shared_limits_and_corner_fallback(self):
        self.assertIn(
            'printer["gcode_macro MOVE_SAFE"]', self.smart_park)
        self.assertIn("variable_fallback_x: 110.0", self.smart_park)
        self.assertIn("variable_fallback_y: 100.0", self.smart_park)
        self.assertIn("variable_fallback_z: 10.0", self.smart_park)
        self.assertIn(
            "smart_park.fallback_x | float", self.smart_park)
        self.assertIn(
            "smart_park.fallback_y | float", self.smart_park)
        self.assertIn("if object_valid", self.smart_park)
        self.assertIn("if not z_valid", self.smart_park)
        self.assertIn(
            'action_raise_error("SMART_PARK requires homed XYZ axes")',
            self.smart_park)

    def test_move_safe_exposes_the_limits_used_by_smart_park(self):
        expected = {
            "variable_x_min": "-110.0",
            "variable_x_max": "110.0",
            "variable_y_min": "-110.0",
            "variable_y_max": "110.0",
            "variable_z_min": "0.0",
            "variable_z_max_margin": "10.0",
        }
        for key, value in expected.items():
            self.assertIn("%s: %s" % (key, value), self.move_safe)
        self.assertIn(
            'printer["gcode_macro MOVE_SAFE"]', self.move_safe)

    def test_move_safe_calculates_targets_before_selecting_g90(self):
        target = self.move_safe.index(
            "{% set x_move = 'X' ~")
        absolute_mode = self.move_safe.index(
            "{% if x_move or y_move or z_move %}")
        self.assertLess(target, absolute_mode)


if __name__ == "__main__":
    unittest.main()
