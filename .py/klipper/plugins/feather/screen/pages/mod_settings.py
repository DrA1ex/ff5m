## Mod-parameter list and update flow for Feather.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import logging

from ui.lazy import LazyModule
from ff5m_ui.screen import ScreenPage



mod_ui = LazyModule("feather.settings.mod")
keyboard_ui = LazyModule("feather.screen.keyboard")
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

    def _mod_list_geometry(self):
        bounds = parameter_ui.content_bounds(self.renderer, kind="list")
        width = parameter_ui.list_width(bounds)
        leading_height = preview_ui.current_view_pitch(self.model_preview, width) if self._preview_parameter_context() else 0
        if width >= 600:
            return bounds, leading_height, mod_ui.ITEM_PITCH
        heights = {}

        def item_pitch(index, param):
            if param.key not in heights:
                heights[param.key] = parameter_ui.parameter_pitch(param, width)
            return heights[param.key]

        return bounds, leading_height, item_pitch

    def _mod_category_pages(self, parameters, geometry=None):
        preview = self._preview_parameter_context()
        bounds, leading_height, item_pitch = geometry or self._mod_list_geometry()
        return mod_ui.category_pages(self.params, parameters,
                                     label="PRINT PREVIEW" if preview else None,
                                     leading_height=leading_height, capacity=bounds.height, item_pitch=item_pitch)

    def _mod_pagination(self):
        """The shown parameters, the list geometry and its category pages.

        Visibility and pagination walk every mod parameter (about 1,300 Python calls), so the mod list keeps them
        until an input changes: the manager's parameters, categories or values, or the list bounds. A write that
        changes none of them, such as a toggle's animation frame or a page turn, reuses them. The preview list has
        a few rows and is computed each time. Callers only read what this returns.
        """
        if self._preview_parameter_context():
            parameters = self._mod_parameters()
            geometry = self._mod_list_geometry()
            return parameters, geometry, self._mod_category_pages(parameters, geometry)
        manager = self.params
        inputs = (manager.params, getattr(manager, "ui_categories_map", None),
                  parameter_ui.content_bounds(self.renderer, kind="list"), manager.variables)
        cached = getattr(self, "_mod_pagination_cache", None)
        if cached is None or cached[0] != inputs:
            parameters = self._mod_parameters()
            geometry = self._mod_list_geometry()
            # The values are a snapshot: the manager changes its dict in place.
            cached = self._mod_pagination_cache = (
                inputs[:3] + (dict(manager.variables),),
                (parameters, geometry, self._mod_category_pages(parameters, geometry)))
        return cached[1]

    def _mod_list_tree(self):
        """The list the screen shows: its templates follow its rows when they are written."""
        bounds = parameter_ui.content_bounds(self.renderer, kind="list")
        preview = self._preview_parameter_context()
        tree = getattr(self, "_mod_tree", None)
        if tree is None or getattr(self, "_mod_tree_variant", None) != (preview, bounds):
            tree = self._mod_tree = parameter_ui.create_list_tree(preview=preview, bounds=bounds, kinds=())
            self._mod_tree_variant = preview, bounds
        return tree

    def _mod_settings_content(self):
        title = "Preview settings" if self._preview_parameter_context() else "Mod settings"
        return self._mod_list_tree(), title, True

    def _write_mod_settings_values(self):
        self._require_idle()
        _parameters, geometry, pages = self._mod_pagination()
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
        bounds, leading_height, item_pitch = geometry
        state = parameter_ui.list_state(
            self.params, sections, self.mod_page, len(pages),
            mod_ui.next_category_hint(pages, self.mod_page, leading_height=leading_height,
                                      capacity=bounds.height, item_pitch=item_pitch),
            preview_ui.current_view_content(self.model_preview) if preview and self.mod_page == 0 else None)
        moving = self.__dict__.get("mod_toggle_motion", {})
        for row in state[parameter_ui.ParameterListState.ROWS]:
            if row.get("parameter") in moving:
                row["active"], row["progress"] = moving[row["parameter"]]
        tree = self._mod_list_tree()
        parameter_ui.ensure_list_templates(tree, state)
        tree.state.update(state)

    def _move_mod_toggle(self, key, active, frames=4, duration=0.12):
        """Move a toggle's thumb toward active in timed writes of the list, as
        the renderer animates a toggle it drew; the last one leaves the toggle
        showing the parameter's value."""
        moving = self.__dict__.setdefault("mod_toggle_motion", {})

        def move(_eventtime, index):
            if index < frames:
                moving[key] = active, index / frames
            else:
                moving.pop(key, None)
            if self.page in (ScreenPage.MOD_SETTINGS, ScreenPage.PREVIEW_SETTINGS):
                self._paint_page_values()

        move(None, 1)
        now = self.reactor.monotonic()
        for index in range(2, frames + 1):
            self.reactor.register_callback(lambda eventtime, step=index: move(eventtime, step),
                                           now + duration * (index - 1) / (frames - 1))

    def _render_mod_settings(self, anchor_key=None):
        if anchor_key is not None:
            self.mod_restore_anchor_key = anchor_key
        self._render_screen()

    def _open_mod_parameter(self, index, return_page=ScreenPage.MOD_SETTINGS):
        parameters, _geometry, pages = self._mod_pagination()
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
        page = pages[max(0, min(getattr(self, "mod_page", 0), len(pages) - 1))]
        rows = mod_ui.page_parameters(page)
        self.mod_restore_anchor_key = rows[0][1].key if rows else None
        if mod_ui.parameter_kind(param) == "bool":
            self.mod_return_page = return_page
            current = bool(self.params.variables.get(param.key, param.default))
            new_value = not current
            self._move_mod_toggle(param.key, mod_ui.bool_display_active(param, new_value))

            def complete():
                self._render_mod_settings()
                self._toast("UPDATED: %s" % param.label)

            self._set_mod_value(param, "1" if new_value else "0",
                                complete, minimum_duration=0.14)
            return
        self.open_parameter_editor(param, return_page)

    def open_parameter_editor(self, param, return_page):
        self.mod_return_page = return_page
        self.mod_parameter = param
        self.mod_edit_value = mod_ui.current_edit_value(self.params, param)
        self.mod_edit_cursor = len(self.mod_edit_value)
        self.mod_keyboard_shift = False
        self.mod_keyboard_symbols = False
        if mod_ui.parameter_kind(param) == "enum" or param.key == "feather_theme":
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
            if getattr(self, "mod_return_page", None) in (ScreenPage.SETUP_OPTIONS, ScreenPage.SETUP_NETWORK):
                self.feature_manager.get("setup").progress.record_options({param.key: result})
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
        self._render_screen()
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
            self._cancel_parameter_editor()
            return
        param = self.mod_parameter
        if param is None:
            raise RuntimeError("No parameter selected")
        kind = mod_ui.parameter_kind(param)
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
                if return_page == ScreenPage.SETUP_NETWORK:
                    try:
                        self.feature_manager.get("setup").go_to_intro("done")
                    except OSError as exc:
                        self._show_message(str(exc), self.page, title="Setup progress could not be saved")
                    return
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
        elif kind == "str" and keyboard_ui.is_keyboard_action(action):
            (self.mod_edit_value, self.mod_edit_cursor,
             self.mod_keyboard_shift,
             self.mod_keyboard_symbols) = keyboard_ui.TEXT_KEYBOARD.apply(
                self.mod_edit_value, self.mod_edit_cursor, action,
                self.mod_keyboard_shift, self.mod_keyboard_symbols,
                max_length=mod_ui.MAX_VALUE_LENGTH)
        self._render_mod_value()

    def _choose_parameter_option(self, index):
        """Stage the picker's option at index until apply; a theme or a status
        bar style shows at once."""
        self._require_idle()
        param = self.mod_parameter
        if param is None:
            raise RuntimeError("No parameter selected")
        entries = self.parameter_option_entries
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

    def _cancel_parameter_editor(self):
        return_page = getattr(self, "mod_return_page", ScreenPage.MOD_SETTINGS)
        if return_page == ScreenPage.SETUP_NETWORK:
            self.feature_manager.get("setup").go_to_intro("network")
        else:
            self._restore_setting_preview()
            self.mod_parameter = None
            self._show_page(return_page)

    def _restore_setting_preview(self):
        param = getattr(self, "mod_parameter", None)
        original = getattr(self, "mod_theme_original", None)
        if param is not None and param.key == "feather_theme" and original:
            self.renderer.set_theme(original)
        if param is not None and param.key == "feather_status_bar":
            self._refresh_status_bar(style=self._setting("feather_status_bar", "METRICS"))
        self.mod_theme_original = None
