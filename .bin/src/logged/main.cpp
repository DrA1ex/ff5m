// Logging utility
//
// Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
//
// This file may be distributed under the terms of the GNU GPLv3 license

#include <iostream>
#include <stdexcept>
#include <string>

#include "logger.h"

int parse_args(int argc, char *argv[], LoggerParams &config);
void print_help();

int main(int argc, char *argv[]) {
    LoggerParams config;

    try {
        if (auto ret = parse_args(argc, argv, config)) return ret;

        Logger logger(config);
        logger.process_stream(std::cin);
    } catch (const std::invalid_argument &e) {
        std::cerr << "Invalid argument: " << e.what() << std::endl;
        return 1;
    } catch (const std::exception &e) {
        std::cerr << "Fatal Error: " << e.what() << std::endl;
        return 2;
    }

    return 0;
}

int read_int(const std::string &param, int &index, int argc, char *argv[]) {
    if (index + 1 >= argc) {
        throw std::invalid_argument("Missing required value for parameter: " + param);
    }

    auto value = argv[++index];

    try {
        return std::stoi(value);
    } catch (const std::exception &e) {
        throw std::invalid_argument("Unable to parse value for parameter " + param + ": " + value);
    }
}

LogLevel read_level(
    const std::string &param, int &index, int argc, char *argv[]) {
    const auto value = read_int(param, index, argc, argv);
    if (value < static_cast<int>(LogLevel::DEBUG)
        || value > static_cast<int>(LogLevel::ERROR)) {
        throw std::invalid_argument(
            param + " must be between 0 and 3");
    }
    return static_cast<LogLevel>(value);
}

int parse_args(int argc, char *argv[], LoggerParams &config) {
    for (int i = 1; i < argc; ++i) {
        std::string param = argv[i];
        if (param == "--no-print") {
            config.print = false;
        } else if (param == "--print-formatted") {
            config.print_formatted = true;
        } else if (param == "--print-level") {
            config.print_level = read_level(param, i, argc, argv);
        } else if (param == "--no-log") {
            config.log = false;
        } else if (param == "--log-level") {
            config.log_level = read_level(param, i, argc, argv);
        } else if (param == "--log-format") {
            config.log_format = argv[++i];
        } else if (param == "--send-to-screen") {
            config.send_to_screen = true;
        } else if (param == "--screen-level") {
            config.screen_level = read_level(param, i, argc, argv);
        } else if (param == "--screen-queue") {
            const auto count = read_int(param, i, argc, argv);
            if (count <= 0
                || count > static_cast<int>(MAX_SCREEN_QUEUE_ROWS)) {
                throw std::invalid_argument(
                    "--screen-queue must be between 1 and "
                    + std::to_string(MAX_SCREEN_QUEUE_ROWS));
            }
            config.screen_queue_max = static_cast<std::size_t>(count);
        } else if (param == "--screen-no-followup") {
            config.screen_followup = false;
        } else if (param == "--benchmark") {
            config.benchmark = true;
        } else if (param == "--help") {
            print_help();
            return 1;
        } else if (config.log && config.log_file.empty()) {
            config.log_file = param;
        } else {
            std::cerr << "Unknown option: " << param << std::endl;
            return 1;
        }
    }

    if (config.log && config.log_file.empty()) {
        std::cerr << "You must specify a log file name." << std::endl;
        return 1;
    }

    return 0;
}

void print_help() {
    std::cout << "Usage: <exec-name> <script-args> | logged [OPTIONS] [LOG_FILE]\n";
    std::cout << "This utility handles stdin, makes logging, printing, and screen drawing.\n";
    std::cout << "Specially designed for Flashforge Adventurer 5M (Pro).\n\n";
    std::cout << "OPTIONS:\n";
    std::cout << "  --no-print                 Disable printing to the console.\n";
    std::cout << "  --print-formatted          Enable formatted output for prints.\n";
    std::cout << "  --print-level LEVEL        Set print verbosity level (integer). Default: 0.\n";
    std::cout << "  --no-log                   Disable logging to a file.\n";
    std::cout << "  --log-level LEVEL          Set logging verbosity level (integer). Default: 0.\n";
    std::cout << "  --log-format FORMAT        Specify log message format.\n";
    std::cout << "                             Default: \"%date% | %level% | %pid% | %script% | %message%\".\n";
    std::cout << "                             Supported fields: %date%, %level%, %pid%, \n";
    std::cout << "                                               %script%, %message%\n";
    std::cout << "  --send-to-screen           Send messages to the screen, not just log/print.\n";
    std::cout << "  --screen-queue COUNT       Set screen lines to draw (integer). Default: 5.\n";
    std::cout << "  --screen-level LEVEL       Set screen verbosity level (integer). Default: 1.\n";
    std::cout << "  --screen-no-followup       Disable follow-up messages from other scripts.\n";
    std::cout << "  --benchmark                Enable benchmarking mode.\n";
    std::cout << "  --help                     Display this help message and exit.\n";
    std::cout << "LOG_FILE:\n";
    std::cout << "  Optional: Path to the log file. If omitted, logging to a file is disabled.\n";
    std::cout << "LEVEL:\n";
    std::cout << "  Verbosity levels for messages:\n";
    std::cout << "    DEBUG = 0, INFO = 1, WARN = 2, ERROR = 3\n\n";
    std::cout << "EXAMPLE USAGE:\n";
    std::cout << "  <script-name> | logged --print-formatted --print-level 2 \\\n";
    std::cout << "                 --log-level 3 /path/to/log-file\n";
    std::cout << "NOTES:\n";
    std::cout << "  - Flags without arguments (e.g., --no-print) toggle boolean options.\n";
    std::cout << "  - Arguments like --log-level require a single numeric value.\n\n";
    std::cout << "Copyright (C) 2025, Alexander K <https://github.com/drA1ex>\n";
    std::cout << "This file may be distributed under the terms of the GNU GPLv3 license\n";
}
