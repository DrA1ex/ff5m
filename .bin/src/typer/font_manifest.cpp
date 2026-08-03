// Deterministic font metrics manifest used by Feather layout clients.

#include "font_manifest.h"

#include <algorithm>
#include <cstdint>
#include <limits>
#include <sstream>
#include <string_view>

namespace typer::font_manifest {
namespace {

std::size_t glyph_count(const Font &font) {
    return font.glyphCount != 0
        ? font.glyphCount
        : (std::size_t)(font.codeTo - font.codeFrom) + 1;
}

void quoted(std::ostringstream &output, std::string_view value) {
    output << '"';
    for (const auto ch: value) {
        switch (ch) {
            case '"': output << "\\\""; break;
            case '\\': output << "\\\\"; break;
            case '\b': output << "\\b"; break;
            case '\f': output << "\\f"; break;
            case '\n': output << "\\n"; break;
            case '\r': output << "\\r"; break;
            case '\t': output << "\\t"; break;
            default:
                if ((uint8_t) ch < 0x20) {
                    constexpr char hex[] = "0123456789abcdef";
                    output << "\\u00"
                           << hex[((uint8_t) ch >> 4) & 0xf]
                           << hex[(uint8_t) ch & 0xf];
                } else {
                    output << ch;
                }
        }
    }
    output << '"';
}

void ranges(std::ostringstream &output, const Font &font) {
    output << '[';
    if (font.ranges != nullptr && font.rangeCount != 0) {
        for (uint16_t index = 0; index < font.rangeCount; ++index) {
            if (index != 0) output << ',';
            output << '[' << font.ranges[index].codeFrom << ','
                   << font.ranges[index].codeTo << ']';
        }
    } else {
        output << '[' << font.codeFrom << ',' << font.codeTo << ']';
    }
    output << ']';
}

}  // namespace

std::string build(const std::map<std::string, const Font *> &fonts) {
    std::ostringstream output;
    output << "{\"schema\":\"font-metrics/v1\","
              "\"wrap_algorithm\":\"word-v1\",\"fonts\":[";
    bool first_font = true;
    for (const auto &[name, pointer]: fonts) {
        if (pointer == nullptr) continue;
        const auto &font = *pointer;
        const auto count = glyph_count(font);
        if (count == 0) continue;

        auto top = std::numeric_limits<int32_t>::max();
        auto bottom = std::numeric_limits<int32_t>::min();
        const auto first_advance = font.glyphs[0].advanceX;
        bool monospaced = true;
        for (std::size_t index = 0; index < count; ++index) {
            const auto &glyph = font.glyphs[index];
            top = std::min(top, (int32_t) glyph.offsetY);
            bottom = std::max(bottom, (int32_t) glyph.offsetY + glyph.height);
            monospaced = monospaced && glyph.advanceX == first_advance;
        }

        if (!first_font) output << ',';
        first_font = false;
        output << "{\"name\":";
        quoted(output, name);
        output << ",\"advance_x\":";
        if (monospaced) {
            output << first_advance;
        } else {
            output << '[';
            for (std::size_t index = 0; index < count; ++index) {
                if (index != 0) output << ',';
                output << font.glyphs[index].advanceX;
            }
            output << ']';
        }
        output << ",\"monospaced\":" << (monospaced ? "true" : "false")
               << ",\"advance_y\":" << font.advanceY
               << ",\"glyph_bounds\":{\"top\":" << top
               << ",\"bottom\":" << bottom << "},\"unicode_ranges\":";
        ranges(output, font);
        output << '}';
    }
    output << "]}\n";
    return output.str();
}

}  // namespace typer::font_manifest
