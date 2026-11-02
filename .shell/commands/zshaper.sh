#!/bin/bash

## zshaper running script
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
## Copyright (C) 2025, Sergei Rozhkov <https://github.com/ghzserg>
##
## This file may be distributed under the terms of the GNU GPLv3 license


source /opt/config/mod/.shell/common.sh


clear_shaper_data() {
    local path="$1"
    local name="$2"
    local preserve_cnt="${3:-1}"
    local preserve_days="${4:-+1}"

    images=$(find "$path" -name "$name" -mtime "$preserve_days" -maxdepth 1 -type f | sort)
        
    images_x=$(echo "$images" | grep -i "_x_" | head -n "-$preserve_cnt")
    images_y=$(echo "$images" | grep -i "_y_" | head -n "-$preserve_cnt")

    echo -e "${images_x}\n${images_y}" | xargs -r -I{} -- rm {}
}

case $1 in
    --plot)
        # One Python child owns the complete export; exec preserves timeout cleanup.
        if [ "$#" -ne 5 ]; then
            echo "Use FORGE_SHAPER_RESULTS PLOT=1 in the G-code console to generate saved graphs." >&2
            exit 2
        fi
        exec chroot "$MOD" /bin/python3 /opt/config/mod/.root/zshaper/calibrate_shaper.py --history "$2" "$3" "$4" "$5"
    ;;
    --clear)
        clear_shaper_data "/opt/config/mod_data" "calibration_data_*.csv"
        clear_shaper_data "/opt/config/mod_data" "calibration_data_*.json"
        clear_shaper_data "/opt/config/mod_data" "calibration_data_*.png"
    ;;
    --calculate)
        cp -u /tmp/*.csv /opt/config/mod_data/
        cp -u /tmp/*.json /opt/config/mod_data/
        chroot "$MOD" /opt/config/mod/.root/zshaper.sh
    ;;
    *)
        echo "Unknown parameter value: '$1'"
        exit 1
    ;;
esac

exit $?
