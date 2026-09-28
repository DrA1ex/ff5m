#!/bin/bash
# Return 0 while timelapse work is active, 1 when idle, 2 if status is unknown.
#
# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

CURL=$(find /opt/cloud/curl-*/bin/curl 2> /dev/null)
[ -n "$CURL" ] || exit 2

status=$("$CURL" --silent --fail --connect-timeout 1 --max-time 1 \
    http://127.0.0.1:7125/machine/timelapse/lastframeinfo) || exit 2

if grep -Eq '"busy"[[:space:]]*:[[:space:]]*true' <<< "$status"; then
    exit 0
fi
if grep -Eq '"busy"[[:space:]]*:[[:space:]]*false' <<< "$status"; then
    exit 1
fi
exit 2
