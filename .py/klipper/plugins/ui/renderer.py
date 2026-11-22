## Low-level Typer renderer for Feather UI object trees.
##
## This module deliberately contains no Klipper state or business logic. The
## declarative layout engine and components live next to it in the ui package.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import logging
import math
from collections import namedtuple
from contextlib import contextmanager

from .actions import Action, DismissToast, action_wire_id
from .engine import (
    NATIVE_METHODS, BinaryCommand, CommandBatch, CommandList, FrameLoop, RendererBase, dialog_layout, quote, renderer_normalize_font,
    renderer_normalize_font_for_text, renderer_text_width, status_dialog_layout,
)
from .font_metrics import (
    get_font_metrics, load_runtime_metrics, set_font_metrics,
)
from .footer import Footer, FooterState, footer_content
from .layout_helpers import (
    CONTENT_BOTTOM, FOOTER_HEIGHT, FOOTER_Y, FRAME_HEIGHT, FRAME_WIDTH, FRAME_X, FRAME_Y, HEADER_BOTTOM,
    SCREEN_HEIGHT, SCREEN_WIDTH,
    DIALOG_BODY_FONT, DIALOG_BODY_HALF_HEIGHT, DIALOG_BODY_Y, DIALOG_BUTTON_BOTTOM,
    DIALOG_BUTTON_COMPACT_FONT, DIALOG_BUTTON_FONT, DIALOG_BUTTON_MEDIUM_FONT,
    DIALOG_MIN_HEIGHT, DIALOG_MIN_WIDTH,
    DIALOG_BUTTON_HEIGHT, DIALOG_BUTTON_GAP, DIALOG_COMPACT_BUTTON_BOTTOM,
    DIALOG_COMPACT_GAP, DIALOG_COMPACT_TITLE_TOP, DIALOG_GROUP_BUTTON_BOTTOM,
    DIALOG_LINE_SPACING, DIALOG_MULTILINE_GAP, DIALOG_ONE_LINE_GAP,
    DIALOG_PAGER_ABOVE_BODY, DIALOG_PAGER_MIN_HEIGHT, DIALOG_PAGER_RESERVE, DIALOG_PAGER_RIGHT, DIALOG_TITLE_FONT,
    DIALOG_TITLE_LINE_SPACING, DIALOG_TITLE_TOP, DIALOG_TOP, DIALOG_BOTTOM_INSET,
    DIALOG_STATUS_FONT, DIALOG_STATUS_GAP, dialog_body_offset,
    dialog_pager_bounds,
)
from .numeric_input import NumericInputSpec
from .render_receipts import RenderReceipt, validate_render_receipt_token
from .theme import ThemeColor, ThemeRole, resolve_theme
from .theme_catalog import (
    DEFAULT_THEME, FALLBACK_THEME, THEME_DIRECTORY, USER_THEME_DIRECTORY,
    ThemeCatalog, normalize_theme_name,
)


Hitbox = namedtuple("Hitbox", "action x y width height continuous layer")
# One batch for the frame loop; a restarted Typer gets none of those it did not acknowledge.
RenderSubmission = namedtuple("RenderSubmission", "commands receipt receipt_phase")
# Typer's input events as process_events() returns them. action is the logical action when the wire action's
# generation is the visible surface's, else None; wire is the action as Typer reported it.
TapEvent = namedtuple("TapEvent", "action wire")
# phase: "begin", "move" or "end"; x and y as Typer reported them.
TouchEvent = namedtuple("TouchEvent", "action wire phase x y")
ButtonEvent = namedtuple("ButtonEvent", "action wire pressed")
TouchDeviceEvent = namedtuple("TouchDeviceEvent", "available")


def screen_failed(message):
    """A chrome frame that could not be built natively (a class overriding a
    hook) is a bug: it is logged and nothing is drawn."""
    logging.warning("[feather_screen] a screen frame was not drawn: %s", message)


# The fonts and constants of the engine's dialog layout, in dialog_layout's order; the renderer's paddings and
# the screen size follow them.
_DIALOG_POLICY = (
    DIALOG_TITLE_FONT, DIALOG_BODY_FONT, DIALOG_BUTTON_FONT, DIALOG_BUTTON_MEDIUM_FONT, DIALOG_BUTTON_COMPACT_FONT,
    DIALOG_TOP, DIALOG_BOTTOM_INSET, DIALOG_MIN_WIDTH, DIALOG_MIN_HEIGHT, DIALOG_TITLE_TOP, DIALOG_BODY_Y,
    DIALOG_LINE_SPACING, DIALOG_BODY_HALF_HEIGHT, DIALOG_PAGER_RESERVE, DIALOG_TITLE_LINE_SPACING,
    DIALOG_BUTTON_HEIGHT, DIALOG_BUTTON_GAP, DIALOG_BUTTON_BOTTOM, DIALOG_ONE_LINE_GAP, DIALOG_MULTILINE_GAP,
    DIALOG_GROUP_BUTTON_BOTTOM, DIALOG_COMPACT_TITLE_TOP, DIALOG_COMPACT_GAP, DIALOG_COMPACT_BUTTON_BOTTOM,
    DIALOG_PAGER_MIN_HEIGHT,
)


# 70%-opaque black: what a layer's backdrop dims the screen beneath it with (ScreenLayer.backdrop).
BACKDROP_COLOR = "B3000000"


def _dialog_policy(renderer_class):
    """The dialog policy of a renderer class: _DIALOG_POLICY, its paddings and the screen size, then the rows a modal
    dialog's surround spans, where a paginated dialog's page arrows stand, the backdrop colour and a status dialog's
    font and gap. The engine lays dialogs out by it and draws them (and layers' backdrops) in a screen's build."""
    return _DIALOG_POLICY + (
        renderer_class.DIALOG_TEXT_PADDING, renderer_class.BUTTON_TEXT_PADDING, SCREEN_WIDTH, SCREEN_HEIGHT,
        HEADER_BOTTOM + 1, CONTENT_BOTTOM, DIALOG_PAGER_RIGHT, DIALOG_PAGER_ABOVE_BODY, BACKDROP_COLOR,
        DIALOG_STATUS_FONT, DIALOG_STATUS_GAP)


DRAW_PIPE = "/tmp/typer"
EVENT_PIPE = "/tmp/feather-events"
TOUCH_DEVICE = "/dev/input/fgx_touch"
TYPER_BINARY = "/root/printer_data/bin/typer"


class PageChromeContract:
    """Framework-owned geometry of the page chrome painted around content.

    ``begin_page`` paints a header above ``HEADER_BOTTOM`` and a persistent
    footer below ``CONTENT_BOTTOM``. Authoring tools place new page content
    inside ``content_bounds`` so that it does not overlap that chrome.
    """

    __slots__ = ("strategy", "content_insets")

    def __init__(self, strategy, content_insets):
        self.strategy = str(strategy)
        left, top, right, bottom = (int(value) for value in content_insets)
        if min(left, top, right, bottom) < 0:
            raise ValueError("Page content insets must be non-negative")
        self.content_insets = (left, top, right, bottom)

    def content_bounds(self, width=SCREEN_WIDTH, height=SCREEN_HEIGHT):
        left, top, right, bottom = self.content_insets
        width, height = int(width), int(height)
        if width - left - right <= 0 or height - top - bottom <= 0:
            raise ValueError("Viewport is smaller than the page chrome")
        return (left, top, width - left - right, height - top - bottom)

    def as_dict(self, width=SCREEN_WIDTH, height=SCREEN_HEIGHT):
        return {
            "strategy": self.strategy,
            "content_insets": list(self.content_insets),
            "content_bounds": list(self.content_bounds(width, height)),
        }


PAGE_CHROME_CONTRACT = PageChromeContract(
    "core.page_chrome",
    (12, HEADER_BOTTOM + 9, 12, SCREEN_HEIGHT - CONTENT_BOTTOM + 4))
BUSY_NOTICE_X = 622
BUSY_NOTICE_Y = 9
BUSY_NOTICE_WIDTH = 160
BUSY_NOTICE_HEIGHT = 38
# The toast's hint box: centered on the screen, its bottom 8 px above the content's.
TOAST_CENTER_X = SCREEN_WIDTH // 2
TOAST_HEIGHT = 44
TOAST_MIN_WIDTH = 180
TOAST_MAX_WIDTH = 740
TOAST_FONT = "JetBrainsMono 8pt"
# What one send() may carry: one transaction must hold the largest frame the
# screen shows, a full 800x480 picture at 4 bits per pixel (192,000 bytes, as
# bands of at most MAX_ATOMIC_DRAW) with a page around it. It only checks a
# batch: nothing is allocated for it, and the frame loop writes a transaction
# one frame at a time.
MAX_BATCH_BYTES = 256 * 1024
MAX_PENDING_DRAW = MAX_BATCH_BYTES
# Historical public name retained for compatibility.  This is a bounded
# logical transport-frame size, not a Linux FIFO atomic-write guarantee:
# 8 KiB may exceed PIPE_BUF.  Correctness depends on the frame loop being the
# sole draw-FIFO writer and completing partial writes.
MAX_ATOMIC_DRAW = 8 * 1024
PRESENT_GUARD_US = 3000

_ButtonSpec = namedtuple(
    "ButtonSpec",
    ("x", "y", "width", "height", "label", "state", "font",
     "subtitle", "layout", "subtitle_font", "subtitle_color", "accent",
     "surfaces"),
)


# BinaryCommand, a Typer command whose payload follows its transport header,
# is the engine's type: the engine also makes the binary commands a recorded
# output keeps.


def _immutable_commands(commands):
    """Freeze a batch: binary commands as they are, everything else as text."""
    if isinstance(commands, CommandBatch):
        return commands
    return CommandBatch(commands, BinaryCommand)


_NATIVE = NATIVE_METHODS["renderer"]


class FeatherRenderer(RendererBase):
    """Translate small UI primitives into typer display-list commands."""

    # The engine draws the primitives. They read the renderer's palette,
    # surface, button and hitbox state, and call each other through the
    # instance, so a subclass or instance attribute may replace any of them.
    color = _NATIVE["color"]
    fill = _NATIVE["fill"]
    stroke = _NATIVE["stroke"]
    panel = _NATIVE["panel"]
    text = _NATIVE["text"]
    composite_button = _NATIVE["composite_button"]
    button = _NATIVE["button"]
    buttons = _NATIVE["buttons"]
    # Button layouts other than a centred label, and the geometric up/down
    # arrow button.
    _button_colors = _NATIVE["_button_colors"]
    _button_commands = _NATIVE["_button_commands"]
    arrow_button = _NATIVE["arrow_button"]
    _arrow_button_commands = _NATIVE["_arrow_button_commands"]
    _wire_action = _NATIVE["_wire_action"]
    # Input regions: action_hitbox registers one and returns its command,
    # button_hitbox skips inactive buttons, and redraw_page_hitboxes replays
    # the page chrome's regions without painting.
    _hitbox = _NATIVE["_hitbox"]
    clear_hitboxes = _NATIVE["clear_hitboxes"]
    action_hitbox = _NATIVE["action_hitbox"]
    button_hitbox = _NATIVE["button_hitbox"]
    _wake_hitbox = _NATIVE["_wake_hitbox"]
    redraw_page_hitboxes = _NATIVE["redraw_page_hitboxes"]
    # Framework values the native primitives use.
    _action_type = Action
    _button_spec = _ButtonSpec
    _literal_types = (ThemeColor, ThemeRole)
    _font_metrics = staticmethod(get_font_metrics)
    _default_background = ThemeColor.BACKGROUND
    _default_foreground = ThemeColor.PRIMARY
    _default_panel = ThemeColor.PANEL
    _default_subtitle = ThemeColor.DIM
    # Page chrome bounds. The wake region covers the screen and is registered
    # before every control, which Typer gives precedence where they overlap.
    _wake_bounds = (0, 0, SCREEN_WIDTH, SCREEN_HEIGHT)
    _back_bounds = (14, 7, 146, 46)
    _header_action_bounds = (648, 7, 132, 46)

    BUTTON_COLORS = {
        "primary": (ThemeRole.ACCENT_BACKGROUND, ThemeRole.ACCENT_BORDER,
                    ThemeRole.ACCENT_TEXT),
        "enabled": (ThemeRole.BUTTON_BACKGROUND, ThemeRole.BUTTON_BORDER,
                    ThemeRole.BUTTON_TEXT),
        "disabled": (ThemeRole.BUTTON_BACKGROUND, ThemeColor.MUTED,
                     ThemeColor.DIM),
        "selected": (
            ThemeRole.BUTTON_SELECTED_BACKGROUND,
            ThemeRole.BUTTON_SELECTED_BORDER,
            ThemeRole.BUTTON_SELECTED_TEXT),
        "warning": (ThemeRole.BUTTON_BACKGROUND, ThemeColor.WARNING,
                    ThemeColor.WARNING),
        "danger": (ThemeColor.DANGER_BACKGROUND, ThemeColor.DANGER, ThemeColor.DANGER),
        "busy": (ThemeRole.BUTTON_BACKGROUND, ThemeColor.WARNING,
                 ThemeColor.WARNING),
        "pressed": (ThemeColor.PRESSED_BACKGROUND, ThemeColor.BRIGHT, ThemeColor.BRIGHT),
        "toggle": (ThemeColor.PANEL, ThemeColor.PRIMARY, ThemeColor.PRIMARY),
        "toggle-disabled": (ThemeColor.PANEL, ThemeColor.MUTED, ThemeColor.DIM),
        "keypad": (ThemeColor.PANEL, ThemeColor.DIM, ThemeColor.TEXT),
        "keypad_aux": (ThemeColor.PANEL, ThemeColor.SECONDARY, ThemeColor.TEXT),
        "keypad_confirm": (ThemeColor.PANEL, ThemeColor.PRIMARY, ThemeColor.BRIGHT),
    }
    BUTTON_TEXT_PADDING = 16
    HINT_TEXT_PADDING = 20
    DIALOG_TEXT_PADDING = 28

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls._dialog_policy = _dialog_policy(cls)

    def __init__(self, debug=False, theme_directories=None, blending=True,
                 raster_acceleration="scalar", ram_composition=False):
        # The persistent status strip; the frame owner's screen holds it beside the page.
        self._footer = Footer(self._footer_content, self._footer_state)
        # The screen composition root (configure_frame_owner); a theme change below asks it to redraw.
        self._frame_owner = None
        self.debug = debug
        self.ram_composition = bool(ram_composition)
        self.blending = bool(blending)
        self.raster_acceleration = str(raster_acceleration).strip().lower()
        if self.raster_acceleration not in ("scalar", "neon"):
            raise ValueError("invalid raster acceleration: %s" %
                             raster_acceleration)
        self._theme_directories = tuple(
            theme_directories or (THEME_DIRECTORY, USER_THEME_DIRECTORY))
        self._theme_catalog = ThemeCatalog.from_directories(
            self._theme_directories)
        self._themes = {}
        self._theme_descriptions = {}
        self._user_theme_issues = ()
        self._theme_name = DEFAULT_THEME
        self._palette = resolve_theme(FALLBACK_THEME)
        self.reload_themes()
        self.event_fd = None
        self._reset_interactions()
        self._generation = 0
        # The native frame loop that owns Typer; made by the first start().
        self._loop = None
        # Its pacing until it exists; see the synced property.
        self._synced = True
        self._sent_handler = None
        self._event_fd_handler = None
        self._restart_handler = None
        # Screen frames the loop could not draw, and the last one's reason: a bug, reported in the status.
        self._screen_errors = 0
        self._screen_error = ""
        self._screen_error_handler = None
        self._next_batch_kind = None
        self._next_batch_key = None
        self._composite_commands = None
        self._composite_kind = None
        self._composite_key = None
        self._composite_receipt = None
        self._busy_label = None
        self._header_action = None
        self._menu_suppressed = False
        self._output_frozen = False
        self._font_manifest_loaded = False
        self._semantic_page_id = None
        self._page_title = ""
        self._page_back = False
        self._prepared_surface = None
        # Set while the frame owner's painters run (painters()).
        self._retain_background = False
        self._dialog_bounds = ()

    def configure_frame_owner(self, owner):
        """Route every ordinary submission through the screen composition root;
        while the frame loop runs, it draws the owner's screen."""
        self._frame_owner = owner
        if self.active:
            self._loop.attach(owner.frames)

    # The engine keeps the input (generation, buttons, regions, pressed
    # buttons and the menu button's suppression) with its own snapshot; these
    # are the other surface fields.
    _SURFACE_FIELDS = (
        "_toggles", "_page_title", "_page_back", "_semantic_page_id",
        "_header_action",
    )
    # Surface fields mutated in place; the snapshot copies them. Every other
    # field is replaced on change, so the snapshot shares its value.
    _SURFACE_CONTAINERS = ("_toggles",)

    @property
    def render_allowed(self):
        return not self._output_frozen

    # Whether the renderer may draw; the first call of a surface keeps the
    # visible surface fields and snapshots the input.
    _prepare_surface = _NATIVE["_prepare_surface"]

    def _finish_surface(self, accepted):
        previous = self._prepared_surface
        self._prepared_surface = None
        if previous is None:
            return
        if accepted:
            self._drop_input_snapshot()
            return
        for name, value in previous.items():
            setattr(self, name, value)
        self._restore_input()

    def discard_surface(self):
        """Roll back a prepared surface that was not submitted."""
        self._finish_surface(False)

    def keep_surface(self):
        """Keep a prepared surface: the frame owner's frame that drew it was accepted."""
        self._finish_surface(True)

    def configure_worker(self, event_fd_handler, restart_handler=None):
        """Connect the reactor before start().

        event_fd_handler(old_fd, new_fd) registers the descriptor that is
        readable while process_events() has work (new_fd None: unregister
        old_fd); restart_handler() repaints after Typer started again.
        """
        self._event_fd_handler = event_fd_handler
        self._restart_handler = restart_handler

    @property
    def active(self):
        return self._loop is not None and self._loop.running

    @property
    def synced(self):
        """Whether frames follow the display's presentation (the default).

        False (unthrottled) paces frames only by Typer's commits, so frames
        the display never shows are still built: what a render benchmark
        measures.
        """
        return self._synced

    @synced.setter
    def synced(self, value):
        self._synced = bool(value)
        if self._loop is not None:
            self._loop.synced = self._synced

    @property
    def output_frozen(self):
        return self._output_frozen

    def shown_actions(self):
        """The logical actions of the visible surface's input: what Typer holds
        of the screen while its frames draw the panel (the loop's builds
        included), else what this renderer's painters registered."""
        shown = self._frame_owner.shown_input() if self._frame_owner is not None else None
        if shown is None:
            actions = set(self._visible_input("_buttons"))
            actions.update(action for name in ("_hitboxes", "_overlay_hitboxes")
                           for action, _spec in self._visible_input(name))
        else:
            actions = set(shown["buttons"])
            actions.update(region[0] for layer in ("base", "overlay") for region in shown[layer])
        return actions

    @property
    def touch_warning_allowed(self):
        """Whether the visible surface has controls worth protecting."""
        actions = self.shown_actions()
        actions.discard("global.wake")
        return bool(actions)

    def discard_pending_output(self):
        """Drop every unwritten batch before a final lifecycle screen."""
        if self._loop is not None:
            self._loop.discard()

    def freeze_output(self):
        """Keep the last submitted safety screen as the sole display owner."""
        self._output_frozen = True
        self.cover_footer()

    def thaw_output(self):
        # The frozen safety screen is the composition's: the next paint draws what changed.
        self._output_frozen = False

    def redraw_display(self):
        """Another owner drew on the panel (a frozen safety screen, Forge-X's redraw
        request): the frame owner draws all of it at its next paint."""
        if self._frame_owner is not None:
            self._frame_owner.surface_cleared()

    # str(value) as one double-quoted Typer token: line breaks become "\n", a
    # line holding only the frame sentinel "--end" gets a leading space, and
    # backslashes and quotes are escaped.
    quote = staticmethod(quote)

    def start(self):
        """Start the frame loop, which launches Typer on its own thread.

        Until then batches are admitted without being written, so a screen
        that another owner holds (a boot screen) is a renderer not started
        yet: start() is its release.
        """
        self._output_frozen = False
        if self._event_fd_handler is None:
            raise RuntimeError("frame loop callbacks are not configured")
        if self.active:
            return False
        if not self._font_manifest_loaded:
            set_font_metrics(load_runtime_metrics(TYPER_BINARY))
            self._font_manifest_loaded = True
        if self._loop is None:
            self._loop = FrameLoop(
                DRAW_PIPE, EVENT_PIPE, "typer", max_frame=MAX_ATOMIC_DRAW,
                max_batch=MAX_BATCH_BYTES, synced=self._synced)
        started = self._loop.start(self._typer_arguments())
        self.event_fd = self._loop.fileno()
        self._event_fd_handler(None, self.event_fd)
        self._busy_label = None
        if self._frame_owner is not None:
            # Typer's double buffer initially holds whatever the framebuffer did: the owner's next paint draws all
            # of its screen, whose first frame fills the screen's bounds first.
            self._loop.attach(self._frame_owner.frames)
            self._frame_owner.surface_cleared()
        return started

    def _typer_arguments(self):
        arguments = [TYPER_BINARY]
        if self.debug:
            arguments.append("--debug")
        arguments += ["--deferred-page-publish", "auto",
                      "--present-guard-us", str(PRESENT_GUARD_US)]
        if self.blending:
            arguments.append("--blending")
        if self.raster_acceleration != "scalar":
            arguments += ["--raster-acceleration", self.raster_acceleration]
        if self.ram_composition:
            # Opt-in: Typer composes in an extra 1.5 MB RAM image, trading
            # scarce printer memory for fewer uncached framebuffer reads.
            arguments.append("--ram-composition")
        arguments += ["--double-buffered", "--touch-device", TOUCH_DEVICE,
                      "--event-pipe", EVENT_PIPE, "batch", "--pipe", DRAW_PIPE]
        return arguments

    def stop(self):
        """Write what is pending, stop the frame loop and leave Typer showing the last screen."""
        if self.active:
            self._loop.stop()
            # A stopped loop draws nothing: the owner's frames are built where it paints again.
            self._loop.attach(None)
            self._event_fd_handler(self.event_fd, None)
            self.event_fd = None
        self._busy_label = None
        self._output_frozen = False

    def restart(self):
        return self._loop is not None and self._loop.restart()

    def set_sent_handler(self, callback):
        """Notify on the reactor after a receipt-bearing batch is fully written.

        This is transport completion, not render completion or presentation.
        Consumers must ignore tokens from cancelled or superseded operations.
        """
        self._sent_handler = callback

    def set_screen_error_handler(self, callback):
        """Report failed native builds as (message, generation, superseded) on the reactor."""
        self._screen_error_handler = callback

    def process_events(self, eventtime):
        """On the reactor, once event_fd is readable: deliver the frame loop's
        sent and restart notices and return Typer's events in order: TapEvent,
        TouchEvent, ButtonEvent, TouchDeviceEvent and RenderReceipt. The loop
        decoded each action against the input it wrote last, so an action is
        None for a region Typer no longer holds."""
        events = []
        for event in self._loop.take():
            kind = event[0]
            if kind == "tap":
                events.append(TapEvent(event[2], event[1]))
            elif kind == "touch":
                events.append(TouchEvent(event[5], *event[1:5]))
            elif kind == "button":
                events.append(ButtonEvent(event[3], event[1], event[2]))
            elif kind == "touch-device":
                events.append(TouchDeviceEvent(event[1]))
            elif kind == "receipt":
                events.append(RenderReceipt(*event[1:]))
            elif kind == "invalid":
                logging.warning("[feather_screen] invalid Typer event discarded: %r", event[1][:256])
            elif kind == "sent":
                if self._sent_handler is not None:
                    self._sent_handler(event[1], eventtime)
            elif kind == "screen-error":
                self._screen_errors += 1
                self._screen_error = event[1]
                if self._screen_error_handler is None:
                    screen_failed(event[1])
                else:
                    self._screen_error_handler(*event[1:])
            else:
                logging.warning("[feather_screen] typer restarted (last error: %s)",
                                self._loop.status()["last_error"] or "none")
                if self._frame_owner is not None:
                    self._frame_owner.restarted()
                if self._restart_handler is not None:
                    self._restart_handler()
        return events

    @contextmanager
    def compose(self):
        """Submit consecutive ordinary draws as one admitted frame (the legacy
        page route; a frame owner's screen submits its own frames)."""
        if self._composite_commands is not None:
            raise RuntimeError("render composition is already active")
        self._composite_commands = CommandList()
        self._composite_kind = None
        self._composite_key = None
        self._composite_receipt = None
        self._prepare_surface()
        self._dialog_bounds = ()
        try:
            yield
        except BaseException:
            self._composite_commands = None
            self._composite_kind = None
            self._composite_key = None
            self._composite_receipt = None
            self._finish_surface(False)
            raise
        commands = self._composite_commands
        kind = self._composite_kind
        key = self._composite_key
        receipt = self._composite_receipt
        self._composite_commands = None
        self._composite_kind = None
        self._composite_key = None
        self._composite_receipt = None
        if commands:
            if receipt is None:
                self.send(commands, kind=kind, key=key)
            else:
                self.send(commands, kind=kind, key=key, receipt=receipt[0], receipt_phase=receipt[1])
        else:
            self._finish_surface(False)

    @contextmanager
    def painters(self, retain_background=False):
        """One pass of the frame owner's painters over its screen's frame:
        retaining_background tells them whether the frame keeps the pixels
        under them, and dialog_bounds collects the dialogs they draw."""
        self._retain_background = bool(retain_background)
        self._dialog_bounds = ()
        try:
            yield
        finally:
            self._retain_background = False

    @property
    def dialog_bounds(self):
        """The bounds of the dialogs drawn since the painters' pass or the composition began."""
        return self._dialog_bounds

    @property
    def retaining_background(self):
        """The current foreground pass must leave retained backdrop pixels intact."""
        return self._retain_background

    @contextmanager
    def collect(self):
        """Adapt an imperative painter into one node's ordered command list:
        what it sends meanwhile is collected, not submitted."""
        previous = self._composite_commands
        commands = self._composite_commands = []
        try:
            yield commands
        finally:
            self._composite_commands = previous

    def send(self, commands, kind=None, key=None, generation=None,
             receipt=None, button_feedback=False, receipt_phase="presented"):
        """Publish one immutable batch; registered buttons can respond while frozen."""
        if self._output_frozen and not button_feedback:
            self._finish_surface(False)
            return False
        if not commands:
            if self._composite_commands is None and not button_feedback:
                self._finish_surface(False)
            return False
        if receipt_phase != "presented" and (receipt_phase != "staged" or receipt is None):
            raise ValueError("staged receipts require a token; unknown receipt phase")
        immutable = _immutable_commands(commands)
        if kind is None and self._next_batch_kind is not None:
            kind, key = self._next_batch_kind, self._next_batch_key
        self._next_batch_kind = None
        self._next_batch_key = None
        if self._composite_commands is not None:
            if generation is not None or button_feedback:
                raise ValueError("this render batch cannot be composed")
            if receipt is not None:
                token = validate_render_receipt_token(receipt)
                request = (token, receipt_phase)
                if self._composite_receipt not in (None, request):
                    raise ValueError("a composed frame can have only one receipt and phase")
                self._composite_receipt = request
            self._composite_commands.extend(immutable)
            if kind == "surface" or (kind == "state" and self._composite_kind is None):
                self._composite_kind = kind
                self._composite_key = key
            return True
        owner = self._frame_owner
        if owner is not None and not owner.painting and not button_feedback:
            if receipt_phase != "presented":
                return owner.submit(
                    immutable, kind=kind, key=key, generation=generation,
                    receipt=receipt, receipt_phase=receipt_phase)
            return owner.submit(
                immutable, kind=kind, key=key, generation=generation,
                receipt=receipt)
        if kind not in (None, "surface", "state", "animation"):
            self._finish_surface(False)
            raise ValueError("unknown render batch kind: %s" % kind)
        try:
            receipt = (None if receipt is None else
                       validate_render_receipt_token(receipt))
            accepted = self._submit(RenderSubmission(immutable, receipt, receipt_phase))
        except ValueError as exc:
            logging.warning("[feather_screen] rejected render batch: %s", exc)
            accepted = False
        except BaseException:
            if not button_feedback:
                self._finish_surface(False)
            raise
        if not button_feedback:
            self._finish_surface(accepted)
        return accepted

    def _submit(self, submission):
        """Hand a batch to the frame loop. Before the first start() Typer does
        not run and start() clears the whole panel, so the batch is admitted
        without being written; after stop() it is refused."""
        if self._loop is None:
            return True
        if not self._loop.running:
            return False
        return self._loop.submit(submission.commands, receipt=submission.receipt, phase=submission.receipt_phase)

    def prioritize_next_batch(self, kind, key=None):
        """Classify the following legacy one-argument send() call."""
        self._next_batch_kind = kind
        self._next_batch_key = key

    def send_animation(self, commands, key, receipt=None):
        return self.send(
            commands, kind="animation", key=key, receipt=receipt)

    def request_presented_receipt(self, token):
        """Queue a Typer presentation barrier without repainting the frame."""
        if not self.render_allowed:
            return False
        token = validate_render_receipt_token(token)
        # A flush alone: a protocol-only barrier passes the modal frame owner.
        return self._submit(RenderSubmission(_immutable_commands(()), token, "presented"))

    def get_status(self):
        loop = (self._loop.status() if self._loop is not None else
                {"state": "stopped", "last_error": "", "restarts": 0,
                 "submissions": 0, "transactions": 0, "pending": 0,
                 "in_flight": 0, "dropped_events": 0})
        return {
            "worker_state": loop["state"],
            "worker_last_error": loop["last_error"],
            "typer_restarts": loop["restarts"],
            "submitted_batches": loop["submissions"],
            "transactions": loop["transactions"],
            "pending_batches": loop["pending"],
            "in_flight": loop["in_flight"],
            "dropped_events": loop["dropped_events"],
            "screen_errors": self._screen_errors,
            "screen_error": self._screen_error,
            "semantic_page_id": self._semantic_page_id,
        }

    set_semantic_page = _NATIVE["set_semantic_page"]

    # decode_action(action) is the engine's: the logical action of a tap when
    # its generation is the visible surface's, so taps emitted for a page that
    # has already been replaced are refused (None).

    @property
    def generation(self):
        return self._visible_input("_generation")

    def invalidate_input_generation(self):
        """Prepare a new input generation for a replacement surface."""
        if self._prepare_surface():
            self._generation += 1

    @property
    def theme_name(self):
        return self._theme_name

    def _sync_theme_catalog(self):
        previous_name = self._theme_name
        previous_palette = self._palette
        self._themes = self._theme_catalog.themes
        self._theme_descriptions = self._theme_catalog.descriptions
        self._user_theme_issues = self._theme_catalog.user_issues
        if self._theme_name not in self._themes:
            self._theme_name = DEFAULT_THEME
        self._palette = self._themes[self._theme_name]
        if self._theme_name != previous_name or self._palette != previous_palette:
            self._theme_changed()
        return tuple(self._themes)

    def reload_themes(self):
        """Reload the complete catalog at construction and klippy:ready."""
        self._theme_catalog.reload_all()
        return self._sync_theme_catalog()

    def reload_user_themes(self):
        """Refresh writable user themes without rescanning bundled files."""
        self._theme_catalog.reload_user_themes()
        return self._sync_theme_catalog()

    def ensure_user_theme_directory(self):
        self._theme_catalog.ensure_user_directories()

    def theme_names(self, reload=False):
        # ``reload=True`` is retained for callers outside this package. A UI
        # refresh must never rescan immutable bundled themes.
        if reload:
            self.reload_user_themes()
        return tuple(self._themes)

    def theme_description(self, name):
        return self._theme_catalog.description(name)

    def user_theme_issues(self):
        return tuple(self._user_theme_issues)

    def set_theme(self, name):
        normalized = normalize_theme_name(name)
        if normalized not in self._themes:
            logging.warning("[feather_screen] unknown theme %r; using DEFAULT",
                            name)
            normalized = DEFAULT_THEME
        changed = normalized != self._theme_name
        self._theme_name = normalized
        self._palette = self._themes[normalized]
        if changed:
            self._theme_changed()
        return changed

    def _theme_changed(self):
        # Every pixel on the panel has the old colors: the next paint draws all of
        # it, and the footer waits for the next page.
        self.cover_footer()
        self.redraw_display()

    def modal_scrim(self):
        """Darken the retained framebuffer with 70%-opaque black."""
        return "--batch fill -p 0 0 -s %d %d -c %s" % (SCREEN_WIDTH, SCREEN_HEIGHT, BACKDROP_COLOR)

    # image(x, y, blob, format="fxi1"): an FXI1 raster at (x, y), the blob as the command's payload.
    image = _NATIVE["image"]

    def filled_circle(self, center_x, center_y, radius, color=ThemeColor.PRIMARY):
        """Draw a compact filled circle using horizontal one-pixel spans."""
        radius = max(1, int(radius))
        commands = []
        for offset_y in range(-radius, radius + 1):
            half_width = int(math.sqrt(
                max(0, radius * radius - offset_y * offset_y)))
            commands.append(self.fill(
                int(center_x) - half_width, int(center_y) + offset_y,
                half_width * 2 + 1, 1, color))
        return commands

    def _hint_box_geometry(self, message, center_x, max_width, min_width,
                           font):
        label = str(message).upper()
        font = self.normalize_font_for_text(font, label)
        available = max(1, int(max_width) - 2 * self.HINT_TEXT_PADDING)
        width = min(
            int(max_width),
            max(int(min_width),
                self.text_width(label, font) + 2 * self.HINT_TEXT_PADDING))
        x = int(center_x) - width // 2
        return label, font, available, x, width

    def hint_box(self, message, center_x, y, max_width=740, min_width=180,
                 height=44, border=ThemeColor.SECONDARY,
                 background=ThemeColor.BACKGROUND, font="JetBrainsMono 8pt"):
        """Draw a centered one-line hint with guaranteed inner padding."""
        label, font, available, x, width = self._hint_box_geometry(
            message, center_x, max_width, min_width, font)
        commands = self.panel(
            x, int(y), width, int(height), border=border,
            background=background, line_width=2)
        commands.append(self.text(
            int(center_x), int(y) + int(height) // 2, label, ThemeColor.TEXT,
            font, "center", "middle", max_width=available, truncate=True))
        return commands

    def section_panel(self, title, x, y, width, height, border=ThemeColor.BORDER):
        """Draw a titled content panel with consistent Feather spacing."""
        commands = self.panel(
            x, y, width, height, border=border, background=ThemeColor.PANEL,
            line_width=1)
        commands.append(self.text(
            x + 18, y + 21, str(title).upper(), ThemeColor.PRIMARY,
            "JetBrainsMono 8pt", "left", "middle"))
        return commands

    def text_field(self, x, y, max_width, value, cursor, color=ThemeColor.PRIMARY,
                   masked=False, font="JetBrainsMono 12pt"):
        """Draw the part of a typed value around its cursor that fits
        max_width, left at y, and a caret at the cursor. The face must be
        monospaced: its one advance places the window and the caret."""
        value = str(value)
        cursor = max(0, min(int(cursor), len(value)))
        displayed = "*" * len(value) if masked else value
        font = self.normalize_font_for_text(font, displayed or " ")
        advance = self.font_advance(font)
        visible_characters = max(1, (int(max_width) - 2) // advance)
        start = min(
            max(0, cursor - visible_characters // 2),
            max(0, len(displayed) - visible_characters))
        visible = displayed[start:start + visible_characters]
        commands = []
        if visible:
            commands.append(self.text(
                x, y, visible, color, font, max_width=max_width,
                truncate=True))
        caret_x = min(
            int(x) + int(max_width) - 2,
            int(x) + (cursor - start) * advance)
        commands.append(self.fill(caret_x, int(y) - 13, 2, 26, color))
        return commands

    def numeric_keypad(self, x, y, width, height, title, value, actions,
                       subtitle="", mode="decimal", minimum=None,
                       maximum=None, max_length=10, fraction_digits=None,
                       confirm_label="CONFIRM", border=ThemeColor.BORDER,
                       background=ThemeColor.PANEL,
                       title_color=ThemeColor.TEXT,
                       subtitle_color=ThemeColor.DIM,
                       input_border=ThemeColor.SECONDARY,
                       value_color=ThemeColor.BRIGHT):
        """Draw a self-contained numeric entry window inside page chrome."""
        spec = (mode if isinstance(mode, NumericInputSpec) else
                NumericInputSpec(mode, minimum, maximum, max_length,
                                 fraction_digits))
        actions = dict(actions or {})
        x, y, width, height = map(int, (x, y, width, height))
        padding = max(5, min(10, width // 80, height // 45))
        gap = max(6, min(10, width // 90))
        inner_x = x + padding
        inner_width = width - padding * 2
        title_height = 44
        input_height = 50
        input_gap = 10
        confirm_height = 54
        confirm_gap = 10
        row_gap = 7
        keys_top = y + padding + title_height + input_height + input_gap
        keys_height = (height - padding * 2 - title_height - input_height
                       - input_gap - confirm_gap - confirm_height)
        key_height = max(1, (keys_height - row_gap * 3) // 4)
        confirm_y = y + height - padding - confirm_height
        key_width = max(1, (inner_width - gap * 2) // 3)
        commands = self.panel(
            x, y, width, height, border=border, background=background,
            line_width=1)
        commands.append(self.text(
            x + width // 2,
            y + padding + (12 if subtitle else title_height // 2),
            str(title).upper(), title_color, "JetBrainsMono Bold 8pt",
            "center", "middle",
            max_width=inner_width - 24, truncate=True))
        if subtitle:
            commands.append(self.text(
                x + width // 2, y + padding + 31,
                str(subtitle).upper(), subtitle_color,
                "JetBrainsMono 8pt", "center", "middle",
                max_width=inner_width - 24, truncate=True))
        input_y = y + padding + title_height
        commands += self.panel(
            inner_x, input_y, inner_width, input_height,
            border=input_border, background=background, line_width=2)
        sign_action = (actions.get("sign")
                       if spec.allows_negative and spec.allows_decimal
                       else None)
        sign_width = 68 if sign_action is not None else 0
        commands.append(self.text(
            inner_x + 16, input_y + input_height // 2, value or "_",
            value_color,
            "JetBrainsMono 12pt", "left", "middle",
            max_width=max(1, inner_width - 32 - sign_width), truncate=True))
        if sign_action is not None:
            commands += self.button(
                sign_action, inner_x + inner_width - sign_width, input_y,
                sign_width, input_height, "+/-", state="keypad_aux",
                font="JetBrainsMono 8pt")
        auxiliary = "decimal" if spec.allows_decimal else (
            "sign" if spec.allows_negative else None)
        rows = (("1", "2", "3"), ("4", "5", "6"),
                ("7", "8", "9"), (auxiliary, "0", "backspace"))
        for row, keys in enumerate(rows):
            key_y = keys_top + row * (key_height + row_gap)
            for column, token in enumerate(keys):
                key_x = inner_x + column * (key_width + gap)
                label = ({"decimal": ".", "sign": "+/-",
                          "backspace": "BACK"}.get(token, token) or "-")
                action = actions.get(token) if token is not None else None
                if action is None:
                    state = "disabled"
                elif token in ("decimal", "sign", "backspace"):
                    state = "keypad_aux"
                else:
                    state = "keypad"
                commands += self.button(
                    action or "numeric.disabled.%d.%d" % (row, column),
                    key_x, key_y, key_width, key_height, label,
                    state=state,
                    font=("JetBrainsMono 8pt" if token == "backspace"
                          else "JetBrainsMono 12pt"))
        confirm_action = actions.get("confirm")
        commands += self.button(
            confirm_action or "numeric.disabled.confirm", inner_x, confirm_y,
            inner_width, confirm_height, confirm_label,
            state=("keypad_confirm" if confirm_action is not None
                   and spec.is_valid(value) else "disabled"),
            font="JetBrainsMono Bold 10pt")
        return commands

    def dot_grid(self, x, y, width, height, columns=11, rows=7,
                 color=ThemeColor.MUTED, clip=None):
        """Draw a sparse point grid, optionally limited to a redraw region."""
        commands = []
        if columns < 2 or rows < 2:
            return commands
        if clip is not None:
            clip_x, clip_y, clip_width, clip_height = clip
            clip_right = clip_x + clip_width
            clip_bottom = clip_y + clip_height
        for row in range(rows):
            point_y = y + row * height // (rows - 1)
            for column in range(columns):
                point_x = x + column * width // (columns - 1)
                if (clip is None
                        or (clip_x <= point_x < clip_right
                            and clip_y <= point_y < clip_bottom)):
                    commands.append(self.fill(point_x, point_y, 1, 1, color))
        return commands

    def corner_marks(self, x, y, width, height, length=12, color=ThemeColor.PRIMARY):
        """Draw four open targeting corners around a control surface."""
        right = x + width - 1
        bottom = y + height - 1
        commands = []
        for horizontal_x, vertical_x in ((x, x), (right - length + 1, right)):
            commands.append(self.fill(horizontal_x, y, length, 1, color))
            commands.append(self.fill(vertical_x, y, 1, length, color))
            commands.append(self.fill(horizontal_x, bottom, length, 1, color))
            commands.append(self.fill(
                vertical_x, bottom - length + 1, 1, length, color))
        return commands

    def metric_row(self, x, y, width, label, value, unit="",
                   label_color=ThemeColor.PRIMARY, value_color=ThemeColor.TEXT,
                   label_font="JetBrainsMono 8pt", value_font="JetBrainsMono 8pt"):
        """Draw a compact label/value/unit row for status cards.

        When the label and the value do not fit side by side, each gets a
        share of the row and Typer truncates the text that exceeds it with an
        ellipsis: the longer text is shortened first, both when neither fits
        in half the row.
        """
        value_right = width - (34 if unit else 0)
        label, value = str(label), str(value)
        metrics = get_font_metrics()
        label_width = metrics.text_width(label, label_font)
        value_width = metrics.text_width(value, value_font)
        available = max(2, value_right - 4)  # 4 px apart
        label_limit = value_limit = None
        if label_width + value_width > available:
            half = available // 2
            if label_width <= half:
                value_limit = available - label_width
            elif value_width <= half:
                label_limit = available - value_width
            else:
                label_limit, value_limit = half, available - half

        def row_text(text_x, text, color, font, align, limit):
            if limit is None:
                return self.text(text_x, y, text, color, font, align, "middle")
            return self.text(text_x, y, text, color, font, align, "middle",
                             max_width=limit, truncate=True)

        commands = [
            row_text(x, label, label_color, label_font, "left", label_limit),
            row_text(x + value_right, value, value_color, value_font, "right", value_limit),
        ]
        if unit:
            commands.append(self.text(
                x + width, y, unit, label_color, label_font,
                "right", "middle"))
        return commands

    def vertical_gauge(self, x, y, width, height, title, value,
                       minimum, maximum, initial=None,
                       value_color=ThemeColor.PRIMARY):
        """Draw an auto-scaled vertical gauge with a movable start marker."""
        value = float(value)
        minimum = float(minimum)
        maximum = float(maximum)
        if maximum <= minimum:
            padding = max(1.0, abs(value) * 0.05)
            minimum = value - padding
            maximum = value + padding
        track_top = y + 78
        track_bottom = y + height - 28
        track_height = max(1, track_bottom - track_top)
        track_x = x + (width - 16) // 2
        track_width = 16

        def gauge_y(sample):
            ratio = ((float(sample) - minimum) / (maximum - minimum))
            ratio = max(0.0, min(1.0, ratio))
            return track_bottom - int(round(ratio * track_height))

        def number(sample):
            sample = float(sample)
            if abs(sample) < 10000:
                return str(round(sample))
            return "%.0e" % sample

        value_y = gauge_y(value)
        commands = self.panel(
            x, y, width, height, border=ThemeColor.BORDER,
            background=ThemeColor.PANEL, line_width=1)
        commands += [
            self.text(x + width // 2, y + 20, str(title).upper(),
                      ThemeColor.PRIMARY, "JetBrainsMono 8pt", "center", "middle"),
            self.text(x + width // 2, y + 48, number(value),
                      ThemeColor.TEXT, "JetBrainsMono Bold 8pt",
                      "center", "middle"),
        ]
        commands += [
            self.fill(track_x, track_top, track_width, track_height,
                      ThemeColor.MUTED),
            self.stroke(track_x, track_top, track_width, track_height,
                        ThemeColor.BORDER, 1),
            self.fill(track_x + 2, value_y,
                      max(1, track_width - 4),
                      max(1, track_bottom - value_y), value_color),
        ]
        if initial is not None:
            marker_y = gauge_y(initial)
            commands += [
                self.fill(x + 6, marker_y, max(1, width - 12), 2,
                          ThemeColor.SECONDARY),
                self.fill(x + 3, marker_y - 2, 4, 6, ThemeColor.SECONDARY),
            ]
        return commands

    def joystick_knob(self, x, y, axis="xy", size=25, color=ThemeColor.PRIMARY, *,
                      glyph=None, background=ThemeColor.PANEL):
        """Draw a centered knob with a glyph independent of its movement axis."""
        if glyph is None:
            glyph = "bars" if axis == "z" else "cross"
        if glyph not in ("bars", "cross", "none"):
            raise ValueError("Unknown knob glyph: %s" % glyph)
        size = max(9, int(size))
        if size % 2 == 0:
            size += 1
        left = int(x) - size // 2
        top = int(y) - size // 2
        commands = self.panel(
            left, top, size, size, border=color, background=background,
            line_width=2)
        center_x = int(x)
        center_y = int(y)
        if glyph == "bars":
            commands += [
                self.fill(center_x - 6, center_y - 3, 13, 1, color),
                self.fill(center_x - 6, center_y + 3, 13, 1, color),
            ]
        elif glyph == "cross":
            commands += [
                self.fill(center_x - 6, center_y, 13, 1, color),
                self.fill(center_x, center_y - 6, 1, 13, color),
            ]
        return commands

    # normalize_font(font, allow_proportional=False) maps UI font requests to sizes compiled into typer;
    # normalize_font_for_text(font, value) uses the requested face when it covers the rendered text. Both
    # measure on the engine catalog of _font_metrics().
    normalize_font = classmethod(renderer_normalize_font)
    normalize_font_for_text = classmethod(renderer_normalize_font_for_text)

    @classmethod
    def font_advance(cls, font):
        metric = get_font_metrics().metric(font)
        if not metric.monospaced:
            raise ValueError("font is not monospaced: %s" % metric.name)
        return metric.advance_x

    # text_width(value, font): the advance typer draws value with.
    text_width = classmethod(renderer_text_width)

    @property
    def hitboxes(self):
        """Ordered prepared input regions, including repeated actions."""
        return tuple(Hitbox(action, *spec, layer)
                     for layer, entries in (("base", self._hitboxes),
                                            ("overlay", self._overlay_hitboxes))
                     for action, spec in entries)

    def overlay_hitbox(self, action, x, y, width, height, continuous=False):
        if not self._prepare_surface():
            return ""
        logical = action_wire_id(action) if isinstance(action, Action) else str(action)
        self._register_region(logical, x, y, width, height, continuous, overlay=True)
        return self._hitbox(
            self._wire_action(action), x, y, width, height,
            continuous, layer="overlay")

    def _toggle_commands(self, x, y, width, height, thumb_x, enabled):
        background, border, thumb_color = self.BUTTON_COLORS["toggle" if enabled else "toggle-disabled"]
        thumb_size, _, inset_y = self._toggle_geometry(width, height)
        return [
            self.fill(x, y, width, height, background),
            self.stroke(x, y, width, height, border, 2),
            self.fill(thumb_x, y + inset_y, thumb_size, thumb_size, thumb_color),
        ]

    @staticmethod
    def _toggle_geometry(width, height):
        half = width // 2
        thumb_size = max(1, min(height, half) - 10)
        inset_y = (height - thumb_size) // 2
        # Keep each resting thumb in its own half, even on a tall or narrow track.
        inset_x = min(inset_y, max(0, half - thumb_size))
        return thumb_size, inset_x, inset_y

    @classmethod
    def _toggle_places(cls, x, width, height):
        """The thumb's x, off and on: equal insets at both ends of the track."""
        thumb_size, inset_x, _ = cls._toggle_geometry(width, height)
        return x + inset_x, x + width - inset_x - thumb_size

    def toggle(self, action, x, y, width, height, active, enabled=True, progress=None):
        """Draw a rectangular switch with a centered square thumb and no text;
        moving to active, progress (0 to 1) of the way from the other side."""
        if width <= 0 or height <= 0 or not self._prepare_surface():
            return []
        logical_action = action_wire_id(action) if isinstance(action, Action) else str(action)
        left, right = self._toggle_places(x, width, height)
        start, finish = (left, right) if active else (right, left)
        thumb_x = finish if progress is None else start + math.floor((finish - start) * progress)
        commands = self._toggle_commands(x, y, width, height, thumb_x, enabled)
        if enabled:
            commands.append(self.action_hitbox(action, x, y, width, height))
        else:
            self._unregister_region(logical_action, x, y, width, height)
        return commands

    def _shown_region(self, action):
        """The rectangle of action's input on the visible surface, None without one."""
        shown = self._frame_owner.shown_input() if self._frame_owner is not None else None
        if shown is None:
            regions = ((name, *spec[:4]) for entries in ("_hitboxes", "_overlay_hitboxes")
                       for name, spec in self._visible_input(entries))
        else:
            regions = (region for layer in ("base", "overlay") for region in shown[layer])
        return next((tuple(region[1:5]) for region in regions if region[0] == action), None)

    def animate_toggle(self, action, active, scheduler, duration=0.12):
        if not self.render_allowed:
            return False
        # A disabled toggle has no input; one already moving there keeps moving.
        region = self._shown_region(action)
        if region is None or self._toggles.get(action) == bool(active):
            return False
        x, y, width, height = region
        left, right = self._toggle_places(x, width, height)
        start, finish = (left, right) if active else (right, left)
        generation = self._generation
        frames = 4
        self._toggles[action] = bool(active)

        def draw_frame(_eventtime, index):
            if index == frames:
                self._toggles.pop(action, None)
            if self._generation != generation:
                return
            thumb_x = start + (finish - start) * index // frames
            self.prioritize_next_batch("animation", "toggle:%s" % action)
            self.send(self._toggle_commands(x, y, width, height, thumb_x, True))

        draw_frame(None, 1)
        for index in range(2, frames + 1):
            scheduler(lambda eventtime, step=index:
                      draw_frame(eventtime, step),
                      duration * (index - 1) / (frames - 1))
        return True

    def block_input(self):
        """Leave only the wake region of the page's input until the next page
        draws: a save in progress takes no other tap. The screen's frame owner
        blocks the input of a tree it draws."""
        owner = self._frame_owner
        if owner is not None and owner.block_input():
            self._reset_interactions()
            return None
        if not self._prepare_surface():
            return None
        self._reset_interactions()
        self.send([
            self.clear_hitboxes("base"),
            self.clear_hitboxes("overlay"),
            self._wake_hitbox(),
        ])

    def _reset_interactions(self):
        self._buttons = {}
        self._toggles = {}
        self._hitboxes = []
        self._overlay_hitboxes = []
        self._pressed_buttons = set()

    def button_surface(self, action, x, y, width, height,
                       normal_commands, pressed_commands):
        """Register complete normal and pressed surfaces for a rich button."""
        if not self._prepare_surface():
            return []
        logical_action = (
            action_wire_id(action) if isinstance(action, Action) else str(action))
        normal = tuple(normal_commands)
        pressed = tuple(pressed_commands)
        self._set_button(logical_action, _ButtonSpec(
            x, y, width, height, "", "enabled", None,
            None, "surface", None, None, None, (normal, pressed)))
        surface = pressed if logical_action in self._pressed_buttons else normal
        return list(surface) + [
            self.action_hitbox(action, x, y, width, height)]

    def _begin_dialog(self, bounds, modal):
        self._dialog_bounds = bounds if modal else self._dialog_bounds + bounds
        if not modal:
            return []
        self._reset_interactions()
        return [self.clear_hitboxes("base"), self.clear_hitboxes("overlay"), self._wake_hitbox()]

    def popup(self, dismiss, x, y, width, height, *, border=ThemeColor.PRIMARY,
              background=ThemeColor.PANEL, line_width=2, preserve_header_action=True):
        """Prepare one modal panel; its component root supplies arbitrary content."""
        if not self._prepare_surface():
            return []
        commands = self._begin_dialog(((x, y, width, height),), True)
        commands.append(self.action_hitbox(dismiss, 0, 0, SCREEN_WIDTH, SCREEN_HEIGHT))
        commands += self.panel(x, y, width, height, border, background, line_width)
        if preserve_header_action and self._header_action is not None:
            commands += self._header_action_commands()
            action = self._header_action[0]
            wire = action_wire_id(action) if isinstance(action, Action) else str(action)
            for region in self.hitboxes:
                if region.action == wire:
                    commands.append(self.overlay_hitbox(wire, region.x, region.y, region.width, region.height))
                    break
        return commands

    def _layout_dialog(self, title, lines, buttons, button_groups, x, y, width, height,
                       modal, custom_body, page, screen_width=SCREEN_WIDTH):
        policy = self._dialog_policy
        if screen_width != SCREEN_WIDTH:
            index = len(_DIALOG_POLICY) + 2
            policy = policy[:index] + (screen_width,) + policy[index + 1:]
        return dialog_layout(self, policy, title, tuple(lines), tuple(buttons), button_groups,
                             x, y, width, height, modal and not custom_body, custom_body, page)

    def dialog(self, title, lines, buttons, x=160, y=130, width=480,
               height=220, tone="warning", modal=True,
               preserve_header_action=True, page=0, page_actions=None,
               custom_body=False, button_groups=()):
        """Build a modal dialog from standard panel, text, and button primitives.

        ``buttons`` contains persistent footer ``(action, label, state)`` tuples.
        ``button_groups`` contains body rows of the same tuples. Clearing all
        existing hitboxes makes a dialog genuinely modal even when it only
        covers one control region visually. Set ``modal`` to false for a
        localized overlay whose caller will explicitly re-register the
        controls that remain available. Set ``preserve_header_action`` to
        false when loss of input makes even that action unusable.
        Set ``custom_body`` when the caller draws its own content in the panel.
        """
        if not self._prepare_surface():
            return []
        tones = {
            "warning": ThemeColor.WARNING,
            "danger": ThemeColor.DANGER,
            "info": ThemeColor.PRIMARY,
        }
        border = tones.get(tone, ThemeColor.PRIMARY)
        button_groups = tuple(tuple(group) for group in button_groups if group)
        x, y, width, height, title_rows, visible_lines, page, page_count, has_footer, footer_height, rows = (
            self._layout_dialog(title, lines, buttons, button_groups, x, y, width, height,
                                modal, custom_body, page))
        if page_count > 1 and (page_actions is None or len(page_actions) != 2):
            raise ValueError("Overflowing dialog requires previous and next actions")
        outer_x = max(0, x - 8)
        outer_y = max(HEADER_BOTTOM + 1, y - 8)
        outer_right = min(SCREEN_WIDTH, x + width + 8)
        outer_bottom = min(CONTENT_BOTTOM, y + height + 8)
        outer_bounds = (outer_x, outer_y, outer_right - outer_x,
                        outer_bottom - outer_y)
        commands = self._begin_dialog(((x, y, width, height),), modal)
        show_header_action = (preserve_header_action and self._header_action is not None)
        if modal:
            # Hide fragments of retained controls immediately beside the frame.
            commands.append(self.fill(*outer_bounds, ThemeColor.BACKGROUND))
        commands += self.panel(
            x, y, width, height, border=border, background=ThemeColor.PANEL)
        for line, title_y in title_rows:
            commands.append(self.text(
                x + width // 2, title_y, line, border,
                DIALOG_TITLE_FONT, "center", "middle",
                max_width=width - 2 * self.DIALOG_TEXT_PADDING,
                truncate=True))
        body_y = dialog_body_offset(title_rows)
        pager_reserve = DIALOG_PAGER_RESERVE if page_count > 1 else 0
        for index, line in enumerate(visible_lines):
            commands.append(self.text(
                x + (width - pager_reserve) // 2,
                y + body_y + index * DIALOG_LINE_SPACING,
                str(line), ThemeColor.TEXT, DIALOG_BODY_FONT, "center", "middle",
                max_width=width - 2 * self.DIALOG_TEXT_PADDING - pager_reserve,
                truncate=True))
        if page_count > 1:
            commands += self.dialog_pager(
                page, page_count, page_actions, x, y, width, height,
                has_buttons=has_footer, footer_height=footer_height, body_y=body_y)
        for specs, font, bounds in rows:
            for (action, label, state), target in zip(specs, bounds):
                commands += self.button(
                    action, *target, label, state=state, font=font)
        if modal and show_header_action:
            commands += self._header_action_commands()
        return commands

    def status_dialog(self, title, detail=None, buttons=(), *, description=None, description_font=None,
                      note=None, note_color=ThemeColor.TEXT, tone="warning",
                      width=DIALOG_MIN_WIDTH, height=DIALOG_MIN_HEIGHT,
                      modal=True, preserve_header_action=True):
        """Render optional value, wrapped explanation, and note below a status.

        Empty slots take no space. The explanation uses the body font unless
        description_font is set. It may be truncated to keep
        the note and standard footer actions visible. Use ``dialog`` for
        instructions that need pagination or more than two rows of actions.
        """
        buttons = tuple(buttons)
        texts = tuple(
            (str(value), font, color, wrap)
            for value, font, color, wrap in (
                (detail, DIALOG_STATUS_FONT, ThemeColor.TEXT, False),
                (description, description_font or DIALOG_BODY_FONT, ThemeColor.TEXT, True),
                (note, DIALOG_STATUS_FONT, note_color, False))
            if value is not None and str(value).strip())
        x, y, width, height, rows = status_dialog_layout(
            self, self._dialog_policy, title, buttons, tuple((text[0], text[3]) for text in texts),
            int(width), int(height), description_font or None)
        commands = self.dialog(
            title, (), buttons, x=x, y=y, width=width, height=height,
            tone=tone, modal=modal, custom_body=bool(texts),
            preserve_header_action=preserve_header_action)
        if not commands:
            return commands

        for (value, font, color, wrap), (text_height, text_y) in zip(texts, rows):
            commands.append(self.text(
                x + width // 2, text_y, value, color, font, "center", "middle",
                max_width=width - 2 * self.DIALOG_TEXT_PADDING, max_height=text_height, wrap=wrap, truncate=True))
        return commands

    def dialog_pager(self, page, page_count, actions, x, y, width, height,
                     *, has_buttons=True, footer_height=DIALOG_BUTTON_HEIGHT, body_y=DIALOG_BODY_Y):
        """Draw the narrow vertical page controls shared by dialog panels."""
        if page_count <= 1:
            return []
        previous, following = actions
        prev_bounds, next_bounds, counter = dialog_pager_bounds(
            x, y, width, height, has_buttons, footer_height)
        commands = self.arrow_button(
            previous, *prev_bounds, "up", active=page > 0)
        commands += self.arrow_button(
            following, *next_bounds, "down", active=page + 1 < page_count)
        if body_y != DIALOG_BODY_Y or (has_buttons and footer_height > DIALOG_BUTTON_HEIGHT):
            commands.append(self.text(
                counter[0], (prev_bounds[1] + prev_bounds[3] + next_bounds[1]) // 2,
                "%d/%d" % (page + 1, page_count), ThemeColor.DIM,
                DIALOG_BODY_FONT, "center", "middle",
                max_width=DIALOG_PAGER_RESERVE - 8, truncate=True))
            return commands
        for offset, value in ((-24, page + 1), (24, page_count)):
            commands.append(self.text(
                counter[0], counter[1] + offset, str(value), ThemeColor.DIM,
                DIALOG_BODY_FONT, "center", "middle",
                max_width=DIALOG_PAGER_RESERVE - 8, truncate=True))
        commands.append(self.fill(
            counter[0] - 11, counter[1] - 1, 22, 2, ThemeColor.DIM))
        return commands

    def flash_button(self, action):
        spec = self._visible_input("_buttons").get(action)
        if spec is None:
            return False
        self._press(action, visible=True)
        self.prioritize_next_batch("animation", "button:%s" % action)
        if spec.layout == "surface":
            self.send(spec.surfaces[1], button_feedback=True)
        elif spec.layout in ("arrow-up", "arrow-down"):
            self.send(self._arrow_button_commands(
                action, spec.x, spec.y, spec.width, spec.height,
                spec.layout[6:], "pressed", False), button_feedback=True)
        else:
            self.send(self._button_commands(
                action, spec.x, spec.y, spec.width, spec.height,
                spec.label, "pressed", spec.font, spec.subtitle, False,
                spec.layout, spec.subtitle_font, spec.subtitle_color,
                spec.accent), button_feedback=True)
        return True

    def restore_button(self, action):
        spec = self._visible_input("_buttons").get(action)
        self._press(action, False, visible=True)
        if spec is None:
            return False
        self.prioritize_next_batch("state", "button:%s" % action)
        if spec.layout == "surface":
            self.send(spec.surfaces[0], button_feedback=True)
        elif spec.layout in ("arrow-up", "arrow-down"):
            self.send(self._arrow_button_commands(
                action, spec.x, spec.y, spec.width, spec.height,
                spec.layout[6:], spec.state, False), button_feedback=True)
        else:
            self.send(self._button_commands(
                action, spec.x, spec.y, spec.width, spec.height,
                spec.label, spec.state, spec.font, spec.subtitle, False,
                spec.layout, spec.subtitle_font, spec.subtitle_color,
                spec.accent), button_feedback=True)
        return True

    def set_header_action(self, action=None, label="", state="danger",
                          font="JetBrainsMono Bold 8pt"):
        if not self._prepare_surface():
            return False
        value = (None if action is None else
                 (action, str(label), str(state), str(font)))
        if value == self._header_action:
            return False
        previous = self._header_action
        self._header_action = value
        if previous is not None:
            old_action = previous[0]
            old_wire_id = (action_wire_id(old_action)
                           if isinstance(old_action, Action)
                           else str(old_action))
            self._pop_button(old_wire_id)
            self._press(old_wire_id, False)
        return True

    @property
    def header_action(self):
        """The action set_header_action shows in the header's right slot, or None."""
        return None if self._header_action is None else self._header_action[0]

    def _header_action_commands(self):
        if self._header_action is None:
            return []
        action, label, state, font = self._header_action
        return self.button(
            action, *self._header_action_bounds, label, state=state, font=font)

    def redraw_page(self):
        """Rebuild interactions and pixels through the normal page lifecycle."""
        return self.begin_page(self._page_title, back=self._page_back)

    @contextmanager
    def rebuild_page_input(self):
        """Replace page input and feedback without repainting unchanged pixels."""
        self.invalidate_input_generation()
        self.redraw_page_hitboxes()
        commands = []
        yield commands
        active = self._keep_input()
        self._toggles = {action: spec for action, spec in self._toggles.items() if action in active}
        # Paint commands may contain old IDs. Final replay replaces all of
        # them, including retained overlay regions, with this generation.
        commands.extend(self._input_commands())
        self.prioritize_next_batch("state")

    def show_page(self, title, back=False):
        """A page starts on the panel: its title and back, the footer beside it, and
        a new input generation without the previous page's interactions. begin_page
        draws its chrome too; a page's slot in a screen tree draws its own header.
        False when the surface cannot be prepared."""
        if not self._prepare_surface():
            return False
        self._page_title = title
        self._page_back = back
        # The page is on the panel: the footer shows beside it.
        self._footer.show()
        self._semantic_page_id = None
        self.invalidate_input_generation()
        self._reset_interactions()
        self._menu_suppressed = False
        return True

    def begin_page(self, title, back=False):
        if not self.show_page(title, back):
            return []
        show_header_action = self._header_action is not None
        commands = [
            self.clear_hitboxes("base"),
            self.clear_hitboxes("overlay"),
            self._wake_hitbox(),
            # Preserve the footer framebuffer. It is a persistent status area
            # that the frame loop draws on its own when one of its values changes.
            self.fill(0, 0, SCREEN_WIDTH, FOOTER_Y - 2, ThemeColor.BACKGROUND),
            # Keep the dirty rectangle above the persistent footer. A full
            # height outer stroke would make TextDrawer.flush() copy the whole
            # footer even though none of its pixels changed.
            self.stroke(FRAME_X, FRAME_Y, FRAME_WIDTH,
                        FOOTER_Y - FRAME_Y - 1,
                        ThemeRole.HEADER_BORDER, 1),
            self.fill(10, 6, 780, HEADER_BOTTOM - 6, ThemeRole.HEADER_BACKGROUND),
        ]
        if back:
            commands += self.button("nav.back", *self._back_bounds, "< BACK",
                                    font="JetBrainsMono Bold 8pt")
        title = str(title).upper()
        title_width = 440 if back or show_header_action or self._busy_label is not None else 740
        commands += [
            self.text(400, 29, title, ThemeRole.HEADER_TEXT,
                      "JetBrainsMono 12pt",
                      "center", "middle", max_width=title_width, truncate=True),
            self.fill(18, HEADER_BOTTOM, 764, 1, ThemeRole.HEADER_BORDER),
            self.fill(18, FOOTER_Y - 2, 764, 1, ThemeColor.BORDER),
        ]
        if self._busy_label is not None and not show_header_action:
            busy_label = self._busy_label
            commands += [
                self.fill(BUSY_NOTICE_X, BUSY_NOTICE_Y,
                          BUSY_NOTICE_WIDTH, BUSY_NOTICE_HEIGHT,
                          ThemeRole.HEADER_BACKGROUND),
                self.stroke(BUSY_NOTICE_X, BUSY_NOTICE_Y,
                            BUSY_NOTICE_WIDTH, BUSY_NOTICE_HEIGHT,
                            ThemeColor.WARNING, 2),
                self.text(BUSY_NOTICE_X + BUSY_NOTICE_WIDTH // 2,
                          BUSY_NOTICE_Y + BUSY_NOTICE_HEIGHT // 2,
                          busy_label, ThemeColor.WARNING,
                          "JetBrainsMono Bold 8pt", "center", "middle",
                          max_width=132, truncate=True),
            ]
        if show_header_action:
            commands += self._header_action_commands()
        return commands

    def _footer_content(self):
        """The footer's tree in footer.FOOTER_BOUNDS, reading the values
        _footer_state gives. A product overrides both to draw its own strip;
        the state always holds footer.FooterState's LEFT and RIGHT."""
        return footer_content()

    def _footer_state(self, left, right):
        return {FooterState.LEFT: left, FooterState.RIGHT: right}

    @property
    def footer_chrome(self):
        """The footer (footer.Footer) the frame owner's screen places beside the page."""
        return self._footer

    def footer(self, left, right):
        """Show the status texts; the frame owner's composition draws them."""
        self._footer.write((str(left), str(right)), self._frame_owner)

    def cover_footer(self):
        # Its area belongs to something else until the next page shows it.
        self._footer.hide()

    def submit_frame(self, commands, receipt=None, receipt_phase="presented"):
        """Submit a frame the frame owner's screen built; it is not composed again."""
        if receipt_phase != "presented" and (receipt_phase != "staged" or receipt is None):
            raise ValueError("staged receipts require a token; unknown receipt phase")
        receipt = None if receipt is None else validate_render_receipt_token(receipt)
        try:
            return self._submit(RenderSubmission(_immutable_commands(commands), receipt, receipt_phase))
        except ValueError as exc:
            logging.warning("[feather_screen] rejected render batch: %s", exc)
            return False

    def toast(self, message):
        """Show message as the screen's toast until hide_toast(): a one-line hint
        near the content's bottom over everything else, whose tap reports
        DismissToast. The frame owner's screen draws it as its top layer
        (ScreenRoot.show_toast), so the page's later frames keep it; nothing
        shows while output is frozen."""
        owner = self._frame_owner
        if owner is None or self._output_frozen:
            return False
        message = str(message)
        _label, _font, _available, x, width = self._hint_box_geometry(
            message, TOAST_CENTER_X, TOAST_MAX_WIDTH, TOAST_MIN_WIDTH, TOAST_FONT)
        return owner.show_toast(message, (x, CONTENT_BOTTOM - TOAST_HEIGHT - 8, width, TOAST_HEIGHT))

    def hide_toast(self, paint=True):
        """Hide the toast toast() showed: the frame shows what lies beneath it.
        Without paint, the owner's next paint draws that."""
        owner = self._frame_owner
        return owner is not None and owner.hide_toast(paint=paint)

    def toast_commands(self, message, box):
        """The toast's drawing in its box (x, y, width, height), as toast() placed
        it: the hint box and the dismiss region over it."""
        x, y, width, height = box
        commands = self.hint_box(
            message, TOAST_CENTER_X, y, max_width=TOAST_MAX_WIDTH, min_width=TOAST_MIN_WIDTH, height=height,
            border=ThemeColor.SECONDARY, background=ThemeColor.BACKGROUND, font=TOAST_FONT)
        commands.append(self.overlay_hitbox(DismissToast(), x, y, width, height))
        return commands

    def _header_drawn(self):
        """Whether the frame owner's tree draws the header (a page's slot), which
        then shows the busy label; otherwise the page drew it with begin_page."""
        owner = self._frame_owner
        return owner is not None and owner.header_changed()

    def busy_notice(self, label="PLEASE WAIT"):
        label = str(label).upper()
        if label == self._busy_label:
            return
        self._busy_label = label
        if self._header_action is not None or self._header_drawn():
            return
        self.send([
            self.fill(BUSY_NOTICE_X, BUSY_NOTICE_Y,
                      BUSY_NOTICE_WIDTH, BUSY_NOTICE_HEIGHT,
                      ThemeRole.HEADER_BACKGROUND),
            self.stroke(BUSY_NOTICE_X, BUSY_NOTICE_Y,
                        BUSY_NOTICE_WIDTH, BUSY_NOTICE_HEIGHT,
                        ThemeColor.WARNING, 2),
            self.text(BUSY_NOTICE_X + BUSY_NOTICE_WIDTH // 2,
                      BUSY_NOTICE_Y + BUSY_NOTICE_HEIGHT // 2,
                      label, ThemeColor.WARNING,
                      "JetBrainsMono Bold 8pt", "center", "middle",
                      max_width=132, truncate=True),
        ])

    def clear_busy_notice(self):
        if self._busy_label is None:
            return
        self._busy_label = None
        if self._header_action is not None or self._header_drawn():
            return
        commands = [
            self.fill(BUSY_NOTICE_X, BUSY_NOTICE_Y,
                      BUSY_NOTICE_WIDTH, BUSY_NOTICE_HEIGHT,
                      ThemeRole.HEADER_BACKGROUND)]
        menu = self._buttons.get("nav.menu")
        if menu is not None:
            commands += self._button_commands(
                "nav.menu", menu.x, menu.y, menu.width, menu.height,
                menu.label, menu.state, menu.font, menu.subtitle,
                self._menu_suppressed, menu.layout, menu.subtitle_font,
                menu.subtitle_color, menu.accent)
            self._menu_suppressed = False
        self.send(commands)


FeatherRenderer._dialog_policy = _dialog_policy(FeatherRenderer)


def rectangles_overlap(first, second):
    """Test helper used to keep page hitboxes away from the footer."""
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah
