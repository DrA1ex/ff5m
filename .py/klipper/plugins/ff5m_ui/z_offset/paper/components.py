## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
## This file may be distributed under the terms of the GNU GPLv3 license

## Reusable declarative components for the paper Z-offset page.

from enum import Enum

from ui import ThemeColor
from ui.components import Panel, Text
from ui.layout import ComponentTemplate, Overlay, Param, param

from ...styles import UiStyle


class PaperComponent(Enum):
    VALUE_CARD = "paper.value_card"


LABEL = Param("label", field=Text.creation_contract.field("value"))
VALUE = Param("value", field=Text.creation_contract.field("value"))
CARD_REF = Param("card_ref")
PANEL_REF = Param("panel_ref")
LABEL_REF = Param("label_ref")
VALUE_REF = Param("value_ref")

VALUE_CARD = ComponentTemplate(
    PaperComponent.VALUE_CARD,
    parameters=(LABEL, VALUE, CARD_REF, PANEL_REF, LABEL_REF, VALUE_REF),
    root=Overlay(
        Panel(
            border=ThemeColor.BORDER,
            background=ThemeColor.PANEL,
            line_width=2,
        ).ref(param(PANEL_REF)),
        Text(param(LABEL)).style(UiStyle.PRIMARY)
        .height(20).margin(top=8).align(vertical="top").ref(param(LABEL_REF)),
        Text(param(VALUE)).style(UiStyle.BRIGHT_BOLD_12)
        .height(20).margin(top=39).align(vertical="top").ref(param(VALUE_REF)),
    ).ref(param(CARD_REF)).repaint_boundary(),
)
