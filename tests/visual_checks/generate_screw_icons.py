## Generate the small indexed calibration icons; never run on the printer.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import math
from pathlib import Path
import struct


OUTPUT = (Path(__file__).resolve().parents[2] / ".py/klipper/plugins/"
          "ff5m_ui/assets/calibration")


def encode(width, height, pixels, palette_size):
    bpp = 1 if palette_size <= 2 else 2
    packed = bytearray((width * height * bpp + 7) // 8)
    for offset, value in enumerate(pixels):
        packed[offset * bpp // 8] |= value << (8 - bpp - offset * bpp % 8)
    return (struct.pack("<4sBBBBHHII", b"FXI1", 1, 0, palette_size, bpp,
                        width, height, len(packed), len(packed))
            + bytes(palette_size * 4) + packed)


def nut():
    canvas = Icon(25, 21)
    # Integer-symmetric silhouette and one-pixel seams at the native size.
    for y in range(21):
        half = 6 + min(y, 6, 20 - y)
        for x in range(12 - half, 13 + half):
            top_edge = 12 - max(0, abs(x - 12) - 6)
            edge = x in (12 - half, 12 + half) or y == 20
            value = 1 if y <= top_edge or edge or x in (6, 18) else (3 if x > 18 else 2)
            canvas.pixels[y * canvas.width + x] = value
    canvas.ellipse(12, 6, 7.5, 3.5, 3)
    return encode(canvas.width, canvas.height, canvas.pixels, 4)


class Icon:
    """Offline indexed drawing; no raster construction runs on the printer."""

    def __init__(self, width, height):
        self.width, self.height = width, height
        self.pixels = bytearray(width * height)

    def ellipse(self, cx, cy, rx, ry, value):
        for y in range(self.height):
            for x in range(self.width):
                if ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1:
                    self.pixels[y * self.width + x] = value


def arrows():
    width, height = 55, 32
    pixels = bytearray(width * height)

    def fill(x, y, w, h):
        for row in range(y + 22, y + 22 + h):
            start = row * width + x + 27
            pixels[start:start + w] = b"\x01" * w

    for y in range(-22, 1):
        outer = int(math.sqrt(22.5 * 22.5 - y * y))
        if -y > 18:
            fill(-outer, y, outer * 2 + 1, 1)
        else:
            inner = int(math.sqrt(18.5 * 18.5 - y * y))
            span = outer - inner + 1
            fill(-outer, y, span, 1)
            fill(inner, y, span, 1)
    for row in range(-1, 6):
        half = 5 - row
        fill(20 - half, row, half * 2 + 1, 1)
    reflected = bytearray()
    for row in range(height):
        reflected.extend(pixels[row * width:(row + 1) * width][::-1])
    return encode(width, height, pixels, 2), encode(width, height, reflected, 2)


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    clockwise, counterclockwise = arrows()
    for name, blob in (("nut", nut()), ("cw", clockwise), ("ccw", counterclockwise)):
        path = OUTPUT / (name + ".fxi1")
        path.write_bytes(blob)
        print("%s: %d bytes" % (path.name, len(blob)))


if __name__ == "__main__":
    main()
