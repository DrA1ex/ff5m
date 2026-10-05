#!/bin/bash

## Klipper plugin and patch overlay management
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

KLIPPER_OVERLAY_SRC=${KLIPPER_OVERLAY_SRC:-/opt/config/mod/.py/klipper}
KLIPPER_OVERLAY_TARGET=${KLIPPER_OVERLAY_TARGET:-/opt/klipper/klippy}
KLIPPER_USER_DIR=${KLIPPER_USER_DIR:-/opt/config/mod_data/plugins}
KLIPPER_USER_CONFIG=${KLIPPER_USER_CONFIG:-/opt/config/mod_data/plugins.cfg}

# User overlay plan for the current apply_klipper_patches call: target -> source,
# targets in package order, and the aggregate config, plus the planned user links
# found by cleanup (target -> current source). apply_klipper_patches shadows these
# with locals; the empty globals keep other callers plan-free.
declare -gA KLIPPER_USER_WANT=()
declare -ga KLIPPER_USER_ORDER=()
declare -g KLIPPER_USER_CFG=""
declare -gA KLIPPER_USER_CURRENT=()

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

klipper_overlay_patch_supported() {
    case "$1" in
        *.py|*.so) return 0 ;;
    esac

    return 1
}

klipper_overlay_restore_or_remove() {
    local target="$1"
    local backup

    klipper_overlay_clear_user_cache "$target" || return 1
    for backup in \
        "$target.bak" \
        "$target.backup" \
        "$target.old" \
        "$target.orig"; do
        if [ -f "$backup" ] && [ ! -L "$backup" ]; then
            echo "// Restore klipper file backup: $backup"
            # rename() replaces the link atomically, so the module never goes
            # missing. Only a directory entry must go first: mv would move into it.
            if [ -d "$target" ]; then rm -f "$target" || return 1; fi
            mv -f "$backup" "$target" || return 1
            return 0
        fi
    done

    echo "// Remove stale klipper symlink: $target"
    rm -f "$target"
}

klipper_overlay_replace_link() {
    local source="$1" target="$2" tmp="$2.fx-new"

    # Same atomic rename as above. A stale tmp link is a broken or unwanted
    # overlay link, so the next cleanup removes it.
    if [ -d "$target" ]; then
        [ -L "$target" ] || return 1
        rm -f "$target" || return 1
    fi
    rm -f "$tmp" && ln -s "$source" "$tmp" && mv -f "$tmp" "$target" && return 0
    rm -f "$tmp"
    return 1
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
    klipper_overlay_patch_supported "$rel_file" || return 1
    klipper_overlay_ignored "$rel_file" && return 1

    return 0
}

klipper_overlay_clean_links() {
    local src_dir="$1"
    local target_dir="$2"
    local mode=${3:-apply}
    local link source rel file target directory

    while IFS= read -r link; do
        if [ ! -L "$link" ]; then
            # A .bak sharing the module inode is the twin of an interrupted user
            # link swap; the module itself is intact, so the twin can go.
            target=${link%.bak}
            if [ -f "$target" ] && [ ! -L "$target" ] && [ "$target" -ef "$link" ]; then
                rm -f "$link" || return 1
            fi
            continue
        fi

        source=$(readlink "$link") || return 1

        case "$source" in
            "$KLIPPER_USER_DIR/"*)
                # A planned target stays as is; the user linker retargets it if needed.
                if [ "$mode" = apply ] && [ -n "${KLIPPER_USER_WANT[$link]+x}" ]; then
                    KLIPPER_USER_CURRENT[$link]=$source
                    continue
                fi

                rel=${link#"$target_dir/"}
                if [ "$mode" = apply ] && [ -f "$src_dir/patches/$rel" ]; then
                    # Return priority to Forge-X without changing the stock .bak.
                    klipper_overlay_clear_user_cache "$link" || return 1
                    klipper_overlay_replace_link "$src_dir/patches/$rel" "$link" || return 1
                else
                    klipper_overlay_restore_or_remove "$link" || return 1
                fi
                continue
            ;;
            "$src_dir/plugins/"*|"$src_dir/patches/"*)
                if [ "$mode" = remove ]; then
                    klipper_overlay_restore_or_remove "$link" || return 1
                    continue
                fi
            ;;
        esac

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
    done < <(find "$target_dir" -type l -o -type f -name '*.bak')

    # Surviving patch paths identify owned backups even if their links are gone.
    if [ "$mode" = remove ]; then
        for directory in "$src_dir/patches" "$KLIPPER_USER_DIR"/*/patches; do
            [ -d "$directory" ] && [ ! -L "$directory" ] || continue
            while IFS= read -r -d '' file; do
                rel=${file#"$directory/"}
                klipper_overlay_ignored "$rel" && continue
                klipper_overlay_patch_supported "$rel" || continue
                target="$target_dir/$rel"
                if [ ! -e "$target" ] && [ ! -L "$target" ] &&
                    [ -f "$target.bak" ] && [ ! -L "$target.bak" ]; then
                    if [ "$directory" != "$src_dir/patches" ]; then
                        klipper_overlay_user_patch_path_valid "$file" "$directory" || continue
                    fi
                    klipper_overlay_restore_or_remove "$target" || return 1
                fi
            done < <(find "$directory" -type f -print0)
        done
    fi
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

klipper_overlay_normalize_legacy_toolhead_backup() {
    local rel_file="$1"
    local target="$2"
    local backup="$target.bak"
    local normalized="$backup.ff5m-normalized"

    [ "$rel_file" = "toolhead.py" ] || return 0
    [ -f "$backup" ] || return 0
    grep -q '^LOOKAHEAD_FLUSH_TIME = 0\.150$' "$backup" || return 0

    # TODO: Remove this migration after upgrades from releases with in-place
    # toolhead tuning no longer need to be supported.
    echo "// Normalize legacy toolhead backup: $backup"
    cp -p "$backup" "$normalized" || return 1
    if ! sed \
            's/^LOOKAHEAD_FLUSH_TIME = 0\.150$/LOOKAHEAD_FLUSH_TIME = 0.5/' \
            "$backup" > "$normalized"; then
        rm -f "$normalized"
        return 1
    fi
    mv -f "$normalized" "$backup"
}

klipper_overlay_link_patches() {
    local src_dir="$1"
    local target_dir="$2"
    local file rel_file target parent current

    while IFS= read -r file; do
        rel_file=${file#"$src_dir/patches/"}
        klipper_overlay_ignored "$rel_file" && continue

        klipper_overlay_patch_supported "$rel_file" || continue

        target="$target_dir/$rel_file"
        if [ -L "$target" ]; then
            current=$(readlink "$target") || return 1
            [ "$current" = "$file" ] && continue
            # A planned user override keeps priority over this Forge-X patch.
            case "$current" in
                "$KLIPPER_USER_DIR/"*)
                    [ -z "${KLIPPER_USER_WANT[$target]+x}" ] || continue
                ;;
            esac

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

        klipper_overlay_normalize_legacy_toolhead_backup \
            "$rel_file" "$target" || return 1

        echo "// Link patched klipper file: $file"
        ln -s "$file" "$target" || return 1
    done < <(find "$src_dir/patches" -type f)
}

apply_klipper_patches() {
    local src_dir="$KLIPPER_OVERLAY_SRC"
    local target_dir="$KLIPPER_OVERLAY_TARGET"
    local overwrite=${1:-0}
    local -A KLIPPER_USER_WANT=()
    local -a KLIPPER_USER_ORDER=()
    local KLIPPER_USER_CFG=""
    local -A KLIPPER_USER_CURRENT=()

    if [ "$#" -eq 0 ] && [ -f "${VAR_PATH:-}" ]; then
        overwrite=$("$CMDS/zconf.sh" "$VAR_PATH" --get "user_plugins_override_patches" "0") || {
            echo "@@ Cannot read user patch priority; overrides disabled."
            overwrite=0
        }
    fi

    mkdir -p "$KLIPPER_USER_DIR" || echo "@@ Cannot create user plugins directory; continuing mod initialization."

    # Decide user ownership before touching links: unchanged packages keep
    # their links, so a normal boot does not rewrite the Klipper tree.
    klipper_overlay_plan_user_plugins "$overwrite"

    klipper_overlay_clean_links "$src_dir" "$target_dir" || return 1

    sync
    echo "Linking extensions..."
    klipper_overlay_link_plugins "$src_dir" "$target_dir" || return 1

    sync
    echo "Apply patches..."
    klipper_overlay_link_patches "$src_dir" "$target_dir" || return 1

    if ! klipper_overlay_link_user_plugins; then
        echo "@@ Failed to apply user plugin files/config; continuing mod initialization."
        : > "$KLIPPER_USER_CONFIG" || echo "@@ Cannot clear user plugin config after overlay failure."
    fi

    sync
}

klipper_overlay_clear_user_cache() {
    local target="$1" name
    case "$target" in *.py) ;; *) return 0 ;; esac
    name=${target##*/}
    name=${name%.py}
    # Same mtime/size can otherwise reuse code from a different symlink target.
    # Also remove legacy adjacent bytecode so a deleted extra cannot load it.
    rm -f "${target}c" "${target}o" "${target%/*}/__pycache__/$name".*.pyc
}

# Sets REPLY to what a user link at target replaces once Forge-X has linked its
# own files: forge, forge-plugin, stock, absent or foreign. Current user links
# and stale Forge-X links are looked through, so the answer does not depend on
# the previous boot.
klipper_overlay_user_base() {
    local target="$1" rel source
    rel=${target#"$KLIPPER_OVERLAY_TARGET/"}

    REPLY=forge
    [ ! -f "$KLIPPER_OVERLAY_SRC/patches/$rel" ] || return 0
    REPLY=forge-plugin
    case "$rel" in
        extras/*) [ ! -e "$KLIPPER_OVERLAY_SRC/plugins/${rel#extras/}" ] || return 0 ;;
    esac

    REPLY=foreign
    if [ -L "$target" ]; then
        source=$(readlink "$target") || return 1
        case "$source" in
            "$KLIPPER_USER_DIR/"*|"$KLIPPER_OVERLAY_SRC/"*) ;;
            *) [ ! -e "$target" ] || return 0 ;;
        esac
    elif [ -e "$target" ]; then
        [ ! -f "$target" ] || REPLY=stock
        return 0
    fi

    if [ -f "$target.bak" ] && [ ! -L "$target.bak" ]; then
        REPLY=stock
    elif [ ! -e "$target.bak" ] && [ ! -L "$target.bak" ]; then
        REPLY=absent
    fi
}

klipper_overlay_user_patch_path_valid() {
    local file="$1" directory="$2" rel target parent
    rel=${file#"$directory/"}
    if [[ ! "$rel" =~ ^([a-zA-Z_][a-zA-Z0-9_]*/)*[a-zA-Z_][a-zA-Z0-9_]*\.py$ ]] ||
        [ ! -f "$file" ] || [ -L "$file" ]; then
        echo "@@ Invalid user patch, skipped: $file"
        return 1
    fi

    target="$KLIPPER_OVERLAY_TARGET/$rel"
    parent=${target%/*}
    while [ "$parent" != "$KLIPPER_OVERLAY_TARGET" ]; do
        if [ -L "$parent" ]; then
            echo "@@ User patch targets a symlinked directory, skipped: $file"
            return 1
        fi
        parent=${parent%/*}
    done
    case "$rel" in
        extras/*)
            if [ -e "$KLIPPER_OVERLAY_SRC/plugins/${rel#extras/}" ]; then
                echo "@@ Cannot replace a Forge-X plugin with a user patch: $file"
                return 1
            fi
        ;;
    esac
}

# Plan helpers add to the caller's pending/pending_order package entries.
klipper_overlay_plan_user_plugin() {
    local file="$1" name target
    name=${file##*/}
    if [[ ! "$name" =~ ^[a-zA-Z_][a-zA-Z0-9_]*\.py$ ]] ||
        [ ! -f "$file" ] || [ -L "$file" ]; then
        echo "@@ Invalid user plugin, skipped: $file"
        return 1
    fi

    target="$KLIPPER_OVERLAY_TARGET/extras/$name"
    klipper_overlay_user_base "$target" || return 1
    if [ "$name" = "__init__.py" ] || [ "$REPLY" != absent ] ||
        [ -n "${KLIPPER_USER_WANT[$target]+x}${pending[$target]+x}" ] ||
        [ -e "$KLIPPER_OVERLAY_TARGET/$name" ] || [ -L "$KLIPPER_OVERLAY_TARGET/$name" ] ||
        [ -e "$KLIPPER_OVERLAY_TARGET/${name%.py}" ] || [ -L "$KLIPPER_OVERLAY_TARGET/${name%.py}" ] ||
        [ -e "$KLIPPER_OVERLAY_SRC/patches/$name" ] || [ -e "$KLIPPER_OVERLAY_SRC/plugins/$name" ] ||
        [ -e "$KLIPPER_OVERLAY_SRC/plugins/${name%.py}" ] ||
        [ -e "$KLIPPER_OVERLAY_SRC/patches/extras/${name%.py}" ] ||
        [ -e "$KLIPPER_OVERLAY_TARGET/extras/${name%.py}" ] ||
        [ -L "$KLIPPER_OVERLAY_TARGET/extras/${name%.py}" ]; then
        echo "@@ Conflicting user plugin, skipped: $file"
        return 1
    fi

    pending[$target]=$file
    pending_order+=("$target")
}

klipper_overlay_plan_user_patch() {
    local file="$1" directory="$2" overwrite="$3" target
    klipper_overlay_user_patch_path_valid "$file" "$directory" || return 1

    target="$KLIPPER_OVERLAY_TARGET/${file#"$directory/"}"
    if { [ -e "$target.bak" ] || [ -L "$target.bak" ]; } &&
        { [ ! -f "$target.bak" ] || [ -L "$target.bak" ]; }; then
        echo "@@ Invalid user patch backup, skipped: $target.bak"
        return 1
    fi
    if [ -n "${KLIPPER_USER_WANT[$target]+x}${pending[$target]+x}" ]; then
        echo "@@ User patch target is already owned, skipped: $file"
        return 1
    fi

    klipper_overlay_user_base "$target" || return 1
    case "$REPLY" in
        # Stock modules use the same backup/restore path as Forge-X patches.
        stock) ;;
        forge)
            if [ "$overwrite" != "1" ]; then
                echo "@@ User patch requires user_plugins_override_patches=1, skipped: $file"
                return 1
            fi
        ;;
        *)
            echo "@@ User patch target is missing or already owned, skipped: $file"
            return 1
        ;;
    esac

    pending[$target]=$file
    pending_order+=("$target")
}

klipper_overlay_plan_user_package() {
    local package="$1" overwrite="$2" component file rel
    if { [ -e "$package/config.cfg" ] || [ -L "$package/config.cfg" ]; } &&
        { [ ! -f "$package/config.cfg" ] || [ -L "$package/config.cfg" ]; }; then
        echo "@@ Invalid user config, skipped: $package/config.cfg"
        return 1
    fi
    for component in plugins patches; do
        if { [ -e "$package/$component" ] || [ -L "$package/$component" ]; } &&
            { [ ! -d "$package/$component" ] || [ -L "$package/$component" ]; }; then
            echo "@@ Invalid user $component directory, skipped: $package/$component"
            return 1
        fi
    done

    for file in "$package/plugins"/*.py; do
        [ -e "$file" ] || [ -L "$file" ] || continue
        klipper_overlay_plan_user_plugin "$file" || return 1
    done
    if [ -d "$package/patches" ]; then
        while IFS= read -r -d '' file; do
            rel=${file#"$package/patches/"}
            klipper_overlay_ignored "$rel" && continue
            klipper_overlay_plan_user_patch "$file" "$package/patches" "$overwrite" || return 1
        done < <(find "$package/patches" -name '*.py' -print0)
    fi
}

# Fills the caller's KLIPPER_USER_* plan without touching the filesystem.
klipper_overlay_plan_user_plugins() {
    local overwrite="$1" package name target
    local -A pending=()
    local -a pending_order=()

    for package in "$KLIPPER_USER_DIR"/*; do
        [ -e "$package" ] || [ -L "$package" ] || continue
        name=${package##*/}
        if [ ! -d "$package" ] || [ -L "$package" ] ||
            [[ ! "$name" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_-]*$ ]]; then
            echo "@@ Invalid user package, skipped: $package"
            continue
        fi
        [ ! -e "$package/disabled" ] || continue

        # Accept a package only as a whole, so it never runs half-linked.
        pending=()
        pending_order=()
        if ! klipper_overlay_plan_user_package "$package" "$overwrite"; then
            echo "@@ Skipping user package: $package"
            continue
        fi

        for target in "${pending_order[@]}"; do
            KLIPPER_USER_WANT[$target]=${pending[$target]}
            KLIPPER_USER_ORDER+=("$target")
        done
        if [ -f "$package/config.cfg" ]; then
            KLIPPER_USER_CFG+="[include $package/config.cfg]"$'\n'
        fi
    done
}

klipper_overlay_link_user_target() {
    local source="$1" target="$2" backup=0

    # An unchanged link is left alone; rebuilding it on every boot wears flash.
    # Cleanup already read every planned user link, so no readlink is needed here.
    if [ "${KLIPPER_USER_CURRENT[$target]-}" = "$source" ]; then
        return 0
    fi

    echo "// Link user klipper file: $source"
    klipper_overlay_clear_user_cache "$target" || return 1
    if [ -f "$target" ] && [ ! -L "$target" ] &&
        [ ! -e "$target.bak" ] && [ ! -L "$target.bak" ]; then
        # A hard link keeps the stock module under both names until the rename.
        ln "$target" "$target.bak" || return 1
        backup=1
    fi
    if ! klipper_overlay_replace_link "$source" "$target"; then
        [ "$backup" -eq 0 ] || rm -f "$target.bak"
        return 1
    fi
}

klipper_overlay_publish_config() {
    local config="$1" current="" tmp
    tmp="${KLIPPER_USER_CONFIG%/*}/.${KLIPPER_USER_CONFIG##*/}.new"

    [ ! -d "$KLIPPER_USER_CONFIG" ] || return 1
    [ ! -e "$tmp" ] || rm -f "$tmp" || return 1
    if [ -f "$KLIPPER_USER_CONFIG" ]; then
        IFS= read -r -d '' current < "$KLIPPER_USER_CONFIG" || true
        [ "$current" != "$config" ] || return 0
    fi

    [ -d "${KLIPPER_USER_CONFIG%/*}" ] || mkdir -p "${KLIPPER_USER_CONFIG%/*}" || return 1
    if printf '%s' "$config" > "$tmp" && mv -f "$tmp" "$KLIPPER_USER_CONFIG"; then
        return 0
    fi
    rm -f "$tmp"
    return 1
}

klipper_overlay_link_user_plugins() {
    local target
    for target in "${KLIPPER_USER_ORDER[@]}"; do
        klipper_overlay_link_user_target "${KLIPPER_USER_WANT[$target]}" "$target" || return 1
    done
    klipper_overlay_publish_config "$KLIPPER_USER_CFG"
}
