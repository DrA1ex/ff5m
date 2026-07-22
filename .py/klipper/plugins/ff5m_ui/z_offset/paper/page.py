## Declarative Z-offset paper test page for Feather.

from enum import Enum

from ui.actions import SetValue
from ui.bindings import bind, derived
from ui.components import Button, Dialog, Panel, Text, VerticalGauge
from ui.layout import Column, Equal, Flex, Grid, Overlay, PageTree as Page, Spacer, When
from ...keys import AppPage
from ..actions import ACCEPT, CLOSER, FARTHER, MOVE_1_5, PROBE, RESET
from ..common import (
    CONTENT, FONT, PAPER_STEPS, Z_WEIGHT_DANGER, compact,
)
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
    MOVE_1_5 = "paper.move_1_5"
    STEPS = "paper.steps"
    STEP_005 = "paper.step.005"
    STEP_010 = "paper.step.010"
    STEP_025 = "paper.step.025"
    STEP_050 = "paper.step.050"
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


_STEP_REFS = dict(zip(PAPER_STEPS, (
    PaperRef.STEP_005, PaperRef.STEP_010,
    PaperRef.STEP_025, PaperRef.STEP_050,
)))


def _value_card(label, value_binding, refs):
    return Overlay(
        Panel(border="295c66", background="050c0f", line_width=2)
        .ref(refs[1]),
        Text(label, color="35d9e6", font=FONT)
        .height(20).margin(top=8).align(vertical="top").ref(refs[2]),
        Text(
            derived(lambda value: "%s MM" % value, value_binding),
            color="ffffff", font="JetBrainsMono Bold 12pt",
        ).height(20).margin(top=39).align(vertical="top").ref(refs[3]),
    ).ref(refs[0]).repaint_boundary()


def _cards():
    return Grid(
        matrix=((
            _value_card(
                derived(
                    lambda manual: "REFERENCE Z" if manual else "TRIGGER Z",
                    bind(PaperState.MANUAL)),
                bind(PaperState.REFERENCE),
                (PaperRef.REFERENCE, PaperRef.REFERENCE_PANEL,
                 PaperRef.REFERENCE_LABEL, PaperRef.REFERENCE_VALUE)),
            _value_card(
                "NOZZLE Z", bind(PaperState.NOZZLE),
                (PaperRef.NOZZLE, PaperRef.NOZZLE_PANEL,
                 PaperRef.NOZZLE_LABEL, PaperRef.NOZZLE_VALUE)),
            _value_card(
                "Z OFFSET", bind(PaperState.CANDIDATE),
                (PaperRef.CANDIDATE, PaperRef.CANDIDATE_PANEL,
                 PaperRef.CANDIDATE_LABEL, PaperRef.CANDIDATE_VALUE)),
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
                font="JetBrainsMono Bold 12pt",
            ).ref(PaperRef.PROBE),
            Button(
                MOVE_1_5, "MOVE TO 1.5 MM",
                state=derived(
                    _move_state,
                    bind(PaperState.PROBING),
                    bind(PaperState.MOVING_TO_START)),
                font="JetBrainsMono Bold 12pt",
            ).ref(PaperRef.MOVE_1_5),
        ),),
        columns=Equal(2), rows=Equal(1), gap=(20, 0),
    ).ref(PaperRef.START)


def _steps():
    return compact(Grid(
        matrix=(tuple(
            Button(
                SetValue(PaperState.STEP, step), "%.3f MM" % step,
                state=derived(
                    lambda current, expected=step:
                    "selected" if current == expected else "enabled",
                    bind(PaperState.STEP)),
            ).ref(_STEP_REFS[step])
            for step in PAPER_STEPS),),
        columns=Equal(4), rows=Equal(1), gap=(10, 0),
    )).padding(right=8).ref(PaperRef.STEPS)


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
                state=adjust_state, font="JetBrainsMono Bold 12pt",
            ).ref(PaperRef.CLOSER),
            Button(
                FARTHER,
                derived(lambda step: "FARTHER  +%.3f" % step,
                        bind(PaperState.STEP)),
                state=adjust_state, font="JetBrainsMono Bold 12pt",
            ).ref(PaperRef.FARTHER),
        ),),
        columns=Equal(2), rows=Equal(1), gap=(20, 0),
    ).ref(PaperRef.ADJUST)


def _finish():
    adjust_state = derived(_ready_state, bind(PaperState.READY))
    return Grid(
        matrix=((
            Button(
                RESET, "RESET TO 0.000",
                state=adjust_state, font=FONT,
            ).ref(PaperRef.RESET),
            Button(
                ACCEPT, "ACCEPT ZONE",
                state=adjust_state, font="JetBrainsMono Bold 10pt",
            ).ref(PaperRef.ACCEPT),
        ),),
        columns=(Flex(205), Flex(445)), rows=Equal(1), gap=(20, 0),
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
        columns=Equal(1), rows=(16, Flex(1), 12),
    ).ref(PaperRef.GAUGE_LAYOUT)
    layout = Grid(
        matrix=((controls, gauge),),
        columns=(Flex(670), 70), rows=Equal(1), gap=(20, 0),
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


PAGE = Page(_content(), CONTENT, page_id=PAGE_ID)


def render(renderer, values):
    return PAGE.draw(renderer, values)


def update_gauge(renderer, gauge):
    return PAGE.update(renderer, {PaperState.GAUGE: gauge})
