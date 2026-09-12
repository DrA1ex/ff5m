#!/bin/bash

## Early firmware image staging and hand-off.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

source /opt/config/mod/.shell/boot/stock_identity.sh || exit 1

FIRMWARE_INSTALL_STAGING_DIR=/data/.firmware
FIRMWARE_INSTALL_RUNNER_DIR=/data/.firmware-runner
FIRMWARE_INSTALL_LAUNCH_LOG=/data/logFiles/firmware-installer-launch.log
FIRMWARE_INSTALL_RESERVE_KB=16384
FIRMWARE_INSTALL_ERROR_DELAY_SECONDS=30
FIRMWARE_INSTALL_BASH=/bin/bash
FIRMWARE_INSTALL_TAR=tar
FIRMWARE_INSTALL_XZ=xz
FIRMWARE_INSTALL_DD=dd
FIRMWARE_INSTALL_DF=df

FIRMWARE_IMAGE=""
FIRMWARE_ENTRYPOINT=""
FIRMWARE_ENTRYPOINT_KIND=""
FIRMWARE_MACHINE=""
FIRMWARE_PRODUCT_ID=""
FIRMWARE_PARENT_STOPPED=0

is_unsigned_integer() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
        *) return 0 ;;
    esac
}

firmware_message() {
    local title=$1
    local detail=${2:-}
    local level=${3:-//}

    echo "$level $title" >&2
    [ -z "$detail" ] || echo "$level $detail" >&2
}

firmware_progress() {
    local title=$1
    local detail=$2

    echo "//% $title${detail:+: $detail}" >&2
}

stop_firmware_parent() {
    local parent_pid="${FIRMWARE_INSTALL_PARENT_PID:-$PPID}"

    [ "$FIRMWARE_PARENT_STOPPED" -eq 0 ] || return 0
    is_unsigned_integer "$parent_pid" || return 1
    [ "$parent_pid" -gt 1 ] || return 1

    killall -9 ffstartup-arm >/dev/null 2>&1 || true
    pidof ffstartup-arm >/dev/null 2>&1 && return 1

    echo "// Stopping stock firmware (PID $parent_pid)..."
    if ! kill -9 "$parent_pid" 2>/dev/null; then
        kill -0 "$parent_pid" 2>/dev/null || {
            FIRMWARE_PARENT_STOPPED=1
            return 0
        }
        return 1
    fi
    FIRMWARE_PARENT_STOPPED=1
}

fail_firmware_image() {
    local reason=$1
    local parent_stopped=0

    stop_firmware_parent && parent_stopped=1
    firmware_message "$reason" "The printer can now be powered off." "@@"
    sync

    # An accepted image must never return control to a live stock parent.
    if [ "$parent_stopped" -eq 0 ]; then
        while ! stop_firmware_parent; do
            sleep 1
        done
    fi

    exit 1
}

cleanup_firmware_staging() {
    [ -n "$FIRMWARE_INSTALL_STAGING_DIR" ] \
        && [ "$FIRMWARE_INSTALL_STAGING_DIR" != "/" ] || return 1
    [ -n "$FIRMWARE_INSTALL_RUNNER_DIR" ] \
        && [ "$FIRMWARE_INSTALL_RUNNER_DIR" != "/" ] || return 1
    [ "$FIRMWARE_INSTALL_STAGING_DIR" != "$FIRMWARE_INSTALL_RUNNER_DIR" ] \
        || return 1

    rm -rf "$FIRMWARE_INSTALL_STAGING_DIR" || return 1
    rm -rf "$FIRMWARE_INSTALL_RUNNER_DIR"
}

prepare_firmware_launch_log() {
    local log_dir=${FIRMWARE_INSTALL_LAUNCH_LOG%/*}

    [ -n "$log_dir" ] && [ "$log_dir" != "$FIRMWARE_INSTALL_LAUNCH_LOG" ] \
        || return 1
    mkdir -p "$log_dir" || return 1

    rm -f "$FIRMWARE_INSTALL_LAUNCH_LOG.3" || return 1
    mv -f "$FIRMWARE_INSTALL_LAUNCH_LOG.2" \
        "$FIRMWARE_INSTALL_LAUNCH_LOG.3" 2>/dev/null || true
    mv -f "$FIRMWARE_INSTALL_LAUNCH_LOG.1" \
        "$FIRMWARE_INSTALL_LAUNCH_LOG.2" 2>/dev/null || true
    mv -f "$FIRMWARE_INSTALL_LAUNCH_LOG" \
        "$FIRMWARE_INSTALL_LAUNCH_LOG.1" 2>/dev/null || true
    : > "$FIRMWARE_INSTALL_LAUNCH_LOG" || return 1
    chmod 0644 "$FIRMWARE_INSTALL_LAUNCH_LOG"
}

firmware_launch_event() {
    printf '%s | INFO | firmware-handoff | %s\n' \
        "$(date '+%Y-%m-%d %H:%M:%S')" "$1" \
        >> "$FIRMWARE_INSTALL_LAUNCH_LOG"
}

find_firmware_image() {
    local path=$1
    local image

    [ -d "$path" ] && [ -n "$FIRMWARE_MACHINE" ] || return 1
    for image in "$path"/"$FIRMWARE_MACHINE"-*.tar.xz \
            "$path"/"$FIRMWARE_MACHINE"-*.tgz; do
        [ -f "$image" ] && [ ! -L "$image" ] || continue
        printf '%s\n' "$image"
        return 0
    done

    return 1
}

firmware_image_matches_machine() {
    local image_name=${1##*/}

    case "$image_name" in
        "$FIRMWARE_MACHINE"-*.tar.xz|"$FIRMWARE_MACHINE"-*.tgz) return 0 ;;
        *) return 1 ;;
    esac
}

report_mismatched_firmware_image() {
    local path=$1
    local image image_name image_machine

    for image in "$path"/Adventurer5M*.tar.xz "$path"/Adventurer5M*.tgz; do
        [ -f "$image" ] && [ ! -L "$image" ] || continue
        firmware_image_matches_machine "$image" && continue
        image_name=${image##*/}
        image_machine=${image_name%%-*}
        echo "Firmware image: $image_name" >&2
        echo "?? Image model: $image_machine" >&2
        echo "?? Printer model: $FIRMWARE_MACHINE; skipped." >&2
        return 0
    done

    return 1
}

firmware_image_size_bytes() {
    local image=$1
    local bytes size_file
    local -a statuses

    case "$image" in
        *.tgz)
            wc -c < "$image" | awk '{ print $1 }'
            ;;
        *.tar.xz)
            bytes=$("$FIRMWARE_INSTALL_XZ" --robot --list "$image" 2>/dev/null \
                | awk -F '\t' '$1 == "totals" { print $5; found = 1 } END { exit !found }')
            if is_unsigned_integer "$bytes"; then
                printf '%s\n' "$bytes"
                return 0
            fi

            size_file="/tmp/forge-x-firmware-size.$$"
            rm -f "$size_file"
            "$FIRMWARE_INSTALL_XZ" -dc "$image" | wc -c > "$size_file"
            statuses=("${PIPESTATUS[@]}")
            [ "${statuses[0]}" -eq 0 ] && [ "${statuses[1]}" -eq 0 ] || {
                rm -f "$size_file"
                return 1
            }
            bytes=$(cat "$size_file" 2>/dev/null)
            rm -f "$size_file"
            is_unsigned_integer "$bytes" || return 1
            printf '%s\n' "$bytes"
            ;;
        *)
            return 1
            ;;
    esac
}

validate_firmware_archive() {
    local image=$1
    local list_file="/tmp/forge-x-firmware-list.$$"
    local magic status
    local -a statuses

    rm -f "$list_file"
    case "$image" in
        *.tgz)
            magic=$(od -An -tx1 -N6 "$image" 2>/dev/null | tr -d ' \n')
            case "$magic" in
                1f8b*|fd377a585a00*) return 1 ;;
            esac
            "$FIRMWARE_INSTALL_TAR" -tf "$image" > "$list_file"
            statuses=("$?")
            ;;
        *.tar.xz)
            "$FIRMWARE_INSTALL_XZ" -dc "$image" \
                | "$FIRMWARE_INSTALL_TAR" -tf - > "$list_file"
            statuses=("${PIPESTATUS[@]}")
            ;;
        *)
            return 1
            ;;
    esac

    for status in "${statuses[@]}"; do
        if [ "$status" -ne 0 ]; then
            rm -f "$list_file"
            return 1
        fi
    done

    # The staging directory is intentionally disposable, but archive members
    # still must not be able to address anything outside it.
    if ! awk '
        {
            path = $0
            while (substr(path, 1, 2) == "./") path = substr(path, 3)
            if (path == "" || path == ".") next
            if (substr(path, 1, 1) == "/") exit 1
            count = split(path, parts, "/")
            for (i = 1; i <= count; i++) if (parts[i] == "..") exit 1
        }
    ' "$list_file"; then
        rm -f "$list_file"
        return 1
    fi

    rm -f "$list_file"
}

check_firmware_space() {
    local image=$1
    local unpacked_bytes required_kb required_mb free_kb free_mb staging_parent

    unpacked_bytes=$(firmware_image_size_bytes "$image") || return 1
    is_unsigned_integer "$unpacked_bytes" || return 1
    is_unsigned_integer "$FIRMWARE_INSTALL_RESERVE_KB" || return 1

    required_kb=$(((unpacked_bytes + 1023) / 1024 + FIRMWARE_INSTALL_RESERVE_KB))
    staging_parent=${FIRMWARE_INSTALL_STAGING_DIR%/*}
    [ -n "$staging_parent" ] || staging_parent=/
    free_kb=$("$FIRMWARE_INSTALL_DF" -Pk "$staging_parent" 2>/dev/null \
        | awk 'NR == 2 { print $4 }')
    is_unsigned_integer "$free_kb" || return 1

    required_mb=$(((required_kb + 1023) / 1024))
    free_mb=$((free_kb / 1024))
    echo "// Space: $required_mb MB needed, $free_mb MB free"
    [ "$free_kb" -ge "$required_kb" ]
}

stream_firmware_image() {
    local image=$1
    local block_size=262144
    local size blocks done=0 percent target count

    size=$(wc -c < "$image" | awk '{ print $1 }') || return 1
    is_unsigned_integer "$size" || return 1
    [ "$size" -gt 0 ] || return 1
    blocks=$(((size + block_size - 1) / block_size))

    percent=5
    while [ "$percent" -le 95 ]; do
        target=$(((blocks * percent + 99) / 100))
        count=$((target - done))
        if [ "$count" -gt 0 ]; then
            "$FIRMWARE_INSTALL_DD" if="$image" bs="$block_size" \
                skip="$done" count="$count" 2>/dev/null || return 1
        fi

        firmware_progress "Extracting firmware" "${percent}%"
        done=$target
        percent=$((percent + 5))
    done

    count=$((blocks - done))
    [ "$count" -eq 0 ] || "$FIRMWARE_INSTALL_DD" if="$image" \
        bs="$block_size" skip="$done" count="$count" 2>/dev/null
}

extract_firmware_image() {
    local image=$1
    local status
    local -a statuses

    case "$image" in
        *.tgz)
            stream_firmware_image "$image" \
                | "$FIRMWARE_INSTALL_TAR" -xf - -C "$FIRMWARE_INSTALL_STAGING_DIR"
            statuses=("${PIPESTATUS[@]}")
            ;;
        *.tar.xz)
            stream_firmware_image "$image" \
                | "$FIRMWARE_INSTALL_XZ" -dc \
                | "$FIRMWARE_INSTALL_TAR" -xf - -C "$FIRMWARE_INSTALL_STAGING_DIR"
            statuses=("${PIPESTATUS[@]}")
            ;;
        *)
            return 1
            ;;
    esac

    for status in "${statuses[@]}"; do
        [ "$status" -eq 0 ] || return 1
    done
    firmware_progress "Extracting firmware" "100%"
}

select_firmware_entrypoint() {
    local binary="$FIRMWARE_INSTALL_STAGING_DIR/forge-x-init"
    local forge_x_script="$FIRMWARE_INSTALL_STAGING_DIR/forge-x-init.sh"
    local flashforge_script="$FIRMWARE_INSTALL_STAGING_DIR/flashforge_init.sh"
    local custom_present=0
    local magic

    if [ -e "$binary" ] || [ -L "$binary" ]; then
        custom_present=1
        if [ -f "$binary" ] && [ ! -L "$binary" ] && [ -x "$binary" ]; then
            magic=$(od -An -tx1 -N4 "$binary" 2>/dev/null | tr -d ' \n')
            if [ "$magic" = "7f454c46" ]; then
                FIRMWARE_ENTRYPOINT=$binary
                FIRMWARE_ENTRYPOINT_KIND=binary
                return 0
            fi
        fi
    fi

    if [ -e "$forge_x_script" ] || [ -L "$forge_x_script" ]; then
        custom_present=1
        if [ -f "$forge_x_script" ] && [ ! -L "$forge_x_script" ] \
                && "$FIRMWARE_INSTALL_BASH" -n "$forge_x_script"; then
            FIRMWARE_ENTRYPOINT=$forge_x_script
            FIRMWARE_ENTRYPOINT_KIND=shell
            return 0
        fi
    fi

    [ "$custom_present" -eq 0 ] || return 1
    [ -f "$flashforge_script" ] && [ ! -L "$flashforge_script" ] || return 1
    "$FIRMWARE_INSTALL_BASH" -n "$flashforge_script" || return 1
    FIRMWARE_ENTRYPOINT=$flashforge_script
    FIRMWARE_ENTRYPOINT_KIND=shell
}

prepare_firmware_runner() {
    local runner_source=/opt/config/mod/.shell/boot/install-image-runner.sh
    local runner="$FIRMWARE_INSTALL_RUNNER_DIR/runner.sh"

    [ -f "$runner_source" ] && [ ! -L "$runner_source" ] || return 1
    [ ! -e "$FIRMWARE_INSTALL_RUNNER_DIR" ] \
        && [ ! -L "$FIRMWARE_INSTALL_RUNNER_DIR" ] || return 1
    mkdir -p "$FIRMWARE_INSTALL_RUNNER_DIR" || return 1
    cp -f "$runner_source" "$runner" || return 1
    chmod 755 "$runner"
}

handoff_firmware_entrypoint() {
    local runner="$FIRMWARE_INSTALL_RUNNER_DIR/runner.sh"
    local entrypoint_name=${FIRMWARE_ENTRYPOINT##*/}

    prepare_firmware_launch_log || return 1
    firmware_launch_event "Preparing detached firmware runner."
    firmware_message "Preparing firmware installer" \
        "Waiting for Forge-X services to stop."
    stop_firmware_parent || {
        firmware_launch_event "Failed to stop the previous firmware parent."
        return 1
    }
    prepare_firmware_runner || {
        firmware_launch_event "Failed to prepare the detached firmware runner."
        return 1
    }

    export FORGE_X_FIRMWARE_IMAGE="$FIRMWARE_IMAGE"
    firmware_launch_event "Starting $entrypoint_name through the detached firmware runner."
    nohup "$runner" "$FIRMWARE_INSTALL_STAGING_DIR" \
        "$entrypoint_name" "$FIRMWARE_ENTRYPOINT_KIND" \
        "$FIRMWARE_MACHINE" "$FIRMWARE_PRODUCT_ID" \
        "$FIRMWARE_INSTALL_ERROR_DELAY_SECONDS" \
        </dev/null >> "$FIRMWARE_INSTALL_LAUNCH_LOG" 2>&1 &
    firmware_launch_event "Detached firmware runner started (PID $!)."
}

install_firmware_image() {
    FIRMWARE_IMAGE=$1

    load_stock_printer_identity \
        || fail_firmware_image "Cannot identify printer model."

    if [ -d "$FIRMWARE_IMAGE" ]; then
        FIRMWARE_IMAGE=$(find_firmware_image "$FIRMWARE_IMAGE") \
            || fail_firmware_image "Firmware image not found."
    fi

    [ -f "$FIRMWARE_IMAGE" ] && [ ! -L "$FIRMWARE_IMAGE" ] \
        || fail_firmware_image "Firmware image not found."
    firmware_image_matches_machine "$FIRMWARE_IMAGE" \
        || fail_firmware_image "Image does not match printer model."

    stop_firmware_parent \
        || fail_firmware_image "Cannot stop stock firmware."
    mount_data_partition

    firmware_message "Preparing firmware image" "Checking archive..."
    validate_firmware_archive "$FIRMWARE_IMAGE" \
        || fail_firmware_image "Archive is corrupt or unsafe."
    check_firmware_space "$FIRMWARE_IMAGE" \
        || fail_firmware_image "Not enough space on /data."

    [ -n "$FIRMWARE_INSTALL_STAGING_DIR" ] \
        && [ "$FIRMWARE_INSTALL_STAGING_DIR" != "/" ] \
        || fail_firmware_image "Invalid staging directory."
    cleanup_firmware_staging \
        || fail_firmware_image "Cannot clear staging directory."
    mkdir -p "$FIRMWARE_INSTALL_STAGING_DIR" \
        || fail_firmware_image "Cannot create staging directory."

    extract_firmware_image "$FIRMWARE_IMAGE" \
        || fail_firmware_image "Cannot extract firmware image."
    sync

    select_firmware_entrypoint \
        || fail_firmware_image "No valid installer in image."

    handoff_firmware_entrypoint \
        || fail_firmware_image "Cannot start firmware installer."
}

if [ "$#" -eq 1 ] && [ "$1" = "cleanup" ]; then
    cleanup_firmware_staging
    exit $?
fi

if [ "$#" -eq 2 ] && [ "$1" = "test" ]; then
    load_stock_printer_identity || exit 1
    find_firmware_image "$2" && exit 0
    report_mismatched_firmware_image "$2" || true
    exit 1
fi

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 test <directory> | cleanup | <image-or-directory>"
    exit 2
fi

source /opt/config/mod/.shell/common.sh

install_firmware_image "$1"
