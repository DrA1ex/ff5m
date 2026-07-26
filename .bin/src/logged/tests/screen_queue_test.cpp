// Tests for the boot-log screen queue.
//
// Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
//
// This file may be distributed under the terms of the GNU GPLv3 license

#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
#include <unistd.h>

#include "../../common/fonts/JetBrainsMono8ptb4.h"
#include "../../common/text.h"
#include "../screen_queue.h"
#include "../utils.h"


namespace {

#define CHECK(expression) \
    do { \
        if (!(expression)) { \
            throw std::runtime_error( \
                std::string("Check failed: ") + #expression); \
        } \
    } while (false)

std::vector<std::string> texts(const ScreenQueue &queue) {
    std::vector<std::string> result;
    for (const auto &row: queue.rows()) {
        result.push_back(row.str);
    }
    return result;
}

void wraps_with_shared_font_metrics() {
    std::vector<uint32_t> screen(800 * 480, 0xff000000);
    TextDrawer drawer(screen.data(), 800, 480);
    drawer.setFont(&JetBrainsMono8ptb4);
    const auto width =
        drawer.calcTextBoundaries("ONE TWO").size().first;
    ScreenQueue queue(drawer, 5, width, width);

    queue.push({LogLevel::INFO, "ONE TWO THREE FOUR"});

    CHECK((texts(queue)
        == std::vector<std::string>{"ONE TWO", "THREE", "FOUR"}));
}

void bottom_row_uses_reserved_uptime_width() {
    std::vector<uint32_t> screen(800 * 480, 0xff000000);
    TextDrawer drawer(screen.data(), 800, 480);
    drawer.setFont(&JetBrainsMono8ptb4);
    const auto normal_width =
        drawer.calcTextBoundaries("AAAAAA").size().first;
    const auto bottom_width =
        drawer.calcTextBoundaries("AAA").size().first;
    ScreenQueue queue(drawer, 5, normal_width, bottom_width);

    queue.push({LogLevel::WARN, "AAAAAA"});

    CHECK((texts(queue) == std::vector<std::string>{"AAA", "AAA"}));
    CHECK(queue.rows().back().log_level == LogLevel::WARN);
    CHECK(drawer.calcTextBoundaries(queue.rows().back().str).size().first
          <= bottom_width);
}

void rotation_counts_physical_rows() {
    std::vector<uint32_t> screen(800 * 480, 0xff000000);
    TextDrawer drawer(screen.data(), 800, 480);
    drawer.setFont(&JetBrainsMono8ptb4);
    const auto width = drawer.calcTextBoundaries("AAA").size().first;
    ScreenQueue queue(drawer, 3, width, width);

    queue.push({LogLevel::INFO, "AAAAAA"});
    queue.push({LogLevel::ERROR, "BBBBBB"});

    CHECK((texts(queue) == std::vector<std::string>{"AAA", "BBB", "BBB"}));
    CHECK(queue.rows()[0].log_level == LogLevel::INFO);
    CHECK(queue.rows()[1].log_level == LogLevel::ERROR);
    CHECK(queue.rows()[2].log_level == LogLevel::ERROR);
}

void reset_migrates_legacy_logical_messages_as_one_layout() {
    std::vector<uint32_t> screen(800 * 480, 0xff000000);
    TextDrawer drawer(screen.data(), 800, 480);
    drawer.setFont(&JetBrainsMono8ptb4);
    const auto normal_width =
        drawer.calcTextBoundaries("AAAAAA").size().first;
    const auto bottom_width =
        drawer.calcTextBoundaries("AAA").size().first;
    ScreenQueue queue(drawer, 5, normal_width, bottom_width);

    queue.reset({
        {LogLevel::INFO, "AAAAAA"},
        {LogLevel::WARN, "B"},
    });

    CHECK((texts(queue) == std::vector<std::string>{"AAAAAA", "B"}));
}

void wrapping_preserves_utf8() {
    std::vector<uint32_t> screen(800 * 480, 0xff000000);
    TextDrawer drawer(screen.data(), 800, 480);
    drawer.setFont(&JetBrainsMono8ptb4);
    const auto width = drawer.calcTextBoundaries("АБ").size().first;
    ScreenQueue queue(drawer, 5, width, width);

    queue.push({LogLevel::INFO, "АБВГД"});

    CHECK((texts(queue) == std::vector<std::string>{"АБ", "ВГ", "Д"}));
}

void queue_file_is_atomic_and_tolerates_invalid_rows() {
    const auto path = std::filesystem::temp_directory_path()
        / ("logged-screen-queue-" + std::to_string(getpid()));
    {
        std::ofstream stream(path);
        stream << "broken\n";
        stream << "9;;invalid level\n";
        stream << "2;;valid;;message\n";
    }

    const auto loaded = load_array_from_file(path.string());
    CHECK(loaded.size() == 1);
    CHECK(loaded[0].log_level == LogLevel::WARN);
    CHECK(loaded[0].str == "valid;;message");

    save_array_to_file(loaded, path.string());
    const auto saved = load_array_from_file(path.string());
    CHECK(saved.size() == 1);
    CHECK(saved[0].log_level == LogLevel::WARN);
    CHECK(saved[0].str == "valid;;message");

    std::filesystem::remove(path);
}

} // namespace

int main() {
    try {
        wraps_with_shared_font_metrics();
        bottom_row_uses_reserved_uptime_width();
        rotation_counts_physical_rows();
        reset_migrates_legacy_logical_messages_as_one_layout();
        wrapping_preserves_utf8();
        queue_file_is_atomic_and_tolerates_invalid_rows();
    } catch (const std::exception &e) {
        std::cerr << e.what() << std::endl;
        return 1;
    }

    return 0;
}
