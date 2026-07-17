// Minimal test runner for the typer test suite.
//
// Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
//
// This file may be distributed under the terms of the GNU GPLv3 license

#pragma once

#include <exception>
#include <functional>
#include <initializer_list>
#include <iostream>
#include <stdexcept>
#include <string>
#include <string_view>

namespace typer::test {

struct Case {
    std::string_view name;
    std::function<void()> body;
};

[[noreturn]] inline void fail(const char *expression, const char *file, int line) {
    throw std::runtime_error(std::string(file) + ":" + std::to_string(line) +
                             ": check failed: " + expression);
}

inline int run(int argc, char **argv, std::initializer_list<Case> cases) {
    const std::string_view selected = argc > 1 ? argv[1] : std::string_view{};
    bool found = selected.empty();
    int failures = 0;
    for (const auto &test: cases) {
        if (!selected.empty() && selected != test.name) continue;
        found = true;
        try {
            test.body();
            std::cout << "[PASS] " << test.name << '\n';
        } catch (const std::exception &error) {
            std::cerr << "[FAIL] " << test.name << ": " << error.what() << '\n';
            ++failures;
        } catch (...) {
            std::cerr << "[FAIL] " << test.name << ": unknown exception\n";
            ++failures;
        }
    }
    if (!found) {
        std::cerr << "Unknown test case: " << selected << '\n';
        return 2;
    }
    return failures ? 1 : 0;
}

}  // namespace typer::test

#define TYPER_CHECK(expression) \
    do { if (!(expression)) typer::test::fail(#expression, __FILE__, __LINE__); } while (false)
