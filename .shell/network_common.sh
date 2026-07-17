#!/bin/sh

# Shared, dependency-free network lifecycle helpers.  The stock udhcpc script
# only replaces routes for the interface it configures, so every mode switch
# must explicitly retire the previous interface after the new one is usable.
#
# Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
#
# This file may be distributed under the terms of the GNU GPLv3 license

PATH=/sbin:/usr/sbin:/bin:/usr/bin
export PATH

: "${NETWORK_RESOLV_CONF:=/etc/resolv.conf}"

network_ipv4() {
    ip addr show "$1" 2>/dev/null \
        | awk '/inet / {print $2; exit}' \
        | cut -d/ -f1
}

network_stop_udhcpc() {
    interface=$1
    pids=""

    for process in /proc/[0-9]*; do
        [ -r "$process/cmdline" ] || continue
        command=$(tr '\000' ' ' < "$process/cmdline" 2>/dev/null)
        case "$command" in
            udhcpc\ *|*/udhcpc\ *) ;;
            *) continue ;;
        esac
        case " $command " in
            *" $interface "*)
                pid=${process##*/}
                kill "$pid" >/dev/null 2>&1 || true
                pids="$pids $pid"
                ;;
        esac
    done

    # Do not race udhcpc's shutdown/deconfig handler with the subsequent
    # address flush and link-down operation.
    attempts=0
    while [ -n "$pids" ] && [ "$attempts" -lt 2 ]; do
        alive=""
        for pid in $pids; do
            kill -0 "$pid" >/dev/null 2>&1 && alive="$alive $pid"
        done
        [ -z "$alive" ] && break
        pids=$alive
        sleep 1
        attempts=$((attempts + 1))
    done
}

network_clear_dns() {
    interface=$1
    [ -f "$NETWORK_RESOLV_CONF" ] || return 0

    temporary=/tmp/feather-resolv.$$
    grep -v "# $interface\$" "$NETWORK_RESOLV_CONF" > "$temporary" || true
    cat "$temporary" > "$NETWORK_RESOLV_CONF"
    rm -f "$temporary"
}

network_clear_interface() {
    interface=$1

    # `ip addr flush` removes connected routes, but not necessarily a DHCP
    # default route.  Flush both explicitly before the interface is reused.
    ip route flush dev "$interface" >/dev/null 2>&1 || true
    ip addr flush dev "$interface" >/dev/null 2>&1 \
        || ifconfig "$interface" 0.0.0.0 >/dev/null 2>&1 \
        || true
    network_clear_dns "$interface"
}

network_deactivate_interface() {
    interface=$1

    network_stop_udhcpc "$interface"
    network_clear_interface "$interface"
    ip link set "$interface" down >/dev/null 2>&1 || true
}

network_prepare_interface() {
    interface=$1

    network_stop_udhcpc "$interface"
    network_clear_interface "$interface"
    ip link set "$interface" up >/dev/null 2>&1
}

network_activate_dhcp() {
    interface=$1
    timeout=$2

    network_prepare_interface "$interface" || return 1
    udhcpc -i "$interface" >/tmp/feather-udhcpc-"$interface".log 2>&1 &

    elapsed=0
    while [ "$elapsed" -lt "$timeout" ]; do
        address=$(network_ipv4 "$interface")
        [ -n "$address" ] && return 0
        sleep 1
        elapsed=$((elapsed + 1))
    done

    network_stop_udhcpc "$interface"
    network_clear_interface "$interface"
    return 1
}
