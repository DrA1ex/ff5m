## Mod-parameter editors for Feather.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from ui.lazy import LazyModule
from ff5m_ui.screen import ScreenPage

from feather.screen.pagination import Pagination


mod_ui = LazyModule("feather.settings.mod")
parameter_ui = LazyModule("ff5m_ui.parameter_controls")


class ModEditorPagesMixin:
    def _render_parameter_options(self):
        if self.mod_parameter is None:
            self._show_page(ScreenPage.MOD_SETTINGS)
            return
        self._paint_page_values()

    def _parameter_options_layout(self):
        """Whether the picker is the theme's, its bounds and how many options a page holds."""
        param = self.mod_parameter
        theme = param.key == "feather_theme"
        bounds = parameter_ui.content_bounds(self.renderer)
        capacity = parameter_ui.theme_capacity(bounds) if theme else parameter_ui.options_capacity(param, bounds)
        return theme, bounds, capacity

    def _parameter_options_content(self):
        """The picker the screen shows: one tree per parameter, its bounds and
        whether its options need pages."""
        param = self.mod_parameter
        theme, bounds, capacity = self._parameter_options_layout()
        setup_theme = theme and self.mod_return_page == ScreenPage.SETUP_NETWORK
        variant = param.key, bounds, len(self.parameter_option_entries) > capacity, setup_theme
        if getattr(self, "_options_tree_variant", None) != variant:
            self._options_tree = (parameter_ui.create_theme_page(bounds, setup=setup_theme) if theme
                                  else parameter_ui.create_options_page(param, variant[2], bounds))
            self._options_tree_variant = variant
        return self._options_tree, "Color theme" if setup_theme else str(param.label), True

    def _write_parameter_options_values(self):
        theme, _bounds, capacity = self._parameter_options_layout()
        if theme and self.selected_parameter_option not in self.parameter_options:
            self.selected_parameter_option = "DEFAULT"
        pagination = Pagination(self.parameter_option_entries, getattr(self, "parameter_options_page_index", 0), capacity)
        self.parameter_options_page_index = pagination.page
        values = parameter_ui.theme_values if theme else parameter_ui.options_values
        self._options_tree.state.update(values(self.renderer, pagination, self.selected_parameter_option))

    def _render_mod_value(self):
        if self.mod_parameter is None:
            self._show_page(ScreenPage.MOD_SETTINGS)
            return
        self._paint_page_values()

    def _mod_value_content(self):
        """The value editor the screen shows: one tree per parameter, bounds
        and, for text, the keyboard's layer."""
        param = self.mod_parameter
        if param is None:
            return None
        if mod_ui.parameter_kind(param) in ("int", "float"):
            variant = param.key, parameter_ui.content_bounds(self.renderer, kind="numeric")
            create = lambda: parameter_ui.create_numeric_page(param, variant[1])
        else:
            variant = (param.key, parameter_ui.content_bounds(self.renderer), bool(self.mod_keyboard_symbols),
                       bool(self.mod_keyboard_shift))
            create = lambda: parameter_ui.create_text_page(param, variant[2], variant[3], variant[1])
        if getattr(self, "_value_tree_variant", None) != variant:
            self._value_tree = create()
            self._value_tree_variant = variant
        return self._value_tree, "Edit value", True

    def _write_mod_value_values(self):
        tree = self._mod_value_content()[0]
        if mod_ui.parameter_kind(self.mod_parameter) in ("int", "float"):
            values = parameter_ui.numeric_values(self.mod_edit_value)
        else:
            values = parameter_ui.text_values(self.mod_edit_value, self.mod_edit_cursor)
        tree.state.update(values)
