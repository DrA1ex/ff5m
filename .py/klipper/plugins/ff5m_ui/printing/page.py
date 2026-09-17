## Declarative active-print page for Feather.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from enum import Enum

from ui import ThemeColor
from ui.bindings import bind, derived, resolve
from ui.components import Button, Component, Fill, Frame, Text
from ui.layout import FLEX, Column, Equal, Flex, Grid, Overlay, PageTree, Rect, Spacer, StateCase

from ..keys import AppPage
from ..styles import UI_STYLES, UiStyle
from .actions import CANCEL, FILAMENT, HOME, PAUSE, RESUME, Z_ADJUST
from .components import METRIC_PAIR
from .state import PrintingState


PAGE_BOUNDS = Rect(0, 0, 800, 442)
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
            renderer.stroke(*bounds, color=ThemeColor.BORDER, line_width=2),
            renderer.fill(inner.x, inner.y, inner.width, inner.height, ThemeColor.BACKGROUND),
        ]
        if width:
            commands.append(renderer.fill(inner.x, inner.y, width, inner.height, ThemeColor.PRIMARY))
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


def _details(compact=False):
    progress_value = bind(PrintingState.PROGRESS)

    information = Column(
        Text(
            bind(PrintingState.FILENAME),
            color=ThemeColor.PRIMARY,
            font="JetBrainsMono Bold 12pt",
            horizontal="left",
            vertical="top",
            truncate=True,
        ).height("content").ref(PrintingRef.FILENAME),
        Text(
            bind(PrintingState.STATUS),
            horizontal="left",
            vertical="top",
            truncate=True,
        ).style(UiStyle.TEXT).height("content").ref(PrintingRef.STATUS),
        gap=8,
    ).padding(top=4).height("content")

    progress = Column(
        Grid(
            matrix=((
                Text("PROGRESS").style(UiStyle.PRIMARY_LEFT),
                Text(
                    derived(lambda value: "%d%%" % value, progress_value),
                    color=ThemeColor.PRIMARY,
                    horizontal="right",
                ).style(UiStyle.VALUE),
            ),),
            columns=Equal(2), rows=Equal(1), gap=0,
        ).height("content"),
        PrintProgress(progress_value).height(34).ref(PrintingRef.PROGRESS),
        gap=8,
    ).height("content")

    timing = METRIC_PAIR(
        instance_key=PrintingRef.ELAPSED,
        left_label="ELAPSED", left_value=bind(PrintingState.ELAPSED), left_ref=PrintingRef.ELAPSED,
        right_label="REMAINING", right_value=bind(PrintingState.REMAINING), right_ref=PrintingRef.REMAINING,
    )
    position = METRIC_PAIR(
        instance_key=PrintingRef.LAYER,
        left_label="LAYER", left_value=bind(PrintingState.LAYER), left_ref=PrintingRef.LAYER,
        right_label="HEIGHT", right_value=bind(PrintingState.HEIGHT), right_ref=PrintingRef.HEIGHT,
    )

    return Column(
        information,
        progress,
        Spacer(),
        Fill(ThemeColor.BORDER).height(1),
        timing,
        Fill(ThemeColor.BORDER).height(1),
        position,
        gap=0 if compact else 4,
    ).ref(PrintingRef.DETAILS)


def _preview():
    no_preview = Overlay(
        StateCase(
            Text("NO PREVIEW").style(UiStyle.DIM),
            selector=bind(PrintingState.PREVIEW_STATUS), expected="none"),
        StateCase(
            Text("NO PREVIEW").style(UiStyle.DIM),
            selector=bind(PrintingState.PREVIEW_STATUS), expected="failed"),
    ).margin(left=8, right=8, bottom=8).ref(PrintingRef.PREVIEW_BOX)

    return Frame(
        Column(
            Column(
                Text("PREVIEW").style(UiStyle.PRIMARY_LEFT).height("content"),
            ).height("content").padding(left=12, top=10, right=0, bottom=8),
            no_preview,
            gap=0,
        ),
    ).style(UiStyle.FRAME_PANEL).ref(PrintingRef.PREVIEW)


def _buttons():
    ready = bind(PrintingState.CONTROLS_READY)
    pending = bind(PrintingState.PENDING_ACTION)
    pause = Overlay(
        StateCase(
            Button(PAUSE, "PAUSE", state=derived(_pause_state, ready, pending)).style(UiStyle.BUTTON_BOLD_8),
            selector=bind(PrintingState.PAUSED), expected=False),
        StateCase(
            Button(RESUME, "RESUME", state=derived(_resume_state, ready, pending)).style(UiStyle.BUTTON_BOLD_8),
            selector=bind(PrintingState.PAUSED), expected=True),
    ).ref(PrintingRef.PAUSE)

    return Grid(
        matrix=((
            pause,
            Button(FILAMENT, "FILAMENT", state=derived(_enabled, ready))
            .style(UiStyle.BUTTON_BOLD_8).ref(PrintingRef.FILAMENT),
            Button(Z_ADJUST, "Z ADJUST", state=derived(_enabled, bind(PrintingState.LIVE_Z_ALLOWED)))
            .style(UiStyle.BUTTON_BOLD_8).ref(PrintingRef.Z_ADJUST),
            Button(CANCEL, "CANCEL", state="danger")
            .style(UiStyle.BUTTON_BOLD_8).ref(PrintingRef.CANCEL),
        ),),
        columns=Equal(4), rows=Equal(1), gap=(12, 0),
    ).margin(left=20, right=20).ref(PrintingRef.BUTTONS)


def create_page(bounds=PAGE_BOUNDS):
    compact = bounds.height < PAGE_BOUNDS.height
    header = Grid(
        matrix=((
            Button(HOME, "HOME").style(UiStyle.BUTTON_BOLD_8).ref(PrintingRef.HOME),
            Spacer(),
        ),),
        columns=(146, FLEX), rows=Equal(1), gap=0,
    ).padding(left=14, top=6, right=14, bottom=2).height(56).ref(PrintingRef.HEADER)

    content = Column(
        Grid(
            matrix=((_details(compact=compact), _preview()),),
            columns=(Flex(2), FLEX), rows=Equal(1), gap=(24, 0),
        ).margin(left=20, top=0, right=20, bottom=0).ref(PrintingRef.BODY),
        _buttons().height(48 if compact else 68),
        gap=18,
    )
    root = Column(header, content, gap=14) \
        .padding(left=0, top=0, right=0, bottom=10) \
        .ref(PrintingRef.ROOT)

    return PageTree(
        root, bounds, page_id=AppPage.PRINTING,
        styles=UI_STYLES,
        component_templates=(METRIC_PAIR,),
    )


PAGE = create_page()
