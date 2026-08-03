## Settings and Mod Settings feature for Feather.

from collections import namedtuple


ParameterOption = namedtuple(
    "ParameterOption", ("value", "label", "description", "enabled"))


try:
    from .ui import Page
    from .feather_feature_manager import FeatureHostProxy
    from .feather_screen_pages import FeatherPagesMixin
    from . import feather_mod_settings as _mod_ui  # load with the feature
except (ImportError, ValueError):
    from ui import Page
    from feather_feature_manager import FeatureHostProxy
    from feather_screen_pages import FeatherPagesMixin
    import feather_mod_settings as _mod_ui  # noqa: F401


class SettingsFeature(FeatherPagesMixin, FeatureHostProxy):
    name = "settings"

    def __init__(self, host):
        FeatureHostProxy.__init__(self, host)
        self.mod_page = 0
        self.mod_parameter = None
        self.mod_return_page = Page.MOD_SETTINGS
        self.mod_edit_value = ""
        self.selected_parameter_option = None
        self._parameter_options_snapshot = ()
        self.parameter_options_page_index = 0
        self.mod_keyboard_shift = False
        self.mod_keyboard_symbols = False
        self.mod_update_pending = False
        self.mod_update_token = 0
        self.mod_update_modal_visible = False
        self.mod_update_complete = None

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
            Page.SETTINGS: self._render_settings,
            Page.MOD_SETTINGS: self._render_mod_settings,
            Page.PARAMETER_OPTIONS: self._render_parameter_options,
            Page.MOD_VALUE: self._render_mod_value,
        }[page]()

    def allows_action(self, page, action):
        exact = {
            Page.SETTINGS: (
                "nav.back", "settings.brightness.minus",
                "settings.brightness.plus", "settings.led.minus",
                "settings.led.plus", "settings.sound", "settings.theme",
                "settings.mod"),
            Page.MOD_SETTINGS: ("nav.back", "mod.prev", "mod.next"),
            Page.PARAMETER_OPTIONS: (
                "nav.back", "mod.cancel", "mod.apply", "mod.options.prev",
                "mod.options.next"),
            Page.MOD_VALUE: (
                "nav.back", "mod.cancel", "mod.save", "mod.backspace",
                "mod.sign", "mod.dot", "mod.shift", "mod.symbols",
                "mod.space"),
        }
        return (action in exact.get(page, ()) or
                (page == Page.MOD_SETTINGS and
                 action.startswith("mod.item.")) or
                (page == Page.PARAMETER_OPTIONS and
                 action.startswith("mod.option.")) or
                (page == Page.MOD_VALUE and
                 action.startswith("mod.key.")))

    def handle_action(self, page, action):
        if action.startswith("settings."):
            self._handle_settings_action(action)
            return True
        if action.startswith("mod."):
            self._handle_mod_action(action)
            return True
        return False

    def back(self, page):
        if page == Page.SETTINGS:
            self._show_page(Page.CONTROL_HOME)
        elif page == Page.MOD_SETTINGS:
            self._show_page(Page.SETTINGS)
        elif page in (Page.PARAMETER_OPTIONS, Page.MOD_VALUE):
            self.mod_parameter = None
            self._show_page(self.mod_return_page)
        else:
            return False
        return True

    @property
    def input_blocked(self):
        return bool(self.mod_update_pending)

    @property
    def theme_update_blocked(self):
        return bool(self.mod_update_pending)

    def deactivate(self):
        self.mod_update_pending = False
        self.mod_update_modal_visible = False
        self.mod_update_complete = None
        self.mod_update_token += 1
