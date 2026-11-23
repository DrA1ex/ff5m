## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
## This file may be distributed under the terms of the GNU GPLv3 license

## Reusable declarative components for the Heat/Fan page.

from enum import Enum

from ui import ThemeColor
from ui.components import Frame, Text
from ui.layout import ComponentTemplate, Param, param

from ..styles import UiStyle


class HeatComponent(Enum):
    VALUE = "heat.value"


VALUE_TEXT = Param("value", field=Text.creation_contract.field("value"))
VALUE_COLOR = Param("color", field=Text.creation_contract.field("color"))

VALUE = ComponentTemplate(
    HeatComponent.VALUE,
    parameters=(VALUE_TEXT, VALUE_COLOR),
    root=Frame(
        Text(param(VALUE_TEXT), color=param(VALUE_COLOR)).style(UiStyle.TEXT_LEFT),
        background=ThemeColor.PANEL, line_width=0,
    ).repaint_boundary(),
)


def target_label(target):
    return "%.0f C" % target if target > 0 else "OFF"
