## Hybrid FF5M UI regression runner.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Hybrid FF5M UI regression runner.

This module is explicit Mac-side test infrastructure. Importing it never
contacts a printer, starts a Designer, or calls a model.
"""

import argparse
import datetime
import json
import os
import pathlib
import shutil
import subprocess
import sys

from . import hybrid
from . import html_report
from . import openai_compatible as vision
from . import printer
from . import run as image_runner


ROOT = pathlib.Path(__file__).parents[2]
DEFAULT_SCENARIOS = pathlib.Path(__file__).with_name("scenarios.json")
DEFAULT_EXPECTATIONS = pathlib.Path(__file__).with_name("expectations.json")


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
        description="Run the explicit host-side FF5M hybrid UI regression.")
    parser.add_argument(
        "--mode", choices=("hybrid", "designer", "parity"),
        default="hybrid")
    parser.add_argument("--project-root", default=str(ROOT))
    parser.add_argument("--designer-root", required=True)
    parser.add_argument("--printer-host")
    parser.add_argument("--printer-artifacts", action="append", default=[])
    parser.add_argument("--confirm-printer-idle", action="store_true")
    parser.add_argument("--scenarios", default=str(DEFAULT_SCENARIOS))
    parser.add_argument("--expectations", default=str(DEFAULT_EXPECTATIONS))
    parser.add_argument(
        "--theme",
        help=(
            "explicit Designer theme; hybrid/parity otherwise use the "
            "theme recorded by the printer artifact"))
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--timeout", type=float)
    parser.add_argument(
        "--check-mode", choices=("advisory", "strict"),
        default=None)
    parser.add_argument("--api-key-env", default="FF5M_VISUAL_API_KEY")
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--output")
    return parser.parse_args(argv)


def _output_directory(value):
    if value:
        return pathlib.Path(value).resolve()
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return (ROOT / "tests" / "artifacts" /
            "ui-regression" / stamp).resolve()


def _printer_directories(args, output, component_cases):
    directories = []
    if args.printer_artifacts:
        for index, value in enumerate(args.printer_artifacts, 1):
            source = pathlib.Path(value).resolve()
            if not source.is_dir():
                raise hybrid.RegressionConfigurationError(
                    "saved printer artifact is not a directory")
            destination = output / "printer" / ("saved-%02d" % index)
            destination.mkdir(parents=True, exist_ok=False)
            for name in ("environment.json", "manifest.json"):
                path = source / name
                if path.is_file():
                    shutil.copy2(path, destination / name)
            for path in sorted(source.iterdir()):
                if (
                    path.is_file()
                    and path.suffix.lower() in image_runner.SUPPORTED_SUFFIXES
                ):
                    shutil.copy2(path, destination / path.name)
            directories.append(destination)
        return directories
    if args.mode == "designer":
        return []
    if not args.printer_host:
        raise hybrid.RegressionConfigurationError(
            "hybrid/parity mode requires --printer-host or "
            "--printer-artifacts")
    collector = printer.PrinterCollector(
        args.printer_host, confirmed_idle=args.confirm_printer_idle)
    root = output / "printer"
    directories.append(collector.collect("UI", root))
    if args.mode == "parity":
        directories.append(collector.collect(
            "COMPONENT", root, component_cases=component_cases))
    return directories


def _image_inputs(records):
    images = []
    for item in records:
        context = {
            "number": item.get("number"),
            "label": item.get("label") or item["case_id"],
            "page": item.get("page"),
            "case_id": item["case_id"],
            "semantic_page_id": item.get("semantic_page_id"),
            "source": item["source"],
            "expectation": item["expectation"],
        }
        images.append({
            "path": pathlib.Path(item["path"]),
            "mime_type": image_runner._mime_type(pathlib.Path(item["path"])),
            "context": context,
            "comparison_path": item.get("comparison_path"),
        })
    return images


def _regression_status(artifact, mode):
    if artifact["status"] == "disabled":
        return "disabled"
    verdicts = []
    integration_error = False
    for frame in artifact.get("screenshots", ()):
        for result in frame.get("models", ()):
            if result.get("status") != "completed":
                integration_error = True
            if result.get("verdict"):
                verdicts.append(result["verdict"])
    if integration_error or "fail" in verdicts:
        return "fail"
    if "warn" in verdicts:
        return "review"
    if mode == "designer":
        return "partial"
    return "pass"


def _flatten_case_results(artifact):
    for frame in artifact.get("screenshots", ()):
        results = frame.get("models", ())
        if not results:
            frame["case_result"] = {
                "verdict": "not_run",
                "reasons": [],
                "json_validation": {"status": "not_run", "error": None},
                "elapsed_seconds": 0.0,
                "error": None,
            }
            continue
        result = results[0]
        frame["case_result"] = {
            "verdict": (
                result.get("verdict")
                if result.get("status") == "completed" else "fail"),
            "reasons": result.get("reasons", []),
            "json_validation": result.get("json_validation"),
            "elapsed_seconds": result.get("elapsed_seconds"),
            "error": result.get("error"),
        }


def _relative_artifact(output, path):
    if path is None:
        return None
    path = pathlib.Path(path).resolve()
    output = pathlib.Path(output).resolve()
    try:
        return path.relative_to(output).as_posix()
    except ValueError:
        return None


def _attach_artifact_paths(artifact, records, output):
    frames = artifact.get("screenshots", ())
    for frame, record in zip(frames, records):
        screenshot = frame.get("screenshot", {})
        screenshot["artifact"] = _relative_artifact(
            output, record.get("path"))
        screenshot["comparison_artifact"] = _relative_artifact(
            output, record.get("comparison_path"))


def _unreviewed_artifacts(output):
    output = pathlib.Path(output).resolve()
    records = []
    for path in sorted(output.rglob("*")):
        if (
            not path.is_file()
            or path.suffix.lower() not in image_runner.SUPPORTED_SUFFIXES
        ):
            continue
        artifact = _relative_artifact(output, path)
        parts = pathlib.PurePosixPath(artifact).parts if artifact else ()
        source = parts[0] if parts else "unknown"
        records.append({
            "status": "not_run",
            "models": [],
            "case_result": {
                "verdict": "not_run",
                "reasons": [],
                "json_validation": {
                    "status": "not_run", "error": None,
                },
                "elapsed_seconds": 0.0,
                "error": None,
            },
            "screenshot": {
                "label": path.stem,
                "file": path.name,
                "artifact": artifact,
                "source": source,
            },
        })
    return records


def _pipeline_stages(mode, designer_records, printer_runs, merged,
                     artifact=None, missing=None):
    discovered = set(
        item.get("semantic_page_id") for item in designer_records
        if item.get("semantic_page_id"))
    printer_captured = sum(item["captured"] for item in printer_runs)
    stages = [{
        "id": "designer",
        "status": "completed",
        "title": "Designer discovery and capture",
        "summary": (
            "%d component pages discovered; %d scenario frames rendered."
            % (len(discovered), len(designer_records))),
        "counts": {
            "discovered_pages": len(discovered),
            "captured_frames": len(designer_records),
        },
    }]
    if mode == "designer":
        stages.append({
            "id": "printer",
            "status": "not_required",
            "title": "Real-printer UI capture",
            "summary": "Skipped by the explicit Designer-only mode.",
            "counts": {"captured_frames": 0},
            "runs": [],
        })
    else:
        stages.append({
            "id": "printer",
            "status": "completed",
            "title": "Real-printer UI capture",
            "summary": (
                "%d framebuffer frames downloaded from %d completed "
                "printer suite run(s)." %
                (printer_captured, len(printer_runs))),
            "counts": {"captured_frames": printer_captured},
            "runs": list(printer_runs),
        })
    stages.append({
        "id": "merge",
        "status": "completed",
        "title": "Hybrid composition",
        "summary": (
            "%d Designer frames and %d retained printer frames; "
            "%d printer duplicate(s) replaced by Designer."
            % (
                len(merged["designer"]),
                len(merged["legacy"]),
                len(merged["replaced"]),
            )),
        "counts": {
            "designer_frames": len(merged["designer"]),
            "printer_frames": len(merged["legacy"]),
            "replaced_duplicates": len(merged["replaced"]),
            "parity_pairs": len(merged["pairs"]),
            "review_corpus": len(merged["records"]),
        },
    })
    if missing:
        stages.append({
            "id": "llm",
            "status": "not_run",
            "title": "LLM visual review",
            "summary": (
                "Not started because %d textual baseline(s) are missing."
                % len(missing)),
            "counts": {"submitted_frames": 0},
        })
        return stages
    summary = (artifact or {}).get("summary", {})
    statuses = summary.get("statuses", {})
    completed = int(statuses.get("completed", 0))
    enabled = bool(summary.get("enabled"))
    stages.append({
        "id": "llm",
        "status": (
            "completed" if enabled and completed == len(merged["records"])
            else "disabled" if not enabled else "failed"),
        "title": "LLM visual review",
        "summary": (
            "%d of %d merged frames received a structured model result."
            % (completed, len(merged["records"]))
            if enabled else
            "Disabled; no images were submitted to a model."),
        "counts": {
            "submitted_frames": len(merged["records"]) if enabled else 0,
            "completed_results": completed,
            "json_valid_results": sum(
                1 for frame in (artifact or {}).get("screenshots", ())
                for result in frame.get("models", ())
                if result.get("json_validation", {}).get("status") == "valid"),
        },
    })
    return stages


def _printer_run(directory, records, suite, output):
    environment_path = pathlib.Path(directory) / "environment.json"
    environment = {}
    if environment_path.is_file():
        try:
            value = json.loads(environment_path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                environment = value
        except (OSError, ValueError):
            pass
    return {
        "suite": suite,
        "run_id": str(
            environment.get("run_id") or pathlib.Path(directory).name),
        "captured": len(records),
        "artifact": _relative_artifact(output, directory),
        "theme": str(environment.get("theme") or ""),
    }


def _designer_theme(requested, printer_runs):
    requested = str(requested or "").strip()
    if requested:
        return requested
    themes = sorted(set(
        str(item.get("theme") or "").strip()
        for item in printer_runs
        if str(item.get("theme") or "").strip()))
    if len(themes) > 1:
        raise hybrid.RegressionConfigurationError(
            "printer artifacts use different UI themes")
    return themes[0] if themes else "DEFAULT"


def _redacted_error(args, exc):
    message = " ".join(str(exc).split())[:500]
    values = [
        getattr(args, "base_url", None),
        os.environ.get("FF5M_VISUAL_BASE_URL"),
        os.environ.get(getattr(args, "api_key_env", ""), ""),
    ]
    for value in values:
        if value:
            message = message.replace(str(value), "[redacted]")
    return message


def _infrastructure_report(args, output, exc):
    frames = _unreviewed_artifacts(output)
    return {
        "schema_version": 1,
        "status": "fail",
        "mode": args.mode,
        "coverage": {
            "designer": sum(
                1 for item in frames
                if item["screenshot"].get("source") == "designer"),
            "legacy_printer": sum(
                1 for item in frames
                if item["screenshot"].get("source") == "printer"),
            "replaced": 0,
            "parity_pairs": 0,
        },
        "configuration": {
            "model": (
                args.model or os.environ.get("FF5M_VISUAL_MODEL", "")),
        },
        "summary": {
            "screenshots": len(frames),
            "statuses": {"not_run": len(frames)},
            "verdicts": {},
        },
        "missing_expectations": [],
        "screenshots": frames,
        "infrastructure_error": {
            "category": type(exc).__name__,
            "message": _redacted_error(args, exc),
        },
    }


def _markdown_report(report):
    coverage = report["coverage"]
    lines = [
        "# FF5M UI regression",
        "",
        "- Status: `%s`" % report["status"],
        "- Mode: `%s`" % report["mode"],
        "- Model: `%s`" % (
            report.get("configuration", {}).get("model") or "disabled"),
        "- Designer cases: %d" % coverage["designer"],
        "- Legacy printer cases: %d" % coverage["legacy_printer"],
        "- Replaced printer duplicates: %d" % coverage["replaced"],
        "- Parity pairs: %d" % coverage["parity_pairs"],
        "",
    ]
    if report.get("missing_expectations"):
        lines.extend([
            "The run needs approved textual baselines for %d cases."
            % len(report["missing_expectations"]),
            "",
        ])
    error = report.get("infrastructure_error")
    if isinstance(error, dict):
        lines.extend([
            "## Infrastructure failure",
            "",
            "- Category: `%s`" % error.get("category", "error"),
            "- Message: %s" % error.get("message", ""),
            "",
        ])
    return "\n".join(lines)


def _write_reports(output, report):
    image_runner.write_artifact(output / "report.json", report)
    (output / "report.md").write_text(
        _markdown_report(report), encoding="utf-8")
    html_report.write(output / "report.html", report)


def execute(args, output=None):
    output = pathlib.Path(output or _output_directory(args.output)).resolve()
    output.mkdir(parents=True, exist_ok=True)
    scenarios = hybrid.load_scenarios(args.scenarios)
    expectations = hybrid.load_expectations(args.expectations)
    discovery = hybrid.discover_designer(
        args.designer_root, args.project_root)
    # The component payload needs only IDs and typed state. Build it before
    # collection, then rebuild the render cases with the printer's captured
    # theme once artifact metadata is available.
    cases = hybrid.build_designer_cases(
        discovery, scenarios, theme=args.theme or "DEFAULT")
    ui_records = []
    component_records = []
    printer_runs = []
    expected_fingerprint = hybrid.ui_fingerprint(args.project_root)
    printer_component_cases = [{
        "id": item["id"],
        "page": item["semantic_page_id"],
        "state": item["state"],
    } for item in cases if item["state"]]
    printer_directories = _printer_directories(
        args, output, printer_component_cases)
    for directory in printer_directories:
        hybrid.verify_artifact_fingerprint(
            directory, expected_fingerprint)
        records = hybrid.load_manifest(directory, source="printer")
        suite = hybrid.artifact_suite(directory)
        printer_runs.append(
            _printer_run(directory, records, suite, output))
        for item in records:
            item["printer_suite"] = suite
        if suite == "UI":
            ui_records.extend(records)
        else:
            component_records.extend(records)
    designer_theme = _designer_theme(args.theme, printer_runs)
    cases = hybrid.build_designer_cases(
        discovery, scenarios, theme=designer_theme)
    designer_records = hybrid.DesignerCapture(
        args.designer_root, args.project_root).capture(
            cases, output / "designer")
    if args.mode == "designer":
        merged = {
            "records": designer_records,
            "designer": designer_records,
            "legacy": [],
            "replaced": [],
            "pairs": [],
            "discovered_page_ids": sorted(set(
                item.get("semantic_page_id") for item in designer_records)),
        }
    else:
        designer_case_ids = set(
            item.get("case_id") for item in designer_records
            if item.get("case_id"))
        hybrid.validate_printer_coverage(
            ui_records, component_records, designer_case_ids,
            require_component=args.mode == "parity")
        merged = hybrid.merge_hybrid(
            designer_records, ui_records, parity=args.mode == "parity",
            parity_records=component_records)
    ready, missing = hybrid.attach_expectations(
        merged["records"], expectations)
    if missing:
        hybrid.write_candidates(output / "expectations.candidate.json", missing)
        report = {
            "schema_version": 1,
            "status": "needs_baseline",
            "mode": args.mode,
            "coverage": {
                "designer": len(merged["designer"]),
                "printer_captured": len(ui_records) + len(component_records),
                "legacy_printer": len(merged["legacy"]),
                "replaced": len(merged["replaced"]),
                "parity_pairs": len(merged["pairs"]),
            },
            "discovered_page_ids": merged["discovered_page_ids"],
            "missing_expectations": missing,
            "configuration": {
                "model": args.model or "",
                "designer_theme": designer_theme,
            },
            "pipeline": _pipeline_stages(
                args.mode, designer_records, printer_runs, merged,
                missing=missing),
        }
    else:
        model = args.model or os.environ.get("FF5M_VISUAL_MODEL", "")
        base_url = args.base_url or os.environ.get(
            "FF5M_VISUAL_BASE_URL", "")
        timeout = args.timeout or float(os.environ.get(
            "FF5M_VISUAL_TIMEOUT", "30"))
        settings = vision.VisualCheckSettings(
            enabled=args.enable, base_url=base_url, model=model,
            api_key=os.environ.get(args.api_key_env, ""),
            timeout=timeout, mode=(
                args.check_mode
                or os.environ.get("FF5M_VISUAL_MODE", "advisory")))
        artifact = image_runner.run_checks(
            settings, _image_inputs(ready))
        _attach_artifact_paths(artifact, ready, output)
        _flatten_case_results(artifact)
        report = dict(artifact)
        report["configuration"] = dict(
            report.get("configuration", {}))
        report["configuration"]["designer_theme"] = designer_theme
        report.update({
            "schema_version": 1,
            "status": _regression_status(artifact, args.mode),
            "mode": args.mode,
            "coverage": {
                "designer": len(merged["designer"]),
                "printer_captured": len(ui_records) + len(component_records),
                "legacy_printer": len(merged["legacy"]),
                "replaced": len(merged["replaced"]),
                "parity_pairs": len(merged["pairs"]),
            },
            "discovered_page_ids": merged["discovered_page_ids"],
            "missing_expectations": [],
            "pipeline": _pipeline_stages(
                args.mode, designer_records, printer_runs, merged,
                artifact=artifact),
        })
    if not report.get("screenshots"):
        report["screenshots"] = _unreviewed_artifacts(output)
    _write_reports(output, report)
    return report, output


def main(argv=None):
    args = _arguments(argv)
    _load_env(args.env_file)
    output = _output_directory(args.output)
    output.mkdir(parents=True, exist_ok=True)
    try:
        report, output = execute(args, output=output)
    except Exception as exc:
        report = _infrastructure_report(args, output, exc)
        _write_reports(output, report)
        print(
            "UI regression configuration/infrastructure error: %s; "
            "report=%s" % (exc, output / "report.html"),
            file=sys.stderr)
        return 2
    print("UI regression: %s; report=%s; artifact=%s" % (
        report["status"], output / "report.html", output / "report.json"))
    if report["status"] in ("pass", "disabled"):
        return 0
    if report["status"] in ("review", "partial", "needs_baseline"):
        return 3
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
