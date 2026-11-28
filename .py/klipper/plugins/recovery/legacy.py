## Resurrection G-code state reducer
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import json, math, os, queue, re, shlex, threading, time

from ..object_exclusion import object_name_parameter
from .state import (
    RecoveryGCodeState, RecoveryParseError, RecoveryParseCancelled, _format_number)


PARSE_CHUNK_SIZE = 64 * 1024
MAX_GCODE_LINE_SIZE = 1024 * 1024


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
        "EXCLUDE_OBJECT_DEFINE", "EXCLUDE_OBJECT_START", "EXCLUDE_OBJECT_END",
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
    # Scan for barriers and state commands together. A literal newline
    # prefix lets the regex engine skip to line boundaries in C.
    SPECIAL_LINE = re.compile(
        rb"\n[ \t]*(?i:(?:(?P<barrier>G(?:9[012]\b|[01][XYZEF])|"
        rb"M8[23]\b|N\d+[ \t]+)|"
        rb"(?:G1[01]|M(?:10[67]|204|22[01]|73)|"
        rb"SET_(?:VELOCITY_LIMIT|PRESSURE_ADVANCE|SKEW|RETRACTION|PRINT_STATS_INFO)|"
        rb"SKEW_PROFILE|EXCLUDE_OBJECT_(?:DEFINE|START|END))\b[^\r\n]*))")
    FEEDRATE = re.compile(
        rb"\n(?i:[ \t]*G[01]\b[^\r\n;]*?[ \t]F("
        + NUMBER_PATTERN + rb")(?=[ \t;\r\n]|$))")
    EXTRUSION = re.compile(
        rb"\n(?i:[ \t]*G[01]\b[^\r\n;]*?[ \t]E("
        + NUMBER_PATTERN + rb")(?=[ \t;\r\n]|$))")
    F_TOKENS = (b" F", b"\tF", b" f", b"\tf")
    E_TOKENS = (b" E", b"\tE", b" e", b"\te")

    def __init__(self):
        self.state = RecoveryGCodeState()

    def consume_block(self, block):
        if not block:
            return
        data = b"\n" + block
        position = 0
        state_lines = []
        for match in self.SPECIAL_LINE.finditer(data):
            start = match.start()
            if start < position:
                continue
            if match.group("barrier") is None:
                state_lines.append(match.group(0)[1:])
                continue
            self._consume_motion_block(
                data, position, start + 1)
            for line in state_lines:
                self.consume(line)
            state_lines.clear()
            line_end = block.find(b"\n", start)
            if line_end < 0:
                line_end = len(block)
            self.consume(block[start:line_end])
            position = line_end + 1
        self._consume_motion_block(
            data, position, len(data))
        for line in state_lines:
            self.consume(line)

    @staticmethod
    def _last_parameter(data, pattern, tokens, start, end):
        # Search the common uppercase, space-prefixed form first. Other
        # spellings only need to be searched after the latest candidate.
        for _ in range(8):
            candidate = data.rfind(tokens[0], start, end)
            for token in tokens[1:]:
                later = data.rfind(token, max(start, candidate + 1), end)
                if later >= 0:
                    candidate = later
            if candidate < 0:
                return None
            line_start = data.rfind(b"\n", start, candidate)
            match = pattern.match(data, line_start, end)
            if match:
                return match.group(1)
            end = line_start
            if end <= start:
                return None
        values = pattern.findall(data, start, end)
        return values[-1] if values else None

    def _consume_motion_block(self, data, start, end):
        if end <= start + 1:
            return
        state = self.state
        feedrate = self._last_parameter(
            data, self.FEEDRATE, self.F_TOKENS, start, end)
        if feedrate is not None:
            state.feedrate = float(feedrate)

        if state.absolute_coordinates and state.absolute_extrude:
            extrusion = self._last_parameter(
                data, self.EXTRUSION, self.E_TOKENS, start, end)
            if extrusion is not None:
                state.logical_e = float(extrusion)
        else:
            extrusion = self.EXTRUSION.findall(data, start, end)
            if extrusion:
                state.logical_e += math.fsum(map(float, extrusion))

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
        try:
            if command.startswith("EXCLUDE_OBJECT_"):
                tokens = shlex.split(re.split(r"[#*]", text, maxsplit=1)[0])
            else:
                tokens = text.split()
        except ValueError:
            raise RecoveryParseError("Invalid parameters for %s" % command)
        params = {}
        for token in tokens:
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
        if command == "EXCLUDE_OBJECT_END":
            state.current_object = None
            return
        if command == "EXCLUDE_OBJECT_DEFINE" and params.get("RESET"):
            state.objects.clear()
            state.current_object = None
            return
        if command in {"EXCLUDE_OBJECT_DEFINE", "EXCLUDE_OBJECT_START"}:
            name = params.get("NAME", "").upper()
            if not name and command == "EXCLUDE_OBJECT_DEFINE":
                return
            try:
                object_name_parameter(name)
            except RuntimeError as error:
                raise RecoveryParseError(str(error))
            if command == "EXCLUDE_OBJECT_START":
                state.objects.setdefault(name, {"name": name})
                state.current_object = name
            else:
                definition = {"name": name}
                for field in ("CENTER", "POLYGON"):
                    if field not in params:
                        continue
                    try:
                        text = "[" + params[field] + "]" if field == "CENTER" else params[field]
                        value = json.loads(text)
                        points = [value] if field == "CENTER" else value
                        if (not isinstance(points, list)
                                or any(not isinstance(point, list) or len(point) != 2
                                       or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in point)
                                       for point in points)):
                            raise ValueError
                    except (ValueError, TypeError, OverflowError):
                        raise RecoveryParseError("Invalid %s for %s" % (field, command))
                    definition[field.lower()] = value
                state.objects[name] = definition
            return
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
        self.bytes_processed = 0

    def parse(self):
        self.bytes_processed = 0
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
            if self.cancel_event.is_set():
                raise RecoveryParseCancelled(
                    "G-Code state parsing was cancelled")
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
                        self.bytes_processed = self.file_position - remaining
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
                self.bytes_processed = self.file_position - remaining
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


class LegacyCheckpointLoader:
    """Load a V1 checkpoint cooperatively without blocking the reactor."""

    def __init__(self, reactor):
        self.reactor = reactor
        self.progress = None
        self._worker = None
        self._cancel_event = threading.Event()

    def cancel(self):
        self._cancel_event.set()
        if self._worker is not None and self._worker.is_alive():
            self._worker.join()

    def load(self, checkpoint):
        try:
            value = checkpoint["z_offset"]
            if isinstance(value, bool):
                raise ValueError
            offset = float(value)
            if not math.isfinite(offset):
                raise ValueError
        except (KeyError, TypeError, ValueError, OverflowError):
            raise RecoveryParseError("Invalid legacy checkpoint state: z_offset")

        parser = GCodeStateParser(
            checkpoint["file_path"], checkpoint["file_position"],
            checkpoint["file_size"], self._cancel_event)
        results = queue.Queue(maxsize=1)

        def parse():
            try:
                results.put((True, parser.parse()))
            except Exception as error:
                results.put((False, error))

        worker = self._worker = threading.Thread(
            target=parse, name="resurrection-gcode-parser")
        self.progress = 0
        try:
            next_progress = self.reactor.monotonic() + 5.
            worker.start()
            while worker.is_alive():
                now = self.reactor.monotonic()
                if now >= next_progress:
                    self.progress = min(
                        100, parser.bytes_processed * 100
                        // max(1, checkpoint["file_position"]))
                    next_progress = now + 5.
                self.reactor.pause(now + .050)
            worker.join()
            if results.empty():
                raise RecoveryParseError(
                    "G-Code state parser stopped without a result")
            success, result = results.get_nowait()
            if not success:
                raise result
            result.gcode_offsets = [0., 0., offset]
            result.base_xyz = list(result.gcode_offsets)
            return result
        finally:
            if worker.is_alive():
                self.cancel()
            self._worker = None
            self.progress = None
