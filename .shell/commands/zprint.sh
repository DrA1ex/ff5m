#!/bin/bash

##
## Stock FlashForge print file preparation and LAN API wrapper
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

# Prepare local G-code for a stock-screen print, then use the LAN API.
API_SCRIPT=/root/printer_data/py/flashforge_api.py

if [ "$#" -ne 2 ]; then
    echo "Usage: $0 PRINT|CLOSE FILE" >&2
    exit 1
fi

unset LD_PRELOAD
unset LD_LIBRARY_PATH

case "$1" in
    CLOSE)
        exec /usr/bin/python "$API_SCRIPT" close
        ;;
    PRINT)
        file="/data/$2"
        if [ ! -f "$file" ]; then
            echo "Print file not found: $file" >&2
            exit 1
        fi

        # Restore object definitions for EXCLUDE_OBJECT before the print.
        {
            echo 'EXCLUDE_OBJECT_DEFINE RESET=1'
            head -n 1000 "$file" | grep '^EXCLUDE_OBJECT_DEFINE' || true
        } >/tmp/printer

        # Stock firmware needs both nozzle and bed heating commands.
        if ! head -n 1000 "$file" | grep -qE '^M(109|104)' || \
           ! head -n 1000 "$file" | grep -qE '^M(190|140)'; then
            echo "Missing nozzle (M104/M109) or bed (M140/M190) heating command in $file" >&2
            echo 'RESPOND TYPE=error MSG="Missing nozzle or bed heating command in print file"' >/tmp/printer
            exit 1
        fi

        exec /usr/bin/python "$API_SCRIPT" print "$2"
        ;;
    *)
        echo "Usage: $0 PRINT|CLOSE FILE" >&2
        exit 1
        ;;
esac
