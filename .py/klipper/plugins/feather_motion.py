## Low-latency ToolHead streaming for Feather's joystick controls.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import logging


# The stock ToolHead keeps up to buffer_time_high (two seconds on the AD5M)
# in its idle lookahead, flushes whenever it falls below buffer_time_low (one
# second), and starts the first processed move 250 ms ahead. Joystick motion
# needs a much smaller, bounded horizon. MIN_KIN_TIME in ToolHead still
# provides the MCU with its required ~100 ms generation window.
# The AD5M MCU needs materially more than ToolHead.MIN_KIN_TIME during the
# first step-generation pass. Keep the proven stock 250 ms startup allowance,
# then maintain a bounded 300 ms rolling horizon. This is still far below the
# stock one-to-two-second buffering, while leaving enough room for a delayed
# host timer without starving the MCU.
START_BUFFER = 0.250
STREAM_BUFFER_LOW = 0.150
LOOKAHEAD_FLUSH = 0.060
TARGET_AHEAD = 0.300
MAX_AHEAD = 0.450
BUSY_TOLERANCE = 0.020
START_BUSY_GRACE = 0.750
MAX_REFILL_SEGMENTS = 16


class StreamUnavailable(Exception):
    pass


class StreamBusy(Exception):
    pass


class LowLatencyToolheadStream:
    """Temporarily tune the native ToolHead queue for held joystick motion.

    Motion still goes through manual_move(), Move, kin.check_move(), trapq, and
    normal step generation.  Only the idle startup buffer and lazy lookahead
    threshold are shortened while this object owns an otherwise idle queue.
    """

    def __init__(self, toolhead, input_shaper=None):
        self.toolhead = toolhead
        self.input_shaper = input_shaper
        self.active = False
        self.queued = False
        self.shaping_owned = False
        self.saved_start_buffer = None
        self.saved_buffer_low = None
        self.saved_flush_time = None
        self.segment_count = 0
        self.maximum_ahead = 0.0
        self.maximum_processed = 0.0
        self.maximum_pending = 0.0
        self.minimum_motion_processed = None
        self.minimum_motion_ahead = None
        self.last_processed = 0.0
        self.last_pending = 0.0
        self.last_ahead = 0.0
        self.motion_active = False
        self.motion_ready = False
        self.motion_started_at = None
        self.maximum_startup_duration = 0.0
        self.last_motion_tick_at = None
        self.maximum_tick_gap = 0.0
        self.maximum_refill_duration = 0.0
        self.maximum_feedback_duration = 0.0
        self.maximum_refill_segments = 0
        self.minimum_motion_refill_processed_before = None
        self.minimum_motion_refill_processed_after = None
        self.start_stalls = 0
        self.last_stall_count = 0
        self.motion_stalls = 0

    def supported(self):
        queue = getattr(self.toolhead, "move_queue", None)
        return (
            hasattr(self.toolhead, "buffer_time_start")
            and hasattr(self.toolhead, "buffer_time_low")
            and hasattr(self.toolhead, "max_accel")
            and callable(getattr(self.toolhead, "check_busy", None))
            and callable(getattr(
                self.toolhead, "_calc_junction_deviation", None))
            and callable(getattr(self.toolhead, "manual_move", None))
            and callable(getattr(self.toolhead, "flush_step_generation", None))
            and queue is not None
            and hasattr(queue, "queue")
            and callable(getattr(queue, "set_flush_time", None))
        )

    def _pending_time(self):
        queue = getattr(getattr(self.toolhead, "move_queue", None),
                        "queue", ())
        return sum(max(0.0, float(getattr(move, "min_move_t", 0.0)))
                   for move in queue)

    def _stall_count(self):
        return int(getattr(self.toolhead, "print_stall", 0))

    def set_motion_active(self, active, eventtime):
        """Start or stop diagnostics for actual planned motion."""
        active = bool(active)
        eventtime = float(eventtime)
        if active and not self.motion_active:
            self.motion_ready = False
            self.motion_started_at = eventtime
            self.last_motion_tick_at = None
            # Stalls before a new motion phase belong to the neutral tail or
            # startup transition, not to steady joystick motion.
            self.last_stall_count = self._stall_count()
        elif not active and self.motion_active:
            self.motion_ready = False
            self.motion_started_at = None
            self.last_motion_tick_at = None
            self.last_stall_count = self._stall_count()
        self.motion_active = active

    def motion_diagnostics_active(self):
        return self.motion_active and self.motion_ready

    def ahead(self, eventtime):
        print_time, estimated_time, _empty = self.toolhead.check_busy(eventtime)
        processed = max(0.0, float(print_time) - float(estimated_time))
        pending = self._pending_time()
        total = processed + pending
        self.last_processed = processed
        self.last_pending = pending
        self.last_ahead = total
        self.maximum_ahead = max(self.maximum_ahead, total)
        self.maximum_processed = max(self.maximum_processed, processed)
        self.maximum_pending = max(self.maximum_pending, pending)
        return total

    def record_motion_cycle(self, eventtime, active, processed_before,
                            ahead_before, processed_after, ahead_after):
        """Record one refill cycle after its final motion state is known."""
        eventtime = float(eventtime)
        self.set_motion_active(active, eventtime)
        current_stalls = self._stall_count()
        if not self.motion_active:
            self.last_stall_count = current_stalls
            return

        was_ready = self.motion_ready
        if not self.motion_ready:
            if max(processed_before, processed_after) < START_BUFFER:
                # Startup and re-prime stalls are intentionally excluded.
                self.last_stall_count = current_stalls
                return
            self.motion_ready = True
            self.last_motion_tick_at = eventtime
            if self.motion_started_at is not None:
                self.maximum_startup_duration = max(
                    self.maximum_startup_duration,
                    eventtime - self.motion_started_at)
            self.last_stall_count = current_stalls
        else:
            if self.last_motion_tick_at is not None:
                self.maximum_tick_gap = max(
                    self.maximum_tick_gap,
                    eventtime - self.last_motion_tick_at)
            self.last_motion_tick_at = eventtime
            self.motion_stalls += max(
                0, current_stalls - self.last_stall_count)
            self.last_stall_count = current_stalls

        samples = []
        if was_ready or processed_before >= START_BUFFER:
            samples.append((processed_before, ahead_before))
            self.minimum_motion_refill_processed_before = (
                processed_before
                if self.minimum_motion_refill_processed_before is None
                else min(self.minimum_motion_refill_processed_before,
                         processed_before))
        if was_ready or processed_after >= START_BUFFER:
            samples.append((processed_after, ahead_after))
            self.minimum_motion_refill_processed_after = (
                processed_after
                if self.minimum_motion_refill_processed_after is None
                else min(self.minimum_motion_refill_processed_after,
                         processed_after))
        for processed, total in samples:
            self.minimum_motion_processed = (
                processed if self.minimum_motion_processed is None
                else min(self.minimum_motion_processed, processed))
            self.minimum_motion_ahead = (
                total if self.minimum_motion_ahead is None
                else min(self.minimum_motion_ahead, total))

    def record_refill(self, duration, segment_count):
        self.maximum_refill_duration = max(
            self.maximum_refill_duration, max(0.0, float(duration)))
        self.maximum_refill_segments = max(
            self.maximum_refill_segments, int(segment_count))

    def record_feedback(self, duration):
        self.maximum_feedback_duration = max(
            self.maximum_feedback_duration, max(0.0, float(duration)))

    def start(self, eventtime):
        if self.active:
            return
        if not self.supported():
            raise StreamUnavailable("low-latency ToolHead API is unavailable")
        _print_time, _estimated_time, lookahead_empty = (
            self.toolhead.check_busy(eventtime))
        queuing_state = getattr(
            self.toolhead, "special_queuing_state", "Flushed")
        if (not lookahead_empty
                or getattr(self.toolhead.move_queue, "queue", ())
                or queuing_state != "Flushed"
                or self.ahead(eventtime) > BUSY_TOLERANCE):
            raise StreamBusy("ToolHead still has queued motion")

        shaper = self.input_shaper
        if shaper is not None:
            axes = (shaper.get_shapers()
                    if callable(getattr(shaper, "get_shapers", None)) else ())
            already_disabled = any(
                getattr(axis, "saved", None) is not None for axis in axes)
            if not already_disabled:
                try:
                    shaper.disable_shaping()
                    self.shaping_owned = True
                except Exception:
                    # disable_shaping() updates both axes before rebuilding
                    # step generation; restore the configured shapers even if
                    # that rebuild reports an error.
                    shaper.enable_shaping()
                    raise

        self.saved_start_buffer = self.toolhead.buffer_time_start
        self.saved_buffer_low = self.toolhead.buffer_time_low
        self.saved_flush_time = getattr(
            self.toolhead.move_queue, "junction_flush", None)
        try:
            self.toolhead.buffer_time_start = min(
                float(self.saved_start_buffer), START_BUFFER)
            # The stock one-second low watermark would flush every interactive
            # window and put ToolHead back into its "Flushed" startup state.
            # Keep its safety timer active, but below the refill target so it
            # only fires on a genuine producer underrun.
            self.toolhead.buffer_time_low = min(
                float(self.saved_buffer_low), STREAM_BUFFER_LOW)
            self.toolhead.move_queue.set_flush_time(LOOKAHEAD_FLUSH)
        except Exception:
            self.toolhead.buffer_time_start = self.saved_start_buffer
            self.toolhead.buffer_time_low = self.saved_buffer_low
            self.saved_start_buffer = None
            self.saved_buffer_low = None
            self.saved_flush_time = None
            if self.shaping_owned:
                try:
                    shaper.enable_shaping()
                finally:
                    self.shaping_owned = False
            raise
        self.active = True
        self.queued = False
        self.segment_count = 0
        self.maximum_ahead = 0.0
        self.maximum_processed = 0.0
        self.maximum_pending = 0.0
        self.minimum_motion_processed = None
        self.minimum_motion_ahead = None
        self.last_processed = 0.0
        self.last_pending = 0.0
        self.last_ahead = 0.0
        self.motion_active = False
        self.motion_ready = False
        self.motion_started_at = None
        self.maximum_startup_duration = 0.0
        self.last_motion_tick_at = None
        self.maximum_tick_gap = 0.0
        self.maximum_refill_duration = 0.0
        self.maximum_feedback_duration = 0.0
        self.maximum_refill_segments = 0
        self.minimum_motion_refill_processed_before = None
        self.minimum_motion_refill_processed_after = None
        self.start_stalls = int(getattr(
            self.toolhead, "print_stall", 0))
        self.last_stall_count = self.start_stalls
        self.motion_stalls = 0
        logging.info(
            "[feather_screen] joystick stream begin start_buffer=%.3f "
            "buffer_low=%.3f lookahead=%.3f target=%.3f shaping=%s "
            "kin_delay=%.3f",
            self.toolhead.buffer_time_start, self.toolhead.buffer_time_low,
            LOOKAHEAD_FLUSH, TARGET_AHEAD,
            "off" if self.shaping_owned else "unchanged",
            float(getattr(self.toolhead, "kin_flush_delay", 0.0)))

    def wants_segment(self, eventtime):
        return self.ahead(eventtime) < TARGET_AHEAD

    def queue_segment(self, segment):
        if not self.active:
            raise StreamUnavailable("joystick stream is not active")

        # Move captures acceleration and junction deviation at construction.
        # Restore the configured global values immediately after submission.
        saved_accel = self.toolhead.max_accel
        try:
            self.toolhead.max_accel = min(
                float(saved_accel), float(segment.acceleration))
            self.toolhead._calc_junction_deviation()
            self.toolhead.manual_move(segment.position, segment.speed)
        finally:
            self.toolhead.max_accel = saved_accel
            self.toolhead._calc_junction_deviation()
            # MoveQueue.flush() resets this to the stock lookahead constant.
            # Reapply the interactive threshold only after an actual flush.
            # Resetting it after a sub-threshold move would discard the
            # accumulated duration and recreate a large hidden lookahead.
            remaining = getattr(
                self.toolhead.move_queue, "junction_flush", LOOKAHEAD_FLUSH)
            if remaining > LOOKAHEAD_FLUSH:
                self.toolhead.move_queue.set_flush_time(LOOKAHEAD_FLUSH)
        self.queued = True
        self.segment_count += 1

    def finish(self):
        if not self.active:
            return
        self.set_motion_active(False, 0.0)
        error = None
        try:
            if self.queued:
                self.toolhead.flush_step_generation()
            elif self.saved_flush_time is not None:
                self.toolhead.move_queue.set_flush_time(self.saved_flush_time)
        except Exception as exc:
            error = exc
        finally:
            if error is not None and self.saved_flush_time is not None:
                try:
                    self.toolhead.move_queue.set_flush_time(
                        self.saved_flush_time)
                except Exception:
                    pass
            if self.saved_start_buffer is not None:
                self.toolhead.buffer_time_start = self.saved_start_buffer
            if self.saved_buffer_low is not None:
                self.toolhead.buffer_time_low = self.saved_buffer_low
            if self.shaping_owned:
                try:
                    self.input_shaper.enable_shaping()
                except Exception as exc:
                    if error is None:
                        error = exc
                finally:
                    self.shaping_owned = False
            logging.info(
                "[feather_screen] joystick stream end segments=%d "
                "max_ahead=%.3f processed=%.3f pending=%.3f "
                "motion_min_processed=%.3f motion_min_ahead=%.3f "
                "stalls=%d motion_stalls=%d motion_tick_gap=%.3f "
                "startup=%.3f refill=%.3f feedback=%.3f "
                "refill_segments=%d motion_refill_processed=%.3f/%.3f",
                self.segment_count, self.maximum_ahead,
                self.maximum_processed, self.maximum_pending,
                self.minimum_motion_processed or 0.0,
                self.minimum_motion_ahead or 0.0,
                self._stall_count() - self.start_stalls,
                self.motion_stalls,
                self.maximum_tick_gap, self.maximum_startup_duration,
                self.maximum_refill_duration,
                self.maximum_feedback_duration,
                self.maximum_refill_segments,
                self.minimum_motion_refill_processed_before or 0.0,
                self.minimum_motion_refill_processed_after or 0.0)
            self.active = False
            self.queued = False
            self.motion_active = False
            self.motion_ready = False
            self.motion_started_at = None
            self.last_motion_tick_at = None
            self.saved_start_buffer = None
            self.saved_buffer_low = None
            self.saved_flush_time = None
        if error is not None:
            raise error
