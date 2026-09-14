## Small immutable FXI1 resources with the active calibration-page palette.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from functools import lru_cache
import os
import struct

from ui import ThemeColor


@lru_cache(maxsize=3)
def _load(name):
    path = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                        "assets", "calibration", name + ".fxi1")
    with open(path, "rb") as stream:
        blob = stream.read(1025)
    if len(blob) < 20 or len(blob) > 1024:
        raise ValueError("Invalid calibration icon: %s" % name)
    magic, version, compression, colors, bpp, width, height, raw, size = struct.unpack_from(
        "<4sBBBBHHII", blob)
    if (magic != b"FXI1" or version != 1 or compression != 0
            or colors not in (2, 4) or bpp != (1 if colors == 2 else 2)
            or not 0 < width <= 64 or not 0 < height <= 64
            or raw != (width * height * bpp + 7) // 8
            or size != raw or len(blob) != 20 + colors * 4 + size):
        raise ValueError("Invalid calibration icon: %s" % name)
    return blob


def _colored(renderer, name, colors):
    blob = _load(name)
    palette = (0,) + tuple(0xff000000 | int(renderer.color(color), 16) for color in colors)
    if len(palette) != blob[6]:
        raise ValueError("Calibration icon palette mismatch: %s" % name)
    return blob[:20] + struct.pack("<%dI" % len(palette), *palette) + blob[20 + 4 * len(palette):]


def nut(renderer, center_x, center_y):
    blob = _colored(renderer, "nut", (ThemeColor.BRIGHT, ThemeColor.DIM, ThemeColor.PANEL))
    return renderer.image(center_x - 12, center_y - 10, blob)


def turn_arrow(renderer, center_x, center_y, direction, color):
    name = {"CW": "cw", "CCW": "ccw"}[direction]
    return renderer.image(center_x - 27, center_y - 22, _colored(renderer, name, (color,)))
