// UTF8 Reader class
//
// Copyright (C) 2025, Alexander K <https://github.com/drA1ex>
//
// This file may be distributed under the terms of the GNU GPLv3 license

#pragma once

#include <string_view>

class UTF8Reader {
    const std::string_view &str;
    std::size_t pos = 0;

public:
    explicit UTF8Reader(const std::string_view &input) : str(input) {}

    struct Iterator {
        const std::string_view &str;
        std::size_t pos;

        uint16_t operator*() const {
            if (pos >= str.size()) {
                throw std::out_of_range("Iterator out of range");
            }

            auto first_byte = (uint8_t) str[pos];

            // 1-byte character
            if (first_byte < 0x80) {
                return first_byte;
            }

            // 2-byte character
            if ((first_byte & 0xE0) == 0xC0) {
                if (pos + 1 >= str.size()) throw std::invalid_argument("Invalid UTF-8 sequence");

                return (uint16_t) ((first_byte & 0x1F) << 6
                    | (uint16_t) str[pos + 1] & 0x3F);
            }

            // 3-byte character
            if ((first_byte & 0xF0) == 0xE0) {
                if (pos + 2 >= str.size()) throw std::invalid_argument("Invalid UTF-8  sequence");

                return (uint16_t) ((first_byte & 0x0F) << 12
                    | ((uint8_t) str[pos + 1] & 0x3F) << 6
                    | (uint8_t) str[pos + 2] & 0x3F);
            }

            throw std::invalid_argument("Character too large and not supported.");
        }

        Iterator &operator++() {
            if (pos >= str.size()) throw std::out_of_range("Iterator out of range");
            auto first_byte = (uint8_t) str[pos];

            if (first_byte < 0x80) {
                pos += 1;
            } else if ((first_byte & 0xE0) == 0xC0) {
                pos += 2;
            } else if ((first_byte & 0xF0) == 0xE0) {
                pos += 3;
            } else {
                throw std::invalid_argument("Character too large and not supported.");
            }

            return *this;
        }

        bool operator!=(const Iterator &other) const {
            return pos != other.pos;
        }
    };

    [[nodiscard]] Iterator begin() const {
        return Iterator{str, 0};
    }

    [[nodiscard]] Iterator end() const {
        return Iterator{str, str.size()};
    }
};
