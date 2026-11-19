## Welcome, network, options and calibration screens for printer setup.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from ui import Command, ThemeColor
from ui.bindings import bind, item
from ui.components import Button, Text
from ui.layout import Column, ListView, Overlay, PageTree, Row, Spacer, When

from ..keys import AppPage
from ..styles import UI_STYLES, UiStyle
from .common import CONTENT, ROW_GAP, calibration_card, option_row, page_controls, page_root, summary_row
from .state import CalibrationState, HubState, OptionGroupsState, OptionsState, SetupCommand


def _rows(binding, templates):
    return ListView(binding, item("id"), item("kind"), templates,
                    reuse_items=True, gap=ROW_GAP).height("content").align(vertical="top")


def _actions(secondary, label, state="selected"):
    return Row(secondary, Button(Command(SetupCommand.ACTION), label, state=state).style(UiStyle.BUTTON_BOLD_11),
               gap=12)


WELCOME_PAGE = PageTree(
    page_root(
        Column(
            Text("Get ready for your first print", font="Roboto Bold 18pt", color=ThemeColor.PRIMARY,
                 wrap=True, auto_height=True).height("content"),
            Text("This wizard will help you set up and calibrate your printer.",
                 wrap=True, auto_height=True).style(UiStyle.TEXT).height("content"),
            gap=16,
        ).height("content").align(vertical="center"),
        _actions(Button(Command(SetupCommand.LEAVE), "I'LL SET IT UP MYSELF").style(UiStyle.BUTTON_BOLD_11), "START SETUP"),
    ),
    CONTENT, page_id=AppPage.SETUP_WELCOME, styles=UI_STYLES,
)


HUB_PAGE = PageTree(
    page_root(
        _rows(bind(HubState.ROWS),
              tuple(summary_row(kind) for kind in ("done", "plain", "advised", "optional", "required"))),
        _actions(When(bind(HubState.EXIT),
                      Button(Command(SetupCommand.LEAVE), bind(HubState.EXIT)).style(UiStyle.BUTTON_BOLD_11)),
                 bind(HubState.ACTION), bind(HubState.ACTION_STATE)),
    ),
    CONTENT, page_id=AppPage.SETUP, styles=UI_STYLES, state_schema=tuple(HubState),
)


OPTION_GROUPS_PAGE = PageTree(
    page_root(
        _rows(bind(OptionGroupsState.ROWS), (summary_row("group"),)),
        _actions(page_controls(OptionGroupsState), bind(OptionGroupsState.ACTION)),
    ),
    CONTENT, page_id=AppPage.SETUP_OPTION_GROUPS, styles=UI_STYLES, state_schema=tuple(OptionGroupsState),
)


OPTIONS_PAGE = PageTree(
    page_root(
        Overlay(
            _rows(bind(OptionsState.ROWS),
                  tuple(option_row(kind) for kind in ("toggle", "toggle-advised", "value", "value-advised"))),
            When(bind(OptionsState.EMPTY_MESSAGE), Text(bind(OptionsState.EMPTY_MESSAGE), wrap=True).style(UiStyle.TEXT)),
        ),
        _actions(page_controls(OptionsState), bind(OptionsState.ACTION)),
    ),
    CONTENT, page_id=AppPage.SETUP_OPTIONS, styles=UI_STYLES, state_schema=tuple(OptionsState),
)


CALIBRATION_PAGE = PageTree(
    page_root(
        _rows(bind(CalibrationState.ROWS),
              tuple(calibration_card(kind) for kind in ("advised", "optional", "blocked", "done", "required"))),
        _actions(Spacer(), bind(CalibrationState.ACTION), bind(CalibrationState.ACTION_STATE)),
    ),
    CONTENT, page_id=AppPage.SETUP_CALIBRATION, styles=UI_STYLES, state_schema=tuple(CalibrationState),
)
