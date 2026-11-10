#!/bin/bash

## Whether the current display and screen_stream settings require a screen stream.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

# Sets SCREEN_STREAM_ENABLED (0/1) after validating the stream preference.
screen_policy() {
    local display stream
    # The screen is read the way the display switch reads it, so an unknown value falls back identically.
    display=$("$CMDS/zdisplay.sh" test 2> /dev/null) || return $?
    stream=$("$CFG_SCRIPT" "$VAR_PATH" --get screen_stream AUTO) || return $?

    case "$stream" in
        AUTO|ON|OFF) ;;
        *) echo "Invalid screen_stream: $stream" >&2; return 1 ;;
    esac
    SCREEN_STREAM_ENABLED=0
    case "$stream:$display" in
        ON:*|AUTO:STOCK|AUTO:FEATHER|AUTO:GUPPY) SCREEN_STREAM_ENABLED=1 ;;
    esac
}
