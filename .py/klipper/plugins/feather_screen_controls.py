## Movement, heating, filament, and calibration controls for Feather.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import logging
import math
import re
import time

try:
    from .ui import (
        Back, Command, Increment, Navigate, Page, PrintState, Replace,
        SetValue, Toggle, state_spec,
    )
    from .ff5m_ui.keys import AppPage
    from .ff5m_ui.move.geometry import (
        JOYSTICK_XY_CENTER, JOYSTICK_XY_RADIUS,
        JOYSTICK_Z_CENTER, JOYSTICK_Z_RADIUS,
    )
    from .ff5m_ui.move import runtime as move_ui
    from .ff5m_ui.z_offset.constants import Z_WEIGHT_DANGER
    from .ff5m_ui.z_offset import runtime as z_offset_ui
    from . import feather_joystick as joystick_ui
    from . import feather_motion as joystick_motion
    from .feather_z_calibration import PAPER_STEPS, SAFE_Z_ADJUST_STEP
    from .feather_pagination import Pagination, pagination_footer
except (ImportError, ValueError):
    from ui import (
        Back, Command, Increment, Navigate, Page, PrintState, Replace,
        SetValue, Toggle, state_spec,
    )
    from ff5m_ui.keys import AppPage
    from ff5m_ui.move.geometry import (
        JOYSTICK_XY_CENTER, JOYSTICK_XY_RADIUS,
        JOYSTICK_Z_CENTER, JOYSTICK_Z_RADIUS,
    )
    from ff5m_ui.move import runtime as move_ui
    from ff5m_ui.z_offset.constants import Z_WEIGHT_DANGER
    from ff5m_ui.z_offset import runtime as z_offset_ui
    import feather_joystick as joystick_ui
    import feather_motion as joystick_motion
    from feather_z_calibration import PAPER_STEPS, SAFE_Z_ADJUST_STEP
    from feather_pagination import Pagination, pagination_footer


MOVE_SAFE_Z_MAX_MARGIN = 10.0
Z_WEIGHT_GAUGE = (710, 72, 70, 358)
PREHEAT = {
    "PLA": (220, 60),
    "PETG": (250, 70),
    "ABS": (260, 100),
    "ABS-PC": (270, 105),
}
CALIBRATION_ROWS = 3
CALIBRATION_ITEMS = (
    ("cal.z", "Z OFFSET", ("SET NOZZLE HEIGHT",)),
    ("cal.screws", "BED SCREWS",
     ("LEVEL BED USING", "ADJUSTMENT SCREWS")),
    ("cal.mesh", "BED MESH",
     ("PROBE BED AND CREATE", "PROFILE AUTO")),
    ("cal.extruder", "EXTRUDER",
     ("PRINT, MEASURE AND", "UPDATE USER.CFG")),
    ("cal.shaper", "SHAPER",
     ("MEASURE X/Y RESONANCE", "GENERATE CSV RESULTS")),
    ("cal.axes", "AXIS",
     ("PRINT, MEASURE AND", "UPDATE USER.CFG")),
    ("cal.pid_bed", "BED PID",
     ("TUNE BED HEATER", "TEMPERATURE CONTROL")),
    ("cal.pid_extruder", "HOTEND PID",
     ("TUNE NOZZLE HEATER", "TEMPERATURE CONTROL")),
)

class FeatherControlsMixin:
    @staticmethod
    def _intersect_axis_limits(configured, restricted):
        lower = max(float(configured[0]), float(restricted[0]))
        upper = min(float(configured[1]), float(restricted[1]))
        if lower >= upper:
            raise RuntimeError("Movement limits have no safe overlap")
        return lower, upper

    def _feather_move_limits(self, status):
        """Return the limits shared by the joystick and step controls.

        XY is intentionally expressed in Feather/MOVE_SAFE coordinates.  The
        printer's ToolHead XY limits use its parking convention and must not
        change this coordinate system.  Z additionally cannot exceed the
        physical ToolHead range.
        """
        x_limits, y_limits, z_limits = getattr(
            self, "joystick_limits",
            ((-110.0, 110.0), (-110.0, 110.0), (0.0, 220.0)))
        x_limits = tuple(float(value) for value in x_limits)
        y_limits = tuple(float(value) for value in y_limits)
        z_limits = tuple(float(value) for value in z_limits)
        axis_minimum = status.get("axis_minimum", (0.0, 0.0, z_limits[0]))
        axis_maximum = status.get("axis_maximum", (0.0, 0.0, z_limits[1]))
        z_limits = self._intersect_axis_limits(
            z_limits, (float(axis_minimum[2]),
                       float(axis_maximum[2]) - MOVE_SAFE_Z_MAX_MARGIN))
        return x_limits, y_limits, z_limits

    def _create_joystick_planner(self):
        now = self.reactor.monotonic()
        status = self.toolhead.get_status(now)
        kinematics = self.toolhead.get_kinematics()
        x_limits, y_limits, z_limits = self._feather_move_limits(status)
        xy_speed = (float(status.get("max_velocity", 600.0))
                    * joystick_ui.MAX_SPEED_SCALE)
        xy_accel = float(status.get("max_accel", 20000.0)) * 0.5
        z_speed = (float(getattr(kinematics, "max_z_velocity", 25.0))
                   * joystick_ui.MAX_SPEED_SCALE)
        z_accel = float(getattr(kinematics, "max_z_accel", 500.0)) * 0.5
        self.joystick = joystick_ui.JoystickPlanner(
            xy_speed, xy_accel, z_speed, z_accel,
            (x_limits, y_limits, z_limits))
        logging.info(
            "[feather_screen] joystick limits xy=%.1f/%.1f z=%.1f/%.1f "
            "bounds=%.1f..%.1f,%.1f..%.1f,%.1f..%.1f",
            xy_speed, xy_accel, z_speed, z_accel,
            x_limits[0], x_limits[1], y_limits[0], y_limits[1],
            z_limits[0], z_limits[1])

    def _start_joystick_timer(self):
        timer = getattr(self, "joystick_timer", None)
        if timer is None or getattr(self, "joystick_timer_active", False):
            return
        self.joystick_timer_active = True
        self.reactor.update_timer(timer, self.reactor.NOW)

    def _stop_joystick(self):
        planner = getattr(self, "joystick", None)
        if planner is not None:
            planner.stop()
        self.joystick_action = None
        self.joystick_suppressed = None
        self.joystick_timer_active = False
        self.joystick_busy_since = None
        self.joystick_cursor = None
        self.joystick_feedback_at = 0.0
        timer = getattr(self, "joystick_timer", None)
        if timer is not None:
            try:
                self.reactor.update_timer(timer, self.reactor.NEVER)
            except Exception:
                pass
        stream = getattr(self, "joystick_stream", None)
        if (stream is not None and getattr(stream, "active", False)
                and getattr(self, "print_state", None) != PrintState.DESTROYED):
            try:
                stream.finish()
            except Exception:
                logging.exception("[feather_screen] joystick stop flush failed")
        self.joystick_queued = False

    def _get_joystick_stream(self):
        stream = getattr(self, "joystick_stream", None)
        if stream is None:
            stream = joystick_motion.LowLatencyToolheadStream(
                self.toolhead, getattr(self, "input_shaper", None))
            self.joystick_stream = stream
        return stream

    def _queue_joystick_segment(self, segment):
        self._get_joystick_stream().queue_segment(segment)
        self.joystick_queued = True

    def _record_joystick_refill(self, stream, planner, started,
                                segment_count, processed_before,
                                ahead_before):
        finished = self.reactor.monotonic()
        stream.ahead(finished)
        stream.record_refill(finished - started, segment_count)
        active = (planner.motion_active()
                  and (segment_count > 0
                       or stream.last_ahead
                       > joystick_motion.BUSY_TOLERANCE))
        stream.record_motion_cycle(
            finished, active, processed_before, ahead_before,
            stream.last_processed, stream.last_ahead)

    def _joystick_tick(self, eventtime):
        try:
            planner = self.joystick
            if (planner is None or self.page != Page.CONTROL_MOVE
                    or self.move_mode != "joystick"
                    or self.print_state != PrintState.IDLE):
                self._stop_joystick()
                return self.reactor.NEVER
            if planner.watchdog(eventtime):
                logging.warning("[feather_screen] joystick touch watchdog released")
                self.joystick_action = None
                self.joystick_cursor = None
            homed = str(self.toolhead.get_status(eventtime).get("homed_axes", ""))
            if (planner.held and self.joystick_action == "move.joy.xy"
                    and ("x" not in homed or "y" not in homed)):
                planner.release()
                self.joystick_action = None
                self.joystick_cursor = None
            if (planner.held and self.joystick_action == "move.joy.z"
                    and "z" not in homed):
                planner.release()
                self.joystick_action = None
                self.joystick_cursor = None

            stream = self._get_joystick_stream()
            if not stream.active:
                try:
                    stream.start(eventtime)
                except joystick_motion.StreamBusy:
                    if not planner.is_moving():
                        self.joystick_busy_since = None
                        self.joystick_timer_active = False
                        self._update_joystick_feedback(eventtime, force=True)
                        return self.reactor.NEVER
                    if getattr(self, "joystick_busy_since", None) is None:
                        self.joystick_busy_since = eventtime
                        logging.info(
                            "[feather_screen] joystick waiting for "
                            "toolhead tail")
                    if (eventtime - self.joystick_busy_since
                            < joystick_motion.START_BUSY_GRACE):
                        self._update_joystick_feedback(eventtime)
                        return eventtime + joystick_ui.QUEUE_RETRY
                    planner.release()
                    self.joystick_action = None
                    self.joystick_cursor = None
                    self.joystick_busy_since = None
                    self.joystick_timer_active = False
                    self._toast("TOOLHEAD BUSY")
                    return self.reactor.NEVER
                except joystick_motion.StreamUnavailable:
                    planner.release()
                    self.joystick_action = None
                    self.joystick_cursor = None
                    self.joystick_timer_active = False
                    self.move_mode = "step"
                    self._toast("JOYSTICK NOT SUPPORTED")
                    self._render_move()
                    return self.reactor.NEVER
            self.joystick_busy_since = None
            tick_eventtime = self.reactor.monotonic()
            stream.set_motion_active(
                planner.motion_active(), tick_eventtime)
            queue_eventtime = self.reactor.monotonic()
            ahead_before = stream.ahead(queue_eventtime)
            processed_before = stream.last_processed
            if ahead_before >= joystick_motion.MAX_AHEAD:
                finished = self.reactor.monotonic()
                stream.ahead(finished)
                stream.record_motion_cycle(
                    finished, planner.motion_active(),
                    processed_before, ahead_before,
                    stream.last_processed, stream.last_ahead)
                self._update_joystick_feedback(eventtime)
                return eventtime + joystick_ui.QUEUE_RETRY

            position = self.toolhead.get_position()
            queued_position = None
            refill_started = self.reactor.monotonic()
            stream.ahead(refill_started)
            processed_before = stream.last_processed
            ahead_before = stream.last_ahead
            refill_segments = 0
            for _index in range(joystick_motion.MAX_REFILL_SEGMENTS):
                refill_eventtime = self.reactor.monotonic()
                if not stream.wants_segment(refill_eventtime):
                    break
                segment = planner.advance(position, joystick_ui.PERIOD)
                if segment is None:
                    self._record_joystick_refill(
                        stream, planner, refill_started, refill_segments,
                        processed_before, ahead_before)
                    if planner.held:
                        self._update_joystick_feedback(
                            eventtime, position=queued_position)
                        return eventtime + joystick_ui.PERIOD
                    stream.finish()
                    self.joystick_queued = False
                    self.joystick_timer_active = False
                    self._update_joystick_feedback(eventtime, force=True)
                    return self.reactor.NEVER
                self._queue_joystick_segment(segment)
                refill_segments += 1
                position = segment.position
                queued_position = position
            self._record_joystick_refill(
                stream, planner, refill_started, refill_segments,
                processed_before, ahead_before)
            self._update_joystick_feedback(eventtime, position=queued_position)
            return eventtime + joystick_ui.PERIOD
        except Exception:
            logging.exception("[feather_screen] joystick motion failed")
            self._stop_joystick()
            return self.reactor.NEVER

    def _render_move(self, snapshot=None, caution=None):
        self._require_idle()
        now = self.reactor.monotonic()
        if snapshot is None:
            snapshot = self._move_status_snapshot(now)
        if caution is None:
            caution = self._move_caution_state(snapshot, now)
        commands = self.renderer.begin_page("Move", back=True)
        if getattr(self, "move_mode", "step") == "joystick":
            self.joystick_feedback_at = 0.0
            commands += self._joystick_move_commands(snapshot, caution)
        else:
            commands += self._step_move_commands(snapshot, caution)
        self.move_caution_signature = caution
        self.renderer.send(commands)
        self._last_move = snapshot

    def _move_ui_state(self, snapshot, caution=None):
        values = move_ui.snapshot_values(snapshot)
        if caution is None:
            caution = self._move_caution_state(
                snapshot, self.reactor.monotonic())
        values[move_ui.MoveState.CAUTION_ACKNOWLEDGED] = bool(
            getattr(self, "move_caution_acknowledged", False))
        values[move_ui.MoveState.CAUTION_Z] = self._move_caution_z()
        values[move_ui.MoveState.AUTO_PROFILE_STATE] = str(
            caution[1] or "missing")
        return values

    def _move_caution_z(self):
        return abs(float(self._setting("safe_z", 10.0))) / 2.0

    def _step_move_commands(self, snapshot, caution=None):
        values = self._move_ui_state(snapshot, caution)
        values[move_ui.MoveState.JOG_STEP] = float(self.jog_step)
        return move_ui.render_step(self.renderer, values)

    def _joystick_move_commands(self, snapshot, caution=None):
        values = self._move_ui_state(snapshot, caution)
        values[move_ui.MoveState.INERTIA] = float(
            self._joystick_inertia_snapshot())
        values[move_ui.MoveState.CURSOR] = None
        return move_ui.render_joystick(self.renderer, values)

    def _move_status_snapshot(self, eventtime, position=None):
        status = self.toolhead.get_status(eventtime)
        if position is None:
            position = status.get("position", (0.0, 0.0, 0.0, 0.0))
        homed = str(status.get("homed_axes", "")).lower()
        missing = "".join(axis.upper() for axis in "xyz" if axis not in homed)
        state = "HOMED: XYZ" if not missing else "NOT HOMED: %s" % missing
        return (round(position[0], 2), round(position[1], 2),
                round(position[2], 2), state,
                "x" in homed and "y" in homed, "z" in homed)

    def _bed_mesh_profile_state(self, eventtime):
        mesh = getattr(self, "bed_mesh", None)
        if mesh is None:
            return None, False
        try:
            status = mesh.get_status(eventtime)
        except Exception:
            logging.exception("[feather_screen] unable to read bed mesh status")
            return None, False
        profile = str(status.get("profile_name", "") or "").strip().lower()
        profiles = status.get("profiles", {})
        available = (
            isinstance(profiles, dict)
            and any(str(name).strip().lower() == "auto" for name in profiles)
        )
        return profile, available

    def _move_caution_state(self, values, eventtime):
        profile, auto_available = self._bed_mesh_profile_state(eventtime)
        unsafe = (
            bool(values[5])
            and float(values[2]) < self._move_caution_z()
        )
        if not unsafe:
            self.move_caution_acknowledged = False
        visible = unsafe and not getattr(
            self, "move_caution_acknowledged", False)
        if not visible:
            return False, None
        if profile == "auto":
            return True, "active"
        return True, "available" if auto_available else "missing"

    def _sync_move_caution_overlay(self, values, caution):
        previous = getattr(self, "move_caution_signature", caution)
        if caution == previous:
            return False
        if (not caution[0]
                and getattr(self, "joystick_action", None)
                == move_ui.JOYSTICK_Z.wire_id):
            # Preserve an active continuous Z gesture until release. The
            # normal page update below still owns the warning; no independent
            # imperative caution renderer is used.
            return False
        ui_state = self._move_ui_state(values, caution)
        if getattr(self, "move_mode", "step") == "joystick":
            ui_state[move_ui.MoveState.INERTIA] = float(
                self._joystick_inertia_snapshot())
            ui_state[move_ui.MoveState.CURSOR] = getattr(
                self, "joystick_cursor", None)
            commands = move_ui.update_joystick(self.renderer, ui_state)
        else:
            commands = move_ui.render_step_status(
                self.renderer, ui_state, axes=True)
        self.move_caution_signature = caution
        if commands:
            self.renderer.send(commands)
        return True

    def _move_status_commands(self, values, axes=False, caution=None):
        return move_ui.render_step_status(
            self.renderer, self._move_ui_state(values, caution), axes=axes)

    def _update_move_status(self, eventtime):
        values = self._move_status_snapshot(eventtime)
        caution = self._move_caution_state(values, eventtime)
        if self._sync_move_caution_overlay(values, caution):
            return
        previous = getattr(self, "_last_move", None)
        if values == previous:
            return
        self._last_move = values
        if getattr(self, "move_mode", "step") == "joystick":
            ui_state = self._move_ui_state(values, caution)
            ui_state[move_ui.MoveState.INERTIA] = float(
                self._joystick_inertia_snapshot())
            ui_state[move_ui.MoveState.CURSOR] = getattr(
                self, "joystick_cursor", None)
            self.renderer.send(move_ui.update_joystick(
                self.renderer, ui_state))
            return
        axes_changed = previous is None or values[4:] != previous[4:]
        self.renderer.send(self._move_status_commands(
            values, axes=axes_changed, caution=caution))

    def _joystick_inertia_snapshot(self):
        planner = getattr(self, "joystick", None)
        state = (planner.inertia() if planner is not None
                 and callable(getattr(planner, "inertia", None)) else {})
        velocity = state.get("velocity", (0.0, 0.0, 0.0))
        return round(sum(float(value) ** 2 for value in velocity) ** 0.5, 1)

    def _update_joystick_feedback(self, eventtime, position=None, force=False):
        if (self.page != Page.CONTROL_MOVE
                or getattr(self, "move_mode", "step") != "joystick"):
            return
        renderer = getattr(self, "renderer", None)
        if renderer is None or getattr(renderer, "send", None) is None:
            return
        deadline = getattr(self, "joystick_feedback_at", 0.0)
        if not force and eventtime < deadline:
            return

        cursor = getattr(self, "joystick_cursor", None)
        values = self._move_status_snapshot(eventtime, position)
        caution = self._move_caution_state(values, eventtime)
        if self._sync_move_caution_overlay(values, caution):
            return
        if (caution[0] and cursor is not None
                and cursor[0] == move_ui.JOYSTICK_XY.wire_id):
            return
        inertia = self._joystick_inertia_snapshot()
        ui_state = self._move_ui_state(values, caution)
        ui_state[move_ui.MoveState.INERTIA] = float(inertia)
        ui_state[move_ui.MoveState.CURSOR] = cursor
        commands = move_ui.update_joystick(self.renderer, ui_state)
        self._last_move = values
        self.joystick_feedback_at = eventtime + joystick_ui.FEEDBACK_PERIOD
        if commands:
            stream = getattr(self, "joystick_stream", None)
            if stream is not None and getattr(stream, "active", False):
                reactor = getattr(self, "reactor", None)
                clock = (reactor.monotonic if reactor is not None
                         else time.monotonic)
                feedback_started = clock()
                self.renderer.send(commands)
                stream.record_feedback(clock() - feedback_started)
            else:
                self.renderer.send(commands)

    def _semantic_ui_page(self):
        if self.page == Page.CONTROL_MOVE:
            return (move_ui.JOYSTICK_PAGE
                    if getattr(self, "move_mode", "step") == "joystick"
                    else move_ui.STEP_PAGE)
        if self.page == Page.SAFE_Z_BRIEFING:
            return z_offset_ui.SAFE_BRIEFING_PAGE
        if self.page == Page.SAFE_Z_CALIBRATION:
            return z_offset_ui.SAFE_PAGE
        if self.page == Page.Z_OFFSET_SUMMARY:
            return z_offset_ui.SUMMARY_PAGE
        if self.page == Page.Z_OFFSET_PAPER_BRIEFING:
            return z_offset_ui.PAPER_BRIEFING_PAGE
        if self.page == Page.Z_OFFSET_PAPER:
            return z_offset_ui.PAPER_PAGE
        return None

    def _resolve_semantic_ui_action(self, wire_id):
        page = self._semantic_ui_page()
        return None if page is None else page.resolve_action(wire_id)

    def _navigate_app_page(self, target):
        if target == AppPage.MOVE_STEP:
            self._stop_joystick()
            self.move_mode = "step"
            self._render_move()
            return
        if target == AppPage.MOVE_JOYSTICK:
            if not self._get_joystick_stream().supported():
                self._toast("JOYSTICK NOT SUPPORTED")
                return
            self._stop_joystick()
            self.move_mode = "joystick"
            self._render_move()
            return
        targets = {
            AppPage.Z_OFFSET_SUMMARY: Page.Z_OFFSET_SUMMARY,
            AppPage.Z_OFFSET_PAPER_BRIEFING: Page.Z_OFFSET_PAPER_BRIEFING,
            AppPage.Z_OFFSET_PAPER: Page.Z_OFFSET_PAPER,
            AppPage.SAFE_Z_BRIEFING: Page.SAFE_Z_BRIEFING,
            AppPage.SAFE_Z_CALIBRATION: Page.SAFE_Z_CALIBRATION,
        }
        if target not in targets:
            raise KeyError("Unknown application page: %s" % target)
        self._show_page(targets[target])

    def _dispatch_semantic_ui_action(self, action):
        if isinstance(action, Back):
            self._go_back()
            return
        if isinstance(action, (Navigate, Replace)):
            self._navigate_app_page(action.target)
            return
        if isinstance(action, SetValue):
            if action.key == move_ui.MoveState.CAUTION_ACKNOWLEDGED:
                self.move_caution_acknowledged = bool(action.value)
                self._render_move()
                return
            if action.key == move_ui.MoveState.JOG_STEP:
                self.jog_step = float(action.value)
                self._render_move()
                return
            if action.key == z_offset_ui.PaperState.STEP:
                self.z_calibration.step = float(action.value)
                self._render_z_paper()
                return
            if action.key == z_offset_ui.SummaryState.DIALOG:
                self.z_calibration.dialog = action.value
                self._render_z_summary()
                return
            if action.key == z_offset_ui.PaperState.DIALOG:
                self.z_calibration.dialog = action.value
                self._render_z_paper()
                return
            raise KeyError("Unsupported product state action: %s" % action.key)
        if isinstance(action, Toggle):
            if action.key == z_offset_ui.SummaryState.LOAD_ZOFFSET:
                self.z_calibration.load_zoffset = not self.z_calibration.load_zoffset
                self._render_z_summary()
                return
            raise KeyError("Unsupported product toggle: %s" % action.key)
        if isinstance(action, Increment):
            if action.key == move_ui.MoveState.JOG_STEP:
                choices = tuple(state_spec(action.key).choices)
                index = choices.index(float(self.jog_step)) + int(action.amount)
                if action.wrap:
                    index %= len(choices)
                else:
                    index = max(0, min(len(choices) - 1, index))
                self.jog_step = choices[index]
                self._render_move()
                return
            raise KeyError("Unsupported product increment: %s" % action.key)
        if isinstance(action, Command):
            if isinstance(action.key, move_ui.MoveCommand):
                self._handle_move_command(action)
                return
            if isinstance(action.key, z_offset_ui.ZOffsetCommand):
                self._handle_z_offset_command(action)
                return
        raise TypeError("Unsupported semantic action: %r" % (action,))

    def _handle_move_command(self, command):
        self._require_idle()
        key = command.key
        if key == move_ui.MoveCommand.CAUTION_DISMISS:
            self._stop_joystick()
            self.move_caution_acknowledged = True
            self._render_move()
            return
        if key == move_ui.MoveCommand.CAUTION_AUTO:
            self._stop_joystick()
            _profile, available = self._bed_mesh_profile_state(
                self.reactor.monotonic())
            if not available:
                raise RuntimeError("Bed mesh profile 'auto' is not available")
            self._run_script("BED_MESH_PROFILE LOAD=auto")
            self.move_caution_acknowledged = True
            self._render_move()
            self._toast("BED PROFILE AUTO LOADED")
            return
        if key == move_ui.MoveCommand.CAUTION_UNLOAD:
            self._stop_joystick()
            self._run_script("BED_MESH_CLEAR")
            self.move_caution_acknowledged = True
            self._render_move()
            self._toast("BED PROFILE UNLOADED")
            return
        blocked = {
            move_ui.MoveCommand.X_PLUS, move_ui.MoveCommand.X_MINUS,
            move_ui.MoveCommand.Y_PLUS, move_ui.MoveCommand.Y_MINUS,
            move_ui.MoveCommand.HOME_ALL, move_ui.MoveCommand.HOME_XY,
        }
        if (getattr(self, "move_caution_signature", (False, None))[0]
                and key in blocked):
            return
        if key in (move_ui.MoveCommand.JOYSTICK_XY,
                   move_ui.MoveCommand.JOYSTICK_Z):
            return
        if key == move_ui.MoveCommand.DISABLE_MOTORS:
            self._stop_joystick()
            self._run_script("M84")
            self._toast("Motors disabled")
            return
        if isinstance(command.payload, move_ui.HomeRequest):
            self._stop_joystick()
            axes = tuple(axis.value.upper() for axis in command.payload.axes)
            gcode = "G28" if len(axes) == 3 else "G28 %s" % " ".join(axes)
            self._run_blocking_gcode(gcode, "HOMING...")
            self._toast("Homing started")
            return
        if isinstance(command.payload, move_ui.JogRequest):
            axis = command.payload.axis.value
            distance = float(command.payload.direction) * float(self.jog_step)
            speed = int(command.hint.speed)
            status = self.toolhead.get_status(self.reactor.monotonic())
            homed = status["homed_axes"]
            if axis not in homed:
                raise RuntimeError("Home %s before moving" % axis.upper())
            axis_index = "xyz".index(axis)
            current = float(status["position"][axis_index])
            limits = self._feather_move_limits(status)[axis_index]
            if distance > 0.0:
                target = min(limits[1], current + distance)
                limit_reached = target <= current
            else:
                target = max(limits[0], current + distance)
                limit_reached = target >= current
            if limit_reached or math.isclose(target, current, abs_tol=0.000001):
                self._toast("%s LIMIT REACHED" % axis.upper())
                return
            self._run_script(
                "MOVE_SAFE %s=%g ABSOLUTE=1 F=%d" % (
                    axis.upper(), target, speed))
            self._toast("Moved %s %g mm" % (axis.upper(), target - current))
            return
        raise KeyError("Unsupported movement command: %s" % key)

    def _handle_z_offset_command(self, command):
        key = command.key
        if isinstance(command.payload, z_offset_ui.ZoneRequest):
            self._choose_z_zone(command.payload.zone.value)
        elif key == z_offset_ui.ZOffsetCommand.ENTER_ZONE:
            self._enter_z_zone()
        elif key == z_offset_ui.ZOffsetCommand.PROBE:
            self._probe_z_zone()
        elif key == z_offset_ui.ZOffsetCommand.SAFE_CALIBRATE:
            self._begin_safe_z_calibration()
        elif key == z_offset_ui.ZOffsetCommand.SAFE_SKIP:
            self._skip_safe_z_calibration()
        elif key == z_offset_ui.ZOffsetCommand.SAFE_PROBE:
            self._probe_safe_z()
        elif key == z_offset_ui.ZOffsetCommand.SAFE_HIGHER:
            self._adjust_safe_z(SAFE_Z_ADJUST_STEP)
        elif key == z_offset_ui.ZOffsetCommand.SAFE_LOWER:
            self._adjust_safe_z(-SAFE_Z_ADJUST_STEP)
        elif key == z_offset_ui.ZOffsetCommand.SAFE_SAVE:
            self._save_safe_z()
        elif key == z_offset_ui.ZOffsetCommand.MOVE_SAFE_HALF:
            self._move_z_manual_start()
        elif isinstance(command.payload, z_offset_ui.AdjustmentRequest):
            direction = command.payload.direction
            delta = (-self.z_calibration.step
                     if direction == z_offset_ui.Adjustment.CLOSER
                     else self.z_calibration.step)
            self._move_z_paper(delta)
        elif key == z_offset_ui.ZOffsetCommand.RESET:
            self._reset_z_paper()
        elif key == z_offset_ui.ZOffsetCommand.ACCEPT:
            self._accept_z_zone()
        elif key == z_offset_ui.ZOffsetCommand.SELECTION_NEXT:
            self.z_calibration.select_next()
            self._render_z_summary()
        elif key == z_offset_ui.ZOffsetCommand.SAVE:
            self._save_z_calibration()
        elif key == z_offset_ui.ZOffsetCommand.DISCARD_CONFIRM:
            self._cancel_z_calibration()
        else:
            raise KeyError("Unsupported Z-offset command: %s" % key)

    def _render_heat(self):
        now = self.reactor.monotonic()
        extruder = self.extruder.get_status(now)
        bed = self.heater_bed.get_status(now)
        fan_speed = (self.fan.get_status(now).get("speed", 0.0) * 100
                     if self.fan is not None else 0.0)
        commands = self.renderer.begin_page(
            "Heat / fan", back=True)
        rows = (("NOZZLE", 95, "selected", "heat.e"),
                ("BED", 175, "warning", "heat.b"))
        for label, y, state, prefix in rows:
            commands.append(self.renderer.text(30, y, label,
                                               "b47aff" if state == "selected" else "f2c94c",
                                               "JetBrainsMono 12pt", "left", "middle"))
            commands += self.renderer.button(prefix + "minus", 430, y - 25, 100, 50,
                                             "-5", state=state)
            commands += self.renderer.button(prefix + "plus", 550, y - 25, 100, 50,
                                             "+5", state=state)
            commands += self.renderer.button(prefix + "off", 670, y - 25, 100, 50,
                                             "OFF", state=state)
            commands.append(self.renderer.fill(30, y + 39, 740, 1, "295c66"))
        commands.append(self.renderer.text(30, 255, "FAN", "35d9e6",
                                           "JetBrainsMono 12pt", "left", "middle"))
        commands.append(self.renderer.text(
            270, 255, "%.0f%%" % fan_speed if self.fan is not None else "N/A",
            "d9e4e8" if self.fan is not None else "56656c",
            "JetBrainsMono 12pt", "center", "middle"))
        for index, percent in enumerate((0, 50, 100)):
            commands += self.renderer.button("heat.fan%d" % percent,
                                             430 + index * 120, 230, 100, 50,
                                             "%d%%" % percent,
                                             active=self.fan is not None)
        commands.append(self.renderer.text(400, 315, "PREHEAT PRESETS", "35d9e6",
                                           "JetBrainsMono 8pt", "center", "middle"))
        for index, material in enumerate(("PLA", "PETG", "ABS", "ABS-PC")):
            commands += self.renderer.button("heat.preheat.%s" % material,
                                             25 + index * 190, 335, 170, 42,
                                             material, font="JetBrainsMono 8pt")
        commands += self.renderer.button("heat.alloff", 25, 392, 745, 42,
                                         "COOLDOWN", state="danger",
                                         font="JetBrainsMono 12pt")
        commands.append(self.renderer.text(
            270, 95, "%.1f / %.0f C" %
            (extruder["temperature"], extruder["target"]),
            "d9e4e8", "JetBrainsMono 12pt", "center", "middle"))
        commands.append(self.renderer.text(
            270, 175, "%.1f / %.0f C" %
            (bed["temperature"], bed["target"]),
            "d9e4e8", "JetBrainsMono 12pt", "center", "middle"))
        self.renderer.send(commands)
        self._last_heat = (round(extruder["temperature"], 1), round(extruder["target"]),
                           round(bed["temperature"], 1), round(bed["target"]),
                           round(fan_speed))

    def _update_heat_status(self, eventtime):
        extruder = self.extruder.get_status(eventtime)
        bed = self.heater_bed.get_status(eventtime)
        fan_speed = (self.fan.get_status(eventtime).get("speed", 0.0) * 100
                     if self.fan is not None else 0.0)
        values = (round(extruder["temperature"], 1), round(extruder["target"]),
                  round(bed["temperature"], 1), round(bed["target"]),
                  round(fan_speed))
        if values == self._last_heat:
            return
        self._last_heat = values
        self.renderer.send([
            self.renderer.fill(145, 70, 250, 50),
            self.renderer.text(270, 95, "%.1f / %.0f C" % values[:2],
                               "d9e4e8", "JetBrainsMono 12pt", "center", "middle"),
            self.renderer.fill(145, 150, 250, 50),
            self.renderer.text(270, 175, "%.1f / %.0f C" % values[2:4],
                               "d9e4e8", "JetBrainsMono 12pt", "center", "middle"),
            self.renderer.fill(145, 230, 250, 50),
            self.renderer.text(270, 255,
                               "%.0f%%" % values[4] if self.fan is not None else "N/A",
                               "d9e4e8" if self.fan is not None else "56656c",
                               "JetBrainsMono 12pt", "center", "middle"),
        ])

    def _handle_heat_action(self, action):
        now = self.reactor.monotonic()
        if action.startswith("heat.e"):
            target = self.extruder.get_status(now)["target"]
            if action == "heat.eplus": target += 5
            elif action == "heat.eminus": target -= 5
            else: target = 0
            target = self._clamp_heater_target(target, self.extruder.heater, 300)
            self._run_script("M104 S%.0f" % target)
        elif action.startswith("heat.b"):
            target = self.heater_bed.get_status(now)["target"]
            if action == "heat.bplus": target += 5
            elif action == "heat.bminus": target -= 5
            else: target = 0
            target = self._clamp_heater_target(target, self.heater_bed, 130)
            self._run_script("M140 S%.0f" % target)
        elif action == "heat.alloff":
            self._run_script("TURN_OFF_HEATERS")
            self._toast("Heaters turned off")
        elif action.startswith("heat.preheat."):
            material = action.rsplit(".", 1)[1]
            nozzle, bed = self._limited_preheat(material)
            self.filament_material = material
            self._run_script(
                "PREHEAT_MATERIAL MATERIAL=%s EXTRUDER_TEMP=%.0f BED_TEMP=%.0f" %
                (material, nozzle, bed))
            self._toast("%s preheat: %.0f/%.0fC" % (material, nozzle, bed))
        elif action.startswith("heat.fan"):
            if getattr(self, "fan", None) is None:
                raise RuntimeError("Part fan is not configured")
            percent = int(action[len("heat.fan"):])
            self._run_script("M106 S%d" % round(percent * 255 / 100))
            self._toast("Fan: %d%%" % percent)
        self.reactor.register_callback(self._refresh_heat_after_action,
                                      self.reactor.monotonic() + 0.1)

    def _refresh_heat_after_action(self, eventtime):
        self._render_heat()
        if self.toast_until > eventtime:
            self.renderer.toast(self.toast_message)

    @staticmethod
    def _clamp_heater_target(target, heater, default_max):
        if target <= 0:
            return 0
        minimum = max(0, getattr(heater, "min_temp", 0))
        maximum = max(minimum, getattr(heater, "max_temp", default_max) - 1)
        return max(minimum, min(target, maximum))

    def _limited_preheat(self, material):
        nozzle, bed = getattr(self, "preheat", PREHEAT).get(material,
                                                            PREHEAT[material])
        return (self._clamp_heater_target(nozzle, self.extruder.heater, 300),
                self._clamp_heater_target(bed, self.heater_bed, 130))

    def _open_filament(self, from_pause):
        if not from_pause:
            self._require_idle()
        else:
            state = self.print_stats.get_status(
                self.reactor.monotonic())["state"]
            if (state != "paused" or self.cancel_requested
                    or self.page == Page.CANCEL_CONFIRM):
                logging.info(
                    "[feather_screen] filament page ignored in state=%s "
                    "page=%s cancel=%s",
                    state, self.page.name, self.cancel_requested)
                return False
        now = self.reactor.monotonic()
        self.filament_from_pause = from_pause
        self.filament_original_target = self.extruder.get_status(now)["target"]
        self._show_page(Page.FILAMENT_MATERIAL)
        return True

    def _render_filament_material(self):
        commands = self.renderer.begin_page("Select material", back=True)
        for index, material in enumerate(("PLA", "PETG", "ABS", "ABS-PC")):
            x = 35 + (index % 2) * 380
            y = 80 + (index // 2) * 165
            nozzle = self._limited_preheat(material)[0]
            commands += self.renderer.button("filament.%s" % material, x, y, 350, 135,
                                             "%s  %.0fC" % (material, nozzle),
                                             font="Roboto Bold 16pt")
        self.renderer.send(commands)

    def _render_filament_action(self):
        now = self.reactor.monotonic()
        status = self.extruder.get_status(now)
        minimum = getattr(self.extruder, "min_extrude_temp", 170.0)
        hot = status["temperature"] >= minimum
        commands = self.renderer.begin_page(
            "Filament - %s" % self.filament_material,
            back=True)
        commands.append(self.renderer.text(
            400, 80, "Nozzle %.1f / %.0fC" % (status["temperature"], status["target"]),
            "ffb000" if not hot else "00f0f0", "Roboto Bold 14pt", "center"))
        if not hot:
            commands.append(self.renderer.text(400, 120, "Heating - please wait",
                                               "ffffff", "Roboto 10pt", "center"))
        state = "enabled" if hot else "disabled"
        commands += self.renderer.button("filament.load", 20, 165, 175, 100,
                                         "LOAD", state=state, font="Roboto Bold 14pt")
        commands += self.renderer.button("filament.unload", 215, 165, 175, 100,
                                         "UNLOAD", state=state, font="Roboto Bold 14pt")
        commands += self.renderer.button("filament.purge", 410, 165, 175, 100,
                                         "PURGE", state=state, font="Roboto Bold 14pt")
        if self.filament_from_pause:
            commands += self.renderer.button("filament.resume", 605, 165, 175, 100,
                                             "CONTINUE", font="Roboto Bold 14pt")
        else:
            commands += self.renderer.button("filament.done", 605, 165, 175, 100,
                                             "DONE", font="Roboto Bold 14pt")
        self.renderer.send(commands)
        self._last_filament_heat = (round(status["temperature"], 1),
                                    round(status["target"]), hot)

    def _update_filament_status(self, eventtime):
        status = self.extruder.get_status(eventtime)
        minimum = getattr(self.extruder, "min_extrude_temp", 170.0)
        values = (round(status["temperature"], 1), round(status["target"]),
                  status["temperature"] >= minimum)
        if values == self._last_filament_heat:
            return
        if self._last_filament_heat is None or values[2] != self._last_filament_heat[2]:
            self._render_filament_action()
            return
        self._last_filament_heat = values
        self.renderer.send([
            self.renderer.fill(150, 58, 500, 70),
            self.renderer.text(400, 80, "Nozzle %.1f / %.0fC" % values[:2],
                               "00f0f0" if values[2] else "ffb000",
                               "Roboto Bold 14pt", "center"),
        ])

    def _handle_filament_action(self, action):
        state = self.print_stats.get_status(self.reactor.monotonic())["state"]
        if self.filament_from_pause:
            if state != "paused":
                raise RuntimeError("Filament change requires a paused print")
        else:
            self._require_idle()
        if action.startswith("filament.") and action.split(".", 1)[1] in PREHEAT:
            self.filament_material = action.split(".", 1)[1]
            target = self._limited_preheat(self.filament_material)[0]
            self._run_script("SET_MATERIAL MATERIAL=%s\nM104 S%.0f" %
                             (self.filament_material, target))
            self._show_page(Page.FILAMENT_ACTION)
            return
        now = self.reactor.monotonic()
        if action in ("filament.load", "filament.unload", "filament.purge"):
            minimum = getattr(self.extruder, "min_extrude_temp", 170.0)
            if self.extruder.get_status(now)["temperature"] < minimum:
                raise RuntimeError("Nozzle is not hot enough")
            macro = {"filament.load": "LOAD_FILAMENT",
                     "filament.unload": "UNLOAD_FILAMENT",
                     "filament.purge": "PURGE_FILAMENT"}[action]
            command = ("%s MATERIAL=%s" % (macro, self.filament_material)
                       if macro == "LOAD_FILAMENT" else macro)
            self._run_blocking_gcode(
                command, macro.replace("_", " ") + "...")
            self._toast(macro.replace("_", " ").title())
        elif action == "filament.done":
            self._finish_filament(False)
        elif action == "filament.resume":
            self._finish_filament(True)

    def _finish_filament(self, resume):
        if not self.filament_from_pause:
            self._run_script("M104 S%.0f" % self.filament_original_target)
            self._show_page(Page.IDLE_HOME)
            return
        state = self.print_stats.get_status(
            self.reactor.monotonic())["state"]
        if state not in ("printing", "paused"):
            self.filament_from_pause = False
            self._show_page(Page.IDLE_HOME)
            return
        target = self.filament_original_target
        if target > 0:
            self._run_script("M104 S%.0f" % target)
        if resume and state == "paused":
            self._run_script("RESUME")
        self._show_page(self.page_for_print_state())

    def _render_calibration_home(self):
        pagination = Pagination(
            CALIBRATION_ITEMS, getattr(self, "calibration_page", 0),
            CALIBRATION_ROWS)
        self.calibration_page = pagination.page
        commands = self.renderer.begin_page("Calibration", back=True)
        saved = float(self._setting("z_offset", 0.0))
        for row, (action, label, subtitle) in enumerate(pagination.visible):
            if action == "cal.z":
                subtitle = (
                    "SAVED %+.3f MM" % saved, "SET NOZZLE HEIGHT")
            commands += self.renderer.button(
                action, 30, 68 + row * 101, 740, 84, label,
                font="JetBrainsMono 16pt", subtitle=subtitle, layout="row")
        commands += pagination_footer(
            self.renderer, pagination, "cal.prev", "cal.next",
            y=388, center_y=413)
        self.renderer.send(commands)

    def _render_calibration_guide(self):
        kind = getattr(self, "calibration_guide_kind", None)
        if kind == "extruder":
            title = "Extruder rotation"
            steps = (
                "1. HEAT THE NOZZLE AND MARK 100 MM OF FILAMENT.",
                "2. EXTRUDE 100 MM, THEN MEASURE THE ACTUAL LENGTH.",
                "3. NEW DISTANCE = CURRENT DISTANCE X ACTUAL / 100.",
                "4. UPDATE EXTRUDER ROTATION_DISTANCE IN USER.CFG.",
            )
        elif kind == "axes":
            title = "Axis dimensions"
            steps = (
                "1. PRINT THE X/Y SQUARE OR Z TOWER FROM CALIBRATION.MD.",
                "2. MEASURE THE FINISHED MODEL WITH CALIPERS.",
                "3. NEW DISTANCE = CURRENT DISTANCE X ACTUAL / EXPECTED.",
                "4. UPDATE THE STEPPER ROTATION_DISTANCE IN USER.CFG.",
            )
        else:
            title = "Calibration guide"
            steps = ("OPEN CALIBRATION.MD IN FLUIDD FOR INSTRUCTIONS.",)
        commands = self.renderer.begin_page(title, back=True)
        commands += self.renderer.panel(
            30, 72, 740, 300, border="295c66", background="050c0f")
        for index, step in enumerate(steps):
            commands.append(self.renderer.text(
                60, 112 + index * 62, step, "d9e4e8",
                "JetBrainsMono 8pt", "left", "middle",
                max_width=680, max_height=48, wrap=True, truncate=True))
        commands.append(self.renderer.text(
            400, 410, "MEASUREMENT AND USER.CFG EDITING REQUIRED",
            "f2c94c", "JetBrainsMono Bold 8pt", "center", "middle"))
        self.renderer.send(commands)

    def _render_live_z_offset(self):
        now = self.reactor.monotonic()
        current = float(
            self.gcode_move.get_status(now)["homing_origin"][2])
        saved = float(self._setting("z_offset", 0.0))
        unsaved = current - saved
        outside_warning = (
            abs(unsaved) > self.z_adjust_warning_threshold + 0.0001)
        value_color = "ff4d5a" if outside_warning else "ffffff"
        commands = self.renderer.begin_page(
            "Live Z offset", back=True)

        cards = (
            ("SAVED", saved, 20, "35d9e6"),
            ("CURRENT", current, 245, value_color),
            ("UNSAVED", unsaved, 470, value_color),
        )
        for label, value, x, color in cards:
            commands += self.renderer.panel(
                x, 82, 215, 112, border="295c66", background="050c0f")
            commands.append(self.renderer.text(
                x + 107, 108, label, "35d9e6",
                "JetBrainsMono 8pt", "center", "middle"))
            commands.append(self.renderer.text(
                x + 107, 151, "%+.3f mm" % value, color,
                "JetBrainsMono Bold 16pt", "center", "middle"))

        commands.append(self.renderer.text(
            355, 218, "ADJUSTMENT STEP", "56656c",
            "JetBrainsMono 8pt", "center", "middle"))
        steps = (
            ("live_z.step.0005", 0.005),
            ("live_z.step.001", 0.010),
            ("live_z.step.005", 0.050),
        )
        for index, (action, step) in enumerate(steps):
            commands += self.renderer.button(
                action, 65 + index * 205, 238, 180, 55,
                "%.3f mm" % step,
                state=("selected" if step == self.live_z_step
                       else "enabled"),
                font="JetBrainsMono 10pt")

        controls_enabled = self._live_z_adjust_allowed(now)
        state = "enabled" if controls_enabled else "disabled"
        commands += self.renderer.button(
            "live_z.closer", 20, 322, 215, 88,
            "CLOSER  -%.3f" % self.live_z_step,
            state=state, font="JetBrainsMono Bold 10pt")
        commands += self.renderer.button(
            "live_z.farther", 245, 322, 215, 88,
            "FARTHER  +%.3f" % self.live_z_step,
            state=state, font="JetBrainsMono Bold 10pt")
        commands += self.renderer.button(
            "live_z.save", 470, 322, 220, 88, "SAVE",
            font="JetBrainsMono Bold 12pt")
        commands += self._z_weight_gauge_commands(now)

        if self.live_z_dialog == "limit":
            commands += self.renderer.dialog(
                "LARGE Z-OFFSET CHANGE",
                ("CURRENT DIFFERS FROM SAVED BY MORE THAN %.2f MM." %
                 self.z_adjust_warning_threshold,
                 "VERIFY THE FIRST LAYER BEFORE CONTINUING."),
                (("live_z.warning.ok", "OK", "warning"),),
                x=100, y=115, width=600, height=260, tone="danger")
        elif self.live_z_dialog == "save":
            commands += self.renderer.dialog(
                "AUTO LOAD IS OFF",
                ("THE VALUE WILL BE SAVED, BUT NOT RESTORED",
                 "AFTER A KLIPPER RESTART. ENABLE AUTO LOAD?"),
                (("live_z.save.no", "NO", "enabled"),
                 ("live_z.save.yes", "YES", "warning")),
                x=100, y=115, width=600, height=260, tone="warning")
        self.renderer.send(commands)

    def _handle_live_z_action(self, action):
        if action.startswith("live_z.step."):
            steps = {
                "live_z.step.0005": 0.005,
                "live_z.step.001": 0.010,
                "live_z.step.005": 0.050,
            }
            step = steps.get(action)
            if step is not None:
                self.live_z_step = step
                self._render_live_z_offset()
        elif action in ("live_z.closer", "live_z.farther"):
            delta = (-self.live_z_step if action == "live_z.closer"
                     else self.live_z_step)
            self._apply_live_z_adjust(delta)
        elif action == "live_z.save":
            self._require_live_z_adjust()
            if self._setting("load_zoffset", 0):
                self._save_live_z_offset(False)
            else:
                self.live_z_dialog = "save"
                self._render_live_z_offset()
        elif action == "live_z.warning.ok":
            if self.live_z_dialog == "limit":
                self.live_z_dialog = None
                self._render_live_z_offset()
        elif action == "live_z.save.no":
            if self.live_z_dialog == "save":
                self._save_live_z_offset(False)
        elif action == "live_z.save.yes":
            if self.live_z_dialog == "save":
                self._save_live_z_offset(True)

    def _handle_calibration_action(self, action):
        if action == "cal.prev":
            self.calibration_page = max(
                0, getattr(self, "calibration_page", 0) - 1)
            self._render_calibration_home()
        elif action == "cal.next":
            self.calibration_page = (
                getattr(self, "calibration_page", 0) + 1)
            self._render_calibration_home()
        elif action in (
                "cal.z", "cal.screws", "cal.mesh",
                "cal.pid_bed", "cal.pid_extruder", "cal.shaper"):
            self._require_idle()
            self.calibration_kind = action.split(".", 1)[1]
            current_material = self._current_material()
            self.calibration_material = (
                current_material if current_material in PREHEAT else "PLA")
            self.calibration_clean_nozzle = True
            self.calibration_repeat_probe = False
            self._show_page(Page.CALIBRATION_CONFIRM)
        elif action == "cal.extruder":
            self._start_extruder_calibration()
        elif action == "cal.axes":
            self._require_idle()
            self.calibration_guide_kind = action.split(".", 1)[1]
            self._show_page(Page.CALIBRATION_GUIDE)
        elif action.startswith("cal.material."):
            self.calibration_material = action.rsplit(".", 1)[1]
            self.calibration_clean_nozzle = True
            self.calibration_repeat_probe = False
            self._render_calibration_confirm()
        elif action == "cal.clean.skip":
            self.calibration_clean_nozzle = False
            self.calibration_repeat_probe = False
            self._render_calibration_confirm()
        elif action == "cal.confirm":
            if self.calibration_kind == "z":
                self._start_z_calibration()
            else:
                self._start_calibration(repeat_probe=False)
        elif action == "cal.cancel.heat":
            self._cancel_calibration_heat()
        elif action == "cal.repeat":
            if self.calibration_kind == "screws":
                self._start_calibration(repeat_probe=True)
            else:
                self.calibration_repeat_probe = False
                self._show_page(Page.CALIBRATION_CONFIRM)
        elif action == "cal.done":
            self._show_page(Page.CALIBRATION_HOME)
        elif action == "cal.mesh.discard":
            if self._mesh_save_available():
                self._show_page(Page.CALIBRATION_HOME)
        elif action == "cal.mesh.save":
            if self._mesh_save_available():
                self._restart_klipper("SAVE_CONFIG")
        elif action == "cal.tuning.discard":
            if self._tuning_save_available():
                self._show_page(Page.CALIBRATION_HOME)
        elif action == "cal.tuning.save":
            if self._tuning_save_available():
                self._restart_klipper("SAVE_CONFIG")

    def _start_calibration(self, repeat_probe=False):
        self._require_idle()
        self._cancel_delayed_tasks()
        self.calibration_repeat_probe = bool(
            repeat_probe and self.calibration_kind == "screws")
        self.calibration_results = []
        self.calibration_mesh = []
        self.calibration_error = None
        self.calibration_cancel_requested = False
        self.calibration_cancel_dispatched = False
        self.calibration_cancelled = False
        if self.calibration_repeat_probe:
            self.print_status_text = "BED SCREWS: PROBING"
        else:
            self.print_status_text = {
                "pid_bed": "BED PID: STARTING",
                "pid_extruder": "HOTEND PID: STARTING",
                "shaper": "INPUT SHAPER: STARTING",
            }.get(self.calibration_kind, "CALIBRATION: STARTING")
        self._show_page(Page.CALIBRATION_PROGRESS)
        self.reactor.register_callback(self._run_calibration)

    @staticmethod
    def _finite_weight(value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    def _begin_z_weight_gauge(self):
        self.z_weight_gauge = None
        self._update_z_weight_gauge(self.reactor.monotonic())

    def _update_z_weight_gauge(self, eventtime):
        sensor = getattr(self, "weight_sensor", None)
        if sensor is None:
            return None
        try:
            status = sensor.get_status(eventtime)
        except Exception:
            logging.exception("[feather_screen] unable to read weightValue")
            return getattr(self, "z_weight_gauge", None)
        current = self._finite_weight(status.get("temperature"))
        if current is None:
            return getattr(self, "z_weight_gauge", None)
        measured_min = self._finite_weight(status.get("measured_min_temp"))
        measured_max = self._finite_weight(status.get("measured_max_temp"))
        samples = [current]
        # Generic temperature sensors expose their uninitialized extrema as
        # min=99999999/max=0. Only seed the gauge from a coherent pair.
        if (measured_min is not None and measured_max is not None
                and measured_min <= measured_max):
            samples.extend((measured_min, measured_max))
        gauge = getattr(self, "z_weight_gauge", None)
        if gauge is None:
            gauge = {
                "initial": current,
                "minimum": min(samples),
                "maximum": max(samples),
                "value": current,
            }
            self.z_weight_gauge = gauge
        else:
            gauge["value"] = current
            gauge["minimum"] = min([gauge["minimum"]] + samples)
            gauge["maximum"] = max([gauge["maximum"]] + samples)
        return gauge

    def _z_weight_gauge_commands(self, eventtime):
        gauge = self._update_z_weight_gauge(eventtime)
        x, y, width, height = Z_WEIGHT_GAUGE
        if gauge is None:
            commands = self.renderer.panel(
                x, y, width, height, border="295c66",
                background="050c0f", line_width=1)
            commands += [
                self.renderer.text(
                    x + width // 2, y + 24, "FORCE", "35d9e6",
                    "JetBrainsMono 8pt", "center", "middle"),
                self.renderer.text(
                    x + width // 2, y + height // 2, "N/A",
                    "56656c", "JetBrainsMono 6pt", "center", "middle"),
            ]
            return commands
        return self.renderer.vertical_gauge(
            x, y, width, height, "LOAD", gauge["value"],
            gauge["minimum"], gauge["maximum"], gauge["initial"],
            value_color=("danger" if gauge["value"] > Z_WEIGHT_DANGER
                         else "primary"))

    def _update_z_weight_status(self, eventtime):
        gauge = self._update_z_weight_gauge(eventtime)
        if getattr(self, "page", None) == Page.Z_OFFSET_PAPER:
            self.renderer.send(z_offset_ui.update_paper_gauge(
                self.renderer, None if gauge is None else dict(gauge)))
            self._check_z_pressure(eventtime)
            return
        self.renderer.send(self._z_weight_gauge_commands(eventtime))

    def _check_z_pressure(self, eventtime):
        session = getattr(self, "z_calibration", None)
        if session is None or not session.active:
            return False
        gauge = self._update_z_weight_gauge(eventtime)
        if gauge is None:
            return False
        warning = session.pressure.update(
            gauge["value"], suppressed=session.probing)
        if warning:
            session.dialog = "pressure"
            session.dialog_weight = gauge["value"]
            self._render_z_paper()
        return warning

    def _live_z_adjust_allowed(self, eventtime):
        stats = self.print_stats.get_status(eventtime)
        if stats.get("state") not in ("printing", "paused"):
            return False
        if self.print_state == PrintState.PREPARING:
            return False
        homed = str(
            self.toolhead.get_status(eventtime).get("homed_axes", "")).lower()
        return "z" in homed

    def _require_live_z_adjust(self):
        if not self._live_z_adjust_allowed(self.reactor.monotonic()):
            raise RuntimeError("Z adjust is not available")

    def _apply_live_z_adjust(self, delta):
        self._require_live_z_adjust()
        now = self.reactor.monotonic()
        current = float(
            self.gcode_move.get_status(now)["homing_origin"][2])
        if abs(current + delta) > self.z_offset_limit + 0.0001:
            raise RuntimeError("Z offset safety limit reached")
        self._run_blocking_gcode(
            "_SET_GCODE_OFFSET Z_ADJUST=%+.3f MOVE=1" % delta,
            "ADJUSTING Z...")
        current = float(self.gcode_move.get_status(
            self.reactor.monotonic())["homing_origin"][2])
        saved = float(self._setting("z_offset", 0.0))
        if (abs(current - saved)
                > self.z_adjust_warning_threshold + 0.0001
                and not self.live_z_limit_warned):
            self.live_z_limit_warned = True
            self.live_z_dialog = "limit"
        self._render_live_z_offset()

    def _save_live_z_offset(self, enable_auto_load):
        self._require_live_z_adjust()
        current = float(self.gcode_move.get_status(
            self.reactor.monotonic())["homing_origin"][2])
        commands = ["SET_MOD PARAM=z_offset VALUE=%.3f" % current]
        if enable_auto_load:
            commands.append("SET_MOD PARAM=load_zoffset VALUE=1")
        self.live_z_dialog = None
        self._run_blocking_gcode(
            "\n".join(commands), "SAVING Z OFFSET...")
        self._render_live_z_offset()
        self._toast("Z offset saved %+.3f mm" % current)

    def _render_calibration_confirm(self):
        kind = self.calibration_kind
        title = {
            "z": "Z offset preparation",
            "pid_bed": "Bed PID calibration",
            "pid_extruder": "Hotend PID calibration",
            "shaper": "Input shaper calibration",
        }.get(kind, "Confirm calibration")
        commands = self.renderer.begin_page(title, back=True)
        if kind == "screws":
            text = ("Select material to run CLEAR_NOZZLE before probing, "
                    "or continue without cleaning.")
        elif kind == "z":
            text = ("Select the material temperature for nozzle cleaning, "
                    "or start without an initial nozzle cleaning.")
        elif kind == "pid_bed":
            text = ("Select the bed target temperature used for PID tuning. "
                    "The printer will home before heating.")
        elif kind == "pid_extruder":
            text = ("Select the nozzle target temperature used for PID tuning. "
                    "Remove filament from the nozzle before starting.")
        elif kind == "shaper":
            text = ("The toolhead will home and vibrate rapidly on both axes. "
                    "Clear the bed and keep away from the printer.")
        else:
            text = "Printer will heat, clean, home and replace mesh profile 'auto'."
        commands.append(self.renderer.text(
            400, 85, text, "ffffff", "Roboto 10pt", "center", "middle",
            max_width=572, max_height=70, wrap=True, truncate=True))
        if kind in ("screws", "mesh"):
            materials = ("PLA", "PETG", "ABS")
        elif kind in ("z", "pid_bed", "pid_extruder"):
            materials = ("PLA", "PETG", "ABS", "ABS-PC")
        else:
            materials = ()
        if materials:
            width = 135 if len(materials) == 4 else 180
            gap = 15
            total = len(materials) * width + (len(materials) - 1) * gap
            left = (800 - total) // 2
            for index, material in enumerate(materials):
                commands += self.renderer.button("cal.material.%s" % material,
                                                 left + index * (width + gap),
                                                 145, width, 55,
                                                 material,
                                                 state=("selected" if
                                                        material ==
                                                        self.calibration_material and
                                                        (kind not in ("screws", "z") or
                                                         getattr(
                                                             self,
                                                             "calibration_clean_nozzle",
                                                             True))
                                                        else "enabled"))
        if kind in ("screws", "z"):
            commands += self.renderer.button(
                "cal.clean.skip", 115, 215, 570, 52, "WITHOUT CLEANING",
                state=("enabled" if getattr(
                    self, "calibration_clean_nozzle", True) else "selected"))
            if getattr(self, "calibration_clean_nozzle", True):
                mode_hint = (
                    "CLEAN NOZZLE FOR %s, THEN HOME AND TARE" %
                    self.calibration_material)
            else:
                mode_hint = (
                    "WITHOUT CLEANING: USE COOLDOWN TEMPERATURE, THEN HOME AND TARE")
            commands.append(self.renderer.text(
                400, 290, mode_hint,
                "56656c", "JetBrainsMono 8pt", "center"))
        elif kind in ("pid_bed", "pid_extruder"):
            nozzle, bed = self._limited_preheat(
                self.calibration_material)
            target = bed if kind == "pid_bed" else nozzle
            commands.append(self.renderer.text(
                400, 255, "TARGET %.0f C // %s" % (
                    target, self.calibration_material),
                "f2c94c", "JetBrainsMono Bold 10pt", "center"))
        elif kind == "shaper":
            commands.append(self.renderer.text(
                400, 255, "STRONG MACHINE VIBRATION IS EXPECTED",
                "f2c94c", "JetBrainsMono Bold 10pt", "center"))
        commands += self.renderer.button("cal.confirm", 220, 330, 360, 85,
                                         "START",
                                         state="enabled",
                                         font="Roboto Bold 16pt")
        self.renderer.send(commands)

    def _render_calibration_progress(self):
        label = self.print_status_text or "Calibration running..."
        title = "Recovery" if self.calibration_kind == "recovery" else "Calibration"
        commands = self.renderer.begin_page(title)
        commands.append(self.renderer.text(
            400, 142, label, "b47aff", "JetBrainsMono Bold 12pt", "center",
            max_width=704, truncate=True))
        commands += self._calibration_stage_commands(label)
        cancel_visible = self._calibration_heat_cancel_visible()
        if cancel_visible:
            commands += self.renderer.button(
                "cal.cancel.heat", 235, 335, 330, 72,
                "CANCELLING..." if getattr(
                    self, "calibration_cancel_requested", False)
                else "CANCEL HEATING",
                state=("busy" if getattr(
                    self, "calibration_cancel_requested", False)
                       else "danger"),
                font="JetBrainsMono Bold 12pt")
        self.renderer.send(commands)
        self._last_calibration_label = label
        self._last_calibration_cancel_visible = cancel_visible

    def _update_calibration_progress(self):
        label = self.print_status_text or "Calibration running..."
        cancel_visible = self._calibration_heat_cancel_visible()
        if (label == self._last_calibration_label
                and cancel_visible == getattr(
                    self, "_last_calibration_cancel_visible", False)):
            return
        # Phase changes are infrequent. Rebuild the whole safety screen so
        # the state-derived Emergency Stop header and its hitbox stay in sync
        # with homing and heater state across partial status redraws.
        if (self.calibration_kind in (
                "screws", "mesh", "z", "pid_bed", "pid_extruder",
                "shaper")
                or cancel_visible != getattr(
                    self, "_last_calibration_cancel_visible", False)):
            self._render_calibration_progress()
            return
        self._last_calibration_label = label
        commands = [self.renderer.fill(40, 105, 720, 205, "030607"),
                    self.renderer.text(
                        400, 142, label, "b47aff", "JetBrainsMono Bold 12pt",
                        "center", max_width=704, truncate=True)]
        commands += self._calibration_stage_commands(label)
        self.renderer.send(commands)

    def _calibration_heat_cancel_visible(self):
        return (
            self.calibration_kind in ("screws", "mesh", "z")
            and (self._temperature_wait_active()
                 or getattr(self, "calibration_cancel_requested", False)))

    def _cancel_calibration_heat(self):
        if (self.calibration_kind not in ("screws", "mesh", "z")
                or not self._temperature_wait_active()
                or getattr(self, "calibration_cancel_requested", False)):
            return
        self.calibration_cancel_requested = True
        self.calibration_cancel_dispatched = True
        self._render_calibration_progress()
        try:
            self._run_immediate_command("M108")
        except Exception:
            self.calibration_cancel_requested = False
            self.calibration_cancel_dispatched = False
            raise

    def _stop_cancelled_calibration_heating(self):
        command = (
            "M104 S0"
            if (self.calibration_kind == "screws"
                and not getattr(
                    self, "calibration_clean_nozzle", True))
            else "TURN_OFF_HEATERS")
        try:
            self._run_script(command)
        except Exception:
            # The original cancellation remains the user-visible result.
            # Cleanup failure is still recorded for diagnostics.
            logging.exception(
                "[feather_screen] unable to stop calibration heating")
            return False
        return True

    def _calibration_stage_commands(self, label):
        text = str(label).upper()
        if self.calibration_kind == "recovery":
            if getattr(self, "recovery_action", None) == "cleanup":
                stages = ("PREP", "HEAT", "HOME", "CLEANUP")
            else:
                stages = ("PREP", "HEAT", "HOME", "POSITION", "RESTORE")
        elif self.calibration_kind == "z":
            stages = (("PREP", "HOME", "HEAT", "CLEAN", "TARE", "READY")
                      if getattr(self, "calibration_clean_nozzle", True)
                      else ("PREP", "HOME", "HEAT", "TARE", "READY"))
        elif self.calibration_kind == "screws":
            repeat_probe = getattr(self, "calibration_repeat_probe", False)
            clean_nozzle = getattr(self, "calibration_clean_nozzle", True)
            if repeat_probe:
                stages = ("PROBE", "DONE")
            elif clean_nozzle:
                stages = ("PREP", "HEAT", "CLEAN", "PROBE", "DONE")
            else:
                stages = ("PREP", "HOME", "HEAT", "PROBE", "DONE")
        elif self.calibration_kind in ("pid_bed", "pid_extruder"):
            stages = ("PREP", "HOME", "TUNE", "DONE")
        elif self.calibration_kind == "shaper":
            stages = ("PREP", "HOME", "MEASURE", "PROCESS", "DONE")
        else:
            stages = ("PREP", "HOME", "HEAT", "CLEAN", "LEVEL")

        phase = stages[0]
        if self.calibration_kind == "recovery" and (
                "START" in text or "ABORT" in text or "RESURRECT" in text):
            phase = "PREP"
        elif "POSITION" in text:
            phase = "POSITION"
        elif "RESTOR" in text:
            phase = "RESTORE"
        elif "PROCESS" in text or "CALCULAT" in text:
            phase = "PROCESS"
        elif "MEASUR" in text or "VIBRAT" in text:
            phase = "MEASURE"
        elif "TUN" in text:
            phase = "TUNE"
        elif "ABORT" in text or "CLEANING UP" in text:
            phase = "CLEANUP"
        elif "READY" in text:
            phase = "READY"
        elif "TARE" in text:
            phase = "TARE"
        elif "COMPLETE" in text:
            phase = stages[-1]
        elif "PROB" in text:
            phase = "PROBE"
        elif "LEVEL" in text:
            phase = "LEVEL"
        elif any(marker in text for marker in ("CLEAN", "COOL")):
            phase = "CLEAN"
        elif "DONE!" in text and "CLEAN" in stages:
            phase = "CLEAN"
        elif "HOM" in text:
            phase = "HOME"
        elif "HEAT" in text:
            phase = "HEAT"
        elif any(marker in text for marker in ("PREP", "START")):
            phase = "PREP"
        current = stages.index(phase) if phase in stages else 0

        left, right, gap = 55, 745, 12
        width = (right - left - gap * (len(stages) - 1)) // len(stages)
        commands = []
        for position, stage in enumerate(stages):
            x = left + position * (width + gap)
            if position == current:
                color = "b47aff"
            elif position < current:
                color = "35d9e6"
            else:
                color = "263238"
            commands += [self.renderer.fill(x, 225, width, 38, "050c0f"),
                         self.renderer.stroke(x, 225, width, 38, color, 2),
                         self.renderer.text(x + width // 2, 244, stage, color,
                                            "JetBrainsMono 8pt", "center", "middle")]
        return commands

    def _run_calibration(self, eventtime):
        stop_heaters = self.calibration_kind in (
            "pid_bed", "pid_extruder")
        try:
            self._require_idle()
            if self.calibration_kind == "screws":
                if getattr(self, "calibration_repeat_probe", False):
                    command = "BED_LEVEL_SCREWS_PROBE"
                else:
                    clean = int(getattr(
                        self, "calibration_clean_nozzle", True))
                    if clean:
                        nozzle, bed = self._limited_preheat(
                            self.calibration_material)
                        command = (
                            "BED_LEVEL_SCREWS_TUNE EXTRUDER_TEMP=%.0f "
                            "BED_TEMP=%.0f CLEAN=1" % (nozzle, bed))
                    else:
                        command = "BED_LEVEL_SCREWS_TUNE CLEAN=0"
                self._run_script(command)
            elif self.calibration_kind == "mesh":
                nozzle, bed = self._limited_preheat(
                    self.calibration_material)
                command = ("AUTO_FULL_BED_LEVEL EXTRUDER_TEMP=%.0f BED_TEMP=%.0f "
                           "PROFILE=auto" % (nozzle, bed))
                self._run_script(command)
                self.calibration_mesh = self._read_mesh_matrix(eventtime)
            elif self.calibration_kind == "pid_bed":
                _nozzle, bed = self._limited_preheat(
                    self.calibration_material)
                self._run_script(
                    "PID_TUNE_BED TEMPERATURE=%.0f" % bed)
            elif self.calibration_kind == "pid_extruder":
                nozzle, _bed = self._limited_preheat(
                    self.calibration_material)
                self._run_script(
                    "PID_TUNE_EXTRUDER TEMPERATURE=%.0f" % nozzle)
            elif self.calibration_kind == "shaper":
                self._run_script("ZSHAPER")
            else:
                raise RuntimeError("Unsupported calibration")
        except Exception as exc:
            if (getattr(self, "calibration_cancel_requested", False)
                    and getattr(
                        self, "calibration_cancel_dispatched", False)):
                logging.info("[feather_screen] calibration heating cancelled")
                if not self._stop_cancelled_calibration_heating():
                    return
                self.calibration_cancelled = True
                self.calibration_error = None
            else:
                logging.exception("[feather_screen] calibration failed")
                self.calibration_error = str(exc)
        finally:
            if stop_heaters:
                try:
                    self._run_script("TURN_OFF_HEATERS")
                except Exception:
                    logging.exception(
                        "[feather_screen] unable to stop PID heating")
                    if not self.calibration_error:
                        self.calibration_error = (
                            "Unable to stop PID heating")
        self._show_page(Page.CALIBRATION_RESULT)

    @staticmethod
    def normalize_mesh_matrix(value):
        if not isinstance(value, (list, tuple)):
            return []
        matrix = []
        width = None
        for row in value:
            if not isinstance(row, (list, tuple)):
                return []
            try:
                normalized = [float(cell) for cell in row]
            except (TypeError, ValueError):
                return []
            if not normalized or (width is not None and len(normalized) != width):
                return []
            width = len(normalized)
            matrix.append(normalized)
        return matrix

    def _read_mesh_matrix(self, eventtime):
        mesh = getattr(self, "bed_mesh", None)
        if mesh is None:
            return []
        status = mesh.get_status(eventtime)
        for key in ("probed_matrix", "mesh_matrix"):
            matrix = self.normalize_mesh_matrix(status.get(key))
            if matrix:
                return matrix
        profile = status.get("profiles", {}).get("auto", {})
        return self.normalize_mesh_matrix(profile.get("points"))

    def _mesh_save_available(self):
        return (
            self.calibration_kind == "mesh"
            and bool(self.calibration_mesh)
            and not self.calibration_error
            and not getattr(self, "calibration_cancelled", False))

    def _tuning_save_available(self):
        return (
            self.calibration_kind in (
                "pid_bed", "pid_extruder", "shaper")
            and not self.calibration_error
            and not getattr(self, "calibration_cancelled", False))

    @staticmethod
    def _mesh_color(value, minimum, maximum):
        if maximum <= minimum:
            return "35d9e6"
        ratio = (value - minimum) / (maximum - minimum)
        colors = ("244c66", "35d9e6", "56c596", "f2c94c", "ff4d5a")
        return colors[min(len(colors) - 1, int(ratio * len(colors)))]

    SCREW_RESULT = re.compile(r"^([^:]+).*adjust\s+(CW|CCW)\s+([0-9]+:[0-9]+)", re.I)
    SCREW_BASE = re.compile(r"^([^:]+)\s+\(base\)\s*:", re.I)

    @classmethod
    def parse_screw_result(cls, message):
        match = cls.SCREW_RESULT.search(str(message).strip())
        if match:
            return {"name": match.group(1).strip(),
                    "direction": match.group(2).upper(), "turns": match.group(3)}
        match = cls.SCREW_BASE.search(str(message).strip())
        if match:
            return {"name": match.group(1).strip(), "direction": "BASE", "turns": "-"}
        return None

    @staticmethod
    def _prompt_response_lines(message):
        for raw_line in str(message).splitlines():
            line = raw_line.strip()
            if line.startswith("//"):
                line = line[2:].lstrip()
            if line.lower().startswith("action:prompt_"):
                yield line

    @staticmethod
    def _prompt_button(payload, index):
        parts = str(payload).split("|", 2)
        label = parts[0].strip()
        command = (
            parts[1].strip()
            if len(parts) > 1 and parts[1].strip() else label)
        color = (
            parts[2].strip().lower()
            if len(parts) > 2 else "")
        state = {
            "error": "danger",
            "warning": "warning",
            "secondary": "selected",
        }.get(color, "enabled")
        return {
            "action": "prompt.button.%d" % index,
            "label": label,
            "command": command,
            "state": state,
        }

    def _start_action_prompt(self, title):
        if (getattr(self, "action_prompt_visible", False)
                and self.page in (
                    Page.ACTION_PROMPT, Page.RECOVERY_PROMPT,
                    Page.RECOVERY_CONFIRM)):
            return_page = self.action_prompt_return_page
        elif self.page in (Page.RECOVERY_PROMPT, Page.RECOVERY_CONFIRM):
            return_page = self.page_for_print_state()
        else:
            return_page = self.page
        self.action_prompt = {
            "title": str(title).strip(),
            "text": [],
            "rows": [],
            "footer": [],
            "group": None,
            "buttons": {},
        }
        self.action_prompt_visible = False
        self.action_prompt_return_page = return_page
        self.action_prompt_page = 0

    def _append_action_prompt_button(self, payload, footer=False):
        prompt = self.action_prompt
        if prompt is None:
            return
        button = self._prompt_button(payload, len(prompt["buttons"]))
        prompt["buttons"][button["action"]] = button
        if footer:
            prompt["footer"].append(button)
        elif prompt["group"] is not None:
            prompt["group"].append(button)
        else:
            prompt["rows"].append([button])

    def _finish_action_prompt_group(self):
        prompt = self.action_prompt
        if prompt is None or prompt["group"] is None:
            return
        if prompt["group"]:
            prompt["rows"].append(prompt["group"])
        prompt["group"] = None

    def _action_prompt_is_recovery(self):
        prompt = self.action_prompt or {}
        return prompt.get("title", "").strip().casefold() == "resurrection"

    def _show_action_prompt(self):
        if self.action_prompt is None:
            return
        self._finish_action_prompt_group()
        if self._action_prompt_is_recovery():
            recovery = getattr(self, "resurrection", None)
            status = (
                recovery.get_status(self.reactor.monotonic())
                if recovery is not None else {})
            self.recovery_status = status
            if not status.get("available"):
                self.action_prompt = None
                self.action_prompt_visible = False
                return
            page = Page.RECOVERY_PROMPT
        else:
            page = Page.ACTION_PROMPT
        self.action_prompt_visible = True
        self.action_prompt_page = 0
        self._show_page(page)

    def _end_action_prompt(self):
        current_page = self.page
        mirrored_recovery = (
            current_page in (Page.RECOVERY_PROMPT, Page.RECOVERY_CONFIRM))
        displayed = (
            getattr(self, "action_prompt_visible", False)
            and current_page == Page.ACTION_PROMPT)
        return_page = getattr(
            self, "action_prompt_return_page", Page.IDLE_HOME)
        self.action_prompt = None
        self.action_prompt_visible = False
        self.action_prompt_page = 0
        if mirrored_recovery or displayed:
            self.recovery_action = None
            self._show_page(
                self.page_for_print_state()
                if mirrored_recovery else return_page)

    def _handle_action_prompt_response(self, line):
        command, separator, payload = line.partition(" ")
        action = command[len("action:prompt_"):].lower()
        payload = payload if separator else ""
        if action == "begin":
            self._start_action_prompt(payload)
        elif action == "text" and self.action_prompt is not None:
            self.action_prompt["text"].append(payload)
        elif action == "button":
            self._append_action_prompt_button(payload)
        elif action == "footer_button":
            self._append_action_prompt_button(payload, footer=True)
        elif action == "button_group_start" and self.action_prompt is not None:
            self._finish_action_prompt_group()
            self.action_prompt["group"] = []
        elif action == "button_group_end":
            self._finish_action_prompt_group()
        elif action == "show":
            self._show_action_prompt()
        elif action == "end":
            self._end_action_prompt()

    def _handle_gcode_output(self, message):
        for line in self._prompt_response_lines(message):
            self._handle_action_prompt_response(line)
        if (getattr(self, "calibration_kind", None) == "screws"
                and self.page == Page.CALIBRATION_PROGRESS):
            result = self.parse_screw_result(message)
            if result:
                self.calibration_results.append(result)

    def _handle_action_prompt_action(self, action):
        prompt = self.action_prompt
        if prompt is None:
            return
        if action == "prompt.prev":
            self.action_prompt_page = max(0, self.action_prompt_page - 1)
            self._render_action_prompt()
        elif action == "prompt.next":
            self.action_prompt_page += 1
            self._render_action_prompt()
        elif action.startswith("prompt.button."):
            button = prompt["buttons"].get(action)
            if button is not None and button["command"]:
                self._run_script(button["command"])

    def _render_calibration_result(self):
        commands = self.renderer.begin_page("Calibration result")
        if self.calibration_error:
            commands.append(self.renderer.text(
                400, 120, self.calibration_error, "ff3030", "Roboto 10pt",
                "center", max_width=740, truncate=True))
        elif getattr(self, "calibration_cancelled", False):
            commands += [
                self.renderer.text(
                    400, 145, "HEATING CANCELLED", "f2c94c",
                    "JetBrainsMono Bold 16pt", "center", "middle"),
                self.renderer.text(
                    400, 195, "Calibration was stopped before probing",
                    "d9e4e8", "JetBrainsMono 8pt", "center", "middle"),
            ]
        elif self.calibration_kind == "mesh" and self.calibration_mesh:
            matrix = self.calibration_mesh
            values = [cell for row in matrix for cell in row]
            minimum, maximum = min(values), max(values)
            rows, columns = len(matrix), len(matrix[0])
            map_x, map_y, map_width, map_height = 35, 75, 575, 245
            cell_width = max(24, map_width // columns)
            cell_height = max(24, map_height // rows)
            for row_index, row in enumerate(reversed(matrix)):
                for column, value in enumerate(row):
                    x = map_x + column * cell_width
                    y = map_y + row_index * cell_height
                    color = self._mesh_color(value, minimum, maximum)
                    commands += [self.renderer.fill(x, y, cell_width - 3,
                                                    cell_height - 3, color),
                                 self.renderer.text(x + (cell_width - 3) // 2,
                                                    y + (cell_height - 3) // 2,
                                                    "%+.2f" % value, "030607",
                                                    "JetBrainsMono Bold 8pt",
                                                    "center", "middle")]
            commands += [
                self.renderer.text(640, 92, "PROFILE AUTO", "35d9e6",
                                   "JetBrainsMono 8pt", "left", "middle"),
                self.renderer.text(640, 145, "MIN %+.3f" % minimum, "d9e4e8",
                                   "JetBrainsMono 8pt", "left", "middle"),
                self.renderer.text(640, 185, "MAX %+.3f" % maximum, "d9e4e8",
                                   "JetBrainsMono 8pt", "left", "middle"),
                self.renderer.text(640, 225, "RANGE %.3f" % (maximum - minimum),
                                   "f2c94c", "JetBrainsMono 8pt", "left", "middle"),
                self.renderer.text(640, 285, "%d X %d POINTS" % (columns, rows),
                                   "56656c", "JetBrainsMono 8pt", "left", "middle"),
            ]
        elif self.calibration_kind == "screws" and self.calibration_results:
            for index, result in enumerate(self.calibration_results[:5]):
                commands.append(self.renderer.text(
                    100, 75 + index * 48, result["name"], "ffffff", "Roboto 10pt"))
                commands.append(self.renderer.text(
                    700, 75 + index * 48, "%s %s" %
                    (result["direction"], result["turns"]), "00f0f0", "Roboto 10pt",
                    "right"))
        else:
            commands.append(self.renderer.text(400, 150, "Calibration completed",
                                               "00f0f0", "Roboto Bold 14pt", "center"))
        if getattr(self, "calibration_cancelled", False):
            commands += self.renderer.button(
                "cal.done", 270, 355, 260, 70, "DONE")
        elif self._mesh_save_available():
            commands += self.renderer.button(
                "cal.repeat", 35, 355, 220, 70, "REPEAT")
            commands += self.renderer.button(
                "cal.mesh.discard", 290, 355, 220, 70, "DON'T SAVE")
            commands += self.renderer.button(
                "cal.mesh.save", 545, 355, 220, 70, "SAVE",
                state="warning")
        elif self._tuning_save_available():
            commands += self.renderer.button(
                "cal.repeat", 35, 355, 220, 70, "REPEAT")
            commands += self.renderer.button(
                "cal.tuning.discard", 290, 355, 220, 70, "DON'T SAVE")
            commands += self.renderer.button(
                "cal.tuning.save", 545, 355, 220, 70, "SAVE",
                state="warning")
        else:
            commands += self.renderer.button(
                "cal.repeat", 100, 355, 260, 70, "REPEAT")
            commands += self.renderer.button(
                "cal.done", 440, 355, 260, 70, "DONE")
        self.renderer.send(commands)
