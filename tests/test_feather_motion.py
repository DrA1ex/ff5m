## Tests for Feather's low-latency ToolHead streaming adapter.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).parents[1]
MODULE = ROOT / ".py" / "klipper" / "plugins" / "feather_motion.py"
SPEC = importlib.util.spec_from_file_location("feather_motion", MODULE)
MOTION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOTION)


class FakeMove:
    def __init__(self, duration):
        self.min_move_t = duration


class FakeMoveQueue:
    STOCK_FLUSH = 2.0

    def __init__(self, toolhead):
        self.toolhead = toolhead
        self.queue = []
        self.junction_flush = self.STOCK_FLUSH
        self.processed = 0

    def set_flush_time(self, duration):
        self.junction_flush = duration

    def add(self, move):
        self.queue.append(move)
        if len(self.queue) == 1:
            return
        self.junction_flush -= move.min_move_t
        if self.junction_flush <= 0.0:
            # Model lazy lookahead: process the safe prefix and retain one
            # segment so the following move can keep junction continuity.
            ready = self.queue[:-1]
            self.queue[:] = self.queue[-1:]
            self.processed += len(ready)
            duration = sum(item.min_move_t for item in ready)
            self.toolhead.print_time = max(
                self.toolhead.print_time,
                self.toolhead.estimated_time + 0.101) + duration
            self.junction_flush = 0.150


class FakeToolhead:
    def __init__(self):
        self.buffer_time_start = 0.250
        self.buffer_time_low = 1.000
        self.max_accel = 20000.0
        self.requested_accel_to_decel = 5000.0
        self.max_accel_to_decel = 5000.0
        self.print_time = 0.0
        self.estimated_time = 100.0
        self.move_queue = FakeMoveQueue(self)
        self.flushes = 0
        self.accels = []
        self.move_duration = 0.0125

    def check_busy(self, eventtime):
        return (self.print_time, self.estimated_time,
                not self.move_queue.queue)

    def _calc_junction_deviation(self):
        self.max_accel_to_decel = min(
            self.requested_accel_to_decel, self.max_accel)

    def manual_move(self, position, speed):
        self.accels.append(self.max_accel)
        self.move_queue.add(FakeMove(self.move_duration))

    def flush_step_generation(self):
        self.flushes += 1
        self.move_queue.queue[:] = []
        self.move_queue.set_flush_time(FakeMoveQueue.STOCK_FLUSH)


class Segment:
    position = (1.0, 2.0, 3.0)
    speed = 100.0
    acceleration = 10000.0


class FakeAxisShaper:
    def __init__(self):
        self.saved = None


class FakeInputShaper:
    def __init__(self):
        self.axes = [FakeAxisShaper(), FakeAxisShaper()]
        self.disabled = 0
        self.enabled = 0

    def get_shapers(self):
        return self.axes

    def disable_shaping(self):
        self.disabled += 1
        for axis in self.axes:
            axis.saved = ("configured",)

    def enable_shaping(self):
        self.enabled += 1
        for axis in self.axes:
            axis.saved = None


class LowLatencyToolheadStreamTest(unittest.TestCase):
    def test_short_window_processes_without_stock_lookahead_wait(self):
        toolhead = FakeToolhead()
        stream = MOTION.LowLatencyToolheadStream(toolhead)
        stream.start(100.0)

        self.assertEqual(toolhead.buffer_time_start, MOTION.START_BUFFER)
        self.assertEqual(toolhead.buffer_time_low, MOTION.STREAM_BUFFER_LOW)
        self.assertEqual(toolhead.move_queue.junction_flush,
                         MOTION.LOOKAHEAD_FLUSH)
        for _index in range(8):
            stream.queue_segment(Segment())

        self.assertGreater(toolhead.move_queue.processed, 0)
        self.assertLess(stream.ahead(100.0), MOTION.MAX_AHEAD)
        self.assertEqual(toolhead.move_queue.junction_flush,
                         MOTION.LOOKAHEAD_FLUSH)

    def test_sub_threshold_segments_accumulate_toward_lazy_flush(self):
        toolhead = FakeToolhead()
        toolhead.move_duration = 0.004
        stream = MOTION.LowLatencyToolheadStream(toolhead)
        stream.start(100.0)

        for _index in range(20):
            stream.queue_segment(Segment())
        self.assertAlmostEqual(toolhead.move_queue.junction_flush, 0.004)
        stream.queue_segment(Segment())

        self.assertEqual(toolhead.move_queue.processed, 20)
        self.assertEqual(toolhead.move_queue.junction_flush,
                         MOTION.LOOKAHEAD_FLUSH)

    def test_total_horizon_includes_unprocessed_lookahead(self):
        toolhead = FakeToolhead()
        stream = MOTION.LowLatencyToolheadStream(toolhead)
        stream.start(100.0)
        toolhead.move_queue.queue.extend(
            [FakeMove(0.025), FakeMove(0.030)])

        self.assertAlmostEqual(stream.ahead(100.0), 0.055)

    def test_repeated_refill_stays_above_idle_flush_watermark(self):
        toolhead = FakeToolhead()
        stream = MOTION.LowLatencyToolheadStream(toolhead)
        stream.start(100.0)

        minimum_processed = None
        for _tick in range(100):
            while stream.wants_segment(toolhead.estimated_time):
                stream.queue_segment(Segment())
            toolhead.estimated_time += 0.010
            processed = toolhead.print_time - toolhead.estimated_time
            minimum_processed = (
                processed if minimum_processed is None
                else min(minimum_processed, processed))
            # This is the condition used by ToolHead._flush_handler(). A
            # continuously refilled stream must never enter its idle flush.
            self.assertGreater(processed, toolhead.buffer_time_low)

        self.assertGreater(minimum_processed, MOTION.STREAM_BUFFER_LOW)
        self.assertLessEqual(stream.ahead(toolhead.estimated_time),
                             MOTION.MAX_AHEAD)

    def test_finish_restores_toolhead_settings_after_flush(self):
        toolhead = FakeToolhead()
        stream = MOTION.LowLatencyToolheadStream(toolhead)
        stream.start(100.0)
        stream.queue_segment(Segment())
        stream.finish()

        self.assertEqual(toolhead.flushes, 1)
        self.assertEqual(toolhead.buffer_time_start, 0.250)
        self.assertEqual(toolhead.buffer_time_low, 1.000)
        self.assertFalse(stream.active)
        self.assertFalse(stream.queued)

    def test_empty_finish_restores_original_lookahead_threshold(self):
        toolhead = FakeToolhead()
        stream = MOTION.LowLatencyToolheadStream(toolhead)
        stream.start(100.0)
        stream.finish()

        self.assertEqual(toolhead.flushes, 0)
        self.assertEqual(toolhead.buffer_time_start, 0.250)
        self.assertEqual(toolhead.buffer_time_low, 1.000)
        self.assertEqual(toolhead.move_queue.junction_flush,
                         FakeMoveQueue.STOCK_FLUSH)

    def test_acceleration_is_scoped_to_each_submitted_move(self):
        toolhead = FakeToolhead()
        stream = MOTION.LowLatencyToolheadStream(toolhead)
        stream.start(100.0)
        stream.queue_segment(Segment())

        self.assertEqual(toolhead.accels, [10000.0])
        self.assertEqual(toolhead.max_accel, 20000.0)
        self.assertEqual(toolhead.max_accel_to_decel, 5000.0)

    def test_existing_motion_rejects_stream_without_changing_toolhead(self):
        toolhead = FakeToolhead()
        toolhead.move_queue.queue.append(FakeMove(0.025))
        stream = MOTION.LowLatencyToolheadStream(toolhead)

        with self.assertRaises(MOTION.StreamBusy):
            stream.start(100.0)
        self.assertEqual(toolhead.buffer_time_start, 0.250)
        self.assertFalse(stream.active)

    def test_missing_private_queue_api_is_reported_as_unsupported(self):
        toolhead = type("Toolhead", (), {
            "buffer_time_start": 0.250,
            "check_busy": lambda self, eventtime: (0.0, 0.0, True),
            "manual_move": lambda self, position, speed: None,
            "flush_step_generation": lambda self: None,
        })()
        stream = MOTION.LowLatencyToolheadStream(toolhead)

        self.assertFalse(stream.supported())
        with self.assertRaises(MOTION.StreamUnavailable):
            stream.start(100.0)

    def test_input_shaping_is_disabled_only_for_the_owned_session(self):
        toolhead = FakeToolhead()
        shaper = FakeInputShaper()
        stream = MOTION.LowLatencyToolheadStream(toolhead, shaper)

        stream.start(100.0)
        self.assertEqual(shaper.disabled, 1)
        self.assertTrue(stream.shaping_owned)
        stream.finish()

        self.assertEqual(shaper.enabled, 1)
        self.assertFalse(stream.shaping_owned)

    def test_previously_disabled_input_shaping_is_left_unchanged(self):
        toolhead = FakeToolhead()
        shaper = FakeInputShaper()
        shaper.axes[0].saved = ("external",)
        stream = MOTION.LowLatencyToolheadStream(toolhead, shaper)

        stream.start(100.0)
        stream.finish()

        self.assertEqual(shaper.disabled, 0)
        self.assertEqual(shaper.enabled, 0)
        self.assertEqual(shaper.axes[0].saved, ("external",))


if __name__ == "__main__":
    unittest.main()
