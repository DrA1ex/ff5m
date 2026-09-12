## On-printer regression run lifecycle for the Feather display.
##
## This module is deliberately absent from Feather's normal import graph.  It
## is loaded only by the hidden _FEATHER_UI_TEST command.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import hashlib
import json
import logging
import math
import os
import shutil
import time
from datetime import datetime

from ff5m_ui.screen import ScreenPage
from ff5m_ui.print_state import PrintState
from ff5m_ui.z_offset import actions as z_actions
from .artifacts import (
    ACTIVE_MARKER, ARTIFACT_ROOT, ArtifactWorker,
    SCREEN_HEIGHT, SCREEN_WIDTH, _atomic_json, _jsonable,
)
from .context_fixtures import OperationContextRecorder
from .scenarios import ScenarioCatalog
from .resources import (
    ContextTestFixture, PrinterStateSnapshot, capture_print_tuning,
    load_context_print_gcode,
    recover_interrupted_context_resources,
)


CAPTURE_RECEIPT_TIMEOUT = 6.0
TAP_OPERATION_TIMEOUT = 1900.0
REACTOR_PROBE_INTERVAL = 0.2
REACTOR_PROBE_FLUSH_INTERVAL = 1.0
# Operation states that drive the toolhead against the bed.  A framebuffer
# capture during one of these competes with Klipper for the host, so periodic
# captures wait.  The names are the ones the macros and plugins actually
# publish: PROBING and LEVELING from [gcode_macro] bed_screws/bed_level,
# "CHECKING MESH" from the mesh validation macro, and TARING from the load cell
# probe in feather_z_calibration.  HOMING is included because _HOME_IF_NEEDED
# publishes it before several of these.
PROBING_STATES = frozenset((
    "HOMING", "PROBING", "LEVELING", "CHECKING MESH", "TARING",
))
PERSISTENT_ACTIONS = frozenset((
    "cal.mesh.save", "cal.tuning.save", "z.save", "live_z.save",
    "live_z.save.yes", "mod.apply", "mod.save", "error.restart",
    "error.firmware_restart", z_actions.SAVE.wire_id,
    z_actions.SAFE_SAVE.wire_id,
))
VALID_SUITES = frozenset((
    "FULL", "UI", "COMPONENT", "RENDER", "MOTION", "HEAT", "SCREWS",
    "MESH", "Z", "CONTEXT_PRINT", "CONTEXT_MATERIAL",
))
NONPHYSICAL_SUITES = frozenset(("UI", "COMPONENT", "RENDER"))
EXTENDED_CONTEXT_SUITES = frozenset(("CONTEXT_PRINT", "CONTEXT_MATERIAL"))
UI_FINGERPRINT_FILES = (
    "feather_extruder_calibration.py",
    "feather_feature_benchmark.py",
    "feather_feature_calibration.py",
    "feather_feature_extruder.py",
    "feather_feature_filament.py",
    "feather_feature_manager.py",
    "feather_feature_settings.py",
    "feather_feature_z.py",
    "feather_screen.py",
    "feather_screen_controls.py",
    "feather_screen_pages.py",
    "feather_network_ui.py",
    "feather_update_notification.py",
    "feather_feature_ui_test.py",
    "feather_keyboard.py",
    "feather_mod_settings.py",
    "feather_operation_context_fixtures.py",
    "feather_pagination.py",
)
UI_FINGERPRINT_PACKAGES = ("ui", "ff5m_ui", "feather_ui_test")


class UITestRun:
    """Own all mutable state and callbacks for one requested test run."""

    def __init__(self, host, session_id=None, on_finished=None):
        self.host = host
        self.session_id = (session_id or
                           "%d-%.6f" % (os.getpid(), time.time()))
        self.on_finished = on_finished
        self.running = False
        self.abort_requested = False
        self.suite = None
        self.material = None
        self.phase = "idle"
        self.steps = []
        self.step_index = 0
        self.step_runtime = {}
        self.gcmd = None
        self.run_id = None
        self.run_directory = None
        self.worker = None
        self.capture_number = 0
        self.screen_capture_interval = 0.0
        self.next_periodic_capture = None
        self.periodic_capture_pending = 0
        self.failures = []
        self.started_at = None
        self.snapshot = None
        self.finalizing = False
        self._last_stage_revision = -1
        self._last_stage_signature = None
        self.calibration_stages = []
        self.test_results = {}
        self.renderer_dropped = 0
        self.capture_receipts = {}
        self.context_recorder = None
        self.context_fixture = None
        self.context_print_gcode = None
        self.saved_print_tuning = None
        self.reactor_probe_timer = None
        self.reactor_probe_next = None
        self.reactor_probe_last = None
        self.reactor_probe_window = None
        self.reactor_probe_samples = 0
        self.reactor_probe_lag_total = 0.0
        self.reactor_probe_lag_max = 0.0
        self.reactor_probe_lag_max_eventtime = None
        self.reactor_probe_interval_max = 0.0
        self.reactor_probe_interval_max_eventtime = None
        self.reactor_probe_missed = 0
        self.scenarios = ScenarioCatalog(self)

    @property
    def reactor(self):
        return self.host.reactor

    @property
    def input_blocked(self):
        # Immediate emergency actions are checked before this product lock.
        return self.running and not self.finalizing

    @property
    def theme_update_blocked(self):
        return self.running and not self.finalizing

    def blocks_action(self, action):
        return (self.running and not self.finalizing
                and str(action) in PERSISTENT_ACTIONS)

    def handle_immediate_action(self, page, action):
        return False

    def update(self, eventtime):
        operation = self.host._operation_context_status(eventtime)
        revision = int(operation.get("revision", 0))
        if revision == self._last_stage_revision:
            return
        self._last_stage_revision = revision
        signature = (
            tuple(operation.get("context_types", ())),
            tuple(operation.get("context_path", ())),
            operation.get("current_state"),
        )
        if (not self.running or self.finalizing
                or signature == self._last_stage_signature):
            return
        self._last_stage_signature = signature
        status = self.host._operation_context_text(status=operation)
        if not status:
            status = "IDLE"
        self.calibration_stages.append({
            "time": time.time(), "phase": self.phase, "status": status,
            "context_types": signature[0],
            "context_path": signature[1],
            "current_state": signature[2],
        })
        self.capture_number += 1
        number = self.capture_number
        label = "%s-stage-%s" % (self.phase, status)
        try:
            metadata = self._screen_metadata()
        except Exception:
            logging.exception("[feather_ui_test] unable to snapshot stage")
            return
        # Stage captures settle the framebuffer like semantic ones but are
        # driven by the printer's own operation context, not by a test step.
        # artifact_timing.csv has to separate the two, or a cost measured
        # there cannot be attributed to a capture path.
        metadata["capture_kind"] = "stage"
        self._event("CAPTURE_QUEUED %s eventtime=%.6f" % (label, eventtime))
        self.worker.capture(number, label, metadata,
                            self._stage_capture_finished)

    def _stage_capture_finished(self, result):
        if isinstance(result, Exception):
            self._event("stage capture failed: %s" % result)

    def _periodic_capture_block_reason(self, eventtime):
        try:
            print_time, estimated_time, lookahead_empty = \
                self.host.toolhead.check_busy(eventtime)
        except Exception as exc:
            return "toolhead state unavailable: %s" % exc
        if not lookahead_empty or print_time > estimated_time:
            return "toolhead busy"
        try:
            operation = self.host._operation_context_status(eventtime)
            state = str(operation.get("current_state") or "").upper()
        except Exception as exc:
            return "operation state unavailable: %s" % exc
        if state in PROBING_STATES:
            return "operation state %s" % state
        return None

    def _periodic_capture_tick(self, eventtime):
        if (not self.running or self.finalizing
                or not self.screen_capture_interval
                or self.worker is None
                or self.next_periodic_capture is None
                or eventtime < self.next_periodic_capture):
            return
        self.next_periodic_capture = (
            eventtime + self.screen_capture_interval)
        self.reactor.register_callback(
            self._periodic_capture_tick, self.next_periodic_capture)
        if self.periodic_capture_pending:
            self._event(
                "periodic capture skipped: previous capture still pending")
            return
        blocked = self._periodic_capture_block_reason(eventtime)
        if blocked is not None:
            self._event("periodic capture skipped: %s" % blocked)
            return
        self.periodic_capture_pending += 1
        self.capture_number += 1
        number = self.capture_number
        label = "periodic-%03d" % number
        try:
            self._event("CAPTURE_QUEUED %s eventtime=%.6f" % (
                label, eventtime))
            metadata = {
                "time": time.time(),
                "phase": self.phase,
                "page": self.host.page.name,
                "generation": getattr(self.host.renderer, "generation", None),
                "capture_kind": "periodic",
            }
            self.worker.capture(
                number, label, metadata,
                lambda result, name=label:
                self._periodic_capture_finished(name, result),
                settle=False)
        except Exception as exc:
            self.periodic_capture_pending = max(
                0, self.periodic_capture_pending - 1)
            self._event("periodic capture failed: %s" % exc)

    def _periodic_capture_finished(self, label, result):
        self.periodic_capture_pending = max(
            0, self.periodic_capture_pending - 1)
        if isinstance(result, Exception):
            self._event("periodic capture failed: %s" % result)
            return
        self._event("CAPTURE %s %s" % (label, result["file"]))

    def _start_reactor_probe(self):
        now = self.reactor.monotonic()
        self.reactor_probe_next = now + REACTOR_PROBE_INTERVAL
        self.reactor_probe_last = now
        self.reactor_probe_window = now
        self.reactor_probe_samples = 0
        self.reactor_probe_lag_total = 0.0
        self.reactor_probe_lag_max = 0.0
        self.reactor_probe_lag_max_eventtime = None
        self.reactor_probe_interval_max = 0.0
        self.reactor_probe_interval_max_eventtime = None
        self.reactor_probe_missed = 0
        self.reactor_probe_timer = self.reactor.register_timer(
            self._reactor_probe_tick, self.reactor_probe_next)

    def _reactor_probe_tick(self, eventtime):
        if (not self.running or self.worker is None
                or self.reactor_probe_next is None):
            return self.reactor.NEVER
        scheduled = self.reactor_probe_next
        lag = max(0.0, eventtime - scheduled)
        callback_interval = max(0.0, eventtime - self.reactor_probe_last)
        skipped = int(lag / REACTOR_PROBE_INTERVAL)
        self.reactor_probe_samples += 1
        self.reactor_probe_lag_total += lag
        if lag >= self.reactor_probe_lag_max:
            self.reactor_probe_lag_max = lag
            self.reactor_probe_lag_max_eventtime = eventtime
        if callback_interval >= self.reactor_probe_interval_max:
            self.reactor_probe_interval_max = callback_interval
            self.reactor_probe_interval_max_eventtime = eventtime
        self.reactor_probe_missed += skipped
        self.reactor_probe_last = eventtime
        self.reactor_probe_next = (
            scheduled + (skipped + 1) * REACTOR_PROBE_INTERVAL)
        if (eventtime - self.reactor_probe_window
                >= REACTOR_PROBE_FLUSH_INTERVAL):
            samples = self.reactor_probe_samples
            step = (self.steps[self.step_index]
                    if 0 <= self.step_index < len(self.steps) else None)
            self.worker.telemetry(
                "reactor",
                ("time", "eventtime", "scheduled", "lag_ms",
                 "average_lag_ms", "max_lag_ms", "max_lag_eventtime",
                 "max_interval_ms", "max_interval_eventtime",
                 "missed_deadlines", "samples", "phase", "step_index",
                 "step", "periodic_capture_pending", "captures_queued",
                 "captures_started", "captures_finished"),
                {
                    "time": time.time(), "eventtime": eventtime,
                    "scheduled": scheduled, "lag_ms": lag * 1000.0,
                    "average_lag_ms": (
                        self.reactor_probe_lag_total * 1000.0 / samples),
                    "max_lag_ms": self.reactor_probe_lag_max * 1000.0,
                    "max_lag_eventtime": (
                        self.reactor_probe_lag_max_eventtime),
                    "max_interval_ms": (
                        self.reactor_probe_interval_max * 1000.0),
                    "max_interval_eventtime": (
                        self.reactor_probe_interval_max_eventtime),
                    "missed_deadlines": self.reactor_probe_missed,
                    "samples": samples,
                    "phase": self.phase,
                    "step_index": self.step_index,
                    "step": None if step is None else step.get("label"),
                    "periodic_capture_pending": (
                        self.periodic_capture_pending),
                    "captures_queued": self.worker.captures_queued,
                    "captures_started": self.worker.captures_started,
                    "captures_finished": self.worker.captures_finished,
                })
            self.reactor_probe_window = eventtime
            self.reactor_probe_samples = 0
            self.reactor_probe_lag_total = 0.0
            self.reactor_probe_lag_max = 0.0
            self.reactor_probe_lag_max_eventtime = None
            self.reactor_probe_interval_max = 0.0
            self.reactor_probe_interval_max_eventtime = None
            self.reactor_probe_missed = 0
        return self.reactor_probe_next

    def _stop_reactor_probe(self):
        timer = self.reactor_probe_timer
        self.reactor_probe_timer = None
        self.reactor_probe_next = None
        if timer is not None:
            self.reactor.unregister_timer(timer)

    def on_gcode_output(self, message):
        pass

    def on_render_receipt(self, receipt, eventtime):
        token = str(getattr(receipt, "token", ""))
        if self.running and token.startswith("ui-test:"):
            self.capture_receipts[token] = receipt

    def on_print_state_changed(self, old_state, new_state, stats_state):
        if (self.running and self.suite != "CONTEXT_PRINT"
                and new_state != PrintState.IDLE):
            self.abort_requested = True

    def safety_active_reasons(self, eventtime):
        return (("ui-test",) if self.running and not self.finalizing
                and self.phase not in ("ui", "component") else ())

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

    def get_status(self):
        step = (self.steps[self.step_index]
                if 0 <= self.step_index < len(self.steps) else None)
        return {
            "running": bool(self.running),
            "finalizing": bool(self.finalizing),
            "run_id": self.run_id,
            "directory": self.run_directory,
            "suite": self.suite,
            "phase": self.phase,
            "step": None if step is None else step.get("label"),
            "step_index": self.step_index,
            "step_count": len(self.steps),
        }

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

    def run(self, gcmd, suite, material, confirm, encoded_cases="",
            screen_capture_interval=0.0):
        if self.running:
            raise gcmd.error("Feather UI test is already running")
        if suite not in VALID_SUITES:
            raise gcmd.error("Unknown Feather UI test SUITE=%s" % suite)
        required_confirm = 2 if suite in EXTENDED_CONTEXT_SUITES else 1
        if confirm != required_confirm:
            raise gcmd.error(
                "Feather UI test SUITE=%s requires CONFIRM=%d" % (
                    suite, required_confirm))
        if encoded_cases and suite != "COMPONENT":
            raise gcmd.error("CASES is supported only by SUITE=COMPONENT")
        try:
            screen_capture_interval = float(screen_capture_interval)
        except (TypeError, ValueError):
            raise gcmd.error("CAPTURE_INTERVAL must be a number")
        if (not math.isfinite(screen_capture_interval)
                or screen_capture_interval < 0.0
                or (screen_capture_interval != 0.0
                    and screen_capture_interval < 5.0)
                or screen_capture_interval > 300.0):
            raise gcmd.error(
                "CAPTURE_INTERVAL must be 0 or between 5 and 300 seconds")
        self._preflight(suite, hardware_targets=False)
        self.suite = suite
        self.screen_capture_interval = screen_capture_interval
        self.component_cases = self.scenarios._decode_component_cases(
            encoded_cases) if suite == "COMPONENT" else ()
        self.material = self._resolve_material(material, suite)
        self.context_print_gcode = None
        self.saved_print_tuning = None
        if suite == "CONTEXT_PRINT":
            self.context_print_gcode = load_context_print_gcode(self.material)
            self.saved_print_tuning = capture_print_tuning(
                self.host, self.reactor.monotonic())
        self.gcmd = gcmd
        self.started_at = time.time()
        self.abort_requested = False
        self.failures = []
        self.step_index = 0
        self.step_runtime = {}
        self.capture_number = 0
        self.next_periodic_capture = None
        self.periodic_capture_pending = 0
        self.calibration_stages = []
        self.test_results = {}
        self.capture_receipts = {}
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
        self.context_fixture = ContextTestFixture(
            self.host, self.reactor, self.run_id, self.material,
            changed=self._persist_resource_marker,
            print_gcode=self.context_print_gcode,
            saved_print_tuning=self.saved_print_tuning)
        self.steps = self.scenarios.build_steps(suite)
        directory_created = False
        try:
            os.makedirs(ARTIFACT_ROOT, exist_ok=True)
            if shutil.disk_usage(ARTIFACT_ROOT).free < 64 * 1024 * 1024:
                raise gcmd.error("Less than 64 MiB is free below /data")
            self.run_directory = os.path.join(ARTIFACT_ROOT, self.run_id)
            os.makedirs(self.run_directory)
            directory_created = True
            _atomic_json(ACTIVE_MARKER, self._marker("starting"))
            _atomic_json(
                os.path.join(self.run_directory, "environment.json"),
                self._environment())
            self.worker = ArtifactWorker(self.reactor, self.run_directory)
            self._attach_context_recorder()
            self.running = True
            self.finalizing = False
            self._start_reactor_probe()
            self.next_periodic_capture = (
                self.reactor.monotonic() + self.screen_capture_interval
                if self.screen_capture_interval else None)
            if self.next_periodic_capture is not None:
                self.reactor.register_callback(
                    self._periodic_capture_tick,
                    self.next_periodic_capture)
            self._last_stage_revision = -1
            self._last_stage_signature = None
            self._event("run started suite=%s material=%s" %
                        (suite, self.material or "n/a"))
            gcmd.respond_info(
                "Feather UI test started: suite=%s artifacts=%s" %
                (suite, self.run_directory))
            self._schedule(0.0)
        except Exception:
            self.running = False
            self._stop_reactor_probe()
            self._detach_context_recorder()
            if self.worker is not None:
                self.worker.stop()
                self.worker = None
            self._discard_active_marker()
            if directory_created:
                try:
                    shutil.rmtree(self.run_directory)
                except OSError:
                    logging.exception(
                        "[feather_ui_test] unable to remove failed run setup")
            self.run_directory = None
            raise

    def _persist_resource_marker(self):
        if (self.run_directory is not None and self.running
                and self.worker is not None):
            self.worker.marker(self._marker(self.phase))

    def _discard_active_marker(self):
        try:
            with open(ACTIVE_MARKER, "r", encoding="utf-8") as stream:
                marker = json.load(stream)
            if (marker.get("run_id") != self.run_id
                    or marker.get("session") != self.session_id):
                return
            os.unlink(ACTIVE_MARKER)
        except (OSError, ValueError):
            pass

    def _preflight(self, suite, hardware_targets=True):
        if self.host.print_state != PrintState.IDLE:
            raise RuntimeError("Printer must be idle")
        state = str(self.host.print_stats.get_status(
            self.reactor.monotonic()).get("state", "")).lower()
        if state in ("printing", "paused"):
            raise RuntimeError("A print is active")
        if self.host.virtual_sdcard.is_active():
            raise RuntimeError("Virtual SD is active")
        # A pause flag left over from an earlier print survives every state a
        # print goes through, and it silently breaks PAUSE for the whole
        # session. The runner must neither inherit nor produce one.
        if self.host.pause_resume.get_status(
                self.reactor.monotonic()).get("is_paused"):
            raise RuntimeError("Printer is paused")
        if getattr(self.host, "command_depth", 0):
            raise RuntimeError("Another Feather command is active")
        if not getattr(self.host.renderer, "active", False):
            raise RuntimeError("Feather renderer is not active")
        status = self.host.toolhead.get_status(self.reactor.monotonic())
        if abs(float(status.get("velocity", 0.0) or 0.0)) > 0.0001:
            raise RuntimeError("Toolhead is moving")
        if hardware_targets and suite not in NONPHYSICAL_SUITES:
            operation_context = getattr(self.host, "operation_context", None)
            if operation_context is None:
                raise RuntimeError("Operation context is not configured")
            if operation_context.get_status(
                    self.reactor.monotonic()).get("contexts"):
                raise RuntimeError("Operation context stack is not empty")
            if suite in ("FULL", "MESH", "CONTEXT_PRINT",
                         "CONTEXT_MATERIAL") and getattr(
                    self.host, "bed_mesh", None) is None:
                raise RuntimeError("Bed mesh is not configured")
            if suite in ("FULL", "SCREWS", "MESH", "Z",
                         "CONTEXT_PRINT", "CONTEXT_MATERIAL") and getattr(
                    self.host, "probe", None) is None:
                raise RuntimeError("Probe is not configured")
            extruder = self.host.extruder.get_status(self.reactor.monotonic())
            bed = self.host.heater_bed.get_status(self.reactor.monotonic())
            if float(extruder.get("target", 0.0)) > 0.0 or float(
                    bed.get("target", 0.0)) > 0.0:
                raise RuntimeError("Turn heaters off before hardware tests")
            if suite in EXTENDED_CONTEXT_SUITES:
                if not getattr(self.host, "heating_materials", ()):
                    raise RuntimeError("No heating material profiles are available")
                if suite == "CONTEXT_MATERIAL" and not getattr(
                        self.host, "cold_pull_materials", ()):
                    raise RuntimeError("No cold-pull material profiles are available")
                resurrection = getattr(self.host, "resurrection", None)
                if (resurrection is not None
                        and os.path.isfile(getattr(
                            resurrection, "file_path", ""))):
                    raise RuntimeError(
                        "A foreign recovery checkpoint already exists")
                if suite == "CONTEXT_PRINT":
                    profiles = self.host.bed_mesh.get_status(
                        self.reactor.monotonic()).get("profiles", ())
                    if "auto" not in profiles:
                        raise RuntimeError(
                            "Bed mesh profile 'auto' is required")
                    if resurrection is None or not getattr(
                            resurrection, "enabled", False):
                        raise RuntimeError("Print recovery is not enabled")

    def _resolve_material(self, requested, suite):
        needs_material = suite in (
            "FULL", "HEAT", "MESH", "CONTEXT_PRINT", "CONTEXT_MATERIAL")
        if not needs_material:
            return requested or None
        materials = tuple(self.host.heating_materials)
        if not materials:
            raise RuntimeError("No heating materials are enabled")
        material = requested.upper() if requested else self.host._current_material()
        # CONTEXT_PRINT prints one fixed model per material, so substituting
        # the first enabled material would print it with the wrong fixture
        # temperatures, flow, and tuning. Every other suite may fall back.
        if (material not in materials and not requested
                and suite != "CONTEXT_PRINT"):
            material = materials[0]
        if material not in materials:
            raise RuntimeError(
                "Unknown or inactive heating material for %s: %s" %
                (suite, material))
        if suite == "CONTEXT_MATERIAL":
            cold_pull = tuple(self.host.cold_pull_materials)
            if requested and material not in cold_pull:
                raise RuntimeError(
                    "Material has no active cold-pull profile: %s" % material)
            if material not in cold_pull:
                shared = [name for name in materials if name in cold_pull]
                if not shared:
                    raise RuntimeError(
                        "Heating and cold-pull profiles have no common material")
                material = shared[0]
        return material

    def _capture_original_state(self):
        self.snapshot = PrinterStateSnapshot.capture(
            self.host, self.reactor)

    def _marker(self, phase):
        return {
            "run_id": self.run_id, "pid": os.getpid(), "suite": self.suite,
            "session": self.session_id,
            "hardware": self.suite not in NONPHYSICAL_SUITES,
            "phase": phase,
            "runtime_z": (0.0 if self.snapshot is None
                          else self.snapshot.runtime_z),
            "mesh_profile": ("" if self.snapshot is None
                             else self.snapshot.mesh_profile),
            "directory": self.run_directory,
            "resources": ({} if self.context_fixture is None
                          else self.context_fixture.marker_state()),
            "updated_at": time.time(),
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
        recover_interrupted_context_resources(self.host, marker)
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
            "material": self.material,
            "pid": os.getpid(),
            "screen_capture_interval": self.screen_capture_interval,
            "software_version": start_args.get("software_version"),
            "ui_fingerprint": self._ui_fingerprint(),
            "theme": getattr(self.host.renderer, "theme_name", None),
            "page": getattr(self.host.page, "name", str(self.host.page)),
            "heating_materials": self.host.heating_materials,
            "heating_profiles": self.host.heating_profiles,
            "current_material": self.host._current_material(),
            "print": self.host.print_stats.get_status(now),
            "toolhead": self.host.toolhead.get_status(now),
            "renderer": self.host.renderer.get_status(),
        }

    @staticmethod
    def _ui_fingerprint():
        """Hash the deployed UI/framework sources only when a test starts."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        files = []
        for relative in UI_FINGERPRINT_FILES:
            path = os.path.join(root, relative)
            if os.path.isfile(path):
                files.append((relative, path))
        for package in UI_FINGERPRINT_PACKAGES:
            package_root = os.path.join(root, package)
            for current, directories, names in os.walk(package_root):
                directories[:] = [
                    name for name in directories if name != "__pycache__"]
                for name in names:
                    if not name.endswith(".py"):
                        continue
                    path = os.path.join(current, name)
                    relative = os.path.relpath(path, root)
                    files.append((relative, path))
        digest = hashlib.sha256()
        for relative, path in sorted(files):
            digest.update(relative.replace(os.sep, "/").encode("utf-8"))
            digest.update(b"\0")
            with open(path, "rb") as stream:
                for chunk in iter(lambda: stream.read(128 * 1024), b""):
                    digest.update(chunk)
            digest.update(b"\0")
        return digest.hexdigest()

    def _attach_context_recorder(self):
        manager = getattr(self.host, "operation_context", None)
        if manager is None:
            return
        self.context_recorder = OperationContextRecorder(manager)
        self.context_recorder.attach()

    def _detach_context_recorder(self):
        recorder = self.context_recorder
        if recorder is not None:
            recorder.detach()

    def _start_context_scenario(self, name, fixtures):
        if self.context_recorder is not None:
            self.context_recorder.start_scenario(name, fixtures)

    def _finish_context_scenario(self):
        if self.context_recorder is not None:
            self.context_recorder.finish_scenario()

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
        self.phase = step["phase"]
        try:
            if not self.step_runtime.get("trace_started"):
                self.step_runtime["trace_started"] = True
                self._event(
                    "STEP_START index=%d kind=%s label=%s eventtime=%.6f" % (
                        self.step_index, step["kind"], step["label"],
                        eventtime))
            if self.worker is not None:
                self.worker.marker(self._marker(self.phase))
            kind = step["kind"]
            if kind == "call":
                step["callback"]()
                self._step_passed(step, step.get("delay", 0.15))
            elif kind in ("tap", "tap_label", "prompt_tap"):
                if kind == "tap":
                    action = step.get("action")
                elif kind == "tap_label":
                    action = self.scenarios._action_for_label(
                        step["button_label"])
                else:
                    action = self.scenarios._prompt_action_for_label(
                        step["button_label"])
                self._tap(action)
                self.reactor.register_callback(
                    lambda now, expected=step.get("page"), item=step:
                    self._after_tap(now, item, expected),
                    eventtime + 0.22)
            elif kind == "wait":
                if step["predicate"]():
                    self._step_passed(step, 0.05)
                else:
                    deadline = self.step_runtime.setdefault(
                        "wait_deadline", eventtime + step["timeout"])
                    if eventtime >= deadline:
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
            if self.host.page == ScreenPage.MESSAGE and expected != ScreenPage.MESSAGE:
                raise RuntimeError(str(getattr(
                    self.host, "message", "Action opened a message")))
            if self.host.page == ScreenPage.ERROR:
                raise RuntimeError(str(getattr(
                    self.host, "error_message", "Action opened an error")))
            expected_seen = self.step_runtime.get(
                "expected_page_seen", expected is None)
            if not expected_seen and self.host.page == expected:
                self.step_runtime["expected_page_seen"] = True
                expected_seen = True
            operation_active = (
                int(getattr(self.host, "command_depth", 0)) > 0
                or getattr(self.host, "busy_message", None) is not None)
            if operation_active:
                deadline = self.step_runtime.setdefault(
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
            self.step_runtime = {}
            self._event("PASS %s" % step["label"])
            self._schedule(0.02)
        except Exception as exc:
            self.failures.append({"step": step["label"], "error": str(exc)})
            self._complete("failed", str(exc))

    def _step_passed(self, step, delay, advance=True):
        if advance:
            self.step_index += 1
            self.step_runtime = {}
        self._event("PASS %s" % step["label"])
        if advance:
            self._schedule(delay)

    def _capture(self, step):
        if self.step_runtime.get("capture_pending"):
            return
        renderer_status = self.host.renderer.get_status()
        target = self.step_runtime.setdefault(
            "render_target", renderer_status["submitted_batches"])
        settled = (renderer_status["rendered_batches"]
                   + renderer_status["coalesced_batches"]
                   + renderer_status["dropped_batches"])
        if renderer_status["dropped_batches"] > self.renderer_dropped:
            raise RuntimeError("Render batch dropped before capture")
        if settled < target:
            deadline = self.step_runtime.setdefault(
                "render_deadline", self.reactor.monotonic() + 6.0)
            if self.reactor.monotonic() >= deadline:
                raise RuntimeError("Renderer did not settle before capture")
            self._schedule(0.02)
            return
        token = self.step_runtime.get("capture_receipt")
        if token is None:
            token = "ui-test:%s:%d:%d" % (
                self.run_id or os.getpid(), self.step_index,
                self.capture_number + 1)
            command = (
                "--batch flush --receipt %s --receipt-phase presented" %
                token)
            accepted = self.host.renderer.send(
                (command,), kind="state", key="ui-test-capture-barrier")
            if not accepted:
                raise RuntimeError("Unable to queue capture receipt")
            self.step_runtime["capture_receipt"] = token
            self.step_runtime["receipt_deadline"] = (
                self.reactor.monotonic() + CAPTURE_RECEIPT_TIMEOUT)
            self._schedule(0.02)
            return
        receipt = self.capture_receipts.get(token)
        if receipt is None:
            if self.reactor.monotonic() >= self.step_runtime[
                    "receipt_deadline"]:
                raise RuntimeError("Typer did not present frame before capture")
            self._schedule(0.02)
            return
        del self.capture_receipts[token]
        if not bool(getattr(receipt, "success", False)):
            raise RuntimeError("Typer failed to present frame before capture")
        self.step_runtime["capture_pending"] = True
        self.renderer_dropped = renderer_status["dropped_batches"]
        self.capture_number += 1
        metadata = self._screen_metadata()
        if step.get("case_id"):
            metadata["case_id"] = step["case_id"]
        self._event("CAPTURE_QUEUED %s eventtime=%.6f" % (
            step["label"], self.reactor.monotonic()))
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
        self.step_runtime = {}
        self._event("CAPTURE %s %s" % (step["label"], result["file"]))
        self._schedule(0.02)

    def _screen_metadata(self):
        renderer = self.host.renderer
        renderer_status = renderer.get_status()
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
            "semantic_page_id": renderer_status.get("semantic_page_id"),
            "buttons": buttons,
            "hitboxes": hitboxes,
            "toggles": _jsonable(getattr(renderer, "_toggles", {})),
            "renderer": renderer_status,
            "temperatures": self._temperatures(),
            "position": self._position(),
        }

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
            self.worker.log(
                "TEMPERATURE %s" % json.dumps(values, sort_keys=True))
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
        self.host._start_touch_action(action)

    def _temperature_wait_active(self):
        wait = getattr(self.host, "temperature_wait", None)
        return bool(wait is not None and getattr(
            wait, "variables", {}).get("active", False))

    def _restore_context_runtime(self):
        if self.context_fixture is not None:
            self.context_fixture.restore(self.suite)

    def _restore_state(self):
        first_error = None
        self.scenarios.restore_synthetic_state()
        try:
            self._restore_context_runtime()
        except Exception as exc:
            first_error = exc
        try:
            if self.snapshot is not None:
                self.snapshot.restore(
                    self.host, self.reactor,
                    self.suite not in NONPHYSICAL_SUITES)
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
        self._stop_reactor_probe()
        recorder = self.context_recorder
        if recorder is not None:
            recorder.abort_active(reason or outcome)
            operation_context = recorder.report()
            recorder.detach()
        else:
            operation_context = {"passed": True, "scenarios": []}
        context_summary = {
            "passed": operation_context["passed"],
            "scenario_count": len(operation_context["scenarios"]),
            "scenarios": [{
                "scenario": item.get("scenario"),
                "passed": item.get("passed", False),
                "fixture": item.get("fixture"),
                "variant": item.get("variant"),
                "diagnostic": item.get("diagnostic"),
            } for item in operation_context["scenarios"]],
        }
        summary = {
            "run_id": self.run_id, "suite": self.suite,
            "material": self.material,
            "outcome": outcome,
            "screen_capture_interval": self.screen_capture_interval,
            "reason": reason, "failures": self.failures,
            "calibration_stages": self.calibration_stages,
            "test_results": self.test_results,
            "operation_context": context_summary,
            "_operation_context_artifact": operation_context,
            "started_at": self.started_at, "finished_at": time.time(),
            "duration": time.time() - self.started_at,
        }
        self.phase = "finalizing"
        self.worker.finish(summary, self._finished)

    def _finished(self, result):
        self._detach_context_recorder()
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
        self.context_print_gcode = None
        self.saved_print_tuning = None
        if self.worker is not None:
            self.worker.stop()
        self.worker = None
        if gcmd is not None:
            gcmd.respond_info(
                "Feather UI test %s: %s; screenshots=%d; artifacts=%s" %
                (outcome, message, screenshots, directory))
        if self.on_finished is not None:
            self.on_finished(self)

    def _event(self, message):
        logging.info("[feather_ui_test] %s", message)
        if self.worker is not None:
            self.worker.log(message)

    def deactivate(self):
        if self.running and not self.finalizing:
            self._complete("aborted", "feature deactivated")
            return
        self.abort_requested = True
        if self.context_recorder is not None:
            self.context_recorder.abort_active("feature deactivated")
            self.context_recorder.detach()
        if self.worker is not None:
            self.worker.stop()
            self.worker = None
        self._stop_reactor_probe()
        self.running = False
