## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
## This file may be distributed under the terms of the GNU GPLv3 license

## Shared declarative styles used by Feather pages.

from enum import Enum

from ui import ThemeColor
from ui.components import Button, Frame, Text
from ui.styles import Style, StyleSheet


class UiStyle(Enum):
    TEXT = "ui.text"
    TEXT_LEFT = "ui.text.left"
    PRIMARY = "ui.text.primary"
    PRIMARY_LEFT = "ui.text.primary.left"
    DIM = "ui.text.dim"
    DIM_LEFT = "ui.text.dim.left"
    VALUE = "ui.text.value"
    VALUE_LEFT = "ui.text.value.left"
    BRIGHT_BOLD_12 = "ui.text.bright.bold12"
    BUTTON = "ui.button"
    BUTTON_BOLD_8 = "ui.button.bold8"
    BUTTON_BOLD_11 = "ui.button.bold11"
    BUTTON_BOLD_12 = "ui.button.bold12"
    FRAME_PANEL = "ui.frame.panel"


UI_STYLES = StyleSheet(
    Style(UiStyle.TEXT, target=Text, color=ThemeColor.TEXT, font="JetBrainsMono 8pt"),
    Style(UiStyle.TEXT_LEFT, target=Text, based_on=UiStyle.TEXT, horizontal="left"),
    Style(UiStyle.PRIMARY, target=Text, color=ThemeColor.PRIMARY, font="JetBrainsMono 8pt"),
    Style(UiStyle.PRIMARY_LEFT, target=Text, based_on=UiStyle.PRIMARY, horizontal="left"),
    Style(UiStyle.DIM, target=Text, color=ThemeColor.DIM, font="JetBrainsMono 8pt"),
    Style(UiStyle.DIM_LEFT, target=Text, based_on=UiStyle.DIM, horizontal="left"),
    Style(UiStyle.VALUE, target=Text, color=ThemeColor.TEXT, font="JetBrainsMono 12pt"),
    Style(UiStyle.VALUE_LEFT, target=Text, based_on=UiStyle.VALUE, horizontal="left"),
    Style(
        UiStyle.BRIGHT_BOLD_12,
        target=Text,
        color=ThemeColor.BRIGHT,
        font="JetBrainsMono Bold 12pt",
    ),
    Style(UiStyle.BUTTON, target=Button, font="JetBrainsMono 8pt"),
    Style(UiStyle.BUTTON_BOLD_8, target=Button, font="JetBrainsMono Bold 8pt"),
    Style(UiStyle.BUTTON_BOLD_11, target=Button, font="JetBrainsMono Bold 11pt"),
    Style(UiStyle.BUTTON_BOLD_12, target=Button, font="JetBrainsMono Bold 12pt"),
    Style(
        UiStyle.FRAME_PANEL,
        target=Frame,
        border=ThemeColor.BORDER,
        background=ThemeColor.PANEL,
        line_width=1,
    ),
)
