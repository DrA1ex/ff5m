## Mod-parameter list and update flow for Feather.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import logging

from ui.lazy import LazyModule
from ff5m_ui.screen import ScreenPage

from feather.screen.keyboard import TEXT_KEYBOARD, is_keyboard_action


mod_ui = LazyModule("feather.settings.mod")
parameter_ui = LazyModule("ff5m_ui.parameter_controls")
preview_ui = LazyModule("ff5m_ui.model_preview.settings")


class ModSettingsPagesMixin:
    def _preview_parameter_context(self, page=None):
        page = self.page if page is None else page
        return (page == ScreenPage.PREVIEW_SETTINGS or
                (page in (ScreenPage.PARAMETER_OPTIONS, ScreenPage.MOD_VALUE)
                 and getattr(self, "mod_return_page", None) == ScreenPage.PREVIEW_SETTINGS))

    def _mod_parameters(self):
        if self._preview_parameter_context():
            state = self.model_preview
            if state is None:
                raise RuntimeError("Model preview is no longer available")
            keys = preview_ui.parameter_keys(self._setting("feather_preview_type", "LAYER_LINES"), state["mode"])
            return [self.params.params_map[key] for key in keys]
        return mod_ui.visible_parameters(self.params)

    def _mod_category_pages(self, parameters):
        preview = self._preview_parameter_context()
        return mod_ui.category_pages(self.params, parameters,
                                     label="PRINT PREVIEW" if preview else None,
                                     leading_height=mod_ui.ITEM_PITCH if preview else 0)

    def _render_mod_settings(self, anchor_key=None):
        self._require_idle()
        parameters = self._mod_parameters()
        pages = self._mod_category_pages(parameters)
        if anchor_key is None:
            anchor_key = getattr(self, "mod_restore_anchor_key", None)
        self.mod_restore_anchor_key = None
        if anchor_key is not None:
            anchored = mod_ui.page_of_parameter(pages, anchor_key)
            if anchored is not None:
                self.mod_page = anchored
        self.mod_page = max(
            0, min(getattr(self, "mod_page", 0), len(pages) - 1))
        sections = pages[self.mod_page]
        self.mod_action_keys = dict(
            (index, param.key)
            for index, param in mod_ui.page_parameters(sections))

        preview = self._preview_parameter_context()
        commands = self.renderer.begin_page("Preview settings" if preview else "Mod settings", back=True)
        commands += parameter_ui.create_list_page(
            self.params, sections, self.mod_page, len(pages),
            mod_ui.next_category_hint(pages, self.mod_page, leading_height=mod_ui.ITEM_PITCH if preview else 0),
            preview=preview,
            leading_row=preview_ui.current_view_row(self.model_preview) if preview and self.mod_page == 0 else None,
            measure_text=self.renderer.text_width).draw(self.renderer)
        self.renderer.send(commands)

    def _open_mod_parameter(self, index, return_page=ScreenPage.MOD_SETTINGS):
        parameters = self._mod_parameters()
        rendered_key = getattr(self, "mod_action_keys", {}).get(index)
        if rendered_key is not None:
            param = next(
                (candidate for candidate in parameters
                 if candidate.key == rendered_key), None)
            if param is None:
                raise RuntimeError("Parameter is no longer available")
        else:
            if index < 0 or index >= len(parameters):
                raise RuntimeError("Parameter is no longer available")
            param = parameters[index]
        if getattr(param, "readonly", False):
            raise RuntimeError("This parameter is read-only")
        # Anchor on the first row of the current page.  Editing a parameter can
        # reveal or hide dependent rows, so the page number alone would not
        # bring the user back to the same place.
        pages = self._mod_category_pages(parameters)
        page = pages[max(0, min(getattr(self, "mod_page", 0), len(pages) - 1))]
        rows = mod_ui.page_parameters(page)
        self.mod_restore_anchor_key = rows[0][1].key if rows else None
        self.mod_return_page = return_page
        kind = mod_ui.parameter_kind(param)
        if kind == "bool":
            current = bool(self.params.variables.get(param.key, param.default))
            new_value = not current
            action = "mod.item.%d" % index
            scheduler = lambda callback, delay: self.reactor.register_callback(
                callback, self.reactor.monotonic() + delay)
            animate = getattr(self.renderer, "animate_toggle", None)
            if animate is not None:
                animate(action, mod_ui.bool_display_active(
                    param, new_value), scheduler)

            def complete():
                self._render_mod_settings()
                self._toast("UPDATED: %s" % param.label)

            self._set_mod_value(param, "1" if new_value else "0",
                                complete, minimum_duration=0.14)
            return
        self.mod_parameter = param
        self.mod_edit_value = mod_ui.current_edit_value(self.params, param)
        self.mod_edit_cursor = len(self.mod_edit_value)
        self.mod_keyboard_shift = False
        self.mod_keyboard_symbols = False
        if kind == "enum" or param.key == "feather_theme":
            if param.key == "feather_theme":
                # User files are writable at runtime. Refresh them exactly once
                # when entering the picker, then keep a stable option snapshot
                # for paging and selection. Bundled themes remain cached.
                self.renderer.reload_user_themes()
                options = self.renderer.theme_names()
                descriptions = dict(
                    (name, self.renderer.theme_description(name))
                    for name in options)
                disabled = self.renderer.user_theme_issues()
                self.mod_theme_original = self.renderer.theme_name
            else:
                options = mod_ui.enum_names(param)
                descriptions = dict(
                    (name, mod_ui.option_description(param, name))
                    for name in options)
                disabled = ()
            self._set_parameter_options(
                options, self.mod_edit_value, descriptions, disabled)
            self._show_page(ScreenPage.PARAMETER_OPTIONS)
        else:
            self.selected_parameter_option = None
            self._show_page(ScreenPage.MOD_VALUE)

    def _set_mod_value(self, param, value, complete=None,
                       minimum_duration=0.0):
        setter = getattr(self.params, "set_value", None)
        if setter is None:
            raise RuntimeError("Mod parameter API is unavailable")
        logging.info("[feather_screen] mod parameter update key=%s", param.key)
        now = self.reactor.monotonic()
        token = getattr(self, "mod_update_token", 0) + 1
        self.mod_update_token = token
        self.mod_update_pending = True
        self.mod_update_modal_visible = False
        self.mod_update_modal_at = 0.0
        self.mod_update_started = now
        self.mod_update_not_before = now + max(0.0, minimum_duration)
        self.mod_update_complete = complete
        restart_effect = mod_ui.restart_effect(param)
        previous_value = self.params.variables.get(param.key)
        block = getattr(self.renderer, "block_input", None)
        if block is not None:
            block()
        if restart_effect is None:
            self.reactor.register_callback(
                lambda eventtime, operation=token:
                self._show_mod_update_modal(eventtime, operation),
                now + 0.3)
        try:
            result = setter(param.key, value)
        except Exception:
            self.mod_update_pending = False
            self.mod_update_token += 1
            self.mod_update_complete = None
            raise
        changed = previous_value != self.params.variables.get(param.key)
        if changed and self._preview_parameter_context():
            self._apply_model_preview_setting(param.key)
        if restart_effect is not None and changed:
            # set_value() only schedules its change hook. Draw and latch the
            # restart UI synchronously before the reactor can run that hook.
            self.mod_update_pending = False
            self.mod_update_token += 1
            self.mod_update_complete = None
            logging.info(
                "[feather_screen] parameter requires %s restart key=%s",
                restart_effect, param.key)
            self._begin_restart_ui()
            return result
        self.reactor.register_callback(
            lambda eventtime, operation=token:
            self._finish_mod_update(eventtime, operation))
        return result

    def _show_mod_update_modal(self, eventtime, token):
        if (not getattr(self, "mod_update_pending", False)
                or token != getattr(self, "mod_update_token", 0)):
            return
        if getattr(self, "mod_update_modal_visible", False):
            return
        self.mod_update_modal_visible = True
        self.mod_update_modal_at = eventtime
        modal = getattr(self.renderer, "applying_modal", None)
        if modal is not None:
            modal()
        logging.info("[feather_screen] showing mod update modal")

    def _finish_mod_update(self, eventtime, token=None):
        if token is None:
            token = getattr(self, "mod_update_token", 0)
        if (not getattr(self, "mod_update_pending", False)
                or token != getattr(self, "mod_update_token", 0)):
            return
        if (not getattr(self, "mod_update_modal_visible", False)
                and eventtime - getattr(self, "mod_update_started", eventtime)
                >= 0.3):
            self._show_mod_update_modal(eventtime, token)
        deadline = getattr(self, "mod_update_not_before", 0.0)
        if getattr(self, "mod_update_modal_visible", False):
            deadline = max(deadline,
                           getattr(self, "mod_update_modal_at", 0.0) + 0.225)
        if eventtime < deadline:
            self.reactor.register_callback(
                lambda when, operation=token:
                self._finish_mod_update(when, operation),
                deadline)
            return
        self.mod_update_pending = False
        self.mod_update_modal_visible = False
        complete = getattr(self, "mod_update_complete", None)
        self.mod_update_complete = None
        logging.info("[feather_screen] mod parameter update finished")
        if complete is not None:
            complete()
        else:
            self._show_page(self.page)

    def _handle_mod_action(self, action):
        self._require_idle()
        if action == "mod.prev":
            self.mod_page = max(0, self.mod_page - 1)
            self._render_mod_settings()
            return
        # "mod.more" is the card standing in for a postponed category; it leads
        # to the page that category starts on, which is the next one.
        if action in ("mod.next", "mod.more"):
            self.mod_page += 1
            self._render_mod_settings()
            return
        if action.startswith("mod.item."):
            self._open_mod_parameter(
                int(action.rsplit(".", 1)[1]), self.page)
            return
        if action == "mod.cancel":
            self._restore_setting_preview()
            self.mod_parameter = None
            self._show_page(getattr(
                self, "mod_return_page", ScreenPage.MOD_SETTINGS))
            return
        param = self.mod_parameter
        if param is None:
            raise RuntimeError("No parameter selected")
        kind = mod_ui.parameter_kind(param)
        if action.startswith("mod.option."):
            entries = self.parameter_option_entries
            index = int(action.rsplit(".", 1)[1])
            if index < 0 or index >= len(entries):
                raise RuntimeError("Unknown option")
            option = entries[index]
            if not option.enabled:
                return
            if param.key == "feather_theme":
                self.renderer.set_theme(option.value)
            self.selected_parameter_option = option.value
            self._render_parameter_options()
            if param.key == "feather_status_bar":
                self._refresh_status_bar(style=option.value)
            return
        if action == "mod.options.prev":
            self.parameter_options_page_index = max(
                0, self.parameter_options_page_index - 1)
            self._render_parameter_options()
            return
        if action == "mod.options.next":
            self.parameter_options_page_index += 1
            self._render_parameter_options()
            return
        if action == "mod.apply":
            value = mod_ui.validate_value(param, self.selected_parameter_option)

            def complete():
                if param.key == "feather_theme":
                    self.renderer.set_theme(value)
                    self.mod_theme_original = None
                return_page = getattr(
                    self, "mod_return_page", ScreenPage.MOD_SETTINGS)
                self.mod_parameter = None
                self._show_page(return_page)
                self._toast("UPDATED: %s" % param.label)

            self._set_mod_value(param, value, complete)
            return
        if action == "mod.save":
            value = mod_ui.validate_value(param, self.mod_edit_value)

            def complete():
                return_page = getattr(
                    self, "mod_return_page", ScreenPage.MOD_SETTINGS)
                self.mod_parameter = None
                self._show_page(return_page)
                self._toast("UPDATED: %s" % param.label)

            self._set_mod_value(param, value, complete)
            return
        if kind in ("int", "float") and (
                action in ("mod.backspace", "mod.sign", "mod.dot")
                or action.startswith("mod.key.")):
            token = ({"mod.backspace": "backspace", "mod.sign": "sign",
                      "mod.dot": "decimal"}.get(action))
            if token is None:
                token = action[len("mod.key."):]
            self.mod_edit_value = mod_ui.numeric_input_spec(param).apply(
                self.mod_edit_value, token)
        elif kind == "str" and is_keyboard_action(action):
            (self.mod_edit_value, self.mod_edit_cursor,
             self.mod_keyboard_shift,
             self.mod_keyboard_symbols) = TEXT_KEYBOARD.apply(
                self.mod_edit_value, self.mod_edit_cursor, action,
                self.mod_keyboard_shift, self.mod_keyboard_symbols,
                max_length=mod_ui.MAX_VALUE_LENGTH)
        self._render_mod_value()

    def _restore_setting_preview(self):
        param = getattr(self, "mod_parameter", None)
        original = getattr(self, "mod_theme_original", None)
        if param is not None and param.key == "feather_theme" and original:
            self.renderer.set_theme(original)
        if param is not None and param.key == "feather_status_bar":
            self._refresh_status_bar(style=self._setting("feather_status_bar", "METRICS"))
        self.mod_theme_original = None
