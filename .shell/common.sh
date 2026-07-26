#!/bin/bash

## Mod's common variables and functions
##
## Copyright (C) 2025, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license


MOD=/data/.mod/.forge-x
SCRIPTS=/opt/config/mod/.shell
PY=/opt/config/mod/.py
CMDS=$SCRIPTS/commands
BINS=/opt/config/mod/.bin/exec
MOD_DATA=/opt/config/mod_data

INIT_FLAG="/tmp/init_finished_f"
NOT_FIRST_LAUNCH_F="/tmp/not_first_launch_f"
CUSTOM_BOOT_F="/tmp/custom_boot_f"
WIFI_CONNECTED_F="/tmp/wifi_connected_f"
ETHERNET_CONNECTED_F="/tmp/ethernet_connected_f"
CAMERA_F="/tmp/camera_f"
NET_IP_F="/tmp/net_ip"

BOOT_FAILURE_F="/opt/config/mod/BOOT_FLAG_FAILURE"
BOOT_SKIP_F="/opt/config/mod/BOOT_FLAG_SKIP"

SCREEN_FOLLOW_UP_LOG="/tmp/logged_message_queue"

CFG_SCRIPT="$CMDS/zconf.sh"
VAR_PATH="$MOD_DATA/variables.cfg"

FLASHED_VERSION_F="$MOD"/version.txt
VERSION_F=/opt/config/mod/version.txt

LOAD_IMG_XZ="/opt/config/mod/load.img.xz"
[ -f /opt/config/mod_data/load.img.xz ] && LOAD_IMG_XZ="/opt/config/mod_data/load.img.xz"

SPLASH_IMG_XZ="/opt/config/mod/splash.img.xz"
[ -f /opt/config/mod_data/splash.img.xz ] && SPLASH_IMG_XZ="/opt/config/mod_data/splash.img.xz"

PATH="$BINS:$PATH"

unset LD_PRELOAD
unset LD_LIBRARY_PATH

mount_data_partition() {
    # mount data - this would otherwise be mounted later by Flashforge's firmware
    if ! mount | grep -q /dev/mmcblk0p7; then
        echo "// Mounting /data partition..."
        fsck -y /dev/mmcblk0p7 || true
        mount /dev/mmcblk0p7 /data;
    fi
    
    # local timeout=60
    # while ! mount | grep -q /dev/mmcblk0p7 && [ $timeout -gt 0 ]; do
    #     echo "Waiting /data..."; sleep 1;
    #     timeout=$(( timeout - 1 ))
    # done
    
    if ! mount | grep -q /dev/mmcblk0p7; then
        echo "@@ Mounting /data failed."
        exit 1
    fi
}

init_chroot() {
    mount -t proc /proc "$MOD"/proc
    mount --rbind /sys "$MOD"/sys
    mount --rbind /dev "$MOD"/dev
    mount --bind /run "$MOD"/run
    mount --bind /tmp "$MOD"/tmp
}

dispose_chroot() {
    umount -lf "$MOD"/proc
    umount -lf "$MOD"/sys
    umount -lf "$MOD"/dev
    umount -lf "$MOD"/run
    umount -lf "$MOD"/tmp
}

message() {
    local text="$1"
    local prefix="${2:-"info"}"
    
    echo "RESPOND PREFIX='$prefix' MSG='$text'" > /tmp/printer
}

command() {
    local value="$1"
    
    echo "RESPOND TYPE=command MSG='$value'" > /tmp/printer
}
