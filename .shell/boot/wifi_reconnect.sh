#!/bin/bash

## Wi-Fi state change handling script
##
## Copyright (C) 2025, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

source /opt/config/mod/.shell/common.sh

handle() {
    case "$2" in
        CONNECTED)
            echo "Connection Established";
            touch "$WIFI_CONNECTED_F"
        ;;
        DISCONNECTED)
            echo "?? Connection Lost";
            rm -f "$WIFI_CONNECTED_F"
        ;;
        *)
            echo "Received Wi-Fi signal: " "$@"
        ;;
    esac
}

handle "$@" 2>&1 | logged "/data/logFiles/wifi.log"
