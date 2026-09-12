#!/bin/bash

## Start and stop the Feather Theme Editor.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

source /opt/config/mod/.shell/common.sh

EDITOR="/opt/config/mod/.py/klipper/plugins/ui/themes/theme_editor.py"
THEMES_DIR="/opt/config/mod/.py/klipper/plugins/ui/themes"
THEME_PY="/opt/config/mod/.py/klipper/plugins/ui/theme.py"
PID_FILE="/tmp/feather-theme-editor.pid"
LOG_FILE="/tmp/feather-theme-editor.log"
EDITOR_PORT=8765


lan_address() {
    ip addr 2>/dev/null \
        | awk '/inet / && ($NF == "wlan0" || $NF == "eth0") { sub("/.*", "", $2); print $2; exit }'
}

editor_running() {
    local editor_pid

    [ -r "$PID_FILE" ] || return 1
    editor_pid=$(cat "$PID_FILE")
    [ -r "/proc/$editor_pid/cmdline" ] || return 1
    grep -Fq "$EDITOR" "/proc/$editor_pid/cmdline" 2>/dev/null
}

start_editor() {
    local address
    local attempt
    local editor_pid

    if editor_running; then
        echo "Theme Editor is already running."
        grep '^  url      :' "$LOG_FILE" 2>/dev/null || true
        return 0
    fi

    address=$(lan_address)
    if [ -z "$address" ]; then
        echo "Unable to determine the printer LAN address."
        return 1
    fi

    rm -f "$PID_FILE"
    : > "$LOG_FILE"
    python3 -u "$EDITOR" \
        --no-open \
        --quiet-http \
        --host 0.0.0.0 \
        --url-host "$address" \
        --port "$EDITOR_PORT" \
        --themes-dir "$THEMES_DIR" \
        --theme-py "$THEME_PY" \
        > "$LOG_FILE" 2>&1 < /dev/null &
    editor_pid=$!
    echo "$editor_pid" > "$PID_FILE"

    attempt=0
    while [ "$attempt" -lt 50 ]; do
        if grep -q '^  url      :' "$LOG_FILE"; then
            grep '^  url      :' "$LOG_FILE"
            return 0
        fi
        if ! kill -0 "$editor_pid" 2>/dev/null; then
            cat "$LOG_FILE"
            rm -f "$PID_FILE"
            return 1
        fi
        sleep 0.1
        attempt=$((attempt + 1))
    done

    kill "$editor_pid" 2>/dev/null || true
    rm -f "$PID_FILE"
    cat "$LOG_FILE"
    echo "Theme Editor did not report its URL."
    return 1
}

stop_editor() {
    local editor_pid

    if ! editor_running; then
        rm -f "$PID_FILE"
        echo "Theme Editor is not running."
        return 0
    fi

    editor_pid=$(cat "$PID_FILE")
    if ! kill "$editor_pid"; then
        echo "Unable to stop Theme Editor."
        return 1
    fi

    rm -f "$PID_FILE"
    echo "Theme Editor stopped."
}

case "$1" in
    start)
        start_editor
        ;;
    stop)
        stop_editor
        ;;
    *)
        echo "Usage: $0 (start|stop)"
        exit 1
        ;;
esac
