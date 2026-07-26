// Utils
//
// Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
//
// This file may be distributed under the terms of the GNU GPLv3 license

#pragma once

#include <chrono>
#include <cerrno>
#include <cstring>
#include <filesystem>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <system_error>
#include <vector>
#include <sys/file.h>
#include <unistd.h>

#include "types.h"


class FileLock {
    int _descriptor = -1;

public:
    explicit FileLock(const std::string &file_name) {
        _descriptor = open(file_name.c_str(), O_CREAT | O_RDWR, 0666);
        if (_descriptor == -1) {
            throw std::runtime_error(
                "Unable to open lock file " + file_name + ": "
                + std::strerror(errno));
        }

        while (flock(_descriptor, LOCK_EX) == -1) {
            if (errno == EINTR) continue;

            const auto error = std::string(std::strerror(errno));
            close(_descriptor);
            _descriptor = -1;
            throw std::runtime_error(
                "Unable to lock " + file_name + ": " + error);
        }
    }

    ~FileLock() {
        if (_descriptor == -1) return;
        flock(_descriptor, LOCK_UN);
        close(_descriptor);
    }

    FileLock(const FileLock &) = delete;
    FileLock &operator=(const FileLock &) = delete;
};

inline std::string current_date_time() {
    using namespace std::chrono;
    auto now = system_clock::now();
    return std::format("{:%F %T}", std::chrono::floor<seconds>(now));
}

inline std::string &replace_placeholder(std::string &str, const std::string &placeholder, const std::string &value) {
    size_t pos = str.find(placeholder);
    while (pos != std::string::npos) {
        str.replace(pos, placeholder.length(), value);
        pos = str.find(placeholder, pos + value.length());
    }

    return str;
}

inline std::vector<ScreenMessage> load_array_from_file(const std::string &file_name) {
    std::vector<ScreenMessage> array;

    if (std::ifstream infile(file_name); infile) {
        std::string line;
        while (std::getline(infile, line)) {
            size_t delimiterPos = line.find(";;");
            if (delimiterPos != std::string::npos) {
                std::string integerPart = line.substr(0, delimiterPos);
                std::string stringPart = line.substr(delimiterPos + 2);

                LogLevel level = LogLevel::DEBUG;

                try {
                    const auto parsed = std::stoi(integerPart);
                    if (parsed < static_cast<int>(LogLevel::DEBUG)
                        || parsed > static_cast<int>(LogLevel::ERROR)) {
                        throw std::out_of_range("unknown log level");
                    }
                    level = static_cast<LogLevel>(parsed);
                } catch (const std::exception &e) {
                    std::cerr << "Ignoring invalid screen queue entry: "
                              << line << " (" << e.what() << ")" << std::endl;
                    continue;
                }

                array.emplace_back(level, stringPart);
            } else {
                std::cerr << "Ignoring invalid screen queue entry: "
                          << line << std::endl;
            }
        }
    }

    return array;
}

inline void save_array_to_file(const std::vector<ScreenMessage> &array, const std::string &file_name) {
    const auto temporary = file_name + "." + std::to_string(getpid()) + ".tmp";
    try {
        std::ofstream outfile(temporary, std::ios::trunc);
        if (!outfile) {
            throw std::runtime_error("Unable to open temporary queue file");
        }
        for (const auto &message: array) {
            outfile << static_cast<int>(message.log_level)
                    << ";;" << message.str << '\n';
        }
        outfile.close();
        if (!outfile) {
            throw std::runtime_error("Unable to write temporary queue file");
        }

        std::error_code error;
        std::filesystem::rename(temporary, file_name, error);
        if (error) {
            throw std::runtime_error(
                "Unable to replace screen queue: " + error.message());
        }
    } catch (...) {
        std::error_code ignored;
        std::filesystem::remove(temporary, ignored);
        throw;
    }
}

inline std::string get_exe_name(int pid) {
    if (std::filesystem::exists("/proc/" + std::to_string(pid) + "/comm")) {
        std::string result;
        std::ifstream("/proc/" + std::to_string(pid) + "/comm") >> result;

        return result;
    }

    if (std::filesystem::exists("/proc/" + std::to_string(pid) + "/cmdline")) {
        std::string result;
        std::ifstream("/proc/" + std::to_string(pid) + "/cmdline") >> result;

        auto pos = result.find('\0');
        if (pos != std::string::npos) result = result.substr(0, pos);

        pos = result.rfind('/');
        if (pos != std::string::npos) result = result.substr(pos + 1);


        if (!result.empty()) return result;
    }

    return "N/A";
}
