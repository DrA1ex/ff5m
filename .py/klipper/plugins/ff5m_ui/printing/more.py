## Declarative popup above the print page MORE control.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from ui import ThemeColor
from ui.components import Button, Popup
from ui.layout import Column, PageTree, Rect

from ..keys import AppPage
from .actions import PrintingAction, PrintingRoute


PAGE = PageTree(
    Popup(
        Column(
            Button(PrintingAction(PrintingRoute.MORE_FILAMENT), "Change Filament",
                   font="JetBrainsMono Bold 10pt").height(64).ref("more.filament"),
            Button(PrintingAction(PrintingRoute.MORE_EXCLUDE), "Exclude Model",
                   font="JetBrainsMono Bold 10pt").height(64).ref("more.exclude"),
            gap=8,
        ).margin(16),
        action=PrintingAction(PrintingRoute.MORE_CLOSE), border=ThemeColor.PRIMARY, line_width=3,
    ).size(308, 168).align("left", "top").offset(294, 186).ref("more.popup"),
    Rect(0, 0, 800, 480), page_id=AppPage.PRINT_MORE,
)
PAGE.title = "Print More"
