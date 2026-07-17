## Interactive Feather screen support
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import errno
import fcntl
import logging
import os
import re
import signal
import struct
import subprocess
import time

try:
    from .feather_ui import FeatherRenderer, Page, PrintState
    from . import feather_mod_settings as mod_ui
    from . import feather_joystick as joystick_ui
except (ImportError, ValueError):
    # Host tests load this file directly rather than as a Klipper package.
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    from feather_ui import FeatherRenderer, Page, PrintState
    import feather_mod_settings as mod_ui
    import feather_joystick as joystick_ui


NETWORK_HELPER = "/root/printer_data/scripts/commands/znetwork.sh"
DISP_LCD_SET_BRIGHTNESS = 0x102
DISP_LCD_BACKLIGHT_ENABLE = 0x104
REFRESH_TIME = 1.0
ACTION_DEBOUNCE = 0.08
MAX_TOUCH_EVENT = 256
FILE_ROWS = 5
VALID_GCODE_EXTS = (".gcode", ".g", ".gco")
PREHEAT = {"PLA": (220, 60), "PETG": (250, 70), "ABS": (260, 100),
           "ABS-PC": (270, 105)}
NETWORK_TIMEOUTS = {"scan": 15.0, "wifi": 45.0, "ethernet": 30.0,
                    "status": 5.0, "status-background": 5.0}


EXACT_ACTIONS = {
    Page.IDLE_HOME: ("nav.menu",),
    Page.MAIN_MENU: ("nav.back", "nav.files", "nav.control", "nav.filament",
                     "nav.network"),
    Page.CONTROL_HOME: ("nav.back", "nav.move", "nav.heat", "nav.calibration",
                        "nav.settings"),
    Page.FILE_BROWSER: ("nav.back", "file.prev", "file.next"),
    Page.FILE_CONFIRM: ("nav.back", "file.start"),
    Page.PRINTING: ("print.pause", "print.filament", "print.cancel", "print.z"),
    Page.PAUSED: ("print.resume", "print.filament", "print.cancel", "print.z"),
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
    Page.CALIBRATION_CONFIRM: ("nav.back", "cal.confirm"),
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
    Page.RECOVERY_PROMPT: ("recovery.restore", "recovery.cleanup", "recovery.later"),
    Page.RECOVERY_CONFIRM: ("nav.back", "recovery.confirm"),
    Page.MESSAGE: ("message.ok",),
}
ALPHA_KEY_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")
SYMBOL_KEY_ROWS = (
    tuple((str(i), str(i)) for i in range(1, 10)) + (("0", "0"),),
    (("minus", "-"), ("under", "_"), ("plus", "+"), ("at", "@"),
     ("hash", "#"), ("dollar", "$"), ("percent", "%"), ("amp", "&"),
     ("star", "*"), ("bang", "!")),
    (("dot", "."), ("comma", ","), ("question", "?"), ("slash", "/"),
     ("colon", ":"), ("semi", ";"), ("lparen", "("), ("rparen", ")"),
     ("quote", '"'), ("bslash", "\\")),
)
class FeatherScreen:
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
        self.joystick_timer = None
        self.joystick_queued = False
        self.joystick_action = None
        self.joystick_suppressed = None
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
        self.calibration_kind = None
        self.calibration_material = "PLA"
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

        self._last_progress = None
        self._last_time = None
        self._last_filename = None
        self._last_heat = None
        self._last_dashboard = None
        self.last_job_name = "NONE"

        self.printer.register_event_handler("klippy:ready", self._init)
        self.printer.register_event_handler("klippy:shutdown", self._shutdown)
        self.printer.register_event_handler("klippy:disconnect", self._shutdown)
        self.gcode.register_command("FEATHER_PRINT_STATUS", self.cmd_FEATHER_PRINT_STATUS)

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
        self.idle_timeout = self.printer.lookup_object("idle_timeout")
        self.pause_resume = self.printer.lookup_object("pause_resume")
        self.display_status = self.printer.lookup_object("display_status")
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
        self.joystick_timer = self.reactor.register_timer(
            self._joystick_tick, self.reactor.NEVER)

        self.renderer.start()
        self.event_handle = self.reactor.register_fd(
            self.renderer.event_fd, self._process_touch_events)
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
        self.print_state = PrintState.DESTROYED
        self._stop_joystick()
        if self.joystick_timer is not None:
            self.reactor.unregister_timer(self.joystick_timer)
            self.joystick_timer = None
        if self.timer is not None:
            self.reactor.unregister_timer(self.timer)
        self.timer = None
        if self.event_handle is not None:
            self.reactor.unregister_fd(self.event_handle)
            self.event_handle = None
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
            self._render_error(msg.split("\n")[0] if category == "shutdown"
                               else "Disconnected")
        self.renderer.stop()

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
        elif action == "move.joy.xy":
            self.joystick.set_xy(x, y, now, 240, 220, 125)
        else:
            self.joystick.set_z(y, now, 220, 125)
        if self.joystick_timer is not None:
            self.reactor.update_timer(self.joystick_timer, self.reactor.NOW)

    def _handle_touch_action(self, action):
        if getattr(self, "mod_update_pending", False):
            logging.info("[feather_screen] touch ignored while mod update is active: %s",
                         action)
            return
        if (getattr(self, "command_depth", 0) > 0 and action not in
                ("print.cancel", "print.cancel.confirm")):
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
                self._require_idle()
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
                self._require_idle()
                self._start_network_process("status", [NETWORK_HELPER, "status"],
                                            Page.IDLE_HOME)
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

    def _go_back(self):
        if self.page == Page.FILE_BROWSER and self.current_directory:
            self.current_directory = os.path.dirname(self.current_directory)
            self.file_page = 0
            self._render_file_browser()
        elif self.page == Page.FILE_CONFIRM:
            self._show_page(Page.FILE_BROWSER)
        elif self.page in (Page.CONTROL_HOME, Page.NETWORK_HOME,
                           Page.FILAMENT_MATERIAL):
            if self.page == Page.FILAMENT_MATERIAL and self.filament_from_pause:
                self._show_page(Page.PAUSED)
            else:
                self._show_page(Page.MAIN_MENU)
        elif self.page == Page.MAIN_MENU:
            self._show_page(Page.IDLE_HOME)
        elif self.page in (Page.CONTROL_MOVE, Page.CONTROL_HEAT,
                           Page.CALIBRATION_HOME, Page.SETTINGS):
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
            self._show_page(Page.PAUSED if self.print_state == PrintState.PAUSED
                            else Page.PRINTING)
        else:
            self._show_page(Page.IDLE_HOME)

    def _render_home(self):
        commands = self.renderer.begin_page("FORGE-X // FEATHER")
        commands += self.renderer.button("nav.menu", 648, 9, 132, 38, "MENU",
                                         font="JetBrainsMono Bold 8pt")
        commands += [
            self.renderer.text(28, 80, "SYSTEM // STANDBY", "35d9e6",
                               "JetBrainsMono Bold 16pt", "left", "middle"),
            self.renderer.text(772, 80, time.strftime("%H:%M"), "d9e4e8",
                               "JetBrainsMono 16pt", "right", "middle"),
            self.renderer.fill(25, 104, 750, 1, "295c66"),
        ]
        panels = ((25, 124, 235, 112, "NOZZLE", "b47aff"),
                  (282, 124, 235, 112, "BED", "f2c94c"),
                  (539, 124, 236, 112, "NETWORK", "35d9e6"))
        for x, y, width, height, label, color in panels:
            commands += [self.renderer.fill(x, y, width, height, "050c0f"),
                         self.renderer.stroke(x, y, width, height, color, 2),
                         self.renderer.text(x + 16, y + 20, label, color,
                                            "JetBrainsMono 8pt", "left", "middle")]
        commands += [
            self.renderer.fill(25, 256, 750, 82, "050c0f"),
            self.renderer.stroke(25, 256, 750, 82, "295c66", 2),
            self.renderer.text(44, 278, "JOB STATUS", "56656c",
                               "JetBrainsMono 8pt", "left", "middle"),
            self.renderer.fill(25, 354, 750, 1, "295c66"),
            self.renderer.text(28, 374, "LAST JOB", "56656c",
                               "JetBrainsMono 8pt", "left", "middle"),
            self.renderer.text(300, 374, "MATERIAL", "56656c",
                               "JetBrainsMono 8pt", "left", "middle"),
            self.renderer.text(570, 374, "TOOLHEAD", "56656c",
                               "JetBrainsMono 8pt", "left", "middle"),
            self.renderer.text(400, 424, "OPEN MENU TO CONTROL PRINTER", "56656c",
                               "JetBrainsMono 8pt", "center", "middle"),
        ]
        self.renderer.send(commands)
        self._last_dashboard = None
        self._update_dashboard(self.reactor.monotonic())

    def _render_main_menu(self):
        commands = self.renderer.begin_page("MAIN MENU", back=True)
        tiles = (("nav.files", 22, 82, "PRINT FILES"),
                 ("nav.control", 410, 82, "CONTROL"),
                 ("nav.filament", 22, 242, "FILAMENT"),
                 ("nav.network", 410, 242, "NETWORK"))
        for action, x, y, label in tiles:
            commands += self.renderer.button(action, x, y, 368, 138, label,
                                             font="JetBrainsMono 12pt")
        self.renderer.send(commands)

    def _update_dashboard(self, eventtime):
        if self.page != Page.IDLE_HOME:
            return
        self.filament_material = self._current_material()
        extruder = self.extruder.get_status(eventtime)
        bed = self.heater_bed.get_status(eventtime)
        toolhead = self.toolhead.get_status(eventtime)
        homed = str(toolhead.get("homed_axes", "")).upper()
        mode = self.network_status.get("mode") or "OFFLINE"
        ssid = self.network_status.get("ssid") or ""
        ip = self.network_status.get("ip") or self._read_text("/tmp/net_ip") or "NO LINK"
        net_name = "%s%s" % (mode.upper(), " / " + ssid if ssid else "")
        values = (round(extruder["temperature"]), round(extruder["target"]),
                  round(bed["temperature"]), round(bed["target"]),
                  net_name, ip, self.last_job_name, self.filament_material,
                  homed or "NOT HOMED", time.strftime("%H:%M"))
        if values == self._last_dashboard:
            return
        previous = self._last_dashboard
        self._last_dashboard = values
        commands = []
        if previous is None or values[:2] != previous[:2]:
            commands += [
                self.renderer.fill(28, 153, 229, 78, "050c0f"),
                self.renderer.text(142, 178, "%d / %d C" % values[:2], "d9e4e8",
                                   "JetBrainsMono 12pt", "center", "middle"),
                self.renderer.text(142, 211,
                                   "HEATING" if values[1] > 0 else "OFF",
                                   "b47aff" if values[1] > 0 else "56656c",
                                   "JetBrainsMono 8pt", "center", "middle")]
        if previous is None or values[2:4] != previous[2:4]:
            commands += [
                self.renderer.fill(285, 153, 229, 78, "050c0f"),
                self.renderer.text(399, 178, "%d / %d C" % values[2:4], "d9e4e8",
                                   "JetBrainsMono 12pt", "center", "middle"),
                self.renderer.text(399, 211,
                                   "HEATING" if values[3] > 0 else "OFF",
                                   "f2c94c" if values[3] > 0 else "56656c",
                                   "JetBrainsMono 8pt", "center", "middle")]
        if previous is None or values[4:6] != previous[4:6]:
            commands += [
                self.renderer.fill(542, 153, 230, 78, "050c0f"),
                self.renderer.text(657, 178,
                                   self.renderer.truncate_text(values[4], 210,
                                                               "JetBrainsMono 8pt"),
                                   "d9e4e8", "JetBrainsMono 8pt", "center", "middle"),
                self.renderer.text(657, 211,
                                   self.renderer.truncate_text(values[5], 210,
                                                               "JetBrainsMono 8pt"),
                                   "35d9e6", "JetBrainsMono 8pt", "center", "middle")]
        if previous is None:
            commands += [
                self.renderer.fill(29, 284, 742, 50, "050c0f"),
                self.renderer.text(44, 310, "NO ACTIVE JOB", "35d9e6",
                                   "JetBrainsMono Bold 12pt", "left", "middle"),
                self.renderer.text(756, 310, "READY", "35d9e6",
                                   "JetBrainsMono 8pt", "right", "middle")]
        if previous is None or values[6:9] != previous[6:9]:
            commands += [
                self.renderer.fill(25, 386, 750, 27, "030607"),
                self.renderer.text(28, 400,
                                   self.renderer.truncate_text(values[6], 240,
                                                               "JetBrainsMono 8pt"),
                                   "d9e4e8", "JetBrainsMono 8pt", "left", "middle"),
                self.renderer.text(300, 400, values[7], "d9e4e8",
                                   "JetBrainsMono 8pt", "left", "middle"),
                self.renderer.text(570, 400, values[8],
                                   "35d9e6" if values[8] == "XYZ" else "f2c94c",
                                   "JetBrainsMono 8pt", "left", "middle")]
        if previous is None or values[9] != previous[9]:
            commands += [
                self.renderer.fill(680, 60, 92, 40, "030607"),
                self.renderer.text(772, 80, values[9], "d9e4e8",
                                   "JetBrainsMono 16pt", "right", "middle")]
        self.renderer.send(commands)

    def _render_control_home(self):
        commands = self.renderer.begin_page("Control menu", back=True)
        tiles = (("nav.move", 22, 82, "MOVE", "selected"),
                 ("nav.heat", 410, 82, "HEAT / FAN", "warning"),
                 ("nav.calibration", 22, 242, "CALIBRATION", "enabled"),
                 ("nav.settings", 410, 242, "SETTINGS", "enabled"))
        for action, x, y, label, state in tiles:
            commands += self.renderer.button(action, x, y, 368, 138, label,
                                             state=state,
                                             font="JetBrainsMono 12pt")
        self.renderer.send(commands)

    def _safe_directory(self):
        root = os.path.realpath(self.virtual_sdcard.sdcard_dirname)
        candidate = os.path.realpath(os.path.join(root, self.current_directory))
        if candidate != root and not candidate.startswith(root + os.sep):
            raise RuntimeError("Invalid print directory")
        return root, candidate

    def _load_file_entries(self):
        root, directory = self._safe_directory()
        entries = []
        try:
            with os.scandir(directory) as listing:
                for entry in listing:
                    if entry.name.startswith("."):
                        continue
                    path = os.path.realpath(entry.path)
                    if path != root and not path.startswith(root + os.sep):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        entries.append({"name": entry.name, "path": path,
                                        "directory": True, "size": 0, "mtime": 0})
                    elif (entry.is_file(follow_symlinks=False)
                          and entry.name.lower().endswith(VALID_GCODE_EXTS)):
                        stat = entry.stat(follow_symlinks=False)
                        entries.append({"name": entry.name, "path": path,
                                        "directory": False, "size": stat.st_size,
                                        "mtime": stat.st_mtime})
        except OSError as exc:
            raise RuntimeError("Unable to list files: %s" % exc)
        directories = sorted((e for e in entries if e["directory"]),
                             key=lambda e: e["name"].lower())
        files = sorted((e for e in entries if not e["directory"]),
                       key=lambda e: (-e["mtime"], e["name"].lower()))
        self.file_entries = directories + files

    def _render_file_browser(self):
        self._load_file_entries()
        max_page = max(0, (len(self.file_entries) - 1) // FILE_ROWS)
        self.file_page = max(0, min(self.file_page, max_page))
        title = "/" + self.current_directory if self.current_directory else "Print files"
        commands = self.renderer.begin_page(title, back=True)
        start = self.file_page * FILE_ROWS
        rows = self.file_entries[start:start + FILE_ROWS]
        for index, entry in enumerate(rows):
            y = 62 + index * 65
            label = ("[DIR] " if entry["directory"] else "") + entry["name"]
            commands += self.renderer.button("file.item%d" % index, 30, y, 740, 56,
                                             label, font="JetBrainsMono 12pt")
        commands += self.renderer.button("file.prev", 210, 390, 150, 50, "< Page",
                                         active=self.file_page > 0)
        commands.append(self.renderer.text(400, 415, "%d / %d" % (
            self.file_page + 1, max_page + 1), "ffffff", "Roboto 8pt",
            "center", "middle"))
        commands += self.renderer.button("file.next", 440, 390, 150, 50, "Page >",
                                         active=self.file_page < max_page)
        if not rows:
            commands.append(self.renderer.text(400, 230, "No G-code files", "606060",
                                               "Roboto 16pt", "center", "middle"))
        self.renderer.send(commands)

    def _handle_file_action(self, action):
        self._require_idle()
        if action == "file.prev":
            self.file_page = max(0, self.file_page - 1)
            self._render_file_browser()
        elif action == "file.next":
            self.file_page += 1
            self._render_file_browser()
        elif action == "file.start":
            self._start_selected_file()
        elif action.startswith("file.item"):
            index = int(action[len("file.item"):])
            offset = self.file_page * FILE_ROWS + index
            if offset >= len(self.file_entries):
                return
            entry = self.file_entries[offset]
            if entry["directory"]:
                root = os.path.realpath(self.virtual_sdcard.sdcard_dirname)
                self.current_directory = os.path.relpath(entry["path"], root)
                self.file_page = 0
                self._render_file_browser()
            else:
                self.selected_file = entry
                self._show_page(Page.FILE_CONFIRM)

    def _render_file_confirm(self):
        entry = self.selected_file
        commands = self.renderer.begin_page("Start print?", back=True)
        filename = self.renderer.truncate_text(
            entry["name"], 720, "JetBrainsMono Bold 16pt")
        commands.append(self.renderer.text(400, 150, filename,
                                           "ffffff", "Roboto Bold 16pt", "center", "middle"))
        commands.append(self.renderer.text(400, 220, self._format_size(entry["size"]),
                                           "00f0f0", "Roboto 12pt", "center", "middle"))
        commands += self.renderer.button("file.start", 220, 310, 360, 100,
                                         "START PRINT", font="Roboto Bold 16pt")
        self.renderer.send(commands)

    def _start_selected_file(self):
        self._require_idle()
        root = os.path.realpath(self.virtual_sdcard.sdcard_dirname)
        path = os.path.realpath(self.selected_file["path"])
        if not os.path.isfile(path) or not path.startswith(root + os.sep):
            raise RuntimeError("Selected file is no longer available")
        relpath = os.path.relpath(path, root)
        if any(ord(ch) < 32 for ch in relpath):
            raise RuntimeError("Unsupported filename")
        escaped = relpath.replace("\\", "\\\\").replace('"', '\\"')
        self.last_job_name = os.path.basename(relpath)
        self._run_script(
            'SDCARD_PRINT_FILE FILENAME="%s"' % escaped)

    def _render_print_page(self):
        paused = self.print_state == PrintState.PAUSED
        commands = self.renderer.begin_page("PAUSED" if paused else "PRINTING")
        filename = self.virtual_sdcard.file_path() or "Unknown"
        filename = os.path.basename(filename)
        filename = self.renderer.truncate_text(
            filename, 750, "JetBrainsMono Bold 12pt")
        commands.append(self.renderer.text(25, 78, filename,
                                           "35d9e6", "JetBrainsMono Bold 12pt",
                                           "left", "middle"))
        commands.append(self.renderer.text(
            25, 110,
            self.renderer.truncate_text(
                self.print_status_text, 750, "JetBrainsMono 8pt"),
            "d9e4e8", "JetBrainsMono 8pt", "left", "middle"))
        commands += [
            self.renderer.text(25, 142, "PROGRESS", "35d9e6",
                               "JetBrainsMono 8pt", "left", "middle"),
            self.renderer.stroke(25, 162, 750, 34, "295c66", 2),
            self.renderer.fill(25, 208, 750, 1, "295c66"),
            self.renderer.text(25, 226, "ELAPSED", "35d9e6",
                               "JetBrainsMono 8pt", "left", "middle"),
            self.renderer.text(410, 226, "REMAINING", "35d9e6",
                               "JetBrainsMono 8pt", "left", "middle"),
            self.renderer.fill(395, 216, 1, 48, "295c66"),
            self.renderer.fill(25, 273, 750, 1, "295c66"),
            self.renderer.text(25, 291, "LAYER", "35d9e6",
                               "JetBrainsMono 8pt", "left", "middle"),
            self.renderer.text(410, 291, "HEIGHT", "35d9e6",
                               "JetBrainsMono 8pt", "left", "middle"),
        ]
        commands += self.renderer.button("print.resume" if paused else "print.pause",
                                         20, 355, 175, 72,
                                         "RESUME" if paused else "PAUSE",
                                         state="busy" if self.pending_action in
                                         ("print.pause", "print.resume") else "enabled",
                                         font="JetBrainsMono Bold 12pt")
        commands += self.renderer.button("print.filament", 215, 355, 175, 72,
                                         "FILAMENT", font="JetBrainsMono Bold 12pt")
        commands += self.renderer.button(
            "print.z", 410, 355, 175, 72, "Z ADJUST",
            state="enabled" if self._z_adjust_allowed(self.reactor.monotonic())
            else "disabled", font="JetBrainsMono Bold 12pt")
        commands += self.renderer.button("print.cancel", 605, 355, 175, 72,
                                         "CANCEL", state="danger",
                                         font="JetBrainsMono Bold 12pt")
        self.renderer.send(commands)
        self._last_progress = self._last_time = None
        self._update_print_progress(self.reactor.monotonic())

    def _update_print_progress(self, eventtime):
        if self.page not in (Page.PRINTING, Page.PAUSED):
            return
        progress = int(self.display_status.get_status(eventtime)["progress"] * 100)
        stats = self.print_stats.get_status(eventtime)
        elapsed, remaining = self._print_time_values(eventtime, stats)
        info = stats.get("info", {})
        current, total = info.get("current_layer"), info.get("total_layer")
        layer = "%s / %s" % (current if current is not None else "?",
                              total if total is not None else "?")
        toolhead = self.toolhead.get_status(eventtime)
        position = toolhead.get("position", (0.0, 0.0, 0.0, 0.0))
        height = float(position[2])
        values = (self._clock_duration(elapsed),
                  self._clock_duration(remaining), layer, round(height, 2))
        if progress == self._last_progress and values == self._last_time:
            return
        self._last_progress, self._last_time = progress, values
        width = round(max(0, min(100, progress)) * 738 / 100)
        commands = [
            self.renderer.fill(650, 130, 125, 29),
            self.renderer.text(775, 142, "%d%%" % progress,
                               "35d9e6", "JetBrainsMono 12pt", "right", "middle"),
            self.renderer.fill(31, 168, 738, 22),
            self.renderer.fill(31, 168, width, 22, "35d9e6"),
            self.renderer.fill(25, 238, 350, 28),
            self.renderer.text(25, 252, values[0], "d9e4e8",
                               "JetBrainsMono 12pt", "left", "middle"),
            self.renderer.fill(410, 238, 350, 28),
            self.renderer.text(410, 252, values[1], "d9e4e8",
                               "JetBrainsMono 12pt", "left", "middle"),
            self.renderer.fill(25, 303, 350, 30),
            self.renderer.text(25, 318, values[2], "d9e4e8",
                               "JetBrainsMono 12pt", "left", "middle"),
            self.renderer.fill(410, 303, 350, 30),
            self.renderer.text(410, 318, "%.2f MM" % values[3], "d9e4e8",
                               "JetBrainsMono 12pt", "left", "middle"),
        ]
        self.renderer.send(commands)

    def _print_time_values(self, eventtime, stats=None):
        stats = stats or self.print_stats.get_status(eventtime)
        duration = float(stats.get("print_duration", 0.0) or 0.0)
        estimate = getattr(self.virtual_sdcard, "estimate_print_time", None)
        if not estimate:
            info = stats.get("info", {})
            current = info.get("current_layer")
            total = info.get("total_layer")
            if current and total:
                estimate = duration / max(current, 1) * total
            else:
                progress = self.display_status.get_status(eventtime)["progress"]
                estimate = duration / progress if progress > 0 else None
        if estimate is not None:
            estimate = max(duration, float(estimate))
        remaining = None if estimate is None else max(0.0, estimate - duration)
        return duration, remaining

    def _draw_print_status(self, status):
        self.renderer.send([
            self.renderer.fill(20, 94, 760, 34),
            self.renderer.text(
                25, 110,
                self.renderer.truncate_text(status, 750, "JetBrainsMono 8pt"),
                "d9e4e8", "JetBrainsMono 8pt", "left", "middle")])

    def _handle_print_action(self, action):
        stats = self.print_stats.get_status(self.reactor.monotonic())["state"]
        if action == "print.pause" and stats == "printing":
            self.pending_action = action
            self.pending_until = self.reactor.monotonic() + 10.0
            self._render_print_page()
            self._run_script("PAUSE")
        elif action == "print.resume" and stats == "paused":
            self.pending_action = action
            self.pending_until = self.reactor.monotonic() + 10.0
            self._render_print_page()
            self._run_script("RESUME")
        elif action == "print.filament" and stats in ("printing", "paused"):
            if stats == "printing":
                self._run_script("PAUSE")
            self._open_filament(True)
        elif action == "print.z" and stats in ("printing", "paused"):
            if not self._z_adjust_allowed(self.reactor.monotonic()):
                raise RuntimeError("Z adjust is available only on the first layer")
            self.z_session_adjust = 0.0
            self._show_page(Page.CALIBRATION_Z)
        elif action == "print.cancel" and stats in ("printing", "paused"):
            self._show_page(Page.CANCEL_CONFIRM)
        elif action == "print.cancel.confirm" and stats in ("printing", "paused"):
            self.pending_action = action
            self.pending_until = self.reactor.monotonic() + 30.0
            self.cancel_requested = True
            self.cancel_waiting_for_heat = self._temperature_wait_active()
            flow = self._print_flow_status()
            started = bool(getattr(getattr(self, "start_print_macro", None),
                                   "variables", {}).get(
                "print_started", False))
            if flow["active"] and not started:
                # START_PRINT owns the dispatcher. Request a cooperative abort
                # and let the macro stop at the next safe boundary. During a
                # temperature wait FEATHER_ABORT also sets the M108 flag.
                self.cancel_mode = "cooperative"
                self.cancel_phase = flow["phase"]
                self._render_cancel_confirm()
                self._run_immediate_command("FEATHER_ABORT")
                if not self._print_flow_status()["cancel_requested"]:
                    raise RuntimeError("START_PRINT did not accept cancellation")
            else:
                # The preparation macro has returned and virtual SD is now
                # printing regular G-code. Dispatch CANCEL_PRINT exactly once.
                self.cancel_mode = "direct"
                self.cancel_phase = "PRINTING"
                self._render_cancel_confirm()
                self._run_script("CANCEL_PRINT")
        elif action == "print.cancel.back":
            self._show_page(Page.PAUSED if stats == "paused" else Page.PRINTING)

    def _render_cancel_confirm(self):
        if self.pending_action == "print.cancel.confirm":
            label = self._cancel_progress_label()
            commands = self.renderer.begin_page("STOPPING PRINT")
            commands.append(self.renderer.text(
                400, 170, label, "f2c94c",
                "JetBrainsMono Bold 16pt", "center", "middle"))
            commands.append(self.renderer.text(
                400, 225, "CANCEL REQUEST ACCEPTED", "35d9e6",
                "JetBrainsMono 12pt", "center", "middle"))
            commands.append(self.renderer.text(
                400, 275, "REQUEST ACCEPTED // CONTROLS LOCKED", "56656c",
                "JetBrainsMono 8pt", "center", "middle"))
            for index in range(5):
                commands.append(self.renderer.fill(
                    290 + index * 48, 325, 32, 12,
                    "35d9e6" if index == self.busy_phase % 5 else "263238"))
            self.renderer.send(commands)
            self._last_cancel_label = label
            return
        commands = self.renderer.begin_page("Cancel print?", back=True)
        commands.append(self.renderer.text(400, 170, "The current print will stop",
                                           "ff9000", "Roboto 16pt", "center", "middle"))
        commands += self.renderer.button("print.cancel.back", 100, 285, 260, 100,
                                         "GO BACK", font="Roboto Bold 16pt")
        commands += self.renderer.button("print.cancel.confirm", 440, 285, 260, 100,
                                         "CANCEL PRINT",
                                         state="busy" if self.pending_action ==
                                         "print.cancel.confirm" else "danger",
                                         font="Roboto Bold 16pt")
        self.renderer.send(commands)

    def _print_flow_status(self):
        variables = getattr(getattr(self, "print_flow", None), "variables", {})
        return {"active": bool(variables.get("active", False)),
                "cancel_requested": bool(variables.get("cancel_requested", False)),
                "cancel_dispatched": bool(variables.get("cancel_dispatched", False)),
                "phase": str(variables.get("phase", "PRINTING")).upper()}

    def _cancel_progress_label(self):
        flow = self._print_flow_status()
        phase = (flow["phase"] if flow["active"] else
                 (getattr(self, "cancel_phase", None) or "PRINTING"))
        labels = {"PREPARING": "STOPPING PREPARATION...",
                  "HOMING": "STOPPING AFTER HOMING...",
                  "LEVELING": "STOPPING AFTER LEVELING...",
                  "PARKING": "STOPPING AFTER PARKING...",
                  "HEATING": "STOPPING HEAT WAIT...",
                  "PRIMING": "STOPPING AFTER PRIME LINE...",
                  "CANCELLING": "CANCEL_PRINT RUNNING...",
                  "PRINTING": "CANCELLING PRINT..."}
        return labels.get(phase, "STOPPING %s..." % phase)

    def _update_cancel_progress(self):
        if self.page != Page.CANCEL_CONFIRM or not self.cancel_requested:
            return
        label = self._cancel_progress_label()
        self.busy_phase = (self.busy_phase + 1) % 5
        if label == self._last_cancel_label:
            commands = []
        else:
            self._last_cancel_label = label
            commands = [self.renderer.fill(100, 140, 600, 65, "030607"),
                        self.renderer.text(400, 170, label, "f2c94c",
                                           "JetBrainsMono Bold 16pt", "center",
                                           "middle")]
        for index in range(5):
            commands.append(self.renderer.fill(
                290 + index * 48, 325, 32, 12,
                "35d9e6" if index == self.busy_phase % 5 else "263238"))
        self.renderer.send(commands)

    def _create_joystick_planner(self):
        now = self.reactor.monotonic()
        status = self.toolhead.get_status(now)
        kinematics = self.toolhead.get_kinematics()
        axis_maximum = status.get("axis_maximum", (110.0, 110.0, 230.0))
        z_maximum = max(0.0, float(axis_maximum[2]) - 10.0)
        xy_speed = float(status.get("max_velocity", 600.0))
        xy_accel = float(status.get("max_accel", 20000.0)) * 0.5
        z_speed = float(getattr(kinematics, "max_z_velocity", 25.0))
        z_accel = float(getattr(kinematics, "max_z_accel", 500.0)) * 0.5
        self.joystick = joystick_ui.JoystickPlanner(
            xy_speed, xy_accel, z_speed, z_accel,
            ((-110.0, 110.0), (-110.0, 110.0), (0.0, z_maximum)))
        logging.info(
            "[feather_screen] joystick limits xy=%.1f/%.1f z=%.1f/%.1f "
            "bounds=-110..110,-110..110,0..%.1f",
            xy_speed, xy_accel, z_speed, z_accel, z_maximum)

    def _stop_joystick(self):
        planner = getattr(self, "joystick", None)
        if planner is not None:
            planner.stop()
        self.joystick_action = None
        self.joystick_suppressed = None
        timer = getattr(self, "joystick_timer", None)
        if timer is not None:
            try:
                self.reactor.update_timer(timer, self.reactor.NEVER)
            except Exception:
                pass
        if (getattr(self, "joystick_queued", False)
                and getattr(self, "print_state", None) != PrintState.DESTROYED):
            try:
                # Finalizing the lookahead makes the last short segment end at
                # zero velocity instead of leaving an open-ended move queue.
                self.toolhead.flush_step_generation()
            except Exception:
                logging.exception("[feather_screen] joystick stop flush failed")
        self.joystick_queued = False

    def _queue_joystick_segment(self, segment):
        # Move captures these limits at construction time. Restore the global
        # toolhead settings immediately so Fluidd and later G-code retain the
        # configured printer limits.
        saved_accel = self.toolhead.max_accel
        try:
            self.toolhead.max_accel = min(saved_accel, segment.acceleration)
            self.toolhead._calc_junction_deviation()
            self.toolhead.manual_move(segment.position, segment.speed)
        finally:
            self.toolhead.max_accel = saved_accel
            self.toolhead._calc_junction_deviation()
        self.joystick_queued = True

    def _joystick_tick(self, eventtime):
        try:
            planner = self.joystick
            if (planner is None or self.page != Page.CONTROL_MOVE
                    or self.move_mode != "joystick"
                    or self.print_state != PrintState.IDLE):
                self._stop_joystick()
                return self.reactor.NEVER
            if planner.watchdog(eventtime):
                logging.warning("[feather_screen] joystick touch watchdog released")
                self.joystick_action = None
            homed = str(self.toolhead.get_status(eventtime).get("homed_axes", ""))
            if (planner.held and self.joystick_action == "move.joy.xy"
                    and ("x" not in homed or "y" not in homed)):
                planner.release()
            if (planner.held and self.joystick_action == "move.joy.z"
                    and "z" not in homed):
                planner.release()

            print_time, estimated_time, _empty = self.toolhead.check_busy(eventtime)
            if print_time - estimated_time > joystick_ui.MAX_QUEUE_AHEAD:
                return eventtime + 0.02
            segment = planner.advance(self.toolhead.get_position(),
                                      joystick_ui.PERIOD)
            if segment is None:
                if self.joystick_queued:
                    self.toolhead.flush_step_generation()
                    self.joystick_queued = False
                return self.reactor.NEVER
            self._queue_joystick_segment(segment)
            return eventtime + joystick_ui.PERIOD
        except Exception:
            logging.exception("[feather_screen] joystick motion failed")
            self._stop_joystick()
            return self.reactor.NEVER

    def _render_move(self):
        self._require_idle()
        snapshot = self._move_status_snapshot(self.reactor.monotonic())
        commands = self.renderer.begin_page("Move", back=True)
        if getattr(self, "move_mode", "step") == "joystick":
            commands += self._joystick_move_commands(snapshot)
        else:
            commands += self._step_move_commands(snapshot)
        self.renderer.send(commands)
        self._last_move = snapshot

    def _step_move_commands(self, snapshot):
        commands = []
        # XY pad and a separate Z rocker. The center is informational, not a
        # hidden homing action.
        commands += self.renderer.button("move.yp", 140, 78, 100, 68, "Y+")
        commands += self.renderer.button("move.xm", 30, 158, 100, 68, "X-")
        commands += self.renderer.button("move.xp", 250, 158, 100, 68, "X+")
        commands += self.renderer.button("move.ym", 140, 238, 100, 68, "Y-")
        commands += self.renderer.button("move.zp", 365, 78, 65, 68, "Z+")
        commands += self.renderer.button("move.zm", 365, 238, 65, 68, "Z-")
        commands.append(self.renderer.fill(445, 65, 1, 360, "295c66"))

        commands += self.renderer.button("move.homeall", 465, 170, 145, 50,
                                         "HOME ALL", font="JetBrainsMono 8pt")
        commands += self.renderer.button("move.homexy", 625, 170, 145, 50,
                                         "HOME XY", font="JetBrainsMono 8pt")
        commands.append(self.renderer.fill(465, 235, 305, 1, "295c66"))
        commands.append(self.renderer.text(617, 254, "STEP SIZE", "35d9e6",
                                           "JetBrainsMono 8pt", "center", "middle"))
        commands += self.renderer.button("move.step.minus", 465, 270, 80, 48, "-")
        commands.append(self.renderer.text(617, 294, "%g MM" % self.jog_step,
                                           "d9e4e8", "JetBrainsMono 8pt",
                                           "center", "middle"))
        commands += self.renderer.button("move.step.plus", 690, 270, 80, 48, "+")
        commands.append(self.renderer.text(617, 338, "PRESET STEPS", "35d9e6",
                                           "JetBrainsMono 8pt", "center", "middle"))
        for index, step in enumerate((0.1, 1.0, 10.0)):
            commands += self.renderer.button(
                "move.step%d" % index, 465 + index * 105, 358, 95, 50,
                "%g" % step,
                state="selected" if step == self.jog_step else "enabled",
                font="JetBrainsMono 8pt")
        commands += self.renderer.button("move.motors", 30, 350, 190, 58,
                                         "DISABLE MOTORS",
                                         font="JetBrainsMono 8pt")
        commands += self.renderer.button("move.mode", 235, 350, 195, 58,
                                         "[>STEP|JOY]",
                                         state="selected",
                                         font="JetBrainsMono 8pt")
        commands += self._move_status_commands(snapshot, axes=True)
        return commands

    def _joystick_move_commands(self, snapshot):
        commands = [
            self.renderer.fill(25, 75, 430, 285, "050c0f"),
            self.renderer.stroke(25, 75, 430, 285, "35d9e6", 2),
            self.renderer.fill(240, 87, 1, 261, "295c66"),
            self.renderer.fill(37, 220, 406, 1, "295c66"),
            self.renderer.text(240, 92, "Y+", "35d9e6", "JetBrainsMono 8pt",
                               "center", "middle"),
            self.renderer.text(240, 343, "Y-", "35d9e6", "JetBrainsMono 8pt",
                               "center", "middle"),
            self.renderer.text(43, 220, "X-", "35d9e6", "JetBrainsMono 8pt",
                               "left", "middle"),
            self.renderer.text(437, 220, "X+", "35d9e6", "JetBrainsMono 8pt",
                               "right", "middle"),
            self.renderer.text(240, 220, "+", "b47aff", "JetBrainsMono 16pt",
                               "center", "middle"),
            self.renderer.fill(485, 75, 110, 285, "050c0f"),
            self.renderer.stroke(485, 75, 110, 285, "b47aff", 2),
            self.renderer.fill(540, 87, 1, 261, "295c66"),
            self.renderer.fill(497, 220, 86, 1, "295c66"),
            self.renderer.text(540, 92, "UP / Z-", "b47aff",
                               "JetBrainsMono 8pt", "center", "middle"),
            self.renderer.text(540, 343, "DOWN / Z+", "b47aff",
                               "JetBrainsMono 8pt", "center", "middle"),
            self.renderer.text(540, 220, "+", "b47aff", "JetBrainsMono 16pt",
                               "center", "middle"),
            self.renderer.fill(615, 75, 160, 285, "050c0f"),
            self.renderer.stroke(615, 75, 160, 285, "295c66", 1),
            self.renderer.text(695, 94, snapshot[3],
                               "35d9e6" if snapshot[3] == "HOMED: XYZ"
                               else "f2c94c", "JetBrainsMono 8pt",
                               "center", "middle"),
            self.renderer.text(695, 125, "X %6.1f" % snapshot[0], "d9e4e8",
                               "JetBrainsMono 8pt", "center", "middle"),
            self.renderer.text(695, 151, "Y %6.1f" % snapshot[1], "d9e4e8",
                               "JetBrainsMono 8pt", "center", "middle"),
            self.renderer.text(695, 177, "Z %6.1f" % snapshot[2], "d9e4e8",
                               "JetBrainsMono 8pt", "center", "middle"),
            self.renderer.text(695, 208, "XY MAX %.0f" % self.joystick.xy_speed,
                               "56656c", "JetBrainsMono 8pt", "center", "middle"),
            self.renderer.text(695, 231, "Z MAX %.0f" % self.joystick.z_speed,
                               "56656c", "JetBrainsMono 8pt", "center", "middle"),
        ]
        commands += self.renderer.button("move.homeall", 627, 255, 136, 42,
                                         "HOME ALL", font="JetBrainsMono 8pt")
        commands += self.renderer.button("move.homexy", 627, 307, 136, 42,
                                         "HOME XY", font="JetBrainsMono 8pt")
        commands += self.renderer.button("move.mode", 25, 375, 205, 55,
                                         "[STEP|>JOY]", state="selected",
                                         font="JetBrainsMono 8pt")
        commands += self.renderer.button("move.motors", 245, 375, 210, 55,
                                         "DISABLE MOTORS",
                                         font="JetBrainsMono 8pt")
        commands += [
            self.renderer.text(485, 402, "HOLD + DRAG", "56656c",
                               "JetBrainsMono 8pt", "left", "middle"),
            self.renderer.action_hitbox("move.joy.xy", 25, 75, 430, 285, True),
            self.renderer.action_hitbox("move.joy.z", 485, 75, 110, 285, True),
        ]
        return commands

    def _move_status_snapshot(self, eventtime):
        status = self.toolhead.get_status(eventtime)
        position = status.get("position", (0.0, 0.0, 0.0, 0.0))
        homed = str(status.get("homed_axes", "")).lower()
        missing = "".join(axis.upper() for axis in "xyz" if axis not in homed)
        state = "HOMED: XYZ" if not missing else "NOT HOMED: %s" % missing
        return (round(position[0], 2), round(position[1], 2),
                round(position[2], 2), state,
                "x" in homed and "y" in homed, "z" in homed)

    def _move_status_commands(self, values, axes=False):
        missing = values[3] != "HOMED: XYZ"
        commands = [
            self.renderer.fill(465, 65, 305, 95, "030607"),
            self.renderer.text(617, 82, values[3],
                               "f2c94c" if missing else "35d9e6",
                               "JetBrainsMono 8pt", "center", "middle"),
            self.renderer.text(617, 122, "X %7.2f   Y %7.2f" % values[:2],
                               "d9e4e8", "JetBrainsMono 8pt", "center", "middle"),
            self.renderer.text(617, 148, "Z %7.2f" % values[2],
                               "d9e4e8", "JetBrainsMono 8pt", "center", "middle"),
        ]
        if axes:
            for x, width, label, homed in (
                    (140, 100, "X / Y", values[4]),
                    (365, 65, "Z", values[5])):
                color = "35d9e6" if homed else "f2c94c"
                commands += [
                    self.renderer.fill(x, 158, width, 68, "050c0f"),
                    self.renderer.stroke(x, 158, width, 68, color, 2),
                    self.renderer.text(x + width // 2, 180, label, color,
                                       "JetBrainsMono 8pt", "center", "middle"),
                    self.renderer.text(x + width // 2, 207,
                                       "HOMED" if homed else "HOME", color,
                                       "JetBrainsMono 8pt", "center", "middle"),
                ]
        return commands

    def _update_move_status(self, eventtime):
        values = self._move_status_snapshot(eventtime)
        previous = getattr(self, "_last_move", None)
        if values == previous:
            return
        self._last_move = values
        if getattr(self, "move_mode", "step") == "joystick":
            self.renderer.send(self._joystick_position_commands(values))
            return
        axes_changed = previous is None or values[4:] != previous[4:]
        self.renderer.send(self._move_status_commands(values, axes=axes_changed))

    def _joystick_position_commands(self, values):
        return [
            self.renderer.fill(620, 80, 150, 112, "050c0f"),
            self.renderer.text(695, 94, values[3],
                               "35d9e6" if values[3] == "HOMED: XYZ"
                               else "f2c94c", "JetBrainsMono 8pt",
                               "center", "middle"),
            self.renderer.text(695, 125, "X %6.1f" % values[0], "d9e4e8",
                               "JetBrainsMono 8pt", "center", "middle"),
            self.renderer.text(695, 151, "Y %6.1f" % values[1], "d9e4e8",
                               "JetBrainsMono 8pt", "center", "middle"),
            self.renderer.text(695, 177, "Z %6.1f" % values[2], "d9e4e8",
                               "JetBrainsMono 8pt", "center", "middle"),
        ]

    def _handle_move_action(self, action):
        self._require_idle()
        if action == "move.mode":
            self._stop_joystick()
            self.move_mode = ("joystick" if self.move_mode == "step"
                              else "step")
            self._render_move()
            return
        if action.startswith("move.step"):
            steps = (0.1, 1.0, 10.0)
            if action == "move.step.minus":
                index = max(0, steps.index(self.jog_step) - 1)
            elif action == "move.step.plus":
                index = min(len(steps) - 1, steps.index(self.jog_step) + 1)
            else:
                index = int(action[-1])
            self.jog_step = steps[index]
            self._render_move()
            return
        commands = {
            "move.homeall": "G28", "move.homexy": "G28 X Y",
            "move.motors": "M84",
        }
        if action in commands:
            self._stop_joystick()
            if action.startswith("move.home"):
                self._run_blocking_gcode(commands[action], "HOMING...")
            else:
                self._run_script(commands[action])
            self._toast("Homing started" if action.startswith("move.home")
                        else "Motors disabled")
            return
        moves = {
            "move.xp": ("x", self.jog_step, 6000),
            "move.xm": ("x", -self.jog_step, 6000),
            "move.yp": ("y", self.jog_step, 6000),
            "move.ym": ("y", -self.jog_step, 6000),
            "move.zp": ("z", self.jog_step, 600),
            "move.zm": ("z", -self.jog_step, 600),
        }
        if action in moves:
            axis, distance, speed = moves[action]
            homed = self.toolhead.get_status(self.reactor.monotonic())["homed_axes"]
            if axis not in homed:
                raise RuntimeError("Home %s before moving" % axis.upper())
            self._run_script(
                "MOVE_SAFE %s=%g F=%d" % (axis.upper(), distance, speed))
            self._toast("Moved %s %g mm" % (axis.upper(), distance))

    def _render_heat(self):
        self._require_idle()
        now = self.reactor.monotonic()
        extruder = self.extruder.get_status(now)
        bed = self.heater_bed.get_status(now)
        fan_speed = (self.fan.get_status(now).get("speed", 0.0) * 100
                     if self.fan is not None else 0.0)
        commands = self.renderer.begin_page("Heat / fan", back=True)
        rows = (("NOZZLE", 95, "selected", "heat.e"),
                ("BED", 175, "warning", "heat.b"))
        for label, y, state, prefix in rows:
            commands.append(self.renderer.text(30, y, label,
                                               "b47aff" if state == "selected" else "f2c94c",
                                               "JetBrainsMono 12pt", "left", "middle"))
            commands += self.renderer.button(prefix + "minus", 430, y - 25, 100, 50,
                                             "-5", state=state)
            commands += self.renderer.button(prefix + "plus", 550, y - 25, 100, 50,
                                             "+5", state=state)
            commands += self.renderer.button(prefix + "off", 670, y - 25, 100, 50,
                                             "OFF", state=state)
            commands.append(self.renderer.fill(30, y + 39, 740, 1, "295c66"))
        commands.append(self.renderer.text(30, 255, "FAN", "35d9e6",
                                           "JetBrainsMono 12pt", "left", "middle"))
        commands.append(self.renderer.text(
            270, 255, "%.0f%%" % fan_speed if self.fan is not None else "N/A",
            "d9e4e8" if self.fan is not None else "56656c",
            "JetBrainsMono 12pt", "center", "middle"))
        for index, percent in enumerate((0, 50, 100)):
            commands += self.renderer.button("heat.fan%d" % percent,
                                             430 + index * 120, 230, 100, 50,
                                             "%d%%" % percent,
                                             active=self.fan is not None)
        commands.append(self.renderer.text(400, 315, "PREHEAT PRESETS", "35d9e6",
                                           "JetBrainsMono 8pt", "center", "middle"))
        for index, material in enumerate(("PLA", "PETG", "ABS", "ABS-PC")):
            commands += self.renderer.button("heat.preheat.%s" % material,
                                             25 + index * 190, 335, 170, 42,
                                             material, font="JetBrainsMono 8pt")
        commands += self.renderer.button("heat.alloff", 25, 392, 745, 42,
                                         "COOLDOWN", state="danger",
                                         font="JetBrainsMono 12pt")
        commands.append(self.renderer.text(
            270, 95, "%.1f / %.0f C" %
            (extruder["temperature"], extruder["target"]),
            "d9e4e8", "JetBrainsMono 12pt", "center", "middle"))
        commands.append(self.renderer.text(
            270, 175, "%.1f / %.0f C" %
            (bed["temperature"], bed["target"]),
            "d9e4e8", "JetBrainsMono 12pt", "center", "middle"))
        self.renderer.send(commands)
        self._last_heat = (round(extruder["temperature"], 1), round(extruder["target"]),
                           round(bed["temperature"], 1), round(bed["target"]),
                           round(fan_speed))

    def _update_heat_status(self, eventtime):
        extruder = self.extruder.get_status(eventtime)
        bed = self.heater_bed.get_status(eventtime)
        fan_speed = (self.fan.get_status(eventtime).get("speed", 0.0) * 100
                     if self.fan is not None else 0.0)
        values = (round(extruder["temperature"], 1), round(extruder["target"]),
                  round(bed["temperature"], 1), round(bed["target"]),
                  round(fan_speed))
        if values == self._last_heat:
            return
        self._last_heat = values
        self.renderer.send([
            self.renderer.fill(145, 70, 250, 50),
            self.renderer.text(270, 95, "%.1f / %.0f C" % values[:2],
                               "d9e4e8", "JetBrainsMono 12pt", "center", "middle"),
            self.renderer.fill(145, 150, 250, 50),
            self.renderer.text(270, 175, "%.1f / %.0f C" % values[2:4],
                               "d9e4e8", "JetBrainsMono 12pt", "center", "middle"),
            self.renderer.fill(145, 230, 250, 50),
            self.renderer.text(270, 255,
                               "%.0f%%" % values[4] if self.fan is not None else "N/A",
                               "d9e4e8" if self.fan is not None else "56656c",
                               "JetBrainsMono 12pt", "center", "middle"),
        ])

    def _handle_heat_action(self, action):
        self._require_idle()
        now = self.reactor.monotonic()
        if action.startswith("heat.e"):
            target = self.extruder.get_status(now)["target"]
            if action == "heat.eplus": target += 5
            elif action == "heat.eminus": target -= 5
            else: target = 0
            target = self._clamp_heater_target(target, self.extruder.heater, 300)
            self._run_script("M104 S%.0f" % target)
        elif action.startswith("heat.b"):
            target = self.heater_bed.get_status(now)["target"]
            if action == "heat.bplus": target += 5
            elif action == "heat.bminus": target -= 5
            else: target = 0
            target = self._clamp_heater_target(target, self.heater_bed, 130)
            self._run_script("M140 S%.0f" % target)
        elif action == "heat.alloff":
            self._run_script("TURN_OFF_HEATERS")
            self._toast("Heaters turned off")
        elif action.startswith("heat.preheat."):
            material = action.rsplit(".", 1)[1]
            nozzle, bed = self._limited_preheat(material)
            self.filament_material = material
            self._run_script(
                "PREHEAT_MATERIAL MATERIAL=%s EXTRUDER_TEMP=%.0f BED_TEMP=%.0f" %
                (material, nozzle, bed))
            self._toast("%s preheat: %.0f/%.0fC" % (material, nozzle, bed))
        elif action.startswith("heat.fan"):
            if getattr(self, "fan", None) is None:
                raise RuntimeError("Part fan is not configured")
            percent = int(action[len("heat.fan"):])
            self._run_script("M106 S%d" % round(percent * 255 / 100))
            self._toast("Fan: %d%%" % percent)
        self.reactor.register_callback(self._refresh_heat_after_action,
                                      self.reactor.monotonic() + 0.1)

    def _refresh_heat_after_action(self, eventtime):
        self._render_heat()
        if self.toast_until > eventtime:
            self.renderer.toast(self.toast_message)

    @staticmethod
    def _clamp_heater_target(target, heater, default_max):
        if target <= 0:
            return 0
        minimum = max(0, getattr(heater, "min_temp", 0))
        maximum = max(minimum, getattr(heater, "max_temp", default_max) - 1)
        return max(minimum, min(target, maximum))

    def _limited_preheat(self, material):
        nozzle, bed = getattr(self, "preheat", PREHEAT).get(material,
                                                            PREHEAT[material])
        return (self._clamp_heater_target(nozzle, self.extruder.heater, 300),
                self._clamp_heater_target(bed, self.heater_bed, 130))

    def _open_filament(self, from_pause):
        if not from_pause:
            self._require_idle()
        now = self.reactor.monotonic()
        self.filament_from_pause = from_pause
        self.filament_original_target = self.extruder.get_status(now)["target"]
        self._show_page(Page.FILAMENT_MATERIAL)

    def _render_filament_material(self):
        commands = self.renderer.begin_page("Select material", back=True)
        for index, material in enumerate(("PLA", "PETG", "ABS", "ABS-PC")):
            x = 35 + (index % 2) * 380
            y = 80 + (index // 2) * 165
            nozzle = self._limited_preheat(material)[0]
            commands += self.renderer.button("filament.%s" % material, x, y, 350, 135,
                                             "%s  %.0fC" % (material, nozzle),
                                             font="Roboto Bold 16pt")
        self.renderer.send(commands)

    def _render_filament_action(self):
        now = self.reactor.monotonic()
        status = self.extruder.get_status(now)
        minimum = getattr(self.extruder, "min_extrude_temp", 170.0)
        hot = status["temperature"] >= minimum
        commands = self.renderer.begin_page("Filament - %s" % self.filament_material,
                                            back=True)
        commands.append(self.renderer.text(
            400, 80, "Nozzle %.1f / %.0fC" % (status["temperature"], status["target"]),
            "ffb000" if not hot else "00f0f0", "Roboto Bold 14pt", "center"))
        if not hot:
            commands.append(self.renderer.text(400, 120, "Heating - please wait",
                                               "ffffff", "Roboto 10pt", "center"))
        state = "enabled" if hot else "disabled"
        commands += self.renderer.button("filament.load", 35, 165, 220, 100,
                                         "LOAD", state=state, font="Roboto Bold 14pt")
        commands += self.renderer.button("filament.unload", 290, 165, 220, 100,
                                         "UNLOAD", state=state, font="Roboto Bold 14pt")
        commands += self.renderer.button("filament.purge", 545, 165, 220, 100,
                                         "PURGE", state=state, font="Roboto Bold 14pt")
        if self.filament_from_pause:
            commands += self.renderer.button("filament.resume", 220, 315, 360, 95,
                                             "DONE & RESUME", font="Roboto Bold 16pt")
        else:
            commands += self.renderer.button("filament.done", 220, 315, 360, 95,
                                             "DONE", font="Roboto Bold 16pt")
        self.renderer.send(commands)
        self._last_filament_heat = (round(status["temperature"], 1),
                                    round(status["target"]), hot)

    def _update_filament_status(self, eventtime):
        status = self.extruder.get_status(eventtime)
        minimum = getattr(self.extruder, "min_extrude_temp", 170.0)
        values = (round(status["temperature"], 1), round(status["target"]),
                  status["temperature"] >= minimum)
        if values == self._last_filament_heat:
            return
        if self._last_filament_heat is None or values[2] != self._last_filament_heat[2]:
            self._render_filament_action()
            return
        self._last_filament_heat = values
        self.renderer.send([
            self.renderer.fill(150, 58, 500, 70),
            self.renderer.text(400, 80, "Nozzle %.1f / %.0fC" % values[:2],
                               "00f0f0" if values[2] else "ffb000",
                               "Roboto Bold 14pt", "center"),
        ])

    def _handle_filament_action(self, action):
        state = self.print_stats.get_status(self.reactor.monotonic())["state"]
        if self.filament_from_pause:
            if state != "paused":
                raise RuntimeError("Filament change requires a paused print")
        else:
            self._require_idle()
        if action.startswith("filament.") and action.split(".", 1)[1] in PREHEAT:
            self.filament_material = action.split(".", 1)[1]
            target = self._limited_preheat(self.filament_material)[0]
            self._run_script("SET_MATERIAL MATERIAL=%s\nM104 S%.0f" %
                             (self.filament_material, target))
            self._show_page(Page.FILAMENT_ACTION)
            return
        now = self.reactor.monotonic()
        if action in ("filament.load", "filament.unload", "filament.purge"):
            minimum = getattr(self.extruder, "min_extrude_temp", 170.0)
            if self.extruder.get_status(now)["temperature"] < minimum:
                raise RuntimeError("Nozzle is not hot enough")
            macro = {"filament.load": "LOAD_FILAMENT",
                     "filament.unload": "UNLOAD_FILAMENT",
                     "filament.purge": "PURGE_FILAMENT"}[action]
            command = ("%s MATERIAL=%s" % (macro, self.filament_material)
                       if macro == "LOAD_FILAMENT" else macro)
            self._run_blocking_gcode(
                command, macro.replace("_", " ") + "...")
            self._toast(macro.replace("_", " ").title())
        elif action == "filament.done":
            self._finish_filament(False)
        elif action == "filament.resume":
            self._finish_filament(True)

    def _finish_filament(self, resume):
        if not self.filament_from_pause:
            self._run_script("M104 S%.0f" % self.filament_original_target)
            self._show_page(Page.IDLE_HOME)
            return
        target = self.filament_original_target
        if target > 0:
            self._run_script("M104 S%.0f" % target)
        if resume and self.print_stats.get_status(self.reactor.monotonic())["state"] == "paused":
            self._run_script("RESUME")
        self._show_page(Page.PAUSED)

    def _render_calibration_home(self):
        commands = self.renderer.begin_page("Calibration", back=True)
        saved = float(self._setting("z_offset", 0.0))
        commands += self.renderer.button(
            "cal.z", 30, 75, 740, 90, "Z OFFSET",
            font="JetBrainsMono 16pt",
            subtitle=("SAVED %+.3f MM" % saved, "SET NOZZLE HEIGHT"),
            layout="row")
        commands += self.renderer.button(
            "cal.screws", 30, 185, 740, 90, "BED SCREWS",
            font="JetBrainsMono 16pt",
            subtitle=("LEVEL BED USING", "ADJUSTMENT SCREWS"), layout="row")
        commands += self.renderer.button(
            "cal.mesh", 30, 295, 740, 90, "BED MESH",
            font="JetBrainsMono 16pt",
            subtitle=("PROBE BED AND CREATE", "PROFILE AUTO"), layout="row")
        commands.append(self.renderer.text(
            400, 415, "PRINTER MUST BE IDLE", "56656c",
            "JetBrainsMono 8pt", "center"))
        self.renderer.send(commands)

    def _render_calibration_z(self):
        now = self.reactor.monotonic()
        live = self.print_state in (PrintState.PRINTING, PrintState.PAUSED)
        allowed = not live or self._z_adjust_allowed(now)
        current = self.gcode_move.get_status(now)["homing_origin"][2]
        saved = float(self._setting("z_offset", 0.0))
        commands = self.renderer.begin_page("First layer Z" if live else "Z offset", back=True)
        commands.append(self.renderer.text(400, 82, "Current %+.3f mm   Saved %+.3f mm" %
                                           (current, saved), "ffffff", "Roboto 12pt",
                                           "center"))
        if live:
            commands.append(self.renderer.text(400, 120,
                                               "Session %+.3f / %.2f mm" %
                                               (self.z_session_adjust,
                                                self.z_adjust_session_limit),
                                               "00f0f0", "Roboto 10pt", "center"))
        for index, step in enumerate((0.01, 0.05)):
            commands += self.renderer.button("z.step.%s" % ("001" if index == 0 else "005"),
                                             210 + index * 200, 150, 180, 65,
                                             "%.2f mm" % step,
                                             state="selected" if step == self.z_step
                                             else "enabled")
        state = "enabled" if allowed else "disabled"
        commands += self.renderer.button("z.closer", 70, 250, 300, 100, "CLOSER",
                                         state=state, font="Roboto Bold 16pt")
        commands += self.renderer.button("z.farther", 430, 250, 300, 100, "FARTHER",
                                         state=state, font="Roboto Bold 16pt")
        if not live:
            load = bool(self._setting("load_zoffset", 0))
            commands += self.renderer.button("z.load.toggle", 70, 375, 300, 55,
                                             "LOAD SAVED: %s" % ("ON" if load else "OFF"),
                                             state="selected" if load else "enabled")
            commands += self.renderer.button("z.reset", 430, 375, 300, 55,
                                             "RESET", state="danger")
        self.renderer.send(commands)

    def _handle_calibration_action(self, action):
        if action == "cal.z":
            self._require_idle()
            self.z_session_adjust = 0.0
            self._show_page(Page.CALIBRATION_Z)
        elif action in ("cal.screws", "cal.mesh"):
            self._require_idle()
            self.calibration_kind = action.split(".", 1)[1]
            self.calibration_material = "PLA"
            self._show_page(Page.CALIBRATION_CONFIRM)
        elif action.startswith("cal.material."):
            self.calibration_material = action.rsplit(".", 1)[1]
            self._render_calibration_confirm()
        elif action == "cal.confirm":
            self._require_idle()
            self.calibration_results = []
            self.calibration_mesh = []
            self.calibration_error = None
            self.print_status_text = ("RESETTING Z" if self.calibration_kind == "zreset"
                                      else "CALIBRATION: STARTING")
            self._show_page(Page.CALIBRATION_PROGRESS)
            self.reactor.register_callback(self._run_calibration)
        elif action == "cal.repeat":
            self._show_page(Page.CALIBRATION_CONFIRM)
        elif action == "cal.done":
            self._show_page(Page.CALIBRATION_HOME)
        elif action.startswith("z.step."):
            self.z_step = 0.01 if action.endswith("001") else 0.05
            self._render_calibration_z()
        elif action in ("z.closer", "z.farther"):
            self._apply_z_adjust(self.z_step if action == "z.closer" else -self.z_step)
        elif action == "z.load.toggle":
            self._require_idle()
            value = 0 if self._setting("load_zoffset", 0) else 1
            self._run_script("SET_MOD PARAM=load_zoffset VALUE=%d" % value)
            self._render_calibration_z()
        elif action == "z.reset":
            self._require_idle()
            self.calibration_kind = "zreset"
            self._show_page(Page.CALIBRATION_CONFIRM)

    def _apply_z_adjust(self, delta):
        now = self.reactor.monotonic()
        live = self.print_state in (PrintState.PRINTING, PrintState.PAUSED)
        if live and not self._z_adjust_allowed(now):
            raise RuntimeError("Z adjust is available only on the first layer")
        if abs(self.z_session_adjust + delta) > self.z_adjust_session_limit + 0.0001:
            raise RuntimeError("Z adjustment session limit reached")
        current = self.gcode_move.get_status(now)["homing_origin"][2]
        if abs(current + delta) > self.z_offset_limit + 0.0001:
            raise RuntimeError("Z offset safety limit reached")
        self._run_blocking_gcode(
            "SET_GCODE_OFFSET Z_ADJUST=%+.3f MOVE=1" % delta,
            "ADJUSTING Z...")
        self.z_session_adjust += delta
        self._render_calibration_z()
        self._toast("Z %+.3f mm" % self.z_session_adjust)

    def _z_adjust_allowed(self, eventtime):
        stats = self.print_stats.get_status(eventtime)
        if stats.get("state") not in ("printing", "paused"):
            return False
        layer = stats.get("info", {}).get("current_layer")
        return layer is not None and layer in (0, 1)

    def _render_calibration_confirm(self):
        kind = self.calibration_kind
        commands = self.renderer.begin_page("Confirm calibration", back=True)
        if kind == "zreset":
            text = "Reset saved and current Z offset to zero?"
        elif kind == "screws":
            text = "Printer will heat, home and probe. Clean the nozzle first."
        else:
            text = "Printer will heat, clean, home and replace mesh profile 'auto'."
        for index, line in enumerate(self._wrap(text, 52, 3)):
            commands.append(self.renderer.text(400, 85 + index * 32, line,
                                               "ffffff", "Roboto 10pt", "center"))
        if kind in ("screws", "mesh"):
            for index, material in enumerate(("PLA", "PETG", "ABS")):
                commands += self.renderer.button("cal.material.%s" % material,
                                                 115 + index * 195, 190, 180, 60,
                                                 material,
                                                 state="selected" if material ==
                                                 self.calibration_material else "enabled")
        commands += self.renderer.button("cal.confirm", 220, 300, 360, 100,
                                         "START" if kind != "zreset" else "RESET",
                                         state="danger" if kind == "zreset" else "enabled",
                                         font="Roboto Bold 16pt")
        self.renderer.send(commands)

    def _render_calibration_progress(self):
        label = "Resetting Z..." if self.calibration_kind == "zreset" else (
            self.print_status_text or "Calibration running...")
        commands = self.renderer.begin_page("Calibration")
        commands.append(self.renderer.text(400, 142, self._shorten(label, 44),
                                           "35d9e6", "JetBrainsMono Bold 12pt",
                                           "center"))
        commands += self._calibration_stage_commands(label)
        if self.calibration_kind == "mesh":
            commands.append(self.renderer.text(
                400, 335, "DO NOT POWER OFF // NO SAFE SOFTWARE CANCEL",
                "f2c94c", "JetBrainsMono 8pt", "center"))
        self.renderer.send(commands)
        self._last_calibration_label = label

    def _update_calibration_progress(self):
        label = self.print_status_text or "Calibration running..."
        if label == self._last_calibration_label:
            return
        self._last_calibration_label = label
        commands = [self.renderer.fill(40, 105, 720, 205, "030607"),
                    self.renderer.text(400, 142, self._shorten(label, 44),
                                       "35d9e6", "JetBrainsMono Bold 12pt",
                                       "center")]
        commands += self._calibration_stage_commands(label)
        self.renderer.send(commands)

    def _calibration_stage_commands(self, label):
        text = str(label).upper()
        if self.calibration_kind == "screws":
            stages = ("PREP", "HOME", "HEAT", "PROBE", "DONE")
        else:
            stages = ("PREP", "HEAT", "CLEAN", "HOME", "LEVEL")
        index = 0
        markers = (("COMPLETE", 5), ("LEVEL", 4), ("PROB", 4),
                   ("HOM", 3), ("CLEAN", 2), ("HEAT", 2),
                   ("PREP", 1), ("START", 1))
        for marker, value in markers:
            if marker in text:
                index = value
                break
        commands = []
        for position, stage in enumerate(stages):
            x = 55 + position * 142
            reached = position < index
            active = position == max(0, index - 1) and index < 5
            color = "35d9e6" if reached else ("f2c94c" if active else "263238")
            commands += [self.renderer.fill(x, 225, 118, 38, "050c0f"),
                         self.renderer.stroke(x, 225, 118, 38, color, 2),
                         self.renderer.text(x + 59, 244, stage, color,
                                            "JetBrainsMono 8pt", "center", "middle")]
        return commands

    def _run_calibration(self, eventtime):
        try:
            self._require_idle()
            if self.calibration_kind == "zreset":
                self._run_script(
                    "SET_GCODE_OFFSET Z=0 MOVE=1\nSET_MOD PARAM=z_offset VALUE=0")
            else:
                nozzle, bed = self._limited_preheat(self.calibration_material)
                if self.calibration_kind == "screws":
                    command = "BED_LEVEL_SCREWS_TUNE EXTRUDER_TEMP=130 BED_TEMP=%.0f" % bed
                else:
                    command = ("AUTO_FULL_BED_LEVEL EXTRUDER_TEMP=%.0f BED_TEMP=%.0f "
                               "PROFILE=auto" % (nozzle, bed))
                self._run_script(command)
                if self.calibration_kind == "mesh":
                    self.calibration_mesh = self._read_mesh_matrix(eventtime)
        except Exception as exc:
            logging.exception("[feather_screen] calibration failed")
            self.calibration_error = str(exc)
        self._show_page(Page.CALIBRATION_RESULT)

    @staticmethod
    def normalize_mesh_matrix(value):
        if not isinstance(value, (list, tuple)):
            return []
        matrix = []
        width = None
        for row in value:
            if not isinstance(row, (list, tuple)):
                return []
            try:
                normalized = [float(cell) for cell in row]
            except (TypeError, ValueError):
                return []
            if not normalized or (width is not None and len(normalized) != width):
                return []
            width = len(normalized)
            matrix.append(normalized)
        return matrix

    def _read_mesh_matrix(self, eventtime):
        mesh = getattr(self, "bed_mesh", None)
        if mesh is None:
            return []
        status = mesh.get_status(eventtime)
        for key in ("probed_matrix", "mesh_matrix"):
            matrix = self.normalize_mesh_matrix(status.get(key))
            if matrix:
                return matrix
        profile = status.get("profiles", {}).get("auto", {})
        return self.normalize_mesh_matrix(profile.get("points"))

    @staticmethod
    def _mesh_color(value, minimum, maximum):
        if maximum <= minimum:
            return "35d9e6"
        ratio = (value - minimum) / (maximum - minimum)
        colors = ("244c66", "35d9e6", "56c596", "f2c94c", "ff4d5a")
        return colors[min(len(colors) - 1, int(ratio * len(colors)))]

    SCREW_RESULT = re.compile(r"^([^:]+).*adjust\s+(CW|CCW)\s+([0-9]+:[0-9]+)", re.I)
    SCREW_BASE = re.compile(r"^([^:]+)\s+\(base\)\s*:", re.I)

    @classmethod
    def parse_screw_result(cls, message):
        match = cls.SCREW_RESULT.search(str(message).strip())
        if match:
            return {"name": match.group(1).strip(),
                    "direction": match.group(2).upper(), "turns": match.group(3)}
        match = cls.SCREW_BASE.search(str(message).strip())
        if match:
            return {"name": match.group(1).strip(), "direction": "BASE", "turns": "-"}
        return None

    def _handle_gcode_output(self, message):
        if self.calibration_kind != "screws" or self.page != Page.CALIBRATION_PROGRESS:
            return
        result = self.parse_screw_result(message)
        if result:
            self.calibration_results.append(result)

    def _render_calibration_result(self):
        commands = self.renderer.begin_page("Calibration result")
        if self.calibration_error:
            commands.append(self.renderer.text(400, 120,
                                               self._shorten(self.calibration_error, 70),
                                               "ff3030", "Roboto 10pt", "center"))
        elif self.calibration_kind == "mesh" and self.calibration_mesh:
            matrix = self.calibration_mesh
            values = [cell for row in matrix for cell in row]
            minimum, maximum = min(values), max(values)
            rows, columns = len(matrix), len(matrix[0])
            map_x, map_y, map_width, map_height = 35, 75, 575, 245
            cell_width = max(24, map_width // columns)
            cell_height = max(24, map_height // rows)
            for row_index, row in enumerate(reversed(matrix)):
                for column, value in enumerate(row):
                    x = map_x + column * cell_width
                    y = map_y + row_index * cell_height
                    color = self._mesh_color(value, minimum, maximum)
                    commands += [self.renderer.fill(x, y, cell_width - 3,
                                                    cell_height - 3, color),
                                 self.renderer.text(x + (cell_width - 3) // 2,
                                                    y + (cell_height - 3) // 2,
                                                    "%+.2f" % value, "030607",
                                                    "JetBrainsMono Bold 8pt",
                                                    "center", "middle")]
            commands += [
                self.renderer.text(640, 92, "PROFILE AUTO", "35d9e6",
                                   "JetBrainsMono 8pt", "left", "middle"),
                self.renderer.text(640, 145, "MIN %+.3f" % minimum, "d9e4e8",
                                   "JetBrainsMono 8pt", "left", "middle"),
                self.renderer.text(640, 185, "MAX %+.3f" % maximum, "d9e4e8",
                                   "JetBrainsMono 8pt", "left", "middle"),
                self.renderer.text(640, 225, "RANGE %.3f" % (maximum - minimum),
                                   "f2c94c", "JetBrainsMono 8pt", "left", "middle"),
                self.renderer.text(640, 285, "%d X %d POINTS" % (columns, rows),
                                   "56656c", "JetBrainsMono 8pt", "left", "middle"),
            ]
        elif self.calibration_kind == "screws" and self.calibration_results:
            for index, result in enumerate(self.calibration_results[:5]):
                commands.append(self.renderer.text(
                    100, 75 + index * 48, result["name"], "ffffff", "Roboto 10pt"))
                commands.append(self.renderer.text(
                    700, 75 + index * 48, "%s %s" %
                    (result["direction"], result["turns"]), "00f0f0", "Roboto 10pt",
                    "right"))
        else:
            commands.append(self.renderer.text(400, 150, "Calibration completed",
                                               "00f0f0", "Roboto Bold 14pt", "center"))
        commands += self.renderer.button("cal.repeat", 100, 355, 260, 70, "REPEAT")
        commands += self.renderer.button("cal.done", 440, 355, 260, 70, "DONE")
        self.renderer.send(commands)

    def _render_settings(self):
        brightness = int(self._setting("backlight", 50))
        eco = int(self._setting("backlight_eco", 10))
        sound = bool(self._setting("sound", 1))
        theme = str(getattr(self.renderer, "theme_name", "DEFAULT"))
        commands = self.renderer.begin_page("Settings", back=True)
        rows = (("SCREEN BRIGHTNESS", brightness, "settings.brightness", 67),
                ("ECO BRIGHTNESS", eco, "settings.eco", 151))
        for label, value, prefix, y in rows:
            commands += [
                self.renderer.fill(25, y, 750, 70, "050c0f"),
                self.renderer.stroke(25, y, 750, 70, "295c66", 1),
                self.renderer.text(44, y + 22, label, "35d9e6",
                                   "JetBrainsMono Bold 8pt"),
                self.renderer.text(425, y + 36, "%d%%" % value, "d9e4e8",
                                   "JetBrainsMono 12pt", "center"),
            ]
            commands += self.renderer.button(prefix + ".minus", 525, y + 12,
                                             105, 46, "-5")
            commands += self.renderer.button(prefix + ".plus", 650, y + 12,
                                             105, 46, "+5")
        commands += [
            self.renderer.fill(25, 235, 750, 66, "050c0f"),
            self.renderer.stroke(25, 235, 750, 66, "295c66", 1),
            self.renderer.text(44, 268, "SOUND FEEDBACK", "35d9e6",
                               "JetBrainsMono Bold 8pt"),
        ]
        commands += self.renderer.toggle("settings.sound", 679, 249, 76, 38,
                                         sound)
        commands += self.renderer.button(
            "settings.theme", 25, 317, 360, 100, "COLOR THEME",
            subtitle=theme.replace("_", " "), layout="center",
            font="JetBrainsMono Bold 8pt")
        commands += self.renderer.button(
            "settings.mod", 415, 317, 360, 100, "MOD PARAMETERS",
            subtitle="ALL FORGE-X OPTIONS", layout="center",
            font="JetBrainsMono Bold 8pt")
        self.renderer.send(commands)

    def _handle_settings_action(self, action):
        self._require_idle()
        if action == "settings.theme":
            parameters = self._mod_parameters()
            for index, param in enumerate(parameters):
                if param.key == "feather_theme":
                    self._open_mod_parameter(index, Page.SETTINGS)
                    return
            raise RuntimeError("Feather theme parameter is unavailable")
        if action == "settings.mod":
            self.mod_page = 0
            self.mod_parameter = None
            self._show_page(Page.MOD_SETTINGS)
            return
        if action == "settings.sound":
            key, value = "sound", 0 if self._setting("sound", 1) else 1
            scheduler = lambda callback, delay: self.reactor.register_callback(
                callback, self.reactor.monotonic() + delay)
            renderer = getattr(self, "renderer", None)
            animate = getattr(renderer, "animate_toggle", None)
            if animate is not None:
                animate(action, bool(value), scheduler)
            block = getattr(renderer, "block_input", None)
            if block is not None:
                block()
        else:
            key = "backlight_eco" if action.startswith("settings.eco") else "backlight"
            delta = -5 if action.endswith("minus") else 5
            value = max(1, min(100, int(self._setting(key, 10)) + delta))
        self._run_script("SET_MOD PARAM=%s VALUE=%d" % (key, value))
        if key == "backlight":
            self._set_backlight(value)
        if key == "sound":
            self.reactor.register_callback(
                lambda _eventtime: self._render_settings(),
                self.reactor.monotonic() + 0.14)
        else:
            self._render_settings()

    def _mod_parameters(self):
        return mod_ui.visible_parameters(self.params)

    def _render_mod_settings(self):
        self._require_idle()
        parameters = self._mod_parameters()
        total = len(parameters)
        page_count = max(1, (total + mod_ui.VISIBLE_ROWS - 1)
                         // mod_ui.VISIBLE_ROWS)
        self.mod_page = max(0, min(getattr(self, "mod_page", 0), page_count - 1))
        start = self.mod_page * mod_ui.VISIBLE_ROWS
        visible = parameters[start:start + mod_ui.VISIBLE_ROWS]
        commands = self.renderer.begin_page("Mod settings", back=True)
        first = start + 1 if total else 0
        last = min(total, start + len(visible))
        commands.append(self.renderer.text(
            25, 72, "MOD PARAMETERS // %02d-%02d / %02d" % (first, last, total),
            "35d9e6", "JetBrainsMono 8pt"))

        row_x, row_width, row_height = 25, 690, 64
        for row, param in enumerate(visible):
            absolute = start + row
            action = "mod.item.%d" % absolute
            y = 88 + row * 66
            title = self.renderer.truncate_text(
                str(param.label).upper(), 430, "JetBrainsMono Bold 8pt")
            detail = self.renderer.truncate_text(
                mod_ui.description(param), 430, "JetBrainsMono 8pt")
            commands += [
                self.renderer.fill(row_x, y, row_width, row_height, "050c0f"),
                self.renderer.stroke(row_x, y, row_width, row_height,
                                     "295c66", 1),
                self.renderer.text(40, y + 14, title, "35d9e6",
                                   "JetBrainsMono Bold 8pt"),
                self.renderer.text(40, y + 32, param.key, "56656c",
                                   "JetBrainsMono 8pt"),
                self.renderer.text(40, y + 50, detail, "d9e4e8",
                                   "JetBrainsMono 8pt"),
            ]
            kind = mod_ui.parameter_kind(param)
            value = mod_ui.display_value(self.params, param)
            state = "disabled" if getattr(param, "readonly", False) else "enabled"
            if kind == "bool":
                commands += self.renderer.toggle(
                    action, 624, y + 13, 76, 38, value == "ON",
                    enabled=state == "enabled")
            else:
                label = self.renderer.truncate_text(value, 130,
                                                    "JetBrainsMono 8pt") + " >"
                commands += self.renderer.button(
                    action, 520, y + 9, 180, 46, label, state=state,
                    font="JetBrainsMono 8pt")

        previous_state = "enabled" if self.mod_page > 0 else "disabled"
        next_state = "enabled" if self.mod_page + 1 < page_count else "disabled"
        commands += self.renderer.button("mod.prev", 728, 88, 52, 48, "^",
                                         state=previous_state,
                                         font="JetBrainsMono 12pt")
        commands += self.renderer.button("mod.next", 728, 365, 52, 48, "v",
                                         state=next_state,
                                         font="JetBrainsMono 12pt")
        track_y, track_height = 146, 209
        commands += [self.renderer.stroke(749, track_y, 10, track_height,
                                          "295c66", 1)]
        thumb_height = max(18, track_height // page_count)
        thumb_y = (track_y if page_count == 1 else
                   track_y + (track_height - thumb_height) * self.mod_page
                   // (page_count - 1))
        commands.append(self.renderer.fill(751, thumb_y + 2, 6,
                                           max(4, thumb_height - 4), "35d9e6"))
        self.renderer.send(commands)

    def _open_mod_parameter(self, index, return_page=Page.MOD_SETTINGS):
        parameters = self._mod_parameters()
        if index < 0 or index >= len(parameters):
            raise RuntimeError("Parameter is no longer available")
        param = parameters[index]
        if getattr(param, "readonly", False):
            raise RuntimeError("This parameter is read-only")
        self.mod_return_page = return_page
        kind = mod_ui.parameter_kind(param)
        if kind == "bool":
            current = bool(self.params.variables.get(param.key, param.default))
            action = "mod.item.%d" % index
            scheduler = lambda callback, delay: self.reactor.register_callback(
                callback, self.reactor.monotonic() + delay)
            animate = getattr(self.renderer, "animate_toggle", None)
            if animate is not None:
                animate(action, not current, scheduler)

            def complete():
                self._render_mod_settings()
                self._toast("UPDATED: %s" % param.label)

            self._set_mod_value(param, "0" if current else "1",
                                complete, minimum_duration=0.14)
            return
        self.mod_parameter = param
        self.mod_edit_value = mod_ui.current_edit_value(self.params, param)
        self.mod_keyboard_shift = False
        self.mod_keyboard_symbols = False
        if kind == "enum" or param.key == "feather_theme":
            self.mod_enum_selection = self.mod_edit_value
            self.mod_enum_page = 0
            self._show_page(Page.MOD_ENUM)
        else:
            self.mod_enum_selection = None
            self._show_page(Page.MOD_VALUE)

    def _set_mod_value(self, param, value, complete=None,
                       minimum_duration=0.0):
        setter = getattr(self.params, "set_value", None)
        if setter is None:
            raise RuntimeError("Mod parameter API is unavailable")
        logging.info("[feather_screen] mod parameter update key=%s", param.key)
        now = self.reactor.monotonic()
        token = getattr(self, "mod_update_token", 0) + 1
        self.mod_update_token = token
        self.mod_update_pending = True
        self.mod_update_modal_visible = False
        self.mod_update_modal_at = 0.0
        self.mod_update_started = now
        self.mod_update_not_before = now + max(0.0, minimum_duration)
        self.mod_update_complete = complete
        block = getattr(self.renderer, "block_input", None)
        if block is not None:
            block()
        self.reactor.register_callback(
            lambda eventtime, operation=token:
            self._show_mod_update_modal(eventtime, operation),
            now + 0.3)
        try:
            result = setter(param.key, value)
        except Exception:
            self.mod_update_pending = False
            self.mod_update_token += 1
            self.mod_update_complete = None
            raise
        self.reactor.register_callback(
            lambda eventtime, operation=token:
            self._finish_mod_update(eventtime, operation))
        return result

    def _show_mod_update_modal(self, eventtime, token):
        if (not getattr(self, "mod_update_pending", False)
                or token != getattr(self, "mod_update_token", 0)):
            return
        if getattr(self, "mod_update_modal_visible", False):
            return
        self.mod_update_modal_visible = True
        self.mod_update_modal_at = eventtime
        modal = getattr(self.renderer, "applying_modal", None)
        if modal is not None:
            modal()
        logging.info("[feather_screen] showing mod update modal")

    def _finish_mod_update(self, eventtime, token=None):
        if token is None:
            token = getattr(self, "mod_update_token", 0)
        if (not getattr(self, "mod_update_pending", False)
                or token != getattr(self, "mod_update_token", 0)):
            return
        if (not getattr(self, "mod_update_modal_visible", False)
                and eventtime - getattr(self, "mod_update_started", eventtime)
                >= 0.3):
            self._show_mod_update_modal(eventtime, token)
        deadline = getattr(self, "mod_update_not_before", 0.0)
        if getattr(self, "mod_update_modal_visible", False):
            deadline = max(deadline,
                           getattr(self, "mod_update_modal_at", 0.0) + 0.225)
        if eventtime < deadline:
            self.reactor.register_callback(
                lambda when, operation=token:
                self._finish_mod_update(when, operation),
                deadline)
            return
        self.mod_update_pending = False
        self.mod_update_modal_visible = False
        complete = getattr(self, "mod_update_complete", None)
        self.mod_update_complete = None
        logging.info("[feather_screen] mod parameter update finished")
        if complete is not None:
            complete()
        else:
            self._show_page(self.page)

    def _handle_mod_action(self, action):
        self._require_idle()
        if action == "mod.prev":
            self.mod_page = max(0, self.mod_page - 1)
            self._render_mod_settings()
            return
        if action == "mod.next":
            self.mod_page += 1
            self._render_mod_settings()
            return
        if action.startswith("mod.item."):
            self._open_mod_parameter(
                int(action.rsplit(".", 1)[1]), Page.MOD_SETTINGS)
            return
        if action == "mod.cancel":
            self.mod_parameter = None
            self._show_page(getattr(
                self, "mod_return_page", Page.MOD_SETTINGS))
            return
        param = self.mod_parameter
        if param is None:
            raise RuntimeError("No parameter selected")
        kind = mod_ui.parameter_kind(param)
        if action.startswith("mod.option."):
            options = (list(self.renderer.theme_names(reload=True))
                       if param.key == "feather_theme"
                       else mod_ui.enum_names(param))
            index = int(action.rsplit(".", 1)[1])
            if index < 0 or index >= len(options):
                raise RuntimeError("Unknown option")
            self.mod_enum_selection = options[index]
            self._render_mod_enum()
            return
        if action == "mod.enum.prev":
            self.mod_enum_page = max(0, self.mod_enum_page - 1)
            self._render_mod_enum()
            return
        if action == "mod.enum.next":
            self.mod_enum_page += 1
            self._render_mod_enum()
            return
        if action == "mod.apply":
            value = mod_ui.validate_value(param, self.mod_enum_selection)

            def complete():
                if param.key == "feather_theme":
                    self.renderer.set_theme(value)
                return_page = getattr(
                    self, "mod_return_page", Page.MOD_SETTINGS)
                self.mod_parameter = None
                self._show_page(return_page)
                self._toast("UPDATED: %s" % param.label)

            self._set_mod_value(param, value, complete)
            return
        if action == "mod.save":
            value = mod_ui.validate_value(param, self.mod_edit_value)

            def complete():
                return_page = getattr(
                    self, "mod_return_page", Page.MOD_SETTINGS)
                self.mod_parameter = None
                self._show_page(return_page)
                self._toast("UPDATED: %s" % param.label)

            self._set_mod_value(param, value, complete)
            return
        if action == "mod.backspace":
            self.mod_edit_value = self.mod_edit_value[:-1]
        elif action == "mod.sign" and kind in ("int", "float"):
            self.mod_edit_value = (self.mod_edit_value[1:]
                                   if self.mod_edit_value.startswith("-")
                                   else "-" + self.mod_edit_value)
        elif action == "mod.dot" and kind == "float":
            if "." not in self.mod_edit_value:
                self.mod_edit_value += "."
        elif action == "mod.shift" and kind == "str":
            self.mod_keyboard_shift = not self.mod_keyboard_shift
        elif action == "mod.symbols" and kind == "str":
            self.mod_keyboard_symbols = not self.mod_keyboard_symbols
        elif action == "mod.space" and kind == "str":
            if len(self.mod_edit_value) < mod_ui.MAX_VALUE_LENGTH:
                self.mod_edit_value += " "
        elif action.startswith("mod.key."):
            token = action[len("mod.key."):]
            character = mod_ui.key_character(token, self.mod_keyboard_shift)
            if character is not None and len(self.mod_edit_value) < mod_ui.MAX_VALUE_LENGTH:
                self.mod_edit_value += character
        self._render_mod_value()

    def _render_mod_enum(self):
        param = self.mod_parameter
        if param is None:
            self._show_page(Page.MOD_SETTINGS)
            return
        commands = self.renderer.begin_page(str(param.label), back=True)
        description = self._wrap(mod_ui.description(param), 58, 2)
        for index, line in enumerate(description):
            commands.append(self.renderer.text(25, 76 + index * 20, line,
                                               "d9e4e8", "JetBrainsMono 8pt"))
        if param.key == "display":
            commands.append(self.renderer.text(
                25, 108, "CHANGING DISPLAY RESTARTS KLIPPER.", "f2c94c",
                "JetBrainsMono 8pt"))
        if param.key == "feather_theme":
            options = list(self.renderer.theme_names(reload=True))
            if self.mod_enum_selection not in options:
                self.mod_enum_selection = "DEFAULT"
        else:
            options = mod_ui.enum_names(param)
        page_count = max(1, (len(options) + 3) // 4)
        self.mod_enum_page = max(
            0, min(getattr(self, "mod_enum_page", 0), page_count - 1))
        start = self.mod_enum_page * 4
        for row, name in enumerate(options[start:start + 4]):
            index = start + row
            selected = name == self.mod_enum_selection
            detail = (self.renderer.theme_description(name).upper()
                      if param.key == "feather_theme"
                      else mod_ui.option_description(param, name).upper())
            label = name.upper()
            if detail:
                label += " // " + detail
            if selected:
                label += "  [SELECTED]"
            commands += self.renderer.button(
                "mod.option.%d" % index, 25, 120 + row * 66, 750, 58,
                label, state="selected" if selected else "enabled",
                font="JetBrainsMono 8pt")
        if page_count > 1:
            commands += self.renderer.button(
                "mod.enum.prev", 25, 390, 120, 47, "<",
                state="enabled" if self.mod_enum_page > 0 else "disabled",
                font="JetBrainsMono Bold 8pt")
            commands += self.renderer.button(
                "mod.cancel", 155, 390, 220, 47, "CANCEL", state="danger",
                font="JetBrainsMono Bold 8pt")
            commands += self.renderer.button(
                "mod.apply", 425, 390, 220, 47, "APPLY",
                font="JetBrainsMono Bold 8pt")
            commands += self.renderer.button(
                "mod.enum.next", 655, 390, 120, 47, ">",
                state=("enabled" if self.mod_enum_page + 1 < page_count
                       else "disabled"),
                font="JetBrainsMono Bold 8pt")
            commands.append(self.renderer.text(
                750, 80, "%d/%d" % (self.mod_enum_page + 1, page_count),
                "56656c", "JetBrainsMono 8pt", "right", "middle"))
        else:
            commands += self.renderer.button(
                "mod.cancel", 25, 390, 360, 47, "CANCEL", state="danger",
                font="JetBrainsMono Bold 8pt")
            commands += self.renderer.button(
                "mod.apply", 415, 390, 360, 47, "APPLY",
                font="JetBrainsMono Bold 8pt")
        self.renderer.send(commands)

    def _render_mod_value(self):
        param = self.mod_parameter
        if param is None:
            self._show_page(Page.MOD_SETTINGS)
            return
        kind = mod_ui.parameter_kind(param)
        commands = self.renderer.begin_page("Edit value", back=True)
        commands += [
            self.renderer.text(25, 73, str(param.label).upper(), "35d9e6",
                               "JetBrainsMono Bold 12pt"),
            self.renderer.text(25, 98, param.key, "56656c",
                               "JetBrainsMono 8pt"),
            self.renderer.text(280, 98,
                               self.renderer.truncate_text(
                                   mod_ui.description(param), 490,
                                   "JetBrainsMono 8pt"),
                               "d9e4e8", "JetBrainsMono 8pt"),
            self.renderer.fill(25, 120, 750, 53, "050c0f"),
            self.renderer.stroke(25, 120, 750, 53, "35d9e6", 2),
            self.renderer.text(42, 147,
                               self.renderer.truncate_text(
                                   self.mod_edit_value or "_", 710,
                                   "JetBrainsMono 12pt"),
                               "35d9e6", "JetBrainsMono 12pt"),
        ]
        if kind in ("int", "float"):
            commands += self._render_mod_numeric_keys(kind)
        else:
            commands += self._render_mod_text_keys()
        self.renderer.send(commands)

    def _render_mod_numeric_keys(self, kind):
        commands = []
        rows = (("1", "2", "3"), ("4", "5", "6"),
                ("7", "8", "9"), ("sign", "0", "dot"))
        for row, keys in enumerate(rows):
            for column, token in enumerate(keys):
                label = "-" if token == "sign" else "." if token == "dot" else token
                action = "mod.%s" % token if token in ("sign", "dot") else "mod.key.%s" % token
                state = "disabled" if token == "dot" and kind == "int" else "enabled"
                commands += self.renderer.button(
                    action, 25 + column * 155, 185 + row * 47, 140, 40,
                    label, state=state, font="JetBrainsMono 12pt")
        commands += self.renderer.button("mod.backspace", 500, 185, 275, 181,
                                         "BACKSPACE", font="JetBrainsMono 8pt")
        commands += self.renderer.button("mod.cancel", 25, 383, 360, 54,
                                         "CANCEL", state="danger",
                                         font="JetBrainsMono Bold 8pt")
        commands += self.renderer.button("mod.save", 415, 383, 360, 54,
                                         "SAVE", font="JetBrainsMono Bold 8pt")
        return commands

    def _render_mod_text_keys(self):
        commands = []
        if self.mod_keyboard_symbols:
            rows = mod_ui.SYMBOL_KEYS[:10], mod_ui.SYMBOL_KEYS[10:20]
        else:
            rows = tuple(tuple((char, char.upper() if self.mod_keyboard_shift else char)
                               for char in row) for row in ALPHA_KEY_ROWS)
        for row, keys in enumerate(rows):
            key_width = 68
            total_width = len(keys) * key_width + max(0, len(keys) - 1) * 7
            x = (800 - total_width) // 2
            for token, label in keys:
                commands += self.renderer.button(
                    "mod.key.%s" % token, x, 181 + row * 49, key_width, 42,
                    label, font="JetBrainsMono 8pt")
                x += key_width + 7
        controls_y = 328
        commands += self.renderer.button(
            "mod.shift", 25, controls_y, 120, 43, "SHIFT",
            state="selected" if self.mod_keyboard_shift else "enabled",
            font="JetBrainsMono 8pt")
        commands += self.renderer.button(
            "mod.symbols", 155, controls_y, 100, 43,
            "ABC" if self.mod_keyboard_symbols else "123",
            state="selected" if self.mod_keyboard_symbols else "enabled",
            font="JetBrainsMono 8pt")
        commands += self.renderer.button("mod.space", 265, controls_y, 300, 43,
                                         "SPACE", font="JetBrainsMono 8pt")
        commands += self.renderer.button("mod.backspace", 575, controls_y, 200, 43,
                                         "BACKSPACE", font="JetBrainsMono 8pt")
        commands += self.renderer.button("mod.cancel", 25, 383, 360, 54,
                                         "CANCEL", state="danger",
                                         font="JetBrainsMono Bold 8pt")
        commands += self.renderer.button("mod.save", 415, 383, 360, 54,
                                         "SAVE", font="JetBrainsMono Bold 8pt")
        return commands

    def _render_network_home(self):
        self._require_idle()
        commands = self.renderer.begin_page("Network", back=True)
        lines = ["Mode: %s" % self.network_status.get("mode", "OFFLINE")]
        if self.network_status.get("ssid"):
            lines.append("SSID: %s   Signal: %s dBm" %
                         (self.network_status["ssid"], self.network_status.get("signal") or "?"))
        lines.append("IP: %s" % (self.network_status.get("ip") or "Offline"))
        for index, line in enumerate(lines):
            commands.append(self.renderer.text(400, 75 + index * 35,
                                               self._shorten(line, 60), "ffffff",
                                               "Roboto 10pt", "center"))
        commands += self.renderer.button("net.scan", 55, 190, 320, 150,
                                         "WI-FI", font="JetBrainsMono 12pt")
        commands += self.renderer.button("net.ethernet", 425, 190, 320, 150,
                                         "ETHERNET DHCP", font="JetBrainsMono 12pt")
        commands += self.renderer.button("net.retry", 270, 365, 260, 60, "RETRY STATUS")
        self.renderer.send(commands)

    def _handle_network_action(self, action):
        self._require_idle()
        if action in ("net.scan", "net.rescan"):
            self._start_network_process("scan", [NETWORK_HELPER, "scan"], Page.NETWORK_HOME)
        elif action == "net.retry":
            self._start_network_process("status", [NETWORK_HELPER, "status"],
                                        Page.NETWORK_HOME)
        elif action == "net.ethernet":
            self._start_network_process("ethernet", [NETWORK_HELPER, "use-ethernet"],
                                        Page.NETWORK_HOME)
        elif action in ("net.prev", "net.next"):
            self.network_page += -1 if action == "net.prev" else 1
            self._render_wifi_scan()
        elif action.startswith("net.item"):
            index = int(action[len("net.item"):])
            offset = self.network_page * FILE_ROWS + index
            if offset < len(self.networks):
                self.selected_network = self.networks[offset]
                self.password = ""
                self.keyboard_shift = self.keyboard_symbols = False
                self._show_page(Page.WIFI_PASSWORD)
        elif action == "net.connect":
            self._connect_wifi()
        elif action == "net.cancel":
            self._cancel_network_process("Network operation cancelled")
        elif action == "net.password.toggle":
            self.password_visible = not self.password_visible
            self._render_keyboard()
        elif action == "key.backspace":
            self.password = self.password[:-1]
            self._render_keyboard()
        elif action == "key.space":
            self._append_password(" ")
        elif action == "key.shift":
            self.keyboard_shift = not self.keyboard_shift
            self._render_keyboard()
        elif action == "key.symbols":
            self.keyboard_symbols = not self.keyboard_symbols
            self._render_keyboard()
        elif action.startswith("key.char."):
            token = action[len("key.char."):]
            chars = {
                "dot": ".", "comma": ",", "minus": "-", "under": "_",
                "plus": "+", "at": "@", "hash": "#", "dollar": "$",
                "percent": "%", "amp": "&", "star": "*", "bang": "!",
                "question": "?", "slash": "/", "colon": ":", "semi": ";",
                "lparen": "(", "rparen": ")", "quote": '"', "bslash": "\\",
            }
            value = chars.get(token, token)
            if self.keyboard_shift and len(value) == 1:
                value = value.upper()
            self._append_password(value)

    def _start_network_process(self, operation, args, return_page):
        if self.network_process is not None:
            if self.network_operation == "status-background":
                self._retire_network_process(self.network_process)
                self.network_process = None
                self.network_operation = None
            else:
                raise RuntimeError("A network operation is already running")
        process = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True)
        self.network_operation = operation
        self.network_return_page = return_page
        self.network_process = process
        self.network_deadline = (self.reactor.monotonic() +
                                 NETWORK_TIMEOUTS.get(operation, 30.0))
        self._show_page(Page.NETWORK_PROGRESS)

    def _start_network_status_refresh(self):
        """Load persisted/live network state without replacing the dashboard."""
        if self.network_process is not None:
            return
        self.network_process = subprocess.Popen(
            [NETWORK_HELPER, "status"], stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, start_new_session=True)
        self.network_operation = "status-background"
        self.network_deadline = (self.reactor.monotonic() +
                                 NETWORK_TIMEOUTS["status-background"])

    def _poll_network_process(self, eventtime):
        if self.network_process is None:
            return
        if self.network_process.poll() is None:
            if eventtime >= self.network_deadline:
                if getattr(self, "network_operation", None) == "status-background":
                    self._stop_network_process()
                else:
                    self._cancel_network_process("Network operation timed out")
            return
        output = self.network_process.communicate()[0].decode("utf-8", "replace")
        returncode = self.network_process.returncode
        operation = self.network_operation
        self.network_process = None
        self.network_operation = None
        self.network_deadline = 0.0
        self._cleanup_network_credentials()
        if returncode != 0:
            if operation == "status-background":
                return
            message = next((line[6:] for line in output.splitlines()
                            if line.startswith("ERROR=")), "Network operation failed")
            self._show_message(message, self.network_return_page)
            return
        if operation == "scan":
            networks = {}
            for line in output.splitlines():
                if not line.startswith("NETWORK\t"):
                    continue
                parts = line.split("\t", 3)
                if len(parts) != 4 or not parts[1]:
                    continue
                ssid, signal, security = parts[1], parts[2], parts[3]
                if any(ord(ch) < 32 for ch in ssid) or "PSK" not in security:
                    continue
                try:
                    signal = int(signal)
                except ValueError:
                    continue
                current = networks.get(ssid)
                if current is None or signal > current["signal"]:
                    networks[ssid] = {"ssid": ssid, "signal": signal,
                                      "security": security}
            self.networks = sorted(networks.values(), key=lambda n: -n["signal"])
            self.network_page = 0
            self._show_page(Page.WIFI_SCAN)
        elif operation in ("status", "status-background"):
            self.network_status = self.parse_network_status(output)
            if operation == "status":
                self._show_page(Page.NETWORK_HOME)
            elif self.page == Page.IDLE_HOME:
                self._update_dashboard(eventtime)
        else:
            self._toast("Network connected")
            self._start_network_process("status", [NETWORK_HELPER, "status"],
                                        Page.NETWORK_HOME)

    def _cancel_network_process(self, message):
        self._stop_network_process()
        self._show_message(message, self.network_return_page)

    def _stop_network_process(self):
        if self.network_process is not None:
            self._retire_network_process(self.network_process)
            self.network_process = None
        self.network_operation = None
        self.network_deadline = 0.0
        self._cleanup_network_credentials()

    def _retire_network_process(self, process):
        group_id = None
        try:
            group_id = os.getpgid(process.pid)
            os.killpg(group_id, signal.SIGTERM)
        except (AttributeError, OSError):
            try:
                process.terminate()
            except OSError:
                pass
        stopping = getattr(self, "network_stopping", [])
        stopping.append(
            (process, self.reactor.monotonic() + 2.0, group_id))
        self.network_stopping = stopping

    def _reap_network_processes(self, eventtime):
        pending = []
        for process, deadline, group_id in getattr(
                self, "network_stopping", []):
            if eventtime >= deadline:
                if group_id is not None:
                    try:
                        os.killpg(group_id, signal.SIGKILL)
                    except OSError:
                        pass
                elif process.poll() is None:
                    killer = getattr(process, "kill", None)
                    if killer is not None:
                        killer()
                if process.poll() is None:
                    pending.append((process, deadline, group_id))
                    continue
            elif process.poll() is None or group_id is not None:
                # Keep the process group through the grace period even when
                # the shell leader exits first; udhcpc may still be alive.
                pending.append((process, deadline, group_id))
                continue
            try:
                process.communicate()
            except (OSError, ValueError):
                pass
        self.network_stopping = pending

    def _cleanup_network_credentials(self):
        credentials = getattr(self, "network_credentials", None)
        self.network_credentials = None
        if not credentials:
            return
        try:
            os.unlink(credentials)
        except OSError:
            pass

    @staticmethod
    def parse_network_status(output):
        result = {"mode": "OFFLINE", "ssid": "", "signal": "", "ip": ""}
        for line in str(output).splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.lower()
            if key in result:
                result[key] = value.strip()
        return result

    def _render_network_progress(self):
        commands = self.renderer.begin_page("Network")
        label = "Scanning Wi-Fi..." if self.network_operation == "scan" else "Connecting..."
        commands.append(self.renderer.text(400, 230, label, "00f0f0", "Roboto Bold 16pt",
                                           "center", "middle"))
        commands += self.renderer.button("net.cancel", 270, 340, 260, 70, "CANCEL",
                                         state="danger")
        self.renderer.send(commands)

    def _render_wifi_scan(self):
        max_page = max(0, (len(self.networks) - 1) // FILE_ROWS)
        self.network_page = max(0, min(self.network_page, max_page))
        commands = self.renderer.begin_page("Select Wi-Fi", back=True)
        rows = self.networks[self.network_page * FILE_ROWS:
                             self.network_page * FILE_ROWS + FILE_ROWS]
        for index, network in enumerate(rows):
            y = 62 + index * 65
            label = "%s   %d dBm" % (self._shorten(network["ssid"], 36), network["signal"])
            commands += self.renderer.button("net.item%d" % index, 30, y, 740, 56,
                                             label, font="JetBrainsMono 12pt")
        commands += self.renderer.button("net.prev", 125, 390, 150, 50, "< Page",
                                         active=self.network_page > 0)
        commands += self.renderer.button("net.rescan", 325, 390, 150, 50, "RESCAN")
        commands += self.renderer.button("net.next", 525, 390, 150, 50, "Page >",
                                         active=self.network_page < max_page)
        if not rows:
            commands.append(self.renderer.text(400, 230, "No supported networks",
                                               "606060", "Roboto 16pt", "center", "middle"))
        self.renderer.send(commands)

    def _render_keyboard(self):
        ssid = self.selected_network["ssid"]
        commands = self.renderer.begin_page(ssid, back=True)
        masked = self.password if self.password_visible else "*" * len(self.password)
        commands.append(self.renderer.stroke(55, 62, 690, 42, "872187", 3))
        commands.append(self.renderer.text(70, 83,
                                           self.renderer.truncate_text(
                                               masked, 660, "JetBrainsMono 12pt"),
                                           "ffffff",
                                           "JetBrainsMono 12pt", "left", "middle"))

        if self.keyboard_symbols:
            rows = SYMBOL_KEY_ROWS
        else:
            rows = [[(ch, ch.upper() if self.keyboard_shift else ch) for ch in row]
                    for row in ALPHA_KEY_ROWS]
        for row_index, row in enumerate(rows):
            key_width = 66
            gap = 5
            total = len(row) * key_width + (len(row) - 1) * gap
            x = (800 - total) // 2
            y = 112 + row_index * 68
            for token, label in row:
                commands += self.renderer.button("key.char.%s" % token, x, y,
                                                 key_width, 58, label,
                                                 font="Roboto Bold 12pt")
                x += key_width + gap
        commands += self.renderer.button("key.shift", 20, 317, 105, 52, "SHIFT")
        commands += self.renderer.button("key.symbols", 135, 317, 105, 52,
                                         "ABC" if self.keyboard_symbols else "123")
        commands += self.renderer.button("key.space", 250, 317, 210, 52, "SPACE")
        commands += self.renderer.button("key.backspace", 470, 317, 165, 52, "BACKSPACE")
        commands += self.renderer.button("net.password.toggle", 645, 317, 135, 52,
                                         "HIDE" if self.password_visible else "SHOW")
        valid = self._valid_password(self.password)
        commands += self.renderer.button("net.connect", 235, 382, 330, 58,
                                         "CONNECT", active=valid,
                                         font="Roboto Bold 16pt")
        self.renderer.send(commands)

    def _append_password(self, value):
        if len(self.password) + len(value) <= 64:
            self.password += value
        self._render_keyboard()

    @staticmethod
    def _valid_password(password):
        if len(password) == 64:
            return all(ch in "0123456789abcdefABCDEF" for ch in password)
        return 8 <= len(password) <= 63 and all(32 <= ord(ch) <= 126 for ch in password)

    def _connect_wifi(self):
        if not self._valid_password(self.password):
            raise RuntimeError("Password must be 8-63 ASCII characters or 64 hex digits")
        fd, credentials = self._create_credentials_file()
        try:
            payload = self.selected_network["ssid"] + "\n" + self.password + "\n"
            os.write(fd, payload.encode("utf-8"))
        finally:
            os.close(fd)
        self.password = ""
        self.network_credentials = credentials
        try:
            self._start_network_process(
                "wifi", [NETWORK_HELPER, "connect-wifi", credentials], Page.WIFI_SCAN)
        except Exception:
            self._cleanup_network_credentials()
            raise

    def _create_credentials_file(self):
        """Create a private file without importing the heavy tempfile module."""
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        sequence = getattr(self, "_credential_sequence", 0)
        for _attempt in range(16):
            sequence += 1
            path = "/tmp/feather-wifi-%d-%d" % (os.getpid(), sequence)
            try:
                fd = os.open(path, flags, 0o600)
                self._credential_sequence = sequence
                os.fchmod(fd, 0o600)
                return fd, path
            except OSError as exc:
                if exc.errno != errno.EEXIST:
                    raise
        raise RuntimeError("Unable to allocate Wi-Fi credentials file")

    def _render_recovery_prompt(self):
        status = self.recovery_status or {}
        commands = self.renderer.begin_page("Power loss recovery")
        commands.append(self.renderer.text(400, 90,
                                           self._shorten(status.get("filename", "Unknown"), 48),
                                           "ffffff", "Roboto Bold 14pt", "center"))
        commands.append(self.renderer.text(400, 140, "Saved progress: %.1f%%" %
                                           (float(status.get("progress", 0.0)) * 100),
                                           "00f0f0", "Roboto 12pt", "center"))
        commands.append(self.renderer.text(
            400, 185, "Nozzle %.0fC   Bed %.0fC   Mesh %s" %
            (status.get("extruder_target", 0), status.get("bed_target", 0),
             status.get("mesh", "?")), "ffffff", "Roboto 10pt", "center"))
        commands += self.renderer.button("recovery.restore", 35, 285, 220, 100,
                                         "RESTORE", font="Roboto Bold 14pt")
        commands += self.renderer.button("recovery.later", 290, 285, 220, 100,
                                         "LATER", font="Roboto Bold 14pt")
        commands += self.renderer.button("recovery.cleanup", 545, 285, 220, 100,
                                         "CLEANUP", state="danger",
                                         font="Roboto Bold 14pt")
        self.renderer.send(commands)

    def _render_recovery_confirm(self):
        cleanup = self.recovery_action == "cleanup"
        commands = self.renderer.begin_page("Confirm recovery", back=True)
        text = ("Cleanup will heat and home, then permanently remove recovery data."
                if cleanup else
                "Restore will heat, home and continue the interrupted print.")
        for index, line in enumerate(self._wrap(text, 52, 3)):
            commands.append(self.renderer.text(400, 150 + index * 38, line,
                                               "ffffff", "Roboto 11pt", "center"))
        commands += self.renderer.button("recovery.confirm", 220, 300, 360, 100,
                                         "CLEANUP" if cleanup else "RESTORE",
                                         state="danger" if cleanup else "enabled",
                                         font="Roboto Bold 16pt")
        self.renderer.send(commands)

    def _handle_recovery_action(self, action):
        if action == "recovery.later":
            self._show_page(Page.IDLE_HOME)
        elif action in ("recovery.restore", "recovery.cleanup"):
            self.recovery_action = action.split(".", 1)[1]
            self._show_page(Page.RECOVERY_CONFIRM)
        elif action == "recovery.confirm":
            command = "RESURRECT" if self.recovery_action == "restore" else "RESURRECT_ABORT"
            self.calibration_kind = "recovery"
            self.print_status_text = "RESTORING..." if command == "RESURRECT" else "CLEANING UP..."
            self._show_page(Page.CALIBRATION_PROGRESS)
            self.reactor.register_callback(
                lambda eventtime, cmd=command: self._run_recovery(eventtime, cmd))

    def _run_recovery(self, eventtime, command):
        try:
            self._run_script(command)
        except Exception as exc:
            logging.exception("[feather_screen] recovery failed")
            self._show_message(str(exc), Page.RECOVERY_PROMPT)
            return
        status = (self.resurrection.get_status(self.reactor.monotonic())
                  if self.resurrection is not None else {})
        if command == "RESURRECT_ABORT" and status.get("available"):
            self._show_message("Recovery cleanup failed", Page.RECOVERY_PROMPT)
        elif command == "RESURRECT_ABORT":
            self._show_message("Recovery data cleaned up", Page.IDLE_HOME)
        elif self.print_stats.get_status(self.reactor.monotonic())["state"] != "printing":
            self._show_message("Restore did not start printing", Page.RECOVERY_PROMPT)

    def page_for_print_state(self):
        return Page.PAUSED if self.print_state == PrintState.PAUSED else Page.PRINTING

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
        self.message = self._shorten(message, 100)
        self.message_return = return_page
        self._show_page(Page.MESSAGE)

    def _render_message(self):
        commands = self.renderer.begin_page("Message")
        lines = self._wrap(self.message, 36, 5)
        y = 150
        for line in lines:
            commands.append(self.renderer.text(400, y, line, "ffffff", "Roboto 12pt",
                                               "center", "middle"))
            y += 38
        commands += self.renderer.button("message.ok", 285, 365, 230, 75, "OK")
        self.renderer.send(commands)

    def _render_error(self, message):
        self.renderer.send([
            "--batch clear-hitboxes", self.renderer.fill(0, 0, 800, 480),
            self.renderer.stroke(120, 120, 560, 240, "ff00ff", 6),
            self.renderer.text(400, 240, self._shorten(message, 80), "00f0f0",
                               "Roboto 12pt", "center", "middle")])

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
            if self.page not in (Page.PRINTING, Page.CANCEL_CONFIRM):
                self._show_page(Page.PRINTING)
        elif new_state == PrintState.PAUSED:
            if self.page not in (Page.CANCEL_CONFIRM, Page.FILAMENT_MATERIAL,
                                 Page.FILAMENT_ACTION, Page.CALIBRATION_Z):
                self._show_page(Page.PAUSED)
        elif new_state == PrintState.IDLE:
            if old_state in (PrintState.PREPARING, PrintState.PRINTING,
                             PrintState.PAUSED, PrintState.FINISHED):
                label = ("Print cancelled" if
                         getattr(self, "cancel_requested", False) else
                         {"complete": "Print finished",
                          "cancelled": "Print cancelled",
                          "error": "Print failed"}.get(stats_state, "Print stopped"))
                self.cancel_requested = False
                self.cancel_waiting_for_heat = False
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
