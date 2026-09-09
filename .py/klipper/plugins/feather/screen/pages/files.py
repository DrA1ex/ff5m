## File-browser pages for Feather.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import logging
import os
import time

from ui import ThemeColor
from ff5m_ui.screen import ScreenPage

from feather.files import FileEntry, scan_gcode_files
from feather.screen.pagination import Pagination, pagination_footer


FILE_ROWS = 5
FILE_CACHE_TTL = 5.0


class FileBrowserPagesMixin:
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
