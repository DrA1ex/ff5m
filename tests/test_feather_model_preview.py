## Experimental preview pacing and display behavior.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import pathlib
import struct
import sys
import unittest
from unittest import mock


PLUGINS = pathlib.Path(__file__).parents[1] / ".py" / "klipper" / "plugins"
sys.path.insert(0, str(PLUGINS))

from feather.screen.pages import model_preview as PAGE
from feather.files import FileEntry
from feather.previews import PreviewCancelled
from ff5m_ui.screen import ScreenPage
from feather.screen.controls import FeatherControlsMixin
from ui.render_receipts import RenderReceipt
from ui import FeatherRenderer
from tests.feather_render_test_helper import RenderCapture, full_render_queue
from tests.test_feather_print_preview import DeferredPreviewWorker, PreviewReactor


class Renderer(FeatherRenderer):
    def __init__(self):
        super().__init__()
        self.capture = RenderCapture(self)
        self.batches = self.capture.batches


class Reactor(PreviewReactor):
    def __init__(self):
        super().__init__()
        self.updates = []
        self.removed = []

    def update_timer(self, timer, when):
        self.updates.append((timer, when))

    def unregister_timer(self, timer):
        self.removed.append(timer)


class Session:
    layers = 57
    last_render_ms = 1.0

    def __init__(self, *_args, **_kwargs):
        self.settings = _args[1]
        self.viewport = _args[2:4]
        self.frames = []
        self.scales = []
        self.pitches = []
        self.cancelled = False

    def render(self, yaw, printed, *_colors, scale=1.0, layer_fraction=None):
        if self.cancelled:
            raise PreviewCancelled()
        self.frames.append((yaw, round(self.layers * layer_fraction) if layer_fraction is not None else printed))
        self.scales.append(scale)
        self.pitches.append(self.settings[3])
        return (struct.pack("<4sBBBBHHII", b"FXI1", 1, 0, 8, 4, 16, 16, 128, 128)
                + struct.pack("<8I", *range(8)) + bytes(128))

    def cancel(self):
        self.cancelled = True

    def close(self):
        pass


class Controller(PAGE.ModelPreviewPagesMixin, FeatherControlsMixin):
    def __init__(self):
        self.reactor = Reactor()
        self.renderer = Renderer()
        self.file_worker = DeferredPreviewWorker()
        self.file_worker.submit_write = mock.Mock()
        self.params = mock.Mock()

    def _require_idle(self):
        pass

    def _current_dialog(self):
        return None

    def _setting(self, _name, default=None):
        return default

    def _show_page(self, page):
        self.page = page
        self._render_model_preview()

    def _page_paint_allowed(self, *pages):
        return self.page in pages


class ModelPreviewPacingTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("feather.model_preview.ModelPreview", Session)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.controller = Controller()
        self.controller._open_model_preview(FileEntry("part.gcode", "/data/part.gcode"))
        self.state = self.controller.model_preview
        self.initial_tick = self.state["render_timer"][1]

    def tick(self, when, finish=True):
        self.controller.reactor.now = when
        count = len(self.controller.file_worker.submitted)
        next_tick = self.controller._model_preview_frame_tick(when)
        if finish and len(self.controller.file_worker.submitted) > count:
            self.controller.file_worker.finish(count)
        return next_tick

    def status_count(self):
        return sum(text.value.startswith("YAW ")
                   for frame in self.controller.renderer.capture.frames for text in frame.texts)

    def test_animation_labels_refresh_at_five_hz_while_frames_keep_running(self):
        self.state["rotation"] = True
        next_tick = self.initial_tick
        for _ in range(30):
            next_tick = self.tick(next_tick)
        self.assertEqual(len(self.state["session"].frames), 30)
        self.assertGreaterEqual(self.status_count(), 5)
        self.assertLessEqual(self.status_count(), 6)

    def test_manual_controls_refresh_labels_immediately(self):
        self.tick(self.initial_tick)
        count = self.status_count()
        self.controller._handle_model_preview_action("model.yaw_right")
        self.assertEqual(self.status_count(), count + 1)
        self.assertTrue(any("YAW 15" in text.value
                            for text in self.controller.renderer.capture.latest.texts))

    def test_benchmark_camera_controls_keep_full_rotation_and_elevation_ranges(self):
        self.state["yaw"] = 165
        self.controller._dispatch_semantic_ui_action(
            self.controller._resolve_semantic_ui_action("model.yaw_right"))
        self.assertEqual(self.state["yaw"], -180)
        self.controller._dispatch_semantic_ui_action(
            self.controller._resolve_semantic_ui_action("model.yaw_left"))
        self.assertEqual(self.state["yaw"], 165)
        for direction, start, endpoint in (("up", 80, 90), ("down", -80, -90)):
            with self.subTest(direction=direction):
                self.state["pitch"] = start
                for _ in range(2):
                    self.controller._dispatch_semantic_ui_action(
                        self.controller._resolve_semantic_ui_action("model.pitch_" + direction))
                    self.assertEqual(self.state["pitch"], endpoint)
                self.tick(self.controller.reactor.now + 0.1)
                self.assertEqual(self.state["session"].pitches[-1], endpoint)

    def test_late_timer_keeps_original_cadence(self):
        self.state["rotation"] = True
        next_tick = self.tick(self.initial_tick + 0.009)
        self.assertAlmostEqual(next_tick, self.initial_tick + 1 / 30)
        next_tick = self.tick(next_tick + 0.011)
        self.assertAlmostEqual(next_tick, self.initial_tick + 2 / 30)

    def test_stall_skips_old_deadlines_without_catch_up_frames(self):
        self.state["rotation"] = True
        late = self.initial_tick + 0.201
        next_tick = self.tick(late)
        self.assertGreater(next_tick, late)
        self.assertLessEqual(next_tick - late, 1 / 30)
        self.assertEqual(len(self.state["session"].frames), 1)

    def test_busy_worker_suspends_timer_and_completion_wakes_it(self):
        self.state["rotation"] = True
        next_tick = self.tick(self.initial_tick, finish=False)
        self.assertEqual(self.tick(next_tick, finish=False), self.controller.reactor.NEVER)
        self.assertEqual(len(self.controller.file_worker.submitted), 1)
        self.controller.reactor.now = next_tick + 0.010
        self.controller.file_worker.finish(0)
        timer, when = self.controller.reactor.updates[-1]
        self.assertEqual(timer, self.state["render_timer"])
        self.assertEqual(when, self.controller.reactor.now)

    def test_rotation_accounts_for_time_spent_waiting_for_worker(self):
        self.state["rotation"] = True
        next_tick = self.tick(self.initial_tick, finish=False)
        self.tick(next_tick, finish=False)
        completed = self.initial_tick + 0.140
        self.controller.reactor.now = completed
        self.controller.file_worker.finish(0)
        self.tick(completed)
        self.assertAlmostEqual(self.state["yaw"], (completed - 100) * 24)

    def test_close_during_pending_frame_cannot_redraw_or_restart_timer(self):
        self.state["rotation"] = True
        self.tick(self.initial_tick, finish=False)
        self.controller._close_model_preview()
        self.controller.page = ScreenPage.FILE_BROWSER
        updates = len(self.controller.reactor.updates)
        batches = len(self.controller.renderer.batches)
        self.controller.file_worker.finish(0)
        self.assertEqual(len(self.controller.reactor.updates), updates)
        self.assertEqual(len(self.controller.renderer.batches), batches)

    def test_completion_resumes_timer_while_page_painting_is_temporarily_blocked(self):
        self.state["rotation"] = True
        next_tick = self.tick(self.initial_tick, finish=False)
        self.tick(next_tick, finish=False)
        self.controller._page_paint_allowed = lambda *_pages: False
        batches = len(self.controller.renderer.batches)
        self.controller.file_worker.finish(0)
        self.assertEqual(len(self.controller.renderer.batches), batches)
        self.assertEqual(self.controller.reactor.updates[-1][0], self.state["render_timer"])
        self.assertNotEqual(self.controller.reactor.updates[-1][1], self.controller.reactor.NEVER)

    def test_failed_generation_can_be_retried_after_reset(self):
        self.state["rotation"] = True
        next_tick = self.tick(self.initial_tick, finish=False)
        self.tick(next_tick, finish=False)
        _task, callback = self.controller.file_worker.submitted[0]
        callback(None, RuntimeError("fixture failure"))
        self.assertEqual(self.state["error"], "fixture failure")
        self.controller._handle_model_preview_action("model.reset")
        self.tick(self.controller.reactor.updates[-1][1])
        self.assertIsNone(self.state["error"])
        self.assertEqual(len(self.state["session"].frames), 1)

    def test_old_style_completion_does_not_wake_or_redraw_new_session(self):
        self.tick(self.initial_tick, finish=False)
        self.controller._handle_model_preview_action("model.style.0")
        updates = len(self.controller.reactor.updates)
        batches = len(self.controller.renderer.batches)
        self.controller.file_worker.finish(0)
        self.assertEqual(len(self.controller.reactor.updates), updates)
        self.assertEqual(len(self.controller.renderer.batches), batches)

    def test_callback_uses_current_clock_when_dispatch_eventtime_is_stale(self):
        self.state["rotation"] = True
        self.controller.reactor.now = self.initial_tick + 0.071
        next_tick = self.controller._model_preview_frame_tick(self.initial_tick)
        self.assertGreater(next_tick, self.controller.reactor.now)
        self.assertLessEqual(next_tick - self.controller.reactor.now, 1 / 30)

    def test_cached_auto_build_skips_duplicate_frames_without_changing_progress(self):
        self.tick(self.initial_tick)
        self.controller._handle_model_preview_action("model.build")
        next_tick = self.initial_tick + 1 / 30
        progress = []
        batches = len(self.controller.renderer.batches)
        for _ in range(116):
            next_tick = self.tick(next_tick)
            progress.append(self.state["printed"])
        self.assertEqual(progress[:4], [0, 1, 1, 2])
        self.assertEqual(progress[-4:], [56, 57, 57, 56])
        self.assertEqual(len(self.state["session"].frames), 59)
        self.assertEqual(len(self.controller.renderer.batches) - batches, 58)
        self.assertTrue(self.state["build"])

    def test_uncached_auto_build_regenerates_duplicate_frames(self):
        self.state["use_cache"] = False
        self.tick(self.initial_tick)
        self.controller._handle_model_preview_action("model.build")
        next_tick = self.initial_tick + 1 / 30
        for _ in range(4):
            next_tick = self.tick(next_tick)
        self.assertEqual([printed for _yaw, printed in self.state["session"].frames], [0, 0, 1, 1, 2])

    def test_rotation_and_build_generate_each_frame_with_cache(self):
        self.state["rotation"] = self.state["build"] = True
        next_tick = self.initial_tick
        for _ in range(4):
            next_tick = self.tick(next_tick)
        frames = self.state["session"].frames
        self.assertEqual([printed for _yaw, printed in frames], [0, 1, 1, 2])
        self.assertEqual(len({yaw for yaw, _printed in frames}), 4)

    def test_cached_build_regenerates_when_theme_changes_on_a_duplicate_tick(self):
        next_tick = self.tick(self.initial_tick)
        self.controller._handle_model_preview_action("model.build")
        self.controller.renderer.color = lambda _color: "123456"
        self.tick(next_tick)
        self.assertEqual(len(self.state["session"].frames), 2)
        self.assertEqual(self.state["printed"], 0)

    def test_cached_build_from_full_reverses_at_zero_and_stop_resume_preserves_progress(self):
        next_tick = self.tick(self.initial_tick)
        self.controller._handle_model_preview_action("model.full")
        next_tick = self.tick(next_tick)
        self.controller._handle_model_preview_action("model.build")
        progress = []
        for _ in range(116):
            next_tick = self.tick(next_tick)
            progress.append(self.state["printed"])
        self.assertEqual(progress[:4], [57, 56, 56, 55])
        self.assertEqual(progress[-4:], [1, 0, 0, 1])
        self.controller._handle_model_preview_action("model.build")
        frames = len(self.state["session"].frames)
        for _ in range(4):
            next_tick = self.tick(next_tick)
        self.assertEqual(self.state["printed"], 1)
        self.assertEqual(len(self.state["session"].frames), frames)
        self.controller._handle_model_preview_action("model.build")
        next_tick = self.tick(next_tick)
        self.assertEqual(len(self.state["session"].frames), frames)
        self.tick(next_tick)
        self.assertEqual(self.state["printed"], 2)
        self.assertEqual(len(self.state["session"].frames), frames + 1)

    def test_reset_during_cached_build_restores_upward_progress_and_requests_a_frame(self):
        next_tick = self.tick(self.initial_tick)
        self.controller._handle_model_preview_action("model.full")
        next_tick = self.tick(next_tick)
        self.controller._handle_model_preview_action("model.build")
        next_tick = self.tick(next_tick)
        self.controller._handle_model_preview_action("model.reset")
        frames = len(self.state["session"].frames)
        next_tick = self.tick(next_tick)
        self.assertEqual(self.state["printed"], 0)
        self.assertEqual(len(self.state["session"].frames), frames + 1)
        self.tick(next_tick)
        self.assertEqual(self.state["printed"], 1)

    def test_controls_resolve_and_dispatch_semantic_actions(self):
        renderer = self.controller.renderer
        for region in renderer.hitboxes:
            if region.action.startswith("model."):
                action = self.controller._resolve_semantic_ui_action(region.action)
                self.assertIsNotNone(action, region.action)
                self.assertEqual(action.wire_id, region.action)
        self.assertIsNone(self.controller._resolve_semantic_ui_action("model.style.99"))
        action = self.controller._resolve_semantic_ui_action("model.yaw_right")
        self.controller._dispatch_semantic_ui_action(action)
        self.assertEqual(self.state["yaw"], 15)
        self.assertTrue(any("YAW 15" in text.value for text in renderer.capture.latest.texts))
        action = self.controller._resolve_semantic_ui_action("model.cache")
        self.controller._dispatch_semantic_ui_action(action)
        self.assertFalse(self.state["use_cache"])
        self.assertEqual(renderer.capture.latest.button("model.cache").label, "CACHE OFF")

    def test_frame_updates_keep_controls_and_only_paint_the_preview_and_status(self):
        renderer = self.controller.renderer
        buttons = dict(renderer._buttons)
        self.state["rotation"] = True
        self.tick(self.initial_tick)
        self.assertEqual(renderer._buttons, buttons)
        frame = renderer.capture.latest
        image = self.state["page_tree"].rect(PAGE.common_ui.PreviewRef.IMAGE)
        self.assertTrue(all(shape.bounds.x >= image.right for shape in frame.shapes))
        self.assertTrue(all(text.value.startswith("YAW ") for text in frame.texts))
        submitted = renderer.capture.submitted[-1]
        self.assertEqual(submitted.kind, "animation")
        self.assertIsNotNone(submitted.receipt)

    def test_all_controls_fit_without_overlap_or_label_truncation(self):
        renderer = self.controller.renderer
        buttons = [button for action, button in renderer.capture.latest.buttons.items()
                   if action.startswith("model.")]
        self.assertEqual(len(buttons), 17)
        for index, button in enumerate(buttons):
            with self.subTest(action=button.action):
                bounds = button.bounds
                self.assertGreaterEqual(bounds.x, 0)
                self.assertLessEqual(bounds.right, 800)
                self.assertGreaterEqual(bounds.y, 56)
                self.assertLessEqual(bounds.bottom, 442)
                self.assertLessEqual(renderer.text_width(button.label, button.font),
                                     bounds.width - 2 * renderer.BUTTON_TEXT_PADDING)
                for other in buttons[index + 1:]:
                    other = other.bounds
                    self.assertFalse(bounds.x < other.right and other.x < bounds.right
                                     and bounds.y < other.bottom and other.y < bounds.bottom)

    def test_rejected_static_frame_is_retried(self):
        with full_render_queue(self.controller.renderer):
            next_tick = self.tick(self.initial_tick)
        self.assertFalse(self.state["accepted"])
        self.tick(next_tick)
        self.assertTrue(self.state["accepted"])
        self.assertEqual(len(self.state["session"].frames), 2)
        self.assertTrue(self.controller.renderer.capture.submitted[-1].receipt.startswith("mpl:"))
        self.assertTrue(any("LAYERS 0/57" in text.value
                            for text in self.controller.renderer.capture.latest.texts))

    def test_static_preview_publishes_loaded_layer_count_on_its_first_frame(self):
        next_tick = self.tick(self.initial_tick)
        self.assertTrue(any("LAYERS 0/57" in text.value
                            for text in self.controller.renderer.capture.latest.texts))
        self.tick(next_tick)
        self.assertEqual(len(self.state["session"].frames), 1)

    def test_failed_receipt_retries_static_frame_and_old_session_receipts_are_ignored(self):
        next_tick = self.tick(self.initial_tick)
        token = self.controller.renderer.capture.submitted[-1].receipt
        self.controller._model_preview_render_receipt(RenderReceipt(token, False, 10, 5, 5))
        self.tick(next_tick)
        self.assertEqual(len(self.state["session"].frames), 2)
        self.controller._handle_model_preview_action("model.style.0")
        before = self.state["render_key"]
        self.controller._model_preview_render_receipt(RenderReceipt(token, True, 10, 5, 5))
        self.assertEqual(self.state["display_frames"], 0)
        self.assertEqual(self.state["render_key"], before)

    def test_close_unregisters_timers_and_retires_session_once(self):
        self.tick(self.initial_tick)
        session = self.state["session"]
        self.controller._close_model_preview()
        self.controller._close_model_preview()
        self.assertCountEqual(self.controller.reactor.removed,
                              [self.state["render_timer"], self.state["monitor_timer"]])
        self.assertTrue(session.cancelled)
        self.controller.file_worker.submit_write.assert_called_once_with(session.close)

    def test_unavailable_worker_displays_failure_and_reset_recovers(self):
        worker = self.controller.file_worker
        self.controller.file_worker = None
        self.controller.reactor.now = self.initial_tick
        next_tick = self.controller._model_preview_frame_tick(self.initial_tick)
        self.assertEqual(self.state["error"], "File worker unavailable")
        self.assertTrue(self.controller.renderer.capture.latest.has_text("FAILED"))
        self.controller.file_worker = worker
        self.controller._handle_model_preview_action("model.reset")
        self.tick(next_tick)
        self.assertIsNone(self.state["error"])
        self.assertEqual(len(self.state["session"].frames), 1)

    def test_band_conversion_failure_also_retires_the_resident_process(self):
        self.tick(self.initial_tick, finish=False)
        session = self.state["session"]
        with mock.patch("feather.model_preview.model_frame_bands", side_effect=RuntimeError("invalid palette")):
            self.controller.file_worker.finish(0)
        self.assertEqual(self.state["error"], "invalid palette")
        self.assertTrue(session.cancelled)
        self.assertIsNone(self.state["session"])
        self.controller.file_worker.submit_write.assert_called_once_with(session.close)

    def test_camera_layer_and_scale_buttons_are_vertical_pairs_with_plus_on_top(self):
        frame = self.controller.renderer.capture.latest
        pairs = (("yaw_right", "yaw_left"), ("pitch_up", "pitch_down"),
                 ("layer_up", "layer_down"), ("scale_up", "scale_down"))
        columns = []
        for plus, minus in pairs:
            top, bottom = frame.button("model." + plus), frame.button("model." + minus)
            self.assertEqual(top.bounds.x, bottom.bounds.x)
            self.assertLess(top.bounds.bottom, bottom.bounds.y)
            self.assertTrue(top.label.endswith("+"))
            self.assertTrue(bottom.label.endswith("-"))
            columns.append(top.bounds.x)
        self.assertEqual(columns, sorted(set(columns)))
        self.assertFalse(frame.has_action("model.reset"))
        self.assertFalse(frame.has_action("model.full"))

    def test_scale_changes_request_a_new_frame_without_reopening_the_cached_model(self):
        next_tick = self.tick(self.initial_tick)
        session = self.state["session"]
        action = self.controller._resolve_semantic_ui_action("model.scale_up")
        self.controller._dispatch_semantic_ui_action(action)
        self.assertEqual(self.state["scale"], 1.05)
        self.assertTrue(self.controller.renderer.capture.latest.has_text("SCALE 105%"))
        self.tick(next_tick)
        self.assertIs(self.state["session"], session)
        self.assertEqual(session.scales, [1.0, 1.05])
        self.controller.file_worker.submit_write.assert_not_called()

    def test_scale_controls_clamp_and_reverse_at_both_limits_without_drift(self):
        for _ in range(50):
            self.controller._handle_model_preview_action("model.scale_up")
        self.assertEqual(self.state["scale"], 3.0)
        self.controller._handle_model_preview_action("model.scale_down")
        self.assertEqual(self.state["scale"], 2.95)
        for _ in range(60):
            self.controller._handle_model_preview_action("model.scale_down")
        self.assertEqual(self.state["scale"], 0.25)
        self.controller._handle_model_preview_action("model.scale_up")
        self.assertEqual(self.state["scale"], 0.30)
        self.assertTrue(self.controller.renderer.capture.latest.has_text("SCALE 30%"))

    def test_reopening_a_file_and_diagnostic_reset_use_the_saved_scale(self):
        self.controller._setting = lambda name, default=None: 1.5 if name == "feather_preview_scale" else default
        self.controller._open_model_preview(FileEntry("part.gcode", "/data/part.gcode"))
        self.state = self.controller.model_preview
        self.assertEqual(self.state["scale"], 1.5)
        self.controller._handle_model_preview_action("model.scale_down")
        self.controller._handle_model_preview_action("model.reset")
        self.assertEqual(self.state["scale"], 1.5)
        self.assertTrue(self.controller.renderer.capture.latest.has_text("SCALE 150%"))

    def test_selected_style_retries_failed_generation_without_a_reset_button(self):
        self.tick(self.initial_tick, finish=False)
        _task, callback = self.controller.file_worker.submitted[0]
        callback(None, RuntimeError("fixture failure"))
        self.controller._handle_model_preview_action("model.style.4")
        self.assertIsNone(self.state["error"])
        self.tick(self.state["frame_at"])
        self.assertEqual(len(self.state["session"].frames), 1)


if __name__ == "__main__":
    unittest.main()
