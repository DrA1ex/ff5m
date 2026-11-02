# Behavioral contracts for bounded native shaper calibration.
# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
# This file may be distributed under the terms of the GNU GPLv3 license.

import json
import copy
import math
import os
import pathlib
import select
import shlex
import struct
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / ".py/klipper/plugins"))
sys.path.insert(0, str(ROOT / ".py/klipper"))
from plugins import forge_shaper as plugin
from plugins import shaper_storage as storage


def saved_results(directory):
    return storage.load_snapshot(os.path.join(directory, storage.LAST_RESULT))[0]


def sample(i, rate=800):
    t = i / float(rate)
    return (t, 1000 * math.sin(2 * math.pi * 43 * t) + 80 * math.cos(2 * math.pi * 77 * t),
            650 * math.sin(2 * math.pi * 61 * t) + 30 * math.cos(2 * math.pi * 17 * t),
            9800 + 100 * math.sin(2 * math.pi * 35 * t))


class Reactor:
    monotonic = staticmethod(time.monotonic)

    def __init__(self):
        self.handlers = {}

    def register_fd(self, fd, read, write):
        handler = types.SimpleNamespace(fd=fd, write=write, enabled=False)
        self.handlers[fd] = handler
        return handler

    def set_fd_wake(self, handler, readable, writable):
        handler.enabled = writable

    def unregister_fd(self, handler):
        del self.handlers[handler.fd]

    def pause(self, deadline):
        fds = [fd for fd, handler in self.handlers.items() if handler.enabled]
        _, ready, _ = select.select([], fds, [], max(0., deadline - time.monotonic()))
        for fd in ready:
            self.handlers[fd].write(time.monotonic())
        return time.monotonic()


def make_plugin(directory, binary, options=None):
    reactor = Reactor()
    gcode = types.SimpleNamespace(commands=[], handlers={}, register_immediate_command=lambda name: None)
    gcode.register_command = lambda name, handler, **kw: gcode.handlers.__setitem__(name, handler)
    limits = {"max_accel": 5000., "max_accel_to_decel": 2500.}
    head = types.SimpleNamespace(clock=0., square_corner_velocity=25., motors_enabled=False,
        manual_move=lambda *args: None, wait_moves=lambda: None, dwell=lambda t: None,
        get_status=lambda now: dict(limits))
    head.get_last_move_time = lambda: head.clock
    chip = types.SimpleNamespace(is_measuring=lambda: False)
    chip.api_dump = types.SimpleNamespace()
    chip.api_dump.add_client = lambda client: setattr(chip.api_dump, "client", client)
    settings, pending = [], {}
    def set_config(section, key, value):
        settings.append((section, key, value))
        pending.setdefault(section, {})[key] = value
    shaping = types.SimpleNamespace(enabled=True)
    shaping.enable_shaping = lambda: setattr(shaping, "enabled", True)
    sweep = types.SimpleNamespace(fail_axis=None, cancel_axis=None,
        get_start_test_points=lambda: [(0., 0., 100.)], prepare_test=lambda gcmd: None,
        freq_start=5., freq_end=100.)
    tester = FakeTester()
    tester.test, tester.accel_chips, tester.move_speed, tester.max_smoothing = sweep, [("xy", chip)], 50., None
    objects = {"gcode": gcode, "toolhead": head, "resonance_tester": tester,
               "input_shaper": shaping,
               "configfile": types.SimpleNamespace(set=set_config,
                   get_status=lambda now: {"save_config_pending_items": pending})}
    events = {}
    def register_event(event, handler):
        events.setdefault(event, []).append(handler)
    def send_event(event, *args):
        for handler in events.get(event, ()):
            handler(*args)
    def execute(command):
        gcode.commands.append(command)
        name, *arguments = shlex.split(command)
        values = dict(argument.split("=", 1) for argument in arguments)
        if name in gcode.handlers:
            return gcode.handlers[name](FakeCommand(values))
        if name == "SET_VELOCITY_LIMIT":
            limits.update(max_accel=float(values["ACCEL"]), max_accel_to_decel=float(values["ACCEL_TO_DECEL"]))
        elif name == "M84":
            head.motors_enabled = False
        else:
            raise AssertionError("Unexpected G-code effect: " + command)
    gcode.run_script_from_command = execute
    printer = types.SimpleNamespace(get_reactor=lambda: reactor,
        lookup_object=lambda name, default=None: objects.get(name, default),
        register_event_handler=register_event, send_event=send_event)
    options = dict({"executable": str(binary), "directory": str(directory)}, **(options or {}))
    config = types.SimpleNamespace(get_printer=lambda: printer,
        get=lambda key, default: options.get(key, default),
        getfloat=lambda key, default, **kwargs: default,
        getboolean=lambda key, default: default)
    instance = plugin.ForgeShaper(config)
    sweep.statuses = []
    sweep.observe = lambda: None
    def run(axis, command):
        start = head.clock
        before = dict(limits)
        head.motors_enabled = True
        shaping.enabled = False
        limits.update(max_accel=10000., max_accel_to_decel=10000.)
        command.respond_info("Testing frequency 43 Hz")
        sweep.statuses.append(instance.get_status(reactor.monotonic()))
        sweep.observe()
        if sweep.fail_axis == axis.axis:
            raise RuntimeError("sweep failed")
        for offset in range(0, 2400, 80):
            chip.api_dump.client.send({"params": {"data": [(start + sample(i)[0],) + sample(i)[1:]
                for i in range(offset, offset + 80)]}})
            reactor.pause(reactor.monotonic() + .001)
        if sweep.cancel_axis == axis.axis:
            instance.cancel()
        command.respond_info("Sweep finished")
        head.clock = start + 3.
        limits.update(before)
        shaping.enabled = True
    sweep.run_test = run
    return instance, settings, gcode, sweep, objects


class NativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.folder = pathlib.Path(cls.temporary.name)
        executable = os.environ.get("FORGE_SHAPER_HELPER")
        if not executable:
            cls.temporary.cleanup()
            raise unittest.SkipTest("Set FORGE_SHAPER_HELPER to the host native executable")
        cls.binary = pathlib.Path(executable)
        cls.reference = json.loads((ROOT / "tests/fixtures/shaper_reference.json").read_text())
        cls.zvd_reference = json.loads((ROOT / "tests/fixtures/shaper_zvd_reference.json").read_text())

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.job = tempfile.TemporaryDirectory(dir=self.folder)
        self.addCleanup(self.job.cleanup)
        self.directory = pathlib.Path(self.job.name)
        self.source = self.directory / "samples.bin"
        self.output = self.directory / "result.json"

    def write_samples(self, count=2400):
        with self.source.open("wb") as stream:
            stream.write(b"FXSAMP1\n")
            for i in range(count):
                stream.write(struct.pack("<4d", *sample(i)))

    def run_native(self, *args):
        return subprocess.run([str(self.binary)] + list(args), text=True, capture_output=True, timeout=10)

    def assert_curve(self, actual, expected):
        self.assertEqual(len(actual), len(expected))
        scale = max(max(expected), 1.)
        for a, b in zip(actual, expected):
            self.assertAlmostEqual(a / scale, b / scale, delta=2e-10)

    def test_matches_existing_klipper_psd_and_all_fitted_methods(self):
        self.write_samples()
        for reference, zvd in zip(self.reference["cases"], self.zvd_reference["cases"]):
            with self.subTest(scv=reference["scv"], limit=reference["max_smoothing"]):
                process = self.run_native("analyze", str(self.source), str(self.output),
                    "--scv", str(reference["scv"]), "--max-smoothing", str(reference["max_smoothing"] or 0))
                self.assertEqual(process.returncode, 0, process.stderr)
                result = storage.load_result(self.output)
                self.assertEqual(result["samples"], 2400)
                self.assertEqual(result["windows"], 8)
                self.assertEqual(result["recommended"], reference["recommended"])
                self.assertEqual(tuple(s['name'] for s in result['shapers']), plugin.METHODS + ('zvd',))
                for key in ("freq_bins", "psd_sum", "psd_x", "psd_y", "psd_z"):
                    self.assert_curve(result[key], reference[key])
                for actual, expected in zip(result["shapers"], reference["shapers"]):
                    self.assertEqual(actual["name"], expected["name"])
                    for key in ("freq", "vibrs", "smoothing", "score", "max_accel"):
                        self.assertAlmostEqual(actual[key], expected[key], delta=2e-8)
                    self.assert_curve(actual["response"], expected["response"])
                actual = result['shapers'][-1]
                self.assertTrue(actual['reference_only'])
                for key in ('freq', 'vibrs', 'smoothing', 'score', 'max_accel'):
                    self.assertAlmostEqual(actual[key], zvd[key], delta=2e-8)
                self.assert_curve(actual['response'], zvd['response'])

    def test_capture_preserves_samples_and_analysis_honors_time_bounds(self):
        payload = b"".join(struct.pack("<4d", *sample(i)) for i in range(3200))
        process = subprocess.run([str(self.binary), "capture", str(self.source)], input=payload,
                                 capture_output=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(self.source.read_bytes(), b"FXSAMP1\n" + payload)
        result = self.run_native("analyze", str(self.source), str(self.output), "--start", "1", "--end", "3")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(storage.load_result(self.output)["samples"], 1601)

    def test_csv_replay_matches_binary_and_does_not_require_numpy(self):
        self.write_samples()
        csv = self.directory / "recording.csv"
        with csv.open("w") as output:
            output.write("#time,accel_x,accel_y,accel_z\n")
            for i in range(2400):
                output.write(",".join("%.17g" % v for v in sample(i)) + "\n")
        for mode, path in (("analyze", self.source), ("analyze-csv", csv)):
            result = self.run_native(mode, str(path), str(self.output))
            self.assertEqual(result.returncode, 0, result.stderr)
            actual = storage.load_result(self.output)
            if mode == "analyze":
                reference = actual
            else:
                self.assertEqual(actual, reference)

    def test_invalid_input_fails_without_publishing_or_overwriting_results(self):
        cases = (b"bad", b"FXSAMP1\n" + b"x", b"FXSAMP1\n" + struct.pack("<4d", 0, float("nan"), 1, 2),
                 b"FXSAMP1\n" + struct.pack("<4d", 1, 1, 2, 3) * 900)
        self.output.write_text("previous result")
        for value in cases:
            self.source.write_bytes(value)
            result = self.run_native("analyze", str(self.source), str(self.output))
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.output.read_text(), "previous result")
            self.assertFalse(pathlib.Path(str(self.output) + ".part").exists())
        process = subprocess.run([str(self.binary), "capture", str(self.directory / "partial")],
                                 input=b"x", capture_output=True, timeout=10)
        self.assertNotEqual(process.returncode, 0)
        self.assertFalse((self.directory / "partial").exists())

    def test_live_capture_has_no_recording_sized_python_list(self):
        reactor = Reactor()
        capture = plugin.CaptureClient(reactor, str(self.binary), str(self.source), str(self.directory / "capture.log"))
        try:
            for offset in range(0, 2400, 80):
                capture.send({"params": {"data": [sample(i) for i in range(offset, offset + 80)]}})
                reactor.pause(reactor.monotonic() + .001)
            capture.finish(lambda: False)
            self.assertLessEqual(capture.peak_pending, plugin.MAX_PENDING_BYTES)
            self.assertEqual(len(capture.pending), 0)
            self.assertEqual(self.source.stat().st_size, 8 + 2400 * 32)
        finally:
            capture.close()
        self.assertEqual(reactor.handlers, {})

    def test_slow_writer_fails_at_queue_bound_and_sensor_errors_fail_explicitly(self):
        reactor = Reactor()
        capture = plugin.CaptureClient(reactor, str(self.binary), str(self.source), str(self.directory / "capture.log"))
        try:
            with mock.patch.object(plugin.os, "write", side_effect=BlockingIOError):
                for offset in range(0, 2400, 80):
                    capture.send({"params": {"data": [sample(i) for i in range(offset, offset + 80)]}})
            self.assertTrue(capture.error)
            self.assertTrue(capture.is_closed())
            self.assertLessEqual(len(capture.pending), plugin.MAX_PENDING_BYTES)
            accepted = capture.samples
            capture.send({'params': {'data': [sample(2400)]}})
            self.assertEqual(capture.samples, accepted)
            with self.assertRaises(RuntimeError):
                capture.finish(lambda: False)
        finally:
            capture.close()
        other = plugin.CaptureClient(
            reactor, str(self.binary), str(self.directory / "other"), str(self.directory / "other.log"))
        try:
            other.send({"params": {"data": [sample(0)], "overflows": 1}})
            self.assertEqual(other.overflows, 1)
            self.assertTrue(other.error)
            self.assertTrue(other.is_closed())
            self.assertEqual(other.samples, 0)
            with self.assertRaises(RuntimeError):
                other.finish(lambda: False)
        finally:
            other.close()

    def test_abrupt_child_exit_and_cancellation_are_reaped(self):
        reactor = Reactor()
        worker = plugin.NativeProcess(reactor, [sys.executable, "-c", "import os; os._exit(42)"],
                                      str(self.directory / "exit.log"))
        try:
            with self.assertRaises(RuntimeError):
                worker.wait(lambda: False, 3.)
        finally:
            worker.close()
        self.assertEqual(worker.process.returncode, 42)
        worker = plugin.NativeProcess(reactor, [sys.executable, "-c", "import time; time.sleep(30)"],
                                      str(self.directory / "cancel.log"))
        try:
            with self.assertRaises(RuntimeError):
                worker.wait(lambda: True, 3.)
        finally:
            worker.close()
        self.assertIsNotNone(worker.process.returncode)

    def make_plugin(self):
        return make_plugin(self.directory, self.binary)

    def test_complete_job_publishes_two_axes_and_only_explicit_apply_stages_settings(self):
        instance, settings, gcode, sweep, objects = self.make_plugin()
        with mock.patch.object(plugin, "disk_directory", return_value=str(self.directory)):
            instance.cmd_CALIBRATE(FakeCommand({}))
        self.assertEqual(instance.state, "complete")
        self.assertFalse(instance.busy)
        self.assertEqual(set(instance.record.results), {"x", "y"})
        self.assertEqual(settings, [])
        self.assertEqual(list(self.directory.rglob("*.samples")), [])
        self.assertEqual([s["axis"] for s in sweep.statuses], ["x", "y"])
        self.assertEqual([s["frequency"] for s in sweep.statuses], [43., 43.])
        self.assertEqual(sweep.statuses[1]["completed_axes"], ("x",))
        status = instance.get_status(instance.job.finished_at + 20.)
        self.assertEqual(status["requested_axes"], ("x", "y"))
        self.assertEqual(status["completed_axes"], ("x", "y"))
        self.assertEqual(status["samples"], 2400)
        self.assertEqual(status["job_id"], 1)
        self.assertEqual(status["elapsed"], instance.job.finished_at - instance.job.started_at)
        instance.cmd_APPLY(FakeCommand({"AXIS": "x", "SHAPER": "ei"}))
        self.assertEqual(settings[0], ("input_shaper", "shaper_type_x", "ei"))
        self.assertEqual(settings[1][1], "shaper_freq_x")
        self.assertFalse(objects["toolhead"].motors_enabled)

    def test_last_result_survives_restart_and_loading_does_not_measure(self):
        instance, settings, _, _, _ = self.make_plugin()
        with mock.patch.object(plugin, "disk_directory", return_value=str(self.directory)):
            instance.cmd_CALIBRATE(FakeCommand({}))
        path = self.directory / storage.LAST_RESULT
        self.assertTrue(path.read_bytes().startswith(storage.SAVED_MAGIC))
        self.assertLess(path.stat().st_size, len(json.dumps(instance.record.results)))
        self.assertEqual({p.name for p in self.directory.iterdir()}, {storage.LAST_RESULT})
        restored, settings, _, sweep, _ = self.make_plugin()
        self.assertEqual(restored.state, "idle")
        self.assertIsNone(restored.record)
        with mock.patch("subprocess.Popen", side_effect=AssertionError("Started measurement")):
            restored.restore_saved()
        self.assertEqual(restored.state, "complete")
        self.assertEqual(restored.record, instance.record)
        self.assertEqual(sweep.statuses, [])
        self.assertEqual(settings, [])
        restored.cmd_APPLY(FakeCommand({"AXIS": "x", "SHAPER": "ei"}))
        self.assertEqual(settings[0], ("input_shaper", "shaper_type_x", "ei"))
        before = list(settings)
        with self.assertRaises(RuntimeError):
            restored.cmd_APPLY(FakeCommand({"AXIS": "x", "SHAPER": "zvd"}))
        self.assertEqual(settings, before)

    def test_load_without_saved_results_keeps_the_plugin_idle(self):
        instance, _, _, _, _ = self.make_plugin()
        with self.assertRaises(ValueError):
            instance.restore_saved()
        self.assertEqual(instance.state, "idle")
        self.assertIsNone(instance.record)

    def test_history_references_previous_for_latest_and_latest_for_an_older_record(self):
        instance, _, _, _, _ = self.make_plugin()
        with mock.patch.object(plugin, 'disk_directory', return_value=str(self.directory)):
            for timestamp in (10., 20., 30.):
                with mock.patch.object(plugin.time, 'time', return_value=timestamp):
                    instance.cmd_CALIBRATE(FakeCommand({}))
        entries = instance.get_history()
        latest = instance.record
        self.assertEqual(latest.saved_at, 30.)
        self.assertEqual(instance.reference.saved_at, 20.)
        with mock.patch('subprocess.Popen', side_effect=AssertionError('Started calibration')):
            instance.restore_saved(entries[-1]['id'])
        self.assertEqual(instance.record.saved_at, 10.)
        self.assertEqual(instance.reference, latest)
        instance.restore_saved()
        self.assertEqual(instance.record, latest)
        self.assertEqual(instance.reference.saved_at, 20.)
        with self.assertRaises(ValueError):
            instance.restore_saved('0' * 64)

    def test_damaged_reference_does_not_hide_the_selected_record(self):
        instance, _, _, _, _ = self.make_plugin()
        with mock.patch.object(plugin, 'disk_directory', return_value=str(self.directory)):
            for timestamp in (10., 20.):
                with mock.patch.object(plugin.time, 'time', return_value=timestamp):
                    instance.cmd_CALIBRATE(FakeCommand({}))
        load = instance.history.load
        previous = instance.reference.id
        def damaged(result_id):
            if result_id == previous:
                raise ValueError("Invalid saved shapers")
            return load(result_id)
        with mock.patch.object(instance.history, 'load', side_effect=damaged):
            instance.restore_saved()
        self.assertEqual(instance.state, 'complete')
        self.assertEqual(instance.record.saved_at, 20.)
        self.assertIsNone(instance.reference)

    def test_failed_new_job_keeps_previous_snapshot_and_success_replaces_it(self):
        instance, _, _, sweep, _ = self.make_plugin()
        with mock.patch.object(plugin, "disk_directory", return_value=str(self.directory)):
            instance.cmd_CALIBRATE(FakeCommand({}))
            before = (self.directory / storage.LAST_RESULT).read_bytes()
            sweep.fail_axis = 'y'
            with self.assertRaises(RuntimeError):
                instance.cmd_CALIBRATE(FakeCommand({}))
            self.assertEqual((self.directory / storage.LAST_RESULT).read_bytes(), before)
            sweep.fail_axis = None
            instance.cmd_CALIBRATE(FakeCommand({'AXIS': 'y'}))
        self.assertEqual(set(saved_results(self.directory)), {'y'})
        self.assertEqual(len(instance.get_history()), 2)
        self.assertEqual(len(list(self.directory.glob('*.fxs'))), 2)

    def test_cleanup_failure_does_not_discard_a_completed_calibration(self):
        instance, _, _, _, _ = self.make_plugin()
        save = storage.save_results
        def save_and_replace_job(*args):
            save(*args)
            job = next(self.directory.glob('job-*'))
            # Simulate the work directory disappearing during housekeeping.
            plugin.shutil.rmtree(job)
            job.write_text('replaced')
        with mock.patch.object(plugin, 'disk_directory', return_value=str(self.directory)), \
                mock.patch.object(storage, 'save_results', side_effect=save_and_replace_job):
            instance.cmd_CALIBRATE(FakeCommand({}))
        self.assertEqual(instance.state, 'complete')
        self.assertEqual(instance.error, '')
        self.assertEqual(saved_results(self.directory), instance.record.results)

    def test_failed_or_cancelled_second_axis_publishes_nothing_and_restores_limits(self):
        for attribute, expected in (("fail_axis", "error"), ("cancel_axis", "cancelled")):
            with self.subTest(outcome=expected):
                instance, settings, gcode, sweep, objects = self.make_plugin()
                setattr(sweep, attribute, "y")
                with mock.patch.object(plugin, "disk_directory", return_value=str(self.directory)):
                    with self.assertRaises(RuntimeError):
                        instance.cmd_CALIBRATE(FakeCommand({}))
                self.assertEqual(instance.state, expected)
                self.assertIsNone(instance.record)
                self.assertFalse(instance.busy)
                self.assertEqual(settings, [])
                self.assertEqual(objects["toolhead"].get_status(0.),
                                 {"max_accel": 5000., "max_accel_to_decel": 2500.})
                self.assertTrue(objects["input_shaper"].enabled)
                self.assertFalse(objects["toolhead"].motors_enabled)
                self.assertEqual(list(self.directory.glob("job-*")), [])

    def test_storage_preflight_failure_is_a_command_error_before_capture(self):
        instance, settings, gcode, sweep, objects = self.make_plugin()
        with mock.patch.object(plugin, "disk_directory", side_effect=ValueError("RAM filesystem")):
            with self.assertRaises(RuntimeError):
                instance.cmd_CALIBRATE(FakeCommand({}))
        self.assertFalse(instance.busy)
        self.assertEqual(sweep.statuses, [])
        self.assertEqual(settings, [])
        self.assertEqual(gcode.commands, [])
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_active_print_is_rejected_before_capture(self):
        instance, settings, gcode, sweep, objects = self.make_plugin()
        objects["print_stats"] = types.SimpleNamespace(get_status=lambda now: {"state": "printing"})
        with self.assertRaises(RuntimeError):
            instance.cmd_CALIBRATE(FakeCommand({}))
        self.assertFalse(instance.busy)
        self.assertEqual(sweep.statuses, [])
        self.assertEqual(settings, [])
        self.assertEqual(gcode.commands, [])
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_active_sd_stream_is_rejected_even_before_print_stats_catches_up(self):
        instance, settings, gcode, sweep, objects = self.make_plugin()
        objects['print_stats'] = types.SimpleNamespace(get_status=lambda now: {'state': 'standby'})
        objects['virtual_sdcard'] = types.SimpleNamespace(is_active=lambda: True)
        with self.assertRaises(RuntimeError):
            instance.cmd_CALIBRATE(FakeCommand({}))
        self.assertFalse(instance.busy)
        self.assertEqual(sweep.statuses, [])
        self.assertEqual(settings, [])
        self.assertEqual(gcode.commands, [])
        self.assertEqual(list(self.directory.iterdir()), [])


class TestAxis:
    def __init__(self, axis):
        self.axis = axis

    def matches(self, chip_axis):
        return self.axis in chip_axis


class FakeTester:
    pass


class FakeCommand:
    error = RuntimeError

    def __init__(self, values):
        self.values = values

    def get(self, name, default=None):
        return self.values.get(name, default)

    def get_float(self, name, default, **kwargs):
        return float(self.get(name, default))

    def get_int(self, name, default, **kwargs):
        return int(self.get(name, default))

    def respond_info(self, message):
        pass


class ResultValidationTests(unittest.TestCase):
    def test_backend_import_does_not_depend_on_feather_registering_the_plugin_path(self):
        code = "import sys; sys.path.insert(0, %r); from plugins import forge_shaper" % str(ROOT/'.py/klipper')
        completed = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True, timeout=5)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())

    def test_snapshot_atomic_failure_preserves_previous_file_and_cleans_temporary(self):
        result=json.loads((ROOT/'tests/fixtures/shaper_reference.json').read_text())['cases'][0]
        result.update(schema=1,kind='input_shaper',axis='x')
        with tempfile.TemporaryDirectory() as directory:
            storage.save_results(directory,{'x':result},1.)
            before=pathlib.Path(directory,storage.LAST_RESULT).read_bytes()
            with mock.patch.object(plugin.os,'replace',side_effect=OSError('disk failed')):
                with self.assertRaises(OSError):storage.save_results(directory,{'x':result},2.)
            self.assertEqual(pathlib.Path(directory,storage.LAST_RESULT).read_bytes(),before)
            self.assertEqual(list(pathlib.Path(directory).glob('*.part')),[])

    def test_saved_result_rejects_corruption_and_oversized_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path=pathlib.Path(directory,storage.LAST_RESULT)
            for blob in (b'bad',storage.SAVED_MAGIC+b'bad data', b'x'*(storage.MAX_SAVED_BYTES+1)):
                path.write_bytes(blob)
                with self.assertRaises((OSError,ValueError,EOFError)):
                    saved_results(directory)

    def test_snapshot_checksums_protect_numeric_arrays(self):
        result=json.loads((ROOT/'tests/fixtures/shaper_reference.json').read_text())['cases'][0]
        result.update(schema=1,kind='input_shaper',axis='x')
        with tempfile.TemporaryDirectory() as directory:
            storage.save_results(directory,{'x':result},1.)
            path=pathlib.Path(directory,storage.LAST_RESULT)
            blob=bytearray(path.read_bytes());blob[-40]^=1;path.write_bytes(blob)
            with self.assertRaises(ValueError):
                saved_results(directory)

    def test_old_results_and_reference_zvd_validate_but_zvd_cannot_be_recommended(self):
        result = json.loads((ROOT / 'tests/fixtures/shaper_reference.json').read_text())['cases'][0]
        result.update(schema=1, kind='input_shaper', axis='x')
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'result.json'
            path.write_text(json.dumps(result))
            self.assertEqual(len(storage.load_result(path)['shapers']), 5)
            result['shapers'].append(dict(result['shapers'][0],name='zvd',reference_only=True))
            path.write_text(json.dumps(result))
            self.assertEqual(len(storage.load_result(path)['shapers']), 6)
            invalid = copy.deepcopy(result)
            invalid['recommended'] = 'zvd'
            path.write_text(json.dumps(invalid))
            with self.assertRaises(ValueError):
                storage.load_result(path)
            for marker in (False, None, 'true', 1):
                invalid = copy.deepcopy(result)
                invalid['shapers'][-1]['reference_only'] = marker
                path.write_text(json.dumps(invalid))
                with self.assertRaises(ValueError):
                    storage.load_result(path)

    def test_direct_apply_rejects_zvd_even_with_a_forged_nonreference_result(self):
        with tempfile.TemporaryDirectory() as directory:
            instance, settings, gcode, _, _ = make_plugin(directory, sys.executable)
            instance.record = plugin.Record('forged', 1., {
                'x': {'shapers': [dict(name='zvd', freq=60., reference_only=False)]}})
            with self.assertRaises(RuntimeError):
                instance.cmd_APPLY(FakeCommand({'AXIS': 'x', 'SHAPER': 'zvd'}))
            self.assertEqual(settings, [])
            self.assertEqual(gcode.commands, [])

    def test_ram_backed_and_symlinked_recording_directories_are_rejected(self):
        mounts = "disk / ext4 rw 0 0\nnone %s tmpfs rw 0 0\n" % os.path.realpath("/tmp")
        with mock.patch.object(storage.sys, "platform", "linux"):
            with mock.patch("builtins.open", mock.mock_open(read_data=mounts)):
                with self.assertRaises(ValueError):
                    plugin.disk_directory("/tmp/shaper")
                with mock.patch.object(plugin.os.path, "realpath", return_value=os.path.realpath("/tmp/shaper")):
                    with self.assertRaises(ValueError):
                        plugin.disk_directory("/some/link")

    def test_untrusted_results_are_bounded_and_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "result.json"
            path.write_bytes(b"x" * (storage.MAX_RESULT_BYTES + 1))
            with self.assertRaises(ValueError):
                storage.load_result(path)
            path.write_text('{"schema": 99}')
            with self.assertRaises(ValueError):
                storage.load_result(path)


if __name__ == "__main__":
    unittest.main()
