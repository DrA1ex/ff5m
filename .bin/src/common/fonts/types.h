// Font structures declaration
//
// Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
//
// This file may be distributed under the terms of the GNU GPLv3 license

#pragma once

#include <cstdint>

// Font generator project can be found here: https://github.com/DrA1ex/font2bitmap-converter

// It's a extension of a format descried here:
// https://learn.adafruit.com/creating-custom-symbol-font-for-adafruit-gfx-library/creating-new-glyphs
// https://learn.adafruit.com/creating-custom-symbol-font-for-adafruit-gfx-library/advanced-topics

struct Glyph {
    uint32_t offset;

    uint16_t width;
    uint16_t height;

    int16_t advanceX;

    int16_t offsetX;
    int16_t offsetY;
};

struct GlyphRange {
    uint16_t codeFrom;
    uint16_t codeTo;
    uint16_t glyphOffset;
};

static_assert(sizeof(GlyphRange) == 6, "Compact font ranges must remain packed");

struct Font {
    const char *name;
    uint8_t bpp;

    uint8_t *buffer;
    Glyph *glyphs;

    uint16_t codeFrom;
    uint16_t codeTo;

    int16_t advanceY;

    // Compact fonts omit the empty glyph slots between disjoint Unicode
    // ranges. Ranges are sorted by codeFrom and map a code point to its
    // densely packed glyph index. Existing contiguous fonts leave these
    // fields at their defaults and retain direct codeFrom-based lookup.
    const GlyphRange *ranges = nullptr;
    uint16_t rangeCount = 0;
    uint16_t glyphCount = 0;
};
