#!/bin/sh

cd /opt/config/mod_data/ || exit 1

data_path_x=$(ls -1 calibration_data_x_*.json | sort | tail -n 1)
data_path_y=$(ls -1 calibration_data_y_*.json | sort | tail -n 1)

ID=$(date +"%Y%m%d_%H%M%S")

echo "Preparing image for X axis. Please wait..."
python3 /opt/config/mod/.root/zshaper/calibrate_shaper.py -o "calibration_data_x_${ID}.png" -d "$data_path_x"

echo "Preparing image for Y axis. Please wait..."
python3 /opt/config/mod/.root/zshaper/calibrate_shaper.py -o "calibration_data_y_${ID}.png" -d "$data_path_y"

echo "Images are located in the Configuration -> mod_data tab. They are named 'calibration_data_x_${ID}.png' and 'calibration_data_y_${ID}.png'."
