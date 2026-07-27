#!/bin/bash

## Inspect and prepare USB storage from a G-code dialog.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

USB_PREPARE_SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$USB_PREPARE_SCRIPT_DIR/../boot/usb_storage.sh"

USB_PREPARE_PROC_SWAPS="${USB_PREPARE_PROC_SWAPS:-/proc/swaps}"
USB_PREPARE_FDISK="${USB_PREPARE_FDISK:-busybox fdisk}"
USB_PREPARE_MKDOSFS="${USB_PREPARE_MKDOSFS:-busybox mkdosfs}"
USB_PREPARE_MKE2FS="${USB_PREPARE_MKE2FS:-busybox mke2fs}"
USB_PREPARE_DD="${USB_PREPARE_DD:-dd}"


usb_prepare_error() {
    echo "!! $*"
}


usb_prepare_result_prompt() {
    local status="$1"
    local format="$2"
    local device="$3"

    echo "// action:prompt_end"
    if [ "$status" -eq 0 ]; then
        echo "// action:prompt_begin USB preparation complete"
        echo "// action:prompt_text /dev/$device was formatted as $format and is ready to use."
        echo "// action:prompt_footer_button Done|RESPOND TYPE=command MSG=action:prompt_end|primary"
    else
        echo "// action:prompt_begin USB preparation failed"
        echo "// action:prompt_text Could not format /dev/$device as $format. See the console for details."
        echo "// action:prompt_footer_button Close|RESPOND TYPE=command MSG=action:prompt_end|error"
    fi
    echo "// action:prompt_show"
}


usb_prepare_size() {
    awk -v kib="$1" 'BEGIN {
        if (kib >= 1048576) printf "%.1f GiB", kib / 1048576;
        else printf "%.1f MiB", kib / 1024;
    }'
}


usb_prepare_device_name() {
    basename "$1"
}


usb_prepare_identity() {
    local disk="$1"
    local size_kib="$2"
    local sys_path model serial="" ancestor

    sys_path=$(readlink -f \
        "$USB_STORAGE_SYS_BLOCK_ROOT/$(usb_prepare_device_name "$disk")" \
        2>/dev/null)
    model=$(cat "$USB_STORAGE_SYS_BLOCK_ROOT/$(usb_prepare_device_name "$disk")/device/model" \
        2>/dev/null | tr -cd '[:alnum:]_.-')
    ancestor="$sys_path"
    while [ -n "$ancestor" ] && [ "$ancestor" != "/" ]; do
        if [ -f "$ancestor/serial" ]; then
            serial=$(tr -cd '[:alnum:]_.-' < "$ancestor/serial")
            break
        fi
        ancestor=$(dirname "$ancestor")
    done
    printf '%s:%s:%s:%s' "$sys_path" "$size_kib" "$model" "$serial" \
        | cksum | awk '{print $1}'
}


usb_prepare_partitions_summary() {
    local disk="$1"
    local disk_name size_kib partition filesystem partitions summary=""
    disk_name=$(usb_prepare_device_name "$disk")

    partitions=$(usb_storage_partitions "$disk")
    while read -r size_kib partition; do
        [ -n "$partition" ] || continue
        filesystem=$(usb_storage_filesystem "$partition")
        [ -n "$summary" ] && summary="$summary, "
        summary="$summary$(basename "$partition") ${filesystem:-unknown} $(usb_prepare_size "$size_kib")"
    done <<< "$partitions"

    if [ -z "$summary" ]; then
        size_kib=$(usb_storage_disks \
            | awk -v target="$disk" '$2 == target { print $1; exit }')
        filesystem=$(usb_storage_filesystem "$disk")
        summary="$disk_name ${filesystem:-unknown} $(usb_prepare_size "$size_kib") (whole disk)"
    fi

    echo "$summary"
}


usb_prepare_prompt() {
    local wait_seconds disks count size_kib disk device identity
    local description

    wait_seconds="${USB_PREPARE_WAIT_SECONDS:-3}"
    if ! usb_storage_wait_for_candidates "$wait_seconds"; then
        usb_prepare_error "No USB storage found. Insert one drive and run PREPARE_USB again."
        return 1
    fi

    disks=$(usb_storage_disks)
    count=$(awk 'NF { count++ } END { print count + 0 }' <<< "$disks")
    if [ "$count" -ne 1 ]; then
        usb_prepare_error "Exactly one USB drive is required; found $count."
        return 1
    fi

    read -r size_kib disk <<< "$disks"
    device=$(usb_prepare_device_name "$disk")
    identity=$(usb_prepare_identity "$disk" "$size_kib")
    description=$(usb_prepare_partitions_summary "$disk")

    echo "// action:prompt_begin Prepare USB drive"
    echo "// action:prompt_text Found $disk: $(usb_prepare_size "$size_kib")."
    echo "// action:prompt_text Current layout: $description."
    echo "// action:prompt_text Both formats support USB swap and file storage. FAT32 has the widest printer and desktop compatibility."
    echo "// action:prompt_text The next step will ask for final confirmation before erasing all data."
    echo "// action:prompt_button_group_start"
    echo "// action:prompt_button FAT32 (recommended)|_PREPARE_USB_CONFIRM FORMAT=FAT32 DEVICE=$device ID=$identity|warning"
    echo "// action:prompt_button Linux EXT|_PREPARE_USB_CONFIRM FORMAT=EXT DEVICE=$device ID=$identity|warning"
    echo "// action:prompt_button_group_end"
    echo "// action:prompt_footer_button Cancel|RESPOND TYPE=command MSG=action:prompt_end|secondary"
    echo "// action:prompt_show"
}


usb_prepare_swapoff_for_mount() {
    local mount_point="$1"
    local swap_path

    while read -r swap_path _; do
        [ "$swap_path" = "Filename" ] && continue
        case "$swap_path" in
            "$mount_point"/*) usb_prepare_swapoff "$swap_path" || return 1 ;;
        esac
    done < "$USB_PREPARE_PROC_SWAPS"
}


usb_prepare_swapoff() {
    if command -v swapoff >/dev/null 2>&1; then
        command swapoff "$@"
    else
        busybox swapoff "$@"
    fi
}


usb_prepare_unmount_disk() {
    local disk="$1"
    local device mount_point

    while read -r device; do
        [ -e "$device" ] || continue
        usb_prepare_swapoff "$device" 2>/dev/null || true
        while :; do
            mount_point=$(usb_storage_mounted_path "$device")
            [ -n "$mount_point" ] || break
            usb_prepare_swapoff_for_mount "$mount_point" || return 1
            if ! umount "$mount_point"; then
                usb_prepare_error "Cannot unmount busy USB path $mount_point; nothing was erased."
                return 1
            fi
        done
    done < <({ echo "$disk"; usb_storage_partitions "$disk" | awk '{print $2}'; })
}


usb_prepare_wait_for_partition() {
    local partition="$1"
    local elapsed=0

    while [ "$elapsed" -le 10 ]; do
        [ -e "$partition" ] && return 0
        sleep 1
        elapsed=$((elapsed + 1))
    done
    return 1
}


usb_prepare_format() {
    local format="$1"
    local requested_device="$2"
    local requested_identity="$3"
    local disk current_size current_identity fdisk_output
    local partition filesystem

    case "$format" in
        EXT|FAT32) ;;
        *) usb_prepare_error "Unsupported format '$format'."; return 1 ;;
    esac
    case "$requested_device" in
        ''|.*|-*|*[!A-Za-z0-9._-]*)
            usb_prepare_error "Invalid USB device name."
            return 1
            ;;
    esac
    case "$requested_identity" in
        *[!0-9]*|'') usb_prepare_error "Invalid USB device ID."; return 1 ;;
    esac

    disk="$USB_STORAGE_DEV_ROOT/$requested_device"
    echo "// Validating selected USB drive $disk..."
    current_size=$(usb_storage_disks \
        | awk -v target="$disk" '$2 == target { print $1; exit }')
    current_identity=$(usb_prepare_identity "$disk" "$current_size")
    if [ -z "$current_size" ] \
            || [ "$current_identity" != "$requested_identity" ] \
            || ! usb_storage_is_usb_disk "$disk"; then
        usb_prepare_error "The confirmed USB drive changed or disappeared; nothing was erased."
        return 1
    fi

    echo "// Unmounting existing filesystems and swap on $disk..."
    usb_prepare_unmount_disk "$disk" || return 1

    echo "// Erasing partition table on $disk..."
    "$USB_PREPARE_DD" if=/dev/zero of="$disk" bs=1048576 count=1 \
        conv=fsync >/dev/null 2>&1 || {
        usb_prepare_error "Failed to erase $disk."
        return 1
    }
    if [ "$format" = "FAT32" ]; then
        # Partition type 0x0c keeps the drive recognizable as FAT32/LBA to
        # printer firmware, Windows, and other removable-media consumers.
        fdisk_commands='o\nn\np\n1\n\n\nt\nc\nw\n'
    else
        # fdisk's default type 0x83 is the correct Linux EXT partition type.
        fdisk_commands='o\nn\np\n1\n\n\nw\n'
    fi
    echo "// Creating a new partition on $disk..."
    if ! fdisk_output=$(printf '%b' "$fdisk_commands" \
            | $USB_PREPARE_FDISK "$disk" 2>&1); then
        [ -z "$fdisk_output" ] || printf '%s\n' "$fdisk_output"
        usb_prepare_error "Failed to create a partition on $disk."
        return 1
    fi

    if command -v "$USB_STORAGE_UDEVADM" >/dev/null 2>&1; then
        "$USB_STORAGE_UDEVADM" settle --timeout=10 2>/dev/null || true
    fi
    partition=$(usb_storage_partition_path "$disk" 1)
    echo "// Waiting for $partition..."
    if ! usb_prepare_wait_for_partition "$partition"; then
        usb_prepare_error "Partition $partition did not appear after formatting."
        return 1
    fi

    case "$format" in
        FAT32)
            echo "// Creating FAT32 filesystem on $partition..."
            $USB_PREPARE_MKDOSFS -n FORGEX "$partition" || {
                usb_prepare_error "Failed to create FAT32 on $partition."
                return 1
            }
            filesystem="vfat"
            ;;
        EXT)
            echo "// Creating Linux EXT filesystem on $partition..."
            $USB_PREPARE_MKE2FS -F -m 0 -L FORGE_X "$partition" || {
                usb_prepare_error "Failed to create EXT on $partition."
                return 1
            }
            filesystem="ext2"
            ;;
    esac

    echo "// Syncing USB data..."
    sync
    if usb_storage_mount_candidate \
            "$partition" "$filesystem" "forge-x-usb-storage"; then
        echo "// USB preparation complete: $partition is $format and mounted at $USB_STORAGE_MOUNT_POINT."
    else
        echo "// USB preparation complete: $partition is $format; reconnect it before use."
    fi
}


case "$1" in
    prompt)
        usb_prepare_prompt
        ;;
    format)
        [ "$#" -eq 4 ] || {
            usb_prepare_error "Usage: $0 format {EXT|FAT32} {device} {id}"
            exit 1
        }
        if ! usb_storage_operation_acquire; then
            usb_prepare_error "Another USB operation is already running."
            usb_prepare_result_prompt 1 "$2" "$3"
            exit 1
        fi
        trap 'usb_storage_operation_release' EXIT
        if usb_prepare_format "$2" "$3" "$4"; then
            usb_prepare_result_prompt 0 "$2" "$3"
        else
            status=$?
            usb_prepare_result_prompt "$status" "$2" "$3"
            exit "$status"
        fi
        ;;
    *)
        echo "Usage: $0 {prompt|format}"
        exit 1
        ;;
esac
