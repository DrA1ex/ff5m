## Tests for Feather screen workflows.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import os
import pathlib
import tempfile
import unittest
from unittest import mock

try:
    from tests.test_feather_screen import (
        FEATHER, RESURRECTION, GCodeRecorder, Reactor, StatusObject)
except ImportError:
    from test_feather_screen import (
        FEATHER, RESURRECTION, GCodeRecorder, Reactor, StatusObject)


class VirtualSD:
    def __init__(self, root=None, active=False):
        self.sdcard_dirname = root
        self.active = active

    def is_active(self):
        return self.active


class FinishedProcess:
    def __init__(self, output, returncode=0):
        self.output = output.encode("utf-8")
        self.returncode = returncode
        self.terminated = False

    def poll(self):
        return self.returncode

    def communicate(self):
        return (self.output, None)

    def terminate(self):
        self.terminated = True


def base_controller(state="idle"):
    controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
    controller.reactor = Reactor()
    controller.reactor.register_callback = lambda callback, waketime=None: None
    controller.gcode = GCodeRecorder()
    controller.print_stats = StatusObject(
        {"state": state, "info": {"current_layer": 1, "total_layer": 10}})
    controller.virtual_sdcard = VirtualSD(active=state in ("printing", "paused"))
    controller.pending_action = None
    controller.pending_until = 0
    controller.cancel_requested = False
    controller.cancel_waiting_for_heat = False
    controller.cancel_mode = None
    controller.cancel_phase = None
    controller._filament_request_token = 0
    controller.busy_phase = 0
    controller.print_flow = type("Flow", (), {"variables": {
        "active": False, "cancel_requested": False,
        "cancel_dispatched": False, "phase": "IDLE"}})()
    controller.start_print_macro = type("Start", (), {"variables": {
        "print_started": state in ("printing", "paused")}})()
    controller.page = FEATHER.Page.IDLE_HOME
    controller.print_state = {
        "idle": FEATHER.PrintState.IDLE,
        "printing": FEATHER.PrintState.PRINTING,
        "paused": FEATHER.PrintState.PAUSED,
    }.get(state, FEATHER.PrintState.IDLE)
    controller.debug = False
    controller.toast_until = 0
    controller.toast_message = ""
    controller._toast = lambda message: None
    controller._render_print_page = lambda: None
    controller._render_cancel_confirm = lambda: None
    return controller


class FileWorkflowTest(unittest.TestCase):
    def test_file_browser_sorts_directories_then_newest_files(self):
        with tempfile.TemporaryDirectory() as root:
            os.mkdir(os.path.join(root, "zeta"))
            os.mkdir(os.path.join(root, "Alpha"))
            old = os.path.join(root, "old.gcode")
            new = os.path.join(root, "new.gco")
            ignored = os.path.join(root, "notes.txt")
            for path in (old, new, ignored):
                pathlib.Path(path).write_text("G28\n", encoding="utf-8")
            os.utime(old, (10, 10))
            os.utime(new, (20, 20))

            controller = base_controller()
            controller.virtual_sdcard = VirtualSD(root)
            controller.current_directory = ""
            controller._load_file_entries()
            self.assertEqual([entry["name"] for entry in controller.file_entries],
                             ["Alpha", "zeta", "new.gco", "old.gcode"])

    def test_safe_directory_rejects_parent_traversal(self):
        with tempfile.TemporaryDirectory() as root:
            controller = base_controller()
            controller.virtual_sdcard = VirtualSD(root)
            controller.current_directory = "../outside"
            with self.assertRaisesRegex(RuntimeError, "Invalid print directory"):
                controller._safe_directory()

    def test_start_file_rechecks_path_and_escapes_filename(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, 'part "one".gcode')
            pathlib.Path(path).write_text("G28\n", encoding="utf-8")
            controller = base_controller()
            controller.virtual_sdcard = VirtualSD(root)
            controller.selected_file = {"path": path}
            controller._start_selected_file()
            self.assertEqual(
                controller.gcode.commands,
                ['SDCARD_PRINT_FILE FILENAME="part \\"one\\".gcode"'])
            os.unlink(path)
            with self.assertRaisesRegex(RuntimeError, "no longer available"):
                controller._start_selected_file()


class PrintWorkflowTest(unittest.TestCase):
    def test_filament_is_rejected_during_start_print_preparation(self):
        controller = base_controller("printing")
        controller.print_flow.variables.update(active=True, phase="HEATING")
        controller.start_print_macro.variables["print_started"] = False
        notices = []
        controller._toast = notices.append

        controller._handle_print_action("print.filament")

        self.assertEqual(controller.gcode.commands, [])
        self.assertEqual(notices, ["Available after print preparation"])

    def test_cancel_invalidates_filament_request_waiting_on_pause(self):
        controller = base_controller("printing")
        controller.page = FEATHER.Page.PRINTING
        opened = []
        controller._open_filament = lambda from_pause: opened.append(from_pause)
        commands = []

        def run(command):
            commands.append(command)
            if command == "PAUSE":
                controller.page = FEATHER.Page.CANCEL_CONFIRM
                controller._handle_print_action("print.cancel.confirm")
            elif command == "CANCEL_PRINT":
                controller.print_stats.status["state"] = "cancelled"

        controller._run_script = run
        controller._handle_print_action("print.filament")

        self.assertEqual(commands, ["PAUSE", "CANCEL_PRINT"])
        self.assertEqual(opened, [])

    def test_pause_resume_and_cancel_are_state_gated(self):
        controller = base_controller("printing")
        controller._handle_print_action("print.pause")
        self.assertEqual(controller.gcode.commands, ["PAUSE"])
        self.assertEqual(controller.pending_action, "print.pause")

        controller.gcode.commands[:] = []
        controller.pending_action = None
        controller._handle_print_action("print.resume")
        self.assertEqual(controller.gcode.commands, [])

        controller._handle_print_action("print.cancel")
        self.assertEqual(controller.page, FEATHER.Page.CANCEL_CONFIRM)

    def test_cancel_requires_confirmation_before_macro(self):
        controller = base_controller("paused")
        pages = []
        controller._show_page = pages.append
        controller._handle_print_action("print.cancel")
        self.assertEqual(pages, [FEATHER.Page.CANCEL_CONFIRM])
        self.assertEqual(controller.gcode.commands, [])
        controller._handle_print_action("print.cancel.confirm")
        self.assertEqual(controller.gcode.commands, ["CANCEL_PRINT"])

    def test_started_print_uses_direct_cancel_even_with_active_flow(self):
        controller = base_controller("printing")
        controller.print_flow.variables.update(active=True, phase="PRINTING")
        controller.start_print_macro.variables["print_started"] = True
        controller._handle_print_action("print.cancel.confirm")
        self.assertEqual(controller.gcode.commands, ["CANCEL_PRINT"])
        self.assertEqual(controller.cancel_mode, "direct")

    def test_cancel_during_heat_uses_cooperative_abort_only(self):
        controller = base_controller("printing")
        controller.print_flow.variables.update(active=True, phase="HEATING")
        controller.start_print_macro.variables["print_started"] = False
        controller.temperature_wait = type("Wait", (), {
            "variables": {"active": True, "cancel": False}})()
        recorder = GCodeRecorder()
        def run(command):
            recorder.commands.append(command)
            if command == "FEATHER_ABORT":
                controller.print_flow.variables["cancel_requested"] = True
                controller.temperature_wait.variables["cancel"] = True
        controller.gcode.run_script_from_command = run
        controller._handle_print_action("print.cancel.confirm")
        self.assertEqual(recorder.commands, ["FEATHER_ABORT"])
        self.assertTrue(controller.cancel_requested)
        self.assertTrue(controller.cancel_waiting_for_heat)
        self.assertNotIn("CANCEL_PRINT", recorder.commands)

    def test_unaccepted_cooperative_abort_never_dispatches_cancel_print(self):
        controller = base_controller("printing")
        controller.print_flow.variables.update(active=True, phase="HOMING")
        controller.start_print_macro.variables["print_started"] = False
        with self.assertRaisesRegex(RuntimeError, "did not accept"):
            controller._handle_print_action("print.cancel.confirm")
        self.assertEqual(controller.gcode.commands, ["FEATHER_ABORT"])
        self.assertNotIn("CANCEL_PRINT", controller.gcode.commands)

    def test_cancel_during_homing_bypasses_mutex_runner(self):
        controller = base_controller("printing")
        controller.print_flow.variables.update(active=True, phase="HOMING")
        controller.start_print_macro.variables["print_started"] = False
        calls = []

        def immediate(command):
            calls.append(("immediate", command))
            controller.print_flow.variables["cancel_requested"] = True

        def serialized(command):
            calls.append(("serialized", command))

        controller.gcode.run_script_from_command = immediate
        controller.gcode.run_script = serialized
        controller._handle_print_action("print.cancel.confirm")
        self.assertEqual(calls, [("immediate", "FEATHER_ABORT")])
        self.assertFalse(controller.cancel_waiting_for_heat)

    def test_normal_cancel_is_rejected_by_immediate_dispatch(self):
        controller = base_controller()
        with self.assertRaisesRegex(ValueError, "Unsupported immediate"):
            controller._run_immediate_command("CANCEL_PRINT")

    def test_feather_prefers_mutex_serialized_gcode_runner(self):
        controller = base_controller()
        calls = []
        controller.gcode.run_script = lambda command: calls.append(("serialized", command))
        controller.gcode.run_script_from_command = (
            lambda command: calls.append(("direct", command)))
        controller._run_script("G28")
        self.assertEqual(calls, [("serialized", "G28")])

    def test_pending_cancel_page_has_no_repeat_or_back_hitboxes(self):
        controller = base_controller("paused")
        controller.pending_action = "print.cancel.confirm"
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        FEATHER.FeatherScreen._render_cancel_confirm(controller)
        drawing = "\n".join(batches[0])
        self.assertIn("CANCELLING PRINT", drawing)
        self.assertIn("REQUEST ACCEPTED // CONTROLS LOCKED", drawing)
        self.assertNotIn("--batch hitbox --id", drawing)
        self.assertNotIn("--id ", drawing)

    def test_print_state_transition_selects_correct_page(self):
        controller = base_controller("idle")
        controller._progress_floor = 0.75
        controller._m73_active = True
        pages = []
        controller._show_page = pages.append
        controller._change_print_state(FEATHER.PrintState.PRINTING, "printing")
        controller._change_print_state(FEATHER.PrintState.PAUSED, "paused")
        self.assertEqual(pages, [FEATHER.Page.PRINTING, FEATHER.Page.PAUSED])
        self.assertEqual(controller._progress_floor, 0.0)
        self.assertFalse(controller._m73_active)

    def test_dashboard_remains_visible_after_explicit_print_home(self):
        controller = base_controller("printing")
        controller.page = FEATHER.Page.IDLE_HOME
        controller.home_during_print = True
        pages = []
        controller._show_page = pages.append

        controller._change_print_state(FEATHER.PrintState.PAUSED, "paused")
        controller._change_print_state(FEATHER.PrintState.PRINTING, "printing")

        self.assertEqual(pages, [])

    def test_filament_back_uses_live_terminal_state(self):
        controller = base_controller("paused")
        controller.page = FEATHER.Page.FILAMENT_MATERIAL
        controller.filament_from_pause = True
        controller.print_stats.status["state"] = "cancelled"
        pages = []
        controller._show_page = pages.append

        controller._go_back()

        self.assertEqual(pages, [FEATHER.Page.IDLE_HOME])

    def test_print_state_change_does_not_drop_accepted_cancel(self):
        controller = base_controller("printing")
        controller.pending_action = "print.cancel.confirm"
        controller.cancel_requested = True
        controller.page = FEATHER.Page.CANCEL_CONFIRM
        controller._show_page = lambda page: None
        controller._change_print_state(FEATHER.PrintState.PAUSED, "paused")
        self.assertEqual(controller.pending_action, "print.cancel.confirm")
        self.assertTrue(controller.cancel_requested)

    def test_start_print_macro_has_cooperative_checks_after_long_phases(self):
        root = pathlib.Path(__file__).parents[1]
        macros = (root / "macros" / "base.cfg").read_text(encoding="utf-8")
        start = macros.split("[gcode_macro _START_PRINT]", 1)[1].split(
            "[gcode_macro _WAIT_TEMPERATURE]", 1)[0]
        self.assertIn("G28\n    _PRINT_FLOW_CHECK", start)
        self.assertIn("_PRINT_FLOW_PHASE PHASE=HEATING", start)
        self.assertIn("_PRINT_FLOW_PHASE PHASE=PRIMING", start)
        self.assertGreaterEqual(start.count("_PRINT_FLOW_CHECK"), 5)
        flow_check = macros.split("[gcode_macro _PRINT_FLOW_CHECK]", 1)[1].split(
            "[gcode_macro _START_PRINT]", 1)[0]
        self.assertEqual(flow_check.count("CANCEL_PRINT"), 1)

    def test_feather_abort_is_an_immediate_gcode_command(self):
        root = pathlib.Path(__file__).parents[1]
        gcode = (root / ".py" / "klipper" / "patches" /
                 "gcode.py").read_text(encoding="utf-8")
        self.assertIn('"FEATHER_ABORT"', gcode)
        self.assertIn("flow.variables[\"cancel_requested\"] = True", gcode)

    def test_screw_tune_cleans_or_uses_cooldown_and_repeat_only_probes(self):
        root = pathlib.Path(__file__).parents[1]
        macros = (root / "macros" / "base.cfg").read_text(encoding="utf-8")
        tune = macros.split("[gcode_macro BED_LEVEL_SCREWS_TUNE]", 1)[1].split(
            "[gcode_macro BED_LEVEL_SCREWS_PROBE]", 1)[0]
        probe = macros.split("[gcode_macro BED_LEVEL_SCREWS_PROBE]", 1)[1].split(
            "[gcode_macro _CHECK_BED_MESH]", 1)[0]
        self.assertIn(
            "CLEAR_NOZZLE EXTRUDER_TEMP={extruder_temp} BED_TEMP={bed_temp}",
            tune)
        self.assertNotIn("M140", tune)
        self.assertIn("M104 S{cooldown_t}", tune)
        self.assertNotIn(
            "_WAIT_TEMPERATURE CMD=M140 VALUE={bed_temp}", tune)
        self.assertIn(
            "_WAIT_TEMPERATURE CMD=M104 VALUE={cooldown_t} BELOW=2 ABOVE=3",
            tune)
        self.assertIn("BED_LEVEL_SCREWS_PROBE", tune)
        self.assertIn("LOAD_CELL_TARE", probe)
        self.assertIn("SCREWS_TILT_CALCULATE", probe)
        self.assertNotIn("_WAIT_TEMPERATURE", probe)
        self.assertNotIn("CLEAR_NOZZLE", probe)
        self.assertNotIn("G28", probe)

    def test_cancelled_temperature_wait_only_reports_abort(self):
        root = pathlib.Path(__file__).parents[1]
        macros = (root / "macros" / "base.cfg").read_text(encoding="utf-8")
        final = macros.split(
            "[gcode_macro _WAIT_TEMPERATURE_FINAL_CHECK]", 1)[1].split(
                "[gcode_macro _RAISE_WITH_PRINT_CANCEL]", 1)[0]
        self.assertIn(
            '"Temperature waiting cancelled." if cancelled', final)
        self.assertIn("_RAISE_WITH_PRINT_CANCEL", final)
        self.assertNotIn("TURN_OFF_HEATERS", final)
        self.assertNotIn("M104 S0", final)

    def test_clear_nozzle_reports_homing_before_g28(self):
        root = pathlib.Path(__file__).parents[1]
        macros = (root / "macros" / "base.cfg").read_text(encoding="utf-8")
        clear_nozzle = macros.split(
            "[gcode_macro CLEAR_NOZZLE]", 1)[1].split(
                "[gcode_macro _CLEAR_NOZZLE_PROBE]", 1)[0]
        homing = clear_nozzle.index('_PRINT_STATUS S="HOMING..."')
        home = clear_nozzle.index("G28")
        self.assertLess(homing, home)

class MotionHeatSettingsTest(unittest.TestCase):
    def test_continuous_touch_updates_planner_and_release(self):
        controller = base_controller()
        controller.page = FEATHER.Page.CONTROL_MOVE
        controller.move_mode = "joystick"
        controller.command_depth = 0
        controller.dimmed = False
        controller.last_touch_time = 0.0
        controller.joystick_suppressed = None
        controller.joystick_action = None
        controller.joystick_timer = object()
        controller.joystick_timer_active = False
        updates = []
        controller.reactor.NOW = 0.0
        controller.reactor.update_timer = (
            lambda timer, when: updates.append((timer, when)))
        controller.renderer = type("Renderer", (), {
            "decode_action": lambda self, action: action.split(":", 1)[-1],
        })()
        controller.toolhead = StatusObject({"homed_axes": "xyz"})

        class Planner:
            def __init__(self):
                self.events = []

            def set_xy(self, *args):
                self.events.append(("xy", args[0], args[1]))

            def set_z(self, *args):
                self.events.append(("z", args[0]))

            def release(self):
                self.events.append(("release",))

        controller.joystick = Planner()
        controller._handle_continuous_touch(
            "touch 7:move.joy.xy begin 365 220")
        controller._handle_continuous_touch(
            "touch 7:move.joy.xy move 400 180")
        controller._handle_continuous_touch(
            "touch 7:move.joy.xy end 400 180")

        self.assertEqual(controller.joystick.events,
                         [("xy", 365, 220), ("xy", 400, 180), ("release",)])
        self.assertIsNone(controller.joystick_action)
        # Raw touch updates only replace the latest vector. The fixed-rate
        # motion loop is started once instead of being forced to NOW for every
        # coordinate report.
        self.assertEqual(len(updates), 1)
        self.assertTrue(controller.joystick_timer_active)

    def test_low_z_warning_blocks_new_xy_touch_without_interrupting_z(self):
        controller = base_controller()
        controller.page = FEATHER.Page.CONTROL_MOVE
        controller.move_mode = "joystick"
        controller.command_depth = 0
        controller.dimmed = False
        controller.last_touch_time = 0.0
        controller.joystick_suppressed = None
        controller.joystick_action = None
        controller.joystick_timer = object()
        controller.joystick_timer_active = False
        controller.move_caution_signature = (True, "active")
        controller.reactor.NOW = 0.0
        controller.reactor.update_timer = lambda timer, when: None
        controller.renderer = type("Renderer", (), {
            "decode_action": lambda self, action: action.split(":", 1)[-1],
        })()
        controller.toolhead = StatusObject({"homed_axes": "xyz"})

        class Planner:
            def __init__(self):
                self.events = []

            def set_xy(self, *args):
                self.events.append(("xy",))

            def set_z(self, *args):
                self.events.append(("z", args[0]))

            def release(self):
                self.events.append(("release",))

        controller.joystick = Planner()
        controller._update_joystick_feedback = lambda *args, **kwargs: None

        controller._handle_continuous_touch(
            "touch 9:move.joy.xy begin 240 229")
        controller._handle_continuous_touch(
            "touch 9:move.joy.xy move 300 229")
        controller._handle_continuous_touch(
            "touch 9:move.joy.xy end 300 229")
        controller._handle_continuous_touch(
            "touch 9:move.joy.z begin 510 180")
        controller._handle_continuous_touch(
            "touch 9:move.joy.z move 510 160")
        controller._handle_continuous_touch(
            "touch 9:move.joy.z end 510 160")

        self.assertEqual(
            controller.joystick.events,
            [("z", 180), ("z", 160), ("release",)])
        self.assertIsNone(controller.joystick_action)
        self.assertIsNone(controller.joystick_suppressed)

    def test_dimmed_joystick_gesture_only_wakes_until_release(self):
        controller = base_controller()
        controller.page = FEATHER.Page.CONTROL_MOVE
        controller.move_mode = "joystick"
        controller.command_depth = 0
        controller.dimmed = True
        controller.last_touch_time = 0.0
        controller.joystick_suppressed = None
        controller.joystick_action = None
        controller.renderer = type("Renderer", (), {
            "decode_action": lambda self, action: action.split(":", 1)[-1],
        })()
        controller._wake_if_dimmed = lambda: True
        controller.joystick = mock.Mock()

        controller._handle_continuous_touch(
            "touch 8:move.joy.z begin 540 100")
        controller._handle_continuous_touch(
            "touch 8:move.joy.z move 540 90")
        controller._handle_continuous_touch(
            "touch 8:move.joy.z end 540 90")

        controller.joystick.assert_not_called()
        self.assertIsNone(controller.joystick_suppressed)

    def test_joystick_tick_queues_direct_motion_and_restores_toolhead_accel(self):
        controller = base_controller()
        controller.page = FEATHER.Page.CONTROL_MOVE
        controller.move_mode = "joystick"
        controller.print_state = FEATHER.PrintState.IDLE
        controller.joystick_action = "move.joy.xy"
        controller.joystick_queued = False
        controller.joystick_timer = object()
        controller.reactor.NEVER = 1.0e30

        class Toolhead:
            def __init__(self):
                self.max_accel = 20000.0
                self.requested_accel_to_decel = 5000.0
                self.max_accel_to_decel = 5000.0
                self.buffer_time_start = 0.250
                self.buffer_time_low = 1.000
                self.position = [0.0, 0.0, 100.0, 0.0]
                self.moves = []
                self.flushes = 0
                self.move_queue = type("MoveQueue", (), {
                    "queue": [],
                    "set_flush_time": lambda queue, duration: setattr(
                        queue, "junction_flush", duration),
                })()

            def _calc_junction_deviation(self):
                self.max_accel_to_decel = min(
                    self.requested_accel_to_decel, self.max_accel)

            def get_status(self, eventtime):
                return {"homed_axes": "xyz"}

            def check_busy(self, eventtime):
                pending = sum(getattr(move, "min_move_t", 0.0)
                              for move in self.move_queue.queue)
                return 0.0, -pending, not self.move_queue.queue

            def get_position(self):
                return list(self.position)

            def manual_move(self, position, speed):
                self.moves.append((list(position), speed, self.max_accel))
                self.position[:3] = position
                self.move_queue.queue.append(
                    type("Move", (), {
                        "min_move_t": FEATHER.joystick_ui.PERIOD})())

            def flush_step_generation(self):
                self.flushes += 1
                self.move_queue.queue[:] = []

        controller.toolhead = Toolhead()
        controller.joystick = FEATHER.joystick_ui.JoystickPlanner(
            600.0, 10000.0, 25.0, 250.0,
            ((-110.0, 110.0), (-110.0, 110.0), (0.0, 220.0)))
        controller.joystick.set_xy(365, 220, 100.0, 240, 220, 125)

        next_wake = controller._joystick_tick(100.0)

        self.assertAlmostEqual(
            next_wake, 100.0 + FEATHER.joystick_ui.PERIOD)
        self.assertGreaterEqual(len(controller.toolhead.moves), 2)
        self.assertLessEqual(len(controller.toolhead.moves),
                             FEATHER.joystick_motion.MAX_REFILL_SEGMENTS)
        self.assertGreater(controller.toolhead.moves[0][2], 0.0)
        self.assertEqual(controller.toolhead.moves[0][2],
                         controller.joystick.xy_accel)
        self.assertEqual(controller.toolhead.max_accel, 20000.0)
        self.assertTrue(controller.joystick_queued)

    def test_joystick_start_waits_for_short_toolhead_tail(self):
        controller = base_controller()
        controller.page = FEATHER.Page.CONTROL_MOVE
        controller.move_mode = "joystick"
        controller.joystick_action = "move.joy.xy"
        controller.joystick_timer_active = True
        controller.joystick_busy_since = None
        controller.reactor.NEVER = 1.0e30
        controller.toolhead = StatusObject({"homed_axes": "xyz"})
        controller._update_joystick_feedback = lambda *args, **kwargs: None
        notices = []
        controller._toast = notices.append

        class Planner:
            held = True

            def __init__(self):
                self.released = False

            def watchdog(self, eventtime):
                return False

            def is_moving(self):
                return not self.released

            def release(self):
                self.held = False
                self.released = True

        class BusyStream:
            active = False

            def start(self, eventtime):
                raise FEATHER.joystick_motion.StreamBusy()

        controller.joystick = Planner()
        controller.joystick_stream = BusyStream()

        retry = controller._joystick_tick(100.0)

        self.assertAlmostEqual(
            retry, 100.0 + FEATHER.joystick_ui.QUEUE_RETRY)
        self.assertFalse(controller.joystick.released)
        self.assertEqual(controller.joystick_action, "move.joy.xy")
        self.assertEqual(notices, [])

        stopped = controller._joystick_tick(
            100.0 + FEATHER.joystick_motion.START_BUSY_GRACE + 0.001)

        self.assertEqual(stopped, controller.reactor.NEVER)
        self.assertTrue(controller.joystick.released)
        self.assertIsNone(controller.joystick_action)
        self.assertEqual(notices, ["TOOLHEAD BUSY"])

    def test_joystick_uses_feather_limits_and_actual_z_limits(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.reactor = Reactor()

        class Kinematics:
            max_z_velocity = 25.0
            max_z_accel = 500.0

        class Toolhead:
            def get_status(self, eventtime):
                return {
                    "axis_minimum": (-120.0, -120.0, 5.0),
                    "axis_maximum": (120.0, 120.0, 230.0),
                    "max_velocity": 600.0,
                    "max_accel": 20000.0,
                }

            def get_kinematics(self):
                return Kinematics()

        controller.toolhead = Toolhead()
        controller.joystick_limits = (
            (-97.0, 103.0), (-89.0, 91.0), (0.0, 220.0))
        controller._create_joystick_planner()

        self.assertEqual(controller.joystick.xy_speed, 300.0)
        self.assertEqual(controller.joystick.z_speed, 12.5)
        self.assertEqual(controller.joystick.xy_accel, 10000.0)
        self.assertEqual(controller.joystick.z_accel, 250.0)
        self.assertEqual(
            controller.joystick.limits,
            ((-97.0, 103.0), (-89.0, 91.0), (5.0, 220.0)))

    def test_heat_page_draws_values_immediately_and_refreshes_fan(self):
        controller = base_controller()
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.extruder = StatusObject({"temperature": 21.5, "target": 220})
        controller.heater_bed = StatusObject({"temperature": 24.0, "target": 60})
        controller.fan = StatusObject({"speed": 0.25})
        controller.preheat = dict(FEATHER.PREHEAT)

        controller._render_heat()

        initial = "\n".join(batches[0])
        self.assertIn("21.5 / 220 C", initial)
        self.assertIn("24.0 / 60 C", initial)
        self.assertIn("25%", initial)
        controller.fan.status["speed"] = 0.5
        controller._update_heat_status(101)
        self.assertIn("50%", "\n".join(batches[-1]))

    def test_move_offers_only_combined_homing_commands(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.jog_step = 1.0
        controller._require_idle = lambda: None
        blocking = []
        controller._run_blocking_gcode = (
            lambda command, message: blocking.append((command, message)))
        controller._toast = lambda message: None

        controller._handle_move_action("move.homeall")
        controller._handle_move_action("move.homexy")

        self.assertEqual(blocking, [("G28", "HOMING..."),
                                    ("G28 X Y", "HOMING...")])

    def test_move_requires_homed_axis_and_uses_conservative_speed(self):
        controller = base_controller()
        controller.jog_step = 10.0
        controller.toolhead = StatusObject({
            "homed_axes": "y", "position": (0.0, 0.0, 0.0)})
        with self.assertRaisesRegex(RuntimeError, "Home X"):
            controller._handle_move_action("move.xp")
        controller.toolhead.status["homed_axes"] = "xyz"
        controller._handle_move_action("move.xm")
        controller._handle_move_action("move.zp")
        self.assertEqual(controller.gcode.commands,
                         ["MOVE_SAFE X=-10 ABSOLUTE=1 F=6000",
                          "MOVE_SAFE Z=10 ABSOLUTE=1 F=600"])

    def test_step_controls_share_joystick_limits(self):
        controller = base_controller()
        controller.jog_step = 10.0
        controller.joystick_limits = (
            (-100.0, 100.0), (-90.0, 90.0), (0.0, 210.0))
        controller.toolhead = StatusObject({
            "homed_axes": "xyz",
            "position": (95.0, -85.0, 205.0),
            "axis_minimum": (-120.0, -120.0, 0.0),
            "axis_maximum": (120.0, 120.0, 220.0),
        })

        controller._handle_move_action("move.xp")
        controller._handle_move_action("move.ym")
        controller._handle_move_action("move.zp")

        self.assertEqual(
            controller.gcode.commands,
            ["MOVE_SAFE X=100 ABSOLUTE=1 F=6000",
             "MOVE_SAFE Y=-90 ABSOLUTE=1 F=6000",
             "MOVE_SAFE Z=210 ABSOLUTE=1 F=600"])

    def test_step_z_respects_toolhead_range_and_does_not_move_at_limit(self):
        controller = base_controller()
        controller.jog_step = 10.0
        controller.joystick_limits = (
            (-100.0, 100.0), (-90.0, 90.0), (0.0, 220.0))
        controller.toolhead = StatusObject({
            "homed_axes": "xyz",
            "position": (0.0, 0.0, 205.0),
            "axis_minimum": (-120.0, -120.0, 5.0),
            "axis_maximum": (120.0, 120.0, 220.0),
        })
        notices = []
        controller._toast = notices.append

        controller._handle_move_action("move.zp")
        controller.toolhead.status["position"] = (0.0, 0.0, 210.0)
        controller._handle_move_action("move.zp")

        self.assertEqual(controller.gcode.commands,
                         ["MOVE_SAFE Z=210 ABSOLUTE=1 F=600"])
        self.assertEqual(notices, ["Moved Z 5 mm", "Z LIMIT REACHED"])

    def test_step_does_not_reverse_when_current_position_is_outside_limit(self):
        controller = base_controller()
        controller.jog_step = 10.0
        controller.joystick_limits = (
            (-100.0, 100.0), (-90.0, 90.0), (0.0, 220.0))
        controller.toolhead = StatusObject({
            "homed_axes": "xyz",
            "position": (105.0, 0.0, 100.0),
            "axis_minimum": (-120.0, -120.0, 0.0),
            "axis_maximum": (120.0, 120.0, 230.0),
        })
        notices = []
        controller._toast = notices.append

        controller._handle_move_action("move.xp")
        controller._handle_move_action("move.xm")

        self.assertEqual(controller.gcode.commands,
                         ["MOVE_SAFE X=95 ABSOLUTE=1 F=6000"])
        self.assertEqual(notices, ["X LIMIT REACHED", "Moved X -10 mm"])

    def test_low_z_warning_blocks_step_xy_but_keeps_step_z_available(self):
        controller = base_controller()
        controller.jog_step = 1.0
        controller.move_caution_signature = (True, "available")
        controller.toolhead = StatusObject({
            "homed_axes": "xyz", "position": (0.0, 0.0, 20.0)})

        controller._handle_move_action("move.xp")
        controller._handle_move_action("move.yp")
        controller._handle_move_action("move.homeall")
        controller._handle_move_action("move.zm")

        self.assertEqual(
            controller.gcode.commands, ["MOVE_SAFE Z=19 ABSOLUTE=1 F=600"])

    def test_preheat_fan_and_cooldown_commands(self):
        controller = base_controller()
        extruder = StatusObject({"temperature": 20, "target": 0})
        extruder.heater = type("Heater", (), {"min_temp": 0, "max_temp": 251})()
        controller.extruder = extruder
        controller.heater_bed = StatusObject({"temperature": 20, "target": 0})
        controller.heater_bed.min_temp = 0
        controller.heater_bed.max_temp = 91
        controller.preheat = dict(FEATHER.PREHEAT)
        controller.fan = StatusObject({"speed": 0.0})

        controller._handle_heat_action("heat.preheat.ABS")
        controller._handle_heat_action("heat.fan50")
        controller._handle_heat_action("heat.alloff")
        self.assertEqual(controller.gcode.commands, [
            "PREHEAT_MATERIAL MATERIAL=ABS EXTRUDER_TEMP=250 BED_TEMP=90",
            "M106 S128", "TURN_OFF_HEATERS"])

    def test_settings_clamp_values_and_toggle_sound(self):
        controller = base_controller()
        controller.params = type("Params", (), {
            "variables": {"backlight": 100, "backlight_eco": 1, "sound": 1}})()
        controller._render_settings = lambda: None
        backlight = []
        controller._set_backlight = backlight.append
        controller._handle_settings_action("settings.brightness.plus")
        controller._handle_settings_action("settings.eco.minus")
        controller._handle_settings_action("settings.sound")
        self.assertEqual(controller.gcode.commands, [
            "SET_MOD PARAM=backlight VALUE=100",
            "SET_MOD PARAM=backlight_eco VALUE=1",
            "SET_MOD PARAM=sound VALUE=0"])
        self.assertEqual(backlight, [100])

    def test_backlight_enable_is_separate_from_brightness(self):
        controller = base_controller()
        device = mock.mock_open()
        enable_error = PermissionError(FEATHER.errno.EPERM, "already enabled")
        with mock.patch("builtins.open", device), mock.patch.object(
                FEATHER.fcntl, "ioctl",
                side_effect=[enable_error, 0]) as ioctl:
            controller._enable_backlight()
            controller._set_backlight(65)
        self.assertEqual(controller.gcode.commands, [])
        self.assertEqual(ioctl.call_count, 2)
        self.assertEqual(ioctl.call_args_list[0].args[1],
                         FEATHER.DISP_LCD_BACKLIGHT_ENABLE)
        self.assertEqual(ioctl.call_args_list[1].args[1],
                         FEATHER.DISP_LCD_SET_BRIGHTNESS)

    def test_backlight_unexpected_failure_does_not_use_gcode(self):
        controller = base_controller()
        with self.assertLogs(level="ERROR") as logs, mock.patch(
                "builtins.open", side_effect=OSError("cannot open")):
            controller._set_backlight(45)
        self.assertEqual(controller.gcode.commands, [])
        self.assertIn("backlight update failed", "\n".join(logs.output))


class FilamentAndCalibrationWorkflowTest(unittest.TestCase):
    def test_filament_page_cannot_replace_cancel_confirmation(self):
        controller = base_controller("paused")
        controller.page = FEATHER.Page.CANCEL_CONFIRM
        pages = []
        controller._show_page = pages.append

        opened = controller._open_filament(True)

        self.assertFalse(opened)
        self.assertEqual(pages, [])

    def test_material_selection_heats_and_opens_action_page(self):
        controller = base_controller("paused")
        controller.filament_from_pause = True
        controller.preheat = dict(FEATHER.PREHEAT)
        extruder = StatusObject({"temperature": 25, "target": 210})
        extruder.heater = type("Heater", (), {"min_temp": 0, "max_temp": 300})()
        controller.extruder = extruder
        controller.heater_bed = type("Bed", (), {"min_temp": 0, "max_temp": 130})()
        pages = []
        controller._show_page = pages.append
        controller._handle_filament_action("filament.PETG")
        self.assertEqual(controller.gcode.commands,
                         ["SET_MATERIAL MATERIAL=PETG\nM104 S250"])
        self.assertEqual(pages, [FEATHER.Page.FILAMENT_ACTION])

    def test_paused_filament_done_restores_target_and_resumes(self):
        controller = base_controller("paused")
        controller.filament_from_pause = True
        controller.filament_original_target = 215
        pages = []
        controller._show_page = pages.append
        controller._finish_filament(True)
        self.assertEqual(controller.gcode.commands, ["M104 S215", "RESUME"])
        self.assertEqual(pages, [FEATHER.Page.PAUSED])

    def test_cancelled_filament_flow_does_not_reheat_or_return_to_print(self):
        controller = base_controller("paused")
        controller.filament_from_pause = True
        controller.filament_original_target = 215
        controller.print_stats.status["state"] = "cancelled"
        pages = []
        controller._show_page = pages.append

        controller._finish_filament(False)

        self.assertEqual(controller.gcode.commands, [])
        self.assertEqual(pages, [FEATHER.Page.IDLE_HOME])
        self.assertFalse(controller.filament_from_pause)

    def test_load_persists_selected_material(self):
        controller = base_controller("paused")
        controller.filament_from_pause = True
        controller.filament_material = "ABS-PC"
        controller.extruder = StatusObject({"temperature": 220})
        controller.extruder.min_extrude_temp = 170
        calls = []
        controller._run_blocking_gcode = (
            lambda command, message: calls.append((command, message)))
        controller._handle_filament_action("filament.load")
        self.assertEqual(calls, [("LOAD_FILAMENT MATERIAL=ABS-PC",
                                  "LOAD FILAMENT...")])

    def test_material_macros_support_fluidd_and_persist_through_mod_params(self):
        root = pathlib.Path(__file__).parents[1]
        macros = (root / "macros" / "base.cfg").read_text(encoding="utf-8")
        self.assertIn("[gcode_macro SET_MATERIAL]", macros)
        self.assertIn("SET_MOD PARAM=current_material", macros)
        self.assertIn("[gcode_macro PREHEAT_MATERIAL]", macros)
        self.assertIn('SET_MATERIAL MATERIAL="{params.MATERIAL}"', macros)
        self.assertIn("_LOAD_MATERIAL_HEATUP MATERIAL=PETG TEMP=250", macros)

    def test_saved_z_adjust_uses_homing_origin_not_base_position(self):
        root = pathlib.Path(__file__).parents[1]
        macros = (root / "macros" / "base.cfg").read_text(encoding="utf-8")
        wrapper = macros.split("[gcode_macro SET_GCODE_OFFSET]", 1)[1].split(
            "[gcode_macro LOAD_GCODE_OFFSET]", 1)[0]
        self.assertIn("printer.gcode_move.homing_origin.z + z_adj", wrapper)
        self.assertNotIn("base_position", wrapper)

    def test_paper_closer_moves_to_smaller_local_z_without_loader(self):
        controller = base_controller()
        controller.z_calibration = FEATHER.ZCalibrationSession()
        controller.z_calibration.begin(0.2, None, "", -0.25, False)
        controller.z_calibration.choose_zone("center")
        controller.z_calibration.set_trigger(-0.5)
        controller.z_calibration.step = 0.010
        controller._render_z_paper = lambda: None
        blocking = []
        controller._run_blocking_gcode = (
            lambda command, message: blocking.append((command, message)))

        controller._handle_calibration_action("z.closer")

        self.assertEqual(controller.gcode.commands,
                         ["MOVE_SAFE Z=-0.010000 ABSOLUTE=1 F=300"])
        self.assertAlmostEqual(controller.z_calibration.local_z, 0.49)
        self.assertEqual(blocking, [])

    def test_paper_farther_moves_to_larger_local_z(self):
        controller = base_controller()
        controller.z_calibration = FEATHER.ZCalibrationSession()
        controller.z_calibration.begin(0.2, None, "", -0.25, False)
        controller.z_calibration.choose_zone("center")
        controller.z_calibration.set_trigger(-0.5)
        controller.z_calibration.step = 0.050
        controller._render_z_paper = lambda: None

        controller._handle_calibration_action("z.farther")

        self.assertEqual(controller.gcode.commands,
                         ["MOVE_SAFE Z=0.050000 ABSOLUTE=1 F=300"])

    def test_live_z_adjust_uses_original_macro_without_saving(self):
        controller = base_controller("printing")
        controller.page = FEATHER.Page.LIVE_Z_OFFSET
        controller.toolhead = StatusObject({"homed_axes": "xyz"})
        controller.gcode_move = StatusObject(
            {"homing_origin": (0.0, 0.0, 0.2)})
        controller.params = type("Params", (), {
            "variables": {"z_offset": 0.1, "load_zoffset": 1}})()
        controller.z_offset_limit = 2.0
        controller.z_adjust_warning_threshold = 0.3
        controller.live_z_limit_warned = False
        controller.live_z_dialog = None
        controller.live_z_step = 0.01
        controller._render_live_z_offset = lambda: None

        controller._handle_live_z_action("live_z.closer")
        controller._handle_live_z_action("live_z.farther")

        self.assertEqual(controller.gcode.commands, [
            "_SET_GCODE_OFFSET Z_ADJUST=-0.010 MOVE=1",
            "_SET_GCODE_OFFSET Z_ADJUST=+0.010 MOVE=1",
        ])
        self.assertNotIn("SET_MOD", "\n".join(controller.gcode.commands))

    def test_live_z_warns_once_after_crossing_half_mm(self):
        controller = base_controller("printing")
        controller.page = FEATHER.Page.LIVE_Z_OFFSET
        controller.toolhead = StatusObject({"homed_axes": "xyz"})
        controller.gcode_move = StatusObject(
            {"homing_origin": (0.0, 0.0, 0.29)})
        controller.params = type("Params", (), {
            "variables": {"z_offset": 0.0, "load_zoffset": 1}})()
        controller.z_offset_limit = 2.0
        controller.z_adjust_warning_threshold = 0.3
        controller.live_z_limit_warned = False
        controller.live_z_dialog = None
        controller._render_live_z_offset = lambda: None

        def run(command, _message):
            controller.gcode.commands.append(command)
            controller.gcode_move.status["homing_origin"] = (0.0, 0.0, 0.34)

        controller._run_blocking_gcode = run
        controller._apply_live_z_adjust(0.05)

        self.assertEqual(controller.live_z_dialog, "limit")
        self.assertTrue(controller.live_z_limit_warned)
        controller.live_z_dialog = None
        controller._apply_live_z_adjust(0.05)
        self.assertIsNone(controller.live_z_dialog)

    def test_live_z_save_can_enable_auto_load(self):
        controller = base_controller("paused")
        controller.page = FEATHER.Page.LIVE_Z_OFFSET
        controller.toolhead = StatusObject({"homed_axes": "xyz"})
        controller.gcode_move = StatusObject(
            {"homing_origin": (0.0, 0.0, 0.235)})
        controller.params = type("Params", (), {
            "variables": {"z_offset": 0.1, "load_zoffset": 0}})()
        controller.live_z_dialog = "save"
        controller._render_live_z_offset = lambda: None

        controller._handle_live_z_action("live_z.save.yes")

        self.assertEqual(controller.gcode.commands, [
            "SET_MOD PARAM=z_offset VALUE=0.235\n"
            "SET_MOD PARAM=load_zoffset VALUE=1"])
        self.assertIsNone(controller.live_z_dialog)

    def test_z_offset_entry_opens_preparation_without_moving_or_live_change(self):
        controller = base_controller()
        controller.z_calibration = FEATHER.ZCalibrationSession()
        controller.toolhead = StatusObject({
            "homed_axes": "", "position": (20.0, 30.0, 0.0, 0.0)})
        pages = []
        controller._show_page = pages.append
        controller._current_material = lambda: "ABS-PC"

        controller._handle_calibration_action("cal.z")

        self.assertEqual(controller.gcode.commands, [])
        self.assertEqual(controller.calibration_material, "ABS-PC")
        self.assertEqual(pages, [FEATHER.Page.CALIBRATION_CONFIRM])
        self.assertFalse(controller.z_calibration.active)

    def test_z_offset_bed_point_lifts_to_five_before_xy_move(self):
        controller = base_controller()
        controller.z_calibration = FEATHER.ZCalibrationSession()
        controller.z_calibration.begin(0.2, None, "", -0.25, False)
        controller.z_calibration.prepared = True
        moves = []
        controller._run_blocking_gcode = (
            lambda command, message: moves.append((command, message)))
        pages = []
        controller._show_page = pages.append

        controller._handle_calibration_action("z.zone.rear_right")

        self.assertEqual(moves, [])
        self.assertEqual(pages, [FEATHER.Page.Z_OFFSET_PAPER_BRIEFING])

        controller._handle_calibration_action("z.paper_briefing.continue")

        self.assertEqual(moves, [(
            "MOVE_SAFE Z=5.0 ABSOLUTE=1 F=600\n"
            "MOVE_SAFE X=94.0 Y=94.0 ABSOLUTE=1 F=6000",
            "POSITIONING HEAD...")])
        self.assertEqual(pages, [FEATHER.Page.Z_OFFSET_PAPER_BRIEFING,
                                 FEATHER.Page.Z_OFFSET_PAPER])

    def test_z_offset_briefing_precedes_zones_and_paper_briefing_follows_each_zone(self):
        controller = base_controller()
        controller.z_calibration = FEATHER.ZCalibrationSession()
        controller.z_calibration.begin(0.2, None, "", -0.25, False)
        pages = []
        moves = []
        controller._show_page = pages.append
        controller._move_z_offset_head = (
            lambda x, y: moves.append((x, y)))

        controller._handle_calibration_action("z.zone.front_left")
        self.assertEqual(pages, [FEATHER.Page.Z_OFFSET_PAPER_BRIEFING])
        self.assertEqual(moves, [])

        controller._handle_calibration_action("z.paper_briefing.continue")
        self.assertEqual(moves, [(-94.0, -94.0)])
        self.assertEqual(pages[-1], FEATHER.Page.Z_OFFSET_PAPER)

        controller._handle_calibration_action("z.zone.center")
        self.assertEqual(moves, [(-94.0, -94.0)])
        self.assertEqual(pages[-1], FEATHER.Page.Z_OFFSET_PAPER_BRIEFING)

        controller._handle_calibration_action("z.paper_briefing.continue")
        self.assertEqual(moves[-1], (0.0, 0.0))
        self.assertEqual(pages.count(FEATHER.Page.Z_OFFSET_PAPER_BRIEFING), 2)

    def test_z_offset_reset_moves_to_zero_candidate_position(self):
        controller = base_controller()
        controller.z_calibration = FEATHER.ZCalibrationSession()
        controller.z_calibration.begin(0.2, None, "", -0.25, False)
        controller.z_calibration.choose_zone("front_left")
        controller.z_calibration.set_trigger(-0.5)
        rendered = []
        controller._render_z_paper = lambda: rendered.append(True)

        controller._handle_calibration_action("z.reset")

        self.assertEqual(controller.gcode.commands, [
            "MOVE_SAFE Z=-0.250000 ABSOLUTE=1 F=300"])
        self.assertEqual(controller.z_calibration.candidate, 0.0)
        self.assertEqual(rendered, [True])

    def test_probe_uses_two_samples_records_trigger_and_retracts_half_mm(self):
        controller = base_controller()
        controller.z_calibration = FEATHER.ZCalibrationSession()
        controller.z_calibration.begin(0.2, None, "", -0.25, False)
        controller.z_calibration.choose_zone("front_right")
        controller.probe = StatusObject({"last_z_result": -0.625})
        controller._render_z_paper = lambda: None
        controller._check_z_pressure = lambda eventtime: False
        probing = []
        controller._run_blocking_gcode = (
            lambda command, message: probing.append((command, message)))

        controller._probe_z_zone()

        self.assertEqual(probing, [("PROBE SAMPLES=2", "PROBING...")])
        self.assertEqual(controller.gcode.commands, [
            "MOVE_SAFE Z=-0.125000 ABSOLUTE=1 F=300"])
        self.assertAlmostEqual(controller.z_calibration.trigger_z, -0.625)
        self.assertAlmostEqual(controller.z_calibration.local_z, 0.5)
        self.assertAlmostEqual(controller.z_calibration.candidate, 0.25)

    def test_manual_paper_start_moves_to_one_point_five_and_enables_controls(self):
        controller = base_controller()
        controller.z_calibration = FEATHER.ZCalibrationSession()
        controller.z_calibration.begin(0.2, None, "", -0.25, False)
        controller.z_calibration.choose_zone("front_right")
        controller._render_z_paper = lambda: None
        moves = []
        controller._run_blocking_gcode = (
            lambda command, message: moves.append((command, message)))

        controller._handle_calibration_action("z.move_1_5")

        self.assertEqual(moves, [(
            "MOVE_SAFE Z=1.500000 ABSOLUTE=1 F=300",
            "MOVING TO 1.5 MM...")])
        self.assertEqual(controller.z_calibration.start_mode, "manual")
        self.assertTrue(controller.z_calibration.ready_for_paper_test)
        self.assertAlmostEqual(controller.z_calibration.reference_z, 1.5)
        self.assertAlmostEqual(controller.z_calibration.paper_contact_z, 1.5)
        self.assertAlmostEqual(controller.z_calibration.candidate, 1.25)

    def test_paper_step_uses_no_loader_or_busy_notice(self):
        controller = base_controller()
        notices = []
        controller.renderer = type("Renderer", (), {
            "busy_notice": lambda self, label: notices.append(label),
            "clear_busy_notice": lambda self: notices.append("clear"),
        })()
        controller.z_calibration = FEATHER.ZCalibrationSession()
        controller.z_calibration.begin(0.2, None, "", -0.25, False)
        controller.z_calibration.choose_zone("center")
        controller.z_calibration.set_trigger(-0.5)
        controller._render_z_paper = lambda: None

        controller._move_z_paper(-0.005)

        self.assertEqual(notices, [])
        self.assertEqual(controller.gcode.commands, [
            "MOVE_SAFE Z=-0.005000 ABSOLUTE=1 F=300"])

    def test_screw_output_is_collected_only_during_workflow(self):
        controller = base_controller()
        controller.calibration_results = []
        controller.calibration_kind = "screws"
        controller.page = FEATHER.Page.CALIBRATION_PROGRESS
        controller._handle_gcode_output(
            "rear : x=1, y=2, z=0.1 : adjust CW 00:05")
        self.assertEqual(controller.calibration_results[0]["turns"], "00:05")
        controller.page = FEATHER.Page.IDLE_HOME
        controller._handle_gcode_output(
            "front : x=1, y=2, z=0.1 : adjust CCW 00:10")
        self.assertEqual(len(controller.calibration_results), 1)


class NetworkWorkflowTest(unittest.TestCase):
    def test_background_status_refresh_updates_dashboard_without_navigation(self):
        controller = base_controller()
        controller.network_process = FinishedProcess(
            "MODE=ETHERNET\nSSID=\nSIGNAL=\nIP=192.168.2.124\n")
        controller.network_operation = "status-background"
        controller.network_deadline = 200
        controller.network_credentials = None
        controller.network_status = {"mode": "OFFLINE", "ssid": "",
                                     "signal": "", "ip": ""}
        controller.page = FEATHER.Page.IDLE_HOME
        updates = []
        controller._update_dashboard = updates.append
        controller._show_page = lambda page: self.fail(
            "background status must not navigate")
        controller._poll_network_process(101)
        self.assertEqual(controller.network_status["mode"], "ETHERNET")
        self.assertEqual(controller.network_status["ip"], "192.168.2.124")
        self.assertEqual(updates, [101])

    def test_network_helper_uses_an_isolated_process_group(self):
        controller = base_controller()
        controller.network_process = None
        process = FinishedProcess("")
        with mock.patch("subprocess.Popen", return_value=process) as popen:
            controller._show_page = lambda page: None
            controller._start_network_process(
                "status", ["znetwork.sh", "status"],
                FEATHER.Page.NETWORK_HOME)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_stopping_network_helper_signals_its_whole_process_group(self):
        controller = base_controller()
        controller.network_stopping = []
        process = FinishedProcess("")
        process.pid = 123
        with mock.patch("os.getpgid", return_value=456), \
                mock.patch("os.killpg") as killpg:
            controller._retire_network_process(process)
        killpg.assert_called_once_with(456, FEATHER.signal.SIGTERM)
        self.assertEqual(controller.network_stopping[0][2], 456)

    def test_scan_deduplicates_and_filters_unsupported_networks(self):
        output = "\n".join([
            "NETWORK\tShop\t-70\t[WPA2-PSK-CCMP][ESS]",
            "NETWORK\tShop\t-45\t[WPA2-PSK-CCMP][ESS]",
            "NETWORK\tOpen\t-20\t[ESS]",
            "NETWORK\tLab\t-55\t[WPA-PSK-TKIP][ESS]",
            "NETWORK\tBroken\tn/a\t[WPA2-PSK-CCMP][ESS]",
        ])
        controller = base_controller()
        controller.network_process = FinishedProcess(output)
        controller.network_operation = "scan"
        controller.network_return_page = FEATHER.Page.NETWORK_HOME
        pages = []
        controller._show_page = pages.append
        controller._poll_network_process(100)
        self.assertEqual([(item["ssid"], item["signal"])
                          for item in controller.networks],
                         [("Shop", -45), ("Lab", -55)])
        self.assertEqual(pages, [FEATHER.Page.WIFI_SCAN])

    def test_network_error_uses_helper_message(self):
        controller = base_controller()
        controller.network_process = FinishedProcess("ERROR=Wrong password\n", 1)
        controller.network_operation = "wifi"
        controller.network_return_page = FEATHER.Page.WIFI_SCAN
        messages = []
        controller._show_message = lambda message, page: messages.append((message, page))
        controller._poll_network_process(100)
        self.assertEqual(messages,
                         [("Wrong password", FEATHER.Page.WIFI_SCAN)])

    def test_cancel_terminates_process_and_returns_message(self):
        controller = base_controller()
        process = FinishedProcess("")
        controller.network_process = process
        controller.network_operation = "scan"
        controller.network_return_page = FEATHER.Page.NETWORK_HOME
        messages = []
        controller._show_message = lambda message, page: messages.append((message, page))
        controller._cancel_network_process("Cancelled")
        self.assertTrue(process.terminated)
        self.assertIsNone(controller.network_process)
        self.assertEqual(messages, [("Cancelled", FEATHER.Page.NETWORK_HOME)])

    def test_starting_print_stops_idle_only_network_operation(self):
        controller = base_controller()
        controller.network_stopping = []
        controller.network_credentials = None
        process = FinishedProcess("")
        controller.network_process = process
        controller.network_operation = "scan"
        controller.network_deadline = 115

        controller._change_print_state(FEATHER.PrintState.PRINTING, "printing")

        self.assertTrue(process.terminated)
        self.assertIsNone(controller.network_process)
        self.assertIsNone(controller.network_operation)

    def test_wifi_credentials_use_private_file_and_not_process_arguments(self):
        controller = base_controller()
        controller.selected_network = {"ssid": "Workshop"}
        controller.password = "secret123"
        started = []
        controller._start_network_process = (
            lambda operation, args, page: started.append((operation, args, page)))
        controller._connect_wifi()
        operation, args, page = started[0]
        credentials = args[-1]
        try:
            self.assertEqual(operation, "wifi")
            self.assertEqual(page, FEATHER.Page.WIFI_SCAN)
            self.assertNotIn("secret123", " ".join(args))
            self.assertEqual(os.stat(credentials).st_mode & 0o777, 0o600)
            self.assertEqual(pathlib.Path(credentials).read_text(encoding="utf-8"),
                             "Workshop\nsecret123\n")
            self.assertEqual(controller.password, "")
        finally:
            if os.path.exists(credentials):
                os.unlink(credentials)

    def test_wifi_credentials_are_removed_when_operation_is_cancelled(self):
        controller = base_controller()
        controller.network_stopping = []
        controller.network_credentials = None
        controller.selected_network = {"ssid": "Workshop"}
        controller.password = "secret123"
        controller._start_network_process = lambda operation, args, page: None
        controller._connect_wifi()
        credentials = controller.network_credentials
        self.assertTrue(os.path.exists(credentials))

        controller.network_process = None
        controller.network_return_page = FEATHER.Page.WIFI_SCAN
        controller._show_message = lambda message, page: None
        controller._cancel_network_process("Cancelled")

        self.assertFalse(os.path.exists(credentials))
        self.assertIsNone(controller.network_credentials)


class TouchEventBridgeTest(unittest.TestCase):
    def test_stale_action_does_not_delay_next_valid_tap(self):
        controller = base_controller()
        controller.last_action_time = -1.0
        controller.pending_action = None
        controller.reactor.now = 100.0
        pages = []
        controller._show_page = pages.append

        controller._dispatch_action("print.cancel.confirm")
        controller._dispatch_action("nav.menu")

        self.assertEqual(pages, [FEATHER.Page.MAIN_MENU])

    def test_feedback_from_replaced_page_is_not_restored_or_dispatched(self):
        controller = base_controller()
        callbacks = []
        controller.reactor.register_callback = (
            lambda callback, waketime=None: callbacks.append(callback))
        events = []

        class Renderer:
            generation = 3

            def flash_button(self, action):
                events.append(("down", action))
                return True

            def restore_button(self, action):
                events.append(("up", action))

        controller.renderer = Renderer()
        controller.touch_feedback_pending = False
        controller._dispatch_action = lambda action: events.append(("action", action))
        controller._handle_touch_action("nav.control")
        controller.page = FEATHER.Page.CONTROL_HOME
        controller.renderer.generation = 4

        callbacks[0](controller.reactor.monotonic())

        self.assertEqual(events, [("down", "nav.control")])
        self.assertFalse(controller.touch_feedback_pending)
    def test_busy_klipper_rejects_normal_tap_but_keeps_cancel_interruptible(self):
        controller = base_controller("printing")
        notices = []
        controller.renderer = type("Renderer", (), {
            "busy_notice": lambda self, label: notices.append(label),
            "flash_button": lambda self, action: False,
        })()
        controller.command_depth = 1
        actions = []
        controller._dispatch_action = actions.append
        controller._handle_touch_action("print.pause")
        self.assertEqual(actions, [])
        self.assertEqual(notices, ["PLEASE WAIT"])
        controller._handle_touch_action("print.cancel")
        self.assertEqual(actions, ["print.cancel"])
        controller.page = FEATHER.Page.CANCEL_CONFIRM
        controller._handle_touch_action("print.cancel.back")
        self.assertEqual(actions, ["print.cancel", "print.cancel.back"])
        controller._handle_touch_action("nav.back")
        self.assertEqual(actions, [
            "print.cancel", "print.cancel.back", "nav.back"])

    def test_gcode_busy_badge_is_cleared_on_success_and_error(self):
        controller = base_controller()
        events = []
        controller.renderer = type("Renderer", (), {
            "busy_notice": lambda self, label: events.append(("busy", label)),
            "clear_busy_notice": lambda self: events.append(("clear", None)),
        })()
        controller.gcode.run_script = lambda command: events.append(
            ("run", command, controller.command_depth))
        controller._run_script("G28")
        self.assertEqual(events, [("busy", "KLIPPER BUSY"),
                                  ("run", "G28", 1), ("clear", None)])
        self.assertEqual(controller.command_depth, 0)

        events[:] = []
        def fail(command):
            raise RuntimeError("failed")
        controller.gcode.run_script = fail
        with self.assertRaisesRegex(RuntimeError, "failed"):
            controller._run_script("M84")
        self.assertEqual(events, [("busy", "KLIPPER BUSY"), ("clear", None)])
        self.assertEqual(controller.command_depth, 0)

    def test_fragmented_cpp_tap_events_are_reassembled(self):
        controller = base_controller()
        controller.renderer = type("Renderer", (), {"event_fd": 7})()
        controller.event_partial = ""
        controller.last_touch_time = 0
        controller.dimmed = False
        controller._wake_if_dimmed = lambda: False
        actions = []
        controller._dispatch_action = actions.append
        with mock.patch("os.read", side_effect=[
                b"tap nav.fi", b"les\ntap nav.control\nignored\n"]):
            controller._process_touch_events(1)
            self.assertEqual(actions, [])
            controller._process_touch_events(2)
        self.assertEqual(actions, ["nav.files", "nav.control"])
        self.assertEqual(controller.event_partial, "")

    def test_partial_touch_event_memory_is_bounded(self):
        controller = base_controller()
        controller.renderer = type("Renderer", (), {"event_fd": 7})()
        controller.event_partial = ""
        with self.assertLogs(level="WARNING") as logs, mock.patch(
                "os.read", return_value=b"x" * (FEATHER.MAX_TOUCH_EVENT + 1)):
            controller._process_touch_events(1)
        self.assertEqual(controller.event_partial, "")
        self.assertIn("oversized partial touch event", "\n".join(logs.output))

    def test_first_cpp_tap_after_dim_wakes_and_dispatches(self):
        controller = base_controller()
        controller.renderer = type("Renderer", (), {"event_fd": 7})()
        controller.event_partial = ""
        controller.last_touch_time = 0
        controller.dimmed = True
        controller.params = type("Params", (), {"variables": {"backlight": 65}})()
        backlight = []
        controller._set_backlight = backlight.append
        actions = []
        controller._dispatch_action = actions.append
        with mock.patch("os.read", return_value=b"tap nav.files\n"):
            controller._process_touch_events(1)
        self.assertEqual(backlight, [65])
        self.assertEqual(actions, ["nav.files"])

    def test_touch_feedback_precedes_deferred_action(self):
        controller = base_controller()
        callbacks = []
        controller.reactor.register_callback = (
            lambda callback, waketime=None: callbacks.append(callback))
        rendered = []
        controller.renderer = type("Renderer", (), {
            "flash_button": lambda self, action: rendered.append(("down", action)) or True,
            "restore_button": lambda self, action: rendered.append(("up", action)) or True,
        })()
        controller.touch_feedback_pending = False
        actions = []
        def dispatch(action):
            actions.append(action)
        controller._dispatch_action = dispatch
        controller._handle_touch_action("nav.control")
        controller._handle_touch_action("nav.files")
        self.assertEqual(rendered, [("down", "nav.control")])
        self.assertEqual(actions, [])
        callbacks[0](controller.reactor.monotonic())
        self.assertEqual(rendered, [("down", "nav.control"),
                                    ("up", "nav.control")])
        self.assertEqual(actions, ["nav.control"])
        self.assertEqual(len(callbacks), 1)
        self.assertFalse(controller.touch_feedback_pending)
        # A valid tap on the newly rendered page is accepted immediately;
        # stale taps are rejected by the renderer generation instead of a
        # global 350 ms dead period.
        controller._handle_touch_action("nav.files")
        self.assertEqual(len(callbacks), 2)
        self.assertEqual(rendered[-1], ("down", "nav.files"))


class RecoveryRobustnessTest(unittest.TestCase):
    def test_corrupt_recovery_file_is_not_advertised(self):
        with tempfile.NamedTemporaryFile(mode="w") as stream:
            stream.write("not-json")
            stream.flush()
            resurrector = RESURRECTION.Resurrector.__new__(RESURRECTION.Resurrector)
            resurrector.state = RESURRECTION.ResurrectorState.RESURRECTION
            resurrector.file_path = stream.name
            with self.assertLogs(level="ERROR"):
                status = resurrector.get_status(0)
        self.assertFalse(status["available"])
        self.assertEqual(status["state"], "error")
        self.assertNotIn("file_path", status)

    def test_later_keeps_recovery_data_and_opens_home(self):
        controller = base_controller()
        pages = []
        controller._show_page = pages.append
        controller._handle_recovery_action("recovery.later")
        self.assertEqual(pages, [FEATHER.Page.IDLE_HOME])
        self.assertEqual(controller.gcode.commands, [])


if __name__ == "__main__":
    unittest.main()
