## Offline HTML report for host-side FF5M UI visual checks.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Render a multi-page, offline UI-regression review around local images.

The report is a set of static pages that share one stylesheet and one script.
Each page works on a single review aspect; only the gallery carries the full
per-frame detail, and the other pages link to it by ``#frame-N``.
"""

import collections
import html
import pathlib
import shutil
import urllib.parse

ASSETS = pathlib.Path(__file__).parents[1] / "report_assets"
ASSET_NAMES = ("report.css", "report.js")

PAGES = (
    ("overview", "Overview"),
    ("gallery", "Gallery"),
    ("problems", "Problems"),
    ("compare", "Compare"),
    ("coverage", "Coverage"),
    ("baselines", "Baselines"),
    ("run", "Run"),
)

OUTCOMES = (
    ("fail", "Fail"),
    ("warn", "Warning"),
    ("pass", "Pass"),
    ("muted", "Not reviewed"),
)

NO_PAGE = "(no semantic page)"
SEVERITY = {"fail": 0, "warn": 1, "muted": 2, "pass": 3}
SLOWEST_FRAMES = 10


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


def _tokens(values):
    """Encode values as the ``|a|b|`` list the page script matches against."""
    return _text("|" + "|".join(str(item) for item in values) + "|")


def _image_url(value):
    if not value:
        return None
    path = pathlib.PurePosixPath(str(value).replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        return None
    return html.escape(
        urllib.parse.quote(path.as_posix(), safe="/"), quote=True)


def _page_names(entry):
    """Map every page slug to its file name; the overview is the entry."""
    entry = pathlib.PurePosixPath(entry)
    return {
        slug: entry.name if slug == "overview"
        else "%s-%s.html" % (entry.stem, slug)
        for slug, _title in PAGES
    }


def _view(frame, number):
    """Flatten one report frame into the values every page needs."""
    shot = frame.get("screenshot") or {}
    case = frame.get("case_result") or {}
    models = frame.get("models") or ()
    model = models[0] if models else {}
    response = model.get("response")
    response = response if isinstance(response, dict) else {}
    verdict = case.get("verdict") or model.get("verdict") or frame.get(
        "status") or "not_run"
    error = case.get("error") or model.get("error")
    elapsed = case.get("elapsed_seconds")
    if elapsed is None:
        elapsed = model.get("elapsed_seconds")
    semantic = shot.get("semantic_page_id") or ""
    title = (
        shot.get("label") or shot.get("case_id") or shot.get("file")
        or "Unlabelled frame")
    return {
        "number": number,
        "title": title,
        "verdict": verdict,
        "outcome": _class(verdict),
        "source": str(shot.get("source") or "unknown").lower(),
        "image": _image_url(shot.get("artifact")),
        "comparison": _image_url(shot.get("comparison_artifact")),
        "page": shot.get("page") or (
            semantic.rsplit(".", 1)[-1] if semantic else NO_PAGE),
        "semantic_page_id": semantic,
        "case_id": shot.get("case_id") or "",
        "file": shot.get("file"),
        "expectation": shot.get("expectation"),
        "references": shot.get("expectation_references") or (),
        "reasons": list(case.get("reasons") or model.get("reasons") or ()),
        "error": error if isinstance(error, dict) else None,
        "summary": response.get("summary"),
        "checks": response.get("checks") or (),
        "validation": (
            case.get("json_validation") or model.get("json_validation")
            or {"status": "not_run"}),
        "elapsed": elapsed if isinstance(elapsed, (int, float)) else None,
        "attempts": model.get("attempts"),
    }


def _problem_tokens(view):
    """Check ids, evidence classes, and error categories behind a problem."""
    checks, evidence, errors = [], [], []
    for reason in view["reasons"]:
        checks.append(reason.get("check_id") or "unspecified")
        if reason.get("evidence_class"):
            evidence.append(reason["evidence_class"])
    if view["error"]:
        errors.append(view["error"].get("category") or "error")
    return sorted(set(checks)), sorted(set(evidence)), sorted(set(errors))


def _search_text(view):
    parts = [
        view["title"], view["case_id"], view["page"], view["source"],
        view["file"], view["summary"],
    ]
    parts.extend(item.get("reason") for item in view["reasons"])
    return " ".join(str(item) for item in parts if item).lower()


def _link(names, slug, fragment="", **query):
    text = urllib.parse.urlencode(query)
    return html.escape(
        names[slug] + ("?" + text if text else "") + fragment, quote=True)


def _frame_link(names, view):
    return _link(names, "gallery", "#frame-%d" % view["number"])


def _count(items, key):
    return collections.Counter(key(item) for item in items)


# --- shared fragments -------------------------------------------------------


def _reasons_html(view):
    reasons = view["reasons"]
    if not reasons:
        return ""
    rows = []
    for reason in reasons:
        rows.append("<li>%s%s%s%s</li>" % (
            _badge(reason.get("status")) + " " if reason.get("status") else "",
            "<code>%s</code> " % _text(reason.get("check_id"))
            if reason.get("check_id") else "",
            '<span class="tag">%s</span>' % _text(reason.get("evidence_class"))
            if reason.get("evidence_class") else "",
            _text(reason.get("reason"), ""),
        ))
    return '<h4>Reasons</h4><ul class="reasons">%s</ul>' % "".join(rows)


def _error_html(view):
    error = view["error"]
    if not error:
        return ""
    return '<div class="error"><strong>%s</strong><br>%s</div>' % (
        _text(error.get("category"), "error"), _text(error.get("message"), ""))


def _summary_html(view):
    if not view["summary"]:
        return ""
    return '<p class="model-summary"><strong>Model summary:</strong> %s</p>' % (
        _text(view["summary"]))


def _expectation_html(value):
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


def _checklist_html(view):
    checks = view["checks"]
    if not checks:
        return ""
    rows = "".join(
        "<tr><td><code>%s</code></td><td>%s</td><td>%s</td></tr>" % (
            _text(check.get("id")), _badge(check.get("status")),
            _text(check.get("reason"), ""))
        for check in checks)
    return (
        '<details class="checks"><summary>Model checklist (%d)</summary>'
        "<table><thead><tr><th>Check</th><th>Status</th><th>Reason</th>"
        "</tr></thead><tbody>%s</tbody></table></details>"
        % (len(checks), rows))


def _parity_html(view):
    parity = next(
        (item for item in view["checks"] if item.get("id") == "source_parity"),
        None)
    if view["source"] != "parity" or parity is None:
        return ""
    return (
        '<div class="parity-result"><strong>Designer ↔ real printer:</strong>'
        " %s %s</div>" % (
            _badge(parity.get("status")), _text(parity.get("reason"), "")))


def _meta_html(view):
    elapsed = view["elapsed"]
    rows = (
        ("Source", view["source"]),
        ("Page", view["page"] if view["page"] != NO_PAGE else None),
        ("Case", view["case_id"]),
        ("Semantic page", view["semantic_page_id"]),
        ("JSON", view["validation"].get("status")),
        ("Elapsed", "%.3f s" % elapsed if elapsed is not None else None),
        ("Attempts", view["attempts"]),
        ("File", view["file"]),
    )
    return "<dl>%s</dl>" % "".join(
        "<div><dt>%s</dt><dd>%s</dd></div>" % (_text(name), _text(value))
        for name, value in rows)


def _images_html(view):
    designer = view["source"] == "parity"
    images = []
    if view["image"] or not view["comparison"]:
        images.append(_figure(
            view["image"], view["title"],
            "Designer" if designer else view["source"]))
    if view["comparison"]:
        images.append(_figure(
            view["comparison"], "Comparison for " + view["title"],
            "Real printer Typer/framebuffer" if designer else "comparison"))
    return "".join(images)


def _detail(view):
    references = view["references"]
    references_html = ""
    if references:
        references_html = (
            "<details><summary>Baseline references (%d)</summary><ul>%s</ul>"
            "</details>" % (
                len(references),
                "".join("<li><code>%s</code></li>" % _text(item)
                        for item in references)))
    return (
        '<article class="frame %s"><header><div>'
        '<span class="index">#%d</span><h3>%s</h3></div>%s</header>'
        '<div class="images">%s</div>%s%s%s%s%s%s%s%s</article>' % (
            view["outcome"], view["number"], _text(view["title"]),
            _badge(view["verdict"]), _images_html(view), _parity_html(view),
            _meta_html(view), _error_html(view), _summary_html(view),
            _reasons_html(view), _expectation_html(view["expectation"]),
            _checklist_html(view), references_html))


def _tile(view):
    thumbnails = []
    if view["image"]:
        thumbnails.append(
            '<span class="thumb"><img loading="lazy" src="%s" alt="%s">%s'
            "</span>" % (
                view["image"], _text(view["title"]),
                '<span class="corner-label">Designer</span>'
                if view["source"] == "parity" else ""))
    if view["comparison"]:
        thumbnails.append(
            '<span class="thumb"><img loading="lazy" src="%s" '
            'alt="Real printer comparison for %s">'
            '<span class="corner-label">Printer</span></span>' % (
                view["comparison"], _text(view["title"])))
    if not thumbnails:
        thumbnails.append(
            '<span class="thumb image-missing">Image unavailable</span>')
    return (
        '<button class="shot-tile %s %s" type="button" data-item '
        'data-frame="%d" data-outcome="%s" data-source="%s" data-page="%s" '
        'data-search="%s" aria-haspopup="dialog">'
        '<span class="shot-images">%s</span><span class="shot-caption">'
        '<span class="shot-title">%s</span>'
        '<span class="shot-meta">#%d · %s</span></span>%s</button>' % (
            view["outcome"], "dual" if view["comparison"] else "solo",
            view["number"], _tokens([view["outcome"]]),
            _tokens([view["source"]]), _tokens([view["page"]]),
            _text(_search_text(view)), "".join(thumbnails),
            _text(view["title"]), view["number"],
            _text("Designer ↔ printer" if view["source"] == "parity"
                  else view["source"]),
            '<span class="problem-marker">%s</span>' % _text(view["verdict"])
            if view["outcome"] != "pass" else ""))


def _chips(name, label, options):
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
        '<div class="chips" data-filter="%s">'
        '<button type="button" data-value="all" aria-pressed="true">All'
        "</button>%s</div>" % (_text(label), name, buttons))


def _outcome_chips(views):
    counts = _count(views, lambda item: item["outcome"])
    return _chips("outcome", "Outcome", [
        (value, label, counts[value]) for value, label in OUTCOMES
        if counts[value]])


def _source_chips(views):
    counts = _count(views, lambda item: item["source"])
    return _chips("source", "Source", [
        (value, {"printer": "Real printer"}.get(value, value.capitalize()),
         counts[value])
        for value in ("designer", "printer", "parity") if counts[value]])


def _page_select(views):
    pages = sorted(set(item["page"] for item in views))
    return (
        '<span class="toolbar-label">Page</span>'
        '<select data-filter="page" aria-label="Page"><option value="all">'
        "All pages</option>%s</select>" % "".join(
            '<option value="%s">%s</option>' % (_text(page), _text(page))
            for page in pages))


def _search_box():
    return (
        '<input type="search" data-search placeholder="Search title, case, '
        'reason…" aria-label="Search">')


# --- pages ------------------------------------------------------------------


def _overview(report, views, names):
    counts = _count(views, lambda item: item["outcome"])
    bar = "".join(
        '<span class="%s" style="flex:%d" title="%s: %d"></span>' % (
            value, counts[value], _text(label), counts[value])
        for value, label in OUTCOMES if counts[value])
    legend = "".join(
        '<a href="%s"><span class="dot %s"></span>%s <strong>%d</strong></a>'
        % (_link(names, "gallery", outcome=value), value, _text(label),
           counts[value])
        for value, label in OUTCOMES if counts[value])
    problems = [item for item in views if item["outcome"] in ("warn", "fail")]
    problems.sort(key=lambda item: (item["outcome"] != "fail", item["number"]))
    attention = "".join(
        '<a class="%s" href="%s"><img loading="lazy" src="%s" alt="%s">'
        '<span>%s<span class="attention-title">%s</span></span></a>' % (
            item["outcome"], _frame_link(names, item),
            item["image"] or "", _text(item["title"]),
            _badge(item["verdict"]), _text(item["title"]))
        for item in problems[:8] if item["image"])
    pairs = sum(1 for item in views if item["comparison"])
    missing = len(report.get("missing_expectations") or ())
    aspects = (
        ("gallery", "Every screenshot at inspection scale, filterable by "
         "outcome, source, page, and text.",
         "%d frames" % len(views)),
        ("problems", "Warnings and failures with model reasons, grouped by "
         "check, evidence class, and error.",
         "%d to review" % len(problems)),
        ("compare", "Designer against the real printer with side-by-side, "
         "swipe, and difference views.",
         "%d pairs" % pairs),
        ("coverage", "Which pages and cases were rendered, retained from the "
         "printer, or paired.",
         "%d pages" % len(set(
             item["semantic_page_id"] for item in views
             if item["semantic_page_id"]))),
        ("baselines", "The textual expectation behind every case and the "
         "cases still missing one.",
         "%d missing" % missing if missing else "complete"),
        ("run", "Pipeline stages, model configuration, timing, and error "
         "statistics.",
         "%d stages" % len(report.get("pipeline") or ())),
    )
    cards = "".join(
        '<a class="aspect" href="%s"><h3>%s</h3><p>%s</p><strong>%s</strong>'
        "</a>" % (_link(names, slug), _text(dict(PAGES)[slug]), _text(text),
                  _text(headline))
        for slug, text, headline in aspects)
    attention_html = ""
    if problems:
        attention_html = (
            '<section><h2>Needs attention</h2><div class="attention">%s</div>'
            '<p><a href="%s">All %d problems</a></p></section>' % (
                attention, _link(names, "problems"), len(problems)))
    return (
        '<section><h2>Outcome</h2><div class="bar">%s</div>'
        '<div class="legend">%s</div></section>%s'
        '<section><h2>Review aspects</h2><div class="aspects">%s</div>'
        "</section>" % (
            bar, legend or '<span class="empty">No frames.</span>',
            attention_html, cards))


def _gallery(views):
    standalone = [item for item in views if item["source"] != "parity"]
    parity = [item for item in views if item["source"] == "parity"]

    def section(title, description, items, pair=False):
        if not items:
            return ""
        return (
            '<section class="gallery-section" data-section>'
            '<header class="gallery-heading"><div><h2>%s</h2><p>%s</p></div>'
            '<span data-section-count>%d</span></header>'
            '<div class="shot-grid %s">%s</div></section>' % (
                _text(title), _text(description), len(items),
                "pair-grid" if pair else "",
                "".join(_tile(item) for item in items)))

    body = section(
        "Screenshot overview",
        "Designer pages and retained real-printer screens.", standalone
    ) + section(
        "Designer ↔ real printer",
        "Pairwise component parity. Each tile shows both renderers.",
        parity, pair=True)
    if not body:
        return '<p class="empty">No screenshots reached the review stage.</p>'
    toolbar = (
        '<nav class="toolbar" aria-label="Frame filter">%s%s%s%s'
        '<span class="spacer"></span><span class="result-count" data-count>'
        '</span><label class="toolbar-label">Size <input type="range" '
        'min="240" max="900" step="20" value="480" data-tile-size></label>'
        "</nav>" % (
            _outcome_chips(views), _source_chips(views), _page_select(views),
            _search_box()))
    templates = "".join(
        '<template id="detail-%d">%s</template>' % (item["number"], _detail(item))
        for item in views)
    dialog = (
        '<dialog id="frame-dialog" aria-label="Screenshot details">'
        '<div class="modal-head"><div class="nav">'
        '<button type="button" data-step="-1" aria-label="Previous frame">'
        "←</button>"
        '<button type="button" data-step="1" aria-label="Next frame">→'
        '</button><span class="modal-position result-count"></span></div>'
        '<button class="modal-close" type="button" aria-label="Close">×'
        '</button></div><div class="modal-content"></div></dialog>')
    return toolbar + body + templates + dialog


def _problem_row(view, names):
    checks, evidence, errors = _problem_tokens(view)
    images = "".join(
        '<img loading="lazy" src="%s" alt="%s">' % (url, _text(view["title"]))
        for url in (view["image"], view["comparison"]) if url)
    tags = "".join(
        '<span class="tag">%s</span>' % _text(item)
        for item in checks + evidence + ["error: " + item for item in errors])
    return (
        '<article class="problem %s" data-item data-outcome="%s" '
        'data-check="%s" data-evidence="%s" data-error="%s" data-page="%s" '
        'data-source="%s" data-search="%s"><div class="problem-images">%s'
        '</div><div><header><h3>%s</h3>%s<a href="%s">Open details</a>'
        "</header><p>%s</p>%s%s%s</div></article>" % (
            view["outcome"], _tokens([view["outcome"]]), _tokens(checks),
            _tokens(evidence), _tokens(errors), _tokens([view["page"]]),
            _tokens([view["source"]]), _text(_search_text(view)),
            images or '<div class="image-missing">Image unavailable</div>',
            _text(view["title"]), _badge(view["verdict"]),
            _frame_link(names, view), tags, _summary_html(view),
            _reasons_html(view), _error_html(view)))


def _problems(views, names):
    problems = [item for item in views if item["outcome"] in ("warn", "fail")]
    unreviewed = sum(1 for item in views if item["outcome"] == "muted")
    note = ""
    if unreviewed:
        note = (
            '<p class="muted">%d frame(s) were not reviewed by a model; open '
            'the <a href="%s">gallery</a> to look at them.</p>' % (
                unreviewed, _link(names, "gallery", outcome="muted")))
    if not problems:
        return note + '<p class="empty">No warnings or failures.</p>'
    problems.sort(key=lambda item: (item["outcome"] != "fail", item["number"]))
    check_counts, evidence_counts, error_counts = (
        collections.Counter(), collections.Counter(), collections.Counter())
    for item in problems:
        checks, evidence, errors = _problem_tokens(item)
        check_counts.update(checks)
        evidence_counts.update(evidence)
        error_counts.update(errors)

    def facet(name, label, counts):
        if not counts:
            return ""
        return _chips(name, label, [
            (value, value, count) for value, count in sorted(
                counts.items(), key=lambda pair: (-pair[1], pair[0]))])

    toolbar = (
        '<nav class="toolbar" aria-label="Problem filter">%s%s%s%s%s%s'
        '<span class="spacer"></span><span class="result-count" data-count>'
        "</span></nav>" % (
            _outcome_chips(problems), facet("check", "Check", check_counts),
            facet("evidence", "Evidence", evidence_counts),
            facet("error", "Error", error_counts), _page_select(problems),
            _search_box()))
    return note + toolbar + '<div class="problem-list">%s</div>' % "".join(
        _problem_row(item, names) for item in problems)


def _figure(url, alt, caption):
    image = (
        '<img loading="lazy" src="%s" alt="%s">' % (url, _text(alt))
        if url else '<div class="image-missing">Image unavailable</div>')
    return "<figure>%s<figcaption>%s</figcaption></figure>" % (
        image, _text(caption))


def _compare(views, names):
    pairs = [item for item in views if item["comparison"]]
    if not pairs:
        return (
            '<p class="empty">No Designer ↔ real-printer pairs in this run. '
            "Run <code>--mode parity</code> to capture them.</p>")
    entries = "".join(
        '<button class="pair-item %s" type="button" data-item data-pair="%d" '
        'data-outcome="%s" data-page="%s" data-search="%s" aria-pressed="false">'
        "<span>%s</span>%s</button>" % (
            item["outcome"], item["number"], _tokens([item["outcome"]]),
            _tokens([item["page"]]), _text(_search_text(item)),
            _text(item["title"]), _badge(item["verdict"]))
        for item in pairs)
    sections = "".join(
        '<section class="pair" id="pair-%d" hidden><header class="frame">'
        '<h3>%s</h3> %s <a href="%s">Open details</a></header>'
        '<div class="viewer" data-mode="side">%s%s</div>%s%s%s%s</section>' % (
            item["number"], _text(item["title"]), _badge(item["verdict"]),
            _frame_link(names, item),
            _figure(item["image"], "Designer frame", "Designer"),
            _figure(item["comparison"], "Real printer frame",
                    "Real printer Typer/framebuffer"),
            _parity_html(item), _summary_html(item), _reasons_html(item),
            _error_html(item))
        for item in pairs)
    toolbar = (
        '<nav class="toolbar" aria-label="Compare view">'
        '<span class="toolbar-label">View</span><div class="chips">'
        '<button type="button" data-view="side" aria-pressed="true">'
        "Side by side</button>"
        '<button type="button" data-view="swipe" aria-pressed="false">Swipe'
        "</button>"
        '<button type="button" data-view="diff" aria-pressed="false">'
        "Difference</button></div>"
        '<span class="spacer"></span><span class="viewer-hint">Arrow keys '
        "switch pairs. Difference view shows black where both match.</span>"
        "</nav>"
        '<div class="swipe-control"><label>Designer ← → Printer '
        '<input type="range" min="0" max="100" value="50" data-cut></label>'
        "</div>")
    return (
        '%s<div class="compare"><aside><nav class="toolbar" '
        'aria-label="Pair filter">%s%s<span class="result-count" data-count>'
        '</span></nav><div class="pair-list">%s</div></aside><div>%s</div>'
        "</div>" % (
            toolbar, _outcome_chips(pairs), _search_box(), entries,
            sections))


def _coverage(report, views, names):
    pages = {}
    for item in views:
        pages.setdefault(item["semantic_page_id"], []).append(item)
    rows = []
    for identifier, items in sorted(
            pages.items(), key=lambda pair: (pair[0] == "", pair[0])):
        sources = _count(items, lambda item: item["source"])
        worst = min((item["outcome"] for item in items), key=SEVERITY.get)
        label = items[0]["page"] if identifier else NO_PAGE
        cases = "".join(
            '<li><span class="badge source">%s</span><a href="%s">%s</a>%s'
            "</li>" % (
                _text(item["source"]), _frame_link(names, item),
                _text(item["title"]), _badge(item["verdict"]))
            for item in items)
        rows.append(
            '<details class="page-row" data-item data-search="%s">'
            "<summary><span><strong>%s</strong><br>"
            '<span class="muted" title="%s">%s</span></span>'
            '<span class="num"><strong>%d</strong><br>designer</span>'
            '<span class="num"><strong>%d</strong><br>printer</span>'
            '<span class="num"><strong>%d</strong><br>parity</span>'
            '<span class="num"><strong>%d</strong><br>total</span>%s'
            '</summary><ul class="case-list">%s</ul></details>' % (
                _text(" ".join(_search_text(item) for item in items)),
                _text(label), _text(identifier), _text(
                    identifier.rsplit(".", 1)[-1] if identifier else "legacy"),
                sources["designer"], sources["printer"], sources["parity"],
                len(items), _badge({"muted": "not_run"}.get(worst, worst)),
                cases))
    discovered = [
        item for item in report.get("discovered_page_ids") or ()
        if item not in pages]
    alert = ""
    if discovered:
        alert = (
            '<section class="alert warn"><h2>Discovered but not captured</h2>'
            "<ul>%s</ul></section>" % "".join(
                "<li><code>%s</code></li>" % _text(item)
                for item in discovered))
    if not rows:
        return alert + '<p class="empty">No frames to cover.</p>'
    return (
        alert +
        '<nav class="toolbar" aria-label="Coverage filter">%s'
        '<span class="spacer"></span><span class="result-count" data-count>'
        "</span></nav>"
        '<div class="column-head"><span>Page</span><span>Designer</span>'
        "<span>Printer</span><span>Parity</span><span>Total</span>"
        "<span>Worst</span></div>%s" % (_search_box(), "".join(rows)))


def _baseline_card(case_id, views, expectation, missing=False):
    sources = sorted(set(item["source"] for item in views))
    outcome = min(
        (item["outcome"] for item in views), key=SEVERITY.get,
        default="muted")
    return (
        '<article class="baseline%s" data-item data-outcome="%s" '
        'data-search="%s"><header><h3>%s</h3><div>%s%s</div></header>%s'
        "</article>" % (
            " missing" if missing else "", _tokens([outcome]),
            _text(case_id.lower() + " " + str(
                (expectation or {}).get("description", "")).lower()),
            _text(case_id),
            "".join('<span class="badge source">%s</span> ' % _text(item)
                    for item in sources),
            _badge("needs_baseline" if missing else outcome),
            _expectation_html(expectation)))


def _baselines(report, views):
    cases = {}
    for item in views:
        if item["expectation"] is None:
            continue
        cases.setdefault(item["case_id"] or item["title"], []).append(item)
    cards = [
        _baseline_card(case_id, items, items[0]["expectation"])
        for case_id, items in cases.items()]
    missing = [
        _baseline_card(item.get("case_id", "?"), [], item, missing=True)
        for item in report.get("missing_expectations") or ()]
    if not cards and not missing:
        return '<p class="empty">No textual baselines attached to this run.</p>'
    body = ""
    if missing:
        body += (
            '<section class="alert warn"><h2>Missing baselines (%d)</h2>'
            "<p>These cases need a reviewed baseline; candidates were written "
            "to <code>expectations.candidate.json</code>.</p>"
            '<div class="baseline-grid">%s</div></section>' % (
                len(missing), "".join(missing)))
    return (
        '<nav class="toolbar" aria-label="Baseline filter">%s%s'
        '<span class="spacer"></span><span class="result-count" data-count>'
        "</span></nav>%s<section><h2>Baselines (%d)</h2>"
        '<div class="baseline-grid">%s</div></section>' % (
            _outcome_chips([item for items in cases.values() for item in items]),
            _search_box(), body, len(cards), "".join(cards)))


def _stages(report):
    cards = []
    for index, stage in enumerate(report.get("pipeline") or (), 1):
        counts = "".join(
            "<span><code>%s</code>: %s</span>" % (
                _text(str(key).replace("_", " ")), _text(value))
            for key, value in (stage.get("counts") or {}).items())
        runs = stage.get("runs") or ()
        run_html = ""
        if runs:
            run_html = '<ul class="runs">%s</ul>' % "".join(
                "<li><strong>%s</strong> — %s frames<br><code>%s</code></li>"
                % (_text(item.get("suite")), _text(item.get("captured", 0)),
                   _text(item.get("run_id")))
                for item in runs)
        cards.append(
            '<article class="stage %s"><header><span class="stage-number">%d'
            "</span><div><h3>%s</h3>%s</div></header><p>%s</p>"
            '<div class="stage-counts">%s</div>%s</article>' % (
                _class(stage.get("status")), index, _text(stage.get("title")),
                _badge(stage.get("status")), _text(stage.get("summary"), ""),
                counts, run_html))
    return cards


def _run(report, views, names):
    configuration = report.get("configuration") or {}
    coverage = report.get("coverage") or {}
    reviewed = [item for item in views if item["elapsed"]]
    total = sum(item["elapsed"] for item in reviewed)
    settings = (
        ("Model", configuration.get("model") or "disabled"),
        ("Backend", configuration.get("backend", "openai-compatible")),
        ("Reasoning effort", configuration.get("reasoning_effort") or "server default"),
        ("Review workers", configuration.get("review_workers", 1)),
        ("Check mode", configuration.get("mode")),
        ("Designer theme", configuration.get("designer_theme") or "default"),
        ("Timeout", configuration.get("timeout")),
        ("Printer captured", coverage.get("printer_captured", 0)),
        ("Printer retained", coverage.get("legacy_printer", 0)),
        ("Replaced by Designer", coverage.get("replaced", 0)),
        ("Reviewed frames", len(reviewed)),
        ("Review time", "%.1f s" % total if reviewed else None),
        ("Mean per frame", "%.2f s" % (total / len(reviewed))
         if reviewed else None),
        ("Retried frames", sum(
            1 for item in views if (item["attempts"] or 1) > 1)),
    )
    config_html = "<dl>%s</dl>" % "".join(
        "<div><dt>%s</dt><dd>%s</dd></div>" % (_text(name), _text(value))
        for name, value in settings)
    validation = _count(views, lambda item: item["validation"].get("status"))
    errors = _count(
        [item for item in views if item["error"]],
        lambda item: item["error"].get("category") or "error")
    statistics = "".join(
        "<table><thead><tr><th>%s</th><th>Frames</th></tr></thead><tbody>%s"
        "</tbody></table>" % (
            _text(title), "".join(
                "<tr><td>%s</td><td>%d</td></tr>" % (_text(key), count)
                for key, count in sorted(counter.items())))
        for title, counter in (
            ("JSON validation", validation), ("Error category", errors))
        if counter)
    slowest = sorted(reviewed, key=lambda item: -item["elapsed"])[
        :SLOWEST_FRAMES]
    slowest_html = ""
    if slowest:
        slowest_html = (
            "<h2>Slowest frames</h2><table><thead><tr><th>Frame</th>"
            "<th>Elapsed</th><th>Attempts</th></tr></thead><tbody>%s"
            "</tbody></table>" % "".join(
                '<tr><td><a href="%s">%s</a></td><td>%.2f s</td><td>%s</td>'
                "</tr>" % (_frame_link(names, item), _text(item["title"]),
                           item["elapsed"], _text(item["attempts"])) for item in slowest))
    checklist = report.get("checklist") or ()
    checklist_html = ""
    if checklist:
        checklist_html = (
            "<details><summary>Model checklist (%d)</summary><table><thead>"
            "<tr><th>Check</th><th>Description</th></tr></thead><tbody>%s"
            "</tbody></table></details>" % (
                len(checklist), "".join(
                    "<tr><td><code>%s</code></td><td>%s</td></tr>" % (
                        _text(item.get("id")), _text(item.get("description")))
                    for item in checklist)))
    stages = _stages(report)
    files = "".join(
        '<li><a href="%s">%s</a></li>' % (_text(name), _text(name))
        for name in ("report.json", "report.md"))
    return (
        "<section><h2>Configuration</h2>%s</section>"
        '<section><h2>Collection stages</h2><div class="pipeline-grid">%s'
        '</div></section><section class="two-column"><div>%s</div>'
        "<div>%s</div></section><section>%s</section>"
        "<section><h2>Artifacts</h2><ul>%s</ul></section>" % (
            config_html, "".join(stages) or '<p class="empty">No stages '
            "recorded.</p>", statistics, slowest_html, checklist_html, files))


# --- assembly ---------------------------------------------------------------


def _navigation(names, current, views, report):
    problems = sum(1 for item in views if item["outcome"] in ("warn", "fail"))
    pairs = sum(1 for item in views if item["comparison"])
    missing = len(report.get("missing_expectations") or ())
    counts = {
        "gallery": (len(views), ""),
        "problems": (problems, "fail" if problems else ""),
        "compare": (pairs, ""),
        "baselines": (missing, "warn") if missing else (None, ""),
    }
    links = []
    for slug, title in PAGES:
        count, tone = counts.get(slug, (None, ""))
        links.append(
            '<a href="%s"%s>%s%s</a>' % (
                _text(names[slug]),
                ' aria-current="page"' if slug == current else "",
                _text(title),
                '<span class="count %s">%d</span>' % (tone, count)
                if count is not None else ""))
    return '<nav class="tabs" aria-label="Report pages">%s</nav>' % "".join(
        links)


def _summary_cards(report, views):
    coverage = report.get("coverage") or {}
    configuration = report.get("configuration") or {}
    verdicts = (report.get("summary") or {}).get("verdicts") or {}
    problems = int(verdicts.get("warn", 0)) + int(verdicts.get("fail", 0))
    cards = (
        ("Status", _badge(report.get("status", "unknown"))),
        ("Mode", _text(report.get("mode"))),
        ("Theme", _text(configuration.get("designer_theme"), "default")),
        ("Frames", _text(len(views), "0")),
        ("Parity pairs", _text(coverage.get("parity_pairs", 0))),
        ("Problems", _text(problems)),
    )
    return '<section class="summary">%s</section>' % "".join(
        '<div class="metric"><span>%s</span><strong>%s</strong></div>' % (
            _text(label), value) for label, value in cards)


def _alerts(report):
    alerts = []
    error = report.get("infrastructure_error")
    if isinstance(error, dict):
        alerts.append(
            '<section class="alert fail"><h2>Infrastructure failure</h2>'
            "<p><strong>%s</strong></p><p>%s</p></section>" % (
                _text(error.get("category"), "error"),
                _text(error.get("message"), "")))
    missing = report.get("missing_expectations") or ()
    if missing:
        alerts.append(
            '<section class="alert warn"><h2>Baselines required</h2>'
            "<p>%d case(s) need a reviewed textual baseline.</p></section>"
            % len(missing))
    return "".join(alerts)


def _document(report, views, names, slug, body):
    status = report.get("status", "unknown")
    title = dict(PAGES)[slug]
    configuration = report.get("configuration") or {}
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FF5M UI regression — %(title)s — %(status_text)s</title>
<link rel="stylesheet" href="report.css">
</head>
<body data-page="%(slug)s">
<main>
  <header class="report-head">
    <h1>FF5M UI regression</h1>%(status)s
    <span class="run-meta">%(mode)s · %(model)s</span>
  </header>
  %(nav)s
  %(alerts)s
  %(body)s
</main>
<script src="report.js"></script>
</body>
</html>
""" % {
        "title": _text(title),
        "status_text": _text(status),
        "slug": slug,
        "status": _badge(status),
        "mode": _text(report.get("mode")),
        "model": _text(configuration.get("model"), "no model"),
        "nav": _navigation(names, slug, views, report),
        "alerts": _alerts(report) if slug == "overview" else "",
        "body": (
            _summary_cards(report, views) if slug == "overview" else "")
        + body,
    }


def render(report, entry="report.html"):
    """Return every report page as ``{file name: HTML text}``.

    The overview is written under ``entry``; the other pages sit beside it.
    """
    names = _page_names(entry)
    views = [
        _view(frame, number)
        for number, frame in enumerate(report.get("screenshots") or (), 1)]
    bodies = {
        "overview": _overview(report, views, names),
        "gallery": _gallery(views),
        "problems": _problems(views, names),
        "compare": _compare(views, names),
        "coverage": _coverage(report, views, names),
        "baselines": _baselines(report, views),
        "run": _run(report, views, names),
    }
    return {
        names[slug]: _document(report, views, names, slug, bodies[slug])
        for slug, _title in PAGES
    }


def _replace(path, writer):
    temporary = path.with_name(path.name + ".tmp")
    writer(temporary)
    temporary.replace(path)


def write(path, report):
    """Atomically write all report pages and their shared assets."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    for name, page in render(report, path.name).items():
        _replace(path.parent / name, lambda target, page=page: (
            target.write_text(page, encoding="utf-8")))
    for name in ASSET_NAMES:
        _replace(path.parent / name, lambda target, name=name: (
            shutil.copyfile(ASSETS / name, target)))
