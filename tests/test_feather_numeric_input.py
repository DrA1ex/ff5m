"""Shared numeric keypad editing, validation, and rendering tests."""

import pathlib
import sys
import unittest


PLUGINS = (pathlib.Path(__file__).parents[1] / ".py" / "klipper" /
           "plugins")
sys.path.insert(0, str(PLUGINS))

from ui import (  # noqa: E402
    Back, CONTENT_BOTTOM, HEADER_BOTTOM, FeatherRenderer, NumericInputSpec,
    NumericKeypad, Rect, rectangles_overlap,
)


class NumericInputSpecTest(unittest.TestCase):
    def test_decimal_editing_limits_fraction_and_length(self):
        spec = NumericInputSpec(
            "decimal", minimum=0, maximum=200, max_length=6,
            fraction_digits=2)
        value = ""
        for token in ("1", "2", "dot", "3", "4", "5", "6"):
            value = spec.apply(value, token)
        self.assertEqual(value, "12.34")
        self.assertEqual(spec.parse(value), 12.34)

    def test_integer_mode_rejects_decimal_and_obeys_range(self):
        spec = NumericInputSpec("integer", minimum=-10, maximum=10)
        self.assertEqual(spec.apply("7", "decimal"), "7")
        self.assertEqual(spec.apply("7", "sign"), "-7")
        self.assertEqual(spec.parse("-7"), -7)
        with self.assertRaisesRegex(ValueError, "at most 10"):
            spec.parse("11")

    def test_positive_input_does_not_accept_sign(self):
        spec = NumericInputSpec("decimal", minimum="0.001")
        self.assertFalse(spec.allows_negative)
        self.assertEqual(spec.apply("12", "sign"), "12")
        with self.assertRaisesRegex(ValueError, "at least"):
            spec.parse("0")

    def test_direct_values_still_obey_length_and_finite_constraints(self):
        spec = NumericInputSpec("decimal", max_length=4)
        with self.assertRaisesRegex(ValueError, "at most 4 characters"):
            spec.parse("12345")
        with self.assertRaisesRegex(ValueError, "finite"):
            NumericInputSpec("decimal", max_length=10002).parse(
                "1" + "0" * 10000)


class NumericKeypadTest(unittest.TestCase):
    def test_renderer_keeps_page_chrome_outside_keypad(self):
        renderer = FeatherRenderer()
        renderer.begin_page("Measured distance", back=True)
        actions = dict((digit, "key.%s" % digit)
                       for digit in "0123456789")
        actions.update({"decimal": "key.dot", "backspace": "key.back",
                        "confirm": "key.confirm"})
        drawing = "\n".join(renderer.numeric_keypad(
            18, 65, 764, 370, "Distance between marks", "100.5", actions,
            subtitle="mm", minimum=0.001, fraction_digits=3,
            confirm_label="CALCULATE"))

        self.assertIn("--id 1:key.dot", drawing)
        self.assertIn("--id 1:key.back", drawing)

        controls = [Rect(*data[:4]) for action, data in renderer._buttons.items()
                    if action.startswith("key.")]
        self.assertEqual(len(controls), 13)
        for control in controls:
            self.assertGreater(control.y, HEADER_BOTTOM)
            self.assertLessEqual(control.bottom, CONTENT_BOTTOM)
        for index, first in enumerate(controls):
            for second in controls[index + 1:]:
                self.assertFalse(rectangles_overlap(first, second))

    def test_declarative_component_requires_semantic_actions(self):
        with self.assertRaisesRegex(TypeError, "semantic Action"):
            NumericKeypad("VALUE", "1", {"confirm": "save"})
        keypad = NumericKeypad(
            "VALUE", "1", {"confirm": Back(), "backspace": Back()},
            mode="integer", minimum=0)
        drawing = keypad.draw(
            FeatherRenderer(), {}, Rect(18, 65, 764, 370))
        self.assertTrue(drawing)

    def test_sign_is_rendered_once_in_each_numeric_mode(self):
        actions = dict((digit, "key.%s" % digit)
                       for digit in "0123456789")
        actions.update({"decimal": "key.dot", "sign": "key.sign",
                        "backspace": "key.back", "confirm": "key.save"})
        renderer = FeatherRenderer()
        decimal = "\n".join(renderer.numeric_keypad(
            18, 65, 764, 370, "VALUE", "-1.5", actions,
            mode="decimal"))
        integer = "\n".join(renderer.numeric_keypad(
            18, 65, 764, 370, "VALUE", "-15", actions,
            mode="integer", fraction_digits=0))

        self.assertEqual(decimal.count(":key.sign"), 1)
        self.assertEqual(decimal.count(":key.dot"), 1)
        self.assertEqual(integer.count(":key.sign"), 1)
        self.assertNotIn(":key.dot", integer)

    def test_theme_roles_create_readable_visual_hierarchy(self):
        renderer = FeatherRenderer()
        self.assertTrue(renderer.set_theme("SYNTH"))
        actions = dict((digit, "key.%s" % digit)
                       for digit in "0123456789")
        actions.update({"sign": "key.sign", "backspace": "key.back",
                        "confirm": "key.save"})
        drawing = "\n".join(renderer.numeric_keypad(
            18, 65, 764, 370,
            "Cooldown temperature for CLEAR_NOZZLE, C", "150", actions,
            subtitle="clear_cooldown_temp", mode="integer"))

        self.assertIn("-c %s" % renderer.color("text"), drawing)
        self.assertIn("-c %s" % renderer.color("dim"), drawing)
        self.assertIn("-c %s" % renderer.color("secondary"), drawing)
        keypad_style = (
            "--background %s --border %s --text-color %s" % (
                renderer.color("primary_dark"),
                renderer.color("primary"),
                renderer.color("bright")))
        self.assertGreaterEqual(drawing.count(keypad_style), 11)


if __name__ == "__main__":
    unittest.main()
