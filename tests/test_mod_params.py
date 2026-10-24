## Behavioral tests for Forge-X parameter validation and persistence.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import configparser
import importlib.util
import json
import pathlib
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "validated_mod_params", ROOT / ".py/klipper/plugins/mod_params.py")
MOD_PARAMS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD_PARAMS)


class ModParameterTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = pathlib.Path(directory.name) / "variables.cfg"
        self.manager = MOD_PARAMS.ModParamManagement.__new__(
            MOD_PARAMS.ModParamManagement)
        self.manager.declaration = str(ROOT / "mod_params.json")
        self.manager.printer = SimpleNamespace(command_error=RuntimeError)
        self.manager.gcode = SimpleNamespace(error=RuntimeError)
        self.manager.reactor = mock.Mock()
        self.manager._variables_lock = threading.RLock()
        self.manager.filename = str(self.path)
        self.manager._load_declaration()
        self.manager._reload()

    def reload(self, text):
        self.path.write_text("[Variables]\n" + text, encoding="utf-8")
        self.manager._reload()

    def test_one_corrupted_value_does_not_block_other_parameters(self):
        for broken in ("nan", "1e309", "%(broken)s", "[invalid"):
            with self.subTest(broken=broken), self.assertLogs(level="ERROR"):
                self.reload("z_offset = %s\npause_z_min = 75.0\n" % broken)
            self.assertEqual(self.manager.variables["z_offset"], 0.0)
            self.assertEqual(self.manager.variables["pause_z_min"], 75.0)

    def test_percent_text_round_trips_without_interpolation(self):
        text = "50% complete %(literal)s"
        self.manager.set_value("midi_start", text)
        self.manager._reload()
        self.assertEqual(self.manager.variables["midi_start"], text)

    def test_preview_back_to_files_defaults_on_and_round_trips_through_storage(self):
        self.assertTrue(self.manager.variables["feather_preview_back_to_files"])
        for enabled in (False, True):
            self.manager.set_value("feather_preview_back_to_files", int(enabled))
            self.manager._reload()
            self.assertIs(self.manager.variables["feather_preview_back_to_files"], enabled)

    def test_related_values_commit_once_and_notify_after_the_whole_snapshot(self):
        values = {"feather_preview_yaw": -45, "feather_preview_pitch": 20, "feather_preview_scale": 1.5}
        with mock.patch.object(self.manager, "_save_all", wraps=self.manager._save_all) as save:
            result = self.manager.set_values(values)
        save.assert_called_once()
        self.assertEqual(result, values)
        snapshots = []
        with mock.patch.object(self.manager, "_notify_committed_change",
                               side_effect=lambda param: snapshots.append(dict(self.manager.variables))):
            for call in self.manager.reactor.register_callback.call_args_list:
                call.args[0](0)
        self.assertEqual(len(snapshots), 3)
        self.assertTrue(all(all(snapshot[key] == value for key, value in values.items()) for snapshot in snapshots))
        self.manager._reload()
        self.assertTrue(all(self.manager.variables[key] == value for key, value in values.items()))

    def test_invalid_batch_and_storage_failure_preserve_every_value_and_hook(self):
        self.manager.set_value("feather_preview_yaw", 10)
        before, stored = dict(self.manager.variables), self.path.read_bytes()
        self.manager.reactor.reset_mock()
        with self.assertRaises(ValueError):
            self.manager.set_values({"feather_preview_yaw": 20, "feather_preview_pitch": 100})
        self.assertEqual(self.manager.variables, before)
        self.assertEqual(self.path.read_bytes(), stored)
        with mock.patch.object(self.manager, "_save_all", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.manager.set_values({"feather_preview_yaw": 20, "feather_preview_pitch": 25})
        self.assertEqual(self.manager.variables, before)
        self.assertEqual(self.path.read_bytes(), stored)
        self.manager.reactor.register_callback.assert_not_called()

    def test_density_ranges_and_defaults_are_style_specific(self):
        self.assertEqual(self.manager.variables["feather_preview_stacked_density"], 100)
        self.assertEqual(self.manager.variables["feather_preview_points_density"], 30)
        for key, accepted, rejected in (
                ("feather_preview_stacked_density", (100, 150, 200), (99, 201)),
                ("feather_preview_points_density", (5, 30, 100), (4, 101))):
            for value in accepted:
                self.manager.set_value(key, value)
                self.assertEqual(self.manager.variables[key], value)
            for value in rejected:
                with self.assertRaises(ValueError):
                    self.manager.set_value(key, value)

    def test_status_bar_modes_default_to_metrics_and_round_trip_through_storage(self):
        self.assertEqual(self.manager.variables['feather_status_bar'], 'METRICS')
        param = self.manager.params_map['feather_status_bar']
        self.assertEqual([option.name for option in param.type], ['METRICS', 'COLORFUL', 'VANILLA'])
        for style in ('COLORFUL', 'VANILLA', 'METRICS'):
            self.manager.set_value('feather_status_bar', style)
            self.manager._reload()
            self.assertEqual(self.manager.variables['feather_status_bar'], style)
        with self.assertRaises(ValueError):
            self.manager.set_value('feather_status_bar', 'UNKNOWN')
        self.assertEqual(self.manager.variables['feather_status_bar'], 'METRICS')

    def test_committed_status_bar_event_repaints_without_waiting_for_a_status_tick(self):
        from tests.test_feather_screen import FEATHER, DeferredReactor, StatusObject
        from tests.feather_render_test_helper import RenderCapture
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.print_state = FEATHER.PrintState.IDLE
        controller.params = self.manager
        controller.renderer = FEATHER.FeatherRenderer()
        capture = RenderCapture(controller.renderer)
        controller.reactor = self.manager.reactor = DeferredReactor()
        controller.extruder = StatusObject(dict(temperature=42, target=0))
        controller.heater_bed = StatusObject(dict(temperature=30, target=0))
        controller.print_stats = StatusObject(dict(state='cancelled'))
        controller.virtual_sdcard = SimpleNamespace(is_active=lambda: False)
        controller.network_status = dict(ip='192.168.2.124')
        controller._update_cycle = mock.Mock(side_effect=AssertionError('Unexpected full status tick'))
        events = []

        def send_event(name):
            events.append(name)
            controller._refresh_status_bar()

        self.manager.printer.send_event = send_event
        self.manager.changes_gcode_present = False
        controller._refresh_status_bar()
        self.assertTrue(capture.latest.has_text('CPU --%'))
        self.manager.set_value('feather_status_bar', 'COLORFUL')
        self.assertTrue(capture.latest.has_text('CPU --%'))
        controller.reactor.run_until(controller.reactor.now)
        self.assertEqual(events, ['mod_params:changed'])
        self.assertTrue(capture.latest.has_text('NOZZLE 42/0C'))
        self.assertFalse(capture.latest.has_text('CPU --%'))
        before = len(capture.frames)
        controller.print_state = FEATHER.PrintState.INACTIVE
        controller._refresh_status_bar()
        self.assertEqual(len(capture.frames), before)

    def test_structural_ini_errors_preserve_the_previous_snapshot(self):
        previous = dict(self.manager.variables)
        self.path.write_text("[Variables\nz_offset = 0\n", encoding="utf-8")
        with self.assertLogs(level="ERROR"):
            with self.assertRaisesRegex(RuntimeError, "Unable to parse variable file"):
                self.manager._reload()
        self.assertEqual(self.manager.variables, previous)

    def test_current_key_wins_even_when_damaged(self):
        for text in ("m600_z_min = 75\npause_z_min = broken\n",
                     "pause_z_min = broken\nm600_z_min = 75\n"):
            with self.subTest(text=text), self.assertLogs(level="INFO"):
                self.reload(text)
            self.assertEqual(self.manager.variables["pause_z_min"],
                             self.manager.params_map["pause_z_min"].default)

    def test_damaged_legacy_value_does_not_block_valid_neighbors(self):
        with self.assertLogs(level="ERROR"):
            self.reload("m600_z_min = broken\nz_offset = 0.125\nunknown = 42\n")
        self.assertEqual(self.manager.variables["pause_z_min"],
                         self.manager.params_map["pause_z_min"].default)
        self.assertEqual(self.manager.variables["z_offset"], 0.125)
        self.assertNotIn("unknown", self.manager.variables)

    def test_mapped_migration_rejects_unmapped_values(self):
        with self.assertLogs(level="INFO"):
            self.reload("display_off = 1\npause_z_min = 75\n")
        self.assertEqual(self.manager.variables["display"],
                         self.manager.params_map["display"].type.FEATHER.value)
        with self.assertLogs(level="ERROR"):
            self.reload("display_off = 2\npause_z_min = 75\n")
        self.assertEqual(self.manager.variables["display"],
                         self.manager._load_param(self.manager.params_map["display"], None))
        self.assertEqual(self.manager.variables["pause_z_min"], 75)

    def test_missing_variables_section_loads_defaults(self):
        self.manager.set_value("pause_z_min", 75)
        self.path.write_text("[Other]\npause_z_min = 80\n", encoding="utf-8")
        self.manager._reload()
        self.assertEqual(self.manager.variables["pause_z_min"],
                         self.manager.params_map["pause_z_min"].default)

    def test_invalid_default_preserves_previous_snapshot(self):
        self.manager.set_value("pause_z_min", 75)
        previous = dict(self.manager.variables)
        self.manager.params_map["pause_z_min"].default = float("nan")
        self.path.write_text("[Variables]\n", encoding="utf-8")
        with self.assertLogs(level="ERROR"):
            with self.assertRaisesRegex(RuntimeError, "Unable to parse variable file"):
                self.manager._reload()
        self.assertEqual(self.manager.variables, previous)

    def test_ini_defaults_load_without_a_variables_section(self):
        self.path.write_text("[DEFAULT]\npause_z_min = 75\n", encoding="utf-8")
        self.manager._reload()
        self.assertEqual(self.manager.variables["pause_z_min"], 75)

    def test_invalid_inputs_leave_file_state_and_hooks_unchanged(self):
        self.manager.set_value("pause_z_min", 75)
        before = self.path.read_bytes()
        self.manager.reactor.reset_mock()
        for key, values in (
                ("z_offset", ("nan", "inf", "-inf", "broken")),
                ("safe_z", (0, -1, "nan", "inf")),
                ("timelapse_every_percent", (0, 100))):
            for value in values:
                with self.subTest(key=key, value=value):
                    old = dict(self.manager.variables)
                    with self.assertRaises(ValueError):
                        self.manager.set_value(key, value)
                    self.assertEqual(self.manager.variables, old)
                    self.assertEqual(self.path.read_bytes(), before)
        self.manager.reactor.register_callback.assert_not_called()

    def test_gcode_and_ui_apply_the_same_numeric_validation(self):
        command = SimpleNamespace(get=lambda key, default=None: {
            "PARAM": "timelapse_every_percent", "VALUE": "100"
        }.get(key, default), error=RuntimeError)
        with self.assertRaises(RuntimeError):
            self.manager.cmd_SET_MOD(command)
        with self.assertRaises(ValueError):
            self.manager.set_value("timelapse_every_percent", 100)

    def test_moonraker_selection_warns_to_restart_in_both_directions(self):
        messages = []
        self.manager.gcode.respond_raw = messages.append
        for value in ("1", "0"):
            messages.clear()
            command = SimpleNamespace(get=lambda key, default=None: {
                "PARAM": "disable_moonraker", "VALUE": value
            }.get(key, default), error=RuntimeError, respond_raw=messages.append)
            self.manager.cmd_SET_MOD(command)
            self.assertIn("Restart the printer to apply this change.", messages)
            self.assertEqual(self.manager.variables["disable_moonraker"], value == "1")

    def test_offsets_outside_two_mm_remain_supported(self):
        for value in (-5.0, 5.0):
            self.manager.set_value("z_offset", value)
            self.manager._reload()
            self.assertEqual(self.manager.variables["z_offset"], value)

    def test_preview_scale_round_trips_and_rejects_invalid_sizes_without_changing_storage(self):
        for value in (0.25, 1.75, 3.0):
            self.manager.set_value("feather_preview_scale", value)
            self.manager._reload()
            self.assertEqual(self.manager.variables["feather_preview_scale"], value)
        before = self.path.read_bytes()
        for value in (0, 0.24, 3.01, "nan", "inf", "broken"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.manager.set_value("feather_preview_scale", value)
            self.assertEqual(self.path.read_bytes(), before)
            self.assertEqual(self.manager.variables["feather_preview_scale"], 3.0)

    def test_preview_camera_accepts_signed_positions_and_wraps_legacy_turns(self):
        from feather.model_preview import model_settings
        for yaw, pitch, expected_yaw in ((-180, -90, -180), (0, 30, 0), (180, 90, 180), (270, 0, -90)):
            self.manager.set_value("feather_preview_yaw", yaw)
            self.manager.set_value("feather_preview_pitch", pitch)
            self.manager._reload()
            settings = model_settings(lambda key, default: self.manager.variables.get(key, default))
            self.assertEqual(settings[2:4], (expected_yaw, pitch))
        for key, value in (("feather_preview_yaw", -181), ("feather_preview_pitch", -91),
                           ("feather_preview_pitch", 91)):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.manager.set_value(key, value)

    def test_all_declared_defaults_pass_validation(self):
        for param in self.manager.params:
            with self.subTest(key=param.key):
                self.assertEqual(self.manager._load_param(param, None),
                                 self.manager._load_param(param, param.default))

    def test_bounded_values_round_trip_and_invalid_stored_values_fall_back(self):
        self.manager.set_value("timelapse_every_percent", 0.1)
        self.manager.set_value("pause_z_min", 0)
        self.manager._reload()
        self.assertEqual(self.manager.variables["timelapse_every_percent"], 0.1)
        self.assertEqual(self.manager.variables["pause_z_min"], 0)
        with self.assertLogs(level="ERROR"):
            self.reload("timelapse_every_percent = 100\npause_z_min = 75\n")
        self.assertEqual(self.manager.variables["timelapse_every_percent"],
                         self.manager.params_map["timelapse_every_percent"].default)
        self.assertEqual(self.manager.variables["pause_z_min"], 75)

    def test_successful_save_publishes_a_complete_snapshot(self):
        self.manager.set_value("pause_z_min", 75)
        before = self.path.read_bytes()
        replace = MOD_PARAMS.os.replace
        observed = []

        def publish(source, destination):
            self.assertEqual(self.path.read_bytes(), before)
            parser = configparser.ConfigParser(interpolation=None)
            parser.read(source)
            self.assertEqual(parser.getfloat("Variables", "pause_z_min"), 80)
            self.assertEqual(set(parser["Variables"]), set(self.manager.variables))
            observed.append(destination)
            replace(source, destination)

        with mock.patch.object(MOD_PARAMS.os, "replace", side_effect=publish):
            self.manager.set_value("pause_z_min", 80)
        self.assertEqual(observed, [str(self.path)])
        self.manager._reload()
        self.assertEqual(self.manager.variables["pause_z_min"], 80)

    def test_failed_write_or_replace_preserves_file_and_rolls_back_memory(self):
        self.manager.set_value("pause_z_min", 75)
        before = self.path.read_bytes()
        self.manager.reactor.reset_mock()

        def partial_write(parser, stream):
            stream.write("[Variables]\npause_z_min = ")
            raise OSError("disk full")

        failures = (
            mock.patch.object(MOD_PARAMS.configparser.ConfigParser, "write",
                              new=partial_write),
            mock.patch.object(MOD_PARAMS.os, "replace",
                              side_effect=OSError("replace failed")),
        )
        for failure in failures:
            with self.subTest(failure=failure), failure, self.assertLogs(level="ERROR"):
                with self.assertRaisesRegex(RuntimeError, "Unable to save"):
                    self.manager.set_value("pause_z_min", 80)
            self.assertEqual(self.manager.variables["pause_z_min"], 75)
            self.assertEqual(self.path.read_bytes(), before)
            self.assertEqual(list(self.path.parent.iterdir()), [self.path])
        self.manager.reactor.register_callback.assert_not_called()


class ParameterVisibilityTest(unittest.TestCase):
    def setUp(self):
        from tests.test_feather_screen import MOD_UI

        self.visible_parameters = MOD_UI.visible_parameters
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = pathlib.Path(directory.name) / "declaration.json"
        declaration = {
            "enums": {"Mode": {"type": "int", "values": {"FIRST": 7, "SECOND": 19, "THIRD": 42}}},
            "parameters": [
                {"key": "mode", "type": "Mode", "default": "FIRST", "label": "Mode"},
                {"key": "enabled", "type": "bool", "default": 1, "label": "Enabled"},
                {"key": "detail", "type": "int", "default": 9, "label": "Detail"},
                {"key": "other", "type": "bool", "default": 0, "label": "Other"},
                {"key": "alternative", "type": "str", "default": "saved", "label": "Alternative"},
                {"key": "explicit", "type": "bool", "default": 1, "label": "Explicit",
                 "ui": {"visible_if": {"parameter": "mode", "operator": "equals", "value": "SECOND"}}},
                {"key": "hidden", "type": "bool", "default": 1, "label": "Hidden", "hidden": True},
            ],
            "ui": {"strict_visibility_dependencies": [
                {"parameter": "enabled", "depends_on": "mode", "operator": "equals",
                 "value": ["FIRST", "SECOND"]},
                {"parameter": "detail", "depends_on": "enabled", "operator": "equals", "value": True},
                {"parameter": "alternative", "depends_on": ["enabled", "other"],
                 "operator": "equals", "value": True},
            ]},
        }
        path.write_text(json.dumps(declaration), encoding="utf-8")
        self.manager = MOD_PARAMS.ModParamManagement.__new__(MOD_PARAMS.ModParamManagement)
        self.manager.declaration = str(path)
        self.manager._load_declaration()
        self.params = self.manager.params_map
        self.manager.variables = {param.key: self.manager._load_param(param, None)
                                  for param in self.manager.params}

    def test_enum_conditions_use_member_names_and_accept_alternative_values(self):
        for mode in self.params["mode"].type:
            with self.subTest(mode=mode):
                self.manager.variables["mode"] = mode.value
                visible = self.visible_parameters(self.manager)
                self.assertEqual(self.params["enabled"] in visible, mode.name in ("FIRST", "SECOND"))
                self.assertEqual(self.params["explicit"] in visible, mode.name == "SECOND")
        self.manager.variables["mode"] = -1
        self.assertNotIn(self.params["enabled"], self.visible_parameters(self.manager))
        self.assertNotIn(self.params["explicit"], self.visible_parameters(self.manager))

    def test_hidden_parent_hides_descendants_even_when_its_saved_value_matches(self):
        for mode in self.params["mode"].type:
            for enabled in (False, True):
                with self.subTest(mode=mode, enabled=enabled):
                    self.manager.variables.update(mode=mode.value, enabled=enabled)
                    visible = self.visible_parameters(self.manager)
                    self.assertEqual(self.params["detail"] in visible,
                                     enabled and mode.name in ("FIRST", "SECOND"))
                    self.assertNotIn(self.params["hidden"], visible)
                    self.assertIn(self.params["other"], visible)

    def test_any_matching_visible_parent_can_reveal_a_dependent(self):
        for mode in self.params["mode"].type:
            for enabled in (False, True):
                for other in (False, True):
                    with self.subTest(mode=mode, enabled=enabled, other=other):
                        self.manager.variables.update(mode=mode.value, enabled=enabled, other=other)
                        self.assertEqual(self.params["alternative"] in self.visible_parameters(self.manager),
                                         other or (enabled and mode.name in ("FIRST", "SECOND")))

    def test_settings_pages_refresh_visibility_without_changing_values(self):
        from tests.test_feather_screen import mod_controller

        feature = mod_controller(self.manager.params, self.manager.variables)
        for mode in self.params["mode"].type:
            with self.subTest(mode=mode):
                feature.params.variables["mode"] = mode.value
                before = dict(feature.params.variables)
                feature.mod_page = 0
                feature._render_mod_settings()
                offered = set(feature.mod_action_keys.values())
                while True:
                    page = feature.mod_page
                    feature._handle_mod_action("mod.next")
                    if feature.mod_page == page:
                        break
                    offered.update(feature.mod_action_keys.values())
                self.assertEqual(self.params["detail"].key in offered, mode.name in ("FIRST", "SECOND"))
                self.assertEqual(self.params["explicit"].key in offered, mode.name == "SECOND")
                self.assertNotIn(self.params["hidden"].key, offered)
                self.assertIn(self.params["other"].key, offered)
                self.assertEqual(feature.params.variables, before)


if __name__ == "__main__":
    unittest.main()
