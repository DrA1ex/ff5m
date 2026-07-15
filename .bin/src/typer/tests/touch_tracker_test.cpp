#include <optional>

#include "../interactive.h"
#include "test_runner.h"

namespace {

constexpr uint16_t ev_syn = 0, ev_key = 1, ev_abs = 3;
constexpr uint16_t syn_report = 0, abs_x = 0, abs_y = 1, btn_touch = 0x14a;

void position(typer::interactive::TouchTracker &tracker, int x, int y) {
    tracker.process(ev_abs, abs_x, x);
    tracker.process(ev_abs, abs_y, y);
}

void press(typer::interactive::TouchTracker &tracker) {
    tracker.process(ev_key, btn_touch, 1);
    TYPER_CHECK(!tracker.process(ev_syn, syn_report, 0));
}

std::optional<typer::interactive::TouchPoint> release(
        typer::interactive::TouchTracker &tracker) {
    tracker.process(ev_key, btn_touch, 0);
    return tracker.process(ev_syn, syn_report, 0);
}

void basic_tap() {
    using typer::interactive::TouchTracker;
    TouchTracker tap_tracker;
    position(tap_tracker, 120, 220);
    press(tap_tracker);
    auto tap = release(tap_tracker);
    TYPER_CHECK(tap && tap->x == 120 && tap->y == 220);
}

void duplicate_release_ignored() {
    typer::interactive::TouchTracker tracker;
    position(tracker, 120, 220);
    press(tracker);
    TYPER_CHECK(release(tracker));
    TYPER_CHECK(!tracker.process(ev_syn, syn_report, 0));
    TYPER_CHECK(!release(tracker));
}

void coordinates_after_press() {
    using typer::interactive::TouchTracker;
    TouchTracker coordinate_tracker;
    coordinate_tracker.process(ev_key, btn_touch, 1);
    position(coordinate_tracker, 799, 479);
    TYPER_CHECK(!coordinate_tracker.process(ev_syn, syn_report, 0));
    auto tap = release(coordinate_tracker);
    TYPER_CHECK(tap && tap->x == 799 && tap->y == 479);
}

void movement_threshold_inclusive() {
    using typer::interactive::TouchTracker;
    TouchTracker threshold_tracker;
    position(threshold_tracker, 100, 100);
    press(threshold_tracker);
    position(threshold_tracker, 135, 100);
    auto tap = release(threshold_tracker);
    TYPER_CHECK(tap && tap->x == 100 && tap->y == 100);
}

void tap_keeps_press_origin() {
    using typer::interactive::TouchTracker;
    TouchTracker tracker;
    position(tracker, 210, 120);
    press(tracker);
    position(tracker, 230, 130);
    auto tap = release(tracker);
    TYPER_CHECK(tap && tap->x == 210 && tap->y == 120);
}

void swipe_rejected() {
    using typer::interactive::TouchTracker;
    TouchTracker swipe_tracker;
    position(swipe_tracker, 100, 100);
    press(swipe_tracker);
    position(swipe_tracker, 136, 100);
    TYPER_CHECK(!release(swipe_tracker));
}

void returned_swipe_rejected() {
    using typer::interactive::TouchTracker;
    TouchTracker returned_swipe_tracker;
    position(returned_swipe_tracker, 100, 100);
    press(returned_swipe_tracker);
    position(returned_swipe_tracker, 180, 100);
    position(returned_swipe_tracker, 100, 100);
    TYPER_CHECK(!release(returned_swipe_tracker));
}

void unrelated_events_ignored() {
    using typer::interactive::TouchTracker;
    TouchTracker unrelated_tracker;
    TYPER_CHECK(!unrelated_tracker.process(ev_key, 123, 1));
    TYPER_CHECK(!unrelated_tracker.process(99, 99, 99));
    TYPER_CHECK(!release(unrelated_tracker));
}

}  // namespace

int main(int argc, char **argv) {
    return typer::test::run(argc, argv, {
        {"basic_tap", basic_tap},
        {"duplicate_release_ignored", duplicate_release_ignored},
        {"coordinates_after_press", coordinates_after_press},
        {"movement_threshold_inclusive", movement_threshold_inclusive},
        {"tap_keeps_press_origin", tap_keeps_press_origin},
        {"swipe_rejected", swipe_rejected},
        {"returned_swipe_rejected", returned_swipe_rejected},
        {"unrelated_events_ignored", unrelated_events_ignored},
    });
}
