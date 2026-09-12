## Neutral on-demand reflection for declarative pages.

from enum import Enum

from .actions import Action, action_metadata
from .bindings import (
    Binding, StateStore, binding_metadata, resolve, resolve_deep,
)
from .identity import FrameworkKey, serialize_key
from .layout import Grid, LAYOUT_SCHEMA, List, Overlay, When, WrapPanel
from .properties import property_names
from .source import (
    annotate_affected, construction_metadata, layout_provenance,
    property_provenance,
)
from . import REFLECTION_SCHEMA_VERSION


def _value(value):
    if isinstance(value, FrameworkKey):
        return serialize_key(value)
    if isinstance(value, Enum):
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


def _layout_source_contract(node):
    contract = getattr(node, "layout_source_contract", None)
    return None if contract is None else contract.as_dict()


def _properties(node, state):
    result = {}
    bindings = {}
    sources = {}
    specs = dict((item.name, item) for item in node.property_schema)
    for name in property_names(node):
        spec = specs[name]
        value = spec.value_from(node)
        if isinstance(value, Binding):
            bindings[name] = binding_metadata(value, state)
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


def _condition_name(node, child):
    if node.key is not None:
        return str(_value(node.key))
    if child is not None:
        if child.key is not None:
            return str(_value(child.key))
        for name in ("label", "value", "text", "title", "action"):
            value = getattr(child, name, None)
            if value is not None and not isinstance(value, Binding):
                return "%s · %s" % (child.__class__.__name__, _value(value))
        return child.__class__.__name__
    return "Condition"


def _node(node, page, state, path, inherited_visible=True):
    key = node.key
    ref = None if key is None else _value(key)
    try:
        bounds = list(page.layout.rect(node))
    except Exception:
        bounds = None
    own_visible = True
    preview_own_visible = True
    condition = None
    if isinstance(node, When):
        predicate = getattr(node, "_designer_original_predicate", node.predicate)
        try:
            own_visible = bool(resolve(predicate, state))
        except Exception:
            own_visible = False
        try:
            preview_own_visible = bool(resolve(node.predicate, state))
        except Exception:
            preview_own_visible = False
        binding = (binding_metadata(predicate, state)
                   if isinstance(predicate, Binding) else None)
        child = next(iter(node.render_children()), None)
        condition = {
            "name": _condition_name(node, child),
            "result": own_visible,
            "preview_result": preview_own_visible,
            "binding": binding,
            "keys": [] if binding is None else list(binding.get("keys", ())),
            "predicate_source": property_provenance(
                node, "predicate", value=predicate),
            "child": None if child is None else {
                "type": child.__class__.__name__,
                "ref": None if child.key is None else _value(child.key),
            },
        }
    visible = bool(inherited_visible and preview_own_visible)
    properties, bindings, property_sources = _properties(node, state)
    if condition is not None:
        properties["predicate"] = own_visible
        property_sources["predicate"] = condition["predicate_source"]
        if condition["binding"] is not None:
            bindings["predicate"] = condition["binding"]
    children = [
        _node(child, page, state, "%s.%d" % (path, index), visible)
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
        "own_visible": own_visible,
        "preview_own_visible": preview_own_visible,
        "condition": condition,
        "layout": _layout(node),
        "layout_source_contract": _layout_source_contract(node),
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
            "selectable": bool(node.canvas_selectable),
            # Layout containers do not clip their children. Keep the effective
            # clip explicit so consumers never infer it from arranged bounds.
            "clip_bounds": None,
        },
        "structure": _structure(node, children),
        "children": children,
    }


def _dependency_indexes(tree, state_schema):
    states = dict((item["key"], {
        "key": item["key"], "name": item.get("name"),
        "properties": [], "conditions": [],
    }) for item in state_schema)
    conditions = []

    def state_entry(key):
        return states.setdefault(key, {
            "key": key, "name": key.rsplit(".", 1)[-1],
            "properties": [], "conditions": [],
        })

    def visit(node):
        condition = node.get("condition")
        if condition is not None:
            item = {
                "node_id": node.get("id"),
                "node_ref": node.get("ref"),
                "name": condition.get("name"),
                "result": condition.get("result"),
                "preview_result": condition.get("preview_result"),
                "keys": list(condition.get("keys") or ()),
                "binding": condition.get("binding"),
                "child": condition.get("child"),
            }
            conditions.append(item)
            for key in item["keys"]:
                state_entry(key)["conditions"].append({
                    "node_id": item["node_id"],
                    "node_ref": item["node_ref"],
                    "name": item["name"],
                    "result": item["result"],
                })
        for name, binding in (node.get("bindings") or {}).items():
            if name == "predicate" and condition is not None:
                continue
            direct = set(binding.get("direct_keys") or ())
            for key in binding.get("keys") or ():
                state_entry(key)["properties"].append({
                    "node_id": node.get("id"),
                    "node_ref": node.get("ref"),
                    "node_type": node.get("type"),
                    "property": name,
                    "direct": key in direct,
                })
        for child in node.get("children") or ():
            visit(child)

    visit(tree)
    for item in states.values():
        item["property_count"] = len(item["properties"])
        item["condition_count"] = len(item["conditions"])
        item["affected_count"] = item["property_count"] + item["condition_count"]
    return {"states": states, "conditions": conditions}


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
    state_schema = current.metadata()
    dependencies = _dependency_indexes(tree, state_schema)
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
        "state_schema": state_schema,
        "dependencies": dependencies,
        "actions": list(page.action_metadata()),
        "bindings": [
            {"key": item["key"], "runtime_type": item.get("type"),
             "nullable": item.get("nullable", False)}
            for item in state_schema
        ],
        "selection": {"ids": [], "primary": None},
        "clipboard": {"available": False, "nodes": []},
        "diagnostics": [],
        "tree": tree,
    }
