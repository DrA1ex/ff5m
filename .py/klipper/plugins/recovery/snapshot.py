## Versioned runtime recovery state.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import math, re

from .state import RecoveryGCodeState, RecoveryParseError
from ..object_exclusion import object_name_parameter

VERSION = 2
FAN_NAMES = ("fanM106", "chamber_fan", "internal_fan", "external_fan")
VECTOR_FIELDS = ("last_position", "last_position_extruded",
                 "last_position_excluded")
EXCLUSION_NUMBERS = ("max_position_extruded", "max_position_excluded",
                     "extruder_adj")
VELOCITY_KEYS = {
    "VELOCITY": "max_velocity", "ACCEL": "max_accel",
    "ACCEL_TO_DECEL": "max_accel_to_decel",
    "SQUARE_CORNER_VELOCITY": "square_corner_velocity",
}


def capture(printer, move, toolhead, extruder, print_stats, eventtime):
    """Read host-owned print state without waiting for motion."""
    state = RecoveryGCodeState()
    state.absolute_coordinates = move["absolute_coordinates"]
    state.absolute_extrude = move["absolute_extrude"]
    state.logical_e = move["gcode_position"][3]
    state.feedrate = move["speed"]
    state.speed_factor = move["speed_factor"] * 100.
    state.extrude_factor = move["extrude_factor"] * 100.
    state.base_xyz = list(move["base_position"][:3])
    state.gcode_offsets = list(move["homing_origin"][:3])
    state.velocity_limits = dict(
        VELOCITY=toolhead["max_velocity"], ACCEL=toolhead["max_accel"],
        ACCEL_TO_DECEL=toolhead["max_accel_to_decel"],
        SQUARE_CORNER_VELOCITY=toolhead["square_corner_velocity"])
    state.pressure_advance["extruder"] = dict(
        ADVANCE=extruder["pressure_advance"], SMOOTH_TIME=extruder["smooth_time"])
    # Klipper names additional extruders extruder1, extruder2, ...
    for index in range(1, 99):
        name = "extruder%d" % index
        source = printer.lookup_object(name, None)
        if source is None:
            break
        status = source.get_status(eventtime)
        if "pressure_advance" in status:
            state.pressure_advance[name] = dict(
                ADVANCE=status["pressure_advance"], SMOOTH_TIME=status["smooth_time"])
    fan_macro = printer.lookup_object("gcode_macro SET_FAN_SPEED", None)
    if fan_macro is not None:
        requested = fan_macro.variables["requested_speeds"]
        for name in FAN_NAMES:
            if printer.lookup_object("fan_generic " + name, None) is not None:
                state.fan_speeds[name] = requested[name]
    source = printer.lookup_object("firmware_retraction", None)
    if source is not None:
        status = source.get_status(eventtime)
        state.retraction = {key: status[key.lower()]
                            for key in state.RETRACTION_FIELDS}
        state.retracted = source.is_retracted
        state.has_retraction_state = True
    display = printer.lookup_object("display_status", None)
    if display is not None and display.progress is not None:
        state.progress["P"] = display.progress * 100.
    for key in ("CURRENT_LAYER", "TOTAL_LAYER"):
        value = getattr(print_stats, "info_" + key.lower(), None)
        if value is not None:
            state.print_stats[key] = value
    source = printer.lookup_object("exclude_object", None)
    excluded = []
    if source is not None:
        status = source.get_status(eventtime)
        state.current_object = status["current_object"]
        excluded = sorted(set(status["excluded_objects"]))
        if source.next_transform is not None:
            state.exclusion_transform = {
                **{key: list(getattr(source, key)) for key in VECTOR_FIELDS},
                **{key: getattr(source, key) for key in EXCLUSION_NUMBERS},
                "offsets": {name: list(value)
                            for name, value in source.extrusion_offsets.items()},
                "in_excluded_region": source.in_excluded_region,
                "initial_extrusion_moves": source.initial_extrusion_moves,
            }
    return state, excluded


# Wire format is deliberately independent of the legacy reducer.
def encode(state):
    result = {
        "absolute_coordinates": state.absolute_coordinates,
        "absolute_extrude": state.absolute_extrude,
        "logical_e": state.logical_e,
        "base_xyz": state.base_xyz,
        "gcode_offsets": state.gcode_offsets,
    }
    for field in ("feedrate", "speed_factor", "extrude_factor",
                  "current_object", "exclusion_transform"):
        value = getattr(state, field)
        if value is not None:
            result[field] = value
    for key, value in state.velocity_limits.items():
        result[VELOCITY_KEYS[key]] = value
    result["pressure_advance"] = {
        name: {key.lower(): value for key, value in values.items()}
        for name, values in state.pressure_advance.items()
    }
    if state.retraction or state.has_retraction_state:
        result["retraction"] = {key.lower(): value for key, value in state.retraction.items()}
        if state.has_retraction_state:
            result["retraction"]["retracted"] = state.retracted
    if state.fan_speeds:
        result["fans"] = state.fan_speeds
    if "P" in state.progress:
        result["progress_percent"] = min(100., state.progress["P"])
    for key, value in state.print_stats.items():
        result[key.lower()] = value
    return result


def _invalid(field):
    raise RecoveryParseError("Invalid checkpoint state: " + field)


def _number(value, field, minimum=None, positive=False):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or (minimum is not None and value < minimum)
            or (positive and value <= 0)):
        _invalid(field)
    return value


def _boolean(value, field):
    if type(value) is not bool:
        _invalid(field)
    return value


def _vector(value, field, size):
    if not isinstance(value, list) or len(value) != size:
        _invalid(field)
    return [_number(v, field) for v in value]


def _mapping(value, field, limits):
    if not isinstance(value, dict) or not set(value) <= set(limits):
        _invalid(field)
    return {key: _number(v, field, limits[key][0], limits[key][1])
            for key, v in value.items()}


def _name(value):
    if not isinstance(value, str):
        _invalid("object name")
    object_name_parameter(value)
    return value


def _extruder_name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_]+", value):
        _invalid("extruder")
    return value


def decode(data, static):
    state = RecoveryGCodeState()
    if not isinstance(data, dict):
        _invalid("fields")
    if not isinstance(static, dict):
        _invalid("recovery objects")
    required = {"feedrate", "speed_factor", "extrude_factor", "base_xyz",
                "gcode_offsets", "pressure_advance"} | set(VELOCITY_KEYS.values())
    if not required <= data.keys():
        _invalid("incomplete runtime snapshot")
    state.mesh_snapshot = static.get("mesh")
    if not isinstance(state.mesh_snapshot, dict):
        _invalid("saved mesh")
    for field in ("absolute_coordinates", "absolute_extrude"):
        setattr(state, field, _boolean(data.get(field), field))
    state.logical_e = _number(data.get("logical_e"), "logical_e")
    for field in ("feedrate", "speed_factor", "extrude_factor"):
        setattr(state, field, _number(data[field], field, positive=True))
    state.base_xyz = _vector(data["base_xyz"], "base_xyz", 3)
    state.gcode_offsets = _vector(data["gcode_offsets"], "gcode_offsets", 3)
    if "skew" in static:
        state.skew_factors = _vector(static["skew"], "skew", 3)
    state.velocity_limits = _mapping(
        {key: data[name] for key, name in VELOCITY_KEYS.items()},
        "velocity_limits", {
        "VELOCITY": (None, True), "ACCEL": (None, True),
        "ACCEL_TO_DECEL": (None, True), "SQUARE_CORNER_VELOCITY": (0., False)})
    pa = data["pressure_advance"]
    if not isinstance(pa, dict) or "extruder" not in pa:
        _invalid("pressure_advance")
    for name, values in pa.items():
        _extruder_name(name)
        values = _mapping(values, "pressure_advance", {
            "advance": (0., False), "smooth_time": (0., False)})
        if set(values) != {"advance", "smooth_time"} or values["smooth_time"] > .2:
            _invalid("pressure_advance")
        state.pressure_advance[name] = {key.upper(): value for key, value in values.items()}
    retraction = data.get("retraction", {})
    if not isinstance(retraction, dict):
        _invalid("retraction")
    if "retracted" in retraction:
        state.has_retraction_state = True
        state.retracted = _boolean(retraction["retracted"], "retracted")
    state.retraction = _mapping(
        {key.upper(): value for key, value in retraction.items() if key != "retracted"},
        "retraction", {
        "RETRACT_LENGTH": (0., False), "RETRACT_SPEED": (1., False),
        "UNRETRACT_EXTRA_LENGTH": (0., False), "UNRETRACT_SPEED": (1., False)})
    state.fan_speeds = _mapping(data.get("fans", {}), "fans",
                                {name: (0., False) for name in FAN_NAMES})
    if any(v > 1. for v in state.fan_speeds.values()):
        _invalid("fans")
    if "progress_percent" in data:
        progress = _number(data["progress_percent"], "progress_percent", 0.)
        if progress > 100.:
            _invalid("progress_percent")
        state.progress["P"] = progress
    state.print_stats = _mapping(
        {key: data[key.lower()] for key in ("CURRENT_LAYER", "TOTAL_LAYER")
         if key.lower() in data}, "print_stats",
        {"CURRENT_LAYER": (0., False), "TOTAL_LAYER": (0., False)})
    if any(type(v) is not int for v in state.print_stats.values()):
        _invalid("print_stats")
    objects = static.get("objects", [])
    if not isinstance(objects, list):
        _invalid("objects")
    for definition in objects:
        if not isinstance(definition, dict) or "name" not in definition:
            _invalid("objects")
        name = _name(definition["name"])
        obj = {"name": name}
        if "center" in definition:
            obj["center"] = _vector(definition["center"], "center", 2)
        if "polygon" in definition:
            if not isinstance(definition["polygon"], list):
                _invalid("polygon")
            obj["polygon"] = [_vector(p, "polygon", 2) for p in definition["polygon"]]
        state.objects[name] = obj
    if "current_object" in data:
        state.current_object = _name(data["current_object"])
    transform = data.get("exclusion_transform")
    if transform is not None:
        fields = set(VECTOR_FIELDS + EXCLUSION_NUMBERS) | {
            "offsets", "in_excluded_region", "initial_extrusion_moves"}
        if not isinstance(transform, dict) or set(transform) != fields:
            _invalid("exclusion_transform")
        if not isinstance(transform["offsets"], dict):
            _invalid("exclusion_transform offsets")
        priming = transform["initial_extrusion_moves"]
        if type(priming) is not int or not 0 <= priming <= 5:
            _invalid("initial_extrusion_moves")
        state.exclusion_transform = {
            **{key: _vector(transform[key], key, 4) for key in VECTOR_FIELDS},
            **{key: _number(transform[key], key) for key in EXCLUSION_NUMBERS},
            "offsets": {_extruder_name(name): _vector(v, "offsets", 4)
                        for name, v in transform["offsets"].items()},
            "in_excluded_region": _boolean(transform["in_excluded_region"],
                                           "in_excluded_region"),
            "initial_extrusion_moves": priming,
        }
    return state


def restore_exclusion(source, state, old_e, new_e):
    """Rebase the saved exclusion transform onto the restarted extruder."""
    if source is None:
        raise RecoveryParseError("Object exclusion is unavailable")
    if source.next_transform is None:
        source._register_transform()
    if source.next_transform is None:
        raise RecoveryParseError("Object exclusion transform could not be restored")
    data = state.exclusion_transform
    delta = new_e - old_e
    for key in VECTOR_FIELDS:
        vector = list(data[key])
        vector[3] += delta
        setattr(source, key, vector)
    for key in EXCLUSION_NUMBERS:
        setattr(source, key, data[key] + (0. if key == "extruder_adj" else delta))
    source.extrusion_offsets = {name: list(v) for name, v in data["offsets"].items()}
    source.in_excluded_region = data["in_excluded_region"]
    source.initial_extrusion_moves = data["initial_extrusion_moves"]
