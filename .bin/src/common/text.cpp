// Text drawing
//
// Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
//
// This file may be distributed under the terms of the GNU GPLv3 license


#include "text.h"

#include <algorithm>
#include <cmath>
#include <limits>
#include <ranges>
#include <stdexcept>
#include <string>
#include <vector>

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

void TextDrawer::setBlending(bool enable) {
    _blending = enable;
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

void TextDrawer::printWrapped(const char *text, int32_t maxWidth,
                              int32_t maxHeight, bool truncateOverflow) {
    const auto lines = wrapText(text, maxWidth, maxHeight, truncateOverflow);
    std::string wrapped;
    for (std::size_t index = 0; index < lines.size(); ++index) {
        if (index > 0) wrapped += '\n';
        wrapped += lines[index];
    }
    print(wrapped.c_str());
}

void TextDrawer::printTruncated(const char *text, int32_t maxWidth) {
    const auto truncated = truncateText(text, maxWidth);
    print(truncated.c_str());
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
    const auto *found = _glyphByCode(symbol);
    if (found == nullptr) return 0;
    const Glyph &glyph = *found;

    const int32_t offsetX = cursorX + glyph.offsetX * _scaleX;
    const int32_t offsetY = cursorY + glyph.offsetY * _scaleY;

    for (uint16_t gy = 0; gy < glyph.height; ++gy) {
        for (uint16_t gx = 0; gx < glyph.width; ++gx) {
            auto index = (gy * glyph.width + gx) * _bpp;
            auto byteOffset = glyph.offset + index / 8;
            auto bitOffset = (8 - _bpp) - index % 8;

            auto pixel = (font.buffer[byteOffset] >> bitOffset) & _pixelMask;

            if (pixel == 0) continue;

            const auto coverage = (uint8_t)(pixel * 255u / _pixelMask);
            _drawGlyphPixel(offsetX + gx * _scaleX, offsetY + gy * _scaleY, coverage);
        }
    }

    // Advance the cursor
    return glyph.advanceX * _scaleX;
}

void TextDrawer::_drawGlyphPixel(int32_t x, int32_t y, uint8_t coverage) {
    uint32_t color = _color;
    if (coverage != 0xff) {
        if (_blending) {
            const auto colorAlpha = (_color >> 24) & 0xff;
            const auto effectiveAlpha = colorAlpha == 0xff
                ? (uint32_t) coverage
                : (uint32_t)((colorAlpha * coverage + 127u) / 255u);

            color = (_color & 0x00ffffffu) | (effectiveAlpha << 24);
        } else {
            color = _lerpColor(_backgroundColor, _color, coverage);
        }
    }

    if (_scaleX == 1 && _scaleY == 1) {
        setPixel(x, y, color);
    } else {
        fillRect(x, y, _scaleX, _scaleY, color);
    }
}

const Glyph *TextDrawer::_glyphByCode(uint16_t symbol) const {
    const auto &font = *this->font();
    if (font.ranges != nullptr && font.rangeCount > 0) {
        // Compact UI fonts put printable ASCII first. Keep the overwhelmingly
        // common path at one range check and one subtraction; only sparse
        // Unicode blocks pay for binary search.
        const auto &primary = font.ranges[0];
        if (symbol >= primary.codeFrom && symbol <= primary.codeTo) {
            const uint32_t index = primary.glyphOffset
                + (uint32_t)(symbol - primary.codeFrom);
            if (font.glyphCount == 0 || index < font.glyphCount) {
                return &font.glyphs[index];
            }
            return nullptr;
        }

        uint16_t left = 1;
        uint16_t right = font.rangeCount;
        while (left < right) {
            const uint16_t middle = left + (right - left) / 2;
            const auto &range = font.ranges[middle];
            if (symbol < range.codeFrom) {
                right = middle;
            } else if (symbol > range.codeTo) {
                left = middle + 1;
            } else {
                const uint32_t index = range.glyphOffset
                    + (uint32_t)(symbol - range.codeFrom);
                if (font.glyphCount > 0 && index >= font.glyphCount) {
                    return nullptr;
                }
                return &font.glyphs[index];
            }
        }
        return nullptr;
    }

    if (symbol < font.codeFrom || symbol > font.codeTo) return nullptr;
    const uint32_t index = symbol - font.codeFrom;
    if (font.glyphCount > 0 && index >= font.glyphCount) return nullptr;
    return &font.glyphs[index];
}

void TextDrawer::setPixel(int32_t x, int32_t y, uint32_t color) {
    const auto alpha = (uint8_t)(color >> 24);
    if (alpha == 0 || x < 0 || x >= _width || y < 0 || y >= _height) {
        return;
    }

    auto *buffer = _backBuffer ? _backBuffer : _screen;
    auto &destination = buffer[y * _width + x];
    
    // Most drawing commands are opaque. Keep that common path as a direct
    // write even when blending is enabled globally for the renderer.
    destination = (alpha == 0xff || !_blending)
        ? color
        : _sourceOverOpaque(destination, color);

    _markAffected(x, y, x + 1, y + 1);
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
    const auto alpha = (uint8_t)(color >> 24);
    if (alpha == 0) return; // Skip fully transparent colors

    const auto fromX = std::max(0, x);
    const auto toX = std::min(x + (int32_t) width, (int32_t) _width);
    const auto fromY = std::max(0, y);
    const auto toY = std::min(y + (int32_t) height, (int32_t) _height);

    if (fromX >= toX || fromY >= toY) return;

    auto *buffer = _backBuffer ? _backBuffer : _screen;
    if (alpha == 0xff || !_blending) {
        for (int32_t row = fromY; row < toY; ++row) {
            auto *begin = buffer + row * _width + fromX;
            std::fill(begin, begin + (toX - fromX), color);
        }
    } else {
        for (int32_t row = fromY; row < toY; ++row) {
            auto *pixel = buffer + row * _width + fromX;
            auto *const end = pixel + (toX - fromX);
            while (pixel != end) {
                *pixel = _sourceOverOpaque(*pixel, color);
                ++pixel;
            }
        }
    }

    _markAffected(fromX, fromY, toX, toY);
}

void TextDrawer::_markAffected(
    int32_t left, int32_t top, int32_t right, int32_t bottom
) {
    if (!_backBuffer || left >= right || top >= bottom) return;

    _affectedArea.left = std::min(_affectedArea.left, left);
    _affectedArea.top = std::min(_affectedArea.top, top);
    _affectedArea.right = std::max(_affectedArea.right, right);
    _affectedArea.bottom = std::max(_affectedArea.bottom, bottom);
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
        }

        const auto *found = _glyphByCode(symbol);
        if (found == nullptr) continue;
        const Glyph &glyph = *found;

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

int32_t TextDrawer::calcTextAdvance(const std::string_view &text) const {
    int32_t widest = 0;
    int32_t current = 0;
    for (const auto symbol: UTF8Reader{text}) {
        if (symbol == '\n') {
            widest = std::max(widest, current);
            current = 0;
            continue;
        }
        const auto *glyph = _glyphByCode(symbol);
        if (glyph != nullptr) current += glyph->advanceX * _scaleX;
    }
    return std::max(widest, current);
}

std::string TextDrawer::truncateText(const std::string_view &text,
                                     int32_t maxWidth) const {
    if (maxWidth <= 0 || calcTextAdvance(text) <= maxWidth) {
        return std::string(text);
    }

    const auto removeLastCodepoint = [](std::string &value) {
        if (value.empty()) return;
        auto offset = value.size() - 1;
        while (offset > 0
               && ((uint8_t) value[offset] & 0xc0) == 0x80) {
            --offset;
        }
        value.erase(offset);
    };

    std::string ellipsis = "...";
    while (!ellipsis.empty() && calcTextAdvance(ellipsis) > maxWidth) {
        ellipsis.pop_back();
    }

    auto result = std::string(text);
    while (!result.empty() && calcTextAdvance(result + ellipsis) > maxWidth) {
        removeLastCodepoint(result);
    }
    return result + ellipsis;
}

std::vector<std::string> TextDrawer::wrapText(
    const std::string_view &text, int32_t maxWidth,
    int32_t maxHeight, bool truncateOverflow) const {
    if (maxWidth <= 0) return {std::string(text)};

    const auto fits = [this, maxWidth](const std::string_view &line) {
        return calcTextAdvance(line) <= maxWidth;
    };
    const auto utf8Length = [](uint8_t firstByte) -> std::size_t {
        if (firstByte < 0x80) return 1;
        if ((firstByte & 0xe0) == 0xc0) return 2;
        if ((firstByte & 0xf0) == 0xe0) return 3;
        throw std::invalid_argument("Character too large and not supported.");
    };

    std::vector<std::string> lines;
    std::string current;
    const auto pushCurrent = [&lines, &current]() {
        lines.push_back(std::move(current));
        current.clear();
    };
    const auto appendWord = [&lines, &current, &fits, &utf8Length,
                             &pushCurrent](const std::string_view &word) {
        if (!current.empty()) {
            auto candidate = current + " " + std::string(word);
            if (fits(candidate)) {
                current = std::move(candidate);
                return;
            }
            pushCurrent();
        }

        if (fits(word)) {
            current = word;
            return;
        }

        std::size_t offset = 0;
        while (offset < word.size()) {
            auto next = offset;
            std::string chunk;
            while (next < word.size()) {
                const auto length = utf8Length((uint8_t) word[next]);
                if (next + length > word.size()) {
                    throw std::invalid_argument("Invalid UTF-8 sequence");
                }
                auto candidate = chunk + std::string(word.substr(next, length));
                if (!chunk.empty() && !fits(candidate)) break;
                chunk = std::move(candidate);
                next += length;
                if (!fits(chunk)) break;
            }
            if (next == offset) {
                const auto length = utf8Length((uint8_t) word[offset]);
                chunk = word.substr(offset, length);
                next = offset + length;
            }
            offset = next;
            if (offset < word.size()) {
                lines.push_back(std::move(chunk));
            } else {
                current = std::move(chunk);
            }
        }
    };

    std::size_t paragraphStart = 0;
    while (paragraphStart <= text.size()) {
        const auto paragraphEnd = text.find('\n', paragraphStart);
        const auto end = paragraphEnd == std::string_view::npos
            ? text.size() : paragraphEnd;
        const auto paragraph = text.substr(paragraphStart, end - paragraphStart);

        std::size_t wordStart = 0;
        bool foundWord = false;
        while (wordStart < paragraph.size()) {
            while (wordStart < paragraph.size()
                   && (paragraph[wordStart] == ' ' || paragraph[wordStart] == '\t'
                       || paragraph[wordStart] == '\r')) {
                ++wordStart;
            }
            if (wordStart >= paragraph.size()) break;
            auto wordEnd = wordStart;
            while (wordEnd < paragraph.size()
                   && paragraph[wordEnd] != ' ' && paragraph[wordEnd] != '\t'
                   && paragraph[wordEnd] != '\r') {
                ++wordEnd;
            }
            appendWord(paragraph.substr(wordStart, wordEnd - wordStart));
            foundWord = true;
            wordStart = wordEnd;
        }
        if (!current.empty()) pushCurrent();
        if (!foundWord) lines.emplace_back();

        if (paragraphEnd == std::string_view::npos) break;
        paragraphStart = paragraphEnd + 1;
    }

    if (maxHeight <= 0 || lines.empty()) return lines;

    std::size_t visible = 0;
    int32_t blockTop = std::numeric_limits<int32_t>::max();
    int32_t blockBottom = std::numeric_limits<int32_t>::min();
    const auto lineAdvance = font()->advanceY * _scaleY;
    for (std::size_t index = 0; index < lines.size(); ++index) {
        const auto boundary = calcTextBoundaries(lines[index], 0, (int32_t) index * lineAdvance);
        if (boundary.left >= boundary.right && boundary.top >= boundary.bottom) {
            ++visible;
            continue;
        }
        const auto nextTop = std::min(blockTop, boundary.top);
        const auto nextBottom = std::max(blockBottom, boundary.bottom);
        if (nextBottom - nextTop > maxHeight) break;
        blockTop = nextTop;
        blockBottom = nextBottom;
        ++visible;
    }

    if (visible >= lines.size()) return lines;
    if (visible == 0) return {};
    lines.resize(visible);
    if (truncateOverflow) {
        lines.back() = truncateText(lines.back() + "...", maxWidth);
    }
    return lines;
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

uint32_t TextDrawer::_lerpColor(uint32_t a, uint32_t b, uint8_t factor) {
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

uint32_t TextDrawer::_sourceOverOpaque(uint32_t destination, uint32_t source) {
    const auto alpha = (uint8_t)(source >> 24);
    if (alpha == 0) return destination;
    if (alpha == 0xff) return source;

    // Typer renders into an opaque framebuffer. Treat source alpha as pixel
    // coverage and blend only the three visible channels. Avoiding redundant
    // alpha-channel composition keeps the hot anti-aliasing path smaller.
    const auto inverse = (uint8_t)(0xff - alpha);
    const auto mix = [alpha, inverse](uint8_t dst, uint8_t src) {
        return (uint8_t)(
            ((uint16_t) dst * inverse + (uint16_t) src * alpha) / 255u
        );
    };

    const auto red = mix((uint8_t)(destination >> 16), (uint8_t)(source >> 16));
    const auto green = mix((uint8_t)(destination >> 8), (uint8_t)(source >> 8));
    const auto blue = mix((uint8_t) destination, (uint8_t) source);

    return 0xff000000u
        | ((uint32_t) red << 16)
        | ((uint32_t) green << 8)
        | blue;
}
