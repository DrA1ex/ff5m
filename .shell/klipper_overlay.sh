#!/bin/bash

## Klipper plugin and patch overlay management
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

klipper_overlay_ignored() {
    local rel_path="$1"

    case "/$rel_path/" in
        */__pycache__/*) return 0 ;;
    esac

    case "$rel_path" in
        .*|*/.*|*.pyc) return 0 ;;
    esac

    return 1
}

klipper_overlay_restore_or_remove() {
    local target="$1"
    local backup

    for backup in \
        "$target.bak" \
        "$target.backup" \
        "$target.old" \
        "$target.orig"; do
        if [ -f "$backup" ] && [ ! -L "$backup" ]; then
            echo "// Restore klipper file backup: $backup"
            rm -f "$target" || return 1
            mv -f "$backup" "$target" || return 1
            return 0
        fi
    done

    echo "// Remove stale klipper symlink: $target"
    rm -f "$target"
}

klipper_overlay_plugin_link_is_current() {
    local link="$1"
    local source="$2"
    local src_dir="$3"
    local target_dir="$4"
    local rel_file expected

    case "$source" in
        "$src_dir/plugins/"*) ;;
        *) return 1 ;;
    esac

    rel_file=${source#"$src_dir/plugins/"}
    expected="$target_dir/extras/$rel_file"

    [ "$link" = "$expected" ] || return 1
    [ -f "$source" ] || return 1
    klipper_overlay_ignored "$rel_file" && return 1

    return 0
}

klipper_overlay_patch_link_is_current() {
    local link="$1"
    local source="$2"
    local src_dir="$3"
    local target_dir="$4"
    local rel_file expected

    case "$source" in
        "$src_dir/patches/"*) ;;
        *) return 1 ;;
    esac

    rel_file=${source#"$src_dir/patches/"}
    expected="$target_dir/$rel_file"

    [ "$link" = "$expected" ] || return 1
    [ -f "$source" ] || return 1
    case "$rel_file" in
        *.py) ;;
        *) return 1 ;;
    esac
    klipper_overlay_ignored "$rel_file" && return 1

    return 0
}

klipper_overlay_clean_links() {
    local src_dir="$1"
    local target_dir="$2"
    local link source

    while IFS= read -r link; do
        source=$(readlink "$link") || return 1

        if [ ! -e "$link" ]; then
            klipper_overlay_restore_or_remove "$link" || return 1
            continue
        fi

        case "$source" in
            "$src_dir/plugins/"*)
                klipper_overlay_plugin_link_is_current \
                    "$link" "$source" "$src_dir" "$target_dir" \
                    || klipper_overlay_restore_or_remove "$link" \
                    || return 1
            ;;
            "$src_dir/patches/"*)
                klipper_overlay_patch_link_is_current \
                    "$link" "$source" "$src_dir" "$target_dir" \
                    || klipper_overlay_restore_or_remove "$link" \
                    || return 1
            ;;
        esac
    done < <(find "$target_dir" -type l)
}

klipper_overlay_link_plugins() {
    local src_dir="$1"
    local target_dir="$2"
    local file rel_file target parent current

    while IFS= read -r file; do
        rel_file=${file#"$src_dir/plugins/"}
        klipper_overlay_ignored "$rel_file" && continue

        target="$target_dir/extras/$rel_file"
        if [ -L "$target" ]; then
            current=$(readlink "$target") || return 1
            [ "$current" = "$file" ] && continue
        fi

        if [ -e "$target" ] || [ -L "$target" ]; then
            echo "@@ Refusing to overwrite klipper plugin target: $target"
            return 1
        fi

        parent=${target%/*}
        mkdir -p "$parent" || return 1

        echo "// Link klipper plugin file: $file"
        ln -s "$file" "$target" || return 1
    done < <(find "$src_dir/plugins" -type f)
}

klipper_overlay_link_patches() {
    local src_dir="$1"
    local target_dir="$2"
    local file rel_file target parent current

    while IFS= read -r file; do
        rel_file=${file#"$src_dir/patches/"}
        klipper_overlay_ignored "$rel_file" && continue

        case "$rel_file" in
            *.py) ;;
            *) continue ;;
        esac

        target="$target_dir/$rel_file"
        if [ -L "$target" ]; then
            current=$(readlink "$target") || return 1
            [ "$current" = "$file" ] && continue

            echo "@@ Refusing to overwrite unmanaged klipper symlink: $target"
            return 1
        fi

        parent=${target%/*}
        mkdir -p "$parent" || return 1

        if [ -e "$target" ]; then
            if [ ! -e "$target.bak" ] && [ ! -L "$target.bak" ]; then
                echo "// Create klipper file backup: $target"
                mv "$target" "$target.bak" || return 1
            else
                echo "// Remove overwritten klipper file: $target"
                rm -f "$target" || return 1
            fi
        elif [ ! -e "$target.bak" ] && [ ! -L "$target.bak" ]; then
            echo "@@ Missing klipper patch target and backup: $target"
            return 1
        fi

        echo "// Link patched klipper file: $file"
        ln -s "$file" "$target" || return 1
    done < <(find "$src_dir/patches" -type f)
}

apply_klipper_patches() {
    local src_dir="${KLIPPER_SRC_DIR:-/opt/config/mod/.py/klipper}"
    local target_dir="${KLIPPER_TARGET_DIR:-/opt/klipper/klippy}"
    local tune_cmd="${KLIPPER_TUNE_CMD:-$CMDS/ztune_klipper.sh}"

    klipper_overlay_clean_links "$src_dir" "$target_dir" || return 1

    sync
    echo "Linking extensions..."
    klipper_overlay_link_plugins "$src_dir" "$target_dir" || return 1

    sync
    echo "Apply patches..."
    klipper_overlay_link_patches "$src_dir" "$target_dir" || return 1

    sync
    echo "Apply fixes..."
    "$tune_cmd" apply || return 1

    sync
}
