#!/bin/bash
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
## This file may be distributed under the terms of the GNU GPLv3 license.

KLIPPER_OVERLAY_SRC=${KLIPPER_OVERLAY_SRC:-/opt/config/mod/.py/klipper}
KLIPPER_OVERLAY_TARGET=${KLIPPER_OVERLAY_TARGET:-/opt/klipper/klippy}
KLIPPER_USER_DIR=${KLIPPER_USER_DIR:-/opt/config/mod_data/plugins}
KLIPPER_USER_CONFIG=${KLIPPER_USER_CONFIG:-/opt/config/mod_data/plugins.cfg}
KLIPPER_OVERLAY_BIN=${KLIPPER_OVERLAY_BIN:-/opt/config/mod/.bin/exec/overlay}

# Keep callers' existing shell entry points; all filesystem policy lives in C++.
klipper_overlay_call() {
    KLIPPER_OVERLAY_SRC="$KLIPPER_OVERLAY_SRC" \
    KLIPPER_OVERLAY_TARGET="$KLIPPER_OVERLAY_TARGET" \
    KLIPPER_USER_DIR="$KLIPPER_USER_DIR" \
    KLIPPER_USER_CONFIG="$KLIPPER_USER_CONFIG" \
    VAR_PATH="${VAR_PATH:-}" "$KLIPPER_OVERLAY_BIN" "$@"
}

apply_klipper_patches() { klipper_overlay_call install "$@"; }
klipper_overlay_clear_user_cache() { klipper_overlay_call clear-cache "$1"; }
klipper_overlay_publish_config() { klipper_overlay_call publish-config "$1"; }
klipper_overlay_clean_links() {
    local action=clean
    [ "${3:-apply}" != remove ] || action=uninstall
    KLIPPER_OVERLAY_SRC="$1" KLIPPER_OVERLAY_TARGET="$2" klipper_overlay_call "$action"
}
