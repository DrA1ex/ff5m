## Tests for power-loss recovery parsing and lifecycle.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import importlib.util
import json
import os
import pathlib
import tempfile
import threading
import time
import unittest
from unittest import mock


MODULE_PATH = (pathlib.Path(__file__).parents[1] / ".py" / "klipper" /
               "plugins" / "resurrection.py")
SPEC = importlib.util.spec_from_file_location(
    "resurrection_under_test", MODULE_PATH)
RESURRECTION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RESURRECTION)
STATE = __import__("resurrection_state")


class GCodeRecorder:
    def __init__(self):
        self.commands = []
        self.responses = []

    def run_script_from_command(self, command):
        self.commands.append(command)

    def respond_raw(self, message):
        self.responses.append(message)


class FailingPreparationGCode(GCodeRecorder):
    def run_script_from_command(self, command):
        self.commands.append(command)
        if "_START_PRINT_PREPARE" in command:
            raise RuntimeError("preparation failed")


class Command:
    def __init__(self):
        self.responses = []

    def respond_raw(self, message):
        self.responses.append(message)


class CooperativeReactor:
    def __init__(self):
        self.now = 100.
        self.pauses = 0

    def monotonic(self):
        return self.now

    def pause(self, waketime):
        self.pauses += 1
        self.now = waketime
        time.sleep(.001)


class VirtualSDRecorder:
    def __init__(self):
        self.loaded = []
        self.resumed = False
        self.cancelled = False

    def load_file(self, gcmd, filename):
        self.loaded.append(filename)

    def do_resume(self):
        self.resumed = True

    def do_cancel(self):
        self.cancelled = True


class GCodeStateParserTest(unittest.TestCase):
    def parse(self, gcode, position=None):
        data = gcode if isinstance(gcode, bytes) else gcode.encode()
        with tempfile.NamedTemporaryFile() as stream:
            stream.write(data)
            stream.flush()
            if position is None:
                position = len(data)
            return RESURRECTION.GCodeStateParser(
                stream.name, position, len(data), threading.Event()).parse()

    def test_checkpoint_excludes_the_next_command(self):
        first = b"M106 S64\n"
        data = first + b"M106 S192\n"
        state = self.parse(data, len(first))
        self.assertEqual(state.fans, {0: 64.})

    def test_checkpoint_must_be_at_a_line_boundary(self):
        data = b"M106 S64\nM106 S192\n"
        with self.assertRaisesRegex(
                RESURRECTION.RecoveryParseError, "line boundary"):
            self.parse(data, len(b"M106 S64\nM10"))

    def test_crlf_lowercase_comments_and_line_numbers_are_supported(self):
        data = (
            b"; header\r\n"
            b"n10 m106 p2 s127*42 ; fan\r\n"
            b"g90\r\n"
            b"m83 ; relative extrusion\r\n")
        state = self.parse(data)
        self.assertEqual(state.fans, {2: 127.})
        self.assertTrue(state.absolute_coordinates)
        self.assertFalse(state.absolute_extrude)

    def test_motion_modes_feed_flow_and_logical_extruder_are_reduced(self):
        state = self.parse(
            "G90\n"
            "M82\n"
            "G1 X10 E5 F1200\n"
            "M221 S95\n"
            "G91\n"
            "G1 E2 F900\n"
            "M83\n"
            "G1 E3\n"
            "G92 E7\n")
        self.assertFalse(state.absolute_coordinates)
        self.assertFalse(state.absolute_extrude)
        self.assertEqual(state.logical_e, 7.)
        self.assertEqual(state.feedrate, 900.)
        self.assertEqual(state.extrude_factor, 95.)
        commands = state.before_retraction_commands()
        self.assertLess(commands.index("M221 S95"), commands.index("G92 E7"))
        self.assertLess(commands.index("G92 E7"), commands.index("G1 F900"))
        self.assertEqual(commands[-2:], ["G91", "M83"])

    def test_velocity_commands_merge_by_effective_field(self):
        state = self.parse(
            "SET_VELOCITY_LIMIT VELOCITY=300 ACCEL=4000 "
            "ACCEL_TO_DECEL=2000\n"
            "M204 P1500 T1200\n"
            "SET_VELOCITY_LIMIT SQUARE_CORNER_VELOCITY=7\n"
            "SET_VELOCITY_LIMIT ACCEL=3500\n")
        self.assertEqual(state.velocity_limits, {
            "VELOCITY": 300.,
            "ACCEL": 3500.,
            "ACCEL_TO_DECEL": 2000.,
            "SQUARE_CORNER_VELOCITY": 7.,
        })
        self.assertIn(
            "SET_VELOCITY_LIMIT VELOCITY=300 ACCEL=3500 "
            "ACCEL_TO_DECEL=2000 SQUARE_CORNER_VELOCITY=7",
            state.before_retraction_commands())

    def test_pressure_advance_skew_and_retraction_are_state_machines(self):
        state = self.parse(
            "SET_PRESSURE_ADVANCE ADVANCE=0.04\n"
            "SET_PRESSURE_ADVANCE SMOOTH_TIME=0.03\n"
            "SKEW_PROFILE LOAD=old\n"
            "SET_SKEW XY=100,101,100\n"
            "SET_SKEW CLEAR=1\n"
            "SET_SKEW YZ=80,81,80\n"
            "SET_RETRACTION RETRACT_LENGTH=0.8 RETRACT_SPEED=35\n"
            "G10\n"
            "G11\n"
            "G10\n")
        self.assertEqual(state.pressure_advance[None], {
            "ADVANCE": .04, "SMOOTH_TIME": .03,
        })
        self.assertEqual(state.skew_base, "SET_SKEW CLEAR=1")
        self.assertEqual(state.skew_planes, {"YZ": "80,81,80"})
        self.assertEqual(state.retraction, {
            "RETRACT_LENGTH": .8, "RETRACT_SPEED": 35.,
        })
        self.assertTrue(state.has_retraction_state)
        self.assertTrue(state.retracted)
        commands = state.before_retraction_commands()
        self.assertIn(
            "SET_PRESSURE_ADVANCE ADVANCE=0.04 SMOOTH_TIME=0.03",
            commands)
        self.assertIn("SET_SKEW CLEAR=1", commands)
        self.assertIn("SET_SKEW YZ=80,81,80", commands)
        self.assertFalse(any(command in {"G10", "G11"} for command in commands))

    def test_fans_progress_and_layer_fields_merge_independently(self):
        state = self.parse(
            "M106 S80\n"
            "M106 P2 S127\n"
            "M107 P2\n"
            "M73 P42\n"
            "M73 R18\n"
            "SET_PRINT_STATS_INFO TOTAL_LAYER=100\n"
            "SET_PRINT_STATS_INFO CURRENT_LAYER=43\n")
        self.assertEqual(state.fans, {0: 80., 2: 0.})
        self.assertEqual(state.progress, {"P": 42., "R": 18.})
        self.assertEqual(state.print_stats, {
            "CURRENT_LAYER": 43, "TOTAL_LAYER": 100,
        })
        self.assertEqual(state.final_commands(), [
            "M106 S80",
            "M106 P2 S0",
            "M73 P42 R18",
            "SET_PRINT_STATS_INFO CURRENT_LAYER=43 TOTAL_LAYER=100",
        ])

    def test_invalid_supported_command_is_not_silently_ignored(self):
        with self.assertRaisesRegex(
                RESURRECTION.RecoveryParseError, "Invalid ACCEL"):
            self.parse("SET_VELOCITY_LIMIT ACCEL=fast\n")

    def test_pre_cancelled_parse_stops_without_reading_state(self):
        data = b"M106 S64\n"
        with tempfile.NamedTemporaryFile() as stream:
            stream.write(data)
            stream.flush()
            cancel = threading.Event()
            cancel.set()
            parser = RESURRECTION.GCodeStateParser(
                stream.name, len(data), len(data), cancel)
            with self.assertRaises(RESURRECTION.RecoveryParseCancelled):
                parser.parse()

    def test_oversized_comments_do_not_expand_parser_memory(self):
        data = (b";" + b"x" * (RESURRECTION.MAX_GCODE_LINE_SIZE + 1)
                + b"\nM106 S64\n")
        state = self.parse(data)
        self.assertEqual(state.fans, {0: 64.})

    def test_oversized_command_is_rejected(self):
        data = (b"UNKNOWN " + b"x" * RESURRECTION.MAX_GCODE_LINE_SIZE
                + b"\n")
        with self.assertRaisesRegex(
                RESURRECTION.RecoveryParseError, "exceeds"):
            self.parse(data)


class ResurrectorLifecycleTest(unittest.TestCase):
    def test_plugin_delegates_state_parsing_to_companion_module(self):
        self.assertIs(RESURRECTION.GCodeStateParser, STATE.GCodeStateParser)
        self.assertIs(RESURRECTION.RecoveryGCodeState,
                      STATE.RecoveryGCodeState)

    def test_worker_wait_is_cooperative_and_leaves_no_thread(self):
        resurrector = RESURRECTION.Resurrector.__new__(
            RESURRECTION.Resurrector)
        resurrector.gcode = GCodeRecorder()
        resurrector.reactor = CooperativeReactor()
        resurrector.state = RESURRECTION.ResurrectorState.LOADING
        resurrector._worker = None
        resurrector._worker_cancel = None
        parsed = RESURRECTION.RecoveryGCodeState()

        def delayed_parse(_parser):
            time.sleep(.025)
            return parsed

        with mock.patch.object(
                RESURRECTION.GCodeStateParser, "parse", delayed_parse):
            result = resurrector._load_state({
                "file_path": "/unused",
                "file_position": 0,
                "file_size": 0,
            })

        self.assertIs(result, parsed)
        self.assertGreater(resurrector.reactor.pauses, 0)
        self.assertIsNone(resurrector._worker)
        self.assertIsNone(resurrector._worker_cancel)

    def test_cancel_worker_joins_it(self):
        resurrector = RESURRECTION.Resurrector.__new__(
            RESURRECTION.Resurrector)
        cancel = threading.Event()

        def wait_for_cancel():
            cancel.wait()

        worker = threading.Thread(target=wait_for_cancel)
        worker.start()
        resurrector._worker = worker
        resurrector._worker_cancel = cancel
        resurrector._cancel_worker()
        self.assertFalse(worker.is_alive())
        self.assertIsNone(resurrector._worker)

    def test_dump_is_atomic_and_keeps_checkpoint_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            gcode_path = os.path.join(directory, "part.gcode")
            checkpoint_path = os.path.join(directory, "resurrection.json")
            with open(gcode_path, "wb") as stream:
                stream.write(b"G90\n")

            resurrector = RESURRECTION.Resurrector.__new__(
                RESURRECTION.Resurrector)
            resurrector.file_path = checkpoint_path
            resurrector.virtual_sdcard = type("SD", (), {
                "get_status": lambda self, eventtime: {
                    "file_path": gcode_path,
                    "file_position": 4,
                    "file_size": 4,
                },
            })()
            resurrector.toolhead = type("Toolhead", (), {
                "get_status": lambda self, eventtime: {
                    "position": [1., 2., 3., 4.],
                },
            })()
            resurrector.extruder = type("Extruder", (), {
                "get_status": lambda self, eventtime: {"target": 220.},
            })()
            resurrector.heater_bed = type("Bed", (), {
                "get_status": lambda self, eventtime: {"target": 60.},
            })()
            resurrector.bed_mesh = type("Mesh", (), {
                "get_status": lambda self, eventtime: {
                    "profile_name": "auto",
                },
            })()
            resurrector.gcode_move = type("Move", (), {
                "get_status": lambda self, eventtime: {
                    "homing_origin": [0., 0., .2],
                },
            })()
            resurrector._checkpoint_cache = None
            resurrector._checkpoint_cache_loaded = False

            resurrector._dump(0.)

            self.assertFalse(os.path.exists(checkpoint_path + ".tmp"))
            with open(checkpoint_path) as stream:
                checkpoint = json.load(stream)
            self.assertEqual(set(checkpoint), {
                "file_path", "file_position", "file_size", "position",
                "extruder_temp", "z_offset", "bed_temp", "mesh",
            })
            self.assertEqual(resurrector._checkpoint_cache, checkpoint)

    def test_checkpoint_accepts_nested_virtual_sd_path(self):
        with tempfile.TemporaryDirectory() as directory:
            nested = os.path.join(directory, "models")
            os.mkdir(nested)
            gcode_path = os.path.join(nested, "part.gcode")
            with open(gcode_path, "wb") as stream:
                stream.write(b"G90\n")
            checkpoint_path = os.path.join(directory, "resurrection.json")
            with open(checkpoint_path, "w") as stream:
                json.dump({
                    "file_path": gcode_path,
                    "file_position": 4,
                    "file_size": 4,
                    "position": [1., 2., 3., 4.],
                    "z_offset": .2,
                    "extruder_temp": 220.,
                    "bed_temp": 60.,
                    "mesh": "auto",
                }, stream)

            resurrector = RESURRECTION.Resurrector.__new__(
                RESURRECTION.Resurrector)
            resurrector.file_path = checkpoint_path
            resurrector.virtual_sdcard = type("SD", (), {
                "sdcard_dirname": directory,
            })()
            command = Command()
            state = resurrector._load_resurrection_state(command)

            self.assertEqual(state["_relative_path"],
                             os.path.join("models", "part.gcode"))
            self.assertEqual(command.responses, [])

    def test_resurrection_applies_reduced_state_in_safe_order(self):
        resurrector = RESURRECTION.Resurrector.__new__(
            RESURRECTION.Resurrector)
        resurrector.state = RESURRECTION.ResurrectorState.RESURRECTION
        resurrector.gcode = GCodeRecorder()
        resurrector.reactor = CooperativeReactor()
        resurrector.virtual_sdcard = VirtualSDRecorder()
        resurrector._worker = None
        resurrector._worker_cancel = None
        resurrector.bed_mesh = type("Mesh", (), {
            "get_status": lambda self, eventtime: {
                "profiles": ["saved"],
            },
        })()
        firmware_retraction = type(
            "FirmwareRetraction", (), {"is_retracted": False})()
        resurrector.printer = type("Printer", (), {
            "lookup_object": lambda self, name, default=None: (
                firmware_retraction
                if name == "firmware_retraction" else default),
        })()
        checkpoint = {
            "file_path": "/gcodes/nested/part.gcode",
            "_relative_path": os.path.join("nested", "part.gcode"),
            "file_position": 1024,
            "file_size": 2048,
            "position": [10., 20., 3., 100.],
            "z_offset": .2,
            "extruder_temp": 220.,
            "bed_temp": 60.,
            "mesh": "saved",
        }
        parsed = RESURRECTION.RecoveryGCodeState()
        parsed.extrude_factor = 95.
        parsed.logical_e = 123.4
        parsed.feedrate = 900.
        parsed.absolute_extrude = False
        parsed.retraction["RETRACT_LENGTH"] = .8
        parsed.retracted = True
        parsed.has_retraction_state = True
        parsed.fans[2] = 127.
        resurrector._load_resurrection_state = lambda gcmd: checkpoint
        resurrector._load_state = lambda state: parsed
        command = Command()

        resurrector.cmd_RESURRECT(command)

        self.assertEqual(
            resurrector.virtual_sdcard.loaded,
            [os.path.join("nested", "part.gcode")])
        self.assertTrue(resurrector.virtual_sdcard.resumed)
        self.assertTrue(firmware_retraction.is_retracted)
        self.assertEqual(
            resurrector.state, RESURRECTION.ResurrectorState.PRINTING)
        lines = [
            line for script in resurrector.gcode.commands
            for line in script.splitlines()
        ]
        self.assertNotIn("G10", lines)
        self.assertNotIn("G11", lines)
        self.assertLess(lines.index("M221 S95"), lines.index("G92 E123.4"))
        self.assertLess(lines.index("G92 E123.4"), lines.index("G1 F900"))
        self.assertLess(
            lines.index("SET_RETRACTION RETRACT_LENGTH=0.8"),
            lines.index("M106 P2 S127"))

    def test_preparation_failure_rolls_back_without_losing_checkpoint(self):
        resurrector = RESURRECTION.Resurrector.__new__(
            RESURRECTION.Resurrector)
        resurrector.state = RESURRECTION.ResurrectorState.RESURRECTION
        resurrector.gcode = FailingPreparationGCode()
        resurrector.reactor = CooperativeReactor()
        resurrector.virtual_sdcard = VirtualSDRecorder()
        resurrector._worker = None
        resurrector._worker_cancel = None
        resurrector.bed_mesh = type("Mesh", (), {
            "get_status": lambda self, eventtime: {
                "profiles": ["saved"],
            },
        })()
        resurrector.printer = type("Printer", (), {
            "lookup_object": lambda self, name, default=None: default,
        })()
        checkpoint = {
            "file_path": "/gcodes/part.gcode",
            "_relative_path": "part.gcode",
            "file_position": 1024,
            "file_size": 2048,
            "position": [10., 20., 3., 100.],
            "z_offset": .2,
            "extruder_temp": 220.,
            "bed_temp": 60.,
            "mesh": "saved",
        }
        resurrector._load_resurrection_state = lambda gcmd: checkpoint
        resurrector._load_state = (
            lambda state: RESURRECTION.RecoveryGCodeState())
        command = Command()

        resurrector.cmd_RESURRECT(command)

        self.assertTrue(resurrector.virtual_sdcard.cancelled)
        self.assertFalse(resurrector.virtual_sdcard.resumed)
        self.assertEqual(
            resurrector.state, RESURRECTION.ResurrectorState.RESURRECTION)
        self.assertTrue(any(
            "TURN_OFF_HEATERS" in script
            for script in resurrector.gcode.commands))
        self.assertTrue(any(
            "preparation failed" in response
            for response in command.responses))


if __name__ == "__main__":
    unittest.main()
