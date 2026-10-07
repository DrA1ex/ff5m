## File-browser pages for Feather.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import logging
import os
import time
import threading

from ui import ThemeColor, ThemeRole
from ff5m_ui.screen import ScreenPage

from feather.files import FileEntry, FileTaskSuperseded, scan_gcode_files
from feather.previews import (
    PREVIEW_MASK_SIZE, PreviewCancelled, colorize_preview, load_preview,
)
from feather.screen.pagination import Pagination, pagination_footer


FILE_ROWS = 5
FILE_TILES = 3
FILE_PRELOAD_LIMIT = 15
FILE_CACHE_TTL = 5.0
FILE_TILE_X = (22, 280, 538)
FILE_TILE_Y = 68
FILE_TILE_WIDTH = 240
FILE_TILE_HEIGHT = 306
FILE_TILE_IMAGE_Y = 78


class FileBrowserPagesMixin:
    def _configured_file_view(self):
        view = str(self._setting("feather_file_view", "list")).strip().lower()
        return view if view in ("list", "tiles") else "list"

    def _file_page_size(self):
        return FILE_TILES if self.file_view == "tiles" else FILE_ROWS

    def _normalize_file_source(self):
        source = self.file_source
        usb_storage = self.usb_storage
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
        usb_storage = self.usb_storage
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
        """Load the current file source synchronously."""
        source = self._normalize_file_source()
        self.file_entries = self._build_file_scan_task(source)()

    def _invalidate_file_entries(self, source=None):
        cache = self.file_entry_cache
        loaded_at = self.file_entry_loaded_at
        if source is None:
            cache.clear()
            loaded_at.clear()
        else:
            cache.pop(source, None)
            loaded_at.pop(source, None)

    def _expire_file_entries_if_stale(self, source):
        cache = self.file_entry_cache
        if source not in cache:
            return True
        loaded_at = self.file_entry_loaded_at
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
        self._paint_file_loading(source)

    def _paint_file_loading(self, source):
        label = ("LOADING USB FILES..." if source == "usb"
                 else "LOADING PRINT FILES...")
        self.renderer.loader(label, self.file_scan_phase)

    def _start_file_scan(self, source):
        self._cancel_file_preview()
        self._cancel_gcode_preview()
        if (self.file_scan_loading and self.file_scan_source == source
                and self.file_scan_token > 0):
            return
        self.file_scan_token += 1
        token = self.file_scan_token
        self._render_file_loading(source)
        task = self._build_file_scan_task(source)
        submitted = self.file_worker.submit(
            task, lambda entries, error:
            self._finish_file_scan(token, source, entries, error))
        if not submitted:
            self._finish_file_scan(
                token, source, None, RuntimeError("File worker stopped"))

    def _finish_file_scan(self, token, source, entries, error):
        if token != self.file_scan_token:
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
        valid_preview_keys = {
            self._file_preview_key(entry) for entry in entries
            if not entry.directory
        }
        for cached_source, cached_entries in self.file_entry_cache.items():
            if cached_source == source:
                continue
            valid_preview_keys.update(
                self._file_preview_key(entry) for entry in cached_entries
                if not entry.directory)
        self.file_preview_failures.intersection_update(valid_preview_keys)
        self.file_entry_cache[source] = entries
        self.file_entry_loaded_at[source] = self.reactor.monotonic()
        if source == self.file_source:
            self.file_entries = entries
        if (self._page_paint_allowed(ScreenPage.FILE_BROWSER)
                and source == self.file_source):
            self._render_file_browser()
            if message is not None:
                self._toast(message)

    def _queue_print_history_write(self, task):
        worker = getattr(self, "file_worker", None)
        return worker is not None and worker.submit_write(task)

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
        source = self._normalize_file_source()
        if self.file_scan_loading and self.file_scan_source == source:
            self._paint_file_loading(source)
            return
        if source not in self.file_entry_cache:
            self._start_file_scan(source)
            return
        self.file_entries = self.file_entry_cache[source]
        self._render_file_entries()

    def _render_file_entries(self):
        self._cancel_file_preview()
        self._cancel_gcode_preview()
        self.file_preview_visible_attempted.clear()
        pagination = Pagination(
            self.file_entries, self.file_page, self._file_page_size())
        self.file_page = pagination.page
        usb_page = self.file_source == "usb"
        title = "USB files" if usb_page else "Print files"
        commands = self.renderer.begin_page(title, back=True)
        commands += self._file_view_commands()
        rows = pagination.visible
        if self.file_view == "tiles":
            commands += self._render_file_tiles(rows)
        else:
            for index, entry in enumerate(rows):
                y = 62 + index * 65
                commands += self.renderer.button(
                    "file.item%d" % index, 30, y, 740, 56,
                    (entry.name + "  >" if entry.directory else entry.name),
                    font="JetBrainsMono 12pt")
        commands += pagination_footer(
            self.renderer, pagination, "file.prev", "file.next")
        if not rows:
            commands.append(self.renderer.text(
                400, 230, "No G-code files", ThemeColor.DIM,
                "Roboto 16pt", "center", "middle"))
        self.renderer.send(commands)
        if self.file_view == "tiles":
            self._start_next_file_preview(rows)

    def _file_view_commands(self):
        view = self.file_view
        commands = self.renderer.button(
            "file.view.list", 548, 7, 70, 46, "LIST",
            state="selected" if view == "list" else "enabled",
            font="Roboto Bold 8pt")
        commands += self.renderer.button(
            "file.view.tiles", 622, 7, 74, 46, "GRID",
            state="selected" if view == "tiles" else "enabled",
            font="Roboto Bold 8pt")
        commands += self.renderer.button(
            "file.refresh", 700, 7, 86, 46, "SCAN",
            font="Roboto Bold 8pt")
        return commands

    def _file_preview_key(self, entry):
        width, height = PREVIEW_MASK_SIZE
        return (
            entry.path, entry.size, entry.mtime, width, height,
        )

    def _file_preview_candidates(self, visible):
        preload = []
        for entry in self.file_entries:
            if entry.directory:
                continue
            preload.append(entry)
            if len(preload) == FILE_PRELOAD_LIMIT:
                break
        signature = tuple(self._file_preview_key(entry) for entry in preload)
        if signature != self.file_preview_preload_signature:
            self.file_preview_preload_signature = signature
            self.file_preview_preload_attempted = set()
        return list(visible), preload

    def _cached_file_preview(self, entry):
        key = self._file_preview_key(entry)
        found, value = self.preview_cache.lookup(key)
        if found:
            if value is None:
                return True, None
            color = self.renderer.color(ThemeColor.PRIMARY)
            if value["color"] == color:
                return True, value["blob"]
        if key in self.file_preview_failures:
            return True, None
        if not found:
            return found, None

        if self.file_preview_request is None:
            self._submit_file_preview(
                entry, key, cached_image=value["image"])
        return False, None

    def _start_next_file_preview(self, visible):
        if self.file_preview_request is not None:
            return
        visible_entries, preload_entries = self._file_preview_candidates(visible)
        cache = self.preview_cache
        color = self.renderer.color(ThemeColor.PRIMARY)

        for entries, attempted in (
                (visible_entries, self.file_preview_visible_attempted),
                (preload_entries, self.file_preview_preload_attempted)):
            for entry in entries:
                if entry.directory:
                    continue
                key = self._file_preview_key(entry)
                if key in self.file_preview_failures or key in attempted:
                    continue
                found, value = cache.lookup(key)
                if found and (value is None or value["color"] == color):
                    continue
                attempted.add(key)
                if key in self.file_preview_preload_signature:
                    self.file_preview_preload_attempted.add(key)
                cached_image = value["image"] if found else None
                self._submit_file_preview(entry, key, cached_image=cached_image)
                return

    def _submit_file_preview(self, entry, key,
                             cached_image=None):
        width, height = PREVIEW_MASK_SIZE
        color = self.renderer.color(ThemeColor.PRIMARY)
        request = {"key": key, "cancel": threading.Event()}
        self.file_preview_request = request
        worker = self.file_worker

        def task():
            if request["cancel"].is_set():
                raise PreviewCancelled()
            if cached_image is None:
                image = load_preview(entry.path, width, height,
                                     cancel=request["cancel"])
            else:
                image = dict(cached_image)
                image["full_mask_blobs"] = {}
            if image is None:
                return None
            blob = colorize_preview(image, None, color, color)[0]
            return {"image": image, "color": color, "blob": blob}

        if worker is None or not worker.submit(
                task, lambda value, error:
                self._finish_file_preview(request, value, error)):
            self._finish_file_preview(
                request, None, RuntimeError("File preview worker stopped"))

    def _cancel_file_preview(self):
        request = getattr(self, "file_preview_request", None)
        if request is None:
            return
        request["cancel"].set()
        self.file_preview_request = None
        self.file_preview_preload_attempted.discard(request["key"])
        self.file_preview_visible_attempted.discard(request["key"])

    def _finish_file_preview(self, request, value, error):
        if self.file_preview_request is not request:
            return
        self.file_preview_request = None

        if isinstance(error, (FileTaskSuperseded, PreviewCancelled)):
            self.file_preview_preload_attempted.discard(request["key"])
            self.file_preview_visible_attempted.discard(request["key"])
        elif error is not None:
            logging.info(
                "[feather_screen] file preview unavailable for %s: %s",
                request["key"][0], error)
            self.file_preview_failures.add(request["key"])
        else:
            self.file_preview_failures.discard(request["key"])
            self.preview_cache.store(request["key"], value)
        if (self._page_paint_allowed(ScreenPage.FILE_BROWSER)
                and self.file_view == "tiles"
                and not self.file_scan_loading):
            pagination = Pagination(
                self.file_entries, self.file_page, self._file_page_size())
            for index, entry in enumerate(pagination.visible):
                if self._file_preview_key(entry) == request["key"]:
                    self.renderer.prioritize_next_batch(
                        "state", "file-preview:%s" % request["key"][0])
                    self.renderer.send(self._file_tile_surface(index, entry))
                    break
            self._start_next_file_preview(pagination.visible)

    def _render_file_tiles(self, entries):
        image_width, image_height = PREVIEW_MASK_SIZE
        commands = []
        for index, entry in enumerate(entries):
            commands += self._file_tile_surface(
                index, entry, image_width, image_height)
        return commands

    def _file_tile_surface(self, index, entry, image_width=None,
                           image_height=None):
        if image_width is None or image_height is None:
            image_width, image_height = PREVIEW_MASK_SIZE
        x = FILE_TILE_X[index]
        found, blob = ((True, None) if entry.directory else
                       self._cached_file_preview(entry))
        normal = self._file_tile_commands(
            entry, x, image_width, image_height, found, blob, False)
        pressed = self._file_tile_commands(
            entry, x, image_width, image_height, found, blob, True)
        return self.renderer.button_surface(
            "file.item%d" % index, x, FILE_TILE_Y,
            FILE_TILE_WIDTH, FILE_TILE_HEIGHT, normal, pressed)

    def _file_tile_commands(self, entry, x, image_width, image_height,
                            found, blob, pressed):
        background = (ThemeColor.PRESSED_BACKGROUND if pressed
                      else ThemeRole.BUTTON_BACKGROUND)
        border = (ThemeColor.BRIGHT if pressed
                  else ThemeRole.BUTTON_BORDER)
        commands = self.renderer.panel(
            x, FILE_TILE_Y, FILE_TILE_WIDTH, FILE_TILE_HEIGHT,
            border=border, background=background,
            line_width=3 if pressed else 2)
        image_x = x + (FILE_TILE_WIDTH - image_width) // 2
        if found and blob is not None:
            commands.append(self.renderer.image(
                image_x, FILE_TILE_IMAGE_Y, blob, format="fxi1"))
        elif not found:
            commands += self.renderer.panel(
                image_x, FILE_TILE_IMAGE_Y, image_width, image_height,
                border=ThemeColor.BORDER, background=ThemeColor.PANEL,
                line_width=1)
            commands.append(self.renderer.text(
                x + FILE_TILE_WIDTH // 2,
                FILE_TILE_IMAGE_Y + image_height // 2,
                "LOADING...", ThemeColor.DIM, "JetBrainsMono 8pt",
                "center", "middle"))
        elif entry.directory:
            commands += self._file_directory_tile(
                x, FILE_TILE_IMAGE_Y, image_height)
        else:
            commands.append(self.renderer.text(
                x + FILE_TILE_WIDTH // 2,
                FILE_TILE_IMAGE_Y + image_height // 2,
                "NO PREVIEW", ThemeColor.DIM, "JetBrainsMono 8pt",
                "center", "middle"))

        label = (entry.name if entry.directory else
                 os.path.splitext(entry.name)[0])
        commands.append(self.renderer.text(
            x + 14, 266, label, ThemeColor.BRIGHT,
            "Roboto Bold 12pt", "left", "top",
            max_width=FILE_TILE_WIDTH - 28, max_height=68,
            wrap=True, truncate=True))
        detail = ("OPEN" if entry.directory else
                  self._format_size(entry.size))
        commands.append(self.renderer.text(
            x + 14, 352, detail, ThemeColor.DIM,
            "JetBrainsMono 8pt", "left", "middle",
            max_width=FILE_TILE_WIDTH - 28, truncate=True))
        return commands

    def _file_directory_tile(self, x, y, height):
        folder_width = 112
        folder_height = 72
        folder_x = x + (FILE_TILE_WIDTH - folder_width) // 2
        folder_y = y + (height - folder_height) // 2 + 6
        return [
            self.renderer.fill(
                folder_x, folder_y, 48, 14, ThemeColor.PRIMARY),
            self.renderer.fill(
                folder_x, folder_y + 12, folder_width, folder_height - 12,
                ThemeColor.PANEL),
            self.renderer.stroke(
                folder_x, folder_y + 12, folder_width, folder_height - 12,
                ThemeColor.PRIMARY, 2),
        ]

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
            self._cancel_file_preview()
            self.file_preview_failures.clear()
            self.file_preview_preload_signature = ()
            self.file_preview_preload_attempted.clear()
            self.file_preview_visible_attempted.clear()
            self._invalidate_file_entries(self.file_source)
            self._render_file_browser()
        elif action in ("file.view.list", "file.view.tiles"):
            view = action.rsplit(".", 1)[-1]
            if view == self.file_view:
                return
            self.params.set_value("feather_file_view", view)
            self.file_view = view
            self.file_page = 0
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
                self.file_entries, self.file_page, self._file_page_size())
            offset = pagination.absolute_index(index)
            if offset is None:
                return
            entry = self.file_entries[offset]
            if entry.directory:
                self.file_source = "usb"
                self.file_page = 0
                self.selected_file = None
                self._cancel_file_preview()
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
            400, 96, entry.name, ThemeColor.BRIGHT, "Roboto Bold 16pt",
            "center", "middle", max_width=720, truncate=True))
        commands.append(self.renderer.text(
            400, 140, self._format_size(entry.size), ThemeColor.PRIMARY,
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
        path = os.path.realpath(self.selected_file.path)
        if not os.path.isfile(path) or not path.startswith(root + os.sep):
            raise RuntimeError("Selected file is no longer available")
        file_stat = os.stat(path)
        self.selected_file.size = file_stat.st_size
        self.selected_file.mtime = file_stat.st_mtime
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
        # Render before the virtual-SD timer can begin START_PRINT and homing.
        self._reconcile_print_state(self.reactor.monotonic())
