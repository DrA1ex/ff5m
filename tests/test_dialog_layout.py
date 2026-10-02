## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Regression coverage for dialog action wrapping and bounded pagination."""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / ".py" / "klipper" / "plugins"))

from ui.renderer import FeatherRenderer
from ui.layout_helpers import DIALOG_BUTTON_FONT
from tests.feather_render_test_helper import RenderFrame
from tests.visual_checks.dialog_cases import CASES, render_case


class DialogLayoutTests(unittest.TestCase):
    labels = ("Standard Mix (PLA/PETG/ABS/TPU)",
              "2x PETG / 2x ABS", "2x PLA / 2x ABS")

    def buttons(self, labels=None):
        return tuple(("choice.%d" % index, label, "enabled")
                     for index, label in enumerate(labels or self.labels))

    def test_screenshot_cases_preserve_rows_padding_and_readability(self):
        for case in CASES:
            with self.subTest(case=case["id"]):
                renderer = FeatherRenderer()
                frame = RenderFrame(render_case(renderer, case), renderer)
                panel = next(shape.bounds for shape in frame.shapes if shape.kind == "fill")
                choices = [button for action, button in frame.buttons.items()
                           if action.startswith("choice.")]
                rows = {}
                for button in choices:
                    rows.setdefault(button.bounds.y, []).append(button)
                self.assertEqual([len(row) for _, row in sorted(rows.items())], case["rows"])
                actions = [button for action, button in frame.buttons.items()
                           if action.startswith(("choice.", "footer."))]
                self.assertEqual(panel.bottom - max(button.bounds.bottom for button in actions),
                                 case.get("bottom", 30))
                row_y = sorted(rows)
                self.assertTrue(all(b - a == 62 for a, b in zip(row_y, row_y[1:])))
                for row in rows.values():
                    self.assertTrue(all(b.bounds.x - a.bounds.right == 12
                                        for a, b in zip(row, row[1:])))
                self.assert_readable(renderer, frame)

    def assert_readable(self, renderer, frame):
        panel = next(shape.bounds for shape in frame.shapes if shape.kind == "fill")
        self.assertGreaterEqual(panel.y, 74)
        self.assertLessEqual(panel.bottom, 422)
        for button in frame.buttons.values():
            bounds = button.bounds
            self.assertGreaterEqual(bounds.x, panel.x)
            self.assertLessEqual(bounds.right, panel.right)
            self.assertGreaterEqual(bounds.y, panel.y)
            self.assertLessEqual(bounds.bottom, panel.bottom)
            self.assertEqual(bounds.height, 50 if button.action.startswith(("choice.", "footer.")) else 44)
            if button.action.startswith(("choice.", "footer.")):
                self.assertLessEqual(renderer.text_width(button.label, button.font),
                                     bounds.width - 2 * renderer.BUTTON_TEXT_PADDING)
        controls = list(frame.buttons.values())
        for index, left in enumerate(controls):
            for right in controls[index + 1:]:
                a, b = left.bounds, right.bounds
                self.assertFalse(a.x < b.right and b.x < a.right
                                 and a.y < b.bottom and b.y < a.bottom)

    def test_mmu_group_wraps_to_readable_rows_and_expands_panel(self):
        renderer = FeatherRenderer()
        frame = RenderFrame(renderer.dialog(
            "MMU Preset Layouts",
            ("Choose your desired lane layout for this session:",), (),
            button_groups=(self.buttons(),), width=700,
            page_actions=("prev", "next")), renderer)
        buttons = [frame.button(action) for action, _, _ in self.buttons()]
        self.assertGreater(len({button.bounds.y for button in buttons}), 1)
        self.assertTrue(all(button.font == DIALOG_BUTTON_FONT for button in buttons))
        self.assertGreater(next(shape.bounds.height for shape in frame.shapes), 220)
        self.assertNotIn("next", frame.buttons)
        self.assert_readable(renderer, frame)

    def test_fitting_compact_row_is_kept_together(self):
        renderer = FeatherRenderer()
        frame = RenderFrame(renderer.dialog(
            "Choices", (), (), width=700,
            button_groups=(self.buttons(("ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX")),)),
            renderer)
        self.assertEqual(len({button.bounds.y for button in frame.buttons.values()}), 1)
        self.assert_readable(renderer, frame)

    def test_single_long_label_uses_a_fitting_font(self):
        renderer = FeatherRenderer()
        frame = RenderFrame(renderer.dialog(
            "Choices", (), self.buttons(("X" * 40,)), width=700), renderer)
        self.assert_readable(renderer, frame)

    def test_explicit_group_boundaries_and_action_states_survive_wrapping(self):
        renderer = FeatherRenderer()
        choices = self.buttons()
        frame = RenderFrame(renderer.dialog(
            "Choices", (), (), width=700,
            button_groups=(choices[:1], (choices[1],),
                           ((choices[2][0], choices[2][1], "disabled"),))), renderer)
        self.assertLess(frame.button("choice.0").bounds.y, frame.button("choice.1").bounds.y)
        self.assertNotIn("choice.2", frame.buttons)
        self.assert_readable(renderer, frame)

    def test_two_footer_rows_reserve_space_on_every_text_page(self):
        renderer = FeatherRenderer()
        buttons = self.buttons(("CONFIRM THIS MATERIAL PROFILE NOW",
                                "CANCEL THIS MATERIAL PROFILE NOW"))
        seen = []
        for page in range(30):
            frame = RenderFrame(renderer.dialog(
                "Instructions", tuple("Instruction %d" % i for i in range(15)),
                buttons, width=700, page=page, page_actions=("prev", "next")), renderer)
            self.assertEqual({button.action for button in frame.buttons.values()
                              if button.action.startswith("choice.")},
                             {"choice.0", "choice.1"})
            footer_top = min(frame.button(action).bounds.y for action, _, _ in buttons)
            for text in frame.texts:
                if text.value.startswith("Instruction "):
                    self.assertLess(text.y + 11, footer_top)
                    seen.append(text.value)
                if "/" in text.value:
                    self.assertGreater(text.y - 11, frame.button("prev").bounds.bottom
                                       if "prev" in frame.buttons else 0)
                    if "next" in frame.buttons:
                        self.assertLess(text.y + 11, frame.button("next").bounds.y)
            self.assert_readable(renderer, frame)
            if "next" not in frame.buttons:
                break
        else:
            self.fail("text pagination did not terminate")
        self.assertEqual(seen, ["Instruction %d" % i for i in range(15)])

    def test_wrapped_rows_repaginate_with_pager_width_without_losing_actions(self):
        renderer = FeatherRenderer()
        buttons = self.buttons(tuple("Material profile %02d: PLA/PETG/ABS" % i
                                     for i in range(12)))
        seen = []
        for page in range(30):
            frame = RenderFrame(renderer.dialog(
                "Choices", ("Choose a profile",), (), width=700,
                button_groups=(buttons,), page=page,
                page_actions=("prev", "next")), renderer)
            seen.extend(action for action in frame.buttons if action.startswith("choice."))
            self.assert_readable(renderer, frame)
            if "next" not in frame.buttons:
                break
        else:
            self.fail("button pagination did not terminate")
        self.assertEqual(seen, [button[0] for button in buttons])

    def test_excessive_footer_rows_remain_reachable_through_pagination(self):
        renderer = FeatherRenderer()
        buttons = self.buttons(tuple("Material profile %02d: PLA/PETG/ABS" % i
                                     for i in range(12)))
        seen = []
        for page in range(30):
            frame = RenderFrame(renderer.dialog(
                "Choices", (), buttons, width=700, page=page,
                page_actions=("prev", "next")), renderer)
            seen.extend(action for action in frame.buttons if action.startswith("choice."))
            self.assert_readable(renderer, frame)
            if "next" not in frame.buttons:
                break
        else:
            self.fail("footer pagination did not terminate")
        self.assertEqual(seen, [button[0] for button in buttons])


if __name__ == "__main__":
    unittest.main()
