#!/bin/bash

## Mount USB print storage for the Feather file browser.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

USB_BROWSER_SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$USB_BROWSER_SCRIPT_DIR/../boot/usb_storage.sh"
USB_BROWSER_PROC_SWAPS="${USB_BROWSER_PROC_SWAPS:-/proc/swaps}"
USB_BROWSER_WAIT_SECONDS="${USB_BROWSER_WAIT_SECONDS:-2}"
USB_BROWSER_DATA_ROOT="${USB_BROWSER_DATA_ROOT:-/data}"
USB_BROWSER_CHROOT_ROOT="${USB_BROWSER_CHROOT_ROOT-/data/.mod/.forge-x}"


usb_browser_error() {
    echo "ERROR $*"
}


usb_browser_mounted_source() {
    awk -v target="$1" '$2 == target { print $1; exit }' \
        "$USB_STORAGE_PROC_MOUNTS"
}


usb_browser_is_candidate() {
    local requested="$1"
    usb_storage_candidates \
        | awk -v requested="$requested" '$2 == requested { found=1 } END { exit !found }'
}


usb_browser_mirror_target() {
    local relative
    [ -n "$USB_BROWSER_CHROOT_ROOT" ] || return 1
    case "$1" in
        "$USB_BROWSER_DATA_ROOT/"*) ;;
        *) return 1 ;;
    esac
    relative=${1#"$USB_BROWSER_DATA_ROOT/"}
    echo "$USB_BROWSER_CHROOT_ROOT/data/$relative"
}


usb_browser_detach_mirror() {
    local target="$1"
    local mirror
    mirror=$(usb_browser_mirror_target "$target") || return 0
    if [ -n "$(usb_browser_mounted_source "$mirror")" ]; then
        umount "$mirror" 2>/dev/null || umount -l "$mirror" 2>/dev/null || {
            usb_browser_error "Unable to unmount $mirror."
            return 1
        }
    fi
    rmdir "$mirror" 2>/dev/null || true
}


usb_browser_attach_mirror() {
    local target="$1"
    local mirror mirror_source primary_source
    mirror=$(usb_browser_mirror_target "$target") || return 0
    if [ ! -d "$USB_BROWSER_CHROOT_ROOT/data" ]; then
        usb_browser_error "Forge-X data mount is unavailable."
        return 1
    fi
    primary_source=$(usb_browser_mounted_source "$target")
    mirror_source=$(usb_browser_mounted_source "$mirror")
    if [ -n "$mirror_source" ]; then
        if [ "$mirror_source" = "$primary_source" ]; then
            return 0
        fi
        usb_browser_detach_mirror "$target" || return 1
    fi
    if [ -d "$mirror" ] && [ -n "$(ls -A "$mirror" 2>/dev/null)" ]; then
        usb_browser_error "Refusing to cover non-empty directory $mirror."
        return 1
    fi
    if [ -e "$mirror" ] && [ ! -d "$mirror" ]; then
        usb_browser_error "Refusing to replace existing $mirror."
        return 1
    fi
    mkdir -p "$mirror" || return 1
    mount -o bind "$target" "$mirror" || {
        usb_browser_error "Unable to expose USB storage inside Forge-X."
        rmdir "$mirror" 2>/dev/null || true
        return 1
    }
}


usb_browser_detach() {
    local target="$1"
    if awk -v prefix="$target/" \
            'NR > 1 && index($1, prefix) == 1 { found=1 } END { exit !found }' \
            "$USB_BROWSER_PROC_SWAPS"; then
        echo "RETAINED active-swap"
        return 0
    fi
    usb_browser_detach_mirror "$target" || return 1
    if [ -n "$(usb_browser_mounted_source "$target")" ]; then
        umount "$target" 2>/dev/null || umount -l "$target" 2>/dev/null || {
            usb_browser_error "Unable to unmount $target."
            return 1
        }
    fi
    rmdir "$target" 2>/dev/null || true
    echo "DETACHED"
}


usb_browser_attach() {
    local target="$1"
    local source size_kib partition filesystem existing_mount

    if ! usb_storage_operation_acquire; then
        echo "BUSY"
        return 3
    fi
    trap 'usb_storage_operation_release' EXIT

    source=$(usb_browser_mounted_source "$target")
    if [ -n "$source" ]; then
        if usb_storage_device_ready "$source" \
                && usb_browser_is_candidate "$source"; then
            filesystem=$(usb_storage_mounted_filesystem "$source")
            usb_browser_attach_mirror "$target" || return 1
            echo "ATTACHED $source ${filesystem:-unknown}"
            return 0
        fi
        usb_browser_detach_mirror "$target" || return 1
        umount -l "$target" 2>/dev/null || {
            usb_browser_error "Unable to release stale mount $target."
            return 1
        }
    fi

    if [ -L "$target" ]; then
        usb_browser_error "Refusing to replace existing $target."
        return 1
    fi
    if [ -d "$target" ] && [ -n "$(ls -A "$target" 2>/dev/null)" ]; then
        usb_browser_error "Refusing to cover non-empty directory $target."
        return 1
    fi
    if [ -e "$target" ] && [ ! -d "$target" ]; then
        usb_browser_error "Refusing to replace existing $target."
        return 1
    fi
    mkdir -p "$target" || return 1

    if ! usb_storage_wait_for_candidates "$USB_BROWSER_WAIT_SECONDS"; then
        rmdir "$target" 2>/dev/null || true
        echo "NONE"
        return 2
    fi

    while read -r size_kib partition; do
        [ -n "$partition" ] || continue
        filesystem=$(usb_storage_filesystem "$partition")
        usb_storage_supports_mount "$filesystem" || continue

        existing_mount=$(usb_storage_mounted_path "$partition")
        if [ -n "$existing_mount" ]; then
            if mount -o bind "$existing_mount" "$target"; then
                if ! usb_browser_attach_mirror "$target"; then
                    usb_browser_detach "$target" >/dev/null
                    return 1
                fi
                echo "ATTACHED $partition $filesystem"
                return 0
            fi
            continue
        fi

        if usb_storage_try_mount "$partition" "$target" "$filesystem" rw; then
            if ! usb_browser_attach_mirror "$target"; then
                usb_browser_detach "$target" >/dev/null
                return 1
            fi
            echo "ATTACHED $partition $filesystem"
            return 0
        fi
    done < <(usb_storage_candidates)

    rmdir "$target" 2>/dev/null || true
    echo "NONE"
    return 2
}


[ "$#" -eq 2 ] || {
    usb_browser_error "Usage: $0 {attach|detach} mount-point"
    exit 1
}

[ "$2" = "$USB_BROWSER_DATA_ROOT/USB" ] || {
    usb_browser_error "Invalid Feather USB mount point."
    exit 1
}

case "$1" in
    attach) usb_browser_attach "$2" ;;
    detach) usb_browser_detach "$2" ;;
    *) usb_browser_error "Unknown operation $1."; exit 1 ;;
esac
