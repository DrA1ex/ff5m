## Shared product layout for modal numeric editors.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
## This file may be distributed under the terms of the GNU GPLv3 license

from ui import FLEX, Back, Button, Column, Frame, Grid, PageTree, Popup, Span, Text, ThemeColor, derived
from ui.font_metrics import get_font_metrics
from ui.header import WAKE

from .keys import AppPage


def create_keypad_popup(title, value, actions, bounds, *, subtitle, spec, confirm_label):
    title, subtitle = title.upper(), subtitle.upper()
    metrics = get_font_metrics()
    label_width = max(metrics.text_width(title, "JetBrainsMono Bold 8pt"),
                      metrics.text_width(subtitle, "JetBrainsMono 8pt"))
    width, height = min(bounds.width, max(440, label_width + 44)), min(bounds.height, 366)
    padding, gap = 10, 8
    content_width = width - 2 * padding
    digit_columns = ((content_width - 2 * gap) // 3,) * 3
    digit_grid_width = sum(digit_columns) + 2 * gap

    def key(token, label, state="keypad", font="JetBrainsMono 12pt"):
        action = actions.get(token)
        return Button(action or WAKE, label, state=state if action is not None else "disabled", font=font)

    title_text = Text(title, color=ThemeColor.TEXT, font="JetBrainsMono Bold 8pt", truncate=True)
    heading = title_text
    if subtitle:
        heading = Column(title_text.height(20),
                         Text(subtitle, color=ThemeColor.DIM, font="JetBrainsMono 8pt", truncate=True).height(24))
    heading.padding(left=12, right=12)

    decimal = spec.allows_decimal and "decimal" in actions
    sign = spec.allows_negative and "sign" in actions
    readout = Frame(
        Text(derived(lambda draft: draft or "_", value), font="JetBrainsMono 12pt",
             color=ThemeColor.BRIGHT, horizontal="left", truncate=True),
        border=ThemeColor.SECONDARY, line_width=2,
    ).padding(left=16, right=16)
    if decimal and sign:
        readout = Grid(((Span(readout, columns=2), None, key("sign", "+/-", "keypad_aux")),),
                       columns=digit_columns, gap=gap).width(digit_grid_width).align(horizontal="left")

    zero = key("0", "0")
    back = key("backspace", "BACK", "keypad_aux", "JetBrainsMono 8pt")
    if decimal:
        bottom = (key("decimal", ".", "keypad_aux"), zero, back)
    elif sign:
        bottom = (key("sign", "+/-", "keypad_aux"), zero, back)
    else:
        bottom = (Span(zero, columns=2), None, back)
    digits = Grid(tuple(tuple(key(digit, digit) for digit in row) for row in ("123", "456", "789")) + (bottom,),
                  columns=digit_columns, gap=(gap, 6)).width(digit_grid_width).align(horizontal="left")

    confirm = derived(lambda draft: "keypad_confirm" if spec.is_valid(draft) else "disabled", value)
    footer = Grid(((key("cancel", "CANCEL", "danger", "JetBrainsMono Bold 10pt"),
                    key("confirm", confirm_label, confirm, "JetBrainsMono Bold 10pt")),),
                  columns=((content_width - gap) // 2, FLEX), gap=gap)
    keypad = Grid(((heading,), (readout,), (None,), (digits,), (None,), (footer,)),
                  columns=(FLEX,), rows=(48, 44, 8, FLEX, 8, 48))
    popup = Popup(keypad, action=WAKE, border=ThemeColor.BORDER, line_width=1).size(width, height) \
        .align("center", "center").padding(left=padding, top=8, right=padding, bottom=8)
    return PageTree(popup, bounds, page_id=AppPage.DIALOG, actions=(Back(),))
