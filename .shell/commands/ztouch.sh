#!/bin/bash

## Start, stop, or apply the settings for the shared touch interface.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

source /opt/config/mod/.shell/common.sh || exit $?

case "$#:$1" in
    1:start|1:stop) action=$1 ;;

    1:apply)
        touch_mode=$("$CFG_SCRIPT" "$VAR_PATH" --get touch_interface AUTO) || exit $?
        case "$touch_mode" in
            ON) action=start ;;

            AUTO)
                display=$("$CMDS/zdisplay.sh" test 2> /dev/null) || exit $?
                action=stop
                if [[ "$display" = FEATHER || "$display" = GUPPY ]]; then
                    action=start
                fi
            ;;

            *)
                echo "Invalid touch_interface: $touch_mode" >&2
                exit 1
            ;;
        esac
    ;;

    *)
        echo "Usage: $0 {start|stop|apply}" >&2
        exit 1
    ;;
esac

exec chroot "$MOD" /opt/config/mod/.root/S35tslib "$action"
