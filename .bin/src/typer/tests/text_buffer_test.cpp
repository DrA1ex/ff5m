// Tests for typer text buffering.
//
// Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
//
// This file may be distributed under the terms of the GNU GPLv3 license

#include <algorithm>
#include <cstdint>
#include <vector>

#include "../../common/text.h"
#include "../../common/fonts/JetBrainsMono12ptb2.h"
#include "test_runner.h"


namespace {

constexpr uint32_t WIDTH = 6;
constexpr uint32_t HEIGHT = 4;

const uint8_t compactBitmaps[] = {0x80, 0x80};
const Glyph compactGlyphs[] = {
    {0, 1, 1, 1, 0, -1},
    {1, 1, 1, 1, 0, -1},
};
const GlyphRange compactRanges[] = {
    {0x0041, 0x0041, 0},
    {0x0401, 0x0401, 1},
};
const Font compactFont = {
    "Compact test", 1,
    const_cast<uint8_t *>(compactBitmaps),
    const_cast<Glyph *>(compactGlyphs),
    0x0041, 0x0401, 2,
    compactRanges, 2, 2,
};

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

void two_bpp_font_blends_edges() {
    constexpr uint32_t width = 220;
    constexpr uint32_t height = 50;
    constexpr uint32_t background = 0xff11051d;
    constexpr uint32_t foreground = 0xffff42c8;
    std::vector<uint32_t> screen(width * height, background);
    TextDrawer drawer(screen.data(), width, height);
    drawer.setFont(&JetBrainsMono12ptb2);
    drawer.setPosition(8, 35);
    drawer.setColor(foreground);
    drawer.setBackgroundColor(background);
    drawer.print("MAIN MENU");

    const auto blended = std::find_if(
        screen.begin(), screen.end(), [](uint32_t pixel) {
            return pixel != background && pixel != foreground;
        });
    TYPER_CHECK(JetBrainsMono12ptb2.bpp == 2);
    TYPER_CHECK(blended != screen.end());
}

void compact_font_maps_disjoint_unicode_ranges() {
    std::vector<uint32_t> screen(4, 0xff000000);
    TextDrawer drawer(screen.data(), 4, 1);
    drawer.setFont(&compactFont);
    drawer.setPosition(0, 1);
    drawer.setColor(0xffffffff);
    drawer.setBackgroundColor(0);
    drawer.print("A\xd0\x81"); // ASCII A followed by Cyrillic Yo.

    TYPER_CHECK(screen[0] == 0xffffffff);
    TYPER_CHECK(screen[1] == 0xffffffff);
    TYPER_CHECK(screen[2] == 0xff000000);
}

void bundled_compact_font_renders_cyrillic() {
    constexpr uint32_t width = 320;
    constexpr uint32_t height = 48;
    constexpr uint32_t background = 0xff030607;
    std::vector<uint32_t> screen(width * height, background);
    TextDrawer drawer(screen.data(), width, height);
    drawer.setFont(&JetBrainsMono12ptb2);
    drawer.setPosition(8, 32);
    drawer.setColor(0xff69d540);
    drawer.setBackgroundColor(0);
    drawer.print("Привет, мир! Ёж");

    const auto changed = std::count_if(
        screen.begin(), screen.end(), [](uint32_t pixel) {
            return pixel != background;
        });
    TYPER_CHECK(JetBrainsMono12ptb2.rangeCount == 4);
    TYPER_CHECK(JetBrainsMono12ptb2.glyphCount == 161);
    TYPER_CHECK(changed > 100);
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
        {"two_bpp_font_blends_edges", two_bpp_font_blends_edges},
        {"compact_font_maps_disjoint_unicode_ranges", compact_font_maps_disjoint_unicode_ranges},
        {"bundled_compact_font_renders_cyrillic", bundled_compact_font_renders_cyrillic},
    });
}
