## Klipper action-prompt dialog for Feather.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from feather.screen import prompt_titles
from ff5m_ui import dialogs as dialog_ui


def _shown(button):
    return button["action"], button["label"], button["state"]


class ActionPromptPagesMixin:
    def _action_prompt_page(self, content):
        if content["kind"] in prompt_titles.STATUS_KINDS:
            return dialog_ui.status_page(
                self._status_prompt_values(content["kind"]), cancel_action=dialog_ui.CANCEL_COLD_PULL)
        return dialog_ui.dialog_page(
            content["title"], tuple(content["text"]), tuple(_shown(button) for button in content["footer"]),
            button_groups=tuple(tuple(_shown(button) for button in row) for row in content["rows"]), tone="info")

    def _status_prompt_values(self, kind):
        """What a status prompt shows now, as dialog state."""
        State = dialog_ui.DialogState
        if kind == prompt_titles.FILAMENT_CHANGE:
            return {State.TITLE: "WORKING WITH FILAMENT", State.DETAIL: "PLEASE WAIT..."}
        if kind == prompt_titles.HEATING_NOZZLE:
            return {State.TITLE: "HEATING NOZZLE", State.DETAIL: "NOZZLE %s / %s C" % self._nozzle_temperature_display()}
        stage, temperature, target, cancel_available, cancel_pending = self._cold_pull_display()
        return {State.TITLE: stage or "STARTING COLD PULL", State.DETAIL: "NOZZLE %s / %s C" % (temperature, target),
                State.CANCELLABLE: cancel_available,
                State.CANCEL_LABEL: "CANCELLING..." if cancel_pending else "CANCEL",
                State.CANCEL_STATE: "busy" if cancel_pending else "danger"}

    def _refresh_status_prompt(self, instance):
        """Show the live values of a status prompt: its layer's state, also while something covers it, so it comes
        back current. A cancel button that comes or goes opens the dialog again, once it is on top."""
        values = self._status_prompt_values(instance.content["kind"])
        State = dialog_ui.DialogState
        if values.get(State.CANCELLABLE, False) == instance.node.state[State.CANCELLABLE]:
            self._ensure_screen_root().write_layer(instance, values)
        elif instance is self._current_dialog_instance():
            self._show_dialog(instance.kind, content=instance.content)
