# Behavioral tests for multi-probe lifecycle ownership and failures.
#
# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock


MODULE_PATH = pathlib.Path(__file__).parents[1] / ".py/klipper/patches/extras/probe.py"
package = types.ModuleType("probe_lifecycle_test")
package.__path__ = []
dependencies = {
    "probe_lifecycle_test": package,
    "probe_lifecycle_test.manual_probe": types.ModuleType("manual_probe"),
    "pins": types.ModuleType("pins"),
}
with mock.patch.dict(sys.modules, dependencies):
    spec = importlib.util.spec_from_file_location(
        "probe_lifecycle_test.probe", MODULE_PATH)
    PROBE = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(PROBE)


class ProbeCommand:
    def get_float(self, name, default, **kwargs):
        return default

    def get_int(self, name, default, **kwargs):
        return 2 if name == "SAMPLES" else default

    def get(self, name, default=None):
        return default

    error = RuntimeError
    respond_info = mock.Mock()


class ProbeLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.probe = probe = PROBE.PrinterProbe.__new__(PROBE.PrinterProbe)
        probe.speed = probe.lift_speed = 5.0
        probe.sample_count = 2
        probe.sample_retract_dist = 2.0
        probe.samples_tolerance = 0.1
        probe.samples_retries = 0
        probe.samples_result = "average"
        probe.multi_probe_pending = False
        probe.mcu_probe = mock.Mock()
        self.toolhead = mock.Mock()
        self.toolhead.get_position.return_value = [0.0, 0.0, 10.0, 0.0]
        self.toolhead.get_status.return_value = {"homed_axes": "xyz"}
        probe.printer = mock.Mock(command_error=RuntimeError)
        probe.printer.lookup_object.side_effect = lambda name: {
            "toolhead": self.toolhead, "homing": self.homing}[name]
        self.homing = mock.Mock()
        probe._probe = mock.Mock(side_effect=[[0, 0, 0.2], [0, 0, 0.25]])
        self.command = ProbeCommand()

    def assert_closed(self):
        self.probe.mcu_probe.multi_probe_begin.assert_called_once_with()
        self.probe.mcu_probe.multi_probe_end.assert_called_once_with()
        self.assertFalse(self.probe.multi_probe_pending)

    def test_successful_samples_preserve_mean_and_median_results(self):
        for result in ("average", "median"):
            with self.subTest(result=result):
                self.setUp()
                self.probe.samples_result = result
                self.assertAlmostEqual(
                    self.probe.run_probe(self.command)[2], 0.225)
                self.assert_closed()

    def test_probe_failure_closes_owned_lifecycle_and_preserves_error(self):
        error = RuntimeError("probe failed")
        self.probe._probe.side_effect = error
        with self.assertRaises(RuntimeError) as raised:
            self.probe.run_probe(self.command)
        self.assertIs(raised.exception, error)
        self.assert_closed()
        self.toolhead.manual_move.assert_not_called()

    def test_retract_failure_closes_owned_lifecycle(self):
        self.toolhead.manual_move.side_effect = RuntimeError("retract failed")
        with self.assertRaisesRegex(RuntimeError, "retract failed"):
            self.probe.run_probe(self.command)
        self.assert_closed()
        self.assertEqual(self.probe._probe.call_count, 1)

    def test_tolerance_exhaustion_closes_owned_lifecycle(self):
        self.probe._probe.side_effect = [[0, 0, 0.2], [0, 0, 0.5]]
        with self.assertRaisesRegex(RuntimeError, "samples_tolerance"):
            self.probe.run_probe(self.command)
        self.assert_closed()

    def test_tolerance_retry_stays_in_one_lifecycle(self):
        self.probe.samples_retries = 1
        self.probe._probe.side_effect = [
            [0, 0, 0.2], [0, 0, 0.5], [0, 0, 0.2], [0, 0, 0.25]]
        self.assertAlmostEqual(self.probe.run_probe(self.command)[2], 0.225)
        self.assert_closed()

    def test_nested_probe_does_not_close_its_callers_lifecycle(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                self.setUp()
                self.probe.multi_probe_begin()
                if fail:
                    self.probe._probe.side_effect = RuntimeError(
                        "nested failure")
                    with self.assertRaisesRegex(RuntimeError, "nested failure"):
                        self.probe.run_probe(self.command)
                else:
                    self.probe.run_probe(self.command)
                self.probe.mcu_probe.multi_probe_end.assert_not_called()
                self.assertTrue(self.probe.multi_probe_pending)
                self.probe.multi_probe_end()
                self.assert_closed()

    def test_accuracy_closes_lifecycle_on_probe_and_retract_failures(self):
        for stage in ("probe", "retract"):
            with self.subTest(stage=stage):
                self.setUp()
                if stage == "probe":
                    self.probe._probe.side_effect = RuntimeError(
                        "accuracy probe failed")
                else:
                    self.toolhead.manual_move.side_effect = RuntimeError(
                        "accuracy retract failed")
                with self.assertRaisesRegex(RuntimeError, "accuracy"):
                    self.probe.cmd_PROBE_ACCURACY(self.command)
                self.assert_closed()

    def test_accuracy_success_retracts_to_starting_xy(self):
        self.probe.cmd_PROBE_ACCURACY(self.command)
        self.assert_closed()
        self.assertEqual(self.toolhead.manual_move.call_count, 2)
        self.assertEqual(self.toolhead.manual_move.call_args[0],
                         ([0, 0, 12.0], 5.0))

    def test_command_error_cleanup_after_finally_does_not_end_twice(self):
        self.probe._probe.side_effect = RuntimeError("probe failed")
        with self.assertRaises(RuntimeError):
            self.probe.run_probe(self.command)
        self.probe._handle_command_error()
        self.assert_closed()

    def test_homing_timeout_aborts_probe_without_retract(self):
        del self.probe._probe
        self.probe.z_position = -10.0
        self.homing.probing_move.side_effect = RuntimeError(
            "Timeout during endstop homing")
        with self.assertRaisesRegex(RuntimeError, "Timeout during endstop"):
            self.probe.run_probe(self.command)
        self.assert_closed()
        self.toolhead.manual_move.assert_not_called()


if __name__ == "__main__":
    unittest.main()
