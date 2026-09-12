#!/bin/bash

## Configuration backup and restore
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

source /opt/config/mod/.shell/common.sh

CFG_PATH="/opt/config/mod_data/backup.params.cfg"
PARAMS="-p ${CFG_PATH}"

PRIVATE_PARAMS=(
    ./mod_data/ssh.conf
    ./mod_data/ssh.key
    ./mod_data/ssh.pub.txt
)

COMMON_CFG_PARAMS=(
    ./printer.cfg
    ./printer.base.cfg
    ./printer.base.cfg.bak
    ./mod_data/backup.params.cfg
    ./mod_data/camera.conf
    ./mod_data/user.cfg
    ./mod_data/user.moonraker.conf
    ./mod_data/variables.cfg
    ./mod_data/web.conf
)

TAR_BACKUP_PARAMS=(
    "${PRIVATE_PARAMS[@]}"
    "${COMMON_CFG_PARAMS[@]}"
)

TAR_DEBUG_PARAMS=(
    "${COMMON_CFG_PARAMS[@]}"
    ./mod/sql/version
    /data/logFiles/boot.log*
    /data/logFiles/skip.log*
    /data/logFiles/ssh.log*
    /data/logFiles/wifi.log*
    /data/logFiles/netd.log*
    /data/logFiles/service.log*
    /data/logFiles/uninstall.log*
    /data/logFiles/mod/*.log*
    /data/logFiles/verification.log*
    /data/logFiles/printer.log*
    /data/logFiles/moonraker.log*
    /data/logFiles/console*.log
    /data/logFiles/firmware-installer-launch.log*
    /data/logFiles/firmwareExe.log*
    /data/logFiles/ffstartup-arm.log
    /data/logFiles/dmesg.complete.log
    /data/logFiles/filesystem-usage.txt
    /data/logFiles/dmesg-recovery.log
    /data/logFiles/recovery.log*
    /root/version
    /data/.mod/.forge-x/etc/os-release
    /data/.mod/.forge-x/version.txt
)

ensure_backup_params() {
    if [ ! -f "$CFG_PATH" ]; then
        cp "/opt/config/mod/.cfg/default/backup.params.cfg" "$CFG_PATH"
    fi
}

archive_to() {
    local list_name="$1"
    local output="$2"
    local raw partial status=0

    declare -n list_ref="$list_name"

    mkdir -p "${output%/*}" || return 1
    partial="${output}.part"
    raw="${output}.part.tar"
    rm -f "$partial" "$raw"

    # Keep partial output deterministic even when Recovery terminates this
    # operation. Recovery also terminates the whole process group on timeout.
    trap 'rm -f -- "$partial" "$raw"' EXIT
    trap 'rm -f -- "$partial" "$raw"; exit 1' HUP INT TERM

    if ! pushd /opt/config > /dev/null; then
        status=1
    else
        # Preserve the existing backup/debug selection and tar member layout.
        # Some entries are optional; the historical collector still produced
        # an archive when tar reported a missing optional path.
        tar -cf "$raw" "${list_ref[@]}" &> /dev/null || true
        if [ ! -s "$raw" ]; then
            status=1
        elif ! gzip -c "$raw" > "$partial"; then
            status=1
        elif [ ! -s "$partial" ]; then
            status=1
        elif ! mv -f "$partial" "$output"; then
            status=1
        fi
        popd > /dev/null || true
    fi

    rm -f "$raw"
    [ "$status" -eq 0 ] || rm -f "$partial"
    trap - EXIT HUP INT TERM

    [ "$status" -eq 0 ] || return "$status"
    sync
    printf '%s\n' "$output"
}

tar_backup() {
    local prefix="$1"
    local list_name="$2"
    local name output

    name="${prefix}_$(date +%Y%m%d_%H%M%S)"
    output="/opt/config/mod_data/$name.tar.gz"
    archive_to "$list_name" "$output" || return 1

    echo "Archive successfully created! You can download it from the Configuration tab:"
    echo "Configuration -> mod_data -> $name.tar.gz"
}

recovery_archive() {
    local kind="$1"
    local list_name="$2"
    local output="$3"

    case "$kind:$output" in
        backup:/data/forge-x-recovery/backup.tar.gz) ;;
        debug:/data/forge-x-recovery/debug.tar.gz) ;;
        *)
            echo "Invalid Recovery archive output path." >&2
            return 2
        ;;
    esac

    archive_to "$list_name" "$output"
}

copy_pipe() {
    local pipe_name="$1"
    local file="$2"

    touch "$file"
    while true; do
        if read -t 0.1 -r line < "$pipe_name"; then
            echo "$line" >> "$file"
        else
            break
        fi
    done
}

collect_debug_snapshots() {
    local usage="/data/logFiles/filesystem-usage.txt"
    local kernel="/data/logFiles/dmesg-recovery.log"
    local usage_part="${usage}.part.$$"
    local kernel_part="${kernel}.part.$$"
    local status=0

    mkdir -p /data/logFiles || return 1
    rm -f -- "$usage_part" "$kernel_part"
    trap 'rm -f -- "$usage_part" "$kernel_part"; exit 1' HUP INT TERM

    # These are small runtime snapshots added to the existing Forge-X debug
    # policy. They are not a second Recovery-specific diagnostics tree.
    df -P > "$usage_part" 2>&1 || true
    dmesg > "$kernel_part" 2>&1 || true

    mv -f "$usage_part" "$usage" || status=1
    mv -f "$kernel_part" "$kernel" || status=1
    rm -f -- "$usage_part" "$kernel_part"
    trap - HUP INT TERM
    return "$status"
}

create_debug_archive() {
    local output="${1:-}"

    collect_debug_snapshots || return 1
    if [ -n "$output" ]; then
        recovery_archive debug TAR_DEBUG_PARAMS "$output"
    else
        tar_backup "debug" TAR_DEBUG_PARAMS
    fi
}

while [ "$#" -gt 0 ]; do
    param=$1; shift

    case "$param" in
        --backup)
            ensure_backup_params || exit 1
            PARAMS="-m backup ${PARAMS}"
        ;;
        --restore)
            ensure_backup_params || exit 1
            PARAMS="-m restore ${PARAMS} -w"
        ;;
        --verify)
            ensure_backup_params || exit 1
            PARAMS="-m verify ${PARAMS}"
        ;;
        --tar-backup)
            ensure_backup_params || exit 1
            tar_backup "backup" TAR_BACKUP_PARAMS
            exit $?
        ;;
        --tar-debug)
            copy_pipe "/tmp/printer" "/data/logFiles/console_$(date +%Y%m%d_%H%M%S).log"
            create_debug_archive
            exit $?
        ;;
        --tar-backup-to)
            [ "$#" -gt 0 ] || { echo "Missing backup output path." >&2; exit 2; }
            ensure_backup_params || exit 1
            recovery_archive backup TAR_BACKUP_PARAMS "$1"
            exit $?
        ;;
        --tar-debug-to)
            [ "$#" -gt 0 ] || { echo "Missing diagnostics output path." >&2; exit 2; }
            copy_pipe "/tmp/printer" "/data/logFiles/console_$(date +%Y%m%d_%H%M%S).log"
            create_debug_archive "$1"
            exit $?
        ;;
        --dry)
            if [ "$1" -eq 1 ]; then
                PARAMS="${PARAMS} --dry"
            fi
            shift
        ;;
        --verbose)
            if [ "$1" -eq 1 ]; then
                PARAMS="${PARAMS} --verbose"
            fi
            shift
        ;;
        *)
            echo "Unknown parameter: '$param'"
            exit 1
        ;;
    esac
done

chroot "$MOD" /bin/python3 "$PY"/cfg_backup.py $PARAMS
