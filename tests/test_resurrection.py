## Tests for power-loss recovery parsing and lifecycle.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import json
import os
import pathlib
import sys
import io
import subprocess
import tempfile
import threading
import time
import unittest
from itertools import product
from types import SimpleNamespace
from unittest import mock


KLIPPER_PATH = pathlib.Path(__file__).parents[1] / ".py" / "klipper"
sys.path.insert(0, str(KLIPPER_PATH))

from plugins import resurrection as RESURRECTION  # noqa: E402
from plugins.recovery import legacy as STATE, state as RECOVERY_STATE  # noqa: E402
from patches.extras import virtual_sdcard as VIRTUAL_SD  # noqa: E402


def move_status(**overrides):
    status = dict(
        absolute_coordinates=True, absolute_extrude=False,
        gcode_position=[0., 0., 0., 5.], speed=900.,
        speed_factor=1., extrude_factor=1.,
        base_position=[0., 0., 0., 0.], homing_origin=[0., 0., 0., 0.])
    status.update(overrides)
    return status


def toolhead_status(**overrides):
    status = dict(position=[1., 2., 3., 4.], max_velocity=300.,
                  max_accel=3000., max_accel_to_decel=1500.,
                  square_corner_velocity=5.)
    status.update(overrides)
    return status


def extruder_status():
    return dict(target=220., pressure_advance=.035, smooth_time=.04)


def inactive_bed_mesh(name=""):
    return SimpleNamespace(
        get_status=lambda eventtime: {"profile_name": name, "profiles": [name]},
        get_mesh=lambda: None,
        pmgr=SimpleNamespace(current_profile=name),
        set_mesh=mock.Mock())


class MeshModel:
    def __init__(self, params):
        self.params = dict(params)
        self.mesh_offsets = [0., 0.]

    def get_mesh_params(self):
        return self.params

    def build_mesh(self, points):
        self.probed_matrix = points

    def set_mesh_offsets(self, offsets):
        self.mesh_offsets = list(offsets)


def active_mesh(points=None):
    mesh = MeshModel(dict(min_x=-10., max_x=20., min_y=-20., max_y=30.,
                          x_count=3, y_count=3, mesh_x_pps=2, mesh_y_pps=2,
                          algo="lagrange", tension=.2))
    mesh.build_mesh(points if points is not None else [
        [.0123456789, .02, .03], [.04, .05, .06], [.07, .08, .09]])
    mesh.set_mesh_offsets([.25, -.5])
    return mesh


class GCodeRecorder:
    def __init__(self):
        self.commands = []
        self.responses = []

    def run_script_from_command(self, command):
        self.commands.append(command)

    def respond_raw(self, message):
        self.responses.append(message)

    def get_mutex(self):
        return SimpleNamespace(test=lambda: False)


class FailingPreparationGCode(GCodeRecorder):
    def run_script_from_command(self, command):
        self.commands.append(command)
        if "_START_PRINT_PREPARE" in command:
            raise RuntimeError("preparation failed")


class Command:
    def __init__(self, **params):
        self.responses = []
        self.params = params

    def get_int(self, name, default, minval=None, maxval=None):
        value = int(self.params.get(name, default))
        if ((minval is not None and value < minval)
                or (maxval is not None and value > maxval)):
            raise ValueError(name)
        return value

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
    def __init__(self, on_resume=None, on_load=None):
        self.loaded = []
        self.resumed = False
        self.cancelled = False
        self.on_resume = on_resume
        self.on_load = on_load
        self.request_command_boundary = mock.Mock()

    def load_file(self, gcmd, filename):
        if self.on_load is not None:
            self.on_load()
        self.loaded.append(filename)

    def do_resume(self):
        self.resumed = True
        if self.on_resume is not None:
            self.on_resume()

    def do_cancel(self):
        self.cancelled = True


class PhysicalToolheadModel:
    def __init__(self, offset=-.08):
        self.offset = offset
        self.physical = [0., 0., 10., 0.]
        self.logical = [0., 0., 0., 0.]
        self.moves = []
        self.following_positions = []
        self.waited = False

    @staticmethod
    def mesh(x, y):
        return .01027586333515498 if x < 0. else .02

    def manual_move(self, coordinate, speed):
        for axis, value in enumerate(coordinate):
            if value is not None:
                self.physical[axis] = value
        self.logical[:2] = self.physical[:2]
        self.logical[2] = (
            self.physical[2] - self.offset
            - self.mesh(self.physical[0], self.physical[1]))
        self.moves.append((list(coordinate), speed))

    def wait_moves(self):
        self.waited = True

    def get_position(self):
        return list(self.physical)

    def following_xy_move(self, x, y):
        self.logical[:2] = [x, y]
        self.physical[:2] = [x, y]
        self.physical[2] = (
            self.logical[2] + self.offset + self.mesh(x, y))
        self.following_positions.append(
            (list(self.logical), list(self.physical)))


class GCodeStateParserTest(unittest.TestCase):
    def parse(self, gcode, position=None):
        data = gcode if isinstance(gcode, bytes) else gcode.encode()
        with tempfile.NamedTemporaryFile() as stream:
            stream.write(data)
            stream.flush()
            if position is None:
                position = len(data)
            return STATE.GCodeStateParser(
                stream.name, position, len(data), threading.Event()).parse()

    def test_mixed_motion_and_state_commands_across_blocks(self):
        data = (b"G90\nM83\nG1 X1 Y2 E.125 F1200\n"
                b"g0 x1 e-1e-3 f900\nG1 X5 Y7\n"
                b"G1 E2 ; E99 F99\nG1 E3\r\n"
                b"N42 G1 E2 F4*15\n"
                b"M106 S64\nM204 S1000\nM220 S95\nM221 S102\n"
                b"SET_PRESSURE_ADVANCE ADVANCE=.05\n"
                b"EXCLUDE_OBJECT_START NAME=PART_A\nEXCLUDE_OBJECT_END\n"
                b"; G1 E99 F99\n# M82\n" + b"; repeated F3 E9 " * 30 +
                b"\nG10\nG11\nM73 P5 R60\n")
        for chunk_size in (17, 65536):
            with self.subTest(chunk_size=chunk_size):
                with mock.patch.object(STATE, "PARSE_CHUNK_SIZE", chunk_size):
                    state = self.parse(data)
                self.assertAlmostEqual(state.logical_e, 7.124)
                self.assertEqual(state.feedrate, 4.)
                self.assertTrue(state.absolute_coordinates)
                self.assertFalse(state.absolute_extrude)
                self.assertEqual(state.fans, {0: 64.})
                self.assertEqual(state.velocity_limits["ACCEL"], 1000.)
                self.assertEqual(state.speed_factor, 95.)
                self.assertEqual(state.extrude_factor, 102.)
                self.assertEqual(state.pressure_advance[None]["ADVANCE"], .05)
                self.assertIsNone(state.current_object)
                self.assertIn("PART_A", state.objects)
                self.assertFalse(state.retracted)
                self.assertEqual(state.progress, {"P": 5., "R": 60.})

    def test_large_file_preserves_relative_and_absolute_extrusion(self):
        prefix = (b"G90\nM83\n" +
                  b"G1 X12 Y3 E.01234\nG1 X8 F900\n" * 5000)
        data = (prefix + b"G92 E0\nM82\nG1 E4.5 F1000\n" +
                b"G1 X1 E6.5\n" * 5000 + b"M106 S80\n")
        relative = self.parse(data, len(prefix))
        self.assertAlmostEqual(relative.logical_e, 61.7)
        self.assertEqual(relative.feedrate, 900.)
        self.assertFalse(relative.absolute_extrude)
        absolute = self.parse(data)
        self.assertEqual(absolute.logical_e, 6.5)
        self.assertEqual(absolute.feedrate, 1000.)
        self.assertTrue(absolute.absolute_extrude)
        self.assertEqual(absolute.fans, {0: 80.})

    def test_reset_preserves_all_other_state(self):
        prefix = (b"M83\nG1 X10 E.0123 F900\n" +
                  b"G1 X11 E.0123\n" * 90000 +
                  b"M106 S128\nM220 S92\nM221 S95\n"
                  b"SET_PRESSURE_ADVANCE ADVANCE=.05\n"
                  b"EXCLUDE_OBJECT_START NAME=PART_A\n")
        tail = (b"G91\nG1 E.15\nG90\n"
                b"g92 e-12.5 ; reset\r\n"
                b"G1 E.5 F1200\nG1 E-.25\nM73 P80\n")
        data = prefix + tail + b"G92 E999\n"
        before = self.parse(data, len(prefix))
        self.assertAlmostEqual(before.logical_e, 1107.0123)
        self.assertEqual(before.feedrate, 900.)
        after = self.parse(data, len(prefix + tail))
        self.assertEqual(after.logical_e, -12.25)
        self.assertEqual(after.feedrate, 1200.)
        self.assertEqual(after.progress, {"P": 80.})
        for state in (before, after):
            self.assertTrue(state.absolute_coordinates)
            self.assertFalse(state.absolute_extrude)
            self.assertEqual(state.fans, {0: 128.})
            self.assertEqual(state.speed_factor, 92.)
            self.assertEqual(state.extrude_factor, 95.)
            self.assertEqual(state.pressure_advance[None]["ADVANCE"], .05)
            self.assertEqual(state.current_object, "PART_A")

    def test_reset_preserves_unusual_relative_values_and_errors(self):
        for body in (b"G1 E1e308\nG1 E1e308\n",
                     b"G1 E1e999\nG1 E-1e999\n",
                     b"G1 E" + b"9" * 320 + b"\nG1 E-1e999\n"):
            with self.subTest(body=body):
                with self.assertRaises((OverflowError, ValueError)):
                    self.parse(b"M83\n" + body + b"G92 E0\nG1 E.5\n")
        self.assertEqual(self.parse(
            b"M83\nG1 E1e-3\nG92 E0\nG1 E.5\n").logical_e, .5)

    def test_only_effective_resets_change_logical_extrusion(self):
        prefix = b"M83\nG1 E1.25\n" * 10000
        for tail, expected in ((b"; G92 E0\n", 12500.5),
                               (b"# G92 E0\n", 12500.5),
                               (b"G92 X0\n", 12500.5),
                               (b"G92 E0 X0\n", .5),
                               (b"N1 G92 E0*42\n", .5),
                               (b"G92\n", .5)):
            with self.subTest(tail=tail):
                self.assertEqual(self.parse(
                    prefix + tail + b"G1 E.5\n").logical_e, expected)

    def test_reset_split_across_chunk_boundary(self):
        data = (b"M83\nG1 E.5\n" + b";" +
                b"x" * (65536 - len(b"M83\nG1 E.5\n") - 5) +
                b"\nG92 E0\nG1 E.25\n")
        self.assertEqual(self.parse(data).logical_e, .25)

    def test_reset_at_block_boundaries(self):
        for delta in (-1, 0, 1, 2):
            with self.subTest(delta=delta):
                prefix = b"M83\nG1 E.125 F900\n" + b"G1 E.25\n" * 100
                prefix += b";" + b"x" * (65536 + delta - len(prefix) - 2) + b"\n"
                data = prefix + b"G92 E7\nG1 E.25 F1000\nM106 S64\n"
                state = self.parse(data)
                self.assertEqual(state.logical_e, 7.25)
                self.assertEqual(state.feedrate, 1000.)
                self.assertEqual(state.fans, {0: 64.})
                self.assertFalse(state.absolute_extrude)

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

    def test_object_definitions_and_current_object_are_reduced_up_to_checkpoint(self):
        first = ('EXCLUDE_OBJECT_DEFINE NAME="Крышка левая" CENTER=1,2 POLYGON=[[0,0],[2,0],[2,3]]\n'
                 'EXCLUDE_OBJECT_START NAME="Крышка левая"\nG1 X1 E1 F900\n')
        state = self.parse(first + 'EXCLUDE_OBJECT_END NAME="Крышка левая"\n', len(first.encode()))
        self.assertEqual(state.current_object, 'КРЫШКА ЛЕВАЯ')
        self.assertEqual(state.objects['КРЫШКА ЛЕВАЯ']['center'], [1, 2])
        self.assertEqual(state.objects['КРЫШКА ЛЕВАЯ']['polygon'], [[0, 0], [2, 0], [2, 3]])
        state = self.parse(first + 'EXCLUDE_OBJECT_END\nEXCLUDE_OBJECT_START NAME=PART_B\n')
        self.assertEqual(state.current_object, 'PART_B')
        self.assertIn('PART_B', state.objects)
        state = self.parse(first + 'EXCLUDE_OBJECT_DEFINE RESET=1\n')
        self.assertEqual(state.objects, {})
        self.assertIsNone(state.current_object)

    def test_object_identifiers_are_parsed_with_klipper_quoting_and_invalid_geometry_is_rejected(self):
        state = self.parse("EXCLUDE_OBJECT_START NAME='PART RESET=1'\n")
        self.assertEqual(state.current_object, 'PART RESET=1')
        for line in ('EXCLUDE_OBJECT_DEFINE NAME="unterminated\n',
                     'EXCLUDE_OBJECT_DEFINE NAME=PART CENTER=nan,0\n',
                     'EXCLUDE_OBJECT_DEFINE NAME=PART POLYGON=[[0,0],[1,"bad"]]\n'):
            with self.subTest(line=line), self.assertRaises(RESURRECTION.RecoveryParseError):
                self.parse(line)

    def test_object_markers_survive_chunk_boundaries_line_numbers_and_comments(self):
        data = ('n10 exclude_object_define name="Крышка левая" center=1,2*42 ; geometry\n'
                'n20 exclude_object_start name="Крышка левая"*42\n'
                'G1 X1 E1 F900\n')
        with mock.patch.object(STATE, 'PARSE_CHUNK_SIZE', 17):
            state = self.parse(data)
        self.assertEqual(state.current_object, 'КРЫШКА ЛЕВАЯ')
        self.assertEqual(state.objects['КРЫШКА ЛЕВАЯ']['center'], [1, 2])

    def test_pre_cancelled_parse_stops_without_reading_state(self):
        data = b"M106 S64\n"
        with tempfile.NamedTemporaryFile() as stream:
            stream.write(data)
            stream.flush()
            cancel = threading.Event()
            cancel.set()
            parser = STATE.GCodeStateParser(
                stream.name, len(data), len(data), cancel)
            with self.assertRaises(RECOVERY_STATE.RecoveryParseCancelled):
                parser.parse()

    def test_oversized_comments_do_not_expand_parser_memory(self):
        data = (b";" + b"x" * (STATE.MAX_GCODE_LINE_SIZE + 1)
                + b"\nM106 S64\n")
        state = self.parse(data)
        self.assertEqual(state.fans, {0: 64.})

    def test_oversized_command_is_rejected(self):
        data = (b"UNKNOWN " + b"x" * STATE.MAX_GCODE_LINE_SIZE
                + b"\n")
        with self.assertRaisesRegex(
                RESURRECTION.RecoveryParseError, "exceeds"):
            self.parse(data)


class ResurrectorLifecycleTest(unittest.TestCase):
    def test_parser_reports_bytes_processed_up_to_checkpoint(self):
        with tempfile.NamedTemporaryFile() as stream:
            data = b"G90\nG1 X1\nG1 X2\n"
            stream.write(data)
            stream.flush()
            position = len(b"G90\nG1 X1\n")
            parser = STATE.GCodeStateParser(
                stream.name, position, len(data), threading.Event())
            self.assertEqual(parser.bytes_processed, 0)
            parser.parse()
            self.assertEqual(parser.bytes_processed, position)

    def test_worker_wait_is_cooperative_and_leaves_no_thread(self):
        resurrector = RESURRECTION.Resurrector.__new__(
            RESURRECTION.Resurrector)
        resurrector.gcode = GCodeRecorder()
        resurrector.reactor = CooperativeReactor()
        resurrector.state = RESURRECTION.ResurrectorState.LOADING
        resurrector._legacy_loader = None
        parsed = RECOVERY_STATE.RecoveryGCodeState()

        threads = []

        def delayed_parse(_parser):
            threads.append(threading.current_thread())
            time.sleep(.025)
            return parsed

        with mock.patch.object(
                STATE.GCodeStateParser, "parse", delayed_parse):
            result = resurrector._load_state({
                "file_path": "/unused",
                "file_position": 0,
                "file_size": 0,
                "z_offset": -.08,
            })

        self.assertIs(result, parsed)
        self.assertEqual(result.gcode_offsets, [0., 0., -.08])
        self.assertEqual(result.base_xyz, result.gcode_offsets)
        self.assertGreater(resurrector.reactor.pauses, 0)
        self.assertIsNone(resurrector._legacy_loader)
        self.assertEqual(len(threads), 1)
        self.assertFalse(threads[0].is_alive())

    def test_disconnect_cancels_and_joins_legacy_parser(self):
        resurrector = RESURRECTION.Resurrector.__new__(
            RESURRECTION.Resurrector)
        resurrector.reactor = CooperativeReactor()
        resurrector.state = RESURRECTION.ResurrectorState.LOADING
        resurrector._legacy_loader = None
        resurrector._timer = None
        resurrector._recovery_active = False
        threads = []

        def wait_for_cancel(parser):
            threads.append(threading.current_thread())
            if not parser.cancel_event.wait(2.):
                raise AssertionError("Legacy parser was not cancelled")
            raise STATE.RecoveryParseCancelled("cancelled")

        def disconnect(_):
            self.assertEqual(resurrector.get_status(0.)["parse_progress"], 0)
            resurrector._disconnect()

        resurrector.reactor.pause = disconnect
        with mock.patch.object(STATE.GCodeStateParser, "parse", wait_for_cancel):
            with self.assertRaises(STATE.RecoveryParseCancelled):
                resurrector._load_state({
                    "file_path": "/unused", "file_position": 0,
                    "file_size": 0, "z_offset": 0.,
                })
        self.assertEqual(resurrector.state, RESURRECTION.ResurrectorState.DESTROYED)
        self.assertIsNone(resurrector._legacy_loader)
        self.assertEqual(len(threads), 1)
        self.assertFalse(threads[0].is_alive())

    def test_dump_is_atomic_and_persists_recovery_state(self):
        with tempfile.TemporaryDirectory() as directory:
            gcode_path = os.path.join(directory, "part.gcode")
            checkpoint_path = os.path.join(directory, "resurrection.json")
            with open(gcode_path, "wb") as stream:
                stream.write(b"G90\nM82\n")

            resurrector = RESURRECTION.Resurrector.__new__(
                RESURRECTION.Resurrector)
            resurrector.file_path = checkpoint_path
            resurrector.printer = SimpleNamespace(lookup_object=lambda name, default=None: default)
            resurrector.virtual_sdcard = type("SD", (), {
                "get_status": lambda self, eventtime: {
                    "file_path": gcode_path,
                    "file_position": 4,
                    "file_size": 8,
                    "file_mtime_ns": os.stat(gcode_path).st_mtime_ns,
                },
                "is_cmd_from_sd": lambda self: True,
                "get_file_position": lambda self: 8,
            })()
            resurrector.toolhead = type("Toolhead", (), {
                "get_status": lambda self, eventtime: toolhead_status(),
            })()
            resurrector.extruder = type("Extruder", (), {
                "get_status": lambda self, eventtime: extruder_status(),
            })()
            resurrector.heater_bed = type("Bed", (), {
                "get_status": lambda self, eventtime: {"target": 60.},
            })()
            resurrector.bed_mesh = inactive_bed_mesh("auto")
            resurrector.gcode_move = type("Move", (), {
                "get_status": lambda self, eventtime: move_status(
                    homing_origin=[0., 0., .2, 0.]),
            })()
            resurrector._checkpoint_cache = None
            resurrector._checkpoint_cache_loaded = False
            resurrector.print_stats = SimpleNamespace(
                get_status=lambda eventtime: {"state": "printing"})
            resurrector.gcode = GCodeRecorder()
            resurrector.state = RESURRECTION.ResurrectorState.PRINTING
            resurrector.reactor = CooperativeReactor()
            resurrector.virtual_sdcard.is_active = lambda: True
            resurrector._disconnect = mock.Mock()

            resurrector._dump(0.)
            previous = pathlib.Path(checkpoint_path).read_bytes()
            resurrector._shutdown()
            resurrector._disconnect.assert_called_once_with()
            self.assertEqual(pathlib.Path(checkpoint_path).read_bytes(), previous)
            resurrector.virtual_sdcard.is_cmd_from_sd = lambda: False
            with mock.patch.object(RESURRECTION.os, "fsync",
                                   side_effect=OSError("disk error")):
                resurrector._shutdown()
            self.assertEqual(pathlib.Path(checkpoint_path).read_bytes(), previous)
            self.assertEqual(resurrector.state, RESURRECTION.ResurrectorState.PRINTING)
            resurrector._dump(0.)

            self.assertFalse(os.path.exists(checkpoint_path + ".tmp"))
            with open(checkpoint_path) as stream:
                checkpoint = json.load(stream)
            restored = resurrector._load_state(checkpoint)
            self.assertEqual(restored.logical_e, 5.)
            self.assertEqual(restored.gcode_offsets, [0., 0., .2])
            self.assertEqual(restored.velocity_limits["ACCEL"], 3000.)
            self.assertEqual(checkpoint["gcode_offsets"], [0., 0., .2])
            self.assertEqual(checkpoint["file_position"], 4)
            self.assertEqual(checkpoint["version"], 2)
            self.assertEqual(checkpoint["logical_e"], 5.)
            self.assertEqual(RESURRECTION.companion.read(
                checkpoint_path, checkpoint["objects_ref"]), {"mesh": {"active": False}})
            self.assertNotIn("homing_xyz", checkpoint)
            self.assertEqual(checkpoint["position"], [1., 2., 3., 4.])
            self.assertEqual(resurrector._checkpoint_cache, checkpoint)

    def test_checkpoint_round_trip_preserves_total_offset_after_mesh_compensation(self):
        from tests.test_bed_mesh_validation import Harness

        cases = product((0, 1), (-.3, .3), (0., .02), (0, 1))
        for mode, shift, manual_adjustment, load_saved_offset in cases:
            with self.subTest(mode=mode, shift=shift,
                              manual_adjustment=manual_adjustment,
                              load_saved_offset=load_saved_offset), tempfile.TemporaryDirectory() as directory:
                live = Harness(directory, action=2, offset_mode=mode,
                               shifts=[shift] * 5)
                live.run('_CHECK_BED_MESH')
                if manual_adjustment:
                    live.run('SET_GCODE_OFFSET', Z_ADJUST=str(manual_adjustment))
                expected = (shift if mode == 1 else .05 + shift) + manual_adjustment
                self.assertAlmostEqual(live.offset, expected)
                # Z changes are not saved while compensation is active.
                self.assertAlmostEqual(live.mod.variables['z_offset'], .05)

                gcode_path = pathlib.Path(directory) / 'part.gcode'
                gcode_path.write_text('G90\nM82\nG1 X10 Y10 E1\n')
                stats = dict(file_path=str(gcode_path), file_position=gcode_path.stat().st_size,
                             file_size=gcode_path.stat().st_size,
                             file_mtime_ns=gcode_path.stat().st_mtime_ns)
                recovery = RESURRECTION.Resurrector.__new__(RESURRECTION.Resurrector)
                recovery.file_path = str(pathlib.Path(directory) / 'resurrection.json')
                recovery.printer = SimpleNamespace(lookup_object=lambda name, default=None: default)
                recovery.virtual_sdcard = VirtualSDRecorder()
                recovery.virtual_sdcard.sdcard_dirname = directory
                recovery.virtual_sdcard.get_status = lambda eventtime: stats
                recovery.virtual_sdcard.is_cmd_from_sd = lambda: False
                recovery.toolhead = SimpleNamespace(
                    get_status=lambda eventtime: toolhead_status(position=[10., 10., .3, 1.]))
                recovery.extruder = SimpleNamespace(
                    get_status=lambda eventtime: extruder_status())
                recovery.heater_bed = SimpleNamespace(
                    get_status=lambda eventtime: {'target': 60.})
                recovery.bed_mesh = inactive_bed_mesh("auto")
                recovery.gcode_move = live.move
                recovery.print_stats = None
                recovery._dump(0.)
                checkpoint = json.loads(pathlib.Path(recovery.file_path).read_text())
                self.assertAlmostEqual(checkpoint['gcode_offsets'][2], expected)


                # A fresh Klipper session does not know about the compensation
                # (documented limitation): the total offset stays until restored.
                rebooted = Harness(directory, action=2, offset_mode=mode,
                                   offset=live.mod.variables['z_offset'])
                rebooted.mod.variables['load_zoffset'] = load_saved_offset
                saved_config = pathlib.Path(rebooted.mod.filename).read_bytes()
                recovery.gcode_move = rebooted.move
                recovery.reactor = CooperativeReactor()
                recovery.state = RESURRECTION.ResurrectorState.RESURRECTION
                recovery._recovery_active = False
                recovery._legacy_loader = None
                recovery.printer = SimpleNamespace(
                    lookup_object=lambda name, default=None: default)
                recovery.gcode = SimpleNamespace(
                    run_script_from_command=lambda script: [
                        rebooted.execute(line) for line in script.splitlines()],
                    respond_raw=lambda message: None)
                restored_positions = []

                def restore_position(position):
                    self.assertAlmostEqual(rebooted.offset, expected)
                    restored_positions.append(position)
                recovery._restore_physical_position = restore_position

                recovery.cmd_RESURRECT(Command())

                self.assertEqual(recovery.state, RESURRECTION.ResurrectorState.PRINTING)
                self.assertTrue(recovery.virtual_sdcard.resumed)
                self.assertEqual(restored_positions, [[10., 10., .3, 1.]])
                self.assertAlmostEqual(rebooted.offset, expected)
                self.assertIsNone(rebooted.offset_before)
                self.assertFalse(any(name in {'PROBE', 'BED_MESH_CALIBRATE'}
                                     for name, params in rebooted.trace))
                recovery._dump(0.)
                updated_checkpoint = json.loads(pathlib.Path(recovery.file_path).read_text())
                self.assertAlmostEqual(updated_checkpoint['gcode_offsets'][2], expected)
                rebooted.run('_STOP')
                self.assertAlmostEqual(rebooted.offset, expected)
                self.assertEqual(pathlib.Path(rebooted.mod.filename).read_bytes(), saved_config)

    def test_pause_freezes_pre_park_checkpoint_until_position_is_restored(self):
        with tempfile.TemporaryDirectory() as directory:
            gcode_path = os.path.join(directory, "part.gcode")
            checkpoint_path = os.path.join(directory, "resurrection.json")
            with open(gcode_path, "wb") as stream:
                stream.write(b"G90\nM82\n")

            head_status = toolhead_status(position=[10., 20., 3.25, 4.])
            print_stats_status = {"state": "paused"}
            pause_resume = SimpleNamespace(is_paused=True)
            sd_active = False
            sd_status = {
                "file_path": gcode_path,
                "file_position": 4,
                "file_size": 8,
                "file_mtime_ns": os.stat(gcode_path).st_mtime_ns,
            }

            resurrector = RESURRECTION.Resurrector.__new__(
                RESURRECTION.Resurrector)
            resurrector.enabled = True
            resurrector.state = RESURRECTION.ResurrectorState.PRINTING
            resurrector.dump_time = 3.
            resurrector.file_path = checkpoint_path
            resurrector.printer = SimpleNamespace(lookup_object=lambda name, default=None:
                pause_resume if name == "pause_resume" else default)
            resurrector.reactor = CooperativeReactor()
            resurrector.print_stats = type("Stats", (), {
                "get_status": lambda self, eventtime: print_stats_status,
            })()
            resurrector.start_print_macro = type("Start", (), {
                "variables": {"print_started": True},
            })()
            resurrector.virtual_sdcard = type("SD", (), {
                "get_status": lambda self, eventtime: sd_status,
                "is_cmd_from_sd": lambda self: False,
                "is_active": lambda self: sd_active,
                "get_file_position": lambda self: 8,
                "request_command_boundary": lambda self: None,
            })()
            resurrector.toolhead = type("Toolhead", (), {
                "get_status": lambda self, eventtime: head_status,
            })()
            resurrector.extruder = type("Extruder", (), {
                "get_status": lambda self, eventtime: extruder_status(),
            })()
            resurrector.heater_bed = type("Bed", (), {
                "get_status": lambda self, eventtime: {"target": 60.},
            })()
            resurrector.bed_mesh = inactive_bed_mesh("auto")
            resurrector.gcode_move = type("Move", (), {
                "get_status": lambda self, eventtime: move_status(
                    homing_origin=[0., 0., -.08, 0.]),
            })()
            resurrector._checkpoint_cache = None
            resurrector._checkpoint_cache_loaded = False
            resurrector._timer = None
            resurrector.gcode = GCodeRecorder()

            # The timer may observe PAUSE_BASE before the explicit marker.
            resurrector._dump_timer_handler(100.)
            self.assertEqual(resurrector.state, RESURRECTION.ResurrectorState.PAUSED)
            self.assertFalse(os.path.exists(checkpoint_path))

            resurrector.toolhead.wait_moves = mock.Mock(
                side_effect=AssertionError("checkpoint must not drain motion"))
            resurrector.cmd_RESURRECTION_PAUSE(Command())
            self.assertEqual(
                resurrector.state, RESURRECTION.ResurrectorState.PAUSED)
            with open(checkpoint_path) as stream:
                paused_checkpoint = json.load(stream)
            self.assertEqual(paused_checkpoint["position"], [10., 20., 3.25, 4.])
            self.assertEqual(paused_checkpoint["file_position"], 4)
            resurrector.toolhead.wait_moves.assert_not_called()

            # Parking and subsequent timers cannot overwrite the print endpoint.
            head_status["position"] = [0., 0., 25., 4.]
            print_stats_status["state"] = "printing"  # SD has not reported its pause yet.
            sd_active = True
            resurrector._dump_timer_handler(resurrector.reactor.monotonic())
            with open(checkpoint_path) as stream:
                parked_checkpoint = json.load(stream)
            self.assertEqual(parked_checkpoint, paused_checkpoint)

            print_stats_status["state"] = "paused"
            sd_active = False
            resurrector._dump_timer_handler(resurrector.reactor.monotonic())
            with open(checkpoint_path) as stream:
                long_pause_checkpoint = json.load(stream)
            self.assertEqual(long_pause_checkpoint, paused_checkpoint)

            # RESUME_BASE restores PAUSE_STATE before the recovery resume
            # marker runs.
            head_status["position"] = [10., 20., 3.25, 4.]
            pause_resume.is_paused = False
            sd_active = True
            resurrector.cmd_RESURRECTION_RESUME(Command())
            with open(checkpoint_path) as stream:
                resumed_checkpoint = json.load(stream)
            self.assertEqual(resumed_checkpoint["position"][2], 3.25)
            self.assertEqual(
                resurrector.state, RESURRECTION.ResurrectorState.PRINTING)

            # print_stats is updated by the SD worker.  If the periodic timer
            # runs first, it must not mistake this transition for a new pause.
            resurrector._dump_timer_handler(
                resurrector.reactor.monotonic())
            self.assertEqual(resurrector.state, RESURRECTION.ResurrectorState.PRINTING)

            print_stats_status["state"] = "printing"
            resurrector._dump_timer_handler(
                resurrector.reactor.monotonic())
            self.assertEqual(resurrector.state, RESURRECTION.ResurrectorState.PRINTING)

    def test_nested_sd_pause_saves_following_line_without_draining_motion(self):
        recovery = RESURRECTION.Resurrector.__new__(RESURRECTION.Resurrector)
        recovery.enabled = True
        recovery.state = RESURRECTION.ResurrectorState.PRINTING
        recovery.reactor = CooperativeReactor()
        recovery.print_stats = SimpleNamespace(
            get_status=lambda eventtime: {"state": "printing"})
        recovery.start_print_macro = SimpleNamespace(variables={"print_started": True})
        recovery.virtual_sdcard = SimpleNamespace(
            is_cmd_from_sd=lambda: True, get_file_position=lambda: 42)
        recovery._dump = mock.Mock()
        recovery.cmd_RESURRECTION_PAUSE(Command())
        self.assertEqual(recovery.state, RESURRECTION.ResurrectorState.PAUSED)
        recovery._dump.assert_called_once_with(100., 42)

    def test_shutdown_saves_only_a_consistent_print_or_refreshes_pause(self):
        for state, parked, active, from_sd, locked, stats, save, refresh in (
                ("PRINTING", False, True, False, False, "printing", True, False),
                ("PRINTING", True, True, False, False, "printing", False, False),
                ("PRINTING", False, False, False, False, "printing", False, False),
                ("PRINTING", False, True, True, False, "printing", False, False),
                ("PRINTING", False, True, False, True, "printing", False, False),
                ("PRINTING", False, True, False, False, "paused", False, False),
                ("PAUSED", True, False, False, False, "paused", False, True),
                ("IDLE", False, False, False, False, "printing", False, False),
                ("PREPARING", False, False, False, False, "printing", False, False)):
            with self.subTest(state=state, parked=parked, active=active,
                              from_sd=from_sd, locked=locked, stats=stats):
                recovery = RESURRECTION.Resurrector.__new__(RESURRECTION.Resurrector)
                recovery.state = RESURRECTION.ResurrectorState[state]
                recovery.reactor = CooperativeReactor()
                recovery.printer = SimpleNamespace(lookup_object=lambda name, default=None:
                    SimpleNamespace(is_paused=parked) if name == "pause_resume" else default)
                recovery.virtual_sdcard = SimpleNamespace(
                    is_cmd_from_sd=lambda: from_sd, is_active=lambda: active)
                recovery.gcode = GCodeRecorder()
                recovery.gcode.get_mutex = lambda: SimpleNamespace(test=lambda: locked)
                recovery.print_stats = SimpleNamespace(
                    get_status=lambda eventtime: {"state": stats})
                recovery._dump = mock.Mock()
                recovery._refresh_paused_exclusions = mock.Mock()
                recovery._disconnect = mock.Mock()
                recovery._shutdown()
                self.assertEqual(recovery._dump.call_count, int(save))
                self.assertEqual(recovery._refresh_paused_exclusions.call_count, int(refresh))
                recovery._disconnect.assert_called_once_with()

    def test_shutdown_disconnects_even_if_dump_fails(self):
        recovery = RESURRECTION.Resurrector.__new__(RESURRECTION.Resurrector)
        recovery.state = RESURRECTION.ResurrectorState.PRINTING
        recovery.reactor = CooperativeReactor()
        recovery.printer = SimpleNamespace(lookup_object=lambda name, default=None: default)
        recovery.virtual_sdcard = SimpleNamespace(
            is_cmd_from_sd=lambda: False, is_active=lambda: True)
        recovery.gcode = GCodeRecorder()
        recovery.print_stats = SimpleNamespace(
            get_status=lambda eventtime: {"state": "printing"})
        recovery._dump = mock.Mock(side_effect=OSError("disk error"))
        recovery._disconnect = mock.Mock()
        with self.assertRaises(OSError):
            recovery._shutdown()
        recovery._disconnect.assert_called_once_with()

    def test_observed_pause_keeps_last_periodic_checkpoint(self):
        resurrector = RESURRECTION.Resurrector.__new__(
            RESURRECTION.Resurrector)
        resurrector.state = RESURRECTION.ResurrectorState.PRINTING
        resurrector.dump_time = 3.
        resurrector.virtual_sdcard = SimpleNamespace(is_active=lambda: False)
        resurrector.print_stats = type("Stats", (), {
            "get_status": lambda self, eventtime: {"state": "paused"},
        })()
        resurrector.start_print_macro = type("Start", (), {
            "variables": {"print_started": True},
        })()
        resurrector._dump = mock.Mock()
        resurrector._checkpoint_cache = None
        resurrector.printer = SimpleNamespace(lookup_object=lambda name, default=None: default)

        resurrector._dump_timer_handler(100.)

        resurrector._dump.assert_not_called()
        self.assertEqual(
            resurrector.state, RESURRECTION.ResurrectorState.PAUSED)

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

    def test_invalid_checkpoint_values_are_reported_without_raising(self):
        with tempfile.TemporaryDirectory() as directory:
            resurrector = RESURRECTION.Resurrector.__new__(
                RESURRECTION.Resurrector)
            resurrector.file_path = os.path.join(directory, "resurrection.json")
            for value in (None, [], 42, "file_path", {
                    "extruder_temp": True, "bed_temp": 60}, {
                    "extruder_temp": float("inf"), "bed_temp": 60}):
                with self.subTest(value=value):
                    with open(resurrector.file_path, "w") as stream:
                        json.dump(value, stream)
                    command = Command()
                    self.assertIsNone(resurrector._load_resurrection_state(
                        command, cleanup=True))
                    self.assertTrue(resurrector.last_error)
                    self.assertIn(resurrector.last_error, command.responses[-1])

    def test_cleanup_does_not_require_original_gcode(self):
        with tempfile.TemporaryDirectory() as directory:
            resurrector = RESURRECTION.Resurrector.__new__(
                RESURRECTION.Resurrector)
            resurrector.file_path = os.path.join(directory, "resurrection.json")
            with open(resurrector.file_path, "w") as stream:
                json.dump({"file_path": "/missing.gcode",
                           "file_size": -1, "file_position": "invalid",
                           "extruder_temp": 220, "bed_temp": 60}, stream)
            resurrector.state = RESURRECTION.ResurrectorState.RESURRECTION
            resurrector.gcode = GCodeRecorder()
            resurrector.reactor = CooperativeReactor()
            command = Command()
            resurrector.cmd_RESURRECT_ABORT(command)
            self.assertFalse(os.path.exists(resurrector.file_path))
            self.assertEqual(resurrector.state, RESURRECTION.ResurrectorState.IDLE)
            self.assertIsNone(resurrector.last_error)
            self.assertFalse(any(line.startswith("!!") for line in command.responses))
            self.assertIn("_HOME_IF_NEEDED", "\n".join(resurrector.gcode.commands))

    def test_failed_checkpoint_removal_does_not_report_cleanup_success(self):
        with tempfile.TemporaryDirectory() as directory:
            resurrector = RESURRECTION.Resurrector.__new__(
                RESURRECTION.Resurrector)
            resurrector.file_path = os.path.join(directory, "resurrection.json")
            with open(resurrector.file_path, "w") as stream:
                json.dump({"extruder_temp": 220, "bed_temp": 60}, stream)
            resurrector.state = RESURRECTION.ResurrectorState.RESURRECTION
            resurrector.gcode = GCodeRecorder()
            resurrector.reactor = CooperativeReactor()
            resurrector._legacy_loader = None
            command = Command()
            with mock.patch.object(RESURRECTION.os, "remove",
                                   side_effect=PermissionError("denied")):
                resurrector.cmd_RESURRECT_ABORT(command)
            self.assertTrue(os.path.exists(resurrector.file_path))
            self.assertEqual(resurrector.state,
                             RESURRECTION.ResurrectorState.RESURRECTION)
            self.assertIn("denied", resurrector.last_error)
            self.assertNotIn("// Resurrection aborted!", resurrector.gcode.responses)

    def test_cleanup_context_failure_after_deletion_does_not_restore_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            resurrector = RESURRECTION.Resurrector.__new__(RESURRECTION.Resurrector)
            resurrector.file_path = os.path.join(directory, "resurrection.json")
            with open(resurrector.file_path, "w") as stream:
                json.dump({"extruder_temp": 220, "bed_temp": 60}, stream)
            resurrector.state = RESURRECTION.ResurrectorState.RESURRECTION
            resurrector.reactor = CooperativeReactor()
            resurrector.gcode = GCodeRecorder()
            run = resurrector.gcode.run_script_from_command

            def fail_end(command):
                run(command)
                if command == "_CONTEXT_END":
                    raise RuntimeError("context failed")

            resurrector.gcode.run_script_from_command = fail_end
            command = Command()
            resurrector.cmd_RESURRECT_ABORT(command)

            self.assertFalse(os.path.exists(resurrector.file_path))
            self.assertEqual(resurrector.state, RESURRECTION.ResurrectorState.IDLE)
            self.assertIsNone(resurrector.last_error)
            self.assertIn("_CONTEXT_RESET", resurrector.gcode.commands)

    def test_validation_error_survives_context_reset_failure(self):
        resurrector = RESURRECTION.Resurrector.__new__(RESURRECTION.Resurrector)
        resurrector.state = RESURRECTION.ResurrectorState.RESURRECTION
        resurrector.gcode = GCodeRecorder()

        def reject(gcmd):
            resurrector._report_recovery_error(gcmd, "invalid checkpoint")
            return None

        def fail_reset(command):
            if command == "_CONTEXT_RESET":
                raise RuntimeError("context reset failed")

        resurrector._load_resurrection_state = reject
        resurrector.gcode.run_script_from_command = fail_reset
        command = Command()
        resurrector.cmd_RESURRECT(command)

        self.assertEqual(resurrector.last_error, "invalid checkpoint")
        self.assertEqual(resurrector.state, RESURRECTION.ResurrectorState.RESURRECTION)
        self.assertIn("!! invalid checkpoint", command.responses)

    def test_recovery_context_wraps_validation_and_resets_on_rejection(self):
        for command_name in ("cmd_RESURRECT", "cmd_RESURRECT_ABORT"):
            with self.subTest(command=command_name):
                resurrector = RESURRECTION.Resurrector.__new__(
                    RESURRECTION.Resurrector)
                resurrector.state = (
                    RESURRECTION.ResurrectorState.RESURRECTION)
                resurrector.gcode = GCodeRecorder()
                resurrector._load_resurrection_state = lambda gcmd, **kwargs: None
                command = Command()

                getattr(resurrector, command_name)(command)

                lines = [
                    line for script in resurrector.gcode.commands
                    for line in script.splitlines()]
                self.assertEqual(lines, [
                    "_CONTEXT_BEGIN TYPE=recovery",
                    '_CONTEXT_STATE NAME="LOADING STATE"',
                    "_CONTEXT_RESET",
                ])

    def test_resurrection_applies_reduced_state_in_safe_order(self):
        resurrector = RESURRECTION.Resurrector.__new__(
            RESURRECTION.Resurrector)
        resurrector.state = RESURRECTION.ResurrectorState.RESURRECTION
        resurrector.gcode = GCodeRecorder()
        resurrector.reactor = CooperativeReactor()
        resurrector.file_path = "/missing"
        toolhead = PhysicalToolheadModel()

        def continue_layer():
            toolhead.following_xy_move(24., 12.)
            toolhead.following_xy_move(-12., -24.)
            toolhead.following_xy_move(15., 15.)

        resurrector.virtual_sdcard = VirtualSDRecorder(
            continue_layer, resurrector._handle_virtual_sd_reset)
        resurrector._recovery_active = False
        resurrector.print_stats = type("Stats", (), {
            "get_status": lambda self, eventtime: {"state": "printing"},
        })()
        resurrector._legacy_loader = None
        resurrector.toolhead = toolhead
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
            "position": [
                -13.985956125424346, -24.423308017104159,
                .23027586333515498, 100.],
            "z_offset": -.08,
            "extruder_temp": 220.,
            "bed_temp": 60.,
            "mesh": "saved",
        }
        parsed = RECOVERY_STATE.RecoveryGCodeState()
        parsed.gcode_offsets = [0., 0., -.08]
        parsed.base_xyz = list(parsed.gcode_offsets)
        resurrector.gcode_move = SimpleNamespace(base_position=[0.] * 4)
        parsed.extrude_factor = 95.
        parsed.logical_e = 123.4
        parsed.feedrate = 900.
        parsed.absolute_extrude = False
        parsed.retraction["RETRACT_LENGTH"] = .8
        parsed.retracted = True
        parsed.has_retraction_state = True
        parsed.fans[2] = 127.
        resurrector._load_resurrection_state = lambda gcmd: checkpoint
        def load_state(state):
            self.assertEqual(
                resurrector.state, RESURRECTION.ResurrectorState.LOADING)
            self.assertFalse(resurrector._recovery_active)
            return parsed

        resurrector._load_state = load_state
        command = Command()

        resurrector.cmd_RESURRECT(command)

        self.assertEqual(
            resurrector.virtual_sdcard.loaded,
            [os.path.join("nested", "part.gcode")])
        self.assertTrue(resurrector.virtual_sdcard.resumed)
        self.assertTrue(resurrector.get_status(0.)["restored"])
        self.assertTrue(firmware_retraction.is_retracted)
        self.assertEqual(toolhead.moves, [
            ([-13.985956125424346, -24.423308017104159, None], 100.),
            ([None, None, .23027586333515498], 50.),
        ])
        self.assertTrue(toolhead.waited)
        for logical, physical in toolhead.following_positions:
            self.assertAlmostEqual(logical[2], .3)
            self.assertAlmostEqual(
                physical[2],
                logical[2] + toolhead.offset
                + toolhead.mesh(logical[0], logical[1]))
        self.assertEqual(
            resurrector.state, RESURRECTION.ResurrectorState.PRINTING)
        lines = [
            line for script in resurrector.gcode.commands
            for line in script.splitlines()
        ]
        self.assertIn("_SET_GCODE_OFFSET X=0.0 Y=0.0 Z=-0.08 MOVE=0", lines)
        self.assertIn('_CONTEXT_BEGIN TYPE=recovery', lines)
        self.assertIn('_CONTEXT_STATE NAME="LOADING STATE"', lines)
        self.assertLess(
            lines.index('_CONTEXT_STATE NAME=POSITIONING'),
            lines.index('_CONTEXT_STATE NAME="RESTORING STATE"'))
        self.assertIn("_CONTEXT_END", lines)
        self.assertIn("_CONTEXT_BEGIN TYPE=print", lines)
        self.assertIn("_CONTEXT_STATE NAME=PRINTING", lines)
        self.assertNotIn("G10", lines)
        self.assertNotIn("G11", lines)
        self.assertFalse(any(
            line.startswith("G1 X") or line.startswith("G1 Z")
            for line in lines))
        self.assertLess(lines.index("M221 S95"), lines.index("G92 E123.4"))
        self.assertLess(lines.index("G92 E123.4"), lines.index("G1 F900"))
        self.assertLess(
            lines.index("SET_RETRACTION RETRACT_LENGTH=0.8"),
            lines.index("M106 P2 S127"))

        resurrector._change_state(
            RESURRECTION.ResurrectorState.RESURRECTION)
        resurrector.virtual_sdcard.do_resume = mock.Mock(
            side_effect=RuntimeError("resume failed"))
        failed = Command()

        resurrector.cmd_RESURRECT(failed)

        self.assertEqual(
            resurrector.state, RESURRECTION.ResurrectorState.RESURRECTION)
        self.assertFalse(resurrector.get_status(0.)["restored"])
        self.assertTrue(any(
            "resume failed" in response for response in failed.responses))

    def test_preparation_failure_rolls_back_without_losing_checkpoint(self):
        resurrector = RESURRECTION.Resurrector.__new__(
            RESURRECTION.Resurrector)
        resurrector.state = RESURRECTION.ResurrectorState.RESURRECTION
        resurrector.gcode = FailingPreparationGCode()
        resurrector.mod_params = SimpleNamespace(variables={"chamber_light_mode": "PRINT_ONLY"})
        resurrector.reactor = CooperativeReactor()
        resurrector.file_path = "/missing"
        resurrector._recovery_active = False
        resurrector.virtual_sdcard = VirtualSDRecorder()
        resurrector._legacy_loader = None
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
            lambda state: RECOVERY_STATE.RecoveryGCodeState())
        command = Command()

        resurrector.cmd_RESURRECT(command)

        self.assertTrue(resurrector.virtual_sdcard.cancelled)
        self.assertFalse(resurrector.virtual_sdcard.resumed)
        self.assertEqual(
            resurrector.state, RESURRECTION.ResurrectorState.RESURRECTION)
        self.assertTrue(any(
            "TURN_OFF_HEATERS" in script
            for script in resurrector.gcode.commands))
        self.assertIn("LED_OFF", resurrector.gcode.commands)
        self.assertTrue(any(
            "_CONTEXT_RESET" in script
            for script in resurrector.gcode.commands))
        self.assertTrue(any(
            "preparation failed" in response
            for response in command.responses))
        self.assertFalse(resurrector.get_status(0.)["restored"])

    def test_cleanup_failure_resets_context_and_turns_off_heat_and_fan(self):
        class FailingCleanupGCode(GCodeRecorder):
            def run_script_from_command(self, command):
                self.commands.append(command)
                if "_CONTEXT_STATE NAME=PREPARING" in command:
                    raise RuntimeError("cleanup failed")

        resurrector = RESURRECTION.Resurrector.__new__(
            RESURRECTION.Resurrector)
        resurrector.state = RESURRECTION.ResurrectorState.RESURRECTION
        resurrector.gcode = FailingCleanupGCode()
        resurrector._legacy_loader = None
        resurrector._load_resurrection_state = lambda gcmd, **kwargs: {
            "bed_temp": 60., "extruder_temp": 220.,
        }
        command = Command()

        with tempfile.TemporaryDirectory() as directory:
            checkpoint = pathlib.Path(directory) / "resurrection.json"
            original = b'{"bed_temp":60,"extruder_temp":220}'
            checkpoint.write_bytes(original)
            resurrector.file_path = str(checkpoint)
            resurrector.reactor = CooperativeReactor()
            resurrector.cmd_RESURRECT_ABORT(command)
            self.assertEqual(checkpoint.read_bytes(), original)

        cleanup = "\n".join(resurrector.gcode.commands)
        self.assertIn("_CONTEXT_RESET", cleanup)
        self.assertIn("TURN_OFF_HEATERS", cleanup)
        self.assertIn("M106 P1 S0", cleanup)
        self.assertEqual(
            resurrector.state, RESURRECTION.ResurrectorState.RESURRECTION)
        self.assertTrue(any(
            "cleanup failed" in response for response in command.responses))


    def test_legacy_is_not_imported_by_normal_plugin_import(self):
        code = ("import sys; sys.path.insert(0, " + repr(str(KLIPPER_PATH)) +
                "); import plugins.resurrection; "
                "assert 'plugins.recovery.legacy' not in sys.modules")
        subprocess.run([sys.executable, "-c", code], check=True)

    def test_new_checkpoint_load_does_not_invoke_legacy(self):
        with tempfile.TemporaryDirectory() as directory:
            recovery = RESURRECTION.Resurrector.__new__(RESURRECTION.Resurrector)
            recovery.file_path = os.path.join(directory, "checkpoint.json")
            printer = SimpleNamespace(lookup_object=lambda name, default=None: default)
            captured, _ = RESURRECTION.snapshot.capture(
                printer, move_status(gcode_position=[0., 0., 0., 42.5]),
                toolhead_status(), extruder_status(), None, 0.)
            reference = RESURRECTION.companion.write(
                recovery.file_path, {"mesh": {"active": False}})
            state = dict(version=2, objects_ref=reference,
                         **RESURRECTION.snapshot.encode(captured))
            with mock.patch.object(STATE, "LegacyCheckpointLoader",
                                   side_effect=AssertionError("legacy loader called")):
                result = recovery._load_state(state)
            self.assertEqual(result.logical_e, 42.5)
            self.assertFalse(result.absolute_extrude)
            incomplete = dict(state, z_offset=.2, homing_xy=[0., 0.])
            del incomplete["gcode_offsets"]
            with mock.patch.object(STATE, "LegacyCheckpointLoader") as legacy:
                with self.assertRaises(RESURRECTION.RecoveryParseError):
                    recovery._load_state(incomplete)
                legacy.assert_not_called()
            del state["objects_ref"]
            with self.assertRaises(RESURRECTION.RecoveryParseError):
                recovery._load_state(state)

    def test_invalid_legacy_offset_is_rejected_before_parsing(self):
        recovery = RESURRECTION.Resurrector.__new__(RESURRECTION.Resurrector)
        recovery.reactor = CooperativeReactor()
        for checkpoint in ({}, {"z_offset": None}, {"z_offset": True},
                           {"z_offset": "invalid"}, {"z_offset": float("nan")},
                           {"z_offset": float("inf")}):
            with self.subTest(checkpoint=checkpoint):
                with mock.patch.object(STATE.GCodeStateParser, "parse") as parse:
                    with self.assertRaisesRegex(
                            RESURRECTION.RecoveryParseError, "z_offset"):
                        recovery._load_state(checkpoint)
                    parse.assert_not_called()
                    self.assertIsNone(recovery._legacy_loader)

    def test_legacy_checkpoint_remains_editable_until_runtime_save(self):
        with tempfile.TemporaryDirectory() as directory:
            file = pathlib.Path(directory) / "job.gcode"
            file.write_bytes(b"M83\nG1 E.5 F900\nG92 E0\nG1 E.25\n")
            recovery = RESURRECTION.Resurrector.__new__(RESURRECTION.Resurrector)
            recovery.file_path = str(pathlib.Path(directory) / "checkpoint.json")
            recovery.gcode = GCodeRecorder()
            recovery.reactor = CooperativeReactor()
            recovery.state = RESURRECTION.ResurrectorState.LOADING
            recovery._legacy_loader = None
            checkpoint = dict(file_path=str(file), file_size=file.stat().st_size,
                              file_position=file.stat().st_size,
                              position=[1., 2., 3., 4.], extruder_temp=220.,
                              bed_temp=60., z_offset=0., mesh="auto")
            original = json.dumps(checkpoint).encode()
            pathlib.Path(recovery.file_path).write_bytes(original)
            with mock.patch.object(recovery, "_write_checkpoint",
                                   side_effect=AssertionError("legacy load must not write")):
                migrated = recovery._load_state(checkpoint)
                self.assertEqual(migrated.logical_e, .25)
                self.assertEqual(migrated.gcode_offsets, [0., 0., 0.])
                self.assertEqual(migrated.base_xyz, migrated.gcode_offsets)
                edited = dict(checkpoint, file_position=len(b"M83\nG1 E.5 F900\n"))
                self.assertEqual(recovery._load_state(edited).logical_e, .5)
            self.assertEqual(pathlib.Path(recovery.file_path).read_bytes(), original)

    def test_timer_requests_completed_boundary_without_polling(self):
        recovery = RESURRECTION.Resurrector.__new__(RESURRECTION.Resurrector)
        recovery.state = RESURRECTION.ResurrectorState.PRINTING
        recovery.dump_time = 3.
        recovery.print_stats = SimpleNamespace(get_status=lambda eventtime: {"state": "printing"})
        recovery.start_print_macro = SimpleNamespace(variables={"print_started": True})
        recovery.printer = SimpleNamespace(lookup_object=lambda name, default=None: default)
        sd = VIRTUAL_SD.VirtualSD.__new__(VIRTUAL_SD.VirtualSD)
        sd.boundary_requested = sd.must_pause_work = False
        sd.work_timer = object()
        sd.reactor = CooperativeReactor()
        sd.printer = SimpleNamespace(send_event=lambda name, when:
                                    recovery._handle_command_boundary(when))
        sd.request_command_boundary = mock.Mock(wraps=sd.request_command_boundary)
        recovery.virtual_sdcard = sd
        recovery._dump = mock.Mock()
        recovery._timer = object()
        recovery.reactor = SimpleNamespace(update_timer=mock.Mock())
        mutex = SimpleNamespace(test=lambda: False)

        def completed_line(when):
            sd.reactor.now = when
            if sd.boundary_requested:
                sd._notify_command_boundary(mutex)

        self.assertEqual(recovery._dump_timer_handler(100.), 103.)
        sd.request_command_boundary.assert_called_once_with()
        recovery._dump.assert_not_called()
        completed_line(100.1)
        recovery._dump.assert_called_once_with(100.1)
        self.assertFalse(sd.boundary_requested)
        completed_line(100.2)
        recovery._dump.assert_called_once_with(100.1)
        recovery.reactor.update_timer.assert_not_called()
        self.assertEqual(recovery._dump_timer_handler(103.), 106.)
        completed_line(103.3)
        self.assertEqual(recovery._dump.call_args_list, [
            mock.call(100.1), mock.call(103.3)])
        recovery.reactor.update_timer.assert_not_called()
        recovery.print_stats.get_status = lambda eventtime: {"state": "error"}
        recovery._dump.reset_mock()
        recovery._dump_timer_handler(106.)
        completed_line(106.2)
        recovery._dump.assert_not_called()

    def test_held_sd_does_not_checkpoint_before_printing(self):
        recovery = RESURRECTION.Resurrector.__new__(RESURRECTION.Resurrector)
        recovery.state = RESURRECTION.ResurrectorState.IDLE
        recovery.dump_time = 3.
        recovery.print_stats = SimpleNamespace(get_status=lambda eventtime: {"state": "printing"})
        recovery.start_print_macro = SimpleNamespace(variables={"print_started": True})
        recovery.printer = SimpleNamespace(lookup_object=lambda name, default=None: default)
        recovery.virtual_sdcard = SimpleNamespace(
            is_active=lambda: False, request_command_boundary=mock.Mock())
        recovery._dump = mock.Mock()
        recovery._dump_timer_handler(100.)
        self.assertEqual(recovery.state, RESURRECTION.ResurrectorState.IDLE)
        recovery.virtual_sdcard.request_command_boundary.assert_not_called()
        recovery._dump.assert_not_called()

    def test_new_print_discards_previous_checkpoint_but_recovery_does_not(self):
        with tempfile.TemporaryDirectory() as directory:
            recovery = RESURRECTION.Resurrector.__new__(RESURRECTION.Resurrector)
            recovery.file_path = str(pathlib.Path(directory) / "resurrection.json")
            recovery.state = RESURRECTION.ResurrectorState.RESURRECTION
            recovery._recovery_active = True
            recovery.reactor = CooperativeReactor()
            recovery.gcode = GCodeRecorder()
            recovery.gcode.error = RuntimeError
            recovery.start_print_macro = SimpleNamespace(variables={"print_started": False})
            ref = RESURRECTION.companion.write(recovery.file_path, {"mesh": {"active": False}})
            self.assertTrue(recovery._write_checkpoint({"objects_ref": ref, "file_path": "/old.gcode"}))
            recovery._handle_print_start()
            self.assertTrue(pathlib.Path(recovery.file_path).is_file())
            recovery._recovery_active = False
            recovery._handle_print_start()
            self.assertFalse(pathlib.Path(recovery.file_path).exists())
            self.assertFalse(pathlib.Path(directory, ref).exists())
            self.assertIsNone(recovery._checkpoint_cache)
            self.assertEqual(recovery.state, RESURRECTION.ResurrectorState.IDLE)

    def test_new_print_continues_if_checkpoint_removal_fails(self):
        recovery = RESURRECTION.Resurrector.__new__(RESURRECTION.Resurrector)
        recovery.state = RESURRECTION.ResurrectorState.RESURRECTION
        recovery._recovery_active = False
        recovery.reactor = CooperativeReactor()
        recovery.start_print_macro = SimpleNamespace(variables={"print_started": False})
        recovery._clear = mock.Mock(return_value=OSError("read-only"))
        recovery._handle_print_start()
        recovery._clear.assert_called_once_with(100.)
        self.assertEqual(recovery.state, RESURRECTION.ResurrectorState.IDLE)

    def test_sd_boundary_notifies_after_successful_line_without_block_refill(self):
        for failure in (False, True):
            with self.subTest(failure=failure):
                sd = VIRTUAL_SD.VirtualSD.__new__(VIRTUAL_SD.VirtualSD)
                sd.current_file = io.StringIO("G1 X1\nG1 X2\n")
                sd.file_position = sd.next_file_position = 0
                sd.must_pause_work = sd.cmd_from_sd = False
                sd.boundary_requested = False
                sd.work_timer = object()
                sd.reactor = CooperativeReactor()
                sd.reactor.NOW, sd.reactor.NEVER = 0., 1.e30
                sd.reactor.unregister_timer = lambda timer: None
                sd.print_stats = mock.Mock()
                events = []
                sd.printer = SimpleNamespace(send_event=lambda name, when:
                    events.append((name, sd.file_position, sd.cmd_from_sd)))
                sd.on_error_gcode = SimpleNamespace(render=lambda: "")
                mutex = SimpleNamespace(test=lambda: False)

                def run(line):
                    if line == "G1 X1":
                        self.assertTrue(sd.cmd_from_sd)
                        sd.request_command_boundary()
                        if failure:
                            raise RuntimeError("line failed")
                    elif line == "G1 X2":
                        self.assertIn(("virtual_sdcard:command_boundary", 6, False), events)

                sd.gcode = SimpleNamespace(run_script=run, error=RuntimeError,
                                           get_mutex=lambda: mutex, respond_raw=lambda msg: None)
                sd.work_handler(100.)
                self.assertFalse(sd.boundary_requested)
                if failure:
                    self.assertEqual(sd.file_position, 0)
                    self.assertEqual(events, [])
                else:
                    self.assertEqual(events, [("virtual_sdcard:command_boundary", 6, False)])
                    self.assertEqual(sd.file_position, 12)

    def test_sd_print_start_event_only_for_a_newly_selected_file(self):
        sd = VIRTUAL_SD.VirtualSD.__new__(VIRTUAL_SD.VirtualSD)
        sd.current_file = io.StringIO("G1 X1\n")
        sd.print_stats = SimpleNamespace(state="standby")
        sd.work_timer = None
        sd.gcode = SimpleNamespace(error=RuntimeError)
        events = []
        sd.printer = SimpleNamespace(send_event=lambda event: events.append(event))
        sd.reactor = SimpleNamespace(NOW=0., register_timer=lambda fn, time:
                                    events.append("schedule") or object())
        sd.do_resume()
        self.assertEqual(events, ["virtual_sdcard:print_start", "schedule"])
        for state in ("paused", "error"):
            sd.print_stats.state = state
            sd.work_timer = None
            sd.do_resume()
        self.assertEqual(events, ["virtual_sdcard:print_start", "schedule",
                                  "schedule", "schedule"])
        # Selecting a new file resets PrintStats to standby, including
        # selecting the same path again after an earlier print.
        sd.print_stats.state = "standby"
        sd.work_timer = None
        sd.do_resume()
        self.assertEqual(events[-2:], ["virtual_sdcard:print_start", "schedule"])

    def test_fan_snapshot_preserves_pending_commands(self):
        from tests.gcode_macro_harness import MacroExecution, render_macro

        root = pathlib.Path(__file__).parents[1]
        for model in ("Adventurer5M", "Adventurer5MPro"):
            with self.subTest(model=model):
                config = root / ".cfg/default/printer" / model / "printer.base.cfg"
                pending = []
                variables = {}

                def terminal(command):
                    name, *args = command.split()
                    self.assertEqual(name, "_SET_FAN_SPEED")
                    params = dict(arg.split("=", 1) for arg in args)
                    fan = params["FAN"]
                    speed = float(params.get("SPEED", 0.))
                    pending.append((fan, max(0., min(1., speed))))

                execution = MacroExecution([
                    (root / "macros/base.cfg", "SET_FAN_SPEED", "gcode_macro"),
                ], variables, terminal)

                def run_mcode(name, **params):
                    for command in render_macro(config, name, params=params).commands:
                        execution.run(command)
                objects = {
                    "gcode_macro SET_FAN_SPEED": SimpleNamespace(
                        variables=variables["gcode_macro SET_FAN_SPEED"]),
                    **{"fan_generic " + name: SimpleNamespace(
                        fan=SimpleNamespace(last_fan_value=0., max_power=1.))
                       for name in RESURRECTION.snapshot.FAN_NAMES},
                }
                printer = SimpleNamespace(lookup_object=lambda name, default=None:
                                          objects.get(name, default))
                run_mcode("M106", S=255)
                run_mcode("M106", S=63.75)
                run_mcode("M106", P=2, S=127.5)
                execution.run("SET_FAN_SPEED FAN=external_fan SPEED=1.5")
                execution.run("SET_FAN_SPEED FAN=internal_fan SPEED=-1")
                execution.run("SET_FAN_SPEED FAN=pcb_fan SPEED=1")
                self.assertEqual(len(pending), 5)

                # Only the oldest callback has applied; snapshot must use
                # the completed command's intent, not the current PWM.
                name, value = pending.pop(0)
                objects["fan_generic " + name].fan.last_fan_value = value
                captured, _ = RESURRECTION.snapshot.capture(
                    printer, move_status(), toolhead_status(), extruder_status(), None, 0.)
                decoded = RESURRECTION.snapshot.decode(
                    RESURRECTION.snapshot.encode(captured), {"mesh": {"active": False}})
                self.assertEqual(decoded.fan_speeds, {
                    "fanM106": .25, "chamber_fan": .5,
                    "external_fan": 1., "internal_fan": 0.})

                run_mcode("M107", P=2)
                execution.run("SET_FAN_SPEED FAN=fanM106")
                with self.assertRaises(ValueError):
                    execution.run("SET_FAN_SPEED FAN=external_fan SPEED=invalid")
                captured, _ = RESURRECTION.snapshot.capture(
                    printer, move_status(), toolhead_status(), extruder_status(), None, 0.)
                self.assertEqual(captured.fan_speeds, {
                    "fanM106": 0., "chamber_fan": 0.,
                    "external_fan": 1., "internal_fan": 0.})

    def test_runtime_state_roundtrip_and_validation(self):
        snapshot = RESURRECTION.snapshot
        printer = SimpleNamespace(lookup_object=lambda name, default=None: default)
        captured, excluded = snapshot.capture(
            printer, move_status(speed=1800., speed_factor=.8, extrude_factor=1.05),
            toolhead_status(max_accel=4500.), extruder_status(), None, 0.)
        payload = json.loads(json.dumps(snapshot.encode(captured)))
        static = {"mesh": {"active": False}}
        decoded = snapshot.decode(payload, static)
        self.assertEqual(excluded, [])
        self.assertEqual(decoded.feedrate, 1800.)
        self.assertAlmostEqual(decoded.speed_factor, 80.)
        self.assertAlmostEqual(decoded.extrude_factor, 105.)
        self.assertEqual(decoded.velocity_limits["ACCEL"], 4500.)
        self.assertEqual(decoded.pressure_advance["extruder"]["ADVANCE"], .035)
        self.assertEqual(payload["pressure_advance"], {
            "extruder": {"advance": .035, "smooth_time": .04}})
        self.assertNotIn("objects", payload)
        self.assertNotIn("skew", payload)
        self.assertNotIn("exclusion_transform", payload)
        for key, invalid in (("logical_e", float("nan")),
                             ("absolute_extrude", 1),
                             ("progress_percent", 101),
                             ("gcode_offsets", [0., 0.]),
                             ("gcode_offsets", [0., 0., float("nan")]),
                             ("fans", {"pcb_fan": 0.})):
            with self.subTest(key=key):
                with self.assertRaises(RESURRECTION.RecoveryParseError):
                    snapshot.decode(dict(payload, **{key: invalid}), static)
        for key in ("feedrate", "pressure_advance", "base_xyz", "gcode_offsets"):
            incomplete = dict(payload)
            del incomplete[key]
            with self.assertRaises(RESURRECTION.RecoveryParseError):
                snapshot.decode(incomplete, static)
        with self.assertRaises(RESURRECTION.RecoveryParseError):
            snapshot.decode(payload, {})

    def test_runtime_and_legacy_continue_real_gcode_move_equivalently(self):
        from tests.test_gcode_origin import HomingHarness

        prefix = ("G90\nM82\nG92 E0\nG1 X10 Y20 Z.3 E2 F1200\n"
                  "M220 S80\nM221 S95\nM83\nG1 X15 E.4\n")
        tail = ("G1 X20 E.3\nG91\nG1 X1 Y2 E-.1 F900\n"
                "G90\nM82\nG1 X30 E3.2\n")
        live = HomingHarness()
        live.run(prefix)
        endpoint = list(live.toolhead.position)
        runtime, _ = RESURRECTION.snapshot.capture(
            live.printer, live.move.get_status(), toolhead_status(),
            extruder_status(), None, 0.)
        runtime = RESURRECTION.snapshot.decode(
            RESURRECTION.snapshot.encode(runtime), {"mesh": {"active": False}})
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "job.gcode"
            path.write_text(prefix)
            legacy = STATE.GCodeStateParser(
                str(path), path.stat().st_size, path.stat().st_size,
                threading.Event()).parse()
        live.toolhead.moves.clear()
        live.run(tail)

        for state in (legacy, runtime):
            with self.subTest(state="runtime" if state is runtime else "legacy"):
                restored = HomingHarness()
                for command in ("SET_VELOCITY_LIMIT", "SET_PRESSURE_ADVANCE"):
                    restored.gcode.register_command(command, lambda gcmd: None)
                restored.toolhead.position = endpoint[:3] + [0.]
                restored.move.reset_last_position()
                restored.run("\n".join(state.before_retraction_commands()))
                if state.base_xyz is not None:
                    restored.gcode.register_command(
                        "_SET_GCODE_OFFSET", restored.move.cmd_SET_GCODE_OFFSET)
                    recovery = RESURRECTION.Resurrector.__new__(RESURRECTION.Resurrector)
                    recovery.gcode = restored.gcode
                    recovery.gcode_move = restored.move
                    recovery._restore_coordinate_state(state)
                restored.toolhead.moves.clear()
                restored.run(tail)
                self.assertEqual(len(restored.toolhead.moves), len(live.toolhead.moves))
                for (pos, speed), (expected, expected_speed) in zip(
                        restored.toolhead.moves, live.toolhead.moves):
                    for axis in range(3):
                        self.assertAlmostEqual(pos[axis], expected[axis])
                    self.assertAlmostEqual(pos[3], expected[3] - endpoint[3])
                    self.assertAlmostEqual(speed, expected_speed)
                self.assertAlmostEqual(restored.move.get_status()["gcode_position"].e,
                                       live.move.get_status()["gcode_position"].e)

    def test_restore_coordinate_state_preserves_offsets_and_g92(self):
        from tests.test_gcode_origin import HomingHarness

        for offsets, shifted in product(([0., 0., 0.], [1.25, -2.5, -.055]),
                                        (False, True)):
            with self.subTest(offsets=offsets, shifted=shifted):
                live = HomingHarness()
                live.run("SET_GCODE_OFFSET X=%s Y=%s Z=%s" % tuple(offsets))
                if shifted:
                    live.run("G92 X10 Y20 Z30")
                saved, _ = RESURRECTION.snapshot.capture(
                    live.printer, live.move.get_status(), toolhead_status(),
                    extruder_status(), None, 0.)
                state = RESURRECTION.snapshot.decode(
                    RESURRECTION.snapshot.encode(saved), {"mesh": {"active": False}})
                restored = HomingHarness()
                restored.run("SET_GCODE_OFFSET X=4 Y=5 Z=.25\nG92 X6 Y7 Z8")
                restored.toolhead.position = list(live.toolhead.position)
                restored.move.reset_last_position()
                restored.gcode.register_command(
                    "_SET_GCODE_OFFSET", restored.move.cmd_SET_GCODE_OFFSET)
                recovery = RESURRECTION.Resurrector.__new__(RESURRECTION.Resurrector)
                recovery.gcode = restored.gcode
                recovery.gcode_move = restored.move
                restored.toolhead.moves.clear()
                recovery._restore_coordinate_state(state)
                self.assertEqual(restored.toolhead.moves, [])
                self.assertEqual(restored.move.homing_position[:3], offsets)
                self.assertEqual(restored.move.base_position[:3], live.move.base_position[:3])
                for commands in ("G90\nG1 X12 Y22 Z32 F900",
                                 "SET_GCODE_OFFSET Z_ADJUST=.02 MOVE=1",
                                 "G28 Z\nG1 X15 Y25 Z35 F900"):
                    live.run(commands)
                    restored.run(commands)
                    for actual, expected in zip(restored.toolhead.position,
                                                live.toolhead.position):
                        self.assertAlmostEqual(actual, expected)
                    self.assertEqual(restored.move.homing_position[:3],
                                     live.move.homing_position[:3])
                    self.assertEqual(restored.move.base_position[:3],
                                     live.move.base_position[:3])

    def test_companion_reuses_unchanged_data_and_restores_unsaved_mesh(self):
        companion = RESURRECTION.companion
        mesh = active_mesh()
        source = SimpleNamespace(objects=[{"name": "PART", "polygon": [[0., 0.], [1., 1.]]}])
        skew = SimpleNamespace(xy_factor=.01, xz_factor=0., yz_factor=.02)
        printer = SimpleNamespace(lookup_object=lambda name, default=None:
                                  {"exclude_object": source, "skew_correction": skew}.get(name, default))
        bed_mesh = SimpleNamespace(get_mesh=lambda: mesh)
        key, data = companion.capture(printer, bed_mesh, None)
        self.assertEqual(data["mesh"]["points"][0][0], .0123456789)
        self.assertEqual(data["skew"], [.01, 0., .02])
        self.assertIsNone(companion.capture(printer, bed_mesh, key)[1])
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = str(pathlib.Path(directory) / "resurrection.json")
            reference = companion.write(checkpoint, data)
            loaded = companion.read(checkpoint, reference)
            restored = companion.build_mesh(loaded["mesh"], factory=MeshModel)
            self.assertEqual(restored.probed_matrix, mesh.probed_matrix)
            self.assertEqual(restored.get_mesh_params(), mesh.get_mesh_params())
            self.assertEqual(restored.mesh_offsets, [.25, -.5])
            bed = inactive_bed_mesh("old")
            companion.restore(bed, restored, "unsaved")
            self.assertEqual(bed.pmgr.current_profile, "unsaved")
            bed.set_mesh.assert_called_once_with(restored)
            with self.assertRaises(RESURRECTION.RecoveryParseError):
                companion.read(checkpoint, "../outside.json")
        # In-place offset and skew changes must not be missed by identity caching.
        mesh.mesh_offsets[0] = 1.
        key, changed = companion.capture(printer, bed_mesh, key)
        self.assertEqual(changed["mesh"]["offsets"], [1., -.5])
        skew.xy_factor = .03
        key, changed = companion.capture(printer, bed_mesh, key)
        self.assertEqual(changed["skew"][0], .03)
        source.objects = source.objects + [{"name": "SECOND"}]
        key, changed = companion.capture(printer, bed_mesh, key)
        self.assertEqual(len(changed["objects"]), 2)

    def test_companion_reference_changes_only_after_checkpoint_commit(self):
        companion = RESURRECTION.companion
        with tempfile.TemporaryDirectory() as directory:
            recovery = RESURRECTION.Resurrector.__new__(RESURRECTION.Resurrector)
            recovery.file_path = str(pathlib.Path(directory) / "resurrection.json")
            first = companion.write(recovery.file_path, {"mesh": {"active": False}})
            self.assertTrue(recovery._write_checkpoint({"objects_ref": first}))
            # A failed main checkpoint must retain the previous sidecar.
            second = companion.write(recovery.file_path, {"objects": [{"name": "PART"}]})
            with mock.patch.object(RESURRECTION.os, "replace", side_effect=OSError("write failed")):
                self.assertFalse(recovery._write_checkpoint({"objects_ref": second}))
            self.assertEqual(companion.read(recovery.file_path, first), {"mesh": {"active": False}})
            self.assertEqual(json.loads(pathlib.Path(recovery.file_path).read_text())["objects_ref"], first)
            self.assertTrue(recovery._write_checkpoint({"objects_ref": second}))
            self.assertFalse(pathlib.Path(directory, first).exists())
            self.assertTrue(pathlib.Path(directory, second).exists())
            recovery._clear(0.)
            self.assertFalse(pathlib.Path(directory, second).exists())
            self.assertEqual(list(pathlib.Path(directory).iterdir()), [])

    def test_first_boundary_after_preparation_saves_static_data_only_once(self):
        recovery = RESURRECTION.Resurrector.__new__(RESURRECTION.Resurrector)
        recovery.state = RESURRECTION.ResurrectorState.IDLE
        recovery.dump_time = 3.
        recovery.reactor = CooperativeReactor()
        recovery.start_print_macro = SimpleNamespace(variables={"print_started": False})
        recovery.printer = SimpleNamespace(lookup_object=lambda name, default=None: default)
        recovery.bed_mesh = inactive_bed_mesh()
        recovery.print_stats = SimpleNamespace(get_status=lambda eventtime: {"state": "printing"})
        recovery.virtual_sdcard = SimpleNamespace(
            is_cmd_from_sd=lambda: False, is_active=lambda: True,
            request_command_boundary=mock.Mock())
        recovery.gcode = GCodeRecorder()
        recovery._dump = mock.Mock(side_effect=lambda eventtime: recovery._save_objects())
        with tempfile.TemporaryDirectory() as directory:
            recovery.file_path = str(pathlib.Path(directory) / "resurrection.json")
            recovery._dump_timer_handler(100.)
            recovery._handle_command_boundary(100.)
            recovery._dump.assert_not_called()
            recovery.virtual_sdcard.request_command_boundary.assert_not_called()
            self.assertFalse(list(pathlib.Path(directory).glob("*.json")))
            recovery.start_print_macro.variables["print_started"] = True
            recovery._dump_timer_handler(101.)
            recovery._handle_command_boundary(101.)
            first = recovery._objects_ref
            recovery._dump.assert_called_once_with(101.)
            recovery._dump_timer_handler(104.)
            recovery._handle_command_boundary(104.)
            self.assertEqual(recovery._objects_ref, first)
            self.assertEqual(len(list(pathlib.Path(directory).glob("resurrection.objects.*.json"))), 1)

if __name__ == "__main__":
    unittest.main()
