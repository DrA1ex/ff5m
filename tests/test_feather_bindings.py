## Tests for typed Feather UI state declarations and bindings.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).parents[1]
PLUGINS = ROOT / ".py" / "klipper" / "plugins"
sys.path.insert(0, str(PLUGINS))

from ff5m_ui.move import runtime as move_ui  # noqa: E402
from ff5m_ui.z_offset import runtime as z_offset_ui  # noqa: E402
from ui import (  # noqa: E402
    FeatherRenderer, PageKey, PageTree, Rect, StateKey, StateStore, Text,
    bind, derived, reflect_page, state, state_spec,
)


class BindingPage(PageKey):
    MAIN = "binding.main"


class BindingState(StateKey):
    COUNT = state(int, default=2, minimum=0, maximum=5, unit="items")
    ENABLED = state(bool, default=True)
    MODE = state(str, default="normal", choices=("normal", "fast"))
    SENSOR = state(float, default=1.5, mutable=False, unit="V")
    POSITION = state(
        float, default=0.0, minimum=-5.0, maximum=5.0,
        simulation_role="position.x", simulation_home=5.0)


class StateDeclarationTest(unittest.TestCase):
    def test_state_store_uses_enum_keys_and_validates_metadata(self):
        store = StateStore(BindingState)

        self.assertEqual(store[BindingState.COUNT], 2)
        self.assertEqual(store[BindingState.SENSOR], 1.5)
        self.assertEqual(state_spec(BindingState.COUNT).unit, "items")
        with self.assertRaisesRegex(TypeError, "StateKey"):
            store.update({"count": 3})
        with self.assertRaisesRegex(ValueError, "above maximum"):
            store.update({BindingState.COUNT: 6})
        with self.assertRaisesRegex(ValueError, "allowed choices"):
            store.update({BindingState.MODE: "invalid"})

    def test_state_store_batch_update_is_transactional(self):
        store = StateStore(BindingState)

        with self.assertRaisesRegex(ValueError, "allowed choices"):
            store.update((
                (BindingState.COUNT, 4),
                (BindingState.MODE, "invalid"),
            ))

        self.assertEqual(store[BindingState.COUNT], 2)

    def test_binding_schema_is_inferred_from_real_component_properties(self):
        page = PageTree(
            Text(derived(
                lambda count, enabled: "%d:%s" % (count, enabled),
                bind(BindingState.COUNT), bind(BindingState.ENABLED))),
            Rect(0, 0, 100, 20), page_id=BindingPage.MAIN)

        self.assertEqual(page.state_schema,
                         (BindingState.COUNT, BindingState.ENABLED))
        drawing = "\n".join(page.draw(FeatherRenderer()))
        self.assertIn('-t "2:True"', drawing)

    def test_derived_callable_only_receives_declared_inputs(self):
        received = []

        def format_value(count, mode):
            received.append((count, mode))
            return "%d/%s" % (count, mode)

        page = PageTree(
            Text(derived(
                format_value,
                bind(BindingState.COUNT), bind(BindingState.MODE))),
            Rect(0, 0, 100, 20), page_id=BindingPage.MAIN)
        page.draw(FeatherRenderer(), {
            BindingState.COUNT: 4,
            BindingState.MODE: "fast",
        })

        self.assertEqual(received[-1], (4, "fast"))
        with self.assertRaisesRegex(TypeError, "explicit bindings"):
            derived(lambda value: value, BindingState.COUNT)
        with self.assertRaisesRegex(TypeError, "does not accept 2 inputs"):
            derived(lambda value: value,
                    bind(BindingState.COUNT), bind(BindingState.MODE))

    def test_raw_state_callable_is_rejected_instead_of_receiving_page_state(self):
        page = PageTree(
            Text(lambda values: values), Rect(0, 0, 100, 20),
            page_id=BindingPage.MAIN)

        with self.assertRaisesRegex(TypeError, "must use derived"):
            page.draw(FeatherRenderer())

    def test_reflection_exposes_state_and_binding_metadata(self):
        page = PageTree(
            Text(bind(BindingState.MODE)), Rect(0, 0, 100, 20),
            page_id=BindingPage.MAIN,
            state_schema=(BindingState.SENSOR,))
        model = reflect_page(page)

        schema = dict((item["name"], item) for item in model["state_schema"])
        self.assertEqual(set(schema), {"SENSOR", "MODE"})
        self.assertFalse(schema["SENSOR"]["mutable"])
        self.assertFalse(schema["SENSOR"]["nullable"])
        self.assertEqual(schema["SENSOR"]["unit"], "V")
        self.assertEqual(model["tree"]["bindings"]["value"]["kind"], "direct")
        self.assertTrue(model["tree"]["bindings"]["value"]["key"].endswith(
            ".BindingState.MODE"))

    def test_state_metadata_exposes_portable_simulation_role(self):
        metadata = StateStore((BindingState.POSITION,)).metadata()[0]

        self.assertEqual(metadata["simulation_role"], "position.x")
        self.assertEqual(metadata["simulation_home"], 5.0)

    def test_simulation_metadata_rejects_ambiguous_declarations(self):
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            state(float, default=0.0, simulation_role="  ")
        with self.assertRaisesRegex(ValueError, "requires simulation_role"):
            state(float, default=0.0, simulation_home=1.0)


class ProductStateMigrationTest(unittest.TestCase):
    def test_product_pages_use_typed_bindings_without_whole_state_lambdas(self):
        pages_root = PLUGINS / "ff5m_ui"
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in pages_root.rglob("*.py"))

        self.assertNotIn("lambda state", sources)
        self.assertNotIn("state[", sources)
        self.assertNotIn("state.get(", sources)
        self.assertIn("bind(ToolheadState.Z)", sources)
        self.assertIn("bind(PaperState.GAUGE)", sources)

    def test_all_discovered_product_pages_publish_valid_state_metadata(self):
        pages = (
            move_ui.STEP_PAGE, move_ui.JOYSTICK_PAGE,
            z_offset_ui.BRIEFING_PAGE, z_offset_ui.SUMMARY_PAGE,
            z_offset_ui.PAPER_BRIEFING_PAGE, z_offset_ui.PAPER_PAGE,
        )
        for page in pages:
            scene = page.state_metadata()
            keys = [item["key"] for item in scene]
            self.assertEqual(len(keys), len(set(keys)))
            self.assertTrue(all("." in key for key in keys))
            page.draw(FeatherRenderer(), page.initial_state())


if __name__ == "__main__":
    unittest.main()
