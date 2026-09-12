## Public Feather UI package.

__version__ = "2.3.0"
FRAMEWORK_API_VERSION = 2
REFLECTION_SCHEMA_VERSION = "2.1.0"
FRAMEWORK_CAPABILITIES = (
    "typed-identities",
    "stable-key-namespaces",
    "semantic-actions",
    "typed-state",
    "on-demand-reflection",
    "on-demand-source-provenance",
    "binding-source-authoring",
    "source-edit-contracts",
    "structural-editing",
    "package-relative-themes",
    "typed-theme-tokens",
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

_EXPORT_GROUPS = (
    ("theme", (
        "ThemeColor", "ThemeRole", "ResolvedTheme",
        "DEFAULT_THEME_ROLES", "normalize_theme_token", "resolve_theme",
    )),
    ("theme_catalog", (
        "DEFAULT_THEME", "FALLBACK_THEME", "MAX_THEME_FILE_BYTES",
        "THEME_DIRECTORY", "THEME_SCHEMA_PATH", "USER_THEME_DIRECTORY",
        "ThemeCatalog", "ThemeFileIssue", "ThemeFileTooLarge",
        "ThemeSchemaError", "normalize_theme_name", "validate_theme_data",
    )),
    ("renderer", (
        "CONTENT_BOTTOM", "FOOTER_HEIGHT", "FOOTER_Y",
        "HEADER_BOTTOM", "MAX_ATOMIC_DRAW", "MAX_PENDING_DRAW",
        "SCREEN_HEIGHT", "SCREEN_WIDTH",
        "FeatherRenderer", "rectangles_overlap",
    )),
    ("render_worker", (
        "MAX_BATCHES", "MAX_BATCH_BYTES", "RenderBatch",
        "RenderBatchQueue", "TyperRenderWorker",
    )),
    ("render_receipts", (
        "PendingReceipt", "ReceiptMeasurement", "ReceiptTracker",
        "RenderReceipt", "parse_render_receipt",
        "validate_render_receipt_token",
    )),
    ("identity", (
        "CommandKey", "FrameworkKey", "PageKey", "StateKey",
        "serialize_key",
    )),
    ("actions", (
        "Action", "Back", "CancellationHint", "Command", "CompletionHint",
        "ContinuousMovementHint", "CoolingHint", "DismissToast",
        "DispatchResult",
        "EditText", "HeatingHint", "HomingHint", "Increment", "MotorStateHint",
        "MovementHint", "Navigate", "ProgressHint", "ProbingHint",
        "Replace", "Router", "SetValue", "SimulationHint", "Toggle",
        "action_metadata", "action_wire_id", "collect_actions",
    )),
    ("bindings", (
        "BINDING_EXPRESSION_CONTRACT", "STATE_DECLARATION_CONTRACT",
        "Binding", "BindingExpressionContract", "DerivedBinding", "DirectBinding",
        "StateDeclarationContract", "StateSpec", "StateStore", "bind", "derived",
        "state", "state_spec",
    )),
    ("numeric_input", ("NumericInputSpec",)),
    ("properties", (
        "CreationFieldSpec", "EditorSpec", "Invalidation", "PropertySpec",
        "RewritePolicy", "SourceSpec", "ValidationSpec", "property_names",
        "property_schema",
    )),
    ("layout", (
        "EMPTY", "FLEX", "Column", "CreationContract",
        "CreationIdentityContract", "CreationSourceContract",
        "DeclarativePage",
        "Dirty", "Equal", "EqualTracks", "Flex", "Grid", "Insets",
        "LAYOUT_SCHEMA", "LayoutModifierSpec", "LayoutResult", "LayoutSourceContract", "List", "Node", "Overlay",
        "Override", "PAGE_DISCOVERY_CONTRACT", "PageDiscoveryContract",
        "PageTree", "Rect", "Row", "Spacer", "Span",
        "StateCase", "StructureContract", "StructureSourceContract", "Tree", "When", "WrapPanel", "split",
        "subdivision_positions",
    )),
    ("components", (
        "ArrowButton", "Button", "ButtonStyle", "CornerMarks", "Crosshair", "Dialog",
        "DotGrid", "Fill", "Hitbox", "JoystickKnob", "Metric", "Panel",
        "NumericKeypad", "Section", "Stroke", "Text", "ToggleSwitch", "VerticalGauge",
        "VerticalScale",
    )),
)
_EXPORTS = dict(
    (name, module_name)
    for module_name, names in _EXPORT_GROUPS
    for name in names)


def __getattr__(name):
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError("module %r has no attribute %r" % (__name__, name))
    module = __import__("%s.%s" % (__name__, module_name), fromlist=(name,))
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()).union(_EXPORTS))

__all__ = (
    "__version__", "FRAMEWORK_API_VERSION", "REFLECTION_SCHEMA_VERSION",
    "FRAMEWORK_CAPABILITIES", "framework_manifest",
    "ThemeColor", "ThemeRole", "ResolvedTheme",
    "DEFAULT_THEME_ROLES", "normalize_theme_token", "resolve_theme",
    "DEFAULT_THEME", "FALLBACK_THEME", "MAX_THEME_FILE_BYTES",
    "THEME_DIRECTORY", "THEME_SCHEMA_PATH", "USER_THEME_DIRECTORY",
    "ThemeCatalog", "ThemeFileIssue", "ThemeFileTooLarge",
    "ThemeSchemaError", "normalize_theme_name", "validate_theme_data",
    "CONTENT_BOTTOM", "FOOTER_HEIGHT", "FOOTER_Y",
    "HEADER_BOTTOM", "MAX_ATOMIC_DRAW", "MAX_PENDING_DRAW",
    "SCREEN_HEIGHT", "SCREEN_WIDTH",
    "MAX_BATCHES", "MAX_BATCH_BYTES", "RenderBatch", "RenderBatchQueue",
    "TyperRenderWorker",
    "PendingReceipt", "ReceiptMeasurement", "ReceiptTracker",
    "RenderReceipt", "parse_render_receipt",
    "validate_render_receipt_token",
    "rectangles_overlap", "FeatherRenderer",
    "FrameworkKey", "PageKey", "StateKey", "CommandKey", "serialize_key",
    "Action", "Navigate", "Back", "DismissToast", "Replace", "SetValue", "Toggle", "EditText",
    "Increment", "Command", "SimulationHint", "HomingHint",
    "MovementHint", "ContinuousMovementHint", "MotorStateHint", "HeatingHint",
    "CoolingHint", "ProbingHint", "ProgressHint", "CompletionHint",
    "CancellationHint", "DispatchResult", "Router", "action_metadata",
    "action_wire_id", "collect_actions",
    "BindingExpressionContract", "StateDeclarationContract",
    "BINDING_EXPRESSION_CONTRACT", "STATE_DECLARATION_CONTRACT",
    "Binding", "DirectBinding", "DerivedBinding", "StateSpec", "StateStore",
    "state", "state_spec", "bind", "derived",
    "NumericInputSpec",
    "Invalidation", "RewritePolicy", "EditorSpec", "SourceSpec",
    "ValidationSpec", "CreationFieldSpec", "PropertySpec", "property_names",
    "property_schema",
    "EMPTY", "FLEX", "Column", "CreationContract",
    "CreationIdentityContract", "CreationSourceContract", "DeclarativePage",
    "Dirty", "Equal", "EqualTracks", "Flex", "Grid", "Insets",
    "LAYOUT_SCHEMA", "LayoutModifierSpec", "LayoutResult",
    "LayoutSourceContract", "List",
    "Node", "Overlay", "Override", "PAGE_DISCOVERY_CONTRACT",
    "PageDiscoveryContract", "PageTree", "Rect", "Row",
    "Spacer", "Span", "StateCase", "StructureContract", "StructureSourceContract", "Tree", "When", "WrapPanel", "split",
    "subdivision_positions", "ArrowButton", "Button",
    "ButtonStyle", "CornerMarks", "Crosshair", "Dialog", "DotGrid",
    "Fill", "Hitbox", "JoystickKnob", "Metric", "NumericKeypad", "Panel",
    "Section", "Stroke", "Text", "ToggleSwitch", "VerticalGauge", "VerticalScale",
)
