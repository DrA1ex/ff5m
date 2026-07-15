// Text drawing
//
// Copyright (C) 2025, Alexander K <https://github.com/drA1ex>
//
// This file may be distributed under the terms of the GNU GPLv3 license


#include "text.h"

#include <algorithm>
#include <cmath>
#include <limits>
#include <ranges>
#include <stdexcept>

#include "utf8.h"


TextDrawer::~TextDrawer() {
    flush();
    _backBuffer = nullptr;
}

void TextBoundary::offset(int32_t x, int32_t y) {
    this->left += x;
    this->right += x;
    this->start += x;

    this->top += y;
    this->bottom += y;
    this->baseline += y;
}

TextBoundary::Size TextBoundary::size() const {
    return {
        std::max(0, this->right - this->left),
        std::max(0, this->bottom - this->top)
    };
}

void TextDrawer::setFont(const Font *font) {
    _font = font;
    _bpp = font->bpp;
    _pixelMask = (1 << _bpp) - 1;
}

const Font *TextDrawer::font() const {
    if (_font == nullptr) throw std::runtime_error("Font not set");

    return _font;
}

void TextDrawer::setColor(uint32_t color) {
    _color = color;
}

void TextDrawer::setBackgroundColor(uint32_t color) {
    _backgroundColor = color;
}

void TextDrawer::setStrokeDirection(StrokeDirection value) {
    _strokeDirection = value;
}

void TextDrawer::setDoubleBuffered(bool enable, uint32_t *externalBuffer) {
    if (!enable) {
        flush();
        _backBuffer = nullptr;
        _ownedBackBuffer.reset();
        return;
    }

    if ((externalBuffer != nullptr && _backBuffer == externalBuffer) ||
        (externalBuffer == nullptr && _ownedBackBuffer &&
         _backBuffer == _ownedBackBuffer.get())) {
        return;
    }

    flush();
    if (externalBuffer != nullptr) {
        _ownedBackBuffer.reset();
        _backBuffer = externalBuffer;
    } else {
        _ownedBackBuffer = std::make_unique<uint32_t[]>(_width * _height);
        _backBuffer = _ownedBackBuffer.get();
    }

    std::copy(_screen, _screen + _width * _height, _backBuffer);
}

void TextDrawer::setPosition(int32_t x, int32_t y) {
    _cursorX = _lineBeginningX = x;
    _cursorY = y;
}

void TextDrawer::setHorizontalAlignment(HorizontalAlign align) {
    _horizontalAlign = align;
}

void TextDrawer::setVerticalAlignment(VerticalAlignment align) {
    _verticalAlign = align;
}

void TextDrawer::setFontScale(int32_t scaleX, int32_t scaleY) {
    _scaleX = std::max(1, scaleX);
    _scaleY = std::max(1, scaleY);
}

void TextDrawer::setDebug(bool enable) {
    _debug = enable;
}

void TextDrawer::print(const char *text) {
    auto lines = std::string_view(text)
        | std::views::split('\n')
        | std::views::transform([](auto rng) {
            return std::string_view(rng.data(), rng.size());
        });

    bool firstLine = true;
    for (const auto &line: lines) {
        if (!firstLine) breakLine();
        firstLine = false;

        auto b = calcTextBoundaries(line, _cursorX, _cursorY);
        if (b.left >= b.right && b.top >= b.bottom) {
            // Empty line or no supported glyphs
            continue;
        }

        fillRect(b, _backgroundColor);

        if (_debug) {
            strokeRect(b, 0xff00ff00);
            fillRect({b.left, b.baseline, b.right, b.baseline + 1}, 0xff0000ff);
        }

        _cursorX = b.start;

        for (const auto &symbol: UTF8Reader{line}) {
            _cursorX += _drawChar(symbol, _cursorX, b.baseline);
        }
    }
}

void TextDrawer::breakLine() {
    _cursorX = _lineBeginningX;
    _cursorY += font()->advanceY * _scaleY;
}

void TextDrawer::flush() {
    if (!_backBuffer
        || _affectedArea.left >= _affectedArea.right
        || _affectedArea.top >= _affectedArea.bottom) {
        return;
    }

    if (_debug) {
        strokeRect(_affectedArea, 0xffffff00);
    }

    if (_affectedArea.left < 0) _affectedArea.left = 0;
    if (_affectedArea.right > _width) _affectedArea.right = (int32_t) _width;
    if (_affectedArea.top < 0) _affectedArea.top = 0;
    if (_affectedArea.bottom > _height) _affectedArea.bottom = (int32_t) _height;

    for (uint32_t j = _affectedArea.top; j < _affectedArea.bottom; ++j) {
        auto *src = _backBuffer + j * _width;
        auto *dst = _screen + j * _width + _affectedArea.left;
        std::copy(src + _affectedArea.left, src + _affectedArea.right, dst);
    }

    _affectedArea = {};
}

int32_t TextDrawer::_drawChar(uint16_t symbol, int32_t cursorX, int32_t cursorY) {
    const auto &font = *this->font();
    if (symbol < font.codeFrom || symbol > font.codeTo) {
        return 0;
    }

    const Glyph &glyph = font.glyphs[symbol - font.codeFrom];

    const int32_t offsetX = cursorX + glyph.offsetX * _scaleX;
    const int32_t offsetY = cursorY + glyph.offsetY * _scaleY;

    for (uint16_t gy = 0; gy < glyph.height; ++gy) {
        for (uint16_t gx = 0; gx < glyph.width; ++gx) {
            auto index = (gy * glyph.width + gx) * _bpp;
            auto byteOffset = glyph.offset + index / 8;
            auto bitOffset = (8 - _bpp) - index % 8;

            auto pixel = (font.buffer[byteOffset] >> bitOffset) & _pixelMask;

            if (pixel == 0) continue;

            uint8_t factor = pixel * 255u / _pixelMask;
            auto color = _mixColor(_backgroundColor, _color, factor);

            if (_scaleX == 1 && _scaleY == 1) {
                setPixel(offsetX + gx, offsetY + gy, color);
            } else {
                fillRect(offsetX + gx * _scaleX, offsetY + gy * _scaleY, _scaleX, _scaleY, color);
            }
        }
    }

    // Advance the cursor
    return glyph.advanceX * _scaleX;
}

void TextDrawer::setPixel(int32_t x, int32_t y, uint32_t color) {
    if ((color & 0xff000000) == 0) return; // Skip fully transparent colors
    if (x < 0 || x >= _width || y < 0 || y >= _height) return;

    if (_backBuffer) {
        _backBuffer[y * _width + x] = color;

        if (_affectedArea.left > x) _affectedArea.left = x;
        if (_affectedArea.right <= x) _affectedArea.right = x + 1;
        if (_affectedArea.top > y) _affectedArea.top = y;
        if (_affectedArea.bottom <= y) _affectedArea.bottom = y + 1;
    } else {
        _screen[y * _width + x] = color;
    }
}

void TextDrawer::fillRect(const Rect &b, uint32_t color) {
    fillRect(
        b.left, b.top,
        std::max(b.right - b.left, 0),
        std::max(b.bottom - b.top, 0),
        color
    );
}

void TextDrawer::fillRect(int32_t x, int32_t y, uint32_t width, uint32_t height, uint32_t color) {
    if ((color & 0xff000000) == 0) return; // Skip fully transparent colors

    auto fromX = std::max(0, x);
    auto toX = std::min(x + (int32_t) width, (int32_t) _width);

    if (fromX >= toX) return;

    auto fromY = std::max(0, y);
    auto toY = std::min(fromY + (int32_t) height, (int32_t) _height);

    auto buffer = _backBuffer ? _backBuffer : _screen;
    for (int32_t j = fromY; j < toY; ++j) {
        auto *pRow = buffer + j * _width;
        std::fill(pRow + fromX, pRow + toX, color);
    }

    if (_backBuffer) {
        if (_affectedArea.left > fromX) _affectedArea.left = fromX;
        if (_affectedArea.right <= toX) _affectedArea.right = toX + 1;
        if (_affectedArea.top > fromY) _affectedArea.top = fromY;
        if (_affectedArea.bottom <= toY) _affectedArea.bottom = toY + 1;
    }
}

void TextDrawer::strokeRect(int32_t x, int32_t y, uint32_t width, uint32_t height, uint32_t color, uint8_t lineWidth) {
    strokeRect({x, y, (int32_t) (x + width), (int32_t) (y + height)}, color, lineWidth);
}

void TextDrawer::drawLine(int32_t x1, int32_t y1, int32_t x2, int32_t y2, uint32_t color, uint8_t lineWidth) {
    if (y1 == y2) return fillRect(std::min(x1, x2), y1 - lineWidth / 2, std::abs(x2 - x1), lineWidth, color);
    if (x1 == x2) return fillRect(x1 - lineWidth / 2, std::min(y1, y2), lineWidth, std::abs(y2 - y1), color);

    const auto dx = x2 - x1;
    const auto dy = y2 - y1;

    if (!dx || !dy) return;

    // Get normalized direction vector
    auto distance = std::sqrtf(dx * dx + dy * dy);
    const auto xDirection = (float) dx / distance;
    const auto yDirection = (float) dy / distance;

    const auto xStep = std::abs(1 / xDirection);
    const auto yStep = std::abs(1 / yDirection);

    float x = x1, y = y1;
    float distanceX = 0, distanceY = 0;
    float drawDistance = 0;
    while (drawDistance <= distance) {
        bool isX = distanceX + xStep < distanceY + yStep;
        if (isX) {
            x += std::copysignf(1.f, xDirection);
            distanceX += xStep;
            drawDistance = distanceX;
        } else {
            y += std::copysignf(1.f, yDirection);
            distanceY += yStep;
            drawDistance = distanceX;
        }

        if (lineWidth <= 1) {
            setPixel((int32_t) x, (int32_t) y, color);
        } else if (isX) {
            fillRect((int32_t) x, (int32_t) y - lineWidth / 2, 1, lineWidth, color);
        } else {
            fillRect((int32_t) x - lineWidth / 2, (int32_t) y, lineWidth, 1, color);
        }
    }
}

void TextDrawer::strokeRect(const Rect &b, uint32_t color, uint8_t lineWidth) {
    int outer, inner;
    if (_strokeDirection == StrokeDirection::OUTER) {
        outer = lineWidth;
        inner = 0;
    } else if (_strokeDirection == StrokeDirection::INNER) {
        auto maxLineWidth = std::min(b.right - b.left, b.bottom - b.top);
        auto lw = std::max(1, std::min((int32_t) lineWidth, maxLineWidth));
        outer = 0;
        inner = lw;
    } else {
        auto maxLineWidth = std::min(b.right - b.left, b.bottom - b.top) * 2;
        auto lw = std::max(1, std::min((int32_t) lineWidth, maxLineWidth));

        outer = lw / 2;
        inner = std::max(1, lw - outer);
    }

    // Left Vertical line
    fillRect({b.left - outer, b.top - outer, b.left + inner, b.bottom + outer}, color);
    // Right Vertical line
    fillRect({b.right - inner, b.top - outer, b.right + outer, b.bottom + outer}, color);
    // Top Horizontal line
    fillRect({b.left, b.top - outer, b.right, b.top + inner}, color);
    // Bottom Horizontal line
    fillRect({b.left, b.bottom - inner, b.right, b.bottom + outer}, color);
}

void TextDrawer::clear(uint32_t color) {
    fillRect(0, 0, _width, _height, color);
}

TextBoundary TextDrawer::calcTextBoundaries(const char *text, int32_t x, int32_t y) const {
    return calcTextBoundaries(std::string_view(text), x, y);
}

TextBoundary TextDrawer::calcTextBoundaries(const std::string_view &text, int32_t x, int32_t y) const {
    TextBoundary boundary = {
        .left = std::numeric_limits<int32_t>::max(),
        .top = std::numeric_limits<int32_t>::max(),
        .right = std::numeric_limits<int32_t>::min(),
        .bottom = std::numeric_limits<int32_t>::min(),
        .start = x,
        .baseline = y
    };

    auto cursorX = x;
    auto cursorY = y;

    const auto &font = *this->font();
    for (const auto &symbol: UTF8Reader{text}) {
        if (symbol == '\n') {
            cursorY = x;
            cursorY += font.advanceY * _scaleY;
        } else if (symbol < font.codeFrom || symbol > font.codeTo) {
            continue;
        }

        const Glyph &glyph = font.glyphs[symbol - font.codeFrom];

        auto left = cursorX + glyph.offsetX * _scaleX;
        auto top = cursorY + glyph.offsetY * _scaleY;

        if (left < boundary.left) boundary.left = left;
        if (top < boundary.top) boundary.top = top;

        auto right = left + glyph.width * _scaleX;
        auto bottom = top + glyph.height * _scaleY;

        if (right > boundary.right) boundary.right = right;
        if (bottom > boundary.bottom) boundary.bottom = bottom;

        cursorX += glyph.advanceX * _scaleX;
    }

    if (boundary.left > boundary.right || boundary.top > boundary.bottom) {
        boundary.left = boundary.top = boundary.right = boundary.bottom = 0;
        return boundary;
    }

    auto [offsetX, offsetY] = _getAlignmentOffset(boundary);
    boundary.offset(offsetX, offsetY);

    return boundary;
}

TextDrawer::Point TextDrawer::_getAlignmentOffset(const TextBoundary &boundary) const {
    const auto [width, height] = boundary.size();

    int32_t offsetX = 0;
    int32_t offsetY = 0;

    auto actualWidth = width - (boundary.start - boundary.left);
    if (_horizontalAlign == HorizontalAlign::CENTER) {
        offsetX = -actualWidth / 2;
    } else if (_horizontalAlign == HorizontalAlign::RIGHT) {
        offsetX = -actualWidth;
    }

    if (_verticalAlign == VerticalAlignment::TOP) {
        offsetY = boundary.baseline - boundary.bottom;
    } else if (_verticalAlign == VerticalAlignment::MIDDLE) {
        offsetY = (boundary.baseline - boundary.top) / 2;
    } else if (_verticalAlign == VerticalAlignment::BOTTOM) {
        offsetY = height;
    }

    return {offsetX, offsetY};
}

uint32_t TextDrawer::_mixColor(uint32_t a, uint32_t b, uint8_t factor) {
    if (factor == 0) return a;
    if (factor == 0xFF) return b;

    uint8_t aA = (a >> 24) & 0xFF; // Alpha
    uint8_t aR = (a >> 16) & 0xFF; // Red
    uint8_t aG = (a >> 8) & 0xFF;  // Green
    uint8_t aB = a & 0xFF;         // Blue

    uint8_t bA = (b >> 24) & 0xFF; // Alpha
    uint8_t bR = (b >> 16) & 0xFF; // Red
    uint8_t bG = (b >> 8) & 0xFF;  // Green
    uint8_t bB = b & 0xFF;         // Blue

    // Calculate the inverse factor
    uint8_t invFactor = 255 - factor;

    // Mix each channel using linear interpolation
    uint8_t mixedA = ((uint16_t) aA * invFactor + (uint16_t) bA * factor) / 255;
    uint8_t mixedR = ((uint16_t) aR * invFactor + (uint16_t) bR * factor) / 255;
    uint8_t mixedG = ((uint16_t) aG * invFactor + (uint16_t) bG * factor) / 255;
    uint8_t mixedB = ((uint16_t) aB * invFactor + (uint16_t) bB * factor) / 255;

    return (mixedA << 24) | (mixedR << 16) | (mixedG << 8) | mixedB;
}
