## Tests for guided Feather extruder rotation-distance calibration.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import importlib.util
import pathlib
import stat
import tempfile
import threading
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]
PLUGINS = ROOT / ".py" / "klipper" / "plugins"
MODULE_PATH = PLUGINS / "feather_extruder_calibration.py"

import sys
sys.path.insert(0, str(PLUGINS))

SPEC = importlib.util.spec_from_file_location(
    "feather_extruder_calibration_test", MODULE_PATH)
EXTRUDER_CAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXTRUDER_CAL)

HEATER_SPEC = importlib.util.spec_from_file_location(
    "feather_heaters_test",
    ROOT / ".py" / "klipper" / "patches" / "extras" / "heaters.py")
HEATERS = importlib.util.module_from_spec(HEATER_SPEC)
HEATER_SPEC.loader.exec_module(HEATERS)

from tests.test_feather_screen import FEATHER, Reactor  # noqa: E402


class ExtruderCalculationTest(unittest.TestCase):
    def test_formula_rounding_and_feed_direction(self):
        candidate = EXTRUDER_CAL.calculate_rotation_distance(4.38, 98)

        self.assertEqual(candidate, 4.292)
        self.assertGreater(
            EXTRUDER_CAL.feed_change_percent(4.38, candidate), 0)
        self.assertLess(
            EXTRUDER_CAL.feed_change_percent(4.38, 4.5), 0)

    def test_measurement_parser_accepts_dot_and_comma(self):
        self.assertEqual(EXTRUDER_CAL.parse_measurement("98.25"), 98.25)
        self.assertEqual(EXTRUDER_CAL.parse_measurement("98,25"), 98.25)

    def test_measurement_parser_rejects_unusable_values(self):
        for value in ("", "0", "-1", "nan", "inf", "1e2", "12 mm"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    EXTRUDER_CAL.parse_measurement(value)

        with self.assertRaisesRegex(ValueError, "unusable"):
            EXTRUDER_CAL.calculate_rotation_distance(4.38, 0.0001)

    def test_only_more_than_twenty_percent_is_suspicious(self):
        self.assertFalse(EXTRUDER_CAL.measurement_is_suspicious(80))
        self.assertFalse(EXTRUDER_CAL.measurement_is_suspicious(120))
        self.assertTrue(EXTRUDER_CAL.measurement_is_suspicious(79.999))
        self.assertTrue(EXTRUDER_CAL.measurement_is_suspicious(120.001))

    def test_session_uses_runtime_value_as_calculation_base(self):
        session = EXTRUDER_CAL.ExtruderCalibrationSession("/tmp/user.cfg")
        session.begin(4.38)

        session.set_measurement("98")

        self.assertEqual(session.original_rotation, 4.38)
        self.assertEqual(session.candidate, 4.292)
        self.assertFalse(session.suspicious)


class UserConfigWriterTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.temporary.name) / "user.cfg"

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, text):
        self.path.write_text(text, encoding="utf-8")

    def test_comments_old_value_and_writes_new_active_value(self):
        original = (
            "# custom settings\n"
            "[extruder]\n"
            "rotation_distance: 4.380  # calibrated before\n"
            "pressure_advance: 0.03\n"
            "\n[heater_bed]\nmax_power: 0.8\n")
        self.write(original)
        snapshot = EXTRUDER_CAL.inspect_user_cfg(self.path)

        with mock.patch.object(
                EXTRUDER_CAL, "_history_timestamp",
                return_value="2026-07-28 15:30:00 +0500"):
            backup = EXTRUDER_CAL.write_user_rotation_distance(
                self.path, 4.292, snapshot.digest)

        updated = self.path.read_text(encoding="utf-8")
        self.assertIn(
            "# rotation_distance: 4.380  # calibrated before  "
            "[Feather saved 2026-07-28 15:30:00 +0500]", updated)
        self.assertIn("rotation_distance: 4.292", updated)
        self.assertIn("pressure_advance: 0.03", updated)
        self.assertIn("[heater_bed]\nmax_power: 0.8", updated)
        self.assertEqual(pathlib.Path(backup).read_text(encoding="utf-8"),
                         original)

    def test_adds_key_to_existing_section(self):
        self.write("[extruder]\npressure_advance: 0.03\n\n[fan]\npin: PA1\n")
        snapshot = EXTRUDER_CAL.inspect_user_cfg(self.path)

        EXTRUDER_CAL.write_user_rotation_distance(
            self.path, 4.25, snapshot.digest)

        self.assertEqual(
            self.path.read_text(encoding="utf-8"),
            "[extruder]\npressure_advance: 0.03\n"
            "rotation_distance: 4.250\n\n[fan]\npin: PA1\n")

    def test_adds_missing_section(self):
        self.write("[heater_bed]\nmax_power: 0.8")
        snapshot = EXTRUDER_CAL.inspect_user_cfg(self.path)

        EXTRUDER_CAL.write_user_rotation_distance(
            self.path, 4.1, snapshot.digest)

        self.assertEqual(
            self.path.read_text(encoding="utf-8"),
            "[heater_bed]\nmax_power: 0.8\n\n"
            "[extruder]\nrotation_distance: 4.100\n")

    def test_commented_value_is_preserved_when_active_value_is_added(self):
        self.write("[extruder]\n# rotation_distance: 4.4\n")
        snapshot = EXTRUDER_CAL.inspect_user_cfg(self.path)

        EXTRUDER_CAL.write_user_rotation_distance(
            self.path, 4.2, snapshot.digest)

        updated = self.path.read_text(encoding="utf-8")
        self.assertIn("# rotation_distance: 4.4", updated)
        self.assertIn("rotation_distance: 4.200", updated)

    def test_existing_equivalent_history_comment_is_preserved(self):
        self.write(
            "[extruder]\n"
            "rotation_distance: 4.38\n"
            "# Old value\n"
            "# rotation_distance: 4.380\n")
        snapshot = EXTRUDER_CAL.inspect_user_cfg(self.path)

        with mock.patch.object(
                EXTRUDER_CAL, "_history_timestamp",
                return_value="2026-07-28 15:31:00 +0500"):
            EXTRUDER_CAL.write_user_rotation_distance(
                self.path, 4.402, snapshot.digest)

        updated = self.path.read_text(encoding="utf-8")
        self.assertIn("# rotation_distance: 4.380\n", updated)
        self.assertIn(
            "# rotation_distance: 4.38  "
            "[Feather saved 2026-07-28 15:31:00 +0500]", updated)
        self.assertEqual(updated.count("rotation_distance: 4.402"), 1)

    def test_different_history_comments_are_left_unchanged(self):
        old_comment = (
            "# rotation_distance: 4.100  "
            "[Feather saved 2026-07-01 10:00:00 +0500]")
        self.write(
            "[extruder]\n" + old_comment + "\n"
            "rotation_distance: 4.380\n")
        snapshot = EXTRUDER_CAL.inspect_user_cfg(self.path)

        with mock.patch.object(
                EXTRUDER_CAL, "_history_timestamp",
                return_value="2026-07-28 15:32:00 +0500"):
            EXTRUDER_CAL.write_user_rotation_distance(
                self.path, 4.402, snapshot.digest)

        updated = self.path.read_text(encoding="utf-8")
        self.assertIn(old_comment, updated)
        self.assertIn(
            "# rotation_distance: 4.380  "
            "[Feather saved 2026-07-28 15:32:00 +0500]", updated)

    def test_each_distinct_previous_value_is_kept_once(self):
        self.write("[extruder]\nrotation_distance: 4.380\n")
        first = EXTRUDER_CAL.inspect_user_cfg(self.path)
        EXTRUDER_CAL.write_user_rotation_distance(
            self.path, 4.402, first.digest)
        second = EXTRUDER_CAL.inspect_user_cfg(self.path)

        EXTRUDER_CAL.write_user_rotation_distance(
            self.path, 4.390, second.digest)

        updated = self.path.read_text(encoding="utf-8")
        self.assertEqual(updated.count("# rotation_distance: 4.380"), 1)
        self.assertEqual(updated.count("# rotation_distance: 4.402"), 1)
        self.assertEqual(updated.count("rotation_distance: 4.390"), 1)

    def test_unchanged_rounded_value_writes_no_history_or_backup(self):
        original = "[extruder]\nrotation_distance: 4.380\n"
        self.write(original)
        snapshot = EXTRUDER_CAL.inspect_user_cfg(self.path)

        with mock.patch.object(
                EXTRUDER_CAL, "_history_timestamp") as timestamp:
            backup = EXTRUDER_CAL.write_user_rotation_distance(
                self.path, 4.38, snapshot.digest)

        self.assertIsNone(backup)
        timestamp.assert_not_called()
        self.assertEqual(self.path.read_text(encoding="utf-8"), original)
        self.assertEqual([item.name for item in self.path.parent.iterdir()],
                         ["user.cfg"])

    def test_duplicate_sections_and_values_are_rejected(self):
        cases = (
            "[extruder]\nrotation_distance: 4.4\n"
            "[extruder]\nrotation_distance: 4.3\n",
            "[extruder]\nrotation_distance: 4.4\n"
            "rotation_distance: 4.3\n",
        )
        for text in cases:
            with self.subTest(text=text):
                self.write(text)
                with self.assertRaises(EXTRUDER_CAL.UserConfigError):
                    EXTRUDER_CAL.inspect_user_cfg(self.path)

    def test_parallel_edit_is_not_overwritten(self):
        self.write("[extruder]\nrotation_distance: 4.380\n")
        snapshot = EXTRUDER_CAL.inspect_user_cfg(self.path)
        self.write("[extruder]\nrotation_distance: 4.390\n")

        with self.assertRaises(EXTRUDER_CAL.ConcurrentUserConfigEdit):
            EXTRUDER_CAL.write_user_rotation_distance(
                self.path, 4.2, snapshot.digest)

        self.assertEqual(
            self.path.read_text(encoding="utf-8"),
            "[extruder]\nrotation_distance: 4.390\n")

    def test_crlf_and_file_mode_are_preserved(self):
        self.path.write_bytes(
            b"[extruder]\r\nrotation_distance: 4.380\r\n")
        self.path.chmod(0o640)
        snapshot = EXTRUDER_CAL.inspect_user_cfg(self.path)

        with mock.patch.object(
                EXTRUDER_CAL, "_history_timestamp",
                return_value="2026-07-28 15:33:00 +0500"):
            EXTRUDER_CAL.write_user_rotation_distance(
                self.path, 4.2, snapshot.digest)

        self.assertEqual(
            self.path.read_bytes(),
            b"[extruder]\r\n# rotation_distance: 4.380  "
            b"[Feather saved 2026-07-28 15:33:00 +0500]\r\n"
            b"rotation_distance: 4.200\r\n")
        self.assertEqual(
            stat.S_IMODE(self.path.stat().st_mode), 0o640)

    def test_post_replace_failure_restores_original_file(self):
        original = "[extruder]\nrotation_distance: 4.380\n"
        self.write(original)
        snapshot = EXTRUDER_CAL.inspect_user_cfg(self.path)
        real_fsync = EXTRUDER_CAL._fsync_directory
        calls = []

        def fail_second(directory):
            calls.append(directory)
            if len(calls) == 2:
                raise OSError("directory fsync failed")
            return real_fsync(directory)

        with mock.patch.object(
                EXTRUDER_CAL, "_fsync_directory", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "directory fsync failed"):
                EXTRUDER_CAL.write_user_rotation_distance(
                    self.path, 4.2, snapshot.digest)

        self.assertEqual(self.path.read_text(encoding="utf-8"), original)


class HeaterExtrusionOverrideTest(unittest.TestCase):
    def heater(self):
        heater = HEATERS.Heater.__new__(HEATERS.Heater)
        heater.lock = threading.Lock()
        heater._extrusion_temperature_disabled = False
        heater._temperature_can_extrude = False
        heater._extrusion_override = False
        heater.can_extrude = False
        heater.last_temp_time = 0.0
        heater.last_temp = 0.0
        heater.smoothed_temp = 20.0
        heater.inv_smooth_time = 1.0
        heater.min_extrude_temp = 100.0
        heater.target_temp = 0.0
        heater.control = type(
            "Control", (), {"temperature_update": lambda *args: None})()
        return heater

    def test_override_survives_temperature_callbacks_until_explicitly_removed(self):
        heater = self.heater()

        heater.set_extrusion_override(True)
        heater.temperature_callback(1.0, 25.0)

        self.assertTrue(heater.can_extrude)
        heater.set_extrusion_override(False)
        self.assertFalse(heater.can_extrude)

    def test_normal_temperature_permission_remains_after_override(self):
        heater = self.heater()
        heater.temperature_callback(1.0, 150.0)

        heater.set_extrusion_override(True)
        heater.set_extrusion_override(False)

        self.assertTrue(heater.can_extrude)


class FakeCalibrationHeater:
    def __init__(self):
        self.calls = []
        self.enabled = False

    def set_extrusion_override(self, enabled):
        self.enabled = bool(enabled)
        self.calls.append(self.enabled)


class FakeCalibrationExtruder:
    def __init__(self, rotation=4.38, temperature=25.0, target=0.0):
        self.heater = FakeCalibrationHeater()
        stepper = type("Stepper", (), {
            "get_rotation_distance": lambda self: (rotation, 200),
        })()
        self.extruder_stepper = type(
            "ExtruderStepper", (), {"stepper": stepper})()
        self.status = {"temperature": temperature, "target": target}

    def get_status(self, eventtime):
        return dict(self.status)


def calibration_controller(path=None):
    controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
    controller.renderer = FEATHER.FeatherRenderer()
    controller.renderer.send = lambda commands: None
    controller.reactor = Reactor()
    controller.extruder = FakeCalibrationExtruder()
    controller.extruder_calibration = FEATHER.ExtruderCalibrationSession(
        path or EXTRUDER_CAL.USER_CFG_PATH)
    controller.extruder_calibration.begin(4.38)
    controller.page = FEATHER.Page.EXTRUDER_CALIBRATION
    controller.previous_page = FEATHER.Page.CALIBRATION_HOME
    controller.print_state = FEATHER.PrintState.IDLE
    controller.command_depth = 0
    controller.busy_message = None
    controller.toast_until = 0.0
    controller.toast_message = ""
    return controller


class ExtruderCalibrationControllerTest(unittest.TestCase):
    def test_intro_explains_when_complete_cold_pull_is_required(self):
        controller = calibration_controller()
        batches = []
        controller.renderer.send = batches.append

        controller._render_extruder_calibration()

        drawing = "\n".join(batches[-1])
        self.assertIn("COLD PULL completely cleans the nozzle", drawing)
        self.assertIn("required if filament is loaded", drawing)
        self.assertIn("Choose FILAMENT READY only after cleaning", drawing)

    def test_empty_cold_pull_disables_only_cold_pull_path(self):
        controller = calibration_controller()
        controller.cold_pull_materials = ()
        controller.cold_pull_profiles = {}
        batches = []
        controller.renderer.send = batches.append

        controller._render_extruder_calibration()

        drawing = "\n".join(batches[-1])
        self.assertIn("COLD PULL", drawing)
        self.assertNotIn("--id 1:extruder.coldpull", drawing)
        self.assertIn("extruder.skip", drawing)
        with self.assertRaisesRegex(RuntimeError, "No cold-pull"):
            controller._handle_extruder_calibration_action(
                "extruder.coldpull")

    def test_cold_move_scopes_override_and_restores_gcode_state(self):
        controller = calibration_controller()
        commands = []
        controller._run_script = (
            lambda command, show_notice=True: commands.append(command))
        controller._run_blocking_gcode = (
            lambda command, message: commands.append(command))

        controller._cold_extrusion_move(100)

        self.assertEqual(
            controller.extruder.heater.calls, [True, False])
        self.assertEqual(
            commands[0],
            "SAVE_GCODE_STATE NAME=_feather_extruder_calibration\n"
            "M83\nG1 E100 F300\nM400")
        self.assertEqual(
            commands[1],
            "RESTORE_GCODE_STATE NAME=_feather_extruder_calibration MOVE=0")

    def test_first_mark_can_repeat_seating_feed_without_advancing(self):
        controller = calibration_controller()
        session = controller.extruder_calibration
        session.phase = "mark_first"
        moves = []
        pages = []
        controller._cold_extrusion_move = moves.append
        controller._show_page = pages.append

        controller._handle_extruder_calibration_action("extruder.feed50")

        self.assertEqual(moves, [50])
        self.assertEqual(session.phase, "mark_first")
        self.assertEqual(pages, [FEATHER.Page.EXTRUDER_CALIBRATION])

    def test_first_mark_explains_repeat_feed_and_measurement_feed(self):
        controller = calibration_controller()
        controller.extruder_calibration.phase = "mark_first"
        batches = []
        controller.renderer.send = batches.append

        controller._render_extruder_calibration()

        drawing = "\n".join(batches[-1])
        self.assertIn("FEED 50 MORE", drawing)
        self.assertIn("MARKED / FEED 100", drawing)
        self.assertIn("use FEED 50 MORE as often as needed", drawing)

    def test_unload_uses_safe_base_then_offers_optional_extra_retract(self):
        controller = calibration_controller()
        session = controller.extruder_calibration
        session.phase = "mark_second"
        moves = []
        pages = []
        controller._cold_extrusion_move = moves.append
        controller._show_page = pages.append

        controller._handle_extruder_calibration_action("extruder.unload")
        controller._handle_extruder_calibration_action(
            "extruder.unload_more")

        self.assertEqual(moves, [-160, -50])
        self.assertEqual(session.phase, "measure_ready")
        self.assertEqual(
            pages,
            [FEATHER.Page.EXTRUDER_CALIBRATION,
             FEATHER.Page.EXTRUDER_CALIBRATION])

    def test_measurement_input_opens_only_after_filament_is_free(self):
        controller = calibration_controller()
        session = controller.extruder_calibration
        session.phase = "measure_ready"
        pages = []
        controller._show_page = pages.append

        controller._handle_extruder_calibration_action(
            "extruder.measure_ready")

        self.assertEqual(session.phase, "input")
        self.assertEqual(pages, [FEATHER.Page.EXTRUDER_CALIBRATION])

    def test_measurement_uses_shared_decimal_keypad_constraints(self):
        controller = calibration_controller()
        session = controller.extruder_calibration
        session.phase = "input"
        batches = []
        controller.renderer.send = batches.append

        for token in ("1", "0", "0", "dot", "5", "0", "0", "9"):
            controller._append_extruder_input(token)

        self.assertEqual(session.input_text, "100.500")
        drawing = "\n".join(batches[-1])
        self.assertIn("DISTANCE BETWEEN MARKS", drawing)
        self.assertIn('-t "MM"', drawing)
        self.assertIn("extruder.key.backspace", drawing)
        self.assertIn("CALCULATE", drawing)

    def test_remove_and_measure_page_explains_optional_unload(self):
        controller = calibration_controller()
        controller.extruder_calibration.phase = "measure_ready"
        batches = []
        controller.renderer.send = batches.append

        controller._render_extruder_calibration()

        drawing = "\n".join(batches[-1])
        self.assertIn("REMOVE FILAMENT AND MEASURE", drawing)
        self.assertIn("UNLOAD 50 MORE", drawing)
        self.assertIn("ENTER MEASUREMENT", drawing)

    def test_failed_move_still_removes_override_and_restores_state(self):
        controller = calibration_controller()
        commands = []
        controller._run_script = (
            lambda command, show_notice=True: commands.append(command))

        def fail(command, message):
            raise RuntimeError("move failed")

        controller._run_blocking_gcode = fail

        with self.assertRaisesRegex(RuntimeError, "move failed"):
            controller._cold_extrusion_move(50)

        self.assertFalse(controller.extruder.heater.enabled)
        self.assertEqual(controller.extruder.heater.calls, [True, False])
        self.assertIn("RESTORE_GCODE_STATE", commands[-1])

    def test_cooling_beeps_once_and_opens_nozzle_instruction(self):
        controller = calibration_controller()
        controller.extruder_calibration.phase = "cooling"
        controller.extruder_calibration.cooling_fan_active = True
        controller.extruder.status.update(temperature=49.9, target=0.0)
        commands = []
        pages = []
        controller._run_script = (
            lambda command, show_notice=True: commands.append(command))
        controller._show_page = pages.append

        controller._poll_extruder_calibration(100.0)
        controller._poll_extruder_calibration(101.0)

        self.assertEqual(commands, ["M107", "BEEP"])
        self.assertFalse(
            controller.extruder_calibration.cooling_fan_active)
        self.assertEqual(controller.extruder_calibration.phase, "remove")
        self.assertEqual(pages, [FEATHER.Page.EXTRUDER_CALIBRATION])

    def test_cooling_publishes_terminal_state_before_yielding_beep(self):
        controller = calibration_controller()
        session = controller.extruder_calibration
        session.phase = "cooling"
        session.cooling_fan_active = True
        controller.extruder.status.update(temperature=45.0, target=0.0)
        commands = []

        def reentrant_script(command, show_notice=True):
            commands.append(command)
            if command == "BEEP":
                controller._poll_extruder_calibration(101.0)

        controller._run_script = reentrant_script
        controller._show_page = lambda page: None

        controller._poll_extruder_calibration(100.0)

        self.assertEqual(commands, ["M107", "BEEP"])
        self.assertTrue(session.cooling_beeped)
        self.assertEqual(session.phase, "remove")

    def test_prepare_runs_head_fan_at_full_speed_while_cooling(self):
        controller = calibration_controller()
        controller.extruder.status.update(temperature=60.0, target=0.0)
        blocking = []
        commands = []
        pages = []
        controller._run_blocking_gcode = (
            lambda command, message: blocking.append((command, message)))
        controller._run_script = (
            lambda command, show_notice=True: commands.append(command))
        controller._show_page = pages.append
        controller._render_extruder_calibration = lambda: None

        controller._prepare_extruder_calibration()

        self.assertEqual(commands, ["M106 P0 S255"])
        self.assertTrue(controller.extruder_calibration.cooling_fan_active)
        self.assertEqual(controller.extruder_calibration.phase, "cooling")
        self.assertIn("M104 S0\nG28", blocking[0][0])
        self.assertEqual(pages, [FEATHER.Page.EXTRUDER_CALIBRATION])

    def test_cancel_stops_calibration_fan(self):
        controller = calibration_controller()
        controller.extruder_calibration.phase = "cooling"
        controller.extruder_calibration.cooling_fan_active = True
        commands = []
        controller._run_script = (
            lambda command, show_notice=True: commands.append(command))
        controller._show_page = lambda page: None

        controller._cancel_extruder_calibration(confirm=False)

        self.assertEqual(commands, ["M107"])
        self.assertFalse(controller.extruder_calibration.active)

    def test_exit_before_save_restores_runtime_value(self):
        controller = calibration_controller()
        session = controller.extruder_calibration
        session.current_rotation = 4.2
        session.nozzle_removed = True
        runtime = []
        pages = []
        controller._set_extruder_runtime_rotation = runtime.append
        controller._show_page = pages.append

        controller._cancel_extruder_calibration(confirm=False)

        self.assertEqual(runtime, [4.38])
        self.assertFalse(session.active)
        self.assertEqual(pages, [FEATHER.Page.CALIBRATION_HOME])

    def test_exit_warning_returns_to_the_exact_interrupted_step(self):
        controller = calibration_controller()
        session = controller.extruder_calibration
        session.nozzle_removed = True
        session.phase = "mark_second"
        controller._render_extruder_calibration = lambda: None

        controller._cancel_extruder_calibration()
        controller._handle_extruder_calibration_action("extruder.stay")

        self.assertEqual(session.phase, "mark_second")

    def test_save_updates_file_then_runtime_without_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "user.cfg"
            path.write_text(
                "[extruder]\nrotation_distance: 4.380\n",
                encoding="utf-8")
            controller = calibration_controller(path)
            session = controller.extruder_calibration
            session.set_measurement("98")
            session.file_snapshot = EXTRUDER_CAL.inspect_user_cfg(path)
            runtime = []
            pages = []
            controller._set_extruder_runtime_rotation = runtime.append
            controller._show_page = pages.append

            controller._save_extruder_rotation(session.candidate)

            self.assertIn(
                "rotation_distance: 4.292",
                path.read_text(encoding="utf-8"))
            self.assertEqual(runtime, [4.292])
            self.assertTrue(session.saved)
            self.assertEqual(session.phase, "saved")
            self.assertEqual(pages, [FEATHER.Page.EXTRUDER_CALIBRATION])

    def test_runtime_apply_failure_keeps_saved_file_and_shows_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "user.cfg"
            path.write_text(
                "[extruder]\nrotation_distance: 4.380\n",
                encoding="utf-8")
            controller = calibration_controller(path)
            session = controller.extruder_calibration
            session.set_measurement("98")
            session.file_snapshot = EXTRUDER_CAL.inspect_user_cfg(path)
            controller._set_extruder_runtime_rotation = (
                lambda value: (_ for _ in ()).throw(
                    RuntimeError("runtime rejected")))
            batches = []
            controller.renderer.send = batches.append

            controller._save_extruder_rotation(session.candidate)

            self.assertIn(
                "rotation_distance: 4.292",
                path.read_text(encoding="utf-8"))
            self.assertFalse(session.saved)
            self.assertTrue(session.save_file_written)
            self.assertEqual(session.candidate, 4.292)
            self.assertEqual(float(session.file_snapshot.existing_value), 4.292)
            self.assertIsNotNone(session.backup_path)
            drawing = "\n".join(batches[-1])
            self.assertIn("RUNTIME APPLY FAILED", drawing)
            self.assertIn("ROTATION_DISTANCE 4.292", drawing)
            self.assertIn("USER.CFG IS SAVED", drawing)
            self.assertIn("RESTART KLIPPER", drawing)

    def test_file_save_failure_preserves_candidate_in_recovery_modal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "user.cfg"
            original = "[extruder]\nrotation_distance: 4.380\n"
            path.write_text(original, encoding="utf-8")
            controller = calibration_controller(path)
            session = controller.extruder_calibration
            session.set_measurement("98")
            session.file_snapshot = EXTRUDER_CAL.inspect_user_cfg(path)
            batches = []
            controller.renderer.send = batches.append
            method_globals = (
                controller._save_extruder_rotation.__func__.__globals__)
            original_writer = method_globals["write_user_rotation_distance"]
            method_globals["write_user_rotation_distance"] = (
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    OSError("read-only filesystem")))
            try:
                controller._save_extruder_rotation(session.candidate)
            finally:
                method_globals["write_user_rotation_distance"] = original_writer

            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertEqual(session.candidate, 4.292)
            self.assertFalse(session.save_file_written)
            self.assertFalse(session.saved)
            drawing = "\n".join(batches[-1])
            self.assertIn("SAVE FAILED", drawing)
            self.assertIn("ROTATION_DISTANCE 4.292", drawing)
            self.assertIn("USER.CFG WAS NOT UPDATED", drawing)
            self.assertIn("WRITE THIS VALUE MANUALLY", drawing)
            self.assertIn("KEEP RESULT", drawing)
            controller._handle_extruder_calibration_action(
                "extruder.save_error.ok")
            self.assertIsNone(session.save_error)
            self.assertEqual(session.candidate, 4.292)
            self.assertEqual(session.phase, "result")

    def test_every_phase_renders_commands_and_expected_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "user.cfg"
            path.write_text(
                "[extruder]\nrotation_distance: 4.380\n",
                encoding="utf-8")
            controller = calibration_controller(path)
            batches = []
            controller.renderer.send = batches.append
            session = controller.extruder_calibration
            session.temperature = 45.0
            session.nozzle_removed = True
            session.input_text = "98.0"
            session.set_measurement("98")
            session.file_snapshot = EXTRUDER_CAL.inspect_user_cfg(path)
            phases = (
                "intro", "material", "cut", "cooling", "remove", "load",
                "mark_first", "mark_second", "measure_ready", "input",
                "warning", "result", "exit_warning", "saved")
            for phase in phases:
                with self.subTest(phase=phase):
                    session.phase = phase
                    if phase == "warning":
                        session.measured = 75.0
                    controller._render_extruder_calibration()
                    self.assertTrue(batches[-1])
                    drawing = "\n".join(batches[-1])
                    self.assertIn("clear-hitboxes", drawing)
                    if phase == "saved":
                        self.assertIn("must calibrate", drawing)
                        self.assertIn("Pressure Advance", drawing)
                        self.assertIn("Bed Mesh", drawing)
                        self.assertIn("Z Offset", drawing)


if __name__ == "__main__":
    unittest.main()
