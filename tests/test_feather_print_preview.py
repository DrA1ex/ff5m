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
from unittest import mock


PLUGINS = (pathlib.Path(__file__).parents[1] / ".py" / "klipper" /
           "plugins")
sys.path.insert(0, str(PLUGINS))

import feather_screen as FEATHER  # noqa: E402
from feather.screen.pages import printing as PAGES  # noqa: E402
from ff5m_ui.print_state import PrintState  # noqa: E402
from ff5m_ui.printing import runtime as printing_ui  # noqa: E402
from ff5m_ui.printing import page as PRINTING_PAGE  # noqa: E402
from ff5m_ui.screen import ScreenPage  # noqa: E402
from feather.previews import (  # noqa: E402
    PREVIEW_MASK_SIZE, PreviewCache, colorize_preview, decode_fxi1)
from feather.files import FileEntry  # noqa: E402
from ui.font_metrics import get_font_metrics  # noqa: E402

from tests.feather_timelapse_test_helper import make_timelapse_state
from tests.test_feather_screen import Reactor, StatusObject  # noqa: E402
from tests.feather_render_test_helper import RenderCapture, full_render_queue  # noqa: E402


class ScenarioController(FEATHER.FeatherScreen):
    """Test harness for controller scenarios without klippy:ready."""

    boot_screen_held = False
    timelapse_state = make_timelapse_state()
    touch_available = None
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
    def __init__(self):
        super().__init__()
        self.callbacks = []

    def register_callback(self, callback, when=None):
        self.callbacks.append(callback)

    def flush_callbacks(self):
        while self.callbacks:
            self.callbacks.pop(0)(self.now)

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
    image = decode_fxi1(blob)
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
    controller.batches = RenderCapture(controller.renderer).batches
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
    controller.file_worker = worker
    controller.selected_file = None
    controller.preview_cache = PreviewCache(256 * 1024)
    controller.file_preview_failures = set()
    controller.file_preview_preload_signature = ()
    controller.file_preview_preload_attempted = set()
    controller.file_preview_visible_attempted = set()
    controller._gcode_preview = None
    return controller


class PreviewProcessTest(unittest.TestCase):
    def test_print_page_uses_shared_preview_loader(self):
        width, height = PAGES._gcode_preview_image_rect()[2:]
        with mock.patch.object(
                PAGES, "load_preview", return_value=None) as load:
            self.assertIsNone(
                PAGES._render_gcode_preview("/data/part.gcode"))

        load.assert_called_once_with(
            "/data/part.gcode", width, height,
            executable=PAGES.PREVIEW_EXECUTABLE,
            timeout=PAGES.PREVIEW_TIMEOUT, cancel=None)

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


class PrintStatusUpdateTest(unittest.TestCase):
    def setUp(self):
        self.page = PRINTING_PAGE.create_page()
        self.page.prepare_layout()
        self.renderer = FEATHER.FeatherRenderer()
        page_patch = mock.patch.object(PRINTING_PAGE, "PAGE", self.page)
        page_patch.start()
        self.addCleanup(page_patch.stop)
        self.values = dict(self.page.initial_state())
        self.values.update({
            printing_ui.PrintingState.FILENAME: "context_print-kamp.gcode",
            printing_ui.PrintingState.STATUS: "PRINT -> HOMING",
            printing_ui.PrintingState.PROGRESS: 31,
            printing_ui.PrintingState.HEIGHT: "-125.00 MM",
            printing_ui.PrintingState.CONTROLS_READY: False,
            printing_ui.PrintingState.PREVIEW_STATUS: "loading",
        })

    def test_first_homing_draw_reuses_prepared_geometry(self):
        layout = self.page.layout
        with mock.patch.object(self.page.root, "arrange", side_effect=AssertionError("geometry recalculated")):
            with mock.patch.object(self.page.root, "update", side_effect=AssertionError("full update")):
                with mock.patch.object(self.page.styles, "apply", side_effect=AssertionError("style pass")):
                    commands = printing_ui.render(self.renderer, self.values, reuse_layout=True)
        self.assertIs(self.page.layout, layout)
        drawing = "\n".join(commands)
        for value in ("PRINT -> HOMING", "31%", "-125.00 MM"):
            self.assertIn(value, drawing)

    def test_status_updates_skip_unrelated_components_and_styles(self):
        printing_ui.render(self.renderer, self.values, reuse_layout=True)
        layout = self.page.layout
        elapsed = self.page.node(printing_ui.PrintingRef.ELAPSED)
        with mock.patch.object(self.page.root, "arrange", side_effect=AssertionError("geometry recalculated")):
            with mock.patch.object(self.page.root, "update", side_effect=AssertionError("full update")):
                with mock.patch.object(elapsed, "update", side_effect=AssertionError("unrelated metric")):
                    with mock.patch.object(self.page.styles, "apply", side_effect=AssertionError("style pass")):
                        commands = printing_ui.update(self.renderer, {
                            printing_ui.PrintingState.STATUS: "PRINT -> HEATING"})
                        self.assertEqual(printing_ui.update(self.renderer, {
                            printing_ui.PrintingState.STATUS: "PRINT -> HEATING"}), [])
        self.assertIs(self.page.layout, layout)
        drawing = "\n".join(commands)
        self.assertIn("PRINT -> HEATING", drawing)
        self.assertNotIn("ELAPSED", drawing)
        self.assertNotIn("PREVIEW", drawing)

    def test_status_reserves_two_lines_without_moving_other_content(self):
        printing_ui.render(self.renderer, self.values, reuse_layout=True)
        original = {key: self.page.rect(key) for key in self.page.layout.keys()}
        status = "PRINT -> WAITING FOR THE BED AND NOZZLE TO REACH THE REQUESTED TEMPERATURE"
        for value in (status, "PRINT -> HOMING", ""):
            commands = printing_ui.update(self.renderer, {printing_ui.PrintingState.STATUS: value})
            self.values[printing_ui.PrintingState.STATUS] = value
            fresh = PRINTING_PAGE.create_page()
            fresh.draw(FEATHER.FeatherRenderer(), self.values)
            for key in self.page.layout.keys():
                self.assertEqual(self.page.rect(key), fresh.rect(key))
                self.assertEqual(self.page.rect(key), original[key])
            self.assertTrue(commands)
            drawing = "\n".join(commands)
            self.assertNotIn("--batch fill -p 0 0 -s 800 442", drawing)
            for text in ("PROGRESS", "ELAPSED", "PREVIEW"):
                self.assertNotIn(text, drawing)
        status_bounds = self.page.rect(printing_ui.PrintingRef.STATUS)
        font = self.page.node(printing_ui.PrintingRef.STATUS).font
        self.assertGreaterEqual(status_bounds.height, 2 * get_font_metrics().metric(font).glyph_height)


class PrintPreviewLayoutTest(unittest.TestCase):
    def test_preview_decode_starts_after_the_page_frame_unless_cancelled(self):
        worker = DeferredPreviewWorker()
        controller = _controller("/data/part.gcode", worker)
        controller._render_print_page()
        self.assertEqual(worker.submitted, [])
        controller.reactor.flush_callbacks()
        self.assertEqual(len(worker.submitted), 1)

        controller._cancel_gcode_preview()
        controller._render_print_page()
        controller._cancel_gcode_preview()
        controller.reactor.flush_callbacks()
        self.assertEqual(len(worker.submitted), 1)

    def test_worker_refusal_after_loading_frame_repaints_without_preview(self):
        worker = mock.Mock()
        worker.submit.return_value = False
        controller = _controller("/data/part.gcode", worker)
        controller._render_print_page()
        painted = len(controller.batches)

        with self.assertLogs(level="INFO"):
            controller.reactor.flush_callbacks()

        self.assertEqual(controller._gcode_preview["status"], "failed")
        self.assertGreater(len(controller.batches), painted)
        self.assertNotIn("--batch image ", "\n".join(controller.batches[-1]))

    def test_ready_preview_redraw_skips_unchanged_metrics_and_styles(self):
        with PreviewHelper():
            path = _write_gcode()
            try:
                worker = DeferredPreviewWorker()
                controller = _controller(path, worker)
                controller._render_print_page()
                controller.reactor.flush_callbacks()
                page = printing_ui.get_page()
                elapsed = page.node(printing_ui.PrintingRef.ELAPSED)

                with mock.patch.object(page.root, "update", side_effect=AssertionError("full update")):
                    with mock.patch.object(elapsed, "update", side_effect=AssertionError("unchanged metric")):
                        with mock.patch.object(page.styles, "apply", side_effect=AssertionError("style pass")):
                            worker.finish(0)
            finally:
                pathlib.Path(path).unlink()

        drawing = "\n".join(controller.batches[-1])
        self.assertIn("--batch image ", drawing)
        self.assertIn("--batch clear-hitboxes --layer base", drawing)

    def test_incomplete_full_redraw_values_use_regular_update(self):
        controller = _controller("/data/missing.gcode")
        controller._render_print_page()
        controller.reactor.flush_callbacks()
        page = printing_ui.get_page()

        with mock.patch.object(page.styles, "apply", wraps=page.styles.apply) as apply:
            commands = printing_ui.render(
                controller.renderer,
                {printing_ui.PrintingState.STATUS: "NEW STATUS"},
                reuse_layout=True)

        apply.assert_called_once()
        self.assertTrue(any("NEW STATUS" in command for command in commands))

    def test_ready_preview_repaints_full_surface_with_existing_geometry(self):
        with PreviewHelper():
            path = _write_gcode()
            try:
                worker = DeferredPreviewWorker()
                controller = _controller(path, worker)
                controller._render_print_page()
                controller.reactor.flush_callbacks()
                layout = printing_ui.get_page().layout
                worker.finish(0)
            finally:
                pathlib.Path(path).unlink()

        self.assertIs(printing_ui.get_page().layout, layout)
        drawing = "\n".join(controller.batches[-1])
        self.assertIn("--batch clear-hitboxes --layer base", drawing)
        self.assertIn("--batch fill -p 0 0 -s 800 442", drawing)
        self.assertIn("--batch image ", drawing)
        self.assertIn("print.cancel", controller.renderer._buttons)

    def test_first_draw_contains_current_progress_without_a_second_paint(self):
        controller = _controller("/data/missing.gcode")
        controller._render_print_page()
        controller.reactor.flush_callbacks()
        drawing = "\n".join(controller.batches[0])
        self.assertEqual(drawing.count('-t "00:01:40"'), 1)
        self.assertEqual(drawing.count('-t "4.50 MM"'), 1)
        self.assertNotIn('-t "0.00 MM"', drawing)

    def test_unchanged_progress_skips_command_construction(self):
        controller = _controller("/data/missing.gcode")
        controller._render_print_page()
        controller.reactor.flush_callbacks()
        controller.batches.clear()
        with mock.patch.object(printing_ui, "update_progress", wraps=printing_ui.update_progress) as update:
            controller._update_print_progress(100)
            update.assert_not_called()
        self.assertEqual(controller.batches, [])

    def test_preparation_time_keeps_updating_without_full_page_traversal(self):
        controller = _controller("/data/missing.gcode")
        controller.print_state = PrintState.PREPARING
        controller.start_print_macro = type("Start", (), {
            "variables": {"print_started": False}})()
        controller._render_print_page()
        controller.reactor.flush_callbacks()
        controller.batches.clear()
        controller.print_stats.status["print_duration"] = 101
        page = printing_ui.get_page()

        with mock.patch.object(page.root, "update", side_effect=AssertionError("full page update")):
            with mock.patch.object(page.styles, "apply", side_effect=AssertionError("style pass")):
                controller._update_print_progress(101)

        self.assertEqual(len(controller.batches), 1)
        self.assertIn('00:01:41', "\n".join(controller.batches[0]))

    def test_height_only_update_does_not_repaint_other_metrics(self):
        controller = _controller("/data/missing.gcode")
        controller._render_print_page()
        controller.reactor.flush_callbacks()
        controller.batches.clear()
        controller.toolhead.status["position"] = (10.0, 20.0, 6.25, 0.0)
        elapsed = printing_ui.get_page().node(printing_ui.PrintingRef.ELAPSED)
        with mock.patch.object(elapsed, "update", side_effect=AssertionError("unchanged metric updated")):
            controller._update_print_progress(101)
        drawing = "\n".join(controller.batches[0])
        self.assertIn('6.25 MM', drawing)
        for text in ('00:01:40', '00:05:00', '5 / 90', 'PROGRESS'):
            self.assertNotIn(text, drawing)

    def test_ready_controls_and_live_z_update_without_restarting_preview(self):
        controller = _controller("/data/missing.gcode", DeferredPreviewWorker())
        controller.print_state = PrintState.PREPARING
        controller._live_z_adjust_allowed = lambda _eventtime: False
        controller._render_print_page()
        controller.reactor.flush_callbacks()
        preview = controller._gcode_preview
        controller.batches.clear()
        controller.print_state = PrintState.PRINTING
        controller._live_z_adjust_allowed = lambda _eventtime: True
        with mock.patch.object(controller, "_render_print_page", side_effect=AssertionError("full page paint")):
            controller._update_print_progress(101)
        self.assertIs(controller._gcode_preview, preview)
        self.assertFalse(preview["cancel"].is_set())
        self.assertEqual(len(controller.file_worker.submitted), 1)
        drawing = "\n".join(controller.batches[0])
        for text in ("PAUSE", "FILAMENT", "Z ADJUST"):
            self.assertIn(text, drawing)
        for text in ("PREVIEW", "missing.gcode", "ELAPSED", "CANCEL"):
            self.assertNotIn(text, drawing)
        actions = {region.action for region in controller.renderer.hitboxes}
        self.assertTrue({"print.pause", "print.filament", "print.z", "print.cancel"} <= actions)

    def test_pending_button_refresh_and_queue_refusal_preserve_visible_input(self):
        controller = _controller("/data/missing.gcode")
        controller._render_print_page()
        controller.reactor.flush_callbacks()
        generation = controller.renderer.generation
        controller.pending_action = "print.pause"
        with full_render_queue(controller.renderer):
            controller._update_print_controls()
        self.assertEqual(controller.renderer.generation, generation)
        self.assertTrue(any(region.action == "print.pause" for region in controller.renderer.hitboxes))
        controller._render_print_page()
        self.assertFalse(any(region.action == "print.pause" for region in controller.renderer.hitboxes))
        controller.batches.clear()
        controller.pending_action = None
        controller._update_print_controls()
        drawing = "\n".join(controller.batches[0])
        self.assertIn("PAUSE", drawing)
        for text in ("PREVIEW", "ELAPSED", "CANCEL", "FILAMENT", "Z ADJUST"):
            self.assertNotIn(text, drawing)
        self.assertTrue(any(region.action == "print.pause" for region in controller.renderer.hitboxes))

    def test_progress_change_updates_track_and_percentage(self):
        controller = _controller("/data/missing.gcode")
        controller._render_print_page()
        controller.reactor.flush_callbacks()
        controller.batches.clear()
        controller.print_stats.status["print_duration"] = 200
        controller._update_print_progress(101)
        drawing = "\n".join(controller.batches[0])
        self.assertIn('50%', drawing)
        track = printing_ui.rect(printing_ui.PrintingRef.PROGRESS)
        self.assertIn("stroke -p %d %d" % (track.x, track.y), drawing)

    def test_page_keeps_controls_left_and_panel_right(self):
        controller = _controller("/data/missing.gcode")
        controller._render_print_page()
        controller.reactor.flush_callbacks()
        drawing = "\n".join(controller.batches[0])

        panel = printing_ui.rect(printing_ui.PrintingRef.PREVIEW)
        panel_x, panel_y, panel_width, panel_height = panel.as_tuple()
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
        details = printing_ui.rect(printing_ui.PrintingRef.DETAILS)
        self.assertLessEqual(int(re.search(
            r"--max-width (\d+)", filename).group(1)) + details.x,
            panel_x - 12)

    def test_file_tiles_and_print_page_share_one_preview_resolution(self):
        self.assertEqual(
            PAGES._gcode_preview_image_rect()[2:], PREVIEW_MASK_SIZE)

    def test_progress_updates_never_paint_over_the_preview_panel(self):
        controller = _controller("/data/missing.gcode")
        # Start from a rendered page, independent of the previous test's
        # singleton page state; then change every telemetry field.
        controller.print_stats.status["print_duration"] = 0
        controller.print_stats.status["info"] = {}
        controller.toolhead.status["position"] = (0, 0, 0, 0)
        controller._render_print_page()
        controller.reactor.flush_callbacks()
        controller.batches.clear()
        controller.print_stats.status["print_duration"] = 100
        controller.print_stats.status["info"] = {"current_layer": 5, "total_layer": 90}
        controller.toolhead.status["position"] = (10.0, 20.0, 4.5, 0.0)
        controller._last_print_controls_ready = True
        controller._last_progress = None
        controller._last_time = None

        controller._update_print_progress(100)

        panel_x = printing_ui.rect(printing_ui.PrintingRef.PREVIEW).x
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
    def test_print_reuses_a_browser_preview_with_current_metadata(self):
        path = _write_gcode()
        try:
            file_stat = pathlib.Path(path).stat()
            controller = _controller(path, SynchronousPreviewWorker())
            controller.selected_file = FileEntry(
                pathlib.Path(path).name, path,
                size=file_stat.st_size, mtime=file_stat.st_mtime)
            controller.preview_cache = PreviewCache(256 * 1024)
            width, height = PAGES._gcode_preview_image_rect()[2:]
            image = decode_fxi1(_preview_fixture_blob())
            image["bounds"] = (18, 20)
            color = controller.renderer.color(PAGES.ThemeColor.PRIMARY)
            blob = colorize_preview(
                image, None, color, color)[0]
            controller.preview_cache.store(
                (path, file_stat.st_size, file_stat.st_mtime, width, height),
                {"image": image, "color": color, "blob": blob})

            with mock.patch.object(
                    PAGES, "_render_gcode_preview",
                    side_effect=AssertionError("preview helper was called")):
                controller._render_print_page()
                controller.reactor.flush_callbacks()
        finally:
            pathlib.Path(path).unlink()

        self.assertEqual(controller._gcode_preview["status"], "ready")
        self.assertEqual(
            len(controller.file_worker.submitted), 1)

    def test_external_print_does_not_reuse_stale_selected_file_metadata(self):
        path = _write_gcode()
        try:
            old_stat = pathlib.Path(path).stat()
            controller = _controller(path, SynchronousPreviewWorker())
            controller.selected_file = FileEntry(
                pathlib.Path(path).name, path,
                size=old_stat.st_size, mtime=old_stat.st_mtime)
            width, height = PAGES._gcode_preview_image_rect()[2:]
            image = decode_fxi1(_preview_fixture_blob())
            image["bounds"] = (18, 20)
            color = controller.renderer.color(PAGES.ThemeColor.PRIMARY)
            old_key = (
                path, old_stat.st_size, old_stat.st_mtime, width, height)
            controller.preview_cache.store(old_key, {
                "image": image,
                "color": color,
                "blob": colorize_preview(image, None, color, color)[0],
            })

            pathlib.Path(path).write_text(
                "G28\nG1 X10 Y10\n", encoding="ascii")
            current_stat = pathlib.Path(path).stat()
            current_key = (
                path, current_stat.st_size, current_stat.st_mtime,
                width, height)
            with mock.patch.object(
                    PAGES, "_render_gcode_preview",
                    return_value=decode_fxi1(
                        _preview_fixture_blob())) as render_preview:
                controller._render_print_page()
                controller.reactor.flush_callbacks()
        finally:
            pathlib.Path(path).unlink()

        render_preview.assert_called_once_with(
            path, controller._gcode_preview["cancel"])
        self.assertNotEqual(old_key, current_key)
        self.assertTrue(controller.preview_cache.contains(current_key))
        self.assertFalse(controller.preview_cache.contains(old_key))

    def test_preview_identity_does_not_read_the_filesystem(self):
        controller = _controller("/data/current.gcode")

        original_stat = PAGES.os.stat
        PAGES.os.stat = lambda _path: self.fail(
            "preview identity touched the filesystem")
        try:
            key = controller._gcode_preview_key_for(
                controller.virtual_sdcard.file_path())
        finally:
            PAGES.os.stat = original_stat

        self.assertEqual(key[0], "/data/current.gcode")

    def test_inactive_print_never_keeps_preview_animation_alive(self):
        controller = _controller("/data/current.gcode")
        controller._gcode_preview = {
            "key": controller._gcode_preview_key_for(
                controller.virtual_sdcard.file_path()),
            "status": "loading",
        }

        controller.print_state = PrintState.INACTIVE

        self.assertFalse(controller._gcode_preview_loading_active())

    def test_ready_result_is_painted_and_shared_with_the_browser(self):
        with PreviewHelper():
            path = _write_gcode()
            try:
                worker = SynchronousPreviewWorker()
                controller = _controller(path, worker)
                file_stat = pathlib.Path(path).stat()
                controller.selected_file = FileEntry(
                    pathlib.Path(path).name, path,
                    size=file_stat.st_size, mtime=file_stat.st_mtime)
                width, height = PAGES._gcode_preview_image_rect()[2:]
                cache_key = (
                    path, file_stat.st_size, file_stat.st_mtime,
                    width, height)
                controller._render_print_page()
                controller.reactor.flush_callbacks()
                controller._render_print_page()
                controller.reactor.flush_callbacks()
            finally:
                pathlib.Path(path).unlink()

        self.assertEqual(len(worker.submitted), 1)
        commands = [
            command for batch in controller.batches for command in batch]
        image = next(command for command in commands
                     if command.startswith("--batch image "))
        box = printing_ui.rect(printing_ui.PrintingRef.PREVIEW_BOX)
        self.assertIn("-p %d %d" % (
            box.x + (box.width - width) // 2,
            box.y + (box.height - height) // 2), image)
        self.assertEqual(image.payload[:4], b"FXI1")
        found, cached = controller.preview_cache.lookup(cache_key)
        self.assertTrue(found)
        self.assertEqual(cached["blob"][:4], b"FXI1")
        found, browser_blob = controller._cached_file_preview(
            controller.selected_file)
        self.assertTrue(found)
        self.assertEqual(browser_blob, cached["blob"])

    def test_preview_uses_theme_colors_and_fills_from_bottom(self):
        with PreviewHelper():
            path = _write_gcode()
            try:
                worker = SynchronousPreviewWorker()
                controller = _controller(path, worker)
                controller.print_stats.status["info"] = {
                    "current_layer": 1, "total_layer": 2,
                }
                controller._render_print_page()
                controller.reactor.flush_callbacks()
            finally:
                pathlib.Path(path).unlink()

        images = [
            command for batch in controller.batches for command in batch
            if command.startswith("--batch image ")
        ]
        # Completion paints the pair once, after the initial loading frame.
        self.assertEqual(len(images), 2)
        images = images[-2:]
        pending = decode_fxi1(images[0].payload)
        printed = decode_fxi1(images[1].payload)
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

    def test_preparation_keeps_full_preview_until_print_started(self):
        controller = _controller("/data/model.gcode")
        controller.start_print_macro = type("StartMacro", (), {
            "variables": {"print_started": False}})()
        controller.print_stats.status["info"] = {
            "current_layer": 0, "total_layer": 100}
        image = decode_fxi1(_preview_fixture_blob())
        image["bounds"] = (18, 20)
        for state in (PrintState.PREPARING, PrintState.PRINTING):
            controller.print_state = state
            _key, layers, pending, printed = controller._gcode_preview_render_spec(
                controller.print_stats.status)
            self.assertIsNone(layers)
            self.assertEqual(colorize_preview(image, layers, pending, printed),
                             colorize_preview(image, None, printed, printed))

        controller.start_print_macro.variables["print_started"] = True
        _key, layers, pending, printed = controller._gcode_preview_render_spec(
            controller.print_stats.status)
        self.assertEqual(layers[2], 0.0)
        self.assertEqual(colorize_preview(image, layers, pending, printed),
                         colorize_preview(image, None, pending, pending))

    def test_restored_preview_uses_existing_progress_until_layers_available(self):
        controller = _controller("/data/model.gcode")
        controller.resurrection = StatusObject({"restored": True})
        controller._progress_start = (0.0, 0.0)
        controller.print_stats.status["info"] = {}
        image = decode_fxi1(_preview_fixture_blob())
        image["bounds"] = (18, 20)
        for state in (PrintState.PRINTING, PrintState.PAUSED):
            controller.print_state = state
            _key, layers, pending, printed = controller._gcode_preview_render_spec(
                controller.print_stats.status)
            self.assertEqual(layers[2], 0.25)
            blobs = colorize_preview(image, layers, pending, printed)
            self.assertEqual(_foreground_rows(blobs[-1]), {20})

        controller.print_stats.status["info"] = {
            "current_layer": 1, "total_layer": 2}
        self.assertEqual(controller._gcode_preview_render_spec(
            controller.print_stats.status)[1][2], 0.5)

        controller.resurrection.status["restored"] = False
        controller.print_stats.status["info"] = {}
        self.assertIsNone(controller._gcode_preview_render_spec(
            controller.print_stats.status)[1])

    def test_layer_change_recolors_cached_mask_once(self):
        with PreviewHelper():
            path = _write_gcode()
            try:
                initial_worker = SynchronousPreviewWorker()
                controller = _controller(path, initial_worker)
                controller.print_stats.status["info"] = {
                    "current_layer": 1, "total_layer": 2,
                }
                controller._render_print_page()
                controller.reactor.flush_callbacks()
                worker = DeferredPreviewWorker()
                controller.file_worker = worker
                before = len(controller.batches)
                controller.print_stats.status["info"] = {
                    "current_layer": 2, "total_layer": 2,
                }
                with mock.patch.object(
                        PAGES, "colorize_preview",
                        wraps=PAGES.colorize_preview) as colorize:
                    controller._update_print_progress(104)
                    self.assertEqual(worker.submitted, [])

                    controller.reactor.now = 105
                    controller._update_print_progress(105)
                    controller._update_print_progress(106)
                    self.assertEqual(len(worker.submitted), 1)
                    colorize.assert_not_called()
                    self.assertFalse(any(
                        command.startswith("--batch image ")
                        for batch in controller.batches[before:]
                        for command in batch))

                    worker.finish(0)
                    colorize.assert_called_once()
            finally:
                pathlib.Path(path).unlink()

        new_images = [
            command for batch in controller.batches[before:]
            for command in batch if command.startswith("--batch image ")
        ]
        self.assertEqual(len(new_images), 1)
        self.assertEqual(_foreground_rows(new_images[0].payload), {18, 19, 20})

    def test_absent_preview_is_remembered_without_resubmission(self):
        with PreviewHelper():
            path = _write_gcode("no-preview-")
            try:
                worker = SynchronousPreviewWorker()
                controller = _controller(path, worker)
                controller._render_print_page()
                controller.reactor.flush_callbacks()
                controller._render_print_page()
                controller.reactor.flush_callbacks()
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
                controller.reactor.flush_callbacks()
                controller.virtual_sdcard.path = second
                controller._prepare_gcode_preview()
                controller.reactor.flush_callbacks()
                second_key = controller._gcode_preview["key"]
                worker.finish(0)
            finally:
                pathlib.Path(first).unlink()
                pathlib.Path(second).unlink()

        self.assertEqual(controller._gcode_preview["key"], second_key)
        self.assertEqual(controller._gcode_preview["status"], "loading")

    def test_previous_run_callback_cannot_replace_same_path_request(self):
        with PreviewHelper():
            path = _write_gcode()
            try:
                worker = DeferredPreviewWorker()
                controller = _controller(path, worker)
                controller._prepare_gcode_preview()
                controller.reactor.flush_callbacks()
                first_preview = controller._gcode_preview
                controller._cancel_gcode_preview()
                controller._prepare_gcode_preview()
                controller.reactor.flush_callbacks()
                current_preview = controller._gcode_preview
                worker.finish(0)
            finally:
                pathlib.Path(path).unlink()

        self.assertIsNot(first_preview, current_preview)
        self.assertTrue(first_preview["cancel"].is_set())
        self.assertIs(controller._gcode_preview, current_preview)
        self.assertEqual(current_preview["status"], "loading")


if __name__ == "__main__":
    unittest.main()
