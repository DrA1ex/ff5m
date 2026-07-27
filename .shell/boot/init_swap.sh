#!/bin/bash

## Swap initialization script
##
## Copyright (C) 2025, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license


MOD="${MOD:-/data/.mod/.forge-x}"
CFG_SCRIPT="${CFG_SCRIPT:-/opt/config/mod/.shell/commands/zconf.sh}"
CFG_PATH="${CFG_PATH:-/opt/config/mod_data/variables.cfg}"
SWAP_SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

source "$SWAP_SCRIPT_DIR/usb_storage.sh"


SWAP_SIZE="${SWAP_SIZE-${1-64M}}"

if [ -z "$SWAP_SIZE" ]; then
    echo "Usage: $0 <swap_size>"
    exit 1
fi


size_convert() {
    local size=$1
    local bytes
    case "$size" in
        *K) bytes=$((${size%K} * 1024)) ;;
        *M) bytes=$((${size%M} * 1024 * 1024)) ;;
        *G) bytes=$((${size%G} * 1024 * 1024 * 1024)) ;;
        *)  bytes=$size ;;  # Assume bytes if no suffix
    esac

    echo "$bytes"
}

swap_mkswap() {
    if command -v mkswap >/dev/null 2>&1; then
        command mkswap "$@"
    else
        busybox mkswap "$@"
    fi
}

swap_swapon() {
    if command -v swapon >/dev/null 2>&1; then
        command swapon "$@"
    else
        busybox swapon "$@"
    fi
}

swap_swapoff() {
    if command -v swapoff >/dev/null 2>&1; then
        command swapoff "$@"
    else
        busybox swapoff "$@"
    fi
}

allocate_swap_file() {
    local swap_file="$1"
    local filesystem="$2"
    local desired_size full_mib remainder

    case "$filesystem" in
        vfat|msdos|fat|fat12|fat16|fat32)
            # The 5.4 FAT driver provides bmap(), so populated FAT files can
            # be swap files. Do not use truncate/fallocate here: swapon must
            # see a physical block behind every page.
            desired_size=$(size_convert "$SWAP_SIZE")
            full_mib=$((desired_size / 1048576))
            remainder=$((desired_size % 1048576))
            dd if=/dev/zero of="$swap_file" bs=1048576 \
                count="$full_mib" conv=fsync || return 1
            if [ "$remainder" -gt 0 ]; then
                dd if=/dev/zero of="$swap_file" bs=1 \
                    count="$remainder" seek=$((full_mib * 1048576)) \
                    conv=notrunc,fsync || return 1
            fi
            ;;
        *)
            fallocate -l "$SWAP_SIZE" "$swap_file"
            ;;
    esac
}

make_swap() {
    local swap_file=$1
    local filesystem="${2:-ext4}"
    local ret=0 current_size desired_size
    
    swap_swapoff -a

    if [ -f "$swap_file" ]; then
        current_size=$(ls -l "$swap_file" | awk '{print $5}')
        desired_size=$(size_convert "$SWAP_SIZE")

        if [ ! "$current_size" -eq "$desired_size" ]; then
            echo "Recreating existing swap file..."
            rm -f "$swap_file"
            allocate_swap_file "$swap_file" "$filesystem"
            ret=$?
        fi
    else
        echo "Generating swap file..."
        allocate_swap_file "$swap_file" "$filesystem"
        ret=$?
    fi

    if [ $ret -ne 0 ]; then
        echo "Unable to create swap file"
        return 1
    fi

    chmod 600 "$swap_file"           \
        && swap_mkswap "$swap_file"  \
        && swap_swapon "$swap_file"  \
    
    return $?
}

activate_usb_swap() {
    local wait_seconds candidates
    local size_kib partition filesystem desired_size desired_kib
    local swap_file current_size available_kib

    echo "// Creating SWAP on USB..."

    wait_seconds="${USB_SWAP_WAIT_SECONDS:-10}"
    echo "Waiting up to ${wait_seconds}s for USB storage..."

    if ! usb_storage_wait_for_candidates "$wait_seconds"; then
        echo "@@ USB swap: no storage appeared within ${wait_seconds}s."
        return 1
    fi

    candidates=$(usb_storage_candidates)
    desired_size=$(size_convert "$SWAP_SIZE")
    desired_kib=$(((desired_size + 1023) / 1024))

    while read -r size_kib partition; do
        [ -n "$partition" ] || continue
        filesystem=$(usb_storage_filesystem "$partition")
        echo "Found USB candidate: $partition (${size_kib} KiB, ${filesystem:-unknown})"

        if [ "$size_kib" -lt "$desired_kib" ]; then
            echo "USB candidate $partition is too small."
            continue
        fi

        if [ "$filesystem" = "swap" ]; then
            swap_swapoff -a
            if swap_swapon "$partition"; then
                echo "// USB swap partition activated on $partition"
                return 0
            fi
            echo "@@ Failed to activate USB swap partition $partition"
            continue
        fi

        if ! usb_storage_supports_mount "$filesystem" \
                && [ -n "$filesystem" ]; then
            echo "@@ USB swap: unsupported filesystem $filesystem on $partition."
            continue
        fi
        if ! usb_storage_mount_candidate \
                "$partition" "$filesystem" "forge-x-usb-swap"; then
            echo "@@ USB swap: could not mount $partition."
            continue
        fi

        filesystem="$USB_STORAGE_MOUNT_FILESYSTEM"

        swap_file="$USB_STORAGE_MOUNT_POINT/swap"
        current_size=0
        [ -f "$swap_file" ] \
            && current_size=$(ls -l "$swap_file" | awk '{print $5}')
        if [ "$current_size" -ne "$desired_size" ]; then
            available_kib=$(df -Pk "$USB_STORAGE_MOUNT_POINT" \
                | awk 'NR == 2 { print $4 }')
            if [ -z "$available_kib" ] \
                    || [ "$available_kib" -lt "$desired_kib" ]; then
                echo "@@ USB swap: $partition has insufficient free space."
                continue
            fi
        fi

        if make_swap "$swap_file" "$filesystem"; then
            echo "// Swap file created and activated on $partition"
            return 0
        fi

        echo "@@ USB swap: $filesystem swap file activation failed on $partition."
    done <<< "$candidates"

    return 1
}

activate_mmc_swap() {
    echo "// Creating SWAP on eMMC..."
    
    make_swap "$MOD/root/swap" ext4
    if [ $? -eq 0 ]; then
        echo "// Swap file created and activated eMMC"
        return 0
    else
        echo "@@ Failed to enable swap file on eMMC"
        return 1
    fi
}

activate_zram_swap() {
    echo "// Creating compressed SWAP on zram..."

    local ZDIR="$(dirname "$0")/zram"
    local ALGO=$($CFG_SCRIPT $CFG_PATH --get "zram_algo" "zstd")
    local ZSIZE="${ZRAM_DISKSIZE:-256M}"

    # Loadable zram+zsmalloc modules built for the stock 5.4.61 kernel
    # (vermagic: "5.4.61 SMP preempt mod_unload ARMv7 p2v8"). The AD5M kernel is
    # byte-identical across stock 2.6.5-5.1.x, so these load on every supported FW.
    insmod "$ZDIR/zsmalloc.ko" 2>/dev/null
    insmod "$ZDIR/zram.ko" 2>/dev/null

    if [ ! -e /dev/zram0 ]; then
        echo "@@ zram module did not load (kernel mismatch?)"
        return 1
    fi

    # (Re)create zram0 as a compressed swap. Touch ONLY zram0 here -- do NOT
    # `swapoff -a` (that forces tens of MB back into RAM and can fail under
    # memory pressure on the 128MB board).
    swap_swapoff /dev/zram0 2>/dev/null
    echo 1 > /sys/block/zram0/reset 2>/dev/null
    echo "$ALGO" > /sys/block/zram0/comp_algorithm 2>/dev/null || ALGO="(default)"
    echo "$ZSIZE" > /sys/block/zram0/disksize
    swap_mkswap /dev/zram0 >/dev/null 2>&1

    # zram = PRIMARY swap (priority 100). busybox `swapon` has no -p, so use the
    # static helper (swapon(2) with SWAP_FLAG_PREFER|prio).
    if ! "$ZDIR/swapon_prio" /dev/zram0 100 >/dev/null 2>&1; then
        echo "@@ Failed to swapon zram"
        return 1
    fi

    # zram is fast RAM-backed swap, so tune the VM to actually use it. None of
    # these reserve any fixed RAM -- they only change reclaim/writeback behaviour:
    #   swappiness=100            - push cold anon pages to (compressed) zram
    #                               readily, freeing RAM. 100 is the max on 5.4.
    #   page-cluster=0            - disable swap read-ahead; zram is fast
    #                               random-access, so reading one page at a time
    #                               avoids decompressing extras.
    #   vfs_cache_pressure=50     - keep cheap in-RAM dentry/inode cache around
    #                               longer, fewer eMMC metadata re-reads.
    #   watermark_scale_factor=40 - wake kswapd earlier and let it reclaim
    #                               longer, so allocating tasks hit far fewer
    #                               synchronous direct-reclaim stalls.
    #   dirty_ratio=10 / dirty_background_ratio=5 - cap dirty pages low so
    #                               writeback to the slow eMMC starts early and
    #                               in small chunks instead of one big stall.
    echo 100 > /proc/sys/vm/swappiness             2>/dev/null
    echo 0   > /proc/sys/vm/page-cluster           2>/dev/null
    echo 50  > /proc/sys/vm/vfs_cache_pressure     2>/dev/null
    echo 40  > /proc/sys/vm/watermark_scale_factor 2>/dev/null
    echo 10  > /proc/sys/vm/dirty_ratio            2>/dev/null
    echo 5   > /proc/sys/vm/dirty_background_ratio 2>/dev/null

    # Keep a small eMMC swapfile as a LOW-priority overflow safety net. Create it
    # once if missing; add it without disturbing existing swaps (no swapoff -a).
    if [ ! -f "$MOD/root/swap" ]; then
        fallocate -l "$SWAP_SIZE" "$MOD/root/swap" 2>/dev/null \
            && chmod 600 "$MOD/root/swap" \
            && swap_mkswap "$MOD/root/swap" >/dev/null 2>&1
    fi
    swap_swapon "$MOD/root/swap" 2>/dev/null   # default priority = overflow

    echo "// zram swap active (algo=$ALGO, size=$ZSIZE); eMMC = overflow"
    return 0
}

cleanup_mounts() {
    local partition mount_point

    mount | grep "/dev/sd" | awk '{print $1 " " $3}' \
        | while read -r partition mount_point; do
        if [ ! -e "$partition" ]; then
            echo "Unmounting dead mounting point: $mount_point"
            umount -l "$mount_point"
            
            case "$mount_point" in
                /tmp/*) rmdir "$mount_point" ;;
            esac
        fi
    done
}

if [ "${INIT_SWAP_LIBRARY_ONLY:-0}" -eq 1 ]; then
    return 0 2>/dev/null || exit 0
fi

swap=$($CFG_SCRIPT  $CFG_PATH --get "use_swap" "MMC")
echo "SWAP: \"$swap\""

case "$swap" in
    OFF)
        echo "Swap disabled."
        
        swap_swapoff -a
        rm -f "$MOD"/root/swap
        
        exit 0
    ;;
    MMC)
        activate_mmc_swap
    ;;
    ZRAM)
        if ! activate_zram_swap; then
            echo "Falling back to eMMC swap."
            activate_mmc_swap
        fi
    ;;
    USB)
        if ! usb_storage_operation_acquire; then
            echo "@@ Another USB operation is running; falling back to eMMC swap."
            if ! activate_mmc_swap; then
                echo "@@ FATAL: eMMC swap initialization failed."
                exit 1
            fi
        else
            trap 'usb_storage_operation_release' EXIT
            cleanup_mounts
            if activate_usb_swap; then
                usb_storage_operation_release
                trap - EXIT
            else
                usb_storage_operation_release
                trap - EXIT
                echo "@@ USB swap initialization failed; falling back to eMMC swap."
                if ! activate_mmc_swap; then
                    echo "@@ FATAL: both USB and eMMC swap initialization failed."
                    exit 1
                fi
            fi
        fi
    ;;
    *)
        echo "Unsupported swap configuration: $swap"
        exit 1
    ;;
esac
