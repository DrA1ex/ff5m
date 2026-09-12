## Suite policy and scenario-local state for Feather UI test runs.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import base64
import binascii
import importlib
import json
import math
import os
import re
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace

from ff5m_ui.screen import ScreenPage
from ff5m_ui.print_state import PrintState
from ff5m_ui.move import actions as move_actions
from ff5m_ui.z_offset import actions as z_actions


MOTION_STEP_TIMEOUT = 10.0
MOTION_STEP_INTERVAL = 0.1
COMPONENT_CASE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,95}$")
MAX_COMPONENT_CASE_BYTES = 32 * 1024
MAX_COMPONENT_CASES = 64
_MISSING = object()


@contextmanager
def _temporary_attributes(target, values):
    """Temporarily replace concrete UI inputs and always restore them."""
    original = {}
    namespace = vars(target)
    for name, value in values.items():
        original[name] = namespace.get(name, _MISSING)
        setattr(target, name, value)
    try:
        yield
    finally:
        for name, value in original.items():
            if value is _MISSING:
                delattr(target, name)
            else:
                setattr(target, name, value)


def _bounded_component_value(value, depth=0):
    if value is None or isinstance(value, (bool, int, float)):
        return True
    if isinstance(value, str):
        return len(value) <= 256
    if depth >= 3:
        return False
    if isinstance(value, list):
        return len(value) <= 32 and all(
            _bounded_component_value(item, depth + 1) for item in value)
    if isinstance(value, dict):
        return len(value) <= 32 and all(
            isinstance(key, str) and len(key) <= 128
            and _bounded_component_value(item, depth + 1)
            for key, item in value.items())
    return False


class ScenarioCatalog:
    """Build fixed suites and own only their scenario-local mutable state."""

    def __init__(self, run):
        self.run = run
        self.motion_origin = None
        self.motion_expected = None
        self.heat_initial = None
        self.heat_stable_since = None
        self._mesh_snapshot = None
        self.ui_filament_target = None
        self.z_probe_local = None
        self._update_maybe_present = None

    @property
    def host(self):
        return self.run.host

    @property
    def reactor(self):
        return self.run.reactor

    @property
    def material(self):
        return self.run.material

    @property
    def component_cases(self):
        return self.run.component_cases

    @property
    def context_fixture(self):
        return self.run.context_fixture

    @property
    def snapshot(self):
        return self.run.snapshot

    @property
    def test_results(self):
        return self.run.test_results

    def _start_context_scenario(self, name, fixtures):
        self.run._start_context_scenario(name, fixtures)

    def _finish_context_scenario(self):
        self.run._finish_context_scenario()

    def build_steps(self, suite):
        steps = []
        self._add_capture(steps, "baseline")
        steps[-1]["phase"] = "baseline"
        phases = (["ui", "render", "motion", "heat", "screws", "mesh", "z"]
                  if suite == "FULL" else [suite.lower()])
        builders = {
            "ui": self._steps_ui,
            "component": self._steps_component,
            "render": self._steps_render,
            "motion": self._steps_motion,
            "heat": self._steps_heat,
            "screws": self._steps_screws,
            "mesh": self._steps_mesh,
            "z": self._steps_z,
            "context_print": self._steps_context_print,
            "context_material": self._steps_context_material,
        }
        for phase in phases:
            first = len(steps)
            if phase in ("context_print", "context_material"):
                builders[phase](steps)
            else:
                fixtures = {
                    "screws": ("screws",),
                    "mesh": ("mesh_clean", "mesh_skip_clean"),
                    "z": ("z_offset_skip_clean",),
                }.get(phase, ("none",))
                self._add_call(
                    steps, "%s-context-start" % phase,
                    lambda name=phase, choices=fixtures:
                    self._start_context_scenario(name, choices), delay=0.0)
                builders[phase](steps)
                self._add_call(
                    steps, "%s-context-verify" % phase,
                    self._finish_context_scenario, delay=0.0)
            for step in steps[first:]:
                step["phase"] = phase
                if phase == "context_print":
                    for prefix, subphase in (
                            ("print_mesh-", "print_mesh"),
                            ("recovery-", "recovery"),
                            ("print_kamp-", "print_kamp")):
                        if step["label"].startswith(prefix):
                            step["phase"] = subphase
                            break
                elif phase == "context_material":
                    for prefix, subphase in (
                            ("filament-", "filament"),
                            ("cold_pull-", "cold_pull")):
                        if step["label"].startswith(prefix):
                            step["phase"] = subphase
                            break
        return steps

    @staticmethod
    def _add_call(steps, label, callback, delay=0.15):
        steps.append({"kind": "call", "label": label,
                      "callback": callback, "delay": delay})

    @staticmethod
    def _add_tap(steps, action, page=None, label=None):
        steps.append({"kind": "tap", "action": action, "page": page,
                      "label": label or str(action)})

    @classmethod
    def _add_semantic_tap(cls, steps, action, page=None):
        """Tap the exact deterministic wire identity of a typed command."""
        cls._add_tap(
            steps, action.wire_id, page,
            label=str(action.key.value))

    @staticmethod
    def _add_tap_label(steps, button_label, page=None):
        steps.append({"kind": "tap_label", "button_label": button_label,
                      "page": page, "label": button_label})

    @staticmethod
    def _add_wait(steps, label, predicate, timeout, interval=0.5):
        steps.append({"kind": "wait", "label": label,
                      "predicate": predicate, "timeout": timeout,
                      "interval": interval})

    @staticmethod
    def _add_capture(steps, label):
        steps.append({"kind": "capture", "label": label})

    @classmethod
    def _add_render_capture(cls, steps, label, callback):
        cls._add_call(steps, label, callback)
        cls._add_capture(steps, label)

    @staticmethod
    def _add_case_capture(steps, label, case_id):
        steps.append({
            "kind": "capture", "label": label, "case_id": case_id,
        })

    def _steps_ui(self, steps):
        self._add_call(steps, "ui-pause-timer", self._pause_ui_timer)
        self._add_render_capture(
            steps, "ui-home-filled", self._render_filled_home)
        self._add_call(steps, "ui-home", lambda: self._show(ScreenPage.IDLE_HOME))
        self._add_capture(steps, "ui-home")
        self._add_tap(steps, "nav.filament", ScreenPage.FILAMENT_MATERIAL)
        self._add_tap(steps, "nav.back", ScreenPage.IDLE_HOME)
        self._add_tap(steps, "nav.move", ScreenPage.CONTROL_MOVE)
        self._add_tap(steps, "nav.back", ScreenPage.IDLE_HOME)
        self._add_tap(steps, "nav.menu", ScreenPage.MAIN_MENU)
        self._add_capture(steps, "ui-main-menu")
        self._add_tap(steps, "nav.files", ScreenPage.FILE_BROWSER)
        self._add_capture(steps, "ui-files")
        for label, callback in (
                ("ui-files-loading", self._render_file_loading_snapshot),
                ("ui-files-empty", self._render_empty_file_browser),
                ("ui-files-usb", self._render_usb_file_browser)):
            self._add_render_capture(steps, label, callback)
        self._add_call(
            steps, "ui-files-return", lambda: self._show(ScreenPage.FILE_BROWSER))
        self._add_call(steps, "ui-file-confirm", self._open_safe_file_confirm)
        self._add_capture(steps, "ui-file-confirm")
        self._add_call(steps, "ui-file-return", self._return_from_file_confirm)
        # The internal file browser belongs to the home screen, so its Back
        # action returns there rather than to the menu used to open it.
        self._add_tap(steps, "nav.back", ScreenPage.IDLE_HOME)
        self._add_tap(steps, "nav.menu", ScreenPage.MAIN_MENU)
        self._add_tap(steps, "nav.control", ScreenPage.CONTROL_HOME)
        self._add_capture(steps, "ui-control")
        self._add_tap(steps, "nav.move", ScreenPage.CONTROL_MOVE)
        self._add_capture(steps, "ui-move")
        self._add_tap(steps, "nav.back", ScreenPage.CONTROL_HOME)
        self._add_tap(steps, "nav.heat", ScreenPage.CONTROL_HEAT)
        self._add_capture(steps, "ui-heat")
        self._add_tap(steps, "nav.back", ScreenPage.CONTROL_HOME)
        self._add_tap(steps, "nav.calibration", ScreenPage.CALIBRATION_HOME)
        self._add_capture(steps, "ui-calibration")
        self._add_call(steps, "ui-calibration-pages",
                       self._render_calibration_variants)
        self._add_capture(steps, "ui-calibration-variants")
        for kind in ("extruder", "axes"):
            self._add_render_capture(
                steps, "ui-calibration-guide-" + kind,
                lambda value=kind: self._render_calibration_guide(value))
        for kind in ("error", "cancelled", "tuning"):
            self._add_render_capture(
                steps, "ui-calibration-result-" + kind,
                lambda value=kind: self._render_calibration_result(value))
        for kind in ("normal", "warning", "save"):
            self._add_render_capture(
                steps, "ui-live-z-" + kind,
                lambda value=kind: self._render_live_z(value))
        for phase in (
                "intro", "material", "cold_pull", "cut", "cooling",
                "remove", "load", "mark_first", "mark_second",
                "measure_ready", "input", "warning", "result",
                "exit_warning", "saved"):
            self._add_render_capture(
                steps, "ui-extruder-" + phase.replace("_", "-"),
                lambda value=phase: self._render_extruder_phase(value))
        self._add_call(
            steps, "ui-calibration-return",
            lambda: self._show(ScreenPage.CALIBRATION_HOME))
        self._add_tap(steps, "nav.back", ScreenPage.CONTROL_HOME)
        self._add_tap(steps, "nav.settings", ScreenPage.SETTINGS)
        self._add_capture(steps, "ui-settings")
        self._add_tap(steps, "settings.mod", ScreenPage.MOD_SETTINGS)
        self._add_capture(steps, "ui-mod-parameters")
        self._add_render_capture(
            steps, "ui-mod-parameters-next", self._render_next_mod_page)
        self._add_render_capture(
            steps, "ui-parameter-options", self._render_parameter_options)
        self._add_render_capture(
            steps, "ui-parameter-options-disabled",
            lambda: self._render_parameter_options(disabled_page=True))
        self._add_render_capture(
            steps, "ui-mod-value-numeric",
            lambda: self._render_mod_value("numeric"))
        self._add_render_capture(
            steps, "ui-mod-value-text",
            lambda: self._render_mod_value("text"))
        self._add_render_capture(
            steps, "ui-applying-changes", self._render_applying_changes)
        self._add_render_capture(
            steps, "ui-render-benchmark-populated",
            self._render_populated_benchmark)
        self._add_call(
            steps, "ui-settings-return", lambda: self._show(ScreenPage.MOD_SETTINGS))
        self._add_tap(steps, "nav.back", ScreenPage.SETTINGS)
        self._add_tap(steps, "nav.back", ScreenPage.CONTROL_HOME)
        self._add_tap(steps, "nav.back", ScreenPage.MAIN_MENU)
        self._add_tap(steps, "nav.filament", ScreenPage.FILAMENT_MATERIAL)
        self._add_capture(steps, "ui-filament-materials")
        self._add_call(steps, "ui-filament-action",
                       self._render_safe_filament_action)
        self._add_capture(steps, "ui-filament-action")
        self._add_call(steps, "ui-filament-cooling",
                       self._render_safe_filament_cooling)
        self._add_capture(steps, "ui-filament-cooling")
        self._add_call(steps, "ui-filament-target",
                       self._remember_ui_filament_target)
        self._add_tap(steps, "nav.back", ScreenPage.FILAMENT_MATERIAL)
        self._add_call(steps, "ui-filament-target-preserved",
                       self._assert_ui_filament_target_preserved)
        self._add_capture(steps, "ui-filament-back-materials")
        self._add_tap(steps, "nav.back", ScreenPage.MAIN_MENU)
        self._add_tap(steps, "nav.network", ScreenPage.NETWORK_HOME)
        self._add_capture(steps, "ui-network")
        for kind in ("offline", "unavailable"):
            self._add_render_capture(
                steps, "ui-network-" + kind,
                lambda value=kind: self._render_network_home_snapshot(value))
        self._add_render_capture(
            steps, "ui-wifi-scan", self._render_wifi_scan_snapshot)
        self._add_render_capture(
            steps, "ui-wifi-scan-empty",
            lambda: self._render_wifi_scan_snapshot(empty=True))
        self._add_render_capture(
            steps, "ui-wifi-password-hidden",
            lambda: self._render_wifi_password_snapshot(visible=False))
        self._add_render_capture(
            steps, "ui-wifi-password-valid",
            lambda: self._render_wifi_password_snapshot(visible=True))
        for kind in ("scan", "connect", "external", "cancel"):
            self._add_render_capture(
                steps, "ui-network-progress-" + kind,
                lambda value=kind: self._render_network_progress_snapshot(value))
        self._add_render_capture(
            steps, "ui-message-two-actions", self._render_two_action_message)
        self._add_call(
            steps, "ui-network-return",
            lambda: self._show(ScreenPage.NETWORK_HOME))
        self._add_tap(steps, "nav.back", ScreenPage.MAIN_MENU)
        self._add_tap(steps, "nav.back", ScreenPage.IDLE_HOME)
        self._add_render_capture(
            steps, "ui-print-preparing", self._render_preparing_print)
        for kind in ("normal", "pending", "not-cancelable"):
            self._add_render_capture(
                steps, "ui-cancel-" + kind,
                lambda value=kind: self._render_cancel_snapshot(value))
        self._add_render_capture(
            steps, "ui-recovery-cleanup", self._render_recovery_cleanup)
        for kind in ("restart", "firmware-restart", "reconnecting"):
            self._add_render_capture(
                steps, "ui-error-" + kind,
                lambda value=kind: self._render_error_snapshot(value))
        self._add_render_capture(
            steps, "ui-update-short",
            lambda: self._render_update_snapshot(long=False))
        self._add_render_capture(
            steps, "ui-update-long",
            lambda: self._render_update_snapshot(long=True))
        self._add_render_capture(
            steps, "ui-update-progress", self._render_update_progress_snapshot)
        self._add_render_capture(
            steps, "ui-update-restart", self._render_update_restart_snapshot)
        for kind in ("startup", "restart", "shutdown"):
            self._add_render_capture(
                steps, "ui-lifecycle-" + kind,
                lambda value=kind: self._render_lifecycle_snapshot(value))
        self._add_render_capture(
            steps, "ui-touch-unavailable", self._render_touch_unavailable)
        self._add_call(
            steps, "ui-overlay-base", lambda: self._show(ScreenPage.IDLE_HOME))
        self._add_render_capture(
            steps, "ui-busy-notice", self._render_busy_notice)
        self._add_call(steps, "ui-busy-clear", self._clear_busy_notice)
        self._add_render_capture(steps, "ui-toast", self._render_toast)
        self._add_call(steps, "ui-resume-timer", self._resume_ui_timer)

    @staticmethod
    def _component_pages():
        """Discover module-level declarative pages only in the cold test path."""
        import ff5m_ui as package
        from ui.layout import DeclarativePage
        package_root = os.path.dirname(package.__file__)
        modules = set()
        for root, directories, files in os.walk(package_root):
            directories[:] = [
                name for name in directories if name != "__pycache__"]
            for filename in files:
                if not filename.endswith(".py"):
                    continue
                relative = os.path.relpath(
                    os.path.join(root, filename), package_root)
                parts = relative[:-3].split(os.sep)
                if parts[-1] == "__init__":
                    parts.pop()
                modules.add(package.__name__ + (
                    "." + ".".join(parts) if parts else ""))
        pages = {}
        for module_name in sorted(modules):
            module = importlib.import_module(module_name)
            for value in vars(module).values():
                if not isinstance(value, DeclarativePage):
                    continue
                pages.setdefault(value.page_id, value)
        return tuple(pages[key] for key in sorted(pages))

    def _steps_component(self, steps):
        self._add_call(
            steps, "component-pause-timer", self._pause_ui_timer)
        for page in self._component_pages():
            slug = re.sub(
                r"[^a-z0-9]+", "-",
                page.page_id.rsplit(".", 1)[-1].lower()).strip("-")
            case_id = "default-" + slug
            self._add_call(
                steps, "component-render-" + slug,
                lambda item=page: self._render_component_default(item))
            self._add_case_capture(
                steps, "component-" + case_id, case_id)
        for case in getattr(self, "component_cases", ()):
            self._add_call(
                steps, "component-render-" + case["id"],
                lambda item=case: self._render_component_case(item))
            self._add_case_capture(
                steps, "component-" + case["id"], case["id"])
        self._add_call(
            steps, "component-resume-timer", self._resume_ui_timer)

    def _render_component_default(self, page):
        title = self._component_title(page)
        commands = self.host.renderer.begin_page(title, back=False)
        commands += page.draw(self.host.renderer, {})
        self.host.renderer.send(commands)
        self._render_component_footer()

    def _render_component_case(self, case):
        page = case["page"]
        title = self._component_title(page)
        commands = self.host.renderer.begin_page(title, back=False)
        commands += page.draw(self.host.renderer, case["state"])
        self.host.renderer.send(commands)
        self._render_component_footer()

    @staticmethod
    def _component_title(page):
        title = getattr(page, "title", None)
        if title:
            return str(title)
        title = str(getattr(page.page_key, "value", page.page_key))
        return title.replace("_", " ").replace(".", " / ")

    def _render_component_footer(self):
        self.host.renderer.footer(
            "NOZZLE 25/0C | BED 25/0C", "PREVIEW | IDLE")

    def _decode_component_cases(self, encoded):
        if not encoded:
            return ()
        if len(encoded) > MAX_COMPONENT_CASE_BYTES * 2:
            raise ValueError("COMPONENT cases payload is too large")
        try:
            padding = "=" * (-len(encoded) % 4)
            raw = base64.urlsafe_b64decode(
                (encoded + padding).encode("ascii"))
        except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
            raise ValueError("COMPONENT cases payload is invalid") from exc
        if len(raw) > MAX_COMPONENT_CASE_BYTES:
            raise ValueError("COMPONENT cases payload is too large")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("COMPONENT cases payload is invalid") from exc
        if not isinstance(value, list) or len(value) > MAX_COMPONENT_CASES:
            raise ValueError("COMPONENT cases must be a bounded array")
        pages = dict((page.page_id, page)
                     for page in self._component_pages())
        result = []
        seen = set()
        for item in value:
            if not isinstance(item, dict) or set(item) != {
                    "id", "page", "state"}:
                raise ValueError(
                    "COMPONENT case accepts only id, page, and state")
            case_id = str(item["id"])
            page_id = str(item["page"])
            state = item["state"]
            if (not COMPONENT_CASE_ID.match(case_id) or case_id in seen
                    or page_id not in pages or not isinstance(state, dict)
                    or len(state) > 32):
                raise ValueError("COMPONENT case is invalid")
            page = pages[page_id]
            schema = dict((str(key), key) for key in page.state_schema)
            typed = {}
            for name, raw_value in state.items():
                key = schema.get(str(name))
                if key is None or not _bounded_component_value(raw_value):
                    raise ValueError(
                        "COMPONENT state is not bounded declared state: %s" %
                        name)
                typed[key] = raw_value
            # StateStore validation checks types, choices and numeric bounds.
            page._fresh_state(typed)
            seen.add(case_id)
            result.append({"id": case_id, "page": page, "state": typed})
        return tuple(result)

    def _render_filled_home(self):
        """Render a worst-case dashboard without mutating printer hardware."""
        host = self.host

        class Status:
            def __init__(self, values):
                self.values = values

            def get_status(self, _eventtime):
                return dict(self.values)

        class VirtualSD:
            def is_active(self):
                return True

            def file_path(self):
                return "/data/THIS-IS-A-VERY-LONG-PRINT-FILENAME-FOR-UI.gcode"

        names = (
            "extruder", "heater_bed", "toolhead", "network_status",
            "last_job_name", "print_state", "print_stats", "virtual_sdcard",
            "_current_material", "_print_progress", "_print_time_values",
            "_operation_context_status", "_operation_context_text",
            "_last_dashboard",
        )
        original = dict((name, getattr(host, name)) for name in names)
        try:
            host.extruder = Status({"temperature": 299.0, "target": 300.0})
            host.heater_bed = Status({"temperature": 129.0, "target": 130.0})
            host.toolhead = Status({"homed_axes": "xyz"})
            host.network_status = {
                "mode": "WIFI", "ssid": "VERY-LONG-WIRELESS-NETWORK-NAME",
                "signal": "100%", "ip": "255.255.255.255",
            }
            host.last_job_name = (
                "THIS-IS-A-VERY-LONG-PREVIOUS-PRINT-FILENAME.gcode")
            host.print_state = PrintState.PREPARING
            host.print_stats = Status({
                "state": "printing", "print_duration": 359999.0,
                "info": {"current_layer": 9999, "total_layer": 9999},
            })
            host.virtual_sdcard = VirtualSD()
            host._current_material = lambda: "CARBON-FIBER-POLYCARBONATE"
            host._print_progress = lambda _eventtime, _stats: 1.0
            host._print_time_values = (
                lambda _eventtime, _stats, _progress: (359999.0, 359999.0))
            host._operation_context_status = lambda _eventtime: {
                "context_types": ("print",),
                "context_path": ("Print",),
                "current_state": "CALIBRATING AND PREPARING PRINT SURFACE",
            }
            host._operation_context_text = (
                lambda eventtime=None, status=None:
                "PRINT -> CALIBRATING AND PREPARING PRINT SURFACE")
            host._last_dashboard = None
            host._render_home()
        finally:
            for name, value in original.items():
                setattr(host, name, value)

    def _render_file_loading_snapshot(self):
        with _temporary_attributes(self.host, {
                "file_scan_loading": False,
                "file_scan_source": None,
                "file_scan_phase": 0,
        }):
            self.host._render_file_loading("internal")

    def _render_empty_file_browser(self):
        with _temporary_attributes(self.host, {
                "file_entries": [], "file_page": 0,
                "file_source": "internal",
        }):
            self.host._render_file_entries()

    def _render_usb_file_browser(self):
        entries = [
            {"name": "USB_BENCHY.gcode", "directory": False},
            {"name": "CALIBRATION", "directory": True},
        ]
        with _temporary_attributes(self.host, {
                "file_entries": entries, "file_page": 0,
                "file_source": "usb",
        }):
            self.host._render_file_entries()

    def _render_calibration_guide(self, kind):
        feature = self.host.feature_manager.get("calibration")
        with _temporary_attributes(feature, {"calibration_guide_kind": kind}):
            self._show(ScreenPage.CALIBRATION_GUIDE)

    def _render_calibration_result(self, kind):
        feature = self.host.feature_manager.get("calibration")
        values = {
            "calibration_kind": "pid_bed" if kind == "tuning" else "mesh",
            "calibration_error": (
                "Probe samples exceeded the configured tolerance"
                if kind == "error" else None),
            "calibration_cancelled": kind == "cancelled",
            "calibration_mesh": [],
            "calibration_results": [],
        }
        with _temporary_attributes(feature, values):
            self._show(ScreenPage.CALIBRATION_RESULT)

    def _render_live_z(self, kind):
        feature = self.host.feature_manager.get("z")
        saved = float(feature._setting("z_offset", 0.0))
        delta = (feature.z_adjust_warning_threshold + 0.1
                 if kind == "warning" else 0.02)

        class Status:
            def __init__(self, values):
                self.values = values

            def get_status(self, _eventtime):
                return dict(self.values)

        with _temporary_attributes(self.host, {
                "gcode_move": Status({"homing_origin": (0.0, 0.0, saved + delta)}),
                "print_stats": Status({"state": "printing"}),
                "toolhead": Status({"homed_axes": "xyz"}),
                "print_state": PrintState.PRINTING,
                "weight_sensor": None,
        }), _temporary_attributes(feature, {
                "live_z_step": 0.01,
                "live_z_dialog": "save" if kind == "save" else (
                    "limit" if kind == "warning" else None),
                "z_weight_gauge": None,
        }):
            self._show(ScreenPage.LIVE_Z_OFFSET)

    def _render_extruder_phase(self, phase):
        from feather_extruder_calibration import (
            ExtruderCalibrationSession, UserConfigSnapshot)

        feature = self.host.feature_manager.get("extruder")
        session = ExtruderCalibrationSession("/tmp/feather-ui-test-user.cfg")
        session.begin(7.550)
        session.phase = phase
        session.temperature = 42.4
        session.cooling_message = "COOLING SAFELY"
        session.input_text = "98.750"
        session.cold_pull_material = (
            self.host.cold_pull_materials[0]
            if self.host.cold_pull_materials else "PLA")
        if phase == "warning":
            session.set_measurement("130.000")
        elif phase == "result":
            session.set_measurement("96.250")
            session.file_snapshot = UserConfigSnapshot(
                session.user_cfg_path, b"", None, [], None, None,
                Decimal("7.550"))
        elif phase == "saved":
            session.current_rotation = 7.267

        operation = {
            "contexts": (), "context_path": ("Cold pull",),
            "context_types": ("cold_pull",),
            "current_state": "HEATING NOZZLE", "cancel_available": True,
            "cancel_pending": False, "cancel_request_id": "ui-test",
            "cancel_target_type": "cold_pull",
            "cancel_target_name": "Cold pull",
            "cancel_target_mode": "cooperative",
            "cancel_blocker_type": None, "cancel_blocker_name": None,
            "revision": 1,
        }
        with _temporary_attributes(feature, {
                "extruder_calibration": session,
        }), _temporary_attributes(self.host, {
                "_operation_context_status": lambda eventtime=None: operation,
        }):
            self._show(ScreenPage.EXTRUDER_CALIBRATION)

    def _render_next_mod_page(self):
        feature = self.host.feature_manager.get("settings")
        with _temporary_attributes(feature, {"mod_page": 1}):
            self._show(ScreenPage.MOD_SETTINGS)

    def _mod_parameter(self, kind, key=None):
        import feather_mod_settings as mod_ui

        feature = self.host.feature_manager.get("settings")
        for parameter in feature._mod_parameters():
            if key is not None and parameter.key == key:
                return parameter
            if (key is None and mod_ui.parameter_kind(parameter) == kind
                    and not (kind == "str"
                             and parameter.key == "feather_theme")):
                return parameter
        raise RuntimeError("No visible %s mod parameter" % (key or kind))

    def _render_parameter_options(self, disabled_page=False):
        feature = self.host.feature_manager.get("settings")
        parameter = self._mod_parameter("str", key="feather_theme")
        entries = tuple(SimpleNamespace(
            value=name, label=name, description=description, enabled=True)
            for name, description in (
                ("DEFAULT", "BUILT-IN PALETTE"),
                ("AMBER", "HIGH CONTRAST"),
                ("BLUE", "COOL PALETTE"),
                ("GREEN", "SOFT PALETTE"),
                ("MONO", "MONOCHROME"),
                ("VIOLET", "CUSTOM PALETTE"),
            )) + (SimpleNamespace(
                value=None, label="BROKEN_THEME.JSON",
                description="INVALID COLOR VALUE", enabled=False),)
        with _temporary_attributes(feature, {
                "mod_parameter": parameter,
                "selected_parameter_option": "DEFAULT",
                "_parameter_options_snapshot": entries,
                "parameter_options_page_index": 1 if disabled_page else 0,
        }):
            self._show(ScreenPage.PARAMETER_OPTIONS)

    def _render_mod_value(self, kind):
        feature = self.host.feature_manager.get("settings")
        parameter_kind = "int" if kind == "numeric" else "str"
        parameter = self._mod_parameter(parameter_kind)
        value = "75" if kind == "numeric" else "STARTUP-TONE.MID"
        with _temporary_attributes(feature, {
                "mod_parameter": parameter,
                "mod_edit_value": value,
                "mod_edit_cursor": len(value),
                "mod_keyboard_shift": False,
                "mod_keyboard_symbols": False,
        }):
            self._show(ScreenPage.MOD_VALUE)

    def _render_applying_changes(self):
        self.host.renderer.applying_modal()

    def _render_populated_benchmark(self):
        from ff5m_ui.benchmark.page import PAGE
        from ff5m_ui.benchmark.state import BenchmarkState

        values = {
            BenchmarkState.ANGLE_X: 0.7,
            BenchmarkState.ANGLE_Y: 1.1,
            BenchmarkState.ANGLE_Z: 0.3,
            BenchmarkState.MODE: "text",
            BenchmarkState.COMMIT_FPS: 59.8,
            BenchmarkState.FRAME_MEDIAN_MS: 15.9,
            BenchmarkState.FRAME_P95_MS: 17.4,
            BenchmarkState.TYPER_MS: 6.2,
            BenchmarkState.CPU_MS: 2.1,
            BenchmarkState.FLUSH_MS: 3.4,
            BenchmarkState.PYTHON_MS: 1.8,
            BenchmarkState.MISSED_PERCENT: 0.3,
            BenchmarkState.RASTER: "NEON",
            BenchmarkState.STATUS: "LIVE / 60.0 FPS",
        }
        self._render_component_case({"page": PAGE, "state": values})

    def _render_network_home_snapshot(self, kind):
        connected = kind != "unavailable"
        status = {
            "mode": "OFFLINE", "state": "DISCONNECTED",
            "ssid": "", "signal": "", "ip": "",
        }
        with _temporary_attributes(self.host, {
                "network_client": SimpleNamespace(connected=connected),
                "network_status": status,
                "network_operation": None,
                "network_cancel_pending": False,
        }):
            self._show(ScreenPage.NETWORK_HOME)

    def _render_wifi_scan_snapshot(self, empty=False):
        networks = [] if empty else [
            {"ssid": "FORGE-X LAB", "signal": -38,
             "frequency": 5180, "saved": True},
            {"ssid": "WORKSHOP 2.4G", "signal": -61,
             "frequency": 2412, "saved": False},
            {"ssid": "A VERY LONG ACCESS POINT NAME", "signal": -78,
             "frequency": 2462, "saved": False},
        ]
        with _temporary_attributes(self.host, {
                "networks": networks, "network_page": 0,
        }):
            self._show(ScreenPage.WIFI_SCAN)

    def _render_wifi_password_snapshot(self, visible):
        password = "valid-password" if visible else "secret12"
        with _temporary_attributes(self.host, {
                "selected_network": {"ssid": "FORGE-X LAB", "saved": False},
                "password": password, "password_cursor": len(password),
                "password_visible": visible, "keyboard_shift": False,
                "keyboard_symbols": False,
        }):
            self._show(ScreenPage.WIFI_PASSWORD)

    def _render_network_progress_snapshot(self, kind):
        operation = {
            "scan": "scan", "connect": "wifi",
            "external": None, "cancel": None,
        }[kind]
        status = {
            "state": "CONNECTING",
            "progress": "HANDSHAKE" if kind == "connect" else "STARTUP",
            "attempt": "2/3" if kind == "connect" else "",
        }
        with _temporary_attributes(self.host, {
                "network_operation": operation,
                "network_cancel_pending": kind == "cancel",
                "network_status": status,
        }):
            self._show(ScreenPage.NETWORK_PROGRESS)

    def _render_two_action_message(self):
        with _temporary_attributes(self.host, {
                "message": "The saved Wi-Fi password was rejected.",
                "message_return": ScreenPage.WIFI_SCAN,
                "message_actions": (
                    ("message.ok", "CANCEL", "enabled"),
                    ("net.reset.saved", "RESET PASSWORD", "warning"),
                ),
        }):
            self._show(ScreenPage.MESSAGE)

    def _render_preparing_print(self):
        class Status:
            def __init__(self, values):
                self.values = values

            def get_status(self, _eventtime):
                return dict(self.values)

        class VirtualSD:
            def file_path(self):
                return "/data/PREPARING_FIRST_LAYER_TEST.gcode"

            def get_status(self, _eventtime):
                return {"progress": 0.0, "estimate_print_time": 900.0}

            def is_active(self):
                return True

        operation = {
            "contexts": (), "context_path": ("Print",),
            "context_types": ("print",),
            "current_state": "CALIBRATING BED MESH",
            "cancel_available": True, "cancel_pending": False,
            "cancel_request_id": None, "cancel_target_type": "print",
            "cancel_target_name": "Print", "cancel_target_mode": "cooperative",
            "cancel_blocker_type": None, "cancel_blocker_name": None,
            "revision": 1,
        }
        with _temporary_attributes(self.host, {
                "print_state": PrintState.PREPARING,
                "print_stats": Status({
                    "state": "printing", "print_duration": 12.0,
                    "info": {"current_layer": 0, "total_layer": 120},
                }),
                "virtual_sdcard": VirtualSD(),
                "toolhead": Status({
                    "homed_axes": "xyz", "position": (110.0, 110.0, 0.3, 0.0),
                }),
                "motion_report": None,
                "_operation_context_status": lambda eventtime=None: operation,
        }):
            self._show(ScreenPage.PRINTING)

    def _render_cancel_snapshot(self, kind):
        mode = "not_cancelable" if kind == "not-cancelable" else kind
        operation = {
            "contexts": (), "context_path": ("Bed mesh",),
            "context_types": ("calibration",),
            "current_state": "PROBING POINT 12 OF 25",
            "cancel_available": True, "cancel_pending": kind == "pending",
            "cancel_request_id": "ui-test", "cancel_target_type": "calibration",
            "cancel_target_name": "Bed mesh", "cancel_target_mode": "cooperative",
            "cancel_blocker_type": None, "cancel_blocker_name": None,
            "revision": 1,
        }
        with _temporary_attributes(self.host, {
                "cancel_mode": mode,
                "operation_cancel_target_name": "Bed mesh",
                "operation_cancel_target_mode": "cooperative",
                "cancel_waiting_for_heat": False,
                "busy_phase": 2,
                "_operation_context_status": lambda eventtime=None: operation,
        }):
            self._show(ScreenPage.CANCEL_CONFIRM)

    def _render_recovery_cleanup(self):
        with _temporary_attributes(self.host, {"recovery_action": "cleanup"}):
            self._show(ScreenPage.RECOVERY_CONFIRM)

    def _render_error_snapshot(self, kind):
        recovery = {
            "restart": "restart",
            "firmware-restart": "firmware_restart",
            "reconnecting": None,
        }[kind]
        message = {
            "restart": "Klipper configuration could not be loaded.",
            "firmware-restart": "MCU shutdown: timer too close.",
            "reconnecting": "Klipper disconnected; reconnecting to host.",
        }[kind]
        with _temporary_attributes(self.host, {
                "error_message": message,
                "error_category": "",
                "error_recovery": recovery,
        }):
            self._show(ScreenPage.ERROR)

    def _render_update_snapshot(self, long):
        notification = getattr(self.host, "update_notification", None)
        if notification is None:
            raise RuntimeError("Update notification feature is unavailable")
        changes = (("Fix Wi-Fi password screen subtitle",)
                   if not long else tuple(
                       "CHANGE %02d: EXERCISE PAGINATED RELEASE NOTES" % index
                       for index in range(1, 15)))
        with _temporary_attributes(notification, {
                "installed_version": "1.4.1-243",
                "available_version": "1.4.1-244",
                "changes": changes,
                "change_page": 1 if long else 0,
        }):
            self._show(ScreenPage.UPDATE_NOTIFICATION)

    def _render_update_progress_snapshot(self):
        notification = getattr(self.host, "update_notification", None)
        if notification is None:
            raise RuntimeError("Update notification feature is unavailable")
        with _temporary_attributes(notification, {
                "installing": True,
        }), _temporary_attributes(self.host, {
                "busy_message": "RECEIVING UPDATE: 42%",
                "busy_phase": 2,
        }):
            self._show(ScreenPage.UPDATE_NOTIFICATION)

    def _render_update_restart_snapshot(self):
        notification = getattr(self.host, "update_notification", None)
        if notification is None:
            raise RuntimeError("Update notification feature is unavailable")
        with _temporary_attributes(notification, {
                "installing": True,
        }), _temporary_attributes(self.host, {
                "busy_message": (
                    "PRINTER WILL RESTART NOW\n"
                    "IF IT DOES NOT RESTART AUTOMATICALLY, RESTART IT MANUALLY"),
                "busy_phase": 2,
        }):
            self._show(ScreenPage.UPDATE_NOTIFICATION)

    def _render_lifecycle_snapshot(self, kind):
        title, detail, critical = {
            "startup": (
                "INITIALIZING KLIPPER", "INITIALIZING PRINTER SERVICES", False),
            "restart": (
                "INITIALIZING KLIPPER",
                "RESTART IN PROGRESS - DISPLAY MAY PAUSE", True),
            "shutdown": ("FORGE-X", "SHUTTING DOWN", True),
        }[kind]
        self.host.renderer.startup_modal(
            title, detail, phase=2, critical=critical)

    def _render_touch_unavailable(self):
        self.host.renderer.touch_unavailable_modal()

    def _render_busy_notice(self):
        self.host.renderer.busy_notice("KLIPPER BUSY")

    def _clear_busy_notice(self):
        self.host.renderer.clear_busy_notice()
        self._show(ScreenPage.IDLE_HOME)

    def _render_toast(self):
        self.host.renderer.toast("SETTINGS UPDATED")

    def _steps_motion(self, steps):
        self._add_call(steps, "motion-open", lambda: self._show(ScreenPage.CONTROL_MOVE))
        self._add_capture(steps, "motion-before-home")
        self._add_call(steps, "motion-caution", self._dismiss_move_caution)
        self._add_semantic_tap(
            steps, move_actions.HOME_ALL, ScreenPage.CONTROL_MOVE)
        self._add_call(steps, "motion-origin", self._save_motion_origin)
        self._add_capture(steps, "motion-homed")
        for axis in "xyz":
            self._add_call(steps, "motion-%s-forward-dispatch" % axis,
                           lambda axis=axis: self._motion_step(axis, 1))
            self._add_wait(
                steps, "motion-%s-forward-complete" % axis,
                lambda axis=axis: self._motion_reached(axis),
                MOTION_STEP_TIMEOUT, MOTION_STEP_INTERVAL)
            self._add_capture(steps, "motion-%s-forward" % axis)
            self._add_call(steps, "motion-%s-return-dispatch" % axis,
                           lambda axis=axis: self._motion_step(axis, -1))
            self._add_wait(
                steps, "motion-%s-return-complete" % axis,
                lambda axis=axis: self._motion_reached(axis),
                MOTION_STEP_TIMEOUT, MOTION_STEP_INTERVAL)
            self._add_capture(steps, "motion-%s-return" % axis)
        self._add_call(steps, "motion-disable", lambda: self.host._run_script("M84"))

    def _steps_render(self, steps):
        before_status = self.host.renderer.get_status()
        before = before_status["typer_restarts"]
        before_error = before_status.get("worker_last_error", "")
        self._add_call(steps, "render-pause-timer", self._pause_ui_timer)
        self._add_call(
            steps, "render-restart-signal",
            lambda: self._request_renderer_restart(before))
        self._add_wait(
            steps, "render-recovered",
            lambda: self._renderer_recovered(before, before_error),
            15.0, 0.1)
        self._add_capture(steps, "render-recovered")
        self._add_call(steps, "render-resume-timer", self._resume_ui_timer)

    def _request_renderer_restart(self, before):
        if not self.host.renderer.restart():
            raise RuntimeError("Renderer restart signal was not accepted")

    def _renderer_recovered(self, before, before_error=""):
        status = self.host.renderer.get_status()
        return (status["worker_state"] == "running"
                and status["typer_restarts"] > before
                and status.get("worker_last_error", "") == before_error)

    def _steps_heat(self, steps):
        self._add_call(steps, "heat-open", lambda: self._show(ScreenPage.CONTROL_HEAT))
        self._add_call(steps, "heat-initial", self._save_heat_initial)
        self._add_capture(steps, "heat-cold")
        self._add_tap_label(steps, self.material, ScreenPage.CONTROL_HEAT)
        self._add_wait(steps, "heat-targets", self._heat_targets_set, 10.0)
        self._add_capture(steps, "heat-target-accepted")
        self._add_wait(steps, "heat-rising", self._heat_is_rising, 120.0, 1.0)
        self._add_capture(steps, "heat-rising")
        self._add_wait(steps, "heat-stable", self._heat_is_stable, 900.0, 2.0)
        self._add_capture(steps, "heat-target-reached")
        self._add_tap_label(steps, "COOLDOWN", ScreenPage.CONTROL_HEAT)
        self._add_wait(steps, "heat-off", self._heaters_off, 10.0)
        self._add_capture(steps, "heat-cooldown-start")

    def _steps_screws(self, steps):
        self._add_call(steps, "screws-open", self._open_calibration_home)
        self._add_tap(steps, "cal.screws", ScreenPage.CALIBRATION_CONFIRM)
        self._add_tap(steps, "cal.clean.skip", ScreenPage.CALIBRATION_CONFIRM)
        self._add_capture(steps, "screws-confirm")
        self._add_tap(steps, "cal.confirm", ScreenPage.CALIBRATION_PROGRESS)
        self._add_wait(steps, "screws-result", self._calibration_result, 1200.0, 1.0)
        self._add_capture(steps, "screws-result")
        self._add_tap(steps, "cal.done", ScreenPage.CALIBRATION_HOME)
        self._add_call(steps, "screws-cleanup", self._hardware_cleanup)

    def _steps_mesh(self, steps):
        self._add_call(steps, "mesh-snapshot", self._save_mesh_snapshot)
        self._add_call(steps, "mesh-open", self._open_calibration_home)
        self._add_tap(steps, "cal.mesh", ScreenPage.CALIBRATION_CONFIRM)
        self._add_capture(steps, "mesh-confirm")
        self._add_tap(steps, "cal.confirm", ScreenPage.CALIBRATION_PROGRESS)
        self._add_wait(steps, "mesh-result", self._calibration_result, 1800.0, 1.0)
        self._add_call(steps, "mesh-validate", self._validate_mesh)
        self._add_capture(steps, "mesh-result")
        self._add_tap(steps, "cal.mesh.discard", ScreenPage.CALIBRATION_HOME)
        self._add_call(steps, "mesh-restore", self._restore_mesh_snapshot)
        self._add_call(steps, "mesh-cleanup", self._hardware_cleanup)

    def _steps_z(self, steps):
        self._add_call(steps, "z-open", self._open_calibration_home)
        self._add_tap(steps, "cal.z", ScreenPage.CALIBRATION_CONFIRM)
        self._add_tap(steps, "cal.clean.skip", ScreenPage.CALIBRATION_CONFIRM)
        self._add_capture(steps, "z-confirm")
        self._add_tap(steps, "cal.confirm", ScreenPage.SAFE_Z_BRIEFING)
        self._add_capture(steps, "z-safe-briefing")
        self._add_semantic_tap(steps, z_actions.SAFE_SKIP)
        self._add_wait(steps, "z-preparation", self._z_summary_ready,
                       1200.0, 1.0)
        self._add_capture(steps, "z-summary-empty")
        self._add_semantic_tap(
            steps, z_actions.ZONE_ACTIONS["center"],
            ScreenPage.Z_OFFSET_PAPER_BRIEFING)
        self._add_capture(steps, "z-paper-briefing")
        self._add_semantic_tap(steps, z_actions.ENTER_ZONE)
        self._add_wait(steps, "z-positioned", self._z_paper_ready,
                       120.0, 0.5)
        self._add_capture(steps, "z-paper-before-probe")
        self._add_semantic_tap(
            steps, z_actions.PROBE, ScreenPage.Z_OFFSET_PAPER)
        self._add_call(steps, "z-dismiss-pressure", self._dismiss_pressure)
        self._add_call(steps, "z-probe-position", self._save_z_probe_position)
        self._add_capture(steps, "z-paper-probed")
        self._add_tap_label(steps, "0.100 MM", ScreenPage.Z_OFFSET_PAPER)
        # The real UI offers at most 0.100 mm. Ten FARTHER presses create the
        # requested 1 mm safety margin, then ten CLOSER presses return to the
        # post-probe point without ever crossing it.
        for _index in range(10):
            self._add_semantic_tap(
                steps, z_actions.FARTHER, ScreenPage.Z_OFFSET_PAPER)
        self._add_call(steps, "z-farther-verify", self._verify_z_farther)
        self._add_capture(steps, "z-paper-farther-1mm")
        for _index in range(10):
            self._add_semantic_tap(
                steps, z_actions.CLOSER, ScreenPage.Z_OFFSET_PAPER)
        self._add_call(steps, "z-return-verify", self._verify_z_return)
        self._add_capture(steps, "z-paper-returned")
        self._add_semantic_tap(steps, z_actions.ACCEPT)
        self._add_wait(steps, "z-summary-result", self._z_summary_ready,
                       120.0, 0.5)
        self._add_capture(steps, "z-summary-result")
        self._add_tap(steps, "nav.back", ScreenPage.Z_OFFSET_SUMMARY)
        self._add_capture(steps, "z-discard-dialog")
        self._add_semantic_tap(steps, z_actions.DISCARD_CONFIRM)
        self._add_wait(steps, "z-discarded", self._z_discarded,
                       120.0, 0.5)
        self._add_call(steps, "z-cleanup-verify", self._verify_z_cleanup)
        self._add_call(steps, "z-hardware-cleanup", self._hardware_cleanup)

    def _steps_context_material(self, steps):
        self._add_call(
            steps, "context_material-prepare",
            self._prepare_context_material)
        self._add_call(
            steps, "filament-context-start",
            lambda: self._start_context_scenario(
                "filament", ("filament",)), delay=0.0)
        self._add_call(
            steps, "filament-open",
            lambda: self.host._run_script("LOAD_MATERIAL"))
        self._add_capture(steps, "filament-material-prompt-screen")
        self._add_prompt_tap(steps, self.material, ScreenPage.ACTION_PROMPT)
        self._add_capture(steps, "filament-action-prompt-screen")
        self._add_prompt_tap(steps, "Load", ScreenPage.ACTION_PROMPT)
        self._add_capture(steps, "filament-loaded-screen")
        self._add_prompt_tap(steps, "Purge", ScreenPage.ACTION_PROMPT)
        self._add_capture(steps, "filament-purged-screen")
        self._add_prompt_tap(steps, "Unload", ScreenPage.ACTION_PROMPT)
        self._add_capture(steps, "filament-unloaded-screen")
        self._add_prompt_tap(steps, "Done")
        self._add_capture(steps, "filament-done-screen")
        self._add_call(
            steps, "filament-context-verify",
            self._finish_context_scenario, delay=0.0)
        self._add_call(
            steps, "cold_pull-context-start",
            lambda: self._start_context_scenario(
                "cold_pull", ("cold_pull",)), delay=0.0)
        self._add_call(
            steps, "cold_pull-open",
            lambda: self.host._run_script("COLDPULL"))
        self._add_capture(steps, "cold_pull-material-prompt-screen")
        self._add_prompt_tap(
            steps, self._context_cold_pull_material(), None)
        self._add_capture(steps, "cold_pull-complete-screen")
        self._add_call(
            steps, "cold_pull-context-verify",
            self._finish_context_scenario, delay=0.0)
        self._add_call(
            steps, "context_material-cleanup", self._hardware_cleanup)

    def _steps_context_print(self, steps):
        self._add_call(
            steps, "context_print-prepare", self._prepare_context_print)

        # The recovery scenario prints a real model and deliberately leaves it
        # on the bed, so every scenario that probes or wipes the bed centre
        # must run before it. KAMP is the only such scenario here.
        self._add_call(
            steps, "print_kamp-context-start",
            lambda: self._start_context_scenario(
                "print_kamp", ("print_kamp",)), delay=0.0)
        self._add_call(
            steps, "print_kamp-file-open",
            lambda: self._open_context_file(self.context_fixture.files[0]))
        self._add_tap(steps, "file.item0", ScreenPage.FILE_CONFIRM)
        self._add_capture(steps, "print_kamp-confirm-screen")
        self._add_tap(steps, "file.start")
        self._add_wait(
            steps, "print_kamp-started", self._context_printing,
            1900.0, 0.5)
        self._add_capture(steps, "print_kamp-printing-screen")
        self._add_wait(
            steps, "print_kamp-complete", self._context_print_complete,
            1900.0, 0.5)
        self._add_capture(steps, "print_kamp-complete-screen")
        self._add_tap(steps, "message.ok", ScreenPage.IDLE_HOME,
                      "print_kamp-finished-dismiss")
        self._add_call(
            steps, "print_kamp-context-verify",
            self._finish_context_scenario, delay=0.0)

        self._add_call(
            steps, "print_mesh-context-start",
            lambda: self._start_context_scenario(
                "print_mesh_resume", ("print_mesh_resume",)), delay=0.0)
        self._add_call(
            steps, "print_mesh-file-open",
            lambda: self._open_context_file(self.context_fixture.files[1]))
        self._add_tap(steps, "file.item0", ScreenPage.FILE_CONFIRM)
        self._add_tap(steps, "file.start")
        self._add_wait(
            steps, "print_mesh-started", self._context_print_controls_ready,
            1900.0, 0.5)
        self._add_capture(steps, "print_mesh-printing-screen")
        self._add_tap(steps, "print.pause")
        self._add_call(
            steps, "print_mesh-pause-motion-complete",
            lambda: self.host._run_script("M400"))
        self._add_wait(
            steps, "print_mesh-paused", self._context_paused,
            10.0, 0.1)
        self._add_capture(steps, "print_mesh-paused-screen")
        self._add_tap(steps, "print.resume")
        self._add_wait(
            steps, "print_mesh-resumed", self._context_print_controls_ready,
            10.0, 0.1)
        self._add_capture(steps, "print_mesh-resumed-screen")
        self._add_wait(
            steps, "print_mesh-pause-for-recovery", self._context_paused,
            600.0, 0.1)
        self._add_call(
            steps, "print_mesh-recovery-pause-motion-complete",
            lambda: self.host._run_script("M400"))
        self._add_wait(
            steps, "print_mesh-recovery-paused", self._context_paused,
            10.0, 0.1)
        self._add_capture(steps, "print_mesh-recovery-paused-screen")
        self._add_wait(
            steps, "print_mesh-idle-timeout", self._context_idle_timeout,
            30.0, 0.25)
        self._add_wait(
            steps, "print_mesh-checkpoint", self._context_checkpoint_ready,
            15.0, 0.25)
        self._add_call(
            steps, "print_mesh-cancel", self._cancel_context_print)
        self._add_wait(
            steps, "print_mesh-cancelled", self._context_cancelled,
            10.0, 0.1)
        self._add_capture(steps, "print_mesh-cancelled-screen")
        self._add_tap(steps, "message.ok", ScreenPage.IDLE_HOME,
                      "print_mesh-cancelled-dismiss")
        self._add_call(
            steps, "print_mesh-activate-recovery",
            self._activate_context_recovery)
        self._add_capture(steps, "print_mesh-recovery-prompt-screen")
        self._add_call(
            steps, "print_mesh-context-verify",
            self._finish_context_scenario, delay=0.0)

        self._add_call(
            steps, "recovery-context-start",
            lambda: self._start_context_scenario(
                "recovery", ("recovery",)), delay=0.0)
        self._add_tap(
            steps, "recovery.restore", ScreenPage.RECOVERY_CONFIRM)
        self._add_capture(steps, "recovery-confirm-screen")
        self._add_tap(
            steps, "recovery.confirm", ScreenPage.CALIBRATION_PROGRESS)
        self._add_capture(steps, "recovery-progress-screen")
        self._add_wait(
            steps, "recovery-printing", self._context_printing,
            1900.0, 0.5)
        self._add_capture(steps, "recovery-printing-screen")
        self._add_wait(
            steps, "recovery-complete", self._context_print_complete,
            1900.0, 0.5)
        self._add_capture(steps, "recovery-complete-screen")
        self._add_tap(steps, "message.ok", ScreenPage.IDLE_HOME,
                      "recovery-finished-dismiss")
        self._add_call(
            steps, "recovery-context-verify",
            self._finish_context_scenario, delay=0.0)
        self._add_call(
            steps, "context_print-cleanup", self._hardware_cleanup)

    @staticmethod
    def _add_prompt_tap(steps, button_label, page=None):
        steps.append({
            "kind": "prompt_tap", "button_label": button_label,
            "page": page, "label": "prompt-%s" % button_label,
        })

    def _action_for_label(self, label):
        matches = [action for action, spec in self.host.renderer._buttons.items()
                   if str(spec[4]).upper() == str(label).upper()]
        if len(matches) != 1:
            raise RuntimeError(
                "Expected one button labelled %s, found %d" %
                (label, len(matches)))
        return matches[0]

    def _prompt_action_for_label(self, label):
        prompt = getattr(self.host, "action_prompt", None) or {}
        matches = [
            action for action, button in prompt.get("buttons", {}).items()
            if str(button.get("label", "")).casefold()
            == str(label).casefold()
        ]
        if len(matches) != 1:
            raise RuntimeError(
                "Expected one action-prompt button labelled %s, found %d" % (
                    label, len(matches)))
        return matches[0]

    def _context_cold_pull_material(self):
        materials = tuple(getattr(self.host, "cold_pull_materials", ()))
        if self.material in materials:
            return self.material
        if not materials:
            raise RuntimeError("No cold-pull material profiles are available")
        return materials[0]

    def _prepare_context_material(self):
        self.context_fixture.prepare_material()
        self._context_cold_pull_material()

    def _prepare_context_print(self):
        self.context_fixture.prepare_print()

    def _open_context_file(self, path):
        self.context_fixture.open_file(
            path, self._show, ScreenPage.FILE_BROWSER)

    def _context_printing(self):
        state = str(self.host.print_stats.get_status(
            self.reactor.monotonic()).get("state", "")).lower()
        return state == "printing" and self.host.page == ScreenPage.PRINTING

    def _context_print_controls_ready(self):
        return (self._context_printing()
                and "print.pause" in self.host.renderer._buttons)

    def _context_paused(self):
        state = str(self.host.print_stats.get_status(
            self.reactor.monotonic()).get("state", "")).lower()
        return (state == "paused" and self.host.page == ScreenPage.PAUSED
                and "print.resume" in self.host.renderer._buttons)

    def _context_print_complete(self):
        state = str(self.host.print_stats.get_status(
            self.reactor.monotonic()).get("state", "")).lower()
        return (state not in ("printing", "paused")
                and not self.host.virtual_sdcard.is_active()
                and self.host.print_state == PrintState.IDLE
                and self.host.page == ScreenPage.MESSAGE
                and "message.ok" in self.host.renderer._buttons)

    def _context_cancelled(self):
        state = str(self.host.print_stats.get_status(
            self.reactor.monotonic()).get("state", "")).lower()
        return (state == "cancelled"
                and not self.host.virtual_sdcard.is_active()
                and self.host.print_state == PrintState.IDLE
                and self.host.page == ScreenPage.MESSAGE
                and "message.ok" in self.host.renderer._buttons)

    def _context_idle_timeout(self):
        status = self.host.idle_timeout.get_status(
            self.reactor.monotonic())
        return str(status.get("state", "")).strip().lower() == "idle"

    def _context_checkpoint_ready(self):
        return self.context_fixture.checkpoint_ready()

    def _cancel_context_print(self):
        if not self._context_checkpoint_ready():
            raise RuntimeError("Runner recovery checkpoint is unavailable")
        resurrection = self.host.resurrection
        resurrection._pause_checkpoint_active = False
        resurrection._resume_pending = False
        state_type = type(resurrection.state)
        resurrection._change_state(state_type.RESURRECTION)
        # CANCEL_PRINT would drop the checkpoint this step arms, so the print is
        # cancelled at the virtual SD level. The pause state still has to be
        # cleared the way CANCEL_PRINT does it.
        self.context_fixture.cancel_print()

    def _activate_context_recovery(self):
        if not self._context_checkpoint_ready():
            raise RuntimeError("Runner recovery checkpoint is unavailable")
        resurrection = self.host.resurrection
        self.host._run_script("_CONTEXT_RESET")
        # A real restart presents recovery from an idle controller. Recreate
        # only that volatile controller fact so the subsequent restored print
        # gets the normal IDLE -> PRINTING page transition.
        self.host.print_state = PrintState.IDLE
        state_type = type(resurrection.state)
        if resurrection.state != state_type.RESURRECTION:
            raise RuntimeError("Runner recovery checkpoint is not armed")
        for line in (
                "// action:prompt_begin Resurrection",
                "// action:prompt_text Resurrection is available! Would you like to restore the print?",
                "// action:prompt_footer_button Restore|RESURRECT",
                "// action:prompt_footer_button Cleanup|RESURRECT_ABORT",
                "// action:prompt_footer_button Later|RESPOND TYPE=command MSG=action:prompt_end",
                "// action:prompt_show"):
            resurrection.gcode.respond_raw(line)
        if self.host.page != ScreenPage.RECOVERY_PROMPT:
            raise RuntimeError("Recovery action prompt did not open")

    def _show(self, page):
        self.host._show_page(page)
        if self.host.page != page:
            raise RuntimeError("Unable to show page %s" % page.name)

    def _render_calibration_variants(self):
        calibration = self.host.feature_manager.get("calibration")
        if "cal.next" in self.host.renderer._buttons:
            calibration._handle_calibration_action("cal.next")

    def _open_calibration_home(self):
        calibration = self.host.feature_manager.get("calibration")
        # UI coverage intentionally leaves the paginated catalog on its next
        # page. Hardware phases must be independent from that presentation
        # state and the three tested entries all live on page zero.
        calibration.calibration_page = 0
        self._show(ScreenPage.CALIBRATION_HOME)

    def _open_safe_file_confirm(self):
        entries = list(getattr(self.host, "file_entries", ()))
        for index, entry in enumerate(entries[:5]):
            if entry["directory"]:
                continue
            action = "file.item%d" % index
            if action not in self.host.renderer._buttons:
                continue
            self.run._tap(action)
            return

    def _return_from_file_confirm(self):
        if self.host.page == ScreenPage.FILE_CONFIRM:
            self.host._show_page(ScreenPage.FILE_BROWSER)

    def _pause_ui_timer(self):
        notification = getattr(self.host, "update_notification", None)
        if (notification is not None
                and self._update_maybe_present is None):
            namespace = vars(notification)
            self._update_maybe_present = (
                "maybe_present" in namespace,
                namespace.get("maybe_present"))
            notification.maybe_present = lambda: False
        timer = getattr(self.host, "timer", None)
        if timer is None:
            return
        self.reactor.unregister_timer(timer)
        self.host.timer = None

    def _resume_ui_timer(self):
        self.restore_synthetic_state()
        if (self.snapshot is None or not self.snapshot.timer_active
                or getattr(self.host, "timer", None) is not None):
            return
        self.host.timer = self.reactor.register_timer(
            self.host._update, self.reactor.NOW)

    def restore_synthetic_state(self):
        notification = getattr(self.host, "update_notification", None)
        saved = self._update_maybe_present
        if notification is not None and saved is not None:
            had_override, callback = saved
            if had_override:
                notification.maybe_present = callback
            else:
                delattr(notification, "maybe_present")
        self._update_maybe_present = None

    def _render_safe_filament_action(self):
        self._render_safe_filament_snapshot(130.4, 250.0)

    def _render_safe_filament_cooling(self):
        self._render_safe_filament_snapshot(260.4, 250.0, update=True)

    def _render_safe_filament_snapshot(self, temperature, target,
                                       update=False):
        if not self.host.heating_materials:
            return
        self.host.filament_material = self.material or self.host.heating_materials[0]
        extruder = self.host.extruder

        class SnapshotExtruder:
            heater = extruder.heater
            min_extrude_temp = getattr(extruder, "min_extrude_temp", 170.0)

            def get_status(self, eventtime):
                status = dict(extruder.get_status(eventtime))
                status.update({
                    "temperature": float(temperature),
                    "target": float(target),
                })
                return status

        # Exercise deliberately wide heating/cooling states without touching
        # either heater or fan. Restoring the object keeps the test read-only.
        self.host.extruder = SnapshotExtruder()
        try:
            if update:
                if self.host.page != ScreenPage.FILAMENT_ACTION:
                    raise RuntimeError(
                        "Filament telemetry update requires action page")
                # Exercise the same declarative dirty-tree path used by live
                # HEATING -> COOLING transitions.  Reopening the current page
                # would clear the framebuffer and test navigation instead of
                # state updates.
                self.host.feature_manager.get("filament").update(
                    self.reactor.monotonic())
            else:
                self._show(ScreenPage.FILAMENT_ACTION)
        finally:
            self.host.extruder = extruder

    def _remember_ui_filament_target(self):
        self.ui_filament_target = float(self.host.extruder.get_status(
            self.reactor.monotonic()).get("target", 0.0))

    def _assert_ui_filament_target_preserved(self):
        target = float(self.host.extruder.get_status(
            self.reactor.monotonic()).get("target", 0.0))
        if target != self.ui_filament_target:
            raise RuntimeError(
                "Filament Back changed nozzle target: %.1f -> %.1f" %
                (self.ui_filament_target, target))

    def _dismiss_move_caution(self):
        buttons = self.host.renderer._buttons
        for action in (move_actions.CAUTION_AUTO.wire_id,
                       move_actions.CAUTION_DISMISS.wire_id,
                       move_actions.CAUTION_UNLOAD.wire_id):
            if action in buttons:
                # Keep synthetic input on the same feedback and dispatch path
                # as a real touch. The following Home tap is a separate step.
                self.run._tap(action)
                break

    def _save_motion_origin(self):
        status = self.host.toolhead.get_status(self.reactor.monotonic())
        if not all(axis in str(status.get("homed_axes", "")) for axis in "xyz"):
            raise RuntimeError("Home All did not home XYZ")
        self.motion_origin = tuple(float(value) for value in status["position"][:3])
        self.host.jog_step = 1.0
        self.host._render_move()

    def _motion_step(self, axis, outward):
        status = self.host.toolhead.get_status(self.reactor.monotonic())
        index = "xyz".index(axis)
        current = float(status["position"][index])
        low, high = self.host._feather_move_limits(status)[index]
        if outward > 0:
            direction = 1 if high - current >= current - low else -1
            self.motion_expected = current + direction
        else:
            origin = self.motion_origin[index]
            # Homing may report a position a fraction beyond the configured UI
            # travel bound (for example Y=110.099675 with a 110.0 maximum).
            # MOVE_SAFE correctly clamps the return, so compare against that
            # reachable coordinate rather than the raw homing overshoot.
            target = max(low, min(high, origin))
            direction = 1 if target > current else -1
            self.motion_expected = target
        actions = {
            ("x", 1): move_actions.X_PLUS,
            ("x", -1): move_actions.X_MINUS,
            ("y", 1): move_actions.Y_PLUS,
            ("y", -1): move_actions.Y_MINUS,
            ("z", 1): move_actions.Z_PLUS,
            ("z", -1): move_actions.Z_MINUS,
        }
        action = actions[(axis, direction)].wire_id
        self.run._tap(action)

    def _motion_reached(self, axis):
        index = "xyz".index(axis)
        actual = float(self.host.toolhead.get_status(
            self.reactor.monotonic())["position"][index])
        return math.isclose(
            actual, self.motion_expected, abs_tol=0.05)

    def _save_heat_initial(self):
        values = self.run._temperatures()
        self.heat_initial = (values["nozzle"], values["bed"])
        self.heat_stable_since = None

    def _heat_targets_set(self):
        values = self.run._temperatures()
        nozzle, bed = self.host.heating_profiles[self.material]
        return (abs(values["nozzle_target"] - nozzle) <= 1.0
                and abs(values["bed_target"] - bed) <= 1.0)

    def _heat_is_rising(self):
        values = self.run._temperatures()
        return (values["nozzle"] >= self.heat_initial[0] + 3.0
                or abs(values["nozzle"] - values["nozzle_target"]) <= 5.0)

    def _heat_is_stable(self):
        values = self.run._temperatures()
        stable = (abs(values["nozzle"] - values["nozzle_target"]) <= 5.0
                  and abs(values["bed"] - values["bed_target"]) <= 3.0)
        now = self.reactor.monotonic()
        if not stable:
            self.heat_stable_since = None
            return False
        if self.heat_stable_since is None:
            self.heat_stable_since = now
        return now - self.heat_stable_since >= 10.0

    def _heaters_off(self):
        values = self.run._temperatures()
        return values["nozzle_target"] <= 0.0 and values["bed_target"] <= 0.0

    def _calibration_result(self):
        return self.host.page == ScreenPage.CALIBRATION_RESULT

    def _z_summary_ready(self):
        return self.host.page == ScreenPage.Z_OFFSET_SUMMARY

    def _z_paper_ready(self):
        return self.host.page == ScreenPage.Z_OFFSET_PAPER

    def _z_discarded(self):
        feature = self.host.feature_manager.get("z")
        return (self.host.page == ScreenPage.CALIBRATION_HOME
                and not feature.z_calibration.active)

    def _save_mesh_snapshot(self):
        mesh = self.host.bed_mesh
        status = mesh.get_status(self.reactor.monotonic())
        self._mesh_snapshot = (
            getattr(mesh, "z_mesh", None), str(status.get("profile_name", "") or ""))

    def _restore_mesh_snapshot(self):
        if self._mesh_snapshot is None:
            return
        feature = self.host.feature_manager.get("z")
        feature._restore_z_mesh(*self._mesh_snapshot)
        current = self.host.bed_mesh.get_status(
            self.reactor.monotonic()).get("profile_name", "")
        if str(current or "") != self._mesh_snapshot[1]:
            raise RuntimeError("Mesh profile was not restored")
        self._mesh_snapshot = None

    def _validate_mesh(self):
        calibration = self.host.feature_manager.get("calibration")
        matrix = calibration.calibration_mesh
        if not matrix or not matrix[0]:
            raise RuntimeError("Mesh result is empty")
        width = len(matrix[0])
        if any(len(row) != width for row in matrix):
            raise RuntimeError("Mesh result is not rectangular")
        if not all(math.isfinite(float(value))
                   for row in matrix for value in row):
            raise RuntimeError("Mesh contains a non-finite value")
        if "cal.mesh.discard" not in self.host.renderer._buttons:
            raise RuntimeError("Mesh result has no DON'T SAVE action")
        self.test_results["mesh"] = matrix

    def _dismiss_pressure(self):
        try:
            action = self._action_for_label("OK")
        except RuntimeError:
            return
        self.run._tap(action)

    def _save_z_probe_position(self):
        feature = self.host.feature_manager.get("z")
        if not feature.z_calibration.ready_for_paper_test:
            raise RuntimeError("Z probe did not produce an adjustable result")
        self.z_probe_local = float(feature.z_calibration.local_z)
        self.test_results["z"] = {"post_probe_local_z": self.z_probe_local}

    def _verify_z_farther(self):
        current = float(self.host.feature_manager.get(
            "z").z_calibration.local_z)
        if not math.isclose(current, self.z_probe_local + 1.0, abs_tol=0.01):
            raise RuntimeError("Z FARTHER sequence did not create 1 mm clearance")
        self.test_results["z"]["farther_local_z"] = current

    def _verify_z_return(self):
        current = float(self.host.feature_manager.get(
            "z").z_calibration.local_z)
        if not math.isclose(current, self.z_probe_local, abs_tol=0.01):
            raise RuntimeError("Z CLOSER sequence did not return safely")
        self.test_results["z"]["returned_local_z"] = current

    def _verify_z_cleanup(self):
        feature = self.host.feature_manager.get("z")
        if feature.z_calibration.active:
            raise RuntimeError("Z calibration session remains active")
        current = float(self.host.gcode_move.get_status(
            self.reactor.monotonic())["homing_origin"][2])
        if not math.isclose(current, self.snapshot.runtime_z, abs_tol=0.001):
            raise RuntimeError("Runtime Z offset was not restored")

    def _hardware_cleanup(self):
        self.host._run_script("TURN_OFF_HEATERS")
