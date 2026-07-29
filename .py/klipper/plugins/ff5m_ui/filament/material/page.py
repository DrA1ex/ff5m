"""Declarative filament material-selection page."""

from enum import Enum

from ui.components import Button, Text
from ui.layout import Column, PageTree, Rect, Row
from ...keys import AppPage
from ..actions import select


CONTENT = Rect(12, 64, 776, 364)


class MaterialRef(Enum):
    ROOT = "filament.material.root"
    EMPTY = "filament.material.empty"


def _button(material, target, index, width, height):
    return Button(
        select(material), material, subtitle="%.0fC" % target,
        font="Roboto Bold 16pt",
        subtitle_font="JetBrainsMono Bold 12pt",
        subtitle_color="d9e4e8",
    ).size(width, height).ref("filament.material.%d" % index)


def create_page(profiles=()):
    profiles = tuple(profiles)
    if not profiles:
        root = Text(
            "NO MATERIALS ENABLED", color="56656c",
            font="JetBrainsMono Bold 12pt",
        ).ref(MaterialRef.EMPTY)
        return PageTree(root, CONTENT, page_id=AppPage.FILAMENT_MATERIAL)

    columns = 3 if len(profiles) >= 3 else max(1, len(profiles))
    gap = 20
    width = min(350, (730 - gap * (columns - 1)) // columns)
    rows = (len(profiles) + columns - 1) // columns
    height = 135 if rows <= 2 else 90
    row_nodes = []
    for row_index in range(rows):
        start = row_index * columns
        entries = profiles[start:start + columns]
        buttons = tuple(
            _button(material, target, start + index, width, height)
            for index, (material, target) in enumerate(entries))
        row_width = len(buttons) * width + max(0, len(buttons) - 1) * gap
        row_nodes.append(
            Row(*buttons, gap=gap).size(row_width, height)
            .align(horizontal="center"))
    root = Column(*row_nodes, gap=20).padding(
        left=23, top=16, right=23, bottom=16).ref(MaterialRef.ROOT)
    return PageTree(root, CONTENT, page_id=AppPage.FILAMENT_MATERIAL)

