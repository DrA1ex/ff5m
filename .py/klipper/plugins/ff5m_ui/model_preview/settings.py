## Visible parameters for the lazy model preview settings screen.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from ui import Button
from ..parameter_controls import control_row
from .actions import PreviewAction, PreviewRoute


def current_view_row(state):
    control = Button(PreviewAction(PreviewRoute.CAPTURE_VIEW), "USE CURRENT",
                     font="JetBrainsMono 8pt").size(180, 46).align("left", "top").offset(495, 9)
    values = "YAW %.1f / TILT %.1f / %.0f%%" % (state["yaw"], state["pitch"], state["scale"] * 100)
    return control_row("DEFAULT VIEW", values, "Save current rotation, tilt and scale.", control)


def parameter_keys(style, mode):
    keys = ["type", "cache"]
    # SOLID chooses its grid from physical model dimensions in the renderer.
    if style != "SOLID":
        keys.append("grid")
    if style == "STACKED":
        keys.append("stacked_density")
    elif style == "POINTS":
        keys.append("points_density")
    keys.extend(("yaw", "pitch", "scale", "fit", "fps"))
    if mode == "lab":
        keys.append("rotation_speed")
    return tuple("feather_preview_" + key for key in keys)
