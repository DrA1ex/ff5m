// Text drawing
//
// Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
//
// This file may be distributed under the terms of the GNU GPLv3 license

#pragma once

#include <limits>
#include <memory>
#include <string>
#include <string_view>
#include <vector>

#include "./fonts/types.h"

struct TextBoundary {
    typedef std::pair<int32_t, int32_t> Size;

    int32_t left;
    int32_t top;
    int32_t right;
    int32_t bottom;

    int32_t start;
    int32_t baseline;

    void offset(int32_t x, int32_t y);
    [[nodiscard]] Size size() const;
};

struct Rect {
    int32_t left = std::numeric_limits<int32_t>::max();
    int32_t top = std::numeric_limits<int32_t>::max();
    int32_t right = std::numeric_limits<int32_t>::min();
    int32_t bottom = std::numeric_limits<int32_t>::min();

    Rect() = default;
    Rect(int32_t left, int32_t top, int32_t right, int32_t bottom):
        left(left), top(top), right(right), bottom(bottom) {}
    Rect(const TextBoundary &boundary): // NOLINT(*-explicit-constructor)
        left(boundary.left), right(boundary.right), top(boundary.top), bottom(boundary.bottom) {}
};


enum class HorizontalAlign: uint8_t {
    LEFT   = 0,
    CENTER = 1,
    RIGHT  = 2,
};

enum class VerticalAlignment: uint8_t {
    TOP      = 0,
    MIDDLE   = 1,
    BASELINE = 2,
    BOTTOM   = 3,
};

enum class StrokeDirection: uint8_t {
    OUTER  = 0,
    MIDDLE = 1,
    INNER  = 2,
};

class TextDrawer {
    uint32_t *_screen;
    uint32_t _width;
    uint32_t _height;

    std::unique_ptr<uint32_t[]> _ownedBackBuffer;
    uint32_t *_backBuffer = nullptr;
    Rect _affectedArea{};

    uint32_t _color = 0xffffffff;
    uint32_t _backgroundColor = 0;
    const Font *_font = nullptr;

    int32_t _lineBeginningX = 0;
    int32_t _cursorX = 0;
    int32_t _cursorY = 0;

    uint8_t _scaleX = 1;
    uint8_t _scaleY = 1;

    HorizontalAlign _horizontalAlign = HorizontalAlign::LEFT;
    VerticalAlignment _verticalAlign = VerticalAlignment::BASELINE;
    StrokeDirection _strokeDirection = StrokeDirection::MIDDLE;

    uint8_t _bpp = 0;
    uint8_t _pixelMask = 0;

    bool _debug = false;

public:
    TextDrawer(uint32_t *screen, uint32_t width, uint32_t height):
        _screen(screen), _width(width), _height(height) {}

    ~TextDrawer();

    TextDrawer(TextDrawer &) = delete;
    TextDrawer &operator=(const TextDrawer &) = delete;

    [[nodiscard]] const Font *font() const;

    void setFont(const Font *font);
    void setFontScale(int32_t scaleX, int32_t scaleY);

    void setPosition(int32_t x, int32_t y);
    void setHorizontalAlignment(HorizontalAlign align);
    void setVerticalAlignment(VerticalAlignment align);

    void setColor(uint32_t color);
    void setBackgroundColor(uint32_t color);

    void setStrokeDirection(StrokeDirection value);

    void setDoubleBuffered(bool enable, uint32_t *externalBuffer = nullptr);
    void setDebug(bool enable);

    void print(const char *text);
    void printWrapped(const char *text, int32_t maxWidth, int32_t maxHeight,
                      bool truncateOverflow = false);
    void printTruncated(const char *text, int32_t maxWidth);
    void breakLine();

    void setPixel(int32_t x, int32_t y, uint32_t color);

    void fillRect(const Rect &b, uint32_t color);
    void fillRect(int32_t x, int32_t y, uint32_t width, uint32_t height, uint32_t color);

    void strokeRect(const Rect &b, uint32_t color, uint8_t lineWidth = 1);
    void strokeRect(int32_t x, int32_t y, uint32_t width, uint32_t height, uint32_t color, uint8_t lineWidth = 1);

    void drawLine(int32_t x1, int32_t y1, int32_t x2, int32_t y2, uint32_t color, uint8_t lineWidth = 1);

    void clear(uint32_t color = 0);

    void flush();

    [[nodiscard]] TextBoundary calcTextBoundaries(const std::string_view &text, int32_t x = 0, int32_t y = 0) const;
    [[nodiscard]] TextBoundary calcTextBoundaries(const char *text, int32_t x = 0, int32_t y = 0) const;
    [[nodiscard]] std::vector<std::string> wrapText(
        const std::string_view &text, int32_t maxWidth,
        int32_t maxHeight, bool truncateOverflow = false) const;
    [[nodiscard]] std::string truncateText(
        const std::string_view &text, int32_t maxWidth) const;

private:
    typedef std::pair<int32_t, int32_t> Point;

    [[nodiscard]] const Glyph *_glyphByCode(uint16_t symbol) const;
    int32_t _drawChar(uint16_t symbol, int32_t cursorX, int32_t cursorY);
    [[nodiscard]] Point _getAlignmentOffset(const TextBoundary &boundary) const;

    static uint32_t _mixColor(uint32_t a, uint32_t b, uint8_t factor);
};
