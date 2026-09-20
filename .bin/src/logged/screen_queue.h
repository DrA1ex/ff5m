// Boot-log screen queue
//
// Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
//
// This file may be distributed under the terms of the GNU GPLv3 license

#pragma once

#include <cstddef>
#include <cstdint>
#include <deque>
#include <vector>

#include "../common/text.h"
#include "types.h"


class ScreenQueue {
    TextDrawer &_drawer;
    std::size_t _max_rows;
    int32_t _line_width;
    int32_t _bottom_line_width;
    std::deque<ScreenMessage> _rows;

public:
    ScreenQueue(TextDrawer &drawer, std::size_t max_rows,
                int32_t line_width, int32_t bottom_line_width);

    void reset(const std::vector<ScreenMessage> &messages);
    void push(const ScreenMessage &message);

    [[nodiscard]] const std::deque<ScreenMessage> &rows() const;
    [[nodiscard]] std::size_t max_rows() const;

private:
    void _append_wrapped(const ScreenMessage &message, int32_t width);
    void _fit_bottom_row();
    void _trim();
};
