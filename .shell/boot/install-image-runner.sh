#!/bin/bash

## Detached firmware image entrypoint runner.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

FIRMWARE_RUNTIME_RELEASE_TIMEOUT_SECONDS=30
FIRMWARE_BINARY_STARTUP_GRACE_SECONDS=10
FIRMWARE_CONSOLE=${FORGE_X_FIRMWARE_CONSOLE:-/dev/console}
FORGE_X_RUNTIME_PATHS='/data/\.mod|/opt/config/mod|/root/printer_data'

# fd 3 remains the small persistent lifecycle log inherited from install-image.
# stdout/stderr are reattached immediately so this detached runner and the
# installer it starts can keep using ordinary terminal output.
exec 3>&2
if ! exec > "$FIRMWARE_CONSOLE" 2>&1; then
    printf '%s | ERROR | firmware-runner | Cannot open system console: %s\n' \
        "$(date '+%Y-%m-%d %H:%M:%S')" "$FIRMWARE_CONSOLE" >&3
fi

runner_lifecycle() {
    local level=$1
    shift
    printf '%s | %s | firmware-runner | %s\n' \
        "$(date '+%Y-%m-%d %H:%M:%S')" "$level" "$*" >&3
}

is_unsigned_integer() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
        *) return 0 ;;
    esac
}

firmware_message() {
    local title=$1
    local detail=$2

    echo "// $title"
    [ -z "$detail" ] || echo "// $detail"
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
            firmware_message "Preparing firmware installer" \
                "Waiting for Forge-X services: ${elapsed}s"
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

runner_lifecycle INFO \
    "Runner started for $FIRMWARE_ENTRYPOINT_KIND:$FIRMWARE_ENTRYPOINT_NAME."
unset LD_PRELOAD
unset LD_LIBRARY_PATH

cd "$FIRMWARE_RUNNER_DIR" || exit 1
if ! wait_for_mod_paths; then
    runner_lifecycle ERROR "Previous Forge-X runtime did not stop."
    firmware_message "Firmware installer blocked" \
        "Power off the printer and retry installation."
    sync
    exit 1
fi

rm -f /tmp/logged_message_queue
export FORGE_X_FIRMWARE_DIR="$FIRMWARE_PACKAGE_DIR"
cd "$FIRMWARE_PACKAGE_DIR" || exit 1
runner_lifecycle INFO "Previous Forge-X runtime released; starting installer."

if [ "$FIRMWARE_ENTRYPOINT_KIND" = "binary" ]; then
    # The outer nohup already made SIGHUP ignored. Keep that disposition
    # explicit while avoiding a second nohup that could redirect /dev/console.
    trap '' HUP
    "./$FIRMWARE_ENTRYPOINT_NAME" \
        "$FIRMWARE_MACHINE" "$FIRMWARE_PRODUCT_ID" </dev/null 3>&- &
    installer_pid=$!
    if [ -z "$installer_pid" ]; then
        runner_lifecycle ERROR "Installer process could not be created."
        echo "@@ Installer process could not be created."
        firmware_message "Firmware installer failed" \
            "The installer could not start. Power off and retry."
        sync
        exit 0
    fi
    runner_lifecycle INFO "Installer process started (PID $installer_pid)."

    elapsed=0

    while [ "$elapsed" -lt "$FIRMWARE_BINARY_STARTUP_GRACE_SECONDS" ]; do
        kill -0 "$installer_pid" 2>/dev/null || break
        sleep 1
        elapsed=$((elapsed + 1))
    done

    if kill -0 "$installer_pid" 2>/dev/null; then
        runner_lifecycle INFO \
            "Installer remained active for ${FIRMWARE_BINARY_STARTUP_GRACE_SECONDS}s; releasing supervision."
        echo "// Installer remained active through the ${FIRMWARE_BINARY_STARTUP_GRACE_SECONDS}s startup window."
        disown "$installer_pid" 2>/dev/null || disown 2>/dev/null || true
        exit 0
    fi

    wait "$installer_pid"
    status=$?

    case "$status" in
        0)
            runner_lifecycle INFO "Installer completed during startup with status 0."
        ;;
        100)
            runner_lifecycle INFO "Installer exited during startup with status 100."
            echo "// Installer reported a handled failure (status 100)."
        ;;
        101)
            runner_lifecycle INFO "Installer exited during startup with status 101."
            echo "// Installer was canceled normally (status 101)."
        ;;
        102)
            runner_lifecycle INFO "Installer exited during startup with status 102."
            echo "// Installer handled an interrupt (status 102)."
        ;;
        *)
            runner_lifecycle ERROR \
                "Installer exited unexpectedly during startup with status $status."
            echo "@@ Installer exited unexpectedly during startup with status $status."
            firmware_message "Firmware installer failed" \
                "The installer could not start. Power off and retry."
            sync
        ;;
    esac

    exit 0
fi

firmware_message "Starting firmware installer" "Launching the selected image."
/bin/bash "./$FIRMWARE_ENTRYPOINT_NAME" \
    "$FIRMWARE_MACHINE" "$FIRMWARE_PRODUCT_ID" 3>&-
status=$?

if [ "$status" -ne 0 ]; then
    runner_lifecycle ERROR "Installer exited with status $status."
    echo "@@ Installer exited with status $status."
    sleep "$FIRMWARE_ERROR_DELAY_SECONDS"
    firmware_message "Firmware installer failed" \
        "Ensure writing stopped, then power off."
    sync
else
    runner_lifecycle INFO "Installer completed with status 0."
fi

exit 0
