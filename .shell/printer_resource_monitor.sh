#!/bin/sh

## Bounded printer resource sampling for host-orchestrated diagnostics.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

## This sampler observes host overload, so its own cost is part of the
## measurement.  Each iteration runs one printer_resource_sample.awk pass, one
## `tr` per Python process it inspects, and `sleep`; everything
## else in the loop is a shell builtin.  Adding a command substitution here
## costs another fork per second on a dual-core Cortex-A7 that is simultaneously
## feeding Klipper's MCU, which is exactly what makes samples unreadable.

output=${1:-}
interval=${2:-1}
duration=${3:-3600}

case "$output" in
    /data/feather-ui-tests/host-monitor-[0-9]*.tsv) ;;
    *) exit 2 ;;
esac
case "$interval:$duration" in
    *[!0-9.:]*) exit 2 ;;
esac

case "$0" in
    */*) sampler=${0%/*}/printer_resource_sample.awk ;;
    *) sampler=./printer_resource_sample.awk ;;
esac
[ -r "$sampler" ] || exit 1

mkdir -p /data/feather-ui-tests || exit 1
printf 'epoch\tuptime\tload1\tmem_available_kb\tswap_free_kb\trole\tpid\tcpu_ticks\trss_kb\tstate\tthreads\tvoluntary_ctxt_switches\tnonvoluntary_ctxt_switches\tminor_faults\tmajor_faults\tscheduler_runtime_ns\tscheduler_wait_ns\tscheduler_timeslices\tread_bytes\twrite_bytes\tsyscr\tsyscw\twchan\tcommand\n' > "$output" || exit 1

tab=$(printf '\t')
newline='
'

# The host correlates these rows against its own wall-clock telemetry, so epoch
# has to stay wall clock.  It is anchored once here and advanced from the
# monotonic /proc/uptime delta afterwards, which keeps the loop free of a `date`
# fork per iteration without letting the two clocks drift apart.
start=$(date +%s)
IFS=' ' read -r start_uptime _rest < /proc/uptime
start_seconds=${start_uptime%%.*}
case "$start_seconds" in
    ''|*[!0-9]*) exit 1 ;;
esac

trap 'exit 0' HUP INT TERM

while :; do
    uptime=
    IFS=' ' read -r uptime _rest < /proc/uptime
    seconds=${uptime%%.*}
    # /proc/uptime is this loop's clock, so an unreadable one has to stop the
    # sampler rather than let it spin past its duration bound.  The host then
    # sees a file with no sample rows and reports that, which is the honest
    # outcome; silently sampling on with a broken clock is not.
    case "$seconds" in
        ''|*[!0-9]*) break ;;
    esac
    elapsed=$((seconds - start_seconds))
    [ "$elapsed" -ge "$duration" ] && break
    epoch=$((start + elapsed))

    # Classification needs the process cmdline, which awk cannot read: busybox
    # awk truncates strings at the NUL separators /proc/<pid>/cmdline uses, so
    # every Python process would look identical to it.  The shell decides who
    # is interesting and awk samples only those.
    processes=
    for procdir in /proc/[0-9]*; do
        [ -r "$procdir/stat" ] || continue
        pid=${procdir#/proc/}
        process_name=
        IFS= read -r process_name < "$procdir/comm" 2>/dev/null
        command=$process_name
        case "$process_name" in
            *dropbear*) role=dropbear ;;
            *Typer*|*typer*) role=typer ;;
            python*|Python*)
                command=$(tr '\000\t\n' '   ' \
                    < "$procdir/cmdline" 2>/dev/null)
                case "$command" in
                    *klippy.py*) role=klippy ;;
                    *moonraker.py*) role=moonraker ;;
                    *) continue ;;
                esac
                ;;
            *)
                continue
                ;;
        esac
        processes="$processes$pid$tab$role$tab$command$newline"
    done

    # The list travels in the environment rather than through `-v` or a pipe:
    # commands are arbitrary text that awk would reinterpret as escapes, and a
    # pipe would cost the fork this pass exists to avoid.
    FF5M_PROCESSES=$processes awk -v epoch="$epoch" -v uptime="${uptime:-0}" \
        -f "$sampler" >> "$output"

    sleep "$interval" || break
done
