#!/bin/bash

## Mod's preparation script
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
## Copyright (C) 2025, Sergei Rozhkov <https://github.com/ghzserg>
##
## This file may be distributed under the terms of the GNU GPLv3 license


source /opt/config/mod/.shell/common.sh

# The stock QT app (firmwareExe) pops a "Please upgrade the slicer software to
# version V1.7.3 or later." modal on every boot of the stock screen, even at
# idle with nothing to print. MainWindow::checkAppTip() shows it whenever
# Config::getCheckAppFrist() (the general/CheckAppFrist flag in Adventurer5M.json)
# is true, so clearing the flag suppresses the nag for good. Only relevant when
# the stock screen is in use - on Feather/Headless/Guppy firmwareExe never runs.
suppress_slicer_nag() {
    local config_file
    config_file=$(ls /opt/config/Adventurer5M*.json 2>/dev/null | head -1)
    [ -f "$config_file" ] || return 0

    grep -q '"CheckAppFrist"[ ]*:[ ]*true' "$config_file" || return 0

    echo "// Suppressing stock slicer-upgrade nag (CheckAppFrist=false)"
    sed -i 's/\("CheckAppFrist"[ ]*:[ ]*\)true/\1false/' "$config_file"
}

auto_enable_camera() {
    [ -f "$VAR_PATH" ] || return 0
    [ "$("$CFG_SCRIPT" "$VAR_PATH" --get camera MISSING)" = MISSING ] || return 0

    local device
    for device in /dev/video*; do
        [ -e "$device" ] || continue
        if v4l2-ctl -d "$device" -V > /dev/null 2>&1; then
            if "$CFG_SCRIPT" "$VAR_PATH" --set camera=1; then
                echo "// Camera auto-enabled: $device (setting was missing)"
            else
                echo "@@ Camera found at $device, but could not save camera=1"
            fi
            return 0
        fi
    done

    echo "// Camera auto-enable: no usable video device found (setting is still missing)"
}

DISPLAY_MODE="$("$CMDS"/zdisplay.sh test)"
DISPLAY_OFF=0
[ "$DISPLAY_MODE" != "STOCK" ] && DISPLAY_OFF=1

if [ "$DISPLAY_OFF" -eq 1 ]; then
    echo "// Starting netd..."
    rm -f "$NET_IP_F"

    network_result=0

    if ! pidof netd >/dev/null 2>&1; then
        rm -f /run/netd.sock
        netd_args=()
        # Adoption is read-only and therefore requires an existing mod target.
        # An older installation without network.conf needs the normal one-shot
        # bootstrap before future boots can safely adopt its early live link.
        [ -f "$MOD_DATA/network.conf" ] && netd_args=(--adopt-existing)
        start-stop-daemon -Sb --exec "$(command -v netd)" -- "${netd_args[@]}"
    fi

    if [ "$DISPLAY_MODE" != "FEATHER" ]; then
        # Give the freshly started daemon a moment to publish its socket; this is
        # service startup synchronization, not network retry policy.
        for _ in 1 2 3 4 5; do
            [ -S /run/netd.sock ] && break
            sleep 1
        done
        # Guppy/headless only bound how long boot waits. netd itself keeps the
        # configured network alive/reconnecting indefinitely.
        netd-cli --timeout 180 wait || network_result=$?
    fi

    if [ "$network_result" -ne 0 ] && [ "$DISPLAY_MODE" != "FEATHER" ]; then
        killall netd >/dev/null 2>&1 || true
        rm -f /run/netd.sock
        echo "?? Switch config to enabled screen..."
        "$CMDS"/zdisplay.sh stock --skip-reboot

        echo "@@ Failed to initialize mod. Booting into stock firmware..."
        DISPLAY_MODE="STOCK"
        DISPLAY_OFF=0
        sleep 1
    fi
fi

if [ "$DISPLAY_OFF" -eq 1 ]; then
    echo "// Starting alternative display."

    touch "$CUSTOM_BOOT_F"
    sync
    
    mkdir -p /dev/pts
    mount -t devpts devpts /dev/pts
    mount -t configfs none /sys/kernel/config -o rw,relatime
    mount -t debugfs none /sys/kernel/debug -o rw,relatime

    echo "// MCU booting..."
    /opt/config/mod/.bin/exec/boot_mcu 2>&1
    
    auto_enable_camera

    echo "// Start klipper."
    [ "$DISPLAY_MODE" = "FEATHER" ] && touch "$FORGE_X_SCREEN_BUSY_F"
    /opt/config/mod/.shell/commands/zstart_klipper.sh &> /dev/null
    
    echo "// Boot sequence done!"
else
    echo "// Booting stock firmware..."
    suppress_slicer_nag
fi
