## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from enum import Enum

from ui import (
    FLEX, Cell, Column, Equal, Flex, Frame, Grid, Overlay, PageTree, Rect,
    Row, Spacer, StateCase, Stroke, Text, ThemeColor,
)
from ui.bindings import bind, derived
from ui.components import Button

from ..keys import AppPage
from .actions import REPEAT, DONE
from .components import BOTTOM_SCREW, TOP_SCREW
from .graphics import BedHardware
from .state import ScrewResultState, adjustment


PAGE_TITLE = "Calibration result"
PAGE_BOUNDS = Rect(0, 56, 800, 386)
FONT = "JetBrainsMono 7pt"


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



rear_left_direction = bind(ScrewResultState.REAR_LEFT_DIRECTION)
rear_left_base = derived(lambda value: value == "BASE", rear_left_direction)
rear_left = TOP_SCREW(
    instance_key=ScrewResultRef.REAR_LEFT,
    name=bind(ScrewResultState.REAR_LEFT_NAME),
    direction=rear_left_direction,
    not_base=derived(lambda value: not value, rear_left_base),
    adjustment=derived(
        adjustment, rear_left_direction, bind(ScrewResultState.REAR_LEFT_MINUTES)),
    align="left",
    card_ref=ScrewResultRef.REAR_LEFT,
    value_ref=ScrewResultRef.REAR_LEFT_VALUE,
)

rear_right_direction = bind(ScrewResultState.REAR_RIGHT_DIRECTION)
rear_right_base = derived(lambda value: value == "BASE", rear_right_direction)
rear_right = TOP_SCREW(
    instance_key=ScrewResultRef.REAR_RIGHT,
    name=bind(ScrewResultState.REAR_RIGHT_NAME),
    direction=rear_right_direction,
    not_base=derived(lambda value: not value, rear_right_base),
    adjustment=derived(
        adjustment, rear_right_direction, bind(ScrewResultState.REAR_RIGHT_MINUTES)),
    align="right",
    card_ref=ScrewResultRef.REAR_RIGHT,
    value_ref=ScrewResultRef.REAR_RIGHT_VALUE,
)

front_left_direction = bind(ScrewResultState.FRONT_LEFT_DIRECTION)
front_left_base = derived(lambda value: value == "BASE", front_left_direction)
front_left = BOTTOM_SCREW(
    instance_key=ScrewResultRef.FRONT_LEFT,
    name=bind(ScrewResultState.FRONT_LEFT_NAME),
    direction=front_left_direction,
    not_base=derived(lambda value: not value, front_left_base),
    adjustment=derived(
        adjustment, front_left_direction, bind(ScrewResultState.FRONT_LEFT_MINUTES)),
    align="left",
    card_ref=ScrewResultRef.FRONT_LEFT,
    value_ref=ScrewResultRef.FRONT_LEFT_VALUE,
)

front_right_direction = bind(ScrewResultState.FRONT_RIGHT_DIRECTION)
front_right_base = derived(lambda value: value == "BASE", front_right_direction)
front_right = BOTTOM_SCREW(
    instance_key=ScrewResultRef.FRONT_RIGHT,
    name=bind(ScrewResultState.FRONT_RIGHT_NAME),
    direction=front_right_direction,
    not_base=derived(lambda value: not value, front_right_base),
    adjustment=derived(
        adjustment, front_right_direction, bind(ScrewResultState.FRONT_RIGHT_MINUTES)),
    align="right",
    card_ref=ScrewResultRef.FRONT_RIGHT,
    value_ref=ScrewResultRef.FRONT_RIGHT_VALUE,
)


visual = Grid(
    cells=(
        Cell(rear_left, 0, 0),
        Cell(bed, 1, 0, row_span=2),
        Cell(rear_right, 2, 0),
        Cell(front_left, 0, 1),
        Cell(front_right, 2, 1),
    ),
    columns=(Flex(5), Flex(12), Flex(5)),
    rows=Equal(2),
    gap=0,
).padding(left=13, right=13)
legend = Column(
    Stroke(ThemeColor.BORDER, line_width=1).height(1),
    legend_rows,
    gap=0,
).height(63).padding(left=15, right=15).ref(ScrewResultRef.LEGEND)
diagram = Frame(
    Grid(
        matrix=((visual,), (legend,)),
        columns=(FLEX,), rows=(FLEX, 63), gap=0,
    ),
    border=ThemeColor.BORDER,
    background=ThemeColor.BACKGROUND,
    line_width=1,
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
PAGE = PageTree(
    root, PAGE_BOUNDS, page_id=AppPage.CALIBRATION_SCREWS_RESULT,
    component_templates=(TOP_SCREW, BOTTOM_SCREW,))
PAGE.title = PAGE_TITLE
PAGE.show_back = False
