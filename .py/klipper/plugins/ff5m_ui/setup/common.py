## Shared composition of the first-run setup pages: one card, one row, one grid.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from ui import Command, FLEX, ThemeColor
from ui.actions import ItemCommand
from ui.bindings import bind, derived, item
from ui.components import ArrowButton, Button, ButtonSurface, Panel, Text, ToggleSwitch
from ui.layout import Column, Grid, Overlay, Rect, Row, Template, When

from ..styles import UiStyle
from .state import SetupCommand


CONTENT = Rect(0, 56, 800, 386)
TITLE_FONT = "JetBrainsMono Bold 8pt"
TEXT_FONT = "JetBrainsMono 8pt"

ROW_HEIGHT = 64
CALIBRATION_HEIGHT = 88
ROW_GAP = 8
ACTION_HEIGHT = 54
CONTROL_HEIGHT = 44
OPTION_CAPACITY = 4
FIXED_UTC_COLUMNS = 7
FIXED_UTC_ROWS = 6

CHIP_COLORS = {
    "done": ThemeColor.SUCCESS, "advised": ThemeColor.WARNING,
    "optional": ThemeColor.DIM, "plain": ThemeColor.PRIMARY,
    "blocked": ThemeColor.DIM, "required": ThemeColor.DANGER,
}


def page_root(body, actions):
    return Grid(matrix=((body,), (actions,)), columns=(FLEX,), rows=(FLEX, ACTION_HEIGHT),
                gap=12).padding(left=25, right=25, top=20, bottom=20)


def page_controls(state):
    page, count = bind(state.PAGE), bind(state.PAGE_COUNT)
    counter = derived(lambda index, total: "%d / %d" % (index + 1, total), page, count)
    return When(derived(lambda total: total > 1, count),
                page_buttons(counter, bind(state.PREVIOUS), bind(state.NEXT)))


def page_buttons(counter, previous, next_state):
    return Row(
        ArrowButton(Command(SetupCommand.PREVIOUS), "up", state=previous).width(ACTION_HEIGHT),
        Text(counter).style(UiStyle.DIM),
        ArrowButton(Command(SetupCommand.NEXT), "down", state=next_state).width(ACTION_HEIGHT),
        gap=12)


def _surface(content, pressed=False):
    return Overlay(Panel(background=ThemeColor.PRESSED_BACKGROUND if pressed else ThemeColor.PANEL,
                         border=ThemeColor.BRIGHT if pressed else ThemeColor.BORDER, line_width=1), content)


def _line(field, color, font=TEXT_FONT):
    return Text(item(field), color=color, font=font, horizontal="left", truncate=True).height(22)


def _chip(kind, pressed=False):
    return Text(item("chip"), color=ThemeColor.BRIGHT if pressed else CHIP_COLORS[kind],
                font=TITLE_FONT, horizontal="right").width("content").align(vertical="center")


def _summary_face(kind, pressed):
    title = ThemeColor.BRIGHT if pressed else ThemeColor.PRIMARY
    text = ThemeColor.BRIGHT if pressed else ThemeColor.TEXT
    lines = Column(_line("title", title, TITLE_FONT), _line("status", text),
                   gap=2).height("content").grow().align(vertical="center")
    content = lines if kind == "group" else Row(lines, _chip(kind, pressed), gap=12)
    return _surface(content.padding(left=15, right=15), pressed)


def summary_row(kind):
    return Template(kind, ButtonSurface(
        ItemCommand(SetupCommand.OPEN, item("id")),
        _summary_face(kind, False), _summary_face(kind, True)).height(ROW_HEIGHT))


def _calibration_face(kind, pressed):
    title = ThemeColor.DIM if kind == "blocked" else ThemeColor.PRIMARY
    heading = Row(_line("title", ThemeColor.BRIGHT if pressed else title, TITLE_FONT).grow(),
                  _chip(kind, pressed), gap=12).height(22)
    lines = Column(heading, _line("status", ThemeColor.TEXT), _line("note", ThemeColor.DIM),
                   gap=2).height("content").align(vertical="center")
    return _surface(Overlay(lines).padding(left=15, right=15), pressed)


def calibration_card(kind):
    return Template(kind, ButtonSurface(
        ItemCommand(SetupCommand.OPEN, item("id")),
        _calibration_face(kind, False), _calibration_face(kind, True),
        enabled=item("enabled")).height(CALIBRATION_HEIGHT))


def option_row(kind):
    if kind.startswith("toggle"):
        control = ToggleSwitch(ItemCommand(SetupCommand.TOGGLE, item("id")),
                               item("active")).size(88, CONTROL_HEIGHT)
    else:
        control = Button(ItemCommand(SetupCommand.EDIT, item("id")), item("value"),
                         font=TEXT_FONT).size(150, CONTROL_HEIGHT)

    heading = Row(_line("title", ThemeColor.PRIMARY, TITLE_FONT).grow(),
                  _chip("advised" if kind.endswith("advised") else "optional"), gap=12).height(22)
    lines = Column(heading, _line("note", ThemeColor.TEXT), gap=2).height("content").grow().align(vertical="center")
    help_button = Button(ItemCommand(SetupCommand.HELP, item("id")), "?", font=TEXT_FONT).size(
        CONTROL_HEIGHT, CONTROL_HEIGHT)
    content = Row(lines, control.align(vertical="center"), help_button.align(vertical="center"),
                  gap=12).padding(left=15, right=15)
    return Template(kind, _surface(content).height(ROW_HEIGHT))
