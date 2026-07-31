#!/bin/bash

## Early USB storage discovery and mounting.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

# Do not depend on stock storage services here: both boot-flag detection and
# swap initialization run before those services. All paths are overridable so
# the early-boot behavior can be tested without real block devices.

USB_STORAGE_SYS_BLOCK_ROOT="${USB_STORAGE_SYS_BLOCK_ROOT:-/sys/block}"
USB_STORAGE_DEV_ROOT="${USB_STORAGE_DEV_ROOT:-/dev}"
USB_STORAGE_PROC_PARTITIONS="${USB_STORAGE_PROC_PARTITIONS:-/proc/partitions}"
USB_STORAGE_PROC_MOUNTS="${USB_STORAGE_PROC_MOUNTS:-/proc/mounts}"
USB_STORAGE_MOUNT_ROOT="${USB_STORAGE_MOUNT_ROOT:-/tmp}"
USB_STORAGE_LSBLK="${USB_STORAGE_LSBLK:-lsblk}"
USB_STORAGE_UDEVADM="${USB_STORAGE_UDEVADM:-udevadm}"
USB_STORAGE_OPERATION_LOCK="${USB_STORAGE_OPERATION_LOCK:-/tmp/forge-x-usb-operation}"
USB_STORAGE_REQUIRE_BLOCK_DEVICES="${USB_STORAGE_REQUIRE_BLOCK_DEVICES:-1}"

USB_STORAGE_MOUNT_POINT=""
USB_STORAGE_MOUNT_FILESYSTEM=""
USB_STORAGE_MOUNTED_BY_US=0


usb_storage_device_ready() {
    [ -e "$1" ] || return 1
    [ "$USB_STORAGE_REQUIRE_BLOCK_DEVICES" = "0" ] || [ -b "$1" ]
}


usb_storage_operation_acquire() {
    local owner=""

    if mkdir "$USB_STORAGE_OPERATION_LOCK" 2>/dev/null; then
        printf '%s\n' "$$" > "$USB_STORAGE_OPERATION_LOCK/owner"
        return 0
    fi

    [ -f "$USB_STORAGE_OPERATION_LOCK/owner" ] \
        && read -r owner < "$USB_STORAGE_OPERATION_LOCK/owner"
    case "$owner" in
        ''|*[!0-9]*) return 1 ;;
    esac
    kill -0 "$owner" 2>/dev/null && return 1

    # The previous owner no longer exists (for example after SIGKILL). Only
    # remove the exact lock contents before one atomic retry.
    rm -f "$USB_STORAGE_OPERATION_LOCK/owner" 2>/dev/null
    rmdir "$USB_STORAGE_OPERATION_LOCK" 2>/dev/null || return 1
    mkdir "$USB_STORAGE_OPERATION_LOCK" 2>/dev/null || return 1
    printf '%s\n' "$$" > "$USB_STORAGE_OPERATION_LOCK/owner"
}


usb_storage_operation_release() {
    local owner=""

    [ -f "$USB_STORAGE_OPERATION_LOCK/owner" ] \
        && read -r owner < "$USB_STORAGE_OPERATION_LOCK/owner"
    [ "$owner" = "$$" ] || return 1
    rm -f "$USB_STORAGE_OPERATION_LOCK/owner" 2>/dev/null
    rmdir "$USB_STORAGE_OPERATION_LOCK" 2>/dev/null
}


usb_storage_is_usb_disk() {
    local device_name device_type
    device_name=$(basename "$1")
    readlink -f "$USB_STORAGE_SYS_BLOCK_ROOT/$device_name" 2>/dev/null \
        | grep -q '/usb' || return 1

    # SCSI type 0 is direct-access storage. If the type is available, exclude
    # optical drives and other USB block devices that must never be formatted.
    if [ -f "$USB_STORAGE_SYS_BLOCK_ROOT/$device_name/device/type" ]; then
        device_type=$(cat \
            "$USB_STORAGE_SYS_BLOCK_ROOT/$device_name/device/type" 2>/dev/null)
        [ "$device_type" = "0" ] || return 1
    fi
}


usb_storage_disks() {
    local sys_device device_name size_kib

    for sys_device in "$USB_STORAGE_SYS_BLOCK_ROOT"/*; do
        [ -e "$sys_device" ] || continue
        device_name=$(basename "$sys_device")
        usb_storage_is_usb_disk "$device_name" || continue
        usb_storage_device_ready \
            "$USB_STORAGE_DEV_ROOT/$device_name" || continue
        size_kib=$(awk -v dev="$device_name" \
            '$4 == dev { print $3; exit }' "$USB_STORAGE_PROC_PARTITIONS")
        [ -n "$size_kib" ] && [ "$size_kib" -gt 0 ] || continue
        echo "$size_kib $USB_STORAGE_DEV_ROOT/$device_name"
    done | sort -k1,1nr
}


usb_storage_has_partitions() {
    local disk_name sys_partition
    disk_name=$(basename "$1")

    for sys_partition in "$USB_STORAGE_SYS_BLOCK_ROOT/$disk_name"/*; do
        [ -f "$sys_partition/partition" ] && return 0
    done
    return 1
}


usb_storage_partitions() {
    local disk_name sys_partition partition_name size_kib
    disk_name=$(basename "$1")

    for sys_partition in "$USB_STORAGE_SYS_BLOCK_ROOT/$disk_name"/*; do
        [ -f "$sys_partition/partition" ] || continue
        partition_name=$(basename "$sys_partition")
        usb_storage_device_ready \
            "$USB_STORAGE_DEV_ROOT/$partition_name" || continue
        size_kib=$(awk -v dev="$partition_name" \
            '$4 == dev { print $3; exit }' "$USB_STORAGE_PROC_PARTITIONS")
        [ -n "$size_kib" ] || continue
        echo "$size_kib $USB_STORAGE_DEV_ROOT/$partition_name"
    done | sort -k1,1nr
}


usb_storage_partition_path() {
    local disk="$1"
    local number="$2"

    case "$(basename "$disk")" in
        *[0-9]) echo "${disk}p${number}" ;;
        *) echo "${disk}${number}" ;;
    esac
}


usb_storage_candidates() {
    local sys_device device_name size_kib

    for sys_device in "$USB_STORAGE_SYS_BLOCK_ROOT"/*; do
        [ -e "$sys_device" ] || continue
        device_name=$(basename "$sys_device")
        usb_storage_is_usb_disk "$device_name" || continue

        if usb_storage_has_partitions "$device_name"; then
            usb_storage_partitions "$device_name"
            continue
        fi

        # A filesystem or swap signature may also be written directly to a
        # whole USB device without a partition table.
        if usb_storage_device_ready "$USB_STORAGE_DEV_ROOT/$device_name"; then
            awk -v dev="$device_name" -v root="$USB_STORAGE_DEV_ROOT" \
                '$4 == dev && $3 > 0 { print $3, root "/" $4 }' \
                "$USB_STORAGE_PROC_PARTITIONS"
        fi
    done | sort -k1,1nr
}


usb_storage_has_enumerated_disk() {
    local sys_device device_name

    for sys_device in "$USB_STORAGE_SYS_BLOCK_ROOT"/*; do
        [ -e "$sys_device" ] || continue
        device_name=$(basename "$sys_device")
        usb_storage_is_usb_disk "$device_name" && return 0
    done
    return 1
}


usb_storage_wait_for_candidates() {
    local timeout="${1:-10}"
    local elapsed=0

    # Let already queued kernel/udev events finish first. Unlike an unconditional
    # sleep this normally returns immediately once device nodes are ready.
    if command -v "$USB_STORAGE_UDEVADM" >/dev/null 2>&1; then
        "$USB_STORAGE_UDEVADM" settle --timeout="$timeout" 2>/dev/null || true
    fi

    while [ "$elapsed" -le "$timeout" ]; do
        if [ -n "$(usb_storage_candidates)" ]; then
            return 0
        fi
        [ "$elapsed" -eq "$timeout" ] && break
        sleep 1
        elapsed=$((elapsed + 1))
    done
    return 1
}


usb_storage_filesystem() {
    "$USB_STORAGE_LSBLK" -dnro FSTYPE "$1" 2>/dev/null \
        | awk 'NF { print $1; exit }'
}


usb_storage_supports_mount() {
    case "$1" in
        ext2|ext3|ext4|vfat|msdos|fat|fat12|fat16|fat32) return 0 ;;
        *) return 1 ;;
    esac
}


usb_storage_mounted_path() {
    awk -v device="$1" '$1 == device { print $2; exit }' \
        "$USB_STORAGE_PROC_MOUNTS"
}


usb_storage_mounted_filesystem() {
    awk -v device="$1" '$1 == device { print $3; exit }' \
        "$USB_STORAGE_PROC_MOUNTS"
}


usb_storage_mount_is_writable() {
    local options
    options=$(awk -v device="$1" '$1 == device { print $4; exit }' \
        "$USB_STORAGE_PROC_MOUNTS")
    case ",$options," in
        *,rw,*) return 0 ;;
        *) return 1 ;;
    esac
}


usb_storage_try_mount() {
    local partition="$1"
    local mount_point="$2"
    local filesystem="$3"
    local access="${4:-rw}"

    case "$filesystem" in
        fat32|fat16|fat12) filesystem="vfat" ;;
        fat) filesystem="msdos" ;;
    esac

    case "$filesystem" in
        vfat|msdos)
            mount -t "$filesystem" \
                -o "$access,noatime,codepage=437,iocharset=utf8" \
                "$partition" "$mount_point" \
                || mount -t "$filesystem" -o "$access,noatime" \
                    "$partition" "$mount_point"
            ;;
        ext2|ext3|ext4)
            mount -t "$filesystem" -o "$access,noatime" \
                "$partition" "$mount_point"
            ;;
        *)
            return 1
            ;;
    esac
}


usb_storage_mount_candidate() {
    local partition="$1"
    local filesystem="$2"
    local mount_prefix="${3:-forge-x-usb}"
    local access="${4:-rw}"
    local mount_point candidate candidates

    USB_STORAGE_MOUNT_POINT=""
    USB_STORAGE_MOUNT_FILESYSTEM=""
    USB_STORAGE_MOUNTED_BY_US=0

    case "$access" in
        ro|rw) ;;
        *) echo "Invalid USB mount access: $access"; return 1 ;;
    esac

    mount_point=$(usb_storage_mounted_path "$partition")
    if [ -n "$mount_point" ]; then
        if [ "$access" = "rw" ] \
                && ! usb_storage_mount_is_writable "$partition"; then
            echo "USB partition $partition is mounted read-only."
            return 1
        fi
        USB_STORAGE_MOUNT_POINT="$mount_point"
        USB_STORAGE_MOUNT_FILESYSTEM=$(
            usb_storage_mounted_filesystem "$partition"
        )
        return 0
    fi

    mount_point="$USB_STORAGE_MOUNT_ROOT/${mount_prefix}-$(basename "$partition")"
    mkdir -p "$mount_point" || return 1

    if [ -n "$filesystem" ]; then
        candidates="$filesystem"
    else
        # FSTYPE probing can be late too. Keep the order used by typical USB
        # print drives, then try the supported Linux filesystems.
        candidates="vfat msdos ext4 ext3 ext2"
    fi

    for candidate in $candidates; do
        usb_storage_supports_mount "$candidate" || continue
        if usb_storage_try_mount \
                "$partition" "$mount_point" "$candidate" "$access"; then
            case "$candidate" in
                fat32|fat16|fat12) candidate="vfat" ;;
                fat) candidate="msdos" ;;
            esac
            USB_STORAGE_MOUNT_POINT="$mount_point"
            USB_STORAGE_MOUNT_FILESYSTEM="$candidate"
            USB_STORAGE_MOUNTED_BY_US=1
            return 0
        fi
    done

    echo "Failed to mount $partition (${filesystem:-unknown filesystem})."
    rmdir "$mount_point" 2>/dev/null
    return 1
}


usb_storage_release_mount() {
    if [ "$USB_STORAGE_MOUNTED_BY_US" -eq 1 ] \
            && [ -n "$USB_STORAGE_MOUNT_POINT" ]; then
        umount "$USB_STORAGE_MOUNT_POINT" 2>/dev/null
        rmdir "$USB_STORAGE_MOUNT_POINT" 2>/dev/null
    fi
    USB_STORAGE_MOUNT_POINT=""
    USB_STORAGE_MOUNT_FILESYSTEM=""
    USB_STORAGE_MOUNTED_BY_US=0
}
