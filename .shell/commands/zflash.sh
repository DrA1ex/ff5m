#!/bin/bash

## Prepare a verified firmware image and request one-shot Recovery flashing.
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
## This file may be distributed under the terms of the GNU GPLv3 license.

set -u
source /opt/config/mod/.shell/common.sh || exit 1
source "$SCRIPTS/boot/stock_identity.sh" || exit 1

REQUEST=/opt/config/mod/BOOT_FLASH_IMAGE
RECOVERY_FLAG=/opt/config/mod/BOOT_FLAG_SKIP
LOCK=/tmp/forge-x-flash-download.lock

fail() {
    echo "!! $1"
    echo "// action:prompt_end"
    echo "// action:prompt_begin Firmware download failed"
    echo "// action:prompt_text $1"
    echo "// action:prompt_footer_button OK|RESPOND TYPE=command MSG=action:prompt_end|error"
    echo "// action:prompt_show"
    exit 1
}

# Listing has no side effects: fetch and display the available image versions.
if [ "$#" -eq 3 ] && [ "$1" = "list" ]; then
    case "$2" in STOCK|FORGEX|RECOVERY) ;; *) fail "Invalid firmware type." ;; esac
    case "$3" in ''|*[!0-9]*) fail "Invalid firmware list page." ;; esac
    load_stock_printer_identity || fail "Unable to detect the printer model."
    [ -x "$MOD/bin/python3" ] || fail "Forge-X Python is unavailable."
    [ -f "$PY/zflash.py" ] || fail "Firmware downloader is unavailable."
    chroot "$MOD" /bin/python3 -u "$PY/zflash.py" list "$2" "$FIRMWARE_MACHINE" "$3"
    [ "$?" -eq 0 ] || fail "Unable to load available firmware versions."
    exit 0
fi

[ "$#" -eq 2 ] || fail "Specify TYPE and VERSION."
case "$1" in STOCK|FORGEX|RECOVERY) ;; *) fail "Invalid firmware type." ;; esac
case "$2" in ''|*[!a-zA-Z0-9._-]*) fail "Invalid firmware version." ;; esac
load_stock_printer_identity || fail "Unable to detect the printer model."
[ -x "$MOD/bin/python3" ] || fail "Forge-X Python is unavailable."
[ -f "$PY/zflash.py" ] || fail "Firmware downloader is unavailable."
# This operation must not run concurrently with a second download request.
( set -o noclobber; : > "$LOCK" ) 2>/dev/null \
    || fail "Another firmware download is in progress."
trap 'rm -f "$LOCK"' EXIT
trap 'exit 1' HUP INT TERM

# Capture the explicit ready marker; only successful, verified downloads
# are permitted to arm the early recovery boot guard.
output=$(mktemp /tmp/forge-x-flash.XXXXXX) || fail "Cannot create download status file."
trap 'rm -f "$output" "$LOCK"' EXIT
chroot "$MOD" /bin/python3 -u "$PY/zflash.py" "$1" "$2" "$FIRMWARE_MACHINE" \
    2>&1 | tee "$output"
status=${PIPESTATUS[0]}
[ "$status" -eq 0 ] || fail "Firmware download or verification failed. See console for details."
image=$(sed -n 's/^@@IMAGE|//p' "$output" | tail -1)
case "$image" in
    "$FIRMWARE_MACHINE"-*.tgz) ;;
    *) fail "Downloaded image does not match the printer model." ;;
esac
[ -f "/data/forge-x-recovery/$image" ] \
    && [ ! -L "/data/forge-x-recovery/$image" ] \
    || fail "Verified firmware image disappeared."

# Fail before writing the recovery flag if the request cannot be persisted.
temporary=$(mktemp /opt/config/mod/.BOOT_FLASH_IMAGE.XXXXXX) \
    || fail "Cannot prepare Recovery request."
printf '%s\n' "$image" > "$temporary" || {
    rm -f "$temporary"
    fail "Cannot save Recovery request."
}
mv -f "$temporary" "$REQUEST" || fail "Cannot save Recovery request."
sync || fail "Cannot persist firmware request."
touch "$RECOVERY_FLAG" || {
    rm -f "$REQUEST"
    fail "Cannot arm Recovery."
}
sync || fail "Cannot persist Recovery flag."
echo "// Firmware is ready. Rebooting into Recovery for installation..."
reboot
