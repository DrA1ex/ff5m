#!/bin/bash

## Starting forge-x services
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
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

# This script runs inside the Forge-X chroot. Replace its bundled sudo file;
# Moonraker prefixes machine actions with sudo even though it runs as root.
ln -fns /opt/config/mod/.root/sudo-shim /usr/bin/sudo

sed -i '
  /"project_owner":"mainsail-crew","version":"v2\.14\.0"/ {
    s/"project_owner":"mainsail-crew"/"project_owner":"DrA1ex"/
    s/"version":"v2\.14\.0"/"version":"v2.13.2"/
  }
' /root/www/mainsail/release_info.json

sed -i 's/\("project_owner":"\)mainsail-crew\("\)/\1DrA1ex\2/' /root/www/mainsail/release_info.json

# Temporary compatibility for older installed Buildroot images.
# Remove when those images no longer need the stream proxy rules added here.
for stream_proxy in webcam:8080 screen:8081; do
    if ! grep -q "^[[:space:]]*P:/${stream_proxy%:*}/:" /etc/httpd.conf; then
        printf '\nP:/%s/:localhost:%s/\n' "${stream_proxy%:*}" "${stream_proxy#*:}" >> /etc/httpd.conf
    fi
done

######

DISPLAY_MODE=$("$CFG_SCRIPT" "$VAR_PATH" --get "display" "FEATHER")
/opt/config/mod/.root/S45ntpd start

# Waits until the Moonraker API answers; fails after 30 seconds.
wait_moonraker() {
    echo "Waiting for moonraker to start..."
    for _ in $(seq 0 30); do
        if curl -s http://localhost:7125 > /dev/null 2>&1; then
            echo "OK"
            return 0
        fi
        sleep 1
    done

    echo "FAIL"
    return 1
}

# TODO: remove in the next major release
# Older releases shipped the camera as a read-only [webcam cam] section. The 4gx_camera entry replaces it.
USER_MOONRAKER_CONF="/opt/config/mod_data/user.moonraker.conf"

has_legacy_camera() { grep -q '^[[:space:]]*\[webcam cam\]' "$USER_MOONRAKER_CONF" 2>/dev/null; }

comment_out_legacy_camera() {
    sed -i '/^[[:space:]]*\[webcam cam\]/,/^[[:space:]]*\[[^]]*\]/ {
        /^[[:space:]]*\[webcam cam\]/! { /^[[:space:]]*\[/ b
        }
        /^[[:space:]]*$/ b
        /^[[:space:]]*#/ b
        s/^/# /
    }' "$USER_MOONRAKER_CONF"
}

DISABLE_MOONRAKER=$("$CFG_SCRIPT" "$VAR_PATH" --get "disable_moonraker" "0")
if [ "$DISABLE_MOONRAKER" != "1" ]; then
    /opt/config/mod/.root/S65moonraker start

    started=0
    wait_moonraker && started=1

    # Moonraker has parsed the old camera by now: copy its settings to the editable 4gx_camera entry, then comment the
    # section out and restart Moonraker once: it keeps the section it has parsed until then, so the old camera would
    # stay in the list. Keep the section if that fails or the migration has not created the entry yet; the next boot
    # tries again.
    if [ "$started" = "1" ] && has_legacy_camera; then
        if /bin/python3 -B - 2>/dev/null <<'EOF'
import sys
sys.path.insert(0, '/opt/config/mod/.py')
from set_webcams import api_request

webcams = {cam['name']: cam for cam in api_request('GET', '/server/webcams/list')['webcams']}
old, new = webcams['cam'], webcams['4gx_camera']
api_request('POST', '/server/webcams/item', {**old, 'uid': new['uid'], 'name': new['name'], 'enabled': new['enabled']})
EOF
        then
            comment_out_legacy_camera

            echo "Restarting moonraker to drop the old camera..."
            /opt/config/mod/.root/S65moonraker restart
            wait_moonraker
        else
            echo "Old camera settings were not copied; the next boot tries again."
        fi
    fi
else
    echo "Moonraker service disabled as per configuration."

    # Nothing can copy the old camera's settings now, and Moonraker would list it next to the new entry once it is
    # enabled: comment the section out. Anyone who needs those settings adds them to the entry.
    if has_legacy_camera; then
        comment_out_legacy_camera
    fi
fi

DISABLE_WEB=$("$CFG_SCRIPT" "$VAR_PATH" --get "disable_web" "0")
if [ "$DISABLE_WEB" != "1" ]; then
    /opt/config/mod/.root/S70httpd start
else
    echo "Web services disabled as per configuration."
fi

if [ "$DISPLAY_MODE" = "GUPPY" ]; then
    /opt/config/mod/.root/guppyscreen start
fi

if [ -d /etc/init.d ]; then
    echo "Starting user services..."
    
    while read -r file; do
        "$file" start
    done < <(find /etc/init.d/ -type f -name "S*")

    echo "Done"
fi

echo "Services started"
