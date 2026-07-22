## Typed semantic actions and portable router behavior.

import hashlib
import json
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum

from .bindings import StateStore, state_spec
from .identity import CommandKey, PageKey, StateKey, serialize_key


def _json_value(value):
    if isinstance(value, (PageKey, StateKey, CommandKey)):
        return serialize_key(value)
    if isinstance(value, Enum):
        try:
            return serialize_key(value)
        except TypeError:
            return value.value
    if is_dataclass(value):
        return {
            field.name: _json_value(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(_json_value(key)): _json_value(item)
            for key, item in value.items()
        }
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError("Action value is not portable: %r" % (value,))



def _validate_payload(value):
    if value is None or isinstance(value, (bool, int, float, str, Enum)):
        return
    if isinstance(value, tuple):
        for item in value:
            _validate_payload(item)
        return
    if is_dataclass(value):
        parameters = getattr(value.__class__, "__dataclass_params__", None)
        if parameters is None or not parameters.frozen:
            raise TypeError("Command payload dataclasses must be frozen")
        for field in fields(value):
            _validate_payload(getattr(value, field.name))
        return
    raise TypeError(
        "Command payload must be an immutable typed value, got %s" %
        type(value).__name__)

def _fingerprint(value):
    encoded = json.dumps(
        _json_value(value), sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode("ascii")
    return hashlib.sha1(encoded).hexdigest()[:12]


class Action:
    """Base class for framework-visible UI effects."""

    kind = None

    @property
    def wire_id(self):
        return "ui.%s" % _fingerprint(self.as_dict())

    def as_dict(self):
        raise NotImplementedError


@dataclass(frozen=True)
class Navigate(Action):
    target: PageKey
    kind = "navigate"

    def __post_init__(self):
        if not isinstance(self.target, PageKey):
            raise TypeError("Navigate target must be a PageKey member")

    @property
    def wire_id(self):
        return "navigate.%s" % str(self.target.value)

    def as_dict(self):
        return {"kind": self.kind, "target": serialize_key(self.target)}


@dataclass(frozen=True)
class Back(Action):
    kind = "back"

    @property
    def wire_id(self):
        return "nav.back"

    def as_dict(self):
        return {"kind": self.kind}


@dataclass(frozen=True)
class Replace(Action):
    target: PageKey
    kind = "replace"

    def __post_init__(self):
        if not isinstance(self.target, PageKey):
            raise TypeError("Replace target must be a PageKey member")

    @property
    def wire_id(self):
        return "replace.%s" % str(self.target.value)

    def as_dict(self):
        return {"kind": self.kind, "target": serialize_key(self.target)}


@dataclass(frozen=True)
class SetValue(Action):
    key: StateKey
    value: object
    kind = "set_value"

    def __post_init__(self):
        if not isinstance(self.key, StateKey):
            raise TypeError("SetValue key must be a StateKey member")
        spec = state_spec(self.key)
        if not spec.mutable:
            raise ValueError("SetValue requires mutable state")
        spec.validate(self.value)

    def as_dict(self):
        return {
            "kind": self.kind,
            "key": serialize_key(self.key),
            "value": _json_value(self.value),
        }


@dataclass(frozen=True)
class Toggle(Action):
    key: StateKey
    kind = "toggle"

    def __post_init__(self):
        if not isinstance(self.key, StateKey):
            raise TypeError("Toggle key must be a StateKey member")
        spec = state_spec(self.key)
        if not spec.mutable:
            raise ValueError("Toggle requires mutable state")
        if spec.value_type is not bool:
            raise TypeError("Toggle requires a boolean StateKey")

    def as_dict(self):
        return {"kind": self.kind, "key": serialize_key(self.key)}


@dataclass(frozen=True)
class Increment(Action):
    key: StateKey
    amount: object = 1
    wrap: bool = False
    kind = "increment"

    def __post_init__(self):
        if not isinstance(self.key, StateKey):
            raise TypeError("Increment key must be a StateKey member")
        spec = state_spec(self.key)
        if not spec.mutable:
            raise ValueError("Increment requires mutable state")
        if spec.choices is not None:
            if not isinstance(self.amount, int) or isinstance(self.amount, bool):
                raise TypeError("Choice Increment amount must be an integer")
        elif spec.value_type not in (int, float):
            raise TypeError("Increment requires numeric state or choices")

    def as_dict(self):
        return {
            "kind": self.kind,
            "key": serialize_key(self.key),
            "amount": _json_value(self.amount),
            "wrap": bool(self.wrap),
        }


class SimulationHint:
    """Portable observable behavior metadata for a semantic command."""

    kind = None

    def as_dict(self):
        result = {"kind": self.kind}
        if is_dataclass(self):
            result.update({
                field.name: _json_value(getattr(self, field.name))
                for field in fields(self)
            })
        return result


@dataclass(frozen=True)
class HomingHint(SimulationHint):
    axes: tuple
    sequence: tuple = ()
    kind = "homing"


@dataclass(frozen=True)
class MovementHint(SimulationHint):
    axis: object
    distance: object = None
    speed: object = None
    requires_homed: bool = True
    kind = "movement"


@dataclass(frozen=True)
class ContinuousMovementHint(SimulationHint):
    axes: tuple
    requires_homed: bool = True
    direction_signs: tuple = ()
    release_duration: object = 0.0
    kind = "continuous_movement"


@dataclass(frozen=True)
class MotorStateHint(SimulationHint):
    enabled: bool
    axes: tuple = ()
    kind = "motor_state"


@dataclass(frozen=True)
class HeatingHint(SimulationHint):
    heater: object
    target: object = None
    kind = "heating"


@dataclass(frozen=True)
class CoolingHint(SimulationHint):
    heater: object = None
    kind = "cooling"


@dataclass(frozen=True)
class ProbingHint(SimulationHint):
    axis: object = None
    kind = "probing"


@dataclass(frozen=True)
class ProgressHint(SimulationHint):
    duration: object = None
    kind = "progress"


@dataclass(frozen=True)
class CompletionHint(SimulationHint):
    result: object = None
    kind = "completion"


@dataclass(frozen=True)
class CancellationHint(SimulationHint):
    target: object = None
    kind = "cancellation"


@dataclass(frozen=True)
class Command(Action):
    key: CommandKey
    payload: object = None
    hint: SimulationHint = None
    kind = "command"

    def __post_init__(self):
        if not isinstance(self.key, CommandKey):
            raise TypeError("Command key must be a CommandKey member")
        _validate_payload(self.payload)
        _json_value(self.payload)
        if self.hint is not None and not isinstance(self.hint, SimulationHint):
            raise TypeError("Command hint must be a SimulationHint")

    @property
    def wire_id(self):
        # The transport identity covers the complete semantic invocation. A
        # project may reuse one CommandKey with several typed payloads without
        # creating collisions or teaching the renderer about domain behavior.
        value = self.key.value
        prefix = str(value) if isinstance(value, str) else "command"
        return "%s.%s" % (prefix, _fingerprint(self.as_dict()))

    def as_dict(self):
        return {
            "kind": self.kind,
            "key": serialize_key(self.key),
            "payload": _json_value(self.payload),
            "hint": None if self.hint is None else self.hint.as_dict(),
        }


def action_wire_id(action):
    if not isinstance(action, Action):
        raise TypeError("Interactive components require a semantic Action")
    return action.wire_id


def action_metadata(action):
    if not isinstance(action, Action):
        return None
    result = action.as_dict()
    result["wire_id"] = action.wire_id
    return result


def collect_actions(root):
    """Collect the semantic actions reachable from one component tree."""
    result = {}

    def add(value):
        if value is None:
            return
        if isinstance(value, Action):
            wire = value.wire_id
            existing = result.get(wire)
            if existing is not None and existing != value:
                raise ValueError("Semantic action wire collision: %s" % wire)
            result[wire] = value
            return
        if isinstance(value, (tuple, list)):
            for item in value:
                if isinstance(item, (tuple, list)) and item:
                    add(item[0])
                else:
                    add(item)

    for node in root.walk():
        add(getattr(node, "action", None))
        add(getattr(node, "active_action", None))
        add(getattr(node, "buttons", None))
    return result


@dataclass(frozen=True)
class DispatchResult:
    page: PageKey
    state: StateStore
    command: Command = None
    history_changed: bool = False


class Router:
    """Generic navigation and state-effect dispatcher for real pages."""

    def __init__(self, pages, current=None):
        pages = tuple(pages)
        if not pages:
            raise ValueError("Router needs at least one page")
        self.pages = {}
        for page in pages:
            key = page.page_key
            if key in self.pages:
                raise ValueError("Duplicate router page: %s" % key)
            self.pages[key] = page
        self.current = current or pages[0].page_key
        if self.current not in self.pages:
            raise KeyError("Unknown initial page: %s" % self.current)
        self.history = []

    def page(self, key=None):
        return self.pages[key or self.current]

    def dispatch(self, action, state):
        if not isinstance(action, Action):
            raise TypeError("Router dispatch requires a semantic Action")
        page = self.current
        store = state if isinstance(state, StateStore) else self.page()._fresh_state(state)
        history_changed = False
        command = None
        if isinstance(action, Navigate):
            if action.target not in self.pages:
                raise KeyError("Unknown navigation target: %s" % action.target)
            self.history.append(self.current)
            self.current = action.target
            page = self.current
            store = self.page().initial_state()
            history_changed = True
        elif isinstance(action, Back):
            if self.history:
                self.current = self.history.pop()
                page = self.current
                store = self.page().initial_state()
                history_changed = True
        elif isinstance(action, Replace):
            if action.target not in self.pages:
                raise KeyError("Unknown replacement target: %s" % action.target)
            self.current = action.target
            page = self.current
            store = self.page().initial_state()
            history_changed = True
        elif isinstance(action, SetValue):
            store = store.copy()
            store.update({action.key: action.value})
        elif isinstance(action, Toggle):
            store = store.copy()
            store.update({action.key: not store[action.key]})
        elif isinstance(action, Increment):
            store = store.copy()
            spec = state_spec(action.key)
            current = store[action.key]
            if spec.choices is not None:
                choices = tuple(spec.choices)
                index = choices.index(current) + int(action.amount)
                if action.wrap:
                    index %= len(choices)
                else:
                    index = max(0, min(len(choices) - 1, index))
                value = choices[index]
            else:
                value = current + action.amount
                if spec.minimum is not None:
                    value = max(spec.minimum, value)
                if spec.maximum is not None:
                    value = min(spec.maximum, value)
            store.update({action.key: value})
        elif isinstance(action, Command):
            command = action
        return DispatchResult(page, store, command, history_changed)
