// Types
//
// Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
//
// This file may be distributed under the terms of the GNU GPLv3 license

#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

constexpr std::size_t MAX_SCREEN_QUEUE_ROWS = 20;

enum class LogLevel :uint8_t {
    DEBUG = 0,
    INFO  = 1,
    WARN  = 2,
    ERROR = 3,
};

struct LoggerParams {
    bool print = true;
    bool print_formatted = false;
    LogLevel print_level = LogLevel::DEBUG;

    bool log = true;
    LogLevel log_level = LogLevel::DEBUG;
    std::string log_file;
    std::string log_format = "%date% | %level% | %pid% | %script% | %message%";

    bool send_to_screen = false;
    LogLevel screen_level = LogLevel::INFO;

    bool screen_followup = true;
    size_t screen_queue_max = 5;
    std::string screen_follow_up_file = "/tmp/logged_message_queue";
    std::string screen_lock_file = "/tmp/logged_screen.lock";

    bool benchmark = false;
};

struct ScreenMessage {
    LogLevel log_level;
    std::string str;

    [[nodiscard]] uint32_t color() const {
        switch (log_level) {
            case LogLevel::ERROR: return 0xffc43c00;
            case LogLevel::WARN: return 0xfffa7c17;
            case LogLevel::INFO: return 0xffffffff;
            default:
                return 0xffb7a6b5;
        }
    }
};
