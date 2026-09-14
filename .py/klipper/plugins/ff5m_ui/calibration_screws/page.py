## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from enum import Enum

from ui import (
    EMPTY, FLEX, Column, Equal, Flex, Grid, Overlay, PageTree, Panel, Rect,
    Row, Spacer, Span, StateCase, Stroke, Text, ThemeColor,
)
from ui.bindings import bind, derived
from ui.components import Button

from ..keys import AppPage
from .actions import REPEAT, DONE
from .graphics import BedHardware
from .state import ScrewResultState, adjustment


PAGE_TITLE = "Calibration result"
PAGE_BOUNDS = Rect(0, 56, 800, 386)
FONT = "JetBrainsMono 7pt"
LABEL_FONT = "Roboto 8pt"
VALUE_FONT = "Roboto Bold 12pt"


class ScrewResultRef(Enum):
    ROOT = "screws.root"
    DIAGRAM = "screws.diagram"
    BED = "screws.bed"
    FRONT_LEFT = "screws.front_left"
    FRONT_RIGHT = "screws.front_right"
    REAR_RIGHT = "screws.rear_right"
    REAR_LEFT = "screws.rear_left"
    REAR_LEFT_VALUE = "screws.rear_left.value"
    REAR_RIGHT_VALUE = "screws.rear_right.value"
    FRONT_LEFT_VALUE = "screws.front_left.value"
    FRONT_RIGHT_VALUE = "screws.front_right.value"
    LEGEND = "screws.legend"
    REPEAT = "screws.repeat"
    DONE = "screws.done"


orientation = Column(
    Text("REAR", color=ThemeColor.DIM, font=FONT).height(20),
    Spacer(),
    Text("FRONT", color=ThemeColor.DIM, font=FONT).height(20),
    gap=0,
).padding(top=46, bottom=84)
instruction = Text(
    "ADJUST FROM BELOW", color=ThemeColor.WARNING,
    font="Roboto Bold 10pt",
).margin(top=36, bottom=72)
bed = Overlay(
    BedHardware(
        front_left=bind(ScrewResultState.FRONT_LEFT_DIRECTION),
        front_right=bind(ScrewResultState.FRONT_RIGHT_DIRECTION),
        rear_right=bind(ScrewResultState.REAR_RIGHT_DIRECTION),
        rear_left=bind(ScrewResultState.REAR_LEFT_DIRECTION)),
    orientation, instruction,
).allow_overflow().ref(ScrewResultRef.BED)


compact_legend = Column(
    Text("CW = CLOCKWISE   CCW = COUNTERCLOCKWISE",
         color=ThemeColor.TEXT, font=FONT),
    Text("TURNS:CLOCK MINUTES", color=ThemeColor.WARNING, font=FONT),
    Text("00:15 = QUARTER   00:30 = HALF   01:00 = FULL TURN",
         color=ThemeColor.TEXT, font=FONT),
    gap=0,
)
legend_rows = Column(
    Row(
        Text("CW = CLOCKWISE", color=ThemeColor.TEXT, font=FONT).width("content"),
        Text("CCW = COUNTERCLOCKWISE", color=ThemeColor.TEXT, font=FONT).width("content"),
        Text("TURNS:CLOCK MINUTES", color=ThemeColor.WARNING,
             font="JetBrainsMono Bold 7pt").width("content"),
        gap=20,
    ).width("content").align(horizontal="center"),
    Row(
        Text("00:15 = QUARTER TURN", color=ThemeColor.TEXT, font=FONT).width("content"),
        Text("00:30 = HALF TURN", color=ThemeColor.TEXT, font=FONT).width("content"),
        Text("01:00 = FULL TURN", color=ThemeColor.TEXT, font=FONT).width("content"),
        gap=20,
    ).width("content").align(horizontal="center"),
    gap=0,
).padding(top=5)


rear_left = Column(
    Column(
        Text(bind(ScrewResultState.REAR_LEFT_NAME),
             color=ThemeColor.BRIGHT, font=LABEL_FONT, horizontal="left",
             truncate=True).height(20),
        Overlay(
            StateCase(
                Text("BASE (REFERENCE)", color=ThemeColor.DIM,
                     font=LABEL_FONT, horizontal="left", truncate=True),
                selector=bind(ScrewResultState.REAR_LEFT_DIRECTION),
                expected="BASE"),
            StateCase(
                Text(derived(adjustment, bind(ScrewResultState.REAR_LEFT_DIRECTION),
                             bind(ScrewResultState.REAR_LEFT_MINUTES)),
                     color=ThemeColor.PRIMARY,
                     font=VALUE_FONT,
                     horizontal="left", truncate=True).ref(ScrewResultRef.REAR_LEFT_VALUE),
                selector=derived(lambda direction: direction == "BASE",
                                 bind(ScrewResultState.REAR_LEFT_DIRECTION)),
                expected=False),
        ).height(30),
        gap=0,
    ).height(50),
    Spacer(),
    gap=0,
).padding(top=22).ref(ScrewResultRef.REAR_LEFT)

rear_right = Column(
    Column(
        Text(bind(ScrewResultState.REAR_RIGHT_NAME),
             color=ThemeColor.BRIGHT, font=LABEL_FONT, horizontal="right",
             truncate=True).height(20),
        Overlay(
            StateCase(
                Text("BASE (REFERENCE)", color=ThemeColor.DIM,
                     font=LABEL_FONT, horizontal="right", truncate=True),
                selector=bind(ScrewResultState.REAR_RIGHT_DIRECTION),
                expected="BASE"),
            StateCase(
                Text(derived(adjustment, bind(ScrewResultState.REAR_RIGHT_DIRECTION),
                             bind(ScrewResultState.REAR_RIGHT_MINUTES)),
                     color=ThemeColor.PRIMARY,
                     font=VALUE_FONT,
                     horizontal="right", truncate=True).ref(ScrewResultRef.REAR_RIGHT_VALUE),
                selector=derived(lambda direction: direction == "BASE",
                                 bind(ScrewResultState.REAR_RIGHT_DIRECTION)),
                expected=False),
        ).height(30),
        gap=0,
    ).height(50),
    Spacer(),
    gap=0,
).padding(top=22).ref(ScrewResultRef.REAR_RIGHT)

front_left = Column(
    Spacer(),
    Column(
        Text(bind(ScrewResultState.FRONT_LEFT_NAME),
             color=ThemeColor.BRIGHT, font=LABEL_FONT, horizontal="left",
             truncate=True).height(20),
        Overlay(
            StateCase(
                Text("BASE (REFERENCE)", color=ThemeColor.DIM,
                     font=LABEL_FONT, horizontal="left", truncate=True),
                selector=bind(ScrewResultState.FRONT_LEFT_DIRECTION),
                expected="BASE"),
            StateCase(
                Text(derived(adjustment, bind(ScrewResultState.FRONT_LEFT_DIRECTION),
                             bind(ScrewResultState.FRONT_LEFT_MINUTES)),
                     color=ThemeColor.PRIMARY,
                     font=VALUE_FONT,
                     horizontal="left", truncate=True).ref(ScrewResultRef.FRONT_LEFT_VALUE),
                selector=derived(lambda direction: direction == "BASE",
                                 bind(ScrewResultState.FRONT_LEFT_DIRECTION)),
                expected=False),
        ).height(30),
        gap=0,
    ).height(50),
    gap=0,
).padding(bottom=19).ref(ScrewResultRef.FRONT_LEFT)

front_right = Column(
    Spacer(),
    Column(
        Text(bind(ScrewResultState.FRONT_RIGHT_NAME),
             color=ThemeColor.BRIGHT, font=LABEL_FONT, horizontal="right",
             truncate=True).height(20),
        Overlay(
            StateCase(
                Text("BASE (REFERENCE)", color=ThemeColor.DIM,
                     font=LABEL_FONT, horizontal="right", truncate=True),
                selector=bind(ScrewResultState.FRONT_RIGHT_DIRECTION),
                expected="BASE"),
            StateCase(
                Text(derived(adjustment, bind(ScrewResultState.FRONT_RIGHT_DIRECTION),
                             bind(ScrewResultState.FRONT_RIGHT_MINUTES)),
                     color=ThemeColor.PRIMARY,
                     font=VALUE_FONT,
                     horizontal="right", truncate=True).ref(ScrewResultRef.FRONT_RIGHT_VALUE),
                selector=derived(lambda direction: direction == "BASE",
                                 bind(ScrewResultState.FRONT_RIGHT_DIRECTION)),
                expected=False),
        ).height(30),
        gap=0,
    ).height(50),
    gap=0,
).padding(bottom=19).ref(ScrewResultRef.FRONT_RIGHT)

visual = Grid(
    matrix=(
        (rear_left,
         Span(bed, rows=2),
         rear_right),
        (front_left,
         EMPTY,
         front_right),
    ),
    columns=(Flex(5), Flex(12), Flex(5)), rows=Equal(2), gap=0,
).padding(left=13, right=13)
legend = Column(
    Stroke(ThemeColor.BORDER, line_width=1).height(1),
    legend_rows,
    gap=0,
).height(63).padding(left=15, right=15).ref(ScrewResultRef.LEGEND)
diagram = Overlay(
    Panel(
        border=ThemeColor.BORDER, background=ThemeColor.BACKGROUND,
        line_width=1),
    Grid(
        matrix=((visual,), (legend,)),
        columns=(FLEX,), rows=(FLEX, 63), gap=0,
    ),
).ref(ScrewResultRef.DIAGRAM)


buttons = Grid(
    matrix=((
        Button(REPEAT, "REPEAT", font="JetBrainsMono 12pt")
        .ref(ScrewResultRef.REPEAT),
        Button(DONE, "DONE", font="JetBrainsMono 12pt")
        .ref(ScrewResultRef.DONE),
    ),),
    columns=Equal(2), rows=(FLEX,), gap=(40, 0),
).height(48)


root = Grid(
    matrix=((diagram,), (buttons,)),
    columns=(FLEX,), rows=(FLEX, 48), gap=(0, 10),
).padding(left=20, top=4, right=20, bottom=6) \
    .ref(ScrewResultRef.ROOT)
PAGE = PageTree(root, PAGE_BOUNDS, page_id=AppPage.CALIBRATION_SCREWS_RESULT)
PAGE.title = PAGE_TITLE
PAGE.show_back = False
