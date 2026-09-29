## Read one printer-regression run directory into plain data for reporting.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Load the artifacts of a finished (or interrupted) printer-regression run.

The run directory is the single source of truth: ``report.json`` names the
suites, and every other file is optional.  Nothing here contacts a printer or
mutates the run.  A missing or malformed file never raises; it is recorded in
``problems`` so the report can say what it could not read instead of silently
showing an empty section.

Time.  Printer artifacts use the printer's epoch and monotonic clocks, while
``recording.mp4`` starts at host time zero.  Each suite carries the recording
offset at which it started (``timeline_start_seconds``), so every event is
placed on one axis, "recording seconds", and a step can seek the video.
"""

import csv
import json
import math
import pathlib
import re
import urllib.parse

LOG_LINE = re.compile(
    r"^(?P<stamp>\d{4}-\d\d-\d\dT[\d:.]+) monotonic=(?P<mono>[\d.]+) "
    r"(?P<event>\S+)(?: (?P<rest>.*))?$")
# Labels may contain spaces ("0.100 MM"), so they are delimited by the
# neighboring fields rather than by whitespace.
STEP_FIELDS = re.compile(
    r"index=(\d+) kind=(\S+) label=(.*?)(?: eventtime=[\d.]+)?$")
EVENT_TIME = re.compile(r" eventtime=[\d.]+$")
# Display-only heuristics for free-text printer log lines.  They are
# deliberately narrow and case-sensitive: Klipper prints "error=None" and a
# "(1=hit 0=timeout ...)" legend on every healthy homing move.
ERROR_TEXT = re.compile(
    r"FAILED|Traceback|Exception|\bERROR\b|\bFATAL\b|shutdown:|"
    r"Timer too close|Lost communication")
WARNING_TEXT = re.compile(r"\bWARN(?:ING)?\b|overflowed|timed out|retrying")
NOISE_EVENTS = {
    "TEMPERATURE": "samples", "POSITION": "samples",
    "CAPTURE_QUEUED": "captures", "CAPTURE": "captures",
    "STEP_START": "steps", "PASS": "steps",
}
CPU_TICKS_PER_SECOND = 100.0
SERIES_LIMIT = 100000


def number(value, default=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def summarize(values):
    """Count, min, mean, 95th percentile, and max of the numeric values."""
    values = sorted(item for item in values if item is not None)
    if not values:
        return None
    index = min(len(values) - 1, int(math.ceil(0.95 * len(values))) - 1)
    return {
        "count": len(values), "min": values[0],
        "mean": sum(values) / len(values), "p95": values[index],
        "max": values[-1],
    }


def _read_text(path, problems):
    try:
        return pathlib.Path(path).read_text(
            encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None
    except OSError as exc:
        problems.append("%s: %s" % (pathlib.Path(path).name, exc))
        return None


def _read_json(path, problems):
    text = _read_text(path, problems)
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError as exc:
        problems.append("%s is not valid JSON: %s" % (
            pathlib.Path(path).name, exc))
        return None


def _read_table(path, problems, delimiter=","):
    text = _read_text(path, problems)
    if text is None:
        return []
    try:
        return list(csv.DictReader(text.splitlines(), delimiter=delimiter))
    except csv.Error as exc:
        problems.append("%s is not valid CSV: %s" % (
            pathlib.Path(path).name, exc))
        return []


def _read_jsonl(path, problems):
    text = _read_text(path, problems)
    records = []
    skipped = 0
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError:
            skipped += 1
            continue
        if isinstance(value, dict):
            records.append(value)
    if skipped:
        problems.append("%s: %d unreadable line(s) skipped" % (
            pathlib.Path(path).name, skipped))
    return records


def _file_list(directory):
    if directory is None or not directory.is_dir():
        return []
    files = []
    for path in sorted(directory.iterdir()):
        if path.is_file() and path.suffix.lower() != ".bmp":
            files.append({"name": path.name, "size": path.stat().st_size})
    return files


def _log_line(line_number, text):
    """Classify one log line for display; the heuristics never hide text."""
    match = LOG_LINE.match(text)
    event = match.group("event") if match else None
    if event is None:
        inner = re.match(r"^\[[\w.]+\] (\w+)", text)
        event = inner.group(1) if inner else None
    if event in NOISE_EVENTS:
        group = NOISE_EVENTS[event]
    elif re.match(r"^\[feather_screen\] command (start|finish)", text):
        group = "commands"
    else:
        group = "key"
    # Step labels such as "ui-error-restart" are data, not diagnostics, so
    # structured step/capture/sample lines are never classified by their text.
    structured = event in NOISE_EVENTS
    level = "info" if structured else "error" if ERROR_TEXT.search(text) else (
        "warn" if WARNING_TEXT.search(text) else "info")
    return {
        "number": line_number, "text": text, "event": event, "group": group,
        "level": level,
        "mono": number(match.group("mono")) if match else None,
    }


def _parse_steps(log, reactor, suite):
    """Turn STEP_START/PASS/FAILED/CAPTURE lines into one record per step."""
    steps = []
    current = None
    run_start = None
    for line in log:
        match = LOG_LINE.match(line["text"])
        if not match:
            continue
        event, rest = match.group("event"), match.group("rest") or ""
        mono = number(match.group("mono"))
        if event == "run" and rest.startswith("started") and run_start is None:
            run_start = mono
        elif event == "STEP_START":
            fields = STEP_FIELDS.match(rest)
            if not fields:
                continue
            current = {
                "index": int(fields.group(1)), "kind": fields.group(2),
                "label": fields.group(3), "mono": mono, "end": None,
                "status": "running", "error": None, "file": None,
                "phase": None, "log_line": line["number"],
            }
            steps.append(current)
        elif current is None:
            continue
        elif event == "PASS" and rest == current["label"]:
            current.update(end=mono, status="passed")
        elif event == "FAILED" and rest.startswith(current["label"] + ": "):
            current.update(
                end=mono, status="failed",
                error=rest[len(current["label"]) + 2:])
        elif event == "CAPTURE_QUEUED" and current["end"] is None and (
                EVENT_TIME.sub("", rest) == current["label"]):
            current.update(end=mono, status="passed")
        elif event == "CAPTURE" and current["file"] is None:
            label, _separator, filename = rest.rpartition(" ")
            if label == current["label"]:
                current["file"] = filename
    suite["run_start_mono"] = run_start
    phases = {}
    for row in reactor:
        index = number(row.get("step_index"))
        if index is not None and row.get("phase"):
            phases.setdefault(int(index), row["phase"])
    phase = None
    for step in steps:
        phase = phases.get(step["index"], phase)
        step["phase"] = phase
        step["duration"] = (
            max(0.0, step["end"] - step["mono"])
            if step["end"] is not None else None)
        step["video"] = video_time(suite, mono=step["mono"])
    return steps


def video_time(suite, epoch=None, mono=None):
    """Recording seconds for a printer epoch or monotonic time, or None."""
    started = suite.get("started_epoch")
    if started is None:
        return None
    if epoch is None:
        run_start = suite.get("run_start_mono")
        if mono is None or run_start is None:
            return None
        epoch = started + (mono - run_start)
    return suite.get("anchor", 0.0) + (epoch - started)


def _load_suite(directory, item, problems):
    suite = dict(item)
    suite["anchor"] = number(item.get("timeline_start_seconds"), 0.0)
    artifact = item.get("artifact")
    path = (directory / artifact) if artifact else None
    if path is not None and not path.is_dir():
        problems.append("%s: artifact directory %s is missing" % (
            item.get("name"), artifact))
        path = None
    suite["dir"] = path
    suite["files"] = _file_list(path)
    empty_list, empty_dict = [], {}
    if path is None:
        suite.update(
            summary=empty_dict, environment=empty_dict, contexts=empty_dict,
            manifest=empty_list, timing={}, timing_rows=empty_list,
            reactor=empty_list,
            run_log=empty_list, printer_log=empty_list, steps=empty_list,
            failures=empty_list, started_epoch=None, run_start_mono=None)
        return suite
    local = []
    summary = _read_json(path / "summary.json", local) or {}
    manifest = _read_json(path / "manifest.json", local)
    timing_rows = _read_table(path / "artifact_timing.csv", local)
    reactor = _read_table(path / "reactor.csv", local)
    run_log = [
        _log_line(number, text) for number, text in enumerate(
            (_read_text(path / "run.log", local) or "").splitlines(), 1)]
    printer_log = [
        _log_line(number, text) for number, text in enumerate(
            (_read_text(path / summary.get("printer_log", "printer.log"),
                        local) or "").splitlines(), 1)]
    suite.update(
        summary=summary if isinstance(summary, dict) else {},
        environment=_read_json(path / "environment.json", local) or {},
        contexts=_read_json(path / "operation_context.json", local) or {},
        manifest=manifest if isinstance(manifest, list) else [],
        timing={row.get("file"): row for row in timing_rows if row.get("file")},
        timing_rows=timing_rows, reactor=reactor, run_log=run_log,
        printer_log=printer_log,
        started_epoch=number(summary.get("started_at")),
        failures=list(summary.get("failures") or []))
    suite["steps"] = _parse_steps(run_log, reactor, suite)
    problems.extend("%s: %s" % (item.get("name"), text) for text in local)
    return suite


def _telemetry(directory, origin, problems):
    records = _read_jsonl(directory / "telemetry.jsonl", problems)
    for record in records:
        epoch = number(record.get("time"))
        record["x"] = (epoch - origin) if (
            epoch is not None and origin is not None) else None
    return [item for item in records if item["x"] is not None][:SERIES_LIMIT]


def _resources(directory, origin, problems):
    """Per-role samples with CPU percent derived from cumulative ticks."""
    rows = _read_table(directory / "resources.tsv", problems, "\t")
    roles = {}
    last = {}
    for row in rows:
        role = row.get("role")
        uptime = number(row.get("uptime"))
        epoch = number(row.get("epoch"))
        ticks = number(row.get("cpu_ticks"))
        if not role or uptime is None or epoch is None:
            continue
        cpu = None
        previous = last.get(role)
        if previous and ticks is not None and uptime > previous[0]:
            cpu = max(0.0, (ticks - previous[1]) / (
                (uptime - previous[0]) * CPU_TICKS_PER_SECOND) * 100.0)
        last[role] = (uptime, ticks)
        roles.setdefault(role, []).append({
            "x": (epoch - origin) if origin is not None else None,
            "cpu": cpu, "rss_kb": number(row.get("rss_kb")),
            "threads": number(row.get("threads")),
            "load1": number(row.get("load1")),
            "mem_available_kb": number(row.get("mem_available_kb")),
            "swap_free_kb": number(row.get("swap_free_kb")),
            "command": row.get("command"),
        })
    return {
        role: [item for item in samples if item["x"] is not None]
        for role, samples in roles.items()}


def load(directory):
    """Read a run directory; unreadable parts become entries in ``problems``."""
    directory = pathlib.Path(directory).resolve()
    problems = []
    report = _read_json(directory / "report.json", problems)
    if not isinstance(report, dict):
        report = {}
    if not (directory / "report.json").is_file():
        problems.append("report.json is missing; suites cannot be listed")
    suites = [
        _load_suite(directory, item, problems)
        for item in report.get("suites") or () if isinstance(item, dict)]
    anchor = next(
        (item for item in suites if item.get("started_epoch") is not None),
        None)
    origin = (
        anchor["started_epoch"] - anchor["anchor"] if anchor else None)
    return {
        "directory": directory, "report": report, "suites": suites,
        "origin": origin,
        "telemetry": _telemetry(directory, origin, problems),
        "resources": _resources(directory, origin, problems),
        "files": _file_list(directory), "problems": problems,
    }


def _quote(path):
    return urllib.parse.quote(str(path), safe="/")


def frames(data):
    """Every captured screen across suites, numbered for modal links."""
    found = []
    for suite in data["suites"]:
        for record in suite["manifest"]:
            if not isinstance(record, dict) or not record.get("file"):
                continue
            timing = suite["timing"].get(record["file"]) or {}
            failed = record.get("passed") is False or (
                str(timing.get("success")).lower() == "false")
            found.append({
                "number": len(found) + 1, "suite": suite, "record": record,
                "timing": timing, "failed": failed,
                "label": record.get("label") or record["file"],
                "phase": record.get("phase") or "",
                "page": record.get("page") or "",
                "kind": record.get("capture_kind") or "semantic",
                "video": video_time(
                    suite, epoch=number(record.get("time"))),
                "url": _quote("%s/%s" % (suite["artifact"], record["file"]))
                if suite["artifact"] else None,
            })
    return found
