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


apply_display_off() {
    killall "ffstartup-arm" &> /dev/null
    killall "firmwareExe" &> /dev/null
    
    # Stop Guppy services if they are running
    chroot "$MOD" /opt/config/mod/.root/S80guppyscreen stop &> /dev/null
    chroot "$MOD" /opt/config/mod/.root/S35tslib stop &> /dev/null
    
    if ip addr show wlan0 | grep -q "inet "; then
        killall "wpa_cli" &> /dev/null
        wpa_cli -B -a "$SCRIPTS/boot/wifi_reconnect.sh" -i wlan0
        touch "$WIFI_CONNECTED_F"
    elif ip addr show eth0 | grep -q "inet "; then
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
        chroot "$MOD" /opt/config/mod/.root/S35tslib start
        chroot "$MOD" /opt/config/mod/.root/S80guppyscreen start
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
