#!/bin/bash

## Handling special boot flag
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

source /opt/config/mod/.shell/boot/boot_mode.sh || exit 1
source /opt/config/mod/.shell/common.sh || exit 1
source /opt/config/mod/.shell/boot/usb_storage.sh || exit 1

INSTALL_IMAGE_SCRIPT=/opt/config/mod/.shell/boot/install-image.sh

FLAGS=("SKIP_MOD" "SKIP_MOD_SOFT" "REMOVE_MOD" "REMOVE_MOD_SOFT" "klipper_mod_skip" "klipper_mod_remove")

check_special_boot_flag() {
    local path=$1

    # Check firmware image first
    if "$INSTALL_IMAGE_SCRIPT" test "$path" > /dev/null; then
        echo "FIRMWARE_IMAGE"
        return 0
    fi

    # Check init script
    if [ -f "$path/flashforge_init.sh" ]; then
        echo "FIRMWARE_SCRIPT"
        return 0
    fi

    # Check boot flags (supported FLAG or FLAG.ext)
    for file_name in "${FLAGS[@]}"; do
        if ! compgen -G "$path/$file_name*" > /dev/null; then
            continue
        fi

        for file in "$path/$file_name"*; do
            if [[ "$file" =~ ^$path/$file_name(\.[^/]*)?$ ]]; then
                echo "$file_name"
                return 0
            fi
        done
    done

    return 1
}

search_special_boot_flag_usb() {
    local callback=$1
    local wait_seconds candidates size_kib size_mb partition_path filesystem
    local mount_point found

    echo "Searching for boot flag in USB files..."

    wait_seconds=10
    if ! usb_storage_has_enumerated_disk; then
        echo "No USB storage found."
        return 1
    fi

    if ! usb_storage_wait_for_candidates "$wait_seconds"; then
        echo "USB storage did not become ready within ${wait_seconds}s."
        return 1
    fi

    candidates=$(usb_storage_candidates)
    while read -r size_kib partition_path; do
        [ -n "$partition_path" ] || continue
        filesystem=$(usb_storage_filesystem "$partition_path")
        size_mb=$((size_kib / 1024))
        echo "// USB $partition_path: ${size_mb} MB, ${filesystem:-unknown}"

        if ! usb_storage_supports_mount "$filesystem" \
                && [ -n "$filesystem" ]; then
            echo "Skipping unsupported filesystem $filesystem on $partition_path."
            continue
        fi

        if ! usb_storage_mount_candidate \
                "$partition_path" "$filesystem" "forge-x-boot-flag" ro; then
            continue
        fi

        mount_point="$USB_STORAGE_MOUNT_POINT"
        found=$(check_special_boot_flag "$mount_point")

        if [ -n "$found" ]; then
            echo "// Boot flag: $found"

            [ "$found" = "FIRMWARE_IMAGE" ] || usb_storage_release_mount
            eval "$callback" "$found" "$mount_point"
            return 0
        fi

        usb_storage_release_mount
    done <<< "$candidates"

    return 1
}

search_special_boot_flag_root() {
    local callback=$1

    echo "Searching for boot flag in MMC files..."

    found=$(check_special_boot_flag "/opt/config/mod/")
    if [ -n "$found" ]; then
        echo "// Boot flag: $found"
        eval "$callback" "$found" "/opt/config/mod/"
        return 0
    fi

    return 1
}

search_for_klipper_mod() {
    local callback=$1

    echo "Searching for klipper mod files..."

    if [ -f "/etc/init.d/S00klipper_mod" ]; then
        echo "// Klipper mod found."
        eval "$callback" "KLIPPER_MOD" ""
        return 0
    fi

    return 1
}

handle_special_boot_flag() {
    local name="$1"
    local mount=${2:-}

    case "$name" in
        SKIP_MOD)
            echo "?? Skipping mod load..."
            rm -f /opt/config/mod/SKIP_MOD
            forge_x_publish_stock_mode stock || exit 1

            echo "// Stock firmware will be loaded soon..."

            exit 0
        ;;

        SKIP_MOD_SOFT)
            echo "?? Skipping mod load in soft mode..."
            rm -f /opt/config/mod/SKIP_MOD_SOFT
            forge_x_publish_stock_mode stock-soft || exit 1

            # oh-my-zsh
            if [ -d /root/.oh-my-zsh ]; then
                mount --bind /opt/config/mod/.zsh/.oh-my-zsh /root/.oh-my-zsh
            fi

            echo "// Stock firmware will be loaded soon..."

            exit 0
        ;;

        REMOVE_MOD)
            echo "@@ Removing mod..."

            rm -f /opt/config/mod/REMOVE_MOD
            mount_data_partition

            cp -f /opt/config/mod/.shell/uninstall.sh /tmp/uninstall.sh
            "$SCRIPTS/screen.sh" splash_stop
            /tmp/uninstall.sh

            exit 0
        ;;

        REMOVE_MOD_SOFT)
            echo "@@ Removing mod in soft mode..."

            rm -f /opt/config/mod/REMOVE_MOD_SOFT
            mount_data_partition

            cp -f /opt/config/mod/.shell/uninstall.sh /tmp/uninstall.sh
            "$SCRIPTS/screen.sh" splash_stop
            /tmp/uninstall.sh --soft

            exit 0
        ;;

        klipper_mod_skip)
            echo "!! Klipper mod skipped. Continuing boot..."

            exit 1
        ;;

        FIRMWARE_IMAGE)
            echo "!! Installation image found. Skipping the mod..."
            forge_x_publish_stock_mode stock-hard || exit 1

            echo "// Starting firmware installer..."
            "$INSTALL_IMAGE_SCRIPT" "$mount"

            # The installer owns the boot once an image was accepted. Its
            # errors are terminal screens, not permission to resume boot.
            exit 0
        ;;

        FIRMWARE_SCRIPT)
            echo "!! Installation script found. Skipping the mod..."
            forge_x_publish_stock_mode stock-hard || exit 1

            echo "// Firmware script will be loaded soon..."

            exit 0
        ;;

        KLIPPER_MOD | klipper_mod_remove)
            echo "@@ Skipping mod because of Klipper Mod..."
            forge_x_publish_stock_mode stock-hard || exit 1

            echo "// Klipper mod will be loaded soon..."

            exit 0
        ;;

        *)
            echo "@@ Unknown special boot flag \"$name\""
            exit 1
    esac
}

print_special_boot_flag() {
    local name="$1"

    usb_storage_release_mount
    echo "Flag: $name"
    exit 0
}

search() {
    local callback=$1

    search_special_boot_flag_usb "$callback" \
        || search_special_boot_flag_root "$callback" \
        || search_for_klipper_mod "$callback"

    ret=$?
    echo "// No special boot flag found."

    return $ret
}

init_boot_flag_main() {
    case "$1" in
        test) search "print_special_boot_flag" ;;
        apply) search "handle_special_boot_flag" ;;

        *)
            echo "Usage $0 (test|apply)"
            return 1
        ;;
    esac
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    init_boot_flag_main "$@"
    exit $?
fi
