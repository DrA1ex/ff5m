#!/bin/bash

##
## Stock FlashForge TCP command wrapper
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

# Send stock FlashForge TCP commands using the shared FlashForge API client.
unset LD_PRELOAD
unset LD_LIBRARY_PATH

API_SCRIPT=/root/printer_data/py/flashforge_api.py

if [ "$#" -eq 1 ]; then
    exec /usr/bin/python "$API_SCRIPT" send "$1"
fi

if [ "$#" -eq 2 ]; then
    file="/data/$2"

    # The stock firmware requires nozzle and bed heating commands in print files.
    M109=$(head -n 1000 "$file" | grep '^M109' | head -1)
    [ -z "$M109" ] && M109=$(head -n 1000 "$file" | grep '^M104' | head -1)
    M190=$(head -n 1000 "$file" | grep '^M190' | head -1)
    [ -z "$M190" ] && M190=$(head -n 1000 "$file" | grep '^M140' | head -1)

    if [ -z "$M109" ] || [ -z "$M190" ]; then
        echo "RESPOND TYPE=error MSG=\"Commands for heating the bed (M140/M190) or nozzle (M104/M109) were not found in the file $2.\"" > /tmp/printer
        echo "RESPOND TYPE=error MSG=\"Without these commands Stock Firmware will not print a file.\"" > /tmp/printer
        exit 1
    fi

    exec /usr/bin/python "$API_SCRIPT" send-file "$2"
fi

echo 'RESPOND TYPE=error MSG="Invalid number of arguments"' >/tmp/printer
exit 1
