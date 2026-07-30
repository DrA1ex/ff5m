## Development-only OpenAI-compatible checks for saved UI screenshots.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Development-only OpenAI-compatible checks for saved UI screenshots.

This module is host-side test infrastructure. It must never be imported by the
printer, Feather, Klipper, or deployment runtime.
"""

import base64
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request


SCHEMA_VERSION = 1
MAX_RESPONSE_BYTES = 256 * 1024
MAX_ERROR_BYTES = 8 * 1024
MAX_REASON_LENGTH = 400
MAX_SUMMARY_LENGTH = 600
MAX_VALIDATION_ATTEMPTS = 2
VALID_MODES = frozenset(("advisory", "strict"))
VALID_VERDICTS = frozenset(("pass", "warn", "fail"))
CHECKLIST = (
    {
        "id": "frame_integrity",
        "description": "The frame is non-blank and has no obvious corruption.",
    },
    {
        "id": "text_legibility",
        "description": "Visible text is legible and is not clipped.",
    },
    {
        "id": "layout_overlap",
        "description": "Controls and text do not overlap unexpectedly.",
    },
    {
        "id": "screen_bounds",
        "description": "Important content remains inside the screen bounds.",
    },
    {
        "id": "visual_consistency",
        "description": "The screen has no obvious broken or inconsistent UI.",
    },
    {
        "id": "expected_content",
        "description": (
            "When a textual expectation is supplied, the visible screen "
            "matches its required and forbidden content."),
    },
    {
        "id": "source_parity",
        "description": (
            "When two frames are supplied, their UI structure and state are "
            "semantically equivalent despite rendering differences."),
    },
)
_CHECK_IDS = tuple(item["id"] for item in CHECKLIST)
_SEVERITY = {"pass": 0, "warn": 1, "fail": 2}
_VISION_TERMS = re.compile(
    r"(vision|image(?:_url)?|multimodal|modality|visual input|"
    r"does not support (?:images|image input)|unsupported (?:image|content))",
    re.IGNORECASE)


class VisualCheckConfigurationError(ValueError):
    pass


class TransportFailure(Exception):
    def __init__(self, category, message, status=None):
        super().__init__(message)
        self.category = str(category)
        self.message = str(message)
        self.status = status


def _safe_text(value, limit, secret=""):
    text = " ".join(str(value or "").split())
    if secret:
        text = text.replace(secret, "<redacted>")
    return text[:limit]


def _model_name(value):
    if value is None:
        values = ()
    elif isinstance(value, str):
        values = value.replace("\n", ",").split(",")
    else:
        values = value
    result = []
    for item in values:
        name = str(item).strip()
        if not name or name in result:
            continue
        if len(name) > 160:
            raise VisualCheckConfigurationError(
                "visual model names must not exceed 160 characters")
        result.append(name)
    if len(result) > 1:
        raise VisualCheckConfigurationError(
            "visual checks accept exactly one model per run")
    return result[0] if result else ""


class VisualCheckSettings:
    def __init__(self, enabled=False, base_url="", model="", api_key="",
                 timeout=30.0, mode="advisory"):
        self.enabled = bool(enabled)
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.model = _model_name(model)
        self.models = (self.model,) if self.model else ()
        self.api_key = str(api_key or "")
        self.timeout = float(timeout)
        self.mode = str(mode or "advisory").strip().lower()
        self._validate()

    @classmethod
    def from_mapping(cls, value):
        value = dict(value or {})
        return cls(
            enabled=value.get("enabled", False),
            base_url=value.get("base_url", ""),
            model=value.get("model", value.get("models", ())),
            api_key=value.get("api_key", ""),
            timeout=value.get("timeout", 30.0),
            mode=value.get("mode", "advisory"),
        )

    def _validate(self):
        if self.mode not in VALID_MODES:
            raise VisualCheckConfigurationError(
                "visual check mode must be advisory or strict")
        if not 1.0 <= self.timeout <= 300.0:
            raise VisualCheckConfigurationError(
                "visual check timeout must be between 1 and 300 seconds")
        if not self.enabled:
            return
        if not self.base_url:
            raise VisualCheckConfigurationError(
                "visual checks require an OpenAI-compatible base URL")
        parsed = urllib.parse.urlsplit(self.base_url)
        if (parsed.scheme not in ("http", "https") or not parsed.netloc
                or parsed.username or parsed.password
                or parsed.query or parsed.fragment):
            raise VisualCheckConfigurationError(
                "visual check base URL must be an http(s) URL without "
                "credentials, query, or fragment")
        if not self.model:
            raise VisualCheckConfigurationError(
                "visual checks require one explicit model name")

    def public(self):
        # Never serialize or log the API key or the endpoint address.
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "model": self.model,
            "timeout": self.timeout,
            "api_key_configured": bool(self.api_key),
        }


class OpenAICompatibleHTTP:
    def __init__(self, settings, requester=None):
        self.settings = settings
        self.requester = requester or urllib.request.urlopen

    def request_json(self, method, path, payload=None):
        url = self.settings.base_url + "/" + str(path).lstrip("/")
        headers = {"Accept": "application/json"}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(
                payload, separators=(",", ":"), ensure_ascii=True).encode(
                    "utf-8")
        if self.settings.api_key:
            headers["Authorization"] = "Bearer " + self.settings.api_key
        request = urllib.request.Request(
            url, data=data, headers=headers, method=method)
        try:
            response = self.requester(
                request, timeout=self.settings.timeout)
            with response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            try:
                raw_error = exc.read(MAX_ERROR_BYTES)
            except Exception:
                raw_error = b""
            finally:
                exc.close()
            message = self._error_message(raw_error, str(exc.reason))
            category = self._http_category(path, exc.code, message)
            raise TransportFailure(category, message, exc.code)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise TransportFailure(
                "service_unavailable",
                _safe_text(reason, 300, self.settings.api_key))
        if len(raw) > MAX_RESPONSE_BYTES:
            raise TransportFailure(
                "invalid_response", "response exceeds the size limit")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise TransportFailure(
                "invalid_response", "response is not valid JSON")
        if not isinstance(value, dict):
            raise TransportFailure(
                "invalid_response", "response JSON must be an object")
        return value

    def _error_message(self, raw, fallback):
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
        return _safe_text(message, 300, self.settings.api_key)

    @staticmethod
    def _http_category(path, status, message):
        if str(path).rstrip("/").endswith("/models"):
            return "service_unavailable"
        if status in (400, 415, 422) and _VISION_TERMS.search(message):
            return "vision_unsupported"
        if status == 404 and "model" in message.lower():
            return "model_unavailable"
        return "request_failed"


def _response_schema():
    return {
        "verdict": "pass|warn|fail",
        "checks": [
            {"id": item["id"], "status": "pass|warn|fail",
             "reason": "short factual reason"}
            for item in CHECKLIST
        ],
        "summary": "short overall summary",
    }


def _response_json_schema():
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["verdict", "checks", "summary"],
        "properties": {
            "verdict": {"type": "string", "enum": sorted(VALID_VERDICTS)},
            "checks": {
                "type": "array",
                "minItems": len(CHECKLIST),
                "maxItems": len(CHECKLIST),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "status", "reason"],
                    "properties": {
                        "id": {"type": "string", "enum": list(_CHECK_IDS)},
                        "status": {
                            "type": "string",
                            "enum": sorted(VALID_VERDICTS),
                        },
                        "reason": {
                            "type": "string",
                            "maxLength": MAX_REASON_LENGTH,
                        },
                    },
                },
            },
            "summary": {
                "type": "string",
                "maxLength": MAX_SUMMARY_LENGTH,
            },
        },
    }


def _completion_payload(model, image_bytes, mime_type, context,
                        corrective_retry=False):
    screenshot = {
        "label": _safe_text(context.get("label"), 120),
        "page": _safe_text(context.get("page"), 120),
    }
    task = {
        "task": "Evaluate only the supplied UI screenshot.",
        "screenshot": screenshot,
        "checklist": CHECKLIST,
        "response_schema": _response_schema(),
    }
    expectation = context.get("expectation")
    if isinstance(expectation, dict):
        encoded = json.dumps(
            expectation, separators=(",", ":"), ensure_ascii=True)
        if len(encoded) > 6000:
            raise VisualCheckConfigurationError(
                "textual expectation exceeds the size limit")
        task["textual_expectation"] = expectation
    comparison = context.get("_comparison_image")
    if comparison is not None:
        task["image_roles"] = [
            "primary frame", "comparison frame from the other renderer"]
    content = [
        {
            "type": "text",
            "text": json.dumps(
                task, separators=(",", ":"), ensure_ascii=True),
        },
        {
            "type": "image_url",
            "image_url": {
                "url": "data:%s;base64,%s" % (
                    mime_type,
                    base64.b64encode(image_bytes).decode("ascii")),
                "detail": "high",
            },
        },
    ]
    if comparison is not None:
        comparison_bytes, comparison_mime = comparison
        content.append({
            "type": "image_url",
            "image_url": {
                "url": "data:%s;base64,%s" % (
                    comparison_mime,
                    base64.b64encode(comparison_bytes).decode("ascii")),
                "detail": "high",
            },
        })
    system_instruction = (
        "Return exactly one JSON object and no markdown. "
        "Use only the supplied checklist. Do not infer printer "
        "safety, functionality, or behavior from the image. "
        "Use every checklist id exactly once and add no fields. "
        "The top-level verdict must equal the most severe checklist "
        "status: pass only if every check passes, warn if the worst "
        "check warns, and fail if any check fails.")
    if corrective_retry:
        system_instruction += (
            " A previous response violated this contract. Re-evaluate the "
            "same frame and obey the verdict rule exactly.")
    return {
        "model": model,
        "temperature": 0,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "ff5m_ui_visual_verdict",
                "strict": True,
                "schema": _response_json_schema(),
            },
        },
        "messages": [
            {
                "role": "system",
                "content": system_instruction,
            },
            {
                "role": "user",
                "content": content,
            },
        ],
    }


def _completion_content(response):
    try:
        choices = response["choices"]
        content = choices[0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise ValueError(
            "completion must contain choices[0].message.content")
    if not isinstance(content, str):
        raise ValueError("completion content must be a JSON string")
    try:
        return json.loads(content)
    except ValueError:
        raise ValueError("completion content is not valid JSON")


def validate_verdict(value):
    if not isinstance(value, dict):
        raise ValueError("verdict must be a JSON object")
    if set(value) != {"verdict", "checks", "summary"}:
        raise ValueError(
            "verdict must contain only verdict, checks, and summary")
    verdict = value["verdict"]
    if verdict not in VALID_VERDICTS:
        raise ValueError("invalid overall verdict")
    summary = value["summary"]
    if not isinstance(summary, str) or len(summary) > MAX_SUMMARY_LENGTH:
        raise ValueError("summary must be a short string")
    checks = value["checks"]
    if not isinstance(checks, list) or len(checks) != len(CHECKLIST):
        raise ValueError("verdict must contain every checklist item")
    normalized = []
    for expected_id, item in zip(_CHECK_IDS, checks):
        if not isinstance(item, dict) or set(item) != {
                "id", "status", "reason"}:
            raise ValueError(
                "each check must contain only id, status, and reason")
        if item["id"] != expected_id:
            raise ValueError("checklist ids must appear once in fixed order")
        status = item["status"]
        reason = item["reason"]
        if status not in VALID_VERDICTS:
            raise ValueError("invalid checklist status for %s" % expected_id)
        if (not isinstance(reason, str)
                or len(reason) > MAX_REASON_LENGTH
                or status != "pass" and not reason.strip()):
            raise ValueError("invalid checklist reason for %s" % expected_id)
        normalized.append({
            "id": expected_id, "status": status,
            "reason": reason.strip(),
        })
    derived = max(normalized, key=lambda item: _SEVERITY[item["status"]])[
        "status"]
    if verdict != derived:
        raise ValueError(
            "overall verdict must match the most severe checklist status")
    return {
        "verdict": verdict, "checks": normalized,
        "summary": summary.strip(),
    }


def _json_validation(status, error=None):
    return {
        "status": status,
        "error": _safe_text(error, 300) if error else None,
    }


def _error_result(model, category, message, elapsed=0.0,
                  json_status="not_run", attempts=1):
    return {
        "model": model,
        "status": category,
        "verdict": None,
        "reasons": [],
        "json_validation": _json_validation(
            json_status, message if json_status == "invalid" else None),
        "elapsed_seconds": round(max(0.0, elapsed), 6),
        "attempts": attempts,
        "error": {
            "category": category,
            "message": _safe_text(message, 300),
        },
        "response": None,
    }


class VisualCheckEvaluator:
    def __init__(self, settings, transport=None, clock=None):
        self.settings = settings
        self.transport = transport or OpenAICompatibleHTTP(settings)
        self.clock = clock or time.monotonic
        self._catalog = None
        self._catalog_error = None

    def _models(self):
        if self._catalog is not None or self._catalog_error is not None:
            return self._catalog, self._catalog_error, 0.0, True
        started = self.clock()
        try:
            response = self.transport.request_json("GET", "/models")
            items = response.get("data")
            if not isinstance(items, list):
                raise TransportFailure(
                    "invalid_response",
                    "models response must contain a data array")
            names = []
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    names.append(item["id"])
            self._catalog = frozenset(names)
        except TransportFailure as exc:
            self._catalog_error = exc
        elapsed = self.clock() - started
        return self._catalog, self._catalog_error, elapsed, False

    def evaluate(self, image_bytes, mime_type, context):
        if not self.settings.enabled:
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "disabled",
                "mode": self.settings.mode,
                "strict_failure": False,
                "preflight": {
                    "status": "disabled", "cached": False,
                    "elapsed_seconds": 0.0, "error": None,
                },
                "models": [],
            }
        catalog, preflight_error, preflight_elapsed, cached = self._models()
        preflight = {
            "status": (
                preflight_error.category if preflight_error else "available"),
            "cached": cached,
            "elapsed_seconds": round(max(0.0, preflight_elapsed), 6),
            "error": (
                None if preflight_error is None else {
                    "category": preflight_error.category,
                    "message": _safe_text(preflight_error.message, 300),
                }),
        }
        results = []
        if preflight_error is not None:
            for model in self.settings.models:
                results.append(_error_result(
                    model, preflight_error.category,
                    preflight_error.message))
        else:
            for model in self.settings.models:
                if model not in catalog:
                    results.append(_error_result(
                        model, "model_unavailable",
                        "configured model is absent from the endpoint catalog"))
                    continue
                results.append(self._evaluate_model(
                    model, image_bytes, mime_type, context))
        has_problem = any(
            item["status"] != "completed" or item["verdict"] != "pass"
            for item in results)
        strict_failure = self.settings.mode == "strict" and has_problem
        return {
            "schema_version": SCHEMA_VERSION,
            "status": (
                "failed" if strict_failure else
                "warning" if has_problem else "passed"),
            "mode": self.settings.mode,
            "strict_failure": strict_failure,
            "preflight": preflight,
            "models": results,
        }

    def _evaluate_model(self, model, image_bytes, mime_type, context):
        started = self.clock()
        last_validation_error = None
        for attempt in range(1, MAX_VALIDATION_ATTEMPTS + 1):
            try:
                response = self.transport.request_json(
                    "POST", "/chat/completions", _completion_payload(
                        model, image_bytes, mime_type, context,
                        corrective_retry=attempt > 1))
            except TransportFailure as exc:
                return _error_result(
                    model, exc.category, exc.message,
                    self.clock() - started, attempts=attempt)
            try:
                verdict = validate_verdict(_completion_content(response))
                break
            except ValueError as exc:
                last_validation_error = str(exc)
        else:
            return _error_result(
                model, "invalid_response", last_validation_error,
                self.clock() - started, json_status="invalid",
                attempts=MAX_VALIDATION_ATTEMPTS)
        reasons = [
            {
                "check_id": item["id"], "status": item["status"],
                "reason": item["reason"],
            }
            for item in verdict["checks"] if item["status"] != "pass"
        ]
        return {
            "model": model,
            "status": "completed",
            "verdict": verdict["verdict"],
            "reasons": reasons,
            "json_validation": _json_validation("valid"),
            "elapsed_seconds": round(
                max(0.0, self.clock() - started), 6),
            "attempts": attempt,
            "error": None,
            "response": verdict,
        }

    def artifact(self, screenshots):
        return {
            "schema_version": SCHEMA_VERSION,
            "configuration": self.settings.public(),
            "checklist": list(CHECKLIST),
            "screenshots": list(screenshots),
        }

    def summary(self, screenshots):
        frames = list(screenshots)
        models = []
        statuses = {}
        verdicts = {}
        for frame in frames:
            for result in frame.get("models", ()):
                model = result["model"]
                if model not in models:
                    models.append(model)
                statuses[result["status"]] = (
                    statuses.get(result["status"], 0) + 1)
                verdict = result.get("verdict")
                if verdict:
                    verdicts[verdict] = verdicts.get(verdict, 0) + 1
        return {
            "enabled": self.settings.enabled,
            "mode": self.settings.mode,
            "models": models,
            "screenshots": len(frames),
            "statuses": statuses,
            "verdicts": verdicts,
            "strict_failures": sum(
                1 for frame in frames if frame.get("strict_failure")),
            "artifact": "visual-checks.json",
        }
