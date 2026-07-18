## Controller and recovery tests for the Feather screen.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import enum
import errno
import json
import tempfile
import unittest
from unittest import mock

from tests.test_feather_screen import (
    DeferredReactor,
    FEATHER,
    FailingGCode,
    GCodeRecorder,
    MOD_PARAMS,
    MOD_UI,
    ModManager,
    RESURRECTION,
    Reactor,
    StatusObject,
    UI,
    mod_controller,
    mod_param,
)


class ControllerSafetyTest(unittest.TestCase):
    def test_move_caution_loads_existing_auto_bed_profile(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.reactor = Reactor()
        controller.gcode = GCodeRecorder()
        controller.bed_mesh = StatusObject({
            "profile_name": "", "profiles": {"auto": {"points": []}}})
        controller.move_caution_acknowledged = False
        controller._require_idle = lambda: None
        controller._stop_joystick = lambda: None
        controller._render_move = lambda: None
        notices = []
        controller._toast = notices.append

        controller._handle_move_action("move.caution.auto")

        self.assertEqual(
            controller.gcode.commands, ["BED_MESH_PROFILE LOAD=auto"])
        self.assertTrue(controller.move_caution_acknowledged)
        self.assertEqual(notices, ["BED PROFILE AUTO LOADED"])

    def test_move_caution_can_unload_active_bed_profile(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.reactor = Reactor()
        controller.gcode = GCodeRecorder()
        controller.move_caution_acknowledged = False
        controller._require_idle = lambda: None
        controller._stop_joystick = lambda: None
        rendered = []
        controller._render_move = lambda: rendered.append(True)
        notices = []
        controller._toast = notices.append

        controller._handle_move_action("move.caution.unload")

        self.assertEqual(controller.gcode.commands, ["BED_MESH_CLEAR"])
        self.assertTrue(controller.move_caution_acknowledged)
        self.assertEqual(rendered, [True])
        self.assertEqual(notices, ["BED PROFILE UNLOADED"])

    def test_move_caution_dismissal_resets_after_z_becomes_safe(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.bed_mesh = StatusObject({
            "profile_name": "", "profiles": {"auto": {"points": []}}})
        controller.move_caution_acknowledged = False
        low = (0.0, 0.0, 4.99, "HOMED: XYZ", True, True)
        safe = (0.0, 0.0, 5.0, "HOMED: XYZ", True, True)

        self.assertEqual(controller._move_caution_state(low, 0),
                         (True, "available"))
        controller.move_caution_acknowledged = True
        self.assertEqual(controller._move_caution_state(low, 0),
                         (False, None))
        self.assertEqual(controller._move_caution_state(safe, 0),
                         (False, None))
        self.assertFalse(controller.move_caution_acknowledged)
        self.assertEqual(controller._move_caution_state(low, 0),
                         (True, "available"))

    def test_every_page_routes_to_a_renderer(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.page = FEATHER.Page.IDLE_HOME
        called = []
        routes = {
            FEATHER.Page.IDLE_HOME: "_render_home",
            FEATHER.Page.MAIN_MENU: "_render_main_menu",
            FEATHER.Page.CONTROL_HOME: "_render_control_home",
            FEATHER.Page.FILE_BROWSER: "_render_file_browser",
            FEATHER.Page.FILE_CONFIRM: "_render_file_confirm",
            FEATHER.Page.PRINTING: "_render_print_page",
            FEATHER.Page.PAUSED: "_render_print_page",
            FEATHER.Page.CANCEL_CONFIRM: "_render_cancel_confirm",
            FEATHER.Page.CONTROL_MOVE: "_render_move",
            FEATHER.Page.CONTROL_HEAT: "_render_heat",
            FEATHER.Page.FILAMENT_MATERIAL: "_render_filament_material",
            FEATHER.Page.FILAMENT_ACTION: "_render_filament_action",
            FEATHER.Page.CALIBRATION_HOME: "_render_calibration_home",
            FEATHER.Page.CALIBRATION_Z: "_render_calibration_z",
            FEATHER.Page.CALIBRATION_CONFIRM: "_render_calibration_confirm",
            FEATHER.Page.CALIBRATION_PROGRESS: "_render_calibration_progress",
            FEATHER.Page.CALIBRATION_RESULT: "_render_calibration_result",
            FEATHER.Page.SETTINGS: "_render_settings",
            FEATHER.Page.MOD_SETTINGS: "_render_mod_settings",
            FEATHER.Page.MOD_ENUM: "_render_mod_enum",
            FEATHER.Page.MOD_VALUE: "_render_mod_value",
            FEATHER.Page.NETWORK_HOME: "_render_network_home",
            FEATHER.Page.WIFI_SCAN: "_render_wifi_scan",
            FEATHER.Page.WIFI_PASSWORD: "_render_keyboard",
            FEATHER.Page.NETWORK_PROGRESS: "_render_network_progress",
            FEATHER.Page.RECOVERY_PROMPT: "_render_recovery_prompt",
            FEATHER.Page.RECOVERY_CONFIRM: "_render_recovery_confirm",
            FEATHER.Page.MESSAGE: "_render_message",
            FEATHER.Page.ERROR: "_render_error",
        }
        for method in set(routes.values()):
            setattr(controller, method,
                    lambda method=method: called.append(method))
        for page, method in routes.items():
            called[:] = []
            controller._show_page(page)
            self.assertEqual(called, [method], page)

    def test_dashboard_refresh_redraws_only_changed_panel(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.page = FEATHER.Page.IDLE_HOME
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.extruder = StatusObject({"temperature": 20.0, "target": 0.0})
        controller.heater_bed = StatusObject({"temperature": 21.0, "target": 0.0})
        controller.toolhead = StatusObject({"homed_axes": ""})
        controller.network_status = {"mode": "ETHERNET", "ssid": "",
                                     "signal": "", "ip": "192.168.2.124"}
        controller.last_job_name = "NONE"
        controller.filament_material = "PLA"
        controller._last_dashboard = None
        controller._read_text = lambda path: ""
        controller._update_dashboard(100)
        controller.extruder.status["temperature"] = 22.0
        controller._update_dashboard(101)
        update = "\n".join(batches[1])
        self.assertIn("-p 28 153 -s 229 78", update)
        self.assertNotIn("-p 285 153", update)
        self.assertNotIn("-p 542 153", update)
        self.assertNotIn("NO ACTIVE JOB", update)

    def test_calibration_menu_contains_workflow_context(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.params = type("Params", (), {
            "variables": {"z_offset": 0.125}})()
        controller._render_calibration_home()
        drawing = "\n".join(batches[0])
        self.assertIn("SAVED +0.125 MM", drawing)
        self.assertIn("LEVEL BED USING", drawing)
        self.assertIn("ADJUSTMENT SCREWS", drawing)
        self.assertIn("PROBE BED AND CREATE", drawing)
        self.assertIn("PROFILE AUTO", drawing)
        self.assertIn("cal.mesh -p 30 295 -s 740 90", drawing)

    def test_screw_calibration_confirm_offers_clean_and_cooldown_paths(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.calibration_kind = "screws"
        controller.calibration_material = "PETG"
        controller.calibration_clean_nozzle = True
        controller._render_calibration_confirm()
        drawing = "\n".join(batches[-1])
        self.assertIn("CLEAR_NOZZLE", drawing)
        self.assertIn("cal.clean.skip", drawing)
        self.assertIn("WITHOUT CLEANING", drawing)
        self.assertIn("probe cooldown temperature", drawing)
        self.assertIn("cal.material.PETG", drawing)

        controller.calibration_clean_nozzle = False
        controller._render_calibration_confirm()
        drawing = "\n".join(batches[-1])
        self.assertIn("cal.clean.skip", drawing)
        self.assertIn("--border b47aff", drawing)

    def test_screw_calibration_marks_only_current_phase_with_accent(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.renderer = FEATHER.FeatherRenderer()
        controller.calibration_kind = "screws"
        controller.calibration_clean_nozzle = True
        controller.calibration_repeat_probe = False
        drawing = "\n".join(controller._calibration_stage_commands(
            "BED SCREWS: HEATING"))
        self.assertIn("stroke -p 55 225 -s 128 38 -c 35d9e6", drawing)
        self.assertIn("stroke -p 195 225 -s 128 38 -c b47aff", drawing)
        self.assertIn("stroke -p 335 225 -s 128 38 -c 263238", drawing)
        self.assertEqual(drawing.count("-c b47aff"), 2)

    def test_screw_repeat_progress_contains_only_probe_and_done(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.renderer = FEATHER.FeatherRenderer()
        controller.calibration_kind = "screws"
        controller.calibration_repeat_probe = True
        drawing = "\n".join(controller._calibration_stage_commands(
            "BED SCREWS: PROBING"))
        self.assertIn('-t "PROBE"', drawing)
        self.assertIn('-t "DONE"', drawing)
        self.assertNotIn('-t "PREP"', drawing)
        self.assertNotIn('-t "HEAT"', drawing)
        self.assertEqual(drawing.count("-c b47aff"), 2)

    def test_settings_buttons_use_compact_signed_step_labels(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.params = type("Params", (), {"variables": {
            "backlight": 50, "backlight_eco": 10, "sound": 1}})()
        controller._render_settings()
        drawing = "\n".join(batches[0])
        self.assertIn('-t " -5"', drawing)
        self.assertIn('-t "+5"', drawing)
        self.assertIn("MOD PARAMETERS", drawing)
        self.assertIn("COLOR THEME", drawing)
        self.assertIn("DEFAULT", drawing)
        self.assertIn("--batch stroke -p 679 249 -s 76 38 -c 35d9e6 -lw 2",
                      drawing)
        self.assertIn("--batch fill -p 722 254 -s 28 28 -c 35d9e6", drawing)
        self.assertNotIn('[ OFF |', drawing)
        self.assertNotIn('[ >OFF< |', drawing)

    def test_mod_settings_list_scrolls_and_uses_square_toggles(self):
        params = [mod_param("flag%d" % index, bool, False,
                            "Feature %d" % index,
                            "Feature description %d." % index)
                  for index in range(7)]
        controller = mod_controller(params, {param.key: False for param in params})

        controller._render_mod_settings()
        first = "\n".join(controller.draw_batches[-1])
        self.assertIn("01-05 / 07", first)
        self.assertIn("--batch stroke -p 624 101 -s 76 38 -c 35d9e6 -lw 2",
                      first)
        self.assertIn("--batch fill -p 629 106 -s 28 28 -c 35d9e6", first)
        self.assertNotIn('[ OFF |', first)
        self.assertNotIn('[ >OFF< |', first)
        self.assertIn("--id 1:mod.next", first)
        self.assertNotIn("--id 1:mod.prev", first)

        controller._handle_mod_action("mod.next")
        second = "\n".join(controller.draw_batches[-1])
        self.assertIn("06-07 / 07", second)
        self.assertIn("--id 2:mod.prev", second)
        self.assertNotIn("--id 2:mod.next", second)

    def test_mod_boolean_toggle_updates_without_opening_an_editor(self):
        flag = mod_param("camera", bool, False, "Alt camera")
        controller = mod_controller([flag], {"camera": False})

        controller._handle_mod_action("mod.item.0")

        self.assertEqual(controller.params.updated, [("camera", True)])
        self.assertIsNone(controller.mod_parameter)
        drawing = "\n".join(controller.draw_batches[-1])
        self.assertIn("--batch fill -p 667 106 -s 28 28 -c 35d9e6", drawing)
        self.assertNotIn('[ OFF |', drawing)
        self.assertNotIn('[ >OFF< |', drawing)

    def test_theme_parameter_uses_dynamic_paginated_picker(self):
        theme = mod_param("feather_theme", str, "DEFAULT",
                          "Feather color theme")
        controller = mod_controller([theme], {"feather_theme": "DEFAULT"})

        controller._handle_mod_action("mod.item.0")
        first = "\n".join(controller.draw_batches[-1])
        self.assertEqual(controller.page, FEATHER.Page.MOD_ENUM)
        self.assertIn("1/3", first)
        self.assertIn("CYBERPUNK_RED", first)
        self.assertIn("--id 1:mod.enum.next", first)

        controller._handle_mod_action("mod.enum.next")
        second = "\n".join(controller.draw_batches[-1])
        self.assertIn("2/3", second)
        options = list(controller.renderer.theme_names())
        ocean_index = options.index("OCEAN")
        self.assertGreaterEqual(ocean_index, 4)
        controller._handle_mod_action("mod.option.%d" % ocean_index)
        controller._handle_mod_action("mod.apply")

        self.assertEqual(controller.params.updated,
                         [("feather_theme", "OCEAN")])
        self.assertEqual(controller.renderer.theme_name, "OCEAN")
        drawing = "\n".join(controller.draw_batches[-1])
        self.assertIn("-c 02080d", drawing)

    def test_settings_opens_theme_picker_and_returns_to_settings(self):
        theme = mod_param("feather_theme", str, "DEFAULT",
                          "Feather color theme")
        controller = mod_controller([theme], {"feather_theme": "DEFAULT"})
        controller.page = FEATHER.Page.SETTINGS

        controller._handle_settings_action("settings.theme")

        self.assertEqual(controller.page, FEATHER.Page.MOD_ENUM)
        self.assertEqual(controller.mod_return_page, FEATHER.Page.SETTINGS)
        controller._handle_mod_action("mod.option.3")
        controller._handle_mod_action("mod.apply")
        self.assertEqual(controller.page, FEATHER.Page.SETTINGS)

    def test_toggle_thumb_is_centered_and_animates_between_halves(self):
        renderer = FEATHER.FeatherRenderer()
        batches = []
        callbacks = []
        renderer.send = batches.append
        initial = "\n".join(renderer.toggle(
            "flag", 100, 50, 76, 38, False))
        self.assertIn("--batch fill -p 105 55 -s 28 28", initial)

        renderer.animate_toggle(
            "flag", True,
            lambda callback, delay: callbacks.append((delay, callback)))
        self.assertIn("--batch fill -p 114 55 -s 28 28",
                      "\n".join(batches[-1]))
        for delay, callback in callbacks:
            callback(100 + delay)
        self.assertIn("--batch fill -p 143 55 -s 28 28",
                      "\n".join(batches[-1]))

    def test_fast_mod_update_blocks_input_without_showing_modal(self):
        flag = mod_param("camera", bool, False, "Alt camera")
        controller = mod_controller([flag], {"camera": False})
        controller.reactor = DeferredReactor()
        controller._render_mod_settings()

        controller._handle_mod_action("mod.item.0")
        self.assertTrue(controller.mod_update_pending)
        self.assertIn("clear-hitboxes",
                      "\n".join(controller.draw_batches[-1]))
        self.assertNotIn("APPLYING CHANGES",
                         "\n".join("\n".join(batch)
                                   for batch in controller.draw_batches))

        controller.reactor.run_until(100.14)
        self.assertFalse(controller.mod_update_pending)
        controller.reactor.run_until(100.3)
        self.assertNotIn("APPLYING CHANGES",
                         "\n".join("\n".join(batch)
                                   for batch in controller.draw_batches))

    def test_slow_mod_update_keeps_modal_visible_for_minimum_time(self):
        flag = mod_param("camera", bool, False, "Alt camera")
        controller = mod_controller([flag], {"camera": False})
        reactor = DeferredReactor()
        controller.reactor = reactor
        completed = []

        class SlowManager(ModManager):
            def set_value(manager, key, value):
                result = super(SlowManager, manager).set_value(key, value)

                def notify(eventtime):
                    controller._show_mod_update_modal(
                        eventtime + 0.3, controller.mod_update_token)
                    reactor.now = eventtime + 0.4

                reactor.register_callback(notify)
                return result

        controller.params = SlowManager([flag], {"camera": False})
        controller._set_mod_value(flag, "1",
                                  lambda: completed.append(reactor.monotonic()))
        reactor.run_until(100.4)
        drawing = "\n".join("\n".join(batch)
                            for batch in controller.draw_batches)
        self.assertIn("APPLYING CHANGES", drawing)
        self.assertTrue(controller.mod_update_pending)

        reactor.run_until(100.524)
        self.assertTrue(controller.mod_update_pending)
        self.assertEqual(completed, [])
        reactor.run_until(100.525)
        self.assertFalse(controller.mod_update_pending)
        self.assertEqual(len(completed), 1)
        self.assertAlmostEqual(completed[0], 100.525)

    def test_mod_enum_selection_is_staged_until_apply(self):
        Display = enum.Enum("Display", {"STOCK": 0, "FEATHER": 1,
                                         "HEADLESS": 2, "GUPPY": 3})
        param = mod_param("display", Display, 1, "Display",
                          "Choose the active local screen.",
                          {"STOCK": "Stock", "FEATHER": "Feather",
                           "HEADLESS": "Headless", "GUPPY": "Guppy"})
        controller = mod_controller([param], {"display": 1})

        controller._handle_mod_action("mod.item.0")
        self.assertEqual(controller.page, FEATHER.Page.MOD_ENUM)
        self.assertEqual(controller.params.updated, [])
        controller._handle_mod_action("mod.option.3")
        drawing = "\n".join(controller.draw_batches[-1])
        self.assertIn("GUPPY // GUPPY  [SELECTED]", drawing)
        controller._handle_mod_action("mod.apply")

        self.assertEqual(controller.params.updated, [("display", 3)])
        self.assertEqual(controller.page, FEATHER.Page.MOD_SETTINGS)

    def test_mod_numeric_editor_rejects_decimal_for_integer(self):
        param = mod_param("park_dz", int, 50, "Park offset")
        controller = mod_controller([param], {"park_dz": 50})
        controller._handle_mod_action("mod.item.0")
        self.assertEqual(controller.page, FEATHER.Page.MOD_VALUE)
        controller.mod_edit_value = ""
        controller._handle_mod_action("mod.key.7")
        controller._handle_mod_action("mod.dot")
        controller._handle_mod_action("mod.key.5")
        self.assertEqual(controller.mod_edit_value, "75")
        controller._handle_mod_action("mod.save")
        self.assertEqual(controller.params.updated, [("park_dz", 75)])

    def test_mod_string_editor_supports_shift_symbols_space_and_backspace(self):
        param = mod_param("midi_on", str, "", "Startup MIDI")
        controller = mod_controller([param], {"midi_on": ""})
        controller._handle_mod_action("mod.item.0")
        controller._handle_mod_action("mod.shift")
        controller._handle_mod_action("mod.key.a")
        controller._handle_mod_action("mod.space")
        controller._handle_mod_action("mod.symbols")
        controller._handle_mod_action("mod.key.hash")
        controller._handle_mod_action("mod.backspace")
        controller._handle_mod_action("mod.key.dot")
        controller._handle_mod_action("mod.save")

        self.assertEqual(controller.params.updated, [("midi_on", "A .")])

    def test_mod_page_hitboxes_stay_above_persistent_footer(self):
        params = [mod_param("flag%d" % index, bool, False,
                            "Feature %d" % index)
                  for index in range(5)]
        controller = mod_controller(params, {param.key: False for param in params})
        controller._render_mod_settings()
        footer = (0, UI.FOOTER_Y, UI.SCREEN_WIDTH, UI.FOOTER_HEIGHT)
        for action, spec in controller.renderer._buttons.items():
            if action == "nav.back":
                continue
            self.assertFalse(UI.rectangles_overlap(spec[:4], footer), action)

    def test_eco_wake_restores_backlight_once(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.dimmed = True
        values = []
        controller._setting = lambda key, default: 55
        controller._set_backlight = values.append
        self.assertTrue(controller._wake_if_dimmed())
        self.assertFalse(controller._wake_if_dimmed())
        self.assertEqual(values, [55])

    def test_pending_print_action_rejects_repeat_tap(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.print_state = FEATHER.PrintState.PRINTING
        controller.reactor = Reactor()
        controller.last_action_time = -1
        controller.pending_action = "print.pause"
        controller.page = FEATHER.Page.PRINTING
        controller.debug = False
        calls = []
        controller._handle_print_action = calls.append
        controller._dispatch_action("print.pause")
        self.assertEqual(calls, [])

    def test_print_page_always_registers_cancel_hitbox(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.reactor = Reactor()
        controller.print_state = FEATHER.PrintState.PRINTING
        controller.pending_action = None
        controller.print_status_text = "Heating"
        controller.virtual_sdcard = type("SD", (), {
            "file_path": lambda self: "/data/test.gcode"})()
        controller._z_adjust_allowed = lambda eventtime: False
        controller._update_print_progress = lambda eventtime: None
        controller._render_print_page()
        drawing = "\n".join(batches[0])
        self.assertIn("-p 605 355 -s 175 72", drawing)
        self.assertIn("--id 1:print.cancel", drawing)
        for label in ("PAUSE", "FILAMENT", "Z ADJUST", "CANCEL"):
            command = next(line for line in drawing.splitlines()
                           if '--batch button' in line and '-t "%s"' % label in line)
            self.assertIn('-f "JetBrainsMono Bold 12pt"', command)

    def test_print_progress_shows_remaining_layer_and_height(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.page = FEATHER.Page.PRINTING
        controller._last_progress = None
        controller._progress_floor = 0.0
        controller._last_time = None
        controller.print_stats = StatusObject({
            "state": "printing", "print_duration": 100,
            "info": {"current_layer": None, "total_layer": None},
        })
        controller.virtual_sdcard = StatusObject({"progress": 0.25})
        controller.virtual_sdcard.estimate_print_time = 400.0
        controller.toolhead = StatusObject({
            "position": (10.0, 20.0, 3.25, 0.0), "homed_axes": "xyz"})

        controller._update_print_progress(100)

        drawing = "\n".join(batches[0])
        self.assertIn("00:01:40", drawing)
        self.assertIn("00:05:00", drawing)
        self.assertIn('? / ?', drawing)
        self.assertIn("3.25 MM", drawing)

    def test_print_progress_uses_sd_position_and_never_moves_backwards(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller._progress_floor = 0.0
        controller._m73_start_expiry = 0.0
        controller._m73_active = False
        controller.display_status = type("Display", (), {
            "progress": None, "expire_progress": 0.0})()
        controller.print_stats = StatusObject({"print_duration": 0.0})
        sdcard = StatusObject({"progress": 0.12})
        sdcard.estimate_print_time = None
        controller.virtual_sdcard = sdcard

        self.assertEqual(controller._print_progress(1.0), 0.12)
        sdcard.status["progress"] = 0.09
        self.assertEqual(controller._print_progress(2.0), 0.12)
        sdcard.status["progress"] = 0.15
        self.assertEqual(controller._print_progress(3.0), 0.15)

    def test_print_progress_prefers_current_print_m73(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller._progress_floor = 0.0
        controller._m73_start_expiry = 10.0
        controller._m73_active = False
        controller.display_status = type("Display", (), {
            "progress": 0.37, "expire_progress": 25.0})()
        controller.print_stats = StatusObject({"print_duration": 50.0})
        controller.virtual_sdcard = StatusObject({
            "progress": 0.80, "estimate_print_time": 100.0})
        controller.virtual_sdcard.estimate_print_time = 100.0

        self.assertEqual(controller._print_progress(20.0), 0.37)
        self.assertEqual(controller._progress_source, "M73")
        controller.display_status.progress = 0.29
        controller.display_status.expire_progress = 30.0
        self.assertEqual(controller._print_progress(25.0), 0.37)

    def test_print_progress_uses_time_estimate_before_sd_fallback(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller._progress_floor = 0.0
        controller._m73_start_expiry = 10.0
        controller._m73_active = False
        controller.display_status = type("Display", (), {
            "progress": 0.75, "expire_progress": 10.0})()
        controller.print_stats = StatusObject({"print_duration": 25.0})
        controller.virtual_sdcard = StatusObject({
            "progress": 0.80, "estimate_print_time": 100.0})
        controller.virtual_sdcard.estimate_print_time = 100.0

        self.assertEqual(controller._print_progress(20.0), 0.25)
        self.assertEqual(controller._progress_source, "TIME")

    def test_cancel_confirmation_uses_plain_cancel_label(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.pending_action = None
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append

        controller._render_cancel_confirm()

        drawing = "\n".join(batches[0])
        self.assertIn('-t "CANCEL"', drawing)
        self.assertNotIn("CANCEL ...", drawing)

    def test_filament_continue_is_next_to_action_buttons(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.reactor = Reactor()
        controller.filament_material = "PLA"
        controller.filament_from_pause = True
        controller.extruder = StatusObject({
            "temperature": 220.0, "target": 220.0})
        controller.extruder.min_extrude_temp = 170.0

        controller._render_filament_action()

        drawing = "\n".join(batches[0])
        for action, x in (("filament.load", 20), ("filament.unload", 215),
                          ("filament.purge", 410), ("filament.resume", 605)):
            self.assertIn("-p %d 165 -s 175 100" % x, drawing)
            self.assertIn("--id 1:%s" % action, drawing)
        self.assertIn('-t "CONTINUE"', drawing)

    def test_terminal_print_state_becomes_idle_and_reports_result(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.print_state = FEATHER.PrintState.PAUSED
        controller.pending_action = "print.cancel.confirm"
        controller.reactor = Reactor()
        controller.debug = False
        messages = []
        controller._show_message = lambda message, page: messages.append((message, page))
        controller._change_print_state(FEATHER.PrintState.IDLE, "cancelled")
        self.assertEqual(controller.print_state, FEATHER.PrintState.IDLE)
        self.assertEqual(messages, [("Print cancelled", FEATHER.Page.IDLE_HOME)])

    def test_preheat_presets_respect_real_heater_limits(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.extruder = type("Extruder", (), {
            "heater": type("Heater", (), {"min_temp": 0, "max_temp": 251})()})()
        controller.heater_bed = type("Bed", (), {"min_temp": 0, "max_temp": 91})()
        self.assertEqual(controller._limited_preheat("ABS"), (250, 90))

    def test_filament_extrusion_is_blocked_when_cold(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.reactor = Reactor()
        controller.filament_from_pause = True
        controller.print_stats = StatusObject({"state": "paused"})
        controller.extruder = StatusObject({"temperature": 160})
        controller.extruder.min_extrude_temp = 170
        controller.gcode = GCodeRecorder()
        controller._require_idle = lambda: None
        with self.assertRaisesRegex(RuntimeError, "not hot enough"):
            controller._handle_filament_action("filament.load")
        controller.extruder.status["temperature"] = 180
        controller._toast = lambda message: None
        controller._handle_filament_action("filament.purge")
        self.assertEqual(controller.gcode.commands, ["PURGE_FILAMENT"])

    def test_idle_filament_flow_restores_original_target(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.filament_from_pause = False
        controller.filament_original_target = 185
        controller.gcode = GCodeRecorder()
        pages = []
        controller._show_page = pages.append
        controller._finish_filament(False)
        self.assertEqual(controller.gcode.commands, ["M104 S185"])
        self.assertEqual(pages, [FEATHER.Page.IDLE_HOME])

    def test_first_layer_z_gate_requires_metadata(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.print_stats = StatusObject(
            {"state": "printing", "info": {"current_layer": None}})
        self.assertFalse(controller._z_adjust_allowed(0))
        controller.print_stats.status["info"]["current_layer"] = 1
        self.assertTrue(controller._z_adjust_allowed(0))
        controller.print_stats.status["info"]["current_layer"] = 2
        self.assertFalse(controller._z_adjust_allowed(0))

    def test_z_adjust_rechecks_layer_and_session_limit(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.reactor = Reactor()
        controller.print_state = FEATHER.PrintState.PRINTING
        controller.print_stats = StatusObject(
            {"state": "printing", "info": {"current_layer": 2}})
        controller.z_session_adjust = 0.0
        controller.z_adjust_session_limit = 0.5
        controller.z_offset_limit = 2.0
        controller.gcode_move = StatusObject({"homing_origin": (0, 0, 0)})
        controller.gcode = GCodeRecorder()
        with self.assertRaisesRegex(RuntimeError, "first layer"):
            controller._apply_z_adjust(0.01)
        controller.print_stats.status["info"]["current_layer"] = 1
        controller.z_session_adjust = 0.49
        with self.assertRaisesRegex(RuntimeError, "session limit"):
            controller._apply_z_adjust(0.05)

    def test_network_operation_timeout_terminates_helper(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.network_process = type("Process", (), {"poll": lambda self: None})()
        controller.network_deadline = 10
        messages = []
        controller._cancel_network_process = messages.append
        controller._poll_network_process(11)
        self.assertEqual(messages, ["Network operation timed out"])

    def test_mesh_uses_auto_profile_and_selected_preheat(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.calibration_kind = "mesh"
        controller.calibration_material = "PETG"
        controller.calibration_error = None
        controller.gcode = GCodeRecorder()
        controller._require_idle = lambda: None
        controller._limited_preheat = lambda material: (245, 68)
        pages = []
        controller._show_page = pages.append
        controller._run_calibration(0)
        self.assertEqual(controller.gcode.commands,
                         ["AUTO_FULL_BED_LEVEL EXTRUDER_TEMP=245 BED_TEMP=68 PROFILE=auto"])
        self.assertEqual(pages, [FEATHER.Page.CALIBRATION_RESULT])

    def test_screw_calibration_passes_selected_cleaning_path(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.calibration_kind = "screws"
        controller.calibration_material = "PETG"
        controller.calibration_clean_nozzle = False
        controller.calibration_repeat_probe = False
        controller.calibration_error = None
        controller.gcode = GCodeRecorder()
        controller._require_idle = lambda: None
        controller._limited_preheat = lambda material: (245, 68)
        pages = []
        controller._show_page = pages.append
        controller._run_calibration(0)
        self.assertEqual(controller.gcode.commands, [
            "BED_LEVEL_SCREWS_TUNE EXTRUDER_TEMP=245 BED_TEMP=68 CLEAN=0"])
        self.assertEqual(pages, [FEATHER.Page.CALIBRATION_RESULT])

    def test_screw_repeat_starts_probe_immediately_without_confirm(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.calibration_kind = "screws"
        controller.calibration_results = [{"name": "old"}]
        controller.calibration_mesh = []
        controller.calibration_error = None
        controller.reactor = DeferredReactor()
        controller._require_idle = lambda: None
        pages = []
        controller._show_page = pages.append
        controller._handle_calibration_action("cal.repeat")
        self.assertEqual(pages, [FEATHER.Page.CALIBRATION_PROGRESS])
        self.assertTrue(controller.calibration_repeat_probe)
        self.assertEqual(controller.calibration_results, [])
        self.assertEqual(controller.print_status_text, "BED SCREWS: PROBING")
        self.assertEqual(len(controller.reactor.callbacks), 1)

        controller.gcode = GCodeRecorder()
        controller._show_page = pages.append
        controller._run_calibration(0)
        self.assertEqual(controller.gcode.commands, ["BED_LEVEL_SCREWS_PROBE"])

    def test_calibration_error_returns_result_page(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.calibration_kind = "mesh"
        controller.calibration_material = "PLA"
        controller.calibration_error = None
        controller.gcode = FailingGCode()
        controller._require_idle = lambda: None
        controller._limited_preheat = lambda material: (220, 60)
        pages = []
        controller._show_page = pages.append
        with self.assertLogs(level="ERROR"):
            controller._run_calibration(0)
        self.assertEqual(controller.calibration_error, "macro failed")
        self.assertEqual(pages, [FEATHER.Page.CALIBRATION_RESULT])

    def test_mcu_failures_are_classified_for_firmware_restart(self):
        classify = FEATHER.FeatherScreen._classify_error
        self.assertEqual(
            classify("MCU 'mcu' shutdown: Timer too close"),
            "firmware_restart")
        self.assertEqual(
            classify("Lost communication with MCU 'mcu'"),
            "firmware_restart")
        self.assertEqual(
            classify("ADC out of range", "shutdown"),
            "firmware_restart")
        self.assertEqual(
            classify("Option 'foo' is not valid", "error"),
            "restart")
        self.assertIsNone(classify("Klipper disconnected", "disconnect"))
        self.assertIsNone(classify("Home X before moving"))

    def test_error_page_offers_firmware_restart_with_padded_dialog(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.error_message = "MCU 'mcu' shutdown: Timer too close"
        controller.error_recovery = "firmware_restart"

        controller._render_error()

        drawing = "\n".join(batches[0])
        self.assertIn("MCU RESTART REQUIRED", drawing)
        self.assertIn("FIRMWARE RESTART", drawing)
        self.assertIn("error.firmware_restart", drawing)
        self.assertIn("--batch fill -p 80 85 -s 640 325", drawing)

    def test_firmware_restart_action_switches_to_animated_startup(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.error_message = "shutdown"
        controller.error_category = "shutdown"
        controller.error_recovery = "firmware_restart"
        controller.startup_phase = 3
        controller.startup_timer = None
        controller.timer = None
        started = []
        controller._start_pre_ready_ui = lambda: started.append(True)
        controller.gcode = GCodeRecorder()
        controller.reactor = Reactor()
        controller.command_depth = 0

        controller._handle_error_action("error.firmware_restart")

        self.assertEqual(started, [True])
        self.assertEqual(controller.gcode.commands, ["FIRMWARE_RESTART"])
        self.assertEqual(controller.startup_phase, 0)
        self.assertEqual(controller.error_message, "")

    def test_startup_tick_advances_pulse_until_klipper_is_ready(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        pulses = []
        controller.renderer = type("Renderer", (), {
            "active": True,
            "startup_pulse": lambda self, phase: ["pulse %d" % phase],
            "send": lambda self, commands: pulses.append(commands),
        })()
        controller.printer = type("Printer", (), {
            "get_state_message": lambda self: ("Printer is not ready", "startup"),
        })()
        controller.reactor = type("Reactor", (), {"NEVER": 1.0e30})()
        controller.event_handle = object()
        controller.print_state = FEATHER.PrintState.INACTIVE
        controller.error_message = ""
        controller.startup_phase = 0

        wake = controller._startup_tick(10.0)
        self.assertEqual(pulses, [["pulse 1"]])
        self.assertAlmostEqual(wake, 10.0 + FEATHER.STARTUP_ANIMATION_PERIOD)

        controller.print_state = FEATHER.PrintState.IDLE
        self.assertEqual(controller._startup_tick(11.0),
                         controller.reactor.NEVER)

    def test_startup_tick_replaces_animation_with_config_error(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        shown = []
        controller.renderer = type("Renderer", (), {"active": True})()
        controller.reactor = type("Reactor", (), {"NEVER": 1.0e30})()
        controller.printer = type("Printer", (), {
            "get_state_message": lambda self: (
                "Option 'foo' is not valid", "error"),
        })()
        controller.event_handle = object()
        controller.print_state = FEATHER.PrintState.INACTIVE
        controller.error_message = ""
        controller.startup_timer = object()
        controller._show_error = (
            lambda message, category: shown.append((message, category)))

        wake = controller._startup_tick(10.0)

        self.assertEqual(wake, controller.reactor.NEVER)
        self.assertEqual(controller.startup_timer, None)
        self.assertEqual(shown, [("Option 'foo' is not valid", "error")])


class ResurrectionStatusTest(unittest.TestCase):
    def test_status_hides_absolute_path_and_reports_progress(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=True) as stream:
            json.dump({"file_path": "/data/gcodes/part.gcode", "file_position": 25,
                       "file_size": 100, "extruder_temp": 220, "bed_temp": 60,
                       "mesh": "auto"}, stream)
            stream.flush()
            resurrector = RESURRECTION.Resurrector.__new__(RESURRECTION.Resurrector)
            resurrector.state = RESURRECTION.ResurrectorState.RESURRECTION
            resurrector.file_path = stream.name
            status = resurrector.get_status(0)
        self.assertTrue(status["available"])
        self.assertEqual(status["filename"], "part.gcode")
        self.assertEqual(status["progress"], 0.25)
        self.assertNotIn("file_path", status)


if __name__ == "__main__":
    unittest.main()
