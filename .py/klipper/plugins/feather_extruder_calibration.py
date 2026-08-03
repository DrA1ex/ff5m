## Guided Feather extruder rotation-distance calibration.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import logging
import math
import os
import re
import stat
import tempfile
import time

try:
    from .ui import NumericInputSpec, Page, ThemeColor
    from .feather_materials import (
        adaptive_grid_columns, render_material_selector)
except (ImportError, ValueError):
    from ui import NumericInputSpec, Page, ThemeColor
    from feather_materials import adaptive_grid_columns, render_material_selector


USER_CFG_PATH = "/opt/config/mod_data/user.cfg"
EXPECTED_DISTANCE = Decimal("100")
SUSPICIOUS_DEVIATION = Decimal("20")
ROTATION_QUANTUM = Decimal("0.001")
MEASUREMENT_INPUT = NumericInputSpec(
    "decimal", minimum=Decimal("0.001"), max_length=10,
    fraction_digits=3)

_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*(?:[;#].*)?$")
_ROTATION_RE = re.compile(
    r"^(\s*rotation_distance\s*:\s*)([^\s#;]+)([^\r\n]*)(\r?\n)?$",
    re.IGNORECASE)
_MEASUREMENT_RE = re.compile(r"^(?:\d+(?:\.\d*)?|\.\d+)$")


class UserConfigError(RuntimeError):
    pass


class ConcurrentUserConfigEdit(UserConfigError):
    pass


def _decimal(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("Enter a positive measurement")
    if not result.is_finite() or result <= 0:
        raise ValueError("Enter a positive measurement")
    return result


def parse_measurement(value):
    text = str(value).strip().replace(",", ".")
    if not _MEASUREMENT_RE.match(text):
        raise ValueError("Enter a positive number in millimeters")
    try:
        MEASUREMENT_INPUT.parse(text)
    except ValueError:
        raise ValueError("Enter a positive number in millimeters")
    return float(_decimal(text))


def round_rotation_distance(value):
    rounded = _decimal(value).quantize(
        ROTATION_QUANTUM, rounding=ROUND_HALF_UP)
    if rounded <= 0:
        raise ValueError(
            "Measurement produces an unusable rotation_distance")
    return float(rounded)


def calculate_rotation_distance(current, measured):
    current_value = _decimal(current)
    measured_value = _decimal(measured)
    return round_rotation_distance(
        current_value * measured_value / EXPECTED_DISTANCE)


def feed_change_percent(current, candidate):
    current_value = _decimal(current)
    candidate_value = _decimal(candidate)
    return float((current_value / candidate_value - Decimal("1")) * 100)


def measurement_is_suspicious(measured):
    measured_value = _decimal(measured)
    return abs(measured_value - EXPECTED_DISTANCE) > SUSPICIOUS_DEVIATION


class UserConfigSnapshot:
    def __init__(self, path, raw, file_stat, lines, section, key_index,
                 existing_value):
        self.path = str(path)
        self.raw = raw
        self.file_stat = file_stat
        self.exists = file_stat is not None
        self.digest = (hashlib.sha256(raw).hexdigest()
                       if self.exists else None)
        self.lines = lines
        self.section = section
        self.key_index = key_index
        self.existing_value = existing_value


def inspect_user_cfg(path=USER_CFG_PATH):
    path = os.path.abspath(path)
    try:
        with open(path, "rb") as source:
            raw = source.read()
            file_stat = os.fstat(source.fileno())
    except FileNotFoundError:
        raw = b""
        file_stat = None
    if file_stat is not None:
        if os.path.islink(path) or not stat.S_ISREG(file_stat.st_mode):
            raise UserConfigError("user.cfg must be a regular file")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UserConfigError("user.cfg is not valid UTF-8: %s" % exc)
    lines = text.splitlines(True)
    sections = []
    current = None
    key_indices = []
    existing_value = None
    for index, line in enumerate(lines):
        body = line.rstrip("\r\n")
        section_match = _SECTION_RE.match(body)
        if section_match is not None:
            current = section_match.group(1).strip().lower()
            if current == "extruder":
                sections.append(index)
            continue
        if current != "extruder":
            continue
        stripped = body.lstrip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        key_match = _ROTATION_RE.match(line)
        if key_match is None:
            continue
        trailing = key_match.group(3).strip()
        if trailing and not trailing.startswith(("#", ";")):
            raise UserConfigError(
                "Ambiguous rotation_distance line in user.cfg")
        key_indices.append(index)
        try:
            existing_value = _decimal(key_match.group(2))
        except ValueError:
            raise UserConfigError(
                "Invalid rotation_distance value in user.cfg")
    if len(sections) > 1:
        raise UserConfigError(
            "Multiple active [extruder] sections in user.cfg")
    if len(key_indices) > 1:
        raise UserConfigError(
            "Multiple active rotation_distance values in user.cfg")
    section = sections[0] if sections else None
    key_index = key_indices[0] if key_indices else None
    return UserConfigSnapshot(
        path, raw, file_stat, lines, section, key_index, existing_value)


def _newline_for(snapshot):
    for line in snapshot.lines:
        if line.endswith("\r\n"):
            return "\r\n"
    return "\n"


def _history_timestamp():
    return time.strftime("%Y-%m-%d %H:%M:%S %z").strip()


def render_user_cfg(snapshot, value):
    value_text = "%.3f" % round_rotation_distance(value)
    newline = _newline_for(snapshot)
    lines = list(snapshot.lines)
    if snapshot.key_index is not None:
        match = _ROTATION_RE.match(lines[snapshot.key_index])
        requested = Decimal(value_text)
        if snapshot.existing_value == requested:
            return snapshot.raw
        line_ending = match.group(4) or ""
        new_active = match.group(1) + value_text + line_ending
        old_line = lines[snapshot.key_index]
        leading = old_line[:len(old_line) - len(old_line.lstrip())]
        old_body = old_line[len(leading):]
        if line_ending:
            old_body = old_body[:-len(line_ending)]
        commented_old = (
            leading + "# " + old_body + "  [Feather saved "
            + _history_timestamp() + "]" + line_ending)
        lines[snapshot.key_index:snapshot.key_index + 1] = (
            commented_old, new_active)
        return "".join(lines).encode("utf-8")
    new_line = "rotation_distance: %s%s" % (value_text, newline)
    if snapshot.section is not None:
        insert_at = len(lines)
        for index in range(snapshot.section + 1, len(lines)):
            if _SECTION_RE.match(lines[index].rstrip("\r\n")) is not None:
                insert_at = index
                break
        while (insert_at > snapshot.section + 1
               and not lines[insert_at - 1].strip()):
            insert_at -= 1
        if insert_at > 0 and not lines[insert_at - 1].endswith(("\n", "\r")):
            lines[insert_at - 1] += newline
        lines.insert(insert_at, new_line)
        return "".join(lines).encode("utf-8")
    if lines:
        if not lines[-1].endswith(("\n", "\r")):
            lines[-1] += newline
        if lines[-1].strip():
            lines.append(newline)
    lines.extend(("[extruder]%s" % newline, new_line))
    return "".join(lines).encode("utf-8")


def _fsync_directory(path):
    flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
    directory_fd = os.open(path, flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _write_backup(snapshot):
    if not snapshot.exists:
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = "%s.bak-extruder-%s-%d" % (
        snapshot.path, stamp, time.time_ns() % 1000000000)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(backup, flags, snapshot.file_stat.st_mode & 0o7777)
    try:
        os.fchmod(descriptor, snapshot.file_stat.st_mode & 0o7777)
        try:
            os.fchown(descriptor, snapshot.file_stat.st_uid,
                      snapshot.file_stat.st_gid)
        except PermissionError:
            pass
        with os.fdopen(descriptor, "wb") as target:
            descriptor = None
            target.write(snapshot.raw)
            target.flush()
            os.fsync(target.fileno())
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return backup


def write_user_rotation_distance(path, value, expected_digest):
    path = os.path.abspath(path)
    snapshot = inspect_user_cfg(path)
    if snapshot.digest != expected_digest:
        raise ConcurrentUserConfigEdit(
            "user.cfg changed while the result was open; review and retry")
    rendered = render_user_cfg(snapshot, value)
    if rendered == snapshot.raw:
        return None
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    backup = _write_backup(snapshot)
    if backup is not None:
        _fsync_directory(directory)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".%s.extruder-" % os.path.basename(path), dir=directory)
    replaced = False
    try:
        mode = (snapshot.file_stat.st_mode & 0o7777
                if snapshot.exists else 0o644)
        os.fchmod(descriptor, mode)
        if snapshot.exists:
            try:
                os.fchown(descriptor, snapshot.file_stat.st_uid,
                          snapshot.file_stat.st_gid)
            except PermissionError:
                pass
        with os.fdopen(descriptor, "wb") as target:
            descriptor = None
            target.write(rendered)
            target.flush()
            os.fsync(target.fileno())
        latest = inspect_user_cfg(path)
        if latest.digest != expected_digest:
            raise ConcurrentUserConfigEdit(
                "user.cfg changed during save; no changes were written")
        os.replace(temporary, path)
        temporary = None
        replaced = True
        _fsync_directory(directory)
        verified = inspect_user_cfg(path)
        expected = Decimal("%.3f" % round_rotation_distance(value))
        if verified.existing_value != expected:
            raise UserConfigError(
                "Saved user.cfg did not contain the requested value")
        return backup
    except Exception:
        if replaced:
            try:
                rollback_user_rotation_distance(
                    path, snapshot, hashlib.sha256(rendered).hexdigest(),
                    backup)
            except Exception:
                logging.exception(
                    "[feather_screen] unable to roll back failed user.cfg save")
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def rollback_user_rotation_distance(path, original_snapshot, expected_digest,
                                    backup):
    """Undo our completed write without overwriting a later external edit."""
    path = os.path.abspath(path)
    current = inspect_user_cfg(path)
    if current.digest != expected_digest:
        raise ConcurrentUserConfigEdit(
            "user.cfg changed after save; automatic rollback was not safe")
    directory = os.path.dirname(path)
    if original_snapshot.exists:
        if backup is None or not os.path.exists(backup):
            raise UserConfigError("The user.cfg backup is unavailable")
        os.replace(backup, path)
    else:
        os.unlink(path)
    _fsync_directory(directory)
    restored = inspect_user_cfg(path)
    if restored.digest != original_snapshot.digest:
        raise UserConfigError("Unable to restore the original user.cfg")


class ExtruderCalibrationSession:
    def __init__(self, user_cfg_path=USER_CFG_PATH):
        self.user_cfg_path = str(user_cfg_path)
        self.clear()

    def clear(self):
        self.active = False
        self.phase = "intro"
        self.original_rotation = None
        self.current_rotation = None
        self.candidate = None
        self.measured = None
        self.feed_change = None
        self.suspicious = False
        self.warning_acknowledged = False
        self.verifying = False
        self.saved = False
        self.nozzle_removed = False
        self.input_text = ""
        self.temperature = None
        self.cooling_beeped = False
        self.cooling_fan_active = False
        self.cooling_message = None
        self.file_snapshot = None
        self.save_error = None
        self.save_file_written = False
        self.backup_path = None
        self.exit_return_phase = None

    def begin(self, rotation_distance):
        path = self.user_cfg_path
        self.clear()
        self.user_cfg_path = path
        self.active = True
        self.original_rotation = float(_decimal(rotation_distance))
        self.current_rotation = self.original_rotation

    def set_measurement(self, text):
        measured = parse_measurement(text)
        self.measured = measured
        self.candidate = calculate_rotation_distance(
            self.current_rotation, measured)
        self.feed_change = feed_change_percent(
            self.current_rotation, self.candidate)
        self.suspicious = measurement_is_suspicious(measured)
        self.warning_acknowledged = not self.suspicious
        return self.candidate

    def begin_measurement(self):
        self.phase = "load"
        self.input_text = ""
        self.measured = None
        self.candidate = None
        self.feed_change = None
        self.suspicious = False
        self.warning_acknowledged = False
        self.file_snapshot = None
        self.save_error = None
        self.save_file_written = False

    def apply_candidate_for_verification(self):
        if self.candidate is None:
            raise RuntimeError("Measure filament before verification")
        self.current_rotation = self.candidate
        self.verifying = True
        self.begin_measurement()


class FeatherExtruderCalibrationMixin:
    """Render and run Feather's guided extruder calibration."""

    def _runtime_rotation_distance(self):
        value = self.extruder.extruder_stepper.stepper.get_rotation_distance()
        if isinstance(value, (tuple, list)):
            value = value[0]
        value = float(value)
        if not math.isfinite(value) or value <= 0:
            raise RuntimeError("Runtime rotation_distance is invalid")
        return value

    def _start_extruder_calibration(self):
        self._require_idle()
        self._cancel_delayed_tasks()
        self.extruder_calibration.begin(self._runtime_rotation_distance())
        self._show_page(Page.EXTRUDER_CALIBRATION)

    def _extruder_simple_page(self, title, heading, body, buttons,
                              tone=ThemeColor.PRIMARY, note=None):
        commands = self.renderer.begin_page(title, back=True)
        commands += self.renderer.panel(
            24, 72, 752, 276, border=tone, background=ThemeColor.PANEL)
        commands += [
            self.renderer.text(
                400, 108, heading, tone, "JetBrainsMono Bold 12pt",
                "center", "middle", max_width=690, truncate=True),
            self.renderer.text(
                400, 196, body, ThemeColor.TEXT, "JetBrainsMono 8pt",
                "center", "middle", max_width=680, max_height=144,
                wrap=True, truncate=True),
        ]
        if note:
            commands.append(self.renderer.text(
                400, 322, note, ThemeColor.WARNING, "JetBrainsMono 8pt",
                "center", "middle", max_width=690, truncate=True))
        count = len(buttons)
        gap = 14
        width = (752 - gap * max(0, count - 1)) // max(1, count)
        for index, (action, label, state) in enumerate(buttons):
            commands += self.renderer.button(
                action, 24 + index * (width + gap), 372, width, 56,
                label, state=state, font="JetBrainsMono Bold 8pt")
        self.renderer.send(commands)

    def _render_extruder_calibration(self):
        session = self.extruder_calibration
        phase = session.phase
        if phase == "intro":
            self._extruder_simple_page(
                "Extruder calibration", "ROTATION DISTANCE %.3f" %
                session.original_rotation,
                "You need a caliper and marker. COLD PULL completely cleans "
                "the nozzle and is required if filament is loaded, after a "
                "material change, or if residue may remain. Choose FILAMENT "
                "READY only after cleaning. Feather then moves exactly 100 "
                "mm and calculates the setting.",
                (("extruder.coldpull", "COLD PULL",
                  "enabled" if self.cold_pull_materials else "disabled"),
                 ("extruder.skip", "FILAMENT READY", "enabled")),
                note="NOZZLE REMOVAL IS REQUIRED")
        elif phase == "material":
            commands = self.renderer.begin_page("Cold pull material", back=True)
            commands.append(self.renderer.text(
                400, 90, "SELECT THE FILAMENT CURRENTLY LOADED",
                ThemeColor.TEXT, "JetBrainsMono 8pt", "center", "middle"))
            materials = self.cold_pull_materials
            if materials:
                columns = adaptive_grid_columns(len(materials))
                gap = 20
                width = min(295, (690 - gap * (columns - 1)) // columns)
                commands += render_material_selector(
                    self.renderer, "extruder.material.", 55, 135, width, 72,
                    columns=columns, column_gap=gap, row_gap=28,
                    area_width=690, materials=materials,
                    font="JetBrainsMono Bold 12pt")
            else:
                commands.append(self.renderer.text(
                    400, 230, "NO COLD PULL MATERIALS ENABLED", ThemeColor.DIM,
                    "JetBrainsMono Bold 10pt", "center", "middle"))
            self.renderer.send(commands)
        elif phase == "cut":
            self._extruder_simple_page(
                "Prepare filament", "REMOVE AND CUT FILAMENT",
                "Pull out the remaining filament by hand. Cut away every "
                "melted or deformed section so the end is straight and clean.",
                (("extruder.prepared", "FILAMENT READY", "enabled"),))
        elif phase == "cooling":
            temperature = ("--" if session.temperature is None
                           else "%.1f C" % session.temperature)
            self._extruder_simple_page(
                "Cool nozzle", "COOLING: %s" % temperature,
                "The heater target is zero and the head fan is at 100%. "
                "Wait until the temperature is below 50 C. Feather will "
                "stop the fan and beep when it is safe.",
                (), tone=ThemeColor.WARNING, note=(session.cooling_message or
                                           "DO NOT REMOVE THE NOZZLE YET"))
        elif phase == "remove":
            self._extruder_simple_page(
                "Remove nozzle", "TEMPERATURE BELOW 50 C",
                "Release both nozzle levers and carefully pull the nozzle "
                "module downward. Feather cannot detect whether it is removed.",
                (("extruder.nozzle_removed", "NOZZLE REMOVED", "warning"),),
                tone=ThemeColor.WARNING)
        elif phase == "load":
            self._extruder_simple_page(
                "Seat filament", "INSERT FILAMENT DIRECTLY",
                "Bypass the PTFE tube and guide the clean filament into the "
                "extruder. Press FEED while holding it straight.",
                (("extruder.feed50", "FEED 50 MM", "enabled"),))
        elif phase == "mark_first":
            self._extruder_simple_page(
                "First mark", "MARK THE ENTRY POINT",
                "If the filament did not seat, use FEED 50 MORE as often as "
                "needed. When it moves reliably, mark the exact entry point; "
                "the next action advances exactly 100 mm.",
                (("extruder.feed50", "FEED 50 MORE", "enabled"),
                 ("extruder.feed100", "MARKED / FEED 100", "enabled")))
        elif phase == "mark_second":
            self._extruder_simple_page(
                "Second mark", "MARK THE NEW ENTRY POINT",
                "Make the second mark at the extruder entrance. Feather will "
                "then retract 160 mm. Extra unload is available if the "
                "filament is still held by the gears.",
                (("extruder.unload", "MARKED / UNLOAD", "enabled"),))
        elif phase == "measure_ready":
            self._extruder_simple_page(
                "Remove and measure", "REMOVE FILAMENT AND MEASURE",
                "If the filament is still held by the gears, use UNLOAD 50 "
                "MORE. Once it is free, pull it out and measure the distance "
                "between the marks with a caliper.",
                (("extruder.unload_more", "UNLOAD 50 MORE", "enabled"),
                 ("extruder.measure_ready", "ENTER MEASUREMENT", "enabled")))
        elif phase == "input":
            self._render_extruder_measurement_input()
        elif phase == "warning":
            self._extruder_simple_page(
                "Check measurement", "UNUSUAL VALUE: %.3f MM" % session.measured,
                "This differs from 100 mm by more than 20 percent. Check the "
                "marks and your input. You may continue if the value is real.",
                (("extruder.edit", "EDIT", "enabled"),
                 ("extruder.warning_accept", "USE ANYWAY", "warning")),
                tone=ThemeColor.WARNING)
        elif phase == "result":
            self._render_extruder_result()
        elif phase == "exit_warning":
            self._extruder_simple_page(
                "Before leaving", "INSTALL THE NOZZLE",
                "If it is still removed, push the nozzle module fully into "
                "place until both levers click. Unsaved calibration is discarded.",
                (("extruder.stay", "STAY", "enabled"),
                 ("extruder.exit", "EXIT", "danger")), tone=ThemeColor.DANGER)
        elif phase == "saved":
            self._extruder_simple_page(
                "Calibration saved", "ROTATION DISTANCE %.3f" %
                session.current_rotation,
                "Install the nozzle until both levers click. You must calibrate "
                "Flow / Flow Ratio, then Pressure Advance. Bed Mesh and Z "
                "Offset do not need recalibration.",
                (("extruder.done", "DONE", "enabled"),),
                tone=ThemeColor.SUCCESS, note="NO KLIPPER RESTART WAS PERFORMED")

    def _render_extruder_measurement_input(self):
        session = self.extruder_calibration
        commands = self.renderer.begin_page("Measured distance", back=True)
        actions = dict((digit, "extruder.key.%s" % digit)
                       for digit in "0123456789")
        actions.update({
            "decimal": "extruder.key.dot",
            "backspace": "extruder.key.backspace",
            "confirm": "extruder.measure",
        })
        commands += self.renderer.numeric_keypad(
            18, 65, 764, 370, "Distance between marks",
            session.input_text, actions, subtitle="mm",
            mode=MEASUREMENT_INPUT, confirm_label="CALCULATE")
        self.renderer.send(commands)

    def _refresh_extruder_file_snapshot(self):
        session = self.extruder_calibration
        session.file_snapshot = inspect_user_cfg(session.user_cfg_path)
        session.save_error = None
        return session.file_snapshot

    def _render_extruder_result(self):
        session = self.extruder_calibration
        snapshot = session.file_snapshot
        if snapshot is None:
            try:
                snapshot = self._refresh_extruder_file_snapshot()
            except UserConfigError as exc:
                session.save_error = str(exc)
        tested = session.current_rotation
        candidate = session.candidate
        change = session.feed_change
        direction = "MORE" if change > 0 else "LESS" if change < 0 else "THE SAME"
        commands = self.renderer.begin_page("Calibration result", back=True)
        commands += self.renderer.panel(
            24, 68, 752, 282, border=ThemeColor.BORDER, background=ThemeColor.PANEL)
        rows = (
            ("MEASURED", "%.3f MM" % session.measured),
            (("TESTED" if session.verifying else "CURRENT"),
             "%.3f" % tested),
            (("NEXT CORRECTION" if session.verifying else "NEW DISTANCE"),
             "%.3f" % candidate),
            (("IF CORRECTED" if session.verifying else "MOTOR FEED"),
             "%s // %+.2f%%" % (direction, change)),
        )
        for index, (label, value) in enumerate(rows):
            commands += self.renderer.metric_row(
                55, 96 + index * 43, 690, label, value)
        existing = None if snapshot is None else snapshot.existing_value
        file_differs = (existing is not None and
                        abs(float(existing) - session.original_rotation) > 0.0005)
        file_text = ("NOT SET" if existing is None else "%.3f" % existing)
        if file_differs:
            file_text += " // DIFFERS FROM RUNTIME %.3f" % session.original_rotation
        color = ThemeColor.WARNING if file_differs else ThemeColor.DIM
        commands.append(self.renderer.text(
            55, 278, "USER.CFG NOW: %s" % file_text, color,
            "JetBrainsMono 8pt", "left", "middle"))
        note = (session.save_error or
                "AFTER SAVING: CALIBRATE FLOW, THEN PRESSURE ADVANCE")
        commands.append(self.renderer.text(
            400, 322, note, ThemeColor.DANGER if session.save_error else ThemeColor.WARNING,
            "JetBrainsMono 8pt", "center", "middle",
            max_width=690, truncate=True))
        save_state = "disabled" if session.file_snapshot is None else "enabled"
        if session.verifying:
            buttons = (("extruder.save_tested", "SAVE TESTED", save_state),
                       ("extruder.refine", "REFINE / RETEST", "enabled"))
        else:
            buttons = (("extruder.save", "SAVE NOW", save_state),
                       ("extruder.verify", "VERIFY", "enabled"))
        width = 369
        for index, (action, label, state) in enumerate(buttons):
            commands += self.renderer.button(
                action, 24 + index * 383, 372, width, 56, label,
                state=state, font="JetBrainsMono Bold 8pt")
        if session.save_error:
            value_text = ("--" if session.candidate is None
                          else "%.3f" % session.candidate)
            if session.save_file_written:
                title = "RUNTIME APPLY FAILED"
                lines = (
                    "ROTATION_DISTANCE %s" % value_text,
                    "USER.CFG IS SAVED.",
                    "RESTART KLIPPER TO LOAD THE VALUE.",
                    session.save_error,
                )
            else:
                title = "SAVE FAILED"
                lines = (
                    "ROTATION_DISTANCE %s" % value_text,
                    "USER.CFG WAS NOT UPDATED.",
                    "WRITE THIS VALUE MANUALLY.",
                    session.save_error,
                )
            commands += self.renderer.dialog(
                title, lines,
                (("extruder.save_error.ok", "KEEP RESULT", "enabled"),),
                x=70, y=82, width=660, height=320, tone="danger")
        self.renderer.send(commands)

    def _cold_extrusion_move(self, distance, speed=300):
        heater = self.extruder.heater
        state_name = "_feather_extruder_calibration"
        move_error = None
        try:
            heater.set_extrusion_override(True)
            self._run_blocking_gcode(
                "SAVE_GCODE_STATE NAME=%s\nM83\nG1 E%g F%d\nM400" %
                (state_name, distance, speed),
                "MOVING FILAMENT...")
        except Exception as exc:
            move_error = exc
            raise
        finally:
            heater.set_extrusion_override(False)
            # SAVE_GCODE_STATE is the first command in the serialized script.
            # Attempt restoration on both success and command failure.
            try:
                self._run_script(
                    "RESTORE_GCODE_STATE NAME=%s MOVE=0" % state_name,
                    show_notice=False)
            except Exception:
                if move_error is None:
                    raise
                logging.exception(
                    "[feather_screen] unable to restore extrusion state "
                    "after a failed move")

    def _prepare_extruder_calibration(self):
        session = self.extruder_calibration
        self._run_blocking_gcode(
            "M104 S0\nG28\nG90\nG1 X0 Y0 F7800\nM400",
            "POSITIONING HEAD...")
        status = self.extruder.get_status(self.reactor.monotonic())
        if float(status.get("target", 0.0)) != 0.0:
            raise RuntimeError("Extruder heater target did not reach zero")
        session.temperature = float(status.get("temperature", 0.0))
        self._set_extruder_cooling_fan(True)
        session.phase = "cooling"
        self._show_page(Page.EXTRUDER_CALIBRATION)
        self._poll_extruder_calibration(self.reactor.monotonic(), force=True)

    def _set_extruder_cooling_fan(self, enabled, best_effort=False):
        session = self.extruder_calibration
        enabled = bool(enabled)
        if session.cooling_fan_active == enabled:
            return
        command = "M106 P0 S255" if enabled else "M107"
        try:
            self._run_script(command, show_notice=False)
        except Exception:
            if not best_effort:
                raise
            logging.exception(
                "[feather_screen] unable to stop extruder calibration fan")
        session.cooling_fan_active = enabled

    def _poll_extruder_calibration(self, eventtime, force=False):
        session = getattr(self, "extruder_calibration", None)
        if session is None or not session.active or session.phase != "cooling":
            return
        status = self.extruder.get_status(eventtime)
        temperature = float(status.get("temperature", 0.0))
        target = float(status.get("target", 0.0))
        old_display = (None if session.temperature is None
                       else int(session.temperature))
        session.temperature = temperature
        if target != 0.0:
            session.cooling_message = "SET THE HEATER TARGET BACK TO 0 C"
            if force or old_display != int(temperature):
                self._render_extruder_calibration()
            return
        session.cooling_message = None
        if temperature < 50.0:
            self._set_extruder_cooling_fan(False)
            should_beep = not session.cooling_beeped
            # BEEP yields while playing its tone sequence. Publish the
            # terminal state first so a reactor callback cannot re-enter this
            # branch and recursively invoke the same macro.
            session.cooling_beeped = True
            session.phase = "remove"
            self._show_page(Page.EXTRUDER_CALIBRATION)
            if should_beep:
                self._run_script("BEEP", show_notice=False)
        elif force or old_display != int(temperature):
            self._render_extruder_calibration()

    def _append_extruder_input(self, token):
        session = self.extruder_calibration
        session.input_text = MEASUREMENT_INPUT.apply(
            session.input_text, token)
        self._render_extruder_measurement_input()

    def _set_extruder_runtime_rotation(self, value):
        self._run_script(
            "SET_EXTRUDER_ROTATION_DISTANCE EXTRUDER=extruder DISTANCE=%.3f" %
            round_rotation_distance(value))

    def _save_extruder_rotation(self, value):
        session = self.extruder_calibration
        if session.file_snapshot is None:
            try:
                self._refresh_extruder_file_snapshot()
            except Exception as exc:
                self._show_extruder_save_error(exc, file_written=False)
                return
        try:
            backup = write_user_rotation_distance(
                session.user_cfg_path, value,
                session.file_snapshot.digest)
        except ConcurrentUserConfigEdit as exc:
            session.file_snapshot = None
            try:
                self._refresh_extruder_file_snapshot()
            except Exception:
                logging.exception(
                    "[feather_screen] unable to refresh user.cfg after "
                    "concurrent edit")
            self._show_extruder_save_error(exc, file_written=False)
            return
        except Exception as exc:
            logging.exception(
                "[feather_screen] unable to save extruder rotation distance")
            self._show_extruder_save_error(exc, file_written=False)
            return
        session.backup_path = backup
        try:
            session.file_snapshot = inspect_user_cfg(session.user_cfg_path)
        except Exception as exc:
            logging.exception(
                "[feather_screen] unable to refresh saved user.cfg")
            self._show_extruder_save_error(exc, file_written=True)
            return
        try:
            self._set_extruder_runtime_rotation(value)
        except Exception as exc:
            logging.exception(
                "[feather_screen] extruder rotation saved but runtime "
                "application failed")
            self._show_extruder_save_error(exc, file_written=True)
            return
        session.current_rotation = round_rotation_distance(value)
        session.saved = True
        session.save_error = None
        session.save_file_written = False
        session.phase = "saved"
        self._show_page(Page.EXTRUDER_CALIBRATION)

    def _show_extruder_save_error(self, error, file_written):
        session = self.extruder_calibration
        session.save_error = str(error) or "Unknown save error"
        session.save_file_written = bool(file_written)
        session.phase = "result"
        value_text = ("--" if session.candidate is None
                      else "%.3f" % session.candidate)
        logging.error(
            "[feather_screen] extruder calibration save recovery "
            "rotation_distance=%s file_written=%s error=%s",
            value_text, session.save_file_written,
            session.save_error)
        self._show_page(Page.EXTRUDER_CALIBRATION)

    def _restore_extruder_runtime(self):
        session = self.extruder_calibration
        if (session.active and not session.saved
                and session.current_rotation != session.original_rotation):
            self._set_extruder_runtime_rotation(session.original_rotation)
            session.current_rotation = session.original_rotation

    def _cancel_extruder_calibration(self, confirm=True):
        session = self.extruder_calibration
        if not session.active:
            self._show_page(Page.CALIBRATION_HOME)
            return
        if confirm and session.nozzle_removed and session.phase != "exit_warning":
            session.exit_return_phase = session.phase
            session.phase = "exit_warning"
            self._render_extruder_calibration()
            return
        self._set_extruder_cooling_fan(False, best_effort=True)
        self._restore_extruder_runtime()
        session.clear()
        self._show_page(Page.CALIBRATION_HOME)

    def _handle_extruder_calibration_action(self, action):
        session = self.extruder_calibration
        if action == "extruder.coldpull":
            if not self.cold_pull_materials:
                raise RuntimeError("No cold-pull materials are enabled")
            session.phase = "material"
        elif action == "extruder.skip":
            session.phase = "cut"
        elif action.startswith("extruder.material."):
            material = action.rsplit(".", 1)[1]
            if material not in self.cold_pull_profiles:
                raise ValueError("Unknown cold-pull material")
            hot, cold = self.cold_pull_profiles[material]
            self._run_blocking_gcode(
                "_COLDPULL_LOAD_MATERIAL TEMP=%g COLD=%g" % (hot, cold),
                "COLD PULL %s..." % material)
            session.phase = "cut"
        elif action == "extruder.prepared":
            self._prepare_extruder_calibration()
            return
        elif action == "extruder.nozzle_removed":
            session.nozzle_removed = True
            session.begin_measurement()
        elif action == "extruder.feed50":
            self._cold_extrusion_move(50)
            session.phase = "mark_first"
        elif action == "extruder.feed100":
            self._cold_extrusion_move(100)
            session.phase = "mark_second"
        elif action == "extruder.unload":
            self._cold_extrusion_move(-160)
            session.phase = "measure_ready"
        elif action == "extruder.unload_more":
            self._cold_extrusion_move(-50)
            session.phase = "measure_ready"
        elif action == "extruder.measure_ready":
            session.phase = "input"
        elif action.startswith("extruder.key."):
            self._append_extruder_input(action.rsplit(".", 1)[1])
            return
        elif action == "extruder.measure":
            session.set_measurement(session.input_text)
            session.phase = "warning" if session.suspicious else "result"
            if session.phase == "result":
                self._refresh_extruder_file_snapshot()
        elif action == "extruder.edit":
            session.phase = "input"
        elif action == "extruder.warning_accept":
            session.warning_acknowledged = True
            session.phase = "result"
            self._refresh_extruder_file_snapshot()
        elif action == "extruder.save_error.ok":
            session.save_error = None
            session.save_file_written = False
            session.phase = "result"
        elif action == "extruder.save":
            self._save_extruder_rotation(session.candidate)
            return
        elif action == "extruder.verify":
            self._set_extruder_runtime_rotation(session.candidate)
            session.apply_candidate_for_verification()
        elif action == "extruder.save_tested":
            self._save_extruder_rotation(session.current_rotation)
            return
        elif action == "extruder.refine":
            self._set_extruder_runtime_rotation(session.candidate)
            session.apply_candidate_for_verification()
        elif action == "extruder.stay":
            session.phase = session.exit_return_phase or (
                "saved" if session.saved else "load")
            session.exit_return_phase = None
        elif action == "extruder.exit":
            self._cancel_extruder_calibration(confirm=False)
            return
        elif action == "extruder.done":
            self._set_extruder_cooling_fan(False, best_effort=True)
            session.clear()
            self._show_page(Page.CALIBRATION_HOME)
            return
        else:
            return
        self._show_page(Page.EXTRUDER_CALIBRATION)
