#!/bin/bash

## Starting forge-x services
##
## Copyright (C) 2025, Alexander K <https://github.com/drA1ex>
## Copyright (C) 2025, Sergei Rozhkov <https://github.com/ghzserg>
##
## This file may be distributed under the terms of the GNU GPLv3 license

# Set HOME to /root to ensure git uses the correct .gitconfig
export HOME=/root

CFG_SCRIPT="/opt/config/mod/.shell/commands/zconf.sh"
VAR_PATH="/opt/config/mod_data/variables.cfg"

# TODO: remove in the next major release
[ -L /usr/bin/ip ] && rm -f /usr/bin/ip
[ -L /usr/bin/tc ] && rm -f /usr/bin/tc

sed -i '
  /"project_owner":"mainsail-crew","version":"v2\.14\.0"/ {
    s/"project_owner":"mainsail-crew"/"project_owner":"DrA1ex"/
    s/"version":"v2\.14\.0"/"version":"v2.13.2"/
  }
' /root/www/mainsail/release_info.json

sed -i 's/\("project_owner":"\)mainsail-crew\("\)/\1DrA1ex\2/' /root/www/mainsail/release_info.json

######

/opt/config/mod/.root/S45ntpd start

DISABLE_MOONRAKER=$("$CFG_SCRIPT" "$VAR_PATH" --get "disable_moonraker" "0")
if [ "$DISABLE_MOONRAKER" != "1" ]; then
    /opt/config/mod/.root/S65moonraker start

    started=0
    echo "Waiting for moonraker to start..."
    for _ in $(seq 0 30); do
        if curl -s http://localhost:7125 > /dev/null 2>&1; then
            started=1
            break
        fi
        sleep 1
    done

    [ "$started" = "1" ] && echo "OK" || echo "FAIL"
else
    echo "Moonraker service disabled as per configuration."
fi

DISABLE_WEB=$("$CFG_SCRIPT" "$VAR_PATH" --get "disable_web" "0")
if [ "$DISABLE_WEB" != "1" ]; then
    /opt/config/mod/.root/S70httpd start
else
    echo "Web services disabled as per configuration."
fi

DISPLAY_MODE=$("$CFG_SCRIPT" "$VAR_PATH" --get "display" "STOCK")
if [ "$DISPLAY_MODE" = "GUPPY" ]; then
    /opt/config/mod/.root/S35tslib start
    /opt/config/mod/.root/S80guppyscreen start
fi

if [ -d /etc/init.d ]; then
    echo "Starting user services..."
    
    while read -r file; do
        "$file" start
    done < <(find /etc/init.d/ -type f -name "S*")

    echo "Done"
fi

echo "Services started"
