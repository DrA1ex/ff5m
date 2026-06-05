#!/bin/bash

## Mod's preparation script
##
## Copyright (C) 2025, Alexander K <https://github.com/drA1ex>
## Copyright (C) 2025, Sergei Rozhkov <https://github.com/ghzserg>
##
## This file may be distributed under the terms of the GNU GPLv3 license


MOD_CUSTOM_BOOT=0
source /opt/config/mod/.shell/common.sh

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
        
        if ! ps | grep -q "[n]l80211"; then
            echo "// Try to connect..."
            
            for _ in $(seq 5); do
                "$SCRIPTS/boot/wifi_connect.sh" 2>&1 | logged /data/logFiles/wifi.log --no-print --send-to-screen
                ret="${PIPESTATUS[0]}"
                
                if [ "$ret" -eq 0 ]; then
                    MOD_CUSTOM_BOOT=1
                    echo "// Connected!"
                    break
                fi
                
                echo "@@ WPA start failed. Retry..."
                sleep 1
            done
        fi
        
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

    echo "Killing processes..."
    killall wpa_supplicant
    killall udhcpc
    
    # shellcheck disable=SC2015
    ip link set eth0 up && udhcpc eth0 \
        || { echo "@@ Failed to initialize connection!"; return 1; }

    touch "$ETHERNET_CONNECTED_F"
    sync

    MOD_CUSTOM_BOOT=1
    echo "// Ethernet connection initialized with DHCP"
}

if [ "$DISPLAY_OFF" -eq 1 ]; then
    # Init Network
    
    echo "// Network initialization..."
    CONFIG_FILE=$(ls /opt/config/Adventurer5M*.json 2>/dev/null)
    
    if [ -f "$CONFIG_FILE" ]; then
        ETHERNET_STATUS=$(grep "ethernetStatus" < "$CONFIG_FILE" | sed 's/.*"ethernetStatus"[ ]*:[ ]*\([^,]*\).*/\1/')
        if [ "$ETHERNET_STATUS" = "true" ]; then
            ethernet_init
        else
            wifi_init
        fi
    else
        echo "@@ Config file not found"
    fi
fi

if [ "$MOD_CUSTOM_BOOT" -eq 1 ]; then
    echo "// Network initialized!"
    
    touch "$CUSTOM_BOOT_F"
    sync
    
    mkdir -p /dev/pts
    mount -t devpts devpts /dev/pts
    mount -t configfs none /sys/kernel/config -o rw,relatime
    mount -t debugfs none /sys/kernel/debug -o rw,relatime
    
    echo "// MCU booting..."
    /opt/config/mod/.bin/exec/boot_mcu 2>&1
    
    echo "// Start klipper."
    /opt/klipper/start.sh &> /dev/null
    
    echo "// Boot sequence done!"
elif [ "$DISPLAY_OFF" -eq 1 ]; then
    # If we're here, we can't initialize network connection
    # This means Feather is useless without network - skip it
    
    echo "?? Switch config to enabled screen..."
    /opt/config/mod/.shell/commands/zdisplay.sh stock --skip-reboot

    echo "@@ Failed to initialize mod. Booting into stock firmware..."
    suppress_slicer_nag
    sleep 1
else
    echo "// Booting stock firmware..."
    suppress_slicer_nag
fi
