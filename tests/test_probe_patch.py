## Tests for Forge-X probe timeout and lifecycle handling.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import importlib.util
import pathlib
import sys
import types
import unittest


ROOT = pathlib.Path(__file__).parents[1]
MODULE_PATH = (ROOT / ".py" / "klipper" / "patches" / "extras" /
               "probe.py")
PACKAGE_NAME = "forge_x_probe_test_package"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = []
manual_probe = types.ModuleType(PACKAGE_NAME + ".manual_probe")
pins = types.ModuleType("pins")
pins.error = RuntimeError
previous_modules = dict(
    (name, sys.modules.get(name)) for name in
    (PACKAGE_NAME, PACKAGE_NAME + ".manual_probe", "pins"))
sys.modules[PACKAGE_NAME] = package
sys.modules[PACKAGE_NAME + ".manual_probe"] = manual_probe
sys.modules["pins"] = pins
try:
    SPEC = importlib.util.spec_from_file_location(
        PACKAGE_NAME + ".probe", MODULE_PATH)
    PROBE = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(PROBE)
finally:
    for name, previous in previous_modules.items():
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


class ProbeCommand:
    def get_float(self, _name, default, **_kwargs):
        return default

    def get_int(self, _name, default, **_kwargs):
        return default

    def get(self, _name, default=None):
        return default

    @staticmethod
    def error(message):
        return RuntimeError(message)

    @staticmethod
    def respond_info(_message):
        pass


class ProbePatchTest(unittest.TestCase):
    def test_failed_probe_always_closes_its_multi_probe_lifecycle(self):
        probe = PROBE.PrinterProbe.__new__(PROBE.PrinterProbe)
        probe.speed = 5.0
        probe.lift_speed = 5.0
        probe.sample_count = 2
        probe.sample_retract_dist = 2.0
        probe.samples_tolerance = 0.1
        probe.samples_retries = 0
        probe.samples_result = "average"
        probe.multi_probe_pending = False
        probe.printer = type("Printer", (), {
            "lookup_object": staticmethod(lambda _name: type("Toolhead", (), {
                "get_position": staticmethod(lambda: [0.0, 0.0, 10.0, 0.0])
            })())
        })()
        lifecycle = []

        def begin():
            lifecycle.append("begin")
            probe.multi_probe_pending = True

        def end():
            lifecycle.append("end")
            probe.multi_probe_pending = False

        probe.multi_probe_begin = begin
        probe.multi_probe_end = end
        probe._probe = lambda _speed: (_ for _ in ()).throw(
            RuntimeError("Timeout during endstop homing"))

        with self.assertRaisesRegex(RuntimeError, "Timeout"):
            probe.run_probe(ProbeCommand())

        self.assertEqual(lifecycle, ["begin", "end"])
        self.assertFalse(probe.multi_probe_pending)

    def test_core_homing_timeout_is_reported_as_an_aborted_probe(self):
        class ProbeError(RuntimeError):
            pass

        class Toolhead:
            @staticmethod
            def get_status(_eventtime):
                return {"homed_axes": "xyz"}

            @staticmethod
            def get_position():
                return [0.0, 0.0, 10.0, 0.0]

        class Homing:
            @staticmethod
            def probing_move(_mcu_probe, _position, _speed):
                raise ProbeError("Timeout during endstop homing")

        class Printer:
            command_error = ProbeError

            @staticmethod
            def get_reactor():
                return type("Reactor", (), {
                    "monotonic": staticmethod(lambda: 0.0)})()

            @staticmethod
            def lookup_object(name):
                return {"toolhead": Toolhead(), "homing": Homing()}[name]

        probe = PROBE.PrinterProbe.__new__(PROBE.PrinterProbe)
        probe.printer = Printer()
        probe.mcu_probe = object()
        probe.z_position = -10.0

        with self.assertRaisesRegex(
                ProbeError, "aborted before later macro moves"):
            probe._probe(5.0)


if __name__ == "__main__":
    unittest.main()
