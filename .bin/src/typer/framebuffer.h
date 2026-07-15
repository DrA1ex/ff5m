// Linux framebuffer access for typer
//
// Copyright (C) 2025, Alexander K <https://github.com/drA1ex>
//
// This file may be distributed under the terms of the GNU GPLv3 license

#pragma once

#include <cstddef>
#include <cstdint>
#include <string>


namespace typer::framebuffer {

struct Geometry {
    uint32_t width = 0;
    uint32_t height = 0;
    uint32_t virtual_width = 0;
    uint32_t virtual_height = 0;
    uint32_t x_offset = 0;
    uint32_t y_offset = 0;
    uint32_t bits_per_pixel = 0;
    uint32_t line_length = 0;
    std::size_t memory_length = 0;
};

struct BufferPlan {
    bool use_framebuffer_backbuffer = false;
    std::size_t mapping_length = 0;
    std::size_t front_offset = 0;
    std::size_t back_offset = 0;
    std::string fallback_reason;
};

BufferPlan select_buffers(const Geometry &geometry,
                          uint32_t width, uint32_t height);

class Device {
    int _fd = -1;
    uint8_t *_mapping = nullptr;
    std::size_t _mapping_length = 0;
    uint32_t *_front = nullptr;
    uint32_t *_back = nullptr;
    std::string _error;
    std::string _warning;

public:
    Device(const char *path, uint32_t width, uint32_t height,
           bool request_backbuffer);
    ~Device();

    Device(const Device &) = delete;
    Device &operator=(const Device &) = delete;

    [[nodiscard]] bool valid() const { return _front != nullptr; }
    [[nodiscard]] uint32_t *front() const { return _front; }
    [[nodiscard]] uint32_t *back() const { return _back; }
    [[nodiscard]] bool uses_framebuffer_backbuffer() const {
        return _back != nullptr;
    }
    [[nodiscard]] const std::string &error() const { return _error; }
    [[nodiscard]] const std::string &warning() const { return _warning; }
};

} // namespace typer::framebuffer
