## Behavioral tests for Forge-X parameter validation and persistence.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import configparser
import importlib.util
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

    def test_offsets_outside_two_mm_remain_supported(self):
        for value in (-5.0, 5.0):
            self.manager.set_value("z_offset", value)
            self.manager._reload()
            self.assertEqual(self.manager.variables["z_offset"], value)

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


if __name__ == "__main__":
    unittest.main()
