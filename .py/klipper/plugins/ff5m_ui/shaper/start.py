# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
# This file may be distributed under the terms of the GNU GPLv3 license.

import time

from ui import FLEX, Button, Column, Flex, Grid, PageTree, Rect, Spacer, Text, ThemeColor
from ui.bindings import bind, derived, state
from ui.identity import StateKey

from ..keys import AppPage
from .actions import ShaperAction


class StartState(StateKey):
    __key_namespace__ = "ff5m_ui.shaper.start"
    HAS_LAST = state(bool, default=False)
    SUMMARY = state(str, default="No saved calibration")


root = Grid(matrix=((Spacer(), Column(
    Spacer(),
    Button(ShaperAction("start"), "START CALIBRATION", font="JetBrainsMono 12pt")
        .height(72).ref("shaper.start.measure"),
    Spacer().height(32),
    Button(ShaperAction("last"), "LAST RESULTS", font="JetBrainsMono 12pt",
           state=derived(lambda ready: "enabled" if ready else "disabled", bind(StartState.HAS_LAST)))
        .height(64).ref("shaper.start.last"),
    Spacer().height(12),
    Text(bind(StartState.SUMMARY), font="JetBrainsMono 8pt", color=ThemeColor.DIM, truncate=True)
        .height(32).ref("shaper.start.summary"),
    Spacer(),
), Spacer()),), columns=(FLEX, Flex(5), FLEX), rows=(FLEX,)) \
    .padding(left=24, right=24, top=18, bottom=18).ref("shaper.start.root")

PAGE = PageTree(root, Rect(0, 56, 800, 386), page_id=AppPage.SHAPER_START, state_schema=tuple(StartState))
PAGE.title = "Input shaper"
PAGE.show_back = True


def render(renderer, latest):
    """Summarize the latest history entry; its spectra are not loaded."""
    summary = "No saved calibration"
    if latest is not None:
        date = time.strftime("%Y-%m-%d %H:%M", time.localtime(latest["saved_at"]))
        summary = " / ".join(axis.upper() for axis in latest["axes"]) + "  |  " + date
    return renderer.begin_page(PAGE.title, back=True) + PAGE.draw(renderer, {
        StartState.HAS_LAST: latest is not None, StartState.SUMMARY: summary})
