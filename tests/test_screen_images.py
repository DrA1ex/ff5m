## Tests for generated screen images.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import lzma
import pathlib
import unittest


ROOT = pathlib.Path(__file__).parents[1]
FRAME_BYTES = 800 * 480 * 4
ROW_BYTES = 800 * 4
BGRA_OPAQUE_BLACK = bytes((0x00, 0x00, 0x00, 0xFF))


class ScreenImageTest(unittest.TestCase):
    def test_framebuffer_images_have_exact_bgra_size(self):
        for name in ("splash.img.xz", "load.img.xz"):
            with self.subTest(name=name):
                raw = lzma.decompress((ROOT / name).read_bytes())
                self.assertEqual(len(raw), FRAME_BYTES)
                self.assertTrue(all(value == 255 for value in raw[3::4]))

    def test_loading_screen_reserves_boot_log_rows(self):
        raw = lzma.decompress((ROOT / "load.img.xz").read_bytes())
        expected = BGRA_OPAQUE_BLACK * (800 * (480 - 350))
        self.assertEqual(raw[350 * ROW_BYTES:], expected)


if __name__ == "__main__":
    unittest.main()
