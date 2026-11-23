## Declarative Heat/Fan page.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
## This file may be distributed under the terms of the GNU GPLv3 license

from enum import Enum

from ui import Command, PAGE_CHROME_CONTRACT, ThemeColor, ThemeRole
from ui.bindings import bind, derived
from ui.components import Button, ButtonSurface, Frame, Text
from ui.layout import FLEX, Column, Equal, Grid, PageTree, Rect, Row

from ..keys import AppPage
from ..numeric_keypad import create_keypad_popup
from ..styles import UI_STYLES, UiStyle
from .actions import (
    APPLY_TARGET, BED_MINUS, BED_OFF, BED_PLUS, CANCEL_TARGET, COOLDOWN,
    EDIT_BED_TARGET, EDIT_NOZZLE_TARGET, FAN_0, FAN_100, FAN_50, HeatCommand,
    NOZZLE_MINUS, NOZZLE_OFF, NOZZLE_PLUS, preheat,
)
from .components import VALUE, target_label
from .state import HeatState, HeatTargetState


class HeatRef(Enum):
    ROOT = "heat.root"
    HEATERS = "heat.heaters"
    NOZZLE_CARD = "heat.nozzle.card"
    NOZZLE_LABEL = "heat.nozzle.label"
    NOZZLE_VALUE = "heat.nozzle.value"
    NOZZLE_TARGET = "heat.nozzle.target"
    NOZZLE_MINUS = "heat.nozzle.minus"
    NOZZLE_PLUS = "heat.nozzle.plus"
    NOZZLE_OFF = "heat.nozzle.off"
    BED_CARD = "heat.bed.card"
    BED_LABEL = "heat.bed.label"
    BED_VALUE = "heat.bed.value"
    BED_TARGET = "heat.bed.target"
    BED_MINUS = "heat.bed.minus"
    BED_PLUS = "heat.bed.plus"
    BED_OFF = "heat.bed.off"
    FAN_ROW = "heat.fan.row"
    FAN_LABEL = "heat.fan.label"
    FAN_VALUE = "heat.fan.value"
    FAN_0 = "heat.fan.0"
    FAN_50 = "heat.fan.50"
    FAN_100 = "heat.fan.100"
    PRESETS = "heat.presets"
    PRESET_TITLE = "heat.presets.title"
    PRESET_ROW = "heat.presets.row"
    EMPTY = "heat.presets.empty"
    FOOTER = "heat.footer"
    COOLDOWN = "heat.cooldown"


def _temperature(current):
    return "%.1f C" % current


def _temperature_readout(label, content):
    return Column(Text(label).style(UiStyle.DIM_LEFT).height(22), content)


def _heater_summary(label, accent, current, target, background, refs=None):
    heading = Text(label, color=accent, font="JetBrainsMono Bold 10pt", horizontal="left")
    current_readout = _temperature_readout("CURRENT", Text(current).style(UiStyle.VALUE_LEFT))
    target_readout = _temperature_readout("TARGET", Row(
        Text(target).style(UiStyle.VALUE_LEFT).width("content"),
        Text("›").style(UiStyle.TEXT_LEFT).width("content"), gap=12,
    ))
    if refs is not None:
        label_ref, value_ref, target_ref = refs
        heading.ref(label_ref)
        current_readout.ref(value_ref)
        target_readout.ref(target_ref)
    readings = Grid(matrix=((current_readout, target_readout),), columns=Equal(2), rows=(FLEX,), gap=12)
    return Frame(Column(heading.height(22), readings, gap=4), background=background, line_width=0)


def _fan_value(speed, available):
    return "%.0f%%" % speed if available else "N/A"


def _fan_state(available):
    return "enabled" if available else "disabled"


def _heater_card(label, accent, current, target, actions, refs):
    card_ref, label_ref, value_ref, target_ref, minus_ref, plus_ref, off_ref = refs
    edit_target, minus, plus, off = actions
    current_value = derived(_temperature, bind(current))
    target_value = derived(target_label, bind(target))
    summary = ButtonSurface(
        edit_target,
        _heater_summary(label, accent, current_value, target_value, ThemeColor.PANEL,
                        refs=(label_ref, value_ref, target_ref)),
        _heater_summary(label, accent, current_value, target_value, ThemeColor.PRESSED_BACKGROUND),
    )
    controls = Grid(matrix=((
        Button(minus, "-5").style(UiStyle.BUTTON).ref(minus_ref),
        Button(plus, "+5").style(UiStyle.BUTTON).ref(plus_ref),
        Button(off, "OFF").style(UiStyle.BUTTON).ref(off_ref),
    ),), columns=Equal(3), rows=(FLEX,), gap=12)
    content = Column(summary, controls.height(48), gap=4)
    return Frame(content).style(UiStyle.FRAME_PANEL) \
        .padding(left=18, top=16, right=18, bottom=16).ref(card_ref)


def _heaters():
    return Grid(
        matrix=((
            _heater_card(
                "NOZZLE", ThemeRole.TEMPERATURE_NOZZLE, HeatState.NOZZLE, HeatState.NOZZLE_TARGET,
                (EDIT_NOZZLE_TARGET, NOZZLE_MINUS, NOZZLE_PLUS, NOZZLE_OFF),
                (HeatRef.NOZZLE_CARD, HeatRef.NOZZLE_LABEL, HeatRef.NOZZLE_VALUE, HeatRef.NOZZLE_TARGET,
                 HeatRef.NOZZLE_MINUS, HeatRef.NOZZLE_PLUS, HeatRef.NOZZLE_OFF),
            ),
            _heater_card(
                "BED", ThemeRole.TEMPERATURE_BED, HeatState.BED, HeatState.BED_TARGET,
                (EDIT_BED_TARGET, BED_MINUS, BED_PLUS, BED_OFF),
                (HeatRef.BED_CARD, HeatRef.BED_LABEL, HeatRef.BED_VALUE, HeatRef.BED_TARGET,
                 HeatRef.BED_MINUS, HeatRef.BED_PLUS, HeatRef.BED_OFF),
            ),
        ),),
        columns=Equal(2), rows=(FLEX,), gap=16,
    ).ref(HeatRef.HEATERS)


def _fan():
    available = bind(HeatState.FAN_AVAILABLE)
    state = derived(_fan_state, available)
    content = Grid(
        matrix=((
            Row(
                Text("PART FAN", color=ThemeRole.TEMPERATURE_FAN)
                .style(UiStyle.TEXT_LEFT).width("content").ref(HeatRef.FAN_LABEL),
                VALUE(
                    instance_key=HeatRef.FAN_VALUE,
                    value=derived(_fan_value, bind(HeatState.FAN), available),
                    color=ThemeRole.TEMPERATURE_FAN,
                ).grow(1).ref(HeatRef.FAN_VALUE),
                gap=12,
            ),
            Button(FAN_0, "0%", state=state)
            .style(UiStyle.BUTTON).ref(HeatRef.FAN_0),
            Button(FAN_50, "50%", state=state)
            .style(UiStyle.BUTTON).ref(HeatRef.FAN_50),
            Button(FAN_100, "100%", state=state)
            .style(UiStyle.BUTTON).ref(HeatRef.FAN_100),
        ),),
        columns=(FLEX, 92, 92, 92), rows=(FLEX,), gap=12,
    )
    return Frame(content).style(UiStyle.FRAME_PANEL) \
        .padding(left=18, top=10, right=18, bottom=10).ref(HeatRef.FAN_ROW)


def _presets(materials):
    if not materials:
        choices = Text("NO MATERIALS ENABLED").style(UiStyle.DIM_LEFT).ref(HeatRef.EMPTY)
    else:
        buttons = tuple(
            Button(preheat(material), material).style(UiStyle.BUTTON).ref("heat.preset.%d" % index)
            for index, material in enumerate(materials)
        )
        choices = Grid(matrix=(buttons,), columns=Equal(len(buttons)), rows=(FLEX,), gap=12) \
            .ref(HeatRef.PRESET_ROW)

    return Column(
        Text("PREHEAT PRESETS").style(UiStyle.DIM_LEFT).height(22).ref(HeatRef.PRESET_TITLE),
        choices,
        gap=4,
    ).ref(HeatRef.PRESETS)


def _footer(materials):
    return Grid(matrix=((
        _presets(materials),
        Button(COOLDOWN, "STOP HEATING", state="danger")
        .style(UiStyle.BUTTON_BOLD_8).height(48).align(vertical="bottom").ref(HeatRef.COOLDOWN),
    ),), columns=(FLEX, 168), rows=(FLEX,), gap=16).ref(HeatRef.FOOTER)


def create_page(materials=(), bounds=None):
    if bounds is None:
        bounds = Rect(*PAGE_CHROME_CONTRACT.content_bounds())
    root = Grid(
        matrix=((_heaters(),), (_fan(),), (_footer(tuple(materials)),)),
        columns=(FLEX,), rows=(FLEX, 68, 74), gap=16,
    ).padding(left=12, top=10, right=12, bottom=14).ref(HeatRef.ROOT)

    return PageTree(
        root, bounds, page_id=AppPage.HEAT,
        styles=UI_STYLES, component_templates=(VALUE,))


def target_editor_page(nozzle, spec, bounds):
    actions = {key: Command(HeatCommand.TARGET_KEY, key) for key in "0123456789"}
    actions["backspace"] = Command(HeatCommand.TARGET_KEY, "backspace")
    actions["confirm"] = APPLY_TARGET
    actions["cancel"] = CANCEL_TARGET
    return create_keypad_popup(
        "NOZZLE TARGET" if nozzle else "BED TARGET", bind(HeatTargetState.DRAFT), actions, bounds,
        subtitle="RANGE %d-%d C" % (spec.minimum, spec.maximum), spec=spec, confirm_label="APPLY",
    )


# Default declaration for framework page discovery; runtime factories retain their inputs.
PAGE = create_page()
