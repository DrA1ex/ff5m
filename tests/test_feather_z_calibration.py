## Tests for Feather Z-offset calibration state and calculations.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import importlib.util
import pathlib
import shlex
import sys
import unittest
from types import SimpleNamespace

from tests.test_bed_mesh_validation import Command, Coord, GCodeMove
from tests.gcode_macro_harness import load_macro, render_macro


MODULE_PATH = (pathlib.Path(__file__).parents[1] / ".py" / "klipper" /
               "plugins" / "feather" / "calibration" / "z.py")
sys.path.insert(0, str(MODULE_PATH.parents[2]))
SPEC = importlib.util.spec_from_file_location(
    "feather_z_calibration_test", MODULE_PATH)
ZCAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ZCAL)


class PaperMotionHarness(ZCAL.FeatherZCalibrationMixin):
    """Execute calibration commands through the shipped Klipper G-code state."""
    def __init__(self):
        self.z_calibration = ZCAL.ZCalibrationSession()
        self.z_calibration.begin(0.0, None, "", -0.25, False)
        self.z_calibration.choose_zone("center")
        self.z_calibration.set_trigger(-0.5)
        self.position = [10.0, 20.0, 0.0, 3.0]
        self.moves = []
        self.fail_move = False
        self.move = GCodeMove.GCodeMove.__new__(GCodeMove.GCodeMove)
        self.move.Coord = Coord
        self.move.absolute_coord = self.move.absolute_extrude = False
        self.move.base_position = [0.0] * 4
        self.move.homing_position = [0.0] * 4
        self.move.last_position = list(self.position)
        self.move.speed = 25.0
        self.move.speed_factor = 1.0 / 60.0
        self.move.extrude_factor = 1.0
        self.move.saved_states = {}
        self.move.move_with_transform = self._move
        self.reactor = SimpleNamespace(monotonic=lambda: 0.0)
        self.probe = SimpleNamespace(
            get_status=lambda eventtime: {"last_z_result": -0.625})

    def _move(self, position, speed):
        if self.fail_move:
            raise RuntimeError("Move out of range")
        self.position[:] = position
        self.moves.append((list(position), speed))

    def _run_script(self, script, show_notice=True):
        for line in script.splitlines():
            name, *arguments = shlex.split(line)
            if name == "PROBE":
                self.position[2] = -0.625
                self.move.last_position[:] = self.position
                continue
            params = dict(word.split("=", 1) for word in arguments
                          if "=" in word)
            if name == "MOVE_SAFE":
                path = pathlib.Path(__file__).parents[1] / "macros" / "base.cfg"
                rendered = render_macro(path, name, params=params, printer={
                    "gcode_macro MOVE_SAFE": load_macro(path, name).variables,
                    "toolhead": {"axis_maximum": {"z": 230.0}},
                })
                self._run_script("\n".join(rendered.commands))
                continue
            if name == "G1":
                params = dict((word[0], word[1:]) for word in arguments)
            try:
                getattr(self.move, "cmd_" + name)(Command(params))
            except Exception:
                # Klipper resets its command position after a rejected move.
                self.move.last_position[:] = self.position
                raise

    def _run_blocking_gcode(self, script, message):
        self._run_script(script)

    def _render_z_paper(self):
        pass

    def _check_z_pressure(self, eventtime):
        return False


class ZCalibrationMotionTest(unittest.TestCase):
    def setUp(self):
        self.harness = PaperMotionHarness()

    def assert_motion_matches_session(self, expected):
        harness = self.harness
        self.assertAlmostEqual(harness.position[2], expected)
        self.assertAlmostEqual(harness.z_calibration.paper_contact_z, expected)
        self.assertEqual(harness.position[:2], [10.0, 20.0])
        self.assertEqual(harness.position[3], 3.0)
        self.assertFalse(harness.move.absolute_coord)
        self.assertFalse(harness.move.absolute_extrude)
        self.assertEqual(harness.move.speed, 25.0)

    def test_closer_crosses_zero_and_farther_moves_immediately(self):
        self.harness._move_z_paper(-0.01)
        self.assert_motion_matches_session(-0.01)
        self.harness._move_z_paper(-0.01)
        self.assert_motion_matches_session(-0.02)
        self.harness._move_z_paper(0.01)
        self.assert_motion_matches_session(-0.01)
        self.assertEqual(len(self.harness.moves), 3)
        self.assertAlmostEqual(self.harness.moves[0][1], 5.0)

    def test_reset_moves_to_negative_position_for_zero_candidate(self):
        self.harness._reset_z_paper()
        self.assert_motion_matches_session(-0.25)
        self.assertAlmostEqual(self.harness.z_calibration.candidate, 0.0)

    def test_absolute_mode_is_preserved_after_adjustment(self):
        self.harness.move.absolute_coord = True
        self.harness._move_z_paper(-0.01)
        self.assertAlmostEqual(self.harness.position[2], -0.01)
        self.assertTrue(self.harness.move.absolute_coord)
        self.assertEqual(self.harness.move.speed, 25.0)

    def test_probe_retracts_half_mm_while_target_is_negative(self):
        self.harness._probe_z_zone()
        self.assert_motion_matches_session(-0.125)
        self.assertAlmostEqual(self.harness.z_calibration.local_z, 0.5)
        self.assertFalse(self.harness.z_calibration.probing)

    def test_manual_start_preserves_gcode_state(self):
        self.harness._move_z_manual_start()
        self.assert_motion_matches_session(5.0)
        self.assertEqual(self.harness.z_calibration.start_mode, "manual")

    def test_failed_probe_retract_or_manual_start_does_not_publish_a_reference(self):
        for action in (self.harness._probe_z_zone,
                       self.harness._move_z_manual_start):
            with self.subTest(action=action):
                session = self.harness.z_calibration
                session.choose_zone("center")
                self.harness.fail_move = True
                with self.assertRaisesRegex(RuntimeError, "Move out of range"):
                    action()
                self.assertFalse(session.ready_for_paper_test)
                self.assertFalse(session.probing)
                self.assertFalse(session.moving_to_start)
                self.assertFalse(self.harness.move.absolute_coord)
                self.assertEqual(self.harness.move.speed, 25.0)

    def test_rejected_adjustment_and_reset_restore_session_and_gcode_state(self):
        for action in (lambda: self.harness._move_z_paper(-0.01),
                       self.harness._reset_z_paper):
            with self.subTest(action=action):
                self.harness.fail_move = True
                old_local = self.harness.z_calibration.local_z
                with self.assertRaisesRegex(RuntimeError, "Move out of range"):
                    action()
                self.assertEqual(self.harness.z_calibration.local_z, old_local)
                self.assert_motion_matches_session(0.0)
                self.harness.fail_move = False
                self.harness._move_z_paper(0.01)
                self.assert_motion_matches_session(0.01)
                self.harness._move_z_paper(-0.01)


class ZCalibrationStateTest(unittest.TestCase):
    def test_session_uses_the_canonical_default_paper_step(self):
        self.assertEqual(
            ZCAL.ZCalibrationSession().step, ZCAL.PAPER_DEFAULT_STEP)

    def test_safe_z_candidate_uses_clean_trigger_plus_five(self):
        session = ZCAL.ZCalibrationSession()
        session.begin(0.0, None, "", -0.25, False, safe_z=10.0)

        target = session.set_safe_z_trigger(-0.375)

        self.assertAlmostEqual(target, 4.625)
        self.assertTrue(session.safe_z_ready)
        self.assertAlmostEqual(session.adjust_safe_z(1.0), 5.625)
        self.assertAlmostEqual(session.adjust_safe_z(-1.0), 4.625)
        self.assertAlmostEqual(session.accept_safe_z(), 4.625)

    def test_safe_z_adjustment_cannot_cross_the_one_mm_clearance_floor(self):
        session = ZCAL.ZCalibrationSession()
        session.begin(0.0, None, "", -0.25, False)
        session.set_safe_z_trigger(0.2)

        self.assertAlmostEqual(session.adjust_safe_z(-100.0), 1.2)

    def test_safe_z_candidate_and_adjustment_respect_setting_minimum(self):
        session = ZCAL.ZCalibrationSession()
        self.assertEqual(session.set_safe_z_trigger(-4.5), 1.0)
        self.assertEqual(session.adjust_safe_z(-1.0), 1.0)
        self.assertEqual(session.accept_safe_z(), 1.0)

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

    def test_manual_start_records_reference_and_reset_still_is_true_offset_zero(self):
        session = ZCAL.ZCalibrationSession()
        session.begin(0.0, None, "", -0.25, False)
        session.choose_zone("center")
        session.set_manual_start(1.5)

        self.assertEqual(session.start_mode, "manual")
        self.assertTrue(session.ready_for_paper_test)
        self.assertAlmostEqual(session.reference_z, 1.5)
        self.assertAlmostEqual(session.reference_base_z, 1.5)
        self.assertAlmostEqual(session.local_z, 0.0)
        self.assertAlmostEqual(session.paper_contact_z, 1.5)
        self.assertAlmostEqual(session.candidate, 1.25)
        self.assertAlmostEqual(session.reset(), 0.25)
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
