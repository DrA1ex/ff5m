## Reversible printer state and owned resources for Feather UI test runs.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import json
import logging
import math
import os
import re


_MISSING = object()
_FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
_CONTEXT_PRINT_FIELDS = (
    "nozzle_initial", "nozzle", "bed_initial", "bed", "flow_ratio",
    "pressure_advance", "retract_length", "retract_speed",
    "unretract_speed", "fan_speed",
)


def load_context_print_gcode(material):
    path = os.path.join(_FIXTURE_DIR, "context_print_profiles.json")
    try:
        with open(path, "r", encoding="utf-8") as stream:
            profiles = json.load(stream)
    except (OSError, ValueError) as exc:
        raise RuntimeError("Unable to load context-print profiles: %s" % exc)
    profile = profiles.get(material) if isinstance(profiles, dict) else None
    if not isinstance(profile, dict):
        raise RuntimeError(
            "No context-print fixture profile for material: %s" % material)
    missing = [field for field in _CONTEXT_PRINT_FIELDS
               if field not in profile]
    if missing:
        raise RuntimeError(
            "Context-print fixture profile %s is missing: %s" %
            (material, ", ".join(missing)))

    try:
        values = {
            "NOZZLE_INITIAL": float(profile["nozzle_initial"]),
            "NOZZLE": float(profile["nozzle"]),
            "BED_INITIAL": float(profile["bed_initial"]),
            "BED": float(profile["bed"]),
            "FLOW_PERCENT": float(profile["flow_ratio"]) * 100.0,
            "PRESSURE_ADVANCE": float(profile["pressure_advance"]),
            "RETRACT_LENGTH": float(profile["retract_length"]),
            "RETRACT_SPEED": float(profile["retract_speed"]),
            "UNRETRACT_SPEED": float(profile["unretract_speed"]),
            "FAN_PWM": round(float(profile["fan_speed"]) * 255.0),
        }
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "Context-print fixture profile %s is invalid: %s" %
            (material, exc))

    template_path = os.path.join(
        _FIXTURE_DIR, "context_recovery_open_box.gcode")
    with open(template_path, "r", encoding="utf-8") as stream:
        result = stream.read()
    for name, value in values.items():
        token = "@%s@" % name
        if token not in result:
            raise RuntimeError("Context-print fixture is missing %s" % token)
        result = result.replace(token, "%g" % value)
    if re.search(r"@[A-Z_]+@", result):
        raise RuntimeError("Context-print fixture has unresolved parameters")
    return result


def capture_print_tuning(host, eventtime):
    # Every value the fixture G-code overwrites has to come back. A completed
    # print ends with END_PRINT -> _STOP, which restores the flow factor, but
    # an interrupted run only cancels the file and resets the operation
    # contexts, and no macro restores pressure advance or firmware retraction
    # at all. Capture all of them here and let restore() replay them.
    extruder = host.extruder
    status = extruder.get_status(eventtime)
    pressure_advance = status.get(
        "pressure_advance", getattr(extruder, "pressure_advance", None))
    retraction = host.printer.lookup_object("firmware_retraction", None)
    if pressure_advance is None or retraction is None:
        raise RuntimeError(
            "Context-print fixture requires restorable pressure advance "
            "and firmware retraction")
    retract_status = retraction.get_status(eventtime)
    tuning = {
        "pressure_advance": pressure_advance,
        "retract_length": retract_status.get("retract_length"),
        "retract_speed": retract_status.get("retract_speed"),
        "unretract_extra_length": retract_status.get(
            "unretract_extra_length"),
        "unretract_speed": retract_status.get("unretract_speed"),
        "extrude_factor": host.gcode_move.get_status(eventtime).get(
            "extrude_factor"),
    }
    # M221 rejects a non-positive factor, so an unusable flow value must fail
    # before the fixture overwrites it rather than during cleanup.
    if (any(value is None or not math.isfinite(float(value))
            for value in tuning.values())
            or float(tuning["extrude_factor"]) <= 0.0):
        raise RuntimeError("Current print tuning cannot be restored safely")
    return dict((key, float(value)) for key, value in tuning.items())


def _restore_print_tuning(host, tuning):
    host._run_script(
        "SET_PRESSURE_ADVANCE ADVANCE=%g\n"
        "SET_RETRACTION RETRACT_LENGTH=%g RETRACT_SPEED=%g "
        "UNRETRACT_EXTRA_LENGTH=%g UNRETRACT_SPEED=%g\n"
        "M221 S%g" % (
            tuning["pressure_advance"], tuning["retract_length"],
            tuning["retract_speed"], tuning["unretract_extra_length"],
            tuning["unretract_speed"], tuning["extrude_factor"] * 100.0))


class PrinterStateSnapshot:
    """Capture and restore the product state temporarily changed by a run."""

    def __init__(self, page, previous_page, filament_material, runtime_z,
                 mesh_object, mesh_profile, extruder_target, bed_target,
                 fan_speed, timer_active):
        self.page = page
        self.previous_page = previous_page
        self.filament_material = filament_material
        self.runtime_z = runtime_z
        self.mesh_object = mesh_object
        self.mesh_profile = mesh_profile
        self.extruder_target = extruder_target
        self.bed_target = bed_target
        self.fan_speed = fan_speed
        self.timer_active = timer_active

    @classmethod
    def capture(cls, host, reactor):
        now = reactor.monotonic()
        mesh = getattr(host, "bed_mesh", None)
        mesh_status = mesh.get_status(now) if mesh is not None else {}
        extruder = host.extruder.get_status(now)
        heater_bed = host.heater_bed.get_status(now)
        fan = getattr(host, "fan", None)
        fan_status = fan.get_status(now) if fan is not None else {}
        return cls(
            host.page, host.previous_page, host.filament_material,
            float(host.gcode_move.get_status(now)["homing_origin"][2]),
            getattr(mesh, "z_mesh", None),
            str(mesh_status.get("profile_name", "") or ""),
            float(extruder.get("target", 0.0)),
            float(heater_bed.get("target", 0.0)),
            float(fan_status.get("speed", 0.0) or 0.0),
            getattr(host, "timer", None) is not None)

    def restore(self, host, reactor, hardware):
        first_error = None
        if hardware:
            try:
                z = host.feature_manager.peek("z")
                if z is not None and z.z_calibration.active:
                    z._cancel_z_calibration()
            except Exception as exc:
                first_error = exc
                logging.exception(
                    "[feather_ui_test] unable to cancel Z session")
            try:
                host._run_script("TURN_OFF_HEATERS")
                host._run_script(
                    "M104 S%.1f\nM140 S%.1f" % (
                        self.extruder_target, self.bed_target))
                if getattr(host, "fan", None) is not None:
                    host._run_script(
                        "SET_FAN_SPEED FAN=fanM106 SPEED=%.4f" %
                        self.fan_speed)
                host._run_script("_SET_GCODE_OFFSET Z=%+.6f MOVE=0" %
                                 self.runtime_z)
                host.feature_manager.get("z")._restore_z_mesh(
                    self.mesh_object, self.mesh_profile)
                host._run_script("M84")
            except Exception as exc:
                if first_error is None:
                    first_error = exc
                logging.exception("[feather_ui_test] cleanup failed")

        host.filament_material = self.filament_material
        try:
            if self.timer_active and getattr(host, "timer", None) is None:
                host.timer = reactor.register_timer(host._update, reactor.NOW)
        except Exception as exc:
            if first_error is None:
                first_error = exc
        try:
            host._show_page(self.page)
            host.previous_page = self.previous_page
        except Exception as exc:
            if first_error is None:
                first_error = exc
        if first_error is not None:
            raise first_error


class ContextTestFixture:
    """Own reversible mutations used by context material and print suites."""

    def __init__(self, host, reactor, run_id, material, changed=None,
                 print_gcode=None, saved_print_tuning=None):
        self.host = host
        self.reactor = reactor
        self.run_id = run_id
        self.material = material
        self.changed = changed
        self.print_gcode = print_gcode
        self.saved_print_tuning = saved_print_tuning
        self.files = []
        self.checkpoint = None
        self.saved_mod_params = {}
        self.saved_current_material = _MISSING
        self.params_store_guard = None
        self.client_macro = None
        self.client_idle_timeout = _MISSING
        self.idle_timeout = _MISSING
        self.file_browser = None

    def _notify_changed(self):
        if self.changed is not None:
            self.changed()

    def marker_state(self):
        return {
            "files": list(self.files),
            "checkpoint": self.checkpoint,
        }

    def _install_material_guard(self):
        params = getattr(self.host, "params", None)
        if params is None or self.params_store_guard is not None:
            return
        namespace = getattr(params, "__dict__", {})
        had_instance = "_store_value" in namespace
        instance_value = namespace.get("_store_value")
        original = params._store_value

        def store_value(param, value):
            if getattr(param, "key", None) != "current_material":
                return original(param, value)
            previous = params.variables[param.key]
            params.variables[param.key] = value
            return previous != value

        params._store_value = store_value
        self.params_store_guard = (
            params, had_instance, instance_value)

    def prepare_material(self):
        self._install_material_guard()
        params = getattr(self.host, "params", None)
        if params is not None and self.saved_current_material is _MISSING:
            self.saved_current_material = params.variables.get(
                "current_material")

    def prepare_print(self):
        self.prepare_material()
        params = getattr(self.host, "params", None)
        controlled = {
            "check_md5": 0,
            "disable_cleaning": False,
            "use_kamp": False,
            "print_leveling": False,
            "bed_mesh_validation": True,
            "bed_mesh_validation_clear": False,
            "disable_priming": True,
        }
        if params is not None:
            for key, value in controlled.items():
                if key in params.variables:
                    self.saved_mod_params[key] = params.variables[key]
                    params.variables[key] = value

        client = self.host.printer.lookup_object(
            "gcode_macro _CLIENT_VARIABLE", None)
        if client is not None:
            self.client_macro = client
            variables = getattr(client, "variables", {})
            self.client_idle_timeout = variables.get("idle_timeout")
            variables["idle_timeout"] = 2

        idle_timeout = getattr(self.host, "idle_timeout", None)
        self.idle_timeout = getattr(idle_timeout, "timeout", None)
        self._create_print_files()

    def _create_print_files(self):
        root = os.path.realpath(self.host.virtual_sdcard.sdcard_dirname)
        nozzle, bed = self.host._limited_preheat(self.material)
        safe_run = re.sub(r"[^a-zA-Z0-9_.-]", "-", self.run_id)
        paths = [
            os.path.join(root, "feather-context-%s-kamp.gcode" % safe_run),
            os.path.join(
                root, "feather-context-%s-recovery.gcode" % safe_run),
        ]
        kamp = (
            "; Feather operation-context runner fixture\n"
            "START_PRINT EXTRUDER_TEMP=%.0f BED_TEMP=%.0f FORCE_KAMP=1\n"
            "G90\nG1 X0 Y0 Z5 F6000\n"
            "G4 P500\nEND_PRINT\n") % (nozzle, bed)
        if self.print_gcode is None:
            raise RuntimeError("Context-print fixture profile is unavailable")
        payloads = [
            kamp,
            self.print_gcode,
        ]
        for path, payload in zip(paths, payloads):
            if os.path.exists(path):
                raise RuntimeError(
                    "Runner G-code path already exists: %s" %
                    os.path.basename(path))
            self.files.append(path)
            # Persist ownership before the first write so interruption at any
            # point cannot turn a partially-created file into an orphan.
            self._notify_changed()
            with open(path, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())

    def open_file(self, path, show_page, file_browser_page):
        path = os.path.realpath(path)
        root = os.path.realpath(self.host.virtual_sdcard.sdcard_dirname)
        if not os.path.isfile(path) or not path.startswith(root + os.sep):
            raise RuntimeError("Runner G-code file is unavailable")
        stat = os.stat(path)
        cache = getattr(self.host, "file_entry_cache", None)
        loaded_at = getattr(self.host, "file_entry_loaded_at", None)
        if (cache is not None and loaded_at is not None
                and self.file_browser is None):
            self.file_browser = {
                "cache_present": "internal" in cache,
                "cache": cache.get("internal"),
                "loaded_present": "internal" in loaded_at,
                "loaded": loaded_at.get("internal"),
                "entries": getattr(self.host, "file_entries", None),
                "source": getattr(self.host, "file_source", "internal"),
                "page": getattr(self.host, "file_page", 0),
            }
        entry = {
            "name": os.path.basename(path), "path": path,
            "directory": False, "size": stat.st_size,
            "mtime": stat.st_mtime,
        }
        if cache is not None and loaded_at is not None:
            self.host.file_scan_token = getattr(
                self.host, "file_scan_token", 0) + 1
            self.host.file_scan_loading = False
            self.host.file_scan_source = None
            cache["internal"] = [entry]
            loaded_at["internal"] = self.reactor.monotonic()
        self.host.file_page = 0
        self.host.file_source = "internal"
        self.host.selected_file = None
        self.host.file_entries = [entry]
        show_page(file_browser_page)

    def checkpoint_ready(self):
        resurrection = getattr(self.host, "resurrection", None)
        path = getattr(resurrection, "file_path", None)
        if not path or not os.path.isfile(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as stream:
                checkpoint = json.load(stream)
        except (OSError, ValueError):
            return False
        source = os.path.realpath(str(checkpoint.get("file_path", "")))
        expected = set(os.path.realpath(item) for item in self.files)
        if source not in expected:
            raise RuntimeError("Recovery checkpoint belongs to another file")
        if self.checkpoint != path:
            self.checkpoint = path
            self._notify_changed()
        return True

    def cancel_print(self):
        # Klipper's CANCEL_PRINT pairs the virtual SD reset with CLEAR_PAUSE.
        # Resetting the SD alone keeps pause_resume paused for the rest of the
        # session, and a leaked pause flag makes the next print's PAUSE a no-op
        # while the file keeps streaming.
        self.host.virtual_sdcard.do_cancel()
        self.host._run_script("CLEAR_PAUSE")

    def restore(self, suite):
        first_error = None
        if suite == "CONTEXT_PRINT":
            try:
                virtual_sdcard = self.host.virtual_sdcard
                file_path = getattr(
                    virtual_sdcard, "file_path", lambda: None)()
                if virtual_sdcard.is_active() or file_path:
                    self.cancel_print()
            except Exception as exc:
                first_error = exc
                logging.exception(
                    "[feather_ui_test] unable to cancel runner print")
        try:
            manager = getattr(self.host, "operation_context", None)
            if (manager is not None
                    and manager.get_status(
                        self.reactor.monotonic()).get("contexts")):
                self.host._run_script("_CONTEXT_RESET")
        except Exception as exc:
            if first_error is None:
                first_error = exc
            logging.exception(
                "[feather_ui_test] unable to reset operation contexts")

        params = getattr(self.host, "params", None)
        if params is not None:
            for key, value in self.saved_mod_params.items():
                params.variables[key] = value
            if self.saved_current_material is not _MISSING:
                params.variables["current_material"] = (
                    self.saved_current_material)
        if self.params_store_guard is not None:
            guarded, had_instance, instance_value = self.params_store_guard
            if had_instance:
                guarded._store_value = instance_value
            else:
                try:
                    del guarded.__dict__["_store_value"]
                except (AttributeError, KeyError):
                    pass

        if self.client_macro is not None:
            self.client_macro.variables["idle_timeout"] = (
                self.client_idle_timeout)
        if self.idle_timeout is not _MISSING and self.idle_timeout is not None:
            try:
                self.host._run_script(
                    "SET_IDLE_TIMEOUT TIMEOUT=%g" % float(self.idle_timeout))
            except Exception as exc:
                if first_error is None:
                    first_error = exc
                logging.exception(
                    "[feather_ui_test] unable to restore idle timeout")

        if suite == "CONTEXT_PRINT" and self.saved_print_tuning is not None:
            try:
                _restore_print_tuning(self.host, self.saved_print_tuning)
            except Exception as exc:
                if first_error is None:
                    first_error = exc
                logging.exception(
                    "[feather_ui_test] unable to restore print tuning")

        if self.file_browser is not None:
            cache = self.host.file_entry_cache
            loaded_at = self.host.file_entry_loaded_at
            if self.file_browser["cache_present"]:
                cache["internal"] = self.file_browser["cache"]
            else:
                cache.pop("internal", None)
            if self.file_browser["loaded_present"]:
                loaded_at["internal"] = self.file_browser["loaded"]
            else:
                loaded_at.pop("internal", None)
            self.host.file_entries = self.file_browser["entries"]
            self.host.file_source = self.file_browser["source"]
            self.host.file_page = self.file_browser["page"]

        owned_files = tuple(os.path.realpath(path) for path in self.files)
        for path in tuple(self.files):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                if first_error is None:
                    first_error = exc
                logging.exception(
                    "[feather_ui_test] unable to remove runner G-code")
        self.files = []

        resurrection = getattr(self.host, "resurrection", None)
        checkpoint = self.checkpoint
        candidate = getattr(resurrection, "file_path", None)
        if checkpoint is None and candidate and os.path.isfile(candidate):
            try:
                with open(candidate, "r", encoding="utf-8") as stream:
                    saved = json.load(stream)
                source = os.path.realpath(str(saved.get("file_path", "")))
                if source in owned_files:
                    checkpoint = candidate
            except (OSError, ValueError):
                pass
        if (checkpoint and resurrection is not None
                and checkpoint == getattr(resurrection, "file_path", None)):
            try:
                os.unlink(checkpoint)
            except FileNotFoundError:
                pass
            except OSError as exc:
                if first_error is None:
                    first_error = exc
                logging.exception(
                    "[feather_ui_test] unable to remove runner checkpoint")
            _reset_resurrection(resurrection)

        self.checkpoint = None
        self.saved_mod_params = {}
        self.saved_current_material = _MISSING
        self.params_store_guard = None
        self.client_macro = None
        self.client_idle_timeout = _MISSING
        self.idle_timeout = _MISSING
        self.file_browser = None
        self.print_gcode = None
        self.saved_print_tuning = None
        if first_error is not None:
            raise first_error


def recover_interrupted_context_resources(host, marker):
    """Remove only resources proven to belong to an interrupted runner."""
    resources = marker.get("resources") or {}
    if not resources:
        return
    root = os.path.realpath(host.virtual_sdcard.sdcard_dirname)
    safe_run = re.sub(
        r"[^a-zA-Z0-9_.-]", "-", str(marker.get("run_id", "")))
    prefix = "feather-context-%s-" % safe_run
    owned = []
    for candidate in resources.get("files", ()):
        path = os.path.realpath(str(candidate))
        if (path.startswith(root + os.sep)
                and os.path.basename(path).startswith(prefix)):
            owned.append(path)

    checkpoint = resources.get("checkpoint")
    resurrection = getattr(host, "resurrection", None)
    candidate = getattr(resurrection, "file_path", None)
    if checkpoint is None:
        checkpoint = candidate
    if checkpoint and resurrection is not None and checkpoint == candidate:
        try:
            with open(checkpoint, "r", encoding="utf-8") as stream:
                saved = json.load(stream)
            source = os.path.realpath(str(saved.get("file_path", "")))
        except (OSError, ValueError):
            source = None
        if source in owned:
            try:
                os.unlink(checkpoint)
            except FileNotFoundError:
                pass
            _reset_resurrection(resurrection)

    for path in owned:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def _reset_resurrection(resurrection):
    resurrection._checkpoint_cache = None
    resurrection._checkpoint_cache_loaded = False
    resurrection._pause_checkpoint_active = False
    resurrection._resume_pending = False
    state = getattr(resurrection, "state", None)
    state_type = type(state)
    if (hasattr(state_type, "IDLE")
            and hasattr(resurrection, "_change_state")):
        resurrection._change_state(state_type.IDLE)
