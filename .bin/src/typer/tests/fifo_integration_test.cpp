#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <exception>
#include <fcntl.h>
#include <mutex>
#include <poll.h>
#include <string>
#include <sys/stat.h>
#include <sys/time.h>
#include <thread>
#include <unistd.h>
#include <vector>

#include "../interactive.h"
#include "test_runner.h"

#ifdef __linux__
#include <linux/input.h>
using TestInputEvent = input_event;
#else
struct TestInputEvent {
    timeval time;
    uint16_t type;
    uint16_t code;
    int32_t value;
};
constexpr uint16_t EV_SYN = 0x00;
constexpr uint16_t EV_KEY = 0x01;
constexpr uint16_t EV_ABS = 0x03;
constexpr uint16_t SYN_REPORT = 0;
constexpr uint16_t ABS_X = 0x00;
constexpr uint16_t ABS_Y = 0x01;
constexpr uint16_t BTN_TOUCH = 0x14a;
#endif

namespace {

void write_all(int fd, const void *data, size_t size) {
    auto bytes = static_cast<const char *>(data);
    while (size) {
        auto written = write(fd, bytes, size);
        TYPER_CHECK(written > 0);
        bytes += written;
        size -= static_cast<size_t>(written);
    }
}

bool wait_for_path(const std::string &path) {
    for (int attempt = 0; attempt < 200; ++attempt) {
        if (access(path.c_str(), F_OK) == 0) return true;
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    return false;
}

void fragmented_frames_and_touch_dispatch() {
    using namespace typer::interactive;

    char directory_template[] = "/tmp/typer-fifo-test-XXXXXX";
    auto directory = mkdtemp(directory_template);
    TYPER_CHECK(directory);
    std::string base(directory);
    auto draw_pipe = base + "/draw";
    auto touch_pipe = base + "/touch";
    auto event_pipe = base + "/events";

    TYPER_CHECK(mkfifo(touch_pipe.c_str(), 0600) == 0);
    TYPER_CHECK(mkfifo(event_pipe.c_str(), 0600) == 0);
    int touch_writer = open(touch_pipe.c_str(), O_RDWR | O_NONBLOCK);
    int event_reader = open(event_pipe.c_str(), O_RDONLY | O_NONBLOCK);
    TYPER_CHECK(touch_writer >= 0 && event_reader >= 0);

    clear_hitboxes();
    register_hitbox(100, 200, 100, 100, "42:dialog.confirm");
    std::mutex frames_mutex;
    std::vector<std::string> frames;
    std::exception_ptr worker_error;
    std::thread worker([&] {
        try {
            run(draw_pipe, touch_pipe, event_pipe,
                [&](std::string_view frame) {
                    std::lock_guard<std::mutex> lock(frames_mutex);
                    frames.emplace_back(frame);
                }, false);
        } catch (...) {
            worker_error = std::current_exception();
        }
    });

    TYPER_CHECK(wait_for_path(draw_pipe));
    // mkfifo() makes the path visible just before run() opens and locks it.
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    int draw_writer = open(draw_pipe.c_str(), O_WRONLY | O_NONBLOCK);
    if (draw_writer < 0) {
        request_stop();
        worker.join();
        if (worker_error) std::rethrow_exception(worker_error);
        TYPER_CHECK(false && "unable to open draw FIFO");
    }
    std::string first = "--batch text -t \"fragmented";
    std::string second = " frame\"\n--end\n--batch flush\n--end\n";
    write_all(draw_writer, first.data(), first.size());
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
    write_all(draw_writer, second.data(), second.size());

    bool received_frames = false;
    for (int attempt = 0; attempt < 200; ++attempt) {
        {
            std::lock_guard<std::mutex> lock(frames_mutex);
            if (frames.size() == 2) {
                TYPER_CHECK(frames[0] == "--batch text -t \"fragmented frame\"");
                TYPER_CHECK(frames[1] == "--batch flush");
                received_frames = true;
                break;
            }
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    TYPER_CHECK(received_frames);

    TestInputEvent touch[] = {
        {{}, EV_ABS, ABS_X, 150},
        {{}, EV_ABS, ABS_Y, 250},
        {{}, EV_KEY, BTN_TOUCH, 1},
        {{}, EV_SYN, SYN_REPORT, 0},
        {{}, EV_KEY, BTN_TOUCH, 0},
        {{}, EV_SYN, SYN_REPORT, 0},
    };
    write_all(touch_writer, touch, sizeof(touch));

    std::string event;
    for (int attempt = 0; attempt < 200 && event.empty(); ++attempt) {
        pollfd fd{event_reader, POLLIN, 0};
        if (poll(&fd, 1, 5) > 0 && (fd.revents & POLLIN)) {
            char buffer[128];
            auto count = read(event_reader, buffer, sizeof(buffer));
            if (count > 0) event.assign(buffer, static_cast<size_t>(count));
        }
    }
    TYPER_CHECK(event == "tap 42:dialog.confirm\n");

    request_stop();
    worker.join();
    if (worker_error) std::rethrow_exception(worker_error);
    close(draw_writer);
    close(touch_writer);
    close(event_reader);
    unlink(touch_pipe.c_str());
    unlink(draw_pipe.c_str());
    unlink(event_pipe.c_str());
    TYPER_CHECK(rmdir(base.c_str()) == 0);
}

}  // namespace

int main(int argc, char **argv) {
    return typer::test::run(argc, argv, {
        {"fragmented_frames_and_touch_dispatch",
         fragmented_frames_and_touch_dispatch},
    });
}
