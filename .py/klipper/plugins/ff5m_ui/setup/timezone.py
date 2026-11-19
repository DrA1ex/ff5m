## Time zone picker: regions, letter ranges, cities and fixed UTC offsets.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from ui import Command, FLEX, ThemeColor
from ui.actions import ItemCommand
from ui.bindings import bind, derived, item
from ui.components import Button, PathText, Text
from ui.layout import Column, Grid, ListView, Overlay, PageTree, Row, Template, When

from ..keys import AppPage
from ..styles import UI_STYLES, UiStyle
from .common import (
    ACTION_HEIGHT, CONTENT, FIXED_UTC_COLUMNS, ROW_GAP, ROW_HEIGHT, TEXT_FONT, TITLE_FONT, page_buttons, page_root,
)
from .state import SetupCommand, TimezoneState


def _cell(column, city, font):
    zone = item("zone%d" % column)
    label = item("label%d" % column)
    state = item("state%d" % column)
    action = ItemCommand(SetupCommand.ZONE, zone)
    if city:
        tile = Button(action, label, state=state, font=font,
                      subtitle=item("note%d" % column), subtitle_font=TEXT_FONT)
    else:
        tile = Button(action, label, state=state, font=font)
    return When(zone, tile).grow()


def _grid(kind, columns, height, font=TITLE_FONT):
    return Template(kind, Row(*(_cell(i, kind == "cities", font) for i in range(columns)),
                              gap=ROW_GAP).height(height))


def _fixed_grid():
    rows = derived(lambda fixed, rows: rows if fixed else [], bind(TimezoneState.FIXED), bind(TimezoneState.ROWS))
    # The compact numerals leave room for the button's text padding at narrower widths.
    return ListView(rows, item("id"), item("kind"),
                    (_grid("grid7", FIXED_UTC_COLUMNS, 44, "Roboto Bold 8pt"),),
                    reuse_items=True, gap=6).height("content").align(vertical="top")


def _footer():
    current = Column(
        Text(bind(TimezoneState.CURRENT_CAPTION)).style(UiStyle.DIM_LEFT).height(20),
        PathText(bind(TimezoneState.CURRENT_NAME), separator="/", trim="",
                 color=ThemeColor.SECONDARY, font="JetBrainsMono Bold 10pt", horizontal="left").height(28),
        gap=2).height("content").grow().align(vertical="center")
    controls = ListView(
        bind(TimezoneState.ACTIONS), item("id"), item("id"),
        (Template("pager", page_buttons(item("counter"), item("previous"), item("next")).width(180)),
         Template("fixed", Button(Command(SetupCommand.ZONE, "fixed"), "FIXED UTC OFFSET")
                  .style(UiStyle.BUTTON_BOLD_8).width(244))),
        direction="horizontal", gap=12).width("content")
    return Row(current, controls, gap=12)


PAGE = PageTree(
    Overlay(
        When(derived(lambda fixed: not fixed, bind(TimezoneState.FIXED)), page_root(
            ListView(derived(lambda fixed, rows: [] if fixed else rows,
                             bind(TimezoneState.FIXED), bind(TimezoneState.ROWS)), item("id"), item("kind"),
                     (_grid("grid3", 3, ROW_HEIGHT), _grid("cities", 3, ROW_HEIGHT)),
                     reuse_items=True, gap=ROW_GAP).height("content").align(vertical="top"),
            _footer())),
        When(bind(TimezoneState.FIXED), Grid(matrix=((_fixed_grid(),), (_footer(),)), columns=(FLEX,),
                                           rows=(FLEX, ACTION_HEIGHT), gap=8)
             .padding(left=25, right=25, top=12, bottom=12)),
    ),
    CONTENT, page_id=AppPage.SETUP_TIMEZONE, styles=UI_STYLES,
    state_schema=tuple(TimezoneState),
)
