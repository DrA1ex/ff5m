## Resurrection G-code state reducer
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import math, os, re, time


PARSE_CHUNK_SIZE = 16 * 1024
MAX_GCODE_LINE_SIZE = 1024 * 1024


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

    def before_retraction_commands(self):
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

        if self.skew_base is not None:
            commands.append(self.skew_base)
        if self.skew_planes:
            commands.append("SET_SKEW " + " ".join(
                "%s=%s" % (plane, self.skew_planes[plane])
                for plane in ("XY", "XZ", "YZ")
                if plane in self.skew_planes))

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


class GCodeStateReducer:
    NUMBER_PATTERN = (
        rb"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
    SHORT_COMMAND = re.compile(r"^([GMT]\d+)(.*)$", re.IGNORECASE)
    LONG_COMMAND = re.compile(
        r"^([A-Z_][A-Z0-9_]*)(?:\s+(.*))?$", re.IGNORECASE)
    SHORT_PARAM = re.compile(
        r"([A-Z])\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)"
        r"(?:[eE][-+]?\d+)?)", re.IGNORECASE)

    SHORT_STATE_COMMANDS = {
        "G0", "G1", "G10", "G11", "G90", "G91", "G92",
        "M82", "M83", "M106", "M107", "M204", "M220", "M221", "M73",
    }
    LONG_STATE_COMMANDS = {
        "SET_VELOCITY_LIMIT", "SET_PRESSURE_ADVANCE",
        "SET_SKEW", "SKEW_PROFILE", "SET_RETRACTION",
        "SET_PRINT_STATS_INFO",
    }
    MODE_CHANGES = {
        "G90": ("absolute_coordinates", True),
        "G91": ("absolute_coordinates", False),
        "M82": ("absolute_extrude", True),
        "M83": ("absolute_extrude", False),
    }
    FACTOR_CHANGES = {
        "M220": ("speed_factor", "S"),
        "M221": ("extrude_factor", "S"),
    }
    RETRACTION_STATES = {"G10": True, "G11": False}
    METADATA_FIELDS = {
        "M73": ("progress", ("P", "R"), False),
        "SET_PRINT_STATS_INFO": (
            "print_stats", ("CURRENT_LAYER", "TOTAL_LAYER"), True),
    }
    VELOCITY_LIMITS = {
        "VELOCITY": (None, 0.),
        "ACCEL": (None, 0.),
        "ACCEL_TO_DECEL": (None, 0.),
        "SQUARE_CORNER_VELOCITY": (0., None),
    }
    RETRACTION_LIMITS = {
        "RETRACT_LENGTH": (0., None),
        "RETRACT_SPEED": (None, 0.),
        "UNRETRACT_EXTRA_LENGTH": (0., None),
        "UNRETRACT_SPEED": (None, 0.),
    }
    MODE_LINE = re.compile(
        rb"(?im)^[ \t]*(?:G90|G91|G92|M82|M83)\b")
    NONSTANDARD_MOTION_LINE = re.compile(
        rb"(?im)^[ \t]*(?:N\d+[ \t]+)?G[01](?:[XYZEF])")
    NUMBERED_LINE = re.compile(rb"(?im)^[ \t]*N\d+[ \t]+")
    STATE_LINE = re.compile(
        rb"(?im)^[ \t]*(?:"
        rb"G10|G11|M106|M107|M204|M220|M221|M73|"
        rb"SET_VELOCITY_LIMIT|SET_PRESSURE_ADVANCE|"
        rb"SET_SKEW|SKEW_PROFILE|SET_RETRACTION|"
        rb"SET_PRINT_STATS_INFO"
        rb")\b[^\r\n]*")
    FEEDRATE = re.compile(
        rb"(?im)^[ \t]*G[01]\b[^\r\n;]*?[ \t]F("
        + NUMBER_PATTERN + rb")(?=[ \t;\r\n]|$)")
    EXTRUSION = re.compile(
        rb"(?im)^[ \t]*G[01]\b[^\r\n;]*?[ \t]E("
        + NUMBER_PATTERN + rb")(?=[ \t;\r\n]|$)")

    def __init__(self):
        self.state = RecoveryGCodeState()

    def consume_block(self, block):
        barriers = list(self.MODE_LINE.finditer(block))
        barriers.extend(self.NONSTANDARD_MOTION_LINE.finditer(block))
        barriers.extend(self.NUMBERED_LINE.finditer(block))
        if barriers:
            position = 0
            for match in sorted(barriers, key=lambda item: item.start()):
                if match.start() < position:
                    continue
                self._consume_fast_block(block[position:match.start()])
                line_end = block.find(b"\n", match.start())
                if line_end < 0:
                    line_end = len(block)
                self.consume(block[match.start():line_end])
                position = line_end + 1
            self._consume_fast_block(block[position:])
            return
        self._consume_fast_block(block)

    def _consume_fast_block(self, block):
        feedrates = self.FEEDRATE.findall(block)
        if feedrates:
            self.state.feedrate = float(feedrates[-1])

        extrusion = self.EXTRUSION.findall(block)
        if extrusion:
            values = [float(value) for value in extrusion]
            if (self.state.absolute_coordinates
                    and self.state.absolute_extrude):
                self.state.logical_e = values[-1]
            else:
                self.state.logical_e += math.fsum(values)

        for match in self.STATE_LINE.finditer(block):
            self.consume(match.group(0))

    def consume(self, raw_line):
        line = raw_line.strip()
        comment = line.find(b";")
        if comment >= 0:
            line = line[:comment].rstrip()
        if not line or line.startswith(b"#"):
            return
        if line[:1].upper() == b"N" or b"*" in line:
            self._consume_slow(line)
            return

        fields = line.split(None, 1)
        command_bytes = fields[0].upper()
        params_bytes = fields[1] if len(fields) > 1 else b""
        try:
            command = command_bytes.decode("ascii")
        except UnicodeDecodeError:
            return

        if command in {"G0", "G1"}:
            self._consume_motion(command, params_bytes)
            return
        if command in self.SHORT_STATE_COMMANDS:
            self._apply(
                command, self._parse_fast_short_params(
                    params_bytes, command))
            return
        if command in self.LONG_STATE_COMMANDS:
            self._apply(
                command, self._parse_long_params(params_bytes, command))
            return
        if (command_bytes[:1] in {b"G", b"M", b"T"}
                and len(command_bytes) > 1
                and command_bytes[1:2].isdigit()):
            self._consume_slow(line)

    def _consume_motion(self, command, params_bytes):
        state = self.state
        for token in params_bytes.split():
            if len(token) < 2:
                raise RecoveryParseError(
                    "Invalid parameters for %s" % (command,))
            key = token[:1].upper()
            if key not in {b"E", b"F"}:
                continue
            try:
                value = float(token[1:])
            except ValueError:
                raise RecoveryParseError(
                    "Invalid %s value for %s"
                    % (key.decode("ascii"), command))
            if not math.isfinite(value):
                raise RecoveryParseError(
                    "Non-finite %s value for %s"
                    % (key.decode("ascii"), command))
            if key == b"F":
                if value <= 0.:
                    raise RecoveryParseError(
                        "Out-of-range F value for %s" % (command,))
                state.feedrate = value
            elif state.absolute_coordinates and state.absolute_extrude:
                state.logical_e = value
            else:
                state.logical_e += value

    def _parse_fast_short_params(self, params_bytes, command):
        params = {}
        for token in params_bytes.split():
            if len(token) < 2 or not token[:1].isalpha():
                raise RecoveryParseError(
                    "Invalid parameters for %s: %s"
                    % (command, params_bytes.decode(
                        "utf-8", errors="replace")))
            try:
                key = token[:1].decode("ascii").upper()
            except UnicodeDecodeError:
                raise RecoveryParseError(
                    "Invalid parameters for %s" % (command,))
            params[key] = token[1:]
        return params

    def _consume_slow(self, raw_line):
        line = raw_line.decode("utf-8", errors="replace")
        line = re.sub(r"^N\d+\s+", "", line, flags=re.IGNORECASE)
        line = re.sub(r"\*\d+\s*$", "", line).strip()

        match = self.SHORT_COMMAND.match(line)
        if match is not None:
            command = match.group(1).upper()
            if command not in self.SHORT_STATE_COMMANDS:
                return
            params = self._parse_short_params(match.group(2), command)
        else:
            match = self.LONG_COMMAND.match(line)
            if match is None:
                return
            command = match.group(1).upper()
            if command not in self.LONG_STATE_COMMANDS:
                return
            params = self._parse_long_params(match.group(2) or "", command)
        self._apply(command, params)

    def _parse_short_params(self, text, command):
        params = {}
        position = 0
        while position < len(text):
            while position < len(text) and text[position].isspace():
                position += 1
            if position >= len(text):
                break
            match = self.SHORT_PARAM.match(text, position)
            if match is None:
                raise RecoveryParseError(
                    "Invalid parameters for %s: %s" % (command, text.strip()))
            params[match.group(1).upper()] = match.group(2)
            position = match.end()
        return params

    def _parse_long_params(self, text, command):
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="replace")
        params = {}
        for token in text.split():
            if "=" not in token:
                raise RecoveryParseError(
                    "Invalid parameters for %s: %s" % (command, text))
            key, value = token.split("=", 1)
            key = key.upper()
            if not key or not value:
                raise RecoveryParseError(
                    "Invalid parameters for %s: %s" % (command, text))
            params[key] = value
        return params

    def _float(self, params, key, command, default=None,
               minimum=None, above=None):
        value = params.get(key)
        if value is None:
            return default
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise RecoveryParseError(
                "Invalid %s value for %s" % (key, command))
        if not math.isfinite(value):
            raise RecoveryParseError(
                "Non-finite %s value for %s" % (key, command))
        if minimum is not None and value < minimum:
            raise RecoveryParseError(
                "Out-of-range %s value for %s" % (key, command))
        if above is not None and value <= above:
            raise RecoveryParseError(
                "Out-of-range %s value for %s" % (key, command))
        return value

    def _integer(self, params, key, command, default=None, minimum=None):
        value = self._float(params, key, command, default)
        if value is None:
            return None
        if not float(value).is_integer():
            raise RecoveryParseError(
                "Invalid integer %s value for %s" % (key, command))
        value = int(value)
        if minimum is not None and value < minimum:
            raise RecoveryParseError(
                "Out-of-range %s value for %s" % (key, command))
        return value

    def _update_numeric_fields(self, target, params, command, constraints):
        for key, (minimum, above) in constraints.items():
            value = self._float(
                params, key, command, minimum=minimum, above=above)
            if value is not None:
                target[key] = value

    def _apply(self, command, params):
        state = self.state
        if command in {"G0", "G1"}:
            feedrate = self._float(params, "F", command, above=0.)
            if feedrate is not None:
                state.feedrate = feedrate
            e_pos = self._float(params, "E", command)
            if e_pos is not None:
                if state.absolute_coordinates and state.absolute_extrude:
                    state.logical_e = e_pos
                else:
                    state.logical_e += e_pos
            return
        if command in self.MODE_CHANGES:
            attribute, value = self.MODE_CHANGES[command]
            setattr(state, attribute, value)
            return
        if command == "G92":
            if not params:
                state.logical_e = 0.
            elif "E" in params:
                state.logical_e = self._float(params, "E", command)
            return
        if command in self.FACTOR_CHANGES:
            attribute, key = self.FACTOR_CHANGES[command]
            setattr(state, attribute, self._float(
                params, key, command, default=100., above=0.))
            return
        if command == "SET_VELOCITY_LIMIT":
            self._update_numeric_fields(
                state.velocity_limits, params, command, self.VELOCITY_LIMITS)
            return
        if command == "M204":
            accel = self._float(params, "S", command, above=0.)
            if accel is None:
                p_accel = self._float(params, "P", command, above=0.)
                t_accel = self._float(params, "T", command, above=0.)
                if p_accel is None or t_accel is None:
                    return
                accel = min(p_accel, t_accel)
            state.velocity_limits["ACCEL"] = accel
            return
        if command == "SET_PRESSURE_ADVANCE":
            extruder = params.get("EXTRUDER")
            if extruder is not None and not re.match(
                    r"^[A-Za-z0-9_]+$", extruder):
                raise RecoveryParseError("Invalid extruder name")
            values = state.pressure_advance.setdefault(extruder, {})
            advance = self._float(
                params, "ADVANCE", command, minimum=0.)
            smooth_time = self._float(
                params, "SMOOTH_TIME", command, minimum=0.)
            if smooth_time is not None and smooth_time > .2:
                raise RecoveryParseError(
                    "Out-of-range SMOOTH_TIME value for %s" % (command,))
            if advance is not None:
                values["ADVANCE"] = advance
            if smooth_time is not None:
                values["SMOOTH_TIME"] = smooth_time
            if not values:
                del state.pressure_advance[extruder]
            return
        if command == "SKEW_PROFILE":
            profile = params.get("LOAD")
            if profile is not None:
                if not re.match(r"^[A-Za-z0-9_.-]+$", profile):
                    raise RecoveryParseError("Invalid skew profile name")
                state.skew_base = "SKEW_PROFILE LOAD=%s" % (profile,)
                state.skew_planes.clear()
            return
        if command == "SET_SKEW":
            clear = self._integer(
                params, "CLEAR", command, default=0, minimum=0)
            if clear:
                state.skew_base = "SET_SKEW CLEAR=1"
                state.skew_planes.clear()
                return
            for plane in ("XY", "XZ", "YZ"):
                if plane not in params:
                    continue
                lengths = params[plane].split(",")
                if len(lengths) != 3:
                    raise RecoveryParseError(
                        "Invalid %s value for %s" % (plane, command))
                values = []
                for length in lengths:
                    try:
                        value = float(length)
                    except ValueError:
                        raise RecoveryParseError(
                            "Invalid %s value for %s" % (plane, command))
                    if not math.isfinite(value) or value <= 0.:
                        raise RecoveryParseError(
                            "Invalid %s value for %s" % (plane, command))
                    values.append(_format_number(value))
                state.skew_planes[plane] = ",".join(values)
            return
        if command == "SET_RETRACTION":
            self._update_numeric_fields(
                state.retraction, params, command, self.RETRACTION_LIMITS)
            state.retracted = False
            state.has_retraction_state = True
            return
        if command in self.RETRACTION_STATES:
            state.retracted = self.RETRACTION_STATES[command]
            state.has_retraction_state = True
            return
        if command in {"M106", "M107"}:
            channel = self._integer(
                params, "P", command, default=0, minimum=0)
            speed = (0. if command == "M107" else self._float(
                params, "S", command, default=255., minimum=0.))
            state.fans[channel] = speed
            return
        if command in self.METADATA_FIELDS:
            attribute, keys, integer = self.METADATA_FIELDS[command]
            target = getattr(state, attribute)
            parser = self._integer if integer else self._float
            for key in keys:
                value = parser(params, key, command, minimum=0)
                if value is not None:
                    target[key] = value


class GCodeStateParser:
    def __init__(self, file_path, file_position, file_size, cancel_event):
        self.file_path = file_path
        self.file_position = file_position
        self.file_size = file_size
        self.cancel_event = cancel_event

    def parse(self):
        if os.path.getsize(self.file_path) != self.file_size:
            raise RecoveryParseError("G-Code file size changed")
        if self.file_position < 0 or self.file_position > self.file_size:
            raise RecoveryParseError("Invalid G-Code file position")

        reducer = GCodeStateReducer()
        with open(self.file_path, "rb") as stream:
            if self.file_position:
                stream.seek(self.file_position - 1)
                if stream.read(1) != b"\n":
                    raise RecoveryParseError(
                        "G-Code file position is not a line boundary")
            stream.seek(0)
            remaining = self.file_position
            partial = b""
            discard_comment = False
            while remaining:
                if self.cancel_event.is_set():
                    raise RecoveryParseCancelled(
                        "G-Code state parsing was cancelled")
                data = stream.read(min(PARSE_CHUNK_SIZE, remaining))
                if not data:
                    raise RecoveryParseError(
                        "Unexpected end of G-Code file")
                remaining -= len(data)
                if discard_comment:
                    line_end = data.find(b"\n")
                    if line_end < 0:
                        time.sleep(0)
                        continue
                    data = data[line_end + 1:]
                    discard_comment = False
                combined = partial + data
                last_line_end = combined.rfind(b"\n")
                if last_line_end < 0:
                    partial = combined
                else:
                    complete = combined[:last_line_end + 1]
                    partial = combined[last_line_end + 1:]
                    first_line_end = complete.find(b"\n")
                    if first_line_end > MAX_GCODE_LINE_SIZE:
                        first_line = complete[:first_line_end]
                        if not first_line.lstrip().startswith((b";", b"#")):
                            raise RecoveryParseError(
                                "G-Code line exceeds the supported size")
                        complete = complete[first_line_end + 1:]
                    if complete:
                        reducer.consume_block(complete)
                if len(partial) > MAX_GCODE_LINE_SIZE:
                    if partial.lstrip().startswith((b";", b"#")):
                        partial = b""
                        discard_comment = True
                    else:
                        raise RecoveryParseError(
                            "G-Code line exceeds the supported size")
                time.sleep(0)
            if partial:
                raise RecoveryParseError(
                    "G-Code file position is not a line boundary")

        if self.cancel_event.is_set():
            raise RecoveryParseCancelled(
                "G-Code state parsing was cancelled")
        if os.path.getsize(self.file_path) != self.file_size:
            raise RecoveryParseError("G-Code file size changed")
        return reducer.state
