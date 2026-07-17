#!/bin/bash

## Screen drawing script
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

export LANG=en_US.UTF-8

source /opt/config/mod/.shell/common.sh

load_version() {
    export FIRMWARE_VERSION=$(cat /root/version)
    export MOD_VERSION=$(cat /opt/config/mod/version.txt)
    export MOD_VERSION_PATCH=$(cat /tmp/version_patch 2> /dev/null)

}

print_versions() {
    local batches=(
        --batch fill -p 72 270 -s 311 54 -c 0
        --batch fill -p 417 270 -s 311 54 -c 0
        --batch text -ha center -va middle -p 228 286 -c b47aff -f "JetBrainsMono Bold 12pt" -t "v$MOD_VERSION"
        --batch text -ha center -va middle -p 572 297 -c 35d9e6 -f "JetBrainsMono Bold 12pt" -t "v$FIRMWARE_VERSION"
    )

    if [ -n "$MOD_VERSION_PATCH" ]; then
        batches+=(
            --batch text -ha center -va middle -p 228 310 -c b47aff -f "JetBrainsMono Bold 8pt" -t "$MOD_VERSION_PATCH"
        )
    fi

    "$BINS/typer" -db batch "${batches[@]}"
}

print_message() {
    local text="$1"
    
    "$BINS/typer" -db batch \
        --batch fill -p 0 370 -s 800 50 -c 0 \
        --batch text -ha center -p 400 400 -c 35d9e6 -f "JetBrainsMono 12pt" -t "$text"
}

print_progress() {
    local value="$1"
    
    value=$((value > 100 ? 100 : value))
    local progress_width=$(( value * 380 / 100 ))
    
    "$BINS/typer" -db batch \
        --batch fill    -c 0         -p 200 420 -s 400 40 \
        --batch stroke  -c 35d9e6    -p 200 420 -s 400 40 -lw 2 -sd inner \
        --batch fill    -c b47aff    -p 210 430 -s $progress_width 20 \
        --batch fill    -c 0         -p 610 420 -s 100 60  \
        --batch text    -c 35d9e6    -p 620 440 -va middle -b 0 -f "JetBrainsMono 12pt" -t "${value}%"
}

print_prepare_status() {
    local text="$1"
    
    "$BINS/typer" -db batch \
        --batch fill -p 205 425 -s 390 30 -c 0 \
        --batch text -p 400 440 -ha center -va middle -c 35d9e6 -f "JetBrainsMono 8pt" -b 0 -t "${text}"
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
        "$BINS/typer" fill -c 0 -p 0 400 -s 200 80
        return
    fi

    "$BINS/typer" -db batch\
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

level_to_color() {
    local level="$1"
    local level_trimmed="${level%"${level##*[![:space:]]}"}"

    case "$level_trimmed" in
            3|ERROR)
                color=c43c00
            ;;
            2|WARN)
                color=fa7c17
            ;;
            1|INFO)
                color=ffffff
            ;;
            *)
                color=b7a6b5
            ;;
        esac

    echo "$color"
}

case "$1" in
    draw_loading)
        load_version

        if [ "$2" != "--no-clear" ]; then
            xzcat "$LOAD_IMG_XZ" > /dev/fb0
        fi
        
        print_versions
    ;;
    
    draw_splash)
        load_version
        if [ "$2" != "--no-clear" ]; then
            xzcat "$SPLASH_IMG_XZ" > /dev/fb0
        fi

        print_versions

        if [ -f "$NET_IP_F" ]; then
            print_prepare_status "IP: $(cat "$NET_IP_F")"
        fi
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
        
        "$BINS/typer" -db batch "${batches[@]}"
    ;;
    
    boot_message)
        shift
        if [ -z "$1" ]; then
            echo "message text is missing"
            exit 1
        fi
        
        args=("$@")
        count=${#args[@]}

        max_lines=5
        line_height=22
        bottom_offset=460

        batches=(
            --batch fill -c 0 -p 0 "$((bottom_offset - max_lines * line_height))" -s 800 "$(((max_lines + 1) * line_height))"
        )

        height=$(((count - 1) * line_height))
        y_offset=$((bottom_offset - height))

        for str in "${args[@]}"; do
            # Split argument into level and message
            level="${str%%;;*}"
            message="${str#*;;}"
            color=$(level_to_color "$level")

            batches+=(
                --batch text -ha left -va middle -p 10 "$y_offset" -c "$color" -f "JetBrainsMono Bold 8pt" -t "$message"
            )

            y_offset=$((y_offset + line_height))
        done

        uptime=$(awk '{print $1}' < /proc/uptime)
        batches+=(
            --batch text -ha right -va middle -p 790 "$bottom_offset" -c "00ffff" -f "JetBrainsMono Bold 8pt" -t "<< $uptime"
        )

        "$BINS/typer" -db batch "${batches[@]}"
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
        echo "Usage: $0 <command> [args...]"
        exit 1
esac
