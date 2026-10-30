## Tests for Forge-X Klipper plugin and patch deployment.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import importlib.util
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]
MCU = ROOT / ".py" / "klipper" / "patches" / "mcu.py"
TOOLHEAD = ROOT / ".py" / "klipper" / "patches" / "toolhead.py"
VIRTUAL_SD = (ROOT / ".py" / "klipper" / "patches" / "extras" /
              "virtual_sdcard.py")
ORCA_FIXTURE = (ROOT / ".py" / "klipper" / "plugins" /
                "feather_ui_test" / "fixtures" /
                "context_recovery_open_box.gcode")


class McuTuningTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dependencies = {
            name: types.ModuleType(name)
            for name in ("serialhdl", "msgproto", "pins", "chelper",
                         "clocksync")
        }
        dependencies["serialhdl"].error = RuntimeError
        spec = importlib.util.spec_from_file_location(
            "ff5m_mcu_patch_test", MCU)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, dependencies):
            spec.loader.exec_module(module)
        cls.load_timeout = staticmethod(module._load_trsync_timeout)

    def test_enabled_value_uses_relaxed_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            variables = pathlib.Path(directory) / "variables.cfg"
            variables.write_text(
                "[Variables]\ntune_klipper = 1\n", encoding="utf-8")

            self.assertEqual(self.load_timeout(str(variables)), 0.05)

    def test_disabled_missing_and_invalid_values_use_default(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            disabled = root / "disabled.cfg"
            disabled.write_text(
                "[Variables]\ntune_klipper = 0\n", encoding="utf-8")
            invalid = root / "invalid.cfg"
            invalid.write_text(
                "[Variables]\ntune_klipper = perhaps\n", encoding="utf-8")
            wrong_section = root / "wrong.cfg"
            wrong_section.write_text(
                "[Other]\ntune_klipper = 1\n", encoding="utf-8")

            self.assertEqual(self.load_timeout(str(disabled)), 0.025)
            self.assertEqual(self.load_timeout(str(invalid)), 0.025)
            self.assertEqual(self.load_timeout(str(wrong_section)), 0.025)
            self.assertEqual(
                self.load_timeout(str(root / "missing.cfg")), 0.025)


class ToolheadTuningTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dependencies = {
            name: types.ModuleType(name)
            for name in ("mcu", "chelper", "kinematics",
                         "kinematics.extruder")
        }
        dependencies["kinematics"].extruder = dependencies[
            "kinematics.extruder"]
        spec = importlib.util.spec_from_file_location(
            "ff5m_toolhead_patch_test", TOOLHEAD)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, dependencies):
            spec.loader.exec_module(module)
        cls.load_flush_time = staticmethod(
            module._load_lookahead_flush_time)
        cls.MoveQueue = module.MoveQueue
        cls.ToolHead = module.ToolHead

    def test_enabled_value_uses_tuned_lookahead(self):
        with tempfile.TemporaryDirectory() as directory:
            variables = pathlib.Path(directory) / "variables.cfg"
            variables.write_text(
                "[Variables]\ntune_klipper = 1\n", encoding="utf-8")

            self.assertEqual(self.load_flush_time(str(variables)), 0.150)

    def test_disabled_missing_and_invalid_values_use_stock_lookahead(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            disabled = root / "disabled.cfg"
            disabled.write_text(
                "[Variables]\ntune_klipper = 0\n", encoding="utf-8")
            invalid = root / "invalid.cfg"
            invalid.write_text(
                "[Variables]\ntune_klipper = perhaps\n", encoding="utf-8")
            wrong_section = root / "wrong.cfg"
            wrong_section.write_text(
                "[Other]\ntune_klipper = 1\n", encoding="utf-8")

            self.assertEqual(self.load_flush_time(str(disabled)), 0.5)
            self.assertEqual(self.load_flush_time(str(invalid)), 0.5)
            self.assertEqual(self.load_flush_time(str(wrong_section)), 0.5)
            self.assertEqual(
                self.load_flush_time(str(root / "missing.cfg")), 0.5)

    def test_move_queue_restores_startup_lookahead_after_reset_and_flush(self):
        queue = self.MoveQueue(mock.Mock(), 0.150)

        queue.set_flush_time(2.0)
        queue.reset()
        self.assertEqual(queue.junction_flush, 0.150)

        queue.set_flush_time(2.0)
        queue.flush()
        self.assertEqual(queue.junction_flush, 0.150)

    def test_stall_check_yields_when_buffer_is_not_full(self):
        reactor = mock.Mock()
        reactor.NOW = object()
        reactor.NEVER = object()
        reactor.monotonic.return_value = 10.0
        reactor.get_dispatch_time.return_value = 9.970
        reactor.pause.side_effect = lambda waketime: waketime
        mcu = mock.Mock()
        mcu.estimated_print_time.return_value = 10.0
        toolhead = object.__new__(self.ToolHead)
        toolhead.reactor = reactor
        toolhead.mcu = mcu
        toolhead.special_queuing_state = ""
        toolhead.print_time = 10.0
        toolhead.buffer_time_high = 2.0
        toolhead.can_pause = True
        toolhead.need_check_stall = -1.0

        toolhead._check_stall()

        reactor.pause.assert_called_once_with(reactor.NOW)
        self.assertEqual(toolhead.need_check_stall, toolhead.print_time)

    def test_low_latency_stream_skips_only_fallback_stall_yield(self):
        reactor = mock.Mock()
        reactor.NOW = object()
        reactor.NEVER = object()
        reactor.monotonic.return_value = 10.0
        mcu = mock.Mock()
        mcu.estimated_print_time.return_value = 10.0
        toolhead = object.__new__(self.ToolHead)
        toolhead.reactor = reactor
        toolhead.mcu = mcu
        toolhead.special_queuing_state = ""
        toolhead.print_time = 10.0
        toolhead.buffer_time_high = 2.0
        toolhead.can_pause = True
        toolhead.need_check_stall = -1.0
        toolhead.low_latency_stream_submission = True

        toolhead._check_stall()

        reactor.pause.assert_not_called()
        self.assertEqual(toolhead.need_check_stall, toolhead.print_time)

    def test_shutdown_restores_low_latency_move_flush_time(self):
        toolhead = object.__new__(self.ToolHead)
        toolhead.can_pause = True
        toolhead.move_flush_time = 0.025
        toolhead.low_latency_saved_move_flush_time = 0.073
        toolhead.low_latency_stream_submission = True
        toolhead.move_queue = mock.Mock()

        toolhead._handle_shutdown()

        self.assertEqual(toolhead.move_flush_time, 0.073)
        self.assertIsNone(toolhead.low_latency_saved_move_flush_time)
        self.assertFalse(toolhead.low_latency_stream_submission)
        toolhead.move_queue.reset.assert_called_once_with()

    def test_stall_pause_has_minimum_delay_without_duplicate_yield(self):
        class Reactor:
            NOW = object()
            NEVER = object()

            def __init__(self):
                self.pauses = []

            def monotonic(self):
                return 10.0

            def pause(self, waketime):
                self.pauses.append(waketime)
                return waketime

        reactor = Reactor()
        mcu = mock.Mock()
        mcu.estimated_print_time.side_effect = lambda eventtime: eventtime
        toolhead = object.__new__(self.ToolHead)
        toolhead.reactor = reactor
        toolhead.mcu = mcu
        toolhead.special_queuing_state = ""
        toolhead.print_time = 12.002
        toolhead.buffer_time_high = 2.0
        toolhead.can_pause = True
        toolhead.need_check_stall = -1.0
        toolhead.low_latency_stream_submission = True

        toolhead._check_stall()

        self.assertEqual(reactor.pauses, [10.005])
        self.assertEqual(toolhead.need_check_stall, toolhead.print_time)


class VirtualSDMetadataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "virtual_sdcard_metadata_under_test", VIRTUAL_SD)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cls.virtual_sd = module.VirtualSD.__new__(module.VirtualSD)

    def parse(self, path):
        with open(path, "r", encoding="utf-8", newline="") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            return self.virtual_sd.parse_estimated_time(stream, size)

    def test_reads_orcaslicer_estimated_extrusion_time(self):
        self.assertEqual(self.parse(ORCA_FIXTURE), 150)

    def test_keeps_legacy_estimated_printing_time_compatibility(self):
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".gcode") as stream:
            stream.write("G1 X1\n; estimated printing time = 1h 2m 3s\n")
            stream.flush()

            self.assertEqual(self.parse(stream.name), 3723)


if __name__ == "__main__":
    unittest.main()
