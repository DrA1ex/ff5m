## Tests for the shared safe Z height.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).parents[1]


class SafeZContractTest(unittest.TestCase):
    def test_macro_safety_moves_use_the_shared_parameter(self):
        base = (ROOT / "macros" / "base.cfg").read_text(encoding="utf-8")
        client = (ROOT / "macros" / "client.cfg").read_text(encoding="utf-8")
        headless = (ROOT / "macros" / "headless.cfg").read_text(
            encoding="utf-8")

        self.assertGreaterEqual(
            base.count("printer.mod_params.variables.safe_z"), 8)
        self.assertIn("printer.mod_params.variables.safe_z", client)
        self.assertIn("[custom_park_dz, safe_z] | max", client)
        self.assertNotIn("variable_custom_park_dz", headless)

        literal_safe_move = re.compile(
            r"^\s*(?:G0|G1|MOVE_SAFE)\b[^\n]*\bZ(?:=)?(?:5|10)(?:\.0+)?\b",
            re.MULTILINE | re.IGNORECASE)
        self.assertEqual(literal_safe_move.findall(base), [])

    def test_python_safety_paths_do_not_restore_literal_five_mm_lifts(self):
        paths = (
            ROOT / ".py" / "klipper" / "plugins" /
            "feather_z_calibration.py",
            ROOT / ".py" / "klipper" / "plugins" / "feather_screen.py",
            ROOT / ".py" / "klipper" / "plugins" / "load_cell_tare.py",
        )
        sources = "\n".join(path.read_text(encoding="utf-8")
                            for path in paths)

        self.assertIsNone(re.search(
            r'MOVE_SAFE Z=[0-9]+(?:\.[0-9]+)? ABSOLUTE=1 F=600', sources))
        self.assertNotIn('"G1 Z10 F6000"', sources)
        self.assertIn('get("safe_z", 10.0)', sources)

if __name__ == "__main__":
    unittest.main()
