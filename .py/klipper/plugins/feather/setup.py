## Durable first-run setup decisions; calibration data stays with Klipper.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import collections
import copy
import hashlib
import json
import os
import tempfile


SETUP_VERSION = 1
INTRO_STEPS = ("welcome", "network", "theme", "done")

OptionGroup = collections.namedtuple("OptionGroup", "id label summary keys")

## Curated Mod Parameters, grouped by the question they answer for the user.
## The first group is what the wizard shows directly; the others are one tap
## away. Mod Parameters remains the complete list.
OPTION_GROUPS = (
    OptionGroup("essentials", "ESSENTIALS", "Tuning and start-up behaviour", (
        "tune_klipper", "tune_config", "load_zoffset", "check_md5")),
    OptionGroup("protection", "PROTECTION", "Recovery and sensors", (
        "power_loss_recovery", "filament_switch_sensor", "weight_check", "weight_check_mode")),
    OptionGroup("mesh", "BED MESH USE", "Leveling and mesh validation", (
        "print_leveling", "use_kamp", "bed_mesh_validation", "bed_mesh_validation_action",
        "bed_mesh_validation_z_offset_mode")),
    OptionGroup("preparation", "PRINT PREPARATION", "Priming and nozzle cleaning", (
        "disable_priming", "zclear", "clear_cooldown_temp")),
    OptionGroup("peripherals", "PERIPHERALS", "Camera, chamber light and screen sharing", (
        "camera", "screen_stream", "chamber_light", "chamber_light_mode")),
    OptionGroup("timelapse", "TIMELAPSE", "Capture settings and intervals", (
        "timelapse", "timelapse_park", "timelapse_final_frame", "timelapse_mode",
        "timelapse_every_layers", "timelapse_every_seconds", "timelapse_every_percent")),
    OptionGroup("display", "DISPLAY", "Brightness and ECO mode", (
        "backlight", "display_eco", "backlight_eco")),
    OptionGroup("finishing", "PRINT FINISHING", "Pause, parking and motor shutdown", (
        "pause_z_min", "park_dz", "stop_motor")),
    OptionGroup("feather", "FEATHER", "Appearance and Forge-X updates", (
        "feather_theme", "feather_status_bar", "mod_check_update", "mod_check_update_interval")),
)
GROUP_BY_ID = {group.id: group for group in OPTION_GROUPS}
ESSENTIALS = OPTION_GROUPS[0]

SETTING_KEYS = tuple(key for group in OPTION_GROUPS for key in group.keys)
EDITABLE_KEYS = frozenset(SETTING_KEYS)

## Options that only take effect after Klipper restarts. They are collected
## into one explicit Apply instead of restarting once per switch.
STAGED_KEYS = frozenset(("tune_klipper", "tune_config", "power_loss_recovery"))

## What Forge-X suggests for a new printer. A value that already matches is
## not advertised, so the wizard only marks what is still worth changing.
RECOMMENDED = {"tune_klipper": 1, "tune_config": 1, "load_zoffset": 1,
               "power_loss_recovery": 1, "filament_switch_sensor": 1}

## Each calibration keeps its own existing screens, result page and save.
## Setup only starts one and observes what it produced.
SAFE_Z_STEP = "z_offset"  # Keep the original checkpoint key.
CALIBRATION_STEPS = (SAFE_Z_STEP, "mesh", "shaper")

PENDING_STATES = (None, "applying", "settings", "running", "saving")


def config_fingerprint(config, step):
    name = "bed_mesh auto" if step == "mesh" else "input_shaper"
    values = config.get(name)
    if values is None:
        return None
    # SAVE_CONFIG rewrites indentation on continuation lines, including mesh points.
    values = {key: "\n".join(line.strip() for line in value.strip().splitlines())
              for key, value in values.items()}
    return hashlib.sha256(json.dumps({name: values}, sort_keys=True).encode("utf-8")).hexdigest()


def effective_config(status):
    result = {section: dict(values) for section, values in status["config"].items()}
    for section, values in status["save_config_pending_items"].items():
        if values is None:
            result.pop(section, None)
        else:
            result.setdefault(section, {}).update(values)
    return result


class SetupProgress:
    """One atomically replaced checkpoint, next to persistent Mod Parameters."""

    def __init__(self, filename, normalize=None):
        self.filename = filename
        self.normalize = normalize
        self.recovery_required = False
        self.data = {"completed_version": 0, "skipped": False, "intro": "welcome", "draft": {}, "applying_values": {},
                     "selections": {}, "time_zone": None,
                     "completed": [], "pending": None, "running_step": None, "expected": None, "error": ""}
        stored = None
        try:
            with open(filename, encoding="utf-8") as stream:
                stored = json.load(stream)
            if isinstance(stored, dict):
                # Older checkpoints kept a redundant copy of committed options.
                stored.pop("applied", None)
            self.data = self._validate(stored)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, KeyError):
            # Retain a readable completion marker even if another field is damaged.
            # An unreadable file never triggers automatic setup; manual review repairs it.
            version = stored.get("completed_version") if isinstance(stored, dict) else None
            if type(version) is int and version >= 0:
                self.data["completed_version"] = version
            self.recovery_required = True
            self.data["error"] = "Setup progress could not be read. Open setup again; printer settings were preserved."

    def _validate(self, stored):
        if not isinstance(stored, dict) or set(stored) - set(self.data):
            raise ValueError("Invalid setup checkpoint")
        candidate = dict(self.data, **stored)
        if (type(candidate["completed_version"]) is not int or candidate["completed_version"] < 0
                or type(candidate["skipped"]) is not bool
                or candidate["intro"] not in INTRO_STEPS
                or candidate["pending"] not in PENDING_STATES
                or not isinstance(candidate["error"], str)
                or (candidate["time_zone"] is not None and not isinstance(candidate["time_zone"], str))
                or candidate["running_step"] not in (None,) + CALIBRATION_STEPS):
            raise ValueError("Invalid setup checkpoint")
        if (candidate["pending"] in ("running", "saving")) != (candidate["running_step"] is not None):
            raise ValueError("Invalid running step")
        expected = candidate["expected"]
        if candidate["pending"] == "saving":
            if (candidate["running_step"] not in ("mesh", "shaper") or not isinstance(expected, str)
                    or len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected)):
                raise ValueError("Invalid pending calibration save")
        elif expected is not None:
            raise ValueError("Unexpected calibration save")
        for field, keys in (("draft", STAGED_KEYS), ("applying_values", STAGED_KEYS),
                            ("selections", EDITABLE_KEYS)):
            values = candidate[field]
            if not isinstance(values, dict) or not set(values).issubset(keys):
                raise ValueError("Unknown setup setting")
            for key, value in values.items():
                if type(value) not in (str, int, float, bool):
                    raise ValueError("Invalid setup value")
                if self.normalize is not None:
                    values[key] = self.normalize(key, value)
        completed = candidate["completed"]
        if (not isinstance(completed, list) or any(type(step) is not str for step in completed)
                or len(set(completed)) != len(completed)
                or not set(completed).issubset(CALIBRATION_STEPS)):
            raise ValueError("Invalid calibration record")
        return candidate

    def write(self, **changes):
        candidate = copy.deepcopy(self.data)
        candidate.update(changes)
        candidate = self._validate(candidate)
        folder = os.path.dirname(self.filename)
        fd, temporary = tempfile.mkstemp(prefix=".feather-setup-", dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(candidate, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.filename)
            directory = os.open(folder, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        self.data = candidate
        self.recovery_required = False

    @property
    def complete(self):
        return self.data["completed_version"] >= SETUP_VERSION

    def record_options(self, values, **changes):
        if not self.complete:
            selections = dict(self.data["selections"], **values)
            if selections != self.data["selections"]:
                changes["selections"] = selections
        if changes:
            self.write(**changes)

    def record(self, step, done):
        """Record an explicit calibration save or cancellation."""
        completed = [name for name in CALIBRATION_STEPS
                     if name in self.data["completed"] or (done and name == step)]
        self.write(completed=completed, pending=None, running_step=None, expected=None, error="")

    def finish(self):
        self.write(completed_version=SETUP_VERSION, skipped=False, intro="done", draft={}, applying_values={},
                   pending=None, running_step=None, expected=None, error="")
