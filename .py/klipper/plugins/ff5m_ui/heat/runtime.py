## Runtime facade for the declarative Heat/Fan page.

import importlib

from .actions import HeatCommand, LEGACY_ACTIONS
from .state import HeatState


_PAGES = {}


def get_page(materials=()):
    key = tuple(materials)
    page = _PAGES.get(key)
    if page is None:
        module = importlib.import_module("%s.page" % __package__)
        page = _PAGES[key] = module.create_page(key)
    return page


def render(renderer, materials, values):
    return get_page(materials).draw(renderer, values)


def update(renderer, materials, values):
    return get_page(materials).update(renderer, values)


def __getattr__(name):
    if name != "HeatRef":
        raise AttributeError(name)
    value = importlib.import_module("%s.page" % __package__).HeatRef
    globals()[name] = value
    return value


__all__ = (
    "HeatCommand", "HeatRef", "HeatState", "LEGACY_ACTIONS",
    "get_page", "render", "update",
)
