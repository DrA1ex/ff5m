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
import struct
import time

try:
    from .feather_ui import FeatherRenderer, Page, PrintState
    from .feather_screen_pages import (
        FeatherPagesMixin, FILE_ROWS, VALID_GCODE_EXTS, mod_ui,
        NETWORK_HELPER, NETWORK_TIMEOUTS, ALPHA_KEY_ROWS,
        SYMBOL_KEY_ROWS)
    from .feather_screen_controls import (
        FeatherControlsMixin, PREHEAT, MOVE_CAUTION_Z,
        joystick_ui, joystick_motion,
        JOYSTICK_XY_PANEL, JOYSTICK_XY_PAD, JOYSTICK_XY_CENTER,
        JOYSTICK_XY_RADIUS, JOYSTICK_XY_CURSOR_BOUNDS, JOYSTICK_XY_GRID,
        JOYSTICK_XY_VERTICAL, JOYSTICK_XY_HORIZONTAL, JOYSTICK_Z_PANEL,
        JOYSTICK_Z_CENTER, JOYSTICK_Z_RADIUS, JOYSTICK_Z_CURSOR_BOUNDS,
        JOYSTICK_Z_HITBOX, JOYSTICK_STATUS_PANEL, JOYSTICK_POSITION_CARD,
        JOYSTICK_INERTIA_CARD, JOYSTICK_KNOB_SIZE, JOYSTICK_DIRTY_MARGIN)
except (ImportError, ValueError):
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    from feather_ui import FeatherRenderer, Page, PrintState
    from feather_screen_pages import (
        FeatherPagesMixin, FILE_ROWS, VALID_GCODE_EXTS, mod_ui,
        NETWORK_HELPER, NETWORK_TIMEOUTS, ALPHA_KEY_ROWS,
        SYMBOL_KEY_ROWS)
    from feather_screen_controls import (
        FeatherControlsMixin, PREHEAT, MOVE_CAUTION_Z,
        joystick_ui, joystick_motion,
        JOYSTICK_XY_PANEL, JOYSTICK_XY_PAD, JOYSTICK_XY_CENTER,
        JOYSTICK_XY_RADIUS, JOYSTICK_XY_CURSOR_BOUNDS, JOYSTICK_XY_GRID,
        JOYSTICK_XY_VERTICAL, JOYSTICK_XY_HORIZONTAL, JOYSTICK_Z_PANEL,
        JOYSTICK_Z_CENTER, JOYSTICK_Z_RADIUS, JOYSTICK_Z_CURSOR_BOUNDS,
        JOYSTICK_Z_HITBOX, JOYSTICK_STATUS_PANEL, JOYSTICK_POSITION_CARD,
        JOYSTICK_INERTIA_CARD, JOYSTICK_KNOB_SIZE, JOYSTICK_DIRTY_MARGIN)


DISP_LCD_SET_BRIGHTNESS = 0x102
DISP_LCD_BACKLIGHT_ENABLE = 0x104
REFRESH_TIME = 1.0
ACTION_DEBOUNCE = 0.08
STARTUP_ANIMATION_PERIOD = 0.16
MAX_TOUCH_EVENT = 256


EXACT_ACTIONS = {
    Page.IDLE_HOME: ("nav.menu", "nav.heat", "nav.network", "nav.job"),
    Page.MAIN_MENU: ("nav.back", "nav.files", "nav.control", "nav.filament",
                     "nav.network"),
    Page.CONTROL_HOME: ("nav.back", "nav.move", "nav.heat", "nav.calibration",
                        "nav.settings"),
    Page.FILE_BROWSER: ("nav.back", "file.prev", "file.next"),
    Page.FILE_CONFIRM: ("nav.back", "file.start"),
    Page.PRINTING: ("nav.home", "print.pause", "print.filament",
                    "print.cancel", "print.z"),
    Page.PAUSED: ("nav.home", "print.resume", "print.filament",
                  "print.cancel", "print.z"),
    Page.CANCEL_CONFIRM: ("nav.back", "print.cancel.back", "print.cancel.confirm"),
    Page.CONTROL_MOVE: ("nav.back", "move.mode", "move.joy.xy", "move.joy.z"),
    Page.CONTROL_HEAT: ("nav.back",),
    Page.FILAMENT_MATERIAL: ("nav.back", "filament.PLA", "filament.PETG",
                             "filament.ABS", "filament.ABS-PC"),
    Page.FILAMENT_ACTION: ("nav.back", "filament.load", "filament.unload",
                           "filament.purge", "filament.done", "filament.resume"),
    Page.CALIBRATION_HOME: ("nav.back", "cal.z", "cal.screws", "cal.mesh"),
    Page.CALIBRATION_Z: ("nav.back", "z.step.001", "z.step.005", "z.closer",
                         "z.farther", "z.reset", "z.load.toggle"),
    Page.CALIBRATION_CONFIRM: ("nav.back", "cal.confirm", "cal.clean.skip"),
    Page.CALIBRATION_RESULT: ("cal.repeat", "cal.done"),
    Page.SETTINGS: ("nav.back", "settings.brightness.minus",
                    "settings.brightness.plus", "settings.eco.minus",
                    "settings.eco.plus", "settings.sound", "settings.theme",
                    "settings.mod"),
    Page.MOD_SETTINGS: ("nav.back", "mod.prev", "mod.next"),
    Page.MOD_ENUM: ("nav.back", "mod.cancel", "mod.apply",
                    "mod.enum.prev", "mod.enum.next"),
    Page.MOD_VALUE: ("nav.back", "mod.cancel", "mod.save", "mod.backspace",
                     "mod.sign", "mod.dot", "mod.shift", "mod.symbols",
                     "mod.space"),
    Page.NETWORK_HOME: ("nav.back", "net.scan", "net.ethernet", "net.retry"),
    Page.WIFI_SCAN: ("nav.back", "net.prev", "net.next", "net.rescan"),
    Page.WIFI_PASSWORD: ("nav.back", "net.connect", "net.password.toggle"),
    Page.NETWORK_PROGRESS: ("net.cancel",),
    Page.RECOVERY_PROMPT: (
        "recovery.restore", "recovery.cleanup", "recovery.later"),
    Page.RECOVERY_CONFIRM: ("nav.back", "recovery.confirm"),
    Page.MESSAGE: ("message.ok",),
    Page.ERROR: ("error.restart", "error.firmware_restart"),
}


class FeatherScreen(FeatherPagesMixin, FeatherControlsMixin):
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        self.debug = config.getboolean("debug", False)
        self.dim_timeout = config.getfloat("dim_timeout", 60.0, minval=10.0)
        self.z_offset_limit = config.getfloat("z_offset_limit", 2.0, minval=0.1)
        self.z_adjust_session_limit = config.getfloat(
            "z_adjust_session_limit", 0.5, minval=0.05)
        self.preheat = {}
        for material, defaults in PREHEAT.items():
            key = material.lower().replace("-", "_")
            self.preheat[material] = (
                config.getfloat("preheat_%s_nozzle" % key, defaults[0], minval=0),
                config.getfloat("preheat_%s_bed" % key, defaults[1], minval=0))
        self.renderer = FeatherRenderer(self.debug)
        self.renderer.set_retry_scheduler(
            lambda callback: self.reactor.register_callback(
                callback, self.reactor.monotonic() + 0.01))

        self.page = Page.IDLE_HOME
        self.previous_page = Page.IDLE_HOME
        self.print_state = PrintState.INACTIVE
        self.state_time = self.reactor.monotonic()
        self.timer = None
        self.startup_timer = None
        self.startup_phase = 0
        self.event_handle = None
        self.event_partial = ""
        self.renderer_retry_at = 0.0
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
        self._last_cancel_label = None
        self.home_during_print = False
        self.busy_message = None
        self.busy_phase = 0
        self.print_status_text = ""
        self.toast_until = 0.0
        self.toast_message = ""

        self.current_directory = ""
        self.file_page = 0
        self.file_entries = []
        self.selected_file = None
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
        self.joystick_drawn_cursor = None
        self.joystick_drawn_inertia = None
        self.joystick_feedback_at = 0.0
        self.move_caution_acknowledged = False
        self.move_caution_signature = None
        self.z_step = 0.01
        self.z_session_adjust = 0.0

        self.mod_page = 0
        self.mod_parameter = None
        self.mod_return_page = Page.MOD_SETTINGS
        self.mod_edit_value = ""
        self.mod_enum_selection = None
        self.mod_enum_page = 0
        self.mod_keyboard_shift = False
        self.mod_keyboard_symbols = False
        self.mod_update_pending = False
        self.mod_update_token = 0
        self.mod_update_modal_visible = False
        self.mod_update_complete = None

        self.network_process = None
        self.network_stopping = []
        self.network_credentials = None
        self.network_operation = None
        self.network_return_page = Page.NETWORK_HOME
        self.network_parent_page = Page.MAIN_MENU
        self.networks = []
        self.network_page = 0
        self.selected_network = None
        self.password = ""
        self.keyboard_shift = False
        self.keyboard_symbols = False
        self.password_visible = False
        self.network_deadline = 0.0
        self.network_status = {"mode": "OFFLINE", "ssid": "", "signal": "", "ip": ""}
        self.filament_material = "n/a"
        self.filament_from_pause = False
        self.filament_original_target = 0.0
        self._filament_request_token = 0
        self.calibration_kind = None
        self.calibration_material = "PLA"
        self.calibration_clean_nozzle = True
        self.calibration_repeat_probe = False
        self.calibration_results = []
        self.calibration_mesh = []
        self.calibration_error = None
        self._last_calibration_label = None
        self._last_filament_heat = None
        self.recovery_action = None
        self.recovery_status = None
        self._filament_present = None

        self.message = ""
        self.message_return = Page.IDLE_HOME
        self.error_message = ""
        self.error_category = ""
        self.error_recovery = None

        self._last_progress = None
        self._progress_floor = 0.0
        self._progress_source = None
        self._m73_start_expiry = 0.0
        self._m73_active = False
        self._last_time = None
        self._last_print_controls_ready = None
        self._last_filename = None
        self._last_heat = None
        self.heat_return_page = Page.CONTROL_HOME
        self._last_dashboard = None
        self.last_job_name = "NONE"

        self.printer.register_event_handler("klippy:ready", self._init)
        self.printer.register_event_handler("klippy:shutdown", self._shutdown)
        self.printer.register_event_handler("klippy:disconnect", self._shutdown)
        self.gcode.register_command("FEATHER_PRINT_STATUS", self.cmd_FEATHER_PRINT_STATUS)
        self._start_pre_ready_ui()

    def _ensure_renderer_started(self):
        if self.renderer.active:
            if self.event_handle is None:
                self.event_handle = self.reactor.register_fd(
                    self.renderer.event_fd, self._process_touch_events)
            return False
        if self.event_handle is not None:
            try:
                self.reactor.unregister_fd(self.event_handle)
            except Exception:
                pass
            self.event_handle = None
        self.renderer.stop()
        self.renderer.start()
        self.event_handle = self.reactor.register_fd(
            self.renderer.event_fd, self._process_touch_events)
        return True

    def _start_pre_ready_ui(self):
        try:
            self._enable_backlight()
            self._ensure_renderer_started()
            self.renderer.startup_modal(self.startup_phase)
        except Exception:
            logging.exception("[feather_screen] unable to draw startup modal")
        if self.startup_timer is None:
            self.startup_timer = self.reactor.register_timer(
                self._startup_tick, self.reactor.NOW)

    def _startup_tick(self, eventtime):
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
                self.renderer.startup_modal(self.startup_phase)
            else:
                self.renderer.send(
                    self.renderer.startup_pulse(self.startup_phase))
        except Exception:
            logging.exception("[feather_screen] startup animation failed")
        return eventtime + STARTUP_ANIMATION_PERIOD

    def _stop_startup_animation(self):
        if self.startup_timer is None:
            return
        try:
            self.reactor.unregister_timer(self.startup_timer)
        except Exception:
            pass
        self.startup_timer = None

    def _init(self):
        self.params = self.printer.lookup_object("mod_params")
        self._enable_backlight()
        self._set_backlight(self._setting("backlight", 100))
        self.renderer.ensure_user_theme_directory()
        self.renderer.reload_themes()
        self.renderer.set_theme(self._setting("feather_theme", "DEFAULT"))
        self.filament_material = self._current_material()
        self.extruder = self.printer.lookup_object("extruder")
        self.heater_bed = self.printer.lookup_object("heater_bed")
        self.toolhead = self.printer.lookup_object("toolhead")
        self.input_shaper = self.printer.lookup_object("input_shaper", None)
        self.idle_timeout = self.printer.lookup_object("idle_timeout")
        self.pause_resume = self.printer.lookup_object("pause_resume")
        self.display_status = self.printer.lookup_object("display_status")
        self._m73_start_expiry = float(
            getattr(self.display_status, "expire_progress", 0.0) or 0.0)
        self.print_stats = self.printer.lookup_object("print_stats")
        self.virtual_sdcard = self.printer.lookup_object("virtual_sdcard")
        self.gcode_move = self.printer.lookup_object("gcode_move")
        self.temperature_wait = self.printer.lookup_object(
            "gcode_macro _WAIT_TEMPERATURE", None)
        self.print_flow = self.printer.lookup_object(
            "gcode_macro _PRINT_FLOW", None)
        self.start_print_macro = self.printer.lookup_object(
            "gcode_macro _START_PRINT", None)
        self.bed_mesh = self.printer.lookup_object("bed_mesh", None)
        self.fan = self.printer.lookup_object("fan", None)
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
            self._show_page(Page.RECOVERY_PROMPT)
        else:
            self._show_page(Page.IDLE_HOME)
        self._start_network_status_refresh()
        self.timer = self.reactor.register_timer(self._update, self.reactor.NOW)

    def _shutdown(self):
        # Suppress any final ToolHead flush after the MCU has already stopped.
        self.print_state = PrintState.DESTROYED
        self._stop_startup_animation()
        self._stop_joystick()
        self.print_state = PrintState.INACTIVE
        if self.joystick_timer is not None:
            self.reactor.unregister_timer(self.joystick_timer)
            self.joystick_timer = None
        if self.timer is not None:
            self.reactor.unregister_timer(self.timer)
        self.timer = None
        if self.network_process is not None:
            self._retire_network_process(self.network_process)
            self.network_process = None
        for process, _deadline, group_id in self.network_stopping:
            if group_id is not None:
                try:
                    os.killpg(group_id, signal.SIGKILL)
                except OSError:
                    pass
            elif process.poll() is None:
                killer = getattr(process, "kill", None)
                if killer is not None:
                    killer()
        self.network_stopping = []
        self._cleanup_network_credentials()
        if self.renderer.active:
            msg, category = self.printer.get_state_message()
            message = msg if str(msg).strip() else "Klipper disconnected"
            self._show_error(message, category)

    def cmd_FEATHER_PRINT_STATUS(self, gcmd):
        status = gcmd.get("S")
        self.print_status_text = status
        if self.page in (Page.PRINTING, Page.PAUSED):
            self._draw_print_status(status)
        elif self.page == Page.CALIBRATION_PROGRESS:
            self._update_calibration_progress()

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
            if line.startswith("touch "):
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
        if phase == "begin":
            logging.info("[feather_screen] continuous begin action=%s x=%d y=%d",
                         action if action is not None else raw_action, x, y)
            if self._wake_if_dimmed():
                self.joystick_suppressed = raw_action
                return
            if (action == "move.joy.xy"
                    and getattr(self, "move_caution_signature",
                                (False, None))[0]):
                self.joystick_suppressed = raw_action
                return
            if (action not in ("move.joy.xy", "move.joy.z")
                    or self.page != Page.CONTROL_MOVE
                    or self.move_mode != "joystick"
                    or not self._action_allowed(self.page, action)
                    or self.print_state != PrintState.IDLE
                    or self.command_depth > 0):
                self.joystick_suppressed = raw_action
                return
            homed = str(self.toolhead.get_status(now).get("homed_axes", ""))
            required = "xy" if action == "move.joy.xy" else "z"
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
        elif action == "move.joy.xy":
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
        if getattr(self, "mod_update_pending", False):
            logging.info("[feather_screen] touch ignored while mod update is active: %s",
                         action)
            return
        busy_allowed = action in ("print.cancel", "print.cancel.confirm")
        if (self.page == Page.CANCEL_CONFIRM and
                action in ("nav.back", "print.cancel.back")):
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
        if ((source_page is not None and self.page != source_page)
                or (generation is not None
                    and current_generation != generation)):
            self.touch_feedback_pending = False
            return
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

    def _restart_renderer(self, eventtime):
        if eventtime < self.renderer_retry_at:
            return False
        self.renderer_retry_at = eventtime + 5.0
        logging.warning("[feather_screen] typer stopped; restarting")
        if self.event_handle is not None:
            self.reactor.unregister_fd(self.event_handle)
            self.event_handle = None
        self.renderer.stop()
        try:
            self.renderer.start()
            self.event_handle = self.reactor.register_fd(
                self.renderer.event_fd, self._process_touch_events)
            self.event_partial = ""
            self._show_page(self.page)
            self.renderer_retry_at = 0.0
            return True
        except Exception:
            logging.exception("[feather_screen] unable to restart typer")
            self.renderer.stop()
            return False

    def _dispatch_action(self, action):
        if self.print_state == PrintState.DESTROYED:
            return
        now = self.reactor.monotonic()
        if now - self.last_action_time < ACTION_DEBOUNCE:
            logging.info("[feather_screen] debounced action=%s", action)
            return
        if self.pending_action is not None and action in (
                "print.pause", "print.resume", "print.cancel.confirm"):
            logging.info("[feather_screen] action already in progress=%s", action)
            return
        if not self._action_allowed(self.page, action):
            logging.info("[feather_screen] ignored action=%s page=%s",
                         action, self.page.name)
            return
        self.last_action_time = now
        logging.info("[feather_screen] action=%s page=%s", action, self.page.name)

        try:
            if action == "nav.back":
                self._go_back()
            elif action == "nav.home":
                self.home_during_print = self.print_state in (
                    PrintState.PREPARING, PrintState.PRINTING,
                    PrintState.PAUSED)
                self._show_page(Page.IDLE_HOME)
            elif action == "nav.menu":
                self._show_page(Page.MAIN_MENU)
            elif action == "nav.files":
                self.current_directory = ""
                self.file_page = 0
                self._show_page(Page.FILE_BROWSER)
            elif action == "nav.control":
                self._require_idle()
                self._show_page(Page.CONTROL_HOME)
            elif action == "nav.move":
                self._require_idle()
                self._show_page(Page.CONTROL_MOVE)
            elif action == "nav.heat":
                self.heat_return_page = self.page
                self._show_page(Page.CONTROL_HEAT)
            elif action == "nav.filament":
                self._open_filament(False)
            elif action == "nav.calibration":
                self._require_idle()
                self._show_page(Page.CALIBRATION_HOME)
            elif action == "nav.settings":
                self._require_idle()
                self._show_page(Page.SETTINGS)
            elif action == "nav.network":
                self.network_parent_page = self.page
                self._show_page(Page.NETWORK_HOME)
            elif action == "nav.job":
                stats = self.print_stats.get_status(
                    self.reactor.monotonic()).get("state")
                if stats in ("printing", "paused"):
                    self.home_during_print = False
                    self._show_page(self.page_for_print_state())
                else:
                    self.current_directory = ""
                    self.file_page = 0
                    self._show_page(Page.FILE_BROWSER)
            elif action.startswith("file."):
                self._handle_file_action(action)
            elif action.startswith("print."):
                self._handle_print_action(action)
            elif action.startswith("move."):
                self._handle_move_action(action)
            elif action.startswith("heat."):
                self._handle_heat_action(action)
            elif action.startswith("filament."):
                self._handle_filament_action(action)
            elif action.startswith("cal.") or action.startswith("z."):
                self._handle_calibration_action(action)
            elif action.startswith("settings."):
                self._handle_settings_action(action)
            elif action.startswith("mod."):
                self._handle_mod_action(action)
            elif action.startswith("recovery."):
                self._handle_recovery_action(action)
            elif action.startswith("error."):
                self._handle_error_action(action)
            elif action.startswith("net.") or action.startswith("key."):
                self._handle_network_action(action)
            elif action == "message.ok":
                self._show_page(self.message_return)
        except Exception as exc:
            logging.exception("[feather_screen] action failed: %s", action)
            self._show_message(str(exc), self.page)

    @staticmethod
    def _action_allowed(page, action):
        if action in EXACT_ACTIONS.get(page, ()):
            return True
        return ((page == Page.FILE_BROWSER and action.startswith("file.item"))
                or (page == Page.CONTROL_MOVE and action.startswith("move."))
                or (page == Page.CONTROL_HEAT and action.startswith("heat."))
                or (page == Page.CALIBRATION_CONFIRM and
                    action.startswith("cal.material."))
                or (page == Page.MOD_SETTINGS and action.startswith("mod.item."))
                or (page == Page.MOD_ENUM and action.startswith("mod.option."))
                or (page == Page.MOD_VALUE and action.startswith("mod.key."))
                or (page == Page.WIFI_SCAN and action.startswith("net.item"))
                or (page == Page.WIFI_PASSWORD and action.startswith("key.")))

    def _show_page(self, page):
        if (self.page == Page.CONTROL_MOVE
                and (page != Page.CONTROL_MOVE
                     or getattr(self, "joystick_action", None) is not None)):
            self._stop_joystick()
        self.previous_page = self.page
        self.page = page
        if page == Page.IDLE_HOME:
            self._render_home()
        elif page == Page.MAIN_MENU:
            self._render_main_menu()
        elif page == Page.CONTROL_HOME:
            self._render_control_home()
        elif page == Page.FILE_BROWSER:
            self._render_file_browser()
        elif page == Page.FILE_CONFIRM:
            self._render_file_confirm()
        elif page in (Page.PRINTING, Page.PAUSED):
            self._render_print_page()
        elif page == Page.CANCEL_CONFIRM:
            self._render_cancel_confirm()
        elif page == Page.CONTROL_MOVE:
            self._render_move()
        elif page == Page.CONTROL_HEAT:
            self._render_heat()
        elif page == Page.FILAMENT_MATERIAL:
            self._render_filament_material()
        elif page == Page.FILAMENT_ACTION:
            self._render_filament_action()
        elif page == Page.CALIBRATION_HOME:
            self._render_calibration_home()
        elif page == Page.CALIBRATION_Z:
            self._render_calibration_z()
        elif page == Page.CALIBRATION_CONFIRM:
            self._render_calibration_confirm()
        elif page == Page.CALIBRATION_PROGRESS:
            self._render_calibration_progress()
        elif page == Page.CALIBRATION_RESULT:
            self._render_calibration_result()
        elif page == Page.SETTINGS:
            self._render_settings()
        elif page == Page.MOD_SETTINGS:
            self._render_mod_settings()
        elif page == Page.MOD_ENUM:
            self._render_mod_enum()
        elif page == Page.MOD_VALUE:
            self._render_mod_value()
        elif page == Page.NETWORK_HOME:
            self._render_network_home()
        elif page == Page.WIFI_SCAN:
            self._render_wifi_scan()
        elif page == Page.WIFI_PASSWORD:
            self._render_keyboard()
        elif page == Page.NETWORK_PROGRESS:
            self._render_network_progress()
        elif page == Page.RECOVERY_PROMPT:
            self._render_recovery_prompt()
        elif page == Page.RECOVERY_CONFIRM:
            self._render_recovery_confirm()
        elif page == Page.MESSAGE:
            self._render_message()
        elif page == Page.ERROR:
            self._render_error()

    def _go_back(self):
        if self.page == Page.FILE_BROWSER and self.current_directory:
            self.current_directory = os.path.dirname(self.current_directory)
            self.file_page = 0
            self._render_file_browser()
        elif self.page == Page.FILE_CONFIRM:
            self._show_page(Page.FILE_BROWSER)
        elif self.page in (Page.CONTROL_HOME, Page.FILAMENT_MATERIAL):
            if self.page == Page.FILAMENT_MATERIAL and self.filament_from_pause:
                self._show_page(self.page_for_print_state())
            else:
                self._show_page(Page.MAIN_MENU)
        elif self.page == Page.NETWORK_HOME:
            self._show_page(getattr(
                self, "network_parent_page", Page.MAIN_MENU))
        elif self.page == Page.MAIN_MENU:
            self._show_page(Page.IDLE_HOME)
        elif self.page == Page.CONTROL_HEAT:
            self._show_page(getattr(
                self, "heat_return_page", Page.CONTROL_HOME))
        elif self.page in (Page.CONTROL_MOVE, Page.CALIBRATION_HOME,
                           Page.SETTINGS):
            self._show_page(Page.CONTROL_HOME)
        elif self.page == Page.MOD_SETTINGS:
            self._show_page(Page.SETTINGS)
        elif self.page in (Page.MOD_ENUM, Page.MOD_VALUE):
            self.mod_parameter = None
            self._show_page(getattr(
                self, "mod_return_page", Page.MOD_SETTINGS))
        elif self.page == Page.FILAMENT_ACTION:
            self._finish_filament(False)
        elif self.page == Page.CALIBRATION_Z:
            self._show_page(Page.CALIBRATION_HOME if self.print_state == PrintState.IDLE
                            else self.page_for_print_state())
        elif self.page == Page.CALIBRATION_CONFIRM:
            self._show_page(Page.CALIBRATION_HOME)
        elif self.page == Page.RECOVERY_CONFIRM:
            self._show_page(Page.RECOVERY_PROMPT)
        elif self.page in (Page.WIFI_SCAN, Page.WIFI_PASSWORD):
            self._show_page(Page.NETWORK_HOME if self.page == Page.WIFI_SCAN
                            else Page.WIFI_SCAN)
        elif self.page == Page.CANCEL_CONFIRM:
            self._show_page(self.page_for_print_state())
        else:
            self._show_page(Page.IDLE_HOME)


    def page_for_print_state(self):
        try:
            state = self.print_stats.get_status(
                self.reactor.monotonic()).get("state")
        except Exception:
            state = None
        if state == "paused":
            return Page.PAUSED
        if state == "printing":
            return Page.PRINTING
        if state in ("complete", "cancelled", "error", "standby"):
            return Page.IDLE_HOME
        return (Page.PAUSED if self.print_state == PrintState.PAUSED
                else Page.PRINTING)

    def _setting(self, key, default):
        params = getattr(self, "params", None)
        return params.variables.get(key, default) if params is not None else default

    @staticmethod
    def _normalize_material(value):
        material = str(value or "n/a").strip().upper().replace("/", "-")
        if material in ("N/A", "NONE", "UNKNOWN", ""):
            return "n/a"
        return material if material in PREHEAT else "n/a"

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

    def _temperature_wait_cancelled(self):
        wait = getattr(self, "temperature_wait", None)
        return bool(wait is not None and
                    getattr(wait, "variables", {}).get("cancel", False))

    def _run_script(self, command):
        """Serialize Feather G-code through Klipper's reactor mutex.

        run_script_from_command() bypasses the mutex and may recursively enter
        a yielding macro. run_script() serializes normal commands while still
        extracting patched immediate commands before acquiring the mutex.
        """
        outermost = getattr(self, "command_depth", 0) == 0
        reactor = getattr(self, "reactor", None)
        clock = reactor.monotonic if reactor is not None else time.monotonic
        started = clock()
        first_line = (str(command).strip().splitlines() or [""])[0]
        command_name = (first_line.split(None, 1) or ["UNKNOWN"])[0]
        self.command_depth = getattr(self, "command_depth", 0) + 1
        renderer = getattr(self, "renderer", None)
        if outermost:
            page = getattr(self, "page", "UNKNOWN")
            logging.info("[feather_screen] command start name=%s page=%s",
                         command_name, getattr(page, "name", page))
            if renderer is not None:
                notice = getattr(renderer, "busy_notice", None)
                if notice is not None:
                    notice("KLIPPER BUSY")
        try:
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
                if renderer is not None:
                    clear = getattr(renderer, "clear_busy_notice", None)
                    if clear is not None:
                        clear()

    def _run_immediate_command(self, command):
        """Dispatch a patched immediate command without entering the G-code mutex.

        M108 is specifically designed to interrupt a yielding temperature macro.
        Passing it through run_script() used to wait for that macro to unwind;
        by then _WAIT_TEMPERATURE had reset its flag and already called
        CANCEL_PRINT, making Feather issue a second cancellation.
        """
        if command not in ("M108", "FEATHER_ABORT"):
            raise ValueError("Unsupported immediate Feather command")
        self.gcode.run_script_from_command(command)

    def _run_blocking_gcode(self, command, message):
        # Unit tests and early startup may not have a live renderer yet.
        renderer = getattr(self, "renderer", None)
        if renderer is None or not hasattr(self, "busy_message"):
            self._run_script(command)
            return
        page = self.page
        self.busy_message = message
        self.busy_phase = 0
        logging.info("[feather_screen] operation start label=%s page=%s",
                     message, page.name)
        renderer.loader(message, 0)
        try:
            self._run_script(command)
        finally:
            self.busy_message = None
            logging.info("[feather_screen] operation finish label=%s page=%s",
                         message, self.page.name)
            if self.page == page and self.print_state != PrintState.DESTROYED:
                self._show_page(page)

    def _toast(self, message):
        self.toast_until = self.reactor.monotonic() + 2.0
        self.toast_message = self._shorten(message, 60)
        self.renderer.toast(self.toast_message)

    def _show_message(self, message, return_page):
        recovery = self._classify_error(message)
        if recovery is not None:
            self._show_error(message, "runtime", recovery)
            return
        self.message = self._shorten(message, 100)
        self.message_return = return_page
        self._show_page(Page.MESSAGE)

    def _render_message(self):
        commands = self.renderer.begin_page("Message")
        commands += self.renderer.dialog(
            "Message", self._wrap(self.message, 48, 4),
            (("message.ok", "OK", "enabled"),),
            x=90, y=95, width=620, height=300, tone="info")
        self.renderer.send(commands)

    @staticmethod
    def _classify_error(message, category=""):
        text = ("%s %s" % (category, message)).lower()
        markers = (
            "mcu shutdown",
            "mcu '",
            "lost communication with mcu",
            "unable to connect to mcu",
            "timer too close",
            "unable to obtain 'endstop_state'",
            "shutdown due to",
            "printer is shutdown",
            "can not update mcu",
            "missed scheduling",
        )
        category = str(category).lower()
        if category == "shutdown":
            return "firmware_restart"
        if any(marker in text for marker in markers):
            return "firmware_restart"
        if category == "error":
            return "restart"
        return None

    def _show_error(self, message, category="", recovery=None):
        self.error_message = self._shorten(str(message).replace("\n", " "), 220)
        self.error_category = str(category or "")
        self.error_recovery = (
            recovery if recovery is not None
            else self._classify_error(self.error_message, self.error_category))
        self._show_page(Page.ERROR)

    def _render_error(self):
        commands = self.renderer.begin_page("Klipper error")
        lines = list(self._wrap(self.error_message, 58, 3))
        if self.error_recovery == "firmware_restart":
            lines.append("Check the printer, then restart the MCU.")
            buttons = (("error.firmware_restart",
                        "FIRMWARE RESTART", "danger"),)
            title = "MCU RESTART REQUIRED"
        elif self.error_recovery == "restart":
            lines.append("Correct the issue, then restart Klipper.")
            buttons = (("error.restart", "RESTART", "danger"),)
            title = "KLIPPER ERROR"
        else:
            lines.append("Waiting for Klipper to reconnect.")
            buttons = ()
            title = "KLIPPER IS NOT READY"
        commands += self.renderer.dialog(
            title, tuple(lines), buttons,
            x=80, y=85, width=640, height=325, tone="danger")
        self.renderer.send(commands)

    def _handle_error_action(self, action):
        commands = {
            "error.restart": "RESTART",
            "error.firmware_restart": "FIRMWARE_RESTART",
        }
        command = commands.get(action)
        if command is None:
            return
        self.error_message = ""
        self.error_category = ""
        self.error_recovery = None
        self.startup_phase = 0
        if self.timer is not None:
            try:
                self.reactor.unregister_timer(self.timer)
            except Exception:
                pass
            self.timer = None
        self._start_pre_ready_ui()
        self._run_script(command)

    def _update(self, eventtime):
        if self.print_state == PrintState.DESTROYED:
            return None
        if not self.renderer.active:
            self._restart_renderer(eventtime)
        if (not getattr(self, "mod_update_pending", False)
                and self.renderer.set_theme(
                    self._setting("feather_theme", "DEFAULT"))):
            logging.info("[feather_screen] color theme changed to %s",
                         self.renderer.theme_name)
            self._show_page(self.page)
        self._reap_network_processes(eventtime)
        self._poll_network_process(eventtime)
        if not self.dimmed and eventtime - self.last_touch_time >= self.dim_timeout:
            self.dimmed = True
            logging.info("[feather_screen] dimming display after %.1fs idle",
                         eventtime - self.last_touch_time)
            self._set_backlight(self._setting("backlight_eco", 10))
        stats = self.print_stats.get_status(eventtime)
        state = stats["state"]
        virtual_sd_active = self.virtual_sdcard.is_active()
        if state == "printing":
            new_state = (PrintState.PREPARING
                         if stats["print_duration"] == 0
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
        if self.pending_action is not None:
            expected = {"print.pause": "paused", "print.resume": "printing",
                        "print.cancel.confirm": "cancelled"}.get(self.pending_action)
            completed = state == expected
            if self.pending_action == "print.cancel.confirm":
                completed = state not in ("printing", "paused") and not virtual_sd_active
            if completed:
                self.pending_action = None
                if self.page == Page.CANCEL_CONFIRM:
                    self.print_state = PrintState.IDLE
                    self._show_message("Print cancelled", Page.IDLE_HOME)
                elif self.page in (Page.PRINTING, Page.PAUSED):
                    self._show_page(self.page)
            elif eventtime >= self.pending_until:
                if self.pending_action == "print.cancel.confirm":
                    # A long G28/mesh/prime operation is expected to finish at
                    # its next cooperative boundary. Keep the accepted request
                    # active instead of re-enabling the confirmation control.
                    self.pending_until = eventtime + 30.0
                    self._update_cancel_progress()
                elif self.page in (Page.PRINTING, Page.PAUSED):
                    self.pending_action = None
                    self._show_page(self.page)
        if self.busy_message is not None:
            self.busy_phase = (self.busy_phase + 1) % 5
            self.renderer.loader(self.busy_message, self.busy_phase)
        elif self.page in (Page.PRINTING, Page.PAUSED):
            self._update_print_progress(eventtime)
        elif self.page == Page.CANCEL_CONFIRM and self.cancel_requested:
            self._update_cancel_progress()
        elif self.page == Page.IDLE_HOME:
            self._update_dashboard(eventtime)
        elif self.page == Page.CONTROL_MOVE:
            self._update_move_status(eventtime)
        elif self.page == Page.CONTROL_HEAT:
            self._update_heat_status(eventtime)
        elif self.page == Page.FILAMENT_ACTION:
            self._update_filament_status(eventtime)
        elif self.page == Page.CALIBRATION_PROGRESS and self.calibration_kind in (
                "mesh", "recovery"):
            self._update_calibration_progress()
        if self.page == Page.CALIBRATION_Z and self.print_state in (
                PrintState.PRINTING, PrintState.PAUSED) and not self._z_adjust_allowed(eventtime):
            self._show_page(self.page_for_print_state())
            self._toast("Z adjust closed after first layer")

        if self.filament_sensor is not None:
            sensor = self.filament_sensor.get_status(eventtime)
            present = sensor.get("filament_detected")
            if (self._filament_present is True and present is False and
                    self.print_state == PrintState.PAUSED and
                    self.page not in (Page.FILAMENT_MATERIAL, Page.FILAMENT_ACTION)):
                self._open_filament(True)
            self._filament_present = present

        extruder = self.extruder.get_status(eventtime)
        bed = self.heater_bed.get_status(eventtime)
        network = self.network_status.get("ip") or self._read_text("/tmp/net_ip") or "Offline"
        self.renderer.footer(extruder["temperature"], extruder["target"],
                             bed["temperature"], bed["target"],
                             self._shorten(network, 18), state.upper())
        if self.toast_until and eventtime >= self.toast_until:
            self.toast_until = 0.0
            self._show_page(self.page)
        return eventtime + REFRESH_TIME

    def _change_print_state(self, new_state, stats_state):
        old_state = self.print_state
        self.print_state = new_state
        self.state_time = self.reactor.monotonic()
        if (new_state in (PrintState.PREPARING, PrintState.PRINTING,
                          PrintState.PAUSED)
                and getattr(self, "network_process", None) is not None):
            self._stop_network_process()
        if self.debug:
            logging.info("[feather_screen] %s -> %s", old_state.name, new_state.name)
        if new_state in (PrintState.PREPARING, PrintState.PRINTING):
            if old_state == PrintState.IDLE:
                self.cancel_requested = False
                self._progress_floor = 0.0
                self._progress_source = None
                self._m73_active = False
            if (self.page not in (Page.PRINTING, Page.CANCEL_CONFIRM)
                    and not (self.page == Page.IDLE_HOME
                             and getattr(
                                 self, "home_during_print", False))):
                self._show_page(Page.PRINTING)
        elif new_state == PrintState.PAUSED:
            if self.page not in (
                    Page.CANCEL_CONFIRM, Page.FILAMENT_MATERIAL,
                    Page.FILAMENT_ACTION, Page.CALIBRATION_Z) and not (
                        self.page == Page.IDLE_HOME
                        and getattr(self, "home_during_print", False)):
                self._show_page(Page.PAUSED)
        elif new_state == PrintState.IDLE:
            self._filament_request_token = getattr(
                self, "_filament_request_token", 0) + 1
            if old_state in (PrintState.PREPARING, PrintState.PRINTING,
                             PrintState.PAUSED, PrintState.FINISHED):
                label = ("Print cancelled" if
                         getattr(self, "cancel_requested", False) else
                         {"complete": "Print finished",
                          "cancelled": "Print cancelled",
                          "error": "Print failed"}.get(stats_state, "Print stopped"))
                self.cancel_requested = False
                self.cancel_waiting_for_heat = False
                self.home_during_print = False
                self._m73_start_expiry = float(getattr(
                    getattr(self, "display_status", None),
                    "expire_progress", 0.0) or 0.0)
                self._m73_active = False
                self._show_message(label, Page.IDLE_HOME)
            elif old_state == PrintState.INACTIVE:
                self._show_page(Page.IDLE_HOME)

    def _require_idle(self):
        state = self.print_stats.get_status(self.reactor.monotonic())["state"]
        if state in ("printing", "paused") or self.virtual_sdcard.is_active():
            raise RuntimeError("This action is available only while idle")

    def _network_status_text(self):
        if os.path.exists("/tmp/ethernet_connected_f"):
            prefix = "Ethernet"
        elif os.path.exists("/tmp/wifi_connected_f"):
            prefix = "Wi-Fi"
        else:
            return "Offline"
        ip = self._read_text("/tmp/net_ip")
        return prefix + (" - " + ip if ip else "")

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
    def _shorten(value, length):
        value = str(value)
        return value if len(value) <= length else value[:length - 3] + "..."

    @staticmethod
    def _wrap(value, width, max_lines):
        words = str(value).split()
        lines, current = [], ""
        for word in words:
            candidate = word if not current else current + " " + word
            if len(candidate) <= width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word[:width]
                if len(lines) >= max_lines:
                    break
        if current and len(lines) < max_lines:
            lines.append(current)
        return lines

    @staticmethod
    def _format_size(size):
        if size >= 1024 * 1024:
            return "%.1f MiB" % (size / (1024.0 * 1024.0))
        if size >= 1024:
            return "%.1f KiB" % (size / 1024.0)
        return "%d bytes" % size


def load_config(config):
    return FeatherScreen(config)
