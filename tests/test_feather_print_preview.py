## Print-page layout and native preview lifecycle integration tests.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import pathlib
import re
import struct
import sys
import tempfile
import unittest


PLUGINS = (pathlib.Path(__file__).parents[1] / ".py" / "klipper" /
           "plugins")
sys.path.insert(0, str(PLUGINS))

import feather_screen as FEATHER  # noqa: E402
from feather.screen.pages import printing as PAGES  # noqa: E402
from ff5m_ui.print_state import PrintState  # noqa: E402
from ff5m_ui.screen import ScreenPage  # noqa: E402
from ui.font_metrics import get_font_metrics  # noqa: E402

from tests.test_feather_screen import Reactor, StatusObject  # noqa: E402


class ScenarioController(FEATHER.FeatherScreen):
    """Test harness for controller scenarios without klippy:ready."""

    boot_screen_held = False
    touch_available = None
    touch_warning_visible = False
    system_shutdown_active = False


class RuntimeSd:
    def __init__(self, path):
        self.path = path
        self.estimate_print_time = 400.0

    def file_path(self):
        return self.path

    def get_status(self, _eventtime):
        return {"progress": 0.25}


class PreviewReactor(Reactor):
    NEVER = float("inf")

    def register_timer(self, callback, when):
        return (callback, when)

    def update_timer(self, _timer, _when):
        pass


class SynchronousPreviewWorker:
    """Run the off-reactor task inline while preserving its error contract."""

    def __init__(self):
        self.submitted = []

    def submit(self, task, callback):
        self.submitted.append((task, callback))
        try:
            value = task()
        except Exception as exc:
            callback(None, exc)
        else:
            callback(value, None)
        return True


class DeferredPreviewWorker:
    def __init__(self):
        self.submitted = []

    def submit(self, task, callback):
        self.submitted.append((task, callback))
        return True

    def finish(self, index):
        task, callback = self.submitted[index]
        try:
            value = task()
        except Exception as exc:
            callback(None, exc)
        else:
            callback(value, None)


def _preview_fixture_blob():
    width, height = PAGES._gcode_preview_image_rect()[2:]
    packed = bytearray((width * height + 7) // 8)
    for y in range(18, 21):
        for x in range(16, 28):
            offset = y * width + x
            packed[offset // 8] |= 1 << (7 - offset % 8)
    header = struct.pack(
        "<4sBBBBHHII", b"FXI1", 1, 0, 2, 1, width, height,
        len(packed), len(packed))
    palette = struct.pack("<II", 0xff101010, 0xffff00ff)
    return header + palette + packed


def _foreground_rows(blob):
    image = PAGES._decode_fxi1(blob)
    width = image["width"]
    packed = image["packed"]
    return {
        index // width
        for index in range(width * image["height"])
        if packed[index // 8] & (1 << (7 - index % 8))
    }


class PreviewHelper:
    def __enter__(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.directory.name) / "preview"
        self.output_path = pathlib.Path(self.directory.name) / "preview.fxi1"
        self.output_path.write_bytes(_preview_fixture_blob())
        self.path.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "input=$1\n"
            "case \"$input\" in\n"
            "  *no-preview*) exit 2 ;;\n"
            "  *failure*) echo 'fixture failure' >&2; exit 1 ;;\n"
            "  *invalid*) printf invalid; exit 0 ;;\n"
            "  *timeout*) sleep 1; exit 0 ;;\n"
            "esac\n"
            "script_dir=${0%/*}\n"
            "cat \"$script_dir/preview.fxi1\"\n",
            encoding="utf-8")
        self.path.chmod(0o755)
        self.original_path = PAGES.PREVIEW_EXECUTABLE
        self.original_timeout = PAGES.PREVIEW_TIMEOUT
        PAGES.PREVIEW_EXECUTABLE = str(self.path)
        return self

    def __exit__(self, *_args):
        PAGES.PREVIEW_EXECUTABLE = self.original_path
        PAGES.PREVIEW_TIMEOUT = self.original_timeout
        self.directory.cleanup()


def _write_gcode(prefix="preview-"):
    handle = tempfile.NamedTemporaryFile(
        "w", prefix=prefix, suffix=".gcode", delete=False,
        encoding="ascii")
    handle.write("G28\n")
    handle.close()
    return handle.name


def _controller(file_path, worker=None):
    controller = ScenarioController.__new__(ScenarioController)
    controller.renderer = FEATHER.FeatherRenderer()
    controller.batches = []

    def send(commands, **_kwargs):
        controller.batches.append(list(commands))
        return True

    controller.renderer.send = send
    controller.reactor = PreviewReactor()
    controller.print_state = PrintState.PRINTING
    controller.page = ScreenPage.PRINTING
    controller.pending_action = None
    controller.operation_context = None
    controller.virtual_sdcard = RuntimeSd(file_path)
    controller.print_stats = StatusObject({
        "state": "printing", "print_duration": 100,
        "info": {"current_layer": 5, "total_layer": 90},
    })
    controller.toolhead = StatusObject({
        "position": (10.0, 20.0, 4.5, 0.0), "homed_axes": "xyz"})
    controller.motion_report = None
    controller._live_z_adjust_allowed = lambda _eventtime: True
    controller.renderer.set_header_action("global.abort", "ABORT")
    controller.file_scan_worker = worker
    controller._gcode_preview = None
    return controller


class PreviewProcessTest(unittest.TestCase):
    def test_exit_contract_and_command_validation(self):
        with PreviewHelper():
            ready = _write_gcode()
            absent = _write_gcode("no-preview-")
            failed = _write_gcode("failure-")
            invalid = _write_gcode("invalid-")
            try:
                image = PAGES._render_gcode_preview(ready)
                self.assertEqual(
                    (image["width"], image["height"]),
                    PAGES._gcode_preview_image_rect()[2:])
                self.assertEqual(image["bounds"], (18, 20))
                self.assertIsNone(PAGES._render_gcode_preview(absent))
                with self.assertRaisesRegex(RuntimeError, "fixture failure"):
                    PAGES._render_gcode_preview(failed)
                with self.assertRaisesRegex(RuntimeError, "invalid FXI1 header"):
                    PAGES._render_gcode_preview(invalid)
            finally:
                for path in (ready, absent, failed, invalid):
                    pathlib.Path(path).unlink()

    def test_timeout_kills_the_helper(self):
        with PreviewHelper():
            path = _write_gcode("timeout-")
            PAGES.PREVIEW_TIMEOUT = 0.01
            try:
                with self.assertRaisesRegex(RuntimeError, "timed out"):
                    PAGES._render_gcode_preview(path)
            finally:
                pathlib.Path(path).unlink()


class PrintPreviewLayoutTest(unittest.TestCase):
    def test_page_keeps_controls_left_and_panel_right(self):
        controller = _controller("/data/missing.gcode")
        controller._render_print_page()
        drawing = "\n".join(controller.batches[0])

        panel_x, panel_y, panel_width, panel_height = PAGES.GCODE_PREVIEW_PANEL
        self.assertIn(
            "fill -p %d %d -s %d %d" % (
                panel_x, panel_y, panel_width, panel_height), drawing)
        self.assertIn("-t \"PREVIEW\"", drawing)
        for action in ("print.pause", "print.filament", "print.z",
                       "print.cancel"):
            button = controller.renderer._buttons[action]
            self.assertGreaterEqual(
                button[1], panel_y + panel_height + 18, action)
            self.assertLessEqual(
                get_font_metrics().text_width(button[4], button[6]),
                button[2] - 2 * FEATHER.FeatherRenderer.BUTTON_TEXT_PADDING,
                action)
        filename = [line for line in drawing.splitlines()
                    if "missing.gcode" in line][0]
        self.assertLessEqual(int(re.search(
            r"--max-width (\d+)", filename).group(1)) + 25, panel_x - 12)

    def test_progress_updates_never_paint_over_the_preview_panel(self):
        controller = _controller("/data/missing.gcode")
        controller._last_print_controls_ready = True
        controller._last_progress = None
        controller._last_time = None

        controller._update_print_progress(100)

        panel_x = PAGES.GCODE_PREVIEW_PANEL[0]
        for batch in controller.batches:
            for command in batch:
                match = re.match(
                    r"--batch (?:fill|stroke) -p (\d+) (\d+) -s (\d+) (\d+)",
                    command)
                if match is not None and int(match.group(1)) >= panel_x \
                        and int(match.group(2)) > 60:
                    self.fail("progress paint over preview panel: %s" % command)
        drawing = "\n".join(controller.batches[0])
        self.assertIn("00:01:40", drawing)
        self.assertIn("00:05:00", drawing)
        self.assertIn("5 / 90", drawing)
        self.assertIn("4.50 MM", drawing)


class PrintPreviewLifecycleTest(unittest.TestCase):
    def test_ready_result_is_painted_inside_the_box_and_cached(self):
        with PreviewHelper():
            path = _write_gcode()
            try:
                worker = SynchronousPreviewWorker()
                controller = _controller(path, worker)
                controller._render_print_page()
                controller._render_print_page()
            finally:
                pathlib.Path(path).unlink()

        self.assertEqual(len(worker.submitted), 1)
        commands = [
            command for batch in controller.batches for command in batch]
        image = next(command for command in commands
                     if command.startswith("--batch image "))
        self.assertIn("-p 564 132", image)
        self.assertEqual(image.payload[:4], b"FXI1")

    def test_preview_uses_theme_colors_and_fills_from_bottom(self):
        with PreviewHelper():
            path = _write_gcode()
            try:
                worker = SynchronousPreviewWorker()
                controller = _controller(path, worker)
                controller.print_stats.status["info"] = {
                    "current_layer": 1, "total_layer": 2,
                }
                with self.assertLogs(level="INFO") as logs:
                    controller._render_print_page()
            finally:
                pathlib.Path(path).unlink()

        images = [
            command for batch in controller.batches for command in batch
            if command.startswith("--batch image ")
        ]
        # The synchronous worker completes during the first page build, so
        # both that build and its completion redraw carry the cached pair.
        self.assertEqual(len(images), 4)
        images = images[-2:]
        pending = PAGES._decode_fxi1(images[0].payload)
        printed = PAGES._decode_fxi1(images[1].payload)
        self.assertEqual(
            pending["palette"],
            (0, 0xff000000 | int(controller.renderer.color(
                PAGES.ThemeColor.SECONDARY), 16)))
        self.assertEqual(
            printed["palette"],
            (0, 0xff000000 | int(controller.renderer.color(
                PAGES.ThemeColor.PRIMARY), 16)))
        self.assertEqual(_foreground_rows(images[0].payload), {18, 19, 20})
        self.assertEqual(_foreground_rows(images[1].payload), {19, 20})
        self.assertEqual(sum(
            "gcode preview recolor" in message for message in logs.output), 1)

    def test_layer_change_recolors_cached_mask_once(self):
        with PreviewHelper():
            path = _write_gcode()
            try:
                worker = SynchronousPreviewWorker()
                controller = _controller(path, worker)
                controller.print_stats.status["info"] = {
                    "current_layer": 1, "total_layer": 2,
                }
                controller._render_print_page()
                submitted = len(worker.submitted)
                before = len(controller.batches)
                controller.print_stats.status["info"] = {
                    "current_layer": 2, "total_layer": 2,
                }
                with self.assertLogs(level="INFO") as logs:
                    controller._update_print_progress(101)
                    controller._update_print_progress(102)
            finally:
                pathlib.Path(path).unlink()

        self.assertEqual(len(worker.submitted), submitted)
        new_images = [
            command for batch in controller.batches[before:]
            for command in batch if command.startswith("--batch image ")
        ]
        self.assertEqual(len(new_images), 1)
        self.assertEqual(_foreground_rows(new_images[0].payload), {18, 19, 20})
        self.assertEqual(sum(
            "gcode preview recolor" in message for message in logs.output), 1)

    def test_absent_preview_is_remembered_without_resubmission(self):
        with PreviewHelper():
            path = _write_gcode("no-preview-")
            try:
                worker = SynchronousPreviewWorker()
                controller = _controller(path, worker)
                controller._render_print_page()
                controller._render_print_page()
            finally:
                pathlib.Path(path).unlink()

        self.assertEqual(len(worker.submitted), 1)
        drawing = "\n".join(
            command for batch in controller.batches for command in batch)
        self.assertIn("NO PREVIEW", drawing)

    def test_stale_callback_cannot_replace_a_new_file_request(self):
        with PreviewHelper():
            first = _write_gcode("first-")
            second = _write_gcode("second-")
            try:
                worker = DeferredPreviewWorker()
                controller = _controller(first, worker)
                controller._prepare_gcode_preview()
                controller.virtual_sdcard.path = second
                controller._prepare_gcode_preview()
                second_key = controller._gcode_preview["key"]
                worker.finish(0)
            finally:
                pathlib.Path(first).unlink()
                pathlib.Path(second).unlink()

        self.assertEqual(controller._gcode_preview["key"], second_key)
        self.assertEqual(controller._gcode_preview["status"], "loading")


if __name__ == "__main__":
    unittest.main()
