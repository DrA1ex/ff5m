## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
## This file may be distributed under the terms of the GNU GPLv3 license

## Reusable declarative components for the Heat/Fan page.

from enum import Enum

from ui import ThemeColor
from ui.components import Fill, Text
from ui.layout import ComponentTemplate, Overlay, Param, param

from ..styles import UiStyle


class HeatComponent(Enum):
    VALUE = "heat.value"


VALUE_TEXT = Param("value", field=Text.creation_contract.field("value"))
VALUE_COLOR = Param("color", field=Text.creation_contract.field("color"))

VALUE = ComponentTemplate(
    HeatComponent.VALUE,
    parameters=(VALUE_TEXT, VALUE_COLOR),
    root=Overlay(
        Fill(ThemeColor.BACKGROUND),
        Text(param(VALUE_TEXT), color=param(VALUE_COLOR)).style(UiStyle.VALUE),
    ).repaint_boundary(),
)
