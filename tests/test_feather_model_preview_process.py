## Model preview process, cancellation and transport contracts.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import pathlib
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock


PLUGINS = pathlib.Path(__file__).parents[1] / ".py" / "klipper" / "plugins"
sys.path.insert(0, str(PLUGINS))

from feather import model_preview as PREVIEW
from feather.previews import PreviewCancelled, decode_fxi1
from ui import FeatherRenderer, MAX_ATOMIC_DRAW


class ModelFrameBandsTest(unittest.TestCase):
    def test_bands_preserve_pixels_palette_and_fit_the_atomic_transport(self):
        renderer = FeatherRenderer()
        for width, height in ((16, 16), (189, 188), (257, 255), (511, 511), (512, 512)):
            with self.subTest(width=width, height=height):
                count = width * height
                packed = bytes((index % 8 << 4) | ((index + 1) % 8)
                               for index in range(0, count, 2))
                palette = tuple(0xff000000 | index for index in range(8))
                blob = (struct.pack("<4sBBBBHHII", b"FXI1", 1, 0, 8, 4,
                                    width, height, len(packed), len(packed))
                        + struct.pack("<8I", *palette) + packed)
                bands = PREVIEW.model_frame_bands(blob)
                output, rows = bytearray(), 0
                commands = []
                for offset, band in bands:
                    image = decode_fxi1(band)
                    self.assertEqual(offset, rows)
                    self.assertEqual(image["width"], width)
                    self.assertEqual(image["palette"], palette)
                    rows += image["height"]
                    output.extend(image["packed"])
                    commands.append(renderer.image(40, 118 + offset, band))
                self.assertEqual(rows, height)
                self.assertEqual(output, packed)
                self.assertTrue(all(len(frame) <= MAX_ATOMIC_DRAW
                                    for frame in renderer._encode_frames(commands, receipt="bands")))


class ModelPreviewProcessTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = pathlib.Path(directory.name)
        self.source = self.root / "part.gcode"
        self.source.write_text("normal")
        self.cache = self.root / "cache"
        self.executable = self.root / "preview-helper"
        self.executable.write_text("#!%s\n" % sys.executable + '''
import pathlib, struct, sys, time
source, cache = map(pathlib.Path, sys.argv[1:3])
mode = source.read_text()
cache.write_text("prepared")
with source.with_suffix(".starts").open("a") as trace:
    trace.write(str(cache) + "\\n")
with source.with_suffix(".arguments").open("a") as trace:
    trace.write(" ".join(sys.argv[3:]) + "\\n")
sys.stdout.buffer.write(struct.pack("<I", 3))
sys.stdout.buffer.flush()
for request in sys.stdin:
    with source.with_suffix(".requests").open("a") as trace:
        trace.write(request)
    source.with_suffix(".requested").touch()
    if mode == "stall":
        time.sleep(60)
    if mode == "stop":
        sys.stderr.write("fixture process stopped\\n")
        break
    size = 0 if mode == "invalid" else 180
    sys.stdout.buffer.write(struct.pack("<II", 1234, size))
    if size:
        sys.stdout.buffer.write(struct.pack("<4sBBBBHHII", b"FXI1", 1, 0, 8, 4, 16, 16, 128, 128))
        sys.stdout.buffer.write(struct.pack("<8I", *([0xff123456] * 8)) + bytes(128))
    sys.stdout.buffer.flush()
''')
        self.executable.chmod(0o755)

    def session(self, use_cache=True):
        session = PREVIEW.ModelPreview(
            str(self.source), (4, 192, 45.0, 45.0, False, 24.0, 30), 16, 16,
            threading.Event(), executable=str(self.executable),
            cache_dir=str(self.cache), use_cache=use_cache)
        self.addCleanup(session.close)
        return session

    def render(self, session):
        return session.render(45, 1, "123456", "654321", "111111")

    def test_cached_frames_share_one_process_and_close_reaps_it(self):
        session = self.session()
        first = self.render(session)
        process = session.process
        self.assertEqual(self.render(session), first)
        self.assertIs(session.process, process)
        self.assertEqual(session.layers, 3)
        self.assertAlmostEqual(session.last_render_ms, 1.234)
        self.assertEqual(decode_fxi1(first)["width"], 16)
        self.assertEqual(len(self.source.with_suffix(".starts").read_text().splitlines()), 1)
        session.close()
        self.assertIsNone(session.process)
        self.assertIsNotNone(process.poll())

    def test_signed_camera_angles_are_sent_without_changing_the_resident_model(self):
        session = self.session()
        for yaw, pitch in ((0, 30), (-180, -90), (180, 90), (0, 0)):
            session.settings = session.settings[:3] + (pitch,) + session.settings[4:]
            session.render(yaw, 1, "123456", "654321", "111111")
        requests = [tuple(map(float, line.split()[:2]))
                    for line in self.source.with_suffix(".requests").read_text().splitlines()]
        self.assertEqual(requests, [(0, 30), (-180, -90), (180, 90), (0, 0)])
        self.assertEqual(len(self.source.with_suffix(".starts").read_text().splitlines()), 1)

    def test_scale_is_sent_per_frame_without_rebuilding_the_model(self):
        session = self.session()
        self.render(session)
        process = session.process
        session.render(45, 1, "123456", "654321", "111111", scale=1.75)
        requests = [line.split() for line in self.source.with_suffix(".requests").read_text().splitlines()]
        self.assertEqual([len(request) for request in requests], [8, 8])
        self.assertEqual([float(request[-1]) for request in requests], [1.0, 1.75])
        self.assertIs(session.process, process)

    def test_density_is_forwarded_to_native_preparation_and_separates_caches(self):
        for density in (5, 100):
            session = self.session()
            session.settings = (2,) + session.settings[1:7] + (1.0, density)
            self.render(session)
            session.close()
        arguments = self.source.with_suffix(".arguments").read_text().splitlines()
        self.assertEqual([line.split()[-1] for line in arguments], ["5", "100"])
        caches = self.source.with_suffix(".starts").read_text().splitlines()
        self.assertNotEqual(caches[0], caches[1])

    def test_live_progress_uses_the_handshake_layer_count_on_the_first_frame(self):
        session = self.session()
        self.assertEqual(session.layers, 0)
        session.render(45, 0, "123456", "654321", "111111", layer_fraction=2 / 3)
        request = self.source.with_suffix(".requests").read_text().split()
        self.assertEqual(int(request[2]), 2)

    def test_uncached_frames_use_unique_scratch_files_and_preserve_existing_cache(self):
        cached = self.session()
        self.render(cached)
        existing = {path.name: path.read_bytes() for path in self.cache.iterdir()}
        cached.close()
        session = self.session(use_cache=False)
        for _ in range(2):
            self.render(session)
            self.assertIsNone(session.process)
            self.assertIsNone(session.scratch)
            self.assertEqual({path.name: path.read_bytes() for path in self.cache.iterdir()}, existing)
        starts = self.source.with_suffix(".starts").read_text().splitlines()
        self.assertNotEqual(starts[-1], starts[-2])

    def test_invalid_frame_and_process_exit_reap_helper_and_clean_scratch(self):
        for mode, diagnostic in (("invalid", "invalid frame size"), ("stop", "fixture process stopped")):
            with self.subTest(mode=mode):
                self.source.write_text(mode)
                session = self.session(use_cache=False)
                with self.assertRaisesRegex(RuntimeError, diagnostic):
                    self.render(session)
                self.assertIsNone(session.process)
                self.assertIsNone(session.scratch)
                self.assertEqual(list(self.cache.iterdir()), [])

    def test_preparation_timeout_reaps_helper_and_removes_scratch(self):
        session = self.session(use_cache=False)
        with mock.patch.object(PREVIEW.time, "monotonic", side_effect=(0, 121)):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                self.render(session)
        self.assertIsNone(session.process)
        self.assertIsNone(session.scratch)
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_cancellation_interrupts_an_inflight_pipe_read_and_reaps_helper(self):
        self.source.write_text("stall")
        session = self.session(use_cache=False)
        errors = []

        def render():
            try:
                self.render(session)
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=render)
        worker.start()
        try:
            deadline = time.monotonic() + 5
            while not self.source.with_suffix(".requested").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(self.source.with_suffix(".requested").exists())
        finally:
            session.cancel()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], PreviewCancelled)
        self.assertIsNone(session.process)
        self.assertIsNone(session.scratch)
        self.assertEqual(list(self.cache.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
