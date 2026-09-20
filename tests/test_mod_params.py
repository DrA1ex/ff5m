## Tests for persisted Forge-X parameter validation.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import configparser
import importlib.util
import pathlib
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]
MODULE_PATH = ROOT / ".py" / "klipper" / "plugins" / "mod_params.py"
SPEC = importlib.util.spec_from_file_location("forge_x_mod_params", MODULE_PATH)
MOD_PARAMS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD_PARAMS)


def declared_manager():
    manager = MOD_PARAMS.ModParamManagement.__new__(
        MOD_PARAMS.ModParamManagement)
    manager.declaration = str(ROOT / "mod_params.json")
    manager.printer = type("Printer", (), {
        "command_error": staticmethod(RuntimeError)})()
    manager._load_declaration()
    return manager


class ModParameterSafetyTest(unittest.TestCase):
    def test_motion_parameters_publish_enforced_bounds(self):
        manager = declared_manager()

        z_offset = manager.params_map["z_offset"]
        safe_z = manager.params_map["safe_z"]
        self.assertEqual((z_offset.minimum, z_offset.maximum), (-2.0, 2.0))
        self.assertEqual((safe_z.minimum, safe_z.maximum), (1.0, 220.0))

        for parameter, valid in ((z_offset, (-2, 0, 2)),
                                 (safe_z, (1, 10, 220))):
            for value in valid:
                with self.subTest(parameter=parameter.key, value=value):
                    self.assertEqual(
                        manager._load_param(parameter, str(value)),
                        float(value))

        for parameter, invalid in (
                (z_offset, (-2.001, 2.001, float("nan"), float("inf"))),
                (safe_z, (0.999, 220.001, float("nan"), float("inf")))):
            for value in invalid:
                with self.subTest(parameter=parameter.key, value=value):
                    with self.assertRaises(ValueError):
                        manager._load_param(parameter, str(value))

    def test_one_corrupted_value_falls_back_without_blocking_other_settings(self):
        manager = declared_manager()
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".cfg") as variables_file:
            variables_file.write(
                "[Variables]\n"
                "z_offset = nan\n"
                "safe_z = -99.0\n"
                "midi_start = %(broken)s\n"
                "pause_z_min = 75.0\n")
            variables_file.flush()
            manager.filename = variables_file.name

            with mock.patch.object(MOD_PARAMS.logging, "error") as logged:
                manager._reload()

        self.assertEqual(manager.variables["z_offset"], 0.0)
        self.assertEqual(manager.variables["safe_z"], 10.0)
        self.assertEqual(manager.variables["pause_z_min"], 75.0)
        messages = "\n".join(call.args[0] for call in logged.call_args_list)
        self.assertIn("z_offset", messages)
        self.assertIn("safe_z", messages)

    def test_complete_parameter_snapshot_is_replaced_atomically(self):
        manager = declared_manager()
        manager.variables = dict(
            (parameter.key,
             manager._load_param(parameter, parameter.default))
            for parameter in manager.params)

        with tempfile.TemporaryDirectory() as directory:
            manager.filename = str(pathlib.Path(directory) / "variables.cfg")
            manager.gcode = type("GCode", (), {
                "error": staticmethod(RuntimeError)})()
            manager._save_all()

            parser = configparser.ConfigParser()
            parser.read(manager.filename)
            self.assertEqual(parser.getfloat("Variables", "safe_z"), 10.0)
            self.assertEqual(parser.getfloat("Variables", "z_offset"), 0.0)
            self.assertFalse(pathlib.Path(manager.filename + ".tmp").exists())


if __name__ == "__main__":
    unittest.main()
