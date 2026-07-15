#pragma once

#include <string>
#include <string_view>
#include <vector>

namespace typer::batch {

std::vector<std::string> tokenize(std::string_view input);
std::vector<std::vector<std::string>> split_commands(
    const std::vector<std::string> &tokens);

}  // namespace typer::batch
