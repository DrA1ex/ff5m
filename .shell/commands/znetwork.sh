#!/bin/sh

## Lightweight network control for the interactive Feather screen.
## This script intentionally uses only BusyBox/POSIX tools available on AD5M.

# Klipper's service environment does not include the sbin directories. Network
# tools exist there on the stock image, so use an explicit deterministic PATH.
PATH=/sbin:/usr/sbin:/bin:/usr/bin
export PATH

SCRIPTS=/opt/config/mod/.shell
MOD_DATA=/opt/config/mod_data
MODE_FILE=$MOD_DATA/network_mode
WPA_CONFIG=/etc/wpa_supplicant.conf
WPA_BACKUP=/etc/wpa_supplicant.conf.feather-stock
WIFI_MARKER=/tmp/wifi_connected_f
ETHERNET_MARKER=/tmp/ethernet_connected_f
IP_FILE=/tmp/net_ip

. "$SCRIPTS/network_common.sh"

error() {
    echo "ERROR=$1"
    exit 1
}

save_mode() {
    mode=$1
    mkdir -p "$MOD_DATA"
    tmp=$MODE_FILE.$$
    printf '%s\n' "$mode" > "$tmp" || return 1
    mv -f "$tmp" "$MODE_FILE"
}

sync_vendor_mode() {
    enabled=$1
    for config in /opt/config/Adventurer5M*.json; do
        [ -f "$config" ] || continue
        tmp=$config.feather.$$
        sed "s/\(\"ethernetStatus\"[ ]*:[ ]*\)\(true\|false\)/\1$enabled/" \
            "$config" > "$tmp" && mv -f "$tmp" "$config"
        rm -f "$tmp"
    done
}

update_ip() {
    ip_address=$(network_ipv4 "$1")
    if [ -n "$ip_address" ]; then
        printf '%s\n' "$ip_address" > "$IP_FILE"
        echo "IP=$ip_address"
        return 0
    fi
    rm -f "$IP_FILE"
    return 1
}

load_wifi_driver() {
    if ! ip link show wlan0 >/dev/null 2>&1; then
        insmod /lib/modules/8821cu.ko >/dev/null 2>&1 || true
        modprobe 8821cu >/dev/null 2>&1 || true
    fi
    ip link show wlan0 >/dev/null 2>&1 || error "Wi-Fi adapter is unavailable"
    ip link set wlan0 up >/dev/null 2>&1 || error "Unable to enable Wi-Fi adapter"
}

ensure_wpa_for_scan() {
    load_wifi_driver
    if ! wpa_cli -i wlan0 ping 2>/dev/null | grep -q '^PONG$'; then
        config=$WPA_CONFIG
        if [ ! -f "$config" ]; then
            config=/tmp/feather-wpa-scan.conf
            umask 077
            {
                echo 'ctrl_interface=DIR=/var/run/wpa_supplicant'
                echo 'update_config=1'
            } > "$config"
        fi
        killall wpa_supplicant >/dev/null 2>&1 || true
        sleep 1
        rm -f /var/run/wpa_supplicant/wlan0
        wpa_supplicant -B -i wlan0 -c "$config" >/dev/null 2>&1 \
            || error "Unable to start Wi-Fi scanner"
    fi
}

scan_wifi() {
    ensure_wpa_for_scan
    wpa_cli -i wlan0 scan >/dev/null 2>&1 || error "Wi-Fi scan failed"
    result=/tmp/feather-wifi-scan.$$
    trap 'rm -f "$result"' EXIT HUP INT TERM
    for unused in 1 2 3 4 5 6 7 8; do
        sleep 1
        wpa_cli -i wlan0 scan_results 2>/dev/null | awk -F '\t' '
            NR > 1 && NF >= 5 && $5 != "" {
                print "NETWORK\t" $5 "\t" $3 "\t" $4
            }
        ' > "$result"
        if [ -s "$result" ]; then
            cat "$result"
            return 0
        fi
    done
    error "Wi-Fi radio returned no scan results"
}

connect_wifi() {
    credentials=$1
    case "$credentials" in
        /tmp/feather-wifi-*) ;;
        *) error "Invalid credentials file" ;;
    esac
    [ -f "$credentials" ] || error "Credentials file is missing"
    candidate=/tmp/feather-wpa-active.conf
    trap 'rm -f "$credentials" "$new_config" "$candidate"' EXIT HUP INT TERM

    IFS= read -r ssid < "$credentials" || error "Unable to read SSID"
    password=$(sed -n '2p' "$credentials")
    rm -f "$credentials"
    [ -n "$ssid" ] || error "SSID is empty"
    password_length=${#password}
    if [ "$password_length" -lt 8 ] || [ "$password_length" -gt 64 ]; then
        error "Wi-Fi password must contain 8-63 characters or 64 hex digits"
    fi

    escaped_ssid=$(printf '%s' "$ssid" | sed 's/\\/\\\\/g; s/"/\\"/g')
    escaped_password=$(printf '%s' "$password" | sed 's/\\/\\\\/g; s/"/\\"/g')
    if [ "$password_length" -eq 64 ]; then
        printf '%s' "$password" | grep -q '^[0-9A-Fa-f][0-9A-Fa-f]*$' \
            || error "A 64-character Wi-Fi key must be hexadecimal"
        psk_line="psk=$password"
    else
        psk_line="psk=\"$escaped_password\""
    fi
    password=
    escaped_password=

    if [ ! -f "$WPA_BACKUP" ] && [ -f "$WPA_CONFIG" ]; then
        cp "$WPA_CONFIG" "$WPA_BACKUP" || error "Unable to save Wi-Fi recovery backup"
        chmod 600 "$WPA_BACKUP"
    fi
    umask 077
    rm -f "$candidate"
    {
        echo 'ctrl_interface=DIR=/var/run/wpa_supplicant'
        echo 'update_config=1'
        echo
        echo 'network={'
        printf '\tssid="%s"\n' "$escaped_ssid"
        printf '\t%s\n' "$psk_line"
        printf '\tkey_mgmt=WPA-PSK\n'
        echo '}'
    } > "$candidate" || error "Unable to write Wi-Fi configuration"
    chmod 600 "$candidate"

    load_wifi_driver
    if ! "$SCRIPTS/boot/wifi_connect.sh" "$candidate" >/dev/null 2>&1; then
        killall wpa_cli >/dev/null 2>&1 || true
        killall wpa_supplicant >/dev/null 2>&1 || true
        network_deactivate_interface wlan0
        error "Unable to connect to selected Wi-Fi network"
    fi
    update_ip wlan0 || error "Wi-Fi connected but DHCP did not provide an address"

    # Commit credentials only after WPA association and DHCP both succeeded.
    # Keep the runtime config path as a symlink so a later wpa_cli reconfigure
    # still resolves to the newly installed persistent file.
    new_config=$WPA_CONFIG.feather.$$
    cp "$candidate" "$new_config" || error "Unable to stage Wi-Fi configuration"
    chmod 600 "$new_config"
    mv -f "$new_config" "$WPA_CONFIG" || error "Unable to install Wi-Fi configuration"
    new_config=
    rm -f "$candidate"
    ln -s "$WPA_CONFIG" "$candidate" || error "Unable to retain active Wi-Fi configuration"
    candidate=

    save_mode WIFI || error "Unable to save network mode"
    sync_vendor_mode false
    # Persistence is committed before the old route is removed.  If the
    # helper is interrupted after this point, the next boot still selects the
    # successfully configured Wi-Fi connection.
    network_deactivate_interface eth0
    rm -f "$ETHERNET_MARKER"
    touch "$WIFI_MARKER"
    killall wpa_cli >/dev/null 2>&1 || true
    wpa_cli -B -a "$SCRIPTS/boot/wifi_reconnect.sh" -i wlan0 >/dev/null 2>&1 || true
}

use_ethernet() {
    rm -f "$IP_FILE"
    # Bring the requested connection up first.  If DHCP fails, the existing
    # Wi-Fi connection and route are left untouched.
    network_activate_dhcp eth0 25 || error "Ethernet DHCP failed"
    update_ip eth0 || error "Ethernet connected but no address is available"
    save_mode ETHERNET || error "Unable to save network mode"
    sync_vendor_mode true
    killall wpa_cli >/dev/null 2>&1 || true
    killall wpa_supplicant >/dev/null 2>&1 || true
    network_deactivate_interface wlan0
    rm -f "$WIFI_MARKER"
    touch "$ETHERNET_MARKER"
}

status() {
    configured_mode=""
    [ -f "$MODE_FILE" ] && configured_mode=$(head -n 1 "$MODE_FILE")
    case "$configured_mode" in
        WIFI|ETHERNET) ;;
        *) configured_mode="" ;;
    esac

    if [ "$configured_mode" = "ETHERNET" ] || [ -f "$ETHERNET_MARKER" ]; then
        echo 'MODE=ETHERNET'
        echo 'SSID='
        echo 'SIGNAL='
        update_ip eth0 || echo 'IP='
    elif [ "$configured_mode" = "WIFI" ] || [ -f "$WIFI_MARKER" ]; then
        echo 'MODE=WIFI'
        ssid=$(wpa_cli -i wlan0 status 2>/dev/null | sed -n 's/^ssid=//p' | sed -n '1p')
        signal=$(wpa_cli -i wlan0 signal_poll 2>/dev/null | sed -n 's/^RSSI=//p' | sed -n '1p')
        printf 'SSID=%s\n' "$ssid"
        printf 'SIGNAL=%s\n' "$signal"
        update_ip wlan0 || echo 'IP='
    else
        echo 'MODE=OFFLINE'
        echo 'SSID='
        echo 'SIGNAL='
        echo 'IP='
    fi
}

case "$1" in
    status) status ;;
    scan) scan_wifi ;;
    connect-wifi) connect_wifi "$2" ;;
    use-ethernet) use_ethernet ;;
    *) error "Usage: znetwork.sh status|scan|connect-wifi <credentials>|use-ethernet" ;;
esac
