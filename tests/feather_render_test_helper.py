## Semantic assertions for Feather renderer tests
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from contextlib import contextmanager
from dataclasses import dataclass
import pathlib
import shlex
import sys
from unittest import mock

from types import SimpleNamespace

PLUGINS = pathlib.Path(__file__).parents[1] / ".py" / "klipper" / "plugins"
sys.path.insert(0, str(PLUGINS))

from ui.engine import CommandBatch, FrameLoop  # noqa: E402
from ui.renderer import MAX_ATOMIC_DRAW, MAX_BATCH_BYTES, BinaryCommand, FeatherRenderer, Hitbox  # noqa: E402


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
    color: str | None = None
    # Typer's -ha: x is the text's left edge, centre or right edge.
    horizontal: str = "left"


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


def shown_hitboxes(renderer):
    """The input regions Typer holds of the screen, in its order (a later one
    wins): what the screen's frames wrote when a screen draws them, else what
    the renderer's painters registered."""
    owner = renderer._frame_owner
    shown = owner.shown_input() if owner is not None else None
    if shown is None:
        return renderer.hitboxes
    return [Hitbox(region[0], *region[1:5], bool(region[5]), layer)
            for layer in ("base", "overlay") for region in shown[layer]]


def tapped_action(hitboxes, x, y):
    """The action a tap at x, y reaches: Typer takes the last region holding it."""
    return next((region.action for region in reversed(hitboxes)
                 if region.x <= x < region.x + region.width and region.y <= y < region.y + region.height), None)


def _button_state(renderer, colors):
    """The button state whose colours a drawn button shows."""
    for state in renderer.BUTTON_COLORS:
        if tuple(renderer.color(color) for color in renderer._button_colors(state)) == colors:
            return state
    return None


class RenderFrame:
    """One submitted Feather frame expressed as UI concepts, not CLI text.

    hitboxes: the input Typer holds with the frame; by default the input shown
    now (shown_hitboxes), so a frame made after its paint ended reads its own.
    """

    def __init__(self, commands, renderer, hitboxes=None):
        self.texts = []
        self.shapes = []
        self.toggles = {}
        self._ids = set()
        self._drawn = []
        regions = {}

        for command in commands:
            tokens = shlex.split(command)
            if len(tokens) < 2 or tokens[0] != "--batch":
                continue
            kind = tokens[1]
            action = _option(tokens, "--id")
            if action is not None:
                self._ids.add(_logical_action(action))
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
                    "--wrap" in tokens, "--truncate" in tokens, _option(tokens, "-c"),
                    _option(tokens, "-ha") or "left"))
            elif kind in ("fill", "stroke"):
                bounds = _bounds(tokens)
                if bounds is not None:
                    self.shapes.append(RenderedShape(kind, bounds))
            elif kind == "hitbox":
                regions[_bounds(tokens)] = _logical_action(action)
            elif kind == "button" and action is not None:
                # A button the engine drew from a page's tree; its state shows in its colours.
                colors = (_option(tokens, "--background"), _option(tokens, "--border"),
                          _option(tokens, "--text-color"))
                self._drawn.append(RenderedButton(
                    _logical_action(action), _bounds(tokens), _option(tokens, "-t"),
                    str(_button_state(renderer, colors)), _option(tokens, "-f")))

        # The painters' buttons, as the renderer holds them while the frame is submitted.
        self._painted = {
            action: RenderedButton(action, Bounds(*spec[:4]), str(spec[4]), str(spec[5]), str(spec[6]))
            for action, spec in dict(renderer._buttons).items()}
        # A toggle: a bordered panel with a region, its square thumb next.
        for shape, thumb in zip(self.shapes, self.shapes[1:]):
            action = regions.get(shape.bounds)
            if (shape.kind == "stroke" and action is not None and thumb.kind == "fill"
                    and thumb.bounds.height == shape.bounds.height - 10):
                self.toggles[action] = RenderedToggle(
                    action, shape.bounds, thumb.bounds.x >= shape.bounds.x + shape.bounds.width // 2, True)
        self.shown(shown_hitboxes(renderer) if hitboxes is None else hitboxes)

    def shown(self, hitboxes):
        """The input Typer holds with this frame: the drawn buttons a tap
        reaches (no modal layer covers them) count as the frame's buttons."""
        self.hitboxes = hitboxes
        self.buttons = {
            button.action: button for button in self._drawn
            if tapped_action(hitboxes, button.bounds.x + button.bounds.width // 2,
                             button.bounds.y + button.bounds.height // 2) == button.action}
        self.buttons.update(self._painted)
        self.actions = set(self._ids)
        self.actions.update(self.buttons)
        self.actions.update(self.toggles)
        self.actions.update(region.action for region in hitboxes)

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
    """Accept every batch at the frame loop hand-off and record it.

    Everything before the hand-off runs for real: composition, frame-owner
    routing, surface acceptance, and visible-layer bookkeeping. Each batch is
    checked by an unstarted frame loop, which refuses what the running one
    refuses. ``submitted`` keeps each accepted ``RenderSubmission`` with its
    receipt; ``batches`` keeps just their command tuples;
    ``frames`` keeps the same submissions as semantic UI frames with the
    painters' controls registered at submission time and the input shown with
    each.
    """

    def __init__(self, renderer):
        self.submitted = []
        self.batches = []
        self._frames = []
        self._unshown = None
        self._taken = 0
        self._renderer = renderer
        self._loop = FrameLoop(
            "/nonexistent/typer", "/nonexistent/events", "feather-test-no-such-process",
            max_frame=MAX_ATOMIC_DRAW, max_batch=MAX_BATCH_BYTES)
        renderer._submit = self._submit

    def _submit(self, submission):
        self._loop.submit(
            submission.commands, receipt=submission.receipt,
            phase=submission.receipt_phase)
        self._loop.discard()
        # A screen shows a frame's input once the frame is accepted: the previous
        # frame's input is the one shown now. Without a screen the painters' input is the frame's.
        self._show_input()
        self.submitted.append(submission)
        self.batches.append(submission.commands)
        screen = self._renderer._frame_owner is not None
        frame = RenderFrame(submission.commands, self._renderer, hitboxes=() if screen else None)
        self._frames.append(frame)
        self._unshown = frame if screen else None
        return True

    def _show_input(self):
        if self._unshown is not None:
            self._unshown.shown(shown_hitboxes(self._renderer))
            self._unshown = None

    @property
    def frames(self):
        """The submissions as semantic frames, each with the input shown with it."""
        self._show_input()
        return self._frames

    def get(self):
        """The oldest submission not taken yet, or None."""
        if self._taken == len(self.submitted):
            return None
        self._taken += 1
        return self.submitted[self._taken - 1]

    @property
    def latest(self):
        if not self.frames:
            raise AssertionError("renderer has not submitted a frame")
        return self.frames[-1]


def accepted_by_loop(commands, max_batch=MAX_BATCH_BYTES):
    """Whether Typer's frame loop takes commands as one batch. An unstarted loop
    refuses what the running one refuses: ValueError for a command larger than
    a frame or a batch larger than max_batch."""
    loop = FrameLoop("/nonexistent/typer", "/nonexistent/events", "feather-test-no-such-process",
                     max_frame=MAX_ATOMIC_DRAW, max_batch=max_batch)
    try:
        return loop.submit(CommandBatch(commands, BinaryCommand))
    finally:
        loop.discard()


def page_screen(renderer, page="Home", deltas=False):
    """A full-screen root over the renderer showing page (a page's title, or a
    painter that sends what it shows), as the product's root shows its pages:
    the renderer's footer is a layer of its screen. deltas: the page takes the
    renderer's direct sends as its deltas. Not painted yet."""
    from feather.screen.composition import PaintSurface
    from ff5m_ui.keys import AppPage
    from ui import Rect, ScreenRoot
    from ui.renderer import SCREEN_HEIGHT, SCREEN_WIDTH
    painter = page if callable(page) else (lambda: renderer.send(renderer.begin_page(page)))
    surface = PaintSurface(painter, accepts_deltas=deltas)
    return ScreenRoot(renderer, surface, Rect(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT), AppPage.SCREEN_ROOT)


def state_screen(renderer, page, values=None, title=None):
    """A full-screen root over the renderer showing a state screen with its values, painted."""
    from ff5m_ui.keys import AppPage
    from ui import Rect, ScreenRoot
    from ui.renderer import SCREEN_HEIGHT, SCREEN_WIDTH
    root = ScreenRoot(renderer, page, Rect(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT), AppPage.SCREEN_ROOT)
    root.replace_content(page, title=title)
    page.state.update(values or {})
    root.paint()
    return root


def shown_actions(root):
    """The logical actions of the regions Typer holds of root's screen."""
    shown = root.shown_input()
    return set() if shown is None else {region[0] for region in tuple(shown["base"]) + tuple(shown["overlay"])}


def painted_dialog(kind, content, renderer, painter, priority=0):
    """A modal layer a painter draws, the screen dimmed unless the background is retained: the reference the
    composition checks hold to the screen's layers."""
    from feather.screen.composition import PaintSurface
    from ui import ScreenLayer

    class Modal(PaintSurface):
        def blocks_input(self, state):
            return True

    class Layer(ScreenLayer):
        pass

    def paint():
        paint_scrim(renderer)
        painter(layer)

    layer = Layer(Modal(paint), retain_background=True)
    layer.kind, layer.content, layer.priority = kind, content, priority
    layer.resolve_action = lambda wire_id: None
    return layer


def paint_scrim(renderer):
    if not renderer.retaining_background:
        renderer.send((renderer.modal_scrim(),))


def refresh_prompt(screen):
    """The periodic update of a shown status prompt."""
    from ff5m_ui.screen import ScreenDialog
    screen._refresh_status_prompt(screen._find_dialog(ScreenDialog.ACTION_PROMPT))


def touchable_actions(renderer):
    """The actions a tap on the visible surface reaches, the wake region aside."""
    return renderer.shown_actions() - {"global.wake"}


def prompt_wire(screen, index=0):
    """The wire id of a shown prompt's button, in the order Klipper declared them."""
    from ff5m_ui.screen import ScreenDialog
    return screen._find_dialog(ScreenDialog.ACTION_PROMPT).content["buttons"][index]["action"].wire_id


def press(screen, wire_id):
    """A tap reaching the shown dialog, past the gates of _dispatch_action."""
    screen._dispatch_semantic_ui_action(screen._current_dialog_instance().resolve_action(wire_id))


@contextmanager
def refused_render(renderer):
    """Refuse every batch at the frame loop hand-off, as a stopped loop does."""
    with mock.patch.object(renderer, "_submit", lambda submission: False):
        yield


def typer_events(decode, *lines):
    """Typer's event lines as FeatherRenderer.process_events() returns them:
    parsed by the engine's frame loop parser, with the action the loop would
    decode against the input it wrote given by decode(wire)."""
    taken = []
    for line in lines:
        event = FrameLoop.parse_event(line)
        if event[0] in ("tap", "touch", "button"):
            event = event[:-1] + (decode(event[1]),)
        taken.append(event)
    host = SimpleNamespace(_loop=SimpleNamespace(take=lambda: taken),
                           _sent_handler=None, _restart_handler=None)
    return FeatherRenderer.process_events(host, 0.0)


def paint_content(controller, content):
    """A controller without a screen root draws a page's content as the root would: its header, then its tree."""
    tree, title, back = content
    commands = controller.renderer.begin_page(title, back=back)
    commands += tree.draw(controller.renderer, tree.state)
    controller.renderer.send(commands)
