## Declarative Z-offset paper briefing page for Feather.

from enum import Enum

from ui.bindings import bind, derived
from ui.components import Button, Text
from ui.layout import Column, Equal, Grid, PageTree as Page, Spacer
from ...keys import AppPage
from ..actions import ENTER_ZONE
from ..common import CONTENT, FONT
from .state import PaperBriefingState
from ui import ThemeColor, ThemeRole


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
        Text(
            "PLACE NORMAL PRINTER PAPER UNDER THE CLEAN NOZZLE.",
            color=ThemeColor.PRIMARY, font=FONT,
        ).height(16).allow_overflow().ref(PaperBriefingRef.LINE_1),
        Text(
            "PRESS PROBE: IT FINDS THE LOAD-CELL TRIGGER, THEN LIFTS 0.5 MM.",
            color=ThemeColor.TEXT, font=FONT,
        ).height(16).allow_overflow().ref(PaperBriefingRef.LINE_2),
        Text(
            derived(lambda height:
                    "MOVE TO %.3f MM USES HALF OF SAFE Z AS REFERENCE, SO YOU" %
                    height,
                    bind(PaperBriefingState.MANUAL_START)),
            color=ThemeColor.TEXT, font=FONT,
        ).height(16).allow_overflow().ref(PaperBriefingRef.LINE_3),
        Text(
            "CAN DO THE PAPER TEST WITH THE SAME CONTROLS WITHOUT PROBING.",
            color=ThemeColor.TEXT, font=FONT,
        ).height(16).allow_overflow().ref(PaperBriefingRef.LINE_4),
        Text(
            "SELECT A STEP: CLOSER INCREASES DRAG; FARTHER REDUCES IT.",
            color=ThemeColor.TEXT, font=FONT,
        ).height(16).allow_overflow().ref(PaperBriefingRef.LINE_5),
        Text(
            "WHEN THE PAPER HAS LIGHT, EVEN DRAG, ACCEPT THE ZONE.",
            color=ThemeColor.TEXT, font=FONT,
        ).height(16).allow_overflow().ref(PaperBriefingRef.LINE_6),
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
        Button(
            ENTER_ZONE, "POSITION HEAD",
            font="JetBrainsMono Bold 12pt",
        ).size(460, 74).align(horizontal="center")
         .ref(PaperBriefingRef.CONTINUE),
    ).padding(left=10, right=10, bottom=28).ref(PaperBriefingRef.ROOT)


PAGE = Page(_content(), CONTENT, page_id=PAGE_ID)


def render(renderer, values):
    return PAGE.draw(renderer, values)
