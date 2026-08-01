#!/bin/bash

## Download a full Forge-X firmware image to a FAT32 USB drive.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

set -u

source /opt/config/mod/.shell/common.sh
source "$SCRIPTS/boot/usb_storage.sh"

MODEL_ARG=${1:-}
LOCKED=0
USB_MOUNTED=0
CHROOT_BIND=""

prompt_error() {
    echo "// action:prompt_end"
    echo "// action:prompt_begin Firmware update failed"
    echo "// action:prompt_text $1"
    echo "// action:prompt_footer_button Close|RESPOND TYPE=command MSG=action:prompt_end|error"
    echo "// action:prompt_show"
}

fail() {
    echo "!! $1"
    prompt_error "$1"
    exit 1
}

cleanup() {
    if [ -n "$CHROOT_BIND" ]; then
        umount "$CHROOT_BIND" >/dev/null 2>&1 || true
        rmdir "$CHROOT_BIND" >/dev/null 2>&1 || true
    fi
    [ "$USB_MOUNTED" -eq 1 ] && usb_storage_release_mount >/dev/null 2>&1 || true
    [ "$LOCKED" -eq 1 ] && usb_storage_operation_release >/dev/null 2>&1 || true
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

case "$MODEL_ARG" in
    5M) MODEL=Adventurer5M ;;
    PRO) MODEL=Adventurer5MPro ;;
    *) fail "Invalid printer model selected." ;;
esac

usb_storage_operation_acquire || fail "Another USB operation is already running."
LOCKED=1

usb_storage_has_enumerated_disk \
    || fail "No USB drive found. Insert a FAT32 drive and run DOWNLOAD_FIRMWARE_UPDATE again."
usb_storage_wait_for_candidates 3 \
    || fail "The USB drive is not ready. Reconnect it and try again."

DISK_COUNT=$(usb_storage_disks | awk 'NF { count++ } END { print count + 0 }')
[ "$DISK_COUNT" -eq 1 ] \
    || fail "Exactly one USB drive is required. Found $DISK_COUNT."

PARTITION=""
FILESYSTEM=""
CANDIDATE_COUNT=0
while read -r _ candidate; do
    [ -n "$candidate" ] || continue
    CANDIDATE_COUNT=$((CANDIDATE_COUNT + 1))
    filesystem=$(usb_storage_filesystem "$candidate" 2>/dev/null || true)
    case "$filesystem" in
        vfat|msdos|fat|fat12|fat16|fat32)
            PARTITION=$candidate
            FILESYSTEM=$filesystem
            ;;
    esac
done < <(usb_storage_candidates)

[ "$CANDIDATE_COUNT" -eq 1 ] && [ -n "$PARTITION" ] \
    || fail "The USB drive must contain one FAT32 partition. Run PREPARE_USB and try again."

usb_storage_mount_candidate "$PARTITION" "$FILESYSTEM" forge-x-firmware rw \
    || fail "Unable to mount the USB drive. Run PREPARE_USB and try again."
USB_MOUNTED=1

[ -x "$MOD/bin/python3" ] && [ -r "$MOD$PY/zupdate.py" ] \
    || fail "Forge-X chroot Python is unavailable. Restart the printer and try again."

CHROOT_PATH="/tmp/forge-x-zupdate-$$"
CHROOT_BIND="$MOD$CHROOT_PATH"
mkdir -p "$CHROOT_BIND" || fail "Unable to prepare the chroot USB mount."
mount --bind "$USB_STORAGE_MOUNT_POINT" "$CHROOT_BIND" \
    || fail "Unable to expose the USB drive inside the Forge-X chroot."

update_download_prompt() {
    local percent=$1 message=$2
    local filled=$((percent / 5)) bar empty
    printf -v bar '%*s' "$filled" ''; bar=${bar// /#}
    printf -v empty '%*s' "$((20 - filled))" ''; empty=${empty// /-}
    echo "// action:prompt_begin Downloading firmware"
    echo "// action:prompt_text $message"
    echo "// action:prompt_text [$bar$empty] $percent%"
    echo "// action:prompt_show"
}

chroot "$MOD" /bin/python3 -u "$PY/zupdate.py" "$MODEL" "$CHROOT_PATH" 2>&1 |
while IFS= read -r line; do
    case "$line" in
        '@@PROGRESS|'*)
            data=${line#@@PROGRESS|}; percent=${data%%|*}; message=${data#*|}
            update_download_prompt "$percent" "$message"
            echo "// $message ($percent%)"
            ;;
        *) echo "$line" ;;
    esac
done
status=${PIPESTATUS[0]}
[ "$status" -eq 0 ] || exit "$status"

sync
