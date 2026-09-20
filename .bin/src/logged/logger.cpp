// Logger impl
//
// Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
//
// This file may be distributed under the terms of the GNU GPLv3 license

#include "logger.h"

#include <algorithm>
#include <fcntl.h>
#include <filesystem>
#include <fstream>
#include <format>
#include <iostream>
#include <limits>
#include <string>
#include <unistd.h>
#include <vector>
#include <sys/mman.h>

#include "types.h"
#include "utils.h"

#include "../common/text.h"
#include "../common/fonts/JetBrainsMono8ptb4.h"

Logger::Logger(LoggerParams config): _config(std::move(config)) {
    if (_config.send_to_screen) {
        try {
            _init_drawer();
        } catch (const std::exception &e) {
            std::cerr << "Warning: screen output disabled: "
                      << e.what() << std::endl;
        }
    }
}

Logger::~Logger() {
    if (_fbp != nullptr) {
        _screen_queue = nullptr;
        _drawer = nullptr;

        munmap(_fbp, WIDTH * HEIGHT * 4);
        close(_fb_descriptor);

        _fbp = nullptr;
        _fb_descriptor = -1;
    }
}

void Logger::process_stream(std::istream &input_stream) {
    auto pid = getppid();
    const auto exec_name = get_exe_name(pid);

    std::string line;
    while (std::getline(input_stream, line)) {
        auto date_str = current_date_time();

        std::string level_str = "DEBUG";
        LogLevel level = _config.print_level;

        // Check log levels
        if (line.starts_with("@@")) {
            level_str = "ERROR";
            level = LogLevel::ERROR;
            line = line.substr(2);
        } else if (line.starts_with("??")) {
            level_str = "WARN ";
            level = LogLevel::WARN;
            line = line.substr(2);
        } else if (line.starts_with("//")) {
            level_str = "INFO ";
            level = LogLevel::INFO;
            line = line.substr(2);
        }

        line.erase(0, line.find_first_not_of(" \t"));

        auto formatted_message = _config.log_format;
        replace_placeholder(formatted_message, "%date%", date_str);
        replace_placeholder(formatted_message, "%level%", level_str);
        replace_placeholder(formatted_message, "%pid%", std::to_string(pid));
        replace_placeholder(formatted_message, "%script%", exec_name);
        replace_placeholder(formatted_message, "%message%", line);

        if (_config.print && level >= _config.print_level) {
            if (_config.print_formatted) {
                std::cout << formatted_message << std::endl;
            } else {
                std::cout << line << std::endl;
            }
        }

        if (_drawer != nullptr && !line.empty()
            && level >= _config.screen_level) {
            try {
                _process_screen_message(level, line);
            } catch (const std::exception &e) {
                std::cerr << "Warning: unable to update boot screen: "
                          << e.what() << std::endl;
            }
        }

        if (_config.log && level >= _config.log_level) {
            std::ofstream log_stream(_config.log_file, std::ios_base::app);
            log_stream << formatted_message << std::endl;
        }
    }

}

void Logger::_process_screen_message(
    LogLevel level, const std::string &message) {
    FileLock lock(_config.screen_lock_file);

    if (_config.screen_followup) {
        _screen_queue->reset(load_array_from_file(_config.screen_follow_up_file));
    }

    _screen_queue->push({level, message});
    if (_config.screen_followup) {
        const auto &rows = _screen_queue->rows();
        save_array_to_file({rows.begin(), rows.end()}, _config.screen_follow_up_file);
    }

    _send_to_screen();
}

void Logger::_send_to_screen() {
    constexpr int bottom_offset = 460;
    const auto line_height = (int32_t) _drawer->font()->advanceY;
    const auto &messages = _screen_queue->rows();

    const int y_clear = std::max(0, bottom_offset - (int32_t) _screen_queue->max_rows() * line_height);
    _drawer->fillRect(0, y_clear, WIDTH, HEIGHT - y_clear, 0xff000000);

    _drawer->setHorizontalAlignment(HorizontalAlign::LEFT);
    _drawer->setVerticalAlignment(VerticalAlignment::MIDDLE);

    const int height =
        ((int32_t) messages.size() - 1) * line_height;
    int y_offset = bottom_offset - height;
    for (const auto &message: messages) {
        _drawer->setColor(message.color());
        _drawer->setPosition(10, y_offset);
        _drawer->print(message.str.c_str());
        y_offset += line_height;
    }

    _drawer->setColor(0xff00ffff);
    _drawer->setPosition(WIDTH - 10, bottom_offset);
    _drawer->setHorizontalAlignment(HorizontalAlign::RIGHT);

    timespec t{};
#ifdef CLOCK_BOOTTIME
    clock_gettime(CLOCK_BOOTTIME, &t);
#else
    clock_gettime(CLOCK_MONOTONIC, &t);
#endif
    _drawer->print(std::format("<< {:.2f}", t.tv_sec + t.tv_nsec / 1e+9f).c_str());

    _drawer->flush();
}


void Logger::_init_drawer() {
    if (_config.screen_queue_max == 0 || _config.screen_queue_max > MAX_SCREEN_QUEUE_ROWS) {
        throw std::invalid_argument("Invalid screen queue size");
    }

    int fbfd = open("/dev/fb0", O_RDWR);
    if (fbfd == -1) {
        throw std::runtime_error("Error: cannot open framebuffer device.");
    }

    auto *fbp = (uint32_t *) mmap(nullptr, WIDTH * HEIGHT * 4, PROT_READ | PROT_WRITE, MAP_SHARED, fbfd, 0);
    if (fbp == MAP_FAILED) {
        close(fbfd);
        throw std::runtime_error("Error: failed to map framebuffer device to memory.");
    }

    try {
        auto drawer = std::make_unique<TextDrawer>(fbp, WIDTH, HEIGHT);
        drawer->setDoubleBuffered(true);
        drawer->setFont(&JetBrainsMono8ptb4);
        auto screen_queue = std::make_unique<ScreenQueue>(*drawer, _config.screen_queue_max, WIDTH - 20, 650);

        _fb_descriptor = fbfd;
        _fbp = fbp;
        _drawer = std::move(drawer);
        _screen_queue = std::move(screen_queue);
    } catch (...) {
        munmap(fbp, WIDTH * HEIGHT * 4);
        close(fbfd);
        throw;
    }
}
