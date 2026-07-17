// Tests for typer text buffering.
//
// Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
//
// This file may be distributed under the terms of the GNU GPLv3 license

#include <algorithm>
#include <cstdint>
#include <vector>

#include "../../common/text.h"
#include "test_runner.h"


namespace {

constexpr uint32_t WIDTH = 6;
constexpr uint32_t HEIGHT = 4;

std::vector<uint32_t> pixels() {
    std::vector<uint32_t> result(WIDTH * HEIGHT);
    for (std::size_t index = 0; index < result.size(); ++index) {
        result[index] = 0xff000000u | static_cast<uint32_t>(index);
    }
    return result;
}

void external_buffer_is_initialized() {
    auto screen = pixels();
    std::vector<uint32_t> back(screen.size(), 0);
    TextDrawer drawer(screen.data(), WIDTH, HEIGHT);
    drawer.setDoubleBuffered(true, back.data());
    TYPER_CHECK(back == screen);
}

void external_buffer_flushes_dirty_area() {
    auto screen = pixels();
    const auto original = screen;
    std::vector<uint32_t> back(screen.size(), 0);
    TextDrawer drawer(screen.data(), WIDTH, HEIGHT);
    drawer.setDoubleBuffered(true, back.data());

    constexpr uint32_t color = 0xff35d9e6;
    drawer.fillRect(2, 1, 2, 2, color);
    TYPER_CHECK(screen == original);
    TYPER_CHECK(back[1 * WIDTH + 2] == color);
    TYPER_CHECK(back[2 * WIDTH + 3] == color);
    drawer.flush();

    for (uint32_t y = 0; y < HEIGHT; ++y) {
        for (uint32_t x = 0; x < WIDTH; ++x) {
            const auto expected = x >= 2 && x < 4 && y >= 1 && y < 3
                ? color : original[y * WIDTH + x];
            TYPER_CHECK(screen[y * WIDTH + x] == expected);
        }
    }
}

void external_buffer_is_not_owned() {
    auto screen = pixels();
    std::vector<uint32_t> back(screen.size(), 0);
    {
        TextDrawer drawer(screen.data(), WIDTH, HEIGHT);
        drawer.setDoubleBuffered(true, back.data());
        drawer.fillRect(0, 0, 1, 1, 0xffffffff);
    }
    TYPER_CHECK(back.size() == WIDTH * HEIGHT);
    back[0] = 0xff123456;
    TYPER_CHECK(back[0] == 0xff123456);
}

void internal_buffer_fallback() {
    auto screen = pixels();
    const auto original = screen;
    TextDrawer drawer(screen.data(), WIDTH, HEIGHT);
    drawer.setDoubleBuffered(true);
    drawer.fillRect(1, 1, 1, 1, 0xffabcdef);
    TYPER_CHECK(screen == original);
    drawer.flush();
    TYPER_CHECK(screen[WIDTH + 1] == 0xffabcdef);
}

void disable_flushes_and_restores_direct_drawing() {
    auto screen = pixels();
    std::vector<uint32_t> back(screen.size(), 0);
    TextDrawer drawer(screen.data(), WIDTH, HEIGHT);
    drawer.setDoubleBuffered(true, back.data());
    drawer.fillRect(0, 0, 1, 1, 0xff010203);
    drawer.setDoubleBuffered(false);
    TYPER_CHECK(screen[0] == 0xff010203);

    drawer.fillRect(1, 0, 1, 1, 0xff040506);
    TYPER_CHECK(screen[1] == 0xff040506);
}

void repeated_enable_preserves_pending_frame() {
    auto screen = pixels();
    std::vector<uint32_t> back(screen.size(), 0);
    TextDrawer drawer(screen.data(), WIDTH, HEIGHT);
    drawer.setDoubleBuffered(true, back.data());
    drawer.fillRect(0, 0, 1, 1, 0xff112233);
    drawer.setDoubleBuffered(true, back.data());
    TYPER_CHECK(screen[0] != 0xff112233);
    TYPER_CHECK(back[0] == 0xff112233);
    drawer.flush();
    TYPER_CHECK(screen[0] == 0xff112233);
}

void switching_buffers_flushes_and_resynchronizes() {
    auto screen = pixels();
    std::vector<uint32_t> first(screen.size(), 0);
    std::vector<uint32_t> second(screen.size(), 0);
    TextDrawer drawer(screen.data(), WIDTH, HEIGHT);
    drawer.setDoubleBuffered(true, first.data());
    drawer.fillRect(0, 0, 1, 1, 0xffaabbcc);
    drawer.setDoubleBuffered(true, second.data());
    TYPER_CHECK(screen[0] == 0xffaabbcc);
    TYPER_CHECK(second == screen);
}

} // namespace

int main(int argc, char **argv) {
    return typer::test::run(argc, argv, {
        {"external_buffer_is_initialized", external_buffer_is_initialized},
        {"external_buffer_flushes_dirty_area", external_buffer_flushes_dirty_area},
        {"external_buffer_is_not_owned", external_buffer_is_not_owned},
        {"internal_buffer_fallback", internal_buffer_fallback},
        {"disable_flushes_and_restores_direct_drawing", disable_flushes_and_restores_direct_drawing},
        {"repeated_enable_preserves_pending_frame", repeated_enable_preserves_pending_frame},
        {"switching_buffers_flushes_and_resynchronizes", switching_buffers_flushes_and_resynchronizes},
    });
}
