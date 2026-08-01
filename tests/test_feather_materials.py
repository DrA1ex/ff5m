## Tests for slot-based material configuration and selector layout.

import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).parents[1]
PLUGINS = ROOT / ".py" / "klipper" / "plugins"
sys.path.insert(0, str(PLUGINS))

import feather_materials as MATERIALS  # noqa: E402


DEFAULTS = {
    "heating_slots": [1, 2, 3, 4, 5],
    "cold_pull_slots": [1, 2, 3, 6],
    "material_1": "PLA", "material_2": "PETG", "material_3": "ABS",
    "material_4": "ABS-PC", "material_5": "TPU", "material_6": "NYLON",
    "material_1_heating": {"nozzle": 220, "bed": 60},
    "material_2_heating": {"nozzle": 250, "bed": 70},
    "material_3_heating": {"nozzle": 260, "bed": 85},
    "material_4_heating": {"nozzle": 270, "bed": 105},
    "material_5_heating": {"nozzle": 220, "bed": 50},
    "material_1_cold_pull": {"hot": 220, "cold": 100},
    "material_2_cold_pull": {"hot": 250, "cold": 100},
    "material_3_cold_pull": {"hot": 260, "cold": 105},
    "material_6_cold_pull": {"hot": 265, "cold": 120},
}


class Macro:
    def __init__(self, variables):
        self.variables = variables


class Heater:
    min_temp = 0
    max_temp = 300


class Extruder:
    heater = Heater()
    min_extrude_temp = 170


class Bed:
    min_temp = 0
    max_temp = 130


def catalog(**overrides):
    variables = dict(DEFAULTS)
    variables.update(overrides)
    return MATERIALS.MaterialCatalog.from_macro(
        Macro(variables), Extruder(), Bed())


class MaterialCatalogTest(unittest.TestCase):
    def test_defaults_preserve_slot_order_and_workflow_membership(self):
        result = catalog()
        self.assertEqual(
            result.heating_materials,
            ("PLA", "PETG", "ABS", "ABS-PC", "TPU"))
        self.assertEqual(
            result.cold_pull_materials, ("PLA", "PETG", "ABS", "NYLON"))
        self.assertEqual(result.heating_profiles["ABS"], (260.0, 85.0))

    def test_overrides_can_disable_rename_reorder_and_replace(self):
        result = catalog(
            heating_slots=[5, 2, 4], material_4="ASA",
            material_5="TPU-95A",
            material_5_heating={"nozzle": 225, "bed": 45})
        self.assertEqual(result.heating_materials, ("TPU-95A", "PETG", "ASA"))
        self.assertEqual(result.heating_profiles["TPU-95A"], (225.0, 45.0))

    def test_inactive_incomplete_profiles_are_ignored(self):
        result = catalog(
            heating_slots=[], cold_pull_slots=[6],
            material_2_heating={"nozzle": "broken"})
        self.assertEqual(result.heating_materials, ())
        self.assertEqual(result.cold_pull_materials, ("NYLON",))

    def assert_invalid(self, message, **overrides):
        with self.assertRaisesRegex(MATERIALS.MaterialConfigError, message):
            catalog(**overrides)

    def test_rejects_invalid_slot_lists(self):
        self.assert_invalid("positive integer", heating_slots=[0])
        self.assert_invalid("positive integer", heating_slots=[1.0])
        self.assert_invalid("duplicate slot", heating_slots=[1, 1])
        self.assert_invalid("at most 5", heating_slots=[1, 2, 3, 4, 5, 6])

    def test_rejects_missing_active_names_and_profiles(self):
        self.assert_invalid("missing material_7", heating_slots=[7])
        self.assert_invalid(
            "missing material_6_heating", heating_slots=[6])
        self.assert_invalid(
            "missing material_5_cold_pull", cold_pull_slots=[5])

    def test_rejects_invalid_or_duplicate_workflow_names(self):
        self.assert_invalid("must match", material_1="pla")
        self.assert_invalid("must match", material_1="n/a")
        self.assert_invalid("duplicate material name", material_2="PLA")

    def test_rejects_incomplete_non_numeric_and_unsafe_temperatures(self):
        self.assert_invalid(
            "missing bed", material_1_heating={"nozzle": 220})
        self.assert_invalid(
            "must be numeric", material_1_heating={"nozzle": "hot", "bed": 60})
        self.assert_invalid(
            "outside hardware", material_1_heating={"nozzle": 301, "bed": 60})
        self.assert_invalid(
            "above cold", material_1_cold_pull={"hot": 100, "cold": 100})
        self.assert_invalid(
            "permit extrusion", material_1_cold_pull={"hot": 160, "cold": 100})


class RecordingRenderer:
    def __init__(self):
        self.buttons = []

    def button(self, action, x, y, width, height, label, **options):
        self.buttons.append((action, x, y, width, height, label, options))
        return [action]


class MaterialLayoutTest(unittest.TestCase):
    def test_empty_selector_creates_no_buttons_or_actions(self):
        renderer = RecordingRenderer()
        commands = MATERIALS.render_material_selector(
            renderer, "material.", 10, 20, 100, 40, materials=())
        self.assertEqual(commands, [])
        self.assertEqual(renderer.buttons, [])

    def test_one_to_five_selectors_emit_only_supplied_materials(self):
        names = ("ONE", "TWO", "THREE", "FOUR", "FIVE")
        for count in range(1, 6):
            with self.subTest(count=count):
                renderer = RecordingRenderer()
                MATERIALS.render_material_selector(
                    renderer, "material.", 0, 0, 100, 40,
                    columns=MATERIALS.adaptive_grid_columns(count),
                    column_gap=10, row_gap=10, area_width=320,
                    materials=names[:count])
                self.assertEqual(
                    [button[0] for button in renderer.buttons],
                    ["material." + name for name in names[:count]])

    def test_five_item_grid_is_three_plus_two_with_centered_last_row(self):
        renderer = RecordingRenderer()
        MATERIALS.render_material_selector(
            renderer, "material.", 0, 0, 100, 40, columns=3,
            column_gap=10, row_gap=10, area_width=320,
            materials=("ONE", "TWO", "THREE", "FOUR", "FIVE"))
        positions = [(button[1], button[2]) for button in renderer.buttons]
        self.assertEqual(positions[:3], [(0, 0), (110, 0), (220, 0)])
        self.assertEqual(positions[3:], [(55, 50), (165, 50)])

    def test_selector_keeps_material_and_temperature_on_separate_lines(self):
        renderer = RecordingRenderer()
        MATERIALS.render_material_selector(
            renderer, "material.", 0, 0, 230, 135,
            materials=("ABS-PC",), label=lambda material: material,
            subtitle=lambda material: "NOZZLE 270C",
            subtitle_font="JetBrainsMono Bold 12pt",
            subtitle_color="d9e4e8")

        button = renderer.buttons[0]
        self.assertEqual(button[5], "ABS-PC")
        self.assertEqual(button[6]["subtitle"], "NOZZLE 270C")
        self.assertEqual(
            button[6]["subtitle_font"], "JetBrainsMono Bold 12pt")
        self.assertEqual(button[6]["subtitle_color"], "d9e4e8")


class MaterialMacroContractTest(unittest.TestCase):
    def test_slot_lists_drive_both_prompts_and_empty_prompts_are_safe(self):
        material = (ROOT / "config" / "material.cfg").read_text(
            encoding="utf-8")
        self.assertIn("for slot in config.heating_slots", material)
        self.assertIn("for slot in config.cold_pull_slots", material)
        self.assertIn("No heating materials are enabled", material)
        self.assertIn("No cold-pull materials are enabled", material)
        preheat = material.split("[gcode_macro PREHEAT_MATERIAL]", 1)[1].split(
            "[gcode_macro LOAD_FILAMENT]", 1)[0]
        self.assertLess(preheat.index("No heating materials are enabled"),
                        preheat.index("M104 S"))


if __name__ == "__main__":
    unittest.main()
