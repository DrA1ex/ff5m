## Neutral runtime reflection for declarative pages.

from enum import Enum

from .actions import Action, action_metadata
from .bindings import Binding, StateStore, resolve, resolve_deep
from .identity import serialize_key
from .layout import Grid, LAYOUT_SCHEMA, List, Overlay, When, WrapPanel
from .properties import property_names
from .source import (
    annotate_affected, construction_metadata, layout_provenance,
    property_provenance,
)
from . import REFLECTION_SCHEMA_VERSION


def _value(value):
    if isinstance(value, Enum):
        try:
            return serialize_key(value)
        except TypeError:
            return value.value
    return value


def _json_value(value):
    if isinstance(value, Action):
        return action_metadata(value)
    if isinstance(value, Enum):
        return _value(value)
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(_json_value(key)): _json_value(item)
                for key, item in value.items()}
    return value


def _resolve(value, state):
    return _json_value(resolve_deep(value, state))


def _layout(node):
    value = node.layout_options
    return {
        "width": value.width,
        "height": value.height,
        "grow": value.grow,
        "margin": [value.margin.left, value.margin.top,
                   value.margin.right, value.margin.bottom],
        "padding": [value.padding.left, value.padding.top,
                    value.padding.right, value.padding.bottom],
        "horizontal": value.horizontal,
        "vertical": value.vertical,
        "offset": [value.offset_x, value.offset_y],
        "allow_overflow": value.allow_overflow,
    }


def _properties(node, state):
    result = {}
    bindings = {}
    sources = {}
    specs = dict((item.name, item) for item in node.property_schema)
    for name in property_names(node):
        spec = specs[name]
        value = spec.value_from(node)
        if isinstance(value, Binding):
            bindings[name] = value.as_dict()
        try:
            result[name] = _resolve(value, state)
        except Exception as error:
            result[name] = {"error": str(error)}
        sources[name] = property_provenance(
            node, name, specs.get(name), value=value)
    return result, bindings, sources


def _structure(node, children):
    contract = getattr(node, "structure_contract", None)
    if contract is None:
        return None
    result = contract.as_dict()
    slots = []
    if isinstance(node, Grid):
        by_child = dict((id(item.child), item) for item in node.cells)
        for index, child in enumerate(node.render_children()):
            cell = by_child[id(child)]
            slot = {
                "index": index, "column": cell.column, "row": cell.row,
                "column_span": cell.column_span, "row_span": cell.row_span,
            }
            children[index]["structure_slot"] = dict(slot)
            slots.append(slot)
    else:
        for index, child in enumerate(children):
            slot = {"index": index}
            child["structure_slot"] = dict(slot)
            slots.append(slot)
    result["slots"] = slots
    return result


def _node(node, page, state, path):
    key = node.key
    ref = None if key is None else _value(key)
    try:
        bounds = list(page.layout.rect(node))
    except Exception:
        bounds = None
    visible = True
    if isinstance(node, When):
        try:
            visible = bool(resolve(node.predicate, state))
        except Exception:
            visible = False
    properties, bindings, property_sources = _properties(node, state)
    children = [
        _node(child, page, state, "%s.%d" % (path, index))
        for index, child in enumerate(node.render_children())
    ]
    source = construction_metadata(node)
    anchor = (source or {}).get("anchor") or {}
    fingerprint = anchor.get("fingerprint")
    stable_id = ref or ("source:%s:%s" % (fingerprint, path)
                        if fingerprint else "path:%s" % path)
    parent_contract = getattr(node.parent, "structure_contract", None)
    return {
        "id": stable_id,
        "ref": ref,
        "identity": {
            "stable": stable_id, "ref": ref, "path": path,
            "source_fingerprint": fingerprint,
        },
        "type": node.__class__.__name__,
        "bounds": bounds,
        "visible": visible,
        "layout": _layout(node),
        "property_schema": [item.as_dict() for item in node.property_schema],
        "layout_schema": [item.as_dict() for item in LAYOUT_SCHEMA],
        "properties": properties,
        "bindings": bindings,
        "source": source,
        "property_sources": property_sources,
        "layout_sources": layout_provenance(node),
        "actions": dict((name, action_metadata(value))
                        for name, value in node.__dict__.items()
                        if isinstance(value, Action)),
        "action_sources": dict((
            name, property_provenance(node, name, value=value))
            for name, value in node.__dict__.items()
            if isinstance(value, Action)),
        "container": isinstance(node, (Grid, List, Overlay, WrapPanel)),
        "canvas": {
            "capabilities": (
                [] if parent_contract is None else list(parent_contract.canvas)),
            "placement": (
                None if parent_contract is None else parent_contract.placement),
            "selectable": True,
        },
        "structure": _structure(node, children),
        "children": children,
    }


def reflect_page(page, state=None):
    """Return a Designer-neutral description of one arranged page."""
    if state is None:
        current = page.state.copy()
    elif isinstance(state, StateStore):
        current = state.copy()
    else:
        current = page._fresh_state(state)
    tree = _node(page.root, page, current, "0")
    annotate_affected(tree)
    return {
        "protocol_version": 2,
        "schema_version": REFLECTION_SCHEMA_VERSION,
        "page": {
            "id": page.page_id,
            "key": page.page_key.symbol,
            "bounds": list(page.bounds),
            "source": construction_metadata(page),
            "viewport": {"x": page.bounds.x, "y": page.bounds.y,
                         "width": page.bounds.width,
                         "height": page.bounds.height},
        },
        "state": current.as_dict(serialized=True),
        "state_schema": current.metadata(),
        "actions": list(page.action_metadata()),
        "bindings": [
            {"key": item["key"], "runtime_type": item.get("type"),
             "nullable": item.get("nullable", False)}
            for item in current.metadata()
        ],
        "selection": {"ids": [], "primary": None},
        "clipboard": {"available": False, "nodes": []},
        "diagnostics": [],
        "tree": tree,
    }
