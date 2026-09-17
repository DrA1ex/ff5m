## Neutral on-demand reflection for declarative pages.

from enum import Enum

from .actions import Action, action_metadata
from .bindings import (
    Binding, StateStore, binding_metadata, resolve, resolve_deep,
)
from .identity import FrameworkKey, serialize_key
from .layout import Grid, LAYOUT_SCHEMA, List, ListView, Overlay, When, WrapPanel
from .components import Frame
from .properties import property_names
from .source import (
    annotate_affected, component_parameter_provenance, component_property_parameter, construction_metadata,
    layout_provenance, property_provenance, structure_provenance,
    style_provenance,
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


def _parameter_value(value, state):
    if isinstance(value, Binding):
        return {"binding": binding_metadata(value, state)}
    if isinstance(value, tuple):
        return [_parameter_value(item, state) for item in value]
    if isinstance(value, list):
        return [_parameter_value(item, state) for item in value]
    if isinstance(value, dict):
        return {str(key): _parameter_value(item, state)
                for key, item in value.items()}
    return _json_value(value)


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


def _property_schema(node):
    result = []
    for spec in node.property_schema:
        value = spec.as_dict()
        fallbacks = getattr(node, "_computed_property_fallbacks", {})
        if spec.name in fallbacks:
            value["default"] = fallbacks[spec.name]
            value["has_default"] = True
        parameter = component_property_parameter(node, spec.name)
        if parameter is not None:
            value["has_default"] = False
            value["source"] = dict(value["source"], name=parameter.name, position=None, index=None)
            value["source_name"] = parameter.name
        result.append(value)
    return result


def _layout_schema(node):
    fallbacks = getattr(node, "_computed_layout_fallbacks", {})
    result = []
    for spec in LAYOUT_SCHEMA:
        value = spec.as_dict()
        if spec.name in fallbacks:
            value["default"] = fallbacks[spec.name]
            value["has_default"] = True
        result.append(value)
    return result


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
    result["provenance"] = structure_provenance(node)
    if isinstance(node, Grid):
        result["source_form"] = getattr(node, "_grid_source_form", "matrix")
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


def _list_view_metadata(node, state):
    return {
        "items_binding": binding_metadata(node.items_binding, state)
                         if isinstance(node.items_binding, Binding) else None,
        "item_key_binding": binding_metadata(node.item_key_binding, state),
        "template_key_binding": binding_metadata(node.template_key_binding, state),
        "direction": node.direction,
        "gap": node.gap,
        "fallback_template": node.fallback_template,
        "templates": [{
            "name": template.name,
            "source": construction_metadata(template),
            "root_source": construction_metadata(template.root),
            "sample": _json_value(template.sample),
        } for template in node.templates],
    }


def _node(node, page, state, path, inherited_visible=True):
    node_state = getattr(node, "_item_scope", state)
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
            own_visible = bool(resolve(predicate, node_state))
        except Exception:
            own_visible = False
        try:
            preview_own_visible = bool(resolve(node.predicate, node_state))
        except Exception:
            preview_own_visible = False
        binding = (binding_metadata(predicate, node_state)
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
    properties, bindings, property_sources = _properties(node, node_state)
    if condition is not None:
        properties["predicate"] = own_visible
        property_sources["predicate"] = condition["predicate_source"]
        if condition["binding"] is not None:
            bindings["predicate"] = condition["binding"]
    children = [
        _node(child, page, node_state, "%s.%d" % (path, index), visible)
        for index, child in enumerate(node.render_children())
    ]
    source = construction_metadata(node)
    template_instance = getattr(node, "_template_instance", None)
    template_definition = getattr(node, "_template_definition", None)
    component_template_instance = getattr(
        node, "_component_template_instance", None)
    sharing_template = (template_instance or template_definition
                        or component_template_instance)
    if source is not None and sharing_template:
        source = dict(source)
        source["sharing"] = {
            "reason": "template",
            "template": sharing_template["template"],
        }
    anchor = (source or {}).get("anchor") or {}
    fingerprint = anchor.get("fingerprint")
    stable_id = ref or ("source:%s:%s" % (fingerprint, path)
                        if fingerprint else "path:%s" % path)
    parent_contract = getattr(node.parent, "structure_contract", None)
    component_template_metadata = None
    if component_template_instance is not None:
        component_template_metadata = dict(
            (name, (_parameter_value(value, node_state)
                    if name == "parameters" and value is not None
                    else _json_value(value)))
            for name, value in component_template_instance.items())
        parameters = component_template_instance.get("parameters") or {}
        component_template_metadata["parameter_sources"] = (
            {} if not component_template_instance.get("root")
            else dict((
                name, component_parameter_provenance(node, name, value))
                for name, value in parameters.items()))
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
        "property_schema": _property_schema(node),
        "layout_schema": _layout_schema(node),
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
        "container": isinstance(node, (Grid, List, Overlay, WrapPanel, Frame)),
        "style": None if node._style_id is None else _value(node._style_id),
        "style_source": (None if node._style_id is None
                         else style_provenance(node)),
        "property_origins": _json_value(
            getattr(node, "_computed_property_origins", {})),
        "layout_origins": _json_value(
            getattr(node, "_computed_layout_origins", {})),
        "list_view": (_list_view_metadata(node, state)
                      if isinstance(node, ListView) else None),
        "template_instance": (None if template_instance is None
                              else dict(template_instance)),
        "template_definition": (None if template_definition is None
                                else dict(template_definition)),
        "component_template_instance": component_template_metadata,
        "definition_owned": bool(
            component_template_instance is not None
            and not component_template_instance.get("root")),
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



def _component_template_catalog(page):
    result = {}

    definitions = {}

    def add(template, registered):
        key = _value(template.id)
        definition = definitions.get(key)
        if definition is not None and definition is not template:
            raise ValueError(
                "ComponentTemplate identity %s refers to multiple definitions" %
                key)
        definitions[key] = template
        existing = result.get(key)
        if existing is not None:
            if registered:
                existing["registered"] = True
            return
        result[key] = {
            "id": key,
            "symbol": "%s.%s" % (
                template.id.__class__.__name__, template.id.name),
            "parameters": [value.name for value in template.parameters],
            "source": construction_metadata(template),
            "root_source": construction_metadata(template.root),
            "registered": bool(registered),
        }

    for template in getattr(page, "component_templates", ()):
        add(template, True)
    for node in page.root.walk():
        template = getattr(node, "_component_template", None)
        if template is not None:
            add(template, False)
    return list(result.values())


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
        "styles": (None if getattr(page, "styles", None) is None
                   else _json_value(page.styles.as_dict())),
        "component_templates": _json_value(
            _component_template_catalog(page)),
        "tree": tree,
    }
