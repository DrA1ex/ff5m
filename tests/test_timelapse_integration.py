"""Behavioral contracts for optional timelapse capture and rendering."""

## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import asyncio
import configparser
import importlib.metadata
import importlib.util
import pathlib
import shlex
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock
from zipfile import ZipFile

import jinja2

from tests.gcode_macro_harness import MacroActionError, render_macro


ROOT = pathlib.Path(__file__).parents[1]
sys.modules.setdefault("importlib_metadata", importlib.metadata)
sys.path.insert(0, str(ROOT / ".root"))
from moonraker.components.klippy_connection import KlippyConnection

CFG_BACKUP = ROOT / ".py" / "cfg_backup.py"
TIMELAPSE_DATA = ROOT / ".cfg" / "default" / "timelapse.moonraker.conf"
COMPONENT = ROOT / ".root" / "moonraker" / "components" / "timelapse.py"
MACROS = ROOT / "macros" / "timelapse.cfg"
GCODE_PARSER = ROOT / ".py" / "klipper" / "patches" / "gcode.py"

gcode_spec = importlib.util.spec_from_file_location(
    "timelapse_gcode_parser", GCODE_PARSER)
gcode_parser = importlib.util.module_from_spec(gcode_spec)
gcode_spec.loader.exec_module(gcode_parser)

spec = importlib.util.spec_from_file_location(
    "moonraker.components.timelapse", COMPONENT)
timelapse = importlib.util.module_from_spec(spec)
tornado = types.ModuleType("tornado")
ioloop = types.ModuleType("tornado.ioloop")
ioloop.IOLoop = mock.Mock()
with mock.patch.dict(sys.modules, {"tornado": tornado, "tornado.ioloop": ioloop}):
    spec.loader.exec_module(timelapse)


class TimelapseConfigTest(unittest.TestCase):
    def test_layer_capture_uses_selected_interval(self):
        printer = {
            "mod_params": {"variables": {
                "timelapse": True, "timelapse_mode": "LAYER",
                "timelapse_every_layers": 3}},
            "print_stats": {"state": "printing"}}

        first = render_macro(
            MACROS, "_TIMELAPSE_LAYER_CAPTURE", printer=printer,
            params={"LAYER": "1"})
        second = render_macro(
            MACROS, "_TIMELAPSE_LAYER_CAPTURE", printer=printer,
            params={"LAYER": "2"}, variables={"last_layer": 1})
        fourth = render_macro(
            MACROS, "_TIMELAPSE_LAYER_CAPTURE", printer=printer,
            params={"LAYER": "4"}, variables={"last_layer": 2})

        self.assertNotIn("TIMELAPSE_TAKE_FRAME", first.commands)
        self.assertNotIn("TIMELAPSE_TAKE_FRAME", second.commands)
        self.assertIn("TIMELAPSE_TAKE_FRAME", fourth.commands)

        printer["mod_params"]["variables"]["timelapse_every_layers"] = 1
        every_layer = render_macro(
            MACROS, "_TIMELAPSE_LAYER_CAPTURE", printer=printer,
            params={"LAYER": "2"}, variables={"last_layer": 1})
        self.assertIn("TIMELAPSE_TAKE_FRAME", every_layer.commands)

        printer["mod_params"]["variables"]["timelapse_mode"] = "TIME"
        other_mode = render_macro(
            MACROS, "_TIMELAPSE_LAYER_CAPTURE", printer=printer,
            params={"LAYER": "1"})
        self.assertNotIn("TIMELAPSE_TAKE_FRAME", other_mode.commands)

    def test_stock_and_alternative_screen_forward_layer_updates(self):
        variables = {"timelapse": True, "timelapse_mode": "LAYER"}
        printer = {
            "mod_params": {"variables": variables},
            "gcode_macro TIMELAPSE_PRINT": {"enable": True}}
        for config_file in (ROOT / "config" / "stock.cfg",
                            ROOT / "macros" / "client.cfg"):
            with self.subTest(config_file=config_file):
                rendered = render_macro(
                    config_file, "SET_PRINT_STATS_INFO",
                    printer=printer,
                    params={"CURRENT_LAYER": "3"},
                    rawparams="CURRENT_LAYER=3")
                self.assertIn("SET_PRINT_STATS_INFO_BASE CURRENT_LAYER=3",
                              rendered.commands)
                self.assertIn("_TIMELAPSE_LAYER_CAPTURE LAYER=3",
                              rendered.commands)

                variables["timelapse"] = False
                disabled = render_macro(
                    config_file, "SET_PRINT_STATS_INFO", printer=printer,
                    params={"CURRENT_LAYER": "3"},
                    rawparams="CURRENT_LAYER=3")
                self.assertIn("SET_PRINT_STATS_INFO_BASE CURRENT_LAYER=3",
                              disabled.commands)
                self.assertNotIn("_TIMELAPSE_LAYER_CAPTURE LAYER=3",
                                 disabled.commands)
                variables["timelapse"] = True

                printer["gcode_macro TIMELAPSE_PRINT"]["enable"] = False
                skipped = render_macro(
                    config_file, "SET_PRINT_STATS_INFO", printer=printer,
                    params={"CURRENT_LAYER": "3"},
                    rawparams="CURRENT_LAYER=3")
                self.assertNotIn("_TIMELAPSE_LAYER_CAPTURE LAYER=3",
                                 skipped.commands)
                printer["gcode_macro TIMELAPSE_PRINT"]["enable"] = True

    def test_print_override_disables_frames(self):
        printer = {"mod_params": {"variables": {"timelapse": True}}}
        override = render_macro(
            MACROS, "TIMELAPSE_PRINT", printer=printer,
            params={"ENABLE": "0"})
        self.assertIn(
            "SET_GCODE_VARIABLE MACRO=TIMELAPSE_PRINT VARIABLE=enable VALUE=False",
            override.commands)
        with self.assertRaises(MacroActionError):
            render_macro(MACROS, "TIMELAPSE_PRINT", printer=printer,
                         params={"ENABLE": "bad"})

        printer.update({
            "gcode_macro HYPERLAPSE": {"run": False},
            "gcode_macro TIMELAPSE_PRINT": {"enable": False}})
        frame = render_macro(
            MACROS, "TIMELAPSE_TAKE_FRAME", printer=printer,
            variables={"enable": True, "verbose": False})
        self.assertFalse(any("_TIMELAPSE_NEW_FRAME" in line
                             for line in frame.commands))

    def test_time_and_progress_modes_start_the_capture_timer(self):
        variables = {
            "timelapse": True, "timelapse_mode": "TIME",
            "timelapse_every_percent": 0.5}
        printer = {"mod_params": {"variables": variables},
                   "print_stats": {"print_duration": 12.0}}
        timed = render_macro(MACROS, "_TIMELAPSE_SCHEDULE", printer=printer)
        self.assertIn(
            "UPDATE_DELAYED_GCODE ID=_TIMELAPSE_TICK DURATION=1",
            timed.commands)
        self.assertIn(
            "SET_GCODE_VARIABLE MACRO=_TIMELAPSE_SCHEDULE "
            "VARIABLE=last_duration VALUE=12.0", timed.commands)

        variables["timelapse_mode"] = "PERCENT"
        progress = render_macro(MACROS, "_TIMELAPSE_SCHEDULE", printer=printer)
        self.assertIn(
            "UPDATE_DELAYED_GCODE ID=_TIMELAPSE_TICK DURATION=1",
            progress.commands)
        self.assertIn(
            "SET_GCODE_VARIABLE MACRO=_TIMELAPSE_SCHEDULE VARIABLE=next_percent VALUE=0.5",
            progress.commands)

        variables["timelapse_mode"] = "LAYER"
        layers = render_macro(MACROS, "_TIMELAPSE_SCHEDULE", printer=printer)
        self.assertNotIn(
            "UPDATE_DELAYED_GCODE ID=_TIMELAPSE_TICK DURATION=1",
            layers.commands)

    def test_start_print_takes_initial_frame_in_each_mode(self):
        start = {
            "zextruder_temp": 200, "zbed_temp": 60,
            "zforce_kamp": False, "zforce_leveling": False,
            "zskip_leveling": True, "zskip_zoffset": False,
            "zzoffset": 0, "zmesh": "", "ztimelapse": True}
        mod = {
            "timelapse": True, "safe_z": 10, "display": 0,
            "print_leveling": False, "use_kamp": False,
            "bed_mesh_validation": False, "chamber_light_mode": "OFF",
            "check_md5": 0, "clear_cooldown_temp": 150,
            "midi_start": "", "weight_check": False,
            "disable_priming": True}
        printer = {
            "gcode_macro _START_PRINT": start,
            "gcode_macro START_PRINT": {"preparation_done": True},
            "mod_params": {"variables": mod},
            "bed_mesh": {"profiles": {}, "profile_name": "default"}}

        for mode in ("LAYER", "TIME", "PERCENT"):
            with self.subTest(mode=mode):
                mod["timelapse_mode"] = mode
                rendered = render_macro(
                    ROOT / "macros" / "base.cfg", "_START_PRINT",
                    printer=printer)
                self.assertEqual(rendered.commands[-1],
                                 "TIMELAPSE_TAKE_FRAME")
                self.assertEqual(rendered.commands[-2], "M400")
                self.assertIn("_TIMELAPSE_SCHEDULE", rendered.commands)

        mod["disable_priming"] = False
        mod["zclear"] = "PRIME_LINE"
        primed = render_macro(
            ROOT / "macros" / "base.cfg", "_START_PRINT",
            printer=printer)
        self.assertLess(primed.commands.index("PRIME_LINE"),
                        primed.commands.index("TIMELAPSE_TAKE_FRAME"))
        mod["disable_priming"] = True

        start["ztimelapse"] = False
        skipped = render_macro(
            ROOT / "macros" / "base.cfg", "_START_PRINT",
            printer=printer)
        self.assertNotIn("TIMELAPSE_TAKE_FRAME", skipped.commands)
        self.assertIn(
            "SET_GCODE_VARIABLE MACRO=TIMELAPSE_PRINT "
            "VARIABLE=enable VALUE=False", skipped.commands)

        start["ztimelapse"] = True
        mod["timelapse"] = False
        disabled = render_macro(
            ROOT / "macros" / "base.cfg", "_START_PRINT",
            printer=printer)
        self.assertNotIn("TIMELAPSE_TAKE_FRAME", disabled.commands)

    def test_start_print_passes_per_print_timelapse_override(self):
        params = {"EXTRUDER_TEMP": 200, "BED_TEMP": 60,
                  "TIMELAPSE": 0}
        skipped = render_macro(
            ROOT / "config" / "stock.cfg", "START_PRINT", params=params)
        self.assertIn(
            "SET_GCODE_VARIABLE MACRO=_START_PRINT "
            "VARIABLE=ztimelapse VALUE=False", skipped.commands)

        params.pop("TIMELAPSE")
        enabled = render_macro(
            ROOT / "config" / "stock.cfg", "START_PRINT", params=params)
        self.assertIn(
            "SET_GCODE_VARIABLE MACRO=_START_PRINT "
            "VARIABLE=ztimelapse VALUE=True", enabled.commands)

        params["TIMELAPSE"] = 2
        with self.assertRaisesRegex(
                MacroActionError, "TIMELAPSE must be 0 or 1"):
            render_macro(
                ROOT / "config" / "stock.cfg", "START_PRINT",
                params=params)

    def test_time_and_progress_capture_only_at_intervals(self):
        variables = {
            "timelapse": True, "timelapse_mode": "TIME",
            "timelapse_every_seconds": 30,
            "timelapse_every_percent": 0.5}
        printer = {
            "mod_params": {"variables": variables},
            "gcode_macro _TIMELAPSE_SCHEDULE": {
                "last_duration": 10, "next_percent": 0.5},
            "gcode_macro _START_PRINT": {
                "print_started": True, "print_active": True},
            "gcode_macro TIMELAPSE_PRINT": {"enable": True},
            "print_stats": {"state": "printing", "print_duration": 39},
            "virtual_sdcard": {"is_active": True, "progress": 0.004}}
        before = render_macro(
            MACROS, "_TIMELAPSE_TICK", printer=printer,
            section="delayed_gcode")
        self.assertNotIn("TIMELAPSE_TAKE_FRAME", before.commands)

        printer["print_stats"]["print_duration"] = 40
        due = render_macro(
            MACROS, "_TIMELAPSE_TICK", printer=printer,
            section="delayed_gcode")
        self.assertIn("TIMELAPSE_TAKE_FRAME", due.commands)

        variables["timelapse_mode"] = "PERCENT"
        under_threshold = render_macro(
            MACROS, "_TIMELAPSE_TICK", printer=printer,
            section="delayed_gcode")
        self.assertNotIn("TIMELAPSE_TAKE_FRAME", under_threshold.commands)

        printer["virtual_sdcard"]["progress"] = 0.005
        percent = render_macro(
            MACROS, "_TIMELAPSE_TICK", printer=printer,
            section="delayed_gcode")
        self.assertIn("TIMELAPSE_TAKE_FRAME", percent.commands)
        self.assertTrue(any("next_percent VALUE=1.0" in command
                            for command in percent.commands))

        printer["gcode_macro _TIMELAPSE_SCHEDULE"]["next_percent"] = 1.0
        printer["virtual_sdcard"]["progress"] = 0.012
        next_step = render_macro(
            MACROS, "_TIMELAPSE_TICK", printer=printer,
            section="delayed_gcode")
        self.assertIn("TIMELAPSE_TAKE_FRAME", next_step.commands)
        self.assertTrue(any("next_percent VALUE=1.5" in command
                            for command in next_step.commands))

        printer["gcode_macro TIMELAPSE_PRINT"]["enable"] = False
        disabled = render_macro(
            MACROS, "_TIMELAPSE_TICK", printer=printer,
            section="delayed_gcode")
        self.assertNotIn("TIMELAPSE_TAKE_FRAME", disabled.commands)

    def test_parameter_change_restarts_moonraker_only_when_idle(self):
        config = configparser.ConfigParser(interpolation=None, strict=False)
        config.read(ROOT / "macros" / "base.cfg")
        template = jinja2.Environment("{%", "%}", "{", "}").from_string(
            config["mod_params"]["changes_gcode"])
        printer = {"idle_timeout": {"state": "Idle"},
                   "mod_params": {"variables": {
                       "timelapse_mode": "LAYER", "timelapse": True}}}
        changes = {"key": "timelapse", "value": 1, "raw": True}

        idle = template.render(printer=printer, changes=changes)
        self.assertIn("RUN_SHELL_COMMAND CMD=parameter_changed", idle)
        self.assertIn("_TIMELAPSE_SETUP_PROMPT", idle)

        printer["idle_timeout"]["state"] = "Printing"
        busy = template.render(printer=printer, changes=changes)
        self.assertNotIn("RUN_SHELL_COMMAND", busy)
        self.assertNotIn("next Moonraker restart", busy)

        printer["mod_params"]["variables"]["timelapse_mode"] = "TIME"
        timed = template.render(printer=printer, changes=changes)
        self.assertNotIn("_TIMELAPSE_SETUP_PROMPT", timed)

        printer["mod_params"]["variables"]["timelapse_mode"] = "LAYER"
        camera_enabled = template.render(
            printer=printer,
            changes={"key": "camera", "value": 1, "raw": True})
        self.assertIn("_TIMELAPSE_SETUP_PROMPT", camera_enabled)

        prompt = render_macro(MACROS, "_TIMELAPSE_SETUP_PROMPT")
        self.assertTrue(any("action:prompt_begin" in command
                            for command in prompt.commands))

    def test_layer_macro_only_requests_frame_when_enabled(self):
        printer = {
            "mod_params": {"variables": {"timelapse": True}},
            "gcode_macro HYPERLAPSE": {"run": False},
            "gcode_macro TIMELAPSE_PRINT": {"enable": True}}

        disabled = render_macro(MACROS, "TIMELAPSE_TAKE_FRAME", printer=printer)
        enabled = render_macro(
            MACROS, "TIMELAPSE_TAKE_FRAME", printer=printer,
            variables={"enable": True, "verbose": False})

        self.assertFalse(any("_TIMELAPSE_NEW_FRAME" in line
                             for line in disabled.commands))
        self.assertIn("_TIMELAPSE_NEW_FRAME HYPERLAPSE=False", enabled.commands)

    def test_manual_commands_report_disabled_timelapse(self):
        printer = {"mod_params": {"variables": {"timelapse": False}}}
        commands = (
            ("GET_TIMELAPSE_SETUP", {}),
            ("_SET_TIMELAPSE_SETUP", {}),
            ("TIMELAPSE_PRINT", {"ENABLE": "1"}),
            ("TIMELAPSE_TAKE_FRAME", {}),
            ("HYPERLAPSE", {"ACTION": "START"}),
            ("TIMELAPSE_RENDER", {}),
            ("TEST_STREAM_DELAY", {}),
        )
        for name, params in commands:
            with self.subTest(name=name):
                with self.assertRaisesRegex(
                        MacroActionError, "disabled in the mod settings"):
                    render_macro(MACROS, name, printer=printer, params=params)

        stopped = render_macro(
            MACROS, "HYPERLAPSE", printer=printer,
            params={"ACTION": "STOP"})
        self.assertIn(
            "UPDATE_DELAYED_GCODE ID=_HYPERLAPSE_LOOP DURATION=0",
            stopped.commands)

    def test_setup_reports_capture_mode_and_interval(self):
        parser = gcode_parser.GCodeDispatch.__new__(
            gcode_parser.GCodeDispatch)

        def respond_message(commands):
            command = next(line for line in commands
                           if line.startswith("RESPOND "))
            parsed = types.SimpleNamespace(
                get_commandline=lambda: command, _params={})
            return parser._get_extended_params(parsed)._params["MSG"]

        variables = {
            "timelapse": True, "timelapse_mode": "LAYER",
            "timelapse_every_layers": 1,
            "timelapse_every_seconds": 30,
            "timelapse_every_percent": 0.5,
            "timelapse_park": False,
            "timelapse_final_frame": True}
        printer = {
            "mod_params": {"variables": variables},
            "gcode_macro MOVE_SAFE": {"x_max": 110, "y_max": 110},
            "gcode_macro TIMELAPSE_TAKE_FRAME": {
                "park": {"enable": False, "pos": "center"},
                "speed": {}, "extruder": {}, "macro": {}},
            "toolhead": {
                "axis_minimum": {"x": 0, "y": 0},
                "axis_maximum": {"x": 220, "y": 220}},
            "configfile": {"settings": {"printer": {
                "kinematics": "cartesian"}}}}

        for mode, interval in (("LAYER", "1 layer(s)"),
                               ("TIME", "30 seconds"),
                               ("PERCENT", "0.5% progress")):
            with self.subTest(mode=mode):
                variables["timelapse_mode"] = mode
                setup = render_macro(
                    MACROS, "_SET_TIMELAPSE_SETUP", printer=printer,
                    params={"ENABLE": "True"})
                message = respond_message(setup.commands)
                self.assertIn(f"Mode: {mode.lower()}", message)
                self.assertIn(f"every {interval}", message)
                self.assertIn("park: off", message)
                self.assertIn("final frame: on", message)

        variables["timelapse_park"] = True
        variables["timelapse_final_frame"] = False
        parked = render_macro(
            MACROS, "_SET_TIMELAPSE_SETUP", printer=printer,
            params={"ENABLE": "True"})
        parked_message = respond_message(parked.commands)
        self.assertIn("park: X110 Y110", parked_message)
        self.assertIn("final frame: off", parked_message)

    def test_parked_frame_uses_current_move_safe_limits(self):
        variables = {"timelapse": True, "timelapse_park": True}
        printer = {
            "mod_params": {"variables": variables},
            "gcode_macro MOVE_SAFE": {"x_max": 107.0, "y_max": 109.0},
            "gcode_macro HYPERLAPSE": {"run": False},
            "gcode_macro TIMELAPSE_PRINT": {"enable": True},
            "gcode_move": {
                "gcode_position": {"z": 20, "e": 1},
                "absolute_coordinates": True,
                "absolute_extrude": True,
                "speed": 1500,
                "speed_factor": 1.0,
                "extrude_factor": 1.0},
            "toolhead": {
                "axis_maximum": {"z": 230},
                "homed_axes": "xyz", "extruder": "extruder"},
            "extruder": {"can_extrude": True}}
        frame = render_macro(
            MACROS, "TIMELAPSE_TAKE_FRAME", printer=printer,
            variables={"enable": True})
        commands = frame.commands
        retract = commands.index("G0 E-1.0 F900")
        pause = next(i for i, command in enumerate(commands)
                     if command.startswith("PAUSE"))
        lift = commands.index("MOVE_SAFE Z=2 F=3000 ABSOLUTE=0")
        park = next(i for i, command in enumerate(commands)
                    if command.startswith("G0 X107.0 Y109.0"))
        self.assertLess(retract, pause)
        self.assertLess(pause, lift)
        self.assertEqual(commands[lift + 1], "M400")
        self.assertLess(lift + 1, park)
        self.assertNotIn(" Z", commands[park])
        self.assertIn("_TIMELAPSE_NEW_FRAME HYPERLAPSE=False",
                      commands)

        variables["timelapse_park"] = False
        unparked = render_macro(
            MACROS, "TIMELAPSE_TAKE_FRAME", printer=printer,
            variables={"enable": True})
        self.assertFalse(any("G0 X107.0 Y109.0" in command
                             for command in unparked.commands))
        self.assertNotIn("MOVE_SAFE Z=2 F=3000 ABSOLUTE=0",
                         unparked.commands)

        variables["timelapse_park"] = True
        firmware = render_macro(
            MACROS, "TIMELAPSE_TAKE_FRAME", printer=printer,
            variables={"enable": True, "extruder": {"fw_retract": True}})
        self.assertLess(firmware.commands.index("G10"),
                        firmware.commands.index("MOVE_SAFE Z=2 F=3000 ABSOLUTE=0"))

    def test_parked_frame_recovers_if_moonraker_does_not_release_it(self):
        tl = {
            "takingframe": True, "check_time": 0.5, "wait_loops": 0,
            "park": {"time": 0.1}, "macro": {"resume": "RESUME_BASE"},
            "speed": {"travel": 100, "extrude": 15},
            "extruder": {"fw_retract": False, "extrude": 1},
            "restore": {
                "speed": 1500, "e": 0,
                "absolute": {"coordinates": True, "extrude": True},
                "factor": {"speed": 1.0, "extrude": 1.0}}}
        printer = {
            "gcode_macro TIMELAPSE_TAKE_FRAME": tl,
            "gcode_move": {"speed_factor": 1.0, "extrude_factor": 1.0},
            "toolhead": {"extruder": "extruder"},
            "extruder": {"can_extrude": True}}

        waiting = render_macro(
            MACROS, "_WAIT_TIMELAPSE_TAKE_FRAME", printer=printer,
            section="delayed_gcode")
        self.assertTrue(any("UPDATE_DELAYED_GCODE" in command
                            for command in waiting.commands))
        self.assertFalse(any("RESUME_BASE" in command
                             for command in waiting.commands))

        tl["takingframe"] = False
        released = render_macro(
            MACROS, "_WAIT_TIMELAPSE_TAKE_FRAME", printer=printer,
            section="delayed_gcode")
        self.assertTrue(any("RESUME_BASE" in command
                            for command in released.commands))
        resume = next(i for i, command in enumerate(released.commands)
                      if command.startswith("RESUME_BASE"))
        self.assertEqual(released.commands[resume + 2], "G0 E1 F900")
        self.assertFalse(any("VARIABLE=takingframe VALUE=False" in command
                             for command in released.commands))

        tl["extruder"]["fw_retract"] = True
        firmware = render_macro(
            MACROS, "_WAIT_TIMELAPSE_TAKE_FRAME", printer=printer,
            section="delayed_gcode")
        resume = next(i for i, command in enumerate(firmware.commands)
                      if command.startswith("RESUME_BASE"))
        self.assertEqual(firmware.commands[resume + 2], "G11")
        tl["extruder"]["fw_retract"] = False

        tl["takingframe"] = True
        tl["wait_loops"] = 11
        recovered = render_macro(
            MACROS, "_WAIT_TIMELAPSE_TAKE_FRAME", printer=printer,
            section="delayed_gcode")
        self.assertTrue(any("VARIABLE=takingframe VALUE=False" in command
                            for command in recovered.commands))
        self.assertTrue(any("RESUME_BASE" in command
                            for command in recovered.commands))
        self.assertFalse(any("UPDATE_DELAYED_GCODE" in command
                             for command in recovered.commands))

    def test_disabled_timelapse_does_not_continue_hyperlapse_timer(self):
        printer = {
            "mod_params": {"variables": {"timelapse": False}},
            "gcode_macro HYPERLAPSE": {"run": True, "cycle": 30}}
        stopped = render_macro(
            MACROS, "_HYPERLAPSE_LOOP", printer=printer,
            section="delayed_gcode")
        self.assertEqual(stopped.commands, ())

        printer["mod_params"]["variables"]["timelapse"] = True
        running = render_macro(
            MACROS, "_HYPERLAPSE_LOOP", printer=printer,
            section="delayed_gcode")
        self.assertIn("TIMELAPSE_TAKE_FRAME HYPERLAPSE=True",
                      running.commands)

    def test_enabling_and_disabling_preserves_user_moonraker_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            user_config = pathlib.Path(directory) / "user.moonraker.conf"
            custom = ("[authorization]\ntrusted_clients: 192.168.1.0/24\n\n"
                      "[webcam cam]\nstream_url: /my-camera\n")
            user_config.write_text(custom, encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(CFG_BACKUP), "--mode", "restore",
                 "--avoid_writes", "--config", str(user_config),
                 "--data", str(TIMELAPSE_DATA),
                 "--params-string", "[timelapse]"],
                capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            enabled = user_config.read_text(encoding="utf-8")
            self.assertIn(custom.strip(), enabled)
            self.assertEqual(enabled.count("[timelapse]"), 1)
            parsed = configparser.ConfigParser()
            parsed.read_string(enabled)
            self.assertTrue(parsed.getboolean("timelapse", "autorender"))
            self.assertFalse(parsed.getboolean("timelapse", "parkhead"))
            self.assertEqual(parsed["webcam cam"]["stream_url"], "/my-camera")

            result = subprocess.run(
                [sys.executable, str(CFG_BACKUP), "--mode", "restore",
                 "--avoid_writes", "--config", str(user_config),
                 "--data", str(TIMELAPSE_DATA),
                 "--params-string", "[timelapse]"],
                capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(user_config.read_text(encoding="utf-8"), enabled)

            result = subprocess.run(
                [sys.executable, str(CFG_BACKUP), "--mode", "restore",
                 "--avoid_writes", "--no_data", "--config", str(user_config),
                 "--params-string=-[timelapse]"],
                capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            disabled = user_config.read_text(encoding="utf-8")
            self.assertIn(custom.strip(), disabled)
            self.assertNotIn("[timelapse]", disabled)


class TimelapseComponentTest(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_url_post_does_not_change_capture_url(self):
        component = timelapse.Timelapse.__new__(timelapse.Timelapse)
        component.config = {
            "snapshoturl": "http://localhost/snapshot",
            "output_framerate": 30}
        component.database = mock.Mock()
        request = mock.Mock()
        request.get_action.return_value = "POST"
        request.get_args.return_value = {"snapshoturl": "http://other"}

        await component.webrequest_settings(request)
        self.assertEqual(component.config["snapshoturl"],
                         "http://localhost/snapshot")
        component.database.insert_item.assert_not_called()

        request.get_args.return_value = {
            "output_framerate": 24, "snapshoturl": "http://other"}
        request.get_int.return_value = 24
        await component.webrequest_settings(request)
        self.assertEqual(component.config["snapshoturl"],
                         "http://localhost/snapshot")
        component.database.insert_item.assert_called_once_with(
            "timelapse", "config.output_framerate", 24)

    async def test_target_length_must_be_positive(self):
        component = timelapse.Timelapse.__new__(timelapse.Timelapse)
        component.config = {"targetlength": 10}
        component.database = mock.Mock()
        component.server = mock.Mock()
        component.server.error = ValueError
        request = mock.Mock()
        request.get_action.return_value = "POST"
        request.get_args.return_value = {"targetlength": 0}
        request.get_int.return_value = 0

        with self.assertRaises(ValueError):
            await component.webrequest_settings(request)
        self.assertEqual(component.config["targetlength"], 10)
        component.database.insert_item.assert_not_called()

    async def test_silent_internal_gcode_executes_without_console_history(self):
        server = mock.Mock()
        server.error = RuntimeError
        connection = KlippyConnection.__new__(KlippyConnection)
        connection.writer = object()
        connection.closing = False
        connection.server = server
        connection._request_standard = mock.AsyncMock(return_value="ok")
        component = timelapse.Timelapse.__new__(timelapse.Timelapse)
        component.server = server
        component.klippy_apis = mock.Mock()
        component.klippy_apis.klippy = connection

        await component._run_gcode_without_history(
            "_SET_TIMELAPSE_SETUP ENABLE=True")
        server.send_event.assert_not_called()
        request = connection._request_standard.await_args.args[0]
        self.assertEqual(request.get_endpoint(), "gcode/script")
        self.assertEqual(request.get_args()["script"],
                         "_SET_TIMELAPSE_SETUP ENABLE=True")
        self.assertIs(request.get_subscribable(), component.klippy_apis)

    async def test_timelapse_setup_and_hyperlapse_controls_stay_out_of_console(self):
        component = timelapse.Timelapse.__new__(timelapse.Timelapse)
        component.config = {
            "enabled": True, "gcode_verbose": False, "parkhead": False,
            "parkpos": "back_left", "park_custom_pos_x": 10.0,
            "park_custom_pos_y": 10.0, "park_custom_pos_dz": 0.0,
            "park_travel_speed": 100, "park_retract_speed": 15,
            "park_extrude_speed": 15, "park_retract_distance": 1.0,
            "park_extrude_distance": 1.0, "park_time": 0.1,
            "fw_retract": False, "mode": "layermacro",
            "hyperlapse_cycle": 30}
        component.klippy_apis = mock.Mock()
        component.klippy_apis.run_gcode = mock.AsyncMock()
        component._run_gcode_without_history = mock.AsyncMock()
        component.server = mock.Mock()
        component.server.error = RuntimeError

        await component.setgcodevariables()
        await component.start_hyperlapse()
        await component.stop_hyperlapse()

        commands = component._run_gcode_without_history.call_args_list
        self.assertEqual(len(commands), 3)
        self.assertTrue(commands[0].args[0].startswith("_SET_TIMELAPSE_SETUP"))
        self.assertEqual(commands[1].args[0], "HYPERLAPSE ACTION=START CYCLE=30")
        self.assertEqual(commands[2].args[0], "HYPERLAPSE ACTION=STOP")
        component.klippy_apis.run_gcode.assert_not_awaited()
        component.server.send_event.assert_not_called()

    async def test_frame_release_resets_macro_without_console_entry(self):
        component = timelapse.Timelapse.__new__(timelapse.Timelapse)
        component.klippy_apis = mock.Mock()
        component.klippy_apis.run_gcode = mock.AsyncMock()
        component._run_gcode_without_history = mock.AsyncMock()
        component.server = mock.Mock()
        component.server.error = RuntimeError

        await component.release_parkedhead()

        component._run_gcode_without_history.assert_awaited_once_with(
            "SET_GCODE_VARIABLE MACRO=TIMELAPSE_TAKE_FRAME "
            "VARIABLE=takingframe VALUE=False")
        component.klippy_apis.run_gcode.assert_not_awaited()
        component.server.send_event.assert_not_called()

    async def test_render_completion_resets_macro_without_console_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            component = timelapse.Timelapse.__new__(timelapse.Timelapse)
            component.temp_dir = directory + "/"
            component.renderisrunning = False
            component.getWebcamConfig = mock.AsyncMock()
            component.notify_event = mock.Mock()
            component.klippy_apis = mock.Mock()
            component.klippy_apis.run_gcode = mock.AsyncMock()
            component._run_gcode_without_history = mock.AsyncMock()
            component.server = mock.Mock()
            component.server.error = RuntimeError

            result = await component.render(byrendermacro=True)

        self.assertEqual(result["status"], "skipped")
        component._run_gcode_without_history.assert_awaited_once_with(
            "SET_GCODE_VARIABLE MACRO=TIMELAPSE_RENDER "
            "VARIABLE=render VALUE=False")
        component.klippy_apis.run_gcode.assert_not_awaited()

    async def test_invalid_render_settings_release_busy_state_and_macro(self):
        with tempfile.TemporaryDirectory() as directory:
            frame = pathlib.Path(directory) / "frame000001.jpg"
            frame.write_bytes(b"jpeg")
            component = timelapse.Timelapse.__new__(timelapse.Timelapse)
            component.temp_dir = directory + "/"
            component.out_dir = directory + "/"
            component.framecount = 1
            component.renderisrunning = False
            component.saveisrunning = False
            component.ffmpeg_installed = True
            component.config = {
                "time_format_code": "%Y", "duplicatelastframe": 0,
                "variable_fps": True, "targetlength": 0}
            component.getWebcamConfig = mock.AsyncMock()
            component._idle_status_for_render = mock.AsyncMock(return_value={
                "print_stats": {"state": "complete", "filename": "part.gcode"},
                "virtual_sdcard": {"is_active": False}})
            component._has_render_space = mock.Mock(return_value=True)
            component._run_gcode_without_history = mock.AsyncMock()
            component.notify_event = mock.Mock()
            component.server = mock.Mock()

            result = await component.render(byrendermacro=True)

        self.assertEqual(result["status"], "error")
        self.assertFalse(component.renderisrunning)
        component._run_gcode_without_history.assert_awaited_once_with(
            "SET_GCODE_VARIABLE MACRO=TIMELAPSE_RENDER "
            "VARIABLE=render VALUE=False")

    async def test_missing_layer_updates_warn_only_for_layer_recording(self):
        component = timelapse.Timelapse.__new__(timelapse.Timelapse)
        component.framecount = 0
        component.takingframe = False
        component.printing = False
        component.config = {"saveframes": False, "autorender": False}
        component.server = mock.Mock()
        component.server.error = RuntimeError
        component.klippy_apis = mock.Mock()
        status = {
            "mod_params": {"variables": {
                "timelapse": True, "timelapse_mode": "LAYER",
                "timelapse_final_frame": False}},
            "gcode_macro _TIMELAPSE_LAYER_CAPTURE": {"last_layer": 0},
            "gcode_macro TIMELAPSE_PRINT": {"enable": True}}
        component.klippy_apis.query_objects = mock.AsyncMock(
            return_value=status)

        await component._finish_print()
        component.server.send_event.assert_called_once()
        self.assertIn("no layer updates",
                      component.server.send_event.call_args.args[1])

        component.server.send_event.reset_mock()
        status["mod_params"]["variables"]["timelapse_mode"] = "TIME"
        await component._finish_print()
        component.server.send_event.assert_not_called()

        status["mod_params"]["variables"]["timelapse_mode"] = "LAYER"
        status["gcode_macro _TIMELAPSE_LAYER_CAPTURE"]["last_layer"] = 1
        await component._finish_print()
        component.server.send_event.assert_not_called()

    async def test_final_frame_waits_for_capture_and_precedes_render(self):
        events = []
        component = timelapse.Timelapse.__new__(timelapse.Timelapse)
        component.framecount = 2
        component.takingframe = True
        component.printing = False
        component.config = {"saveframes": False, "autorender": True}
        component.server = mock.Mock()
        component.server.error = RuntimeError
        component.klippy_apis = mock.Mock()
        status = {
            "mod_params": {"variables": {
                "timelapse": True, "timelapse_mode": "PERCENT",
                "timelapse_final_frame": True}},
            "gcode_macro _TIMELAPSE_LAYER_CAPTURE": {"last_layer": 0},
            "gcode_macro TIMELAPSE_PRINT": {"enable": True}}
        component.klippy_apis.query_objects = mock.AsyncMock(
            return_value=status)
        component.newframe = mock.AsyncMock(
            side_effect=lambda **_: events.append("frame"))
        component.render = mock.AsyncMock(
            side_effect=lambda: events.append("render"))

        async def finish_pending_capture(_):
            events.append("wait")
            component.takingframe = False

        with mock.patch.object(timelapse.asyncio, "sleep",
                               side_effect=finish_pending_capture):
            await component._finish_print()
        self.assertEqual(events, ["wait", "frame", "render"])
        component.newframe.assert_awaited_once_with(final_frame=True)

        events.clear()
        status["gcode_macro TIMELAPSE_PRINT"]["enable"] = False
        await component._finish_print()
        self.assertEqual(events, ["render"])

        events.clear()
        status["mod_params"]["variables"]["timelapse_final_frame"] = True
        component.printing = True
        await component._finish_print()
        self.assertEqual(events, [])

        events.clear()
        component.printing = False
        status["gcode_macro TIMELAPSE_PRINT"]["enable"] = True
        status["mod_params"]["variables"]["timelapse_final_frame"] = False
        await component._finish_print()
        self.assertEqual(events, ["render"])

    async def test_selecting_a_file_keeps_frames_until_print_starts(self):
        with tempfile.TemporaryDirectory() as directory:
            frame = pathlib.Path(directory) / "frame000001.jpg"
            frame.write_bytes(b"previous print")
            component = timelapse.Timelapse.__new__(timelapse.Timelapse)
            component.temp_dir = directory + "/"
            component.framecount = 1
            component.lastframefile = frame.name
            component.save_cancel = None
            encoder = mock.Mock()
            encoder.cancel = mock.AsyncMock()
            component.render_command = encoder
            component.printing = False
            component.config = {"mode": "layermacro"}

            await component.handle_gcode_response("File selected")
            self.assertTrue(frame.exists())
            encoder.cancel.assert_not_awaited()

            await component.handle_status_update({
                "print_stats": {"state": "cancelled"}})
            self.assertTrue(frame.exists())

            await component.handle_gcode_response("File selected")

            await component.handle_status_update({
                "print_stats": {"state": "printing"}})
            self.assertFalse(frame.exists())
            self.assertEqual(component.framecount, 0)
            encoder.cancel.assert_awaited_once()

            component.render_command = None
            frame.write_bytes(b"current print")
            await component.handle_status_update({
                "print_stats": {"state": "printing"}})
            self.assertTrue(frame.exists())

    async def test_completed_short_print_clears_previous_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            frame = pathlib.Path(directory) / "frame000001.jpg"
            frame.write_bytes(b"previous print")
            component = timelapse.Timelapse.__new__(timelapse.Timelapse)
            component.temp_dir = directory + "/"
            component.framecount = 1
            component.lastframefile = frame.name
            component.save_cancel = None
            component.render_command = None
            component.printing = False
            component.config = {"mode": "layermacro", "enabled": False}

            await component.handle_gcode_response("File selected")
            await component.handle_gcode_response("Done printing file")

            self.assertFalse(frame.exists())
            self.assertFalse(component.printing)

    async def test_capture_commits_only_complete_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            events = []
            component = timelapse.Timelapse.__new__(timelapse.Timelapse)
            component.temp_dir = directory + "/"
            component.framecount = 0
            component.lastframefile = ""
            component.takingframe = True
            component.config = {"snapshoturl": "http://localhost/snapshot?a=1"}
            component.getWebcamConfig = mock.AsyncMock()
            component.notify_event = events.append

            class Command:
                def __init__(self, command, succeed, start_print):
                    self.command = command
                    self.succeed = succeed
                    self.start_print = start_print

                async def run(self, **_kwargs):
                    destination = shlex.split(self.command)[-2]
                    pathlib.Path(destination).write_bytes(b"jpeg")
                    if self.start_print:
                        component.printing = True
                    return self.succeed

            class Shell:
                succeed = False
                start_print = False

                def build_shell_command(self, command, _callback):
                    return Command(command, self.succeed, self.start_print)

            shell = Shell()
            component.server = mock.Mock()
            component.server.lookup_component.return_value = shell

            await component.newframe()
            self.assertEqual(component.framecount, 0)
            self.assertFalse(list(pathlib.Path(directory).iterdir()))
            self.assertEqual(events[-1]["status"], "error")
            self.assertFalse(component.takingframe)

            shell.succeed = True
            await component.newframe()
            self.assertEqual(component.framecount, 1)
            self.assertEqual(events[-1]["status"], "success")
            self.assertEqual(
                (pathlib.Path(directory) / "frame000001.jpg").read_bytes(),
                b"jpeg")

            component.printing = False
            shell.start_print = True
            await component.newframe(final_frame=True)
            self.assertEqual(component.framecount, 1)
            self.assertEqual(events[-1]["status"], "error")
            self.assertFalse((pathlib.Path(directory) / "frame000002.jpg").exists())
            self.assertFalse((pathlib.Path(directory) / "frame000002.jpg.part").exists())

    async def test_render_rejected_while_printing(self):
        with tempfile.TemporaryDirectory() as directory:
            (pathlib.Path(directory) / "frame000001.jpg").write_bytes(b"jpeg")
            events = []
            component = timelapse.Timelapse.__new__(timelapse.Timelapse)
            component.temp_dir = directory + "/"
            component.framecount = 1
            component.renderisrunning = False
            component.saveisrunning = False
            component.ffmpeg_installed = True
            component.getWebcamConfig = mock.AsyncMock()
            component.notify_event = events.append
            component.klippy_apis = mock.Mock()
            component.klippy_apis.query_objects = mock.AsyncMock(return_value={
                "print_stats": {"state": "printing"},
                "virtual_sdcard": {"is_active": True}})

            result = await component.render()

            self.assertEqual(result["status"], "error")
            self.assertFalse(component.renderisrunning)
            self.assertEqual(events[-1]["status"], "error")

    async def test_completed_print_renders_with_bounded_encoder_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            frames = pathlib.Path(directory) / "frames"
            videos = pathlib.Path(directory) / "videos"
            frames.mkdir()
            videos.mkdir()
            (frames / "frame000001.jpg").write_bytes(b"jpeg")
            commands = []
            component = timelapse.Timelapse.__new__(timelapse.Timelapse)
            component.temp_dir = str(frames) + "/"
            component.out_dir = str(videos) + "/"
            component.framecount = 1
            component.ffmpeg_binary_path = "/fake/ffmpeg"
            component.ffmpeg_installed = True
            component.renderisrunning = False
            component.saveisrunning = False
            component.render_command = None
            component.lastcmdreponse = ""
            component.getWebcamConfig = mock.AsyncMock()
            component.notify_event = mock.Mock()
            component.config = {
                "time_format_code": "%Y%m%d",
                "duplicatelastframe": 1,
                "variable_fps": False,
                "output_framerate": 30,
                "rotation": 270,
                "flip_x": False,
                "flip_y": True,
                "constant_rate_factor": 30,
                "pixelformat": "yuv420p",
                "extraoutputparams": "",
                "previewimage": True,
            }
            component.klippy_apis = mock.Mock()
            component.klippy_apis.query_objects = mock.AsyncMock(return_value={
                "print_stats": {"state": "complete", "filename": "part.gcode"},
                "virtual_sdcard": {"is_active": False}})

            class Command:
                def __init__(self, command):
                    self.command = shlex.split(command)

                async def run(self, **_kwargs):
                    commands.append(self.command)
                    output = pathlib.Path(self.command[-2])
                    output.write_bytes(
                        b"video" if output.suffix == ".mp4" else b"preview")
                    return True

            shell = mock.Mock()
            shell.build_shell_command.side_effect = lambda command, *_: Command(command)
            component.server = mock.Mock()
            component.server.lookup_component.return_value = shell

            result = await component.render()

            self.assertEqual(result["status"], "success")
            self.assertTrue((videos / result["filename"]).is_file())
            self.assertEqual(commands[0][commands[0].index("-threads") + 1], "1")
            self.assertIn("-nostats", commands[0])
            self.assertEqual(
                commands[0][commands[0].index("-preset") + 1], "ultrafast")
            self.assertTrue(
                commands[0][commands[0].index("-i") + 1].endswith("frame%06d.jpg"))
            self.assertEqual(commands[0][commands[0].index("-vf") + 1],
                             "transpose=0")
            self.assertEqual(commands[1][commands[1].index("-i") + 1],
                             str(frames / "frame000002.jpg"))
            self.assertNotEqual(commands[1][commands[1].index("-i") + 1],
                                commands[1][-2])
            self.assertEqual(
                (videos / result["previewimage"]).read_bytes(), b"preview")
            self.assertEqual(component.framecount, 1)
            self.assertFalse((frames / "frame000002.jpg").exists())

            second = await component.render()
            self.assertEqual(second["status"], "success")
            self.assertNotEqual(second["filename"], result["filename"])
            self.assertEqual((videos / result["filename"]).read_bytes(), b"video")

    async def test_frame_archive_waits_for_idle_and_is_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            frames = pathlib.Path(directory) / "frames"
            videos = pathlib.Path(directory) / "videos"
            frames.mkdir()
            videos.mkdir()
            (frames / "frame000001.jpg").write_bytes(b"one")
            (frames / "frame000002.jpg").write_bytes(b"two")
            component = timelapse.Timelapse.__new__(timelapse.Timelapse)
            component.temp_dir = str(frames) + "/"
            component.out_dir = str(videos) + "/"
            component.saveisrunning = False
            component.renderisrunning = False
            component.MIN_FREE_BYTES = 0
            component.config = {"time_format_code": "%Y%m%d"}
            component.klippy_apis = mock.Mock()
            component.klippy_apis.query_objects = mock.AsyncMock(return_value={
                "print_stats": {"state": "printing", "filename": "part.gcode"},
                "virtual_sdcard": {"is_active": True}})

            busy = await component.saveFramesZip()
            self.assertEqual(busy["status"], "error")
            self.assertFalse(list(videos.iterdir()))

            component.klippy_apis.query_objects.return_value = {
                "print_stats": {"state": "complete", "filename": "part.gcode"},
                "virtual_sdcard": {"is_active": False}}
            saved = await component.saveFramesZip()
            self.assertEqual(saved["status"], "finished")
            archive_path = videos / saved["zipfile"]
            with ZipFile(archive_path) as archive:
                self.assertEqual(archive.read("frame000001.jpg"), b"one")
                self.assertEqual(archive.read("frame000002.jpg"), b"two")
            self.assertFalse(list(videos.glob("*.part")))
            self.assertFalse(component.saveisrunning)

            previous = archive_path.read_bytes()
            saved_again = await component.saveFramesZip()
            self.assertEqual(saved_again["status"], "finished")
            self.assertNotEqual(saved_again["zipfile"], saved["zipfile"])
            self.assertEqual(archive_path.read_bytes(), previous)

            def interrupted_export(path, _frames, _cancel):
                pathlib.Path(path).write_bytes(b"incomplete")
                raise OSError("disk failure")

            with mock.patch.object(component, "_write_frames_zip",
                                   side_effect=interrupted_export):
                failed = await component.saveFramesZip()
            self.assertEqual(failed["status"], "error")
            self.assertEqual(archive_path.read_bytes(), previous)
            self.assertFalse(list(videos.glob("*.part")))
            self.assertFalse(component.saveisrunning)

    async def test_archive_blocks_concurrent_render_before_status_query_finishes(self):
        with tempfile.TemporaryDirectory() as directory:
            frames = pathlib.Path(directory) / "frames"
            videos = pathlib.Path(directory) / "videos"
            frames.mkdir()
            videos.mkdir()
            (frames / "frame000001.jpg").write_bytes(b"jpeg")
            entered = asyncio.Event()
            proceed = asyncio.Event()

            async def delayed_status():
                entered.set()
                await proceed.wait()
                return {"print_stats": {"state": "complete", "filename": "part.gcode"},
                        "virtual_sdcard": {"is_active": False}}

            component = timelapse.Timelapse.__new__(timelapse.Timelapse)
            component.temp_dir = str(frames) + "/"
            component.out_dir = str(videos) + "/"
            component.MIN_FREE_BYTES = 0
            component.saveisrunning = False
            component.renderisrunning = False
            component.config = {"time_format_code": "%Y%m%d"}
            component._idle_status_for_render = delayed_status
            component.getWebcamConfig = mock.AsyncMock()
            component.notify_event = mock.Mock()

            exporting = asyncio.create_task(component.saveFramesZip())
            await entered.wait()
            self.assertTrue(component.saveisrunning)
            self.assertEqual((await component.saveFramesZip())["status"], "running")
            self.assertEqual((await component.render())["status"], "running")
            proceed.set()
            self.assertEqual((await exporting)["status"], "finished")
            self.assertFalse(component.saveisrunning)

    async def test_new_print_stops_frame_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            frames = pathlib.Path(directory) / "frames"
            videos = pathlib.Path(directory) / "videos"
            frames.mkdir()
            videos.mkdir()
            (frames / "frame000001.jpg").write_bytes(b"jpeg")
            entered = threading.Event()
            proceed = threading.Event()

            def interrupted_export(path, _frames, cancel):
                pathlib.Path(path).write_bytes(b"incomplete")
                entered.set()
                proceed.wait(2)
                if cancel.is_set():
                    raise InterruptedError("new print")

            component = timelapse.Timelapse.__new__(timelapse.Timelapse)
            component.temp_dir = str(frames) + "/"
            component.out_dir = str(videos) + "/"
            component.MIN_FREE_BYTES = 0
            component.saveisrunning = False
            component.renderisrunning = False
            component.render_command = None
            component.pending_file_selected = False
            component.config = {"time_format_code": "%Y%m%d"}
            component.klippy_apis = mock.Mock()
            component.klippy_apis.query_objects = mock.AsyncMock(return_value={
                "print_stats": {"state": "complete", "filename": "part.gcode"},
                "virtual_sdcard": {"is_active": False}})

            with mock.patch.object(component, "_write_frames_zip",
                                   side_effect=interrupted_export):
                exporting = asyncio.create_task(component.saveFramesZip())
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                await component.handle_status_update({
                    "print_stats": {"state": "printing"}})
                proceed.set()
                result = await exporting

            self.assertEqual(result["status"], "skipped")
            self.assertFalse(list(videos.iterdir()))
            self.assertFalse(component.saveisrunning)

            # Cancellation can arrive while the final ZIP entry is being written.
            entered.clear()
            proceed.clear()
            component.printing = False

            def completed_export(path, _frames, _cancel):
                pathlib.Path(path).write_bytes(b"completed")
                entered.set()
                proceed.wait(2)

            with mock.patch.object(component, "_write_frames_zip",
                                   side_effect=completed_export):
                exporting = asyncio.create_task(component.saveFramesZip())
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                await component.handle_status_update({
                    "print_stats": {"state": "printing"}})
                proceed.set()
                result = await exporting

            self.assertEqual(result["status"], "skipped")
            self.assertFalse(list(videos.iterdir()))

    async def test_automatic_archive_and_render_run_in_sequence(self):
        calls = []
        component = timelapse.Timelapse.__new__(timelapse.Timelapse)
        component.config = {"saveframes": True, "autorender": True}
        component.printing = False
        component.server = mock.Mock()
        component.server.error = RuntimeError
        component.klippy_apis = mock.Mock()
        component.klippy_apis.query_objects = mock.AsyncMock(
            return_value={})
        component.saveFramesZip = mock.AsyncMock(
            side_effect=lambda: calls.append("archive"))
        component.render = mock.AsyncMock(
            side_effect=lambda: calls.append("render"))

        await component._finish_print()

        self.assertEqual(calls, ["archive", "render"])

    async def test_new_print_cancels_active_encoder(self):
        component = timelapse.Timelapse.__new__(timelapse.Timelapse)
        component.save_cancel = None
        component.render_command = mock.Mock()
        component.render_command.cancel = mock.AsyncMock()
        component.pending_file_selected = False

        await component.handle_status_update({
            "print_stats": {"state": "printing"}})

        component.render_command.cancel.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
