#!/bin/sh

## Preserve the wired interface MAC in the stock ifupdown configuration.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

INTERFACES_FILE=${INTERFACES_FILE:-/etc/network/interfaces}
SYS_CLASS_NET=${SYS_CLASS_NET:-/sys/class/net}

interface=${1:-eth0}
interface_dir=$SYS_CLASS_NET/$interface

# A printer without the wired interface has nothing to preserve.
[ -d "$interface_dir" ] || exit 0

mac_addr=$(sed -n '1p' "$interface_dir/address" 2>/dev/null)
if ! printf '%s\n' "$mac_addr" \
        | grep -Eq '^[[:xdigit:]]{2}(:[[:xdigit:]]{2}){5}$'; then
    echo "Warning: Could not determine MAC address for $interface" >&2
    exit 1
fi

if [ ! -f "$INTERFACES_FILE" ]; then
    echo "Warning: $INTERFACES_FILE not found" >&2
    exit 1
fi

# Report whether the target stanza exists and already owns an explicit MAC.
# Splitting on awk's default whitespace avoids a regex assembled from the
# interface name and accepts the indentation used by BusyBox ifupdown files.
state=$(awk -v wanted="$interface" '
    /^[[:space:]]*iface[[:space:]]/ {
        count = split($0, fields)
        in_target = count >= 2 && fields[1] == "iface" && fields[2] == wanted
        if (in_target) found = 1
    }
    in_target {
        count = split($0, fields)
        if (count >= 2 && fields[2] == "ether" &&
                (fields[1] == "hwaddress" || fields[1] == "address")) {
            has_mac = 1
        }
    }
    END { printf "%d:%d\n", found, has_mac }
' "$INTERFACES_FILE") || exit 1

[ "$state" = "1:1" ] && exit 0

temporary=$INTERFACES_FILE.feather.$$
backup=$INTERFACES_FILE.backup.$(date +%s)
trap 'rm -f "$temporary"' EXIT
trap 'exit 130' HUP INT TERM

if [ "$state" = "0:0" ]; then
    {
        cat "$INTERFACES_FILE"
        printf '\nauto %s\niface %s inet dhcp\n    hwaddress ether %s\n' \
            "$interface" "$interface" "$mac_addr"
    } > "$temporary" || exit 1
else
    # The stanza exists but has no MAC. Insert immediately after its iface line
    # and preserve every other line byte-for-byte apart from the final newline.
    awk -v wanted="$interface" -v mac="$mac_addr" '
        !inserted && /^[[:space:]]*iface[[:space:]]/ {
            count = split($0, fields)
            if (count >= 2 && fields[1] == "iface" && fields[2] == wanted) {
                print
                print "    hwaddress ether " mac
                inserted = 1
                next
            }
        }
        { print }
    ' "$INTERFACES_FILE" > "$temporary" || exit 1
fi

cp "$INTERFACES_FILE" "$backup" || exit 1
if ! cat "$temporary" > "$INTERFACES_FILE"; then
    cat "$backup" > "$INTERFACES_FILE" 2>/dev/null || true
    exit 1
fi

echo "MAC address $mac_addr recorded for $interface in $INTERFACES_FILE"
