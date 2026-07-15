#!/usr/bin/env python

## Configuration management, backup and restore
##
## Copyright (C) 2024, Tristan <https://github.com/consp>
##
## This file may be distributed under the terms of the GNU GPLv3 license


from fcntl import ioctl
from struct import pack
import argparse
import errno

DISP_LCD_SET_BRIGHTNESS = 0x102
DISP_LCD_GET_BRIGHTNESS = 0x103
DISP_LCD_BACKLIGHT_ENABLE = 0x104
DISP_LCD_BACKLIGHT_DISABLE = 0x105

def backlight_enable(enable=True):
    if enable:
        ctl = DISP_LCD_BACKLIGHT_ENABLE
    else:
        ctl = DISP_LCD_BACKLIGHT_DISABLE
    with open("/dev/disp", "wb") as f:
        try:
            ioctl(f, ctl, b"")
        except OSError as exc:
            # Re-enabling an already enabled backlight returns EPERM on this
            # display driver. It is harmless when a brightness update follows.
            if not enable or exc.errno != errno.EPERM:
                raise

def backlight_get():
    with open("/dev/disp", "wb") as f:
        buffer = bytearray(4)
        value = ioctl(f, DISP_LCD_GET_BRIGHTNESS, buffer)
    return value

def backlight_set(value):
    with open("/dev/disp", "wb") as f:
        ioctl(f, DISP_LCD_SET_BRIGHTNESS, pack("=L", 0) + pack("=L", int(1 + (value * (255/100.0)))))

def inputrange(value):
    try:
        value = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("input must be int or float")

    if not (value is None or (value >= 0 and value <= 100.0)):
        raise argparse.ArgumentTypeError("value must be within 0-100 or -1")
    return value

def main():
    parser = argparse.ArgumentParser(
        prog="FF AD5M Backlight control",
        description='Control the backlight, 0 disables, 1-100% sets backlight from 1 to 100%. Credits to @xblax for finding the IOCTLs.',
        epilog="See https://github.com/consp/flashforge_ad5m_backlight and https://github.com/xblax/flashforge_adm5_klipper_mod")

    parser.add_argument(
            'brightness',
            type=inputrange,
            const=None,
            nargs='?',
            help="brightness, 0 disables, >0 to 100.0 sets brightness in %%, nothing returns current value (0-255)",
            )

    args = parser.parse_args()

    value = args.brightness

    if value is None:
        print("%d" % backlight_get())
    elif value == 0 or value == 0.0:
        backlight_enable(False)
    else:
        backlight_enable()
        backlight_set(args.brightness)

if __name__ == "__main__":
    main()
