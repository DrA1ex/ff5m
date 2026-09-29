## Semantic assertions for Feather renderer tests
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from contextlib import contextmanager
from dataclasses import dataclass
import shlex


@dataclass(frozen=True)
class Bounds:
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self):
        return self.x + self.width

    @property
    def bottom(self):
        return self.y + self.height


@dataclass(frozen=True)
class RenderedText:
    value: str
    x: int
    y: int
    font: str
    max_width: int | None
    max_height: int | None
    wrap: bool
    truncate: bool


@dataclass(frozen=True)
class RenderedShape:
    kind: str
    bounds: Bounds


@dataclass(frozen=True)
class RenderedButton:
    action: str
    bounds: Bounds
    label: str
    state: str
    font: str


@dataclass(frozen=True)
class RenderedToggle:
    action: str
    bounds: Bounds
    active: bool
    enabled: bool


def _option(tokens, name, count=1):
    try:
        start = tokens.index(name) + 1
    except ValueError:
        return None
    values = tokens[start:start + count]
    if len(values) != count:
        raise ValueError("render option %s is incomplete" % name)
    return values[0] if count == 1 else tuple(values)


def _bounds(tokens):
    position = _option(tokens, "-p", 2)
    size = _option(tokens, "-s", 2)
    if position is None or size is None:
        return None
    return Bounds(*(int(value) for value in position + size))


def _logical_action(value):
    prefix, separator, action = str(value).partition(":")
    return action if separator and prefix.isdigit() else str(value)


class RenderFrame:
    """One submitted Feather frame expressed as UI concepts, not CLI text."""

    def __init__(self, commands, renderer):
        self.texts = []
        self.shapes = []
        self.buttons = {}
        self.toggles = {}
        self.actions = set()

        for command in commands:
            tokens = shlex.split(command)
            if len(tokens) < 2 or tokens[0] != "--batch":
                continue
            kind = tokens[1]
            action = _option(tokens, "--id")
            if action is not None:
                self.actions.add(_logical_action(action))
            if kind == "text":
                position = _option(tokens, "-p", 2)
                value = _option(tokens, "-t")
                font = _option(tokens, "-f")
                if position is None or value is None or font is None:
                    raise ValueError("incomplete rendered text command")
                max_width = _option(tokens, "--max-width")
                max_height = _option(tokens, "--max-height")
                self.texts.append(RenderedText(
                    value, int(position[0]), int(position[1]), font,
                    int(max_width) if max_width is not None else None,
                    int(max_height) if max_height is not None else None,
                    "--wrap" in tokens, "--truncate" in tokens))
            elif kind in ("fill", "stroke"):
                bounds = _bounds(tokens)
                if bounds is not None:
                    self.shapes.append(RenderedShape(kind, bounds))

        for action, spec in dict(renderer._buttons).items():
            self.buttons[action] = RenderedButton(
                action, Bounds(*spec[:4]), str(spec[4]), str(spec[5]),
                str(spec[6]))
        for action, spec in dict(renderer._toggles).items():
            self.toggles[action] = RenderedToggle(
                action, Bounds(*spec[:4]), bool(spec[4]), bool(spec[5]))
        self.actions.update(self.buttons)
        self.actions.update(self.toggles)
        self.actions.update(dict(renderer._hitboxes))

    def has_action(self, action):
        return action in self.actions

    def button(self, action):
        try:
            return self.buttons[action]
        except KeyError as error:
            raise AssertionError("button is not rendered: %s" % action) from error

    def toggle(self, action):
        try:
            return self.toggles[action]
        except KeyError as error:
            raise AssertionError("toggle is not rendered: %s" % action) from error

    def text(self, value):
        matches = [text for text in self.texts if text.value == value]
        if len(matches) != 1:
            raise AssertionError(
                "expected one rendered text %r, found %d" %
                (value, len(matches)))
        return matches[0]

    def has_text(self, value):
        return any(text.value == value for text in self.texts)


class RenderCapture:
    """Accept every batch at the Typer worker hand-off and record it.

    Everything before the queue runs for real: composition, frame-owner
    routing, surface acceptance, and visible-layer bookkeeping. ``submitted``
    keeps each accepted ``RenderBatch`` with its kind, key, and receipt;
    ``batches`` keeps just their command tuples; ``frames`` keeps the same
    submissions as semantic UI frames with the controls registered at
    submission time.
    """

    def __init__(self, renderer):
        self.submitted = []
        self.batches = []
        self.frames = []
        self._renderer = renderer
        renderer._batch_queue.put_nowait = self._submit

    def _submit(self, batch):
        self.submitted.append(batch)
        self.batches.append(batch.commands)
        self.frames.append(RenderFrame(batch.commands, self._renderer))
        return True

    @property
    def latest(self):
        if not self.frames:
            raise AssertionError("renderer has not submitted a frame")
        return self.frames[-1]


@contextmanager
def full_render_queue(renderer):
    """Simulate a saturated Typer queue that refuses every submitted batch."""
    queue = renderer._batch_queue
    submit = queue.put_nowait
    queue.put_nowait = lambda batch: False
    try:
        yield
    finally:
        queue.put_nowait = submit
