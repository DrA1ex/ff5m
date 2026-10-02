## Host-side behavioral contracts for the printer-regression HTML report.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Behavioral contracts for loading and reporting printer-regression runs."""

import html.parser
import io
import json
import pathlib
import tempfile
import unittest
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from tests import printer_analysis as ANALYSIS
from tests import printer_charts as CHARTS
from tests import printer_report as REPORT
from tests import printer_run_data as RUN_DATA

STARTED = 1000.0
SUITE_DIR = "suites/core/20260930-000000-000001-full"
FAILURE = "operation_context fixture mismatch: snapshot 1 differs"


def _context(states, cancel=True):
    return {
        "contexts": [{"name": "Print"}] if states else [],
        "current_state": states,
        "cancel_available": cancel,
    }


def make_run(root, failing=False, recording=True, lag=9.0, extra=()):
    """Write a small but complete run directory the way the runner does."""
    root = pathlib.Path(root)
    suite = root / SUITE_DIR
    suite.mkdir(parents=True)
    steps = [
        ("baseline", "capture"), ("ui-open", "tap"),
        ("0.100 MM", "tap_label"), ("ui-error-restart", "capture"),
    ]
    lines = ["2026-09-30T10:00:00.000 monotonic=50.000000 run started "
             "suite=FULL material=PETG"]
    frames = []
    for index, (label, kind) in enumerate(steps):
        mono = 50.0 + 1.0 + index
        lines.append(
            "2026-09-30T10:00:01.000 monotonic=%.6f STEP_START index=%d "
            "kind=%s label=%s eventtime=%.6f" % (mono, index, kind, label, mono))
        if kind == "capture":
            filename = "%03d-%s.bmp" % (len(frames) + 1, label)
            (suite / filename).write_bytes(b"BM")
            lines.append(
                "2026-09-30T10:00:01.100 monotonic=%.6f CAPTURE_QUEUED %s "
                "eventtime=%.6f" % (mono + 0.1, label, mono))
            lines.append(
                "2026-09-30T10:00:01.300 monotonic=%.6f CAPTURE %s %s" % (
                    mono + 0.3, label, filename))
            frames.append({
                "file": filename, "label": label, "number": len(frames) + 1,
                "phase": "ui", "page": "IDLE_HOME", "passed": True,
                "time": STARTED + mono - 50.0, "position": [1.0, 2.0, 3.0],
                "temperatures": {
                    "nozzle": 25.0, "nozzle_target": 0.0, "bed": 24.0,
                    "bed_target": 0.0},
                "buttons": {"nav.menu": {
                    "label": "MENU", "state": "enabled", "x": 650, "y": 11,
                    "width": 132, "height": 38}},
                "hitboxes": {"global.wake": {
                    "x": 0, "y": 0, "width": 800, "height": 480}},
                "renderer": {
                    "queue_depth": 0, "queue_high_watermark": 2,
                    "dropped_batches": 0, "coalesced_batches": 1,
                    "typer_restarts": 0, "worker_state": "running",
                    "worker_last_error": ""},
                "sha256": "ab" * 32,
            })
        elif failing and index == 3:
            pass
        else:
            lines.append(
                "2026-09-30T10:00:01.200 monotonic=%.6f PASS %s" % (
                    mono + 0.2, label))
    for label, kind, phase, page in extra:
        filename = "%03d-%s.bmp" % (len(frames) + 1, label)
        (suite / filename).write_bytes(b"BM")
        frames.append(dict(
            frames[0], file=filename, label=label, number=len(frames) + 1,
            phase=phase, page=page, capture_kind=kind, passed=True))
    if failing:
        lines.append(
            "2026-09-30T10:00:05.000 monotonic=%.6f FAILED ui-error-restart: "
            "Unable to queue capture receipt" % 54.5)
        next(item for item in frames
             if item["label"] == "ui-error-restart")["passed"] = False
    lines.append(
        "2026-09-30T10:00:03.000 monotonic=52.000000 periodic capture "
        "skipped: toolhead busy")
    (suite / "run.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (suite / "printer.log").write_text(
        "[feather_ui_test] run started suite=FULL\n"
        "[feather_screen] USB event queue overflowed; reconciling\n"
        "[feather_screen] command start name=M104 page=HOME\n",
        encoding="utf-8")
    (suite / "manifest.json").write_text(json.dumps(frames), encoding="utf-8")
    (suite / "artifact_timing.csv").write_text(
        "number,label,capture_kind,phase,page,queue_delay_ms,duration_ms,"
        "success,file,error\n" + "".join(
            "%d,%s,semantic,ui,IDLE_HOME,5,120,%s,%s,\n" % (
                item["number"], item["label"], item["passed"], item["file"])
            for item in frames), encoding="utf-8")
    (suite / "reactor.csv").write_text(
        "time,eventtime,scheduled,lag_ms,average_lag_ms,max_lag_ms,"
        "missed_deadlines,phase,step_index,step\n"
        "%f,51,51,0.2,1.0,4.0,0,ui,1,ui-open\n"
        "%f,53,53,0.3,2.0,%s,1,ui,3,ui-error-restart\n" % (
            STARTED + 1, STARTED + 3, lag), encoding="utf-8")
    scenario_actual = [_context("PRINTING"), _context("PAUSED")]
    scenarios = [{
        "scenario": "ui", "variant": "default", "fixture": "none",
        "passed": True, "actual": [], "expected": [],
    }, {
        "scenario": "print_mesh_resume", "variant": "HEATING",
        "fixture": "print_mesh", "passed": not failing,
        "actual": scenario_actual,
        "expected": scenario_actual if not failing else [
            _context("PRINTING"), _context(None, cancel=False)],
    }]
    (suite / "operation_context.json").write_text(
        json.dumps({"passed": not failing, "scenarios": scenarios}),
        encoding="utf-8")
    summary = {
        "started_at": STARTED, "suite": "FULL", "outcome": (
            "failed" if failing else "passed"),
        "reason": FAILURE if failing else None,
        "printer_log": "printer.log",
        "failures": [{
            "step": "ui-error-restart", "error": FAILURE}] if failing else [],
        "test_results": {
            "mesh": [[0.0, 0.025], [-0.01, 0.05]],
            "z": {"farther_local_z": 1.5},
        },
        "calibration_stages": [{
            "time": STARTED + 2.0, "phase": "screws",
            "context_path": ["Bed Screws"], "current_state": "HOMING"}],
    }
    (suite / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (suite / "environment.json").write_text(json.dumps({
        "run_id": "20260930-000000-000001-full", "suite": "FULL",
        "theme": "INDUSTRIAL", "material": "PETG",
        "heating_profiles": {"PETG": [250.0, 70.0]},
        "toolhead": {"axis_minimum": [-125, -125, -10, 0],
                     "axis_maximum": [125, 125, 230, 0],
                     "max_velocity": 600, "max_accel": 20000},
    }), encoding="utf-8")
    (root / "telemetry.jsonl").write_text("".join(
        json.dumps({
            "time": STARTED + offset, "offset": offset,
            "nozzle": {"temperature": 25.0 + offset * 10, "target": 200.0,
                       "power": 0.5},
            "bed": {"temperature": 24.0, "target": 0.0, "power": 0.0},
            "position": [1.0, 2.0, 3.0], "velocity": 0.0,
            "extruder_velocity": 0.0,
            "buffer": {"margin": -1.0, "stalls": 0.0},
            "mcu": {"srtt": 0.001, "awake": 0.002, "task_avg": 0.0,
                    "bytes_retransmit": 9.0},
            "print_progress": 0.0,
        }) + "\n" for offset in range(5)), encoding="utf-8")
    (root / "resources.tsv").write_text(
        "epoch\tuptime\tload1\tmem_available_kb\tswap_free_kb\trole\tpid\t"
        "cpu_ticks\trss_kb\tstate\tthreads\tcommand\n" + "".join(
            "%d\t%.2f\t0.5\t30000\t60000\t%s\t1\t%d\t%d\tS\t1\tcmd\n" % (
                STARTED + offset, 100.0 + offset, role, 1000 + offset * 50,
                20000)
            for offset in range(3) for role in ("system", "klippy")),
        encoding="utf-8")
    if recording:
        (root / "recording.mp4").write_bytes(b"\x00\x00\x00\x18ftyp")
    report = {
        "schema_version": 1, "status": "failed" if failing else "passed",
        "requested_suite": "core", "requested_suites": ["core"],
        "printer_host": "printer.invalid", "fps": 10,
        "started_at": "2026-09-30T10:00:00.000+00:00",
        "finished_at": "2026-09-30T10:00:08.000+00:00",
        "duration_seconds": 8.0, "screen_capture_interval": 5.0,
        "infrastructure_error": None,
        "camera": {"status": "recorded", "metadata": {
            "name": "cam", "target_fps": 30}},
        "media": {"status": "passed", "recording": (
            "recording.mp4" if recording else None), "duration_seconds": 8.0},
        "resources": {"status": "recorded", "file": "resources.tsv"},
        "telemetry": {
            "status": "recorded", "rate_hz": 1.0, "effective_rate_hz": 1.0,
            "sample_count": 5, "failure_count": 0,
            "file": "telemetry.jsonl"},
        "warnings": ["sample warning"],
        "suites": [{
            "name": "core", "printer_suite": "FULL",
            "status": "failed" if failing else "passed",
            "outcome": "failed" if failing else "passed",
            "reason": FAILURE if failing else None,
            "started_at": "2026-09-30T10:00:01.000+00:00",
            "finished_at": "2026-09-30T10:00:07.000+00:00",
            "duration_seconds": 6.0, "timeline_start_seconds": 1.0,
            "screenshot_count": len(frames), "artifact": SUITE_DIR,
            "links": {},
        }],
    }
    (root / "report.json").write_text(json.dumps(report), encoding="utf-8")
    return root


class PageLinks(html.parser.HTMLParser):
    """Collect the targets and anchors that connect the report pages."""

    def __init__(self):
        super().__init__()
        self.hrefs = []
        self.ids = set()
        self.items = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for name in ("href", "src"):
            if attrs.get(name):
                self.hrefs.append(attrs[name])
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        if "data-item" in attrs:
            self.items += 1


def parse(page):
    parser = PageLinks()
    parser.feed(page)
    return parser


class RunDataTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)

    def test_steps_keep_labels_with_spaces_files_and_recording_time(self):
        data = RUN_DATA.load(make_run(self.root))
        steps = {item["label"]: item for item in data["suites"][0]["steps"]}

        self.assertEqual(len(steps), 4)
        self.assertEqual(steps["0.100 MM"]["status"], "passed")
        self.assertEqual(steps["baseline"]["file"], "001-baseline.bmp")
        self.assertEqual(steps["ui-open"]["phase"], "ui")
        # Suite anchor 1.0 s plus 2 s after "run started".
        self.assertAlmostEqual(steps["ui-open"]["video"], 3.0, places=1)
        self.assertAlmostEqual(steps["ui-open"]["duration"], 0.2, places=2)

    def test_failed_step_carries_its_error_message(self):
        data = RUN_DATA.load(make_run(self.root, failing=True))
        step = data["suites"][0]["steps"][-1]

        self.assertEqual(step["status"], "failed")
        self.assertEqual(step["error"], "Unable to queue capture receipt")

    def test_structured_lines_are_not_classified_by_their_labels(self):
        data = RUN_DATA.load(make_run(self.root, failing=True))
        run_log = data["suites"][0]["run_log"]
        printer_log = data["suites"][0]["printer_log"]

        restart = next(
            item for item in run_log if "STEP_START" in item["text"]
            and "ui-error-restart" in item["text"])
        failed = next(item for item in run_log if "FAILED" in item["text"])
        self.assertEqual(restart["level"], "info")
        self.assertEqual(failed["level"], "error")
        self.assertEqual(failed["group"], "key")
        self.assertEqual(
            [item["level"] for item in printer_log], ["info", "warn", "info"])
        self.assertEqual(printer_log[2]["group"], "commands")

    def test_unreadable_artifacts_are_reported_not_raised(self):
        run = make_run(self.root)
        (run / SUITE_DIR / "manifest.json").write_text("{bad", encoding="utf-8")
        (run / "telemetry.jsonl").write_text(
            "not json\n" + (run / "telemetry.jsonl").read_text(
                encoding="utf-8"), encoding="utf-8")
        (run / "resources.tsv").unlink()

        data = RUN_DATA.load(run)

        self.assertEqual(data["suites"][0]["manifest"], [])
        self.assertEqual(len(data["telemetry"]), 5)
        self.assertEqual(data["resources"], {})
        self.assertTrue(any("manifest.json" in item for item in data["problems"]))
        self.assertTrue(any("telemetry.jsonl" in item for item in data["problems"]))

    def test_missing_report_and_artifact_directory_are_reported(self):
        self.assertTrue(any(
            "report.json is missing" in item
            for item in RUN_DATA.load(self.root)["problems"]))
        run = make_run(self.root / "run")
        import shutil
        shutil.rmtree(run / "suites")

        data = RUN_DATA.load(run)

        self.assertEqual(data["suites"][0]["steps"], [])
        self.assertTrue(any("artifact directory" in i for i in data["problems"]))

    def test_cpu_percent_is_derived_from_cumulative_ticks(self):
        data = RUN_DATA.load(make_run(self.root))
        samples = data["resources"]["klippy"]

        self.assertIsNone(samples[0]["cpu"])
        # 50 ticks per second at 100 ticks/s is half a core.
        self.assertAlmostEqual(samples[1]["cpu"], 50.0, places=3)

    def test_summarize_reports_percentiles_and_ignores_missing_values(self):
        summary = RUN_DATA.summarize([None, 1.0, 2.0, 3.0, 4.0, 100.0])

        self.assertEqual(summary["count"], 5)
        self.assertEqual(summary["max"], 100.0)
        self.assertEqual(summary["p95"], 100.0)
        self.assertIsNone(RUN_DATA.summarize([None]))


class ChartsTest(unittest.TestCase):
    def test_decimation_keeps_the_peaks_of_a_long_series(self):
        points = [(float(i), 0.0) for i in range(5000)]
        points[2345] = (2345.0, 99.0)

        reduced = CHARTS.decimate(points, limit=200)

        self.assertLess(len(reduced), 450)
        self.assertIn((2345.0, 99.0), reduced)
        self.assertEqual(reduced, sorted(reduced))

    def test_chart_embeds_series_and_bridges_missing_samples(self):
        figure = CHARTS.line_chart("Temp", [{
            "name": "Nozzle", "color": "#fb923c",
            "points": [(0, 1.0), (1, None), (2, 3.0), (3, 4.0)],
        }], 10, unit="°C", bands=[(0, 5, "heat")], markers=[(4, "FAILED", "fail")])

        self.assertEqual(figure.count("<polyline"), 1)
        self.assertIn("[[0,1.0],[2,3.0],[3,4.0]]", figure.replace("&quot;", '"'))
        self.assertIn("data-series=", figure)
        self.assertIn("heat", figure)
        self.assertIn("marker fail", figure)

    def test_chart_without_samples_says_so_instead_of_drawing_axes(self):
        figure = CHARTS.line_chart("Empty", [{
            "name": "x", "color": "#fff", "points": [(0, None)]}], 10)

        self.assertIn("No samples recorded.", figure)
        self.assertNotIn("<svg", figure)

    def test_time_labels_switch_to_hours_when_needed(self):
        self.assertEqual(CHARTS.format_time(75), "1:15")
        self.assertEqual(CHARTS.format_time(3725), "1:02:05")

    def test_heatmap_marks_sign_and_magnitude(self):
        svg = CHARTS.heatmap([[0.05, -0.05], [0.0, 0.01]])

        self.assertIn("+0.050", svg)
        self.assertIn("-0.050", svg)
        self.assertEqual(svg.count("<rect"), 4)


class ReportPagesTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)

    def build(self, **options):
        run = make_run(self.root / "run", **options)
        REPORT.write(run)
        return run

    def read(self, run, name):
        return (run / name).read_text(encoding="utf-8")

    def test_every_review_aspect_has_its_own_page(self):
        run = self.build()

        self.assertEqual(
            sorted(item.name for item in run.glob("report*.html")),
            sorted([
                "report.html", "report-steps.html", "report-screens.html",
                "report-telemetry.html", "report-performance.html",
                "report-checks.html", "report-logs.html", "report-run.html"]))
        for name in REPORT.ASSET_NAMES:
            self.assertTrue((run / name).is_file(), name)
        self.assertEqual(sorted(run.glob("*.tmp")), [])

    def test_links_resolve_to_files_and_anchors_that_exist(self):
        run = self.build(failing=True)
        pages = {item.name: parse(self.read(run, item.name))
                 for item in run.glob("report*.html")}

        for name, page in pages.items():
            for target in page.hrefs:
                parts = urllib.parse.urlsplit(target)
                if parts.scheme or (not parts.path and parts.fragment):
                    continue
                self.assertTrue(
                    (run / urllib.parse.unquote(parts.path)).exists(),
                    "%s links to missing %s" % (name, target))
                fragment = parts.fragment
                if parts.path in pages and fragment.startswith(
                        ("step-", "run-", "printer-", "chart-")):
                    self.assertIn(
                        fragment, pages[parts.path].ids,
                        "%s links to missing anchor %s" % (name, target))

    def test_passing_run_summarizes_suites_steps_and_screens(self):
        run = self.build()
        overview = self.read(run, "report.html")

        self.assertIn("PASSED", overview)
        self.assertIn("Nothing needs attention.", overview)
        self.assertIn("4 steps passed", overview)
        self.assertIn("sample warning", overview)
        self.assertNotIn("Needs attention</h2>", overview)
        self.assertIn('<table class="results">', overview)
        self.assertIn("Reactor lag", overview)
        self.assertEqual(parse(self.read(run, "report-steps.html")).items, 4)
        self.assertEqual(parse(self.read(run, "report-screens.html")).items, 2)

    def test_failure_is_explained_with_error_screen_log_and_recording(self):
        run = self.build(failing=True)
        overview = self.read(run, "report.html")

        self.assertIn("Needs attention</h2>", overview)
        self.assertIn("Step failed: ui-error-restart", overview)
        self.assertIn("Unable to queue capture receipt", overview)
        self.assertIn('class="pill screen"', overview)
        self.assertIn('class="pill log" href="report-logs.html?suite=core&amp;'
                      'stream=run&amp;group=all#run-core-', overview)
        self.assertIn('class="pill video" href="report-run.html#t=', overview)
        steps = self.read(run, "report-steps.html")
        self.assertIn('data-status="|failed|"', steps)
        screens = self.read(run, "report-screens.html")
        self.assertIn('data-status="|failed|"', screens)

    def test_checks_show_a_field_level_diff_for_a_mismatched_scenario(self):
        run = self.build(failing=True)
        checks = self.read(run, "report-checks.html")

        self.assertIn("Snapshot 1 differs", checks)
        self.assertIn("cancel_available", checks)
        self.assertIn("PAUSED", checks)
        self.assertIn("1 failed", checks)
        self.assertIn('<svg class="heatmap"', checks)
        self.assertIn("Bed Screws", checks)

    def test_screen_report_preserves_multiple_regions_of_one_action(self):
        boxes = REPORT._hit_boxes({"hitboxes": [
            {"action": "same", "x": 10, "y": 20, "width": 30, "height": 40},
            {"action": "same", "x": 100, "y": 20, "width": 30, "height": 40},
        ]})
        self.assertEqual(boxes.count('title="same"'), 2)
        self.assertIn("left:1.250%", boxes)
        self.assertIn("left:12.500%", boxes)

    def test_screens_expose_touch_areas_renderer_state_and_capture_timing(self):
        run = self.build()
        screens = self.read(run, "report-screens.html")

        self.assertIn("toggle-hitboxes", screens)
        self.assertIn('class="hit button"', screens)
        self.assertIn("nav.menu", screens)
        self.assertIn("Render queue depth", screens)
        self.assertIn("Capture duration", screens)
        self.assertIn('data-frame="1"', screens)
        self.assertIn('id="detail-2"', screens)

    def test_logs_default_to_key_lines_and_keep_failures_visible(self):
        run = self.build(failing=True)
        logs = self.read(run, "report-logs.html")

        self.assertIn('data-default="key"', logs)
        failed = [
            line for line in logs.splitlines()
            if "FAILED ui-error-restart" in line]
        self.assertTrue(failed)
        self.assertIn('data-group="|key|"', failed[0])
        self.assertIn('data-level="|error|"', failed[0])

    def test_run_page_embeds_the_recording_and_lists_files(self):
        run = self.build()
        page = self.read(run, "report-run.html")

        self.assertIn('<video id="recording"', page)
        self.assertIn('src="recording.mp4"', page)
        self.assertIn("20260930-000000-000001-full", page)
        self.assertIn("summary.json", page)
        self.assertIn("INDUSTRIAL", page)

    def test_run_without_recording_or_telemetry_still_renders_every_page(self):
        run = make_run(self.root / "bare", recording=False)
        (run / "telemetry.jsonl").unlink()
        (run / "resources.tsv").unlink()

        REPORT.write(run)

        self.assertIn(
            "No recording was produced",
            self.read(run, "report-run.html"))
        self.assertIn(
            "No telemetry was recorded",
            self.read(run, "report-telemetry.html"))

    def test_infrastructure_error_with_no_artifacts_is_reported(self):
        run = self.root / "broken"
        run.mkdir()
        (run / "report.json").write_text(json.dumps({
            "status": "error", "requested_suite": "all",
            "infrastructure_error": {
                "category": "RegressionError", "message": "printer <busy>"},
            "suites": [{
                "name": "core", "printer_suite": "FULL",
                "status": "infrastructure_error", "reason": "printer <busy>",
                "artifact": None, "timeline_start_seconds": None,
            }],
            "warnings": [],
        }), encoding="utf-8")

        REPORT.write(run)
        overview = self.read(run, "report.html")

        self.assertIn("Infrastructure failure", overview)
        self.assertIn("printer &lt;busy&gt;", overview)
        self.assertNotIn("<busy>", overview)

    def test_page_that_cannot_be_built_does_not_hide_the_others(self):
        run = make_run(self.root / "run")

        with mock.patch.object(
                REPORT, "_steps_page", side_effect=ValueError("odd data")):
            REPORT.write(run)

        self.assertIn(
            "This page could not be built", self.read(run, "report-steps.html"))
        self.assertIn("odd data", self.read(run, "report-steps.html"))
        self.assertIn("PASSED", self.read(run, "report.html"))

    def test_command_line_rebuilds_a_run_and_rejects_a_missing_directory(self):
        run = make_run(self.root / "run")
        (run / "report-steps.html").write_text("stale", encoding="utf-8")
        out = io.StringIO()

        with redirect_stdout(out):
            self.assertEqual(REPORT.main([str(run)]), 0)
        with redirect_stderr(io.StringIO()):
            self.assertEqual(REPORT.main([str(self.root / "missing")]), 2)

        self.assertIn("report.html", out.getvalue())
        self.assertNotEqual(self.read(run, "report-steps.html"), "stale")


class AnalysisTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)

    def past(self, lags):
        for index, lag in enumerate(lags):
            make_run(self.root / ("2026-01-%02d" % index), lag=lag)

    def assess(self, **options):
        current = make_run(self.root / "2026-99", **options)
        data = RUN_DATA.load(current)
        return ANALYSIS.assess(data, ANALYSIS.history(
            current, data["report"]["requested_suites"]))

    def test_no_verdict_without_enough_earlier_runs(self):
        self.assertEqual(ANALYSIS.judge(500.0, [100.0, 120.0]), "unknown")
        self.past([10.0, 10.0])

        tile = next(item for item in self.assess()["tiles"]
                    if item["key"] == "reactor_lag")

        self.assertEqual(tile["verdict"], "unknown")
        self.assertIn("typical", tile["typical"])

    def test_number_is_unusual_only_above_history_and_well_above_typical(self):
        past = [300.0, 350.0, 450.0]

        self.assertEqual(ANALYSIS.judge(400.0, past), "normal")
        self.assertEqual(ANALYSIS.judge(500.0, past), "normal")
        self.assertEqual(ANALYSIS.judge(900.0, past), "unusual")
        self.assertEqual(ANALYSIS.judge(0.0, [0.0, 0.0, 0.0]), "normal")
        self.assertEqual(ANALYSIS.judge(1.0, [0.0, 0.0, 0.0]), "unusual")
        # Free memory is the other way around: lower is worse.
        self.assertEqual(
            ANALYSIS.judge(10.0, [40.0, 38.0, 42.0], higher_is_worse=False),
            "unusual")

    def test_unusual_lag_becomes_a_note_with_chart_and_video_links(self):
        self.past([10.0, 12.0, 11.0])

        result = self.assess(lag=900.0)
        notes = [item for item in result["findings"]
                 if item["severity"] == "warn"]

        lag = next(item for item in notes if "Reactor lag" in item["title"])
        self.assertIn("900 ms", lag["detail"])
        self.assertIn("typical 10–12 ms (3 runs)", lag["detail"])
        self.assertEqual(
            {link["kind"] for link in lag["links"]}, {"chart", "video"})

    def test_healthy_numbers_raise_no_finding(self):
        self.past([10.0, 12.0, 11.0])

        self.assertEqual(self.assess(lag=11.0)["findings"], [])

    def test_history_uses_only_older_passing_runs_of_the_same_suites(self):
        make_run(self.root / "2026-01-01", lag=10.0)
        make_run(self.root / "2026-01-02", lag=20.0, failing=True)
        other = make_run(self.root / "2026-01-03", lag=30.0)
        report = json.loads((other / "report.json").read_text())
        report["requested_suites"] = ["print"]
        (other / "report.json").write_text(json.dumps(report))
        make_run(self.root / "2026-99", lag=40.0)
        make_run(self.root / "2027-01-01", lag=50.0)

        past = ANALYSIS.history(self.root / "2026-99", ["core"])

        self.assertEqual(
            [item["reactor_lag"]["value"] for item in past], [10.0])

    def test_failures_come_first_and_carry_every_way_to_investigate(self):
        self.past([10.0, 12.0, 11.0])

        findings = self.assess(failing=True, lag=900.0)["findings"]

        self.assertEqual(findings[0]["severity"], "fail")
        step = findings[0]
        self.assertEqual(
            [link["kind"] for link in step["links"]],
            ["video", "screen_before", "steps", "logs"])
        severities = [item["severity"] for item in findings]
        self.assertEqual(severities, sorted(severities, key="fail".__ne__))

    def test_results_pair_each_phase_with_its_context_scenario(self):
        rows = self.assess()["results"]

        ui = next(item for item in rows if item["name"] == "ui")
        self.assertEqual(ui["steps"], 3)
        self.assertEqual(ui["scenarios"][0]["scenario"], "ui")
        self.assertEqual(ui["status"], "passed")
        orphan = next(item for item in rows if item["steps"] == 0)
        self.assertEqual(orphan["name"], "print_mesh_resume")

    def test_failed_scenario_fails_its_row(self):
        rows = self.assess(failing=True)["results"]

        self.assertEqual(
            next(i for i in rows if i["name"] == "print_mesh_resume")["status"],
            "failed")

    def test_late_ticks_are_summed_per_second_not_treated_as_cumulative(self):
        data = RUN_DATA.load(make_run(self.root / "run"))

        self.assertEqual(
            ANALYSIS.measure(data)["late_ticks"]["value"], 1.0)


class LogClassificationTest(unittest.TestCase):
    def line(self, text):
        return RUN_DATA._log_line(1, text)

    def test_routine_homing_output_is_not_an_error_or_warning(self):
        for text in (
                "Homing move end: endstops=['probe'] trigger_pos=[1] error=None",
                "Homing endstop stop: mcu=mcu oid=1 reasons=[('mcu', 1, 2)] "
                "(1=hit 0=timeout 0=host 0=past_end)"):
            self.assertEqual(self.line(text)["level"], "info", text)

    def test_real_problems_are_flagged(self):
        self.assertEqual(self.line(
            "MCU 'mcu' shutdown: Timer too close")["level"], "error")
        self.assertEqual(self.line(
            "[feather_screen] USB event queue overflowed")["level"], "warn")


class ScreenGroupingTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)

    def page(self, **options):
        run = make_run(self.root / "run", **options)
        REPORT.write(run)
        return (run / "report-screens.html").read_text(encoding="utf-8")

    def test_similar_screens_share_one_group_without_duplicating_tiles(self):
        extra = [
            ("periodic-%d" % i, "periodic", "heat", "CONTROL_HEAT")
            for i in range(9)
        ] + [
            ("mesh-stage-%d" % i, "stage", "mesh", "CALIBRATION_PROGRESS")
            for i in range(3)]

        page = self.page(extra=extra)

        self.assertEqual(page.count('class="screen-group'), 4)
        self.assertEqual(page.count("data-frame="), 14)
        self.assertIn("Periodic captures", page)
        self.assertIn("Operation stages", page)
        # Big groups start collapsed with a preview strip; small ones open.
        self.assertIn('class="screen-group closed"', page)
        self.assertIn('class="screen-group open"', page)
        # One preview strip per group: 2 + 4 + 3 thumbnails.
        self.assertEqual(page.count('class="mini"'), 9)
        self.assertIn('data-screens-view="flat"', page)

    def test_group_with_a_failed_screen_starts_open(self):
        extra = [
            ("same-page-%d" % i, "semantic", "ui", "IDLE_HOME")
            for i in range(9)]

        page = self.page(extra=extra, failing=True)

        failed_group = next(
            part for part in page.split("<section ")[1:]
            if "ui-error-restart" in part)
        self.assertTrue(failed_group.startswith('class="screen-group open"'))
        self.assertIn("1 failed", failed_group)


if __name__ == "__main__":
    unittest.main()
