// Tests for typer interactive controls.
//
// Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
//
// This file may be distributed under the terms of the GNU GPLv3 license

#include "../interactive.h"
#include "test_runner.h"

namespace {

void empty_registry() {
    using namespace typer::interactive;
    clear_hitboxes();
    TYPER_CHECK(action_at(10, 10).empty());
}

void rectangle_boundaries() {
    using namespace typer::interactive;
    clear_hitboxes();
    register_hitbox(0, 0, 100, 100, "background");
    TYPER_CHECK(action_at(0, 0) == "background");
    TYPER_CHECK(action_at(99, 99) == "background");
    TYPER_CHECK(action_at(-1, 0).empty());
    TYPER_CHECK(action_at(0, -1).empty());
    TYPER_CHECK(action_at(100, 99).empty());
    TYPER_CHECK(action_at(99, 100).empty());
    TYPER_CHECK(action_at(100, 100).empty());
}

void overlap_precedence() {
    using namespace typer::interactive;
    clear_hitboxes();
    register_hitbox(0, 0, 100, 100, "background");
    register_hitbox(25, 25, 50, 50, "dialog.confirm");
    TYPER_CHECK(action_at(25, 25) == "dialog.confirm");
    TYPER_CHECK(action_at(74, 74) == "dialog.confirm");
    TYPER_CHECK(action_at(75, 75) == "background");
    TYPER_CHECK(action_at(30, 30) == "dialog.confirm");
    TYPER_CHECK(action_at(5, 5) == "background");
}

void negative_coordinates() {
    using namespace typer::interactive;
    clear_hitboxes();
    register_hitbox(-20, -20, 25, 25, "negative-origin");
    TYPER_CHECK(action_at(-10, -10) == "negative-origin");
    TYPER_CHECK(action_at(0, 0) == "negative-origin");
}

void clear_registry() {
    using namespace typer::interactive;
    clear_hitboxes();
    register_hitbox(0, 0, 100, 100, "temporary");
    clear_hitboxes();
    TYPER_CHECK(action_at(30, 30).empty());
}

void registry_limit() {
    using namespace typer::interactive;
    clear_hitboxes();
    for (int index = 0; index < 128; ++index) {
        register_hitbox(index * 2, 0, 1, 1, "bounded");
    }
    register_hitbox(500, 0, 1, 1, "overflow");
    TYPER_CHECK(action_at(254, 0) == "bounded");
    TYPER_CHECK(action_at(500, 0).empty());
}

void action_length_limit() {
    using namespace typer::interactive;
    clear_hitboxes();
    register_hitbox(0, 0, 10, 10, std::string(129, 'x'));
    TYPER_CHECK(action_at(5, 5).empty());
}

void continuous_hitbox_flag() {
    using namespace typer::interactive;
    clear_hitboxes();
    register_hitbox(10, 20, 100, 80, "move.xy", true);
    register_hitbox(30, 40, 20, 20, "normal", false);
    TYPER_CHECK(continuous_at(10, 20));
    TYPER_CHECK(action_at(15, 25) == "move.xy");
    TYPER_CHECK(!continuous_at(35, 45));
    TYPER_CHECK(action_at(35, 45) == "normal");
    TYPER_CHECK(!continuous_at(500, 500));
}

}  // namespace

int main(int argc, char **argv) {
    return typer::test::run(argc, argv, {
        {"empty_registry", empty_registry},
        {"rectangle_boundaries", rectangle_boundaries},
        {"overlap_precedence", overlap_precedence},
        {"negative_coordinates", negative_coordinates},
        {"clear_registry", clear_registry},
        {"registry_limit", registry_limit},
        {"action_length_limit", action_length_limit},
        {"continuous_hitbox_flag", continuous_hitbox_flag},
    });
}
