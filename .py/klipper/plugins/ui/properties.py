"""Typed, Designer-neutral component and layout property contracts (v2)."""

from enum import Enum
import re


class Invalidation(str, Enum):
    PAINT = "paint"
    LAYOUT = "layout"
    STRUCTURE = "structure"


class RewritePolicy(str, Enum):
    """How a literal property may be represented in product source."""

    LITERAL = "literal"
    DIRECT_BINDING = "direct_binding"
    LITERAL_OR_BINDING = "literal_or_binding"
    LOCKED = "locked"


_MISSING = object()


class EditorSpec:
    """Portable editor presentation metadata.

    ``catalog`` names a reflected project catalog (``fonts``, ``colors``,
    ``actions`` or ``bindings``).  This keeps choices out of the Designer and
    lets applications publish their own values without changing its code.
    """

    __slots__ = (
        "kind", "label", "group", "choices", "catalog", "placeholder",
        "step", "multiline", "order", "metadata",
    )

    def __init__(self, kind="auto", label=None, group="Component", choices=(),
                 catalog=None, placeholder=None, step=None, multiline=False,
                 order=0, **metadata):
        self.kind = str(kind)
        self.label = None if label is None else str(label)
        self.group = str(group)
        self.choices = tuple(choices or ())
        self.catalog = None if catalog is None else str(catalog)
        self.placeholder = None if placeholder is None else str(placeholder)
        self.step = step
        self.multiline = bool(multiline)
        self.order = int(order)
        self.metadata = dict(metadata)

    def as_dict(self):
        value = {
            "kind": self.kind,
            "label": self.label,
            "group": self.group,
            "choices": list(self.choices),
            # ``options`` remains a wire alias, not a second source of truth.
            "options": list(self.choices),
            "catalog": self.catalog,
            "placeholder": self.placeholder,
            "step": self.step,
            "multiline": self.multiline,
            "order": self.order,
        }
        value.update(self.metadata)
        return value


class ValidationSpec:
    """Serializable constraints plus an optional framework-side validator."""

    __slots__ = (
        "minimum", "maximum", "choices", "pattern", "maximum_length",
        "maximum_items", "validator",
    )

    def __init__(self, minimum=None, maximum=None, choices=(), pattern=None,
                 maximum_length=None, maximum_items=None, validator=None):
        self.minimum = minimum
        self.maximum = maximum
        self.choices = tuple(choices or ())
        self.pattern = None if pattern is None else str(pattern)
        self.maximum_length = (None if maximum_length is None
                               else int(maximum_length))
        self.maximum_items = (None if maximum_items is None
                              else int(maximum_items))
        self.validator = validator

    def validate(self, value, name="value"):
        if self.minimum is not None and value < self.minimum:
            raise ValueError("%s must be at least %s" % (name, self.minimum))
        if self.maximum is not None and value > self.maximum:
            raise ValueError("%s must be at most %s" % (name, self.maximum))
        if self.choices and value not in self.choices:
            raise ValueError("%s must be one of %s" % (name, self.choices))
        if self.pattern is not None and not re.match(self.pattern, value):
            raise ValueError("%s has an invalid format" % name)
        if self.maximum_length is not None and len(value) > self.maximum_length:
            raise ValueError("%s is too long" % name)
        if self.maximum_items is not None and len(value) > self.maximum_items:
            raise ValueError(
                "%s supports at most %d items" % (name, self.maximum_items))
        if self.validator is not None:
            result = self.validator(value)
            if result is False:
                raise ValueError("%s is invalid" % name)
            if result is not None and result is not True:
                value = result
        return value

    def as_dict(self):
        return {
            "minimum": self.minimum,
            "maximum": self.maximum,
            "choices": list(self.choices),
            "pattern": self.pattern,
            "maximum_length": self.maximum_length,
            "maximum_items": self.maximum_items,
        }


class SourceSpec:
    """Source rewrite and runtime storage policy for a property."""

    __slots__ = (
        "name", "position", "index", "storage", "runtime_name", "runtime_index",
        "policy", "reason",
    )

    def __init__(self, name=None, position=None, index=None, storage="attribute",
                 runtime_name=None, runtime_index=None,
                 policy=RewritePolicy.LITERAL_OR_BINDING, reason=None):
        self.name = None if name is None else str(name)
        self.position = None if position is None else int(position)
        self.index = None if index is None else int(index)
        self.storage = str(storage)
        self.runtime_name = None if runtime_name is None else str(runtime_name)
        self.runtime_index = (None if runtime_index is None
                              else int(runtime_index))
        self.policy = RewritePolicy(policy)
        self.reason = None if reason is None else str(reason)

    def as_dict(self, property_name=None):
        return {
            "name": self.name or property_name,
            "position": self.position,
            "index": self.index,
            "storage": self.storage,
            "runtime_name": self.runtime_name or property_name,
            "runtime_index": self.runtime_index,
            "policy": self.policy.value,
            "rewrite": self.policy != RewritePolicy.LOCKED,
            "reason": self.reason,
        }


_TYPE_NAMES = {
    str: "string", int: "integer", float: "number", bool: "boolean",
    list: "list", tuple: "list", dict: "object", object: "any",
}


def _type_name(runtime_type):
    if isinstance(runtime_type, str):
        return runtime_type
    if isinstance(runtime_type, tuple):
        return " | ".join(_type_name(value) for value in runtime_type)
    return _TYPE_NAMES.get(runtime_type, getattr(runtime_type, "__name__", "any"))


class PropertySpec:
    """Complete v2 contract for one public component property."""

    __slots__ = (
        "name", "runtime_type", "default", "nullable", "validation",
        "editor", "bindings", "invalidation", "live", "source",
    )

    def __init__(self, name, runtime_type=object, default=_MISSING,
                 nullable=False, validation=None, editor=None, bindings=("direct",),
                 invalidation=Invalidation.PAINT, live=True, source=None):
        self.name = str(name)
        self.runtime_type = runtime_type
        self.default = default
        self.nullable = bool(nullable)
        self.validation = validation or ValidationSpec()
        if not isinstance(self.validation, ValidationSpec):
            self.validation = ValidationSpec(**dict(self.validation))
        if editor is None:
            editor = EditorSpec()
        elif not isinstance(editor, EditorSpec):
            editor = EditorSpec(**dict(editor))
        self.editor = editor
        if bindings is True:
            bindings = ("direct",)
        elif bindings is False or bindings is None:
            bindings = ()
        self.bindings = tuple(str(value) for value in bindings)
        self.invalidation = Invalidation(invalidation)
        self.live = bool(live)
        if source is None:
            source = SourceSpec(name=self.name)
        elif not isinstance(source, SourceSpec):
            source = SourceSpec(**dict(source))
        self.source = source

    @property
    def has_default(self):
        return self.default is not _MISSING

    def value_from(self, node):
        source_name = self.source.runtime_name or self.name
        if self.source.storage == "kwargs":
            value = getattr(node, "kwargs", {}).get(
                source_name, self.default if self.has_default else None)
        else:
            value = getattr(
                node, source_name, self.default if self.has_default else None)
        if self.source.runtime_index is not None:
            try:
                value = value[self.source.runtime_index]
            except (IndexError, TypeError):
                value = self.default if self.has_default else None
        return value

    def set_on(self, node, value):
        source_name = self.source.runtime_name or self.name
        if self.source.storage == "kwargs":
            values = getattr(node, "kwargs")
            if value is None and self.nullable:
                values.pop(source_name, None)
            else:
                values[source_name] = value
            return
        if self.source.runtime_index is not None:
            values = list(getattr(node, source_name))
            values[self.source.runtime_index] = value
            setattr(node, source_name, tuple(values))
            return
        setattr(node, source_name, value)

    def validate(self, value):
        if value is None:
            if self.nullable:
                return None
            raise ValueError("%s may not be null" % self.name)
        runtime_type = self.runtime_type
        if runtime_type is not object and not isinstance(runtime_type, str):
            if runtime_type is int and isinstance(value, bool):
                raise ValueError("%s must be an integer" % self.name)
            if not isinstance(value, runtime_type):
                raise ValueError(
                    "%s must be %s" % (self.name, _type_name(runtime_type)))
        return self.validation.validate(value, self.name)

    @property
    def source_name(self):
        return self.source.name or self.name

    @property
    def source_index(self):
        return self.source.index

    @property
    def rewrite(self):
        return self.source.policy != RewritePolicy.LOCKED

    def as_dict(self):
        source = self.source.as_dict(self.name)
        editor = self.editor.as_dict()
        validation = self.validation.as_dict()
        result = {
            "name": self.name,
            "runtime_type": _type_name(self.runtime_type),
            "default": None if not self.has_default else self.default,
            "has_default": self.has_default,
            "nullable": self.nullable,
            "validation": validation,
            "editor": editor,
            "bindings": list(self.bindings),
            "binding": bool(self.bindings),
            "invalidation": self.invalidation.value,
            "live_preview": self.live,
            "live": self.live,
            "source": source,
            # Flat aliases keep the wire DTO convenient while metadata still
            # has one framework-owned definition.
            "kind": editor["kind"],
            "label": editor["label"],
            "group": editor["group"],
            "options": editor["options"],
            "minimum": validation["minimum"],
            "maximum": validation["maximum"],
            "maximum_items": validation["maximum_items"],
            "rewrite": source["rewrite"],
            "source_name": source["name"],
            "source_index": source["index"],
        }
        result.update(self.editor.metadata)
        return result


class CreationFieldSpec(PropertySpec):
    """A typed constructor input published to generic component palettes."""

    __slots__ = ("required", "argument")

    def __init__(self, name, runtime_type=object, required=False,
                 argument=None, **kwargs):
        super().__init__(name, runtime_type, **kwargs)
        self.required = bool(required)
        self.argument = str(argument or name)

    def as_dict(self):
        value = super().as_dict()
        value.update({
            "required": self.required,
            "argument": self.argument,
        })
        return value


def property_schema(*specs):
    if not all(isinstance(value, PropertySpec) for value in specs):
        raise TypeError("property_schema v2 accepts only PropertySpec values")
    names = [value.name for value in specs]
    if len(names) != len(set(names)):
        raise ValueError("Property names must be unique")
    return tuple(specs)


def property_names(node_or_type):
    schema = getattr(node_or_type, "property_schema", ())
    return tuple(item.name for item in schema)
