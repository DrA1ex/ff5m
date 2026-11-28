## Resurrection G-code state reducer
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import json

from ..object_exclusion import exclude_command, object_name_parameter


class RecoveryParseError(Exception):
    pass


class RecoveryParseCancelled(RecoveryParseError):
    pass


def _format_number(value):
    if float(value).is_integer():
        return str(int(value))
    return format(float(value), ".12g")


class RecoveryGCodeState:
    VELOCITY_FIELDS = (
        "VELOCITY", "ACCEL", "ACCEL_TO_DECEL", "SQUARE_CORNER_VELOCITY")
    RETRACTION_FIELDS = (
        "RETRACT_LENGTH", "RETRACT_SPEED",
        "UNRETRACT_EXTRA_LENGTH", "UNRETRACT_SPEED")

    def __init__(self):
        self.absolute_coordinates = True
        self.absolute_extrude = True
        self.logical_e = 0.
        self.feedrate = None
        self.speed_factor = None
        self.extrude_factor = None
        self.velocity_limits = {}
        self.pressure_advance = {}
        self.skew_base = None
        self.skew_planes = {}
        self.retraction = {}
        self.retracted = False
        self.has_retraction_state = False
        self.fans = {}
        self.progress = {}
        self.print_stats = {}
        self.objects = {}
        self.current_object = None
        self.skew_factors = None
        self.base_xyz = None
        self.gcode_offsets = None
        self.fan_speeds = {}
        self.exclusion_transform = None
        self.mesh_snapshot = None

    def exclusion_commands(self, excluded):
        commands = []
        for name, definition in self.objects.items():
            params = [object_name_parameter(name)]
            if "center" in definition:
                params.append("CENTER=" + ",".join(_format_number(value) for value in definition["center"]))
            if "polygon" in definition:
                params.append("POLYGON=" + json.dumps(definition["polygon"], separators=(",", ":"), allow_nan=False))
            commands.append("EXCLUDE_OBJECT_DEFINE " + " ".join(params))
        commands.extend(exclude_command(name) for name in excluded)
        if self.current_object is not None:
            commands.append("EXCLUDE_OBJECT_START " + object_name_parameter(self.current_object))
        return commands

    def skew_commands(self):
        commands = []
        if self.skew_base is not None:
            commands.append(self.skew_base)
        if self.skew_planes:
            commands.append("SET_SKEW " + " ".join(
                "%s=%s" % (plane, self.skew_planes[plane])
                for plane in ("XY", "XZ", "YZ")
                if plane in self.skew_planes))
        return commands

    def before_retraction_commands(self, include_skew=True):
        commands = []
        if self.velocity_limits:
            params = [
                "%s=%s" % (key, _format_number(self.velocity_limits[key]))
                for key in self.VELOCITY_FIELDS
                if key in self.velocity_limits
            ]
            commands.append("SET_VELOCITY_LIMIT " + " ".join(params))

        for extruder in sorted(
                self.pressure_advance,
                key=lambda value: (value is not None, value or "")):
            values = self.pressure_advance[extruder]
            params = []
            if extruder is not None:
                params.append("EXTRUDER=%s" % (extruder,))
            for key in ("ADVANCE", "SMOOTH_TIME"):
                if key in values:
                    params.append("%s=%s" % (
                        key, _format_number(values[key])))
            if len(params) > (1 if extruder is not None else 0):
                commands.append("SET_PRESSURE_ADVANCE " + " ".join(params))

        if include_skew:
            commands.extend(self.skew_commands())

        if self.speed_factor is not None:
            commands.append("M220 S%s" % (
                _format_number(self.speed_factor),))
        if self.extrude_factor is not None:
            commands.append("M221 S%s" % (
                _format_number(self.extrude_factor),))

        commands.append("G92 E%s" % (_format_number(self.logical_e),))
        if self.feedrate is not None:
            commands.append("G1 F%s" % (_format_number(self.feedrate),))
        commands.append("G90" if self.absolute_coordinates else "G91")
        commands.append("M82" if self.absolute_extrude else "M83")

        if self.retraction:
            commands.append("SET_RETRACTION " + " ".join(
                "%s=%s" % (key, _format_number(self.retraction[key]))
                for key in self.RETRACTION_FIELDS
                if key in self.retraction))
        return commands

    def final_commands(self):
        commands = []
        for channel in sorted(self.fans):
            speed = self.fans[channel]
            params = []
            if channel:
                params.append("P%d" % (channel,))
            params.append("S%s" % (_format_number(speed),))
            commands.append("M106 " + " ".join(params))
        for name, speed in sorted(self.fan_speeds.items()):
            commands.append("SET_FAN_SPEED FAN=%s SPEED=%s" %
                            (name, _format_number(speed)))
        if self.progress:
            commands.append("M73 " + " ".join(
                "%s%s" % (key, _format_number(self.progress[key]))
                for key in ("P", "R") if key in self.progress))
        if self.print_stats:
            commands.append("SET_PRINT_STATS_INFO " + " ".join(
                "%s=%s" % (key, _format_number(self.print_stats[key]))
                for key in ("CURRENT_LAYER", "TOTAL_LAYER")
                if key in self.print_stats))
        return commands


