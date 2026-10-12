## Semantic actions for model preview views.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from dataclasses import dataclass
from enum import Enum

from ui.actions import Action


class PreviewRoute(Enum):
    FILE = "file"
    PRINT = "print"
    SETTINGS = "settings"
    CAPTURE_VIEW = "capture_view"
    CACHE = "cache"
    STYLE = "style"
    YAW_LEFT = "yaw_left"
    YAW_RIGHT = "yaw_right"
    PITCH_DOWN = "pitch_down"
    PITCH_UP = "pitch_up"
    LAYER_DOWN = "layer_down"
    LAYER_UP = "layer_up"
    SCALE_DOWN = "scale_down"
    SCALE_UP = "scale_up"
    YAW_JOYSTICK = "yaw_joystick"
    PITCH_JOYSTICK = "pitch_joystick"
    RESET = "reset"
    FULL = "full"
    ROTATION = "rotation"
    BUILD = "build"


@dataclass(frozen=True)
class PreviewAction(Action):
    route: PreviewRoute
    style: int = None
    kind = "model_preview_action"

    def __post_init__(self):
        if not isinstance(self.route, PreviewRoute):
            raise TypeError("PreviewAction requires a PreviewRoute")
        if self.route == PreviewRoute.STYLE:
            if type(self.style) is not int or not 0 <= self.style < 5:
                raise ValueError("Preview style must be between 0 and 4")
        elif self.style is not None:
            raise ValueError("Only a style action accepts a style index")

    @property
    def wire_id(self):
        return "model.%s%s" % (self.route.value,
                               ".%d" % self.style if self.style is not None else "")

    def as_dict(self):
        return {"kind": self.kind, "route": self.route.value, "style": self.style}
