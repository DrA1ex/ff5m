## Klipper action-prompt dialog for Feather.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from ff5m_ui.screen import ScreenDialog


class ActionPromptPagesMixin:
    def _render_action_prompt(self):
        prompt = self._dialog_render_content(ScreenDialog.ACTION_PROMPT) or {
            "title": "Prompt", "text": [], "rows": [], "footer": []}
        if prompt["title"].strip().casefold() == "cold pull":
            self._render_cold_pull_prompt()
            return
        commands = self.renderer.dialog(
            prompt["title"], tuple(prompt["text"]),
            tuple((button["action"], button["label"], button["state"])
                  for button in prompt["footer"]),
            button_groups=tuple(
                tuple((button["action"], button["label"], button["state"])
                      for button in row) for row in prompt["rows"]),
            x=50, y=130, width=700, height=220, tone="info",
            page=self._dialog_page(ScreenDialog.ACTION_PROMPT),
            page_actions=("prompt.prev", "prompt.next"))
        self.renderer.send(commands)
