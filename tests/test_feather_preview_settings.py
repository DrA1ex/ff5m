## Preview settings, persistence, screen navigation and cancellation contracts.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import pathlib
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from tests.test_feather_workflows import base_controller
from tests.test_feather_model_preview import Session, Reactor
from tests.test_feather_print_preview import DeferredPreviewWorker
from tests.test_mod_params import MOD_PARAMS, ROOT
from tests.feather_render_test_helper import RenderCapture
from feather.features.manager import LazyFeatureManager
from feather.files import FileEntry
from feather.settings.mod import visible_parameters
from ff5m_ui.screen import ScreenPage
from ui import FeatherRenderer
import feather_screen as FEATHER


class EditorReactor(Reactor):
    def register_callback(self, callback, when=None):
        self.callbacks.append((callback, self.now if when is None else when))

    def flush_callbacks(self):
        while self.callbacks:
            self.callbacks.sort(key=lambda item: item[1])
            callback, when = self.callbacks.pop(0)
            self.now = max(self.now, when)
            callback(self.now)


class PreviewSettingsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        folder = pathlib.Path(directory.name)
        self.path = folder / "part.gcode"
        self.path.write_text(";TIME:60\n;LAYER_COUNT:57\nG1 X20\n")
        manager = MOD_PARAMS.ModParamManagement.__new__(MOD_PARAMS.ModParamManagement)
        manager.declaration, manager.filename = str(ROOT / "mod_params.json"), str(folder / "variables.cfg")
        manager.printer = SimpleNamespace(command_error=RuntimeError)
        manager.gcode = SimpleNamespace(error=RuntimeError)
        manager.reactor, manager._variables_lock = mock.Mock(), threading.RLock()
        manager._load_declaration()
        manager._reload()
        self.manager = manager
        controller = base_controller()
        controller.params = manager
        controller.reactor = EditorReactor()
        controller.reactor.now = 1
        controller.renderer = FeatherRenderer()
        self.capture = RenderCapture(controller.renderer)
        controller.feature_manager = LazyFeatureManager(controller, FEATHER.FEATURE_SPECS)
        controller.file_worker = DeferredPreviewWorker()
        controller.file_worker.submit_write = mock.Mock()
        def paint():
            owner = controller.feature_manager.owner_name(controller.page)
            if owner is not None:
                controller.feature_manager.get_for_page(controller.page).render(controller.page)
            elif controller.page in (ScreenPage.PRINT_PREVIEW, ScreenPage.MODEL_PREVIEW):
                controller._render_model_preview()
            else:
                controller.renderer.send(controller.renderer.begin_page(controller.page.name))
        controller._paint_page = paint
        controller._render_screen = lambda feature=None: FEATHER.FeatherScreen._render_screen(controller)
        controller._render_dialog = lambda: FEATHER.FeatherScreen._render_dialog(controller)
        controller._show_touch_unavailable = lambda: None
        controller._require_idle = lambda: None
        controller.last_action_time, controller.command_depth = -100, 0
        controller.selected_file = FileEntry(self.path.name, str(self.path), size=self.path.stat().st_size)
        self.controller = controller
        patcher = mock.patch("feather.model_preview.ModelPreview", Session)
        patcher.start()
        self.addCleanup(patcher.stop)
        controller._open_selected_model_preview()
        self.state = controller.model_preview
        self.feature = controller.feature_manager.peek("model_preview")

    def tap(self, action):
        self.controller.reactor.now += 0.3
        self.controller._dispatch_action(action)
        self.controller.reactor.flush_callbacks()

    def open_settings(self):
        self.tap("model.settings")
        self.assertEqual(self.controller.page, ScreenPage.PREVIEW_SETTINGS)
        self.assertIsNone(self.controller._current_dialog())
        self.assertIs(self.controller.model_preview, self.state)
        return self.controller.feature_manager.peek("settings")

    def edit(self, suffix):
        feature = self.controller.feature_manager.peek("settings")
        key = "feather_preview_" + suffix
        for _ in range(10):
            index = next((index for index, value in feature.mod_action_keys.items() if value == key), None)
            if index is not None:
                self.tap("mod.item.%d" % index)
                return
            self.tap("mod.prev" if feature.mod_page else "mod.next")
        self.fail("Parameter is not reachable: " + key)

    def option(self, name, apply=True):
        feature = self.controller.feature_manager.peek("settings")
        index = next(index for index, entry in enumerate(feature.parameter_option_entries) if entry.value == name)
        while feature.parameter_options_page_index != index // 4:
            self.tap("mod.options.next" if feature.parameter_options_page_index < index // 4 else "mod.options.prev")
        self.tap("mod.option.%d" % index)
        if apply:
            self.tap("mod.apply")

    def style(self, name):
        self.edit("type")
        self.option(name)

    def enter_number(self, text):
        feature = self.controller.feature_manager.peek("settings")
        for _ in range(len(feature.mod_edit_value)):
            self.tap("mod.backspace")
        for character in text:
            self.tap({".": "mod.dot", "-": "mod.sign"}.get(character, "mod.key." + character))

    def load_frame(self):
        self.controller.file_worker.finish(0)
        self.controller.reactor.now += 0.1
        self.feature._model_preview_frame_tick(self.controller.reactor.now)
        self.controller.file_worker.finish(len(self.controller.file_worker.submitted) - 1)

    def test_settings_button_opens_a_full_screen_with_mod_controls_and_back_returns(self):
        frame = self.capture.latest
        settings, printing = frame.button("model.settings"), frame.button("model.print")
        self.assertLess(settings.bounds.right, printing.bounds.x)
        self.assertEqual(settings.bounds.y, printing.bounds.y)
        self.state["yaw"], self.state["pitch"] = 12, -20
        feature = self.open_settings()
        self.assertTrue(self.capture.latest.has_text("PREVIEW SETTINGS"))
        self.assertTrue(self.capture.latest.has_action("nav.back"))
        self.assertTrue(self.capture.latest.has_text(self.manager.params_map["feather_preview_type"].label.upper()))
        cache = next(index for index, key in feature.mod_action_keys.items() if key == "feather_preview_cache")
        self.assertIn("mod.item.%d" % cache, self.capture.latest.toggles)
        self.tap("model.print")
        self.assertEqual(self.controller.page, ScreenPage.PREVIEW_SETTINGS)
        self.tap("nav.back")
        self.assertEqual(self.controller.page, ScreenPage.PRINT_PREVIEW)
        self.assertIs(self.controller.model_preview, self.state)
        self.assertEqual((self.state["yaw"], self.state["pitch"]), (12, -20))
        self.assertTrue(self.capture.latest.has_action("model.print"))

    def test_all_parameters_are_reachable_in_the_shared_paged_list(self):
        feature = self.open_settings()
        seen = set()
        while True:
            seen.update(feature.mod_action_keys.values())
            for button in self.capture.latest.buttons.values():
                if button.action.startswith("mod.item."):
                    self.assertGreaterEqual(button.bounds.y, 55)
                    self.assertLessEqual(button.bounds.bottom, 442)
            if not self.capture.latest.has_action("mod.next"):
                break
            self.tap("mod.next")
        self.assertEqual(seen, {"feather_preview_" + key for key in
                               ("type", "cache", "grid", "yaw", "pitch", "scale", "fit", "fps")})

    def test_style_selection_uses_the_mod_editor_and_changes_only_on_apply(self):
        self.load_frame()
        session = self.state["session"]
        feature = self.open_settings()
        self.edit("type")
        self.assertEqual(self.controller.page, ScreenPage.PARAMETER_OPTIONS)
        self.option("SOLID", apply=False)
        self.assertEqual(self.manager.variables["feather_preview_type"], "LAYER_LINES")
        self.assertFalse(session.cancelled)
        self.tap("mod.apply")
        self.assertEqual(self.controller.page, ScreenPage.PREVIEW_SETTINGS)
        self.assertEqual(self.state["settings"][0], 0)
        self.assertTrue(session.cancelled)
        keys = {param.key for param in feature._mod_parameters()}
        self.assertNotIn("feather_preview_grid", keys)
        self.assertNotIn("feather_preview_rotation_speed", keys)
        self.style("POINTS")
        self.assertIn("feather_preview_grid", {param.key for param in feature._mod_parameters()})

    def test_numeric_cancel_and_save_use_the_mod_numeric_editor(self):
        self.open_settings()
        self.edit("scale")
        self.assertEqual(self.controller.page, ScreenPage.MOD_VALUE)
        self.enter_number("1.5")
        self.tap("nav.back")
        self.assertEqual(self.controller.page, ScreenPage.PREVIEW_SETTINGS)
        self.assertEqual(self.manager.variables["feather_preview_scale"], 1)
        self.edit("scale")
        self.enter_number("1.5")
        self.tap("mod.save")
        self.assertEqual(self.controller.page, ScreenPage.PREVIEW_SETTINGS)
        self.assertEqual(self.state["scale"], 1.5)
        self.style("STACKED")
        self.assertEqual(self.state["scale"], 1.5)
        self.manager._reload()
        self.assertEqual(self.manager.variables["feather_preview_scale"], 1.5)

    def test_enum_cancel_returns_to_settings_without_saving(self):
        self.open_settings()
        self.edit("type")
        self.option("SOLID", apply=False)
        self.tap("mod.cancel")
        self.assertEqual(self.controller.page, ScreenPage.PREVIEW_SETTINGS)
        self.assertEqual(self.state["settings"][0], 4)

    def test_invalid_numeric_input_cannot_be_saved(self):
        feature = self.open_settings()
        self.edit("fps")
        self.enter_number("")
        self.assertFalse(self.capture.latest.has_action("mod.save"))
        with self.assertRaises(ValueError):
            feature._handle_mod_action("mod.save")
        self.assertEqual(self.manager.variables["feather_preview_fps"], 30)

    def test_storage_failure_preserves_the_running_preview_and_allows_retry(self):
        self.load_frame()
        session, settings = self.state["session"], self.state["settings"]
        feature = self.open_settings()
        self.edit("fit")
        self.option("VIEW", apply=False)
        with mock.patch.object(self.manager, "_save_all", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                feature._handle_mod_action("mod.apply")
        self.assertFalse(feature.mod_update_pending)
        self.assertEqual(self.state["settings"], settings)
        self.assertIs(self.state["session"], session)
        self.tap("mod.apply")
        self.assertTrue(self.state["settings"][4])

    def test_settings_pause_preview_updates_and_return_resumes_both_timers(self):
        self.open_settings()
        batches = len(self.capture.batches)
        self.controller.file_worker.finish(0)
        self.assertEqual(len(self.capture.batches), batches)
        self.assertEqual(self.feature._model_preview_frame_tick(self.controller.reactor.now), self.controller.reactor.NEVER)
        self.assertEqual(self.feature._model_preview_monitor_tick(self.controller.reactor.now), self.controller.reactor.NEVER)
        self.tap("nav.back")
        self.assertTrue(any(timer is self.state["monitor_timer"] for timer, when in self.controller.reactor.updates))
        self.feature._model_preview_frame_tick(self.controller.reactor.now)
        session = self.state["session"]
        self.open_settings()
        batches = len(self.capture.batches)
        self.controller.file_worker.finish(len(self.controller.file_worker.submitted) - 1)
        self.assertEqual(len(self.capture.batches), batches)
        self.tap("nav.back")
        self.assertIs(self.state["session"], session)

    def test_density_parameters_are_style_specific_and_use_shared_editors(self):
        feature = self.open_settings()
        for style, suffix, other, value in (("STACKED", "stacked_density", "points_density", 150),
                                            ("POINTS", "points_density", "stacked_density", 50)):
            with self.subTest(style=style):
                self.style(style)
                keys = {param.key for param in feature._mod_parameters()}
                self.assertIn("feather_preview_" + suffix, keys)
                self.assertNotIn("feather_preview_" + other, keys)
                self.edit(suffix)
                self.enter_number(str(value))
                generation = self.state["generation"]
                self.tap("mod.save")
                self.assertEqual(self.state["settings"][8], value)
                self.assertGreater(self.state["generation"], generation)
        self.style("SOLID")
        self.assertNotIn("feather_preview_points_density", {param.key for param in feature._mod_parameters()})

    def test_cache_toggle_and_resolution_changes_retire_the_previous_session(self):
        self.load_frame()
        session = self.state["session"]
        self.open_settings()
        self.edit("cache")
        self.assertFalse(self.state["use_cache"])
        self.assertTrue(session.cancelled)
        self.edit("grid")
        self.option("256")
        self.assertEqual(self.state["settings"][1], 256)
        self.manager._reload()
        self.assertEqual(self.manager.variables["feather_preview_grid"], 256)

    def test_hidden_preview_parameters_remain_hidden_from_general_mod_settings(self):
        feature = self.open_settings()
        preview_keys = {param.key for param in feature._mod_parameters()}
        general_keys = {param.key for param in visible_parameters(self.manager)}
        self.assertTrue(preview_keys)
        self.assertTrue(preview_keys.isdisjoint(general_keys))
        self.assertIn("feather_preview_back_to_files", general_keys)
        self.tap("nav.back")
        self.controller._show_page(ScreenPage.MOD_SETTINGS)
        self.assertIsNone(self.controller.model_preview)
        self.assertTrue(preview_keys.isdisjoint(param.key for param in feature._mod_parameters()))

    def test_current_view_button_saves_the_live_camera_as_defaults(self):
        self.state["yaw"], self.state["pitch"], self.state["scale"] = -83.27, -44.678, 1.45
        self.open_settings()
        button = self.capture.latest.button("model.capture_view")
        self.assertEqual(button.label, "USE CURRENT")
        self.assertTrue(self.capture.latest.has_text("DEFAULT VIEW"))
        self.assertTrue(self.capture.latest.has_text("YAW -83.3 / TILT -44.7 / 145%"))
        self.assertGreaterEqual(button.bounds.y, 76)
        with mock.patch.object(self.manager, "_save_all", wraps=self.manager._save_all) as save:
            self.tap("model.capture_view")
        save.assert_called_once()
        self.assertEqual(self.state["settings"][2:4], (-83.3, -44.7))
        self.assertEqual(self.state["settings"][7], 1.45)
        self.assertEqual((self.state["yaw"], self.state["pitch"], self.state["scale"]), (-83.27, -44.678, 1.45))
        self.manager._reload()
        self.tap("nav.back")
        self.controller._show_page(ScreenPage.FILE_CONFIRM)
        self.controller._open_selected_model_preview()
        state = self.controller.model_preview
        self.assertEqual((state["yaw"], state["pitch"], state["scale"]), (-83.3, -44.7, 1.45))

    def test_current_view_storage_failure_does_not_change_any_defaults(self):
        self.state["yaw"], self.state["pitch"], self.state["scale"] = 60, 15, 1.5
        feature = self.open_settings()
        before, settings = dict(self.manager.variables), self.state["settings"]
        with mock.patch.object(self.manager, "_save_all", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                feature._capture_preview_view()
        self.assertEqual(self.manager.variables, before)
        self.assertEqual(self.state["settings"], settings)
        self.assertEqual((self.state["yaw"], self.state["pitch"], self.state["scale"]), (60, 15, 1.5))

    def test_leaving_the_preview_settings_flow_retires_the_preview(self):
        self.load_frame()
        self.open_settings()
        self.edit("scale")
        session = self.state["session"]
        self.controller._show_page(ScreenPage.FILE_CONFIRM)
        self.assertIsNone(self.controller.model_preview)
        self.assertTrue(session.cancelled)

    def test_leaving_during_a_parameter_update_cannot_reopen_settings(self):
        self.load_frame()
        feature = self.open_settings()
        index = next(index for index, key in feature.mod_action_keys.items() if key == "feather_preview_cache")
        self.controller.reactor.now += 0.3
        self.controller._dispatch_action("mod.item.%d" % index)
        self.assertTrue(feature.mod_update_pending)
        self.controller._show_page(ScreenPage.PRINTING)
        self.controller.reactor.flush_callbacks()
        self.assertFalse(feature.mod_update_pending)
        self.assertIsNone(self.controller.model_preview)
        self.assertEqual(self.controller.page, ScreenPage.PRINTING)

    def test_fit_change_during_an_inflight_frame_renders_the_new_fit_after_return(self):
        class FitSession(Session):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.fits = []

            def render(self, *args, **kwargs):
                self.fits.append(self.settings[4])
                return super().render(*args, **kwargs)

        with mock.patch("feather.model_preview.ModelPreview", FitSession):
            self.controller.file_worker.finish(0)
            self.controller.reactor.now += 0.1
            self.feature._model_preview_frame_tick(self.controller.reactor.now)
            session = self.state["session"]
            self.open_settings()
            self.edit("fit")
            self.option("VIEW")
            self.tap("nav.back")
            self.controller.file_worker.finish(len(self.controller.file_worker.submitted) - 1)
            self.controller.reactor.now += 0.1
            self.feature._model_preview_frame_tick(self.controller.reactor.now)
            self.controller.file_worker.finish(len(self.controller.file_worker.submitted) - 1)
        self.assertEqual(session.fits, [False, True])
        self.assertIs(self.state["session"], session)

    def test_stale_worker_completion_cannot_publish_after_a_style_change(self):
        self.controller.file_worker.finish(0)
        self.controller.reactor.now += 0.1
        self.feature._model_preview_frame_tick(self.controller.reactor.now)
        work, ready = self.controller.file_worker.submitted[-1]
        old = self.state["session"]
        blob = work()
        self.open_settings()
        self.style("MODEL_EDGES")
        ready(blob, None)
        self.assertTrue(old.cancelled)
        self.assertIsNone(self.state["blob"])
        self.assertEqual(self.state["settings"][0], 3)

