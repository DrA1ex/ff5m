#!/bin/bash

## Swap initialization script
##
## Copyright (C) 2025, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license


MOD=/data/.mod/.forge-x
CFG_SCRIPT="/opt/config/mod/.shell/commands/zconf.sh"
CFG_PATH="/opt/config/mod_data/variables.cfg"


SWAP_SIZE="${1-"64M"}"

if [ -z "$SWAP_SIZE" ]; then
    echo "Usage: $0 <swap_size>"
    exit 1
fi


is_usb_disk() {
    local device=$1
    local device_name=$(basename "$device")
    
    if readlink -f "/sys/block/$device_name" | grep -q 'usb'; then
        return 0
    else
        return 1
    fi
}

size_convert() {
    size=$1
    case $size in
        *K) bytes=$((${size%K} * 1024)) ;;
        *M) bytes=$((${size%M} * 1024 * 1024)) ;;
        *G) bytes=$((${size%G} * 1024 * 1024 * 1024)) ;;
        *)  bytes=$size ;;  # Assume bytes if no suffix
    esac

    echo "$bytes"
}

make_swap() {
    swap_file=$1
    
    swapoff -a
    
    ret=0
    if [ -f "$swap_file" ]; then
        current_size=$(ls -l "$swap_file" | awk '{print $5}')
        desired_size=$(size_convert "$SWAP_SIZE")

        if [ ! "$current_size" -eq "$desired_size" ]; then
            echo "Recreating existing swap file..."
            rm -f "$swap_file"
            fallocate -l "$SWAP_SIZE" "$swap_file"
            ret=$?
        fi
    else
        echo "Generating swap file..."
        fallocate -l "$SWAP_SIZE" "$swap_file"
        ret=$?
    fi

    if [ $ret -ne 0 ]; then
        echo "Unable to create swap file"
        return 1
    fi

    chmod 600 "$swap_file"           \
        && mkswap "$swap_file"       \
        && swapon "$swap_file"       \
    
    return $?
}

activate_usb_swap() {
    echo "// Creating SWAP on USB..."
    
    for device in /dev/sd*; do
        # Skip if it's a partition (e.g., /dev/sda1)
        if [[ $device =~ [0-9]$ ]]; then
            continue
        fi
        
        # Check if the device is a USB disk
        if is_usb_disk "$device"; then
            echo "Found USB disk: $device"
            
            partitions=$(fdisk -l "$device" | awk '/^ *[0-9]+/ {print $1 " " $4}' | sort -k2,2nr)
            if [ -z "$partitions" ]; then
                echo "No partitions found on $device. Please create a partition on the USB disk."
                continue
            fi
            
            echo "Disk has partitions: $(echo "$partitions" | wc -l)"
            
            while read -r partition size; do
                partition_path="${device}${partition}"
                mount_point=$(mount | grep "$partition_path" | awk '{print $3}')
                
                if [ -z "$mount_point" ]; then
                    echo "Partition $partition not mounted. Mounting..."
                    mount_point=$(mktemp -d)
                    
                    if ! mount -t vfat -o codepage=437,iocharset=utf8 "$partition_path" "$mount_point"; then
                        echo "Failed to mount $device; partition $partition"
                        rmdir "$mount_point"
                    fi
                fi
                
                echo "USB disk mounted at $mount_point; Size: $size"

                disk_size=$(size_convert "$size")
                desired_size=$(size_convert "$SWAP_SIZE")

                if [ "$disk_size" -lt "$desired_size" ]; then
                    echo "Partition not big enough!"
                    continue
                fi
                
                make_swap "$mount_point/swap"
                
                if [ $? -eq 0 ]; then
                    echo "// Swap file created and activated on $device"
                    return 0
                else
                    echo "@@ Failed to enable swap file on $device"
                fi
            done <<< "$partitions"
        else
            echo "$device is not a USB disk."
        fi
        
        return 1
    done
}

activate_mmc_swap() {
    echo "// Creating SWAP on eMMC..."
    
    make_swap "$MOD/root/swap"
    if [ $? -eq 0 ]; then
        echo "// Swap file created and activated eMMC"
    else
        echo "@@ Failed to enable swap file on eMMC"
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
    swapoff /dev/zram0 2>/dev/null
    echo 1 > /sys/block/zram0/reset 2>/dev/null
    echo "$ALGO" > /sys/block/zram0/comp_algorithm 2>/dev/null || ALGO="(default)"
    echo "$ZSIZE" > /sys/block/zram0/disksize
    mkswap /dev/zram0 >/dev/null 2>&1

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
            && mkswap "$MOD/root/swap" >/dev/null 2>&1
    fi
    swapon "$MOD/root/swap" 2>/dev/null   # default (low) priority = overflow

    echo "// zram swap active (algo=$ALGO, size=$ZSIZE); eMMC = overflow"
    return 0
}

cleanup_mounts() {
    mount | grep "/dev/sd" | awk '{print $1 " " $3}' | while read -r partition mount; do
        if ! ls "$partition" > /dev/null 2>&1; then
            echo "Unmounting dead mounting point: $mount"
            umount -l "$mount"
            
            if [[ $mount == /tmp/* ]]; then
                rmdir "$mount"
            fi
        fi
    done
}

swap=$($CFG_SCRIPT  $CFG_PATH --get "use_swap" "MMC")
echo "SWAP: \"$swap\""

case "$swap" in
    OFF)
        echo "Swap disabled."
        
        swapoff -a
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
        cleanup_mounts
        if ! activate_usb_swap; then
            echo "Failed to activate USB swap. Activating MMC swap instead."
            activate_mmc_swap
        fi
    ;;
    *)
        echo "Unsupported swap configuration: $swap"
        exit 1
    ;;
esac
