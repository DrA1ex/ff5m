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
import subprocess
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
from tests.visual_checks import designer_scenes as DESIGNER_SCENES  # noqa: E402
from tests.visual_checks import regression as REGRESSION  # noqa: E402
from tests.visual_checks import run as PIPELINE  # noqa: E402
from tests.visual_checks import html_report as HTML_REPORT  # noqa: E402
from tests.visual_checks import lmstudio_benchmark as BENCHMARK  # noqa: E402


def verdict(status="pass", evidence_class=None, reason=None):
    checks = []
    for index, item in enumerate(VISION.CHECKLIST):
        item_status = status if index == 0 else "pass"
        checks.append({
            "id": item["id"],
            "status": item_status,
            "evidence_class": (
                evidence_class or "product_semantic"
                if item_status != "pass" else "none"),
            "reason": (
                reason or "Visible overlap near the header."
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
            if isinstance(response, list):
                response = response.pop(0)
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

    def test_invalid_verdict_gets_one_corrective_retry(self):
        invalid = verdict("pass")
        invalid["checks"][0] = {
            "id": VISION.CHECKLIST[0]["id"],
            "status": "warn", "reason": "Needs review.",
        }
        with FakeOpenAIEndpoint(
                ("vision-a",),
                {"vision-a": [invalid, verdict("warn")]}) as server:
            settings = VISION.VisualCheckSettings(
                True, server.base_url, "vision-a", "", 5, "advisory")
            result = server.evaluator(settings).evaluate(
                b"image", "image/bmp", {})

        model_result = result["models"][0]
        self.assertEqual(model_result["status"], "completed")
        self.assertEqual(model_result["verdict"], "warn")
        self.assertEqual(model_result["attempts"], 2)
        posts = [item for item in server.requests if item[0] == "POST"]
        self.assertEqual(len(posts), 2)
        self.assertIn(
            "previous response violated this contract",
            posts[1][2]["messages"][0]["content"])

    def test_allowed_runtime_difference_is_retried_instead_of_reviewed(self):
        first = verdict(
            "warn", evidence_class="dynamic_runtime",
            reason="Only live footer temperatures differ.")
        with FakeOpenAIEndpoint(
                ("vision-a",),
                {"vision-a": [first, verdict("pass")]}) as server:
            settings = VISION.VisualCheckSettings(
                True, server.base_url, "vision-a", "", 5, "advisory")
            result = server.evaluator(settings).evaluate(
                b"designer", "image/png", {
                    "_comparison_image": (b"printer", "image/png"),
                })

        model = result["models"][0]
        self.assertEqual(model["verdict"], "pass")
        self.assertEqual(model["attempts"], 2)

    def test_product_semantic_difference_remains_a_valid_warning(self):
        response = verdict(
            "warn", evidence_class="product_semantic",
            reason="The selected movement step differs.")
        with FakeOpenAIEndpoint(
                ("vision-a",), {"vision-a": response}) as server:
            settings = VISION.VisualCheckSettings(
                True, server.base_url, "vision-a", "", 5, "advisory")
            result = server.evaluator(settings).evaluate(
                b"designer", "image/png", {
                    "_comparison_image": (b"printer", "image/png"),
                })

        model = result["models"][0]
        self.assertEqual(model["verdict"], "warn")
        self.assertEqual(model["attempts"], 1)
        self.assertEqual(
            model["reasons"][0]["evidence_class"], "product_semantic")

    def test_non_pass_without_product_semantic_evidence_is_retried(self):
        first = verdict(
            "warn", evidence_class="none",
            reason="No product defect was identified.")
        with FakeOpenAIEndpoint(
                ("vision-a",),
                {"vision-a": [first, verdict("pass")]}) as server:
            settings = VISION.VisualCheckSettings(
                True, server.base_url, "vision-a", "", 5, "advisory")
            result = server.evaluator(settings).evaluate(
                b"designer", "image/png", {})

        model = result["models"][0]
        self.assertEqual(model["verdict"], "pass")
        self.assertEqual(model["attempts"], 2)

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
            [
                "Designer-generated reference frame",
                "real printer Typer/framebuffer frame",
            ])
        self.assertIn(
            "live footer-only status",
            task["source_parity_allowances"][1])
        self.assertIn(
            "first image is generated by Designer",
            post[2]["messages"][0]["content"])
        self.assertIn(
            "must pass, not warn",
            post[2]["messages"][0]["content"])
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


class LMStudioBenchmarkTest(unittest.TestCase):
    def test_memory_estimate_parser_uses_binary_gigabytes(self):
        self.assertEqual(
            BENCHMARK.parse_estimated_memory(
                "Estimated GPU Memory: 4.10 GB\n"
                "Estimated Total Memory: 5.72 GB\n"),
            round(5.72 * 1024 ** 3))
        self.assertEqual(
            BENCHMARK.parse_estimated_memory(
                "Estimated Total Memory: 8.95 GiB\n"),
            round(8.95 * 1024 ** 3))
        self.assertIsNone(
            BENCHMARK.parse_estimated_memory("Estimate unavailable"))

    def test_catalog_filter_requires_declared_vision_and_at_most_12b(self):
        catalog = {
            "models": [
                {
                    "type": "llm",
                    "key": "vision-4b",
                    "params_string": "4B",
                    "size_bytes": 2_000_000_000,
                    "capabilities": {"vision": True},
                },
                {
                    "type": "llm",
                    "key": "vision-12b",
                    "params_string": "12B",
                    "size_bytes": 7_000_000_000,
                    "capabilities": {"vision": True},
                },
                {
                    "type": "llm",
                    "key": "vision-12.1b",
                    "params_string": "12.1B",
                    "size_bytes": 7_100_000_000,
                    "capabilities": {"vision": True},
                },
                {
                    "type": "llm",
                    "key": "text-7b",
                    "params_string": "7B",
                    "size_bytes": 4_000_000_000,
                    "capabilities": {"vision": False},
                },
                {
                    "type": "llm",
                    "key": "vision-unknown",
                    "params_string": None,
                    "size_bytes": 3_000_000_000,
                    "capabilities": {"vision": True},
                },
            ],
        }

        eligible = BENCHMARK.eligible_models(catalog, max_billions=12.0)

        self.assertEqual(
            [item["key"] for item in eligible],
            ["vision-12b", "vision-4b"])
        self.assertEqual(
            [item["parameter_billions"] for item in eligible],
            [12.0, 4.0])
        with self.assertRaisesRegex(ValueError, "at most 12B"):
            BENCHMARK.eligible_models(catalog, max_billions=120.0)
        with self.assertRaisesRegex(ValueError, "finite"):
            BENCHMARK.eligible_models(catalog, max_billions=float("nan"))

    def test_preflight_unloads_only_selected_model_instances(self):
        events = []

        class Manager:
            def unload(self, instance_id):
                events.append(("unload", instance_id))

            def is_loaded(self, instance_id):
                events.append(("verify_unloaded", instance_id))
                return False

        BENCHMARK.unload_selected_instances(Manager(), [
            {
                "key": "vision-a",
                "loaded_instances": [{"id": "vision-a-loaded"}],
            },
            {
                "key": "vision-b",
                "loaded_instances": [],
            },
        ])

        self.assertEqual(events, [
            ("unload", "vision-a-loaded"),
            ("verify_unloaded", "vision-a-loaded"),
        ])

    def test_failed_regression_unloads_before_the_next_model(self):
        events = []

        class Manager:
            def load(self, model):
                events.append(("load", model["key"]))
                return {
                    "instance_id": "instance-" + model["key"],
                    "load_time_seconds": 1.25,
                }

            def unload(self, instance_id):
                events.append(("unload", instance_id))

            def is_loaded(self, instance_id):
                events.append(("verify_unloaded", instance_id))
                return False

        def run_regression(model, output):
            events.append(("regression", model["key"]))
            if model["key"] == "vision-a":
                raise RuntimeError("fake inference failure")
            return {
                "returncode": 0,
                "report": str(output / "report.json"),
                "summary": {"status": "pass", "cases": 63},
            }

        runner = BENCHMARK.BenchmarkRunner(
            Manager(), run_regression, clock=iter((
                0.0, 1.0, 2.0, 5.0,
                6.0, 7.0, 9.0, 12.0,
            )).__next__)
        result = runner.run([
            {
                "key": "vision-a", "params_string": "4B",
                "parameter_billions": 4.0, "size_bytes": 2_000_000_000,
            },
            {
                "key": "vision-b", "params_string": "8B",
                "parameter_billions": 8.0, "size_bytes": 4_000_000_000,
            },
        ], pathlib.Path("/ignored/benchmark"))

        self.assertEqual(events, [
            ("load", "vision-a"),
            ("regression", "vision-a"),
            ("unload", "instance-vision-a"),
            ("verify_unloaded", "instance-vision-a"),
            ("load", "vision-b"),
            ("regression", "vision-b"),
            ("unload", "instance-vision-b"),
            ("verify_unloaded", "instance-vision-b"),
        ])
        self.assertEqual(
            [item["status"] for item in result["models"]],
            ["error", "completed"])
        self.assertEqual(
            [item["load_wall_time_seconds"] for item in result["models"]],
            [1.0, 2.0])

    def test_unload_verification_failure_stops_before_next_load(self):
        events = []

        class Manager:
            def load(self, model):
                events.append(("load", model["key"]))
                return {
                    "instance_id": "instance-" + model["key"],
                    "load_time_seconds": 0.5,
                }

            def unload(self, instance_id):
                events.append(("unload", instance_id))

            def is_loaded(self, instance_id):
                return True

        runner = BENCHMARK.BenchmarkRunner(
            Manager(),
            lambda model, output: {
                "returncode": 0,
                "report": str(output / "report.json"),
                "summary": {"status": "pass"},
            },
            clock=iter((0.0, 0.1, 0.6, 1.0)).__next__)

        with self.assertRaisesRegex(
                BENCHMARK.BenchmarkIsolationError, "remained loaded"):
            runner.run([
                {
                    "key": "vision-a", "params_string": "4B",
                    "parameter_billions": 4.0, "size_bytes": 2_000_000_000,
                },
                {
                    "key": "vision-b", "params_string": "8B",
                    "parameter_billions": 8.0, "size_bytes": 4_000_000_000,
                },
            ], pathlib.Path("/ignored/benchmark"))

        self.assertEqual(events, [
            ("load", "vision-a"),
            ("unload", "instance-vision-a"),
        ])

    def test_benchmark_redacts_exception_secrets(self):
        secret = "local-secret-value"
        endpoint = "http://127.0.0.1:1234"

        class Manager:
            def load(self, model):
                raise RuntimeError("%s failed at %s" % (secret, endpoint))

            def loaded_instance_ids(self, model):
                return []

        runner = BENCHMARK.BenchmarkRunner(
            Manager(), lambda *_args: {},
            clock=iter((0.0, 0.1, 0.5, 1.0)).__next__,
            redactor=lambda value: str(value).replace(
                secret, "<redacted>").replace(endpoint, "<endpoint>"))
        result = runner.run([{
            "key": "vision-a", "params_string": "4B",
            "parameter_billions": 4.0, "size_bytes": 2_000_000_000,
        }], pathlib.Path("/ignored/benchmark"))

        encoded = json.dumps(result)
        self.assertNotIn(secret, encoded)
        self.assertNotIn(endpoint, encoded)

    def test_regression_infrastructure_result_marks_benchmark_error(self):
        class Manager:
            def load(self, model):
                return {
                    "instance_id": "instance-a",
                    "load_time_seconds": 0.5,
                }

            def unload(self, instance_id):
                pass

            def is_loaded(self, instance_id):
                return False

        runner = BENCHMARK.BenchmarkRunner(
            Manager(),
            lambda model, output: {
                "status": "error",
                "returncode": 2,
                "report": str(output / "report.json"),
                "summary": {
                    "status": "fail", "errors": 1,
                    "infrastructure_error": True,
                },
                "error": {
                    "category": "RuntimeError",
                    "message": "Designer unavailable",
                },
            },
            clock=iter((0.0, 0.1, 0.6, 1.0)).__next__)
        result = runner.run([{
            "key": "vision-a", "params_string": "4B",
            "parameter_billions": 4.0, "size_bytes": 2_000_000_000,
        }], pathlib.Path("/ignored/benchmark"))

        self.assertEqual(result["status"], "completed_with_errors")
        self.assertEqual(result["models"][0]["status"], "error")
        self.assertEqual(result["models"][0]["returncode"], 2)
        self.assertEqual(
            result["models"][0]["error"]["category"], "RuntimeError")

    def test_ambiguous_load_failure_cleans_instance_before_next_model(self):
        events = []

        class Manager:
            def load(self, model):
                events.append(("load", model["key"]))
                if model["key"] == "vision-a":
                    raise TimeoutError("response lost after load")
                return {
                    "instance_id": "instance-b",
                    "load_time_seconds": 0.5,
                }

            def loaded_instance_ids(self, model):
                events.append(("discover", model["key"]))
                return ["leaked-a"] if model["key"] == "vision-a" else []

            def unload(self, instance_id):
                events.append(("unload", instance_id))

            def is_loaded(self, instance_id):
                events.append(("verify_unloaded", instance_id))
                return False

        def regression(model, output):
            events.append(("regression", model["key"]))
            return {
                "returncode": 0,
                "report": str(output / "report.json"),
                "summary": {"status": "pass"},
            }

        runner = BENCHMARK.BenchmarkRunner(
            Manager(), regression,
            clock=iter((
                0.0, 0.1, 0.5, 1.0,
                2.0, 2.1, 2.6, 3.0,
            )).__next__)
        result = runner.run([
            {
                "key": "vision-a", "params_string": "4B",
                "parameter_billions": 4.0, "size_bytes": 2_000_000_000,
            },
            {
                "key": "vision-b", "params_string": "8B",
                "parameter_billions": 8.0, "size_bytes": 4_000_000_000,
            },
        ], pathlib.Path("/ignored/benchmark"))

        self.assertEqual(events, [
            ("load", "vision-a"),
            ("discover", "vision-a"),
            ("unload", "leaked-a"),
            ("verify_unloaded", "leaked-a"),
            ("load", "vision-b"),
            ("regression", "vision-b"),
            ("unload", "instance-b"),
            ("verify_unloaded", "instance-b"),
        ])
        self.assertEqual(
            [item["status"] for item in result["models"]],
            ["error", "completed"])

    def test_regression_subprocess_forwards_custom_api_key_env(self):
        class Args:
            designer_root = "/designer"
            env_file = "/ignored/.env"
            printer_artifacts = ["/saved/ui", "/saved/component"]
            api_key_env = "CUSTOM_VISUAL_TOKEN"

        with tempfile.TemporaryDirectory() as temporary:
            output = pathlib.Path(temporary)

            def fake_run(command, **_kwargs):
                (output / "report.json").write_text(json.dumps({
                    "status": "pass",
                    "configuration": {"model": "vision-a"},
                    "screenshots": [],
                }), encoding="utf-8")
                return mock.Mock(returncode=0, stdout="", stderr="")

            with mock.patch.object(
                    BENCHMARK.subprocess, "run", side_effect=fake_run) as call:
                BENCHMARK._regression_runner(
                    Args(), {"CUSTOM_VISUAL_TOKEN": "secret"})({
                        "key": "vision-a",
                        "inference_model": "instance-a",
                    }, output)

        command = call.call_args.args[0]
        self.assertEqual(
            command[command.index("--api-key-env") + 1],
            "CUSTOM_VISUAL_TOKEN")
        self.assertNotIn("secret", command)

    def test_benchmark_regression_inherits_console_progress_streams(self):
        class Args:
            designer_root = "/designer"
            env_file = "/ignored/.env"
            printer_artifacts = ["/saved/ui", "/saved/component"]
            api_key_env = "FF5M_VISUAL_API_KEY"

        with tempfile.TemporaryDirectory() as temporary:
            output = pathlib.Path(temporary)

            def fake_run(_command, **kwargs):
                (output / "report.json").write_text(json.dumps({
                    "status": "pass",
                    "configuration": {"model": "vision-a"},
                    "screenshots": [],
                }), encoding="utf-8")
                return mock.Mock(returncode=0)

            with mock.patch.object(
                    BENCHMARK.subprocess, "run", side_effect=fake_run) as call:
                BENCHMARK._regression_runner(Args(), {})({
                    "key": "vision-a",
                    "inference_model": "instance-a",
                }, output)

        kwargs = call.call_args.kwargs
        self.assertNotIn("capture_output", kwargs)
        self.assertNotIn("stdout", kwargs)
        self.assertNotIn("stderr", kwargs)

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

    def test_run_checks_reports_frame_count_and_eta(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            images = self._artifact_input(directory)
            second = directory / "002-settings.bmp"
            second.write_bytes(images[0]["path"].read_bytes())
            images.append({
                "path": second,
                "context": {
                    "number": 2,
                    "label": "Settings",
                    "page": "SETTINGS",
                    "case_id": "settings",
                    "source": "designer",
                },
                "mime_type": "image/bmp",
            })
            settings = VISION.VisualCheckSettings()
            evaluator = VISION.VisualCheckEvaluator(
                settings,
                transport=FailingTransport(
                    "service_unavailable", "must not be called"))
            events = []

            PIPELINE.run_checks(
                settings, images, evaluator=evaluator,
                progress=events.append,
                clock=iter((10.0, 10.0, 12.0, 12.0, 16.0)).__next__)

        self.assertEqual(
            [(item["completed"], item["total"]) for item in events],
            [(0, 2), (1, 2), (2, 2)])
        self.assertIsNone(events[0]["eta_seconds"])
        self.assertEqual(events[1]["last_elapsed_seconds"], 2.0)
        self.assertEqual(events[1]["eta_seconds"], 2.0)
        self.assertEqual(events[2]["eta_seconds"], 0.0)
        self.assertEqual(events[2]["case_id"], "settings")

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
        remote_sync = (ROOT / "sync_remote.sh").read_text(encoding="utf-8")

        self.assertNotIn("visual_checks", screen)
        self.assertNotIn("openai_compatible", screen)
        self.assertNotIn("visual_checks", runner)
        self.assertNotIn("openai_compatible", runner)
        self.assertIn('"./tests/"', sync)
        self.assertIn('"./.env"', sync)
        self.assertIn(
            "git ls-files --others --ignored --exclude-standard", sync)
        self.assertIn('".py/klipper/plugins/ui"', remote_sync)
        self.assertIn('".py/klipper/plugins/ff5m_ui"', remote_sync)
        self.assertIn("S00init reload", remote_sync)
        self.assertIn("Removing obsolete file", remote_sync)

    def test_sync_keeps_nonignored_untracked_paths_archive_eligible(self):
        sync = (ROOT / "sync.sh").read_text(encoding="utf-8")
        with tempfile.NamedTemporaryFile(
                dir=str(ROOT), prefix="ff5m-sync-untracked-") as candidate:
            ignored = subprocess.check_output([
                "git", "ls-files", "--others", "--ignored",
                "--exclude-standard", "--directory",
            ], cwd=str(ROOT), text=True).splitlines()
            relative = pathlib.Path(candidate.name).relative_to(ROOT).as_posix()

        self.assertIn(
            "Ordinary untracked paths remain eligible", sync)
        self.assertNotIn(relative, ignored)


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
                "benchmark": {
                    "load_time_seconds": 4.5,
                    "load_wall_time_seconds": 4.8,
                    "wall_time_seconds": 130.0,
                    "model_size_bytes": 4_000_000_000,
                    "estimated_memory_bytes": 5_000_000_000,
                },
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
        self.assertEqual(value["load_time_seconds"], 4.5)
        self.assertEqual(value["load_wall_time_seconds"], 4.8)
        self.assertEqual(value["wall_time_seconds"], 130.0)
        self.assertEqual(value["model_size_bytes"], 4_000_000_000)
        self.assertEqual(value["estimated_memory_bytes"], 5_000_000_000)


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
    def test_main_streams_stage_frame_count_and_eta_to_stderr(self):
        output = io.StringIO()

        def execute(_args, output=None, progress=None):
            self.assertIsNotNone(progress)
            progress.stage(1, 6, "Preparing regression")
            progress.review({
                "completed": 3,
                "total": 10,
                "case_id": "move-ready",
                "last_elapsed_seconds": 2.25,
                "elapsed_seconds": 9.0,
                "eta_seconds": 21.0,
            })
            return {
                "status": "disabled",
                "mode": "designer",
                "coverage": {
                    "designer": 0,
                    "legacy_printer": 0,
                    "replaced": 0,
                    "parity_pairs": 0,
                },
            }, pathlib.Path(output)

        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(REGRESSION, "execute",
                                  side_effect=execute), \
                mock.patch.object(sys, "stderr", output):
            code = REGRESSION.main([
                "--mode", "designer",
                "--designer-root", str(pathlib.Path(temporary) / "designer"),
                "--output", str(pathlib.Path(temporary) / "output"),
            ])

        progress = output.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("[stage 1/6] Preparing regression", progress)
        self.assertIn("[review 3/10] move-ready", progress)
        self.assertIn("last 2.2s", progress)
        self.assertIn("elapsed 9s", progress)
        self.assertIn("ETA 21s", progress)

    def test_printer_theme_is_automatic_and_mismatches_are_rejected(self):
        runs = [{"theme": "OCEAN"}, {"theme": "OCEAN"}]
        self.assertEqual(
            REGRESSION._designer_theme(None, runs), "OCEAN")
        self.assertEqual(
            REGRESSION._designer_theme("DEFAULT", runs), "DEFAULT")
        with self.assertRaisesRegex(
                HYBRID.RegressionConfigurationError,
                "different UI themes"):
            REGRESSION._designer_theme(
                None, [{"theme": "OCEAN"}, {"theme": "DEFAULT"}])

    def test_designer_scene_rejects_silently_ignored_requested_state(self):
        case = {
            "state": {
                "ui.State.STEP": 10.0,
            },
        }
        stale_scene = {
            "state_schema": [{
                "key": "ui.State.STEP",
                "value_available": True,
                "value": 1.0,
            }],
        }

        with self.assertRaisesRegex(
                ValueError, "did not apply requested state key"):
            DESIGNER_SCENES._assert_requested_state(case, stale_scene)

    def test_html_report_contains_images_baselines_and_model_evidence(self):
        report = {
            "status": "review",
            "mode": "designer",
            "coverage": {
                "designer": 1, "legacy_printer": 0,
                "replaced": 0, "parity_pairs": 0,
            },
            "configuration": {"model": "vision-model"},
            "summary": {
                "verdicts": {"warn": 1},
            },
            "pipeline": [{
                "id": "designer",
                "status": "completed",
                "title": "Designer discovery and capture",
                "summary": "One frame rendered.",
                "counts": {"captured_frames": 1},
            }],
            "screenshots": [{
                "case_result": {
                    "verdict": "warn",
                    "json_validation": {"status": "valid"},
                    "elapsed_seconds": 1.25,
                    "reasons": [{
                        "check_id": "layout_overlap",
                        "reason": "Header needs review.",
                    }],
                },
                "models": [{
                    "attempts": 1,
                    "response": {
                        "verdict": "warn",
                        "summary": "Review <header> alignment.",
                        "checks": [{
                            "id": "layout_overlap",
                            "status": "warn",
                            "reason": "Possible overlap.",
                        }],
                    },
                }],
                "screenshot": {
                    "label": "Main <menu>",
                    "case_id": "main-menu",
                    "source": "designer",
                    "artifact": "designer/frame one.png",
                    "file": "frame one.png",
                    "expectation": {
                        "description": "Readable menu.",
                        "required": ["navigation choices"],
                        "forbidden": ["overlap"],
                        "allowed_variations": ["colors"],
                    },
                    "expectation_references": [
                        "cases.main-menu.required[0]",
                    ],
                },
            }],
        }

        page = HTML_REPORT.render(report)

        self.assertIn('src="designer/frame%20one.png"', page)
        self.assertIn("Main &lt;menu&gt;", page)
        self.assertIn("Review &lt;header&gt; alignment.", page)
        self.assertIn("Textual baseline", page)
        self.assertIn("navigation choices", page)
        self.assertIn("Model checklist (1)", page)
        self.assertIn("Run details, model evidence", page)
        self.assertIn("Screenshot overview", page)
        self.assertIn('class="shot-grid', page)
        self.assertIn('<dialog id="frame-dialog"', page)
        self.assertIn('data-detail="detail-1"', page)
        self.assertIn('data-source="designer"', page)
        self.assertIn("Real printer", page)
        self.assertIn(
            "dl{display:grid;grid-template-columns:", page)
        self.assertIn(
            ".shot-grid{display:grid;grid-template-columns:"
            "repeat(auto-fill,minmax(480px,1fr))", page)
        self.assertIn(
            ".shot-grid.pair-grid{grid-template-columns:"
            "repeat(auto-fill,minmax(720px,1fr))", page)
        self.assertNotIn("<menu>", page)

    def test_infrastructure_failure_always_writes_html_and_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = pathlib.Path(temporary) / "failed-run"
            error = HYBRID.RegressionConfigurationError(
                "deployed UI fingerprint does not match <local>")
            with mock.patch.object(
                    REGRESSION, "execute", side_effect=error):
                result = REGRESSION.main([
                    "--mode", "designer",
                    "--designer-root", str(pathlib.Path(temporary) / "designer"),
                    "--output", str(output),
                ])
            report = json.loads(
                (output / "report.json").read_text(encoding="utf-8"))
            page = (output / "report.html").read_text(encoding="utf-8")

        self.assertEqual(result, 2)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(
            report["infrastructure_error"]["category"],
            "RegressionConfigurationError")
        self.assertIn("Infrastructure failure", page)
        self.assertIn(
            "deployed UI fingerprint does not match &lt;local&gt;", page)

    def test_unexpected_runtime_failure_always_writes_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = pathlib.Path(temporary) / "failed-run"
            with mock.patch.object(
                    REGRESSION, "execute",
                    side_effect=RuntimeError("unexpected regression defect")):
                result = REGRESSION.main([
                    "--mode", "designer",
                    "--designer-root", str(pathlib.Path(temporary) / "designer"),
                    "--output", str(output),
                ])
            report = json.loads(
                (output / "report.json").read_text(encoding="utf-8"))
            page = (output / "report.html").read_text(encoding="utf-8")

        self.assertEqual(result, 2)
        self.assertEqual(
            report["infrastructure_error"]["category"], "RuntimeError")
        self.assertIn("unexpected regression defect", page)

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
                "theme": "CYBERPUNK_YELLOW",
            }), encoding="utf-8")
            (component / "environment.json").write_text(json.dumps({
                "suite": "COMPONENT", "ui_fingerprint": fingerprint,
                "theme": "CYBERPUNK_YELLOW",
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

            captured = {}

            def capture(cases, _output):
                captured["themes"] = set(
                    item["theme"] for item in cases)
                return designer

            with mock.patch.object(
                    REGRESSION.hybrid, "discover_designer",
                    return_value={"status": "ok", "pages": [{
                        "id": page_id, "title": "Alpha",
                    }]}), mock.patch.object(
                        REGRESSION.hybrid.DesignerCapture, "capture",
                        side_effect=capture):
                report, _output = REGRESSION.execute(args)
            page = (output / "report.html").read_text(encoding="utf-8")
            copied_ui = output / "printer" / "saved-01"
            copied_component = output / "printer" / "saved-02"

            self.assertEqual(report["status"], "disabled")
            self.assertEqual(report["coverage"]["designer"], 2)
            self.assertEqual(report["coverage"]["printer_captured"], 20)
            self.assertEqual(report["coverage"]["legacy_printer"], 17)
            self.assertEqual(report["coverage"]["replaced"], 1)
            self.assertEqual(report["coverage"]["parity_pairs"], 2)
            self.assertEqual(
                report["configuration"]["designer_theme"],
                "CYBERPUNK_YELLOW")
            self.assertEqual(captured["themes"], {"CYBERPUNK_YELLOW"})
            self.assertEqual(
                [item["id"] for item in report["pipeline"]],
                ["designer", "printer", "merge", "llm"])
            self.assertEqual(
                report["pipeline"][1]["counts"]["captured_frames"], 20)
            self.assertIn("Screenshot overview", page)
            self.assertIn("Designer ↔ real printer", page)
            self.assertIn(
                "<figcaption>Real printer Typer/framebuffer</figcaption>",
                page)
            self.assertIn("printer/saved-01/frame.png", page)
            self.assertIn("printer/saved-02/frame.png", page)
            self.assertTrue(copied_ui.is_dir())
            self.assertTrue(copied_component.is_dir())


if __name__ == "__main__":
    unittest.main()
