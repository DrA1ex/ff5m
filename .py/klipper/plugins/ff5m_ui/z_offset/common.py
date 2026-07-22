## Shared Z-offset layout constants.

from ui.components import ButtonStyle
from ui.layout import Override, Rect


PAPER_STEPS = (0.005, 0.010, 0.025, 0.050)
CONTENT = Rect(0, 56, 800, 386)
FONT = "JetBrainsMono 8pt"
COMPACT_BUTTONS = ButtonStyle(font=FONT)
Z_WEIGHT_DANGER = 400.0


def compact(content):
    return Override(content).with_button_style(COMPACT_BUTTONS).apply()
