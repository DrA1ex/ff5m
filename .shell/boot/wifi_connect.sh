#!/bin/bash

## Wi-fi connecting script
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

source /opt/config/mod/.shell/common.sh
source /opt/config/mod/.shell/network_common.sh

INTERFACE="wlan0"
WPA_SUPPLICANT_CONF="${1:-/etc/wpa_supplicant.conf}"

[ -f "$WPA_SUPPLICANT_CONF" ] || {
    echo "@@ Wi-Fi configuration is missing." >&2
    exit 1
}

echo "Killing processes..."
killall wpa_cli >/dev/null 2>&1 || true
killall wpa_supplicant >/dev/null 2>&1 || true
network_stop_udhcpc "$INTERFACE"

rm -f "/var/run/wpa_supplicant/$INTERFACE"

echo "Restarting interface..."
network_clear_interface "$INTERFACE"
ip link set "$INTERFACE" down
ip link set "$INTERFACE" up

echo "Restarting wpa_supplicant service..."
if ! wpa_supplicant -B -i "$INTERFACE" -c "$WPA_SUPPLICANT_CONF"; then
    echo "@@ Failed to restart wpa_supplicant." >&2
    exit 1
fi

echo "Initialize network..."

if ! wpa_cli -i "$INTERFACE" enable_network all >/dev/null; then
    echo "@@ Failed to enable configured Wi-Fi networks." >&2
    exit 1
fi

echo "Initialize Wi-Fi connection..."
for _ in $(seq 22); do
    STATUS=$(wpa_cli -i "$INTERFACE" status 2>/dev/null \
        | awk -F= '$1 == "wpa_state" {print $2; exit}')

    case "$STATUS" in
        COMPLETED)
            echo "// Successfully connected!"
            echo "// Requesting DHCP..."

            if network_activate_dhcp "$INTERFACE" 12; then
                exit 0
            fi

            echo "@@ Wi-Fi connected, but DHCP failed." >&2
            exit 1
        ;;
        SCANNING)
            echo "Connecting..."
        ;;
        AUTHENTICATING|ASSOCIATING|ASSOCIATED|4WAY_HANDSHAKE|GROUP_HANDSHAKE)
            echo "?? Still connecting... Current status: $STATUS"
        ;;
        *)
            echo "@@ Waiting for Wi-Fi. Current status: ${STATUS:-UNKNOWN}"
        ;;
    esac

    sleep 1
done

echo "@@ Timed out while connecting to Wi-Fi." >&2
exit 1
