## Host-side contracts for optional OpenAI-compatible UI visual checks.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Host-side contracts for optional OpenAI-compatible UI visual checks."""

import base64
import io
import json
import os
import pathlib
import struct
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from tests.visual_checks import openai_compatible as VISION  # noqa: E402
from tests.visual_checks import hybrid as HYBRID  # noqa: E402
from tests.visual_checks import printer as PRINTER  # noqa: E402
from tests.visual_checks import compare_reports as COMPARE  # noqa: E402
from tests.visual_checks import regression as REGRESSION  # noqa: E402
from tests.visual_checks import run as PIPELINE  # noqa: E402


def verdict(status="pass"):
    checks = []
    for index, item in enumerate(VISION.CHECKLIST):
        item_status = status if index == 0 else "pass"
        checks.append({
            "id": item["id"],
            "status": item_status,
            "reason": (
                "Visible overlap near the header."
                if item_status != "pass" else ""),
        })
    return {
        "verdict": status,
        "checks": checks,
        "summary": "Structured visual result.",
    }


class FakeHTTPResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class FakeOpenAIEndpoint:
    """In-memory OpenAI-compatible endpoint; it never opens a socket."""

    def __init__(self, models, model_responses):
        self.models = tuple(models)
        self.model_responses = dict(model_responses)
        self.requests = []

    @property
    def base_url(self):
        return "http://fake-openai.invalid/v1"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def __call__(self, request, timeout=None):
        path = urllib.parse.urlsplit(request.full_url).path
        headers = dict(request.header_items())
        payload = (
            json.loads(request.data.decode("utf-8"))
            if request.data is not None else None)
        self.requests.append(
            (request.method, path, payload, headers, timeout))
        if request.method == "GET" and path == "/v1/models":
            return FakeHTTPResponse(json.dumps({
                "data": [{"id": name} for name in self.models],
            }).encode("utf-8"))
        if request.method == "POST" and path == "/v1/chat/completions":
            response = self.model_responses[payload["model"]]
            if isinstance(response, tuple):
                status, value = response
                body = io.BytesIO(json.dumps(value).encode("utf-8"))
                raise urllib.error.HTTPError(
                    request.full_url, status, "fake error", {}, body)
            return FakeHTTPResponse(json.dumps({
                "choices": [{"message": {
                    "content": (
                        response if isinstance(response, str)
                        else json.dumps(response)),
                }}],
            }).encode("utf-8"))
        body = io.BytesIO(json.dumps({
            "error": {"message": "missing endpoint"},
        }).encode("utf-8"))
        raise urllib.error.HTTPError(
            request.full_url, 404, "missing endpoint", {}, body)

    def evaluator(self, settings):
        return VISION.VisualCheckEvaluator(
            settings,
            transport=VISION.OpenAICompatibleHTTP(
                settings, requester=self))


class FailingTransport:
    def __init__(self, category, message):
        self.category = category
        self.message = message
        self.calls = []

    def request_json(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        raise VISION.TransportFailure(self.category, self.message)


class VisualEvaluatorTest(unittest.TestCase):
    def test_disabled_is_default_and_never_contacts_transport(self):
        settings = VISION.VisualCheckSettings()
        transport = FailingTransport("service_unavailable", "not called")
        evaluator = VISION.VisualCheckEvaluator(
            settings, transport=transport)

        result = evaluator.evaluate(
            b"BMframe", "image/bmp", {"label": "home", "page": "HOME"})

        self.assertEqual(result["status"], "disabled")
        self.assertFalse(result["strict_failure"])
        self.assertEqual(result["models"], [])
        self.assertEqual(transport.calls, [])

    def test_one_model_gets_image_expectation_and_comparable_result(self):
        with FakeOpenAIEndpoint(
                ("vision-a",),
                {"vision-a": verdict("warn")}) as server:
            settings = VISION.VisualCheckSettings(
                enabled=True, base_url=server.base_url,
                model="vision-a", api_key="local-secret",
                timeout=5, mode="advisory")
            evaluator = server.evaluator(settings)

            result = evaluator.evaluate(
                b"BMframe-data", "image/bmp",
                {"label": "Main menu", "page": "MAIN_MENU",
                 "expectation": {
                     "description": "Main menu",
                     "required": ["menu choices"],
                     "forbidden": ["overlap"],
                     "allowed_variations": ["colors"],
                 }})

            self.assertEqual(result["status"], "warning")
            self.assertFalse(result["strict_failure"])
            self.assertEqual(
                [item["model"] for item in result["models"]],
                ["vision-a"])
            self.assertEqual(
                [item["verdict"] for item in result["models"]],
                ["warn"])
            self.assertTrue(all(
                item["json_validation"]["status"] == "valid"
                for item in result["models"]))
            self.assertTrue(all(
                item["elapsed_seconds"] >= 0
                for item in result["models"]))
            self.assertEqual(
                result["models"][0]["reasons"][0]["check_id"],
                VISION.CHECKLIST[0]["id"])

            posts = [
                item for item in server.requests if item[0] == "POST"]
            self.assertEqual(
                [item[2]["model"] for item in posts],
                ["vision-a"])
            image = posts[0][2]["messages"][1]["content"][1]["image_url"][
                "url"]
            self.assertTrue(image.startswith("data:image/bmp;base64,"))
            self.assertEqual(
                base64.b64decode(image.split(",", 1)[1]), b"BMframe-data")
            prompt = json.loads(
                posts[0][2]["messages"][1]["content"][0]["text"])
            self.assertEqual(
                prompt["textual_expectation"]["required"], ["menu choices"])
            response_format = posts[0][2]["response_format"]
            self.assertEqual(response_format["type"], "json_schema")
            self.assertTrue(response_format["json_schema"]["strict"])
            self.assertEqual(
                posts[0][3]["Authorization"], "Bearer local-secret")

            artifact_text = json.dumps(evaluator.artifact([result]))
            self.assertNotIn("local-secret", artifact_text)
            self.assertNotIn(server.base_url, artifact_text)
            self.assertEqual(
                evaluator.summary([result])["models"],
                ["vision-a"])

            second = evaluator.evaluate(
                b"BMsecond", "image/bmp",
                {"label": "Settings", "page": "SETTINGS"})
            self.assertTrue(second["preflight"]["cached"])
            self.assertEqual(
                sum(1 for item in server.requests if item[0] == "GET"), 1)

    def test_more_than_one_model_is_rejected(self):
        with self.assertRaisesRegex(
                VISION.VisualCheckConfigurationError, "exactly one"):
            VISION.VisualCheckSettings(
                enabled=True, base_url="http://local.invalid/v1",
                model=("vision-a", "vision-b"))

    def test_single_model_keeps_the_same_simple_result_shape(self):
        with FakeOpenAIEndpoint(
                ("only-model",),
                {"only-model": verdict("pass")}) as server:
            settings = VISION.VisualCheckSettings(
                True, server.base_url, ("only-model",), "", 5,
                "advisory")
            evaluator = server.evaluator(settings)
            result = evaluator.evaluate(
                b"image", "image/bmp", {"label": "Home", "page": "HOME"})

        self.assertEqual(result["status"], "passed")
        self.assertEqual(len(result["models"]), 1)
        self.assertEqual(result["models"][0]["verdict"], "pass")

    def test_unavailable_service_and_absent_model_are_distinct(self):
        settings = VISION.VisualCheckSettings(
            True, "http://local.invalid/v1", ("vision-a",), "", 5,
            "advisory")
        transport = FailingTransport(
            "service_unavailable", "connection refused")
        unavailable = VISION.VisualCheckEvaluator(
            settings, transport=transport).evaluate(
                b"image", "image/bmp", {})
        self.assertEqual(
            unavailable["models"][0]["status"], "service_unavailable")
        self.assertEqual(
            unavailable["models"][0]["json_validation"]["status"], "not_run")

        with FakeOpenAIEndpoint(("other-model",), {}) as server:
            settings = VISION.VisualCheckSettings(
                True, server.base_url, ("vision-a",), "", 5,
                "advisory")
            absent = server.evaluator(settings).evaluate(
                b"image", "image/bmp", {})
        self.assertEqual(
            absent["models"][0]["status"], "model_unavailable")

    def test_vision_rejection_and_invalid_json_are_mapped(self):
        rejection = (400, {"error": {
            "message": "This model does not support image input"}})
        with FakeOpenAIEndpoint(
                ("no-vision",), {"no-vision": rejection}) as server:
            settings = VISION.VisualCheckSettings(
                True, server.base_url, "no-vision", "", 5, "advisory")
            rejected = server.evaluator(settings).evaluate(
                b"image", "image/bmp", {})
        with FakeOpenAIEndpoint(
                ("bad-json",), {"bad-json": "not-json"}) as server:
            settings = VISION.VisualCheckSettings(
                True, server.base_url, "bad-json", "", 5, "advisory")
            invalid = server.evaluator(settings).evaluate(
                b"image", "image/bmp", {})

        self.assertEqual(
            rejected["models"][0]["status"], "vision_unsupported")
        self.assertEqual(
            invalid["models"][0]["status"], "invalid_response")
        self.assertEqual(
            invalid["models"][0]["json_validation"]["status"], "invalid")
        self.assertIsNotNone(
            invalid["models"][0]["json_validation"]["error"])

    def test_strict_is_explicit_and_turns_any_non_pass_into_failure(self):
        with FakeOpenAIEndpoint(
                ("vision-a",),
                {"vision-a": verdict("warn")}) as server:
            advisory_settings = VISION.VisualCheckSettings(
                True, server.base_url, ("vision-a",), "", 5,
                "advisory")
            strict_settings = VISION.VisualCheckSettings(
                True, server.base_url, ("vision-a",), "", 5,
                "strict")
            advisory = server.evaluator(advisory_settings).evaluate(
                b"image", "image/bmp", {})
            strict = server.evaluator(strict_settings).evaluate(
                b"image", "image/bmp", {})

        self.assertEqual(advisory["status"], "warning")
        self.assertFalse(advisory["strict_failure"])
        self.assertEqual(strict["status"], "failed")
        self.assertTrue(strict["strict_failure"])

    def test_parity_payload_contains_two_images_and_explicit_roles(self):
        with FakeOpenAIEndpoint(
                ("vision-a",), {"vision-a": verdict("pass")}) as server:
            settings = VISION.VisualCheckSettings(
                True, server.base_url, "vision-a", "", 5, "advisory")
            server.evaluator(settings).evaluate(
                b"designer", "image/png", {
                    "label": "Move parity",
                    "_comparison_image": (b"printer", "image/png"),
                })
            post = next(
                item for item in server.requests if item[0] == "POST")

        content = post[2]["messages"][1]["content"]
        self.assertEqual(
            [item["type"] for item in content],
            ["text", "image_url", "image_url"])
        task = json.loads(content[0]["text"])
        self.assertEqual(
            task["image_roles"],
            ["primary frame", "comparison frame from the other renderer"])
        self.assertEqual(
            base64.b64decode(
                content[2]["image_url"]["url"].split(",", 1)[1]),
            b"printer")

    def test_control_defect_corpus_keeps_each_failure_separate(self):
        names = ("blank", "clipping", "overlap", "missing-element",
                 "designer-printer-mismatch")
        with FakeOpenAIEndpoint(
                ("vision-a",), {"vision-a": verdict("fail")}) as server:
            evaluator = server.evaluator(VISION.VisualCheckSettings(
                True, server.base_url, "vision-a", "", 5, "advisory"))
            results = [
                evaluator.evaluate(
                    ("image-" + name).encode("ascii"), "image/png",
                    {"label": name})
                for name in names
            ]

        self.assertEqual(len(results), len(names))
        self.assertTrue(all(
            item["models"][0]["verdict"] == "fail" for item in results))
        self.assertEqual(
            sum(1 for item in server.requests if item[0] == "POST"),
            len(names))

    def test_verdict_validator_rejects_extra_fields_and_wrong_severity(self):
        extra = verdict("pass")
        extra["extra"] = True
        with self.assertRaisesRegex(ValueError, "only verdict"):
            VISION.validate_verdict(extra)

        mismatch = verdict("warn")
        mismatch["verdict"] = "pass"
        with self.assertRaisesRegex(ValueError, "most severe"):
            VISION.validate_verdict(mismatch)


class HostPipelineTest(unittest.TestCase):
    def _artifact_input(self, directory):
        screenshot = directory / "001-home.bmp"
        pixel = b"\x10\x20\x30\xff"
        screenshot.write_bytes(
            b"BM"
            + struct.pack("<IHHI", 54 + len(pixel), 0, 0, 54)
            + struct.pack(
                "<IiiHHIIiiII", 40, 1, -1, 1, 32, 0, len(pixel),
                2835, 2835, 0, 0)
            + pixel)
        (directory / "manifest.json").write_text(json.dumps([{
            "number": 1, "label": "Home", "page": "IDLE_HOME",
            "file": screenshot.name,
        }]), encoding="utf-8")
        return PIPELINE.discover_images([directory])

    def test_advisory_persists_warning_without_failing_pipeline(self):
        with tempfile.TemporaryDirectory() as temporary, \
                FakeOpenAIEndpoint(
                    ("vision-a",),
                    {"vision-a": verdict("fail")}) as server:
            directory = pathlib.Path(temporary)
            images = self._artifact_input(directory)
            settings = VISION.VisualCheckSettings(
                True, server.base_url, ("vision-a",), "", 5,
                "advisory")
            evaluator = server.evaluator(settings)
            artifact = PIPELINE.run_checks(
                evaluator.settings, images, evaluator=evaluator)
            output = directory / "visual-checks.json"
            PIPELINE.write_artifact(output, artifact)

            self.assertEqual(artifact["status"], "warning")
            model = artifact["screenshots"][0]["models"][0]
            self.assertEqual(model["verdict"], "fail")
            self.assertEqual(model["json_validation"]["status"], "valid")
            self.assertEqual(
                artifact["screenshots"][0]["screenshot"]["page"],
                "IDLE_HOME")
            screenshot_record = artifact["screenshots"][0]["screenshot"]
            self.assertEqual(
                screenshot_record["input_mime_type"], "image/bmp")
            self.assertEqual(
                screenshot_record["submitted_mime_type"], "image/png")
            post = next(
                item for item in server.requests if item[0] == "POST")
            image_url = post[2]["messages"][1]["content"][1][
                "image_url"]["url"]
            self.assertTrue(image_url.startswith("data:image/png;base64,"))
            self.assertTrue(
                base64.b64decode(image_url.split(",", 1)[1]).startswith(
                    b"\x89PNG\r\n\x1a\n"))
            self.assertTrue(output.is_file())

    def test_strict_marks_pipeline_failed_after_persistable_result(self):
        with tempfile.TemporaryDirectory() as temporary, \
                FakeOpenAIEndpoint(
                    ("vision-a",),
                    {"vision-a": verdict("warn")}) as server:
            directory = pathlib.Path(temporary)
            images = self._artifact_input(directory)
            settings = VISION.VisualCheckSettings(
                True, server.base_url, ("vision-a",), "", 5,
                "strict")
            evaluator = server.evaluator(settings)
            artifact = PIPELINE.run_checks(
                evaluator.settings, images, evaluator=evaluator)

            self.assertEqual(artifact["status"], "failed")
            self.assertTrue(
                artifact["screenshots"][0]["strict_failure"])

    def test_disabled_pipeline_writes_comparable_disabled_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            images = self._artifact_input(directory)
            transport = FailingTransport(
                "service_unavailable", "must not be called")
            settings = VISION.VisualCheckSettings()
            evaluator = VISION.VisualCheckEvaluator(
                settings, transport=transport)

            artifact = PIPELINE.run_checks(
                settings, images, evaluator=evaluator)

            self.assertEqual(artifact["status"], "disabled")
            self.assertEqual(
                artifact["screenshots"][0]["status"], "disabled")
            self.assertEqual(transport.calls, [])

    def test_cli_requires_explicit_enable_flag(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            args = PIPELINE._arguments(["saved-artifacts"])

        self.assertFalse(args.enable)
        self.assertEqual(args.mode, "advisory")

    def test_disabled_cli_writes_artifact_without_request_settings(self):
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch("builtins.print"):
            directory = pathlib.Path(temporary)
            self._artifact_input(directory)
            output = directory / "disabled-visual-checks.json"

            code = PIPELINE.main([
                str(directory), "--output", str(output)])

            self.assertEqual(code, 0)
            artifact = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(artifact["status"], "disabled")

    def test_checker_is_absent_from_printer_runtime_and_sync_archive(self):
        screen = (
            ROOT / ".py" / "klipper" / "plugins" / "feather_screen.py"
        ).read_text(encoding="utf-8")
        runner = (
            ROOT / ".py" / "klipper" / "plugins" /
            "feather_feature_ui_test.py"
        ).read_text(encoding="utf-8")
        sync = (ROOT / "sync.sh").read_text(encoding="utf-8")

        self.assertNotIn("visual_checks", screen)
        self.assertNotIn("openai_compatible", screen)
        self.assertNotIn("visual_checks", runner)
        self.assertNotIn("openai_compatible", runner)
        self.assertIn('"./tests/"', sync)


class HybridCompositionTest(unittest.TestCase):
    def test_discovery_creates_defaults_without_page_registry(self):
        discovery = {"pages": [
            {"id": "ui.Pages.BETA", "title": "Beta"},
            {"id": "ui.Pages.ALPHA", "title": "Alpha"},
        ]}
        cases = HYBRID.build_designer_cases(discovery, [{
            "id": "alpha-warning",
            "label": "Alpha warning",
            "page": "ui.Pages.ALPHA",
            "state": {"ui.State.WARNING": True},
            "actions": [],
        }])

        self.assertEqual(
            [item["id"] for item in cases],
            ["default-alpha", "default-beta", "alpha-warning"])
        self.assertEqual(
            {item["semantic_page_id"] for item in cases},
            {"ui.Pages.ALPHA", "ui.Pages.BETA"})

    def test_new_page_without_baseline_is_never_silently_skipped(self):
        record = {
            "case_id": "default-new-page",
            "label": "New page default",
            "semantic_page_id": "ui.Pages.NEW",
            "source": "designer",
            "path": pathlib.Path("/tmp/not-opened.png"),
        }
        ready, missing = HYBRID.attach_expectations([record], {})

        self.assertEqual(ready, [])
        self.assertEqual(
            [item["case_id"] for item in missing],
            ["default-new-page"])

    def test_hybrid_replaces_only_matching_semantic_frames(self):
        designer = [{
            "case_id": "default-alpha",
            "label": "Alpha",
            "semantic_page_id": "ui.Pages.ALPHA",
            "source": "designer",
            "path": pathlib.Path("/tmp/designer.png"),
        }]
        matching = {
            "case_id": "printer-alpha",
            "label": "ui-alpha",
            "semantic_page_id": "ui.Pages.ALPHA",
            "source": "printer",
            "path": pathlib.Path("/tmp/printer-alpha.bmp"),
        }
        legacy = {
            "case_id": "printer-home",
            "label": "ui-home",
            "semantic_page_id": None,
            "source": "printer",
            "path": pathlib.Path("/tmp/printer-home.bmp"),
        }
        unknown = {
            "case_id": "printer-gamma",
            "label": "ui-gamma",
            "semantic_page_id": "ui.Pages.GAMMA",
            "source": "printer",
            "path": pathlib.Path("/tmp/printer-gamma.bmp"),
        }

        hybrid = HYBRID.merge_hybrid(
            designer, [matching, legacy, unknown])
        parity = HYBRID.merge_hybrid(
            designer, [matching, legacy, unknown], parity=True)

        self.assertEqual(hybrid["replaced"], [matching])
        self.assertEqual(hybrid["legacy"], [legacy, unknown])
        self.assertEqual(
            [item["source"] for item in hybrid["records"]],
            ["designer", "printer", "printer"])
        self.assertEqual(len(parity["pairs"]), 1)
        self.assertEqual(
            parity["pairs"][0]["comparison_path"], matching["path"])

    def test_manifest_preserves_semantic_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            (directory / "frame.png").write_bytes(b"not-decoded")
            (directory / "manifest.json").write_text(json.dumps([{
                "file": "frame.png",
                "label": "component",
                "semantic_page_id": "ui.Pages.COMPONENT",
            }]), encoding="utf-8")

            records = HYBRID.load_manifest(directory)

        self.assertEqual(
            records[0]["semantic_page_id"], "ui.Pages.COMPONENT")

    def test_incomplete_printer_suite_is_an_infrastructure_failure(self):
        with self.assertRaisesRegex(
                HYBRID.RegressionConfigurationError, "incomplete"):
            HYBRID.validate_printer_coverage(
                [{"label": "baseline"}], [], ["default-alpha"])
        with self.assertRaisesRegex(
                HYBRID.RegressionConfigurationError, "component.*incomplete"):
            HYBRID.validate_printer_coverage(
                [{"label": label} for label in HYBRID.UI_SUITE_LABELS],
                [{"case_id": "default-alpha"}],
                ["default-alpha", "alpha-warning"],
                require_component=True)

    def test_mismatched_or_missing_fingerprint_stops_before_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            (directory / "environment.json").write_text(json.dumps({
                "suite": "UI", "ui_fingerprint": "different",
            }), encoding="utf-8")
            with self.assertRaisesRegex(
                    HYBRID.RegressionConfigurationError, "does not match"):
                HYBRID.verify_artifact_fingerprint(
                    directory, "expected")
            (directory / "environment.json").write_text(
                '{"suite":"UI"}', encoding="utf-8")
            with self.assertRaisesRegex(
                    HYBRID.RegressionConfigurationError, "no UI fingerprint"):
                HYBRID.verify_artifact_fingerprint(
                    directory, "expected")

    def test_report_comparison_is_read_only_and_single_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            report = directory / "report.json"
            report.write_text(json.dumps({
                "status": "review",
                "configuration": {"model": "vision-a"},
                "screenshots": [
                    {"models": [{
                        "model": "vision-a",
                        "verdict": "pass",
                        "elapsed_seconds": 1.0,
                        "json_validation": {"status": "valid"},
                        "error": None,
                    }]},
                    {"models": [{
                        "model": "vision-a",
                        "verdict": "warn",
                        "elapsed_seconds": 3.0,
                        "json_validation": {"status": "valid"},
                        "error": None,
                    }]},
                ],
            }), encoding="utf-8")

            value = COMPARE.compare([report])["reports"][0]

        self.assertEqual(value["model"], "vision-a")
        self.assertEqual(value["json_valid_rate"], 1.0)
        self.assertEqual(value["review_rate"], 0.5)
        self.assertEqual(value["mean_elapsed_seconds"], 2.0)


class PrinterCollectorSafetyTest(unittest.TestCase):
    @staticmethod
    def _response(value):
        return FakeHTTPResponse(json.dumps(value).encode("utf-8"))

    def test_live_collection_requires_explicit_idle_confirmation(self):
        collector = PRINTER.PrinterCollector(
            "printer.invalid", confirmed_idle=False,
            requester=lambda *_args, **_kwargs: self._response({}))
        with self.assertRaisesRegex(
                PRINTER.PrinterCollectionError, "confirm-printer-idle"):
            collector.preflight()

    def test_preflight_rejects_printing_and_heater_targets(self):
        def requester(_request, timeout=None):
            del timeout
            return self._response({"result": {"status": {
                "print_stats": {"state": "printing"},
                "extruder": {"target": 210},
                "heater_bed": {"target": 0},
                "virtual_sdcard": {"is_active": False},
            }}})

        collector = PRINTER.PrinterCollector(
            "printer.invalid", confirmed_idle=True, requester=requester)
        with self.assertRaisesRegex(
                PRINTER.PrinterCollectionError, "print is active"):
            collector.preflight()


class RegressionOrchestratorTest(unittest.TestCase):
    def test_offline_parity_uses_fake_printer_artifacts_without_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            frame = root / "frame.png"
            frame.write_bytes(b"\x89PNG\r\n\x1a\nfake")
            page_id = "ui.Pages.ALPHA"
            designer = [
                {
                    "case_id": "default-alpha",
                    "label": "Alpha default",
                    "page": "Alpha",
                    "semantic_page_id": page_id,
                    "source": "designer",
                    "path": frame,
                },
                {
                    "case_id": "alpha-warning",
                    "label": "Alpha warning",
                    "page": "Alpha",
                    "semantic_page_id": page_id,
                    "source": "designer",
                    "path": frame,
                },
            ]
            fingerprint = HYBRID.ui_fingerprint(ROOT)
            ui = root / "ui"
            component = root / "component"
            ui.mkdir()
            component.mkdir()
            (ui / "frame.png").write_bytes(frame.read_bytes())
            (component / "frame.png").write_bytes(frame.read_bytes())
            (ui / "environment.json").write_text(json.dumps({
                "suite": "UI", "ui_fingerprint": fingerprint,
            }), encoding="utf-8")
            (component / "environment.json").write_text(json.dumps({
                "suite": "COMPONENT", "ui_fingerprint": fingerprint,
            }), encoding="utf-8")
            ui_manifest = []
            for label in sorted(HYBRID.UI_SUITE_LABELS):
                item = {"label": label, "file": "frame.png"}
                if label == "ui-move":
                    item.update({
                        "case_id": "printer-move",
                        "semantic_page_id": page_id,
                    })
                ui_manifest.append(item)
            (ui / "manifest.json").write_text(
                json.dumps(ui_manifest), encoding="utf-8")
            (component / "manifest.json").write_text(json.dumps([
                {
                    "case_id": "default-alpha",
                    "label": "component-default-alpha",
                    "semantic_page_id": page_id,
                    "file": "frame.png",
                },
                {
                    "case_id": "alpha-warning",
                    "label": "component-alpha-warning",
                    "semantic_page_id": page_id,
                    "file": "frame.png",
                },
            ]), encoding="utf-8")
            scenarios = root / "scenarios.json"
            scenarios.write_text(json.dumps({
                "schema_version": 1,
                "cases": [{
                    "id": "alpha-warning",
                    "label": "Alpha warning",
                    "page": page_id,
                    "state": {"ui.State.WARNING": True},
                }],
            }), encoding="utf-8")
            expectation = {
                "description": "Complete readable test frame.",
                "required": ["visible content"],
                "forbidden": ["blank frame"],
                "allowed_variations": ["colors"],
            }
            expected_cases = {
                "default-alpha": expectation,
                "alpha-warning": expectation,
            }
            expected_cases.update({
                "printer:" + label: expectation
                for label in HYBRID.UI_SUITE_LABELS
            })
            expectations = root / "expectations.json"
            expectations.write_text(json.dumps({
                "schema_version": 1, "cases": expected_cases,
            }), encoding="utf-8")
            output = root / "output"
            args = REGRESSION._arguments([
                "--mode", "parity",
                "--designer-root", str(root / "fake-designer"),
                "--printer-artifacts", str(ui),
                "--printer-artifacts", str(component),
                "--scenarios", str(scenarios),
                "--expectations", str(expectations),
                "--output", str(output),
            ])

            with mock.patch.object(
                    REGRESSION.hybrid, "discover_designer",
                    return_value={"status": "ok", "pages": [{
                        "id": page_id, "title": "Alpha",
                    }]}), mock.patch.object(
                        REGRESSION.hybrid.DesignerCapture, "capture",
                        return_value=designer):
                report, _output = REGRESSION.execute(args)

        self.assertEqual(report["status"], "disabled")
        self.assertEqual(report["coverage"]["designer"], 2)
        self.assertEqual(report["coverage"]["legacy_printer"], 17)
        self.assertEqual(report["coverage"]["replaced"], 1)
        self.assertEqual(report["coverage"]["parity_pairs"], 2)


if __name__ == "__main__":
    unittest.main()
