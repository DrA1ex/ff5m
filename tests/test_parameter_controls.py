## Responsive settings layout and pagination contracts.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import unittest
import shlex
from types import SimpleNamespace
from unittest import mock

from tests import test_feather_preview_settings as preview_fixture
from tests.feather_render_test_helper import RenderCapture, RenderFrame, shown_actions
from feather.settings import mod
from feather.screen.pagination import Pagination
from ff5m_ui import parameter_controls as controls
from ff5m_ui.model_preview import settings
from ui import FeatherRenderer, Rect, ScreenRoot, ThemeColor
from ui.components import Text
from ui.font_metrics import get_font_metrics
from ui.bindings import resolve
from ff5m_ui.screen import ScreenPage
from ff5m_ui.keys import AppPage


class ParameterControlsTest(unittest.TestCase):
    SIZES = ((800, 480), (640, 400), (480, 480), (1024, 600))

    def setUp(self):
        self.fixture = preview_fixture.PreviewSettingsTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.manager = self.fixture.manager

    def bounds(self, size, kind="editor"):
        renderer = SimpleNamespace(screen_width=size[0], screen_height=size[1])
        return controls.content_bounds(renderer, kind=kind)

    def assert_page_fits(self, page):
        renderer = FeatherRenderer()
        commands = page.draw(renderer, page.state)
        for node in page.root.walk():
            with self.subTest(node=type(node).__name__):
                bounds = page.layout.rect(node)
                self.assertTrue(page.bounds.contains(bounds))
                if isinstance(node, Text) and node.kwargs.get("wrap"):
                    self.assertLessEqual(get_font_metrics().text_height(str(resolve(node.value, getattr(node, "_measurement_state", page.state))), node.font,
                                                                       max_width=bounds.width, wrap=True), bounds.height)
        frame = RenderFrame(commands, renderer)
        for button in frame.buttons.values():
            self.assertTrue(page.bounds.contains(Rect(button.bounds.x, button.bounds.y,
                                                     button.bounds.width, button.bounds.height)))
        return frame

    def list_page(self, sections, index, count, next_label, bounds, preview=False, leading=None):
        page = controls.create_list_tree(preview=preview, bounds=bounds)
        page.state.update(controls.list_state(self.manager, sections, index, count, next_label, leading))
        return page

    def test_preview_lists_repaginate_and_every_parameter_remains_reachable(self):
        params = [self.manager.params_map[key] for key in settings.parameter_keys("STACKED", "file")]
        for size in self.SIZES:
            with self.subTest(size=size):
                bounds = self.bounds(size, "list")
                width = controls.list_width(bounds)
                leading = settings.current_view_content(self.fixture.state)
                leading_pitch = settings.current_view_pitch(self.fixture.state, width)
                pitch = lambda index, param: controls.parameter_pitch(param, width)
                pages = mod.category_pages(self.manager, params, label="PRINT PREVIEW", leading_height=leading_pitch,
                                           capacity=bounds.height, item_pitch=pitch)
                seen = []
                for index, sections in enumerate(pages):
                    page = self.list_page(sections, index, len(pages), None, bounds, preview=True,
                                          leading=leading if index == 0 else None)
                    self.assert_page_fits(page)
                    seen.extend(param.key for _, param in mod.page_parameters(sections))
                self.assertEqual(seen, [param.key for param in params])

    def test_general_mod_lists_keep_all_visible_parameters_reachable(self):
        params = mod.visible_parameters(self.manager)
        for size in self.SIZES:
            with self.subTest(size=size):
                bounds = self.bounds(size, "list")
                width = controls.list_width(bounds)
                pitch = lambda index, param: controls.parameter_pitch(param, width)
                pages = mod.category_pages(self.manager, params, capacity=bounds.height, item_pitch=pitch)
                seen = []
                for index, sections in enumerate(pages):
                    next_label = mod.next_category_hint(pages, index, capacity=bounds.height, item_pitch=pitch)
                    page = self.list_page(sections, index, len(pages), next_label, bounds)
                    self.assert_page_fits(page)
                    seen.extend(param.key for _, param in mod.page_parameters(sections))
                self.assertEqual(seen, [param.key for param in params])

    def test_settings_paging_materializes_only_visible_rows(self):
        feature = self.fixture.open_settings()
        for size in ((800, 480), (480, 480)):
            with self.subTest(size=size):
                renderer = self.fixture.controller.renderer
                renderer.screen_width, renderer.screen_height = size
                self.fixture.controller._show_page(ScreenPage.MOD_SETTINGS)
                for action in ("mod.next", "mod.prev"):
                    self.fixture.tap(action)
                    rows = feature._mod_tree.node("settings.rows")
                    built = [node._item_scope.item["parameter"] for node in rows.items
                             if "parameter" in node._item_scope.item]
                    self.assertEqual(set(built), set(feature.mod_action_keys.values()))

    def test_warm_settings_pages_reuse_trees_and_recheck_values_and_geometry(self):
        feature = self.fixture.open_settings()
        controller = self.fixture.controller
        controller._show_page(ScreenPage.MOD_SETTINGS)
        self.fixture.tap("mod.next")
        with mock.patch.object(controls, "create_list_tree", wraps=controls.create_list_tree) as create:
            self.fixture.tap("mod.prev")
            self.fixture.tap("mod.next")
            self.fixture.tap("mod.prev")
            self.assertEqual(create.call_count, 0)
            # A value row: its button label shows the value (a bool row is a toggle with no label).
            key = next(key for key in feature.mod_action_keys.values()
                       if mod.parameter_kind(self.manager.params_map[key]) in ("enum", "int", "float"))
            param = self.manager.params_map[key]
            original = self.manager.variables[key]
            if mod.parameter_kind(param) == "enum":
                self.manager.variables[key] = next(member.value for member in param.type if member.value != original)
            else:
                self.manager.variables[key] = original + 1
            feature._render_mod_settings()
            self.assertEqual(create.call_count, 0)
            self.assertIn(mod.display_value(self.manager, param), self.fixture.capture.latest.button(controls.parameter_action(key)).label)
            controller.renderer.screen_width, controller.renderer.screen_height = 480, 480
            feature._render_mod_settings()
            self.assertEqual(create.call_count, 1)
        self.assertTrue(any(self.fixture.capture.latest.has_action(controls.parameter_action(key))
                            for key in feature.mod_action_keys.values()))

    def test_cached_pages_refresh_theme_and_reject_old_touch_generations(self):
        feature = self.fixture.open_settings()
        controller = self.fixture.controller
        controller._show_page(ScreenPage.MOD_SETTINGS)
        first_key = next(iter(feature.mod_action_keys.values()))
        label = str(self.manager.params_map[first_key].label).upper()
        before = controller.renderer.color(ThemeColor.PRIMARY)
        generation = controller.renderer.generation
        self.fixture.tap("mod.next")
        controller.renderer.set_theme("AMBER")
        with mock.patch.object(controls, "create_list_tree", wraps=controls.create_list_tree) as create:
            self.fixture.tap("mod.prev")
            self.assertEqual(create.call_count, 0)
        after = controller.renderer.color(ThemeColor.PRIMARY)
        self.assertNotEqual(after, before)
        commands = [shlex.split(command) for command in self.fixture.capture.batches[-1]]
        text = next(parts for parts in commands if len(parts) > 1 and parts[1] == "text"
                    and "-t" in parts and parts[parts.index("-t") + 1] == label)
        self.assertEqual(text[text.index("-c") + 1], after)
        action = controls.parameter_action(first_key)
        self.assertIsNone(controller.renderer.decode_action("%d:%s" % (generation, action)))
        current = controller.renderer.generation
        self.assertEqual(controller.renderer.decode_action("%d:%s" % (current, action)), action)

    def test_recycled_row_opens_current_parameter_and_discards_previous_action(self):
        feature = self.fixture.open_settings()
        self.fixture.controller._show_page(ScreenPage.MOD_SETTINGS)
        tree = feature._mod_tree
        first = next(key for key in feature.mod_action_keys.values() if mod.parameter_kind(self.manager.params_map[key]) != "bool")
        old_action = controls.parameter_action(first)
        old_rows = set(tree.node("settings.rows").items)
        self.fixture.tap("mod.next")
        self.assertIs(feature._mod_tree, tree)
        self.assertTrue(old_rows.intersection(tree.node("settings.rows").items))
        self.assertIsNone(feature.resolve_semantic_action(ScreenPage.MOD_SETTINGS, old_action))
        key = next(key for key in feature.mod_action_keys.values() if mod.parameter_kind(self.manager.params_map[key]) != "bool")
        action = controls.parameter_action(key)
        self.assertEqual(feature.resolve_semantic_action(ScreenPage.MOD_SETTINGS, action).payload, key)
        self.fixture.tap(action)
        self.assertEqual(feature.mod_parameter.key, key)

    def test_enum_and_theme_choices_fit_different_viewports(self):
        for size in self.SIZES:
            bounds = self.bounds(size)
            for suffix in ("grid", "fit", "type"):
                with self.subTest(size=size, parameter=suffix):
                    param = self.manager.params_map["feather_preview_" + suffix]
                    entries = [SimpleNamespace(value=name, label=name, description=mod.option_description(param, name), enabled=True)
                               for name in mod.enum_names(param)]
                    capacity = controls.options_capacity(param, bounds)
                    multipage = len(entries) > capacity
                    for index in range((len(entries) + capacity - 1) // capacity):
                        page = controls.create_options_page(param, multipage, bounds=bounds)
                        page.state.update(controls.options_values(
                            FeatherRenderer(), Pagination(entries, index, capacity), entries[0].value))
                        self.assert_page_fits(page)
            param = self.manager.params_map["feather_theme"]
            entries = [SimpleNamespace(value=name, label=name, description="Sample theme", enabled=True)
                       for name in ("DEFAULT", "AMBER", "DARK", "SYNTH", "CYBERDECK", "RED", "CLASSIC")]
            capacity = controls.theme_capacity(bounds)
            for index in range((len(entries) + capacity - 1) // capacity):
                with self.subTest(size=size, theme_page=index):
                    page = controls.create_theme_page(bounds=bounds)
                    page.state.update(controls.theme_values(
                        FeatherRenderer(), Pagination(entries, index, capacity), "DEFAULT"))
                    self.assert_page_fits(page)

    def test_text_editor_exposes_typing_and_editor_actions_at_each_size(self):
        for size in self.SIZES:
            with self.subTest(size=size):
                param = self.manager.params_map["midi_start"]
                page = controls.create_text_page(param, False, False, bounds=self.bounds(size))
                page.state.update(controls.text_values("MIDI text", 4))
                renderer = FeatherRenderer()
                RenderCapture(renderer)
                root = ScreenRoot(renderer, page, Rect(0, 0, *size), AppPage.SCREEN_ROOT)
                root.paint()
                self.assertTrue({"keyboard.key.q", "keyboard.key.z", "keyboard.space", "keyboard.backspace",
                                 "mod.save", "mod.cancel"} <= shown_actions(root))

    def test_numeric_editor_only_offers_optional_keys_allowed_by_the_parameter(self):
        for name, auxiliary in (
            ("mod_check_update_interval", ()),
            ("cell_weight", ("mod.sign",)),
            ("safe_z", ("mod.dot",)),
            ("z_offset", ("mod.dot", "mod.sign")),
        ):
            for size in self.SIZES:
                with self.subTest(parameter=name, size=size):
                    param = self.manager.params_map[name]
                    page = controls.create_numeric_page(param, bounds=self.bounds(size, "numeric"))
                    page.state.update(controls.numeric_values("1"))
                    renderer = FeatherRenderer()
                    RenderCapture(renderer)
                    root = ScreenRoot(renderer, page, Rect(0, 0, *size), AppPage.SCREEN_ROOT)
                    root.paint()
                    actions = shown_actions(root)
                    self.assertEqual(actions.intersection(("mod.dot", "mod.sign")), set(auxiliary))
                    self.assertTrue({"mod.key.0", "mod.backspace", "mod.cancel", "mod.save"} <= actions)

    def test_smaller_screen_choice_paging_save_and_cancel_preserve_preview_session(self):
        self.fixture.controller.renderer.screen_width = 640
        self.fixture.controller.renderer.screen_height = 400
        feature = self.fixture.open_settings()
        self.fixture.edit("type")
        target = next(index for index, entry in enumerate(feature.parameter_option_entries) if entry.value == "POINTS")
        for _ in range(len(feature.parameter_option_entries)):
            if self.fixture.capture.latest.has_action(controls.option_action(target)):
                break
            self.fixture.tap("mod.options.next")
        self.assertTrue(self.fixture.capture.latest.has_action(controls.option_action(target)))
        before = self.manager.variables["feather_preview_type"]
        self.fixture.tap(controls.option_action(target))
        self.fixture.tap("mod.cancel")
        self.assertEqual(self.manager.variables["feather_preview_type"], before)
        self.fixture.edit("type")
        while not self.fixture.capture.latest.has_action(controls.option_action(target)):
            self.fixture.tap("mod.options.next")
        self.fixture.tap(controls.option_action(target))
        self.fixture.tap("mod.apply")
        param = self.manager.params_map["feather_preview_type"]
        self.assertEqual(param.type(self.manager.variables[param.key]).name, "POINTS")
        self.assertEqual(self.fixture.controller.page, ScreenPage.PREVIEW_SETTINGS)
        self.assertIs(self.fixture.controller.model_preview, self.fixture.state)
