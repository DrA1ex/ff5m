## Dashboard, print, settings, and mod-parameter pages for Feather.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import errno
import logging
import os
import signal
import subprocess
import time

from ui import ThemeColor, ThemeRole
from ui.lazy import LazyModule
from ff5m_ui.screen import ScreenPage
from ff5m_ui.print_state import PrintState

from feather_keyboard import TEXT_KEYBOARD, is_keyboard_action
from feather_files import FileEntry, scan_gcode_files
from feather_pagination import Pagination, pagination_footer
from feather_network_ui import FeatherNetworkPagesMixin


home_page = LazyModule("ff5m_ui.home.page")
home_state = LazyModule("ff5m_ui.home.state")
mod_ui = LazyModule("feather_mod_settings")

FILE_ROWS = 5
FILE_CACHE_TTL = 5.0


class FeatherPagesMixin(FeatherNetworkPagesMixin):
    def _render_home(self):
        return home_page.render(self)

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
        return home_page.update(self, eventtime)

    def _dashboard_job(self, eventtime):
        return home_state.dashboard_job(self, eventtime)

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

    def _normalize_file_source(self):
        source = getattr(self, "file_source", "internal")
        usb_storage = getattr(self, "usb_storage", None)
        if source == "usb" and (
                usb_storage is None or not usb_storage.available):
            source = "internal"
            self.file_source = source
            self.file_page = 0
        return source

    def _build_file_scan_task(self, source):
        root = self.virtual_sdcard.sdcard_dirname
        history = getattr(self, "print_history", None)
        history_snapshot = dict(getattr(history, "timestamps", {}))
        usb_storage = getattr(self, "usb_storage", None)
        if source == "usb":
            mount_point = usb_storage.mount_point
            history_prefix = os.path.relpath(mount_point, root)

            def scan_usb():
                try:
                    return scan_gcode_files(
                        mount_point, history_snapshot,
                        history_prefix=history_prefix)
                except RuntimeError as exc:
                    # Removable media can disappear between the monitor tick
                    # and directory traversal. Keep the UI responsive; the
                    # next tick will remove the USB entry if its mount is gone.
                    logging.info(
                        "[feather_screen] USB file scan deferred: %s", exc)
                    return []
            return scan_usb

        excluded = ((usb_storage.mount_point,)
                    if usb_storage is not None else ())
        usb_available = bool(
            usb_storage is not None and usb_storage.available)
        usb_mount_point = (usb_storage.mount_point
                           if usb_storage is not None else None)

        def scan_internal():
            entries = scan_gcode_files(
                root, history_snapshot, excluded_paths=excluded)
            if usb_available:
                entries.insert(0, FileEntry(
                    "USB", usb_mount_point, directory=True))
            return entries
        return scan_internal

    def _load_file_entries(self):
        """Synchronous compatibility helper for tests and maintenance tools."""
        source = self._normalize_file_source()
        self.file_entries = self._build_file_scan_task(source)()

    def _invalidate_file_entries(self, source=None):
        cache = getattr(self, "file_entry_cache", None)
        if cache is None:
            return
        loaded_at = getattr(self, "file_entry_loaded_at", None)
        if source is None:
            cache.clear()
            if loaded_at is not None:
                loaded_at.clear()
        else:
            cache.pop(source, None)
            if loaded_at is not None:
                loaded_at.pop(source, None)

    def _expire_file_entries_if_stale(self, source):
        cache = getattr(self, "file_entry_cache", {})
        if source not in cache:
            return True
        loaded_at = getattr(self, "file_entry_loaded_at", {})
        timestamp = loaded_at.get(source)
        now = self.reactor.monotonic()
        if timestamp is None or now - timestamp >= FILE_CACHE_TTL:
            self._invalidate_file_entries(source)
            return True
        return False

    def _render_file_loading(self, source):
        self.file_scan_loading = True
        self.file_scan_source = source
        self.file_scan_phase = 0
        label = ("LOADING USB FILES..." if source == "usb"
                 else "LOADING PRINT FILES...")
        self.renderer.loader(label, self.file_scan_phase)

    def _start_file_scan(self, source):
        if (getattr(self, "file_scan_loading", False)
                and getattr(self, "file_scan_source", None) == source
                and getattr(self, "file_scan_token", 0) > 0):
            return
        self.file_scan_token = getattr(self, "file_scan_token", 0) + 1
        token = self.file_scan_token
        self._render_file_loading(source)
        task = self._build_file_scan_task(source)
        submitted = self.file_scan_worker.submit(
            task, lambda entries, error:
            self._finish_file_scan(token, source, entries, error))
        if not submitted:
            self._finish_file_scan(
                token, source, None, RuntimeError("File scanner stopped"))

    def _finish_file_scan(self, token, source, entries, error):
        if token != getattr(self, "file_scan_token", 0):
            return
        self.file_scan_loading = False
        self.file_scan_source = None
        if error is not None:
            logging.error(
                "[feather_screen] unable to scan %s files: %s",
                source, error)
            entries = []
            message = "Unable to load USB files" if source == "usb" \
                else "Unable to load print files"
        else:
            message = None
        self.file_entry_cache[source] = entries
        self.file_entry_loaded_at[source] = self.reactor.monotonic()
        if source == getattr(self, "file_source", "internal"):
            self.file_entries = entries
        if (self.page == ScreenPage.FILE_BROWSER
                and source == getattr(self, "file_source", "internal")):
            self._render_file_browser()
            if message is not None:
                self._toast(message)

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
            self.last_job_path = os.path.relpath(path, root).replace(
                os.sep, "/")
            self.last_job_name = os.path.basename(path)
            self._invalidate_file_entries()

    def _render_file_browser(self):
        # Isolated tests and third-party extensions that construct the mixin
        # without FeatherScreen keep the old synchronous helper behavior.
        if getattr(self, "file_scan_worker", None) is None:
            self._load_file_entries()
            return self._render_file_entries()
        source = self._normalize_file_source()
        if source not in self.file_entry_cache:
            if not (getattr(self, "file_scan_loading", False)
                    and getattr(self, "file_scan_source", None) == source):
                self._start_file_scan(source)
            return
        self.file_entries = self.file_entry_cache[source]
        self._render_file_entries()

    def _render_file_entries(self):
        pagination = Pagination(self.file_entries, self.file_page, FILE_ROWS)
        self.file_page = pagination.page
        usb_page = getattr(self, "file_source", "internal") == "usb"
        title = "USB files" if usb_page else "Print files"
        commands = self.renderer.begin_page(title, back=True)
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
            commands.append(self.renderer.text(400, 230, "No G-code files", ThemeColor.DIM,
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
            self.file_page = 0
            self._invalidate_file_entries(
                getattr(self, "file_source", "internal"))
            self._render_file_browser()
        elif action == "file.mesh.rebuild":
            self.file_confirm_rebuild_mesh = not bool(getattr(
                self, "file_confirm_rebuild_mesh", False))
            if not self.file_confirm_rebuild_mesh:
                self.file_confirm_auto_mesh = False
            self._render_file_confirm()
        elif action == "file.mesh.auto":
            if not getattr(self, "file_confirm_rebuild_mesh", False):
                return
            self.file_confirm_auto_mesh = not bool(getattr(
                self, "file_confirm_auto_mesh", False))
            self._render_file_confirm()
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
                self._expire_file_entries_if_stale("usb")
                self._render_file_browser()
                return
            self.selected_file = entry
            self.file_confirm_return_page = ScreenPage.FILE_BROWSER
            self.file_confirm_repeat = False
            self.file_confirm_rebuild_mesh = False
            self.file_confirm_auto_mesh = False
            self._show_page(ScreenPage.FILE_CONFIRM)

    def _open_last_job(self):
        self._require_idle()
        relative = getattr(self, "last_job_path", None)
        if not relative:
            history = getattr(self, "print_history", None)
            relative = (history.latest_path()
                        if history is not None else None)
        if not relative:
            raise RuntimeError("No previous print is available")
        root = os.path.realpath(self.virtual_sdcard.sdcard_dirname)
        path = os.path.realpath(os.path.join(root, relative))
        if not os.path.isfile(path) or not path.startswith(root + os.sep):
            raise RuntimeError("The last print file is no longer available")
        stat = os.stat(path)
        self.last_job_path = relative
        self.last_job_name = os.path.basename(relative)
        self.selected_file = FileEntry(
            self.last_job_name, path, size=stat.st_size, mtime=stat.st_mtime)
        self.file_confirm_return_page = ScreenPage.IDLE_HOME
        self.file_confirm_repeat = True
        self.file_confirm_rebuild_mesh = False
        self.file_confirm_auto_mesh = False
        self._show_page(ScreenPage.FILE_CONFIRM)

    def _render_file_confirm(self):
        entry = self.selected_file
        repeat = getattr(self, "file_confirm_repeat", False)
        rebuild_mesh = bool(getattr(
            self, "file_confirm_rebuild_mesh", False))
        auto_mesh = bool(getattr(self, "file_confirm_auto_mesh", False))
        rebuild_hint = (
            "KAMP ENABLED - FULL MESH WILL RUN INSTEAD"
            if bool(self._setting("use_kamp", False))
            else "FOR THIS PRINT ONLY")
        commands = self.renderer.begin_page(
            "Print again?" if repeat else "Start print?", back=True)
        commands.append(self.renderer.text(
            400, 96, entry["name"], ThemeColor.BRIGHT, "Roboto Bold 16pt",
            "center", "middle", max_width=720, truncate=True))
        commands.append(self.renderer.text(
            400, 140, self._format_size(entry["size"]), ThemeColor.PRIMARY,
            "Roboto 12pt", "center", "middle"))
        commands += self._file_confirm_option(
            "file.mesh.rebuild", 170, "REBUILD BED MESH",
            rebuild_hint, rebuild_mesh)
        if rebuild_mesh:
            commands += self._file_confirm_option(
                "file.mesh.auto", 238, "SAVE MESH FOR FUTURE PRINTS",
                "YOU'LL BE ASKED AFTER PRINT", auto_mesh)
        commands += self.renderer.button("file.start", 220, 316, 360, 96,
                                         "PRINT AGAIN" if repeat else "START PRINT",
                                         font="Roboto Bold 16pt")
        self.renderer.send(commands)

    def _file_confirm_option(self, action, y, label, subtitle, active):
        commands = [
            self.renderer.text(44, y + 12, label, ThemeColor.PRIMARY,
                               "JetBrainsMono Bold 8pt"),
            self.renderer.text(44, y + 34, subtitle, ThemeColor.DIM,
                               "JetBrainsMono 8pt"),
        ]
        commands += self.renderer.toggle(
            action, 679, y + 5, 76, 38, active)
        return commands

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
        self.last_job_path = relpath.replace(os.sep, "/")
        self.last_job_name = os.path.basename(relpath)
        rebuild_mesh = bool(getattr(
            self, "file_confirm_rebuild_mesh", False))
        auto_mesh = bool(getattr(
            self, "file_confirm_auto_mesh", False) and rebuild_mesh)
        # The virtual-SD timer cannot consume the file until this script
        # yields. Staging after file acceptance avoids a stale one-print choice
        # when SDCARD_PRINT_FILE rejects the path or an already-active job.
        force_leveling = "True" if rebuild_mesh else "None"
        # G-code parsing consumes the outer quotes; literal_eval() in
        # SET_GCODE_VARIABLE must still receive the inner quoted string.
        mesh_name = "'\"auto\"'" if auto_mesh else "None"
        self._run_script("\n".join((
            'SDCARD_PRINT_FILE FILENAME="%s"' % escaped,
            "SET_GCODE_VARIABLE MACRO=START_PRINT "
            "VARIABLE=feather_force_leveling VALUE=%s" % force_leveling,
            "SET_GCODE_VARIABLE MACRO=START_PRINT "
            "VARIABLE=feather_mesh_name VALUE=%s" % mesh_name,
        )))
        self.file_confirm_rebuild_mesh = False
        self.file_confirm_auto_mesh = False

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
                                           ThemeColor.PRIMARY, "JetBrainsMono Bold 12pt",
                                           "left", "middle", max_width=750,
                                           truncate=True))
        commands.append(self.renderer.text(
            25, 110, self._display_status_text(
                self.reactor.monotonic()), ThemeColor.TEXT,
            "JetBrainsMono 8pt",
            "left", "middle", max_width=750, truncate=True))
        commands += [
            self.renderer.text(25, 142, "PROGRESS", ThemeColor.PRIMARY,
                               "JetBrainsMono 8pt", "left", "middle"),
            self.renderer.stroke(25, 162, 750, 34, ThemeColor.BORDER, 2),
            self.renderer.fill(25, 208, 750, 1, ThemeColor.BORDER),
            self.renderer.text(25, 226, "ELAPSED", ThemeColor.PRIMARY,
                               "JetBrainsMono 8pt", "left", "middle"),
            self.renderer.text(410, 226, "REMAINING", ThemeColor.PRIMARY,
                               "JetBrainsMono 8pt", "left", "middle"),
            self.renderer.fill(395, 216, 1, 48, ThemeColor.BORDER),
            self.renderer.fill(25, 273, 750, 1, ThemeColor.BORDER),
            self.renderer.text(25, 291, "LAYER", ThemeColor.PRIMARY,
                               "JetBrainsMono 8pt", "left", "middle"),
            self.renderer.text(410, 291, "HEIGHT", ThemeColor.PRIMARY,
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
        start = getattr(self, "start_print_macro", None)
        if start is None:
            return True
        return bool(getattr(start, "variables", {}).get(
            "print_started", False))

    def _update_print_progress(self, eventtime):
        if self.page not in (ScreenPage.PRINTING, ScreenPage.PAUSED):
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
        motion_report = getattr(self, "motion_report", None)
        if motion_report is not None:
            position = motion_report.get_status(eventtime).get(
                "live_position", position)
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
                               ThemeColor.PRIMARY, "JetBrainsMono 12pt", "right", "middle"),
            self.renderer.fill(31, 168, 738, 22),
            self.renderer.fill(31, 168, width, 22, ThemeColor.PRIMARY),
            self.renderer.fill(25, 238, 350, 28),
            self.renderer.text(25, 252, values[0], ThemeColor.TEXT,
                               "JetBrainsMono 12pt", "left", "middle"),
            self.renderer.fill(410, 238, 350, 28),
            self.renderer.text(410, 252, values[1], ThemeColor.TEXT,
                               "JetBrainsMono 12pt", "left", "middle"),
            self.renderer.fill(25, 303, 350, 30),
            self.renderer.text(25, 318, values[2], ThemeColor.TEXT,
                               "JetBrainsMono 12pt", "left", "middle"),
            self.renderer.fill(410, 303, 350, 30),
            self.renderer.text(410, 318, "%.2f MM" % values[3], ThemeColor.TEXT,
                               "JetBrainsMono 12pt", "left", "middle"),
        ]
        self.renderer.send(commands)

    def _print_progress(self, eventtime, stats=None):
        stats = stats or self.print_stats.get_status(eventtime)
        status = self.virtual_sdcard.get_status(eventtime)
        duration = stats.get("print_duration") or 0.0
        sd_progress = status.get("progress") or 0.0

        if not self._print_controls_ready():
            self._progress_start = (duration, sd_progress)
            self._progress_floor = 0.0
            self._progress_source = None
            return 0.0

        progress_start = getattr(self, "_progress_start", (0.0, 0.0))
        if progress_start is None:
            progress_start = self._progress_start = (duration, sd_progress)
        duration_start, sd_start = progress_start
        print_duration = max(0.0, duration - duration_start)
        sd_progress = ((sd_progress - sd_start) / (1.0 - sd_start)
                       if sd_start < 1.0 else 0.0)

        display_status = getattr(self, "display_status", None)
        m73_expiry = float(
            getattr(display_status, "expire_progress", 0.0) or 0.0)
        if m73_expiry > getattr(self, "_m73_start_expiry", 0.0):
            self._m73_active = True
        m73_progress = getattr(display_status, "progress", None)
        estimate = (getattr(self.virtual_sdcard, "estimate_print_time", None)
                    or status.get("estimate_print_time"))

        if getattr(self, "_m73_active", False) and m73_progress is not None:
            progress, source = m73_progress, "M73"
        elif self._restored_print_active(eventtime):
            progress, source = sd_progress, "SD"
        elif estimate:
            progress, source = min(0.99, print_duration / estimate), "TIME"
        else:
            progress, source = sd_progress, "SD"

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
        restored = self._restored_print_active(eventtime)
        estimate = getattr(self.virtual_sdcard, "estimate_print_time", None)
        if not estimate:
            if restored:
                return duration, None
            info = stats.get("info", {})
            current = info.get("current_layer")
            total = info.get("total_layer")
            if current and total:
                estimate = duration / max(current, 1) * total
            else:
                if progress is None:
                    progress = self._print_progress(eventtime)
                estimate = duration / progress if progress > 0 else None
        if restored and estimate is not None and progress is not None:
            remaining = float(estimate) * max(
                0.0, 1.0 - max(0.0, min(1.0, progress)))
            return duration, remaining
        if estimate is not None:
            estimate = max(duration, float(estimate))
        remaining = None if estimate is None else max(0.0, estimate - duration)
        return duration, remaining

    def _draw_print_status(self, status):
        self.renderer.send([
            self.renderer.fill(20, 94, 760, 34),
            self.renderer.text(25, 110, status, ThemeColor.TEXT,
                               "JetBrainsMono 8pt", "left", "middle",
                               max_width=750, truncate=True)])

    def _update_operation_context(self, eventtime):
        operation = self._operation_context_status(eventtime)
        revision = operation["revision"]
        if revision == getattr(self, "_last_operation_revision", -1):
            return
        self._last_operation_revision = revision
        if self._page_paint_allowed(ScreenPage.PRINTING, ScreenPage.PAUSED):
            self._draw_print_status(
                self._display_status_text(status=operation))

    cmd_FEATHER_ABORT_help = "Request cancellation of the active operation"
    def _request_operation_cancel(self):
        manager = getattr(self, "operation_context", None)
        if manager is None:
            result = {"status": "not_cancelable", "accepted": False,
                      "request_id": None, "target_name": None,
                      "blocker_name": None}
        else:
            result = manager.request_cancel()
        return result

    def _clear_operation_cancel_request(self):
        manager = getattr(self, "operation_context", None)
        if manager is None:
            return {"status": "not_pending", "cleared": False,
                    "request_id": None}
        return manager.clear_cancel(
            getattr(self, "operation_cancel_request_id", None))

    def cmd_FEATHER_ABORT(self, gcmd):
        """Request cooperative cancellation outside the G-code mutex."""
        result = self._request_operation_cancel()
        if result["accepted"]:
            gcmd.respond_raw(
                "Feather cancellation requested: %s"
                % (result["target_name"],))
            if self._temperature_wait_active():
                self._run_immediate_command("M108")
        else:
            gcmd.respond_raw(
                "The active operation cannot be cancelled safely")

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
                        or self.page not in (ScreenPage.PRINTING, ScreenPage.PAUSED)):
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
                self._show_page(ScreenPage.LIVE_Z_OFFSET)
        elif action == "print.cancel" and stats in ("printing", "paused"):
            self._open_operation_cancel(
                ScreenPage.PAUSED if stats == "paused" else ScreenPage.PRINTING,
                self._accept_print_operation_cancel,
                self._clear_print_operation_cancel)

    def _open_operation_cancel(
            self, return_page, on_accept=None, on_clear=None):
        operation = self._operation_context_status()
        self.operation_cancel_return_page = return_page
        self.operation_cancel_on_accept = on_accept
        self.operation_cancel_on_clear = on_clear
        self.operation_cancel_request_id = None
        self.operation_cancel_target_name = (
            operation.get("cancel_target_name")
            or operation.get("cancel_blocker_name")
            or (operation.get("context_path") or (None,))[-1])
        self.operation_cancel_target_mode = operation.get(
            "cancel_target_mode")
        self.cancel_mode = ("confirm" if operation["cancel_available"]
                            else "not_cancelable")
        self._show_page(ScreenPage.CANCEL_CONFIRM)

    def _close_operation_cancel(self):
        if getattr(self, "cancel_mode", None) == "pending":
            return
        return_page = getattr(
            self, "operation_cancel_return_page", ScreenPage.IDLE_HOME)
        self._reset_operation_cancel()
        self._show_page(return_page)

    def _reset_operation_cancel(self):
        self.cancel_mode = None
        self.operation_cancel_on_accept = None
        self.operation_cancel_on_clear = None
        self.operation_cancel_request_id = None
        self.operation_cancel_target_name = None
        self.operation_cancel_target_mode = None

    def _handle_operation_cancel_action(self, action):
        if action == "operation.cancel.back":
            self._close_operation_cancel()
            return
        if (action == "operation.cancel.continue"
                and self.cancel_mode == "pending"):
            result = self._clear_operation_cancel_request()
            if not result.get("cleared", False):
                # Losing this race is normal: the safe point may already have
                # taken the request. Repainting alone looked like a dead button.
                self._render_cancel_confirm()
                self._toast("CANCELLATION ALREADY STARTED")
                return
            callback = self.operation_cancel_on_clear
            if callback is not None:
                callback(result)
            self.cancel_mode = "cleared"
            self._close_operation_cancel()
            return
        if action != "operation.cancel.confirm" or self.cancel_mode != "confirm":
            return
        result = self._request_operation_cancel()
        if not result["accepted"]:
            self.cancel_mode = "not_cancelable"
            self.operation_cancel_target_name = (
                result.get("blocker_name") or result.get("target_name")
                or self.operation_cancel_target_name)
            self._render_cancel_confirm()
            return
        self.cancel_mode = "pending"
        self.operation_cancel_request_id = result.get("request_id")
        self.operation_cancel_target_name = result.get("target_name")
        self.operation_cancel_target_mode = result.get("target_mode")
        # on_accept may block for seconds on the G-code mutex a running print
        # holds, so paint first. An interrupted wait ends immediately and may
        # close this page instead, so that path paints after the dispatch.
        interrupting_wait = self._temperature_wait_active()
        if not interrupting_wait:
            self._render_cancel_confirm()
        callback = self.operation_cancel_on_accept
        if callback is not None:
            callback(result)
        if interrupting_wait:
            self._run_immediate_command("M108")
            if (self.page == ScreenPage.CANCEL_CONFIRM
                    and self.cancel_mode == "pending"):
                self._render_cancel_confirm()

    def _accept_print_operation_cancel(self, result):
        self._filament_request_token = getattr(
            self, "_filament_request_token", 0) + 1
        self.pending_action = "print.cancel.confirm"
        self.pending_until = self.reactor.monotonic() + 30.0
        self.cancel_requested = True
        self.cancel_waiting_for_heat = self._temperature_wait_active()
        self.cancel_phase = result.get("target_name")
        started = bool(getattr(
            getattr(self, "start_print_macro", None), "variables", {}
        ).get("print_started", False))
        if started and not self.cancel_waiting_for_heat:
            try:
                self._run_script("_CONTEXT_CANCEL_POINT")
            except Exception:
                # Delivering the request aborts the print by raising. The print
                # state transition reports it, so this is not an action failure.
                logging.info("[feather_screen] print cancellation delivered")

    def _clear_print_operation_cancel(self, result):
        del result
        self.pending_action = None
        self.cancel_requested = False
        self.cancel_waiting_for_heat = False
        self.cancel_phase = None

    def _render_cancel_confirm(self):
        target = str(getattr(
            self, "operation_cancel_target_name", None) or "operation")
        interrupt = (getattr(
            self, "operation_cancel_target_mode", None) == "interruptible")
        if self.cancel_mode == "not_cancelable":
            commands = self.renderer.begin_page("CANNOT CANCEL SAFELY")
            commands.append(self.renderer.text(
                400, 145, "THIS OPERATION HAS NO SAFE CANCEL POINT",
                ThemeColor.WARNING, "JetBrainsMono Bold 12pt", "center",
                "middle", max_width=720, truncate=True))
            commands.append(self.renderer.text(
                400, 205, "ABORT STOPS THE PRINTER IMMEDIATELY (M112)",
                ThemeColor.DIM, "JetBrainsMono 8pt", "center", "middle"))
            commands += self.renderer.button(
                "operation.cancel.back", 100, 285, 260, 100,
                "CONTINUE", font="Roboto Bold 16pt")
            commands += self.renderer.button(
                "operation.cancel.force", 440, 285, 260, 100,
                "ABORT NOW", state="danger", font="Roboto Bold 16pt")
            self.renderer.send(commands)
            return
        if self.cancel_mode == "pending":
            label = self._cancel_progress_label()
            commands = self.renderer.begin_page(
                "%s %s" % (
                    "INTERRUPTING" if interrupt else "CANCELLING",
                    target.upper()))
            commands.append(self.renderer.text(
                400, 170, label, ThemeColor.WARNING,
                "JetBrainsMono Bold 16pt", "center", "middle",
                max_width=700, truncate=True))
            commands.append(self.renderer.text(
                400, 225,
                ("INTERRUPT REQUEST ACCEPTED" if interrupt
                 else "CANCEL REQUEST ACCEPTED"),
                ThemeColor.PRIMARY,
                "JetBrainsMono 12pt", "center", "middle"))
            commands += self.renderer.button(
                "operation.cancel.continue", 85, 285, 290, 62,
                "CONTINUE OPERATION", font="JetBrainsMono Bold 8pt")
            commands += self.renderer.button(
                "operation.cancel.force", 425, 285, 290, 62,
                "ABORT NOW (M112)", state="danger",
                font="JetBrainsMono Bold 8pt")
            loader_y = 385
            for index in range(5):
                commands.append(self.renderer.fill(
                    290 + index * 48, loader_y, 32, 12,
                    ThemeColor.PRIMARY if index == self.busy_phase % 5 else ThemeColor.MUTED))
            self.renderer.send(commands)
            self._last_cancel_label = label
            return
        commands = self.renderer.begin_page(
            "%s %s?" % (
                "Interrupt" if interrupt else "Cancel", target), back=False)
        commands.append(self.renderer.text(400, 170,
                                           "The operation will stop at a safe point",
                                           ThemeColor.WARNING, "Roboto 16pt", "center", "middle"))
        commands += self.renderer.button("operation.cancel.back", 100, 285, 260, 100,
                                         "GO BACK", font="Roboto Bold 16pt")
        commands += self.renderer.button("operation.cancel.confirm", 440, 285, 260, 100,
                                         "INTERRUPT" if interrupt else "CANCEL",
                                         state="danger",
                                         font="Roboto Bold 16pt")
        self.renderer.send(commands)

    def _cancel_progress_label(self):
        operation = self._operation_context_status()
        state = str(operation.get("current_state") or "").strip().upper()
        if (self._temperature_wait_active() or
                getattr(self, "cancel_waiting_for_heat", False)):
            if state:
                return "INTERRUPTING %s..." % (state,)
            path = operation.get("context_path") or ()
            if path:
                return "INTERRUPTING %s..." % str(path[-1]).strip().upper()
            return "INTERRUPTING TEMPERATURE WAIT..."
        if state:
            return "WILL STOP AFTER %s" % (state,)
        return "WILL STOP AT THE NEXT STEP"

    def _update_cancel_progress(self):
        if (self.page != ScreenPage.CANCEL_CONFIRM
                or self.cancel_mode != "pending"):
            return
        label = self._cancel_progress_label()
        self.busy_phase = (self.busy_phase + 1) % 5
        if label == self._last_cancel_label:
            commands = []
        else:
            self._last_cancel_label = label
            commands = [self.renderer.fill(100, 140, 600, 65, ThemeColor.BACKGROUND),
                        self.renderer.text(400, 170, label, ThemeColor.WARNING,
                                           "JetBrainsMono Bold 16pt", "center",
                                           "middle", max_width=700,
                                           truncate=True)]
        loader_y = 385
        for index in range(5):
            commands.append(self.renderer.fill(
                290 + index * 48, loader_y, 32, 12,
                ThemeColor.PRIMARY if index == self.busy_phase % 5 else ThemeColor.MUTED))
        self.renderer.send(commands)


    def _render_settings(self):
        brightness = int(self._setting("backlight", 50))
        sound = bool(self._setting("sound", 1))
        light_mode = str(self._setting("chamber_light_mode", "AT_BOOT"))
        light_subtitle = {
            "MANUAL": "APPLIES IMMEDIATELY",
            "AT_BOOT": "APPLIES AT BOOT",
            "PRINT_ONLY": "APPLIES DURING PRINTING",
        }.get(light_mode, "APPLIES AT BOOT")
        light_available = getattr(self, "chamber_light", None) is not None
        light = (self._chamber_light_brightness()
                 if light_available else None)
        theme = str(getattr(self.renderer, "theme_name", "DEFAULT"))
        commands = self.renderer.begin_page("Settings", back=True)
        # Hidden diagnostic entry: five title taps within two seconds. The
        # benchmark feature itself remains unloaded until the fifth tap opens
        # its page. Keep the hitbox clear of BACK and the emergency action.
        commands.append(self.renderer.action_hitbox(
            "settings.benchmark.tap", 170, 7, 450, 46))
        rows = (
            ("SCREEN BRIGHTNESS", None, brightness,
             "settings.brightness", 67, True),
            ("CHAMBER LIGHT", light_subtitle, light,
             "settings.led", 151, light_available),
        )
        for label, subtitle, value, prefix, y, enabled in rows:
            commands += [
                self.renderer.fill(25, y, 750, 70, ThemeColor.PANEL),
                self.renderer.stroke(25, y, 750, 70, ThemeColor.BORDER, 1),
                self.renderer.text(44, y + (18 if subtitle else 35), label,
                                   ThemeColor.PRIMARY,
                                   "JetBrainsMono Bold 8pt"),
                self.renderer.text(425, y + 36,
                                   "%d%%" % value if enabled else "--",
                                   ThemeColor.TEXT if enabled else ThemeColor.MUTED,
                                   "JetBrainsMono 12pt", "center"),
            ]
            if subtitle:
                commands.append(self.renderer.text(
                    44, y + 46, subtitle, ThemeColor.DIM,
                    "JetBrainsMono 8pt"))
            commands += self.renderer.button(prefix + ".minus", 525, y + 12,
                                             105, 46, "-5",
                                             state=("enabled" if enabled
                                                    else "disabled"))
            commands += self.renderer.button(prefix + ".plus", 650, y + 12,
                                             105, 46, "+5",
                                             state=("enabled" if enabled
                                                    else "disabled"))
        commands += [
            self.renderer.fill(25, 235, 750, 66, ThemeColor.PANEL),
            self.renderer.stroke(25, 235, 750, 66, ThemeColor.BORDER, 1),
            self.renderer.text(44, 268, "SOUND FEEDBACK", ThemeColor.PRIMARY,
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
        if action == "settings.benchmark.tap":
            self._handle_benchmark_tap()
            return
        if action == "settings.theme":
            parameters = self._mod_parameters()
            for index, param in enumerate(parameters):
                if param.key == "feather_theme":
                    self._open_mod_parameter(index, ScreenPage.SETTINGS)
                    return
            raise RuntimeError("Feather theme parameter is unavailable")
        if action == "settings.mod":
            self.mod_page = 0
            self.mod_parameter = None
            self._show_page(ScreenPage.MOD_SETTINGS)
            return
        if action.startswith("settings.led."):
            if getattr(self, "chamber_light", None) is None:
                raise RuntimeError("Chamber light is unavailable")
            delta = -5 if action.endswith("minus") else 5
            value = max(
                0, min(100, self._chamber_light_brightness() + delta))
            self._run_script(
                "SET_MOD PARAM=chamber_light VALUE=%d" % value)
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

    def _render_mod_settings(self, anchor_key=None):
        self._require_idle()
        parameters = self._mod_parameters()
        pages = mod_ui.category_pages(self.params, parameters)
        if anchor_key is None:
            anchor_key = getattr(self, "mod_restore_anchor_key", None)
        self.mod_restore_anchor_key = None
        if anchor_key is not None:
            anchored = mod_ui.page_of_parameter(pages, anchor_key)
            if anchored is not None:
                self.mod_page = anchored
        self.mod_page = max(
            0, min(getattr(self, "mod_page", 0), len(pages) - 1))
        sections = pages[self.mod_page]
        self.mod_action_keys = dict(
            (index, param.key)
            for index, param in mod_ui.page_parameters(sections))

        commands = self.renderer.begin_page("Mod settings", back=True)
        y = mod_ui.LIST_TOP
        for position, section in enumerate(sections):
            pitch = mod_ui.band_pitch(position)
            commands += self._mod_category_band(section, y, pitch)
            y += pitch
            for index, param in section.items:
                commands += self._mod_parameter_row(param, index, y)
                y += mod_ui.ITEM_PITCH
        upcoming = mod_ui.next_category_hint(pages, self.mod_page)
        if upcoming is not None:
            commands += self._mod_next_category_card(upcoming, y)
        commands += self._mod_scroll_rail(self.mod_page, len(pages))
        self.renderer.send(commands)

    def _mod_category_band(self, section, y, pitch):
        """Draw the in-list heading that owns the parameter rows below it."""
        label = section.label + (" (CONT.)" if section.continued else "")
        counter = "%02d-%02d / %02d" % (
            section.first, section.first + len(section.items) - 1,
            section.total)
        label_font, counter_font = "JetBrainsMono Bold 8pt", "JetBrainsMono 8pt"
        # The band sits at the bottom of its slot, so the padding of a later
        # band opens a gap above it and separates it from the rows before.
        top = y + pitch - mod_ui.BAND_HEIGHT - mod_ui.BAND_GAP_BELOW
        middle = top + mod_ui.BAND_HEIGHT // 2
        right = mod_ui.LIST_X + mod_ui.LIST_WIDTH
        label_x = mod_ui.LIST_X + 14
        label_limit = (right - self.renderer.text_width(counter, counter_font)
                       - 20 - label_x)
        rule_x = label_x + min(
            label_limit, self.renderer.text_width(label, label_font)) + 10
        return [
            self.renderer.fill(mod_ui.LIST_X, top, 4, mod_ui.BAND_HEIGHT,
                               ThemeColor.PRIMARY),
            self.renderer.text(label_x, middle, label, ThemeColor.PRIMARY,
                               label_font, "left", "middle",
                               max_width=label_limit, truncate=True),
            self.renderer.fill(rule_x, middle,
                               max(0, label_x + label_limit - rule_x), 1,
                               ThemeColor.BORDER),
            self.renderer.text(right, middle, counter, ThemeColor.DIM,
                               counter_font, "right", "middle"),
        ]

    def _mod_parameter_row(self, param, index, y):
        """Draw one parameter row together with its editing control."""
        action = "mod.item.%d" % index
        commands = [
            self.renderer.fill(mod_ui.LIST_X, y, mod_ui.LIST_WIDTH,
                               mod_ui.ITEM_HEIGHT, ThemeColor.PANEL),
            self.renderer.stroke(mod_ui.LIST_X, y, mod_ui.LIST_WIDTH,
                                 mod_ui.ITEM_HEIGHT, ThemeColor.BORDER, 1),
            self.renderer.text(40, y + 14, str(param.label).upper(),
                               ThemeColor.PRIMARY, "JetBrainsMono Bold 8pt",
                               max_width=430, truncate=True),
            self.renderer.text(40, y + 32, param.key, ThemeColor.DIM,
                               "JetBrainsMono 8pt"),
            self.renderer.text(40, y + 50, mod_ui.description(param),
                               ThemeColor.TEXT, "JetBrainsMono 8pt",
                               max_width=430, truncate=True),
        ]
        state = "disabled" if getattr(param, "readonly", False) else "enabled"
        if mod_ui.parameter_kind(param) == "bool":
            raw_value = self.params.variables.get(param.key, param.default)
            commands += self.renderer.toggle(
                action, 624, y + 13, 76, 38,
                mod_ui.bool_display_active(param, raw_value),
                enabled=state == "enabled")
        else:
            commands += self.renderer.button(
                action, 520, y + 9, 180, 46,
                mod_ui.display_value(self.params, param) + " >",
                state=state, font="JetBrainsMono 8pt")
        return commands

    def _mod_next_category_card(self, label, y):
        """Turn the space a postponed category left into the way to reach it.

        The card wears the frame of an ordinary list block so it does not pull
        attention away from the parameters, and carries the accent text of a
        category heading to show that it leads to that category.
        """
        middle = y + mod_ui.ITEM_HEIGHT // 2
        return [
            self.renderer.fill(mod_ui.LIST_X, y, mod_ui.LIST_WIDTH,
                               mod_ui.ITEM_HEIGHT, ThemeColor.PANEL),
            self.renderer.stroke(mod_ui.LIST_X, y, mod_ui.LIST_WIDTH,
                                 mod_ui.ITEM_HEIGHT, ThemeColor.BORDER, 1),
            self.renderer.text(
                mod_ui.LIST_X + mod_ui.LIST_WIDTH // 2, middle,
                "NEXT: %s >" % label, ThemeColor.TEXT,
                "JetBrainsMono Bold 8pt", "center", "middle",
                max_width=mod_ui.LIST_WIDTH - 40, truncate=True),
            self.renderer.action_hitbox(
                "mod.more", mod_ui.LIST_X, y, mod_ui.LIST_WIDTH,
                mod_ui.ITEM_HEIGHT),
        ]

    def _mod_scroll_rail(self, page, page_count):
        """Draw the page arrows and the position thumb beside the list."""
        commands = self.renderer.arrow_button(
            "mod.prev", 728, mod_ui.LIST_TOP, 52, 48, "up",
            state="enabled" if page > 0 else "disabled")
        commands += self.renderer.arrow_button(
            "mod.next", 728, 388, 52, 48, "down",
            state="enabled" if page + 1 < page_count else "disabled")
        track_y, track_height = 134, 244
        commands.append(self.renderer.stroke(749, track_y, 10, track_height,
                                             ThemeColor.BORDER, 1))
        thumb_height = max(18, track_height // page_count)
        thumb_y = (track_y if page_count == 1 else
                   track_y + (track_height - thumb_height) * page
                   // (page_count - 1))
        commands.append(self.renderer.fill(751, thumb_y + 2, 6,
                                           max(4, thumb_height - 4),
                                           ThemeColor.PRIMARY))
        return commands

    def _open_mod_parameter(self, index, return_page=ScreenPage.MOD_SETTINGS):
        parameters = self._mod_parameters()
        rendered_key = getattr(self, "mod_action_keys", {}).get(index)
        if rendered_key is not None:
            param = next(
                (candidate for candidate in parameters
                 if candidate.key == rendered_key), None)
            if param is None:
                raise RuntimeError("Parameter is no longer available")
        else:
            if index < 0 or index >= len(parameters):
                raise RuntimeError("Parameter is no longer available")
            param = parameters[index]
        if getattr(param, "readonly", False):
            raise RuntimeError("This parameter is read-only")
        # Anchor on the first row of the current page.  Editing a parameter can
        # reveal or hide dependent rows, so the page number alone would not
        # bring the user back to the same place.
        pages = mod_ui.category_pages(self.params, parameters)
        page = pages[max(0, min(getattr(self, "mod_page", 0), len(pages) - 1))]
        rows = mod_ui.page_parameters(page)
        self.mod_restore_anchor_key = rows[0][1].key if rows else None
        self.mod_return_page = return_page
        kind = mod_ui.parameter_kind(param)
        if kind == "bool":
            current = bool(self.params.variables.get(param.key, param.default))
            new_value = not current
            action = "mod.item.%d" % index
            scheduler = lambda callback, delay: self.reactor.register_callback(
                callback, self.reactor.monotonic() + delay)
            animate = getattr(self.renderer, "animate_toggle", None)
            if animate is not None:
                animate(action, mod_ui.bool_display_active(
                    param, new_value), scheduler)

            def complete():
                self._render_mod_settings()
                self._toast("UPDATED: %s" % param.label)

            self._set_mod_value(param, "1" if new_value else "0",
                                complete, minimum_duration=0.14)
            return
        self.mod_parameter = param
        self.mod_edit_value = mod_ui.current_edit_value(self.params, param)
        self.mod_edit_cursor = len(self.mod_edit_value)
        self.mod_keyboard_shift = False
        self.mod_keyboard_symbols = False
        if kind == "enum" or param.key == "feather_theme":
            if param.key == "feather_theme":
                # User files are writable at runtime. Refresh them exactly once
                # when entering the picker, then keep a stable option snapshot
                # for paging and selection. Bundled themes remain cached.
                self.renderer.reload_user_themes()
                options = self.renderer.theme_names()
                descriptions = dict(
                    (name, self.renderer.theme_description(name))
                    for name in options)
                disabled = self.renderer.user_theme_issues()
            else:
                options = mod_ui.enum_names(param)
                descriptions = dict(
                    (name, mod_ui.option_description(param, name))
                    for name in options)
                disabled = ()
            self._set_parameter_options(
                options, self.mod_edit_value, descriptions, disabled)
            self._show_page(ScreenPage.PARAMETER_OPTIONS)
        else:
            self.selected_parameter_option = None
            self._show_page(ScreenPage.MOD_VALUE)

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
        # "mod.more" is the card standing in for a postponed category; it leads
        # to the page that category starts on, which is the next one.
        if action in ("mod.next", "mod.more"):
            self.mod_page += 1
            self._render_mod_settings()
            return
        if action.startswith("mod.item."):
            self._open_mod_parameter(
                int(action.rsplit(".", 1)[1]), ScreenPage.MOD_SETTINGS)
            return
        if action == "mod.cancel":
            self.mod_parameter = None
            self._show_page(getattr(
                self, "mod_return_page", ScreenPage.MOD_SETTINGS))
            return
        param = self.mod_parameter
        if param is None:
            raise RuntimeError("No parameter selected")
        kind = mod_ui.parameter_kind(param)
        if action.startswith("mod.option."):
            entries = self.parameter_option_entries
            index = int(action.rsplit(".", 1)[1])
            if index < 0 or index >= len(entries):
                raise RuntimeError("Unknown option")
            option = entries[index]
            if not option.enabled:
                return
            self.selected_parameter_option = option.value
            self._render_parameter_options()
            return
        if action == "mod.options.prev":
            self.parameter_options_page_index = max(
                0, self.parameter_options_page_index - 1)
            self._render_parameter_options()
            return
        if action == "mod.options.next":
            self.parameter_options_page_index += 1
            self._render_parameter_options()
            return
        if action == "mod.apply":
            value = mod_ui.validate_value(param, self.selected_parameter_option)

            def complete():
                if param.key == "feather_theme":
                    self.renderer.set_theme(value)
                return_page = getattr(
                    self, "mod_return_page", ScreenPage.MOD_SETTINGS)
                self.mod_parameter = None
                self._show_page(return_page)
                self._toast("UPDATED: %s" % param.label)

            self._set_mod_value(param, value, complete)
            return
        if action == "mod.save":
            value = mod_ui.validate_value(param, self.mod_edit_value)

            def complete():
                return_page = getattr(
                    self, "mod_return_page", ScreenPage.MOD_SETTINGS)
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
        elif kind == "str" and is_keyboard_action(action):
            (self.mod_edit_value, self.mod_edit_cursor,
             self.mod_keyboard_shift,
             self.mod_keyboard_symbols) = TEXT_KEYBOARD.apply(
                self.mod_edit_value, self.mod_edit_cursor, action,
                self.mod_keyboard_shift, self.mod_keyboard_symbols,
                max_length=mod_ui.MAX_VALUE_LENGTH)
        self._render_mod_value()

    def _render_parameter_options(self):
        param = self.mod_parameter
        if param is None:
            self._show_page(ScreenPage.MOD_SETTINGS)
            return
        commands = self.renderer.begin_page(str(param.label), back=True)
        commands.append(self.renderer.text(
            25, 76, mod_ui.description(param), ThemeColor.TEXT,
            "JetBrainsMono 8pt", max_width=650, max_height=44, wrap=True,
            truncate=True))
        if mod_ui.restart_effect(param) is not None:
            commands.append(self.renderer.text(
                25, 108, "APPLYING THIS VALUE RESTARTS KLIPPER.", ThemeColor.WARNING,
                "JetBrainsMono 8pt"))
        options = self.parameter_options
        if (param.key == "feather_theme"
                and self.selected_parameter_option not in options):
            self.selected_parameter_option = "DEFAULT"
        entries = self.parameter_option_entries
        pagination = Pagination(
            entries, getattr(self, "parameter_options_page_index", 0), 4)
        self.parameter_options_page_index = pagination.page
        start = pagination.start
        for row, option in enumerate(pagination.visible):
            index = start + row
            selected = (option.enabled
                        and option.value == self.selected_parameter_option)
            detail = str(option.description or "").upper()
            label = str(option.label).upper()
            if detail:
                label += " // " + detail
            if selected:
                label += "  [SELECTED]"
            state = ("disabled" if not option.enabled else
                     "selected" if selected else "enabled")
            commands += self.renderer.button(
                "mod.option.%d" % index, 25, 120 + row * 66, 750, 58,
                label, state=state, font="JetBrainsMono 8pt")
        if pagination.page_count > 1:
            commands += self.renderer.button(
                "mod.options.prev", 25, 390, 120, 47, "<",
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
                "mod.options.next", 655, 390, 120, 47, ">",
                state=("enabled" if pagination.has_next else "disabled"),
                font="JetBrainsMono Bold 8pt")
            commands.append(self.renderer.text(
                750, 80, "%d/%d" % (
                    pagination.page + 1, pagination.page_count),
                ThemeColor.DIM, "JetBrainsMono 8pt", "right", "middle"))
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
            self._show_page(ScreenPage.MOD_SETTINGS)
            return
        kind = mod_ui.parameter_kind(param)
        commands = self.renderer.begin_page("Edit value", back=True)
        if kind in ("int", "float"):
            commands += self._render_mod_numeric_keys(param)
            self.renderer.send(commands)
            return
        commands += [
            self.renderer.text(25, 73, str(param.label).upper(), ThemeColor.PRIMARY,
                               "JetBrainsMono Bold 12pt"),
            self.renderer.text(25, 98, param.key, ThemeColor.DIM,
                               "JetBrainsMono 8pt"),
            self.renderer.text(280, 98, mod_ui.description(param), ThemeColor.TEXT,
                               "JetBrainsMono 8pt", max_width=490,
                               truncate=True),
            self.renderer.fill(25, 120, 750, 53, ThemeColor.PANEL),
            self.renderer.stroke(25, 120, 750, 53, ThemeColor.PRIMARY, 2),
        ]
        commands += TEXT_KEYBOARD.render_value(
            self.renderer, self.mod_edit_value, self.mod_edit_cursor,
            42, 147, 710, ThemeColor.PRIMARY)
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
        commands = TEXT_KEYBOARD.render(
            self.renderer, self.mod_keyboard_symbols,
            self.mod_keyboard_shift)
        commands += self.renderer.button(
            "mod.cancel", 25, 383, 360, 54, "CANCEL", state="danger",
            font="JetBrainsMono Bold 8pt")
        commands += self.renderer.button(
            "mod.save", 415, 383, 360, 54, "SAVE",
            font="JetBrainsMono Bold 8pt")
        return commands

    def _render_recovery_prompt(self):
        status = self.recovery_status or {}
        commands = self.renderer.begin_page("Power loss recovery")
        commands.append(self.renderer.text(
            400, 90, status.get("filename", "Unknown"), ThemeColor.BRIGHT,
            "Roboto Bold 14pt", "center", max_width=720, truncate=True))
        commands.append(self.renderer.text(400, 140, "Saved progress: %.1f%%" %
                                           (float(status.get("progress", 0.0)) * 100),
                                           ThemeColor.PRIMARY, "Roboto 12pt", "center"))
        commands.append(self.renderer.text(
            400, 185, "Nozzle %.0fC   Bed %.0fC   Mesh %s" %
            (status.get("extruder_target", 0), status.get("bed_target", 0),
             status.get("mesh", "?")), ThemeColor.BRIGHT, "Roboto 10pt", "center"))
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
            400, 150, text, ThemeColor.BRIGHT, "JetBrainsMono 12pt", "center",
            "middle", max_width=640, max_height=100, wrap=True,
            truncate=True))
        commands += self.renderer.button("recovery.confirm", 220, 300, 360, 100,
                                         "CLEANUP" if cleanup else "RESTORE",
                                         state="danger" if cleanup else "enabled",
                                         font="Roboto Bold 16pt")
        self.renderer.send(commands)

    def _render_action_prompt(self):
        if self._action_prompt_is_cold_pull():
            self._render_cold_pull_prompt()
            return
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
            30, 67, 740, 365, border=ThemeColor.BORDER, background=ThemeColor.PANEL)
        commands.append(self.renderer.text(
            400, 102, prompt["title"], ThemeColor.PRIMARY,
            "JetBrainsMono Bold 16pt", "center", "middle",
            max_width=680, truncate=True))
        text = "\n".join(prompt["text"])
        if text:
            commands.append(self.renderer.text(
                400, 158, text, ThemeColor.TEXT, "JetBrainsMono 8pt",
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
            self._show_page(ScreenPage.RECOVERY_CONFIRM)
        elif action == "recovery.confirm":
            command = "RESURRECT" if self.recovery_action == "restore" else "RESURRECT_ABORT"
            manager = getattr(self, "feature_manager", None)
            if manager is not None:
                manager.get("calibration").begin_recovery()
            else:
                self.calibration_kind = "recovery"
                self.calibration_starting_text = "STARTING..."
                self._reset_calibration_progress()
            self._show_page(ScreenPage.CALIBRATION_PROGRESS)
            self.reactor.register_callback(
                lambda eventtime, cmd=command: self._run_recovery(eventtime, cmd))

    def _run_recovery(self, eventtime, command):
        try:
            self._run_script(command)
        except Exception as exc:
            logging.exception("[feather_screen] recovery failed")
            self._show_message(str(exc), ScreenPage.RECOVERY_PROMPT)
            return
        status = (self.resurrection.get_status(self.reactor.monotonic())
                  if self.resurrection is not None else {})
        if command == "RESURRECT_ABORT" and status.get("available"):
            self._show_message("Recovery cleanup failed", ScreenPage.RECOVERY_PROMPT)
        elif command == "RESURRECT_ABORT":
            self._show_message("Recovery data cleaned up", ScreenPage.IDLE_HOME)
        elif status.get("state") != "printing":
            self._show_message("Restore did not start printing", ScreenPage.RECOVERY_PROMPT)
