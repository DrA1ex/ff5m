## Session state and pure calculations for Feather Z-offset calibration.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from collections import OrderedDict
from decimal import Decimal, ROUND_HALF_UP
import logging

try:
    from .ui import Page, PrintState
    from .ff5m_ui.z_offset import runtime as z_offset_ui
    from .ff5m_ui.z_offset.constants import PAPER_STEPS
except (ImportError, ValueError):
    from ui import Page, PrintState
    from ff5m_ui.z_offset import runtime as z_offset_ui
    from ff5m_ui.z_offset.constants import PAPER_STEPS


ZONE_POINTS = (
    ("front_left", "FRONT LEFT", -94.0, -94.0),
    ("front_right", "FRONT RIGHT", 94.0, -94.0),
    ("center", "CENTER", 0.0, 0.0),
    ("rear_left", "REAR LEFT", -94.0, 94.0),
    ("rear_right", "REAR RIGHT", 94.0, 94.0),
)
ZONE_BY_KEY = dict((point[0], point) for point in ZONE_POINTS)
POSITIONAL_WARNING = 0.025
PRESSURE_WARN = 800.0
PRESSURE_REARM = 600.0
SAFE_Z_CLEARANCE = 5.0
SAFE_Z_ADJUST_STEP = 1.0


def calculate_z_offset(paper_contact_z, probe_trigger_z,
                       configured_probe_z_offset):
    """Apply Klipper's PROBE_CALIBRATE result formula."""
    return (float(paper_contact_z) - float(probe_trigger_z)
            + float(configured_probe_z_offset))


def calculate_z_offset_from_reference(paper_contact_z, reference_z,
                                      reference_base_z,
                                      configured_probe_z_offset):
    """Calculate a candidate from a displayed paper-test reference."""
    return (float(paper_contact_z) - float(reference_z)
            + float(reference_base_z) + float(configured_probe_z_offset))


def rounded_average(values):
    values = tuple(float(value) for value in values)
    if not values:
        return None
    return round_mm(sum(values) / len(values))


def round_mm(value):
    return float(Decimal(str(float(value))).quantize(
        Decimal("0.001"), rounding=ROUND_HALF_UP))


class PressureHysteresis:
    def __init__(self, warning=PRESSURE_WARN, rearm=PRESSURE_REARM):
        self.warning = float(warning)
        self.rearm = float(rearm)
        self.armed = True

    def update(self, value, suppressed=False):
        value = float(value)
        if value < self.rearm:
            self.armed = True
        if suppressed or not self.armed or value <= self.warning:
            return False
        self.armed = False
        return True


class ZCalibrationSession:
    """Own one idle calibration without changing persistent state early."""
    def __init__(self):
        self.active = False
        self.prepared = False
        self.original_runtime_offset = 0.0
        self.original_mesh = None
        self.original_mesh_profile = ""
        self.probe_z_offset = 0.0
        self.load_zoffset = False
        self.safe_z = 10.0
        self.safe_z_trigger = None
        self.safe_z_candidate = None
        self.safe_z_probing = False
        self.results = OrderedDict()
        self.selected = None
        self.zone = None
        self.trigger_z = None
        self.reference_z = None
        self.reference_base_z = 0.0
        self.local_z = None
        self.step = PAPER_STEPS[1]
        self.start_mode = None
        self.probing = False
        self.moving_to_start = False
        self.dialog = None
        self.pressure = PressureHysteresis()

    def begin(self, runtime_offset, mesh_object, mesh_profile,
              probe_z_offset, load_zoffset, safe_z=10.0):
        self.__init__()
        self.active = True
        self.original_runtime_offset = float(runtime_offset)
        self.original_mesh = mesh_object
        self.original_mesh_profile = str(mesh_profile or "")
        self.probe_z_offset = float(probe_z_offset)
        self.load_zoffset = bool(load_zoffset)
        self.safe_z = abs(float(safe_z))

    def clear(self):
        self.__init__()

    @property
    def safe_z_ready(self):
        return (self.safe_z_trigger is not None
                and self.safe_z_candidate is not None
                and not self.safe_z_probing)

    def set_safe_z_trigger(self, trigger_z, clearance=SAFE_Z_CLEARANCE):
        self.safe_z_trigger = float(trigger_z)
        self.safe_z_candidate = self.safe_z_trigger + abs(float(clearance))
        return self.safe_z_candidate

    def adjust_safe_z(self, delta):
        if self.safe_z_candidate is None:
            raise ValueError("Probe the bed before adjusting Safe Z")
        minimum = self.safe_z_trigger + SAFE_Z_ADJUST_STEP
        self.safe_z_candidate = max(
            minimum, self.safe_z_candidate + float(delta))
        return self.safe_z_candidate

    def accept_safe_z(self):
        if self.safe_z_candidate is None:
            raise ValueError("Probe the bed before saving Safe Z")
        self.safe_z = round_mm(self.safe_z_candidate)
        return self.safe_z

    def choose_zone(self, key):
        if key not in ZONE_BY_KEY:
            raise ValueError("Unknown Z calibration zone")
        self.zone = key
        self.trigger_z = None
        self.reference_z = None
        self.reference_base_z = 0.0
        self.local_z = None

    def set_trigger(self, trigger_z, retract=0.5):
        self.trigger_z = float(trigger_z)
        self.reference_z = self.trigger_z
        self.reference_base_z = 0.0
        self.local_z = float(retract)
        self.start_mode = "probe"

    def set_manual_start(self, height=1.5):
        """Use the homed Z origin when a load-cell probe is unavailable.

        The visible reference is the actual 1.5 mm starting coordinate.  Its
        base is retained separately so paper moves still calculate a value in
        the normal global Z-offset coordinate system.
        """
        self.trigger_z = None
        self.reference_z = float(height)
        self.reference_base_z = self.reference_z
        self.local_z = 0.0
        self.start_mode = "manual"

    @property
    def ready_for_paper_test(self):
        return (self.reference_z is not None and self.local_z is not None
                and not self.probing and not self.moving_to_start)

    @property
    def paper_contact_z(self):
        if self.reference_z is None or self.local_z is None:
            return None
        return self.reference_z + self.local_z

    @property
    def candidate(self):
        contact = self.paper_contact_z
        if contact is None:
            return None
        return calculate_z_offset_from_reference(
            contact, self.reference_z, self.reference_base_z,
            self.probe_z_offset)

    def adjust(self, delta):
        if self.local_z is None:
            raise ValueError("Probe the zone before adjusting Z")
        self.local_z += float(delta)
        return self.reference_z + self.local_z

    def reset(self):
        if self.reference_z is None:
            raise ValueError("Probe the zone before resetting Z")
        self.local_z = -(self.reference_base_z + self.probe_z_offset)
        return self.reference_z + self.local_z

    def accept(self):
        if self.zone is None or self.candidate is None:
            raise ValueError("Probe the zone before accepting it")
        self.results[self.zone] = round_mm(self.candidate)
        self._select_default()
        return self.results[self.zone]

    def _select_default(self):
        if len(self.results) == 1:
            self.selected = next(iter(self.results))
        elif len(self.results) > 1:
            self.selected = "average"
        else:
            self.selected = None

    @property
    def average(self):
        return rounded_average(self.results.values())

    @property
    def spread(self):
        if len(self.results) < 2:
            return 0.0
        values = tuple(self.results.values())
        return max(values) - min(values)

    @property
    def selected_value(self):
        if self.selected == "average":
            return self.average
        return self.results.get(self.selected)

    def selection_options(self):
        options = []
        if len(self.results) > 1:
            options.append("average")
        options.extend(self.results.keys())
        return options

    def select_next(self):
        options = self.selection_options()
        if not options:
            self.selected = None
            return None
        if self.selected not in options:
            self.selected = options[0]
        else:
            self.selected = options[
                (options.index(self.selected) + 1) % len(options)]
        return self.selected


class FeatherZCalibrationMixin:
    """Render and execute the multi-stage idle Z-calibration workflow."""
    def _safe_z(self):
        session = getattr(self, "z_calibration", None)
        if session is not None and session.active:
            return abs(float(session.safe_z))
        return abs(float(self._setting("safe_z", 10.0)))

    def _safe_z_preparation_height(self):
        return self._safe_z() * 2.0

    def _safe_z_move_command(self, speed=600):
        return "MOVE_SAFE Z=%g ABSOLUTE=1 F=%d" % (
            self._safe_z(), int(speed))

    @staticmethod
    def _z_zone_labels():
        return dict((key, label) for key, label, _x, _y in ZONE_POINTS)

    def _z_summary_ui_state(self):
        session = self.z_calibration
        return {
            z_offset_ui.SummaryState.ZONE_LABELS: self._z_zone_labels(),
            z_offset_ui.SummaryState.RESULTS: dict(session.results),
            z_offset_ui.SummaryState.SPREAD: float(session.spread),
            z_offset_ui.SummaryState.POSITIONAL_WARNING: float(
                POSITIONAL_WARNING),
            z_offset_ui.SummaryState.SELECTED: session.selected,
            z_offset_ui.SummaryState.AVERAGE: session.average,
            z_offset_ui.SummaryState.LOAD_ZOFFSET: bool(
                session.load_zoffset),
            z_offset_ui.SummaryState.DIALOG: session.dialog,
        }

    def _render_z_summary(self):
        commands = self.renderer.begin_page("Z offset zones", back=True)
        commands += z_offset_ui.render_summary(
            self.renderer, self._z_summary_ui_state())
        self.renderer.send(commands)

    def _render_z_briefing(self):
        commands = self.renderer.begin_page(
            "Z offset calibration", back=True)
        commands += z_offset_ui.render_briefing(self.renderer, {
            z_offset_ui.BriefingState.SAFE_Z: self._safe_z(),
        })
        self.renderer.send(commands)

    def _safe_z_briefing_ui_state(self):
        return {
            z_offset_ui.SafeBriefingState.CURRENT: self._safe_z(),
            z_offset_ui.SafeBriefingState.START:
                self._safe_z_preparation_height(),
        }

    def _render_safe_z_briefing(self):
        commands = self.renderer.begin_page("Safe Z calibration", back=True)
        commands += z_offset_ui.render_safe_briefing(
            self.renderer, self._safe_z_briefing_ui_state())
        self.renderer.send(commands)

    def _safe_z_ui_state(self):
        session = self.z_calibration
        return {
            z_offset_ui.SafeState.CURRENT: float(session.safe_z),
            z_offset_ui.SafeState.TRIGGER:
                session.safe_z_trigger,
            z_offset_ui.SafeState.CANDIDATE:
                session.safe_z_candidate,
            z_offset_ui.SafeState.PROBING:
                bool(session.safe_z_probing),
            z_offset_ui.SafeState.READY:
                bool(session.safe_z_ready),
        }

    def _render_safe_z(self):
        commands = self.renderer.begin_page("Calibrate Safe Z", back=True)
        commands += z_offset_ui.render_safe(
            self.renderer, self._safe_z_ui_state())
        self.renderer.send(commands)

    def _render_z_paper_briefing(self):
        point = ZONE_BY_KEY[self.z_calibration.zone]
        commands = self.renderer.begin_page(
            "Paper test briefing", back=True)
        commands += z_offset_ui.render_paper_briefing(
            self.renderer, {
                z_offset_ui.PaperBriefingState.ZONE_LABEL: str(point[1]),
                z_offset_ui.PaperBriefingState.MANUAL_START:
                    self._safe_z() / 2.0,
            })
        self.renderer.send(commands)

    def _z_paper_ui_state(self, eventtime=None):
        session = self.z_calibration
        if eventtime is None:
            eventtime = self.reactor.monotonic()
        gauge = self._update_z_weight_gauge(eventtime)
        return {
            z_offset_ui.PaperState.MANUAL:
                session.start_mode == "manual",
            z_offset_ui.PaperState.REFERENCE:
                ("--" if session.reference_z is None
                 else "%+.3f" % session.reference_z),
            z_offset_ui.PaperState.NOZZLE:
                ("--" if session.paper_contact_z is None
                 else "%+.3f" % session.paper_contact_z),
            z_offset_ui.PaperState.CANDIDATE:
                ("--" if session.candidate is None
                 else "%+.3f" % session.candidate),
            z_offset_ui.PaperState.PROBING: bool(session.probing),
            z_offset_ui.PaperState.MOVING_TO_START:
                bool(session.moving_to_start),
            z_offset_ui.PaperState.STEP: float(session.step),
            z_offset_ui.PaperState.MANUAL_START: self._safe_z() / 2.0,
            z_offset_ui.PaperState.READY:
                bool(session.ready_for_paper_test),
            z_offset_ui.PaperState.GAUGE:
                None if gauge is None else dict(gauge),
            z_offset_ui.PaperState.DIALOG: session.dialog,
            z_offset_ui.PaperState.DIALOG_WEIGHT: float(
                getattr(session, "dialog_weight", 0.0)),
        }

    def _render_z_paper(self):
        point = ZONE_BY_KEY[self.z_calibration.zone]
        commands = self.renderer.begin_page(
            "Paper test - %s" % point[1], back=True)
        commands += z_offset_ui.render_paper(
            self.renderer, self._z_paper_ui_state())
        self.renderer.send(commands)

    def _z_offset_head_state(self):
        status = self.toolhead.get_status(self.reactor.monotonic())
        homed = str(status.get("homed_axes", "")).lower()
        position = status.get("position", (0.0, 0.0, 0.0, 0.0))
        return all(axis in homed for axis in "xyz"), position

    def _z_offset_move_commands(self, x, y):
        return "\n".join([
            self._safe_z_move_command(),
            "MOVE_SAFE X=%.1f Y=%.1f ABSOLUTE=1 F=6000" % (x, y),
        ])

    def _move_z_offset_head(self, x, y):
        self._run_blocking_gcode(
            self._z_offset_move_commands(x, y), "POSITIONING HEAD...")

    def _capture_z_mesh(self):
        mesh = getattr(self, "bed_mesh", None)
        if mesh is None:
            return None, ""
        status = mesh.get_status(self.reactor.monotonic())
        return getattr(mesh, "z_mesh", None), status.get("profile_name", "")

    def _restore_z_mesh(self, mesh_object, profile_name):
        mesh = getattr(self, "bed_mesh", None)
        if mesh is None:
            return
        mesh.set_mesh(mesh_object)
        for owner in (mesh, getattr(mesh, "bmc", None),
                      getattr(mesh, "bed_mesh_calibrate", None)):
            if owner is not None and hasattr(owner, "profile_name"):
                owner.profile_name = str(profile_name or "")

    def _start_z_calibration(self):
        self._require_idle()
        self._cancel_delayed_tasks()
        mesh_object, mesh_profile = self._capture_z_mesh()
        runtime = float(self.gcode_move.get_status(
            self.reactor.monotonic())["homing_origin"][2])
        probe_offset = float(getattr(self.probe, "z_offset", -0.25))
        self.z_calibration.begin(
            runtime, mesh_object, mesh_profile, probe_offset,
            self._setting("load_zoffset", 0), self._setting("safe_z", 10.0))
        self.calibration_error = None
        self.calibration_cancel_requested = False
        self.calibration_cancel_dispatched = False
        self.calibration_cancelled = False
        try:
            self._run_script("SET_SKEW CLEAR=1")
            self._run_script("_SET_GCODE_OFFSET Z=0 MOVE=0")
            self._run_script("BED_MESH_CLEAR")
        except Exception:
            try:
                self._run_script(
                    "_SET_GCODE_OFFSET Z=%+.6f MOVE=0" % runtime)
            finally:
                self._restore_z_mesh(mesh_object, mesh_profile)
                self.z_calibration.clear()
            raise
        self._show_page(Page.SAFE_Z_BRIEFING)

    def _start_z_calibration_preparation(self):
        self.print_status_text = "Z OFFSET: PREP"
        self._show_page(Page.CALIBRATION_PROGRESS)
        self.reactor.register_callback(self._run_z_calibration_preparation)

    def _z_preparation_command(self):
        preparation_z = self._safe_z_preparation_height()
        if self.calibration_clean_nozzle:
            nozzle, bed = self._limited_preheat(self.calibration_material)
            return "\n".join((
                '_PRINT_STATUS S="Z OFFSET: PREP"',
                "CLEAR_NOZZLE EXTRUDER_TEMP=%.0f BED_TEMP=%.0f" %
                (nozzle, bed),
                '_PRINT_STATUS S="Z OFFSET: TARE"',
                "MOVE_SAFE Z=%g ABSOLUTE=1 F=600" % preparation_z,
                "LOAD_CELL_TARE",
                '_PRINT_STATUS S="Z OFFSET: READY"',
            ))
        cooldown = float(self._setting("clear_cooldown_temp", 120))
        return "\n".join((
            '_PRINT_STATUS S="Z OFFSET: PREP"',
            "M104 S%.0f" % cooldown,
            '_PRINT_STATUS S="Z OFFSET: HOME"',
            "G28",
            '_PRINT_STATUS S="Z OFFSET: HEAT"',
            "_WAIT_TEMPERATURE CMD=M104 VALUE=%.0f BELOW=2 ABOVE=3" %
            cooldown,
            '_PRINT_STATUS S="Z OFFSET: TARE"',
            "MOVE_SAFE Z=%g ABSOLUTE=1 F=600" % preparation_z,
            "LOAD_CELL_TARE",
            '_PRINT_STATUS S="Z OFFSET: READY"',
        ))

    def _run_z_calibration_preparation(self, eventtime):
        try:
            self._require_idle()
            self._run_script(self._z_preparation_command())
            self.z_calibration.prepared = True
            self._begin_z_weight_gauge()
            self._show_page(Page.Z_OFFSET_BRIEFING)
            return
        except Exception as exc:
            if getattr(self, "shutdown_active", False):
                self.z_calibration.clear()
                return
            cancelled = (self.calibration_cancel_requested
                         and self.calibration_cancel_dispatched)
            if not cancelled:
                logging.exception(
                    "[feather_screen] Z calibration preparation failed")
                self.calibration_error = str(exc)
        try:
            self._finish_z_calibration(None)
        finally:
            if self.print_state != PrintState.DESTROYED:
                if cancelled:
                    self._show_message(
                        "Z-offset heating cancelled",
                        Page.CALIBRATION_HOME)
                else:
                    self._show_message(
                        self.calibration_error or
                        "Z-offset preparation failed",
                        Page.CALIBRATION_HOME)

    def _choose_z_zone(self, key):
        self._require_idle()
        self.z_calibration.choose_zone(key)
        self._show_page(Page.Z_OFFSET_PAPER_BRIEFING)

    def _begin_safe_z_calibration(self, preserve_result=False):
        self._require_idle()
        session = self.z_calibration
        if not preserve_result:
            session.safe_z_trigger = None
            session.safe_z_candidate = None
        session.safe_z_probing = False
        commands = [
            "G28",
            "MOVE_SAFE Z=%g ABSOLUTE=1 F=600" %
            self._safe_z_preparation_height(),
            "MOVE_SAFE X=0 Y=0 ABSOLUTE=1 F=6000",
            "LOAD_CELL_TARE",
        ]
        if preserve_result and session.safe_z_candidate is not None:
            commands.append(
                "MOVE_SAFE Z=%.6f ABSOLUTE=1 F=300" %
                session.safe_z_candidate)
        self._run_blocking_gcode(
            "\n".join(commands), "POSITIONING HEAD...")
        self._show_page(Page.SAFE_Z_CALIBRATION)

    def _continue_after_safe_z(self):
        if self.z_calibration.prepared:
            self._show_page(Page.Z_OFFSET_BRIEFING)
            return
        self._start_z_calibration_preparation()

    def _skip_safe_z_calibration(self):
        self._continue_after_safe_z()

    def _probe_safe_z(self):
        session = self.z_calibration
        if session.safe_z_probing:
            return
        session.safe_z_probing = True
        self._render_safe_z()
        try:
            self._run_blocking_gcode("PROBE SAMPLES=2", "PROBING...")
            status = self.probe.get_status(self.reactor.monotonic())
            trigger = float(status["last_z_result"])
            target = session.set_safe_z_trigger(trigger)
            self._run_script(
                "MOVE_SAFE Z=%.6f ABSOLUTE=1 F=300" % target,
                show_notice=False)
        finally:
            session.safe_z_probing = False
        self._render_safe_z()

    def _adjust_safe_z(self, delta):
        session = self.z_calibration
        old = session.safe_z_candidate
        target = session.adjust_safe_z(delta)
        try:
            self._run_script(
                "MOVE_SAFE Z=%.6f ABSOLUTE=1 F=300" % target,
                show_notice=False)
        except Exception:
            session.safe_z_candidate = old
            raise
        self._render_safe_z()

    def _save_safe_z(self):
        value = self.z_calibration.accept_safe_z()
        self._run_script("SET_MOD PARAM=safe_z VALUE=%.3f" % value)
        self._toast("Safe Z saved %.3f mm" % value)
        self._continue_after_safe_z()

    def _enter_z_zone(self):
        point = ZONE_BY_KEY[self.z_calibration.zone]
        self._move_z_offset_head(point[2], point[3])
        self._show_page(Page.Z_OFFSET_PAPER)

    def _probe_z_zone(self):
        session = self.z_calibration
        if session.probing:
            return
        session.probing = True
        session.dialog = None
        self._render_z_paper()
        try:
            self._run_blocking_gcode("PROBE SAMPLES=2", "PROBING...")
            status = self.probe.get_status(self.reactor.monotonic())
            trigger = float(status["last_z_result"])
            self._run_script(
                "MOVE_SAFE Z=%.6f ABSOLUTE=1 F=300" % (trigger + 0.5),
                show_notice=False)
            session.set_trigger(trigger, 0.5)
        finally:
            session.probing = False
        self._render_z_paper()
        self._check_z_pressure(self.reactor.monotonic())

    def _move_z_manual_start(self):
        session = self.z_calibration
        if session.moving_to_start or session.probing:
            return
        session.moving_to_start = True
        self._render_z_paper()
        try:
            height = self._safe_z() / 2.0
            self._run_blocking_gcode(
                "MOVE_SAFE Z=%.6f ABSOLUTE=1 F=300" % height,
                "MOVING TO %.3f MM..." % height)
            session.set_manual_start(height)
        finally:
            session.moving_to_start = False
        self._render_z_paper()

    def _move_z_paper(self, delta):
        session = self.z_calibration
        target = session.adjust(delta)
        try:
            self._run_script(
                "MOVE_SAFE Z=%.6f ABSOLUTE=1 F=300" % target,
                show_notice=False)
        except Exception:
            session.adjust(-delta)
            raise
        self._render_z_paper()

    def _reset_z_paper(self):
        session = self.z_calibration
        old_local = session.local_z
        target = session.reset()
        try:
            self._run_script(
                "MOVE_SAFE Z=%.6f ABSOLUTE=1 F=300" % target,
                show_notice=False)
        except Exception:
            session.local_z = old_local
            raise
        self._render_z_paper()

    def _accept_z_zone(self):
        result = self.z_calibration.accept()
        self._run_blocking_gcode(
            self._safe_z_move_command(), "LIFTING Z...")
        self._show_page(Page.Z_OFFSET_SUMMARY)
        self._toast("Zone accepted %+.3f mm" % result)

    def _finish_z_calibration(self, saved_offset):
        session = self.z_calibration
        if not session.active:
            return
        runtime = (session.original_runtime_offset if saved_offset is None
                   else float(saved_offset))
        commands = []
        homed, _position = self._z_offset_head_state()
        if homed:
            commands.append(self._safe_z_move_command())
        commands.append("TURN_OFF_HEATERS")
        state_commands = [
            "_SET_GCODE_OFFSET Z=%+.6f MOVE=0" % runtime]
        if saved_offset is not None:
            state_commands += [
                "SET_MOD PARAM=z_offset VALUE=%.3f" % saved_offset,
                "SET_MOD PARAM=load_zoffset VALUE=%d" %
                int(session.load_zoffset),
            ]
        commands.append("\n".join(state_commands))
        mesh_object = session.original_mesh
        mesh_profile = session.original_mesh_profile
        first_error = None
        try:
            for command in commands:
                try:
                    self._run_script(command)
                except Exception as exc:
                    if first_error is None:
                        first_error = exc
                    logging.exception(
                        "[feather_screen] Z calibration cleanup failed: %s",
                        command.splitlines()[0])
        finally:
            try:
                self._restore_z_mesh(mesh_object, mesh_profile)
            finally:
                session.clear()
        if first_error is not None:
            raise first_error

    def _save_z_calibration(self):
        value = self.z_calibration.selected_value
        if value is None:
            raise RuntimeError("Measure and select a Z-offset result first")
        self._finish_z_calibration(value)
        self._show_page(Page.CALIBRATION_HOME)
        self._toast("Z offset saved %+.3f mm" % value)

    def _cancel_z_calibration(self):
        self._finish_z_calibration(None)
        self._show_page(Page.CALIBRATION_HOME)
