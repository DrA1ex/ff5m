"""Execute parked captures and user pause handoff with Klipper G-code state."""

## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import importlib.util
import pathlib
import re
import shlex
import types
import unittest
from unittest import mock

from tests.gcode_macro_harness import MacroActionError, MacroExecution, render_macro

ROOT = pathlib.Path(__file__).parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GCODE = load_module("pause_test_gcode", ".py/klipper/patches/gcode.py")
MOVE = load_module("pause_test_move", ".py/klipper/patches/extras/gcode_move.py")
SENSOR = load_module("pause_test_sensor", ".py/klipper/patches/extras/temperature_sensor.py")


class ParkedPrint:
    def __init__(self, held=False, absolute=True, relative_axes=False, hot=True, firmware=False):
        self.paused = False
        self.sd_paused = False
        self.moves = []
        self.sd_starts = []
        self.firmware_retracted = False
        self.gcode = mock.Mock(Coord=GCODE.Coord)
        self.toolhead = mock.Mock()
        self.toolhead.get_position.return_value = [30., 40., 12., 80.]
        self.toolhead.move.side_effect = lambda p, v: self.moves.append((tuple(p), v))
        printer = mock.Mock()
        printer.lookup_object.side_effect = lambda name: {"gcode": self.gcode, "toolhead": self.toolhead}[name]
        self.move = MOVE.GCodeMove(types.SimpleNamespace(get_printer=lambda: printer))
        self.move._handle_ready()
        self.status = {
            "mod_params": {"variables": {
                "timelapse": True, "timelapse_park": True, "pause_z_min": 30,
                "safe_z": 2, "park_dz": 2, "filament_switch_sensor": False,
                "timelapse_final_frame": True, "midi_end": ""}},
            "gcode_macro MOVE_SAFE": {"x_max": 210, "y_max": 210,
                                      "x_min": 0, "y_min": 0, "z_min": 0, "z_max_margin": 5},
            "gcode_macro TIMELAPSE_PRINT": {"enable": True},
            "gcode_macro HYPERLAPSE": {"run": False},
            "gcode_macro _TIMELAPSE_START_GUARD": {"waiting": False, "sd_held": held},
            "gcode_macro _CLIENT_VARIABLE": {"user_cancel_macro": "", "idle_timeout": 600},
            "toolhead": {"extruder": "extruder", "homed_axes": "xyz", "axis_maximum": {"z": 230}},
            "extruder": {"can_extrude": hot, "target": 215},
            "heater_bed": {"target": 60},
            "print_stats": {"state": "printing"},
            "virtual_sdcard": {"is_active": not held, "file_path": "job.gcode"},
            "configfile": {"settings": {"pause_resume": {"recover_velocity": 50},
                                        "idle_timeout": {"timeout": 300},
                                        "printer": {"kinematics": "corexy"}}},
        }
        macros = [(ROOT / "macros/timelapse.cfg", name, section) for name, section in (
            ("TIMELAPSE_TAKE_FRAME", "gcode_macro"), ("_TIMELAPSE_NEW_FRAME", "gcode_macro"),
            ("_WAIT_TIMELAPSE_TAKE_FRAME", "delayed_gcode"),
            ("_TIMELAPSE_FRAME_RESET", "gcode_macro"),
            ("_TIMELAPSE_START_RELEASE_SD", "gcode_macro"),
            ("_TIMELAPSE_START_RESET", "gcode_macro"),
            ("_TIMELAPSE_FINAL_PARK", "gcode_macro"))]
        macros += [(ROOT / "macros/base.cfg", "MOVE_SAFE", "gcode_macro"),
                   (ROOT / "macros/headless.cfg", "END_PRINT", "gcode_macro")]
        macros += [(ROOT / "macros/client.cfg", name, "gcode_macro") for name in (
            "PAUSE", "RESUME", "_RESUME_REJECTED", "CANCEL_PRINT", "_CLIENT_PAUSE", "_CLIENT_EXTRUDE", "_CLIENT_RETRACT",
            "_TOOLHEAD_PARK_PAUSE_CANCEL")]
        self.runtime = MacroExecution(macros, self.status, self.execute, self.refresh)
        frame = self.status["gcode_macro TIMELAPSE_TAKE_FRAME"]
        frame.update(enable=True)
        frame["extruder"]["fw_retract"] = firmware
        self.execute("M220 S75")
        self.execute("M221 S120")
        self.execute("SET_GCODE_OFFSET X=2 Y=-3 Z=1")
        self.execute("G92 E40")
        self.execute("G1 F1800")
        self.execute("M82" if absolute else "M83")
        if relative_axes:
            self.execute("G91")
        self.original = self.move.get_status()
        self.refresh()

    def refresh(self):
        self.status["gcode_move"] = self.move.get_status()
        self.status["pause_resume"] = {"is_paused": self.paused}

    def execute(self, command):
        name, _, raw = command.partition(" ")
        params = {}
        for arg in shlex.split(raw):
            if "=" in arg:
                key, value = arg.split("=", 1)
            else:
                key, value = arg[:1], arg[1:]
            params[key] = value
        gcmd = GCODE.GCodeCommand(self.gcode, name, command, params, False)
        move_name = "G1" if name == "G0" else name
        handler = getattr(self.move, "cmd_" + move_name, None)
        if handler is not None:
            handler(gcmd)
        elif name == "PAUSE_BASE":
            if not self.paused:
                self.sd_paused = self.status["virtual_sdcard"]["is_active"]
                self.status["virtual_sdcard"]["is_active"] = False
                self.execute("SAVE_GCODE_STATE NAME=PAUSE_STATE")
                self.paused = True
                self.status["print_stats"]["state"] = "paused"
        elif name == "RESUME_BASE":
            self.execute("RESTORE_GCODE_STATE NAME=PAUSE_STATE MOVE=1 MOVE_SPEED=" + params.get("VELOCITY", "50"))
            if self.sd_paused:
                self.execute("M24.1")
            self.paused = self.sd_paused = False
        elif name == "CANCEL_PRINT_BASE":
            self.paused = self.sd_paused = False
            self.status["virtual_sdcard"]["is_active"] = False
            self.status["print_stats"]["state"] = "cancelled"
        elif name == "M24.1":
            self.sd_starts.append((tuple(self.move.last_position), self.move.absolute_extrude))
            self.status["virtual_sdcard"]["is_active"] = True
            self.status["print_stats"]["state"] = "printing"
        elif name == "SDCARD_CANCEL_FILE":
            self.status["virtual_sdcard"].update(is_active=False, file_path=None)
            self.status["print_stats"]["state"] = "cancelled"
        elif name in ("G10", "G11"):
            self.firmware_retracted = name == "G10"
        elif name not in {"M400", "RESPOND", "_CONTEXT_BEGIN", "_CONTEXT_END", "_CONTEXT_RESET",
                          "SET_IDLE_TIMEOUT", "_WAIT_TEMPERATURE", "TURN_OFF_HEATERS", "M106",
                          "SET_PAUSE_NEXT_LAYER", "SET_PAUSE_AT_LAYER",
                          "TONE", "ALARM", "_COMMON_END_PRINT", "_MAYBE_AUTO_REBOOT"}:
            raise AssertionError("Unhandled terminal command: " + command)

    def capture(self):
        self.runtime.run("TIMELAPSE_TAKE_FRAME")

    def finish(self, timeout=False):
        frame = self.status["gcode_macro TIMELAPSE_TAKE_FRAME"]
        if timeout:
            frame["wait_loops"] = 50
        else:
            frame["takingframe"] = False
        self.runtime.fire("_WAIT_TIMELAPSE_TAKE_FRAME")


class CollisionPauseIntegrationTest(unittest.TestCase):
    def pause_source(self, screen):
        sources = []
        seen = set()

        def read(path):
            path = path.resolve()
            if path in seen:
                return
            seen.add(path)
            text = path.read_text()
            if "[gcode_macro PAUSE]" in text:
                sources.append(path)
            for include in re.findall(r"^\[include (.+)\]$", text, re.MULTILINE):
                for child in sorted(path.parent.glob(include)):
                    read(child)

        read(ROOT / "config" / (screen + ".cfg"))
        self.assertEqual(len(sources), 1, sources)
        return sources[0]

    def job(self, screen="feather"):
        job = ParkedPrint(absolute=True, relative_axes=True)
        job.status.update({
            "idle_timeout": {"state": "Printing"},
            "operation_context": {
                "context_types": ("print",), "current_state": "PRINTING",
                "cancel_pending": False},
        })
        job.status["mod_params"]["variables"].update(
            weight_check=True, weight_check_max=1200, weight_check_mode="PAUSE")
        source = self.pause_source(screen)
        job.runtime.macros.update({
            name: (source, "gcode_macro") for name in ("PAUSE", "RESUME")})
        job.shell_commands = []

        def terminal(command):
            if not command.startswith("RUN_SHELL_COMMAND "):
                return job.execute(command)
            job.shell_commands.append(command)
            # Model Stock firmware accepting its existing zsend M25/M24 path.
            self.assertIn('CMD=zsend PARAMS="', command)
            if command.endswith('PARAMS="M25"'):
                job.status["virtual_sdcard"]["is_active"] = False
                job.status["print_stats"]["state"] = "paused"
            elif command.endswith('PARAMS="M24"'):
                job.status["virtual_sdcard"]["is_active"] = True
                job.status["print_stats"]["state"] = "printing"
            else:
                self.fail(command)

        job.runtime.terminal = terminal
        return job

    def collision(self, job):
        job.refresh()
        return render_macro(
            ROOT / "macros/base.cfg", "weightValue", section="temperature_sensor",
            gcode_option="exceed_gcode", variables={"value": 1200},
            printer=job.status)

    def test_collision_pause_and_resume_use_each_screen_configuration(self):
        for screen in ("stock", "feather", "headless", "guppy"):
            with self.subTest(screen=screen):
                job = self.job(screen)
                first = self.collision(job)
                self.assertIn("PAUSE", first.commands)
                self.assertNotIn("M112", first.commands)
                for command in first.commands:
                    job.runtime.run(command)
                self.assertFalse(job.status["virtual_sdcard"]["is_active"])
                self.assertEqual(job.status["print_stats"]["state"], "paused")
                # Stock pauses virtual SD without marking pause_resume paused.
                self.assertEqual(job.paused, screen != "stock")
                repeated = self.collision(job)
                self.assertNotIn("PAUSE", repeated.commands)
                self.assertNotIn("M112", repeated.commands)
                job.runtime.run("RESUME")
                self.assertTrue(job.status["virtual_sdcard"]["is_active"])
                self.assertFalse(job.paused)
                if screen == "stock":
                    self.assertEqual(job.shell_commands, [
                        'RUN_SHELL_COMMAND CMD=zsend PARAMS="M25"',
                        'RUN_SHELL_COMMAND CMD=zsend PARAMS="M24"'])
                else:
                    after = job.move.get_status()
                    for field in ("gcode_position", "absolute_coordinates", "absolute_extrude"):
                        self.assertEqual(after[field], job.original[field])

    def test_parked_timelapse_collision_is_immediate_emergency(self):
        for screen in ("stock", "feather", "headless", "guppy"):
            with self.subTest(screen=screen):
                job = self.job(screen)
                job.capture()
                self.assertTrue(job.paused)
                self.assertFalse(job.status["virtual_sdcard"]["is_active"])
                rendered = self.collision(job)
                self.assertIn("M112", rendered.commands)
                self.assertNotIn("PAUSE", rendered.commands)
                sensor = SENSOR.PrinterSensorGeneric.__new__(SENSOR.PrinterSensorGeneric)
                sensor.name = "weightValue"
                sensor.printer = mock.Mock()
                sensor._template = lambda value: rendered.text
                sensor._handle_exceed(1200)
                sensor.printer.invoke_shutdown.assert_called_once()

    def test_timelapse_between_frames_preserves_normal_collision_pause(self):
        for mode in ("LAYER", "TIME", "PERCENT"):
            for screen in ("stock", "feather", "headless", "guppy"):
                with self.subTest(mode=mode, screen=screen):
                    job = self.job(screen)
                    job.status["mod_params"]["variables"]["timelapse_mode"] = mode
                    job.capture()
                    job.finish()
                    self.assertTrue(job.status["virtual_sdcard"]["is_active"])
                    rendered = self.collision(job)
                    self.assertIn("PAUSE", rendered.commands)
                    self.assertNotIn("M112", rendered.commands)
                    for command in rendered.commands:
                        job.runtime.run(command)
                    self.assertFalse(job.status["virtual_sdcard"]["is_active"])
                    self.assertTrue(job.status["mod_params"]["variables"]["timelapse"])
                    self.assertTrue(job.status["gcode_macro TIMELAPSE_TAKE_FRAME"]["enable"])

    def test_timelapse_without_parking_does_not_block_collision_pause(self):
        for screen in ("stock", "feather", "headless", "guppy"):
            with self.subTest(screen=screen):
                job = self.job(screen)
                job.status["mod_params"]["variables"]["timelapse_park"] = False
                job.capture()
                self.assertTrue(job.status["virtual_sdcard"]["is_active"])
                rendered = self.collision(job)
                self.assertIn("PAUSE", rendered.commands)
                self.assertNotIn("M112", rendered.commands)
                for command in rendered.commands:
                    job.runtime.run(command)
                job.runtime.run("_WAIT_TIMELAPSE_TAKE_FRAME")
                self.assertFalse(job.status["virtual_sdcard"]["is_active"])


class TimelapsePauseStateTest(unittest.TestCase):
    def test_end_print_applies_configured_lift_with_and_without_final_photo(self):
        for final_photo in (False, True):
            for relative_axes in (False, True):
                for park_dz in (1, 25, 50, 500):
                    with self.subTest(final_photo=final_photo, relative_axes=relative_axes, park_dz=park_dz):
                        job = ParkedPrint(relative_axes=relative_axes)
                        job.status["mod_params"]["variables"].update(
                            timelapse_final_frame=final_photo, park_dz=park_dz)
                        before = job.move.get_status()

                        job.runtime.run("END_PRINT")

                        after = job.move.get_status()
                        self.assertAlmostEqual(after["gcode_position"].z,
                                               min(before["gcode_position"].z + park_dz, 225))
                        self.assertEqual(after["absolute_coordinates"], before["absolute_coordinates"])
                        self.assertEqual(after["absolute_extrude"], before["absolute_extrude"])

    def test_cancel_reuses_pause_park_unless_cancel_xy_is_configured(self):
        for paused in (False, True):
            for cancel_x in (None, 90):
                for relative_axes in (False, True):
                    with self.subTest(paused=paused, cancel_x=cancel_x, relative_axes=relative_axes):
                        job = ParkedPrint(relative_axes=relative_axes)
                        job.status["gcode_macro _CLIENT_VARIABLE"].update(
                            park_at_cancel=True, park_at_cancel_x=cancel_x)
                        job.status["mod_params"]["variables"]["park_dz"] = 50
                        if paused:
                            job.runtime.run("PAUSE")
                        before = job.move.get_status()

                        job.runtime.run("CANCEL_PRINT")

                        after = job.move.get_status()
                        lift = 0 if paused and cancel_x is None else 50
                        self.assertAlmostEqual(after["gcode_position"].z, before["gcode_position"].z + lift)
                        if lift == 0:
                            self.assertEqual(after["gcode_position"][:3], before["gcode_position"][:3])
                        elif cancel_x is not None:
                            self.assertAlmostEqual(after["gcode_position"].x, cancel_x)
                        self.assertEqual(after["absolute_coordinates"], before["absolute_coordinates"])
                        self.assertFalse(job.paused)
                        self.assertFalse(job.status["virtual_sdcard"]["is_active"])

    def assert_restored(self, job):
        status = job.move.get_status()
        for key in ("absolute_extrude", "absolute_coordinates", "homing_origin", "speed_factor", "extrude_factor", "speed"):
            self.assertEqual(status[key], job.original[key], key)
        for actual, original in zip(status["gcode_position"], job.original["gcode_position"]):
            self.assertAlmostEqual(actual, original)

    def test_user_pause_handoff_and_resume_restores_full_state(self):
        for held in (False, True):
            for absolute in (False, True):
                for relative_axes in (False, True):
                    for timeout in (False, True):
                        with self.subTest(held=held, absolute=absolute, relative_axes=relative_axes, timeout=timeout):
                            job = ParkedPrint(held, absolute, relative_axes)
                            job.capture()
                            job.runtime.run("PAUSE")
                            job.runtime.run("RESUME")
                            self.assertTrue(job.paused)
                            self.assertFalse(job.status["virtual_sdcard"]["is_active"])
                            job.finish(timeout)
                            self.assertTrue(job.paused)
                            self.assertFalse(job.status["virtual_sdcard"]["is_active"])
                            job.runtime.run("PAUSE")
                            job.runtime.run("RESUME")
                            self.assertFalse(job.paused)
                            self.assertTrue(job.status["virtual_sdcard"]["is_active"])
                            self.assertEqual(len(job.sd_starts), 1)
                            self.assert_restored(job)
                            self.assertFalse(job.status["gcode_macro TIMELAPSE_TAKE_FRAME"]["user_pause_requested"])

    def test_automatic_release_restores_before_starting_sd_and_is_idempotent(self):
        for held in (False, True):
            for firmware in (False, True):
                with self.subTest(held=held, firmware=firmware):
                    job = ParkedPrint(held=held, firmware=firmware)
                    job.capture()
                    job.runtime.run("_TIMELAPSE_START_RELEASE_SD")
                    self.assertFalse(job.status["virtual_sdcard"]["is_active"])
                    job.finish()
                    self.assert_restored(job)
                    self.assertFalse(job.firmware_retracted)
                    self.assertEqual(job.sd_starts[0][0][:3], job.original["position"][:3])
                    self.assertTrue(job.sd_starts[0][1])
                    commands = len(job.runtime.commands)
                    job.runtime.run("_WAIT_TIMELAPSE_TAKE_FRAME")
                    job.runtime.run("_TIMELAPSE_START_RELEASE_SD")
                    self.assertEqual(len(job.sd_starts), 1)
                    self.assertEqual(len(job.runtime.commands), commands)

    def test_cancel_during_capture_does_not_move_on_a_late_completion(self):
        for held in (False, True):
            with self.subTest(held=held):
                job = ParkedPrint(held)
                job.capture()
                job.runtime.run("PAUSE")
                job.runtime.run("CANCEL_PRINT")
                moves = len(job.moves)
                job.finish()
                self.assertEqual(job.status["print_stats"]["state"], "cancelled")
                self.assertFalse(job.status["virtual_sdcard"]["is_active"])
                self.assertEqual(len(job.moves), moves)
                self.assertFalse(job.status["gcode_macro TIMELAPSE_TAKE_FRAME"]["is_paused"])

    def test_cancel_during_initial_timelapse_wait_closes_file_without_moving(self):
        job = ParkedPrint(held=True)
        job.status["gcode_macro _CLIENT_VARIABLE"]["park_at_cancel"] = True
        job.status["gcode_macro _TIMELAPSE_START_GUARD"].update(
            waiting=True, prompt_open=True)
        job.status["print_stats"]["state"] = "paused"
        moves = len(job.moves)

        job.runtime.run("CANCEL_PRINT")

        self.assertEqual(len(job.moves), moves)
        self.assertFalse(job.sd_starts)
        self.assertIsNone(job.status["virtual_sdcard"]["file_path"])
        self.assertEqual(job.status["print_stats"]["state"], "cancelled")
        self.assertFalse(job.status["gcode_macro _TIMELAPSE_START_GUARD"]["waiting"])
        self.assertFalse(job.status["gcode_macro _TIMELAPSE_START_GUARD"]["sd_held"])

    def test_rejected_user_resume_keeps_first_file_held_until_a_successful_retry(self):
        job = ParkedPrint(held=True)
        job.capture()
        job.runtime.run("PAUSE")
        job.finish()
        job.status["mod_params"]["variables"]["filament_switch_sensor"] = True
        job.status["gcode_macro _CLIENT_VARIABLE"]["runout_sensor"] = "sensor"
        job.status["sensor"] = {"enabled": True, "filament_detected": False}
        with self.assertRaisesRegex(MacroActionError, "No filament"):
            job.runtime.run("RESUME")
        job.runtime.run("_TIMELAPSE_START_RELEASE_SD")
        self.assertTrue(job.paused)
        self.assertTrue(job.status["gcode_macro _TIMELAPSE_START_GUARD"]["sd_held"])
        self.assertTrue(job.status["gcode_macro TIMELAPSE_TAKE_FRAME"]["user_pause_requested"])
        self.assertFalse(job.sd_starts)
        job.status["sensor"]["filament_detected"] = True
        job.runtime.run("RESUME")
        self.assertEqual(len(job.sd_starts), 1)
        self.assert_restored(job)

    def test_cancel_after_handoff_stops_sd_and_late_capture_stays_inert(self):
        for held in (False, True):
            with self.subTest(held=held):
                job = ParkedPrint(held=held)
                job.capture()
                job.runtime.run("PAUSE")
                job.finish()
                job.runtime.run("CANCEL_PRINT")
                moves = len(job.moves)
                job.runtime.run("_WAIT_TIMELAPSE_TAKE_FRAME")
                self.assertFalse(job.status["virtual_sdcard"]["is_active"])
                self.assertFalse(job.status["gcode_macro _TIMELAPSE_START_GUARD"]["sd_held"])
                self.assertEqual(job.status["print_stats"]["state"], "cancelled")
                self.assertEqual(len(job.moves), moves)

    def test_cold_capture_does_not_extrude_if_temperature_rises_before_completion(self):
        job = ParkedPrint(held=True, hot=False)
        job.capture()
        extrusion = job.move.last_position[3]
        job.status["extruder"]["can_extrude"] = True
        job.finish()
        self.assertEqual(job.move.last_position[3], extrusion)
        self.assert_restored(job)

    def test_normal_user_pause_keeps_the_same_resume_contract(self):
        job = ParkedPrint(absolute=True, relative_axes=True)
        job.runtime.run("PAUSE X=90 Y=80 Z_MIN=25")
        self.assertTrue(job.paused)
        self.assertFalse(job.status["virtual_sdcard"]["is_active"])
        self.assertEqual(job.status["gcode_macro RESUME"]["last_extruder_temp"], {"restore": True, "temp": 215})
        self.assertAlmostEqual(job.move.get_status()["gcode_position"].x, 90)
        job.runtime.run("RESUME")
        self.assert_restored(job)
        self.assertEqual(len(job.sd_starts), 1)
