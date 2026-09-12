## Tests for Feather screen behavior.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import configparser
import importlib.util
import enum
import json
import pathlib
import re
import tempfile
import threading
import unittest
from unittest import mock


MODULE_PATH = (pathlib.Path(__file__).parents[1] / ".py" / "klipper" /
               "plugins" / "feather_screen.py")
SPEC = importlib.util.spec_from_file_location("feather_screen", MODULE_PATH)
FEATHER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FEATHER)
UI = __import__("ui")
NETWORK_PROTOCOL = __import__("feather_netd_protocol")
from ff5m_ui.move import runtime as MOVE_LAYOUT
from ff5m_ui.z_offset import runtime as Z_OFFSET_LAYOUT
from feather_feature_z import ZCalibrationFeature

# Unit controllers created with __new__ do not receive klippy:ready. Give
# those isolated fixtures the same catalog that config/material.cfg provides;
# production code has no Python-side material defaults.
FEATHER.FeatherScreen.heating_materials = (
    "PLA", "PETG", "ABS", "ABS-PC", "TPU")
FEATHER.FeatherScreen.heating_profiles = {
    "PLA": (220, 60), "PETG": (250, 70), "ABS": (260, 85),
    "ABS-PC": (270, 105), "TPU": (220, 50),
}
FEATHER.FeatherScreen.cold_pull_materials = (
    "PLA", "PETG", "ABS", "NYLON")
FEATHER.FeatherScreen.cold_pull_profiles = {
    "PLA": (220, 100), "PETG": (250, 100), "ABS": (260, 105),
    "NYLON": (265, 120),
}


def joystick_values(snapshot, inertia=0.0, cursor=None):
    values = MOVE_LAYOUT.snapshot_values(snapshot)
    values[MOVE_LAYOUT.MoveState.INERTIA] = float(inertia)
    values[MOVE_LAYOUT.MoveState.CURSOR] = cursor
    return values


MOD_UI = __import__("feather_mod_settings")
SETTINGS = __import__("feather_feature_settings")
PAGES = __import__("feather_screen_pages")
KEYBOARD = __import__("feather_keyboard")
PAGINATION = __import__("feather_pagination")

MOD_PARAMS_PATH = (pathlib.Path(__file__).parents[1] / ".py" / "klipper" /
                   "plugins" / "mod_params.py")
MOD_PARAMS_SPEC = importlib.util.spec_from_file_location(
    "feather_mod_params", MOD_PARAMS_PATH)
MOD_PARAMS = importlib.util.module_from_spec(MOD_PARAMS_SPEC)
MOD_PARAMS_SPEC.loader.exec_module(MOD_PARAMS)

RESURRECTION_PATH = (pathlib.Path(__file__).parents[1] / ".py" / "klipper" /
                     "plugins" / "resurrection.py")
RESURRECTION_SPEC = importlib.util.spec_from_file_location(
    "feather_resurrection", RESURRECTION_PATH)
RESURRECTION = importlib.util.module_from_spec(RESURRECTION_SPEC)
RESURRECTION_SPEC.loader.exec_module(RESURRECTION)


class StatusObject:
    def __init__(self, status):
        self.status = status

    def get_status(self, eventtime):
        return dict(self.status)


class GCodeRecorder:
    def __init__(self):
        self.commands = []

    def run_script_from_command(self, command):
        self.commands.append(command)


class FailingGCode:
    def run_script_from_command(self, command):
        raise RuntimeError("macro failed")


class Reactor:
    def __init__(self, now=100.0):
        self.now = now

    def monotonic(self):
        return self.now

    def register_callback(self, callback, when=None):
        callback(self.now if when is None else when)


class DeferredReactor:
    def __init__(self, now=100.0):
        self.now = now
        self.callbacks = []
        self.sequence = 0

    def monotonic(self):
        return self.now

    def register_callback(self, callback, when=None):
        self.sequence += 1
        self.callbacks.append((
            self.now if when is None else when, self.sequence, callback))

    def run_until(self, deadline):
        while self.callbacks:
            scheduled, sequence, callback = min(
                self.callbacks, key=lambda item: (item[0], item[1]))
            if scheduled > deadline:
                break
            self.callbacks.remove((scheduled, sequence, callback))
            self.now = max(self.now, scheduled)
            callback(self.now)
        self.now = max(self.now, deadline)


class ModManager:
    def __init__(self, params, variables):
        self.params = params
        self.variables = dict(variables)
        self.updated = []
        self.params_map = dict((param.key, param) for param in params)
        category_ids = set(
            getattr(param, "ui_category", None) for param in params)
        self.ui_categories_map = dict(
            (category_id, type("Category", (), {
                "label": str(category_id).upper()})())
            for category_id in category_ids if category_id is not None)

    def set_value(self, key, value):
        param = next(param for param in self.params if param.key == key)
        kind = MOD_UI.parameter_kind(param)
        if kind == "bool":
            value = bool(int(value))
        elif kind == "enum":
            value = param.type[str(value)].value
        elif kind == "int":
            value = int(value)
        elif kind == "float":
            value = float(value)
        else:
            value = str(value)
        self.variables[key] = value
        self.updated.append((key, value))
        return value


def mod_param(key, param_type, default, label, description="Description",
              options=None, readonly=False, hidden=False, restart=None,
              ui_inverted=False, ui_category=None, ui_visible_if=None):
    return type("Param", (), {
        "key": key, "type": param_type, "default": default,
        "label": label, "description": description, "options": options,
        "readonly": readonly, "hidden": hidden, "warning": None,
        "restart": restart, "ui_inverted": ui_inverted,
        "ui_category": ui_category, "ui_visible_if": ui_visible_if,
    })()


def mod_controller(params, variables):
    host = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
    host.renderer = FEATHER.FeatherRenderer()
    host.draw_batches = []
    host.renderer.send = host.draw_batches.append
    host.params = ModManager(params, variables)
    host.reactor = Reactor()
    host.print_stats = StatusObject({"state": "standby"})
    host.virtual_sdcard = type(
        "SD", (), {"is_active": lambda self: False})()
    host.print_state = FEATHER.PrintState.IDLE
    host.page = FEATHER.ScreenPage.MOD_SETTINGS
    host.previous_page = FEATHER.ScreenPage.SETTINGS
    host.toast_until = 0
    host.toast_message = ""
    host._toast = lambda message: None

    feature = SETTINGS.SettingsFeature(host)

    def show_page(page):
        host.previous_page = host.page
        host.page = page
        if page in (FEATHER.ScreenPage.SETTINGS, FEATHER.ScreenPage.MOD_SETTINGS,
                    FEATHER.ScreenPage.PARAMETER_OPTIONS, FEATHER.ScreenPage.MOD_VALUE):
            feature.render(page)

    host._show_page = show_page
    return feature



class FeatherUtilitiesTest(unittest.TestCase):
    def test_shared_pagination_clamps_and_maps_visible_indices(self):
        pagination = PAGINATION.Pagination(list(range(8)), 99, 3)

        self.assertEqual(pagination.page, 2)
        self.assertEqual(pagination.page_count, 3)
        self.assertEqual(pagination.visible, [6, 7])
        self.assertTrue(pagination.has_previous)
        self.assertFalse(pagination.has_next)
        self.assertEqual(pagination.absolute_index(1), 7)
        self.assertIsNone(pagination.absolute_index(2))

    def test_file_entries_use_compact_slots_and_keep_mapping_access(self):
        entry = PAGES.FileEntry(
            "part.gcode", "/data/gcodes/part.gcode", False, 1024, 42)

        self.assertFalse(hasattr(entry, "__dict__"))
        self.assertEqual(entry["name"], "part.gcode")
        self.assertEqual(entry["size"], 1024)
        with self.assertRaises(KeyError):
            _value = entry["unknown"]

    def test_renderer_escapes_untrusted_text(self):
        quoted = FEATHER.FeatherRenderer.quote('file "one"\\two\nnext')
        self.assertEqual(quoted, '"file \\"one\\"\\\\two next"')

    def test_renderer_normalizes_fonts_from_active_manifest(self):
        from ui import font_metrics

        synthetic = font_metrics.parse_manifest({
            "schema": "font-metrics/v1",
            "wrap_algorithm": "word-v1",
            "fonts": [
                {
                    "name": "Display 12pt", "advance_x": 9,
                    "monospaced": True, "advance_y": 14,
                    "glyph_bounds": {"top": -11, "bottom": 2},
                    "unicode_ranges": [[32, 126]],
                },
                {
                    "name": "Display 8pt", "advance_x": 6,
                    "monospaced": True, "advance_y": 10,
                    "glyph_bounds": {"top": -8, "bottom": 1},
                    "unicode_ranges": [[32, 126]],
                },
            ],
        })
        with mock.patch("ui.renderer.get_font_metrics",
                        return_value=synthetic):
            normalize = FEATHER.FeatherRenderer.normalize_font
            self.assertEqual(normalize("Display 9pt"), "Display 8pt")
            self.assertEqual(normalize("Display 11pt"), "Display 12pt")
            command = FEATHER.FeatherRenderer().text(
                10, 10, "Visible", font="Display 10pt")

        self.assertIn('-f "Display 8pt"', command)
        self.assertNotIn("10pt", command)

    def test_leading_minus_is_not_parsed_as_a_typer_option(self):
        command = FEATHER.FeatherRenderer().text(10, 10, "-5")
        self.assertIn('-t " -5"', command)

    def test_wifi_password_validation(self):
        validate = FEATHER.FeatherScreen._valid_password
        self.assertFalse(validate("short"))
        self.assertTrue(validate("password"))
        self.assertTrue(validate("a" * 63))
        self.assertTrue(validate("a1" * 32))
        self.assertFalse(validate("z" * 64))
        self.assertFalse(validate("validpass\n"))

    def test_text_keyboards_share_digits_and_symbol_rows(self):
        rows = KEYBOARD.keyboard_rows(symbols=True)

        self.assertEqual(
            tuple(label for _token, label in rows[0]), tuple("1234567890"))
        self.assertIn(("hash", "#"), rows[1])
        self.assertIn(("dot", "."), rows[2])

    def test_shared_text_keyboard_covers_printable_ascii(self):
        characters = {" "}
        for symbols, shift in (
                (False, False), (False, True),
                (True, False), (True, True)):
            characters.update(
                label for row in KEYBOARD.keyboard_rows(symbols, shift)
                for _token, label in row)

        self.assertEqual(
            characters, set(chr(value) for value in range(32, 127)))
        shifted_symbols = KEYBOARD.keyboard_rows(symbols=True, shift=True)
        self.assertIn(("apostrophe", "'"), shifted_symbols[0])
        self.assertIn(("pipe", "|"), shifted_symbols[2])

    def test_shared_text_keyboard_applies_layout_filter_and_length(self):
        keyboard = KEYBOARD.TEXT_KEYBOARD
        value, cursor, shift, symbols = keyboard.apply(
            "", 0, "keyboard.shift", False, False)
        value, cursor, shift, symbols = keyboard.apply(
            value, cursor, "keyboard.key.a", shift, symbols)
        self.assertEqual((value, cursor, shift, symbols),
                         ("A", 1, True, False))

        value, cursor, shift, symbols = keyboard.apply(
            value, cursor, "keyboard.symbols", shift, symbols)
        self.assertEqual((shift, symbols), (False, True))
        value, cursor, shift, symbols = keyboard.apply(
            value, cursor, "keyboard.shift", shift, symbols)
        value, cursor, shift, symbols = keyboard.apply(
            value, cursor, "keyboard.key.pipe", shift, symbols)
        self.assertEqual(value, "A|")

        value, cursor, shift, symbols = keyboard.apply(
            value, cursor, "keyboard.key.tilde", shift, symbols,
            allowed_characters=lambda character: character.isalnum())
        self.assertEqual(value, "A|")
        value, cursor, shift, symbols = keyboard.apply(
            value, cursor, "keyboard.space", shift, symbols, max_length=2)
        self.assertEqual(value, "A|")

    def test_shared_text_keyboard_edits_at_cursor_and_clamps_movement(self):
        keyboard = KEYBOARD.TEXT_KEYBOARD
        value, cursor, shift, symbols = keyboard.apply(
            "ac", 2, "keyboard.left")
        value, cursor, shift, symbols = keyboard.apply(
            value, cursor, "keyboard.key.b", shift, symbols)
        self.assertEqual((value, cursor), ("abc", 2))

        value, cursor, shift, symbols = keyboard.apply(
            value, cursor, "keyboard.backspace", shift, symbols)
        self.assertEqual((value, cursor), ("ac", 1))
        for _step in range(3):
            value, cursor, shift, symbols = keyboard.apply(
                value, cursor, "keyboard.left", shift, symbols)
        self.assertEqual((value, cursor), ("ac", 0))
        for _step in range(4):
            value, cursor, shift, symbols = keyboard.apply(
                value, cursor, "keyboard.right", shift, symbols)
        self.assertEqual((value, cursor), ("ac", 2))

    def test_shared_text_keyboard_keeps_caret_inside_long_value_field(self):
        renderer = FEATHER.FeatherRenderer()
        commands = KEYBOARD.TEXT_KEYBOARD.render_value(
            renderer, "a" * 64, 32, 42, 147, 575,
            UI.ThemeColor.PRIMARY)

        caret = commands[-1]
        match = re.search(
            r"fill -p (\d+) (\d+) -s (\d+) (\d+)", caret)
        self.assertIsNotNone(match)
        self.assertGreaterEqual(int(match.group(1)), 42)
        self.assertLessEqual(int(match.group(1)), 615)
        self.assertGreater(int(match.group(3)), 0)
        self.assertGreater(int(match.group(4)), 0)

    def test_duration_formatting(self):
        duration = FEATHER.FeatherScreen._duration
        self.assertEqual(duration(None), "???")
        self.assertEqual(duration(0), "0s")
        self.assertEqual(duration(3661, 2), "1h 1m")

    def test_clock_duration_is_stable_and_handles_unknown_time(self):
        clock = FEATHER.FeatherScreen._clock_duration
        self.assertEqual(clock(None), "--:--:--")
        self.assertEqual(clock(4354), "01:12:34")
        self.assertEqual(clock(90061), "1d 01:01:01")

    def test_dashboard_reloads_timezone_only_when_localtime_changes(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        stat_result = type("Stat", (), {"st_ino": 10, "st_mtime": 20})()
        with (mock.patch.object(PAGES.os, "lstat", return_value=stat_result),
              mock.patch.object(PAGES.os.path, "islink", return_value=True),
              mock.patch.object(
                  PAGES.os, "readlink",
                  return_value="/usr/share/zoneinfo/Asia/Yekaterinburg"),
              mock.patch.object(PAGES.time, "tzset") as tzset):
            controller._refresh_local_timezone()
            controller._refresh_local_timezone()
        tzset.assert_called_once_with()

    def test_stale_actions_are_rejected(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        allowed = controller._action_allowed
        self.assertTrue(allowed(FEATHER.ScreenPage.FILE_CONFIRM, "file.start"))
        self.assertFalse(allowed(FEATHER.ScreenPage.IDLE_HOME, "file.start"))
        self.assertTrue(allowed(FEATHER.ScreenPage.CANCEL_CONFIRM,
                                "operation.cancel.confirm"))
        self.assertTrue(allowed(FEATHER.ScreenPage.CANCEL_CONFIRM,
                                "operation.cancel.back"))
        self.assertFalse(allowed(FEATHER.ScreenPage.PRINTING,
                                 "print.cancel.confirm"))

    def test_heater_targets_use_configured_limits(self):
        heater = type("Heater", (), {"min_temp": 10, "max_temp": 300})()
        clamp = FEATHER.FeatherScreen._clamp_heater_target
        self.assertEqual(clamp(0, heater, 250), 0)
        self.assertEqual(clamp(5, heater, 250), 10)
        self.assertEqual(clamp(350, heater, 250), 299)

    def test_page_actions_cover_navigation_and_reject_stale_taps(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        allowed = controller._action_allowed
        self.assertTrue(allowed(FEATHER.ScreenPage.IDLE_HOME, "nav.menu"))
        self.assertTrue(allowed(FEATHER.ScreenPage.IDLE_HOME, "nav.heat"))
        self.assertTrue(allowed(FEATHER.ScreenPage.IDLE_HOME, "nav.network"))
        self.assertTrue(allowed(FEATHER.ScreenPage.IDLE_HOME, "nav.job"))
        self.assertTrue(allowed(FEATHER.ScreenPage.IDLE_HOME, "home.last_job"))
        self.assertTrue(allowed(FEATHER.ScreenPage.IDLE_HOME, "nav.filament"))
        self.assertTrue(allowed(FEATHER.ScreenPage.IDLE_HOME, "nav.move"))
        self.assertTrue(allowed(FEATHER.ScreenPage.PRINTING, "nav.home"))
        self.assertFalse(allowed(FEATHER.ScreenPage.IDLE_HOME, "nav.settings"))
        self.assertTrue(allowed(FEATHER.ScreenPage.MAIN_MENU, "nav.filament"))
        self.assertTrue(allowed(FEATHER.ScreenPage.CONTROL_HOME, "nav.calibration"))
        # Feature-owned actions never fall back to the controller table.
        self.assertFalse(allowed(FEATHER.ScreenPage.CALIBRATION_CONFIRM,
                                 "cal.material.PETG"))
        self.assertFalse(allowed(FEATHER.ScreenPage.EXTRUDER_CALIBRATION,
                                 "extruder.feed100"))
        self.assertFalse(allowed(FEATHER.ScreenPage.CALIBRATION_HOME,
                                 "extruder.feed100"))
        # Declarative pages accept only actions registered in their real tree.
        self.assertFalse(allowed(FEATHER.ScreenPage.Z_OFFSET_SUMMARY,
                                 "z.zone.front_left"))
        self.assertFalse(allowed(FEATHER.ScreenPage.Z_OFFSET_PAPER, "z.probe"))
        host = type("Host", (), {})()
        host.page = FEATHER.ScreenPage.Z_OFFSET_SUMMARY
        z_feature = ZCalibrationFeature(host)
        self.assertEqual(
            z_feature.resolve_semantic_action(
                host.page,
                Z_OFFSET_LAYOUT.ZONE_ACTIONS["front_left"].wire_id),
            Z_OFFSET_LAYOUT.ZONE_ACTIONS["front_left"])
        host.page = FEATHER.ScreenPage.Z_OFFSET_PAPER
        self.assertEqual(
            z_feature.resolve_semantic_action(
                host.page, Z_OFFSET_LAYOUT.PROBE.wire_id),
            Z_OFFSET_LAYOUT.PROBE)
        self.assertFalse(allowed(FEATHER.ScreenPage.LIVE_Z_OFFSET,
                                 "live_z.closer"))
        self.assertFalse(allowed(FEATHER.ScreenPage.LIVE_Z_OFFSET,
                                 "live_z.save"))
        self.assertFalse(allowed(FEATHER.ScreenPage.Z_OFFSET_SUMMARY,
                                 "live_z.closer"))
        self.assertFalse(allowed(FEATHER.ScreenPage.LIVE_Z_OFFSET, "z.probe"))
        self.assertFalse(allowed(FEATHER.ScreenPage.CONTROL_MOVE,
                                 "z.zone.front_left"))
        self.assertFalse(allowed(FEATHER.ScreenPage.SETTINGS, "cal.confirm"))
        self.assertFalse(allowed(FEATHER.ScreenPage.SETTINGS, "settings.mod"))
        self.assertFalse(allowed(FEATHER.ScreenPage.SETTINGS, "settings.led"))
        self.assertFalse(allowed(FEATHER.ScreenPage.SETTINGS,
                                 "settings.led.minus"))
        self.assertFalse(allowed(FEATHER.ScreenPage.SETTINGS,
                                 "settings.led.plus"))
        self.assertFalse(allowed(FEATHER.ScreenPage.MOD_SETTINGS, "mod.item.12"))
        self.assertFalse(allowed(FEATHER.ScreenPage.PARAMETER_OPTIONS, "mod.item.12"))
        self.assertFalse(allowed(FEATHER.ScreenPage.PARAMETER_OPTIONS,
                                 "mod.option.2"))
        self.assertFalse(allowed(FEATHER.ScreenPage.MOD_VALUE, "mod.key.7"))
        self.assertFalse(allowed(
            FEATHER.ScreenPage.MOD_VALUE, "keyboard.key.hash"))
        self.assertTrue(allowed(
            FEATHER.ScreenPage.WIFI_PASSWORD, "keyboard.backspace"))
        self.assertFalse(allowed(
            FEATHER.ScreenPage.WIFI_SCAN, "net.reset.saved"))
        self.assertFalse(allowed(
            FEATHER.ScreenPage.MESSAGE, "net.reset.saved"))
        controller.message_actions = (
            ("message.ok", "CANCEL", "enabled"),
            ("net.reset.saved", "RESET PASSWORD", "warning"),
        )
        self.assertTrue(allowed(
            FEATHER.ScreenPage.MESSAGE, "net.reset.saved"))
        self.assertFalse(allowed(
            FEATHER.ScreenPage.MOD_SETTINGS, "keyboard.key.hash"))

        self.assertFalse(allowed(FEATHER.ScreenPage.MOD_SETTINGS, "mod.save"))
        self.assertTrue(allowed(
            FEATHER.ScreenPage.UPDATE_NOTIFICATION, "update.install"))
        self.assertTrue(allowed(
            FEATHER.ScreenPage.UPDATE_NOTIFICATION, "update.later"))
        self.assertTrue(allowed(
            FEATHER.ScreenPage.UPDATE_NOTIFICATION, "update.reset.confirm"))
        self.assertFalse(allowed(
            FEATHER.ScreenPage.IDLE_HOME, "update.install"))
        self.assertFalse(allowed(
            FEATHER.ScreenPage.IDLE_HOME, "update.reset.confirm"))

    def test_network_status_parser_is_bounded_to_public_fields(self):
        # Unknown keys must be silently dropped; only SNAPSHOT_KEYS survive.
        # SSIDs travel as base64 on the wire; STATE= is published explicitly.
        ssid = NETWORK_PROTOCOL.encode_field("Workshop")
        parsed = FEATHER.FeatherScreen.parse_network_status(
            "MODE=WIFI\nSTATE=CONNECTED\nSSID=%s\nSIGNAL=-54\nIP=192.168.2.10\nSECRET=no\n" % ssid)
        self.assertEqual(parsed, {"mode": "WIFI", "state": "CONNECTED",
                                  "ssid": "Workshop", "signal": "-54",
                                  "ip": "192.168.2.10", "reason": "",
                                  "progress": "", "attempt": ""})

    def test_material_values_are_normalized_for_persistence(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        normalize = controller._normalize_material
        self.assertEqual(normalize(None), "n/a")
        self.assertEqual(normalize("abs/pc"), "ABS-PC")
        self.assertEqual(normalize("custom"), "n/a")



    def test_visible_mod_parameters_fit_their_settings_row(self):
        # The screen truncates whatever does not fit a row, so a long label or
        # description silently loses its ending on the printer.  Render the real
        # declaration and measure every row against the width it is drawn with.
        declaration_path = pathlib.Path(__file__).parents[1] / "mod_params.json"
        manager = MOD_PARAMS.ModParamManagement.__new__(
            MOD_PARAMS.ModParamManagement)
        manager.declaration = str(declaration_path)
        manager.printer = type("Printer", (), {
            "command_error": staticmethod(RuntimeError)})()
        manager._load_declaration()

        def loaded_default(param):
            if issubclass(param.type, enum.Enum):
                return param.type[param.default].value
            if param.type is bool:
                return bool(param.default)
            return param.type(param.default)

        variables = dict(
            (param.key, loaded_default(param)) for param in manager.params)
        # Reveal every conditional parameter at once; hidden rows are never
        # drawn and would escape the measurement.
        variables.update({
            "display_eco": True, "weight_check": True,
            "bed_mesh_validation": True, "disable_cleaning": False,
            "use_swap": manager.params_map["use_swap"].type["ZRAM"].value,
        })
        feature = mod_controller(manager.params, variables)
        expected = MOD_UI.visible_parameters(feature.params)
        self.assertEqual(
            sorted(param.key for param in expected),
            sorted(param.key for param in manager.params if not param.hidden))

        with mock.patch.object(feature.renderer, "text",
                               wraps=feature.renderer.text) as text:
            feature._render_mod_settings()
            while True:
                page = feature.mod_page
                feature._handle_mod_action("mod.next")
                if feature.mod_page == page:
                    break

        drawn = {}
        for call in text.call_args_list:
            if not call.kwargs.get("truncate") or call.kwargs.get("wrap"):
                continue
            font = call.kwargs.get(
                "font", call.args[4] if len(call.args) > 4 else None)
            drawn.setdefault(str(call.args[2]), set()).add(
                (font, int(call.kwargs["max_width"])))

        self.assertTrue(expected)
        for param in expected:
            for value in (str(param.label).upper(), MOD_UI.description(param)):
                self.assertTrue(value, param.key)
                self.assertIn(value, drawn, param.key)
                for font, max_width in drawn[value]:
                    self.assertLessEqual(
                        FEATHER.FeatherRenderer.text_width(value, font),
                        max_width, "%s: %s" % (param.key, value))

    def test_mod_category_pages_keep_every_heading_with_its_rows(self):
        params = (
            [mod_param("a%d" % index, bool, False, "A %d" % index,
                       ui_category="alpha") for index in range(12)]
            + [mod_param("b0", bool, False, "B 0", ui_category="beta")]
            + [mod_param("g%d" % index, bool, False, "G %d" % index,
                         ui_category="gamma") for index in range(7)])
        manager = ModManager(params, {})
        capacity = MOD_UI.LIST_BOTTOM - MOD_UI.LIST_TOP

        pages = MOD_UI.category_pages(manager, params)

        self.assertTrue(pages)
        for sections in pages:
            self.assertTrue(sections)
            height = MOD_UI.page_height(sections)
            self.assertLessEqual(height, capacity)
            # Later bands are padded to a row pitch, so the rows of every page
            # stay on one grid and no page ends with a half-row gap.
            self.assertEqual((height - MOD_UI.band_pitch(0)) % MOD_UI.ITEM_PITCH,
                             0, [section.label for section in sections])
            for section in sections:
                # A heading with nothing under it is exactly what the pixel
                # pagination exists to prevent.
                self.assertTrue(section.items, section.label)

        self.assertEqual(
            [entry for sections in pages
             for entry in MOD_UI.page_parameters(sections)],
            list(enumerate(params)))

        # A category too long for one page repeats its heading, and every
        # repeat reports where its rows sit inside the whole category.
        for label, start in (("ALPHA", 0), ("BETA", 12), ("GAMMA", 13)):
            sections = [section for sections in pages for section in sections
                        if section.label == label]
            total = sum(len(section.items) for section in sections)
            self.assertEqual([section.continued for section in sections],
                             [False] + [True] * (len(sections) - 1))
            for section in sections:
                self.assertEqual(section.total, total)
                self.assertEqual(section.first,
                                 section.items[0][0] - start + 1)
        self.assertGreater(
            len([section for sections in pages for section in sections
                 if section.label == "ALPHA"]), 1)

        for number, sections in enumerate(pages):
            for _, param in MOD_UI.page_parameters(sections):
                self.assertEqual(
                    MOD_UI.page_of_parameter(pages, param.key), number)
        self.assertIsNone(MOD_UI.page_of_parameter(pages, "absent"))

        # A category that did not fit leaves a row-sized hole; that page names
        # what follows rather than ending in blank space.
        hints = [MOD_UI.next_category_hint(pages, number)
                 for number in range(len(pages))]
        self.assertTrue(any(hints), hints)
        for number, hint in enumerate(hints):
            if hint is None:
                continue
            self.assertGreaterEqual(
                capacity - MOD_UI.page_height(pages[number]),
                MOD_UI.ITEM_PITCH)
            self.assertEqual(hint, pages[number + 1][0].label)
        # The last page has nothing to announce, however much room is left.
        self.assertIsNone(MOD_UI.next_category_hint(pages, len(pages) - 1))

    def test_mod_value_validation_is_type_specific_and_bounded(self):
        integer = mod_param("count", int, 0, "Count")
        decimal = mod_param("offset", float, 0.0, "Offset")
        text = mod_param("name", str, "", "Name")
        self.assertEqual(MOD_UI.validate_value(integer, "-12"), -12)
        self.assertEqual(MOD_UI.validate_value(decimal, ".25"), 0.25)
        self.assertEqual(MOD_UI.validate_value(text, "hello world"),
                         "hello world")
        with self.assertRaisesRegex(ValueError, "whole number"):
            MOD_UI.validate_value(integer, "1.5")
        with self.assertRaisesRegex(ValueError, "printable ASCII"):
            MOD_UI.validate_value(text, "bad\nvalue")
        with self.assertRaisesRegex(ValueError, "too long"):
            MOD_UI.validate_value(text, "x" * 65)

    def test_mod_boolean_switch_uses_declared_semantic_labels(self):
        parameter = mod_param("disable_priming", bool, False, "Priming",
                              options=["left", "right"])
        self.assertEqual(MOD_UI.bool_labels(parameter),
                         tuple(label.upper() for label in parameter.options))
        parameter.options = None
        labels = MOD_UI.bool_labels(parameter)
        self.assertEqual(len(labels), 2)
        self.assertNotEqual(labels[0], labels[1])

    def test_mod_boolean_display_inversion_does_not_change_raw_value(self):
        normal = mod_param("normal", bool, False, "Normal")
        inverted = mod_param(
            "inverted", bool, False, "Inverted", ui_inverted=True)

        self.assertFalse(MOD_UI.bool_display_active(normal, False))
        self.assertTrue(MOD_UI.bool_display_active(normal, True))
        self.assertTrue(MOD_UI.bool_display_active(inverted, False))
        self.assertFalse(MOD_UI.bool_display_active(inverted, True))
        with self.assertRaisesRegex(TypeError, "not boolean"):
            MOD_UI.bool_display_active(
                mod_param("count", int, 0, "Count"), 0)

    def test_mod_declaration_loads_optional_ui_inversion(self):
        declaration = {
            "parameters": [
                {"key": "normal", "type": "bool", "default": 0,
                 "label": "Normal"},
                {"key": "inverted", "type": "bool", "default": 0,
                 "label": "Inverted", "ui": {"inverted": True}},
            ]
        }
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".json") as declaration_file:
            json.dump(declaration, declaration_file)
            declaration_file.flush()
            manager = MOD_PARAMS.ModParamManagement.__new__(
                MOD_PARAMS.ModParamManagement)
            manager.declaration = declaration_file.name
            manager.printer = type("Printer", (), {
                "command_error": staticmethod(RuntimeError)})()

            manager._load_declaration()

        params = {parameter.key: parameter for parameter in manager.params}
        self.assertFalse(params["normal"].ui_inverted)
        self.assertTrue(params["inverted"].ui_inverted)

    def test_unknown_mod_parameter_uses_the_fallback_category(self):
        declaration_path = pathlib.Path(__file__).parents[1] / "mod_params.json"
        declaration = json.loads(declaration_path.read_text(encoding="utf-8"))
        declaration["parameters"].append({
            "key": "future_parameter",
            "type": "bool",
            "default": 0,
            "label": "Future parameter",
            "description": "A parameter unknown to this category schema.",
        })
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".json") as declaration_file:
            json.dump(declaration, declaration_file)
            declaration_file.flush()
            future_manager = MOD_PARAMS.ModParamManagement.__new__(
                MOD_PARAMS.ModParamManagement)
            future_manager.declaration = declaration_file.name
            future_manager.printer = type("Printer", (), {
                "command_error": staticmethod(RuntimeError)})()
            future_manager._load_declaration()

        future = future_manager.params_map["future_parameter"]
        self.assertEqual(future.ui_category, "other")
        other = [param.key for param in future_manager.params
                 if param.ui_category == "other"]
        self.assertEqual(other, sorted(
            other, key=lambda key: (future_manager.params_map[key].order, key)))

    def test_mod_parameter_ui_order_contract(self):
        declaration = {
            "ui": {
                "categories": [
                    {"id": "other", "label": "OTHER", "order": 30,
                     "fallback": True, "parameters": []},
                    {"id": "second", "label": "SECOND", "order": 20,
                     "parameters": ["second_manual"]},
                    {"id": "first", "label": "FIRST", "order": 10,
                     "parameters": ["first_manual_b", "first_manual_a"]},
                ]
            },
            "parameters": [
                {"key": "fallback_z", "type": "bool", "default": 0,
                 "label": "Fallback Z", "order": 5},
                {"key": "first_unlisted_b", "type": "bool", "default": 0,
                 "label": "First unlisted B", "order": 10,
                 "ui": {"category": "first"}},
                {"key": "second_manual", "type": "bool", "default": 0,
                 "label": "Second manual", "order": 1},
                {"key": "first_manual_a", "type": "bool", "default": 0,
                 "label": "First manual A", "order": 1},
                {"key": "fallback_a", "type": "bool", "default": 0,
                 "label": "Fallback A", "order": 5},
                {"key": "first_unlisted_a", "type": "bool", "default": 0,
                 "label": "First unlisted A", "order": 10,
                 "ui": {"category": "first"}},
                {"key": "first_unlisted_early", "type": "bool", "default": 0,
                 "label": "First unlisted early", "order": 2,
                 "ui": {"category": "first"}},
                {"key": "fallback_no_order_b", "type": "bool", "default": 0,
                 "label": "Fallback no order B"},
                {"key": "first_manual_b", "type": "bool", "default": 0,
                 "label": "First manual B", "order": 999},
                {"key": "fallback_no_order_a", "type": "bool", "default": 0,
                 "label": "Fallback no order A"},
            ]
        }
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".json") as declaration_file:
            json.dump(declaration, declaration_file)
            declaration_file.flush()
            manager = MOD_PARAMS.ModParamManagement.__new__(
                MOD_PARAMS.ModParamManagement)
            manager.declaration = declaration_file.name
            manager.printer = type("Printer", (), {
                "command_error": staticmethod(RuntimeError)})()
            manager._load_declaration()

        self.assertEqual([category.id for category in manager.ui_categories],
                         ["first", "second", "other"])
        self.assertEqual([param.key for param in manager.params], [
            "first_manual_b",
            "first_manual_a",
            "first_unlisted_early",
            "first_unlisted_a",
            "first_unlisted_b",
            "second_manual",
            "fallback_no_order_a",
            "fallback_no_order_b",
            "fallback_a",
            "fallback_z",
        ])

    def test_all_strict_mod_visibility_dependencies(self):
        declaration_path = pathlib.Path(__file__).parents[1] / "mod_params.json"
        manager = MOD_PARAMS.ModParamManagement.__new__(
            MOD_PARAMS.ModParamManagement)
        manager.declaration = str(declaration_path)
        manager.printer = type("Printer", (), {
            "command_error": staticmethod(RuntimeError)})()
        manager._load_declaration()
        manager.variables = dict((param.key, param.default)
                                 for param in manager.params)
        cases = (
            ("mod_check_update_interval", "mod_check_update", False, True),
            ("backlight_eco", "display_eco", False, True),
            ("weight_check_max", "weight_check", False, True),
            ("bed_mesh_validation_clear", "bed_mesh_validation", False, True),
            ("bed_mesh_validation_tolerance", "bed_mesh_validation", False, True),
            ("load_zoffset_cleaning", "disable_cleaning", True, False),
            ("zram_algo", "use_swap", 1, 3),
        )
        for child, parent, hidden_value, visible_value in cases:
            with self.subTest(child=child):
                manager.variables[parent] = hidden_value
                self.assertNotIn(
                    child, [param.key for param in
                            MOD_UI.visible_parameters(manager)])
                manager.variables[parent] = visible_value
                self.assertIn(
                    child, [param.key for param in
                            MOD_UI.visible_parameters(manager)])

    def test_update_check_declaration_exposes_runtime_defaults_and_bounds(self):
        declaration_path = pathlib.Path(__file__).parents[1] / "mod_params.json"
        manager = MOD_PARAMS.ModParamManagement.__new__(
            MOD_PARAMS.ModParamManagement)
        manager.declaration = str(declaration_path)
        manager.printer = type("Printer", (), {
            "command_error": staticmethod(RuntimeError)})()
        manager._load_declaration()

        enabled = manager.params_map["mod_check_update"]
        interval = manager.params_map["mod_check_update_interval"]
        self.assertIs(enabled.type, bool)
        self.assertTrue(enabled.default)
        self.assertIs(interval.type, int)
        self.assertEqual(interval.default, 360)
        self.assertEqual(interval.minimum, 1)
        self.assertIsNone(interval.restart)
        self.assertEqual(enabled.ui_category, "system_services")
        self.assertEqual(interval.ui_category, "system_services")

    def test_renamed_parameter_carries_stored_values_to_the_current_key(self):
        declaration_path = pathlib.Path(__file__).parents[1] / "mod_params.json"

        def reload_variables(text):
            manager = MOD_PARAMS.ModParamManagement.__new__(
                MOD_PARAMS.ModParamManagement)
            manager.declaration = str(declaration_path)
            manager.printer = type("Printer", (), {
                "command_error": staticmethod(RuntimeError)})()
            manager._load_declaration()
            with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", suffix=".cfg") as variables_file:
                variables_file.write(text)
                variables_file.flush()
                manager.filename = variables_file.name
                # Each reload reports the migration decision it made; capture
                # that output instead of printing it.
                with self.assertLogs(level="INFO") as logs:
                    manager._reload()
            return manager, logs.output

        # A tuned M600 park height keeps its meaning under the new name.  The
        # rename carries every value, including one that equals the default, so
        # no height is silently reinterpreted as "never configured".
        configured, output = reload_variables("[Variables]\nm600_z_min = 75.0\n")
        self.assertEqual(configured.variables["pause_z_min"], 75.0)
        self.assertTrue(any("m600_z_min" in line and "pause_z_min" in line
                            for line in output), output)
        stock, _ = reload_variables("[Variables]\nm600_z_min = 50.0\n")
        self.assertEqual(stock.variables["pause_z_min"], 50.0)

        # The current key wins over a leftover, whichever order they appear in.
        for text in ("[Variables]\npause_z_min = 120.0\nm600_z_min = 75.0\n",
                     "[Variables]\nm600_z_min = 75.0\npause_z_min = 120.0\n"):
            with self.subTest(text=text):
                both, _ = reload_variables(text)
                self.assertEqual(both.variables["pause_z_min"], 120.0)

        # An enumerated rename stays strict: a value it cannot translate is
        # refused rather than carried into the new key.
        strict, _ = reload_variables("[Variables]\ndisplay_off = 7\n")
        self.assertEqual(strict.variables["display"],
                         strict.params_map["display"].type["FEATHER"].value)

    def test_mod_variables_without_enum_keys_load_defaults_without_errors(self):
        # A fresh install (or a key introduced by an update) has no stored value
        # for an enum parameter.  That is a normal state, not a parse failure.
        declaration_path = pathlib.Path(__file__).parents[1] / "mod_params.json"
        manager = MOD_PARAMS.ModParamManagement.__new__(
            MOD_PARAMS.ModParamManagement)
        manager.declaration = str(declaration_path)
        manager.printer = type("Printer", (), {
            "command_error": staticmethod(RuntimeError)})()
        manager._load_declaration()
        enums = [param for param in manager.params
                 if issubclass(param.type, enum.Enum)]
        self.assertTrue(enums)

        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".cfg") as variables_file:
            variables_file.write("[Variables]\n")
            variables_file.flush()
            manager.filename = variables_file.name

            with mock.patch.object(MOD_PARAMS.logging, "error") as logged:
                manager._reload()

            self.assertEqual(logged.call_args_list, [])
            self.assertEqual(
                {param.key: manager._transform(
                    param, manager.variables[param.key]) for param in enums},
                {param.key: param.default for param in enums})

            # An unknown member name in the file remains a real parse error: it
            # is reported once and the declared default is restored.
            broken = enums[0]
            variables_file.write("%s = %r\n" % (broken.key, "NOT_A_MEMBER"))
            variables_file.flush()

            with mock.patch.object(MOD_PARAMS.logging, "error") as logged:
                manager._reload()

            self.assertEqual(len(logged.call_args_list), 1)
            self.assertIn(broken.key, logged.call_args_list[0].args[0])
            self.assertEqual(
                manager._transform(broken, manager.variables[broken.key]),
                broken.default)

    def test_mod_parameter_writes_are_serialized_and_preserve_full_state(self):
        params = [
            MOD_PARAMS.Parameter("first", int, 0, "First"),
            MOD_PARAMS.Parameter("second", int, 0, "Second"),
        ]
        with tempfile.NamedTemporaryFile(suffix=".cfg") as variables_file:
            manager = MOD_PARAMS.ModParamManagement.__new__(
                MOD_PARAMS.ModParamManagement)
            manager.params = params
            manager.params_map = dict((param.key, param) for param in params)
            manager.migration_map = {}
            manager.variables = {"first": 0, "second": 0}
            manager.filename = variables_file.name
            manager._variables_lock = threading.RLock()
            manager.changes_gcode_present = False
            manager.reactor = Reactor()
            manager.printer = type("Printer", (), {
                "send_event": staticmethod(lambda _name: None)})()
            manager.gcode = type("GCode", (), {
                "error": staticmethod(RuntimeError)})()

            manager.set_value("first", "11")

            class SetCommand:
                values = {"PARAM": "second", "VALUE": "22"}

                def get(command, key, default=None):
                    return command.values.get(key, default)

                @staticmethod
                def error(message):
                    return RuntimeError(message)

                @staticmethod
                def respond_raw(message):
                    pass

            manager.cmd_SET_MOD_PARAM(SetCommand())
            manager._reload()

            self.assertEqual(manager.variables, {"first": 11, "second": 22})
            parser = configparser.ConfigParser()
            parser.read(variables_file.name)
            self.assertEqual(parser.getint("Variables", "first"), 11)
            self.assertEqual(parser.getint("Variables", "second"), 22)

            original_save = manager._save_all
            entered = []

            def reentrant_save():
                if not entered:
                    entered.append(True)
                    manager.set_value("second", "33")
                original_save()

            manager._save_all = reentrant_save
            manager.set_value("first", "44")
            manager._save_all = original_save
            manager._reload()
            self.assertEqual(manager.variables, {"first": 44, "second": 33})

    def test_concurrent_mod_parameter_writes_cannot_overwrite_newer_snapshot(self):
        params = [
            MOD_PARAMS.Parameter("first", int, 0, "First"),
            MOD_PARAMS.Parameter("second", int, 0, "Second"),
        ]
        manager = MOD_PARAMS.ModParamManagement.__new__(
            MOD_PARAMS.ModParamManagement)
        manager.params = params
        manager.params_map = dict((param.key, param) for param in params)
        manager.variables = {"first": 0, "second": 0}
        manager._variables_lock = threading.RLock()
        manager.changes_gcode_present = False
        manager.reactor = Reactor()
        manager.printer = type("Printer", (), {
            "send_event": staticmethod(lambda _name: None)})()
        first_snapshot_ready = threading.Event()
        release_first_snapshot = threading.Event()
        second_save_entered = threading.Event()
        persisted = {}
        failures = []

        def controlled_save():
            snapshot = dict(manager.variables)
            if threading.current_thread().name == "first-mod-writer":
                first_snapshot_ready.set()
                if not release_first_snapshot.wait(1.0):
                    raise RuntimeError("first writer was not released")
            else:
                second_save_entered.set()
            persisted.clear()
            persisted.update(snapshot)

        def write(key, value):
            try:
                manager.set_value(key, value)
            except Exception as exc:
                failures.append(exc)

        manager._save_all = controlled_save
        first = threading.Thread(
            target=write, args=("first", "11"), name="first-mod-writer")
        second = threading.Thread(
            target=write, args=("second", "22"), name="second-mod-writer")
        first.start()
        self.assertTrue(first_snapshot_ready.wait(1.0))
        second.start()
        self.assertFalse(second_save_entered.wait(0.1))
        release_first_snapshot.set()
        first.join(1.0)
        second.join(1.0)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(manager.variables, {"first": 11, "second": 22})
        self.assertEqual(persisted, {"first": 11, "second": 22})

    def test_failed_mod_parameter_write_rolls_back_without_notification(self):
        parameter = MOD_PARAMS.Parameter("value", int, 1, "Value")
        manager = MOD_PARAMS.ModParamManagement.__new__(
            MOD_PARAMS.ModParamManagement)
        manager.params = [parameter]
        manager.params_map = {parameter.key: parameter}
        manager.variables = {parameter.key: 1}
        manager._variables_lock = threading.RLock()
        manager.changes_gcode_present = True
        callbacks = []
        manager.reactor = type("Reactor", (), {
            "register_callback": callbacks.append})()
        manager._save_all = mock.Mock(side_effect=RuntimeError("write failed"))

        with self.assertRaisesRegex(RuntimeError, "write failed"):
            manager.set_value("value", "2")

        self.assertEqual(manager.variables, {"value": 1})
        self.assertEqual(callbacks, [])

    def test_mod_declaration_rejects_ui_inversion_for_non_boolean(self):
        declaration = {
            "parameters": [
                {"key": "count", "type": "int", "default": 0,
                 "label": "Count", "ui": {"inverted": False}},
            ]
        }
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".json") as declaration_file:
            json.dump(declaration, declaration_file)
            declaration_file.flush()
            manager = MOD_PARAMS.ModParamManagement.__new__(
                MOD_PARAMS.ModParamManagement)
            manager.declaration = declaration_file.name
            manager.printer = type("Printer", (), {
                "command_error": staticmethod(RuntimeError)})()

            with self.assertRaisesRegex(ValueError, "not boolean"):
                manager._load_declaration()

    def test_ui_inversion_does_not_affect_mod_parameter_storage(self):
        parameter = MOD_PARAMS.Parameter(
            key="disable_priming", type=bool, default=False,
            label="Nozzle priming", options=["YES", "NO"],
            ui_inverted=True)
        manager = MOD_PARAMS.ModParamManagement.__new__(
            MOD_PARAMS.ModParamManagement)
        manager.params_map = {parameter.key: parameter}
        manager.variables = {parameter.key: False}
        saved = []
        manager._save_all = lambda: saved.append(dict(manager.variables))
        manager.changes_gcode_present = False
        manager.reactor = Reactor()
        manager.printer = type("Printer", (), {
            "send_event": staticmethod(lambda _name: None)})()

        result = manager.set_value(parameter.key, "1")

        self.assertEqual(result, 1)
        self.assertTrue(manager.variables[parameter.key])
        self.assertEqual(saved, [{parameter.key: True}])
        self.assertFalse(manager._load_param(parameter, "0"))
        self.assertTrue(manager._load_param(parameter, "1"))
        self.assertEqual(manager._transform(parameter, False), 0)
        self.assertEqual(manager._transform(parameter, True), 1)
        self.assertEqual(manager._format_label(parameter, False),
                         "Nozzle priming: YES")
        self.assertEqual(manager._format_label(parameter, True),
                         "Nozzle priming: NO")

    def test_mod_params_public_setter_preserves_types_and_notifies(self):
        Display = enum.Enum("Display", {"FEATHER": 1, "GUPPY": 3})
        parameter = MOD_PARAMS.Parameter(
            key="display", type=Display, default=1, label="Display")
        manager = MOD_PARAMS.ModParamManagement.__new__(
            MOD_PARAMS.ModParamManagement)
        manager.params_map = {"display": parameter}
        manager.variables = {"display": 1}
        saved = []
        notified = []
        manager._save_all = lambda: saved.append(dict(manager.variables))
        manager.changes_gcode_present = True
        manager.reactor = Reactor()
        manager.printer = type("Printer", (), {
            "send_event": staticmethod(lambda _name: None)})()
        manager._notify_changed = lambda param: notified.append(param.key)

        result = manager.set_value("display", "GUPPY")

        self.assertEqual(result, "GUPPY")
        self.assertEqual(manager.variables["display"], 3)
        self.assertEqual(saved, [{"display": 3}])
        self.assertEqual(notified, ["display"])

    def test_mod_params_emits_post_commit_event_before_optional_change_gcode(self):
        parameter = MOD_PARAMS.Parameter("value", int, 1, "Value")
        manager = MOD_PARAMS.ModParamManagement.__new__(
            MOD_PARAMS.ModParamManagement)
        manager.params = [parameter]
        manager.params_map = {parameter.key: parameter}
        manager.variables = {parameter.key: 1}
        manager._variables_lock = threading.RLock()
        manager.changes_gcode_present = True
        manager.reactor = Reactor()
        order = []
        manager._save_all = lambda: order.append("persisted")
        manager.printer = type("Printer", (), {
            "send_event": lambda _self, name: order.append(name)})()
        manager._notify_changed = lambda _param: order.append("changes_gcode")

        manager.set_value("value", "2")
        manager.set_value("value", "2")

        self.assertEqual(order, [
            "persisted", "mod_params:changed", "changes_gcode"])

    def test_reload_emits_once_only_for_effective_snapshot_change(self):
        manager = MOD_PARAMS.ModParamManagement.__new__(
            MOD_PARAMS.ModParamManagement)
        manager.variables = {"value": 1}
        manager.reactor = DeferredReactor()
        events = []
        manager.printer = type("Printer", (), {
            "send_event": lambda _self, name: events.append(name)})()
        manager._reload = lambda: setattr(manager, "variables", {"value": 2})

        manager.cmd_RELOAD_MOD_PARAMS(None)
        self.assertEqual(events, [])
        manager.reactor.run_until(manager.reactor.now)
        self.assertEqual(events, ["mod_params:changed"])

        manager.cmd_RELOAD_MOD_PARAMS(None)
        manager.reactor.run_until(manager.reactor.now)
        self.assertEqual(events, ["mod_params:changed"])

        manager._reload = mock.Mock(side_effect=RuntimeError("reload failed"))
        with self.assertRaisesRegex(RuntimeError, "reload failed"):
            manager.cmd_RELOAD_MOD_PARAMS(None)
        manager.reactor.run_until(manager.reactor.now)
        self.assertEqual(events, ["mod_params:changed"])

    def test_screws_output_parser(self):
        parse = FEATHER.FeatherScreen.parse_screw_result
        self.assertEqual(parse("rear right : x=1, y=2, z=0.1 : adjust CCW 00:13"),
                         {"name": "rear right", "direction": "CCW", "turns": "00:13"})
        self.assertEqual(parse("front left (base) : x=1, y=2, z=0"),
                         {"name": "front left", "direction": "BASE", "turns": "-"})


class RendererStateTest(unittest.TestCase):
    def test_touch_warning_wraps_body_and_invalidates_cached_footer(self):
        renderer = FEATHER.FeatherRenderer()
        batches = []

        def capture(commands):
            if renderer.output_frozen:
                return False
            batches.append(commands)
            return True

        renderer.send = capture
        renderer.footer(
            "NOZZLE 21/220C | BED 24/60C", "192.168.2.4 | IDLE")
        commands = renderer.begin_page("Ready")
        commands += renderer.button(
            "ready.confirm", 220, 300, 360, 100, "CONTINUE")
        renderer.send(commands)

        renderer.touch_unavailable_modal()

        warning = "\n".join(batches[-1])
        self.assertIn("--max-height 108 --wrap --truncate", warning)
        self.assertFalse(renderer._footer_drawn)

        renderer.freeze_output()
        renderer.footer(
            "NOZZLE 22/220C | BED 24/60C", "192.168.2.4 | PRINTING")
        self.assertFalse(renderer._footer_drawn)
        renderer.thaw_output()
        restored = "\n".join(renderer.begin_page("Ready"))
        footer_clear = renderer.fill(
            0, UI.FOOTER_Y - 2, UI.SCREEN_WIDTH,
            UI.SCREEN_HEIGHT - (UI.FOOTER_Y - 2),
            UI.ThemeColor.BACKGROUND)
        self.assertIn(footer_clear, restored)
        self.assertIn("192.168.2.4 | PRINTING", restored)

    def test_touch_warning_requires_an_interactive_surface(self):
        renderer = FEATHER.FeatherRenderer()
        batches = []
        renderer.send = batches.append

        renderer.startup_modal("INITIALIZING KLIPPER", "PLEASE WAIT")
        self.assertFalse(renderer.touch_warning_allowed)

        renderer.set_header_action("emergency.stop", "STOP")
        commands = renderer.begin_page("Ready")
        commands += renderer.button(
            "ready.confirm", 220, 300, 360, 100, "CONTINUE")
        renderer.send(commands)
        self.assertTrue(renderer.touch_warning_allowed)

        renderer.touch_unavailable_modal()

        warning = "\n".join(batches[-1])
        self.assertIn("TOUCH INPUT UNAVAILABLE", warning)
        self.assertNotIn("emergency.stop", warning)
        self.assertFalse(renderer.touch_warning_allowed)

    def test_layout_primitives_compose_sections_metrics_and_grids(self):
        renderer = FEATHER.FeatherRenderer()

        commands = renderer.section_panel("Position", 10, 60, 200, 300)
        commands += renderer.metric_row(
            25, 120, 150, "X", "110.0", "mm")
        commands += renderer.dot_grid(30, 150, 100, 50, columns=3, rows=2)
        commands += renderer.corner_marks(20, 90, 160, 120)
        commands += renderer.joystick_knob(100, 220, "xy")
        commands += renderer.joystick_knob(140, 220, "z")
        drawing = "\n".join(commands)

        self.assertIn("POSITION", drawing)
        self.assertIn('"110.0"', drawing)
        self.assertIn('"mm"', drawing)
        self.assertIn("-p 30 150 -s 1 1", drawing)
        self.assertIn("-p 130 200 -s 1 1", drawing)
        self.assertIn("-p 20 90 -s 12 1", drawing)
        self.assertIn("-p 88 208 -s 25 25", drawing)
        self.assertIn("-p 128 208 -s 25 25", drawing)
        self.assertIn("-p 94 220 -s 13 1", drawing)
        self.assertIn("-p 134 217 -s 13 1", drawing)
        self.assertIn(
            "-p 95 250",
            renderer.text(100, 250, "-Y", font="JetBrainsMono 8pt",
                          h_align="center"))

    def test_dialog_composes_panel_text_and_modal_buttons(self):
        renderer = FEATHER.FeatherRenderer()

        commands = renderer.dialog(
            "Caution", ("FIRST LINE", "SECOND LINE"),
            (("dialog.close", "CLOSE", "enabled"),
             ("dialog.apply", "APPLY", "warning")),
            x=25, y=75, width=430, height=285)
        drawing = "\n".join(commands)

        self.assertEqual(
            commands[0], "--batch clear-hitboxes --layer base")
        self.assertIn("--batch fill -p 25 75 -s 430 285", drawing)
        self.assertIn("--batch stroke -p 25 75 -s 430 285", drawing)
        self.assertIn("CAUTION", drawing)
        self.assertIn("FIRST LINE", drawing)
        self.assertIn("--id 0:dialog.close", drawing)
        self.assertIn("--id 0:dialog.apply", drawing)
        self.assertEqual(
            set(renderer._buttons), {"dialog.close", "dialog.apply"})

    def test_dialog_supports_five_lines_below_title(self):
        renderer = FEATHER.FeatherRenderer()
        lines = tuple("LINE %d" % index for index in range(1, 7))

        drawing = "\n".join(renderer.dialog(
            "Notice", lines, (), x=80, y=85, width=640, height=325))

        for index in range(1, 6):
            self.assertIn("LINE %d" % index, drawing)
        self.assertNotIn("LINE 6", drawing)
        self.assertIn("-p 400 171", drawing)
        self.assertNotIn("-p 400 163", drawing)

    def test_hints_and_dialog_lines_keep_horizontal_padding(self):
        renderer = FEATHER.FeatherRenderer()
        long_text = "X" * 120

        hint = "\n".join(renderer.hint_box(
            long_text, 400, 397, max_width=740))
        dialog = "\n".join(renderer.dialog(
            "Notice", (long_text,), (), x=80, y=90, width=640, height=240))

        self.assertIn(long_text, hint)
        self.assertIn("--max-width 700 --truncate", hint)
        hint_panel = re.search(
            r"--batch fill -p (\d+) 397 -s (\d+) 44", hint)
        self.assertIsNotNone(hint_panel)
        self.assertGreaterEqual(int(hint_panel.group(1)), 30)
        self.assertLessEqual(int(hint_panel.group(2)), 740)
        self.assertIn(long_text, dialog)
        self.assertIn("--max-width 584 --truncate", dialog)

    def test_text_bounds_are_delegated_to_typer(self):
        renderer = FEATHER.FeatherRenderer()
        wrapped = renderer.text(
            400, 160, "one two three", max_width=584, max_height=66,
            wrap=True, truncate=True)
        truncated = renderer.text(
            400, 160, "one two three", max_width=584, truncate=True)

        self.assertIn(
            "--max-width 584 --max-height 66 --wrap --truncate", wrapped)
        self.assertIn("--max-width 584 --truncate", truncated)

    def test_startup_modal_draws_pulsing_circle_and_loading_text(self):
        renderer = FEATHER.FeatherRenderer()
        batches = []
        renderer.send = batches.append

        renderer.startup_modal("INITIALIZING KLIPPER", "STARTING", 0)
        renderer.startup_modal("INITIALIZING KLIPPER", "STARTING", 2)

        first = "\n".join(batches[0])
        expanded = "\n".join(batches[1])
        self.assertIn("-p 392 232 -s 17 1", first)
        self.assertIn("-p 384 232 -s 33 1", expanded)
        self.assertIn("--batch clear-hitboxes", first)

        pulse = "\n".join(renderer.startup_pulse(1))
        self.assertIn("-p 388 232 -s 25 1", pulse)
        self.assertNotIn("--batch clear-hitboxes", pulse)
        self.assertNotIn("-p 0 0 -s 800 480", pulse)

    def test_restart_startup_modal_cancels_late_toggle_animation_frames(self):
        renderer = FEATHER.FeatherRenderer()
        batches = []
        callbacks = []
        renderer.send = batches.append
        renderer.toggle("mod.item.0", 624, 101, 76, 38, False)
        renderer.animate_toggle(
            "mod.item.0", True,
            lambda callback, delay: callbacks.append((delay, callback)))

        page_generation = renderer.generation
        renderer.startup_modal(
            "INITIALIZING KLIPPER", "RESTARTING", 0, critical=True)
        loader_batch_count = len(batches)
        for delay, callback in callbacks:
            callback(100.0 + delay)

        self.assertEqual(renderer.generation, page_generation + 1)
        self.assertEqual(len(batches), loader_batch_count)

    def test_local_dialog_preserves_existing_controls_and_hitboxes(self):
        renderer = FEATHER.FeatherRenderer()
        renderer.button("outside", 10, 10, 100, 40, "OUTSIDE")

        commands = renderer.dialog(
            "Caution", ("LOCAL OVERLAY",),
            (("dialog.ok", "OK", "enabled"),),
            x=30, y=96, width=420, height=266, modal=False)
        drawing = "\n".join(commands)

        self.assertNotIn("--batch clear-hitboxes", drawing)
        self.assertEqual(
            set(renderer._buttons), {"outside", "dialog.ok"})

    def test_bundled_themes_live_with_the_versioned_ui_package(self):
        expected = MODULE_PATH.parent / "ui" / "themes"
        self.assertEqual(
            pathlib.Path(UI.THEME_DIRECTORY).resolve(), expected.resolve())
        self.assertTrue(expected.is_dir())

    def test_bundled_themes_apply_base_colors_and_context_roles(self):
        renderer = FEATHER.FeatherRenderer()
        self.assertTrue(
            {"DEFAULT", "DARK", "DESERT", "AMBER"}.issubset(
                renderer.theme_names()))
        renderer.set_theme("SYNTH")
        synth_data = json.loads(
            pathlib.Path(UI.THEME_DIRECTORY, "synth.json").read_text(
                encoding="utf-8"))
        _name, _description, synth = UI.validate_theme_data(synth_data)

        page = "\n".join(renderer.begin_page("Themes"))
        self.assertIn(
            "-c %s" % synth.resolve(UI.ThemeColor.BACKGROUND), page)
        self.assertIn(
            "-c %s" % synth.resolve(UI.ThemeColor.PRIMARY), page)

        button = "\n".join(renderer.button(
            "test", 10, 70, 180, 44, "BUTTON"))
        self.assertIn(
            "--background %s" %
            synth.resolve(UI.ThemeRole.BUTTON_BACKGROUND), button)
        self.assertIn(
            "--border %s" % synth.resolve(UI.ThemeRole.BUTTON_BORDER),
            button)
        selected = "\n".join(renderer.button(
            "selected", 200, 70, 180, 44, "SELECTED",
            state="selected"))
        self.assertIn(
            "--border %s" %
            synth.resolve(UI.ThemeRole.BUTTON_SELECTED_BORDER), selected)

        bed = renderer.text(
            10, 10, "BED", UI.ThemeRole.TEMPERATURE_BED)
        self.assertIn(
            "-c %s" % synth.resolve(UI.ThemeRole.TEMPERATURE_BED), bed)


    def test_user_theme_directory_can_add_and_override_themes(self):
        with tempfile.TemporaryDirectory() as user_directory:
            custom = {
                "schema_version": 2,
                "name": "CUSTOM_BLUE",
                "description": "User supplied blue",
                "colors": dict(UI.FALLBACK_THEME, primary="123abc"),
            }
            override = {
                "schema_version": 2,
                "name": "SYNTH",
                "description": "User override",
                "colors": dict(UI.FALLBACK_THEME, primary="abcdef"),
            }
            pathlib.Path(user_directory, "custom.json").write_text(
                json.dumps(custom), encoding="utf-8")
            pathlib.Path(user_directory, "override.json").write_text(
                json.dumps(override), encoding="utf-8")
            pathlib.Path(user_directory, "invalid.json").write_text(
                '{"schema_version": 2, "name": "BROKEN"}', encoding="utf-8")

            with self.assertLogs(level="WARNING") as logs:
                renderer = FEATHER.FeatherRenderer(
                    theme_directories=(UI.THEME_DIRECTORY, user_directory))
            self.assertIn("CUSTOM_BLUE", renderer.theme_names())
            self.assertIn("invalid theme", "\n".join(logs.output))
            renderer.set_theme("CUSTOM_BLUE")
            self.assertEqual(renderer.color(UI.ThemeColor.PRIMARY), "123abc")
            renderer.set_theme("SYNTH")
            self.assertEqual(renderer.color(UI.ThemeColor.PRIMARY), "abcdef")
            self.assertEqual(renderer.theme_description("SYNTH"),
                             "User override")

    def test_python_renderer_matches_cpp_protocol_fixture(self):
        fixture = pathlib.Path(__file__).parent / "fixtures" / "feather_draw_protocol.txt"
        renderer = FEATHER.FeatherRenderer()
        colors = dict(UI.FALLBACK_THEME)
        colors[UI.ThemeColor.BACKGROUND.value] = "a0b1c2"
        renderer._palette = UI.resolve_theme(colors)
        commands = [
            renderer.clear_hitboxes("base"),
            "--batch clear -c 000000",
            renderer.fill(10, 20, 30, 40, UI.ThemeColor.BACKGROUND),
            renderer.stroke(11, 21, 31, 41, UI.ThemeColor.SECONDARY_DARK, 3),
            renderer.text(
                100, 120, 'file "one" \\ Привет', UI.ThemeColor.BRIGHT, "Roboto 12pt",
                "center", "middle"),
            FEATHER.FeatherRenderer.hitbox("print.pause", 20, 315, 175, 100),
            "--batch flush",
            "--end",
        ]
        self.assertEqual(fixture.read_text(encoding="utf-8"),
                         "\n".join(commands) + "\n")

    def test_selected_and_danger_buttons_remain_clickable(self):
        renderer = FEATHER.FeatherRenderer()
        for state in ("enabled", "selected", "danger"):
            commands = renderer.button("tap", 0, 0, 10, 10, "A", state=state)
            self.assertTrue(any("--id " in command for command in commands), state)

    def test_plain_button_uses_one_composite_cpp_command(self):
        renderer = FEATHER.FeatherRenderer()
        renderer.begin_page("Home")
        commands = renderer.button("nav.files", 25, 90, 365, 125,
                                   "[PRINT FILES]", font="JetBrainsMono 16pt")
        self.assertEqual(len(commands), 1)
        self.assertIn("--batch button", commands[0])
        self.assertIn("--id 1:nav.files", commands[0])

    def test_action_hitbox_is_registered_for_runtime_introspection(self):
        renderer = FEATHER.FeatherRenderer()
        renderer.begin_page("Home")

        command = renderer.action_hitbox("nav.move", 10, 20, 30, 40)

        self.assertIn("--id 1:nav.move", command)
        self.assertEqual(renderer._hitboxes["nav.move"],
                         (10, 20, 30, 40, False))

    def test_composite_button_protects_leading_minus_from_argparse(self):
        renderer = FEATHER.FeatherRenderer()
        renderer.begin_page("Heat")
        commands = renderer.button("heat.minus", 20, 80, 100, 50, "-5")
        self.assertIn('-t " -5"', commands[0])
        self.assertIn("--id 1:heat.minus", commands[0])

    def test_row_button_keeps_independent_text_layout(self):
        renderer = FEATHER.FeatherRenderer()
        renderer.begin_page("Calibration")
        commands = renderer.button(
            "cal.screws", 30, 185, 740, 90, "BED SCREWS",
            subtitle="LEVEL BED USING ADJUSTMENT SCREWS", layout="row")
        self.assertGreater(len(commands), 1)
        self.assertFalse(any("--batch button" in command for command in commands))

        compact = renderer.button(
            "filament.unload", 320, 164, 460, 76, "02  UNLOAD",
            subtitle="RETRACT FILAMENT", layout="row")
        drawing = "\n".join(compact)
        self.assertIn("-p 535 202", drawing)
        self.assertIn("--max-width 187 --truncate", drawing)

    def test_center_button_omits_empty_subtitle_and_renders_lines_without_python_repr(self):
        renderer = FEATHER.FeatherRenderer()
        empty = renderer.button(
            "empty", 0, 0, 240, 80, "LABEL", subtitle=())
        self.assertEqual(len(empty), 1)
        self.assertIn("--batch button", empty[0])

        lines = renderer.button(
            "lines", 0, 0, 240, 100, "LABEL",
            subtitle=("FIRST LINE", "SECOND LINE"))
        rendered = "\n".join(lines)
        self.assertIn('"FIRST LINE"', rendered)
        self.assertIn('"SECOND LINE"', rendered)
        self.assertNotIn("('FIRST LINE'", rendered)

    def test_center_button_supports_readable_subtitle_style(self):
        renderer = FEATHER.FeatherRenderer()
        commands = renderer.button(
            "material", 0, 0, 230, 135, "ABS-PC",
            subtitle="NOZZLE 270C",
            subtitle_font="JetBrainsMono Bold 12pt",
            subtitle_color=UI.ThemeColor.TEXT)
        rendered = "\n".join(commands)

        self.assertIn('"NOZZLE 270C"', rendered)
        self.assertIn('-c d9e4e8', rendered)
        self.assertIn('-f "JetBrainsMono Bold 12pt"', rendered)

    def test_disabled_and_busy_buttons_have_no_hitbox(self):
        renderer = FEATHER.FeatherRenderer()
        for state in ("disabled", "busy"):
            commands = renderer.button("tap", 0, 0, 10, 10, "A", state=state)
            self.assertFalse(any("--id " in command for command in commands), state)

    def test_back_button_is_a_large_consistent_touch_target(self):
        renderer = FEATHER.FeatherRenderer()
        commands = renderer.begin_page("Menu", back=True)
        hitbox = next(command for command in commands
                      if "nav.back" in command and "--id " in command)
        self.assertIn("-s 146 46", hitbox)

    def test_button_text_bounds_are_delegated_to_typer(self):
        renderer = FEATHER.FeatherRenderer()
        renderer.begin_page("Audit")
        cases = (
            ("back", 146, "[< BACK]", "JetBrainsMono Bold 12pt"),
            ("step", 105, "0.1", "JetBrainsMono 12pt"),
            ("motors", 200, "MOTORS OFF", "JetBrainsMono 12pt"),
            ("brighter", 200, "BRIGHTER 5", "JetBrainsMono 12pt"),
            ("cancel", 260, "CANCEL PRINT", "Roboto Bold 16pt"),
            ("backspace", 165, "BACKSPACE", "Roboto Bold 12pt"),
        )
        for action, width, label, requested in cases:
            commands = renderer.button(
                action, 0, 60, width, 50, label, font=requested)
            self.assertIn(
                "--max-width %d --truncate" %
                (width - 2 * renderer.BUTTON_TEXT_PADDING), commands[0])

    def test_large_label_keeps_source_text_and_font_for_native_truncation(self):
        renderer = FEATHER.FeatherRenderer()
        renderer.begin_page("Cancel")
        renderer.button("cancel", 0, 60, 260, 100, "CANCEL PRINT",
                        font="Roboto Bold 16pt")
        self.assertEqual(renderer._buttons["cancel"][6],
                         "Roboto Bold 16pt")
        self.assertEqual(renderer._buttons["cancel"][4], "CANCEL PRINT")

    def test_footer_is_preserved_across_page_frames(self):
        renderer = FEATHER.FeatherRenderer()
        sent = []
        renderer.send = sent.append
        renderer.footer(
            "NOZZLE 21/220C | BED 24/60C", "192.168.2.4 | IDLE")

        first = renderer.begin_page("Control")
        second = renderer.begin_page("Settings")

        self.assertEqual(len(sent), 1)
        self.assertEqual(renderer._last_footer, (
            "NOZZLE 21/220C | BED 24/60C", "192.168.2.4 | IDLE"))
        self.assertIn("-s 800 442", "\n".join(first))
        self.assertNotIn("-s 784 472", "\n".join(first))
        self.assertIn("-s 784 439", "\n".join(first))

    def test_footer_fits_full_network_and_standby_status(self):
        renderer = FEATHER.FeatherRenderer()
        drawing = "\n".join(renderer._footer_commands(
            ("NOZZLE 250/250C | BED 32/0C",
             "192.168.2.124 | STANDBY")))

        self.assertIn('"192.168.2.124 | STANDBY"', drawing)
        self.assertIn("--max-width 340 --truncate", drawing)
        self.assertLessEqual(renderer.text_width(
            "192.168.2.124 | STANDBY", "JetBrainsMono 8pt"), 340)

    def test_theme_change_repaints_cached_footer(self):
        renderer = FEATHER.FeatherRenderer()
        renderer.send = lambda _commands: None
        renderer.footer(
            "NOZZLE 21/220C | BED 24/60C", "192.168.2.4 | IDLE")

        self.assertTrue(renderer.set_theme("SYNTH"))
        expected_primary = renderer.color(UI.ThemeColor.PRIMARY)
        page = "\n".join(renderer.begin_page("Settings"))
        self.assertIn("-c %s" % expected_primary, page)
        footer_clear = renderer.fill(
            0, UI.FOOTER_Y - 2, UI.SCREEN_WIDTH,
            UI.SCREEN_HEIGHT - (UI.FOOTER_Y - 2),
            UI.ThemeColor.BACKGROUND)
        self.assertIn(footer_clear, page)

        unchanged = "\n".join(renderer.begin_page("Settings"))
        self.assertNotIn(footer_clear, unchanged)

    def test_footer_repaint_removes_previous_fullscreen_overlay_pixels(self):
        renderer = FEATHER.FeatherRenderer()
        sent = []
        renderer.send = sent.append
        overlay = renderer.color(UI.ThemeColor.OVERLAY)
        renderer.startup_modal(
            "INITIALIZING KLIPPER", "INITIALIZING PRINTER SERVICES")

        colors = dict(UI.FALLBACK_THEME)
        colors["background"] = (
            "abcdef" if overlay != "abcdef" else "fedcba")
        renderer._palette = UI.resolve_theme(colors)
        background = renderer.color(UI.ThemeColor.BACKGROUND)
        renderer._footer_values = (
            "NOZZLE 21/220C | BED 24/60C", "192.168.2.4 | IDLE")
        renderer._footer_drawn = False
        page = renderer.begin_page("Settings")

        fill_pattern = re.compile(
            r"^--batch fill -p (\d+) (\d+) -s (\d+) (\d+) "
            r"-c ([0-9a-f]{6})$")
        top = UI.FOOTER_Y - 2
        pixels = [
            [None for _x in range(UI.SCREEN_WIDTH)]
            for _y in range(UI.SCREEN_HEIGHT - top)
        ]
        for command in list(sent[0]) + list(page):
            match = fill_pattern.match(command)
            if match is None:
                continue
            x, y, width, height = map(int, match.groups()[:4])
            color = match.group(5)
            left = max(0, x)
            right = min(UI.SCREEN_WIDTH, x + width)
            start_y = max(top, y)
            end_y = min(UI.SCREEN_HEIGHT, y + height)
            for screen_y in range(start_y, end_y):
                row = pixels[screen_y - top]
                row[left:right] = [color] * max(0, right - left)

        self.assertTrue(all(
            color is not None and color != overlay
            for row in pixels for color in row))
        for x, y in ((0, top), (UI.SCREEN_WIDTH - 1, top),
                     (0, UI.SCREEN_HEIGHT - 1),
                     (UI.SCREEN_WIDTH - 1, UI.SCREEN_HEIGHT - 1)):
            self.assertEqual(pixels[y - top][x], background)

    def test_mesh_matrix_validation_and_color_bands(self):
        normalize = FEATHER.FeatherScreen.normalize_mesh_matrix
        self.assertEqual(normalize([[0, "0.1"], [-0.2, 0.3]]),
                         [[0.0, 0.1], [-0.2, 0.3]])
        self.assertEqual(normalize([[0], [1, 2]]), [])
        self.assertEqual(normalize([0, 1]), [])
        color = FEATHER.FeatherScreen._mesh_color
        self.assertEqual(color(-0.2, -0.2, 0.3), UI.ThemeColor.PRIMARY_DARK)
        self.assertEqual(color(0.3, -0.2, 0.3), UI.ThemeColor.DANGER)

    def test_row_subtitle_has_space_after_long_calibration_label(self):
        commands = FEATHER.FeatherRenderer()._button_commands(
            "cal.screws", 30, 185, 740, 90, "BED SCREWS", "enabled",
            "JetBrainsMono 16pt", "LEVEL BED USING ADJUSTMENT SCREWS",
            True, "row")
        subtitle = next(command for command in commands
                        if "LEVEL BED" in command)
        self.assertIn("-p 315 ", subtitle)

    def test_page_generation_rejects_late_taps_without_input_delay(self):
        renderer = FEATHER.FeatherRenderer()
        first = renderer.begin_page("Home")
        first += renderer.button("nav.files", 0, 60, 100, 100, "FILES")
        wire_action = next(command.split("--id ", 1)[1].split(" ", 1)[0]
                           for command in first if "--id " in command)
        renderer.begin_page("Files", back=True)
        self.assertIsNone(renderer.decode_action(wire_action))
        self.assertEqual(renderer.decode_action("2:nav.back"), "nav.back")

    def test_page_background_wakes_without_covering_later_buttons(self):
        renderer = FEATHER.FeatherRenderer()

        commands = renderer.begin_page("Home")
        commands += renderer.button(
            "nav.files", 20, 60, 100, 100, "FILES")
        drawing = "\n".join(commands)

        wake = "--id 1:global.wake -p 0 0 -s 800 480"
        button = "--id 1:nav.files"
        self.assertIn(wake, drawing)
        self.assertIn(button, drawing)
        self.assertLess(drawing.index(wake), drawing.index(button))

    def test_send_only_publishes_without_io_process_or_wait(self):
        renderer = FEATHER.FeatherRenderer()
        with mock.patch("os.write") as write, \
                mock.patch("subprocess.Popen") as popen, \
                mock.patch("time.sleep") as sleep:
            accepted = renderer.send(["--batch clear -c 030607"])
        self.assertTrue(accepted)
        write.assert_not_called()
        popen.assert_not_called()
        sleep.assert_not_called()
        self.assertEqual(renderer.get_status()["queue_depth"], 1)

    def test_large_draw_is_split_into_bounded_complete_frames(self):
        commands = [
            "--batch text -p 10 %d -t %s" % (index, "x" * 90)
            for index in range(100)
        ]
        frames = FEATHER.FeatherRenderer._encode_frames(commands)
        self.assertGreater(len(frames), 1)
        self.assertTrue(all(len(frame) <= UI.MAX_ATOMIC_DRAW
                            for frame in frames))
        self.assertTrue(all(frame.endswith(b"--end\n") for frame in frames))
        self.assertTrue(all(b"--batch flush" not in frame
                            for frame in frames[:-1]))
        self.assertTrue(frames[-1].endswith(b"--batch flush\n--end\n"))
        self.assertEqual(sum(frame.count(b"--batch flush")
                             for frame in frames), 1)
        joined = b"\n".join(frames)
        for command in commands:
            self.assertIn(command.encode("utf-8"), joined)

    def test_small_draw_is_committed_once(self):
        frames = FEATHER.FeatherRenderer._encode_frames([
            "--batch fill -p 0 0 -s 10 10 -c 030607",
        ])

        self.assertEqual(len(frames), 1)
        self.assertTrue(frames[0].endswith(b"--batch flush\n--end\n"))
        self.assertEqual(frames[0].count(b"--batch flush"), 1)

    def test_frame_limit_counts_utf8_bytes(self):
        command = "--batch text -t " + ("Я" * 4100)

        with self.assertRaisesRegex(ValueError, "single Typer command"):
            FEATHER.FeatherRenderer._encode_frames([command])

    def test_toast_registers_dismiss_hitbox_in_overlay_layer(self):
        renderer = FEATHER.FeatherRenderer()
        renderer.begin_page("Home")
        sent = []
        renderer.send = sent.append

        renderer.toast("Saved")

        drawing = "\n".join(sent[-1])
        self.assertIn("--batch hitbox", drawing)
        self.assertIn(
            "--id 1:%s" % FEATHER.DismissToast().wire_id,
            drawing,
        )
        self.assertIn("--layer overlay", drawing)

    def test_replacing_toast_hides_old_surface_before_drawing_new_one(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.reactor = Reactor()
        controller.renderer = mock.Mock()
        controller.page = FEATHER.ScreenPage.IDLE_HOME
        controller.print_state = FEATHER.PrintState.IDLE
        controller.toast_until = 101.0
        controller.toast_message = "Old"
        controller._show_page = mock.Mock()

        controller._toast("New")

        controller._show_page.assert_called_once_with(FEATHER.ScreenPage.IDLE_HOME)
        controller.renderer.toast.assert_called_once_with("New")
        self.assertEqual(controller.toast_message, "New")
        self.assertEqual(controller.toast_until, 102.0)

    def test_toast_touch_action_dismisses_before_normal_routing(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller._hide_toast = mock.Mock()

        controller._handle_touch_action(FEATHER.DismissToast().wire_id)

        controller._hide_toast.assert_called_once_with()

    def test_toast_hitbox_can_be_cleared_without_page_redraw(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.renderer = mock.Mock()
        controller.toast_until = 101.0
        controller.toast_message = "Visible"

        self.assertTrue(controller._hide_toast(redraw=False))

        controller.renderer.clear_toast_hitbox.assert_called_once_with()
        self.assertEqual(controller.toast_until, 0.0)
        self.assertEqual(controller.toast_message, "")

    def test_keyed_animation_frames_are_latest_wins(self):
        renderer = FEATHER.FeatherRenderer()
        renderer.send_animation(["frame 1"], "loader")
        renderer.send_animation(["frame 2"], "loader")
        renderer.send_animation(["frame 3"], "loader")
        status = renderer.get_status()
        self.assertEqual(status["queue_depth"], 1)
        self.assertEqual(status["coalesced_batches"], 2)
        queued = renderer._batch_queue.get()
        self.assertEqual(queued.commands, ("frame 3",))

    def test_render_batch_serialized_size_is_bounded(self):
        renderer = FEATHER.FeatherRenderer()
        accepted = renderer.send(["x" * (UI.MAX_PENDING_DRAW + 1)])
        self.assertFalse(accepted)
        status = renderer.get_status()
        self.assertEqual(status["queue_depth"], 0)
        self.assertEqual(status["dropped_batches"], 1)

    def test_renderer_hands_off_touch_fd_before_closing_it(self):
        renderer = FEATHER.FeatherRenderer()
        events = []
        renderer.configure_worker(
            lambda callback: callback(0.0),
            lambda old, new: events.append(("handoff", old, new)))
        # The actual close ordering is exercised on the transport object: its
        # acknowledged reactor handoff precedes descriptor close.
        from ui.render_worker import TyperRenderWorker, RenderBatchQueue
        transport = TyperRenderWorker(
            RenderBatchQueue(), renderer._encode_frames, False,
            ("typer", "/tmp/draw", "/tmp/event", "/dev/input/touch"),
            lambda callback: callback(0.0),
            lambda old, new: events.append(("handoff", old, new)))
        transport.event_fd = 10
        transport.draw_fd = 11
        with mock.patch("os.close",
                        side_effect=lambda fd: events.append(("close", fd))):
            transport._close_transport()
        self.assertEqual(events, [
            ("handoff", 10, None), ("close", 10), ("close", 11)])

    def test_button_press_feedback_redraws_without_duplicate_hitbox(self):
        renderer = FEATHER.FeatherRenderer()
        sent = []
        renderer.send = sent.append
        renderer.button("nav.control", 20, 60, 200, 100, "CONTROL",
                        subtitle="Move and heat")
        self.assertTrue(renderer.flash_button("nav.control"))
        self.assertTrue(renderer.restore_button("nav.control"))
        self.assertEqual(len(sent), 2)
        self.assertFalse(any("--id " in command for batch in sent for command in batch))
        self.assertTrue(any("ffffff" in command for command in sent[0]))

    def test_arrow_button_uses_geometry_and_preserves_it_during_feedback(self):
        renderer = FEATHER.FeatherRenderer()
        up = renderer.arrow_button("mod.prev", 728, 88, 52, 48, "up")
        down = renderer.arrow_button(
            "mod.next", 728, 365, 52, 48, "down", state="disabled")
        self.assertFalse(any(" -t " in command for command in up + down))
        self.assertIn(renderer.fill(751, 111, 7, 12, UI.ThemeRole.BUTTON_TEXT), up)
        self.assertIn(renderer.fill(745, 390, 19, 2, UI.ThemeColor.DIM), down)
        self.assertTrue(any("--id 0:mod.prev" in command for command in up))
        self.assertFalse(any("--id " in command for command in down))

        sent = []
        renderer.send = sent.append
        self.assertTrue(renderer.flash_button("mod.prev"))
        self.assertTrue(renderer.restore_button("mod.prev"))
        self.assertEqual(len(sent), 2)
        self.assertFalse(any(" -t " in command
                             for batch in sent for command in batch))
        self.assertTrue(all(any("--batch fill" in command for command in batch)
                            for batch in sent))
        self.assertFalse(any("--id " in command
                             for batch in sent for command in batch))

    def test_footer_updates_only_when_values_change(self):
        renderer = FEATHER.FeatherRenderer()
        sent = []
        renderer.send = sent.append
        renderer.footer("NOZZLE 20/0C | BED 25/0C", "Offline | IDLE")
        renderer.footer("NOZZLE 20/0C | BED 25/0C", "Offline | IDLE")
        renderer.footer("NOZZLE 21/0C | BED 25/0C", "Offline | IDLE")
        self.assertEqual(len(sent), 2)

    def test_dynamic_list_and_keyboard_hitboxes_stay_between_chrome(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.renderer = FEATHER.FeatherRenderer()
        controller.renderer.send = lambda commands: None

        controller.file_entries = [
            {"name": "part-%d.gcode" % index, "directory": False}
            for index in range(5)]
        controller.file_page = 0
        controller._load_file_entries = lambda: None
        controller._render_file_browser()
        file_buttons = dict(controller.renderer._buttons)

        controller.networks = [
            {"ssid": "Workshop-%d" % index, "signal": -40 - index}
            for index in range(5)]
        controller.network_page = 0
        controller._render_wifi_scan()
        wifi_buttons = dict(controller.renderer._buttons)
        self.assertNotIn("net.reset.saved", wifi_buttons)

        controller.selected_network = {"ssid": "Workshop"}
        controller.password = "secret123"
        controller.password_cursor = len(controller.password)
        controller.password_visible = False
        controller.keyboard_symbols = False
        controller.keyboard_shift = False
        controller._render_keyboard()
        keyboard_buttons = dict(controller.renderer._buttons)

        for page_buttons in (file_buttons, wifi_buttons, keyboard_buttons):
            rectangles = []
            for action, spec in page_buttons.items():
                if action in ("nav.back", "file.refresh"):
                    continue
                rectangle = spec[:4]
                self.assertGreaterEqual(rectangle[1], UI.HEADER_BOTTOM + 1,
                                        action)
                self.assertLessEqual(rectangle[1] + rectangle[3],
                                     UI.CONTENT_BOTTOM, action)
                rectangles.append((action, rectangle))
            for index, (action, rectangle) in enumerate(rectangles):
                for other_action, other in rectangles[index + 1:]:
                    self.assertFalse(
                        UI.rectangles_overlap(rectangle, other),
                        "%s overlaps %s" % (action, other_action))

    def test_busy_notice_is_persistent_and_deduplicated(self):
        renderer = FEATHER.FeatherRenderer()
        self.assertTrue(renderer.set_theme("SYNTH"))
        header_background = renderer.color(UI.ThemeRole.HEADER_BACKGROUND)
        warning = renderer.color(UI.ThemeColor.WARNING)
        sent = []
        renderer.send = sent.append
        renderer.busy_notice("Klipper busy")
        renderer.busy_notice("Klipper busy")
        page = renderer.begin_page("Control")
        renderer.clear_busy_notice()
        self.assertEqual(len(sent), 2)
        self.assertIsNone(renderer._busy_label)
        notice = "\n".join(sent[0])
        page_drawing = "\n".join(page)
        cleared = "\n".join(sent[1])
        self.assertIn("-c %s" % header_background, notice)
        self.assertIn("-c %s" % warning, notice)
        self.assertIn("-c %s" % header_background, page_drawing)
        self.assertIn("-c %s" % warning, page_drawing)
        self.assertNotIn("stroke", cleared)
        self.assertIn("-c %s" % header_background, cleared)

    def test_busy_notice_replaces_menu_until_command_finishes(self):
        renderer = FEATHER.FeatherRenderer()
        sent = []
        renderer.send = sent.append
        renderer.busy_notice("Klipper busy")

        page = renderer.begin_page("Home")
        menu = renderer.button(
            "nav.menu", 648, 9, 132, 38, "MENU")

        self.assertTrue(page)
        self.assertEqual(menu, [])
        self.assertIn("nav.menu", renderer._buttons)

        renderer.clear_busy_notice()

        restored = "\n".join(sent[-1])
        self.assertIn("nav.menu", restored)

    def test_emergency_stop_has_priority_over_busy_notice_and_loader(self):
        renderer = FEATHER.FeatherRenderer()
        sent = []
        renderer.send = sent.append
        renderer.set_header_action("global.abort", "ABORT")
        page = renderer.begin_page("Printing", back=True)
        page_generation = renderer.generation
        sent_before_busy = len(sent)

        renderer.busy_notice("Klipper busy")
        renderer.loader("Moving", 0)
        renderer.clear_busy_notice()

        self.assertIn("global.abort", "\n".join(page))
        self.assertIn("nav.back", "\n".join(page))
        self.assertEqual(sent_before_busy + 1, len(sent))
        loader = "\n".join(sent[-1])
        self.assertEqual(renderer.generation, page_generation + 1)
        self.assertIn("global.abort", loader)
        self.assertNotIn("nav.back", loader)
        self.assertEqual(set(renderer._buttons), {"global.abort"})
        self.assertIsNone(renderer.decode_action(
            "%d:nav.back" % page_generation))

        renderer.loader("Moving", 1)
        self.assertEqual(renderer.generation, page_generation + 1)
        self.assertIn("global.abort", "\n".join(sent[-1]))

    def test_modal_dialog_preserves_emergency_stop_hitbox(self):
        renderer = FEATHER.FeatherRenderer()
        renderer.set_header_action("global.abort", "ABORT")
        commands = renderer.begin_page("Live Z")
        commands += renderer.dialog(
            "Warning", ("Check the first layer",),
            (("warning.ok", "OK", "warning"),))

        drawing = "\n".join(commands)
        self.assertEqual(drawing.count("--batch clear-hitboxes"), 4)
        self.assertEqual(drawing.count("--layer overlay"), 2)
        self.assertIn("warning.ok", renderer._buttons)
        self.assertIn("global.abort", renderer._buttons)
        self.assertGreater(
            drawing.rfind("global.abort"),
            drawing.rfind("clear-hitboxes"))
        self.assertIsNotNone(renderer._header_action)

    def test_primary_layouts_do_not_overlap_footer(self):
        footer = (0, UI.FOOTER_Y, UI.SCREEN_WIDTH, UI.FOOTER_HEIGHT)
        rectangles = [
            (35, 70, 350, 150), (415, 70, 350, 150),
            (35, 255, 350, 150), (415, 255, 350, 150),
            (20, 315, 175, 100), (215, 315, 175, 100),
            (410, 315, 175, 100), (605, 315, 175, 100),
            (25, 383, 750, 54),
        ]
        self.assertTrue(all(not UI.rectangles_overlap(rect, footer)
                            for rect in rectangles))

    def test_move_step_caption_clears_z_controls_and_screen_edge(self):
        caption_width = UI.FeatherRenderer.text_width(
            "JOG STEP (MM)", "JetBrainsMono 8pt")
        caption_left = 680 - caption_width // 2
        caption_right = caption_left + caption_width
        self.assertGreaterEqual(caption_left, 590)
        self.assertLessEqual(caption_right, UI.SCREEN_WIDTH - 24)

    def test_move_page_has_combined_homing_and_live_toolhead_status(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.reactor = Reactor()
        controller.jog_step = 1.0
        controller.toolhead = StatusObject({
            "position": (103.45, 67.89, 4.2, 0.0),
            "homed_axes": "xy",
        })
        controller._require_idle = lambda: None

        controller._render_move()
        drawing = "\n".join(command for batch in batches for command in batch)

        self.assertIn(MOVE_LAYOUT.Z_MINUS.wire_id,
                      controller.renderer._buttons)
        self.assertIn(MOVE_LAYOUT.Z_PLUS.wire_id,
                      controller.renderer._buttons)
        self.assertNotIn(MOVE_LAYOUT.HOME_Z.wire_id,
                         controller.renderer._buttons)
        self.assertIn("NOT HOMED: Z", drawing)
        self.assertIn("X  103.45   Y   67.89", drawing)
        self.assertIn("Z    4.20", drawing)

    def test_move_status_redraws_only_after_toolhead_changes(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.toolhead = StatusObject({
            "position": (1.0, 2.0, 10.0, 0.0),
            "homed_axes": "xyz",
        })
        controller._last_move = None

        controller._update_move_status(0)
        controller._update_move_status(1)
        controller.toolhead.status["position"] = (1.0, 2.0, 10.1, 0.0)
        controller._update_move_status(2)
        controller.toolhead.status["homed_axes"] = "xy"
        controller._update_move_status(3)

        self.assertEqual(len(batches), 3)
        position_update = "\n".join(batches[1])
        self.assertIn("Z   10.10", position_update)
        self.assertNotIn('-t "HOME"', position_update)
        self.assertNotIn("--batch clear-hitboxes", position_update)

        homing_update = "\n".join(batches[2])
        self.assertIn("NOT HOMED: Z", homing_update)
        self.assertIn('-t "HOME"', homing_update)
        self.assertNotIn("--batch clear-hitboxes", homing_update)

    def test_move_status_accepts_post_home_park_position(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.toolhead = StatusObject({
            "position": (120.0, 120.0, 230.0, 0.0),
            "homed_axes": "xyz",
        })
        controller._last_move = None

        controller._update_move_status(0)

        drawing = "\n".join(batches[0])
        self.assertIn("X  120.00   Y  120.00", drawing)
        self.assertIn("Z  230.00", drawing)

    def test_periodic_update_contains_any_ui_failure_and_recovers(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.print_state = FEATHER.PrintState.IDLE
        controller.page = FEATHER.ScreenPage.CONTROL_HEAT
        controller._update_cycle = mock.Mock(side_effect=(
            ValueError("bad heat telemetry"),
            ValueError("bad footer telemetry"),
            103.0,
        ))

        with mock.patch.object(FEATHER.logging, "exception") as logged:
            self.assertEqual(controller._update(100.0), 101.0)
            self.assertEqual(controller._update(101.0), 102.0)
            self.assertEqual(controller._update(102.0), 103.0)

        logged.assert_called_once_with(
            "[feather_screen] periodic update failed page=%s failures=%d",
            "CONTROL_HEAT", 1)
        self.assertEqual(controller._update_failures, 0)

    def test_destroyed_periodic_update_parks_the_direct_reactor_timer(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.print_state = FEATHER.PrintState.DESTROYED
        controller.reactor = type("Reactor", (), {"NEVER": 12345.0})()

        self.assertEqual(controller._update(100.0), 12345.0)

    def test_joystick_move_page_registers_two_continuous_regions(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.renderer = FEATHER.FeatherRenderer()
        controller.renderer.send = lambda commands: None
        controller.reactor = Reactor()
        controller.move_mode = "joystick"
        controller.joystick = type("Planner", (), {
            "xy_speed": 600.0, "z_speed": 25.0})()
        controller.toolhead = StatusObject({
            "position": (1.0, 2.0, 10.0, 0.0), "homed_axes": "xyz"})
        controller._require_idle = lambda: None

        controller._render_move()

        self.assertIn("navigate.move.step", controller.renderer._buttons)
        self.assertIn(MOVE_LAYOUT.HOME_Z.wire_id,
                      controller.renderer._buttons)
        self.assertEqual(
            set(controller.renderer._hitboxes) - {"global.wake"},
            {MOVE_LAYOUT.JOYSTICK_XY.wire_id,
             MOVE_LAYOUT.JOYSTICK_Z.wire_id})
        for action in (
                MOVE_LAYOUT.JOYSTICK_XY.wire_id,
                MOVE_LAYOUT.JOYSTICK_Z.wire_id):
            self.assertTrue(controller.renderer._hitboxes[action][4])

    def test_low_z_move_page_always_warns_and_reports_auto_profile_state(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.reactor = Reactor()
        controller.move_mode = "joystick"
        controller.move_caution_acknowledged = False
        controller.params = type("Params", (), {
            "variables": {"safe_z": 12.0}})()
        controller.joystick = type("Planner", (), {
            "xy_speed": 600.0, "z_speed": 25.0})()
        controller.toolhead = StatusObject({
            "position": (1.0, 2.0, 5.99, 0.0), "homed_axes": "xyz"})
        controller.bed_mesh = StatusObject({
            "profile_name": "", "profiles": {"auto": {"points": []}}})
        controller._require_idle = lambda: None

        controller._render_move()
        warning = "\n".join(batches[-1])

        self.assertEqual(warning.count("--batch clear-hitboxes"), 2)
        self.assertEqual(warning.count("--layer overlay"), 1)
        dismiss_id = UI.SetValue(
            MOVE_LAYOUT.MoveState.CAUTION_ACKNOWLEDGED, True).wire_id
        self.assertIn("--id 1:%s" % dismiss_id, warning)
        self.assertIn("--id 1:move.caution.auto", warning)
        self.assertIn("--id 1:move.joy.z", warning)
        self.assertIn("--id 1:move.joy.xy", warning)

        controller.bed_mesh.status["profile_name"] = "auto"
        controller._render_move()
        safe = "\n".join(batches[-1])

        self.assertEqual(safe.count("--batch clear-hitboxes"), 2)
        self.assertEqual(safe.count("--layer overlay"), 1)
        self.assertIn("--id 2:move.caution.unload", safe)
        self.assertIn("--id 2:%s" % dismiss_id, safe)
        self.assertIn("--id 2:move.homez", safe)
        self.assertIn("--id 2:move.joy.z", safe)
        self.assertIn("--id 2:move.joy.xy", safe)
        self.assertFalse(controller.move_caution_acknowledged)

    def test_joystick_feedback_tracks_cursor_and_position_in_realtime(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.page = FEATHER.ScreenPage.CONTROL_MOVE
        controller.move_mode = "joystick"
        controller.toolhead = StatusObject({
            "position": (1.0, 2.0, 10.0, 0.0), "homed_axes": "xyz"})
        controller._last_move = (1.0, 2.0, 10.0, "HOMED: XYZ", True, True)
        cursor = (320, 180)
        controller.joystick_cursor = (
            MOVE_LAYOUT.JOYSTICK_XY.wire_id, cursor[0], cursor[1])
        controller.joystick_feedback_at = 0.0
        controller.joystick = type("Planner", (), {
            "inertia": lambda self: {
                "velocity": (42.0, -18.0, 1.5),
                "xy_speed": 45.7,
                "z_speed": 1.5,
                "acceleration_magnitude": 850.0,
            },
        })()

        controller._update_joystick_feedback(
            1.0, position=(2.0, 3.0, 11.0))

        live = "\n".join(batches[-1])
        knob_size = MOVE_LAYOUT.JOYSTICK_PAGE.node("xy.knob").size
        self.assertIn(
            "--batch stroke -p %d %d -s %d %d" % (
                cursor[0] - knob_size // 2,
                cursor[1] - knob_size // 2,
                knob_size, knob_size),
            live)
        for value in ('"   2.0"', '"   3.0"', '"  11.0"', '" 45.7"'):
            self.assertIn(value, live)
        self.assertNotIn('"VX"', live)
        self.assertNotIn('"VY"', live)
        self.assertNotIn('"VZ', live)

        controller.joystick_cursor = None
        controller._update_joystick_feedback(1.1, force=True)
        released = "\n".join(batches[-1])
        center = MOVE_LAYOUT.JOYSTICK_PAGE.rect("xy.pad").center
        dirty_padding = 2
        self.assertIn(
            "--batch fill -p %d %d -s %d %d" % (
                cursor[0] - knob_size // 2 - dirty_padding,
                cursor[1] - knob_size // 2 - dirty_padding,
                knob_size + dirty_padding * 2,
                knob_size + dirty_padding * 2),
            released)
        self.assertIn(
            "--batch stroke -p %d %d -s %d %d" % (
                center[0] - knob_size // 2,
                center[1] - knob_size // 2,
                knob_size, knob_size),
            released)

    def test_joystick_feedback_uses_fallback_clock_without_reactor(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.page = FEATHER.ScreenPage.CONTROL_MOVE
        controller.move_mode = "joystick"
        controller.toolhead = StatusObject({
            "position": (1.0, 2.0, 10.0, 0.0), "homed_axes": "xyz"})
        controller._last_move = None
        controller.joystick_cursor = None
        controller.joystick_feedback_at = 0.0
        controller.joystick = type("Planner", (), {
            "inertia": lambda self: {"velocity": (0.0, 0.0, 0.0)},
        })()

        feedback_durations = []
        controller.joystick_stream = type("Stream", (), {
            "active": True,
            "record_feedback": (
                lambda self, duration: feedback_durations.append(duration)),
        })()

        controller._update_joystick_feedback(
            1.0, position=(2.0, 3.0, 11.0), force=True)

        self.assertTrue(batches)
        self.assertEqual(len(feedback_durations), 1)
        self.assertGreaterEqual(feedback_durations[0], 0.0)

    def test_joystick_knob_dirty_region_stays_inside_static_artwork(self):
        renderer = FEATHER.FeatherRenderer()
        snapshot = (1.0, 2.0, 10.0, "HOMED: XYZ", True, True)
        page = MOVE_LAYOUT.JOYSTICK_PAGE
        xy_pad = page.rect("xy.pad")
        z_hitbox = page.rect("z.hitbox")

        for raw_x, raw_y in ((-100, -100), (900, 900)):
            MOVE_LAYOUT.render_joystick(
                renderer, joystick_values(snapshot))
            drawing = "\n".join(MOVE_LAYOUT.update_joystick(
                renderer, joystick_values(
                    snapshot, cursor=(MOVE_LAYOUT.JOYSTICK_XY.wire_id, raw_x, raw_y))))
            strokes = [line for line in drawing.splitlines()
                       if "--batch stroke" in line and "-s 25 25" in line]
            self.assertEqual(len(strokes), 1)
            match = re.search(r"-p (\d+) (\d+) -s 25 25", strokes[0])
            left, top = int(match.group(1)), int(match.group(2))
            self.assertGreaterEqual(left - 2, xy_pad.x)
            self.assertLessEqual(left + 27, xy_pad.right)
            self.assertGreaterEqual(top - 2, xy_pad.y)
            self.assertLessEqual(top + 27, xy_pad.bottom)

        for raw_y in (-100, 900):
            MOVE_LAYOUT.render_joystick(
                renderer, joystick_values(snapshot))
            drawing = "\n".join(MOVE_LAYOUT.update_joystick(
                renderer, joystick_values(
                    snapshot, cursor=(MOVE_LAYOUT.JOYSTICK_Z.wire_id, 0, raw_y))))
            strokes = [line for line in drawing.splitlines()
                       if "--batch stroke" in line and "-s 25 25" in line]
            self.assertEqual(len(strokes), 1)
            match = re.search(r"-p (\d+) (\d+) -s 25 25", strokes[0])
            top = int(match.group(2))
            self.assertGreaterEqual(top - 2, z_hitbox.y)
            self.assertLessEqual(top + 27, z_hitbox.bottom)

    def test_joystick_knob_move_clears_previous_position_without_ghost(self):
        renderer = FEATHER.FeatherRenderer()
        snapshot = (1.0, 2.0, 10.0, "HOMED: XYZ", True, True)
        old_cursor = (240, 229)
        new_cursor = (320, 180)
        MOVE_LAYOUT.render_joystick(
            renderer, joystick_values(
                snapshot, cursor=(
                    MOVE_LAYOUT.JOYSTICK_XY.wire_id, *old_cursor)))

        commands = MOVE_LAYOUT.update_joystick(
            renderer, joystick_values(
                snapshot, cursor=(
                    MOVE_LAYOUT.JOYSTICK_XY.wire_id, *new_cursor)))
        drawing = "\n".join(commands)
        knob_size = MOVE_LAYOUT.JOYSTICK_PAGE.node("xy.knob").size
        dirty_padding = 2

        self.assertIn(
            "--batch fill -p %d %d -s %d %d" % (
                old_cursor[0] - knob_size // 2 - dirty_padding,
                old_cursor[1] - knob_size // 2 - dirty_padding,
                knob_size + dirty_padding * 2,
                knob_size + dirty_padding * 2),
            drawing)
        self.assertIn(
            "--batch stroke -p %d %d -s %d %d" % (
                new_cursor[0] - knob_size // 2,
                new_cursor[1] - knob_size // 2,
                knob_size, knob_size),
            drawing)
        self.assertNotIn(
            "--batch stroke -p %d %d -s %d %d" % (
                old_cursor[0] - knob_size // 2,
                old_cursor[1] - knob_size // 2,
                knob_size, knob_size),
            drawing)
        self.assertFalse(hasattr(
            FEATHER.FeatherScreen, "_joystick_indicator_commands"))


    def test_joystick_refill_resamples_monotonic_time_for_each_segment(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.page = FEATHER.ScreenPage.CONTROL_MOVE
        controller.move_mode = "joystick"
        controller.print_state = FEATHER.PrintState.IDLE
        controller.joystick_action = MOVE_LAYOUT.JOYSTICK_XY.wire_id
        controller.joystick_busy_since = None
        controller.joystick_queued = False

        class TickReactor:
            NEVER = 9999.0

            def __init__(self):
                self.now = 10.0

            def monotonic(self):
                self.now += 0.001
                return self.now

        class Planner:
            held = True

            @staticmethod
            def watchdog(eventtime):
                return False

            @staticmethod
            def motion_active():
                return True

            @staticmethod
            def advance(position, period):
                next_position = list(position)
                next_position[0] += 0.01
                return type("Segment", (), {
                    "position": next_position,
                    "speed": 1.0,
                    "acceleration": 1.0,
                })()

        class Stream:
            active = True

            def __init__(self):
                self.last_processed = 0.2
                self.last_ahead = 0.2
                self.wants_times = []
                self.queued = []
                self.motion_cycles = []

            @staticmethod
            def set_motion_active(active, eventtime):
                pass

            def ahead(self, eventtime):
                self.last_processed = 0.2
                self.last_ahead = 0.2
                return self.last_ahead

            def wants_segment(self, eventtime):
                self.wants_times.append(eventtime)
                return len(self.wants_times) <= 2

            def queue_segment(self, segment):
                self.queued.append(segment)

            @staticmethod
            def record_refill(duration, segment_count):
                pass

            def record_motion_cycle(self, eventtime, active,
                                    processed_before, ahead_before,
                                    processed_after, ahead_after):
                self.motion_cycles.append((
                    eventtime, active, processed_before, ahead_before,
                    processed_after, ahead_after))

        controller.reactor = TickReactor()
        controller.joystick = Planner()
        controller.toolhead = type("Toolhead", (), {
            "get_status": lambda self, eventtime: {"homed_axes": "xyz"},
            "get_position": lambda self: [0.0, 0.0, 0.0, 0.0],
        })()
        stream = Stream()
        controller.joystick_stream = stream
        controller._update_joystick_feedback = lambda *args, **kwargs: None

        result = controller._joystick_tick(10.0)

        self.assertEqual(result, 10.010)
        self.assertEqual(len(stream.queued), 2)
        self.assertGreaterEqual(len(stream.wants_times), 3)
        self.assertTrue(all(
            later > earlier for earlier, later in zip(
                stream.wants_times, stream.wants_times[1:])))
        self.assertTrue(all(value > 10.0 for value in stream.wants_times))
        self.assertEqual(len(stream.motion_cycles), 1)
        self.assertTrue(stream.motion_cycles[0][1])

    def test_joystick_tick_forces_final_zero_inertia_frame(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.page = FEATHER.ScreenPage.CONTROL_MOVE
        controller.move_mode = "joystick"
        controller.print_state = FEATHER.PrintState.IDLE
        controller.joystick_action = None
        controller.joystick_busy_since = None
        controller.joystick_queued = True
        controller.joystick_timer_active = True
        controller.reactor = type("TimerReactor", (), {
            "NEVER": 9999.0,
            "monotonic": lambda self: 10.001,
        })()
        controller.toolhead = type("Toolhead", (), {
            "get_status": lambda self, eventtime: {"homed_axes": "xyz"},
            "get_position": lambda self: (0.0, 0.0, 10.0),
        })()

        class SettledPlanner:
            held = False

            @staticmethod
            def watchdog(eventtime):
                return False

            @staticmethod
            def motion_active():
                return False

            @staticmethod
            def advance(position, period):
                return None

        class ActiveStream:
            active = True

            def __init__(self):
                self.finished = False
                self.last_processed = 0.0
                self.last_ahead = 0.0

            @staticmethod
            def set_motion_active(active, eventtime):
                pass

            def ahead(self, eventtime):
                self.last_processed = 0.0
                self.last_ahead = 0.0
                return 0.0

            @staticmethod
            def wants_segment(eventtime):
                return True

            @staticmethod
            def record_refill(duration, segment_count):
                pass

            @staticmethod
            def record_motion_cycle(eventtime, active, processed_before,
                                    ahead_before, processed_after, ahead_after):
                pass

            def finish(self):
                self.finished = True

        stream = ActiveStream()
        controller.joystick = SettledPlanner()
        controller._get_joystick_stream = lambda: stream
        feedback = []
        controller._update_joystick_feedback = (
            lambda eventtime, position=None, force=False:
            feedback.append((eventtime, position, force)))

        result = controller._joystick_tick(10.0)

        self.assertEqual(result, controller.reactor.NEVER)
        self.assertTrue(stream.finished)
        self.assertEqual(feedback[-1], (10.0, None, True))

    def test_low_z_queued_position_is_used_for_immediate_caution(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.page = FEATHER.ScreenPage.CONTROL_MOVE
        controller.move_mode = "joystick"
        controller.move_caution_signature = (False, None)
        controller.move_caution_acknowledged = False
        controller.toolhead = StatusObject({
            "position": (0.0, 0.0, 10.0, 0.0), "homed_axes": "xyz"})
        controller.bed_mesh = StatusObject({
            "profile_name": "auto", "profiles": {"auto": {"points": []}}})
        stopped = []
        rendered = []
        controller._stop_joystick = lambda: stopped.append(True)
        controller._render_move = (
            lambda snapshot=None, caution=None:
            rendered.append((snapshot, caution)))

        safe_values = MOVE_LAYOUT.snapshot_values(
            (0.0, 0.0, 10.0, "HOMED: XYZ", True, True))
        safe_values[MOVE_LAYOUT.MoveState.CAUTION_ACKNOWLEDGED] = False
        safe_values[MOVE_LAYOUT.MoveState.AUTO_PROFILE_STATE] = "active"
        safe_values[MOVE_LAYOUT.MoveState.INERTIA] = 0.0
        safe_values[MOVE_LAYOUT.MoveState.CURSOR] = None
        MOVE_LAYOUT.render_joystick(controller.renderer, safe_values)

        controller._update_joystick_feedback(
            1.0, position=(1.0, 2.0, 4.9), force=True)

        self.assertEqual(stopped, [])
        self.assertEqual(rendered, [])
        self.assertEqual(controller.move_caution_signature, (True, "active"))
        self.assertNotIn("--batch clear-hitboxes", "\n".join(batches[0]))

    def test_low_z_overlay_keeps_z_feedback_live(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.page = FEATHER.ScreenPage.CONTROL_MOVE
        controller.move_mode = "joystick"
        controller.move_caution_signature = (True, "active")
        controller.move_caution_acknowledged = False
        controller.toolhead = StatusObject({
            "position": (1.0, 2.0, 4.8, 0.0), "homed_axes": "xyz"})
        controller.bed_mesh = StatusObject({
            "profile_name": "auto", "profiles": {"auto": {"points": []}}})
        controller._last_move = (
            1.0, 2.0, 4.9, "HOMED: XYZ", True, True)
        controller.joystick_cursor = (MOVE_LAYOUT.JOYSTICK_Z.wire_id, 510, 150)
        controller.joystick_feedback_at = 0.0
        controller.joystick = type("Planner", (), {
            "inertia": lambda self: {"velocity": (0.0, 0.0, -2.0)},
        })()

        controller._update_joystick_feedback(
            1.0, position=(1.0, 2.0, 4.8), force=True)

        drawing = "\n".join(batches[-1])
        knob_size = MOVE_LAYOUT.JOYSTICK_PAGE.node("z.knob").size
        knob_left = FEATHER.JOYSTICK_Z_CENTER[0] - knob_size // 2
        knob_top = 150 - knob_size // 2
        self.assertIn(
            "--batch stroke -p %d %d -s 25 25" %
            (knob_left, knob_top), drawing)
        self.assertIn('"  2.0"', drawing)
        self.assertIn('"   4.8"', drawing)

    def test_step_mode_caution_uses_same_overlay_geometry_as_joystick(self):
        renderer = FEATHER.FeatherRenderer()
        safe_values = MOVE_LAYOUT.snapshot_values(
            (0.0, 0.0, 10.0, "HOMED: XYZ", True, True))
        safe_values[MOVE_LAYOUT.MoveState.JOG_STEP] = 1.0
        safe_values[MOVE_LAYOUT.MoveState.CAUTION_ACKNOWLEDGED] = False
        safe_values[MOVE_LAYOUT.MoveState.AUTO_PROFILE_STATE] = "available"
        MOVE_LAYOUT.render_step(renderer, safe_values)

        warning_values = MOVE_LAYOUT.snapshot_values(
            (0.0, 0.0, 4.9, "HOMED: XYZ", True, True))
        warning_values[MOVE_LAYOUT.MoveState.JOG_STEP] = 1.0
        warning_values[MOVE_LAYOUT.MoveState.CAUTION_ACKNOWLEDGED] = False
        warning_values[MOVE_LAYOUT.MoveState.AUTO_PROFILE_STATE] = "available"
        commands = MOVE_LAYOUT.render_step_status(
            renderer, warning_values, axes=True)
        drawing = "\n".join(commands)
        overlay = MOVE_LAYOUT.JOYSTICK_PAGE.rect("xy.pad")

        self.assertIn(
            "-p %d %d -s %d %d" % overlay.as_tuple(), drawing)
        self.assertNotIn("--batch clear-hitboxes", drawing)
