## Host-only Designer capture and hybrid artifact composition.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Host-only Designer capture and hybrid artifact composition."""

import hashlib
import json
import os
import pathlib
import re
import selectors
import subprocess
import sys
import time


SCHEMA_VERSION = 1
CASE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,95}$")
UI_FINGERPRINT_FILES = (
    "feather_screen.py",
    "feather_feature_ui_test.py",
)
UI_SUITE_LABELS = frozenset((
    "baseline",
    "ui-home-filled",
    "ui-home",
    "ui-main-menu",
    "ui-files",
    "ui-file-confirm",
    "ui-control",
    "ui-move",
    "ui-heat",
    "ui-calibration",
    "ui-calibration-variants",
    "ui-settings",
    "ui-mod-parameters",
    "ui-filament-materials",
    "ui-filament-action",
    "ui-filament-cooling",
    "ui-filament-back-materials",
    "ui-network",
))
CAPTURE_PROGRESS_PREFIX = "FF5M_CAPTURE_PROGRESS "


class RegressionConfigurationError(ValueError):
    pass


def _run_capture_command(command, timeout, progress=None, clock=None,
                         total=None):
    clock = clock or time.monotonic
    started = clock()
    last_update = started
    initialized = False
    if progress is not None and total is not None:
        total = int(total)
        if total <= 0:
            raise ValueError("Designer capture total must be positive")
        progress({
            "completed": 0,
            "total": total,
            "case_id": None,
            "last_elapsed_seconds": None,
            "elapsed_seconds": 0.0,
            "eta_seconds": None,
        })
        initialized = True
    process = subprocess.Popen(
        command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        bufsize=1)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    stdout = []

    def consume(line):
        nonlocal initialized, last_update
        stdout.append(line)
        if progress is None or not line.startswith(CAPTURE_PROGRESS_PREFIX):
            return
        try:
            event = json.loads(line[len(CAPTURE_PROGRESS_PREFIX):])
            completed = int(event["completed"])
            total = int(event["total"])
            case_id = str(event["case_id"])
        except (KeyError, TypeError, ValueError):
            raise RegressionConfigurationError(
                "Designer capture returned invalid progress")
        if total <= 0 or completed <= 0 or completed > total:
            raise RegressionConfigurationError(
                "Designer capture returned invalid progress counts")
        if not initialized:
            progress({
                "completed": 0,
                "total": total,
                "case_id": None,
                "last_elapsed_seconds": None,
                "elapsed_seconds": 0.0,
                "eta_seconds": None,
            })
            initialized = True
        now = clock()
        elapsed = max(0.0, now - started)
        progress({
            "completed": completed,
            "total": total,
            "case_id": case_id,
            "last_elapsed_seconds": max(0.0, now - last_update),
            "elapsed_seconds": elapsed,
            "eta_seconds": (
                elapsed / completed * (total - completed)
                if completed < total else 0.0),
        })
        last_update = now

    try:
        while process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            for key, _mask in selector.select(min(0.25, remaining)):
                line = key.fileobj.readline()
                if line:
                    consume(line)
        for line in process.stdout:
            consume(line)
    except BaseException:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()
        raise
    finally:
        selector.close()
    stderr = process.stderr.read()
    process.stdout.close()
    process.stderr.close()
    return subprocess.CompletedProcess(
        command, process.returncode, "".join(stdout), stderr)


def _load_json(path, default):
    path = pathlib.Path(path)
    if not path.is_file():
        return default
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise RegressionConfigurationError(
            "unsupported JSON contract: %s" % path)
    return value


def _slug(value):
    value = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return value[:96] or "screen"


def _default_case_id(page_id):
    return "default-" + _slug(str(page_id).rsplit(".", 1)[-1])


def load_scenarios(path):
    value = _load_json(path, {"schema_version": 1, "cases": []})
    cases = value.get("cases")
    if not isinstance(cases, list):
        raise RegressionConfigurationError("scenario cases must be an array")
    result = []
    seen = set()
    for item in cases:
        if not isinstance(item, dict):
            raise RegressionConfigurationError(
                "each scenario must be an object")
        case_id = str(item.get("id", ""))
        page = str(item.get("page", ""))
        state = item.get("state", {})
        actions = item.get("actions", [])
        if not CASE_ID.match(case_id) or not page:
            raise RegressionConfigurationError(
                "scenario requires a stable lowercase id and page")
        if case_id in seen:
            raise RegressionConfigurationError(
                "duplicate scenario id: %s" % case_id)
        if not isinstance(state, dict) or not isinstance(actions, list):
            raise RegressionConfigurationError(
                "scenario state/actions have invalid types")
        seen.add(case_id)
        result.append({
            "id": case_id,
            "page": page,
            "label": str(item.get("label") or case_id),
            "state": state,
            "actions": actions,
        })
    return result


def load_expectations(path):
    value = _load_json(path, {"schema_version": 1, "cases": {}})
    cases = value.get("cases")
    if not isinstance(cases, dict):
        raise RegressionConfigurationError(
            "expectation cases must be an object")
    result = {}
    for case_id, expectation in cases.items():
        if not isinstance(case_id, str) or not isinstance(expectation, dict):
            raise RegressionConfigurationError(
                "expectations must map ids to objects")
        description = str(expectation.get("description", "")).strip()
        required = expectation.get("required", [])
        forbidden = expectation.get("forbidden", [])
        allowed = expectation.get("allowed_variations", [])
        if (not description or not isinstance(required, list)
                or not isinstance(forbidden, list)
                or not isinstance(allowed, list)):
            raise RegressionConfigurationError(
                "invalid expectation for %s" % case_id)
        result[case_id] = {
            "description": description,
            "required": [str(item) for item in required],
            "forbidden": [str(item) for item in forbidden],
            "allowed_variations": [str(item) for item in allowed],
        }
    return result


def discover_designer(designer_root, project_root, timeout=60):
    designer_root = pathlib.Path(designer_root).resolve()
    validator = designer_root / "feather_ui_validate.py"
    if not validator.is_file():
        raise RegressionConfigurationError(
            "Feather UI Designer validator was not found")
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, str(validator), "--project", str(project_root),
         "--json"],
        cwd=str(designer_root), env=environment, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode != 0:
        raise RegressionConfigurationError(
            "Designer validation failed: %s" %
            " ".join(result.stderr.split())[:500])
    try:
        value = json.loads(result.stdout)
    except ValueError as exc:
        raise RegressionConfigurationError(
            "Designer validation returned invalid JSON") from exc
    if value.get("status") != "ok" or not isinstance(value.get("pages"), list):
        raise RegressionConfigurationError("Designer project is not valid")
    return value


def build_designer_cases(discovery, scenarios, theme="DEFAULT",
                         width=800, height=480):
    pages = {}
    for page in discovery.get("pages", ()):
        page_id = str(page.get("id", ""))
        if page_id:
            pages[page_id] = page
    cases = []
    for page_id in sorted(pages):
        page = pages[page_id]
        cases.append({
            "id": _default_case_id(page_id),
            "label": "%s default" % page.get("title", page_id),
            "semantic_page_id": page_id,
            "state": {},
            "actions": [],
            "theme": theme,
            "width": int(width),
            "height": int(height),
        })
    for scenario in scenarios:
        page_id = scenario["page"]
        if page_id not in pages:
            raise RegressionConfigurationError(
                "scenario references an undiscovered page: %s" % page_id)
        item = dict(scenario)
        item["semantic_page_id"] = item.pop("page")
        item.update({
            "theme": theme, "width": int(width), "height": int(height),
        })
        cases.append(item)
    return cases


class DesignerCapture:
    def __init__(self, designer_root, project_root, node="node",
                 command_runner=None):
        self.designer_root = pathlib.Path(designer_root).resolve()
        self.project_root = pathlib.Path(project_root).resolve()
        self.node = str(node)
        self.command_runner = command_runner or subprocess.run

    def capture(self, cases, output_directory, timeout=120, progress=None):
        output_directory = pathlib.Path(output_directory).resolve()
        output_directory.mkdir(parents=True, exist_ok=True)
        browser_module = (
            self.designer_root / "ui_preview" / "node_modules" /
            "playwright")
        capture_script = pathlib.Path(__file__).with_name(
            "designer_capture.cjs")
        preview = self.designer_root / "feather_ui_preview.py"
        if not browser_module.is_dir():
            raise RegressionConfigurationError(
                "Designer Playwright runtime is unavailable; no dependency "
                "was installed")
        plan_path = output_directory / "capture-plan.json"
        plan_path.write_text(json.dumps({
            "schema_version": SCHEMA_VERSION,
            "cases": list(cases),
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        scene_script = pathlib.Path(__file__).with_name(
            "designer_scenes.py")
        scene_result = subprocess.run(
            [sys.executable, str(scene_script), str(self.designer_root),
             str(self.project_root), str(plan_path)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout)
        if scene_result.returncode != 0:
            raise RegressionConfigurationError(
                "Designer scenario validation failed: %s" %
                " ".join(scene_result.stderr.split())[:500])
        data_root = output_directory / ".designer-data"
        environment = dict(os.environ)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        process = subprocess.Popen(
            [sys.executable, str(preview), "--project",
             str(self.project_root), "--host", "127.0.0.1", "--port", "0",
             "--width", "800", "--height", "480", "--data-root",
             str(data_root)],
            cwd=str(self.designer_root), env=environment, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            url = self._server_url(process, timeout=30)
            command = [
                self.node, str(capture_script), str(self.designer_root),
                url, str(plan_path), str(output_directory),
            ]
            if progress is not None and self.command_runner is subprocess.run:
                result = _run_capture_command(
                    command, timeout=timeout, progress=progress,
                    total=len(cases))
            else:
                result = self.command_runner(
                    command, text=True, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, timeout=timeout)
            if result.returncode != 0:
                raise RegressionConfigurationError(
                    "Designer capture failed: %s" %
                    " ".join(result.stderr.split())[:500])
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        return load_manifest(output_directory, source="designer")

    @staticmethod
    def _server_url(process, timeout):
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                events = selector.select(min(0.5, deadline - time.monotonic()))
                if not events:
                    continue
                line = process.stdout.readline()
                match = re.search(r"(http://\S+/)", line)
                if match:
                    return match.group(1)
        finally:
            selector.close()
        error = process.stderr.read() if process.poll() is not None else ""
        raise RegressionConfigurationError(
            "Designer server did not start: %s" %
            " ".join(error.split())[:500])


def load_manifest(directory, source="printer"):
    directory = pathlib.Path(directory).resolve()
    path = directory / "manifest.json"
    if not path.is_file():
        raise RegressionConfigurationError(
            "artifact manifest was not found: %s" % path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise RegressionConfigurationError("artifact manifest must be an array")
    records = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or not item.get("file"):
            continue
        image = (directory / str(item["file"])).resolve()
        if directory not in image.parents or not image.is_file():
            raise RegressionConfigurationError(
                "artifact image is missing or escapes its directory")
        record = dict(item)
        record.update({
            "source": source,
            "path": image,
            "artifact_directory": directory,
            "case_id": str(item.get("case_id") or (
                "%s-%s" % (source, _slug(item.get("label") or index)))),
        })
        records.append(record)
    return records


def merge_hybrid(designer_records, printer_records, parity=False,
                 parity_records=None):
    designer_records = list(designer_records)
    printer_records = list(printer_records)
    discovered = set(
        item.get("semantic_page_id") for item in designer_records
        if item.get("semantic_page_id"))
    legacy = []
    replaced = []
    for item in printer_records:
        if item.get("semantic_page_id") in discovered:
            replaced.append(item)
        else:
            legacy.append(item)
    combined = list(designer_records) + legacy
    pairs = []
    if parity:
        defaults = {}
        by_case = {}
        for item in designer_records:
            defaults.setdefault(item.get("semantic_page_id"), item)
            by_case[item.get("case_id")] = item
        candidates = (
            list(parity_records) if parity_records is not None else replaced)
        for printer in candidates:
            designer = by_case.get(printer.get("case_id"))
            if designer is None:
                designer = defaults.get(printer.get("semantic_page_id"))
            if designer is None:
                continue
            pairs.append({
                "case_id": "parity-" + designer["case_id"],
                "label": "Designer / printer parity",
                "page": printer.get("page"),
                "semantic_page_id": printer.get("semantic_page_id"),
                "source": "parity",
                "path": designer["path"],
                "comparison_path": printer["path"],
                "designer_case_id": designer["case_id"],
                "printer_case_id": printer["case_id"],
            })
        combined.extend(pairs)
    return {
        "records": combined,
        "designer": designer_records,
        "legacy": legacy,
        "replaced": replaced,
        "pairs": pairs,
        "discovered_page_ids": sorted(discovered),
    }


def attach_expectations(records, expectations):
    ready = []
    missing = []
    for item in records:
        value = dict(item)
        expectation = expectations.get(value["case_id"])
        if expectation is None and value["source"] == "printer":
            expectation = expectations.get(
                "printer:" + str(value.get("label", "")))
        if expectation is None and value["source"] == "parity":
            expectation = expectations.get(value.get("designer_case_id"))
        if expectation is None:
            missing.append({
                "case_id": value["case_id"],
                "source": value["source"],
                "semantic_page_id": value.get("semantic_page_id"),
                "description": (
                    "Describe the intended visible structure and state for %s."
                    % value.get("label", value["case_id"])),
                "required": [],
                "forbidden": [
                    "blank or corrupted frame",
                    "clipped or overlapping important content",
                ],
                "allowed_variations": [],
            })
        else:
            value["expectation"] = expectation
            ready.append(value)
    return ready, missing


def write_candidates(path, missing):
    pathlib.Path(path).write_text(json.dumps({
        "schema_version": 1,
        "cases": dict((item["case_id"], {
            key: value for key, value in item.items()
            if key in ("description", "required", "forbidden",
                       "allowed_variations")
        }) for item in missing),
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def file_sha256(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ui_fingerprint(project_root):
    root = pathlib.Path(project_root).resolve() / ".py" / "klipper" / "plugins"
    files = []
    for relative in UI_FINGERPRINT_FILES:
        path = root / relative
        if path.is_file():
            files.append((relative, path))
    for package in ("ui", "ff5m_ui"):
        package_root = root / package
        if not package_root.is_dir():
            raise RegressionConfigurationError(
                "UI fingerprint package is missing: %s" % package)
        for path in package_root.rglob("*.py"):
            if "__pycache__" not in path.parts:
                files.append((path.relative_to(root).as_posix(), path))
    digest = hashlib.sha256()
    for relative, path in sorted(files, key=lambda item: item[0]):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(128 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def verify_artifact_fingerprint(directory, expected):
    path = pathlib.Path(directory).resolve() / "environment.json"
    if not path.is_file():
        raise RegressionConfigurationError(
            "printer artifact has no environment fingerprint: %s" % path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise RegressionConfigurationError(
            "printer environment is invalid JSON") from exc
    actual = str(value.get("ui_fingerprint", ""))
    if not actual:
        raise RegressionConfigurationError(
            "printer artifact has no UI fingerprint")
    if actual != str(expected):
        raise RegressionConfigurationError(
            "deployed UI fingerprint does not match the local checkout")
    return actual


def artifact_suite(directory):
    path = pathlib.Path(directory).resolve() / "environment.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RegressionConfigurationError(
            "printer artifact environment is unavailable") from exc
    suite = str(value.get("suite", "")).upper()
    if suite not in ("UI", "COMPONENT"):
        raise RegressionConfigurationError(
            "unexpected printer artifact suite: %s" % (suite or "missing"))
    return suite


def validate_printer_coverage(ui_records, component_records,
                              designer_case_ids, require_component=False):
    ui_labels = set(str(item.get("label", "")) for item in ui_records)
    missing_ui = sorted(UI_SUITE_LABELS - ui_labels)
    if missing_ui:
        raise RegressionConfigurationError(
            "printer UI artifact is incomplete; missing: %s" %
            ", ".join(missing_ui))
    if require_component:
        component_ids = set(
            item.get("case_id") for item in component_records
            if item.get("case_id"))
        missing_components = sorted(
            set(designer_case_ids) - component_ids)
        if missing_components:
            raise RegressionConfigurationError(
                "printer component artifact is incomplete; missing: %s" %
                ", ".join(missing_components))
