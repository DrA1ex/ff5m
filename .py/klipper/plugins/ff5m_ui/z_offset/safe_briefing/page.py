## Declarative Safe Z briefing page for Feather.

from enum import Enum

from ui.bindings import bind, derived
from ui.components import Button, Text
from ui.layout import Column, Equal, Grid, PageTree as Page, Spacer
from ...keys import AppPage
from ...styles import UI_STYLES, UiStyle
from ..actions import SAFE_CALIBRATE, SAFE_SKIP
from ..common import CONTENT
from .state import SafeBriefingState
from ui import ThemeColor


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
            "SAFE Z DEFINES HOW HIGH THE TOOLHEAD MOVES BEFORE PARKING OR MAKING XY MOVES.",
            wrap=True, auto_height=True).style(UiStyle.PRIMARY),
        Text(
            "CALIBRATE IT IF THE BED HAS BEEN RAISED, OR IF A LONGER NOZZLE IS INSTALLED.",
            wrap=True, auto_height=True).style(UiStyle.TEXT),
        Text(
            "FEATHER PROBES THE CENTER OF THE BED AND ADDS 5 MM OF CLEARANCE.",
            wrap=True, auto_height=True).style(UiStyle.TEXT),
        Text(
            "SKIP THIS ONLY IF SAFE Z HAS ALREADY BEEN CHECKED FOR THE CURRENT BED AND NOZZLE.",
            wrap=True, auto_height=True).style(UiStyle.TEXT),
        gap=20,
    ).height("content").ref(SafeBriefingRef.TEXT)
    current = Text(
        derived(lambda current, start:
                "CURRENT: %.3f MM   START HEIGHT: %.3f MM" %
                (current, start),
                bind(SafeBriefingState.CURRENT),
                bind(SafeBriefingState.START)),
        color=ThemeColor.SECONDARY, font="JetBrainsMono Bold 10pt",
    ).height(28).align(horizontal="center").ref(SafeBriefingRef.CURRENT)
    actions = Grid(
        matrix=((
            Button(SAFE_SKIP, "SKIP").style(UiStyle.BUTTON_BOLD_12)
            .ref(SafeBriefingRef.SKIP),
            Button(SAFE_CALIBRATE, "CALIBRATE SAFE Z", state="warning")
            .style(UiStyle.BUTTON_BOLD_12).ref(SafeBriefingRef.CALIBRATE),
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


PAGE = Page(_content(), CONTENT, page_id=PAGE_ID, styles=UI_STYLES)


def render(renderer, values):
    return PAGE.draw(renderer, values)
