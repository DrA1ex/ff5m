## Klipper action-prompt dialog for Feather.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from feather.screen import prompt_titles

class ActionPromptPagesMixin:
    def _render_action_prompt(self, instance):
        prompt = instance.content
        if prompt["kind"] == prompt_titles.COLD_PULL:
            self.renderer.send(self._cold_pull_status_dialog("coldpull.cancel"))
            return
        if prompt["kind"] == prompt_titles.HEATING_NOZZLE:
            temperature, target = self._nozzle_temperature_display()
            self.renderer.send(self.renderer.status_dialog(
                "HEATING NOZZLE", "NOZZLE %s / %s C" % (temperature, target)))
            return
        if prompt["kind"] == prompt_titles.FILAMENT_CHANGE:
            self.renderer.send(self.renderer.status_dialog(
                "WORKING WITH FILAMENT", "PLEASE WAIT..."))
            return
        commands = self.renderer.dialog(
            prompt["title"], tuple(prompt["text"]),
            tuple((button["action"], button["label"], button["state"])
                  for button in prompt["footer"]),
            button_groups=tuple(
                tuple((button["action"], button["label"], button["state"])
                      for button in row) for row in prompt["rows"]),
            tone="info",
            page=instance.page,
            page_actions=("prompt.prev", "prompt.next"))
        self.renderer.send(commands)
