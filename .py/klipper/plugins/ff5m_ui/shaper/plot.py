# Small renderer-independent spectrum adapter for Feather drawing commands.
# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
# This file may be distributed under the terms of the GNU GPLv3 license.

from array import array
import math
import logging
import os
import re
import struct
import subprocess
import sys

_UNAVAILABLE = set()


def _native_strokes(renderer, bounds, series, x_max, y_max, line_width):
    """One finite native raster job; calibration and printer state are untouched."""
    if not series:
        return []
    explicit = os.environ.get("FORGE_SHAPER_RENDERER")
    path = explicit or "/opt/config/mod/.bin/exec/forge-shaper"
    if path in _UNAVAILABLE or (not explicit and sys.platform != "linux") or not os.access(path, os.X_OK):
        return None
    from ui import ThemeColor
    data = bytearray(struct.pack("<8s4H2I", b"FXPLOT1\n", bounds.width + 1, bounds.height + 1,
                                 line_width, len(series), int(renderer.color(ThemeColor.BACKGROUND), 16),
                                 int(renderer.color(ThemeColor.BORDER), 16)))
    for xs, ys, color in series:
        if len(xs) != len(ys):
            raise ValueError("Plot coordinate lengths differ")
        data.extend(struct.pack("<IH", int(renderer.color(color), 16), len(xs)))
        for x, y in zip(xs, ys):
            data.extend(struct.pack("<2d", min(1., max(0., x / x_max)) * bounds.width,
                                    (1. - min(1., max(0., y / y_max))) * bounds.height))
    try:
        result = subprocess.run([path, "render"], input=bytes(data), stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=2., check=True)
        output = result.stdout
        if len(output) > 64 * 1024 or len(output) < 12 or output[:8] != b"FXDRAW1\n":
            raise ValueError("Invalid native plot response")
        count, = struct.unpack_from("<I", output, 8)
        if count > 512:
            raise ValueError("Too many native plot bands")
        offset, commands = 12, []
        for _ in range(count):
            x, y, size = struct.unpack_from("<HHI", output, offset)
            offset += 8
            blob = output[offset:offset + size]
            if len(blob) != size or size < 28 or size > 7200 or blob[:4] != b"FXI1":
                raise ValueError("Invalid native plot image")
            w, h = struct.unpack_from("<HH", blob, 8)
            if not w or not h or x + w > bounds.width + 1 or y + h > bounds.height + 1:
                raise ValueError("Native plot image out of bounds")
            commands.append(renderer.image(bounds.x + x, bounds.y + y, blob))
            offset += size
        if offset != len(output):
            raise ValueError("Unexpected native plot output")
        return commands
    except (OSError, ValueError, struct.error, subprocess.SubprocessError):
        _UNAVAILABLE.add(path)
        logging.warning("[forge_shaper] Native graph renderer unavailable; using portable renderer")
        return None


_REPEATS = re.compile(rb"(.)\1{2,127}", re.DOTALL)


def _packbits(data):
    """Locate long zero/color runs in C, not one Python iteration per byte."""
    output = bytearray()
    position = 0
    for match in _REPEATS.finditer(data):
        end = match.start()
        for start in range(position, end, 128):
            count = min(128, end-start)
            output.append(count-1)
            output.extend(data[start:start+count])
        output.extend((257-(match.end()-end), data[end]))
        position = match.end()
    for start in range(position, len(data), 128):
        count = min(128, len(data)-start)
        output.append(count-1)
        output.extend(data[start:start+count])
    return bytes(output)


def _image_commands(renderer, bounds, pixels, width, painted):
    # Group final colors into opaque one-bit masks. Transparent background means
    # this works even when Typer alpha blending is disabled. PackBits keeps sparse
    # curves small; bands remain under the existing 8 KiB transport limit.
    groups = {}
    for index in sorted(painted):
        color = pixels[index]
        y, x = divmod(index, width)
        group = groups.get(color)
        if group is None:
            group = groups[color] = [x, y, x, y, array('I')]
        else:
            group[0], group[2], group[3] = min(group[0], x), max(group[2], x), y
        group[4].append(index)
    commands = []
    for color, (left, top, right, bottom, indices) in groups.items():
        band_width = right-left+1
        rows = max(1, 7168*8//band_width)
        cursor = 0
        for y in range(top, bottom+1, rows):
            count = min(rows, bottom+1-y)
            size = (band_width*count+7)//8
            packed = bytearray(size)
            first = cursor
            while cursor < len(indices) and indices[cursor] < (y+count)*width:
                py, px = divmod(indices[cursor], width)
                bit = (py-y)*band_width+px-left
                packed[bit//8] |= 0x80 >> (bit%8)
                cursor += 1
            if first == cursor:
                continue
            compressed = _packbits(packed)
            use_compression = len(compressed) < size
            payload = compressed if use_compression else bytes(packed)
            header = struct.pack('<4sBBBBHHII', b'FXI1', 1, int(use_compression), 2, 1,
                                 band_width, count, size, len(payload))
            palette = struct.pack('<II', 0, 0xff000000 | (color & 0xffffff))
            commands.append(renderer.image(bounds.x+left, bounds.y+y, header+palette+payload))
    return commands


def _stroke_commands(renderer, bounds, series, x_max, y_max, line_width, coverage_steps=8):
    """Rasterize round joins once, with constant width normal to each segment.

    Opaque FXI1 masks work on the old command renderer and the native engine.
    Packed raster buffers use five bytes per plot pixel; grouped pixel indices
    use at most four more. Coverage is unioned before compositing, so shared
    endpoints never darken twice.
    """
    from ui import ThemeColor

    width, height = bounds.width + 1, bounds.height + 1
    pixels = array("I", [0]) * (width * height)
    background = int(renderer.color(ThemeColor.BACKGROUND), 16)
    border = int(renderer.color(ThemeColor.BORDER), 16)
    grid_x = {int(f * bounds.width) for f in (0., .25, .5, .75, 1.)}
    grid_y = {bounds.height - int(f * bounds.height) for f in (0., .5, 1.)}
    painted = array("I")
    radius = line_width * .5
    reach = radius + .5
    for xs, ys, color in series:
        if len(xs) != len(ys):
            raise ValueError("Plot coordinate lengths differ")
        coverage = bytearray(width * height)
        covered = array("I")
        previous = None
        for x, y in zip(xs, ys):
            point = (min(1., max(0., x / x_max)) * bounds.width,
                     (1. - min(1., max(0., y / y_max))) * bounds.height)
            if previous is not None:
                ax, ay = previous
                bx, by = point
                dx, dy = bx - ax, by - ay
                length2 = dx * dx + dy * dy
                for py in range(max(0, math.floor(min(ay, by) - reach)),
                                min(height, math.ceil(max(ay, by) + reach) + 1)):
                    row = py * width
                    for px in range(max(0, math.floor(min(ax, bx) - reach)),
                                    min(width, math.ceil(max(ax, bx) + reach) + 1)):
                        t = ((px-ax)*dx + (py-ay)*dy) / length2 if length2 else 0.
                        # Explicit clamps; min/max calls are slower in this per-pixel loop.
                        if t < 0.:
                            t = 0.
                        elif t > 1.:
                            t = 1.
                        distance2 = (px-ax-t*dx)**2 + (py-ay-t*dy)**2
                        if distance2 >= reach * reach:
                            continue
                        value = int(min(1., reach - math.sqrt(distance2)) * coverage_steps + .5)
                        index = row + px
                        if value > coverage[index]:
                            if not coverage[index]:
                                covered.append(index)
                            coverage[index] = value
            previous = point
        foreground = int(renderer.color(color), 16)
        for index in covered:
            alpha = coverage[index]
            old = pixels[index]
            if not old:
                painted.append(index)
                y, x = divmod(index, width)
                old = border if x in grid_x or y in grid_y else background
            value = 0x1000000  # Distinguish painted black from an untouched pixel.
            for shift in (0, 8, 16):
                channel = (((old >> shift) & 255) * (coverage_steps-alpha)
                           + ((foreground >> shift) & 255) * alpha + coverage_steps//2) // coverage_steps
                value |= channel << shift
            pixels[index] = value
        del coverage
    return _image_commands(renderer, bounds, pixels, width, painted)


def plot_commands(renderer, bounds, series, x_max=200., y_max=1., line_width=1):
    """Translate (x values, y values, color) series to ordinary drawing commands.

    Numerical results, method selection and component lifecycle stay with callers.
    Both current and native-tree Feather renderers consume this same protocol.
    """
    from ui import ThemeColor

    if x_max <= 0 or y_max <= 0 or line_width <= 0:
        raise ValueError("Plot scales must be positive")
    commands = [renderer.fill(bounds.x - 1, bounds.y - 1, bounds.width + 2,
                              bounds.height + 2, ThemeColor.BACKGROUND)]

    def line(first, second, color, width=1):
        return "--batch line --start %d %d --end %d %d --color %s --line-width %d" % (
            *first, *second, renderer.color(color), width)

    for fraction in (0., .25, .5, .75, 1.):
        x = bounds.x + int(fraction * bounds.width)
        commands.append(line((x, bounds.y), (x, bounds.bottom), ThemeColor.BORDER))
    for fraction in (0., .5, 1.):
        y = bounds.bottom - int(fraction * bounds.height)
        commands.append(line((bounds.x, y), (bounds.right, y), ThemeColor.BORDER))
    strokes = _native_strokes(renderer, bounds, series, x_max, y_max, line_width)
    if strokes is not None:
        commands.extend(strokes)
        return tuple(commands)
    # Leave 16 KiB of the release queue's 64 KiB budget for controls and text.
    # Extremely jagged spectra can compress poorly: reduce edge color levels,
    # retaining the same geometry and width, rather than dropping the whole page.
    for steps in (8, 4, 2, 1):
        strokes = _stroke_commands(renderer, bounds, series, x_max, y_max, line_width, steps)
        if sum(len(c) + len(c.payload) + 64 for c in strokes) <= 48*1024:
            commands.extend(strokes)
            break
    else:
        raise ValueError("Spectrum raster exceeds transport budget")
    return tuple(commands)
