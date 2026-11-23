# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
# This file may be distributed under the terms of the GNU GPLv3 license.

from copy import deepcopy
from functools import partial

from ui import (
    FLEX, ButtonSurface, Column, FeatherRenderer, Flex, Frame, Grid, PageTree, Text, ThemeColor,
    ThemeRole, When,
)
from ui.bindings import bind, derived

from ..calibration_layout import CONTENT, SIDE_INSET
from ..keys import AppPage
from .actions import ShaperAction
from .page import FONT, apply_button, axes, save_button
from .state import ShaperState


def _cell(rows, method, field):
    return rows.get(method, {}).get(field, "--")


def _row_cells(method, refs):
    """The row's values; refs names them on the one face that is the row's own."""
    values = {name: derived(partial(_cell, method=method, field=field), bind(ShaperState.TABLE))
              for name, field in (("name", "name"), ("frequency", "freq"), ("vibrations", "vibrs"),
                                  ("smoothing", "smoothing"), ("acceleration", "max_accel"))}
    color = derived(lambda current: ThemeRole.BUTTON_SELECTED_TEXT if current == method else ThemeColor.TEXT,
                    bind(ShaperState.METHOD))

    def cell(name, **layout):
        node = Text(values[name], font=FONT, color=color, **layout)
        return node.ref("shaper.row.%s.%s" % (method, name)) if refs else node

    return Grid(matrix=((
        cell("name", horizontal="left", truncate=True), cell("frequency"), cell("vibrations"),
        cell("smoothing"), cell("acceleration"),
    ),), columns=(Flex(2), FLEX, FLEX, FLEX, FLEX), rows=(FLEX,), gap=8).padding(left=16, right=16)


def _row_face(method, pressed):
    """The row drawn as a button in its state, its cells over it."""
    def colors(current):
        return FeatherRenderer.BUTTON_COLORS["pressed" if pressed else "selected" if current == method else "enabled"]

    return Frame(
        _row_cells(method, not pressed),
        border=derived(lambda current: colors(current)[1], bind(ShaperState.METHOD)),
        background=derived(lambda current: colors(current)[0], bind(ShaperState.METHOD)), line_width=2,
    )


def method_row(method):
    return ButtonSurface(ShaperAction("method", method), _row_face(method, False), _row_face(method, True)) \
        .height(44).ref("shaper.row." + method)


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
    .padding(left=SIDE_INSET, right=SIDE_INSET, top=8, bottom=10).ref("shaper.compare.root")


def create_page(bounds=CONTENT):
    page = PageTree(deepcopy(root), bounds, page_id=AppPage.SHAPER_COMPARE, state_schema=tuple(ShaperState))
    page.title = "Compare shapers"
    page.show_back = True
    return page


# Designer discovers the module's page; every result view creates its own tree.
PAGE = create_page()
