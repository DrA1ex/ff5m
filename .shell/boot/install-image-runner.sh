#!/bin/bash

## Detached firmware image entrypoint runner.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

is_unsigned_integer() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
        *) return 0 ;;
    esac
}

firmware_screen() {
    local title=$1
    local detail=$2
    local typer="$FIRMWARE_RUNNER_DIR/.forge-x-install-typer"
    local runtime="$FIRMWARE_RUNNER_DIR/.forge-x-install-runtime"
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
            -f "JetBrainsMono 11pt" --max-width 720 -t "$detail" \
        >/dev/null 2>&1 || true
}

mod_paths_busy() {
    if mount | grep -Eq '/data/\.mod| /root/printer_data'; then
        return 0
    fi

    if lsof 2>&1 \
            | grep -Eq '/data/\.mod|/opt/config/mod|/root/printer_data'; then
        return 0
    fi

    return 1
}

if [ "$#" -ne 5 ]; then
    exit 2
fi

FIRMWARE_ENTRYPOINT_NAME=$1
FIRMWARE_ENTRYPOINT_KIND=$2
FIRMWARE_MACHINE=$3
FIRMWARE_PRODUCT_ID=$4
FIRMWARE_ERROR_DELAY_SECONDS=$5
FIRMWARE_RUNNER_DIR=$(cd "$(dirname "$0")" && pwd) || exit 1

case "$FIRMWARE_ENTRYPOINT_KIND:$FIRMWARE_ENTRYPOINT_NAME" in
    binary:forge-x-init|shell:forge-x-init.sh|shell:flashforge_init.sh) ;;
    *) exit 2 ;;
esac
case "$FIRMWARE_MACHINE:$FIRMWARE_PRODUCT_ID" in
    Adventurer5M:0023|Adventurer5MPro:0024) ;;
    *) exit 2 ;;
esac
is_unsigned_integer "$FIRMWARE_ERROR_DELAY_SECONDS" || exit 2

unset LD_PRELOAD
unset LD_LIBRARY_PATH

cd "$FIRMWARE_RUNNER_DIR" || exit 1
while mod_paths_busy; do
    sleep 1
done

rm -f /tmp/logged_message_queue
export FORGE_X_FIRMWARE_DIR="$FIRMWARE_RUNNER_DIR"

if [ "$FIRMWARE_ENTRYPOINT_KIND" = "binary" ]; then
    "./$FIRMWARE_ENTRYPOINT_NAME" "$FIRMWARE_MACHINE" "$FIRMWARE_PRODUCT_ID"
else
    /bin/bash "./$FIRMWARE_ENTRYPOINT_NAME" \
        "$FIRMWARE_MACHINE" "$FIRMWARE_PRODUCT_ID"
fi
status=$?

if [ "$status" -ne 0 ]; then
    echo "@@ Installer exited with status $status."
    sleep "$FIRMWARE_ERROR_DELAY_SECONDS"
    firmware_screen "Firmware installer failed" \
        "Ensure writing stopped, then power off."
    sync
fi

exit 0
