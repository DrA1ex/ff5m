## Declarative Z-offset briefing page for Feather.

from enum import Enum

from ui.actions import Navigate
from ui.components import Button, Text
from ui.layout import Column, PageTree as Page, Spacer
from ...keys import AppPage
from ..common import CONTENT, FONT


PAGE_ID = AppPage.Z_OFFSET_BRIEFING


class BriefingRef(Enum):
    ROOT = "briefing.root"
    TEXT = "briefing.text"
    LINE_1 = "briefing.line.1"
    LINE_2 = "briefing.line.2"
    LINE_3 = "briefing.line.3"
    LINE_4 = "briefing.line.4"
    LINE_5 = "briefing.line.5"
    SPACER = "briefing.spacer"
    CONTINUE = "briefing.continue"


def _content():
    text = Column(
        Text(
            "Z OFFSET SETS THE NOZZLE-TO-BED HEIGHT FOR THE FIRST LAYER.",
            color="35d9e6", font=FONT, wrap=True, auto_height=True,
        ).ref(BriefingRef.LINE_1),
        Text(
            "CHOOSE ONE OR MORE BED ZONES; FEATHER GUIDES EACH PAPER TEST.",
            color="d9e4e8", font=FONT, wrap=True, auto_height=True,
        ).ref(BriefingRef.LINE_2),
        Text(
            "THE SCREEN COLLECTS THE RESULTS AND CAN USE THEIR AVERAGE.",
            color="d9e4e8", font=FONT, wrap=True, auto_height=True,
        ).ref(BriefingRef.LINE_3),
        Text(
            "WITH AUTO LOAD ON, THE CHOSEN Z OFFSET IS APPLIED BEFORE",
            color="d9e4e8", font=FONT, wrap=True, auto_height=True,
        ).ref(BriefingRef.LINE_4),
        Text(
            "EVERY PRINT.", color="d9e4e8", font=FONT,
            wrap=True, auto_height=True,
        ).ref(BriefingRef.LINE_5),
        gap=23,
    ).ref(BriefingRef.TEXT)
    return Column(
        text,
        Spacer().ref(BriefingRef.SPACER),
        Button(
            Navigate(AppPage.Z_OFFSET_SUMMARY), "SELECT ZONES",
            font="JetBrainsMono Bold 12pt",
        ).size(460, 74).align(horizontal="center")
         .ref(BriefingRef.CONTINUE),
    ).padding(left=20, top=12, right=20, bottom=28).ref(BriefingRef.ROOT)


PAGE = Page(_content(), CONTENT, page_id=PAGE_ID)


def render(renderer):
    return PAGE.draw(renderer)
