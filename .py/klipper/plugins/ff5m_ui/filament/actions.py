"""Semantic actions for the declarative filament workflow."""

from enum import Enum

from ui.actions import Command, CompletionHint, HeatingHint
from ui.identity import CommandKey


class FilamentCommand(CommandKey):
    __key_namespace__ = "ui.pages.filament.actions.FilamentCommand"
    SELECT = "filament.select"
    LOAD = "filament.load"
    UNLOAD = "filament.unload"
    PURGE = "filament.purge"
    DONE = "filament.done"
    RESUME = "filament.resume"


def select(material):
    return Command(
        FilamentCommand.SELECT, str(material),
        hint=HeatingHint("material", str(material)))


LOAD = Command(FilamentCommand.LOAD)
UNLOAD = Command(FilamentCommand.UNLOAD)
PURGE = Command(FilamentCommand.PURGE)
DONE = Command(FilamentCommand.DONE, hint=CompletionHint("filament"))
RESUME = Command(FilamentCommand.RESUME, hint=CompletionHint("print"))


LEGACY_ACTIONS = {
    FilamentCommand.LOAD: "filament.load",
    FilamentCommand.UNLOAD: "filament.unload",
    FilamentCommand.PURGE: "filament.purge",
    FilamentCommand.DONE: "filament.done",
    FilamentCommand.RESUME: "filament.resume",
}

