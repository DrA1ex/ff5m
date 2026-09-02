#!/bin/bash

## Detached firmware image entrypoint runner.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

FIRMWARE_RUNTIME_RELEASE_TIMEOUT_SECONDS=30
FIRMWARE_BINARY_STARTUP_GRACE_SECONDS=10
FORGE_X_RUNTIME_PATHS='/data/\.mod|/opt/config/mod|/root/printer_data'

is_unsigned_integer() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
        *) return 0 ;;
    esac
}

firmware_screen() {
    local title=$1
    local detail=$2
    local typer="$FIRMWARE_RUNNER_DIR/typer"
    local runtime="$FIRMWARE_RUNNER_DIR/runtime"
    local libstdcxx="$runtime/libstdc++.so.6"

    echo "// $title"
    [ -z "$detail" ] || echo "// $detail"
    [ -x "$typer" ] && [ -r "$libstdcxx" ] || return 0
    LD_LIBRARY_PATH="$runtime" LD_PRELOAD="$libstdcxx" \
        "$typer" -db --framebuffer-copy-only batch \
        --batch fill -p 0 0 -s 800 480 -c 0 \
        --batch text -p 400 205 -ha center -va middle -c 35d9e6 \
            -f "JetBrainsMono 20pt" --max-width 720 -t "$title" \
        --batch text -p 400 285 -ha center -va middle -c ffffff \
            -f "JetBrainsMono 12pt" --max-width 720 -t "$detail" \
        >/dev/null 2>&1 || true
}

mod_path_references() {
    mount 2>&1 | grep -E "$FORGE_X_RUNTIME_PATHS" || true
    lsof 2>&1 | grep -E "$FORGE_X_RUNTIME_PATHS" || true
}

mod_paths_busy() {
    mod_path_references | grep -q .
}

wait_for_mod_paths() {
    local elapsed=0

    while mod_paths_busy; do
        if [ "$elapsed" -ge "$FIRMWARE_RUNTIME_RELEASE_TIMEOUT_SECONDS" ]; then
            echo "@@ Previous Forge-X runtime did not stop."
            mod_path_references >&2
            return 1
        fi

        if [ "$((elapsed % 5))" -eq 0 ]; then
            firmware_screen "Preparing firmware installer" \
                "Waiting for recovery services: ${elapsed}s"
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
}

if [ "$#" -ne 6 ]; then
    exit 2
fi

FIRMWARE_PACKAGE_DIR=$1
FIRMWARE_ENTRYPOINT_NAME=$2
FIRMWARE_ENTRYPOINT_KIND=$3
FIRMWARE_MACHINE=$4
FIRMWARE_PRODUCT_ID=$5
FIRMWARE_ERROR_DELAY_SECONDS=$6
FIRMWARE_RUNNER_DIR=$(cd "$(dirname "$0")" && pwd) || exit 1

[ -n "$FIRMWARE_PACKAGE_DIR" ] && [ "$FIRMWARE_PACKAGE_DIR" != "/" ] \
    && [ -d "$FIRMWARE_PACKAGE_DIR" ] || exit 2
case "$FIRMWARE_ENTRYPOINT_KIND:$FIRMWARE_ENTRYPOINT_NAME" in
    binary:forge-x-init|shell:forge-x-init.sh|shell:flashforge_init.sh) ;;
    *) exit 2 ;;
esac
case "$FIRMWARE_MACHINE:$FIRMWARE_PRODUCT_ID" in
    Adventurer5M:0023|Adventurer5MPro:0024) ;;
    *) exit 2 ;;
esac
is_unsigned_integer "$FIRMWARE_ERROR_DELAY_SECONDS" || exit 2
is_unsigned_integer "$FIRMWARE_RUNTIME_RELEASE_TIMEOUT_SECONDS" || exit 2
is_unsigned_integer "$FIRMWARE_BINARY_STARTUP_GRACE_SECONDS" || exit 2

unset LD_PRELOAD
unset LD_LIBRARY_PATH

cd "$FIRMWARE_RUNNER_DIR" || exit 1
if ! wait_for_mod_paths; then
    firmware_screen "Firmware installer blocked" \
        "Power off the printer and retry recovery."
    sync
    exit 1
fi

rm -f /tmp/logged_message_queue
export FORGE_X_FIRMWARE_DIR="$FIRMWARE_PACKAGE_DIR"
cd "$FIRMWARE_PACKAGE_DIR" || exit 1

if [ "$FIRMWARE_ENTRYPOINT_KIND" = "binary" ]; then
    nohup "./$FIRMWARE_ENTRYPOINT_NAME" \
        "$FIRMWARE_MACHINE" "$FIRMWARE_PRODUCT_ID" </dev/null &
    installer_pid=$!
    if [ -z "$installer_pid" ]; then
        echo "@@ Installer process could not be created."
        firmware_screen "Firmware installer failed" \
            "The installer could not start. Power off and retry recovery."
        sync
        exit 0
    fi

    elapsed=0

    while [ "$elapsed" -lt "$FIRMWARE_BINARY_STARTUP_GRACE_SECONDS" ]; do
        kill -0 "$installer_pid" 2>/dev/null || break
        sleep 1
        elapsed=$((elapsed + 1))
    done

    if kill -0 "$installer_pid" 2>/dev/null; then
        echo "// Installer remained active through the ${FIRMWARE_BINARY_STARTUP_GRACE_SECONDS}s startup window."
        disown "$installer_pid" 2>/dev/null || disown 2>/dev/null || true
        exit 0
    fi

    wait "$installer_pid"
    status=$?

    case "$status" in
        0) ;;
        100) echo "// Installer reported a handled failure (status 100)." ;;
        101) echo "// Installer was canceled normally (status 101)." ;;
        102) echo "// Installer handled an interrupt (status 102)." ;;
        *)
            echo "@@ Installer exited unexpectedly during startup with status $status."
            firmware_screen "Firmware installer failed" \
                "The installer could not start. Power off and retry recovery."
            sync
        ;;
    esac

    exit 0
fi

firmware_screen "Starting firmware installer" "Launching the selected image."
/bin/bash "./$FIRMWARE_ENTRYPOINT_NAME" \
    "$FIRMWARE_MACHINE" "$FIRMWARE_PRODUCT_ID"
status=$?

if [ "$status" -ne 0 ]; then
    echo "@@ Installer exited with status $status."
    sleep "$FIRMWARE_ERROR_DELAY_SECONDS"
    firmware_screen "Firmware installer failed" \
        "Ensure writing stopped, then power off."
    sync
fi

exit 0
