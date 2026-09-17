## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
## This file may be distributed under the terms of the GNU GPLv3 license

## Reusable declarative components for the active-print page.

from enum import Enum

from ui import ThemeColor
from ui.components import Fill, Text
from ui.layout import Cell, ComponentTemplate, FLEX, Grid, Param, param

from ..styles import UiStyle


class PrintingComponent(Enum):
    METRIC_PAIR = "printing.metric_pair"


LEFT_LABEL = Param("left_label", field=Text.creation_contract.field("value"))
LEFT_VALUE = Param("left_value", field=Text.creation_contract.field("value"))
LEFT_REF = Param("left_ref")
RIGHT_LABEL = Param("right_label", field=Text.creation_contract.field("value"))
RIGHT_VALUE = Param("right_value", field=Text.creation_contract.field("value"))
RIGHT_REF = Param("right_ref")


METRIC_PAIR = ComponentTemplate(
    PrintingComponent.METRIC_PAIR,
    parameters=(
        LEFT_LABEL,
        LEFT_VALUE,
        LEFT_REF,
        RIGHT_LABEL,
        RIGHT_VALUE,
        RIGHT_REF,
    ),
    root=Grid(
        cells=(
            Cell(Text(param(LEFT_LABEL)).style(UiStyle.PRIMARY_LEFT).height("content"), 0, 0),
            Cell(
                Text(param(LEFT_VALUE)).style(UiStyle.VALUE_LEFT)
                .height("content").ref(param(LEFT_REF)),
                0, 1,
            ),
            Cell(Fill(ThemeColor.BORDER).margin(top=8), 1, 0, row_span=2),
            Cell(
                Text(param(RIGHT_LABEL)).style(UiStyle.PRIMARY_LEFT)
                .height("content"),
                2, 0,
            ),
            Cell(
                Text(param(RIGHT_VALUE)).style(UiStyle.VALUE_LEFT)
                .height("content").ref(param(RIGHT_REF)),
                2, 1,
            ),
        ),
        columns=(FLEX, 1, FLEX),
        gap=(5, 0),
    ).height("content"),
)
