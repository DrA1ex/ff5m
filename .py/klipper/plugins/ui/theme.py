## Typed color tokens and one-shot theme resolution for Feather UI.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from enum import Enum
from types import MappingProxyType
import re


_HEX_COLOR = re.compile(r"^[0-9a-fA-F]{6}$")


class ThemeColor(str, Enum):
    """Required physical colors that define a theme's base palette."""

    BACKGROUND = "background"
    PANEL = "panel"
    PRIMARY = "primary"
    PRIMARY_DARK = "primary_dark"
    SECONDARY = "secondary"
    SECONDARY_DARK = "secondary_dark"
    WARNING = "warning"
    DANGER = "danger"
    DANGER_BACKGROUND = "danger_background"
    TEXT = "text"
    BRIGHT = "bright"
    DIM = "dim"
    BORDER = "border"
    MUTED = "muted"
    SUCCESS = "success"
    PRESSED_BACKGROUND = "pressed_background"
    OVERLAY = "overlay"


class ThemeRole(str, Enum):
    """Optional context-specific colors resolved from a theme once."""

    BUTTON_BACKGROUND = "button_background"
    BUTTON_BORDER = "button_border"
    BUTTON_TEXT = "button_text"
    BUTTON_SELECTED_BACKGROUND = "button_selected_background"
    BUTTON_SELECTED_BORDER = "button_selected_border"
    BUTTON_SELECTED_TEXT = "button_selected_text"
    ACCENT_BACKGROUND = "accent_background"
    ACCENT_BORDER = "accent_border"
    ACCENT_TEXT = "accent_text"
    HEADER_BACKGROUND = "header_background"
    HEADER_TEXT = "header_text"
    HEADER_BORDER = "header_border"
    TEMPERATURE_NOZZLE = "temperature_nozzle"
    TEMPERATURE_BED = "temperature_bed"
    TEMPERATURE_FAN = "temperature_fan"


# These defaults deliberately express the least surprising visual contract.
# Product-specific elements use the ordinary accent unless a theme explicitly
# chooses to distinguish them. Component roles inherit their nearest base token.
DEFAULT_THEME_ROLES = MappingProxyType({
    ThemeRole.BUTTON_BACKGROUND: ThemeColor.PANEL,
    ThemeRole.BUTTON_BORDER: ThemeColor.PRIMARY,
    ThemeRole.BUTTON_TEXT: ThemeColor.PRIMARY,
    ThemeRole.BUTTON_SELECTED_BACKGROUND: ThemeColor.PANEL,
    ThemeRole.BUTTON_SELECTED_BORDER: ThemeColor.SECONDARY,
    ThemeRole.BUTTON_SELECTED_TEXT: ThemeColor.SECONDARY,
    ThemeRole.ACCENT_BACKGROUND: ThemeColor.PRIMARY_DARK,
    ThemeRole.ACCENT_BORDER: ThemeColor.PRIMARY,
    ThemeRole.ACCENT_TEXT: ThemeColor.BRIGHT,
    ThemeRole.HEADER_BACKGROUND: ThemeColor.PANEL,
    ThemeRole.HEADER_TEXT: ThemeColor.PRIMARY,
    ThemeRole.HEADER_BORDER: ThemeColor.BORDER,
    ThemeRole.TEMPERATURE_NOZZLE: ThemeColor.PRIMARY,
    ThemeRole.TEMPERATURE_BED: ThemeColor.PRIMARY,
    ThemeRole.TEMPERATURE_FAN: ThemeColor.PRIMARY,
})


def normalize_theme_token(value, nullable=False):
    """Validate a typed theme token or normalize a custom HEX color.

    Named palette values are intentionally not accepted as strings. Runtime
    declarations must use ``ThemeColor`` or ``ThemeRole`` so token references
    remain explicit and statically searchable. A six-digit HEX string remains
    available for intentionally custom colors outside the active theme.
    """
    if value is None:
        if nullable:
            return None
        raise TypeError("theme color must not be None")
    if isinstance(value, (ThemeColor, ThemeRole)):
        return value
    if not isinstance(value, str):
        raise TypeError(
            "theme color must be ThemeColor, ThemeRole or HEX string, got %r" %
            (value,))
    normalized = value.strip().lower().lstrip("#")
    if _HEX_COLOR.fullmatch(normalized) is not None:
        return normalized
    raise ValueError(
        "named theme colors must use ThemeColor or ThemeRole; "
        "custom colors must be six-digit hexadecimal strings")


class ResolvedTheme:
    """Immutable physical palette indexed only by typed theme tokens."""

    __slots__ = ("_values",)

    def __init__(self, values):
        self._values = MappingProxyType(dict(values))

    def resolve(self, token):
        token = normalize_theme_token(token)
        if isinstance(token, (ThemeColor, ThemeRole)):
            return self._values[token]
        return token

    def as_dict(self):
        return dict((token.value, value) for token, value in self._values.items())

    def __eq__(self, other):
        return isinstance(other, ResolvedTheme) and self._values == other._values

    def __repr__(self):
        return "ResolvedTheme(%r)" % self.as_dict()


def _normalize_hex(value, label):
    normalized = str(value).strip().lower()
    if _HEX_COLOR.fullmatch(normalized) is None:
        raise ValueError("%s must be a six-digit hexadecimal color" % label)
    return normalized


def resolve_theme(colors, roles=None):
    """Build a complete immutable palette from one validated theme document.

    Role values may reference a base ``ThemeColor`` by name or provide their
    own physical HEX value. Role-to-role references are intentionally rejected.
    """
    colors = dict(colors or {})
    roles = dict(roles or {})
    expected_colors = set(item.value for item in ThemeColor)
    actual_colors = set(colors)
    missing = sorted(expected_colors - actual_colors)
    unknown = sorted(actual_colors - expected_colors)
    if missing:
        raise ValueError("missing theme colors: %s" % ", ".join(missing))
    if unknown:
        raise ValueError("unknown theme colors: %s" % ", ".join(unknown))

    expected_roles = set(item.value for item in ThemeRole)
    unknown_roles = sorted(set(roles) - expected_roles)
    if unknown_roles:
        raise ValueError("unknown theme roles: %s" % ", ".join(unknown_roles))

    resolved = {}
    for token in ThemeColor:
        resolved[token] = _normalize_hex(colors[token.value], token.value)

    color_names = dict((item.value, item) for item in ThemeColor)
    for role in ThemeRole:
        value = roles.get(role.value, DEFAULT_THEME_ROLES[role].value)
        reference = color_names.get(str(value).strip().lower())
        if reference is not None:
            resolved[role] = resolved[reference]
        else:
            resolved[role] = _normalize_hex(value, role.value)

    return ResolvedTheme(resolved)
