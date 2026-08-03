// Boot-log screen queue
//
// Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
//
// This file may be distributed under the terms of the GNU GPLv3 license

#include "screen_queue.h"

#include <stdexcept>
#include <utility>


ScreenQueue::ScreenQueue(TextDrawer &drawer, std::size_t max_rows,
                         int32_t line_width, int32_t bottom_line_width):
    _drawer(drawer),
    _max_rows(max_rows),
    _line_width(line_width),
    _bottom_line_width(bottom_line_width) {
    if (_max_rows == 0) {
        throw std::invalid_argument("screen queue must contain at least one row");
    }
    if (_line_width <= 0 || _bottom_line_width <= 0) {
        throw std::invalid_argument("screen row widths must be positive");
    }
    if (_bottom_line_width > _line_width) {
        throw std::invalid_argument("bottom screen row cannot be wider than other rows");
    }
}

void ScreenQueue::reset(const std::vector<ScreenMessage> &messages) {
    _rows.clear();
    for (const auto &message: messages) {
        _append_wrapped(message, _line_width);
    }
    _fit_bottom_row();
    _trim();
}

void ScreenQueue::push(const ScreenMessage &message) {
    _append_wrapped(message, _line_width);
    _fit_bottom_row();
    _trim();
}

const std::deque<ScreenMessage> &ScreenQueue::rows() const {
    return _rows;
}

std::size_t ScreenQueue::max_rows() const {
    return _max_rows;
}

void ScreenQueue::_append_wrapped(const ScreenMessage &message, int32_t width) {
    for (auto &line: _drawer.wrapText(message.str, width, 0)) {
        _rows.push_back({message.log_level, std::move(line)});
    }
}

void ScreenQueue::_fit_bottom_row() {
    if (_rows.empty()) return;
    if (_drawer.calcTextBoundaries(_rows.back().str).size().first
        <= _bottom_line_width) {
        return;
    }

    auto bottom = std::move(_rows.back());
    _rows.pop_back();
    _append_wrapped(bottom, _bottom_line_width);
}

void ScreenQueue::_trim() {
    while (_rows.size() > _max_rows) {
        _rows.pop_front();
    }
}
