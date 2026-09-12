## Typed state declarations and bindings for the Feather UI framework.

import copy
import inspect
from enum import Enum

from .identity import StateKey, serialize_key
from .theme import ThemeColor, ThemeRole


_UNAVAILABLE = object()


class StateDeclarationContract:
    """Framework-owned source grammar for one ``StateKey`` member."""

    __slots__ = (
        "strategy", "factory_names", "positional_fields", "editable_fields",
        "field_codecs", "portable_types", "supported_forms",
    )

    def __init__(self, strategy, factory_names, positional_fields,
                 editable_fields, field_codecs, portable_types,
                 supported_forms=("direct_enum_assignment",)):
        self.strategy = str(strategy)
        self.factory_names = tuple(str(value) for value in factory_names)
        self.positional_fields = tuple(str(value) for value in positional_fields)
        self.editable_fields = tuple(str(value) for value in editable_fields)
        self.field_codecs = dict((str(name), str(codec))
                                 for name, codec in field_codecs.items())
        self.portable_types = tuple(portable_types)
        self.supported_forms = tuple(str(value) for value in supported_forms)

    def as_dict(self):
        return {
            "strategy": self.strategy,
            "factory_names": list(self.factory_names),
            "positional_fields": list(self.positional_fields),
            "editable_fields": list(self.editable_fields),
            "field_codecs": dict(self.field_codecs),
            "portable_types": [_type_name(value)
                               for value in self.portable_types],
            "supported_forms": list(self.supported_forms),
        }


class BindingExpressionContract:
    """Framework-owned grammar for authoring a derived expression."""

    __slots__ = (
        "strategy", "factory_names", "callable_forms", "allowed_ast_nodes",
        "allowed_globals", "portable_default_types", "sharing",
    )

    def __init__(self, strategy, factory_names, callable_forms,
                 allowed_ast_nodes, allowed_globals=(),
                 portable_default_types=(), sharing="catalog_uses"):
        self.strategy = str(strategy)
        self.factory_names = tuple(str(value) for value in factory_names)
        self.callable_forms = tuple(str(value) for value in callable_forms)
        self.allowed_ast_nodes = tuple(str(value) for value in allowed_ast_nodes)
        self.allowed_globals = tuple(str(value) for value in allowed_globals)
        self.portable_default_types = tuple(portable_default_types)
        self.sharing = str(sharing)

    def as_dict(self):
        return {
            "strategy": self.strategy,
            "factory_names": list(self.factory_names),
            "callable_forms": list(self.callable_forms),
            "allowed_ast_nodes": list(self.allowed_ast_nodes),
            "allowed_globals": list(self.allowed_globals),
            "portable_default_types": [
                _type_name(value) for value in self.portable_default_types],
            "sharing": self.sharing,
        }


_STATE_FIELDS = (
    "value_type", "default", "minimum", "maximum", "choices", "mutable",
    "unit", "category", "simulation_role", "simulation_home",
)
STATE_DECLARATION_CONTRACT = StateDeclarationContract(
    "core.state_enum_member", ("state",), _STATE_FIELDS, _STATE_FIELDS[1:],
    dict((name, "python_literal") for name in _STATE_FIELDS),
    (bool, int, float, str),
)
BINDING_EXPRESSION_CONTRACT = BindingExpressionContract(
    "core.python_expression", ("derived",),
    ("inline_lambda", "same_module_single_return"),
    (
        "Expression", "BoolOp", "BinOp", "UnaryOp", "IfExp", "Compare",
        "Name", "Load", "Constant", "Attribute", "Subscript", "Slice",
        "Tuple", "List", "Dict",
        "Set", "JoinedStr", "FormattedValue", "And", "Or", "Not", "UAdd",
        "USub", "Add", "Sub", "Mult", "Div", "FloorDiv", "Mod", "Pow",
        "Eq", "NotEq", "Lt", "LtE", "Gt", "GtE", "Is", "IsNot", "In",
        "NotIn",
    ),
    allowed_globals=("ThemeColor", "ThemeRole"),
    portable_default_types=(
        type(None), str, int, float, bool, tuple, list, dict, Enum),
)


def _capture_binding(binding, names=()):
    return None


def _install_source_hook(capture_binding):
    """Install the optional neutral binding provenance hook."""
    global _capture_binding
    _capture_binding = capture_binding


def _type_name(value_type):
    return "%s.%s" % (
        getattr(value_type, "__module__", "builtins"),
        getattr(value_type, "__qualname__", getattr(value_type, "__name__", str(value_type))),
    )


def _json_value(value):
    if isinstance(value, Enum):
        try:
            return serialize_key(value)
        except TypeError:
            return value.value
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return dict((str(_json_value(key)), _json_value(item))
                    for key, item in value.items())
    return value


class StateSpec:
    """Metadata and validation for one application-defined state key."""

    __slots__ = (
        "value_type", "default", "minimum", "maximum", "choices",
        "mutable", "unit", "category", "simulation_role", "simulation_home",
        "declaration_source_contract",
    )

    def __init__(self, value_type, default=_UNAVAILABLE, minimum=None,
                 maximum=None, choices=None, mutable=True, unit=None,
                 category=None, simulation_role=None, simulation_home=None):
        if not isinstance(value_type, type):
            raise TypeError("state value_type must be a type")
        self.value_type = value_type
        self.default = default
        self.minimum = minimum
        self.maximum = maximum
        self.choices = None if choices is None else tuple(choices)
        self.mutable = bool(mutable)
        self.unit = None if unit is None else str(unit)
        self.category = None if category is None else str(category)
        self.simulation_role = (None if simulation_role is None
                                else str(simulation_role).strip())
        if self.simulation_role == "":
            raise ValueError("simulation_role must not be empty")
        self.simulation_home = simulation_home
        self.declaration_source_contract = None
        if self.simulation_home is not None and self.simulation_role is None:
            raise ValueError("simulation_home requires simulation_role")
        if self.simulation_home is not None:
            self.validate(self.simulation_home)
        if default is not _UNAVAILABLE:
            self.validate(default)

    @property
    def available(self):
        return self.default is not _UNAVAILABLE

    def default_value(self):
        if not self.available:
            raise ValueError("State value has no default")
        return copy.deepcopy(self.default)

    def validate(self, value):
        if value is None:
            if self.default is None:
                return value
            raise TypeError("State value cannot be None")
        if self.value_type is float and isinstance(value, int):
            value = float(value)
        elif self.value_type is int and isinstance(value, bool):
            raise TypeError("Boolean is not a valid integer state value")
        elif not isinstance(value, self.value_type):
            raise TypeError("Expected %s state value, got %s" % (
                self.value_type.__name__, type(value).__name__))
        if self.minimum is not None and value < self.minimum:
            raise ValueError("State value is below minimum %s" % self.minimum)
        if self.maximum is not None and value > self.maximum:
            raise ValueError("State value is above maximum %s" % self.maximum)
        if self.choices is not None and value not in self.choices:
            raise ValueError("State value is not one of the allowed choices")
        return value

    def as_dict(self):
        return {
            "type": _type_name(self.value_type),
            "nullable": self.available and self.default is None,
            "default_available": self.available,
            "default": None if not self.available else _json_value(self.default),
            "minimum": self.minimum,
            "maximum": self.maximum,
            "choices": None if self.choices is None else _json_value(self.choices),
            "mutable": self.mutable,
            "unit": self.unit,
            "category": self.category,
            "simulation_role": self.simulation_role,
            "simulation_home": _json_value(self.simulation_home),
            "declaration_source_contract": (
                None if self.declaration_source_contract is None
                else self.declaration_source_contract.as_dict()),
        }


def state(value_type, default=_UNAVAILABLE, minimum=None, maximum=None,
          choices=None, mutable=True, unit=None, category=None,
          simulation_role=None, simulation_home=None):
    """Declare metadata for a ``StateKey`` enum member."""
    spec = StateSpec(
        value_type, default=default, minimum=minimum, maximum=maximum,
        choices=choices, mutable=mutable, unit=unit, category=category,
        simulation_role=simulation_role, simulation_home=simulation_home)
    spec.declaration_source_contract = STATE_DECLARATION_CONTRACT
    return spec


def state_spec(key):
    if not isinstance(key, StateKey):
        raise TypeError("State binding key must be a StateKey member")
    spec = key.value
    if not isinstance(spec, StateSpec):
        raise TypeError("StateKey members must be declared with state(...)")
    return spec


class StateStore:
    """Validated state values addressed only by typed ``StateKey`` members."""

    __slots__ = ("_schema", "_values")

    def __init__(self, keys=(), values=None):
        self._schema = tuple(_unique_keys(keys))
        self._values = {}
        for key in self._schema:
            spec = state_spec(key)
            if spec.available:
                self._values[key] = spec.default_value()
        if values is not None:
            self.update(values)

    @property
    def schema(self):
        return self._schema

    def copy(self):
        return StateStore(self._schema, self._values)

    def __deepcopy__(self, memo):
        copied = StateStore(self._schema)
        copied._values = copy.deepcopy(self._values, memo)
        return copied

    def __getitem__(self, key):
        if not isinstance(key, StateKey):
            raise TypeError("State values must be addressed by StateKey members")
        if key not in self._schema:
            raise KeyError("State key is not declared by this page: %s" % key)
        if key not in self._values:
            raise KeyError("State value is unavailable: %s" % key)
        return self._values[key]

    def __contains__(self, key):
        return key in self._values

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def keys(self):
        return self._values.keys()

    def values(self):
        return self._values.values()

    def items(self):
        return self._values.items()

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def update(self, values):
        if isinstance(values, StateStore):
            values = tuple(values.items())
        elif hasattr(values, "items"):
            values = tuple(values.items())
        else:
            values = tuple(values)
        staged = {}
        for key, value in values:
            if not isinstance(key, StateKey):
                raise TypeError(
                    "Page state updates must use StateKey members, not %r" % key)
            if key not in self._schema:
                raise KeyError("State key is not declared by this page: %s" % key)
            staged[key] = state_spec(key).validate(value)
        self._values.update(staged)
        return self

    def as_dict(self, serialized=False):
        if serialized:
            return dict((serialize_key(key), _json_value(value))
                        for key, value in self._values.items())
        return copy.deepcopy(self._values)

    def metadata(self):
        result = []
        for key in self._schema:
            item = state_spec(key).as_dict()
            item.update({
                "key": serialize_key(key),
                "name": key.name,
                "value_available": key in self._values,
                "value": _json_value(self._values.get(key)),
            })
            result.append(item)
        return result


def _unique_keys(keys):
    result = []
    seen = set()
    for value in tuple(keys or ()):
        if isinstance(value, type) and issubclass(value, StateKey):
            values = tuple(value)
        else:
            values = (value,)
        for key in values:
            state_spec(key)
            if key not in seen:
                result.append(key)
                seen.add(key)
    return tuple(result)


class Binding:
    """Base class for values resolved from an explicit typed state schema."""

    __slots__ = ()

    @property
    def keys(self):
        raise NotImplementedError

    def resolve(self, store):
        raise NotImplementedError

    def as_dict(self):
        raise NotImplementedError


class DirectBinding(Binding):
    __slots__ = ("key", "_source")

    def __init__(self, key):
        state_spec(key)
        self.key = key
        self._source = None

    @property
    def keys(self):
        return (self.key,)

    def resolve(self, store):
        return store[self.key]

    def as_dict(self):
        return {"kind": "direct", "key": serialize_key(self.key)}


class DerivedBinding(Binding):
    __slots__ = ("function", "inputs", "_source", "expression_source_contract")

    def __init__(self, function, inputs):
        if not callable(function):
            raise TypeError("derived() requires a callable")
        inputs = tuple(inputs)
        if not inputs or not all(isinstance(value, Binding) for value in inputs):
            raise TypeError("derived() inputs must be explicit bindings")
        _validate_callable_arity(function, len(inputs))
        self.function = function
        self.inputs = inputs
        self._source = None
        self.expression_source_contract = None

    @property
    def keys(self):
        result = []
        seen = set()
        for item in self.inputs:
            for key in item.keys:
                if key not in seen:
                    result.append(key)
                    seen.add(key)
        return tuple(result)

    def resolve(self, store):
        return self.function(*tuple(item.resolve(store) for item in self.inputs))

    def as_dict(self):
        return {
            "kind": "derived",
            "inputs": [item.as_dict() for item in self.inputs],
            "callable": getattr(self.function, "__name__", "<callable>"),
        }


def _validate_callable_arity(function, count):
    target = function
    code = getattr(target, "__code__", None)
    bound = getattr(target, "__self__", None) is not None
    if code is None:
        target = getattr(function, "__call__", None)
        code = getattr(target, "__code__", None)
        bound = target is not None
    if code is None:
        # Some extension callables do not expose a Python signature. Their
        # invocation remains the authoritative compatibility check.
        return

    positional = int(code.co_argcount) - int(bound)
    defaults = getattr(target, "__defaults__", None) or ()
    minimum = max(0, positional - len(defaults))
    maximum = None if code.co_flags & 0x04 else positional
    keyword_defaults = getattr(target, "__kwdefaults__", None) or {}
    required_keywords = max(
        0, int(getattr(code, "co_kwonlyargcount", 0))
        - len(keyword_defaults))
    if (required_keywords or count < minimum
            or (maximum is not None and count > maximum)):
        raise TypeError("derived() callable does not accept %d inputs" % count)


def bind(key):
    binding = DirectBinding(key)
    binding._source = _capture_binding(binding, names=("bind",))
    return binding


def derived(function, *inputs):
    binding = DerivedBinding(function, inputs)
    binding.expression_source_contract = BINDING_EXPRESSION_CONTRACT
    binding._source = _capture_binding(binding, names=("derived",))
    return binding


def resolve(value, store):
    if isinstance(value, Binding):
        value = value.resolve(store)
    elif callable(value):
        raise TypeError(
            "State callables must use derived(function, bind(...), ...) instead")
    if isinstance(value, (ThemeColor, ThemeRole)):
        return value
    if isinstance(value, Enum):
        return value.value
    return value


def resolve_deep(value, store):
    value = resolve(value, store)
    if isinstance(value, Enum):
        try:
            return serialize_key(value)
        except TypeError:
            return value.value
    if isinstance(value, tuple):
        return tuple(resolve_deep(item, store) for item in value)
    if isinstance(value, list):
        return [resolve_deep(item, store) for item in value]
    if isinstance(value, dict):
        return dict((key, resolve_deep(item, store))
                    for key, item in value.items())
    return value


def _binding_resolution(binding, store):
    try:
        return {"resolved": _json_value(binding.resolve(store))}
    except Exception as error:
        return {"resolved": None, "error": str(error)}


def _callable_metadata(function):
    result = {
        "name": getattr(function, "__name__", "<callable>"),
        "qualname": getattr(function, "__qualname__", None),
        "module": getattr(function, "__module__", None),
    }
    code = getattr(function, "__code__", None)
    if code is not None:
        result["location"] = {
            "file": inspect.getsourcefile(function),
            "line": int(code.co_firstlineno),
        }
    return result


def _binding_source_metadata(binding):
    trace = getattr(binding, "_source", None)
    if not isinstance(trace, dict):
        return None
    anchor = trace.get("anchor")
    if not isinstance(anchor, dict):
        return None
    return {
        "file": anchor.get("file"),
        "relative_file": anchor.get("relative_file"),
        "range": anchor.get("range"),
        "expression": anchor.get("expression"),
        "kind": anchor.get("kind"),
        "callable": anchor.get("callable"),
        "fingerprint": anchor.get("fingerprint"),
        "context_fingerprint": anchor.get("context_fingerprint"),
        "file_fingerprint": anchor.get("file_fingerprint"),
        "call_chain": list(trace.get("chain", ())),
    }


def binding_metadata(binding, store):
    """Return resolved, recursive Designer metadata for one binding.

    The compact :meth:`Binding.as_dict` representation remains suitable for
    persistent/runtime contracts. This richer form is produced only during
    reflection, so normal printer rendering does not retain dependency trees.
    """
    if not isinstance(binding, Binding):
        raise TypeError("binding_metadata requires a Binding")
    keys = [serialize_key(key) for key in binding.keys]
    if isinstance(binding, DirectBinding):
        result = {
            "kind": "direct",
            "key": serialize_key(binding.key),
            "keys": keys,
            "direct_keys": keys,
            "transitive_keys": [],
            "source": _binding_source_metadata(binding),
        }
        result.update(_binding_resolution(binding, store))
        return result
    if isinstance(binding, DerivedBinding):
        inputs = [binding_metadata(item, store) for item in binding.inputs]
        direct = []
        transitive = []
        for item in inputs:
            target = direct if item.get("kind") == "direct" else transitive
            for key in item.get("keys", ()):
                if key not in target:
                    target.append(key)
        result = {
            "kind": "derived",
            "keys": keys,
            "direct_keys": direct,
            "transitive_keys": [key for key in transitive if key not in direct],
            "inputs": inputs,
            "callable": _callable_metadata(binding.function),
            "source": _binding_source_metadata(binding),
            "expression_source_contract": (
                None if binding.expression_source_contract is None
                else binding.expression_source_contract.as_dict()),
        }
        result.update(_binding_resolution(binding, store))
        return result
    result = binding.as_dict()
    result["keys"] = keys
    result.update(_binding_resolution(binding, store))
    return result


def binding_keys(value):
    result = []
    seen = set()

    def visit(item):
        if isinstance(item, Binding):
            for key in item.keys:
                if key not in seen:
                    result.append(key)
                    seen.add(key)
        elif isinstance(item, (tuple, list)):
            for child in item:
                visit(child)
        elif isinstance(item, dict):
            for child in item.values():
                visit(child)

    visit(value)
    return tuple(result)


def page_state_keys(root, explicit=()):
    result = list(_unique_keys(explicit))
    seen = set(result)
    for node in root.walk():
        for name, value in node.__dict__.items():
            if name.startswith("_") or name in ("parent", "layout_options"):
                continue
            for key in binding_keys(value):
                if key not in seen:
                    result.append(key)
                    seen.add(key)
    return tuple(result)
