## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""The native node engine that every framework node is built on.

``feather_engine`` owns each node's parent, dirty level, layout revision,
flags and layout options, the layout's geometry values, and each page's
retained geometry, action table, state values and dependency index. It
arranges and renders the node tree, calling the hooks Python classes
override, draws the renderer's primitives, measures text in the font faces
and writes Typer commands. The framework has no Python implementation of
them, so it cannot start without a matching engine.
"""

ENGINE_ABI = 119

try:
    import feather_engine
except ImportError as installed_error:
    # A deployment without a package manager (Forge-X on the printer) ships
    # the engine built for its interpreter inside this package.
    try:
        from . import feather_engine
    except ImportError as packaged_error:
        raise ImportError(
            "The Feather UI framework needs the feather_engine extension (ABI %d) "
            "installed for this interpreter or shipped in the ui package: %s; %s" % (
                ENGINE_ABI, installed_error, packaged_error)) from packaged_error

if getattr(feather_engine, "ABI", None) != ENGINE_ABI:
    raise ImportError("feather_engine at %s has ABI %r; the framework needs ABI %d" % (
        feather_engine.__file__, getattr(feather_engine, "ABI", None), ENGINE_ABI))

NodeBase = feather_engine.NodeBase
# Node state stored in the engine record rather than the instance dictionary.
NODE_FIELDS = feather_engine.NODE_FIELDS
# The descriptor of a declared node field: its value lives in the node's engine
# record.
Field = feather_engine.Field
# A view of a node's layout fields, made on each read of ``node.layout_options``;
# setting a field relayouts its node, and views compare by value.
LayoutOptions = feather_engine.LayoutOptions
LAYOUT_FIELDS = feather_engine.LAYOUT_FIELDS
# A template's clone takes each node's record in one native pass; only values that are not immutable call back.
copy_records = feather_engine.copy_records

# Immutable layout geometry and the track arithmetic of rows, columns and grids.
Rect = feather_engine.Rect
Insets = feather_engine.Insets
Flex = feather_engine.Flex
EqualTracks = feather_engine.EqualTracks
# An immutable Grid child placed at a column and row with its spans.
GridCell = feather_engine.GridCell
aligned_position = feather_engine.aligned_position
normalize_tracks = feather_engine.normalize_tracks
resolve_tracks = feather_engine.resolve_tracks
split = feather_engine.split
split_sizes = feather_engine.split_sizes
# Each page's retained geometry: every node's rect and slot, and the refs.
LayoutResult = feather_engine.LayoutResult
CommandList = feather_engine.CommandList
CommandBatch = feather_engine.CommandBatch
BinaryCommand = feather_engine.BinaryCommand
# Each page's semantic actions by wire id, each with a stable index.
ActionTable = feather_engine.ActionTable
# Each page's state values by schema key, with the keys changed since a render.
StateStoreBase = feather_engine.StateStoreBase
# Whether a value is a portable tree (None, bool, int, float, str and given
# scalar types in lists, tuples and str-keyed dicts), and whether it is a tree
# of exact built-ins whose copy runs no user-defined code.
portable = feather_engine.portable
plain_tree = feather_engine.plain_tree
# resolve() and Component's signature freezing for the framework's bindings.
BindingResolver = feather_engine.BindingResolver
# StyleSheet.apply over a node tree for the framework's property sources and layout fields.
StyleApplier = feather_engine.StyleApplier
# The state keys each node of a page reads, and the nodes reading each key.
DependencyIndex = feather_engine.DependencyIndex
# Font faces and the word-v1 text measurement on them.
FontCatalog = feather_engine.FontCatalog
FontMetricsBase = feather_engine.FontMetricsBase
# The engine's count of font metric edits: a catalog read before an edit is
# read again, so measurement observes every edit.
font_edit = feather_engine.font_edit
font_edits = feather_engine.font_edits
# Typer display-list commands.
quote = feather_engine.quote
fill_command = feather_engine.fill_command
stroke_command = feather_engine.stroke_command
hitbox_command = feather_engine.hitbox_command
text_command = feather_engine.text_command
button_command = feather_engine.button_command
# One intrinsic size question to a node, answered once per layout pass.
measure = feather_engine.measure
# The engine base of the framework's renderer, whose drawing primitives are
# NATIVE_METHODS["renderer"].
RendererBase = feather_engine.RendererBase
# The engine's native methods by kind ("list", "grid", "wrap_panel",
# "overlay", "single_child", "renderer", "fill", "stroke", "panel", "text",
# "button"): method descriptors the framework's classes assign, which the
# engine calls directly while it walks the tree.
NATIVE_METHODS = feather_engine.NATIVE_METHODS
# The page's update and its paint policy: the stale dependencies and hosted
# nodes commit updates, the update walk, a frame of named
# refs, the rendering of an update (the partial path that repaints only the
# dirty repaint boundaries, a full draw or a repaint), whether only the repaint
# boundaries changed, which nodes the dirty nodes repaint, the state a node
# paints with, whether a subtree covers a rect, and the fill restoring the
# pixels below a transparent dirty root.
refresh_stale_dependencies = feather_engine.refresh_stale_dependencies
update_hosted = feather_engine.update_hosted
update_nodes = feather_engine.update_nodes
update_refs = feather_engine.update_refs
render_updates = feather_engine.render_updates
regions_compatible = feather_engine.regions_compatible
dirty_roots = feather_engine.dirty_roots
paint_state = feather_engine.paint_state
subtree_covers = feather_engine.subtree_covers
background_repair = feather_engine.background_repair
# The build side of a page's frames (a mirror of its tree, built without
# calling Python) and the number that changes when a node class's behaviour
# does, which a page's remembered build refusal is keyed on.
FrameBuilder = feather_engine.FrameBuilder
FrameLoop = feather_engine.FrameLoop
# A screen tree a FrameLoop draws on its own thread from a snapshot taken between
# write transactions.
ScreenFrames = feather_engine.ScreenFrames
behavior_epoch = feather_engine.behavior_epoch
page_structure = feather_engine.page_structure
collect_actions = feather_engine.collect_actions
style_rules_match = feather_engine.style_rules_match
dialog_layout = feather_engine.dialog_layout
status_dialog_layout = feather_engine.status_dialog_layout
renderer_text_width = feather_engine.renderer_text_width
renderer_normalize_font = feather_engine.renderer_normalize_font
renderer_normalize_font_for_text = feather_engine.renderer_normalize_font_for_text
