"""Typed runtime state for the filament action page."""

from ui.bindings import state
from ui.identity import StateKey


class FilamentState(StateKey):
    __key_namespace__ = "ui.pages.filament.state.FilamentState"
    TEMPERATURE = state(
        float, default=0.0, minimum=0.0, mutable=False,
        unit="C", category="temperature")
    TARGET = state(
        float, default=0.0, minimum=0.0, mutable=False,
        unit="C", category="temperature")
    MATERIAL = state(str, default="", mutable=False, category="material")
    READY = state(bool, default=False, mutable=False, category="temperature")
    COOLING = state(bool, default=False, mutable=False,
                    category="temperature")
