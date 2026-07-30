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
    configuration = report.get("configuration", {})
    coverage = report.get("coverage", {})
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
        '<details class="run-details"><summary>Run details, model evidence '
        'and collection stages</summary>'
        '<p class="run-configuration">Model: <code>%s</code> · '
        'Theme: <code>%s</code> · Printer captured: %s · '
        'Printer retained: %s · Replaced: %s</p>'
        '<section class="pipeline">'
        '<div class="pipeline-grid">%s</div></section></details>'
        % (
            _text(configuration.get("model"), "disabled"),
            _text(configuration.get("designer_theme"), "default"),
            _text(coverage.get("printer_captured", 0)),
            _text(coverage.get("legacy_printer", 0)),
            _text(coverage.get("replaced", 0)),
            "".join(cards),
        )
    )


def _tile(frame, number):
    screenshot = frame.get("screenshot", {})
    case = frame.get("case_result", {})
    models = frame.get("models", ())
    model = models[0] if models else {}
    verdict = case.get("verdict") or model.get("verdict") or frame.get(
        "status") or "not_run"
    outcome = _class(verdict)
    source = str(screenshot.get("source") or "unknown").lower()
    image = _image_url(screenshot.get("artifact"))
    comparison = _image_url(screenshot.get("comparison_artifact"))
    title = (
        screenshot.get("label")
        or screenshot.get("case_id")
        or screenshot.get("file")
        or "Unlabelled frame"
    )
    thumbnails = []
    if image:
        thumbnails.append(
            '<span class="thumb"><img loading="lazy" src="%s" alt="%s">'
            '%s</span>' % (
                image,
                _text(title),
                '<span class="corner-label">Designer</span>'
                if source == "parity" else "",
            ))
    if comparison:
        thumbnails.append(
            '<span class="thumb"><img loading="lazy" src="%s" '
            'alt="Real printer comparison for %s">'
            '<span class="corner-label">Printer</span></span>' % (
                comparison, _text(title)))
    if not thumbnails:
        thumbnails.append(
            '<span class="thumb image-missing">Image unavailable</span>')
    problem = (
        '<span class="problem-marker">%s</span>' % _text(verdict)
        if outcome != "pass" else "")
    return """
<button class="shot-tile %(outcome)s %(pair)s" type="button"
        data-detail="detail-%(number)d" data-outcome="%(outcome)s"
        data-source="%(source)s" aria-haspopup="dialog">
  <span class="shot-images">%(images)s</span>
  <span class="shot-caption">
    <span class="shot-title">%(title)s</span>
    <span class="shot-meta">#%(number)d · %(source_label)s</span>
  </span>
  %(problem)s
</button>""" % {
        "outcome": outcome,
        "pair": "pair" if comparison else "single",
        "number": number,
        "source": _text(source),
        "images": "".join(thumbnails),
        "title": _text(title),
        "source_label": _text(
            "Designer ↔ printer" if source == "parity" else source),
        "problem": problem,
    }


def _gallery(title, description, items, gallery_id, pair=False):
    if not items:
        return ""
    return """
<section class="gallery-section" id="%(gallery_id)s">
  <header class="gallery-heading">
    <div><h2>%(title)s</h2><p>%(description)s</p></div>
    <span>%(count)d</span>
  </header>
  <div class="shot-grid %(pair_class)s">%(tiles)s</div>
</section>""" % {
        "gallery_id": _text(gallery_id),
        "title": _text(title),
        "description": _text(description),
        "count": len(items),
        "pair_class": "pair-grid" if pair else "",
        "tiles": "".join(_tile(frame, number) for number, frame in items),
    }


def _overview(frames):
    numbered = list(enumerate(frames, 1))
    standalone = [
        item for item in numbered
        if item[1].get("screenshot", {}).get("source") != "parity"]
    parity = [
        item for item in numbered
        if item[1].get("screenshot", {}).get("source") == "parity"]
    galleries = [
        _gallery(
            "Screenshot overview",
            "Designer pages and retained real-printer screens.",
            standalone, "screenshot-overview"),
        _gallery(
            "Designer ↔ real printer",
            "Pairwise component parity. Each tile shows both renderers.",
            parity, "parity-overview", pair=True),
    ]
    templates = "".join(
        '<template id="detail-%d">%s</template>' % (
            number, _frame(frame, number))
        for number, frame in numbered
    )
    return "".join(galleries) + templates


def render(report):
    """Return a portable, dependency-free HTML report as text."""
    status = report.get("status", "unknown")
    coverage = report.get("coverage", {})
    configuration = report.get("configuration", {})
    summary = report.get("summary", {})
    infrastructure_error = report.get("infrastructure_error")
    frames = report.get("screenshots", ())
    verdicts = summary.get("verdicts", {})
    problem_count = int(verdicts.get("warn", 0)) + int(
        verdicts.get("fail", 0))
    cards = [
        ("Status", _badge(status)),
        ("Mode", _text(report.get("mode"))),
        ("Theme", _text(configuration.get("designer_theme"), "default")),
        ("Frames", _text(len(frames), "0")),
        ("Parity pairs", _text(coverage.get("parity_pairs", 0))),
        ("Problems", _text(problem_count)),
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
    overview_html = _overview(frames)
    if not overview_html:
        overview_html = (
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
font:14px/1.4 system-ui,-apple-system,sans-serif}main{max-width:1800px;margin:auto;
padding:18px}h1,h2,h3,h4,h5{margin:.2em 0 .55em}h1{font-size:23px}
.report-head{display:flex;align-items:center;justify-content:space-between;gap:14px;
flex-wrap:wrap}.report-head h1{margin:0}.badge{display:inline-block;border:1px solid;
padding:2px 8px;border-radius:999px;font-weight:750;text-transform:uppercase;
font-size:11px}.badge.pass{color:var(--pass)}.badge.warn{color:var(--warn)}
.badge.fail{color:var(--fail)}.badge.muted{color:var(--muted)}
.summary{display:flex;gap:7px;flex-wrap:wrap;margin:12px 0}.metric{display:flex;gap:7px;
align-items:center;background:var(--panel);border:1px solid var(--line);border-radius:8px;
padding:7px 10px}.metric span{color:var(--muted);font-size:11px}.metric strong{font-size:13px}
.toolbar{position:sticky;top:0;z-index:10;display:flex;gap:6px;align-items:center;
flex-wrap:wrap;margin:12px 0;padding:9px;background:rgba(11,17,22,.94);
backdrop-filter:blur(8px);border:1px solid var(--line);border-radius:9px}
.toolbar button{background:#17242c;color:var(--text);border:1px solid var(--line);
border-radius:7px;padding:6px 10px;cursor:pointer}.toolbar-label{color:var(--muted);
font-size:11px;margin-left:7px}.alert{padding:13px;margin:12px 0;background:var(--panel);
border:1px solid var(--line);border-radius:9px}.alert.fail{border-color:var(--fail)}
.alert.warn{border-color:var(--warn)}.run-details{margin:10px 0;color:var(--muted)}
.run-details>summary{display:inline-block;cursor:pointer;padding:7px 10px;
border:1px solid var(--line);border-radius:7px;background:#111b22}
.pipeline{margin:12px 0}.pipeline-grid{display:grid;
grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:9px}.stage{padding:12px;
background:var(--panel);border:1px solid var(--line);border-top:3px solid var(--muted);
border-radius:8px;color:var(--text)}.stage.pass{border-top-color:var(--pass)}
.stage.warn{border-top-color:var(--warn)}.stage.fail{border-top-color:var(--fail)}
.stage header{display:flex;gap:8px;align-items:start}.stage header h3{font-size:14px;margin:0 0 4px}
.stage-number{display:grid;place-items:center;width:24px;height:24px;border-radius:50%%;
background:#20313b;font-weight:700;flex:0 0 auto}.stage-counts{display:flex;gap:5px;
flex-wrap:wrap}.stage-counts span{padding:3px 5px;background:#0e171d;border-radius:4px;
font-size:10px}.runs{padding-left:18px}.gallery-section{margin:22px 0 34px}
.gallery-heading{display:flex;justify-content:space-between;align-items:end;
margin-bottom:9px}.gallery-heading h2{font-size:18px;margin:0}.gallery-heading p{margin:2px 0 0;
color:var(--muted)}.gallery-heading>span{font-size:20px;font-weight:800;color:var(--muted)}
.shot-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:8px}
.shot-grid.pair-grid{grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:10px}
.shot-tile{position:relative;display:block;width:100%%;padding:0;overflow:hidden;
background:#070b0e;color:var(--text);border:2px solid #20323c;border-radius:7px;
text-align:left;cursor:zoom-in;transition:transform .12s,border-color .12s,box-shadow .12s}
.shot-tile:hover,.shot-tile:focus-visible{transform:translateY(-2px);border-color:#8cb7ca;
box-shadow:0 8px 24px #0008;outline:none}.shot-tile.warn{border-color:var(--warn);
box-shadow:0 0 0 2px #facc1530}.shot-tile.fail{border-color:var(--fail);
box-shadow:0 0 0 2px #fb71853b}.shot-images{display:grid;grid-template-columns:1fr;
aspect-ratio:5/3;background:#030506}.shot-tile.pair .shot-images{grid-template-columns:1fr 1fr;
aspect-ratio:10/3}.thumb{position:relative;min-width:0;overflow:hidden}
.thumb+ .thumb{border-left:1px solid var(--line)}.thumb img{display:block;width:100%%;
height:100%%;object-fit:contain;background:#030506}.corner-label{position:absolute;left:5px;
top:5px;padding:2px 5px;background:#000b;border-radius:4px;color:#fff;font-size:9px;
text-transform:uppercase;letter-spacing:.05em}.shot-caption{display:flex;justify-content:space-between;
gap:8px;align-items:center;padding:6px 8px;background:#111a20}.shot-title{font-weight:650;
white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.shot-meta{color:var(--muted);
font-size:10px;white-space:nowrap}.problem-marker{position:absolute;right:5px;top:5px;
padding:3px 7px;border-radius:999px;background:#111e;text-transform:uppercase;
font-weight:800;font-size:10px}.shot-tile.warn .problem-marker{color:var(--warn)}
.shot-tile.fail .problem-marker{color:var(--fail)}
body[data-outcome-filter="problem"] .shot-tile.pass{display:none}
body[data-outcome-filter="pass"] .shot-tile:not(.pass){display:none}
body[data-source-filter="designer"] .shot-tile:not([data-source="designer"]),
body[data-source-filter="printer"] .shot-tile:not([data-source="printer"]),
body[data-source-filter="parity"] .shot-tile:not([data-source="parity"]){display:none}
dialog{width:min(1500px,96vw);max-height:94vh;padding:0;color:var(--text);
background:var(--panel);border:1px solid var(--line);border-radius:11px;box-shadow:0 20px 70px #000}
dialog::backdrop{background:#020406dc;backdrop-filter:blur(3px)}.modal-head{position:sticky;
top:0;z-index:2;display:flex;justify-content:flex-end;padding:8px;background:var(--panel);
border-bottom:1px solid var(--line)}.modal-close{width:36px;height:36px;border-radius:50%%;
border:1px solid var(--line);background:#19262e;color:var(--text);font-size:22px;cursor:pointer}
.modal-content{padding:0 16px 16px}.frame{padding:8px}.frame>header{display:flex;
justify-content:space-between;gap:12px;align-items:start}.frame>header>div{display:flex;
gap:10px;align-items:baseline}.index{color:var(--muted)}.images{display:grid;
grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:10px;margin:10px 0}
.images figure{margin:0}.images img{display:block;width:100%%;max-height:64vh;
object-fit:contain;background:#030506;border:1px solid var(--line)}figcaption{text-align:center;
color:var(--muted);padding:4px}.image-missing{display:grid;place-items:center;min-height:120px;
color:var(--muted);border:1px dashed var(--line)}.parity-result{padding:9px 11px;
background:#0e171d;border:1px solid var(--line);border-radius:7px;margin:9px 0}
dl{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:6px;margin:10px 0}
dl div{background:#0e171d;padding:7px;border-radius:6px}dt{color:var(--muted);font-size:10px}
dd{margin:2px 0 0;overflow-wrap:anywhere}.expectation{background:#0e171d;padding:11px;
border-radius:7px}.expectation h5{color:var(--muted);margin-top:9px}.error{padding:11px;
border:1px solid var(--fail);background:#2a1118;border-radius:7px}.model-summary{font-size:15px}
table{width:100%%;border-collapse:collapse;margin-top:9px}th,td{padding:7px;
border:1px solid var(--line);text-align:left;vertical-align:top}code{overflow-wrap:anywhere}
details{margin-top:10px}summary{cursor:pointer;color:#b8d9e8}.empty{color:var(--muted)}
@media(max-width:700px){main{padding:10px}.shot-grid,.shot-grid.pair-grid{
grid-template-columns:1fr 1fr}.shot-tile.pair{grid-column:span 2}.images{
grid-template-columns:1fr}.shot-caption{display:block}.shot-meta{display:block;margin-top:2px}}
</style>
</head>
<body data-outcome-filter="all" data-source-filter="all">
<main>
  <header class="report-head">
    <h1>FF5M UI regression</h1>
    %(status)s
  </header>
  <section class="summary">%(cards)s</section>
  %(alerts)s
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
  %(overview)s
  %(pipeline)s
</main>
<dialog id="frame-dialog" aria-label="Screenshot details">
  <div class="modal-head">
    <button class="modal-close" type="button" aria-label="Close">×</button>
  </div>
  <div class="modal-content"></div>
</dialog>
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
const frameDialog=document.getElementById("frame-dialog");
const modalContent=frameDialog.querySelector(".modal-content");
document.querySelectorAll(".shot-tile").forEach(function(tile){
  tile.addEventListener("click",function(){
    const template=document.getElementById(tile.dataset.detail);
    if(!template)return;
    modalContent.replaceChildren(template.content.cloneNode(true));
    frameDialog.showModal();
  });
});
frameDialog.querySelector(".modal-close").addEventListener("click",function(){
  frameDialog.close();
});
frameDialog.addEventListener("click",function(event){
  if(event.target===frameDialog)frameDialog.close();
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
        "overview": overview_html,
    }


def write(path, report):
    """Atomically write a UTF-8 HTML report."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(render(report), encoding="utf-8")
    temporary.replace(path)
