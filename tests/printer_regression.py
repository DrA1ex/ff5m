## Host-orchestrated Feather printer regression runs and local reports.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Run Feather printer suites while recording and reporting on the host.

Importing this module never contacts a printer or starts FFmpeg.  All mutable
work begins only from :func:`main` or an explicit :class:`RegressionRun`.
"""

import argparse
import csv
import datetime
import html
import json
import math
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.parse

from tests.printer_connection import (
    ARTIFACT_ROOT,
    PrinterConnection,
    PrinterConnectionError,
)


ROOT = pathlib.Path(__file__).parents[1]
DEFAULT_ARTIFACT_ROOT = ROOT / "tests" / "artifacts" / "printer-runs"
ACTIVE_MARKER = ARTIFACT_ROOT + "/active.json"
SAFE_RUN_ID = re.compile(
    r"^\d{8}-\d{6}-\d{6}-(full|ui|component|render|motion|heat|"
    r"screws|mesh|z|context_print|context_material)$")
SAFE_MATERIAL = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

SUITES = {
    "core": {"printer_suite": "FULL", "confirm": 1, "physical": True},
    "print": {
        "printer_suite": "CONTEXT_PRINT", "confirm": 2,
        "physical": True,
    },
    "material": {
        "printer_suite": "CONTEXT_MATERIAL", "confirm": 2,
        "physical": True,
    },
    "ui": {"printer_suite": "UI", "confirm": 1, "physical": False},
    "component": {
        "printer_suite": "COMPONENT", "confirm": 1, "physical": False,
    },
    "render": {
        "printer_suite": "RENDER", "confirm": 1, "physical": False,
    },
    "motion": {
        "printer_suite": "MOTION", "confirm": 1, "physical": True,
    },
    "heat": {"printer_suite": "HEAT", "confirm": 1, "physical": True},
    "screws": {
        "printer_suite": "SCREWS", "confirm": 1, "physical": True,
    },
    "mesh": {"printer_suite": "MESH", "confirm": 1, "physical": True},
    "z": {"printer_suite": "Z", "confirm": 1, "physical": True},
}
# Order is a physical constraint, not a preference: "print" leaves a real model
# in the bed centre, so it must follow every bed-probing suite. "material"
# cannot be chained after it. The suites run back to back with no operator
# stop, the printer drops the motors shortly after a print ends, and
# "material" re-homes and then travels to the bed centre and purges 100 mm of
# filament there - onto the model, at whatever Z homing left. Run "material"
# separately once the bed has been cleared.
ALL_SUITES = ("core", "print")
MATERIAL_SUITES = frozenset((
    "FULL", "HEAT", "MESH", "CONTEXT_PRINT", "CONTEXT_MATERIAL",
))
ARTIFACT_LINKS = (
    "summary.json", "manifest.json", "environment.json", "run.log",
    "printer.log", "temperatures.csv", "positions.csv",
    "reactor.csv", "artifact_timing.csv", "operation_context.json",
)
TELEMETRY_FILE = "telemetry.jsonl"
RESOURCE_FILE = "resources.tsv"
RESOURCE_MONITOR_SCRIPT = "/opt/config/mod/.shell/printer_resource_monitor.sh"
SAFE_MONITOR_ID = re.compile(r"^[0-9]+-[0-9]+$")
EVENT_STALL_TIMEOUT = 30.0
PROGRESS_INTERVAL = 10.0
LINUX_USER_HZ = 100.0
TELEMETRY_QUERY = (
    "/printer/objects/query?toolhead=homed_axes,position,print_time,"
    "estimated_print_time,stalls"
    "&motion_report=live_position,live_velocity,live_extruder_velocity"
    "&extruder=temperature,target,power"
    "&heater_bed=temperature,target,power"
    "&print_stats=state,filename&virtual_sdcard=progress"
    "&pause_resume=is_paused"
    "&mcu=last_stats"
    "&feather_screen=page,generation,context_path,context_types,"
    "current_state,ui_test"
)
FONT_5X7 = {
    " ": (0, 0, 0, 0, 0, 0, 0),
    "?": (14, 17, 1, 2, 4, 0, 4),
    "A": (14, 17, 17, 31, 17, 17, 17),
    "B": (30, 17, 17, 30, 17, 17, 30),
    "C": (14, 17, 16, 16, 16, 17, 14),
    "D": (30, 17, 17, 17, 17, 17, 30),
    "E": (31, 16, 16, 30, 16, 16, 31),
    "F": (31, 16, 16, 30, 16, 16, 16),
    "G": (14, 17, 16, 23, 17, 17, 14),
    "H": (17, 17, 17, 31, 17, 17, 17),
    "I": (14, 4, 4, 4, 4, 4, 14),
    "J": (7, 2, 2, 2, 18, 18, 12),
    "K": (17, 18, 20, 24, 20, 18, 17),
    "L": (16, 16, 16, 16, 16, 16, 31),
    "M": (17, 27, 21, 21, 17, 17, 17),
    "N": (17, 25, 21, 19, 17, 17, 17),
    "O": (14, 17, 17, 17, 17, 17, 14),
    "P": (30, 17, 17, 30, 16, 16, 16),
    "Q": (14, 17, 17, 17, 21, 18, 13),
    "R": (30, 17, 17, 30, 20, 18, 17),
    "S": (15, 16, 16, 14, 1, 1, 30),
    "T": (31, 4, 4, 4, 4, 4, 4),
    "U": (17, 17, 17, 17, 17, 17, 14),
    "V": (17, 17, 17, 17, 17, 10, 4),
    "W": (17, 17, 17, 21, 21, 21, 10),
    "X": (17, 17, 10, 4, 10, 17, 17),
    "Y": (17, 17, 10, 4, 4, 4, 4),
    "Z": (31, 1, 2, 4, 8, 16, 31),
    "0": (14, 17, 19, 21, 25, 17, 14),
    "1": (4, 12, 4, 4, 4, 4, 14),
    "2": (14, 17, 1, 2, 4, 8, 31),
    "3": (30, 1, 1, 14, 1, 1, 30),
    "4": (2, 6, 10, 18, 31, 2, 2),
    "5": (31, 16, 16, 30, 1, 1, 30),
    "6": (14, 16, 16, 30, 17, 17, 14),
    "7": (31, 1, 2, 4, 8, 8, 8),
    "8": (14, 17, 17, 14, 17, 17, 14),
    "9": (14, 17, 17, 15, 1, 1, 14),
    "-": (0, 0, 0, 31, 0, 0, 0),
    "_": (0, 0, 0, 0, 0, 0, 31),
    ".": (0, 0, 0, 0, 0, 6, 6),
    ":": (0, 6, 6, 0, 6, 6, 0),
    "/": (1, 2, 2, 4, 8, 8, 16),
    "|": (4, 4, 4, 4, 4, 4, 4),
    ">": (16, 8, 4, 2, 4, 8, 16),
    "<": (1, 2, 4, 8, 4, 2, 1),
    "+": (0, 4, 4, 31, 4, 4, 0),
    "=": (0, 0, 31, 0, 31, 0, 0),
    "%": (25, 25, 2, 4, 8, 19, 19),
    "(": (2, 4, 8, 8, 8, 4, 2),
    ")": (8, 4, 2, 2, 2, 4, 8),
}


class RegressionError(RuntimeError):
    pass


def selected_suites(selection):
    requested = (selection,) if isinstance(selection, str) else tuple(selection)
    if not requested:
        raise RegressionError("at least one host suite is required")

    names = []
    seen = set()
    for name in requested:
        if name == "all":
            expanded = ALL_SUITES
        elif name in SUITES:
            expanded = (name,)
        else:
            raise RegressionError("unknown host suite: %s" % name)
        for item in expanded:
            if item not in seen:
                seen.add(item)
                names.append(item)

    physical = [item for item in names if SUITES[item]["physical"]]
    if "material" in physical and len(physical) > 1:
        raise RegressionError(
            "material must run without another physical host suite")
    if "print" in names:
        after_print = names[names.index("print") + 1:]
        if any(SUITES[item]["physical"] for item in after_print):
            raise RegressionError(
                "print must be the final physical host suite")
    return [dict(SUITES[item], name=item) for item in names]


def resolve_camera_url(connection, value):
    value = str(value or "").strip()
    if not value:
        raise RegressionError("camera has no stream URL")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme:
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise RegressionError("camera stream URL is unsupported")
        return value
    return urllib.parse.urljoin(connection.web_base_url, value)


def camera_metadata(camera):
    """Return report-safe metadata without URLs or opaque extra data."""
    return {
        key: camera.get(key)
        for key in (
            "name", "location", "service", "enabled", "target_fps",
            "rotation", "flip_horizontal", "flip_vertical", "aspect_ratio",
        )
    }


def _finite_number(value, default=None, digits=3):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return round(number, digits)


def _position(status):
    value = status.get("live_position", status.get("position", ()))
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    result = [_finite_number(item) for item in value[:3]]
    return result if all(item is not None for item in result) else None


class TelemetryRecorder:
    """Append bounded host observations to one durable RT timeline."""

    def __init__(self, output, rate_hz, snapshot, clock=None,
                 wall_clock=None):
        self.output = pathlib.Path(output).resolve()
        self.path = self.output / TELEMETRY_FILE
        self.rate_hz = float(rate_hz)
        self.snapshot = snapshot
        self.clock = clock or time.monotonic
        self.wall_clock = wall_clock or time.time
        self.origin = None
        self.stream = None
        self.sample_count = 0
        self.failure_count = 0
        self.first_offset = None
        self.last_offset = None
        self.last_success_at = None
        self.latest_test_status = None
        self.expected_run_id = None
        self.expected_after_sample = 0
        self.stop_event = None
        self.thread = None

    def start(self, origin, test_status=None):
        self.origin = float(origin)
        self.last_success_at = self.clock()
        if not self.rate_hz:
            return
        try:
            self.stream = self.path.open("w", encoding="utf-8")
        except OSError:
            self.failure_count += 1
            return
        if test_status is not None:
            self.stop_event = threading.Event()
            self.thread = threading.Thread(
                target=self._collect, args=(test_status,),
                name="ff5m-rt-telemetry", daemon=True)
            self.thread.start()

    def _collect(self, test_status):
        interval = 1.0 / self.rate_hz
        next_sample = self.clock()
        while not self.stop_event.is_set():
            self.sample(test_status())
            next_sample += interval
            now = self.clock()
            if next_sample <= now:
                next_sample = now
            self.stop_event.wait(max(0.0, next_sample - now))

    @staticmethod
    def _heater(status):
        return {
            "temperature": _finite_number(status.get("temperature")),
            "target": _finite_number(status.get("target")),
            "power": _finite_number(status.get("power")),
        }

    @staticmethod
    def _buffer(toolhead):
        # "Timer too close" fires when the host stops feeding the MCU ahead of
        # time, so the only host-side number that describes how close the run
        # came to that is the lookahead margin: print_time is how far the queue
        # is planned to, estimated_print_time is where the MCU clock is now.
        # Klipper shuts the MCU down when the margin runs out, and `stalls`
        # counts the lookahead already having run dry.  Both are idle-valued
        # between moves, hence recorded raw instead of interpreted here.
        planned = _finite_number(toolhead.get("print_time"))
        elapsed = _finite_number(toolhead.get("estimated_print_time"))
        margin = None
        if planned is not None and elapsed is not None:
            margin = round(planned - elapsed, 6)
        return {
            "print_time": planned,
            "estimated_print_time": elapsed,
            "margin": margin,
            "stalls": _finite_number(toolhead.get("stalls")),
        }

    @staticmethod
    def _mcu(mcu):
        # mcu.last_stats is the MCU's own view of the same overload: mcu_awake
        # and mcu_task_avg say how loaded the microcontroller was, srtt and
        # bytes_retransmit say whether the serial link was the bottleneck
        # instead.  The object is absent on hosts that expose no [mcu] status,
        # so a missing block is recorded as None rather than treated as an
        # invalid snapshot.
        stats = mcu.get("last_stats") if isinstance(mcu, dict) else None
        if not isinstance(stats, dict):
            return None
        return {
            "awake": _finite_number(stats.get("mcu_awake")),
            "task_avg": _finite_number(stats.get("mcu_task_avg")),
            "bytes_retransmit": _finite_number(stats.get("bytes_retransmit")),
            "srtt": _finite_number(stats.get("srtt")),
        }

    def _record(self, observed, test):
        toolhead = observed.get("toolhead", {})
        motion = observed.get("motion_report", {})
        extruder = observed.get("extruder", {})
        bed = observed.get("heater_bed", {})
        print_stats = observed.get("print_stats", {})
        virtual_sd = observed.get("virtual_sdcard", {})
        pause_resume = observed.get("pause_resume", {})
        screen = observed.get("feather_screen", {})
        for value in (toolhead, motion, extruder, bed, print_stats,
                      virtual_sd, pause_resume, screen):
            if not isinstance(value, dict):
                raise ValueError("printer telemetry status is invalid")
        ui_test = screen.get("ui_test", {})
        if not isinstance(ui_test, dict):
            ui_test = {}
        test_status = {
            "host_suite": test.get("host_suite"),
            "printer_suite": test.get("printer_suite"),
            "running": bool(ui_test.get("running", False)),
            "finalizing": bool(ui_test.get("finalizing", False)),
            "run_id": ui_test.get("run_id"),
            "suite": ui_test.get("suite"),
            "phase": ui_test.get("phase"),
            "step": ui_test.get("step"),
            "step_index": ui_test.get("step_index"),
            "step_count": ui_test.get("step_count"),
        }
        context_path = screen.get("context_path", ())
        context_types = screen.get("context_types", ())
        if not isinstance(context_path, (list, tuple)):
            context_path = ()
        if not isinstance(context_types, (list, tuple)):
            context_types = ()
        position = _position(motion) or _position(toolhead)
        return {
            "time": round(float(self.wall_clock()), 6),
            "offset": round(max(0.0, self.clock() - self.origin), 6),
            "test": test_status,
            "page": str(screen.get("page") or "UNKNOWN"),
            "generation": screen.get("generation"),
            "print_state": str(print_stats.get("state") or "unknown"),
            "print_paused": bool(pause_resume.get("is_paused", False)),
            "print_file": str(print_stats.get("filename") or ""),
            "print_progress": _finite_number(
                virtual_sd.get("progress"), default=0.0),
            "position": position,
            "homed_axes": str(toolhead.get("homed_axes") or "").upper(),
            "velocity": _finite_number(motion.get("live_velocity")),
            "extruder_velocity": _finite_number(
                motion.get("live_extruder_velocity")),
            "nozzle": self._heater(extruder),
            "bed": self._heater(bed),
            "buffer": self._buffer(toolhead),
            "mcu": self._mcu(observed.get("mcu", {})),
            "context": {
                "path": [str(item) for item in context_path],
                "types": [str(item) for item in context_types],
                "state": screen.get("current_state"),
            },
        }

    def sample(self, test):
        if not self.rate_hz or self.stream is None:
            return
        try:
            record = self._record(self.snapshot(), dict(test or {}))
            self.stream.write(json.dumps(
                record, sort_keys=True, separators=(",", ":")) + "\n")
            self.stream.flush()
        except (OSError, TypeError, ValueError, PrinterConnectionError):
            self.failure_count += 1
            return
        self.sample_count += 1
        self.last_success_at = self.clock()
        self.latest_test_status = record["test"]
        self.first_offset = (record["offset"] if self.first_offset is None
                             else self.first_offset)
        self.last_offset = record["offset"]

    def events_alive(self):
        if not self.rate_hz:
            return True
        reference = self.last_success_at
        return (reference is not None
                and self.clock() - reference <= EVENT_STALL_TIMEOUT)

    def expect_run(self, run_id):
        self.expected_run_id = str(run_id)
        self.expected_after_sample = self.sample_count

    def run_state(self, run_id):
        expected = str(run_id)
        if self.expected_run_id != expected:
            return "pending"
        status = self.latest_test_status
        if status is None or self.sample_count <= self.expected_after_sample:
            return "pending"
        if status.get("running") or status.get("finalizing"):
            return ("active" if status.get("run_id") == expected
                    else "changed")
        return "finished"

    def finish(self):
        if self.stop_event is not None:
            self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2.0)
            if self.thread.is_alive():
                self.failure_count += 1
            self.thread = None
            self.stop_event = None
        if self.stream is not None:
            try:
                self.stream.close()
            except OSError:
                self.failure_count += 1
            self.stream = None
        if not self.rate_hz:
            status = "disabled"
        elif not self.sample_count:
            status = "unavailable"
        elif self.failure_count:
            status = "partial"
        else:
            status = "recorded"
        span = ((self.last_offset or 0.0) - (self.first_offset or 0.0))
        effective_rate = (
            (self.sample_count - 1) / span
            if self.sample_count > 1 and span > 0.0 else 0.0)
        return {
            "status": status,
            "rate_hz": self.rate_hz,
            "effective_rate_hz": round(effective_rate, 3),
            "sample_count": self.sample_count,
            "failure_count": self.failure_count,
            "file": TELEMETRY_FILE if self.sample_count else None,
        }


def _utc_time(epoch):
    return datetime.datetime.fromtimestamp(
        epoch, datetime.timezone.utc).isoformat(timespec="milliseconds")


def _safe_remote_run(marker, expected_suite=None):
    if not isinstance(marker, dict):
        raise RegressionError("printer active marker is invalid")
    run_id = str(marker.get("run_id") or "")
    suite = str(marker.get("suite") or "")
    directory = str(marker.get("directory") or "")
    if not SAFE_RUN_ID.match(run_id):
        raise RegressionError("printer returned an unsafe run id")
    if directory != ARTIFACT_ROOT + "/" + run_id:
        raise RegressionError("printer returned an unsafe run path")
    if expected_suite is not None and suite != expected_suite:
        raise RegressionError("printer started an unexpected suite")
    return run_id, directory


class PrinterRunClient:
    """Perform the concrete remote lifecycle for one launched runner."""

    def __init__(self, connection, clock=None, sleeper=None):
        self.connection = connection
        self.clock = clock or time.monotonic
        self.sleeper = sleeper or time.sleep

    def _ui_test_status(self):
        value = self.connection.request_json(
            "GET", "/printer/objects/query?feather_screen=ui_test",
            timeout=0.5)
        result = value.get("result")
        status = result.get("status") if isinstance(result, dict) else None
        screen = status.get("feather_screen") \
            if isinstance(status, dict) else None
        if not isinstance(screen, dict):
            raise RegressionError("Feather test status is unavailable")
        ui_test = screen.get("ui_test")
        if ui_test is None:
            return {"running": False}
        if (not isinstance(ui_test, dict)
                or not isinstance(ui_test.get("running"), bool)):
            raise RegressionError("Feather test status is invalid")
        return ui_test

    def _ui_test_running(self):
        return self._ui_test_status()["running"]

    def preflight(self):
        server = self.connection.request_json("GET", "/server/info")
        try:
            info = dict(server.get("result", server))
        except (TypeError, ValueError) as exc:
            raise RegressionError("Moonraker server status is invalid") from exc
        if not info.get("klippy_connected", False):
            raise RegressionError("Moonraker is not connected to Klipper")
        if str(info.get("klippy_state", "")).lower() != "ready":
            raise RegressionError("Klipper is not ready")
        objects_value = self.connection.request_json(
            "GET", "/printer/objects/list")
        objects_result = objects_value.get("result", {})
        objects = (
            objects_result.get("objects", [])
            if isinstance(objects_result, dict) else [])
        if not isinstance(objects, list) or "feather_screen" not in objects:
            raise RegressionError("Feather screen object is unavailable")
        self.connection.require_safe_idle()
        if self._ui_test_running():
            raise RegressionError("another Feather UI test is active")

    def require_safe_idle(self):
        state = self.connection.require_safe_idle()
        if self._ui_test_running():
            raise RegressionError("the previous Feather UI test is still active")
        return state

    def discover_camera(self):
        value = self.connection.request_json("GET", "/server/webcams/list")
        result = value.get("result", value)
        webcams = result.get("webcams", []) if isinstance(result, dict) else []
        for camera in webcams:
            if (isinstance(camera, dict) and camera.get("enabled", False)
                    and str(camera.get("stream_url") or "").strip()):
                return {
                    "url": resolve_camera_url(
                        self.connection, camera.get("stream_url")),
                    "metadata": camera_metadata(camera),
                }
        return None

    def telemetry_snapshot(self):
        value = self.connection.request_json(
            "GET", TELEMETRY_QUERY, timeout=0.5)
        result = value.get("result")
        status = result.get("status") if isinstance(result, dict) else None
        if not isinstance(status, dict):
            raise PrinterConnectionError(
                "printer telemetry status is incomplete")
        return status

    def launch(self, spec, material=None, screen_capture_interval=0.0,
               start_timeout=20):
        if self._ui_test_running():
            raise RegressionError("another Feather UI test is active")
        command = "_FEATHER_UI_TEST ACTION=RUN SUITE=%s CONFIRM=%d" % (
            spec["printer_suite"], spec["confirm"])
        command += " CAPTURE_INTERVAL=%g" % float(screen_capture_interval)
        if material and spec["printer_suite"] in MATERIAL_SUITES:
            if not SAFE_MATERIAL.match(material):
                raise RegressionError("material name contains unsafe characters")
            command += " MATERIAL=" + material
        self.connection.request_json(
            "POST", "/printer/gcode/script", {"script": command})
        deadline = self.clock() + float(start_timeout)
        while self.clock() < deadline:
            status = self._ui_test_status()
            if status.get("running"):
                marker = {
                    "run_id": status.get("run_id"),
                    "suite": status.get("suite"),
                    "directory": status.get("directory"),
                }
                _safe_remote_run(marker, spec["printer_suite"])
                return marker
            self.sleeper(0.25)
        raise RegressionError("printer did not publish the launched run id")

    def wait(self, marker, timeout, run_state=None, events_alive=None,
             progress=None, abort_grace=60):
        run_id, _directory = _safe_remote_run(marker)
        started = self.clock()
        deadline = started + float(timeout)
        next_progress = started + PROGRESS_INTERVAL
        while self.clock() < deadline:
            if events_alive is not None and not events_alive():
                raise RegressionError(
                    "no printer status event for %.0f seconds" %
                    EVENT_STALL_TIMEOUT)
            if run_state is None:
                status = self._ui_test_status()
                if status.get("running") or status.get("finalizing"):
                    state = ("active" if status.get("run_id") == run_id
                             else "changed")
                else:
                    state = "finished"
            else:
                state = run_state(run_id)
            if state == "finished":
                return False
            if state == "changed":
                raise RegressionError("printer active run ownership changed")
            if state not in ("pending", "active"):
                raise RegressionError("printer run status is invalid")
            now = self.clock()
            if progress is not None and now >= next_progress:
                progress(max(0.0, now - started))
                next_progress = now + PROGRESS_INTERVAL
            self.sleeper(0.25 if run_state is not None else 1.0)
        self.abort(marker, abort_grace=abort_grace)
        return True

    def abort(self, marker, abort_grace=60):
        run_id, _directory = _safe_remote_run(marker)
        self.connection.request_json(
            "POST", "/printer/gcode/script",
            {"script": "_FEATHER_UI_TEST ACTION=ABORT"})
        deadline = self.clock() + float(abort_grace)
        while self.clock() < deadline:
            status = self._ui_test_status()
            if not status.get("running") and not status.get("finalizing"):
                return
            if status.get("run_id") != run_id:
                raise RegressionError("printer active run ownership changed")
            self.sleeper(0.25)
        raise RegressionError("printer runner remained active after abort")

    def copy_and_verify(self, marker, output_parent):
        run_id, remote = _safe_remote_run(marker)
        output_parent = pathlib.Path(output_parent).resolve()
        output_parent.mkdir(parents=True, exist_ok=True)
        try:
            result = self.connection.command_runner(
                ["scp", "-O", "-r",
                 "%s:%s" % (self.connection.scp_target, remote),
                 str(output_parent)],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=300)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RegressionError("unable to copy printer artifacts") from exc
        if result.returncode != 0:
            raise RegressionError("unable to copy printer artifacts")
        local = output_parent / run_id
        summary_path = local / "summary.json"
        if not summary_path.is_file():
            raise RegressionError("copied printer run has no summary")
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RegressionError("copied printer summary is invalid") from exc
        if (not isinstance(summary, dict)
                or summary.get("run_id") != run_id
                or not summary.get("outcome")):
            raise RegressionError("copied printer summary has wrong ownership")
        manifest_path = local / "manifest.json"
        manifest = []
        if manifest_path.is_file():
            try:
                manifest = json.loads(
                    manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise RegressionError("copied printer manifest is invalid") \
                    from exc
            if not isinstance(manifest, list):
                raise RegressionError("copied printer manifest is invalid")
        try:
            screenshot_count = int(summary.get("screenshots", 0) or 0)
            started_at = float(summary["started_at"])
            duration = float(summary["duration"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RegressionError("copied printer summary is incomplete") \
                from exc
        if (screenshot_count < 0 or not math.isfinite(started_at)
                or not math.isfinite(duration) or duration < 0.0):
            raise RegressionError("copied printer summary is incomplete")
        if len(manifest) != screenshot_count:
            raise RegressionError("copied printer manifest is incomplete")
        for record in manifest:
            if not isinstance(record, dict):
                raise RegressionError("copied printer manifest is invalid")
            filename = str(record.get("file") or "")
            if (not filename
                    or pathlib.PurePosixPath(filename).name != filename
                    or not (local / filename).is_file()):
                raise RegressionError("copied printer screenshot is missing")
            try:
                capture_time = float(record["time"])
            except (KeyError, TypeError, ValueError) as exc:
                raise RegressionError(
                    "copied printer screenshot time is invalid") from exc
            if not math.isfinite(capture_time):
                raise RegressionError(
                    "copied printer screenshot time is invalid")
        return local, summary, manifest

    def copy_partial(self, marker, output_parent):
        run_id, remote = _safe_remote_run(marker)
        output_parent = pathlib.Path(output_parent).resolve()
        output_parent.mkdir(parents=True, exist_ok=True)
        try:
            result = self.connection.command_runner(
                ["scp", "-O", "-r",
                 "%s:%s" % (self.connection.scp_target, remote),
                 str(output_parent)],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=300)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RegressionError("unable to copy partial printer artifacts") \
                from exc
        local = output_parent / run_id
        if result.returncode != 0 or not local.is_dir():
            raise RegressionError("unable to copy partial printer artifacts")
        return local

    def delete_remote(self, marker):
        run_id, remote = _safe_remote_run(marker)
        expected = ARTIFACT_ROOT + "/" + run_id
        if remote != expected:
            raise RegressionError("refusing unsafe remote cleanup")
        output = self.connection.ssh(
            "rm -rf %s && test ! -e %s && echo removed" %
            (expected, expected), timeout=60)
        if output != "removed":
            raise RegressionError("unable to remove copied printer artifacts")


class ResourceMonitor:
    """Own one bounded printer-side /proc sampler and its exact artifact."""

    def __init__(self, connection, output, duration, clock=None,
                 popen=None, sleeper=None):
        self.connection = connection
        self.output = pathlib.Path(output).resolve()
        self.duration = max(1, int(math.ceil(float(duration))))
        self.clock = clock or time.time
        self.popen = popen or subprocess.Popen
        self.sleeper = sleeper or time.sleep
        self.monitor_id = "%d-%d" % (
            os.getpid(), int(self.clock() * 1000000))
        if not SAFE_MONITOR_ID.match(self.monitor_id):
            raise RegressionError("resource monitor id is unsafe")
        self.remote = "%s/host-monitor-%s.tsv" % (
            ARTIFACT_ROOT, self.monitor_id)
        self.process = None

    def start(self):
        command = [
            "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
            self.connection.ssh_target, RESOURCE_MONITOR_SCRIPT,
            self.remote, "1", str(self.duration),
        ]
        try:
            self.process = self.popen(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
        except OSError as exc:
            raise RegressionError(
                "printer resource monitor did not start") from exc
        self.sleeper(0.1)
        if self.process.poll() is not None:
            self.process = None
            raise RegressionError("printer resource monitor did not start")

    def finish(self):
        process = self.process
        self.process = None
        if process is None:
            return {"status": "not_started", "file": None}
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        destination = self.output / RESOURCE_FILE
        try:
            result = self.connection.command_runner(
                ["scp", "-O", "%s:%s" % (
                    self.connection.scp_target, self.remote),
                 str(destination)],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RegressionError(
                "unable to copy printer resource monitor") from exc
        if result.returncode != 0 or not destination.is_file():
            raise RegressionError("unable to copy printer resource monitor")
        try:
            with destination.open(encoding="utf-8") as stream:
                header = stream.readline().strip()
                first_row = stream.readline().strip()
        except OSError as exc:
            raise RegressionError(
                "printer resource monitor artifact is invalid") from exc
        if not header.startswith("epoch\tuptime\tload1\t"):
            raise RegressionError("printer resource monitor artifact is invalid")
        # The sampler writes the header before its loop, so a file that carries
        # only a header means the sampling pass itself never produced anything
        # on this printer.  Without this check that failure reads as "the
        # sampler ran and the host was idle", which is the one conclusion the
        # report must never invent.
        if not first_row:
            raise RegressionError(
                "printer resource monitor recorded no samples")
        try:
            self.connection.ssh(
                "rm -f %s && test ! -e %s" % (self.remote, self.remote),
                timeout=10)
        except PrinterConnectionError:
            pass
        return {"status": "recorded", "file": RESOURCE_FILE}


class MediaPipeline:
    """Own camera recording and deterministic FFmpeg finalization."""

    def __init__(self, output, fps, runner=None, popen=None, sleeper=None,
                 telemetry_rate=5.0):
        self.output = pathlib.Path(output).resolve()
        self.work = self.output / "work"
        self.fps = int(fps)
        self.telemetry_rate = float(telemetry_rate)
        self.runner = runner or subprocess.run
        self.popen = popen or subprocess.Popen
        self.sleeper = sleeper or time.sleep
        self.camera_process = None
        self.camera_path = self.work / "camera.mp4"
        self.camera = {"status": "disabled"}
        self.warnings = []

    @staticmethod
    def _camera_filter(metadata, fps):
        filters = ["setpts=PTS-STARTPTS"]
        if metadata.get("flip_horizontal"):
            filters.append("hflip")
        if metadata.get("flip_vertical"):
            filters.append("vflip")
        rotation = int(metadata.get("rotation") or 0)
        if rotation == 90:
            filters.append("transpose=1")
        elif rotation == 180:
            filters.extend(("transpose=1", "transpose=1"))
        elif rotation == 270:
            filters.append("transpose=2")
        filters.extend((
            "fps=%d" % fps,
            "scale=640:480:force_original_aspect_ratio=decrease",
            "pad=640:480:(ow-iw)/2:(oh-ih)/2:black",
        ))
        return ",".join(filters)

    def start_camera(self, camera):
        self.work.mkdir(parents=True, exist_ok=True)
        if camera is None:
            self.camera = {"status": "unavailable", "metadata": None}
            self.warnings.append("No enabled printer camera was available.")
            return
        metadata = camera["metadata"]
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-use_wallclock_as_timestamps", "1", "-i", camera["url"],
            "-an", "-vf",
            self._camera_filter(metadata, self.fps),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-pix_fmt", "yuv420p", str(self.camera_path),
        ]
        try:
            process = self.popen(
                command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.sleeper(0.5)
        except OSError:
            process = None
        if process is None or process.poll() is not None:
            self.camera = {"status": "unavailable", "metadata": metadata}
            self.warnings.append("The printer camera stream could not start.")
            return
        self.camera_process = process
        self.camera = {"status": "recording", "metadata": metadata}

    def stop_camera(self):
        process = self.camera_process
        self.camera_process = None
        if process is None:
            return
        try:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
            self.camera["status"] = "partial"
            self.warnings.append("Camera recording could not stop cleanly.")
            return
        if process.returncode not in (0, 255):
            self.camera["status"] = "partial"
            self.warnings.append("Camera recording ended unexpectedly.")
        elif (not self.camera_path.is_file()
              or self.camera_path.stat().st_size == 0):
            self.camera["status"] = "partial"
            self.warnings.append("Camera recording produced no usable media.")
        else:
            self.camera["status"] = "recorded"

    def _run_ffmpeg(self, command):
        result = self.runner(
            command, text=True, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=600)
        return result.returncode == 0

    def _run_ffmpeg_stream(self, command, frames):
        process = None
        try:
            process = self.popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
            for frame in frames:
                process.stdin.write(frame)
            process.stdin.close()
            return process.wait(timeout=600) == 0
        except (OSError, subprocess.SubprocessError):
            if process is not None:
                try:
                    process.kill()
                except OSError:
                    pass
            return False

    @staticmethod
    def _concat_path(path):
        return str(path).replace("'", "'\\''")

    def _screen_frames(self, suites):
        frames = []
        for suite in suites:
            artifact = suite.get("artifact_path")
            summary = suite.get("summary")
            manifest = suite.get("manifest")
            if not artifact or not summary or not manifest:
                continue
            printer_start = float(summary.get("started_at", 0.0) or 0.0)
            anchor = float(suite.get("timeline_start_seconds", 0.0) or 0.0)
            for record in manifest:
                filename = str(record.get("file") or "")
                path = pathlib.Path(artifact) / filename
                if not filename or not path.is_file():
                    continue
                capture_time = float(record.get("time", printer_start) or
                                     printer_start)
                frames.append((
                    max(anchor, anchor + capture_time - printer_start), path,
                ))
        return sorted(frames, key=lambda item: item[0])

    def _screen_video(self, suites, duration):
        frames = self._screen_frames(suites)
        if not frames:
            return None
        concat = self.work / "screen.ffconcat"
        screen = self.work / "screen.mp4"
        first_offset = frames[0][0]
        lines = ["ffconcat version 1.0"]
        for index, (_offset, path) in enumerate(frames):
            next_offset = (
                frames[index + 1][0] if index + 1 < len(frames) else duration)
            active_offset = frames[index][0]
            frame_duration = max(0.001, next_offset - active_offset)
            lines.append("file '%s'" % self._concat_path(path))
            lines.append("duration %.6f" % frame_duration)
        lines.append("file '%s'" % self._concat_path(frames[-1][1]))
        concat.write_text("\n".join(lines) + "\n", encoding="utf-8")
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat),
            "-vf", (
                "tpad=start_mode=add:start_duration=%.6f:color=0x090d12,"
                "fps=%d,scale=800:480" % (first_offset, self.fps)),
            "-t", "%.6f" % duration,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
            "-pix_fmt", "yuv420p", str(screen),
        ]
        return screen if self._run_ffmpeg(command) else None

    @staticmethod
    def _panel_value(value, fallback="--"):
        text = str(value if value not in (None, "") else fallback)
        return " ".join(text.replace("\n", " ").split())

    @staticmethod
    def _panel_number(value):
        number = _finite_number(value, digits=2)
        return "--" if number is None else "%.0f" % number

    def _panel_lines(self, record):
        test = record.get("test", {})
        context = record.get("context", {})
        position = record.get("position") or (None, None, None)
        nozzle = record.get("nozzle", {})
        bed = record.get("bed", {})
        suite = self._panel_value(
            test.get("host_suite") or test.get("suite"), "IDLE")
        phase = self._panel_value(test.get("phase"), "-")
        step = self._panel_value(test.get("step"), "-")[:40]
        index = test.get("step_index")
        count = test.get("step_count")
        progress = (
            "%d/%d" % (int(index) + 1, int(count))
            if isinstance(index, int) and isinstance(count, int) and count
            else "-")
        title = "TEST %s | %s | %s %s" % (
            suite.upper(), phase, step, progress)
        state = "UI %s | PRN %s | HOME %s" % (
            self._panel_value(record.get("page")),
            self._panel_value(record.get("print_state")).upper(),
            self._panel_value(record.get("homed_axes"), "NONE"))
        motion = "XYZ %s %s %s | V %s MM/S | N %s/%s | B %s/%s C" % (
            self._panel_number(position[0]),
            self._panel_number(position[1]),
            self._panel_number(position[2]),
            self._panel_number(record.get("velocity")),
            self._panel_number(nozzle.get("temperature")),
            self._panel_number(nozzle.get("target")),
            self._panel_number(bed.get("temperature")),
            self._panel_number(bed.get("target")))
        path = [self._panel_value(item) for item in context.get("path", ())]
        context_state = self._panel_value(context.get("state"), "IDLE")
        context_line = "CTX %s" % " > ".join(path + [context_state])
        resources = record.get("resources", {})
        resource_line = "RES CPU K%s T%s D%s%% | RSS K%s T%sM | MEM %sM L%s" % (
            self._panel_number(resources.get("klippy_cpu")),
            self._panel_number(resources.get("typer_cpu")),
            self._panel_number(resources.get("dropbear_cpu")),
            self._panel_number(resources.get("klippy_rss_mb")),
            self._panel_number(resources.get("typer_rss_mb")),
            self._panel_number(resources.get("mem_available_mb")),
            self._panel_number(resources.get("load1")))
        return (title[:65], state[:65], motion[:65], context_line[:65],
                resource_line[:65])

    @staticmethod
    def _draw_bitmap_text(frame, width, height, x, y, text, color,
                          scale=2):
        pixel = bytes((color[2], color[1], color[0]))
        cursor = int(x)
        for character in str(text).upper():
            if cursor + 5 * scale > width:
                break
            glyph = FONT_5X7.get(character, FONT_5X7["?"])
            for row, bits in enumerate(glyph):
                for column in range(5):
                    if not bits & (1 << (4 - column)):
                        continue
                    left = cursor + column * scale
                    top = y + row * scale
                    for dy in range(scale):
                        if top + dy >= height:
                            continue
                        offset = ((top + dy) * width + left) * 3
                        for dx in range(scale):
                            if left + dx < width:
                                begin = offset + dx * 3
                                frame[begin:begin + 3] = pixel
            cursor += 6 * scale

    def _panel_frame(self, record):
        width, height = 800, 150
        frame = bytearray(bytes((28, 20, 13)) * width * height)
        frame[:width * 3 * 3] = bytes((247, 129, 47)) * width * 3
        lines = self._panel_lines(record)
        for y, line, color in zip(
                (9, 37, 65, 93, 121), lines,
                ((240, 246, 252), (139, 148, 158),
                 (255, 184, 107), (126, 231, 135),
                 (110, 190, 255))):
            self._draw_bitmap_text(
                frame, width, height, 12, y, line, color)
        return bytes(frame)

    def _resource_records(self):
        path = self.output / RESOURCE_FILE
        if not path.is_file():
            return []
        samples = {}
        try:
            with path.open("r", encoding="utf-8", newline="") as stream:
                for row in csv.DictReader(stream, delimiter="\t"):
                    epoch = _finite_number(row.get("epoch"), digits=6)
                    if epoch is None:
                        continue
                    sample = samples.setdefault(epoch, {
                        "time": epoch, "processes": [],
                    })
                    role = str(row.get("role") or "")
                    if role == "system":
                        sample["load1"] = _finite_number(row.get("load1"))
                        memory = _finite_number(row.get("mem_available_kb"))
                        sample["mem_available_mb"] = (
                            None if memory is None else memory / 1024.0)
                        continue
                    if role not in ("klippy", "typer", "dropbear"):
                        continue
                    sample["processes"].append({
                        "key": (role, str(row.get("pid") or "")),
                        "role": role,
                        "cpu_ticks": _finite_number(row.get("cpu_ticks")),
                        "rss_kb": _finite_number(row.get("rss_kb")),
                    })
        except (OSError, TypeError, ValueError):
            return []
        records = []
        previous_ticks = {}
        previous_time = None
        for epoch in sorted(samples):
            sample = samples[epoch]
            elapsed = (None if previous_time is None
                       else max(0.0, epoch - previous_time))
            cpu_delta = {"klippy": 0.0, "typer": 0.0, "dropbear": 0.0}
            rss_kb = {"klippy": 0.0, "typer": 0.0, "dropbear": 0.0}
            for process in sample["processes"]:
                ticks = process["cpu_ticks"]
                prior = previous_ticks.get(process["key"])
                if ticks is not None and prior is not None and ticks >= prior:
                    cpu_delta[process["role"]] += ticks - prior
                if ticks is not None:
                    previous_ticks[process["key"]] = ticks
                rss_kb[process["role"]] += process["rss_kb"] or 0.0
            record = dict((key, value) for key, value in sample.items()
                          if key != "processes")
            for role in cpu_delta:
                # Linux /proc CPU counters use USER_HZ ticks. Convert each
                # role to a percentage of one CPU over the observed interval.
                record[role + "_cpu"] = (
                    cpu_delta[role] * 100.0 / LINUX_USER_HZ / elapsed
                    if elapsed else None)
                record[role + "_rss_mb"] = rss_kb[role] / 1024.0
            records.append(record)
            previous_time = epoch
        return records

    def _telemetry_records(self):
        path = self.output / TELEMETRY_FILE
        if not path.is_file():
            return []
        records = []
        try:
            with path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        continue
                    offset = _finite_number(value.get("offset"), digits=6)
                    if offset is not None:
                        value["offset"] = max(0.0, offset)
                        records.append(value)
        except (OSError, TypeError, ValueError):
            return []
        return sorted(records, key=lambda item: item["offset"])

    def _telemetry_video(self, duration):
        records = self._telemetry_records()
        resources = self._resource_records()
        if not records:
            records = [{
                "offset": 0.0,
                "test": {"host_suite": (
                    "TELEMETRY DISABLED" if not self.telemetry_rate
                    else "TELEMETRY UNAVAILABLE")},
                "page": "-", "print_state": "-", "position": None,
                "homed_axes": "-", "velocity": None,
                "nozzle": {}, "bed": {},
                "context": {"path": (), "state": "NO RT SAMPLES"},
            }]
        panel = self.work / "telemetry.mp4"
        panel_fps = max(1.0, min(
            float(self.fps), self.telemetry_rate or 1.0))
        frame_count = max(1, int(math.ceil(duration * panel_fps)))

        def frames():
            record_index = 0
            resource_index = 0
            for frame_index in range(frame_count):
                frame_time = frame_index / panel_fps
                while (record_index + 1 < len(records)
                       and records[record_index + 1]["offset"] <= frame_time):
                    record_index += 1
                record = dict(records[record_index])
                observed_time = _finite_number(record.get("time"), digits=6)
                if observed_time is not None and resources:
                    while (resource_index + 1 < len(resources)
                           and resources[resource_index + 1]["time"]
                           <= observed_time):
                        resource_index += 1
                    if resources[resource_index]["time"] <= observed_time:
                        record["resources"] = resources[resource_index]
                yield self._panel_frame(record)

        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pixel_format", "bgr24",
            "-video_size", "800x150", "-framerate", "%g" % panel_fps,
            "-i", "pipe:0",
            "-t", "%.6f" % duration,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
            "-pix_fmt", "yuv420p", str(panel),
        ]
        return panel if self._run_ffmpeg_stream(command, frames()) else None

    def _placeholder_video(self, label, width, height, duration):
        stem = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
        video = self.work / (stem + ".mp4")
        frame = bytearray(bytes((18, 13, 9)) * width * height)
        scale = 3
        text_width = len(label) * 6 * scale
        self._draw_bitmap_text(
            frame, width, height, max(0, (width - text_width) // 2),
            max(0, height // 2 - 11), label, (110, 118, 129), scale)
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pixel_format", "bgr24",
            "-video_size", "%dx%d" % (width, height),
            "-framerate", "1", "-i", "pipe:0", "-vf",
            "tpad=stop_mode=clone:stop_duration=%.6f,fps=%d" % (
                duration, self.fps),
            "-t", "%.6f" % duration,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
            "-pix_fmt", "yuv420p", str(video),
        ]
        return video if self._run_ffmpeg_stream(
            command, (bytes(frame),)) else None

    def finalize(self, suites, duration):
        duration = max(0.1, float(duration))
        self.work.mkdir(parents=True, exist_ok=True)
        recording = self.output / "recording.mp4"
        had_screen_frames = bool(self._screen_frames(suites))
        screen = self._screen_video(suites, duration)
        telemetry = self._telemetry_video(duration)
        camera_available = self.camera_path.is_file()
        if screen is None and not camera_available:
            return {"status": "failed", "recording": None}
        screen_input = screen or self._placeholder_video(
            "SCREEN UNAVAILABLE", 800, 480, duration)
        camera_input = (self.camera_path if camera_available else
                        self._placeholder_video(
                            "CAMERA UNAVAILABLE", 800, 450, duration))
        if (screen_input is not None and camera_input is not None
                and telemetry is not None):
            filter_graph = (
                "[0:v]scale=800:480,setsar=1[screen];"
                "[1:v]scale=800:450:force_original_aspect_ratio=decrease,"
                "pad=800:450:(ow-iw)/2:(oh-ih)/2:color=0x090d12,setsar=1,"
                "tpad=stop_mode=clone:stop_duration=%.6f[camera];"
                "[2:v]scale=800:150,setsar=1,"
                "tpad=stop_mode=clone:stop_duration=%.6f[telemetry];"
                "[screen][camera][telemetry]vstack=inputs=3[v]" % (
                    duration, duration))
            command = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(screen_input), "-i", str(camera_input), "-i",
                str(telemetry),
                "-filter_complex", filter_graph, "-map", "[v]",
                "-t", "%.6f" % duration, "-r", str(self.fps),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                "-pix_fmt", "yuv420p", str(recording),
            ]
            if not self._run_ffmpeg(command):
                self.warnings.append(
                    "Vertical composite failed; fallback media was kept.")
                if screen is not None:
                    shutil.copy2(screen, recording)
                elif camera_available:
                    shutil.copy2(self.camera_path, recording)
                else:
                    return {"status": "failed", "recording": None}
        elif screen is not None:
            if telemetry is None:
                self.warnings.append(
                    "RT telemetry panel could not be assembled.")
            shutil.copy2(screen, recording)
        elif camera_available:
            self.warnings.append(
                "Semantic screen video was unavailable; camera-only media "
                "was kept." if not had_screen_frames else
                "Semantic screen video could not be assembled; camera-only "
                "media was kept.")
            command = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(self.camera_path), "-an", "-vf",
                "fps=%d,scale=640:480" % self.fps,
                "-t", "%.6f" % duration, "-c:v", "libx264",
                "-pix_fmt", "yuv420p", str(recording),
            ]
            if not self._run_ffmpeg(command):
                return {"status": "failed", "recording": None}
        else:
            return {"status": "failed", "recording": None}
        shutil.rmtree(self.work, ignore_errors=True)
        return {"status": "passed", "recording": recording.name}


def _host_preflight(output, suite_count, run_timeout, which=None,
                    disk_usage=None):
    which = which or shutil.which
    disk_usage = disk_usage or shutil.disk_usage
    output = pathlib.Path(output)
    output.mkdir(parents=True, exist_ok=False)
    missing = [name for name in ("ffmpeg", "ssh", "scp") if not which(name)]
    if missing:
        suffix = (
            " Install FFmpeg with `brew install ffmpeg`."
            if "ffmpeg" in missing and which("brew") else "")
        raise RegressionError(
            "missing host dependencies: %s.%s" %
            (", ".join(missing), suffix))
    probe = output / ".write-probe"
    try:
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as exc:
        raise RegressionError("local artifact root is not writable") from exc
    required = 256 * 1024 * 1024 + int(
        max(1, suite_count) * max(1, run_timeout) * 1_000_000)
    if disk_usage(output).free < required:
        raise RegressionError(
            "insufficient local disk space (need about %.1f GiB free)" %
            (required / float(1024 ** 3)))


def _local_output(value=None):
    if value:
        return pathlib.Path(value).expanduser().resolve()
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return (DEFAULT_ARTIFACT_ROOT / stamp).resolve()


def _relative_artifact(output, path):
    return pathlib.Path(path).resolve().relative_to(output).as_posix()


def _suite_report(spec, started, anchor):
    return {
        "name": spec["name"],
        "printer_suite": spec["printer_suite"],
        "status": "running",
        "outcome": None,
        "reason": None,
        "started_at": _utc_time(started),
        "finished_at": None,
        "duration_seconds": None,
        "timeline_start_seconds": anchor,
        "screenshot_count": 0,
        "artifact": None,
        "links": {},
    }


def _public_suite(value):
    return dict((key, item) for key, item in value.items()
                if key not in ("artifact_path", "summary", "manifest"))


def _overall_status(report):
    if report.get("infrastructure_error"):
        return "error"
    if report.get("media", {}).get("status") == "failed":
        return "error"
    if any(item.get("status") == "failed" for item in report["suites"]):
        return "failed"
    if any(item.get("status") == "skipped" for item in report["suites"]):
        return "partial"
    return "passed"


def _write_report(output, report):
    output = pathlib.Path(output)
    public = dict(report)
    public["suites"] = [_public_suite(item) for item in report["suites"]]
    temporary = output / "report.json.tmp"
    temporary.write_text(
        json.dumps(public, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    temporary.replace(output / "report.json")

    def escape(value):
        return html.escape(str(value if value is not None else ""))

    rows = []
    for suite in public["suites"]:
        links = " ".join(
            '<a href="%s">%s</a>' % (
                escape(urllib.parse.quote(path, safe="/")), escape(name))
            for name, path in suite.get("links", {}).items())
        rows.append(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
            "<td>%s</td><td>%s</td><td>%s</td></tr>" % (
                escape(suite["name"]), escape(suite["printer_suite"]),
                escape(suite["status"]), escape(suite.get("reason")),
                escape(suite.get("duration_seconds")),
                escape(suite.get("screenshot_count", 0)), links))
    warnings = "".join(
        "<li>%s</li>" % escape(item) for item in public.get("warnings", []))
    media = public.get("media", {})
    video = ""
    if media.get("recording"):
        source = escape(urllib.parse.quote(media["recording"], safe="/"))
        video = '<video controls preload="metadata" src="%s"></video>' % source
    error = public.get("infrastructure_error")
    error_html = (
        "<p class=error><strong>Infrastructure:</strong> %s</p>" %
        escape(error.get("message")) if error else "")
    camera = public.get("camera", {})
    camera_info = camera.get("metadata") or {}
    camera_html = (
        "<p>Camera: <code>%s</code>%s</p>" % (
            escape(camera.get("status", "unknown")),
            (" · %s · %s FPS" % (
                escape(camera_info.get("name") or
                       camera_info.get("service") or "printer camera"),
                escape(camera_info.get("target_fps"))))
            if camera_info else ""))
    telemetry = public.get("telemetry", {})
    telemetry_link = (
        ' · <a href="%s">JSONL</a>' % escape(
            urllib.parse.quote(telemetry["file"], safe="/"))
        if telemetry.get("file") else "")
    telemetry_html = (
        "<p>RT telemetry: <code>%s</code> · requested %s Hz · "
        "effective %s Hz · %s samples%s</p>" % (
            escape(telemetry.get("status", "unknown")),
            escape(telemetry.get("rate_hz", 0)),
            escape(telemetry.get("effective_rate_hz", 0)),
            escape(telemetry.get("sample_count", 0)), telemetry_link))
    resources = public.get("resources", {})
    resources_link = (
        ' · <a href="%s">TSV</a>' % escape(
            urllib.parse.quote(resources["file"], safe="/"))
        if resources.get("file") else "")
    resources_html = "<p>Printer resources: <code>%s</code>%s</p>" % (
        escape(resources.get("status", "unknown")), resources_link)
    page = """<!doctype html>
<html><head><meta charset="utf-8"><title>FF5M printer regression</title>
<style>
body{font:15px system-ui,sans-serif;max-width:1200px;margin:2rem auto;padding:0 1rem;color:#18202a}
table{border-collapse:collapse;width:100%%}th,td{border:1px solid #ccd3db;padding:.55rem;text-align:left;vertical-align:top}
video{width:100%%;max-height:70vh;background:#000}.error{color:#a11}code{background:#eef1f4;padding:.1rem .3rem}
</style></head><body>
<h1>FF5M printer regression: %s</h1>
<p>Printer <code>%s</code> · requested <code>%s</code> · %s FPS · screen capture interval %ss · duration %.1fs<br>Started %s · finished %s</p>
%s%s%s%s%s
<h2>Suites</h2><table><thead><tr><th>Host suite</th><th>Printer suite</th><th>Status</th><th>Reason</th><th>Duration</th><th>Screens</th><th>Artifacts</th></tr></thead><tbody>%s</tbody></table>
<h2>Warnings</h2><ul>%s</ul>
<p><a href="report.json">Machine-readable report</a></p>
</body></html>
""" % (
        escape(public["status"]), escape(public["printer_host"]),
        escape(public["requested_suite"]), public["fps"],
        escape(public.get("screen_capture_interval", 0)),
        float(public.get("duration_seconds") or 0.0),
        escape(public.get("started_at")), escape(public.get("finished_at")),
        camera_html, telemetry_html, resources_html, error_html, video,
        "".join(rows), warnings)
    (output / "report.html").write_text(page, encoding="utf-8")


class RegressionRun:
    """Own one local unattended orchestration and its durable report."""

    def __init__(self, args, client=None, media=None, telemetry=None,
                 resource_monitor=None,
                 clock=None, wall_clock=None, sleeper=None, progress=None):
        self.args = args
        self.specs = selected_suites(args.suite)
        self.requested_suites = list(args.suite)
        self.output = _local_output(args.output)
        self.clock = clock or time.monotonic
        self.wall_clock = wall_clock or time.time
        self.sleeper = sleeper or time.sleep
        self.progress = progress or (lambda message: print(
            message, file=sys.stderr, flush=True))
        self.connection = None
        self.client = client
        self.media = media
        self.telemetry = telemetry
        self.resource_monitor = resource_monitor
        self.started_monotonic = None
        self.current_marker = None
        self.current_suite = None
        self.report = {
            "schema_version": 1,
            "status": "running",
            "requested_suite": ", ".join(self.requested_suites),
            "requested_suites": self.requested_suites,
            "printer_host": args.printer,
            "started_at": None,
            "finished_at": None,
            "duration_seconds": None,
            "fps": args.fps,
            "screen_capture_interval": args.screen_capture_interval,
            "telemetry": {
                "status": "pending", "rate_hz": args.telemetry_rate,
                "effective_rate_hz": 0.0, "sample_count": 0,
                "failure_count": 0, "file": None,
            },
            "resources": {"status": "pending", "file": None},
            "camera": {"status": "pending"},
            "media": {"status": "pending", "recording": None},
            "suites": [],
            "warnings": [],
            "infrastructure_error": None,
        }

    def _fail(self, category, message):
        if self.report["infrastructure_error"] is None:
            self.report["infrastructure_error"] = {
                "category": category, "message": str(message),
            }

    def _skip_remaining(self, start, reason):
        for spec in self.specs[start:]:
            self.report["suites"].append({
                "name": spec["name"],
                "printer_suite": spec["printer_suite"],
                "status": "skipped", "outcome": None,
                "reason": str(reason), "started_at": None,
                "finished_at": None, "duration_seconds": None,
                "timeline_start_seconds": None, "screenshot_count": 0,
                "artifact": None, "links": {},
            })

    def _finish_suite(self, suite, local, summary, manifest, finished):
        suite["artifact_path"] = str(local)
        suite["summary"] = summary
        suite["manifest"] = manifest
        suite["outcome"] = str(summary.get("outcome"))
        suite["status"] = (
            "passed" if suite["outcome"] == "passed" else "failed")
        suite["reason"] = summary.get("reason")
        suite["finished_at"] = _utc_time(finished)
        suite["duration_seconds"] = float(summary.get("duration", 0.0) or 0.0)
        suite["screenshot_count"] = len(manifest)
        suite["artifact"] = _relative_artifact(self.output, local)
        suite["links"] = {
            name: (pathlib.PurePosixPath(suite["artifact"]) / name).as_posix()
            for name in ARTIFACT_LINKS if (local / name).is_file()
        }

    def _salvage_suite(self, suite, marker):
        local = self.client.copy_partial(
            marker, self.output / "suites" / suite["name"])
        suite["artifact_path"] = str(local)
        suite["artifact"] = _relative_artifact(self.output, local)
        suite["links"] = {
            name: (pathlib.PurePosixPath(suite["artifact"]) / name).as_posix()
            for name in ARTIFACT_LINKS if (local / name).is_file()
        }
        manifest_path = local / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                manifest = []
            if isinstance(manifest, list):
                suite["screenshot_count"] = sum(
                    1 for item in manifest if isinstance(item, dict)
                    and (local / str(item.get("file") or "")).is_file())

    def _telemetry_test(self):
        suite = self.current_suite or {}
        return {
            "host_suite": suite.get("name"),
            "printer_suite": suite.get("printer_suite"),
        }

    def _suite_progress(self, elapsed):
        suite = self.current_suite or {}
        seconds = max(0, int(elapsed))
        parts = ["%s: running %dm %02ds" % (
            suite.get("name", "suite"), seconds // 60, seconds % 60)]
        status = getattr(self.telemetry, "latest_test_status", None)
        if isinstance(status, dict):
            phase = " ".join(str(status.get("phase") or "").split())
            step = " ".join(str(status.get("step") or "").split())
            if phase:
                parts.append("phase=%s" % phase)
            index = status.get("step_index")
            count = status.get("step_count")
            if (isinstance(index, int) and isinstance(count, int)
                    and count > 0):
                parts.append("step=%d/%d" % (index + 1, count))
            if step:
                parts.append("action=%s" % step[:60])
        samples = getattr(self.telemetry, "sample_count", None)
        if isinstance(samples, int):
            parts.append("telemetry=%d" % samples)
        self.progress(" | ".join(parts))

    def _run_suites(self):
        for index, spec in enumerate(self.specs):
            self.progress("%s: running" % spec["name"])
            started_wall = self.wall_clock()
            anchor = self.clock() - self.started_monotonic
            suite = _suite_report(spec, started_wall, anchor)
            self.current_suite = suite
            self.report["suites"].append(suite)
            marker = None
            timed_out = False
            try:
                marker = self.client.launch(
                    spec, self.args.material,
                    self.args.screen_capture_interval)
                # The runner becomes observable only after its lazy setup has
                # completed. Anchor printer-relative screenshot timestamps at
                # that observation, not before the launch request, otherwise
                # the screen track advances ahead of the continuous camera.
                suite["timeline_start_seconds"] = max(
                    0.0, self.clock() - self.started_monotonic)
                self.current_marker = marker
                self.telemetry.expect_run(marker["run_id"])
                timed_out = self.client.wait(
                    marker, self.args.run_timeout,
                    run_state=(self.telemetry.run_state
                               if self.args.telemetry_rate else None),
                    events_alive=(self.telemetry.events_alive
                                  if self.args.telemetry_rate else None),
                    progress=self._suite_progress)
                self.progress("%s: downloading artifacts" % spec["name"])
                local, summary, manifest = self.client.copy_and_verify(
                    marker, self.output / "suites" / spec["name"])
                self._finish_suite(
                    suite, local, summary, manifest, self.wall_clock())
                try:
                    self.client.delete_remote(marker)
                except (PrinterConnectionError, RegressionError) as exc:
                    self.report["warnings"].append(str(exc))
                if timed_out:
                    raise RegressionError(
                        "suite exceeded its timeout and was aborted")
                cleanup_failed = any(
                    item.get("step") == "cleanup"
                    for item in summary.get("failures", [])
                    if isinstance(item, dict))
                if cleanup_failed:
                    raise RegressionError(
                        "printer runner reported a cleanup failure")
                self.client.require_safe_idle()
            except (PrinterConnectionError, RegressionError, OSError) as exc:
                if marker is not None and suite["status"] == "running":
                    try:
                        self._salvage_suite(suite, marker)
                    except (PrinterConnectionError, RegressionError, OSError) \
                            as salvage_exc:
                        self.report["warnings"].append(
                            "Partial printer artifacts could not be copied: %s" %
                            salvage_exc)
                if suite["status"] == "running":
                    suite["status"] = "infrastructure_error"
                    suite["reason"] = str(exc)
                    suite["finished_at"] = _utc_time(self.wall_clock())
                self._fail(type(exc).__name__, exc)
                self.progress("%s: infrastructure failure" % spec["name"])
                self._skip_remaining(index + 1, str(exc))
                return
            self.current_marker = None
            self.current_suite = None
            self.progress("%s: %s (artifacts copied)" % (
                spec["name"], suite["status"]))

    def run(self):
        started_wall = self.wall_clock()
        self.report["started_at"] = _utc_time(started_wall)
        self.progress("preflight")
        try:
            _host_preflight(
                self.output, len(self.specs), self.args.run_timeout)
        except (RegressionError, OSError) as exc:
            self._fail(type(exc).__name__, exc)
            return self._finalize(started_wall, media=False)
        if (any(item["physical"] for item in self.specs)
                and not self.args.confirm_unattended_physical_test):
            self._fail(
                "ConfirmationRequired",
                "physical suites require --confirm-unattended-physical-test")
            return self._finalize(started_wall, media=False)
        try:
            if self.client is None:
                self.connection = PrinterConnection(
                    self.args.printer, timeout=self.args.connection_timeout)
                self.client = PrinterRunClient(
                    self.connection, clock=self.clock, sleeper=self.sleeper)
            self.client.preflight()
        except KeyboardInterrupt:
            self._fail("UserCancelled", "run cancelled by the operator")
            self._skip_remaining(0, "run cancelled by the operator")
            return self._finalize(started_wall, media=False)
        except (PrinterConnectionError, RegressionError, OSError) as exc:
            self._fail(type(exc).__name__, exc)
            self._skip_remaining(0, str(exc))
            return self._finalize(started_wall, media=False)

        camera = None
        if not self.args.no_camera:
            try:
                camera = self.client.discover_camera()
            except (PrinterConnectionError, RegressionError, OSError):
                self.report["warnings"].append(
                    "Camera discovery failed; continuing screen-only.")
        if self.media is None:
            self.media = MediaPipeline(
                self.output, self.args.fps, sleeper=self.sleeper,
                telemetry_rate=self.args.telemetry_rate)
        self.started_monotonic = self.clock()
        if self.telemetry is None:
            self.telemetry = TelemetryRecorder(
                self.output, self.args.telemetry_rate,
                self.client.telemetry_snapshot,
                clock=self.clock, wall_clock=self.wall_clock)
        if self.args.no_resource_monitor:
            # Declared rather than silently skipped, so the report cannot read
            # like the sampler ran and observed nothing.  Clearing the monitor
            # here keeps one owner for "is it running" below.
            self.resource_monitor = None
            self.report["resources"] = {"status": "disabled", "file": None}
        elif self.resource_monitor is None and self.connection is not None:
            self.resource_monitor = ResourceMonitor(
                self.connection, self.output,
                len(self.specs) * self.args.run_timeout + 120.0,
                clock=self.wall_clock)
        if self.resource_monitor is not None:
            try:
                self.resource_monitor.start()
            except (PrinterConnectionError, RegressionError, OSError) as exc:
                self.report["resources"] = {
                    "status": "unavailable", "file": None,
                }
                message = "Printer resource monitor could not start: %s" % exc
                self._fail(type(exc).__name__, message)
                self._skip_remaining(0, message)
                return self._finalize(started_wall, media=False)
        self.telemetry.start(self.started_monotonic, self._telemetry_test)
        if self.args.no_camera:
            self.media.camera = {"status": "disabled", "metadata": None}
        else:
            self.media.start_camera(camera)
        self.report["camera"] = self.media.camera
        try:
            self._run_suites()
        except KeyboardInterrupt:
            self._fail("UserCancelled", "run cancelled by the operator")
            if self.current_suite is not None:
                self.current_suite["status"] = "infrastructure_error"
                self.current_suite["reason"] = "run cancelled by the operator"
                self.current_suite["finished_at"] = _utc_time(
                    self.wall_clock())
            if self.current_marker is not None:
                try:
                    self.client.abort(self.current_marker)
                except (PrinterConnectionError, RegressionError, OSError):
                    self.report["warnings"].append(
                        "The active printer suite could not be confirmed aborted.")
            self._skip_remaining(
                len(self.report["suites"]), "run cancelled by the operator")
        finally:
            self.report["telemetry"] = self.telemetry.finish()
            if (self.resource_monitor is not None
                    and self.report["resources"]["status"] == "pending"):
                try:
                    self.report["resources"] = self.resource_monitor.finish()
                except (PrinterConnectionError, RegressionError, OSError) as exc:
                    self.report["resources"] = {
                        "status": "unavailable", "file": None,
                    }
                    self.report["warnings"].append(
                        "Printer resource monitor could not be collected: %s" %
                        exc)
            elif self.report["resources"]["status"] == "pending":
                self.report["resources"]["status"] = "not_started"
            if self.report["telemetry"]["status"] == "partial":
                self.report["warnings"].append(
                    "RT telemetry had intermittent sampling failures.")
            elif self.report["telemetry"]["status"] == "unavailable":
                self.report["warnings"].append(
                    "RT telemetry could not be collected.")
            try:
                self.media.stop_camera()
            except (OSError, subprocess.SubprocessError):
                self.report["warnings"].append(
                    "Camera recording could not stop cleanly.")
            self.report["camera"] = self.media.camera
        return self._finalize(started_wall, media=True)

    def _finalize(self, started_wall, media):
        finished_wall = self.wall_clock()
        duration = max(0.0, finished_wall - started_wall)
        self.report["finished_at"] = _utc_time(finished_wall)
        self.report["duration_seconds"] = duration
        if media:
            self.progress("video: finalizing")
            try:
                media_duration = max(
                    0.0, self.clock() - self.started_monotonic)
                self.report["media"] = self.media.finalize(
                    self.report["suites"], media_duration)
                self.report["media"]["duration_seconds"] = media_duration
            except (OSError, subprocess.SubprocessError):
                self.report["media"] = {
                    "status": "failed", "recording": None,
                }
                self.report["warnings"].append(
                    "FFmpeg finalization failed; intermediates were retained.")
            for warning in self.media.warnings:
                if warning not in self.report["warnings"]:
                    self.report["warnings"].append(warning)
        else:
            self.report["media"] = {"status": "not_started", "recording": None}
            if self.report["telemetry"]["status"] == "pending":
                self.report["telemetry"]["status"] = "not_started"
            if self.report["resources"]["status"] == "pending":
                self.report["resources"]["status"] = "not_started"
        self.report["status"] = _overall_status(self.report)
        try:
            _write_report(self.output, self.report)
            self.progress("report: %s" % (self.output / "report.html"))
        except OSError as exc:
            self._fail(type(exc).__name__, "unable to write local report")
            self.report["status"] = "error"
        return self.report, self.output


def _fps(value):
    try:
        fps = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("FPS must be an integer") from exc
    if fps < 1 or fps > 30:
        raise argparse.ArgumentTypeError("FPS must be between 1 and 30")
    return fps


def _positive_seconds(value):
    try:
        result = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a number") from exc
    if result <= 0:
        raise argparse.ArgumentTypeError("timeout must be positive")
    return result


def _screen_capture_interval(value):
    try:
        result = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "screen capture interval must be a number") from exc
    if result == 0.0:
        return result
    if result < 5.0 or result > 300.0 or not math.isfinite(result):
        raise argparse.ArgumentTypeError(
            "screen capture interval must be 0 or between 5 and 300 seconds")
    return result


def _telemetry_rate(value):
    try:
        result = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "telemetry rate must be a number") from exc
    if result == 0.0:
        return result
    if result < 1.0 or result > 10.0 or not math.isfinite(result):
        raise argparse.ArgumentTypeError(
            "telemetry rate must be 0 or between 1 and 10 Hz")
    return result


def _arguments(argv=None):
    parser = argparse.ArgumentParser(
        description="Run unattended FF5M printer regression suites locally.")
    parser.add_argument("--printer", required=True, help="printer host or IP")
    parser.add_argument(
        "--suite", choices=("all",) + tuple(SUITES), action="append",
        nargs="+", metavar="SUITE",
        help="one or more host suites in execution order; may be repeated")
    parser.add_argument("--material")
    parser.add_argument("--fps", type=_fps, default=10)
    parser.add_argument(
        "--screen-capture-interval", type=_screen_capture_interval,
        default=5.0,
        help="capture the latest stable Feather screen at least this often; "
             "0 keeps semantic captures only")
    parser.add_argument(
        "--telemetry-rate", type=_telemetry_rate, default=1.0,
        help="collect host-side RT printer telemetry at this rate in Hz; "
             "0 disables telemetry")
    parser.add_argument("--run-timeout", type=_positive_seconds, default=2400)
    parser.add_argument(
        "--connection-timeout", type=_positive_seconds, default=10)
    parser.add_argument("--output")
    parser.add_argument("--no-camera", action="store_true")
    parser.add_argument(
        "--no-resource-monitor", action="store_true",
        help="skip the printer-side /proc sampler; it is the only observer "
             "that runs for the whole run, so leaving it out isolates its own "
             "cost when bisecting host overload")
    parser.add_argument(
        "--confirm-unattended-physical-test", action="store_true",
        help="confirm an observed idle printer, prepared bed, and unattended "
             "motion/heating")
    args = parser.parse_args(argv)
    args.suite = [
        item for group in (args.suite or [["core"]]) for item in group]
    try:
        specs = selected_suites(args.suite)
    except RegressionError as exc:
        parser.error(str(exc))
    if args.material:
        args.material = args.material.strip().upper()
        if not SAFE_MATERIAL.match(args.material):
            parser.error("material name contains unsafe characters")
    if (args.telemetry_rate > 1.0
            and any(item["physical"] for item in specs)):
        parser.error(
            "physical suites limit telemetry to 1 Hz to protect the "
            "Klipper reactor")
    return args


def main(argv=None):
    args = _arguments(argv)
    run = RegressionRun(args)
    try:
        report, _output = run.run()
    except KeyboardInterrupt:
        return 130
    if report["status"] == "passed":
        return 0
    if report["status"] == "failed":
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
