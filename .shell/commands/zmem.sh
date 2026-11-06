#!/bin/sh

## Memory usage print
##
## Copyright (C) 2025, Alexander K <https://github.com/drA1ex>
## Copyright (C) 2025, Sergei Rozhkov <https://github.com/ghzserg>
##
## This file may be distributed under the terms of the GNU GPLv3 license


/root/printer_data/py/ps_mem.py --swap > /tmp/mem

< /tmp/mem sed 's/python3.11/Moonraker/' | sed 's/firmwareExe/Firmware/' | sed 's/forge_x_camera/Camera/' | sed 's/forge_x_screen/Screen stream/'
rm -f /tmp/mem

free -m