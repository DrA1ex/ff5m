## Shared presentation definitions for model preview views.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from enum import Enum

from ui import ThemeColor
from ui.bindings import resolve, state
from ui.components import Component
from ui.identity import StateKey


FONT = "JetBrainsMono 8pt"
BUTTON_FONT = "JetBrainsMono Bold 8pt"


class PreviewState(StateKey):
    __key_namespace__ = "ff5m_ui.model_preview.PreviewState"
    FILENAME = state(str, default="")
    BANDS = state(tuple, default=())
    ERROR = state(str, default="")
    STYLE = state(int, default=4, minimum=0, maximum=4)
    CACHE = state(bool, default=True)
    ROTATION = state(bool, default=False)
    BUILD = state(bool, default=False)
    SCALE = state(float, default=1.0, minimum=0.25, maximum=3.0, simulation_role="preview.scale")
    YAW = state(float, default=0.0, minimum=-150.0, maximum=150.0, simulation_role="preview.yaw")
    PITCH = state(float, default=30.0, minimum=-70.0, maximum=70.0, simulation_role="preview.pitch")
    STATUS = state(str, default="")
    METRICS = state(str, default="")
    DETAIL = state(str, default="")
    FILE_TIME = state(str, default="--")
    FILE_LAYERS = state(str, default="--")
    FILE_SIZE = state(str, default="--")


class PreviewRef(Enum):
    ROOT = "model_preview.root"
    FILENAME = "model_preview.filename"
    CAMERA = "model_preview.camera"
    IMAGE = "model_preview.image"
    STATUS = "model_preview.status"
    METRICS = "model_preview.metrics"
    SCALE = "model_preview.scale"
    INFO = "model_preview.info"
    YAW_PAD = "model_preview.yaw_pad"
    TILT_PAD = "model_preview.tilt_pad"
    ERROR = "model_preview.error"


class ModelFrame(Component):
    """One opaque raster, transported in bounded FXI1 image bands."""

    covers_bounds = True

    def __init__(self, bands, error):
        super().__init__()
        self.bands, self.error = bands, error

    def state_signature(self, state):
        # Bands are immutable worker results; avoid traversing pixel bytes.
        return id(resolve(self.bands, state)), resolve(self.error, state)

    def draw(self, renderer, state, bounds):
        bands = resolve(self.bands, state)
        if bands:
            return [renderer.image(bounds.x, bounds.y + offset, blob, format="fxi1")
                    for offset, blob in bands]
        return [renderer.fill(*bounds, ThemeColor.PANEL),
                renderer.text(bounds.center_x, bounds.center_y,
                              "FAILED" if resolve(self.error, state) else "PREPARING...",
                              ThemeColor.DIM, FONT, "center", "middle")]


def _selected(value):
    return "selected" if value else "enabled"


def _style_state(current, index):
    return _selected(current == index)
