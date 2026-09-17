## Declarative Z-offset paper test page for Feather.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from enum import Enum

from ui.actions import SetValue
from ui.bindings import bind, derived
from ui.components import Button, Dialog, VerticalGauge
from ui.layout import (
    FLEX, Column, Equal, Grid, Overlay, PageTree as Page, Spacer, When,
)
from ...keys import AppPage
from ...styles import UI_STYLES, UiStyle
from ..actions import ACCEPT, CLOSER, FARTHER, MOVE_SAFE_HALF, PROBE, RESET
from ..common import CONTENT
from ..constants import PAPER_STEPS, Z_WEIGHT_DANGER
from .components import VALUE_CARD
from .state import PaperState


PAGE_ID = AppPage.Z_OFFSET_PAPER


class PaperRef(Enum):
    ROOT = "paper.root"
    LAYOUT = "paper.layout"
    CONTROLS = "paper.controls"
    CARDS = "paper.cards"
    SPACER_1 = "paper.spacer.1"
    SPACER_2 = "paper.spacer.2"
    SPACER_3 = "paper.spacer.3"
    SPACER_4 = "paper.spacer.4"
    REFERENCE = "paper.reference"
    REFERENCE_PANEL = "paper.reference.panel"
    REFERENCE_LABEL = "paper.reference.label"
    REFERENCE_VALUE = "paper.reference.value"
    NOZZLE = "paper.nozzle"
    NOZZLE_PANEL = "paper.nozzle.panel"
    NOZZLE_LABEL = "paper.nozzle.label"
    NOZZLE_VALUE = "paper.nozzle.value"
    CANDIDATE = "paper.candidate"
    CANDIDATE_PANEL = "paper.candidate.panel"
    CANDIDATE_LABEL = "paper.candidate.label"
    CANDIDATE_VALUE = "paper.candidate.value"
    START = "paper.start"
    PROBE = "paper.probe"
    MOVE_SAFE_HALF = "paper.move_safe_half"
    STEPS = "paper.steps"
    ADJUST = "paper.adjust"
    CLOSER = "paper.closer"
    FARTHER = "paper.farther"
    FINISH = "paper.finish"
    RESET = "paper.reset"
    ACCEPT = "paper.accept"
    GAUGE_LAYOUT = "paper.gauge.layout"
    GAUGE = "paper.gauge"
    PRESSURE = "paper.pressure"
    PRESSURE_DIALOG = "paper.pressure.dialog"


def _step_ref(step):
    return "paper.step.%03d" % round(float(step) * 1000.0)


def _cards():
    return Grid(
        matrix=((
            VALUE_CARD(
                instance_key=PaperRef.REFERENCE,
                label=derived(
                    lambda manual: "REFERENCE Z" if manual else "TRIGGER Z",
                    bind(PaperState.MANUAL)),
                value=derived(lambda value: "%s MM" % value, bind(PaperState.REFERENCE)),
                card_ref=PaperRef.REFERENCE, panel_ref=PaperRef.REFERENCE_PANEL,
                label_ref=PaperRef.REFERENCE_LABEL, value_ref=PaperRef.REFERENCE_VALUE),
            VALUE_CARD(
                instance_key=PaperRef.NOZZLE,
                label="NOZZLE Z",
                value=derived(lambda value: "%s MM" % value, bind(PaperState.NOZZLE)),
                card_ref=PaperRef.NOZZLE, panel_ref=PaperRef.NOZZLE_PANEL,
                label_ref=PaperRef.NOZZLE_LABEL, value_ref=PaperRef.NOZZLE_VALUE),
            VALUE_CARD(
                instance_key=PaperRef.CANDIDATE,
                label="Z OFFSET",
                value=derived(lambda value: "%s MM" % value, bind(PaperState.CANDIDATE)),
                card_ref=PaperRef.CANDIDATE, panel_ref=PaperRef.CANDIDATE_PANEL,
                label_ref=PaperRef.CANDIDATE_LABEL, value_ref=PaperRef.CANDIDATE_VALUE),
        ),),
        columns=Equal(3), rows=Equal(1), gap=(15, 0),
    ).padding(right=10).ref(PaperRef.CARDS)


def _probe_state(probing, moving):
    return "busy" if probing else "disabled" if moving else "danger"


def _move_state(probing, moving):
    return "busy" if moving else "disabled" if probing else "enabled"


def _start():
    return Grid(
        matrix=((
            Button(
                PROBE, "PROBE",
                state=derived(
                    _probe_state,
                    bind(PaperState.PROBING),
                    bind(PaperState.MOVING_TO_START)),
            ).style(UiStyle.BUTTON_BOLD_12).ref(PaperRef.PROBE),
            Button(
                MOVE_SAFE_HALF,
                derived(lambda height: "MOVE TO %.3f MM" % height,
                        bind(PaperState.MANUAL_START)),
                state=derived(
                    _move_state,
                    bind(PaperState.PROBING),
                    bind(PaperState.MOVING_TO_START)),
            ).style(UiStyle.BUTTON_BOLD_12).ref(PaperRef.MOVE_SAFE_HALF),
        ),),
        columns=Equal(2), rows=Equal(1), gap=(20, 0),
    ).ref(PaperRef.START)


def _steps():
    return Grid(
        matrix=(tuple(
            Button(
                SetValue(PaperState.STEP, step), "%.3f MM" % step,
                state=derived(
                    lambda current, expected=step:
                    "selected" if current == expected else "enabled",
                    bind(PaperState.STEP)),
            ).style(UiStyle.BUTTON).ref(_step_ref(step))
            for step in PAPER_STEPS),),
        columns=Equal(len(PAPER_STEPS)), rows=Equal(1), gap=(8, 0),
    ).padding(right=8).ref(PaperRef.STEPS)


def _ready_state(ready):
    return "enabled" if ready else "disabled"


def _adjust():
    adjust_state = derived(_ready_state, bind(PaperState.READY))
    return Grid(
        matrix=((
            Button(
                CLOSER,
                derived(lambda step: "CLOSER  -%.3f" % step,
                        bind(PaperState.STEP)),
                state=adjust_state,
            ).style(UiStyle.BUTTON_BOLD_12).ref(PaperRef.CLOSER),
            Button(
                FARTHER,
                derived(lambda step: "FARTHER  +%.3f" % step,
                        bind(PaperState.STEP)),
                state=adjust_state,
            ).style(UiStyle.BUTTON_BOLD_12).ref(PaperRef.FARTHER),
        ),),
        columns=Equal(2), rows=Equal(1), gap=(20, 0),
    ).ref(PaperRef.ADJUST)


def _finish():
    adjust_state = derived(_ready_state, bind(PaperState.READY))
    return Grid(
        matrix=((
            Button(RESET, "RESET TO 0.000", state=adjust_state).style(UiStyle.BUTTON).ref(PaperRef.RESET),
            Button(ACCEPT, "ACCEPT ZONE", state=adjust_state, font="JetBrainsMono Bold 10pt").ref(PaperRef.ACCEPT),
        ),),
        columns=(205, FLEX), rows=Equal(1), gap=(20, 0),
    ).ref(PaperRef.FINISH)


def _content():
    controls = Column(
        _cards().height(72),
        Spacer().grow(12).ref(PaperRef.SPACER_1),
        _start().height(70),
        Spacer().grow(14).ref(PaperRef.SPACER_2),
        _steps().height(48),
        Spacer().grow(14).ref(PaperRef.SPACER_3),
        _adjust().height(66),
        Spacer().grow(14).ref(PaperRef.SPACER_4),
        _finish().height(48),
    ).padding(top=14, bottom=14).ref(PaperRef.CONTROLS)
    gauge = Grid(
        matrix=((None,), (VerticalGauge(
            bind(PaperState.GAUGE), danger_above=Z_WEIGHT_DANGER,
        ).ref(PaperRef.GAUGE).repaint_boundary(),), (None,)),
        columns=Equal(1), rows=(16, FLEX, 12),
    ).ref(PaperRef.GAUGE_LAYOUT)
    layout = Grid(
        matrix=((controls, gauge),),
        columns=(FLEX, 70), rows=Equal(1), gap=(20, 0),
    ).padding(left=20, right=20).ref(PaperRef.LAYOUT)
    pressure = When(
        derived(lambda dialog: dialog == "pressure", bind(PaperState.DIALOG)),
        Dialog(
            "HIGH BED PRESSURE",
            derived(
                lambda weight: (
                    "CURRENT LOAD: %.0F G" % weight,
                    "MOVE FARTHER AND CHECK THE PAPER / NOZZLE.",
                ),
                bind(PaperState.DIALOG_WEIGHT)),
            ((SetValue(PaperState.DIALOG, None), "OK", "danger"),),
            tone="danger", modal=True,
        ).size(610, 260).margin(top=56)
         .align(horizontal="center", vertical="top")
         .ref(PaperRef.PRESSURE_DIALOG),
    ).ref(PaperRef.PRESSURE)
    return Overlay(layout, pressure).ref(PaperRef.ROOT)


PAGE = Page(
    _content(), CONTENT, page_id=PAGE_ID, styles=UI_STYLES,
    component_templates=(VALUE_CARD,))


def render(renderer, values):
    return PAGE.draw(renderer, values)


def update_gauge(renderer, gauge):
    return PAGE.update(renderer, {PaperState.GAUGE: gauge})
