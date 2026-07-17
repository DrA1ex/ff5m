// Interactive input interfaces for the typer utility.
//
// Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
//
// This file may be distributed under the terms of the GNU GPLv3 license

#pragma once

#include <cstdint>
#include <functional>
#include <optional>
#include <string>
#include <string_view>

namespace typer::interactive {

using FrameHandler = std::function<void(std::string_view)>;

struct TouchPoint {
    int x;
    int y;
};

enum class TouchPhase {
    PRESS,
    MOVE,
    RELEASE,
};

struct TouchReport {
    TouchPhase phase;
    TouchPoint point;
    TouchPoint start;
    bool tap;
};

class TouchTracker {
public:
    std::optional<TouchPoint> process(uint16_t type, uint16_t code, int32_t value);
    std::optional<TouchReport> process_report(
        uint16_t type, uint16_t code, int32_t value);

private:
    int x_ = 0, y_ = 0, start_x_ = 0, start_y_ = 0, max_distance_ = 0;
    int reported_x_ = 0, reported_y_ = 0;
    bool down_ = false, starting_ = false, released_ = false;
};

void register_hitbox(int x, int y, int width, int height, std::string action,
                     bool continuous = false);
void clear_hitboxes();
[[nodiscard]] std::string action_at(int x, int y);
[[nodiscard]] bool continuous_at(int x, int y);
void request_stop();

void run(const std::string &draw_pipe,
         const std::string &touch_device,
         const std::string &event_pipe,
         const FrameHandler &frame_handler,
         bool debug);

}  // namespace typer::interactive
