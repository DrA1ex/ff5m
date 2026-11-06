#!/bin/bash

## Bring the touch input, the screen stream, and the web interface entries in line with the parameters.
##
##   touch    start or stop the shared touch input; run at boot before the UI starts and after `touch_interface` changes
##   apply    touch, restart the screen stream, update the entries; run after `display` or `screen_stream` changes
##   webcams  update the entries only; run at boot, once the services have started, and after `camera` changes
##
## screen_policy.sh decides what must run; S98camera, S98screen and S35tslib own their processes. A `camera`
## change is applied by zchanges.sh, which first checks that the stock camera is not running.
##
## The `enabled` flag of the 4gx_camera and 4gx_screen entries always follows the parameters, so every run
## checks it and the next run corrects a missed update.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

source /opt/config/mod/.shell/common.sh
source "$SCRIPTS/screen_policy.sh"

apply_touch() {
    if [ "$SCREEN_TOUCH_ENABLED" -eq 1 ]; then
        chroot "$MOD" /opt/config/mod/.root/S35tslib start
    else
        chroot "$MOD" /opt/config/mod/.root/S35tslib stop
    fi
}

# The entries are a convenience of the web interface: a failure is reported but never fails the caller.
update_webcams() {
    # Without Moonraker there is no web interface whose entries could be updated.
    local moonraker_disabled=$("$CFG_SCRIPT" "$VAR_PATH" --get "disable_moonraker" "0")
    if [ "$moonraker_disabled" = "1" ]; then
        return 0
    fi

    local camera=$("$CFG_SCRIPT" "$VAR_PATH" --get camera 0)

    chroot "$MOD" /bin/python3 "$PY/set_webcams.py" --camera "$camera" --screen "$SCREEN_STREAM_ENABLED" "$@" \
        || echo "Camera entries were not updated; they are corrected by the next change or reboot."
    return 0
}

command=$1
shift

case "$command" in
    touch)
        screen_policy || exit $?
        apply_touch
    ;;

    apply)
        screen_policy || exit $?
        apply_touch || exit $?
        /etc/init.d/S98screen restart || echo "Screen stream failed to start. Details: /data/logFiles/streamer.log" >&2
        update_webcams
    ;;

    webcams)
        screen_policy || exit $?
        update_webcams "$@"
    ;;

    *)
        echo "Usage: $0 {touch|apply|webcams [--wait SECONDS]}"
        exit 1
esac
