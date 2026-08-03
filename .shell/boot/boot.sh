#!/bin/bash

## Mod's preparation script
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
## Copyright (C) 2025, Sergei Rozhkov <https://github.com/ghzserg>
##
## This file may be distributed under the terms of the GNU GPLv3 license


source /opt/config/mod/.shell/common.sh
source /opt/config/mod/.shell/network_common.sh

if [ ! -f /etc/init.d/S00init ]; then
    echo "@@ Missing initialization script. Initialize now."
    
    rm -f /etc/init.d/S00fix
    ln -s "$SCRIPTS/S00init" /etc/init.d/S00init
    /etc/init.d/S00init start
fi

DISPLAY_MODE="$("$CMDS"/zdisplay.sh test)"
DISPLAY_OFF=0
[ "$DISPLAY_MODE" != "STOCK" ] && DISPLAY_OFF=1

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

wifi_init() {
    local send_to_screen=${1:-0}
    local ret

    [ -f /etc/wpa_supplicant.conf ] || {
        echo "@@ Wi-Fi configuration is missing." >&2
        return 1
    }

    echo "Configuration found"

    if ! ip link show wlan0 >/dev/null 2>&1; then
        echo "Load kernel module."
        insmod /lib/modules/8821cu.ko 2>/dev/null \
            || modprobe 8821cu \
            || { echo "@@ Failed to load Wi-Fi driver." >&2; return 1; }
    fi

    echo "// Try to connect..."

    for _ in $(seq 5); do
        if [ "$send_to_screen" -eq 1 ]; then
            "$SCRIPTS/boot/wifi_connect.sh" 2>&1 \
                | logged --no-print --send-to-screen /data/logFiles/wifi.log
        else
            "$SCRIPTS/boot/wifi_connect.sh" 2>&1 \
                | logged --no-print /data/logFiles/wifi.log
        fi
        ret=${PIPESTATUS[0]}

        if [ "$ret" -eq 0 ]; then
            # Retire Ethernet only after Wi-Fi has an address. Until this
            # point it remains a working rollback path.
            network_deactivate_interface eth0
            rm -f "$ETHERNET_CONNECTED_F"

            touch "$WIFI_CONNECTED_F"
            sync

            echo "Start wifi reconnect daemon."
            killall wpa_cli 2>/dev/null || true
            wpa_cli -B -a "$SCRIPTS/boot/wifi_reconnect.sh" -i wlan0 \
                || echo "@@ Failed to start Wi-Fi reconnect daemon." >&2

            echo "// Connected!"
            return 0
        fi

        echo "@@ WPA start failed. Retry..."
        sleep 1
    done

    return 1
}

ethernet_init() {
    echo "// Initializing Ethernet connection..."

    # Keep Wi-Fi alive until Ethernet has actually obtained a lease. This
    # makes switching modes transactional instead of dropping both links.
    network_activate_dhcp eth0 25 \
        || { echo "@@ Failed to initialize connection!"; return 1; }

    killall wpa_cli 2>/dev/null || true
    killall wpa_supplicant 2>/dev/null || true
    network_deactivate_interface wlan0
    rm -f "$WIFI_CONNECTED_F"

    touch "$ETHERNET_CONNECTED_F"
    sync

    echo "// Ethernet connection initialized with DHCP"
}

save_network_ip() {
    local ip

    rm -f "$NET_IP_F"
    ip="$(network_ipv4 "$1")"
    [ -n "$ip" ] && echo "$ip" > "$NET_IP_F"
}

network_init() {
    local send_wifi_to_screen=${1:-0}
    local config_file network_mode ethernet_status

    config_file=$(ls /opt/config/Adventurer5M*.json 2>/dev/null | head -n 1)

    network_mode=""
    [ -f /opt/config/mod_data/network_mode ] \
        && network_mode=$(head -n 1 /opt/config/mod_data/network_mode)

    case "$network_mode" in
        WIFI|ETHERNET) ;;
        *) network_mode="" ;;
    esac

    if [ -z "$network_mode" ]; then
        [ -f "$config_file" ] || {
            echo "@@ Config file not found" >&2
            return 1
        }

        ethernet_status=$(grep "ethernetStatus" < "$config_file" \
            | sed 's/.*"ethernetStatus"[ ]*:[ ]*\([^,]*\).*/\1/')

        if [ "$ethernet_status" = "true" ]; then
            network_mode="ETHERNET"
        else
            network_mode="WIFI"
        fi
    fi

    case "$network_mode" in
        ETHERNET)
            ethernet_init && save_network_ip eth0
        ;;
        WIFI)
            wifi_init "$send_wifi_to_screen" && save_network_ip wlan0
        ;;
    esac
}

if [ "$DISPLAY_OFF" -eq 1 ]; then
    echo "// Network initialization..."
    rm -f "$NET_IP_F"

    if [ "$DISPLAY_MODE" = "FEATHER" ]; then
        # Run independently from the boot pipeline. Feather does not wait for
        # network initialization and reports the resulting state itself.
        # Also run it fully detached to avoid leaving a shell process running.
        network_init 0 </dev/null >/dev/null 2>&1 &
    elif ! network_init 1; then
        echo "?? Switch config to enabled screen..."
        "$CMDS"/zdisplay.sh stock --skip-reboot

        echo "@@ Failed to initialize mod. Booting into stock firmware..."
        DISPLAY_MODE="STOCK"
        DISPLAY_OFF=0
        sleep 1
    fi
fi

if [ "$DISPLAY_OFF" -eq 1 ]; then
    if [ "$DISPLAY_MODE" = "FEATHER" ]; then
        echo "// Starting Feather; network initialization continues in background."
    else
        echo "// Network initialized. Starting alternative display."
    fi

    touch "$CUSTOM_BOOT_F"
    sync
    
    mkdir -p /dev/pts
    mount -t devpts devpts /dev/pts
    mount -t configfs none /sys/kernel/config -o rw,relatime
    mount -t debugfs none /sys/kernel/debug -o rw,relatime

    if [ "$DISPLAY_MODE" = "FEATHER" ]; then
        echo "// Starting calibrated Feather touch input..."
        chroot "$MOD" /opt/config/mod/.root/S35tslib start
    fi
    
    echo "// MCU booting..."
    /opt/config/mod/.bin/exec/boot_mcu 2>&1
    
    echo "// Start klipper."
    /opt/config/mod/.shell/commands/zstart_klipper.sh &> /dev/null
    
    echo "// Boot sequence done!"
else
    echo "// Booting stock firmware..."
    suppress_slicer_nag
fi
