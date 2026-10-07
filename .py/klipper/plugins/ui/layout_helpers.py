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
DIALOG_STATUS_FONT = "JetBrainsMono 12pt"
DIALOG_STATUS_GAP = 20
DIALOG_LIST_FONT = "JetBrainsMono 10pt"
DIALOG_BUTTON_FONT = "JetBrainsMono Bold 12pt"
DIALOG_BUTTON_MEDIUM_FONT = "JetBrainsMono Bold 10pt"
DIALOG_BUTTON_COMPACT_FONT = "JetBrainsMono Bold 8pt"
DIALOG_MIN_WIDTH = 530
DIALOG_MIN_HEIGHT = 240
DIALOG_TITLE_TOP = 22
DIALOG_BODY_Y = 99
DIALOG_LINE_SPACING = 24
DIALOG_BODY_HALF_HEIGHT = 11
DIALOG_PAGER_RESERVE = 60
DIALOG_TITLE_LINE_SPACING = 50
DIALOG_BUTTON_HEIGHT = 50
DIALOG_BUTTON_GAP = 12
DIALOG_BUTTON_BOTTOM = 24
DIALOG_ONE_LINE_GAP = 50
DIALOG_MULTILINE_GAP = 30
# Preserve the action-prompt inset from the original one-line, one-row panel.
DIALOG_GROUP_BUTTON_BOTTOM = 30
DIALOG_COMPACT_TITLE_TOP = 32
DIALOG_COMPACT_GAP = 38
DIALOG_COMPACT_BUTTON_BOTTOM = 30
DIALOG_PAGER_RIGHT = 14
DIALOG_PAGER_ABOVE_BODY = 2
DIALOG_PAGER_MIN_HEIGHT = 334


def _button_row_width(buttons, font, measure_text, padding):
    minimum = 96 if len(buttons) > 2 else 144
    widths = (max(minimum, measure_text(str(label), font) + 2 * padding)
              for _, label, _ in buttons)
    return sum(widths) + DIALOG_BUTTON_GAP * max(0, len(buttons) - 1)


def _wrap_dialog_lines(lines, width, font, metrics):
    return tuple(segment for line in lines
                 for segment in metrics.wrap_text(str(line), font, max(1, width)))


def dialog_button_group_width(groups, *, measure_text, padding=16, margin=24):
    """Measure complete choice rows before deciding to wrap or paginate them."""
    return max((_button_row_width(group, DIALOG_BUTTON_FONT, measure_text, padding) + 2 * margin
                for group in groups if group), default=0)


def dialog_horizontal_bounds(width, title, labels, *, measure_text,
                             title_padding=40, button_padding=30,
                             button_margin=24, screen_width=800,
                             body_lines=(), text_padding=28,
                             button_groups=(), group_button_padding=16):
    """Keep the panel centered while fitting its title, actions, and body."""
    body_width = max((measure_text(segment, DIALOG_BODY_FONT)
                      for line in body_lines
                      for segment in str(line).split("\n")), default=0)
    group_width = dialog_button_group_width(
        button_groups, measure_text=measure_text,
        padding=group_button_padding, margin=button_margin)
    title_width = measure_text(str(title).upper(), DIALOG_TITLE_FONT) + 2 * title_padding
    button_width = max((measure_text(str(label), DIALOG_BUTTON_FONT)
                        + 2 * (button_padding + button_margin) for label in labels), default=0)
    required = max(DIALOG_MIN_WIDTH, int(width), title_width, button_width,
                   body_width + 2 * text_padding, group_width)
    panel_width = min(required, max(DIALOG_MIN_WIDTH, int(screen_width) - 100))
    return (int(screen_width) - panel_width) // 2, panel_width


def centered_button_row(labels, x, y, width, *, measure_text,
                        font="JetBrainsMono 8pt", height=50, padding=16,
                        minimum=144, maximum=360, gap=12, margin=18):
    """Return centered touch bounds sized for each button's own label."""
    labels = tuple(str(label) for label in labels)
    if not labels:
        return ()
    available = max(len(labels), width - 2 * margin - gap * (len(labels) - 1))
    widths = [min(maximum, max(minimum, measure_text(label, font) + 2 * padding))
              for label in labels]
    if sum(widths) > available:
        floor = min(minimum, available // len(labels))
        remaining = available - floor * len(labels)
        weights = [max(0, item - floor) for item in widths]
        total = sum(weights)
        widths = [floor + remaining * item // total for item in weights]
        # Integer division leaves fewer than one pixel per button undistributed.
        for index in range(available - sum(widths)):
            widths[index] += 1
    group_width = sum(widths) + gap * (len(labels) - 1)
    first_x = x + (width - group_width) // 2
    bounds = []
    for button_width in widths:
        bounds.append((first_x, y, button_width, height))
        first_x += button_width + gap
    return tuple(bounds)


def dialog_vertical_bounds(y, height, line_count, has_buttons,
                           screen_height=480, minimum_height=0,
                           footer_height=DIALOG_BUTTON_HEIGHT):
    """Expand the panel for measured text within the content area."""
    top, bottom = DIALOG_TOP, screen_height - DIALOG_BOTTOM_INSET
    line_count = max(0, int(line_count))
    if line_count:
        last_line_bottom = (DIALOG_BODY_Y + (line_count - 1) *
                            DIALOG_LINE_SPACING + DIALOG_BODY_HALF_HEIGHT)
        gap = DIALOG_ONE_LINE_GAP if line_count == 1 else DIALOG_MULTILINE_GAP
        bottom_space = gap + footer_height + DIALOG_BUTTON_BOTTOM if has_buttons else 16
        needed_height = last_line_bottom + bottom_space
    else:
        needed_height = 140 if has_buttons else 68
    desired_height = max(int(height), needed_height, minimum_height,
                         DIALOG_MIN_HEIGHT if line_count else 0)
    new_height = min(desired_height, bottom - top)
    new_y = int(y) - max(0, new_height - int(height)) // 2
    new_y = max(top, min(new_y, bottom - new_height))
    return new_y, new_height


def layout_title_only_dialog(title, y, width, height, has_buttons, *,
                             text_padding=28, screen_height=480,
                             metrics=None, footer_height=DIALOG_BUTTON_HEIGHT):
    """Center a wrapped title above the actions when no body text is shown."""
    metrics = metrics or get_font_metrics()
    title_lines = tuple(metrics.wrap_text(
        str(title).upper(), DIALOG_TITLE_FONT,
        max(1, int(width) - 2 * text_padding)))
    count = max(1, len(title_lines))
    title_height = metrics.metric(DIALOG_TITLE_FONT).glyph_height
    needed_height = (DIALOG_COMPACT_TITLE_TOP + title_height
                     + DIALOG_COMPACT_GAP + footer_height
                     + DIALOG_COMPACT_BUTTON_BOTTOM) if has_buttons else 88
    needed_height += (count - 1) * DIALOG_TITLE_LINE_SPACING
    if needed_height > height:
        y, height = dialog_vertical_bounds(
            y, height, 0, has_buttons, screen_height,
            minimum_height=needed_height)
    if count == 1 and has_buttons:
        first_y = y + DIALOG_COMPACT_TITLE_TOP + title_height // 2
    else:
        center_y = y + (height - (footer_height + 16 if has_buttons else 0)) // 2
        first_y = center_y - (count - 1) * DIALOG_TITLE_LINE_SPACING // 2
    return y, height, tuple(
        (line, first_y + index * DIALOG_TITLE_LINE_SPACING)
        for index, line in enumerate(title_lines))


def layout_dialog_text(lines, y, width, height, has_buttons, *, page=0,
                       text_padding=28, screen_height=480,
                       font=DIALOG_BODY_FONT, metrics=None,
                       footer_height=DIALOG_BUTTON_HEIGHT):
    """Measure wrapped lines and return bounds, visible text, and page count."""
    metrics = metrics or get_font_metrics()
    text_width = max(1, int(width) - 2 * text_padding)
    wrapped = _wrap_dialog_lines(lines, text_width, font, metrics)
    max_height = screen_height - DIALOG_TOP - DIALOG_BOTTOM_INSET
    first_line_bottom = DIALOG_BODY_Y + DIALOG_BODY_HALF_HEIGHT
    bottom_space = DIALOG_MULTILINE_GAP + footer_height + DIALOG_BUTTON_BOTTOM if has_buttons else 16
    page_size = max(1, (max_height - bottom_space - first_line_bottom) // DIALOG_LINE_SPACING + 1)
    page_count, visible, minimum_height = 1, wrapped, 0
    if len(wrapped) > page_size:
        text_width = max(1, text_width - DIALOG_PAGER_RESERVE)
        wrapped = _wrap_dialog_lines(lines, text_width, font, metrics)
        page_count = (len(wrapped) + page_size - 1) // page_size
        page = max(0, min(int(page), page_count - 1))
        visible = wrapped[page * page_size:(page + 1) * page_size]
        # The page controls need the same vertical room on every page.
        if has_buttons:
            minimum_height = DIALOG_PAGER_MIN_HEIGHT + footer_height - DIALOG_BUTTON_HEIGHT
    else:
        page = 0
    y, height = dialog_vertical_bounds(
        y, height, len(visible), has_buttons, screen_height,
        minimum_height=minimum_height, footer_height=footer_height)
    return y, height, visible, page, page_count


def dialog_pager_bounds(x, y, width, height, has_buttons,
                        footer_height=DIALOG_BUTTON_HEIGHT):
    """Stack page controls at the right edge, clear of the action row."""
    arrow_x = x + width - DIALOG_PAGER_RIGHT - 40
    return ((arrow_x, y + DIALOG_BODY_Y - DIALOG_BODY_HALF_HEIGHT
             - DIALOG_PAGER_ABOVE_BODY, 40, 44),
            (arrow_x, y + height - (footer_height + 75 if has_buttons else 76), 40, 44),
            (arrow_x + 20, y + height // 2))


def layout_dialog_body(lines, button_groups, y, width, height, has_buttons, *,
                       page=0, text_padding=28, screen_height=480, metrics=None,
                       footer_height=DIALOG_BUTTON_HEIGHT,
                       measure_text=None, normalize_font=None, button_padding=16):
    """Paginate wrapped instructions followed by fitted button rows.

    Footer actions stay available on every page. Groups share the body with
    text and leave the right edge free for page controls when needed.
    """
    groups = tuple(tuple(group) for group in button_groups if group)
    if not groups:
        y, height, visible, page, count = layout_dialog_text(
            lines, y, width, height, has_buttons, page=page,
            text_padding=text_padding, screen_height=screen_height,
            metrics=metrics, footer_height=footer_height)
        return y, height, visible, (), page, count

    metrics = metrics or get_font_metrics()
    max_height = screen_height - DIALOG_TOP - DIALOG_BOTTOM_INSET
    bottom_space = (DIALOG_MULTILINE_GAP + footer_height
                    + DIALOG_BUTTON_BOTTOM) if has_buttons else DIALOG_GROUP_BUTTON_BOTTOM
    body_bottom = max_height - bottom_space

    def paginate(text_width):
        rows = []
        for group in groups:
            if measure_text is None:
                rows.append(group)
            else:
                rows.extend(dialog_button_rows(
                    group, text_width + 2 * text_padding,
                    measure_text=measure_text, normalize_font=normalize_font, padding=button_padding))

        wrapped = _wrap_dialog_lines(lines, text_width, DIALOG_BODY_FONT, metrics)
        body_top = DIALOG_BODY_Y - DIALOG_BODY_HALF_HEIGHT
        line_height = 2 * DIALOG_BODY_HALF_HEIGHT
        page_size = max(1, (body_bottom - body_top - line_height) // DIALOG_LINE_SPACING + 1)
        pages = []
        for start in range(0, max(1, len(wrapped)), page_size):
            text_rows = wrapped[start:start + page_size]
            end = body_top + (len(text_rows) - 1) * DIALOG_LINE_SPACING + line_height if text_rows else body_top
            pages.append((text_rows, (), end))

        # Choices continue on the last text page and then fill further pages.
        text_rows, _, end = pages.pop()
        group_rows = []
        for row in rows:
            gap = DIALOG_BUTTON_GAP if group_rows else DIALOG_MULTILINE_GAP if text_rows else 0
            top = end + gap
            if top + DIALOG_BUTTON_HEIGHT > body_bottom and (text_rows or group_rows):
                pages.append((text_rows, tuple(group_rows), end))
                text_rows, group_rows, top = (), [], body_top
            group_rows.append((row, top))
            end = top + DIALOG_BUTTON_HEIGHT
        pages.append((text_rows, tuple(group_rows), end))
        return pages

    pages = paginate(max(1, width - 2 * text_padding))
    if len(pages) > 1:
        pages = paginate(max(1, width - 2 * text_padding - DIALOG_PAGER_RESERVE))
    page = max(0, min(int(page), len(pages) - 1))
    visible, visible_groups, end = pages[page]
    needed = max_height if len(pages) > 1 else end + bottom_space
    y, height = dialog_vertical_bounds(
        y, height, 0, has_buttons, screen_height, minimum_height=needed)
    if visible_groups and not has_buttons:
        # Extra height (including paginated panels) belongs above the action
        # block; its last row keeps the same bottom inset as the original panel.
        offset = height - DIALOG_GROUP_BUTTON_BOTTOM - end
        visible_groups = tuple((group, top + offset)
                               for group, top in visible_groups)
    return y, height, visible, visible_groups, page, len(pages)


def dialog_button_layout(buttons, x, y, width, *, measure_text,
                         normalize_font, padding):
    """Use the largest readable font that fits the complete action row."""
    font = DIALOG_BUTTON_FONT
    if buttons:
        available = width - 48
        for candidate in (DIALOG_BUTTON_FONT, DIALOG_BUTTON_MEDIUM_FONT,
                          DIALOG_BUTTON_COMPACT_FONT):
            font = normalize_font(candidate)
            if _button_row_width(buttons, font, measure_text, padding) <= available:
                break
    bounds = centered_button_row(
        (item[1] for item in buttons), x, y, width,
        measure_text=measure_text, font=font, padding=padding,
        minimum=96 if len(buttons) > 2 else 144, maximum=width, margin=24)
    return font, bounds


def dialog_button_rows(buttons, width, *, measure_text, normalize_font, padding):
    """Wrap only rows that would truncate even at the smallest dialog font.

    Preserve order and explicit group boundaries.
    Start wrapped rows at the preferred font, then let each row choose its
    largest fitting font through dialog_button_layout.
    """
    available = max(1, int(width) - 48)
    compact_font = normalize_font(DIALOG_BUTTON_COMPACT_FONT)
    if _button_row_width(buttons, compact_font, measure_text, padding) <= available:
        return (tuple(buttons),) if buttons else ()
    font = normalize_font(DIALOG_BUTTON_FONT)
    rows, row = [], []
    for button in buttons:
        candidate = row + [button]
        if row and _button_row_width(candidate, font, measure_text, padding) > available:
            rows.append(tuple(row))
            row = []
        row.append(button)
    if row:
        rows.append(tuple(row))
    return tuple(rows)
