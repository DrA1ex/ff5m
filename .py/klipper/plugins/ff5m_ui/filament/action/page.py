"""Declarative filament load, unload, and purge page."""
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from enum import Enum

from ui.bindings import bind, derived
from ui.components import Button, Fill, Frame, Metric, Text
from ui.layout import Column, PageTree, Rect, Row, Spacer
from ...keys import AppPage
from ...styles import UI_STYLES, UiStyle
from ..actions import DONE, LOAD, PURGE, RESUME, UNLOAD
from ..state import FilamentState
from ui import ThemeColor


CONTENT = Rect(12, 64, 776, 364)
FONT = "JetBrainsMono 8pt"


class ActionRef(Enum):
    ROOT = "filament.action.root"
    STATUS = "filament.action.status"
    TEMPERATURE = "filament.action.temperature"
    MATERIAL = "filament.action.material"
    STATE = "filament.action.state"
    ACTIONS = "filament.action.actions"
    LOAD = "filament.action.load"
    UNLOAD = "filament.action.unload"
    PURGE = "filament.action.purge"
    FINISH = "filament.action.finish"


def _temperature(current, target):
    return "%.0f / %.0fC" % (current, target)


def _status_color(ready, cooling):
    if ready:
        return ThemeColor.PRIMARY
    return ThemeColor.PRIMARY if cooling else ThemeColor.WARNING


def _button_state(ready):
    return "enabled" if ready else "disabled"


def _status_label(ready, cooling):
    if ready:
        return "READY"
    return "COOLING" if cooling else "HEATING"


def _instruction_top(ready, cooling):
    if ready:
        return "TEMPERATURE STABLE"
    return "COOLING TO TARGET" if cooling else "HEATING TO TARGET"


def _instruction_bottom(ready, cooling):
    return "SELECT AN ACTION" if ready else "PLEASE WAIT..."


def _status_card():
    ready = bind(FilamentState.READY)
    cooling = bind(FilamentState.COOLING)
    color = derived(_status_color, ready, cooling)
    content = Column(
        Text("NOZZLE").style(UiStyle.DIM_LEFT).height(22),
        Text(
            derived(
                _temperature, bind(FilamentState.TEMPERATURE),
                bind(FilamentState.TARGET)),
            color=color, font="Roboto Bold 18pt",
            max_width=240, truncate=True,
        ).height(66).ref(ActionRef.TEMPERATURE),
        Metric(
            "MATERIAL", bind(FilamentState.MATERIAL),
            label_color=ThemeColor.DIM, value_color=ThemeColor.TEXT,
        ).height(34).ref(ActionRef.MATERIAL),
        Fill(ThemeColor.BORDER).height(1),
        Spacer().grow(2),
        Text(
            derived(_status_label, ready, cooling), color=color,
            font="JetBrainsMono Bold 12pt", horizontal="left",
        ).height(38).ref(ActionRef.STATE),
        Spacer(),
        Column(
            Text(derived(_instruction_top, ready, cooling)).style(UiStyle.DIM_LEFT),
            Text(derived(_instruction_bottom, ready, cooling)).style(UiStyle.DIM_LEFT),
            gap=4,
        ).height(54),
    ).padding(left=20, top=18, right=20, bottom=18)
    return Frame(
        content, border=color, background=ThemeColor.PANEL, line_width=2,
    ).ref(ActionRef.STATUS).repaint_boundary()


def _action_button(action, label, subtitle, ref):
    return Button(
        action, label,
        state=derived(_button_state, bind(FilamentState.READY)),
        subtitle=subtitle,
        layout="row", subtitle_font=FONT,
    ).style(UiStyle.BUTTON_BOLD_12).height(76).ref(ref)


def create_page(from_pause=False):
    actions = Column(
        _action_button(LOAD, "01  LOAD", "FEED FILAMENT", ActionRef.LOAD),
        _action_button(
            UNLOAD, "02  UNLOAD", "RETRACT FILAMENT", ActionRef.UNLOAD),
        _action_button(
            PURGE, "03  PURGE", "CLEAR THE NOZZLE", ActionRef.PURGE),
        gap=16,
    ).height(260).ref(ActionRef.ACTIONS)
    finish = Button(
        RESUME if from_pause else DONE,
        "CONTINUE PRINT" if from_pause else "DONE",
        state="selected",
    ).style(UiStyle.BUTTON_BOLD_12).height(54).ref(ActionRef.FINISH)
    right = Column(actions, Spacer(), finish)
    root = Row(
        _status_card().width(280), right, gap=20,
    ).padding(8).ref(ActionRef.ROOT)
    return PageTree(root, CONTENT, page_id=AppPage.FILAMENT_ACTION, styles=UI_STYLES)


# Default declaration for framework page discovery; runtime factories retain their inputs.
PAGE = create_page()
