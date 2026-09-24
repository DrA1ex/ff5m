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


def _changed_refs(module, page, values):
    ref = module.PrintingRef
    targets = {
        PrintingState.FILENAME: (ref.FILENAME,),
        PrintingState.STATUS: (ref.STATUS,),
        PrintingState.PROGRESS: (ref.PROGRESS, ref.PROGRESS_VALUE),
        PrintingState.ELAPSED: (ref.ELAPSED,),
        PrintingState.REMAINING: (ref.REMAINING,),
        PrintingState.LAYER: (ref.LAYER,),
        PrintingState.HEIGHT: (ref.HEIGHT,),
        PrintingState.PAUSED: (ref.PAUSE,),
        PrintingState.CONTROLS_READY: (ref.PAUSE, ref.FILAMENT),
        PrintingState.PENDING_ACTION: (ref.PAUSE,),
        PrintingState.LIVE_Z_ALLOWED: (ref.Z_ADJUST,),
        PrintingState.PREVIEW_STATUS: (ref.PREVIEW,),
    }
    if any(key not in targets for key in values):
        return None
    return tuple(dict.fromkeys(
        target for key, value in values.items()
        if value != page.state.get(key)
        for target in targets[key]))


def render(renderer, values, reuse_layout=False):
    module = _page_module()
    page = module.PAGE
    refs = (_changed_refs(module, page, values)
            if reuse_layout and all(key in values for key in page.state)
            else None)
    return page.draw(renderer, values, reuse_layout=reuse_layout,
                     refs=refs, reuse_styles=refs is not None)


def update(renderer, values):
    return get_page().update(renderer, values)


def update_progress(renderer, values):
    module = _page_module()
    page = module.PAGE
    refs = _changed_refs(module, page, values)
    if refs is None:
        return page.update(renderer, values)
    return page.update_refs(renderer, values, refs)


def rect(ref):
    return get_page().rect(ref)


def __getattr__(name):
    return resolve_lazy_export(globals(), name, _LAZY_EXPORTS, __package__)


__all__ = (
    "PREVIEW_IMAGE_PADDING", "PrintingAction", "PrintingRef",
    "PrintingRoute", "PrintingState", "get_page", "rect", "render",
    "update", "update_progress",
)
