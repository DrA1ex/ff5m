## Behavioral tests for Forge-X G-code macros.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import ast
import pathlib
import shlex
import unittest

from tests.gcode_macro_harness import (
    MacroActionError, execute_macro_chain, load_macro, render_macro)


ROOT = pathlib.Path(__file__).parents[1]
BASE = ROOT / "macros" / "base.cfg"
HEADLESS = ROOT / "macros" / "headless.cfg"
CLIENT = ROOT / "macros" / "client.cfg"
MATERIAL = ROOT / "config" / "material.cfg"
SMART_PARK = ROOT / "KAMP" / "Smart_Park.cfg"
MOTION_MACROS = (
    (BASE, "M600"),
    (BASE, "MOVE_SAFE"),
    (CLIENT, "PAUSE"),
    (CLIENT, "CANCEL_PRINT"),
    (CLIENT, "_TOOLHEAD_PARK_PAUSE_CANCEL"),
    (HEADLESS, "END_PRINT"),
)


def assert_order(test, commands, expected):
    positions = [commands.index(command) for command in expected]
    test.assertEqual(positions, sorted(positions))


def macro_status(path, name, **overrides):
    status = dict(load_macro(path, name).variables)
    status.update(overrides)
    return status


def material_config():
    return macro_status(MATERIAL, "_MATERIAL_CONFIG")


def start_print_printer(display, bed_mesh, mesh="", zforce_leveling=False,
                        zskip_leveling=False, use_kamp=False,
                        print_leveling=False):
    return {
        "gcode_macro _START_PRINT": macro_status(
            BASE, "_START_PRINT", zmesh=mesh,
            zforce_leveling=zforce_leveling,
            zskip_leveling=zskip_leveling),
        "gcode_macro START_PRINT": {"preparation_done": True},
        "mod_params": {"variables": {
            "safe_z": 10,
            "chamber_light_mode": "MANUAL",
            "display": display,
            "check_md5": 0,
            "print_leveling": print_leveling,
            "use_kamp": use_kamp,
            "bed_mesh_validation": False,
            "midi_start": "",
            "weight_check": False,
            "disable_priming": True,
        }},
        "extruder": {"temperature": 25, "can_extrude": False},
        "bed_mesh": bed_mesh,
    }


# Sub-macros expanded while executing _START_PRINT. Deeper helpers (KAMP
# meshing, SMART_PARK, LINE_PURGE, temperature waits, context commands)
# stay terminal and are asserted as the observable handoff.
START_PRINT_EXECUTION_CHAIN = (
    (BASE, "_START_PRINT"),
    (BASE, "_START_PRINT_PREPARE"),
    (BASE, "_CANCEL_DELAYED_COMMANDS"),
    (BASE, "_ENSURE_SERVICES_STARTED"),
    (BASE, "LOAD_GCODE_OFFSET"),
    (BASE, "_HOME_IF_NEEDED"),
    (BASE, "_PREPARE_LEVELING"),
    (BASE, "_FULL_BED_LEVEL"),
    (BASE, "KAMP"),
    (BASE, "_RAISE_WITH_PRINT_CANCEL"),
    (BASE, "_RAISE_ERROR"),
)


def start_print_state(
        *,
        display=1,
        mesh="",
        profile_name="auto",
        profiles=("auto", "PLA_profile"),
        zforce_leveling=False,
        zforce_kamp=False,
        zskip_leveling=False,
        zskip_zoffset=False,
        zzoffset=0.0,
        print_leveling=False,
        use_kamp=False,
        mesh_validation=False,
        validation_clear=False,
        disable_priming=True,
        extruder_temperature=25.0,
        can_extrude=False,
        chamber_light_mode="MANUAL",
        check_md5=0,
        preparation_done=True,
        midi_start="",
        weight_check=False,
        load_zoffset=False,
        filament_switch_sensor=False,
        filament_detected=True,
        start_status=None):
    """Build the standing printer state for a _START_PRINT execution.

    SET_GCODE_VARIABLE side effects are terminal commands in this harness,
    so variables the macro sets before reading them back are pre-seeded on
    the status: print_active is set by the macro itself at the start of
    every run, and the z* variables hold the START_PRINT wrapper request
    (or the fully staged status supplied by a wrapper execution).
    """
    if start_status is None:
        start_status = macro_status(
            BASE, "_START_PRINT", zmesh=mesh, print_active=True,
            zforce_leveling=zforce_leveling, zforce_kamp=zforce_kamp,
            zskip_leveling=zskip_leveling, zskip_zoffset=zskip_zoffset,
            zzoffset=zzoffset)
    printer = {
        "gcode_macro _START_PRINT": start_status,
        "gcode_macro START_PRINT": {"preparation_done": preparation_done},
        "gcode_macro _ENSURE_SERVICES_STARTED": {"initialized": True},
        "mod_params": {"variables": {
            "safe_z": 10,
            "chamber_light_mode": chamber_light_mode,
            "display": display,
            "check_md5": check_md5,
            "print_leveling": print_leveling,
            "use_kamp": use_kamp,
            "bed_mesh_validation": mesh_validation,
            "bed_mesh_validation_clear": validation_clear,
            "bed_mesh_validation_tolerance": 0.2,
            "clear_cooldown_temp": 150,
            "disable_cleaning": False,
            "zclear": "_CLEAR1",
            "z_offset": 0.1,
            "filament_switch_sensor": filament_switch_sensor,
            "load_zoffset": load_zoffset,
            "disable_skew": True,
            "midi_start": midi_start,
            "weight_check": weight_check,
            "disable_priming": disable_priming,
        }},
        "extruder": {"temperature": extruder_temperature,
                     "can_extrude": can_extrude},
        "bed_mesh": {
            "profile_name": profile_name,
            "profiles": {name: {} for name in profiles},
        },
        "toolhead": {"homed_axes": "xyz"},
    }
    if filament_switch_sensor:
        printer["filament_switch_sensor e0_sensor"] = {
            "filament_detected": filament_detected}
    return printer


def run_start_print(**state):
    return execute_macro_chain(
        START_PRINT_EXECUTION_CHAIN, "_START_PRINT",
        printer=start_print_state(**state))


def run_headless_start_print(
        params, *, profiles=("auto", "PLA_profile"), profile_name="auto",
        feather_force_leveling=None, feather_mesh_name=None, **standing):
    """Execute the headless START_PRINT wrapper through _START_PRINT.

    The wrapper stages the slicer request with SET_GCODE_VARIABLE
    commands; the staged values are applied to the _START_PRINT status
    before the consumer executes, the way Klipper updates macro variables.
    Returns (wrapper commands, full chain commands); the full chain keeps
    the wrapper's own commands, so the default-mesh handoff stays ordered
    in a single stream.
    """
    wrapper_status = macro_status(
        HEADLESS, "START_PRINT", preparation_done=True,
        feather_force_leveling=feather_force_leveling,
        feather_mesh_name=feather_mesh_name)
    wrapper = render_macro(
        HEADLESS, "START_PRINT",
        printer={
            "gcode_macro START_PRINT": wrapper_status,
            "mod_params": {"variables": {"filament_switch_sensor": False}},
            "bed_mesh": {"profiles": {name: {} for name in profiles}},
        }, params=params)

    staged = macro_status(BASE, "_START_PRINT", print_active=True)
    for command in wrapper.commands:
        arguments = shlex.split(command)
        if not arguments or arguments[0] != "SET_GCODE_VARIABLE":
            continue
        options = dict(
            argument.split("=", 1) for argument in arguments[1:])
        if options.get("MACRO") == "_START_PRINT":
            staged[options["VARIABLE"]] = ast.literal_eval(options["VALUE"])

    printer = start_print_state(
        start_status=staged, profiles=profiles, profile_name=profile_name,
        **standing)
    printer["gcode_macro START_PRINT"] = wrapper_status
    full_chain = execute_macro_chain(
        ((HEADLESS, "START_PRINT"),) + START_PRINT_EXECUTION_CHAIN,
        "START_PRINT", printer=printer, params=params)
    return wrapper.commands, full_chain


def mesh_actions(commands):
    names = ("BED_MESH_CALIBRATE", "_KAMP_BED_MESH_CALIBRATE",
             "BED_MESH_PROFILE")
    return tuple(command for command in commands
                 if command.split()[0] in names)


def mesh_generated_publish(profile):
    return ("SET_GCODE_VARIABLE MACRO=_START_PRINT "
            "VARIABLE=zmesh_generated VALUE='\"%s\"'" % profile)


class WorkflowMacroTest(unittest.TestCase):
    def test_system_power_macros_prepare_hardware_before_action(self):
        macros = (
            (BASE, "_PREPARE_SYSTEM_POWER"),
            (BASE, "SHUTDOWN"),
            (BASE, "REBOOT"),
        )
        preparation = (
            'RESPOND TYPE=command MSG="action:forge_x_shutting_down"',
            "BED_MESH_CLEAR",
            "M400",
            "SET_PIN PIN=clear_power_off VALUE=1",
            "WAIT TIME=500",
            "SET_PIN PIN=clear_power_off VALUE=0",
        )

        shutdown = execute_macro_chain(macros, "SHUTDOWN")
        reboot = execute_macro_chain(macros, "REBOOT")

        self.assertEqual(
            shutdown, preparation + (
                "SET_PIN PIN=power_off VALUE=0",
                "RUN_SHELL_COMMAND CMD=sync",
                "RUN_SHELL_COMMAND CMD=poweroff"))
        self.assertEqual(
            reboot, preparation + (
                "RUN_SHELL_COMMAND CMD=sync",
                "RUN_SHELL_COMMAND CMD=reboot"))

    def test_conditional_homing_publishes_state_only_when_needed(self):
        unhomed = render_macro(BASE, "_HOME_IF_NEEDED", printer={
            "toolhead": {"homed_axes": ""},
            "operation_context": {"context_path": ["print"]},
        })
        homed = render_macro(BASE, "_HOME_IF_NEEDED", printer={
            "toolhead": {"homed_axes": "xyz"},
            "operation_context": {"context_path": ["print"]},
        })
        outside_context = render_macro(BASE, "_HOME_IF_NEEDED", printer={
            "toolhead": {"homed_axes": ""},
            "operation_context": {"context_path": []},
        })

        self.assertEqual(
            unhomed.commands, ("_CONTEXT_STATE NAME=HOMING", "G28"))
        self.assertEqual(homed.commands, ())
        self.assertEqual(outside_context.commands, ("G28",))

    def test_start_print_emits_complete_context_lifecycle(self):
        start = macro_status(BASE, "_START_PRINT", zskip_leveling=True)
        result = render_macro(BASE, "_START_PRINT", printer={
            "gcode_macro _START_PRINT": start,
            "gcode_macro START_PRINT": {"preparation_done": True},
            "mod_params": {"variables": {
                "safe_z": 10,
                "chamber_light_mode": "MANUAL",
                "display": 1,
                "check_md5": 0,
                "print_leveling": False,
                "use_kamp": False,
                "bed_mesh_validation": False,
                "midi_start": "",
                "weight_check": False,
                "disable_priming": True,
            }},
            "extruder": {"temperature": 25, "can_extrude": False},
            "bed_mesh": {"profile_name": "", "profiles": {}},
        })

        assert_order(self, result.commands, (
            "_CONTEXT_BEGIN TYPE=print",
            "_START_PRINT_PREPARE",
            "_HOME_IF_NEEDED",
            "_CONTEXT_STATE NAME=LEVELING",
            '_CONTEXT_STATE NAME="SKIPPING LEVELING"',
            "_CONTEXT_STATE NAME=PARKING",
            "_WAIT_TEMPERATURE CMD=M140 VALUE=80.0 BELOW=2 ABOVE=5",
            "_WAIT_TEMPERATURE CMD=M104 VALUE=245.0",
            "_CONTEXT_STATE NAME=PRIMING",
            "_CONTEXT_STATE NAME=PRINTING",
        ))

    def test_feather_start_options_override_leveling_for_one_print(self):
        start = macro_status(
            HEADLESS, "START_PRINT", feather_force_leveling=True,
            feather_mesh_name=None)
        temporary = render_macro(HEADLESS, "START_PRINT", printer={
            "gcode_macro START_PRINT": start,
            "mod_params": {"variables": {"filament_switch_sensor": False}},
            "bed_mesh": {"profiles": {"auto": {}}},
        }, params={"EXTRUDER_TEMP": 230, "BED_TEMP": 65,
                   "SKIP_LEVELING": 1, "MESH": "slicer"})

        self.assertIn(
            "SET_GCODE_VARIABLE MACRO=_START_PRINT "
            "VARIABLE=zforce_leveling VALUE=1", temporary.commands)
        self.assertIn(
            "SET_GCODE_VARIABLE MACRO=_START_PRINT "
            "VARIABLE=zskip_leveling VALUE=0", temporary.commands)
        self.assertIn(
            "SET_GCODE_VARIABLE MACRO=_START_PRINT "
            "VARIABLE=zmesh VALUE='\"\"'", temporary.commands)

        start["feather_mesh_name"] = "auto"
        persistent = render_macro(HEADLESS, "START_PRINT", printer={
            "gcode_macro START_PRINT": start,
            "mod_params": {"variables": {"filament_switch_sensor": False}},
            "bed_mesh": {"profiles": {"auto": {}}},
        }, params={"EXTRUDER_TEMP": 230, "BED_TEMP": 65})

        self.assertIn(
            "SET_GCODE_VARIABLE MACRO=_START_PRINT "
            "VARIABLE=zmesh VALUE='\"auto\"'", persistent.commands)

    def test_headless_start_uses_slicer_values_without_feather_override(self):
        start = macro_status(HEADLESS, "START_PRINT")
        result = render_macro(HEADLESS, "START_PRINT", printer={
            "gcode_macro START_PRINT": start,
            "mod_params": {"variables": {"filament_switch_sensor": False}},
            "bed_mesh": {"profiles": {}},
        }, params={"EXTRUDER_TEMP": 230, "BED_TEMP": 65,
                   "FORCE_LEVELING": 0, "SKIP_LEVELING": 1,
                   "MESH": "slicer"})

        self.assertIn(
            "SET_GCODE_VARIABLE MACRO=_START_PRINT "
            "VARIABLE=zforce_leveling VALUE=0", result.commands)
        self.assertIn(
            "SET_GCODE_VARIABLE MACRO=_START_PRINT "
            "VARIABLE=zskip_leveling VALUE=1", result.commands)
        self.assertIn(
            "SET_GCODE_VARIABLE MACRO=_START_PRINT "
            "VARIABLE=zmesh VALUE='\"slicer\"'", result.commands)

    def test_feather_rebuild_uses_full_mesh_even_when_kamp_is_enabled(self):
        start = macro_status(
            BASE, "_START_PRINT", zforce_leveling=True, zmesh="auto")
        result = render_macro(BASE, "_START_PRINT", printer={
            "gcode_macro _START_PRINT": start,
            "gcode_macro START_PRINT": {"preparation_done": True},
            "mod_params": {"variables": {
                "safe_z": 10,
                "chamber_light_mode": "MANUAL",
                "display": 1,
                "check_md5": 0,
                "print_leveling": False,
                "use_kamp": True,
                "bed_mesh_validation": False,
                "midi_start": "",
                "weight_check": False,
                "disable_priming": True,
            }},
            "extruder": {"temperature": 25, "can_extrude": False},
            "bed_mesh": {"profile_name": "auto", "profiles": {"auto": {}}},
        })

        full_level = (
            "_FULL_BED_LEVEL BED_TEMP=80.0 EXTRUDER_TEMP=245.0 "
            "PROFILE=auto")
        self.assertIn(full_level, result.commands)
        self.assertNotIn(
            "KAMP BED_TEMP=80.0 EXTRUDER_TEMP=245.0", result.commands)
        self.assertIn(
            "SET_GCODE_VARIABLE MACRO=_START_PRINT "
            "VARIABLE=zmesh_generated VALUE='\"auto\"'", result.commands)

    def test_start_print_fallback_mesh_uses_display_persistent_profile(self):
        cases = (
            # A pure default start on an alternative screen recreates the
            # persistent 'auto' profile instead of a temporary mesh.
            (1, "", "auto"),
            # A requested but missing 'auto' profile is regenerated as 'auto'.
            (1, "auto", "auto"),
            # Stock keeps its temporary 'default' fallback.
            (0, "", "default"),
        )
        for display, mesh, expected in cases:
            with self.subTest(display=display, mesh=mesh):
                result = render_macro(
                    BASE, "_START_PRINT", printer=start_print_printer(
                        display, {"profile_name": "", "profiles": {}},
                        mesh=mesh))

                self.assertIn(
                    "_FULL_BED_LEVEL BED_TEMP=80.0 EXTRUDER_TEMP=245.0 "
                    "PROFILE=%s" % expected, result.commands)
                self.assertIn(
                    "SET_GCODE_VARIABLE MACRO=_START_PRINT "
                    "VARIABLE=zmesh_generated VALUE='\"%s\"'" % expected,
                    result.commands)

    def test_start_print_records_generated_mesh_profile(self):
        cases = (
            ("forced unnamed mesh", {"zforce_leveling": True}, "", "default"),
            ("kamp leveling", {"use_kamp": True}, "", "default"),
            ("loaded requested profile", {}, "PLA_profile", ""),
            ("skipped leveling", {"zskip_leveling": True}, "", ""),
            # A print_leveling policy measures the requested profile like any
            # forced run; the screen decides whether persisting makes sense.
            ("print_leveling policy with named mesh",
             {"print_leveling": True}, "auto", "auto"),
        )
        for label, overrides, mesh, expected in cases:
            with self.subTest(label=label):
                result = render_macro(
                    BASE, "_START_PRINT", printer=start_print_printer(
                        1, {"profile_name": "PLA_profile",
                            "profiles": {"PLA_profile": {}}},
                        mesh=mesh, **overrides))

                self.assertIn(
                    "SET_GCODE_VARIABLE MACRO=_START_PRINT "
                    "VARIABLE=zmesh_generated VALUE='\"%s\"'" % expected,
                    result.commands)

    def test_headless_end_clears_pending_feather_mesh_options(self):
        result = render_macro(HEADLESS, "_COMMON_END_PRINT", printer={
            "mod_params": {"variables": {"stop_motor": 0}},
            "bed_mesh": {"profile_name": "auto"},
        })

        for variable, value in (
                ("feather_force_leveling", "None"),
                ("feather_mesh_name", "None")):
            self.assertIn(
                "SET_GCODE_VARIABLE MACRO=START_PRINT "
                "VARIABLE=%s VALUE=%s" % (variable, value),
                result.commands)

    def test_tuning_macros_emit_their_lifecycle_in_order(self):
        cases = (
            ("PID_TUNE_BED", {}, (
                "_CONTEXT_BEGIN TYPE=pid_bed", "_HOME_IF_NEEDED",
                "_CONTEXT_STATE NAME=TUNING",
                "PID_CALIBRATE HEATER=heater_bed TARGET=80",
                "_CONTEXT_STATE NAME=COMPLETE", "_CONTEXT_END")),
            ("PID_TUNE_EXTRUDER", {}, (
                "_CONTEXT_BEGIN TYPE=pid_extruder", "_HOME_IF_NEEDED",
                "_CONTEXT_STATE NAME=TUNING",
                "PID_CALIBRATE HEATER=extruder TARGET=245",
                "_CONTEXT_STATE NAME=COMPLETE", "_CONTEXT_END")),
            ("ZSHAPER", {"toolhead": {"square_corner_velocity": 5}}, (
                "_CONTEXT_BEGIN TYPE=input_shaper",
                "_CONTEXT_STATE NAME=PREPARING", "_HOME_IF_NEEDED",
                "_CONTEXT_STATE NAME=MEASURING", "SHAPER_CALIBRATE",
                "_CONTEXT_STATE NAME=PROCESSING",
                'RUN_SHELL_COMMAND CMD=zshaper PARAMS="--calculate"',
                "_CONTEXT_STATE NAME=COMPLETE", "_CONTEXT_END")),
        )
        for name, printer, expected in cases:
            with self.subTest(name=name):
                result = render_macro(BASE, name, printer=printer)
                assert_order(self, result.commands, expected)

    def test_bed_screw_tune_selects_clean_or_cooldown_path(self):
        printer = {"mod_params": {"variables": {
            "clear_cooldown_temp": 150,
        }}}
        clean = render_macro(
            BASE, "BED_LEVEL_SCREWS_TUNE", printer=printer,
            params={"EXTRUDER_TEMP": 235, "BED_TEMP": 75, "CLEAN": 1})
        cooldown = render_macro(
            BASE, "BED_LEVEL_SCREWS_TUNE", printer=printer,
            params={"EXTRUDER_TEMP": 235, "BED_TEMP": 75, "CLEAN": 0})
        probe = render_macro(BASE, "_BED_LEVEL_SCREWS_PROBE")

        self.assertIn(
            "CLEAR_NOZZLE EXTRUDER_TEMP=235.0 BED_TEMP=75.0",
            clean.commands)
        self.assertNotIn("M104 S150", clean.commands)
        assert_order(self, cooldown.commands, (
            "M104 S150", "_HOME_IF_NEEDED",
            "_CONTEXT_STATE NAME=HEATING",
            "_WAIT_TEMPERATURE CMD=M104 VALUE=150 BELOW=2 ABOVE=3",
            "_CONTEXT_STATE NAME=PROBING",
        ))
        self.assertEqual(probe.commands[:2], (
            "LOAD_CELL_TARE", "SCREWS_TILT_CALCULATE"))

    def test_workflow_macros_publish_their_owned_contexts(self):
        cases = (
            (BASE, "CLEAR_NOZZLE", {
                "mod_params": {"variables": {
                    "safe_z": 10,
                    "clear_cooldown_temp": 150,
                }},
                "bed_mesh": {"profile_name": "default"},
            }, {}, "_CONTEXT_BEGIN TYPE=nozzle_clean", True),
            (BASE, "_FULL_BED_LEVEL", {
                "mod_params": {"variables": {"safe_z": 10}},
            }, {"PROFILE": "test"}, "_CONTEXT_BEGIN TYPE=bed_level", True),
            (BASE, "KAMP", {}, {}, "_CONTEXT_BEGIN TYPE=kamp", True),
            (BASE, "_AUTO_FULL_BED_LEVEL", {}, {
                "EXTRUDER_TEMP": 230, "BED_TEMP": 70, "PROFILE": "auto",
            }, "_CONTEXT_BEGIN TYPE=auto_bed_level", True),
            (MATERIAL, "LOAD_MATERIAL", {
                "gcode_macro _MATERIAL_CONFIG": material_config(),
                "extruder": {"target": 0},
            }, {}, "_CONTEXT_BEGIN TYPE=filament", False),
        )
        for path, name, printer, params, context_command, completes in cases:
            with self.subTest(name=name):
                result = render_macro(
                    path, name, printer=printer, params=params)
                self.assertIn(context_command, result.commands)
                self.assertEqual("_CONTEXT_END" in result.commands, completes)


class TemperatureMacroTest(unittest.TestCase):
    @staticmethod
    def _wait_printer(temperature, current_state=""):
        return {
            "extruder": {"temperature": temperature},
            "heater_bed": {"temperature": temperature},
            "operation_context": {
                "context_path": ["print"],
                "current_state": current_state,
            },
        }

    def test_wait_derives_heating_cooling_and_in_range_states(self):
        params = {
            "CMD": "M104", "VALUE": 200, "TIMEOUT": 1, "DELAY": 1000,
        }
        heating = render_macro(
            BASE, "_WAIT_TEMPERATURE",
            printer=self._wait_printer(150), params=params,
            rawparams="CMD=M104 VALUE=200 TIMEOUT=1 DELAY=1000")
        cooling = render_macro(
            BASE, "_WAIT_TEMPERATURE",
            printer=self._wait_printer(220), params=params,
            rawparams="CMD=M104 VALUE=200 TIMEOUT=1 DELAY=1000")
        ready = render_macro(
            BASE, "_WAIT_TEMPERATURE",
            printer=self._wait_printer(200), params=params,
            rawparams="CMD=M104 VALUE=200 TIMEOUT=1 DELAY=1000")

        self.assertIn(
            '_CONTEXT_STATE NAME="HEATING NOZZLE" TEMPORARY=1',
            heating.commands)
        self.assertIn(
            '_CONTEXT_STATE NAME="COOLING NOZZLE" TEMPORARY=1',
            cooling.commands)
        self.assertNotIn("_CONTEXT_STATE", "\n".join(ready.commands))
        self.assertIn(
            "SET_GCODE_VARIABLE MACRO=_WAIT_TEMPERATURE "
            "VARIABLE=temporary_state VALUE=False",
            ready.commands)

    def test_wait_check_clears_state_before_context_cancel_point(self):
        result = render_macro(BASE, "_WAIT_TEMPERATURE_CHECK", printer={
            "gcode_macro _WAIT_TEMPERATURE": {
                "temperature_reached": False,
                "cancel": False,
            },
            "operation_context": {
                "context_path": ["print"],
                "cancel_pending": True,
            },
            "extruder": {"temperature": 150},
        }, params={"CMD": "M104", "VALUE": 200, "DELAY": 1000})

        assert_order(self, result.commands, (
            "_WAIT_TEMPERATURE_RESET_STATE",
            "_CONTEXT_CANCEL_POINT",
            "M104 S200",
            "WAIT TIME=1000",
        ))

    def test_cancelled_wait_restores_context_and_routes_cancellation(self):
        result = render_macro(BASE, "_WAIT_TEMPERATURE_FINAL_CHECK", printer={
            "gcode_macro _WAIT_TEMPERATURE": {
                "temperature_reached": False,
                "cancel": True,
                "temporary_state": True,
            },
            "operation_context": {
                "context_path": ["print"],
                "cancel_available": True,
            },
        })

        self.assertEqual(result.commands, (
            "_WAIT_TEMPERATURE_RESET_STATE",
            "_CONTEXT_STATE RESTORE=1",
            "_CONTEXT_CANCEL",
            "_CONTEXT_CANCEL_POINT",
            '_RAISE_WITH_PRINT_CANCEL MSG="Temperature waiting cancelled."',
        ))

    def test_wait_reset_clears_all_latches(self):
        result = render_macro(BASE, "_WAIT_TEMPERATURE_RESET_STATE")

        self.assertEqual(result.commands, (
            "SET_GCODE_VARIABLE MACRO=_WAIT_TEMPERATURE VARIABLE=active VALUE=False",
            "SET_GCODE_VARIABLE MACRO=_WAIT_TEMPERATURE VARIABLE=cancel VALUE=False",
            "SET_GCODE_VARIABLE MACRO=_WAIT_TEMPERATURE VARIABLE=temperature_reached VALUE=False",
            "SET_GCODE_VARIABLE MACRO=_WAIT_TEMPERATURE VARIABLE=temporary_state VALUE=False",
        ))


class MaterialMacroTest(unittest.TestCase):
    @staticmethod
    def _printer():
        return {
            "gcode_macro _MATERIAL_CONFIG": material_config(),
            "configfile": {"settings": {"extruder": {
                "min_temp": 0,
                "max_temp": 300,
                "min_extrude_temp": 170,
            }}},
        }

    def test_material_selection_and_preheat_emit_persistence_and_targets(self):
        printer = self._printer()
        selected = render_macro(
            MATERIAL, "SET_MATERIAL", printer=printer,
            params={"MATERIAL": "petg"})
        preheat = render_macro(
            MATERIAL, "PREHEAT_MATERIAL", printer=printer,
            params={"MATERIAL": "petg"})

        self.assertEqual(selected.commands, (
            "_VALIDATE_MATERIAL_CONFIG WORKFLOW=heating",
            'SET_MOD PARAM=current_material VALUE="PETG"',
            'RESPOND PREFIX="info" MSG="Current material: PETG"',
        ))
        self.assertEqual(preheat.commands, (
            "_VALIDATE_MATERIAL_CONFIG WORKFLOW=heating",
            'SET_MATERIAL MATERIAL="PETG"',
            "M104 S250.0",
            "M140 S70.0",
            'RESPOND PREFIX="info" MSG="Preheat PETG: 250/70 C"',
        ))

    def test_cold_pull_selector_passes_profile_to_progress_workflow(self):
        result = render_macro(
            MATERIAL, "COLDPULL", printer=self._printer())

        self.assertIn(
            'RESPOND TYPE=command MSG="action:prompt_button PETG|'
            '_COLDPULL_LOAD_MATERIAL MATERIAL=PETG TEMP=250 COLD=100 '
            'PROMPT=1|primary"',
            result.commands)

    def test_cold_pull_progress_uses_context_and_managed_waits(self):
        result = render_macro(
            MATERIAL, "_COLDPULL_LOAD_MATERIAL", printer=self._printer(),
            params={"MATERIAL": "PETG", "TEMP": 250, "COLD": 100,
                    "PROMPT": 1})

        assert_order(self, result.commands, (
            'RESPOND TYPE=command MSG="action:prompt_begin Cold Pull"',
            'RESPOND TYPE=command MSG="action:prompt_footer_button Cancel|_CONTEXT_CANCEL|secondary"',
            "_CONTEXT_BEGIN TYPE=cold_pull",
            "_HOME_IF_NEEDED",
            "_CONTEXT_STATE NAME=HEATING",
            "_WAIT_TEMPERATURE CMD=M104 VALUE=250.0 MINIMUM=250.0 MAXIMUM=260.0",
            "_CONTEXT_STATE NAME=EXTRUDING",
            "_WAIT_TEMPERATURE CMD=M104 VALUE=100.0 MINIMUM=98.0 MAXIMUM=102.0",
            "_CONTEXT_STATE NAME=PULLING",
            "_CONTEXT_END",
            "RESPOND TYPE=command MSG=action:prompt_end",
        ))


class MotionAndIntegrationMacroTest(unittest.TestCase):
    @staticmethod
    def _motion_printer(current_z, *, origin=0, safe_z=10,
                        pause_z_min=50, park_dz=50, homed="xyz"):
        return {
            "resurrection": {"supports_pause_markers": False},
            "gcode_macro _CLIENT_VARIABLE": macro_status(
                HEADLESS, "_CLIENT_VARIABLE"),
            "gcode_macro RESUME": {"restore_idle_timeout": 0},
            "gcode_macro MOVE_SAFE": macro_status(BASE, "MOVE_SAFE"),
            "configfile": {"settings": {
                "idle_timeout": {"timeout": 600},
                "pause_resume": {"recover_velocity": 50},
                "printer": {"kinematics": "cartesian"},
            }},
            "mod_params": {"variables": {
                "safe_z": safe_z,
                "pause_z_min": pause_z_min,
                "park_dz": park_dz,
                "midi_end": "",
            }},
            "pause_resume": {"is_paused": False},
            "gcode_move": {
                "homing_origin": {"z": origin},
                "gcode_position": {"z": current_z},
                "absolute_coordinates": True,
            },
            "toolhead": {
                "axis_maximum": {"z": 230},
                "cone_start_z": 230,
                "homed_axes": homed,
                "extruder": "",
                "position": {"x": 0, "y": 0, "z": current_z},
            },
            "extruder": {"can_extrude": False},
        }

    @staticmethod
    def _axis_targets(commands, axis):
        targets = []
        for command in commands:
            tokens = command.split()
            if not tokens or tokens[0] != "G1":
                continue
            targets.extend(float(token[1:]) for token in tokens[1:]
                           if token.startswith(axis))
        return targets

    def test_pause_and_m600_execute_to_bounded_z_motion(self):
        cases = (
            (5, 0, 10, 50, 50),
            (100, 0, 10, 50, 110),
            (215, 0, 10, 50, 220),
            (215, 2, 10, 50, 218),
            (5, 0, 10, 500, 220),
        )
        for entry in ("PAUSE", "M600"):
            for current, origin, safe_z, pause_z_min, expected in cases:
                with self.subTest(
                        entry=entry, current=current, origin=origin,
                        safe_z=safe_z, pause_z_min=pause_z_min):
                    commands = execute_macro_chain(
                        MOTION_MACROS, entry, printer=self._motion_printer(
                            current, origin=origin, safe_z=safe_z,
                            pause_z_min=pause_z_min))

                    self.assertEqual(
                        self._axis_targets(commands, "Z"), [expected])

    def test_cancel_executes_park_dz_to_bounded_z_motion(self):
        cases = (
            (0, 0, 10, 50, 50),
            (100, 0, 10, 50, 150),
            (215, 0, 10, 50, 220),
            (215, 2, 10, 50, 218),
            (100, 0, 10, 5, 110),
            (100, 0, 10, -50, 150),
            (100, 0, 10, 500, 220),
        )
        for paused in (False, True):
            for current, origin, safe_z, park_dz, expected in cases:
                with self.subTest(
                        paused=paused, current=current, origin=origin,
                        safe_z=safe_z, park_dz=park_dz):
                    printer = self._motion_printer(
                        current, origin=origin, safe_z=safe_z,
                        park_dz=park_dz)
                    printer["pause_resume"]["is_paused"] = paused
                    commands = execute_macro_chain(
                        MOTION_MACROS, "CANCEL_PRINT", printer=printer,
                        params={"REASON": "USER"})

                    self.assertEqual(
                        self._axis_targets(commands, "Z"), [expected])

    def test_cancel_publishes_current_reason_before_base_cancel(self):
        printer = self._motion_printer(100)
        for params, reason in ((None, ""),
                               ({"REASON": "FILAMENT RUNOUT"},
                                "FILAMENT RUNOUT")):
            with self.subTest(reason=reason):
                result = render_macro(
                    CLIENT, "CANCEL_PRINT", printer=printer, params=params)
                publish = (
                    "SET_GCODE_VARIABLE MACRO=CANCEL_PRINT "
                    "VARIABLE=cancel_reason VALUE='\"%s\"'" % reason)

                self.assertIn(publish, result.commands)
                assert_order(self, result.commands, (
                    publish,
                    "CANCEL_PRINT_BASE",
                ))

    def test_print_failure_forwards_message_as_cancel_reason(self):
        result = render_macro(
            BASE, "_RAISE_WITH_PRINT_CANCEL",
            printer={"gcode_macro _START_PRINT": {"print_active": True}},
            params={"MSG": "Temperature waiting timed out."})

        self.assertEqual(result.commands, (
            'CANCEL_PRINT REASON="Temperature waiting timed out."',
            "M400",
            'RESPOND PREFIX="!!" MSG="Temperature waiting timed out."',
            "_RAISE_ERROR",
        ))

    def test_end_print_executes_move_safe_to_bounded_z_motion(self):
        cases = (
            (0, 50, 50),
            (100, 50, 150),
            (215, 50, 220),
            (100, 500, 220),
            (100, -500, 0),
        )
        for current, park_dz, expected in cases:
            with self.subTest(current=current, park_dz=park_dz):
                commands = execute_macro_chain(
                    MOTION_MACROS, "END_PRINT",
                    printer=self._motion_printer(
                        current, park_dz=park_dz))

                self.assertEqual(
                    self._axis_targets(commands, "Z"), [expected])

    def test_end_print_relative_lift_uses_gcode_position_with_active_mesh(self):
        printer = self._motion_printer(60, park_dz=1)
        printer["bed_mesh"] = {"profile_name": "auto"}
        printer["toolhead"]["position"]["z"] = 60.056863

        commands = execute_macro_chain(
            MOTION_MACROS, "END_PRINT", printer=printer)

        self.assertEqual(self._axis_targets(commands, "Z"), [61])

    def test_terminal_motion_macros_clamp_requested_xy(self):
        cases = (
            ("PAUSE", {"X": 999, "Y": -999}),
            ("M600", {"X": 999, "Y": -999}),
            ("CANCEL_PRINT", {"REASON": "USER"}),
            ("END_PRINT", None),
        )
        for entry, params in cases:
            with self.subTest(entry=entry):
                printer = self._motion_printer(100)
                client = printer["gcode_macro _CLIENT_VARIABLE"]
                client["park_at_cancel_x"] = 999
                client["park_at_cancel_y"] = -999
                client["custom_park_x"] = 999
                client["custom_park_y"] = -999
                commands = execute_macro_chain(
                    MOTION_MACROS, entry, printer=printer, params=params)

                self.assertEqual(
                    self._axis_targets(commands, "X"), [110])
                self.assertEqual(
                    self._axis_targets(commands, "Y"), [-110])

    def test_terminal_motion_macros_do_not_move_unhomed_axes(self):
        for entry in ("PAUSE", "M600", "CANCEL_PRINT", "END_PRINT"):
            with self.subTest(entry=entry):
                commands = execute_macro_chain(
                    MOTION_MACROS, entry,
                    printer=self._motion_printer(100, homed=""),
                    params={"REASON": "USER"}
                    if entry == "CANCEL_PRINT" else None)

                self.assertFalse(any(
                    command.split()[0] == "G1" for command in commands))

    def test_pause_park_uses_minimum_lift_and_reachable_z_ceiling(self):
        limits = macro_status(BASE, "MOVE_SAFE")
        cases = (
            (10, 230, 0, 50),
            (35, 230, 0, 50),
            (45, 230, 0, 55),
            (150, 230, 0, 160),
            (215, 230, 0, 220),
            (195, 210, 0, 200),
            (215, 230, 2, 218),
        )
        for current, axis_max, offset, expected in cases:
            with self.subTest(current=current, axis_max=axis_max,
                              offset=offset):
                result = render_macro(
                    CLIENT, "_TOOLHEAD_PARK_PAUSE_CANCEL", printer={
                        "gcode_macro _CLIENT_VARIABLE": {},
                        "gcode_macro MOVE_SAFE": limits,
                        "configfile": {"settings": {
                            "pause_resume": {"recover_velocity": 50},
                            "printer": {"kinematics": "cartesian"},
                        }},
                        "mod_params": {"variables": {"safe_z": 10}},
                        "gcode_move": {
                            "homing_origin": {"z": offset},
                            "gcode_position": {"z": current},
                            "absolute_coordinates": True,
                        },
                        "toolhead": {
                            "axis_maximum": {"z": axis_max},
                            "cone_start_z": axis_max,
                            "homed_axes": "xyz",
                        },
                    }, params={"Z_MIN": 50})

                self.assertIn("G1 Z%.1f F900" % expected, result.commands)

    def test_z_adjust_uses_current_homing_origin(self):
        result = render_macro(
            BASE, "SET_GCODE_OFFSET",
            printer={
                "mod_params": {"variables": {"z_offset": 0.1}},
                "gcode_move": {"homing_origin": {"z": 0.25}},
            },
            params={"Z_ADJUST": -0.01}, rawparams="Z_ADJUST=-0.01")

        self.assertEqual(result.commands, (
            "_SET_GCODE_OFFSET Z_ADJUST=-0.01",
            'SET_MOD PARAM="z_offset" VALUE=\'0.24\'',
        ))

    def test_move_safe_clamps_absolute_targets_to_shared_limits(self):
        limits = macro_status(BASE, "MOVE_SAFE")
        result = render_macro(BASE, "MOVE_SAFE", printer={
            "gcode_macro MOVE_SAFE": limits,
            "toolhead": {
                "axis_maximum": {"z": 220},
                "position": {"x": 0, "y": 0, "z": 0},
            },
        }, params={"X": 999, "Y": -999, "Z": 999, "ABSOLUTE": 1,
                   "F": 6000})

        self.assertEqual(result.commands, (
            "SAVE_GCODE_STATE NAME=_client_movement",
            "G90",
            "G1 X110.0 Y-110.0 Z210.0  F6000",
            "RESTORE_GCODE_STATE NAME=_client_movement",
        ))

    def test_move_safe_relative_targets_ignore_bed_mesh_transform(self):
        limits = macro_status(BASE, "MOVE_SAFE")
        cases = (
            ("unloaded", "", 60.0),
            ("loaded", "auto", 60.056863),
        )
        for label, profile, physical_z in cases:
            with self.subTest(mesh=label):
                result = render_macro(BASE, "MOVE_SAFE", printer={
                    "gcode_macro MOVE_SAFE": limits,
                    "bed_mesh": {"profile_name": profile},
                    "gcode_move": {
                        "gcode_position": {"x": 10, "y": 20, "z": 60},
                    },
                    "toolhead": {
                        "axis_maximum": {"z": 230},
                        "position": {"x": 10, "y": 20, "z": physical_z},
                    },
                }, params={"X": 1, "Y": -2, "Z": -1, "F": 6000})

                self.assertEqual(self._axis_targets(result.commands, "X"), [11])
                self.assertEqual(self._axis_targets(result.commands, "Y"), [18])
                self.assertEqual(self._axis_targets(result.commands, "Z"), [59])
                assert_order(self, result.commands, (
                    "SAVE_GCODE_STATE NAME=_client_movement",
                    "G90",
                    "G1 X11.0 Y18.0 Z59.0  F6000",
                    "RESTORE_GCODE_STATE NAME=_client_movement",
                ))

    def test_smart_park_uses_fallback_and_rejects_unhomed_motion(self):
        printer = {
            "gcode_macro _KAMP_Settings": {
                "verbose_enable": True,
                "purge_margin": 5,
            },
            "gcode_macro SMART_PARK": macro_status(
                SMART_PARK, "SMART_PARK"),
            "gcode_macro MOVE_SAFE": macro_status(BASE, "MOVE_SAFE"),
            "mod_params": {"variables": {"safe_z": 10}},
            "toolhead": {
                "homed_axes": "xyz",
                "axis_maximum": {"z": 220},
                "position": {"z": 5},
                "max_velocity": 300,
            },
            "exclude_object": {"objects": []},
        }
        result = render_macro(SMART_PARK, "SMART_PARK", printer=printer)

        self.assertIn(
            "MOVE_SAFE X=110.0 Y=100.0 F=18000.0 ABSOLUTE=1",
            result.commands)
        self.assertEqual(
            [command for command in result.commands
             if command.startswith("MOVE_SAFE ")],
            [
                "MOVE_SAFE Z=10.0 F=18000.0 ABSOLUTE=1",
                "MOVE_SAFE X=110.0 Y=100.0 F=18000.0 ABSOLUTE=1",
                "MOVE_SAFE Z=10.0 F=18000.0 ABSOLUTE=1",
            ])

        printer["toolhead"]["homed_axes"] = "xy"
        with self.assertRaisesRegex(
                MacroActionError, "requires homed XYZ axes"):
            render_macro(SMART_PARK, "SMART_PARK", printer=printer)

    def test_pause_and_resume_publish_recovery_markers_around_base_calls(self):
        pause = render_macro(CLIENT, "PAUSE", printer={
            "resurrection": {"supports_pause_markers": True},
            "gcode_macro _CLIENT_VARIABLE": {},
            "toolhead": {"extruder": "extruder"},
            "extruder": {"target": 215, "can_extrude": True},
            "mod_params": {"variables": {"pause_z_min": 50}},
            "pause_resume": {"is_paused": False},
        })
        resume = render_macro(CLIENT, "RESUME", printer={
            "resurrection": {"supports_pause_markers": True},
            "gcode_macro _CLIENT_VARIABLE": {},
            "configfile": {"settings": {
                "pause_resume": {"recover_velocity": 50},
            }},
            "mod_params": {"variables": {"filament_switch_sensor": False}},
            "toolhead": {"extruder": "extruder"},
            "extruder": {"can_extrude": True},
            "idle_timeout": {"state": "READY"},
        })

        assert_order(self, pause.commands, (
            "_RESURRECTION_PAUSE", "PAUSE_BASE",
            "_TOOLHEAD_PARK_PAUSE_CANCEL   Z_MIN=50.0",
        ))
        assert_order(self, resume.commands, (
            "_CLIENT_EXTRUDE", "RESUME_BASE VELOCITY=50",
            "_RESURRECTION_RESUME",
        ))

    def test_idle_resume_restores_temperature_through_managed_wait(self):
        result = render_macro(
            CLIENT, "RESUME",
            variables={
                "last_extruder_temp": {"restore": True, "temp": 215},
            },
            printer={
                "resurrection": {"supports_pause_markers": False},
                "gcode_macro _CLIENT_VARIABLE": {},
                "configfile": {"settings": {
                    "pause_resume": {"recover_velocity": 50},
                }},
                "mod_params": {"variables": {
                    "filament_switch_sensor": False,
                }},
                "toolhead": {"extruder": "extruder"},
                "extruder": {"can_extrude": False},
                "idle_timeout": {"state": "IDLE"},
            })

        assert_order(self, result.commands, (
            "_CONTEXT_BEGIN TYPE=resume",
            "_WAIT_TEMPERATURE CMD=M104 VALUE=215 MINIMUM=215",
            "_CONTEXT_END",
            "_CLIENT_EXTRUDE",
            "RESUME_BASE VELOCITY=50",
        ))

    def test_m600_inherits_pause_minimum_without_overriding_it(self):
        result = render_macro(BASE, "M600", params={"X": 10, "Y": 20})

        self.assertEqual(result.commands[0], "PAUSE X=10.0 Y=20.0")

    def test_timezone_macro_passes_the_selected_zone_to_helper(self):
        result = render_macro(
            BASE, "SET_TIMEZONE",
            params={"ZONE": "Asia/Yekaterinburg"})

        self.assertEqual(result.commands[-1],
                         'RUN_SHELL_COMMAND CMD=ztimezone '
                         'PARAMS="Asia/Yekaterinburg"')

    def test_usb_prepare_blocks_printing_and_preserves_confirmed_identity(self):
        idle = render_macro(
            BASE, "PREPARE_USB",
            printer={"idle_timeout": {"state": "Ready"}})
        confirm = render_macro(
            BASE, "_PREPARE_USB_CONFIRM",
            params={"FORMAT": "fat32", "DEVICE": "sda", "ID": 42})

        self.assertEqual(
            idle.commands, ('RUN_SHELL_COMMAND CMD=zusb PARAMS="prompt"',))
        self.assertIn(
            'RESPOND TYPE=command MSG="action:prompt_footer_button Erase and '
            'format|_PREPARE_USB_EXECUTE FORMAT=FAT32 DEVICE=sda ID=42|error"',
            confirm.commands)
        with self.assertRaisesRegex(
                MacroActionError, "unavailable while printing"):
            render_macro(
                BASE, "PREPARE_USB",
                printer={"idle_timeout": {"state": "Printing"}})


class StartPrintExecutionTest(unittest.TestCase):
    """Executes the full _START_PRINT chain to fixate its mesh contract."""

    def test_leveling_branch_selects_exactly_one_mesh_action(self):
        cases = (
            ("skipped leveling", {"zskip_leveling": True}, (), ""),
            ("skipped leveling with standing kamp policy",
             {"zskip_leveling": True, "use_kamp": True}, (), ""),
            ("skipped leveling preempts standing print_leveling policy",
             {"zskip_leveling": True, "print_leveling": True}, (), ""),
            ("forced kamp", {"zforce_kamp": True},
             ("_KAMP_BED_MESH_CALIBRATE",), "default"),
            ("standing kamp policy", {"use_kamp": True},
             ("_KAMP_BED_MESH_CALIBRATE",), "default"),
            ("kamp preempts a requested saved profile",
             {"use_kamp": True, "mesh": "PLA_profile"},
             ("_KAMP_BED_MESH_CALIBRATE",), "default"),
            ("forced kamp preempts a requested saved profile",
             {"zforce_kamp": True, "mesh": "PLA_profile"},
             ("_KAMP_BED_MESH_CALIBRATE",), "default"),
            ("forced leveling preempts kamp",
             {"zforce_kamp": True, "zforce_leveling": True},
             ('BED_MESH_CALIBRATE PROFILE="default"',), "default"),
            ("forced leveling without a profile name",
             {"zforce_leveling": True},
             ('BED_MESH_CALIBRATE PROFILE="default"',), "default"),
            ("forced leveling with a profile name",
             {"zforce_leveling": True, "mesh": "auto"},
             ('BED_MESH_CALIBRATE PROFILE="auto"',), "auto"),
            ("forced leveling rebuilds a requested saved profile",
             {"zforce_leveling": True, "mesh": "PLA_profile"},
             ('BED_MESH_CALIBRATE PROFILE="PLA_profile"',), "PLA_profile"),
            ("standing print_leveling policy with a profile name",
             {"print_leveling": True, "mesh": "auto"},
             ('BED_MESH_CALIBRATE PROFILE="auto"',), "auto"),
            ("standing print_leveling policy without a profile name",
             {"print_leveling": True},
             ('BED_MESH_CALIBRATE PROFILE="default"',), "default"),
            ("requested saved profile is loaded", {"mesh": "PLA_profile"},
             ("BED_MESH_PROFILE LOAD=PLA_profile",), ""),
            ("requested missing profile is regenerated under its name",
             {"mesh": "missing"},
             ('BED_MESH_CALIBRATE PROFILE="missing"',), "missing"),
            ("no request and nothing loaded rebuilds persistent auto",
             {"profile_name": ""},
             ('BED_MESH_CALIBRATE PROFILE="auto"',), "auto"),
            ("no request and nothing loaded keeps stock default profile",
             {"profile_name": "", "display": 0},
             ('BED_MESH_CALIBRATE PROFILE="default"',), "default"),
            ("no request keeps the already loaded profile", {}, (), ""),
        )
        for label, scenario, actions, generated in cases:
            with self.subTest(label=label):
                commands = run_start_print(**scenario)

                self.assertEqual(mesh_actions(commands), actions)
                self.assertIn(mesh_generated_publish(generated), commands)

    def test_bed_mesh_validation_protects_only_reused_profiles(self):
        for label, scenario in (
                ("kamp generation", {"use_kamp": True}),
                ("forced full leveling", {"zforce_leveling": True}),
                ("missing-profile fallback", {"mesh": "missing"})):
            with self.subTest(generated_by=label):
                commands = run_start_print(
                    mesh_validation=True, **scenario)

                self.assertFalse(any(
                    command.startswith("_CHECK_BED_MESH")
                    for command in commands))

        cleared = run_start_print(
            mesh="PLA_profile", mesh_validation=True, validation_clear=True)
        assert_order(self, cleared, (
            "BED_MESH_PROFILE LOAD=PLA_profile",
            "G1 X110 Y110 F6000",
            "CLEAR_NOZZLE EXTRUDER_TEMP=245.0 BED_TEMP=80.0",
            "_CHECK_BED_MESH RETRACT=0",
            '_CONTEXT_STATE NAME="RESUMING HEAT"',
            "_WAIT_TEMPERATURE CMD=M104 VALUE=245.0",
        ))
        self.assertNotIn(
            "_WAIT_TEMPERATURE CMD=M104 VALUE=245.0 BELOW=5 ABOVE=5",
            cleared)

        heated = run_start_print(
            mesh="PLA_profile", mesh_validation=True, can_extrude=True)
        assert_order(self, heated, (
            "_WAIT_TEMPERATURE CMD=M104 VALUE=245.0 BELOW=5 ABOVE=5",
            # The whole _START_PRINT template renders before any command
            # executes, so the retract decision is deferred to a macro that
            # renders after the wait and observes the actually-hot extruder.
            "_CHECK_BED_MESH_AFTER_HEATING",
        ))
        self.assertNotIn("CLEAR_NOZZLE EXTRUDER_TEMP=245.0 BED_TEMP=80.0",
                         heated)

        # The everyday headless print: no explicit request and the wrapper
        # preloaded the persistent default, so the standing profile is
        # reused and stays under the validation protection.
        reused = run_start_print(mesh_validation=True)
        assert_order(self, reused, (
            'RESPOND PREFIX="//" MSG="Using loaded bed mesh profile: auto"',
            "_WAIT_TEMPERATURE CMD=M104 VALUE=245.0 BELOW=5 ABOVE=5",
            "_CHECK_BED_MESH_AFTER_HEATING",
        ))

        # SKIP_LEVELING reuses the standing mesh, so the protection
        # validates it instead of treating the run as a mesh generation.
        # Standing kamp/leveling policies must not masquerade as a
        # generated mesh under skip.
        for standing_flags in ({}, {"use_kamp": True},
                               {"print_leveling": True}):
            with self.subTest(skip_with=standing_flags or "no flags"):
                skipped = run_start_print(
                    zskip_leveling=True, mesh_validation=True,
                    **standing_flags)
                assert_order(self, skipped, (
                    '_CONTEXT_STATE NAME="SKIPPING LEVELING"',
                    "_WAIT_TEMPERATURE CMD=M104 VALUE=245.0 BELOW=5 ABOVE=5",
                    "_CHECK_BED_MESH_AFTER_HEATING",
                ))
                self.assertIn(mesh_generated_publish(""), skipped)

    def test_skip_leveling_without_any_profile_warns_and_skips_validation(self):
        # Nothing is generated and no profile is active: the print must
        # continue without mesh compensation, say so loudly, and skip the
        # validation preparation entirely instead of heating for a check
        # that could only discover its own absence.
        bare = run_start_print(
            zskip_leveling=True, profiles=(), profile_name="",
            mesh_validation=True, validation_clear=True)
        assert_order(self, bare, (
            '_CONTEXT_STATE NAME="SKIPPING LEVELING"',
            'RESPOND PREFIX="warn" MSG="No bed mesh profile is loaded. '
            'Printing without bed mesh compensation. Check if '
            'SKIP_LEVELING is intentional."',
            'RESPOND PREFIX="//" MSG="Bed Mesh Validation Protection is '
            'skipped because no bed mesh profile is loaded."',
            "_WAIT_TEMPERATURE CMD=M140 VALUE=80.0 BELOW=2 ABOVE=5",
            "_WAIT_TEMPERATURE CMD=M104 VALUE=245.0",
        ))
        self.assertFalse(any(
            command.startswith("_CHECK_BED_MESH") for command in bare))
        self.assertNotIn(
            "_WAIT_TEMPERATURE CMD=M104 VALUE=245.0 BELOW=5 ABOVE=5", bare)
        self.assertNotIn(
            "CLEAR_NOZZLE EXTRUDER_TEMP=245.0 BED_TEMP=80.0", bare)
        self.assertNotIn(
            'RESPOND PREFIX="//" MSG="Bed Mesh Validation Protection is '
            'enabled and will run after heating."', bare)
        self.assertIn(mesh_generated_publish(""), bare)

        # The missing-profile warning is a property of printing without a
        # mesh, not of the validation setup, and fires unconditionally.
        unguarded = run_start_print(
            zskip_leveling=True, profiles=(), profile_name="")
        self.assertTrue(any(
            command.startswith(
                'RESPOND PREFIX="warn" MSG="No bed mesh profile is loaded')
            for command in unguarded))

    def test_skip_leveling_loads_requested_profile_without_generating(self):
        # SKIP_LEVELING suppresses generation only. A requested profile is
        # still loaded over the standing one and, being loaded rather than
        # generated, stays under the validation protection.
        loaded = run_start_print(
            zskip_leveling=True, mesh="PLA_profile", mesh_validation=True)
        assert_order(self, loaded, (
            "BED_MESH_PROFILE LOAD=PLA_profile",
            "_WAIT_TEMPERATURE CMD=M104 VALUE=245.0 BELOW=5 ABOVE=5",
            "_CHECK_BED_MESH_AFTER_HEATING",
        ))
        self.assertFalse(any(
            command.startswith("BED_MESH_CALIBRATE") for command in loaded))
        self.assertIn(mesh_generated_publish(""), loaded)

        # A requested profile that does not exist cannot be generated under
        # skip; the standing profile stays in effect with an explicit warn.
        missing = run_start_print(
            zskip_leveling=True, mesh="missing", mesh_validation=True)
        self.assertIn(
            'RESPOND PREFIX="warn" MSG="Requested bed mesh \'missing\' '
            'not found. SKIP_LEVELING prevents generating a new one."',
            missing)
        self.assertEqual(mesh_actions(missing), ())
        assert_order(self, missing, (
            "_WAIT_TEMPERATURE CMD=M104 VALUE=245.0 BELOW=5 ABOVE=5",
            "_CHECK_BED_MESH_AFTER_HEATING",
        ))

        # And when nothing else is loaded either, the print runs without a
        # mesh and skips the validation preparation entirely.
        bare = run_start_print(
            zskip_leveling=True, mesh="missing", mesh_validation=True,
            profiles=(), profile_name="")
        self.assertFalse(any(
            command.startswith(("_CHECK_BED_MESH", "BED_MESH"))
            for command in bare))
        self.assertNotIn(
            "_WAIT_TEMPERATURE CMD=M104 VALUE=245.0 BELOW=5 ABOVE=5", bare)
        self.assertIn(
            'RESPOND PREFIX="//" MSG="Bed Mesh Validation Protection is '
            'skipped because no bed mesh profile is loaded."', bare)

    def test_parking_and_priming_follow_the_leveling_mode(self):
        kamp = run_start_print(use_kamp=True, disable_priming=False)
        assert_order(self, kamp, (
            "_KAMP_BED_MESH_CALIBRATE",
            "SMART_PARK",
            "LINE_PURGE",
            "SET_GCODE_VARIABLE MACRO=_LINE_PURGE "
            "VARIABLE=print_area_min VALUE=None",
        ))
        self.assertNotIn("G1 X110 Y110 F6000", kamp)
        self.assertNotIn("_CLEAR1", kamp)

        stock_kamp = run_start_print(
            display=0, use_kamp=True, disable_priming=False)
        self.assertNotIn("SMART_PARK", stock_kamp)
        self.assertIn("G1 X110 Y110 F6000", stock_kamp)
        self.assertIn("LINE_PURGE", stock_kamp)

        corner = run_start_print(disable_priming=False)
        self.assertNotIn("SMART_PARK", corner)
        self.assertIn("G1 X110 Y110 F6000", corner)
        self.assertIn("_CLEAR1", corner)
        self.assertNotIn("LINE_PURGE", corner)

        # SKIP_LEVELING disables the entire leveling flow including the
        # KAMP-specific parking and purge, even when the KAMP policy is on.
        skipped = run_start_print(
            zskip_leveling=True, use_kamp=True, disable_priming=False)
        self.assertNotIn("SMART_PARK", skipped)
        self.assertNotIn("LINE_PURGE", skipped)
        self.assertIn("G1 X110 Y110 F6000", skipped)
        self.assertIn("_CLEAR1", skipped)

        disabled = run_start_print(use_kamp=True, disable_priming=True)
        self.assertNotIn("LINE_PURGE", disabled)
        self.assertNotIn("_CLEAR1", disabled)

    def test_lifecycle_publishes_flags_and_preheats_without_wasting_heat(self):
        cold = run_start_print()
        assert_order(self, cold, (
            "_CONTEXT_BEGIN TYPE=print",
            "SET_GCODE_VARIABLE MACRO=_START_PRINT "
            "VARIABLE=print_active VALUE=True",
            "M140 S80.0",
            "G1 X110 Y110 F6000",
            "_WAIT_TEMPERATURE CMD=M140 VALUE=80.0 BELOW=2 ABOVE=5",
            "_WAIT_TEMPERATURE CMD=M104 VALUE=245.0",
            "SET_GCODE_VARIABLE MACRO=_START_PRINT "
            "VARIABLE=print_started VALUE=True",
            "_CONTEXT_STATE NAME=PRINTING",
        ))
        self.assertFalse(
            any(command.startswith("M104 ") for command in cold))

        hold = run_start_print(extruder_temperature=200.0)
        self.assertIn("M104 S200.0", hold)
        self.assertNotIn("M104 S245.0", hold)

        hot = run_start_print(extruder_temperature=245.0)
        self.assertIn("M104 S245.0", hot)

    def test_environment_options_react_during_start(self):
        run = run_start_print(
            chamber_light_mode="PRINT_ONLY", midi_start="startup.mid",
            weight_check=True, load_zoffset=True)
        assert_order(self, run, (
            "LED_ON",
            "_SET_GCODE_OFFSET Z='0.1'",
            "_WAIT_TEMPERATURE CMD=M104 VALUE=245.0",
            "PLAY_MIDI FILE=startup.mid",
            "LOAD_CELL_TARE",
        ))

        verified = run_start_print(check_md5=1, preparation_done=False)
        assert_order(self, verified, (
            '_CONTEXT_STATE NAME="CHECKING FILE"',
            "CHECK_MD5",
            "M140 S80.0",
        ))

    def test_prepare_aborts_when_the_filament_sensor_reports_runout(self):
        with self.assertRaises(MacroActionError):
            run_start_print(
                filament_switch_sensor=True, filament_detected=False)

        started = run_start_print(
            filament_switch_sensor=True, filament_detected=True)
        self.assertIn(
            "SET_GCODE_VARIABLE MACRO=_START_PRINT "
            "VARIABLE=print_started VALUE=True", started)

    def test_deprecated_zoffset_arguments_abort_the_print(self):
        for parameter, overrides in (
                ("SKIP_ZOFFSET", {"zskip_zoffset": True}),
                ("Z_OFFSET", {"zzoffset": 0.2})):
            with self.subTest(parameter=parameter):
                with self.assertRaises(MacroActionError):
                    run_start_print(**overrides)


class StartPrintDefaultMeshTest(unittest.TestCase):
    """Lifecycle of the persistent default mesh around _START_PRINT."""

    def test_boot_prepare_loads_the_persistent_auto_profile(self):
        result = render_macro(
            HEADLESS, "prepare_headless", section="delayed_gcode")

        self.assertEqual(result.commands, (
            "BED_MESH_CLEAR", "BED_MESH_PROFILE LOAD=auto"))

    def test_wrapper_preloads_auto_and_start_print_reuses_it(self):
        wrapper, full = run_headless_start_print(
            {"EXTRUDER_TEMP": 230, "BED_TEMP": 65})

        assert_order(self, wrapper, (
            "SET_FILAMENT_SENSOR SENSOR=e0_sensor ENABLE=0",
            "BED_MESH_CLEAR",
            "BED_MESH_PROFILE LOAD=auto",
            "_BACKLIGHT",
            "_START_PRINT",
        ))
        # The pre-loaded persistent profile is the only mesh action in the
        # whole flow; _START_PRINT reuses it without calibrating.
        self.assertEqual(mesh_actions(full), ("BED_MESH_PROFILE LOAD=auto",))
        self.assertIn(mesh_generated_publish(""), full)

    def test_first_print_without_auto_generates_the_persistent_profile(self):
        wrapper, full = run_headless_start_print(
            {"EXTRUDER_TEMP": 230, "BED_TEMP": 65},
            profiles=("PLA_profile",), profile_name="")

        self.assertFalse(any(
            command.startswith("BED_MESH") for command in wrapper))
        self.assertEqual(mesh_actions(full),
                         ('BED_MESH_CALIBRATE PROFILE="auto"',))
        self.assertIn(mesh_generated_publish("auto"), full)

    def test_feather_rebuild_override_drives_the_staged_request(self):
        wrapper, full = run_headless_start_print(
            {"EXTRUDER_TEMP": 230, "BED_TEMP": 65, "SKIP_LEVELING": 1},
            feather_force_leveling=True, feather_mesh_name=None)

        # The one-print Feather override clears the slicer's SKIP_LEVELING
        # and requests an unnamed full rebuild, preloaded auto included.
        assert_order(self, wrapper, (
            "SET_GCODE_VARIABLE MACRO=_START_PRINT "
            "VARIABLE=zforce_leveling VALUE=1",
            "SET_GCODE_VARIABLE MACRO=_START_PRINT "
            "VARIABLE=zskip_leveling VALUE=0",
            "SET_GCODE_VARIABLE MACRO=_START_PRINT "
            "VARIABLE=zmesh VALUE='\"\"'",
            "BED_MESH_PROFILE LOAD=auto",
            "_START_PRINT",
        ))
        self.assertEqual(mesh_actions(full), (
            "BED_MESH_PROFILE LOAD=auto",
            'BED_MESH_CALIBRATE PROFILE="default"',
        ))
        self.assertIn(mesh_generated_publish("default"), full)

        _, named = run_headless_start_print(
            {"EXTRUDER_TEMP": 230, "BED_TEMP": 65},
            feather_force_leveling=True, feather_mesh_name="auto")
        self.assertEqual(mesh_actions(named), (
            "BED_MESH_PROFILE LOAD=auto",
            'BED_MESH_CALIBRATE PROFILE="auto"',
        ))

    def test_skip_leveling_print_still_loads_the_requested_profile(self):
        wrapper, full = run_headless_start_print(
            {"EXTRUDER_TEMP": 230, "BED_TEMP": 65, "SKIP_LEVELING": 1,
             "MESH": "PLA_profile"})

        # The wrapper preloads the persistent default, then the explicit
        # slicer request wins without any calibration in between.
        self.assertEqual(mesh_actions(wrapper),
                         ("BED_MESH_PROFILE LOAD=auto",))
        self.assertEqual(mesh_actions(full), (
            "BED_MESH_PROFILE LOAD=auto",
            "BED_MESH_PROFILE LOAD=PLA_profile",
        ))

    def test_wrapper_preload_does_not_shadow_an_explicit_profile_request(self):
        wrapper, full = run_headless_start_print(
            {"EXTRUDER_TEMP": 230, "BED_TEMP": 65, "MESH": "PLA_profile"})

        self.assertEqual(mesh_actions(wrapper),
                         ("BED_MESH_PROFILE LOAD=auto",))
        self.assertEqual(mesh_actions(full), (
            "BED_MESH_PROFILE LOAD=auto",
            "BED_MESH_PROFILE LOAD=PLA_profile",
        ))

    def test_end_print_removes_only_the_temporary_default_profile(self):
        temporary = render_macro(HEADLESS, "_COMMON_END_PRINT", printer={
            "mod_params": {"variables": {"stop_motor": 0}},
            "bed_mesh": {"profile_name": "default"},
        })
        persistent = render_macro(HEADLESS, "_COMMON_END_PRINT", printer={
            "mod_params": {"variables": {"stop_motor": 0}},
            "bed_mesh": {"profile_name": "auto"},
        })

        assert_order(self, temporary.commands, (
            "BED_MESH_CLEAR", "BED_MESH_PROFILE REMOVE=default"))
        self.assertFalse(any(
            command.startswith("BED_MESH_PROFILE REMOVE")
            for command in persistent.commands))


class LevelingPreparationMacroTest(unittest.TestCase):
    @staticmethod
    def _printer(disable_cleaning=False):
        return {"mod_params": {"variables": {
            "safe_z": 10,
            "clear_cooldown_temp": 150.0,
            "disable_cleaning": disable_cleaning,
        }}}

    def test_hot_nozzle_is_cleaned_before_leveling(self):
        result = render_macro(
            BASE, "_PREPARE_LEVELING", printer=self._printer(),
            params={"EXTRUDER_TEMP": 240, "BED_TEMP": 80})

        self.assertIn("CLEAR_NOZZLE EXTRUDER_TEMP=240.0 BED_TEMP=80.0",
                      result.commands)
        self.assertFalse(any(command.startswith("_WAIT_TEMPERATURE")
                             for command in result.commands))
        self.assertNotIn("LOAD_CELL_TARE", result.commands)

    def test_uncleanable_nozzle_heats_under_managed_waits(self):
        # A cold or disabled-clean nozzle must not be wiped: the heaters
        # are brought to safe levels through managed waits and the load
        # cell is tared for the coming probes.
        cases = (
            ("disabled by parameter", True, 240, 150.0,
             "Nozzle cleaning is disabled due to 'disable_cleaning' "
             "parameter"),
            ("below cooldown threshold", False, 140, 140.0,
             "Nozzle cleaning was skipped because nozzle temperature is "
             "below cooldown threshold"),
        )
        for label, disabled, extruder, wait_target, message in cases:
            with self.subTest(label=label):
                result = render_macro(
                    BASE, "_PREPARE_LEVELING",
                    printer=self._printer(disable_cleaning=disabled),
                    params={"EXTRUDER_TEMP": extruder, "BED_TEMP": 80})

                assert_order(self, result.commands, (
                    'RESPOND PREFIX="info" MSG="%s"' % message,
                    "M140 S80.0",
                    "_HOME_IF_NEEDED",
                    "_CONTEXT_STATE NAME=HEATING",
                    "_WAIT_TEMPERATURE CMD=M140 VALUE=80.0 BELOW=2 ABOVE=3",
                    "_WAIT_TEMPERATURE CMD=M104 VALUE=%s BELOW=2 ABOVE=3"
                    % wait_target,
                    "LOAD_CELL_TARE",
                ))
                self.assertFalse(any(command.startswith("CLEAR_NOZZLE")
                                     for command in result.commands))

    def test_nozzle_cleaning_restores_the_mesh_it_cleared(self):
        # PROBE reports absolute Z while G1 moves relative to the active
        # mesh, so cleaning must clear it first and put it back afterwards.
        printer = self._printer()
        restored = render_macro(
            BASE, "CLEAR_NOZZLE", printer=dict(
                printer, bed_mesh={"profile_name": "auto"}),
            params={"EXTRUDER_TEMP": 240, "BED_TEMP": 80})
        assert_order(self, restored.commands, (
            "BED_MESH_CLEAR",
            "M106 P0 S0",
            'BED_MESH_PROFILE LOAD="auto"',
            "RESTORE_GCODE_STATE NAME=_clear_nozzle",
        ))

        bare = render_macro(
            BASE, "CLEAR_NOZZLE", printer=dict(
                printer, bed_mesh={"profile_name": ""}),
            params={"EXTRUDER_TEMP": 240, "BED_TEMP": 80})
        self.assertIn("BED_MESH_CLEAR", bare.commands)
        self.assertFalse(any(command.startswith("BED_MESH_PROFILE")
                             for command in bare.commands))


class BedMeshValidationMacroTest(unittest.TestCase):
    def test_heated_validation_retract_samples_the_extruder_at_call_time(self):
        # Rendering _START_PRINT samples the printer state once, before any
        # command executes; deferring the retract decision to this macro
        # makes it observe the extruder after the temperature wait.
        for can_extrude, retract in ((True, 1), (False, 0)):
            with self.subTest(can_extrude=can_extrude):
                result = render_macro(
                    BASE, "_CHECK_BED_MESH_AFTER_HEATING",
                    printer={"extruder": {"can_extrude": can_extrude}})

                self.assertEqual(
                    result.commands, ("_CHECK_BED_MESH RETRACT=%d" % retract,))

    MESH = (
        (0.01, 0.02, 0.03, 0.04, 0.05),
        (0.06, 0.07, 0.08, 0.09, 0.10),
        (0.11, 0.12, 0.13, 0.14, 0.15),
        (0.16, 0.17, 0.18, 0.19, 0.20),
        (0.21, 0.22, 0.23, 0.24, 0.25),
    )

    @staticmethod
    def _printer(mesh, mesh_min, mesh_max, profile="auto"):
        return {
            "mod_params": {"variables": {
                "safe_z": 10, "bed_mesh_validation_tolerance": 0.2}},
            "bed_mesh": {"profile_name": profile, "mesh_matrix": mesh,
                         "mesh_min": mesh_min, "mesh_max": mesh_max},
        }

    def test_without_loaded_profile_the_check_warns_and_probes_nothing(self):
        result = render_macro(BASE, "_CHECK_BED_MESH", printer=self._printer(
            self.MESH, [-100, -100], [100, 100], profile=""))

        assert_order(self, result.commands, (
            "_CONTEXT_BEGIN TYPE=mesh_validation",
            'RESPOND PREFIX="warn" MSG="Skipping bed mesh check; '
            'no mesh loaded."',
            "_CONTEXT_END",
        ))
        self.assertFalse(any(command.startswith((
            "_CHECK_BED_MESH_PROBE", "_CHECK_BED_MESH_VERIFY",
            "_CHECK_BED_MESH_HANDLE_FAIL")) for command in result.commands))

    def test_probes_edge_points_and_matches_them_to_mesh_cells(self):
        result = render_macro(
            BASE, "_CHECK_BED_MESH",
            printer=self._printer(self.MESH, [-100, -100], [100, 100]),
            params={"RETRACT": 1})

        # Points are inset 5mm from the mesh bounds and skip the middle
        # column/row to keep plastic traces off the print area; every
        # verification expects the mesh cell its probe is standing on.
        self.assertEqual(
            tuple(command for command in result.commands
                  if command.startswith(("_CHECK_BED_MESH_PROBE",
                                         "_CHECK_BED_MESH_VERIFY"))),
            (
                "_CHECK_BED_MESH_PROBE X=-105 Y=52.5 RETRACT=1",
                "_CHECK_BED_MESH_VERIFY EXPECTED=0.16 TOLERANCE=0.2",
                "_CHECK_BED_MESH_PROBE X=-105 Y=-105 RETRACT=1",
                "_CHECK_BED_MESH_VERIFY EXPECTED=0.21 TOLERANCE=0.2",
                "_CHECK_BED_MESH_PROBE X=52.5 Y=-105 RETRACT=1",
                "_CHECK_BED_MESH_VERIFY EXPECTED=0.04 TOLERANCE=0.2",
                "_CHECK_BED_MESH_PROBE X=105 Y=-105 RETRACT=1",
                "_CHECK_BED_MESH_VERIFY EXPECTED=0.25 TOLERANCE=0.2",
                "_CHECK_BED_MESH_PROBE X=105 Y=52.5 RETRACT=1",
                "_CHECK_BED_MESH_VERIFY EXPECTED=0.2 TOLERANCE=0.2",
            ))
        assert_order(self, result.commands, (
            "SAVE_GCODE_STATE NAME=_check_bed_mesh",
            "RESTORE_GCODE_STATE NAME=_check_bed_mesh",
            "_CHECK_BED_MESH_HANDLE_FAIL COUNT=5",
        ))

    def test_probes_stay_within_the_physical_bed(self):
        result = render_macro(BASE, "_CHECK_BED_MESH", printer=self._printer(
            [[0.0] * 3 for _ in range(3)], [-120, -120], [120, 120]))

        probes = tuple(command for command in result.commands
                       if command.startswith("_CHECK_BED_MESH_PROBE"))
        self.assertEqual(len(probes), 5)
        for command in probes:
            coordinates = dict(
                token.split("=") for token in command.split()[1:3])
            for axis in ("X", "Y"):
                self.assertLessEqual(float(coordinates[axis]), 110)
                self.assertGreaterEqual(float(coordinates[axis]), -110)

    def test_verification_latches_failure_and_accumulates_deviation(self):
        def verify(diff_sum, tolerance):
            return render_macro(
                BASE, "_CHECK_BED_MESH_VERIFY",
                printer={
                    "configfile": {"config": {"probe": {"z_offset": 0.25}}},
                    "probe": {"last_z_result": 0.5},
                    "gcode_macro _CHECK_BED_MESH": macro_status(
                        BASE, "_CHECK_BED_MESH", probe_diff_sum=diff_sum),
                }, params={"EXPECTED": 0.5, "TOLERANCE": tolerance})

        # The expected height folds in the probe offset: 0.5 + 0.25 vs the
        # probed 0.5 leaves a 0.25mm deviation.
        self.assertEqual(verify(0, 0.3).commands, (
            "SET_GCODE_VARIABLE MACRO=_CHECK_BED_MESH "
            "VARIABLE='probe_diff_sum' VALUE=0.25",
            'RESPOND PREFIX="//" MSG="Probe result match. '
            'Difference: 0.25 mm."',
        ))

        self.assertEqual(verify(0.5, 0.2).commands, (
            "SET_GCODE_VARIABLE MACRO=_CHECK_BED_MESH "
            "VARIABLE='probe_diff_sum' VALUE=0.75",
            "SET_GCODE_VARIABLE MACRO=_CHECK_BED_MESH "
            "VARIABLE='check_failed' VALUE=True",
            'RESPOND PREFIX="!!" MSG="Probe result doesn\'t match. '
            'Expected difference within 0.20 mm, but got 0.25 mm."',
        ))

    def test_handle_fail_cancels_the_print_or_reports_tolerance(self):
        failed = render_macro(BASE, "_CHECK_BED_MESH_HANDLE_FAIL", printer={
            "gcode_macro _CHECK_BED_MESH": macro_status(
                BASE, "_CHECK_BED_MESH", check_failed=True,
                probe_diff_sum=0.75),
        }, params={"COUNT": 5})
        self.assertEqual(failed.commands, (
            '_RAISE_WITH_PRINT_CANCEL MSG="Bed mesh checking is failed. '
            'Avg. diff: 0.15 mm"',))

        passed = render_macro(BASE, "_CHECK_BED_MESH_HANDLE_FAIL", printer={
            "gcode_macro _CHECK_BED_MESH": macro_status(
                BASE, "_CHECK_BED_MESH", check_failed=False,
                probe_diff_sum=0.5),
        }, params={"COUNT": 5})
        self.assertEqual(passed.commands, (
            'RESPOND PREFIX="//" MSG="Bed Mesh is within tolerance: '
            '0.10 mm"',))


if __name__ == "__main__":
    unittest.main()
