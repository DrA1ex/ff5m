#!/bin/sh

## Forge-X boot-local mode state
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

BOOT_FAILURE_F=/opt/config/mod/BOOT_FLAG_FAILURE
BOOT_SKIP_F=/opt/config/mod/BOOT_FLAG_SKIP
BOOT_RECOVERY_FAILURE_F=/opt/config/mod/BOOT_FLAG_RECOVERY_FAILURE

INIT_FLAG=/tmp/init_finished_f
SKIP_MOD_F=/tmp/SKIP_MOD
SKIP_MOD_SOFT_F=/tmp/SKIP_MOD_SOFT
SKIP_MOD_HARD_F=/tmp/SKIP_MOD_HARD
BOOT_REASON_F=/tmp/forge_x_boot_reason

forge_x_boot_mode() {
    [ -f "$SKIP_MOD_HARD_F" ] && { echo stock-hard; return; }
    [ -f "$SKIP_MOD_F" ] && { echo stock; return; }
    [ -f "$SKIP_MOD_SOFT_F" ] && { echo stock-soft; return; }
    [ -f "$INIT_FLAG" ] && { echo mod; return; }

    echo incomplete
}

forge_x_publish_stock_mode() {
    case "$1" in
        stock)
            touch "$SKIP_MOD_F" || return 1
            rm -f "$SKIP_MOD_SOFT_F" "$SKIP_MOD_HARD_F" "$INIT_FLAG"
        ;;

        stock-soft)
            touch "$SKIP_MOD_SOFT_F" || return 1
            rm -f "$SKIP_MOD_F" "$SKIP_MOD_HARD_F" "$INIT_FLAG"
        ;;

        stock-hard)
            touch "$SKIP_MOD_HARD_F" || return 1
            rm -f "$SKIP_MOD_F" "$SKIP_MOD_SOFT_F" "$INIT_FLAG"
        ;;

        *) return 1 ;;
    esac
}

forge_x_publish_mod_ready() {
    case "$(forge_x_boot_mode)" in
        incomplete|mod) touch "$INIT_FLAG" ;;
        *) return 1 ;;
    esac
}

forge_x_prepare_normal_boot() {
    rm -f "$BOOT_SKIP_F" "$BOOT_RECOVERY_FAILURE_F" "$BOOT_FAILURE_F" \
        "$SKIP_MOD_F" "$SKIP_MOD_SOFT_F" "$SKIP_MOD_HARD_F" "$INIT_FLAG"
}
