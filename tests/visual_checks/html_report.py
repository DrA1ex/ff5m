## Offline HTML report for host-side FF5M UI visual checks.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Render a self-contained UI-regression review page around local images."""

import html
import pathlib
import urllib.parse


def _text(value, fallback="—"):
    if value is None or value == "":
        value = fallback
    return html.escape(str(value), quote=True)


def _class(value):
    value = str(value or "not_run").lower()
    if value in ("pass", "passed", "completed", "valid"):
        return "pass"
    if value in ("warn", "warning", "review", "partial", "needs_baseline"):
        return "warn"
    if value in ("fail", "failed", "invalid", "invalid_response"):
        return "fail"
    return "muted"


def _badge(value):
    return '<span class="badge %s">%s</span>' % (
        _class(value), _text(value or "not_run"))


def _image_url(value):
    if not value:
        return None
    path = pathlib.PurePosixPath(str(value).replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        return None
    return html.escape(
        urllib.parse.quote(path.as_posix(), safe="/"), quote=True)


def _expectation(value):
    if not isinstance(value, dict):
        return ""
    sections = (
        ("Required", "required"),
        ("Forbidden", "forbidden"),
        ("Allowed variations", "allowed_variations"),
    )
    body = [
        '<section class="expectation"><h4>Textual baseline</h4>',
        "<p>%s</p>" % _text(value.get("description"), ""),
    ]
    for title, name in sections:
        items = value.get(name, ())
        if not isinstance(items, (list, tuple)) or not items:
            continue
        body.append("<h5>%s</h5><ul>" % _text(title))
        body.extend("<li>%s</li>" % _text(item) for item in items)
        body.append("</ul>")
    body.append("</section>")
    return "".join(body)


def _checklist(model):
    response = model.get("response") if isinstance(model, dict) else None
    checks = response.get("checks", ()) if isinstance(response, dict) else ()
    if not checks:
        return ""
    rows = []
    for check in checks:
        rows.append(
            "<tr><td><code>%s</code></td><td>%s</td><td>%s</td></tr>" % (
                _text(check.get("id")),
                _badge(check.get("status")),
                _text(check.get("reason"), ""),
            ))
    return (
        '<details class="checks"><summary>Model checklist (%d)</summary>'
        "<table><thead><tr><th>Check</th><th>Status</th><th>Reason</th>"
        "</tr></thead><tbody>%s</tbody></table></details>"
        % (len(rows), "".join(rows))
    )


def _parity_result(model):
    response = model.get("response") if isinstance(model, dict) else None
    checks = response.get("checks", ()) if isinstance(response, dict) else ()
    parity = next(
        (item for item in checks if item.get("id") == "source_parity"),
        None)
    if parity is None:
        return ""
    return (
        '<div class="parity-result"><strong>Designer ↔ real printer:</strong>'
        " %s %s</div>" % (
            _badge(parity.get("status")),
            _text(parity.get("reason"), ""),
        ))


def _frame(frame, number):
    screenshot = frame.get("screenshot", {})
    case = frame.get("case_result", {})
    models = frame.get("models", ())
    model = models[0] if models else {}
    verdict = case.get("verdict") or model.get("verdict") or frame.get(
        "status") or "not_run"
    source = screenshot.get("source") or "unknown"
    image = _image_url(screenshot.get("artifact"))
    comparison = _image_url(screenshot.get("comparison_artifact"))
    title = (
        screenshot.get("label")
        or screenshot.get("case_id")
        or screenshot.get("file")
        or "Unlabelled frame"
    )
    images = []
    if image:
        primary_caption = "Designer" if source == "parity" else source
        images.append(
            '<figure><img loading="lazy" src="%s" alt="%s">'
            "<figcaption>%s</figcaption></figure>" % (
                image, _text(title), _text(primary_caption)))
    if comparison:
        comparison_caption = (
            "Real printer Typer/framebuffer"
            if source == "parity" else "comparison")
        images.append(
            '<figure><img loading="lazy" src="%s" alt="Comparison for %s">'
            "<figcaption>%s</figcaption></figure>" % (
                comparison, _text(title), _text(comparison_caption)))
    if not images:
        images.append('<div class="image-missing">Image unavailable</div>')

    reasons = case.get("reasons") or model.get("reasons") or ()
    reason_html = ""
    if reasons:
        reason_html = "<h4>Reasons</h4><ul>%s</ul>" % "".join(
            "<li>%s%s</li>" % (
                (
                    "<code>%s</code>: " % _text(reason.get("check_id"))
                    if reason.get("check_id") else ""
                ),
                _text(reason.get("reason"), ""),
            )
            for reason in reasons
        )
    error = case.get("error") or model.get("error")
    error_html = ""
    if isinstance(error, dict):
        error_html = (
            '<div class="error"><strong>%s</strong><br>%s</div>' % (
                _text(error.get("category"), "error"),
                _text(error.get("message"), ""),
            ))
    response = model.get("response")
    summary = response.get("summary") if isinstance(response, dict) else None
    validation = (
        case.get("json_validation")
        or model.get("json_validation")
        or {"status": "not_run"}
    )
    elapsed = case.get("elapsed_seconds")
    if elapsed is None:
        elapsed = model.get("elapsed_seconds")
    meta = [
        ("Source", source),
        ("Page", screenshot.get("page")),
        ("Case", screenshot.get("case_id")),
        ("Semantic page", screenshot.get("semantic_page_id")),
        ("JSON", validation.get("status")),
        ("Elapsed", (
            "%.3f s" % elapsed if isinstance(elapsed, (int, float)) else None)),
        ("Attempts", model.get("attempts")),
        ("File", screenshot.get("file")),
    ]
    references = screenshot.get("expectation_references") or ()
    references_html = ""
    if references:
        references_html = (
            "<details><summary>Baseline references (%d)</summary><ul>%s</ul>"
            "</details>" % (
                len(references),
                "".join("<li><code>%s</code></li>" % _text(item)
                        for item in references),
            ))
    return """
<article class="frame %(frame_class)s" data-outcome="%(frame_class)s"
         data-source="%(source_class)s">
  <header>
    <div><span class="index">#%(number)d</span><h3>%(title)s</h3></div>
    %(verdict)s
  </header>
  <div class="images">%(images)s</div>
  %(parity)s
  <dl>%(meta)s</dl>
  %(error)s
  %(summary)s
  %(reasons)s
  %(expectation)s
  %(checks)s
  %(references)s
</article>""" % {
        "frame_class": _class(verdict),
        "source_class": _text(source.lower()),
        "number": number,
        "title": _text(title),
        "verdict": _badge(verdict),
        "images": "".join(images),
        "parity": _parity_result(model) if source == "parity" else "",
        "meta": "".join(
            "<div><dt>%s</dt><dd>%s</dd></div>" % (
                _text(name), _text(value))
            for name, value in meta
        ),
        "error": error_html,
        "summary": (
            "<p class=\"model-summary\"><strong>Model summary:</strong> %s</p>"
            % _text(summary) if summary else ""),
        "reasons": reason_html,
        "expectation": _expectation(screenshot.get("expectation")),
        "checks": _checklist(model),
        "references": references_html,
    }


def _pipeline(report):
    stages = report.get("pipeline", ())
    if not stages:
        return ""
    cards = []
    for index, stage in enumerate(stages, 1):
        counts = stage.get("counts", {})
        runs = stage.get("runs", ())
        details = [
            "<span><code>%s</code>: %s</span>" % (
                _text(str(key).replace("_", " ")), _text(value))
            for key, value in counts.items()
        ]
        run_html = ""
        if runs:
            run_html = "<ul class=\"runs\">%s</ul>" % "".join(
                "<li><strong>%s</strong> — %s frames"
                "<br><code>%s</code></li>" % (
                    _text(item.get("suite")),
                    _text(item.get("captured", 0)),
                    _text(item.get("run_id")),
                )
                for item in runs
            )
        cards.append("""
<article class="stage %(stage_class)s">
  <header><span class="stage-number">%(number)d</span>
    <div><h3>%(title)s</h3>%(status)s</div></header>
  <p>%(summary)s</p>
  <div class="stage-counts">%(counts)s</div>
  %(runs)s
</article>""" % {
            "stage_class": _class(stage.get("status")),
            "number": index,
            "title": _text(stage.get("title")),
            "status": _badge(stage.get("status")),
            "summary": _text(stage.get("summary"), ""),
            "counts": "".join(details),
            "runs": run_html,
        })
    return (
        '<section class="pipeline"><h2>What this run actually did</h2>'
        '<div class="pipeline-grid">%s</div></section>' % "".join(cards)
    )


def _frame_groups(frames):
    order = ("designer", "printer", "parity", "unknown")
    buckets = {}
    for number, frame in enumerate(frames, 1):
        source = str(
            frame.get("screenshot", {}).get("source") or "unknown").lower()
        buckets.setdefault(source, []).append((number, frame))
    groups = []
    extra = tuple(item for item in sorted(buckets) if item not in order)
    for source in order + extra:
        items = buckets.get(source)
        if not items:
            continue
        title = {
            "designer": "Designer-generated frames",
            "printer": "Real printer framebuffer frames",
            "parity": "Designer / printer parity pairs",
            "unknown": "Unclassified frames",
        }.get(source, source.title() + " frames")
        groups.append("""
<section class="source-group" id="source-%(source)s"
         data-source-group="%(source)s">
  <header class="source-heading">
    <div><h2>%(title)s</h2>
      <p>%(count)d frame(s) in the final LLM review corpus.</p></div>
    <span class="source-count">%(count)d</span>
  </header>
  %(frames)s
</section>""" % {
            "source": _text(source),
            "title": _text(title),
            "count": len(items),
            "frames": "".join(
                _frame(frame, number) for number, frame in items),
        })
    return "".join(groups)


def render(report):
    """Return a portable, dependency-free HTML report as text."""
    status = report.get("status", "unknown")
    coverage = report.get("coverage", {})
    configuration = report.get("configuration", {})
    summary = report.get("summary", {})
    infrastructure_error = report.get("infrastructure_error")
    frames = report.get("screenshots", ())
    verdicts = summary.get("verdicts", {})
    cards = [
        ("Status", _badge(status)),
        ("Mode", _text(report.get("mode"))),
        ("Model", _text(configuration.get("model"), "disabled")),
        ("Frames", _text(len(frames), "0")),
        ("Designer", _text(coverage.get("designer", 0))),
        ("Printer captured", _text(coverage.get(
            "printer_captured", coverage.get("legacy_printer", 0)))),
        ("Printer in review", _text(coverage.get("legacy_printer", 0))),
        ("Printer replaced", _text(coverage.get("replaced", 0))),
        ("Parity pairs", _text(coverage.get("parity_pairs", 0))),
        ("LLM reviewed", _text(
            summary.get("statuses", {}).get("completed", 0))),
        ("Pass / Warn / Fail", "%s / %s / %s" % (
            _text(verdicts.get("pass", 0)),
            _text(verdicts.get("warn", 0)),
            _text(verdicts.get("fail", 0)),
        )),
    ]
    alerts = []
    if isinstance(infrastructure_error, dict):
        alerts.append(
            '<section class="alert fail"><h2>Infrastructure failure</h2>'
            "<p><strong>%s</strong></p><p>%s</p></section>" % (
                _text(infrastructure_error.get("category"), "error"),
                _text(infrastructure_error.get("message"), ""),
            ))
    missing = report.get("missing_expectations") or ()
    if missing:
        alerts.append(
            '<section class="alert warn"><h2>Baselines required</h2>'
            "<p>%d case(s) need a reviewed textual baseline.</p></section>"
            % len(missing))
    frame_html = _frame_groups(frames)
    if not frame_html:
        frame_html = (
            '<p class="empty">No screenshots reached the review stage.</p>')
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FF5M UI regression — %(title_status)s</title>
<style>
:root{color-scheme:dark;--bg:#0b1116;--panel:#121b22;--line:#29404d;
--text:#e4edf2;--muted:#91a4ae;--pass:#4ade80;--warn:#facc15;--fail:#fb7185}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:15px/1.45 system-ui,-apple-system,sans-serif}main{max-width:1500px;
margin:auto;padding:24px}h1,h2,h3,h4,h5{margin:.2em 0 .6em}h1{font-size:28px}
.sub{color:var(--muted)}.summary{display:grid;
grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:10px;margin:20px 0}
.metric,.frame,.alert{background:var(--panel);border:1px solid var(--line);
border-radius:10px}.metric{padding:12px}.metric span{display:block;color:var(--muted);
font-size:12px;margin-bottom:5px}.metric strong{font-size:17px}.toolbar{display:flex;
gap:8px;flex-wrap:wrap;margin:18px 0}.toolbar button{background:#17242c;color:var(--text);
border:1px solid var(--line);border-radius:7px;padding:8px 13px;cursor:pointer}
.alert{padding:16px;margin:14px 0}.alert.fail{border-color:var(--fail)}
.alert.warn{border-color:var(--warn)}.frame{padding:16px;margin:16px 0}
.frame.pass{border-left:5px solid var(--pass)}.frame.warn{border-left:5px solid var(--warn)}
.frame.fail{border-left:5px solid var(--fail)}.frame.muted{border-left:5px solid var(--muted)}
.frame>header{display:flex;justify-content:space-between;gap:12px;align-items:start}
.frame>header>div{display:flex;gap:10px;align-items:baseline}.index{color:var(--muted)}
.badge{display:inline-block;border:1px solid;padding:2px 8px;border-radius:999px;
font-weight:700;text-transform:uppercase;font-size:12px}.badge.pass{color:var(--pass)}
.badge.warn{color:var(--warn)}.badge.fail{color:var(--fail)}.badge.muted{color:var(--muted)}
.images{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));
gap:12px;margin:12px 0}.images figure{margin:0}.images img{display:block;width:100%%;
max-height:600px;object-fit:contain;background:#050809;border:1px solid var(--line)}
figcaption{text-align:center;color:var(--muted);padding:4px}.image-missing{padding:60px;
text-align:center;color:var(--muted);border:1px dashed var(--line)}
.parity-result{padding:10px 12px;background:#0e171d;border:1px solid var(--line);
border-radius:7px;margin:10px 0}
dl{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
gap:7px;margin:12px 0}
dl div{background:#0e171d;padding:8px;border-radius:6px}dt{color:var(--muted);font-size:12px}
dd{margin:2px 0 0;overflow-wrap:anywhere}.expectation{background:#0e171d;padding:12px;
border-radius:7px}.expectation h5{color:var(--muted);margin-top:10px}.error{padding:12px;
border:1px solid var(--fail);background:#2a1118;border-radius:7px}.model-summary{font-size:16px}
table{width:100%%;border-collapse:collapse;margin-top:10px}th,td{padding:8px;
border:1px solid var(--line);text-align:left;vertical-align:top}code{overflow-wrap:anywhere}
details{margin-top:12px}summary{cursor:pointer;color:#b8d9e8}.empty{color:var(--muted)}
.pipeline{margin:24px 0}.pipeline-grid{display:grid;
grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px}.stage{padding:15px;
background:var(--panel);border:1px solid var(--line);border-top:4px solid var(--muted);
border-radius:10px}.stage.pass{border-top-color:var(--pass)}.stage.warn{border-top-color:var(--warn)}
.stage.fail{border-top-color:var(--fail)}.stage header{display:flex;gap:10px;align-items:start}
.stage header h3{margin:0 0 5px}.stage-number{display:grid;place-items:center;width:28px;
height:28px;border-radius:50%%;background:#20313b;font-weight:700;flex:0 0 auto}
.stage-counts{display:flex;gap:6px;flex-wrap:wrap}.stage-counts span{padding:4px 7px;
background:#0e171d;border-radius:5px;font-size:12px}.runs{padding-left:20px}
.source-group{scroll-margin-top:16px}.source-heading{display:flex;justify-content:space-between;
align-items:center;margin:34px 0 10px;padding:14px 16px;background:#16232b;
border:1px solid var(--line);border-radius:10px}.source-heading h2{margin:0}
.source-heading p{margin:4px 0 0;color:var(--muted)}.source-count{font-size:24px;
font-weight:800}.toolbar-label{align-self:center;color:var(--muted);margin-left:8px}
body[data-outcome-filter="problem"] .frame.pass{display:none}
body[data-outcome-filter="pass"] .frame:not(.pass){display:none}
body[data-source-filter="designer"] .frame:not([data-source="designer"]),
body[data-source-filter="printer"] .frame:not([data-source="printer"]),
body[data-source-filter="parity"] .frame:not([data-source="parity"]){display:none}
body[data-source-filter="designer"] .source-group:not([data-source-group="designer"]),
body[data-source-filter="printer"] .source-group:not([data-source-group="printer"]),
body[data-source-filter="parity"] .source-group:not([data-source-group="parity"]){display:none}
@media(max-width:600px){main{padding:12px}.images{grid-template-columns:1fr}}
</style>
</head>
<body data-outcome-filter="all" data-source-filter="all">
<main>
  <h1>FF5M UI regression %(status)s</h1>
  <p class="sub">Offline review report. Images and model evidence remain local.</p>
  <section class="summary">%(cards)s</section>
  %(alerts)s
  %(pipeline)s
  <nav class="toolbar" aria-label="Frame filter">
    <span class="toolbar-label">Outcome:</span>
    <button type="button" data-outcome="all">All</button>
    <button type="button" data-outcome="problem">Problems / not run</button>
    <button type="button" data-outcome="pass">Pass only</button>
    <span class="toolbar-label">Source:</span>
    <button type="button" data-source="all">All</button>
    <button type="button" data-source="designer">Designer</button>
    <button type="button" data-source="printer">Real printer</button>
    <button type="button" data-source="parity">Parity</button>
  </nav>
  <section id="frames">%(frames)s</section>
</main>
<script>
document.querySelectorAll("button[data-outcome]").forEach(function(button){
  button.addEventListener("click",function(){
    document.body.dataset.outcomeFilter=button.dataset.outcome;
  });
});
document.querySelectorAll("button[data-source]").forEach(function(button){
  button.addEventListener("click",function(){
    document.body.dataset.sourceFilter=button.dataset.source;
  });
});
</script>
</body>
</html>
""" % {
        "title_status": _text(status),
        "status": _badge(status),
        "cards": "".join(
            '<div class="metric"><span>%s</span><strong>%s</strong></div>'
            % (_text(label), value)
            for label, value in cards
        ),
        "alerts": "".join(alerts),
        "pipeline": _pipeline(report),
        "frames": frame_html,
    }


def write(path, report):
    """Atomically write a UTF-8 HTML report."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(render(report), encoding="utf-8")
    temporary.replace(path)
