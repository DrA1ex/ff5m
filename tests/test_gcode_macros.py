## Behavioral tests for Forge-X G-code macros.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import pathlib
import unittest

from tests.gcode_macro_harness import (
    MacroActionError, load_macro, render_macro)


ROOT = pathlib.Path(__file__).parents[1]
BASE = ROOT / "macros" / "base.cfg"
CLIENT = ROOT / "macros" / "client.cfg"
MATERIAL = ROOT / "config" / "material.cfg"
SMART_PARK = ROOT / "KAMP" / "Smart_Park.cfg"


def assert_order(test, commands, expected):
    positions = [commands.index(command) for command in expected]
    test.assertEqual(positions, sorted(positions))


def macro_status(path, name, **overrides):
    status = dict(load_macro(path, name).variables)
    status.update(overrides)
    return status


def material_config():
    return macro_status(MATERIAL, "_MATERIAL_CONFIG")


class WorkflowMacroTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
