## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import re

from ui.bindings import state
from ui.identity import StateKey


class ScrewResultState(StateKey):
    __key_namespace__ = "ui.pages.calibration.screws.state.ScrewResultState"
    # Editable examples; render() replaces every field with real results.
    REAR_LEFT_NAME = state(str, default="REAR LEFT", category="rear_left")
    REAR_LEFT_DIRECTION = state(str, default="BASE", choices=("", "BASE", "CW", "CCW"),
                               category="rear_left")
    REAR_LEFT_MINUTES = state(int, default=0, minimum=0, unit="turn minutes", category="rear_left")

    REAR_RIGHT_NAME = state(str, default="REAR RIGHT", category="rear_right")
    REAR_RIGHT_DIRECTION = state(str, default="CCW", choices=("", "BASE", "CW", "CCW"),
                               category="rear_right")
    REAR_RIGHT_MINUTES = state(int, default=4, minimum=0, unit="turn minutes", category="rear_right")

    FRONT_LEFT_NAME = state(str, default="FRONT LEFT", category="front_left")
    FRONT_LEFT_DIRECTION = state(str, default="CW", choices=("", "BASE", "CW", "CCW"),
                               category="front_left")
    FRONT_LEFT_MINUTES = state(int, default=5, minimum=0, unit="turn minutes", category="front_left")

    FRONT_RIGHT_NAME = state(str, default="FRONT RIGHT", category="front_right")
    FRONT_RIGHT_DIRECTION = state(str, default="CCW", choices=("", "BASE", "CW", "CCW"),
                               category="front_right")
    FRONT_RIGHT_MINUTES = state(int, default=15, minimum=0, unit="turn minutes", category="front_right")


_CORNER_ORDER = ("front_left", "front_right", "rear_right", "rear_left")
_CORNER_FIELDS = {
    "rear_left": (ScrewResultState.REAR_LEFT_NAME,
                    ScrewResultState.REAR_LEFT_DIRECTION,
                    ScrewResultState.REAR_LEFT_MINUTES),
    "rear_right": (ScrewResultState.REAR_RIGHT_NAME,
                    ScrewResultState.REAR_RIGHT_DIRECTION,
                    ScrewResultState.REAR_RIGHT_MINUTES),
    "front_left": (ScrewResultState.FRONT_LEFT_NAME,
                    ScrewResultState.FRONT_LEFT_DIRECTION,
                    ScrewResultState.FRONT_LEFT_MINUTES),
    "front_right": (ScrewResultState.FRONT_RIGHT_NAME,
                    ScrewResultState.FRONT_RIGHT_DIRECTION,
                    ScrewResultState.FRONT_RIGHT_MINUTES),
}


def _corner(name):
    words = set(re.findall(r"[a-z]+", str(name).lower()))
    row = "front" if "front" in words else "rear" if words & {"rear", "back"} else None
    column = "left" if "left" in words else "right" if "right" in words else None
    return "%s_%s" % (row, column) if row and column else None


def result_values(results, reference_name=None):
    by_corner = {}
    unplaced = []
    for result in results:
        result = dict(result)
        corner = _corner(result.get("name", ""))
        if corner and corner not in by_corner:
            by_corner[corner] = result
        else:
            unplaced.append(result)

    for corner in _CORNER_ORDER:
        if corner not in by_corner and unplaced:
            by_corner[corner] = unplaced.pop(0)

    reference_corner = _corner(reference_name)
    if reference_corner in by_corner:
        for corner, result in by_corner.items():
            if corner == reference_corner:
                result["direction"] = "BASE"
                result["turns"] = "-"
            elif str(result.get("direction", "")).upper() == "BASE":
                result["direction"] = ""
                result["turns"] = ""
    values = {}
    for corner, (name_key, direction_key, minutes_key) in _CORNER_FIELDS.items():
        result = by_corner.get(corner, {})
        name = re.sub(r"\s+screw\s*$", "", str(result.get("name", "")), flags=re.I).upper()
        direction = str(result.get("direction", "")).upper()
        minutes = 0
        if direction in ("CW", "CCW"):
            match = re.fullmatch(r"(\d+):([0-5]\d)", str(result.get("turns", "")))
            if match:
                minutes = int(match.group(1)) * 60 + int(match.group(2))
            else:
                direction = ""
        elif direction != "BASE":
            direction = ""
        values.update({name_key: name, direction_key: direction, minutes_key: minutes})
    return values


def adjustment(direction, minutes):
    return ("%s %02d:%02d" % (direction, minutes // 60, minutes % 60)
            if direction in ("CW", "CCW") else "")
