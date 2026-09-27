## Klipper action-prompt dialog for Feather.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from ui import ThemeColor
from ui.layout_helpers import centered_button_row

from feather.screen.pagination import Pagination


class ActionPromptPagesMixin:
    def _render_action_prompt(self):
        if self._action_prompt_is_cold_pull():
            self._render_cold_pull_prompt()
            return
        prompt = self.action_prompt or {
            "title": "Prompt", "text": [], "rows": [], "footer": []}
        rows = prompt["rows"]
        if not rows:
            commands = self.renderer.dialog(
                prompt["title"], tuple(prompt["text"]),
                tuple((button["action"], button["label"], button["state"])
                      for button in prompt["footer"]),
                x=50, y=130, width=700, height=220, tone="info",
                page=self.action_prompt_page,
                page_actions=("prompt.prev", "prompt.next"))
            self.renderer.send(commands)
            return

        pagination = Pagination(rows, self.action_prompt_page, 3)
        self.action_prompt_page = pagination.page
        commands = self.renderer.dialog(
            prompt["title"], (), (), x=30, y=67, width=740, height=365,
            tone="info", custom_body=True)
        text = "\n".join(prompt["text"])
        if text:
            commands.append(self.renderer.text(
                400, 158, text, ThemeColor.TEXT, "JetBrainsMono 8pt",
                "center", "middle", max_width=680, max_height=76,
                wrap=True, truncate=True))

        if pagination.page_count > 1:
            commands += self.renderer.button(
                "prompt.prev", 48, 77, 70, 40, "<",
                state=("enabled" if pagination.has_previous else "disabled"),
                font="JetBrainsMono Bold 12pt")
            commands += self.renderer.button(
                "prompt.next", 682, 77, 70, 40, ">",
                state=("enabled" if pagination.has_next else "disabled"),
                font="JetBrainsMono Bold 12pt")

        for row_index, row in enumerate(pagination.visible):
            gap = 10
            margin = 48
            width = max(
                1, (704 - gap * (len(row) - 1)) // max(1, len(row)))
            y = 213 + row_index * 55
            for column, button in enumerate(row):
                commands += self.renderer.button(
                    button["action"], margin + column * (width + gap), y,
                    width, 45, button["label"], state=button["state"],
                    font="JetBrainsMono 8pt")

        footer = prompt["footer"]
        if footer:
            for button, bounds in zip(
                    footer, centered_button_row(
                        (item["label"] for item in footer), 30, 370, 740,
                        measure_text=self.renderer.text_width,
                        padding=self.renderer.BUTTON_TEXT_PADDING)):
                commands += self.renderer.button(
                    button["action"], *bounds, button["label"],
                    state=button["state"], font="JetBrainsMono 8pt")
        self.renderer.send(commands)
