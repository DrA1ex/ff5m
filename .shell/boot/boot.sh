#!/bin/bash

## Mod's preparation script
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
## Copyright (C) 2025, Sergei Rozhkov <https://github.com/ghzserg>
##
## This file may be distributed under the terms of the GNU GPLv3 license


MOD_CUSTOM_BOOT=0
source /opt/config/mod/.shell/common.sh
source /opt/config/mod/.shell/network_common.sh

if [ ! -f /etc/init.d/S00init ]; then
    echo "@@ Missing initialization script. Initialize now."
    
    rm -f /etc/init.d/S00fix
    ln -s "$SCRIPTS/S00init" /etc/init.d/S00init
    /etc/init.d/S00init start
fi

DISPLAY_OFF=0
[ "$("$CMDS"/zdisplay.sh test)" != "STOCK" ] && DISPLAY_OFF=1

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
    if [ -f "/etc/wpa_supplicant.conf" ]; then
        echo "Configuration found"
        
        if ! ip link show | grep -q "wlan0"; then
            echo "Load kernel module."
            insmod /lib/modules/8821cu.ko
            modprobe 8821cu
        fi
        
        echo "// Try to connect..."

        for _ in $(seq 5); do
            "$SCRIPTS/boot/wifi_connect.sh" 2>&1 | logged /data/logFiles/wifi.log --no-print --send-to-screen
            ret="${PIPESTATUS[0]}"

            if [ "$ret" -eq 0 ]; then
                # Retire Ethernet only after Wi-Fi has an address.  Until this
                # point it remains a working rollback path.
                network_deactivate_interface eth0
                rm -f "$ETHERNET_CONNECTED_F"
                MOD_CUSTOM_BOOT=1
                echo "// Connected!"
                break
            fi

            echo "@@ WPA start failed. Retry..."
            sleep 1
        done
        
        #TODO: Create AP if no active network configuration
    fi

    if [ "$MOD_CUSTOM_BOOT" -eq 1 ]; then
        touch "$WIFI_CONNECTED_F"
        sync

        echo "Start wifi reconnect daemon."

        killall "wpa_cli" 2> /dev/null
        wpa_cli -B -a "$SCRIPTS/boot/wifi_reconnect.sh" -i wlan0
    fi
}

ethernet_init() {
    echo "// Initializing Ethernet connection..."

    # Keep Wi-Fi alive until Ethernet has actually obtained a lease.  This
    # makes switching modes transactional instead of dropping both links.
    network_activate_dhcp eth0 25 \
        || { echo "@@ Failed to initialize connection!"; return 1; }

    killall wpa_cli 2>/dev/null || true
    killall wpa_supplicant 2>/dev/null || true
    network_deactivate_interface wlan0
    rm -f "$WIFI_CONNECTED_F"

    touch "$ETHERNET_CONNECTED_F"
    sync

    MOD_CUSTOM_BOOT=1
    echo "// Ethernet connection initialized with DHCP"
}

save_network_ip() {
    rm -f "$NET_IP_F"
    IP="$(network_ipv4 "$1")"
    [ -n "$IP" ] && echo "$IP" > "$NET_IP_F"
}

if [ "$DISPLAY_OFF" -eq 1 ]; then
    # Init Network
    
    echo "// Network initialization..."
    CONFIG_FILE=$(ls /opt/config/Adventurer5M*.json 2>/dev/null | head -n 1)
    
    NETWORK_MODE=""
    [ -f /opt/config/mod_data/network_mode ] && NETWORK_MODE=$(head -n 1 /opt/config/mod_data/network_mode)
    case "$NETWORK_MODE" in
        WIFI|ETHERNET) ;;
        *) NETWORK_MODE="" ;;
    esac

    if [ "$NETWORK_MODE" = "ETHERNET" ]; then
        (ethernet_init && save_network_ip eth0) >> /data/logFiles/wifi.log 2>&1 &
    elif [ "$NETWORK_MODE" = "WIFI" ]; then
        (wifi_init && save_network_ip wlan0) >> /data/logFiles/wifi.log 2>&1 &
    elif [ -f "$CONFIG_FILE" ]; then
        ETHERNET_STATUS=$(grep "ethernetStatus" < "$CONFIG_FILE" | sed 's/.*"ethernetStatus"[ ]*:[ ]*\([^,]*\).*/\1/')
        if [ "$ETHERNET_STATUS" = "true" ]; then
            (ethernet_init && save_network_ip eth0) >> /data/logFiles/wifi.log 2>&1 &
        else
            (wifi_init && save_network_ip wlan0) >> /data/logFiles/wifi.log 2>&1 &
        fi
    else
        echo "@@ Config file not found"
    fi
fi

if [ "$DISPLAY_OFF" -eq 1 ]; then
    echo "// Starting alternative display; network initialization continues in background."

    touch "$CUSTOM_BOOT_F"
    sync
    
    mkdir -p /dev/pts
    mount -t devpts devpts /dev/pts
    mount -t configfs none /sys/kernel/config -o rw,relatime
    mount -t debugfs none /sys/kernel/debug -o rw,relatime

    if [ "$("$CMDS"/zdisplay.sh test)" = "FEATHER" ]; then
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
