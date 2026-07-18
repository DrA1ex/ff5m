## Tests for Feather screen behavior.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import importlib.util
import enum
import errno
import json
import pathlib
import re
import tempfile
import unittest
from unittest import mock


MODULE_PATH = (pathlib.Path(__file__).parents[1] / ".py" / "klipper" /
               "plugins" / "feather_screen.py")
SPEC = importlib.util.spec_from_file_location("feather_screen", MODULE_PATH)
FEATHER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FEATHER)
UI = __import__("feather_ui")
MOD_UI = __import__("feather_mod_settings")
PAGES = __import__("feather_screen_pages")

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
              options=None, readonly=False, hidden=False):
    return type("Param", (), {
        "key": key, "type": param_type, "default": default,
        "label": label, "description": description, "options": options,
        "readonly": readonly, "hidden": hidden, "warning": None,
    })()


def mod_controller(params, variables):
    controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
    controller.renderer = FEATHER.FeatherRenderer()
    controller.draw_batches = []
    controller.renderer.send = controller.draw_batches.append
    controller.params = ModManager(params, variables)
    controller.reactor = Reactor()
    controller.print_stats = StatusObject({"state": "standby"})
    controller.virtual_sdcard = type("SD", (), {"is_active": lambda self: False})()
    controller.print_state = FEATHER.PrintState.IDLE
    controller.page = FEATHER.Page.MOD_SETTINGS
    controller.previous_page = FEATHER.Page.SETTINGS
    controller.mod_page = 0
    controller.mod_parameter = None
    controller.mod_return_page = FEATHER.Page.MOD_SETTINGS
    controller.mod_edit_value = ""
    controller.mod_enum_selection = None
    controller.mod_keyboard_shift = False
    controller.mod_keyboard_symbols = False
    controller.toast_until = 0
    controller.toast_message = ""
    controller._toast = lambda message: None
    return controller


class FeatherUtilitiesTest(unittest.TestCase):
    def test_file_entries_use_compact_slots_and_keep_mapping_access(self):
        entry = PAGES.FileEntry(
            "part.gcode", "/data/gcodes/part.gcode", False, 1024, 42)

        self.assertFalse(hasattr(entry, "__dict__"))
        self.assertEqual(entry["name"], "part.gcode")
        self.assertEqual(entry["size"], 1024)
        with self.assertRaises(KeyError):
            _value = entry["unknown"]

    def test_packaged_feather_config_declares_joystick_safety_limits(self):
        config_path = pathlib.Path(__file__).parents[1] / "config" / "feather.cfg"
        contents = config_path.read_text(encoding="utf-8")
        section = contents.split(
            "[feather_screen]", 1)[1].split("[", 1)[0]
        active_options = [line for line in section.splitlines()
                          if line.strip() and not line.lstrip().startswith("#")]
        self.assertEqual(active_options, [
            "joystick_x_min: -110",
            "joystick_x_max: 110",
            "joystick_y_min: -110",
            "joystick_y_max: 110",
            "joystick_z_min: 0",
            "joystick_z_max: 220",
        ])
        self.assertNotIn("[delayed_gcode reset_screen]", contents)

    def test_network_helper_includes_stock_sbin_paths(self):
        helper = (pathlib.Path(__file__).parents[1] / ".shell" / "commands" /
                  "znetwork.sh").read_text(encoding="utf-8")
        self.assertIn("PATH=/sbin:/usr/sbin:/bin:/usr/bin", helper)

    def test_renderer_escapes_untrusted_text(self):
        quoted = FEATHER.FeatherRenderer.quote('file "one"\\two\nnext')
        self.assertEqual(quoted, '"file \\"one\\"\\\\two next"')

    def test_renderer_normalizes_fonts_compiled_into_typer(self):
        normalize = FEATHER.FeatherRenderer.normalize_font
        self.assertEqual(normalize("Roboto 9pt"), "JetBrainsMono 8pt")
        self.assertEqual(normalize("Roboto Bold 14pt"),
                         "JetBrainsMono Bold 12pt")
        self.assertEqual(normalize("JetBrainsMono 11pt"), "JetBrainsMono 12pt")
        command = FEATHER.FeatherRenderer().text(
            10, 10, "Visible", font="Roboto 10pt")
        self.assertIn('-f "JetBrainsMono 8pt"', command)
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
        allowed = FEATHER.FeatherScreen._action_allowed
        self.assertTrue(allowed(FEATHER.Page.FILE_CONFIRM, "file.start"))
        self.assertFalse(allowed(FEATHER.Page.IDLE_HOME, "file.start"))
        self.assertTrue(allowed(FEATHER.Page.CANCEL_CONFIRM,
                                "print.cancel.confirm"))
        self.assertTrue(allowed(FEATHER.Page.CANCEL_CONFIRM, "nav.back"))
        self.assertFalse(allowed(FEATHER.Page.PRINTING,
                                 "print.cancel.confirm"))

    def test_heater_targets_use_configured_limits(self):
        heater = type("Heater", (), {"min_temp": 10, "max_temp": 300})()
        clamp = FEATHER.FeatherScreen._clamp_heater_target
        self.assertEqual(clamp(0, heater, 250), 0)
        self.assertEqual(clamp(5, heater, 250), 10)
        self.assertEqual(clamp(350, heater, 250), 299)

    def test_page_actions_cover_new_navigation_and_reject_stale_taps(self):
        allowed = FEATHER.FeatherScreen._action_allowed
        self.assertTrue(allowed(FEATHER.Page.IDLE_HOME, "nav.menu"))
        self.assertTrue(allowed(FEATHER.Page.IDLE_HOME, "nav.heat"))
        self.assertTrue(allowed(FEATHER.Page.IDLE_HOME, "nav.network"))
        self.assertTrue(allowed(FEATHER.Page.IDLE_HOME, "nav.job"))
        self.assertTrue(allowed(FEATHER.Page.PRINTING, "nav.home"))
        self.assertFalse(allowed(FEATHER.Page.IDLE_HOME, "nav.filament"))
        self.assertTrue(allowed(FEATHER.Page.MAIN_MENU, "nav.filament"))
        self.assertTrue(allowed(FEATHER.Page.CONTROL_HOME, "nav.calibration"))
        self.assertTrue(allowed(FEATHER.Page.CALIBRATION_CONFIRM,
                                "cal.material.PETG"))
        self.assertTrue(allowed(FEATHER.Page.Z_OFFSET_SUMMARY,
                                "z.zone.front_left"))
        self.assertTrue(allowed(FEATHER.Page.Z_OFFSET_PAPER, "z.probe"))
        self.assertTrue(allowed(FEATHER.Page.Z_OFFSET_PAPER, "z.move_1_5"))
        self.assertTrue(allowed(FEATHER.Page.Z_OFFSET_PAPER, "z.step.25"))
        self.assertTrue(allowed(FEATHER.Page.Z_OFFSET_PAPER_BRIEFING,
                                "z.paper_briefing.continue"))
        self.assertTrue(allowed(FEATHER.Page.LIVE_Z_OFFSET,
                                "live_z.closer"))
        self.assertTrue(allowed(FEATHER.Page.LIVE_Z_OFFSET,
                                "live_z.save"))
        self.assertFalse(allowed(FEATHER.Page.Z_OFFSET_SUMMARY,
                                 "live_z.closer"))
        self.assertFalse(allowed(FEATHER.Page.LIVE_Z_OFFSET, "z.probe"))
        self.assertFalse(allowed(FEATHER.Page.CONTROL_MOVE,
                                 "z.zone.front_left"))
        self.assertFalse(allowed(FEATHER.Page.SETTINGS, "cal.confirm"))
        self.assertTrue(allowed(FEATHER.Page.SETTINGS, "settings.mod"))
        self.assertTrue(allowed(FEATHER.Page.MOD_SETTINGS, "mod.item.12"))
        self.assertFalse(allowed(FEATHER.Page.MOD_ENUM, "mod.item.12"))
        self.assertTrue(allowed(FEATHER.Page.MOD_ENUM, "mod.option.2"))
        self.assertTrue(allowed(FEATHER.Page.MOD_VALUE, "mod.key.hash"))
        self.assertFalse(allowed(FEATHER.Page.MOD_SETTINGS, "mod.save"))

    def test_network_status_parser_is_bounded_to_public_fields(self):
        parsed = FEATHER.FeatherScreen.parse_network_status(
            "MODE=WIFI\nSSID=Workshop\nSIGNAL=-54\nIP=192.168.2.10\nSECRET=no\n")
        self.assertEqual(parsed, {"mode": "WIFI", "ssid": "Workshop",
                                  "signal": "-54", "ip": "192.168.2.10"})

    def test_material_setting_defaults_to_na_and_is_persisted(self):
        declaration = json.loads((pathlib.Path(__file__).parents[1] /
                                  "mod_params.json").read_text(encoding="utf-8"))
        material = next(item for item in declaration["parameters"]
                        if item["key"] == "current_material")
        self.assertEqual(material["default"], "n/a")
        self.assertTrue(material["hidden"])
        normalize = FEATHER.FeatherScreen._normalize_material
        self.assertEqual(normalize(None), "n/a")
        self.assertEqual(normalize("abs/pc"), "ABS-PC")
        self.assertEqual(normalize("custom"), "n/a")

    def test_visible_mod_parameters_have_screen_descriptions(self):
        declaration = json.loads((pathlib.Path(__file__).parents[1] /
                                  "mod_params.json").read_text(encoding="utf-8"))
        visible = [item for item in declaration["parameters"]
                   if not item.get("hidden", False)]
        self.assertTrue(visible)
        self.assertTrue(all(item.get("description") for item in visible))

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
                              options=["YES", "NO"])
        self.assertEqual(MOD_UI.bool_labels(parameter), ("YES", "NO"))
        parameter.options = None
        self.assertEqual(MOD_UI.bool_labels(parameter), ("OFF", "ON"))

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
        manager._notify_changed = lambda param: notified.append(param.key)

        result = manager.set_value("display", "GUPPY")

        self.assertEqual(result, "GUPPY")
        self.assertEqual(manager.variables["display"], 3)
        self.assertEqual(saved, [{"display": 3}])
        self.assertEqual(notified, ["display"])

    def test_screws_output_parser(self):
        parse = FEATHER.FeatherScreen.parse_screw_result
        self.assertEqual(parse("rear right : x=1, y=2, z=0.1 : adjust CCW 00:13"),
                         {"name": "rear right", "direction": "CCW", "turns": "00:13"})
        self.assertEqual(parse("front left (base) : x=1, y=2, z=0"),
                         {"name": "front left", "direction": "BASE", "turns": "-"})


class RendererStateTest(unittest.TestCase):
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

        self.assertEqual(commands[0], "--batch clear-hitboxes")
        self.assertIn("--batch fill -p 25 75 -s 430 285", drawing)
        self.assertIn("--batch stroke -p 25 75 -s 430 285", drawing)
        self.assertIn("CAUTION", drawing)
        self.assertIn("FIRST LINE", drawing)
        self.assertIn("--id 0:dialog.close", drawing)
        self.assertIn("--id 0:dialog.apply", drawing)
        self.assertEqual(
            set(renderer._buttons), {"dialog.close", "dialog.apply"})

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

        renderer.startup_modal(0)
        renderer.startup_modal(2)

        first = "\n".join(batches[0])
        expanded = "\n".join(batches[1])
        self.assertIn("INITIALIZING KLIPPER", first)
        self.assertIn("PLEASE WAIT", first)
        self.assertIn("-p 392 232 -s 17 1", first)
        self.assertIn("-p 384 232 -s 33 1", expanded)
        self.assertIn("--batch clear-hitboxes", first)

        pulse = "\n".join(renderer.startup_pulse(1))
        self.assertIn("-p 388 232 -s 25 1", pulse)
        self.assertNotIn("--batch clear-hitboxes", pulse)
        self.assertNotIn("-p 0 0 -s 800 480", pulse)

    def test_restart_startup_modal_explains_the_static_reconnect_gap(self):
        renderer = FEATHER.FeatherRenderer()
        batches = []
        renderer.send = batches.append

        renderer.startup_modal(0, restarting=True)

        drawing = "\n".join(batches[0])
        self.assertIn("INITIALIZING KLIPPER", drawing)
        self.assertIn("RESTART IN PROGRESS - DISPLAY MAY PAUSE", drawing)
        self.assertIn("PLEASE WAIT", drawing)
        self.assertNotIn("KLIPPER IS LOADING", drawing)

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

    def test_bundled_themes_are_loaded_and_recolor_all_known_roles(self):
        renderer = FEATHER.FeatherRenderer()
        self.assertEqual(set(renderer.theme_names()), {
            "DEFAULT", "CYBERPUNK_RED", "CYBERPUNK_YELLOW", "OCEAN",
            "DARK", "SYNTH", "FOREST", "AURORA", "PIP_BOY_2000",
            "PIP_BOY_3000"})
        renderer.set_theme("OCEAN")
        for source, role in UI.COLOR_ROLES.items():
            self.assertEqual(renderer.color(source),
                             renderer._themes["OCEAN"][role])
        page = "\n".join(renderer.begin_page("Themes"))
        self.assertIn("-c 02080d", page)
        self.assertIn("-c 35baf6", page)
        self.assertNotIn("-c 35d9e6", page)

        renderer.set_theme("PIP_BOY_2000")
        self.assertEqual(renderer.color(UI.COLOR_CYAN), "69d540")
        self.assertEqual(renderer.color(UI.COLOR_VIOLET), "a8cf45")
        self.assertEqual(renderer.color("button_background"), "28230f")
        self.assertEqual(renderer.color("header_text"), "b88d2e")
        button = "\n".join(renderer.button(
            "test", 10, 70, 180, 44, "BUTTON"))
        self.assertIn("--background 28230f", button)
        self.assertIn("--border 6d5c2b", button)
        self.assertIn("--text-color b58c2f", button)
        selected = "\n".join(renderer.button(
            "selected", 200, 70, 180, 44, "SELECTED",
            state="selected"))
        self.assertIn("--background 102306", selected)
        self.assertIn("--border 69d540", selected)
        header = "\n".join(renderer.begin_page("Pip-Boy"))
        self.assertIn("-c 24200e", header)
        self.assertIn("-c b88d2e", header)
        self.assertIn("-c 5b4c24", header)
        renderer.set_theme("PIP_BOY_3000")
        self.assertEqual(renderer.color(UI.COLOR_CYAN), "15eb18")
        self.assertEqual(renderer.color(UI.COLOR_TEXT), "8df58a")

    def test_every_screen_color_is_assigned_to_a_theme_role(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        colors = {value.lower() for value in
                  re.findall(r'"([0-9a-fA-F]{6})"', source)}
        self.assertEqual(colors - set(UI.COLOR_ROLES), set())

    def test_user_theme_directory_can_add_and_override_themes(self):
        with tempfile.TemporaryDirectory() as user_directory:
            custom = {
                "schema_version": 1,
                "name": "CUSTOM_BLUE",
                "description": "User supplied blue",
                "colors": dict(UI.FALLBACK_THEME, primary="123abc"),
            }
            override = {
                "schema_version": 1,
                "name": "OCEAN",
                "description": "User override",
                "colors": dict(UI.FALLBACK_THEME, primary="abcdef"),
            }
            pathlib.Path(user_directory, "custom.json").write_text(
                json.dumps(custom), encoding="utf-8")
            pathlib.Path(user_directory, "override.json").write_text(
                json.dumps(override), encoding="utf-8")
            pathlib.Path(user_directory, "invalid.json").write_text(
                '{"schema_version": 1, "name": "BROKEN"}', encoding="utf-8")

            with self.assertLogs(level="WARNING") as logs:
                renderer = FEATHER.FeatherRenderer(
                    theme_directories=(UI.THEME_DIRECTORY, user_directory))
            self.assertIn("CUSTOM_BLUE", renderer.theme_names())
            self.assertIn("invalid theme", "\n".join(logs.output))
            renderer.set_theme("CUSTOM_BLUE")
            self.assertEqual(renderer.color("35d9e6"), "123abc")
            renderer.set_theme("OCEAN")
            self.assertEqual(renderer.color("35d9e6"), "abcdef")
            self.assertEqual(renderer.theme_description("OCEAN"),
                             "User override")

    def test_theme_schema_rejects_missing_and_invalid_colors(self):
        valid = {
            "schema_version": 1,
            "name": "VALID",
            "description": "Valid test theme",
            "colors": dict(UI.FALLBACK_THEME),
        }
        name, description, colors = FEATHER.FeatherRenderer._validate_theme(valid)
        self.assertEqual((name, description), ("VALID", "Valid test theme"))
        self.assertEqual(colors["primary"], UI.COLOR_CYAN)
        self.assertEqual(colors["button_background"], colors["panel"])
        self.assertEqual(colors["button_border"], colors["primary"])
        self.assertEqual(colors["header_text"], colors["primary"])

        invalid = dict(valid)
        invalid["colors"] = dict(valid["colors"], primary="not-a-color")
        with self.assertRaisesRegex(ValueError, "invalid primary"):
            FEATHER.FeatherRenderer._validate_theme(invalid)

        invalid_optional = dict(valid)
        invalid_optional["colors"] = dict(
            valid["colors"], button_background="not-a-color")
        with self.assertRaisesRegex(ValueError, "invalid button_background"):
            FEATHER.FeatherRenderer._validate_theme(invalid_optional)

    def test_python_renderer_matches_cpp_protocol_fixture(self):
        fixture = pathlib.Path(__file__).parent / "fixtures" / "feather_draw_protocol.txt"
        renderer = FEATHER.FeatherRenderer()
        commands = [
            "--batch clear-hitboxes",
            "--batch clear -c 000000",
            renderer.fill(10, 20, 30, 40, "a0b1c2"),
            renderer.stroke(11, 21, 31, 41, "872187", 3),
            renderer.text(
                100, 120, 'file "one" \\ Привет', "ffffff", "Roboto 12pt",
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
                         "JetBrainsMono Bold 16pt")
        self.assertEqual(renderer._buttons["cancel"][4], "CANCEL PRINT")

    def test_footer_is_preserved_across_page_frames(self):
        renderer = FEATHER.FeatherRenderer()
        sent = []
        renderer.send = sent.append
        renderer.footer(21, 220, 24, 60, "192.168.2.4", "idle")

        first = renderer.begin_page("Control")
        second = renderer.begin_page("Settings")

        footer = "\n".join(sent[0])
        self.assertIn("NOZZLE 21/220C", footer)
        self.assertIn("192.168.2.4 | IDLE", footer)
        self.assertNotIn("NOZZLE", "\n".join(first))
        self.assertNotIn("NOZZLE", "\n".join(second))
        self.assertIn("-s 800 442", "\n".join(first))
        self.assertNotIn("-s 784 472", "\n".join(first))
        self.assertIn("-s 784 439", "\n".join(first))

    def test_theme_change_repaints_cached_footer(self):
        renderer = FEATHER.FeatherRenderer()
        renderer.send = lambda _commands: None
        renderer.footer(21, 220, 24, 60, "192.168.2.4", "idle")

        self.assertTrue(renderer.set_theme("OCEAN"))
        page = "\n".join(renderer.begin_page("Settings"))
        self.assertIn("NOZZLE 21/220C", page)
        self.assertIn("192.168.2.4 | IDLE", page)
        self.assertIn("-c 35baf6", page)

        unchanged = "\n".join(renderer.begin_page("Settings"))
        self.assertNotIn("NOZZLE 21/220C", unchanged)

    def test_mesh_matrix_validation_and_color_bands(self):
        normalize = FEATHER.FeatherScreen.normalize_mesh_matrix
        self.assertEqual(normalize([[0, "0.1"], [-0.2, 0.3]]),
                         [[0.0, 0.1], [-0.2, 0.3]])
        self.assertEqual(normalize([[0], [1, 2]]), [])
        self.assertEqual(normalize([0, 1]), [])
        color = FEATHER.FeatherScreen._mesh_color
        self.assertEqual(color(-0.2, -0.2, 0.3), "244c66")
        self.assertEqual(color(0.3, -0.2, 0.3), "ff4d5a")

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

    def test_nonblocking_fifo_retries_without_dropping_page_commands(self):
        renderer = FEATHER.FeatherRenderer()
        renderer.draw_fd = 7
        retries = []
        renderer.set_retry_scheduler(retries.append)
        writes = []

        def write(_fd, payload):
            if not writes:
                writes.append(None)
                raise BlockingIOError(errno.EAGAIN, "full")
            writes.append(bytes(payload))
            return len(payload)

        with mock.patch("os.write", side_effect=write):
            renderer.send(["--batch clear -c 030607"])
            self.assertEqual(len(retries), 1)
            self.assertTrue(renderer._pending_draw)
            retries[0](0.0)
        self.assertFalse(renderer._pending_draw)
        self.assertIn(b"--batch clear", writes[1])

    def test_large_draw_is_split_into_atomic_complete_frames(self):
        commands = [
            "--batch text -p 10 %d -t %s" % (index, "x" * 90)
            for index in range(48)
        ]
        frames = FEATHER.FeatherRenderer._encode_frames(commands)
        self.assertGreater(len(frames), 1)
        self.assertTrue(all(len(frame) <= UI.MAX_ATOMIC_DRAW
                            for frame in frames))
        self.assertTrue(all(frame.endswith(b"--batch flush\n--end\n")
                            for frame in frames))
        joined = b"\n".join(frames)
        for command in commands:
            self.assertIn(command.encode("utf-8"), joined)

    def test_atomic_draw_frames_resume_after_fifo_becomes_writable(self):
        renderer = FEATHER.FeatherRenderer()
        renderer.draw_fd = 7
        retries = []
        renderer.set_retry_scheduler(retries.append)
        commands = ["--batch text -t %s" % ("x" * 100) for _ in range(48)]
        accepted = []
        calls = 0

        def write(_fd, payload):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise BlockingIOError(errno.EAGAIN, "full")
            accepted.append(bytes(payload))
            return len(payload)

        with mock.patch("os.write", side_effect=write):
            renderer.send(commands)
            self.assertEqual(len(retries), 1)
            self.assertTrue(renderer._pending_draw)
            retries[0](0.0)
        self.assertFalse(renderer._pending_draw)
        self.assertFalse(renderer._pending_frames)
        self.assertGreater(len(accepted), 1)

    def test_pending_draw_memory_is_bounded_and_renderer_is_restarted(self):
        renderer = FEATHER.FeatherRenderer()
        renderer.draw_fd = 7
        renderer._pending_draw = bytearray(UI.MAX_PENDING_DRAW - 4)
        renderer.process = mock.Mock()
        renderer.process.poll.return_value = None
        with self.assertLogs(level="ERROR") as logs:
            renderer.send(["--batch clear -c 030607"])
        self.assertEqual(renderer._pending_draw, bytearray())
        renderer.process.terminate.assert_called_once()
        self.assertIn("pending draw data exceeded", "\n".join(logs.output))

    def test_renderer_waits_for_old_typer_before_recreating_fifos(self):
        renderer = FEATHER.FeatherRenderer()
        events = []
        writes = []
        process = type("Process", (), {})()
        with mock.patch("subprocess.call",
                        side_effect=lambda *args, **kwargs: events.append("kill")), \
                mock.patch.object(renderer, "_wait_for_typer_exit",
                                  side_effect=lambda timeout: events.append("wait") or True), \
                mock.patch("os.unlink",
                           side_effect=lambda path: events.append("unlink:" + path)), \
                mock.patch.object(renderer, "_make_fifo",
                                  side_effect=lambda path: events.append("fifo:" + path)), \
                mock.patch("os.open", side_effect=(10, 11)), \
                mock.patch("os.write",
                           side_effect=lambda _fd, data:
                           writes.append(bytes(data)) or len(data)), \
                mock.patch("subprocess.Popen", return_value=process):
            renderer.start()
        self.assertEqual(events[:4], ["kill", "wait",
                                      "unlink:/tmp/typer",
                                      "unlink:/tmp/feather-events"])
        self.assertLess(events.index("unlink:/tmp/feather-events"),
                        events.index("fifo:/tmp/feather-events"))
        initial_frame = b"".join(writes)
        self.assertIn(b"--batch clear-hitboxes", initial_frame)
        self.assertIn(b"--batch clear -c 030607", initial_frame)

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

    def test_footer_updates_only_when_values_change(self):
        renderer = FEATHER.FeatherRenderer()
        sent = []
        renderer.send = sent.append
        renderer.footer(20, 0, 25, 0, "Offline", "idle")
        renderer.footer(20, 0, 25, 0, "Offline", "idle")
        renderer.footer(21, 0, 25, 0, "Offline", "idle")
        self.assertEqual(len(sent), 2)

    def test_dynamic_list_and_keyboard_hitboxes_stay_between_chrome(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.renderer = FEATHER.FeatherRenderer()
        controller.renderer.send = lambda commands: None

        controller.file_entries = [
            {"name": "part-%d.gcode" % index, "directory": False}
            for index in range(5)]
        controller.file_page = 0
        controller.current_directory = ""
        controller._load_file_entries = lambda: None
        controller._render_file_browser()
        file_buttons = dict(controller.renderer._buttons)

        controller.networks = [
            {"ssid": "Workshop-%d" % index, "signal": -40 - index}
            for index in range(5)]
        controller.network_page = 0
        controller._render_wifi_scan()
        wifi_buttons = dict(controller.renderer._buttons)

        controller.selected_network = {"ssid": "Workshop"}
        controller.password = "secret123"
        controller.password_visible = False
        controller.keyboard_symbols = False
        controller.keyboard_shift = False
        controller._render_keyboard()
        keyboard_buttons = dict(controller.renderer._buttons)

        for page_buttons in (file_buttons, wifi_buttons, keyboard_buttons):
            rectangles = []
            for action, spec in page_buttons.items():
                if action == "nav.back":
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
        renderer.set_theme("PIP_BOY_2000")
        sent = []
        renderer.send = sent.append
        renderer.busy_notice("Klipper busy")
        renderer.busy_notice("Klipper busy")
        page = renderer.begin_page("Control")
        renderer.clear_busy_notice()
        self.assertEqual(len(sent), 2)
        self.assertIn("KLIPPER BUSY", "\n".join(sent[0]))
        self.assertIn("-c 24200e", "\n".join(sent[0]))
        self.assertIn("KLIPPER BUSY", "\n".join(page))
        self.assertIn("-c 24200e", "\n".join(page))
        self.assertNotIn("stroke", "\n".join(sent[1]))
        self.assertIn("-c 24200e", "\n".join(sent[1]))
        self.assertNotIn("-c 071305", "\n".join(sent[1]))

    def test_primary_layouts_do_not_overlap_footer(self):
        footer = (0, UI.FOOTER_Y, UI.SCREEN_WIDTH, UI.FOOTER_HEIGHT)
        rectangles = [
            (35, 70, 350, 150), (415, 70, 350, 150),
            (35, 255, 350, 150), (415, 255, 350, 150),
            (20, 315, 175, 100), (215, 315, 175, 100),
            (410, 315, 175, 100), (605, 315, 175, 100),
            (235, 382, 330, 58),
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
            "position": (123.45, 67.89, 4.2, 0.0),
            "homed_axes": "xy",
        })
        controller._require_idle = lambda: None

        controller._render_move()
        drawing = "\n".join(command for batch in batches for command in batch)

        self.assertIn("HOME ALL", drawing)
        self.assertIn("HOME XY", drawing)
        self.assertIn("JOY MODE", drawing)
        self.assertNotIn("[>STEP|JOY]", drawing)
        self.assertIn('-p 365 78 -s 65 68', drawing)
        self.assertIn('"Z-" --id 1:move.zm', drawing)
        self.assertIn('-p 365 238 -s 65 68', drawing)
        self.assertIn('"Z+" --id 1:move.zp', drawing)
        self.assertIn("NOT HOMED: Z", drawing)
        self.assertIn("X  123.45   Y   67.89", drawing)
        self.assertIn("Z    4.20", drawing)
        self.assertIn("HOMED", drawing)
        self.assertIn("HOME", drawing)
        self.assertIn("-p 190 207", drawing)
        self.assertIn("-p 397 207", drawing)
        self.assertNotIn("move.homex ", drawing)
        self.assertNotIn("move.homey", drawing)
        self.assertNotIn("move.homez", drawing)

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
        self.assertIn("HOMED: XYZ", "\n".join(batches[0]))
        self.assertNotIn("-p 140 158", "\n".join(batches[1]))
        homing_update = "\n".join(batches[2])
        self.assertIn("NOT HOMED: Z", homing_update)
        self.assertIn("-p 140 158", homing_update)
        self.assertIn("-p 365 158", homing_update)

    def test_joystick_move_page_registers_two_continuous_regions(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.reactor = Reactor()
        controller.move_mode = "joystick"
        controller.joystick = type("Planner", (), {
            "xy_speed": 600.0, "z_speed": 25.0})()
        controller.toolhead = StatusObject({
            "position": (1.0, 2.0, 10.0, 0.0), "homed_axes": "xyz"})
        controller._require_idle = lambda: None

        controller._render_move()
        drawing = "\n".join(batches[0])

        self.assertIn("XY POSITION", drawing)
        self.assertIn("Z AXIS", drawing)
        self.assertIn("POSITION", drawing)
        self.assertIn("STEP MODE", drawing)
        self.assertIn("--id 1:move.mode", drawing)
        self.assertIn("INERTIA", drawing)
        self.assertNotIn('"VX"', drawing)
        self.assertNotIn('"VZ', drawing)
        self.assertIn("HOME Z", drawing)
        self.assertIn("--id 1:move.homez", drawing)
        self.assertIn("-p 30 96 -s 420 266", drawing)
        self.assertIn("-p 486 96 -s 84 266", drawing)
        self.assertNotIn('"+100"', drawing)
        self.assertNotIn('"-100"', drawing)
        self.assertIn("--id 1:move.joy.xy", drawing)
        self.assertIn("--id 1:move.joy.z", drawing)
        self.assertEqual(drawing.count("--continuous"), 2)
        self.assertNotIn("--continuous", UI.FeatherRenderer.hitbox(
            "normal", 0, 0, 10, 10))

    def test_low_z_move_page_always_warns_and_reports_auto_profile_state(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.reactor = Reactor()
        controller.move_mode = "joystick"
        controller.move_caution_acknowledged = False
        controller.joystick = type("Planner", (), {
            "xy_speed": 600.0, "z_speed": 25.0})()
        controller.toolhead = StatusObject({
            "position": (1.0, 2.0, 4.99, 0.0), "homed_axes": "xyz"})
        controller.bed_mesh = StatusObject({
            "profile_name": "", "profiles": {"auto": {"points": []}}})
        controller._require_idle = lambda: None

        controller._render_move()
        warning = "\n".join(batches[-1])

        self.assertIn("CAUTION", warning)
        self.assertIn("Z IS BELOW 5 MM", warning)
        self.assertIn("XY MOTION MAY SCRATCH THE BED", warning)
        self.assertIn("LOAD BED PROFILE 'AUTO'?", warning)
        self.assertIn('"LOAD"', warning)
        self.assertEqual(warning.count("--batch clear-hitboxes"), 1)
        self.assertIn("--id 1:move.caution.dismiss", warning)
        self.assertIn("--id 1:move.caution.auto", warning)
        self.assertIn("--id 1:move.joy.z", warning)
        self.assertIn("--id 1:move.joy.xy", warning)

        controller.bed_mesh.status["profile_name"] = "auto"
        controller._render_move()
        safe = "\n".join(batches[-1])

        self.assertIn("CAUTION", safe)
        self.assertIn("BED PROFILE 'AUTO' IS LOADED", safe)
        self.assertEqual(safe.count("--batch clear-hitboxes"), 1)
        self.assertIn("UNLOAD", safe)
        self.assertIn('"OK"', safe)
        self.assertIn("--id 2:move.caution.unload", safe)
        self.assertIn("--id 2:move.caution.dismiss", safe)
        self.assertIn("--id 2:move.homez", safe)
        self.assertIn("--id 2:move.joy.z", safe)
        self.assertIn("--id 2:move.joy.xy", safe)
        self.assertFalse(controller.move_caution_acknowledged)

    def test_joystick_feedback_tracks_cursor_and_position_in_realtime(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.page = FEATHER.Page.CONTROL_MOVE
        controller.move_mode = "joystick"
        controller.toolhead = StatusObject({
            "position": (1.0, 2.0, 10.0, 0.0), "homed_axes": "xyz"})
        controller._last_move = (1.0, 2.0, 10.0, "HOMED: XYZ", True, True)
        controller.joystick_cursor = ("move.joy.xy", 320, 180)
        controller.joystick_drawn_cursor = None
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
        self.assertIn("--batch stroke -p 308 168 -s 25 25", live)
        self.assertIn('"X"', live)
        self.assertIn('"   2.0"', live)
        self.assertIn('"Y"', live)
        self.assertIn('"   3.0"', live)
        self.assertIn('"Z"', live)
        self.assertIn('"  11.0"', live)
        self.assertIn('" 45.7"', live)
        self.assertNotIn('"VX"', live)
        self.assertNotIn('"VY"', live)
        self.assertNotIn('"VZ', live)

        controller.joystick_cursor = None
        controller._update_joystick_feedback(1.1, force=True)
        released = "\n".join(batches[-1])
        self.assertIn("--batch fill -p 306 166 -s 29 29", released)
        self.assertIn("--batch stroke -p 228 217 -s 25 25", released)

    def test_joystick_knob_dirty_region_stays_inside_static_artwork(self):
        margin = (FEATHER.JOYSTICK_KNOB_SIZE // 2
                  + FEATHER.JOYSTICK_DIRTY_MARGIN)
        for cursor in (
                ("move.joy.xy", -100, -100),
                ("move.joy.xy", 900, 900)):
            _action, x, y, _cx, _cy, _color = (
                FEATHER.FeatherScreen._joystick_cursor_geometry(cursor))
            self.assertGreaterEqual(x - margin, 70)
            self.assertLessEqual(x + margin, 410)
            self.assertGreaterEqual(y - margin, 120)
            self.assertLessEqual(y + margin, 338)

        for raw_y in (-100, 900):
            geometry = FEATHER.FeatherScreen._joystick_cursor_geometry(
                ("move.joy.z", 443, raw_y))
            self.assertGreaterEqual(geometry[2] - margin, 103)
            self.assertLessEqual(geometry[2] + margin, 354)

    def test_joystick_knob_move_clears_center_instead_of_leaving_ghost(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.renderer = FEATHER.FeatherRenderer()

        commands = controller._joystick_indicator_commands(
            ("move.joy.xy", 240, 229), ("move.joy.xy", 320, 180))
        drawing = "\n".join(commands)

        self.assertIn("--batch fill -p 226 215 -s 29 29", drawing)
        self.assertIn("--batch stroke -p 308 168 -s 25 25", drawing)
        self.assertNotIn("--batch stroke -p 228 217 -s 25 25", drawing)

    def test_joystick_tick_forces_final_zero_inertia_frame(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.page = FEATHER.Page.CONTROL_MOVE
        controller.move_mode = "joystick"
        controller.print_state = FEATHER.PrintState.IDLE
        controller.joystick_action = None
        controller.joystick_busy_since = None
        controller.joystick_queued = True
        controller.joystick_timer_active = True
        controller.reactor = type("TimerReactor", (), {"NEVER": 9999.0})()
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
            def advance(position, period):
                return None

        class ActiveStream:
            active = True

            def __init__(self):
                self.finished = False

            @staticmethod
            def ahead(eventtime):
                return 0.0

            @staticmethod
            def wants_segment(eventtime):
                return True

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
        controller.page = FEATHER.Page.CONTROL_MOVE
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

        controller._update_joystick_feedback(
            1.0, position=(1.0, 2.0, 4.9), force=True)

        self.assertEqual(stopped, [])
        self.assertEqual(rendered, [])
        self.assertEqual(controller.move_caution_signature, (True, "active"))
        self.assertIn("CAUTION", "\n".join(batches[0]))
        self.assertNotIn("--batch clear-hitboxes", "\n".join(batches[0]))

    def test_low_z_overlay_keeps_z_feedback_live(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.page = FEATHER.Page.CONTROL_MOVE
        controller.move_mode = "joystick"
        controller.move_caution_signature = (True, "active")
        controller.move_caution_acknowledged = False
        controller.toolhead = StatusObject({
            "position": (1.0, 2.0, 4.8, 0.0), "homed_axes": "xyz"})
        controller.bed_mesh = StatusObject({
            "profile_name": "auto", "profiles": {"auto": {"points": []}}})
        controller._last_move = (
            1.0, 2.0, 4.9, "HOMED: XYZ", True, True)
        controller.joystick_cursor = ("move.joy.z", 510, 150)
        controller.joystick_drawn_cursor = None
        controller.joystick_feedback_at = 0.0
        controller.joystick_drawn_inertia = 0.0
        controller.joystick = type("Planner", (), {
            "inertia": lambda self: {"velocity": (0.0, 0.0, -2.0)},
        })()

        controller._update_joystick_feedback(
            1.0, position=(1.0, 2.0, 4.8), force=True)

        drawing = "\n".join(batches[-1])
        self.assertIn("--batch stroke -p 498 138 -s 25 25", drawing)
        self.assertIn('"  2.0"', drawing)
        self.assertIn('"   4.8"', drawing)

    def test_step_mode_caution_uses_same_overlay_geometry_as_joystick(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.renderer = FEATHER.FeatherRenderer()
        controller.move_mode = "step"

        commands = controller._move_caution_commands("available")
        drawing = "\n".join(commands)

        self.assertIn("-p 30 96 -s 420 266", drawing)
        self.assertNotIn("--batch clear-hitboxes", drawing)
