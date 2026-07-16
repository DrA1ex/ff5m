## Lightweight helpers for the Feather mod-parameter editor.
##
## The helpers operate on objects already loaded by mod_params.py.  They do not
## parse the declaration again or retain a second copy of the parameter list.

import enum
import math
import re


VISIBLE_ROWS = 5
MAX_VALUE_LENGTH = 64

SYMBOL_KEYS = (
    ("minus", "-"), ("under", "_"), ("plus", "+"), ("at", "@"),
    ("hash", "#"), ("dollar", "$"), ("percent", "%"), ("amp", "&"),
    ("star", "*"), ("bang", "!"), ("dot", "."), ("comma", ","),
    ("question", "?"), ("slash", "/"), ("colon", ":"), ("semi", ";"),
    ("lparen", "("), ("rparen", ")"), ("quote", '"'), ("bslash", "\\"),
)
SYMBOL_MAP = dict(SYMBOL_KEYS)


def visible_parameters(manager):
    return [param for param in manager.params
            if not getattr(param, "hidden", False)]


def parameter_kind(param):
    param_type = param.type
    if param_type is bool:
        return "bool"
    if isinstance(param_type, type) and issubclass(param_type, enum.Enum):
        return "enum"
    if param_type is int:
        return "int"
    if param_type is float:
        return "float"
    return "str"


def enum_names(param):
    return [member.name for member in param.type]


def current_edit_value(manager, param):
    value = manager.variables.get(param.key, param.default)
    if parameter_kind(param) == "enum":
        return param.type(value).name
    if parameter_kind(param) == "bool":
        return "1" if value else "0"
    return str(value)


def display_value(manager, param):
    kind = parameter_kind(param)
    value = manager.variables.get(param.key, param.default)
    if kind == "bool":
        return "ON" if value else "OFF"
    if kind == "enum":
        return param.type(value).name
    if kind == "float":
        return "%g" % value
    return str(value) if str(value) else "<EMPTY>"


def bool_labels(param):
    """Return the user-facing labels for false and true switch positions."""
    options = getattr(param, "options", None)
    if isinstance(options, (list, tuple)) and len(options) >= 2:
        return str(options[0]).upper(), str(options[1]).upper()
    return "OFF", "ON"


def description(param):
    value = getattr(param, "description", None)
    if value:
        return str(value)
    warning = getattr(param, "warning", None)
    if warning:
        return str(warning).splitlines()[0].lstrip("! ")
    return "Configure %s." % str(param.label).lower().rstrip(".")


def option_description(param, name):
    options = getattr(param, "options", None)
    if isinstance(options, dict):
        return str(options.get(name, ""))
    return ""


def validate_value(param, text):
    text = str(text)
    if len(text) > MAX_VALUE_LENGTH:
        raise ValueError("Value is too long")
    kind = parameter_kind(param)
    if kind == "int":
        if re.match(r"^-?\d+$", text) is None:
            raise ValueError("Enter a whole number")
        return int(text)
    if kind == "float":
        if re.match(r"^-?(?:\d+(?:\.\d*)?|\.\d+)$", text) is None:
            raise ValueError("Enter a decimal number")
        value = float(text)
        if not math.isfinite(value):
            raise ValueError("Enter a finite number")
        return value
    if kind == "str":
        if any(ord(char) < 32 or ord(char) > 126 for char in text):
            raise ValueError("Only printable ASCII is supported")
        return text
    if kind == "bool":
        return bool(int(text))
    if text not in enum_names(param):
        raise ValueError("Unknown option")
    return text


def key_character(token, shift=False):
    if len(token) == 1 and token.isalnum():
        return token.upper() if shift and token.isalpha() else token
    return SYMBOL_MAP.get(token)
