## Active-print page and preview lifecycle for Feather.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import logging
import math
import os
import struct
import subprocess
import time

from ui import ThemeColor
from ff5m_ui.screen import ScreenPage
from ff5m_ui.print_state import PrintState


GCODE_PREVIEW_PANEL = (536, 74, 240, 263)
GCODE_PREVIEW_BOX = (544, 112, 224, 217)
GCODE_PREVIEW_IMAGE_PADDING = 20
GCODE_PREVIEW_LOADER_PERIOD = 0.08
GCODE_PREVIEW_LOADER_RADII = (7, 10, 13, 10)
GCODE_PREVIEW_LOADER_DIAMETER = 72
PREVIEW_EXECUTABLE = "/opt/config/mod/.bin/exec/preview"
PREVIEW_TIMEOUT = 15.0


def _gcode_preview_image_rect():
    box_x, box_y, box_width, box_height = GCODE_PREVIEW_BOX
    padding = max(0, GCODE_PREVIEW_IMAGE_PADDING)
    width = max(1, box_width - 2 * padding)
    height = max(1, box_height - 2 * padding)
    return box_x + padding, box_y + padding, width, height


def _decode_packbits(payload, expected_size):
    output = bytearray()
    index = 0

    while index < len(payload):
        header = struct.unpack_from("<b", payload, index)[0]
        index += 1

        if header >= 0:
            count = header + 1
            end = index + count

            if end > len(payload):
                raise RuntimeError("preview helper returned truncated PackBits data")

            output.extend(payload[index:end])
            index = end
        elif header != -128:
            count = 1 - header

            if index >= len(payload):
                raise RuntimeError("preview helper returned truncated PackBits repeat")

            output.extend(payload[index:index + 1] * count)
            index += 1

        if len(output) > expected_size:
            raise RuntimeError("preview helper returned oversized PackBits output")

    if len(output) != expected_size:
        raise RuntimeError("preview helper returned invalid PackBits output size")

    return bytes(output)


def _decode_fxi1(blob):
    if len(blob) < 20 or blob[:4] != b"FXI1":
        raise RuntimeError("preview helper returned invalid FXI1 header")

    version, compression, palette_size, bpp = struct.unpack_from("<BBBB", blob, 4)
    width, height, unpacked_size, payload_size = struct.unpack_from("<HHII", blob, 8)

    if version != 1:
        raise RuntimeError("preview helper returned unsupported FXI1 version")

    if palette_size < 1 or palette_size > 8:
        raise RuntimeError("preview helper returned invalid FXI1 palette size")

    expected_bpp = 1 if palette_size <= 2 else 2 if palette_size <= 4 else 4

    if bpp != expected_bpp:
        raise RuntimeError("preview helper returned invalid FXI1 bits-per-pixel")

    expected_unpacked_size = (width * height * bpp + 7) // 8

    if unpacked_size != expected_unpacked_size:
        raise RuntimeError("preview helper returned invalid FXI1 unpacked size")

    palette_offset = 20
    palette_end = palette_offset + palette_size * 4
    payload_end = palette_end + payload_size

    if len(blob) != payload_end:
        raise RuntimeError("preview helper returned truncated FXI1 payload")

    palette = struct.unpack_from("<%dI" % palette_size, blob, palette_offset)
    payload = blob[palette_end:payload_end]

    if compression == 0:
        packed = payload
    elif compression == 1:
        packed = _decode_packbits(payload, unpacked_size)
    else:
        raise RuntimeError("preview helper returned unknown FXI1 compression")

    if len(packed) != unpacked_size:
        raise RuntimeError("preview helper returned malformed FXI1 payload")

    return {
        "blob": blob,
        "width": width,
        "height": height,
        "palette": palette,
        "packed": packed,
        "bpp": bpp,
    }


def _encode_packbits(payload):
    output = bytearray()
    index = 0

    while index < len(payload):
        repeat = 1
        while (index + repeat < len(payload) and repeat < 128
               and payload[index + repeat] == payload[index]):
            repeat += 1

        if repeat >= 3:
            output.extend((257 - repeat, payload[index]))
            index += repeat
            continue

        literal_start = index
        literal_count = 0
        while index < len(payload) and literal_count < 128:
            repeat = 1
            while (index + repeat < len(payload) and repeat < 128
                   and payload[index + repeat] == payload[index]):
                repeat += 1
            if repeat >= 3:
                break
            take = min(repeat, 128 - literal_count)
            index += take
            literal_count += take

        output.append(literal_count - 1)
        output.extend(payload[literal_start:index])

    return bytes(output)


def _parse_argb(value):
    value = str(value).strip().lstrip("#")
    if not value or len(value) > 8:
        raise ValueError("invalid preview color: %s" % value)
    color = int(value, 16)
    return color if len(value) > 6 else 0xff000000 | color


def _mask_y_bounds(image):
    width = image["width"]
    packed = image["packed"]
    first = None
    last = None

    for y in range(image["height"]):
        row_start = y * width
        present = False
        for x in range(width):
            bit = row_start + x
            if packed[bit // 8] & (1 << (7 - bit % 8)):
                present = True
                break
        if present:
            if first is None:
                first = y
            last = y

    return None if first is None else (first, last)


def _fxi1_mask_blob(image, color, first_row=0):
    if image["bpp"] != 1:
        raise RuntimeError("preview helper returned a non-mask FXI1 image")

    width = image["width"]
    height = image["height"]
    packed = bytearray(image["packed"])
    first_bit = max(0, min(height, int(first_row))) * width
    full_bytes, remaining_bits = divmod(first_bit, 8)

    if full_bytes:
        packed[:full_bytes] = b"\x00" * full_bytes
    if remaining_bits and full_bytes < len(packed):
        packed[full_bytes] &= (1 << (8 - remaining_bits)) - 1

    compressed = _encode_packbits(packed)
    use_compression = len(compressed) < len(packed)
    payload = compressed if use_compression else bytes(packed)
    palette = struct.pack("<II", 0, _parse_argb(color))
    header = struct.pack(
        "<4sBBBBHHII", b"FXI1", 1, int(use_compression), 2, 1,
        width, height, len(packed), len(payload))
    return header + palette + payload


def _layer_progress(stats):
    info = stats.get("info", {})
    current = info.get("current_layer")
    total = info.get("total_layer")
    try:
        current_value = float(current)
        total_value = float(total)
    except (TypeError, ValueError):
        return None
    if total_value <= 0:
        return None
    ratio = max(0.0, min(1.0, current_value / total_value))
    return current, total, ratio


def _colorize_gcode_preview(image, layer_state, pending, printed):
    cache = image.setdefault("full_mask_blobs", {})

    def full_mask(color):
        blob = cache.get(color)
        if blob is None:
            blob = _fxi1_mask_blob(image, color)
            cache[color] = blob
        return blob

    bounds = image.get("bounds")
    if bounds is None or layer_state is None:
        return (full_mask(printed),)

    _current, _total, ratio = layer_state
    first_y, last_y = bounds
    row_count = last_y - first_y + 1
    printed_rows = min(row_count, max(0, int(math.ceil(row_count * ratio))))

    if printed_rows <= 0:
        return (full_mask(pending),)
    if printed_rows >= row_count:
        return (full_mask(printed),)

    cutoff = last_y + 1 - printed_rows
    return (
        full_mask(pending),
        _fxi1_mask_blob(image, printed, cutoff),
    )


def _render_gcode_preview(path):
    _box_x, _box_y, width, height = _gcode_preview_image_rect()
    args = [
        PREVIEW_EXECUTABLE,
        path,
        "--size", str(width), str(height),
    ]
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    try:
        stdout, stderr = process.communicate(timeout=PREVIEW_TIMEOUT)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise RuntimeError("preview helper timed out")

    if process.returncode == 2:
        return None

    if process.returncode != 0:
        detail = (stderr.decode("utf-8", "replace").strip() or "no error output")[:400]
        raise RuntimeError("preview helper failed (%d): %s" % (process.returncode, detail))

    image = _decode_fxi1(stdout)

    if image["width"] != width or image["height"] != height:
        raise RuntimeError("preview helper returned an unexpected FXI1 size")

    image["bounds"] = _mask_y_bounds(image)
    return image


class PrintingPagesMixin:
    def _render_print_page(self):
        paused = self.print_state == PrintState.PAUSED
        controls_ready = self._print_controls_ready()
        eventtime = self.reactor.monotonic()
        print_stats = getattr(self, "print_stats", None)
        stats = (print_stats.get_status(eventtime)
                 if print_stats is not None else None)
        commands = self.renderer.begin_page(
            "PAUSED" if paused else "PRINTING")
        commands += self.renderer.button(
            "nav.home", 14, 7, 146, 46, "HOME",
            font="JetBrainsMono Bold 8pt")
        filename = self.virtual_sdcard.file_path() or "Unknown"
        filename = os.path.basename(filename)
        commands.append(self.renderer.text(25, 78, filename,
                                           ThemeColor.PRIMARY, "JetBrainsMono Bold 12pt",
                                           "left", "middle", max_width=487,
                                           truncate=True))
        commands.append(self.renderer.text(
            25, 110, self._display_status_text(
                eventtime), ThemeColor.TEXT,
            "JetBrainsMono 8pt",
            "left", "middle", max_width=487, truncate=True))
        commands += [
            self.renderer.text(25, 142, "PROGRESS", ThemeColor.PRIMARY,
                               "JetBrainsMono 8pt", "left", "middle"),
            self.renderer.stroke(25, 162, 487, 34, ThemeColor.BORDER, 2),
            self.renderer.fill(25, 208, 487, 1, ThemeColor.BORDER),
            self.renderer.text(25, 226, "ELAPSED", ThemeColor.PRIMARY,
                               "JetBrainsMono 8pt", "left", "middle"),
            self.renderer.text(270, 226, "REMAINING", ThemeColor.PRIMARY,
                               "JetBrainsMono 8pt", "left", "middle"),
            self.renderer.fill(264, 216, 1, 48, ThemeColor.BORDER),
            self.renderer.fill(25, 273, 487, 1, ThemeColor.BORDER),
            self.renderer.text(25, 291, "LAYER", ThemeColor.PRIMARY,
                               "JetBrainsMono 8pt", "left", "middle"),
            self.renderer.text(270, 291, "HEIGHT", ThemeColor.PRIMARY,
                               "JetBrainsMono 8pt", "left", "middle"),
        ]
        button_y, button_width, button_gap = 355, 184, 8
        button_x = 20
        commands += self.renderer.button("print.resume" if paused else "print.pause",
                                         button_x, button_y, button_width, 72,
                                         "RESUME" if paused else "PAUSE",
                                         state=("disabled" if not controls_ready else
                                                "busy" if self.pending_action in
                                                ("print.pause", "print.resume")
                                                else "enabled"),
                                         font="JetBrainsMono Bold 8pt")
        button_x += button_width + button_gap
        commands += self.renderer.button("print.filament", button_x, button_y,
                                         button_width, 72, "FILAMENT",
                                         state=("enabled" if controls_ready
                                                else "disabled"),
                                         font="JetBrainsMono Bold 8pt")
        button_x += button_width + button_gap
        commands += self.renderer.button(
            "print.z", button_x, button_y, button_width, 72, "Z ADJUST",
            state="enabled" if self._live_z_adjust_allowed(
                self.reactor.monotonic())
            else "disabled", font="JetBrainsMono Bold 8pt")
        button_x += button_width + button_gap
        commands += self.renderer.button("print.cancel", button_x, button_y,
                                         button_width, 72, "CANCEL",
                                         state="danger",
                                         font="JetBrainsMono Bold 8pt")
        preview = self._prepare_gcode_preview()
        commands += self._gcode_preview_panel_commands(preview)
        if stats is None:
            progress_commands, progress, values = (
                self._current_print_progress_commands(eventtime))
        else:
            progress_commands, progress, values = (
                self._current_print_progress_commands(eventtime, stats))
        commands += progress_commands
        if preview is not None and preview["status"] == "ready":
            commands += self._gcode_preview_image_commands(
                preview, _layer_progress(stats) if stats is not None else None)
            self._stop_gcode_preview_loader()
        elif preview is not None and preview["status"] == "loading":
            self._start_gcode_preview_loader()
        else:
            self._stop_gcode_preview_loader()
        accepted = self.renderer.send(commands)
        if (accepted is not False and preview is not None
                and preview["status"] == "ready"):
            preview["painted_generation"] = self.renderer.generation
            preview["painted_render_key"] = preview.get("render_key")
        self._last_print_controls_ready = controls_ready
        self._last_progress = progress
        self._last_time = values

    def _prepare_gcode_preview(self):
        path = self.virtual_sdcard.file_path()
        key = self._gcode_preview_key_for(path)
        if key is None:
            self._stop_gcode_preview_loader()
            return None

        preview = getattr(self, "_gcode_preview", None)
        if preview is not None and preview["key"] == key:
            if preview.get("status") == "loading":
                self._start_gcode_preview_loader()
            return preview

        preview = {
            "key": key,
            "status": "loading",
            "image": None,
            "painted_generation": None,
            "painted_render_key": None,
            "render_key": None,
            "render_blobs": (),
            "loading_phase": 0,
        }
        self._gcode_preview = preview

        def task():
            return _render_gcode_preview(path)

        worker = getattr(self, "file_scan_worker", None)
        if worker is None:
            preview["status"] = "failed"
            self._stop_gcode_preview_loader()
            return preview

        submitted = worker.submit(
            task, lambda value, error: self._gcode_preview_ready(
                key, value, error))
        if submitted:
            self._start_gcode_preview_loader()
        else:
            preview["status"] = "failed"
            self._stop_gcode_preview_loader()
        return preview

    def _gcode_preview_panel_commands(self, preview):
        x, y, width, height = GCODE_PREVIEW_PANEL
        commands = self.renderer.panel(
            x, y, width, height, border=ThemeColor.BORDER,
            background=ThemeColor.PANEL, line_width=1)
        commands.append(self.renderer.text(
            x + 18, y + 21, "PREVIEW", ThemeColor.PRIMARY,
            "JetBrainsMono 8pt", "left", "middle"))

        if preview is None or preview["status"] == "failed":
            label = "NO PREVIEW"
            box_x, box_y, box_width, box_height = GCODE_PREVIEW_BOX
            commands.append(self.renderer.text(
                box_x + box_width // 2, box_y + box_height // 2,
                label, ThemeColor.DIM, "JetBrainsMono 8pt",
                "center", "middle"))
        elif preview["status"] == "loading":
            commands += self._gcode_preview_loader_frame_commands(
                preview.get("loading_phase", 0), clear_box=False)
        return commands

    def _gcode_preview_image_commands(self, preview, layer_state=None):
        image = preview.get("image")

        if image is None:
            return []

        pending = self.renderer.color(ThemeColor.SECONDARY)
        printed = self.renderer.color(ThemeColor.PRIMARY)
        layer_key = (None if layer_state is None
                     else (layer_state[0], layer_state[1]))
        render_key = (layer_key, pending, printed)

        if preview.get("render_key") != render_key:
            started = time.monotonic()
            preview["render_blobs"] = _colorize_gcode_preview(
                image, layer_state, pending, printed)
            preview["render_key"] = render_key
            elapsed_ms = (time.monotonic() - started) * 1000.0
            logging.info(
                "[feather_screen] gcode preview recolor layer=%s took %.3f ms",
                "%s/%s" % layer_key if layer_key is not None else "unknown",
                elapsed_ms)

        x, y, _width, _height = _gcode_preview_image_rect()
        return [
            self.renderer.image(x, y, blob, format="fxi1")
            for blob in preview.get("render_blobs", ())
        ]

    def _gcode_preview_key_for(self, path):
        if not path:
            return None
        try:
            stat = os.stat(path)
        except OSError:
            return None
        return (
            path, stat.st_mtime, stat.st_size,
            _gcode_preview_image_rect(),
        )

    def _gcode_preview_ready(self, key, value, error):
        preview = getattr(self, "_gcode_preview", None)
        if preview is None or preview["key"] != key:
            return

        self._stop_gcode_preview_loader()
        if error is not None or value is None:
            logging.info(
                "[feather_screen] gcode preview unavailable for %s", key[0])
            preview["status"] = "failed"
            preview["image"] = None
        else:
            preview["status"] = "ready"
            preview["image"] = value
        preview["painted_generation"] = None

        if (self._page_paint_allowed(ScreenPage.PRINTING, ScreenPage.PAUSED)
                and self._gcode_preview_key_for(
                    self.virtual_sdcard.file_path()) == key):
            # Publish the completed preview as part of one complete page
            # surface. A separate partial preview batch can flip onto the
            # alternate framebuffer page while it still contains pixels from
            # a previous dialog.
            self._render_print_page()

    def _gcode_preview_loader_frame_commands(self, phase=0, clear_box=True):
        box_x, box_y, box_width, box_height = GCODE_PREVIEW_BOX
        center_x = box_x + box_width // 2
        center_y = box_y + box_height // 2
        radius = GCODE_PREVIEW_LOADER_RADII[int(phase) % len(
            GCODE_PREVIEW_LOADER_RADII)]
        diameter = GCODE_PREVIEW_LOADER_DIAMETER
        dirty_x = center_x - diameter // 2
        dirty_y = center_y - diameter // 2
        commands = []
        if clear_box:
            commands.append(self.renderer.fill(
                box_x, box_y, box_width, box_height, ThemeColor.PANEL))
        else:
            commands.append(self.renderer.fill(
                dirty_x, dirty_y, diameter, diameter, ThemeColor.PANEL))
        commands += self.renderer.filled_circle(
            center_x, center_y, radius, ThemeColor.PRIMARY)
        return commands

    def _gcode_preview_loading_active(self):
        preview = getattr(self, "_gcode_preview", None)
        return bool(
            preview is not None
            and preview.get("status") == "loading"
            and self.page in (ScreenPage.PRINTING, ScreenPage.PAUSED)
            and self._gcode_preview_key_for(self.virtual_sdcard.file_path())
            == preview.get("key"))

    def _start_gcode_preview_loader(self):
        if not self._gcode_preview_loading_active():
            return
        timer = getattr(self, "_gcode_preview_loader_timer", None)
        if timer is not None:
            return
        self._gcode_preview_loader_phase = 0
        self._gcode_preview_loader_timer = self.reactor.register_timer(
            self._gcode_preview_loader_tick,
            self.reactor.monotonic() + GCODE_PREVIEW_LOADER_PERIOD)

    def _stop_gcode_preview_loader(self):
        timer = getattr(self, "_gcode_preview_loader_timer", None)
        if timer is None:
            return
        self.reactor.update_timer(timer, self.reactor.NEVER)
        self._gcode_preview_loader_timer = None

    def _gcode_preview_loader_tick(self, eventtime):
        if not self._gcode_preview_loading_active():
            self._gcode_preview_loader_timer = None
            return self.reactor.NEVER
        preview = getattr(self, "_gcode_preview", None)
        phase = (getattr(self, "_gcode_preview_loader_phase", 0) + 1) % len(
            GCODE_PREVIEW_LOADER_RADII)
        self._gcode_preview_loader_phase = phase
        if preview is not None:
            preview["loading_phase"] = phase
        commands = self._gcode_preview_loader_frame_commands(
            phase, clear_box=False)
        sender = getattr(self.renderer, "send_animation", None)
        if sender is not None:
            sender(commands, "gcode-preview-loader")
        else:
            self.renderer.send(commands, kind="animation",
                               key="gcode-preview-loader")
        return eventtime + GCODE_PREVIEW_LOADER_PERIOD

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
        commands, progress, values = self._current_print_progress_commands(
            eventtime, stats)
        if progress == self._last_progress and values == self._last_time:
            return

        preview = getattr(self, "_gcode_preview", None)
        preview_redraw = False
        if preview is not None and preview.get("status") == "ready":
            image_commands = self._gcode_preview_image_commands(
                preview, _layer_progress(stats))
            if preview.get("painted_render_key") != preview.get("render_key"):
                commands += image_commands
                preview_redraw = bool(image_commands)

        self._last_progress, self._last_time = progress, values
        accepted = self.renderer.send(commands)
        if accepted is not False and preview_redraw:
            preview["painted_render_key"] = preview.get("render_key")

    def _current_print_progress_commands(self, eventtime, stats=None):
        stats = stats or self.print_stats.get_status(eventtime)
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
        width = round(max(0, min(100, progress)) * 475 / 100)
        commands = [
            self.renderer.fill(430, 130, 74, 29),
            self.renderer.text(500, 142, "%d%%" % progress,
                               ThemeColor.PRIMARY, "JetBrainsMono 12pt", "right", "middle"),
            self.renderer.fill(31, 168, 475, 22),
            self.renderer.fill(31, 168, width, 22, ThemeColor.PRIMARY),
            self.renderer.fill(25, 238, 234, 28),
            self.renderer.text(25, 252, values[0], ThemeColor.TEXT,
                               "JetBrainsMono 12pt", "left", "middle"),
            self.renderer.fill(270, 238, 242, 28),
            self.renderer.text(270, 252, values[1], ThemeColor.TEXT,
                               "JetBrainsMono 12pt", "left", "middle"),
            self.renderer.fill(25, 303, 234, 30),
            self.renderer.text(25, 318, values[2], ThemeColor.TEXT,
                               "JetBrainsMono 12pt", "left", "middle"),
            self.renderer.fill(270, 303, 242, 30),
            self.renderer.text(270, 318, "%.2f MM" % values[3], ThemeColor.TEXT,
                               "JetBrainsMono 12pt", "left", "middle"),
        ]
        return commands, progress, values

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
            self.renderer.fill(20, 94, 496, 34),
            self.renderer.text(25, 110, status, ThemeColor.TEXT,
                               "JetBrainsMono 8pt", "left", "middle",
                               max_width=487, truncate=True)])

    def _update_operation_context(self, eventtime):
        operation = self._operation_context_status(eventtime)
        revision = operation["revision"]
        if revision == getattr(self, "_last_operation_revision", -1):
            return
        self._last_operation_revision = revision
        if self._page_paint_allowed(ScreenPage.PRINTING, ScreenPage.PAUSED):
            self._draw_print_status(
                self._display_status_text(status=operation))

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
