## Mod-parameter list and update flow for Feather.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import logging

from ui import ThemeColor
from ui.lazy import LazyModule
from ff5m_ui.screen import ScreenPage

from feather.screen.keyboard import TEXT_KEYBOARD, is_keyboard_action


mod_ui = LazyModule("feather.settings.mod")


class ModSettingsPagesMixin:
    def _mod_parameters(self):
        return mod_ui.visible_parameters(self.params)

    def _render_mod_settings(self, anchor_key=None):
        self._require_idle()
        parameters = self._mod_parameters()
        pages = mod_ui.category_pages(self.params, parameters)
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

        commands = self.renderer.begin_page("Mod settings", back=True)
        y = mod_ui.LIST_TOP
        for position, section in enumerate(sections):
            pitch = mod_ui.band_pitch(position)
            commands += self._mod_category_band(section, y, pitch)
            y += pitch
            for index, param in section.items:
                commands += self._mod_parameter_row(param, index, y)
                y += mod_ui.ITEM_PITCH
        upcoming = mod_ui.next_category_hint(pages, self.mod_page)
        if upcoming is not None:
            commands += self._mod_next_category_card(upcoming, y)
        commands += self._mod_scroll_rail(self.mod_page, len(pages))
        self.renderer.send(commands)

    def _mod_category_band(self, section, y, pitch):
        """Draw the in-list heading that owns the parameter rows below it."""
        label = section.label + (" (CONT.)" if section.continued else "")
        counter = "%02d-%02d / %02d" % (
            section.first, section.first + len(section.items) - 1,
            section.total)
        label_font, counter_font = "JetBrainsMono Bold 8pt", "JetBrainsMono 8pt"
        # The band sits at the bottom of its slot, so the padding of a later
        # band opens a gap above it and separates it from the rows before.
        top = y + pitch - mod_ui.BAND_HEIGHT - mod_ui.BAND_GAP_BELOW
        middle = top + mod_ui.BAND_HEIGHT // 2
        right = mod_ui.LIST_X + mod_ui.LIST_WIDTH
        label_x = mod_ui.LIST_X + 14
        label_limit = (right - self.renderer.text_width(counter, counter_font)
                       - 20 - label_x)
        rule_x = label_x + min(
            label_limit, self.renderer.text_width(label, label_font)) + 10
        return [
            self.renderer.fill(mod_ui.LIST_X, top, 4, mod_ui.BAND_HEIGHT,
                               ThemeColor.PRIMARY),
            self.renderer.text(label_x, middle, label, ThemeColor.PRIMARY,
                               label_font, "left", "middle",
                               max_width=label_limit, truncate=True),
            self.renderer.fill(rule_x, middle,
                               max(0, label_x + label_limit - rule_x), 1,
                               ThemeColor.BORDER),
            self.renderer.text(right, middle, counter, ThemeColor.DIM,
                               counter_font, "right", "middle"),
        ]

    def _mod_parameter_row(self, param, index, y):
        """Draw one parameter row together with its editing control."""
        action = "mod.item.%d" % index
        commands = [
            self.renderer.fill(mod_ui.LIST_X, y, mod_ui.LIST_WIDTH,
                               mod_ui.ITEM_HEIGHT, ThemeColor.PANEL),
            self.renderer.stroke(mod_ui.LIST_X, y, mod_ui.LIST_WIDTH,
                                 mod_ui.ITEM_HEIGHT, ThemeColor.BORDER, 1),
            self.renderer.text(40, y + 14, str(param.label).upper(),
                               ThemeColor.PRIMARY, "JetBrainsMono Bold 8pt",
                               max_width=430, truncate=True),
            self.renderer.text(40, y + 32, param.key, ThemeColor.DIM,
                               "JetBrainsMono 8pt"),
            self.renderer.text(40, y + 50, mod_ui.description(param),
                               ThemeColor.TEXT, "JetBrainsMono 8pt",
                               max_width=430, truncate=True),
        ]
        state = "disabled" if getattr(param, "readonly", False) else "enabled"
        if mod_ui.parameter_kind(param) == "bool":
            raw_value = self.params.variables.get(param.key, param.default)
            commands += self.renderer.toggle(
                action, 624, y + 13, 76, 38,
                mod_ui.bool_display_active(param, raw_value),
                enabled=state == "enabled")
        else:
            commands += self.renderer.button(
                action, 520, y + 9, 180, 46,
                mod_ui.display_value(self.params, param) + " >",
                state=state, font="JetBrainsMono 8pt")
        return commands

    def _mod_next_category_card(self, label, y):
        """Turn the space a postponed category left into the way to reach it.

        The card wears the frame of an ordinary list block so it does not pull
        attention away from the parameters, and carries the accent text of a
        category heading to show that it leads to that category.
        """
        middle = y + mod_ui.ITEM_HEIGHT // 2
        return [
            self.renderer.fill(mod_ui.LIST_X, y, mod_ui.LIST_WIDTH,
                               mod_ui.ITEM_HEIGHT, ThemeColor.PANEL),
            self.renderer.stroke(mod_ui.LIST_X, y, mod_ui.LIST_WIDTH,
                                 mod_ui.ITEM_HEIGHT, ThemeColor.BORDER, 1),
            self.renderer.text(
                mod_ui.LIST_X + mod_ui.LIST_WIDTH // 2, middle,
                "NEXT: %s >" % label, ThemeColor.TEXT,
                "JetBrainsMono Bold 8pt", "center", "middle",
                max_width=mod_ui.LIST_WIDTH - 40, truncate=True),
            self.renderer.action_hitbox(
                "mod.more", mod_ui.LIST_X, y, mod_ui.LIST_WIDTH,
                mod_ui.ITEM_HEIGHT),
        ]

    def _mod_scroll_rail(self, page, page_count):
        """Draw the page arrows and the position thumb beside the list."""
        commands = self.renderer.arrow_button(
            "mod.prev", 728, mod_ui.LIST_TOP, 52, 48, "up",
            state="enabled" if page > 0 else "disabled")
        commands += self.renderer.arrow_button(
            "mod.next", 728, 388, 52, 48, "down",
            state="enabled" if page + 1 < page_count else "disabled")
        track_y, track_height = 134, 244
        commands.append(self.renderer.stroke(749, track_y, 10, track_height,
                                             ThemeColor.BORDER, 1))
        thumb_height = max(18, track_height // page_count)
        thumb_y = (track_y if page_count == 1 else
                   track_y + (track_height - thumb_height) * page
                   // (page_count - 1))
        commands.append(self.renderer.fill(751, thumb_y + 2, 6,
                                           max(4, thumb_height - 4),
                                           ThemeColor.PRIMARY))
        return commands

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
        pages = mod_ui.category_pages(self.params, parameters)
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
                int(action.rsplit(".", 1)[1]), ScreenPage.MOD_SETTINGS)
            return
        if action == "mod.cancel":
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
            self.selected_parameter_option = option.value
            self._render_parameter_options()
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
