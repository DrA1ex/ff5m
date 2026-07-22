## Public Feather UI package.

__version__ = "2.0.0"
FRAMEWORK_API_VERSION = 2
REFLECTION_SCHEMA_VERSION = 2
FRAMEWORK_CAPABILITIES = (
    "typed-identities",
    "stable-key-namespaces",
    "semantic-actions",
    "typed-state",
    "runtime-reflection",
    "source-provenance",
    "structural-editing",
    "package-relative-themes",
)


def framework_manifest():
    """Return the stable, serializable framework compatibility contract."""
    return {
        "name": "feather-ui",
        "version": __version__,
        "api_version": FRAMEWORK_API_VERSION,
        "reflection_schema_version": REFLECTION_SCHEMA_VERSION,
        "capabilities": list(FRAMEWORK_CAPABILITIES),
    }

from .renderer import (
    COLOR_CYAN, COLOR_ROLES, COLOR_TEXT, COLOR_VIOLET, CONTENT_BOTTOM,
    FALLBACK_THEME, FOOTER_HEIGHT, FOOTER_Y, HEADER_BOTTOM,
    MAX_ATOMIC_DRAW, MAX_PENDING_DRAW, SCREEN_HEIGHT, SCREEN_WIDTH,
    THEME_DIRECTORY, FeatherRenderer, Page, PrintState,
    rectangles_overlap,
)
from .identity import CommandKey, FrameworkKey, PageKey, StateKey, serialize_key
from .actions import (
    Action, Back, CancellationHint, Command, CompletionHint,
    ContinuousMovementHint, CoolingHint, DispatchResult, HeatingHint,
    HomingHint, Increment, MotorStateHint, MovementHint, Navigate, ProgressHint,
    ProbingHint, Replace, Router, SetValue, SimulationHint, Toggle,
    action_metadata, action_wire_id, collect_actions,
)
from .bindings import (
    Binding, DerivedBinding, DirectBinding, StateSpec, StateStore,
    bind, derived, state, state_spec,
)
from .properties import (
    CreationFieldSpec, EditorSpec, Invalidation, PropertySpec, RewritePolicy,
    SourceSpec, ValidationSpec, property_names, property_schema,
)
from .reflection import reflect_page
from .source import capture_enabled, source_capture
from .layout import (
    EMPTY, FLEX, Column, CreationContract, DeclarativePage, Dirty, Equal, EqualTracks, Flex,
    Grid, Insets, LAYOUT_SCHEMA, LayoutResult, List, Node, Overlay, Override,
    PageTree, Rect, Row, Spacer, Span, StructureContract, Tree, When, WrapPanel,
    split, subdivision_positions,
)
from .components import (
    Button, ButtonStyle, CornerMarks, Crosshair, Dialog, DotGrid, Fill,
    Hitbox, JoystickKnob, Metric, Panel, Section, Stroke, Text,
    VerticalGauge, VerticalScale,
)

__all__ = (
    "__version__", "FRAMEWORK_API_VERSION", "REFLECTION_SCHEMA_VERSION",
    "FRAMEWORK_CAPABILITIES", "framework_manifest",
    "COLOR_CYAN", "COLOR_ROLES", "COLOR_TEXT", "COLOR_VIOLET",
    "CONTENT_BOTTOM", "FALLBACK_THEME", "FOOTER_HEIGHT", "FOOTER_Y",
    "HEADER_BOTTOM", "MAX_ATOMIC_DRAW", "MAX_PENDING_DRAW",
    "SCREEN_HEIGHT", "SCREEN_WIDTH", "THEME_DIRECTORY",
    "rectangles_overlap", "FeatherRenderer", "Page", "PrintState",
    "FrameworkKey", "PageKey", "StateKey", "CommandKey", "serialize_key",
    "Action", "Navigate", "Back", "Replace", "SetValue", "Toggle",
    "Increment", "Command", "SimulationHint", "HomingHint",
    "MovementHint", "ContinuousMovementHint", "MotorStateHint", "HeatingHint",
    "CoolingHint", "ProbingHint", "ProgressHint", "CompletionHint",
    "CancellationHint", "DispatchResult", "Router", "action_metadata",
    "action_wire_id", "collect_actions",
    "Binding", "DirectBinding", "DerivedBinding", "StateSpec", "StateStore",
    "state", "state_spec", "bind", "derived",
    "Invalidation", "RewritePolicy", "EditorSpec", "SourceSpec",
    "ValidationSpec", "CreationFieldSpec", "PropertySpec", "property_names",
    "property_schema",
    "reflect_page", "source_capture", "capture_enabled",
    "EMPTY", "FLEX", "Column", "CreationContract", "DeclarativePage", "Dirty", "Equal",
    "EqualTracks", "Flex", "Grid", "Insets", "LAYOUT_SCHEMA", "LayoutResult", "List",
    "Node", "Overlay", "Override", "PageTree", "Rect", "Row",
    "Spacer", "Span", "StructureContract", "Tree", "When", "WrapPanel", "split",
    "subdivision_positions", "Button",
    "ButtonStyle", "CornerMarks", "Crosshair", "Dialog", "DotGrid",
    "Fill", "Hitbox", "JoystickKnob", "Metric", "Panel", "Section",
    "Stroke", "Text", "VerticalGauge", "VerticalScale",
)
