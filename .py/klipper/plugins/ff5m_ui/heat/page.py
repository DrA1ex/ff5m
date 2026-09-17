## Declarative Heat/Fan page.

from enum import Enum

from ui import ThemeColor, ThemeRole
from ui.bindings import bind, derived
from ui.components import Button, Text
from ui.layout import FLEX, Column, Equal, Grid, PageTree, Rect, Row

from ..keys import AppPage
from ..styles import UI_STYLES, UiStyle
from .actions import (
    BED_MINUS, BED_OFF, BED_PLUS, COOLDOWN, FAN_0, FAN_100, FAN_50,
    NOZZLE_MINUS, NOZZLE_OFF, NOZZLE_PLUS, preheat,
)
from .components import VALUE
from .state import HeatState


CONTENT = Rect(12, 64, 776, 364)


class HeatRef(Enum):
    ROOT = "heat.root"
    HEATERS = "heat.heaters"
    NOZZLE_LABEL = "heat.nozzle.label"
    NOZZLE_VALUE = "heat.nozzle.value"
    NOZZLE_MINUS = "heat.nozzle.minus"
    NOZZLE_PLUS = "heat.nozzle.plus"
    NOZZLE_OFF = "heat.nozzle.off"
    BED_LABEL = "heat.bed.label"
    BED_VALUE = "heat.bed.value"
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
    COOLDOWN = "heat.cooldown"


_PRESET_REFS = tuple("heat.preset.%d" % index for index in range(5))


def _temperature(current, target):
    return "%.1f / %.0f C" % (current, target)


def _fan_value(speed, available):
    return "%.0f%%" % speed if available else "N/A"


def _fan_color(available):
    return ThemeRole.TEMPERATURE_FAN if available else ThemeColor.DIM


def _fan_state(available):
    return "enabled" if available else "disabled"


def _heaters():
    return Grid(
        matrix=(
            (
                Text("NOZZLE", color=ThemeRole.TEMPERATURE_NOZZLE)
                .style(UiStyle.TEXT_LEFT).ref(HeatRef.NOZZLE_LABEL),
                VALUE(
                    instance_key=HeatRef.NOZZLE_VALUE,
                    value=derived(
                        _temperature, bind(HeatState.NOZZLE),
                        bind(HeatState.NOZZLE_TARGET)),
                    color=ThemeColor.TEXT,
                ).ref(HeatRef.NOZZLE_VALUE),
                Button(NOZZLE_MINUS, "-5", accent=ThemeRole.TEMPERATURE_NOZZLE)
                .style(UiStyle.BUTTON).ref(HeatRef.NOZZLE_MINUS),
                Button(NOZZLE_PLUS, "+5", accent=ThemeRole.TEMPERATURE_NOZZLE)
                .style(UiStyle.BUTTON).ref(HeatRef.NOZZLE_PLUS),
                Button(NOZZLE_OFF, "OFF", accent=ThemeRole.TEMPERATURE_NOZZLE)
                .style(UiStyle.BUTTON).ref(HeatRef.NOZZLE_OFF),
            ),
            (
                Text("BED", color=ThemeRole.TEMPERATURE_BED)
                .style(UiStyle.TEXT_LEFT).ref(HeatRef.BED_LABEL),
                VALUE(
                    instance_key=HeatRef.BED_VALUE,
                    value=derived(
                        _temperature, bind(HeatState.BED),
                        bind(HeatState.BED_TARGET)),
                    color=ThemeColor.TEXT,
                ).ref(HeatRef.BED_VALUE),
                Button(BED_MINUS, "-5", accent=ThemeRole.TEMPERATURE_BED)
                .style(UiStyle.BUTTON).ref(HeatRef.BED_MINUS),
                Button(BED_PLUS, "+5", accent=ThemeRole.TEMPERATURE_BED)
                .style(UiStyle.BUTTON).ref(HeatRef.BED_PLUS),
                Button(BED_OFF, "OFF", accent=ThemeRole.TEMPERATURE_BED)
                .style(UiStyle.BUTTON).ref(HeatRef.BED_OFF),
            ),
        ),
        columns=(150, FLEX, 92, 92, 92), rows=Equal(2), gap=(10, 8),
    ).ref(HeatRef.HEATERS)


def _fan():
    available = bind(HeatState.FAN_AVAILABLE)
    state = derived(_fan_state, available)
    return Grid(
        matrix=((
            Text("PART FAN", color=ThemeRole.TEMPERATURE_FAN)
            .style(UiStyle.TEXT_LEFT).ref(HeatRef.FAN_LABEL),
            VALUE(
                instance_key=HeatRef.FAN_VALUE,
                value=derived(_fan_value, bind(HeatState.FAN), available),
                color=derived(_fan_color, available),
            ).ref(HeatRef.FAN_VALUE),
            Button(FAN_0, "0%", state=state, accent=ThemeRole.TEMPERATURE_FAN)
            .style(UiStyle.BUTTON).ref(HeatRef.FAN_0),
            Button(FAN_50, "50%", state=state, accent=ThemeRole.TEMPERATURE_FAN)
            .style(UiStyle.BUTTON).ref(HeatRef.FAN_50),
            Button(FAN_100, "100%", state=state, accent=ThemeRole.TEMPERATURE_FAN)
            .style(UiStyle.BUTTON).ref(HeatRef.FAN_100),
        ),),
        columns=(150, FLEX, 92, 92, 92), rows=Equal(1), gap=(10, 0),
    ).ref(HeatRef.FAN_ROW)


def _presets(materials):
    if not materials:
        choices = Text("NO MATERIALS ENABLED").style(UiStyle.DIM) \
            .height(38).ref(HeatRef.EMPTY)
    else:
        gap = 10
        width = min(140, (740 - gap * (len(materials) - 1)) // len(materials))
        row_width = len(materials) * width + (len(materials) - 1) * gap
        choices = Row(
            *(Button(preheat(material), material).style(UiStyle.BUTTON)
              .width(width).ref(_PRESET_REFS[index])
              for index, material in enumerate(materials)),
            gap=gap,
        ).size(row_width, 38).align(horizontal="center").ref(HeatRef.PRESET_ROW)

    return Column(
        Text("PREHEAT PRESETS").style(UiStyle.PRIMARY)
        .height(18).ref(HeatRef.PRESET_TITLE),
        choices,
        gap=6,
    ).ref(HeatRef.PRESETS)


def create_page(materials=()):
    root = Column(
        _heaters().height(132),
        _fan().height(54),
        _presets(tuple(materials)).height(62),
        Button(COOLDOWN, "COOLDOWN", state="danger", font="JetBrainsMono 12pt")
        .height(48).ref(HeatRef.COOLDOWN),
        gap=None,
    ).padding(left=18, top=6, right=18, bottom=6).ref(HeatRef.ROOT)

    return PageTree(
        root, CONTENT, page_id=AppPage.HEAT,
        styles=UI_STYLES, component_templates=(VALUE,))


# Default declaration for framework page discovery; runtime factories retain their inputs.
PAGE = create_page()
