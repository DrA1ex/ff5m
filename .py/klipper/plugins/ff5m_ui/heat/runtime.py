## Runtime facade for the declarative Heat/Fan page.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
## This file may be distributed under the terms of the GNU GPLv3 license

import importlib

from ui import PAGE_CHROME_CONTRACT, SCREEN_HEIGHT, SCREEN_WIDTH
from ui.lazy import resolve_lazy_export
from .actions import HeatCommand
from .state import HeatState, HeatTargetState


_PAGES = {}
_LAZY_EXPORTS = {"HeatRef": "page", "target_editor_page": "page"}


def get_page(materials=(), renderer=None):
    materials = tuple(materials)
    width = getattr(renderer, "screen_width", SCREEN_WIDTH)
    height = getattr(renderer, "screen_height", SCREEN_HEIGHT)
    bounds = PAGE_CHROME_CONTRACT.content_bounds(width, height)
    key = materials, bounds
    page = _PAGES.get(key)
    if page is None:
        module = importlib.import_module("%s.page" % __package__)
        page = _PAGES[key] = module.create_page(materials, bounds)
    return page


def __getattr__(name):
    return resolve_lazy_export(
        globals(), name, _LAZY_EXPORTS, __package__)


__all__ = (
    "HeatCommand", "HeatRef", "HeatState", "HeatTargetState",
    "get_page", "target_editor_page",
)
