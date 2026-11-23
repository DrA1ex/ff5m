"""Common geometry and briefing card for calibration pages."""

# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
# This file may be distributed under the terms of the GNU GPLv3 license.

from ui import Column, Frame, Rect, Text

from .styles import UiStyle


CONTENT = Rect(0, 56, 800, 386)
SIDE_INSET = 40
VERTICAL_INSET = 18


def briefing(heading, text, *, height=108):
    return Frame(Column(
        Text(heading).style(UiStyle.PRIMARY_LEFT).height(22),
        Text(text, wrap=True).style(UiStyle.TEXT_LEFT).height("content").ref("briefing.body"),
        gap=6,
    )).style(UiStyle.FRAME_PANEL).height(height).padding(left=20, right=20, top=16, bottom=16)
