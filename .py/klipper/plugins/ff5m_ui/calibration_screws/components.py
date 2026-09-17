## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
## This file may be distributed under the terms of the GNU GPLv3 license

## Reusable screw-result components.

from enum import Enum

from ui import ThemeColor
from ui.components import Text
from ui.layout import Column, ComponentTemplate, Overlay, Param, Spacer, StateCase, When, param


LABEL_FONT = "Roboto 8pt"
VALUE_FONT = "Roboto Bold 12pt"


class ScrewComponent(Enum):
    TOP = "screws.result.top"
    BOTTOM = "screws.result.bottom"


NAME = Param("name", field=Text.creation_contract.field("value"))
DIRECTION = Param("direction")
NOT_BASE = Param("not_base")
ADJUSTMENT = Param("adjustment", field=Text.creation_contract.field("value"))
ALIGN = Param("align", field=Text.creation_contract.field("horizontal"))
CARD_REF = Param("card_ref")
VALUE_REF = Param("value_ref")
PARAMS = (NAME, DIRECTION, NOT_BASE, ADJUSTMENT, ALIGN, CARD_REF, VALUE_REF)


TOP_SCREW = ComponentTemplate(
    ScrewComponent.TOP,
    parameters=PARAMS,
    root=Column(
        Column(
            Text(
                param(NAME), color=ThemeColor.BRIGHT, font=LABEL_FONT,
                horizontal=param(ALIGN), truncate=True,
            ).height(20),
            Overlay(
                StateCase(
                    Text(
                        "BASE (REFERENCE)", color=ThemeColor.DIM,
                        font=LABEL_FONT, horizontal=param(ALIGN), truncate=True),
                    selector=param(DIRECTION), expected="BASE"),
                When(
                    param(NOT_BASE),
                    Text(
                        param(ADJUSTMENT), color=ThemeColor.PRIMARY,
                        font=VALUE_FONT, horizontal=param(ALIGN), truncate=True)
                    .ref(param(VALUE_REF))),
            ).height(30),
            gap=0,
        ).height(50),
        Spacer(),
        gap=0,
    ).padding(top=22).ref(param(CARD_REF)),
)


BOTTOM_SCREW = ComponentTemplate(
    ScrewComponent.BOTTOM,
    parameters=PARAMS,
    root=Column(
        Spacer(),
        Column(
            Text(
                param(NAME), color=ThemeColor.BRIGHT, font=LABEL_FONT,
                horizontal=param(ALIGN), truncate=True,
            ).height(20),
            Overlay(
                StateCase(
                    Text(
                        "BASE (REFERENCE)", color=ThemeColor.DIM,
                        font=LABEL_FONT, horizontal=param(ALIGN), truncate=True),
                    selector=param(DIRECTION), expected="BASE"),
                When(
                    param(NOT_BASE),
                    Text(
                        param(ADJUSTMENT), color=ThemeColor.PRIMARY,
                        font=VALUE_FONT, horizontal=param(ALIGN), truncate=True)
                    .ref(param(VALUE_REF))),
            ).height(30),
            gap=0,
        ).height(50),
        gap=0,
    ).padding(bottom=19).ref(param(CARD_REF)),
)
