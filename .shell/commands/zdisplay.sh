#!/bin/bash

## Display configuration script
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
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
    local display_mode
    local stock_owner=0
    display_mode="$(test)"
    pidof firmwareExe >/dev/null 2>&1 && stock_owner=1

    killall "ffstartup-arm" &> /dev/null
    killall "firmwareExe" &> /dev/null
    
    # Stop Guppy services if they are running
    chroot "$MOD" /opt/config/mod/.root/guppyscreen stop

    # Start the selected alternative display before restarting Klipper.  This
    # function may be invoked by Klipper through SET_MOD; anything placed after
    # the hard restart is not guaranteed to run because the caller is stopped.
    case "$display_mode" in
        FEATHER)
            # Feather only needs tslib's calibrated uinput device.
            chroot "$MOD" /opt/config/mod/.root/S35tslib start
        ;;
        GUPPY)
            # Guppy owns its UI process and shares the same tslib device.
            chroot "$MOD" /opt/config/mod/.root/guppyscreen start
        ;;
    esac
    
    # Stock -> Feather is the only path that asks a newly started daemon to
    # migrate the live vendor link. Between non-Stock modes the existing daemon
    # simply keeps running.
    if ! pidof netd >/dev/null 2>&1; then
        rm -f /run/netd.sock
        if [ "$display_mode" = "FEATHER" ] && [ "$stock_owner" -eq 1 ]; then
            start-stop-daemon -Sb --exec "$(command -v netd)" -- --migrate-existing
        else
            start-stop-daemon -Sb --exec "$(command -v netd)" --
        fi
    fi
    
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
