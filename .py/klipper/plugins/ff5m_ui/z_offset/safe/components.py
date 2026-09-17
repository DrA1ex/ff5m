## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
## This file may be distributed under the terms of the GNU GPLv3 license

## Reusable declarative components for Safe Z calibration.

from enum import Enum

from ui import ThemeColor
from ui.components import Frame, Text
from ui.layout import ComponentTemplate, Overlay, Param, param

from ...styles import UiStyle


class SafeComponent(Enum):
    VALUE_CARD = "safe.value_card"


LABEL = Param("label", field=Text.creation_contract.field("value"))
VALUE = Param("value", field=Text.creation_contract.field("value"))
REF = Param("ref")

VALUE_CARD = ComponentTemplate(
    SafeComponent.VALUE_CARD,
    parameters=(LABEL, VALUE, REF),
    root=Frame(
        Overlay(
            Text(param(LABEL)).style(UiStyle.PRIMARY)
            .height(20).margin(top=10).align(vertical="top"),
            Text(param(VALUE)).style(UiStyle.BRIGHT_BOLD_12)
            .height(20).margin(top=43).align(vertical="top"),
        ),
        border=ThemeColor.BORDER,
        background=ThemeColor.PANEL,
        line_width=2,
    ).ref(param(REF)).repaint_boundary(),
)
