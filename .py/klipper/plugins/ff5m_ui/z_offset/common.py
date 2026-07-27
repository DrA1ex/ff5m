## Shared Z-offset layout constants.

from ui.components import ButtonStyle
from ui.layout import Override, Rect
from .constants import PAPER_STEPS, Z_WEIGHT_DANGER


CONTENT = Rect(0, 56, 800, 386)
FONT = "JetBrainsMono 8pt"
COMPACT_BUTTONS = ButtonStyle(font=FONT)


def compact(content):
    return Override(content).with_button_style(COMPACT_BUTTONS).apply()
