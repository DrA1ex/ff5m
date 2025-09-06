#!/bin/bash

## Mod's parameters change handle
##
## Copyright (C) 2025, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license


source /opt/config/mod/.shell/common.sh

key=$1
value=$2

if [ -z "$key" ]; then
    echo "Usage: $0 <key> <value>"
    exit 1
fi

case "$key" in
    display)
        if [ "$value" = "STOCK" ]; then
            "$SCRIPTS"/commands/zdisplay.sh "stock"
        else
            message "Critical Notice." "!!"
            message "Do not disable the Stock Screen unless you fully understand:"  "!!"
            message " - Bed mesh calibration"  "!!"
            message " - Z-offset configuration"  "!!"
            message " - START_PRINT/END_PRINT macro behavior"  "!!"
            message " " " "
            message "Improper handling may cause printer damage. Review the documentation first:" "!!"
            message "https://github.com/DrA1ex/ff5m/blob/main/docs/SCREEN.md" "->"

            sleep 1
            
            if [ "$value" = "FEATHER" ]; then
                "$SCRIPTS"/commands/zdisplay.sh "feather"
            elif [ "$value" = "GUPPY" ]; then
                "$SCRIPTS"/commands/zdisplay.sh "guppy"
            else
                "$SCRIPTS"/commands/zdisplay.sh "headless"
            fi
        fi
    ;;
    
    use_swap)
        "$SCRIPTS"/boot/init_swap.sh
    ;;
    
    camera)
        if [ "$value" -eq 1 ]; then
            message "You can change camera parameters here: Configuration -> mod_data -> camera.conf"
            
            cam_pid_file="/run/camera.pid"
            ss -tuln | grep -q ":8080"; STREAM_ACTIVE=$(( $? == 0 ))
            [ -f "$cam_pid_file" ] && kill -0 "$(cat $cam_pid_file)" 2>/dev/null; MOD_CAM_ACTIVE=$(( $? == 0 ))
            
            if [ "$STREAM_ACTIVE" -eq 1 ] && [ "$MOD_CAM_ACTIVE" -eq 0 ]; then
                command "action:prompt_begin Camera"
                command 'action:prompt_text The camera is currently in use! Disable it in the Stock Screen settings and try again.'
                command "action:prompt_end"
                command "action:prompt_show"
                
                exit 1
            fi
            
            /etc/init.d/S98camera start
        else
            /etc/init.d/S98camera stop
        fi
    ;;
    
    tune_klipper)
        if "$SCRIPTS"/commands/ztune_klipper.sh "$value"; then
            message "Klipper was changed. Printer will reboot now"
            sleep 5
            reboot
        fi
    ;;
    
    zssh)
        if [ "$value" -eq 1 ]; then
            SSH_PUB=$( cat /opt/config/mod_data/ssh.pub.txt )
            
            message "You can change SSH parameters here: Configuration -> mod_data -> ssh.conf"
            message "Place the text one line below in ~/.ssh/authorized_keys for the specified user on the ssh server"
            message "${SSH_PUB}"
            message "In the authorized_keys file, remove the first 2 characters '# ' - this is a comment"
            
            /etc/init.d/S98zssh start
        else
            /etc/init.d/S98zssh stop
        fi
    ;;
    tune_config)
        if [ "$value" -eq 1 ]; then
            chroot "$MOD" /bin/python3 "$PY"/cfg_backup.py \
                --mode restore --avoid_writes --no_data                  \
                --config /opt/config/printer.cfg                         \
                --params /opt/config/mod/.cfg/tuning.cfg
        else
            chroot "$MOD" /bin/python3 "$PY"/cfg_backup.py \
                --mode restore --avoid_writes --no_data                  \
                --config /opt/config/printer.cfg                         \
                --params /opt/config/mod/.cfg/tuning.off.cfg
        fi

        message "Klipper will be restarted to apply changes."
        "$SCRIPTS"/restart_klipper.sh
    ;;
    power_loss_recovery)
        rm -f /opt/config/mod_data/resurrection.json

        screen=$("$CMDS"/zdisplay.sh test)
        if [ "$screen" != "STOCK" ]; then
            message "Klipper will be restarted to apply changes."
            "$SCRIPTS"/restart_klipper.sh
        fi
    ;;
esac