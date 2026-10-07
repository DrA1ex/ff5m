## Declarative Z-offset paper briefing page for Feather.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from enum import Enum

from ui.bindings import bind, derived
from ui.components import Button, Text
from ui.layout import Column, Equal, Grid, PageTree as Page, Spacer
from ...keys import AppPage
from ...styles import UI_STYLES, UiStyle
from ..actions import ENTER_ZONE
from ..common import CONTENT
from .state import PaperBriefingState
from ui import ThemeColor


PAGE_ID = AppPage.Z_OFFSET_PAPER_BRIEFING


class PaperBriefingRef(Enum):
    ROOT = "paper_briefing.root"
    SPACER_BEFORE_TEXT = "paper_briefing.spacer.before_text"
    TEXT = "paper_briefing.text"
    LINE_1 = "paper_briefing.line.1"
    LINE_2 = "paper_briefing.line.2"
    LINE_3 = "paper_briefing.line.3"
    LINE_4 = "paper_briefing.line.4"
    LINE_5 = "paper_briefing.line.5"
    LINE_6 = "paper_briefing.line.6"
    SPACER_AFTER_TEXT = "paper_briefing.spacer.after_text"
    ZONE_LAYOUT = "paper_briefing.zone.layout"
    ZONE = "paper_briefing.zone"
    SPACER_ACTION = "paper_briefing.spacer.action"
    CONTINUE = "paper_briefing.continue"


def _content():
    text = Column(
        Text("PLACE A SHEET OF PRINTER PAPER UNDER THE CLEAN NOZZLE.")
        .style(UiStyle.PRIMARY).height(16).allow_overflow().ref(PaperBriefingRef.LINE_1),
        Text("PROBE FINDS THE BED, THEN LIFTS THE NOZZLE 0.5 MM.")
        .style(UiStyle.TEXT).height(16).allow_overflow().ref(PaperBriefingRef.LINE_2),
        Text(
            derived(lambda height:
                    "OR USE MOVE TO %.3f MM (HALF OF SAFE Z) TO SKIP PROBING" %
                    height,
                    bind(PaperBriefingState.MANUAL_START)),
        ).style(UiStyle.TEXT).height(16).allow_overflow().ref(PaperBriefingRef.LINE_3),
        Text("AND DO THE PAPER TEST FROM THERE WITH THE SAME CONTROLS.")
        .style(UiStyle.TEXT).height(16).allow_overflow().ref(PaperBriefingRef.LINE_4),
        Text("CHOOSE A STEP. CLOSER INCREASES THE DRAG, FARTHER REDUCES IT.")
        .style(UiStyle.TEXT).height(16).allow_overflow().ref(PaperBriefingRef.LINE_5),
        Text("WHEN THE PAPER MOVES WITH LIGHT, EVEN DRAG, TAP ACCEPT ZONE.")
        .style(UiStyle.TEXT).height(16).allow_overflow().ref(PaperBriefingRef.LINE_6),
        gap=20,
    ).height(196).ref(PaperBriefingRef.TEXT)
    zone = Grid(
        matrix=((Text(
            derived(lambda zone: "SELECTED ZONE: %s" % zone,
                    bind(PaperBriefingState.ZONE_LABEL)),
            color=ThemeColor.SECONDARY, font="JetBrainsMono Bold 12pt",
        ).ref(PaperBriefingRef.ZONE),),),
        columns=Equal(1), rows=Equal(1),
    ).height(24).padding(left=10, right=10).ref(PaperBriefingRef.ZONE_LAYOUT)
    return Column(
        Spacer().ref(PaperBriefingRef.SPACER_BEFORE_TEXT),
        text,
        Spacer().height(21).ref(PaperBriefingRef.SPACER_AFTER_TEXT),
        zone,
        Spacer().ref(PaperBriefingRef.SPACER_ACTION),
        Button(ENTER_ZONE, "POSITION HEAD").style(UiStyle.BUTTON_BOLD_12).size(460, 74).align(horizontal="center")
         .ref(PaperBriefingRef.CONTINUE),
    ).padding(left=10, right=10, bottom=28).ref(PaperBriefingRef.ROOT)


PAGE = Page(_content(), CONTENT, page_id=PAGE_ID, styles=UI_STYLES)


def render(renderer, values):
    return PAGE.draw(renderer, values)
