## Typed declarative style support for Feather UI.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from enum import Enum

from .layout import Insets, LAYOUT_SCHEMA, Node
from .properties import Invalidation, inheritable_style_properties
from .source import (
    capture_construction, construction_metadata, style_property_provenance,
)


def _style_id(value):
    if not isinstance(value, Enum):
        raise TypeError("Style identities must be Enum members")
    return value.value


def _component_specs(node_type):
    return dict((spec.name, spec) for spec in getattr(node_type, "property_schema", ()))


def _layout_specs():
    return dict((spec.name, spec) for spec in LAYOUT_SCHEMA)


def _inheritable_specs():
    # Context-style semantics are registered centrally and are therefore
    # independent of the order in which component modules are imported.
    return inheritable_style_properties()


def _layout_value(node, name):
    options = node.layout_options
    if name == "margin":
        value = options.margin
        return (value.left, value.top, value.right, value.bottom)
    if name == "padding":
        value = options.padding
        return (value.left, value.top, value.right, value.bottom)
    if name == "offset":
        return (options.offset_x, options.offset_y)
    return getattr(options, name)


def _set_layout_value(node, name, value):
    options = node.layout_options
    if name == "margin":
        options.margin = value if isinstance(value, Insets) else Insets(*value)
    elif name == "padding":
        options.padding = value if isinstance(value, Insets) else Insets(*value)
    elif name == "offset":
        options.offset = value
    else:
        setattr(options, name, value)


class Style:
    """One typed style declaration with optional single inheritance."""

    def __init__(self, style_id, target=None, based_on=None, **properties):
        _style_id(style_id)
        if target is not None and (not isinstance(target, type)
                                   or not issubclass(target, Node)):
            raise TypeError("Style target must be a Node type or None")
        if based_on is not None:
            _style_id(based_on)
        self.id = style_id
        self.target = target
        self.based_on = based_on
        self.properties = dict(properties)
        self._source = capture_construction(self, names=("Style",))


class StyleSheet:
    """Validated collection of typed styles used by a page."""

    def __init__(self, *styles):
        if not all(isinstance(value, Style) for value in styles):
            raise TypeError("StyleSheet accepts only Style values")
        ids = [_style_id(value.id) for value in styles]
        if len(ids) != len(set(ids)):
            raise ValueError("Style wire identities must be unique")
        self.styles = tuple(styles)
        self._by_id = dict((_style_id(value.id), value) for value in styles)
        self._source = capture_construction(self, names=("StyleSheet",))
        self._validate()

    def _validate(self):
        layout = _layout_specs()
        inheritable = _inheritable_specs()
        for style in self.styles:
            if (style.based_on is not None
                    and _style_id(style.based_on) not in self._by_id):
                raise ValueError("Unknown base style: %s" % style.based_on)
            component = _component_specs(style.target) if style.target else {}
            for name, value in style.properties.items():
                if style.target is None:
                    spec = inheritable.get(name)
                    if spec is None:
                        raise ValueError(
                            "Context style property %s is not inheritable" % name)
                    spec.validate(value)
                    continue
                component_spec = component.get(name)
                layout_spec = layout.get(name)
                component_ok = component_spec is not None and component_spec.styleable
                layout_ok = layout_spec is not None and layout_spec.styleable
                if component_ok and layout_ok:
                    raise ValueError("Ambiguous style property: %s" % name)
                if component_ok:
                    component_spec.validate(value)
                elif layout_ok:
                    layout_spec.validate(value)
                else:
                    raise ValueError(
                        "%s is not styleable on %s" % (name, style.target.__name__))

        visiting = set()
        visited = set()

        def visit(style):
            if style.id in visited:
                return
            if style.id in visiting:
                raise ValueError("Style inheritance cycle at %s" % style.id)
            visiting.add(style.id)
            if style.based_on is not None:
                base = self._by_id[_style_id(style.based_on)]
                if style.target is None and base.target is not None:
                    raise ValueError("A context style cannot derive from a targeted style")
                if (style.target is not None and base.target is not None
                        and not issubclass(style.target, base.target)):
                    raise ValueError("Derived style target is incompatible with its base")
                visit(base)
            visiting.remove(style.id)
            visited.add(style.id)

        for style in self.styles:
            visit(style)

    def get(self, style_id):
        key = _style_id(style_id)
        try:
            return self._by_id[key]
        except KeyError:
            raise ValueError("Unknown style: %s" % key) from None

    def _chain(self, style_id):
        result = []
        current = self.get(style_id)
        while current is not None:
            result.append(current)
            current = (None if current.based_on is None
                       else self.get(current.based_on))
        result.reverse()
        return tuple(result)

    @staticmethod
    def _origin(kind, style=None, property_name=None, inherited_from=None):
        result = {"kind": kind}
        if style is not None:
            result["style"] = _style_id(style.id)
            result["style_source"] = construction_metadata(style)
            if property_name is not None:
                result["style_property_source"] = style_property_provenance(
                    style, property_name, style.properties.get(property_name))
        if inherited_from is not None:
            result["inherited_from"] = inherited_from
        return result

    def _style_values(self, node, component, layout, inheritable):
        values = {}
        context = {}
        if node._style_id is None:
            return values, context
        style = self.get(node._style_id)
        if style.target is not None and not isinstance(node, style.target):
            raise TypeError(
                "Style %s targets %s, not %s" %
                (_style_id(style.id), style.target.__name__, type(node).__name__))
        for item in self._chain(node._style_id):
            for name, value in item.properties.items():
                spec = component.get(name)
                if spec is not None and spec.styleable:
                    values[name] = (value, item)
                    if spec.inheritable:
                        context[name] = (value, item)
                    continue
                layout_spec = layout.get(name)
                if layout_spec is not None and layout_spec.styleable and item.target is not None:
                    values["layout:" + name] = (value, item)
                    continue
                if name in inheritable:
                    context[name] = (value, item)
        return values, context

    def apply(self, root):
        """Compute and apply the stylesheet using CSS-free RFC precedence."""
        if not isinstance(root, Node):
            raise TypeError("StyleSheet root must be a Node")

        # Reuse reflection and style resolution within this pass only. A later
        # pass still observes mutable styles, schemas and authoring provenance.
        layout_specs = _layout_specs()
        layout_names = tuple(name for name, spec in layout_specs.items() if spec.styleable)
        inheritable = _inheritable_specs()
        component_specs = {}
        resolved_styles = {}
        origins = {}

        def style_origin(style, property_name):
            key = (style.id, property_name)
            if key not in origins:
                origins[key] = self._origin("style", style, property_name)
            return dict(origins[key])

        def visit(node, inherited):
            node_type = type(node)
            if node_type not in component_specs:
                component_specs[node_type] = {
                    name: spec for name, spec in _component_specs(node_type).items()
                    if spec.styleable}
            specs = component_specs[node_type]
            key = (node_type, node._style_id)
            if key not in resolved_styles:
                values, context_values = self._style_values(node, specs, layout_specs, inheritable)
                # Declared inheritable properties publish their final value and
                # origin below, after explicit-property precedence is applied.
                context_values = {
                    name: value for name, value in context_values.items()
                    if name not in specs or not specs[name].inheritable}
                resolved_styles[key] = (values, context_values)
            style_values, style_context = resolved_styles[key]
            node._computed_property_origins = {}
            node._computed_layout_origins = {}
            node._computed_property_fallbacks = {}
            node._computed_layout_fallbacks = {}

            context = dict(inherited)
            for name, (value, style) in style_context.items():
                context[name] = (value, style_origin(style, name))

            for name, spec in specs.items():
                current = spec.value_from(node)
                baseline = node._authoring_property_defaults.get(name, current)
                inherited_value = inherited.get(name) if spec.inheritable else None
                if name in style_values:
                    desired, style = style_values[name]
                elif inherited_value is not None:
                    desired = inherited_value[0]
                else:
                    desired = baseline
                if name in style_values or inherited_value is not None:
                    node._computed_property_fallbacks[name] = desired
                if name in node._explicit_properties:
                    desired = current
                    property_origin = {"kind": "explicit"}
                else:
                    if name in style_values:
                        property_origin = style_origin(style, name)
                    elif inherited_value is not None:
                        property_origin = {"kind": "inherited", "inherited_from": inherited_value[1]}
                    else:
                        property_origin = {"kind": "default"}
                    if current != desired:
                        spec.set_on(node, desired)
                        if hasattr(node, "_measurement"):
                            node._measurement = None
                        node.invalidate(
                            Dirty.LAYOUT if spec.invalidation != Invalidation.PAINT
                            else Dirty.PAINT)
                node._computed_property_origins[name] = property_origin
                if spec.inheritable:
                    context[name] = (desired, property_origin)

            for name in layout_names:
                styled = style_values.get("layout:" + name)
                explicit = name in node._explicit_layout
                # Explicit values stay untouched. Only a styled fallback needs
                # normalization for the editor's reset-to-style operation.
                if explicit and styled is None:
                    node._computed_layout_origins[name] = {"kind": "explicit"}
                    continue
                current = _layout_value(node, name)
                baseline_value = node._authoring_layout_defaults.get(name, current)
                if isinstance(baseline_value, Insets):
                    baseline = (baseline_value.left, baseline_value.top,
                                baseline_value.right, baseline_value.bottom)
                else:
                    baseline = baseline_value
                desired = baseline
                if styled is not None:
                    desired, style = styled
                # Compare and store runtime layout values, just as fluent
                # modifiers do ("fill" is represented by None).
                if name in ("width", "height"):
                    desired = node._size_value(desired)
                elif name in ("margin", "padding"):
                    desired = tuple(desired)
                if styled is not None:
                    node._computed_layout_fallbacks[name] = desired
                if explicit:
                    property_origin = {"kind": "explicit"}
                else:
                    property_origin = (style_origin(style, name) if styled is not None
                                       else {"kind": "default"})
                    if current != desired:
                        _set_layout_value(node, name, desired)
                        node.invalidate(Dirty.LAYOUT)
                node._computed_layout_origins[name] = property_origin

            for child in node.render_children():
                visit(child, context)

        # Dirty is imported lazily to keep the public style module independent
        # from the implementation order in layout.py.
        from .layout import Dirty
        visit(root, {})

    def as_dict(self):
        return {
            "source": construction_metadata(self),
            "styles": [self.style_metadata(value) for value in self.styles],
        }

    def style_metadata(self, style):
        component = _component_specs(style.target) if style.target else {}
        layout = _layout_specs()
        component_values = {}
        layout_values = {}
        context_values = {}
        for name, value in style.properties.items():
            if name in component and component[name].styleable:
                component_values[name] = value
            elif name in layout and layout[name].styleable and style.target is not None:
                layout_values[name] = value
            else:
                context_values[name] = value
        return {
            "id": _style_id(style.id),
            "symbol": "%s.%s" % (style.id.__class__.__name__, style.id.name),
            "qualified_symbol": "%s.%s.%s" % (
                style.id.__class__.__module__, style.id.__class__.__name__, style.id.name),
            "preview_only": bool(getattr(style, "_designer_draft", False)),
            "target": None if style.target is None else style.target.__name__,
            "based_on": None if style.based_on is None else _style_id(style.based_on),
            "component_properties": component_values,
            "layout_properties": layout_values,
            "context_properties": context_values,
            "property_sources": dict((
                name, style_property_provenance(style, name, value))
                for name, value in style.properties.items()),
            "source": construction_metadata(style),
        }
