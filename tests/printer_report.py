## Offline HTML report for host-orchestrated printer-regression runs.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Render a multi-page review of one printer-regression run directory.

The report is rebuilt from the artifacts on disk (see ``printer_run_data``),
so ``python -m tests.printer_report RUN_DIR`` regenerates it for any earlier
run without contacting a printer.  Pages share the stylesheet and script of the
UI-regression report and add printer-specific charts, logs, and screens.
"""

import argparse
import html
import json
import pathlib
import shutil
import sys
import urllib.parse

from tests import printer_analysis as analysis
from tests import printer_charts as charts
from tests import printer_run_data as run_data

ASSETS = pathlib.Path(__file__).with_name("report_assets")
ASSET_NAMES = ("report.css", "report.js", "printer.css", "printer.js")

PAGES = (
    ("overview", "Overview"),
    ("steps", "Steps"),
    ("screens", "Screens"),
    ("telemetry", "Telemetry"),
    ("performance", "Performance"),
    ("checks", "Checks"),
    ("logs", "Logs"),
    ("run", "Run"),
)

GOOD = ("passed", "completed", "recorded", "ok", "success")
BAD = ("failed", "error", "infrastructure_error", "invalid")
WARN = ("partial", "unavailable", "running", "unfinished", "warn")
LONG_TEXT = 260
SMALL_GROUP = 6
SLOWEST_STEPS = 10
PALETTE = {
    "nozzle": "#fb923c", "bed": "#60a5fa", "green": "#4ade80",
    "yellow": "#facc15", "pink": "#f472b6", "cyan": "#22d3ee",
    "violet": "#a78bfa", "grey": "#94a3b8",
}


def _text(value, fallback="—"):
    if value is None or value == "":
        value = fallback
    return html.escape(str(value), quote=True)


def _tone(value):
    value = str(value or "").lower()
    if value in GOOD:
        return "pass"
    if value in BAD:
        return "fail"
    if value in WARN:
        return "warn"
    return "muted"


def _badge(value, label=None):
    return '<span class="badge %s">%s</span>' % (
        _tone(value), _text(label or str(value or "unknown").replace("_", " ")))


def _tokens(values):
    """Encode values as the ``|a|b|`` list the page script matches against."""
    return _text("|" + "|".join(str(item) for item in values) + "|")


def _quote(path):
    return urllib.parse.quote(str(path), safe="/")


def _duration(seconds):
    if seconds is None:
        return "—"
    if seconds < 1:
        return "%d ms" % round(seconds * 1000)
    if seconds < 60:
        return "%.1f s" % seconds
    return charts.format_time(seconds)


def _size(value):
    value = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return ("%d %s" if unit == "B" else "%.1f %s") % (value, unit)
        value /= 1024


def _number(value, digits=1, fallback="—"):
    return fallback if value is None else ("%." + str(digits) + "f") % value


def _page_names(entry):
    entry = pathlib.PurePosixPath(entry)
    return {
        slug: entry.name if slug == "overview"
        else "%s-%s.html" % (entry.stem, slug)
        for slug, _title in PAGES
    }


def _link(names, slug, fragment="", **query):
    text = urllib.parse.urlencode(query)
    return html.escape(
        names[slug] + ("?" + text if text else "") + fragment, quote=True)


def _chips(name, label, options, default=None):
    """A filter button group; ``options`` are (value, label, count) rows."""
    buttons = "".join(
        '<button type="button" data-value="%s" aria-pressed="false">%s%s'
        "</button>" % (
            _text(value), _text(text),
            '<span class="chip-count">%d</span>' % count
            if count is not None else "")
        for value, text, count in options)
    return (
        '<span class="toolbar-label">%s</span>'
        '<div class="chips" data-filter="%s"%s>'
        '<button type="button" data-value="all" aria-pressed="false">All'
        "</button>%s</div>" % (
            _text(label), name,
            ' data-default="%s"' % _text(default) if default else "",
            buttons))


def _select(name, label, values):
    return (
        '<span class="toolbar-label">%s</span><select data-filter="%s" '
        'aria-label="%s"><option value="all">All</option>%s</select>' % (
            _text(label), name, _text(label), "".join(
                '<option value="%s">%s</option>' % (_text(item), _text(item))
                for item in sorted(set(values)))))


def _search_box(placeholder="Search…"):
    return (
        '<input type="search" data-search placeholder="%s" '
        'aria-label="Search">' % _text(placeholder))


def _toolbar(*parts, label="Filter"):
    return (
        '<nav class="toolbar" aria-label="%s">%s<span class="spacer"></span>'
        '<span class="result-count" data-count></span></nav>' % (
            _text(label), "".join(parts)))


def _facets(items, name, key, labels=None):
    """Chips for the distinct values of ``key`` over ``items``."""
    counts = {}
    for item in items:
        value = key(item)
        if value not in (None, ""):
            counts[value] = counts.get(value, 0) + 1
    return [
        (value, (labels or {}).get(value, value), counts[value])
        for value in sorted(counts)]


# --- derived views ----------------------------------------------------------


def _steps(data):
    return [
        dict(step, suite=suite) for suite in data["suites"]
        for step in suite["steps"]]


def _failures(data):
    """One entry per failed step, plus suite-level failures without a step."""
    failures = []
    for suite in data["suites"]:
        seen = set()
        for step in suite["steps"]:
            if step["status"] == "failed":
                seen.add(step["label"])
                failures.append({
                    "suite": suite, "label": step["label"],
                    "error": step["error"], "video": step["video"],
                    "index": step["index"], "step": step,
                })
        for item in suite["failures"]:
            if isinstance(item, dict) and item.get("step") not in seen:
                failures.append({
                    "suite": suite, "label": item.get("step") or "(suite)",
                    "error": item.get("error"), "video": None, "index": None,
                    "step": None,
                })
    return failures


def _scenarios(data):
    rows = []
    for suite in data["suites"]:
        for item in (suite["contexts"] or {}).get("scenarios") or ():
            if isinstance(item, dict):
                rows.append(dict(item, suite=suite))
    return rows


def _phase_bands(data):
    """Contiguous (start, end, phase) spans on the recording axis."""
    bands = []
    for suite in data["suites"]:
        steps = [item for item in suite["steps"] if item["video"] is not None]
        for step in steps:
            end = step["video"] + (step["duration"] or 0.0)
            if bands and bands[-1][2] == step["phase"] and (
                    step["video"] - bands[-1][1] < 30.0):
                bands[-1] = (bands[-1][0], end, step["phase"])
            elif step["phase"]:
                bands.append((step["video"], end, step["phase"]))
    return bands


def _markers(data):
    markers = []
    for suite in data["suites"]:
        if suite["started_epoch"] is not None and len(data["suites"]) > 1:
            markers.append((
                suite["anchor"], "%s started" % suite["name"], "suite"))
    for failure in _failures(data):
        if failure["video"] is not None:
            markers.append((
                failure["video"], "FAILED: %s" % failure["label"], "fail"))
    return markers


def _duration_total(data):
    value = data["report"].get("duration_seconds")
    return value if isinstance(value, (int, float)) else None


def _x_max(data):
    ends = [
        item["x"] for item in data["telemetry"]] + [
        _duration_total(data) or 0.0]
    for suite in data["suites"]:
        for step in suite["steps"]:
            if step["video"] is not None:
                ends.append(step["video"] + (step["duration"] or 0.0))
    return max(ends + [1.0])


# --- shared fragments -------------------------------------------------------


def _long_text(value, limit=LONG_TEXT):
    value = "" if value is None else str(value)
    if len(value) <= limit:
        return "<span>%s</span>" % _text(value)
    return (
        "<details><summary>%s…</summary><pre class=\"wrap\">%s</pre>"
        "</details>" % (_text(value[:limit]), _text(value)))


def _video_link(names, seconds, label=None, plain=False):
    if seconds is None:
        return ""
    href = _link(names, "run", "#t=%.1f" % max(0.0, seconds - 2.0))
    text = "▶ %s" % (label or charts.format_time(seconds))
    if plain:
        return '<a class="seek" href="%s">%s</a>' % (href, _text(text))
    return _pill("video", href, text, "Open the recording at this moment")


def _definition_list(rows):
    return "<dl>%s</dl>" % "".join(
        "<div><dt>%s</dt><dd>%s</dd></div>" % (_text(name), value)
        for name, value in rows)


def _thermal_chart(data, x_max, height=190):
    telemetry = data["telemetry"]

    def series(name, color, key, sub, dash=False):
        return {
            "name": name, "color": color, "dash": dash,
            "points": [
                (item["x"], (item.get(key) or {}).get(sub))
                for item in telemetry]}

    return charts.line_chart(
        "Temperatures", [
            series("Nozzle", PALETTE["nozzle"], "nozzle", "temperature"),
            series("Nozzle target", PALETTE["nozzle"], "nozzle", "target",
                   True),
            series("Bed", PALETTE["bed"], "bed", "temperature"),
            series("Bed target", PALETTE["bed"], "bed", "target", True),
        ], x_max, unit="°C", bands=_phase_bands(data),
        markers=_markers(data), y_min=0, height=height, anchor="temperatures")


# --- pages ------------------------------------------------------------------


def _pill(kind, href, label, title=None):
    """A clearly separate action button; ``kind`` picks its color."""
    return '<a class="pill %s" href="%s"%s>%s</a>' % (
        kind, href, ' title="%s"' % _text(title) if title else "",
        _text(label))


def _nearest_frame(frames, suite_name, seconds):
    before = [
        frame for frame in frames if frame["suite"]["name"] == suite_name
        and frame["video"] is not None and frame["video"] <= seconds + 0.5]
    return max(before, key=lambda frame: frame["video"], default=None)


def _action(spec, names, frames):
    """Render one analysis link spec as a pill, or nothing if it has no target."""
    kind, label = spec["kind"], spec["label"]
    if kind == "video":
        seconds = spec.get("seconds")
        if seconds is None:
            return ""
        return _pill("video", _link(
            names, "run", "#t=%.1f" % max(0.0, seconds - 2.0)),
            "▶ %s %s" % (label, charts.format_time(seconds)),
            "Open the recording at this moment")
    if kind == "screen_before":
        if spec.get("seconds") is None:
            return ""
        frame = _nearest_frame(frames, spec["suite"], spec["seconds"])
        return _pill("screen", _link(
            names, "screens", "#frame-%d" % frame["number"]),
            "%s: %s" % (label, frame["label"]),
            "The last screen captured before this moment") if frame else ""
    if kind == "screens":
        fragment = "#frame-%d" % spec["frame"] if "frame" in spec else ""
        return _pill("screen", _link(
            names, "screens", fragment, **spec.get("query", {})), label)
    if kind == "steps":
        return _pill("steps", _link(
            names, "steps", **spec.get("query", {})), label)
    if kind == "logs":
        stream = spec.get("stream", "run")
        fragment = "#%s-%s-%d" % (
            stream, spec["suite"], spec["line"]) if "line" in spec else ""
        return _pill("log", _link(
            names, "logs", fragment, suite=spec["suite"], stream=stream,
            group="all"), label)
    if kind == "chart":
        return _pill("chart", _link(
            names, spec["page"], "#chart-%s" % spec["anchor"]), label)
    return _pill(
        "checks" if kind == "checks" else "page", _link(names, kind), label)


def _actions(specs, names, frames):
    return "".join(_action(spec, names, frames) for spec in specs)


def _finding(item, names, frames):
    return (
        '<article class="finding %s"><div class="finding-text"><h3>%s</h3>'
        '<div class="detail">%s</div></div><div class="actions">%s</div>'
        "</article>" % (
            item["severity"], _text(item["title"]),
            _long_text(item["detail"], 360),
            _actions(item["links"], names, frames)))


def _verdict(data, assessment):
    report = data["report"]
    steps, frames = _steps(data), run_data.frames(data)
    failed_steps = sum(1 for item in steps if item["status"] == "failed")
    failed_frames = sum(1 for item in frames if item["failed"])
    scenarios = _scenarios(data)
    failed_scenarios = sum(
        1 for item in scenarios if not item.get("passed", True))
    findings = assessment["findings"]
    failures = sum(1 for item in findings if item["severity"] == "fail")
    notes = len(findings) - failures
    facts = [
        "%d steps%s" % (len(steps), " (%d failed)" % failed_steps
                        if failed_steps else " passed"),
        "%d scenarios%s" % (len(scenarios), " (%d failed)" % failed_scenarios
                            if failed_scenarios else " passed"),
        "%d screens%s" % (len(frames), " (%d failed)" % failed_frames
                          if failed_frames else ""),
        _duration(_duration_total(data)),
    ]
    if not findings:
        summary = "Nothing needs attention."
    else:
        summary = "%s%s%s to look at." % (
            "%d failure%s" % (failures, "" if failures == 1 else "s")
            if failures else "",
            " and " if failures and notes else "",
            "%d note%s" % (notes, "" if notes == 1 else "s")
            if notes else "")
    history = assessment["history_runs"]
    return (
        '<section class="verdict %s"><div class="verdict-status">%s</div>'
        '<div><p class="verdict-line">%s</p><p class="muted">%s'
        "</p></div></section>" % (
            _tone(report.get("status")),
            _text(str(report.get("status") or "unknown").upper()),
            _text(summary), _text(" · ".join(facts) + (
                " · compared with %d earlier run%s" % (
                    history, "" if history == 1 else "s")
                if history else " · no earlier runs to compare with"))))


def _results_table(assessment, names):
    rows = []
    for row in assessment["results"]:
        scenarios = row["scenarios"]
        badges = "".join(
            '<span class="mini-badge %s" title="%s">%s</span>' % (
                "pass" if item.get("passed", True) else "fail",
                _text("%s: %s" % (item.get("scenario"), item.get("variant"))),
                _text(item.get("variant") or item.get("scenario")))
            for item in scenarios)
        links = "".join((
            _pill("steps", _link(names, "steps", suite=row["suite"],
                                 phase=row["name"]), "Steps")
            if row["steps"] else "",
            _pill("video", _link(names, "run", "#t=%.1f" % max(
                0.0, row["video"] - 2.0)), "▶ Video")
            if row["video"] is not None else "",
            _pill("screen", _link(names, "screens", suite=row["suite"],
                                  phase=row["name"]), "Screens")
            if row["screens"] else "",
            _pill("checks", _link(names, "checks"), "Checks")
            if scenarios else ""))
        rows.append(
            '<tr class="%s"><td><strong>%s</strong><br><span class="muted">'
            "%s</span></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
            '<td>%s</td><td class="actions">%s</td></tr>' % (
                _tone(row["status"]), _text(row["name"]), _text(row["suite"]),
                _badge(row["status"]),
                "%d%s" % (row["steps"], " · %d failed" % row["failed"]
                          if row["failed"] else "") if row["steps"] else "—",
                row["screens"] or "—", _text(_duration(row["seconds"])),
                badges or "—", links))
    if not rows:
        return '<p class="empty">No test results were recorded.</p>'
    return (
        '<table class="results"><thead><tr><th>Test</th><th>Result</th>'
        "<th>Steps</th><th>Screens</th><th>Duration</th>"
        "<th>Context scenarios</th><th>Open</th></tr></thead><tbody>%s"
        "</tbody></table>" % "".join(rows))


def _scorecard(assessment, names):
    tiles = []
    for tile in assessment["tiles"]:
        link = ""
        if tile["anchor"]:
            link = '<a href="%s">Chart →</a>' % _link(
                names, tile["page"], "#chart-%s" % tile["anchor"])
        tiles.append(
            '<article class="tile %s"><h3><span class="dot %s"></span>%s</h3>'
            '<strong class="value">%s</strong><p class="meaning">%s</p>'
            '<p class="typical">%s</p>%s</article>' % (
                {"unusual": "warn", "normal": "pass"}.get(
                    tile["verdict"], "muted"),
                {"unusual": "warn", "normal": "pass"}.get(
                    tile["verdict"], ""),
                _text(tile["title"]), _text(tile["value"]),
                _text(tile["meaning"]), _text(tile["typical"]), link))
    if not tiles:
        return '<p class="empty">No performance data was recorded.</p>'
    note = ""
    if assessment["history_runs"] < analysis.MIN_HISTORY:
        note = (
            '<p class="muted">Fewer than %d earlier passing runs of these '
            "suites exist, so the numbers below are shown without a verdict."
            "</p>" % analysis.MIN_HISTORY)
    return note + '<div class="tiles">%s</div>' % "".join(tiles)


def _suite_card(suite, names):
    steps = suite["steps"]
    name = suite["name"]
    facts = [
        ("Printer suite", _text(suite.get("printer_suite"))),
        ("Duration", _text(_duration(suite.get("duration_seconds")))),
        ("Steps", _text(len(steps))),
        ("Screens", _text(suite.get("screenshot_count", 0))),
        ("Recording offset", _text(charts.format_time(suite["anchor"]))),
    ]
    return (
        '<article class="suite-card %s"><header><h3>%s</h3>%s</header>%s%s'
        '<p class="actions">%s%s%s%s</p></article>' % (
            _tone(suite.get("status")), _text(name),
            _badge(suite.get("status")),
            '<p class="reason">%s</p>' % _long_text(suite["reason"])
            if suite.get("reason") else "", _definition_list(facts),
            _pill("steps", _link(names, "steps", suite=name), "Steps"),
            _pill("screen", _link(names, "screens", suite=name), "Screens"),
            _pill("log", _link(names, "logs", suite=name, stream="run"),
                  "Log"),
            _pill("checks", _link(names, "checks"), "Checks")))


def _overview(data, names, past_runs=None):
    assessment = analysis.assess(data, past_runs)
    frames = run_data.frames(data)
    body = [_verdict(data, assessment)]
    if assessment["findings"]:
        body.append(
            '<section><h2>Needs attention</h2><div class="findings">%s</div>'
            "</section>" % "".join(
                _finding(item, names, frames)
                for item in assessment["findings"]))
    body.append(_alerts(data))
    body.append(
        "<section><h2>Test results</h2>%s</section>" %
        _results_table(assessment, names))
    body.append(
        "<section><h2>Health</h2>%s</section>" %
        _scorecard(assessment, names))
    body.append(
        '<section><h2>Suites</h2><div class="suite-grid">%s</div></section>' %
        ("".join(_suite_card(item, names) for item in data["suites"])
         or '<p class="empty">No suites were recorded.</p>'))
    return "".join(body)


def _step_row(step, names):
    suite = step["suite"]
    name = suite["name"]
    screen = ""
    if step["file"] and suite["artifact"]:
        screen = _pill("screen", html.escape(
            _quote("%s/%s" % (suite["artifact"], step["file"])), quote=True),
            "Screen")
    status = step["status"] if step["status"] != "running" else "unfinished"
    return (
        '<tr id="step-%s-%d" data-item data-suite="%s" data-status="%s" '
        'data-kind="%s" data-phase="%s" data-search="%s"'
        ' class="%s"><td>%d</td><td>%s</td><td>%s</td><td>%s</td>'
        '<td class="label">%s%s</td><td>%s</td><td>%s</td><td>%s</td>'
        '<td class="actions">%s%s%s</td></tr>' % (
            _text(name), step["index"], _tokens([name]), _tokens([status]),
            _tokens([step["kind"]]), _tokens([step["phase"] or "none"]),
            _text(" ".join((
                step["label"], step["kind"], step["phase"] or "",
                step["error"] or "")).lower()),
            _tone(status), step["index"], _text(name),
            _text(step["phase"]), _text(step["kind"]), _text(step["label"]),
            '<div class="error">%s</div>' % _long_text(step["error"], 200)
            if step["error"] else "",
            _text(charts.format_time(step["video"]))
            if step["video"] is not None else "—",
            _text(_duration(step["duration"])), _badge(status),
            _video_link(names, step["video"], "Video"), screen,
            _pill("log", _link(
                names, "logs", "#run-%s-%d" % (name, step["log_line"]),
                suite=name, stream="run", group="all"), "Log")))


def _phase_table(data, names):
    rows = []
    for suite in data["suites"]:
        phases = {}
        for step in suite["steps"]:
            phases.setdefault(step["phase"] or "none", []).append(step)
        for phase, steps in phases.items():
            starts = [item["video"] for item in steps if item["video"]]
            ends = [
                item["video"] + (item["duration"] or 0.0)
                for item in steps if item["video"] is not None]
            failed = sum(1 for item in steps if item["status"] == "failed")
            rows.append(
                '<tr><td>%s</td><td><a href="%s">%s</a></td><td>%d</td>'
                "<td>%s</td><td>%s</td><td>%s</td></tr>" % (
                    _text(suite["name"]),
                    _link(names, "steps", suite=suite["name"], phase=phase),
                    _text(phase), len(steps),
                    _text(charts.format_time(min(starts))) if starts else "—",
                    _text(_duration(max(ends) - min(starts)))
                    if starts and ends else "—",
                    '<strong class="bad">%d</strong>' % failed if failed
                    else "0"))
    return (
        "<table><thead><tr><th>Suite</th><th>Phase</th><th>Steps</th>"
        "<th>Starts</th><th>Span</th><th>Failed</th></tr></thead>"
        "<tbody>%s</tbody></table>" % "".join(rows)) if rows else ""


def _steps_page(data, names):
    steps = _steps(data)
    if not steps:
        return (
            '<p class="empty">No step log was recorded for this run.</p>')
    slowest = sorted(
        (item for item in steps if item["duration"] is not None),
        key=lambda item: -item["duration"])[:SLOWEST_STEPS]
    slow_rows = "".join(
        '<tr><td><a href="%s">%s</a></td><td>%s</td><td>%s</td><td>%s</td>'
        "</tr>" % (
            _link(names, "steps", "#step-%s-%d" % (
                item["suite"]["name"], item["index"]),
                suite=item["suite"]["name"]),
            _text(item["label"]), _text(item["phase"]), _text(item["kind"]),
            _text(_duration(item["duration"]))) for item in slowest)
    names_seen = [item["suite"]["name"] for item in steps]
    toolbar = _toolbar(
        _chips("suite", "Suite", _facets(
            steps, "suite", lambda item: item["suite"]["name"]))
        if len(set(names_seen)) > 1 else "",
        _chips("status", "Status", _facets(
            steps, "status", lambda item: (
                "unfinished" if item["status"] == "running"
                else item["status"]))),
        _chips("kind", "Kind", _facets(steps, "kind", lambda i: i["kind"])),
        _select("phase", "Phase", [i["phase"] or "none" for i in steps]),
        _search_box("Search label, phase, error…"), label="Step filter")
    rows = "".join(_step_row(item, names) for item in steps)
    return (
        '<div class="two-column"><section><h2>Phases</h2>%s</section>'
        "<section><h2>Slowest steps</h2><table><thead><tr><th>Step</th>"
        "<th>Phase</th><th>Kind</th><th>Duration</th></tr></thead><tbody>"
        "%s</tbody></table></section></div>%s"
        '<table class="steps"><thead><tr><th>#</th><th>Suite</th>'
        "<th>Phase</th><th>Kind</th><th>Step</th><th>At</th><th>Duration</th>"
        "<th>Status</th><th>Links</th></tr></thead><tbody>%s</tbody></table>"
        % (_phase_table(data, names), slow_rows, toolbar, rows))


def _hit_boxes(record):
    parts = []
    hitboxes = record.get("hitboxes") or ()
    regions = (hitboxes.items() if isinstance(hitboxes, dict) else
               ((box["action"], box) for box in hitboxes))
    for name, box in regions:
        parts.append(_hit("hit", name, box))
    for name, box in (record.get("buttons") or {}).items():
        parts.append(_hit("hit button", "%s — %s (%s)" % (
            name, box.get("label", ""), box.get("state", "")), box))
    return "".join(parts)


def _hit(kind, title, box):
    try:
        return (
            '<i class="%s" style="left:%.3f%%;top:%.3f%%;width:%.3f%%;'
            'height:%.3f%%" title="%s"></i>' % (
                kind, box["x"] / 8.0, box["y"] / 4.8, box["width"] / 8.0,
                box["height"] / 4.8, _text(title)))
    except (KeyError, TypeError):
        return ""


def _frame_detail(frame, names):
    record, timing = frame["record"], frame["timing"]
    renderer = record.get("renderer") or {}
    temperatures = record.get("temperatures") or {}
    position = record.get("position") or ()
    buttons = record.get("buttons") or {}
    rows = [
        ("Suite", _text(frame["suite"]["name"])),
        ("Phase", _text(frame["phase"])),
        ("Page", _text(frame["page"])),
        ("Semantic page", _text(record.get("semantic_page_id"))),
        ("Capture kind", _text(frame["kind"])),
        ("Recording time", _video_link(names, frame["video"]) or "—"),
        ("Position X/Y/Z", _text(" / ".join(
            "%.2f" % item for item in position) if position else None)),
        ("Nozzle", _text("%s → %s °C" % (
            _number(temperatures.get("nozzle")),
            _number(temperatures.get("nozzle_target"), 0))
            if temperatures else None)),
        ("Bed", _text("%s → %s °C" % (
            _number(temperatures.get("bed")),
            _number(temperatures.get("bed_target"), 0))
            if temperatures else None)),
        ("Capture duration", _text(_duration(
            (run_data.number(timing.get("duration_ms")) or 0) / 1000.0)
            if timing else None)),
        ("Queue delay", _text(_duration(
            (run_data.number(timing.get("queue_delay_ms")) or 0) / 1000.0)
            if timing else None)),
        ("Render queue depth / high mark", _text("%s / %s" % (
            renderer.get("queue_depth"), renderer.get("queue_high_watermark"))
            if renderer else None)),
        ("Dropped / coalesced batches", _text("%s / %s" % (
            renderer.get("dropped_batches"), renderer.get("coalesced_batches"))
            if renderer else None)),
        ("Typer restarts", _text(renderer.get("typer_restarts"))),
        ("Render worker", _text(renderer.get("worker_state"))),
        ("SHA-256", "<code>%s</code>" % _text(
            str(record.get("sha256") or "")[:16])),
        ("File", '<a href="%s">%s</a>' % (
            html.escape(frame["url"] or "", quote=True),
            _text(record["file"]))),
    ]
    error = renderer.get("worker_last_error") or timing.get("error")
    button_rows = "".join(
        "<tr><td><code>%s</code></td><td>%s</td><td>%s</td><td>%sx%s at "
        "%s,%s</td></tr>" % (
            _text(name), _text(box.get("label")), _text(box.get("state")),
            box.get("width"), box.get("height"), box.get("x"), box.get("y"))
        for name, box in sorted(buttons.items()))
    return (
        '<article class="frame %s"><header><div><span class="index">#%d</span>'
        "<h3>%s</h3></div>%s</header>"
        '<p><button type="button" class="toggle-hitboxes">Show touch areas'
        "</button></p>"
        '<div class="screen-view"><img src="%s" alt="%s">%s</div>%s%s%s'
        "</article>" % (
            "fail" if frame["failed"] else "pass", frame["number"],
            _text(frame["label"]),
            _badge("failed" if frame["failed"] else "passed"),
            html.escape(frame["url"] or "", quote=True), _text(frame["label"]),
            _hit_boxes(record), _definition_list(rows),
            '<div class="error">%s</div>' % _text(error) if error else "",
            '<details><summary>Buttons (%d)</summary><table><thead><tr>'
            "<th>Id</th><th>Label</th><th>State</th><th>Box</th></tr></thead>"
            "<tbody>%s</tbody></table></details>" % (
                len(buttons), button_rows) if buttons else ""))


def _screen_tile(frame):
    suite = frame["suite"]
    return (
        '<button class="shot-tile %s solo" type="button" data-item '
        'data-frame="%d" data-suite="%s" data-status="%s" data-kind="%s" '
        'data-phase="%s" data-page="%s" data-search="%s" '
        'aria-haspopup="dialog"><span class="shot-images"><span '
        'class="thumb"><img loading="lazy" src="%s" alt="%s"></span></span>'
        '<span class="shot-caption"><span class="shot-title">%s</span>'
        '<span class="shot-meta">#%d · %s</span></span>%s</button>' % (
            "fail" if frame["failed"] else "pass", frame["number"],
            _tokens([suite["name"]]),
            _tokens(["failed" if frame["failed"] else "ok"]),
            _tokens([frame["kind"]]), _tokens([frame["phase"] or "none"]),
            _tokens([frame["page"] or "none"]),
            _text(" ".join((
                frame["label"], frame["page"], frame["phase"])).lower()),
            html.escape(frame["url"] or "", quote=True),
            _text(frame["label"]), _text(frame["label"]),
            frame["number"], _text(frame["page"] or frame["phase"]),
            '<span class="problem-marker">failed</span>'
            if frame["failed"] else ""))


def _screen_groups(frames):
    """Group consecutive-purpose screens: stages, periodic shots, or one page.

    The operation-context test produces dozens of near-identical screens, so
    the page shows one card per (suite, phase, purpose) that opens into its
    screens instead of one undifferentiated wall.
    """
    groups = {}
    for frame in frames:
        if frame["kind"] == "periodic":
            title = "Periodic captures"
        elif frame["kind"] == "stage":
            title = "Operation stages"
        else:
            title = (frame["page"] or "Other").replace("_", " ").capitalize()
        key = (frame["suite"]["name"], frame["phase"], frame["kind"]
               if frame["kind"] in ("periodic", "stage") else title)
        groups.setdefault(key, {"title": title, "frames": []})[
            "frames"].append(frame)
    return list(groups.values())


def _screen_group(group, names):
    frames = group["frames"]
    failed = sum(1 for item in frames if item["failed"])
    opened = failed or len(frames) <= SMALL_GROUP
    times = [item["video"] for item in frames if item["video"] is not None]
    first = frames[0]
    strip = "".join(
        '<img class="mini" loading="lazy" src="%s" alt="">' % html.escape(
            item["url"] or "", quote=True)
        for item in frames[:: max(1, len(frames) // 4)][:4])
    return (
        '<section class="screen-group %s" data-section>'
        '<header class="group-head"><button type="button" '
        'class="group-toggle" aria-expanded="%s"><span class="chev"></span>'
        "<strong>%s</strong></button>"
        '<span class="tag">%s</span><span class="count-label">'
        '<span data-section-count>%d</span> screens</span>%s'
        '<span class="strip">%s</span><span class="actions">%s</span>'
        "</header>"
        '<div class="shot-grid screens group-body">%s</div></section>' % (
            "open" if opened else "closed", "true" if opened else "false",
            _text(group["title"]),
            _text("%s · %s" % (first["suite"]["name"], first["phase"]
                               or "setup")),
            len(frames), _badge("failed", "%d failed" % failed)
            if failed else "", strip,
            _video_link(names, min(times)) if times else "",
            "".join(_screen_tile(item) for item in frames)))


def _screens_page(data, names):
    frames = run_data.frames(data)
    if not frames:
        return '<p class="empty">No screens were captured.</p>'
    toolbar = _toolbar(
        '<span class="toolbar-label">View</span><div class="chips">'
        '<button type="button" data-screens-view="grouped" '
        'aria-pressed="true">Grouped</button>'
        '<button type="button" data-screens-view="flat" '
        'aria-pressed="false">All screens</button></div>'
        '<button type="button" data-groups="open">Expand all</button>'
        '<button type="button" data-groups="closed">Collapse all</button>',
        _chips("suite", "Suite", _facets(
            frames, "suite", lambda item: item["suite"]["name"]))
        if len({i["suite"]["name"] for i in frames}) > 1 else "",
        _chips("status", "Result", _facets(
            frames, "status",
            lambda item: "failed" if item["failed"] else "ok")),
        _chips("kind", "Kind", _facets(frames, "kind", lambda i: i["kind"])),
        _select("phase", "Phase", [i["phase"] or "none" for i in frames]),
        _select("page", "Page", [i["page"] or "none" for i in frames]),
        _search_box("Search label or page…"),
        '<label class="toolbar-label">Size <input type="range" min="200" '
        'max="700" step="20" value="320" data-tile-size></label>',
        label="Screen filter")
    templates = "".join(
        '<template id="detail-%d">%s</template>' % (
            item["number"], _frame_detail(item, names)) for item in frames)
    dialog = (
        '<dialog id="frame-dialog" aria-label="Screen details">'
        '<div class="modal-head"><div class="nav">'
        '<button type="button" data-step="-1" aria-label="Previous screen">←'
        '</button><button type="button" data-step="1" '
        'aria-label="Next screen">→</button>'
        '<span class="modal-position result-count"></span></div>'
        '<button class="modal-close" type="button" aria-label="Close">×'
        '</button></div><div class="modal-content"></div></dialog>')
    return (
        toolbar + '<div class="screen-groups">%s</div>%s%s' % (
            "".join(_screen_group(item, names)
                    for item in _screen_groups(frames)), templates, dialog))


def _telemetry_page(data, names):
    telemetry = data["telemetry"]
    status = (data["report"].get("telemetry") or {}).get("status")
    if not telemetry:
        return '<p class="empty">No telemetry was recorded (status: %s).</p>' % (
            _text(status, "unknown"))
    x_max = _x_max(data)
    bands, markers = _phase_bands(data), _markers(data)

    def pick(*path):
        def read(item):
            value = item
            for key in path:
                value = value.get(key) if isinstance(value, dict) else None
            return value
        return read

    def series(name, color, reader, dash=False, scale=1.0, index=None):
        points = []
        for item in telemetry:
            value = reader(item)
            if index is not None:
                value = value[index] if isinstance(value, list) and len(
                    value) > index else None
            points.append((
                item["x"], value * scale if isinstance(
                    value, (int, float)) else None))
        return {"name": name, "color": color, "dash": dash, "points": points}

    def chart(title, rows, unit, **options):
        return charts.line_chart(
            title, rows, x_max, unit=unit, bands=bands, markers=markers,
            **options)

    figures = [
        _thermal_chart(data, x_max),
        chart("Heater power", [
            series("Nozzle", PALETTE["nozzle"], pick("nozzle", "power")),
            series("Bed", PALETTE["bed"], pick("bed", "power")),
        ], "fraction", y_min=0, y_max=1, height=130, anchor="heater-power"),
        chart("Toolhead position", [
            series("X", PALETTE["pink"], pick("position"), index=0),
            series("Y", PALETTE["cyan"], pick("position"), index=1),
            series("Z", PALETTE["green"], pick("position"), index=2),
        ], "mm", anchor="position"),
        chart("Velocity", [
            series("Toolhead", PALETTE["violet"], pick("velocity")),
            series("Extruder", PALETTE["yellow"], pick("extruder_velocity")),
        ], "mm/s", height=130, anchor="velocity"),
        chart("Motion buffer margin", [
            series("Margin", PALETTE["green"], pick("buffer", "margin")),
        ], "s", height=130, anchor="buffer-margin"),
        chart("Motion buffer stalls", [
            series("Stalls", PALETTE["pink"], pick("buffer", "stalls")),
        ], "count", y_min=0, height=110, anchor="buffer-stalls"),
        chart("MCU round-trip time", [
            series("srtt", PALETTE["cyan"], pick("mcu", "srtt"),
                   scale=1000.0),
        ], "ms", y_min=0, height=130, anchor="mcu-rtt"),
        chart("MCU load", [
            series("Awake", PALETTE["nozzle"], pick("mcu", "awake")),
            series("Task average", PALETTE["violet"], pick("mcu", "task_avg")),
        ], "s", y_min=0, height=130, anchor="mcu-load"),
        chart("MCU retransmitted bytes", [
            series("Retransmit", PALETTE["pink"],
                   pick("mcu", "bytes_retransmit")),
        ], "bytes", y_min=0, height=110, anchor="mcu-retransmit"),
    ]
    if any((item.get("print_progress") or 0) > 0 for item in telemetry):
        figures.append(chart("Print progress", [
            series("Progress", PALETTE["green"], pick("print_progress")),
        ], "fraction", y_min=0, y_max=1, height=110))
    report = data["report"].get("telemetry") or {}
    facts = _definition_list([
        ("Status", _badge(report.get("status"))),
        ("Requested rate", _text("%s Hz" % report.get("rate_hz"))),
        ("Effective rate", _text("%s Hz" % report.get("effective_rate_hz"))),
        ("Samples", _text(report.get("sample_count"))),
        ("Sampling failures", _text(report.get("failure_count"))),
        ("Raw data", '<a href="%s">telemetry.jsonl</a>' % _text(
            report.get("file") or "telemetry.jsonl")),
    ])
    return (
        '<section class="summary-dl">%s</section><p class="muted">Bands are '
        "test phases; red rules mark failed steps. Hover a chart for values; "
        "all charts share the cursor.</p>%s" % (facts, "".join(figures)))


def _performance_page(data, names):
    x_max = _x_max(data)
    bands, markers = _phase_bands(data), _markers(data)
    frames = run_data.frames(data)
    reactor = []
    for suite in data["suites"]:
        for row in suite["reactor"]:
            x = run_data.video_time(
                suite, epoch=run_data.number(row.get("time")))
            if x is not None:
                reactor.append((x, row))

    def reactor_series(name, color, key, dash=False):
        return {"name": name, "color": color, "dash": dash, "points": [
            (x, run_data.number(row.get(key))) for x, row in reactor]}

    def frame_points(key, scale=1.0, kind=None):
        points = []
        for frame in frames:
            value = run_data.number(frame["timing"].get(key))
            if frame["video"] is not None and value is not None and (
                    kind is None or frame["kind"] == kind):
                points.append((frame["video"], value * scale))
        return points

    def renderer_points(key):
        return [
            (frame["video"], run_data.number(
                (frame["record"].get("renderer") or {}).get(key)))
            for frame in frames if frame["video"] is not None]

    def chart(title, rows, unit, **options):
        return charts.line_chart(
            title, rows, x_max, unit=unit, bands=bands, markers=markers,
            **options)

    harness = [
        chart("Reactor lag", [
            reactor_series("Average", PALETTE["green"], "average_lag_ms"),
            reactor_series("Maximum", PALETTE["nozzle"], "max_lag_ms"),
        ], "ms", y_min=0, anchor="reactor-lag"),
        chart("Late reactor ticks per second", [
            reactor_series("Late ticks", PALETTE["pink"], "missed_deadlines"),
        ], "count", y_min=0, height=110, anchor="late-ticks"),
        chart("Screen capture pipeline", [
            {"name": "Duration", "color": PALETTE["cyan"],
             "points": frame_points("duration_ms")},
            {"name": "Queue delay", "color": PALETTE["yellow"],
             "points": frame_points("queue_delay_ms")},
        ], "ms", y_min=0, anchor="capture-pipeline"),
        chart("Render queue", [
            {"name": "Queue depth", "color": PALETTE["cyan"],
             "points": renderer_points("queue_depth")},
            {"name": "High watermark", "color": PALETTE["violet"],
             "points": renderer_points("queue_high_watermark")},
        ], "batches", y_min=0, height=130, anchor="render-queue"),
        chart("Render batches (cumulative)", [
            {"name": "Dropped", "color": PALETTE["pink"],
             "points": renderer_points("dropped_batches")},
            {"name": "Coalesced", "color": PALETTE["yellow"],
             "points": renderer_points("coalesced_batches")},
        ], "batches", y_min=0, height=130, anchor="render-batches"),
    ]
    roles = data["resources"]
    order = [name for name in ("klippy", "moonraker", "typer", "dropbear")
             if name in roles]
    colors = dict(zip(order, (
        PALETTE["green"], PALETTE["cyan"], PALETTE["yellow"],
        PALETTE["violet"])))
    system = roles.get("system") or []
    resources = []
    if system:
        resources.append(chart("Load average (1 min)", [
            {"name": "load1", "color": PALETTE["nozzle"], "points": [
                (item["x"], item["load1"]) for item in system]},
        ], "", y_min=0, height=130, anchor="load"))
        resources.append(chart("Memory", [
            {"name": "Available", "color": PALETTE["green"], "points": [
                (item["x"], None if item["mem_available_kb"] is None
                 else item["mem_available_kb"] / 1024.0) for item in system]},
            {"name": "Swap free", "color": PALETTE["grey"], "points": [
                (item["x"], None if item["swap_free_kb"] is None
                 else item["swap_free_kb"] / 1024.0) for item in system]},
        ], "MB", y_min=0, height=140, anchor="memory"))
    if order:
        resources.append(chart("Process CPU", [
            {"name": name, "color": colors[name], "points": [
                (item["x"], item["cpu"]) for item in roles[name]]}
            for name in order
        ] + ([{"name": "all cores", "color": PALETTE["nozzle"], "dash": True,
               "points": [(item["x"], item["cpu"]) for item in system]}]
             if system else []), "% of one core", y_min=0,
            anchor="cpu"))
        resources.append(chart("Process memory (RSS)", [
            {"name": name, "color": colors[name], "points": [
                (item["x"], None if item["rss_kb"] is None
                 else item["rss_kb"] / 1024.0) for item in roles[name]]}
            for name in order
        ], "MB", y_min=0, anchor="rss"))
    stats = [
        ("Capture duration (ms)", run_data.summarize(
            [p[1] for p in frame_points("duration_ms")])),
        ("Capture queue delay (ms)", run_data.summarize(
            [p[1] for p in frame_points("queue_delay_ms")])),
        ("Reactor average lag (ms)", run_data.summarize(
            [run_data.number(r.get("average_lag_ms")) for _x, r in reactor])),
        ("Reactor maximum lag (ms)", run_data.summarize(
            [run_data.number(r.get("max_lag_ms")) for _x, r in reactor])),
    ]
    stat_rows = "".join(
        "<tr><td>%s</td><td>%d</td><td>%s</td><td>%s</td><td>%s</td>"
        "<td>%s</td></tr>" % (
            _text(name), value["count"], _number(value["min"], 2),
            _number(value["mean"], 2), _number(value["p95"], 2),
            _number(value["max"], 2))
        for name, value in stats if value)
    if not (stat_rows or resources or any(harness)):
        return '<p class="empty">No performance data was recorded.</p>'
    return (
        "<section><h2>Summary</h2><table><thead><tr><th>Measure</th>"
        "<th>Samples</th><th>Min</th><th>Mean</th><th>p95</th><th>Max</th>"
        "</tr></thead><tbody>%s</tbody></table></section>"
        "<section><h2>Test harness</h2>%s</section>"
        "<section><h2>Printer system</h2>%s<p class=\"muted\">CPU assumes 100 "
        "clock ticks per second; \"all cores\" is the sum over cores.</p>"
        "</section>" % (
            stat_rows, "".join(harness),
            "".join(resources) or '<p class="empty">No resource samples.</p>'))


def _first_difference(expected, actual):
    """Index of the first differing snapshot and its differing keys."""
    for index in range(max(len(expected), len(actual))):
        want = expected[index] if index < len(expected) else None
        got = actual[index] if index < len(actual) else None
        if want != got:
            keys = sorted(set(want or {}) | set(got or {}))
            return index, [
                (key, (want or {}).get(key), (got or {}).get(key))
                for key in keys
                if (want or {}).get(key) != (got or {}).get(key)]
    return None, []


def _json_cell(value):
    return "<code>%s</code>" % _text(json.dumps(
        value, sort_keys=True, ensure_ascii=False))


def _scenario_row(item):
    passed = item.get("passed", True)
    expected = item.get("expected") or []
    actual = item.get("actual") or []
    detail = ""
    if not passed:
        index, differences = _first_difference(expected, actual)
        if index is not None:
            detail = (
                "<details open><summary>Snapshot %d differs "
                "(%d expected, %d actual)</summary><table class=\"diff\">"
                "<thead><tr>"
                "<th>Field</th><th>Expected</th><th>Actual</th></tr></thead>"
                "<tbody>%s</tbody></table></details>" % (
                    index, len(expected), len(actual), "".join(
                        "<tr><td><code>%s</code></td><td>%s</td><td>%s</td>"
                        "</tr>" % (_text(key), _json_cell(want),
                                   _json_cell(got))
                        for key, want, got in differences)))
    diagnostic = item.get("diagnostic")
    return (
        '<tr class="%s"><td>%s</td><td>%s</td><td>%s</td><td>%s</td>'
        "<td>%s</td><td>%s%s%s</td></tr>" % (
            "" if passed else "failed-row", _text(item["suite"]["name"]),
            _text(item.get("scenario")), _text(item.get("variant")),
            _text(item.get("fixture")), _badge("passed" if passed else "failed"),
            len(actual) if actual else "—",
            '<div class="error">%s</div>' % _long_text(diagnostic, 400)
            if diagnostic else "", detail))


def _results_section(suite):
    results = (suite["summary"] or {}).get("test_results") or {}
    if not results:
        return ""
    parts = []
    mesh = results.get("mesh")
    if isinstance(mesh, list) and mesh and isinstance(mesh[0], list):
        values = [v for row in mesh for v in row if v is not None]
        rms = (sum(v * v for v in values) / len(values)) ** 0.5
        parts.append(
            "<h3>Bed mesh</h3>%s%s" % (
                charts.heatmap(mesh),
                _definition_list([
                    ("Points", str(len(values))),
                    ("Range", _text("%.3f mm" % (max(values) - min(values)))),
                    ("Min / max", _text("%+.3f / %+.3f mm" % (
                        min(values), max(values)))),
                    ("RMS", _text("%.3f mm" % rms)),
                ])))
    for key, value in sorted(results.items()):
        if key == "mesh":
            continue
        if isinstance(value, dict):
            parts.append("<h3>%s</h3>%s" % (_text(key), _definition_list([
                (name, _text(_number(item, 3) if isinstance(item, float)
                             else item)) for name, item in sorted(
                    value.items())])))
        else:
            parts.append("<h3>%s</h3><pre class=\"wrap\">%s</pre>" % (
                _text(key), _text(json.dumps(value, indent=1))))
    return '<section><h2>Measured results — %s</h2>%s</section>' % (
        _text(suite["name"]), "".join(parts))


def _stage_table(suite):
    stages = (suite["summary"] or {}).get("calibration_stages") or ()
    if not stages:
        return ""
    rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            _text(charts.format_time(run_data.video_time(
                suite, epoch=run_data.number(item.get("time")))) if (
                    run_data.video_time(suite, epoch=run_data.number(
                        item.get("time"))) is not None) else None),
            _text(item.get("phase")),
            _text(" › ".join(item.get("context_path") or ()) or "idle"),
            _text(item.get("current_state")))
        for item in stages if isinstance(item, dict))
    return (
        "<section><h2>Operation stages — %s</h2><table><thead><tr><th>At</th>"
        "<th>Phase</th><th>Context</th><th>State</th></tr></thead><tbody>%s"
        "</tbody></table></section>" % (_text(suite["name"]), rows))


def _checks_page(data, names):
    scenarios = _scenarios(data)
    sections = []
    if scenarios:
        failed = sum(1 for item in scenarios if not item.get("passed", True))
        sections.append(
            "<section><h2>Operation-context scenarios</h2><p>%d scenarios, "
            "%s.</p><table class=\"scenarios\"><thead><tr><th>Suite</th>"
            "<th>Scenario</th><th>Variant</th><th>Fixture</th><th>Result</th>"
            "<th>Snapshots</th><th>Detail</th></tr></thead><tbody>%s</tbody>"
            "</table></section>" % (
                len(scenarios),
                "<strong class=\"bad\">%d failed</strong>" % failed
                if failed else "all passed",
                "".join(_scenario_row(item) for item in scenarios)))
    for suite in data["suites"]:
        sections.append(_results_section(suite))
        sections.append(_stage_table(suite))
    body = "".join(part for part in sections if part)
    return body or (
        '<p class="empty">This run recorded no operation-context '
        "scenarios or measured results.</p>")


def _log_table(data, names):
    rows = []
    groups = {}
    for suite in data["suites"]:
        for stream, lines in (
                ("run", suite["run_log"]), ("printer", suite["printer_log"])):
            for line in lines:
                group = "key" if line["level"] != "info" else line["group"]
                groups[group] = groups.get(group, 0) + 1
                x = None
                if line["mono"] is not None:
                    x = run_data.video_time(suite, mono=line["mono"])
                rows.append(
                    '<tr id="%s-%s-%d" data-item data-suite="%s" '
                    'data-stream="%s" data-group="%s" data-level="%s" '
                    'data-search="%s" class="%s"><td class="n">%d</td>'
                    '<td class="t">%s</td><td class="m">%s</td></tr>' % (
                        stream, _text(suite["name"]), line["number"],
                        _tokens([suite["name"]]), _tokens([stream]),
                        _tokens([group]), _tokens([line["level"]]),
                        _text(line["text"].lower()), line["level"],
                        line["number"],
                        _video_link(names, x, plain=True) if x is not None else "",
                        _text(line["text"])))
    if not rows:
        return '<p class="empty">No logs were recorded for this run.</p>'
    labels = {
        "key": "Key", "steps": "Steps", "captures": "Captures",
        "samples": "Samples", "commands": "Commands"}
    suite_names = [item["name"] for item in data["suites"]]
    toolbar = _toolbar(
        _chips("suite", "Suite", [
            (name, name, None) for name in suite_names])
        if len(suite_names) > 1 else "",
        _chips("stream", "Log", [
            ("run", "Host run log", None), ("printer", "Printer log", None)]),
        _chips("group", "Lines", [
            (key, labels[key], groups[key]) for key in labels if key in groups],
            default="key"),
        _chips("level", "Level", [
            ("error", "Errors", None), ("warn", "Warnings", None)]),
        _search_box("Search the log…"), label="Log filter")
    return toolbar + (
        '<table class="log"><thead><tr><th>#</th><th>At</th><th>Line</th>'
        "</tr></thead><tbody>%s</tbody></table>" % "".join(rows))


def _environment(suite):
    env = suite["environment"] or {}
    if not env:
        return ""
    toolhead = env.get("toolhead") or {}
    rows = [
        ("Run id", "<code>%s</code>" % _text(env.get("run_id"))),
        ("Printer suite", _text(env.get("suite"))),
        ("Theme", _text(env.get("theme"))),
        ("Material", _text(env.get("material"))),
        ("Software version", _text(env.get("software_version"))),
        ("UI fingerprint", "<code>%s</code>" % _text(
            str(env.get("ui_fingerprint") or "")[:16])),
        ("Screen capture interval", _text("%s s" % env.get(
            "screen_capture_interval"))),
        ("Print state", _text((env.get("print") or {}).get("state"))),
        ("Axis minimum", _text(toolhead.get("axis_minimum"))),
        ("Axis maximum", _text(toolhead.get("axis_maximum"))),
        ("Max velocity / accel", _text("%s / %s" % (
            toolhead.get("max_velocity"), toolhead.get("max_accel")))),
    ]
    profiles = env.get("heating_profiles") or {}
    profile_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            _text(name), _text(value[0]), _text(value[1]))
        for name, value in sorted(profiles.items())
        if isinstance(value, list) and len(value) > 1)
    return (
        "<section><h2>Environment — %s</h2>%s%s</section>" % (
            _text(suite["name"]), _definition_list(rows),
            "<table><thead><tr><th>Material</th><th>Nozzle °C</th>"
            "<th>Bed °C</th></tr></thead><tbody>%s</tbody></table>"
            % profile_rows if profile_rows else ""))


def _file_table(title, prefix, files):
    if not files:
        return ""
    return (
        "<section><h2>%s</h2><table><thead><tr><th>File</th><th>Size</th>"
        "</tr></thead><tbody>%s</tbody></table></section>" % (
            _text(title), "".join(
                '<tr><td><a href="%s">%s</a></td><td>%s</td></tr>' % (
                    html.escape(_quote(prefix + item["name"]), quote=True),
                    _text(item["name"]), _text(_size(item["size"])))
                for item in files)))


def _run_page(data, names):
    report = data["report"]
    media = report.get("media") or {}
    camera = report.get("camera") or {}
    meta = camera.get("metadata") or {}
    resources = report.get("resources") or {}
    recording = media.get("recording")
    video = (
        '<video id="recording" controls preload="metadata" src="%s"></video>'
        % html.escape(_quote(recording), quote=True) if recording else
        '<p class="empty">No recording was produced (status: %s).</p>' %
        _text(media.get("status"), "unknown"))
    facts = _definition_list([
        ("Printer host", _text(report.get("printer_host"))),
        ("Requested suites", _text(", ".join(
            report.get("requested_suites") or [
                report.get("requested_suite")] or []))),
        ("Started", _text(report.get("started_at"))),
        ("Finished", _text(report.get("finished_at"))),
        ("Video FPS", _text(report.get("fps"))),
        ("Screen capture interval", _text("%s s" % report.get(
            "screen_capture_interval"))),
        ("Camera", "%s%s" % (_badge(camera.get("status")), _text(
            " %s · %s FPS" % (meta.get("name") or meta.get("service"),
                              meta.get("target_fps")) if meta else ""))),
        ("Printer resources", "%s%s" % (
            _badge(resources.get("status")),
            ' · <a href="%s">resources.tsv</a>' % _text(resources["file"])
            if resources.get("file") else "")),
        ("Recording", _badge(media.get("status"))),
    ])
    sections = [
        "<section><h2>Recording</h2>%s</section>" % video,
        "<section><h2>Run</h2>%s</section>" % facts,
    ]
    sections.extend(_environment(item) for item in data["suites"])
    sections.append(_file_table("Run files", "", data["files"]))
    for suite in data["suites"]:
        sections.append(_file_table(
            "Suite files — %s" % suite["name"],
            (suite["artifact"] + "/") if suite["artifact"] else "",
            suite["files"]))
    sections.append(
        "<p class=\"muted\">Rebuild these pages at any time with "
        "<code>python -m tests.printer_report &lt;run directory&gt;</code>.</p>")
    return "".join(part for part in sections if part)


# --- assembly ---------------------------------------------------------------


def _navigation(data, names, current):
    steps, frames = _steps(data), run_data.frames(data)
    scenarios = _scenarios(data)
    failed_steps = sum(1 for item in steps if item["status"] == "failed")
    failed_frames = sum(1 for item in frames if item["failed"])
    failed_scenarios = sum(
        1 for item in scenarios if not item.get("passed", True))
    counts = {
        "steps": (len(steps), "fail" if failed_steps else ""),
        "screens": (len(frames), "fail" if failed_frames else ""),
        "checks": (len(scenarios), "fail" if failed_scenarios else ""),
    }
    links = []
    for slug, title in PAGES:
        count, tone = counts.get(slug, (None, ""))
        links.append('<a href="%s"%s>%s%s</a>' % (
            _text(names[slug]), ' aria-current="page"' if slug == current
            else "", _text(title), '<span class="count %s">%d</span>' % (
                tone, count) if count is not None else ""))
    return '<nav class="tabs" aria-label="Report pages">%s</nav>' % "".join(
        links)


def _alerts(data):
    report = data["report"]
    alerts = []
    for title, items in (
            ("Warnings", report.get("warnings") or ()),
            ("Some artifacts could not be read", data["problems"])):
        if items:
            alerts.append(
                '<section class="alert warn"><h2>%s</h2><ul>%s</ul></section>'
                % (_text(title), "".join(
                    "<li>%s</li>" % _text(item) for item in items)))
    return "".join(alerts)


def _document(data, names, slug, body):
    report = data["report"]
    title = dict(PAGES)[slug]
    requested = report.get("requested_suites") or [
        report.get("requested_suite")]
    meta = " · ".join(str(item) for item in (
        report.get("printer_host"),
        "/".join(str(item) for item in requested if item),
        report.get("started_at")) if item)
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FF5M printer regression — %(title)s — %(status_text)s</title>
<link rel="stylesheet" href="report.css">
<link rel="stylesheet" href="printer.css">
</head>
<body data-page="%(slug)s">
<main>
  <header class="report-head">
    <h1>FF5M printer regression</h1>%(status)s
    <span class="run-meta">%(meta)s</span>
  </header>
  %(nav)s
  %(alerts)s
  %(body)s
</main>
<script src="report.js"></script>
<script src="printer.js"></script>
</body>
</html>
""" % {
        "title": _text(title), "status_text": _text(report.get("status")),
        "slug": slug, "status": _badge(report.get("status")),
        "meta": _text(meta, ""), "nav": _navigation(data, names, slug),
        "alerts": "", "body": body,
    }


def render(data, entry="report.html", past_runs=None):
    """Return every page as ``{file name: HTML text}`` for loaded run data.

    A page that cannot be built from unexpected artifact contents is replaced
    by an explanation instead of failing the others: the report is written
    after the verdict is already on disk and must never hide it.
    """
    names = _page_names(entry)
    builders = {
        "overview": lambda data, names: _overview(data, names, past_runs),
        "steps": _steps_page, "screens": _screens_page,
        "telemetry": _telemetry_page, "performance": _performance_page,
        "checks": _checks_page, "logs": _log_table, "run": _run_page,
    }
    pages = {}
    for slug, _title in PAGES:
        try:
            body = builders[slug](data, names)
        except Exception as exc:  # noqa: BLE001 - see docstring
            body = (
                '<section class="alert fail"><h2>This page could not be '
                "built</h2><p>%s: %s</p></section>" % (
                    _text(type(exc).__name__), _text(exc)))
        pages[names[slug]] = _document(data, names, slug, body)
    return pages


def _replace(path, writer):
    temporary = path.with_name(path.name + ".tmp")
    writer(temporary)
    temporary.replace(path)


def write(output, entry="report.html"):
    """Load the run in ``output`` and atomically write all pages and assets."""
    output = pathlib.Path(output)
    data = run_data.load(output)
    requested = data["report"].get("requested_suites") or []
    past = analysis.history(output, requested)
    for name, page in render(data, entry, past).items():
        _replace(output / name, lambda target, page=page: (
            target.write_text(page, encoding="utf-8")))
    for name in ASSET_NAMES:
        _replace(output / name, lambda target, name=name: (
            shutil.copyfile(ASSETS / name, target)))
    return output / entry


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Rebuild the HTML report of a printer-regression run.")
    parser.add_argument("run", help="run directory containing report.json")
    args = parser.parse_args(argv)
    directory = pathlib.Path(args.run)
    if not directory.is_dir():
        print("not a directory: %s" % directory, file=sys.stderr)
        return 2
    print(write(directory))
    return 0


if __name__ == "__main__":
    sys.exit(main())
