## Shared component controls for mod and preview parameter lists.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from dataclasses import dataclass

from ui import (FLEX, ArrowButton, Button, ButtonSurface, Column, Command, CommandKey, Equal, Fill, Flex, Grid,
                Hitbox, Image, ItemCommand, ListView, NumericKeypad, Overlay, PageTree, Panel, Rect, Row,
                ScrollIndicator, Spacer, StateKey, Stroke, Template, Text, TextField, ThemeColor, ThemeRole,
                bind, item, state)
from ui.bindings import Binding
from ui.font_metrics import get_font_metrics
from ui.renderer import FeatherRenderer, HEADER_BOTTOM, SCREEN_HEIGHT, SCREEN_WIDTH
from ui.actions import Action
from ui.components import ToggleSwitch
from feather.settings import mod
from feather.screen.keyboard import TEXT_KEYBOARD, is_keyboard_action

from . import icons
from .keys import AppPage


ROW_LABEL_FONT = "JetBrainsMono Bold 8pt"
ROW_TEXT_FONT = "JetBrainsMono 8pt"
COMPACT_ROW_PADDING = 8
COMPACT_ROW_GAP = 4
TOGGLE_WIDTH = 76
TOGGLE_HEIGHT = 38
VALUE_WIDTH = 180
VALUE_HEIGHT = 46


class ParameterCommand(CommandKey):
    OPEN = "mod.parameter"
    # The payload is the option's index in the picker's options.
    OPTION = "mod.option"


class ParameterListState(StateKey):
    ROWS = state(list, default=[])
    PAGE = state(int, default=0)
    COUNT = state(int, default=1)
    PREVIOUS = state(str, default="disabled")
    NEXT = state(str, default="disabled")


class ParameterOptionsState(StateKey):
    ROWS = state(list, default=[])
    COUNTER = state(str, default="")
    PREVIOUS = state(str, default="disabled")
    NEXT = state(str, default="disabled")
    # The selected option's checkmark, normal and pressed: Image bands in the theme's colours.
    CHECK = state(tuple, default=())
    PRESSED_CHECK = state(tuple, default=())
    # The theme picker's preview of the selected theme.
    NAME = state(str, default="")
    DESCRIPTION = state(str, default="")


class ParameterValueState(StateKey):
    # The value being typed, as the editor shows it, and the text editor's cursor in it.
    VALUE = state(str, default="")
    CURSOR = state(int, default=0)


def parameter_action(key):
    return Command(ParameterCommand.OPEN, key).wire_id


def option_action(index):
    return Command(ParameterCommand.OPTION, index).wire_id


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


def content_bounds(renderer=None, kind="editor"):
    width = getattr(renderer, "screen_width", SCREEN_WIDTH)
    height = getattr(renderer, "screen_height", SCREEN_HEIGHT)
    footer_y = height - 36
    if kind == "list":
        return Rect(25, HEADER_BOTTOM + 21, width - 45, footer_y - 2 - HEADER_BOTTOM - 21)
    inset, bottom = (18, 9) if kind == "numeric" else (25, 7)
    return Rect(inset, HEADER_BOTTOM + 10, width - 2 * inset, footer_y - bottom - HEADER_BOTTOM - 10)


def list_width(bounds):
    return bounds.width - 52 - 13


def control_row_height(label, description, row_width):
    if row_width >= 600:
        return mod.ITEM_HEIGHT
    metrics = get_font_metrics()
    text_width = row_width - 2 * COMPACT_ROW_PADDING
    return (metrics.text_height(label, ROW_LABEL_FONT, max_width=text_width, wrap=True)
            + VALUE_HEIGHT
            + metrics.text_height(description, ROW_TEXT_FONT, max_width=text_width, wrap=True)
            + 2 * COMPACT_ROW_GAP + 2 * COMPACT_ROW_PADDING)


def control_row_pitch(label, description, row_width):
    return control_row_height(label, description, row_width) + mod.ITEM_PITCH - mod.ITEM_HEIGHT


def parameter_pitch(param, row_width):
    return control_row_pitch(str(param.label).upper(), mod.description(param), row_width)


def control_row(label, subtitle, description, control, row_width=mod.LIST_WIDTH):
    value = Overlay(control.align("right", "center")).width(VALUE_WIDTH)
    if row_width >= 600:
        content = Grid(
            matrix=((Column(
                Text(label, color=ThemeColor.PRIMARY, font=ROW_LABEL_FONT, horizontal="left", truncate=True).height(18),
                Text(subtitle, color=ThemeColor.DIM, font="JetBrainsMono 8pt", horizontal="left", truncate=True).height(18),
                Text(description, color=ThemeColor.TEXT, font=ROW_TEXT_FONT, horizontal="left", truncate=True).height(18),
                gap=0), value),),
            columns=(FLEX, VALUE_WIDTH), rows=(FLEX,), gap=15,
        ).padding(left=15, right=15, top=5, bottom=5)
    else:
        content = Column(
            Text(label, color=ThemeColor.PRIMARY, font=ROW_LABEL_FONT, horizontal="left", wrap=True).height("content"),
            Row(Text(subtitle, color=ThemeColor.DIM, font="JetBrainsMono 7pt", horizontal="left", truncate=True).grow(),
                value, gap=12).height(VALUE_HEIGHT),
            Text(description, color=ThemeColor.TEXT, font=ROW_TEXT_FONT, horizontal="left", wrap=True).height("content"),
            gap=COMPACT_ROW_GAP,
        ).padding(COMPACT_ROW_PADDING)
    height = ("content" if row_width < 600 and isinstance(label, Binding)
              else control_row_height(label, description, row_width))
    # A content height is the content column's: the overlay prefers its largest child's extent.
    return Overlay(Panel(border=ThemeColor.BORDER, background=ThemeColor.PANEL, line_width=1), content).height(height)


def category_band(label, counter, pitch, width):
    heading = Text(label, color=ThemeColor.PRIMARY, font="JetBrainsMono Bold 8pt", horizontal="left", truncate=True)
    if width >= 600:
        heading = Row(heading.width("content"), Fill(ThemeColor.BORDER).height(1).grow(), gap=10)
    return Row(Fill(ThemeColor.PRIMARY).width(4), heading.grow(),
               Text(counter, color=ThemeColor.DIM, font="JetBrainsMono 8pt", horizontal="right").width("content"),
               gap=10).height(pitch).padding(
                    top=pitch - mod.BAND_HEIGHT - mod.BAND_GAP_BELOW, bottom=mod.BAND_GAP_BELOW)


def next_category_card(caption):
    return Overlay(
        Panel(border=ThemeColor.BORDER, background=ThemeColor.PANEL, line_width=1),
        Text(caption, color=ThemeColor.TEXT, font="JetBrainsMono Bold 8pt", truncate=True).margin(left=20, right=20),
        Hitbox(ParameterAction("more")),
    ).height(mod.ITEM_HEIGHT)


def page_rail():
    return Column(
        ArrowButton(ParameterAction("prev"), "up", state=bind(ParameterListState.PREVIOUS)).height(48),
        ScrollIndicator(bind(ParameterListState.PAGE), bind(ParameterListState.COUNT)).width(10).grow().align("center").margin(top=10, bottom=10),
        ArrowButton(ParameterAction("next"), "down", state=bind(ParameterListState.NEXT)).height(48),
        gap=0,
    ).width(52)


def _list_template(kind, width):
    if kind in ("heading", "section"):
        return Template(kind, category_band(item("label"), item("counter"), mod.band_pitch(0 if kind == "heading" else 1), width))
    if kind == "next":
        return Template(kind, next_category_card(item("label")))
    action = ItemCommand(ParameterCommand.OPEN, item("parameter"))
    if kind == "toggle":
        control = ToggleSwitch(action, item("active"), enabled=item("enabled"),
                               progress=item("progress")).size(TOGGLE_WIDTH, TOGGLE_HEIGHT)
    elif kind == "value":
        control = Button(action, item("value"), state=item("state"), font=ROW_TEXT_FONT).size(VALUE_WIDTH, VALUE_HEIGHT)
    elif kind == "current":
        from .model_preview.actions import PreviewAction, PreviewRoute
        control = Button(PreviewAction(PreviewRoute.CAPTURE_VIEW), "USE CURRENT", font=ROW_TEXT_FONT).size(VALUE_WIDTH, VALUE_HEIGHT)
    else:
        raise ValueError("Unknown parameter list template: %s" % kind)
    return Template(kind, control_row(item("label"), item("subtitle"), item("description"), control,
                                     row_width=width).margin(bottom=mod.ITEM_PITCH - mod.ITEM_HEIGHT))


def create_list_tree(*, preview=False, bounds=None, kinds=None):
    bounds = bounds or content_bounds(kind="list")
    if kinds is None:
        kinds = ("heading", "section", "next", "toggle", "value") + (("current",) if preview else ())
    templates = tuple(_list_template(kind, list_width(bounds)) for kind in sorted(set(kinds or ("heading",))))
    rows = ListView(bind(ParameterListState.ROWS), item("id"), item("kind"), templates,
                    reuse_items=True).ref("settings.rows").height("content").grow().align(vertical="top")
    root = Row(rows, page_rail(), gap=13)
    return PageTree(root, bounds, page_id=AppPage.PREVIEW_SETTINGS if preview else AppPage.MOD_SETTINGS)


def ensure_list_templates(tree, state):
    # The ListView create_list_tree puts first in its root: the node itself, which a
    # frame the loop builds has not necessarily laid out on the reactor yet.
    rows = tree.root.items[0]
    known = set(template.name for template in rows.templates)
    missing = sorted(set(row["kind"] for row in state[ParameterListState.ROWS]) - known)
    if missing:
        rows.templates += tuple(_list_template(kind, list_width(tree.bounds)) for kind in missing)
        rows.template_names = tuple(template.name for template in rows.templates)


def list_state(manager, sections, page, page_count, next_label, leading_values=None):
    rows = []
    if leading_values is not None:
        label, subtitle, description = leading_values
        rows.append(dict(id="current", kind="current", label=label, subtitle=subtitle, description=description))
    for position, section in enumerate(sections):
        rows.append(dict(id="category:" + section.label, kind="heading" if position == 0 else "section",
                         label=section.label + (" (CONT.)" if section.continued else ""),
                         counter="%02d-%02d / %02d" % (section.first, section.first + len(section.items) - 1, section.total)))
        for _index, param in section.items:
            toggle = mod.parameter_kind(param) == "bool"
            enabled = not getattr(param, "readonly", False)
            value = manager.variables.get(param.key, param.default)
            rows.append(dict(id=param.key, parameter=param.key, kind="toggle" if toggle else "value",
                             label=str(param.label).upper(), subtitle=param.key, description=mod.description(param),
                             active=mod.bool_display_active(param, value) if toggle else False, progress=None,
                             enabled=enabled,
                             value=mod.display_value(manager, param) + " >", state="enabled" if enabled else "disabled"))
    if next_label is not None:
        rows.append(dict(id="next", kind="next", label="NEXT: %s >" % next_label))
    return {ParameterListState.ROWS: rows, ParameterListState.PAGE: page, ParameterListState.COUNT: page_count,
            ParameterListState.PREVIOUS: "enabled" if page else "disabled",
            ParameterListState.NEXT: "enabled" if page + 1 < page_count else "disabled"}


def _option_header_height(param, width):
    metrics = get_font_metrics()
    height = metrics.text_height(mod.description(param), "JetBrainsMono 8pt", max_width=width - 70, wrap=True)
    if mod.restart_effect(param) is not None:
        height += 2 + metrics.text_height("APPLYING THIS VALUE RESTARTS KLIPPER.", "JetBrainsMono 8pt",
                                         max_width=width - 70, wrap=True)
    return max(47, height)


def options_capacity(param, bounds):
    area = bounds.height - _option_header_height(param, bounds.width) - 47 - 16
    return max(1, (area + 8) // 66)


OPTION_TEXT_INSET = 52
OPTION_ICON_SIZE = 24


def _option_face(kind, pressed):
    """An option drawn as a button in its state, its caption kept clear of the
    checkmark a selected option shows at its left."""
    background, border, text = FeatherRenderer.BUTTON_COLORS["pressed" if pressed else kind]
    face = [Fill(background), Stroke(border, 2),
            Text(item("label"), color=text, font="JetBrainsMono 8pt", wrap=True, truncate=True).margin(
                left=OPTION_TEXT_INSET, right=OPTION_TEXT_INSET, top=6, bottom=6)]
    if kind == "selected":
        face.append(Image(bind(ParameterOptionsState.PRESSED_CHECK if pressed else ParameterOptionsState.CHECK)).size(OPTION_ICON_SIZE, OPTION_ICON_SIZE)
                    .align("left", "center").margin(left=22))
    return Overlay(*face)


def _choice_rows(height, gap):
    """The options of the shown page, written as ParameterOptionsState.ROWS."""
    templates = tuple(Template(kind, ButtonSurface(
        ItemCommand(ParameterCommand.OPTION, item("index")), _option_face(kind, False), _option_face(kind, True),
        enabled=kind != "disabled").height(height)) for kind in ("enabled", "selected", "disabled"))
    return ListView(bind(ParameterOptionsState.ROWS), item("id"), item("kind"), templates, gap=gap,
                    reuse_items=True).height("content").align(vertical="top")


def _option_values(renderer, pagination, selection, descriptions=True):
    """The options the page shows, and the checkmark coloured as the selected
    option's caption in the renderer's theme."""
    def check(face):
        return ((0, 0, icons.colored(renderer, "controls", "check", (FeatherRenderer.BUTTON_COLORS[face][2],))),)

    rows = []
    for row, option in enumerate(pagination.visible):
        index = pagination.start + row
        label = str(option.label).upper()
        if descriptions and option.description:
            label += " // " + str(option.description).upper()
        kind = "disabled" if not option.enabled else "selected" if option.value == selection else "enabled"
        rows.append(dict(id=str(index), kind=kind, index=index, label=label))
    return {ParameterOptionsState.ROWS: rows, ParameterOptionsState.CHECK: check("selected"),
            ParameterOptionsState.PRESSED_CHECK: check("pressed"),
            ParameterOptionsState.PREVIOUS: "enabled" if pagination.has_previous else "disabled",
            ParameterOptionsState.NEXT: "enabled" if pagination.has_next else "disabled"}


def _button(route, label, state="enabled"):
    return Button(ParameterAction(route), label, state=state, font="JetBrainsMono Bold 8pt")


def _options_footer(multipage):
    if not multipage:
        return Grid(matrix=((_button("cancel", "CANCEL", "danger"), _button("apply", "APPLY")),),
                    columns=Equal(2), rows=(FLEX,), gap=30)
    return Grid(matrix=((
        _button("options.prev", "<", bind(ParameterOptionsState.PREVIOUS)),
        _button("cancel", "CANCEL", "danger"), _button("apply", "APPLY"),
        _button("options.next", ">", bind(ParameterOptionsState.NEXT)),
    ),), columns=(Flex(1), Flex(2), Flex(2), Flex(1)), rows=(FLEX,), gap=12)


def create_options_page(param, multipage, bounds=None):
    """The picker of param's options; its values are options_values'."""
    bounds = bounds or content_bounds()
    texts = [Text(mod.description(param), color=ThemeColor.TEXT, font="JetBrainsMono 8pt", horizontal="left", wrap=True).grow()]
    if mod.restart_effect(param) is not None:
        texts.append(Text("APPLYING THIS VALUE RESTARTS KLIPPER.", color=ThemeColor.WARNING,
                          font="JetBrainsMono 8pt", horizontal="left", wrap=True).height("content"))
    header = Grid(matrix=((Column(*texts, gap=2),
                           Text(bind(ParameterOptionsState.COUNTER), color=ThemeColor.DIM, font="JetBrainsMono 8pt",
                                horizontal="right").align(vertical="top")),),
                  columns=(FLEX, 60), rows=(FLEX,), gap=10)
    root = Grid(matrix=((header,), (_choice_rows(58, 8),), (_options_footer(multipage),)),
                columns=(FLEX,), rows=(_option_header_height(param, bounds.width), FLEX, 47), gap=8)
    return PageTree(root, bounds, page_id=AppPage.PARAMETER_OPTIONS, state_schema=tuple(ParameterOptionsState))


def options_values(renderer, pagination, selection):
    counter = "%d/%d" % (pagination.page + 1, pagination.page_count) if pagination.page_count > 1 else ""
    return {**_option_values(renderer, pagination, selection), ParameterOptionsState.COUNTER: counter}


def _theme_content_height(bounds):
    return bounds.height - 8 - 47 - 11


def theme_capacity(bounds):
    height = _theme_content_height(bounds)
    if bounds.width < 700:
        height -= 120 + 12
    return max(1, (height + 6) // 50)


def _theme_sample(label, background, border, color):
    return Overlay(Panel(background=background, border=border, line_width=2),
                   Text(label, color=color, font="JetBrainsMono Bold 8pt", truncate=True))


def _theme_preview(compact=False):
    header = Column(
        Text(bind(ParameterOptionsState.NAME), color=ThemeColor.PRIMARY,
             font="JetBrainsMono Bold 8pt" if compact else "JetBrainsMono Bold 12pt", horizontal="left", truncate=True).grow(),
        Text(bind(ParameterOptionsState.DESCRIPTION), color=ThemeColor.DIM, font="JetBrainsMono 8pt", horizontal="left",
             truncate=True).grow(), gap=0,
    ).height(40 if compact else 44)
    samples = Grid(matrix=((
        _theme_sample("NORMAL", ThemeRole.BUTTON_BACKGROUND, ThemeRole.BUTTON_BORDER, ThemeRole.BUTTON_TEXT),
        _theme_sample("SELECTED", ThemeRole.BUTTON_SELECTED_BACKGROUND, ThemeRole.BUTTON_SELECTED_BORDER, ThemeRole.BUTTON_SELECTED_TEXT),
    ), (
        _theme_sample("ALERT", ThemeColor.DANGER_BACKGROUND, ThemeColor.DANGER, ThemeColor.DANGER),
        _theme_sample("READY", ThemeColor.PRIMARY_DARK, ThemeColor.SUCCESS, ThemeColor.SUCCESS),
    )), columns=Equal(2), rows=Equal(2), gap=(12 if compact else 20, 6 if compact else 18)).grow()
    children = [header]
    if not compact:
        dashboard = Overlay(
            Panel(background=ThemeColor.BACKGROUND, border=ThemeColor.BORDER),
            Column(Row(Text("PRINT READY", color=ThemeColor.PRIMARY, font="JetBrainsMono Bold 12pt", horizontal="left", truncate=True).grow(),
                       Text("ONLINE", color=ThemeColor.SUCCESS, font="JetBrainsMono Bold 8pt").width("content"), gap=12),
                   Text("NOZZLE 220 C  /  BED 60 C", color=ThemeColor.TEXT, font="JetBrainsMono 8pt", horizontal="left", truncate=True),
                   gap=4).padding(16),
        ).height(76)
        children.append(dashboard)
    children.append(samples)
    return Overlay(Panel(background=ThemeColor.PANEL, border=ThemeColor.BORDER),
                   Column(*children, gap=6 if compact else 16).padding(10 if compact else 20))


def create_theme_page(bounds=None, *, setup=False):
    """The theme picker with a preview of the selected theme; its values are theme_values'."""
    bounds = bounds or content_bounds()
    choices = _choice_rows(44, 6)
    compact = bounds.width < 700
    preview = _theme_preview(compact or _theme_content_height(bounds) < 290)
    counter = Text(bind(ParameterOptionsState.COUNTER), color=ThemeColor.DIM, font="JetBrainsMono 8pt")
    previous = _button("options.prev", "<", bind(ParameterOptionsState.PREVIOUS))
    following = _button("options.next", ">", bind(ParameterOptionsState.NEXT))
    cancel = Spacer() if setup else _button("cancel", "CANCEL", "danger")
    apply = _button("apply", "CONTINUE" if setup else "SAVE", "selected" if setup else "enabled")
    if compact:
        body = Grid(matrix=((choices,), (preview,)), columns=(FLEX,), rows=(FLEX, 120), gap=12)
        footer = Grid(matrix=((
            previous, counter, following, cancel, apply,
        ),), columns=(44, 40, 44, FLEX, FLEX), rows=(FLEX,), gap=8)
    else:
        body = Grid(matrix=((choices, preview),), columns=(Flex(27), Flex(46)), rows=(FLEX,), gap=20)
        pager = Grid(matrix=((previous, counter, following),), columns=(90, FLEX, 90), rows=(FLEX,))
        actions = Row(cancel.width(125), apply.width(125), gap=20)
        footer = Grid(matrix=((pager, actions.width("content").align(horizontal="right")),),
                      columns=(Flex(27), Flex(46)), rows=(FLEX,), gap=20)
    root = Grid(matrix=((body,), (footer,)), columns=(FLEX,), rows=(FLEX, 47), gap=11).padding(top=8)
    return PageTree(root, bounds, page_id=AppPage.PARAMETER_OPTIONS, state_schema=tuple(ParameterOptionsState))


def theme_values(renderer, pagination, selection):
    descriptions = {entry.value: entry.description for entry in pagination.items if entry.enabled}
    return {**_option_values(renderer, pagination, selection, descriptions=False),
            ParameterOptionsState.COUNTER: "%d/%d" % (pagination.page + 1, pagination.page_count),
            ParameterOptionsState.NAME: str(selection or "DEFAULT").replace("_", " "),
            ParameterOptionsState.DESCRIPTION: descriptions.get(selection, "").upper()}


def create_numeric_page(param, bounds=None):
    """The numeric editor of param; its value is numeric_values'."""
    spec = mod.numeric_input_spec(param)
    actions = {digit: ParameterAction("key", int(digit)) for digit in "0123456789"}
    actions.update({"backspace": ParameterAction("backspace"), "confirm": ParameterAction("save")})
    if spec.allows_decimal:
        actions["decimal"] = ParameterAction("dot")
    if spec.allows_negative:
        actions["sign"] = ParameterAction("sign")
    keypad = NumericKeypad(param.key, bind(ParameterValueState.VALUE), actions, subtitle=str(param.label), mode=spec,
                           confirm_label="SAVE")
    return PageTree(keypad, bounds or content_bounds(kind="numeric"), page_id=AppPage.MOD_VALUE)


def numeric_values(value):
    return {ParameterValueState.VALUE: str(value)}


@dataclass(frozen=True)
class TextKeyAction(Action):
    route: str
    token: str = None
    kind = "text_key_action"

    def __post_init__(self):
        if not is_keyboard_action(self.wire_id) or (self.route == "key") != (self.token is not None):
            raise ValueError("Invalid text keyboard action")

    @property
    def wire_id(self):
        return "keyboard.%s%s" % (self.route, "." + self.token if self.token is not None else "")

    def as_dict(self):
        return {"kind": self.kind, "route": self.route, "token": self.token}


def _text_keyboard(symbols, shift, width):
    compact = width < 600
    gap = 4 if compact else 7
    minimum_key_width = 2 * FeatherRenderer.BUTTON_TEXT_PADDING + get_font_metrics().text_width("W", "JetBrainsMono 8pt")
    columns = min(10, max(1, (width + gap) // (minimum_key_width + gap)))
    key_width = min(68, (width - (columns - 1) * gap) // columns)
    rows = []
    for keys in TEXT_KEYBOARD.rows(symbols, shift):
        for start in range(0, len(keys), columns):
            rows.append(Row(*(Button(TextKeyAction("key", token), label, font="JetBrainsMono 8pt").width(key_width)
                              for token, label in keys[start:start + columns]), gap=gap).width("content").align(horizontal="center"))
    def key(route, label, selected=False, font="JetBrainsMono 8pt"):
        return Button(TextKeyAction(route), label, state="selected" if selected else "enabled", font=font)
    controls = (key("shift", "SHIFT", shift), key("symbols", "ABC" if symbols else "123", symbols),
                key("left", "<", font="JetBrainsMono 12pt"), key("space", "SPACE"),
                key("right", ">", font="JetBrainsMono 12pt"), key("backspace", "BACKSPACE"))
    if compact:
        rows.extend((Grid(matrix=((controls[0], controls[1], controls[5]),), columns=Equal(3), rows=(FLEX,), gap=8),
                     Grid(matrix=((controls[2], controls[3], controls[4]),), columns=(Flex(1), Flex(3), Flex(1)), rows=(FLEX,), gap=8)))
        tracks = Equal(len(rows))
    else:
        rows.append(Grid(matrix=(controls,), columns=tuple(Flex(weight) for weight in (110, 90, 60, 200, 60, 180)),
                         rows=(FLEX,), gap=10))
        tracks = tuple(Flex(weight) for weight in (42, 42, 42, 43))
    return Grid(matrix=tuple((row,) for row in rows), columns=(FLEX,), rows=tracks, gap=gap)


def create_text_page(param, symbols, shift, bounds=None):
    """The text editor of param with its keyboard's layer; its value is text_values'."""
    bounds = bounds or content_bounds()
    compact = bounds.height < 350
    header = Column(
        Text(str(param.label).upper(), color=ThemeColor.PRIMARY,
             font="JetBrainsMono Bold 8pt" if compact else "JetBrainsMono Bold 12pt", horizontal="left", truncate=True).grow(),
        Row(Text(param.key, color=ThemeColor.DIM, font="JetBrainsMono 8pt", horizontal="left", truncate=True).grow(),
            Text(mod.description(param), color=ThemeColor.TEXT, font="JetBrainsMono 8pt", horizontal="left", truncate=True).grow(), gap=12).height(20),
        gap=0,
    )
    field = Overlay(Panel(background=ThemeColor.PANEL, border=ThemeColor.PRIMARY, line_width=2),
                    TextField(bind(ParameterValueState.VALUE), bind(ParameterValueState.CURSOR),
                              color=ThemeColor.PRIMARY).margin(left=17, right=17, top=2, bottom=2))
    footer = Grid(matrix=((_button("cancel", "CANCEL", "danger"), _button("save", "SAVE")),),
                  columns=Equal(2), rows=(FLEX,), gap=30)
    root = Grid(matrix=((header,), (field,), (Fill(ThemeColor.BACKGROUND),),
                        (_text_keyboard(symbols, shift, bounds.width),), (Fill(ThemeColor.BACKGROUND),), (footer,)),
                columns=(FLEX,), rows=(42 if compact else 55, 40 if compact else 53, 8, FLEX, 12, 47 if compact else 54))
    return PageTree(root, bounds, page_id=AppPage.MOD_VALUE)


def text_values(value, cursor):
    return {ParameterValueState.VALUE: str(value), ParameterValueState.CURSOR: int(cursor)}
