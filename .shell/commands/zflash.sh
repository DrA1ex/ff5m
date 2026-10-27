#!/bin/bash

## Prepare a verified firmware image and request one-shot Recovery flashing.
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
## This file may be distributed under the terms of the GNU GPLv3 license.

set -u
source /opt/config/mod/.shell/common.sh || exit 1
source "$SCRIPTS/boot/stock_identity.sh" || exit 1

REQUEST=/opt/config/mod/BOOT_FLASH_IMAGE
RECOVERY_FLAG=/opt/config/mod/BOOT_FLAG_SKIP

fail() {
    echo "!! $1"
    echo "// action:prompt_end"
    echo "// action:prompt_begin Firmware download failed"
    echo "// action:prompt_text $1"
    echo "// action:prompt_footer_button OK|RESPOND TYPE=command MSG=action:prompt_end|error"
    echo "// action:prompt_show"
    exit 1
}

load_stock_printer_identity || fail "Unable to detect the printer model."
[ -x "$MOD/bin/python3" ] || fail "Forge-X Python is unavailable."

# zflash.py validates the arguments. It prints "@@IMAGE|name" only after the
# image is downloaded and verified, and "!! reason" on any failure.
image="" reason=""
while IFS= read -r line; do
    case "$line" in
        '@@PROGRESS|'*)
            data=${line#@@PROGRESS|}; percent=${data%%|*}; message=${data#*|}
            firmware_download_prompt "$percent" "$message"
            echo "// $message ($percent%)"
        ;;
        '@@IMAGE|'*) image=${line#@@IMAGE|} ;;
        '!! '*) reason=${line#!! }; echo "$line" ;;
        *) echo "$line" ;;
    esac
done < <(chroot "$MOD" /bin/python3 -u "$PY/zflash.py" "$FIRMWARE_MACHINE" "$@" 2>&1)

[ -z "$reason" ] || fail "$reason"
[ "${1:-}" != list ] || exit 0
[ -n "$image" ] || fail "Firmware download or verification failed."

# Recovery validates the requested name again and consumes it before use.
printf '%s\n' "$image" > "$REQUEST" && touch "$RECOVERY_FLAG" && sync || {
    rm -f "$REQUEST" "$RECOVERY_FLAG"
    fail "Cannot request firmware installation in Recovery."
}
echo "// Firmware is ready. Rebooting into Recovery for installation..."
reboot
