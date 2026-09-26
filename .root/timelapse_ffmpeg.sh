#!/bin/sh

## Run the printer's stock encoder at low scheduling priority.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

FFMPEG=/opt/ffmpeg-4.0.2/bin/ffmpeg
LOADER=/opt/stock-lib/ld-linux.so.3
if [ ! -x "$FFMPEG" ] || [ ! -e "$LOADER" ]; then
    echo "The stock FFmpeg is unavailable in the Moonraker chroot" >&2
    exit 127
fi

LIBRARIES=/opt/ffmpeg-4.0.2/lib:/opt/x264/lib:/opt/stock-lib:/opt/stock-usr-lib
# The printer regression holds one render here to start a print while the
# previous timelapse is still encoding. The runner creates and removes this
# flag; /tmp is shared with Klipper and cleared on reboot, and the bound keeps
# a flag left by an interrupted runner from blocking renders for long.
RENDER_HOLD=/tmp/feather-ui-test-timelapse-hold
case "$*" in
    *frame%06d.jpg*)
        waited=0
        while [ -e "$RENDER_HOLD" ] && [ "$waited" -lt 600 ]; do
            sleep 1
            waited=$((waited + 1))
        done
        ;;
esac
exec /usr/bin/nice -n 19 "$LOADER" --library-path "$LIBRARIES" "$FFMPEG" -nostdin "$@"
