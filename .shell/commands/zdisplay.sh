#!/bin/bash

## Display configuration script
##
## Copyright (C) 2025, Alexander K <https://github.com/drA1ex>
## Copyright (C) 2025, Sergei Rozhkov <https://github.com/ghzserg>
##
## This file may be distributed under the terms of the GNU GPLv3 license

source /opt/config/mod/.shell/common.sh

display_stock() {
    chroot "$MOD" /bin/python3 "$PY"/cfg_backup.py \
        --mode restore --avoid_writes \
        --config /opt/config/printer.cfg \
        --no_data \
        --params /opt/config/mod/.cfg/init.display.stock.cfg
}

display_feather() {
    chroot "$MOD" /bin/python3 "$PY"/cfg_backup.py \
        --mode restore --avoid_writes \
        --config /opt/config/printer.cfg \
        --no_data \
        --params /opt/config/mod/.cfg/init.display.feather.cfg
}

display_headless() {
    chroot "$MOD" /bin/python3 "$PY"/cfg_backup.py \
        --mode restore --avoid_writes \
        --config /opt/config/printer.cfg \
        --no_data \
        --params /opt/config/mod/.cfg/init.display.headless.cfg
}

display_guppy() {
    chroot "$MOD" /bin/python3 "$PY"/cfg_backup.py \
        --mode restore --avoid_writes \
        --config /opt/config/printer.cfg \
        --no_data \
        --params /opt/config/mod/.cfg/init.display.guppy.cfg
}

test() {
    local display_off=$("$CMDS"/zconf.sh "$VAR_PATH" --get "display_off" "MISSING")

    if [ "$display_off" != "MISSING" ]; then
        [ "$display_off" = "0" ] && echo "STOCK" || echo "FEATHER"
    else
        local display=$("$CMDS"/zconf.sh "$VAR_PATH" --get "display" "STOCK")
        echo "$display"
    fi
}

check_and_fix_eth0_mac() {
    # Check if eth0 interface exists and is configured
    if ! ifconfig eth0 &>/dev/null; then
        return 0  # eth0 doesn't exist, nothing to do
    fi
    
    local interfaces_file="/etc/network/interfaces"
    local mac_addr=""
    
    # Get MAC address from ifconfig
    mac_addr=$(ifconfig eth0 2>/dev/null | awk '/HWaddr/ {print $5}' | head -1)
    
    # If we couldn't get MAC address, exit
    if [ -z "$mac_addr" ]; then
        echo "Warning: Could not determine MAC address for eth0" >&2
        return 1
    fi
    
    # Check if interfaces file exists
    if [ ! -f "$interfaces_file" ]; then
        echo "Warning: $interfaces_file not found" >&2
        return 1
    fi
    
    # Check if there's an eth0 block in interfaces file
    if ! grep -q "iface.*eth0" "$interfaces_file"; then
        # No eth0 block found, create one
        echo "Adding eth0 configuration block to $interfaces_file..."
        
        # Create backup
        cp "$interfaces_file" "${interfaces_file}.backup.$(date +%s)"
        
        # Add eth0 block at the end of file
        echo "" >> "$interfaces_file"
        echo "auto eth0" >> "$interfaces_file"
        echo "iface eth0 inet dhcp" >> "$interfaces_file"
        echo "    hwaddress ether $mac_addr" >> "$interfaces_file"
        
        echo "eth0 configuration block added with MAC address $mac_addr"
        return 0
    fi
    
    # Check if MAC address is already configured for eth0
    local eth0_section_start=$(grep -n "iface.*eth0" "$interfaces_file" | head -1 | cut -d: -f1)
    if [ -z "$eth0_section_start" ]; then
        return 0
    fi
    
    # Find the end of eth0 section (next iface line or end of file)
    local eth0_section_end=$(tail -n +$((eth0_section_start + 1)) "$interfaces_file" | grep -n "iface" | head -1 | cut -d: -f1)
    if [ -n "$eth0_section_end" ]; then
        eth0_section_end=$((eth0_section_start + eth0_section_end - 1))
    else
        eth0_section_end=$(wc -l < "$interfaces_file")
    fi
    
    # Check if MAC address (hwaddress ether or address ether) is already present in eth0 section
    local eth0_content=$(sed -n "${eth0_section_start},${eth0_section_end}p" "$interfaces_file")
    
    if echo "$eth0_content" | grep -q "address.*ether" || echo "$eth0_content" | grep -q "hwaddress.*ether"; then
        # MAC address already configured
        return 0
    fi
    
    # Add MAC address to eth0 section
    echo "Adding MAC address $mac_addr to eth0 configuration..."
    
    # Create backup
    cp "$interfaces_file" "${interfaces_file}.backup.$(date +%s)"
    
    # Add the MAC address line after the iface line
    sed -i "${eth0_section_start}a\\    hwaddress ether $mac_addr" "$interfaces_file"
    
    echo "MAC address $mac_addr added to eth0 configuration in $interfaces_file"
    
    return 0
}


apply_display_off() {
    killall "ffstartup-arm" &> /dev/null
    killall "firmwareExe" &> /dev/null
    
    # Stop Guppy services if they are running
    chroot "$MOD" /opt/config/mod/.root/guppyscreen stop
    
    if ip addr show wlan0 | grep -q "inet "; then
        killall "wpa_cli" &> /dev/null
        wpa_cli -B -a "$SCRIPTS/boot/wifi_reconnect.sh" -i wlan0
        touch "$WIFI_CONNECTED_F"
    elif ip addr show eth0 | grep -q "inet "; then
        check_and_fix_eth0_mac
        touch "$ETHERNET_CONNECTED_F"
    fi

    IP="$(ip addr show wlan0 2> /dev/null | awk '/inet / {print $2}' | cut -d'/' -f1)"
    [ -z "$IP" ] && IP="$(ip addr show eth0 2> /dev/null | awk '/inet / {print $2}' | cut -d'/' -f1)"
    [ -n "$IP" ] && echo "$IP" > "$NET_IP_F"
    
    "$SCRIPTS"/screen.sh backlight 0
    "$SCRIPTS"/screen.sh draw_splash
    "$SCRIPTS"/screen.sh backlight 100
    
    /etc/init.d/S00init reload
    echo "// Restarting Klipper..." | logged --no-log --send-to-screen
    
    "$SCRIPTS"/restart_klipper.sh --hard
    
    return 0
}

case "$1" in
    stock)
        display_stock
        sync
        
        if [ "$2" != "--skip-reboot" ]; then
            echo "Printer will be rebooted in 5 seconds..."
            echo "RESPOND prefix='//' MSG='Printer will be rebooted in 5 seconds...'" > /tmp/printer
            
            { sleep 5 && reboot; } &>/dev/null &
        fi
        
        exit 0
    ;;
    
    feather)
        display_feather
        apply_display_off
    ;;

    headless)
        display_headless
        apply_display_off
    ;;

    guppy)
        display_guppy    
        apply_display_off

        # Start Guppy services
        chroot "$MOD" /opt/config/mod/.root/guppyscreen start
    ;;
    
    apply)
        if [ "$(test)" != "STOCK" ]; then
            echo "Turning off Stock screen..."
            apply_display_off
        fi
    ;;
    
    test)
        result="$(test)"
        echo "Display: $result" 1>&2 
        
        echo "$result"
    ;;
    
    *)
        echo "Usage: $0 stock|feather|headless|guppy|test"; exit 1;
    ;;
esac

exit $?
