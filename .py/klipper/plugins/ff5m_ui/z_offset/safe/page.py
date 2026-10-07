## Declarative Safe Z calibration page for Feather.
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
from ..actions import SAFE_HIGHER, SAFE_LOWER, SAFE_PROBE, SAFE_SAVE
from ..common import CONTENT
from .components import VALUE_CARD
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


def _content():
    cards = Grid(
        matrix=((
            VALUE_CARD(
                instance_key=SafeRef.CURRENT,
                label="CURRENT SAFE Z", value=derived(_format, bind(SafeState.CURRENT)),
                ref=SafeRef.CURRENT),
            VALUE_CARD(
                instance_key=SafeRef.TRIGGER,
                label="TRIGGER Z", value=derived(_format, bind(SafeState.TRIGGER)),
                ref=SafeRef.TRIGGER),
            VALUE_CARD(
                instance_key=SafeRef.CANDIDATE,
                label="NEW SAFE Z", value=derived(_format, bind(SafeState.CANDIDATE)),
                ref=SafeRef.CANDIDATE),
        ),),
        columns=Equal(3), rows=Equal(1), gap=(15, 0),
    ).height(82).ref(SafeRef.CARDS)
    ready = derived(
        lambda value: "enabled" if value else "disabled",
        bind(SafeState.READY))

    return Column(
        Text(
            "PROBE TOUCHES THE CLEAN BED AT THE CENTER. THE STARTING SAFE Z IS THE TRIGGER HEIGHT + 5 MM.",
            wrap=True, auto_height=True,
        ).style(UiStyle.TEXT).ref(SafeRef.HELP),
        Spacer().grow(12),
        cards,
        Spacer().grow(14),
        Button(
            SAFE_PROBE, "PROBE BED CENTER",
            state=derived(
                lambda busy: "busy" if busy else "danger",
                bind(SafeState.PROBING)),
        ).style(UiStyle.BUTTON_BOLD_12).height(66).ref(SafeRef.PROBE),
        Spacer().grow(14),
        Grid(
            matrix=((
                Button(SAFE_LOWER, "LOWER  -1 MM", state=ready).style(UiStyle.BUTTON_BOLD_11).ref(SafeRef.LOWER),
                Button(SAFE_HIGHER, "HIGHER  +1 MM", state=ready).style(UiStyle.BUTTON_BOLD_11).ref(SafeRef.HIGHER),
            ),),
            columns=Equal(2), rows=Equal(1), gap=(20, 0),
        ).height(64).ref(SafeRef.ADJUST),
        Spacer().grow(14),
        Button(SAFE_SAVE, "SAVE SAFE Z AND CONTINUE", state=ready)
        .style(UiStyle.BUTTON_BOLD_12).height(68).ref(SafeRef.SAVE),
    ).padding(left=24, top=14, right=24, bottom=20).ref(SafeRef.ROOT)


PAGE = Page(
    _content(), CONTENT, page_id=PAGE_ID,
    styles=UI_STYLES, component_templates=(VALUE_CARD,))


def render(renderer, values):
    return PAGE.draw(renderer, values)
