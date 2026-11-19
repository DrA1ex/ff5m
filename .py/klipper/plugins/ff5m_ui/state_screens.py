## Product state screens as declarative pages.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""A state screen covers every page while the printer cannot take input. Each is one tree, built on first use and drawn
by the core; a turn of its animation is a state write."""

from functools import lru_cache

from ui.bindings import bind, state
from ui.identity import StateKey

from .keys import AppPage

LOADER_TITLE = "OPERATION IN PROGRESS"
LOADER_STEPS = 5
_PULSE_RADII = (8, 12, 16, 12)


class LoaderState(StateKey):
    __key_namespace__ = "ui.pages.state_screens.LoaderState"
    MESSAGE = state(str, default="")
    PHASE = state(int, default=0, minimum=0)


class StartupState(StateKey):
    __key_namespace__ = "ui.pages.state_screens.StartupState"
    TITLE = state(str, default="")
    DETAIL = state(str, default="")
    PULSE_RADIUS = state(int, default=_PULSE_RADII[0], minimum=1)


def loader_values(message, phase):
    return {LoaderState.MESSAGE: message, LoaderState.PHASE: phase % LOADER_STEPS}


def startup_values(title, detail, phase=0):
    return {StartupState.TITLE: title, StartupState.DETAIL: detail,
            StartupState.PULSE_RADIUS: _PULSE_RADII[phase % len(_PULSE_RADII)]}


def _screen():
    from ui.layout import Rect
    return Rect(0, 0, 800, 480)


def _centred(text, y, height):
    """text centred on the screen, in a box around y."""
    return text.offset(0, y - height // 2).size(800, height)


def _loader_step(index):
    from ui.components import Fill, Frame
    from ui.layout import StateCase
    from ui.theme import ThemeColor

    return Frame(
        StateCase(Fill(ThemeColor.PRIMARY), selector=bind(LoaderState.PHASE), expected=index),
        background=ThemeColor.MUTED, line_width=0,
    ).offset(290 + index * 48, 280).size(32, 12)


@lru_cache(maxsize=None)
def loader_page():
    """A yielding operation's view under the page header (LOADER_TITLE): nothing but the header action takes input."""
    from ui.components import Text
    from ui.layout import Overlay, PageTree
    from ui.theme import ThemeColor

    return PageTree(
        Overlay(
            # Bottom-aligned so a second line grows into the free band under the header; below it there is no room.
            Text(bind(LoaderState.MESSAGE), ThemeColor.TEXT, "JetBrainsMono Bold 16pt", "center", "bottom",
                 max_width=704, max_height=93, wrap=True, truncate=True).offset(48, 122).size(704, 93),
            _centred(Text("PLEASE WAIT", ThemeColor.DIM, "JetBrainsMono 12pt"), 235, 30),
            *(_loader_step(index) for index in range(LOADER_STEPS))),
        _screen(), page_id=AppPage.LOADER, state_schema=tuple(LoaderState))


def _full_screen(*content, page_id, state_schema=()):
    """A page that owns the whole panel, the footer's strip included; only the wake region takes input."""
    from ui.components import Fill, Hitbox
    from ui.header import WAKE
    from ui.layout import Overlay, PageTree
    from ui.theme import ThemeColor

    page = PageTree(Overlay(Hitbox(WAKE), Fill(ThemeColor.OVERLAY), *content), _screen(), page_id=page_id,
                    state_schema=state_schema)
    page.covers_footer = True
    return page


@lru_cache(maxsize=None)
def render_error_background():
    """A render error dialog can be drawn without arranging the failed page again."""
    return _full_screen(page_id=AppPage.RENDER_ERROR)


@lru_cache(maxsize=None)
def startup_page():
    """The screen while Klipper starts, restarts or shuts down: it owns the panel until the host is ready."""
    from ui.components import Disc, Fill, Panel, Text
    from ui.layout import Overlay
    from ui.theme import ThemeColor

    return _full_screen(
        Panel(ThemeColor.PRIMARY, ThemeColor.PANEL, 2).offset(150, 120).size(500, 250),
        _centred(Text(bind(StartupState.TITLE), ThemeColor.PRIMARY, "JetBrainsMono Bold 16pt"), 170, 44),
        # Its own bounds: a pulse repaints the disc alone.
        Overlay(Fill(ThemeColor.PANEL), Disc(bind(StartupState.PULSE_RADIUS), ThemeColor.SECONDARY))
        .offset(374, 206).size(53, 53),
        _centred(Text("PLEASE WAIT", ThemeColor.TEXT, "JetBrainsMono 12pt"), 300, 30),
        _centred(Text(bind(StartupState.DETAIL), ThemeColor.DIM, "JetBrainsMono 8pt"), 335, 24),
        page_id=AppPage.STARTUP, state_schema=tuple(StartupState))


@lru_cache(maxsize=None)
def applying_page():
    """The screen while changed mod parameters are applied."""
    from ui.components import Fill, Panel, Text
    from ui.theme import ThemeColor

    return _full_screen(
        Panel().offset(160, 145).size(480, 180),
        _centred(Text("APPLYING CHANGES", ThemeColor.TEXT, "JetBrainsMono Bold 16pt"), 205, 44),
        _centred(Text("PLEASE WAIT", ThemeColor.DIM, "JetBrainsMono 12pt"), 260, 30),
        Fill(ThemeColor.PRIMARY).offset(310, 292).size(180, 8),
        page_id=AppPage.APPLYING)
