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
    def test_full_suite_order_and_z_safety_sequence(self):
        feature = UI_TEST.UITestFeature(object())
        feature.material = "PLA"
        steps = feature._build_steps("FULL")
        labels = [step["label"] for step in steps]

        first = dict((phase, next(index for index, label in enumerate(labels)
                                  if label.startswith(phase + "-")))
                     for phase in ("ui", "motion", "heat", "screws",
                                   "mesh", "z"))
        self.assertEqual(list(first), [
            "ui", "motion", "heat", "screws", "mesh", "z"])
        self.assertEqual(list(first.values()), sorted(first.values()))
        actions = [step.get("action") for step in steps]
        self.assertEqual(actions.count("z.farther"), 10)
        self.assertEqual(actions.count("z.closer"), 10)
        self.assertIn("z.discard.confirm", actions)
        self.assertNotIn("z.save", actions)
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

    def test_test_mode_blocks_only_persistent_actions(self):
        feature = UI_TEST.UITestFeature(object())
        self.assertFalse(feature.blocks_action("z.save"))
        feature.running = True
        self.assertTrue(feature.input_blocked)
        self.assertTrue(feature.blocks_action("z.save"))
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

    def test_ui_only_cleanup_does_not_issue_hardware_gcode(self):
        shown = []
        host = type("Host", (), {
            "timer": None,
            "filament_material": "PETG",
            "previous_page": FEATHER.Page.MAIN_MENU,
            "_run_script": lambda self, command: (_ for _ in ()).throw(
                AssertionError("UI suite issued hardware G-code: %s" % command)),
            "_show_page": lambda self, page: shown.append(page),
        })()
        feature = UI_TEST.UITestFeature(host)
        feature.suite = "UI"
        feature.original = {
            "filament_material": "PLA", "timer_active": False,
            "page": FEATHER.Page.IDLE_HOME,
            "previous_page": FEATHER.Page.CONTROL_HOME,
        }

        feature._restore_state()

        self.assertEqual(host.filament_material, "PLA")
        self.assertEqual(shown, [FEATHER.Page.IDLE_HOME])
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
