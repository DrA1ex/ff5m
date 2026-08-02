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
STANDALONE_VERDICTS = VALID_VERDICTS - frozenset(("warn",))
EVIDENCE_CLASSES = frozenset((
    "none", "dynamic_runtime", "rendering_only", "design_mismatch",
    "aesthetic_defect", "product_semantic",
))
STANDALONE_EVIDENCE_CLASSES = EVIDENCE_CLASSES - frozenset((
    "design_mismatch",
))
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
        "id": "spacing_and_clearance",
        "description": (
            "Text and controls have deliberate clearance from headers, "
            "borders, neighbors, and related regions, with consistent "
            "internal padding and vertical rhythm."),
    },
    {
        "id": "alignment_and_balance",
        "description": (
            "Alignment, proportions, hierarchy, and whitespace form a "
            "coherent and balanced composition."),
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
            "When two frames are supplied, compare their visible geometry, "
            "presentation, structure, and state after independently checking "
            "the visual quality of each frame."),
    },
)
_CHECK_IDS = tuple(item["id"] for item in CHECKLIST)
_SEVERITY = {"pass": 0, "warn": 1, "fail": 2}
_EVIDENCE_STATUSES = {
    "none": frozenset(("pass",)),
    "dynamic_runtime": frozenset(("pass",)),
    "rendering_only": frozenset(("pass",)),
    "design_mismatch": frozenset(("warn",)),
    "aesthetic_defect": frozenset(("fail",)),
    "product_semantic": frozenset(("pass", "fail")),
}
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


def _response_schema(allow_design_mismatch=False):
    evidence_classes = (
        EVIDENCE_CLASSES if allow_design_mismatch
        else STANDALONE_EVIDENCE_CLASSES)
    verdicts = (
        VALID_VERDICTS if allow_design_mismatch
        else STANDALONE_VERDICTS)
    return {
        "verdict": "|".join(sorted(verdicts)),
        "checks": [
            {"id": item["id"], "status": "|".join(sorted(verdicts)),
             "evidence_class": "|".join(sorted(evidence_classes)),
             "reason": "short factual reason"}
            for item in CHECKLIST
        ],
        "summary": "short overall summary",
    }


def _response_json_schema(allow_design_mismatch=False):
    evidence_classes = (
        EVIDENCE_CLASSES if allow_design_mismatch
        else STANDALONE_EVIDENCE_CLASSES)
    verdicts = (
        VALID_VERDICTS if allow_design_mismatch
        else STANDALONE_VERDICTS)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["verdict", "checks", "summary"],
        "properties": {
            "verdict": {"type": "string", "enum": sorted(verdicts)},
            "checks": {
                "type": "array",
                "minItems": len(CHECKLIST),
                "maxItems": len(CHECKLIST),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "id", "status", "evidence_class", "reason"],
                    "properties": {
                        "id": {"type": "string", "enum": list(_CHECK_IDS)},
                        "status": {
                            "type": "string",
                            "enum": sorted(verdicts),
                        },
                        "evidence_class": {
                            "type": "string",
                            "enum": sorted(evidence_classes),
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
    comparison = context.get("_comparison_image")
    allow_design_mismatch = comparison is not None
    screenshot = {
        "label": _safe_text(context.get("label"), 120),
        "page": _safe_text(context.get("page"), 120),
    }
    task = {
        "task": "Evaluate only the supplied UI screenshot.",
        "screenshot": screenshot,
        "checklist": CHECKLIST,
        "evaluation_order": ["standalone_quality_each_image"],
        "standalone_quality_policy": {
            "scope": (
                "Independently inspect every supplied image, including an "
                "ordinary screenshot with no Designer reference."),
            "criteria": [
                "text readability, clipping, and overlap",
                (
                    "clearance between text and headers, borders, controls, "
                    "and adjacent text"),
                (
                    "internal padding, spacing between regions, and "
                    "consistent vertical rhythm"),
                (
                    "alignment, sizes, proportions, borders, visual "
                    "hierarchy, and balanced whitespace"),
            ],
            "defect_rule": (
                "Readable content with no literal overlap can still be "
                "defective. Treat visibly cramped, nearly touching, unevenly "
                "spaced, awkwardly aligned, or unbalanced content as an "
                "aesthetic defect."),
            "aesthetic_defect_verdict": "fail",
            "product_semantic_defect_verdict": "fail",
        },
        "evidence_policy": {
            "dynamic_runtime": (
                "Live or synthetic values such as temperatures, network "
                "addresses, clocks, progress, filenames, and runtime status. "
                "Compare their role, plausible format, readability, and "
                "location; do not compare their literal values unless the "
                "textual expectation explicitly constrains them."
            ),
            "rendering_only": (
                "Permitted rasterization, anti-aliasing, or other renderer "
                "variation explicitly allowed by the textual expectation."
            ),
            "aesthetic_defect": (
                "A standalone defect in spacing, clearance, alignment, "
                "balance, proportions, borders, hierarchy, or other visible "
                "presentation quality. This evidence requires fail."
            ),
            "product_semantic": (
                "Structure, controls, dialogs, selection, typed component "
                "state, missing content, clipping, overlap, or a value "
                "explicitly constrained by the textual expectation. A defect "
                "with this evidence requires fail."
            ),
        },
        "severity_policy": {
            "allowed_variation": "pass",
            "aesthetic_or_product_defect": "fail",
        },
        "response_schema": _response_schema(allow_design_mismatch),
    }
    expectation = context.get("expectation")
    if isinstance(expectation, dict):
        encoded = json.dumps(
            expectation, separators=(",", ":"), ensure_ascii=True)
        if len(encoded) > 6000:
            raise VisualCheckConfigurationError(
                "textual expectation exceeds the size limit")
        task["textual_expectation"] = expectation
    if comparison is not None:
        task["evaluation_order"].append(
            "designer_parity_if_comparison")
        task["image_roles"] = [
            "Designer-generated reference frame",
            "real printer Typer/framebuffer frame",
        ]
        task["evidence_policy"]["design_mismatch"] = (
            "A visible Designer/printer geometry or presentation difference "
            "that is not itself a visual-quality or product defect. This "
            "evidence requires warn.")
        task["severity_policy"]["clean_designer_mismatch"] = "warn"
        task["designer_parity_policy"] = {
            "comparison": (
                "After both standalone audits, compare visible presentation "
                "region by region from header through content and controls "
                "to footer."),
            "criteria": [
                (
                    "relative positions, sizes, proportions, alignment, "
                    "borders, padding, gaps, and whitespace"),
                "line breaks and typography hierarchy",
                "visible UI structure, content, selection, and typed state",
            ],
            "allowances": [
                "theme colors and font rasterization",
                (
                    "live footer-only status outside component state, such "
                    "as temperatures, network address, and preview/standby "
                    "labels"),
            ],
            "clean_mismatch_evidence_class": "design_mismatch",
            "clean_mismatch_verdict": "warn",
            "quality_defect_evidence_class": "aesthetic_defect",
            "quality_defect_verdict": "fail",
        }
        task["task"] = (
            "Independently evaluate the visual quality of both supplied UI "
            "screenshots, then compare their visible presentation and state.")
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
        "Follow evaluation_order. First independently inspect each image for "
        "standalone visual quality, even when there is no Designer reference. "
        "Readable, non-overlapping content is not automatically acceptable: "
        "inspect clearance, padding, spacing, vertical rhythm, alignment, "
        "proportions, hierarchy, and balance. Classify each check's evidence "
        "using only the classes allowed by response_schema. Dynamic runtime "
        "values are "
        "semantic slots rather than literals: compare their role, plausible "
        "format, readability, and location. Require exact or approximate "
        "numeric equality only when the textual expectation explicitly "
        "constrains that value. A permitted dynamic_runtime or rendering_only "
        "difference must pass and must not be reported as a defect. "
        "A standalone visual-quality defect uses aesthetic_defect and must "
        "fail. A structural, content, dialog, "
        "selection, typed-state, clipping, overlap, missing-content, or "
        "explicitly constrained-value defect uses product_semantic and must "
        "fail. A satisfied product-semantic check may pass. "
        "When no comparison image is supplied, source_parity must pass with "
        "none evidence. "
        "The top-level verdict must equal the most severe checklist "
        "status: pass only if every check passes, warn if the worst "
        "check warns, and fail if any check fails.")
    if corrective_retry:
        system_instruction += (
            " A previous response violated this contract. Re-evaluate the "
            "same image or images in standalone-first order, correct any "
            "evidence or allowed-variation misclassification, and obey the "
            "severity mapping exactly.")
    if comparison is not None:
        system_instruction += (
            " The first image is generated by Designer. The second image is "
            "captured from the real printer Typer/framebuffer. Independently "
            "inspect each image before comparing them. Then use source_parity "
            "to compare visual presentation and UI state region by region, "
            "from header to footer; semantic equivalence alone is not enough. "
            "Apply designer_parity_policy allowances only to theme, "
            "rasterization, and footer-only live status. Do not use them for "
            "geometry, layout, page titles, controls, dialogs, selections, or "
            "typed-state values. An allowed difference passes. A clean visible "
            "Designer mismatch uses design_mismatch and warns. A mismatch that "
            "also creates a standalone aesthetic or product defect fails. "
            "design_mismatch evidence is limited to source_parity.")
    return {
        "model": model,
        "temperature": 0,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "ff5m_ui_visual_verdict",
                "strict": True,
                "schema": _response_json_schema(allow_design_mismatch),
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


def validate_verdict(value, allow_design_mismatch=False):
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
                "id", "status", "evidence_class", "reason"}:
            raise ValueError(
                "each check must contain only id, status, evidence_class, "
                "and reason")
        if item["id"] != expected_id:
            raise ValueError("checklist ids must appear once in fixed order")
        status = item["status"]
        evidence_class = item["evidence_class"]
        reason = item["reason"]
        if status not in VALID_VERDICTS:
            raise ValueError("invalid checklist status for %s" % expected_id)
        if evidence_class not in EVIDENCE_CLASSES:
            raise ValueError(
                "invalid evidence class for %s" % expected_id)
        if (expected_id == "source_parity" and not allow_design_mismatch
                and (status != "pass" or evidence_class != "none")):
            raise ValueError(
                "standalone source_parity must pass with none evidence")
        if (evidence_class == "design_mismatch"
                and expected_id != "source_parity"):
            raise ValueError(
                "design_mismatch evidence is limited to source_parity")
        if (evidence_class == "design_mismatch"
                and not allow_design_mismatch):
            raise ValueError(
                "design_mismatch evidence requires a comparison image")
        if status not in _EVIDENCE_STATUSES[evidence_class]:
            raise ValueError(
                "evidence class %s does not permit %s status" %
                (evidence_class, status))
        if (not isinstance(reason, str)
                or len(reason) > MAX_REASON_LENGTH
                or status != "pass" and not reason.strip()):
            raise ValueError("invalid checklist reason for %s" % expected_id)
        normalized.append({
            "id": expected_id, "status": status,
            "evidence_class": evidence_class,
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
                verdict = validate_verdict(
                    _completion_content(response),
                    allow_design_mismatch=(
                        context.get("_comparison_image") is not None))
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
                "evidence_class": item["evidence_class"],
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
