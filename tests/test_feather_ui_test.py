## Host-side contracts for the lazy on-printer Feather UI runner.

import csv
import json
import pathlib
import re
import tempfile
import threading
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]
PLUGINS = ROOT / ".py" / "klipper" / "plugins"

import sys
sys.path.insert(0, str(PLUGINS))

from feather_ui_test import feature as UI_TEST_FEATURE  # noqa: E402
from feather_ui_test import artifacts as ARTIFACTS  # noqa: E402
from feather_ui_test import runner as UI_TEST  # noqa: E402
from feather_ui_test import scenarios as SCENARIOS  # noqa: E402
from feather_ui_test import context_fixtures as CONTEXT_FIXTURES  # noqa: E402
from feather_ui_test import resources as RESOURCES  # noqa: E402
import feather_screen as FEATHER  # noqa: E402
from feather_feature_manager import LazyFeatureManager  # noqa: E402
from tests.visual_checks import hybrid as HYBRID  # noqa: E402


class AsyncReactor:
    def register_async_callback(self, callback):
        callback(1.0)


class GCmd:
    def __init__(self, values=None):
        self.values = values or {}
        self.responses = []

    def get(self, name, default=None):
        return self.values.get(name, default)

    def get_int(self, name, default=0):
        return int(self.values.get(name, default))

    def respond_info(self, message):
        self.responses.append(message)

    def error(self, message):
        return RuntimeError(message)


class ArtifactWorkerTest(unittest.TestCase):
    def test_frame_capture_requires_continuous_quiet_window(self):
        worker = object.__new__(ARTIFACTS.ArtifactWorker)
        frames = [b"first", b"first", b"second", b"second",
                  b"second", b"second"]
        worker._read_frame = mock.Mock(side_effect=frames)

        with mock.patch.object(ARTIFACTS, "FRAME_SETTLE_INTERVAL", 0.15), \
                mock.patch.object(ARTIFACTS, "FRAME_SETTLE_TIMEOUT", 2.0), \
                mock.patch.object(ARTIFACTS.time, "monotonic", side_effect=(
                    0.0, 0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30)), \
                mock.patch.object(ARTIFACTS.time, "sleep") as sleep:
            data, digest, observed_time = worker._stable_frame()

        self.assertEqual(data, b"second")
        self.assertIsInstance(observed_time, float)
        self.assertEqual(
            digest, ARTIFACTS.hashlib.sha256(b"second").hexdigest())
        self.assertEqual(worker._read_frame.call_count, 6)
        self.assertEqual(sleep.call_count, 5)

    def test_settling_hashes_only_the_frame_it_accepts(self):
        worker = object.__new__(ARTIFACTS.ArtifactWorker)
        frames = [b"first", b"first", b"second", b"second",
                  b"second", b"second"]
        worker._read_frame = mock.Mock(side_effect=frames)
        real_sha256 = ARTIFACTS.hashlib.sha256

        with mock.patch.object(ARTIFACTS, "FRAME_SETTLE_INTERVAL", 0.15), \
                mock.patch.object(ARTIFACTS, "FRAME_SETTLE_TIMEOUT", 2.0), \
                mock.patch.object(ARTIFACTS.time, "monotonic", side_effect=(
                    0.0, 0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30)), \
                mock.patch.object(ARTIFACTS.time, "sleep"), \
                mock.patch.object(ARTIFACTS.hashlib, "sha256",
                                  side_effect=real_sha256) as sha256:
            data, digest, observed_time = worker._stable_frame()

        # Change detection must not cost a SHA-256 pass over 1.5 MiB per
        # sample: the printer's Cortex-A7 shares its cores with Klipper, and
        # the loop samples every 50 ms until the screen goes quiet.
        self.assertEqual(sha256.call_count, 1)
        self.assertEqual(data, b"second")
        self.assertEqual(digest, real_sha256(b"second").hexdigest())
        self.assertIsInstance(observed_time, float)

    def test_one_shot_capture_does_not_wait_for_a_quiet_framebuffer(self):
        worker = object.__new__(ARTIFACTS.ArtifactWorker)
        worker.run_directory = "/unused"
        worker._read_frame = mock.Mock(return_value=b"current")
        worker._stable_frame = mock.Mock(
            side_effect=AssertionError("one-shot capture waited for settling"))
        worker.records = []

        with mock.patch.object(ARTIFACTS, "_atomic_json") as atomic_json, \
                mock.patch.object(ARTIFACTS.time, "time",
                                  return_value=1234.5), \
                mock.patch("builtins.open", mock.mock_open()):
            record = worker._capture(
                1, "periodic", {"time": 1000.0}, settle=False)

        self.assertEqual(
            record["sha256"],
            ARTIFACTS.hashlib.sha256(b"current").hexdigest())
        self.assertEqual(record["time"], 1234.5)
        worker._read_frame.assert_called_once_with()
        worker._stable_frame.assert_not_called()
        atomic_json.assert_not_called()

    def test_frame_capture_reads_the_active_virtual_page(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            framebuffer = root / "fb0"
            pan = root / "pan"
            stride = root / "stride"
            first = b"\x11\x22\x33\xff" * (
                ARTIFACTS.SCREEN_WIDTH * ARTIFACTS.SCREEN_HEIGHT)
            second = b"\xaa\xbb\xcc\xff" * (
                ARTIFACTS.SCREEN_WIDTH * ARTIFACTS.SCREEN_HEIGHT)
            framebuffer.write_bytes(first + second)
            pan.write_text("0,480\n", encoding="ascii")
            stride.write_text("3200\n", encoding="ascii")
            worker = object.__new__(ARTIFACTS.ArtifactWorker)
            worker.framebuffer = str(framebuffer)
            worker.framebuffer_pan = str(pan)
            worker.framebuffer_stride = str(stride)

            self.assertEqual(worker._read_frame(), second)

    def test_frame_capture_retries_if_page_flips_during_read(self):
        worker = object.__new__(ARTIFACTS.ArtifactWorker)
        first = b"first"
        second = b"second"
        worker._read_stride = mock.Mock(return_value=3200)
        worker._read_pan = mock.Mock(side_effect=(
            (0, 0), (0, 480), (0, 480), (0, 480)))
        worker._read_frame_at = mock.Mock(side_effect=(first, second))

        self.assertEqual(worker._read_frame(), second)
        self.assertEqual(worker._read_frame_at.call_count, 2)

    def test_writes_bmp_manifest_telemetry_and_log_slice(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            run = root / "20260729-120000-ui"
            run.mkdir()
            framebuffer = root / "fb0"
            pixels = bytearray(ARTIFACTS.FRAME_BYTES)
            pixels[0:4] = b"\x03\x06\x07\xff"
            framebuffer.write_bytes(pixels)
            printer_log = root / "printer.log"
            printer_log.write_text("before\n", encoding="utf-8")
            active = root / "active.json"
            active.write_text("{}\n", encoding="utf-8")

            worker = ARTIFACTS.ArtifactWorker(
                AsyncReactor(), str(run), str(framebuffer), str(printer_log))
            printer_log.write_text("before\nafter\n", encoding="utf-8")
            captured = []
            captured_event = threading.Event()
            worker.capture(
                1, "Main menu", {"page": "MAIN_MENU"},
                lambda value: (captured.append(value), captured_event.set()))
            self.assertTrue(captured_event.wait(3.0))
            self.assertFalse(isinstance(captured[0], Exception))
            with (run / "artifact_timing.csv").open(
                    newline="", encoding="utf-8") as stream:
                timings = list(csv.DictReader(stream))
            self.assertEqual(len(timings), 1)
            self.assertEqual(timings[0]["label"], "Main menu")
            self.assertEqual(timings[0]["capture_kind"], "semantic")
            self.assertEqual(timings[0]["success"], "True")
            self.assertGreaterEqual(float(timings[0]["queue_delay_ms"]), 0.0)
            self.assertGreater(float(timings[0]["duration_ms"]), 0.0)

            worker.telemetry(
                "positions", ("time", "x", "y", "z"),
                {"time": 1, "x": 2, "y": 3, "z": 4})
            worker.log("finished phase")
            finished = []
            finished_event = threading.Event()
            with mock.patch.object(ARTIFACTS, "ACTIVE_MARKER", str(active)):
                worker.finish(
                    {"outcome": "passed",
                     "operation_context": {
                         "passed": True, "scenario_count": 0,
                         "scenarios": []},
                     "_operation_context_artifact": {
                         "passed": True, "scenarios": []}},
                    lambda value: (finished.append(value),
                                   finished_event.set()))
                self.assertTrue(finished_event.wait(3.0))
            worker.stop()

            bmp = run / captured[0]["file"]
            self.assertEqual(bmp.read_bytes()[:2], b"BM")
            self.assertEqual(
                bmp.stat().st_size, ARTIFACTS.FRAME_BYTES + 54)
            manifest = json.loads((run / "manifest.json").read_text())
            self.assertEqual(manifest[0]["page"], "MAIN_MENU")
            self.assertTrue(manifest[0]["passed"])
            self.assertIn("time,x,y,z", (run / "positions.csv").read_text())
            self.assertIn("finished phase", (run / "run.log").read_text())
            self.assertEqual((run / "printer.log").read_text(), "after\n")
            operation = json.loads(
                (run / "operation_context.json").read_text())
            self.assertTrue(operation["passed"])
            summary = json.loads((run / "summary.json").read_text())
            self.assertNotIn("_operation_context_artifact", summary)
            self.assertTrue(summary["operation_context"]["passed"])
            self.assertFalse(active.exists())

    def test_capture_started_counter_brackets_frame_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            run = root / "20260729-120000-ui"
            run.mkdir()
            printer_log = root / "printer.log"
            printer_log.write_text("", encoding="utf-8")
            worker = ARTIFACTS.ArtifactWorker(
                AsyncReactor(), str(run), str(root / "unused-fb"),
                str(printer_log))
            started = threading.Event()
            release = threading.Event()
            done = threading.Event()

            def capture(*_args):
                started.set()
                release.wait(3.0)
                return {"file": "screen.bmp"}

            worker._capture = capture
            worker.capture(1, "held", {}, lambda _value: done.set())
            self.assertEqual(worker.captures_queued, 1)
            self.assertTrue(started.wait(3.0))
            self.assertEqual(worker.captures_started, 1)
            self.assertEqual(worker.captures_finished, 0)
            release.set()
            self.assertTrue(done.wait(3.0))
            worker.stop()

        self.assertEqual(worker.captures_finished, 1)

    def test_capture_finished_counter_includes_failed_frame_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            run = root / "20260729-120000-ui"
            run.mkdir()
            framebuffer = root / "fb0"
            framebuffer.write_bytes(bytes(ARTIFACTS.FRAME_BYTES))
            printer_log = root / "printer.log"
            printer_log.write_text("", encoding="utf-8")

            worker = ARTIFACTS.ArtifactWorker(
                AsyncReactor(), str(run), str(framebuffer), str(printer_log))
            results = []
            done = threading.Event()
            worker.capture(
                1, "blank", {},
                lambda value: (results.append(value), done.set()),
                settle=False)
            self.assertTrue(done.wait(3.0))
            worker.stop()

        # An all-zero framebuffer fails the capture. The finished counter still
        # has to move, otherwise a failed capture and a hung one look the same.
        self.assertIsInstance(results[0], Exception)
        self.assertEqual(worker.captures_started, 1)
        self.assertEqual(worker.captures_finished, 1)

    def test_retention_preserves_newest_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            current = root / "20260729-120004-000001-ui"
            current.mkdir()
            (current / "summary.json").write_text(
                '{"outcome":"passed"}', encoding="utf-8")
            names = []
            for index in range(4):
                name = "20260729-12000%d-000001-ui" % index
                names.append(name)
                directory = root / name
                directory.mkdir()
                outcome = "failed" if index == 3 else "passed"
                (directory / "summary.json").write_text(
                    json.dumps({"outcome": outcome}), encoding="utf-8")
            worker = ARTIFACTS.ArtifactWorker(
                AsyncReactor(), str(current), "/missing-fb", "/missing-log")

            with mock.patch.object(ARTIFACTS, "MAX_RUNS", 3), \
                    mock.patch.object(ARTIFACTS, "MAX_BYTES", 1024 * 1024):
                worker._retain()
            worker.stop()

            remaining = {path.name for path in root.iterdir() if path.is_dir()}
            self.assertEqual(len(remaining), 3)
            self.assertIn(names[-1], remaining)


class RunnerContractTest(unittest.TestCase):
    def test_stage_capture_follows_semantic_context_revisions(self):
        operation = {
            "revision": 1,
            "context_types": ("bed_level", "nozzle_clean"),
            "context_path": ("Bed Mesh", "Nozzle Cleaning"),
            "current_state": "CLEANING",
        }
        host = type("Host", (), {
            "_operation_context_status":
                lambda self, eventtime: dict(operation),
            "_operation_context_text":
                lambda self, eventtime=None, status=None:
                " -> ".join(tuple(status["context_path"]) +
                            (status["current_state"],)),
        })()
        run = UI_TEST.UITestRun(host)
        run.running = True
        run.phase = "mesh"
        run.worker = mock.Mock()
        run._screen_metadata = lambda: {"page": "CALIBRATION_PROGRESS"}

        run.update(1.0)
        run.update(2.0)
        operation["revision"] = 2
        run.update(3.0)
        operation.update(revision=3, current_state="LEVELING",
                         context_types=("bed_level",),
                         context_path=("Bed Mesh",))
        run.update(4.0)

        self.assertEqual(run.worker.capture.call_count, 2)
        self.assertEqual(
            [stage["current_state"] for stage in run.calibration_stages],
            ["CLEANING", "LEVELING"])
        self.assertEqual(
            run.calibration_stages[0]["context_types"],
            ("bed_level", "nozzle_clean"))
        self.assertEqual(
            run.calibration_stages[1]["context_path"], ("Bed Mesh",))
        # Stage captures settle the framebuffer, so a post-mortem has to see
        # that one was requested and be able to tell it from a semantic one.
        logged = [str(call[0][0]) for call in run.worker.log.call_args_list]
        self.assertEqual(
            len([line for line in logged
                 if line.startswith("CAPTURE_QUEUED")]), 2)
        self.assertEqual(
            run.worker.capture.call_args_list[0][0][2]["capture_kind"],
            "stage")

    def test_operation_stage_changes_are_not_limited_by_test_phase(self):
        operation = {
            "revision": 1,
            "context_types": ("recovery",),
            "context_path": ("Recovery",),
            "current_state": "HEATING NOZZLE",
        }
        host = type("Host", (), {
            "_operation_context_status":
                lambda self, eventtime: dict(operation),
            "_operation_context_text":
                lambda self, eventtime=None, status=None:
                " -> ".join(tuple(status["context_path"]) +
                            (status["current_state"],)),
        })()
        run = UI_TEST.UITestRun(host)
        run.running = True
        run.phase = "recovery"
        run.worker = mock.Mock()
        run._screen_metadata = lambda: {"page": "CALIBRATION_PROGRESS"}

        run.update(1.0)
        operation.update(revision=2, current_state="HOMING")
        run.update(2.0)
        operation.update(revision=3, current_state="POSITIONING")
        run.update(3.0)
        run.phase = "future_workflow"
        operation.update(revision=4, current_state="RESTORING")
        run.update(4.0)

        labels = [call[0][1] for call in run.worker.capture.call_args_list]
        self.assertEqual(labels, [
            "recovery-stage-Recovery -> HEATING NOZZLE",
            "recovery-stage-Recovery -> HOMING",
            "recovery-stage-Recovery -> POSITIONING",
            "future_workflow-stage-Recovery -> RESTORING",
        ])
        self.assertEqual(
            [stage["current_state"] for stage in run.calibration_stages],
            ["HEATING NOZZLE", "HOMING", "POSITIONING", "RESTORING"])
        self.assertTrue(all(
            call[0][2]["capture_kind"] == "stage"
            for call in run.worker.capture.call_args_list))

    def test_tap_waits_for_active_gcode_before_advancing(self):
        callbacks = []
        reactor = type("Reactor", (), {
            "register_callback": lambda self, callback, when=None:
                callbacks.append((callback, when)),
        })()
        host = type("Host", (), {
            "reactor": reactor,
            "page": FEATHER.ScreenPage.CONTROL_MOVE,
            "command_depth": 1,
            "busy_message": "HOMING...",
        })()
        feature = UI_TEST.UITestRun(host)
        feature.running = True
        feature.step_index = 4
        events = []
        schedules = []
        feature._event = events.append
        feature._schedule = schedules.append
        step = {"kind": "tap", "label": "move.homeall"}

        feature._after_tap(10.0, step, FEATHER.ScreenPage.CONTROL_MOVE)

        self.assertEqual(feature.step_index, 4)
        self.assertEqual(feature.step_runtime["operation_deadline"],
                         10.0 + UI_TEST.TAP_OPERATION_TIMEOUT)
        self.assertTrue(feature.step_runtime["expected_page_seen"])
        self.assertEqual(callbacks[0][1], 10.1)
        host.command_depth = 0
        host.busy_message = None
        # A long operation may legitimately advance from its progress page to
        # a result page before synthetic tap completion is acknowledged.
        host.page = FEATHER.ScreenPage.CALIBRATION_RESULT
        callbacks[0][0](10.1)
        self.assertEqual(feature.step_index, 5)
        self.assertEqual(events, ["PASS move.homeall"])
        self.assertEqual(schedules, [0.02])
        self.assertEqual(step, {"kind": "tap", "label": "move.homeall"})

    def test_motion_return_clamps_homing_overshoot_to_ui_limits(self):
        position = [110.0, 109.1, 220.0]
        dispatched = []
        toolhead = type("Toolhead", (), {
            "get_status": lambda self, eventtime: {
                "position": tuple(position), "homed_axes": "xyz",
            },
        })()

        def dispatch(_host, action):
            dispatched.append(action)
            position[1] = 110.0

        host = type("Host", (), {
            "reactor": type("Reactor", (), {
                "monotonic": lambda self: 10.0,
            })(),
            "toolhead": toolhead,
            "_feather_move_limits": lambda self, status: (
                (-110.0, 110.0), (-110.0, 110.0), (0.0, 220.0)),
            "_start_touch_action": dispatch,
            "renderer": type("Renderer", (), {
                "_buttons": {SCENARIOS.move_actions.Y_PLUS.wire_id: ()},
                "_toggles": {}, "_hitboxes": {},
            })(),
        })()
        feature = UI_TEST.UITestRun(host)
        feature.scenarios.motion_origin = (110.0, 110.099675, 220.0)

        feature.scenarios._motion_step("y", -1)

        self.assertEqual(
            dispatched, [SCENARIOS.move_actions.Y_PLUS.wire_id])
        self.assertEqual(feature.scenarios.motion_expected, 110.0)

    def test_motion_step_waits_for_delayed_toolhead_update(self):
        position = [110.006, 109.0, 220.0]
        dispatched = []
        toolhead = type("Toolhead", (), {
            "get_status": lambda self, eventtime: {
                "position": tuple(position), "homed_axes": "xyz",
            },
        })()
        host = type("Host", (), {
            "reactor": type("Reactor", (), {
                "monotonic": lambda self: 10.0,
            })(),
            "toolhead": toolhead,
            "_feather_move_limits": lambda self, status: (
                (-110.0, 110.0), (-110.0, 110.0), (0.0, 220.0)),
            "_start_touch_action": (
                lambda self, action: dispatched.append(action)),
            "renderer": type("Renderer", (), {
                "_buttons": {SCENARIOS.move_actions.X_MINUS.wire_id: ()},
                "_toggles": {}, "_hitboxes": {},
            })(),
        })()
        feature = UI_TEST.UITestRun(host)
        feature.scenarios.motion_origin = tuple(position)

        feature.scenarios._motion_step("x", 1)

        self.assertEqual(
            dispatched, [SCENARIOS.move_actions.X_MINUS.wire_id])
        self.assertFalse(feature.scenarios._motion_reached("x"))
        position[0] = feature.scenarios.motion_expected
        self.assertTrue(feature.scenarios._motion_reached("x"))
        labels = [step["label"] for step in feature.scenarios.build_steps("MOTION")]
        self.assertLess(labels.index("motion-x-forward-dispatch"),
                        labels.index("motion-x-forward-complete"))
        self.assertLess(labels.index("motion-x-forward-complete"),
                        labels.index("motion-x-forward"))

    def test_mesh_restore_is_noop_before_snapshot(self):
        feature = UI_TEST.UITestRun(object())

        feature.scenarios._restore_mesh_snapshot()

        self.assertIsNone(feature.scenarios._mesh_snapshot)

    def test_render_suite_is_nonphysical_and_waits_for_recovery(self):
        statuses = [
            {"worker_state": "running", "typer_restarts": 2},
            {"worker_state": "running", "typer_restarts": 3},
        ]
        renderer = type("Renderer", (), {
            "get_status": lambda self: statuses.pop(0) if len(statuses) > 1
            else statuses[0],
            "restart": lambda self: True,
        })()
        feature = UI_TEST.UITestRun(type("Host", (), {
            "renderer": renderer,
        })())
        steps = feature.scenarios.build_steps("RENDER")
        labels = [step["label"] for step in steps]

        self.assertEqual(labels, [
            "baseline", "render-context-start", "render-pause-timer",
            "render-restart-signal", "render-recovered",
            "render-recovered", "render-resume-timer",
            "render-context-verify"])
        steps[3]["callback"]()
        self.assertTrue(steps[4]["predicate"]())

    def test_capture_waits_for_worker_and_fails_on_dropped_batch(self):
        status = {
            "submitted_batches": 5, "rendered_batches": 4,
            "coalesced_batches": 0, "dropped_batches": 0,
        }
        renderer = type("Renderer", (), {
            "get_status": lambda self: dict(status),
            "send": lambda self, *args, **kwargs: True,
        })()
        reactor = type("Reactor", (), {"monotonic": lambda self: 10.0})()
        feature = UI_TEST.UITestRun(type("Host", (), {
            "renderer": renderer, "reactor": reactor,
        })())
        scheduled = []
        feature._schedule = scheduled.append
        feature._screen_metadata = lambda: {"page": "IDLE_HOME"}
        captures = []
        feature.worker = type("Worker", (), {
            "capture": lambda self, *args: captures.append(args),
            "log": lambda self, message: None,
        })()
        step = {"kind": "capture", "label": "settled"}

        feature._capture(step)
        self.assertEqual(scheduled, [0.02])
        self.assertEqual(captures, [])

        status["dropped_batches"] = 1
        with self.assertRaisesRegex(RuntimeError, "batch dropped"):
            feature._capture(step)

    def test_capture_waits_for_presented_receipt_before_reading_frame(self):
        status = {
            "submitted_batches": 5, "rendered_batches": 5,
            "coalesced_batches": 0, "dropped_batches": 0,
        }
        submissions = []

        class Renderer:
            def get_status(self):
                return dict(status)

            def send(self, commands, **kwargs):
                submissions.append((commands, kwargs))
                status["submitted_batches"] += 1
                status["rendered_batches"] += 1
                return True

        reactor = type("Reactor", (), {"monotonic": lambda self: 10.0})()
        feature = UI_TEST.UITestRun(type("Host", (), {
            "renderer": Renderer(), "reactor": reactor,
        })())
        feature.running = True
        feature.step_index = 3
        feature._schedule = mock.Mock()
        feature._screen_metadata = lambda: {"page": "IDLE_HOME"}
        captures = []
        feature.worker = type("Worker", (), {
            "capture": lambda self, *args: captures.append(args),
            "log": lambda self, message: None,
        })()
        step = {"kind": "capture", "label": "presented"}

        feature._capture(step)

        self.assertEqual(captures, [])
        self.assertEqual(len(submissions), 1)
        self.assertIn("--receipt-phase presented", submissions[0][0][0])
        token = feature.step_runtime["capture_receipt"]
        receipt = type("Receipt", (), {
            "token": token, "success": True,
        })()
        feature.on_render_receipt(receipt, 10.0)
        feature._capture(step)

        self.assertEqual(len(captures), 1)
        self.assertNotIn(token, feature.capture_receipts)
        self.assertEqual(step, {"kind": "capture", "label": "presented"})

    def test_screen_metadata_observes_live_telemetry(self):
        telemetry = []
        logs = []

        class Status:
            def __init__(self, values):
                self.values = values

            def get_status(self, eventtime):
                return dict(self.values)

        renderer = type("Renderer", (), {
            "generation": 7,
            "_buttons": {}, "_hitboxes": {}, "_toggles": {},
            "get_status": lambda self: {
                "semantic_page_id": "home", "dropped_batches": 0,
            },
        })()
        host = type("Host", (), {
            "reactor": type("Reactor", (), {
                "monotonic": lambda self: 12.5,
            })(),
            "renderer": renderer,
            "page": FEATHER.ScreenPage.IDLE_HOME,
            "extruder": Status({"temperature": 31.5, "target": 0.0}),
            "heater_bed": Status({"temperature": 29.0, "target": 0.0}),
            "toolhead": Status({"position": (1.0, 2.0, 3.0, 4.0)}),
        })()
        feature = UI_TEST.UITestRun(host)
        feature.phase = "baseline"
        feature.worker = type("Worker", (), {
            "log": lambda self, message: logs.append(message),
            "telemetry": lambda self, name, fields, values:
                telemetry.append((name, fields, values)),
        })()

        metadata = feature._screen_metadata()

        self.assertEqual(metadata["phase"], "baseline")
        self.assertEqual(metadata["semantic_page_id"], "home")
        self.assertEqual(metadata["position"], [1.0, 2.0, 3.0])
        self.assertEqual(metadata["temperatures"]["nozzle"], 31.5)
        self.assertEqual(metadata["temperatures"]["bed"], 29.0)
        self.assertEqual(
            [(name, fields) for name, fields, values in telemetry], [
                ("temperatures", (
                    "time", "nozzle", "nozzle_target", "bed", "bed_target")),
                ("positions", ("time", "x", "y", "z")),
            ])
        self.assertEqual(telemetry[1][2]["z"], 3.0)
        self.assertTrue(logs[0].startswith("TEMPERATURE "))
        self.assertEqual(logs[1], "POSITION [1.0, 2.0, 3.0]")

    def test_full_suite_order_and_z_safety_sequence(self):
        renderer = type("Renderer", (), {
            "get_status": lambda self: {"typer_restarts": 0},
        })()
        feature = UI_TEST.UITestRun(type("Host", (), {
            "renderer": renderer,
        })())
        feature.material = "PLA"
        steps = feature.scenarios.build_steps("FULL")
        labels = [step["label"] for step in steps]

        first = dict((phase, next(index for index, label in enumerate(labels)
                                  if label.startswith(phase + "-")))
                     for phase in ("ui", "render", "motion", "heat", "screws",
                                   "mesh", "z"))
        self.assertEqual(list(first), [
            "ui", "render", "motion", "heat", "screws", "mesh", "z"])
        self.assertEqual(list(first.values()), sorted(first.values()))
        actions = [step.get("action") for step in steps]
        self.assertIn(SCENARIOS.move_actions.HOME_ALL.wire_id, actions)
        self.assertNotIn("move.homeall", actions)
        self.assertEqual(
            actions.count(UI_TEST.z_actions.FARTHER.wire_id), 10)
        self.assertEqual(
            actions.count(UI_TEST.z_actions.CLOSER.wire_id), 10)
        self.assertIn(UI_TEST.z_actions.DISCARD_CONFIRM.wire_id, actions)
        self.assertNotIn(UI_TEST.z_actions.SAVE.wire_id, actions)
        self.assertFalse(any("pid" in label.lower() for label in labels))

    def test_periodic_capture_waits_for_every_probing_operation_state(self):
        class Toolhead:
            @staticmethod
            def check_busy(_eventtime):
                # Idle toolhead: lookahead empty and no queued move time, so
                # only the operation state can block the capture here.
                return 0.0, 1.0, True

        host = type("Host", (), {"reactor": None})()
        host.toolhead = Toolhead()
        state = {"current_state": None}
        host._operation_context_status = lambda _eventtime: state
        run = UI_TEST.UITestRun(host)

        blocked = {}
        for name in ("HOMING", "PROBING", "LEVELING", "CHECKING MESH",
                     "TARING", "checking mesh"):
            state["current_state"] = name
            blocked[name] = run._periodic_capture_block_reason(1.0)
        state["current_state"] = "PREPARING"
        idle_reason = run._periodic_capture_block_reason(1.0)

        # Every state that drives the toolhead against the bed must block a
        # framebuffer capture, including the two-word one the mesh validation
        # macro publishes and the load-cell TARING state.
        for name, reason in blocked.items():
            self.assertIsNotNone(reason, name)
        self.assertIsNone(idle_reason)

    def test_reactor_probe_records_delay_and_unregisters_its_timer(self):
        telemetry = []
        unregistered = []

        class Reactor:
            NEVER = 1.0e30

            @staticmethod
            def monotonic():
                return 10.0

            @staticmethod
            def register_timer(callback, when):
                return (callback, when)

            @staticmethod
            def unregister_timer(timer):
                unregistered.append(timer)

        run = UI_TEST.UITestRun(type("Host", (), {
            "reactor": Reactor(),
        })())
        run.running = True
        run.worker = type("Worker", (), {
            "telemetry": lambda self, name, fields, values:
                telemetry.append((name, fields, values)),
            "captures_queued": 7,
            "captures_started": 7,
            "captures_finished": 6,
        })()
        run._start_reactor_probe()
        wake = run._reactor_probe_tick(11.45)
        timer = run.reactor_probe_timer
        run._stop_reactor_probe()

        self.assertGreater(wake, 11.45)
        self.assertEqual(telemetry[0][0], "reactor")
        self.assertGreaterEqual(telemetry[0][2]["missed_deadlines"], 6)
        self.assertGreater(telemetry[0][2]["max_lag_ms"], 1000.0)
        self.assertEqual(telemetry[0][2]["max_lag_eventtime"], 11.45)
        self.assertEqual(telemetry[0][2]["max_interval_eventtime"], 11.45)
        self.assertEqual(telemetry[0][2]["phase"], "idle")
        self.assertIsNone(telemetry[0][2]["step"])
        # A late reactor sample has to distinguish a queued request from frame
        # work that actually started; artifact_timing.csv only gains its row
        # once a capture returns, so an interrupted one leaves no trace there.
        self.assertEqual(telemetry[0][2]["captures_queued"], 7)
        self.assertEqual(telemetry[0][2]["captures_started"], 7)
        self.assertEqual(telemetry[0][2]["captures_finished"], 6)
        self.assertIn("captures_queued", telemetry[0][1])
        self.assertIn("captures_started", telemetry[0][1])
        self.assertIn("captures_finished", telemetry[0][1])
        self.assertEqual(unregistered, [timer])

    def test_ui_file_browser_returns_home_before_reopening_menu(self):
        feature = UI_TEST.UITestRun(object())
        steps = feature.scenarios.build_steps("UI")
        labels = [step["label"] for step in steps]
        returned = labels.index("ui-file-return")

        self.assertEqual(
            (steps[returned + 1]["action"], steps[returned + 1]["page"]),
            ("nav.back", FEATHER.ScreenPage.IDLE_HOME))
        self.assertEqual(
            (steps[returned + 2]["action"], steps[returned + 2]["page"]),
            ("nav.menu", FEATHER.ScreenPage.MAIN_MENU))

    def test_ui_suite_capture_contract_matches_hybrid_coverage(self):
        feature = UI_TEST.UITestRun(object())

        captures = {
            step["label"] for step in feature.scenarios.build_steps("UI")
            if step["kind"] == "capture"
        }

        self.assertEqual(captures, HYBRID.UI_SUITE_LABELS)
        self.assertIn("ui-update-short", captures)
        self.assertIn("ui-update-long", captures)
        self.assertIn("ui-update-progress", captures)
        self.assertIn("ui-update-restart", captures)
        self.assertEqual(
            {"ui-extruder-" + phase for phase in (
                "intro", "material", "cold-pull", "cut", "cooling",
                "remove", "load", "mark-first", "mark-second",
                "measure-ready", "input", "warning", "result",
                "exit-warning", "saved")},
            {label for label in captures if label.startswith("ui-extruder-")})

    def test_every_ui_capture_has_a_visual_review_expectation(self):
        expectations = json.loads(
            (ROOT / "tests" / "visual_checks" / "expectations.json")
            .read_text(encoding="utf-8"))["cases"]

        missing = sorted(
            label for label in HYBRID.UI_SUITE_LABELS
            if "printer:" + label not in expectations)

        self.assertEqual(missing, [])

    def test_synthetic_ui_inputs_restore_absent_and_existing_fields(self):
        target = type("Target", (), {"existing": "before"})()

        with self.assertRaisesRegex(RuntimeError, "render failed"):
            with SCENARIOS._temporary_attributes(target, {
                    "existing": "during", "temporary": 42}):
                self.assertEqual(target.existing, "during")
                self.assertEqual(target.temporary, 42)
                raise RuntimeError("render failed")

        self.assertEqual(target.existing, "before")
        self.assertNotIn("existing", vars(target))
        self.assertFalse(hasattr(target, "temporary"))

    def test_update_dialog_snapshots_render_without_starting_an_update(self):
        notification = type("Notification", (), {
            "installed_version": "installed-before",
            "available_version": "available-before",
            "changes": ("before",),
            "change_page": 0,
        })()
        host = type("Host", (), {"update_notification": notification})()
        feature = UI_TEST.UITestRun(host)
        rendered = []
        feature.scenarios._show = lambda page: rendered.append((
            page, notification.installed_version,
            notification.available_version, notification.changes,
            notification.change_page))

        feature.scenarios._render_update_snapshot(long=False)
        feature.scenarios._render_update_snapshot(long=True)

        self.assertEqual(
            [item[0] for item in rendered],
            [FEATHER.ScreenPage.UPDATE_NOTIFICATION] * 2)
        self.assertEqual(len(rendered[0][3]), 1)
        self.assertGreater(len(rendered[1][3]), 6)
        self.assertEqual(rendered[1][4], 1)
        self.assertEqual(notification.installed_version, "installed-before")
        self.assertEqual(notification.available_version, "available-before")
        self.assertEqual(notification.changes, ("before",))
        self.assertEqual(notification.change_page, 0)

    def test_ui_timer_pause_suppresses_and_restores_real_update_dialog(self):
        class Notification:
            def maybe_present(self):
                return True

        notification = Notification()
        host = type("Host", (), {
            "timer": None,
            "update_notification": notification,
        })()
        feature = UI_TEST.UITestRun(host)

        feature.scenarios._pause_ui_timer()
        self.assertFalse(notification.maybe_present())

        feature.scenarios.restore_synthetic_state()
        self.assertTrue(notification.maybe_present())
        self.assertNotIn("maybe_present", vars(notification))

    def test_all_extruder_snapshots_use_disposable_sessions(self):
        original_session = object()
        extruder = type("ExtruderFeature", (), {
            "extruder_calibration": original_session,
        })()
        manager = type("Manager", (), {
            "get": lambda self, name: extruder,
        })()
        host = type("Host", (), {
            "feature_manager": manager,
            "cold_pull_materials": ("PLA",),
            "_operation_context_status": lambda self, eventtime=None: {},
        })()
        feature = UI_TEST.UITestRun(host)
        rendered = []
        feature.scenarios._show = lambda page: rendered.append((
            page, extruder.extruder_calibration.phase,
            extruder.extruder_calibration.active))
        phases = (
            "intro", "material", "cold_pull", "cut", "cooling",
            "remove", "load", "mark_first", "mark_second",
            "measure_ready", "input", "warning", "result",
            "exit_warning", "saved",
        )

        for phase in phases:
            feature.scenarios._render_extruder_phase(phase)

        self.assertEqual([item[1] for item in rendered], list(phases))
        self.assertTrue(all(item[0] == FEATHER.ScreenPage.EXTRUDER_CALIBRATION
                            and item[2] for item in rendered))
        self.assertIs(extruder.extruder_calibration, original_session)

    def test_component_suite_discovers_declarative_pages_without_hardware(self):
        feature = UI_TEST.UITestRun(object())

        steps = feature.scenarios.build_steps("COMPONENT")
        labels = [step["label"] for step in steps]
        captures = [
            label for step, label in zip(steps, labels)
            if step["kind"] == "capture"]

        self.assertEqual(labels[0], "baseline")
        self.assertEqual(labels[1], "component-context-start")
        self.assertEqual(labels[2], "component-pause-timer")
        self.assertEqual(labels[-2], "component-resume-timer")
        self.assertEqual(labels[-1], "component-context-verify")
        self.assertEqual(len(captures), 10)
        self.assertEqual(len(set(captures)), 10)
        self.assertIn("component-default-home", captures)
        self.assertIn("component-default-render-benchmark", captures)
        self.assertTrue(all(
            label == "baseline" or label.startswith("component-default-")
            for label in captures))

    def test_component_render_uses_declared_page_title(self):
        renderer = mock.Mock()
        renderer.begin_page.return_value = ["header"]
        page = mock.Mock()
        page.title = "FORGE-X // FEATHER"
        page.page_key.value = "HOME.DASHBOARD"
        page.draw.return_value = ["body"]
        feature = UI_TEST.UITestRun(object())
        feature.host = mock.Mock(renderer=renderer)

        feature.scenarios._render_component_default(page)

        renderer.begin_page.assert_called_once_with(
            "FORGE-X // FEATHER", back=False)
        renderer.send.assert_called_once_with(["header", "body"])
        renderer.footer.assert_called_once_with(
            "NOZZLE 25/0C | BED 25/0C", "PREVIEW | IDLE")

    def test_component_cases_accept_only_known_mutable_typed_state(self):
        feature = UI_TEST.UITestRun(object())
        payload = [{
            "id": "move-fine",
            "page": "ui.pages.keys.AppPage.MOVE_STEP",
            "state": {
                "ui.pages.move.state.MoveState.JOG_STEP": 0.1,
            },
        }]
        encoded = SCENARIOS.base64.urlsafe_b64encode(
            json.dumps(payload).encode("utf-8")).decode("ascii")

        cases = feature.scenarios._decode_component_cases(encoded)

        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["id"], "move-fine")
        self.assertEqual(
            [str(key) for key in cases[0]["state"]],
            ["ui.pages.move.state.MoveState.JOG_STEP"])

        payload[0]["state"] = {
            "ui.pages.move.state.MoveState.CURSOR": [10, 20],
        }
        encoded = SCENARIOS.base64.urlsafe_b64encode(
            json.dumps(payload).encode("utf-8")).decode("ascii")
        with self.assertRaisesRegex(ValueError, "bounded declared state"):
            feature.scenarios._decode_component_cases(encoded)

        payload[0].update({
            "page": "ui.pages.keys.AppPage.Z_OFFSET_PAPER",
            "state": {
                "ui.pages.z_offset.paper.state.PaperState.PROBING": True,
            },
        })
        encoded = SCENARIOS.base64.urlsafe_b64encode(
            json.dumps(payload).encode("utf-8")).decode("ascii")
        readonly = feature.scenarios._decode_component_cases(encoded)
        self.assertEqual(
            [str(key) for key in readonly[0]["state"]],
            ["ui.pages.z_offset.paper.state.PaperState.PROBING"])

    def test_printer_and_host_use_the_same_ui_fingerprint_contract(self):
        self.assertEqual(
            UI_TEST.UITestRun._ui_fingerprint(),
            HYBRID.ui_fingerprint(ROOT))

    def test_ui_filament_back_preserves_target_before_leaving_materials(self):
        feature = UI_TEST.UITestRun(object())
        steps = feature.scenarios.build_steps("UI")
        labels = [step["label"] for step in steps]
        action_capture = next(
            index for index, step in enumerate(steps)
            if step["label"] == "ui-filament-action"
            and step["kind"] == "capture")

        self.assertEqual(labels[action_capture + 1], "ui-filament-cooling")
        self.assertEqual(labels[action_capture + 2], "ui-filament-cooling")
        self.assertEqual(labels[action_capture + 3], "ui-filament-target")
        self.assertEqual(
            (steps[action_capture + 4]["action"],
             steps[action_capture + 4]["page"]),
            ("nav.back", FEATHER.ScreenPage.FILAMENT_MATERIAL))
        self.assertEqual(
            labels[action_capture + 5], "ui-filament-target-preserved")
        self.assertEqual(labels[action_capture + 6],
                         "ui-filament-back-materials")

    def test_safe_filament_snapshot_supports_real_extruder_shape(self):
        class Extruder:
            heater = object()

            def get_status(self, eventtime):
                return {"temperature": 31.0, "target": 0.0}

        original = Extruder()
        seen = []
        host = type("Host", (), {})()
        host.heating_materials = ("PETG",)
        host.filament_material = None
        host.extruder = original
        host.page = FEATHER.ScreenPage.FILAMENT_MATERIAL
        host.reactor = type("Reactor", (), {
            "monotonic": lambda self: 1.0,
        })()

        def show(page):
            snapshot = host.extruder.get_status(0.0)
            seen.append(("show", snapshot, host.extruder.min_extrude_temp))
            host.page = page

        class FilamentFeature:
            def update(self, eventtime):
                snapshot = host.extruder.get_status(eventtime)
                seen.append((
                    "update", snapshot, host.extruder.min_extrude_temp))

        filament = FilamentFeature()
        host.feature_manager = type("FeatureManager", (), {
            "get": lambda self, name: filament,
        })()
        host._show_page = show
        feature = UI_TEST.UITestRun(host)
        feature.material = "PETG"

        feature.scenarios._render_safe_filament_action()
        feature.scenarios._render_safe_filament_cooling()

        self.assertIs(host.extruder, original)
        self.assertEqual(host.filament_material, "PETG")
        self.assertEqual(seen, [
            ("show", {"temperature": 130.4, "target": 250.0}, 170.0),
            ("update", {"temperature": 260.4, "target": 250.0}, 170.0),
        ])

    def test_hardware_calibration_open_resets_ui_catalog_page(self):
        calibration = type("Calibration", (), {"calibration_page": 2})()
        manager = type("Manager", (), {
            "get": lambda self, name: calibration,
        })()
        host = type("Host", (), {
            "feature_manager": manager,
            "page": FEATHER.ScreenPage.IDLE_HOME,
            "_show_page": lambda self, page: setattr(self, "page", page),
        })()
        feature = UI_TEST.UITestRun(host)

        feature.scenarios._open_calibration_home()

        self.assertEqual(calibration.calibration_page, 0)
        self.assertEqual(host.page, FEATHER.ScreenPage.CALIBRATION_HOME)

    def test_test_mode_blocks_only_persistent_actions(self):
        feature = UI_TEST.UITestRun(object())
        self.assertFalse(feature.blocks_action("z.save"))
        feature.running = True
        self.assertTrue(feature.input_blocked)
        self.assertTrue(feature.blocks_action("z.save"))
        self.assertTrue(feature.blocks_action(
            UI_TEST.z_actions.SAVE.wire_id))
        self.assertTrue(feature.blocks_action(
            UI_TEST.z_actions.SAFE_SAVE.wire_id))
        self.assertTrue(feature.blocks_action("cal.mesh.save"))
        self.assertFalse(feature.blocks_action("z.farther"))
        feature.finalizing = True
        self.assertFalse(feature.input_blocked)
        self.assertFalse(feature.theme_update_blocked)
        self.assertFalse(feature.blocks_action("cal.mesh.save"))

    def test_controller_drops_persistent_action_while_runner_is_active(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.print_state = FEATHER.PrintState.IDLE
        controller.command_depth = 0
        controller.busy_message = None
        controller.feature_manager = LazyFeatureManager(
            controller, FEATHER.FEATURE_SPECS)
        feature = controller.feature_manager.get("ui_test")
        run = UI_TEST.UITestRun(controller)
        run.running = True
        feature.current_run = run

        # Returning before page routing is the contract: no save handler or
        # additional controller fixture is needed for this safety check.
        controller._dispatch_action("cal.mesh.save")

    def test_test_action_keeps_feedback_timing_and_reports_failure(self):
        class FailingOwner:
            name = "failing"

            def allows_action(self, page, action):
                return True

            def handle_action(self, page, action):
                raise RuntimeError("synthetic command failed")

        callbacks = []
        feature = UI_TEST.UITestRun(None)
        owner = FailingOwner()

        class Manager:
            input_blocked = True

            def owner_name(self, page):
                return owner.name

            def resolve_semantic_action(self, page, action):
                return owner, None

            def handle_immediate_action(self, page, action):
                return False

        feedback = []
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.print_state = FEATHER.PrintState.IDLE
        controller.command_depth = 0
        controller.busy_message = None
        controller.page = FEATHER.ScreenPage.IDLE_HOME
        controller.previous_page = FEATHER.ScreenPage.IDLE_HOME
        controller.last_action_time = -1.0
        controller.pending_action = None
        controller.touch_feedback_pending = False
        controller.reactor = type("Reactor", (), {
            "monotonic": lambda self: 1.0,
            "register_callback": lambda self, callback, waketime=None:
            callbacks.append(callback),
        })()
        controller.renderer = type("Renderer", (), {
            "generation": 1,
            "_buttons": {"synthetic.action": ()},
            "_toggles": {},
            "_hitboxes": {},
            "flash_button": lambda self, action:
            feedback.append(("down", action)) or True,
            "restore_button": lambda self, action:
            feedback.append(("up", action)) or True,
        })()
        controller.feature_manager = Manager()
        messages = []

        def show_message(message, page):
            messages.append((message, page))
            controller.message = message
            controller.page = FEATHER.ScreenPage.MESSAGE

        controller._show_message = show_message
        feature.host = controller
        feature.running = True
        completed = []
        feature._complete = lambda outcome, reason: completed.append(
            (outcome, reason))

        controller._handle_touch_action("synthetic.action")
        self.assertEqual(feedback, [])

        feature._tap("synthetic.action")
        self.assertEqual(feedback, [("down", "synthetic.action")])

        callbacks.pop()(1.08)
        self.assertEqual(feedback[-1], ("up", "synthetic.action"))
        self.assertEqual(messages, [("synthetic command failed",
                                     FEATHER.ScreenPage.IDLE_HOME)])

        feature._after_tap(1.22, {"label": "synthetic.action"}, None)
        self.assertEqual(completed, [("failed", "synthetic command failed")])

    def test_nonphysical_cleanup_does_not_issue_hardware_gcode(self):
        shown = []
        host = type("Host", (), {
            "timer": None,
            "reactor": type("Reactor", (), {"NOW": 0.0})(),
            "filament_material": "PETG",
            "previous_page": FEATHER.ScreenPage.MAIN_MENU,
            "_run_script": lambda self, command: (_ for _ in ()).throw(
                AssertionError("UI suite issued hardware G-code: %s" % command)),
            "_show_page": lambda self, page: shown.append(page),
        })()
        for suite in ("UI", "COMPONENT", "RENDER"):
            feature = UI_TEST.UITestRun(host)
            feature.suite = suite
            feature.snapshot = UI_TEST.PrinterStateSnapshot(
                FEATHER.ScreenPage.IDLE_HOME, FEATHER.ScreenPage.CONTROL_HOME,
                "PLA", 0.0, None, "", 0.0, 0.0, 0.0, False)
            feature._restore_state()

        self.assertEqual(host.filament_material, "PLA")
        self.assertEqual(shown, [FEATHER.ScreenPage.IDLE_HOME] * 3)
        self.assertEqual(host.previous_page, FEATHER.ScreenPage.CONTROL_HOME)

    def test_status_does_not_load_lazy_feature(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.feature_manager = LazyFeatureManager(
            controller, FEATHER.FEATURE_SPECS)
        gcmd = GCmd({"ACTION": "STATUS"})

        controller.cmd_FEATHER_UI_TEST(gcmd)

        self.assertIsNone(controller.feature_manager.peek("ui_test"))
        self.assertEqual(
            gcmd.responses, ["Feather UI test: idle (feature not loaded)"])

    def test_run_is_the_only_command_path_that_loads_feature(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.feature_manager = LazyFeatureManager(
            controller, FEATHER.FEATURE_SPECS)
        gcmd = GCmd({
            "ACTION": "RUN", "SUITE": "UI", "CONFIRM": 1,
            "CAPTURE_INTERVAL": "5",
        })
        calls = []

        with mock.patch.object(
                UI_TEST_FEATURE.UITestFeature, "run",
                lambda feature, *args: calls.append((feature, args))):
            controller.cmd_FEATHER_UI_TEST(gcmd)

        feature = controller.feature_manager.peek("ui_test")
        self.assertIsNotNone(feature)
        self.assertEqual(calls, [(
            feature, (gcmd, "UI", "", 1, "", "5"))])

    def test_feature_publishes_only_a_successfully_started_run(self):
        host = object()
        feature = UI_TEST_FEATURE.UITestFeature(host)
        gcmd = GCmd()
        started = []

        class Candidate:
            running = False

            def __init__(self, candidate_host, session_id, on_finished):
                self.host = candidate_host
                self.on_finished = on_finished

            def run(self, *args):
                started.append(args)
                raise RuntimeError("setup failed")

        with mock.patch.object(UI_TEST_FEATURE, "UITestRun", Candidate):
            with self.assertRaisesRegex(RuntimeError, "setup failed"):
                feature.run(gcmd, "UI", "", 1)

        self.assertIsNone(feature.current_run)
        self.assertEqual(len(started), 1)

    def test_late_finished_callback_cannot_clear_a_newer_run(self):
        feature = UI_TEST_FEATURE.UITestFeature(object())
        older = object()
        newer = object()
        feature.current_run = newer

        feature._run_finished(older)

        self.assertIs(feature.current_run, newer)

    def test_running_feature_exposes_current_test_step_for_host_telemetry(self):
        run = object.__new__(UI_TEST.UITestRun)
        run.running = True
        run.finalizing = False
        run.suite = "CONTEXT_MATERIAL"
        run.phase = "cold_pull"
        run.step_index = 2
        run.run_id = "synthetic-run"
        run.run_directory = "/data/feather-ui-tests/synthetic-run"
        run.steps = [
            {"label": "prepare"},
            {"label": "heat"},
            {"label": "cool"},
        ]
        feature = UI_TEST_FEATURE.UITestFeature(object())
        feature.current_run = run

        self.assertEqual(feature.get_status(), {
            "running": True,
            "finalizing": False,
            "run_id": "synthetic-run",
            "directory": "/data/feather-ui-tests/synthetic-run",
            "suite": "CONTEXT_MATERIAL",
            "phase": "cold_pull",
            "step": "cool",
            "step_index": 2,
            "step_count": 3,
        })

    def test_failed_run_setup_removes_marker_directory_and_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            active = root / "active.json"
            workers = []

            class Worker:
                def __init__(self, reactor, directory):
                    self.directory = directory
                    self.stopped = False
                    workers.append(self)

                def stop(self):
                    self.stopped = True

            host = type("Host", (), {
                "renderer": type("Renderer", (), {
                    "get_status": lambda self: {"dropped_batches": 0},
                })(),
                "reactor": object(),
            })()
            run = UI_TEST.UITestRun(host)
            run._preflight = lambda *args, **kwargs: None
            run._capture_original_state = lambda: None
            run._recover_stale_marker = lambda: None
            run._environment = lambda: {}
            run._build_steps = lambda suite: []
            run._attach_context_recorder = lambda: (_ for _ in ()).throw(
                RuntimeError("recorder setup failed"))

            with mock.patch.object(UI_TEST, "ARTIFACT_ROOT", str(root)), \
                    mock.patch.object(UI_TEST, "ACTIVE_MARKER", str(active)), \
                    mock.patch.object(UI_TEST, "ArtifactWorker", Worker):
                with self.assertRaisesRegex(
                        RuntimeError, "recorder setup failed"):
                    run.run(GCmd(), "UI", "", 1)

            self.assertEqual(len(workers), 1)
            self.assertTrue(workers[0].stopped)
            self.assertIsNone(run.run_directory)
            self.assertEqual(list(root.iterdir()), [])

    def test_completion_restores_once_before_artifact_finalization(self):
        calls = []

        class Snapshot:
            def restore(self, host, reactor, hardware):
                calls.append(("snapshot", hardware))

        class Fixture:
            def restore(self, suite):
                calls.append(("fixture", suite))

        class Worker:
            def finish(self, summary, callback):
                calls.append(("finish", summary["outcome"]))

        run = UI_TEST.UITestRun(type("Host", (), {
            "reactor": object(),
        })())
        run.running = True
        run.suite = "UI"
        run.run_id = "test-run"
        run.started_at = UI_TEST.time.time()
        run.snapshot = Snapshot()
        run.context_fixture = Fixture()
        run.worker = Worker()

        run._complete("failed", "boom")
        run._complete("failed", "again")

        self.assertEqual(calls, [
            ("fixture", "UI"), ("snapshot", False),
            ("finish", "failed"),
        ])
        self.assertTrue(run.finalizing)
        self.assertFalse(run.input_blocked)

    def test_resource_marker_is_serialized_by_the_artifact_worker(self):
        markers = []
        run = UI_TEST.UITestRun(type("Host", (), {
            "reactor": object(),
        })())
        run.running = True
        run.run_id = "test-run"
        run.run_directory = "/data/feather-ui-tests/test-run"
        run.suite = "CONTEXT_PRINT"
        run.phase = "context_print"
        run.worker = type("Worker", (), {
            "marker": lambda self, value: markers.append(value),
        })()

        with mock.patch.object(
                UI_TEST, "_atomic_json",
                side_effect=AssertionError("reactor wrote active marker")):
            run._persist_resource_marker()

        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0]["run_id"], "test-run")
        self.assertEqual(markers[0]["phase"], "context_print")

    def test_periodic_capture_uses_one_shot_frame_and_minimal_metadata(self):
        captures = []
        callbacks = []
        run = UI_TEST.UITestRun(type("Host", (), {
            "reactor": type("Reactor", (), {
                "register_callback": lambda self, callback, when:
                    callbacks.append((when, callback)),
            })(),
            "toolhead": type("Toolhead", (), {
                "check_busy": lambda self, eventtime:
                    (eventtime, eventtime, True),
            })(),
            "_operation_context_status": lambda self, eventtime: {
                "current_state": None,
            },
        })())
        run.running = True
        run.screen_capture_interval = 5.0
        run.next_periodic_capture = 105.0
        run.worker = type("Worker", (), {
            "capture": lambda self, number, label, metadata, callback,
            settle=True:
                captures.append((number, label, metadata, callback, settle)),
            "log": lambda self, message: None,
        })()
        run.host.page = type("Page", (), {"name": "CALIBRATION_PROGRESS"})()
        run.host.renderer = type("Renderer", (), {"generation": 12})()

        run._periodic_capture_tick(104.9)
        self.assertEqual(captures, [])

        run._periodic_capture_tick(105.0)
        self.assertEqual(len(captures), 1)
        self.assertEqual(captures[0][1], "periodic-001")
        self.assertEqual(captures[0][2]["capture_kind"], "periodic")
        self.assertEqual(captures[0][2]["page"], "CALIBRATION_PROGRESS")
        self.assertEqual(captures[0][2]["generation"], 12)
        self.assertNotIn("buttons", captures[0][2])
        self.assertFalse(captures[0][4])
        self.assertEqual(run.periodic_capture_pending, 1)
        self.assertEqual(run.next_periodic_capture, 110.0)
        self.assertEqual(callbacks[0][0], 110.0)

        captures[0][3]({"file": "001-periodic.bmp"})
        self.assertEqual(run.periodic_capture_pending, 0)

        callbacks[0][1](110.0)
        self.assertEqual(len(captures), 2)
        callbacks[1][1](115.0)
        self.assertEqual(len(captures), 2)

    def test_periodic_capture_skips_motion_and_timing_critical_contexts(self):
        callbacks = []
        events = []
        host = type("Host", (), {
            "reactor": type("Reactor", (), {
                "register_callback": lambda self, callback, when:
                    callbacks.append((when, callback)),
            })(),
            "toolhead": type("Toolhead", (), {
                "check_busy": lambda self, eventtime:
                    (eventtime + 1.0, eventtime, True),
            })(),
            "_operation_context_status": lambda self, eventtime: {
                "current_state": None,
            },
        })()
        run = UI_TEST.UITestRun(host)
        run.running = True
        run.screen_capture_interval = 5.0
        run.next_periodic_capture = 105.0
        run.worker = mock.Mock()
        run._event = events.append

        run._periodic_capture_tick(105.0)
        self.assertIn("periodic capture skipped: toolhead busy", events)
        run.worker.capture.assert_not_called()
        self.assertEqual(run.next_periodic_capture, 110.0)

        host.toolhead.check_busy = lambda eventtime: (
            eventtime, eventtime, True)
        host._operation_context_status = lambda eventtime: {
            "current_state": "LEVELING",
        }
        callbacks[0][1](110.0)

        self.assertIn(
            "periodic capture skipped: operation state LEVELING", events)
        run.worker.capture.assert_not_called()
        self.assertEqual(run.next_periodic_capture, 115.0)

    def test_interrupted_context_cleanup_removes_only_owned_resources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            run_id = "20260811-120000-000001-context_print"
            owned = root / ("feather-context-%s-recovery.gcode" % run_id)
            unrelated = root / "keep.gcode"
            checkpoint = root / "checkpoint.json"
            owned.write_text("G4 P1\n", encoding="utf-8")
            unrelated.write_text("G4 P1\n", encoding="utf-8")
            checkpoint.write_text(json.dumps({
                "file_path": str(owned),
            }), encoding="utf-8")

            class State:
                IDLE = "idle"

            resurrection = type("Resurrection", (), {
                "file_path": str(checkpoint),
                "state": State(),
                "_change_state": lambda self, state: setattr(
                    self, "state", state),
            })()
            host = type("Host", (), {
                "virtual_sdcard": type("SD", (), {
                    "sdcard_dirname": str(root),
                })(),
                "resurrection": resurrection,
            })()
            marker = {
                "run_id": run_id,
                "resources": {
                    "files": [str(owned), str(unrelated)],
                    "checkpoint": str(checkpoint),
                },
            }

            UI_TEST.recover_interrupted_context_resources(host, marker)

            self.assertFalse(owned.exists())
            self.assertFalse(checkpoint.exists())
            self.assertTrue(unrelated.exists())

    def test_stale_same_process_run_is_cleaned_before_next_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            previous = root / "old"
            previous.mkdir()
            active = root / "active.json"
            active.write_text(json.dumps({
                "pid": 42, "runtime_z": 0.125, "mesh_profile": "auto",
                "directory": str(previous), "session": "42-test",
                "hardware": True,
            }), encoding="utf-8")
            commands = []
            host = type("Host", (), {
                "_run_script": lambda self, command: commands.append(command),
            })()
            feature = UI_TEST.UITestRun(host)
            feature.session_id = "42-test"

            with mock.patch.object(UI_TEST, "ACTIVE_MARKER", str(active)), \
                    mock.patch.object(UI_TEST.os, "getpid", return_value=42):
                feature._recover_stale_marker()

            self.assertEqual(commands, [
                "TURN_OFF_HEATERS",
                "_SET_GCODE_OFFSET Z=+0.125000 MOVE=0",
                "BED_MESH_PROFILE LOAD=auto",
                "M84",
            ])
            summary = json.loads((previous / "summary.json").read_text())
            self.assertEqual(summary["outcome"], "interrupted")
            self.assertFalse(active.exists())

    def test_stale_different_session_does_not_reapply_volatile_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            active = root / "active.json"
            active.write_text(json.dumps({
                "session": "old", "hardware": True,
                "runtime_z": 1.5, "mesh_profile": "auto",
            }), encoding="utf-8")
            commands = []
            host = type("Host", (), {
                "_run_script": lambda self, command: commands.append(command),
            })()
            feature = UI_TEST.UITestRun(host)
            feature.session_id = "new"

            with mock.patch.object(UI_TEST, "ACTIVE_MARKER", str(active)):
                feature._recover_stale_marker()

            self.assertEqual(commands, [])
            self.assertFalse(active.exists())

    def test_extended_context_suites_are_separate_and_ordered(self):
        host = type("Host", (), {
            "cold_pull_materials": ("PLA",),
            "renderer": type("Renderer", (), {
                "get_status": lambda self: {"typer_restarts": 0},
            })(),
        })()
        feature = UI_TEST.UITestRun(host)
        feature.material = "PLA"
        material = feature.scenarios.build_steps("CONTEXT_MATERIAL")
        material_labels = [step["label"] for step in material]
        self.assertLess(material_labels.index("filament-context-start"),
                        material_labels.index("prompt-PLA"))
        self.assertLess(material_labels.index("prompt-Load"),
                        material_labels.index("prompt-Purge"))
        self.assertLess(material_labels.index("prompt-Purge"),
                        material_labels.index("prompt-Unload"))
        self.assertLess(material_labels.index("prompt-Unload"),
                        material_labels.index("prompt-Done"))
        self.assertLess(material_labels.index("filament-context-verify"),
                        material_labels.index("cold_pull-context-start"))
        self.assertEqual([
            step["label"] for step in material
            if step["kind"] == "capture"
        ], [
            "baseline",
            "filament-material-prompt-screen",
            "filament-action-prompt-screen",
            "filament-loaded-screen",
            "filament-purged-screen",
            "filament-unloaded-screen",
            "filament-done-screen",
            "cold_pull-material-prompt-screen",
            "cold_pull-complete-screen",
        ])

        printing = feature.scenarios.build_steps("CONTEXT_PRINT")
        print_labels = [step["label"] for step in printing]
        expected = (
            "print_kamp-context-start", "print_kamp-file-open",
            "print_kamp-complete", "print_kamp-finished-dismiss",
            "print_kamp-context-verify",
            "print_mesh-context-start", "print_mesh-pause-motion-complete",
            "print_mesh-paused",
            "print_mesh-resumed", "print_mesh-pause-for-recovery",
            "print_mesh-recovery-pause-motion-complete",
            "print_mesh-recovery-paused", "print_mesh-idle-timeout",
            "print_mesh-checkpoint", "print_mesh-cancel",
            "print_mesh-cancelled", "print_mesh-cancelled-dismiss",
            "print_mesh-activate-recovery",
            "print_mesh-context-verify", "recovery-context-start",
            "recovery-printing", "recovery-complete",
            "recovery-finished-dismiss",
            "recovery-context-verify",
        )
        positions = [print_labels.index(label) for label in expected]
        self.assertEqual(positions, sorted(positions))
        # The recovery scenario prints a real model and leaves it on the bed,
        # so no later scenario may touch the bed centre again.
        self.assertEqual(
            [step["label"] for step in printing
             if step["label"].endswith("-context-start")][-1],
            "recovery-context-start")
        print_phases = dict(
            (step["label"], step["phase"]) for step in printing)
        self.assertEqual(print_phases["print_mesh-paused"], "print_mesh")
        self.assertEqual(print_phases["recovery-complete"], "recovery")
        self.assertEqual(
            print_phases["print_kamp-complete"], "print_kamp")
        self.assertEqual([
            step["label"] for step in printing
            if step["kind"] == "capture"
        ], [
            "baseline",
            "print_kamp-confirm-screen",
            "print_kamp-printing-screen",
            "print_kamp-complete-screen",
            "print_mesh-printing-screen",
            "print_mesh-paused-screen",
            "print_mesh-resumed-screen",
            "print_mesh-recovery-paused-screen",
            "print_mesh-cancelled-screen",
            "print_mesh-recovery-prompt-screen",
            "recovery-confirm-screen",
            "recovery-progress-screen",
            "recovery-printing-screen",
            "recovery-complete-screen",
        ])

        full_labels = [
            step["label"] for step in feature.scenarios.build_steps("FULL")]
        self.assertFalse(any(label.startswith("context_print-")
                             for label in full_labels))
        self.assertFalse(any(label.startswith("context_material-")
                             for label in full_labels))

    def test_context_cancel_arms_recovery_and_leaves_no_pause_state(self):
        class State:
            pass

        State.PAUSED = State()
        State.RESURRECTION = State()

        checkpoint = {"present": True}
        printer = {"print_state": "paused", "paused": True}
        resurrection = type("Resurrection", (), {})()
        resurrection.state = State.PAUSED
        resurrection._pause_checkpoint_active = True
        resurrection._resume_pending = True
        resurrection._change_state = lambda state: setattr(
            resurrection, "state", state)

        def cancel():
            if resurrection.state != State.RESURRECTION:
                checkpoint["present"] = False
            # Klipper resets the file without touching the pause state.
            printer["print_state"] = "cancelled"

        def run_script(script):
            for line in script.splitlines():
                if line.strip().upper() == "CLEAR_PAUSE":
                    printer["paused"] = False

        host = type("Host", (), {})()
        host.reactor = type("Reactor", (), {
            "monotonic": lambda self: 1.0,
        })()
        host.resurrection = resurrection
        host.virtual_sdcard = type("SD", (), {
            "do_cancel": lambda self: cancel(),
        })()
        host._run_script = run_script
        fixture = UI_TEST.ContextTestFixture(
            host, host.reactor, "test-run", "PLA")
        fixture.checkpoint_ready = lambda: checkpoint["present"]
        run = type("Run", (), {})()
        run.host = host
        run.context_fixture = fixture

        SCENARIOS.ScenarioCatalog(run)._cancel_context_print()

        self.assertTrue(checkpoint["present"])
        self.assertEqual(resurrection.state, State.RESURRECTION)
        self.assertFalse(resurrection._pause_checkpoint_active)
        self.assertFalse(resurrection._resume_pending)
        self.assertEqual(printer["print_state"], "cancelled")
        # A pause state that outlives the print makes the next PAUSE a no-op
        # while the file keeps streaming, so cancelling has to clear it.
        self.assertFalse(printer["paused"])

    def test_preflight_rejects_a_pause_state_left_by_an_earlier_print(self):
        host = type("Host", (), {})()
        host.reactor = type("Reactor", (), {
            "monotonic": lambda self: 1.0,
        })()
        host.print_state = FEATHER.PrintState.IDLE
        host.print_stats = type("Stats", (), {
            "get_status": lambda self, eventtime: {"state": "standby"},
        })()
        host.virtual_sdcard = type("SD", (), {
            "is_active": lambda self: False,
        })()
        host.pause_resume = type("PauseResume", (), {
            "get_status": lambda self, eventtime: {"is_paused": True},
        })()
        host.command_depth = 0
        host.renderer = type("Renderer", (), {"active": True})()
        host.toolhead = type("Toolhead", (), {
            "get_status": lambda self, eventtime: {"velocity": 0.0},
        })()
        feature = UI_TEST.UITestRun(host)

        with self.assertRaisesRegex(RuntimeError, "paused"):
            feature._preflight("RENDER", hardware_targets=False)

    def test_action_prompt_selection_uses_exact_visible_label(self):
        host = type("Host", (), {})()
        host.action_prompt = {"buttons": {
            "prompt.button.0": {"label": "Load"},
            "prompt.button.1": {"label": "Unload"},
        }}
        feature = UI_TEST.UITestRun(host)

        self.assertEqual(feature.scenarios._prompt_action_for_label("load"),
                         "prompt.button.0")
        with self.assertRaisesRegex(RuntimeError, "found 0"):
            feature.scenarios._prompt_action_for_label("Purge")

    def test_context_file_is_published_through_real_browser_entry(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "runner.gcode"
            path.write_text("G4 P1\n", encoding="utf-8")
            host = type("Host", (), {})()
            host.virtual_sdcard = type("SD", (), {
                "sdcard_dirname": temporary,
            })()
            host.reactor = type("Reactor", (), {
                "monotonic": lambda self: 12.0,
            })()
            host.file_entry_cache = {"internal": ["original"]}
            host.file_entry_loaded_at = {"internal": 4.0}
            host.file_entries = ["original"]
            host.file_source = "internal"
            host.file_page = 2
            host.file_scan_token = 3
            host.page = FEATHER.ScreenPage.IDLE_HOME
            host._show_page = lambda page: setattr(host, "page", page)
            feature = UI_TEST.UITestRun(host)
            feature.context_fixture = UI_TEST.ContextTestFixture(
                host, host.reactor, "test-run", "PLA")

            feature.scenarios._open_context_file(str(path))

            entry = host.file_entry_cache["internal"][0]
            self.assertEqual(entry["path"], str(path.resolve()))
            self.assertEqual(host.file_entries, [entry])
            self.assertEqual(host.page, FEATHER.ScreenPage.FILE_BROWSER)
            self.assertEqual(host.file_scan_token, 4)
            self.assertEqual(
                feature.context_fixture.file_browser["cache"],
                ["original"])

    def test_context_print_waits_for_ui_terminal_state(self):
        host = type("Host", (), {})()
        host.reactor = type("Reactor", (), {
            "monotonic": lambda self: 1.0,
        })()
        host.print_stats = type("Stats", (), {
            "get_status": lambda self, eventtime: {"state": "complete"},
        })()
        host.virtual_sdcard = type("SD", (), {
            "is_active": lambda self: False,
        })()
        host.print_state = FEATHER.PrintState.PRINTING
        host.page = FEATHER.ScreenPage.PRINTING
        host.renderer = type("Renderer", (), {"_buttons": {}})()
        feature = UI_TEST.UITestRun(host)

        self.assertFalse(feature.scenarios._context_print_complete())
        host.print_state = FEATHER.PrintState.IDLE
        self.assertFalse(feature.scenarios._context_print_complete())
        host.page = FEATHER.ScreenPage.MESSAGE
        self.assertFalse(feature.scenarios._context_print_complete())
        host.renderer._buttons["message.ok"] = ()
        self.assertTrue(feature.scenarios._context_print_complete())

    def test_context_print_waits_for_pause_control(self):
        status = {"state": "printing"}
        host = type("Host", (), {})()
        host.reactor = type("Reactor", (), {
            "monotonic": lambda self: 1.0,
        })()
        host.print_stats = type("Stats", (), {
            "get_status": lambda self, eventtime: dict(status),
        })()
        host.page = FEATHER.ScreenPage.PRINTING
        host.renderer = type("Renderer", (), {"_buttons": {}})()
        feature = UI_TEST.UITestRun(host)

        self.assertTrue(feature.scenarios._context_printing())
        self.assertFalse(feature.scenarios._context_print_controls_ready())
        host.renderer._buttons["print.pause"] = ()
        self.assertTrue(feature.scenarios._context_print_controls_ready())

        status["state"] = "paused"
        host.page = FEATHER.ScreenPage.PAUSED
        host.renderer._buttons = {"print.resume": ()}
        self.assertTrue(feature.scenarios._context_paused())

        status["state"] = "cancelled"
        host.print_state = FEATHER.PrintState.IDLE
        host.page = FEATHER.ScreenPage.MESSAGE
        host.virtual_sdcard = type("SD", (), {
            "is_active": lambda self: False,
        })()
        host.renderer._buttons = {"message.ok": ()}
        self.assertTrue(feature.scenarios._context_cancelled())

    def test_extended_suites_require_confirm_two(self):
        feature = UI_TEST.UITestRun(object())
        gcmd = GCmd()
        for suite in ("CONTEXT_PRINT", "CONTEXT_MATERIAL"):
            with self.subTest(suite=suite), self.assertRaisesRegex(
                    RuntimeError, "requires CONFIRM=2"):
                feature.run(gcmd, suite, "PLA", 1)

    def test_periodic_capture_interval_is_bounded_on_printer(self):
        feature = UI_TEST.UITestRun(object())
        gcmd = GCmd()
        for interval in (-1, 4.9, 301, float("nan")):
            with self.subTest(interval=interval), self.assertRaisesRegex(
                    RuntimeError, "CAPTURE_INTERVAL"):
                feature.run(
                    gcmd, "UI", "", 1,
                    screen_capture_interval=interval)

    def test_context_print_preflight_rejects_foreign_checkpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            checkpoint = pathlib.Path(temporary) / "resurrection.json"
            checkpoint.write_text("{}", encoding="utf-8")
            empty_context = type("Context", (), {
                "get_status": lambda self, eventtime: {"contexts": ()},
            })()
            host = type("Host", (), {})()
            host.reactor = type("Reactor", (), {
                "monotonic": lambda self: 1.0,
            })()
            host.print_state = FEATHER.PrintState.IDLE
            host.print_stats = type("Stats", (), {
                "get_status": lambda self, eventtime: {"state": "standby"},
            })()
            host.virtual_sdcard = type("SD", (), {
                "is_active": lambda self: False,
            })()
            host.pause_resume = type("PauseResume", (), {
                "get_status": lambda self, eventtime: {"is_paused": False},
            })()
            host.command_depth = 0
            host.renderer = type("Renderer", (), {"active": True})()
            host.toolhead = type("Toolhead", (), {
                "get_status": lambda self, eventtime: {"velocity": 0.0},
            })()
            host.operation_context = empty_context
            host.bed_mesh = type("Mesh", (), {
                "get_status": lambda self, eventtime: {
                    "profiles": ("auto",)},
            })()
            host.probe = object()
            heater = type("Heater", (), {
                "get_status": lambda self, eventtime: {"target": 0.0},
            })()
            host.extruder = heater
            host.heater_bed = heater
            host.heating_materials = ("PLA",)
            host.resurrection = type("Resurrection", (), {
                "enabled": True, "file_path": str(checkpoint),
            })()
            feature = UI_TEST.UITestRun(host)

            with self.assertRaisesRegex(
                    RuntimeError, "foreign recovery checkpoint"):
                feature._preflight("CONTEXT_PRINT", hardware_targets=True)

    def test_context_runtime_cleanup_restores_memory_and_owned_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            gcode = root / "runner.gcode"
            checkpoint = root / "checkpoint.json"
            unrelated = root / "keep.gcode"
            for path in (gcode, checkpoint, unrelated):
                path.write_text("test", encoding="utf-8")

            class Params:
                def __init__(self):
                    self.variables = {
                        "check_md5": 0, "current_material": "PLA"}

                def _store_value(self, param, value):
                    return "product"

            params = Params()
            original_store = params._store_value
            params._store_value = lambda param, value: "guard"
            client = type("Client", (), {
                "variables": {"idle_timeout": 2},
            })()
            commands = []
            cancelled = []
            printer = {"paused": True}

            def run_script(script):
                commands.append(script)
                for line in script.splitlines():
                    if line.strip().upper() == "CLEAR_PAUSE":
                        printer["paused"] = False

            host = type("Host", (), {})()
            host.reactor = type("Reactor", (), {
                "monotonic": lambda self: 1.0,
            })()
            host.virtual_sdcard = type("SD", (), {
                "is_active": lambda self: False,
                "file_path": lambda self: str(gcode),
                "do_cancel": lambda self: cancelled.append(True),
            })()
            host.operation_context = type("Context", (), {
                "get_status": lambda self, eventtime: {"contexts": ()},
            })()
            host.params = params
            host.idle_timeout = object()
            host.resurrection = type("Resurrection", (), {
                "file_path": str(checkpoint),
            })()
            host._run_script = run_script
            feature = UI_TEST.UITestRun(host)
            feature.suite = "CONTEXT_PRINT"
            fixture = UI_TEST.ContextTestFixture(
                host, host.reactor, "test-run", "PETG")
            fixture.files = [str(gcode)]
            fixture.checkpoint = str(checkpoint)
            fixture.saved_mod_params = {"check_md5": 1}
            fixture.saved_current_material = "PETG"
            fixture.params_store_guard = (params, True, original_store)
            fixture.client_macro = client
            fixture.client_idle_timeout = 3600
            fixture.idle_timeout = 600.0
            feature.context_fixture = fixture

            feature._restore_context_runtime()

            self.assertEqual(params.variables["check_md5"], 1)
            self.assertEqual(params.variables["current_material"], "PETG")
            self.assertIs(params._store_value, original_store)
            self.assertEqual(client.variables["idle_timeout"], 3600)
            self.assertIn("SET_IDLE_TIMEOUT TIMEOUT=600", commands)
            self.assertEqual(cancelled, [True])
            self.assertFalse(printer["paused"])
            self.assertFalse(gcode.exists())
            self.assertFalse(checkpoint.exists())
            self.assertTrue(unrelated.exists())


class ContextPrintFixtureTest(unittest.TestCase):
    """The model fixture is only useful if it is fully parameterised."""

    def _profiles(self):
        path = (PLUGINS / "feather_ui_test" / "fixtures"
                / "context_print_profiles.json")
        return json.loads(path.read_text(encoding="utf-8"))

    def test_every_shipped_profile_resolves_the_whole_template(self):
        for material in self._profiles():
            gcode = RESOURCES.load_context_print_gcode(material)
            self.assertNotRegex(gcode, r"@[A-Z_]+@")
            self.assertIn("START_PRINT ", gcode)

    def test_profile_values_reach_the_commands_that_use_them(self):
        # What matters is which profile field lands in which parameter - a
        # transposed pair would print at the wrong temperature or retract by a
        # speed. Expectations are derived from the shipped profile so this
        # protects the mapping instead of restating the JSON. Flow and fan also
        # change unit on the way in: ratio -> M221 percent, ratio -> M106 PWM.
        for material, profile in self._profiles().items():
            gcode = RESOURCES.load_context_print_gcode(material)
            self.assertIn(
                "START_PRINT EXTRUDER_TEMP=%g BED_TEMP=%g" % (
                    float(profile["nozzle_initial"]),
                    float(profile["bed_initial"])), gcode)
            self.assertIn(
                "SET_RETRACTION RETRACT_LENGTH=%g RETRACT_SPEED=%g "
                "UNRETRACT_EXTRA_LENGTH=0 UNRETRACT_SPEED=%g" % (
                    float(profile["retract_length"]),
                    float(profile["retract_speed"]),
                    float(profile["unretract_speed"])), gcode)
            self.assertIn(
                "M221 S%g" % (float(profile["flow_ratio"]) * 100.0), gcode)
            self.assertIn(
                "M106 S%g" % round(float(profile["fan_speed"]) * 255.0), gcode)

    def test_recovery_checkpoint_interrupts_the_middle_of_the_model(self):
        # Resurrection is only exercised meaningfully if the interrupted print
        # has both deposited layers to restore onto and layers left to finish.
        gcode = RESOURCES.load_context_print_gcode("PLA")
        total = int(re.search(r"TOTAL_LAYER=(\d+)", gcode).group(1))
        layers = [
            (int(match.group(1)), match.start())
            for match in re.finditer(r"CURRENT_LAYER=(\d+)", gcode)]
        self.assertEqual([number for number, _ in layers],
                         list(range(1, total + 1)))
        pause = gcode.index("\nPAUSE\n")
        before = [number for number, offset in layers if offset < pause]
        self.assertGreater(len(before), 1)
        self.assertLess(max(before), total)
        # "Around the middle" keeps the restored print long enough to film.
        self.assertAlmostEqual(max(before), total / 2.0, delta=2.0)
        self.assertEqual(gcode.count("\nPAUSE\n"), 1)

    def test_unknown_material_fails_instead_of_printing_something_else(self):
        with self.assertRaises(RuntimeError) as raised:
            RESOURCES.load_context_print_gcode("UNOBTAINIUM")
        self.assertIn("UNOBTAINIUM", str(raised.exception))

    def test_incomplete_profile_names_the_missing_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            (directory / "context_print_profiles.json").write_text(
                json.dumps({"PLA": {"nozzle": 220}}), encoding="utf-8")
            (directory / "context_recovery_open_box.gcode").write_text(
                "G4 P1\n", encoding="utf-8")
            with mock.patch.object(RESOURCES, "_FIXTURE_DIR", str(directory)):
                with self.assertRaises(RuntimeError) as raised:
                    RESOURCES.load_context_print_gcode("PLA")
        message = str(raised.exception)
        self.assertIn("bed_initial", message)
        self.assertIn("fan_speed", message)
        self.assertNotIn("nozzle,", message)

    def test_template_token_without_a_profile_field_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            (directory / "context_print_profiles.json").write_text(
                json.dumps({"PLA": dict(
                    (field, 1) for field in
                    RESOURCES._CONTEXT_PRINT_FIELDS)}), encoding="utf-8")
            (directory / "context_recovery_open_box.gcode").write_text(
                "START_PRINT EXTRUDER_TEMP=@NOZZLE_INITIAL@ "
                "BED_TEMP=@BED_INITIAL@\nM104 S@NOZZLE@\nM140 S@BED@\n"
                "M221 S@FLOW_PERCENT@\nM106 S@FAN_PWM@\n"
                "SET_PRESSURE_ADVANCE ADVANCE=@PRESSURE_ADVANCE@\n"
                "SET_RETRACTION RETRACT_LENGTH=@RETRACT_LENGTH@ "
                "RETRACT_SPEED=@RETRACT_SPEED@ "
                "UNRETRACT_SPEED=@UNRETRACT_SPEED@\n"
                "M104 S@CHAMBER@\n", encoding="utf-8")
            with mock.patch.object(RESOURCES, "_FIXTURE_DIR", str(directory)):
                with self.assertRaises(RuntimeError) as raised:
                    RESOURCES.load_context_print_gcode("PLA")
        self.assertIn("unresolved", str(raised.exception))

    def test_template_missing_a_parameter_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            (directory / "context_print_profiles.json").write_text(
                json.dumps({"PLA": dict(
                    (field, 1) for field in
                    RESOURCES._CONTEXT_PRINT_FIELDS)}), encoding="utf-8")
            (directory / "context_recovery_open_box.gcode").write_text(
                "START_PRINT EXTRUDER_TEMP=@NOZZLE_INITIAL@\n",
                encoding="utf-8")
            with mock.patch.object(RESOURCES, "_FIXTURE_DIR", str(directory)):
                with self.assertRaisesRegex(
                        RuntimeError, r"missing @[A-Z_]+@"):
                    RESOURCES.load_context_print_gcode("PLA")


class PrintTuningCaptureTest(unittest.TestCase):
    """The fixture overwrites tuning, so it must be restorable first."""

    @staticmethod
    def _host(pressure_advance=0.04, retraction=None, extrude_factor=1.0):
        host = type("Host", (), {})()
        host.commands = []
        host.extruder = type("Extruder", (), {
            "get_status": lambda self, eventtime: {
                "pressure_advance": pressure_advance},
        })()
        host.gcode_move = type("GcodeMove", (), {
            "get_status": lambda self, eventtime: {
                "extrude_factor": extrude_factor},
        })()
        host.printer = type("Printer", (), {
            "lookup_object": lambda self, name, default=None: retraction,
        })()
        host._run_script = lambda command: host.commands.append(command)
        return host

    @staticmethod
    def _retraction(**overrides):
        status = {
            "retract_length": 0.5, "retract_speed": 30,
            "unretract_extra_length": 0, "unretract_speed": 25,
        }
        status.update(overrides)
        return type("Retraction", (), {
            "get_status": lambda self, eventtime: dict(status),
        })()

    def test_capture_returns_floats_for_every_restored_setting(self):
        host = self._host(retraction=self._retraction(), extrude_factor=0.95)
        tuning = RESOURCES.capture_print_tuning(host, 1.0)
        self.assertEqual(tuning, {
            "pressure_advance": 0.04, "retract_length": 0.5,
            "retract_speed": 30.0, "unretract_extra_length": 0.0,
            "unretract_speed": 25.0, "extrude_factor": 0.95,
        })
        for value in tuning.values():
            self.assertIsInstance(value, float)

    def test_capture_refuses_a_printer_without_firmware_retraction(self):
        with self.assertRaises(RuntimeError):
            RESOURCES.capture_print_tuning(self._host(retraction=None), 1.0)

    def test_capture_refuses_values_it_could_not_restore(self):
        for overrides in ({"retract_speed": None},
                          {"unretract_speed": float("nan")},
                          {"retract_length": float("inf")}):
            host = self._host(retraction=self._retraction(**overrides))
            with self.assertRaises(RuntimeError):
                RESOURCES.capture_print_tuning(host, 1.0)
        for factor in (None, 0.0, -1.0):
            host = self._host(
                retraction=self._retraction(), extrude_factor=factor)
            with self.assertRaises(RuntimeError):
                RESOURCES.capture_print_tuning(host, 1.0)

    def test_restore_replays_the_captured_values(self):
        # The fixture G-code sets flow with M221 and only END_PRINT -> _STOP
        # brings it back, so an interrupted run depends on this restore.
        host = self._host(retraction=self._retraction(), extrude_factor=0.95)
        RESOURCES._restore_print_tuning(
            host, RESOURCES.capture_print_tuning(host, 1.0))
        self.assertEqual(host.commands, [
            "SET_PRESSURE_ADVANCE ADVANCE=0.04\n"
            "SET_RETRACTION RETRACT_LENGTH=0.5 RETRACT_SPEED=30 "
            "UNRETRACT_EXTRA_LENGTH=0 UNRETRACT_SPEED=25\n"
            "M221 S95",
        ])


class ContextManagerFixture:
    def __init__(self):
        self.status = self._raw({
            "contexts": [], "context_path": [], "current_state": None,
            "cancel_available": False, "cancel_pending": False,
            "cancel_target": {"type": None, "name": None, "mode": None},
            "cancel_blocker": {"type": None, "name": None},
        })
        self.original_calls = 0

    def _changed(self):
        self.original_calls += 1

    def get_status(self, eventtime):
        return dict(self.status)

    @staticmethod
    def _raw(snapshot):
        contexts = []
        for index, frame in enumerate(snapshot["contexts"], 100):
            value = dict(frame)
            value["id"] = index
            contexts.append(value)
        target = snapshot["cancel_target"]
        blocker = snapshot["cancel_blocker"]
        return {
            "contexts": contexts,
            "context_path": tuple(snapshot["context_path"]),
            "current_state": snapshot["current_state"],
            "cancel_available": snapshot["cancel_available"],
            "cancel_pending": snapshot["cancel_pending"],
            "cancel_request_id": 700,
            "cancel_target_id": 101 if target["type"] else None,
            "cancel_target_type": target["type"],
            "cancel_target_name": target["name"],
            "cancel_target_mode": target["mode"],
            "cancel_blocker_id": 102 if blocker["type"] else None,
            "cancel_blocker_type": blocker["type"],
            "cancel_blocker_name": blocker["name"],
            "revision": 999,
        }

    def emit(self, snapshot):
        self.status = self._raw(snapshot)
        self._changed()


class OperationContextRecorderTest(unittest.TestCase):
    def _record(self, fixture, variants):
        manager = ContextManagerFixture()
        recorder = CONTEXT_FIXTURES.OperationContextRecorder(manager)
        recorder.attach()
        recorder.start_scenario("fixture", (fixture,))
        expected = CONTEXT_FIXTURES.expand_events(
            CONTEXT_FIXTURES.FIXTURES[fixture], variants)
        for snapshot in expected:
            manager.emit(snapshot)
        result = recorder.finish_scenario()
        recorder.detach()
        return manager, recorder, result

    def test_records_nested_stack_and_ignores_dynamic_fields(self):
        manager, recorder, result = self._record(
            "mesh_clean", ("NONE", "HEATING", "COOLING"))

        self.assertTrue(result["passed"])
        self.assertEqual(result["temperature_variants"],
                         ["NONE", "HEATING", "COOLING"])
        nested = next(snapshot for snapshot in result["actual"]
                      if len(snapshot["contexts"]) == 3)
        self.assertEqual([frame["type"] for frame in nested["contexts"]],
                         ["auto_bed_level", "bed_level", "nozzle_clean"])
        self.assertEqual(manager.original_calls, len(result["actual"]))
        self.assertNotIn("_changed", manager.__dict__)
        self.assertTrue(recorder.report()["passed"])

    def test_all_temperature_boundary_variants_are_exact(self):
        self.assertEqual(
            CONTEXT_FIXTURES.temperature_variant(199.9, 200, 205),
            "HEATING")
        self.assertEqual(
            CONTEXT_FIXTURES.temperature_variant(205.1, 200, 205),
            "COOLING")
        self.assertEqual(
            CONTEXT_FIXTURES.temperature_variant(200, 200, 205), "NONE")
        self.assertEqual(
            CONTEXT_FIXTURES.temperature_variant(205, 200, 205), "NONE")
        for variant in CONTEXT_FIXTURES.WAIT_VARIANTS:
            with self.subTest(variant=variant):
                _manager, _recorder, result = self._record(
                    "screws", (variant,))
                self.assertEqual(result["temperature_variants"], [variant])

    def test_optional_homing_matches_both_real_helper_outcomes(self):
        candidates = dict((variant, expected)
                          for variant, expected, _choices
                          in CONTEXT_FIXTURES.exact_variants("screws"))
        self.assertIn("HOMING,NONE", candidates)
        self.assertIn("SKIP_HOMING,NONE", candidates)
        homed = candidates["HOMING,NONE"]
        skipped = candidates["SKIP_HOMING,NONE"]
        self.assertTrue(any(
            snapshot["current_state"] == "HOMING" for snapshot in homed))
        self.assertFalse(any(
            snapshot["current_state"] == "HOMING" for snapshot in skipped))
        self.assertEqual(
            [snapshot["current_state"] for snapshot in skipped],
            [None, "HEATING", "PROBING", None])

    def test_mismatch_reports_first_exact_difference_and_final_stack(self):
        manager = ContextManagerFixture()
        recorder = CONTEXT_FIXTURES.OperationContextRecorder(manager)
        recorder.attach()
        recorder.start_scenario("screws", ("screws",))
        events = CONTEXT_FIXTURES.expand_events(
            CONTEXT_FIXTURES.SCREWS, ("NONE",))
        for snapshot in events[:-1]:
            manager.emit(snapshot)

        with self.assertRaisesRegex(
                CONTEXT_FIXTURES.FixtureMismatch,
                "final operation stack is not empty"):
            recorder.finish_scenario()
        self.assertFalse(recorder.report()["passed"])
        recorder.detach()

    def test_wrapper_restores_external_instance_state_on_exception_and_abort(self):
        manager = ContextManagerFixture()
        calls = []

        def external_changed():
            calls.append("external")
            raise RuntimeError("original failed")

        manager._changed = external_changed
        recorder = CONTEXT_FIXTURES.OperationContextRecorder(manager)
        recorder.attach()
        recorder.start_scenario("none", ("none",))
        with self.assertRaisesRegex(RuntimeError, "original failed"):
            manager._changed()
        self.assertEqual(len(recorder._trace), 1)
        recorder.abort_active("aborted")
        recorder.detach()

        self.assertIs(manager._changed, external_changed)
        self.assertEqual(calls, ["external"])
        self.assertEqual(recorder.results[0]["diagnostic"], "aborted")

    def test_transition_between_scenarios_is_a_fail_closed_leak(self):
        manager = ContextManagerFixture()
        recorder = CONTEXT_FIXTURES.OperationContextRecorder(manager)
        recorder.attach()
        manager.emit(CONTEXT_FIXTURES.expand_events(
            CONTEXT_FIXTURES.SCREWS, ("NONE",))[-1])

        with self.assertRaisesRegex(
                CONTEXT_FIXTURES.FixtureMismatch,
                "transitions occurred between scenarios"):
            recorder.start_scenario("next", ("none",))

        self.assertFalse(recorder.results[0]["passed"])
        recorder.detach()

    def test_feature_deactivation_restores_changed_wrapper(self):
        manager = ContextManagerFixture()
        feature = UI_TEST.UITestRun(type("Host", (), {})())
        feature.context_recorder = CONTEXT_FIXTURES.OperationContextRecorder(
            manager)
        feature.context_recorder.attach()
        feature.context_recorder.start_scenario("none", ("none",))

        feature.deactivate()

        self.assertFalse(feature.context_recorder.attached)
        self.assertNotIn("_changed", manager.__dict__)
        self.assertEqual(
            feature.context_recorder.results[0]["diagnostic"],
            "feature deactivated")


if __name__ == "__main__":
    unittest.main()
