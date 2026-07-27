## Declarative Safe Z calibration page for Feather.

from enum import Enum

from ui.bindings import bind, derived
from ui.components import Button, Panel, Text
from ui.layout import Column, Equal, Grid, Overlay, PageTree as Page, Spacer
from ...keys import AppPage
from ..actions import SAFE_HIGHER, SAFE_LOWER, SAFE_PROBE, SAFE_SAVE
from ..common import CONTENT, FONT
from .state import SafeState


PAGE_ID = AppPage.SAFE_Z_CALIBRATION


class SafeRef(Enum):
    ROOT = "safe.root"
    HELP = "safe.help"
    CARDS = "safe.cards"
    CURRENT = "safe.current"
    TRIGGER = "safe.trigger"
    CANDIDATE = "safe.candidate"
    PROBE = "safe.probe"
    ADJUST = "safe.adjust"
    LOWER = "safe.lower"
    HIGHER = "safe.higher"
    SAVE = "safe.save"


def _format(value):
    return "--" if value is None else "%.3f MM" % value


def _card(label, value, ref):
    return Overlay(
        Panel(border="295c66", background="050c0f", line_width=2),
        Text(label, color="35d9e6", font=FONT)
        .height(20).margin(top=10).align(vertical="top"),
        Text(derived(_format, value), color="ffffff",
             font="JetBrainsMono Bold 12pt")
        .height(20).margin(top=43).align(vertical="top"),
    ).ref(ref).repaint_boundary()


def _content():
    help_text = Text(
        "PROBE FINDS THE CLEAN BED TRIGGER AT CENTER. THE INITIAL SAFE Z IS TRIGGER + 5 MM.",
        color="d9e4e8", font=FONT, wrap=True, auto_height=True,
    ).ref(SafeRef.HELP)
    cards = Grid(
        matrix=((
            _card("CURRENT SAFE Z", bind(SafeState.CURRENT), SafeRef.CURRENT),
            _card("TRIGGER Z", bind(SafeState.TRIGGER), SafeRef.TRIGGER),
            _card("NEW SAFE Z", bind(SafeState.CANDIDATE), SafeRef.CANDIDATE),
        ),), columns=Equal(3), rows=Equal(1), gap=(15, 0),
    ).height(82).ref(SafeRef.CARDS)
    probe = Button(
        SAFE_PROBE, "PROBE BED CENTER",
        state=derived(lambda busy: "busy" if busy else "danger",
                      bind(SafeState.PROBING)),
        font="JetBrainsMono Bold 12pt",
    ).height(66).ref(SafeRef.PROBE)
    ready = derived(lambda value: "enabled" if value else "disabled",
                    bind(SafeState.READY))
    adjust = Grid(
        matrix=((
            Button(SAFE_LOWER, "LOWER  -1 MM", state=ready,
                   font="JetBrainsMono Bold 11pt").ref(SafeRef.LOWER),
            Button(SAFE_HIGHER, "HIGHER  +1 MM", state=ready,
                   font="JetBrainsMono Bold 11pt").ref(SafeRef.HIGHER),
        ),), columns=Equal(2), rows=Equal(1), gap=(20, 0),
    ).height(64).ref(SafeRef.ADJUST)
    save = Button(
        SAFE_SAVE, "SAVE SAFE Z AND CONTINUE", state=ready,
        font="JetBrainsMono Bold 12pt",
    ).height(68).ref(SafeRef.SAVE)
    return Column(
        help_text,
        Spacer().grow(12),
        cards,
        Spacer().grow(14),
        probe,
        Spacer().grow(14),
        adjust,
        Spacer().grow(14),
        save,
    ).padding(left=24, top=14, right=24, bottom=20).ref(SafeRef.ROOT)


PAGE = Page(_content(), CONTENT, page_id=PAGE_ID)


def render(renderer, values):
    return PAGE.draw(renderer, values)
