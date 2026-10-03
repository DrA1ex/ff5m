"""Behavioral contracts for optional timelapse capture and rendering."""

## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import asyncio
import configparser
import importlib.metadata
import importlib.util
import os
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

from tests.gcode_macro_harness import (
    MacroActionError, MacroExecution, execute_macro_chain, render_macro)


ROOT = pathlib.Path(__file__).parents[1]
sys.modules.setdefault("importlib_metadata", importlib.metadata)
sys.path.insert(0, str(ROOT / ".root"))
from moonraker.components.klippy_connection import KlippyConnection

CFG_BACKUP = ROOT / ".py" / "cfg_backup.py"
TIMELAPSE_DATA = ROOT / ".cfg" / "default" / "timelapse.moonraker.conf"
COMPONENT = ROOT / ".root" / "moonraker" / "components" / "timelapse.py"
MACROS = ROOT / "macros" / "timelapse.cfg"
HEADLESS = ROOT / "macros" / "headless.cfg"
CLIENT = ROOT / "macros" / "client.cfg"
GCODE_PARSER = ROOT / ".py" / "klipper" / "patches" / "gcode.py"
VIRTUAL_SD = ROOT / ".py" / "klipper" / "patches" / "extras" / "virtual_sdcard.py"
SDCARD_CANCEL = ROOT / ".py" / "klipper" / "plugins" / "sdcard_cancel.py"

gcode_spec = importlib.util.spec_from_file_location(
    "timelapse_gcode_parser", GCODE_PARSER)
gcode_parser = importlib.util.module_from_spec(gcode_spec)
gcode_spec.loader.exec_module(gcode_parser)
sd_spec = importlib.util.spec_from_file_location(
    "timelapse_virtual_sdcard", VIRTUAL_SD)
virtual_sdcard = importlib.util.module_from_spec(sd_spec)
sd_spec.loader.exec_module(virtual_sdcard)
cancel_spec = importlib.util.spec_from_file_location(
    "timelapse_sdcard_cancel", SDCARD_CANCEL)
sdcard_cancel = importlib.util.module_from_spec(cancel_spec)
cancel_spec.loader.exec_module(sdcard_cancel)

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
            "pause_resume": {"is_paused": False},
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

        printer["pause_resume"]["is_paused"] = True
        paused = render_macro(
            MACROS, "_TIMELAPSE_LAYER_CAPTURE", printer=printer,
            params={"LAYER": "3"}, variables={"last_layer": 2})
        self.assertEqual(paused.commands, ())
        printer["pause_resume"]["is_paused"] = False

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
                self.assertEqual(rendered.commands[-2:], (
                    "TIMELAPSE_TAKE_FRAME", "_CONTEXT_STATE NAME=PRINTING"))
                self.assertEqual(rendered.commands[-3], "M400")
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
            "pause_resume": {"is_paused": False},
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

        # Pause may already be active while print_stats still says printing.
        printer["pause_resume"] = {"is_paused": True}
        paused = render_macro(
            MACROS, "_TIMELAPSE_TICK", printer=printer,
            section="delayed_gcode")
        self.assertEqual(paused.commands, (
            "UPDATE_DELAYED_GCODE ID=_TIMELAPSE_TICK DURATION=1",))
        printer["pause_resume"]["is_paused"] = False
        resumed = render_macro(
            MACROS, "_TIMELAPSE_TICK", printer=printer,
            section="delayed_gcode")
        self.assertIn("TIMELAPSE_TAKE_FRAME", resumed.commands)

        variables["timelapse_mode"] = "PERCENT"
        under_threshold = render_macro(
            MACROS, "_TIMELAPSE_TICK", printer=printer,
            section="delayed_gcode")
        self.assertNotIn("TIMELAPSE_TAKE_FRAME", under_threshold.commands)

        printer["virtual_sdcard"]["progress"] = 0.005
        printer["pause_resume"]["is_paused"] = True
        paused_percent = render_macro(
            MACROS, "_TIMELAPSE_TICK", printer=printer,
            section="delayed_gcode")
        self.assertEqual(paused_percent.commands, (
            "UPDATE_DELAYED_GCODE ID=_TIMELAPSE_TICK DURATION=1",))
        printer["pause_resume"]["is_paused"] = False
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
        self.assertIn(
            'RESPOND TYPE=command MSG="action:prompt_text '
            'SET_PRINT_STATS_INFO CURRENT_LAYER={layer_num + 1}"',
            prompt.commands)
        self.assertIn(
            'RESPOND TYPE=command MSG="action:prompt_footer_button Got it|'
            'RESPOND TYPE=command MSG=action:prompt_end|primary"',
            prompt.commands)

    def test_layer_macro_only_requests_frame_when_enabled(self):
        printer = {
            "mod_params": {"variables": {"timelapse": True}},
            "gcode_macro HYPERLAPSE": {"run": False},
            "gcode_macro TIMELAPSE_PRINT": {"enable": True},
            "pause_resume": {"is_paused": False}}

        disabled = render_macro(MACROS, "TIMELAPSE_TAKE_FRAME", printer=printer)
        enabled = render_macro(
            MACROS, "TIMELAPSE_TAKE_FRAME", printer=printer,
            variables={"enable": True, "verbose": False})

        self.assertFalse(any("_TIMELAPSE_NEW_FRAME" in line
                             for line in disabled.commands))
        self.assertIn("_TIMELAPSE_NEW_FRAME HYPERLAPSE=False PARKED=False",
                      enabled.commands)

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

    def test_manual_render_leaves_result_messages_to_moonraker(self):
        printer = {
            "mod_params": {"variables": {"timelapse": True}},
            "print_stats": {"state": "complete"},
            "virtual_sdcard": {"is_active": False},
            "configfile": {"settings": {
                "gcode_macro pause": {"rename_existing": "BASE_PAUSE"},
                "gcode_macro resume": {"rename_existing": "BASE_RESUME"}}},
            "gcode_macro TIMELAPSE_RENDER": {
                "render": False, "run_identifier": 0},
        }

        started = render_macro(MACROS, "TIMELAPSE_RENDER", printer=printer)
        finished = render_macro(
            MACROS, "_WAIT_TIMELAPSE_RENDER", printer=printer,
            section="delayed_gcode")

        self.assertEqual(started.remote_calls, (
            ("timelapse_render", {"byrendermacro": "True"}),))
        self.assertEqual(started.info, ())
        self.assertEqual(finished.info, ())
        self.assertFalse(any(command.startswith(("PAUSE", "RESUME"))
                             for command in started.commands + finished.commands))

        printer["print_stats"]["state"] = "paused"
        with self.assertRaisesRegex(MacroActionError, "only after printing stops"):
            render_macro(MACROS, "TIMELAPSE_RENDER", printer=printer)
        printer["print_stats"]["state"] = "complete"
        printer["virtual_sdcard"]["is_active"] = True
        with self.assertRaisesRegex(MacroActionError, "only after printing stops"):
            render_macro(MACROS, "TIMELAPSE_RENDER", printer=printer)

    def test_user_pause_and_runout_during_parked_capture_keep_print_paused(self):
        printer = {
            "gcode_macro TIMELAPSE_TAKE_FRAME": {
                "is_paused": True, "user_pause_requested": False},
            "gcode_macro _START_PRINT": {"print_active": True},
            "gcode_macro _CLIENT_VARIABLE": {},
            "mod_params": {"variables": {
                "pause_z_min": 50, "filament_switch_sensor": True}},
            "toolhead": {"extruder": "extruder"},
            "extruder": {"target": 215},
            "pause_resume": {"is_paused": True},
            "idle_timeout": {"state": "Printing"},
            "filament_switch_sensor e0_sensor": {"filament_detected": False},
        }
        paused = render_macro(CLIENT, "PAUSE", printer=printer)
        self.assertIn(
            "SET_GCODE_VARIABLE MACRO=TIMELAPSE_TAKE_FRAME "
            "VARIABLE=user_pause_requested VALUE=True", paused.commands)
        self.assertNotIn("PAUSE_BASE", paused.commands)

        runout = render_macro(
            HEADLESS, "e0_sensor", printer=printer,
            section="filament_switch_sensor", gcode_option="runout_gcode")
        self.assertIn("PAUSE", runout.commands)
        self.assertTrue(any("Filament runout detected" in command
                            for command in runout.commands))
        printer["gcode_macro TIMELAPSE_TAKE_FRAME"]["user_pause_requested"] = True
        repeated = render_macro(
            HEADLESS, "e0_sensor", printer=printer,
            section="filament_switch_sensor", gcode_option="runout_gcode")
        self.assertEqual(repeated.commands, ())

        tl = printer["gcode_macro TIMELAPSE_TAKE_FRAME"]
        tl.update({
            "takingframe": False, "is_paused": True, "retracted": True,
            "check_time": 0.1, "wait_loops": 0,
            "release_timeout": 5.0, "macro": {"resume": "RESUME_BASE"},
            "speed": {"travel": 100, "extrude": 15},
            "extruder": {"fw_retract": False, "extrude": 1},
            "restore": {
                "speed": 1500, "e": 0,
                "absolute": {"coordinates": True, "extrude": True},
                "factor": {"speed": 1.0, "extrude": 1.0}}})
        printer["print_stats"] = {"state": "paused"}
        printer["gcode_move"] = {
            "speed_factor": 1.0, "extrude_factor": 1.0}
        printer["extruder"]["can_extrude"] = True
        held = render_macro(
            MACROS, "_WAIT_TIMELAPSE_TAKE_FRAME", printer=printer,
            section="delayed_gcode")
        self.assertFalse(any(command.startswith(("RESUME", "M24.1"))
                             for command in held.commands))
        self.assertIn("G0 E1 F900", held.commands)

        resume = render_macro(CLIENT, "RESUME", printer=printer)
        self.assertFalse(any(command.startswith("RESUME_BASE")
                             for command in resume.commands))

        printer["gcode_macro _TIMELAPSE_START_GUARD"] = {
            "waiting": False, "sd_held": True}
        held_pause = render_macro(CLIENT, "PAUSE", printer=printer)
        self.assertIn(
            "SET_GCODE_VARIABLE MACRO=TIMELAPSE_TAKE_FRAME "
            "VARIABLE=user_pause_requested VALUE=True", held_pause.commands)
        printer["gcode_macro TIMELAPSE_TAKE_FRAME"]["is_paused"] = False
        printer["configfile"] = {"settings": {
            "pause_resume": {"recover_velocity": 50}}}
        printer["toolhead"]["homed_axes"] = "xyz"
        printer["gcode_macro _CLIENT_VARIABLE"]["runout_sensor"] = ""
        printer["filament_switch_sensor e0_sensor"]["filament_detected"] = True
        resumed = render_macro(CLIENT, "RESUME", printer=printer)
        self.assertLess(
            resumed.commands.index("RESUME_BASE VELOCITY=50"),
            resumed.commands.index("_TIMELAPSE_START_RELEASE_SD"))

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
            "pause_resume": {"is_paused": False},
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
        self.assertLess(pause, retract)
        self.assertLess(pause, lift)
        self.assertEqual(commands[lift + 1], "M400")
        self.assertLess(lift + 1, park)
        self.assertNotIn(" Z", commands[park])
        self.assertIn("_TIMELAPSE_NEW_FRAME HYPERLAPSE=False PARKED=True",
                      commands)

        variables["timelapse_park"] = False
        unparked = render_macro(
            MACROS, "TIMELAPSE_TAKE_FRAME", printer=printer,
            variables={"enable": True})
        self.assertFalse(any("G0 X107.0 Y109.0" in command
                             for command in unparked.commands))
        self.assertNotIn("MOVE_SAFE Z=2 F=3000 ABSOLUTE=0",
                         unparked.commands)
        self.assertIn("_TIMELAPSE_NEW_FRAME HYPERLAPSE=False PARKED=False",
                      unparked.commands)

        variables["timelapse_park"] = True
        firmware = render_macro(
            MACROS, "TIMELAPSE_TAKE_FRAME", printer=printer,
            variables={"enable": True, "extruder": {"fw_retract": True}})
        self.assertLess(firmware.commands.index("G10"),
                        firmware.commands.index("MOVE_SAFE Z=2 F=3000 ABSOLUTE=0"))

        # A queued frame must not start after a manual pause in either mode.
        printer["pause_resume"] = {"is_paused": True}
        for park_enabled in (True, False):
            variables["timelapse_park"] = park_enabled
            paused = render_macro(
                MACROS, "TIMELAPSE_TAKE_FRAME", printer=printer,
                variables={"enable": True})
            self.assertEqual(paused.commands, ())
            self.assertEqual(paused.remote_calls, ())

    def test_parked_frame_recovers_if_moonraker_does_not_release_it(self):
        tl = {
            "takingframe": True, "is_paused": True, "retracted": True,
            "check_time": 0.1, "wait_loops": 0,
            "release_timeout": 5.0,
            "park": {"time": 0.1}, "macro": {"resume": "RESUME_BASE"},
            "speed": {"travel": 100, "extrude": 15},
            "extruder": {"fw_retract": False, "extrude": 1},
            "restore": {
                "speed": 1500, "e": 0,
                "absolute": {"coordinates": True, "extrude": True},
                "factor": {"speed": 1.0, "extrude": 1.0}}}
        printer = {
            "gcode_macro TIMELAPSE_TAKE_FRAME": tl,
            "print_stats": {"state": "printing"},
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
        self.assertIn("G0 E1 F900", released.commands[:resume])
        self.assertFalse(any("VARIABLE=takingframe VALUE=False" in command
                             for command in released.commands))

        tl["extruder"]["fw_retract"] = True
        firmware = render_macro(
            MACROS, "_WAIT_TIMELAPSE_TAKE_FRAME", printer=printer,
            section="delayed_gcode")
        resume = next(i for i, command in enumerate(firmware.commands)
                      if command.startswith("RESUME_BASE"))
        self.assertIn("G11", firmware.commands[:resume])
        tl["extruder"]["fw_retract"] = False

        tl["takingframe"] = True
        tl["wait_loops"] = 49
        still_waiting = render_macro(
            MACROS, "_WAIT_TIMELAPSE_TAKE_FRAME", printer=printer,
            section="delayed_gcode")
        self.assertTrue(any("UPDATE_DELAYED_GCODE" in command
                            for command in still_waiting.commands))

        tl["wait_loops"] = 50
        recovered = render_macro(
            MACROS, "_WAIT_TIMELAPSE_TAKE_FRAME", printer=printer,
            section="delayed_gcode")
        self.assertTrue(any("VARIABLE=takingframe VALUE=False" in command
                            for command in recovered.commands))
        self.assertTrue(any("RESUME_BASE" in command
                            for command in recovered.commands))
        self.assertFalse(any("UPDATE_DELAYED_GCODE" in command
                             for command in recovered.commands))

        printer["print_stats"]["state"] = "cancelled"
        cancelled = render_macro(
            MACROS, "_WAIT_TIMELAPSE_TAKE_FRAME", printer=printer,
            section="delayed_gcode")
        self.assertFalse(any(command.startswith(("RESUME_BASE", "G0", "G11", "M82", "G92"))
                             for command in cancelled.commands))
        self.assertFalse(any("UPDATE_DELAYED_GCODE" in command
                             for command in cancelled.commands))

    def test_new_frame_remote_flags_are_booleans(self):
        printer = {"gcode_macro TIMELAPSE_TAKE_FRAME": {
            "park": {"enable": False}}}
        for parked, hyperlapse in ((False, False), (True, True)):
            with self.subTest(parked=parked, hyperlapse=hyperlapse):
                frame = render_macro(
                    MACROS, "_TIMELAPSE_NEW_FRAME", printer=printer,
                    params={"HYPERLAPSE": str(hyperlapse),
                            "PARKED": str(parked)})
                self.assertEqual(frame.remote_calls, (
                    ("timelapse_newframe", {
                        "parked": parked, "hyperlapse": hyperlapse}),))

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
    async def test_frame_scheduling_only_acknowledges_parked_capture(self):
        component = timelapse.Timelapse.__new__(timelapse.Timelapse)
        component.config = {
            "enabled": True, "mode": "layermacro",
            "stream_delay_compensation": 0.05}
        component.takingframe = False
        loop = mock.Mock()

        with mock.patch.object(timelapse.IOLoop, "current", return_value=loop):
            component.call_newframe(parked=False, hyperlapse=False)
            loop.call_later.assert_called_once_with(
                delay=0.05, callback=component.newframe,
                release_parked=False, frame_generation=0)
            loop.spawn_callback.assert_not_called()

            loop.reset_mock()
            component.takingframe = False
            component.call_newframe(macropark={"enable": True},
                                    hyperlapse="False")
            loop.call_later.assert_called_once_with(
                delay=0.05, callback=component.newframe,
                release_parked=True, frame_generation=0)

    async def test_scheduled_frame_is_discarded_after_new_print_starts(self):
        with tempfile.TemporaryDirectory() as directory:
            component = timelapse.Timelapse.__new__(timelapse.Timelapse)
            component.config = {"enabled": True, "mode": "layermacro",
                                "stream_delay_compensation": 0.05,
                                "snapshoturl": "http://localhost/snapshot"}
            component.temp_dir = directory + "/"
            component.framecount = 0
            component.lastframefile = ""
            component.frame_generation = 0
            component.takingframe = False
            component.pending_file_selected = True
            component.printing = False
            component.save_cancel = None
            component.render_command = None
            component.server = mock.Mock()
            component.notify_event = mock.Mock()
            loop = mock.Mock()
            with mock.patch.object(timelapse.IOLoop, "current", return_value=loop):
                component.call_newframe()
            scheduled = loop.call_later.call_args.kwargs

            await component._begin_print()
            await scheduled["callback"](
                release_parked=scheduled["release_parked"],
                frame_generation=scheduled["frame_generation"])

            component.server.lookup_component.assert_not_called()
            self.assertEqual(component.framecount, 0)
            self.assertEqual(list(pathlib.Path(directory).iterdir()), [])

    async def test_declined_parked_frame_releases_immediately(self):
        component = timelapse.Timelapse.__new__(timelapse.Timelapse)
        component.config = {
            "enabled": True, "mode": "layermacro",
            "stream_delay_compensation": 0.05}
        component.takingframe = False
        loop = mock.Mock()

        with mock.patch.object(timelapse.IOLoop, "current", return_value=loop):
            for reason in ("disabled", "busy", "wrong mode"):
                with self.subTest(reason=reason):
                    loop.reset_mock()
                    component.config["enabled"] = reason != "disabled"
                    component.config["mode"] = (
                        "hyperlapse" if reason == "wrong mode"
                        else "layermacro")
                    component.takingframe = reason == "busy"
                    component.call_newframe(parked=True, hyperlapse=False)
                    loop.call_later.assert_not_called()
                    loop.spawn_callback.assert_called_once_with(
                        component.release_parkedhead)

            loop.reset_mock()
            component.config["enabled"] = False
            component.call_newframe(parked=False, hyperlapse=False)
            loop.spawn_callback.assert_not_called()

    async def test_snapshot_releases_parked_head_after_success_or_failure(self):
        for parked, fail in ((False, False), (True, False), (True, True)):
            with self.subTest(parked=parked, fail=fail):
                with tempfile.TemporaryDirectory() as directory:
                    events = []
                    component = timelapse.Timelapse.__new__(
                        timelapse.Timelapse)
                    component.temp_dir = directory + "/"
                    component.framecount = 0
                    component.lastframefile = ""
                    component.takingframe = True
                    component.config = {
                        "snapshoturl": "http://localhost/snapshot"}
                    component.notify_event = lambda result: events.append(
                        ("notify", result["status"]))
                    component._run_gcode_without_history = mock.AsyncMock(
                        side_effect=lambda _: events.append("release"))
                    component.server = mock.Mock()
                    component.server.error = RuntimeError

                    def build_command(command, _callback):
                        async def run(**_kwargs):
                            events.append("curl")
                            if fail:
                                raise RuntimeError("snapshot failed")
                            candidate = shlex.split(command)[-2]
                            pathlib.Path(candidate).write_bytes(b"jpeg")
                            return True
                        return types.SimpleNamespace(run=run)

                    component.server.lookup_component.return_value = (
                        types.SimpleNamespace(
                            build_shell_command=build_command))
                    await component.newframe(release_parked=parked)

                    self.assertEqual(events[:2], [
                        "curl", ("notify", "error" if fail else "success")])
                    self.assertEqual(events[2:], ["release"] if parked else [])
                    self.assertFalse(component.takingframe)
                    if parked:
                        component._run_gcode_without_history.assert_awaited_once_with(
                            "SET_GCODE_VARIABLE MACRO=TIMELAPSE_TAKE_FRAME "
                            "VARIABLE=takingframe VALUE=False")
                    else:
                        component._run_gcode_without_history.assert_not_awaited()

    async def test_frame_info_reports_render_activity(self):
        component = timelapse.Timelapse.__new__(timelapse.Timelapse)
        component.framecount = 2
        component.lastframefile = "frame000002.jpg"
        component.renderisrunning = True

        status = await component.webrequest_lastframeinfo(None)

        self.assertEqual(status["framecount"], 2)
        self.assertEqual(status["lastframefile"], "frame000002.jpg")
        self.assertTrue(status["rendering"])

    async def test_frame_info_marks_capture_export_and_finalization_busy(self):
        component = timelapse.Timelapse.__new__(timelapse.Timelapse)
        component.framecount = 0
        component.lastframefile = ""
        component.renderisrunning = False
        component.saveisrunning = False
        component.takingframe = False
        component.finishing_print = False
        for field in ("takingframe", "saveisrunning", "finishing_print",
                      "renderisrunning"):
            setattr(component, field, True)
            self.assertTrue((await component.webrequest_lastframeinfo(None))["busy"])
            setattr(component, field, False)
        self.assertFalse((await component.webrequest_lastframeinfo(None))["busy"])

    async def test_finalization_is_busy_from_print_end_until_it_finishes(self):
        component = timelapse.Timelapse.__new__(timelapse.Timelapse)
        component.config = {"enabled": True, "mode": "layermacro"}
        component.pending_file_selected = False
        component.printing = True
        component.finishing_print = False
        component._finish_print_frames = mock.AsyncMock()
        loop = mock.Mock()
        with mock.patch.object(timelapse.IOLoop, "current", return_value=loop):
            await component.handle_gcode_response("Done printing file")
        self.assertTrue(component.finishing_print)
        callback = loop.spawn_callback.call_args.args[0]
        await callback()
        self.assertFalse(component.finishing_print)

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
        async def final_frame(**_):
            events.append("frame")
            component.takingframe = False

        component.newframe = mock.AsyncMock(side_effect=final_frame)
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

        events.clear()
        component.takingframe = True
        async def finish_without_final_frame(_):
            events.append("wait")
            component.takingframe = False
        with mock.patch.object(timelapse.asyncio, "sleep",
                               side_effect=finish_without_final_frame):
            await component._finish_print()
        self.assertEqual(events, ["wait", "render"])

        events.clear()
        component.takingframe = True
        with mock.patch.object(timelapse.asyncio, "sleep",
                               new=mock.AsyncMock()):
            await component._finish_print()
        self.assertEqual(events, [])
        component.takingframe = False

    async def test_inflight_capture_blocks_manual_export_and_render(self):
        component = timelapse.Timelapse.__new__(timelapse.Timelapse)
        component.takingframe = True
        component.renderisrunning = False
        component.notify_event = mock.Mock()

        exported = await component.saveFramesZip()
        rendered = await component.render()

        self.assertEqual(exported["status"], "running")
        self.assertEqual(rendered["status"], "running")
        component.notify_event.assert_called_once_with(rendered)

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
            component.renderisrunning = False
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

    async def test_capture_from_previous_print_cannot_commit_after_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            component = timelapse.Timelapse.__new__(timelapse.Timelapse)
            component.temp_dir = directory + "/"
            component.framecount = 1
            component.lastframefile = "frame000001.jpg"
            component.frame_generation = 0
            component.takingframe = True
            component.save_cancel = None
            component.render_command = None
            component.pending_file_selected = True
            component.printing = False
            component.config = {"snapshoturl": "http://localhost/snapshot",
                                "mode": "layermacro"}
            component.notify_event = mock.Mock()

            class Command:
                async def run(self, **_kwargs):
                    candidate = shlex.split(self.command)[-2]
                    pathlib.Path(candidate).write_bytes(b"old print")
                    await component._begin_print()
                    return True

            def build(command, _callback):
                capture = Command()
                capture.command = command
                return capture

            component.server = mock.Mock()
            component.server.lookup_component.return_value.build_shell_command = build
            await component.newframe()
            self.assertEqual(component.framecount, 0)
            self.assertEqual(component.lastframefile, "")
            self.assertEqual(list(pathlib.Path(directory).iterdir()), [])
            self.assertEqual(component.notify_event.call_args.args[0]["status"],
                             "error")
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
            component.server = mock.Mock()
            component.klippy_apis = mock.Mock()
            component.klippy_apis.query_objects = mock.AsyncMock(return_value={
                "print_stats": {"state": "printing"},
                "virtual_sdcard": {"is_active": True}})

            result = await component.render()

            self.assertEqual(result["status"], "error")
            self.assertFalse(component.renderisrunning)
            self.assertEqual(events[-1]["status"], "error")
            component.server.send_event.assert_not_called()

            component.klippy_apis.query_objects.return_value = {
                "print_stats": {"state": "cancelled"},
                "virtual_sdcard": {"is_active": True}}
            result = await component.render()

            self.assertEqual(result["status"], "error")
            component.server.send_event.assert_not_called()

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
                    if shell.fail:
                        return False
                    output = pathlib.Path(self.command[-2])
                    output.write_bytes(
                        b"video" if output.suffix == ".mp4" else b"preview")
                    return True

            shell = mock.Mock()
            shell.fail = False
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
            self.assertEqual(
                [call.args for call in component.server.send_event.call_args_list],
                [("server:gcode_response", "// Timelapse: video generation started"),
                 ("server:gcode_response", "// Timelapse: video generation finished")])

            component.klippy_apis.query_objects.return_value = {
                "print_stats": {"state": "cancelled", "filename": "part.gcode"},
                "virtual_sdcard": {"is_active": False}}
            second = await component.render()
            self.assertEqual(second["status"], "success")
            self.assertNotEqual(second["filename"], result["filename"])
            self.assertEqual((videos / result["filename"]).read_bytes(), b"video")

            shell.fail = True
            failed = await component.render()
            self.assertEqual(failed["status"], "error")
            self.assertEqual(
                [call.args[1] for call in component.server.send_event.call_args_list],
                ["// Timelapse: video generation started",
                 "// Timelapse: video generation finished",
                 "// Timelapse: video generation started",
                 "// Timelapse: video generation finished",
                 "// Timelapse: video generation started",
                 "!! Timelapse: video generation failed"])

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
        component.renderisrunning = False
        component.pending_file_selected = False

        await component.handle_status_update({
            "print_stats": {"state": "printing"}})

        component.render_command.cancel.assert_awaited_once()

    async def test_print_status_preserves_render_until_preparation(self):
        component = timelapse.Timelapse.__new__(timelapse.Timelapse)
        component.renderisrunning = True
        component.pending_file_selected = True
        component.printing = False
        component.render_command = mock.Mock()
        component.render_command.cancel = mock.AsyncMock()
        component.save_cancel = None
        component.cleanup = mock.Mock()
        component.config = {"mode": "layermacro"}

        await component.handle_status_update({
            "print_stats": {"state": "printing"}})
        await component.handle_status_update({
            "print_stats": {"state": "paused"}})

        self.assertTrue(component.printing)
        component.render_command.cancel.assert_not_awaited()
        component.cleanup.assert_not_called()

        await component.handle_status_update({
            "gcode_macro _START_PRINT": {"print_active": True}})

        component.render_command.cancel.assert_awaited_once()
        component.cleanup.assert_called_once()

    async def test_actual_new_print_interrupts_previous_finalization(self):
        with tempfile.TemporaryDirectory() as directory:
            frame = pathlib.Path(directory) / "frame000001.jpg"
            frame.write_bytes(b"previous print")
            component = timelapse.Timelapse.__new__(timelapse.Timelapse)
            component.temp_dir = directory + "/"
            component.framecount = 1
            component.lastframefile = frame.name
            component.finishing_print = True
            component.renderisrunning = False
            component.pending_file_selected = True
            component.printing = False
            component.save_cancel = None
            component.render_command = None
            component.config = {"mode": "layermacro"}
            component.frame_generation = 0

            await component.handle_status_update({
                "print_stats": {"state": "paused"}})
            self.assertTrue(frame.exists())
            self.assertFalse(component.printing)
            self.assertTrue(component.finishing_print)

            await component.handle_status_update({
                "print_stats": {"state": "printing"},
                "gcode_macro _START_PRINT": {"print_active": True}})
            self.assertFalse(frame.exists())
            self.assertTrue(component.printing)
            self.assertEqual(component.frame_generation, 1)

            new_frame = pathlib.Path(directory) / "frame000001.jpg"
            new_frame.write_bytes(b"next print")
            await component.handle_status_update({
                "gcode_macro _START_PRINT": {"print_active": True}})
            self.assertEqual(new_frame.read_bytes(), b"next print")

            component._finish_print_frames = mock.AsyncMock()
            await component._finish_print()
            self.assertEqual(new_frame.read_bytes(), b"next print")
            self.assertEqual(component.frame_generation, 1)

    async def test_stale_finish_stops_after_new_print_starts(self):
        component = timelapse.Timelapse.__new__(timelapse.Timelapse)
        component.frame_generation = 0
        component.finishing_print = True
        component.printing = False
        component.config = {"saveframes": True, "autorender": True}
        component.server = mock.Mock(error=RuntimeError)
        pending = asyncio.Event()

        async def query(_objects):
            await pending.wait()
            return {"mod_params": {"variables": {"timelapse": True}}}

        component.klippy_apis = mock.Mock(query_objects=query)
        component.saveFramesZip = mock.AsyncMock()
        component.render = mock.AsyncMock()
        finish = asyncio.create_task(component._finish_print())
        await asyncio.sleep(0)
        component.frame_generation = 1
        component.printing = False
        pending.set()
        await finish
        component.saveFramesZip.assert_not_awaited()
        component.render.assert_not_awaited()

    async def test_interrupted_render_does_not_remove_new_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            component = timelapse.Timelapse.__new__(timelapse.Timelapse)
            component.frame_generation = 0
            component.renderisrunning = False
            component.temp_dir = directory + "/"
            component.notify_event = mock.Mock()
            frame = pathlib.Path(directory) / "frame000001.jpg"

            async def old_render(temporary_paths):
                temporary_paths.append(str(frame))
                component.frame_generation = 1
                frame.write_bytes(b"next print")
                return {"action": "render", "status": "skipped"}

            component._render = old_render
            result = await component.render()
            self.assertEqual(result["status"], "skipped")
            self.assertEqual(frame.read_bytes(), b"next print")

    async def test_waiting_next_print_allows_previous_export_with_previous_name(self):
        component = timelapse.Timelapse.__new__(timelapse.Timelapse)
        component.finishing_print = True
        component.finishing_filename = "previous.gcode"
        component.klippy_apis = mock.Mock()
        component.klippy_apis.query_objects = mock.AsyncMock(return_value={
            "print_stats": {"state": "paused", "filename": "next.gcode"},
            "virtual_sdcard": {"is_active": False},
            "gcode_macro _TIMELAPSE_START_GUARD": {"waiting": True}})

        status = await component._idle_status_for_render()
        self.assertEqual(status["print_stats"]["filename"], "previous.gcode")

        component.klippy_apis.query_objects.return_value[
            "gcode_macro _TIMELAPSE_START_GUARD"]["waiting"] = False
        self.assertIsNone(await component._idle_status_for_render())

    async def test_filename_and_state_updates_bind_name_to_frame_generation(self):
        component = timelapse.Timelapse.__new__(timelapse.Timelapse)
        component.frame_generation = 4
        component.active_print_filename = "previous.gcode"
        component.finishing_print = False
        component.printing = False
        component.pending_file_selected = False
        component.renderisrunning = False
        component.render_command = None
        component.save_cancel = None
        component.config = {"mode": "layermacro", "enabled": True}
        component.cleanup = mock.Mock()

        await component.handle_status_update({
            "print_stats": {"filename": "current.gcode"}})
        await component.handle_status_update({
            "print_stats": {"state": "printing"}})
        self.assertEqual(component.active_print_filename, "current.gcode")

        loop = mock.Mock()
        with mock.patch.object(timelapse.IOLoop, "current", return_value=loop):
            await component.handle_gcode_response("Done printing file")
        self.assertEqual(component.finishing_filename, "current.gcode")
        self.assertEqual(component.finishing_frame_generation, 4)

        await component.handle_status_update({
            "print_stats": {"filename": "next.gcode"}})
        self.assertEqual(component.active_print_filename, "next.gcode")
        self.assertEqual(component.finishing_filename, "current.gcode")

        component.klippy_apis = mock.Mock()
        component.klippy_apis.query_objects = mock.AsyncMock(return_value={
            "print_stats": {"state": "paused", "filename": "next.gcode"},
            "virtual_sdcard": {"is_active": False},
            "gcode_macro _TIMELAPSE_START_GUARD": {"waiting": True}})
        status = await component._idle_status_for_render()
        self.assertEqual(status["print_stats"]["filename"], "current.gcode")

    async def test_cancelling_during_render_wait_keeps_previous_frames(self):
        component = timelapse.Timelapse.__new__(timelapse.Timelapse)
        component.renderisrunning = True
        component.pending_file_selected = True
        component.printing = False
        component.render_command = mock.Mock()
        component.render_command.cancel = mock.AsyncMock()
        component.cleanup = mock.Mock()
        component.stop_hyperlapse = mock.AsyncMock()

        await component.handle_status_update({
            "print_stats": {"state": "printing"}})
        with mock.patch.object(timelapse.IOLoop, "current"):
            await component.handle_status_update({
                "print_stats": {"state": "cancelled"}})

        self.assertFalse(component.printing)
        self.assertFalse(component.pending_file_selected)
        component.render_command.cancel.assert_not_awaited()
        component.cleanup.assert_not_called()

    async def test_moonraker_subscribes_to_print_preparation(self):
        component = timelapse.Timelapse.__new__(timelapse.Timelapse)
        component.klippy_apis = mock.Mock()
        component.klippy_apis.subscribe_objects = mock.AsyncMock()
        component.setgcodevariables = mock.AsyncMock()
        component.stop_hyperlapse = mock.AsyncMock()

        with mock.patch.object(timelapse.IOLoop, "current"):
            await component.handle_klippy_ready()

        component.klippy_apis.subscribe_objects.assert_awaited_once_with({
            "gcode_macro _START_PRINT": ["print_active"]})


class TimelapseStartGuardTest(unittest.TestCase):
    def test_unavailable_status_reports_cause_and_recovery_before_starting(self):
        printer = self._printer(2)
        runtime = MacroExecution(
            [(MACROS, name, "gcode_macro") for name in (
                "_TIMELAPSE_START_DECIDE", "_TIMELAPSE_START_RESET",
                "_TIMELAPSE_START_CONTINUE", "_TIMELAPSE_START_WAIT_CHOICE")]
            + [(MACROS, "_TIMELAPSE_START_POLL", "delayed_gcode")],
            printer, lambda command: None)

        runtime.run("_TIMELAPSE_START_DECIDE")

        prompt = "\n".join(runtime.commands)
        self.assertIn("status is unavailable", prompt)
        self.assertIn("Moonraker", prompt)
        self.assertNotIn("_START_PRINT", runtime.commands)
        self.assertFalse(any("previous timelapse is still saving" in command
                             for command in runtime.commands))
        count = len(runtime.commands)
        runtime.fire("_TIMELAPSE_START_POLL")
        self.assertFalse(any("action:prompt_begin" in command
                             for command in runtime.commands[count:]))
        printer["gcode_shell_command timelapse_render_status"]["returncode"] = 0
        runtime.fire("_TIMELAPSE_START_POLL")
        recovered = "\n".join(runtime.commands[count:])
        self.assertIn("connection restored", recovered)
        self.assertIn("previous timelapse is still saving", recovered)
        self.assertNotIn("_START_PRINT", runtime.commands)
        self.assertEqual(runtime.commands.count("M25.1"), 1)

        printer["gcode_shell_command timelapse_render_status"]["returncode"] = 1
        runtime.fire("_TIMELAPSE_START_POLL")
        self.assertEqual(runtime.commands.count("_START_PRINT"), 1)
        self.assertFalse(printer["gcode_macro _TIMELAPSE_START_GUARD"]["waiting"])
        count = len(runtime.commands)
        runtime.fire("_TIMELAPSE_START_POLL")
        self.assertEqual(len(runtime.commands), count)
        parser = gcode_parser.GCodeDispatch.__new__(gcode_parser.GCodeDispatch)
        for command in runtime.commands:
            if command.startswith("RESPOND "):
                parsed = types.SimpleNamespace(
                    get_commandline=lambda command=command: command, _params={})
                parser._get_extended_params(parsed)

    def test_restored_connection_to_idle_starts_once_without_waiting_for_render(self):
        printer = self._printer(2)
        runtime = MacroExecution(
            [(MACROS, name, "gcode_macro") for name in (
                "_TIMELAPSE_START_DECIDE", "_TIMELAPSE_START_RESET",
                "_TIMELAPSE_START_CONTINUE")]
            + [(MACROS, "_TIMELAPSE_START_POLL", "delayed_gcode")],
            printer, lambda command: None)
        runtime.run("_TIMELAPSE_START_DECIDE")
        count = len(runtime.commands)
        printer["gcode_shell_command timelapse_render_status"]["returncode"] = 1

        runtime.fire("_TIMELAPSE_START_POLL")

        self.assertIn("connection restored. Idle confirmed", "\n".join(runtime.commands[count:]))
        self.assertEqual(runtime.commands.count("_START_PRINT"), 1)
        self.assertEqual(runtime.timers["_TIMELAPSE_START_POLL"], 0)

    def test_resume_explains_unavailable_status_and_keeps_print_held(self):
        printer = self._printer(2, waiting=True)
        printer["gcode_macro _TIMELAPSE_START_GUARD"]["wait_status"] = "unavailable"

        result = render_macro(CLIENT, "RESUME", printer=printer)

        self.assertIn("status is unavailable", "\n".join(result.commands))
        self.assertNotIn("RESUME_BASE", result.commands)
        self.assertNotIn("_TIMELAPSE_START_CONTINUE", result.commands)
        self.assertNotIn("M24.1", result.commands)

    def test_connection_loss_updates_visible_prompt_without_reholding_sd(self):
        printer = self._printer(0)
        runtime = MacroExecution(
            [(MACROS, "_TIMELAPSE_START_DECIDE", "gcode_macro")],
            printer, lambda command: None)
        runtime.run("_TIMELAPSE_START_DECIDE")
        count = len(runtime.commands)
        printer["gcode_shell_command timelapse_render_status"]["returncode"] = 2

        runtime.run("_TIMELAPSE_START_DECIDE")

        changed = "\n".join(runtime.commands[count:])
        self.assertIn("status is unavailable", changed)
        self.assertIn("action:prompt_show", changed)
        self.assertEqual(runtime.commands.count("M25.1"), 1)
        self.assertNotIn("_START_PRINT", runtime.commands)

    def test_wait_choice_explains_unavailability_and_recovery_keeps_it_dismissed(self):
        printer = self._printer(2)
        runtime = MacroExecution(
            [(MACROS, name, "gcode_macro") for name in (
                "_TIMELAPSE_START_DECIDE", "_TIMELAPSE_START_WAIT_CHOICE")],
            printer, lambda command: None)
        runtime.run("_TIMELAPSE_START_DECIDE")
        count = len(runtime.commands)

        runtime.run("_TIMELAPSE_START_WAIT_CHOICE")

        self.assertIn("status is unavailable", "\n".join(runtime.commands[count:]))
        count = len(runtime.commands)
        printer["gcode_shell_command timelapse_render_status"]["returncode"] = 0
        runtime.run("_TIMELAPSE_START_DECIDE")
        self.assertIn("connection restored", "\n".join(runtime.commands[count:]))
        self.assertFalse(any("action:prompt_show" in command
                             for command in runtime.commands[count:]))
        self.assertNotIn("_START_PRINT", runtime.commands)

    @staticmethod
    def _cancel_command(sdcard):
        gcode = mock.Mock()
        printer = mock.Mock()
        printer.lookup_object.side_effect = {
            "gcode": gcode, "virtual_sdcard": sdcard}.__getitem__
        config = mock.Mock()
        config.get_printer.return_value = printer
        command = sdcard_cancel.load_config(config)
        gcode.register_command.assert_called_once_with(
            "SDCARD_CANCEL_FILE", command.cmd_SDCARD_CANCEL_FILE,
            desc=command.cmd_SDCARD_CANCEL_FILE_help)
        return command.cmd_SDCARD_CANCEL_FILE

    @staticmethod
    def _printer(returncode, waiting=False, state="printing",
                 file_path="same.gcode", prompt_open=None):
        return {
            "gcode_macro _TIMELAPSE_START_GUARD": {
                "waiting": waiting,
                "sd_held": waiting,
                "prompt_open": waiting if prompt_open is None else prompt_open},
            "gcode_shell_command timelapse_render_status": {
                "returncode": returncode},
            "print_stats": {"state": state},
            "virtual_sdcard": {"file_path": file_path,
                               "is_active": not waiting},
        }

    def test_render_status_script_distinguishes_busy_idle_and_unknown(self):
        script = ROOT / ".shell" / "commands" / "timelapse_render_status.sh"
        with tempfile.TemporaryDirectory() as temp_dir:
            curl = pathlib.Path(temp_dir) / "curl"
            curl.write_text(
                '#!/bin/sh\nprintf "%s\\n" "$*" > "$TIMELAPSE_CURL_ARGS"\n'
                'printf "%s" "$TIMELAPSE_RESPONSE"\n'
                'exit "${TIMELAPSE_CURL_EXIT:-0}"\n')
            curl.chmod(0o755)
            find = pathlib.Path(temp_dir) / "find"
            find.write_text('#!/bin/sh\nprintf "%s\\n" "$TIMELAPSE_FAKE_CURL"\n')
            find.chmod(0o755)
            env = dict(os.environ, PATH=temp_dir + os.pathsep + os.environ["PATH"],
                       TIMELAPSE_FAKE_CURL=str(curl),
                       TIMELAPSE_CURL_ARGS=str(pathlib.Path(temp_dir) / "args"))

            for response, curl_exit, expected in (
                    ('{"result":{"busy":true}}', "0", 0),
                    ('{"result":{"busy":false}}', "0", 1),
                    ('{"result":{}}', "0", 2),
                    ('', "7", 2)):
                env["TIMELAPSE_RESPONSE"] = response
                env["TIMELAPSE_CURL_EXIT"] = curl_exit
                result = subprocess.run([str(script)], env=env,
                                        capture_output=True, timeout=10)
                self.assertEqual(result.returncode, expected)

            request = pathlib.Path(env["TIMELAPSE_CURL_ARGS"]).read_text()
            self.assertIn("/machine/timelapse/lastframeinfo", request)

    def test_active_render_pauses_before_print_preparation_and_shows_choices(self):
        result = render_macro(MACROS, "_TIMELAPSE_START_DECIDE",
                              printer=self._printer(0))
        self.assertIn("M25.1", result.commands)
        self.assertNotIn("_START_PRINT", result.commands)
        self.assertIn("BEEP", result.commands)
        self.assertTrue(any("WAIT|_TIMELAPSE_START_WAIT_CHOICE" in cmd
                            for cmd in result.commands))
        self.assertTrue(any("CANCEL PRINT|_TIMELAPSE_START_CANCEL" in cmd
                            for cmd in result.commands))
        self.assertTrue(any("CANCEL TIMELAPSE|_TIMELAPSE_START_CONTINUE"
                            in cmd for cmd in result.commands))

    def test_paused_virtual_sd_file_cancels_without_being_resumed(self):
        sd = virtual_sdcard.VirtualSD.__new__(virtual_sdcard.VirtualSD)
        file = mock.Mock()
        sd.current_file = file
        sd.file_position = sd.file_size = 100
        sd.work_timer = None
        sd.cmd_from_sd = False
        sd.reactor = mock.Mock()
        state = {"value": "paused"}
        sd.print_stats = mock.Mock()
        sd.print_stats.note_cancel.side_effect = lambda: state.update(
            value="cancelled")

        self._cancel_command(sd)(mock.Mock())

        self.assertEqual(state["value"], "cancelled")
        file.close.assert_called_once()
        self.assertIsNone(sd.current_file)
        self.assertIsNone(sd.work_timer)
        sd.reactor.register_timer.assert_not_called()

    def test_cancel_file_command_rejects_execution_from_sd(self):
        sd = virtual_sdcard.VirtualSD.__new__(virtual_sdcard.VirtualSD)
        sd.current_file = mock.Mock()
        sd.cmd_from_sd = True
        gcmd = mock.Mock()
        gcmd.error = RuntimeError

        with self.assertRaisesRegex(RuntimeError, "cannot be run from the sdcard"):
            self._cancel_command(sd)(gcmd)

        sd.current_file.close.assert_not_called()

    def test_headless_start_checks_render_for_virtual_sd_print(self):
        printer = {
            "gcode_macro START_PRINT": {
                "feather_force_leveling": None, "feather_mesh_name": None},
            "mod_params": {"variables": {
                "filament_switch_sensor": False, "display": 1,
                "timelapse": True}},
            "bed_mesh": {"profiles": {}},
            "virtual_sdcard": {
                "is_active": True, "file_path": "same.gcode"},
        }
        result = render_macro(HEADLESS, "START_PRINT", printer=printer,
                              params={"EXTRUDER_TEMP": 210, "BED_TEMP": 60})
        self.assertEqual(result.commands[-2:], (
            "RUN_SHELL_COMMAND CMD=timelapse_render_status",
            "_TIMELAPSE_START_DECIDE"))

        printer["mod_params"]["variables"]["timelapse"] = False
        disabled = render_macro(HEADLESS, "START_PRINT", printer=printer,
                                params={"EXTRUDER_TEMP": 210, "BED_TEMP": 60})
        self.assertEqual(disabled.commands[-1], "_START_PRINT")
        self.assertFalse(any("timelapse_render_status" in command
                             for command in disabled.commands))
        printer["mod_params"]["variables"]["timelapse"] = True

        printer["virtual_sdcard"]["is_active"] = False
        result = render_macro(HEADLESS, "START_PRINT", printer=printer,
                              params={"EXTRUDER_TEMP": 210, "BED_TEMP": 60})
        self.assertEqual(result.commands[-1], "_START_PRINT")

        printer["gcode_macro _TIMELAPSE_START_GUARD"] = {"sd_held": True}
        with self.assertRaises(MacroActionError):
            render_macro(HEADLESS, "START_PRINT", printer=printer,
                         params={"EXTRUDER_TEMP": 210, "BED_TEMP": 60})

    def test_only_confirmed_idle_starts_print(self):
        result = render_macro(MACROS, "_TIMELAPSE_START_DECIDE",
                              printer=self._printer(1))
        self.assertEqual(result.commands, ("_START_PRINT",))

        for status in (2, None):
            result = render_macro(MACROS, "_TIMELAPSE_START_DECIDE",
                                  printer=self._printer(status))
            self.assertIn("M25.1", result.commands)
            self.assertNotIn("_START_PRINT", result.commands)
            waiting = render_macro(MACROS, "_TIMELAPSE_START_DECIDE",
                                   printer=self._printer(status, waiting=True))
            self.assertNotIn("_TIMELAPSE_START_CONTINUE", waiting.commands)

        result = render_macro(MACROS, "_TIMELAPSE_START_DECIDE",
                              printer=self._printer(1, waiting=True))
        self.assertEqual(result.commands, ("_TIMELAPSE_START_CONTINUE",))

    def test_disabling_timelapse_releases_an_existing_wait(self):
        printer = self._printer(2, waiting=True, state="paused")
        printer["mod_params"] = {"variables": {"timelapse": False}}
        result = render_macro(MACROS, "_TIMELAPSE_START_DECIDE",
                              printer=printer)
        self.assertEqual(result.commands, ("_TIMELAPSE_START_CONTINUE",))

    def test_held_virtual_sd_resumes_after_preparation_or_closes_on_cancel(self):
        printer = self._printer(1, waiting=True, state="paused")
        printer["gcode_macro _TIMELAPSE_START_GUARD"]["sd_held"] = True
        printer["virtual_sdcard"].update(is_active=False)

        continue_print = render_macro(
            MACROS, "_TIMELAPSE_START_CONTINUE", printer=printer)
        self.assertEqual(continue_print.commands, (
            "_TIMELAPSE_START_RESET", "_START_PRINT",
            "_TIMELAPSE_START_RELEASE_SD"))

        printer["gcode_macro _TIMELAPSE_START_GUARD"]["waiting"] = False
        printer.update({
            "gcode_macro _CLIENT_VARIABLE": {"user_cancel_macro": ""},
            "gcode_macro RESUME": {"restore_idle_timeout": 0},
            "mod_params": {"variables": {"park_dz": 10}},
            "pause_resume": {"is_paused": False}})
        cancel = render_macro(CLIENT, "CANCEL_PRINT", printer=printer)
        self.assertEqual(cancel.commands[-3:], (
            "SDCARD_CANCEL_FILE",
            "SET_GCODE_VARIABLE MACRO=_TIMELAPSE_START_GUARD "
            "VARIABLE=sd_held VALUE=False",
            "CANCEL_PRINT_BASE"))
        self.assertNotIn("M24.1", cancel.commands)

        release = render_macro(
            MACROS, "_TIMELAPSE_START_RELEASE_SD", printer=printer)
        self.assertEqual(release.commands, (
            "M24.1",
            "SET_GCODE_VARIABLE MACRO=_TIMELAPSE_START_GUARD "
            "VARIABLE=sd_held VALUE=False"))

        sd = virtual_sdcard.VirtualSD.__new__(virtual_sdcard.VirtualSD)
        sd.current_file = mock.Mock()
        sd.file_position = sd.file_size = 100
        sd.work_timer = None
        sd.cmd_from_sd = False
        sd.must_pause_work = True
        sd.reactor = mock.Mock()
        sd.reactor.NOW = 0
        sd.reactor.monotonic.return_value = 1.0
        sd.reactor.register_timer.return_value = object()
        sd.reactor.pause.side_effect = lambda _until: setattr(
            sd, "work_timer", None)
        sd.gcode = mock.Mock()
        sd.print_stats = mock.Mock()
        for command in cancel.commands[-3:]:
            if command == "SDCARD_CANCEL_FILE":
                self._cancel_command(sd)(mock.Mock())
        self.assertIsNone(sd.current_file)
        sd.print_stats.note_cancel.assert_called_once()
        sd.reactor.register_timer.assert_not_called()

        printer["virtual_sdcard"]["file_path"] = None
        missing_file = render_macro(
            MACROS, "_TIMELAPSE_START_RELEASE_SD", printer=printer)
        self.assertNotIn("M24.1", missing_file.commands)
        self.assertTrue(any("VARIABLE=sd_held VALUE=False" in command
                            for command in missing_file.commands))

        printer["virtual_sdcard"].update(file_path="same.gcode", is_active=True)
        already_active = render_macro(
            MACROS, "_TIMELAPSE_START_RELEASE_SD", printer=printer)
        self.assertNotIn("M24.1", already_active.commands)

    def test_held_sd_waits_for_parked_frame_and_head_restore(self):
        printer = self._printer(1, waiting=False, state="paused")
        printer["gcode_macro _TIMELAPSE_START_GUARD"]["sd_held"] = True
        printer["virtual_sdcard"]["is_active"] = False
        printer["gcode_macro TIMELAPSE_TAKE_FRAME"] = {"is_paused": True}
        held = render_macro(MACROS, "_TIMELAPSE_START_RELEASE_SD",
                            printer=printer)
        self.assertEqual(held.commands, ())

        printer.update({
            "mod_params": {"variables": {
                "timelapse": True, "timelapse_park": True}},
            "gcode_macro MOVE_SAFE": {"x_max": 110, "y_max": 110},
            "gcode_macro HYPERLAPSE": {"run": False},
            "gcode_macro TIMELAPSE_PRINT": {"enable": True},
            "pause_resume": {"is_paused": False},
            "gcode_move": {
                "gcode_position": {"z": 20, "e": 1},
                "absolute_coordinates": True, "absolute_extrude": True,
                "speed": 1500, "speed_factor": 1.0,
                "extrude_factor": 1.0},
            "toolhead": {"axis_maximum": {"z": 230},
                         "homed_axes": "xyz", "extruder": "extruder"},
            "extruder": {"can_extrude": True}})
        captured = render_macro(MACROS, "TIMELAPSE_TAKE_FRAME",
                                printer=printer,
                                variables={"enable": True})
        self.assertIn("PAUSE_BASE", captured.commands)

        tl = {
            "takingframe": False, "is_paused": True, "retracted": True,
            "check_time": 0.1, "wait_loops": 0,
            "release_timeout": 5.0,
            "macro": {"resume": "RESUME_BASE"},
            "speed": {"travel": 100, "extrude": 15},
            "extruder": {"fw_retract": False, "extrude": 1},
            "restore": {
                "speed": 1500, "e": 1,
                "absolute": {"coordinates": True, "extrude": True},
                "factor": {"speed": 1.0, "extrude": 1.0}}}
        printer["gcode_macro TIMELAPSE_TAKE_FRAME"] = tl
        restored = render_macro(MACROS, "_WAIT_TIMELAPSE_TAKE_FRAME",
                                printer=printer, section="delayed_gcode")
        self.assertIn("RESUME_BASE VELOCITY=100", restored.commands)
        self.assertEqual(restored.commands[-1], "_TIMELAPSE_START_RELEASE_SD")
        self.assertLess(restored.commands.index("G0 E1 F900"),
                        restored.commands.index("_TIMELAPSE_START_RELEASE_SD"))

        forced = render_macro(MACROS, "_TIMELAPSE_START_RELEASE_SD",
                              printer=printer, params={"FORCE": 1})
        self.assertIn("M24.1", forced.commands)

        printer.update({
            "gcode_macro _CLIENT_VARIABLE": {"user_cancel_macro": ""},
            "gcode_macro RESUME": {"restore_idle_timeout": 0},
            "pause_resume": {"is_paused": True}})
        printer["mod_params"]["variables"]["park_dz"] = 10
        printer["gcode_macro TIMELAPSE_TAKE_FRAME"]["is_paused"] = True
        cancelled = execute_macro_chain((
            (CLIENT, "CANCEL_PRINT"),
            (MACROS, "_TIMELAPSE_START_RELEASE_SD")),
            "CANCEL_PRINT", printer=printer)
        self.assertLess(cancelled.index("SDCARD_CANCEL_FILE"),
                        cancelled.index("CANCEL_PRINT_BASE"))
        self.assertNotIn("M24.1", cancelled)

        printer["print_stats"]["state"] = "cancelled"
        late_capture = render_macro(MACROS, "_WAIT_TIMELAPSE_TAKE_FRAME",
                                    printer=printer, section="delayed_gcode")
        self.assertNotIn("RESUME_BASE VELOCITY=100", late_capture.commands)
        self.assertNotIn("_TIMELAPSE_START_RELEASE_SD", late_capture.commands)

    def test_held_virtual_sd_rejects_unrelated_pause_and_resume(self):
        printer = self._printer(1, waiting=False, state="paused")
        printer["gcode_macro _TIMELAPSE_START_GUARD"]["sd_held"] = True
        with self.assertRaises(MacroActionError):
            render_macro(CLIENT, "PAUSE", printer=printer)
        resume = render_macro(CLIENT, "RESUME", printer=printer)
        self.assertFalse(any(command == "RESUME_BASE" or command == "M24.1"
                             for command in resume.commands))

    def test_cancelled_print_cannot_reopen_prompt_with_file_still_loaded(self):
        result = render_macro(MACROS, "_TIMELAPSE_START_DECIDE",
                              printer=self._printer(
                                  0, state="cancelled", file_path="same.gcode"))
        self.assertEqual(result.commands, ())

        result = render_macro(MACROS, "_TIMELAPSE_START_DECIDE",
                              printer=self._printer(
                                  0, waiting=True, state="cancelled"))
        self.assertEqual(result.commands, ("_TIMELAPSE_START_RESET",))

    def test_paused_print_can_keep_waiting_and_resume(self):
        paused = self._printer(0, waiting=True, state="paused")
        result = render_macro(MACROS, "_TIMELAPSE_START_DECIDE",
                              printer=paused)
        self.assertEqual(result.commands, (
            "UPDATE_DELAYED_GCODE ID=_TIMELAPSE_START_POLL DURATION=2",))

        result = render_macro(MACROS, "_TIMELAPSE_START_CONTINUE",
                              printer=paused)
        self.assertEqual(result.commands,
                         ("_TIMELAPSE_START_RESET", "_START_PRINT",
                          "_TIMELAPSE_START_RELEASE_SD"))

    def test_standard_resume_waits_and_cancel_stops_held_file(self):
        waiting = self._printer(2, waiting=True, state="paused")
        resume = render_macro(CLIENT, "RESUME", printer=waiting)
        cancel_printer = {
            **waiting,
            "gcode_macro _CLIENT_VARIABLE": {
                "user_cancel_macro": "", "park_at_cancel": True},
            "gcode_macro RESUME": {"restore_idle_timeout": 0},
            "mod_params": {"variables": {"park_dz": 10}},
            "pause_resume": {"is_paused": False}}
        cancel = render_macro(CLIENT, "CANCEL_PRINT", printer=cancel_printer)
        self.assertNotIn("_TIMELAPSE_START_CONTINUE", resume.commands)
        self.assertTrue(any("CANCEL TIMELAPSE" in command
                            for command in resume.commands))
        parser = gcode_parser.GCodeDispatch.__new__(
            gcode_parser.GCodeDispatch)
        for command in resume.commands:
            parsed = types.SimpleNamespace(
                get_commandline=lambda command=command: command, _params={})
            parser._get_extended_params(parsed)
        self.assertEqual(cancel.commands[0], "_TIMELAPSE_START_RESET")
        self.assertEqual(cancel.commands[-3:], (
            "SDCARD_CANCEL_FILE",
            "SET_GCODE_VARIABLE MACRO=_TIMELAPSE_START_GUARD "
            "VARIABLE=sd_held VALUE=False",
            "CANCEL_PRINT_BASE"))
        self.assertFalse(any(command.startswith((
            "_TOOLHEAD_PARK_PAUSE_CANCEL", "_CLIENT_RETRACT"))
            for command in cancel.commands))
        self.assertIn("CANCEL_PRINT_BASE", cancel.commands)

        full_cancel = execute_macro_chain((
            (CLIENT, "CANCEL_PRINT"),
            (MACROS, "_TIMELAPSE_START_RESET")),
            "CANCEL_PRINT", printer=cancel_printer)
        self.assertLess(full_cancel.index("SDCARD_CANCEL_FILE"),
                        full_cancel.index("CANCEL_PRINT_BASE"))
        self.assertNotIn("M24.1", full_cancel)

        resumed_cancel = render_macro(
            CLIENT, "CANCEL_PRINT",
            printer={"gcode_macro _TIMELAPSE_START_GUARD": {"waiting": False},
                     "gcode_macro _CLIENT_VARIABLE": {
                         "user_cancel_macro": ""},
                     "gcode_macro RESUME": {"restore_idle_timeout": 0},
                     "mod_params": {"variables": {"park_dz": 10}},
                     "pause_resume": {"is_paused": False}})
        self.assertIn("CANCEL_PRINT_BASE", resumed_cancel.commands)

    def test_wait_poll_resumes_same_file_and_cancel_does_not_resume(self):
        waiting = self._printer(0, waiting=True)
        result = render_macro(MACROS, "_TIMELAPSE_START_DECIDE",
                              printer=waiting)
        self.assertEqual(result.commands, (
            "UPDATE_DELAYED_GCODE ID=_TIMELAPSE_START_POLL DURATION=2",))

        result = render_macro(MACROS, "_TIMELAPSE_START_CONTINUE",
                              printer=waiting)
        self.assertEqual(result.commands,
                         ("_TIMELAPSE_START_RESET", "_START_PRINT",
                          "_TIMELAPSE_START_RELEASE_SD"))

        result = render_macro(MACROS, "_TIMELAPSE_START_CANCEL",
                              printer=waiting)
        cancel_command = 'CANCEL_PRINT REASON="Timelapse start cancelled"'
        self.assertEqual(result.commands, (cancel_command,))

        cancelled = execute_macro_chain((
            (MACROS, "_TIMELAPSE_START_CANCEL"),
            (CLIENT, "CANCEL_PRINT"),
            (MACROS, "_TIMELAPSE_START_RESET")),
            "_TIMELAPSE_START_CANCEL", printer={
                **waiting,
                "gcode_macro _CLIENT_VARIABLE": {"user_cancel_macro": ""},
                "gcode_macro RESUME": {"restore_idle_timeout": 0},
                "mod_params": {"variables": {"park_dz": 10}},
                "pause_resume": {"is_paused": False}})
        self.assertLess(cancelled.index(
            'RESPOND TYPE=command MSG="action:prompt_end Previous timelapse"'),
                        cancelled.index("CANCEL_PRINT_BASE"))
        self.assertLess(cancelled.index("SDCARD_CANCEL_FILE"),
                        cancelled.index("CANCEL_PRINT_BASE"))
        self.assertNotIn("M24.1", cancelled)
        self.assertIn("UPDATE_DELAYED_GCODE ID=_TIMELAPSE_START_POLL DURATION=0",
                      cancelled)

        result = render_macro(MACROS, "_TIMELAPSE_START_CANCEL",
                              printer=self._printer(0, waiting=False))
        self.assertEqual(result.commands, ())

        late_cancel = self._printer(1, waiting=False, state="paused")
        late_cancel["gcode_macro _TIMELAPSE_START_GUARD"]["sd_held"] = True
        result = render_macro(MACROS, "_TIMELAPSE_START_CANCEL",
                              printer=late_cancel)
        self.assertEqual(result.commands, (cancel_command,))

        closed_wait = self._printer(1, waiting=True, prompt_open=False)
        reset = render_macro(MACROS, "_TIMELAPSE_START_RESET",
                             printer=closed_wait)
        self.assertFalse(any("action:prompt_end" in command
                             for command in reset.commands))

        wait_choice = render_macro(MACROS, "_TIMELAPSE_START_WAIT_CHOICE",
                                   printer=waiting)
        self.assertTrue(any("action:prompt_end Previous timelapse"
                            in command for command in wait_choice.commands))

        missing_file = self._printer(1, waiting=True, state="cancelled")
        result = render_macro(MACROS, "_TIMELAPSE_START_CONTINUE",
                              printer=missing_file)
        self.assertEqual(result.commands, ("_TIMELAPSE_START_RESET",))

        result = render_macro(MACROS, "_TIMELAPSE_START_POLL",
                              section="delayed_gcode",
                              printer=self._printer(0, waiting=False))
        self.assertEqual(result.commands, ())

    def test_external_cancel_clears_pending_wait(self):
        result = render_macro(HEADLESS, "_COMMON_END_PRINT", printer={
            "gcode_macro _TIMELAPSE_START_GUARD": {"waiting": True},
            "mod_params": {"variables": {"stop_motor": 0}},
            "bed_mesh": {"profile_name": "auto"},
        })
        self.assertEqual(result.commands[0], "_TIMELAPSE_START_RESET")

        without_guard = render_macro(HEADLESS, "_COMMON_END_PRINT", printer={
            "mod_params": {"variables": {"stop_motor": 0}},
            "bed_mesh": {"profile_name": "auto"},
        })
        self.assertEqual(without_guard.commands[0], "_STOP")


if __name__ == "__main__":
    unittest.main()
