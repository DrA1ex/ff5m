## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
## This file may be distributed under the terms of the GNU GPLv3 license

## Reusable dashboard components for the home page.

from enum import Enum

from ui import ThemeColor
from ui.components import Fill, Frame, Text
from ui.layout import ComponentTemplate, Overlay, Param, param

from ..styles import UiStyle


class HomeComponent(Enum):
    TEMPERATURE_CARD = "home.temperature_card"
    NETWORK_CARD = "home.network_card"
    BOTTOM_VALUE = "home.bottom_value"


X = Param("x")
TITLE = Param("title", field=Text.creation_contract.field("value"))
BORDER = Param("border", field=Frame.creation_contract.field("border"))
VALUE = Param("value", field=Text.creation_contract.field("value"))
STATUS = Param("status", field=Text.creation_contract.field("value"))
STATUS_COLOR = Param("status_color", field=Text.creation_contract.field("color"))
REF = Param("ref")

TEMPERATURE_CARD = ComponentTemplate(
    HomeComponent.TEMPERATURE_CARD,
    parameters=(X, TITLE, BORDER, VALUE, STATUS, STATUS_COLOR, REF),
    root=Frame(
        Overlay(
            Text(param(TITLE), color=param(BORDER)).style(UiStyle.TEXT)
            .size(235, 40).offset(0, 8),
            Overlay(
                Fill(ThemeColor.PANEL),
                Text(param(VALUE)).style(UiStyle.VALUE)
                .size(229, 34).offset(0, 10),
                Text(param(STATUS), color=param(STATUS_COLOR)).style(UiStyle.TEXT)
                .size(229, 34).offset(0, 52),
            ).size(229, 87).offset(3, 40).repaint_boundary(),
        ),
        border=param(BORDER), background=ThemeColor.PANEL, line_width=2,
    ).size(235, 132).offset(param(X), 72).ref(param(REF)),
)


NETWORK_NAME = Param("name", field=Text.creation_contract.field("value"))
NETWORK_ADDRESS = Param("address", field=Text.creation_contract.field("value"))

NETWORK_CARD = ComponentTemplate(
    HomeComponent.NETWORK_CARD,
    parameters=(NETWORK_NAME, NETWORK_ADDRESS, REF),
    root=Frame(
        Overlay(
            Text("NETWORK").style(UiStyle.PRIMARY)
            .size(236, 40).offset(0, 8),
            Overlay(
                Fill(ThemeColor.PANEL),
                Text(
                    param(NETWORK_NAME),
                    max_width=210, truncate=True,
                ).style(UiStyle.TEXT).size(230, 34).offset(0, 10),
                Text(
                    param(NETWORK_ADDRESS),
                    max_width=210, truncate=True,
                ).style(UiStyle.PRIMARY).size(230, 34).offset(0, 52),
            ).size(230, 87).offset(3, 40).repaint_boundary(),
        ),
        border=ThemeColor.PRIMARY, background=ThemeColor.PANEL, line_width=2,
    ).size(236, 132).offset(539, 72).ref(param(REF)),
)


BOTTOM_VALUE_TEXT = Param("value", field=Text.creation_contract.field("value"))
BOTTOM_VALUE_COLOR = Param("color", field=Text.creation_contract.field("color"))
BOTTOM_X = Param("x")
BOTTOM_WIDTH = Param("width")
BOTTOM_TEXT_X = Param("text_x")
BOTTOM_MAX_WIDTH = Param("max_width", field=Text.creation_contract.field("max_width"))
BOTTOM_REF = Param("ref")

BOTTOM_VALUE = ComponentTemplate(
    HomeComponent.BOTTOM_VALUE,
    parameters=(
        BOTTOM_VALUE_TEXT, BOTTOM_VALUE_COLOR, BOTTOM_X, BOTTOM_WIDTH,
        BOTTOM_TEXT_X, BOTTOM_MAX_WIDTH, BOTTOM_REF,
    ),
    root=Overlay(
        Fill(ThemeColor.BACKGROUND),
        Text(
            param(BOTTOM_VALUE_TEXT), color=param(BOTTOM_VALUE_COLOR),
            max_width=param(BOTTOM_MAX_WIDTH), truncate=True,
        ).style(UiStyle.TEXT_LEFT).size(param(BOTTOM_MAX_WIDTH), 24).offset(param(BOTTOM_TEXT_X), 5),
    ).repaint_boundary().size(param(BOTTOM_WIDTH), 34)
     .offset(param(BOTTOM_X), 393).ref(param(BOTTOM_REF)),
)
