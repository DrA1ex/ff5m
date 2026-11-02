# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
# This file may be distributed under the terms of the GNU GPLv3 license.

from copy import deepcopy
from enum import Enum
from functools import partial

from ui import FLEX, Button, Column, Equal, Flex, Grid, PageTree, Rect, Spacer, Text, ThemeColor
from ui.bindings import bind, derived

from ..keys import AppPage
from .actions import ShaperAction
from .components import SpectrumPlot
from .state import ShaperState

CONTENT = Rect(0, 56, 800, 386)
FONT = "JetBrainsMono 8pt"


class ShaperRef(Enum):
    ROOT = "shaper.root"
    TOOLBAR = "shaper.toolbar"
    LEGEND = "shaper.legend"
    MEASURED = "shaper.legend.measured"
    SCALE_TITLE = "shaper.legend.scale"
    AFTER = "shaper.legend.after"
    PLOT = "shaper.plot"
    Y_TICKS = "shaper.y_ticks"
    X_TICKS = "shaper.x_ticks"
    METRICS = "shaper.metrics"
    APPLY = "shaper.apply"
    SAVE = "shaper.save"


def _axis_state(current, enabled, axis):
    return ("selected" if current == axis else "enabled") if enabled else "disabled"


def axes():
    return Grid(matrix=((
        Button(ShaperAction("axis", "x"), "X", font=FONT,
               state=derived(partial(_axis_state, axis="x"), bind(ShaperState.AXIS), bind(ShaperState.X_ENABLED))),
        Button(ShaperAction("axis", "y"), "Y", font=FONT,
               state=derived(partial(_axis_state, axis="y"), bind(ShaperState.AXIS), bind(ShaperState.Y_ENABLED))),
    ),), columns=Equal(2), rows=(FLEX,), gap=12)


def apply_button():
    return Button(ShaperAction("apply"), derived(
        lambda axis, selected: ("SELECTED " if selected else "USE ") + axis.upper(),
        bind(ShaperState.AXIS), bind(ShaperState.IS_SELECTED)),
        font=FONT, state=derived(lambda enabled, selected: ("selected" if selected else "enabled")
                                if enabled else "disabled",
                                bind(ShaperState.CAN_APPLY), bind(ShaperState.IS_SELECTED))).ref(ShaperRef.APPLY)


def save_button():
    return Button(ShaperAction("save"), bind(ShaperState.SAVE_LABEL), font=FONT,
                  state=derived(lambda staged: "warning" if staged else "disabled",
                                bind(ShaperState.CAN_SAVE))).ref(ShaperRef.SAVE)


toolbar = Grid(matrix=((
    axes(),
    Grid(matrix=((
        Button(ShaperAction("step", "prev"), "<", font=FONT),
        Text(bind(ShaperState.METHOD_LABEL), color=ThemeColor.SECONDARY, font=FONT, truncate=True),
        Button(ShaperAction("step", "next"), ">", font=FONT),
    ),), columns=(48, FLEX, 48), rows=(FLEX,), gap=8),
    Button(ShaperAction("scale"), derived(lambda scale: "LOG" if scale == "log" else "LINEAR",
                                          bind(ShaperState.SCALE)), font=FONT),
    Button(ShaperAction("view", "table"), "LIST", font=FONT),
),), columns=(Flex(6), Flex(15), Flex(5), Flex(5)), rows=(FLEX,), gap=12).height(48).ref(ShaperRef.TOOLBAR)

legend = Column(Grid(matrix=((
    Text("MEASURED", font=FONT, color=ThemeColor.PRIMARY, horizontal="left").ref(ShaperRef.MEASURED),
    Text(bind(ShaperState.SCALE_TITLE), font=FONT, color=ThemeColor.TEXT).ref(ShaperRef.SCALE_TITLE),
    Text("AFTER SHAPER", font=FONT, color=ThemeColor.SECONDARY, horizontal="right").ref(ShaperRef.AFTER),
),), columns=Equal(3), rows=(FLEX,)).height(24),
    Text(bind(ShaperState.REFERENCE_LABEL), font=FONT, color=ThemeColor.DIM,
         horizontal="left", truncate=True).height(20).ref("shaper.legend.reference"),
).height(44).padding(left=48).ref(ShaperRef.LEGEND)

y_ticks = Column(
    Text(bind(ShaperState.TICK_HIGH), font=FONT, color=ThemeColor.DIM, horizontal="right").height(24),
    Spacer(),
    Text(bind(ShaperState.TICK_MIDDLE), font=FONT, color=ThemeColor.DIM, horizontal="right").height(24),
    Spacer(),
    Text(bind(ShaperState.TICK_LOW), font=FONT, color=ThemeColor.DIM, horizontal="right").height(24),
).padding(right=8).ref(ShaperRef.Y_TICKS)

chart = Grid(matrix=((
    y_ticks,
    SpectrumPlot(bind(ShaperState.RESULT), bind(ShaperState.METHOD), bind(ShaperState.SCALE),
                 before_color=ThemeColor.PRIMARY, after_color=ThemeColor.SECONDARY, line_width=2,
                 reference=bind(ShaperState.REFERENCE))
        .margin(top=12, bottom=12).ref(ShaperRef.PLOT),
),), columns=(48, FLEX), rows=(FLEX,))

x_ticks = Grid(matrix=((
    Text("0", font=FONT, color=ThemeColor.DIM, horizontal="left"),
    Text("50", font=FONT, color=ThemeColor.DIM),
    Text("100", font=FONT, color=ThemeColor.DIM),
    Text("150", font=FONT, color=ThemeColor.DIM),
    Text("200 Hz", font=FONT, color=ThemeColor.DIM, horizontal="right"),
),), columns=(Flex(1), Flex(2), Flex(2), Flex(2), Flex(1)), rows=(FLEX,)) \
    .height(22).padding(left=48).ref(ShaperRef.X_TICKS)

metrics = Column(
    Text(bind(ShaperState.METRICS), font=FONT, color=ThemeColor.TEXT, horizontal="left", truncate=True),
    Text(derived(lambda accel, enabled: accel if enabled else "REFERENCE ONLY / unavailable",
                 bind(ShaperState.ACCEL), bind(ShaperState.CAN_APPLY)),
         font=FONT, color=ThemeColor.DIM, horizontal="left", truncate=True),
).ref(ShaperRef.METRICS)

root = Grid(matrix=((toolbar,), (Column(legend, chart, x_ticks),),
                    (Grid(matrix=((metrics, apply_button(), save_button()),),
                          columns=(Flex(12), Flex(5), Flex(5)), rows=(FLEX,), gap=12),)),
            columns=(FLEX,), rows=(48, FLEX, 48), gap=12) \
    .padding(left=24, right=24, top=18, bottom=18).ref(ShaperRef.ROOT)

PAGE = PageTree(root, CONTENT, page_id=AppPage.SHAPER_RESULT, state_schema=tuple(ShaperState))
PAGE.title = "Input shaper"
PAGE.show_back = True


def create_page(bounds=CONTENT):
    page = PageTree(deepcopy(PAGE.root), bounds, page_id=AppPage.SHAPER_RESULT, state_schema=tuple(ShaperState))
    page.title, page.show_back = PAGE.title, PAGE.show_back
    return page
