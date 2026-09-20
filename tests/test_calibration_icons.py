## Palette, resource lifetime, and binary transport contracts for screw icons.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import io
from pathlib import Path
import struct
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".py/klipper/plugins"))

from ff5m_ui import calibration_icons as icons
from ff5m_ui.calibration_screws import runtime as screws
from feather.previews import decode_fxi1
from ui import ThemeColor
from ui.renderer import BinaryCommand, FeatherRenderer


def pixels(blob):
    image = decode_fxi1(blob)
    bpp = image["bpp"]
    mask = (1 << bpp) - 1
    return [
        image["palette"][(image["packed"][index * bpp // 8]
                           >> (8 - bpp - index * bpp % 8)) & mask]
        for index in range(image["width"] * image["height"])
    ]


class CalibrationIconsTest(unittest.TestCase):
    def tearDown(self):
        icons._load.cache_clear()

    def test_palette_tracks_theme_without_changing_source_or_pixels(self):
        renderer = FeatherRenderer()
        original = icons._load("nut")
        default = icons.nut(renderer, 100, 100).payload
        renderer.set_theme("AMBER")
        amber = icons.nut(renderer, 100, 100).payload
        self.assertNotEqual(default[20:36], amber[20:36])
        self.assertEqual(default[36:], amber[36:])
        self.assertEqual(default[36:], original[36:])
        self.assertEqual(struct.unpack_from("<4I", amber, 20), (0,) + tuple(
            0xff000000 | int(renderer.color(token), 16)
            for token in (ThemeColor.BRIGHT, ThemeColor.DIM, ThemeColor.PANEL)))
        renderer.set_theme("DEFAULT")
        self.assertEqual(icons.nut(renderer, 100, 100).payload, default)
        self.assertIs(icons._load("nut"), original)
        self.assertEqual(original[20:36], bytes(16))

    def test_prebuilt_arrows_are_exact_horizontal_reflections(self):
        renderer = FeatherRenderer()
        right = icons.turn_arrow(renderer, 100, 100, "CW", ThemeColor.PRIMARY).payload
        left = icons.turn_arrow(renderer, 100, 100, "CCW", ThemeColor.PRIMARY).payload
        width = decode_fxi1(right)["width"]
        right_pixels, left_pixels = pixels(right), pixels(left)
        self.assertIn(0, right_pixels)
        self.assertIn(0xff000000 | int(renderer.color(ThemeColor.PRIMARY), 16), right_pixels)
        for row in range(decode_fxi1(right)["height"]):
            self.assertEqual(left_pixels[row * width:(row + 1) * width],
                             right_pixels[row * width:(row + 1) * width][::-1])

    def test_only_immutable_source_is_loaded_once_per_icon(self):
        icons._load.cache_clear()
        renderer = FeatherRenderer()
        with mock.patch("builtins.open", wraps=open) as read:
            icons.nut(renderer, 0, 0)
            icons.nut(renderer, 10, 10)
            renderer.set_theme("AMBER")
            icons.nut(renderer, 20, 20)
        self.assertEqual(read.call_count, 1)

    def test_corrupt_resource_fails_without_poisoning_cache(self):
        icons._load.cache_clear()
        renderer = FeatherRenderer()
        with mock.patch("builtins.open", return_value=io.BytesIO(b"broken")):
            with self.assertRaisesRegex(ValueError, "Invalid calibration icon: nut"):
                icons.nut(renderer, 0, 0)
        self.assertIsInstance(icons.nut(renderer, 0, 0), BinaryCommand)

    def test_base_has_a_nut_but_no_arrow_and_frame_reaches_transport(self):
        renderer = FeatherRenderer()
        values = [dict(name=corner + " screw", direction="CW", turns="00:05")
                  for corner in ("rear left", "rear right", "front left", "front right")]
        commands = renderer.begin_page("Calibration result")
        commands += screws.render(renderer, values, "rear left screw")
        images = [command for command in commands if isinstance(command, BinaryCommand)]
        nut_blob = icons.nut(renderer, 0, 0).payload
        arrow_blob = icons.turn_arrow(renderer, 0, 0, "CW", ThemeColor.PRIMARY).payload
        self.assertEqual(sum(image.payload == nut_blob for image in images), 4)
        self.assertEqual(sum(image.payload == arrow_blob for image in images), 3)
        self.assertEqual(len(images), 7)
        self.assertTrue(renderer.send(commands))
        frames = renderer._encode_frames(commands)
        for command in images:
            self.assertTrue(any(command.payload in frame for frame in frames))
