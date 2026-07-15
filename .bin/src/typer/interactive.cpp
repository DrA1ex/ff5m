#include "interactive.h"

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <csignal>
#include <cstring>
#include <fcntl.h>
#include <iostream>
#include <poll.h>
#include <string>
#include <unistd.h>
#include <vector>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/time.h>

#ifdef __linux__
#include <linux/input.h>
#else
struct input_event {
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

namespace typer::interactive {
namespace {

constexpr size_t MAX_DRAW_FRAME = 256 * 1024;
constexpr size_t MAX_HITBOXES = 128;
constexpr size_t MAX_ACTION_LENGTH = 128;

struct Hitbox {
    int x;
    int y;
    int width;
    int height;
    std::string action;

    [[nodiscard]] bool contains(int px, int py) const {
        return px >= x && py >= y && px < x + width && py < y + height;
    }
};

std::vector<Hitbox> hitboxes;
volatile sig_atomic_t signal_terminated = false;
std::atomic_bool stop_requested = false;

void terminate_handler(int) {
    signal_terminated = true;
}

void dispatch_action(int event_fd, int x, int y) {
    if (event_fd < 0) return;
    auto action = action_at(x, y);
    if (action.empty()) return;
    auto message = "tap " + action + "\n";
    if (write(event_fd, message.data(), message.size()) < 0 && errno != EAGAIN) {
        std::cerr << "Unable to write touch event: " << strerror(errno) << std::endl;
    }
}

void process_touch_events(int touch_fd, int event_fd, TouchTracker &tracker) {
    input_event events[16];
    while (true) {
        auto count = read(touch_fd, events, sizeof(events));
        if (count < 0) {
            if (errno != EAGAIN && errno != EINTR) {
                std::cerr << "Unable to read touch input: " << strerror(errno) << std::endl;
            }
            return;
        }
        if (count == 0) return;

        auto event_count = count / static_cast<ssize_t>(sizeof(input_event));
        for (ssize_t i = 0; i < event_count; ++i) {
            const auto &event = events[i];
            auto tap = tracker.process(event.type, event.code, event.value);
            if (tap) dispatch_action(event_fd, tap->x, tap->y);
        }
    }
}

}  // namespace

std::optional<TouchPoint> TouchTracker::process(uint16_t type, uint16_t code,
                                                int32_t value) {
    if (type == EV_ABS && code == ABS_X) {
        x_ = value;
        if (down_ && !starting_)
            max_distance_ = std::max(max_distance_,
                                     std::abs(x_ - start_x_) + std::abs(y_ - start_y_));
    } else if (type == EV_ABS && code == ABS_Y) {
        y_ = value;
        if (down_ && !starting_)
            max_distance_ = std::max(max_distance_,
                                     std::abs(x_ - start_x_) + std::abs(y_ - start_y_));
    } else if (type == EV_KEY && code == BTN_TOUCH) {
        if (value) {
            down_ = true;
            starting_ = true;
            released_ = false;
            max_distance_ = 0;
        } else if (down_) {
            down_ = false;
            released_ = true;
        }
    } else if (type == EV_SYN && code == SYN_REPORT) {
        if (down_ && starting_) {
            start_x_ = x_;
            start_y_ = y_;
            max_distance_ = 0;
            starting_ = false;
        } else if (released_) {
            released_ = false;
            starting_ = false;
            // A tap belongs to the control where the finger went down. Using
            // release coordinates lets a small drift activate a neighbouring
            // button or turn a press that started in a gap into an action.
            if (max_distance_ <= 35) return TouchPoint{start_x_, start_y_};
        }
    }
    return std::nullopt;
}

void register_hitbox(int x, int y, int width, int height, std::string action) {
    if (hitboxes.size() >= MAX_HITBOXES || action.size() > MAX_ACTION_LENGTH) return;
    hitboxes.push_back({x, y, width, height, std::move(action)});
}

void clear_hitboxes() {
    hitboxes.clear();
}

std::string action_at(int x, int y) {
    for (auto it = hitboxes.rbegin(); it != hitboxes.rend(); ++it) {
        if (it->contains(x, y)) return it->action;
    }
    return {};
}

void request_stop() {
    stop_requested.store(true, std::memory_order_relaxed);
}

void run(const std::string &draw_pipe, const std::string &touch_device,
         const std::string &event_pipe, const FrameHandler &frame_handler,
         bool debug) {
    signal_terminated = false;
    stop_requested.store(false, std::memory_order_relaxed);
    if (mkfifo(draw_pipe.c_str(), 0666) == -1 && errno != EEXIST) {
        throw std::runtime_error("Failed to create draw pipe: " + draw_pipe);
    }
    int draw_fd = open(draw_pipe.c_str(), O_RDWR | O_NONBLOCK);
    if (draw_fd < 0) throw std::runtime_error("Unable to open draw pipe");
    if (flock(draw_fd, LOCK_EX | LOCK_NB) == -1) {
        auto lock_error = errno;
        bool unsupported = lock_error == EOPNOTSUPP;
#if defined(ENOTSUP) && ENOTSUP != EOPNOTSUPP
        unsupported = unsupported || lock_error == ENOTSUP;
#endif
        if (!unsupported) {
            auto reason = std::string(strerror(lock_error));
            close(draw_fd);
            throw std::runtime_error("Unable to lock draw pipe: " + reason);
        }
    }

    int touch_fd = -1;
    int event_fd = -1;
    if (!touch_device.empty() && !event_pipe.empty()) {
        if (mkfifo(event_pipe.c_str(), 0666) == -1 && errno != EEXIST) {
            std::cerr << "Unable to create event pipe: " << strerror(errno) << std::endl;
        } else {
            event_fd = open(event_pipe.c_str(), O_WRONLY | O_NONBLOCK);
            touch_fd = open(touch_device.c_str(), O_RDONLY | O_NONBLOCK);
            if (event_fd < 0 || touch_fd < 0) {
                std::cerr << "Touch input disabled: " << strerror(errno) << std::endl;
                if (event_fd >= 0) close(event_fd);
                if (touch_fd >= 0) close(touch_fd);
                event_fd = touch_fd = -1;
            }
        }
    }

    signal(SIGTERM, terminate_handler);
    signal(SIGINT, terminate_handler);
    signal(SIGPIPE, SIG_IGN);
    std::string draw_buffer;
    TouchTracker touch_tracker;
    while (!signal_terminated && !stop_requested.load(std::memory_order_relaxed)) {
        if (touch_fd < 0 && !touch_device.empty() && !event_pipe.empty()) {
            auto new_event_fd = open(event_pipe.c_str(), O_WRONLY | O_NONBLOCK);
            auto new_touch_fd = open(touch_device.c_str(), O_RDONLY | O_NONBLOCK);
            if (new_event_fd >= 0 && new_touch_fd >= 0) {
                event_fd = new_event_fd;
                touch_fd = new_touch_fd;
                touch_tracker = {};
                if (debug) std::cerr << "Touch input connected" << std::endl;
            } else {
                if (new_event_fd >= 0) close(new_event_fd);
                if (new_touch_fd >= 0) close(new_touch_fd);
            }
        }
        pollfd fds[2] = {
            {.fd = draw_fd, .events = POLLIN, .revents = 0},
            {.fd = touch_fd, .events = POLLIN, .revents = 0},
        };
        auto ready = poll(fds, touch_fd >= 0 ? 2 : 1, 1000);
        if (ready < 0) {
            if (errno == EINTR) continue;
            std::cerr << "Poll failed: " << strerror(errno) << std::endl;
            break;
        }

        if (fds[0].revents & POLLIN) {
            char buffer[4096];
            while (true) {
                auto count = read(draw_fd, buffer, sizeof(buffer));
                if (count > 0) draw_buffer.append(buffer, count);
                else if (count < 0 && errno == EINTR) continue;
                else break;
            }
            constexpr std::string_view delimiter = "\n--end\n";
            size_t pos;
            while ((pos = draw_buffer.find(delimiter)) != std::string::npos) {
                if (pos <= MAX_DRAW_FRAME) {
                    try {
                        frame_handler(std::string_view(draw_buffer.data(), pos));
                    } catch (const std::exception &error) {
                        std::cerr << "Error while processing frame: " << error.what() << std::endl;
                    }
                } else if (debug) {
                    std::cerr << "Discarding oversized draw frame" << std::endl;
                }
                draw_buffer.erase(0, pos + delimiter.size());
            }
            if (draw_buffer.size() > MAX_DRAW_FRAME) {
                if (debug) std::cerr << "Discarding oversized draw frame" << std::endl;
                std::string{}.swap(draw_buffer);
            }
        }
        if (touch_fd >= 0 && (fds[1].revents & POLLIN)) {
            process_touch_events(touch_fd, event_fd, touch_tracker);
        }
        if (touch_fd >= 0 && (fds[1].revents & (POLLERR | POLLHUP | POLLNVAL))) {
            close(touch_fd);
            close(event_fd);
            touch_fd = event_fd = -1;
        }
    }

    if (event_fd >= 0) close(event_fd);
    if (touch_fd >= 0) close(touch_fd);
    close(draw_fd);
    unlink(draw_pipe.c_str());
    if (!event_pipe.empty()) unlink(event_pipe.c_str());
}

}  // namespace typer::interactive
