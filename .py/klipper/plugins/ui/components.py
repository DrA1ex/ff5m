## Reusable declarative Feather UI components.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from enum import Enum

from .theme import ThemeColor, ThemeRole

from .actions import Action, action_wire_id
from .bindings import resolve, resolve_deep
from .font_metrics import get_font_metrics
from .layout import (
    CreationContract, CreationIdentityContract, CreationSourceContract, Node, Rect,
    subdivision_positions,
)
from .numeric_input import NumericInputSpec
from .properties import (
    CreationFieldSpec, EditorSpec, Invalidation, PropertySpec, RewritePolicy, SourceSpec,
    ValidationSpec, property_schema,
)


def _property(name, runtime_type=object, default=None, kind="auto",
              label=None, group="Component", choices=(), catalog=None,
              minimum=None, maximum=None, nullable=False, bindings=("direct",),
              invalidation=Invalidation.PAINT, live=True, source=None,
              storage="attribute", source_position=None, source_index=None,
              runtime_name=None,
              runtime_index=None, rewrite=True,
              maximum_items=None, **metadata):
    policy = (RewritePolicy.LITERAL_OR_BINDING if rewrite
              else RewritePolicy.LOCKED)
    return PropertySpec(
        name, runtime_type, default=default, nullable=nullable,
        validation=ValidationSpec(
            minimum=minimum, maximum=maximum, choices=choices,
            maximum_items=maximum_items),
        editor=EditorSpec(
            kind, label=label or name.replace("_", " ").title(),
            group=group, choices=choices, catalog=catalog, **metadata),
        bindings=bindings, invalidation=invalidation, live=live,
        source=SourceSpec(
            name=source or name, position=source_position,
            index=source_index, storage=storage,
            runtime_name=runtime_name, runtime_index=runtime_index,
            policy=policy),
    )


def _text(name, default="", **kwargs):
    return _property(name, (str, int, float), default, kind="text", **kwargs)


def _number(name, default=0, integer=True, **kwargs):
    return _property(
        name, int if integer else (int, float), default,
        kind="number", **kwargs)


def _color(name, default=ThemeColor.PRIMARY, **kwargs):
    return _property(
        name, (ThemeColor, ThemeRole, str), default, kind="theme_color",
        catalog="theme_tokens", **kwargs)


def _select(name, choices, default, **kwargs):
    return _property(
        name, str, default, kind="select", choices=choices, **kwargs)


def _frozen(value):
    if isinstance(value, dict):
        return tuple(sorted((key, _frozen(item))
                            for key, item in value.items()))
    if isinstance(value, (tuple, list)):
        return tuple(_frozen(item) for item in value)
    return value


class ButtonStyle:
    """Typed button defaults accepted by ``Override.with_button_style``."""

    __slots__ = ("font", "layout")

    def __init__(self, font=None, layout=None):
        self.font = font
        self.layout = layout


class Component(Node):
    """Renderable leaf with automatic state-binding change detection."""

    def state_signature(self, state):
        values = []
        for name, value in self.__dict__.items():
            if name.startswith("_") or name in (
                    "key", "layout_options", "parent"):
                continue
            values.append((name, _frozen(resolve_deep(value, state))))
        return tuple(values)


class Fill(Component):
    covers_bounds = True
    property_schema = property_schema(_color("color", ThemeColor.BACKGROUND))

    def __init__(self, color, key=None):
        super().__init__(key=key)
        self.color = color

    def draw(self, renderer, state, bounds):
        return renderer.fill(*bounds, color=resolve(self.color, state))


class Stroke(Component):
    property_schema = property_schema(
        _color("color"),
        _number("line_width", 2, minimum=1, maximum=12))

    def __init__(self, color, line_width=2, key=None):
        super().__init__(key=key)
        self.color = color
        self.line_width = line_width

    def draw(self, renderer, state, bounds):
        return renderer.stroke(
            *bounds, color=resolve(self.color, state),
            line_width=resolve(self.line_width, state))


class Panel(Component):
    covers_bounds = True
    property_schema = property_schema(
        _color("border"), _color("background", ThemeColor.PANEL),
        _number("line_width", 2, minimum=0, maximum=12))

    def __init__(self, border=ThemeColor.PRIMARY, background=ThemeColor.PANEL,
                 line_width=2, key=None):
        super().__init__(key=key)
        self.border = border
        self.background = background
        self.line_width = line_width

    def draw(self, renderer, state, bounds):
        return renderer.panel(
            *bounds, border=resolve(self.border, state),
            background=resolve(self.background, state),
            line_width=resolve(self.line_width, state))


class Section(Component):
    covers_bounds = True
    property_schema = property_schema(
        _text("title", group="Content", live=True),
        _color("border", ThemeColor.BORDER, group="Appearance"))

    def __init__(self, title, border=ThemeColor.BORDER, key=None):
        super().__init__(key=key)
        self.title = title
        self.border = border

    def draw(self, renderer, state, bounds):
        return renderer.section_panel(
            resolve(self.title, state), *bounds,
            border=resolve(self.border, state))


class Button(Component):
    covers_bounds = True
    property_schema = property_schema(
        _property(
            "action", Action, None, kind="semantic_action",
            group="Behavior", bindings=(), live=False),
        _text("label", group="Content", live=True),
        _property(
            "subtitle", (str, tuple, list), None, kind="text_lines",
            group="Content", maximum_items=2, storage="kwargs", live=True,
            nullable=True),
        _property(
            "font", str, "JetBrainsMono 8pt", kind="select",
            group="Typography", catalog="fonts"),
        _select(
            "state", ("enabled", "disabled", "selected", "warning",
                      "danger", "busy"), "enabled", group="Behavior"),
        _color(
            "accent", None, group="Appearance", storage="kwargs",
            nullable=True),
        _select(
            "button_layout", ("center", "row"), "center",
            group="Behavior", source="layout", storage="kwargs",
            runtime_name="layout"))

    def __init__(self, action, label, state="enabled", key=None, **kwargs):
        super().__init__(key=key)
        if not isinstance(action, Action):
            raise TypeError("Button action must be a semantic Action")
        self.action = action
        self.label = label
        self.state = state
        self.font = kwargs.pop("font", None)
        self.kwargs = kwargs

    def apply_override(self, name, value):
        if name == "button_style" and isinstance(value, ButtonStyle):
            if self.font is None and value.font is not None:
                self.font = value.font
            if value.layout is not None and "layout" not in self.kwargs:
                self.kwargs["layout"] = value.layout

    def auto_gap_extent(self, direction, cross_extent=None):
        if direction == "vertical":
            return 48 + self.layout_options.padding.vertical
        return None

    def draw(self, renderer, state, bounds):
        kwargs = dict(
            (name, resolve(value, state))
            for name, value in self.kwargs.items())
        kwargs["state"] = resolve(self.state, state)
        if self.font is not None:
            kwargs["font"] = resolve(self.font, state)
        return renderer.button(
            resolve(self.action, state), *bounds,
            resolve(self.label, state), **kwargs)


class ArrowButton(Component):
    """Theme-aware vertical paging button rendered as geometry."""

    covers_bounds = True
    property_schema = property_schema(
        _property(
            "action", Action, None, kind="semantic_action",
            group="Behavior", bindings=(), live=False),
        _select("direction", ("up", "down"), "up", group="Content"),
        _select(
            "state", ("enabled", "disabled", "busy"), "enabled",
            group="Behavior"))

    def __init__(self, action, direction="up", state="enabled", key=None):
        super().__init__(key=key)
        if not isinstance(action, Action):
            raise TypeError("ArrowButton action must be a semantic Action")
        if direction not in ("up", "down"):
            raise ValueError("ArrowButton direction must be 'up' or 'down'")
        self.action = action
        self.direction = direction
        self.state = state

    def draw(self, renderer, state, bounds):
        return renderer.arrow_button(
            resolve(self.action, state), *bounds,
            direction=resolve(self.direction, state),
            state=resolve(self.state, state))


class ToggleSwitch(Component):
    """Rectangular boolean switch with renderer-owned hitbox and animation."""

    covers_bounds = True
    property_schema = property_schema(
        _property(
            "action", Action, None, kind="semantic_action",
            group="Behavior", bindings=(), live=False),
        _property("active", bool, False, kind="checkbox", group="Behavior"),
        _property("enabled", bool, True, kind="checkbox", group="Behavior"))

    def __init__(self, action, active=False, enabled=True, key=None):
        super().__init__(key=key)
        if not isinstance(action, Action):
            raise TypeError("ToggleSwitch action must be a semantic Action")
        self.action = action
        self.active = active
        self.enabled = enabled

    def draw(self, renderer, state, bounds):
        return renderer.toggle(
            resolve(self.action, state), *bounds,
            active=resolve(self.active, state),
            enabled=resolve(self.enabled, state))


class Hitbox(Component):
    canvas_selectable = False
    property_schema = property_schema(
        _property(
            "action", Action, None, kind="semantic_action",
            group="Behavior", bindings=(), live=False),
        _property(
            "continuous", bool, False, kind="checkbox", group="Behavior"))

    def __init__(self, action, continuous=False, key=None):
        super().__init__(key=key)
        if not isinstance(action, Action):
            raise TypeError("Hitbox action must be a semantic Action")
        self.action = action
        self.continuous = continuous

    def draw(self, renderer, state, bounds):
        return renderer.action_hitbox(
            resolve(self.action, state), *bounds,
            continuous=resolve(self.continuous, state))


class Text(Component):
    property_schema = property_schema(
        _property(
            "value", (str, int, float), "", kind="textarea",
            group="Content", multiline=True, live=True),
        _property(
            "font", str, "JetBrainsMono 8pt", kind="select",
            group="Typography", catalog="fonts"),
        _color("color", group="Appearance"),
        _select(
            "horizontal", ("left", "center", "right"), "center",
            group="Typography"),
        _select(
            "vertical", ("top", "center", "bottom"), "center",
            group="Typography"),
        _number(
            "max_width", None, minimum=1, maximum=4000, nullable=True,
            group="Text layout", storage="kwargs",
            invalidation=Invalidation.LAYOUT),
        _number(
            "max_height", None, minimum=1, maximum=4000, nullable=True,
            group="Text layout", storage="kwargs",
            invalidation=Invalidation.LAYOUT),
        _property(
            "wrap", bool, False, kind="checkbox", group="Text layout",
            storage="kwargs", invalidation=Invalidation.LAYOUT),
        _property(
            "truncate", bool, False, kind="checkbox", group="Text layout",
            storage="kwargs"),
        _property(
            "auto_height", bool, False, kind="checkbox", group="Text layout",
            invalidation=Invalidation.LAYOUT))

    def __init__(self, value, color=ThemeColor.PRIMARY, font=None,
                 horizontal="center", vertical="center", key=None,
                 auto_height=False, **kwargs):
        super().__init__(key=key)
        self.value = value
        self.color = color
        self.font = font
        self.horizontal = horizontal
        self.vertical = vertical
        self.auto_height = bool(auto_height)
        self.kwargs = kwargs

    def apply_override(self, name, value):
        if name == "font" and self.font is None:
            self.font = value
        elif name == "text_color" and self.color is None:
            self.color = value

    def _content_height(self, cross_extent=None, dynamic_minimum=False):
        metrics = get_font_metrics()
        font = self.font if isinstance(self.font, str) else "JetBrainsMono 8pt"
        metric = metrics.metric(font)
        wrap = bool(self.kwargs.get("wrap", False))
        width = None
        if wrap:
            width = self.kwargs.get("max_width")
            if width is None:
                width = (self.layout_options.width
                         if self.layout_options.width is not None
                         else cross_extent)
            if width is None and not dynamic_minimum:
                return None
            if width is not None:
                width = max(1, int(width) - self.layout_options.padding.horizontal)
        maximum = self.kwargs.get("max_height")
        if isinstance(self.value, (str, int, float)):
            text_height = metrics.text_height(
                self.value, font, max_width=width, wrap=wrap and width is not None)
        elif dynamic_minimum:
            text_height = metric.glyph_height
        else:
            return None
        height = text_height + self.layout_options.padding.vertical
        if maximum is not None:
            height = min(height, int(maximum))
        return max(metric.glyph_height, height)

    def preferred_extent(self, direction, cross_extent=None):
        wrap = bool(self.kwargs.get("wrap", False))
        if direction != "vertical" or not (self.auto_height or wrap):
            return None
        return self._content_height(cross_extent)

    def content_extent(self, direction, cross_extent=None):
        if direction != "vertical":
            return None
        return self._content_height(cross_extent, dynamic_minimum=True)

    def draw(self, renderer, state, bounds):
        horizontal = resolve(self.horizontal, state)
        vertical = resolve(self.vertical, state)
        if horizontal == "left":
            x = bounds.x
        elif horizontal == "right":
            x = bounds.right
        else:
            x = bounds.center_x
        if vertical == "top":
            y = bounds.y
        elif vertical == "bottom":
            y = bounds.bottom
        else:
            y = bounds.center_y
        renderer_vertical = "middle" if vertical == "center" else vertical
        kwargs = dict(
            (name, resolve(value, state))
            for name, value in self.kwargs.items())
        if kwargs.get("wrap"):
            kwargs.setdefault("max_width", max(1, bounds.width))
            kwargs.setdefault("max_height", max(1, bounds.height))
        return renderer.text(
            x, y, resolve(self.value, state), resolve(self.color, state),
            resolve(self.font, state) if self.font is not None
            else "JetBrainsMono 8pt",
            horizontal, renderer_vertical, **kwargs)


class Metric(Component):
    property_schema = property_schema(
        _text("label", group="Content", live=True),
        _text("value", group="Content", live=True),
        _text("unit", group="Content", live=True),
        _color(
            "label_color", ThemeColor.PRIMARY, group="Appearance", storage="kwargs"),
        _color(
            "value_color", ThemeColor.TEXT, group="Appearance", storage="kwargs"))

    def __init__(self, label, value, unit="", key=None, **kwargs):
        super().__init__(key=key)
        self.label = label
        self.value = value
        self.unit = unit
        self.kwargs = kwargs

    def draw(self, renderer, state, bounds):
        kwargs = dict(
            (name, resolve(value, state))
            for name, value in self.kwargs.items())
        return renderer.metric_row(
            bounds.x, bounds.center_y, bounds.width,
            resolve(self.label, state), resolve(self.value, state),
            resolve(self.unit, state), **kwargs)


class NumericKeypad(Component):
    """Reusable numeric entry window; the surrounding page owns its chrome."""

    covers_bounds = True
    property_schema = property_schema(
        _text("title", group="Content", live=True),
        _text("subtitle", group="Content", live=True),
        _text("value", group="Content", live=True),
        _select("mode", NumericInputSpec.MODES, "decimal", group="Behavior"),
        _number("minimum", None, integer=False, nullable=True,
                group="Validation"),
        _number("maximum", None, integer=False, nullable=True,
                group="Validation"),
        _number("max_length", 10, minimum=1, maximum=64,
                group="Validation"),
        _number("fraction_digits", None, minimum=0, maximum=12,
                nullable=True, group="Validation"),
        _text("confirm_label", "CONFIRM", group="Content", live=True),
        _color("border", ThemeColor.BORDER, group="Appearance"),
        _color("background", ThemeColor.PANEL, group="Appearance"),
        _color("title_color", ThemeColor.TEXT, group="Appearance"),
        _color("subtitle_color", ThemeColor.DIM, group="Appearance"),
        _color("input_border", ThemeColor.SECONDARY, group="Appearance"),
        _color("value_color", ThemeColor.BRIGHT, group="Appearance"))

    def __init__(self, title, value, actions, subtitle="", mode="decimal",
                 minimum=None, maximum=None, max_length=10,
                 fraction_digits=None, confirm_label="CONFIRM",
                 border=ThemeColor.BORDER, background=ThemeColor.PANEL,
                 title_color=ThemeColor.TEXT, subtitle_color=ThemeColor.DIM,
                 input_border=ThemeColor.SECONDARY,
                 value_color=ThemeColor.BRIGHT, key=None):
        super().__init__(key=key)
        if not isinstance(actions, dict):
            raise TypeError("NumericKeypad actions must be a dictionary")
        for name, action in actions.items():
            if not isinstance(action, Action):
                raise TypeError(
                    "NumericKeypad action %s must be a semantic Action" % name)
        self.title = title
        self.subtitle = subtitle
        self.value = value
        self.actions = dict(actions)
        # collect_actions already understands dialog-style button tuples.
        self.buttons = tuple((action, name, "enabled")
                             for name, action in self.actions.items())
        self.mode = mode
        self.minimum = minimum
        self.maximum = maximum
        self.max_length = max_length
        self.fraction_digits = fraction_digits
        self.confirm_label = confirm_label
        self.border = border
        self.background = background
        self.title_color = title_color
        self.subtitle_color = subtitle_color
        self.input_border = input_border
        self.value_color = value_color

    def draw(self, renderer, state, bounds):
        return renderer.numeric_keypad(
            *bounds, resolve(self.title, state), resolve(self.value, state),
            resolve_deep(self.actions, state),
            subtitle=resolve(self.subtitle, state),
            mode=resolve(self.mode, state),
            minimum=resolve(self.minimum, state),
            maximum=resolve(self.maximum, state),
            max_length=resolve(self.max_length, state),
            fraction_digits=resolve(self.fraction_digits, state),
            confirm_label=resolve(self.confirm_label, state),
            border=resolve(self.border, state),
            background=resolve(self.background, state),
            title_color=resolve(self.title_color, state),
            subtitle_color=resolve(self.subtitle_color, state),
            input_border=resolve(self.input_border, state),
            value_color=resolve(self.value_color, state))


class DotGrid(Component):
    property_schema = property_schema(
        _number("columns", 11, minimum=1, maximum=100, group="Grid"),
        _number("rows", 7, minimum=1, maximum=100, group="Grid"),
        _color("color", ThemeColor.DIM, group="Appearance"))

    def __init__(self, columns=11, rows=7, color=ThemeColor.MUTED, key=None):
        super().__init__(key=key)
        self.columns = columns
        self.rows = rows
        self.color = color

    def draw(self, renderer, state, bounds):
        return renderer.dot_grid(
            *bounds, columns=resolve(self.columns, state),
            rows=resolve(self.rows, state),
            color=resolve(self.color, state))


class CornerMarks(Component):
    property_schema = property_schema(
        _number("length", 12, minimum=1, maximum=1000),
        _color("color"))

    def __init__(self, length=12, color=ThemeColor.PRIMARY, key=None):
        super().__init__(key=key)
        self.length = length
        self.color = color

    def draw(self, renderer, state, bounds):
        return renderer.corner_marks(
            *bounds, length=resolve(self.length, state),
            color=resolve(self.color, state))


class Crosshair(Component):
    property_schema = property_schema(_color("color"))

    """One-pixel cross centered in the arranged bounds."""

    def __init__(self, color=ThemeColor.PRIMARY, key=None):
        super().__init__(key=key)
        self.color = color

    def draw(self, renderer, state, bounds):
        color = resolve(self.color, state)
        return [
            renderer.fill(
                bounds.center_x, bounds.y, 1, bounds.height + 1, color),
            renderer.fill(
                bounds.x, bounds.center_y, bounds.width + 1, 1, color),
        ]


class JoystickKnob(Component):
    property_schema = property_schema(
        _select("axis", ("xy", "z"), "xy", group="Behavior"),
        _number("size", 25, minimum=9, maximum=200, group="Knob"),
        _number(
            "edge_padding", 0, minimum=0, maximum=1000, group="Knob"),
        _number(
            "dirty_margin", 2, minimum=0, maximum=100, group="Knob"),
        _color("color", group="Appearance"),
        _color("background", ThemeColor.PANEL, group="Appearance"))

    """State-bound joystick indicator with local damage restoration.

    The component owns the fast moving part of a joystick surface. A normal
    page draw paints the centered knob. Later position binding changes are
    handled through the page dirty tree: only the old knob rectangle is
    restored and the new knob is painted. No controller-side draw commands or
    parallel cursor bookkeeping are required.
    """

    def __init__(self, axis="xy", position=None, active_action=None,
                 surface_ref=None, edge_padding=0, size=25, color=ThemeColor.PRIMARY,
                 background=ThemeColor.PANEL, dirty_margin=2, key=None):
        super().__init__(key=key)
        if axis not in ("xy", "z"):
            raise ValueError("Unknown joystick axis: %s" % axis)
        if active_action is not None and not isinstance(active_action, Action):
            raise TypeError("JoystickKnob active_action must be a semantic Action")
        self.axis = axis
        self.position = position
        self.active_action = active_action
        self.surface_ref = surface_ref
        self.edge_padding = edge_padding
        self.size = size
        self.color = color
        self.background = background
        self.dirty_margin = dirty_margin
        self._drawn_geometry = None

    @staticmethod
    def _normalized_size(value):
        value = max(9, int(value))
        return value + 1 if value % 2 == 0 else value

    def _position(self, state):
        position = resolve_deep(self.position, state)
        if position is None or self.active_action is None:
            return position
        action, x, y = position
        expected = resolve(self.active_action, state)
        expected = action_wire_id(expected) if isinstance(expected, Action) else (
            expected.value if isinstance(expected, Enum) else expected)
        action = action_wire_id(action) if isinstance(action, Action) else (
            action.value if isinstance(action, Enum) else action)
        return (x, y) if action == expected else None

    def state_signature(self, state):
        return (
            self.axis,
            _frozen(self._position(state)),
            self.active_action,
            self.surface_ref,
            int(resolve(self.edge_padding, state)),
            self._normalized_size(resolve(self.size, state)),
            resolve(self.color, state),
            resolve(self.background, state),
            int(resolve(self.dirty_margin, state)),
        )

    def _movement_bounds(self, layout, bounds):
        if self.axis == "xy" and self.surface_ref is not None:
            return layout.rect(self.surface_ref)
        return bounds

    def _geometry(self, state, bounds, layout):
        size = self._normalized_size(resolve(self.size, state))
        dirty_margin = int(resolve(self.dirty_margin, state))
        position = self._position(state)
        x, y = bounds.center
        if position is not None:
            movement = self._movement_bounds(layout, bounds)
            half = size // 2 + dirty_margin
            if self.axis == "xy":
                raw_x, raw_y = position
                x = max(movement.x + half,
                        min(movement.right - half, int(raw_x)))
                y = max(movement.y + half,
                        min(movement.bottom - half, int(raw_y)))
            else:
                _raw_x, raw_y = position
                edge = half + int(resolve(self.edge_padding, state))
                x = bounds.center_x
                y = max(bounds.y + edge,
                        min(bounds.bottom - edge, int(raw_y)))
        return x, y, size, resolve(self.color, state), self.axis

    def render(self, renderer, state, layout):
        bounds = layout.rect(self)
        geometry = self._geometry(state, bounds, layout)
        self._drawn_geometry = geometry
        x, y, size, color, axis = geometry
        return renderer.joystick_knob(x, y, axis, size, color)

    def render_dirty(self, renderer, state, layout):
        bounds = layout.rect(self)
        previous = self._drawn_geometry
        current = self._geometry(state, bounds, layout)
        if previous == current:
            return []
        if previous is None:
            return self.render(renderer, state, layout)
        commands = self._restore_previous(
            renderer, state, bounds, layout, previous)
        x, y, size, color, axis = current
        commands += renderer.joystick_knob(x, y, axis, size, color)
        self._drawn_geometry = current
        return commands

    def _restore_previous(self, renderer, state, bounds, layout, geometry):
        x, y, size, _color, axis = geometry
        half = size // 2 + int(resolve(self.dirty_margin, state))
        patch = Rect(x - half, y - half, half * 2 + 1, half * 2 + 1)
        background = resolve(self.background, state)
        color = resolve(self.color, state)
        commands = [renderer.fill(
            patch.x, patch.y, patch.width, patch.height, background)]
        if axis == "xy":
            grid = self._movement_bounds(layout, bounds)
            columns, rows, grid_color = 11, 7, ThemeColor.MUTED
            if self.surface_ref is not None:
                try:
                    surface = layout.node(self.surface_ref)
                except KeyError:
                    surface = None
                if isinstance(surface, DotGrid):
                    columns = int(resolve(surface.columns, state))
                    rows = int(resolve(surface.rows, state))
                    grid_color = resolve(surface.color, state)
            return commands + self._restore_xy(
                renderer, grid, patch, color, columns, rows, grid_color)
        return commands + self._restore_z(renderer, bounds, patch, color)

    @staticmethod
    def _restore_xy(renderer, grid, patch, color, columns, rows, grid_color):
        commands = renderer.dot_grid(
            *grid, columns=columns, rows=rows, color=grid_color,
            clip=patch.as_tuple())
        if patch.x <= grid.center_x < patch.right:
            top = max(patch.y, grid.y)
            bottom = min(patch.bottom, grid.bottom + 1)
            if top < bottom:
                commands.append(renderer.fill(
                    grid.center_x, top, 1, bottom - top, color))
        if patch.y <= grid.center_y < patch.bottom:
            left = max(patch.x, grid.x)
            right = min(patch.right, grid.right + 1)
            if left < right:
                commands.append(renderer.fill(
                    left, grid.center_y, right - left, 1, color))
        return commands

    @staticmethod
    def _restore_z(renderer, bounds, patch, color):
        track_left = bounds.x
        track_top = bounds.y
        track_right = bounds.right - 1
        track_bottom = bounds.bottom - 1
        line_top = max(patch.y, track_top)
        line_bottom = min(patch.bottom - 1, track_bottom)
        commands = []
        if line_top <= line_bottom:
            line_height = line_bottom - line_top + 1
            commands += [
                renderer.fill(
                    track_left, line_top, 1, line_height, color),
                renderer.fill(
                    track_right, line_top, 1, line_height, color),
            ]
        if patch.y <= track_top < patch.bottom:
            commands.append(renderer.fill(
                track_left, track_top, bounds.width, 1, color))
        if patch.y <= track_bottom < patch.bottom:
            commands.append(renderer.fill(
                track_left, track_bottom, bounds.width, 1, color))
        return commands


class VerticalScale(Component):
    property_schema = property_schema(
        _number("tick_gap", 20, minimum=0, maximum=1000, group="Scale"),
        _number("depth", 3, minimum=0, maximum=8, group="Scale"),
        _number(
            "tick_width_small", 5, minimum=1, maximum=100, group="Scale",
            source="tick_widths", source_index=0,
            runtime_name="tick_widths", runtime_index=0),
        _number(
            "tick_width_medium", 8, minimum=1, maximum=100, group="Scale",
            source="tick_widths", source_index=1,
            runtime_name="tick_widths", runtime_index=1),
        _number(
            "tick_width_large", 12, minimum=1, maximum=100, group="Scale",
            source="tick_widths", source_index=2,
            runtime_name="tick_widths", runtime_index=2),
        _color("tick_color", ThemeColor.DIM, group="Appearance"),
        _color("center_color", group="Appearance"))

    """Binary-subdivision ticks derived from the arranged track bounds."""

    def __init__(self, tick_gap=20, tick_widths=(5, 8, 12), depth=3,
                 tick_color=ThemeColor.DIM, center_color=ThemeColor.PRIMARY, key=None):
        super().__init__(key=key)
        self.tick_gap = tick_gap
        self.tick_widths = tuple(tick_widths)
        self.depth = depth
        self.tick_color = tick_color
        self.center_color = center_color
        if len(self.tick_widths) != 3:
            raise ValueError("VerticalScale requires three tick widths")

    def draw(self, renderer, state, bounds):
        tick_gap = int(resolve(self.tick_gap, state))
        depth = int(resolve(self.depth, state))
        tick_right = bounds.x - tick_gap - 1
        positions = subdivision_positions(bounds.y, bounds.height - 1, depth)
        divisions = 1 << depth
        commands = []
        for index, y, level in positions:
            if level in (-1, 0):
                width = self.tick_widths[2]
            elif level == 1:
                width = self.tick_widths[1]
            else:
                width = self.tick_widths[0]
            commands.append(renderer.fill(
                tick_right - width + 1, y, width, 1,
                resolve(self.center_color, state)
                if index == divisions // 2
                else resolve(self.tick_color, state)))
        return commands


class VerticalGauge(Component):
    property_schema = property_schema(
        _text("title", "LOAD", group="Content", live=True),
        _text("unavailable_title", "FORCE", group="Content", live=True),
        _text("unavailable_value", "N/A", group="Content", live=True),
        _number(
            "danger_above", None, integer=False, minimum=-1000000,
            maximum=1000000, nullable=True, group="Behavior"))

    """State-bound vertical gauge with a stable unavailable presentation."""

    covers_bounds = True

    def __init__(self, gauge, title="LOAD", unavailable_title="FORCE",
                 unavailable_value="N/A", danger_above=None, key=None):
        super().__init__(key=key)
        self.gauge = gauge
        self.title = title
        self.unavailable_title = unavailable_title
        self.unavailable_value = unavailable_value
        self.danger_above = danger_above

    def draw(self, renderer, state, bounds):
        gauge = resolve_deep(self.gauge, state)
        if gauge is None:
            commands = renderer.panel(
                *bounds, border=ThemeColor.BORDER, background=ThemeColor.PANEL,
                line_width=1)
            commands += [
                renderer.text(
                    bounds.center_x, bounds.y + 24,
                    resolve(self.unavailable_title, state), ThemeColor.PRIMARY,
                    "JetBrainsMono 8pt", "center", "middle"),
                renderer.text(
                    bounds.center_x, bounds.center_y,
                    resolve(self.unavailable_value, state), ThemeColor.DIM,
                    "JetBrainsMono 6pt", "center", "middle"),
            ]
            return commands
        value = float(gauge["value"])
        danger_above = resolve(self.danger_above, state)
        value_color = (
            ThemeColor.DANGER
            if danger_above is not None and value > danger_above
            else ThemeColor.PRIMARY)
        return renderer.vertical_gauge(
            *bounds, resolve(self.title, state), value,
            gauge["minimum"], gauge["maximum"], gauge.get("initial"),
            value_color=value_color)


class Dialog(Component):
    covers_bounds = True
    property_schema = property_schema(
        _text("title", group="Content", live=True),
        _property(
            "lines", (tuple, list), (), kind="text_lines", group="Content",
            maximum_items=4, live=True),
        _property(
            "buttons", (tuple, list), (), kind="dialog_buttons",
            group="Actions", rewrite=False, live=False,
            source="buttons", source_index=None),
        _select(
            "tone", ("info", "warning", "danger"), "warning",
            group="Appearance"),
        _property(
            "modal", bool, False, kind="checkbox", group="Behavior"))

    def __init__(self, title, lines, buttons, tone="warning", modal=False,
                 key=None, **kwargs):
        super().__init__(key=key)
        for button in buttons:
            if not isinstance(button, (tuple, list)) or not button:
                raise TypeError("Dialog buttons must be non-empty sequences")
            if not isinstance(button[0], Action):
                raise TypeError("Dialog button action must be a semantic Action")
        self.title = title
        self.lines = lines
        self.buttons = buttons
        self.tone = tone
        self.modal = modal
        self.kwargs = kwargs

    def draw(self, renderer, state, bounds):
        kwargs = dict(
            (name, resolve(value, state))
            for name, value in self.kwargs.items())
        return renderer.dialog(
            resolve(self.title, state), resolve_deep(self.lines, state),
            resolve_deep(self.buttons, state),
            x=bounds.x, y=bounds.y, width=bounds.width,
            height=bounds.height, tone=resolve(self.tone, state),
            modal=resolve(self.modal, state), **kwargs)


def _creation_field(spec, required=False):
    return CreationFieldSpec(
        spec.name, spec.runtime_type, required=required,
        default=spec.default if spec.has_default else None,
        nullable=spec.nullable, validation=spec.validation,
        editor=spec.editor, bindings=spec.bindings,
        invalidation=spec.invalidation, live=spec.live, source=spec.source)


def _publish_property_source_positions(component, **positions):
    """Declare the positional constructor grammar owned by the framework."""
    specs = {item.name: item for item in component.property_schema}
    for name, position in positions.items():
        specs[name].source.position = int(position)


def _action_creation(name="action", required=True):
    return CreationFieldSpec(
        name, Action, required=required, default=None,
        editor=EditorSpec(
            "semantic_action", label="Action", group="Behavior",
            catalog="actions"), bindings=(), nullable=not required)


def _publish_creation(component, names=(), extra=(), category="Components",
                      required=()):
    specs = {item.name: item for item in component.property_schema}
    required = set(str(value) for value in required)
    component.creation_contract = CreationContract(
        category, fields=tuple(extra) + tuple(
            _creation_field(specs[name], required=name in required)
            for name in names),
        source=CreationSourceContract(
            "core.keyword_call", identity=CreationIdentityContract()))


_publish_property_source_positions(Fill, color=0)
_publish_property_source_positions(Stroke, color=0, line_width=1)
_publish_property_source_positions(Panel, border=0, background=1, line_width=2)
_publish_property_source_positions(Section, title=0, border=1)
_publish_property_source_positions(Button, action=0, label=1, state=2)
_publish_property_source_positions(
    ArrowButton, action=0, direction=1, state=2)
_publish_property_source_positions(
    ToggleSwitch, action=0, active=1, enabled=2)
_publish_property_source_positions(Hitbox, action=0, continuous=1)
_publish_property_source_positions(
    Text, value=0, color=1, font=2, horizontal=3, vertical=4, auto_height=6)
_publish_property_source_positions(Metric, label=0, value=1, unit=2)
_publish_property_source_positions(
    NumericKeypad, title=0, value=1, subtitle=3, mode=4, minimum=5,
    maximum=6, max_length=7, fraction_digits=8, confirm_label=9,
    border=10, background=11, title_color=12, subtitle_color=13,
    input_border=14, value_color=15)
_publish_property_source_positions(DotGrid, columns=0, rows=1, color=2)
_publish_property_source_positions(CornerMarks, length=0, color=1)
_publish_property_source_positions(Crosshair, color=0)
_publish_property_source_positions(
    JoystickKnob, axis=0, edge_padding=4, size=5, color=6,
    background=7, dirty_margin=8)
_publish_property_source_positions(
    VerticalScale, tick_gap=0, tick_width_small=1, tick_width_medium=1,
    tick_width_large=1, depth=2, tick_color=3, center_color=4)
_publish_property_source_positions(
    VerticalGauge, title=1, unavailable_title=2, unavailable_value=3,
    danger_above=4)
_publish_property_source_positions(
    Dialog, title=0, lines=1, buttons=2, tone=3, modal=4)


_publish_creation(Fill, ("color",), required=("color",))
_publish_creation(Stroke, ("color", "line_width"))
_publish_creation(Panel, ("border", "background", "line_width"))
_publish_creation(Section, ("title", "border"))
_publish_creation(Button, (
    "label", "subtitle", "font", "state", "accent", "button_layout"),
    (_action_creation(),))
_publish_creation(ArrowButton, ("direction", "state"), (_action_creation(),))
_publish_creation(ToggleSwitch, ("active", "enabled"), (_action_creation(),))
_publish_creation(Hitbox, ("continuous",), (_action_creation(),))
_publish_creation(Text, (
    "value", "font", "color", "horizontal", "vertical", "max_width",
    "max_height", "wrap", "truncate", "auto_height"))
_publish_creation(Metric, ("label", "value", "unit"))
_publish_creation(NumericKeypad, (
    "title", "value", "subtitle", "mode", "minimum", "maximum",
    "max_length", "fraction_digits", "confirm_label", "border",
    "background", "title_color", "subtitle_color", "input_border",
    "value_color"), (
        CreationFieldSpec(
            "actions", dict, required=True,
            editor=EditorSpec(
                "semantic_action_map", label="Key actions", group="Behavior",
                keys=tuple("0123456789") + (
                    "decimal", "backspace", "confirm")),
            bindings=()),
    ), required=("title", "value"))
_publish_creation(DotGrid, ("columns", "rows", "color"))
_publish_creation(CornerMarks, ("length", "color"))
_publish_creation(Crosshair, ("color",))
_publish_creation(JoystickKnob, ("axis", "size", "color", "background"))
_publish_creation(VerticalScale, ("tick_gap", "depth", "tick_color", "center_color"))
