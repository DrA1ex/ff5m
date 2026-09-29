## Dependency-free SVG charts for the printer-regression report.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Draw time-series charts as inline SVG.

Charts are rendered on the host at report time so the report works offline
and its output can be inspected in tests.  Every chart embeds its (decimated)
series in ``data-series`` so the page script can show values under the cursor
without redrawing anything.
"""

import html
import json
import math

WIDTH = 1000
PLOT = {"left": 58, "right": 12, "top": 10, "bottom": 24}
POINT_LIMIT = 700
BAND_COLORS = ("#ffffff0a", "#ffffff18")


def _esc(value):
    return html.escape(str(value), quote=True)


def format_time(seconds):
    """``m:ss`` (or ``h:mm:ss``) for a non-negative number of seconds."""
    seconds = int(max(0, round(seconds)))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return "%d:%02d:%02d" % (hours, minutes, seconds)
    return "%d:%02d" % (minutes, seconds)


def _nice_step(span, target):
    raw = span / max(target, 1)
    magnitude = 10 ** math.floor(math.log10(raw))
    for factor in (1, 2, 5, 10):
        if raw <= factor * magnitude:
            return factor * magnitude
    return 10 * magnitude


def _value_ticks(low, high, target=4):
    step = _nice_step(high - low, target)
    first = math.ceil(low / step) * step
    ticks = []
    value = first
    while value <= high + step * 1e-9:
        ticks.append(round(value, 10))
        value += step
    return ticks


def _time_ticks(x_max, target=8):
    steps = (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600)
    step = next((item for item in steps if x_max / item <= target), 7200)
    return [index * step for index in range(int(x_max // step) + 1)]


def decimate(points, limit=POINT_LIMIT):
    """Drop missing values and reduce to about ``limit`` points.

    Each bucket keeps its minimum and maximum so spikes survive.  Missing
    samples are bridged rather than drawn as gaps: a chart reads as a trend.
    """
    points = [item for item in points if item[1] is not None]
    if len(points) <= limit:
        return points
    size = len(points) / (limit / 2.0)
    kept = []
    position = 0.0
    while position < len(points):
        bucket = points[int(position):int(position + size) or None]
        position += size
        if not bucket:
            continue
        low = min(bucket, key=lambda item: item[1])
        high = max(bucket, key=lambda item: item[1])
        kept.extend(sorted({low, high}, key=lambda item: item[0]))
    return kept


def _range(series, y_min, y_max):
    values = [y for item in series for _x, y in item["points"] if y is not None]
    low = min(values) if values else 0.0
    high = max(values) if values else 1.0
    if y_min is not None:
        low = y_min
    if y_max is not None:
        high = y_max
    if high - low < 1e-9:
        high = low + 1.0
    pad = 0.0 if (y_min is not None and y_max is not None) else (
        (high - low) * 0.06)
    low = y_min if y_min is not None else low - pad
    high = y_max if y_max is not None else high + pad
    return low, high


def line_chart(title, series, x_max, unit="", bands=(), markers=(),
               y_min=None, y_max=None, height=170, anchor=None):
    """Return a ``<figure>`` with a legend, an SVG line chart, and a readout.

    ``series`` items are ``{"name", "color", "points": [(x, y)], "dash"}``.
    ``bands`` are ``(x0, x1, label)`` spans drawn behind the lines and
    ``markers`` are ``(x, label, tone)`` vertical rules.  ``anchor`` makes the
    figure linkable as ``#chart-<anchor>``.
    """
    figure_id = ' id="chart-%s"' % _esc(anchor) if anchor else ""
    x_max = max(float(x_max), 1.0)
    series = [
        dict(item, points=decimate(item["points"])) for item in series
        if any(y is not None for _x, y in item["points"])]
    low, high = _range(series, y_min, y_max)
    left, right = PLOT["left"], PLOT["right"]
    top, bottom = PLOT["top"], PLOT["bottom"]
    plot_w = WIDTH - left - right
    plot_h = height - top - bottom

    def px(x):
        return left + min(max(x / x_max, 0.0), 1.0) * plot_w

    def py(y):
        return top + plot_h * (1.0 - (y - low) / (high - low))

    parts = []
    for tick in _value_ticks(low, high):
        parts.append(
            '<line class="grid" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/>'
            '<text class="tick" x="%d" y="%.1f" text-anchor="end">%s</text>'
            % (left, WIDTH - right, py(tick), py(tick), left - 6,
               py(tick) + 3.5, _esc(("%g" % round(tick, 3)))))
    for tick in _time_ticks(x_max):
        parts.append(
            '<line class="grid vertical" x1="%.1f" x2="%.1f" y1="%d" y2="%d"/>'
            '<text class="tick" x="%.1f" y="%d" text-anchor="middle">%s</text>'
            % (px(tick), px(tick), top, top + plot_h, px(tick),
               height - 7, format_time(tick)))
    for index, (start, end, label) in enumerate(bands):
        x0, x1 = px(start), px(end)
        if x1 - x0 < 0.5:
            continue
        parts.append(
            '<rect class="band" x="%.1f" y="%d" width="%.1f" height="%d" '
            'fill="%s"><title>%s</title></rect>' % (
                x0, top, x1 - x0, plot_h, BAND_COLORS[index % 2],
                _esc(label)))
        if x1 - x0 > 7 * len(str(label)):
            parts.append(
                '<text class="band-label" x="%.1f" y="%d">%s</text>' % (
                    x0 + 3, top + 10, _esc(label)))
    for item in series:
        parts.append(
            '<polyline class="line%s" fill="none" stroke="%s" '
            'points="%s"/>' % (
                " dash" if item.get("dash") else "", _esc(item["color"]),
                " ".join("%.1f,%.1f" % (px(x), py(y))
                         for x, y in item["points"])))
    for x, label, tone in markers:
        parts.append(
            '<line class="marker %s" x1="%.1f" x2="%.1f" y1="%d" y2="%d">'
            "<title>%s</title></line>" % (
                _esc(tone), px(x), px(x), top, top + plot_h, _esc(label)))
    parts.append(
        '<line class="cursor" x1="0" x2="0" y1="%d" y2="%d" hidden/>'
        % (top, top + plot_h))
    data = [
        [item["name"], item["color"],
         [[round(x, 2), round(y, 3)] for x, y in item["points"]]]
        for item in series]
    legend = "".join(
        '<span class="swatch"><i style="background:%s"></i>%s</span>' % (
            _esc(item["color"]), _esc(item["name"])) for item in series)
    if not series:
        return (
            '<figure class="chart-card"%s><figcaption><strong>%s</strong>'
            '</figcaption><p class="empty">No samples recorded.</p></figure>'
            % (figure_id, _esc(title)))
    return (
        '<figure class="chart-card"%s><figcaption><strong>%s</strong>'
        '<span class="legend">%s</span>'
        '<span class="chart-readout" aria-live="off"></span></figcaption>'
        '<svg class="chart" viewBox="0 0 %d %d" role="img" aria-label="%s" '
        'data-xmax="%.3f" data-ymin="%.6f" data-ymax="%.6f" '
        'data-plot="%d,%d,%d,%d" data-unit="%s" data-series="%s">%s</svg>'
        "</figure>" % (
            figure_id, _esc(title), legend, WIDTH, height, _esc(title), x_max,
            low, high,
            left, top, plot_w, plot_h, _esc(unit),
            _esc(json.dumps(data, separators=(",", ":"))), "".join(parts)))


def _heat_color(value, limit):
    """Blue for negative, red for positive, neutral near zero."""
    if limit <= 0:
        return "#2b3a45"
    ratio = max(-1.0, min(1.0, value / limit))
    base = (43, 58, 69)
    target = (251, 113, 133) if ratio > 0 else (96, 165, 250)
    mix = abs(ratio)
    return "#%02x%02x%02x" % tuple(
        int(round(a + (b - a) * mix)) for a, b in zip(base, target))


def heatmap(matrix, unit="mm", decimals=3):
    """A compact SVG grid of numbers, colored by sign and magnitude."""
    rows = [list(row) for row in matrix if isinstance(row, (list, tuple))]
    if not rows or not rows[0]:
        return ""
    limit = max(abs(v) for row in rows for v in row if v is not None) or 1.0
    cell_w, cell_h = 92, 46
    width, height = cell_w * len(rows[0]), cell_h * len(rows)
    cells = []
    for y, row in enumerate(rows):
        for x, value in enumerate(row):
            cells.append(
                '<g><rect x="%d" y="%d" width="%d" height="%d" rx="4" '
                'fill="%s"/><text x="%d" y="%d" text-anchor="middle">%s'
                "</text></g>" % (
                    x * cell_w + 2, y * cell_h + 2, cell_w - 4, cell_h - 4,
                    _heat_color(value, limit), x * cell_w + cell_w // 2,
                    y * cell_h + cell_h // 2 + 5,
                    _esc(("%+." + str(decimals) + "f") % value)
                    if value is not None else "—"))
    return (
        '<svg class="heatmap" viewBox="0 0 %d %d" role="img" '
        'aria-label="Bed mesh heights in %s">%s</svg>' % (
            width, height, _esc(unit), "".join(cells)))
