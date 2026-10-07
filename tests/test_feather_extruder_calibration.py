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
import types
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]
PLUGINS = ROOT / ".py" / "klipper" / "plugins"
MODULE_PATH = PLUGINS / "feather" / "calibration" / "extruder.py"

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
from tests.feather_render_test_helper import RenderCapture, full_render_queue  # noqa: E402


class ScenarioController(EXTRUDER_CAL.FeatherExtruderCalibrationMixin,
                         FEATHER.FeatherScreen):
    """Test harness for the extracted extruder scenario."""

    boot_screen_held = False
    touch_available = None
    system_shutdown_active = False


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
    controller = ScenarioController.__new__(ScenarioController)
    controller.renderer = FEATHER.FeatherRenderer()
    RenderCapture(controller.renderer)
    controller.reactor = Reactor()
    controller.extruder = FakeCalibrationExtruder()
    controller.extruder_calibration = EXTRUDER_CAL.ExtruderCalibrationSession(
        path or EXTRUDER_CAL.USER_CFG_PATH)
    controller.extruder_calibration.begin(4.38)
    controller.page = FEATHER.ScreenPage.EXTRUDER_CALIBRATION
    controller.previous_page = FEATHER.ScreenPage.CALIBRATION_HOME
    controller.print_state = FEATHER.PrintState.IDLE
    controller.command_depth = 0
    controller.busy_message = None
    controller.toast_until = 0.0
    controller.toast_message = ""
    controller.operation_context = types.SimpleNamespace(
        get_status=lambda eventtime: {
            "context_path": (), "current_state": None, "revision": 0,
            "cancel_available": False, "cancel_pending": False,
            "cancel_request_id": None, "cancel_target_name": None,
            "cancel_target_mode": None, "cancel_blocker_name": None})
    controller.operation_cancel_on_accept = None
    controller.operation_cancel_on_clear = None
    controller.operation_cancel_request_id = None
    controller.operation_cancel_target_name = None
    controller.operation_cancel_target_mode = None
    controller.cancel_mode = None
    return controller


class ExtruderCalibrationControllerTest(unittest.TestCase):
    def test_empty_cold_pull_disables_only_cold_pull_path(self):
        controller = calibration_controller()
        controller.cold_pull_materials = ()
        controller.cold_pull_profiles = {}
        batches = RenderCapture(controller.renderer).batches

        controller._render_extruder_calibration()

        drawing = "\n".join(batches[-1])
        self.assertNotIn("--id 1:extruder.coldpull", drawing)
        self.assertIn("extruder.skip", drawing)
        with self.assertRaisesRegex(RuntimeError, "No materials are enabled"):
            controller._handle_extruder_calibration_action(
                "extruder.coldpull")

    def test_cold_pull_page_offers_cancel_for_the_whole_operation(self):
        controller = calibration_controller()
        capture = RenderCapture(controller.renderer)
        session = controller.extruder_calibration
        session.phase = "cold_pull"

        controller._render_extruder_calibration()

        self.assertFalse(capture.latest.has_action("extruder.coldpull.cancel"))
        self.assertFalse(capture.latest.has_action("nav.back"))
        self.assertFalse(any(action.startswith("extruder.material.")
                             for action in capture.latest.buttons))
        self.assertTrue(capture.latest.has_text("STARTING COLD PULL"))

        controller.operation_context = types.SimpleNamespace(
            get_status=lambda eventtime: {
                "context_path": ("COLD PULL",),
                "current_state": "HEATING NOZZLE", "revision": 1,
                "cancel_available": True, "cancel_pending": False,
                "cancel_target_name": "Cold Pull",
                "cancel_target_mode": "cancelable"})
        controller._poll_extruder_calibration(0.0)

        self.assertEqual(capture.latest.buttons["extruder.coldpull.cancel"].label, "CANCEL")
        self.assertTrue(capture.latest.has_text("HEATING NOZZLE"))
        self.assertFalse(capture.latest.has_action("nav.back"))

    def test_cold_pull_cancel_uses_shared_operation_page(self):
        controller = calibration_controller()
        commands = []
        batches = RenderCapture(controller.renderer).batches
        session = controller.extruder_calibration
        session.phase = "cold_pull"
        controller.temperature_wait = types.SimpleNamespace(
            variables={"active": True})
        controller.operation_context = types.SimpleNamespace(
            get_status=lambda eventtime: {
                "context_path": ("COLD PULL",),
                "current_state": "COOLING NOZZLE", "revision": 2,
                "cancel_available": True, "cancel_pending": False,
                "cancel_target_name": "Cold Pull",
                "cancel_target_mode": "cancelable"})
        opened = []
        controller._open_operation_cancel = (
            lambda page, callback, clear: opened.append(
                (page, callback, clear)))

        controller._open_cold_pull_cancel()

        self.assertEqual(opened[0][0], FEATHER.ScreenPage.EXTRUDER_CALIBRATION)
        opened[0][1]({"accepted": True})
        self.assertTrue(session.cold_pull_cancel_requested)
        self.assertTrue(session.cold_pull_cancel_dispatched)
        opened[0][2]({"cleared": True})
        self.assertFalse(session.cold_pull_cancel_requested)
        self.assertFalse(session.cold_pull_cancel_dispatched)
        self.assertEqual(commands, [])

    def test_clean_root_does_not_repaint_cancel_page(self):
        controller = calibration_controller()
        batches = RenderCapture(controller.renderer).batches
        session = controller.extruder_calibration
        session.phase = "cold_pull"
        controller.page = FEATHER.ScreenPage.OPERATION_CANCEL
        controller.operation_context = types.SimpleNamespace(
            get_status=lambda eventtime: {
                "context_path": ("Cold Pull",),
                "context_types": ("cold_pull",),
                "current_state": "HEATING NOZZLE", "revision": 2,
                "cancel_available": True, "cancel_pending": True,
                "cancel_target_name": "Cold Pull",
                "cancel_target_mode": "cancelable"})

        controller._ensure_screen_root().paint()
        batches.clear()
        controller._ensure_screen_root().paint()

        self.assertEqual(batches, [])
        self.assertEqual(controller.page, FEATHER.ScreenPage.OPERATION_CANCEL)

    def test_cold_pull_runs_on_calibration_page_without_blocking_loader(self):
        controller = calibration_controller()
        controller._paint_page = controller._render_extruder_calibration
        controller.cold_pull_profiles = {"PLA": (220, 100)}
        controller.extruder_calibration.phase = "material"
        events = []
        capture = RenderCapture(controller.renderer)
        lease = types.SimpleNamespace(
            release=lambda: events.append("release"))
        registry = types.SimpleNamespace(
            activity=lambda reason: (
                events.append(("activity", reason)) or lease))
        controller._apply_safety_visibility = lambda *args: None
        controller._ensure_safety_registry = lambda: registry
        controller._refresh_emergency_stop = (
            lambda: events.append("refresh"))
        def run(command):
            events.append(("run", command))
            self.assertEqual(len(capture.batches), 1)
            self.assertIsNone(controller._current_dialog())
            self.assertTrue(capture.latest.has_text("STARTING COLD PULL"))

        controller._run_script = run

        controller._handle_extruder_calibration_action("extruder.material.PLA")

        session = controller.extruder_calibration
        self.assertEqual(session.phase, "cut")
        self.assertIsNone(controller.busy_message)
        self.assertIn(("activity", "cold-pull"), events)
        self.assertIn(
            ("run", "_COLDPULL_LOAD_MATERIAL TEMP=220 COLD=100"),
            events)
        self.assertIn("release", events)
        self.assertEqual(sum(frame.has_text("REMOVE AND CUT FILAMENT")
                             for frame in capture.frames), 1)
        self.assertTrue(capture.latest.has_text("REMOVE AND CUT FILAMENT"))

    def test_successful_cold_pull_closes_open_cancel_confirmation(self):
        controller = calibration_controller()
        capture = RenderCapture(controller.renderer)
        controller.cold_pull_profiles = {"PLA": (220, 100)}
        controller.extruder_calibration.phase = "material"
        controller._apply_safety_visibility = lambda *args: None
        controller._refresh_emergency_stop = lambda: None
        controller._ensure_safety_registry = lambda: types.SimpleNamespace(
            activity=lambda reason: types.SimpleNamespace(release=lambda: None))
        controller.operation_context = types.SimpleNamespace(get_status=lambda eventtime: {
            "current_state": "EXTRUDING", "cancel_available": True,
            "cancel_target_name": "Cold pull", "cancel_target_mode": "cancelable"})
        controller._paint_page = lambda: (
            controller._render_cancel_confirm() if controller.page == FEATHER.ScreenPage.OPERATION_CANCEL
            else controller._render_extruder_calibration())

        def complete_with_confirmation_open(command):
            controller._open_cold_pull_cancel()
            self.assertEqual(controller.page, FEATHER.ScreenPage.OPERATION_CANCEL)
            self.assertEqual(controller.cancel_mode, "confirm")

        controller._run_script = complete_with_confirmation_open
        controller._handle_extruder_calibration_action("extruder.material.PLA")
        self.assertEqual(controller.extruder_calibration.phase, "cut")
        self.assertEqual(controller.page, FEATHER.ScreenPage.EXTRUDER_CALIBRATION)
        self.assertIsNone(controller.cancel_mode)
        self.assertIsNone(controller._current_dialog())
        self.assertTrue(capture.latest.has_text("REMOVE AND CUT FILAMENT"))
        self.assertEqual(sum(frame.has_text("REMOVE AND CUT FILAMENT") for frame in capture.frames), 1)

    def test_calibration_cold_pull_refreshes_progress_and_restores_page(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                controller = calibration_controller()
                capture = RenderCapture(controller.renderer)
                controller.cold_pull_profiles = {"PLA": (220, 100)}
                controller.extruder_calibration.phase = "material"
                controller._paint_page = controller._render_extruder_calibration
                controller._refresh_emergency_stop = lambda: None
                released = []
                controller._apply_safety_visibility = lambda *args: None
                controller._ensure_safety_registry = lambda: types.SimpleNamespace(
                    activity=lambda reason: types.SimpleNamespace(
                        release=lambda: released.append(reason)))
                controller.operation_context = types.SimpleNamespace(get_status=lambda eventtime: {
                    "current_state": "HEATING NOZZLE", "cancel_available": True,
                })
                opened = []
                controller._open_operation_cancel = lambda *args: opened.append(args)

                def run(command):
                    self.assertNotIn("extruder.material.PLA", controller.renderer._buttons)
                    self.assertNotIn("nav.back", controller.renderer._buttons)
                    count = len(capture.batches)
                    controller._poll_extruder_calibration(0.0)
                    self.assertEqual(len(capture.batches), count)
                    controller.extruder.status["temperature"] = 130.0
                    controller._poll_extruder_calibration(0.0)
                    self.assertEqual(len(capture.batches), count + 1)
                    self.assertTrue(capture.latest.has_text("NOZZLE 130 / 0 C"))
                    controller.command_depth = 1
                    from feather.features.extruder import ExtruderCalibrationFeature
                    feature = ExtruderCalibrationFeature(controller)
                    feature.extruder_calibration = controller.extruder_calibration
                    controller.feature_manager = types.SimpleNamespace(
                        handle_immediate_action=feature.handle_immediate_action)
                    controller._handle_touch_action("extruder.coldpull.cancel")
                    self.assertEqual(opened[0][0], FEATHER.ScreenPage.EXTRUDER_CALIBRATION)
                    del controller.feature_manager
                    if fail:
                        raise RuntimeError("Nozzle heater fault")

                controller._run_script = run
                if fail:
                    with self.assertRaisesRegex(RuntimeError, "Nozzle heater fault"):
                        controller._handle_extruder_calibration_action("extruder.material.PLA")
                else:
                    controller._handle_extruder_calibration_action("extruder.material.PLA")
                self.assertEqual(released, ["cold-pull"])
                self.assertIsNone(controller._current_dialog())
                self.assertEqual(controller.extruder_calibration.phase, "material" if fail else "cut")
                self.assertEqual("extruder.material.PLA" in controller.renderer._buttons, fail)
                self.assertIn("nav.back", controller.renderer._buttons)

    def test_cancelled_cold_pull_returns_to_material_selection(self):
        controller = calibration_controller()
        controller.cold_pull_profiles = {"PLA": (220, 100)}
        controller.cancel_mode = "pending"
        controller.operation_cancel_request_id = 7
        pages = []

        def cancel_during_wait(hot, cold):
            self.assertEqual((hot, cold), (220, 100))
            session = controller.extruder_calibration
            session.cold_pull_cancel_requested = True
            session.cold_pull_cancel_dispatched = True
            session.phase = "material"
            raise RuntimeError("Temperature waiting cancelled")

        controller._run_cold_pull_material = cancel_during_wait
        controller._show_page = pages.append

        controller._handle_extruder_calibration_action(
            "extruder.material.PLA")

        session = controller.extruder_calibration
        self.assertEqual(session.phase, "material")
        self.assertFalse(session.cold_pull_cancel_requested)
        self.assertFalse(session.cold_pull_cancel_dispatched)
        self.assertIsNone(controller.cancel_mode)
        self.assertIsNone(controller.operation_cancel_request_id)
        self.assertEqual(pages, [FEATHER.ScreenPage.EXTRUDER_CALIBRATION])

    def test_extruder_feature_routes_page_cancel_as_immediate_action(self):
        from feather.features.extruder import ExtruderCalibrationFeature

        host = types.SimpleNamespace()
        feature = ExtruderCalibrationFeature(host)
        feature.extruder_calibration.phase = "cold_pull"
        calls = []
        feature._open_cold_pull_cancel = (
            lambda: calls.append("cancel"))

        handled = feature.handle_immediate_action(
            FEATHER.ScreenPage.EXTRUDER_CALIBRATION,
            "extruder.coldpull.cancel")

        self.assertTrue(handled)
        self.assertEqual(calls, ["cancel"])

    def test_immediate_feature_action_waits_for_open_dialog(self):
        for dialog_open in (True, False):
            with self.subTest(dialog_open=dialog_open):
                controller = calibration_controller()
                calls = []
                controller._start_touch_action = lambda action: None
                if dialog_open:
                    controller._show_message("Notice", controller.page)
                controller.feature_manager = types.SimpleNamespace(
                    handle_immediate_action=lambda page, action: calls.append(action) or True,
                    input_blocked=False)
                controller._handle_touch_action("extruder.coldpull.cancel")
                self.assertEqual(calls, [] if dialog_open else ["extruder.coldpull.cancel"])

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
        self.assertEqual(pages, [FEATHER.ScreenPage.EXTRUDER_CALIBRATION])

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
            [FEATHER.ScreenPage.EXTRUDER_CALIBRATION,
             FEATHER.ScreenPage.EXTRUDER_CALIBRATION])

    def test_measurement_input_opens_only_after_filament_is_free(self):
        controller = calibration_controller()
        session = controller.extruder_calibration
        session.phase = "measure_ready"
        pages = []
        controller._show_page = pages.append

        controller._handle_extruder_calibration_action(
            "extruder.measure_ready")

        self.assertEqual(session.phase, "input")
        self.assertEqual(pages, [FEATHER.ScreenPage.EXTRUDER_CALIBRATION])

    def test_measurement_uses_shared_decimal_keypad_constraints(self):
        controller = calibration_controller()
        session = controller.extruder_calibration
        session.phase = "input"
        batches = RenderCapture(controller.renderer).batches

        for token in ("1", "0", "0", "dot", "5", "0", "0", "9"):
            controller._append_extruder_input(token)

        self.assertEqual(session.input_text, "100.500")
        drawing = "\n".join(batches[-1])
        self.assertIn("extruder.key.backspace", drawing)

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

    def test_cooling_uses_large_status_and_preserves_safety_instructions(self):
        from ui.font_metrics import get_font_metrics
        from ui.layout_helpers import DIALOG_TITLE_FONT, DIALOG_STATUS_FONT

        controller = calibration_controller()
        capture = RenderCapture(controller.renderer)
        controller.extruder_calibration.phase = "cooling"
        controller.extruder_calibration.temperature = 130.0

        controller._render_extruder_calibration()

        frame = capture.latest
        self.assertEqual(frame.text("COOLING NOZZLE").font, DIALOG_TITLE_FONT)
        self.assertEqual(frame.text("130 C").font, DIALOG_STATUS_FONT)
        self.assertTrue(frame.has_text("DO NOT REMOVE THE NOZZLE YET"))
        instructions = next(text for text in frame.texts if "You will hear a beep" in text.value)
        metrics = get_font_metrics()
        instruction_height = metrics.text_height(
            instructions.value, instructions.font, max_width=instructions.max_width, wrap=True)
        self.assertLessEqual(instruction_height, instructions.max_height)
        warning = frame.text("DO NOT REMOVE THE NOZZLE YET")
        self.assertGreater(
            warning.y - metrics.metric(warning.font).glyph_height // 2,
            instructions.y - instruction_height // 2 + instruction_height)
        self.assertIn("nav.back", frame.buttons)

    def test_cooling_repaints_when_rounded_temperature_changes(self):
        controller = calibration_controller()
        capture = RenderCapture(controller.renderer)
        controller.extruder_calibration.phase = "cooling"
        controller.extruder_calibration.temperature = 130.1
        controller.extruder.status.update(temperature=130.4, target=0.0)
        controller._render_extruder_calibration()
        count = len(capture.frames)
        controller._poll_extruder_calibration(10.0)
        self.assertEqual(len(capture.frames), count)
        controller.extruder.status["temperature"] = 130.6
        controller._poll_extruder_calibration(11.0)
        self.assertEqual(len(capture.frames), count + 1)
        self.assertTrue(capture.latest.has_text("131 C"))

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
        self.assertEqual(pages, [FEATHER.ScreenPage.EXTRUDER_CALIBRATION])

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

    def test_cooling_updates_state_and_preserves_modal_output(self):

        controller = calibration_controller()
        session = controller.extruder_calibration
        session.phase = "cooling"
        controller.renderer = FEATHER.FeatherRenderer()
        rendering = RenderCapture(controller.renderer)
        def paint():
            controller.renderer.send(controller.renderer.begin_page("Cooling") + [
                controller.renderer.text(40, 100, str(session.temperature))])
        controller._paint_page = paint
        controller._render_extruder_calibration = paint
        controller._show_message("Cooling message", controller.page)
        frames = len(rendering.frames)

        controller.extruder.status.update(temperature=70.0, target=0.0)
        controller._poll_extruder_calibration(100.0)

        self.assertEqual(session.temperature, 70.0)
        self.assertEqual(len(rendering.frames), frames)
        self.assertFalse(rendering.latest.has_text("70.0"))
        self.assertTrue(rendering.latest.has_text("Cooling message"))
        self.assertEqual(set(controller.renderer._buttons), {"message.ok"})

        controller._close_dialog(FEATHER.ScreenDialog.MESSAGE)
        self.assertTrue(rendering.latest.has_text("70.0"))
        self.assertFalse(rendering.latest.has_text("Cooling message"))

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
        self.assertEqual(pages, [FEATHER.ScreenPage.EXTRUDER_CALIBRATION])

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
        self.assertEqual(pages, [FEATHER.ScreenPage.CALIBRATION_HOME])

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
            self.assertEqual(pages, [FEATHER.ScreenPage.EXTRUDER_CALIBRATION])

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
            batches = RenderCapture(controller.renderer).batches

            controller._save_extruder_rotation(session.candidate)

            self.assertIn(
                "rotation_distance: 4.292",
                path.read_text(encoding="utf-8"))
            self.assertFalse(session.saved)
            self.assertTrue(session.save_file_written)
            self.assertEqual(session.candidate, 4.292)
            self.assertEqual(float(session.file_snapshot.existing_value), 4.292)
            self.assertIsNotNone(session.backup_path)

    def test_file_save_failure_preserves_candidate_in_recovery_modal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "user.cfg"
            original = "[extruder]\nrotation_distance: 4.380\n"
            path.write_text(original, encoding="utf-8")
            controller = calibration_controller(path)
            session = controller.extruder_calibration
            session.set_measurement("98")
            session.file_snapshot = EXTRUDER_CAL.inspect_user_cfg(path)
            batches = RenderCapture(controller.renderer).batches
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
            batches = RenderCapture(controller.renderer).batches
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


if __name__ == "__main__":
    unittest.main()
