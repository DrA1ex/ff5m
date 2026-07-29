## Lazy on-printer regression runner for the Feather display.
##
## This module is deliberately absent from Feather's normal import graph.  It
## is loaded only by the hidden _FEATHER_UI_TEST command.

import hashlib
import json
import logging
import math
import os
import queue
import re
import shutil
import struct
import threading
import time
from datetime import datetime

try:
    from .ui import Page, PrintState
    from .ff5m_ui.move import actions as move_actions
    from .ff5m_ui.z_offset import actions as z_actions
except (ImportError, ValueError):
    from ui import Page, PrintState
    from ff5m_ui.move import actions as move_actions
    from ff5m_ui.z_offset import actions as z_actions


ARTIFACT_ROOT = "/data/feather-ui-tests"
ACTIVE_MARKER = os.path.join(ARTIFACT_ROOT, "active.json")
FRAMEBUFFER = "/dev/fb0"
PRINTER_LOG = "/data/logFiles/printer.log"
SCREEN_WIDTH = 800
SCREEN_HEIGHT = 480
FRAME_BYTES = SCREEN_WIDTH * SCREEN_HEIGHT * 4
MAX_RUNS = 10
MAX_BYTES = 512 * 1024 * 1024
TAP_OPERATION_TIMEOUT = 1900.0
PERSISTENT_ACTIONS = frozenset((
    "cal.mesh.save", "cal.tuning.save", "z.save", "live_z.save",
    "live_z.save.yes", "mod.apply", "mod.save", "error.restart",
    "error.firmware_restart", z_actions.SAVE.wire_id,
    z_actions.SAFE_SAVE.wire_id,
))
VALID_SUITES = frozenset((
    "FULL", "UI", "RENDER", "MOTION", "HEAT", "SCREWS", "MESH", "Z",
))


def _jsonable(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return dict((str(key), _jsonable(item))
                    for key, item in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    name = getattr(value, "name", None)
    return str(name if name is not None else value)


def _atomic_json(path, value):
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(_jsonable(value), stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _directory_size(path):
    total = 0
    for root, _directories, files in os.walk(path):
        for filename in files:
            try:
                total += os.path.getsize(os.path.join(root, filename))
            except OSError:
                pass
    return total


class ArtifactWorker:
    """Perform framebuffer and artifact IO away from Klipper's reactor."""

    def __init__(self, reactor, run_directory, framebuffer=FRAMEBUFFER,
                 printer_log=PRINTER_LOG):
        self.reactor = reactor
        self.run_directory = run_directory
        self.framebuffer = framebuffer
        self.printer_log = printer_log
        self.tasks = queue.Queue()
        self.records = []
        self.log_start = self._file_size(printer_log)
        self.thread = threading.Thread(
            target=self._work, name="feather-ui-artifacts")
        self.thread.daemon = True
        self.thread.start()

    @staticmethod
    def _file_size(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    def _reactor_callback(self, callback, value):
        def deliver(_eventtime):
            callback(value)
        register = getattr(self.reactor, "register_async_callback", None)
        if register is not None:
            register(deliver)
        else:
            self.reactor.register_callback(deliver)

    def log(self, message):
        self.tasks.put(("log", str(message), None))

    def marker(self, value):
        self.tasks.put(("marker", value, None))

    def telemetry(self, name, fields, values):
        self.tasks.put(("telemetry", (name, fields, values), None))

    def capture(self, number, label, metadata, callback):
        self.tasks.put(("capture", (number, label, metadata), callback))

    def finish(self, summary, callback):
        self.tasks.put(("finish", summary, callback))

    def stop(self):
        self.tasks.put(("stop", None, None))

    def _work(self):
        while True:
            kind, payload, callback = self.tasks.get()
            try:
                if kind == "stop":
                    return
                if kind == "log":
                    self._append_log(payload)
                    continue
                if kind == "marker":
                    _atomic_json(ACTIVE_MARKER, payload)
                    continue
                if kind == "telemetry":
                    self._append_telemetry(*payload)
                    continue
                if kind == "capture":
                    value = self._capture(*payload)
                elif kind == "finish":
                    value = self._finish(payload)
                else:
                    raise RuntimeError("Unknown artifact task: %s" % kind)
            except Exception as exc:
                logging.exception("[feather_ui_test] artifact task failed")
                value = exc
            if callback is not None:
                self._reactor_callback(callback, value)

    def _append_log(self, message):
        with open(os.path.join(self.run_directory, "run.log"), "a",
                  encoding="utf-8") as stream:
            stream.write("%s %s\n" % (
                datetime.now().isoformat(timespec="milliseconds"), message))

    def _append_telemetry(self, name, fields, values):
        path = os.path.join(self.run_directory, "%s.csv" % name)
        new_file = not os.path.exists(path)
        with open(path, "a", encoding="utf-8") as stream:
            if new_file:
                stream.write(",".join(fields) + "\n")
            stream.write(",".join(str(values.get(field, ""))
                                  for field in fields) + "\n")

    def _read_frame(self):
        with open(self.framebuffer, "rb", buffering=0) as stream:
            data = stream.read(FRAME_BYTES)
        if len(data) != FRAME_BYTES:
            raise RuntimeError(
                "Framebuffer has %d bytes, expected %d" %
                (len(data), FRAME_BYTES))
        return data

    def _stable_frame(self):
        deadline = time.monotonic() + 1.0
        previous = None
        data = None
        while True:
            data = self._read_frame()
            digest = hashlib.sha256(data).hexdigest()
            if digest == previous:
                return data, digest
            if time.monotonic() >= deadline:
                return data, digest
            previous = digest
            time.sleep(0.05)

    @staticmethod
    def _bmp(data):
        size = 54 + len(data)
        header = struct.pack("<2sIHHI", b"BM", size, 0, 0, 54)
        header += struct.pack(
            "<IiiHHIIIIII", 40, SCREEN_WIDTH, -SCREEN_HEIGHT,
            1, 32, 0, len(data), 2835, 2835, 0, 0)
        return header + data

    def _capture(self, number, label, metadata):
        data, digest = self._stable_frame()
        if not any(data):
            raise RuntimeError("Framebuffer is blank")
        safe_label = "".join(
            char.lower() if char.isalnum() else "_" for char in label)
        safe_label = "_".join(filter(None, safe_label.split("_")))[:48]
        filename = "%03d-%s.bmp" % (number, safe_label or "screen")
        with open(os.path.join(self.run_directory, filename), "wb") as stream:
            stream.write(self._bmp(data))
        record = dict(metadata)
        record.update({
            "number": number, "label": label, "file": filename,
            "sha256": digest, "frame_bytes": len(data), "passed": True,
        })
        self.records.append(record)
        _atomic_json(os.path.join(self.run_directory, "manifest.json"),
                     self.records)
        return record

    def _copy_printer_log(self):
        if not os.path.isfile(self.printer_log):
            return None
        current_size = os.path.getsize(self.printer_log)
        offset = self.log_start if current_size >= self.log_start else 0
        target = os.path.join(self.run_directory, "printer.log")
        with open(self.printer_log, "rb") as source:
            source.seek(offset)
            with open(target, "wb") as destination:
                shutil.copyfileobj(source, destination, 64 * 1024)
        return os.path.basename(target)

    def _finish(self, summary):
        summary = dict(summary)
        summary["screenshots"] = len(self.records)
        summary["printer_log"] = self._copy_printer_log()
        _atomic_json(os.path.join(self.run_directory, "summary.json"), summary)
        try:
            os.unlink(ACTIVE_MARKER)
        except OSError:
            pass
        self._retain()
        return summary

    def _retain(self):
        root = os.path.dirname(self.run_directory)
        entries = []
        try:
            names = sorted(os.listdir(root))
        except OSError:
            return
        for name in names:
            path = os.path.join(root, name)
            if path == self.run_directory or not os.path.isdir(path):
                continue
            if re.match(
                    r"^\d{8}-\d{6}-\d{6}-(full|ui|render|motion|heat|screws|mesh|z)$",
                    name) is None:
                continue
            summary_path = os.path.join(path, "summary.json")
            if not os.path.isfile(summary_path):
                continue
            try:
                with open(summary_path, "r", encoding="utf-8") as stream:
                    outcome = json.load(stream).get("outcome")
            except Exception:
                outcome = "failed"
            entries.append({
                "path": path, "name": name, "size": _directory_size(path),
                "failed": outcome != "passed",
            })
        # Preserve the newest failed run for post-mortem inspection.
        newest_failed = next((item["path"] for item in reversed(entries)
                              if item["failed"]), None)
        total = sum(item["size"] for item in entries)
        while entries and (len(entries) + 1 > MAX_RUNS
                           or total + _directory_size(
                               self.run_directory) > MAX_BYTES):
            victim = next((item for item in entries
                           if item["path"] != newest_failed), None)
            if victim is None:
                break
            entries.remove(victim)
            total -= victim["size"]
            shutil.rmtree(victim["path"])


class UITestFeature:
    name = "ui_test"

    def __init__(self, host):
        self.host = host
        self.session_id = "%d-%.6f" % (os.getpid(), time.time())
        self.running = False
        self.dispatching_test_action = False
        self.abort_requested = False
        self.suite = None
        self.material = None
        self.phase = "idle"
        self.steps = []
        self.step_index = 0
        self.step_deadline = None
        self.gcmd = None
        self.run_id = None
        self.run_directory = None
        self.worker = None
        self.capture_number = 0
        self.failures = []
        self.started_at = None
        self.original = {}
        self.motion_origin = None
        self.motion_expected = None
        self.heat_initial = None
        self.heat_stable_since = None
        self._mesh_snapshot = None
        self.finalizing = False
        self._last_stage_status = None
        self.calibration_stages = []
        self.test_results = {}
        self.renderer_dropped = 0

    @property
    def reactor(self):
        return self.host.reactor

    @property
    def input_blocked(self):
        # Immediate emergency actions are checked before this product lock.
        return self.running

    @property
    def theme_update_blocked(self):
        return self.running

    def blocks_action(self, action):
        return self.running and str(action) in PERSISTENT_ACTIONS

    def handle_immediate_action(self, page, action):
        return False

    def update(self, eventtime):
        pass

    def on_print_status(self, status):
        status = str(status)
        if (not self.running or self.finalizing
                or status == self._last_stage_status
                or self.phase not in ("screws", "mesh", "z")):
            return
        self._last_stage_status = status
        self.calibration_stages.append({
            "time": time.time(), "phase": self.phase, "status": status,
        })
        self.capture_number += 1
        number = self.capture_number
        label = "%s-stage-%s" % (self.phase, status)
        try:
            metadata = self._screen_metadata()
        except Exception:
            logging.exception("[feather_ui_test] unable to snapshot stage")
            return
        self.worker.capture(number, label, metadata,
                            self._stage_capture_finished)

    def _stage_capture_finished(self, result):
        if isinstance(result, Exception):
            self._event("stage capture failed: %s" % result)

    def on_gcode_output(self, message):
        pass

    def on_print_state_changed(self, old_state, new_state, stats_state):
        if self.running and new_state != PrintState.IDLE:
            self.abort_requested = True

    def safety_active_reasons(self, eventtime):
        return (("ui-test",) if self.running and not self.finalizing
                and self.phase != "ui" else ())

    def safety_armed_reasons(self, page, eventtime):
        return ()

    def respond_status(self, gcmd):
        if not self.running:
            gcmd.respond_info("Feather UI test: idle")
            return
        gcmd.respond_info(
            "Feather UI test: suite=%s phase=%s step=%d/%d artifacts=%s" %
            (self.suite, self.phase, self.step_index, len(self.steps),
             self.run_directory))

    def abort(self, gcmd):
        if not self.running:
            gcmd.respond_info("Feather UI test: nothing to abort")
            return
        self.abort_requested = True
        if self._temperature_wait_active():
            try:
                self.host._run_immediate_command("M108")
            except Exception:
                logging.exception(
                    "[feather_ui_test] unable to interrupt temperature wait")
        gcmd.respond_info("Feather UI test abort requested: %s" % self.run_id)
        self._schedule(0.0)

    def run(self, gcmd, suite, material, confirm):
        if self.running:
            raise gcmd.error("Feather UI test is already running")
        if suite not in VALID_SUITES:
            raise gcmd.error("Unknown Feather UI test SUITE=%s" % suite)
        if confirm != 1:
            raise gcmd.error("Feather UI test requires CONFIRM=1")
        self._preflight(suite, hardware_targets=False)
        self.suite = suite
        self.material = self._resolve_material(material, suite)
        self.gcmd = gcmd
        self.started_at = time.time()
        self.abort_requested = False
        self.failures = []
        self.step_index = 0
        self.step_deadline = None
        self.capture_number = 0
        self.calibration_stages = []
        self.test_results = {}
        self.renderer_dropped = self.host.renderer.get_status()[
            "dropped_batches"]
        self._capture_original_state()
        self._recover_stale_marker()
        # Recovery may have restored a profile or runtime offset belonging to
        # the interrupted run.  That recovered state is the baseline the new
        # run must preserve, not the transient state observed before cleanup.
        self._capture_original_state()
        self._preflight(suite, hardware_targets=True)
        self.run_id = "%s-%s" % (
            datetime.now().strftime("%Y%m%d-%H%M%S-%f"), suite.lower())
        os.makedirs(ARTIFACT_ROOT, exist_ok=True)
        if shutil.disk_usage(ARTIFACT_ROOT).free < 64 * 1024 * 1024:
            raise gcmd.error("Less than 64 MiB is free below /data")
        self.run_directory = os.path.join(ARTIFACT_ROOT, self.run_id)
        os.makedirs(self.run_directory)
        _atomic_json(ACTIVE_MARKER, self._marker("starting"))
        _atomic_json(os.path.join(self.run_directory, "environment.json"),
                     self._environment())
        self.worker = ArtifactWorker(self.reactor, self.run_directory)
        self.steps = self._build_steps(suite)
        self.running = True
        self.finalizing = False
        self._last_stage_status = None
        self._event("run started suite=%s material=%s" %
                    (suite, self.material or "n/a"))
        gcmd.respond_info(
            "Feather UI test started: suite=%s artifacts=%s" %
            (suite, self.run_directory))
        self._schedule(0.0)

    def _preflight(self, suite, hardware_targets=True):
        if self.host.print_state != PrintState.IDLE:
            raise RuntimeError("Printer must be idle")
        state = str(self.host.print_stats.get_status(
            self.reactor.monotonic()).get("state", "")).lower()
        if state in ("printing", "paused"):
            raise RuntimeError("A print is active")
        if self.host.virtual_sdcard.is_active():
            raise RuntimeError("Virtual SD is active")
        if getattr(self.host, "command_depth", 0):
            raise RuntimeError("Another Feather command is active")
        if not getattr(self.host.renderer, "active", False):
            raise RuntimeError("Feather renderer is not active")
        status = self.host.toolhead.get_status(self.reactor.monotonic())
        if abs(float(status.get("velocity", 0.0) or 0.0)) > 0.0001:
            raise RuntimeError("Toolhead is moving")
        if hardware_targets and suite not in ("UI", "RENDER"):
            if suite in ("FULL", "MESH") and getattr(
                    self.host, "bed_mesh", None) is None:
                raise RuntimeError("Bed mesh is not configured")
            if suite in ("FULL", "SCREWS", "MESH", "Z") and getattr(
                    self.host, "probe", None) is None:
                raise RuntimeError("Probe is not configured")
            extruder = self.host.extruder.get_status(self.reactor.monotonic())
            bed = self.host.heater_bed.get_status(self.reactor.monotonic())
            if float(extruder.get("target", 0.0)) > 0.0 or float(
                    bed.get("target", 0.0)) > 0.0:
                raise RuntimeError("Turn heaters off before hardware tests")

    def _resolve_material(self, requested, suite):
        needs_material = suite in ("FULL", "HEAT", "MESH")
        if not needs_material:
            return requested or None
        materials = tuple(self.host.heating_materials)
        if not materials:
            raise RuntimeError("No heating materials are enabled")
        material = requested.upper() if requested else self.host._current_material()
        if material not in materials:
            material = materials[0] if not requested else material
        if material not in materials:
            raise RuntimeError("Unknown or inactive heating material: %s" % material)
        return material

    def _capture_original_state(self):
        now = self.reactor.monotonic()
        mesh = getattr(self.host, "bed_mesh", None)
        mesh_status = mesh.get_status(now) if mesh is not None else {}
        self.original = {
            "page": self.host.page,
            "previous_page": self.host.previous_page,
            "filament_material": self.host.filament_material,
            "runtime_z": float(self.host.gcode_move.get_status(now)[
                "homing_origin"][2]),
            "mesh_object": getattr(mesh, "z_mesh", None),
            "mesh_profile": str(mesh_status.get("profile_name", "") or ""),
        }

    def _marker(self, phase):
        return {
            "run_id": self.run_id, "pid": os.getpid(), "suite": self.suite,
            "session": self.session_id,
            "hardware": self.suite not in ("UI", "RENDER"),
            "phase": phase, "runtime_z": self.original.get("runtime_z", 0.0),
            "mesh_profile": self.original.get("mesh_profile", ""),
            "directory": self.run_directory, "updated_at": time.time(),
        }

    def _recover_stale_marker(self):
        try:
            with open(ACTIVE_MARKER, "r", encoding="utf-8") as stream:
                marker = json.load(stream)
        except (OSError, ValueError):
            return
        # A Klipper restart already cleared volatile heaters, motion, mesh and
        # runtime offsets.  Same-process recovery restores the recorded state.
        if (marker.get("session") == self.session_id
                and marker.get("hardware", True)):
            self.host._run_script("TURN_OFF_HEATERS")
            self.host._run_script("_SET_GCODE_OFFSET Z=%+.6f MOVE=0" %
                                  float(marker.get("runtime_z", 0.0)))
            profile = str(marker.get("mesh_profile", "") or "")
            self.host._run_script(
                "BED_MESH_PROFILE LOAD=%s" % profile
                if profile else "BED_MESH_CLEAR")
            self.host._run_script("M84")
        directory = marker.get("directory")
        if directory and os.path.isdir(directory):
            try:
                _atomic_json(os.path.join(directory, "summary.json"), {
                    "outcome": "interrupted", "recovered_at": time.time(),
                    "reason": "superseded by a new on-printer run",
                })
            except OSError:
                logging.exception(
                    "[feather_ui_test] unable to annotate stale run")
        try:
            os.unlink(ACTIVE_MARKER)
        except OSError:
            pass

    def _environment(self):
        now = self.reactor.monotonic()
        start_args = getattr(self.host.printer, "get_start_args", lambda: {})()
        return {
            "run_id": self.run_id, "suite": self.suite,
            "material": self.material, "pid": os.getpid(),
            "software_version": start_args.get("software_version"),
            "theme": getattr(self.host.renderer, "theme_name", None),
            "page": getattr(self.host.page, "name", str(self.host.page)),
            "heating_materials": self.host.heating_materials,
            "heating_profiles": self.host.heating_profiles,
            "current_material": self.host._current_material(),
            "print": self.host.print_stats.get_status(now),
            "toolhead": self.host.toolhead.get_status(now),
            "renderer": self.host.renderer.get_status(),
        }

    def _build_steps(self, suite):
        steps = []
        self._add_capture(steps, "baseline")
        phases = (["ui", "render", "motion", "heat", "screws", "mesh", "z"]
                  if suite == "FULL" else [suite.lower()])
        for phase in phases:
            getattr(self, "_steps_%s" % phase)(steps)
        return steps

    @staticmethod
    def _add_call(steps, label, callback, delay=0.15):
        steps.append({"kind": "call", "label": label,
                      "callback": callback, "delay": delay})

    @staticmethod
    def _add_tap(steps, action, page=None, label=None):
        steps.append({"kind": "tap", "action": action, "page": page,
                      "label": label or str(action)})

    @classmethod
    def _add_semantic_tap(cls, steps, action, page=None):
        """Tap the exact deterministic wire identity of a typed command."""
        cls._add_tap(
            steps, action.wire_id, page,
            label=str(action.key.value))

    @staticmethod
    def _add_tap_label(steps, button_label, page=None):
        steps.append({"kind": "tap_label", "button_label": button_label,
                      "page": page, "label": button_label})

    @staticmethod
    def _add_wait(steps, label, predicate, timeout, interval=0.5):
        steps.append({"kind": "wait", "label": label,
                      "predicate": predicate, "timeout": timeout,
                      "interval": interval})

    @staticmethod
    def _add_capture(steps, label):
        steps.append({"kind": "capture", "label": label})

    def _steps_ui(self, steps):
        self._add_call(steps, "ui-pause-timer", self._pause_ui_timer)
        self._add_call(steps, "ui-home-filled", self._render_filled_home)
        self._add_capture(steps, "ui-home-filled")
        self._add_call(steps, "ui-home", lambda: self._show(Page.IDLE_HOME))
        self._add_capture(steps, "ui-home")
        self._add_tap(steps, "nav.filament", Page.FILAMENT_MATERIAL)
        self._add_tap(steps, "nav.back", Page.IDLE_HOME)
        self._add_tap(steps, "nav.move", Page.CONTROL_MOVE)
        self._add_tap(steps, "nav.back", Page.IDLE_HOME)
        self._add_tap(steps, "nav.menu", Page.MAIN_MENU)
        self._add_capture(steps, "ui-main-menu")
        self._add_tap(steps, "nav.files", Page.FILE_BROWSER)
        self._add_capture(steps, "ui-files")
        self._add_call(steps, "ui-file-confirm", self._open_safe_file_confirm)
        self._add_capture(steps, "ui-file-confirm")
        self._add_call(steps, "ui-file-return", self._return_from_file_confirm)
        # The internal file browser belongs to the home screen, so its Back
        # action returns there rather than to the menu used to open it.
        self._add_tap(steps, "nav.back", Page.IDLE_HOME)
        self._add_tap(steps, "nav.menu", Page.MAIN_MENU)
        self._add_tap(steps, "nav.control", Page.CONTROL_HOME)
        self._add_capture(steps, "ui-control")
        self._add_tap(steps, "nav.move", Page.CONTROL_MOVE)
        self._add_capture(steps, "ui-move")
        self._add_tap(steps, "nav.back", Page.CONTROL_HOME)
        self._add_tap(steps, "nav.heat", Page.CONTROL_HEAT)
        self._add_capture(steps, "ui-heat")
        self._add_tap(steps, "nav.back", Page.CONTROL_HOME)
        self._add_tap(steps, "nav.calibration", Page.CALIBRATION_HOME)
        self._add_capture(steps, "ui-calibration")
        self._add_call(steps, "ui-calibration-pages",
                       self._render_calibration_variants)
        self._add_capture(steps, "ui-calibration-variants")
        self._add_tap(steps, "nav.back", Page.CONTROL_HOME)
        self._add_tap(steps, "nav.settings", Page.SETTINGS)
        self._add_capture(steps, "ui-settings")
        self._add_tap(steps, "settings.mod", Page.MOD_SETTINGS)
        self._add_capture(steps, "ui-mod-parameters")
        self._add_tap(steps, "nav.back", Page.SETTINGS)
        self._add_tap(steps, "nav.back", Page.CONTROL_HOME)
        self._add_tap(steps, "nav.back", Page.MAIN_MENU)
        self._add_tap(steps, "nav.filament", Page.FILAMENT_MATERIAL)
        self._add_capture(steps, "ui-filament-materials")
        self._add_call(steps, "ui-filament-action",
                       self._render_safe_filament_action)
        self._add_capture(steps, "ui-filament-action")
        self._add_call(steps, "ui-filament-return",
                       lambda: self._show(Page.FILAMENT_MATERIAL))
        self._add_tap(steps, "nav.back", Page.MAIN_MENU)
        self._add_tap(steps, "nav.network", Page.NETWORK_HOME)
        self._add_capture(steps, "ui-network")
        self._add_tap(steps, "nav.back", Page.MAIN_MENU)
        self._add_tap(steps, "nav.back", Page.IDLE_HOME)
        self._add_call(steps, "ui-resume-timer", self._resume_ui_timer)

    def _render_filled_home(self):
        """Render a worst-case dashboard without mutating printer hardware."""
        host = self.host

        class Status:
            def __init__(self, values):
                self.values = values

            def get_status(self, _eventtime):
                return dict(self.values)

        class VirtualSD:
            def is_active(self):
                return True

            def file_path(self):
                return "/data/THIS-IS-A-VERY-LONG-PRINT-FILENAME-FOR-UI.gcode"

        names = (
            "extruder", "heater_bed", "toolhead", "network_status",
            "last_job_name", "print_state", "print_stats", "virtual_sdcard",
            "_current_material", "_print_progress", "_print_time_values",
            "print_status_text", "_last_dashboard",
        )
        original = dict((name, getattr(host, name)) for name in names)
        try:
            host.extruder = Status({"temperature": 299.0, "target": 300.0})
            host.heater_bed = Status({"temperature": 129.0, "target": 130.0})
            host.toolhead = Status({"homed_axes": "xyz"})
            host.network_status = {
                "mode": "WIFI", "ssid": "VERY-LONG-WIRELESS-NETWORK-NAME",
                "signal": "100%", "ip": "255.255.255.255",
            }
            host.last_job_name = (
                "THIS-IS-A-VERY-LONG-PREVIOUS-PRINT-FILENAME.gcode")
            host.print_state = PrintState.PREPARING
            host.print_stats = Status({
                "state": "printing", "print_duration": 359999.0,
                "info": {"current_layer": 9999, "total_layer": 9999},
            })
            host.virtual_sdcard = VirtualSD()
            host._current_material = lambda: "CARBON-FIBER-POLYCARBONATE"
            host._print_progress = lambda _eventtime, _stats: 1.0
            host._print_time_values = (
                lambda _eventtime, _stats, _progress: (359999.0, 359999.0))
            host.print_status_text = "CALIBRATING AND PREPARING PRINT SURFACE"
            host._last_dashboard = None
            host._render_home()
        finally:
            for name, value in original.items():
                setattr(host, name, value)

    def _steps_motion(self, steps):
        self._add_call(steps, "motion-open", lambda: self._show(Page.CONTROL_MOVE))
        self._add_capture(steps, "motion-before-home")
        self._add_call(steps, "motion-caution", self._dismiss_move_caution)
        self._add_semantic_tap(
            steps, move_actions.HOME_ALL, Page.CONTROL_MOVE)
        self._add_call(steps, "motion-origin", self._save_motion_origin)
        self._add_capture(steps, "motion-homed")
        for axis in "xyz":
            self._add_call(steps, "motion-%s-forward" % axis,
                           lambda axis=axis: self._motion_step(axis, 1))
            self._add_capture(steps, "motion-%s-forward" % axis)
            self._add_call(steps, "motion-%s-return" % axis,
                           lambda axis=axis: self._motion_step(axis, -1))
            self._add_capture(steps, "motion-%s-return" % axis)
        self._add_call(steps, "motion-disable", lambda: self.host._run_script("M84"))

    def _steps_render(self, steps):
        before_status = self.host.renderer.get_status()
        before = before_status["typer_restarts"]
        before_error = before_status.get("worker_last_error", "")
        self._add_call(steps, "render-pause-timer", self._pause_ui_timer)
        self._add_call(
            steps, "render-restart-signal",
            lambda: self._request_renderer_restart(before))
        self._add_wait(
            steps, "render-recovered",
            lambda: self._renderer_recovered(before, before_error),
            15.0, 0.1)
        self._add_capture(steps, "render-recovered")
        self._add_call(steps, "render-resume-timer", self._resume_ui_timer)

    def _request_renderer_restart(self, before):
        if not self.host.renderer.restart():
            raise RuntimeError("Renderer restart signal was not accepted")

    def _renderer_recovered(self, before, before_error=""):
        status = self.host.renderer.get_status()
        return (status["worker_state"] == "running"
                and status["typer_restarts"] > before
                and status.get("worker_last_error", "") == before_error)

    def _steps_heat(self, steps):
        self._add_call(steps, "heat-open", lambda: self._show(Page.CONTROL_HEAT))
        self._add_call(steps, "heat-initial", self._save_heat_initial)
        self._add_capture(steps, "heat-cold")
        self._add_tap_label(steps, self.material, Page.CONTROL_HEAT)
        self._add_wait(steps, "heat-targets", self._heat_targets_set, 10.0)
        self._add_capture(steps, "heat-target-accepted")
        self._add_wait(steps, "heat-rising", self._heat_is_rising, 120.0, 1.0)
        self._add_capture(steps, "heat-rising")
        self._add_wait(steps, "heat-stable", self._heat_is_stable, 900.0, 2.0)
        self._add_capture(steps, "heat-target-reached")
        self._add_tap_label(steps, "COOLDOWN", Page.CONTROL_HEAT)
        self._add_wait(steps, "heat-off", self._heaters_off, 10.0)
        self._add_capture(steps, "heat-cooldown-start")

    def _steps_screws(self, steps):
        self._add_call(steps, "screws-open", self._open_calibration_home)
        self._add_tap(steps, "cal.screws", Page.CALIBRATION_CONFIRM)
        self._add_tap(steps, "cal.clean.skip", Page.CALIBRATION_CONFIRM)
        self._add_capture(steps, "screws-confirm")
        self._add_tap(steps, "cal.confirm", Page.CALIBRATION_PROGRESS)
        self._add_wait(steps, "screws-result", self._calibration_result, 1200.0, 1.0)
        self._add_capture(steps, "screws-result")
        self._add_tap(steps, "cal.done", Page.CALIBRATION_HOME)
        self._add_call(steps, "screws-cleanup", self._hardware_cleanup)

    def _steps_mesh(self, steps):
        self._add_call(steps, "mesh-snapshot", self._save_mesh_snapshot)
        self._add_call(steps, "mesh-open", self._open_calibration_home)
        self._add_tap(steps, "cal.mesh", Page.CALIBRATION_CONFIRM)
        self._add_capture(steps, "mesh-confirm")
        self._add_tap(steps, "cal.confirm", Page.CALIBRATION_PROGRESS)
        self._add_wait(steps, "mesh-result", self._calibration_result, 1800.0, 1.0)
        self._add_call(steps, "mesh-validate", self._validate_mesh)
        self._add_capture(steps, "mesh-result")
        self._add_tap(steps, "cal.mesh.discard", Page.CALIBRATION_HOME)
        self._add_call(steps, "mesh-restore", self._restore_mesh_snapshot)
        self._add_call(steps, "mesh-cleanup", self._hardware_cleanup)

    def _steps_z(self, steps):
        self._add_call(steps, "z-open", self._open_calibration_home)
        self._add_tap(steps, "cal.z", Page.CALIBRATION_CONFIRM)
        self._add_tap(steps, "cal.clean.skip", Page.CALIBRATION_CONFIRM)
        self._add_capture(steps, "z-confirm")
        self._add_tap(steps, "cal.confirm", Page.SAFE_Z_BRIEFING)
        self._add_capture(steps, "z-safe-briefing")
        self._add_semantic_tap(steps, z_actions.SAFE_SKIP)
        self._add_wait(steps, "z-preparation", self._z_summary_ready,
                       1200.0, 1.0)
        self._add_capture(steps, "z-summary-empty")
        self._add_semantic_tap(
            steps, z_actions.ZONE_ACTIONS["center"],
            Page.Z_OFFSET_PAPER_BRIEFING)
        self._add_capture(steps, "z-paper-briefing")
        self._add_semantic_tap(steps, z_actions.ENTER_ZONE)
        self._add_wait(steps, "z-positioned", self._z_paper_ready,
                       120.0, 0.5)
        self._add_capture(steps, "z-paper-before-probe")
        self._add_semantic_tap(
            steps, z_actions.PROBE, Page.Z_OFFSET_PAPER)
        self._add_call(steps, "z-dismiss-pressure", self._dismiss_pressure)
        self._add_call(steps, "z-probe-position", self._save_z_probe_position)
        self._add_capture(steps, "z-paper-probed")
        self._add_tap_label(steps, "0.100 MM", Page.Z_OFFSET_PAPER)
        # The real UI offers at most 0.100 mm. Ten FARTHER presses create the
        # requested 1 mm safety margin, then ten CLOSER presses return to the
        # post-probe point without ever crossing it.
        for _index in range(10):
            self._add_semantic_tap(
                steps, z_actions.FARTHER, Page.Z_OFFSET_PAPER)
        self._add_call(steps, "z-farther-verify", self._verify_z_farther)
        self._add_capture(steps, "z-paper-farther-1mm")
        for _index in range(10):
            self._add_semantic_tap(
                steps, z_actions.CLOSER, Page.Z_OFFSET_PAPER)
        self._add_call(steps, "z-return-verify", self._verify_z_return)
        self._add_capture(steps, "z-paper-returned")
        self._add_semantic_tap(steps, z_actions.ACCEPT)
        self._add_wait(steps, "z-summary-result", self._z_summary_ready,
                       120.0, 0.5)
        self._add_capture(steps, "z-summary-result")
        self._add_tap(steps, "nav.back", Page.Z_OFFSET_SUMMARY)
        self._add_capture(steps, "z-discard-dialog")
        self._add_semantic_tap(steps, z_actions.DISCARD_CONFIRM)
        self._add_wait(steps, "z-discarded", self._z_discarded,
                       120.0, 0.5)
        self._add_call(steps, "z-cleanup-verify", self._verify_z_cleanup)
        self._add_call(steps, "z-hardware-cleanup", self._hardware_cleanup)

    def _schedule(self, delay):
        self.reactor.register_callback(
            self._advance, self.reactor.monotonic() + max(0.0, delay))

    def _advance(self, eventtime):
        if not self.running:
            return
        if self.abort_requested:
            self._complete("aborted", "abort requested")
            return
        if self.step_index >= len(self.steps):
            self._complete("passed", None)
            return
        step = self.steps[self.step_index]
        self.phase = step["label"].split("-", 1)[0]
        try:
            if self.worker is not None:
                self.worker.marker(self._marker(self.phase))
            kind = step["kind"]
            if kind == "call":
                step["callback"]()
                self._step_passed(step, step.get("delay", 0.15))
            elif kind in ("tap", "tap_label"):
                action = (step.get("action") if kind == "tap"
                          else self._action_for_label(step["button_label"]))
                self._tap(action)
                self.reactor.register_callback(
                    lambda now, expected=step.get("page"), item=step:
                    self._after_tap(now, item, expected),
                    eventtime + 0.22)
            elif kind == "wait":
                if step["predicate"]():
                    self.step_deadline = None
                    self._step_passed(step, 0.05)
                else:
                    if self.step_deadline is None:
                        self.step_deadline = eventtime + step["timeout"]
                    if eventtime >= self.step_deadline:
                        raise RuntimeError("Timed out: %s" % step["label"])
                    self._schedule(step["interval"])
            elif kind == "capture":
                self._capture(step)
            else:
                raise RuntimeError("Unknown test step: %s" % kind)
        except Exception as exc:
            logging.exception("[feather_ui_test] step failed: %s", step["label"])
            self.failures.append({"step": step["label"], "error": str(exc)})
            self._event("FAILED %s: %s" % (step["label"], exc))
            self._complete("failed", str(exc))

    def _after_tap(self, eventtime, step, expected):
        if not self.running or self.finalizing:
            return
        try:
            if self.abort_requested:
                self._complete("aborted", "abort requested")
                return
            expected_seen = step.get("expected_page_seen", expected is None)
            if not expected_seen and self.host.page == expected:
                step["expected_page_seen"] = True
                expected_seen = True
            operation_active = (
                int(getattr(self.host, "command_depth", 0)) > 0
                or getattr(self.host, "busy_message", None) is not None)
            if operation_active:
                deadline = step.setdefault(
                    "operation_deadline",
                    eventtime + TAP_OPERATION_TIMEOUT)
                if eventtime >= deadline:
                    raise RuntimeError(
                        "Timed out waiting for action to finish: %s" %
                        step["label"])
                self.reactor.register_callback(
                    lambda now, target=expected, item=step:
                    self._after_tap(now, item, target),
                    eventtime + 0.1)
                return
            if not expected_seen:
                raise RuntimeError(
                    "Action %s did not reach page %s; stopped at %s" %
                    (step["label"], expected.name, self.host.page.name))
            self.step_index += 1
            self._event("PASS %s" % step["label"])
            self._schedule(0.02)
        except Exception as exc:
            self.failures.append({"step": step["label"], "error": str(exc)})
            self._complete("failed", str(exc))

    def _step_passed(self, step, delay, advance=True):
        if advance:
            self.step_index += 1
        self._event("PASS %s" % step["label"])
        if advance:
            self._schedule(delay)

    def _capture(self, step):
        renderer_status = self.host.renderer.get_status()
        target = step.setdefault(
            "render_target", renderer_status["submitted_batches"])
        settled = (renderer_status["rendered_batches"]
                   + renderer_status["coalesced_batches"]
                   + renderer_status["dropped_batches"])
        if renderer_status["dropped_batches"] > self.renderer_dropped:
            raise RuntimeError("Render batch dropped before capture")
        if settled < target:
            deadline = step.setdefault(
                "render_deadline", self.reactor.monotonic() + 6.0)
            if self.reactor.monotonic() >= deadline:
                raise RuntimeError("Renderer did not settle before capture")
            self._schedule(0.02)
            return
        self.renderer_dropped = renderer_status["dropped_batches"]
        self.capture_number += 1
        metadata = self._screen_metadata()
        self.worker.capture(
            self.capture_number, step["label"], metadata,
            lambda result, item=step: self._capture_finished(item, result))

    def _capture_finished(self, step, result):
        if not self.running or self.finalizing:
            return
        if isinstance(result, Exception):
            self.failures.append({"step": step["label"], "error": str(result)})
            self._complete("failed", str(result))
            return
        self.step_index += 1
        self._event("CAPTURE %s %s" % (step["label"], result["file"]))
        self._schedule(0.02)

    def _screen_metadata(self):
        renderer = self.host.renderer
        buttons = {}
        for action, spec in getattr(renderer, "_buttons", {}).items():
            buttons[str(action)] = {
                "x": spec[0], "y": spec[1], "width": spec[2],
                "height": spec[3], "label": str(spec[4]),
                "state": str(spec[5]),
            }
            if (spec[0] < 0 or spec[1] < 0 or spec[2] <= 0 or spec[3] <= 0
                    or spec[0] + spec[2] > SCREEN_WIDTH
                    or spec[1] + spec[3] > SCREEN_HEIGHT):
                raise RuntimeError("Invalid hitbox: %s" % action)
        hitboxes = {}
        for action, spec in getattr(renderer, "_hitboxes", {}).items():
            hitboxes[str(action)] = {
                "x": spec[0], "y": spec[1], "width": spec[2],
                "height": spec[3], "continuous": bool(spec[4]),
            }
            if (spec[0] < 0 or spec[1] < 0 or spec[2] <= 0 or spec[3] <= 0
                    or spec[0] + spec[2] > SCREEN_WIDTH
                    or spec[1] + spec[3] > SCREEN_HEIGHT):
                raise RuntimeError("Invalid hitbox: %s" % action)
        return {
            "time": time.time(), "phase": self.phase,
            "page": self.host.page.name,
            "generation": getattr(renderer, "generation", None),
            "buttons": buttons,
            "hitboxes": hitboxes,
            "toggles": _jsonable(getattr(renderer, "_toggles", {})),
            "renderer": renderer.get_status(),
            "temperatures": self._temperatures(),
            "position": self._position(),
        }

    def _tap(self, action):
        action = str(action)
        renderer = self.host.renderer
        interactive = (
            action in getattr(renderer, "_buttons", {})
            or action in getattr(renderer, "_toggles", {})
            or action in getattr(renderer, "_hitboxes", {}))
        if not interactive:
            raise RuntimeError("Button is absent or disabled: %s" % action)
        if action in PERSISTENT_ACTIONS:
            raise RuntimeError("Persistent action is forbidden: %s" % action)
        self.dispatching_test_action = True
        try:
            self.host._handle_touch_action(action)
        finally:
            self.dispatching_test_action = False

    def _action_for_label(self, label):
        matches = [action for action, spec in self.host.renderer._buttons.items()
                   if str(spec[4]).upper() == str(label).upper()]
        if len(matches) != 1:
            raise RuntimeError(
                "Expected one button labelled %s, found %d" %
                (label, len(matches)))
        return matches[0]

    def _show(self, page):
        self.host._show_page(page)
        if self.host.page != page:
            raise RuntimeError("Unable to show page %s" % page.name)

    def _render_calibration_variants(self):
        calibration = self.host.feature_manager.get("calibration")
        if "cal.next" in self.host.renderer._buttons:
            calibration._handle_calibration_action("cal.next")

    def _open_calibration_home(self):
        calibration = self.host.feature_manager.get("calibration")
        # UI coverage intentionally leaves the paginated catalog on its next
        # page. Hardware phases must be independent from that presentation
        # state and the three tested entries all live on page zero.
        calibration.calibration_page = 0
        self._show(Page.CALIBRATION_HOME)

    def _open_safe_file_confirm(self):
        entries = list(getattr(self.host, "file_entries", ()))
        for index, entry in enumerate(entries[:5]):
            if entry["directory"]:
                continue
            action = "file.item%d" % index
            if action not in self.host.renderer._buttons:
                continue
            self.dispatching_test_action = True
            try:
                self.host._dispatch_action(action)
            finally:
                self.dispatching_test_action = False
            return

    def _return_from_file_confirm(self):
        if self.host.page == Page.FILE_CONFIRM:
            self.host._show_page(Page.FILE_BROWSER)

    def _pause_ui_timer(self):
        timer = getattr(self.host, "timer", None)
        self.original["timer_active"] = timer is not None
        if timer is None:
            return
        self.reactor.unregister_timer(timer)
        self.host.timer = None

    def _resume_ui_timer(self):
        if (not self.original.get("timer_active")
                or getattr(self.host, "timer", None) is not None):
            return
        self.host.timer = self.reactor.register_timer(
            self.host._update, self.reactor.NOW)

    def _render_safe_filament_action(self):
        if not self.host.heating_materials:
            return
        self.host.filament_material = self.material or self.host.heating_materials[0]
        self._show(Page.FILAMENT_ACTION)

    def _dismiss_move_caution(self):
        buttons = self.host.renderer._buttons
        for action in (move_actions.CAUTION_AUTO.wire_id,
                       move_actions.CAUTION_DISMISS.wire_id,
                       move_actions.CAUTION_UNLOAD.wire_id):
            if action in buttons:
                # Caution actions do not move the toolhead. Use direct semantic
                # dispatch here so the next Home tap remains a separate step.
                self.dispatching_test_action = True
                try:
                    self.host._dispatch_action(action)
                finally:
                    self.dispatching_test_action = False
                break

    def _save_motion_origin(self):
        status = self.host.toolhead.get_status(self.reactor.monotonic())
        if not all(axis in str(status.get("homed_axes", "")) for axis in "xyz"):
            raise RuntimeError("Home All did not home XYZ")
        self.motion_origin = tuple(float(value) for value in status["position"][:3])
        self.host.jog_step = 1.0
        self.host._render_move()

    def _motion_step(self, axis, outward):
        status = self.host.toolhead.get_status(self.reactor.monotonic())
        index = "xyz".index(axis)
        current = float(status["position"][index])
        low, high = self.host._feather_move_limits(status)[index]
        if outward > 0:
            direction = 1 if high - current >= current - low else -1
            self.motion_expected = current + direction
        else:
            origin = self.motion_origin[index]
            # Homing may report a position a fraction beyond the configured UI
            # travel bound (for example Y=110.099675 with a 110.0 maximum).
            # MOVE_SAFE correctly clamps the return, so compare against that
            # reachable coordinate rather than the raw homing overshoot.
            target = max(low, min(high, origin))
            direction = 1 if target > current else -1
            self.motion_expected = target
        actions = {
            ("x", 1): move_actions.X_PLUS,
            ("x", -1): move_actions.X_MINUS,
            ("y", 1): move_actions.Y_PLUS,
            ("y", -1): move_actions.Y_MINUS,
            ("z", 1): move_actions.Z_PLUS,
            ("z", -1): move_actions.Z_MINUS,
        }
        action = actions[(axis, direction)].wire_id
        self.dispatching_test_action = True
        try:
            self.host._dispatch_action(action)
        finally:
            self.dispatching_test_action = False
        actual = float(self.host.toolhead.get_status(
            self.reactor.monotonic())["position"][index])
        if not math.isclose(actual, self.motion_expected, abs_tol=0.05):
            raise RuntimeError(
                "%s position %.3f, expected %.3f" %
                (axis.upper(), actual, self.motion_expected))

    def _save_heat_initial(self):
        values = self._temperatures()
        self.heat_initial = (values["nozzle"], values["bed"])
        self.heat_stable_since = None

    def _temperatures(self):
        now = self.reactor.monotonic()
        nozzle = self.host.extruder.get_status(now)
        bed = self.host.heater_bed.get_status(now)
        values = {
            "time": time.time(), "nozzle": float(nozzle["temperature"]),
            "nozzle_target": float(nozzle["target"]),
            "bed": float(bed["temperature"]),
            "bed_target": float(bed["target"]),
        }
        if self.worker is not None:
            self.worker.log("TEMPERATURE %s" % json.dumps(values, sort_keys=True))
            self.worker.telemetry(
                "temperatures",
                ("time", "nozzle", "nozzle_target", "bed", "bed_target"),
                values)
        return values

    def _position(self):
        status = self.host.toolhead.get_status(self.reactor.monotonic())
        values = [float(value) for value in status.get("position", ())[:3]]
        if self.worker is not None:
            self.worker.log("POSITION %s" % json.dumps(values))
            self.worker.telemetry(
                "positions", ("time", "x", "y", "z"),
                {"time": time.time(),
                 "x": values[0] if len(values) > 0 else "",
                 "y": values[1] if len(values) > 1 else "",
                 "z": values[2] if len(values) > 2 else ""})
        return values

    def _heat_targets_set(self):
        values = self._temperatures()
        nozzle, bed = self.host.heating_profiles[self.material]
        return (abs(values["nozzle_target"] - nozzle) <= 1.0
                and abs(values["bed_target"] - bed) <= 1.0)

    def _heat_is_rising(self):
        values = self._temperatures()
        return (values["nozzle"] >= self.heat_initial[0] + 3.0
                or abs(values["nozzle"] - values["nozzle_target"]) <= 5.0)

    def _heat_is_stable(self):
        values = self._temperatures()
        stable = (abs(values["nozzle"] - values["nozzle_target"]) <= 5.0
                  and abs(values["bed"] - values["bed_target"]) <= 3.0)
        now = self.reactor.monotonic()
        if not stable:
            self.heat_stable_since = None
            return False
        if self.heat_stable_since is None:
            self.heat_stable_since = now
        return now - self.heat_stable_since >= 10.0

    def _heaters_off(self):
        values = self._temperatures()
        return values["nozzle_target"] <= 0.0 and values["bed_target"] <= 0.0

    def _calibration_result(self):
        return self.host.page == Page.CALIBRATION_RESULT

    def _z_summary_ready(self):
        return self.host.page == Page.Z_OFFSET_SUMMARY

    def _z_paper_ready(self):
        return self.host.page == Page.Z_OFFSET_PAPER

    def _z_discarded(self):
        feature = self.host.feature_manager.get("z")
        return (self.host.page == Page.CALIBRATION_HOME
                and not feature.z_calibration.active)

    def _save_mesh_snapshot(self):
        mesh = self.host.bed_mesh
        status = mesh.get_status(self.reactor.monotonic())
        self._mesh_snapshot = (
            getattr(mesh, "z_mesh", None), str(status.get("profile_name", "") or ""))

    def _restore_mesh_snapshot(self):
        if self._mesh_snapshot is None:
            return
        feature = self.host.feature_manager.get("z")
        feature._restore_z_mesh(*self._mesh_snapshot)
        current = self.host.bed_mesh.get_status(
            self.reactor.monotonic()).get("profile_name", "")
        if str(current or "") != self._mesh_snapshot[1]:
            raise RuntimeError("Mesh profile was not restored")
        self._mesh_snapshot = None

    def _validate_mesh(self):
        calibration = self.host.feature_manager.get("calibration")
        matrix = calibration.calibration_mesh
        if not matrix or not matrix[0]:
            raise RuntimeError("Mesh result is empty")
        width = len(matrix[0])
        if any(len(row) != width for row in matrix):
            raise RuntimeError("Mesh result is not rectangular")
        if not all(math.isfinite(float(value))
                   for row in matrix for value in row):
            raise RuntimeError("Mesh contains a non-finite value")
        if "cal.mesh.discard" not in self.host.renderer._buttons:
            raise RuntimeError("Mesh result has no DON'T SAVE action")
        self.test_results["mesh"] = matrix

    def _dismiss_pressure(self):
        try:
            action = self._action_for_label("OK")
        except RuntimeError:
            return
        self.dispatching_test_action = True
        try:
            self.host._dispatch_action(action)
        finally:
            self.dispatching_test_action = False

    def _save_z_probe_position(self):
        feature = self.host.feature_manager.get("z")
        if not feature.z_calibration.ready_for_paper_test:
            raise RuntimeError("Z probe did not produce an adjustable result")
        self.z_probe_local = float(feature.z_calibration.local_z)
        self.test_results["z"] = {"post_probe_local_z": self.z_probe_local}

    def _verify_z_farther(self):
        current = float(self.host.feature_manager.get(
            "z").z_calibration.local_z)
        if not math.isclose(current, self.z_probe_local + 1.0, abs_tol=0.01):
            raise RuntimeError("Z FARTHER sequence did not create 1 mm clearance")
        self.test_results["z"]["farther_local_z"] = current

    def _verify_z_return(self):
        current = float(self.host.feature_manager.get(
            "z").z_calibration.local_z)
        if not math.isclose(current, self.z_probe_local, abs_tol=0.01):
            raise RuntimeError("Z CLOSER sequence did not return safely")
        self.test_results["z"]["returned_local_z"] = current

    def _verify_z_cleanup(self):
        feature = self.host.feature_manager.get("z")
        if feature.z_calibration.active:
            raise RuntimeError("Z calibration session remains active")
        current = float(self.host.gcode_move.get_status(
            self.reactor.monotonic())["homing_origin"][2])
        if not math.isclose(current, self.original["runtime_z"], abs_tol=0.001):
            raise RuntimeError("Runtime Z offset was not restored")

    def _temperature_wait_active(self):
        wait = getattr(self.host, "temperature_wait", None)
        return bool(wait is not None and getattr(
            wait, "variables", {}).get("active", False))

    def _hardware_cleanup(self):
        self.host._run_script("TURN_OFF_HEATERS")

    def _restore_state(self):
        first_error = None
        if self.suite not in ("UI", "RENDER"):
            try:
                z = self.host.feature_manager.peek("z")
                if z is not None and z.z_calibration.active:
                    z._cancel_z_calibration()
            except Exception as exc:
                first_error = exc
                logging.exception(
                    "[feather_ui_test] unable to cancel Z session")
            try:
                self.host._run_script("TURN_OFF_HEATERS")
                self.host._run_script("_SET_GCODE_OFFSET Z=%+.6f MOVE=0" %
                                      self.original["runtime_z"])
                feature = self.host.feature_manager.get("z")
                feature._restore_z_mesh(
                    self.original["mesh_object"],
                    self.original["mesh_profile"])
                self.host._run_script("M84")
            except Exception as exc:
                if first_error is None:
                    first_error = exc
                logging.exception("[feather_ui_test] cleanup failed")
        self.host.filament_material = self.original["filament_material"]
        try:
            self._resume_ui_timer()
        except Exception as exc:
            if first_error is None:
                first_error = exc
        try:
            self.host._show_page(self.original["page"])
            self.host.previous_page = self.original["previous_page"]
        except Exception as exc:
            if first_error is None:
                first_error = exc
        if first_error is not None:
            raise first_error

    def _complete(self, outcome, reason):
        if not self.running or self.finalizing:
            return
        self.finalizing = True
        try:
            self._restore_state()
        except Exception as exc:
            self.failures.append({"step": "cleanup", "error": str(exc)})
            outcome = "failed"
            reason = reason or ("cleanup failed: %s" % exc)
        summary = {
            "run_id": self.run_id, "suite": self.suite,
            "material": self.material, "outcome": outcome,
            "reason": reason, "failures": self.failures,
            "calibration_stages": self.calibration_stages,
            "test_results": self.test_results,
            "started_at": self.started_at, "finished_at": time.time(),
            "duration": time.time() - self.started_at,
        }
        self.phase = "finalizing"
        self.worker.finish(summary, self._finished)

    def _finished(self, result):
        outcome = "failed"
        if isinstance(result, Exception):
            message = "artifact finalization failed: %s" % result
        else:
            outcome = result.get("outcome", "failed")
            message = result.get("reason") or outcome
        gcmd = self.gcmd
        directory = self.run_directory
        screenshots = (0 if isinstance(result, Exception)
                       else result.get("screenshots", 0))
        self.running = False
        self.finalizing = False
        self.phase = "idle"
        self.gcmd = None
        if self.worker is not None:
            self.worker.stop()
        self.worker = None
        if gcmd is not None:
            gcmd.respond_info(
                "Feather UI test %s: %s; screenshots=%d; artifacts=%s" %
                (outcome, message, screenshots, directory))

    def _event(self, message):
        logging.info("[feather_ui_test] %s", message)
        if self.worker is not None:
            self.worker.log(message)

    def deactivate(self):
        self.abort_requested = True
        if self.worker is not None:
            self.worker.stop()
            self.worker = None
        self.running = False
