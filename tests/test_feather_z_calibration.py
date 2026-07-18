## Tests for Feather Z-offset calibration state and calculations.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import importlib.util
import pathlib
import sys
import unittest


MODULE_PATH = (pathlib.Path(__file__).parents[1] / ".py" / "klipper" /
               "plugins" / "feather_z_calibration.py")
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location(
    "feather_z_calibration_test", MODULE_PATH)
ZCAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ZCAL)


class ZCalibrationStateTest(unittest.TestCase):
    def test_formula_supports_negative_configured_probe_offset(self):
        self.assertAlmostEqual(
            ZCAL.calculate_z_offset(0.125, -0.500, -0.250), 0.375)

    def test_closer_decreases_candidate_and_farther_increases_it(self):
        session = ZCAL.ZCalibrationSession()
        session.begin(0.2, None, "", -0.25, False)
        session.choose_zone("center")
        session.set_trigger(-0.5)
        initial = session.candidate

        session.adjust(-0.025)
        self.assertAlmostEqual(session.candidate, initial - 0.025)
        session.adjust(0.050)
        self.assertAlmostEqual(session.candidate, initial + 0.025)

    def test_reset_places_nozzle_at_zero_offset_position(self):
        session = ZCAL.ZCalibrationSession()
        session.begin(0.0, None, "", -0.25, False)
        session.choose_zone("center")
        session.set_trigger(-0.5)

        target = session.reset()

        self.assertAlmostEqual(target, -0.25)
        self.assertAlmostEqual(session.local_z, 0.25)
        self.assertAlmostEqual(session.candidate, 0.0)

    def test_average_is_rounded_and_remeasurement_replaces_zone(self):
        session = ZCAL.ZCalibrationSession()
        session.begin(0.0, None, "", -0.25, False)
        for zone, local in (
                ("front_left", 0.2514), ("rear_right", 0.2534)):
            session.choose_zone(zone)
            session.set_trigger(-0.5)
            session.local_z = local
            session.accept()

        self.assertEqual(session.average, 0.002)
        self.assertEqual(session.selected, "average")

        session.choose_zone("front_left")
        session.set_trigger(-0.5)
        session.local_z = 0.260
        session.accept()

        self.assertEqual(len(session.results), 2)
        self.assertEqual(session.results["front_left"], 0.010)
        self.assertEqual(session.average, 0.007)

    def test_single_zone_is_selected_automatically(self):
        session = ZCAL.ZCalibrationSession()
        session.begin(0.0, None, "", -0.25, False)
        session.choose_zone("rear_left")
        session.set_trigger(-0.5)
        session.local_z = 0.275
        session.accept()

        self.assertEqual(session.selected, "rear_left")
        self.assertEqual(session.selected_value, 0.025)

    def test_pressure_warning_uses_800_600_hysteresis_and_probe_suppression(self):
        pressure = ZCAL.PressureHysteresis()

        self.assertFalse(pressure.update(801, suppressed=True))
        self.assertTrue(pressure.update(801))
        self.assertFalse(pressure.update(900))
        self.assertFalse(pressure.update(600))
        self.assertFalse(pressure.update(599))
        self.assertTrue(pressure.update(850))


if __name__ == "__main__":
    unittest.main()
