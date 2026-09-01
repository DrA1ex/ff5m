#!/bin/bash

## Stock printer identity detection.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

load_stock_printer_identity() {
    local version launcher line machine="" product_id=""

    version=$(cat /root/version 2>/dev/null) || return 1
    case "$version" in
        ''|*[!0-9A-Za-z._-]*) return 1 ;;
    esac

    launcher="/opt/PROGRAM/software/$version/auto_run.sh"
    [ -r "$launcher" ] || return 1

    while IFS= read -r line; do
        case "$line" in
            MACHINE=*) machine=${line#MACHINE=} ;;

            PID=*) product_id=${line#PID=} ;;
        esac
    done < "$launcher"

    case "$machine:$product_id" in
        Adventurer5M:0023|Adventurer5MPro:0024) ;;
        *) return 1 ;;
    esac

    FIRMWARE_MACHINE=$machine
    FIRMWARE_PRODUCT_ID=$product_id
}
