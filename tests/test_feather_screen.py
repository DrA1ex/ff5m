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
    def test_packaged_feather_config_uses_plugin_defaults(self):
        config_path = pathlib.Path(__file__).parents[1] / "config" / "feather.cfg"
        contents = config_path.read_text(encoding="utf-8")
        section = contents.split(
            "[feather_screen]", 1)[1].split("[", 1)[0]
        active_options = [line for line in section.splitlines()
                          if line.strip() and not line.lstrip().startswith("#")]
        self.assertEqual(active_options, [])
        reset = contents.split("[delayed_gcode reset_screen]", 1)[1].split(
            "[", 1)[0]
        self.assertIn("_BACKLIGHT", reset)
        self.assertNotIn("draw_splash", reset)

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

    def test_message_wrapping_is_bounded(self):
        lines = FEATHER.FeatherScreen._wrap("one two three four five", 8, 2)
        self.assertEqual(lines, ["one two", "three"])

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
        self.assertFalse(allowed(FEATHER.Page.IDLE_HOME, "nav.filament"))
        self.assertTrue(allowed(FEATHER.Page.MAIN_MENU, "nav.filament"))
        self.assertTrue(allowed(FEATHER.Page.CONTROL_HOME, "nav.calibration"))
        self.assertTrue(allowed(FEATHER.Page.CALIBRATION_CONFIRM,
                                "cal.material.PETG"))
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

    def test_bundled_themes_are_loaded_and_recolor_all_known_roles(self):
        renderer = FEATHER.FeatherRenderer()
        self.assertEqual(set(renderer.theme_names()), {
            "DEFAULT", "CYBERPUNK_RED", "CYBERPUNK_YELLOW", "OCEAN",
            "DARK", "SYNTH", "FOREST", "AURORA"})
        renderer.set_theme("OCEAN")
        for source, role in UI.COLOR_ROLES.items():
            self.assertEqual(renderer.color(source),
                             renderer._themes["OCEAN"][role])
        page = "\n".join(renderer.begin_page("Themes"))
        self.assertIn("-c 02080d", page)
        self.assertIn("-c 35baf6", page)
        self.assertNotIn("-c 35d9e6", page)

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

        invalid = dict(valid)
        invalid["colors"] = dict(valid["colors"], primary="not-a-color")
        with self.assertRaisesRegex(ValueError, "invalid primary"):
            FEATHER.FeatherRenderer._validate_theme(invalid)

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

    def test_button_fonts_keep_a_visible_horizontal_margin(self):
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
            renderer.button(action, 0, 60, width, 50, label, font=requested)
            rendered = renderer._buttons[action][4]
            font = renderer._buttons[action][6]
            remaining = width - renderer.text_width(rendered, font)
            self.assertGreaterEqual(
                remaining, 2 * renderer.BUTTON_TEXT_PADDING,
                "%s uses %s with only %d px remaining" %
                (rendered, font, remaining))

    def test_large_label_is_shortened_without_changing_font_size(self):
        renderer = FEATHER.FeatherRenderer()
        renderer.begin_page("Cancel")
        renderer.button("cancel", 0, 60, 260, 100, "CANCEL PRINT",
                        font="Roboto Bold 16pt")
        self.assertEqual(renderer._buttons["cancel"][6],
                         "JetBrainsMono Bold 16pt")
        self.assertEqual(renderer._buttons["cancel"][4], "CANCEL ...")

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
        sent = []
        renderer.send = sent.append
        renderer.busy_notice("Klipper busy")
        renderer.busy_notice("Klipper busy")
        page = renderer.begin_page("Control")
        renderer.clear_busy_notice()
        self.assertEqual(len(sent), 2)
        self.assertIn("KLIPPER BUSY", "\n".join(sent[0]))
        self.assertIn("KLIPPER BUSY", "\n".join(page))
        self.assertNotIn("stroke", "\n".join(sent[1]))

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
        self.assertIn("LOAD AUTO", warning)
        self.assertEqual(
            set(controller.renderer._buttons),
            {"move.caution.dismiss", "move.caution.auto"})

        controller.bed_mesh.status["profile_name"] = "auto"
        controller._render_move()
        safe = "\n".join(batches[-1])

        self.assertIn("CAUTION", safe)
        self.assertIn("BED PROFILE 'AUTO' IS ACTIVE", safe)
        self.assertEqual(
            set(controller.renderer._buttons),
            {"move.caution.dismiss"})
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

        self.assertEqual(stopped, [True])
        self.assertEqual(rendered[-1][0][2], 4.9)
        self.assertEqual(rendered[-1][1], (True, "active"))


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
        self.assertIn("1/2", first)
        self.assertIn("CYBERPUNK_RED", first)
        self.assertIn("--id 1:mod.enum.next", first)

        controller._handle_mod_action("mod.enum.next")
        second = "\n".join(controller.draw_batches[-1])
        self.assertIn("2/2", second)
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
        controller._last_time = None
        controller.display_status = StatusObject({"progress": 0.25})
        controller.print_stats = StatusObject({
            "state": "printing", "print_duration": 100,
            "info": {"current_layer": None, "total_layer": None},
        })
        controller.virtual_sdcard = type(
            "SD", (), {"estimate_print_time": 400.0})()
        controller.toolhead = StatusObject({
            "position": (10.0, 20.0, 3.25, 0.0), "homed_axes": "xyz"})

        controller._update_print_progress(100)

        drawing = "\n".join(batches[0])
        self.assertIn("00:01:40", drawing)
        self.assertIn("00:05:00", drawing)
        self.assertIn('? / ?', drawing)
        self.assertIn("3.25 MM", drawing)

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
