## Declarative active-print page for Feather.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from enum import Enum

from ui import ThemeColor
from ui.bindings import bind, derived, resolve
from ui.components import Button, Component, Fill, Panel, Text
from ui.layout import (
    FLEX, Column, Equal, Flex, Grid, Overlay, PageTree, Rect, Row, Spacer,
    StateCase,
)

from ..keys import AppPage
from .actions import CANCEL, FILAMENT, HOME, PAUSE, RESUME, Z_ADJUST
from .state import PrintingState


PAGE_BOUNDS = Rect(0, 0, 800, 442)
FONT = "JetBrainsMono 8pt"
VALUE_FONT = "JetBrainsMono 12pt"
BUTTON_FONT = "JetBrainsMono Bold 8pt"
PREVIEW_IMAGE_PADDING = 20


class PrintingRef(Enum):
    ROOT = "printing.root"
    HEADER = "printing.header"
    HOME = "printing.home"
    BODY = "printing.body"
    DETAILS = "printing.details"
    FILENAME = "printing.filename"
    STATUS = "printing.status"
    PROGRESS = "printing.progress"
    ELAPSED = "printing.elapsed"
    REMAINING = "printing.remaining"
    LAYER = "printing.layer"
    HEIGHT = "printing.height"
    PREVIEW = "printing.preview"
    PREVIEW_BOX = "printing.preview.box"
    BUTTONS = "printing.buttons"
    PAUSE = "printing.pause"
    FILAMENT = "printing.filament"
    Z_ADJUST = "printing.z_adjust"
    CANCEL = "printing.cancel"


class PrintProgress(Component):
    """The progress value and its inset track are one paint invariant."""

    covers_bounds = True

    def __init__(self, value, key=None):
        super().__init__(key=key)
        self.value = value

    def draw(self, renderer, state, bounds):
        inset = min(6, bounds.width // 2, bounds.height // 2)
        inner = bounds.inset(inset)
        progress = max(0, min(100, int(resolve(self.value, state))))
        width = round(inner.width * progress / 100.0)
        commands = [
            renderer.stroke(
                *bounds, color=ThemeColor.BORDER, line_width=2),
            renderer.fill(
                inner.x, inner.y, inner.width, inner.height,
                ThemeColor.BACKGROUND),
        ]
        if width:
            commands.append(renderer.fill(
                inner.x, inner.y, width, inner.height, ThemeColor.PRIMARY))
        return commands


def _button_state(ready, pending, action):
    if not ready:
        return "disabled"
    return "busy" if pending == action else "enabled"


def _pause_state(ready, pending):
    return _button_state(ready, pending, "print.pause")


def _resume_state(ready, pending):
    return _button_state(ready, pending, "print.resume")


def _enabled(value):
    return "enabled" if value else "disabled"


def _metric(label, value, ref):
    return Column(
        Text(label, color=ThemeColor.PRIMARY, font=FONT,
             horizontal="left"),
        Overlay(
            Fill(ThemeColor.BACKGROUND),
            Text(value, color=ThemeColor.TEXT, font=VALUE_FONT,
                 horizontal="left"),
        ).ref(ref).repaint_boundary(),
        gap=0,
    )


def _detail_metrics(first, second, divider=True):
    if divider:
        matrix = ((
            first,
            Fill(ThemeColor.BORDER).margin(top=7, bottom=1),
            second,
        ),)
        columns = (FLEX, 1, FLEX)
        gap = (5, 0)
    else:
        matrix = ((first, second),)
        columns = Equal(2)
        gap = (11, 0)
    return Grid(matrix=matrix, columns=columns, rows=Equal(1), gap=gap)


def _details():
    information = Column(
        Overlay(
            Fill(ThemeColor.BACKGROUND),
            Text(
                bind(PrintingState.FILENAME), color=ThemeColor.PRIMARY,
                font="JetBrainsMono Bold 12pt", horizontal="left",
                vertical="top", truncate=True),
        ).height(24).ref(PrintingRef.FILENAME).repaint_boundary(),
        Overlay(
            Fill(ThemeColor.BACKGROUND),
            Text(
                bind(PrintingState.STATUS), color=ThemeColor.TEXT, font=FONT,
                horizontal="left", vertical="top", truncate=True),
        ).height(24).ref(PrintingRef.STATUS).repaint_boundary(),
        gap=8,
    ).padding(top=4)
    progress = Column(
        Row(
            Text("PROGRESS", color=ThemeColor.PRIMARY, font=FONT,
                 horizontal="left"),
            Overlay(
                Fill(ThemeColor.BACKGROUND),
                Text(
                    derived(lambda value: "%d%%" % value,
                            bind(PrintingState.PROGRESS)),
                    color=ThemeColor.PRIMARY, font=VALUE_FONT,
                    horizontal="right"),
            ).repaint_boundary(),
            gap=0,
        ).height(16),
        PrintProgress(bind(PrintingState.PROGRESS)).height(34)
        .ref(PrintingRef.PROGRESS),
        gap=12,
    )
    timing = _detail_metrics(
        _metric("ELAPSED", bind(PrintingState.ELAPSED), PrintingRef.ELAPSED),
        _metric("REMAINING", bind(PrintingState.REMAINING),
                PrintingRef.REMAINING),
    )
    position = _detail_metrics(
        _metric("LAYER", bind(PrintingState.LAYER), PrintingRef.LAYER),
        _metric("HEIGHT", bind(PrintingState.HEIGHT), PrintingRef.HEIGHT),
        divider=False,
    )
    return Grid(
        matrix=(
            (information,),
            (progress,),
            (Spacer(),),
            (Fill(ThemeColor.BORDER),),
            (timing,),
            (Spacer(),),
            (Fill(ThemeColor.BORDER),),
            (position,),
        ),
        columns=(FLEX,),
        rows=(60, 62, FLEX, 1, Flex(4), 8, 1, Flex(4)),
        gap=0,
    ).ref(PrintingRef.DETAILS)


def _preview():
    no_preview = Overlay(
        StateCase(
            Text("NO PREVIEW", color=ThemeColor.DIM, font=FONT),
            selector=bind(PrintingState.PREVIEW_STATUS), expected="none"),
        StateCase(
            Text("NO PREVIEW", color=ThemeColor.DIM, font=FONT),
            selector=bind(PrintingState.PREVIEW_STATUS), expected="failed"),
    ).margin(left=8, right=8, bottom=8).ref(PrintingRef.PREVIEW_BOX)
    return Overlay(
        Panel(border=ThemeColor.BORDER, background=ThemeColor.PANEL,
              line_width=1),
        Column(
            Column(
                Text("PREVIEW", color=ThemeColor.PRIMARY, font=FONT,
                     horizontal="left"),
            ).height(38).padding(left=18, top=4),
            no_preview,
            gap=0,
        ),
    ).ref(PrintingRef.PREVIEW)


def _buttons():
    ready = bind(PrintingState.CONTROLS_READY)
    pending = bind(PrintingState.PENDING_ACTION)
    pause_state = derived(_pause_state, ready, pending)
    resume_state = derived(_resume_state, ready, pending)
    filament_state = derived(_enabled, ready)
    z_state = derived(_enabled, bind(PrintingState.LIVE_Z_ALLOWED))
    pause = Overlay(
        StateCase(
            Button(PAUSE, "PAUSE", state=pause_state, font=BUTTON_FONT),
            selector=bind(PrintingState.PAUSED), expected=False),
        StateCase(
            Button(RESUME, "RESUME", state=resume_state, font=BUTTON_FONT),
            selector=bind(PrintingState.PAUSED), expected=True),
    ).ref(PrintingRef.PAUSE)
    return Grid(
        matrix=((
            pause,
            Button(FILAMENT, "FILAMENT", state=filament_state,
                   font=BUTTON_FONT).ref(PrintingRef.FILAMENT),
            Button(Z_ADJUST, "Z ADJUST", state=z_state,
                   font=BUTTON_FONT).ref(PrintingRef.Z_ADJUST),
            Button(CANCEL, "CANCEL", state="danger", font=BUTTON_FONT)
            .ref(PrintingRef.CANCEL),
        ),),
        columns=Equal(4), rows=Equal(1), gap=(8, 0),
    ).margin(left=20, right=20).ref(PrintingRef.BUTTONS)


def create_page(bounds=PAGE_BOUNDS):
    header = Row(
        Button(HOME, "HOME", font=BUTTON_FONT).width(146)
        .ref(PrintingRef.HOME),
        Spacer(),
        gap=0,
    ).padding(left=14, top=7, right=14, bottom=7).ref(PrintingRef.HEADER)
    content = Grid(
        matrix=(
            (Grid(
                matrix=((_details(), _preview()),),
                columns=(Flex(2), FLEX), rows=Equal(1), gap=(24, 0),
            ).margin(left=25, right=24).ref(PrintingRef.BODY),
            ),
            (_buttons().height(72),),
        ),
        columns=(FLEX,), rows=(FLEX, 72), gap=(0, 18),
    )
    root = Grid(
        matrix=((header,), (content,)),
        columns=(FLEX,), rows=(60, FLEX), gap=(0, 14),
    ).padding(bottom=15).ref(PrintingRef.ROOT)
    return PageTree(root, bounds, page_id=AppPage.PRINTING)


PAGE = create_page()
