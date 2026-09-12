## Controller and recovery tests for the Feather screen.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import enum
import errno
import json
import pathlib
import re
import tempfile
import unittest
from unittest import mock

from tests.test_feather_screen import (
    DeferredReactor,
    FEATHER,
    FailingGCode,
    GCodeRecorder,
    MOD_PARAMS,
    MOD_UI,
    ModManager,
    RESURRECTION,
    Reactor,
    StatusObject,
    UI,
    mod_controller,
    mod_param,
)
from ff5m_ui.move import runtime as MOVE_UI
from ff5m_ui.filament import actions as FILAMENT_ACTIONS
from ff5m_ui.home import page as HOME_PAGE
from ff5m_ui.home import state as HOME_STATE
from ff5m_ui.keys import AppPage
from feather_feature_filament import FilamentFeature
from feather_z_calibration import (
    FeatherZCalibrationMixin, ZCalibrationSession)
from feather_extruder_calibration import FeatherExtruderCalibrationMixin


class ScenarioController(FeatherZCalibrationMixin,
                         FeatherExtruderCalibrationMixin,
                         FEATHER.FeatherScreen):
    """Test harness for scenario implementations no longer on the host."""


class BedMeshState(StatusObject):
    def __init__(self, mesh_object, profile_name):
        super().__init__({"profile_name": profile_name, "profiles": {}})
        self.z_mesh = mesh_object
        self.profile_name = profile_name
        self.restored = []

    def set_mesh(self, mesh_object):
        self.z_mesh = mesh_object
        self.restored.append(mesh_object)


class ControllerSafetyTest(unittest.TestCase):
    def test_home_dashboard_is_a_discoverable_declarative_page(self):
        self.assertIsInstance(HOME_PAGE.PAGE, UI.DeclarativePage)
        self.assertEqual(HOME_PAGE.PAGE.page_key, AppPage.HOME)
        self.assertFalse(HOME_PAGE.PAGE.show_back)
        available = set(
            action.wire_id for action in HOME_PAGE.PAGE.actions.values())
        for wire_id in (
                "nav.menu", "nav.heat", "nav.network", "nav.job",
                "home.last_job", "nav.filament", "nav.move"):
            self.assertIn(wire_id, available)

    def test_home_semantic_route_preserves_existing_navigation(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.page = FEATHER.ScreenPage.IDLE_HOME
        controller._cancel_delayed_tasks = mock.Mock()
        shown = []
        controller._show_page = shown.append

        action = controller._resolve_semantic_ui_action("nav.heat")
        controller._dispatch_semantic_ui_action(action)

        self.assertEqual(controller.heat_return_page, FEATHER.ScreenPage.IDLE_HOME)
        controller._cancel_delayed_tasks.assert_called_once_with()
        self.assertEqual(shown, [FEATHER.ScreenPage.CONTROL_HEAT])

    def test_declarative_navigation_rejects_non_controller_page_ids(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller._show_page = mock.Mock()

        with self.assertRaisesRegex(KeyError, "Unsupported application page"):
            controller._navigate_app_page(AppPage.HOME)

        controller._show_page.assert_not_called()

    def test_declarative_navigation_accepts_move_mode_route(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.move_mode = "joystick"
        controller._stop_joystick = mock.Mock()
        controller._render_move = mock.Mock()

        controller._navigate_app_page(AppPage.MOVE_STEP)

        self.assertEqual(controller.move_mode, "step")
        controller._stop_joystick.assert_called_once_with()
        controller._render_move.assert_called_once_with()

    def test_screen_transition_rejects_declarative_page_identity(self):
        controller = ScenarioController.__new__(ScenarioController)

        with self.assertRaisesRegex(TypeError, "ScreenPage"):
            controller._show_page(AppPage.HOME)

    def test_home_cards_register_navigation_without_icon_font(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.page = FEATHER.ScreenPage.IDLE_HOME
        controller.renderer = FEATHER.FeatherRenderer()
        controller.reactor = Reactor()
        controller.extruder = StatusObject(
            {"temperature": 20.0, "target": 0.0})
        controller.heater_bed = StatusObject(
            {"temperature": 21.0, "target": 0.0})
        controller.toolhead = StatusObject({"homed_axes": "xyz"})
        controller.network_status = {
            "mode": "ETHERNET", "ssid": "", "ip": "192.168.2.124"}
        controller.last_job_name = "NONE"
        controller._current_material = lambda: "PLA"
        controller._read_text = lambda _path: ""
        controller._refresh_local_timezone = lambda: None
        batches = []
        controller.renderer.send = batches.append

        controller._render_home()

        drawing = "\n".join(batches[0])
        self.assertNotIn("Typicons", drawing)
        self.assertIn("nav.menu", controller.renderer._buttons)
        for action in (
                "nav.heat", "nav.network", "nav.job",
                "home.last_job", "nav.filament", "nav.move"):
            self.assertIn(action, controller.renderer._hitboxes)

    def test_move_caution_loads_existing_auto_bed_profile(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.reactor = Reactor()
        controller.gcode = GCodeRecorder()
        controller.bed_mesh = StatusObject({
            "profile_name": "", "profiles": {"auto": {"points": []}}})
        controller.move_caution_acknowledged = False
        controller._require_idle = lambda: None
        controller._stop_joystick = lambda: None
        controller._render_move = lambda: None
        notices = []
        controller._toast = notices.append

        controller._handle_move_command(MOVE_UI.CAUTION_AUTO)

        self.assertEqual(
            controller.gcode.commands, ["BED_MESH_PROFILE LOAD=auto"])
        self.assertTrue(controller.move_caution_acknowledged)
        self.assertEqual(len(notices), 1)

    def test_move_caution_can_unload_active_bed_profile(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.reactor = Reactor()
        controller.gcode = GCodeRecorder()
        controller.move_caution_acknowledged = False
        controller._require_idle = lambda: None
        controller._stop_joystick = lambda: None
        rendered = []
        controller._render_move = lambda: rendered.append(True)
        notices = []
        controller._toast = notices.append

        controller._handle_move_command(MOVE_UI.CAUTION_UNLOAD)

        self.assertEqual(controller.gcode.commands, ["BED_MESH_CLEAR"])
        self.assertTrue(controller.move_caution_acknowledged)
        self.assertEqual(rendered, [True])
        self.assertEqual(len(notices), 1)

    def test_move_caution_dismissal_resets_after_z_becomes_safe(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.bed_mesh = StatusObject({
            "profile_name": "", "profiles": {"auto": {"points": []}}})
        controller.move_caution_acknowledged = False
        low = (0.0, 0.0, 4.99, "HOMED: XYZ", True, True)
        safe = (0.0, 0.0, 5.0, "HOMED: XYZ", True, True)

        self.assertEqual(controller._move_caution_state(low, 0),
                         (True, "available"))
        controller.move_caution_acknowledged = True
        self.assertEqual(controller._move_caution_state(low, 0),
                         (False, None))
        self.assertEqual(controller._move_caution_state(safe, 0),
                         (False, None))
        self.assertFalse(controller.move_caution_acknowledged)
        self.assertEqual(controller._move_caution_state(low, 0),
                         (True, "available"))

    def test_every_page_routes_to_a_renderer(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.page = FEATHER.ScreenPage.IDLE_HOME
        called = []
        routes = {
            FEATHER.ScreenPage.IDLE_HOME: "_render_home",
            FEATHER.ScreenPage.MAIN_MENU: "_render_main_menu",
            FEATHER.ScreenPage.CONTROL_HOME: "_render_control_home",
            FEATHER.ScreenPage.FILE_BROWSER: "_render_file_browser",
            FEATHER.ScreenPage.FILE_CONFIRM: "_render_file_confirm",
            FEATHER.ScreenPage.PRINTING: "_render_print_page",
            FEATHER.ScreenPage.PAUSED: "_render_print_page",
            FEATHER.ScreenPage.CANCEL_CONFIRM: "_render_cancel_confirm",
            FEATHER.ScreenPage.CONTROL_MOVE: "_render_move",
            FEATHER.ScreenPage.CONTROL_HEAT: "_render_heat",
            FEATHER.ScreenPage.CALIBRATION_HOME: "_render_calibration_home",
            FEATHER.ScreenPage.CALIBRATION_GUIDE: "_render_calibration_guide",
            FEATHER.ScreenPage.EXTRUDER_CALIBRATION:
                "_render_extruder_calibration",
            FEATHER.ScreenPage.CALIBRATION_Z: "_render_z_summary",
            FEATHER.ScreenPage.Z_OFFSET_SUMMARY: "_render_z_summary",
            FEATHER.ScreenPage.Z_OFFSET_PAPER_BRIEFING: "_render_z_paper_briefing",
            FEATHER.ScreenPage.Z_OFFSET_PAPER: "_render_z_paper",
            FEATHER.ScreenPage.SAFE_Z_BRIEFING: "_render_safe_z_briefing",
            FEATHER.ScreenPage.SAFE_Z_CALIBRATION: "_render_safe_z",
            FEATHER.ScreenPage.LIVE_Z_OFFSET: "_render_live_z_offset",
            FEATHER.ScreenPage.CALIBRATION_CONFIRM: "_render_calibration_confirm",
            FEATHER.ScreenPage.CALIBRATION_PROGRESS: "_render_calibration_progress",
            FEATHER.ScreenPage.CALIBRATION_RESULT: "_render_calibration_result",
            FEATHER.ScreenPage.SETTINGS: "_render_settings",
            FEATHER.ScreenPage.MOD_SETTINGS: "_render_mod_settings",
            FEATHER.ScreenPage.PARAMETER_OPTIONS: "_render_parameter_options",
            FEATHER.ScreenPage.MOD_VALUE: "_render_mod_value",
            FEATHER.ScreenPage.NETWORK_HOME: "_render_network_home",
            FEATHER.ScreenPage.WIFI_SCAN: "_render_wifi_scan",
            FEATHER.ScreenPage.WIFI_PASSWORD: "_render_keyboard",
            FEATHER.ScreenPage.NETWORK_PROGRESS: "_render_network_progress",
            FEATHER.ScreenPage.RECOVERY_PROMPT: "_render_recovery_prompt",
            FEATHER.ScreenPage.RECOVERY_CONFIRM: "_render_recovery_confirm",
            FEATHER.ScreenPage.ACTION_PROMPT: "_render_action_prompt",
            FEATHER.ScreenPage.MESSAGE: "_render_message",
            FEATHER.ScreenPage.ERROR: "_render_error",
        }
        feature_pages = {
            page for spec in FEATHER.FEATURE_SPECS for page in spec.pages
        }

        class Feature:
            def render(self, page):
                getattr(controller, routes[page])()

        class FeatureManager:
            def owner_name(self, page):
                return "test" if page in feature_pages else None

            def get_for_page(self, page):
                return Feature()

        controller.feature_manager = FeatureManager()
        for method in set(routes.values()):
            setattr(controller, method,
                    lambda method=method: called.append(method))
        for page, method in routes.items():
            called[:] = []
            controller._show_page(page)
            self.assertEqual(called, [method], page)

    def test_active_print_keeps_menu_available_on_home_page(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        controller.print_state = FEATHER.PrintState.PRINTING
        controller.page = FEATHER.ScreenPage.IDLE_HOME
        drawing = []

        def render_home():
            commands = controller.renderer.begin_page("Home")
            commands += controller.renderer.button(
                "nav.menu", 648, 9, 132, 38, "MENU")
            drawing.extend(commands)

        controller._render_home = render_home

        controller._show_page(FEATHER.ScreenPage.IDLE_HOME)

        self.assertIsNone(controller.renderer._header_action)
        self.assertIn("nav.menu", "\n".join(drawing))
        self.assertNotIn("global.abort", "\n".join(drawing))

    def test_safety_composes_armed_pages_and_global_printer_activity(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.reactor = Reactor()
        controller.page = FEATHER.ScreenPage.MAIN_MENU
        controller.print_state = FEATHER.PrintState.IDLE
        controller.print_stats = StatusObject({"state": "standby"})
        controller.idle_timeout = StatusObject({"state": "Ready"})
        controller.temperature_wait = type(
            "Wait", (), {"variables": {"active": False}})()
        controller.extruder = StatusObject({"target": 0.0})
        controller.heater_bed = StatusObject({"target": 0.0})
        controller.motion_report = StatusObject({
            "live_velocity": 0.0, "live_extruder_velocity": 0.0})
        controller.toolhead = StatusObject({"homed_axes": ""})
        controller.joystick_stream = type(
            "Stream", (), {"active": False})()
        controller.joystick_action = None

        class Mutex:
            busy = False

            def test(self):
                return self.busy

        mutex = Mutex()
        controller.gcode = type("GCode", (), {
            "get_mutex": lambda self: mutex,
        })()

        for state in ("standby", "complete", "cancelled", "error"):
            controller.print_stats.status["state"] = state
            self.assertFalse(
                controller._safety_decision().visible, state)

        controller.print_stats.status["state"] = "paused"
        self.assertTrue(controller._safety_decision().visible)

        controller.print_stats.status["state"] = "printing"
        self.assertTrue(controller._safety_decision().visible)
        self.assertFalse(controller._safety_decision(
            FEATHER.ScreenPage.IDLE_HOME).visible)

        controller.print_stats.status["state"] = "standby"
        mutex.busy = True
        self.assertFalse(controller._safety_decision().visible)
        mutex.busy = False

        controller.idle_timeout.status["state"] = "Printing"
        self.assertFalse(controller._safety_decision().visible)
        controller.idle_timeout.status["state"] = "Ready"

        controller.temperature_wait.variables["active"] = True
        self.assertTrue(controller._safety_decision().visible)
        controller.temperature_wait.variables["active"] = False

        controller.extruder.status["target"] = 180.0
        self.assertTrue(controller._safety_decision().visible)
        controller.extruder.status["target"] = 0.0

        controller.motion_report.status["live_velocity"] = 25.0
        self.assertTrue(controller._safety_decision().visible)
        controller.motion_report.status["live_velocity"] = 0.0

        controller.joystick_stream.active = True
        self.assertTrue(controller._safety_decision().visible)
        controller.joystick_stream.active = False

        self.assertFalse(controller._safety_decision(
            FEATHER.ScreenPage.CONTROL_MOVE).visible)
        controller.toolhead.status["homed_axes"] = "x"
        move = controller._safety_decision(FEATHER.ScreenPage.CONTROL_MOVE)
        self.assertTrue(move.visible)
        self.assertEqual(move.armed_reasons, ("homed-motion-controls",))
        self.assertTrue(controller._safety_decision(
            FEATHER.ScreenPage.CONTROL_HEAT).visible)
        self.assertFalse(controller._safety_decision(
            FEATHER.ScreenPage.FILE_CONFIRM).visible)

    def test_active_process_shows_abort_on_every_live_page_except_home(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.reactor = Reactor()
        controller.page = FEATHER.ScreenPage.MAIN_MENU
        controller.print_state = FEATHER.PrintState.PRINTING
        controller.print_stats = StatusObject({"state": "printing"})

        for page in FEATHER.ScreenPage:
            decision = controller._safety_decision(page)
            self.assertEqual(decision.visible,
                             page != FEATHER.ScreenPage.IDLE_HOME, page)

    def test_feature_armed_policy_failure_is_fail_safe(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.reactor = Reactor()
        controller.page = FEATHER.ScreenPage.SETTINGS
        controller.print_state = FEATHER.PrintState.IDLE
        controller.feature_manager = type("Manager", (), {
            "safety_active_reasons": lambda self, eventtime: (),
            "safety_armed_reasons": lambda self, page, eventtime: (
                (_ for _ in ()).throw(ValueError("bad feature state"))),
        })()

        with mock.patch.object(FEATHER.logging, "exception") as logged:
            decision = controller._safety_decision()
            controller._safety_decision()

        self.assertTrue(decision.visible)
        self.assertEqual(decision.armed_reasons,
                         ("feature-policy-error",))
        logged.assert_called_once()

    def test_short_gcode_state_does_not_flash_abort(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.reactor = Reactor()
        controller.page = FEATHER.ScreenPage.MAIN_MENU
        controller.print_state = FEATHER.PrintState.IDLE
        controller.print_stats = StatusObject({"state": "standby"})
        controller.idle_timeout = StatusObject({"state": "Ready"})
        controller.command_depth = 0
        controller.busy_message = None

        class Mutex:
            busy = False

            def test(self):
                return self.busy

        mutex = Mutex()
        controller.gcode = type("GCode", (), {
            "get_mutex": lambda self: mutex,
        })()
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append

        def render_menu():
            controller.renderer.send(
                controller.renderer.begin_page("Menu", back=True))

        controller._render_main_menu = render_menu
        render_menu()
        self.assertNotIn("global.abort", controller.renderer._buttons)
        batch_count = len(batches)

        # Klipper briefly reports both of these for ordinary bookkeeping
        # commands. They are not an emergency-operation contract.
        mutex.busy = True
        controller.idle_timeout.status["state"] = "Printing"
        self.assertFalse(controller._refresh_emergency_stop())
        self.assertNotIn("global.abort", controller.renderer._buttons)
        self.assertEqual(len(batches), batch_count)

        mutex.busy = False
        controller.idle_timeout.status["state"] = "Ready"
        lease = controller._ensure_safety_registry().activity(
            "explicit-operation")
        self.assertTrue(controller._refresh_emergency_stop())
        self.assertIn("global.abort", controller.renderer._buttons)

        lease.release()
        self.assertTrue(controller._refresh_emergency_stop())
        self.assertNotIn("global.abort", controller.renderer._buttons)
        self.assertNotIn("global.abort", "\n".join(batches[-1]))

    def test_dashboard_refresh_redraws_only_changed_panel(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.page = FEATHER.ScreenPage.IDLE_HOME
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.extruder = StatusObject({"temperature": 20.0, "target": 0.0})
        controller.heater_bed = StatusObject({"temperature": 21.0, "target": 0.0})
        controller.toolhead = StatusObject({"homed_axes": ""})
        controller.network_status = {"mode": "ETHERNET", "ssid": "",
                                     "signal": "", "ip": "192.168.2.124"}
        controller.last_job_name = "NONE"
        controller.filament_material = "PLA"
        controller._last_dashboard = None
        controller._read_text = lambda path: ""

        with mock.patch.object(
                HOME_STATE.time, "strftime", return_value="20:00"):
            controller._update_dashboard(100)
            initial_state = controller._last_dashboard
            controller.extruder.status["temperature"] = 22.0
            controller._update_dashboard(101)

        self.assertEqual(len(batches), 2)
        update = "\n".join(batches[1])
        self.assertIn('-t "22 / 0 C"', update)
        for unchanged in (
                '21 / 0 C', 'ETHERNET', '192.168.2.124',
                'NO ACTIVE JOB', 'READY', 'MENU'):
            self.assertNotIn(unchanged, update)
        self.assertNotIn("--batch clear-hitboxes", update)
        self.assertNotIn("--batch hitbox", update)
        self.assertNotIn("--batch button", update)
        self.assertEqual(controller._last_dashboard.nozzle, 22)
        self.assertEqual(
            controller._last_dashboard._replace(nozzle=initial_state.nozzle),
            initial_state)

    def test_dashboard_worst_case_content_is_bounded(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.page = FEATHER.ScreenPage.IDLE_HOME
        controller.print_state = FEATHER.PrintState.PREPARING
        controller.renderer = FEATHER.FeatherRenderer()
        controller.reactor = Reactor()
        batches = []
        controller.renderer.send = batches.append
        controller.extruder = StatusObject(
            {"temperature": 299.0, "target": 300.0})
        controller.heater_bed = StatusObject(
            {"temperature": 129.0, "target": 130.0})
        controller.toolhead = StatusObject({"homed_axes": "xyz"})
        controller.network_status = {
            "mode": "WIFI", "ssid": "VERY-LONG-WIRELESS-NETWORK-NAME",
            "signal": "100%", "ip": "255.255.255.255",
        }
        controller.last_job_name = (
            "THIS-IS-A-VERY-LONG-PREVIOUS-PRINT-FILENAME.gcode")
        controller._current_material = (
            lambda: "CARBON-FIBER-POLYCARBONATE")
        controller.print_stats = StatusObject({
            "state": "printing", "print_duration": 359999.0,
            "info": {"current_layer": 9999, "total_layer": 9999},
        })
        controller.virtual_sdcard = type("VirtualSD", (), {
            "is_active": lambda self: True,
            "file_path": lambda self:
                "/data/THIS-IS-A-VERY-LONG-PRINT-FILENAME-FOR-UI.gcode",
        })()
        controller._print_progress = lambda eventtime, stats: 1.0
        controller._print_time_values = (
            lambda eventtime, stats, progress: (359999.0, 359999.0))
        controller.operation_context = StatusObject({
            "context_types": ("print",),
            "context_path": ("Print",),
            "current_state": "CALIBRATING AND PREPARING PRINT SURFACE",
            "revision": 1,
        })
        controller._last_dashboard = None
        controller._read_text = lambda path: ""
        controller._refresh_local_timezone = lambda: None

        controller._render_home()

        drawing = "\n".join("\n".join(batch) for batch in batches)
        for value in (
                "CARBON-FIBER-POLYCARBONATE",
                "VERY-LONG-WIRELESS-NETWORK-NAME",
                "100% //"):
            command = next(
                line for line in drawing.splitlines() if value in line)
            self.assertIn("--truncate", command)
            match = re.search(r"--max-width ([0-9]+)", command)
            self.assertIsNotNone(match)
            self.assertGreater(int(match.group(1)), 0)
            self.assertLessEqual(int(match.group(1)), UI.SCREEN_WIDTH)


    def test_calibration_menu_paginates_available_workflows(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        controller.renderer.send = lambda commands: None
        controller.params = type("Params", (), {
            "variables": {"z_offset": 0.125}})()

        controller._render_calibration_home()
        self.assertIn("cal.mesh", controller.renderer._buttons)
        self.assertIn("cal.next", controller.renderer._buttons)
        self.assertNotIn("cal.prev", controller.renderer._buttons)

        controller.calibration_page = 1
        controller._render_calibration_home()
        for action in ("cal.extruder", "cal.shaper", "cal.axes"):
            self.assertIn(action, controller.renderer._buttons)
        self.assertIn("cal.prev", controller.renderer._buttons)

        controller.calibration_page = 2
        controller._render_calibration_home()
        for action in ("cal.pid_bed", "cal.pid_extruder"):
            self.assertIn(action, controller.renderer._buttons)
        self.assertIn("cal.prev", controller.renderer._buttons)
        self.assertNotIn("cal.next", controller.renderer._buttons)

    def test_extruder_opens_guided_workflow_and_axes_keeps_measurement_guide(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller._require_idle = lambda: None
        pages = []
        controller._show_page = pages.append
        extruder_started = []
        controller._start_extruder_calibration = (
            lambda: extruder_started.append(True))

        controller._handle_calibration_action("cal.extruder")
        self.assertEqual(extruder_started, [True])

        controller._handle_calibration_action("cal.axes")
        self.assertEqual(controller.calibration_guide_kind, "axes")
        self.assertEqual(pages[-1], FEATHER.ScreenPage.CALIBRATION_GUIDE)

    def test_pid_confirm_uses_selected_material_temperature(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.calibration_kind = "pid_bed"
        controller.calibration_material = "PETG"
        controller._limited_preheat = lambda material: (250, 70)

        controller._render_calibration_confirm()

        drawing = "\n".join(batches[-1])
        self.assertIn("cal.material.PETG", drawing)
        self.assertNotIn("cal.clean.skip", drawing)

    def test_pid_and_shaper_workflows_use_supported_macros(self):
        cases = (
            ("pid_bed",
             ["PID_TUNE_BED TEMPERATURE=70", "TURN_OFF_HEATERS"]),
            ("pid_extruder",
             ["PID_TUNE_EXTRUDER TEMPERATURE=250", "TURN_OFF_HEATERS"]),
            ("shaper", ["ZSHAPER"]),
        )
        for kind, expected in cases:
            with self.subTest(kind=kind):
                controller = ScenarioController.__new__(ScenarioController)
                controller.calibration_kind = kind
                controller.calibration_material = "PETG"
                controller.calibration_error = None
                controller.calibration_cancelled = False
                controller.gcode = GCodeRecorder()
                controller._require_idle = lambda: None
                controller._limited_preheat = lambda material: (250, 70)
                pages = []
                controller._show_page = pages.append

                controller._run_calibration(0)

                self.assertEqual(controller.gcode.commands, expected)
                self.assertEqual(
                    pages, [FEATHER.ScreenPage.CALIBRATION_RESULT])

    def test_tuning_result_can_save_or_return_without_saving(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.calibration_kind = "shaper"
        controller.calibration_error = None
        controller.calibration_cancelled = False

        controller._render_calibration_result()

        drawing = "\n".join(batches[-1])
        self.assertIn("cal.tuning.discard", drawing)
        self.assertIn("cal.tuning.save", drawing)
        restarts = []
        pages = []
        controller._restart_klipper = restarts.append
        controller._show_page = pages.append
        controller._handle_calibration_action("cal.tuning.save")
        self.assertEqual(restarts, ["SAVE_CONFIG"])

        controller._handle_calibration_action("cal.tuning.discard")
        self.assertEqual(pages, [FEATHER.ScreenPage.CALIBRATION_HOME])


    def test_z_offset_summary_registers_all_positions(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        controller.renderer.send = lambda commands: None
        controller.z_calibration = ZCalibrationSession()
        controller.z_calibration.begin(
            0.125, None, "adaptive", -0.25, True)

        controller._render_z_summary()

        actions = (
            "z.zone.front_left", "z.zone.front_right", "z.zone.center",
            "z.zone.rear_left", "z.zone.rear_right")
        keys = []
        for action in actions:
            keys.append(next(
                key for key in controller.renderer._buttons
                if key == action or key.startswith(action + ".")))
        rectangles = [controller.renderer._buttons[key][:4] for key in keys]
        for index, rectangle in enumerate(rectangles):
            for other in rectangles[index + 1:]:
                self.assertFalse(UI.rectangles_overlap(rectangle, other))

    def test_z_paper_controls_are_disabled_until_probe_or_manual_start(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.reactor = Reactor()
        controller.z_calibration = ZCalibrationSession()
        controller.z_calibration.begin(0.0, None, "", -0.25, False)
        controller.z_calibration.choose_zone("center")
        controller._z_weight_gauge_commands = lambda eventtime: []

        controller._render_z_paper()

        drawing = "\n".join(batches[-1])
        self.assertIn("z.move_safe_half", drawing)
        for action in ("z.closer", "z.farther", "z.reset", "z.accept"):
            self.assertNotIn(action, drawing)

    def test_safe_z_pages_explain_measurement_and_gate_adjustment_until_probe(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.z_calibration = ZCalibrationSession()
        controller.z_calibration.begin(
            0.0, None, "", -0.25, False, safe_z=8.0)

        controller._render_safe_z_briefing()
        briefing = "\n".join(batches[-1])
        self.assertIn("CURRENT: 8.000 MM", briefing)
        self.assertIn("START HEIGHT: 16.000 MM", briefing)
        self.assertIn("z.safe.skip", briefing)
        self.assertIn("z.safe.calibrate", briefing)

        controller._render_safe_z()
        before_probe = "\n".join(batches[-1])
        self.assertIn("z.safe.probe", before_probe)
        for action in ("z.safe.lower", "z.safe.higher", "z.safe.save"):
            self.assertNotIn(action, before_probe)

        controller.z_calibration.set_safe_z_trigger(-0.4)
        controller._render_safe_z()
        after_probe = "\n".join(batches[-1])
        self.assertIn("4.600 MM", after_probe)
        for action in ("z.safe.lower", "z.safe.higher", "z.safe.save"):
            self.assertIn(action, after_probe)

    def test_live_z_offset_page_separates_saved_current_and_unsaved(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.reactor = Reactor()
        controller.print_state = FEATHER.PrintState.PRINTING
        controller.print_stats = StatusObject({"state": "printing"})
        controller.toolhead = StatusObject({"homed_axes": "xyz"})
        controller.gcode_move = StatusObject(
            {"homing_origin": (0.0, 0.0, 0.635)})
        controller.params = type("Params", (), {
            "variables": {"z_offset": 0.125, "load_zoffset": 1}})()
        controller.live_z_step = 0.005
        controller.live_z_dialog = None
        controller.z_adjust_warning_threshold = 0.3
        controller.renderer.set_header_action("global.abort", "ABORT")

        controller._render_live_z_offset()

        drawing = "\n".join(batches[-1])
        self.assertIn("+0.125 mm", drawing)
        self.assertIn("+0.635 mm", drawing)
        self.assertIn("+0.510 mm", drawing)
        self.assertIn("global.abort", controller.renderer._buttons)
        self.assertIn("live_z.save", controller.renderer._buttons)
        self.assertFalse(UI.rectangles_overlap(
            controller.renderer._buttons["global.abort"][:4],
            controller.renderer._buttons["live_z.save"][:4]))

    def test_live_z_offset_load_warning_has_explicit_choice(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.reactor = Reactor()
        controller.print_state = FEATHER.PrintState.PAUSED
        controller.print_stats = StatusObject({"state": "paused"})
        controller.toolhead = StatusObject({"homed_axes": "xyz"})
        controller.gcode_move = StatusObject(
            {"homing_origin": (0.0, 0.0, 0.2)})
        controller.params = type("Params", (), {
            "variables": {"z_offset": 0.1, "load_zoffset": 0}})()
        controller.live_z_step = 0.01
        controller.live_z_dialog = "save"
        controller.z_adjust_warning_threshold = 0.3

        controller._render_live_z_offset()

        drawing = "\n".join(batches[-1])
        self.assertIn("live_z.save.no", drawing)
        self.assertIn("live_z.save.yes", drawing)
        self.assertNotIn("global.abort", controller.renderer._buttons)

    def test_weight_gauge_uses_history_and_expands_without_clamping(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        controller.reactor = Reactor()
        controller.weight_sensor = StatusObject({
            "temperature": 100.0,
            "measured_min_temp": 80.0,
            "measured_max_temp": 120.0,
        })
        controller.z_weight_gauge = None

        controller._begin_z_weight_gauge()

        gauge = controller.z_weight_gauge
        self.assertEqual(gauge, {
            "initial": 100.0,
            "minimum": 80.0,
            "maximum": 120.0,
            "value": 100.0,
        })
        initial_commands = controller._z_weight_gauge_commands(0)
        initial_marker = next(
            line for line in initial_commands
            if "-s 58 2 -c b47aff" in line)
        self.assertFalse(any('-t "START"' in line
                             for line in initial_commands))
        self.assertFalse(any('-t "+80.0"' in line
                             for line in initial_commands))
        self.assertFalse(any('-t "+120.0"' in line
                             for line in initial_commands))

        controller.weight_sensor.status.update({
            "temperature": 160.0,
            "measured_max_temp": 160.0,
        })
        expanded_commands = controller._z_weight_gauge_commands(1)
        expanded_marker = next(
            line for line in expanded_commands
            if "-s 58 2 -c b47aff" in line)

        self.assertEqual(gauge["initial"], 100.0)
        self.assertEqual(gauge["minimum"], 80.0)
        self.assertEqual(gauge["maximum"], 160.0)
        self.assertEqual(gauge["value"], 160.0)
        self.assertNotEqual(initial_marker, expanded_marker)

        controller.weight_sensor.status.update({
            "temperature": 40.0,
            "measured_min_temp": 40.0,
        })
        controller._update_z_weight_gauge(2)
        self.assertEqual(gauge["minimum"], 40.0)
        self.assertEqual(gauge["maximum"], 160.0)
        self.assertEqual(gauge["initial"], 100.0)

    def test_weight_gauge_ignores_uninitialized_sensor_extrema(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.reactor = Reactor()
        controller.weight_sensor = StatusObject({
            "temperature": 0.0,
            "measured_min_temp": 99999999.0,
            "measured_max_temp": 0.0,
        })
        controller.z_weight_gauge = None

        controller._begin_z_weight_gauge()

        self.assertEqual(controller.z_weight_gauge, {
            "initial": 0.0,
            "minimum": 0.0,
            "maximum": 0.0,
            "value": 0.0,
        })

    def test_weight_gauge_turns_red_only_above_four_hundred(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        self.assertTrue(controller.renderer.set_theme("SYNTH"))
        primary = controller.renderer.color(UI.ThemeColor.PRIMARY)
        danger_color = controller.renderer.color(UI.ThemeColor.DANGER)
        controller.reactor = Reactor()
        controller.weight_sensor = StatusObject({
            "temperature": 400.0,
            "measured_min_temp": 0.0,
            "measured_max_temp": 400.0,
        })
        controller.z_weight_gauge = None

        normal = controller._z_weight_gauge_commands(0)
        self.assertTrue(
            any("-c %s" % primary in line for line in normal))
        self.assertFalse(
            any("-c %s" % danger_color in line for line in normal))

        controller.weight_sensor.status.update({
            "temperature": 401.0,
            "measured_max_temp": 401.0,
        })
        danger = controller._z_weight_gauge_commands(1)
        self.assertTrue(
            any("-c %s" % danger_color in line for line in danger))

    def test_screw_calibration_confirm_offers_clean_and_cooldown_paths(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        controller.renderer.send = lambda commands: None
        controller.calibration_kind = "screws"
        controller.calibration_material = "PETG"

        controller.calibration_clean_nozzle = True
        controller._render_calibration_confirm()
        self.assertEqual(
            controller.renderer._buttons["cal.material.PETG"][5],
            "selected")
        self.assertEqual(
            controller.renderer._buttons["cal.clean.skip"][5], "enabled")
        self.assertIn("cal.material.ABS-PC", controller.renderer._buttons)
        self.assertIn("cal.confirm", controller.renderer._buttons)

        controller.calibration_clean_nozzle = False
        controller._render_calibration_confirm()
        self.assertEqual(
            controller.renderer._buttons["cal.clean.skip"][5], "selected")
        self.assertTrue(all(
            spec[5] != "selected"
            for action, spec in controller.renderer._buttons.items()
            if action.startswith("cal.material.")))

    def test_mesh_cleaning_uses_complete_shared_material_selector(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        controller.renderer.send = lambda commands: None
        controller.calibration_kind = "mesh"
        controller.calibration_material = "ABS-PC"

        controller._render_calibration_confirm()

        for material in controller.heating_materials:
            self.assertIn(
                "cal.material.%s" % material, controller.renderer._buttons)
        self.assertEqual(
            controller.renderer._buttons["cal.material.ABS-PC"][5],
            "selected")

    def test_empty_heating_disables_mesh_but_preserves_no_clean_screws(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.heating_materials = ()
        controller.heating_profiles = {}
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.calibration_material = "n/a"
        controller.calibration_clean_nozzle = True

        controller.calibration_kind = "mesh"
        controller._render_calibration_confirm()
        mesh = "\n".join(batches[-1])
        self.assertNotIn("cal.material.", mesh)
        self.assertNotIn(":cal.confirm", mesh)

        controller.calibration_kind = "screws"
        controller._render_calibration_confirm()
        screws = "\n".join(batches[-1])
        self.assertIn("cal.clean.skip", screws)
        self.assertNotIn(":cal.confirm", screws)
        controller.calibration_clean_nozzle = False
        controller._render_calibration_confirm()
        screws = "\n".join(batches[-1])
        self.assertIn(":cal.confirm", screws)

    def test_screw_calibration_marks_only_current_phase_with_accent(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        controller.calibration_kind = "screws"
        controller.calibration_clean_nozzle = True
        controller.calibration_repeat_probe = False

        controller._calibration_stage_commands("CALIBRATION: STARTING")

        with mock.patch.object(
                controller.renderer, "text",
                wraps=controller.renderer.text) as text:
            controller._calibration_stage_commands("BED SCREWS: HEATING")

        colors = dict((call.args[2], call.args[3])
                      for call in text.call_args_list)
        self.assertEqual(colors["PREP"], UI.ThemeColor.PRIMARY)
        self.assertEqual(colors["HEAT"], UI.ThemeColor.BRIGHT)
        for stage in ("CLEAN", "PROBE", "DONE"):
            self.assertEqual(colors[stage], UI.ThemeColor.TEXT)

    def test_mesh_progress_uses_current_state_not_context_path(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        controller.calibration_kind = "mesh"

        controller._calibration_stage_commands("CALIBRATION: STARTING")
        with mock.patch.object(
                controller.renderer, "text",
                wraps=controller.renderer.text) as text:
            controller._calibration_stage_commands(
                "BED LEVEL -> BED MESH -> NOZZLE CLEANING -> CLEANING", {
                    "context_path": (
                        "Bed Level", "Bed Mesh", "Nozzle Cleaning"),
                    "current_state": "CLEANING",
                })

        colors = dict((call.args[2], call.args[3])
                      for call in text.call_args_list)
        self.assertEqual(colors["PREP"], UI.ThemeColor.PRIMARY)
        self.assertEqual(colors["CLEAN"], UI.ThemeColor.BRIGHT)
        self.assertEqual(colors["LEVEL"], UI.ThemeColor.TEXT)

    def test_mesh_progress_does_not_regress_during_post_clean_cooling(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        controller.calibration_kind = "mesh"
        operation = {
            "context_path": ("Bed Level", "Bed Mesh", "Nozzle Cleaning"),
            "current_state": None,
        }

        def active_phase(state):
            operation["current_state"] = state
            with mock.patch.object(
                    controller.renderer, "text",
                    wraps=controller.renderer.text) as text:
                controller._calibration_stage_commands("", operation)
            return next(
                call.args[2] for call in text.call_args_list
                if call.args[3] == UI.ThemeColor.BRIGHT)

        self.assertEqual([
            active_phase("HEATING NOZZLE"),
            active_phase("CLEANING"),
            active_phase("COOLING NOZZLE"),
            active_phase("LEVELING"),
        ], ["HEAT", "CLEAN", "CLEAN", "LEVEL"])

    def test_context_name_without_state_does_not_advance_progress(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        controller.calibration_kind = "mesh"

        with mock.patch.object(
                controller.renderer, "text",
                wraps=controller.renderer.text) as text:
            controller._calibration_stage_commands("BED LEVEL", {
                "context_path": ("Bed Level",),
                "current_state": None,
            })

        colors = dict((call.args[2], call.args[3])
                      for call in text.call_args_list)
        self.assertEqual(colors["PREP"], UI.ThemeColor.BRIGHT)
        self.assertEqual(colors["LEVEL"], UI.ThemeColor.TEXT)

    def test_skipped_and_pending_calibration_phases_use_distinct_colors(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        controller.calibration_kind = "mesh"

        controller._calibration_stage_commands("CALIBRATION: STARTING")
        with mock.patch.object(
                controller.renderer, "text",
                wraps=controller.renderer.text) as text:
            controller._calibration_stage_commands(
                "BED LEVEL -> BED MESH -> NOZZLE CLEANING -> HEATING NOZZLE")

        colors = dict((call.args[2], call.args[3])
                      for call in text.call_args_list)
        self.assertEqual(colors["PREP"], UI.ThemeColor.PRIMARY)
        self.assertEqual(colors["HOME"], UI.ThemeColor.DIM)
        self.assertEqual(colors["HEAT"], UI.ThemeColor.BRIGHT)
        labels = [call.args[2] for call in text.call_args_list]
        self.assertNotIn("SKIPPED", labels)
        self.assertNotIn("PENDING", labels)
        self.assertNotIn("ACTIVE", labels)

    def test_cancelable_calibration_offers_context_cancel_and_global_abort(self):
        for kind in ("screws", "mesh", "z"):
            with self.subTest(kind=kind):
                controller = ScenarioController.__new__(ScenarioController)
                controller.renderer = FEATHER.FeatherRenderer()
                controller.calibration_kind = kind
                controller.calibration_repeat_probe = False
                controller.calibration_clean_nozzle = True
                controller.calibration_cancel_requested = False
                controller.operation_context = type("Contexts", (), {
                    "get_status": lambda self, eventtime: {
                        "context_path": ("Calibration",),
                        "current_state": "HEATING NOZZLE",
                        "cancel_available": True,
                        "cancel_pending": False,
                        "cancel_target_name": "Calibration",
                        "revision": 1,
                    }})()
                controller.reactor = Reactor()
                controller.renderer.send = lambda commands: None
                controller.renderer.set_header_action("global.abort", "ABORT")

                controller._render_calibration_progress()

                self.assertIn(
                    "cal.cancel", controller.renderer._buttons)
                self.assertEqual(
                    controller.renderer._buttons["cal.cancel"][5],
                    "danger")
                self.assertIn("global.abort", controller.renderer._buttons)

    def test_z_preparation_has_clean_and_no_clean_command_paths(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.calibration_material = "ABS-PC"
        controller._limited_preheat = lambda material: (270, 105)
        controller.params = type("Params", (), {
            "variables": {"clear_cooldown_temp": 120}})()

        controller.calibration_clean_nozzle = True
        clean = controller._z_preparation_command()
        self.assertIn(
            "CLEAR_NOZZLE EXTRUDER_TEMP=270 BED_TEMP=105", clean)
        self.assertIn("_CONTEXT_BEGIN TYPE=z_offset", clean)
        self.assertIn("_CONTEXT_STATE NAME=TARING", clean)
        self.assertIn("_CONTEXT_END", clean)
        self.assertIn("MOVE_SAFE Z=20 ABSOLUTE=1 F=600", clean)
        self.assertIn("LOAD_CELL_TARE", clean)

        controller.calibration_clean_nozzle = False
        no_clean = controller._z_preparation_command()
        self.assertIn("M104 S120", no_clean)
        self.assertIn("_HOME_IF_NEEDED", no_clean)
        self.assertIn(
            "_WAIT_TEMPERATURE CMD=M104 VALUE=120", no_clean)
        self.assertIn("_CONTEXT_STATE NAME=HEATING", no_clean)
        self.assertNotIn("_CONTEXT_STATE NAME=HOMING", no_clean)
        self.assertNotIn("\nG28\n", no_clean)
        self.assertNotIn("M140", no_clean)
        self.assertNotIn("CLEAR_NOZZLE", no_clean)
        self.assertIn("MOVE_SAFE Z=20 ABSOLUTE=1 F=600", no_clean)
        self.assertIn("LOAD_CELL_TARE", no_clean)

    def test_z_progress_stages_match_cleaning_choice(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        controller.calibration_kind = "z"
        controller.calibration_clean_nozzle = True
        clean = "\n".join(controller._calibration_stage_commands(
            "Z OFFSET: TARE"))
        for stage in ("PREP", "HOME", "HEAT", "CLEAN", "TARE", "READY"):
            self.assertIn('-t "%s"' % stage, clean)

        controller.calibration_clean_nozzle = False
        no_clean = "\n".join(controller._calibration_stage_commands(
            "Z OFFSET: TARE"))
        self.assertNotIn('-t "CLEAN"', no_clean)

    def test_z_session_captures_runtime_and_exact_mesh_before_clearing(self):
        mesh_object = object()
        controller = ScenarioController.__new__(ScenarioController)
        controller.reactor = DeferredReactor()
        controller.gcode = GCodeRecorder()
        controller.gcode_move = StatusObject({
            "homing_origin": (0.0, 0.0, 0.187)})
        controller.bed_mesh = BedMeshState(mesh_object, "adaptive-run")
        controller.probe = type("Probe", (), {"z_offset": -0.25})()
        controller.params = type("Params", (), {
            "variables": {"load_zoffset": 1}})()
        controller.z_calibration = ZCalibrationSession()
        controller._require_idle = lambda: None
        pages = []
        controller._show_page = pages.append

        controller._start_z_calibration()

        session = controller.z_calibration
        self.assertTrue(session.active)
        self.assertEqual(session.original_runtime_offset, 0.187)
        self.assertIs(session.original_mesh, mesh_object)
        self.assertEqual(session.original_mesh_profile, "adaptive-run")
        self.assertEqual(controller.gcode.commands, [
            "_CANCEL_DELAYED_COMMANDS",
            "SET_SKEW CLEAR=1",
            "_SET_GCODE_OFFSET Z=0 MOVE=0",
            "BED_MESH_CLEAR"])
        self.assertEqual(pages, [FEATHER.ScreenPage.SAFE_Z_BRIEFING])
        self.assertEqual(controller.reactor.callbacks, [])

    def test_z_session_entry_failure_restores_runtime_and_mesh(self):
        class FailClear(GCodeRecorder):
            def run_script_from_command(self, command):
                super().run_script_from_command(command)
                if command == "BED_MESH_CLEAR":
                    raise RuntimeError("clear failed")

        mesh_object = object()
        controller = ScenarioController.__new__(ScenarioController)
        controller.reactor = Reactor()
        controller.gcode = FailClear()
        controller.gcode_move = StatusObject({
            "homing_origin": (0.0, 0.0, -0.187)})
        controller.bed_mesh = BedMeshState(None, "")
        controller.bed_mesh.z_mesh = mesh_object
        controller.bed_mesh.status["profile_name"] = "temporary"
        controller.probe = type("Probe", (), {"z_offset": -0.25})()
        controller.params = type("Params", (), {
            "variables": {"load_zoffset": 0}})()
        controller.z_calibration = ZCalibrationSession()
        controller._require_idle = lambda: None

        with self.assertRaisesRegex(RuntimeError, "clear failed"):
            controller._start_z_calibration()

        self.assertEqual(controller.gcode.commands, [
            "_CANCEL_DELAYED_COMMANDS",
            "SET_SKEW CLEAR=1",
            "_SET_GCODE_OFFSET Z=0 MOVE=0",
            "BED_MESH_CLEAR",
            "_SET_GCODE_OFFSET Z=-0.187000 MOVE=0",
        ])
        self.assertIs(controller.bed_mesh.z_mesh, mesh_object)
        self.assertEqual(controller.bed_mesh.profile_name, "temporary")
        self.assertFalse(controller.z_calibration.active)

    def test_z_normal_exit_restores_absent_named_and_temporary_mesh(self):
        for original, profile in (
                (None, ""), (object(), "auto"),
                (object(), "adaptive-run")):
            with self.subTest(profile=profile or "absent"):
                controller = ScenarioController.__new__(ScenarioController)
                controller.reactor = Reactor()
                controller.gcode = GCodeRecorder()
                controller.toolhead = StatusObject({
                    "homed_axes": "xyz",
                    "position": (0.0, 0.0, 0.2, 0.0)})
                controller.bed_mesh = BedMeshState(None, "")
                controller.z_calibration = ZCalibrationSession()
                controller.z_calibration.begin(
                    0.321, original, profile, -0.25, False)

                controller._finish_z_calibration(None)

                command = "\n".join(controller.gcode.commands)
                self.assertIn(
                    "MOVE_SAFE Z=10 ABSOLUTE=1 F=600", command)
                self.assertIn("TURN_OFF_HEATERS", command)
                self.assertIn(
                    "_SET_GCODE_OFFSET Z=+0.321000 MOVE=0", command)
                self.assertIs(controller.bed_mesh.z_mesh, original)
                self.assertEqual(controller.bed_mesh.profile_name, profile)
                self.assertFalse(controller.z_calibration.active)

    def test_z_save_applies_runtime_persists_offset_and_auto_load(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.reactor = Reactor()
        controller.gcode = GCodeRecorder()
        controller.toolhead = StatusObject({
            "homed_axes": "xyz", "position": (0.0, 0.0, 0.2, 0.0)})
        controller.bed_mesh = BedMeshState(None, "")
        controller.z_calibration = ZCalibrationSession()
        controller.z_calibration.begin(
            -0.100, None, "", -0.25, True)

        controller._finish_z_calibration(0.123)

        command = "\n".join(controller.gcode.commands)
        self.assertIn("_SET_GCODE_OFFSET Z=+0.123000 MOVE=0", command)
        self.assertIn("SET_MOD PARAM=z_offset VALUE=0.123", command)
        self.assertIn("SET_MOD PARAM=load_zoffset VALUE=1", command)
        self.assertIn("TURN_OFF_HEATERS", command)

    def test_z_pressure_dialog_is_suppressed_during_probe_then_rearms(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.reactor = Reactor()
        controller.page = FEATHER.ScreenPage.Z_OFFSET_PAPER
        controller.weight_sensor = StatusObject({
            "temperature": 850.0,
            "measured_min_temp": 0.0,
            "measured_max_temp": 850.0,
        })
        controller.z_weight_gauge = None
        controller.z_calibration = ZCalibrationSession()
        controller.z_calibration.begin(
            0.0, None, "", -0.25, False)
        rendered = []
        controller._render_z_paper = lambda: rendered.append(True)

        controller.z_calibration.probing = True
        self.assertFalse(controller._check_z_pressure(100.0))
        self.assertIsNone(controller.z_calibration.dialog)

        controller.z_calibration.probing = False
        self.assertTrue(controller._check_z_pressure(100.0))
        self.assertEqual(controller.z_calibration.dialog, "pressure")
        self.assertEqual(rendered, [True])
        self.assertFalse(controller._check_z_pressure(101.0))

        controller.weight_sensor.status["temperature"] = 599.0
        self.assertFalse(controller._check_z_pressure(102.0))
        controller.z_calibration.dialog = None
        controller.weight_sensor.status["temperature"] = 850.0
        self.assertTrue(controller._check_z_pressure(103.0))

    def test_z_cancelled_preparation_turns_off_heaters_and_restores_state(self):
        class AbortPreparation:
            def __init__(self):
                self.commands = []

            def run_script_from_command(self, command):
                self.commands.append(command)
                if len(self.commands) == 1:
                    raise RuntimeError("cancelled")

        original_mesh = object()
        controller = ScenarioController.__new__(ScenarioController)
        controller.reactor = Reactor()
        controller.gcode = AbortPreparation()
        controller.page = FEATHER.ScreenPage.CALIBRATION_PROGRESS
        controller.print_state = FEATHER.PrintState.IDLE
        controller.shutdown_active = False
        controller.toolhead = StatusObject({
            "homed_axes": "xyz", "position": (0.0, 0.0, 1.0, 0.0)})
        controller.bed_mesh = BedMeshState(None, "")
        controller.params = type("Params", (), {
            "variables": {"clear_cooldown_temp": 120}})()
        controller.calibration_clean_nozzle = False
        controller.calibration_cancel_requested = True
        controller.calibration_cancel_dispatched = True
        controller.z_calibration = ZCalibrationSession()
        controller.z_calibration.begin(
            0.111, original_mesh, "adaptive", -0.25, False)
        controller._require_idle = lambda: None
        messages = []
        controller._show_message = (
            lambda message, page: messages.append((message, page)))

        controller._run_z_calibration_preparation(100.0)

        self.assertEqual(len(controller.gcode.commands), 4)
        cleanup = "\n".join(controller.gcode.commands[1:])
        self.assertIn("TURN_OFF_HEATERS", cleanup)
        self.assertIn("_SET_GCODE_OFFSET Z=+0.111000 MOVE=0", cleanup)
        self.assertIs(controller.bed_mesh.z_mesh, original_mesh)
        self.assertEqual(controller.bed_mesh.profile_name, "adaptive")
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0][1], FEATHER.ScreenPage.CALIBRATION_HOME)

    def test_z_shutdown_clears_local_session_without_replacing_error_page(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.gcode = FailingGCode()
        controller.page = FEATHER.ScreenPage.ERROR
        controller.print_state = FEATHER.PrintState.IDLE
        controller.shutdown_active = True
        controller.calibration_clean_nozzle = False
        controller.z_calibration = ZCalibrationSession()
        controller.z_calibration.begin(
            0.111, object(), "adaptive", -0.25, False)
        controller._require_idle = lambda: None
        controller._z_preparation_command = lambda: "FAIL"
        pages = []
        controller._show_page = pages.append
        controller._show_message = (
            lambda message, page: pages.append((message, page)))

        controller._run_z_calibration_preparation(100.0)

        self.assertFalse(controller.z_calibration.active)
        self.assertEqual(controller.page, FEATHER.ScreenPage.ERROR)
        self.assertEqual(pages, [])

    def test_calibration_cancel_requests_context_domain_once(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.calibration_kind = "mesh"
        controller.calibration_cancel_requested = False
        controller.calibration_cancel_dispatched = False
        controller.reactor = Reactor()
        controller.operation_cancel_return_page = FEATHER.ScreenPage.IDLE_HOME
        controller.operation_cancel_on_accept = None
        controller.operation_cancel_on_clear = None
        controller.operation_cancel_request_id = None
        controller.operation_cancel_target_name = None
        controller.operation_cancel_target_mode = None
        controller.cancel_mode = None
        controller._show_page = lambda page: setattr(controller, "page", page)
        controller._render_cancel_confirm = lambda: None
        requests = []
        controller.operation_context = type("Contexts", (), {
            "get_status": lambda self, eventtime: {
                "context_path": ("Bed Level",),
                "current_state": "LEVELING",
                "cancel_available": True,
                "cancel_pending": False,
                "cancel_request_id": None,
                "cancel_target_name": "Bed Level",
                "cancel_target_mode": "cancelable",
                "revision": 1,
            },
            "request_cancel": lambda self: (
                requests.append("cancel") or {
                    "accepted": True, "status": "accepted",
                    "request_id": 1, "target_name": "Bed Level",
                    "target_mode": "cancelable"})})()

        controller._handle_calibration_action("cal.cancel")
        controller._handle_operation_cancel_action(
            "operation.cancel.confirm")
        controller._handle_operation_cancel_action(
            "operation.cancel.confirm")

        self.assertEqual(requests, ["cancel"])
        self.assertTrue(controller.calibration_cancel_requested)
        self.assertTrue(controller.calibration_cancel_dispatched)

    def test_calibration_cancel_dialog_is_not_blocked_by_active_macro(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.page = FEATHER.ScreenPage.CALIBRATION_PROGRESS
        controller.calibration_kind = "mesh"
        controller.command_depth = 1
        controller.mod_update_pending = False
        controller.touch_feedback_pending = False
        controller.renderer = type("Renderer", (), {
            "flash_button": lambda self, action: True,
            "generation": 1,
        })()
        cancelled = []
        controller._open_calibration_cancel = lambda: cancelled.append(True)

        controller._handle_touch_action("cal.cancel")

        self.assertEqual(cancelled, [True])
        self.assertFalse(controller.touch_feedback_pending)

    def test_same_page_redraw_does_not_discard_delayed_button_action(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.page = FEATHER.ScreenPage.SETTINGS
        controller.touch_feedback_pending = True
        restored = []
        controller.renderer = type("Renderer", (), {
            "generation": 2,
            "restore_button": lambda self, action: restored.append(action),
        })()
        dispatched = []
        controller._dispatch_action = dispatched.append

        controller._finish_touch_action(
            0, "settings.theme.next",
            source_page=FEATHER.ScreenPage.SETTINGS, generation=1)

        self.assertEqual(restored, [])
        self.assertEqual(dispatched, ["settings.theme.next"])
        self.assertFalse(controller.touch_feedback_pending)

    def test_page_change_discards_delayed_button_action(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.page = FEATHER.ScreenPage.IDLE_HOME
        controller.touch_feedback_pending = True
        controller.renderer = type("Renderer", (), {"generation": 2})()
        dispatched = []
        controller._dispatch_action = dispatched.append

        controller._finish_touch_action(
            0, "settings.theme.next",
            source_page=FEATHER.ScreenPage.SETTINGS, generation=1)

        self.assertEqual(dispatched, [])
        self.assertFalse(controller.touch_feedback_pending)

    def test_homing_progress_keeps_global_abort_registered(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        controller.calibration_kind = "mesh"
        controller.calibration_repeat_probe = False
        controller.calibration_cancel_requested = False
        controller.temperature_wait = type(
            "Wait", (), {"variables": {
                "active": False, "cancel": False}})()
        operation = {
            "context_types": ("bed_level",),
            "context_path": ("Bed Mesh",),
            "current_state": None,
            "revision": 1,
        }
        controller.operation_context = type("Contexts", (), {
            "get_status": lambda self, eventtime: dict(operation),
        })()
        controller.reactor = Reactor()
        batches = []
        controller.renderer.send = batches.append
        controller.renderer.set_header_action("global.abort", "ABORT")
        controller._render_calibration_progress()
        initial_generation = controller.renderer.generation

        operation.update(current_state="HOMING", revision=2)
        controller._update_calibration_progress()

        self.assertGreater(controller.renderer.generation, initial_generation)
        self.assertIn("global.abort", controller.renderer._buttons)
        self.assertIn("--batch clear-hitboxes", "\n".join(batches[-1]))

    def test_global_abort_bypasses_busy_and_touch_feedback(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.page = FEATHER.ScreenPage.MAIN_MENU
        controller.print_state = FEATHER.PrintState.IDLE
        controller.command_depth = 1
        controller.mod_update_pending = False
        controller.touch_feedback_pending = True
        immediate = []
        controller._run_immediate_command = immediate.append
        lease = controller._ensure_safety_registry().activity("test-operation")

        controller._handle_touch_action("global.abort")

        self.assertEqual(immediate, ["M112"])

        lease.release()
        controller.command_depth = 0
        immediate[:] = []
        controller._handle_touch_action("global.abort")
        self.assertEqual(immediate, [])

    def test_screw_repeat_progress_marks_probe_as_current_stage(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        controller.calibration_kind = "screws"
        controller.calibration_repeat_probe = True

        with mock.patch.object(
                controller.renderer, "text",
                wraps=controller.renderer.text) as text:
            controller._calibration_stage_commands("BED SCREWS: PROBING")

        colors = dict((call.args[2], call.args[3])
                      for call in text.call_args_list)
        self.assertEqual(colors["PROBE"], UI.ThemeColor.BRIGHT)
        self.assertEqual(colors["DONE"], UI.ThemeColor.TEXT)

    def test_settings_use_switch_for_sound_and_show_light_level(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        controller.reactor = Reactor()
        batches = []
        controller.renderer.send = batches.append
        controller.params = type("Params", (), {"variables": {
            "backlight": 50, "backlight_eco": 10, "sound": 1,
            "chamber_light": 40}})()
        controller.chamber_light = StatusObject({
            "color_data": [(0.0, 0.0, 0.0, 0.0)]})

        controller._render_settings()

        drawing = "\n".join(batches[0])
        self.assertIn('-t "40%"', drawing)
        self.assertIn("settings.sound", controller.renderer._toggles)
        self.assertTrue(controller.renderer._toggles["settings.sound"][4])
        for action in (
                "settings.brightness.minus", "settings.brightness.plus",
                "settings.led.minus", "settings.led.plus",
                "settings.theme", "settings.mod"):
            self.assertIn(action, controller.renderer._buttons)
        self.assertNotIn('[ OFF |', drawing)
        self.assertNotIn('[ >OFF< |', drawing)

    def test_mod_settings_list_scrolls_and_uses_toggle_controls(self):
        params = [mod_param("flag%d" % index, bool, False,
                            "Feature %d" % index,
                            "Feature description %d." % index)
                  for index in range(7)]
        controller = mod_controller(
            params, {param.key: False for param in params})

        controller._render_mod_settings()
        first = "\n".join(controller.draw_batches[-1])
        self.assertIn("01-05 / 07", first)
        self.assertEqual(
            set(controller.renderer._toggles),
            {"mod.item.%d" % index for index in range(5)})
        self.assertIn("mod.next", controller.renderer._buttons)
        self.assertNotIn("mod.prev", controller.renderer._buttons)
        self.assertNotIn('-t "^"', first)
        self.assertNotIn('-t "v"', first)

        controller._handle_mod_action("mod.next")
        second = "\n".join(controller.draw_batches[-1])
        self.assertIn("06-07 / 07", second)
        self.assertEqual(
            set(controller.renderer._toggles),
            {"mod.item.5", "mod.item.6"})
        self.assertIn("mod.prev", controller.renderer._buttons)
        self.assertNotIn("mod.next", controller.renderer._buttons)

    def test_mod_settings_repeats_category_headings_inside_the_list(self):
        params = [
            mod_param("a%d" % index, bool, False, "A %d" % index,
                      ui_category="first") for index in range(6)] + [
            mod_param("b0", bool, False, "B 0", ui_category="second")]
        controller = mod_controller(
            params, dict((param.key, False) for param in params))
        controller.params.ui_categories_map["first"].label = "FIRST"
        controller.params.ui_categories_map["second"].label = "SECOND"

        controller._render_mod_settings()
        first = "\n".join(controller.draw_batches[-1])
        self.assertIn('-t "FIRST"', first)
        self.assertIn('-t "01-05 / 06"', first)
        self.assertNotIn("SECOND", first)
        self.assertEqual(
            set(controller.renderer._toggles),
            {"mod.item.%d" % index for index in range(5)})

        # The last row of FIRST keeps a heading of its own instead of leaving
        # the reader without context, and SECOND opens its own section below.
        controller._handle_mod_action("mod.next")
        second = "\n".join(controller.draw_batches[-1])
        self.assertIn('-t "FIRST (CONT.)"', second)
        self.assertIn('-t "06-06 / 06"', second)
        self.assertIn('-t "SECOND"', second)
        self.assertIn('-t "01-01 / 01"', second)
        self.assertEqual(set(controller.renderer._toggles),
                         {"mod.item.5", "mod.item.6"})

        controller._handle_mod_action("mod.prev")
        back = "\n".join(controller.draw_batches[-1])
        self.assertIn('-t "FIRST"', back)
        self.assertNotIn("(CONT.)", back)

    def test_mod_settings_turns_the_leftover_row_into_the_way_forward(self):
        params = [
            mod_param("a%d" % index, bool, False, "A %d" % index,
                      ui_category="first") for index in range(9)] + [
            mod_param("b0", bool, False, "B 0", ui_category="second")]
        controller = mod_controller(
            params, dict((param.key, False) for param in params))
        controller.params.ui_categories_map["first"].label = "FIRST"
        controller.params.ui_categories_map["second"].label = "SECOND"

        controller._render_mod_settings()
        self.assertNotIn("mod.more", controller.renderer._hitboxes)

        # SECOND needs a heading and a row, and only one row of space is left.
        # An empty strip there would read as the end of the list, so the page
        # spends it on a tappable card that leads to SECOND.
        controller._handle_mod_action("mod.next")
        second = "\n".join(controller.draw_batches[-1])
        self.assertIn('-t "NEXT: SECOND >"', second)
        self.assertEqual(
            set(controller.renderer._toggles),
            {"mod.item.%d" % index for index in range(5, 9)})

        # The card is an ordinary list block that happens to be touchable, not
        # a button, so it fills one row slot inside the list without button
        # chrome, and the page lets the tap through.
        self.assertNotIn("mod.more", controller.renderer._buttons)
        x, y, width, height = controller.renderer._hitboxes["mod.more"][:4]
        self.assertEqual((x, width, height),
                         (MOD_UI.LIST_X, MOD_UI.LIST_WIDTH, MOD_UI.ITEM_HEIGHT))
        self.assertLessEqual(y + height, MOD_UI.LIST_BOTTOM)
        self.assertTrue(controller.allows_action(
            FEATHER.ScreenPage.MOD_SETTINGS, "mod.more"))

        controller._handle_mod_action("mod.more")
        third = "\n".join(controller.draw_batches[-1])
        self.assertEqual(controller.mod_page, 2)
        self.assertIn('-t "SECOND"', third)
        self.assertNotIn("mod.more", controller.renderer._hitboxes)

    def test_mod_dependency_toggle_repaginates_and_preserves_anchor(self):
        condition = {"parameter": "parent", "operator": "equals",
                     "value": True}
        params = [
            mod_param("before%d" % index, bool, False, "Before %d" % index,
                      ui_category="first") for index in range(4)] + [
            mod_param("parent", bool, False, "Parent", ui_category="first"),
            mod_param("child", int, 1, "Child", ui_category="first",
                      ui_visible_if=condition),
            mod_param("tail", bool, False, "Tail", ui_category="second"),
        ]
        controller = mod_controller(
            params, dict((param.key, param.default) for param in params))
        controller.params.ui_categories_map["first"].label = "FIRST"
        controller.params.ui_categories_map["second"].label = "SECOND"

        controller._render_mod_settings()
        self.assertIn('-t "01-05 / 05"', "\n".join(controller.draw_batches[-1]))
        controller._handle_mod_action("mod.item.4")

        expanded = "\n".join(controller.draw_batches[-1])
        self.assertIn('-t "01-05 / 06"', expanded)
        self.assertEqual(controller.mod_page, 0)
        controller._handle_mod_action("mod.next")
        expanded_second = "\n".join(controller.draw_batches[-1])
        self.assertIn('-t "FIRST (CONT.)"', expanded_second)
        self.assertIn('-t "06-06 / 06"', expanded_second)
        self.assertIn('-t "SECOND"', expanded_second)
        self.assertEqual(
            set(controller.renderer._buttons) & {"mod.item.5", "mod.item.6"},
            {"mod.item.5"})

        controller._handle_mod_action("mod.prev")
        controller._handle_mod_action("mod.item.4")
        self.assertIn('-t "01-05 / 05"', "\n".join(controller.draw_batches[-1]))
        controller.mod_page = 99
        controller._render_mod_settings()
        self.assertEqual(controller.mod_page, 1)
        collapsed_second = "\n".join(controller.draw_batches[-1])
        self.assertIn('-t "SECOND"', collapsed_second)
        self.assertIn('-t "01-01 / 01"', collapsed_second)
        self.assertNotIn("FIRST", collapsed_second)

    def test_mod_enum_dependency_updates_after_apply_in_both_directions(self):
        Swap = enum.Enum("Swap", {"OFF": 0, "ZRAM": 3})
        condition = {"parameter": "use_swap", "operator": "equals",
                     "value": "ZRAM"}
        params = [
            mod_param("before%d" % index, bool, False, "Before %d" % index)
            for index in range(4)] + [
            mod_param("use_swap", Swap, 0, "Swap"),
            mod_param("zram_algo", str, "zstd", "Compression",
                      ui_visible_if=condition),
        ]
        controller = mod_controller(
            params, dict((param.key, param.default) for param in params))

        controller._render_mod_settings()
        self.assertIn("01-05 / 05", "\n".join(controller.draw_batches[-1]))
        controller._handle_mod_action("mod.item.4")
        controller._handle_mod_action("mod.option.1")
        controller._handle_mod_action("mod.apply")

        self.assertEqual(controller.page, FEATHER.ScreenPage.MOD_SETTINGS)
        self.assertIn("01-05 / 06", "\n".join(controller.draw_batches[-1]))
        self.assertEqual(controller.mod_page, 0)
        controller._handle_mod_action("mod.next")
        self.assertIn("06-06 / 06", "\n".join(controller.draw_batches[-1]))

        controller._handle_mod_action("mod.prev")
        controller._handle_mod_action("mod.item.4")
        controller._handle_mod_action("mod.option.0")
        controller._handle_mod_action("mod.apply")

        self.assertEqual(controller.mod_page, 0)
        self.assertIn("01-05 / 05", "\n".join(controller.draw_batches[-1]))

    def test_stale_mod_action_index_never_opens_a_different_parameter(self):
        condition = {"parameter": "parent", "operator": "equals",
                     "value": True}
        parent = mod_param("parent", bool, True, "Parent")
        child = mod_param("child", int, 1, "Child", ui_visible_if=condition)
        tail = mod_param("tail", bool, False, "Tail")
        controller = mod_controller(
            [parent, child, tail],
            {"parent": True, "child": 1, "tail": False})
        controller._render_mod_settings()

        controller.params.variables["parent"] = False
        controller._handle_mod_action("mod.item.2")

        self.assertEqual(controller.params.updated, [("tail", True)])

    def test_mod_ui_uses_only_public_setter_for_updates(self):
        flag = mod_param("camera", bool, False, "Camera")

        class GuardedVariables(dict):
            def __setitem__(self, key, value):
                raise AssertionError("UI mutated variables directly")

        class PublicManager:
            def __init__(manager):
                manager.params = [flag]
                manager.params_map = {flag.key: flag}
                manager.variables = GuardedVariables(camera=False)
                manager.calls = []

            def set_value(manager, key, value):
                manager.calls.append((key, value))
                dict.__setitem__(manager.variables, key, bool(int(value)))
                return int(manager.variables[key])

        controller = mod_controller([flag], {"camera": False})
        manager = PublicManager()
        controller.params = manager

        controller._handle_mod_action("mod.item.0")

        self.assertEqual(manager.calls, [("camera", "1")])

    def test_mod_settings_renders_raw_and_inverted_boolean_states(self):
        params = [
            mod_param("normal_false", bool, False, "Normal false"),
            mod_param("normal_true", bool, True, "Normal true"),
            mod_param("inverted_false", bool, False, "Inverted false",
                      ui_inverted=True),
            mod_param("inverted_true", bool, True, "Inverted true",
                      ui_inverted=True),
        ]
        controller = mod_controller(params, {
            "normal_false": False,
            "normal_true": True,
            "inverted_false": False,
            "inverted_true": True,
        })

        controller._render_mod_settings()

        states = [
            controller.renderer._toggles["mod.item.%d" % index][4]
            for index in range(4)
        ]
        self.assertEqual(states, [False, True, True, False])

    def test_mod_boolean_toggle_updates_without_opening_an_editor(self):
        flag = mod_param("camera", bool, False, "Alt camera")
        controller = mod_controller([flag], {"camera": False})

        controller._handle_mod_action("mod.item.0")

        self.assertEqual(controller.params.updated, [("camera", True)])
        self.assertIsNone(controller.mod_parameter)
        self.assertIn("mod.item.0", controller.renderer._toggles)
        self.assertTrue(controller.renderer._toggles["mod.item.0"][4])

    def test_inverted_mod_toggle_animates_display_but_saves_raw_value(self):
        flag = mod_param(
            "disable_priming", bool, False, "Nozzle priming",
            ui_inverted=True)
        controller = mod_controller([flag], {"disable_priming": False})
        controller._render_mod_settings()
        self.assertTrue(controller.renderer._toggles["mod.item.0"][4])

        controller._handle_mod_action("mod.item.0")

        self.assertEqual(controller.params.updated,
                         [("disable_priming", True)])
        self.assertFalse(controller.renderer._toggles["mod.item.0"][4])

        controller._handle_mod_action("mod.item.0")

        self.assertEqual(controller.params.updated, [
            ("disable_priming", True),
            ("disable_priming", False),
        ])
        self.assertTrue(controller.renderer._toggles["mod.item.0"][4])

    def test_theme_parameter_refreshes_users_once_and_uses_stable_snapshot(self):
        theme = mod_param("feather_theme", str, "DEFAULT",
                          "Feather color theme")
        controller = mod_controller([theme], {"feather_theme": "DEFAULT"})

        with mock.patch.object(
                controller.renderer, "reload_user_themes",
                wraps=controller.renderer.reload_user_themes) as refresh:
            controller._handle_mod_action("mod.item.0")
            options = tuple(controller.parameter_options)
            page_count = (len(options) + 3) // 4
            first = "\n".join(controller.draw_batches[-1])
            self.assertEqual(controller.page, FEATHER.ScreenPage.PARAMETER_OPTIONS)
            self.assertIn("1/%d" % page_count, first)
            self.assertEqual(refresh.call_count, 1)

            synth_index = options.index("SYNTH")
            for _ in range(synth_index // 4):
                controller._handle_mod_action("mod.options.next")
            controller._handle_mod_action("mod.option.%d" % synth_index)
            self.assertEqual(refresh.call_count, 1)
            self.assertEqual(tuple(controller.parameter_options), options)
            controller._handle_mod_action("mod.apply")

        self.assertEqual(controller.params.updated,
                         [("feather_theme", "SYNTH")])
        self.assertEqual(controller.renderer.theme_name, "SYNTH")
        expected_background = controller.renderer.color(UI.ThemeColor.BACKGROUND)
        drawing = "\n".join(controller.draw_batches[-1])
        self.assertIn("-c %s" % expected_background, drawing)

    def test_theme_picker_discovers_user_file_added_after_start(self):
        theme = mod_param("feather_theme", str, "DEFAULT",
                          "Feather color theme")
        controller = mod_controller([theme], {"feather_theme": "DEFAULT"})
        with tempfile.TemporaryDirectory() as user_directory:
            controller.renderer = FEATHER.FeatherRenderer(
                theme_directories=(UI.THEME_DIRECTORY, user_directory))
            controller.draw_batches = []
            controller.renderer.send = controller.draw_batches.append
            runtime_theme = {
                "schema_version": 2,
                "name": "RUNTIME_ADDED",
                "description": "Added without restarting Klipper",
                "colors": dict(UI.FALLBACK_THEME, primary="123abc"),
            }
            pathlib.Path(user_directory, "runtime.json").write_text(
                json.dumps(runtime_theme), encoding="utf-8")

            controller._handle_mod_action("mod.item.0")

            self.assertIn("RUNTIME_ADDED", controller.parameter_options)
            index = controller.parameter_options.index("RUNTIME_ADDED")
            controller._handle_mod_action("mod.option.%d" % index)
            controller._handle_mod_action("mod.apply")
            self.assertEqual(
                controller.params.updated,
                [("feather_theme", "RUNTIME_ADDED")])
            self.assertEqual(controller.renderer.theme_name, "RUNTIME_ADDED")

    def test_theme_picker_shows_invalid_user_file_as_disabled_row(self):
        theme = mod_param("feather_theme", str, "DEFAULT",
                          "Feather color theme")
        controller = mod_controller([theme], {"feather_theme": "DEFAULT"})
        with tempfile.TemporaryDirectory() as user_directory:
            controller.renderer = FEATHER.FeatherRenderer(
                theme_directories=(UI.THEME_DIRECTORY, user_directory))
            controller.draw_batches = []
            controller.renderer.send = controller.draw_batches.append
            invalid = {
                "schema_version": 2,
                "name": "BROKEN_USER",
                "description": "Invalid user theme",
                "colors": dict(UI.FALLBACK_THEME, primary="not-a-color"),
            }
            pathlib.Path(user_directory, "broken.json").write_text(
                json.dumps(invalid), encoding="utf-8")

            with self.assertLogs(level="WARNING"):
                controller._handle_mod_action("mod.item.0")

            entries = controller.parameter_option_entries
            issue_index = next(
                index for index, option in enumerate(entries)
                if not option.enabled and option.label == "BROKEN_USER")
            target_page = issue_index // 4
            while controller.parameter_options_page_index < target_page:
                controller._handle_mod_action("mod.options.next")

            drawing = "\n".join(controller.draw_batches[-1])
            issue_command = next(
                line for line in drawing.splitlines()
                if "BROKEN_USER // SCHEMA MISMATCH" in line)
            self.assertNotIn("--id ", issue_command)

            selected = controller.selected_parameter_option
            batch_count = len(controller.draw_batches)
            controller._handle_mod_action("mod.option.%d" % issue_index)
            self.assertEqual(controller.selected_parameter_option, selected)
            self.assertEqual(len(controller.draw_batches), batch_count)

    def test_settings_opens_theme_picker_and_returns_to_settings(self):
        theme = mod_param("feather_theme", str, "DEFAULT",
                          "Feather color theme")
        controller = mod_controller([theme], {"feather_theme": "DEFAULT"})
        controller.page = FEATHER.ScreenPage.SETTINGS

        controller._handle_settings_action("settings.theme")

        self.assertEqual(controller.page, FEATHER.ScreenPage.PARAMETER_OPTIONS)
        self.assertEqual(controller.mod_return_page, FEATHER.ScreenPage.SETTINGS)
        options = tuple(controller.parameter_options)
        dark_index = options.index("DARK")
        controller._handle_mod_action("mod.option.%d" % dark_index)
        controller._handle_mod_action("mod.apply")
        self.assertEqual(controller.page, FEATHER.ScreenPage.SETTINGS)

    def test_toggle_thumb_is_centered_and_animates_between_halves(self):
        renderer = FEATHER.FeatherRenderer()
        batches = []
        callbacks = []
        renderer.send = batches.append
        initial = "\n".join(renderer.toggle(
            "flag", 100, 50, 76, 38, False))
        self.assertIn("--batch fill -p 105 55 -s 28 28", initial)

        renderer.animate_toggle(
            "flag", True,
            lambda callback, delay: callbacks.append((delay, callback)))
        self.assertIn("--batch fill -p 114 55 -s 28 28",
                      "\n".join(batches[-1]))
        for delay, callback in callbacks:
            callback(100 + delay)
        self.assertIn("--batch fill -p 143 55 -s 28 28",
                      "\n".join(batches[-1]))

    def test_fast_mod_update_blocks_input_without_showing_modal(self):
        flag = mod_param("camera", bool, False, "Alt camera")
        controller = mod_controller([flag], {"camera": False})
        controller.reactor = DeferredReactor()
        controller._render_mod_settings()

        controller._handle_mod_action("mod.item.0")
        self.assertTrue(controller.mod_update_pending)
        self.assertIn("clear-hitboxes",
                      "\n".join(controller.draw_batches[-1]))

        controller.reactor.run_until(100.14)
        self.assertFalse(controller.mod_update_pending)
        controller.reactor.run_until(100.3)

    def test_slow_mod_update_keeps_modal_visible_for_minimum_time(self):
        flag = mod_param("camera", bool, False, "Alt camera")
        controller = mod_controller([flag], {"camera": False})
        reactor = DeferredReactor()
        controller.reactor = reactor
        completed = []

        class SlowManager(ModManager):
            def set_value(manager, key, value):
                result = super(SlowManager, manager).set_value(key, value)

                def notify(eventtime):
                    controller._show_mod_update_modal(
                        eventtime + 0.3, controller.mod_update_token)
                    reactor.now = eventtime + 0.4

                reactor.register_callback(notify)
                return result

        controller.params = SlowManager([flag], {"camera": False})
        controller._set_mod_value(flag, "1",
                                  lambda: completed.append(reactor.monotonic()))
        reactor.run_until(100.4)
        self.assertTrue(controller.mod_update_pending)

        reactor.run_until(100.524)
        self.assertTrue(controller.mod_update_pending)
        self.assertEqual(completed, [])
        reactor.run_until(100.525)
        self.assertFalse(controller.mod_update_pending)
        self.assertEqual(len(completed), 1)
        self.assertAlmostEqual(completed[0], 100.525)

    def test_restart_parameter_draws_loader_before_scheduled_change_hook(self):
        param = mod_param(
            "klipper_rt", bool, False, "Klipper real-time priority",
            restart="klipper")
        controller = mod_controller([param], {"klipper_rt": False})
        reactor = DeferredReactor()
        controller.reactor = reactor
        events = []

        class RestartingManager(ModManager):
            def set_value(manager, key, value):
                result = super(RestartingManager, manager).set_value(key, value)
                reactor.register_callback(
                    lambda eventtime: events.append("change-hook"))
                return result

        controller.params = RestartingManager(
            [param], {"klipper_rt": False})
        controller._begin_restart_ui = (
            lambda: events.append("restart-loader") or True)

        controller._set_mod_value(param, "1")

        self.assertEqual(events, ["restart-loader"])
        self.assertEqual(controller.params.updated, [("klipper_rt", True)])
        self.assertFalse(controller.mod_update_pending)
        reactor.run_until(100.0)
        self.assertEqual(events, ["restart-loader", "change-hook"])

    def test_unchanged_restart_parameter_does_not_show_loader(self):
        param = mod_param(
            "klipper_rt", bool, True, "Klipper real-time priority",
            restart="klipper")
        controller = mod_controller([param], {"klipper_rt": True})
        controller.reactor = DeferredReactor()
        loaders = []
        controller._begin_restart_ui = lambda: loaders.append(True) or True

        controller._set_mod_value(param, "1")
        controller.reactor.run_until(100.0)

        self.assertEqual(loaders, [])
        self.assertFalse(controller.mod_update_pending)


    def test_selected_parameter_option_is_staged_until_apply(self):
        Display = enum.Enum("Display", {"STOCK": 0, "FEATHER": 1,
                                         "HEADLESS": 2, "GUPPY": 3})
        param = mod_param("display", Display, 1, "Display",
                          "Choose the active local screen.",
                          {"STOCK": "Stock", "FEATHER": "Feather",
                           "HEADLESS": "Headless", "GUPPY": "Guppy"})
        controller = mod_controller([param], {"display": 1})

        controller._handle_mod_action("mod.item.0")
        self.assertEqual(controller.page, FEATHER.ScreenPage.PARAMETER_OPTIONS)
        self.assertEqual(controller.params.updated, [])
        controller._handle_mod_action("mod.option.3")
        controller._handle_mod_action("mod.apply")

        self.assertEqual(controller.params.updated, [("display", 3)])
        self.assertEqual(controller.page, FEATHER.ScreenPage.MOD_SETTINGS)

    def test_mod_numeric_editor_rejects_decimal_for_integer(self):
        param = mod_param("park_dz", int, 50, "Park offset")
        controller = mod_controller([param], {"park_dz": 50})
        controller._handle_mod_action("mod.item.0")
        self.assertEqual(controller.page, FEATHER.ScreenPage.MOD_VALUE)
        controller.mod_edit_value = ""
        controller._handle_mod_action("mod.key.7")
        controller._handle_mod_action("mod.dot")
        controller._handle_mod_action("mod.key.5")
        self.assertEqual(controller.mod_edit_value, "75")
        controller._handle_mod_action("mod.save")
        self.assertEqual(controller.params.updated, [("park_dz", 75)])

    def test_mod_numeric_editor_uses_shared_controls_and_constraints(self):
        param = mod_param("speed", float, 5.0, "Travel speed")
        param.minimum = 1.0
        param.maximum = 10.0
        param.fraction_digits = 1
        controller = mod_controller([param], {"speed": 5.0})

        controller._handle_mod_action("mod.item.0")
        drawing = "\n".join(controller.draw_batches[-1])
        self.assertIn("--id 1:mod.dot", drawing)
        self.assertNotIn("--id 1:mod.sign", drawing)
        self.assertNotIn("keyboard.left", controller.renderer._buttons)
        self.assertNotIn("keyboard.right", controller.renderer._buttons)
        controller.mod_edit_value = ""
        for action in ("mod.key.9", "mod.dot", "mod.key.5", "mod.key.9"):
            controller._handle_mod_action(action)
        self.assertEqual(controller.mod_edit_value, "9.5")
        controller.mod_edit_value = "11"
        with self.assertRaisesRegex(ValueError, "at most 10"):
            controller._handle_mod_action("mod.save")

    def test_mod_string_editor_uses_shared_text_keyboard(self):
        param = mod_param("midi_on", str, "", "Startup MIDI")
        controller = mod_controller([param], {"midi_on": ""})
        controller._handle_mod_action("mod.item.0")
        for action in (
                "keyboard.shift", "keyboard.key.a", "keyboard.space",
                "keyboard.symbols"):
            self.assertTrue(controller.handle_action(
                FEATHER.ScreenPage.MOD_VALUE, action))
        self.assertIn("keyboard.key.1", dict(controller.renderer._buttons))
        self.assertIn("keyboard.key.0", dict(controller.renderer._buttons))
        controller._handle_mod_action("keyboard.key.hash")
        controller._handle_mod_action("keyboard.left")
        controller._handle_mod_action("keyboard.key.dot")
        controller._handle_mod_action("keyboard.right")
        controller._handle_mod_action("keyboard.backspace")
        controller._handle_mod_action("mod.save")

        self.assertEqual(controller.params.updated, [("midi_on", "A .")])

    def test_wifi_and_mod_settings_use_identical_text_keyboard_geometry(self):
        param = mod_param("midi_on", str, "", "Startup MIDI")
        mod = mod_controller([param], {"midi_on": ""})
        mod._handle_mod_action("mod.item.0")
        mod_buttons = dict(mod.renderer._buttons)

        wifi = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        wifi.renderer = FEATHER.FeatherRenderer()
        wifi.renderer.send = lambda commands: None
        wifi.selected_network = {"ssid": "Workshop"}
        wifi.password = "secret123"
        wifi.password_cursor = len(wifi.password)
        wifi.password_visible = False
        wifi.keyboard_symbols = False
        wifi.keyboard_shift = False
        wifi._render_keyboard()
        wifi_buttons = dict(wifi.renderer._buttons)

        shared_actions = sorted(
            action for action in mod_buttons
            if action.startswith("keyboard."))
        self.assertTrue(shared_actions)
        self.assertEqual(
            shared_actions,
            sorted(action for action in wifi_buttons
                   if action.startswith("keyboard.")))
        for action in shared_actions:
            self.assertEqual(mod_buttons[action], wifi_buttons[action], action)
        left = mod_buttons["keyboard.left"]
        space = mod_buttons["keyboard.space"]
        right = mod_buttons["keyboard.right"]
        self.assertLessEqual(left[0] + left[2], space[0])
        self.assertLessEqual(space[0] + space[2], right[0])
        self.assertIn("net.password.toggle", wifi_buttons)
        self.assertIn("net.connect", wifi_buttons)

    def test_wifi_password_editor_inserts_at_moved_cursor(self):
        wifi = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        wifi.renderer = FEATHER.FeatherRenderer()
        wifi.renderer.send = lambda commands: None
        wifi.selected_network = {"ssid": "Workshop"}
        wifi.password = "pasword"
        wifi.password_cursor = len(wifi.password)
        wifi.password_visible = False
        wifi.keyboard_symbols = False
        wifi.keyboard_shift = False

        for _step in range(4):
            wifi._handle_network_action("keyboard.left")
        wifi._handle_network_action("keyboard.key.s")

        self.assertEqual(wifi.password, "password")
        self.assertEqual(wifi.password_cursor, 4)

    def test_mod_page_hitboxes_stay_above_persistent_footer(self):
        params = [mod_param("flag%d" % index, bool, False,
                            "Feature %d" % index)
                  for index in range(5)]
        controller = mod_controller(params, {param.key: False for param in params})
        controller._render_mod_settings()
        footer = (0, UI.FOOTER_Y, UI.SCREEN_WIDTH, UI.FOOTER_HEIGHT)
        for action, spec in controller.renderer._buttons.items():
            if action == "nav.back":
                continue
            self.assertFalse(UI.rectangles_overlap(spec[:4], footer), action)

    def test_eco_wake_restores_backlight_once(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.dimmed = True
        values = []
        controller._setting = lambda key, default: 55
        controller._set_backlight = values.append
        self.assertTrue(controller._wake_if_dimmed())
        self.assertFalse(controller._wake_if_dimmed())
        self.assertEqual(values, [55])

    def test_eco_setting_controls_idle_dimming_and_wakes_when_disabled(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.last_touch_time = 0.0
        controller.dim_timeout = 60.0
        controller.dimmed = False
        controller.params = type("Params", (), {"variables": {
            "backlight": 65, "backlight_eco": 10,
            "display_eco": False}})()
        values = []
        controller._set_backlight = values.append

        controller._update_eco_backlight(61.0)
        self.assertFalse(controller.dimmed)
        self.assertEqual(values, [])

        controller.params.variables["display_eco"] = True
        controller._update_eco_backlight(62.0)
        self.assertTrue(controller.dimmed)
        self.assertEqual(values, [10])

        controller.params.variables["display_eco"] = False
        controller._update_eco_backlight(63.0)
        self.assertFalse(controller.dimmed)
        self.assertEqual(values, [10, 65])

    def test_background_wake_action_has_no_page_side_effect(self):
        controller = ScenarioController.__new__(ScenarioController)

        controller._handle_touch_action("global.wake")

    def test_pending_print_action_rejects_repeat_tap(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.print_state = FEATHER.PrintState.PRINTING
        controller.reactor = Reactor()
        controller.last_action_time = -1
        controller.pending_action = "print.pause"
        controller.page = FEATHER.ScreenPage.PRINTING
        controller.debug = False
        calls = []
        controller._handle_print_action = calls.append
        controller._dispatch_action("print.pause")
        self.assertEqual(calls, [])

    def test_print_page_always_registers_cancel_action(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        controller.renderer.send = lambda commands: None
        controller.reactor = Reactor()
        controller.print_state = FEATHER.PrintState.PRINTING
        controller.pending_action = None
        controller.operation_context = None
        controller.virtual_sdcard = type("SD", (), {
            "file_path": lambda self: "/data/test.gcode"})()
        controller._live_z_adjust_allowed = lambda eventtime: False
        controller._update_print_progress = lambda eventtime: None
        controller.renderer.set_header_action("global.abort", "ABORT")

        controller._render_print_page()

        self.assertIn("global.abort", controller.renderer._buttons)
        self.assertIn("print.cancel", controller.renderer._buttons)
        self.assertEqual(
            controller.renderer._buttons["print.cancel"][5], "danger")
        self.assertNotIn("print.live_z", controller.renderer._buttons)
        self.assertFalse(UI.rectangles_overlap(
            controller.renderer._buttons["global.abort"][:4],
            controller.renderer._buttons["print.cancel"][:4]))

    def test_print_preparation_disables_pause_and_filament(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        controller.renderer.send = lambda commands: None
        controller.reactor = Reactor()
        controller.print_state = FEATHER.PrintState.PREPARING
        controller.pending_action = None
        controller.operation_context = None
        controller.virtual_sdcard = type("SD", (), {
            "file_path": lambda self: "/data/test.gcode"})()
        controller.print_flow = type("Flow", (), {"variables": {
            "active": False, "phase": "PREPARING"}})()
        controller.start_print_macro = type("Start", (), {"variables": {
            "print_started": False}})()
        controller._live_z_adjust_allowed = lambda eventtime: False
        controller._update_print_progress = lambda eventtime: None
        controller.renderer.set_header_action("global.abort", "ABORT")

        controller._render_print_page()

        self.assertNotIn("print.pause", controller.renderer._buttons)
        self.assertNotIn("print.filament", controller.renderer._buttons)
        self.assertIn("print.cancel", controller.renderer._buttons)
        self.assertIn("global.abort", controller.renderer._buttons)

    def test_print_progress_prefers_live_height_and_falls_back(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.page = FEATHER.ScreenPage.PRINTING
        controller._last_progress = None
        controller._progress_floor = 0.0
        controller._last_time = None
        controller.print_stats = StatusObject({
            "state": "printing", "print_duration": 100,
            "info": {"current_layer": None, "total_layer": None},
        })
        controller.virtual_sdcard = StatusObject({"progress": 0.25})
        controller.virtual_sdcard.estimate_print_time = 400.0
        controller.toolhead = StatusObject({
            "position": (10.0, 20.0, 9.75, 0.0), "homed_axes": "xyz"})
        controller.motion_report = StatusObject({
            "live_position": (10.0, 20.0, 3.25, 0.0)})

        controller._update_print_progress(100)

        drawing = "\n".join(batches[0])
        self.assertIn("00:01:40", drawing)
        self.assertIn("00:05:00", drawing)
        self.assertIn('? / ?', drawing)
        self.assertIn("3.25 MM", drawing)

        controller.motion_report = None
        controller.toolhead.status["position"] = (10.0, 20.0, 4.5, 0.0)

        controller._update_print_progress(101)

        self.assertIn("4.50 MM", "\n".join(batches[1]))

    def test_print_progress_uses_sd_position_and_never_moves_backwards(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller._progress_floor = 0.0
        controller._m73_start_expiry = 0.0
        controller._m73_active = False
        controller.display_status = type("Display", (), {
            "progress": None, "expire_progress": 0.0})()
        controller.print_stats = StatusObject({"print_duration": 0.0})
        sdcard = StatusObject({"progress": 0.12})
        sdcard.estimate_print_time = None
        controller.virtual_sdcard = sdcard

        self.assertEqual(controller._print_progress(1.0), 0.12)
        sdcard.status["progress"] = 0.09
        self.assertEqual(controller._print_progress(2.0), 0.12)
        sdcard.status["progress"] = 0.15
        self.assertEqual(controller._print_progress(3.0), 0.15)

    def test_print_progress_prefers_current_print_m73(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller._progress_floor = 0.0
        controller._m73_start_expiry = 10.0
        controller._m73_active = False
        controller.display_status = type("Display", (), {
            "progress": 0.37, "expire_progress": 25.0})()
        controller.print_stats = StatusObject({"print_duration": 50.0})
        controller.virtual_sdcard = StatusObject({
            "progress": 0.80, "estimate_print_time": 100.0})
        controller.virtual_sdcard.estimate_print_time = 100.0

        self.assertEqual(controller._print_progress(20.0), 0.37)
        self.assertEqual(controller._progress_source, "M73")
        controller.display_status.progress = 0.29
        controller.display_status.expire_progress = 30.0
        self.assertEqual(controller._print_progress(25.0), 0.37)

    def test_print_progress_uses_time_estimate_before_sd_fallback(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller._progress_floor = 0.0
        controller._m73_start_expiry = 10.0
        controller._m73_active = False
        controller.display_status = type("Display", (), {
            "progress": 0.75, "expire_progress": 10.0})()
        controller.print_stats = StatusObject({"print_duration": 25.0})
        controller.virtual_sdcard = StatusObject({
            "progress": 0.80, "estimate_print_time": 100.0})
        controller.virtual_sdcard.estimate_print_time = 100.0

        self.assertEqual(controller._print_progress(20.0), 0.25)
        self.assertEqual(controller._progress_source, "TIME")

    def test_print_progress_excludes_start_print_time(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.print_state = FEATHER.PrintState.PRINTING
        controller._progress_floor = 0.52
        controller._progress_start = None
        controller._m73_start_expiry = 0.0
        controller._m73_active = False
        controller.display_status = type("Display", (), {
            "progress": None, "expire_progress": 0.0})()
        controller.start_print_macro = type("Start", (), {"variables": {
            "print_started": False}})()
        controller.print_stats = StatusObject({"print_duration": 52.0})
        controller.virtual_sdcard = StatusObject({
            "progress": 0.40, "estimate_print_time": 100.0})
        controller.virtual_sdcard.estimate_print_time = 100.0

        self.assertEqual(controller._print_progress(1.0), 0.0)
        self.assertEqual(controller._progress_floor, 0.0)
        controller.start_print_macro.variables["print_started"] = True
        self.assertEqual(controller._print_progress(2.0), 0.0)
        controller.print_stats.status["print_duration"] = 77.0
        self.assertEqual(controller._print_progress(3.0), 0.25)

    def test_print_progress_rebases_sd_after_start_print(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.print_state = FEATHER.PrintState.PRINTING
        controller._progress_floor = 0.0
        controller._progress_start = None
        controller._m73_start_expiry = 0.0
        controller._m73_active = False
        controller.display_status = type("Display", (), {
            "progress": None, "expire_progress": 0.0})()
        controller.start_print_macro = type("Start", (), {"variables": {
            "print_started": False}})()
        controller.print_stats = StatusObject({"print_duration": 10.0})
        controller.virtual_sdcard = StatusObject({"progress": 0.52})
        controller.virtual_sdcard.estimate_print_time = None

        self.assertEqual(controller._print_progress(1.0), 0.0)
        controller.start_print_macro.variables["print_started"] = True
        self.assertEqual(controller._print_progress(2.0), 0.0)
        controller.virtual_sdcard.status["progress"] = 0.76
        self.assertAlmostEqual(controller._print_progress(3.0), 0.5)

    def test_filament_continue_is_next_to_action_buttons(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.reactor = Reactor()
        controller.filament_material = "PLA"
        controller.filament_from_pause = True
        controller.extruder = StatusObject({
            "temperature": 220.0, "target": 220.0})
        controller.extruder.min_extrude_temp = 170.0

        FilamentFeature(controller).render(FEATHER.ScreenPage.FILAMENT_ACTION)

        drawing = "\n".join(batches[0])
        for action, y in ((FILAMENT_ACTIONS.LOAD, 72),
                          (FILAMENT_ACTIONS.UNLOAD, 164),
                          (FILAMENT_ACTIONS.PURGE, 256)):
            self.assertIn("-p 320 %d -s 460 76" % y, drawing)
            self.assertIn("--id 1:%s" % action.wire_id, drawing)
        self.assertIn("--id 1:%s" % FILAMENT_ACTIONS.RESUME.wire_id, drawing)

    def test_filament_actions_enable_only_at_selected_target_temperature(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.reactor = Reactor()
        controller.filament_material = "PETG"
        controller.filament_from_pause = False
        controller.extruder = StatusObject({
            "temperature": 200.0, "target": 250.0})
        controller.extruder.min_extrude_temp = 170.0

        feature = FilamentFeature(controller)
        feature.render(FEATHER.ScreenPage.FILAMENT_ACTION)

        drawing = "\n".join(batches[0])
        for action in (FILAMENT_ACTIONS.LOAD, FILAMENT_ACTIONS.UNLOAD,
                       FILAMENT_ACTIONS.PURGE):
            self.assertNotIn("--id 1:%s" % action.wire_id, drawing)

        controller.extruder.status["temperature"] = 248.0
        batches.clear()
        feature.render(FEATHER.ScreenPage.FILAMENT_ACTION)
        drawing = "\n".join(batches[0])
        for action in (FILAMENT_ACTIONS.LOAD, FILAMENT_ACTIONS.UNLOAD,
                       FILAMENT_ACTIONS.PURGE):
            self.assertIn("--id 2:%s" % action.wire_id, drawing)

        controller.extruder.status["temperature"] = 260.0
        batches.clear()
        feature.render(FEATHER.ScreenPage.FILAMENT_ACTION)
        drawing = "\n".join(batches[0])
        for action in (FILAMENT_ACTIONS.LOAD, FILAMENT_ACTIONS.UNLOAD,
                       FILAMENT_ACTIONS.PURGE):
            self.assertNotIn("--id 3:%s" % action.wire_id, drawing)

    def test_terminal_print_state_becomes_idle_and_reports_result(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.print_state = FEATHER.PrintState.PAUSED
        controller.pending_action = "print.cancel.confirm"
        controller.reactor = Reactor()
        controller.debug = False
        messages = []
        controller._show_message = lambda message, page: messages.append((message, page))
        controller._change_print_state(FEATHER.PrintState.IDLE, "cancelled")
        self.assertEqual(controller.print_state, FEATHER.PrintState.IDLE)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0][1], FEATHER.ScreenPage.IDLE_HOME)

    def test_preheat_presets_respect_real_heater_limits(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.extruder = type("Extruder", (), {
            "heater": type("Heater", (), {"min_temp": 0, "max_temp": 251})()})()
        controller.heater_bed = type("Bed", (), {"min_temp": 0, "max_temp": 91})()
        self.assertEqual(controller._limited_preheat("ABS"), (250, 85))

    def test_filament_extrusion_is_blocked_when_cold(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.reactor = Reactor()
        controller.filament_from_pause = True
        controller.print_stats = StatusObject({"state": "paused"})
        controller.extruder = StatusObject({
            "temperature": 160, "target": 220})
        controller.extruder.min_extrude_temp = 170
        controller.gcode = GCodeRecorder()
        controller._require_idle = lambda: None
        with self.assertRaisesRegex(RuntimeError, "has not reached the target"):
            controller._handle_filament_action("filament.load")
        controller.extruder.status["temperature"] = 180
        with self.assertRaisesRegex(RuntimeError, "has not reached the target"):
            controller._handle_filament_action("filament.purge")
        controller.extruder.status["temperature"] = 218
        controller._toast = lambda message: None
        controller._handle_filament_action("filament.purge")
        self.assertEqual(controller.gcode.commands, ["PURGE_FILAMENT"])

    def test_idle_filament_flow_restores_original_target(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.filament_from_pause = False
        controller.filament_original_target = 185
        controller.gcode = GCodeRecorder()
        pages = []
        controller._show_page = pages.append
        controller._finish_filament(False)
        self.assertEqual(controller.gcode.commands, ["M104 S185"])
        self.assertEqual(pages, [FEATHER.ScreenPage.IDLE_HOME])

    def test_live_z_adjust_is_available_on_every_layer_when_z_is_homed(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.print_state = FEATHER.PrintState.PRINTING
        controller.print_stats = StatusObject(
            {"state": "printing", "info": {"current_layer": None}})
        controller.toolhead = StatusObject({"homed_axes": "xyz"})
        self.assertTrue(controller._live_z_adjust_allowed(0))
        controller.print_stats.status["info"]["current_layer"] = 1
        self.assertTrue(controller._live_z_adjust_allowed(0))
        controller.print_stats.status["info"]["current_layer"] = 2
        self.assertTrue(controller._live_z_adjust_allowed(0))
        controller.toolhead.status["homed_axes"] = "xy"
        self.assertFalse(controller._live_z_adjust_allowed(0))
        controller.toolhead.status["homed_axes"] = "xyz"
        controller.print_state = FEATHER.PrintState.PREPARING
        self.assertFalse(controller._live_z_adjust_allowed(0))

    def test_mesh_uses_auto_profile_and_selected_preheat(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.calibration_kind = "mesh"
        controller.calibration_material = "PETG"
        controller.calibration_error = None
        controller.gcode = GCodeRecorder()
        controller._require_idle = lambda: None
        controller._limited_preheat = lambda material: (245, 68)
        pages = []
        controller._show_page = pages.append
        controller._run_calibration(0)
        self.assertEqual(controller.gcode.commands,
                         ["AUTO_FULL_BED_LEVEL EXTRUDER_TEMP=245 BED_TEMP=68 PROFILE=auto"])
        self.assertEqual(pages, [FEATHER.ScreenPage.CALIBRATION_RESULT])

    def test_screw_calibration_passes_selected_cleaning_path(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.calibration_kind = "screws"
        controller.calibration_material = "PETG"
        controller.calibration_clean_nozzle = False
        controller.calibration_repeat_probe = False
        controller.calibration_error = None
        controller.gcode = GCodeRecorder()
        controller._require_idle = lambda: None
        controller._limited_preheat = lambda material: (245, 68)
        pages = []
        controller._show_page = pages.append
        controller._run_calibration(0)
        self.assertEqual(controller.gcode.commands, [
            "BED_LEVEL_SCREWS_TUNE CLEAN=0"])
        self.assertEqual(pages, [FEATHER.ScreenPage.CALIBRATION_RESULT])

    def test_cancelled_calibration_result_does_not_offer_unsafe_repeat(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        controller.calibration_kind = "screws"
        controller.calibration_error = None
        controller.calibration_cancelled = True
        batches = []
        controller.renderer.send = batches.append

        controller._render_calibration_result()

        drawing = "\n".join(batches[-1])
        self.assertIn("cal.done", drawing)
        self.assertNotIn("cal.repeat", drawing)

    def test_mesh_result_offers_repeat_discard_and_save(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        controller.calibration_kind = "mesh"
        controller.calibration_mesh = [[-0.1, 0.0], [0.05, 0.1]]
        controller.calibration_error = None
        controller.calibration_cancelled = False
        batches = []
        controller.renderer.send = batches.append

        controller._render_calibration_result()

        drawing = "\n".join(batches[-1])
        self.assertIn("cal.repeat", drawing)
        self.assertIn("cal.mesh.discard", drawing)
        self.assertIn("cal.mesh.save", drawing)
        self.assertNotIn("cal.done", drawing)

    def test_mesh_result_save_starts_restart_ui_with_save_config(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.calibration_kind = "mesh"
        controller.calibration_mesh = [[0.0]]
        controller.calibration_error = None
        controller.calibration_cancelled = False
        restarts = []
        controller._restart_klipper = restarts.append

        controller._handle_calibration_action("cal.mesh.save")

        self.assertEqual(restarts, ["SAVE_CONFIG"])

    def test_mesh_result_discard_keeps_previous_done_behavior(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.calibration_kind = "mesh"
        controller.calibration_mesh = [[0.0]]
        controller.calibration_error = None
        controller.calibration_cancelled = False
        pages = []
        controller._show_page = pages.append

        controller._handle_calibration_action("cal.mesh.discard")

        self.assertEqual(pages, [FEATHER.ScreenPage.CALIBRATION_HOME])

    def test_mesh_save_actions_are_ignored_without_valid_result(self):
        for error, cancelled, mesh in (
                ("failed", False, [[0.0]]),
                (None, True, [[0.0]]),
                (None, False, [])):
            with self.subTest(error=error, cancelled=cancelled, mesh=mesh):
                controller = ScenarioController.__new__(ScenarioController)
                controller.calibration_kind = "mesh"
                controller.calibration_mesh = mesh
                controller.calibration_error = error
                controller.calibration_cancelled = cancelled
                controller.gcode = GCodeRecorder()
                pages = []
                controller._show_page = pages.append

                controller._handle_calibration_action("cal.mesh.save")
                controller._handle_calibration_action("cal.mesh.discard")

                self.assertEqual(controller.gcode.commands, [])
                self.assertEqual(pages, [])

    def test_cancelled_calibration_heating_is_stopped_by_screen_code(self):
        class CancelThenRecord:
            def __init__(self):
                self.commands = []

            def run_script_from_command(self, command):
                self.commands.append(command)
                if len(self.commands) == 1:
                    raise RuntimeError("Aborted")

        cases = (
            ("screws", False, "BED_LEVEL_SCREWS_TUNE CLEAN=0", "M104 S0"),
            ("screws", True,
             "BED_LEVEL_SCREWS_TUNE EXTRUDER_TEMP=245 BED_TEMP=68 CLEAN=1",
             "TURN_OFF_HEATERS"),
            ("mesh", True,
             "AUTO_FULL_BED_LEVEL EXTRUDER_TEMP=245 BED_TEMP=68 PROFILE=auto",
             "TURN_OFF_HEATERS"),
        )
        for kind, clean, start_command, cleanup_command in cases:
            with self.subTest(kind=kind, clean=clean):
                controller = ScenarioController.__new__(ScenarioController)
                controller.calibration_kind = kind
                controller.calibration_material = "PETG"
                controller.calibration_clean_nozzle = clean
                controller.calibration_repeat_probe = False
                controller.calibration_cancel_requested = True
                controller.calibration_cancel_dispatched = True
                controller.calibration_cancelled = False
                controller.calibration_error = None
                controller.gcode = CancelThenRecord()
                controller._require_idle = lambda: None
                controller._limited_preheat = lambda material: (245, 68)
                pages = []
                controller._show_page = pages.append

                controller._run_calibration(0)

                self.assertEqual(
                    controller.gcode.commands,
                    [start_command, cleanup_command])
                self.assertTrue(controller.calibration_cancelled)
                self.assertIsNone(controller.calibration_error)
                self.assertEqual(
                    pages, [FEATHER.ScreenPage.CALIBRATION_RESULT])

    def test_screw_repeat_starts_probe_immediately_without_confirm(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.calibration_kind = "screws"
        controller.calibration_results = [{"name": "old"}]
        controller.calibration_mesh = []
        controller.calibration_error = None
        controller.reactor = DeferredReactor()
        controller.gcode = GCodeRecorder()
        controller._require_idle = lambda: None
        pages = []
        controller._show_page = pages.append
        controller._handle_calibration_action("cal.repeat")
        self.assertEqual(pages, [FEATHER.ScreenPage.CALIBRATION_PROGRESS])
        self.assertTrue(controller.calibration_repeat_probe)
        self.assertEqual(controller.calibration_results, [])
        self.assertEqual(len(controller.reactor.callbacks), 1)
        self.assertEqual(
            controller.gcode.commands, ["_CANCEL_DELAYED_COMMANDS"])

        controller.gcode.commands[:] = []
        controller._show_page = pages.append
        controller._run_calibration(0)
        self.assertEqual(controller.gcode.commands, ["BED_LEVEL_SCREWS_PROBE"])

    def test_calibration_error_returns_result_page(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.calibration_kind = "mesh"
        controller.calibration_material = "PLA"
        controller.calibration_error = None
        controller.gcode = FailingGCode()
        controller._require_idle = lambda: None
        controller._limited_preheat = lambda material: (220, 60)
        pages = []
        controller._show_page = pages.append
        with self.assertLogs(level="ERROR"):
            controller._run_calibration(0)
        self.assertEqual(controller.calibration_error, "macro failed")
        self.assertEqual(pages, [FEATHER.ScreenPage.CALIBRATION_RESULT])

    def test_calibration_shutdown_preserves_firmware_restart_screen(self):
        class ShutdownGCode:
            def run_script_from_command(self, command):
                raise RuntimeError("opaque command failure")

        controller = ScenarioController.__new__(ScenarioController)
        controller.calibration_kind = "mesh"
        controller.calibration_material = "PLA"
        controller.calibration_error = None
        controller.calibration_cancel_requested = False
        controller.gcode = ShutdownGCode()
        controller.renderer = FEATHER.FeatherRenderer()
        controller.renderer.freeze_output()
        controller.page = FEATHER.ScreenPage.ERROR
        controller.error_recovery = None
        controller._require_idle = lambda: None
        controller._limited_preheat = lambda material: (220, 60)
        rendered = []
        controller._render_calibration_result = lambda: rendered.append(True)

        with self.assertLogs(level="ERROR"):
            controller._run_calibration(0)

        self.assertEqual(controller.calibration_error, "opaque command failure")
        self.assertEqual(controller.page, FEATHER.ScreenPage.ERROR)
        self.assertEqual(rendered, [])

    def test_workflow_pages_cannot_replace_firmware_restart_screen(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.page = FEATHER.ScreenPage.ERROR
        controller.renderer = FEATHER.FeatherRenderer()
        controller.renderer.freeze_output()
        controller.error_recovery = "firmware_restart"
        controller._stop_joystick = lambda: None
        rendered = []
        controller._render_calibration_result = lambda: rendered.append(True)

        controller._show_page(FEATHER.ScreenPage.CALIBRATION_RESULT)

        self.assertEqual(controller.page, FEATHER.ScreenPage.ERROR)
        self.assertEqual(rendered, [])

    def test_frozen_shutdown_screen_ignores_late_action_error_page(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.page = FEATHER.ScreenPage.ERROR
        controller.renderer = FEATHER.FeatherRenderer()
        controller.renderer.freeze_output()
        controller.print_state = FEATHER.PrintState.IDLE
        controller.last_action_time = 0
        controller.pending_action = None
        controller.reactor = Reactor(now=100)
        controller.error_recovery = None
        rendered = []
        controller._render_message = lambda: rendered.append(True)

        controller._show_message(
            "opaque command failure", FEATHER.ScreenPage.CONTROL_HOME)

        self.assertEqual(controller.page, FEATHER.ScreenPage.ERROR)
        self.assertEqual(rendered, [])

    def test_frozen_shutdown_screen_preserves_recovery_hitbox_generation(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.page = FEATHER.ScreenPage.ERROR
        controller.renderer = FEATHER.FeatherRenderer()
        controller.renderer._generation = 9
        controller.renderer.freeze_output()
        controller.error_message = "Shutdown due to M112 command"
        controller.error_category = "shutdown"
        controller.error_recovery = "firmware_restart"

        controller._show_message(
            "Shutdown due to M112 command; use FIRMWARE_RESTART",
            FEATHER.ScreenPage.ERROR)

        self.assertEqual(controller.renderer.generation, 9)
        self.assertEqual(controller.error_message,
                         "Shutdown due to M112 command")
        self.assertEqual(controller.error_category, "shutdown")
        self.assertEqual(controller.error_recovery, "firmware_restart")

    def test_error_classification_honors_firmware_restart_state_message(self):
        classify = FEATHER.FeatherScreen._classify_error
        self.assertIsNone(classify("MCU 'mcu' shutdown: Timer too close"))
        self.assertIsNone(classify("Lost communication with MCU 'mcu'"))
        self.assertEqual(
            classify("ADC out of range", "shutdown"),
            "firmware_restart")
        self.assertEqual(
            classify(
                "MCU 'mcu' shutdown: Timer too close\n"
                "Once the underlying issue is corrected, use the "
                "\"FIRMWARE_RESTART\" command.",
                "error"),
            "firmware_restart")
        self.assertEqual(
            classify("Option 'foo' is not valid", "error"),
            "restart")
        self.assertIsNone(classify("Klipper disconnected", "disconnect"))
        self.assertIsNone(classify("Home X before moving"))

    def test_shutdown_event_owns_firmware_restart_screen(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.shutdown_active = False
        events = []
        controller.renderer = type("Renderer", (), {
            "active": True,
            "discard_pending_output":
                lambda self: events.append("discard"),
            "thaw_output": lambda self: events.append("thaw"),
            "freeze_output": lambda self: events.append("freeze"),
        })()
        controller.printer = type("Printer", (), {
            "get_state_message": lambda self: (
                "Shutdown due to M112 command\nPrinter is shutdown",
                "shutdown"),
        })()
        controller._deactivate_components = lambda: events.append("stop")
        controller._show_error = (
            lambda message, category, recovery=None:
            events.append((message, category, recovery)))

        controller._shutdown()

        self.assertTrue(controller.shutdown_active)
        self.assertEqual(events, [
            "stop", "discard", "thaw",
            ("Shutdown due to M112 command\nPrinter is shutdown",
             "shutdown", "firmware_restart"),
            "freeze",
        ])

    def test_error_page_offers_firmware_restart_recovery(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.error_message = "MCU 'mcu' shutdown: Timer too close"
        controller.error_recovery = "firmware_restart"

        controller._render_error()

        drawing = "\n".join(batches[0])
        self.assertIn(controller.error_message, drawing)
        self.assertIn("error.firmware_restart", controller.renderer._buttons)
        self.assertEqual(
            controller.renderer._buttons["error.firmware_restart"][5],
            "danger")

    def test_touch_warning_covers_and_restores_frozen_restart_dialog(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.page = FEATHER.ScreenPage.ERROR
        controller.error_message = "MCU shutdown"
        controller.error_recovery = "firmware_restart"
        controller.touch_available = True
        controller.touch_warning_visible = False
        controller.renderer.footer(
            "NOZZLE 21/220C | BED 24/60C", "192.168.2.4 | IDLE")
        controller._render_error()
        controller.renderer.freeze_output()
        controller._show_page = lambda page: controller._render_error()

        controller._handle_touch_device_status(False)

        self.assertFalse(controller.touch_available)
        self.assertTrue(controller.touch_warning_visible)
        self.assertTrue(controller.renderer.output_frozen)
        self.assertIn("TOUCH INPUT UNAVAILABLE", "\n".join(batches[-1]))

        controller._handle_touch_device_status(True)

        self.assertTrue(controller.touch_available)
        self.assertFalse(controller.touch_warning_visible)
        self.assertTrue(controller.renderer.output_frozen)
        self.assertIn("MCU shutdown", "\n".join(batches[-1]))
        self.assertIn("192.168.2.4 | IDLE", "\n".join(batches[-1]))
        self.assertIn(
            "error.firmware_restart", controller.renderer._buttons)

    def test_touch_warning_waits_until_startup_loader_is_replaced(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.touch_available = True
        controller.touch_warning_visible = False
        controller.renderer.startup_modal(
            "INITIALIZING KLIPPER", "INITIALIZING PRINTER SERVICES")

        controller._handle_touch_device_status(False)

        self.assertFalse(controller.touch_available)
        self.assertFalse(controller.touch_warning_visible)
        self.assertEqual(len(batches), 1)

        commands = controller.renderer.begin_page("Ready")
        commands += controller.renderer.button(
            "ready.confirm", 220, 300, 360, 100, "CONTINUE")
        controller.renderer.send(commands)
        controller._show_touch_unavailable()

        self.assertTrue(controller.touch_warning_visible)
        self.assertIn("TOUCH INPUT UNAVAILABLE", "\n".join(batches[-1]))

    def test_system_shutdown_surface_owns_late_teardown_events(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.shutdown_active = False
        controller.system_shutdown_active = False
        controller.page = FEATHER.ScreenPage.IDLE_HOME
        controller.touch_available = True
        controller.touch_warning_visible = False

        controller._handle_gcode_output(
            "// action:forge_x_shutting_down")

        self.assertTrue(controller.system_shutdown_active)
        self.assertTrue(controller.renderer.output_frozen)
        shutdown = "\n".join(batches[-1])
        self.assertIn("FORGE-X", shutdown)
        self.assertIn("SHUTTING DOWN", shutdown)
        batch_count = len(batches)

        controller._handle_touch_device_status(False)
        controller._shutdown()
        controller._disconnect()

        self.assertFalse(controller.touch_available)
        self.assertFalse(controller.touch_warning_visible)
        self.assertEqual(len(batches), batch_count)
        self.assertTrue(controller.renderer.output_frozen)

        controller._handle_gcode_output("// action:forge_x_redraw")

        self.assertEqual(len(batches), batch_count)

    def test_root_service_redraws_the_current_page(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.page = FEATHER.ScreenPage.CONTROL_HEAT
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.renderer.footer(
            "NOZZLE 21/220C | BED 24/60C", "192.168.2.4 | IDLE")
        controller.renderer.begin_page("Control heat")
        batches.clear()

        def show_page(page):
            self.assertEqual(page, FEATHER.ScreenPage.CONTROL_HEAT)
            controller.renderer.send(
                controller.renderer.begin_page("Control heat"))

        controller._show_page = show_page

        controller._handle_gcode_output("// action:forge_x_redraw")

        drawing = "\n".join(batches[-1])
        footer_clear = controller.renderer.fill(
            0, UI.FOOTER_Y - 2, UI.SCREEN_WIDTH,
            UI.SCREEN_HEIGHT - (UI.FOOTER_Y - 2),
            UI.ThemeColor.BACKGROUND)
        self.assertIn(footer_clear, drawing)
        self.assertIn("192.168.2.4 | IDLE", drawing)

    def test_touch_device_protocol_dispatches_availability_transitions(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = type("Renderer", (), {"event_fd": 7})()
        controller.event_partial = ""
        transitions = []
        controller._handle_touch_device_status = transitions.append

        with mock.patch.object(
                FEATHER.os, "read",
                return_value=(b"touch-device unavailable\n"
                              b"touch-device connected\n")):
            controller._process_touch_events(100.0)

        self.assertEqual(transitions, [False, True])

    def test_shutdown_message_is_wrapped_by_typer_inside_dialog(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.error_message = (
            "Shutdown caused by a toolhead communication timeout while the "
            "printer was waiting for the motion queue to finish safely")
        controller.error_recovery = "firmware_restart"

        controller._render_error()

        command = next(
            line for line in batches[0]
            if controller.error_message in line)
        self.assertIn("--wrap", command)
        self.assertIn("--truncate", command)
        width = re.search(r"--max-width ([0-9]+)", command)
        height = re.search(r"--max-height ([0-9]+)", command)
        self.assertIsNotNone(width)
        self.assertIsNotNone(height)
        self.assertGreater(int(width.group(1)), 0)
        self.assertGreater(int(height.group(1)), 0)
        self.assertEqual(int(height.group(1)), 110)
        self.assertLessEqual(int(width.group(1)), UI.SCREEN_WIDTH)
        self.assertLessEqual(int(height.group(1)), UI.SCREEN_HEIGHT)
        self.assertIn("-p 400 200", command)
        self.assertNotIn("communication time...", command)

    def test_recovery_confirmation_is_wrapped_by_typer(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.recovery_action = "cleanup"

        controller._render_recovery_confirm()

        command = next(
            line for line in batches[0] if "Cleanup will heat" in line)
        self.assertIn("--wrap", command)
        self.assertIn("--truncate", command)
        self.assertRegex(command, r"--max-width [1-9][0-9]*")
        self.assertRegex(command, r"--max-height [1-9][0-9]*")

    def test_action_prompt_renders_groups_footer_and_pagination(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.action_prompt_page = 0

        def button(index, label):
            return {
                "action": "prompt.button.%d" % index,
                "label": label,
                "command": label,
                "state": "enabled",
            }

        controller.action_prompt = {
            "title": "Material menu",
            "text": ["Select a profile"],
            "rows": [
                [button(0, "PLA"), button(1, "PETG")],
                [button(2, "ABS")],
                [button(3, "ASA")],
                [button(4, "PA")],
            ],
            "footer": [button(5, "CANCEL")],
        }

        controller._render_action_prompt()

        drawing = "\n".join(batches[0])
        self.assertIn("Material menu", drawing)
        self.assertIn("Select a profile", drawing)
        self.assertIn('prompt.button.0', drawing)
        self.assertIn('prompt.button.5', drawing)
        self.assertIn('prompt.next', drawing)
        self.assertNotIn('prompt.button.4', drawing)

    def test_firmware_restart_action_switches_to_animated_startup(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.error_message = "shutdown"
        controller.error_category = "shutdown"
        controller.error_recovery = "firmware_restart"
        controller.shutdown_active = True
        controller.restart_pending = False
        controller.renderer = type("Renderer", (), {
            "thaw_output": lambda self: None,
        })()
        controller.startup_phase = 3
        controller.startup_timer = None
        controller.timer = None
        started = []
        controller._start_pre_ready_ui = (
            lambda restarting=False: started.append(restarting))
        controller.gcode = GCodeRecorder()
        controller.reactor = Reactor()
        controller.command_depth = 0

        controller._handle_error_action("error.firmware_restart")

        self.assertEqual(started, [True])
        self.assertEqual(controller.gcode.commands, ["FIRMWARE_RESTART"])
        self.assertEqual(controller.startup_phase, 0)
        self.assertEqual(controller.error_message, "")
        self.assertFalse(controller.shutdown_active)
        self.assertTrue(controller.restart_pending)

    def test_restart_ui_is_drawn_before_restart_command(self):
        events = []

        class RestartGCode:
            def run_script_from_command(self, command):
                events.append(("command", command))

        controller = ScenarioController.__new__(ScenarioController)
        controller.error_message = ""
        controller.error_category = ""
        controller.error_recovery = None
        controller.shutdown_active = False
        controller.restart_pending = False
        controller.renderer = type("Renderer", (), {
            "thaw_output": lambda self: events.append(("thaw", None)),
        })()
        controller.startup_phase = 3
        controller.startup_timer = None
        controller.timer = None
        controller._start_pre_ready_ui = (
            lambda restarting=False: events.append(("startup", restarting)))
        controller.gcode = RestartGCode()
        controller.reactor = Reactor()
        controller.command_depth = 0

        controller._restart_klipper("SAVE_CONFIG")

        self.assertEqual(events, [
            ("thaw", None),
            ("startup", True),
            ("command", "SAVE_CONFIG"),
        ])
        self.assertTrue(controller.restart_pending)
        self.assertEqual(controller.startup_phase, 0)

        controller._restart_klipper("SAVE_CONFIG")
        self.assertEqual(len(events), 3)

    def test_mesh_calibration_shows_homing_immediately_after_prep(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = FEATHER.FeatherRenderer()
        controller.calibration_kind = "mesh"

        drawing = "\n".join(controller._calibration_stage_commands(
            "BED MESH: PREPARING"))
        labels = re.findall(r'-t "([^"]+)"', drawing)

        self.assertEqual(
            [label for label in labels if label in (
                "PREP", "HOME", "HEAT", "CLEAN", "LEVEL")],
            ["PREP", "HOME", "HEAT", "CLEAN", "LEVEL"])
        self.assertNotIn("ACTIVE", labels)
        self.assertNotIn("PENDING", labels)

    def test_persisted_theme_is_selected_before_first_renderer_output(self):
        with tempfile.TemporaryDirectory() as directory:
            theme_directory = pathlib.Path(directory)
            schema = pathlib.Path(UI.THEME_SCHEMA_PATH).read_text(
                encoding="utf-8")
            (theme_directory / "theme.schema.json").write_text(
                schema, encoding="utf-8")
            colors = dict(UI.FALLBACK_THEME)
            colors.update({
                "overlay": "102030",
                "panel": "203040",
                "primary": "304050",
                "secondary": "405060",
                "text": "506070",
                "dim": "607080",
            })
            (theme_directory / "early.json").write_text(json.dumps({
                "schema_version": 2,
                "name": "EARLY",
                "description": "Early startup behavioral theme",
                "colors": colors,
            }), encoding="utf-8")

            renderer = FEATHER.FeatherRenderer(
                theme_directories=(str(theme_directory),))
            events = []
            renderer.start = lambda: events.append((
                "start", renderer.theme_name,
                renderer.color(UI.ThemeColor.OVERLAY)))
            renderer.send = lambda commands, **kwargs: events.append((
                "draw", renderer.theme_name, "\n".join(commands)))
            params = type("Params", (), {
                "variables": {"feather_theme": "EARLY"},
            })()

            class Printer:
                def lookup_object(self, name, default=None):
                    return params if name == "mod_params" else default

            class TimerReactor:
                NOW = 0.0

                def register_timer(self, callback, when):
                    return (callback, when)

            controller = ScenarioController.__new__(ScenarioController)
            controller.renderer = renderer
            controller.printer = Printer()
            controller.reactor = TimerReactor()
            controller._enable_backlight = lambda: None
            controller.startup_phase = 0
            controller.startup_restarting = False
            controller.startup_timer = None

            controller._start_pre_ready_ui()

            self.assertIs(controller.params, params)
            self.assertEqual(events[0], ("start", "EARLY", "102030"))
            self.assertEqual(events[1][0:2], ("draw", "EARLY"))
            self.assertIn(
                "-p 0 0 -s %d %d -c 102030" %
                (UI.SCREEN_WIDTH, UI.SCREEN_HEIGHT),
                events[1][2])

    def test_firmware_restart_reapplies_persisted_theme_before_modal(self):
        events = []

        class Renderer:
            active = True
            _worker = object()

            def set_theme(self, name):
                events.append(("theme", name))
                return True

            def startup_modal(self, title, detail, phase, critical=False):
                events.append(("modal", title, detail, phase, critical))

        class TimerReactor:
            NOW = 0.0

            def register_timer(self, callback, when):
                return (callback, when)

        controller = ScenarioController.__new__(ScenarioController)
        controller.renderer = Renderer()
        controller.params = type("Params", (), {
            "variables": {"feather_theme": "USER_THEME"},
        })()
        controller.printer = object()
        controller.reactor = TimerReactor()
        controller._enable_backlight = lambda: None
        controller.startup_phase = 2
        controller.startup_restarting = False
        controller.startup_timer = None

        controller._start_pre_ready_ui(restarting=True)

        self.assertEqual(events, [
            ("theme", "USER_THEME"),
            ("modal", "INITIALIZING KLIPPER",
             "RESTART IN PROGRESS - DISPLAY MAY PAUSE", 2, True),
        ])

    def test_startup_tick_advances_pulse_until_klipper_is_ready(self):
        controller = ScenarioController.__new__(ScenarioController)
        pulses = []
        controller.renderer = type("Renderer", (), {
            "active": True,
            "startup_pulse": lambda self, phase: ["pulse %d" % phase],
            "send": lambda self, commands: pulses.append(commands),
        })()
        controller.printer = type("Printer", (), {
            "get_state_message": lambda self: ("Printer is not ready", "startup"),
        })()
        controller.reactor = type("Reactor", (), {"NEVER": 1.0e30})()
        controller.event_handle = object()
        controller.print_state = FEATHER.PrintState.INACTIVE
        controller.error_message = ""
        controller.startup_phase = 0
        controller.startup_restarting = False

        wake = controller._startup_tick(10.0)
        self.assertEqual(pulses, [["pulse 1"]])
        self.assertAlmostEqual(wake, 10.0 + FEATHER.STARTUP_ANIMATION_PERIOD)

        controller.print_state = FEATHER.PrintState.IDLE
        self.assertEqual(controller._startup_tick(11.0),
                         controller.reactor.NEVER)

    def test_startup_tick_replaces_animation_with_config_error(self):
        controller = ScenarioController.__new__(ScenarioController)
        shown = []
        controller.renderer = type("Renderer", (), {"active": True})()
        controller.reactor = type("Reactor", (), {"NEVER": 1.0e30})()
        controller.printer = type("Printer", (), {
            "get_state_message": lambda self: (
                "Option 'foo' is not valid", "error"),
        })()
        controller.event_handle = object()
        controller.print_state = FEATHER.PrintState.INACTIVE
        controller.error_message = ""
        controller.startup_timer = object()
        controller._show_error = (
            lambda message, category: shown.append((message, category)))

        wake = controller._startup_tick(10.0)

        self.assertEqual(wake, controller.reactor.NEVER)
        self.assertEqual(controller.startup_timer, None)
        self.assertEqual(len(shown), 1)
        self.assertEqual(shown[0][1], "error")


class ResurrectionStatusTest(unittest.TestCase):
    def test_status_hides_absolute_path_and_reports_progress(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=True) as stream:
            json.dump({"file_path": "/data/gcodes/part.gcode", "file_position": 25,
                       "file_size": 100, "extruder_temp": 220, "bed_temp": 60,
                       "mesh": "auto"}, stream)
            stream.flush()
            resurrector = RESURRECTION.Resurrector.__new__(RESURRECTION.Resurrector)
            resurrector.state = RESURRECTION.ResurrectorState.RESURRECTION
            resurrector.file_path = stream.name
            status = resurrector.get_status(0)
        self.assertTrue(status["available"])
        self.assertEqual(status["filename"], "part.gcode")
        self.assertEqual(status["progress"], 0.25)
        self.assertNotIn("file_path", status)


if __name__ == "__main__":
    unittest.main()
