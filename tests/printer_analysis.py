## Decide what in a printer-regression run deserves a human look.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Turn loaded run data into verdict-level findings and a health scorecard.

The policy lives here so it is easy to read and change.  A healthy run is
noisy: the reactor is often a few hundred milliseconds late and the render test
restarts Typer on purpose.  Fixed thresholds would either cry wolf or stay
silent, so performance numbers are judged against the same measurements from
earlier *passing* runs of the same suites in the same artifact directory.  With
too little history a number is shown without a verdict.  Only objectively bad
things (failed steps, scenarios or captures, renderer errors, real log errors)
are flagged without history.  Motion stalls are not: the Z paper test jogs in
tiny manual steps and stalls the buffer by design, so they are judged like any
other number.
"""

import json
import pathlib
import statistics

from tests import printer_charts as charts
from tests import printer_run_data as run_data

HISTORY_RUNS = 10
MIN_HISTORY = 3
UNUSUAL_FACTOR = 1.5
MAX_LISTED = 8

# key, title, unit, plain-language meaning, page, chart anchor, higher is worse
MEASURES = (
    ("reactor_lag", "Reactor lag", "ms",
     "How late Klipper's scheduler ran at its worst. Large lag delays taps "
     "and screen captures.", "performance", "reactor-lag", True),
    ("late_ticks", "Late reactor ticks", "",
     "Seconds in which the 5 Hz test timer missed a 200 ms deadline.",
     "performance", "late-ticks", True),
    ("capture_p95", "Capture time (p95)", "ms",
     "Time to grab one screen frame; 95% of captures were faster.",
     "performance", "capture-pipeline", True),
    ("queue_delay", "Capture queue delay", "ms",
     "Longest wait of a capture request before its work started.",
     "performance", "capture-pipeline", True),
    ("renderer_restarts", "Typer restarts", "",
     "How often the screen renderer restarted. The render test restarts it "
     "once on purpose.", "performance", "render-queue", True),
    ("dropped_batches", "Dropped render batches", "",
     "Screen updates the renderer discarded under load.", "performance",
     "render-batches", True),
    ("telemetry_failures", "Telemetry gaps", "",
     "Status samples the host could not read.", "telemetry", "temperatures",
     True),
    ("stalls", "Motion stalls", "",
     "Times the toolhead ran out of queued moves.", "telemetry",
     "buffer-stalls", True),
    ("mcu_rtt", "MCU round trip", "ms",
     "Worst host-to-MCU round-trip time.", "telemetry", "mcu-rtt", True),
    ("min_memory", "Lowest free memory", "MB",
     "Smallest available memory on the printer; very low values risk the "
     "out-of-memory killer.", "performance", "memory", False),
    ("duration", "Run time", "s",
     "Total time from start to finish of the run.", "run", None, True),
)


def _link(kind, label, **fields):
    return dict(fields, kind=kind, label=label)


def measure(data):
    """Headline numbers of a run: ``{key: {"value", "where"}}`` for those seen."""
    found = {}

    def put(key, value, **where):
        if value is not None:
            found[key] = {"value": value, "where": where}

    peak = None
    late = 0
    restarts = dropped = 0
    durations, delays = [], []
    for suite in data["suites"]:
        for row in suite["reactor"]:
            lag = run_data.number(row.get("max_lag_ms"))
            late += int(run_data.number(row.get("missed_deadlines"), 0))
            if lag is not None and (peak is None or lag > peak[0]):
                peak = (lag, {
                    "video": run_data.video_time(
                        suite, epoch=run_data.number(row.get("time"))),
                    "label": row.get("step"), "phase": row.get("phase"),
                    "suite": suite["name"]})
        for row in suite["timing_rows"]:
            duration = run_data.number(row.get("duration_ms"))
            delay = run_data.number(row.get("queue_delay_ms"))
            if duration is not None:
                durations.append(duration)
            if delay is not None:
                delays.append((delay, row.get("label"), suite["name"]))
        for record in suite["manifest"]:
            renderer = (record or {}).get("renderer") or {}
            restarts = max(restarts, renderer.get("typer_restarts") or 0)
            dropped = max(dropped, renderer.get("dropped_batches") or 0)
    if peak:
        put("reactor_lag", peak[0], **peak[1])
        put("late_ticks", float(late))
    summary = run_data.summarize(durations)
    if summary:
        put("capture_p95", summary["p95"])
    if delays:
        worst = max(delays)
        put("queue_delay", worst[0], label=worst[1], suite=worst[2])
    if any(suite["manifest"] for suite in data["suites"]):
        put("renderer_restarts", float(restarts))
        put("dropped_batches", float(dropped))
    telemetry = data["telemetry"]
    report_telemetry = data["report"].get("telemetry") or {}
    if telemetry or report_telemetry.get("failure_count") is not None:
        put("telemetry_failures", run_data.number(
            report_telemetry.get("failure_count")))
    stalls = [
        (item.get("buffer") or {}).get("stalls") for item in telemetry]
    stalls = [value for value in stalls if isinstance(value, (int, float))]
    if stalls:
        put("stalls", float(max(stalls)))
    rtts = [(item.get("mcu") or {}).get("srtt") for item in telemetry]
    rtts = [value for value in rtts if isinstance(value, (int, float))]
    if rtts:
        put("mcu_rtt", max(rtts) * 1000.0)
    memory = [
        item["mem_available_kb"] for item in data["resources"].get("system", ())
        if item["mem_available_kb"] is not None]
    if memory:
        put("min_memory", min(memory) / 1024.0)
    put("duration", run_data.number(data["report"].get("duration_seconds")))
    return found


def history(directory, requested, limit=HISTORY_RUNS):
    """Measurements of earlier passing runs of the same suites, newest first."""
    directory = pathlib.Path(directory).resolve()
    past = []
    try:
        siblings = sorted(
            (item for item in directory.parent.iterdir()
             if item.is_dir() and item.name < directory.name), reverse=True)
    except OSError:
        return past
    for sibling in siblings:
        try:
            report = json.loads(
                (sibling / "report.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(report, dict) or report.get("status") != "passed":
            continue
        if (report.get("requested_suites") or []) != requested:
            continue
        past.append(measure(run_data.load(sibling)))
        if len(past) >= limit:
            break
    return past


def judge(value, past, higher_is_worse=True):
    """``"unusual"``, ``"normal"``, or ``"unknown"`` without enough history."""
    if len(past) < MIN_HISTORY:
        return "unknown"
    median = statistics.median(past)
    if higher_is_worse:
        unusual = value > max(past) and value > UNUSUAL_FACTOR * median
    else:
        unusual = value < min(past) and value < median / UNUSUAL_FACTOR
    return "unusual" if unusual else "normal"


def _format(key, unit, value):
    if key == "duration":
        return charts.format_time(value)
    text = "%d" % round(value) if value >= 10 or value == int(value) else (
        "%.1f" % value)
    return text + (" " + unit if unit else "")


def scorecard(measured, past_runs):
    """One tile per measurement, judged against earlier runs when possible."""
    tiles = []
    for key, title, unit, meaning, page, anchor, higher in MEASURES:
        if key not in measured:
            continue
        value = measured[key]["value"]
        past = [item[key]["value"] for item in past_runs if key in item]
        verdict = judge(value, past, higher)
        if past:
            low, high = min(past), max(past)
            typical = "typical %s (%d run%s)" % (
                _format(key, unit, high) if low == high else "%s–%s" % (
                    _format(key, "", low), _format(key, unit, high)),
                len(past), "" if len(past) == 1 else "s")
        else:
            typical = "no earlier runs to compare"
        tiles.append({
            "key": key, "title": title, "meaning": meaning,
            "value": _format(key, unit, value), "verdict": verdict,
            "typical": typical, "page": page, "anchor": anchor,
            "where": measured[key]["where"],
        })
    return tiles


def _failed_steps(data):
    for suite in data["suites"]:
        for step in suite["steps"]:
            if step["status"] == "failed":
                yield suite, step


def findings(data, tiles):
    """Things to look at, worst first: failures before unusual numbers."""
    report = data["report"]
    failures, notes = [], []
    error = report.get("infrastructure_error")
    if isinstance(error, dict):
        failures.append({
            "severity": "fail", "title": "Infrastructure failure",
            "detail": "%s: %s" % (
                error.get("category", "error"), error.get("message", "")),
            "links": [_link("run", "Run details")]})
    suites = data["suites"]
    failed_labels = {(s["name"], st["label"]) for s, st in _failed_steps(data)}
    for suite, step in _failed_steps(data):
        failures.append({
            "severity": "fail",
            "title": "Step failed: %s" % step["label"],
            "detail": step["error"] or "The step reported a failure.",
            "links": [
                _link("video", "Video", seconds=step["video"]),
                _link("screen_before", "Last screen", suite=suite["name"],
                      seconds=step["video"]),
                _link("steps", "Step", query={
                    "suite": suite["name"], "status": "failed"}),
                _link("logs", "Log", suite=suite["name"],
                      line=step["log_line"]),
            ]})
    for suite in suites:
        for item in suite["failures"]:
            if isinstance(item, dict) and (
                    suite["name"], item.get("step")) not in failed_labels:
                failures.append({
                    "severity": "fail",
                    "title": "Failure in %s: %s" % (
                        suite["name"], item.get("step") or "suite"),
                    "detail": item.get("error") or "",
                    "links": [_link("logs", "Log", suite=suite["name"])]})
        reported = isinstance(error, dict) and suite.get("reason") == (
            error.get("message"))
        if suite.get("status") not in ("passed", "skipped") and not (
                reported or suite["failures"] or any(
                    st["status"] == "failed" for st in suite["steps"])):
            failures.append({
                "severity": "fail",
                "title": "Suite %s: %s" % (
                    suite["name"],
                    str(suite.get("status")).replace("_", " ")),
                "detail": suite.get("reason") or "",
                "links": [_link("logs", "Log", suite=suite["name"])]})
        bad = [
            item for item in (suite["contexts"] or {}).get("scenarios") or ()
            if isinstance(item, dict) and not item.get("passed", True)]
        for item in bad[:MAX_LISTED]:
            failures.append({
                "severity": "fail",
                "title": "Scenario failed: %s" % item.get("scenario"),
                "detail": item.get("diagnostic") or "Context mismatch.",
                "links": [_link("checks", "Diff")]})
    frames = run_data.frames(data)
    # A failed capture step is already reported as a failed step.
    bad_frames = [
        item for item in frames if item["failed"]
        and (item["suite"]["name"], item["label"]) not in failed_labels]
    if bad_frames:
        failures.append({
            "severity": "fail",
            "title": "%d screen capture(s) failed" % len(bad_frames),
            "detail": ", ".join(item["label"] for item in bad_frames[
                :MAX_LISTED]),
            "links": [_link("screens", "Screens", query={"status": "failed"})]})
    worker = [
        item for item in frames
        if (item["record"].get("renderer") or {}).get("worker_last_error")]
    if worker:
        notes.append({
            "severity": "warn", "title": "Screen renderer reported an error",
            "detail": (worker[0]["record"]["renderer"]["worker_last_error"]),
            "links": [_link("screens", "Screen", frame=worker[0]["number"])]})
    log_errors = [
        (suite["name"], line) for suite in suites
        for line in suite["printer_log"] if line["level"] == "error"]
    if log_errors:
        name, line = log_errors[0]
        notes.append({
            "severity": "warn",
            "title": "%d error line(s) in the printer log" % len(log_errors),
            "detail": line["text"][:300],
            "links": [_link("logs", "Log", suite=name, stream="printer",
                            line=line["number"])]})
    for tile in tiles:
        if tile["verdict"] != "unusual":
            continue
        links = [_link("chart", "Chart", page=tile["page"],
                       anchor=tile["anchor"])] if tile["anchor"] else []
        where = tile["where"]
        if where.get("video") is not None:
            links.append(_link("video", "Video", seconds=where["video"]))
        place = ""
        if where.get("label"):
            place = " at %s" % where["label"]
        notes.append({
            "severity": "warn",
            "title": "%s is unusually %s" % (
                tile["title"], "high" if tile["key"] != "min_memory"
                else "low"),
            "detail": "%s%s — %s." % (tile["value"], place, tile["typical"]),
            "links": links})
    return failures + notes


def results(data):
    """One row per test phase (with its context scenarios), per suite."""
    rows = []
    frames = run_data.frames(data)
    for suite in data["suites"]:
        scenarios = [
            item for item in (suite["contexts"] or {}).get("scenarios") or ()
            if isinstance(item, dict)]
        used = set()
        phases = {}
        for step in suite["steps"]:
            if step["phase"]:
                phases.setdefault(step["phase"], []).append(step)
        for phase, steps in phases.items():
            mine = [
                (index, item) for index, item in enumerate(scenarios)
                if str(item.get("scenario", "")).startswith(phase)]
            used.update(index for index, _item in mine)
            starts = [item["video"] for item in steps if item["video"]]
            ends = [
                item["video"] + (item["duration"] or 0.0)
                for item in steps if item["video"] is not None]
            failed = sum(1 for item in steps if item["status"] == "failed")
            rows.append({
                "suite": suite["name"], "name": phase, "steps": len(steps),
                "failed": failed,
                "seconds": (max(ends) - min(starts)) if starts and ends
                else None,
                "video": min(starts) if starts else None,
                "screens": sum(
                    1 for item in frames
                    if item["suite"] is suite and item["phase"] == phase),
                "scenarios": [item for _index, item in mine],
                "status": "failed" if failed or any(
                    not item.get("passed", True) for _i, item in mine)
                else "passed"})
        for index, item in enumerate(scenarios):
            if index not in used:
                rows.append({
                    "suite": suite["name"], "name": item.get("scenario"),
                    "steps": 0, "failed": 0, "seconds": None, "video": None,
                    "screens": 0, "scenarios": [item],
                    "status": "passed" if item.get("passed", True)
                    else "failed"})
    return rows


def assess(data, past_runs=None):
    """Everything the dashboard shows: tiles, findings, per-phase results."""
    past_runs = [] if past_runs is None else past_runs
    tiles = scorecard(measure(data), past_runs)
    return {
        "tiles": tiles, "findings": findings(data, tiles),
        "results": results(data), "history_runs": len(past_runs),
    }
