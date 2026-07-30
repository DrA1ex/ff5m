## Isolated LM Studio lifecycle benchmark for saved FF5M UI regressions.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Isolated LM Studio lifecycle benchmark for saved FF5M UI regressions.

This host-side development command may manage already downloaded local models.
It never downloads a model and is not imported by printer or product runtime.
"""

import argparse
import datetime
import json
import math
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from tests.visual_checks import compare_reports


_PARAMETERS = re.compile(r"^\s*(\d+(?:\.\d+)?)B(?:\b|[-_])",
                         re.IGNORECASE)
_MEMORY = re.compile(
    r"Estimated Total Memory:\s*(\d+(?:\.\d+)?)\s*(GiB|MiB|GB|MB)\b",
    re.IGNORECASE)
ROOT = pathlib.Path(__file__).parents[2]
DEFAULT_ENV_FILE = ROOT / ".env"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class BenchmarkIsolationError(RuntimeError):
    pass


class LMStudioManagementError(RuntimeError):
    pass


def parse_parameter_billions(value):
    if not isinstance(value, str):
        return None
    match = _PARAMETERS.match(value)
    if match is None:
        return None
    amount = float(match.group(1))
    return amount if amount > 0 else None


def parse_estimated_memory(value):
    match = _MEMORY.search(str(value or ""))
    if match is None:
        return None
    amount = float(match.group(1))
    scale = 1024 ** 3 if match.group(2).upper().startswith("G") else 1024 ** 2
    return round(amount * scale)


def eligible_models(catalog, max_billions=12.0):
    if not isinstance(catalog, dict) or not isinstance(
            catalog.get("models"), list):
        raise ValueError("LM Studio catalog must contain a models array")
    maximum = float(max_billions)
    if not math.isfinite(maximum):
        raise ValueError("maximum parameter count must be finite")
    if maximum <= 0:
        raise ValueError("maximum parameter count must be positive")
    if maximum > 12.0:
        raise ValueError("maximum parameter count must be at most 12B")
    result = []
    for item in catalog["models"]:
        if not isinstance(item, dict) or item.get("type") != "llm":
            continue
        capabilities = item.get("capabilities")
        if not isinstance(capabilities, dict) or not capabilities.get("vision"):
            continue
        key = item.get("key")
        size = item.get("size_bytes")
        parameters = parse_parameter_billions(item.get("params_string"))
        if (not isinstance(key, str) or not key.strip()
                or not isinstance(size, int) or size <= 0
                or parameters is None or parameters > maximum):
            continue
        model = dict(item)
        model["key"] = key.strip()
        model["parameter_billions"] = parameters
        result.append(model)
    return sorted(result, key=lambda item: item["key"])


def unload_selected_instances(manager, models):
    for model in models:
        for instance in model.get("loaded_instances", ()):
            instance_id = instance.get("id")
            if not isinstance(instance_id, str) or not instance_id:
                raise BenchmarkIsolationError(
                    "selected model has an invalid loaded instance")
            manager.unload(instance_id)
            if manager.is_loaded(instance_id):
                raise BenchmarkIsolationError(
                    "selected model instance remained loaded after preflight")


class BenchmarkRunner:
    def __init__(self, manager, regression_runner, clock=None,
                 memory_estimator=None, redactor=None):
        self.manager = manager
        self.regression_runner = regression_runner
        self.clock = clock or time.monotonic
        self.memory_estimator = memory_estimator
        self.redactor = redactor or (
            lambda value: " ".join(str(value).split())[:300])

    def run(self, models, output):
        records = []
        for index, model in enumerate(models, 1):
            started = self.clock()
            instance_id = None
            record = {
                "model": model["key"],
                "params_string": model.get("params_string"),
                "parameter_billions": model.get("parameter_billions"),
                "model_size_bytes": model.get("size_bytes"),
                "estimated_memory_bytes": None,
                "load_time_seconds": None,
                "load_wall_time_seconds": None,
                "wall_time_seconds": None,
                "status": "error",
                "returncode": None,
                "report": None,
                "summary": None,
                "error": None,
                "unload_verified": False,
            }
            load_attempted = False
            try:
                if self.memory_estimator is not None:
                    record["estimated_memory_bytes"] = (
                        self.memory_estimator(model))
                load_attempted = True
                load_started = self.clock()
                try:
                    loaded = self.manager.load(model)
                finally:
                    record["load_wall_time_seconds"] = round(
                        max(0.0, self.clock() - load_started), 6)
                instance_id = loaded["instance_id"]
                record["load_time_seconds"] = loaded.get(
                    "load_time_seconds")
                run_model = dict(model)
                run_model["inference_model"] = instance_id
                regression = self.regression_runner(
                    run_model, output / ("%02d-%s" % (
                        index, _safe_name(model["key"]))))
                record.update({
                    "returncode": regression.get("returncode"),
                    "report": regression.get("report"),
                    "summary": regression.get("summary"),
                    "status": regression.get("status", "completed"),
                    "error": _redact_error(
                        regression.get("error"), self.redactor),
                })
            except Exception as exc:
                record["error"] = {
                    "category": type(exc).__name__,
                    "message": self.redactor(exc),
                }
            finally:
                if load_attempted and instance_id is None:
                    for leaked_id in self.manager.loaded_instance_ids(model):
                        self.manager.unload(leaked_id)
                        if self.manager.is_loaded(leaked_id):
                            raise BenchmarkIsolationError(
                                "ambiguous model load remained active")
                if instance_id is not None:
                    self.manager.unload(instance_id)
                    record["unload_verified"] = not self.manager.is_loaded(
                        instance_id)
                    if not record["unload_verified"]:
                        raise BenchmarkIsolationError(
                            "model instance remained loaded after unload")
                record["wall_time_seconds"] = round(
                    max(0.0, self.clock() - started), 6)
                records.append(record)
        return {
            "schema_version": 1,
            "status": (
                "completed"
                if all(item["status"] == "completed" for item in records)
                else "completed_with_errors"),
            "models": records,
        }


def _safe_name(value):
    result = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value)).strip("-")
    return result[:100] or "model"


def _redact_error(value, redactor):
    if not isinstance(value, dict):
        return None
    return {
        "category": str(value.get("category") or "error")[:120],
        "message": redactor(value.get("message") or ""),
    }


def _native_origin(openai_base_url):
    parsed = urllib.parse.urlsplit(str(openai_base_url or "").strip())
    path = parsed.path.rstrip("/")
    if (parsed.scheme not in ("http", "https") or not parsed.netloc
            or parsed.username or parsed.password
            or parsed.query or parsed.fragment or path not in ("", "/v1")):
        raise ValueError(
            "LM Studio benchmark requires an http(s) OpenAI base URL "
            "ending in /v1")
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, "", "", ""))


class NativeLMStudio:
    def __init__(self, openai_base_url, api_key="", timeout=300.0,
                 context_length=8192, requester=None):
        self.origin = _native_origin(openai_base_url)
        self.api_key = str(api_key or "")
        self.timeout = float(timeout)
        self.context_length = int(context_length)
        self.requester = requester or urllib.request.urlopen
        if not 1 <= self.context_length <= 262144:
            raise ValueError("context length must be between 1 and 262144")

    def _request(self, method, path, payload=None):
        headers = {"Accept": "application/json"}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(
                payload, separators=(",", ":"), ensure_ascii=True).encode(
                    "utf-8")
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        request = urllib.request.Request(
            self.origin + path, data=data, headers=headers, method=method)
        try:
            response = self.requester(request, timeout=self.timeout)
            with response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            try:
                raw_error = exc.read(8192)
            except Exception:
                raw_error = b""
            finally:
                exc.close()
            message = _management_error_message(raw_error, str(exc.reason))
            raise LMStudioManagementError(self.redact(message))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise LMStudioManagementError(self.redact(reason))
        if len(raw) > MAX_RESPONSE_BYTES:
            raise LMStudioManagementError(
                "LM Studio response exceeds the size limit")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise LMStudioManagementError(
                "LM Studio response is not valid JSON")
        if not isinstance(value, dict):
            raise LMStudioManagementError(
                "LM Studio response must be a JSON object")
        return value

    def catalog(self):
        return self._request("GET", "/api/v1/models")

    def load(self, model):
        selected = model.get("selected_variant") or model["key"]
        response = self._request("POST", "/api/v1/models/load", {
            "model": selected,
            "context_length": self.context_length,
            "flash_attention": True,
            "echo_load_config": True,
        })
        if response.get("status") != "loaded" or not isinstance(
                response.get("instance_id"), str):
            raise LMStudioManagementError(
                "LM Studio did not return a loaded model instance")
        return response

    def unload(self, instance_id):
        response = self._request("POST", "/api/v1/models/unload", {
            "instance_id": instance_id,
        })
        if response.get("instance_id") != instance_id:
            raise LMStudioManagementError(
                "LM Studio did not confirm the unloaded model instance")

    def is_loaded(self, instance_id):
        for model in self.catalog().get("models", ()):
            for instance in model.get("loaded_instances", ()):
                if instance.get("id") == instance_id:
                    return True
        return False

    def loaded_instance_ids(self, selected_model):
        selected_key = selected_model.get("key")
        for model in self.catalog().get("models", ()):
            if model.get("key") != selected_key:
                continue
            return [
                instance["id"]
                for instance in model.get("loaded_instances", ())
                if isinstance(instance, dict)
                and isinstance(instance.get("id"), str)
            ]
        return []

    def redact(self, value):
        text = " ".join(str(value or "").split())
        if self.api_key:
            text = text.replace(self.api_key, "<redacted>")
        if self.origin:
            text = text.replace(self.origin, "<endpoint>")
        return text[:300]


def _management_error_message(raw, fallback):
    message = fallback
    try:
        value = json.loads(raw.decode("utf-8"))
        error = value.get("error", value)
        if isinstance(error, dict):
            message = error.get("message", fallback)
        elif isinstance(error, str):
            message = error
    except Exception:
        if raw:
            message = raw.decode("utf-8", "replace")
    return " ".join(str(message).split())[:300]


def estimate_memory(model, context_length=8192):
    executable = shutil.which("lms")
    if executable is None:
        return None
    command = [
        executable, "load", model["key"],
        "--context-length", str(int(context_length)),
        "--estimate-only",
    ]
    try:
        result = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode:
        return None
    return parse_estimated_memory(result.stdout + "\n" + result.stderr)


def _load_env(path):
    path = pathlib.Path(path)
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if not name.startswith("FF5M_VISUAL_"):
            continue
        value = value.strip()
        if len(value) >= 2 and value[:1] == value[-1:] \
                and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(name, value)


def _arguments(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark downloaded LM Studio vision models on saved FF5M "
            "parity artifacts."))
    parser.add_argument("--designer-root", required=True)
    parser.add_argument(
        "--printer-artifacts", action="append", required=True)
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--base-url")
    parser.add_argument("--api-key-env", default="FF5M_VISUAL_API_KEY")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--context-length", type=int, default=8192)
    parser.add_argument("--max-params", type=float, default=12.0)
    parser.add_argument("--output")
    return parser.parse_args(argv)


def _output_directory(value):
    if value:
        return pathlib.Path(value).resolve()
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return (ROOT / "tests" / "artifacts" /
            "ui-regression-benchmarks" / stamp).resolve()


def _regression_runner(args, environment):
    def run(model, output):
        output.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable, "-m", "tests.visual_checks.regression",
            "--mode", "parity",
            "--designer-root", str(pathlib.Path(
                args.designer_root).resolve()),
            "--model", model["inference_model"],
            "--enable",
            "--check-mode", "advisory",
            "--env-file", str(pathlib.Path(args.env_file).resolve()),
            "--api-key-env", args.api_key_env,
            "--output", str(output),
        ]
        for artifact in args.printer_artifacts:
            command.extend([
                "--printer-artifacts",
                str(pathlib.Path(artifact).resolve()),
            ])
        result = subprocess.run(
            command, cwd=str(ROOT), env=environment,
            check=False, capture_output=True, text=True)
        report = output / "report.json"
        if not report.is_file():
            message = (result.stderr or result.stdout or
                       "regression did not write report.json")
            raise RuntimeError(" ".join(message.split())[:300])
        report_value = json.loads(report.read_text(encoding="utf-8"))
        infrastructure = report_value.get("infrastructure_error")
        error = None
        status = "completed"
        if result.returncode == 2 or isinstance(infrastructure, dict):
            status = "error"
            infrastructure = (
                infrastructure if isinstance(infrastructure, dict) else {})
            error = {
                "category": infrastructure.get(
                    "category", "RegressionInfrastructureError"),
                "message": infrastructure.get(
                    "message", "regression infrastructure failure"),
            }
        return {
            "status": status,
            "returncode": result.returncode,
            "report": str(report),
            "summary": compare_reports.summarize(report),
            "error": error,
        }
    return run


def _write_json(path, value):
    pathlib.Path(path).write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")


def _annotate_reports(benchmark):
    for record in benchmark["models"]:
        report_path = record.get("report")
        if not report_path or not pathlib.Path(report_path).is_file():
            continue
        path = pathlib.Path(report_path)
        report = json.loads(path.read_text(encoding="utf-8"))
        report["benchmark"] = {
            key: record.get(key)
            for key in (
                "load_time_seconds", "wall_time_seconds",
                "load_wall_time_seconds", "model_size_bytes",
                "estimated_memory_bytes",
            )
        }
        _write_json(path, report)
        record["summary"] = compare_reports.summarize(path)


def main(argv=None):
    args = _arguments(argv)
    _load_env(args.env_file)
    base_url = args.base_url or os.environ.get(
        "FF5M_VISUAL_BASE_URL", "")
    api_key = os.environ.get(args.api_key_env, "")
    manager = NativeLMStudio(
        base_url, api_key=api_key, timeout=args.timeout,
        context_length=args.context_length)
    output = _output_directory(args.output)
    output.mkdir(parents=True, exist_ok=True)
    catalog = manager.catalog()
    models = eligible_models(catalog, max_billions=args.max_params)
    if not models:
        raise LMStudioManagementError(
            "no downloaded vision models have a declared parameter count "
            "within the configured limit")
    unload_selected_instances(manager, models)
    environment = dict(os.environ)
    environment["FF5M_VISUAL_BASE_URL"] = base_url
    if api_key:
        environment[args.api_key_env] = api_key
    runner = BenchmarkRunner(
        manager, _regression_runner(args, environment),
        memory_estimator=lambda model: estimate_memory(
            model, context_length=args.context_length),
        redactor=manager.redact)
    benchmark = runner.run(models, output)
    benchmark["configuration"] = {
        "max_parameter_billions": args.max_params,
        "context_length": args.context_length,
        "models": [item["key"] for item in models],
        "api_key_configured": bool(api_key),
    }
    _annotate_reports(benchmark)
    artifact = output / "benchmark.json"
    _write_json(artifact, benchmark)
    print(
        "LM Studio vision benchmark: %s; models=%d; artifact=%s"
        % (benchmark["status"], len(models), artifact))
    return 0 if benchmark["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
