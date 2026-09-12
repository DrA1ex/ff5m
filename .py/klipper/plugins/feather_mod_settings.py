## Lightweight helpers for the Feather mod-parameter editor.
##
## The helpers operate on objects already loaded by mod_params.py.  They do not
## parse the declaration again or retain a second copy of the parameter list.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import enum

from collections import namedtuple

from ui import CONTENT_BOTTOM, NumericInputSpec


MAX_VALUE_LENGTH = 64
RESTART_EFFECTS = frozenset(("klipper", "printer"))

## Geometry of the parameter list.  The list is paginated by pixels instead of a
## fixed row count because a category band shares the page with its rows.  The
## page renderer draws with the same constants, so a change here moves both the
## layout and the page boundaries together.
LIST_X = 25
LIST_WIDTH = 690
LIST_TOP = 76
LIST_BOTTOM = CONTENT_BOTTOM
BAND_HEIGHT = 24
BAND_GAP_BELOW = 2
BAND_PITCH = 32
ITEM_HEIGHT = 64
ITEM_PITCH = 66


def band_pitch(position):
    """Vertical slot taken by the category band at that place on a page.

    A band that opens a page needs only its own air, while a later band is
    padded to a whole row pitch.  That keeps every row on the same grid, so a
    page with several categories ends where a plain page of rows ends instead
    of leaving a half-row gap at the bottom.
    """
    return BAND_PITCH if position == 0 else ITEM_PITCH


def visible_parameters(manager):
    return [param for param in manager.params
            if (not getattr(param, "hidden", False)
                and parameter_is_visible(manager, param))]


def parameter_is_visible(manager, param):
    condition = getattr(param, "ui_visible_if", None)
    if not condition:
        return True
    if condition.get("operator") != "equals":
        return True
    parent_key = condition.get("parameter")
    parent = getattr(manager, "params_map", {}).get(parent_key)
    if parent is None:
        parent = next(
            (candidate for candidate in manager.params
             if candidate.key == parent_key), None)
    if parent is None:
        return True
    current = manager.variables.get(parent.key, parent.default)
    expected = condition.get("value")
    if parameter_kind(parent) == "enum":
        try:
            current = parent.type(current).name
        except (TypeError, ValueError):
            return False
    return current == expected


def parameter_category(manager, param):
    category_id = getattr(param, "ui_category", None)
    category = getattr(manager, "ui_categories_map", {}).get(category_id)
    return getattr(category, "label", None) or "OTHER"


# One category band together with the rows drawn under it on a single page.
# "first" and "total" describe the position of those rows inside the whole
# category, and "continued" marks a category that already had rows on an
# earlier page.
CategorySection = namedtuple(
    "CategorySection", ("label", "continued", "first", "total", "items"))


def _category_runs(manager, parameters):
    """Group the visible parameters into consecutive runs of one category."""
    runs = []
    for index, param in enumerate(parameters):
        label = parameter_category(manager, param)
        if not runs or runs[-1][0] != label:
            runs.append((label, []))
        runs[-1][1].append((index, param))
    return runs


def category_pages(manager, parameters):
    """Split the parameter list into pages of category sections.

    A band is placed only when at least one of its rows fits below it, so a
    page never ends with a category heading that has nothing under it.  A
    category too long for the remaining space continues on the next page and
    repeats its band there.
    """
    capacity = LIST_BOTTOM - LIST_TOP
    pages = [[]]
    used = 0
    for label, entries in _category_runs(manager, parameters):
        shown = 0
        while shown < len(entries):
            pitch = band_pitch(len(pages[-1]))
            if used + pitch + ITEM_PITCH > capacity:
                pages.append([])
                used = 0
                pitch = band_pitch(0)
            rows = (capacity - used - pitch) // ITEM_PITCH
            chunk = tuple(entries[shown:shown + rows])
            pages[-1].append(CategorySection(
                label, shown > 0, shown + 1, len(entries), chunk))
            used += pitch + len(chunk) * ITEM_PITCH
            shown += len(chunk)
    return pages


def page_height(sections):
    """Pixels taken by one page of sections, bands and rows together."""
    return sum(band_pitch(position) + len(section.items) * ITEM_PITCH
               for position, section in enumerate(sections))


def next_category_hint(pages, page):
    """Return the category the next page opens with, when there is room to say.

    A postponed category leaves a hole at least as tall as a row, so the page
    names what follows instead of ending in blank space.
    """
    if page + 1 >= len(pages):
        return None
    if LIST_BOTTOM - LIST_TOP - page_height(pages[page]) < ITEM_PITCH:
        return None
    return pages[page + 1][0].label


def page_parameters(sections):
    """Flatten one page back into its (index, parameter) pairs."""
    return [entry for section in sections for entry in section.items]


def page_of_parameter(pages, key):
    """Return the page showing key, or None when the key is not visible."""
    for number, sections in enumerate(pages):
        if any(param.key == key for _, param in page_parameters(sections)):
            return number
    return None


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


def restart_effect(param):
    effect = getattr(param, "restart", None)
    return effect if effect in RESTART_EFFECTS else None


def numeric_input_spec(param):
    kind = parameter_kind(param)
    if kind not in ("int", "float"):
        raise TypeError("Parameter is not numeric")
    return NumericInputSpec(
        "integer" if kind == "int" else "decimal",
        minimum=getattr(param, "minimum", None),
        maximum=getattr(param, "maximum", None),
        max_length=MAX_VALUE_LENGTH,
        fraction_digits=(0 if kind == "int" else
                         getattr(param, "fraction_digits", None)))


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


def bool_display_active(param, raw_value):
    """Map a raw boolean to its visual toggle state without changing data."""
    if parameter_kind(param) != "bool":
        raise TypeError("Parameter is not boolean")
    return bool(raw_value) != bool(getattr(param, "ui_inverted", False))


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
    if kind in ("int", "float"):
        return numeric_input_spec(param).parse(text)
    if kind == "str":
        if any(ord(char) < 32 or ord(char) > 126 for char in text):
            raise ValueError("Only printable ASCII is supported")
        return text
    if kind == "bool":
        return bool(int(text))
    if text not in enum_names(param):
        raise ValueError("Unknown option")
    return text
