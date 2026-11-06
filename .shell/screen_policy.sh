#!/bin/bash

## Which screen stream and touch input the current parameters require.
##
## Sourced, never executed. The only place that maps the display and the
## `screen_stream` / `touch_interface` parameters to what must run.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

# Sets SCREEN_STREAM_ENABLED and SCREEN_TOUCH_ENABLED (0/1); the second one says whether ts_uinput must run.
# Fails before setting anything when a parameter has an unknown value.
screen_policy() {
    local display stream touch
    # The screen is read the way the display switch reads it, so an unknown value falls back identically.
    display=$("$CMDS/zdisplay.sh" test 2> /dev/null) || return $?
    stream=$("$CFG_SCRIPT" "$VAR_PATH" --get screen_stream AUTO) || return $?
    touch=$("$CFG_SCRIPT" "$VAR_PATH" --get touch_interface AUTO) || return $?

    case "$stream" in
        AUTO|ON|OFF) ;;
        *) echo "Invalid screen_stream: $stream" >&2; return 1 ;;
    esac
    case "$touch" in
        AUTO|ON) ;;
        *) echo "Invalid touch_interface: $touch" >&2; return 1 ;;
    esac

    SCREEN_STREAM_ENABLED=0
    case "$stream:$display" in
        ON:*|AUTO:STOCK|AUTO:FEATHER|AUTO:GUPPY) SCREEN_STREAM_ENABLED=1 ;;
    esac

    SCREEN_TOUCH_ENABLED=0
    case "$touch:$display" in
        ON:*|AUTO:FEATHER|AUTO:GUPPY) SCREEN_TOUCH_ENABLED=1 ;;
    esac
}
