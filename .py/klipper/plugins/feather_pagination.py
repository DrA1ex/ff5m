## Shared pagination helpers for Feather pages
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license


class Pagination:
    """A clamped page view over an indexable collection."""

    __slots__ = (
        "items", "page_size", "total", "page_count", "page", "start", "stop")

    def __init__(self, items, page, page_size):
        if page_size <= 0:
            raise ValueError("page_size must be positive")
        self.items = items
        self.page_size = int(page_size)
        self.total = len(items)
        self.page_count = max(
            1, (self.total + self.page_size - 1) // self.page_size)
        self.page = max(0, min(int(page), self.page_count - 1))
        self.start = self.page * self.page_size
        self.stop = min(self.total, self.start + self.page_size)

    @property
    def visible(self):
        return self.items[self.start:self.stop]

    @property
    def has_previous(self):
        return self.page > 0

    @property
    def has_next(self):
        return self.page + 1 < self.page_count

    def absolute_index(self, visible_index):
        index = self.start + int(visible_index)
        return index if self.start <= index < self.stop else None


def pagination_footer(renderer, pagination, previous_action, next_action,
                      y=390, previous_x=210, next_x=440,
                      button_width=150, button_height=50,
                      previous_label="< PAGE", next_label="PAGE >",
                      center_x=400, center_y=415):
    commands = renderer.button(
        previous_action, previous_x, y, button_width, button_height,
        previous_label, active=pagination.has_previous)
    commands.append(renderer.text(
        center_x, center_y,
        "%d / %d" % (pagination.page + 1, pagination.page_count),
        "ffffff", "Roboto 8pt", "center", "middle"))
    commands += renderer.button(
        next_action, next_x, y, button_width, button_height,
        next_label, active=pagination.has_next)
    return commands
