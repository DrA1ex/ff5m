## Lightweight movement input geometry shared with the product controller.

# These values are the resolved centers of joystick/page.py's ``xy.pad`` and
# ``z.track`` rectangles. Keeping them independent lets Klipper normalize
# touch input without constructing either declarative movement page at boot.
JOYSTICK_XY_CENTER = (240, 229)
JOYSTICK_XY_RADIUS = 138
JOYSTICK_Z_CENTER = (546, 260)
JOYSTICK_Z_RADIUS = 156
