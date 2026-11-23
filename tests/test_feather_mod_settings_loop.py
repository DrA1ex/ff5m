## The mod settings list and its option pickers drawn by the frame loop.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import enum
import shlex
import unittest

from tests.test_feather_workflows import FEATHER, base_controller
from tests.test_feather_preview_settings import EditorReactor
from tests.test_feather_screen import ModManager, mod_param
from tests.feather_render_test_helper import RenderFrame, shown_actions, shown_hitboxes, tapped_action
from tests.feather_typer_loop import TyperLoop, wait_for

from feather.features.manager import LazyFeatureManager
from ff5m_ui.parameter_controls import option_action, parameter_action
from feather.previews import decode_fxi1
from ui import ThemeRole
from ui.renderer import SCREEN_HEIGHT, SCREEN_WIDTH


Display = enum.Enum("Display", {"STOCK": 0, "FEATHER": 1, "HEADLESS": 2, "GUPPY": 3})


class ModSettingsOnTheLoopTest(unittest.TestCase):
    def settings(self, params, variables):
        """A real screen over the loop showing the general mod settings of params."""
        controller = base_controller()
        for method in ("_paint_page", "_render_screen", "_render_dialog", "_paint_page_values"):
            controller.__dict__.pop(method, None)
        controller._show_touch_unavailable = lambda: None
        controller._require_idle = lambda: None
        controller.reactor = EditorReactor()
        controller.reactor.now = 1
        controller.params = ModManager(params, variables)
        controller.feature_manager = LazyFeatureManager(controller, FEATHER.FEATURE_SPECS)
        controller.renderer = FEATHER.FeatherRenderer()
        controller.last_action_time = controller.last_touch_time = 0
        controller.pending_action, controller.dimmed = None, False
        typer = TyperLoop(controller.renderer)
        self.addCleanup(typer.close)
        typer.start()
        return controller, typer

    def drawn_toggle(self, controller, typer, action):
        """The toggle as the loop's last frame that drew it shows it; None until Typer logged one (the fake Typer
        may still be starting after the screen's own input already shows the page)."""
        for frame in reversed(typer.history()):
            try:
                return RenderFrame(frame, controller.renderer).toggle(action)
            except AssertionError:
                continue
        return None

    def toggle_active(self, controller, typer, action):
        toggle = self.drawn_toggle(controller, typer, action)
        return toggle is not None and toggle.active

    def tap(self, controller, typer, action, done):
        """Taps action as Typer reports it until done() holds."""
        def wire():
            try:
                return typer.wire(action)
            except AssertionError:
                return None  # Typer has not logged the frame that gave it a region yet.
        controller.reactor.now += 1
        typer.tell("tap %s" % wait_for(wire))
        return wait_for(lambda: controller._process_touch_events(controller.reactor.now) or done())

    def drew_since(self, typer, start, text, **wait):
        """Whether a batch the loop drew after the first start ones holds text, once one does (or the timeout)."""
        return wait_for(lambda: any(text in str(getattr(command, "command", command))
                                    for batch in typer.history()[start:] for command in batch), **wait)

    def checked(self, controller, typer):
        """The option row the loop last drew the checkmark in, and the checkmark's colour."""
        for frame in reversed(typer.history()):
            images = [command for command in frame if getattr(command, "payload", None) is not None]
            if not images:
                continue
            tokens = shlex.split(str(images[-1]))
            x, y = map(int, tokens[tokens.index("-p") + 1:tokens.index("-p") + 3])
            return tapped_action(shown_hitboxes(controller.renderer), x, y), decode_fxi1(images[-1].payload)["palette"][1]
        return None

    def open_picker(self, controller, typer, page, action):
        controller._show_page(page)
        self.assertTrue(wait_for(lambda: action in shown_actions(controller._screen_root)))
        self.assertTrue(self.tap(controller, typer, action,
                                 lambda: controller.page == FEATHER.ScreenPage.PARAMETER_OPTIONS))
        return controller.feature_manager.get("settings")

    def selected_color(self, controller):
        return 0xff000000 | int(controller.renderer.color(ThemeRole.BUTTON_SELECTED_TEXT), 16)

    def test_the_loop_draws_the_options_and_a_tap_moves_the_checkmark(self):
        display = mod_param("display", Display, 1, "Display", "Choose the active local screen.",
                            {"STOCK": "Stock", "FEATHER": "Feather", "HEADLESS": "Headless", "GUPPY": "Guppy"})
        controller, typer = self.settings([display], {"display": 1})
        with self.assertNoLogs(level="WARNING"):
            feature = self.open_picker(controller, typer, FEATHER.ScreenPage.MOD_SETTINGS,
                                       parameter_action("display"))
            self.assertTrue(controller._screen_root.frames.attached)
            self.assertTrue(self.drew_since(typer, 0, "GUPPY // GUPPY"))
            self.assertTrue(wait_for(lambda: {option_action(index) for index in range(4)}
                                     <= shown_actions(controller._screen_root)))
            self.assertEqual(self.checked(controller, typer), (option_action(1), self.selected_color(controller)))
            self.assertTrue(self.tap(controller, typer, option_action(3),
                                     lambda: self.checked(controller, typer)[0] == option_action(3)))
        self.assertEqual(feature.selected_parameter_option, "GUPPY")
        self.assertEqual(controller.params.variables["display"], 1)

    def test_a_theme_picked_on_the_loop_recolours_its_checkmark(self):
        controller, typer = self.settings([mod_param("feather_theme", str, "DEFAULT", "Feather color theme")],
                                          {"feather_theme": "DEFAULT"})
        with self.assertNoLogs(level="WARNING"):
            feature = self.open_picker(controller, typer, FEATHER.ScreenPage.SETTINGS, "settings.theme")
            amber = feature.parameter_options.index("AMBER")
            default_color = self.selected_color(controller)
            self.assertTrue(wait_for(lambda: self.checked(controller, typer) == (
                option_action(feature.parameter_options.index("DEFAULT")), default_color)))
            self.assertTrue(self.tap(controller, typer, option_action(amber),
                                     lambda: self.checked(controller, typer)[0] == option_action(amber)))
            self.assertEqual(controller.renderer.theme_name, "AMBER")
            self.assertNotEqual(self.selected_color(controller), default_color)
            self.assertEqual(self.checked(controller, typer), (option_action(amber), self.selected_color(controller)))

    def test_the_loop_removes_save_input_until_the_draft_is_valid(self):
        speed = mod_param("speed", float, 5.0, "Travel speed")
        speed.minimum, speed.maximum, speed.fraction_digits = 1.0, 10.0, 1
        controller, typer = self.settings([speed], {"speed": 5.0})
        with self.assertNoLogs(level="WARNING"):
            controller._show_page(FEATHER.ScreenPage.MOD_SETTINGS)
            self.assertTrue(wait_for(lambda: parameter_action("speed") in shown_actions(controller._screen_root)))
            self.assertTrue(self.tap(controller, typer, parameter_action("speed"),
                                     lambda: controller._current_dialog() == FEATHER.ScreenDialog.NUMERIC_INPUT))
            feature = controller.feature_manager.get("settings")
            self.assertTrue(controller._screen_root.frames.attached)
            self.assertTrue(wait_for(lambda: {"mod.key.9", "mod.dot", "mod.save"} <= shown_actions(controller._screen_root)))
            # An empty value cannot be saved: the confirm key loses its input.
            while feature.mod_edit_value:
                self.assertTrue(self.tap(controller, typer, "mod.backspace",
                                         lambda count=len(feature.mod_edit_value): len(feature.mod_edit_value) < count))
            self.assertTrue(wait_for(lambda: "mod.save" not in shown_actions(controller._screen_root)))
            self.assertTrue(self.tap(controller, typer, "mod.key.9", lambda: feature.mod_edit_value == "9"))
            self.assertTrue(wait_for(lambda: "mod.save" in shown_actions(controller._screen_root)))

    def test_signed_setting_uses_the_sign_and_point_on_the_compact_keypad(self):
        offset = mod_param("z_offset", float, 0.0, "Z offset")
        offset.minimum, offset.maximum, offset.fraction_digits = -5.0, 5.0, 2
        controller, typer = self.settings([offset], {"z_offset": 0.0})
        controller._show_page(FEATHER.ScreenPage.MOD_SETTINGS)
        self.assertTrue(wait_for(lambda: parameter_action("z_offset") in shown_actions(controller._screen_root)))
        self.assertTrue(self.tap(controller, typer, parameter_action("z_offset"),
                                 lambda: controller._current_dialog() == FEATHER.ScreenDialog.NUMERIC_INPUT))
        feature = controller.feature_manager.get("settings")
        self.assertTrue(wait_for(lambda: {"mod.sign", "mod.dot", "mod.cancel"} <= shown_actions(controller._screen_root)))
        while feature.mod_edit_value:
            self.assertTrue(self.tap(controller, typer, "mod.backspace",
                                     lambda count=len(feature.mod_edit_value): len(feature.mod_edit_value) < count))
        for action, text in (("mod.sign", "-"), ("mod.key.1", "-1"), ("mod.dot", "-1."), ("mod.key.2", "-1.2")):
            self.assertTrue(self.tap(controller, typer, action, lambda: feature.mod_edit_value == text))
        self.assertTrue(self.tap(controller, typer, "mod.save",
                                 lambda: controller.params.variables["z_offset"] == -1.2))
        controller.reactor.flush_callbacks()
        self.assertEqual(controller.page, FEATHER.ScreenPage.MOD_SETTINGS)
        self.assertIsNone(controller._current_dialog())

    def caret_x(self, typer):
        """Where the loop last drew the text field's caret, a 2x26 bar."""
        for batch in reversed(typer.history()):
            for command in reversed(batch):
                tokens = str(getattr(command, "command", command)).split()
                if "fill" in tokens and tokens[tokens.index("-s") + 1:tokens.index("-s") + 3] == ["2", "26"]:
                    return int(tokens[tokens.index("-p") + 1])
        return None

    def test_the_loop_draws_the_text_editor_and_keys_type_through_its_layers(self):
        controller, typer = self.settings([mod_param("midi_on", str, "", "Startup MIDI")], {"midi_on": ""})
        with self.assertNoLogs(level="WARNING"):
            controller._show_page(FEATHER.ScreenPage.MOD_SETTINGS)
            self.assertTrue(wait_for(lambda: parameter_action("midi_on") in shown_actions(controller._screen_root)))
            self.assertTrue(self.tap(controller, typer, parameter_action("midi_on"),
                                     lambda: controller.page == FEATHER.ScreenPage.MOD_VALUE))
            feature = controller.feature_manager.get("settings")
            self.assertTrue(wait_for(lambda: {"keyboard.key.a", "keyboard.shift", "mod.save"}
                                     <= shown_actions(controller._screen_root)))
            self.assertTrue(wait_for(lambda: self.caret_x(typer) is not None))
            empty = self.caret_x(typer)
            start = len(typer.history())
            self.assertTrue(self.tap(controller, typer, "keyboard.key.a", lambda: feature.mod_edit_value == "a"))
            self.assertTrue(self.drew_since(typer, start, '-t "a"'))
            self.assertTrue(wait_for(lambda: self.caret_x(typer) > empty))
            # A key repaints the field alone, not the screen.
            self.assertFalse(self.drew_since(typer, start, "-p 0 0 -s %d %d" % (SCREEN_WIDTH, SCREEN_HEIGHT), timeout=0))
            typed = self.caret_x(typer)
            # Shift shows the upper layer, whose key types its capital.
            start = len(typer.history())
            self.assertTrue(self.tap(controller, typer, "keyboard.shift", lambda: feature.mod_keyboard_shift))
            self.assertTrue(self.drew_since(typer, start, '-t "A"'))
            self.assertTrue(self.tap(controller, typer, "keyboard.key.a", lambda: feature.mod_edit_value == "aA"))
            self.assertTrue(self.drew_since(typer, start, '-t "aA"'))
            self.assertTrue(wait_for(lambda: self.caret_x(typer) > typed))
            # The symbols layer offers the way back to letters.
            start = len(typer.history())
            self.assertTrue(self.tap(controller, typer, "keyboard.symbols", lambda: feature.mod_keyboard_symbols))
            self.assertTrue(self.drew_since(typer, start, '-t "ABC"'))

    def test_the_loop_draws_the_list_and_its_input_without_python(self):
        flag = mod_param("camera", bool, False, "Camera")
        level = mod_param("z_offset", float, 0.0, "Z offset")
        controller, typer = self.settings([flag, level], {"camera": False, "z_offset": 0.0})
        with self.assertNoLogs(level="WARNING"):
            controller._show_page(FEATHER.ScreenPage.MOD_SETTINGS)
            self.assertTrue(controller._screen_root.frames.attached)
            self.assertTrue(typer.drew("CAMERA"))
            self.assertTrue(wait_for(lambda: {parameter_action("camera"), parameter_action("z_offset")}
                                     <= shown_actions(controller._screen_root)))
        toggle = self.drawn_toggle(controller, typer, parameter_action("camera"))
        self.assertIsNotNone(toggle)
        self.assertFalse(toggle.active)

    def test_a_toggle_moves_on_the_loop_while_its_save_takes_no_tap(self):
        flag = mod_param("camera", bool, False, "Camera")
        controller, typer = self.settings([flag], {"camera": False})
        feature = controller.feature_manager.get("settings")
        action = parameter_action("camera")
        with self.assertNoLogs(level="WARNING"):
            controller._show_page(FEATHER.ScreenPage.MOD_SETTINGS)
            self.assertTrue(wait_for(lambda: action in shown_actions(controller._screen_root)))
            # The save holds the screen: Typer keeps only the wake region while the toggle moves.
            feature.mod_update_pending = True
            feature._move_mod_toggle("camera", True)
            controller.renderer.block_input()
            self.assertTrue(wait_for(lambda: shown_actions(controller._screen_root) == {"global.wake"}))
            # The value is saved before the move ends, whose last write shows it. The loop may draw the move's
            # writes in fewer transactions, so the wait is for what the screen shows, not for a count.
            controller.params.variables["camera"] = True
            controller.reactor.flush_callbacks()
            self.assertTrue(wait_for(lambda: self.toggle_active(controller, typer, action)))
            self.assertEqual(shown_actions(controller._screen_root), {"global.wake"})
            # The saved value draws the page anew, which takes taps again.
            feature.mod_update_pending = False
            feature._render_mod_settings()
            self.assertTrue(wait_for(lambda: action in shown_actions(controller._screen_root)))
        self.assertTrue(self.toggle_active(controller, typer, action))


if __name__ == "__main__":
    unittest.main()
