## Declarative geometry and layout containers for Feather screens.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from enum import Enum, IntEnum

from .bindings import StateStore, derived, page_state_keys, resolve
from .actions import action_wire_id, collect_actions
from .identity import PageKey, serialize_key
from .properties import (
    CreationFieldSpec, EditorSpec, Invalidation, PropertySpec, SourceSpec,
    ValidationSpec, property_schema,
)


def _capture_construction(instance, names=()):
    return None


def _capture_modifier(node, method, properties):
    return None


def _install_source_hooks(capture_construction, capture_modifier):
    """Install optional Designer provenance hooks on explicit request."""
    global _capture_construction, _capture_modifier
    _capture_construction = capture_construction
    _capture_modifier = capture_modifier



class Insets:
    """Immutable edge insets used by element layout options."""

    __slots__ = ("left", "top", "right", "bottom")

    def __init__(self, left=0, top=0, right=None, bottom=None):
        self.left = int(left)
        self.top = int(top)
        self.right = self.left if right is None else int(right)
        self.bottom = self.top if bottom is None else int(bottom)
        if min(self.left, self.top, self.right, self.bottom) < 0:
            raise ValueError("Insets must be non-negative")

    @classmethod
    def all(cls, value):
        return cls(value, value, value, value)

    @classmethod
    def symmetric(cls, horizontal=0, vertical=0):
        return cls(horizontal, vertical, horizontal, vertical)

    @classmethod
    def from_values(cls, value=0, left=None, top=None, right=None,
                    bottom=None, horizontal=None, vertical=None):
        base = int(value)
        horizontal = base if horizontal is None else int(horizontal)
        vertical = base if vertical is None else int(vertical)
        return cls(
            horizontal if left is None else left,
            vertical if top is None else top,
            horizontal if right is None else right,
            vertical if bottom is None else bottom,
        )

    @property
    def horizontal(self):
        return self.left + self.right

    @property
    def vertical(self):
        return self.top + self.bottom


class Rect:
    """Small immutable rectangle with layout-oriented operations."""

    __slots__ = ("x", "y", "width", "height")

    def __init__(self, x, y, width, height):
        self.x = int(x)
        self.y = int(y)
        self.width = int(width)
        self.height = int(height)
        if self.width < 0 or self.height < 0:
            raise ValueError("Rectangle size must be non-negative")

    def __iter__(self):
        return iter((self.x, self.y, self.width, self.height))

    def __repr__(self):
        return "Rect(%d, %d, %d, %d)" % tuple(self)

    def __eq__(self, other):
        return isinstance(other, Rect) and tuple(self) == tuple(other)

    @property
    def right(self):
        return self.x + self.width

    @property
    def bottom(self):
        return self.y + self.height

    @property
    def center_x(self):
        return self.x + self.width // 2

    @property
    def center_y(self):
        return self.y + self.height // 2

    @property
    def center(self):
        return self.center_x, self.center_y

    def as_tuple(self):
        return tuple(self)

    def inset(self, insets=0, top=None, right=None, bottom=None):
        if isinstance(insets, Insets):
            edges = insets
        elif top is None and right is None and bottom is None:
            edges = Insets.all(insets)
        else:
            edges = Insets(insets, top or 0, right, bottom)
        width = self.width - edges.horizontal
        height = self.height - edges.vertical
        if width < 0 or height < 0:
            raise ValueError("Insets exceed rectangle size")
        return Rect(self.x + edges.left, self.y + edges.top, width, height)

    def offset(self, x=0, y=0):
        return Rect(self.x + int(x), self.y + int(y),
                    self.width, self.height)

    def with_size(self, width=None, height=None):
        return Rect(self.x, self.y,
                    self.width if width is None else width,
                    self.height if height is None else height)

    def union(self, other):
        left = min(self.x, other.x)
        top = min(self.y, other.y)
        right = max(self.right, other.right)
        bottom = max(self.bottom, other.bottom)
        return Rect(left, top, right - left, bottom - top)

    def align(self, width, height, horizontal="center", vertical="center"):
        width = int(width)
        height = int(height)
        if width > self.width or height > self.height:
            raise ValueError("Aligned rectangle does not fit its container")
        x = _aligned_position(
            self.x, self.width, width, horizontal, "left", "right")
        y = _aligned_position(
            self.y, self.height, height, vertical, "top", "bottom")
        return Rect(x, y, width, height)

    def row(self, *tracks, **kwargs):
        return split(self, "horizontal", tracks, kwargs.get("gap", 0))

    def column(self, *tracks, **kwargs):
        return split(self, "vertical", tracks, kwargs.get("gap", 0))


def _aligned_position(origin, span, size, alignment, start_name, end_name):
    if alignment in ("stretch", start_name):
        return origin
    if alignment == end_name:
        return origin + span - size
    if alignment == "center":
        return origin + (span - size) // 2
    raise ValueError("Unknown alignment: %s" % alignment)


class Flex:
    """A weighted flexible track."""

    __slots__ = ("weight",)

    def __init__(self, weight=1):
        self.weight = int(weight)
        if self.weight <= 0:
            raise ValueError("Flexible track weight must be positive")


class EqualTracks:
    """Primitive describing a requested number of equal flexible tracks."""

    __slots__ = ("count",)

    def __init__(self, count):
        self.count = int(count)
        if self.count <= 0:
            raise ValueError("Equal track count must be positive")

    def tracks(self):
        return tuple(Flex() for _index in range(self.count))


Equal = EqualTracks
FLEX = Flex()


def _normalize_tracks(tracks):
    if isinstance(tracks, EqualTracks):
        return tracks.tracks()
    return tuple(tracks)


def _resolved_tracks(total, tracks, gap):
    tracks = _normalize_tracks(tracks)
    if not tracks:
        return ()
    gap = int(gap)
    if gap < 0:
        raise ValueError("Layout gap must be non-negative")
    available = int(total) - gap * (len(tracks) - 1)
    fixed = sum(
        int(value) for value in tracks
        if value is not None and not isinstance(value, Flex))
    flexible = [
        Flex() if value is None else value
        for value in tracks
        if value is None or isinstance(value, Flex)]
    remaining = available - fixed
    if remaining < 0 or (not flexible and remaining != 0):
        raise ValueError("Layout tracks do not fit their container")
    if not flexible:
        return tuple(int(value) for value in tracks)
    total_weight = sum(track.weight for track in flexible)
    weighted_sizes = [
        remaining * track.weight // total_weight for track in flexible]
    remainder = remaining - sum(weighted_sizes)
    for index in range(remainder):
        weighted_sizes[index % len(weighted_sizes)] += 1
    result = []
    flexible_index = 0
    for value in tracks:
        if value is None or isinstance(value, Flex):
            result.append(weighted_sizes[flexible_index])
            flexible_index += 1
        else:
            result.append(int(value))
    return tuple(result)


def _split_sizes(rect, direction, sizes, gap=0):
    horizontal = direction == "horizontal"
    if not horizontal and direction != "vertical":
        raise ValueError("Unknown split direction: %s" % direction)
    result = []
    cursor = rect.x if horizontal else rect.y
    for size in sizes:
        if horizontal:
            result.append(Rect(cursor, rect.y, size, rect.height))
        else:
            result.append(Rect(rect.x, cursor, rect.width, size))
        cursor += size + int(gap)
    return tuple(result)


def split(rect, direction, tracks, gap=0):
    horizontal = direction == "horizontal"
    if not horizontal and direction != "vertical":
        raise ValueError("Unknown split direction: %s" % direction)
    sizes = _resolved_tracks(
        rect.width if horizontal else rect.height, tracks, gap)
    return _split_sizes(rect, direction, sizes, gap)


def subdivision_positions(start, span, depth):
    """Return adaptive binary subdivisions including both endpoints."""
    depth = int(depth)
    if depth < 0:
        raise ValueError("Subdivision depth must be non-negative")
    divisions = 1 << depth
    start = int(start)
    span = int(span)
    result = []
    for index in range(divisions + 1):
        coordinate = int(round(start + span * index / float(divisions)))
        if index in (0, divisions):
            level = -1
        else:
            power = 0
            value = index
            while value % 2 == 0:
                power += 1
                value //= 2
            level = max(0, depth - power - 1)
        result.append((index, coordinate, level))
    return tuple(result)


class Dirty(IntEnum):
    CLEAN = 0
    PAINT = 1
    LAYOUT = 2


class LayoutModifierSpec:
    """Map one fluent source method to framework layout fields."""

    __slots__ = (
        "method", "fields", "argument_codec", "activation", "selection",
    )

    def __init__(self, method, fields, argument_codec="positional_fields",
                 activation="any_non_default", selection="any_changed"):
        self.method = str(method)
        self.fields = tuple(
            (str(name), tuple(int(value) for value in positions))
            for name, positions in fields)
        self.argument_codec = str(argument_codec)
        self.activation = str(activation)
        self.selection = str(selection)

    def as_dict(self):
        return {
            "method": self.method,
            "fields": dict((name, list(positions))
                           for name, positions in self.fields),
            "argument_codec": self.argument_codec,
            "activation": self.activation,
            "selection": self.selection,
        }


class LayoutSourceContract:
    """Framework-owned grammar for fluent layout source editing."""

    __slots__ = ("strategy", "modifiers")

    def __init__(self, strategy, modifiers=()):
        self.strategy = str(strategy)
        self.modifiers = tuple(modifiers)
        if not all(isinstance(value, LayoutModifierSpec)
                   for value in self.modifiers):
            raise TypeError(
                "LayoutSourceContract modifiers must be LayoutModifierSpec values")

    def as_dict(self):
        return {
            "strategy": self.strategy,
            "modifiers": [value.as_dict() for value in self.modifiers],
        }


_FLUENT_LAYOUT_SOURCE = LayoutSourceContract(
    "core.fluent_layout", (
        LayoutModifierSpec(
            "size", (("width", (0,)), ("height", (1,))),
            activation="all_non_default", selection="all_changed"),
        LayoutModifierSpec("width", (("width", (0,)),)),
        LayoutModifierSpec("height", (("height", (0,)),)),
        LayoutModifierSpec("grow", (("grow", (0,)),)),
        LayoutModifierSpec(
            "margin", (("margin", (0,)),), argument_codec="insets"),
        LayoutModifierSpec(
            "padding", (("padding", (0,)),), argument_codec="insets"),
        LayoutModifierSpec(
            "align", (("horizontal", (0,)), ("vertical", (1,))),
            argument_codec="compact_fields"),
        LayoutModifierSpec(
            "offset", (("offset", (0, 1)),), argument_codec="spread_field"),
        LayoutModifierSpec(
            "allow_overflow", (("allow_overflow", (0,)),)),
    ))


class LayoutOptions:
    __slots__ = (
        "width", "height", "grow", "margin", "padding",
        "horizontal", "vertical", "offset_x", "offset_y",
        "allow_overflow",
    )

    def __init__(self):
        self.width = None
        self.height = None
        self.grow = 1
        self.margin = Insets()
        self.padding = Insets()
        self.horizontal = "stretch"
        self.vertical = "stretch"
        self.offset_x = 0
        self.offset_y = 0
        self.allow_overflow = False

    @property
    def offset(self):
        return self.offset_x, self.offset_y

    @offset.setter
    def offset(self, value):
        self.offset_x, self.offset_y = (int(value[0]), int(value[1]))


def _layout_property(name, runtime_type, default, kind="number", choices=(),
                     minimum=None, maximum=None, nullable=False,
                     source=None, **editor_metadata):
    return PropertySpec(
        name, runtime_type, default=default, nullable=nullable,
        validation=ValidationSpec(
            minimum=minimum, maximum=maximum, choices=choices),
        editor=EditorSpec(
            kind, label=name.replace("_", " ").title(), group="Layout",
            choices=choices, **editor_metadata),
        bindings=(), invalidation=Invalidation.LAYOUT,
        source=SourceSpec(name=source or name, storage="layout"),
    )


LAYOUT_SCHEMA = property_schema(
    _layout_property("width", int, None, minimum=1, maximum=4000,
                     nullable=True, wire_codec="integer", auto_label="Auto"),
    _layout_property("height", int, None, minimum=1, maximum=4000,
                     nullable=True, wire_codec="integer", auto_label="Auto"),
    _layout_property("grow", int, 1, minimum=0, maximum=100,
                     wire_codec="integer"),
    _layout_property(
        "margin", (tuple, list), (0, 0, 0, 0), kind="insets",
        wire_codec="insets", vector_labels=("Left", "Top", "Right", "Bottom"),
        item_minimum=0, item_maximum=2000),
    _layout_property(
        "padding", (tuple, list), (0, 0, 0, 0), kind="insets",
        wire_codec="insets", vector_labels=("Left", "Top", "Right", "Bottom"),
        item_minimum=0, item_maximum=2000),
    _layout_property(
        "horizontal", str, "stretch", kind="select",
        choices=("stretch", "left", "center", "right"),
        wire_codec="identity"),
    _layout_property(
        "vertical", str, "stretch", kind="select",
        choices=("stretch", "top", "center", "bottom"),
        wire_codec="identity"),
    _layout_property(
        "offset", (tuple, list), (0, 0), kind="point",
        wire_codec="point", vector_labels=("X", "Y"),
        item_minimum=-4000, item_maximum=4000,
        runtime_guard="overlay_child_axes",
        help=("For an Auto-sized Overlay child, a positive offset consumes "
              "space on that axis and the child fills the remainder of its slot.")),
    _layout_property(
        "allow_overflow", bool, False, kind="checkbox", wire_codec="boolean",
        help=("Permits an explicit size or offset to draw beyond the parent "
              "slot. It does not resize the parent or fit wrapped content.")),
)


class LayoutResult:
    """Arranged bounds and nodes indexed by object identity and stable refs."""

    __slots__ = ("_nodes", "_names", "_named_nodes")

    def __init__(self):
        self._nodes = {}
        self._names = {}
        self._named_nodes = {}

    @staticmethod
    def _name(value):
        return value.value if isinstance(value, Enum) else value

    def set(self, node, rect):
        self._nodes[id(node)] = rect
        if node.key is not None:
            key = self._name(node.key)
            if key in self._names:
                raise ValueError("Duplicate layout ref: %s" % key)
            self._names[key] = rect
            self._named_nodes[key] = node

    def rect(self, node_or_key):
        key = self._name(node_or_key)
        if isinstance(key, str):
            return self._names[key]
        return self._nodes[id(node_or_key)]

    def node(self, key):
        return self._named_nodes[self._name(key)]

    def __getitem__(self, key):
        return self._names[self._name(key)]

    def get(self, key, default=None):
        return self._names.get(self._name(key), default)

    def keys(self):
        return self._names.keys()


class CreationIdentityContract:
    """Framework-owned stable identity emitted after node construction."""

    __slots__ = (
        "strategy", "field", "method", "required", "suggestion", "pattern",
    )

    def __init__(self, strategy="core.fluent_ref", field="ref", method="ref",
                 required=True, suggestion="type_counter",
                 pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$"):
        self.strategy = str(strategy)
        self.field = str(field)
        self.method = str(method)
        self.required = bool(required)
        self.suggestion = str(suggestion)
        self.pattern = str(pattern)

    def as_dict(self):
        return {
            "strategy": self.strategy,
            "field": self.field,
            "method": self.method,
            "required": self.required,
            "suggestion": self.suggestion,
            "pattern": self.pattern,
        }


class CreationSourceContract:
    """Framework-owned source generation strategy for a creatable node."""

    __slots__ = ("strategy", "callable", "identity")

    def __init__(self, strategy, callable=None, identity=None):
        self.strategy = str(strategy)
        self.callable = None if callable is None else str(callable)
        if identity is not None and not isinstance(
                identity, CreationIdentityContract):
            raise TypeError(
                "CreationSourceContract identity must be a "
                "CreationIdentityContract")
        self.identity = identity

    def as_dict(self):
        return {
            "strategy": self.strategy,
            "callable": self.callable,
            "identity": (
                None if self.identity is None else self.identity.as_dict()),
        }


class CreationContract:
    """Portable framework-owned construction form for generic tools.

    It describes only how a framework node can be instantiated safely. Product
    behavior, page placement and domain defaults remain in product source.
    """

    __slots__ = ("category", "kind", "fields", "children", "source")

    def __init__(self, category, kind="component", fields=(), children=False,
                 source=None):
        self.category = str(category)
        self.kind = str(kind)
        self.fields = tuple(fields)
        if not all(isinstance(value, CreationFieldSpec) for value in self.fields):
            raise TypeError(
                "CreationContract v2 fields must be CreationFieldSpec values")
        self.children = bool(children)
        if source is not None and not isinstance(source, CreationSourceContract):
            raise TypeError(
                "CreationContract source must be a CreationSourceContract")
        self.source = source

    def as_dict(self):
        return {
            "category": self.category,
            "kind": self.kind,
            "fields": [value.as_dict() for value in self.fields],
            "children": self.children,
            "source": None if self.source is None else self.source.as_dict(),
        }


class StructureSourceContract:
    """Framework-owned source grammar for structural operations."""

    __slots__ = ("strategy", "supported_forms")

    def __init__(self, strategy, supported_forms=()):
        self.strategy = str(strategy)
        self.supported_forms = tuple(str(value) for value in supported_forms)

    def as_dict(self):
        return {
            "strategy": self.strategy,
            "supported_forms": list(self.supported_forms),
        }


class StructureContract:
    """Portable structural editing capabilities for layout containers."""

    __slots__ = (
        "kind", "source", "operations", "minimum_children",
        "supports_spans", "placement", "reorder", "canvas",
    )

    def __init__(self, kind, source, operations=("insert", "move", "delete",
                 "extract", "duplicate", "clipboard_insert", "move_many"),
                 minimum_children=0, supports_spans=False,
                 placement="flow", reorder=True, canvas=()):
        self.kind = str(kind)
        if not isinstance(source, StructureSourceContract):
            raise TypeError(
                "StructureContract source must be a StructureSourceContract")
        self.source = source
        self.operations = tuple(str(value) for value in operations)
        self.minimum_children = int(minimum_children)
        self.supports_spans = bool(supports_spans)
        self.placement = str(placement)
        self.reorder = bool(reorder)
        self.canvas = tuple(str(value) for value in canvas)

    def as_dict(self):
        return {
            "kind": self.kind,
            "source": self.source.as_dict(),
            "operations": list(self.operations),
            "minimum_children": self.minimum_children,
            "supports_spans": self.supports_spans,
            "placement": self.placement,
            "reorder": self.reorder,
            "canvas": list(self.canvas),
        }


_SEQUENCE_STRUCTURE = StructureContract(
    "sequence", StructureSourceContract(
        "core.variadic_children",
        ("inline_arguments", "named_local_collection")),
    minimum_children=0, placement="flow",
    canvas=("flow_reorder", "resize", "multi_select"))
_GRID_STRUCTURE = StructureContract(
    "grid", StructureSourceContract(
        "core.grid_matrix", ("inline_matrix", "named_local_matrix")),
    minimum_children=0, supports_spans=True,
    placement="grid", canvas=("grid_drop", "grid_span", "resize", "multi_select"))
_OVERLAY_STRUCTURE = StructureContract(
    "sequence", StructureSourceContract(
        "core.variadic_children",
        ("inline_arguments", "named_local_collection")),
    minimum_children=0,
    placement="absolute", canvas=(
        "absolute_move", "absolute_resize", "align", "distribute",
        "snapping", "multi_select"))

_STABLE_CREATION_IDENTITY = CreationIdentityContract()
_KEYWORD_CREATION_SOURCE = CreationSourceContract(
    "core.keyword_call", identity=_STABLE_CREATION_IDENTITY)
_GRID_CREATION_SOURCE = CreationSourceContract(
    "core.grid_matrix", identity=_STABLE_CREATION_IDENTITY)


_SEQUENCE_CREATION_FIELDS = (
    CreationFieldSpec(
        "gap", int, default=0,
        validation=ValidationSpec(minimum=0, maximum=4000),
        editor=EditorSpec("number", label="Gap", group="Layout"),
        bindings=()),
)
_ROW_CREATION = CreationContract(
    "Layout", "sequence", _SEQUENCE_CREATION_FIELDS, children=True,
    source=_KEYWORD_CREATION_SOURCE)
_COLUMN_CREATION = CreationContract(
    "Layout", "sequence", _SEQUENCE_CREATION_FIELDS, children=True,
    source=_KEYWORD_CREATION_SOURCE)
_OVERLAY_CREATION = CreationContract(
    "Layout", "absolute", (), children=True,
    source=_KEYWORD_CREATION_SOURCE)
_STATE_CASE_CREATION = CreationContract(
    "Conditions", "absolute", (
        CreationFieldSpec(
            "selector", object, required=True,
            editor=EditorSpec(
                "state_binding", label="State selector", group="Condition"),
            bindings=("direct",)),
        CreationFieldSpec(
            "expected", (str, int, float, bool), required=True,
            editor=EditorSpec(
                "text", label="Expected value", group="Condition"),
            bindings=()),
    ), children=True, source=_KEYWORD_CREATION_SOURCE)
_GRID_CREATION = CreationContract("Layout", "grid", (
    CreationFieldSpec(
        "row_count", int, default=2,
        validation=ValidationSpec(minimum=1, maximum=12),
        editor=EditorSpec("number", label="Rows"), bindings=()),
    CreationFieldSpec(
        "column_count", int, default=2,
        validation=ValidationSpec(minimum=1, maximum=12),
        editor=EditorSpec("number", label="Columns"), bindings=()),
    CreationFieldSpec(
        "column_gap", int, default=0,
        validation=ValidationSpec(minimum=0, maximum=4000),
        editor=EditorSpec("number", label="Column gap"), bindings=()),
    CreationFieldSpec(
        "row_gap", int, default=0,
        validation=ValidationSpec(minimum=0, maximum=4000),
        editor=EditorSpec("number", label="Row gap"), bindings=()),
), children=True, source=_GRID_CREATION_SOURCE)
_WRAP_CREATION = CreationContract("Layout", "sequence", (
    CreationFieldSpec(
        "orientation", str, default="horizontal",
        validation=ValidationSpec(choices=("horizontal", "vertical")),
        editor=EditorSpec(
            "select", label="Orientation",
            choices=("horizontal", "vertical")), bindings=()),
    CreationFieldSpec(
        "item_width", int, default=None, nullable=True,
        validation=ValidationSpec(minimum=1, maximum=4000),
        editor=EditorSpec("number", label="Item width"), bindings=()),
    CreationFieldSpec(
        "item_height", int, default=None, nullable=True,
        validation=ValidationSpec(minimum=1, maximum=4000),
        editor=EditorSpec("number", label="Item height"), bindings=()),
    CreationFieldSpec(
        "horizontal_gap", int, default=0,
        validation=ValidationSpec(minimum=0, maximum=4000),
        editor=EditorSpec("number", label="Horizontal gap"), bindings=()),
    CreationFieldSpec(
        "vertical_gap", int, default=0,
        validation=ValidationSpec(minimum=0, maximum=4000),
        editor=EditorSpec("number", label="Vertical gap"), bindings=()),
), children=True, source=_KEYWORD_CREATION_SOURCE)
_SPACER_CREATION = CreationContract(
    "Layout", source=_KEYWORD_CREATION_SOURCE)


_UNSET = object()


class Node:
    """Base object for layout containers and renderable components."""

    covers_bounds = False
    canvas_selectable = True
    property_schema = ()
    structure_contract = None
    creation_contract = None
    layout_source_contract = _FLUENT_LAYOUT_SOURCE

    def __init__(self, key=None):
        self.key = key
        self.layout_options = LayoutOptions()
        self.parent = None
        self._dirty = Dirty.CLEAN
        self._last_signature = _UNSET
        self._repaint_boundary = False
        self._source_mutations = {}
        self._source = _capture_construction(self)

    # Shared layout modifiers. They deliberately mutate the declaration node
    # so page construction stays compact and does not allocate wrapper trees.
    def ref(self, key):
        _capture_modifier(self, "ref", (("key", 0),))
        self.key = key
        return self

    def width(self, value):
        _capture_modifier(self, "width", (("width", 0),))
        self.layout_options.width = None if value is None else int(value)
        return self

    def height(self, value):
        _capture_modifier(self, "height", (("height", 0),))
        self.layout_options.height = None if value is None else int(value)
        return self

    def size(self, width, height):
        _capture_modifier(self, "size", (("width", 0), ("height", 1)))
        self.layout_options.width = None if width is None else int(width)
        self.layout_options.height = None if height is None else int(height)
        return self

    def grow(self, value=1):
        _capture_modifier(self, "grow", (("grow", 0),))
        self.layout_options.grow = int(value)
        if self.layout_options.grow < 0:
            raise ValueError("Element grow must be non-negative")
        return self

    def margin(self, value=0, **kwargs):
        _capture_modifier(self, "margin", (("margin", 0),))
        self.layout_options.margin = Insets.from_values(value, **kwargs)
        return self

    def padding(self, value=0, **kwargs):
        _capture_modifier(self, "padding", (("padding", 0),))
        self.layout_options.padding = Insets.from_values(value, **kwargs)
        return self

    def align(self, horizontal=None, vertical=None):
        _capture_modifier(
            self, "align", (("horizontal", 0), ("vertical", 1)))
        if horizontal is not None:
            self.layout_options.horizontal = horizontal
        if vertical is not None:
            self.layout_options.vertical = vertical
        return self

    def offset(self, x=0, y=0):
        """Offset an explicitly sized element inside its arranged slot.

        Offsets are primarily useful for Overlay children. Flow containers still
        own their children slots, so editor tooling only exposes this modifier
        when moving the element cannot silently rewrite Grid/List structure.
        """
        _capture_modifier(self, "offset", (("offset", 0),))
        self.layout_options.offset_x = int(x)
        self.layout_options.offset_y = int(y)
        return self

    def allow_overflow(self, value=True):
        _capture_modifier(
            self, "allow_overflow", (("allow_overflow", 0),))
        self.layout_options.allow_overflow = bool(value)
        return self

    def repaint_boundary(self):
        self._repaint_boundary = True
        return self

    def invalidate(self, dirty=Dirty.PAINT):
        dirty = Dirty(dirty)
        node = self
        while node is not None:
            if dirty > node._dirty:
                node._dirty = dirty
            node = node.parent
        return self

    def invalidate_layout(self):
        return self.invalidate(Dirty.LAYOUT)

    def _adopt(self, *children):
        for child in children:
            if child is None:
                continue
            child.parent = self

    def _box(self, bounds):
        options = self.layout_options
        available = bounds.inset(options.margin)
        width = (available.width - options.offset_x
                 if options.width is None else options.width)
        height = (available.height - options.offset_y
                  if options.height is None else options.height)
        if width < 0 or height < 0:
            raise ValueError(
                "%s %r offset (%d, %d) exceeds available slot %r" %
                (self.__class__.__name__, self.key, options.offset_x,
                 options.offset_y, available))
        minimum_height = self.minimum_extent("vertical", width)
        if minimum_height is not None:
            height = max(height, int(minimum_height))
        if (not options.allow_overflow and
                (width > available.width or height > available.height)):
            raise ValueError(
                "%s %r size %dx%d does not fit slot %r after margin %r" %
                (self.__class__.__name__, self.key, width, height, available,
                 (options.margin.left, options.margin.top,
                  options.margin.right, options.margin.bottom)))
        horizontal = options.horizontal
        vertical = options.vertical
        if options.width is None:
            horizontal = "stretch"
        if options.height is None:
            vertical = "stretch"
        x = _aligned_position(
            available.x, available.width, width, horizontal, "left", "right")
        y = _aligned_position(
            available.y, available.height, height, vertical, "top", "bottom")
        x += options.offset_x
        y += options.offset_y
        arranged = Rect(x, y, width, height)
        if (not options.allow_overflow and
                (arranged.x < available.x or arranged.y < available.y or
                 arranged.right > available.right or
                 arranged.bottom > available.bottom)):
            raise ValueError(
                "%s %r offset (%d, %d) moves it outside slot %r" %
                (self.__class__.__name__, self.key, options.offset_x,
                 options.offset_y, available))
        return arranged

    def preferred_extent(self, direction, cross_extent=None):
        """Return an optional intrinsic main-axis size for flow containers.

        Most nodes remain parent-sized. Components and composite containers may
        opt in when their product declaration has a deterministic content size.
        The contract belongs to the framework and is used identically by the
        product renderer and external tools.
        """
        return None

    def content_extent(self, direction, cross_extent=None):
        """Return an optional minimum content size for intrinsic containers.

        Flow containers use :meth:`preferred_extent` only when a child opts into
        content sizing.  Intrinsic containers such as an auto-sized ``Grid``
        additionally need the natural size of their cells even when a leaf would
        normally stretch in a Row/Column.  The default keeps both contracts the
        same; leaves may publish a more useful minimum without changing ordinary
        flow behavior.
        """
        return self.preferred_extent(direction, cross_extent)

    def minimum_extent(self, direction, cross_extent=None):
        """Return an optional hard content minimum for arranged bounds.

        Unlike :meth:`preferred_extent`, this constraint also applies when a
        caller supplied an explicit size.  Most nodes have no hard minimum;
        intrinsic containers opt in when shrinking their tracks would make
        arranged geometry disagree with what their children actually paint.
        """
        return None

    def auto_gap_extent(self, direction, cross_extent=None):
        """Return an intrinsic size used only by ``gap=None`` lists.

        Normal fixed-gap layouts keep their existing flex behavior. Components
        may opt into a compact intrinsic size so a space-between style list is
        useful without requiring every child to declare an explicit size.
        """
        return self.preferred_extent(direction, cross_extent)

    def arrange(self, bounds, result):
        if not isinstance(bounds, Rect):
            bounds = Rect(*bounds)
        arranged = self._box(bounds)
        result.set(self, arranged)
        self._arrange(arranged.inset(self.layout_options.padding), result)

    def _arrange(self, bounds, result):
        return None

    def render(self, renderer, state, layout):
        commands = _command_list(self.draw(renderer, state, layout.rect(self)))
        for child in self.render_children():
            commands.extend(child.render(renderer, state, layout))
        return commands

    def render_dirty(self, renderer, state, layout):
        """Render an invalidated subtree.

        Most nodes repaint normally. Dynamic leaves may override this method
        when they can restore only their previous damage region more cheaply
        than redrawing their whole arranged surface.
        """
        return self.render(renderer, state, layout)

    def draw(self, renderer, state, bounds):
        return ()

    def render_children(self):
        return ()

    def replace_preview_children(self, children, placements=None):
        """Replace children on a cloned Designer tree through the framework.

        Product runtime never calls this method. Generic tools use it only on
        deep-copied pages, so container-specific storage stays framework-owned.
        """
        raise TypeError("%s does not publish preview structure editing" %
                        self.__class__.__name__)

    def preview_child_placements(self):
        return None

    def state_signature(self, state):
        return None

    def update(self, state, initialize=False):
        signature = self.state_signature(state)
        if signature is not None:
            if self._last_signature is _UNSET or initialize:
                self._last_signature = signature
            elif signature != self._last_signature:
                self._last_signature = signature
                self.invalidate(Dirty.PAINT)
        for child in self.render_children():
            child.update(state, initialize)

    def clear_dirty(self):
        self._dirty = Dirty.CLEAN
        for child in self.render_children():
            child.clear_dirty()

    def walk(self):
        yield self
        for child in self.render_children():
            for descendant in child.walk():
                yield descendant

    def apply_override(self, name, value):
        for child in self.render_children():
            child.apply_override(name, value)


class SingleChild(Node):
    def __init__(self, child, key=None):
        super().__init__(key=key)
        self.child = child
        self._adopt(child)

    def render_children(self):
        return (self.child,)


class Overlay(Node):
    creation_contract = _OVERLAY_CREATION
    structure_contract = _OVERLAY_STRUCTURE

    def __init__(self, *children, **kwargs):
        super().__init__(key=kwargs.get("key"))
        self.children = tuple(children)
        self._adopt(*self.children)

    def _arrange(self, bounds, result):
        for child in self.children:
            child.arrange(bounds, result)

    def render_children(self):
        return self.children

    def replace_preview_children(self, children, placements=None):
        self.children = tuple(children)
        self._adopt(*self.children)


class Spacer(Node):
    creation_contract = _SPACER_CREATION

    pass


class List(Node):
    """Arrange children sequentially using their shared layout options."""

    property_schema = property_schema(
        PropertySpec(
            "direction", str, default="horizontal",
            validation=ValidationSpec(choices=("horizontal", "vertical")),
            editor=EditorSpec(
                "select", label="Direction", group="Layout",
                choices=("horizontal", "vertical")),
            bindings=(), invalidation=Invalidation.STRUCTURE),
        PropertySpec(
            "gap", (int, type(None)), default=0, nullable=True,
            validation=ValidationSpec(minimum=0, maximum=4000),
            editor=EditorSpec(
                "number", label="Gap", group="Layout",
                placeholder="Auto", auto_label="Auto"),
            bindings=(), invalidation=Invalidation.LAYOUT))
    structure_contract = _SEQUENCE_STRUCTURE

    def __init__(self, direction, *children, **kwargs):
        super().__init__(key=kwargs.get("key"))
        if direction not in ("horizontal", "vertical"):
            raise ValueError("Unknown list direction: %s" % direction)
        self.direction = direction
        gap = kwargs.get("gap", 0)
        self.gap = None if gap is None else int(gap)
        if self.gap is not None and self.gap < 0:
            raise ValueError("List gap must be non-negative or None")
        self.items = tuple(children)
        self._adopt(*self.items)

    @classmethod
    def horizontal(cls, *children, **kwargs):
        return cls("horizontal", *children, **kwargs)

    @classmethod
    def vertical(cls, *children, **kwargs):
        return cls("vertical", *children, **kwargs)

    def _main_extent(self, child, cross_extent=None):
        options = child.layout_options
        margin = options.margin
        size = options.width if self.direction == "horizontal" else options.height
        preferred_cross = cross_extent
        if preferred_cross is not None:
            preferred_cross = max(
                0, int(preferred_cross) -
                (margin.vertical if self.direction == "horizontal"
                 else margin.horizontal))
        minimum = child.minimum_extent(self.direction, preferred_cross)
        if minimum is not None:
            size = max(0 if size is None else int(size), int(minimum))
        if size is None:
            preferred = child.preferred_extent(
                self.direction, preferred_cross)
            if preferred is None and self.gap is None:
                preferred = child.auto_gap_extent(
                    self.direction, preferred_cross)
            if preferred is not None:
                size = preferred
        if size is None:
            return None
        return int(size) + (margin.horizontal if self.direction == "horizontal"
                            else margin.vertical)

    def preferred_extent(self, direction, cross_extent=None):
        if direction != self.direction or not self.items:
            return None
        extents = [self._main_extent(child, cross_extent)
                   for child in self.items]
        if any(value is None for value in extents):
            return None
        padding = self.layout_options.padding
        extra = padding.horizontal if direction == "horizontal" else padding.vertical
        gap = 0 if self.gap is None else self.gap
        return sum(extents) + gap * (len(extents) - 1) + extra

    def _arrange(self, bounds, result):
        main_extent = bounds.width if self.direction == "horizontal" else bounds.height
        gap = 0 if self.gap is None else self.gap
        available = main_extent - gap * (len(self.items) - 1)
        cross_extent = bounds.height if self.direction == "horizontal" else bounds.width
        tracks = []
        fixed = 0
        flexible = []
        for child in self.items:
            extent = self._main_extent(child, cross_extent)
            if extent is None:
                weight = max(1, child.layout_options.grow)
                flexible.append(Flex(weight))
                tracks.append(flexible[-1])
            else:
                fixed += extent
                tracks.append(extent)
        if fixed > available and flexible:
            sizes = [0 if isinstance(track, Flex) else int(track)
                     for track in tracks]
            areas = _split_sizes(bounds, self.direction, sizes, gap)
        elif fixed > available:
            raise ValueError("List children do not fit their container")
        elif flexible:
            areas = split(bounds, self.direction, tracks, gap)
        else:
            areas = []
            origin = bounds.x if self.direction == "horizontal" else bounds.y
            cursor = origin
            free_space = max(0, main_extent - fixed)
            gap_count = max(0, len(tracks) - 1)
            for index, extent in enumerate(tracks):
                if self.direction == "horizontal":
                    areas.append(Rect(cursor, bounds.y, extent, bounds.height))
                else:
                    areas.append(Rect(bounds.x, cursor, bounds.width, extent))
                cursor += extent
                if index < gap_count:
                    if self.gap is None:
                        before = int(round(free_space * index / float(gap_count)))
                        after = int(round(free_space * (index + 1) / float(gap_count)))
                        cursor += after - before
                    else:
                        cursor += gap
        for area, child in zip(areas, self.items):
            child.arrange(area, result)

    def render_children(self):
        return self.items

    def replace_preview_children(self, children, placements=None):
        self.items = tuple(children)
        self._adopt(*self.items)


class Row(List):
    creation_contract = _ROW_CREATION

    def __init__(self, *children, **kwargs):
        super().__init__("horizontal", *children, **kwargs)


class Column(List):
    creation_contract = _COLUMN_CREATION

    def __init__(self, *children, **kwargs):
        super().__init__("vertical", *children, **kwargs)


class GridCell:
    __slots__ = ("child", "column", "row", "column_span", "row_span")

    def __init__(self, child, column, row, column_span=1, row_span=1):
        self.child = child
        self.column = int(column)
        self.row = int(row)
        self.column_span = int(column_span)
        self.row_span = int(row_span)


class Span:
    """A typed matrix cell spanning adjacent grid columns or rows."""

    __slots__ = ("child", "columns", "rows")

    def __init__(self, child, columns=1, rows=1):
        self.child = child
        self.columns = int(columns)
        self.rows = int(rows)
        if self.columns <= 0 or self.rows <= 0:
            raise ValueError("Grid span must be positive")


class _EmptyCell:
    pass


EMPTY = _EmptyCell()



class Grid(Node):
    """Arrange a typed visual matrix in fixed or flexible tracks."""

    creation_contract = _GRID_CREATION
    property_schema = property_schema(
        PropertySpec(
            "columns", (tuple, list), default=(),
            editor=EditorSpec("tracks", label="Column tracks", group="Grid"),
            bindings=(), invalidation=Invalidation.STRUCTURE,
            source=SourceSpec(name="columns", position=1)),
        PropertySpec(
            "rows", (tuple, list), default=(),
            editor=EditorSpec("tracks", label="Row tracks", group="Grid"),
            bindings=(), invalidation=Invalidation.STRUCTURE,
            source=SourceSpec(name="rows", position=2)),
        PropertySpec(
            "column_gap", int, default=0,
            validation=ValidationSpec(minimum=0, maximum=4000),
            editor=EditorSpec("number", label="Column gap", group="Grid"),
            bindings=(), invalidation=Invalidation.LAYOUT,
            source=SourceSpec(name="gap", position=3, index=0)),
        PropertySpec(
            "row_gap", int, default=0,
            validation=ValidationSpec(minimum=0, maximum=4000),
            editor=EditorSpec("number", label="Row gap", group="Grid"),
            bindings=(), invalidation=Invalidation.LAYOUT,
            source=SourceSpec(name="gap", position=3, index=1)))
    structure_contract = _GRID_STRUCTURE

    def __init__(self, matrix, columns=None, rows=None, gap=0, key=None):
        super().__init__(key=key)
        self.column_gap, self.row_gap = self._gaps(gap)
        self.cells, column_count, row_count = self._matrix_cells(matrix)
        self.columns = _normalize_tracks(
            EqualTracks(column_count) if columns is None else columns)
        self.rows = _normalize_tracks(
            EqualTracks(row_count) if rows is None else rows)
        if len(self.columns) != column_count or len(self.rows) != row_count:
            raise ValueError("Grid tracks must match the visual matrix")
        self._adopt(*(item.child for item in self.cells))

    @staticmethod
    def _gaps(gap):
        if isinstance(gap, tuple):
            return int(gap[0]), int(gap[1])
        return int(gap), int(gap)

    @staticmethod
    def _matrix_cells(matrix):
        rows = tuple(tuple(row) for row in matrix)
        if not rows:
            raise ValueError("Grid matrix must not be empty")
        columns = max(len(row) for row in rows)
        if columns <= 0 or any(len(row) != columns for row in rows):
            raise ValueError("Grid matrix rows must have equal length")
        occupied = set()
        cells = []
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row):
                if value is None or value is EMPTY:
                    continue
                if (column_index, row_index) in occupied:
                    raise ValueError("Grid matrix cell overlaps a span")
                if isinstance(value, Span):
                    child = value.child
                    column_span = value.columns
                    row_span = value.rows
                else:
                    child = value
                    column_span = row_span = 1
                if (column_index + column_span > columns
                        or row_index + row_span > len(rows)):
                    raise ValueError("Grid span is outside the matrix")
                for occupied_row in range(row_index, row_index + row_span):
                    for occupied_column in range(
                            column_index, column_index + column_span):
                        coordinate = (occupied_column, occupied_row)
                        if coordinate in occupied:
                            raise ValueError("Grid matrix spans overlap")
                        occupied.add(coordinate)
                cells.append(GridCell(
                    child, column_index, row_index, column_span, row_span))
        return tuple(cells), columns, len(rows)

    @staticmethod
    def _span(rects, start, count):
        if start < 0 or count <= 0 or start + count > len(rects):
            raise ValueError("Grid span is outside its tracks")
        return rects[start], rects[start + count - 1]

    @staticmethod
    def _distribute(value, indexes, tracks):
        if value <= 0 or not indexes:
            return dict((index, 0) for index in indexes)
        total_weight = sum(tracks[index].weight for index in indexes)
        result = dict(
            (index, value * tracks[index].weight // total_weight)
            for index in indexes)
        remainder = value - sum(result.values())
        for offset in range(remainder):
            result[indexes[offset % len(indexes)]] += 1
        return result

    def _intrinsic_row_sizes(self, width):
        if not self.rows:
            return ()
        column_sizes = _resolved_tracks(width, self.columns, self.column_gap)
        sizes = [
            0 if isinstance(track, Flex) else int(track)
            for track in self.rows]
        flexible = [
            isinstance(track, Flex) for track in self.rows]
        measured = False
        for item in self.cells:
            child = item.child
            margin = child.layout_options.margin
            first = item.column
            last = item.column + item.column_span
            cell_width = (
                sum(column_sizes[first:last]) +
                self.column_gap * max(0, item.column_span - 1))
            child_width = max(0, cell_width - margin.horizontal)
            extent = child.layout_options.height
            if extent is None:
                extent = child.content_extent("vertical", child_width)
            if extent is None:
                continue
            measured = True
            required = int(extent) + margin.vertical
            row_first = item.row
            row_last = item.row + item.row_span
            current = (
                sum(sizes[row_first:row_last]) +
                self.row_gap * max(0, item.row_span - 1))
            indexes = [
                index for index in range(row_first, row_last)
                if flexible[index]]
            additions = self._distribute(
                max(0, required - current), indexes, self.rows)
            for index, amount in additions.items():
                sizes[index] += amount
        if any(flexible) and not measured:
            return None
        return tuple(sizes)

    def preferred_extent(self, direction, cross_extent=None):
        if direction != "vertical" or cross_extent is None:
            return None
        width = (self.layout_options.width
                 if self.layout_options.width is not None
                 else int(cross_extent))
        width = max(0, width - self.layout_options.padding.horizontal)
        sizes = self._intrinsic_row_sizes(width)
        if sizes is None:
            return None
        return (sum(sizes) + self.row_gap * max(0, len(sizes) - 1) +
                self.layout_options.padding.vertical)

    def minimum_extent(self, direction, cross_extent=None):
        return self.preferred_extent(direction, cross_extent)

    def _row_areas(self, bounds):
        # Preserve the legacy flex-track behavior unless this Grid is actually
        # being content-sized by a vertical flow parent. Intrinsic measurement
        # also acts as a minimum for an undersized explicit height so row bounds
        # continue to describe the pixels their children paint.
        parent = self.parent
        if not isinstance(parent, List) or parent.direction != "vertical":
            return split(bounds, "vertical", self.rows, self.row_gap)
        intrinsic = self._intrinsic_row_sizes(bounds.width)
        if intrinsic is None:
            return split(bounds, "vertical", self.rows, self.row_gap)
        minimum = (sum(intrinsic) +
                   self.row_gap * max(0, len(self.rows) - 1))
        if bounds.height != minimum:
            return split(bounds, "vertical", self.rows, self.row_gap)
        return _split_sizes(bounds, "vertical", intrinsic, self.row_gap)

    def grid_areas(self, bounds):
        return (
            split(bounds, "horizontal", self.columns, self.column_gap),
            self._row_areas(bounds),
        )

    def _arrange(self, bounds, result):
        column_areas, row_areas = self.grid_areas(bounds)
        for item in self.cells:
            first_column, last_column = self._span(
                column_areas, item.column, item.column_span)
            first_row, last_row = self._span(
                row_areas, item.row, item.row_span)
            item.child.arrange(Rect(
                first_column.x, first_row.y,
                last_column.right - first_column.x,
                last_row.bottom - first_row.y), result)

    def render_children(self):
        return tuple(item.child for item in self.cells)

    def replace_preview_children(self, children, placements=None):
        placements = tuple(placements or ())
        if len(children) != len(placements):
            raise ValueError("Grid preview children need explicit placements")
        self.cells = tuple(GridCell(
            child, value.get("column", 0), value.get("row", 0),
            value.get("column_span", 1), value.get("row_span", 1))
            for child, value in zip(children, placements))
        self._adopt(*tuple(children))

    def preview_child_placements(self):
        return tuple({
            "column": item.column, "row": item.row,
            "column_span": item.column_span, "row_span": item.row_span,
        } for item in self.cells)


class WrapPanel(Node):
    creation_contract = _WRAP_CREATION
    structure_contract = _SEQUENCE_STRUCTURE
    property_schema = property_schema(
        PropertySpec(
            "orientation", str, default="horizontal",
            validation=ValidationSpec(choices=("horizontal", "vertical")),
            editor=EditorSpec(
                "select", label="Orientation", group="Wrap layout",
                choices=("horizontal", "vertical")),
            bindings=(), invalidation=Invalidation.STRUCTURE),
        PropertySpec(
            "item_width", int, default=None, nullable=True,
            validation=ValidationSpec(minimum=1, maximum=4000),
            editor=EditorSpec(
                "number", label="Item width", group="Wrap layout"),
            bindings=(), invalidation=Invalidation.LAYOUT),
        PropertySpec(
            "item_height", int, default=None, nullable=True,
            validation=ValidationSpec(minimum=1, maximum=4000),
            editor=EditorSpec(
                "number", label="Item height", group="Wrap layout"),
            bindings=(), invalidation=Invalidation.LAYOUT),
        PropertySpec(
            "horizontal_gap", int, default=0,
            validation=ValidationSpec(minimum=0, maximum=4000),
            editor=EditorSpec(
                "number", label="Horizontal gap", group="Wrap layout"),
            bindings=(), invalidation=Invalidation.LAYOUT),
        PropertySpec(
            "vertical_gap", int, default=0,
            validation=ValidationSpec(minimum=0, maximum=4000),
            editor=EditorSpec(
                "number", label="Vertical gap", group="Wrap layout"),
            bindings=(), invalidation=Invalidation.LAYOUT))

    """Lay out equal-sized children and wrap them across rows or columns."""

    def __init__(self, *children, **kwargs):
        super().__init__(key=kwargs.get("key"))
        self.children = tuple(children)
        self.orientation = kwargs.get("orientation", "horizontal")
        if self.orientation not in ("horizontal", "vertical"):
            raise ValueError("Unknown wrap orientation: %s" % self.orientation)
        self.item_width = kwargs.get("item_width")
        self.item_height = kwargs.get("item_height")
        self.horizontal_gap = int(kwargs.get("horizontal_gap", 0))
        self.vertical_gap = int(kwargs.get("vertical_gap", 0))
        self._adopt(*self.children)

    @staticmethod
    def _fit_count(span, item, gap):
        if item is None:
            return 1
        item = int(item)
        if item <= 0:
            raise ValueError("WrapPanel item size must be positive")
        return max(1, (span + gap) // (item + gap))

    def _arrange(self, bounds, result):
        count = len(self.children)
        if not count:
            return
        if self.orientation == "horizontal":
            columns = self._fit_count(
                bounds.width, self.item_width, self.horizontal_gap)
            rows = (count + columns - 1) // columns
        else:
            rows = self._fit_count(
                bounds.height, self.item_height, self.vertical_gap)
            columns = (count + rows - 1) // rows
        width = ((bounds.width - self.horizontal_gap * (columns - 1)) // columns
                 if self.item_width is None else int(self.item_width))
        height = ((bounds.height - self.vertical_gap * (rows - 1)) // rows
                  if self.item_height is None else int(self.item_height))
        for index, child in enumerate(self.children):
            if self.orientation == "horizontal":
                column, row = index % columns, index // columns
            else:
                row, column = index % rows, index // rows
            child.arrange(Rect(
                bounds.x + column * (width + self.horizontal_gap),
                bounds.y + row * (height + self.vertical_gap),
                width, height), result)

    def render_children(self):
        return self.children

    def replace_preview_children(self, children, placements=None):
        self.children = tuple(children)
        self._adopt(*self.children)


class When(SingleChild):
    """Render a child only when the state predicate is true."""

    def __init__(self, predicate, child, key=None):
        super().__init__(child, key=key)
        self.predicate = predicate
        self._visible = _UNSET

    def _arrange(self, bounds, result):
        self.child.arrange(bounds, result)

    def state_signature(self, state):
        return bool(resolve(self.predicate, state))

    def render(self, renderer, state, layout):
        if resolve(self.predicate, state):
            return self.child.render(renderer, state, layout)
        return []


class StateCase(When):
    """Designer-friendly conditional Overlay selected by a state value.

    Children are ordinary positional source arguments while ``selector`` and
    ``expected`` stay explicit keyword fields. This makes empty creation and
    later contract-driven insertion deterministic without parsing a lambda.
    """

    creation_contract = _STATE_CASE_CREATION
    structure_contract = _OVERLAY_STRUCTURE

    def __init__(self, *children, **kwargs):
        if "selector" not in kwargs or "expected" not in kwargs:
            raise TypeError("StateCase requires selector and expected")
        self.selector = kwargs.pop("selector")
        self.expected = kwargs.pop("expected")
        if kwargs.keys() - {"key"}:
            raise TypeError("Unknown StateCase arguments: %s" %
                            ", ".join(sorted(kwargs)))
        predicate = derived(
            lambda current, expected=self.expected: current == expected,
            self.selector)
        super().__init__(predicate, Overlay(*children), key=kwargs.get("key"))

    def state_signature(self, state):
        return resolve(self.selector, state) == resolve(self.expected, state)

    def render(self, renderer, state, layout):
        if self.state_signature(state):
            return self.child.render(renderer, state, layout)
        return []

    def render_children(self):
        return self.child.render_children()

    def replace_preview_children(self, children, placements=None):
        self.child.replace_preview_children(children, placements)
        self._adopt(*tuple(children))


class Override:
    """Apply explicit inherited defaults to an existing object subtree."""

    def __init__(self, content):
        self.content = content
        self.values = []

    def with_font(self, font):
        self.values.append(("font", font))
        return self

    def with_text_color(self, color):
        self.values.append(("text_color", color))
        return self

    def with_button_style(self, style):
        self.values.append(("button_style", style))
        return self

    def apply(self):
        for name, value in self.values:
            self.content.apply_override(name, value)
        return self.content


class Tree:
    """A reusable arranged UI object tree rendered in full."""

    def __init__(self, root, bounds):
        self.root = root
        self.bounds = bounds if isinstance(bounds, Rect) else Rect(*bounds)
        self.layout = LayoutResult()
        self.root.arrange(self.bounds, self.layout)

    def render(self, renderer, state=None):
        current = state if isinstance(state, StateStore) else StateStore((), state)
        return self.root.render(renderer, current, self.layout)

    def rect(self, key):
        return self.layout[key]

    def node(self, key):
        return self.layout.node(key)


class PageDiscoveryContract:
    """Framework-owned AST prefilter contract for declarative page modules."""

    __slots__ = ("strategy", "factory_names", "module_names")

    def __init__(self, strategy, factory_names, module_names=()):
        self.strategy = str(strategy)
        self.factory_names = tuple(str(value) for value in factory_names)
        self.module_names = tuple(str(value) for value in module_names)

    def as_dict(self):
        return {
            "strategy": self.strategy,
            "factory_names": list(self.factory_names),
            "module_names": list(self.module_names),
        }


PAGE_DISCOVERY_CONTRACT = PageDiscoveryContract(
    "core.module_page_instances", ("PageTree", "DeclarativePage"),
    ("ui", "ui.layout"),
)


class PageMetadataSourceContract:
    """Framework-owned source grammar for editable page metadata."""

    __slots__ = ("strategy", "fields")

    def __init__(self, strategy, fields):
        self.strategy = str(strategy)
        self.fields = tuple(str(value) for value in fields)

    def as_dict(self):
        return {"strategy": self.strategy, "fields": list(self.fields)}


PAGE_METADATA_SOURCE_CONTRACT = PageMetadataSourceContract(
    "core.module_page_assignments", ("title", "show_back"))


class DeclarativePage(Tree):
    """An arranged page that discovers and redraws dirty subtrees."""

    discovery_contract = PAGE_DISCOVERY_CONTRACT
    metadata_source_contract = PAGE_METADATA_SOURCE_CONTRACT

    def __init__(self, content, bounds, state=None, page_id=None,
                 state_schema=(), actions=()):
        if not isinstance(page_id, PageKey):
            raise TypeError("DeclarativePage page_id must be a PageKey member")
        self._source = _capture_construction(
            self, names=("Page", "PageTree", "DeclarativePage"))
        super().__init__(content, bounds)
        self.page_key = page_id
        self.page_id = serialize_key(page_id)
        self.state_schema = page_state_keys(self.root, state_schema)
        self.actions = {}
        for action in tuple(actions or ()):
            self.actions[action_wire_id(action)] = action
        for wire_id, action in collect_actions(self.root).items():
            existing = self.actions.get(wire_id)
            if existing is not None and existing != action:
                raise ValueError("Semantic action wire collision: %s" % wire_id)
            self.actions[wire_id] = action
        self.state = StateStore(self.state_schema, state)
        self.initialized = state is not None
        if self.initialized:
            self.root.update(self.state, initialize=True)
            self.root.clear_dirty()

    def initial_state(self):
        return StateStore(self.state_schema)

    def resolve_action(self, wire_id):
        return self.actions.get(str(wire_id))

    def action_metadata(self):
        from .actions import action_metadata
        return tuple(action_metadata(action)
                     for _wire, action in sorted(self.actions.items()))

    def state_metadata(self):
        return self.state.metadata()

    def _fresh_state(self, values=None):
        return StateStore(self.state_schema, values)

    def draw(self, renderer, state=None):
        self.state = self._fresh_state(state)
        self.root.update(self.state, initialize=True)
        set_page_identity = getattr(renderer, "set_semantic_page", None)
        if set_page_identity is not None:
            set_page_identity(self.page_id)
        commands = self.root.render(renderer, self.state, self.layout)
        self.root.clear_dirty()
        self.initialized = True
        return commands

    def update(self, renderer, state=None):
        if state is not None:
            self.state.update(state)
        if not self.initialized:
            return self.draw(renderer, self.state)
        self.root.update(self.state)
        if self.root._dirty >= Dirty.LAYOUT:
            self.layout = LayoutResult()
            self.root.arrange(self.bounds, self.layout)
            return self.draw(renderer, self.state)
        roots = self._dirty_roots()
        commands = []
        for root in roots:
            commands.extend(
                root.render_dirty(renderer, self.state, self.layout))
            root.clear_dirty()
        self._clear_ancestor_flags()
        return commands

    def invalidate(self, key, dirty=Dirty.PAINT):
        self.node(key).invalidate(dirty)

    def _dirty_roots(self):
        roots = []
        for node in self.root.walk():
            if node._dirty == Dirty.CLEAN:
                continue
            children_dirty = any(
                child._dirty != Dirty.CLEAN for child in node.render_children())
            if children_dirty and node.state_signature(self.state) is None:
                continue
            candidate = self._paint_root(node)
            if any(self._is_ancestor(existing, candidate) for existing in roots):
                continue
            roots = [
                existing for existing in roots
                if not self._is_ancestor(candidate, existing)]
            roots.append(candidate)
        return roots

    @staticmethod
    def _paint_root(node):
        candidate = node if node.covers_bounds else None
        current = node
        while current is not None:
            if current._repaint_boundary:
                return current
            current = current.parent
        return candidate or node

    @staticmethod
    def _is_ancestor(ancestor, node):
        current = node
        while current is not None:
            if current is ancestor:
                return True
            current = current.parent
        return False

    def _clear_ancestor_flags(self):
        for node in reversed(tuple(self.root.walk())):
            if any(child._dirty != Dirty.CLEAN
                   for child in node.render_children()):
                continue
            node._dirty = Dirty.CLEAN


PageTree = DeclarativePage


def _command_list(value):
    if value is None:
        return []
    if isinstance(value, (str, dict)):
        return [value]
    return list(value)
