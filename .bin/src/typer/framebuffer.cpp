// Linux framebuffer access for typer
//
// Copyright (C) 2025, Alexander K <https://github.com/drA1ex>
//
// This file may be distributed under the terms of the GNU GPLv3 license

#include "framebuffer.h"

#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <limits>
#include <sstream>
#ifdef __linux__
#include <sys/file.h>
#endif
#include <sys/mman.h>
#include <unistd.h>

#ifdef __linux__
#include <linux/fb.h>
#include <sys/ioctl.h>
#endif


namespace typer::framebuffer {
namespace {

std::string system_error(const char *message) {
    std::ostringstream stream;
    stream << message << ": " << std::strerror(errno);
    return stream.str();
}

std::string geometry_error(const char *message) {
    return std::string(message) + "; using heap backbuffer";
}

} // namespace

BufferPlan select_buffers(const Geometry &geometry,
                          uint32_t width, uint32_t height) {
    BufferPlan plan;

    if (width == 0 || height == 0 ||
        width > std::numeric_limits<std::size_t>::max() / sizeof(uint32_t) /
                    height) {
        plan.fallback_reason = geometry_error("invalid requested dimensions");
        return plan;
    }

    const auto legacy_frame_length =
        static_cast<std::size_t>(width) * height * sizeof(uint32_t);
    plan.mapping_length = legacy_frame_length;

    if (geometry.width != width || geometry.height != height ||
        geometry.virtual_width != width) {
        plan.fallback_reason = geometry_error("unexpected framebuffer dimensions");
        return plan;
    }
    if (geometry.bits_per_pixel != 32) {
        plan.fallback_reason = geometry_error("framebuffer is not 32 bpp");
        return plan;
    }
    if (geometry.line_length != width * sizeof(uint32_t)) {
        plan.fallback_reason = geometry_error("unexpected framebuffer stride");
        return plan;
    }
    if (geometry.x_offset != 0 ||
        (geometry.y_offset != 0 && geometry.y_offset != height)) {
        plan.fallback_reason = geometry_error("unsupported framebuffer offset");
        return plan;
    }
    if (geometry.virtual_height < height * 2) {
        plan.fallback_reason = geometry_error("second framebuffer page is unavailable");
        return plan;
    }

    const auto frame_length =
        static_cast<std::size_t>(geometry.line_length) * height;
    if (geometry.memory_length < frame_length * 2) {
        plan.fallback_reason = geometry_error("framebuffer memory is too small");
        return plan;
    }

    plan.use_framebuffer_backbuffer = true;
    plan.mapping_length = geometry.memory_length;
    plan.front_offset = geometry.y_offset == 0 ? 0 : frame_length;
    plan.back_offset = geometry.y_offset == 0 ? frame_length : 0;
    return plan;
}

Device::Device(const char *path, uint32_t width, uint32_t height,
               bool request_backbuffer) {
    const auto legacy_length =
        static_cast<std::size_t>(width) * height * sizeof(uint32_t);

    _fd = open(path, O_RDWR);
    if (_fd == -1) {
        _error = system_error("cannot open framebuffer device");
        return;
    }

    BufferPlan plan;
    plan.mapping_length = legacy_length;

#ifdef __linux__
    if (request_backbuffer) {
        if (flock(_fd, LOCK_EX | LOCK_NB) == -1) {
            _warning = system_error("framebuffer backbuffer is busy") +
                       "; using heap backbuffer";
        } else {
            fb_fix_screeninfo fixed{};
            fb_var_screeninfo variable{};
            if (ioctl(_fd, FBIOGET_FSCREENINFO, &fixed) == -1 ||
                ioctl(_fd, FBIOGET_VSCREENINFO, &variable) == -1) {
                _warning = system_error("unable to inspect framebuffer") +
                           "; using heap backbuffer";
            } else {
                Geometry geometry{
                    .width = variable.xres,
                    .height = variable.yres,
                    .virtual_width = variable.xres_virtual,
                    .virtual_height = variable.yres_virtual,
                    .x_offset = variable.xoffset,
                    .y_offset = variable.yoffset,
                    .bits_per_pixel = variable.bits_per_pixel,
                    .line_length = fixed.line_length,
                    .memory_length = fixed.smem_len,
                };
                plan = select_buffers(geometry, width, height);
                _warning = plan.fallback_reason;
            }
        }

        if (!plan.use_framebuffer_backbuffer) {
            // A heap-backed process must not prevent another typer instance
            // from using the hardware page.
            flock(_fd, LOCK_UN);
        }
    }
#else
    if (request_backbuffer) {
        _warning = "framebuffer inspection is unavailable; using heap backbuffer";
    }
#endif

    _mapping_length = plan.mapping_length;
    _mapping = static_cast<uint8_t *>(mmap(
        nullptr, _mapping_length, PROT_READ | PROT_WRITE, MAP_SHARED, _fd, 0));

    if (_mapping == MAP_FAILED && plan.use_framebuffer_backbuffer) {
#ifdef __linux__
        flock(_fd, LOCK_UN);
#endif
        _mapping = static_cast<uint8_t *>(mmap(
            nullptr, legacy_length, PROT_READ | PROT_WRITE, MAP_SHARED, _fd, 0));
        _mapping_length = legacy_length;
        plan = {};
        plan.mapping_length = legacy_length;
        _warning = system_error("unable to map both framebuffer pages") +
                   "; using heap backbuffer";
    }

    if (_mapping == MAP_FAILED) {
        _mapping = nullptr;
        _mapping_length = 0;
        _error = system_error("failed to map framebuffer device");
        close(_fd);
        _fd = -1;
        return;
    }

    _front = reinterpret_cast<uint32_t *>(_mapping + plan.front_offset);
    if (request_backbuffer && plan.use_framebuffer_backbuffer) {
        _back = reinterpret_cast<uint32_t *>(_mapping + plan.back_offset);
    }
}

Device::~Device() {
    if (_mapping != nullptr) {
        munmap(_mapping, _mapping_length);
    }
    if (_fd != -1) {
        close(_fd);
    }
}

} // namespace typer::framebuffer
