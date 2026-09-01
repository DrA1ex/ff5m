#!/bin/bash

## Restore Forge-X and stock printer configuration defaults.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

set -u

MACHINE=${1:-}
RESET_ROOT=${FORGE_X_RESET_ROOT:-}
CONFIG_ROOT="$RESET_ROOT/opt/config"
DEFAULT_ROOT="$CONFIG_ROOT/mod/.cfg/default"

case "$MACHINE" in
    Adventurer5M|Adventurer5MPro) ;;
    *)
        echo "Unsupported printer model: $MACHINE" >&2
        exit 2
    ;;
esac

DEFAULTS=(
    "printer/$MACHINE/printer.cfg:printer.cfg"
    "printer/$MACHINE/printer.base.cfg:printer.base.cfg"
    "backup.params.cfg:mod_data/backup.params.cfg"
    "camera.conf:mod_data/camera.conf"
    "ssh.conf:mod_data/ssh.conf"
    "web.conf:mod_data/web.conf"
    "user.cfg:mod_data/user.cfg"
    "user.moonraker.conf:mod_data/user.moonraker.conf"
    "variables.cfg:mod_data/variables.cfg"
)
REMOVALS=(printer.base.cfg.bak)

for mapping in "${DEFAULTS[@]}"; do
    source_rel=${mapping%%:*}
    source_path="$DEFAULT_ROOT/$source_rel"
    if [ ! -f "$source_path" ] || [ -L "$source_path" ]; then
        echo "Invalid default file: $source_path" >&2
        exit 1
    fi
done

[ -d "$CONFIG_ROOT/mod_data" ] && [ ! -L "$CONFIG_ROOT/mod_data" ] || {
    echo "Forge-X configuration directory is unavailable." >&2
    exit 1
}
transaction=$(mktemp -d "$CONFIG_ROOT/.forge-x-reset.XXXXXX") || exit 1
staged="$transaction/staged"
previous="$transaction/previous"
applied=()
committed=0

rollback() {
    local index target_rel target_path previous_path marker

    [ "$committed" -eq 0 ] || return 0
    for ((index=${#applied[@]} - 1; index >= 0; index--)); do
        target_rel=${applied[index]}
        target_path="$CONFIG_ROOT/$target_rel"
        previous_path="$previous/$target_rel"
        marker="$previous_path.present"
        if [ -f "$marker" ]; then
            mkdir -p "${target_path%/*}" || true
            mv -f "$previous_path" "$target_path" || true
        else
            rm -f "$target_path" || true
        fi
    done
}

cleanup() {
    status=$?
    if [ "$status" -ne 0 ]; then
        rollback
    fi
    rm -rf "$transaction"
    exit "$status"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

mkdir -p "$staged" "$previous" || exit 1

for mapping in "${DEFAULTS[@]}"; do
    source_rel=${mapping%%:*}
    target_rel=${mapping#*:}
    staged_path="$staged/$target_rel"
    mkdir -p "${staged_path%/*}" || exit 1
    cp -p "$DEFAULT_ROOT/$source_rel" "$staged_path" || exit 1
done

for mapping in "${DEFAULTS[@]}"; do
    target_rel=${mapping#*:}
    target_path="$CONFIG_ROOT/$target_rel"
    previous_path="$previous/$target_rel"

    if [ -e "$target_path" ] || [ -L "$target_path" ]; then
        if [ ! -f "$target_path" ] || [ -L "$target_path" ]; then
            echo "Refusing to replace non-regular config: $target_path" >&2
            exit 1
        fi
        mkdir -p "${previous_path%/*}" || exit 1
        cp -p "$target_path" "$previous_path" || exit 1
        : > "$previous_path.present" || exit 1
    fi

    applied+=("$target_rel")
    mv -f "$staged/$target_rel" "$target_path" || exit 1
done

for target_rel in "${REMOVALS[@]}"; do
    target_path="$CONFIG_ROOT/$target_rel"
    previous_path="$previous/$target_rel"
    if [ -e "$target_path" ] || [ -L "$target_path" ]; then
        if [ ! -f "$target_path" ] || [ -L "$target_path" ]; then
            echo "Refusing to remove non-regular config: $target_path" >&2
            exit 1
        fi
        mkdir -p "${previous_path%/*}" || exit 1
        cp -p "$target_path" "$previous_path" || exit 1
        : > "$previous_path.present" || exit 1
        applied+=("$target_rel")
        rm -f "$target_path" || exit 1
    fi
done

sync
committed=1
echo "Forge-X configuration reset for $MACHINE."
