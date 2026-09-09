## General settings page for Feather.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from ui import ThemeColor
from ff5m_ui.screen import ScreenPage


class SettingsPagesMixin:
    def _render_settings(self):
        brightness = int(self._setting("backlight", 50))
        sound = bool(self._setting("sound", 1))
        light_mode = str(self._setting("chamber_light_mode", "AT_BOOT"))
        light_subtitle = {
            "MANUAL": "APPLIES IMMEDIATELY",
            "AT_BOOT": "APPLIES AT BOOT",
            "PRINT_ONLY": "APPLIES DURING PRINTING",
        }.get(light_mode, "APPLIES AT BOOT")
        light_available = getattr(self, "chamber_light", None) is not None
        light = (self._chamber_light_brightness()
                 if light_available else None)
        theme = str(getattr(self.renderer, "theme_name", "DEFAULT"))
        commands = self.renderer.begin_page("Settings", back=True)
        # Hidden diagnostic entry: five title taps within two seconds. The
        # benchmark feature itself remains unloaded until the fifth tap opens
        # its page. Keep the hitbox clear of BACK and the emergency action.
        commands.append(self.renderer.action_hitbox(
            "settings.benchmark.tap", 170, 7, 450, 46))
        rows = (
            ("SCREEN BRIGHTNESS", None, brightness,
             "settings.brightness", 67, True),
            ("CHAMBER LIGHT", light_subtitle, light,
             "settings.led", 151, light_available),
        )
        for label, subtitle, value, prefix, y, enabled in rows:
            commands += [
                self.renderer.fill(25, y, 750, 70, ThemeColor.PANEL),
                self.renderer.stroke(25, y, 750, 70, ThemeColor.BORDER, 1),
                self.renderer.text(44, y + (18 if subtitle else 35), label,
                                   ThemeColor.PRIMARY,
                                   "JetBrainsMono Bold 8pt"),
                self.renderer.text(425, y + 36,
                                   "%d%%" % value if enabled else "--",
                                   ThemeColor.TEXT if enabled else ThemeColor.MUTED,
                                   "JetBrainsMono 12pt", "center"),
            ]
            if subtitle:
                commands.append(self.renderer.text(
                    44, y + 46, subtitle, ThemeColor.DIM,
                    "JetBrainsMono 8pt"))
            commands += self.renderer.button(prefix + ".minus", 525, y + 12,
                                             105, 46, "-5",
                                             state=("enabled" if enabled
                                                    else "disabled"))
            commands += self.renderer.button(prefix + ".plus", 650, y + 12,
                                             105, 46, "+5",
                                             state=("enabled" if enabled
                                                    else "disabled"))
        commands += [
            self.renderer.fill(25, 235, 750, 66, ThemeColor.PANEL),
            self.renderer.stroke(25, 235, 750, 66, ThemeColor.BORDER, 1),
            self.renderer.text(44, 268, "SOUND FEEDBACK", ThemeColor.PRIMARY,
                               "JetBrainsMono Bold 8pt"),
        ]
        commands += self.renderer.toggle("settings.sound", 679, 249, 76, 38,
                                         sound)
        commands += self.renderer.button(
            "settings.theme", 25, 317, 360, 100, "COLOR THEME",
            subtitle=theme.replace("_", " "), layout="center",
            font="JetBrainsMono Bold 8pt")
        commands += self.renderer.button(
            "settings.mod", 415, 317, 360, 100, "MOD PARAMETERS",
            subtitle="ALL FORGE-X OPTIONS", layout="center",
            font="JetBrainsMono Bold 8pt")
        self.renderer.send(commands)

    def _handle_settings_action(self, action):
        self._require_idle()
        if action == "settings.benchmark.tap":
            self._handle_benchmark_tap()
            return
        if action == "settings.theme":
            parameters = self._mod_parameters()
            for index, param in enumerate(parameters):
                if param.key == "feather_theme":
                    self._open_mod_parameter(index, ScreenPage.SETTINGS)
                    return
            raise RuntimeError("Feather theme parameter is unavailable")
        if action == "settings.mod":
            self.mod_page = 0
            self.mod_parameter = None
            self._show_page(ScreenPage.MOD_SETTINGS)
            return
        if action.startswith("settings.led."):
            if getattr(self, "chamber_light", None) is None:
                raise RuntimeError("Chamber light is unavailable")
            delta = -5 if action.endswith("minus") else 5
            value = max(
                0, min(100, self._chamber_light_brightness() + delta))
            self._run_script(
                "SET_MOD PARAM=chamber_light VALUE=%d" % value)
            self._render_settings()
            return
        if action == "settings.sound":
            key, value = "sound", 0 if self._setting("sound", 1) else 1
            self._animate_settings_toggle(action, bool(value))
        else:
            key = "backlight"
            delta = -5 if action.endswith("minus") else 5
            value = max(1, min(100, int(self._setting(key, 10)) + delta))
        self._run_script("SET_MOD PARAM=%s VALUE=%d" % (key, value))
        if key == "backlight":
            self._set_backlight(value)
        if key == "sound":
            self.reactor.register_callback(
                lambda _eventtime: self._render_settings(),
                self.reactor.monotonic() + 0.14)
        else:
            self._render_settings()

    def _chamber_light_brightness(self):
        try:
            value = int(self._setting("chamber_light", 50))
        except (TypeError, ValueError):
            value = 50
        return max(0, min(100, value))

    def _animate_settings_toggle(self, action, active):
        scheduler = lambda callback, delay: self.reactor.register_callback(
            callback, self.reactor.monotonic() + delay)
        renderer = getattr(self, "renderer", None)
        animate = getattr(renderer, "animate_toggle", None)
        if animate is not None:
            animate(action, bool(active), scheduler)
        block = getattr(renderer, "block_input", None)
        if block is not None:
            block()
