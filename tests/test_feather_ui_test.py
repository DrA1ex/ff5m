## Host-side contracts for the lazy on-printer Feather UI runner.

import json
import pathlib
import tempfile
import threading
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]
PLUGINS = ROOT / ".py" / "klipper" / "plugins"

import sys
sys.path.insert(0, str(PLUGINS))

import feather_feature_ui_test as UI_TEST  # noqa: E402
import feather_screen as FEATHER  # noqa: E402
from feather_feature_manager import LazyFeatureManager  # noqa: E402


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
        worker = object.__new__(UI_TEST.ArtifactWorker)
        frames = [b"first", b"first", b"second", b"second",
                  b"second", b"second"]
        worker._read_frame = mock.Mock(side_effect=frames)

        with mock.patch.object(UI_TEST, "FRAME_SETTLE_INTERVAL", 0.15), \
                mock.patch.object(UI_TEST, "FRAME_SETTLE_TIMEOUT", 2.0), \
                mock.patch.object(UI_TEST.time, "monotonic", side_effect=(
                    0.0, 0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30)), \
                mock.patch.object(UI_TEST.time, "sleep") as sleep:
            data, digest = worker._stable_frame()

        self.assertEqual(data, b"second")
        self.assertEqual(digest, UI_TEST.hashlib.sha256(b"second").hexdigest())
        self.assertEqual(worker._read_frame.call_count, 6)
        self.assertEqual(sleep.call_count, 5)

    def test_writes_bmp_manifest_telemetry_and_log_slice(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            run = root / "20260729-120000-ui"
            run.mkdir()
            framebuffer = root / "fb0"
            pixels = bytearray(UI_TEST.FRAME_BYTES)
            pixels[0:4] = b"\x03\x06\x07\xff"
            framebuffer.write_bytes(pixels)
            printer_log = root / "printer.log"
            printer_log.write_text("before\n", encoding="utf-8")
            active = root / "active.json"
            active.write_text("{}\n", encoding="utf-8")

            worker = UI_TEST.ArtifactWorker(
                AsyncReactor(), str(run), str(framebuffer), str(printer_log))
            printer_log.write_text("before\nafter\n", encoding="utf-8")
            captured = []
            captured_event = threading.Event()
            worker.capture(
                1, "Main menu", {"page": "MAIN_MENU"},
                lambda value: (captured.append(value), captured_event.set()))
            self.assertTrue(captured_event.wait(3.0))
            self.assertFalse(isinstance(captured[0], Exception))

            worker.telemetry(
                "positions", ("time", "x", "y", "z"),
                {"time": 1, "x": 2, "y": 3, "z": 4})
            worker.log("finished phase")
            finished = []
            finished_event = threading.Event()
            with mock.patch.object(UI_TEST, "ACTIVE_MARKER", str(active)):
                worker.finish(
                    {"outcome": "passed"},
                    lambda value: (finished.append(value),
                                   finished_event.set()))
                self.assertTrue(finished_event.wait(3.0))
            worker.stop()

            bmp = run / captured[0]["file"]
            self.assertEqual(bmp.read_bytes()[:2], b"BM")
            self.assertEqual(bmp.stat().st_size, UI_TEST.FRAME_BYTES + 54)
            manifest = json.loads((run / "manifest.json").read_text())
            self.assertEqual(manifest[0]["page"], "MAIN_MENU")
            self.assertTrue(manifest[0]["passed"])
            self.assertIn("time,x,y,z", (run / "positions.csv").read_text())
            self.assertIn("finished phase", (run / "run.log").read_text())
            self.assertEqual((run / "printer.log").read_text(), "after\n")
            self.assertFalse(active.exists())

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
            worker = UI_TEST.ArtifactWorker(
                AsyncReactor(), str(current), "/missing-fb", "/missing-log")

            with mock.patch.object(UI_TEST, "MAX_RUNS", 3), \
                    mock.patch.object(UI_TEST, "MAX_BYTES", 1024 * 1024):
                worker._retain()
            worker.stop()

            remaining = {path.name for path in root.iterdir() if path.is_dir()}
            self.assertEqual(len(remaining), 3)
            self.assertIn(names[-1], remaining)


class RunnerContractTest(unittest.TestCase):
    def test_tap_waits_for_active_gcode_before_advancing(self):
        callbacks = []
        reactor = type("Reactor", (), {
            "register_callback": lambda self, callback, when=None:
                callbacks.append((callback, when)),
        })()
        host = type("Host", (), {
            "reactor": reactor,
            "page": FEATHER.Page.CONTROL_MOVE,
            "command_depth": 1,
            "busy_message": "HOMING...",
        })()
        feature = UI_TEST.UITestFeature(host)
        feature.running = True
        feature.step_index = 4
        events = []
        schedules = []
        feature._event = events.append
        feature._schedule = schedules.append
        step = {"kind": "tap", "label": "move.homeall"}

        feature._after_tap(10.0, step, FEATHER.Page.CONTROL_MOVE)

        self.assertEqual(feature.step_index, 4)
        self.assertEqual(step["operation_deadline"],
                         10.0 + UI_TEST.TAP_OPERATION_TIMEOUT)
        self.assertTrue(step["expected_page_seen"])
        self.assertEqual(callbacks[0][1], 10.1)
        host.command_depth = 0
        host.busy_message = None
        # A long operation may legitimately advance from its progress page to
        # a result page before synthetic tap completion is acknowledged.
        host.page = FEATHER.Page.CALIBRATION_RESULT
        callbacks[0][0](10.1)
        self.assertEqual(feature.step_index, 5)
        self.assertEqual(events, ["PASS move.homeall"])
        self.assertEqual(schedules, [0.02])

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
            "_dispatch_action": dispatch,
        })()
        feature = UI_TEST.UITestFeature(host)
        feature.motion_origin = (110.0, 110.099675, 220.0)

        feature._motion_step("y", -1)

        self.assertEqual(
            dispatched, [UI_TEST.move_actions.Y_PLUS.wire_id])
        self.assertEqual(feature.motion_expected, 110.0)

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
        feature = UI_TEST.UITestFeature(type("Host", (), {
            "renderer": renderer,
        })())
        steps = feature._build_steps("RENDER")
        labels = [step["label"] for step in steps]

        self.assertEqual(labels, [
            "baseline", "render-pause-timer", "render-restart-signal",
            "render-recovered", "render-recovered", "render-resume-timer"])
        steps[2]["callback"]()
        self.assertTrue(steps[3]["predicate"]())

    def test_capture_waits_for_worker_and_fails_on_dropped_batch(self):
        status = {
            "submitted_batches": 5, "rendered_batches": 4,
            "coalesced_batches": 0, "dropped_batches": 0,
        }
        renderer = type("Renderer", (), {
            "get_status": lambda self: dict(status),
        })()
        reactor = type("Reactor", (), {"monotonic": lambda self: 10.0})()
        feature = UI_TEST.UITestFeature(type("Host", (), {
            "renderer": renderer, "reactor": reactor,
        })())
        scheduled = []
        feature._schedule = scheduled.append
        feature._screen_metadata = lambda: {"page": "IDLE_HOME"}
        captures = []
        feature.worker = type("Worker", (), {
            "capture": lambda self, *args: captures.append(args),
        })()
        step = {"kind": "capture", "label": "settled"}

        feature._capture(step)
        self.assertEqual(scheduled, [0.02])
        self.assertEqual(captures, [])

        status["dropped_batches"] = 1
        with self.assertRaisesRegex(RuntimeError, "batch dropped"):
            feature._capture(step)

    def test_full_suite_order_and_z_safety_sequence(self):
        renderer = type("Renderer", (), {
            "get_status": lambda self: {"typer_restarts": 0},
        })()
        feature = UI_TEST.UITestFeature(type("Host", (), {
            "renderer": renderer,
        })())
        feature.material = "PLA"
        steps = feature._build_steps("FULL")
        labels = [step["label"] for step in steps]

        first = dict((phase, next(index for index, label in enumerate(labels)
                                  if label.startswith(phase + "-")))
                     for phase in ("ui", "render", "motion", "heat", "screws",
                                   "mesh", "z"))
        self.assertEqual(list(first), [
            "ui", "render", "motion", "heat", "screws", "mesh", "z"])
        self.assertEqual(list(first.values()), sorted(first.values()))
        actions = [step.get("action") for step in steps]
        self.assertIn(UI_TEST.move_actions.HOME_ALL.wire_id, actions)
        self.assertNotIn("move.homeall", actions)
        self.assertEqual(
            actions.count(UI_TEST.z_actions.FARTHER.wire_id), 10)
        self.assertEqual(
            actions.count(UI_TEST.z_actions.CLOSER.wire_id), 10)
        self.assertIn(UI_TEST.z_actions.DISCARD_CONFIRM.wire_id, actions)
        self.assertNotIn(UI_TEST.z_actions.SAVE.wire_id, actions)
        self.assertFalse(any("pid" in label.lower() for label in labels))

    def test_ui_file_browser_returns_home_before_reopening_menu(self):
        feature = UI_TEST.UITestFeature(object())
        steps = feature._build_steps("UI")
        labels = [step["label"] for step in steps]
        returned = labels.index("ui-file-return")

        self.assertEqual(
            (steps[returned + 1]["action"], steps[returned + 1]["page"]),
            ("nav.back", FEATHER.Page.IDLE_HOME))
        self.assertEqual(
            (steps[returned + 2]["action"], steps[returned + 2]["page"]),
            ("nav.menu", FEATHER.Page.MAIN_MENU))

    def test_ui_filament_back_preserves_target_before_leaving_materials(self):
        feature = UI_TEST.UITestFeature(object())
        steps = feature._build_steps("UI")
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
            ("nav.back", FEATHER.Page.FILAMENT_MATERIAL))
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
        host.page = FEATHER.Page.FILAMENT_MATERIAL
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
        feature = UI_TEST.UITestFeature(host)
        feature.material = "PETG"

        feature._render_safe_filament_action()
        feature._render_safe_filament_cooling()

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
            "page": FEATHER.Page.IDLE_HOME,
            "_show_page": lambda self, page: setattr(self, "page", page),
        })()
        feature = UI_TEST.UITestFeature(host)

        feature._open_calibration_home()

        self.assertEqual(calibration.calibration_page, 0)
        self.assertEqual(host.page, FEATHER.Page.CALIBRATION_HOME)

    def test_test_mode_blocks_only_persistent_actions(self):
        feature = UI_TEST.UITestFeature(object())
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

    def test_controller_drops_persistent_action_while_runner_is_active(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.print_state = FEATHER.PrintState.IDLE
        controller.command_depth = 0
        controller.busy_message = None
        controller.feature_manager = LazyFeatureManager(
            controller, FEATHER.FEATURE_SPECS)
        feature = controller.feature_manager.get("ui_test")
        feature.running = True

        # Returning before page routing is the contract: no save handler or
        # additional controller fixture is needed for this safety check.
        controller._dispatch_action("cal.mesh.save")

    def test_nonphysical_cleanup_does_not_issue_hardware_gcode(self):
        shown = []
        host = type("Host", (), {
            "timer": None,
            "filament_material": "PETG",
            "previous_page": FEATHER.Page.MAIN_MENU,
            "_run_script": lambda self, command: (_ for _ in ()).throw(
                AssertionError("UI suite issued hardware G-code: %s" % command)),
            "_show_page": lambda self, page: shown.append(page),
        })()
        for suite in ("UI", "RENDER"):
            feature = UI_TEST.UITestFeature(host)
            feature.suite = suite
            feature.original = {
                "filament_material": "PLA", "timer_active": False,
                "page": FEATHER.Page.IDLE_HOME,
                "previous_page": FEATHER.Page.CONTROL_HOME,
            }
            feature._restore_state()

        self.assertEqual(host.filament_material, "PLA")
        self.assertEqual(shown, [FEATHER.Page.IDLE_HOME] * 2)
        self.assertEqual(host.previous_page, FEATHER.Page.CONTROL_HOME)

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
        gcmd = GCmd({"ACTION": "RUN", "SUITE": "UI", "CONFIRM": 1})
        calls = []

        with mock.patch.object(
                UI_TEST.UITestFeature, "run",
                lambda feature, *args: calls.append((feature, args))):
            controller.cmd_FEATHER_UI_TEST(gcmd)

        feature = controller.feature_manager.peek("ui_test")
        self.assertIsNotNone(feature)
        self.assertEqual(calls, [(feature, (gcmd, "UI", "", 1))])

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
            feature = UI_TEST.UITestFeature(host)
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
            feature = UI_TEST.UITestFeature(host)
            feature.session_id = "new"

            with mock.patch.object(UI_TEST, "ACTIVE_MARKER", str(active)):
                feature._recover_stale_marker()

            self.assertEqual(commands, [])
            self.assertFalse(active.exists())


if __name__ == "__main__":
    unittest.main()
