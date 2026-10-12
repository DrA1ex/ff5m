## Experimental compact model preview process for Feather.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import hashlib
import os
import select
import struct
import subprocess
import tempfile
import time

from feather.previews import PreviewCancelled, decode_fxi1, _parse_argb


MODEL_EXECUTABLE = "/opt/config/mod/.bin/exec/model_preview"
MODEL_CACHE_DIR = "/data/.tmp/feather-model-preview"
MODEL_STYLES = ("SOLID", "STACKED", "POINTS", "MODEL_EDGES", "LAYER_LINES")


def model_settings(setting):
    style = str(setting("feather_preview_type", "LAYER_LINES")).upper()
    if style not in MODEL_STYLES:
        return None
    yaw = float(setting("feather_preview_yaw", 0))
    if not -180 <= yaw <= 180:
        yaw = (yaw + 180) % 360 - 180
    density_key = {"STACKED": "feather_preview_stacked_density", "POINTS": "feather_preview_points_density"}.get(style)
    return (
        MODEL_STYLES.index(style),
        int(setting("feather_preview_grid", "192")),
        yaw,
        float(setting("feather_preview_pitch", 30)),
        str(setting("feather_preview_fit", "TURN")).upper() == "VIEW",
        float(setting("feather_preview_rotation_speed", 24)),
        int(setting("feather_preview_fps", 30)),
        float(setting("feather_preview_scale", 1.0)),
        int(setting(density_key, 30 if style == "POINTS" else 100)) if density_key else 0,
    )


def model_frame_bands(blob):
    """Keep each image command below Typer's 8 KiB atomic transport limit."""
    image = decode_fxi1(blob)
    width, height = image["width"], image["height"]
    if image["bpp"] != 4 or len(image["palette"]) != 8:
        raise RuntimeError("Model preview returned an invalid palette")
    # Even row counts keep nibble alignment for odd viewport widths.
    rows = max(2, (7168 * 2 // width) // 2 * 2)
    palette = struct.pack("<8I", *image["palette"])
    bands = []
    for y in range(0, height, rows):
        count = min(rows, height - y)
        start = y * width // 2
        size = (count * width + 1) // 2
        payload = image["packed"][start:start + size]
        header = struct.pack("<4sBBBBHHII", b"FXI1", 1, 0, 8, 4, width, count, size, size)
        bands.append((y, header + palette + payload))
    return tuple(bands)


class ModelPreview:
    """One worker-owned process and model; cancellation may kill it from the reactor."""

    def __init__(self, path, settings, width, height, cancel,
                 executable=MODEL_EXECUTABLE, cache_dir=MODEL_CACHE_DIR, use_cache=True):
        self.path = path
        self.settings = settings
        self.width, self.height = width, height
        self.cancelled = cancel
        self.executable, self.cache_dir = executable, cache_dir
        self.use_cache = use_cache
        self.scratch = None
        self.retired_cpu = 0.0
        self.process = None
        self.layers = 0
        self.last_render_ms = 0.0

    def cancel(self):
        self.cancelled.set()
        process = self.process
        if process is not None and process.poll() is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass

    def close(self):
        self.cancel()
        self._close_process()
        if self.scratch is not None:
            self.scratch.cleanup()
            self.scratch = None

    def cpu_time(self):
        process, retired = self.process, self.retired_cpu
        if process is None:
            return retired
        try:
            with open("/proc/%d/stat" % process.pid) as stream:
                fields = stream.read().rsplit(")", 1)[1].split()
            current = (int(fields[11]) + int(fields[12])) / float(os.sysconf("SC_CLK_TCK"))
            return retired + current if retired is not None else None
        except (OSError, ValueError, IndexError):
            return None

    def _close_process(self):
        process = self.process
        if process is not None:
            # Preserve CPU samples across per-frame uncached processes.
            cpu = self.cpu_time()
            process.stdin.close()
            process.wait()
            process.stdout.close()
            process.stderr.close()
            self.process, self.retired_cpu = None, cpu

    def _read(self, size, deadline):
        result = bytearray()
        while len(result) < size:
            if self.cancelled.is_set():
                raise PreviewCancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("Model preview timed out")
            if not select.select([self.process.stdout], [], [], min(0.1, remaining))[0]:
                continue
            chunk = os.read(self.process.stdout.fileno(), size - len(result))
            if not chunk:
                if self.cancelled.is_set():
                    raise PreviewCancelled()
                detail = self.process.stderr.read(400).decode("utf-8", "replace").strip()
                raise RuntimeError(detail or "Model preview process stopped")
            result.extend(chunk)
        return bytes(result)

    def render(self, yaw, printed_layers, primary, pending, panel, scale=1.0, layer_fraction=None):
        try:
            if self.cancelled.is_set():
                raise PreviewCancelled()
            if self.process is None:
                os.makedirs(self.cache_dir, exist_ok=True)
                style, grid = self.settings[:2]
                density = self.settings[8] if len(self.settings) > 8 else 0
                if self.use_cache:
                    identity = "%s\0%d\0%d\0rev2" % (self.path, style, grid)
                    if density:
                        identity += "\0density4:%d" % density
                    name = hashlib.sha256(identity.encode("utf-8")).hexdigest() + ".gcp"
                    cache_path = os.path.join(self.cache_dir, name)
                else:
                    # The compact API requires a preparation file. A unique
                    # scratch path bypasses persistent caches without deleting them.
                    self.scratch = tempfile.TemporaryDirectory(prefix="uncached-", dir=self.cache_dir)
                    cache_path = os.path.join(self.scratch.name, "model.gcp")
                command = [
                    "nice", "-n", "10", self.executable, self.path,
                    cache_path, str(style), str(grid),
                    str(self.width), str(self.height),
                ]
                if density:
                    command.append(str(density))
                self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                stderr=subprocess.PIPE, bufsize=0)
                self.layers = struct.unpack("<I", self._read(4, time.monotonic() + 120))[0]
                if self.scratch is not None:
                    # The handshake confirms gcp_open loaded the complete model.
                    self.scratch.cleanup()
                    self.scratch = None
            if layer_fraction is not None:
                # The first request needs the physical count from the handshake.
                printed_layers = round(self.layers * layer_fraction)
            request = "%g %g %d %d %x %x %x %g\n" % (
                yaw, self.settings[3], min(self.layers, max(0, printed_layers)),
                int(self.settings[4]), _parse_argb(primary), _parse_argb(pending), _parse_argb(panel), scale)
            self.process.stdin.write(request.encode("ascii"))
            deadline = time.monotonic() + 5
            render_us, size = struct.unpack("<II", self._read(8, deadline))
            self.last_render_ms = render_us / 1000.0
            if size != 52 + (self.width * self.height + 1) // 2:
                raise RuntimeError("Model preview returned an invalid frame size")
            blob = self._read(size, deadline)
            image = decode_fxi1(blob)
            if (image["width"], image["height"]) != (self.width, self.height):
                raise RuntimeError("Model preview returned an invalid viewport")
            if not self.use_cache:
                self._close_process()
            return blob
        except BaseException:
            self.close()
            raise
