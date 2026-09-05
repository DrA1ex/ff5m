#!/bin/bash

## Early fail-open recovery lifecycle.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

source /opt/config/mod/.shell/boot/boot_mode.sh || exit 1
source /opt/config/mod/.shell/common.sh || exit 1
source /opt/config/mod/.shell/boot/stock_identity.sh || exit 1

RECOVERY_ACTION=/tmp/forge_x_recovery_action
RECOVERY_TOUCH_DEVICE=/dev/input/guppy
RECOVERY_LOG=/data/logFiles/recovery.log
RECOVERY_INSTALL_IMAGE=/opt/config/mod/.shell/boot/install-image.sh
RECOVERY_UNINSTALL=/opt/config/mod/.shell/uninstall.sh
RECOVERY_RESET_CONFIG=/opt/config/mod/.shell/commands/zreset_config.sh
RECOVERY_PYTHON=""
RECOVERY_CURL=""
RECOVERY_CACERT=""
RECOVERY_CHROOT_MOUNTS=()
RECOVERY_NETD_STARTED=0
RECOVERY_NTP_STARTED=0
RECOVERY_TSLIB_STARTED=0
RECOVERY_TSLIB_PARENT_MOUNTED=0
RECOVERY_DEVPTS_MOUNTED=0
RECOVERY_SSH_ACTIVE=0
RECOVERY_NOTICE=""

prepare_recovery_log() {
    mkdir -p "${RECOVERY_LOG%/*}" || return 1

    mv -f "$RECOVERY_LOG.4" "$RECOVERY_LOG.5" >/dev/null 2>&1 || true
    mv -f "$RECOVERY_LOG.3" "$RECOVERY_LOG.4" >/dev/null 2>&1 || true
    mv -f "$RECOVERY_LOG.2" "$RECOVERY_LOG.3" >/dev/null 2>&1 || true
    mv -f "$RECOVERY_LOG.1" "$RECOVERY_LOG.2" >/dev/null 2>&1 || true
    mv -f "$RECOVERY_LOG" "$RECOVERY_LOG.1" >/dev/null 2>&1 || true

    : > "$RECOVERY_LOG"
}

mount_recovery_chroot() {
    mkdir -p "$MOD"/proc "$MOD"/sys "$MOD"/dev "$MOD"/run "$MOD"/tmp \
        "$MOD"/data "$MOD"/opt/config || return 1

    mount -t proc proc "$MOD"/proc || return 1
    RECOVERY_CHROOT_MOUNTS+=("$MOD/proc")

    mount --rbind /sys "$MOD"/sys || return 1
    RECOVERY_CHROOT_MOUNTS+=("$MOD/sys")

    mount --rbind /dev "$MOD"/dev || return 1
    RECOVERY_CHROOT_MOUNTS+=("$MOD/dev")

    mount --bind /run "$MOD"/run || return 1
    RECOVERY_CHROOT_MOUNTS+=("$MOD/run")

    mount --bind /tmp "$MOD"/tmp || return 1
    RECOVERY_CHROOT_MOUNTS+=("$MOD/tmp")

    mount --bind /data "$MOD"/data || return 1
    RECOVERY_CHROOT_MOUNTS+=("$MOD/data")

    mount --bind /opt/config "$MOD"/opt/config || return 1
    RECOVERY_CHROOT_MOUNTS+=("$MOD/opt/config")
}

mount_recovery_devpts() {
    mount | grep -q ' on /dev/pts ' && return 0

    mkdir -p /dev/pts || return 1
    mount -t devpts devpts /dev/pts || return 1
    RECOVERY_DEVPTS_MOUNTED=1
}

stop_recovery_netd() {
    [ "$RECOVERY_NETD_STARTED" -eq 1 ] || return 0

    killall netd >/dev/null 2>&1 || true
    for _ in 1 2 3 4 5; do
        pidof netd >/dev/null 2>&1 || break
        sleep 0.1
    done

    rm -f /run/netd.sock
    RECOVERY_NETD_STARTED=0
}

start_recovery_clock() {
    if [ -s "$MOD/etc/fake-hwclock.data" ]; then
        chroot "$MOD" /usr/sbin/fake-hwclock load >/dev/null 2>&1 || true
    else
        date -u -s "2026-09-01 00:00:00" >/dev/null 2>&1 || true
    fi

    chroot "$MOD" /opt/config/mod/.root/S45ntpd start >/dev/null 2>&1 \
        || return 1
    RECOVERY_NTP_STARTED=1
}

stop_recovery_clock() {
    [ "$RECOVERY_NTP_STARTED" -eq 1 ] || return 0

    chroot "$MOD" /opt/config/mod/.root/S45ntpd stop >/dev/null 2>&1 || true
    RECOVERY_NTP_STARTED=0
}

unmount_recovery_chroot() {
    local index

    for ((index=${#RECOVERY_CHROOT_MOUNTS[@]} - 1; index >= 0; index--)); do
        umount -lf "${RECOVERY_CHROOT_MOUNTS[index]}" >/dev/null 2>&1 || true
    done

    RECOVERY_CHROOT_MOUNTS=()
}

cleanup_recovery_runtime() {
    rm -f "$RECOVERY_ACTION"

    # Recovery SSH intentionally survives soft stock boot. Hard bypass and
    # firmware hand-off stop it explicitly before this runtime is released.
    stop_recovery_clock

    if [ "$RECOVERY_TSLIB_STARTED" -eq 1 ]; then
        chroot "$MOD" /opt/config/mod/.root/S35tslib stop >/dev/null 2>&1 || true
        RECOVERY_TSLIB_STARTED=0
    fi

    if [ "$RECOVERY_TSLIB_PARENT_MOUNTED" -eq 1 ]; then
        umount /tmp/parent_root >/dev/null 2>&1 || true
        rmdir /tmp/parent_root >/dev/null 2>&1 || true
        RECOVERY_TSLIB_PARENT_MOUNTED=0
    fi

    stop_recovery_netd
    unmount_recovery_chroot

    if [ "$RECOVERY_DEVPTS_MOUNTED" -eq 1 ] \
            && [ "$RECOVERY_SSH_ACTIVE" -eq 0 ]; then
        umount /dev/pts >/dev/null 2>&1 || true
        RECOVERY_DEVPTS_MOUNTED=0
    fi
}
trap cleanup_recovery_runtime EXIT
trap 'exit 1' HUP INT TERM

start_recovery_netd() {
    pidof netd >/dev/null 2>&1 && return 0

    rm -f /run/netd.sock
    netd_args=()
    [ -f "$MOD_DATA/network.conf" ] && netd_args=(--adopt-existing)

    start-stop-daemon -Sb --exec "$(command -v netd)" -- "${netd_args[@]}" \
        || return 1
    RECOVERY_NETD_STARTED=1
}

start_recovery_touch() {
    local already_running=0
    local parent_mounted=0

    if [ -f "$MOD/var/run/ts_uinput.pid" ] \
            && kill -0 "$(cat "$MOD/var/run/ts_uinput.pid")" 2>/dev/null; then
        already_running=1
    fi

    mount | grep -q ' on /tmp/parent_root ' && parent_mounted=1

    chroot "$MOD" /opt/config/mod/.root/S35tslib start >/dev/null 2>&1 \
        || return 1
    [ "$already_running" -eq 1 ] || RECOVERY_TSLIB_STARTED=1

    if [ "$parent_mounted" -eq 0 ] \
            && mount | grep -q ' on /tmp/parent_root '; then
        RECOVERY_TSLIB_PARENT_MOUNTED=1
    fi

    [ -e "$RECOVERY_TOUCH_DEVICE" ]
}

start_recovery_ssh() {
    if [ -f /run/dropbear.pid ] \
            && kill -0 "$(cat /run/dropbear.pid)" 2>/dev/null; then
        RECOVERY_SSH_ACTIVE=1
        return 0
    fi

    /opt/config/mod/.shell/S60dropbear recovery-start || return 1
    for _ in 1 2 3 4 5; do
        if [ -f /run/dropbear.pid ] \
                && kill -0 "$(cat /run/dropbear.pid)" 2>/dev/null; then
            RECOVERY_SSH_ACTIVE=1
            return 0
        fi

        sleep 0.1
    done

    return 1
}

stop_recovery_ssh() {
    [ "$RECOVERY_SSH_ACTIVE" -eq 1 ] || return 0

    /opt/config/mod/.shell/S60dropbear stop >/dev/null 2>&1 || true
    RECOVERY_SSH_ACTIVE=0
}

valid_firmware_path() {
    local path=$1

    case "$path" in
        /data/forge-x-recovery/Adventurer5M-*.tgz|\
        /data/forge-x-recovery/Adventurer5M-*.tar.xz|\
        /data/forge-x-recovery/Adventurer5MPro-*.tgz|\
        /data/forge-x-recovery/Adventurer5MPro-*.tar.xz)
            [ -f "$path" ] && [ ! -L "$path" ]
        ;;

        *) return 1 ;;
    esac
}

find_stock_python() {
    local candidate name selected=""

    for candidate in /opt/Python-*/bin/python3.*; do
        [ -x "$candidate" ] || continue
        name=${candidate##*/}
        case "$name" in python3.*[!0-9]*) continue ;; esac

        [ -z "$selected" ] || return 1
        selected=$candidate
    done

    [ -n "$selected" ] || return 1
    printf '%s\n' "$selected"
}

find_vendor_curl() {
    local candidate selected=""

    for candidate in /opt/cloud/curl-*-https/bin/curl; do
        [ -x "$candidate" ] || continue
        [ -z "$selected" ] || return 1
        selected=$candidate
    done

    [ -n "$selected" ] || return 1
    printf '%s\n' "$selected"
}

find_vendor_cacert() {
    local candidate python_root selected=""

    python_root=${RECOVERY_PYTHON%/bin/*}
    for candidate in "$python_root"/lib/python*/site-packages/pip/_vendor/certifi/cacert.pem; do
        [ -r "$candidate" ] || continue
        [ -z "$selected" ] || return 1
        selected=$candidate
    done

    [ -n "$selected" ] || return 1
    printf '%s\n' "$selected"
}

run_recovery_ui() {
    rm -f "$RECOVERY_ACTION"

    RECOVERY_SSH_ACTIVE="$RECOVERY_SSH_ACTIVE" \
        RECOVERY_NOTICE="$RECOVERY_NOTICE" \
        RECOVERY_CURL="$RECOVERY_CURL" \
        RECOVERY_CACERT="$RECOVERY_CACERT" \
        "$RECOVERY_PYTHON" -u /opt/config/mod/.py/recovery.py \
            --machine "$FIRMWARE_MACHINE" \
            --action-file "$RECOVERY_ACTION"
}

run_firmware_installer() {
    local image=$1
    local -a log_options=(--no-log)
    local -a statuses

    if [ -f "$RECOVERY_LOG" ] && [ -w "$RECOVERY_LOG" ]; then
        log_options=("$RECOVERY_LOG")
    fi

    "$RECOVERY_INSTALL_IMAGE" "$image" 2>&1 \
        | logged "${log_options[@]}" --send-to-screen --screen-no-followup
    statuses=("${PIPESTATUS[@]}")
    return "${statuses[0]}"
}

clear_recovery_screen() {
    screen_typer fill -p 0 0 -s 800 480 -c 0
}

run_recovery_lifecycle() {
    local action image status

    echo "// Recovery lifecycle started."
    RECOVERY_PYTHON=$(find_stock_python) || {
        echo "@@ Unable to select exactly one stock Python runtime."
        return 1
    }
    RECOVERY_CURL=$(find_vendor_curl) || RECOVERY_CURL=""
    RECOVERY_CACERT=$(find_vendor_cacert) || RECOVERY_CACERT=""
    echo "// Stock Python: $RECOVERY_PYTHON"
    if [ -n "$RECOVERY_CURL" ] && [ -n "$RECOVERY_CACERT" ]; then
        echo "// Vendor HTTPS client is available."
    else
        echo "?? Vendor HTTPS client is unavailable."
    fi

    load_stock_printer_identity || {
        echo "@@ Unable to identify the stock printer model."
        return 1
    }
    echo "// Printer model: $FIRMWARE_MACHINE"

    mount_recovery_devpts || {
        echo "@@ Unable to prepare recovery devpts."
        return 1
    }
    mount_recovery_chroot || {
        echo "@@ Unable to mount the recovery chroot."
        return 1
    }
    start_recovery_touch || {
        echo "@@ Unable to start recovery touch input."
        return 1
    }
    start_recovery_netd || echo "?? Recovery networking is unavailable."
    start_recovery_clock || echo "?? Recovery time synchronization is unavailable."

    "$SCRIPTS/screen.sh" splash_stop >/dev/null 2>&1 || true

    while true; do
        echo "// Starting recovery UI."
        run_recovery_ui
        status=$?
        if [ "$status" -ne 0 ]; then
            echo "@@ Recovery UI exited with status $status."
            return "$status"
        fi

        RECOVERY_NOTICE=""
        action=$(cat "$RECOVERY_ACTION" 2>/dev/null) || {
            echo "@@ Recovery UI returned without an action."
            return 1
        }
        echo "// Recovery action: $action"

        case "$action" in
            stock-soft)
                forge_x_publish_stock_mode stock-soft || return 1
                return 0
            ;;

            stock-hard)
                stop_recovery_ssh
                forge_x_publish_stock_mode stock-hard || return 1
                return 0
            ;;

            ssh)
                if ! start_recovery_ssh; then
                    RECOVERY_NOTICE="Unable to start recovery SSH."
                fi
            ;;

            reboot)
                forge_x_prepare_normal_boot || return 1
                cleanup_recovery_runtime
                trap - EXIT

                sync
                reboot -f
                return 1
            ;;

            flash:*)
                image=${action#flash:}
                valid_firmware_path "$image" || {
                    echo "@@ Recovery UI returned an invalid firmware path."
                    return 1
                }

                stop_recovery_ssh
                cleanup_recovery_runtime
                trap - EXIT

                clear_recovery_screen \
                    || echo "?? Unable to clear the Recovery screen before firmware hand-off."
                sync
                run_firmware_installer "$image"
                return $?
            ;;

            uninstall|uninstall-soft)
                [ -f "$RECOVERY_UNINSTALL" ] || {
                    echo "@@ Forge-X uninstall script is unavailable."
                    return 1
                }
                cp -f "$RECOVERY_UNINSTALL" /tmp/forge-x-uninstall.sh || {
                    echo "@@ Unable to stage Forge-X uninstall script."
                    return 1
                }

                stop_recovery_ssh
                cleanup_recovery_runtime
                trap - EXIT

                sync
                if [ "$action" = "uninstall-soft" ]; then
                    /bin/bash /tmp/forge-x-uninstall.sh --soft
                else
                    /bin/bash /tmp/forge-x-uninstall.sh
                fi
                return $?
            ;;

            reset-config)
                if "$RECOVERY_RESET_CONFIG" "$FIRMWARE_MACHINE"; then
                    RECOVERY_NOTICE="Configuration reset complete. Reboot to apply the defaults."
                else
                    RECOVERY_NOTICE="Configuration reset failed. Existing settings were restored."
                fi
            ;;

            *)
                echo "@@ Recovery UI returned an unknown action."
                return 1
            ;;
        esac
    done
}

main() {
    local status

    rm -f "$RECOVERY_ACTION"
    mount_data_partition || return 1

    if prepare_recovery_log; then
        run_recovery_lifecycle >> "$RECOVERY_LOG" 2>&1
        return $?
    fi

    echo "?? Unable to prepare the persistent recovery log." >&2
    run_recovery_lifecycle
    status=$?
    return "$status"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main
    exit $?
fi
