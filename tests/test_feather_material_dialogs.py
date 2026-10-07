## Material workflow dialog integration tests for Feather.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import pathlib
import shlex
import unittest

from tests.gcode_macro_harness import load_macro, render_macro
from tests.feather_render_test_helper import RenderCapture
from tests.test_feather_screen import (
    FEATHER, GCodeRecorder, Reactor, StatusObject, blank_page,
)
from ui.font_metrics import get_font_metrics


MATERIAL = pathlib.Path(__file__).parents[1] / "config/material.cfg"


class MaterialDialogTest(unittest.TestCase):
    def setUp(self):
        self.screen = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        self.screen.renderer = FEATHER.FeatherRenderer()
        self.capture = RenderCapture(self.screen.renderer)
        self.screen.reactor = Reactor()
        self.screen.extruder = StatusObject({"temperature": 130., "target": 250.})
        self.screen.gcode = GCodeRecorder()
        self.operation = {}
        self.screen._operation_context_status = lambda eventtime=None: self.operation
        self.screen._run_script = self.screen.gcode.run_script_from_command
        blank_page(self.screen)
        self.screen._paint_page = lambda feature=None: self.screen.renderer.send(
            self.screen.renderer.begin_page("Home"))
        self.printer = {
            "gcode_macro _MATERIAL_CONFIG": load_macro(MATERIAL, "_MATERIAL_CONFIG").variables,
            "gcode_macro LOAD_MATERIAL": {"initial_target_temp": 0},
            "extruder": {"target": 0},
            "configfile": {"settings": {"extruder": {
                "min_temp": 0, "max_temp": 300, "min_extrude_temp": 170,
            }}},
        }

    def show_macro_prompt(self, name, **params):
        for command in render_macro(MATERIAL, name, printer=self.printer, params=params).commands:
            # Execute the actual prompt protocol without running heater or motion commands.
            if command.startswith("_CONTEXT_BEGIN"):
                break
            tokens = shlex.split(command)
            if tokens and tokens[0] == "RESPOND":
                message = next((token[4:] for token in tokens if token.startswith("MSG=")), "")
                if message.startswith("action:prompt_"):
                    self.screen._handle_gcode_output("// " + message)
        return self.capture.latest

    def visible_action(self, label):
        return next(action for action, button in self.capture.latest.buttons.items()
                    if button.label == label)

    def tap(self, label):
        self.screen._handle_action_prompt_action(self.visible_action(label))

    def assert_content_fits_dialog(self):
        frame = self.capture.latest
        filled = {shape.bounds for shape in frame.shapes if shape.kind == "fill"}
        panel = max((shape.bounds for shape in frame.shapes
                     if shape.kind == "stroke" and shape.bounds in filled),
                    key=lambda bounds: bounds.width * bounds.height)
        self.assertLessEqual(abs(2 * panel.x + panel.width - 800), 1)
        self.assertLessEqual(panel.width, 700)
        self.assertGreaterEqual(panel.y, 74)
        self.assertLessEqual(panel.bottom, 422)
        for button in frame.buttons.values():
            self.assertGreaterEqual(button.bounds.x, panel.x)
            self.assertLessEqual(button.bounds.right, panel.right)
            self.assertGreaterEqual(button.bounds.y, panel.y)
            self.assertLessEqual(button.bounds.bottom, panel.bottom)
        for text in frame.texts:
            if text.y < 60:
                continue
            self.assertGreater(text.y, panel.y)
            self.assertLess(text.y, panel.bottom)

    def test_material_change_preserves_commands_and_replaces_each_stage(self):
        self.show_macro_prompt("_LOAD_MATERIAL_SELECT")
        self.tap("PETG")
        self.assertEqual(self.screen.gcode.commands[-1], "_LOAD_MATERIAL_HEATUP MATERIAL=PETG TEMP=250")

        frame = self.show_macro_prompt("_LOAD_MATERIAL_HEATUP", MATERIAL="PETG", TEMP=250)
        self.assertEqual(frame.text("HEATING NOZZLE").font, "JetBrainsMono Bold 16pt")
        self.assertEqual(frame.text("NOZZLE 130 / 250 C").font, "JetBrainsMono 12pt")
        self.assertFalse(frame.buttons)

        self.show_macro_prompt("_LOAD_MATERIAL_ACTION")
        for label, macro in (("Load", "LOAD_FILAMENT"), ("Unload", "UNLOAD_FILAMENT"),
                             ("Purge", "PURGE_FILAMENT")):
            self.tap(label)
            self.assertEqual(self.screen.gcode.commands[-1], "_FILAMENT_ACTION MACRO='%s'" % macro)

        frame = self.show_macro_prompt("_FILAMENT_ACTION", MACRO="PURGE_FILAMENT")
        self.assertEqual(frame.text("WORKING WITH FILAMENT").font, "JetBrainsMono Bold 16pt")
        self.assertEqual(frame.text("PLEASE WAIT...").font, "JetBrainsMono 12pt")
        self.assertFalse(frame.buttons)

        self.show_macro_prompt("_LOAD_MATERIAL_ACTION")
        self.tap("Done")
        self.assertEqual(self.screen.gcode.commands[-1], "_LOAD_MATERIAL_END")
        self.show_macro_prompt("_LOAD_MATERIAL_END")
        self.assertIsNone(self.screen._current_dialog())

    def test_selectors_keep_every_profile_and_cleanup_action_reachable(self):
        for macro, workflow, cleanup in (
                ("COLDPULL", "cold_pull", "_COLDPULL_LOAD_MATERIAL_END"),
                ("_LOAD_MATERIAL_SELECT", "heating", "_LOAD_MATERIAL_END")):
            with self.subTest(macro=macro):
                self.show_macro_prompt(macro)
                self.assertFalse(self.capture.latest.has_action("prompt.next"))
                self.assertFalse(self.capture.latest.has_action("prompt.prev"))
                labels = set()
                for _ in range(10):
                    frame = self.capture.latest
                    labels.update(button.label for button in frame.buttons.values())
                    self.assertIn("Cancel", {button.label for button in frame.buttons.values()})
                    self.assert_content_fits_dialog()
                    if not frame.has_action("prompt.next"):
                        break
                    self.screen._handle_action_prompt_action("prompt.next")
                else:
                    self.fail("selector pagination did not finish")
                config = self.printer["gcode_macro _MATERIAL_CONFIG"]
                expected = {config["material_%d" % slot] for slot in config[workflow + "_slots"]}
                self.assertTrue(expected.issubset(labels))
                self.tap("Cancel")
                self.assertEqual(self.screen.gcode.commands[-1], cleanup)

    def test_empty_catalog_keeps_explanation_and_dismiss_command(self):
        for macro, workflow, cleanup in (
                ("COLDPULL", "cold_pull", "_COLDPULL_LOAD_MATERIAL_END"),
                ("_LOAD_MATERIAL_EMPTY", "heating", "_LOAD_MATERIAL_EMPTY_END")):
            with self.subTest(macro=macro):
                self.printer["gcode_macro _MATERIAL_CONFIG"][workflow + "_slots"] = []
                self.show_macro_prompt(macro)
                self.assert_content_fits_dialog()
                text = " ".join(item.value for item in self.capture.latest.texts)
                self.assertIn("materials are enabled", text)
                self.tap("Cancel")
                self.assertEqual(self.screen.gcode.commands[-1], cleanup)
                self.show_macro_prompt(cleanup)
                self.assertIsNone(self.screen._current_dialog())

    def test_cold_pull_progress_refreshes_temperature_and_keeps_cancel_confirmation(self):
        self.operation.update({
            "context_types": ("cold_pull",), "current_state": "HEATING NOZZLE",
            "cancel_available": True, "cancel_pending": False,
        })
        self.show_macro_prompt("_COLDPULL_LOAD_MATERIAL", MATERIAL="PETG", TEMP=250, COLD=100, PROMPT=1)
        text = " ".join(item.value for item in self.capture.latest.texts)
        self.assertIn("HEATING NOZZLE", text)
        self.assertIn("NOZZLE 130 / 250 C", text)
        self.assertTrue(self.capture.latest.has_action("coldpull.cancel"))
        self.assert_content_fits_dialog()
        opened = []
        self.screen._open_operation_cancel = opened.append
        self.screen._handle_touch_action("coldpull.cancel")
        self.assertEqual(opened, [self.screen.page])
        self.assertFalse(self.screen.gcode.commands)

        count = len(self.capture.frames)
        self.screen._render_dialog()
        self.assertEqual(len(self.capture.frames), count)
        self.screen.extruder.status["temperature"] = 200.
        self.operation["cancel_pending"] = True
        self.screen._render_dialog()
        text = " ".join(item.value for item in self.capture.latest.texts)
        self.assertIn("NOZZLE 200 / 250 C", text)
        self.assertFalse(self.capture.latest.has_action("coldpull.cancel"))
        self.operation["cancel_available"] = False
        self.screen._render_dialog()
        self.assertFalse(self.capture.latest.has_action("coldpull.cancel"))

    def test_cold_pull_status_is_large_and_separate_from_temperature_on_every_stage(self):
        metrics = get_font_metrics()
        self.operation.update(context_types=("cold_pull",), cancel_available=True)
        self.show_macro_prompt("_COLDPULL_LOAD_MATERIAL", MATERIAL="PETG", TEMP=250, COLD=100, PROMPT=1)
        for stage in ("HOMING", "HEATING NOZZLE", "EXTRUDING", "COOLING NOZZLE", "PULLING"):
            with self.subTest(stage=stage):
                self.operation["current_state"] = stage
                self.screen._render_dialog()
                frame = self.capture.latest
                status = frame.text(stage)
                temperature = frame.text("NOZZLE 130 / 250 C")
                self.assertEqual(status.font, "JetBrainsMono Bold 16pt")
                self.assertEqual(temperature.font, "JetBrainsMono 12pt")
                body = [text for text in frame.texts if text.y > 60]
                self.assertEqual(len(body), 2)
                self.assertGreaterEqual(
                    temperature.y - metrics.metric(temperature.font).glyph_height // 2
                    - status.y - metrics.metric(status.font).glyph_height // 2, 20)
                self.assertGreaterEqual(
                    frame.button("coldpull.cancel").bounds.y - temperature.y
                    - metrics.metric(temperature.font).glyph_height // 2, 20)
                self.assert_content_fits_dialog()

    def test_user_prompt_with_a_feather_title_keeps_its_own_dialog(self):
        self.operation.update(context_types=("cold_pull",), current_state="HEATING",
                              cancel_available=True, cancel_pending=False)
        for title in ("Cold Pull", "Heating nozzle", "Filament change"):
            with self.subTest(title=title):
                self.screen._handle_gcode_output("\n".join((
                    "// action:prompt_begin " + title,
                    "// action:prompt_text User macro instructions.",
                    "// action:prompt_footer_button Continue|USER_CONTINUE",
                    "// action:prompt_show")))
                frame = self.capture.latest
                self.assertTrue(frame.has_text("User macro instructions."))
                self.assertFalse(any(text.value.startswith("NOZZLE ") for text in frame.texts))
                self.assertEqual({button.label for button in frame.buttons.values()}, {"Continue"})

    def test_heating_prompt_updates_temperature_without_repeating_scrim(self):
        self.show_macro_prompt("_LOAD_MATERIAL_HEATUP", MATERIAL="PETG", TEMP=250)
        scrim = self.screen.renderer.modal_scrim()
        self.assertEqual(self.capture.batches[-1].count(scrim), 1)
        generation = self.screen.renderer.generation
        count = len(self.capture.batches)
        self.screen._render_dialog()
        self.assertEqual(len(self.capture.batches), count)
        self.screen.extruder.status["temperature"] = 200.0
        self.screen._render_dialog()
        self.assertTrue(self.capture.latest.has_text("NOZZLE 200 / 250 C"))
        self.assertNotIn(scrim, self.capture.batches[-1])
        self.assertEqual(self.screen.renderer.generation, generation)

    def test_cold_pull_paginates_instructions_and_groups_with_all_footer_actions(self):
        instructions = ["Instruction %d for cleaning the nozzle." % index for index in range(12)]
        self.screen._handle_gcode_output("\n".join(
            ["// action:prompt_begin Cold Pull"]
            + ["// action:prompt_text " + line for line in instructions]
            + ["// action:prompt_button_group_start"]
            + ["// action:prompt_button %s|SELECT_%s" % (label, label)
               for label in ("PLA", "PETG", "ABS", "NYLON")]
            + ["// action:prompt_button_group_end",
               "// action:prompt_footer_button Cancel|CLEANUP",
               "// action:prompt_footer_button Help|HELP",
               "// action:prompt_show"]))
        self.assertTrue(self.capture.latest.has_action("prompt.next"))
        texts, labels = [], set()
        for _ in range(20):
            frame = self.capture.latest
            self.assert_content_fits_dialog()
            self.assertTrue({"Cancel", "Help"}.issubset(
                {button.label for button in frame.buttons.values()}))
            texts.extend(text.value for text in frame.texts
                         if text.font == "JetBrainsMono 8pt" and not text.value.isdigit())
            labels.update(button.label for button in frame.buttons.values())
            if not frame.has_action("prompt.next"):
                break
            self.screen._handle_action_prompt_action("prompt.next")
        else:
            self.fail("instruction pagination did not finish")
        self.assertEqual(" ".join(texts), " ".join(instructions))
        self.assertTrue({"PLA", "PETG", "ABS", "NYLON"}.issubset(labels))
        self.tap("Help")
        self.assertEqual(self.screen.gcode.commands[-1], "HELP")
        self.assertTrue(frame.has_action("prompt.prev"))
        self.screen._handle_action_prompt_action("prompt.prev")
        self.assertTrue(self.capture.latest.has_action("prompt.next"))


if __name__ == "__main__":
    unittest.main()
