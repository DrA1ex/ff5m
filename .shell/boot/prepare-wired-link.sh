#!/bin/bash

## Prepare the stock Ethernet link while normal initialization continues.
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
## This file may be distributed under the terms of the GNU GPLv3 license.

prepare_wired_link() {
    local interfaces="$1" mac
    [ -r "$interfaces" ] || return 0

    # Only overlap carrier negotiation for the simple stock DHCP setup.
    # The driver accepts the same MAC again while up; DHCP stays with ifup.
    if mac=$(/bin/busybox awk '
        !NF || $1 ~ /^#/ { next }
        $1 == "auto" {
            for (i = 2; i <= NF && $i !~ /^#/; i++)
                if ($i == "eth0") automatic = 1
            next
        }
        $1 == "iface" {
            selected = $2 == "eth0"
            if (selected) {
                definitions++
                dhcp = $3 == "inet" && $4 == "dhcp"
                if (NF > 4 && $5 !~ /^#/) custom = 1
            }
            next
        }
        $1 == "hwaddress" && selected {
            hardware_addresses++
            mac = $3
            if ($2 != "ether" || (NF > 3 && $4 !~ /^#/)) custom = 1
            if (split(mac, octets, ":") != 6) custom = 1
            for (i = 1; i <= 6; i++)
                if (octets[i] !~ /^[[:xdigit:]][[:xdigit:]]$/) custom = 1
            next
        }
        { custom = 1 }
        END {
            if (!automatic || definitions != 1 || !dhcp || custom || hardware_addresses > 1) exit 1
            print mac
        }
    ' "$interfaces"); then
        local -a link_args=(eth0)
        [ -z "$mac" ] || link_args+=(hw ether "$mac")
        echo "// Preparing Ethernet carrier..."
        if ! /bin/busybox ifconfig "${link_args[@]}" up; then
            echo "?? Ethernet carrier preparation failed; stock network startup will retry."
        fi
    fi
    return 0
}

prepare_wired_link "${1:-/etc/network/interfaces}"
