## Shared component controls for mod and preview parameter lists.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from dataclasses import dataclass

from ui import ArrowButton, Button, Column, Fill, Hitbox, NumericKeypad, Overlay, PageTree, Panel, Rect, Stroke, Text, ThemeColor
from ui.actions import Action
from ui.components import ToggleSwitch
from feather.settings import mod

from .keys import AppPage


@dataclass(frozen=True)
class ParameterAction(Action):
    route: str
    index: int = None
    kind = "parameter_action"

    def __post_init__(self):
        if self.route not in ("item", "prev", "next", "more", "option", "options.prev", "options.next",
                              "cancel", "apply", "save", "key", "backspace", "sign", "dot"):
            raise ValueError("Unknown parameter action")
        if self.route in ("item", "option", "key"):
            if type(self.index) is not int or self.index < 0:
                raise ValueError("An indexed parameter action requires a non-negative index")
            if self.route == "key" and self.index > 9:
                raise ValueError("A numeric key must be a digit")
        elif self.index is not None:
            raise ValueError("This parameter action does not accept an index")

    @property
    def wire_id(self):
        return "mod.%s%s" % (self.route, ".%d" % self.index if self.index is not None else "")

    def as_dict(self):
        return {"kind": self.kind, "route": self.route, "index": self.index}


def parameter_row(manager, param, index):
    action = ParameterAction("item", index)
    enabled = not getattr(param, "readonly", False)
    if mod.parameter_kind(param) == "bool":
        value = manager.variables.get(param.key, param.default)
        control = ToggleSwitch(action, mod.bool_display_active(param, value), enabled=enabled)
        control = control.size(76, 38).align("left", "top").offset(599, 13)
    else:
        control = Button(action, mod.display_value(manager, param) + " >",
                         state="enabled" if enabled else "disabled", font="JetBrainsMono 8pt")
        control = control.size(180, 46).align("left", "top").offset(495, 9)
    return control_row(str(param.label).upper(), param.key, mod.description(param), control)


def control_row(label, subtitle, description, control):
    return Overlay(
        Panel(border=ThemeColor.BORDER, background=ThemeColor.PANEL, line_width=1),
        Column(
            Text(label, color=ThemeColor.PRIMARY, font="JetBrainsMono Bold 8pt",
                 horizontal="left", truncate=True).height(18),
            Text(subtitle, color=ThemeColor.DIM, font="JetBrainsMono 8pt", horizontal="left").height(18),
            Text(description, color=ThemeColor.TEXT, font="JetBrainsMono 8pt",
                 horizontal="left", truncate=True).height(18),
            gap=0,
        ).size(430, 54).align("left", "top").offset(15, 5),
        control,
    ).height(mod.ITEM_HEIGHT)


def category_band(section, pitch, measure_text):
    label = section.label + (" (CONT.)" if section.continued else "")
    counter = "%02d-%02d / %02d" % (section.first, section.first + len(section.items) - 1, section.total)
    font = "JetBrainsMono 8pt"
    counter_width = measure_text(counter, font)
    label_width = mod.LIST_WIDTH - counter_width - 34
    line_x = 14 + min(label_width, measure_text(label, "JetBrainsMono Bold 8pt")) + 10
    top = pitch - mod.BAND_HEIGHT - mod.BAND_GAP_BELOW
    return Overlay(
        Fill(ThemeColor.PRIMARY).size(4, mod.BAND_HEIGHT).align("left", "top").offset(0, top),
        Text(label, color=ThemeColor.PRIMARY, font="JetBrainsMono Bold 8pt", horizontal="left", truncate=True)
        .size(label_width, mod.BAND_HEIGHT).align("left", "top").offset(14, top),
        Fill(ThemeColor.BORDER).size(max(0, 14 + label_width - line_x), 1)
        .align("left", "top").offset(line_x, top + mod.BAND_HEIGHT // 2),
        Text(counter, color=ThemeColor.DIM, font=font, horizontal="right")
        .size(counter_width, mod.BAND_HEIGHT).align("right", "top").offset(0, top),
    ).height(pitch)


def next_category_card(label):
    return Overlay(
        Panel(border=ThemeColor.BORDER, background=ThemeColor.PANEL, line_width=1),
        Text("NEXT: %s >" % label, color=ThemeColor.TEXT, font="JetBrainsMono Bold 8pt", truncate=True)
        .margin(left=20, right=20),
        Hitbox(ParameterAction("more")),
    ).height(mod.ITEM_HEIGHT)


def page_rail(page, page_count):
    thumb_height = max(18, 244 // page_count)
    thumb_y = 58 if page_count == 1 else 58 + (244 - thumb_height) * page // (page_count - 1)
    return Overlay(
        ArrowButton(ParameterAction("prev"), "up", state="enabled" if page else "disabled")
        .size(52, 48).align("left", "top"),
        ArrowButton(ParameterAction("next"), "down", state="enabled" if page + 1 < page_count else "disabled")
        .size(52, 48).align("left", "top").offset(0, 312),
        Stroke(ThemeColor.BORDER, 1).size(10, 244).align("left", "top").offset(21, 58),
        Fill(ThemeColor.PRIMARY).size(6, max(4, thumb_height - 4))
        .align("left", "top").offset(23, thumb_y + 2),
    ).size(52, 360).align("right", "top")


def create_list_page(manager, sections, page, page_count, next_label, *, preview=False, measure_text, leading_row=None):
    rows = [leading_row.margin(bottom=mod.ITEM_PITCH - mod.ITEM_HEIGHT)] if leading_row is not None else []
    for position, section in enumerate(sections):
        rows.append(category_band(section, mod.band_pitch(position), measure_text))
        rows.extend(parameter_row(manager, param, index).margin(bottom=mod.ITEM_PITCH - mod.ITEM_HEIGHT)
                    for index, param in section.items)
    if next_label is not None:
        rows.append(next_category_card(next_label))
    root = Overlay(Column(*rows, gap=0).width(mod.LIST_WIDTH).height("content").align("left", "top"),
                   page_rail(page, page_count))
    return PageTree(root, Rect(mod.LIST_X, mod.LIST_TOP, 755, mod.LIST_BOTTOM - mod.LIST_TOP),
                    page_id=AppPage.PREVIEW_SETTINGS if preview else AppPage.MOD_SETTINGS)


def create_options_page(param, pagination, selection):
    choices = []
    for row, option in enumerate(pagination.visible):
        selected = option.enabled and option.value == selection
        label = str(option.label).upper()
        if option.description:
            label += " // " + str(option.description).upper()
        if selected:
            label += "  [SELECTED]"
        choices.append(Button(ParameterAction("option", pagination.start + row), label,
                              state="disabled" if not option.enabled else "selected" if selected else "enabled",
                              font="JetBrainsMono 8pt").height(58).margin(bottom=8))
    restart = mod.restart_effect(param)
    children = [
        Text(mod.description(param), color=ThemeColor.TEXT, font="JetBrainsMono 8pt",
             horizontal="left", wrap=True, truncate=True).size(650, 28 if restart else 44).align("left", "top"),
        Column(*choices, gap=0).height("content").align("left", "top").offset(0, 55),
    ]
    if restart is not None:
        children.append(Text("APPLYING THIS VALUE RESTARTS KLIPPER.", color=ThemeColor.WARNING,
                             font="JetBrainsMono 8pt", horizontal="left")
                        .height(22).align("left", "top").offset(0, 32))
    if pagination.page_count > 1:
        buttons = (("options.prev", "<", "enabled" if pagination.has_previous else "disabled", 0, 120),
                   ("cancel", "CANCEL", "danger", 130, 220), ("apply", "APPLY", "enabled", 400, 220),
                   ("options.next", ">", "enabled" if pagination.has_next else "disabled", 630, 120))
        children.append(Text("%d/%d" % (pagination.page + 1, pagination.page_count),
                             color=ThemeColor.DIM, font="JetBrainsMono 8pt", horizontal="right")
                        .size(100, 30).align("left", "top").offset(625, 0))
    else:
        buttons = (("cancel", "CANCEL", "danger", 0, 360), ("apply", "APPLY", "enabled", 390, 360))
    children.extend(Button(ParameterAction(route), label, state=state, font="JetBrainsMono Bold 8pt")
                    .size(width, 47).align("left", "top").offset(x, 325)
                    for route, label, state, x, width in buttons)
    return PageTree(Overlay(*children), Rect(25, 65, 750, 372), page_id=AppPage.PARAMETER_OPTIONS)


def create_numeric_page(param, value):
    spec = mod.numeric_input_spec(param)
    actions = {digit: ParameterAction("key", int(digit)) for digit in "0123456789"}
    actions.update({"backspace": ParameterAction("backspace"), "confirm": ParameterAction("save")})
    if spec.allows_decimal:
        actions["decimal"] = ParameterAction("dot")
    if spec.allows_negative:
        actions["sign"] = ParameterAction("sign")
    keypad = NumericKeypad(param.key, value, actions, subtitle=param.label, mode=spec, confirm_label="SAVE")
    return PageTree(keypad, Rect(18, 65, 764, 370), page_id=AppPage.MOD_VALUE)
