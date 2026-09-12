## Host-side behavioral contracts for unattended printer regression runs.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Host-side behavioral contracts for unattended printer regression runs."""

import json
import io
import pathlib
import shutil
import subprocess
import tempfile
import threading
import types
import unittest
from unittest import mock

from tests import printer_regression as REGRESSION
from tests.printer_connection import PrinterConnection, PrinterConnectionError


def arguments(output, suite="all", extra=()):
    suites = [suite] if isinstance(suite, str) else list(suite)
    return REGRESSION._arguments([
        "--printer", "printer.invalid",
        "--suite",
    ] + suites + [
        "--output", str(output),
        "--confirm-unattended-physical-test",
    ] + list(extra))


def fake_host_preflight(output, _suite_count, _run_timeout):
    pathlib.Path(output).mkdir(parents=True)


class FakeClient:
    def __init__(self, outcomes=None, unsafe_after=None, copy_failure=None):
        self.outcomes = dict(outcomes or {})
        self.unsafe_after = unsafe_after
        self.copy_failure = copy_failure
        self.launched = []
        self.deleted = []
        self.safe_checks = 0
        self.aborted = []
        self.telemetry_calls = 0

    def preflight(self):
        pass

    def discover_camera(self):
        return None

    def launch(self, spec, material, screen_capture_interval,
               start_timeout=20):
        del start_timeout
        del material
        self.screen_capture_interval = screen_capture_interval
        self.launched.append(spec["name"])
        suffix = spec["printer_suite"].lower()
        run_id = "20260811-120000-%06d-%s" % (len(self.launched), suffix)
        return {
            "run_id": run_id,
            "suite": spec["printer_suite"],
            "directory": REGRESSION.ARTIFACT_ROOT + "/" + run_id,
        }

    def wait(self, marker, timeout, run_state=None, events_alive=None,
             progress=None):
        del marker, timeout, run_state
        del progress
        if events_alive is not None and not events_alive():
            raise REGRESSION.RegressionError(
                "no printer status event for 30 seconds")
        return False

    def telemetry_snapshot(self):
        self.telemetry_calls += 1
        return {
            "toolhead": {"homed_axes": "xyz", "position": [1, 2, 3, 0]},
            "motion_report": {
                "live_position": [1, 2, 3, 0], "live_velocity": 4.5,
            },
            "extruder": {"temperature": 210, "target": 220},
            "heater_bed": {"temperature": 55, "target": 60},
            "print_stats": {"state": "standby"},
            "virtual_sdcard": {"progress": 0.0},
            "feather_screen": {
                "page": "IDLE_HOME", "context_path": [],
                "context_types": [], "current_state": None,
                "ui_test": {"running": True, "phase": "synthetic"},
            },
        }

    def copy_and_verify(self, marker, output_parent):
        name = self.launched[-1]
        if self.copy_failure == name:
            raise REGRESSION.RegressionError("synthetic copy failure")
        run_id = marker["run_id"]
        local = pathlib.Path(output_parent) / run_id
        local.mkdir(parents=True)
        outcome = self.outcomes.get(name, "passed")
        summary = {
            "run_id": run_id,
            "outcome": outcome,
            "reason": "synthetic assertion" if outcome == "failed" else None,
            "duration": 2.0,
            "started_at": 5000.0,
            "finished_at": 5002.0,
            "screenshots": 1,
            "failures": [],
        }
        manifest = [{
            "file": "001-screen.bmp", "time": 5001.0,
        }]
        (local / "001-screen.bmp").write_bytes(b"BMsynthetic")
        (local / "summary.json").write_text(
            json.dumps(summary), encoding="utf-8")
        (local / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8")
        return local, summary, manifest

    def copy_partial(self, marker, output_parent):
        target = pathlib.Path(output_parent) / marker["run_id"]
        target.mkdir(parents=True, exist_ok=True)
        return target

    def delete_remote(self, marker):
        self.deleted.append(marker["run_id"])

    def require_safe_idle(self):
        self.safe_checks += 1
        if self.safe_checks == self.unsafe_after:
            raise REGRESSION.RegressionError("synthetic unsafe state")

    def abort(self, marker):
        self.aborted.append(marker["run_id"])


class FakeMedia:
    def __init__(self, status="passed"):
        self.status = status
        self.camera = {"status": "disabled", "metadata": None}
        self.warnings = []
        self.started = False
        self.stopped = False
        self.duration = None

    def start_camera(self, camera):
        self.started = True
        self.camera = {
            "status": "unavailable" if camera is None else "recording",
            "metadata": None if camera is None else camera["metadata"],
        }
        if camera is None:
            self.warnings.append("No enabled printer camera was available.")

    def stop_camera(self):
        self.stopped = True

    def finalize(self, suites, duration):
        del suites
        self.duration = duration
        return {
            "status": self.status,
            "recording": "recording.mp4" if self.status == "passed" else None,
        }


class FakeTelemetry:
    def __init__(self, rate_hz):
        self.rate_hz = float(rate_hz)
        self.sample_count = 0
        self.latest_test_status = None

    def start(self, _origin, test_status=None):
        self.test_status = test_status

    def finish(self):
        enabled = bool(self.rate_hz)
        return {
            "status": "recorded" if enabled else "disabled",
            "rate_hz": self.rate_hz,
            "effective_rate_hz": 0.0,
            "sample_count": 1 if enabled else 0,
            "failure_count": 0,
            "file": REGRESSION.TELEMETRY_FILE if enabled else None,
        }

    def events_alive(self):
        return True

    def expect_run(self, run_id):
        self.run_id = run_id

    def run_state(self, run_id):
        return "active" if run_id == self.run_id else "changed"


class FakePopen:
    class Sink:
        def __init__(self):
            self.bytes_written = 0

        def write(self, value):
            self.bytes_written += len(value)

        def close(self):
            pass

    class Process:
        def __init__(self, command):
            self.command = command
            self.stdin = FakePopen.Sink()
            self.returncode = None

        def wait(self, timeout=None):
            del timeout
            pathlib.Path(self.command[-1]).write_bytes(b"synthetic video")
            self.returncode = 0
            return 0

        def kill(self):
            self.returncode = -9

    def __init__(self):
        self.processes = []

    def __call__(self, command, **_kwargs):
        process = self.Process(command)
        self.processes.append(process)
        return process


class SuiteSelectionTest(unittest.TestCase):
    def test_host_aggregates_preserve_printer_suite_meanings(self):
        self.assertEqual(
            [item["printer_suite"] for item in
             REGRESSION.selected_suites("all")],
            ["FULL", "CONTEXT_PRINT"])
        self.assertEqual(
            REGRESSION.selected_suites("core")[0]["printer_suite"], "FULL")
        self.assertEqual(
            REGRESSION.selected_suites("print")[0]["confirm"], 2)
        self.assertEqual(
            REGRESSION.selected_suites("material")[0]["confirm"], 2)

    def test_model_printing_suite_is_ordered_by_physical_constraints(self):
        # "print" leaves a real model in the bed centre, so it must follow the
        # bed-probing suite and nothing that re-homes or purges over the bed
        # centre may be chained after it without an operator clearing the bed.
        names = [item["name"] for item in REGRESSION.selected_suites("all")]
        self.assertLess(names.index("core"), names.index("print"))
        self.assertNotIn("material", names)

    def test_individual_suites_do_not_expand(self):
        for name in (
                "ui", "component", "render", "motion", "heat", "screws",
                "mesh", "z"):
            self.assertEqual(len(REGRESSION.selected_suites(name)), 1)

    def test_multiple_suites_preserve_order_and_deduplicate_expansions(self):
        selected = REGRESSION.selected_suites(
            ["ui", "all", "core", "component", "ui"])

        self.assertEqual(
            [item["name"] for item in selected],
            ["ui", "core", "print", "component"])

    def test_cli_accepts_grouped_and_repeated_suite_options_in_order(self):
        args = REGRESSION._arguments([
            "--printer", "printer.invalid",
            "--suite", "render", "ui",
            "--suite", "component", "core",
        ])

        self.assertEqual(args.suite, ["render", "ui", "component", "core"])

    def test_cli_default_remains_the_single_core_suite(self):
        args = REGRESSION._arguments([
            "--printer", "printer.invalid",
        ])

        self.assertEqual(args.suite, ["core"])

    def test_unsafe_physical_compositions_are_rejected(self):
        with self.assertRaisesRegex(
                REGRESSION.RegressionError, "final physical"):
            REGRESSION.selected_suites(["print", "mesh"])
        with self.assertRaisesRegex(
                REGRESSION.RegressionError, "material"):
            REGRESSION.selected_suites(["core", "material"])

        self.assertEqual(
            [item["name"] for item in
             REGRESSION.selected_suites(["print", "ui", "component"])],
            ["print", "ui", "component"])

    def test_invalid_fps_is_rejected_by_cli(self):
        with mock.patch("sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit):
                REGRESSION._arguments([
                    "--printer", "printer.invalid", "--fps", "31"])
            with self.assertRaises(SystemExit):
                REGRESSION._arguments([
                    "--printer", "printer.invalid", "--fps", "0"])

    def test_screen_capture_interval_defaults_to_five_and_zero_disables_it(self):
        self.assertEqual(
            REGRESSION._arguments([
                "--printer", "printer.invalid",
            ]).screen_capture_interval,
            5.0)
        self.assertEqual(
            REGRESSION._arguments([
                "--printer", "printer.invalid",
                "--screen-capture-interval", "0",
            ]).screen_capture_interval,
            0.0)
        with mock.patch("sys.stderr", new=io.StringIO()):
            for value in ("4.9", "301", "nan"):
                with self.subTest(value=value), self.assertRaises(SystemExit):
                    REGRESSION._arguments([
                        "--printer", "printer.invalid",
                        "--screen-capture-interval", value,
                    ])

    def test_telemetry_rate_defaults_to_one_and_zero_disables_it(self):
        self.assertEqual(
            REGRESSION._arguments([
                "--printer", "printer.invalid",
            ]).telemetry_rate,
            1.0)
        self.assertEqual(
            REGRESSION._arguments([
                "--printer", "printer.invalid",
                "--telemetry-rate", "0",
            ]).telemetry_rate,
            0.0)
        with mock.patch("sys.stderr", new=io.StringIO()):
            for value in ("0.5", "10.1", "nan"):
                with self.subTest(value=value), self.assertRaises(SystemExit):
                    REGRESSION._arguments([
                        "--printer", "printer.invalid",
                        "--telemetry-rate", value,
                    ])

    def test_physical_suites_reject_reactor_polling_above_one_hz(self):
        with mock.patch("sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit):
                REGRESSION._arguments([
                    "--printer", "printer.invalid", "--suite", "all",
                    "--telemetry-rate", "5",
                ])


class CameraContractTest(unittest.TestCase):
    def test_relative_camera_url_uses_printer_port_80(self):
        connection = PrinterConnection("192.0.2.4")
        self.assertEqual(
            REGRESSION.resolve_camera_url(
                connection, "/webcam/?action=stream"),
            "http://192.0.2.4/webcam/?action=stream")

    def test_report_camera_metadata_omits_urls_and_extra_data(self):
        metadata = REGRESSION.camera_metadata({
            "name": "Printer", "enabled": True, "target_fps": 15,
            "stream_url": "http://user:secret@example.invalid/stream?token=x",
            "snapshot_url": "http://example.invalid/snapshot?token=x",
            "extra_data": {"token": "secret"},
        })
        serialized = json.dumps(metadata)
        self.assertNotIn("secret", serialized)
        self.assertNotIn("token", serialized)
        self.assertNotIn("stream_url", metadata)

    def test_camera_filter_applies_metadata_without_changing_service(self):
        value = REGRESSION.MediaPipeline._camera_filter({
            "flip_horizontal": True,
            "flip_vertical": False,
            "rotation": 90,
        }, 10)
        self.assertIn("hflip", value)
        self.assertIn("transpose=1", value)
        self.assertIn("fps=10", value)
        self.assertTrue(value.startswith("setpts=PTS-STARTPTS,"))

    def test_camera_uses_host_arrival_time_for_missing_mjpeg_timestamps(self):
        with tempfile.TemporaryDirectory() as temporary:
            process = mock.Mock()
            process.poll.return_value = None
            popen = mock.Mock(return_value=process)
            media = REGRESSION.MediaPipeline(
                temporary, 10, popen=popen, sleeper=lambda _delay: None)
            media.start_camera({
                "url": "http://printer.invalid/camera",
                "metadata": {"rotation": 0},
            })
            command = popen.call_args.args[0]

        timestamp_option = command.index("-use_wallclock_as_timestamps")
        input_option = command.index("-i")
        self.assertEqual(command[timestamp_option + 1], "1")
        self.assertLess(timestamp_option, input_option)
        self.assertEqual(media.camera["status"], "recording")


class LaunchContractTest(unittest.TestCase):
    @staticmethod
    def _launch(name, material):
        spec = REGRESSION.selected_suites(name)[0]
        run_id = "20260811-120000-000001-%s" % \
            spec["printer_suite"].lower()
        marker = {
            "run_id": run_id, "suite": spec["printer_suite"],
            "directory": REGRESSION.ARTIFACT_ROOT + "/" + run_id,
        }

        class Connection:
            def __init__(self):
                self.requests = []
                self.status_reads = 0

            def request_json(self, method, path, payload=None, timeout=None):
                del timeout
                self.requests.append((method, path, payload))
                if method == "GET":
                    self.status_reads += 1
                    return {"result": {"status": {"feather_screen": {
                        "ui_test": ({"running": False}
                                    if self.status_reads == 1 else
                                    dict(marker, running=True)),
                    }}}}
                return {"result": "ok"}

        connection = Connection()
        client = REGRESSION.PrinterRunClient(connection)
        client.launch(spec, material, 5.0)
        return next(payload["script"] for method, _path, payload
                    in connection.requests if method == "POST")

    def test_material_is_forwarded_only_to_profile_suites(self):
        self.assertIn("MATERIAL=PLA", self._launch("core", "PLA"))
        self.assertNotIn("MATERIAL=PLA", self._launch("ui", "PLA"))

    def test_periodic_capture_interval_is_forwarded(self):
        self.assertIn("CAPTURE_INTERVAL=5", self._launch("ui", None))


class PrinterPreflightTest(unittest.TestCase):
    class Connection:
        def __init__(self, objects):
            self.objects = objects

        def request_json(self, _method, path, payload=None, timeout=None):
            del payload, timeout
            if path == "/server/info":
                return {"result": {
                    "klippy_connected": True, "klippy_state": "ready",
                }}
            if path == "/printer/objects/list":
                return {"result": {"objects": list(self.objects)}}
            if path == "/printer/objects/query?feather_screen=ui_test":
                return {"result": {"status": {"feather_screen": {
                    "ui_test": {"running": False},
                }}}}
            raise AssertionError("unexpected request: %s" % path)

        def require_safe_idle(self):
            return {
                "print_state": "standby", "heaters_off": True,
                "virtual_sd_inactive": True,
            }

    def test_feather_object_proves_hidden_command_registration(self):
        client = REGRESSION.PrinterRunClient(
            self.Connection(["gcode", "feather_screen"]))
        client.preflight()

    def test_missing_feather_object_fails_preflight(self):
        client = REGRESSION.PrinterRunClient(self.Connection(["gcode"]))
        with self.assertRaisesRegex(
                REGRESSION.RegressionError, "Feather screen object"):
            client.preflight()


class OrchestrationTest(unittest.TestCase):
    def _run(self, temporary, client, media, suite="all", extra=()):
        output = pathlib.Path(temporary) / "run"
        args = arguments(output, suite=suite, extra=extra)
        run = REGRESSION.RegressionRun(
            args, client=client, media=media,
            telemetry=FakeTelemetry(args.telemetry_rate),
            progress=lambda _message: None)
        with mock.patch.object(
                REGRESSION, "_host_preflight", side_effect=fake_host_preflight):
            return run.run()

    def test_normal_suite_failure_does_not_block_later_suites(self):
        with tempfile.TemporaryDirectory() as temporary:
            client = FakeClient({"core": "failed"})
            report, output = self._run(
                temporary, client, FakeMedia(), suite="all")

            durable = json.loads(
                (output / "report.json").read_text(encoding="utf-8"))

        self.assertEqual(client.launched, list(REGRESSION.ALL_SUITES))
        self.assertEqual(len(client.deleted), len(REGRESSION.ALL_SUITES))
        self.assertEqual(
            [item["status"] for item in report["suites"]],
            ["failed"] + ["passed"] * (len(REGRESSION.ALL_SUITES) - 1))
        self.assertEqual(report["status"], "failed")
        self.assertEqual(durable["suites"][0]["reason"],
                         "synthetic assertion")
        self.assertEqual(durable["telemetry"]["status"], "recorded")
        self.assertEqual(durable["telemetry"]["sample_count"], 1)

    def test_selected_suites_run_once_in_requested_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            report, _output = self._run(
                temporary, FakeClient(), FakeMedia(),
                suite=("render", "ui", "render", "component"))

        self.assertEqual(
            [item["name"] for item in report["suites"]],
            ["render", "ui", "component"])
        self.assertEqual(
            report["requested_suites"],
            ["render", "ui", "render", "component"])

    def test_console_progress_uses_already_sampled_test_status(self):
        class ProgressClient(FakeClient):
            def wait(self, marker, timeout, run_state=None,
                     events_alive=None, progress=None):
                del marker, timeout, run_state, events_alive
                progress(65.0)
                return False

        with tempfile.TemporaryDirectory() as temporary:
            output = pathlib.Path(temporary) / "run"
            args = arguments(output, suite="core")
            telemetry = FakeTelemetry(args.telemetry_rate)
            telemetry.sample_count = 66
            telemetry.latest_test_status = {
                "phase": "mesh", "step": "leveling",
                "step_index": 2, "step_count": 8,
            }
            messages = []
            run = REGRESSION.RegressionRun(
                args, client=ProgressClient(), media=FakeMedia(),
                telemetry=telemetry, progress=messages.append)
            with mock.patch.object(
                    REGRESSION, "_host_preflight",
                    side_effect=fake_host_preflight):
                run.run()

        self.assertIn(
            "core: running 1m 05s | phase=mesh | step=3/8 | "
            "action=leveling | telemetry=66",
            messages)
        self.assertIn("core: downloading artifacts", messages)

    def test_screen_timeline_is_anchored_after_lazy_runner_launch(self):
        class Clock:
            now = 100.0

            def __call__(self):
                return self.now

        with tempfile.TemporaryDirectory() as temporary:
            clock = Clock()
            client = FakeClient()
            original_launch = client.launch

            def delayed_launch(*args, **kwargs):
                clock.now += 1.25
                return original_launch(*args, **kwargs)

            client.launch = delayed_launch
            output = pathlib.Path(temporary) / "run"
            args = arguments(output, suite="core")
            run = REGRESSION.RegressionRun(
                args, client=client, media=FakeMedia(),
                telemetry=FakeTelemetry(args.telemetry_rate), clock=clock,
                progress=lambda _message: None)
            with mock.patch.object(
                    REGRESSION, "_host_preflight",
                    side_effect=fake_host_preflight):
                report, _output = run.run()

        self.assertAlmostEqual(
            report["suites"][0]["timeline_start_seconds"], 1.25)

    def test_unsafe_state_skips_later_physical_suites(self):
        with tempfile.TemporaryDirectory() as temporary:
            client = FakeClient(unsafe_after=1)
            report, _output = self._run(
                temporary, client, FakeMedia(), suite="all")

        self.assertEqual(client.launched, [REGRESSION.ALL_SUITES[0]])
        self.assertEqual(
            [item["status"] for item in report["suites"]],
            ["passed"] + ["skipped"] * (len(REGRESSION.ALL_SUITES) - 1))
        self.assertEqual(report["status"], "error")

    def test_media_failure_does_not_rewrite_printer_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            report, _output = self._run(
                temporary, FakeClient(), FakeMedia(status="failed"),
                suite="core")

        self.assertEqual(report["suites"][0]["status"], "passed")
        self.assertEqual(report["media"]["status"], "failed")
        self.assertEqual(report["status"], "error")

    def test_camera_degradation_keeps_passed_host_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            media = FakeMedia(status="passed")
            report, _output = self._run(
                temporary, FakeClient(), media, suite="core")

        self.assertEqual(report["suites"][0]["status"], "passed")
        self.assertEqual(report["camera"]["status"], "unavailable")
        self.assertEqual(report["status"], "passed")

    def test_copy_failure_preserves_remote_artifact_and_skips_later(self):
        with tempfile.TemporaryDirectory() as temporary:
            client = FakeClient(copy_failure="core")
            report, _output = self._run(
                temporary, client, FakeMedia(), suite="all")

        self.assertEqual(client.deleted, [])
        self.assertEqual(client.launched, ["core"])
        self.assertEqual(report["suites"][0]["status"],
                         "infrastructure_error")
        self.assertEqual(report["suites"][1]["status"], "skipped")

    def test_no_camera_option_is_explicit_and_warning_free(self):
        with tempfile.TemporaryDirectory() as temporary:
            media = FakeMedia()
            report, _output = self._run(
                temporary, FakeClient(), media, suite="core",
                extra=("--no-camera",))

        self.assertFalse(media.started)
        self.assertEqual(report["camera"]["status"], "disabled")
        self.assertEqual(report["warnings"], [])

    def test_no_resource_monitor_option_declares_the_missing_sampler(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = pathlib.Path(temporary) / "run"
            args = arguments(output, suite="core",
                             extra=("--no-resource-monitor",))

            class Sampler:
                @staticmethod
                def start():
                    raise AssertionError("resource monitor was started")

            run = REGRESSION.RegressionRun(
                args, client=FakeClient(), media=FakeMedia(),
                telemetry=FakeTelemetry(args.telemetry_rate),
                resource_monitor=Sampler(), progress=lambda _message: None)
            with mock.patch.object(
                    REGRESSION, "_host_preflight",
                    side_effect=fake_host_preflight):
                report, _output = run.run()

        # The /proc sampler is the only observer that runs for the whole run,
        # so leaving it out has to read as a deliberate exclusion instead of a
        # sampler that ran and collected nothing.
        self.assertEqual(report["resources"]["status"], "disabled")
        self.assertEqual(
            [item for item in report["warnings"] if "resource" in item], [])

    def test_zero_telemetry_rate_does_not_query_printer(self):
        with tempfile.TemporaryDirectory() as temporary:
            client = FakeClient()
            report, _output = self._run(
                temporary, client, FakeMedia(), suite="core",
                extra=("--telemetry-rate", "0"))

        self.assertEqual(client.telemetry_calls, 0)
        self.assertEqual(report["telemetry"]["status"], "disabled")

    def test_physical_confirmation_is_required_before_client_preflight(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = pathlib.Path(temporary) / "run"
            args = REGRESSION._arguments([
                "--printer", "printer.invalid", "--suite", "core",
                "--output", str(output),
            ])
            client = mock.Mock()
            run = REGRESSION.RegressionRun(
                args, client=client, media=FakeMedia(),
                progress=lambda _message: None)
            with mock.patch.object(
                    REGRESSION, "_host_preflight",
                    side_effect=fake_host_preflight):
                report, _output = run.run()

        client.preflight.assert_not_called()
        self.assertEqual(
            report["infrastructure_error"]["category"],
            "ConfirmationRequired")


class ArtifactOwnershipTest(unittest.TestCase):
    def test_live_status_is_a_fail_closed_launch_guard_without_ssh(self):
        connection = mock.Mock()
        connection.request_json.return_value = {
            "result": {"status": {"feather_screen": {
                "ui_test": {"running": True},
            }}},
        }
        client = REGRESSION.PrinterRunClient(connection)

        with self.assertRaisesRegex(
                REGRESSION.RegressionError, "another Feather UI test"):
            client.launch(REGRESSION.selected_suites("ui")[0])
        connection.ssh.assert_not_called()

    def test_copy_verifies_exact_run_before_cleanup_is_available(self):
        marker = {
            "run_id": "20260811-120000-000001-ui",
            "suite": "UI",
            "directory": "/data/feather-ui-tests/"
                         "20260811-120000-000001-ui",
        }

        with tempfile.TemporaryDirectory() as temporary:
            output = pathlib.Path(temporary) / "output"

            def command_runner(command, **_kwargs):
                target = pathlib.Path(command[-1]) / marker["run_id"]
                target.mkdir(parents=True)
                (target / "summary.json").write_text(json.dumps({
                    "run_id": marker["run_id"], "outcome": "passed",
                    "screenshots": 0, "started_at": 1000.0,
                    "duration": 1.0,
                }), encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, "", "")

            connection = types.SimpleNamespace(
                command_runner=command_runner,
                scp_target="root@printer.invalid",
            )
            client = REGRESSION.PrinterRunClient(connection)
            local, summary, manifest = client.copy_and_verify(marker, output)

        self.assertEqual(local.name, marker["run_id"])
        self.assertEqual(summary["run_id"], marker["run_id"])
        self.assertEqual(manifest, [])

    def test_wrong_summary_ownership_fails_verification(self):
        marker = {
            "run_id": "20260811-120000-000001-ui",
            "suite": "UI",
            "directory": "/data/feather-ui-tests/"
                         "20260811-120000-000001-ui",
        }
        with tempfile.TemporaryDirectory() as temporary:
            def command_runner(command, **_kwargs):
                target = pathlib.Path(command[-1]) / marker["run_id"]
                target.mkdir(parents=True)
                (target / "summary.json").write_text(json.dumps({
                    "run_id": "unrelated", "outcome": "passed",
                }), encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, "", "")

            connection = types.SimpleNamespace(
                command_runner=command_runner,
                scp_target="root@printer.invalid",
            )
            client = REGRESSION.PrinterRunClient(connection)
            with self.assertRaisesRegex(
                    REGRESSION.RegressionError, "wrong ownership"):
                client.copy_and_verify(marker, pathlib.Path(temporary) / "out")

    def test_missing_manifest_screenshot_preserves_verification_failure(self):
        marker = {
            "run_id": "20260811-120000-000001-ui",
            "suite": "UI",
            "directory": "/data/feather-ui-tests/"
                         "20260811-120000-000001-ui",
        }
        with tempfile.TemporaryDirectory() as temporary:
            def command_runner(command, **_kwargs):
                target = pathlib.Path(command[-1]) / marker["run_id"]
                target.mkdir(parents=True)
                (target / "summary.json").write_text(json.dumps({
                    "run_id": marker["run_id"], "outcome": "passed",
                    "screenshots": 1, "started_at": 1000.0,
                    "duration": 1.0,
                }), encoding="utf-8")
                (target / "manifest.json").write_text(json.dumps([{
                    "file": "001-missing.bmp", "time": 1000.0,
                }]), encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, "", "")

            connection = types.SimpleNamespace(
                command_runner=command_runner,
                scp_target="root@printer.invalid",
            )
            client = REGRESSION.PrinterRunClient(connection)
            with self.assertRaisesRegex(
                    REGRESSION.RegressionError, "screenshot is missing"):
                client.copy_and_verify(marker, pathlib.Path(temporary) / "out")

    def test_remote_cleanup_rejects_broad_or_unowned_path(self):
        marker = {
            "run_id": "20260811-120000-000001-ui",
            "suite": "UI",
            "directory": REGRESSION.ARTIFACT_ROOT,
        }
        connection = mock.Mock()
        client = REGRESSION.PrinterRunClient(connection)

        with self.assertRaisesRegex(
                REGRESSION.RegressionError, "unsafe run path"):
            client.delete_remote(marker)
        connection.ssh.assert_not_called()


class ResourceMonitorTest(unittest.TestCase):
    def test_monitor_uses_one_bounded_remote_process_and_exact_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = pathlib.Path(temporary)
            commands = []

            class Connection:
                ssh_target = "root@printer.invalid"
                scp_target = "root@printer.invalid"

                def ssh(self, command, timeout=None):
                    commands.append((command, timeout))
                    return ""

                @staticmethod
                def command_runner(command, **_kwargs):
                    pathlib.Path(command[-1]).write_text(
                        "epoch\tuptime\tload1\tmem_available_kb\n"
                        "1770000000\t12345.67\t1.42\t45120\n",
                        encoding="utf-8")
                    return subprocess.CompletedProcess(command, 0, "", "")

            class Process:
                returncode = None
                terminated = False

                def poll(self):
                    return self.returncode

                def terminate(self):
                    self.terminated = True
                    self.returncode = -15

                def wait(self, timeout=None):
                    del timeout
                    return self.returncode

            process = Process()
            popen_commands = []

            def popen(command, **_kwargs):
                popen_commands.append(command)
                return process

            monitor = REGRESSION.ResourceMonitor(
                Connection(), output, 123.2, clock=lambda: 1000.0,
                popen=popen, sleeper=lambda _delay: None)
            monitor.start()
            result = monitor.finish()

        self.assertEqual(result, {
            "status": "recorded", "file": REGRESSION.RESOURCE_FILE,
        })
        self.assertEqual(popen_commands[0][-3:], [monitor.remote, "1", "124"])
        self.assertTrue(process.terminated)
        self.assertIn(monitor.remote, commands[0][0])

    def test_monitor_rejects_an_artifact_that_holds_no_samples(self):
        # The header is written before the sampling loop, so a header-only file
        # means the printer-side pass never emitted a row.  That has to surface
        # as a failure: a report that shows an empty resource timeline reads as
        # "the host was idle", which is the opposite conclusion.
        with tempfile.TemporaryDirectory() as temporary:
            output = pathlib.Path(temporary)

            class Connection:
                ssh_target = "root@printer.invalid"
                scp_target = "root@printer.invalid"

                @staticmethod
                def ssh(command, timeout=None):
                    del command, timeout
                    return ""

                @staticmethod
                def command_runner(command, **_kwargs):
                    pathlib.Path(command[-1]).write_text(
                        "epoch\tuptime\tload1\tmem_available_kb\n",
                        encoding="utf-8")
                    return subprocess.CompletedProcess(command, 0, "", "")

            class Process:
                returncode = 0

                def poll(self):
                    return self.returncode

            monitor = REGRESSION.ResourceMonitor(
                Connection(), output, 10.0, clock=lambda: 1000.0,
                popen=lambda *_args, **_kwargs: Process(),
                sleeper=lambda _delay: None)
            monitor.process = Process()
            with self.assertRaises(REGRESSION.RegressionError) as caught:
                monitor.finish()

        self.assertIn("no samples", str(caught.exception))


class TelemetryContractTest(unittest.TestCase):
    def test_rt_query_has_a_short_timeout_and_one_status_snapshot(self):
        connection = mock.Mock()
        connection.request_json.return_value = {
            "result": {"status": {"toolhead": {}}},
        }
        client = REGRESSION.PrinterRunClient(connection)

        self.assertEqual(client.telemetry_snapshot(), {"toolhead": {}})
        connection.request_json.assert_called_once_with(
            "GET", REGRESSION.TELEMETRY_QUERY, timeout=0.5)

    def test_recorder_collects_in_a_thread_independent_of_run_polling(self):
        with tempfile.TemporaryDirectory() as temporary:
            sampled = threading.Event()

            def snapshot():
                sampled.set()
                return {
                    "toolhead": {}, "motion_report": {}, "extruder": {},
                    "heater_bed": {}, "print_stats": {},
                    "virtual_sdcard": {}, "feather_screen": {},
                }

            recorder = REGRESSION.TelemetryRecorder(
                pathlib.Path(temporary), 5.0, snapshot)
            recorder.start(0.0, lambda: {
                "host_suite": "ui", "printer_suite": "UI",
            })
            self.assertTrue(sampled.wait(1.0))
            report = recorder.finish()

        self.assertEqual(report["status"], "recorded")
        self.assertGreaterEqual(report["sample_count"], 1)

    def test_disabled_recorder_never_calls_its_snapshot_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = mock.Mock()
            recorder = REGRESSION.TelemetryRecorder(
                pathlib.Path(temporary), 0.0, snapshot)

            recorder.start(0.0, lambda: {})
            report = recorder.finish()

        snapshot.assert_not_called()
        self.assertEqual(report["status"], "disabled")

    def test_wait_stops_after_thirty_seconds_without_status_events(self):
        class Clock:
            now = 0.0

            def __call__(self):
                return self.now

            def sleep(self, delay):
                self.now += delay

        clock = Clock()
        client = REGRESSION.PrinterRunClient(
            object(), clock=clock, sleeper=clock.sleep)
        marker = {
            "run_id": "20260811-120000-000001-ui",
            "suite": "UI",
            "directory": REGRESSION.ARTIFACT_ROOT +
                         "/20260811-120000-000001-ui",
        }
        client.active_marker = mock.Mock(return_value=marker)

        with self.assertRaisesRegex(
                REGRESSION.RegressionError,
                "no printer status event for 30 seconds"):
            client.wait(
                marker, 100.0,
                run_state=lambda _run_id: "active",
                events_alive=lambda: clock.now < 30.0)

        self.assertEqual(clock.now, 30.0)
        client.active_marker.assert_not_called()

    def test_wait_emits_progress_from_its_existing_observation_loop(self):
        class Clock:
            now = 0.0

            def __call__(self):
                return self.now

            def sleep(self, delay):
                self.now += delay

        clock = Clock()
        client = REGRESSION.PrinterRunClient(
            object(), clock=clock, sleeper=clock.sleep)
        marker = {
            "run_id": "20260811-120000-000001-ui",
            "suite": "UI",
            "directory": REGRESSION.ARTIFACT_ROOT +
                         "/20260811-120000-000001-ui",
        }
        observed = []

        timed_out = client.wait(
            marker, 100.0,
            run_state=lambda _run_id: (
                "finished" if clock.now >= 21.0 else "active"),
            events_alive=lambda: True,
            progress=observed.append)

        self.assertFalse(timed_out)
        self.assertEqual(observed, [10.0, 20.0])

    def test_run_completion_is_owned_by_status_heartbeat(self):
        recorder = REGRESSION.TelemetryRecorder(
            pathlib.Path("/tmp"), 1.0, lambda: {})
        recorder.expect_run("owned-run")
        recorder.sample_count = 1
        recorder.latest_test_status = {
            "running": True, "finalizing": False, "run_id": "owned-run",
        }
        self.assertEqual(recorder.run_state("owned-run"), "active")
        recorder.sample_count = 2
        recorder.latest_test_status = {"running": False}
        self.assertEqual(recorder.run_state("owned-run"), "finished")
        recorder.latest_test_status = {
            "running": True, "run_id": "another-run",
        }
        self.assertEqual(recorder.run_state("owned-run"), "changed")

    def test_recorder_normalizes_rt_state_and_keeps_durable_jsonl(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = pathlib.Path(temporary)
            recorder = REGRESSION.TelemetryRecorder(
                output, 5.0,
                snapshot=lambda: {
                    "toolhead": {
                        "homed_axes": "xyz", "position": [9, 8, 7, 0],
                    },
                    "motion_report": {
                        "live_position": [1.25, 2.5, 3.75, 0],
                        "live_velocity": 12.5,
                    },
                    "extruder": {
                        "temperature": 249.4, "target": 250.0,
                    },
                    "heater_bed": {
                        "temperature": 34.8, "target": 0.0,
                    },
                    "print_stats": {"state": "standby"},
                    "virtual_sdcard": {"progress": 0.25},
                    "feather_screen": {
                        "page": "ACTION_PROMPT",
                        "context_path": ["Cold Pull"],
                        "context_types": ["cold_pull"],
                        "current_state": "COOLING NOZZLE",
                        "ui_test": {
                            "running": True, "suite": "CONTEXT_MATERIAL",
                            "phase": "cold_pull", "step": "cold_pull-open",
                            "step_index": 12, "step_count": 20,
                        },
                    },
                }, clock=lambda: 10.2, wall_clock=lambda: 1000.0)
            recorder.start(10.0)
            recorder.sample({
                "host_suite": "material",
                "printer_suite": "CONTEXT_MATERIAL",
            })
            report = recorder.finish()
            record = json.loads(
                (output / "telemetry.jsonl").read_text(encoding="utf-8"))

        self.assertEqual(report["status"], "recorded")
        self.assertEqual(report["sample_count"], 1)
        self.assertEqual(report["effective_rate_hz"], 0.0)
        self.assertAlmostEqual(record["offset"], 0.2)
        self.assertEqual(record["position"], [1.25, 2.5, 3.75])
        self.assertEqual(record["homed_axes"], "XYZ")
        self.assertEqual(record["test"]["step"], "cold_pull-open")
        self.assertEqual(record["context"]["state"], "COOLING NOZZLE")
        # A printer that publishes no [mcu] status still has to produce a valid
        # record, and an absent lookahead reads as unknown rather than zero.
        self.assertIsNone(record["mcu"])
        self.assertIsNone(record["buffer"]["margin"])

    def test_recorder_keeps_the_lookahead_margin_and_mcu_load(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = pathlib.Path(temporary)
            recorder = REGRESSION.TelemetryRecorder(
                output, 5.0,
                snapshot=lambda: {
                    "toolhead": {
                        "homed_axes": "xyz", "position": [9, 8, 7, 0],
                        "print_time": 812.5, "estimated_print_time": 811.25,
                        "stalls": 3,
                    },
                    "motion_report": {},
                    "extruder": {}, "heater_bed": {},
                    "print_stats": {"state": "standby"},
                    "virtual_sdcard": {},
                    "mcu": {"last_stats": {
                        "mcu_awake": 0.412, "mcu_task_avg": 0.000031,
                        "bytes_retransmit": 12, "srtt": 0.002,
                    }},
                    "feather_screen": {"page": "IDLE_HOME"},
                }, clock=lambda: 10.0, wall_clock=lambda: 1000.0)
            recorder.start(10.0)
            recorder.sample({})
            recorder.finish()
            record = json.loads(
                (output / "telemetry.jsonl").read_text(encoding="utf-8"))

        # "Timer too close" is the host running out of planned move time, so the
        # margin between print_time and the MCU clock is the number that has to
        # survive the shutdown in the report.
        self.assertAlmostEqual(record["buffer"]["margin"], 1.25)
        self.assertEqual(record["buffer"]["stalls"], 3)
        self.assertAlmostEqual(record["mcu"]["awake"], 0.412)
        self.assertEqual(record["mcu"]["bytes_retransmit"], 12)

    def test_recorder_failure_is_reported_without_raising(self):
        with tempfile.TemporaryDirectory() as temporary:
            def unavailable():
                raise PrinterConnectionError("synthetic telemetry failure")

            recorder = REGRESSION.TelemetryRecorder(
                pathlib.Path(temporary), 5.0, unavailable,
                clock=lambda: 1.0, wall_clock=lambda: 2.0)
            recorder.start(0.0)
            recorder.sample({"host_suite": "ui", "printer_suite": "UI"})
            report = recorder.finish()

        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["sample_count"], 0)
        self.assertEqual(report["failure_count"], 1)


class TimelineAndReportTest(unittest.TestCase):
    def test_report_write_failure_is_published_without_masking_the_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = pathlib.Path(temporary) / "run"
            args = arguments(output, suite="core")
            run = REGRESSION.RegressionRun(
                args, client=FakeClient(), media=FakeMedia(),
                telemetry=FakeTelemetry(args.telemetry_rate),
                progress=lambda _message: None)
            with mock.patch.object(
                    REGRESSION, "_host_preflight",
                    side_effect=fake_host_preflight), \
                    mock.patch.object(
                        REGRESSION, "_write_report",
                        side_effect=OSError("read-only filesystem")):
                report, result_output = run.run()

        self.assertEqual(result_output, output.resolve())
        self.assertEqual(report["status"], "error")
        self.assertEqual(
            report["infrastructure_error"]["category"], "OSError")
        self.assertEqual(
            report["infrastructure_error"]["message"],
            "unable to write local report")

    def test_printer_wall_clock_skew_does_not_change_relative_timeline(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            artifact = root / "suite"
            artifact.mkdir()
            first = artifact / "001.bmp"
            second = artifact / "002.bmp"
            first.write_bytes(b"BMfirst")
            second.write_bytes(b"BMsecond")
            suites = [{
                "artifact_path": str(artifact),
                "timeline_start_seconds": 5.0,
                "summary": {"started_at": 50_000.0},
                "manifest": [
                    {"file": first.name, "time": 50_002.0},
                    {"file": second.name, "time": 50_004.5},
                ],
            }]
            media = REGRESSION.MediaPipeline(root, 10)
            frames = media._screen_frames(suites)

        self.assertEqual([item[0] for item in frames], [7.0, 9.5])

    def test_screen_is_blank_until_first_observed_semantic_frame(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            artifact = root / "suite"
            artifact.mkdir()
            for name in ("001.bmp", "002.bmp"):
                (artifact / name).write_bytes(b"BMsynthetic")

            commands = []

            def runner(command, **_kwargs):
                commands.append(command)
                pathlib.Path(command[-1]).write_bytes(b"synthetic video")
                return subprocess.CompletedProcess(command, 0, "", "")

            media = REGRESSION.MediaPipeline(
                root, 10, runner=runner, popen=FakePopen())
            media.work.mkdir()
            screen = media._screen_video([{
                "artifact_path": str(artifact),
                "timeline_start_seconds": 5.0,
                "summary": {"started_at": 1000.0},
                "manifest": [
                    {"file": "001.bmp", "time": 1002.0},
                    {"file": "002.bmp", "time": 1004.5},
                ],
            }], 12.0)
            concat = (media.work / "screen.ffconcat").read_text(
                encoding="utf-8")
            screen_filter = commands[-1][commands[-1].index("-vf") + 1]

        self.assertIsNotNone(screen)
        self.assertEqual(concat.count("duration 2.500000"), 2)
        self.assertIn("start_duration=7.000000", screen_filter)

    def test_no_camera_finalizes_screen_only_without_prior_work_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            artifact = root / "suite"
            artifact.mkdir()
            (artifact / "001.bmp").write_bytes(b"BMsynthetic")

            def runner(command, **_kwargs):
                pathlib.Path(command[-1]).write_bytes(b"synthetic video")
                return subprocess.CompletedProcess(command, 0, "", "")

            media = REGRESSION.MediaPipeline(
                root, 10, runner=runner, popen=FakePopen())
            result = media.finalize([{
                "artifact_path": str(artifact),
                "timeline_start_seconds": 0.0,
                "summary": {"started_at": 1000.0},
                "manifest": [{"file": "001.bmp", "time": 1000.0}],
            }], 2.0)

            recording_exists = (root / "recording.mp4").is_file()

        self.assertEqual(result["status"], "passed")
        self.assertTrue(recording_exists)

    def test_html_and_json_distinguish_failures_warnings_and_skips(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = pathlib.Path(temporary)
            report = {
                "schema_version": 1, "status": "error",
                "requested_suite": "all", "printer_host": "printer.invalid",
                "started_at": "2026-08-11T12:00:00+00:00",
                "finished_at": "2026-08-11T12:01:00+00:00",
                "duration_seconds": 60.0, "fps": 10,
                "camera": {"status": "unavailable", "metadata": None},
                "media": {"status": "failed", "recording": None},
                "warnings": ["camera unavailable"],
                "infrastructure_error": {
                    "category": "Safety", "message": "heaters remain on",
                },
                "suites": [{
                    "name": "core", "printer_suite": "FULL",
                    "status": "failed", "outcome": "failed",
                    "reason": "assertion", "started_at": None,
                    "finished_at": None, "duration_seconds": 2.0,
                    "timeline_start_seconds": 0.0,
                    "screenshot_count": 1, "artifact": None, "links": {},
                }, {
                    "name": "print", "printer_suite": "CONTEXT_PRINT",
                    "status": "skipped", "outcome": None,
                    "reason": "heaters remain on", "started_at": None,
                    "finished_at": None, "duration_seconds": None,
                    "timeline_start_seconds": None,
                    "screenshot_count": 0, "artifact": None, "links": {},
                }],
            }
            REGRESSION._write_report(output, report)
            page = (output / "report.html").read_text(encoding="utf-8")
            durable = json.loads(
                (output / "report.json").read_text(encoding="utf-8"))

        self.assertIn("assertion", page)
        self.assertIn("heaters remain on", page)
        self.assertIn("camera unavailable", page)
        self.assertEqual(durable["suites"][1]["status"], "skipped")


class VideoLayoutTest(unittest.TestCase):
    @staticmethod
    def telemetry_record(offset=0.0):
        return {
            "offset": offset, "time": 1000.0 + offset,
            "test": {
                "host_suite": "material", "phase": "cold_pull",
                "step": "cold_pull-open", "step_index": 12,
                "step_count": 20,
            },
            "page": "ACTION_PROMPT", "print_state": "standby",
            "position": [1.25, 2.5, 3.75], "homed_axes": "XYZ",
            "velocity": 4.5,
            "nozzle": {"temperature": 249.4, "target": 250.0},
            "bed": {"temperature": 34.8, "target": 0.0},
            "context": {
                "path": ["Cold Pull"], "state": "COOLING NOZZLE",
            },
        }

    def test_rt_panel_is_a_timestamped_800_by_150_timeline(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            (root / "telemetry.jsonl").write_text(
                json.dumps(self.telemetry_record(0.0)) + "\n" +
                json.dumps(self.telemetry_record(1.0)) + "\n",
                encoding="utf-8")
            (root / "resources.tsv").write_text(
                "epoch\tuptime\tload1\tmem_available_kb\tswap_free_kb\t"
                "role\tpid\tcpu_ticks\trss_kb\n"
                "1000\t1\t0.4\t40960\t0\tsystem\t0\t1000\t0\n"
                "1000\t1\t0.4\t40960\t0\tklippy\t10\t100\t24576\n"
                "1000\t1\t0.4\t40960\t0\ttyper\t11\t10\t3072\n"
                "1001\t2\t0.8\t39936\t0\tsystem\t0\t1100\t0\n"
                "1001\t2\t0.8\t39936\t0\tklippy\t10\t125\t24576\n"
                "1001\t2\t0.8\t39936\t0\ttyper\t11\t12\t3072\n",
                encoding="utf-8")

            def runner(command, **_kwargs):
                pathlib.Path(command[-1]).write_bytes(b"synthetic video")
                return subprocess.CompletedProcess(command, 0, "", "")

            popen = FakePopen()
            media = REGRESSION.MediaPipeline(
                root, 10, runner=runner, popen=popen)
            media.work.mkdir()
            panel = media._telemetry_video(2.0)
            command = popen.processes[-1].command
            panel_record = self.telemetry_record(1.0)
            panel_record["resources"] = media._resource_records()[-1]
            lines = media._panel_lines(panel_record)

        self.assertIsNotNone(panel)
        self.assertIn("800x150", command)
        self.assertIn("cold_pull-open", lines[0])
        self.assertIn("XYZ 1 2 4", lines[2])
        self.assertNotIn(".00", lines[2])
        self.assertIn("COOLING NOZZLE", lines[3])
        self.assertEqual(len(lines), 5)
        self.assertIn("CPU K25 T2 D0%", lines[4])
        self.assertIn("MEM 39M", lines[4])
        self.assertGreater(popen.processes[-1].stdin.bytes_written, 0)

    def test_final_layout_is_vertical_and_does_not_exceed_1080p_height(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            artifact = root / "suite"
            artifact.mkdir()
            (artifact / "001.bmp").write_bytes(b"BMsynthetic")
            (root / "telemetry.jsonl").write_text(
                json.dumps(self.telemetry_record()) + "\n",
                encoding="utf-8")
            commands = []

            def runner(command, **_kwargs):
                commands.append(command)
                pathlib.Path(command[-1]).write_bytes(b"synthetic video")
                return subprocess.CompletedProcess(command, 0, "", "")

            media = REGRESSION.MediaPipeline(
                root, 10, runner=runner, popen=FakePopen())
            media.work.mkdir()
            media.camera_path.write_bytes(b"synthetic camera")
            result = media.finalize([{
                "artifact_path": str(artifact),
                "timeline_start_seconds": 0.0,
                "summary": {"started_at": 1000.0},
                "manifest": [{"file": "001.bmp", "time": 1000.0}],
            }], 2.0)
            final_command = commands[-1]

        self.assertEqual(result["status"], "passed")
        self.assertEqual(final_command.count("-i"), 3)
        graph = final_command[final_command.index("-filter_complex") + 1]
        self.assertIn("scale=800:480", graph)
        self.assertIn("scale=800:450", graph)
        self.assertIn("scale=800:150", graph)
        self.assertIn("vstack=inputs=3", graph)


class HostPreflightTest(unittest.TestCase):
    def test_missing_ffmpeg_is_reported_before_printer_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = pathlib.Path(temporary) / "run"
            available = {"ssh": "/usr/bin/ssh", "scp": "/usr/bin/scp"}
            with self.assertRaisesRegex(
                    REGRESSION.RegressionError, "ffmpeg"):
                REGRESSION._host_preflight(
                    output, 1, 10,
                    which=lambda name: available.get(name),
                    disk_usage=lambda _path: shutil.disk_usage(temporary))
            self.assertTrue(output.exists())

    def test_missing_ssh_or_scp_is_actionable(self):
        for missing in ("ssh", "scp"):
            with self.subTest(missing=missing), \
                    tempfile.TemporaryDirectory() as temporary:
                output = pathlib.Path(temporary) / "run"
                available = {
                    "ffmpeg": "/usr/bin/ffmpeg",
                    "ssh": "/usr/bin/ssh",
                    "scp": "/usr/bin/scp",
                }
                available.pop(missing)
                with self.assertRaisesRegex(
                        REGRESSION.RegressionError, missing):
                    REGRESSION._host_preflight(
                        output, 1, 10,
                        which=lambda name: available.get(name),
                        disk_usage=lambda _path:
                            shutil.disk_usage(temporary))


class PrinterSafetyTest(unittest.TestCase):
    def test_print_heater_virtual_sd_and_pause_each_block_testing(self):
        unsafe = ({
            "print_state": "printing",
            "heater_targets": {"extruder": 0.0, "heater_bed": 0.0},
            "virtual_sd_active": False,
            "paused": False,
        }, {
            "print_state": "standby",
            "heater_targets": {"extruder": 1.0, "heater_bed": 0.0},
            "virtual_sd_active": False,
            "paused": False,
        }, {
            "print_state": "standby",
            "heater_targets": {"extruder": 0.0, "heater_bed": 0.0},
            "virtual_sd_active": True,
            "paused": False,
        }, {
            "print_state": "standby",
            "heater_targets": {"extruder": 0.0, "heater_bed": 0.0},
            "virtual_sd_active": False,
            "paused": True,
        })
        for state in unsafe:
            with self.subTest(state=state):
                connection = PrinterConnection("printer.invalid")
                connection.printer_status = lambda state=state: dict(state)
                with self.assertRaises(PrinterConnectionError):
                    connection.require_safe_idle()

    def test_incomplete_status_fails_closed(self):
        connection = PrinterConnection("printer.invalid")
        connection.request_json = lambda *_args, **_kwargs: {
            "result": {"status": {
                "print_stats": {"state": "standby"},
                "extruder": {"target": 0.0},
            }},
        }

        with self.assertRaisesRegex(
                PrinterConnectionError, "status is incomplete"):
            connection.require_safe_idle()


if __name__ == "__main__":
    unittest.main()
