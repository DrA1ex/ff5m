## Tests for the smart homing G-code origin safeguard.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license.

import contextlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.gcode_macro_harness import render_macro


ROOT = Path(__file__).parents[1]


def load_patch(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GCODE = load_patch("origin_test_gcode", ".py/klipper/patches/gcode.py")
MOVE = load_patch("origin_test_move", ".py/klipper/patches/extras/gcode_move.py")


class Printer:
    config_error = ValueError

    def __init__(self):
        self.handlers = {}
        self.objects = {}

    def register_event_handler(self, event, callback):
        self.handlers.setdefault(event, []).append(callback)

    def send_event(self, event, *args):
        for callback in self.handlers.get(event, ()):
            callback(*args)

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def get_start_args(self):
        return {}

    def get_reactor(self):
        return SimpleNamespace(mutex=contextlib.nullcontext)

    def invoke_shutdown(self, message):
        pytest.fail(message)


class Toolhead:
    def __init__(self, printer, homed_axes):
        self.printer = printer
        self.position = [120., 130., 20., 40.]
        self.homed_axes = homed_axes
        self.moves = []
        self.homes = []
        self.fail_axis = None

    def get_position(self):
        return list(self.position)

    def move(self, position, speed):
        self.position = list(position)
        self.moves.append((list(position), speed))

    def home(self, gcmd):
        axes = [axis for axis in "XYZ" if axis in gcmd.get_command_parameters()]
        for axis in axes or "XYZ":
            if axis == self.fail_axis:
                raise gcmd.error("Homing failed on " + axis)
            index = "XYZ".index(axis)
            self.position[index] = [110., 110., 220.][index]
            self.homes.append(axis)
            self.homed_axes = "".join(sorted(set(self.homed_axes + axis.lower())))
            self.printer.send_event("toolhead:set_position")
            state = SimpleNamespace(get_axes=lambda: [index])
            self.printer.send_event("homing:home_rails_end", state, [])


class HomingHarness:
    """Run the real parser, coordinate commands and rendered G28 macro."""

    def __init__(self, homed_axes="xyz"):
        printer = self.printer = Printer()
        self.toolhead = Toolhead(printer, homed_axes)
        printer.objects["toolhead"] = self.toolhead
        self.gcode = GCODE.GCodeDispatch(printer)
        printer.objects["gcode"] = self.gcode
        self.move = MOVE.GCodeMove(SimpleNamespace(get_printer=lambda: printer))
        self.gcode.register_command("G28.1", self.toolhead.home)
        self.gcode.register_command("G28", self.smart_home)
        for command in ("_CANCEL_DELAYED_COMMANDS", "M400", "RESPOND"):
            self.gcode.register_command(command, lambda gcmd: None)
        printer.send_event("klippy:ready")

    def run(self, commands):
        self.gcode.run_script_from_command(commands)

    def smart_home(self, gcmd):
        rendered = render_macro(
            ROOT / "macros/base.cfg", "G28",
            params=gcmd.get_command_parameters(),
            printer={
                "mod_params": {"variables": {"safe_z": 10}},
                "toolhead": {
                    "homed_axes": self.toolhead.homed_axes,
                    "position": GCODE.Coord(*self.toolhead.position),
                },
            })
        self.run(rendered.text)

    def shifted_state(self):
        self.run("SET_GCODE_OFFSET X=1 Y=2 Z=3\n"
                 "G92 X10 Y20 Z30 E7\nM220 S80\nM221 S90\n"
                 "G1 F1234\nG91\nM83")
        self.toolhead.moves.clear()
        return self.move.get_status()


def assert_preserved(before, after):
    for field in ("homing_origin", "absolute_coordinates", "absolute_extrude",
                  "speed", "speed_factor", "extrude_factor"):
        assert after[field] == before[field]
    assert after["gcode_position"].e == before["gcode_position"].e


@pytest.mark.parametrize("command", ["RESET_GCODE_ORIGIN", "G28", "G28 X Y Z"])
def test_reset_clears_xyz_g92_without_motion_and_preserves_modal_state(command):
    harness = HomingHarness()
    before = harness.shifted_state()
    harness.run(command)
    after = harness.move.get_status()
    assert after["gcode_position"][:3] == (119., 128., 17.)
    assert_preserved(before, after)
    assert harness.toolhead.moves == []
    assert harness.toolhead.homes == []
    harness.run(command)
    assert harness.move.get_status() == after


@pytest.mark.parametrize("command, expected", [
    ("RESET_GCODE_ORIGIN AXES=xz", (119., 20., 17.)),
    ("G28 X", (119., 20., 30.)),
    ("G28 Y", (10., 128., 30.)),
    ("G28 Z", (10., 20., 17.)),
])
def test_partial_reset_preserves_unselected_axis_shifts(command, expected):
    harness = HomingHarness()
    before = harness.shifted_state()
    harness.run(command)
    after = harness.move.get_status()
    assert after["gcode_position"][:3] == expected
    assert_preserved(before, after)
    assert harness.toolhead.homes == harness.toolhead.moves == []


@pytest.mark.parametrize("axes", ['""', "E", "XYZE", "XX", "X,Y", '"X Y"'])
def test_invalid_axis_selection_fails_without_changing_coordinates(axes):
    harness = HomingHarness()
    before = harness.shifted_state()
    with pytest.raises(GCODE.CommandError, match="AXES must"):
        harness.run("RESET_GCODE_ORIGIN AXES=" + axes)
    assert harness.move.get_status() == before
    assert harness.toolhead.homes == harness.toolhead.moves == []


def test_actual_homing_and_skipped_homing_have_the_same_origin_semantics():
    harness = HomingHarness("")
    before = harness.shifted_state()
    harness.run("G28")
    after = harness.move.get_status()
    assert harness.toolhead.homes == ["Z", "X", "Y"]
    assert after["gcode_position"][:3] == (109., 108., 217.)
    assert_preserved(before, after)
    assert harness.toolhead.homed_axes == "xyz"
    harness.run("G28")
    assert harness.move.get_status() == after
    assert harness.toolhead.homes == ["Z", "X", "Y"]


@pytest.mark.parametrize("command, z, homes, expected", [
    ("G28 Y", 0., ["Z", "X", "Y"], (109., 108., 217.)),
    ("G28 X", 0., ["Z", "X"], (109., 20., 217.)),
    ("G28 Y", 20., ["X", "Y"], (109., 108., 30.)),
])
def test_partial_homing_also_resets_axes_homed_for_clearance(command, z, homes, expected):
    harness = HomingHarness("")
    harness.toolhead.position[2] = z
    harness.printer.send_event("toolhead:set_position")
    before = harness.shifted_state()
    harness.run(command)
    after = harness.move.get_status()
    assert harness.toolhead.homes == homes
    assert after["gcode_position"][:3] == expected
    assert_preserved(before, after)


def test_partial_homing_preserves_shifts_on_other_already_homed_axes():
    harness = HomingHarness("xz")
    before = harness.shifted_state()
    harness.run("G28 Y")
    after = harness.move.get_status()
    assert harness.toolhead.homes == ["Y"]
    assert after["gcode_position"][:3] == (10., 108., 30.)
    assert_preserved(before, after)


def test_reset_uses_current_transform_position_without_claiming_axes_homed():
    harness = HomingHarness("")
    harness.shifted_state()
    harness.move.set_move_transform(SimpleNamespace(
        get_position=lambda: [125., 135., 25., 40.],
        move=lambda *args: pytest.fail("origin reset must not move")))
    harness.run("RESET_GCODE_ORIGIN")
    assert harness.move.get_status()["gcode_position"][:3] == (124., 133., 22.)
    assert harness.toolhead.homed_axes == ""


def test_failed_homing_does_not_run_the_final_origin_reset():
    harness = HomingHarness("xz")
    before = harness.shifted_state()
    harness.toolhead.fail_axis = "Y"
    with pytest.raises(GCODE.CommandError, match="Homing failed on Y"):
        harness.run("G28 Y")
    assert harness.move.get_status() == before
    assert harness.toolhead.homed_axes == "xz"
    assert harness.toolhead.homes == []
