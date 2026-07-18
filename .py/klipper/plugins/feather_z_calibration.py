## Session state and pure calculations for Feather Z-offset calibration.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from collections import OrderedDict
from decimal import Decimal, ROUND_HALF_UP
import logging

try:
    from .feather_ui import Page, PrintState
except (ImportError, ValueError):
    from feather_ui import Page, PrintState


ZONE_POINTS = (
    ("front_left", "FRONT LEFT", -94.0, -94.0),
    ("front_right", "FRONT RIGHT", 94.0, -94.0),
    ("center", "CENTER", 0.0, 0.0),
    ("rear_left", "REAR LEFT", -94.0, 94.0),
    ("rear_right", "REAR RIGHT", 94.0, 94.0),
)
ZONE_BY_KEY = dict((point[0], point) for point in ZONE_POINTS)
PAPER_STEPS = (0.005, 0.010, 0.025, 0.050)
POSITIONAL_WARNING = 0.025
PRESSURE_WARN = 800.0
PRESSURE_REARM = 600.0


def calculate_z_offset(paper_contact_z, probe_trigger_z,
                       configured_probe_z_offset):
    """Apply Klipper's PROBE_CALIBRATE result formula."""
    return (float(paper_contact_z) - float(probe_trigger_z)
            + float(configured_probe_z_offset))


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
        self.results = OrderedDict()
        self.selected = None
        self.zone = None
        self.trigger_z = None
        self.local_z = None
        self.step = PAPER_STEPS[1]
        self.briefing_seen = False
        self.probing = False
        self.dialog = None
        self.pressure = PressureHysteresis()

    def begin(self, runtime_offset, mesh_object, mesh_profile,
              probe_z_offset, load_zoffset):
        self.__init__()
        self.active = True
        self.original_runtime_offset = float(runtime_offset)
        self.original_mesh = mesh_object
        self.original_mesh_profile = str(mesh_profile or "")
        self.probe_z_offset = float(probe_z_offset)
        self.load_zoffset = bool(load_zoffset)

    def clear(self):
        self.__init__()

    def choose_zone(self, key):
        if key not in ZONE_BY_KEY:
            raise ValueError("Unknown Z calibration zone")
        self.zone = key
        self.trigger_z = None
        self.local_z = None

    def set_trigger(self, trigger_z, retract=0.5):
        self.trigger_z = float(trigger_z)
        self.local_z = float(retract)

    @property
    def paper_contact_z(self):
        if self.trigger_z is None or self.local_z is None:
            return None
        return self.trigger_z + self.local_z

    @property
    def candidate(self):
        contact = self.paper_contact_z
        if contact is None:
            return None
        return calculate_z_offset(
            contact, self.trigger_z, self.probe_z_offset)

    def adjust(self, delta):
        if self.local_z is None:
            raise ValueError("Probe the zone before adjusting Z")
        self.local_z += float(delta)
        return self.trigger_z + self.local_z

    def reset(self):
        if self.trigger_z is None:
            raise ValueError("Probe the zone before resetting Z")
        self.local_z = -self.probe_z_offset
        return self.trigger_z + self.local_z

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
    def _render_z_summary(self):
        session = self.z_calibration
        commands = self.renderer.begin_page("Z offset zones", back=True)
        geometry = (
            (20, 72, 210, 64), (245, 72, 210, 64),
            (470, 72, 210, 64), (130, 146, 260, 64),
            (410, 146, 260, 64),
        )
        for point, (x, y, width, height) in zip(ZONE_POINTS, geometry):
            key, label = point[:2]
            result = session.results.get(key)
            caption = (label if result is None else
                       "%s  %+.3f" % (label, result))
            commands += self.renderer.button(
                "z.zone.%s" % key, x, y, width, height, caption,
                state=("selected" if result is not None else "enabled"),
                font="JetBrainsMono 8pt")
        if session.spread > POSITIONAL_WARNING:
            commands.append(self.renderer.text(
                350, 230, "POSITIONAL SPREAD %.3f MM - CHECK BED / PROBE" %
                session.spread, "f2c94c", "JetBrainsMono 8pt",
                "center", "middle"))
        elif session.results:
            commands.append(self.renderer.text(
                350, 230, "%d ZONE%s MEASURED" %
                (len(session.results),
                 "" if len(session.results) == 1 else "S"),
                "56656c", "JetBrainsMono 8pt", "center", "middle"))
        else:
            commands.append(self.renderer.text(
                350, 230, "SELECT A POSITION TO START THE PAPER TEST",
                "56656c", "JetBrainsMono 8pt", "center", "middle"))
        selected = session.selected
        if selected == "average":
            selection = "USE AVERAGE  %+.3f" % session.average
        elif selected in ZONE_BY_KEY:
            selection = "USE %s  %+.3f" % (
                ZONE_BY_KEY[selected][1], session.results[selected])
        else:
            selection = "NO RESULT SELECTED"
        selection_state = "enabled" if session.results else "disabled"
        commands += self.renderer.button(
            "z.selection.next", 20, 258, 450, 58, selection,
            state=selection_state, font="JetBrainsMono 8pt")
        commands += self.renderer.button(
            "z.load.toggle", 490, 258, 200, 58,
            "AUTO LOAD: %s" % ("ON" if session.load_zoffset else "OFF"),
            state="selected" if session.load_zoffset else "enabled",
            font="JetBrainsMono 8pt")
        commands += self.renderer.button(
            "z.save", 20, 334, 660, 82, "SAVE SELECTED Z OFFSET",
            state=selection_state, font="JetBrainsMono Bold 12pt")
        if session.dialog == "discard":
            commands += self.renderer.dialog(
                "DISCARD Z CALIBRATION?",
                ("ALL MEASURED ZONE RESULTS WILL BE LOST.",
                 "THE ORIGINAL MESH AND RUNTIME OFFSET WILL BE RESTORED."),
                (("z.discard.cancel", "KEEP", "enabled"),
                 ("z.discard.confirm", "DISCARD", "danger")),
                x=85, y=105, width=630, height=275, tone="danger")
        self.renderer.send(commands)

    def _render_z_briefing(self):
        point = ZONE_BY_KEY[self.z_calibration.zone]
        commands = self.renderer.begin_page("Paper test briefing", back=True)
        lines = (
            "PROBE FINDS THE LOAD-CELL TRIGGER HEIGHT AND SETS LOCAL ZERO.",
            "PLACE NORMAL PRINTER PAPER BETWEEN THE CLEAN NOZZLE AND BED.",
            "CLOSER LOWERS LOCAL Z; FARTHER RAISES IT.",
            "STOP WHEN THE PAPER DRAGS EVENLY, THEN ACCEPT THE ZONE.",
        )
        for index, line in enumerate(lines):
            commands.append(self.renderer.text(
                400, 92 + index * 52, line,
                "d9e4e8" if index else "35d9e6",
                "JetBrainsMono 8pt", "center", "middle"))
        commands.append(self.renderer.text(
            400, 306, "FIRST POSITION: %s" % point[1],
            "b47aff", "JetBrainsMono Bold 12pt", "center", "middle"))
        commands += self.renderer.button(
            "z.briefing.continue", 170, 344, 460, 74,
            "CONTINUE TO PAPER TEST", font="JetBrainsMono Bold 12pt")
        self.renderer.send(commands)

    def _render_z_paper(self):
        session = self.z_calibration
        point = ZONE_BY_KEY[session.zone]
        commands = self.renderer.begin_page(
            "Paper test - %s" % point[1], back=True)
        trigger = ("--" if session.trigger_z is None else
                   "%+.3f" % session.trigger_z)
        local = ("--" if session.local_z is None else
                 "%+.3f" % session.local_z)
        candidate = ("--" if session.candidate is None else
                     "%+.3f" % session.candidate)
        for label, value, x in (
                ("TRIGGER Z", trigger, 20),
                ("LOCAL HEIGHT", local, 245),
                ("Z OFFSET", candidate, 470)):
            commands += self.renderer.panel(
                x, 70, 210, 72, border="295c66", background="050c0f")
            commands.append(self.renderer.text(
                x + 105, 88, label, "35d9e6",
                "JetBrainsMono 8pt", "center", "middle"))
            commands.append(self.renderer.text(
                x + 105, 119, "%s MM" % value, "ffffff",
                "JetBrainsMono Bold 12pt", "center", "middle"))
        commands += self.renderer.button(
            "z.probe", 20, 154, 670, 70, "PROBE",
            state="busy" if session.probing else "danger",
            font="JetBrainsMono Bold 16pt")
        for index, step in enumerate(PAPER_STEPS):
            token = ("%04d" % round(step * 1000)).lstrip("0")
            commands += self.renderer.button(
                "z.step.%s" % token, 20 + index * 168, 238, 158, 48,
                "%.3f MM" % step,
                state=("selected" if step == session.step else "enabled"),
                font="JetBrainsMono 8pt")
        ready = session.trigger_z is not None and not session.probing
        state = "enabled" if ready else "disabled"
        commands += self.renderer.button(
            "z.closer", 20, 300, 325, 66,
            "CLOSER  -%.3f" % session.step,
            state=state, font="JetBrainsMono Bold 12pt")
        commands += self.renderer.button(
            "z.farther", 365, 300, 325, 66,
            "FARTHER  +%.3f" % session.step,
            state=state, font="JetBrainsMono Bold 12pt")
        commands += self.renderer.button(
            "z.reset", 20, 380, 205, 48, "RESET TO 0.000",
            state=state, font="JetBrainsMono 8pt")
        commands += self.renderer.button(
            "z.accept", 245, 380, 445, 48, "ACCEPT ZONE",
            state=state, font="JetBrainsMono Bold 10pt")
        commands += self._z_weight_gauge_commands(self.reactor.monotonic())
        if session.dialog == "pressure":
            weight = getattr(session, "dialog_weight", 0.0)
            commands += self.renderer.dialog(
                "HIGH BED PRESSURE",
                ("CURRENT LOAD: %.0F G" % weight,
                 "MOVE FARTHER AND CHECK THE PAPER / NOZZLE."),
                (("z.pressure.ok", "OK", "danger"),),
                x=95, y=112, width=610, height=260, tone="danger")
        self.renderer.send(commands)

    def _z_offset_head_state(self):
        status = self.toolhead.get_status(self.reactor.monotonic())
        homed = str(status.get("homed_axes", "")).lower()
        position = status.get("position", (0.0, 0.0, 0.0, 0.0))
        return all(axis in homed for axis in "xyz"), position

    @staticmethod
    def _z_offset_move_commands(x, y):
        return "\n".join([
            "MOVE_SAFE Z=5.0 ABSOLUTE=1 F=600",
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
        mesh_object, mesh_profile = self._capture_z_mesh()
        runtime = float(self.gcode_move.get_status(
            self.reactor.monotonic())["homing_origin"][2])
        probe_offset = float(getattr(self.probe, "z_offset", -0.25))
        self.z_calibration.begin(
            runtime, mesh_object, mesh_profile, probe_offset,
            self._setting("load_zoffset", 0))
        self.calibration_error = None
        self.calibration_cancel_requested = False
        self.calibration_cancel_dispatched = False
        self.calibration_cancelled = False
        try:
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
        self.print_status_text = "Z OFFSET: PREP"
        self._show_page(Page.CALIBRATION_PROGRESS)
        self.reactor.register_callback(self._run_z_calibration_preparation)

    def _z_preparation_command(self):
        if self.calibration_clean_nozzle:
            nozzle, bed = self._limited_preheat(self.calibration_material)
            return (
                '_PRINT_STATUS S="Z OFFSET: PREP"\n'
                "CLEAR_NOZZLE EXTRUDER_TEMP=%.0f BED_TEMP=%.0f\n"
                '_PRINT_STATUS S="Z OFFSET: TARE"\n'
                "MOVE_SAFE Z=5.0 ABSOLUTE=1 F=600\n"
                "LOAD_CELL_TARE\n"
                '_PRINT_STATUS S="Z OFFSET: READY"' % (nozzle, bed))
        cooldown = float(self._setting("clear_cooldown_temp", 120))
        return (
            '_PRINT_STATUS S="Z OFFSET: PREP"\n'
            "M104 S%.0f\n"
            '_PRINT_STATUS S="Z OFFSET: HOME"\n'
            "G28\n"
            '_PRINT_STATUS S="Z OFFSET: HEAT"\n'
            "_WAIT_TEMPERATURE CMD=M104 VALUE=%.0f BELOW=2 ABOVE=3\n"
            '_PRINT_STATUS S="Z OFFSET: TARE"\n'
            "MOVE_SAFE Z=5.0 ABSOLUTE=1 F=600\n"
            "LOAD_CELL_TARE\n"
            '_PRINT_STATUS S="Z OFFSET: READY"' % (cooldown, cooldown))

    def _run_z_calibration_preparation(self, eventtime):
        try:
            self._require_idle()
            self._run_script(self._z_preparation_command())
            self.z_calibration.prepared = True
            self._begin_z_weight_gauge()
            self._show_page(Page.Z_OFFSET_SUMMARY)
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
        if not self.z_calibration.briefing_seen:
            self._show_page(Page.Z_OFFSET_BRIEFING)
        else:
            self._enter_z_zone()

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
            "MOVE_SAFE Z=5.0 ABSOLUTE=1 F=600", "LIFTING Z...")
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
            commands.append("MOVE_SAFE Z=5.0 ABSOLUTE=1 F=600")
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
