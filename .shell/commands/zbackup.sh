#!/bin/bash

## Configuration backup and restore
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

source /opt/config/mod/.shell/common.sh

CFG_PATH="/opt/config/mod_data/backup.params.cfg"

if [ ! -f $CFG_PATH ]; then
    cp "/opt/config/mod/.cfg/default/backup.params.cfg" "$CFG_PATH"
fi


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
    /data/logFiles/firmwareExe.log*
    /data/logFiles/ffstartup-arm.log
    /data/logFiles/dmesg.complete.log
    /root/version
    /data/.mod/.forge-x/etc/os-release
    /data/.mod/.forge-x/version.txt
)

tar_backup() {
    local prefix="$1"
    local list_name="$2"

    declare -n list_ref="$list_name"

    local name="${prefix}_$(date +%Y%m%d_%H%M%S)"

    pushd > /dev/null /opt/config || exit 1

    tar -cf "./mod_data/$name.tar" "${list_ref[@]}" &> /dev/null
    gzip "./mod_data/$name.tar"
    rm -f "./mod_data/$name.tar"

    popd > /dev/null || true

    echo "Archive successfully created! You can download it from the Configuration tab:"
    echo "Configuration -> mod_data -> $name.tar.gz"
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

while [ "$#" -gt 0 ]; do
    param=$1; shift
    
    case "$param" in
        --backup)
            PARAMS="-m backup ${PARAMS}"
        ;;
        --restore)
            PARAMS="-m restore ${PARAMS} -w"
        ;;
        --verify)
            PARAMS="-m verify ${PARAMS}"
        ;;
        --tar-backup)
            tar_backup "backup" TAR_BACKUP_PARAMS
            exit $?
        ;;
        --tar-debug)
            copy_pipe "/tmp/printer" "/data/logFiles/console_$(date +%Y%m%d_%H%M%S).log"
            tar_backup "debug" TAR_DEBUG_PARAMS
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
            echo "Unknown parameter: '$1'"
            exit 1
        ;;
    esac
done

chroot "$MOD" /bin/python3 "$PY"/cfg_backup.py $PARAMS
