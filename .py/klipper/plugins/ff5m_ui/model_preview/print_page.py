## Interactive model preview before and during printing.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from copy import deepcopy

from ui import (
    Button, Cell, Slider, Column, Equal, Fill, FLEX, Grid, Metric, Overlay,
    PageTree, Panel, Rect, Row, Spacer, StateCase, Text, ThemeColor,
)
from ui.bindings import bind, derived

from ..keys import AppPage
from ..printing.state import PrintingState
from ..styles import UI_STYLES
from .actions import PreviewAction, PreviewRoute
from .common import BUTTON_FONT, FONT, ModelFrame, PreviewRef, PreviewState


TITLE = "Preview"


CONTENT = Grid(
    cells=(
        Cell(Text(bind(PreviewState.FILENAME), color=ThemeColor.BRIGHT, font=FONT,
                  horizontal="left", truncate=True).ref(PreviewRef.FILENAME),
             0, 0, column_span=2),
        Cell(Column(
            Column(
                Metric("PRINT TIME", bind(PreviewState.FILE_TIME), orientation="vertical",
                       label_font=FONT, value_font="JetBrainsMono 12pt")
                .padding(left=4, right=4).height("content"),
                Fill(ThemeColor.BORDER).height(1),
                Metric("LAYERS", bind(PreviewState.FILE_LAYERS), orientation="vertical",
                       label_font=FONT, value_font="JetBrainsMono 12pt")
                .padding(left=4, right=4).height("content"),
                Fill(ThemeColor.BORDER).height(1),
                Metric("FILE SIZE", bind(PreviewState.FILE_SIZE), orientation="vertical",
                       label_font=FONT, value_font="JetBrainsMono 12pt")
                .padding(left=4, right=4).height("content"),
                gap=10,
            ).height("content").ref(PreviewRef.METRICS),
            Spacer(),
            Text(derived(lambda scale: "SCALE %.0f%%" % (scale * 100), bind(PreviewState.SCALE)),
                 color=ThemeColor.PRIMARY, font=FONT).height(22).ref(PreviewRef.SCALE),
            Grid(matrix=((
                Button(PreviewAction(PreviewRoute.SCALE_DOWN), "-", font=BUTTON_FONT),
                Button(PreviewAction(PreviewRoute.SCALE_UP), "+", font=BUTTON_FONT),
            ),), columns=Equal(2), rows=(FLEX,), gap=12).height(46),
            gap=10,
        ).ref(PreviewRef.INFO), 0, 1),
        Cell(Column(
            Overlay(
                Panel(border=ThemeColor.PRIMARY, background=ThemeColor.PANEL),
                ModelFrame(bind(PreviewState.BANDS), bind(PreviewState.ERROR))
                .margin(8).ref(PreviewRef.IMAGE).repaint_boundary(),
                StateCase(
                    Text(bind(PreviewState.ERROR), color=ThemeColor.WARNING, font=FONT, wrap=True)
                    .height(72).margin(left=12, right=12, bottom=12).align(vertical="bottom"),
                    selector=derived(bool, bind(PreviewState.ERROR)), expected=True).ref(PreviewRef.ERROR),
            ),
            Slider(PreviewAction(PreviewRoute.YAW_JOYSTICK), "ROTATE", "x",
                   value=bind(PreviewState.YAW), minimum=-150.0, maximum=150.0,
                   glyph="bars", background=ThemeColor.BACKGROUND,
                   track_color=ThemeColor.BORDER, label_color=ThemeColor.DIM,
                   label_font=FONT, label_height=18, track_insets=(0, 20, 0, 6))
            .height(56).ref(PreviewRef.YAW_PAD).repaint_boundary(),
            gap=10,
        ).width(384).align(horizontal="center").ref(PreviewRef.CAMERA), 1, 1),
        Cell(Slider(PreviewAction(PreviewRoute.PITCH_JOYSTICK), "TILT", "z",
                    value=bind(PreviewState.PITCH), minimum=-70.0, maximum=70.0,
                    glyph="bars", background=ThemeColor.BACKGROUND,
                    track_color=ThemeColor.BORDER, label_color=ThemeColor.DIM,
                    label_font=FONT, label_top=4)
             .ref(PreviewRef.TILT_PAD).repaint_boundary(), 2, 0, row_span=2),
    ),
    columns=(220, FLEX, 56), rows=(26, FLEX), gap=(20, 8),
).padding(bottom=12).ref(PreviewRef.ROOT)


PAGE = PageTree(
    Overlay(
        CONTENT.margin(left=6, right=6, top=59),
        Row(
            Button(PreviewAction(PreviewRoute.SETTINGS), "SETTINGS", font=BUTTON_FONT),
            Button(PreviewAction(PreviewRoute.PRINT), "PRINT", font=BUTTON_FONT),
            gap=12,
        ).width(284).height(46).align(horizontal="right", vertical="top"),
    ),
    Rect(14, 7, 772, 435), page_id=AppPage.PRINT_PREVIEW, styles=UI_STYLES,
)
PAGE.title, PAGE.show_back, PAGE.list_title = TITLE, True, "Print preview"


def create_page(before_print=False):
    # Each runtime opening owns fresh nodes; the Designer edits the declaration above.
    if before_print:
        root, bounds = deepcopy(PAGE.root), PAGE.bounds
    else:
        root = deepcopy(CONTENT, {id(CONTENT.parent): None}).margin(0)
        information = next(node for node in root.walk() if node.key == PreviewRef.INFO)
        metrics = Column(
            Text(bind(PrintingState.STATUS), color=ThemeColor.PRIMARY, font=FONT,
                 horizontal="left", truncate=True).height(22),
            Metric("PROGRESS", derived(lambda value: "%d%%" % value, bind(PrintingState.PROGRESS)),
                   orientation="vertical").height("content"),
            Metric("ELAPSED", bind(PrintingState.ELAPSED), orientation="vertical").height("content"),
            Metric("REMAINING", bind(PrintingState.REMAINING), orientation="vertical").height("content"),
            Metric("LAYER", bind(PrintingState.LAYER), orientation="vertical").height("content"),
            Metric("HEIGHT", bind(PrintingState.HEIGHT), orientation="vertical").height("content"),
            gap=2,
        ).height("content").ref(PreviewRef.METRICS)
        information.replace_preview_children(tuple(
            metrics if child.key == PreviewRef.METRICS else child
            for child in information.render_children()))
        information.gap = 2
        bounds = Rect(20, 66, 760, 376)
    page = PageTree(root, bounds, page_id=AppPage.PRINT_PREVIEW, styles=UI_STYLES)
    page.title, page.show_back, page.list_title = PAGE.title, PAGE.show_back, PAGE.list_title
    return page
