## Temperature polling through the real dispatcher and macro lifecycle.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import ast
import contextlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

from tests.gcode_macro_harness import load_macro, render_macro


ROOT = Path(__file__).parents[1]
BASE = ROOT / "macros/base.cfg"


def load_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GCODE = load_module("wait_test_gcode", ".py/klipper/patches/gcode.py")
CONTEXT = load_module("wait_test_context", ".py/klipper/plugins/operation_context.py")
POLL = load_module("wait_test_poll", ".py/klipper/plugins/temperature_wait.py")


class Heater:
    def __init__(self, temperature):
        self.temperature = temperature
        self.target = 0

    def get_temp(self, eventtime):
        return self.temperature, self.target


class Printer:
    config_error = ValueError
    command_error = GCODE.CommandError

    def __init__(self):
        self.objects = {}
        self.events = {}
        self.now = 0
        self.reactor = SimpleNamespace(
            monotonic=lambda: self.now, mutex=contextlib.nullcontext)

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def get_reactor(self):
        return self.reactor

    def get_start_args(self):
        return {}

    def register_event_handler(self, event, callback):
        self.events.setdefault(event, []).append(callback)

    def send_event(self, event, *args):
        for callback in self.events.get(event, ()):
            callback(*args)

    def invoke_shutdown(self, reason):
        raise AssertionError(reason)


class TemperatureWaitTest(unittest.TestCase):
    def setUp(self):
        self.printer = printer = Printer()
        self.gcode = printer.objects["gcode"] = GCODE.GCodeDispatch(printer)
        self.wait = printer.objects["gcode_macro _WAIT_TEMPERATURE"] = SimpleNamespace(
            variables=load_macro(BASE, "_WAIT_TEMPERATURE").variables)
        self.nozzle = Heater(200)
        self.bed = Heater(60)
        printer.objects["extruder"] = SimpleNamespace(get_heater=lambda: self.nozzle)
        printer.objects["heater_bed"] = SimpleNamespace(heater=self.bed)
        printer.objects["heaters"] = SimpleNamespace(
            lookup_heater={"heater_bed": self.bed}.__getitem__)
        config = SimpleNamespace(get_printer=lambda: printer)
        self.context = printer.objects["operation_context"] = CONTEXT.load_config(config)
        self.context.context_types["print"] = CONTEXT.ContextType(
            "print", "Print", cancel_mode="cancelable", on_cancel="CANCEL_PRINT")
        self.plugin = POLL.load_config(config)
        self.effects = []
        self.on_wait = self.on_target = None
        self.cancel_flags = None
        for name in ("_WAIT_TEMPERATURE", "_WAIT_TEMPERATURE_FINAL_CHECK",
                     "_WAIT_TEMPERATURE_RESET_STATE"):
            self.gcode.register_command(name, self.run_macro)
        for name in ("M104", "M140", "WAIT", "SET_GCODE_VARIABLE",
                     "RESPOND", "CANCEL_PRINT", "_RAISE_WITH_PRINT_CANCEL"):
            self.gcode.register_command(name, self.terminal)
        printer.send_event("klippy:ready")
        self.gcode.run_script_from_command(
            "_CONTEXT_BEGIN TYPE=print\n_CONTEXT_STATE NAME=PRINTING")

    def run_macro(self, gcmd):
        rendered = render_macro(BASE, gcmd.get_command(), printer={
            "extruder": {"temperature": self.nozzle.temperature},
            "heater_bed": {"temperature": self.bed.temperature},
            "operation_context": self.context.get_status(self.printer.now),
            "gcode_macro _WAIT_TEMPERATURE": self.wait.variables,
        }, variables=self.wait.variables, params=gcmd.get_command_parameters(),
            rawparams=gcmd.get_raw_command_parameters())
        self.gcode.run_script_from_command(rendered.text)

    def terminal(self, gcmd):
        name = gcmd.get_command()
        if name == "SET_GCODE_VARIABLE":
            self.wait.variables = dict(self.wait.variables, **{
                gcmd.get("VARIABLE"): ast.literal_eval(gcmd.get("VALUE"))})
            return
        self.effects.append(gcmd.get_commandline())
        if name in ("M104", "M140"):
            heater = self.nozzle if name == "M104" else self.bed
            heater.target = gcmd.get_float("S")
            if self.on_target:
                self.on_target()
        elif name == "WAIT":
            self.printer.now += gcmd.get_float("TIME") / 1000.
            if self.on_wait:
                self.on_wait()
        elif name == "CANCEL_PRINT":
            self.cancel_flags = dict(self.wait.variables)
        elif name == "_RAISE_WITH_PRINT_CANCEL":
            raise gcmd.error(gcmd.get("MSG"))

    def wait_for(self, params="CMD=M104 VALUE=200"):
        self.gcode.run_script_from_command("_WAIT_TEMPERATURE " + params)

    def assert_inactive(self):
        self.assertFalse(any(self.wait.variables.values()))

    def test_hot_nozzle_restores_target_without_waiting(self):
        self.wait_for()
        self.assertEqual(self.nozzle.target, 200)
        self.assertNotIn("WAIT TIME=3000", self.effects)
        self.assertEqual(self.context.contexts[-1].current_state, "PRINTING")
        self.assert_inactive()

    def test_cold_bed_yields_then_restores_context(self):
        self.bed.temperature = 20
        seen = []
        def warm_bed():
            seen.append((self.context.contexts[-1].current_state,
                         self.wait.variables["active"]))
            self.bed.temperature = 60
        self.on_wait = warm_bed
        self.wait_for("CMD=M140 VALUE=60")
        self.assertEqual(seen, [("HEATING BED", True)])
        self.assertEqual(self.bed.target, 60)
        self.assertEqual(self.context.contexts[-1].current_state, "PRINTING")
        self.assert_inactive()

    def test_poll_preserves_reported_temperature_rounding_at_boundary(self):
        self.nozzle.temperature = 199.999
        self.wait_for("CMD=M104 VALUE=200 MINIMUM=200 MAXIMUM=201")
        self.assertNotIn("WAIT TIME=3000", self.effects)
        self.assert_inactive()

    def test_cooling_and_one_sided_bounds(self):
        self.nozzle.temperature = 230
        self.on_wait = lambda: setattr(self.nozzle, "temperature", 205)
        self.wait_for("CMD=M104 VALUE=200 MAXIMUM=205")
        self.assertEqual(self.effects.count("WAIT TIME=3000"), 1)
        self.assert_inactive()
        self.effects.clear()
        self.wait_for("CMD=M104 VALUE=200 MINIMUM=190")
        self.assertNotIn("WAIT TIME=3000", self.effects)

    def test_zero_target_does_not_poll(self):
        self.wait_for("CMD=M140 VALUE=0")
        self.assertEqual(self.bed.target, 0)
        self.assertNotIn("WAIT TIME=3000", self.effects)
        self.assert_inactive()

    def test_timeout_keeps_the_existing_rounded_check_budget(self):
        self.nozzle.temperature = 20
        with self.assertRaisesRegex(GCODE.CommandError, "timed out"):
            self.wait_for("CMD=M104 VALUE=200 TIMEOUT=61 DELAY=3000")
        self.assertEqual(self.effects.count("WAIT TIME=3000"), 20)
        self.assert_inactive()

    def test_short_timeout_keeps_zero_check_budget(self):
        with self.assertRaisesRegex(GCODE.CommandError, "timed out"):
            self.wait_for("CMD=M104 VALUE=200 TIMEOUT=1 DELAY=1000")
        self.assertNotIn("M104 S200", self.effects)
        self.assert_inactive()

    def test_m108_cancellation_reads_the_replaced_variable_dictionary(self):
        self.nozzle.temperature = 20
        self.on_wait = lambda: self.gcode.run_script("M108")
        with self.assertRaisesRegex(GCODE.CommandError, "cancelled"):
            self.wait_for()
        self.assertEqual(self.effects.count("WAIT TIME=3000"), 1)
        self.assert_inactive()

    def test_m108_during_target_setting_takes_precedence_over_reached(self):
        self.on_target = lambda: self.gcode.run_script("M108")
        with self.assertRaisesRegex(GCODE.CommandError, "cancelled"):
            self.wait_for()
        self.assert_inactive()

    def test_context_cancellation_clears_flags_before_print_cleanup(self):
        self.nozzle.temperature = 20
        self.on_wait = self.context.request_cancel
        with self.assertRaisesRegex(GCODE.CommandError, "Operation cancelled"):
            self.wait_for()
        self.assertEqual(self.cancel_flags, dict.fromkeys(self.wait.variables, False))
        self.assert_inactive()

    def test_context_cancellation_after_target_setting_is_delivered(self):
        self.on_target = self.context.request_cancel
        with self.assertRaisesRegex(GCODE.CommandError, "Operation cancelled"):
            self.wait_for()
        self.assertIsNotNone(self.cancel_flags)
        self.assert_inactive()

    def test_heater_failure_clears_active_wait_and_preserves_error(self):
        def fail():
            raise GCODE.CommandError("heater rejected target")
        self.on_target = fail
        with self.assertRaisesRegex(GCODE.CommandError, "heater rejected target"):
            self.wait_for()
        self.assert_inactive()

    def test_sensor_read_failure_clears_active_wait(self):
        def fail(_eventtime):
            raise GCODE.CommandError("sensor unavailable")
        self.nozzle.get_temp = fail
        with self.assertRaisesRegex(GCODE.CommandError, "sensor unavailable"):
            self.wait_for()
        self.assert_inactive()

    def test_poll_rejects_bad_parameters_without_leaving_wait_active(self):
        for params in ("CMD=M109 VALUE=200 CHECKS=1 DELAY=1",
                       "CMD=M104 VALUE=200 CHECKS=1 DELAY=1 MINIMUM=210 MAXIMUM=190",
                       "CMD=M104 VALUE=200 CHECKS=bad DELAY=1"):
            with self.subTest(params=params):
                self.wait.variables["active"] = True
                with self.assertRaises(GCODE.CommandError):
                    self.gcode.run_script_from_command("_WAIT_TEMPERATURE_POLL " + params)
                self.assert_inactive()


if __name__ == "__main__":
    unittest.main()
