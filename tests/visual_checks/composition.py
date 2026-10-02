## Standalone retained-dialog visual inspection suite.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Run locally; no printer, reference images or model review are involved."""

import argparse
import datetime
import html
import json
import pathlib
import subprocess
import sys

from .hybrid import DesignerCapture, RegressionConfigurationError


ROOT = pathlib.Path(__file__).parents[2]
LAYOUTS = ("cards", "list", "imperative")


def build_cases(steps, theme):
    return [dict(id=step["id"], label=step["label"],
                 semantic_page_id="ui.pages.keys.AppPage.MOVE_STEP", state={}, actions=[],
                 width=800, height=480, theme=theme,
                 composition_fixture=step["composition_fixture"])
            for step in steps]


def write_report(output, steps, records, error=None):
    """Keep screenshots and preparation traces visible even on failure."""
    output = pathlib.Path(output)
    images = {record["case_id"]: record["file"] for record in records}
    escape = html.escape
    entries = []
    for step in steps:
        image = images.get(step["id"])
        screenshot = ('<a href="%s"><img src="%s" width="800" height="480"></a>'
                      % (escape(image, quote=True), escape(image, quote=True))) if image else "<p>Screenshot unavailable</p>"
        counts = step["counts"]
        result = "; ".join(step["failures"]) or "Draw-count checks passed; visual inspection required."
        trace = {key: step[key] for key in (
            "render_calls", "passes", "tree", "batches", "dirty_operations", "dialog_bounds")}
        entries.append('<article id="%s"><h2>%s</h2><p>%s</p>%s<p>%s</p>'
                       '<p>Background: %d · Dialog preparations: %d · Scrims: %d · '
                       'Batches: %d · Tree passes: %d · Layout passes: %d · %.3f ms</p>'
                       '<details><summary>Drawing order, regions, tree and native commands</summary><pre>%s</pre></details>'
                       '</article>' % (
                           escape(step["id"], quote=True), escape(step["label"]),
                           escape(step["description"]), screenshot, escape(result),
                           counts["background"], counts["dialog"], counts["scrim"],
                           counts["batches"], counts["passes"], counts["layout_passes"],
                           step["milliseconds"], escape(json.dumps(trace, indent=2))))
    failure = '<p class="error">%s</p>' % escape(error) if error else ""
    document = ('<!doctype html><html lang="en"><meta charset="utf-8">'
                '<title>Retained dialog composition</title><style>'
                'body{background:#171921;color:#eee;font:16px system-ui;margin:24px auto;max-width:900px}'
                'a{color:#b9cfff}img{max-width:100%;height:auto;image-rendering:auto}'
                'article{margin:40px 0;border-top:1px solid #555;padding-top:12px}'
                'pre{overflow:auto;font-size:12px}.error{color:#ffa1a1}'
                '</style><h1>Retained dialog composition</h1>'
                '<p>Manual visual review. No reference images or pixel comparison.</p>'
                '<p>Each image replays all accepted native drawing commands up to that step, '
                'so previous pixels and repeated alpha fills remain visible.</p>'
                '<p>Dialog preparations include a discarded size probe when bounds change; '
                'accepted batches and scrims count actual output. Timings measure local Python preparation, '
                'not printer performance. Node regions are arranged bounds; dirty operations are the actual drawing commands.</p>'
                '<p><a href="trace.json">Full trace JSON</a></p>' + failure + ''.join(entries) + '</html>')
    (output / "report.html").write_text(document, encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--designer-root", type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path)
    parser.add_argument("--layout", choices=("all",) + LAYOUTS, default="all")
    parser.add_argument("--theme", default="DEFAULT")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--trace-only", action="store_true",
                        help="Run drawing contracts without starting the Designer")
    args = parser.parse_args(argv)
    if not args.trace_only and args.designer_root is None:
        parser.error("--designer-root is required for screenshots")
    output = (args.output or ROOT / "tests/artifacts" / (
        "composition-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f"))).resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Artifact directory is not empty; choose a new output directory")
    output.mkdir(parents=True, exist_ok=True)
    from .composition_scenes import run_sequence

    steps = []
    for layout in LAYOUTS if args.layout == "all" else (args.layout,):
        print("Preparing retained composition: " + layout, flush=True)
        steps.extend(run_sequence(layout, args.theme))
    trace = [{key: value for key, value in step.items() if key != "composition_fixture"} for step in steps]
    (output / "trace.json").write_text(json.dumps(trace, indent=2) + "\n", encoding="utf-8")
    records = []
    error = None
    if not args.trace_only:
        try:
            capture = DesignerCapture(args.designer_root, ROOT)
            records = capture.capture(
                build_cases(steps, args.theme), output, timeout=args.timeout,
                workers=1, progress=lambda item: print(
                    "Screenshots: %d/%d" % (item["completed"], item["total"]), flush=True))
            ids = {record["case_id"] for record in records}
            missing = sorted({step["id"] for step in steps} - ids)
            if missing:
                raise RegressionConfigurationError("Missing screenshots: " + ", ".join(missing))
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            error = str(exc)
            # The capture worker saves completed images incrementally.
            manifest = output / "manifest.json"
            if manifest.is_file():
                records = json.loads(manifest.read_text(encoding="utf-8"))
    write_report(output, steps, records, error)
    failures = sum(len(step["failures"]) for step in steps)
    print("%d steps; %d draw-count failures; report: %s" % (len(steps), failures, output / "report.html"))
    if error:
        print(error, file=sys.stderr)
    return 1 if error or failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
