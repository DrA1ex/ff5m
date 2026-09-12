## Interactive Feather screen support
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import errno
import fcntl
import logging
import os
import signal
import sys
import struct
import time

# Klipper loads this entry point as ``extras.feather_screen``, while
# Feather UI is intentionally shared with the standalone Designer under
# the canonical top-level namespaces ``ui`` and ``ff5m_ui``. Register
# the extras directory once, before importing either package, so every
# module is loaded under one stable name.
_PLUGIN_ROOT = os.path.dirname(os.path.realpath(__file__))
if not sys.path or sys.path[0] != _PLUGIN_ROOT:
    try:
        sys.path.remove(_PLUGIN_ROOT)
    except ValueError:
        pass
    sys.path.insert(0, _PLUGIN_ROOT)

from ui import (
    Command, DismissToast, FeatherRenderer, ThemeColor,
    normalize_theme_name, parse_render_receipt,
)
from ff5m_ui.screen import ScreenPage
from ff5m_ui.move import runtime as move_ui
from ff5m_ui.print_state import PrintState
from feather_screen_pages import FeatherPagesMixin, FILE_ROWS
from feather_files import (
        DEFAULT_HISTORY_PATH, FileScanWorker, PrintHistory,
        UsbStorageMonitor)
from feather_screen_controls import (
        FeatherControlsMixin,
        joystick_ui, joystick_motion,
        JOYSTICK_XY_CENTER, JOYSTICK_XY_RADIUS,
        JOYSTICK_Z_CENTER, JOYSTICK_Z_RADIUS)
from feather_materials import load_material_catalog
from feather_feature_manager import (
        FeatureLoadError, FeatureSpec, LazyFeatureManager)
from feather_safety import SafetyRegistry
from feather_keyboard import is_keyboard_action
from feather_update_notification import ForgeXUpdateNotification


DISP_LCD_SET_BRIGHTNESS = 0x102
DISP_LCD_BACKLIGHT_ENABLE = 0x104
REFRESH_TIME = 1.0
ACTION_DEBOUNCE = 0.08
STARTUP_ANIMATION_PERIOD = 0.16
FORGE_X_SCREEN_BUSY_PATH = "/tmp/forge_x_screen_busy"
MAX_TOUCH_EVENT = 256


EXACT_ACTIONS = {
    ScreenPage.IDLE_HOME: (
        "nav.menu", "nav.heat", "nav.network", "nav.job",
        "home.last_job", "nav.filament", "nav.move"),
    ScreenPage.MAIN_MENU: ("nav.back", "nav.files", "nav.control", "nav.filament",
                     "nav.network"),
    ScreenPage.CONTROL_HOME: ("nav.back", "nav.move", "nav.heat", "nav.calibration",
                        "nav.settings"),
    ScreenPage.FILE_BROWSER: (
        "nav.back", "file.prev", "file.next", "file.refresh"),
    ScreenPage.FILE_CONFIRM: (
        "nav.back", "file.start", "file.mesh.rebuild", "file.mesh.auto"),
    ScreenPage.PRINTING: ("nav.home", "print.pause", "print.filament",
                    "print.cancel", "print.z"),
    ScreenPage.PAUSED: ("nav.home", "print.resume", "print.filament",
                  "print.cancel", "print.z"),
    ScreenPage.CANCEL_CONFIRM: (
        "operation.cancel.back", "operation.cancel.confirm",
        "operation.cancel.continue", "operation.cancel.force"),
    ScreenPage.CONTROL_MOVE: ("nav.back",),
    ScreenPage.CONTROL_HEAT: ("nav.back",),
    ScreenPage.NETWORK_HOME: ("nav.back", "net.scan", "net.ethernet"),
    ScreenPage.WIFI_SCAN: ("nav.back", "net.prev", "net.next", "net.rescan"),
    ScreenPage.WIFI_PASSWORD: ("nav.back", "net.connect", "net.password.toggle"),
    ScreenPage.NETWORK_PROGRESS: ("net.cancel", "net.keep"),
    ScreenPage.RECOVERY_PROMPT: (
        "recovery.restore", "recovery.cleanup", "recovery.later"),
    ScreenPage.RECOVERY_CONFIRM: ("nav.back", "recovery.confirm"),
    ScreenPage.ACTION_PROMPT: ("prompt.prev", "prompt.next"),
    ScreenPage.MESSAGE: ("message.ok",),
    ScreenPage.ERROR: ("error.restart", "error.firmware_restart"),
    ScreenPage.UPDATE_NOTIFICATION: (
        "update.install", "update.later", "update.prev", "update.next",
        "update.reset", "update.reset.back", "update.reset.confirm"),
}

ACTIVE_PRINT_STATES = frozenset((
    PrintState.PREPARING, PrintState.PRINTING, PrintState.PAUSED,
))

IDLE_ONLY_NAVIGATION_ACTIONS = frozenset((
    "nav.files", "nav.control", "nav.move", "nav.heat", "nav.filament",
    "nav.calibration", "nav.settings", "home.last_job",
))

CORE_SAFETY_ARMED_PAGES = frozenset((
    ScreenPage.CONTROL_HEAT,
    ScreenPage.FILAMENT_MATERIAL, ScreenPage.FILAMENT_ACTION,
))


_FEATURE_MANAGER_PACKAGE = LazyFeatureManager.__module__.rpartition(".")[0]
_FEATURE_MODULE_PREFIX = (
    "%s." % _FEATURE_MANAGER_PACKAGE if _FEATURE_MANAGER_PACKAGE else "")


def _feature_module(name):
    return _FEATURE_MODULE_PREFIX + name


FEATURE_SPECS = (
    FeatureSpec("ui_test", _feature_module("feather_feature_ui_test"),
                "UITestFeature"),
    FeatureSpec("filament", _feature_module("feather_feature_filament"),
                "FilamentFeature", (
                    ScreenPage.FILAMENT_MATERIAL, ScreenPage.FILAMENT_ACTION)),
    FeatureSpec("calibration", _feature_module("feather_feature_calibration"),
                "CalibrationFeature", (
                    ScreenPage.CALIBRATION_HOME, ScreenPage.CALIBRATION_GUIDE,
                    ScreenPage.CALIBRATION_CONFIRM, ScreenPage.CALIBRATION_PROGRESS,
                    ScreenPage.CALIBRATION_RESULT)),
    FeatureSpec("z", _feature_module("feather_feature_z"),
                "ZCalibrationFeature", (
        ScreenPage.CALIBRATION_Z, ScreenPage.Z_OFFSET_SUMMARY,
        ScreenPage.Z_OFFSET_PAPER_BRIEFING, ScreenPage.Z_OFFSET_PAPER,
        ScreenPage.SAFE_Z_BRIEFING, ScreenPage.SAFE_Z_CALIBRATION,
        ScreenPage.LIVE_Z_OFFSET)),
    FeatureSpec("extruder", _feature_module("feather_feature_extruder"),
                "ExtruderCalibrationFeature", (
                    ScreenPage.EXTRUDER_CALIBRATION,)),
    FeatureSpec("settings", _feature_module("feather_feature_settings"),
                "SettingsFeature", (
        ScreenPage.SETTINGS, ScreenPage.MOD_SETTINGS, ScreenPage.PARAMETER_OPTIONS, ScreenPage.MOD_VALUE)),
    FeatureSpec("benchmark", _feature_module("feather_feature_benchmark"),
                "BenchmarkFeature", (ScreenPage.RENDER_BENCHMARK,)),
)


class FeatherScreen(FeatherPagesMixin, FeatherControlsMixin):
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        self.debug = config.getboolean("debug", False)
        self.blending = config.getboolean("blending", True)
        self.raster_acceleration = str(
            config.get("raster_acceleration", "scalar")).strip().lower()
        if self.raster_acceleration not in ("scalar", "neon"):
            raise config.error(
                "feather_screen raster_acceleration must be scalar or neon")
        self.dim_timeout = config.getfloat("dim_timeout", 60.0, minval=10.0)
        self.z_offset_limit = config.getfloat("z_offset_limit", 2.0, minval=0.1)
        # Parse the retired option so existing user configs keep loading. The
        # guided paper test relies only on the configured hardware axis limits.
        config.getfloat("z_adjust_session_limit", 0.5, minval=0.05)
        self.z_adjust_warning_threshold = config.getfloat(
            "z_adjust_warning_threshold", 0.3, minval=0.05)
        self.print_history = PrintHistory(
            config.get("print_history_path", DEFAULT_HISTORY_PATH))
        self.joystick_limits = (
            (config.getfloat("joystick_x_min", -110.0),
             config.getfloat("joystick_x_max", 110.0)),
            (config.getfloat("joystick_y_min", -110.0),
             config.getfloat("joystick_y_max", 110.0)),
            (config.getfloat("joystick_z_min", 0.0),
             config.getfloat("joystick_z_max", 220.0)),
        )
        if any(low >= high for low, high in self.joystick_limits):
            raise config.error(
                "feather_screen joystick minimum must be below maximum")
        self.heating_materials = ()
        self.heating_profiles = {}
        self.cold_pull_materials = ()
        self.cold_pull_profiles = {}
        self.renderer = FeatherRenderer(
            self.debug, blending=self.blending,
            raster_acceleration=self.raster_acceleration)
        register_async = getattr(
            self.reactor, "register_async_callback", None)
        if register_async is None:
            raise config.error(
                "feather_screen requires reactor.register_async_callback")
        self.file_scan_schedule_async = register_async
        self.renderer.configure_worker(
            register_async, self._renderer_event_fd_changed,
            self._renderer_restarted)
        self.boot_screen_held = os.path.exists(FORGE_X_SCREEN_BUSY_PATH)
        if self.boot_screen_held:
            self.renderer.hold_output()
        self.file_scan_worker = FileScanWorker(register_async)
        self.feature_manager = LazyFeatureManager(self, FEATURE_SPECS)
        self.safety = self._build_safety_registry()
        self.update_notification = ForgeXUpdateNotification(
            self, self.printer.lookup_object("webhooks", None))

        self.page = ScreenPage.IDLE_HOME
        self.previous_page = ScreenPage.IDLE_HOME
        self.print_state = PrintState.INACTIVE
        self.state_time = self.reactor.monotonic()
        self.timer = None
        self.startup_timer = None
        self.startup_phase = 0
        self.event_handle = None
        self.event_partial = ""
        self.touch_available = None
        self.touch_warning_visible = False
        self.touch_warning_restore_frozen = False
        self.last_touch_time = self.reactor.monotonic()
        self.last_action_time = -1.0
        self.touch_feedback_pending = False
        self.dimmed = False
        self.pending_action = None
        self.pending_until = 0.0
        self.command_depth = 0
        self.cancel_requested = False
        self.cancel_waiting_for_heat = False
        self.cancel_mode = None
        self.cancel_phase = None
        self.operation_cancel_return_page = ScreenPage.IDLE_HOME
        self.operation_cancel_on_accept = None
        self.operation_cancel_on_clear = None
        self.operation_cancel_request_id = None
        self.operation_cancel_target_name = None
        self.operation_cancel_target_mode = None
        self._last_cancel_label = None
        self.operation_context = None
        self._last_operation_revision = -1
        self.home_during_print = False
        self.busy_message = None
        self.busy_phase = 0
        self.toast_until = 0.0
        self.toast_message = ""

        self.file_page = 0
        self.file_entries = []
        self.file_entry_cache = {}
        self.file_entry_loaded_at = {}
        self.file_scan_loading = False
        self.file_scan_source = None
        self.file_scan_phase = 0
        self.file_scan_token = 0
        self.selected_file = None
        self.file_confirm_return_page = ScreenPage.FILE_BROWSER
        self.file_confirm_repeat = False
        self.file_confirm_rebuild_mesh = False
        self.file_confirm_auto_mesh = False
        self.file_source = "internal"
        self.usb_storage = None
        self.jog_step = 1.0
        self.move_mode = "step"
        self.joystick = None
        self.joystick_stream = None
        self.joystick_timer = None
        self.joystick_queued = False
        self.joystick_action = None
        self.joystick_suppressed = None
        self.joystick_timer_active = False
        self.joystick_busy_since = None
        self.joystick_cursor = None
        self.joystick_feedback_at = 0.0
        self.move_caution_acknowledged = False
        self.move_caution_signature = None
        self.weight_sensor = None
        self.chamber_light = None

        self._init_network_ui()
        self.filament_material = "n/a"
        self.filament_from_pause = False
        self.filament_original_target = 0.0
        self._filament_request_token = 0
        self._last_filament_heat = None
        self.recovery_action = None
        self.recovery_status = None
        self.action_prompt = None
        self.action_prompt_visible = False
        self.action_prompt_return_page = ScreenPage.IDLE_HOME
        self.action_prompt_page = 0
        self._filament_present = None

        self.message = ""
        self.message_return = ScreenPage.IDLE_HOME
        self.message_actions = (("message.ok", "OK", "enabled"),)
        self.error_message = ""
        self.error_category = ""
        self.error_recovery = None
        self.shutdown_active = False
        self.system_shutdown_active = False
        self.restart_pending = False
        self.startup_restarting = False

        self._last_progress = None
        self._progress_floor = 0.0
        self._progress_source = None
        self._progress_start = (0.0, 0.0)
        self._m73_start_expiry = 0.0
        self._m73_active = False
        self._last_time = None
        self._last_print_controls_ready = None
        self._last_filename = None
        self._last_heat = None
        self.heat_return_page = ScreenPage.CONTROL_HOME
        self.move_return_page = ScreenPage.CONTROL_HOME
        self.filament_return_page = ScreenPage.MAIN_MENU
        self._last_dashboard = None
        self.last_job_path = self.print_history.latest_path()
        self.last_job_name = (os.path.basename(self.last_job_path)
                              if self.last_job_path else "NONE")

        self.printer.register_event_handler("klippy:ready", self._init)
        self.printer.register_event_handler("klippy:shutdown", self._shutdown)
        self.printer.register_event_handler("klippy:disconnect", self._disconnect)
        self.printer.register_event_handler(
            "mod_params:changed", self.update_notification.on_mod_params_changed)
        self.gcode.register_command(
            "FEATHER_ABORT", self.cmd_FEATHER_ABORT,
            desc=self.cmd_FEATHER_ABORT_help)
        self.gcode.register_immediate_command("FEATHER_ABORT")
        self.gcode.register_command(
            "_REFRESH_FEATHER_THEME", self.cmd_REFRESH_FEATHER_THEME)
        # Intentionally undocumented.  The implementation remains a cold
        # lazy feature until ACTION=RUN is explicitly requested on a printer.
        self.gcode.register_command("_FEATHER_UI_TEST", self.cmd_FEATHER_UI_TEST)
        # Start only once the reactor can accept callbacks posted by worker.
        self.reactor.register_callback(self._deferred_start_pre_ready_ui)

    def _deferred_start_pre_ready_ui(self, eventtime):
        self._start_pre_ready_ui()

    def _release_boot_screen(self):
        if (not getattr(self, "boot_screen_held", False)
                or os.path.exists(FORGE_X_SCREEN_BUSY_PATH)):
            return False
        self.boot_screen_held = False
        self.renderer.release_output()
        renderer_started = self._ensure_renderer_started()
        if not renderer_started:
            self.renderer.clear_display("boot-handoff")
        if self.renderer.output_frozen:
            return True
        if self.print_state == PrintState.INACTIVE and not self.error_message:
            self._render_startup_modal()
        else:
            self._show_page(self.page)
        return True

    def _lookup_mod_params_before_ready(self):
        """Return already-loaded settings without waiting for klippy:ready."""
        params = getattr(self, "params", None)
        if params is not None:
            return params
        params = self.printer.lookup_object("mod_params", None)
        if params is not None:
            # Config objects are fully constructed before reactor callbacks run,
            # even though Klipper has not emitted klippy:ready yet. Keep the
            # same object for the regular ready path and all later restarts.
            self.params = params
        return params

    def _apply_configured_theme(self, refresh_catalog=False):
        """Select the persisted theme before any framebuffer output."""
        if refresh_catalog:
            self.renderer.ensure_user_theme_directory()
            self.renderer.reload_themes()
        params = self._lookup_mod_params_before_ready()
        variables = (getattr(params, "variables", {})
                     if params is not None else {})
        return self.renderer.set_theme(
            variables.get("feather_theme", "DEFAULT"))

    def _renderer_event_fd_changed(self, old_fd, new_fd):
        if self.event_handle is not None:
            try:
                self.reactor.unregister_fd(self.event_handle)
            except Exception:
                logging.exception(
                    "[feather_screen] unable to unregister old touch FIFO")
                raise
            self.event_handle = None
        self.event_partial = ""
        if new_fd is not None:
            self.event_handle = self.reactor.register_fd(
                new_fd, self._process_touch_events)

    def _notify_features(self, hook, *args):
        manager = getattr(self, "feature_manager", None)
        notify = None if manager is None else getattr(manager, "notify", None)
        if notify is not None:
            notify(hook, *args)

    def _renderer_restarted(self):
        if self.renderer.output_frozen:
            return
        self._notify_features("on_renderer_restarted")
        try:
            if self.print_state == PrintState.INACTIVE:
                self._render_startup_modal()
            else:
                self._show_page(self.page)
        except Exception:
            logging.exception(
                "[feather_screen] unable to redraw after typer restart")

    def _ensure_renderer_started(self):
        if self.renderer.active:
            return False
        if getattr(self.renderer, "_worker", None) is not None:
            return False
        if not hasattr(self.renderer, "start"):
            return False
        self.renderer.start()
        return True

    def _start_pre_ready_ui(self, restarting=None):
        if restarting is not None:
            self.startup_restarting = bool(restarting)
        try:
            # The worker's first full-screen clear and the startup modal must
            # use the persisted theme. Applying it after renderer.start() leaves
            # the pre-ready UI on the fallback palette until klippy:ready.
            self._apply_configured_theme()
            self._enable_backlight()
            self._ensure_renderer_started()
            self._render_startup_modal()
        except Exception:
            logging.exception("[feather_screen] unable to draw startup modal")
        if self.startup_timer is None:
            self.startup_timer = self.reactor.register_timer(
                self._startup_tick, self.reactor.NOW)

    def _startup_tick(self, eventtime):
        if (getattr(self, "boot_screen_held", False)
                and not self._release_boot_screen()):
            return eventtime + STARTUP_ANIMATION_PERIOD
        if self.print_state != PrintState.INACTIVE or self.error_message:
            self.startup_timer = None
            return self.reactor.NEVER
        message, category = self.printer.get_state_message()
        if str(category).lower() in ("error", "shutdown", "disconnect"):
            self.startup_timer = None
            self._show_error(message or "Klipper is not ready", category)
            return self.reactor.NEVER
        try:
            restarted = self._ensure_renderer_started()
            self.startup_phase = (self.startup_phase + 1) % 4
            if restarted:
                self._render_startup_modal()
            else:
                pulse = self.renderer.startup_pulse(self.startup_phase)
                send_animation = getattr(
                    self.renderer, "send_animation", None)
                if send_animation is None:
                    self.renderer.send(pulse)
                else:
                    send_animation(pulse, "startup-pulse")
        except Exception:
            logging.exception("[feather_screen] startup animation failed")
        return eventtime + STARTUP_ANIMATION_PERIOD

    def _render_startup_modal(self):
        detail = ("RESTART IN PROGRESS - DISPLAY MAY PAUSE"
                  if self.startup_restarting
                  else "INITIALIZING PRINTER SERVICES")
        self.renderer.startup_modal(
            "INITIALIZING KLIPPER", detail, self.startup_phase,
            critical=self.startup_restarting)

    def _stop_startup_animation(self):
        if self.startup_timer is None:
            return
        try:
            self.reactor.unregister_timer(self.startup_timer)
        except Exception:
            pass
        self.startup_timer = None

    def _init(self):
        if getattr(self, "file_scan_worker", None) is None:
            self.file_scan_worker = FileScanWorker(
                self.file_scan_schedule_async)
        self.file_entry_cache.clear()
        self.file_entry_loaded_at.clear()
        self.file_scan_loading = False
        self.file_scan_source = None
        self.shutdown_active = False
        self.system_shutdown_active = False
        self.restart_pending = False
        self.startup_restarting = False
        self.error_message = ""
        self.error_category = ""
        self.error_recovery = None
        self.renderer.thaw_output()
        self.params = self.printer.lookup_object("mod_params")
        self.extruder = self.printer.lookup_object("extruder")
        self.heater_bed = self.printer.lookup_object("heater_bed")
        catalog = load_material_catalog(
            self.printer, self.extruder, self.heater_bed)
        self.heating_materials = catalog.heating_materials
        self.heating_profiles = catalog.heating_profiles
        self.cold_pull_materials = catalog.cold_pull_materials
        self.cold_pull_profiles = catalog.cold_pull_profiles
        self._enable_backlight()
        self._set_backlight(self._setting("backlight", 100))
        # Refresh user files at ready, but keep theme selection centralized so
        # pre-ready loading, firmware restart, and the normal UI share exactly
        # the same persisted setting contract.
        self._apply_configured_theme(refresh_catalog=True)
        self.filament_material = self._current_material()
        self.toolhead = self.printer.lookup_object("toolhead")
        self.input_shaper = self.printer.lookup_object("input_shaper", None)
        self.motion_report = self.printer.lookup_object("motion_report", None)
        self.idle_timeout = self.printer.lookup_object("idle_timeout")
        self.pause_resume = self.printer.lookup_object("pause_resume")
        self.display_status = self.printer.lookup_object("display_status")
        self._m73_start_expiry = float(
            getattr(self.display_status, "expire_progress", 0.0) or 0.0)
        self.print_stats = self.printer.lookup_object("print_stats")
        self.virtual_sdcard = self.printer.lookup_object("virtual_sdcard")
        self.usb_storage = UsbStorageMonitor(
            self.virtual_sdcard.sdcard_dirname, self.reactor)
        self.gcode_move = self.printer.lookup_object("gcode_move")
        self.temperature_wait = self.printer.lookup_object(
            "gcode_macro _WAIT_TEMPERATURE", None)
        self.operation_context = self.printer.lookup_object(
            "operation_context", None)
        self._last_operation_revision = -1
        self.start_print_macro = self.printer.lookup_object(
            "gcode_macro _START_PRINT", None)
        self.cancel_print_macro = self.printer.lookup_object(
            "gcode_macro CANCEL_PRINT", None)
        self.bed_mesh = self.printer.lookup_object("bed_mesh", None)
        self.probe = self.printer.lookup_object("probe")
        self.weight_sensor = self.printer.lookup_object(
            "temperature_sensor weightValue", None)
        self.chamber_light = self.printer.lookup_object(
            "led chamber_light", None)
        # FlashForge exposes the part-cooling fan as a named generic fan.
        self.fan = self.printer.lookup_object("fan_generic fanM106", None)
        self.filament_sensor = self.printer.lookup_object(
            "filament_switch_sensor e0_sensor", None)
        self.resurrection = self.printer.lookup_object("resurrection", None)
        self.gcode.register_output_handler(self._handle_gcode_output)
        self._create_joystick_planner()
        self.joystick_stream = joystick_motion.LowLatencyToolheadStream(
            self.toolhead, self.input_shaper)
        self.joystick_timer = self.reactor.register_timer(
            self._joystick_tick, self.reactor.NEVER)

        self._ensure_renderer_started()
        self._stop_startup_animation()
        self.print_state = PrintState.IDLE
        self.recovery_status = (self.resurrection.get_status(self.reactor.monotonic())
                                if self.resurrection is not None else None)
        if self.recovery_status and self.recovery_status.get("available"):
            self._show_page(ScreenPage.RECOVERY_PROMPT)
        else:
            self._show_page(ScreenPage.IDLE_HOME)
        self.update_notification.start()
        self._initialize_network_monitoring()
        self.timer = self.reactor.register_timer(self._update, self.reactor.NOW)

    def _shutdown(self):
        # invoke_shutdown() calls this synchronously. Stop every producer and
        # discard its queued output before submitting the one final screen.
        if getattr(self, "system_shutdown_active", False):
            return
        self.shutdown_active = True
        self._deactivate_components()
        if self.renderer.active:
            self.renderer.discard_pending_output()
            self.renderer.thaw_output()
            msg, _category = self.printer.get_state_message()
            message = msg if str(msg).strip() else "Printer is shutdown"
            self._show_error(message, "shutdown", "firmware_restart")
            self.renderer.freeze_output()

    def _disconnect(self):
        if (self.shutdown_active
                or getattr(self, "system_shutdown_active", False)):
            return
        self._deactivate_components()
        if self.restart_pending:
            return
        if self.renderer.active:
            self.renderer.discard_pending_output()
            self.renderer.thaw_output()
            self._show_error(
                "Klipper disconnected", "disconnect", recovery=None)
            self.renderer.freeze_output()

    def _deactivate_components(self):
        # Suppress any final ToolHead flush after the MCU has already stopped.
        self.print_state = PrintState.DESTROYED
        manager = getattr(self, "feature_manager", None)
        if manager is not None:
            manager.deactivate()
        safety = getattr(self, "safety", None)
        if safety is not None:
            safety.reset()
        update_notification = getattr(self, "update_notification", None)
        if update_notification is not None:
            update_notification.stop()
        self._stop_startup_animation()
        self._stop_joystick()
        self.print_state = PrintState.INACTIVE
        if self.joystick_timer is not None:
            self.reactor.unregister_timer(self.joystick_timer)
            self.joystick_timer = None
        if self.timer is not None:
            self.reactor.unregister_timer(self.timer)
        self.timer = None
        self._stop_network_client()
        if self.usb_storage is not None:
            self.usb_storage.stop()
            self.usb_storage = None
        file_scan_worker = getattr(self, "file_scan_worker", None)
        if file_scan_worker is not None:
            self.file_scan_token = getattr(self, "file_scan_token", 0) + 1
            self.file_scan_loading = False
            file_scan_worker.stop()
            self.file_scan_worker = None
        self.pending_action = None
        self.cancel_requested = False
        self.cancel_waiting_for_heat = False
        self.cancel_mode = None
        self.operation_cancel_on_accept = None
        self.operation_cancel_on_clear = None
        self.operation_cancel_request_id = None
        self.operation_cancel_target_name = None
        self.operation_cancel_target_mode = None
        self.touch_feedback_pending = False
        self.busy_message = None
        self.toast_until = 0.0
        self.toast_message = ""
        self.action_prompt = None
        self.action_prompt_visible = False
        self.action_prompt_page = 0
        wait = getattr(self, "temperature_wait", None)
        if wait is not None:
            wait.variables = dict(getattr(wait, "variables", {}))
            wait.variables["active"] = False
            wait.variables["cancel"] = True

    def get_status(self, eventtime):
        status = self.renderer.get_status()
        operation = self._operation_context_status(eventtime)
        status.update({
            "page": getattr(self.page, "name", str(self.page)),
            "generation": self.renderer.generation,
            "output_frozen": self.renderer.output_frozen,
            "touch_available": getattr(self, "touch_available", None),
            "touch_warning_visible": getattr(
                self, "touch_warning_visible", False),
            "context_path": operation["context_path"],
            "context_types": operation["context_types"],
            "current_state": operation["current_state"],
            "cancel_available": operation["cancel_available"],
            "cancel_pending": operation["cancel_pending"],
            "cancel_request_id": operation["cancel_request_id"],
            "cancel_target_type": operation["cancel_target_type"],
            "cancel_target_name": operation["cancel_target_name"],
            "cancel_target_mode": operation["cancel_target_mode"],
            "cancel_blocker_type": operation["cancel_blocker_type"],
            "cancel_blocker_name": operation["cancel_blocker_name"],
            "operation_revision": operation["revision"],
        })
        manager = getattr(self, "feature_manager", None)
        ui_test = None if manager is None else manager.peek("ui_test")
        if ui_test is not None:
            status["ui_test"] = ui_test.get_status()
        return status

    def cmd_FEATHER_UI_TEST(self, gcmd):
        """Route the hidden on-printer test command without eager imports."""
        action = str(gcmd.get("ACTION", "STATUS")).strip().upper()
        feature = self.feature_manager.peek("ui_test")
        if action == "STATUS":
            if feature is None:
                gcmd.respond_info("Feather UI test: idle (feature not loaded)")
            else:
                feature.respond_status(gcmd)
            return
        if action == "ABORT":
            if feature is None:
                gcmd.respond_info("Feather UI test: nothing to abort")
            else:
                feature.abort(gcmd)
            return
        if action != "RUN":
            raise gcmd.error("Unknown Feather UI test ACTION=%s" % action)
        try:
            feature = self.feature_manager.get("ui_test")
            feature.run(
                gcmd, str(gcmd.get("SUITE", "FULL")).strip().upper(),
                str(gcmd.get("MATERIAL", "")).strip(),
                int(gcmd.get_int("CONFIRM", 0)),
                str(gcmd.get("CASES", "")).strip(),
                str(gcmd.get("CAPTURE_INTERVAL", "0")).strip())
        except Exception as exc:
            raise gcmd.error(str(exc))

    def cmd_REFRESH_FEATHER_THEME(self, gcmd):
        """Reload user themes and redraw the configured palette."""
        self._require_idle()
        name = normalize_theme_name(self._setting("feather_theme", "DEFAULT"))
        self.renderer.reload_user_themes()
        if name not in self.renderer.theme_names():
            raise gcmd.error("Theme %s is unavailable or invalid" % name)
        self.renderer.set_theme(name)
        self._show_page(self.page)
        gcmd.respond_info("Feather theme refreshed: %s" % name)

    def _process_touch_events(self, eventtime):
        try:
            data = os.read(self.renderer.event_fd, 4096).decode("ascii")
        except OSError as exc:
            logging.warning("[feather_screen] touch read failed: %s", exc)
            return
        except UnicodeDecodeError as exc:
            logging.warning("[feather_screen] invalid touch event data: %s", exc)
            return
        lines = data.split("\n")
        lines[0] = self.event_partial + lines[0]
        self.event_partial = lines.pop()
        if len(self.event_partial) > MAX_TOUCH_EVENT:
            logging.warning("[feather_screen] oversized partial touch event discarded")
            self.event_partial = ""
        for line in lines:
            receipt = parse_render_receipt(line)
            if receipt is not None:
                self._notify_features(
                    "on_render_receipt", receipt, eventtime)
            elif line.startswith("render "):
                logging.warning(
                    "[feather_screen] invalid render receipt discarded: %r",
                    line[:MAX_TOUCH_EVENT])
            elif line == "touch-device connected":
                self._handle_touch_device_status(True)
            elif line == "touch-device unavailable":
                self._handle_touch_device_status(False)
            elif line.startswith("touch-device "):
                logging.warning(
                    "[feather_screen] invalid touch device status: %r",
                    line[:MAX_TOUCH_EVENT])
            elif line.startswith("touch "):
                self._handle_continuous_touch(line)
            elif line.startswith("tap "):
                now = self.reactor.monotonic()
                idle_for = max(0.0, now - self.last_touch_time)
                raw_action = line[4:].strip()
                decode = getattr(self.renderer, "decode_action", lambda value: value)
                action = decode(raw_action)
                logging.info(
                    "[feather_screen] touch action=%s page=%s dimmed=%s "
                    "idle=%.1fs command_depth=%d pending=%s",
                    action if action is not None else raw_action,
                    self.page.name, self.dimmed, idle_for,
                    getattr(self, "command_depth", 0), self.pending_action)
                self.last_touch_time = now
                # A normal tap both wakes the panel and activates its target.
                # Typer has already delivered a complete, current-generation
                # hitbox event, so discarding it only forces a second tap.
                self._wake_if_dimmed()
                if action is not None:
                    self._handle_touch_action(action)
                else:
                    logging.info("[feather_screen] stale touch ignored: %s", raw_action)

    def _show_touch_unavailable(self):
        if (self.boot_screen_held
                or self.touch_available is not False
                or self.touch_warning_visible
                or self.system_shutdown_active
                or not self.renderer.touch_warning_allowed):
            return False
        was_frozen = self.renderer.output_frozen
        if was_frozen:
            self.renderer.thaw_output()
        self.renderer.touch_unavailable_modal()
        self.renderer.freeze_output()
        self.touch_warning_restore_frozen = was_frozen
        self.touch_warning_visible = True
        return True

    def _handle_touch_device_status(self, available):
        available = bool(available)
        previous = getattr(self, "touch_available", None)
        self.touch_available = available
        if previous != available:
            logging.info(
                "[feather_screen] touch device %s",
                "connected" if available else "unavailable")
        if getattr(self, "system_shutdown_active", False):
            return
        if not available:
            self._show_touch_unavailable()
            return
        if not getattr(self, "touch_warning_visible", False):
            return

        restore_frozen = getattr(
            self, "touch_warning_restore_frozen", False)
        self.touch_warning_visible = False
        self.touch_warning_restore_frozen = False
        self.renderer.thaw_output()
        self._show_page(self.page)
        if restore_frozen:
            self.renderer.freeze_output()

    def _handle_continuous_touch(self, line):
        fields = line.split()
        if len(fields) != 5 or fields[2] not in ("begin", "move", "end"):
            logging.warning("[feather_screen] invalid continuous touch event: %r",
                            line[:MAX_TOUCH_EVENT])
            return
        raw_action, phase = fields[1], fields[2]
        try:
            x = max(0, min(799, int(fields[3])))
            y = max(0, min(479, int(fields[4])))
        except ValueError:
            logging.warning("[feather_screen] invalid continuous coordinates")
            return
        now = self.reactor.monotonic()
        self.last_touch_time = now

        if raw_action == self.joystick_suppressed:
            if phase == "end":
                self.joystick_suppressed = None
            return
        decode = getattr(self.renderer, "decode_action", lambda value: value)
        action = decode(raw_action)
        semantic_action = (
            None if action is None else self._resolve_semantic_ui_action(action))
        joystick_axis = None
        if isinstance(semantic_action, Command):
            if semantic_action.key == move_ui.MoveCommand.JOYSTICK_XY:
                joystick_axis = "xy"
            elif semantic_action.key == move_ui.MoveCommand.JOYSTICK_Z:
                joystick_axis = "z"
        if phase == "begin":
            logging.info("[feather_screen] continuous begin action=%s x=%d y=%d",
                         action if action is not None else raw_action, x, y)
            if self._wake_if_dimmed():
                self.joystick_suppressed = raw_action
                return
            if (joystick_axis == "xy"
                    and getattr(self, "move_caution_signature",
                                (False, None))[0]):
                self.joystick_suppressed = raw_action
                return
            if (joystick_axis is None
                    or self.page != ScreenPage.CONTROL_MOVE
                    or self.move_mode != "joystick"
                    or self.print_state != PrintState.IDLE
                    or self.command_depth > 0):
                self.joystick_suppressed = raw_action
                return
            homed = str(self.toolhead.get_status(now).get("homed_axes", ""))
            required = joystick_axis
            if any(axis not in homed for axis in required):
                self.joystick_suppressed = raw_action
                self._toast("HOME %s BEFORE MOVING" % required.upper())
                return
            self.joystick_action = action
        elif action is None or action != self.joystick_action:
            return

        if phase == "end":
            logging.info("[feather_screen] continuous end action=%s", action)
            self.joystick.release()
            self.joystick_action = None
            self.joystick_cursor = None
        elif joystick_axis == "xy":
            self.joystick.set_xy(
                x, y, now, JOYSTICK_XY_CENTER[0], JOYSTICK_XY_CENTER[1],
                JOYSTICK_XY_RADIUS)
            self.joystick_cursor = (action, x, y)
        else:
            self.joystick.set_z(
                y, now, JOYSTICK_Z_CENTER[1], JOYSTICK_Z_RADIUS)
            self.joystick_cursor = (action, JOYSTICK_Z_CENTER[0], y)
        self._start_joystick_timer()
        self._update_joystick_feedback(now, force=phase in ("begin", "end"))

    def _handle_touch_action(self, action):
        # The renderer's background hitbox makes empty-area taps observable.
        # _process_touch_events() has already refreshed last_touch_time and
        # restored the configured brightness, so no page action is required.
        if action == "global.wake":
            return
        if action == DismissToast().wire_id:
            self._hide_toast()
            return
        # Emergency actions and page-owned cancellation entry points must not
        # wait for an active G-code dispatcher mutex.
        if action == "global.abort":
            if not self._safety_decision().visible:
                logging.info(
                    "[feather_screen] ignored inactive emergency stop")
                return
            logging.warning(
                "[feather_screen] immediate emergency stop requested page=%s",
                self.page.name)
            self._run_immediate_command("M112")
            return
        if (action == "operation.cancel.force"
                and self.page == ScreenPage.CANCEL_CONFIRM
                and (getattr(self, "cancel_mode", None) == "not_cancelable"
                     or getattr(self, "cancel_mode", None) == "pending")):
            logging.warning(
                "[feather_screen] confirmed immediate abort from operation "
                "cancellation dialog")
            self._run_immediate_command("M112")
            return
        if (action == "operation.cancel.continue"
                and self.page == ScreenPage.CANCEL_CONFIRM
                and getattr(self, "cancel_mode", None) == "pending"):
            self._handle_operation_cancel_action(action)
            return
        if (action == "coldpull.cancel"
                and self.page == ScreenPage.ACTION_PROMPT
                and self._action_prompt_is_cold_pull()
                and "cold_pull" in self._operation_context_status().get(
                    "context_types", ())):
            self._open_operation_cancel(ScreenPage.ACTION_PROMPT)
            return
        manager = getattr(self, "feature_manager", None)
        if (manager is not None and
                manager.handle_immediate_action(self.page, action)):
            logging.info(
                "[feather_screen] immediate feature action=%s page=%s",
                action, self.page.name)
            return
        if (manager is None and action == "cal.cancel"
                and self.page == ScreenPage.CALIBRATION_PROGRESS
                and getattr(self, "calibration_kind", None) in (
                    "screws", "mesh", "z")):
            self._open_calibration_cancel()
            return
        if ((manager is not None and manager.input_blocked) or
                (manager is None and
                 getattr(self, "mod_update_pending", False))):
            logging.info(
                "[feather_screen] touch ignored while input is blocked: %s",
                action)
            return
        self._start_touch_action(action)

    def _start_touch_action(self, action):
        """Apply normal button feedback, then dispatch one UI action."""
        if self._blocking_operation_active():
            logging.info(
                "[feather_screen] touch ignored while blocking operation "
                "is active: action=%s operation=%s",
                action, self.busy_message)
            return
        busy_allowed = action in (
            "print.cancel", "operation.cancel.confirm",
            "operation.cancel.back", "operation.cancel.continue",
            "operation.cancel.force")
        if (self.page == ScreenPage.CANCEL_CONFIRM and
                action == "operation.cancel.back"):
            busy_allowed = True
        if getattr(self, "command_depth", 0) > 0 and not busy_allowed:
            logging.info("[feather_screen] touch ignored while command is active: %s",
                         action)
            notice = getattr(self.renderer, "busy_notice", None)
            if notice is not None:
                notice("PLEASE WAIT")
            return
        if getattr(self, "touch_feedback_pending", False):
            logging.info("[feather_screen] touch ignored during visual feedback: %s",
                         action)
            return
        flash = getattr(self.renderer, "flash_button", None)
        if flash is None or not flash(action):
            self._dispatch_action(action)
            return
        self.touch_feedback_pending = True
        page = self.page
        generation = getattr(self.renderer, "generation", None)
        self.reactor.register_callback(
            lambda eventtime, tap=action, source_page=page, token=generation:
            self._finish_touch_action(eventtime, tap, source_page, token),
            self.reactor.monotonic() + 0.08)

    def _finish_touch_action(self, eventtime, action, source_page=None,
                             generation=None):
        current_generation = getattr(self.renderer, "generation", None)
        if source_page is not None and self.page != source_page:
            self.touch_feedback_pending = False
            return
        # A redraw of the same page may legitimately occur while the 80 ms
        # pressed-state flash is visible (status, temperature, or phase
        # update).  Do not paint the stale button over the new generation, but
        # never discard the user's action merely because that redraw happened.
        if generation is None or current_generation == generation:
            restore = getattr(self.renderer, "restore_button", None)
            if restore is not None:
                restore(action)
        # Release the visual-feedback lock before dispatch. G-code is already
        # serialized through run_script(), while generation-tagged hitboxes
        # reject bounce events belonging to a page that has been replaced.
        self.touch_feedback_pending = False
        self._dispatch_action(action)

    def _wake_if_dimmed(self):
        if not self.dimmed:
            return False
        brightness = self._setting("backlight", 100)
        logging.info("[feather_screen] waking display brightness=%s", brightness)
        self.dimmed = False
        self._set_backlight(brightness)
        return True

    def _update_eco_backlight(self, eventtime):
        eco_enabled = self._setting("display_eco", True)
        if not eco_enabled and self.dimmed:
            self._wake_if_dimmed()
        elif (eco_enabled and not self.dimmed
              and eventtime - self.last_touch_time >= self.dim_timeout):
            self.dimmed = True
            logging.info("[feather_screen] dimming display after %.1fs idle",
                         eventtime - self.last_touch_time)
            self._set_backlight(self._setting("backlight_eco", 10))

    def _restart_renderer(self, eventtime):
        # Lifecycle and backoff are worker-owned. The reactor only signals.
        return self.renderer.restart()

    def _dispatch_action(self, action):
        if self.print_state == PrintState.DESTROYED:
            return
        # Recheck here as well as in _handle_touch_action(). A button may have
        # entered its 80 ms feedback delay immediately before a blocking
        # operation replaced the page with a loader.
        if self._blocking_operation_active():
            logging.info(
                "[feather_screen] delayed action ignored while blocking "
                "operation is active: action=%s operation=%s",
                action, self.busy_message)
            return
        manager = getattr(self, "feature_manager", None)
        blocks_action = (None if manager is None
                         else getattr(manager, "blocks_action", None))
        if blocks_action is not None and blocks_action(action):
            logging.warning(
                "[feather_screen] feature blocked action=%s", action)
            return
        owner = None
        if manager is not None and manager.owner_name(self.page) is not None:
            try:
                owner, semantic_action = manager.resolve_semantic_action(
                    self.page, action)
            except FeatureLoadError as exc:
                self._show_message(str(exc), self.previous_page)
                return
        else:
            semantic_action = self._resolve_semantic_ui_action(action)
        now = self.reactor.monotonic()
        if now - self.last_action_time < ACTION_DEBOUNCE:
            logging.info("[feather_screen] debounced action=%s", action)
            return
        if self.pending_action is not None and action in (
                "print.pause", "print.resume", "operation.cancel.confirm"):
            logging.info("[feather_screen] action already in progress=%s", action)
            return
        allowed = (owner.allows_action(self.page, action)
                   if owner is not None
                   else self._action_allowed(self.page, action))
        if semantic_action is None and not allowed:
            logging.info("[feather_screen] ignored action=%s page=%s",
                         action, self.page.name)
            return
        self.last_action_time = now
        logging.info("[feather_screen] action=%s page=%s", action, self.page.name)

        try:
            if (action in IDLE_ONLY_NAVIGATION_ACTIONS
                    and self._safety_print_active(now)):
                logging.info(
                    "[feather_screen] navigation blocked during print: %s",
                    action)
                self._toast("UNAVAILABLE DURING PRINT")
                return
            if semantic_action is not None:
                if owner is not None:
                    owner.handle_semantic_action(self.page, semantic_action)
                else:
                    self._dispatch_semantic_ui_action(semantic_action)
            elif action == "nav.back":
                self._go_back()
            elif action == "nav.home":
                self.home_during_print = self.print_state in (
                    PrintState.PREPARING, PrintState.PRINTING,
                    PrintState.PAUSED)
                self._show_page(ScreenPage.IDLE_HOME)
            elif action == "nav.menu":
                self._show_page(ScreenPage.MAIN_MENU)
            elif action == "nav.files":
                self.file_page = 0
                self.file_source = "internal"
                self._expire_file_entries_if_stale("internal")
                self._show_page(ScreenPage.FILE_BROWSER)
            elif action == "nav.control":
                self._require_idle()
                self._show_page(ScreenPage.CONTROL_HOME)
            elif action == "nav.move":
                self._require_idle()
                self.move_return_page = self.page
                self._cancel_delayed_tasks()
                self._show_page(ScreenPage.CONTROL_MOVE)
            elif action == "nav.heat":
                self.heat_return_page = self.page
                self._cancel_delayed_tasks()
                self._show_page(ScreenPage.CONTROL_HEAT)
            elif action == "nav.filament":
                self._open_filament(False)
            elif action == "nav.calibration":
                self._require_idle()
                if manager is not None:
                    manager.get("calibration").calibration_page = 0
                self._show_page(ScreenPage.CALIBRATION_HOME)
            elif action == "nav.settings":
                self._require_idle()
                self._show_page(ScreenPage.SETTINGS)
            elif action == "nav.network":
                self.network_parent_page = self.page
                self._open_network_page()
            elif action == "nav.job":
                stats = self.print_stats.get_status(
                    self.reactor.monotonic()).get("state")
                if stats in ("printing", "paused"):
                    self.home_during_print = False
                    self._show_page(self.page_for_print_state())
                else:
                    self.file_page = 0
                    self.file_source = "internal"
                    self._expire_file_entries_if_stale("internal")
                    self._show_page(ScreenPage.FILE_BROWSER)
            elif owner is not None:
                if not owner.handle_action(self.page, action):
                    raise KeyError(
                        "Feature %s did not handle action %s" %
                        (owner.name, action))
            elif action.startswith("file."):
                self._handle_file_action(action)
            elif action.startswith("operation.cancel."):
                self._handle_operation_cancel_action(action)
            elif action.startswith("print."):
                self._handle_print_action(action)
            elif action.startswith("heat."):
                self._handle_heat_action(action)
            elif action.startswith("recovery."):
                self._handle_recovery_action(action)
            elif action.startswith("prompt."):
                self._handle_action_prompt_action(action)
            elif action.startswith("error."):
                self._handle_error_action(action)
            elif action.startswith("update."):
                self.update_notification.handle_action(action)
            elif (action.startswith("net.")
                  or (self.page == ScreenPage.WIFI_PASSWORD
                      and is_keyboard_action(action))):
                self._handle_network_action(action)
            elif action == "mesh.save":
                self._require_idle()
                mesh_status = (self.bed_mesh.get_status(now)
                               if getattr(self, "bed_mesh", None) is not None
                               else {})
                if mesh_status.get("profile_name") != "auto":
                    raise RuntimeError("The auto bed mesh is no longer active")
                self._restart_klipper("SAVE_CONFIG")
            elif action == "message.ok":
                self._show_page(self.message_return)
        except Exception as exc:
            logging.exception("[feather_screen] action failed: %s", action)
            self._show_message(str(exc), self.page)

    def _action_allowed(self, page, action):
        if page == ScreenPage.MESSAGE and action != "message.ok":
            return any(
                item[0] == action
                for item in getattr(self, "message_actions", ()))

        if action in EXACT_ACTIONS.get(page, ()):
            return True
        return ((page == ScreenPage.FILE_BROWSER and action.startswith("file.item"))
                or (page == ScreenPage.WIFI_SCAN and action.startswith("net.item"))
                or (page == ScreenPage.WIFI_PASSWORD and is_keyboard_action(action))
                or (page == ScreenPage.ACTION_PROMPT
                    and action.startswith("prompt.button.")))

    def _show_page(self, page):
        if not isinstance(page, ScreenPage):
            raise TypeError("screen page must be a ScreenPage member")
        if (page != ScreenPage.ERROR
                and getattr(
                    getattr(self, "renderer", None),
                    "output_frozen", False)):
            return
        # A complete surface repaint removes every visual toast and commits a
        # cleared overlay hitbox layer, so its controller state must not outlive
        # that surface generation.
        self.toast_until = 0.0
        self.toast_message = ""
        manager = getattr(self, "feature_manager", None)
        feature = None
        if manager is not None and manager.owner_name(page) is not None:
            try:
                feature = manager.get_for_page(page)
            except FeatureLoadError as exc:
                logging.error("[feather_screen] feature page failed: %s", exc)
                self._show_message(str(exc), getattr(self, "page", ScreenPage.IDLE_HOME))
                return
        self._apply_safety_visibility(page)
        if (self.page == ScreenPage.CONTROL_MOVE
                and (page != ScreenPage.CONTROL_MOVE
                     or getattr(self, "joystick_action", None) is not None)):
            self._stop_joystick()
        old_page = self.page
        self.previous_page = old_page
        self.page = page
        self._notify_features("on_page_changed", old_page, page)
        if feature is not None:
            feature.render(page)
        elif page == ScreenPage.IDLE_HOME:
            self._render_home()
        elif page == ScreenPage.MAIN_MENU:
            self._render_main_menu()
        elif page == ScreenPage.CONTROL_HOME:
            self._render_control_home()
        elif page == ScreenPage.FILE_BROWSER:
            self._render_file_browser()
        elif page == ScreenPage.FILE_CONFIRM:
            self._render_file_confirm()
        elif page in (ScreenPage.PRINTING, ScreenPage.PAUSED):
            self._render_print_page()
        elif page == ScreenPage.CANCEL_CONFIRM:
            self._render_cancel_confirm()
        elif page == ScreenPage.CONTROL_MOVE:
            self._render_move()
        elif page == ScreenPage.CONTROL_HEAT:
            self._render_heat()
        elif page == ScreenPage.NETWORK_HOME:
            self._render_network_home()
        elif page == ScreenPage.WIFI_SCAN:
            self._render_wifi_scan()
        elif page == ScreenPage.WIFI_PASSWORD:
            self._render_keyboard()
        elif page == ScreenPage.NETWORK_PROGRESS:
            self._render_network_progress()
        elif page == ScreenPage.RECOVERY_PROMPT:
            self._render_recovery_prompt()
        elif page == ScreenPage.RECOVERY_CONFIRM:
            self._render_recovery_confirm()
        elif page == ScreenPage.ACTION_PROMPT:
            self._render_action_prompt()
        elif page == ScreenPage.MESSAGE:
            self._render_message()
        elif page == ScreenPage.ERROR:
            self._render_error()
        elif page == ScreenPage.UPDATE_NOTIFICATION:
            self.update_notification.render()
        self._show_touch_unavailable()
        update_notification = getattr(self, "update_notification", None)
        if update_notification is not None:
            update_notification.on_page_changed(old_page, page)

    def _go_back(self):
        manager = getattr(self, "feature_manager", None)
        if manager is not None:
            owner_name = manager.owner_name(self.page)
            if owner_name is not None:
                manager.get(owner_name).back(self.page)
                return
        if (self.page == ScreenPage.FILE_BROWSER
                and getattr(self, "file_source", "internal") == "usb"):
            self.file_source = "internal"
            self.file_page = 0
            self.selected_file = None
            self._show_page(ScreenPage.FILE_BROWSER)
        elif self.page == ScreenPage.FILE_CONFIRM:
            return_page = getattr(
                self, "file_confirm_return_page", ScreenPage.FILE_BROWSER)
            self.selected_file = None
            self.file_confirm_repeat = False
            self.file_confirm_rebuild_mesh = False
            self.file_confirm_auto_mesh = False
            self._show_page(return_page)
        elif self.page == ScreenPage.CONTROL_HOME:
            self._show_page(ScreenPage.MAIN_MENU)
        elif self.page == ScreenPage.NETWORK_HOME:
            self._show_page(getattr(
                self, "network_parent_page", ScreenPage.MAIN_MENU))
        elif self.page == ScreenPage.MAIN_MENU:
            self._show_page(ScreenPage.IDLE_HOME)
        elif self.page == ScreenPage.CONTROL_HEAT:
            self._show_page(getattr(
                self, "heat_return_page", ScreenPage.CONTROL_HOME))
        elif self.page == ScreenPage.CONTROL_MOVE:
            self._show_page(getattr(
                self, "move_return_page", ScreenPage.CONTROL_HOME))
        elif self.page == ScreenPage.RECOVERY_CONFIRM:
            self._show_page(ScreenPage.RECOVERY_PROMPT)
        elif self.page in (ScreenPage.WIFI_SCAN, ScreenPage.WIFI_PASSWORD):
            self._show_page(ScreenPage.NETWORK_HOME if self.page == ScreenPage.WIFI_SCAN
                            else ScreenPage.WIFI_SCAN)
        elif self.page == ScreenPage.CANCEL_CONFIRM:
            self._close_operation_cancel()
        else:
            self._show_page(ScreenPage.IDLE_HOME)


    def page_for_print_state(self):
        try:
            state = self.print_stats.get_status(
                self.reactor.monotonic()).get("state")
        except Exception:
            state = None
        if state == "paused":
            return ScreenPage.PAUSED
        if state == "printing":
            return ScreenPage.PRINTING
        if state in ("complete", "cancelled", "error", "standby"):
            return ScreenPage.IDLE_HOME
        return (ScreenPage.PAUSED if self.print_state == PrintState.PAUSED
                else ScreenPage.PRINTING)

    def _setting(self, key, default):
        params = getattr(self, "params", None)
        return params.variables.get(key, default) if params is not None else default

    def _cancel_delayed_tasks(self):
        self._run_immediate_command("_CANCEL_DELAYED_COMMANDS")

    def _normalize_material(self, value):
        material = str(value or "n/a").strip().upper().replace("/", "-")
        if material in ("N/A", "NONE", "UNKNOWN", ""):
            return "n/a"
        return material if material in self.heating_materials else "n/a"

    def _current_material(self):
        return self._normalize_material(self._setting("current_material", "n/a"))

    def _enable_backlight(self):
        try:
            with open("/dev/disp", "wb") as device:
                try:
                    fcntl.ioctl(device, DISP_LCD_BACKLIGHT_ENABLE, b"")
                except OSError as exc:
                    # This driver reports EPERM when enable is repeated while
                    # the backlight is already on. Brightness is still writable.
                    if exc.errno != errno.EPERM:
                        raise
            logging.info("[feather_screen] backlight enabled")
        except Exception:
            logging.exception("[feather_screen] backlight enable failed")

    def _set_backlight(self, value):
        value = max(1, min(100, int(value)))
        try:
            with open("/dev/disp", "wb") as device:
                hardware_value = int(1 + value * (255 / 100.0))
                payload = struct.pack("=LL", 0, hardware_value)
                fcntl.ioctl(device, DISP_LCD_SET_BRIGHTNESS, payload)
            logging.info("[feather_screen] backlight applied=%d%%", value)
        except Exception:
            logging.exception("[feather_screen] backlight update failed")

    def _temperature_wait_active(self):
        wait = getattr(self, "temperature_wait", None)
        return bool(wait is not None and
                    getattr(wait, "variables", {}).get("active", False))

    def _operation_context_status(self, eventtime=None):
        manager = getattr(self, "operation_context", None)
        if manager is None:
            return {
                "contexts": (), "context_path": (), "context_types": (),
                "current_state": None, "cancel_available": False,
                "cancel_pending": False, "cancel_request_id": None,
                "cancel_target_type": None, "cancel_target_name": None,
                "cancel_target_mode": None, "cancel_blocker_type": None,
                "cancel_blocker_name": None, "revision": 0,
            }
        if eventtime is None:
            eventtime = self.reactor.monotonic()
        status = manager.get_status(eventtime)
        return {
            "contexts": tuple(status.get("contexts", ())),
            "context_path": tuple(status.get("context_path", ())),
            "context_types": tuple(status.get("context_types", ())),
            "current_state": status.get("current_state"),
            "cancel_available": bool(status.get("cancel_available", False)),
            "cancel_pending": bool(status.get("cancel_pending", False)),
            "cancel_request_id": status.get("cancel_request_id"),
            "cancel_target_type": status.get("cancel_target_type"),
            "cancel_target_name": status.get("cancel_target_name"),
            "cancel_target_mode": status.get("cancel_target_mode"),
            "cancel_blocker_type": status.get("cancel_blocker_type"),
            "cancel_blocker_name": status.get("cancel_blocker_name"),
            "revision": int(status.get("revision", 0)),
        }

    def _operation_context_text(self, eventtime=None, status=None):
        if status is None:
            status = self._operation_context_status(eventtime)
        parts = [str(name).strip().upper()
                 for name in status["context_path"] if str(name).strip()]
        state = status["current_state"]
        if state is not None and str(state).strip():
            parts.append(str(state).strip().upper())
        return " -> ".join(parts)

    def _display_status_text(self, eventtime=None, status=None):
        if status is None:
            status = self._operation_context_status(eventtime)
        if "print" in status["context_types"]:
            operation = self._operation_context_text(status=status)
            if operation:
                return operation
        return {
            PrintState.PREPARING: "PREPARING",
            PrintState.PAUSED: "PAUSED",
            PrintState.PRINTING: "PRINTING",
        }.get(getattr(self, "print_state", None), "PRINTING")

    def _build_safety_registry(self):
        registry = SafetyRegistry(excluded_routes=(ScreenPage.IDLE_HOME,))
        registry.register_source("print-job", self._safety_print_active)
        registry.register_source("heaters", self._safety_heaters_active)
        registry.register_source(
            "temperature-wait",
            lambda _eventtime: self._temperature_wait_active())
        registry.register_source("motion", self._safety_motion_active)
        registry.register_source("joystick", self._safety_joystick_active)
        registry.register_source("features", self._safety_features_active)
        return registry

    def _ensure_safety_registry(self):
        registry = getattr(self, "safety", None)
        if registry is None:
            registry = self._build_safety_registry()
            self.safety = registry
        return registry

    def _safety_print_active(self, eventtime):
        if getattr(self, "print_state", None) in ACTIVE_PRINT_STATES:
            return True

        print_stats = getattr(self, "print_stats", None)
        if print_stats is not None:
            state = str(print_stats.get_status(eventtime).get(
                "state", "")).lower()
            if state in ("printing", "paused"):
                return True

        virtual_sdcard = getattr(self, "virtual_sdcard", None)
        if (virtual_sdcard is not None
                and callable(getattr(virtual_sdcard, "is_active", None))
                and virtual_sdcard.is_active()):
            return True
        return False

    def _update_notification_safe(self, eventtime):
        decision = self._ensure_safety_registry().evaluate(
            ScreenPage.UPDATE_NOTIFICATION, eventtime)
        return not decision.visible

    def _safety_heaters_active(self, eventtime):
        for heater in (getattr(self, "extruder", None),
                       getattr(self, "heater_bed", None)):
            if heater is None:
                continue
            target = float(heater.get_status(eventtime).get("target", 0.0))
            if target > 0.0:
                return True
        return False

    def _safety_motion_active(self, eventtime):
        report = getattr(self, "motion_report", None)
        if report is None:
            return False
        status = report.get_status(eventtime)
        velocity = abs(float(status.get("live_velocity", 0.0)))
        extruder_velocity = abs(float(
            status.get("live_extruder_velocity", 0.0)))
        return max(velocity, extruder_velocity) > 0.000001

    def _safety_joystick_active(self, eventtime):
        stream = getattr(self, "joystick_stream", None)
        return bool(getattr(stream, "active", False)
                    or getattr(self, "joystick_action", None) is not None)

    def _safety_features_active(self, eventtime):
        manager = getattr(self, "feature_manager", None)
        if manager is None:
            return False
        return bool(manager.safety_active_reasons(eventtime))

    def _homed_motion_available(self, eventtime):
        toolhead = getattr(self, "toolhead", None)
        if toolhead is None:
            return False
        try:
            homed = str(toolhead.get_status(eventtime).get(
                "homed_axes", "")).lower()
        except Exception:
            # Unknown homing state must not arm an otherwise idle movement
            # page. Actual operations remain covered by activity leases and
            # observable printer state.
            return False
        # Partial homing is useful and safe to expose: each movement action
        # still validates its own required axis before dispatch.
        return any(axis in homed for axis in "xyz")

    def _safety_armed_reasons(self, page, eventtime):
        reasons = []
        if page == ScreenPage.CONTROL_MOVE:
            if self._homed_motion_available(eventtime):
                reasons.append("homed-motion-controls")
        elif page in CORE_SAFETY_ARMED_PAGES:
            reasons.append("core-controls")
        manager = getattr(self, "feature_manager", None)
        if manager is not None:
            try:
                reasons.extend(manager.safety_armed_reasons(page, eventtime))
            except Exception:
                failures = getattr(self, "_feature_safety_failures", 0) + 1
                self._feature_safety_failures = failures
                if failures == 1 or failures % 60 == 0:
                    logging.exception(
                        "[feather_screen] feature safety policy failed "
                        "page=%s failures=%d",
                        getattr(page, "name", page), failures)
                # Policy failure is fail-safe just like a provider failure.
                reasons.append("feature-policy-error")
            else:
                self._feature_safety_failures = 0
        return tuple(reasons)

    def _safety_decision(self, page=None, eventtime=None):
        page = page if page is not None else getattr(
            self, "page", ScreenPage.IDLE_HOME)
        if eventtime is None:
            reactor = getattr(self, "reactor", None)
            eventtime = (reactor.monotonic()
                         if reactor is not None else time.monotonic())
        state = getattr(self, "print_state", PrintState.IDLE)
        renderer = getattr(self, "renderer", None)
        enabled = (state not in (PrintState.INACTIVE, PrintState.DESTROYED)
                   and not bool(getattr(renderer, "output_frozen", False)))
        return self._ensure_safety_registry().evaluate(
            page, eventtime,
            self._safety_armed_reasons(page, eventtime), enabled=enabled)

    def _apply_safety_visibility(self, page=None, eventtime=None):
        renderer = getattr(self, "renderer", None)
        setter = getattr(renderer, "set_header_action", None)
        if setter is None:
            return False
        page = page if page is not None else getattr(
            self, "page", ScreenPage.IDLE_HOME)
        decision = self._safety_decision(page, eventtime)
        action = "global.abort" if decision.visible else None
        if not setter(action, "ABORT", state="danger",
                      font="JetBrainsMono Bold 8pt"):
            return False
        logging.info(
            "[feather_screen] emergency action visible=%s page=%s reasons=%s",
            decision.visible, getattr(page, "name", page),
            ",".join(decision.reasons) or "none")
        return True

    def _refresh_emergency_stop(self, eventtime=None):
        if not self._apply_safety_visibility(eventtime=eventtime):
            return False
        # A full page render clears the old Typer hitbox as well as its
        # framebuffer. This is required when an operation ends; merely
        # painting over ABORT would leave an invisible M112 touch target.
        self._show_page(self.page)
        return True

    def _run_script(self, command, show_notice=True):
        """Serialize Feather G-code through Klipper's reactor mutex.

        run_script_from_command() bypasses the mutex and may recursively enter
        a yielding macro. run_script() serializes normal commands while still
        extracting patched immediate commands before acquiring the mutex.
        Serialization alone is not evidence of danger: callers which own a
        long-running operation must use _run_blocking_gcode() or an explicit
        safety activity lease.
        """
        outermost = getattr(self, "command_depth", 0) == 0
        reactor = getattr(self, "reactor", None)
        clock = reactor.monotonic if reactor is not None else time.monotonic
        started = clock()
        first_line = (str(command).strip().splitlines() or [""])[0]
        command_name = (first_line.split(None, 1) or ["UNKNOWN"])[0]
        self.command_depth = getattr(self, "command_depth", 0) + 1
        renderer = getattr(self, "renderer", None)
        try:
            if outermost:
                page = getattr(self, "page", "UNKNOWN")
                logging.info("[feather_screen] command start name=%s page=%s",
                             command_name, getattr(page, "name", page))
                if renderer is not None and show_notice:
                    notice = getattr(renderer, "busy_notice", None)
                    if notice is not None:
                        notice("KLIPPER BUSY")
                self._refresh_emergency_stop()
            runner = getattr(self.gcode, "run_script", None)
            if runner is not None:
                runner(command)
            else:
                # Lightweight host-test fakes expose only the older method.
                self.gcode.run_script_from_command(command)
        finally:
            self.command_depth = max(0, self.command_depth - 1)
            if outermost:
                logging.info("[feather_screen] command finish name=%s elapsed=%.3fs",
                             command_name, clock() - started)
                if renderer is not None and show_notice:
                    clear = getattr(renderer, "clear_busy_notice", None)
                    if clear is not None:
                        clear()
                self._refresh_emergency_stop()

    def _run_immediate_command(self, command):
        """Dispatch a patched immediate command without entering the G-code mutex.

        M108 interrupts a yielding temperature macro, M112 is the emergency
        stop, and FEATHER_ABORT is the external immediate bridge to the
        operation context manager. Normal UI cancellation calls the manager
        directly from the reactor and queues only a safe point when needed.
        _CANCEL_DELAYED_COMMANDS contains only timer updates and must run before
        manual control even when another command owns the mutex.
        """
        if command not in (
                "M108", "M112", "FEATHER_ABORT", "_CANCEL_DELAYED_COMMANDS"):
            raise ValueError("Unsupported immediate Feather command")
        self.gcode.run_script_from_command(command)

    def _blocking_operation_active(self):
        """Whether a loader owns the whole interactive surface.

        This is intentionally distinct from command_depth. Short serialized
        commands may keep normal page navigation, while a loader operation
        must reject every stale page action until its owning call completes.
        """
        return bool(getattr(self, "busy_message", None))

    def _page_paint_allowed(self, *pages):
        """Whether a periodic painter may still update one of `pages`.

        The output counterpart of _blocking_operation_active(): a loader owns
        the whole surface, so an incremental paint would land on top of it and
        be overdrawn by its next frame anyway.
        """
        return (self.page in pages
                and not self._blocking_operation_active())

    def _run_blocking_gcode(self, command, message):
        # Unit tests and early startup may not have a live renderer yet.
        renderer = getattr(self, "renderer", None)
        if renderer is None or not hasattr(self, "busy_message"):
            self._run_script(command)
            return
        page = self.page
        safety_lease = self._ensure_safety_registry().activity(
            "blocking-gcode")
        self.busy_message = message
        self.busy_phase = 0
        logging.info("[feather_screen] operation start label=%s page=%s",
                     message, page.name)
        try:
            self._refresh_emergency_stop()
            renderer.loader(message, 0)
            self._run_script(command)
        finally:
            self.busy_message = None
            safety_lease.release()
            logging.info("[feather_screen] operation finish label=%s page=%s",
                         message, self.page.name)
            if self.page == page and self.print_state != PrintState.DESTROYED:
                self._show_page(page)

    def _hide_toast(self, redraw=True):
        active = bool(self.toast_until or self.toast_message)
        self.toast_until = 0.0
        self.toast_message = ""
        if not active:
            return False

        if (redraw
                and getattr(self, "print_state", None) != PrintState.DESTROYED
                and getattr(self, "page", None) is not None):
            # TODO: Render toasts as transient visual overlays and restore only
            # their dirty rectangle from the base backbuffer instead of
            # redrawing the page.
            self._show_page(self.page)
        else:
            clear_hitbox = getattr(
                getattr(self, "renderer", None), "clear_toast_hitbox", None)
            if clear_hitbox is not None:
                clear_hitbox()
        return True

    def _toast(self, message):
        if self.toast_until or self.toast_message:
            self._hide_toast()
        self.toast_until = self.reactor.monotonic() + 2.0
        self.toast_message = str(message)
        self.renderer.toast(self.toast_message)

    def _show_message(self, message, return_page, actions=None):
        recovery = self._classify_error(message)
        if recovery is not None:
            self._show_error(message, "runtime", recovery)
            return
        self.message = str(message)
        self.message_return = return_page
        self.message_actions = (
            actions if actions is not None
            else (("message.ok", "OK", "enabled"),))
        self._show_page(ScreenPage.MESSAGE)

    def _render_message(self):
        save_mesh = any(
            action == "mesh.save"
            for action, _label, _state in self.message_actions)
        title = "Save bed mesh?" if save_mesh else "Message"
        commands = self.renderer.begin_page(title)
        commands += self.renderer.dialog(
            title, (),
            self.message_actions,
            x=90, y=95, width=620, height=300, tone="info")
        commands.append(self.renderer.text(
            400, 215, self.message, ThemeColor.TEXT, "JetBrainsMono 12pt", "center",
            "middle", max_width=584, max_height=108, wrap=True,
            truncate=True))
        self.renderer.send(commands)

    @staticmethod
    def _classify_error(message, category=""):
        text = str(message).upper()
        category = str(category).lower()
        # Klipper's state message is authoritative when it names the recovery
        # command. In particular, an MCU failure can be exposed with the
        # generic "error" category while still requiring FIRMWARE_RESTART.
        if "FIRMWARE_RESTART" in text or category == "shutdown":
            return "firmware_restart"
        if category == "error":
            return "restart"
        return None

    def _show_error(self, message, category="", recovery=None):
        # A shutdown/disconnect screen is frozen after its complete frame has
        # reached Typer.  A late exception from the interrupted operation may
        # report the same error again.  Calling begin_page() while output is
        # frozen would advance the renderer generation without replacing the
        # visible hitboxes, making the recovery button permanently stale.
        if (getattr(self, "page", None) == ScreenPage.ERROR
                and getattr(
                    getattr(self, "renderer", None),
                    "output_frozen", False)):
            logging.info(
                "[feather_screen] duplicate error ignored while error "
                "screen is frozen")
            return
        self.error_message = str(message).replace("\n", " ")
        self.error_category = str(category or "")
        self.error_recovery = (
            recovery if recovery is not None
            else self._classify_error(self.error_message, self.error_category))
        self._show_page(ScreenPage.ERROR)

    def _render_error(self):
        commands = self.renderer.begin_page("Klipper error")
        if self.error_recovery == "firmware_restart":
            advice = "Check the printer, then restart the MCU."
            buttons = (("error.firmware_restart",
                        "FIRMWARE RESTART", "danger"),)
            title = "MCU RESTART REQUIRED"
        elif self.error_recovery == "restart":
            advice = "Correct the issue, then restart Klipper."
            buttons = (("error.restart", "RESTART", "danger"),)
            title = "KLIPPER ERROR"
        else:
            advice = "Waiting for Klipper to reconnect."
            buttons = ()
            title = "KLIPPER IS NOT READY"
        commands += self.renderer.dialog(
            title, (), buttons,
            x=80, y=85, width=640, height=325, tone="danger")
        commands += [
            self.renderer.text(
                400, 200, self.error_message, ThemeColor.TEXT, "JetBrainsMono 8pt",
                "center", "middle", max_width=584, max_height=110, wrap=True,
                truncate=True),
            self.renderer.text(
                400, 285, advice, ThemeColor.TEXT, "JetBrainsMono 8pt", "center",
                "middle", max_width=584, truncate=True),
        ]
        self.renderer.prioritize_next_batch("critical", "error-screen")
        self.renderer.send(commands)

    def _handle_error_action(self, action):
        commands = {
            "error.restart": "RESTART",
            "error.firmware_restart": "FIRMWARE_RESTART",
        }
        command = commands.get(action)
        if command is None:
            return
        self._restart_klipper(command)

    def _restart_klipper(self, command):
        if not self._begin_restart_ui():
            return
        self._run_script(command, show_notice=False)

    def _begin_restart_ui(self):
        if self.restart_pending:
            return False
        self.renderer.thaw_output()
        self.error_message = ""
        self.error_category = ""
        self.error_recovery = None
        self.shutdown_active = False
        self.restart_pending = True
        self.startup_phase = 0
        if getattr(self, "timer", None) is not None:
            try:
                self.reactor.unregister_timer(self.timer)
            except Exception:
                pass
            self.timer = None
        self._start_pre_ready_ui(restarting=True)
        return True

    def _begin_system_shutdown(self):
        if getattr(self, "system_shutdown_active", False):
            return
        self.system_shutdown_active = True
        self.touch_warning_visible = False
        self.renderer.discard_pending_output()
        self.renderer.thaw_output()
        self.renderer.startup_modal(
            "FORGE-X", "SHUTTING DOWN", critical=True)
        self.renderer.freeze_output()
        logging.info("[feather_screen] Forge-X system shutdown started")

    def _update(self, eventtime):
        if self.print_state == PrintState.DESTROYED:
            # This is a direct reactor timer callback. Deactivation can yield
            # while components are being torn down, so a final timer dispatch
            # may observe DESTROYED before the timer is unregistered. Returning
            # None corrupts the reactor timer heap; park the callback instead.
            return self.reactor.NEVER
        try:
            self._release_boot_screen()
            waketime = self._update_cycle(eventtime)
        except Exception:
            failures = getattr(self, "_update_failures", 0) + 1
            self._update_failures = failures
            # A UI/status failure must never escape a reactor timer callback:
            # Klipper treats that as a host failure and shuts the printer down.
            # Avoid flooding the log for a persistent bad status value while
            # retaining periodic evidence that the UI still needs attention.
            if failures == 1 or failures % 60 == 0:
                page = getattr(getattr(self, "page", None), "name", "UNKNOWN")
                logging.exception(
                    "[feather_screen] periodic update failed page=%s "
                    "failures=%d", page, failures)
            return eventtime + REFRESH_TIME
        self._update_failures = 0
        return waketime

    def _update_cycle(self, eventtime):
        manager = getattr(self, "feature_manager", None)
        if ((manager is None or not manager.theme_update_blocked)
                and self.renderer.set_theme(
                    self._setting("feather_theme", "DEFAULT"))):
            logging.info("[feather_screen] color theme changed to %s",
                         self.renderer.theme_name)
            self._show_page(self.page)
        self._service_network(eventtime)
        self._update_eco_backlight(eventtime)
        stats = self.print_stats.get_status(eventtime)
        state = stats["state"]
        virtual_sd_active = self.virtual_sdcard.is_active()
        if state == "printing":
            new_state = (PrintState.PREPARING
                         if (stats["print_duration"] == 0
                             and not self._restored_print_active(eventtime))
                         else PrintState.PRINTING)
        elif state == "paused":
            new_state = PrintState.PAUSED
        elif state in ("complete", "cancelled", "error"):
            # Terminal virtual_sd states are operationally idle. Keeping a
            # separate FINISHED controller state left controls looking active
            # while rejecting or delaying taps after cancel.
            new_state = PrintState.IDLE
        else:
            new_state = PrintState.IDLE
        if new_state != self.print_state:
            self._change_print_state(new_state, state)
        self._poll_usb_storage(eventtime)
        self._update_operation_context(eventtime)
        if manager is not None:
            manager.update(eventtime)
        self._refresh_emergency_stop(eventtime)
        if self.pending_action is not None:
            expected = {"print.pause": "paused", "print.resume": "printing",
                        "print.cancel.confirm": "cancelled"}.get(self.pending_action)
            completed = state == expected
            if self.pending_action == "print.cancel.confirm":
                completed = state not in ("printing", "paused") and not virtual_sd_active
            if completed:
                self.pending_action = None
                if self.page == ScreenPage.CANCEL_CONFIRM:
                    self.print_state = PrintState.IDLE
                    self._show_message("Print cancelled", ScreenPage.IDLE_HOME)
                elif self.page in (ScreenPage.PRINTING, ScreenPage.PAUSED):
                    self._show_page(self.page)
            elif eventtime >= self.pending_until:
                if self.pending_action == "print.cancel.confirm":
                    # A long G28/mesh/prime operation is expected to finish at
                    # its next cooperative boundary. Keep the accepted request
                    # active instead of re-enabling the confirmation control.
                    self.pending_until = eventtime + 30.0
                    self._update_cancel_progress()
                elif self.page in (ScreenPage.PRINTING, ScreenPage.PAUSED):
                    self.pending_action = None
                    self._show_page(self.page)
        if (getattr(self, "file_scan_loading", False)
                and self.page == ScreenPage.FILE_BROWSER):
            self.file_scan_phase = (self.file_scan_phase + 1) % 5
            label = ("LOADING USB FILES..."
                     if getattr(self, "file_scan_source", None) == "usb"
                     else "LOADING PRINT FILES...")
            self.renderer.loader(label, self.file_scan_phase)
        elif self.busy_message is not None:
            self.busy_phase = (self.busy_phase + 1) % 5
            self.renderer.loader(self.busy_message, self.busy_phase)
        elif self.page in (ScreenPage.PRINTING, ScreenPage.PAUSED):
            self._update_print_progress(eventtime)
        elif (self.page == ScreenPage.CANCEL_CONFIRM
              and getattr(self, "cancel_mode", None) == "pending"):
            self._update_cancel_progress()
        elif self.page == ScreenPage.IDLE_HOME:
            self._update_dashboard(eventtime)
        elif self.page == ScreenPage.CONTROL_MOVE:
            self._update_move_status(eventtime)
        elif self.page == ScreenPage.CONTROL_HEAT:
            self._update_heat_status(eventtime)
        elif (self.page == ScreenPage.ACTION_PROMPT
              and self._action_prompt_is_cold_pull()):
            self._render_action_prompt()
        if self.filament_sensor is not None:
            sensor = self.filament_sensor.get_status(eventtime)
            present = sensor.get("filament_detected")
            if (self._filament_present is True and present is False and
                    self.print_state == PrintState.PAUSED and
                    self.page not in (ScreenPage.FILAMENT_MATERIAL, ScreenPage.FILAMENT_ACTION)):
                self._open_filament(True)
            self._filament_present = present

        extruder = self.extruder.get_status(eventtime)
        bed = self.heater_bed.get_status(eventtime)
        network = self.network_status.get("ip") or "Offline"
        temperatures = "NOZZLE %.0f/%.0fC | BED %.0f/%.0fC" % (
            extruder["temperature"], extruder["target"],
            bed["temperature"], bed["target"])
        status = "%s | %s" % (network, state.upper())
        self.renderer.footer(temperatures, status)
        if self.toast_until and eventtime >= self.toast_until:
            self._hide_toast()
        return eventtime + REFRESH_TIME

    def _poll_usb_storage(self, eventtime):
        monitor = getattr(self, "usb_storage", None)
        if monitor is None:
            return
        if self.print_state != PrintState.IDLE:
            monitor.pause()
            return
        monitor.resume(eventtime)
        if not monitor.tick(eventtime):
            return
        self._invalidate_file_entries()
        self.file_scan_token = getattr(self, "file_scan_token", 0) + 1
        self.file_scan_loading = False
        self.file_scan_source = None
        if (not monitor.available
                and getattr(self, "file_source", "internal") == "usb"):
            self.file_source = "internal"
            self.file_page = 0
            self.selected_file = None
            self.file_confirm_rebuild_mesh = False
            self.file_confirm_auto_mesh = False
            if self.page == ScreenPage.FILE_CONFIRM:
                self._show_message("USB drive removed", ScreenPage.FILE_BROWSER)
                return
        if self.page == ScreenPage.FILE_BROWSER:
            self._render_file_browser()

    def _restored_print_active(self, eventtime):
        resurrection = getattr(self, "resurrection", None)
        status = (resurrection.get_status(eventtime)
                  if resurrection is not None else {})
        return bool(status.get("restored"))

    def _change_print_state(self, new_state, stats_state):
        old_state = self.print_state
        self.print_state = new_state
        self.state_time = self.reactor.monotonic()
        if (new_state in (PrintState.PREPARING, PrintState.PRINTING)
                and old_state not in (
                    PrintState.PREPARING, PrintState.PRINTING,
                    PrintState.PAUSED)):
            self._record_current_print()
        self._notify_features(
            "on_print_state_changed", old_state, new_state, stats_state)
        update_notification = getattr(self, "update_notification", None)
        if update_notification is not None:
            update_notification.on_print_state_changed(
                old_state, new_state, stats_state)
        if (new_state in (PrintState.PREPARING, PrintState.PRINTING,
                          PrintState.PAUSED)
                and getattr(self, "network_operation", None) is not None):
            # A print starting mid-attempt cancels it, but the rollback is the
            # daemon's: it owns the candidate netblock and the incumbent. The
            # subscription itself stays up so the home page keeps rendering the
            # link while printing.
            self._cancel_network_operation()
        if self.debug:
            logging.info("[feather_screen] %s -> %s", old_state.name, new_state.name)
        if (old_state == PrintState.IDLE
                and new_state in (PrintState.PREPARING, PrintState.PRINTING,
                                  PrintState.PAUSED)):
            self.cancel_requested = False
            self._progress_floor = 0.0
            self._progress_source = None
            restored = self._restored_print_active(self.state_time)
            self._progress_start = (0.0, 0.0) if restored else None
            self._m73_active = False
        if new_state in (PrintState.PREPARING, PrintState.PRINTING):
            if (self.page not in (
                    ScreenPage.PRINTING, ScreenPage.CANCEL_CONFIRM, ScreenPage.LIVE_Z_OFFSET)
                    and not (self.page == ScreenPage.IDLE_HOME
                             and getattr(
                                 self, "home_during_print", False))):
                self._show_page(ScreenPage.PRINTING)
        elif new_state == PrintState.PAUSED:
            if self.page not in (
                    ScreenPage.CANCEL_CONFIRM, ScreenPage.FILAMENT_MATERIAL,
                    ScreenPage.FILAMENT_ACTION, ScreenPage.LIVE_Z_OFFSET) and not (
                        self.page == ScreenPage.IDLE_HOME
                        and getattr(self, "home_during_print", False)):
                self._show_page(ScreenPage.PAUSED)
        elif new_state == PrintState.IDLE:
            self._filament_request_token = getattr(
                self, "_filament_request_token", 0) + 1
            if old_state in (PrintState.PREPARING, PrintState.PRINTING,
                             PrintState.PAUSED, PrintState.FINISHED):
                cancelled = (getattr(self, "cancel_requested", False)
                             or stats_state == "cancelled")
                if cancelled:
                    macro = getattr(self, "cancel_print_macro", None)
                    variables = getattr(macro, "variables", {})
                    reason = str(
                        variables.get("cancel_reason") or ""
                    ).replace("\n", " ").strip()
                    label = ("Print cancelled\nReason: %s" % reason
                             if reason else "Print cancelled")
                else:
                    label = {
                        "complete": "Print finished",
                        "error": "Print failed",
                    }.get(stats_state, "Print stopped")
                self.cancel_requested = False
                self.cancel_waiting_for_heat = False
                self.home_during_print = False
                self._m73_start_expiry = float(getattr(
                    getattr(self, "display_status", None),
                    "expire_progress", 0.0) or 0.0)
                self._m73_active = False
                start = getattr(
                    getattr(self, "start_print_macro", None),
                    "variables", {})
                mesh_status = (self.bed_mesh.get_status(self.state_time)
                               if getattr(self, "bed_mesh", None) is not None
                               else {})
                offer_mesh_save = (
                    stats_state == "complete"
                    and start.get("zmesh_generated") == "auto"
                    and mesh_status.get("profile_name") == "auto"
                    and not self._setting("print_leveling", 0))
                if offer_mesh_save:
                    self._show_message(
                        "THE NEW AUTO BED MESH IS ACTIVE FOR THIS SESSION. "
                        "SAVE IT TO PRINTER.CFG? KLIPPER WILL RESTART.",
                        ScreenPage.IDLE_HOME,
                        actions=(("mesh.save", "SAVE & RESTART", "enabled"),
                                 ("message.ok", "LATER", "enabled")))
                else:
                    self._show_message(label, ScreenPage.IDLE_HOME)
            elif old_state == PrintState.INACTIVE:
                self._show_page(ScreenPage.IDLE_HOME)

    def _require_idle(self):
        if self._safety_print_active(self.reactor.monotonic()):
            raise RuntimeError("This action is available only while idle")

    def _get_time_estimation_str(self, eventtime):
        duration, remaining = self._print_time_values(eventtime)
        if self.print_state == PrintState.PRINTING:
            return "%s / %s" % (self._duration(duration),
                                 self._duration(remaining))
        return "~ %s" % self._duration(remaining, 2)

    @staticmethod
    def _clock_duration(value):
        if value is None:
            return "--:--:--"
        seconds = max(0, int(round(value)))
        days, seconds = divmod(seconds, 86400)
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        clock = "%02d:%02d:%02d" % (hours, minutes, seconds)
        return "%dd %s" % (days, clock) if days else clock

    @staticmethod
    def _duration(value, digits=1):
        if value is None:
            return "???"
        value = round(value)
        result = []
        for unit, divider in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
            if value >= divider:
                result.append("%d%s" % (value // divider, unit))
                value %= divider
        return " ".join(result[:digits]) if result else "0s"

    @staticmethod
    def _read_text(path):
        try:
            with open(path, "r") as stream:
                return stream.readline().strip()
        except OSError:
            return None

    @staticmethod
    def _format_size(size):
        if size >= 1024 * 1024:
            return "%.1f MiB" % (size / (1024.0 * 1024.0))
        if size >= 1024:
            return "%.1f KiB" % (size / 1024.0)
        return "%d bytes" % size


def load_config(config):
    return FeatherScreen(config)
