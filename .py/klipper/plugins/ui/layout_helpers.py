## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Pure geometry helpers shared by Feather renderers and product surfaces."""

from .font_metrics import get_font_metrics


# Leave the same breathing room around a full-height dialog as the
# application header and persistent footer provide around shorter dialogs.
DIALOG_TOP = 74
DIALOG_BOTTOM_INSET = 58
DIALOG_TITLE_FONT = "JetBrainsMono Bold 16pt"
DIALOG_BODY_FONT = "JetBrainsMono 8pt"
DIALOG_LIST_FONT = "JetBrainsMono 10pt"
DIALOG_BUTTON_FONT = "JetBrainsMono Bold 12pt"
DIALOG_MIN_WIDTH = 480
DIALOG_MIN_HEIGHT = 220
DIALOG_TITLE_TOP = 22
DIALOG_BODY_Y = 99
DIALOG_LINE_SPACING = 24
DIALOG_BODY_HALF_HEIGHT = 11
DIALOG_PAGER_RESERVE = 60
DIALOG_TITLE_LINE_SPACING = 50
DIALOG_BUTTON_HEIGHT = 50
DIALOG_BUTTON_BOTTOM = 24
DIALOG_ONE_LINE_GAP = 50
DIALOG_MULTILINE_GAP = 30
DIALOG_COMPACT_TITLE_TOP = 32
DIALOG_COMPACT_GAP = 38
DIALOG_COMPACT_BUTTON_BOTTOM = 30
DIALOG_PAGER_RIGHT = 14
DIALOG_PAGER_ABOVE_BODY = 2
DIALOG_PAGER_MIN_HEIGHT = 334


def dialog_horizontal_bounds(width, title, labels, *, measure_text,
                             title_padding=40, button_padding=30,
                             button_margin=24, screen_width=800,
                             body_lines=(), text_padding=28):
    """Keep the panel centered while fitting its title, actions, and body."""
    body_width = max((measure_text(segment, DIALOG_BODY_FONT)
                      for line in body_lines
                      for segment in str(line).split("\n")), default=0)
    required = min(max(DIALOG_MIN_WIDTH, int(screen_width) - 100), max(
        DIALOG_MIN_WIDTH, int(width),
        measure_text(str(title).upper(), DIALOG_TITLE_FONT) + 2 * title_padding,
        max((measure_text(str(label), DIALOG_BUTTON_FONT)
             + 2 * button_padding + 2 * button_margin for label in labels),
            default=0),
        body_width + 2 * text_padding,
    ))
    return (int(screen_width) - required) // 2, required


def centered_button_row(labels, x, y, width, *, measure_text,
                        font="JetBrainsMono 8pt", height=50, padding=16,
                        minimum=144, maximum=360, gap=12, margin=18):
    """Return equal, centered touch bounds sized for the widest label."""
    labels = tuple(str(label) for label in labels)
    if not labels:
        return ()
    label_width = max(measure_text(label, font) for label in labels)
    available = max(1, (width - 2 * margin - gap * (len(labels) - 1))
                    // len(labels))
    button_width = min(available, maximum, max(minimum, label_width + 2 * padding))
    group_width = len(labels) * button_width + gap * (len(labels) - 1)
    first_x = x + (width - group_width) // 2
    return tuple((first_x + index * (button_width + gap), y,
                  button_width, height) for index in range(len(labels)))


def dialog_vertical_bounds(y, height, line_count, has_buttons,
                           screen_height=480, minimum_height=0):
    """Expand the panel for measured text within the content area."""
    top, bottom = DIALOG_TOP, screen_height - DIALOG_BOTTOM_INSET
    line_count = max(0, int(line_count))
    if line_count:
        last_line_bottom = (DIALOG_BODY_Y + (line_count - 1) *
                            DIALOG_LINE_SPACING + DIALOG_BODY_HALF_HEIGHT)
        gap = DIALOG_ONE_LINE_GAP if line_count == 1 else DIALOG_MULTILINE_GAP
        needed_height = last_line_bottom + (
            gap + DIALOG_BUTTON_HEIGHT + DIALOG_BUTTON_BOTTOM
            if has_buttons else 16)
    else:
        needed_height = 140 if has_buttons else 68
    new_height = min(max(int(height), needed_height, minimum_height,
                         DIALOG_MIN_HEIGHT if line_count else 0),
                     bottom - top)
    new_y = int(y) - max(0, new_height - int(height)) // 2
    new_y = max(top, min(new_y, bottom - new_height))
    return new_y, new_height


def layout_title_only_dialog(title, y, width, height, has_buttons, *,
                             text_padding=28, screen_height=480,
                             metrics=None):
    """Center a wrapped title above the actions when no body text is shown."""
    metrics = metrics or get_font_metrics()
    title_lines = tuple(metrics.wrap_text(
        str(title).upper(), DIALOG_TITLE_FONT,
        max(1, int(width) - 2 * text_padding)))
    count = max(1, len(title_lines))
    title_height = metrics.metric(DIALOG_TITLE_FONT).glyph_height
    needed_height = (DIALOG_COMPACT_TITLE_TOP + title_height
                     + DIALOG_COMPACT_GAP + DIALOG_BUTTON_HEIGHT
                     + DIALOG_COMPACT_BUTTON_BOTTOM) if has_buttons else 88
    needed_height += (count - 1) * DIALOG_TITLE_LINE_SPACING
    if needed_height > height:
        y, height = dialog_vertical_bounds(
            y, height, 0, has_buttons, screen_height,
            minimum_height=needed_height)
    if count == 1 and has_buttons:
        first_y = y + DIALOG_COMPACT_TITLE_TOP + title_height // 2
    else:
        center_y = y + (height - (66 if has_buttons else 0)) // 2
        first_y = center_y - (count - 1) * DIALOG_TITLE_LINE_SPACING // 2
    return y, height, tuple(
        (line, first_y + index * DIALOG_TITLE_LINE_SPACING)
        for index, line in enumerate(title_lines))


def layout_dialog_text(lines, y, width, height, has_buttons, *, page=0,
                       text_padding=28, screen_height=480,
                       font=DIALOG_BODY_FONT, metrics=None):
    """Measure wrapped lines and return bounds, visible text, and page count."""
    metrics = metrics or get_font_metrics()
    text_width = max(1, int(width) - 2 * text_padding)
    wrapped = tuple(
        segment for value in lines
        for segment in metrics.wrap_text(str(value), font, text_width))
    max_height = screen_height - DIALOG_TOP - DIALOG_BOTTOM_INSET
    first_line_bottom = DIALOG_BODY_Y + DIALOG_BODY_HALF_HEIGHT
    vertical_capacity = max(1, (
        max_height - ((DIALOG_MULTILINE_GAP + DIALOG_BUTTON_HEIGHT
                       + DIALOG_BUTTON_BOTTOM) if has_buttons else 16)
        - first_line_bottom
    ) // DIALOG_LINE_SPACING + 1)
    if len(wrapped) > vertical_capacity:
        text_width = max(1, text_width - DIALOG_PAGER_RESERVE)
        wrapped = tuple(
            segment for value in lines
            for segment in metrics.wrap_text(str(value), font, text_width))
        page_size = vertical_capacity
        page_count = (len(wrapped) + page_size - 1) // page_size
        page = max(0, min(int(page), page_count - 1))
        visible = wrapped[page * page_size:(page + 1) * page_size]
        # The page controls need the same vertical room on every page.
        y, height = dialog_vertical_bounds(
            y, height, len(visible), has_buttons, screen_height,
            minimum_height=DIALOG_PAGER_MIN_HEIGHT if has_buttons else 0)
    else:
        page_count, page, visible = 1, 0, wrapped
        y, height = dialog_vertical_bounds(
            y, height, len(visible), has_buttons, screen_height)
    return y, height, visible, page, page_count


def dialog_pager_bounds(x, y, width, height, has_buttons):
    """Stack page controls at the right edge, clear of the action row."""
    arrow_x = x + width - DIALOG_PAGER_RIGHT - 40
    return ((arrow_x, y + DIALOG_BODY_Y - DIALOG_BODY_HALF_HEIGHT
             - DIALOG_PAGER_ABOVE_BODY, 40, 44),
            (arrow_x, y + height - (125 if has_buttons else 76), 40, 44),
            (arrow_x + 20, y + height // 2))
