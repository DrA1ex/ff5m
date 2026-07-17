// Batch command protocol for the typer utility.
//
// Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
//
// This file may be distributed under the terms of the GNU GPLv3 license

#include "batch_protocol.h"

#include <stdexcept>
#include <utility>

namespace typer::batch {

std::vector<std::string> tokenize(std::string_view input) {
    std::vector<std::string> tokens;
    std::string current;
    char quote = 0;
    bool escaped = false;
    bool token_started = false;

    for (char ch: input) {
        if (escaped) {
            current += ch;
            escaped = false;
            token_started = true;
        } else if (ch == '\\') {
            escaped = true;
            token_started = true;
        } else if (quote) {
            if (ch == quote) quote = 0;
            else current += ch;
        } else if (ch == '"' || ch == '\'') {
            quote = ch;
            token_started = true;
        } else if (ch == ' ' || ch == '\t' || ch == '\r' || ch == '\n' || ch == '\0') {
            if (token_started) {
                tokens.push_back(std::move(current));
                current.clear();
                token_started = false;
            }
        } else {
            current += ch;
            token_started = true;
        }
    }

    if (escaped || quote) throw std::invalid_argument("Incomplete quoted batch");
    if (token_started) tokens.push_back(std::move(current));
    return tokens;
}

std::vector<std::vector<std::string>> split_commands(
        const std::vector<std::string> &tokens) {
    if (tokens.empty()) return {};

    std::vector<std::vector<std::string>> result;
    result.push_back({"--batch"});
    for (const auto &token: tokens) {
        if (token == "--batch") {
            if (result.back().size() > 1) {
                result.push_back({"--batch"});
            }
        } else {
            result.back().push_back(token);
        }
    }
    if (!result.empty() && result.back().size() == 1) result.pop_back();
    return result;
}

}  // namespace typer::batch
