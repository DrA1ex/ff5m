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
    bool continuous;

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

void dispatch_message(int event_fd, const std::string &message) {
    if (event_fd < 0) return;
    if (write(event_fd, message.data(), message.size()) < 0 && errno != EAGAIN) {
        std::cerr << "Unable to write touch event: " << strerror(errno) << std::endl;
    }
}

void dispatch_action(int event_fd, int x, int y) {
    auto action = action_at(x, y);
    if (!action.empty()) dispatch_message(event_fd, "tap " + action + "\n");
}

struct TouchDispatchState {
    std::string action;
    int x = 0;
    int y = 0;

    [[nodiscard]] bool active() const { return !action.empty(); }

    void dispatch(int event_fd, const char *phase) const {
        if (!active()) return;
        dispatch_message(event_fd, "touch " + action + " " + phase + " "
                                   + std::to_string(x) + " "
                                   + std::to_string(y) + "\n");
    }

    void clear() { action.clear(); }
};

void process_touch_events(int touch_fd, int event_fd, TouchTracker &tracker,
                          TouchDispatchState &dispatch) {
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
            auto report = tracker.process_report(event.type, event.code, event.value);
            if (!report) continue;
            if (report->phase == TouchPhase::PRESS) {
                if (dispatch.active()) {
                    dispatch.dispatch(event_fd, "end");
                    dispatch.clear();
                }
                if (continuous_at(report->start.x, report->start.y)) {
                    dispatch.action = action_at(report->start.x, report->start.y);
                    dispatch.x = report->point.x;
                    dispatch.y = report->point.y;
                    dispatch.dispatch(event_fd, "begin");
                }
            } else if (report->phase == TouchPhase::MOVE && dispatch.active()) {
                dispatch.x = report->point.x;
                dispatch.y = report->point.y;
                dispatch.dispatch(event_fd, "move");
            } else if (report->phase == TouchPhase::RELEASE) {
                if (dispatch.active()) {
                    dispatch.x = report->point.x;
                    dispatch.y = report->point.y;
                    dispatch.dispatch(event_fd, "end");
                    dispatch.clear();
                } else if (report->tap) {
                    dispatch_action(event_fd, report->start.x, report->start.y);
                }
            }
        }
    }
}

}  // namespace

std::optional<TouchPoint> TouchTracker::process(uint16_t type, uint16_t code,
                                                int32_t value) {
    auto report = process_report(type, code, value);
    if (report && report->phase == TouchPhase::RELEASE && report->tap)
        return report->start;
    return std::nullopt;
}

std::optional<TouchReport> TouchTracker::process_report(
        uint16_t type, uint16_t code, int32_t value) {
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
            reported_x_ = x_;
            reported_y_ = y_;
            max_distance_ = 0;
            starting_ = false;
            return TouchReport{TouchPhase::PRESS, {x_, y_},
                               {start_x_, start_y_}, false};
        } else if (down_ && (x_ != reported_x_ || y_ != reported_y_)) {
            reported_x_ = x_;
            reported_y_ = y_;
            return TouchReport{TouchPhase::MOVE, {x_, y_},
                               {start_x_, start_y_}, false};
        } else if (released_) {
            released_ = false;
            starting_ = false;
            // A tap belongs to the control where the finger went down. Using
            // release coordinates lets a small drift activate a neighbouring
            // button or turn a press that started in a gap into an action.
            return TouchReport{TouchPhase::RELEASE, {x_, y_},
                               {start_x_, start_y_}, max_distance_ <= 35};
        }
    }
    return std::nullopt;
}

void register_hitbox(int x, int y, int width, int height, std::string action,
                     bool continuous) {
    if (hitboxes.size() >= MAX_HITBOXES || action.size() > MAX_ACTION_LENGTH) return;
    hitboxes.push_back({x, y, width, height, std::move(action), continuous});
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

bool continuous_at(int x, int y) {
    for (auto it = hitboxes.rbegin(); it != hitboxes.rend(); ++it) {
        if (it->contains(x, y)) return it->continuous;
    }
    return false;
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
    TouchDispatchState touch_dispatch;
    while (!signal_terminated && !stop_requested.load(std::memory_order_relaxed)) {
        if (touch_fd < 0 && !touch_device.empty() && !event_pipe.empty()) {
            auto new_event_fd = open(event_pipe.c_str(), O_WRONLY | O_NONBLOCK);
            auto new_touch_fd = open(touch_device.c_str(), O_RDONLY | O_NONBLOCK);
            if (new_event_fd >= 0 && new_touch_fd >= 0) {
                event_fd = new_event_fd;
                touch_fd = new_touch_fd;
                touch_tracker = {};
                touch_dispatch = {};
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
        auto ready = poll(fds, touch_fd >= 0 ? 2 : 1,
                          touch_dispatch.active() ? 100 : 1000);
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
            process_touch_events(touch_fd, event_fd, touch_tracker, touch_dispatch);
        } else if (ready == 0 && touch_dispatch.active()) {
            // A stationary finger may not produce input_event records.  The
            // heartbeat lets the controller distinguish a held joystick from
            // a lost release event and fail safe if typer disappears.
            touch_dispatch.dispatch(event_fd, "move");
        }
        if (touch_fd >= 0 && (fds[1].revents & (POLLERR | POLLHUP | POLLNVAL))) {
            touch_dispatch.dispatch(event_fd, "end");
            touch_dispatch.clear();
            close(touch_fd);
            close(event_fd);
            touch_fd = event_fd = -1;
        }
    }

    touch_dispatch.dispatch(event_fd, "end");
    if (event_fd >= 0) close(event_fd);
    if (touch_fd >= 0) close(touch_fd);
    close(draw_fd);
    unlink(draw_pipe.c_str());
    if (!event_pipe.empty()) unlink(event_pipe.c_str());
}

}  // namespace typer::interactive
