## Declarative Z-offset paper briefing page for Feather.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from enum import Enum

from ui.bindings import bind, derived
from ui.components import Button, Text
from ui.layout import Column, PageTree as Page, Spacer
from ...keys import AppPage
from ...styles import UI_STYLES, UiStyle
from ..actions import ENTER_ZONE
from ..common import CONTENT
from .state import PaperBriefingState
from ui import ThemeColor


PAGE_ID = AppPage.Z_OFFSET_PAPER_BRIEFING


class PaperBriefingRef(Enum):
    ROOT = "paper_briefing.root"
    TEXT = "paper_briefing.text"
    ZONE = "paper_briefing.zone"
    CONTINUE = "paper_briefing.continue"


def _content():
    text = Column(
        Text("TAP PROBE. THE NOZZLE TOUCHES THE BED, THEN LIFTS 0.5 MM.",
             wrap=True, auto_height=True).style(UiStyle.PRIMARY),
        Text("SLIDE PAPER UNDER THE NOZZLE. USE CLOSER AND FARTHER UNTIL IT DRAGS LIGHTLY BUT STILL MOVES.",
             wrap=True, auto_height=True).style(UiStyle.TEXT),
        Text(
            derived(lambda height:
                    "NO PROBING: MOVE TO %.3f MM (HALF OF SAFE Z) AND START THE PAPER TEST FROM THERE." % height,
                    bind(PaperBriefingState.MANUAL_START)),
            wrap=True, auto_height=True).style(UiStyle.TEXT),
        Text("WHEN THE DRAG FEELS LIGHT AND EVEN, TAP ACCEPT ZONE.",
             wrap=True, auto_height=True).style(UiStyle.DIM),
        gap=16,
    ).height("content").ref(PaperBriefingRef.TEXT)
    zone = Text(
        derived(lambda zone: "ZONE: %s" % zone,
                bind(PaperBriefingState.ZONE_LABEL)),
        color=ThemeColor.SECONDARY, font="JetBrainsMono Bold 12pt",
    ).height(28).align(horizontal="center").ref(PaperBriefingRef.ZONE)
    return Column(
        text,
        Spacer().grow(18),
        zone,
        Spacer().grow(18),
        Button(ENTER_ZONE, "MOVE HEAD TO ZONE").style(UiStyle.BUTTON_BOLD_12)
        .size(460, 74).align(horizontal="center").ref(PaperBriefingRef.CONTINUE),
    ).padding(left=24, top=18, right=24, bottom=28).ref(PaperBriefingRef.ROOT)


PAGE = Page(_content(), CONTENT, page_id=PAGE_ID, styles=UI_STYLES)


def render(renderer, values):
    return PAGE.draw(renderer, values)
