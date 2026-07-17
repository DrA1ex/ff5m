// Batch command protocol interfaces for the typer utility.
//
// Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
//
// This file may be distributed under the terms of the GNU GPLv3 license

#pragma once

#include <string>
#include <string_view>
#include <vector>

namespace typer::batch {

std::vector<std::string> tokenize(std::string_view input);
std::vector<std::vector<std::string>> split_commands(
    const std::vector<std::string> &tokens);

}  // namespace typer::batch
