"""Contract tests for the Forge-X Orca-Flashforge profile overrides."""

import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).parents[1]
PROFILE_ROOT = (
    ROOT / "slicer_profiles" / "Orca-Flashforge" / "resources" /
    "profiles" / "Flashforge"
)
MACHINE = PROFILE_ROOT / "machine" / (
    "Flashforge Adventurer 5M 0.4 Nozzle.json"
)
PROCESS_COMMON = (
    PROFILE_ROOT / "process" / "fdm_process_flashforge_common.json"
)
PROCESS_020 = (
    PROFILE_ROOT / "process" / "fdm_process_flashforge_0.20.json"
)
FILAMENT = PROFILE_ROOT / "filament" / "Flashforge Generic PLA.json"

STOCK_START_GCODE = "\n".join((
    "START_PRINT EXTRUDER_TEMP=[nozzle_temperature_initial_layer] "
    "BED_TEMP=[bed_temperature_initial_layer_single]",
    "M190 S[bed_temperature_initial_layer_single]",
    "M104 S[nozzle_temperature_initial_layer]",
))


def load_profile(path):
    with path.open(encoding="utf-8") as profile_file:
        return json.load(profile_file)


class OrcaProfileTest(unittest.TestCase):
    def test_profile_json_is_valid(self):
        for path in (MACHINE, PROCESS_COMMON, PROCESS_020, FILAMENT):
            with self.subTest(path=path):
                self.assertIsInstance(load_profile(path), dict)

    def test_machine_delegates_print_lifecycle_to_forge_x(self):
        machine = load_profile(MACHINE)

        self.assertEqual(machine["machine_start_gcode"], STOCK_START_GCODE)
        self.assertEqual(machine["machine_end_gcode"], "END_PRINT")
        self.assertNotIn("G28", machine["machine_start_gcode"])
        self.assertNotIn("G92", machine["machine_start_gcode"])
        self.assertEqual(machine["printable_area"], [
            "-110x-110", "110x-110", "110x110", "-110x110",
        ])

    def test_regular_ad5m_does_not_advertise_pro_hardware(self):
        machine = load_profile(MACHINE)

        self.assertEqual(machine["auxiliary_fan"], "0")
        self.assertEqual(machine["support_air_filtration"], "0")
        self.assertEqual(machine["support_chamber_temp_control"], "0")
        self.assertEqual(machine["nozzle_type"], "stainless_steel")

    def test_process_emits_object_labels_for_kamp(self):
        common = load_profile(PROCESS_COMMON)
        standard = load_profile(PROCESS_020)

        self.assertEqual(common["exclude_object"], "1")
        self.assertEqual(standard["gcode_label_objects"], "1")

    def test_generic_filament_leaves_pressure_advance_to_firmware(self):
        filament = load_profile(FILAMENT)

        self.assertEqual(filament["enable_pressure_advance"], ["0"])
        self.assertNotIn("pressure_advance", filament)


if __name__ == "__main__":
    unittest.main()
