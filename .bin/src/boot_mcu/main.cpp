#include <fcntl.h>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>
#include <termios.h>
#include <unistd.h>

const char *TTY = "/dev/ttyS1";
const int BAUD = B115200;

const char *RESP_READY = "Ready.";

const char CMD_BOOT = 'A';
const char RESP_ACK = 0x06;
const char RESP_OK = 0x01;

const int READY_RETRIES = 15;
const int BOOT_RETRIES = 15;


void print_hex(const std::string &data) {
    for (unsigned char c: data) {
        std::cout
            << std::hex << std::uppercase
            << std::setfill('0') << std::setw(2)
            << (int) c << " ";
    }
    std::cout << "| " << data << std::endl;
}

void configure_serial(int fd) {
    termios options = {};

    if (tcgetattr(fd, &options) < 0) {
        throw std::runtime_error("Failed to get serial port attributes.");
    }

    cfmakeraw(&options);
    cfsetispeed(&options, BAUD);
    cfsetospeed(&options, BAUD);

    if (tcsetattr(fd, TCSANOW, &options) < 0) {
        throw std::runtime_error("Failed to configure serial port.");
    }
}

std::string read_serial(int fd, size_t size) {
    char buffer[32];

    ssize_t bytes_read = read(fd, buffer, size);
    if (bytes_read < 0) throw std::runtime_error("Failed to read from serial port.");

    return std::string(buffer, bytes_read);
}

void write_serial(int fd, const std::string &data) {
    ssize_t bytes_written = write(fd, data.c_str(), data.size());
    if (bytes_written < 0) throw std::runtime_error("Failed to write to serial port.");
}

int main() {
    try {
        std::cout << "Setting up serial port: " << TTY << std::endl;

        // Check if TTY device exists
        if (access(TTY, F_OK) != 0) {
            std::cerr << "@@ Serial: " << TTY << " does not exist." << std::endl;
            return 1;
        }

        // Open the serial port
        int fd = open(TTY, O_RDWR | O_NOCTTY);
        if (fd < 0) {
            std::cerr << "@@ Serial: Failed to open " << TTY << std::endl;
            return 1;
        }

        // Configure the serial port
        std::cout << "Configuring..." << std::endl;
        configure_serial(fd);

        std::cout << "// Waiting for MCU to become ready ..." << std::endl;

        // Wait for MCU to become ready
        bool mcu_ready = false;
        for (int i = 0; i < READY_RETRIES; ++i) {
            std::string buf = read_serial(fd, 32);
            if (buf.empty()) {
                std::cerr << "@@ No data received from MCU." << std::endl;
                close(fd);
                return 1;
            }

            std::cout << "MCU Recv: ";
            print_hex(buf);

            if (buf.find(RESP_READY) != std::string::npos) {
                std::cout << "// MCU Ready." << std::endl;
                mcu_ready = true;
                break;
            }
        }

        if (!mcu_ready) {
            std::cerr << "?? MCU not ready" << std::endl;
            close(fd);
            return 1;
        }

        std::cout << "// Sending boot command..." << std::endl;

        // Send boot command
        for (int i = 0; i < BOOT_RETRIES; ++i) {
            std::cout << "MCU Send: ";
            print_hex(std::string(1, CMD_BOOT));

            write_serial(fd, std::string(1, CMD_BOOT));
            std::string buf = read_serial(fd, 32);

            if (buf.empty()) {
                std::cerr << "@@ No response received from MCU." << std::endl;
                close(fd);
                return 2;
            }

            std::cout << "MCU Recv: ";
            print_hex(buf);

            if (buf.find(std::string(1, RESP_OK)) != std::string::npos
                || buf.find(std::string(1, RESP_ACK)) != std::string::npos) {
                std::cout << "// MCU is starting." << std::endl;
                close(fd);
                return 0;
            }
        }

        std::cerr << "?? Didn't receive boot confirmation" << std::endl;
        close(fd);
        return 1;

    } catch (const std::exception &e) {
        std::cerr << "@@ Error: " << e.what() << std::endl;
        return 1;
    }
}
