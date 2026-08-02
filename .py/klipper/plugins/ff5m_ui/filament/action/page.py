"""Declarative filament load, unload, and purge page."""

from enum import Enum

from ui.bindings import bind, derived
from ui.components import Button, Fill, Metric, Panel, Text
from ui.layout import Column, Overlay, PageTree, Rect, Row, Spacer
from ...keys import AppPage
from ..actions import DONE, LOAD, PURGE, RESUME, UNLOAD
from ..state import FilamentState


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
        return "00f0f0"
    return "35d9e6" if cooling else "ffb000"


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
        Text("NOZZLE", color="56656c", font=FONT,
             horizontal="left").height(22),
        Text(
            derived(
                _temperature, bind(FilamentState.TEMPERATURE),
                bind(FilamentState.TARGET)),
            color=color, font="Roboto Bold 18pt",
            max_width=240, truncate=True,
        ).height(66).ref(ActionRef.TEMPERATURE),
        Metric(
            "MATERIAL", bind(FilamentState.MATERIAL),
            label_color="56656c", value_color="d9e4e8",
        ).height(34).ref(ActionRef.MATERIAL),
        Fill("295c66").height(1),
        Spacer().grow(2),
        Text(
            derived(_status_label, ready, cooling), color=color,
            font="JetBrainsMono Bold 12pt", horizontal="left",
        ).height(38).ref(ActionRef.STATE),
        Spacer(),
        Column(
            Text(derived(_instruction_top, ready, cooling),
                 color="56656c", font=FONT,
                 horizontal="left"),
            Text(derived(_instruction_bottom, ready, cooling),
                 color="56656c", font=FONT,
                 horizontal="left"),
            gap=4,
        ).height(54),
    ).padding(left=20, top=18, right=20, bottom=18)
    return Overlay(
        Panel(border=color, background="050c0f", line_width=2),
        content,
    ).ref(ActionRef.STATUS).repaint_boundary()


def _action_button(action, label, subtitle, ref):
    return Button(
        action, label,
        state=derived(_button_state, bind(FilamentState.READY)),
        font="JetBrainsMono Bold 12pt", subtitle=subtitle,
        layout="row", subtitle_font=FONT,
    ).height(76).ref(ref)


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
        state="selected", font="JetBrainsMono Bold 12pt",
    ).height(54).ref(ActionRef.FINISH)
    right = Column(actions, Spacer(), finish)
    root = Row(
        _status_card().width(280), right, gap=20,
    ).padding(8).ref(ActionRef.ROOT)
    return PageTree(root, CONTENT, page_id=AppPage.FILAMENT_ACTION)
