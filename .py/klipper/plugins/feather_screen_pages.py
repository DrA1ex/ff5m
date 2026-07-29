## Dashboard, print, settings, and mod-parameter pages for Feather.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import errno
import importlib
import logging
import os
import signal
import subprocess
import time

try:
    from .ui import Page, PrintState
    from .feather_keyboard import keyboard_rows
    from .feather_files import FileEntry, scan_gcode_files
    from .feather_pagination import Pagination, pagination_footer
except (ImportError, ValueError):
    from ui import Page, PrintState
    from feather_keyboard import keyboard_rows
    from feather_files import FileEntry, scan_gcode_files
    from feather_pagination import Pagination, pagination_footer


class _LazyModUI:
    """Do not import Mod Settings helpers before Settings is opened."""

    _module = None

    def _load(self):
        if self._module is None:
            name = ("%s.feather_mod_settings" % __package__
                    if __package__ else "feather_mod_settings")
            self._module = importlib.import_module(name)
        return self._module

    def __getattr__(self, name):
        return getattr(self._load(), name)


mod_ui = _LazyModUI()


FILE_ROWS = 5
NETWORK_HELPER = "/root/printer_data/scripts/commands/znetwork.sh"
NETWORK_TIMEOUTS = {
    "scan": 15.0,
    "wifi": 45.0,
    "ethernet": 30.0,
    "status": 5.0,
    "status-background": 5.0,
}
NETWORK_ROWS = 5


class FeatherPagesMixin:
    def _render_home(self):
        commands = self.renderer.begin_page("FORGE-X // FEATHER")
        commands += self.renderer.button("nav.menu", 648, 9, 132, 38, "MENU",
                                         font="JetBrainsMono Bold 8pt")
        commands += [
            self.renderer.text(28, 80, "SYSTEM // STANDBY", "35d9e6",
                               "JetBrainsMono Bold 16pt", "left", "middle"),
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
            self.renderer.text(44, 274, "JOB STATUS", "35d9e6",
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
            self.renderer.action_hitbox("nav.heat", 25, 124, 492, 112),
            self.renderer.action_hitbox("nav.network", 539, 124, 236, 112),
            self.renderer.action_hitbox("nav.job", 25, 256, 750, 82),
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
        self._refresh_local_timezone()
        self.filament_material = self._current_material()
        extruder = self.extruder.get_status(eventtime)
        bed = self.heater_bed.get_status(eventtime)
        toolhead = self.toolhead.get_status(eventtime)
        homed = str(toolhead.get("homed_axes", "")).upper()
        mode = self.network_status.get("mode") or "OFFLINE"
        ssid = self.network_status.get("ssid") or ""
        ip = self.network_status.get("ip") or self._read_text("/tmp/net_ip") or "NO LINK"
        net_name = "%s%s" % (mode.upper(), " / " + ssid if ssid else "")
        job = self._dashboard_job(eventtime)
        values = (round(extruder["temperature"]), round(extruder["target"]),
                  round(bed["temperature"]), round(bed["target"]),
                  net_name, ip, self.last_job_name, self.filament_material,
                  homed or "NOT HOMED", job, time.strftime("%H:%M"))
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
                                   values[4], "d9e4e8", "JetBrainsMono 8pt",
                                   "center", "middle", max_width=210,
                                   truncate=True),
                self.renderer.text(657, 211,
                                   values[5], "35d9e6", "JetBrainsMono 8pt",
                                   "center", "middle", max_width=210,
                                   truncate=True)]
        if previous is None or values[9] != previous[9]:
            active, state, filename, progress, elapsed, remaining, detail = values[9]
            commands += [
                self.renderer.fill(25, 60, 500, 40, "030607"),
                self.renderer.text(
                    28, 80, "SYSTEM // %s" % (
                        state if active else "STANDBY"),
                    "35d9e6", "JetBrainsMono Bold 16pt",
                    "left", "middle"),
                self.renderer.fill(29, 292, 742, 42, "050c0f"),
                self.renderer.text(
                    44, 305, filename if active else "NO ACTIVE JOB",
                    "d9e4e8" if active else "35d9e6",
                    "JetBrainsMono Bold 8pt", "left", "middle",
                    max_width=560, truncate=True),
                self.renderer.text(
                    756, 305, state if active else "READY",
                    "f2c94c" if state == "PAUSED" else "35d9e6",
                    "JetBrainsMono 8pt", "right", "middle")]
            if active:
                commands += [
                    self.renderer.text(
                        44, 327, detail, "56656c", "JetBrainsMono 8pt",
                        "left", "middle", max_width=330, truncate=True),
                    self.renderer.text(
                        756, 327, "%d%% // %s / %s" % (
                            progress, elapsed, remaining),
                        "d9e4e8", "JetBrainsMono 8pt", "right", "middle")]
        if previous is None or values[6:9] != previous[6:9]:
            commands += [
                self.renderer.fill(25, 386, 750, 27, "030607"),
                self.renderer.text(28, 400, values[6], "d9e4e8",
                                   "JetBrainsMono 8pt", "left", "middle",
                                   max_width=240, truncate=True),
                self.renderer.text(300, 400, values[7], "d9e4e8",
                                   "JetBrainsMono 8pt", "left", "middle"),
                self.renderer.text(570, 400, values[8],
                                   "35d9e6" if values[8] == "XYZ" else "f2c94c",
                                   "JetBrainsMono 8pt", "left", "middle")]
        if previous is None or values[10] != previous[10]:
            commands += [
                self.renderer.fill(650, 60, 125, 40, "030607"),
                self.renderer.text(772, 80, values[10], "d9e4e8",
                                   "JetBrainsMono 16pt", "right", "middle")]
        self.renderer.send(commands)

    def _dashboard_job(self, eventtime):
        stats_object = getattr(self, "print_stats", None)
        virtual_sdcard = getattr(self, "virtual_sdcard", None)
        stats = (stats_object.get_status(eventtime)
                 if stats_object is not None else {})
        state = str(stats.get("state", "")).lower()
        active = (state in ("printing", "paused")
                  or bool(virtual_sdcard is not None
                          and virtual_sdcard.is_active()))
        if not active:
            return (False, "READY", self.last_job_name, 0,
                    "--:--:--", "--:--:--", "")

        if getattr(self, "print_state", None) == PrintState.PREPARING:
            label = "PREPARING"
        else:
            label = "PAUSED" if state == "paused" else "PRINTING"
        path = (virtual_sdcard.file_path()
                if virtual_sdcard is not None
                and hasattr(virtual_sdcard, "file_path") else "")
        filename = os.path.basename(path or self.last_job_name or "UNKNOWN")
        try:
            progress_value = self._print_progress(eventtime, stats)
            elapsed, remaining = self._print_time_values(
                eventtime, stats, progress_value)
        except (AttributeError, TypeError, ValueError):
            progress_value = 0.0
            elapsed = stats.get("print_duration")
            remaining = None
        detail = getattr(self, "print_status_text", "") or label
        return (
            True, label, filename, int(progress_value * 100),
            self._clock_duration(elapsed),
            self._clock_duration(remaining), detail)

    def _refresh_local_timezone(self):
        """Reload libc timezone data after SET_TIMEZONE replaces localtime."""
        try:
            stat_result = os.lstat("/etc/localtime")
            signature = (
                stat_result.st_ino, stat_result.st_mtime,
                os.readlink("/etc/localtime")
                if os.path.islink("/etc/localtime") else "")
        except OSError:
            signature = None
        if signature == getattr(self, "_timezone_signature", object()):
            return
        if hasattr(time, "tzset"):
            time.tzset()
        self._timezone_signature = signature

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

    def _load_file_entries(self):
        root = self.virtual_sdcard.sdcard_dirname
        history = getattr(self, "print_history", None)
        usb_storage = getattr(self, "usb_storage", None)
        if getattr(self, "file_source", "internal") == "usb":
            if usb_storage is None or not usb_storage.available:
                self.file_source = "internal"
                self.file_page = 0
            else:
                try:
                    self.file_entries = scan_gcode_files(
                        usb_storage.mount_point, history,
                        history_prefix=os.path.relpath(
                            usb_storage.mount_point, root))
                except RuntimeError as exc:
                    # Removable media can disappear between the monitor tick
                    # and directory traversal. Keep the UI responsive; the
                    # next tick will remove the USB entry if its mount is gone.
                    logging.info(
                        "[feather_screen] USB file scan deferred: %s", exc)
                    self.file_entries = []
                return

        excluded = ((usb_storage.mount_point,)
                    if usb_storage is not None else ())
        self.file_entries = scan_gcode_files(
            root, history, excluded_paths=excluded)
        if usb_storage is not None and usb_storage.available:
            self.file_entries.insert(0, FileEntry(
                "USB", usb_storage.mount_point, directory=True))

    def _record_current_print(self):
        history = getattr(self, "print_history", None)
        virtual_sdcard = getattr(self, "virtual_sdcard", None)
        if history is None or virtual_sdcard is None:
            return
        path = (virtual_sdcard.file_path()
                if hasattr(virtual_sdcard, "file_path") else None)
        if not path:
            return
        root = virtual_sdcard.sdcard_dirname
        if history.record(root, path, time.time()):
            self.last_job_name = os.path.basename(path)

    def _render_file_browser(self):
        self._load_file_entries()
        pagination = Pagination(self.file_entries, self.file_page, FILE_ROWS)
        self.file_page = pagination.page
        usb_page = getattr(self, "file_source", "internal") == "usb"
        title = "USB files" if usb_page else "Print files"
        commands = self.renderer.begin_page(title, back=True)
        if usb_page:
            commands += self.renderer.button(
                "file.refresh", 640, 7, 146, 46, "REFRESH",
                font="JetBrainsMono Bold 8pt")
        rows = pagination.visible
        for index, entry in enumerate(rows):
            y = 62 + index * 65
            commands += self.renderer.button("file.item%d" % index, 30, y, 740, 56,
                                             (entry["name"] + "  >"
                                              if entry["directory"]
                                              else entry["name"]),
                                             font="JetBrainsMono 12pt")
        commands += pagination_footer(
            self.renderer, pagination, "file.prev", "file.next")
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
        elif action == "file.refresh":
            self._render_file_browser()
        elif action == "file.start":
            self._start_selected_file()
        elif action.startswith("file.item"):
            index = int(action[len("file.item"):])
            pagination = Pagination(
                self.file_entries, self.file_page, FILE_ROWS)
            offset = pagination.absolute_index(index)
            if offset is None:
                return
            entry = self.file_entries[offset]
            if entry["directory"]:
                self.file_source = "usb"
                self.file_page = 0
                self.selected_file = None
                self._render_file_browser()
                return
            self.selected_file = entry
            self._show_page(Page.FILE_CONFIRM)

    def _render_file_confirm(self):
        entry = self.selected_file
        commands = self.renderer.begin_page("Start print?", back=True)
        commands.append(self.renderer.text(
            400, 150, entry["name"], "ffffff", "Roboto Bold 16pt", "center",
            "middle", max_width=720, truncate=True))
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
        controls_ready = self._print_controls_ready()
        commands = self.renderer.begin_page(
            "PAUSED" if paused else "PRINTING")
        commands += self.renderer.button(
            "nav.home", 14, 7, 146, 46, "HOME",
            font="JetBrainsMono Bold 8pt")
        filename = self.virtual_sdcard.file_path() or "Unknown"
        filename = os.path.basename(filename)
        commands.append(self.renderer.text(25, 78, filename,
                                           "35d9e6", "JetBrainsMono Bold 12pt",
                                           "left", "middle", max_width=750,
                                           truncate=True))
        commands.append(self.renderer.text(
            25, 110, self.print_status_text, "d9e4e8", "JetBrainsMono 8pt",
            "left", "middle", max_width=750, truncate=True))
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
                                         state=("disabled" if not controls_ready else
                                                "busy" if self.pending_action in
                                                ("print.pause", "print.resume")
                                                else "enabled"),
                                         font="JetBrainsMono Bold 12pt")
        commands += self.renderer.button("print.filament", 215, 355, 175, 72,
                                         "FILAMENT",
                                         state=("enabled" if controls_ready
                                                else "disabled"),
                                         font="JetBrainsMono Bold 12pt")
        commands += self.renderer.button(
            "print.z", 410, 355, 175, 72, "Z ADJUST",
            state="enabled" if self._live_z_adjust_allowed(
                self.reactor.monotonic())
            else "disabled", font="JetBrainsMono Bold 12pt")
        commands += self.renderer.button("print.cancel", 605, 355, 175, 72,
                                         "CANCEL", state="danger",
                                         font="JetBrainsMono Bold 12pt")
        self.renderer.send(commands)
        self._last_print_controls_ready = controls_ready
        self._last_progress = self._last_time = None
        self._update_print_progress(self.reactor.monotonic())

    def _print_controls_ready(self):
        if getattr(self, "print_state", None) == PrintState.PREPARING:
            return False
        flow = self._print_flow_status()
        started = bool(getattr(getattr(
            self, "start_print_macro", None), "variables", {}).get(
                "print_started", False))
        return not flow["active"] or started

    def _update_print_progress(self, eventtime):
        if self.page not in (Page.PRINTING, Page.PAUSED):
            return
        controls_ready = self._print_controls_ready()
        if controls_ready != getattr(
                self, "_last_print_controls_ready", controls_ready):
            self._render_print_page()
            return
        stats = self.print_stats.get_status(eventtime)
        progress_value = self._print_progress(eventtime, stats)
        progress = int(progress_value * 100)
        elapsed, remaining = self._print_time_values(
            eventtime, stats, progress_value)
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

    def _print_progress(self, eventtime, stats=None):
        stats = stats or self.print_stats.get_status(eventtime)
        status = self.virtual_sdcard.get_status(eventtime)
        try:
            sd_progress = float(status.get("progress", 0.0) or 0.0)
        except (TypeError, ValueError):
            sd_progress = 0.0
        sd_progress = max(0.0, min(1.0, sd_progress))

        display_status = getattr(self, "display_status", None)
        m73_expiry = float(
            getattr(display_status, "expire_progress", 0.0) or 0.0)
        if m73_expiry > getattr(self, "_m73_start_expiry", 0.0):
            self._m73_active = True
        m73_progress = getattr(display_status, "progress", None)

        progress = None
        source = None
        if getattr(self, "_m73_active", False) and m73_progress is not None:
            try:
                progress = float(m73_progress)
                source = "M73"
            except (TypeError, ValueError):
                progress = None

        estimate = (getattr(self.virtual_sdcard, "estimate_print_time", None)
                    or status.get("estimate_print_time"))
        if progress is None and estimate:
            try:
                duration = float(stats.get("print_duration", 0.0) or 0.0)
                estimate = float(estimate)
                if estimate > 0:
                    progress = min(0.99, duration / estimate)
                    source = "TIME"
            except (TypeError, ValueError):
                progress = None

        if progress is None:
            progress = sd_progress
            source = "SD"

        progress = max(0.0, min(1.0, progress))
        progress = max(getattr(self, "_progress_floor", 0.0), progress)
        self._progress_floor = progress
        if source != getattr(self, "_progress_source", None):
            logging.info("[feather_screen] print progress source=%s", source)
        self._progress_source = source
        return progress

    def _print_time_values(self, eventtime, stats=None, progress=None):
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
                if progress is None:
                    progress = self._print_progress(eventtime)
                estimate = duration / progress if progress > 0 else None
        if estimate is not None:
            estimate = max(duration, float(estimate))
        remaining = None if estimate is None else max(0.0, estimate - duration)
        return duration, remaining

    def _draw_print_status(self, status):
        self.renderer.send([
            self.renderer.fill(20, 94, 760, 34),
            self.renderer.text(25, 110, status, "d9e4e8",
                               "JetBrainsMono 8pt", "left", "middle",
                               max_width=750, truncate=True)])

    def _handle_print_action(self, action):
        stats = self.print_stats.get_status(self.reactor.monotonic())["state"]
        if action in ("print.pause", "print.filament"):
            if not self._print_controls_ready():
                self._toast("Available after print preparation")
                return
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
                self._filament_request_token = getattr(
                    self, "_filament_request_token", 0) + 1
                token = self._filament_request_token
                self._run_script("PAUSE")
                current = self.print_stats.get_status(
                    self.reactor.monotonic())["state"]
                if (token != self._filament_request_token
                        or current != "paused"
                        or self.cancel_requested
                        or self.page not in (Page.PRINTING, Page.PAUSED)):
                    logging.info(
                        "[feather_screen] stale filament request discarded "
                        "token=%s current=%s page=%s cancel=%s",
                        token, current, self.page.name, self.cancel_requested)
                    return
            self._open_filament(True)
        elif action == "print.z" and stats in ("printing", "paused"):
            manager = getattr(self, "feature_manager", None)
            if manager is not None:
                manager.get("z").open_live_z()
            else:
                if not self._live_z_adjust_allowed(self.reactor.monotonic()):
                    raise RuntimeError("Z adjust is not available yet")
                self.live_z_dialog = None
                self._begin_z_weight_gauge()
                self._show_page(Page.LIVE_Z_OFFSET)
        elif action == "print.cancel" and stats in ("printing", "paused"):
            self._show_page(Page.CANCEL_CONFIRM)
        elif action == "print.cancel.confirm" and stats in ("printing", "paused"):
            self._filament_request_token = getattr(
                self, "_filament_request_token", 0) + 1
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
            commands = self.renderer.begin_page(
                "STOPPING PRINT")
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
        commands = self.renderer.begin_page(
            "Cancel print?", back=True)
        commands.append(self.renderer.text(400, 170, "The current print will stop",
                                           "ff9000", "Roboto 16pt", "center", "middle"))
        commands += self.renderer.button("print.cancel.back", 100, 285, 260, 100,
                                         "GO BACK", font="Roboto Bold 16pt")
        commands += self.renderer.button("print.cancel.confirm", 440, 285, 260, 100,
                                         "CANCEL",
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


    def _render_settings(self):
        brightness = int(self._setting("backlight", 50))
        sound = bool(self._setting("sound", 1))
        light_available = getattr(self, "chamber_light", None) is not None
        light = (self._chamber_light_brightness()
                 if light_available else None)
        theme = str(getattr(self.renderer, "theme_name", "DEFAULT"))
        commands = self.renderer.begin_page("Settings", back=True)
        rows = (
            ("SCREEN BRIGHTNESS", brightness, "settings.brightness", 67, True),
            ("CHAMBER LIGHT", light, "settings.led", 151, light_available),
        )
        for label, value, prefix, y, enabled in rows:
            commands += [
                self.renderer.fill(25, y, 750, 70, "050c0f"),
                self.renderer.stroke(25, y, 750, 70, "295c66", 1),
                self.renderer.text(44, y + 22, label, "35d9e6",
                                   "JetBrainsMono Bold 8pt"),
                self.renderer.text(425, y + 36,
                                   "%d%%" % value if enabled else "--",
                                   "d9e4e8" if enabled else "56656c",
                                   "JetBrainsMono 12pt", "center"),
            ]
            commands += self.renderer.button(prefix + ".minus", 525, y + 12,
                                             105, 46, "-5",
                                             state=("enabled" if enabled
                                                    else "disabled"))
            commands += self.renderer.button(prefix + ".plus", 650, y + 12,
                                             105, 46, "+5",
                                             state=("enabled" if enabled
                                                    else "disabled"))
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
        if action.startswith("settings.led."):
            if getattr(self, "chamber_light", None) is None:
                raise RuntimeError("Chamber light is unavailable")
            delta = -5 if action.endswith("minus") else 5
            value = max(
                0, min(100, self._chamber_light_brightness() + delta))
            self._run_script(
                "SET_LED LED=chamber_light WHITE=%g SYNC=0" % (value / 100.0))
            self._render_settings()
            return
        if action == "settings.sound":
            key, value = "sound", 0 if self._setting("sound", 1) else 1
            self._animate_settings_toggle(action, bool(value))
        else:
            key = "backlight"
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

    def _chamber_light_brightness(self):
        try:
            value = int(self._setting("chamber_light", 50))
        except (TypeError, ValueError):
            value = 50
        return max(0, min(100, value))

    def _animate_settings_toggle(self, action, active):
        scheduler = lambda callback, delay: self.reactor.register_callback(
            callback, self.reactor.monotonic() + delay)
        renderer = getattr(self, "renderer", None)
        animate = getattr(renderer, "animate_toggle", None)
        if animate is not None:
            animate(action, bool(active), scheduler)
        block = getattr(renderer, "block_input", None)
        if block is not None:
            block()

    def _mod_parameters(self):
        return mod_ui.visible_parameters(self.params)

    def _render_mod_settings(self):
        self._require_idle()
        parameters = self._mod_parameters()
        pagination = Pagination(
            parameters, getattr(self, "mod_page", 0),
            mod_ui.VISIBLE_ROWS)
        self.mod_page = pagination.page
        total = pagination.total
        start = pagination.start
        visible = pagination.visible
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
            title = str(param.label).upper()
            detail = mod_ui.description(param)
            commands += [
                self.renderer.fill(row_x, y, row_width, row_height, "050c0f"),
                self.renderer.stroke(row_x, y, row_width, row_height,
                                     "295c66", 1),
                self.renderer.text(40, y + 14, title, "35d9e6",
                                   "JetBrainsMono Bold 8pt", max_width=430,
                                   truncate=True),
                self.renderer.text(40, y + 32, param.key, "56656c",
                                   "JetBrainsMono 8pt"),
                self.renderer.text(40, y + 50, detail, "d9e4e8",
                                   "JetBrainsMono 8pt", max_width=430,
                                   truncate=True),
            ]
            kind = mod_ui.parameter_kind(param)
            value = mod_ui.display_value(self.params, param)
            state = "disabled" if getattr(param, "readonly", False) else "enabled"
            if kind == "bool":
                commands += self.renderer.toggle(
                    action, 624, y + 13, 76, 38, value == "ON",
                    enabled=state == "enabled")
            else:
                label = value + " >"
                commands += self.renderer.button(
                    action, 520, y + 9, 180, 46, label, state=state,
                    font="JetBrainsMono 8pt")

        previous_state = (
            "enabled" if pagination.has_previous else "disabled")
        next_state = "enabled" if pagination.has_next else "disabled"
        commands += self.renderer.button("mod.prev", 728, 88, 52, 48, "^",
                                         state=previous_state,
                                         font="JetBrainsMono 12pt")
        commands += self.renderer.button("mod.next", 728, 365, 52, 48, "v",
                                         state=next_state,
                                         font="JetBrainsMono 12pt")
        track_y, track_height = 146, 209
        commands += [self.renderer.stroke(749, track_y, 10, track_height,
                                          "295c66", 1)]
        thumb_height = max(18, track_height // pagination.page_count)
        thumb_y = (track_y if pagination.page_count == 1 else
                   track_y + (track_height - thumb_height) * self.mod_page
                   // (pagination.page_count - 1))
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
        restart_effect = mod_ui.restart_effect(param)
        previous_value = self.params.variables.get(param.key)
        block = getattr(self.renderer, "block_input", None)
        if block is not None:
            block()
        if restart_effect is None:
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
        changed = previous_value != self.params.variables.get(param.key)
        if restart_effect is not None and changed:
            # set_value() only schedules its change hook. Draw and latch the
            # restart UI synchronously before the reactor can run that hook.
            self.mod_update_pending = False
            self.mod_update_token += 1
            self.mod_update_complete = None
            logging.info(
                "[feather_screen] parameter requires %s restart key=%s",
                restart_effect, param.key)
            self._begin_restart_ui()
            return result
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
        if kind in ("int", "float") and (
                action in ("mod.backspace", "mod.sign", "mod.dot")
                or action.startswith("mod.key.")):
            token = ({"mod.backspace": "backspace", "mod.sign": "sign",
                      "mod.dot": "decimal"}.get(action))
            if token is None:
                token = action[len("mod.key."):]
            self.mod_edit_value = mod_ui.numeric_input_spec(param).apply(
                self.mod_edit_value, token)
        elif action == "mod.backspace":
            self.mod_edit_value = self.mod_edit_value[:-1]
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
        commands.append(self.renderer.text(
            25, 76, mod_ui.description(param), "d9e4e8",
            "JetBrainsMono 8pt", max_width=650, max_height=44, wrap=True,
            truncate=True))
        if mod_ui.restart_effect(param) is not None:
            commands.append(self.renderer.text(
                25, 108, "APPLYING THIS VALUE RESTARTS KLIPPER.", "f2c94c",
                "JetBrainsMono 8pt"))
        if param.key == "feather_theme":
            options = list(self.renderer.theme_names(reload=True))
            if self.mod_enum_selection not in options:
                self.mod_enum_selection = "DEFAULT"
        else:
            options = mod_ui.enum_names(param)
        pagination = Pagination(
            options, getattr(self, "mod_enum_page", 0), 4)
        self.mod_enum_page = pagination.page
        start = pagination.start
        for row, name in enumerate(pagination.visible):
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
        if pagination.page_count > 1:
            commands += self.renderer.button(
                "mod.enum.prev", 25, 390, 120, 47, "<",
                state=("enabled" if pagination.has_previous
                       else "disabled"),
                font="JetBrainsMono Bold 8pt")
            commands += self.renderer.button(
                "mod.cancel", 155, 390, 220, 47, "CANCEL", state="danger",
                font="JetBrainsMono Bold 8pt")
            commands += self.renderer.button(
                "mod.apply", 425, 390, 220, 47, "APPLY",
                font="JetBrainsMono Bold 8pt")
            commands += self.renderer.button(
                "mod.enum.next", 655, 390, 120, 47, ">",
                state=("enabled" if pagination.has_next else "disabled"),
                font="JetBrainsMono Bold 8pt")
            commands.append(self.renderer.text(
                750, 80, "%d/%d" % (
                    pagination.page + 1, pagination.page_count),
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
        if kind in ("int", "float"):
            commands += self._render_mod_numeric_keys(param)
            self.renderer.send(commands)
            return
        commands += [
            self.renderer.text(25, 73, str(param.label).upper(), "35d9e6",
                               "JetBrainsMono Bold 12pt"),
            self.renderer.text(25, 98, param.key, "56656c",
                               "JetBrainsMono 8pt"),
            self.renderer.text(280, 98, mod_ui.description(param), "d9e4e8",
                               "JetBrainsMono 8pt", max_width=490,
                               truncate=True),
            self.renderer.fill(25, 120, 750, 53, "050c0f"),
            self.renderer.stroke(25, 120, 750, 53, "35d9e6", 2),
            self.renderer.text(42, 147, self.mod_edit_value or "_", "35d9e6",
                               "JetBrainsMono 12pt", max_width=710,
                               truncate=True),
        ]
        commands += self._render_mod_text_keys()
        self.renderer.send(commands)

    def _render_mod_numeric_keys(self, param):
        spec = mod_ui.numeric_input_spec(param)
        actions = dict((digit, "mod.key.%s" % digit)
                       for digit in "0123456789")
        actions.update({
            "backspace": "mod.backspace",
            "confirm": "mod.save",
        })
        if spec.allows_decimal:
            actions["decimal"] = "mod.dot"
        if spec.allows_negative:
            actions["sign"] = "mod.sign"
        return self.renderer.numeric_keypad(
            18, 65, 764, 370, param.key, self.mod_edit_value, actions,
            subtitle=param.label, mode=spec, confirm_label="SAVE")

    def _render_mod_text_keys(self):
        commands = []
        rows = keyboard_rows(
            self.mod_keyboard_symbols, self.mod_keyboard_shift)
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
        commands = self.renderer.begin_page("Network", back=True)
        lines = ["Mode: %s" % self.network_status.get("mode", "OFFLINE")]
        if self.network_status.get("ssid"):
            lines.append("SSID: %s   Signal: %s dBm" %
                         (self.network_status["ssid"], self.network_status.get("signal") or "?"))
        lines.append("IP: %s" % (self.network_status.get("ip") or "Offline"))
        for index, line in enumerate(lines):
            commands.append(self.renderer.text(
                400, 75 + index * 35, line, "ffffff", "Roboto 10pt", "center",
                max_width=660, truncate=True))
        commands += self.renderer.button("net.scan", 55, 190, 320, 150,
                                         "WI-FI", font="JetBrainsMono 12pt")
        commands += self.renderer.button("net.ethernet", 425, 190, 320, 150,
                                         "ETHERNET DHCP", font="JetBrainsMono 12pt")
        commands += self.renderer.button("net.retry", 270, 365, 260, 60, "RETRY STATUS")
        self.renderer.send(commands)

    def _handle_network_action(self, action):
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
            pagination = Pagination(
                self.networks, self.network_page, NETWORK_ROWS)
            offset = pagination.absolute_index(index)
            if offset is not None:
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
        pagination = Pagination(
            self.networks, self.network_page, NETWORK_ROWS)
        self.network_page = pagination.page
        commands = self.renderer.begin_page("Select Wi-Fi", back=True)
        rows = pagination.visible
        for index, network in enumerate(rows):
            y = 62 + index * 65
            label = "%s   %d dBm" % (network["ssid"], network["signal"])
            commands += self.renderer.button("net.item%d" % index, 30, y, 740, 56,
                                             label, font="JetBrainsMono 12pt")
        commands += self.renderer.button("net.prev", 125, 390, 150, 50, "< Page",
                                         active=pagination.has_previous)
        commands += self.renderer.button("net.rescan", 325, 390, 150, 50, "RESCAN")
        commands += self.renderer.button("net.next", 525, 390, 150, 50, "Page >",
                                         active=pagination.has_next)
        if not rows:
            commands.append(self.renderer.text(400, 230, "No supported networks",
                                               "606060", "Roboto 16pt", "center", "middle"))
        self.renderer.send(commands)

    def _render_keyboard(self):
        ssid = self.selected_network["ssid"]
        commands = self.renderer.begin_page(ssid, back=True)
        masked = self.password if self.password_visible else "*" * len(self.password)
        commands.append(self.renderer.stroke(55, 62, 690, 42, "872187", 3))
        commands.append(self.renderer.text(
            70, 83, masked, "ffffff", "JetBrainsMono 12pt", "left", "middle",
            max_width=660, truncate=True))

        rows = keyboard_rows(self.keyboard_symbols, self.keyboard_shift)
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
        commands.append(self.renderer.text(
            400, 90, status.get("filename", "Unknown"), "ffffff",
            "Roboto Bold 14pt", "center", max_width=720, truncate=True))
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
        commands.append(self.renderer.text(
            400, 150, text, "ffffff", "JetBrainsMono 12pt", "center",
            "middle", max_width=640, max_height=100, wrap=True,
            truncate=True))
        commands += self.renderer.button("recovery.confirm", 220, 300, 360, 100,
                                         "CLEANUP" if cleanup else "RESTORE",
                                         state="danger" if cleanup else "enabled",
                                         font="Roboto Bold 16pt")
        self.renderer.send(commands)

    def _render_action_prompt(self):
        prompt = self.action_prompt or {
            "title": "Prompt", "text": [], "rows": [], "footer": []}
        rows = prompt["rows"]
        rows_per_page = 3
        pagination = Pagination(
            rows, self.action_prompt_page, rows_per_page)
        self.action_prompt_page = pagination.page
        visible_rows = pagination.visible

        commands = self.renderer.begin_page("KLIPPER PROMPT")
        commands += self.renderer.panel(
            30, 67, 740, 365, border="295c66", background="050c0f")
        commands.append(self.renderer.text(
            400, 102, prompt["title"], "35d9e6",
            "JetBrainsMono Bold 16pt", "center", "middle",
            max_width=680, truncate=True))
        text = "\n".join(prompt["text"])
        if text:
            commands.append(self.renderer.text(
                400, 158, text, "d9e4e8", "JetBrainsMono 8pt",
                "center", "middle", max_width=680, max_height=76,
                wrap=True, truncate=True))

        if pagination.page_count > 1:
            commands += self.renderer.button(
                "prompt.prev", 48, 77, 70, 40, "<",
                state=("enabled" if pagination.has_previous else "disabled"),
                font="JetBrainsMono Bold 12pt")
            commands += self.renderer.button(
                "prompt.next", 682, 77, 70, 40, ">",
                state=("enabled" if pagination.has_next else "disabled"),
                font="JetBrainsMono Bold 12pt")

        for row_index, row in enumerate(visible_rows):
            gap = 10
            margin = 48
            width = max(
                1, (704 - gap * (len(row) - 1)) // max(1, len(row)))
            y = 213 + row_index * 55
            for column, button in enumerate(row):
                commands += self.renderer.button(
                    button["action"], margin + column * (width + gap), y,
                    width, 45, button["label"], state=button["state"],
                    font="JetBrainsMono 8pt")

        footer = prompt["footer"]
        if footer:
            gap = 10
            margin = 48
            width = max(
                1, (704 - gap * (len(footer) - 1))
                // max(1, len(footer)))
            for column, button in enumerate(footer):
                commands += self.renderer.button(
                    button["action"], margin + column * (width + gap), 374,
                    width, 42, button["label"], state=button["state"],
                    font="JetBrainsMono 8pt")
        self.renderer.send(commands)

    def _handle_recovery_action(self, action):
        if action == "recovery.later":
            self._run_script(
                "RESPOND TYPE=command MSG=action:prompt_end")
        elif action in ("recovery.restore", "recovery.cleanup"):
            self.recovery_action = action.split(".", 1)[1]
            self._show_page(Page.RECOVERY_CONFIRM)
        elif action == "recovery.confirm":
            command = "RESURRECT" if self.recovery_action == "restore" else "RESURRECT_ABORT"
            status = ("STARTING RECOVERY..."
                      if command == "RESURRECT" else "STARTING CLEANUP...")
            manager = getattr(self, "feature_manager", None)
            if manager is not None:
                manager.get("calibration").begin_recovery(status)
            else:
                self.calibration_kind = "recovery"
                self.print_status_text = status
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
