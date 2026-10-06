## Behavioral checks for cooperative motion yielding.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license.

import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock

from tests.test_reactor_patch import REACTOR


ROOT = pathlib.Path(__file__).parents[1]


def load_toolhead():
    dependencies = {name: types.ModuleType(name) for name in (
        "mcu", "chelper", "kinematics", "kinematics.extruder")}
    dependencies["kinematics"].extruder = dependencies["kinematics.extruder"]
    spec = importlib.util.spec_from_file_location(
        "buffer_yield_toolhead", ROOT / ".py/klipper/patches/toolhead.py")
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, dependencies):
        spec.loader.exec_module(module)
    return module


TOOLHEAD = load_toolhead()


class BufferingHarness:
    """Execute the planner and reactor callbacks with a virtual MCU clock."""

    def __init__(self):
        self.now = 204.430019
        self.reactor = REACTOR.SelectReactor()
        self.reactor.monotonic = lambda: self.now
        self.pauses = []
        self.reactor.pause = self.pause
        self.reactor._check_timers(self.now, True)
        self.moves = []
        t = self.toolhead = object.__new__(TOOLHEAD.ToolHead)
        t.reactor = self.reactor
        t.mcu = mock.Mock()
        t.mcu.estimated_print_time.side_effect = lambda eventtime: eventtime
        t.all_mcus = [t.mcu]
        t.printer = mock.Mock()
        t.extruder = mock.Mock()
        t.extruder.calc_junction.return_value = 1.e12
        t.kin = mock.Mock()
        t.kin.check_move.side_effect = self.limit_z
        t.max_velocity, t.max_accel = 600., 8700.
        t.requested_accel_to_decel, t.square_corner_velocity = 5000., 9.
        t._calc_junction_deviation()
        t.print_time = 205.396991
        t.special_queuing_state, t.need_check_stall = "", -1.
        t.buffer_time_high, t.buffer_time_low, t.buffer_time_start = 1.5, 1., .250
        t.can_pause, t.idle_flush_print_time, t.print_stall = True, 0., 0
        t.kin_flush_delay, t.move_flush_time = .020, .050
        t.last_kin_flush_time, t.last_kin_move_time = 0., t.print_time
        t.step_generators, t.trapq = [], None
        t.trapq_append = lambda *args: self.moves.append(args[1:])
        t.trapq_finalize_moves = lambda *args: None
        t.low_latency_stream_submission = False
        t.low_latency_saved_move_flush_time = None
        t.flush_timer = self.reactor.register_timer(t._flush_handler)
        t.move_queue = TOOLHEAD.MoveQueue(t, .150)

    @staticmethod
    def limit_z(move):
        if abs(move.axes_d[2]) > 1.:
            move.limit_speed(25., 500.)

    def pause(self, waketime):
        self.pauses.append(waketime)
        target = max(self.now, waketime)
        # A timer represents resumption, so the real reactor updates its
        # dispatch timestamp through the same path as a resumed greenlet.
        resume = self.reactor.register_timer(lambda t: self.reactor.NEVER, target)
        while True:
            earliest = min(t.waketime for t in self.reactor._timers)
            if earliest > target:
                break
            self.now = max(self.now, earliest)
            self.reactor._check_timers(self.now, True)
        self.now = target
        self.reactor.unregister_timer(resume)
        return self.now

    def park(self):
        points = [
            (21.9608, -1.3122, 3.8041, 0.),
            (21.9759, -1.3769, 50.0041, 0.),
            (66.4419, 55.2515, 49.9750, 0.),
            (86.2045, 80.4197, 49.9450, 0.),
            (105.4506, 104.9301, 49.9300, 0.)]
        t = self.toolhead
        lift = TOOLHEAD.Move(t, points[0], points[1], 25.)
        self.limit_z(lift)
        t.move_queue.add_move(lift)
        t.commanded_pos = list(points[1])
        self.reactor.update_timer(t.flush_timer, self.reactor.NOW)
        for point in points[2:]:
            t.move(point, 300.)
        t.flush_step_generation()


class ToolheadYieldTest(unittest.TestCase):
    def test_short_segmented_parking_keeps_one_continuous_xy_move(self):
        harness = BufferingHarness()
        harness.park()
        before, after = harness.moves[1:3]
        gap = after[0] - (before[0] + sum(before[1:4]))
        self.assertAlmostEqual(gap, 0.)
        self.assertGreater(before[-3], 0.)

    def test_extra_yield_waits_for_work_budget_and_resets_after_resumption(self):
        harness = BufferingHarness()
        harness.now += .019
        harness.toolhead._check_stall()
        self.assertEqual(harness.pauses, [])
        harness.now += .002
        harness.toolhead._check_stall()
        self.assertEqual(harness.pauses, [harness.reactor.NOW])
        harness.now += .019
        harness.toolhead._check_stall()
        self.assertEqual(harness.pauses, [harness.reactor.NOW])
        harness.now += .002
        harness.toolhead._check_stall()
        self.assertEqual(harness.pauses, [harness.reactor.NOW] * 2)

    def test_low_buffer_alone_does_not_trigger_an_early_yield(self):
        harness = BufferingHarness()
        harness.toolhead.print_time = harness.now - .100
        harness.toolhead._check_stall()
        self.assertEqual(harness.pauses, [])

    def test_idle_time_before_a_new_callback_does_not_spend_work_budget(self):
        harness = BufferingHarness()
        harness.now += 60.
        harness.reactor.register_timer(
            lambda t: harness.toolhead._check_stall() or harness.reactor.NEVER,
            harness.now)
        harness.reactor._check_timers(harness.now, True)
        self.assertEqual(harness.pauses, [])

    def test_full_buffer_wait_is_preserved_without_an_extra_yield(self):
        harness = BufferingHarness()
        harness.toolhead.print_time = harness.now + 2.
        harness.toolhead._check_stall()
        self.assertEqual(len(harness.pauses), 1)
        self.assertGreater(harness.pauses[0], harness.reactor.NOW)

    def test_low_latency_submission_still_suppresses_extra_yield(self):
        harness = BufferingHarness()
        harness.toolhead.low_latency_stream_submission = True
        harness.now += .030
        harness.toolhead._check_stall()
        self.assertEqual(harness.pauses, [])

    def test_original_pause_error_propagates(self):
        harness = BufferingHarness()
        harness.now += .030
        harness.reactor.pause = mock.Mock(side_effect=RuntimeError("pause failed"))
        with self.assertRaisesRegex(RuntimeError, "pause failed"):
            harness.toolhead._check_stall()


if __name__ == "__main__":
    unittest.main()
