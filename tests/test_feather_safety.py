## Safety composition and memory contracts for Feather.

import gc
import pathlib
import sys
import tracemalloc
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]
PLUGINS = ROOT / ".py" / "klipper" / "plugins"
sys.path.insert(0, str(PLUGINS))

from feather_safety import SafetyRegistry  # noqa: E402


class SafetyRegistryTest(unittest.TestCase):
    def test_sources_leases_and_armed_reasons_compose(self):
        active = {"heater": False}
        registry = SafetyRegistry(excluded_routes=("home",))
        registry.register_source(
            "heater", lambda _eventtime: active["heater"])

        self.assertFalse(registry.evaluate("menu", 1.0).visible)
        self.assertFalse(registry.evaluate(
            "home", 1.0, ("move-controls",)).visible)
        armed = registry.evaluate("move", 1.0, ("move-controls",))
        self.assertTrue(armed.visible)
        self.assertEqual(armed.armed_reasons, ("move-controls",))

        active["heater"] = True
        heated = registry.evaluate("settings", 2.0)
        self.assertTrue(heated.visible)
        self.assertEqual(heated.global_reasons, ("heater",))

        active["heater"] = False
        outer = registry.activity("gcode")
        inner = registry.activity("gcode")
        self.assertEqual(registry.lease_count, 2)
        self.assertEqual(
            registry.evaluate("network", 3.0).global_reasons, ("gcode",))
        self.assertTrue(inner.release())
        self.assertFalse(inner.release())
        self.assertEqual(registry.lease_count, 1)
        outer.release()
        self.assertEqual(registry.lease_count, 0)

    def test_activity_context_releases_after_exception(self):
        registry = SafetyRegistry()
        with self.assertRaisesRegex(RuntimeError, "failed"):
            with registry.activity("operation"):
                raise RuntimeError("failed")
        self.assertEqual(registry.lease_count, 0)
        self.assertFalse(registry.evaluate("menu", 0.0).visible)

    def test_provider_failure_is_fail_safe_and_recovers(self):
        state = {"broken": True}

        def provider(_eventtime):
            if state["broken"]:
                raise ValueError("bad telemetry")
            return False

        registry = SafetyRegistry()
        registry.register_source("telemetry", provider)
        with mock.patch("feather_safety.logging.exception") as logged:
            self.assertTrue(registry.evaluate("menu", 0.0).visible)
            self.assertTrue(registry.evaluate("menu", 1.0).visible)
        logged.assert_called_once()

        state["broken"] = False
        self.assertFalse(registry.evaluate("menu", 2.0).visible)

    def test_repeated_evaluation_does_not_retain_history(self):
        registry = SafetyRegistry(excluded_routes=("home",))
        registry.register_source("idle", lambda _eventtime: False)
        for index in range(1000):
            with registry.activity("gcode"):
                registry.evaluate("menu", float(index), ("controls",))
        gc.collect()

        tracemalloc.start()
        before = tracemalloc.take_snapshot()
        for index in range(5000):
            with registry.activity("gcode"):
                registry.evaluate("menu", float(index), ("controls",))
        gc.collect()
        after = tracemalloc.take_snapshot()
        tracemalloc.stop()

        retained = sum(max(0, stat.size_diff) for stat in
                       after.compare_to(before, "filename")
                       if stat.traceback[0].filename.endswith(
                           "feather_safety.py"))
        self.assertLess(retained, 128 * 1024)
        self.assertEqual(registry.lease_count, 0)
        self.assertEqual(registry.source_count, 1)


if __name__ == "__main__":
    unittest.main()
