#!/bin/bash

## Screen drawing script
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

export LANG=en_US.UTF-8

source /opt/config/mod/.shell/common.sh

splash_running() {
    [ -p "$SPLASH_CONTROL_FIFO" ] || return 1
    pidof splash > /dev/null 2>&1
}

splash_seed() {
    local uptime

    if read -r uptime _ < /proc/uptime; then
        printf '%s\n' "${uptime%%.*}"
    else
        printf '%s\n' "1"
    fi
}

splash_command() {
    local command="$1"
    shift

    [ -p "$SPLASH_CONTROL_FIFO" ] || return 1

    # Open read/write so a stale FIFO cannot block the shell while opening it.
    case "$command" in
        subtitle)
            { printf 'subtitle %s\n' "$*" >&3; } 3<> "$SPLASH_CONTROL_FIFO"
        ;;
        stop)
            { printf 'stop\n' >&3; } 3<> "$SPLASH_CONTROL_FIFO"
        ;;
        *)
            echo "Unknown splash command: $command" >&2
            return 1
        ;;
    esac
}

splash_start() {
    if splash_running; then
        echo "?? Splash already running"
        return 0
    fi

    echo "Starting splash..."

    rm -f "$SPLASH_CONTROL_FIFO"
    screen_theme_args

    "$BINS/splash" \
        "${SCREEN_THEME_ARGS[@]}" \
        --subtitle "$(splash_subtitle)" \
        --seed "$(splash_seed)" \
        --control-fifo "$SPLASH_CONTROL_FIFO" \
        </dev/null >/dev/null 2>&1 &

    echo "Splash: Waiting for FIFO"

    local attempts=0
    while [ ! -p "$SPLASH_CONTROL_FIFO" ] && [ "$attempts" -lt 50 ]; do
        sleep 0.1
        attempts=$((attempts + 1))
    done

    if [ ! -p "$SPLASH_CONTROL_FIFO" ]; then
        echo "?? Splash: control FIFO was not created"
        return 1
    fi

    echo "Splash: Done"
}

splash_set_subtitle() {
    splash_command subtitle "$1" || echo "splash: cannot update subtitle; splash is not running" >&2
}

splash_subtitle() {
    local version="${1:-}"
    local patch=""
    local firmware=""

    if [ -z "$version" ] && [ -s "$VERSION_F" ]; then
        version="$(cat "$VERSION_F")"
    fi

    if [ -n "$version" ] && [ -s "$VERSION_PATCH_F" ]; then
        patch="$(cat "$VERSION_PATCH_F")"
        patch="${patch#@}"
    fi

    local display_mode
    display_mode="$("$CMDS"/zdisplay.sh test)"
    if [ "$display_mode" = "STOCK" ] && [ -s "$FIRMWARE_VERSION_F" ]; then
        firmware="$(cat "$FIRMWARE_VERSION_F")"
    fi

    if [ -z "$version" ]; then
        printf '%s\n' "FLASHFORGE AD5M (PRO) MOD"
        return 0
    fi

    local subtitle
    if [ -n "$patch" ] || [ -n "$firmware" ]; then
        subtitle="FF AD5M (PRO) MOD // V${version}"
    else
        subtitle="FLASHFORGE AD5M (PRO) MOD  // V${version}"
    fi

    [ -n "$patch" ] && subtitle="$subtitle @${patch}"
    [ -n "$firmware" ] && subtitle="$subtitle // FW V${firmware}"

    printf '%s\n' "$subtitle"
}

splash_set_version() {
    local version="$1"
    [ -n "$version" ] || return 0

    splash_set_subtitle "$(splash_subtitle "$version")"
}

splash_stop() {
    if [ ! -p "$SPLASH_CONTROL_FIFO" ]; then
        return 0
    fi

    echo "Stopping splash..."

    splash_command stop || return 0

    local attempts=0
    while [ -p "$SPLASH_CONTROL_FIFO" ] && [ "$attempts" -lt 30 ]; do
        sleep 0.1
        attempts=$((attempts + 1))
    done

    if [ -p "$SPLASH_CONTROL_FIFO" ]; then
        echo "?? Splash: stop command timed out"
        return 1
    fi
}

# Preserve the original draw_splash semantics: draw one neutral frame and return.
# This must not create a long-lived renderer or a control FIFO.
draw_splash() {
    screen_theme_args

    "$BINS/splash" \
        "${SCREEN_THEME_ARGS[@]}" \
        --subtitle "$(splash_subtitle)" \
        --seed "$(splash_seed)" \
        --static

    local display_mode
    display_mode="$("$CMDS"/zdisplay.sh test)"
    if [ "$display_mode" = "HEADLESS" ] && [ -s "$NET_IP_F" ]; then
        print_prepare_status "IP: $(cat "$NET_IP_F")"
    fi
}

print_message() {
    local text="$1"
    
    screen_typer batch \
        --batch fill -p 0 370 -s 800 50 -c 0 \
        --batch text -ha center -p 400 400 -c 35d9e6 -f "JetBrainsMono 12pt" --max-width 760 --truncate -t "$text"
}

print_progress() {
    local value="$1"
    
    value=$((value > 100 ? 100 : value))
    local progress_width=$(( value * 380 / 100 ))
    
    screen_typer batch \
        --batch fill    -c 0         -p 200 420 -s 400 40 \
        --batch stroke  -c 35d9e6    -p 200 420 -s 400 40 -lw 2 -sd inner \
        --batch fill    -c b47aff    -p 210 430 -s $progress_width 20 \
        --batch fill    -c 0         -p 610 420 -s 100 60  \
        --batch text    -c 35d9e6    -p 620 440 -va middle -b 0 -f "JetBrainsMono 12pt" -t "${value}%"
}

print_prepare_status() {
    local text="$1"
    
    screen_typer batch \
        --batch fill -p 205 425 -s 390 30 -c 0 \
        --batch text -p 400 440 -ha center -va middle -c 35d9e6 -f "JetBrainsMono 8pt" -b 0 --max-width 370 --truncate -t "${text}"
}

print_time() {
    if [ -z "$1" ] || [ -z "$2" ]; then
        print_left_panel ""
        return
    fi

    print_duration=$(convert_duration "$1")
    total_duration=$(convert_duration "$2")

    print_left_panel "$print_duration / $total_duration"
}

print_left_panel() {
    if [ -z "$1" ]; then
        screen_typer fill -c 0 -p 0 400 -s 200 80
        return
    fi

    screen_typer batch \
        --batch fill -c 0 -p 0 400 -s 200 80 \
        --batch text -p 180 440 -va middle -ha right -c 00f0f0 -b 0 -t "$1"
}

convert_duration() {
    local float_time=$1
    local rounded_time=$(printf "%.0f" "$float_time") # Round off the time to the nearest integer

    if (( rounded_time < 60 )); then
        echo "$rounded_time s"
    elif (( rounded_time < 3600 )); then
        local minutes=$((rounded_time / 60))
        echo "$minutes m"
    else
        local hours=$((rounded_time / 3600))
        echo "$hours h"
    fi
}

case "$1" in
    splash_start)
        splash_start
    ;;

    splash_version)
        splash_set_version "$2"
    ;;

    splash_subtitle)
        splash_set_subtitle "$2"
    ;;

    splash_stop)
        splash_stop
    ;;

    draw_splash)
        draw_splash
    ;;

    draw_status_bar)
        icon_wifi=$(printf '\uE146')
        icon_heater=$(printf '\ue119')
        icon_bed=$(printf '\ue003')
        icon_servo=$(printf '\ue050')
        icon_active=$(printf '\ue076')
        icon_camera=$(printf '\ue03b')

        shift

        nozzle_temp="$1"
        bed_temp="$2"
        camera_active=$( ps | grep -q "[m]jpg_streamer"; echo $(($? == 0)) )

        wifi_color=$( [ -f "$WIFI_CONNECTED_F" ] && echo "ffffff" || echo "606060" )
        nozzle_color=$( [ "$nozzle_temp" -ge 50 ] && echo "ff0000" || echo "ffffff" )
        bed_color=$( [ "$bed_temp" -ge 40 ] && echo "ff0000" || echo "ffffff" )
        active_color="ea00ff"
        servo_color="ff9000"
        camera_color="ffffff"

        y=25

        batches=(
            --batch fill -p 0 0 -s 800 40
            --batch text -p 30  "$y" -c "$bed_color"      -ha right  -va middle -f  "Typicons 12pt"      -t "$icon_bed"
            --batch text             -c "$bed_color"      -ha left   -va middle -f  "Roboto 12pt"        -t " $bed_temp  "
            --batch text             -c "$nozzle_color"   -ha left   -va middle -f  "Typicons 12pt"      -t "$icon_heater"
            --batch text             -c "$nozzle_color"   -ha left   -va middle -f  "Roboto 12pt"        -t " $nozzle_temp"
        )

        x=770
        x_offset=40

        batches+=(
            --batch text -p $x $y  -c "$wifi_color"     -ha right    -va middle -f  "Typicons 12pt"      -t "$icon_wifi"
        ) && x=$((x - x_offset))

        [ "$camera_active" -eq 1 ] && batches+=(
            --batch text -p $x $y  -c "$camera_color"   -ha right    -va middle -f  "Typicons 12pt"      -t "$icon_camera"
        ) && x=$((x - x_offset))

        [ "$3" -eq 1 ] && batches+=(
            --batch text -p $x $y  -c "$active_color"   -ha right    -va middle -f  "Typicons 12pt"      -t "$icon_active"
        ) && x=$((x - x_offset))

        [ "$4" -eq 1 ] && batches+=(
            --batch text -p $x $y  -c "$servo_color"    -ha right    -va middle -f  "Typicons 12pt"      -t "$icon_servo"
        ) && x=$((x - x_offset))
        
        screen_typer batch "${batches[@]}"
    ;;
    
    print_file)
        if [ -z "$2" ]; then
            echo "File name is missing"
            exit 1
        fi
        
        print_message "$2"
        print_progress 0
        print_time ""
    ;;
    
    print_progress)
        if [ -z "$2" ]; then
            echo "Progress value is missing"
            exit 1
        fi
        
        print_progress "$2"
    ;;

    print_time)      
        print_time "$2" "$3"
    ;;

    print_temperature)
        print_left_panel "$2"
    ;;

    print_status)
        if [ -z "$2" ]; then
            echo "Status is missing"
            exit 1
        fi
        
        print_prepare_status "$2"
    ;;
    
    end_print)
        message="$2"
        if [ -z "$message" ]; then
            message="Finished!"
        fi
        
        print_message "$message"
        print_progress "100"
        print_time ""
    ;;
    
    backlight)
        value=$2
        if [ -z "$2" ]; then
            echo "Backlight value is missing"
            exit 1
        fi
        
        chroot "$MOD" /root/printer_data/py/backlight.py $value
    ;;
    *)
        echo "Usage: $0 splash_start|splash_version|splash_subtitle|splash_stop|draw_splash|<screen command> [args...]"
        exit 1
esac
