# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
# This file may be distributed under the terms of the GNU GPLv3 license.

from copy import deepcopy

from ui import FLEX, Button, Column, Equal, Flex, Frame, Grid, PageTree, Rect, Text, ThemeColor, ThemeRole, When
from ui.actions import Back
from ui.bindings import bind, derived

from ..calibration_layout import SIDE_INSET
from ..keys import AppPage
from .actions import ShaperAction
from .page import FONT, ShaperRef, axes, chart, scale_button, x_ticks
from .state import ShaperState


BOUNDS = Rect(0, 0, 800, 480)
FRAME_INSET = 8
CONTENT_INSET = SIDE_INSET - FRAME_INSET

header = Frame(Grid(matrix=((
    Button(Back(), "BACK", font="JetBrainsMono Bold 8pt"),
    Grid(matrix=((
        Button(ShaperAction("step", "prev"), "<", font=FONT),
        Text(bind(ShaperState.METHOD_LABEL), font=FONT, color=ThemeRole.HEADER_TEXT),
        Button(ShaperAction("step", "next"), ">", font=FONT),
    ),), columns=(64, FLEX, 64), rows=(FLEX,), gap=8),
    scale_button(),
    axes(),
),), columns=(Flex(4), Flex(15), 112, Flex(5)), rows=(FLEX,), gap=12)
    .padding(left=CONTENT_INSET, right=CONTENT_INSET),
    background=ThemeRole.HEADER_BACKGROUND, border=ThemeRole.HEADER_BORDER).height(48)

legend = Grid(matrix=((
    Text("MEASURED", font=FONT, color=ThemeColor.PRIMARY, horizontal="left"),
    Text(bind(ShaperState.SCALE_TITLE), font=FONT, color=ThemeColor.DIM),
    Text("AFTER SHAPER", font=FONT, color=ThemeColor.SECONDARY, horizontal="right"),
),), columns=Equal(3), rows=(FLEX,)).height(24).padding(left=48).ref(ShaperRef.LEGEND)

metrics = Grid(matrix=((
    Text(bind(ShaperState.METRICS), font=FONT, color=ThemeColor.TEXT, horizontal="left"),
    Text(derived(lambda accel: "MAX " + accel, bind(ShaperState.ACCEL)),
         font=FONT, color=ThemeColor.DIM, horizontal="right"),
),), columns=Equal(2), rows=(FLEX,)).height(24).padding(left=48).ref(ShaperRef.METRICS)

body = Column(
    deepcopy(chart), deepcopy(x_ticks), legend, metrics,
    When(bind(ShaperState.REFERENCE_LABEL),
         Text(bind(ShaperState.REFERENCE_LABEL), font=FONT, color=ThemeColor.DIM,
              horizontal="left", truncate=True).padding(left=48)).height(22),
).padding(left=CONTENT_INSET, right=CONTENT_INSET, top=12, bottom=10)

root = Frame(Column(header, body), border=ThemeRole.HEADER_BORDER,
             background=ThemeColor.BACKGROUND).padding(FRAME_INSET).ref(ShaperRef.ROOT)


def create_page(bounds=BOUNDS):
    page = PageTree(deepcopy(root), bounds, page_id=AppPage.SHAPER_FULLSCREEN, state_schema=tuple(ShaperState))
    # This page owns its header controls and uses the status strip for the chart.
    page.title = None
    page.show_back = False
    page.covers_footer = True
    return page


# Designer discovers the module's page; every result view creates its own tree.
PAGE = create_page()
