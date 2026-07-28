"""Validated Typer font metrics and the shared word-v1 layout algorithm."""

import json
import logging
import os
import re
import subprocess


SCHEMA = "font-metrics/v1"
WRAP_ALGORITHM = "word-v1"
FALLBACK_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                             "font_metrics.json")
_FONT_NAME = re.compile(r"^(.+) (\d+)pt$")


class FontMetric:
    __slots__ = (
        "name", "advance_x", "monospaced", "advance_y", "top", "bottom",
        "unicode_ranges", "_range_offsets",
    )

    def __init__(self, name, advance_x, monospaced, advance_y, top, bottom,
                 unicode_ranges):
        self.name = name
        self.advance_x = advance_x
        self.monospaced = monospaced
        self.advance_y = advance_y
        self.top = top
        self.bottom = bottom
        self.unicode_ranges = unicode_ranges
        offsets = []
        offset = 0
        for start, end in unicode_ranges:
            offsets.append((start, end, offset))
            offset += end - start + 1
        self._range_offsets = tuple(offsets)

    @property
    def glyph_height(self):
        return self.bottom - self.top

    def character_advance(self, character):
        codepoint = ord(character)
        for start, end, offset in self._range_offsets:
            if start <= codepoint <= end:
                if self.monospaced:
                    return self.advance_x
                return self.advance_x[offset + codepoint - start]
        return 0

    def text_advance(self, value):
        return sum(self.character_advance(character) for character in str(value))


class FontMetrics:
    __slots__ = ("fonts", "names")

    def __init__(self, fonts):
        self.fonts = dict((metric.name, metric) for metric in fonts)
        self.names = tuple(sorted(self.fonts))

    def normalize_font(self, font):
        match = _FONT_NAME.match(str(font))
        if match is None:
            logging.warning(
                "[feather_screen] unsupported font %r; using JetBrainsMono 12pt",
                font)
            return self._default_name()
        family, requested = match.group(1), int(match.group(2))
        # Feather's UI font must cover the bundled Cyrillic strings. Typer
        # still reports Roboto, but legacy Roboto requests intentionally map to
        # the equivalent JetBrains Mono face as they did before the manifest.
        if family.startswith("Roboto"):
            family = family.replace("Roboto", "JetBrainsMono", 1)
        candidates = []
        for name in self.names:
            candidate = _FONT_NAME.match(name)
            if candidate is not None and candidate.group(1) == family:
                candidates.append((int(candidate.group(2)), name))
        if not candidates:
            logging.warning(
                "[feather_screen] unsupported font %r; using JetBrainsMono 12pt",
                font)
            return self._default_name()
        return min(
            candidates, key=lambda item: (abs(item[0] - requested), item[0]))[1]

    def _default_name(self):
        if "JetBrainsMono 12pt" in self.fonts:
            return "JetBrainsMono 12pt"
        return self.names[0]

    def metric(self, font):
        return self.fonts[self.normalize_font(font)]

    def available_fonts(self):
        return tuple(name for name in self.names
                     if name.startswith("JetBrainsMono"))

    def text_width(self, value, font):
        return self.metric(font).text_advance(value)

    def wrap_text(self, value, font, max_width):
        """Implement TextDrawer's whitespace and long-word rules (word-v1)."""
        text = str(value)
        width = int(max_width)
        if width <= 0:
            return [text]
        metric = self.metric(font)

        def fits(line):
            return metric.text_advance(line) <= width

        lines = []
        current = ""

        def append_word(word):
            nonlocal current
            if current:
                candidate = current + " " + word
                if fits(candidate):
                    current = candidate
                    return
                lines.append(current)
                current = ""
            if fits(word):
                current = word
                return
            chunk = ""
            for character in word:
                candidate = chunk + character
                if chunk and not fits(candidate):
                    lines.append(chunk)
                    chunk = ""
                    candidate = character
                chunk = candidate
            current = chunk

        for paragraph in text.split("\n"):
            found_word = False
            start = 0
            while start < len(paragraph):
                while start < len(paragraph) and paragraph[start] in " \t\r":
                    start += 1
                if start >= len(paragraph):
                    break
                end = start
                while end < len(paragraph) and paragraph[end] not in " \t\r":
                    end += 1
                append_word(paragraph[start:end])
                found_word = True
                start = end
            if current:
                lines.append(current)
                current = ""
            if not found_word:
                lines.append("")
        return lines

    def text_height(self, value, font, max_width=None, wrap=False):
        metric = self.metric(font)
        if wrap:
            lines = self.wrap_text(value, font, max_width)
        else:
            lines = str(value).split("\n")
        count = max(1, len(lines))
        return metric.glyph_height + (count - 1) * metric.advance_y


def _integer(value, label, positive=False):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("%s must be an integer" % label)
    if positive and value <= 0:
        raise ValueError("%s must be positive" % label)
    return value


def parse_manifest(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError("font manifest must be an object")
    if value.get("schema") != SCHEMA:
        raise ValueError("unsupported font manifest schema")
    if value.get("wrap_algorithm") != WRAP_ALGORITHM:
        raise ValueError("unsupported font wrap algorithm")
    entries = value.get("fonts")
    if not isinstance(entries, list) or not entries:
        raise ValueError("font manifest must contain fonts")

    metrics = []
    previous_name = None
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("font entry must be an object")
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("font name must be non-empty")
        if previous_name is not None and name <= previous_name:
            raise ValueError("font entries must be uniquely sorted")
        previous_name = name
        monospaced = entry.get("monospaced")
        if not isinstance(monospaced, bool):
            raise ValueError("font monospaced must be boolean")
        ranges = entry.get("unicode_ranges")
        if not isinstance(ranges, list) or not ranges:
            raise ValueError("font unicode_ranges must be non-empty")
        normalized_ranges = []
        glyph_count = 0
        previous_end = -1
        for item in ranges:
            if not isinstance(item, list) or len(item) != 2:
                raise ValueError("font Unicode range must be a pair")
            start = _integer(item[0], "range start")
            end = _integer(item[1], "range end")
            if start < 0 or end < start or start <= previous_end or end > 0xffff:
                raise ValueError("font Unicode ranges must be sorted and disjoint")
            normalized_ranges.append((start, end))
            glyph_count += end - start + 1
            previous_end = end
        advance_x = entry.get("advance_x")
        if monospaced:
            advance_x = _integer(advance_x, "advance_x", positive=True)
        else:
            if not isinstance(advance_x, list) or len(advance_x) != glyph_count:
                raise ValueError("proportional advance_x must cover every glyph")
            advance_x = tuple(
                _integer(item, "advance_x", positive=True) for item in advance_x)
        advance_y = _integer(entry.get("advance_y"), "advance_y", positive=True)
        bounds = entry.get("glyph_bounds")
        if not isinstance(bounds, dict):
            raise ValueError("glyph_bounds must be an object")
        top = _integer(bounds.get("top"), "glyph top")
        bottom = _integer(bounds.get("bottom"), "glyph bottom")
        if bottom <= top:
            raise ValueError("glyph bounds must have positive height")
        metrics.append(FontMetric(
            name, advance_x, monospaced, advance_y, top, bottom,
            tuple(normalized_ranges)))
    return FontMetrics(metrics)


def load_fallback_metrics(path=FALLBACK_PATH):
    with open(path, "rb") as stream:
        return parse_manifest(stream.read())


def load_runtime_metrics(binary, timeout=0.35, fallback=None):
    fallback = fallback or load_fallback_metrics()
    try:
        completed = subprocess.run(
            [binary, "--font-manifest"], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=timeout, check=False)
        if completed.returncode != 0:
            raise ValueError("Typer returned %d" % completed.returncode)
        return parse_manifest(completed.stdout)
    except (OSError, TypeError, ValueError, UnicodeError,
            subprocess.TimeoutExpired) as error:
        logging.warning(
            "[feather_screen] unable to load Typer font manifest; using packaged fallback: %s",
            error)
        return fallback


_metrics = None


def get_font_metrics():
    global _metrics
    if _metrics is None:
        _metrics = load_fallback_metrics()
    return _metrics


def set_font_metrics(metrics):
    global _metrics
    _metrics = metrics
