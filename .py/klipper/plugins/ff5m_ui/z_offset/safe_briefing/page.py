## Declarative Safe Z briefing page for Feather.

from enum import Enum

from ui.bindings import bind, derived
from ui.components import Button, Text
from ui.layout import Column, Equal, Grid, PageTree as Page, Spacer
from ...keys import AppPage
from ..actions import SAFE_CALIBRATE, SAFE_SKIP
from ..common import CONTENT, FONT
from .state import SafeBriefingState


PAGE_ID = AppPage.SAFE_Z_BRIEFING


class SafeBriefingRef(Enum):
    ROOT = "safe_briefing.root"
    TEXT = "safe_briefing.text"
    CURRENT = "safe_briefing.current"
    ACTIONS = "safe_briefing.actions"
    SKIP = "safe_briefing.skip"
    CALIBRATE = "safe_briefing.calibrate"


def _content():
    text = Column(
        Text(
            "SAFE Z IS THE HEIGHT USED BEFORE LATERAL PARKING AND CALIBRATION MOVES.",
            color="35d9e6", font=FONT, wrap=True, auto_height=True),
        Text(
            "BED LEVELING AND A LONGER NOZZLE CAN REDUCE THE REAL CLEARANCE.",
            color="d9e4e8", font=FONT, wrap=True, auto_height=True),
        Text(
            "FEATHER WILL MOVE TO THE BED CENTER, PROBE IT, THEN ADD 5 MM.",
            color="d9e4e8", font=FONT, wrap=True, auto_height=True),
        Text(
            "THIS CHECK RUNS BEFORE NOZZLE CLEANING; NORMAL PREPARATION CONTINUES AFTERWARD.",
            color="d9e4e8", font=FONT, wrap=True, auto_height=True),
        Text(
            "SKIP THIS STEP IF SAFE Z WAS ALREADY VERIFIED FOR THIS SETUP.",
            color="d9e4e8", font=FONT, wrap=True, auto_height=True),
        gap=20,
    ).ref(SafeBriefingRef.TEXT)
    current = Text(
        derived(lambda current, start:
                "CURRENT: %.3f MM   START HEIGHT: %.3f MM" %
                (current, start),
                bind(SafeBriefingState.CURRENT),
                bind(SafeBriefingState.START)),
        color="b47aff", font="JetBrainsMono Bold 10pt",
    ).height(28).align(horizontal="center").ref(SafeBriefingRef.CURRENT)
    actions = Grid(
        matrix=((
            Button(SAFE_SKIP, "SKIP", font="JetBrainsMono Bold 12pt")
            .ref(SafeBriefingRef.SKIP),
            Button(SAFE_CALIBRATE, "CALIBRATE SAFE Z",
                   state="warning", font="JetBrainsMono Bold 12pt")
            .ref(SafeBriefingRef.CALIBRATE),
        ),),
        columns=Equal(2), rows=Equal(1), gap=(20, 0),
    ).height(74).ref(SafeBriefingRef.ACTIONS)
    return Column(
        text,
        Spacer().grow(18),
        current,
        Spacer().grow(18),
        actions,
    ).padding(left=24, top=18, right=24, bottom=28) \
     .ref(SafeBriefingRef.ROOT)


PAGE = Page(_content(), CONTENT, page_id=PAGE_ID)


def render(renderer, values):
    return PAGE.draw(renderer, values)
