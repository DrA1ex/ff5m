## Settings and Mod Settings feature for Feather.

from collections import namedtuple


ParameterOption = namedtuple(
    "ParameterOption", ("value", "label", "description", "enabled"))


from ff5m_ui.screen import ScreenPage
from feather.features.manager import FeatureHostProxy
from feather.screen.pages import (
    ModEditorPagesMixin, ModSettingsPagesMixin, SettingsPagesMixin,
)
from feather.screen.keyboard import is_keyboard_action
from feather.settings import mod as _mod_ui


class SettingsFeature(
        SettingsPagesMixin, ModSettingsPagesMixin, ModEditorPagesMixin,
        FeatureHostProxy):
    name = "settings"

    def open_preview_settings(self):
        if self.model_preview is None:
            raise RuntimeError("Open a model preview first")
        self.mod_page = 0
        self.mod_parameter = None
        self.mod_restore_anchor_key = None
        self.mod_return_page = ScreenPage.PREVIEW_SETTINGS
        self._show_page(ScreenPage.PREVIEW_SETTINGS)

    def retains_model_preview(self, page):
        return self._preview_parameter_context(page)

    def _capture_preview_view(self):
        self._require_idle()
        state = self.model_preview
        if state is None:
            raise RuntimeError("Open a model preview first")
        values = {}
        for name in ("yaw", "pitch", "scale"):
            param = self.params.params_map["feather_preview_" + name]
            digits = param.fraction_digits
            values[param.key] = round(state[name], digits) if digits is not None else state[name]
        self.params.set_values(values)
        state["settings"] = self._loaded_model_preview()._model_preview_settings(state["mode"])
        self._render_mod_settings()
        self._toast("CURRENT VIEW SAVED")

    def on_page_changed(self, old_page, new_page):
        if (self.feature_manager.owner_name(old_page) == self.name
                and self.feature_manager.owner_name(new_page) != self.name):
            self.deactivate()

    def __init__(self, host):
        FeatureHostProxy.__init__(self, host)
        self.mod_page = 0
        self.mod_parameter = None
        self.mod_return_page = ScreenPage.MOD_SETTINGS
        self.mod_edit_value = ""
        self.selected_parameter_option = None
        self.mod_theme_original = None
        self._parameter_options_snapshot = ()
        self.parameter_options_page_index = 0
        self.mod_keyboard_shift = False
        self.mod_keyboard_symbols = False
        self.mod_update_pending = False
        self.mod_update_token = 0
        self.mod_update_modal_visible = False
        self.mod_update_complete = None
        self._benchmark_taps = 0
        self._benchmark_tap_deadline = 0.0

    def _reset_benchmark_taps(self):
        self._benchmark_taps = 0
        self._benchmark_tap_deadline = 0.0

    def _handle_benchmark_tap(self):
        now = self.reactor.monotonic()
        if now > self._benchmark_tap_deadline:
            self._benchmark_taps = 0
        self._benchmark_taps += 1
        self._benchmark_tap_deadline = now + 2.0
        if self._benchmark_taps < 5:
            return
        self._reset_benchmark_taps()
        self._show_page(ScreenPage.RENDER_BENCHMARK)

    @property
    def parameter_options(self):
        return tuple(
            option.value for option in self._parameter_options_snapshot
            if option.enabled)

    @property
    def parameter_option_entries(self):
        return self._parameter_options_snapshot

    def _set_parameter_options(self, options, selection,
                               descriptions=None, disabled=()):
        """Replace the immutable row snapshot for this picker."""
        descriptions = descriptions or {}
        entries = [ParameterOption(
            value, value, descriptions.get(value, ""), True)
                   for value in options]
        entries.extend(ParameterOption(
            None, issue.name, issue.description, False)
                       for issue in disabled)
        self._parameter_options_snapshot = tuple(entries)
        self.selected_parameter_option = selection
        self.parameter_options_page_index = 0

    def render(self, page):
        {
            ScreenPage.SETTINGS: self._render_settings,
            ScreenPage.MOD_SETTINGS: self._render_mod_settings,
            ScreenPage.PREVIEW_SETTINGS: self._render_mod_settings,
            ScreenPage.PARAMETER_OPTIONS: self._render_parameter_options,
            ScreenPage.MOD_VALUE: self._render_mod_value,
        }[page]()

    def allows_action(self, page, action):
        exact = {
            ScreenPage.SETTINGS: (
                "nav.back", "settings.brightness.minus",
                "settings.brightness.plus", "settings.led.minus",
                "settings.led.plus", "settings.sound", "settings.theme",
                "settings.mod", "settings.benchmark.tap"),
            ScreenPage.MOD_SETTINGS: ("nav.back", "mod.prev", "mod.next", "mod.more"),
            ScreenPage.PREVIEW_SETTINGS: ("nav.back", "mod.prev", "mod.next", "mod.more", "model.capture_view"),
            ScreenPage.PARAMETER_OPTIONS: (
                "nav.back", "mod.cancel", "mod.apply", "mod.options.prev",
                "mod.options.next"),
            ScreenPage.MOD_VALUE: (
                "nav.back", "mod.cancel", "mod.save", "mod.backspace",
                "mod.sign", "mod.dot"),
        }
        return (action in exact.get(page, ()) or
                (page in (ScreenPage.MOD_SETTINGS, ScreenPage.PREVIEW_SETTINGS) and
                 action.startswith("mod.item.")) or
                (page == ScreenPage.PARAMETER_OPTIONS and
                 action.startswith("mod.option.")) or
                (page == ScreenPage.MOD_VALUE and
                 (action.startswith("mod.key.") or
                  is_keyboard_action(action))))

    def handle_action(self, page, action):
        if page == ScreenPage.PREVIEW_SETTINGS and action == "model.capture_view":
            self._capture_preview_view()
            return True
        if action.startswith("settings."):
            if action != "settings.benchmark.tap":
                self._reset_benchmark_taps()
            self._handle_settings_action(action)
            return True
        if action.startswith("mod.") or (
                page == ScreenPage.MOD_VALUE and is_keyboard_action(action)):
            self._handle_mod_action(action)
            return True
        return False

    def back(self, page):
        self._reset_benchmark_taps()
        if page == ScreenPage.SETTINGS:
            self._show_page(ScreenPage.CONTROL_HOME)
        elif page == ScreenPage.MOD_SETTINGS:
            self._show_page(ScreenPage.SETTINGS)
        elif page == ScreenPage.PREVIEW_SETTINGS:
            state = self.model_preview
            self._show_page(ScreenPage.MODEL_PREVIEW if state and state["mode"] == "lab"
                            else ScreenPage.PRINT_PREVIEW)
        elif page in (ScreenPage.PARAMETER_OPTIONS, ScreenPage.MOD_VALUE):
            self._restore_setting_preview()
            self.mod_parameter = None
            self._show_page(self.mod_return_page)
        else:
            return False
        return True

    @property
    def status_bar_preview(self):
        param = self.mod_parameter
        if (self.page == ScreenPage.PARAMETER_OPTIONS and param is not None
                and param.key == "feather_status_bar"):
            return self.selected_parameter_option
        return None

    @property
    def input_blocked(self):
        return bool(self.mod_update_pending)

    @property
    def theme_update_blocked(self):
        param = getattr(self, "mod_parameter", None)
        previewing_theme = (
            self.page == ScreenPage.PARAMETER_OPTIONS
            and param is not None and param.key == "feather_theme")
        return bool(self.mod_update_pending or previewing_theme)

    def deactivate(self):
        self._reset_benchmark_taps()
        self._restore_setting_preview()
        self.mod_update_pending = False
        self.mod_update_modal_visible = False
        self.mod_update_complete = None
        self.mod_update_token += 1
        self.mod_parameter = None
        self.mod_return_page = ScreenPage.MOD_SETTINGS
