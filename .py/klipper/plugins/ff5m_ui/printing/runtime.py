## Runtime facade for the declarative active-print page.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import importlib

from ui.lazy import resolve_lazy_export

from .actions import PrintingAction, PrintingRoute
from .state import PrintingState


_LAZY_EXPORTS = {
    "PREVIEW_IMAGE_PADDING": "page",
    "PrintingRef": "page",
}


def _page_module():
    return importlib.import_module("%s.page" % __package__)


def get_page():
    return _page_module().PAGE


def render(renderer, values):
    return get_page().draw(renderer, values)


def update(renderer, values):
    return get_page().update(renderer, values)


def update_progress(renderer, values):
    module = _page_module()
    page = module.PAGE
    for ref in (
            module.PrintingRef.PROGRESS,
            module.PrintingRef.ELAPSED,
            module.PrintingRef.REMAINING,
            module.PrintingRef.LAYER,
            module.PrintingRef.HEIGHT):
        page.invalidate(ref)
    return page.update(renderer, values)


def rect(ref):
    return get_page().rect(ref)


def __getattr__(name):
    return resolve_lazy_export(globals(), name, _LAZY_EXPORTS, __package__)


__all__ = (
    "PREVIEW_IMAGE_PADDING", "PrintingAction", "PrintingRef",
    "PrintingRoute", "PrintingState", "get_page", "rect", "render",
    "update", "update_progress",
)
