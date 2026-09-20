## Theme-neutral G-code preview cache behavior tests.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import pathlib
import struct
import sys
import threading
import unittest
from unittest import mock


PLUGINS = (pathlib.Path(__file__).parents[1] / ".py" / "klipper" /
           "plugins")
sys.path.insert(0, str(PLUGINS))

from feather import previews as PREVIEWS  # noqa: E402
from feather.files import FileWorker  # noqa: E402


def _mask_blob(width, height):
    packed = b"\xff" * ((width * height + 7) // 8)
    header = struct.pack(
        "<4sBBBBHHII", b"FXI1", 1, 0, 2, 1, width, height,
        len(packed), len(packed))
    return header + struct.pack("<II", 0, 0xffffffff) + packed


class PreviewProcess:
    calls = []
    blob = b""

    def __init__(self, args, **_kwargs):
        self.args = list(args)
        self.returncode = 0
        self.calls.append(self.args)

    def communicate(self, timeout=None):
        return self.blob, b""

    def kill(self):
        self.returncode = -9


class PreviewCacheTest(unittest.TestCase):
    def setUp(self):
        PreviewProcess.calls = []
        PreviewProcess.blob = _mask_blob(4, 3)

    def test_cache_reuses_entries_and_evicts_the_least_recently_used(self):
        cache = PREVIEWS.PreviewCache(550)
        cache.store(("a", 1), b"aa")
        cache.store(("b", 1), b"bb")
        self.assertEqual(cache.lookup(("a", 1)), (True, b"aa"))

        cache.store(("c", 1), b"cc")

        self.assertEqual(cache.lookup(("b", 1)), (False, None))
        self.assertEqual(cache.lookup(("a", 1)), (True, b"aa"))
        self.assertEqual(cache.lookup(("c", 1)), (True, b"cc"))
        self.assertLessEqual(cache.size_bytes, cache.budget_bytes)

    def test_new_file_version_replaces_old_version_for_same_path(self):
        cache = PREVIEWS.PreviewCache(1024)
        old_key = ("part.gcode", 10, 1.0)
        new_key = ("part.gcode", 11, 2.0)
        cache.store(old_key, b"old")
        cache.store(new_key, b"new")

        self.assertEqual(cache.lookup(old_key), (False, None))
        self.assertEqual(cache.lookup(new_key), (True, b"new"))

    def test_oversized_entry_is_not_retained(self):
        cache = PREVIEWS.PreviewCache(2)
        self.assertFalse(cache.store(("part.gcode",), b"big"))
        self.assertEqual(cache.lookup(("part.gcode",)), (False, None))

    def test_loader_returns_valid_theme_neutral_mask(self):
        with mock.patch.object(PREVIEWS.subprocess, "Popen", PreviewProcess):
            image = PREVIEWS.load_preview("part.gcode", 4, 3)
        themed = PREVIEWS.colorize_preview(
            image, None, "123456", "123456")[0]

        self.assertEqual(len(PreviewProcess.calls), 1)
        self.assertEqual(
            PREVIEWS.decode_fxi1(themed)["palette"],
            (0, 0xff123456))

    def test_loader_rejects_non_mask_output(self):
        payload = b"\xff" * 3
        PreviewProcess.blob = struct.pack(
            "<4sBBBBHHII", b"FXI1", 1, 0, 4, 2, 4, 3, 3, 3)
        PreviewProcess.blob += struct.pack("<4I", 0, 1, 2, 3) + payload
        with mock.patch.object(PREVIEWS.subprocess, "Popen", PreviewProcess):
            with self.assertRaisesRegex(RuntimeError, "non-mask"):
                PREVIEWS.load_preview("part.gcode", 4, 3)

    def test_cancelled_preview_does_not_start_a_process(self):
        cancel = threading.Event()
        cancel.set()
        with mock.patch.object(PREVIEWS.subprocess, "Popen") as start:
            with self.assertRaises(PREVIEWS.PreviewCancelled):
                PREVIEWS.load_preview("part.gcode", 4, 3, cancel=cancel)
        start.assert_not_called()

    def test_cancellation_kills_and_reaps_the_running_helper(self):
        cancel = threading.Event()
        process = mock.Mock()
        events = []

        def communicate(timeout=None):
            if timeout is not None:
                self.assertLessEqual(timeout, 0.1)
                cancel.set()
                raise PREVIEWS.subprocess.TimeoutExpired("preview", timeout)
            events.append("reaped")
            return b"", b""

        process.communicate.side_effect = communicate
        process.kill.side_effect = lambda: events.append("killed")
        with mock.patch.object(PREVIEWS.subprocess, "Popen", return_value=process):
            with self.assertRaises(PREVIEWS.PreviewCancelled):
                PREVIEWS.load_preview("part.gcode", 4, 3, cancel=cancel)
        self.assertEqual(events, ["killed", "reaped"])

    def test_timeout_kills_and_reaps_the_helper(self):
        process = mock.Mock()
        with mock.patch.object(PREVIEWS.subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                PREVIEWS.load_preview("part.gcode", 4, 3, timeout=0)
        process.kill.assert_called_once_with()
        process.communicate.assert_called_once_with()

    def test_worker_reaps_cancelled_helper_before_starting_next_task(self):
        cancel = threading.Event()
        started = threading.Event()
        finished = threading.Event()
        events = []
        process = mock.Mock()

        def communicate(timeout=None):
            if timeout is not None:
                started.set()
                cancel.wait(1)
                raise PREVIEWS.subprocess.TimeoutExpired("preview", timeout)
            events.append("reaped")
            return b"", b""

        process.communicate.side_effect = communicate
        process.kill.side_effect = lambda: events.append("killed")
        worker = FileWorker(lambda callback: callback(0))
        try:
            with mock.patch.object(PREVIEWS.subprocess, "Popen", return_value=process):
                worker.submit(
                    lambda: PREVIEWS.load_preview("part.gcode", 4, 3, cancel=cancel),
                    lambda value, error: events.append(type(error)))
                self.assertTrue(started.wait(1))
                cancel.set()
                worker.submit(
                    lambda: events.append("next task"),
                    lambda value, error: finished.set())
                self.assertTrue(finished.wait(2))
        finally:
            cancel.set()
            worker.stop()
        self.assertEqual(events, [
            "killed", "reaped", PREVIEWS.PreviewCancelled, "next task"])


if __name__ == "__main__":
    unittest.main()
