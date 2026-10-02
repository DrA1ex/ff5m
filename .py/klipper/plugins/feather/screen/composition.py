## Product adapters for the shared screen composition tree.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from ui import ScreenLayer
from ui.layout import Node


class PaintSurface(Node):
    """Bridge existing page painters while the root owns their output."""

    paints_pixels = True
    covers_bounds = True

    def __init__(self, painter, modal=False, accepts_deltas=None):
        super().__init__()
        self.painter = painter
        self.modal = bool(modal)
        self._accepts_deltas = accepts_deltas

    @property
    def accepts_deltas(self):
        return self._accepts_deltas is not None and self._accepts_deltas()

    def blocks_input(self, state):
        return self.modal

    def render(self, renderer, state, layout):
        with renderer.collect() as commands:
            self.painter()
        return commands


class DialogInstance(ScreenLayer):
    """Own the accepted dialog content and its actions until explicitly closed."""

    def __init__(self, kind, content, paint_dialog, painter, actions, priority):
        self.kind = kind
        self.content = content
        self.page = 0
        self.painter = painter
        self.actions = frozenset(actions)
        self.priority = priority
        super().__init__(PaintSurface(lambda: paint_dialog(self), modal=True),
                         retain_background=True)
