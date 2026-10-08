## Host-side contracts for Codex screenshot review.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Exercise the CLI protocol without invoking a model or contacting a printer."""

import base64
import io
from concurrent.futures import CancelledError
import json
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from tests.visual_checks import codex_cli, regression, run
from tests.visual_checks import openai_compatible as vision


def spacing():
    return {
        "defect": False, "subject": "body text", "gap_relation": "clear",
        "reason": "Visible clearance from the header.",
    }


def verdict():
    return {
        "verdict": "pass", "summary": "Readable screenshot.",
        "checks": [
            {"id": check["id"], "status": "pass",
             "evidence_class": "none", "reason": ""}
            for check in vision.CHECKLIST
        ],
    }


class CodexProtocolTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ff5m-codex-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.command = self.root / "fake-codex"
        # A real child process exercises stdin, image files, schemas, and final
        # answer files across the same boundary as the installed CLI.
        self.command.write_text("#!" + sys.executable + "\n" + '''
import argparse
import json
import os
import pathlib
import sys
import time

root = pathlib.Path(__file__).parent
parser = argparse.ArgumentParser()
parser.add_argument("action", choices=["exec"])
parser.add_argument("--model", required=True)
parser.add_argument("--config", action="append", default=[])
parser.add_argument("--sandbox", choices=["read-only"], required=True)
parser.add_argument("--ephemeral", action="store_true")
parser.add_argument("--ignore-user-config", action="store_true")
parser.add_argument("--skip-git-repo-check", action="store_true")
parser.add_argument("--color", choices=["never"])
parser.add_argument("--output-schema", required=True)
parser.add_argument("--output-last-message", required=True)
parser.add_argument("--image", action="append", required=True)
parser.add_argument("prompt", choices=["-"])
args = parser.parse_args()
log = root / "requests.jsonl"
index = len(log.read_text().splitlines()) if log.exists() else 0
record = vars(args)
record["prompt"] = sys.stdin.read()
record["schema"] = json.loads(pathlib.Path(args.output_schema).read_text())
record["images"] = [pathlib.Path(path).read_bytes().hex() for path in args.image]
record["cwd"] = str(pathlib.Path.cwd())
with log.open("a") as stream:
    stream.write(json.dumps(record) + "\\n")
response = json.loads((root / "responses.json").read_text())[index]
if response.get("delay"):
    (root / ("started-%d" % os.getpid())).write_text("running")
    time.sleep(response["delay"])
print("private CLI diagnostic", file=sys.stderr)
print("event stream is not the final answer")
if "exit" in response:
    sys.exit(response["exit"])
if "missing" not in response:
    raw = response.get("raw", json.dumps(response.get("answer")))
    pathlib.Path(args.output_last_message).write_bytes(raw.encode("utf-8"))
''', encoding="utf-8")
        self.command.chmod(0o755)

    def responses(self, *values):
        (self.root / "responses.json").write_text(
            json.dumps(values), encoding="utf-8")

    def requests(self):
        path = self.root / "requests.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()]

    def settings(self, **kwargs):
        return vision.VisualCheckSettings(
            enabled=True, backend="codex", codex_command=str(self.command), **kwargs)

    def test_parity_attaches_both_images_in_order_and_preserves_shared_prompts(self):
        self.responses({"answer": spacing()}, {"answer": spacing()}, {"answer": verdict()})
        settings = self.settings()
        context = {"_comparison_image": (b"printer frame", "image/jpeg")}
        result = vision.VisualCheckEvaluator(settings).evaluate(
            b"designer frame", "image/png", context)

        self.assertEqual(result["status"], "passed")
        requests = self.requests()
        self.assertEqual([item["images"] for item in requests], [
            [b"designer frame".hex()], [b"printer frame".hex()],
            [b"designer frame".hex(), b"printer frame".hex()],
        ])
        payload = vision._completion_payload(
            settings.model, b"designer frame", "image/png", context)
        self.assertEqual(requests[-1]["schema"], payload["response_format"]["json_schema"]["schema"])
        for message in payload["messages"]:
            parts = message["content"]
            parts = [{"type": "text", "text": parts}] if isinstance(parts, str) else parts
            for part in parts:
                if part["type"] == "text":
                    self.assertIn(part["text"], requests[-1]["prompt"])
        for request in requests:
            self.assertEqual(request["model"], "gpt-6-luna")
            self.assertIn('model_reasoning_effort="high"', request["config"])
            self.assertTrue(request["ephemeral"])
            self.assertTrue(request["ignore_user_config"])
            self.assertIn('approval_policy="never"', request["config"])
            self.assertFalse(pathlib.Path(request["cwd"]).exists())

    def test_relative_executable_path_survives_isolated_working_directory(self):
        self.responses({"answer": spacing()}, {"answer": verdict()})
        settings = self.settings()
        settings.codex_command = os.path.relpath(self.command)
        result = vision.VisualCheckEvaluator(settings).evaluate(b"frame", "image/png", {})
        self.assertEqual(result["status"], "passed")

    def test_invalid_final_json_retries_then_uses_valid_verdict(self):
        self.responses({"answer": spacing()}, {"raw": "{broken"}, {"answer": verdict()})
        result = vision.VisualCheckEvaluator(self.settings()).evaluate(b"frame", "image/png", {})
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["models"][0]["attempts"], 2)
        self.assertIn("previous response", self.requests()[-1]["prompt"])

    def test_clear_audit_without_a_subject_continues_to_full_review(self):
        for relation in ("clear", "uncertain"):
            with self.subTest(relation=relation):
                (self.root / "requests.jsonl").unlink(missing_ok=True)
                audit = dict(spacing(), subject="", gap_relation=relation)
                self.responses({"answer": audit}, {"answer": verdict()})
                result = vision.VisualCheckEvaluator(self.settings()).evaluate(
                    b"frame", "image/png", {})
                self.assertEqual(result["status"], "passed")
                self.assertEqual(len(self.requests()), 2)

    def test_invalid_audit_retries_once_and_preserves_the_corrected_defect(self):
        audit = dict(spacing(), defect=True, subject="footer text",
                     gap_relation="clipped", reason="The bottom glyph strokes are cut off.")
        self.responses({"answer": dict(audit, subject="")},
                       {"answer": audit}, {"answer": verdict()})
        result = vision.VisualCheckEvaluator(self.settings()).evaluate(b"frame", "image/png", {})
        model = result["models"][0]
        self.assertEqual(model["status"], "completed")
        self.assertEqual(model["verdict"], "fail")
        self.assertIn("bottom glyph strokes", model["reasons"][0]["reason"])
        requests = self.requests()
        self.assertEqual(len(requests), 3)
        self.assertEqual(requests[0]["images"], requests[1]["images"])

    def test_bad_audit_and_exhausted_verdict_retries_are_reported(self):
        cases = (
            ({"raw": "not JSON"}, {"raw": "not JSON"}),
            ({"missing": True}, {"missing": True}),
            ({"raw": "x" * (vision.MAX_RESPONSE_BYTES + 1)},) * 2,
            ({"answer": {"unexpected": True}},) * 2,
            ({"answer": spacing()}, {"raw": "invalid"}, {"missing": True}),
        )
        for responses in cases:
            with self.subTest(responses=len(responses)):
                (self.root / "requests.jsonl").unlink(missing_ok=True)
                self.responses(*responses)
                result = vision.VisualCheckEvaluator(self.settings(mode="strict")).evaluate(
                    b"frame", "image/png", {})
                self.assertEqual(result["status"], "failed")
                model = result["models"][0]
                self.assertEqual(model["error"]["category"], "invalid_response")
                self.assertEqual(model["json_validation"]["status"], "invalid")
                self.assertEqual(len(self.requests()), len(responses))

    def test_cli_failure_is_normalized_without_copying_diagnostics(self):
        self.responses({"exit": 7})
        result = vision.VisualCheckEvaluator(self.settings()).evaluate(b"frame", "image/png", {})
        self.assertEqual(result["status"], "warning")
        self.assertEqual(result["models"][0]["error"]["category"], "request_failed")
        self.assertIn("code 7", result["models"][0]["error"]["message"])
        self.assertNotIn("private CLI diagnostic", json.dumps(result))

    def test_missing_cli_is_reported_and_disabled_checks_never_start_it(self):
        self.command.unlink()
        settings = self.settings(mode="strict")
        result = vision.VisualCheckEvaluator(settings).evaluate(b"frame", "image/png", {})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["preflight"]["status"], "service_unavailable")
        settings.enabled = False
        with mock.patch.object(codex_cli.subprocess, "Popen") as launch:
            result = vision.VisualCheckEvaluator(settings).evaluate(b"frame", "image/png", {})
        self.assertEqual(result["status"], "disabled")
        launch.assert_not_called()

    def test_image_cli_writes_codex_result_and_respects_environment_overrides(self):
        self.responses({"answer": spacing()}, {"answer": verdict()})
        image = self.root / "frame.png"
        image.write_bytes(b"frame")
        output = self.root / "report.json"
        with mock.patch.dict(os.environ, {
                "FF5M_VISUAL_BACKEND": "codex", "FF5M_VISUAL_MODEL": "gpt-6-luna",
                "FF5M_VISUAL_REASONING_EFFORT": "medium",
                "FF5M_VISUAL_CODEX_COMMAND": str(self.command),
                "FF5M_VISUAL_REVIEW_WORKERS": "3",
                "FF5M_VISUAL_TIMEOUT": "5"}, clear=True), mock.patch("builtins.print"):
            code = run.main([str(image), "--enable", "--reasoning-effort", "high",
                             "--mode", "strict", "--review-workers", "2", "--output", str(output)])
        artifact = json.loads(output.read_text())
        self.assertEqual(code, 0)
        self.assertEqual(artifact["status"], "passed")
        self.assertEqual(artifact["configuration"]["backend"], "codex")
        self.assertEqual(artifact["configuration"]["reasoning_effort"], "high")
        self.assertEqual(artifact["configuration"]["timeout"], 5)
        self.assertEqual(artifact["configuration"]["review_workers"], 2)

    def test_parallel_cancellation_stops_running_cli_processes_and_pending_reviews(self):
        self.responses(*({"delay": 10} for _ in range(8)))
        images = []
        for index in range(8):
            image = self.root / ("frame-%d.png" % index)
            image.write_bytes(b"frame")
            images.append({"path": image, "mime_type": "image/png", "context": {}})
        settings = self.settings(review_workers=4, timeout=5)
        evaluator = vision.VisualCheckEvaluator(settings)
        cancelled_at = []

        def interrupt_when_started(_futures):
            deadline = time.monotonic() + 4
            while len(list(self.root.glob("started-*"))) < 4:
                if time.monotonic() >= deadline:
                    self.fail("The configured CLI workers did not start")
                time.sleep(0.01)
            cancelled_at.append(time.monotonic())
            raise KeyboardInterrupt()

        with mock.patch.object(run, "as_completed", side_effect=interrupt_when_started):
            with self.assertRaises(KeyboardInterrupt):
                run.run_checks(settings, images, evaluator=evaluator)

        self.assertLess(time.monotonic() - cancelled_at[0], 2)
        started = list(self.root.glob("started-*"))
        self.assertEqual(len(started), 4)
        for path in started:
            with self.assertRaises(ProcessLookupError):
                os.kill(int(path.name.split("-")[1]), 0)
        with self.assertRaises(CancelledError):
            evaluator.evaluate(b"frame", "image/png", {})

    def test_designer_regression_writes_json_markdown_and_html_with_codex_results(self):
        self.responses({"answer": spacing()}, {"answer": verdict()})
        scenarios = self.root / "scenarios.json"
        scenarios.write_text(json.dumps({"schema_version": 1, "cases": []}))
        expectations = self.root / "expectations.json"
        expectations.write_text(json.dumps({"schema_version": 1, "cases": {
            "default-alpha": {
                "description": "A readable frame.", "required": ["body text"],
                "forbidden": ["blank frame"], "allowed_variations": [],
            },
        }}))
        output = self.root / "regression"

        def capture(cases, directory, **_kwargs):
            directory.mkdir(parents=True)
            frame = directory / "alpha.png"
            frame.write_bytes(b"frame")
            return [{
                "case_id": cases[0]["id"], "label": "Alpha", "page": "Alpha",
                "semantic_page_id": "ui.Pages.ALPHA", "source": "designer", "path": frame,
            }]

        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(regression.hybrid, "discover_designer", return_value={
                    "status": "ok", "pages": [{"id": "ui.Pages.ALPHA", "title": "Alpha"}]}), \
                mock.patch.object(regression.hybrid.DesignerCapture, "capture", side_effect=capture), \
                mock.patch.object(regression.printer, "PrinterCollector") as printer, \
                mock.patch("builtins.print"):
            code = regression.main([
                "--mode", "designer", "--designer-root", str(self.root / "designer"),
                "--env-file", str(self.root / "absent.env"), "--scenarios", str(scenarios),
                "--expectations", str(expectations), "--output", str(output), "--enable",
                "--backend", "codex", "--codex-command", str(self.command),
                "--review-workers", "3",
            ])
        printer.assert_not_called()
        report = json.loads((output / "report.json").read_text())
        self.assertEqual(code, 3)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["screenshots"][0]["case_result"]["verdict"], "pass")
        self.assertEqual(report["configuration"]["backend"], "codex")
        self.assertEqual(report["configuration"]["review_workers"], 3)
        self.assertIn("gpt-6-luna", (output / "report.md").read_text())
        self.assertIn("high", (output / "report-run.html").read_text())
        self.assertTrue((output / "report.html").is_file())


class ParallelReviewTest(unittest.TestCase):
    def test_reviews_overlap_with_bounded_workers_and_reports_stay_in_input_order(self):
        for override, workers in ((None, 8), (3, 3)):
            with self.subTest(workers=workers), tempfile.TemporaryDirectory() as temporary:
                root = pathlib.Path(temporary)
                images = []
                for index in range(10):
                    path = root / ("%d.png" % index)
                    path.write_bytes(str(index).encode())
                    images.append({"path": path, "mime_type": "image/png",
                                   "context": {"case_id": str(index)}})
                lock = threading.Lock()
                barrier = threading.Barrier(workers)
                release_first = threading.Event()
                active = 0
                peak = 0
                model_calls = []

                def models():
                    model_calls.append(True)
                    time.sleep(0.02)
                    return {"data": [{"id": "gpt-6-luna"}]}

                def complete(payload):
                    nonlocal active, peak
                    if payload["response_format"]["json_schema"]["name"] != "ff5m_ui_spacing_audit":
                        return verdict()
                    encoded = payload["messages"][1]["content"][1]["image_url"]["url"].split(",")[1]
                    index = int(base64.b64decode(encoded))
                    with lock:
                        active += 1
                        peak = max(peak, active)
                    try:
                        if index < workers:
                            barrier.wait(timeout=3)
                        if index == 0 and not release_first.wait(timeout=3):
                            self.fail("Progress did not report a completed later screenshot")
                        return spacing()
                    finally:
                        with lock:
                            active -= 1

                settings = vision.VisualCheckSettings(enabled=True, review_workers=override)
                evaluator = vision.VisualCheckEvaluator(
                    settings, transport=mock.Mock(models=models, complete=complete))
                events = []
                callback_threads = []

                def progress(event):
                    events.append(event)
                    callback_threads.append(threading.get_ident())
                    if event["completed"]:
                        release_first.set()

                artifact = run.run_checks(settings, images, evaluator=evaluator, progress=progress)

                self.assertEqual(artifact["status"], "passed")
                self.assertEqual(peak, workers)
                self.assertEqual(len(model_calls), 1)
                self.assertEqual([item["screenshot"]["case_id"] for item in artifact["screenshots"]],
                                 [str(index) for index in range(10)])
                self.assertEqual([event["completed"] for event in events], list(range(11)))
                self.assertNotEqual(events[1]["case_id"], "0")
                self.assertEqual(set(callback_threads), {threading.get_ident()})
                self.assertEqual(events[-1]["eta_seconds"], 0)
                self.assertAlmostEqual(artifact["summary"]["review_elapsed_seconds"],
                                       events[-1]["elapsed_seconds"], places=5)
                self.assertEqual([event["elapsed_seconds"] for event in events],
                                 sorted(event["elapsed_seconds"] for event in events))

    def test_local_llm_remains_sequential_by_default(self):
        settings = vision.VisualCheckSettings(
            enabled=True, base_url="http://unused.invalid/v1", model="local")
        threads = []

        def complete(payload):
            threads.append(threading.get_ident())
            return spacing() if payload["response_format"]["json_schema"]["name"] == "ff5m_ui_spacing_audit" else verdict()

        evaluator = vision.VisualCheckEvaluator(settings, transport=mock.Mock(
            models=lambda: {"data": [{"id": "local"}]}, complete=complete))
        with tempfile.TemporaryDirectory() as temporary:
            image = pathlib.Path(temporary) / "frame.png"
            image.write_bytes(b"frame")
            images = [{"path": image, "mime_type": "image/png", "context": {}}] * 3
            artifact = run.run_checks(settings, images, evaluator=evaluator)

        self.assertEqual(artifact["status"], "passed")
        self.assertEqual(len(threads), 6)
        self.assertEqual(set(threads), {threading.get_ident()})

    def test_worker_failure_cancels_other_reviews_and_propagates_original_error(self):
        settings = vision.VisualCheckSettings(enabled=True, review_workers=2)
        evaluator = vision.VisualCheckEvaluator(settings)
        waiting = threading.Event()
        cancelled = threading.Event()

        def evaluate(_data, _mime, context):
            if context["case_id"] == "bad":
                if not waiting.wait(timeout=3):
                    self.fail("The other review did not start")
                raise ValueError("invalid image input")
            waiting.set()
            if not cancelled.wait(timeout=3):
                self.fail("The failed run did not cancel the other review")
            raise CancelledError()

        with tempfile.TemporaryDirectory() as temporary:
            image = pathlib.Path(temporary) / "frame.png"
            image.write_bytes(b"frame")
            images = [{"path": image, "mime_type": "image/png", "context": {"case_id": case}}
                      for case in ("bad", "waiting")]
            with mock.patch.object(evaluator, "evaluate", side_effect=evaluate), \
                    mock.patch.object(evaluator, "cancel", side_effect=cancelled.set):
                with self.assertRaisesRegex(ValueError, "invalid image input"):
                    run.run_checks(settings, images, evaluator=evaluator)

    def test_shared_preflight_failure_is_reported_for_every_frame(self):
        for mode, expected in (("advisory", "warning"), ("strict", "failed")):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                settings = vision.VisualCheckSettings(enabled=True, mode=mode)
                transport = mock.Mock()
                transport.models.side_effect = vision.TransportFailure("service_unavailable", "offline")
                evaluator = vision.VisualCheckEvaluator(settings, transport=transport)
                image = pathlib.Path(temporary) / "frame.png"
                image.write_bytes(b"frame")
                images = [{"path": image, "mime_type": "image/png", "context": {}}] * 10
                artifact = run.run_checks(settings, images, evaluator=evaluator)
                self.assertEqual(artifact["status"], expected)
                self.assertEqual(len(artifact["screenshots"]), 10)
                self.assertEqual(artifact["summary"]["statuses"], {"service_unavailable": 10})
                transport.models.assert_called_once()
                transport.complete.assert_not_called()


class CodexProcessTest(unittest.TestCase):
    def test_timeout_and_cancellation_kill_and_reap_owned_process(self):
        for failure in (subprocess.TimeoutExpired("codex", 1), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__):
                process = mock.Mock(pid=12345, stdin=io.BytesIO())
                process.communicate.side_effect = failure
                process.poll.return_value = None
                with mock.patch.object(codex_cli.subprocess, "Popen", return_value=process), \
                        mock.patch.object(codex_cli.os, "killpg") as kill:
                    expected = vision.TransportFailure if isinstance(failure, subprocess.TimeoutExpired) else KeyboardInterrupt
                    with self.assertRaises(expected):
                        codex_cli._run(["codex", "exec", "-"], "review", 1, "/tmp")
                kill.assert_called_once_with(process.pid, signal.SIGKILL)
                process.wait.assert_called_once()
                self.assertTrue(process.stdin.closed)

    def test_spawn_failure_is_normalized(self):
        with mock.patch.object(codex_cli.subprocess, "Popen", side_effect=OSError()):
            with self.assertRaises(vision.TransportFailure) as raised:
                codex_cli._run(["codex"], "review", 1, "/tmp")
        self.assertEqual(raised.exception.category, "service_unavailable")


class VisualBackendSettingsTest(unittest.TestCase):
    def test_defaults_and_explicit_backend_selection(self):
        codex = vision.VisualCheckSettings(enabled=True)
        self.assertEqual((codex.backend, codex.model, codex.reasoning_effort),
                         ("codex", "gpt-6-luna", "high"))
        self.assertEqual(codex.review_workers, 8)
        local = vision.VisualCheckSettings(enabled=True, base_url="http://localhost:1234/v1", model="local")
        self.assertEqual((local.backend, local.model, local.reasoning_effort, local.timeout),
                         ("openai-compatible", "local", "", 30))
        self.assertEqual(local.review_workers, 1)
        explicit = vision.VisualCheckSettings(enabled=True, backend="codex", base_url="http://unused.invalid")
        self.assertEqual(explicit.backend, "codex")
        mapped = vision.VisualCheckSettings.from_mapping({
            "backend": "codex", "reasoning_effort": "medium", "timeout": 5, "review_workers": "4"})
        self.assertEqual((mapped.backend, mapped.reasoning_effort, mapped.timeout), ("codex", "medium", 5))
        self.assertEqual(mapped.review_workers, 4)

    def test_review_worker_environment_and_cli_override(self):
        with mock.patch.dict(os.environ, {"FF5M_VISUAL_REVIEW_WORKERS": "4"}, clear=True):
            for argv, expected in (([], 4), (["--review-workers", "2"], 2)):
                args = regression._arguments(["--designer-root", "/unused", *argv])
                self.assertEqual(regression._visual_settings(args).review_workers, expected)

    def test_review_workers_reject_invalid_values(self):
        for value in (0, 33, -1, "bad", 1.5, True):
            with self.subTest(value=value), self.assertRaises(vision.VisualCheckConfigurationError):
                vision.VisualCheckSettings(review_workers=value)

    def test_invalid_backend_effort_and_http_configuration_are_rejected(self):
        for values in ({"backend": "unknown"}, {"reasoning_effort": "invalid"},
                       {"backend": "openai-compatible"}, {"timeout": 0}):
            with self.subTest(values=values), self.assertRaises(vision.VisualCheckConfigurationError):
                vision.VisualCheckSettings(enabled=True, **values)

    def test_regression_rejects_invalid_settings_before_capture(self):
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(regression.hybrid, "discover_designer") as discover:
            args = regression._arguments([
                "--mode", "designer", "--designer-root", temporary,
                "--reasoning-effort", "invalid", "--output", temporary,
            ])
            with self.assertRaises(vision.VisualCheckConfigurationError):
                regression.execute(args)
        discover.assert_not_called()


if __name__ == "__main__":
    unittest.main()
