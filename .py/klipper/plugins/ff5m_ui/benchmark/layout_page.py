## Declarative component layout benchmark page.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from enum import Enum

from ui import (
    Button, Column, FLEX, Grid, Overlay, PageTree, Panel, Text,
    ThemeColor, WrapPanel,
)
from ui.bindings import bind, state
from ui.identity import StateKey

from ..keys import AppPage
from .actions import NEXT_MODE
from .page import CONTENT, STATS_WIDTH, stats_panel


DESCRIPTION = "Card width changes every frame. Text and buttons reflow."
ROWS = (
    "Quick setup controls",
    "Material profile changes",
    "Status text wraps here",
    "Recent actions scroll",
)


class LayoutState(StateKey):
    __key_namespace__ = "ui.pages.benchmark.layout.LayoutState"

    WIDTH = state(str, default="300 PX")
    DESCRIPTION = state(str, default=DESCRIPTION)
    ROW_ONE = state(str, default=ROWS[0])
    ROW_TWO = state(str, default=ROWS[1])
    ROW_THREE = state(str, default=ROWS[2])


class LayoutRef(Enum):
    ROOT = "component_benchmark.root"
    CARD = "component_benchmark.card"
    DESCRIPTION = "component_benchmark.description"
    ACTIONS = "component_benchmark.actions"
    ROW_ONE = "component_benchmark.row_one"
    ROW_TWO = "component_benchmark.row_two"
    ROW_THREE = "component_benchmark.row_three"


def _workload():
    content = Column(
        Text("LAYOUT REFLOW", color=ThemeColor.PRIMARY,
             font="JetBrainsMono Bold 10pt", horizontal="left").height(29),
        Text(bind(LayoutState.WIDTH), color=ThemeColor.SECONDARY,
             font="JetBrainsMono Bold 8pt", horizontal="left").height(23),
        Text(bind(LayoutState.DESCRIPTION), color=ThemeColor.TEXT,
             font="JetBrainsMono 8pt", horizontal="left", vertical="top",
             wrap=True, auto_height=True).ref(LayoutRef.DESCRIPTION),
        WrapPanel(
            Button(NEXT_MODE, "RESET RUN", font="JetBrainsMono 8pt"),
            Button(NEXT_MODE, "SAMPLE B", state="disabled",
                   font="JetBrainsMono 8pt"),
            Button(NEXT_MODE, "DETAILS", state="disabled",
                   font="JetBrainsMono 8pt"),
            min_item_width=122, item_height=42, horizontal_gap=8,
            vertical_gap=7,
        ).height(88).ref(LayoutRef.ACTIONS),
        Text(bind(LayoutState.ROW_ONE), color=ThemeColor.BRIGHT,
             font="JetBrainsMono 8pt", horizontal="left", vertical="top",
             wrap=True, auto_height=True).ref(LayoutRef.ROW_ONE),
        Text(bind(LayoutState.ROW_TWO), color=ThemeColor.TEXT,
             font="JetBrainsMono 8pt", horizontal="left", vertical="top",
             wrap=True, auto_height=True).ref(LayoutRef.ROW_TWO),
        Text(bind(LayoutState.ROW_THREE), color=ThemeColor.DIM,
             font="JetBrainsMono 8pt", horizontal="left", vertical="top",
             wrap=True, auto_height=True).ref(LayoutRef.ROW_THREE),
        gap=5,
    ).padding(left=14, top=10, right=14, bottom=10)

    return Overlay(
        Panel(border=ThemeColor.BORDER, background=ThemeColor.PANEL,
              line_width=1),
        content,
    ).width(300).repaint_boundary().ref(LayoutRef.CARD)


def create_page():
    root = Grid(
        matrix=((_workload(), stats_panel()),),
        columns=(FLEX, STATS_WIDTH), rows=(FLEX,), gap=10,
    ).padding(left=8, top=8, right=8, bottom=8).ref(LayoutRef.ROOT)
    page = PageTree(root, CONTENT, page_id=AppPage.COMPONENT_BENCHMARK)
    page.title = "Component benchmark"
    page.show_back = True
    return page


PAGE = create_page()
