## Live model view, camera gestures and print navigation contracts.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import pathlib
import shlex
import tempfile
import unittest
from unittest import mock

from tests.test_feather_model_preview import Controller, PAGE, Session
from tests.test_feather_screen import FEATHER, StatusObject
from feather.screen.pages.printing import PrintingPagesMixin
from feather.screen.pages.files import FileBrowserPagesMixin
from feather.files import FileEntry, FileTaskSuperseded
from feather.features.manager import LazyFeatureManager
from feather.screen.model_preview import ModelPreviewEntryMixin
from ff5m_ui.model_preview.actions import PreviewAction, PreviewRoute
from ff5m_ui.printing import runtime as PRINTING
from ff5m_ui.print_state import PrintState
from ff5m_ui.screen import ScreenPage
from timelapse_state import TimelapsePhase
from ui import Rect, RenderBatchQueue, ScreenRoot
from ui.render_receipts import RenderReceipt
from tests.feather_render_test_helper import full_render_queue
from feather.screen.composition import PaintSurface
from ff5m_ui.keys import AppPage


class LiveController(Controller, PrintingPagesMixin):
    _show_page = FEATHER.FeatherScreen._show_page
    _go_back = FEATHER.FeatherScreen._go_back
    _handle_continuous_touch = FEATHER.FeatherScreen._handle_continuous_touch
    _process_touch_events = FEATHER.FeatherScreen._process_touch_events
    _change_print_state = FEATHER.FeatherScreen._change_print_state
    page_for_print_state = FEATHER.FeatherScreen.page_for_print_state
    _observed_print_state = FEATHER.FeatherScreen._observed_print_state
    _clock_duration = staticmethod(FEATHER.FeatherScreen._clock_duration)

    def __init__(self):
        super().__init__()
        self.page, self.print_state = ScreenPage.PRINTING, PrintState.PRINTING
        self.print_stats = StatusObject({"state": "printing", "print_duration": 100,
                                        "info": {"current_layer": 2, "total_layer": 10}})
        self.virtual_sdcard = StatusObject({"progress": 0.25})
        self.path = "/data/part.gcode"
        self.virtual_sdcard.file_path = lambda: self.path
        self.virtual_sdcard.estimate_print_time = 400
        self.toolhead = StatusObject({"position": (0, 0, 3.25, 0)})
        self.pending_action, self.command_depth, self.debug = None, 0, False
        self.joystick_suppressed = None
        self.joystick = mock.Mock()
        for name in ("_run_script", "_run_immediate_command", "_toast", "_cancel_file_preview",
                     "_cancel_gcode_preview", "_apply_safety_visibility", "_notify_features", "_show_print_result"):
            setattr(self, name, mock.Mock())
        self._wake_if_dimmed = mock.Mock(return_value=False)
        self._current_dialog = mock.Mock(return_value=None)
        self._blocking_operation_active = mock.Mock(return_value=False)
        self._print_controls_ready = lambda: self.print_state != PrintState.PREPARING
        self._restored_print_active = lambda _now: False
        self._timelapse_phase = lambda: TimelapsePhase.NONE
        self._display_status_text = lambda _now: self.print_state.name

    def _render_screen(self, feature=None):
        if self.page in PAGE.PREVIEW_PAGES:
            self._render_model_preview()
        else:
            self.renderer.send(self.renderer.begin_page(self.page.name))


class LazyLiveController(ModelPreviewEntryMixin, LiveController):
    def __init__(self):
        super().__init__()
        self.feature_manager = LazyFeatureManager(self, FEATHER.FEATURE_SPECS)

    def _model_preview_frame_tick(self, now):
        return self.feature_manager.peek("model_preview")._model_preview_frame_tick(now)


class PrintModelPreviewTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("feather.model_preview.ModelPreview", Session)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.controller = LazyLiveController()
        self.controller._open_print_model_preview()
        self.state = self.controller.model_preview
        self.initial_tick = self.state["render_timer"][1]

    def tick(self, now, finish=True):
        self.controller.reactor.now = now
        count = len(self.controller.file_worker.submitted)
        next_tick = self.controller._model_preview_frame_tick(now)
        if finish and len(self.controller.file_worker.submitted) > count:
            self.controller.file_worker.finish(count)
        return next_tick

    def track(self, route):
        ref = (PAGE.common_ui.PreviewRef.YAW_PAD if route == PreviewRoute.YAW_JOYSTICK
               else PAGE.common_ui.PreviewRef.TILT_PAD)
        page = self.state["page_tree"]
        return page.node(ref).track_bounds(page.rect(ref))

    def touch(self, route, phase, coordinate=None, now=None, acknowledge=True):
        x, y = coordinate or self.track(route).center
        if now is not None:
            self.controller.reactor.now = now
        wire = self.controller.renderer._wire_action(PreviewAction(route))
        self.controller._handle_continuous_touch("touch %s %s %d %d" % (wire, phase, x, y))
        if acknowledge:
            self.acknowledge_cursor()

    def acknowledge_cursor(self):
        for batch in reversed(self.controller.renderer.capture.submitted):
            if batch.receipt and batch.receipt.startswith("mpc:"):
                self.controller._model_preview_render_receipt(RenderReceipt(batch.receipt, True, 10, 5, 5))
                return

    def test_drag_does_not_flood_display_queue_or_cancel_the_active_gesture(self):
        root = ScreenRoot(self.controller.renderer,
                          PaintSurface(self.controller._render_model_preview, accepts_deltas=lambda: True),
                          Rect(0, 0, 800, 480), AppPage.SCREEN_ROOT, chrome=False)
        self.assertTrue(root.paint())
        queue = self.controller.renderer._batch_queue = RenderBatchQueue(max_batches=2)
        redraw = mock.Mock(side_effect=lambda: root.paint(force=True))
        self.controller.renderer.configure_worker(None, None, redraw_handler=redraw)
        self.tick(self.initial_tick, finish=False)
        track = self.track(PreviewRoute.YAW_JOYSTICK)
        self.touch(PreviewRoute.YAW_JOYSTICK, "begin", (track.x, track.center_y), acknowledge=False)
        for index in range(1, 61):
            self.touch(PreviewRoute.YAW_JOYSTICK, "move",
                       (track.x + round(track.width * index / 60), track.center_y), acknowledge=False)
        self.assertEqual(self.state["yaw"], 150)
        self.assertEqual(self.state["touch_action"], "model.yaw_joystick")
        self.assertEqual(queue.snapshot()["queue_depth"], 1)
        redraw.assert_not_called()
        self.controller.file_worker.finish(0)
        self.tick(self.controller.reactor.now)
        self.assertEqual(self.state["session"].frames[-1][0], 150)

    def test_display_receipts_send_latest_drag_position_at_100_fps(self):
        track = self.track(PreviewRoute.YAW_JOYSTICK)
        self.touch(PreviewRoute.YAW_JOYSTICK, "begin", (track.x, track.center_y), acknowledge=False)
        batch = self.controller.renderer.capture.submitted[-1]
        self.assertIsNotNone(batch.receipt)
        for frame in range(1, 11):
            count = len(self.controller.renderer.capture.submitted)
            for sample in range(1, 21):
                self.touch(PreviewRoute.YAW_JOYSTICK, "move",
                           (track.x + frame * 20 + sample, track.center_y),
                           now=100 + frame / 100 + sample / 100000, acknowledge=False)
            self.assertEqual(len(self.controller.renderer.capture.submitted), count)
            self.controller._model_preview_render_receipt(RenderReceipt(batch.receipt, True, 10000, 8000, 2000))
            self.assertEqual(len(self.controller.renderer.capture.submitted), count + 1)
            batch = self.controller.renderer.capture.submitted[-1]
            knob = self.knobs()[0]
            self.assertEqual(knob.x + knob.width // 2, track.x + frame * 20 + 20)
        self.touch(PreviewRoute.YAW_JOYSTICK, "end", (track.right, track.center_y), acknowledge=False)
        self.assertIsNone(self.state["touch_action"])
        self.controller._model_preview_render_receipt(RenderReceipt(batch.receipt, True, 10000, 8000, 2000))
        self.assertEqual(self.knobs()[0].right, track.right)

    def test_failed_or_missing_cursor_receipt_rebuilds_latest_view_and_keeps_drag(self):
        for failure in ("failed", "missing"):
            with self.subTest(failure=failure):
                self.controller._open_print_model_preview()
                self.state = self.controller.model_preview
                track = self.track(PreviewRoute.YAW_JOYSTICK)
                self.touch(PreviewRoute.YAW_JOYSTICK, "begin", (track.x, track.center_y), acknowledge=False)
                receipt = self.controller.renderer.capture.submitted[-1].receipt
                self.touch(PreviewRoute.YAW_JOYSTICK, "move", (track.right, track.center_y), acknowledge=False)
                if failure == "failed":
                    self.controller._model_preview_render_receipt(RenderReceipt(receipt, False, 10, 5, 5))
                else:
                    self.controller.reactor.now += 1.01
                    self.controller._loaded_model_preview()._model_preview_monitor_tick(self.controller.reactor.now)
                self.assertEqual(self.knobs()[0].right, track.right)
                self.assertEqual(self.state["touch_action"], "model.yaw_joystick")
                self.touch(PreviewRoute.YAW_JOYSTICK, "move", track.center, acknowledge=False)
                pending = self.controller.renderer.capture.submitted[-1].receipt
                count = len(self.controller.renderer.capture.submitted)
                self.controller._model_preview_render_receipt(RenderReceipt(receipt, True, 10, 5, 5))
                self.assertEqual(len(self.controller.renderer.capture.submitted), count)
                self.assertEqual(self.state["cursor_receipt"].pending.token, pending)
                self.acknowledge_cursor()
                self.assertEqual(self.state["display_frames"], 0)

    def test_rejected_cursor_and_recovery_retry_after_queue_accepts_output(self):
        track = self.track(PreviewRoute.YAW_JOYSTICK)
        with full_render_queue(self.controller.renderer):
            self.touch(PreviewRoute.YAW_JOYSTICK, "begin", (track.right, track.center_y), acknowledge=False)
        self.assertEqual(self.state["touch_action"], "model.yaw_joystick")
        self.controller._loaded_model_preview()._model_preview_monitor_tick(self.controller.reactor.now)
        self.assertEqual(self.knobs()[0].right, track.right)
        self.touch(PreviewRoute.YAW_JOYSTICK, "move", track.center, acknowledge=False)
        self.assertIsNotNone(self.controller.renderer.capture.submitted[-1].receipt)

    def test_late_cursor_receipt_cannot_paint_reopened_preview(self):
        track = self.track(PreviewRoute.YAW_JOYSTICK)
        self.touch(PreviewRoute.YAW_JOYSTICK, "begin", (track.x, track.center_y), acknowledge=False)
        receipt = self.controller.renderer.capture.submitted[-1].receipt
        old = self.state
        self.touch(PreviewRoute.YAW_JOYSTICK, "move", (track.right, track.center_y), acknowledge=False)
        self.controller._open_print_model_preview()
        self.state = self.controller.model_preview
        self.assertIsNone(old["cursor_receipt"].pending)
        count = len(self.controller.renderer.capture.submitted)
        self.controller._model_preview_render_receipt(RenderReceipt(receipt, True, 10, 5, 5))
        self.assertEqual(len(self.controller.renderer.capture.submitted), count)
        self.assertEqual(self.state["yaw"], 0)

    def test_print_page_keeps_preview_panel_without_extra_header_button(self):
        self.controller._go_back()
        self.controller.renderer.send(PRINTING.render(self.controller.renderer, {}))
        self.assertFalse(self.controller.renderer.capture.latest.has_action("print.preview"))
        self.assertTrue(self.controller.renderer.capture.latest.has_action("print.more"))

    def test_current_print_preview_back_ignores_file_navigation_preference(self):
        self.controller._setting = FEATHER.FeatherScreen._setting.__get__(self.controller)
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                self.controller.params.variables = {"feather_preview_back_to_files": enabled}
                self.controller._open_print_model_preview()
                state = self.controller.model_preview
                self.controller._go_back()
                self.assertEqual(self.controller.page, ScreenPage.PRINTING)
                self.assertIsNone(self.controller.model_preview)
                self.assertTrue(state["cancel"].is_set())

    def test_current_file_preview_remains_available_without_idle_or_motion_commands(self):
        self.controller._go_back()
        self.controller._require_idle = mock.Mock(side_effect=RuntimeError("not idle"))
        self.controller._open_print_model_preview()
        self.assertEqual(self.controller.page, ScreenPage.PRINT_PREVIEW)
        self.assertEqual(self.controller.model_preview["path"], self.controller.path)
        self.controller._require_idle.assert_not_called()
        self.controller._run_script.assert_not_called()
        self.controller._run_immediate_command.assert_not_called()

    def test_preview_uses_shared_layout_without_mode_selector(self):
        page, refs = self.state["page_tree"], PAGE.common_ui.PreviewRef
        image, info = page.rect(refs.IMAGE), page.rect(refs.INFO)
        yaw, tilt = page.rect(refs.YAW_PAD), page.rect(refs.TILT_PAD)
        frame = self.controller.renderer.capture.latest
        self.assertLess(info.right, image.x)
        self.assertGreater(image.width, PAGE._preview_rect()[2])
        self.assertGreater(image.height, PAGE._preview_rect()[3])
        self.assertGreater(yaw.y, image.bottom)
        self.assertGreater(tilt.x, image.right)
        self.assertEqual((image.width, image.height), (368, 248))
        self.assertTrue(info.contains(page.rect(refs.SCALE)))
        self.assertFalse(frame.has_action("model.print"))
        for index in range(5):
            self.assertFalse(frame.has_action("model.style.%d" % index))
        for action in ("scale_up", "scale_down", "yaw_joystick", "pitch_joystick"):
            self.assertTrue(frame.has_action("model." + action))

    def test_preview_and_motion_z_use_the_same_complete_slider(self):
        from ui import Slider
        from ff5m_ui.move.joystick.page import PAGE as joystick_page

        refs = PAGE.common_ui.PreviewRef
        controls = [self.state["page_tree"].node(ref) for ref in (refs.YAW_PAD, refs.TILT_PAD)]
        controls.append(joystick_page.node("z.panel"))
        self.assertTrue(all(type(control) is Slider for control in controls))
        frame = self.controller.renderer.capture.latest
        for ref in (refs.YAW_PAD, refs.TILT_PAD):
            box = self.state["page_tree"].rect(ref)
            knob = next(shape.bounds for shape in frame.shapes if shape.kind == "stroke"
                        and shape.bounds.width == shape.bounds.height == 25 and box.contains(shape.bounds))
            bars = [shape for shape in frame.shapes if shape.kind == "fill"
                    and shape.bounds.width == 13 and shape.bounds.height == 1
                    and knob.x <= shape.bounds.x < shape.bounds.right <= knob.right
                    and knob.y <= shape.bounds.y < shape.bounds.bottom <= knob.bottom]
            self.assertEqual(len(bars), 2)

    def test_rotate_rail_and_endpoint_knob_align_with_preview_frame(self):
        page, refs = self.state["page_tree"], PAGE.common_ui.PreviewRef
        image = page.rect(refs.IMAGE)
        frame = self.controller.renderer.capture.latest
        panel = min((shape.bounds for shape in frame.shapes
                     if shape.kind == "stroke" and shape.bounds.x <= image.x
                     and shape.bounds.y <= image.y and image.right <= shape.bounds.right
                     and image.bottom <= shape.bounds.bottom),
                    key=lambda bounds: bounds.width * bounds.height)
        track = self.track(PreviewRoute.YAW_JOYSTICK)
        self.assertEqual((track.x, track.right), (panel.x, panel.right))
        for x, expected, edge in ((track.x, -150.0, "left"), (track.right, 150.0, "right")):
            self.touch(PreviewRoute.YAW_JOYSTICK, "begin", (x, track.center_y))
            self.touch(PreviewRoute.YAW_JOYSTICK, "end", (x, track.center_y))
            self.assertEqual(self.state["yaw"], expected)
            frame = self.controller.renderer.capture.latest
            knob = next(shape.bounds for shape in frame.shapes if shape.kind == "stroke"
                        and shape.bounds.width == shape.bounds.height == 25)
            self.assertTrue(page.rect(refs.YAW_PAD).contains(knob))
            self.assertEqual(knob.x if edge == "left" else knob.right,
                             panel.x if edge == "left" else panel.right)

    def test_model_follows_layers_and_updates_live_information(self):
        next_tick = self.tick(self.initial_tick)
        session = self.state["session"]
        self.assertEqual(session.frames[-1][1], round(57 * 0.2))
        image = self.state["page_tree"].rect(PAGE.common_ui.PreviewRef.IMAGE)
        self.assertEqual(session.viewport, (image.width, image.height))
        self.controller.print_stats.status["info"]["current_layer"] = 5
        self.controller.print_stats.status["print_duration"] = 200
        self.controller._update_print_model_preview(next_tick)
        frame = self.controller.renderer.capture.latest
        self.assertTrue(frame.has_text("50%"))
        self.assertTrue(frame.has_text("5 / 10"))
        self.tick(next_tick)
        self.assertEqual(session.frames[-1][1], round(57 * 0.5))
        self.assertEqual(self.controller.print_state, PrintState.PRINTING)

    def test_joystick_rail_repairs_stay_centered_when_knobs_move(self):
        for route in (PreviewRoute.YAW_JOYSTICK, PreviewRoute.PITCH_JOYSTICK):
            vertical = route == PreviewRoute.PITCH_JOYSTICK
            box = self.track(route)
            positions = ((box.center_x, box.y), (box.center_x, box.bottom)) if vertical else (
                (box.x, box.center_y), (box.right, box.center_y))
            for phase, position in ((None, None), ("begin", positions[0]),
                                    ("move", positions[1]), ("end", positions[1])):
                with self.subTest(route=route, phase=phase):
                    if phase is None:
                        self.controller._render_model_preview()
                    else:
                        self.touch(route, phase, position)
                    shapes = self.controller.renderer.capture.latest.shapes
                    rails = [shape.bounds for shape in shapes if shape.kind == "fill" and (
                        box.y <= shape.bounds.y < shape.bounds.bottom <= box.bottom
                        and shape.bounds.width == 2
                        and shape.bounds.x <= box.center_x < shape.bounds.right if vertical else
                        box.x <= shape.bounds.x < shape.bounds.right <= box.right
                        and shape.bounds.height == 2
                        and shape.bounds.y <= box.center_y < shape.bounds.bottom)]
                    self.assertEqual(len(rails), 1)
                    rail = rails[0]
                    self.assertEqual((rail.x + rail.right) // 2 if vertical else
                                     (rail.y + rail.bottom) // 2,
                                     box.center_x if vertical else box.center_y)
                    knobs = [shape.bounds for shape in shapes if shape.kind == "stroke"
                             and shape.bounds.width == shape.bounds.height
                             and box.x <= shape.bounds.x < shape.bounds.right <= box.right
                             and box.y <= shape.bounds.y < shape.bounds.bottom <= box.bottom]
                    self.assertEqual(len(knobs), 1)
                    knob = knobs[0]
                    self.assertEqual((knob.x + knob.right) // 2 if vertical else
                                     (knob.y + knob.bottom) // 2,
                                     box.center_x if vertical else box.center_y)

    def knobs(self):
        frames = self.controller.renderer.capture.frames
        result = []
        for route in (PreviewRoute.YAW_JOYSTICK, PreviewRoute.PITCH_JOYSTICK):
            box = self.track(route)
            result.append(next(shape.bounds for frame in reversed(frames) for shape in frame.shapes if shape.kind == "stroke"
                               and shape.bounds.width == shape.bounds.height
                               and box.x <= shape.bounds.x < shape.bounds.right <= box.right
                               and box.y <= shape.bounds.y < shape.bounds.bottom <= box.bottom))
        return result

    def test_yaw_sets_absolute_position_and_stays_there_after_release(self):
        box = self.track(PreviewRoute.YAW_JOYSTICK)
        for position, angle in (((box.x, box.center_y), -150), (box.center, 0),
                                ((box.right, box.center_y), 150)):
            with self.subTest(angle=angle):
                self.touch(PreviewRoute.YAW_JOYSTICK, "begin", position)
                self.assertEqual(self.state["yaw"], angle)
                self.touch(PreviewRoute.YAW_JOYSTICK, "end", position)
                next_tick = self.tick(self.state["frame_at"])
                self.assertEqual(self.state["session"].frames[-1][0], angle)
                self.tick(next_tick + 1)
                self.assertEqual(self.state["yaw"], angle)
                self.assertEqual(self.state["pitch"], 30)
        self.assertFalse(self.controller.joystick.method_calls)
        self.controller._run_script.assert_not_called()

    def test_static_view_does_not_redraw_until_a_physical_layer_changes(self):
        next_tick = self.tick(self.initial_tick)
        session = self.state["session"]
        self.controller.print_stats.status["info"]["current_layer"] = 2.001
        self.tick(next_tick)
        self.assertEqual(len(session.frames), 1)

    def test_tilt_maps_above_horizon_and_below_without_changing_yaw(self):
        box = self.track(PreviewRoute.PITCH_JOYSTICK)
        for position, angle in (((box.center_x, box.y), 70), (box.center, 0),
                                ((box.center_x, box.bottom), -70)):
            self.touch(PreviewRoute.PITCH_JOYSTICK, "begin", position)
            self.assertEqual(self.state["pitch"], angle)
            self.touch(PreviewRoute.PITCH_JOYSTICK, "end", position)
            self.tick(self.state["frame_at"])
            self.assertEqual(self.state["session"].pitches[-1], angle)
            self.assertEqual(self.state["yaw"], 0)

    def test_lost_release_preserves_camera_and_both_knob_positions(self):
        box = self.track(PreviewRoute.YAW_JOYSTICK)
        self.touch(PreviewRoute.YAW_JOYSTICK, "begin", (box.x, box.center_y))
        previous = self.knobs()
        self.tick(102)
        self.assertIsNone(self.state["touch_action"])
        self.assertEqual(self.state["yaw"], -150)
        self.controller._render_model_preview()
        self.assertEqual(self.knobs(), previous)

    def test_queued_camera_moves_leave_the_same_pixels_as_a_full_redraw(self):
        def paint(pixels, commands, area):
            for command in commands:
                tokens = shlex.split(command)
                if tokens[:2] not in (["--batch", "fill"], ["--batch", "stroke"]):
                    continue
                position, size = tokens.index("-p") + 1, tokens.index("-s") + 1
                x, y = map(int, tokens[position:position + 2])
                width, height = map(int, tokens[size:size + 2])
                color = tokens[tokens.index("-c") + 1]
                line = int(tokens[tokens.index("-lw") + 1]) if tokens[1] == "stroke" else None
                for py in range(max(y, area.y), min(y + height, area.bottom)):
                    for px in range(max(x, area.x), min(x + width, area.right)):
                        if line is None or min(px - x, x + width - 1 - px, py - y, y + height - 1 - py) < line:
                            pixels[(px, py)] = color

        for capacity in (16, 2):
            with self.subTest(queue_capacity=capacity):
                self.controller = LazyLiveController()
                self.controller._open_print_model_preview()
                self.state = self.controller.model_preview
                refs = PAGE.common_ui.PreviewRef
                areas = [self.state["page_tree"].rect(ref) for ref in (refs.YAW_PAD, refs.TILT_PAD)]
                pixels = [{}, {}]
                for canvas, area in zip(pixels, areas):
                    paint(canvas, self.controller.renderer.capture.batches[-1], area)
                queue = self.controller.renderer._batch_queue = RenderBatchQueue(max_batches=capacity)
                self.controller.renderer.configure_worker(
                    None, None, redraw_handler=self.controller._render_model_preview)
                for route in (PreviewRoute.YAW_JOYSTICK, PreviewRoute.PITCH_JOYSTICK):
                    track = self.track(route)
                    positions = ((track.x, track.center_y), (track.right, track.center_y), track.center) if (
                        route == PreviewRoute.YAW_JOYSTICK) else (
                        (track.center_x, track.y), (track.center_x, track.bottom), track.center)
                    for phase, position in zip(("begin", "move", "end"), positions):
                        self.touch(route, phase, position, acknowledge=False)
                while (batch := queue.get(timeout=0)) is not None:
                    for canvas, area in zip(pixels, areas):
                        paint(canvas, batch.commands, area)
                    if batch.receipt:
                        self.controller._model_preview_render_receipt(RenderReceipt(batch.receipt, True, 10, 5, 5))
                page = self.state["page_tree"]
                expected_commands = page.draw(self.controller.renderer, page.state)
                for canvas, area in zip(pixels, areas):
                    expected = {}
                    paint(expected, expected_commands, area)
                    self.assertTrue(canvas == expected, "Queued slider pixels differ from a full redraw")

    def test_default_camera_open_reset_and_reopen_restore_original_view(self):
        initial = self.knobs()
        self.assertEqual((self.state["yaw"], self.state["pitch"]), (0, 30))
        yaw, tilt = initial
        self.assertEqual(yaw.x + yaw.width // 2, self.track(PreviewRoute.YAW_JOYSTICK).center_x)
        self.assertLess(tilt.y + tilt.height // 2, self.track(PreviewRoute.PITCH_JOYSTICK).center_y)
        for route in (PreviewRoute.YAW_JOYSTICK, PreviewRoute.PITCH_JOYSTICK):
            self.touch(route, "begin")
            self.touch(route, "end")
        self.controller._handle_model_preview_action("model.reset")
        self.assertEqual(self.knobs(), initial)
        self.tick(self.state["frame_at"])
        self.assertEqual(self.state["session"].frames[-1][0], 0)
        self.assertEqual(self.state["session"].pitches[-1], 30)
        self.controller._open_print_model_preview()
        self.state = self.controller.model_preview
        self.assertEqual(self.knobs(), initial)

    def test_initial_camera_and_knobs_use_saved_angles_and_redraw_preserves_them(self):
        settings = {"feather_preview_yaw": -90, "feather_preview_pitch": -30}
        self.controller._setting = lambda name, default=None: settings.get(name, default)
        self.controller._open_print_model_preview()
        self.state = self.controller.model_preview
        self.initial_tick = self.state["frame_at"]
        yaw, tilt = self.knobs()
        self.assertLess(yaw.x + yaw.width // 2, self.track(PreviewRoute.YAW_JOYSTICK).center_x)
        self.assertGreater(tilt.y + tilt.height // 2, self.track(PreviewRoute.PITCH_JOYSTICK).center_y)
        self.tick(self.initial_tick)
        self.assertEqual(self.state["session"].frames[-1][0], -90)
        self.assertEqual(self.state["session"].pitches[-1], -30)
        self.controller._handle_model_preview_action("model.style.0")
        self.assertEqual(self.knobs(), [yaw, tilt])

    def test_saved_angles_outside_slider_limits_start_and_reset_at_the_rail_ends(self):
        for sign in (-1, 1):
            with self.subTest(sign=sign):
                settings = {"feather_preview_yaw": sign * 180, "feather_preview_pitch": sign * 90}
                self.controller._setting = lambda name, default=None: settings.get(name, default)
                self.controller._open_print_model_preview()
                self.state = self.controller.model_preview
                initial_knobs = self.knobs()
                yaw, tilt = self.track(PreviewRoute.YAW_JOYSTICK), self.track(PreviewRoute.PITCH_JOYSTICK)
                self.touch(PreviewRoute.YAW_JOYSTICK, "begin",
                           (yaw.right if sign > 0 else yaw.x, yaw.center_y))
                self.touch(PreviewRoute.PITCH_JOYSTICK, "begin",
                           (tilt.center_x, tilt.y if sign > 0 else tilt.bottom))
                self.assertEqual(self.knobs(), initial_knobs)
                self.tick(self.state["frame_at"])
                self.assertEqual(self.state["session"].frames[-1][0], sign * 150)
                self.assertEqual(self.state["session"].pitches[-1], sign * 70)
                self.controller._handle_model_preview_action("model.reset")
                self.assertEqual(self.state["yaw"], sign * 150)
                self.assertEqual(self.state["pitch"], sign * 70)
                self.assertEqual(self.knobs(), initial_knobs)

    def test_busy_render_coalesces_gestures_and_requests_the_latest_position(self):
        self.tick(self.initial_tick, finish=False)
        yaw_box, tilt_box = self.track(PreviewRoute.YAW_JOYSTICK), self.track(PreviewRoute.PITCH_JOYSTICK)
        self.touch(PreviewRoute.YAW_JOYSTICK, "begin", (yaw_box.x, yaw_box.center_y))
        self.touch(PreviewRoute.YAW_JOYSTICK, "move", (yaw_box.right, yaw_box.center_y))
        self.touch(PreviewRoute.YAW_JOYSTICK, "end", yaw_box.center)
        self.touch(PreviewRoute.PITCH_JOYSTICK, "begin", (tilt_box.center_x, tilt_box.y))
        self.touch(PreviewRoute.PITCH_JOYSTICK, "end", (tilt_box.center_x, tilt_box.bottom))
        self.assertEqual(len(self.controller.file_worker.submitted), 1)
        self.assertEqual(self.tick(102, finish=False), self.controller.reactor.NEVER)
        self.controller.file_worker.finish(0)
        self.tick(102)
        session = self.state["session"]
        self.assertEqual(session.frames, [(0, round(57 * .2)), (0, round(57 * .2))])
        self.assertEqual(session.pitches, [30, -70])
        self.tick(103)
        self.assertEqual(len(session.frames), 2)

    def test_slider_requests_a_frame_immediately_when_worker_is_free(self):
        self.controller._setting = lambda name, default=None: 1 if name == "feather_preview_fps" else default
        self.controller._open_print_model_preview()
        self.state = self.controller.model_preview
        initial = self.state["frame_at"]
        self.tick(initial)
        now = initial + .01
        box = self.track(PreviewRoute.YAW_JOYSTICK)
        self.touch(PreviewRoute.YAW_JOYSTICK, "begin", (box.right, box.center_y), now=now)
        timer, deadline = self.controller.reactor.updates[-1]
        self.assertIs(timer, self.state["render_timer"])
        self.assertEqual(deadline, now)
        self.tick(now)
        self.assertEqual([yaw for yaw, _layers in self.state["session"].frames], [0, 150])

    def read_touch_events(self, payload):
        self.controller.renderer.event_fd = 7
        self.controller.event_partial = getattr(self.controller, "event_partial", "")
        with mock.patch("os.read", return_value=payload.encode("ascii")):
            self.controller._process_touch_events(self.controller.reactor.now)

    def test_fast_slider_burst_paints_only_latest_move_and_keeps_release(self):
        self.tick(self.initial_tick, finish=False)
        box = self.track(PreviewRoute.YAW_JOYSTICK)
        wire = self.controller.renderer._wire_action(PreviewAction(PreviewRoute.YAW_JOYSTICK))
        lines = ["touch %s begin %d %d" % (wire, box.x, box.center_y)]
        lines += ["touch %s move %d %d" % (wire, box.x + index % box.width, box.center_y)
                  for index in range(60)]
        lines.append("touch %s end %d %d" % (wire, box.right, box.center_y))
        paints = len(self.controller.renderer.capture.submitted)
        self.read_touch_events("\n".join(lines) + "\n")
        self.assertEqual(len(self.controller.renderer.capture.submitted) - paints, 1)
        self.acknowledge_cursor()
        self.assertEqual(self.knobs()[0].right, box.right)
        self.assertIsNone(self.state["touch_action"])
        self.assertEqual(self.state["yaw"], 150)
        self.assertEqual(len(self.controller.file_worker.submitted), 1)
        self.controller.file_worker.finish(0)
        self.tick(self.controller.reactor.now)
        self.assertEqual(self.state["session"].frames[-1][0], 150)

    def test_slider_bursts_preserve_receipt_and_gesture_order(self):
        yaw = self.controller.renderer._wire_action(PreviewAction(PreviewRoute.YAW_JOYSTICK))
        tilt = self.controller.renderer._wire_action(PreviewAction(PreviewRoute.PITCH_JOYSTICK))
        lines = ["touch %s begin 220 400" % yaw,
                 "touch %s move 230 400" % yaw,
                 "touch %s move 240 400" % yaw,
                 "render diagnostic ok 5 2 1",
                 "touch %s move 250 400" % yaw,
                 "touch %s move 260 400" % yaw,
                 "touch %s end 270 400" % yaw,
                 "touch %s begin 740 200" % tilt,
                 "touch %s move 740 210" % tilt,
                 "touch %s move 740 220" % tilt,
                 "touch %s end 740 230" % tilt]
        events = []
        self.controller._handle_continuous_touch = events.append
        self.controller._model_preview_render_receipt = lambda receipt: events.append(receipt.token)
        self.read_touch_events("\n".join(lines) + "\n")
        self.assertEqual(events, [lines[0], lines[2], "diagnostic", lines[5], lines[6],
                                  lines[7], lines[9], lines[10]])

    def test_slider_burst_keeps_malformed_and_stale_events_separate(self):
        wire = self.controller.renderer._wire_action(PreviewAction(PreviewRoute.YAW_JOYSTICK))
        stale = "0:model.yaw_joystick"
        lines = ["touch %s begin 220 400" % wire,
                 "touch %s move 230 400" % wire,
                 "touch %s move invalid 400" % wire,
                 "touch %s move 240 400" % stale,
                 "touch %s move 250 400" % wire,
                 "touch %s end 260 400" % wire]
        with mock.patch.object(self.controller, "_handle_continuous_touch",
                               wraps=self.controller._handle_continuous_touch) as handle, \
                self.assertLogs(level="WARNING"):
            self.read_touch_events("\n".join(lines) + "\n")
        self.assertEqual([call.args[0] for call in handle.call_args_list], lines)
        self.assertIsNone(self.state["touch_action"])

    def test_slider_coalescing_preserves_fragmented_final_position(self):
        wire = self.controller.renderer._wire_action(PreviewAction(PreviewRoute.YAW_JOYSTICK))
        box = self.track(PreviewRoute.YAW_JOYSTICK)
        self.read_touch_events("touch %s begin %d %d\ntouch %s move %d %d\ntouch %s end " % (
            wire, box.x, box.center_y, wire, box.center_x, box.center_y, wire))
        self.assertEqual(self.state["yaw"], 0)
        self.assertIsNotNone(self.state["touch_action"])
        self.read_touch_events("%d %d\n" % (box.right, box.center_y))
        self.assertEqual(self.state["yaw"], 150)
        self.assertIsNone(self.state["touch_action"])
        self.assertEqual(self.controller.event_partial, "")

    def test_each_completed_render_requests_latest_drag_position_without_waiting(self):
        self.controller._setting = lambda name, default=None: 1 if name == "feather_preview_fps" else default
        self.controller._open_print_model_preview()
        self.state = self.controller.model_preview
        initial = self.state["frame_at"]
        self.tick(initial, finish=False)
        box = self.track(PreviewRoute.YAW_JOYSTICK)
        self.touch(PreviewRoute.YAW_JOYSTICK, "begin", (box.right, box.center_y), now=initial + .001)
        self.touch(PreviewRoute.YAW_JOYSTICK, "move", (box.x, box.center_y), now=initial + .002)
        self.assertEqual(len(self.controller.file_worker.submitted), 1)
        self.controller.reactor.now = initial + .003
        self.controller.file_worker.finish(0)
        self.assertEqual(self.controller.reactor.updates[-1][1], initial + .003)
        self.tick(initial + .003, finish=False)
        self.assertEqual(len(self.controller.file_worker.submitted), 2)
        self.touch(PreviewRoute.YAW_JOYSTICK, "end", box.center, now=initial + .004)
        self.controller.reactor.now = initial + .005
        self.controller.file_worker.finish(1)
        self.assertEqual(self.controller.reactor.updates[-1][1], initial + .005)
        self.tick(initial + .005)
        self.assertEqual([yaw for yaw, _layers in self.state["session"].frames], [0, -150, 0])
        self.tick(self.state["frame_at"])
        self.assertEqual(len(self.state["session"].frames), 3)

    def test_wake_gesture_and_modal_input_do_not_rotate(self):
        self.controller._wake_if_dimmed.return_value = True
        box = self.track(PreviewRoute.YAW_JOYSTICK)
        for phase in ("begin", "move", "end"):
            self.touch(PreviewRoute.YAW_JOYSTICK, phase, (box.right, box.center_y))
        self.assertEqual(self.state["yaw"], 0)
        self.controller._wake_if_dimmed.return_value = False
        self.controller._current_dialog.return_value = object()
        self.touch(PreviewRoute.YAW_JOYSTICK, "begin", (box.right, box.center_y))
        self.assertEqual(self.state["yaw"], 0)
        self.assertIsNone(self.state["touch_action"])

    def test_pause_resume_keep_view_and_back_closes_it_for_current_print_state(self):
        self.tick(self.initial_tick)
        session = self.state["session"]
        self.controller.print_stats.status["state"] = "paused"
        self.controller._change_print_state(PrintState.PAUSED, "paused")
        self.assertEqual(self.controller.page, ScreenPage.PRINT_PREVIEW)
        self.controller.print_stats.status["state"] = "printing"
        self.controller._change_print_state(PrintState.PRINTING, "printing")
        self.assertIs(self.controller.model_preview["session"], session)
        self.controller.print_stats.status["state"] = "paused"
        self.controller._change_print_state(PrintState.PAUSED, "paused")
        self.controller._go_back()
        self.assertEqual(self.controller.page, ScreenPage.PAUSED)
        self.assertIsNone(self.controller.model_preview)
        self.assertTrue(session.cancelled)
        self.assertEqual(len(self.controller.reactor.removed), 2)

    def test_completion_closes_preview_before_showing_the_result(self):
        self.tick(self.initial_tick)
        session = self.state["session"]
        self.controller.print_stats.status["state"] = "complete"
        self.controller._change_print_state(PrintState.IDLE, "complete")
        self.assertEqual(self.controller.page, ScreenPage.IDLE_HOME)
        self.assertIsNone(self.controller.model_preview)
        self.assertTrue(session.cancelled)
        self.controller._show_print_result.assert_called_once_with("", "Print finished")

    def test_file_change_and_late_completion_cannot_paint_previous_job(self):
        self.tick(self.initial_tick, finish=False)
        self.controller.path = "/data/next.gcode"
        self.controller._update_print_model_preview(self.initial_tick + 0.1)
        self.assertEqual(self.controller.page, ScreenPage.PRINTING)
        batches = len(self.controller.renderer.batches)
        self.controller.file_worker.finish(0)
        self.assertEqual(len(self.controller.renderer.batches), batches)
        self.assertIsNone(self.controller.model_preview)

    def test_style_changes_keep_camera_scale_and_errors_show_retryable_diagnostics(self):
        self.tick(self.initial_tick)
        self.controller._handle_model_preview_action("model.scale_up")
        self.controller._handle_model_preview_action("model.style.0")
        self.assertEqual(self.state["scale"], 1.05)
        self.assertEqual(self.state["yaw"], 0)
        self.tick(self.initial_tick + 0.1, finish=False)
        _work, callback = self.controller.file_worker.submitted[-1]
        callback(None, RuntimeError("fixture render failed"))
        self.assertTrue(self.controller.renderer.capture.latest.has_text("fixture render failed"))
        self.controller._handle_model_preview_action("model.style.0")
        self.assertIsNone(self.state["error"])
        self.tick(self.initial_tick + 0.2)
        self.assertIsNotNone(self.state["blob"])


class FileController(LiveController, FileBrowserPagesMixin):
    _format_size = staticmethod(FEATHER.FeatherScreen._format_size)

    def _render_screen(self, feature=None):
        if self.page == ScreenPage.FILE_CONFIRM:
            self._render_file_confirm()
        else:
            super()._render_screen(feature)


class LazyFileController(ModelPreviewEntryMixin, FileController):
    def __init__(self):
        super().__init__()
        self.feature_manager = LazyFeatureManager(self, FEATHER.FEATURE_SPECS)

    def _model_preview_frame_tick(self, now):
        return self.feature_manager.peek("model_preview")._model_preview_frame_tick(now)


class SelectedFileModelPreviewTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("feather.model_preview.ModelPreview", Session)
        patcher.start()
        self.addCleanup(patcher.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = pathlib.Path(folder.name) / "part.gcode"
        self.path.write_text(";TIME:3600\n;LAYER_COUNT:10\n;model_size = 20,20,2\n"
                             ";filament used [g] = 3.5\n;filament_type = PLA\nG1 X20\n")
        self.controller = LazyFileController()
        self.controller.params.variables = {}
        self.controller._setting = FEATHER.FeatherScreen._setting.__get__(self.controller)
        self.controller.print_state = PrintState.IDLE
        self.controller.virtual_sdcard.sdcard_dirname = folder.name
        self.entry = FileEntry(self.path.name, str(self.path), size=self.path.stat().st_size)
        self.controller.selected_file = self.entry
        self.controller.file_confirm_rebuild_mesh = True
        self.controller.file_confirm_auto_mesh = True
        self.controller._show_page(ScreenPage.FILE_CONFIRM)

    def open(self):
        self.controller._handle_file_action("file.preview")
        return self.controller.model_preview

    def test_file_preview_keeps_raster_budget_and_moves_scale_into_information(self):
        state = self.open()
        page, refs = state["page_tree"], PAGE.common_ui.PreviewRef
        active_page = PAGE.print_preview_ui.create_page()
        active_page.draw(self.controller.renderer)
        image, active_image = page.rect(refs.IMAGE), active_page.rect(refs.IMAGE)
        info = page.rect(refs.INFO)
        self.assertEqual((image.width, image.height), (active_image.width, active_image.height))
        self.assertEqual(info.width, active_page.rect(refs.INFO).width)
        self.assertLess(info.right, image.x)
        frame = self.controller.renderer.capture.latest
        for action in ("model.scale_up", "model.scale_down"):
            self.assertTrue(info.contains(frame.button(action).bounds))
        for index in range(5):
            self.assertFalse(frame.has_action("model.style.%d" % index))
        self.assertTrue(info.contains(page.rect(refs.SCALE)))
        self.assertEqual(self.controller.print_state, PrintState.IDLE)

    def test_filename_spans_information_and_model_while_tilt_owns_the_full_column(self):
        page = self.open()["page_tree"]
        refs = PAGE.common_ui.PreviewRef
        filename, information, tilt = (page.rect(ref) for ref in (refs.FILENAME, refs.INFO, refs.TILT_PAD))
        self.assertEqual(filename.x, information.x)
        self.assertEqual(filename.y, tilt.y)
        self.assertEqual(tilt.bottom, information.bottom)
        self.assertLess(filename.bottom, information.y)
        self.assertEqual(tilt.x - filename.right, page.node(refs.ROOT).column_gap)
        self.assertEqual((page.rect(refs.IMAGE).width, page.rect(refs.IMAGE).height), (368, 248))

    def test_openings_own_independent_layout_nodes_and_preserve_the_designer_declaration(self):
        ui, refs = PAGE.print_preview_ui, PAGE.common_ui.PreviewRef
        first, second = ui.create_page(before_print=True), ui.create_page(before_print=True)
        first.node(refs.TILT_PAD).padding(right=10)
        self.assertEqual(second.node(refs.TILT_PAD).layout_options.padding.right, 0)
        self.assertEqual(ui.PAGE.node(refs.TILT_PAD).layout_options.padding.right, 0)
        self.assertIsNot(first.node(refs.IMAGE), second.node(refs.IMAGE))

    def test_runtime_openings_use_the_declarations_page_metadata(self):
        ui = PAGE.print_preview_ui
        with mock.patch.object(ui.PAGE, "title", "Custom preview"), \
                mock.patch.object(ui.PAGE, "show_back", False), \
                mock.patch.object(ui.PAGE, "list_title", "Custom catalog entry"):
            for before_print in (False, True):
                with self.subTest(before_print=before_print):
                    page = ui.create_page(before_print=before_print)
                    self.assertEqual(page.title, "Custom preview")
                    self.assertFalse(page.show_back)
                    self.assertEqual(page.list_title, "Custom catalog entry")

    def test_file_camera_and_scale_updates_render_without_print_commands(self):
        state = self.open()
        page, refs = state["page_tree"], PAGE.common_ui.PreviewRef
        self.controller._handle_model_preview_action("model.scale_up")
        self.assertTrue(self.controller.renderer.capture.latest.has_text("SCALE 105%"))
        for route, ref, endpoint, expected in (
                (PreviewRoute.YAW_JOYSTICK, refs.YAW_PAD, "right", 150.0),
                (PreviewRoute.PITCH_JOYSTICK, refs.TILT_PAD, "top", 70.0)):
            track = page.node(ref).track_bounds(page.rect(ref))
            x, y = (track.right, track.center_y) if endpoint == "right" else (track.center_x, track.y)
            wire = self.controller.renderer._wire_action(PreviewAction(route))
            for phase in ("begin", "end"):
                self.controller._handle_continuous_touch("touch %s %s %d %d" % (wire, phase, x, y))
            key = "yaw" if route == PreviewRoute.YAW_JOYSTICK else "pitch"
            self.assertEqual(state[key], expected)
        self.controller._run_script.assert_not_called()
        self.controller._run_immediate_command.assert_not_called()
        self.assertEqual(self.controller.print_state, PrintState.IDLE)

    def test_file_open_reset_and_reopen_use_initial_camera_angles(self):
        def knobs(page):
            return tuple(next(shape.bounds for shape in self.controller.renderer.capture.latest.shapes
                              if shape.kind == "stroke" and shape.bounds.width == shape.bounds.height == 25
                              and page.rect(ref).contains(shape.bounds))
                         for ref in (PAGE.common_ui.PreviewRef.YAW_PAD, PAGE.common_ui.PreviewRef.TILT_PAD))

        for settings, expected in (({}, (0, 30)),
                                   ({"feather_preview_yaw": -90, "feather_preview_pitch": -30}, (-90, -30))):
            with self.subTest(angles=expected):
                self.controller._setting = lambda name, default=None: settings.get(name, default)
                state = self.open()
                self.assertEqual((state["yaw"], state["pitch"]), expected)
                page = state["page_tree"]
                initial = knobs(page)
                self.controller._handle_model_preview_action("model.yaw_right")
                self.controller._handle_model_preview_action("model.pitch_down")
                self.controller._handle_model_preview_action("model.reset")
                self.assertEqual((state["yaw"], state["pitch"]), expected)
                self.assertEqual(knobs(page), initial)
                self.controller._go_back()
                state = self.open()
                self.assertEqual((state["yaw"], state["pitch"]), expected)
                self.assertEqual(knobs(state["page_tree"]), initial)

    def test_file_preview_honors_configured_type_without_exposing_mode_buttons(self):
        self.controller._setting = lambda name, default=None: "SOLID" if name == "feather_preview_type" else default
        state = self.open()
        self.assertEqual(PAGE.model_runtime.MODEL_STYLES[state["settings"][0]], "SOLID")
        self.controller.params.set_value.assert_not_called()
        self.controller.file_worker.finish(0)
        self.controller.reactor.now = state["frame_at"]
        self.controller._model_preview_frame_tick(state["frame_at"])
        self.controller.file_worker.finish(1)
        self.assertEqual(state["session"].settings[0], state["settings"][0])

    def test_file_render_failure_can_recover_by_reopening_the_selected_file(self):
        state = self.open()
        self.controller.file_worker.finish(0)
        self.controller.reactor.now = state["frame_at"]
        self.controller._model_preview_frame_tick(state["frame_at"])
        _work, callback = self.controller.file_worker.submitted[-1]
        callback(None, RuntimeError("fixture model unavailable"))
        self.assertTrue(self.controller.renderer.capture.latest.has_text("fixture model unavailable"))
        self.controller._go_back()
        replacement = self.open()
        self.assertTrue(state["cancel"].is_set())
        self.assertIsNone(replacement["error"])
        self.assertIs(self.controller.selected_file, self.entry)

    def test_preview_is_next_to_start_and_print_is_in_preview_header(self):
        frame = self.controller.renderer.capture.latest
        preview, start = frame.button("file.preview"), frame.button("file.start")
        self.assertLess(preview.bounds.right, start.bounds.x)
        self.assertEqual(preview.bounds.width, start.bounds.width)
        state = self.open()
        self.assertEqual(self.controller.page, ScreenPage.PRINT_PREVIEW)
        frame = self.controller.renderer.capture.latest
        button = frame.button("model.print")
        self.assertEqual(button.label, "PRINT")
        self.assertLess(button.bounds.bottom, state["page_tree"].rect(PAGE.common_ui.PreviewRef.IMAGE).y)
        self.assertGreater(button.bounds.x, frame.text("PREVIEW").x)
        self.controller._run_script.assert_not_called()

    def test_file_details_and_full_model_do_not_depend_on_current_print_progress(self):
        state = self.open()
        self.assertFalse(self.controller.renderer.capture.latest.has_text("BEFORE PRINT"))
        self.controller.file_worker.finish(0)
        frame = self.controller.renderer.capture.latest
        for value in ("01:00:00", "10"):
            self.assertTrue(frame.has_text(value), value)
        for label in ("DIMENSIONS", "FILAMENT", "20x20x2 MM", "3.5 G PLA"):
            self.assertFalse(frame.has_text(label), label)
        self.controller.reactor.now = state["frame_at"]
        self.controller._model_preview_frame_tick(state["frame_at"])
        self.controller.file_worker.finish(1)
        self.assertEqual(state["session"].frames[-1][1], Session.layers)
        self.assertEqual(state["printed"], Session.layers)
        self.controller._run_script.assert_not_called()

    def test_back_returns_to_same_browser_and_ignores_late_metadata(self):
        for configured in (None, True):
            with self.subTest(configured=configured):
                if configured is not None:
                    self.controller.params.variables["feather_preview_back_to_files"] = configured
                self.controller.file_source = "usb"
                self.controller.file_page = 2
                state = self.open()
                self.controller._go_back()
                self.assertEqual(self.controller.page, ScreenPage.FILE_BROWSER)
                self.assertEqual(self.controller.file_source, "usb")
                self.assertEqual(self.controller.file_page, 2)
                self.assertIsNone(self.controller.model_preview)
                self.assertTrue(state["cancel"].is_set())
                batches = len(self.controller.renderer.capture.batches)
                self.controller.file_worker.finish(len(self.controller.file_worker.submitted) - 1)
                self.assertEqual(len(self.controller.renderer.capture.batches), batches)

    def test_disabled_back_to_files_keeps_file_and_mesh_options_and_ignores_late_metadata(self):
        self.controller.params.variables["feather_preview_back_to_files"] = False
        state = self.open()
        self.controller._go_back()
        self.assertEqual(self.controller.page, ScreenPage.FILE_CONFIRM)
        self.assertIs(self.controller.selected_file, self.entry)
        self.assertTrue(self.controller.file_confirm_rebuild_mesh)
        self.assertTrue(self.controller.file_confirm_auto_mesh)
        self.assertIsNone(self.controller.model_preview)
        self.assertTrue(state["cancel"].is_set())
        batches = len(self.controller.renderer.capture.batches)
        self.controller.file_worker.finish(0)
        self.assertEqual(len(self.controller.renderer.capture.batches), batches)

    def test_model_waits_for_details_and_superseded_read_retries(self):
        state = self.open()
        self.controller.reactor.now = state["frame_at"]
        self.assertEqual(self.controller._model_preview_frame_tick(state["frame_at"]),
                         self.controller.reactor.NEVER)
        self.assertEqual(len(self.controller.file_worker.submitted), 1)
        self.controller.file_worker.submitted[0][1](None, FileTaskSuperseded("replaced"))
        self.assertEqual(len(self.controller.file_worker.submitted), 2)
        self.controller.file_worker.finish(1)
        self.assertIsNotNone(state["file_info"])
        self.controller._model_preview_frame_tick(state["frame_at"])
        self.controller.file_worker.finish(2)
        self.assertIsNotNone(state["blob"])

    def test_print_uses_existing_start_validation_and_one_print_options(self):
        state = self.open()
        self.controller._reconcile_print_state = lambda _now: setattr(
            self.controller, "print_state", PrintState.PREPARING)
        action = self.controller._resolve_semantic_ui_action("model.print")
        self.controller._dispatch_semantic_ui_action(action)
        script = self.controller._run_script.call_args.args[0]
        self.assertIn('SDCARD_PRINT_FILE FILENAME="part.gcode"', script)
        self.assertIn("VARIABLE=feather_force_leveling VALUE=True", script)
        self.assertIn('VARIABLE=feather_mesh_name VALUE=\'"auto"\'', script)
        self.assertEqual(self.controller.page, ScreenPage.PRINTING)
        self.assertIsNone(self.controller.model_preview)
        self.assertTrue(state["cancel"].is_set())

    def test_print_opens_printing_page_before_virtual_sd_updates_print_stats(self):
        state = self.open()
        self.controller.print_stats.status.update(state="standby", print_duration=0)
        active = False
        self.controller.virtual_sdcard.is_active = lambda: active
        self.controller._record_current_print = mock.Mock()
        self.controller._reconcile_print_state = (
            FEATHER.FeatherScreen._reconcile_print_state.__get__(self.controller))
        rendered = []
        render = self.controller._render_screen

        def paint(feature=None):
            rendered.append(self.controller.page)
            return render(feature)

        def accept(_script):
            nonlocal active
            active = True

        self.controller._render_screen = paint
        self.controller._run_script.side_effect = accept
        action = self.controller._resolve_semantic_ui_action("model.print")
        self.controller._dispatch_semantic_ui_action(action)

        self.assertEqual(self.controller.page, ScreenPage.PRINTING)
        self.assertEqual(self.controller.print_state, PrintState.PREPARING)
        self.assertEqual(self.controller.print_stats.status["state"], "standby")
        self.assertEqual(rendered, [ScreenPage.PRINTING])
        self.assertIsNone(self.controller.model_preview)
        self.assertTrue(state["cancel"].is_set())
        self.controller.print_stats.status["state"] = "printing"
        self.controller._reconcile_print_state(self.controller.reactor.monotonic())
        self.assertEqual(rendered, [ScreenPage.PRINTING])

    def test_rejected_print_start_keeps_preview_and_mesh_options(self):
        state = self.open()
        self.controller._run_script.side_effect = RuntimeError("SD busy")
        action = self.controller._resolve_semantic_ui_action("model.print")
        with self.assertRaisesRegex(RuntimeError, "SD busy"):
            self.controller._dispatch_semantic_ui_action(action)
        self.assertEqual(self.controller.page, ScreenPage.PRINT_PREVIEW)
        self.assertEqual(self.controller.print_state, PrintState.IDLE)
        self.assertIs(self.controller.model_preview, state)
        self.assertFalse(state["cancel"].is_set())
        self.assertTrue(self.controller.file_confirm_rebuild_mesh)
        self.assertTrue(self.controller.file_confirm_auto_mesh)

    def test_missing_file_cannot_start_and_keeps_preview_recoverable(self):
        self.open()
        self.path.unlink()
        with self.assertRaisesRegex(RuntimeError, "no longer available"):
            self.controller._handle_model_preview_action("model.print")
        self.controller._run_script.assert_not_called()
        self.assertEqual(self.controller.page, ScreenPage.PRINT_PREVIEW)
        self.controller._go_back()
        self.assertEqual(self.controller.page, ScreenPage.FILE_BROWSER)

    def test_unknown_file_details_use_physical_layer_count_after_render(self):
        self.path.write_text("G1 X20\n")
        state = self.open()
        self.controller.file_worker.finish(0)
        self.assertTrue(any(text.value.strip() == "--:--:--"
                            for frame in self.controller.renderer.capture.frames for text in frame.texts))
        self.controller.reactor.now = state["frame_at"]
        self.controller._model_preview_frame_tick(state["frame_at"])
        self.controller.file_worker.finish(1)
        frame = self.controller.renderer.capture.latest
        self.assertTrue(frame.has_text(str(Session.layers)))
        self.assertTrue(frame.has_action("model.print"))

    def test_new_print_or_lost_selection_closes_file_preview(self):
        self.open()
        self.controller.print_state = PrintState.PRINTING
        self.controller._update_print_model_preview(100)
        self.assertEqual(self.controller.page, ScreenPage.PRINTING)
        self.assertIsNone(self.controller.model_preview)
        self.controller.print_state = PrintState.IDLE
        self.open()
        self.controller.selected_file = None
        self.controller._update_print_model_preview(100)
        self.assertEqual(self.controller.page, ScreenPage.FILE_BROWSER)
        self.assertIsNone(self.controller.model_preview)

    def test_preview_lab_command_replaces_file_view_and_cancels_old_work(self):
        old = self.open()
        command = mock.Mock()
        command.get.side_effect = lambda key, default=None: {
            "ACTION": "OPEN", "FILE": self.path.name,
        }.get(key, default)

        self.controller.cmd_FEATHER_PREVIEW_LAB(command)

        state = self.controller.model_preview
        self.assertEqual(self.controller.page, ScreenPage.MODEL_PREVIEW)
        self.assertEqual(state["mode"], "lab")
        self.assertFalse(state["cancel"].is_set())
        self.assertTrue(old["cancel"].is_set())
        self.assertEqual(len(self.controller.reactor.removed), 2)
        frames = len(self.controller.renderer.batches)
        self.controller.file_worker.finish(0)
        self.assertEqual(len(self.controller.renderer.batches), frames)
        self.assertIs(self.controller.model_preview, state)

    def test_direct_lab_to_file_transition_keeps_new_session(self):
        self.controller._open_model_preview(self.entry)
        old = self.controller.model_preview
        state = self.open()
        self.assertEqual(self.controller.page, ScreenPage.PRINT_PREVIEW)
        self.assertEqual(state["mode"], "file")
        self.assertFalse(state["cancel"].is_set())
        self.assertTrue(old["cancel"].is_set())


if __name__ == "__main__":
    unittest.main()
