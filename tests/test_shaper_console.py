# Universal shaper inspection and legacy PNG export contracts.
# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
# This file may be distributed under the terms of the GNU GPLv3 license.

import copy
import json
import os
from pathlib import Path
import shlex
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock
import zlib

from tests.test_forge_shaper import FakeCommand, Reactor, make_plugin, plugin, storage
from tests.test_operation_context import CONTEXT, FakeConfig
from tests.gcode_macro_harness import MacroExecution

ROOT = Path(__file__).parents[1]
PLOT = ROOT / ".root/zshaper/calibrate_shaper.py"
PLOT_PYTHON = os.environ.get("FORGE_SHAPER_PLOT_PYTHON")


class Command(FakeCommand):
    def __init__(self, **values):
        super().__init__(values)
        self.messages = []

    def respond_info(self, message):
        self.messages.append(message)


def png_contents(path):
    blob = path.read_bytes()
    if not blob.startswith(b"\x89PNG\r\n\x1a\n"):
        raise AssertionError("Not a PNG image")
    chunks, offset = {}, 8
    while offset < len(blob):
        size, = struct.unpack_from(">I", blob, offset)
        name = blob[offset + 4:offset + 8]
        data = blob[offset + 8:offset + 8 + size]
        crc, = struct.unpack_from(">I", blob, offset + 8 + size)
        if zlib.crc32(name + data) != crc:
            raise AssertionError("Damaged PNG chunk")
        chunks.setdefault(name, []).append(data)
        offset += size + 12
    return chunks


class ShaperConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        reference = json.loads((ROOT / "tests/fixtures/shaper_reference.json").read_text())["cases"][0]
        self.results = {axis: dict(copy.deepcopy(reference), schema=1, kind="input_shaper", axis=axis)
                        for axis in ("x", "y")}
        self.settings, self.commands = [], {}
        self.gcode = types.SimpleNamespace(register_command=lambda name, handler, **kwargs:
                                           self.commands.__setitem__(name, handler),
                                           register_immediate_command=lambda name: None,
                                           run_script_from_command=mock.Mock(side_effect=AssertionError("G-code effect")))
        self.objects = {"gcode": self.gcode, "configfile": types.SimpleNamespace(
            set=lambda *args: self.settings.append(args))}
        printer = types.SimpleNamespace(get_reactor=Reactor,
            lookup_object=lambda name, default=None: self.objects.get(name, default),
            register_event_handler=lambda *args: None)
        self.options = {"directory": str(self.directory)}
        if PLOT_PYTHON:
            self.options["plot_command"] = " ".join(shlex.quote(str(p)) for p in (PLOT_PYTHON, PLOT, "--history"))
        config = types.SimpleNamespace(get_printer=lambda: printer,
            get=lambda key, default: self.options.get(key, default),
            getfloat=lambda key, default, **kwargs: default,
            getboolean=lambda key, default: default)
        self.instance = plugin.ForgeShaper(config)

    def save(self, timestamp=1.):
        return self.instance.history.save(self.results, timestamp)[0]

    def inspect(self, name, **values):
        command = Command(**values)
        self.commands[name](command)
        return "\n".join(command.messages)

    def test_apply_old_measurement_does_not_switch_the_open_record(self):
        older = self.save(1.)
        self.save(2.)
        self.instance.restore_saved()
        opened = self.instance.record
        self.inspect("FORGE_SHAPER_APPLY", RESULT=older["id"], AXIS="x", SHAPER="ei")
        self.assertIs(self.instance.record, opened)
        self.assertEqual(self.settings[0], ("input_shaper", "shaper_type_x", "ei"))
        self.assertEqual(len(self.settings), 2)

    def test_invalid_second_axis_recommendation_cannot_stage_the_first_axis(self):
        self.instance.record = plugin.Record("invalid", 1., self.results)
        self.results["y"]["recommended"] = "zvd"
        with self.assertRaises(RuntimeError):
            self.inspect("FORGE_SHAPER_APPLY", SHAPER="auto")
        self.assertEqual(self.settings, [])

    def test_inspection_reports_the_requested_result_without_applying_it(self):
        entry = self.save()
        report = self.inspect("FORGE_SHAPER_RESULTS", AXIS="x")
        self.assertIn(entry["id"], report)
        self.assertNotIn("SAVE_CONFIG", report)
        self.assertIsNone(self.instance.record)
        self.assertEqual(self.settings, [])
        self.gcode.run_script_from_command.assert_not_called()

    def test_apply_without_parameters_uses_saved_recommendations_after_restart(self):
        self.save()
        self.inspect("FORGE_SHAPER_APPLY")
        for axis, result in self.results.items():
            fit = next(f for f in result["shapers"] if f["name"] == result["recommended"])
            self.assertIn(("input_shaper", "shaper_type_" + axis, fit["name"]), self.settings)
            self.assertIn(("input_shaper", "shaper_freq_" + axis, "%.1f" % fit["freq"]), self.settings)
        self.assertEqual(len(self.settings), 4)
        self.assertIsNone(self.instance.record)

    def test_history_ids_select_records_but_inspection_keeps_the_open_record(self):
        older = self.save(1.)
        newer = self.save(2.)
        history = self.inspect("FORGE_SHAPER_HISTORY")
        self.assertLess(history.index(newer["id"]), history.index(older["id"]))
        self.instance.restore_saved(older["id"])
        opened, reference, revision = self.instance.record, self.instance.reference, self.instance.revision
        self.assertEqual(opened.id, older["id"])
        self.assertEqual(reference.id, newer["id"])
        self.assertIn(older["id"], self.inspect("FORGE_SHAPER_RESULTS"))
        self.assertIn(newer["id"], self.inspect("FORGE_SHAPER_RESULTS", RESULT=newer["id"]))
        self.assertIs(self.instance.record, opened)
        self.assertIs(self.instance.reference, reference)
        self.assertEqual(self.instance.revision, revision)
        self.assertEqual(self.settings, [])

    def test_invalid_results_axes_and_busy_jobs_have_no_effects(self):
        self.inspect("FORGE_SHAPER_HISTORY")
        self.assertEqual(self.instance.get_history(), [])
        with self.assertRaises(RuntimeError):
            self.inspect("FORGE_SHAPER_RESULTS")
        self.results.pop("y")
        self.save()
        for values in ({"AXIS": "z"}, {"AXIS": "xy"}, {"RESULT": "../last.fxs"}):
            with self.subTest(values=values), self.assertRaises(RuntimeError):
                self.inspect("FORGE_SHAPER_RESULTS", **values)
        self.instance.busy = True
        for name in ("FORGE_SHAPER_RESULTS", "FORGE_SHAPER_APPLY"):
            with self.subTest(command=name), self.assertRaises(RuntimeError):
                self.inspect(name, SHAPER="auto")
        self.assertIsNone(self.instance.record)
        self.assertEqual(self.settings, [])
        self.assertEqual([p.name for p in self.directory.iterdir()], [storage.LAST_RESULT])

    def test_export_rejects_printing_before_starting_a_process(self):
        self.save()
        for state, active in (("printing", False), ("paused", False), ("standby", True)):
            self.objects["print_stats"] = types.SimpleNamespace(get_status=lambda now: {"state": state})
            self.objects["virtual_sdcard"] = types.SimpleNamespace(is_active=lambda: active)
            with self.subTest(state=state, active=active), mock.patch.object(
                    plugin, "NativeProcess", side_effect=AssertionError("Started export")):
                with self.assertRaises(RuntimeError):
                    self.inspect("FORGE_SHAPER_RESULTS", PLOT="1")

    def test_failed_export_preserves_previous_images_measurements_and_selections(self):
        entry = self.save()
        self.instance.restore_saved()
        before = self.instance.record
        (self.directory / "plots").mkdir()
        graph = self.directory / "plots/x.png"
        graph.write_bytes(b"previous PNG")
        self.instance.plot_command = [sys.executable, "-c", "raise SystemExit(42)"]
        with self.assertRaises(RuntimeError):
            self.inspect("FORGE_SHAPER_RESULTS", PLOT="1")
        self.assertEqual(graph.read_bytes(), b"previous PNG")
        self.assertIs(self.instance.record, before)
        self.assertEqual(self.instance.history.entries()[0]["id"], entry["id"])
        self.assertEqual(list(self.directory.glob("plot-*")), [])
        self.assertEqual(self.settings, [])

    def test_export_timeout_reaps_the_child_and_cleans_its_work_directory(self):
        self.save()
        self.instance.timeout = .01
        self.instance.plot_command = [sys.executable, "-c", "import time; time.sleep(30)"]
        workers = []
        process = plugin.NativeProcess
        def start(*args, **kwargs):
            worker = process(*args, **kwargs)
            workers.append(worker)
            return worker
        with mock.patch.object(plugin, "NativeProcess", side_effect=start):
            with self.assertRaises(RuntimeError):
                self.inspect("FORGE_SHAPER_RESULTS", PLOT="1")
        self.assertIsNotNone(workers[0].process.poll())
        self.assertEqual(list(self.directory.glob("plot-*")), [])
        self.assertEqual(list((self.directory / "plots").iterdir()), [])

    @unittest.skipUnless(PLOT_PYTHON, "Set FORGE_SHAPER_PLOT_PYTHON to a Python with the legacy plot dependencies")
    def test_binary_history_exports_real_png_without_loading_or_changing_settings(self):
        self.save()
        before = (self.directory / storage.LAST_RESULT).read_bytes()
        table = Command()
        self.commands["FORGE_SHAPER_RESULTS"](table)
        command = Command(PLOT="1")
        announced = []
        spawn = subprocess.Popen
        def start(*args, **kwargs):
            announced.extend(command.messages)
            return spawn(*args, **kwargs)
        with mock.patch.object(plugin.subprocess, "Popen", side_effect=start):
            self.commands["FORGE_SHAPER_RESULTS"](command)
        self.assertTrue(any(message.strip() and message not in table.messages for message in announced))
        report = "\n".join(command.messages)
        for path in (self.directory / "plots").glob("*.png"):
            self.assertIn(str(path), report)
            self.assertFalse(any(str(path) in message for message in announced))
        for axis in ("x", "y"):
            chunks = png_contents(next((self.directory / "plots").glob("shaper_%s_*.png" % axis)))
            width, height = struct.unpack_from(">II", chunks[b"IHDR"][0])
            self.assertGreater(width, 0)
            self.assertGreater(height, 0)
            pixels = zlib.decompress(b"".join(chunks[b"IDAT"]))
            self.assertGreater(len(set(pixels)), 64)
        self.assertEqual((self.directory / storage.LAST_RESULT).read_bytes(), before)
        self.assertIsNone(self.instance.record)
        self.assertEqual(self.instance.state, "idle")
        self.assertEqual(self.settings, [])
        self.gcode.run_script_from_command.assert_not_called()
        self.assertEqual(list(self.directory.glob("plot-*")), [])

    @unittest.skipUnless(PLOT_PYTHON, "Set FORGE_SHAPER_PLOT_PYTHON to a Python with the legacy plot dependencies")
    def test_binary_export_has_the_same_pixels_as_the_legacy_json_renderer(self):
        self.save()
        self.inspect("FORGE_SHAPER_RESULTS", PLOT="1", AXIS="x")
        result = self.results["x"]
        legacy = dict(axis="x", scv=result["scv"], best_shaper=result["recommended"],
                      calibration_data={name: result[name] for name in storage.SPECTRUM_FIELDS},
                      all_shapers=[dict(fit, vals=fit["response"]) for fit in result["shapers"]])
        source, image = self.directory / "legacy.json", self.directory / "legacy.png"
        source.write_text(json.dumps(legacy))
        completed = subprocess.run([PLOT_PYTHON, str(ROOT / ".root/zshaper/calibrate_shaper.py"),
                                    "-d", str(source), "-o", str(image)], capture_output=True, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        actual = png_contents(next((self.directory / "plots").glob("shaper_x_*.png")))
        expected = png_contents(image)
        self.assertEqual(actual[b"IHDR"], expected[b"IHDR"])
        self.assertEqual(zlib.decompress(b"".join(actual[b"IDAT"])),
                         zlib.decompress(b"".join(expected[b"IDAT"])))

    @unittest.skipUnless(PLOT_PYTHON, "Set FORGE_SHAPER_PLOT_PYTHON to a Python with the legacy plot dependencies")
    def test_requested_old_axis_exports_its_spectrum_and_preserves_the_other_graph(self):
        old = self.save(1.)
        self.inspect("FORGE_SHAPER_RESULTS", PLOT="1", AXIS="x")
        old_graph = next((self.directory / "plots").glob("shaper_x_*.png"))
        expected = png_contents(old_graph)
        self.results["x"]["psd_sum"] = [v * 100 for v in self.results["x"]["psd_sum"]]
        self.save(2.)
        self.instance.restore_saved()
        opened = self.instance.record
        self.inspect("FORGE_SHAPER_RESULTS", PLOT="1")
        graphs = {p: p.read_bytes() for p in (self.directory / "plots").glob("*.png")}
        newer_x = next(p for p in graphs if p != old_graph and "_x_" in p.name)
        self.assertNotEqual(png_contents(newer_x)[b"IDAT"], expected[b"IDAT"])
        old_graph.unlink()
        report = self.inspect("FORGE_SHAPER_RESULTS", PLOT="1", RESULT=old["id"], AXIS="x")
        self.assertIn(str(old_graph), report)
        self.assertEqual(png_contents(old_graph)[b"IDAT"], expected[b"IDAT"])
        for path, before in graphs.items():
            if path != old_graph:
                self.assertEqual(path.read_bytes(), before)
        self.assertIs(self.instance.record, opened)
        self.assertEqual(self.settings, [])


NATIVE = os.environ.get("FORGE_SHAPER_HELPER")


def shaper_macros(instance, gcode, objects):
    """Execute actual macros and registered commands; fake only machine effects."""
    context = CONTEXT.OperationContextManager(FakeConfig(instance.printer))
    for kind in ("input_shaper", "homing"):
        context.register_context_type(FakeConfig(instance.printer, "operation_context_type " + kind))
    objects["operation_context"] = context
    machine = objects["toolhead"]
    machine.homed, machine.delayed_work = False, True
    messages = []
    def terminal(command):
        name, *arguments = shlex.split(command)
        values = dict(argument.split("=", 1) for argument in arguments)
        if name == "_ENSURE_SERVICES_STARTED":
            return
        if name == "_CANCEL_DELAYED_COMMANDS":
            machine.delayed_work = False
        elif name == "_HOME_IF_NEEDED":
            if machine.delayed_work:
                raise AssertionError("Delayed work can interfere with homing")
            machine.homed = machine.motors_enabled = True
        elif name in gcode.handlers:
            if name == "_FORGE_SHAPER_MEASURE" and not machine.homed:
                raise AssertionError("Measurement started before homing")
            cmd = Command(**values)
            gcode.handlers[name](cmd)
            messages.extend(cmd.messages)
        else:
            gcode.run_script_from_command(command)
    execution = MacroExecution([(ROOT / "macros/shaper.cfg", name, "gcode_macro")
                               for name in ("FORGE_SHAPER", "FORGE_SHAPER_CALIBRATE")],
                              {"print_stats": {"state": "standby"}}, terminal=terminal)
    return execution, messages


@unittest.skipUnless(NATIVE and PLOT_PYTHON, "Set native helper and legacy plot Python for the complete workflow")
class ShaperPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        options = {"plot_command": shlex.join([PLOT_PYTHON, str(PLOT), "--history"])}
        self.instance, self.settings, self.gcode, self.sweep, self.objects = make_plugin(
            self.directory, NATIVE, options)
        self.execution, self.messages = shaper_macros(self.instance, self.gcode, self.objects)
        disk = mock.patch.object(plugin, "disk_directory", return_value=str(self.directory))
        disk.start()
        self.addCleanup(disk.stop)

    def run_macro(self, command):
        try:
            self.execution.run(command)
        except Exception:
            self.instance.printer.send_event("gcode:command_error")
            raise

    def attach_feather(self, miss_begin=False):
        from tests.test_feather_lazy_features import ControllerFeatureRoutingTest
        from tests.test_feather_screen import StatusObject
        from ff5m_ui.screen import ScreenPage
        from tests.feather_render_test_helper import RenderCapture
        self.host = ControllerFeatureRoutingTest.controller()
        self.host.printer = self.instance.printer
        self.host.reactor = self.instance.reactor
        self.host.operation_context = self.objects["operation_context"]
        self.objects["forge_shaper"] = self.instance
        self.host.print_stats = StatusObject({"state": "standby"})
        self.host.virtual_sdcard = types.SimpleNamespace(is_active=lambda: False)
        self.host._prompt_draft = None
        self.callbacks = []
        self.host.reactor.register_callback = self.callbacks.append
        self.host._run_script = self.run_macro
        self.capture = RenderCapture(self.host.renderer)
        self.progress_seen = []
        def show_page(page):
            self.host.page = page
            self.host.feature_manager.get("calibration").render(page)
        self.host._show_page = show_page
        self.host._render_screen = lambda: show_page(self.host.page)
        if not miss_begin:
            self.instance.printer.register_event_handler("operation_context:begin", self.host._handle_operation_begin)
        self.instance.printer.register_event_handler("operation_context:end", self.host._handle_operation_end)
        def observe():
            self.host._update_operation_context(self.instance.reactor.monotonic())
            self.host.feature_manager.update(self.instance.reactor.monotonic())
            self.progress_seen.append(self.host.page == ScreenPage.CALIBRATION_PROGRESS
                                      and self.capture.latest.has_action("cal.cancel"))
        self.sweep.observe = observe
        return self.host.feature_manager.get("calibration") if not miss_begin else None

    def finish_feather_callbacks(self):
        callbacks, self.callbacks[:] = self.callbacks[:], []
        for callback in callbacks:
            callback(self.instance.reactor.monotonic())

    def test_console_macro_is_adopted_by_feather_and_shows_automatically_staged_settings(self):
        from ff5m_ui.screen import ScreenPage
        self.attach_feather()
        for _ in range(2):
            self.run_macro("FORGE_SHAPER AXIS=x")
            self.finish_feather_callbacks()
            self.assertEqual(self.host.page, ScreenPage.CALIBRATION_RESULT)
            feature = self.host.feature_manager.peek("calibration")
            self.assertIs(feature.shaper.view.results, self.instance.record.results)
            self.assertTrue(self.capture.latest.has_action("cal.shaper.save"))
            self.assertEqual(self.capture.latest.button("cal.shaper.apply").state, "selected")
        self.assertTrue(self.progress_seen)
        self.assertTrue(all(self.progress_seen))

    def test_feather_adopts_an_already_running_measurement_from_context_snapshot(self):
        from ff5m_ui.screen import ScreenPage
        self.attach_feather(miss_begin=True)
        self.run_macro("FORGE_SHAPER_CALIBRATE AXIS=y")
        self.finish_feather_callbacks()
        self.assertTrue(self.progress_seen)
        self.assertTrue(all(self.progress_seen))
        self.assertEqual(self.host.page, ScreenPage.CALIBRATION_RESULT)
        self.assertTrue(self.capture.latest.has_action("cal.shaper.apply"))
        self.assertFalse(self.capture.latest.has_action("cal.shaper.save"))

    def test_feather_start_executes_measurement_without_automatic_apply_or_png(self):
        from ff5m_ui.screen import ScreenPage
        feature = self.attach_feather()
        feature.calibration_kind = "shaper"
        feature._start_calibration()
        self.finish_feather_callbacks()
        self.assertEqual(self.host.page, ScreenPage.CALIBRATION_RESULT)
        self.assertIs(feature.shaper.view.results, self.instance.record.results)
        self.assertEqual(self.settings, [])
        self.assertFalse((self.directory / "plots").exists())
        self.assertFalse(self.objects["toolhead"].motors_enabled)

    def test_complete_macro_measures_stages_reports_and_exports_real_images(self):
        self.run_macro("FORGE_SHAPER AXIS=x")
        record = self.instance.record
        self.assertEqual(set(record.results), {"x"})
        self.assertEqual(self.instance.history.load(record.id)[0], record.results)
        result = record.results["x"]
        fit = next(f for f in result["shapers"] if f["name"] == result["recommended"])
        self.assertEqual(self.settings, [("input_shaper", "shaper_type_x", fit["name"]),
                                         ("input_shaper", "shaper_freq_x", "%.1f" % fit["freq"])])
        images = list((self.directory / "plots").glob("*.png"))
        self.assertEqual(len(images), 1)
        self.assertTrue(png_contents(images[0])[b"IDAT"])
        self.assertIn(str(images[0]), "\n".join(self.messages))
        self.assertFalse(self.objects["toolhead"].motors_enabled)
        self.assertFalse(self.objects["operation_context"].get_status(0.)["context_types"])

    def test_feather_measurement_stores_data_without_staging_or_exporting(self):
        self.run_macro("FORGE_SHAPER_CALIBRATE AXIS=y")
        self.assertEqual(set(self.instance.record.results), {"y"})
        self.assertEqual(self.settings, [])
        self.assertFalse((self.directory / "plots").exists())
        self.assertFalse(self.objects["toolhead"].motors_enabled)

    def test_failed_measurement_preserves_history_and_does_not_apply_or_plot(self):
        self.run_macro("FORGE_SHAPER_CALIBRATE AXIS=x")
        before = self.instance.history.entries()
        self.sweep.fail_axis = "y"
        with self.assertRaises(RuntimeError):
            self.run_macro("FORGE_SHAPER")
        self.assertEqual(self.instance.history.entries(), before)
        self.assertEqual(self.settings, [])
        self.assertFalse((self.directory / "plots").exists())
        self.assertFalse(self.objects["toolhead"].motors_enabled)

    def test_plot_failure_preserves_measurement_settings_and_powers_down_motors(self):
        self.instance.plot_command = [sys.executable, "-c", "raise SystemExit('Plot renderer failed')"]
        with self.assertRaisesRegex(RuntimeError, "Plot renderer failed"):
            self.run_macro("FORGE_SHAPER AXIS=y")
        record = self.instance.record
        self.assertEqual(self.instance.history.load(record.id)[0], record.results)
        self.assertEqual(self.instance.state, "complete")
        self.assertEqual(len(self.settings), 2)
        self.assertFalse(self.objects["toolhead"].motors_enabled)
        self.assertEqual(list((self.directory / "plots").glob("*.png")), [])

    def test_empty_renderer_output_is_an_error_and_cannot_replace_previous_graphs(self):
        self.run_macro("FORGE_SHAPER AXIS=x")
        before = {p: p.read_bytes() for p in (self.directory / "plots").glob("*.png")}
        self.instance.plot_command = [sys.executable, "-c", "pass"]
        command = Command(PLOT="1")
        with self.assertRaises(RuntimeError):
            self.instance.cmd_RESULTS(command)
        self.assertTrue(command.messages)
        self.assertEqual({p: p.read_bytes() for p in (self.directory / "plots").glob("*.png")}, before)
        self.assertEqual(list(self.directory.glob("plot-*")), [])

    def test_plot_history_is_dated_bounded_and_leaves_user_files_alone(self):
        # A real child writes test images; the parent still owns publication and retention.
        self.instance.plot_command = [sys.executable, "-c",
            "import pathlib,sys; p=pathlib.Path(sys.argv[4]); "
            "[(p/(axis+'.png')).write_bytes(sys.argv[2].encode()) for axis in sys.argv[3]]"]
        first_x = None
        for timestamp in range(1, 8):
            with mock.patch.object(plugin.time, "time", return_value=float(timestamp)):
                self.run_macro("FORGE_SHAPER AXIS=xy" if timestamp == 1 else "FORGE_SHAPER AXIS=x")
            plots = list((self.directory / "plots").glob("*.png"))
            for path in plots:
                if path.name == "custom.png":
                    continue
                self.assertIn("19700101_", path.name)
            if timestamp == 1:
                first_x = next(p for p in plots if "_x_" in p.name)
                y = next(p for p in plots if "_y_" in p.name)
                y_before = y.read_bytes()
                note = self.directory / "plots/custom.png"
                note.write_bytes(b"user image")
        self.assertFalse(first_x.exists())
        self.assertEqual(len(list((self.directory / "plots").glob("shaper_x_*.png"))), 5)
        self.assertEqual(y.read_bytes(), y_before)
        self.assertEqual(note.read_bytes(), b"user image")
        before = set((self.directory / "plots").iterdir())
        self.instance.cmd_RESULTS(Command(PLOT="1", AXIS="x"))
        self.assertEqual(set((self.directory / "plots").iterdir()), before)


if __name__ == "__main__":
    unittest.main()
