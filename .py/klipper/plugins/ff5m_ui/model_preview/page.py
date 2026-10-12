## Declarative model preview benchmark page.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from functools import partial

from ui import Button, Column, Equal, FLEX, Grid, Overlay, PageTree, Panel, Rect, Spacer, Text, ThemeColor
from ui.bindings import bind, derived

from ..keys import AppPage
from .actions import PreviewAction, PreviewRoute
from .common import BUTTON_FONT, FONT, ModelFrame, PreviewRef, PreviewState, _style_state, _selected


TITLE = "Model preview benchmark"


def _button(route, label, **kwargs):
    return Button(PreviewAction(route), label, font=BUTTON_FONT, **kwargs)


def create_page(width=189, height=188):
    preview = Column(
        Overlay(
            Panel(border=ThemeColor.PRIMARY, background=ThemeColor.PANEL),
            ModelFrame(bind(PreviewState.BANDS), bind(PreviewState.ERROR))
            .margin(20).ref(PreviewRef.IMAGE).repaint_boundary(),
        ).height(height + 40),
        Spacer(),
        _button(PreviewRoute.CACHE,
                derived(lambda enabled: "CACHE ON" if enabled else "CACHE OFF", bind(PreviewState.CACHE)),
                state=derived(_selected, bind(PreviewState.CACHE))).height(46),
        gap=0,
    )
    styles = tuple(
        Button(PreviewAction(PreviewRoute.STYLE, index), label, font=BUTTON_FONT,
               state=derived(partial(_style_state, index=index), bind(PreviewState.STYLE)))
        for index, label in enumerate(("SOLID", "STACKED", "POINTS", "EDGES", "LINES")))
    controls = Column(
        Grid(matrix=(styles[:3], styles[3:] + (_button(PreviewRoute.FILE, "FILE"),)),
             columns=Equal(3), rows=Equal(2), gap=8).height(100),
        Spacer().height(8),
        Text(bind(PreviewState.STATUS), color=ThemeColor.PRIMARY, font=FONT,
             horizontal="left", truncate=True).height(24).ref(PreviewRef.STATUS),
        Spacer().height(4),
        Grid(matrix=(
            (_button(PreviewRoute.YAW_RIGHT, "YAW +"), _button(PreviewRoute.PITCH_UP, "TILT +"),
             _button(PreviewRoute.LAYER_UP, "LYR +"), _button(PreviewRoute.SCALE_UP, "SCALE +")),
            (_button(PreviewRoute.YAW_LEFT, "YAW -"), _button(PreviewRoute.PITCH_DOWN, "TILT -"),
             _button(PreviewRoute.LAYER_DOWN, "LYR -"), _button(PreviewRoute.SCALE_DOWN, "SCALE -")),
        ), columns=Equal(4), rows=Equal(2), gap=8).height(100),
        Spacer().height(8),
        Grid(matrix=((
            _button(PreviewRoute.ROTATION,
                    derived(lambda active: "STOP TURN" if active else "AUTO TURN", bind(PreviewState.ROTATION))),
            _button(PreviewRoute.BUILD,
                    derived(lambda active: "STOP BUILD" if active else "AUTO BUILD", bind(PreviewState.BUILD))),
        ),), columns=Equal(2), rows=(FLEX,), gap=12).height(46),
        gap=0,
    )
    metrics = Column(
        Text(bind(PreviewState.METRICS), color=ThemeColor.PRIMARY, font=FONT,
             horizontal="left", truncate=True).height(22),
        Text(bind(PreviewState.DETAIL), color=ThemeColor.DIM, font=FONT,
             horizontal="left", truncate=True).height(22),
        gap=0,
    ).height(44).ref(PreviewRef.METRICS)
    root = Column(
        Grid(matrix=((
            Text(bind(PreviewState.FILENAME), color=ThemeColor.BRIGHT, font=FONT,
                 horizontal="left", truncate=True),
            Text(derived(lambda scale: "SCALE %.0f%%" % (scale * 100), bind(PreviewState.SCALE)),
                 color=ThemeColor.PRIMARY, font=FONT, horizontal="right").ref(PreviewRef.SCALE),
        ),), columns=(FLEX, 144), rows=(FLEX,), gap=12).height(26),
        Spacer().height(6),
        Grid(matrix=((preview, controls),), columns=(width + 40, FLEX),
             rows=(FLEX,), gap=61).height(290),
        Spacer().height(10),
        metrics,
        gap=0,
    ).ref(PreviewRef.ROOT)
    page = PageTree(root, Rect(20, 66, 760, 376), page_id=AppPage.MODEL_PREVIEW,
                    actions=(PreviewAction(PreviewRoute.RESET), PreviewAction(PreviewRoute.FULL)))
    page.title, page.show_back = TITLE, True
    return page


PAGE = create_page()
