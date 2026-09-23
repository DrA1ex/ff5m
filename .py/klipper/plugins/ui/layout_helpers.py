"""Pure geometry helpers shared by Feather renderers and product surfaces."""

from .font_metrics import get_font_metrics


# Leave the same breathing room around a full-height dialog as the
# application header and persistent footer provide around shorter dialogs.
DIALOG_TOP = 74
DIALOG_BOTTOM_INSET = 58


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
                           screen_height=480):
    """Expand the panel for measured text within the content area."""
    top, bottom = DIALOG_TOP, screen_height - DIALOG_BOTTOM_INSET
    line_count = max(0, int(line_count))
    if line_count:
        last_line_bottom = 86 + (line_count - 1) * 24 + 12
        needed_height = last_line_bottom + (82 if has_buttons else 16)
    else:
        needed_height = 140 if has_buttons else 68
    new_height = min(max(int(height), needed_height), bottom - top)
    new_y = int(y) - max(0, new_height - int(height)) // 2
    new_y = max(top, min(new_y, bottom - new_height))
    return new_y, new_height


def layout_dialog_text(lines, y, width, height, has_buttons, *, page=0,
                       text_padding=28, screen_height=480,
                       font="JetBrainsMono 8pt", metrics=None):
    """Measure wrapped lines and return bounds, visible text, and page count."""
    metrics = metrics or get_font_metrics()
    text_width = max(1, int(width) - 2 * text_padding)
    wrapped = tuple(
        segment for value in lines
        for segment in metrics.wrap_text(str(value), font, text_width))
    max_height = screen_height - DIALOG_TOP - DIALOG_BOTTOM_INSET
    normal_capacity = max(1, (max_height - (82 if has_buttons else 16)
                              - 98) // 24 + 1)
    if len(wrapped) > normal_capacity:
        # Navigation occupies a row above the action buttons.
        page_size = max(1, (max_height - (120 if has_buttons else 72)
                            - 98) // 24 + 1)
        page_count = (len(wrapped) + page_size - 1) // page_size
        page = max(0, min(int(page), page_count - 1))
        visible = wrapped[page * page_size:(page + 1) * page_size]
        y, height = dialog_vertical_bounds(
            y, max_height, len(visible), has_buttons, screen_height)
    else:
        page_count, page, visible = 1, 0, wrapped
        y, height = dialog_vertical_bounds(
            y, height, len(visible), has_buttons, screen_height)
    return y, height, visible, page, page_count


def dialog_pager_bounds(x, y, width, height, has_buttons):
    """Return previous, next, and counter positions for an overflowing dialog."""
    pager_y = y + height - (114 if has_buttons else 66)
    return ((x + 28, pager_y, 52, 44),
            (x + width - 80, pager_y, 52, 44),
            (x + width // 2, pager_y + 22))
