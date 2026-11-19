## First-run setup behavior through the real store, feature routes and renderer.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import copy
import json
import os
import pathlib
import shlex
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from tests.test_mod_params import MOD_PARAMS, ROOT
from tests.test_feather_workflows import base_controller, StatusObject
from tests.test_feather_preview_settings import EditorReactor
from tests.feather_render_test_helper import RenderCapture, shown_actions
from tests.feather_typer_loop import TyperLoop, wait_for
from feather.features.manager import LazyFeatureManager
from feather.setup import SetupProgress
from ff5m_ui.parameter_controls import ParameterCommand
from ff5m_ui.network import NetworkCommand
from ff5m_ui.screen import ScreenPage
from ff5m_ui.setup import SetupCommand
from ff5m_ui.z_offset.actions import SAFE_CALIBRATE, SAFE_PROBE, SAFE_SAVE, SAFE_SKIP
from ui import Command, FeatherRenderer, ThemeColor
import feather_screen as FEATHER


class SetupTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = pathlib.Path(folder.name)
        self.manager = MOD_PARAMS.ModParamManagement.__new__(MOD_PARAMS.ModParamManagement)
        self.manager.declaration = str(ROOT / "mod_params.json")
        self.manager.filename = str(self.folder / "variables.cfg")
        self.manager.printer = SimpleNamespace(command_error=RuntimeError, send_event=lambda name: None)
        self.manager.gcode = SimpleNamespace(error=RuntimeError)
        self.manager.reactor = mock.Mock()
        self.manager._load_declaration()
        self.manager._reload()
        self.manager.changes_gcode_present = False
        self.config = {"config": {}, "save_config_pending_items": {}, "save_config_pending": False}
        c = self.host = base_controller()
        for name in ("_paint_page", "_render_screen", "_paint_page_values", "_render_dialog", "_home_content", "_toast"):
            c.__dict__.pop(name, None)
        c.params = self.manager
        c.renderer = FeatherRenderer()
        c.renderer.start = lambda: None
        c._enable_backlight = lambda: None
        c.startup_timer = None
        c.gcode_move = StatusObject({"homing_origin": (0, 0, 0.125, 0)})
        c.probe = SimpleNamespace(z_offset=-0.25)
        self.capture = RenderCapture(c.renderer)
        c._show_touch_unavailable = lambda: None
        c._refresh_emergency_stop = lambda *a, **kw: False
        c.reactor = EditorReactor()
        c.reactor.now = 10
        c.reactor.NOW = 0
        c.printer = SimpleNamespace(lookup_object=self.lookup, get_state_message=lambda: ("Ready", "ready"))
        c.feature_manager = LazyFeatureManager(c, FEATHER.FEATURE_SPECS)
        c.heater_bed = StatusObject({"temperature": 20, "target": 0})
        c.extruder.heater = SimpleNamespace(min_temp=0, max_temp=300)
        c.heater_bed.heater = SimpleNamespace(min_temp=0, max_temp=130)
        c.bed_mesh = None
        c.heating_materials, c.heating_profiles = ("PLA",), {"PLA": (220, 60)}
        c.shutdown_active, c.restart_pending, c.startup_restarting = False, False, False
        c.last_action_time = c.last_touch_time = 0
        c.dimmed, c.command_depth, c.timer = False, 0, None
        self.hook = SimpleNamespace(returncode=None)
        self.effect_failure = False
        self.events = []
        c.gcode.run_script_from_command = self.run_command
        self.feature = c.feature_manager.get("setup")

    def lookup(self, name, default=None):
        if name == "configfile":
            return SimpleNamespace(get_status=lambda _: self.config)
        if name == "gcode_shell_command setup_config":
            return self.hook
        return default

    def run_command(self, command):
        self.events.append((command, copy.deepcopy(self.feature.progress.data)))
        if "CMD=setup_config" in command:
            self.hook.returncode = 1 if self.effect_failure else 0

    def tap(self, key, payload=None):
        self.dispatch(Command(key, payload).wire_id)

    def dispatch(self, wire):
        self.host.reactor.now += 1
        self.assertIn(wire, shown_actions(self.host._screen_root))
        self.host._dispatch_action(wire)

    def select_option(self, value):
        settings = self.host.feature_manager.get("settings")
        index = next(index for index, entry in enumerate(settings.parameter_option_entries) if entry.value == value)
        self.tap(ParameterCommand.OPTION, index)

    def fresh_feature(self):
        self.host.feature_manager = LazyFeatureManager(self.host, FEATHER.FEATURE_SPECS)
        self.feature = self.host.feature_manager.get("setup")
        return self.feature

    def open_options(self, group="essentials"):
        self.feature.open()
        if group == "essentials":
            self.tap(SetupCommand.OPEN, "options")
        else:
            self.tap(SetupCommand.OPEN, "additional")
            if Command(SetupCommand.OPEN, "group:" + group).wire_id not in shown_actions(self.host._screen_root):
                self.tap(SetupCommand.NEXT)
            self.tap(SetupCommand.OPEN, "group:" + group)

    def test_first_run_intro_restarts_and_confirmed_skip_survives_restart(self):
        before = dict(self.manager.variables)
        self.feature.startup()
        self.assertEqual(self.host.page, ScreenPage.SETUP_WELCOME)
        self.tap(SetupCommand.ACTION)
        self.assertEqual(self.host.page, ScreenPage.SETUP_NETWORK)
        self.fresh_feature().startup()
        self.assertEqual(self.host.page, ScreenPage.SETUP_WELCOME)
        self.tap(SetupCommand.ACTION)
        self.assertEqual(self.host.page, ScreenPage.SETUP_NETWORK)
        self.tap(NetworkCommand.CONTINUE)
        self.assertEqual(self.host.page, ScreenPage.PARAMETER_OPTIONS)
        self.fresh_feature().startup()
        self.assertEqual(self.host.page, ScreenPage.SETUP_WELCOME)
        self.tap(SetupCommand.ACTION)
        self.tap(NetworkCommand.CONTINUE)
        self.assertEqual(self.host.page, ScreenPage.PARAMETER_OPTIONS)
        self.assertEqual(self.capture.latest.button("mod.apply").label, "CONTINUE")
        self.dispatch("mod.apply")
        self.host.reactor.flush_callbacks()
        self.assertEqual(self.host.page, ScreenPage.SETUP)
        self.assertNotIn("nav.back", shown_actions(self.host._screen_root))
        for step in ("timezone", "options", "additional", "calibration"):
            self.assertIn(Command(SetupCommand.OPEN, step).wire_id, shown_actions(self.host._screen_root))
        self.tap(SetupCommand.LEAVE)
        dialog = self.host._find_dialog(FEATHER.ScreenDialog.MESSAGE)
        self.assertEqual(dialog.content["tone"], "warning")
        self.assertIn("before the first print", dialog.content["message"])
        self.assertIn("new bed mesh", dialog.content["message"])
        self.assertIn("Setup Wizard", dialog.content["message"])
        self.assertIn("Settings", dialog.content["message"])
        self.dispatch(FEATHER.dialog_ui.OK.wire_id)
        self.assertTrue(self.feature.active)
        self.assertFalse(self.feature.progress.data["skipped"])
        self.tap(SetupCommand.LEAVE)
        self.dispatch(FEATHER.dialog_ui.SETUP_SKIP.wire_id)
        self.assertFalse(self.feature.active)
        self.assertEqual(self.manager.variables, before)
        self.fresh_feature().startup()
        self.assertEqual(self.host.page, ScreenPage.IDLE_HOME)
        self.assertFalse(self.feature.progress.complete)
        self.assertEqual(self.feature.progress.data["selections"], {"feather_theme": "DEFAULT"})
        self.host._show_page(ScreenPage.SETTINGS)
        self.dispatch("settings.setup")
        self.assertEqual(self.host.page, ScreenPage.SETUP)
        self.tap(SetupCommand.LEAVE)
        self.assertEqual(self.host.page, ScreenPage.SETTINGS)

    def test_option_navigation_help_and_staging_do_not_restart(self):
        self.open_options()
        before = self.manager.snapshot(("tune_config",))
        self.tap(SetupCommand.TOGGLE, "tune_config")
        self.assertNotEqual(self.feature.progress.data["draft"]["tune_config"], before["tune_config"])
        self.assertEqual(self.manager.snapshot(("tune_config",)), before)
        self.tap(SetupCommand.HELP, "tune_config")
        dialog = self.host._find_dialog(FEATHER.ScreenDialog.MESSAGE)
        self.assertIn("restart", dialog.content["message"])
        self.host._close_dialog(dialog)
        self.dispatch("nav.back")
        self.tap(SetupCommand.OPEN, "additional")
        action = Command(SetupCommand.ACTION).wire_id
        self.assertEqual(self.capture.latest.button(action).label, "APPLY & RESTART")
        self.tap(SetupCommand.OPEN, "group:protection")
        self.assertEqual(self.capture.latest.button(action).label, "NEXT")
        self.host.reactor.now += 1
        self.host._dispatch_action("nav.back")
        self.assertEqual(self.feature.options_level, "groups")
        self.host.reactor.now += 1
        self.host._dispatch_action("nav.back")
        self.assertEqual(self.host.page, ScreenPage.SETUP)
        self.assertEqual(self.capture.latest.button(action).label, "APPLY & RESTART")
        self.assertFalse(self.events)

    def test_review_options_and_finish_without_replacing_existing_calibration(self):
        self.host.bed_mesh = StatusObject({"profiles": {"my-bed": {}}})
        self.open_options()
        before = dict(self.manager.variables)
        action = Command(SetupCommand.ACTION).wire_id
        self.assertEqual(self.capture.latest.button(action).label, "ADDITIONAL OPTIONS")
        self.tap(SetupCommand.ACTION)
        self.assertEqual(self.capture.latest.button(action).label, "REVIEW CALIBRATION")
        self.tap(SetupCommand.ACTION)
        self.assertEqual(self.host.page, ScreenPage.SETUP_CALIBRATION)
        self.assertTrue(any("my-bed" in text.value for text in self.capture.latest.texts))
        self.tap(SetupCommand.ACTION)
        self.host.reactor.now += 1
        self.host._dispatch_action(FEATHER.dialog_ui.SETUP_FINISH.wire_id)
        self.assertEqual(self.host.page, ScreenPage.IDLE_HOME)
        self.assertTrue(self.feature.progress.complete)
        self.assertEqual(self.manager.variables, before)
        self.assertFalse(self.events)

    def test_pending_options_are_visible_and_apply_from_manual_review(self):
        self.feature.progress.finish()
        self.host._show_page(ScreenPage.SETTINGS)
        self.host._dispatch_action("settings.setup")
        self.tap(SetupCommand.OPEN, "options")
        self.tap(SetupCommand.TOGGLE, "tune_config")
        self.tap(SetupCommand.ACTION)
        self.assertEqual(self.capture.latest.button(Command(SetupCommand.ACTION).wire_id).label, "APPLY & RESTART")
        self.tap(SetupCommand.ACTION)
        dialog = self.host._find_dialog(FEATHER.ScreenDialog.MESSAGE)
        self.assertIn("restart", dialog.content["title"])
        self.host._close_dialog(dialog)
        self.dispatch("nav.back")
        self.tap(SetupCommand.LEAVE)
        self.assertEqual(self.host.page, ScreenPage.SETTINGS)
        self.assertFalse(self.events)
        self.assertTrue(self.feature.progress.complete)
        self.assertEqual(self.feature.progress.data["draft"], {"tune_config": 1})

    def test_shared_editors_return_to_setup_and_keep_enum_and_numeric_values(self):
        self.open_options("preparation")
        self.tap(SetupCommand.EDIT, "zclear")
        self.assertEqual(self.host.page, ScreenPage.PARAMETER_OPTIONS)
        self.select_option("FF1")
        self.dispatch("mod.apply")
        self.host.reactor.flush_callbacks()
        self.assertEqual(self.host.page, ScreenPage.SETUP_OPTIONS)
        self.assertFalse(self.feature.progress.data["draft"])
        self.feature._open_options("group:preparation")
        self.tap(SetupCommand.EDIT, "clear_cooldown_temp")
        self.assertEqual(self.host.page, ScreenPage.MOD_VALUE)
        settings = self.host.feature_manager.get("settings")
        settings.mod_edit_value = "170"
        self.host.reactor.now += 1
        self.host._dispatch_action("mod.save")
        self.host.reactor.flush_callbacks()
        self.assertEqual(self.manager.variables["clear_cooldown_temp"], 170)
        self.assertEqual(self.host.page, ScreenPage.SETUP_OPTIONS)
        self.assertEqual(self.fresh_feature().progress.data["selections"],
                         {"clear_cooldown_temp": 170, "zclear": "FF1"})

    def test_visibility_inverted_controls_and_kamp_conflict(self):
        from feather.settings import mod
        self.open_options("protection")
        self.feature._set_option("weight_check", 0)
        self.host._paint_page_values()
        editor = Command(SetupCommand.EDIT, "weight_check_mode").wire_id
        self.assertNotIn(editor, shown_actions(self.host._screen_root))
        self.feature._set_option("weight_check", 1)
        self.host._paint_page_values()
        self.assertIn(editor, shown_actions(self.host._screen_root))
        self.feature._open_options("group:preparation")
        self.feature._set_option("disable_priming", 1)
        self.host._render_screen(force=True)
        toggle = Command(SetupCommand.TOGGLE, "disable_priming").wire_id
        self.assertFalse(self.capture.latest.toggles[toggle].active)
        purge = Command(SetupCommand.EDIT, "zclear").wire_id
        self.assertNotIn(purge, shown_actions(self.host._screen_root))
        self.assertNotIn(self.manager.params_map["zclear"], mod.visible_parameters(self.manager))
        self.tap(SetupCommand.TOGGLE, "disable_priming")
        self.assertIn(purge, shown_actions(self.host._screen_root))
        self.manager.set_value("zclear", "WILD_BILL")
        param = self.manager.params_map["zclear"]
        self.assertEqual(mod.display_value(self.manager, param), "WILD_BILL")
        self.assertEqual(mod.display_value(self.manager, param, {"zclear": "WILD_BILL"}), "WILD_BILL")
        self.feature._set_option("print_leveling", 1)
        self.feature._set_option("use_kamp", 1)
        self.assertFalse(self.manager.variables["print_leveling"])
        self.host._paint_page_values()
        self.assertNotIn(purge, shown_actions(self.host._screen_root))
        self.assertNotIn(param, mod.visible_parameters(self.manager))

    def test_dependent_mesh_options_remain_reachable_on_the_second_page(self):
        self.manager.set_value("bed_mesh_validation", 1)
        self.manager.set_value("bed_mesh_validation_action", "Z_OFFSET")
        self.open_options("mesh")
        self.tap(SetupCommand.NEXT)
        self.tap(SetupCommand.EDIT, "bed_mesh_validation_z_offset_mode")
        self.assertEqual(self.host.page, ScreenPage.PARAMETER_OPTIONS)
        self.host.reactor.now += 1
        self.host._dispatch_action("nav.back")
        self.tap(SetupCommand.PREVIOUS)
        self.tap(SetupCommand.HELP, "use_kamp")
        self.assertIsNotNone(self.host._find_dialog(FEATHER.ScreenDialog.MESSAGE))

    def test_restart_confirmation_precedes_effects_and_readback_clears_draft(self):
        self.open_options()
        self.feature._set_option("tune_config", 1)
        self.feature._set_option("tune_klipper", 0)
        self.feature._confirm_apply()
        self.assertFalse(self.events)
        dialog = self.host._find_dialog(FEATHER.ScreenDialog.MESSAGE)
        self.assertIn(self.manager.params_map["tune_config"].label, dialog.content["message"])
        self.feature.confirm_dialog(FEATHER.DialogRoute.SETUP_APPLY)
        self.assertEqual(self.events[-1][0], 'RUN_SHELL_COMMAND CMD=restart_klipper PARAMS="--hard"')
        self.assertEqual(self.events[-1][1]["pending"], "settings")
        self.assertTrue(self.host.restart_pending)
        self.assertEqual(len([event for event, _ in self.events if "CMD=setup_config" in event]), 1)
        self.host.restart_pending = False
        self.fresh_feature().startup()
        self.assertEqual(self.host.page, ScreenPage.SETUP)
        self.assertFalse(self.feature.progress.data["draft"])
        self.assertIsNone(self.feature.progress.data["pending"])
        self.assertEqual(self.feature.progress.data["selections"], {"tune_config": 1, "tune_klipper": 0})

    def test_failed_effect_keeps_explicit_retry_even_if_persistence_matches(self):
        self.open_options()
        self.feature._set_option("tune_config", 1)
        self.effect_failure = True
        with self.assertRaisesRegex(RuntimeError, "Unable to apply"):
            self.feature.apply_settings()
        self.assertEqual(self.manager.variables["tune_config"], 1)
        self.feature._set_option("tune_config", 0)
        self.manager.set_value("tune_config", 0)
        self.feature._set_option("tune_config", 0)
        self.effect_failure = False
        self.feature.apply_settings()
        self.assertIn('PARAMS="tune_config 0 --apply-only"', self.events[-2][0])

    def test_rejected_restart_restores_screen_and_keeps_options_for_retry(self):
        self.open_options()
        self.feature._set_option("tune_config", 1)
        original = self.host.gcode.run_script_from_command
        def reject(command):
            if command == "RESTART":
                raise RuntimeError("Restart rejected")
            return original(command)
        self.host.gcode.run_script_from_command = reject
        with self.assertRaisesRegex(RuntimeError, "Restart rejected"):
            self.feature.apply_settings()
        self.assertFalse(self.host.restart_pending)
        self.assertIsNotNone(self.host.timer)
        self.assertEqual(self.feature.progress.data["draft"], {"tune_config": 1})
        self.assertEqual(self.host.page, ScreenPage.SETUP_OPTIONS)

    def test_tuning_invalidates_mesh_and_z_but_preserves_shaper_completion(self):
        self.open_options()
        self.feature.progress.write(completed=["z_offset", "mesh", "shaper"])
        self.feature._set_option("tune_config", 1)
        self.feature.apply_settings()
        self.assertEqual(self.feature.progress.data["completed"], ["shaper"])

    def test_meshing_choices_are_exclusive_in_both_directions(self):
        self.feature._set_option("use_kamp", 1)
        self.feature._set_option("print_leveling", 1)
        self.assertTrue(self.manager.variables["print_leveling"])
        self.assertFalse(self.manager.variables["use_kamp"])
        self.feature._set_option("use_kamp", 1)
        self.assertTrue(self.manager.variables["use_kamp"])
        self.assertFalse(self.manager.variables["print_leveling"])

    def test_pending_calibration_blocks_restart_before_any_effect(self):
        self.open_options()
        self.feature._set_option("tune_config", 1)
        self.config["save_config_pending"] = True
        with self.assertRaisesRegex(RuntimeError, "Save or discard"):
            self.feature.apply_settings()
        self.assertFalse(self.events)
        self.assertEqual(self.manager.variables["tune_config"], 0)

    def test_restart_timeout_restores_normal_updates(self):
        self.open_options()
        self.feature._set_option("tune_config", 1)
        self.feature.apply_settings()
        self.host.reactor.flush_callbacks()
        self.assertFalse(self.host.restart_pending)
        self.assertFalse(self.host.startup_restarting)
        self.assertIsNotNone(self.host.timer)
        self.assertEqual(self.host.page, ScreenPage.SETUP_OPTIONS)
        self.assertIn("did not restart", self.feature.progress.data["error"])
        dialog = self.host._find_dialog(FEATHER.ScreenDialog.MESSAGE)
        self.assertIn("did not restart", dialog.content["message"])

    def test_setup_network_reuses_scan_and_ethernet_and_keeps_navigation_in_the_wizard(self):
        self.host.network_client = mock.Mock(connected=True)
        self.feature.startup()
        self.tap(SetupCommand.ACTION)
        self.assertIn(Command(NetworkCommand.CONTINUE).wire_id, shown_actions(self.host._screen_root))
        self.tap(NetworkCommand.SCAN)
        self.host.network_client.scan.assert_called_once_with()
        self.host._on_network_event("ok", None, self.host.reactor.now)
        self.assertEqual(self.host.page, ScreenPage.WIFI_SCAN)
        self.dispatch("nav.back")
        self.assertEqual(self.host.page, ScreenPage.SETUP_NETWORK)
        self.tap(NetworkCommand.ETHERNET)
        self.host.network_client.use_ethernet.assert_called_once_with()
        self.host.network_status.update(mode="ETHERNET", state="CONNECTED", ip="192.0.2.14")
        self.host._on_network_event("ok", None, self.host.reactor.now)
        self.assertEqual(self.host.page, ScreenPage.SETUP_NETWORK)
        self.assertNotIn(Command(NetworkCommand.ETHERNET).wire_id, shown_actions(self.host._screen_root))
        self.assertIn(Command(NetworkCommand.CONTINUE).wire_id, shown_actions(self.host._screen_root))
        self.dispatch("nav.back")
        self.assertEqual(self.host.page, ScreenPage.SETUP_WELCOME)

    def test_setup_theme_previews_without_saving_on_back_and_applies_on_continue(self):
        self.feature.startup()
        self.tap(SetupCommand.ACTION)
        self.tap(NetworkCommand.CONTINUE)
        self.select_option("AMBER")
        self.assertEqual(self.host.renderer.theme_name, "AMBER")
        self.assertEqual(self.manager.variables["feather_theme"], "DEFAULT")
        self.dispatch("nav.back")
        self.assertEqual(self.host.page, ScreenPage.SETUP_NETWORK)
        self.assertEqual(self.host.renderer.theme_name, "DEFAULT")
        self.assertFalse(self.feature.progress.data["selections"])
        self.tap(NetworkCommand.CONTINUE)
        self.select_option("AMBER")
        self.dispatch("mod.apply")
        self.host.reactor.flush_callbacks()
        self.assertEqual(self.host.page, ScreenPage.SETUP)
        self.assertEqual(self.manager.variables["feather_theme"], "AMBER")
        self.assertEqual(self.host.renderer.theme_name, "AMBER")

    def test_additional_groups_are_reachable_and_next_follows_the_list_order(self):
        self.feature.open()
        entry = Command(SetupCommand.OPEN, "additional").wire_id
        hub_row = next(hitbox for hitbox in self.capture.latest.hitboxes if hitbox.action == entry)
        self.tap(SetupCommand.OPEN, "additional")
        hits = {hitbox.action: hitbox for hitbox in self.capture.latest.hitboxes}
        group_actions = [Command(SetupCommand.OPEN, "group:" + group).wire_id
                         for group in ("protection", "mesh", "preparation", "peripherals")]
        self.assertTrue(all(action in hits for action in group_actions))
        rows = [hits[action] for action in group_actions]
        self.assertTrue(all((row.x, row.width) == (hub_row.x, hub_row.width) for row in rows))
        self.assertTrue(all(first.y + first.height <= second.y for first, second in zip(rows, rows[1:])))
        self.tap(SetupCommand.OPEN, "group:protection")
        action = Command(SetupCommand.ACTION).wire_id
        for setting in ("power_loss_recovery", "print_leveling", "disable_priming"):
            self.assertIn(Command(SetupCommand.HELP, setting).wire_id, shown_actions(self.host._screen_root))
            self.assertEqual(self.capture.latest.button(action).label, "NEXT")
            self.tap(SetupCommand.ACTION)
        for setting in ("camera", "chamber_light", "chamber_light_mode", "screen_stream"):
            self.assertIn(Command(SetupCommand.HELP, setting).wire_id, shown_actions(self.host._screen_root))
        self.assertEqual(self.capture.latest.button(action).label, "NEXT")
        self.tap(SetupCommand.ACTION)
        self.assertTrue(self.capture.latest.has_text("Enable Camera in Peripherals to configure timelapse."))
        self.tap(SetupCommand.ACTION)
        self.assertIn(Command(SetupCommand.EDIT, "backlight").wire_id, shown_actions(self.host._screen_root))
        self.tap(SetupCommand.ACTION)
        self.assertIn(Command(SetupCommand.EDIT, "park_dz").wire_id, shown_actions(self.host._screen_root))
        self.tap(SetupCommand.ACTION)
        self.assertIn(Command(SetupCommand.EDIT, "feather_theme").wire_id, shown_actions(self.host._screen_root))
        self.assertEqual(self.capture.latest.button(action).label, "DONE")
        self.tap(SetupCommand.ACTION)
        self.assertEqual(self.capture.latest.button(action).label, "REVIEW CALIBRATION")
        for group in ("timelapse", "display", "finishing", "feather"):
            self.assertIn(Command(SetupCommand.OPEN, "group:" + group).wire_id, shown_actions(self.host._screen_root))
        self.tap(SetupCommand.PREVIOUS)
        self.assertTrue(all(wire_id in shown_actions(self.host._screen_root) for wire_id in group_actions))

    def test_peripherals_edits_chamber_light(self):
        self.open_options("peripherals")
        self.tap(SetupCommand.EDIT, "chamber_light_mode")
        self.select_option("PRINT_ONLY")
        self.dispatch("mod.apply")
        self.host.reactor.flush_callbacks()
        self.assertEqual(self.host.page, ScreenPage.SETUP_OPTIONS)
        self.assertEqual(self.manager.variables["chamber_light_mode"], "PRINT_ONLY")
        self.tap(SetupCommand.EDIT, "chamber_light")
        self.assertEqual(self.host.page, ScreenPage.MOD_VALUE)

    def test_settings_opens_the_wizard_and_time_zone_returns_to_its_hub(self):
        self.host._show_page(ScreenPage.SETTINGS)
        self.assertNotIn("settings.timezone", shown_actions(self.host._screen_root))
        self.assertFalse(self.capture.latest.has_text("TIME ZONE"))
        self.assertEqual(self.capture.latest.button("settings.setup").label, "SETUP WIZARD")
        self.dispatch("settings.setup")
        self.assertEqual(self.host.page, ScreenPage.SETUP)
        self.tap(SetupCommand.OPEN, "timezone")
        self.tap(SetupCommand.ZONE, "fixed")
        self.tap(SetupCommand.ZONE, "UTC+3")
        self.assertEqual(self.events[-1][0], "SET_TIMEZONE ZONE=UTC+3")
        self.assertEqual(self.host.page, ScreenPage.SETUP)
        self.tap(SetupCommand.OPEN, "timezone")
        self.tap(SetupCommand.ZONE, "Europe")
        while self.feature.zone_level[0] == "letters":
            value = self.feature._zone_cells()[0][0][0]
            self.tap(SetupCommand.ZONE, value)
        self.host.reactor.now += 1
        self.host._dispatch_action("nav.back")
        self.assertIn(self.feature.zone_level[0], ("letters", "regions"))
        while self.host.page == ScreenPage.SETUP_TIMEZONE:
            self.dispatch("nav.back")
        self.assertEqual(self.host.page, ScreenPage.SETUP)
        self.tap(SetupCommand.LEAVE)
        self.dispatch(FEATHER.dialog_ui.SETUP_SKIP.wire_id)
        self.assertEqual(self.host.page, ScreenPage.SETTINGS)
        self.assertEqual(self.fresh_feature().progress.data["time_zone"], "UTC+3")

    def test_fixed_offset_is_separate_from_every_page_of_regions(self):
        regions = ["Region%d" % index for index in range(13)]
        with mock.patch("system_time.timezone_regions", return_value=regions):
            self.feature.open()
            self.tap(SetupCommand.OPEN, "timezone")
            action = Command(SetupCommand.ZONE, "fixed").wire_id
            for region in (regions[0], regions[-1]):
                frame = self.capture.latest
                tile = frame.button(Command(SetupCommand.ZONE, region).wire_id)
                self.assertGreaterEqual(frame.button(action).bounds.y, tile.bounds.bottom)
                if region == regions[0]:
                    self.tap(SetupCommand.NEXT)
            self.tap(SetupCommand.ZONE, "fixed")
            self.assertNotIn(action, shown_actions(self.host._screen_root))
            self.tap(SetupCommand.ZONE, "UTC+3")
            self.assertEqual(self.host.page, ScreenPage.SETUP)
            self.assertEqual(self.events[-1][0], "SET_TIMEZONE ZONE=UTC+3")

    def test_picker_names_the_current_zone_and_shows_a_fixed_offset_once(self):
        self.feature.open()
        for zone, offset, abbreviation, expected in (
                ("Asia/Yekaterinburg", "UTC+05:00", "+05", "Asia/Yekaterinburg"),
                ("UTC+05:30", "UTC+05:30", "+0530", "UTC+05:30"),
                ("UTC+02:00", "UTC+02:00", "CEST", "CEST")):
            formats = {"%z": offset[3:].replace(":", ""), "%Z": abbreviation}
            with self.subTest(zone=zone), mock.patch("system_time.current_zone", return_value=zone), \
                    mock.patch("time.strftime", side_effect=formats.get):
                self.tap(SetupCommand.OPEN, "timezone")
                labels = [text.value for text in self.capture.latest.texts]
                self.assertIn(expected, labels)
                self.assertEqual(sum(offset in label for label in labels), 1)
            self.dispatch("nav.back")

    def test_fixed_offsets_fit_one_ordered_grid_with_utc_in_the_middle(self):
        from system_time import canonical_timezone
        from ui.font_metrics import get_font_metrics

        self.feature.open()
        self.tap(SetupCommand.OPEN, "timezone")
        self.tap(SetupCommand.ZONE, "fixed")
        frame = self.capture.latest
        actions = shown_actions(self.host._screen_root)
        self.assertNotIn(Command(SetupCommand.PREVIOUS).wire_id, actions)
        self.assertNotIn(Command(SetupCommand.NEXT).wire_id, actions)
        regions = {hit.action: hit for hit in frame.hitboxes}
        utc_action = Command(SetupCommand.ZONE, "UTC").wire_id
        utc = regions[utc_action]
        self.assertAlmostEqual(utc.x + utc.width / 2, 400, delta=1)
        self.assertGreaterEqual(utc.height, 44)
        buttons = [button for button in frame.buttons.values()
                   if button.label.strip()[:1] in ("+", "-") and ":" in button.label]
        self.assertEqual(len(buttons), 39)
        ordered = []
        for button in sorted(buttons, key=lambda item: (item.bounds.y, item.bounds.x)):
            value = button.label.strip()
            self.assertRegex(value, r"^[+-][0-9]{2}:[0-9]{2}$")
            hours, minutes = map(int, value[1:].split(":"))
            ordered.append((-1 if value.startswith("-") else 1) * (60 * hours + minutes))
            action = Command(SetupCommand.ZONE, canonical_timezone("UTC" + value)).wire_id
            self.assertEqual(button.action, action)
            available = button.bounds.width - 2 * self.host.renderer.BUTTON_TEXT_PADDING
            self.assertLessEqual(get_font_metrics().text_width(value, button.font), available)
            self.assertGreaterEqual(regions[action].height, 44)
        self.assertEqual(ordered, sorted(set(ordered)))
        self.assertEqual(frame.button(Command(SetupCommand.ZONE, "UTC+13:45").wire_id).label, "+13:45")
        self.tap(SetupCommand.ZONE, "UTC+13:45")
        self.assertEqual(self.events[-1][0], "SET_TIMEZONE ZONE=UTC+13:45")

    def test_timezone_button_feedback_draws_labels_on_press_and_cancel(self):
        self.host.renderer = FeatherRenderer()
        typer = TyperLoop(self.host.renderer)
        self.addCleanup(typer.close)
        typer.start()

        def tap_when_shown(key, value):
            wire = Command(key, value).wire_id
            self.assertTrue(wait_for(lambda: wire in shown_actions(self.host._screen_root)))
            self.tap(key, value)

        with mock.patch("system_time.timezone_cities", return_value=[("Europe/Tallinn", "Tallinn")]), \
                mock.patch("system_time.zone_offsets", return_value={"Europe/Tallinn": "UTC+02:00"}):
            for region, value, labels in (
                    ("fixed", "UTC+13:45", ("+13:45",)),
                    ("Europe", "Europe/Tallinn", ("TALLINN", "UTC+02:00"))):
                with self.subTest(region=region):
                    self.feature.open()
                    tap_when_shown(SetupCommand.OPEN, "timezone")
                    tap_when_shown(SetupCommand.ZONE, region)
                    self.assertTrue(typer.drew(labels[0]))
                    wire = typer.wire(Command(SetupCommand.ZONE, value).wire_id)

                    for phase in ("down", "up"):
                        with self.subTest(phase=phase):
                            before = len(typer.history())
                            typer.tell("button %s %s" % (wire, phase))
                            self.assertTrue(wait_for(lambda: len(typer.history()) > before))
                            commands = [shlex.split(str(command)) for command in typer.history()[-1]]
                            drawn = {tokens[tokens.index("-t") + 1] for tokens in commands if "-t" in tokens}
                            self.assertTrue(set(labels) <= drawn, drawn)
                    self.assertEqual(self.host.page, ScreenPage.SETUP_TIMEZONE)
        self.assertEqual(self.events, [])

    def test_safe_z_saves_and_marks_done_even_if_the_value_matches(self):
        self.manager.set_value("safe_z", 6.0)
        self.host.probe.get_status = lambda _: {"last_z_result": 1.0}
        self.feature.open()
        self.tap(SetupCommand.OPEN, "calibration")
        self.assertTrue(self.capture.latest.has_text("SAFE Z"))
        self.assertTrue(self.capture.latest.has_text("Saved Safe Z: 6.000 mm"))
        self.tap(SetupCommand.OPEN, "z_offset")
        self.dispatch(SAFE_CALIBRATE.wire_id)
        self.dispatch(SAFE_PROBE.wire_id)
        self.dispatch(SAFE_SAVE.wire_id)
        self.assertIn("z_offset", self.feature.progress.data["completed"])
        self.assertEqual(self.host.page, ScreenPage.SETUP_CALIBRATION)
        self.assertTrue(self.capture.latest.has_text("DONE"))
        commands = [command for command, _ in self.events]
        self.assertIn("SET_MOD PARAM=safe_z VALUE=6.000", commands)
        self.assertNotIn("M84", commands)
        self.dispatch("nav.back")
        self.tap(SetupCommand.LEAVE)
        self.dispatch(FEATHER.dialog_ui.SETUP_SKIP.wire_id)
        self.assertEqual(self.events[-1][0], "M84")

    def test_skipping_or_backing_out_of_safe_z_returns_directly_without_completion(self):
        for action in (SAFE_SKIP.wire_id, "nav.back"):
            with self.subTest(action=action):
                self.feature.open()
                self.tap(SetupCommand.OPEN, "calibration")
                self.tap(SetupCommand.OPEN, "z_offset")
                before = len(self.capture.batches)
                self.dispatch(action)
                self.assertEqual(self.host.page, ScreenPage.SETUP_CALIBRATION)
                self.host.reactor.flush_callbacks()
                self.assertFalse(any(frame.has_action("cal.z") for frame in self.capture.frames[before:]))
                self.assertNotIn("z_offset", self.feature.progress.data["completed"])
                self.dispatch("nav.back")
                self.tap(SetupCommand.LEAVE)
                if not self.feature.progress.data["skipped"]:
                    self.dispatch(FEATHER.dialog_ui.SETUP_SKIP.wire_id)
                self.assertNotIn("M84", [command for command, _ in self.events])

    def test_mesh_dont_save_keeps_active_result_and_pending_configuration(self):
        self.host.bed_mesh = StatusObject({"profiles": {"auto": {"points": [[0.0, 0.0], [0.0, 0.0]]}}})
        self.config["config"] = {"bed_mesh auto": {"points": "0,0\n0,0"}}
        self.feature.open()
        self.tap(SetupCommand.OPEN, "calibration")
        self.tap(SetupCommand.OPEN, "mesh")
        calibration = self.host.feature_manager.get("calibration")
        matrix = [[0.1, 0.2], [0.3, 0.4]]
        measured = {"profile_name": "auto", "probed_matrix": matrix,
                    "profiles": {"auto": {"points": matrix},
                                 "auto_prev": {"points": [[0.0, 0.0], [0.0, 0.0]]}}}
        self.host.bed_mesh.status = copy.deepcopy(measured)
        pending = {"bed_mesh auto": {"points": "0.1,0.2\n0.3,0.4"},
                   "bed_mesh auto_prev": {"points": "0,0\n0,0"},
                   "input_shaper": {"shaper_freq_y": "42.5"}}
        self.config.update(save_config_pending=True, save_config_pending_items=copy.deepcopy(pending))
        calibration.calibration_mesh = matrix
        self.host._show_page(ScreenPage.CALIBRATION_RESULT)
        self.assertEqual(self.capture.latest.button("cal.mesh.discard").label, "DON'T SAVE")
        self.host.reactor.now += 1
        self.host._dispatch_action("cal.mesh.discard")
        self.assertEqual(self.host.page, ScreenPage.SETUP_CALIBRATION)
        self.assertIn(Command(SetupCommand.ACTION).wire_id, shown_actions(self.host._screen_root))
        self.assertEqual(self.host.bed_mesh.get_status(0), measured)
        self.assertTrue(self.config["save_config_pending"])
        self.assertEqual(self.config["save_config_pending_items"], pending)
        self.assertNotIn("mesh", self.feature.progress.data["completed"])
        self.assertFalse(self.events)

    def test_mesh_cancel_before_measurement_leaves_klipper_state_untouched(self):
        original = {"profile_name": "auto", "profiles": {"auto": {"points": "current"},
                                                            "auto_prev": {"points": "older"}}}
        self.host.bed_mesh = StatusObject(copy.deepcopy(original))
        self.feature.open()
        self.feature._start_calibration("mesh")
        self.host.reactor.now += 1
        self.host._dispatch_action("nav.back")
        self.host.reactor.flush_callbacks()
        self.assertFalse(self.events)
        self.assertEqual(self.host.bed_mesh.get_status(0), original)
        self.assertEqual(self.host.page, ScreenPage.SETUP_CALIBRATION)

    def test_finishing_setup_turns_off_motors_after_mesh_motion(self):
        matrix = [[0.1, 0.2], [0.3, 0.4]]
        self.host.bed_mesh = StatusObject({"profiles": {"auto": {"points": matrix}}})
        self.feature.open()
        self.tap(SetupCommand.OPEN, "calibration")
        self.tap(SetupCommand.OPEN, "mesh")
        self.dispatch("cal.confirm")
        self.host.reactor.flush_callbacks()
        self.dispatch("cal.mesh.discard")
        self.tap(SetupCommand.ACTION)
        self.dispatch(FEATHER.dialog_ui.SETUP_FINISH.wire_id)
        self.assertEqual(self.host.page, ScreenPage.IDLE_HOME)
        commands = [command for command, _ in self.events]
        self.assertTrue(any(command.startswith("AUTO_FULL_BED_LEVEL ") for command in commands[:-1]))
        self.assertEqual(commands[-1], "M84")
        self.assertEqual(commands.count("M84"), 1)

    def test_shaper_opens_with_standard_vibrations_and_save_is_verified_after_restart(self):
        from tests.test_feather_shaper import opened_result
        from ff5m_ui.shaper.actions import ShaperAction
        reference = json.loads((ROOT / "tests/fixtures/shaper_reference.json").read_text())["cases"]
        native = opened_result({"x": reference[0], "y": reference[1]})
        lookup = self.host.printer.lookup_object
        self.host.printer.lookup_object = lambda name, default=None: native if name == "forge_shaper" else lookup(name, default)
        self.feature.open()
        self.feature._start_calibration("shaper")
        calibration = self.host.feature_manager.get("calibration")
        self.assertEqual(self.host.page, ScreenPage.CALIBRATION_CONFIRM)
        standard = ShaperAction("vibrations", "standard").wire_id
        self.assertEqual(self.capture.latest.button(standard).state, "selected")
        self.dispatch(ShaperAction("vibrations", "sweeping").wire_id)
        self.dispatch("nav.back")
        self.assertEqual(self.host.page, ScreenPage.SETUP_CALIBRATION)
        self.tap(SetupCommand.OPEN, "shaper")
        self.assertEqual(self.capture.latest.button(standard).state, "selected")
        self.assertIn("cal.shaper.start", shown_actions(self.host._screen_root))
        self.config.update(save_config_pending=True, save_config_pending_items={"input_shaper": {
            "shaper_type_x": "mzv", "shaper_freq_x": "40.0", "shaper_type_y": "ei", "shaper_freq_y": "45.0"}})
        calibration._restart_klipper("SAVE_CONFIG")
        self.assertEqual(self.feature.progress.data["pending"], "saving")
        self.config.update(config={"input_shaper": {"shaper_type_x": "mzv", "shaper_freq_x": "40.0",
                                                   "shaper_type_y": "ei", "shaper_freq_y": "45.0"}},
                           save_config_pending=False, save_config_pending_items={})
        self.host.restart_pending = False
        self.fresh_feature().startup()
        self.assertIn("shaper", self.feature.progress.data["completed"])
        self.assertEqual(self.host.page, ScreenPage.SETUP_CALIBRATION)
        self.assertTrue(self.capture.latest.has_text("DONE"))

    def test_saved_mesh_round_trips_through_klipper_and_reopens_calibration(self):
        from tests.test_webhooks_patch import CONFIGFILE

        path = self.folder / "printer.cfg"
        path.touch()
        request_restart = mock.Mock()
        gcode = SimpleNamespace(register_command=mock.Mock(), request_restart=request_restart)
        printer = SimpleNamespace(lookup_object=lambda name: gcode, lookup_objects=lambda: (),
                                  get_start_args=lambda: {"config_file": str(path)})
        parser = CONFIGFILE.configparser.RawConfigParser
        compat = mock.patch.object(parser, "readfp", parser.read_file, create=True)
        with compat:
            configfile = CONFIGFILE.PrinterConfig(printer)
            configfile.check_unused_options(configfile.read_main_config())
            self.host.bed_mesh = StatusObject({"profiles": {}})
            self.feature.open()
            self.tap(SetupCommand.OPEN, "calibration")
            self.tap(SetupCommand.OPEN, "mesh")
            configfile.set("bed_mesh auto", "points", "\n  1, 2\n  3, 4\n")
            self.config.update(configfile.get_status(0))
            calibration = self.host.feature_manager.get("calibration")
            calibration.calibration_mesh = [[1., 2.], [3., 4.]]
            self.host._show_page(ScreenPage.CALIBRATION_RESULT)
            self.dispatch("cal.mesh.save")
            self.assertNotIn("mesh", self.feature.progress.data["completed"])
            self.assertEqual(self.events[-1][0], "SAVE_CONFIG")
            self.assertEqual(self.events[-1][1]["pending"], "saving")
            configfile.cmd_SAVE_CONFIG(None)
            request_restart.assert_called_once_with("restart")
            restarted = CONFIGFILE.PrinterConfig(printer)
            restarted.check_unused_options(restarted.read_main_config())
        self.config.update(restarted.get_status(0))
        self.host.bed_mesh.status = {"profiles": {"auto": {}}}
        self.host.restart_pending = False
        self.fresh_feature().startup()
        self.assertEqual(self.host.page, ScreenPage.SETUP_CALIBRATION)
        self.assertIn("mesh", self.feature.progress.data["completed"])
        self.assertTrue(self.capture.latest.has_text("DONE"))
        self.assertIsNone(self.host._current_dialog())

    def test_unsaved_first_mesh_does_not_enable_finishing_setup(self):
        self.host.bed_mesh = StatusObject({"profiles": {}})
        self.feature.open()
        self.tap(SetupCommand.OPEN, "calibration")
        self.tap(SetupCommand.OPEN, "mesh")
        calibration = self.host.feature_manager.get("calibration")
        calibration.calibration_mesh = [[1., 2.], [3., 4.]]
        self.host.bed_mesh.status = {"profiles": {"auto": {"points": calibration.calibration_mesh}}}
        self.config.update(save_config_pending=True,
                           save_config_pending_items={"bed_mesh auto": {"points": "1,2\n3,4"}})
        self.host._show_page(ScreenPage.CALIBRATION_RESULT)
        self.dispatch("cal.mesh.discard")
        self.assertEqual(self.host.page, ScreenPage.SETUP_CALIBRATION)
        self.assertNotIn(Command(SetupCommand.ACTION).wire_id, shown_actions(self.host._screen_root))

    def test_failed_save_readback_and_interrupted_motion_never_start_motion(self):
        self.feature.open()
        self.feature.progress.write(pending="running", running_step="shaper")
        self.events.clear()
        self.fresh_feature().startup()
        self.assertIsNone(self.feature.running_step())
        self.assertFalse(self.events)
        self.feature.progress.write(pending="saving", running_step="mesh", expected="0" * 64)
        self.fresh_feature().startup()
        self.assertNotIn("mesh", self.feature.progress.data["completed"])
        self.assertIn("not saved", self.feature.progress.data["error"])
        self.assertEqual(self.host.page, ScreenPage.SETUP_CALIBRATION)
        self.assertIn("not saved", self.host.toast_message)
        self.assertIsNone(self.host._current_dialog())
        self.assertFalse(self.events)

    def test_missing_mesh_is_required_in_both_places_and_cannot_finish(self):
        self.feature.open()
        action = Command(SetupCommand.ACTION).wire_id
        for page in (ScreenPage.SETUP, ScreenPage.SETUP_CALIBRATION):
            self.assertEqual(self.host.page, page)
            self.assertEqual(self.capture.latest.text("REQUIRED").color, self.host.renderer.color(ThemeColor.DANGER))
            self.assertNotIn(action, shown_actions(self.host._screen_root))
            self.host.reactor.now += 1
            self.host._dispatch_action(action)
            self.assertEqual(self.host.page, page)
            self.assertFalse(self.feature.progress.complete)
            self.assertIsNone(self.host._current_dialog())
            if page == ScreenPage.SETUP:
                self.tap(SetupCommand.OPEN, "calibration")

    def test_finish_warns_about_missing_calibration_and_persists_completion(self):
        self.host.bed_mesh = StatusObject({"profiles": {"auto": {}}})
        self.open_options("finishing")
        self.tap(SetupCommand.TOGGLE, "stop_motor")
        self.dispatch("nav.back")
        self.dispatch("nav.back")
        self.tap(SetupCommand.ACTION)
        self.assertFalse(self.feature.progress.complete)
        self.assertIsNotNone(self.host._find_dialog(FEATHER.ScreenDialog.MESSAGE))
        self.feature.confirm_dialog(FEATHER.DialogRoute.SETUP_FINISH)
        self.assertTrue(self.feature.progress.complete)
        self.assertEqual(self.host.page, ScreenPage.IDLE_HOME)
        self.fresh_feature().startup()
        self.assertEqual(self.host.page, ScreenPage.IDLE_HOME)
        self.assertEqual(self.feature.progress.data["selections"], {"stop_motor": 0})
        self.open_options("finishing")
        self.tap(SetupCommand.TOGGLE, "stop_motor")
        self.assertTrue(self.manager.variables["stop_motor"])
        self.assertEqual(self.fresh_feature().progress.data["selections"], {"stop_motor": 0})
        self.assertFalse(self.events)

    def test_malformed_checkpoints_never_apply_values_and_preserve_completion(self):
        before = dict(self.manager.variables)
        for field, value in (("draft", {"disable_web": 1}), ("completed", [{}]),
                             ("running_step", "safe_z"), ("draft", {"tune_config": "invalid"}),
                             ("expected", "bad"), ("pending", "unknown")):
            with self.subTest(field=field):
                data = dict(self.feature.progress.data, completed_version=1)
                data[field] = value
                pathlib.Path(self.feature.progress.filename).write_text(json.dumps(data))
                self.host.page = ScreenPage.IDLE_HOME
                self.fresh_feature().startup()
                self.assertTrue(self.feature.progress.recovery_required)
                self.assertTrue(self.feature.progress.complete)
                self.assertEqual(self.host.page, ScreenPage.IDLE_HOME)
                self.assertEqual(self.manager.variables, before)
                self.assertFalse(self.events)

    def test_checkpoint_failure_cannot_persist_settings_or_restart(self):
        self.open_options()
        self.feature._set_option("tune_config", 1)
        with mock.patch("feather.setup.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.feature.apply_settings()
        self.assertFalse(self.events)
        self.assertEqual(self.manager.variables["tune_config"], 0)

    def test_production_update_opens_setup_at_a_suitable_first_idle_cycle(self):
        self.feature.progress.write(intro="done", completed=["mesh"], draft={"tune_config": 1})
        self.host.bed_mesh = StatusObject({"profiles": {"auto": {}}})
        self.fresh_feature()
        c = self.host
        c.print_stats.status["state"] = "standby"
        c._setup_startup_pending = True
        c._service_network = mock.Mock()
        c._update_eco_backlight = mock.Mock()
        c._refresh_status_bar = mock.Mock()
        c._update_operation_context = mock.Mock()
        c._update_dashboard = mock.Mock()
        c.filament_sensor = None
        c.busy_message = None
        c._update_cycle(10)
        self.assertEqual(c.page, ScreenPage.SETUP_WELCOME)
        self.assertFalse(c._setup_startup_pending)
        self.tap(SetupCommand.ACTION)
        self.assertEqual(c.page, ScreenPage.SETUP_NETWORK)
        self.tap(NetworkCommand.CONTINUE)
        self.assertEqual(c.page, ScreenPage.PARAMETER_OPTIONS)
        self.dispatch("mod.apply")
        c.reactor.flush_callbacks()
        self.assertEqual(c.page, ScreenPage.SETUP)
        self.assertEqual(self.capture.latest.button(Command(SetupCommand.ACTION).wire_id).label, "APPLY & RESTART")
        self.tap(SetupCommand.OPEN, "calibration")
        self.assertTrue(self.capture.latest.has_text("DONE"))
        self.assertTrue(self.capture.latest.has_text("Saved profile: auto"))
        self.assertFalse(self.events)
        self.feature.leave()
        c._update_cycle(11)
        self.assertEqual(c.page, ScreenPage.IDLE_HOME)

    def test_startup_opportunity_is_consumed_even_if_the_user_already_left_home(self):
        c = self.host
        c.print_stats.status["state"] = "standby"
        c._setup_startup_pending = True
        for name in ("_service_network", "_update_eco_backlight", "_refresh_status_bar", "_update_operation_context", "_update_dashboard"):
            setattr(c, name, mock.Mock())
        c.filament_sensor = None
        c.busy_message = None
        c.page = ScreenPage.SETTINGS
        c._update_cycle(10)
        self.assertFalse(c._setup_startup_pending)
        c.page = ScreenPage.IDLE_HOME
        c._update_cycle(11)
        self.assertEqual(c.page, ScreenPage.IDLE_HOME)
        self.assertFalse(self.feature.active)



class ProgressTest(unittest.TestCase):
    def test_atomic_failure_preserves_checkpoint_and_in_memory_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "setup.json")
            progress = SetupProgress(path)
            progress.write(draft={"tune_config": 1})
            with mock.patch("feather.setup.os.replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    progress.write(draft={"tune_config": 0})
            self.assertEqual(progress.data["draft"], {"tune_config": 1})
            self.assertEqual(SetupProgress(path).data["draft"], {"tune_config": 1})
            self.assertEqual(os.listdir(folder), ["setup.json"])

    def test_earlier_checkpoint_keeps_completion_and_pending_choices(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "setup.json"
            path.write_text(json.dumps({"completed_version": 1, "draft": {"tune_config": 1},
                                        "completed": ["mesh"], "applied": {"tune_config": 0}}))
            progress = SetupProgress(str(path))
            self.assertTrue(progress.complete)
            self.assertFalse(progress.recovery_required)
            self.assertEqual(progress.data["draft"], {"tune_config": 1})
            self.assertEqual(progress.data["completed"], ["mesh"])

    def test_checkpoint_does_not_accept_credentials_and_corruption_is_recoverable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "setup.json"
            progress = SetupProgress(str(path))
            with self.assertRaises(ValueError):
                progress.write(draft={"password": "test-only"})
            with self.assertRaises(ValueError):
                progress.record_options({"password": "test-only"})
            path.write_text("{broken")
            recovered = SetupProgress(str(path))
            self.assertFalse(recovered.complete)
            self.assertTrue(recovered.data["error"])


class SetupConfigShellTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = pathlib.Path(folder.name)
        self.log = self.folder / "effects"
        self.backup = self.folder / "resurrection.json"
        self.backup.write_text("test recovery state")
        (self.folder / "commands").mkdir()
        common = self.folder / "common.sh"
        common.write_text('SCRIPTS="$HARNESS"\nCMDS="$HARNESS/commands"\nMOD="$HARNESS/root"\nPY="$HARNESS/py"\nmessage() { :; }\n')
        (self.folder / "restart_klipper.sh").write_text('#!/bin/sh\necho "restart $*" >> "$EFFECTS"\n')
        (self.folder / "chroot").write_text('#!/bin/sh\necho "apply $*" >> "$EFFECTS"\nexit "${EFFECT_STATUS:-0}"\n')
        (self.folder / "commands/zdisplay.sh").write_text('#!/bin/sh\necho FEATHER\n')
        for path in (self.folder / "restart_klipper.sh", self.folder / "chroot", self.folder / "commands/zdisplay.sh"):
            path.chmod(0o755)
        script = (ROOT / ".shell/commands/zchanges.sh").read_text()
        script = script.replace("/opt/config/mod/.shell/common.sh", str(common)).replace(
            "/opt/config/mod_data/resurrection.json", str(self.backup))
        self.script = self.folder / "zchanges.sh"
        self.script.write_text(script)

    def run_hook(self, key, value, apply_only=True, failure=False):
        environment = dict(os.environ, HARNESS=str(self.folder), EFFECTS=str(self.log),
                           EFFECT_STATUS="1" if failure else "0", PATH=str(self.folder) + os.pathsep + os.environ["PATH"])
        result = subprocess.run(["bash", str(self.script), key, str(value)] + (["--apply-only"] if apply_only else []),
                                env=environment, capture_output=True, text=True)
        effects = self.log.read_text().splitlines() if self.log.exists() else []
        return result.returncode, effects

    def test_timing_selection_does_not_restart_in_apply_only_mode(self):
        self.assertEqual(self.run_hook("tune_klipper", 0), (0, []))
        code, effects = self.run_hook("tune_klipper", 1, apply_only=False)
        self.assertEqual((code, effects), (0, ["restart --hard"]))

    def test_config_application_runs_real_hook_without_intermediate_restart(self):
        for value, configuration in ((0, "tuning.off.cfg"), (1, "tuning.cfg")):
            with self.subTest(value=value):
                if self.log.exists():
                    self.log.unlink()
                code, effects = self.run_hook("tune_config", value)
                self.assertEqual(code, 0)
                self.assertEqual(len(effects), 1)
                arguments = shlex.split(effects[0])
                self.assertIn("--avoid_writes", arguments)
                self.assertIn("--no_data", arguments)
                self.assertTrue(arguments[-1].endswith("/" + configuration))

    def test_failed_configuration_effect_never_restarts(self):
        code, effects = self.run_hook("tune_config", 1, apply_only=False, failure=True)
        self.assertEqual(code, 1)
        self.assertTrue(effects)
        self.assertFalse(any(effect.startswith("restart") for effect in effects))

    def test_recovery_cleanup_happens_without_restart(self):
        self.assertEqual(self.run_hook("power_loss_recovery", 0), (0, []))
        self.assertFalse(self.backup.exists())
