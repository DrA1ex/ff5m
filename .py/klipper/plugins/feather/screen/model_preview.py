## Cold entry points for the optional model preview feature.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license


class ModelPreviewEntryMixin:
    @property
    def model_preview(self):
        feature = self._loaded_model_preview()
        return None if feature is None else feature.model_preview

    def _loaded_model_preview(self):
        manager = getattr(self, "feature_manager", None)
        return None if manager is None else manager.peek("model_preview")

    def cmd_FEATHER_PREVIEW_LAB(self, gcmd):
        self._require_idle()
        self.feature_manager.get("model_preview").cmd_FEATHER_PREVIEW_LAB(gcmd)

    def _handle_model_preview_tap(self):
        self._require_idle()
        now = self.reactor.monotonic()
        count = getattr(self, "_model_preview_taps", 0)
        if now > getattr(self, "_model_preview_tap_deadline", 0):
            count = 0
        self._model_preview_taps = count + 1
        self._model_preview_tap_deadline = now + 2.0
        if self._model_preview_taps == 5:
            self._model_preview_taps = 0
            self._open_model_preview_browser()

    def _open_model_preview_browser(self):
        self._require_idle()
        self.feature_manager.get("model_preview")._open_model_preview_browser()

    def _open_model_preview(self, entry):
        self._require_idle()
        self.feature_manager.get("model_preview")._open_model_preview(entry)

    def _open_selected_model_preview(self):
        self._require_idle()
        self.feature_manager.get("model_preview")._open_selected_model_preview()

    def _open_print_model_preview(self):
        self.feature_manager.get("model_preview")._open_print_model_preview()

    def _close_model_preview(self):
        feature = self._loaded_model_preview()
        if feature is not None:
            feature._close_model_preview()

    def _model_preview_render_receipt(self, receipt):
        feature = self._loaded_model_preview()
        if feature is not None:
            feature._model_preview_render_receipt(receipt)

    def _coalesce_model_preview_moves(self, lines):
        feature = self._loaded_model_preview()
        return lines if feature is None else feature._coalesce_model_preview_moves(lines)

    def _handle_model_preview_touch(self, action, phase, x, y, now):
        feature = self._loaded_model_preview()
        return False if feature is None else feature._handle_model_preview_touch(action, phase, x, y, now)

    def _handle_model_preview_action(self, action):
        feature = self._loaded_model_preview()
        if feature is not None:
            feature._handle_model_preview_action(action)

    def _render_model_preview(self):
        feature = self._loaded_model_preview()
        if feature is not None:
            feature._render_model_preview()

    def _apply_model_preview_setting(self, key):
        feature = self._loaded_model_preview()
        if feature is not None:
            feature._apply_model_preview_setting(key)

    def _update_print_model_preview(self, eventtime):
        feature = self._loaded_model_preview()
        if feature is not None:
            feature._update_print_model_preview(eventtime)
