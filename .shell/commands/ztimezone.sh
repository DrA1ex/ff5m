#!/bin/sh

## Validate and atomically apply the system timezone.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

PATH=/sbin:/usr/sbin:/bin:/usr/bin
export PATH

ZONEINFO_ROOT=${ZONEINFO_ROOT:-/usr/share/zoneinfo}
LOCALTIME_PATH=${LOCALTIME_PATH:-/etc/localtime}

error() {
    echo "ERROR=$1"
    exit 1
}

[ "$#" -eq 1 ] || error "Usage: SET_TIMEZONE ZONE=Area/City"
zone=$1

case "$zone" in
    ""|/*|.|..|../*|*/..|*/../*|*//*|*[!A-Za-z0-9_+./-]*)
        error "Invalid timezone name"
        ;;
esac

root=$(readlink -f "$ZONEINFO_ROOT") ||
    error "Timezone database is unavailable"
source_path=$(readlink -f "$ZONEINFO_ROOT/$zone") ||
    error "Unknown timezone: $zone"

case "$source_path" in
    "$root"/*) ;;
    *) error "Timezone resolves outside the timezone database" ;;
esac
[ -f "$source_path" ] || error "Unknown timezone: $zone"

temporary="${LOCALTIME_PATH}.forge-x.$$"
trap 'rm -f "$temporary"' EXIT HUP INT TERM
ln -s "$source_path" "$temporary" ||
    error "Unable to create timezone link"
mv -f "$temporary" "$LOCALTIME_PATH" ||
    error "Unable to install timezone"
trap - EXIT HUP INT TERM

echo "TIMEZONE=$zone"
date
