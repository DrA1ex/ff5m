# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
# This file may be distributed under the terms of the GNU GPLv3 license.

import time
from functools import partial

from ui import Button, Column, PageTree, Spacer, When
from ui.bindings import bind, derived, state
from ui.identity import StateKey

from ..keys import AppPage
from .actions import ShaperAction
from .page import CONTENT


class HistoryState(StateKey):
    __key_namespace__ = "ff5m_ui.shaper.history"
    ENTRIES = state(list, default=[dict(id="example", saved_at=1793613222., axes=["x", "y"], latest=True)])


def _label(entries, index):
    if index >= len(entries):
        return ""
    entry = entries[index]
    date = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry["saved_at"]))
    return "%s  |  %s%s" % (date, " / ".join(a.upper() for a in entry["axes"]),
                            "  LATEST" if index == 0 else "")


root = Column(
    *(When(derived(lambda entries, i=i: len(entries) > i, bind(HistoryState.ENTRIES)),
           Button(ShaperAction("record", str(i)),
                  derived(partial(_label, index=i), bind(HistoryState.ENTRIES)),
                  font="JetBrainsMono 10pt").ref("shaper.history.record.%d" % i)).height(60)
      for i in range(5)),
    Spacer(), gap=10,
).padding(left=24, right=24, top=18, bottom=18).ref("shaper.history.root")

PAGE = PageTree(root, CONTENT, page_id=AppPage.SHAPER_HISTORY, state_schema=tuple(HistoryState))
PAGE.title = "Shaper results"
PAGE.show_back = True


def render(renderer, entries):
    return renderer.begin_page(PAGE.title, back=True) + PAGE.draw(renderer, {HistoryState.ENTRIES: entries})
