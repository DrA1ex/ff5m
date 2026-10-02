## Klipper action-prompt dialog for Feather.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from feather.screen import prompt_titles

class ActionPromptPagesMixin:
    def _render_action_prompt(self, instance):
        prompt = instance.content
        if prompt_titles.is_cold_pull(prompt):
            self._render_cold_pull_prompt(instance)
            return
        commands = self.renderer.dialog(
            prompt["title"], tuple(prompt["text"]),
            tuple((button["action"], button["label"], button["state"])
                  for button in prompt["footer"]),
            button_groups=tuple(
                tuple((button["action"], button["label"], button["state"])
                      for button in row) for row in prompt["rows"]),
            x=50, y=130, width=700, height=220, tone="info",
            page=instance.page,
            page_actions=("prompt.prev", "prompt.next"))
        self.renderer.send(commands)
