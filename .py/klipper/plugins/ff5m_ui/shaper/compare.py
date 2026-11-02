# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
# This file may be distributed under the terms of the GNU GPLv3 license.

from copy import deepcopy
from functools import partial

from ui import FLEX, Button, Column, Flex, Grid, PageTree, Text, ThemeColor, ThemeRole, When
from ui.bindings import bind, derived

from ..keys import AppPage
from .actions import ShaperAction
from .components import MethodRow
from .page import CONTENT, FONT, apply_button, axes, save_button
from .state import ShaperState


def _cell(rows, method, field):
    return rows.get(method, {}).get(field, "--")


def method_row(method):
    values = {name: derived(partial(_cell, method=method, field=field), bind(ShaperState.TABLE))
              for name, field in (("name", "name"), ("frequency", "freq"), ("vibrations", "vibrs"),
                                  ("smoothing", "smoothing"), ("acceleration", "max_accel"))}
    color = derived(lambda current: ThemeRole.BUTTON_SELECTED_TEXT if current == method else ThemeColor.TEXT,
                    bind(ShaperState.METHOD))
    return MethodRow(
        Button(ShaperAction("method", method), "", font=FONT,
               state=derived(lambda current: "selected" if current == method else "enabled", bind(ShaperState.METHOD))),
        Grid(matrix=((
            Text(values["name"], font=FONT, color=color, horizontal="left", truncate=True)
                .ref("shaper.row." + method + ".name"),
            Text(values["frequency"], font=FONT, color=color).ref("shaper.row." + method + ".frequency"),
            Text(values["vibrations"], font=FONT, color=color).ref("shaper.row." + method + ".vibrations"),
            Text(values["smoothing"], font=FONT, color=color).ref("shaper.row." + method + ".smoothing"),
            Text(values["acceleration"], font=FONT, color=color).ref("shaper.row." + method + ".acceleration"),
        ),), columns=(Flex(2), FLEX, FLEX, FLEX, FLEX), rows=(FLEX,), gap=8).padding(left=16, right=16),
    ).height(44).ref("shaper.row." + method)


caption = Text(bind(ShaperState.RECOMMENDED), font=FONT, color=ThemeColor.DIM,
               horizontal="right", truncate=True).height(22).ref("shaper.compare.caption")

header = Grid(matrix=((
    Text("SHAPER", font=FONT, color=ThemeColor.DIM, horizontal="left"),
    Text("Hz", font=FONT, color=ThemeColor.DIM),
    Text("VIB %", font=FONT, color=ThemeColor.DIM),
    Text("SM", font=FONT, color=ThemeColor.DIM),
    Text("ACCEL", font=FONT, color=ThemeColor.DIM),
),), columns=(Flex(2), FLEX, FLEX, FLEX, FLEX), rows=(FLEX,), gap=8) \
    .height(22).padding(left=16, right=16).ref("shaper.compare.header")

rows = Column(
    method_row("zv"), method_row("mzv"), method_row("ei"),
    method_row("2hump_ei"), method_row("3hump_ei"),
    When(bind(ShaperState.HAS_ZVD), method_row("zvd")).height(44),
).ref("shaper.compare.rows")

footer = Grid(matrix=((
    axes(), apply_button(), save_button(),
),), columns=(104, FLEX, FLEX), rows=(FLEX,), gap=12).height(44).ref("shaper.compare.footer")

root = Column(Column(caption, header).height(44), rows, footer, gap=8) \
    .padding(left=24, right=24, top=8, bottom=10).ref("shaper.compare.root")
PAGE = PageTree(root, CONTENT, page_id=AppPage.SHAPER_COMPARE,
                state_schema=tuple(ShaperState))
PAGE.title = "Compare shapers"
PAGE.show_back = True


def create_page(bounds=CONTENT):
    page = PageTree(deepcopy(PAGE.root), bounds, page_id=AppPage.SHAPER_COMPARE,
                    state_schema=tuple(ShaperState))
    page.title, page.show_back = PAGE.title, PAGE.show_back
    return page
