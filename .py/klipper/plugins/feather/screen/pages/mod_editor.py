## Mod-parameter editors for Feather.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from ui import ThemeColor
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
