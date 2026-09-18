## Mod-parameter editors for Feather.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from ui import ThemeColor, ThemeRole
from ui.lazy import LazyModule
from ff5m_ui.screen import ScreenPage

from feather.screen.keyboard import TEXT_KEYBOARD
from feather.screen.pagination import Pagination


mod_ui = LazyModule("feather.settings.mod")


class ModEditorPagesMixin:
    def _render_parameter_options(self):
        param = self.mod_parameter
        if param is None:
            self._show_page(ScreenPage.MOD_SETTINGS)
            return
        if param.key == "feather_theme":
            self._render_theme_options(param)
            return
        commands = self.renderer.begin_page(str(param.label), back=True)
        commands.append(self.renderer.text(
            25, 76, mod_ui.description(param), ThemeColor.TEXT,
            "JetBrainsMono 8pt", max_width=650, max_height=44, wrap=True,
            truncate=True))
        if mod_ui.restart_effect(param) is not None:
            commands.append(self.renderer.text(
                25, 108, "APPLYING THIS VALUE RESTARTS KLIPPER.", ThemeColor.WARNING,
                "JetBrainsMono 8pt"))
        options = self.parameter_options
        if (param.key == "feather_theme"
                and self.selected_parameter_option not in options):
            self.selected_parameter_option = "DEFAULT"
        entries = self.parameter_option_entries
        pagination = Pagination(
            entries, getattr(self, "parameter_options_page_index", 0), 4)
        self.parameter_options_page_index = pagination.page
        start = pagination.start
        for row, option in enumerate(pagination.visible):
            index = start + row
            selected = (option.enabled
                        and option.value == self.selected_parameter_option)
            detail = str(option.description or "").upper()
            label = str(option.label).upper()
            if detail:
                label += " // " + detail
            if selected:
                label += "  [SELECTED]"
            state = ("disabled" if not option.enabled else
                     "selected" if selected else "enabled")
            commands += self.renderer.button(
                "mod.option.%d" % index, 25, 120 + row * 66, 750, 58,
                label, state=state, font="JetBrainsMono 8pt")
        if pagination.page_count > 1:
            commands += self.renderer.button(
                "mod.options.prev", 25, 390, 120, 47, "<",
                state=("enabled" if pagination.has_previous
                       else "disabled"),
                font="JetBrainsMono Bold 8pt")
            commands += self.renderer.button(
                "mod.cancel", 155, 390, 220, 47, "CANCEL", state="danger",
                font="JetBrainsMono Bold 8pt")
            commands += self.renderer.button(
                "mod.apply", 425, 390, 220, 47, "APPLY",
                font="JetBrainsMono Bold 8pt")
            commands += self.renderer.button(
                "mod.options.next", 655, 390, 120, 47, ">",
                state=("enabled" if pagination.has_next else "disabled"),
                font="JetBrainsMono Bold 8pt")
            commands.append(self.renderer.text(
                750, 80, "%d/%d" % (
                    pagination.page + 1, pagination.page_count),
                ThemeColor.DIM, "JetBrainsMono 8pt", "right", "middle"))
        else:
            commands += self.renderer.button(
                "mod.cancel", 25, 390, 360, 47, "CANCEL", state="danger",
                font="JetBrainsMono Bold 8pt")
            commands += self.renderer.button(
                "mod.apply", 415, 390, 360, 47, "APPLY",
                font="JetBrainsMono Bold 8pt")
        self.renderer.send(commands)

    def _render_theme_options(self, param):
        options = self.parameter_options
        if self.selected_parameter_option not in options:
            self.selected_parameter_option = "DEFAULT"
        entries = self.parameter_option_entries
        pagination = Pagination(
            entries, getattr(self, "parameter_options_page_index", 0), 6)
        self.parameter_options_page_index = pagination.page

        commands = self.renderer.begin_page(str(param.label), back=True)
        start = pagination.start
        for row, option in enumerate(pagination.visible):
            index = start + row
            selected = (option.enabled
                        and option.value == self.selected_parameter_option)
            state = ("disabled" if not option.enabled else
                     "selected" if selected else "enabled")
            commands += self.renderer.button(
                "mod.option.%d" % index, 25, 73 + row * 50, 270, 44,
                str(option.label).upper(), state=state,
                font="JetBrainsMono 8pt")

        commands += self._render_theme_preview()
        commands += self.renderer.button(
            "mod.options.prev", 25, 390, 90, 47, "<",
            state=("enabled" if pagination.has_previous else "disabled"),
            font="JetBrainsMono Bold 8pt")
        commands.append(self.renderer.text(
            160, 413, "%d/%d" % (
                pagination.page + 1, pagination.page_count),
            ThemeColor.DIM, "JetBrainsMono 8pt", "center", "middle"))
        commands += self.renderer.button(
            "mod.options.next", 205, 390, 90, 47, ">",
            state=("enabled" if pagination.has_next else "disabled"),
            font="JetBrainsMono Bold 8pt")
        commands += self.renderer.button(
            "mod.cancel", 505, 390, 125, 47, "CANCEL", state="danger",
            font="JetBrainsMono Bold 8pt")
        commands += self.renderer.button(
            "mod.apply", 650, 390, 125, 47, "SAVE",
            font="JetBrainsMono Bold 8pt")
        self.renderer.send(commands)

    def _render_theme_preview(self):
        selected = self.selected_parameter_option
        descriptions = dict(
            (entry.value, entry.description)
            for entry in self.parameter_option_entries if entry.enabled)
        description = str(descriptions.get(selected, "")).upper()
        commands = self.renderer.panel(
            315, 73, 460, 306, ThemeColor.BORDER, ThemeColor.PANEL, 1)
        commands += [
            self.renderer.text(
                335, 93, str(selected or "DEFAULT").replace("_", " "),
                ThemeColor.PRIMARY, "JetBrainsMono Bold 12pt",
                max_width=420, truncate=True),
            self.renderer.text(
                335, 115, description, ThemeColor.DIM,
                "JetBrainsMono 8pt", max_width=420, truncate=True),
            self.renderer.fill(335, 133, 420, 76, ThemeColor.BACKGROUND),
            self.renderer.stroke(335, 133, 420, 76, ThemeColor.BORDER, 1),
            self.renderer.text(
                351, 158, "PRINT READY", ThemeColor.PRIMARY,
                "JetBrainsMono Bold 12pt"),
            self.renderer.text(
                351, 185, "NOZZLE 220 C  /  BED 60 C", ThemeColor.TEXT,
                "JetBrainsMono 8pt"),
            self.renderer.text(
                739, 158, "ONLINE", ThemeColor.SUCCESS,
                "JetBrainsMono Bold 8pt", "right"),
        ]
        commands += self._theme_preview_button(
            335, 225, 200, "NORMAL", ThemeRole.BUTTON_BACKGROUND,
            ThemeRole.BUTTON_BORDER, ThemeRole.BUTTON_TEXT)
        commands += self._theme_preview_button(
            555, 225, 200, "SELECTED",
            ThemeRole.BUTTON_SELECTED_BACKGROUND,
            ThemeRole.BUTTON_SELECTED_BORDER,
            ThemeRole.BUTTON_SELECTED_TEXT)
        commands += [
            self.renderer.fill(335, 293, 200, 48,
                               ThemeColor.DANGER_BACKGROUND),
            self.renderer.stroke(335, 293, 200, 48,
                                 ThemeColor.DANGER, 1),
            self.renderer.text(
                435, 317, "ALERT", ThemeColor.DANGER,
                "JetBrainsMono Bold 8pt", "center", "middle"),
            self.renderer.fill(555, 293, 200, 48, ThemeColor.PRIMARY_DARK),
            self.renderer.stroke(555, 293, 200, 48,
                                 ThemeColor.SUCCESS, 1),
            self.renderer.text(
                655, 317, "READY", ThemeColor.SUCCESS,
                "JetBrainsMono Bold 8pt", "center", "middle"),
        ]
        return commands

    def _theme_preview_button(self, x, y, width, label,
                              background, border, text):
        return [
            self.renderer.fill(x, y, width, 50, background),
            self.renderer.stroke(x, y, width, 50, border, 2),
            self.renderer.text(
                x + width // 2, y + 25, label, text,
                "JetBrainsMono Bold 8pt", "center", "middle"),
        ]

    def _render_mod_value(self):
        param = self.mod_parameter
        if param is None:
            self._show_page(ScreenPage.MOD_SETTINGS)
            return
        kind = mod_ui.parameter_kind(param)
        commands = self.renderer.begin_page("Edit value", back=True)
        if kind in ("int", "float"):
            commands += self._render_mod_numeric_keys(param)
            self.renderer.send(commands)
            return
        commands += [
            self.renderer.text(25, 73, str(param.label).upper(), ThemeColor.PRIMARY,
                               "JetBrainsMono Bold 12pt"),
            self.renderer.text(25, 98, param.key, ThemeColor.DIM,
                               "JetBrainsMono 8pt"),
            self.renderer.text(280, 98, mod_ui.description(param), ThemeColor.TEXT,
                               "JetBrainsMono 8pt", max_width=490,
                               truncate=True),
            self.renderer.fill(25, 120, 750, 53, ThemeColor.PANEL),
            self.renderer.stroke(25, 120, 750, 53, ThemeColor.PRIMARY, 2),
        ]
        commands += TEXT_KEYBOARD.render_value(
            self.renderer, self.mod_edit_value, self.mod_edit_cursor,
            42, 147, 710, ThemeColor.PRIMARY)
        commands += self._render_mod_text_keys()
        self.renderer.send(commands)

    def _render_mod_numeric_keys(self, param):
        spec = mod_ui.numeric_input_spec(param)
        actions = dict((digit, "mod.key.%s" % digit)
                       for digit in "0123456789")
        actions.update({
            "backspace": "mod.backspace",
            "confirm": "mod.save",
        })
        if spec.allows_decimal:
            actions["decimal"] = "mod.dot"
        if spec.allows_negative:
            actions["sign"] = "mod.sign"
        return self.renderer.numeric_keypad(
            18, 65, 764, 370, param.key, self.mod_edit_value, actions,
            subtitle=param.label, mode=spec, confirm_label="SAVE")

    def _render_mod_text_keys(self):
        commands = TEXT_KEYBOARD.render(
            self.renderer, self.mod_keyboard_symbols,
            self.mod_keyboard_shift)
        commands += self.renderer.button(
            "mod.cancel", 25, 383, 360, 54, "CANCEL", state="danger",
            font="JetBrainsMono Bold 8pt")
        commands += self.renderer.button(
            "mod.save", 415, 383, 360, 54, "SAVE",
            font="JetBrainsMono Bold 8pt")
        return commands
