#!/bin/bash
# Report whether Moonraker is rendering a timelapse video.
#
# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

CURL=$(find /opt/cloud/curl-*/bin/curl 2> /dev/null)
[ -n "$CURL" ] || exit 1

status=$("$CURL" --silent --fail --connect-timeout 1 --max-time 1 \
    http://127.0.0.1:7125/machine/timelapse/lastframeinfo) || exit 1

grep -Eq '"rendering"[[:space:]]*:[[:space:]]*true' <<< "$status"
