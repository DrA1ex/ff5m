## Runtime facade for movement page packages.

from ui.lazy import resolve_lazy_export
from .actions import (
    CAUTION_AUTO, CAUTION_DISMISS, CAUTION_UNLOAD, DISABLE_MOTORS, HOME_ALL,
    HOME_XY, HOME_Z, JOYSTICK_XY, JOYSTICK_Z, X_MINUS, X_PLUS, Y_MINUS,
    Y_PLUS, Z_MINUS, Z_PLUS, Axis, HomeRequest, JogRequest, JoystickRequest,
    MoveCommand, ProfileMode, ProfileRequest,
)
from .state import MoveState, ToolheadState, snapshot_values


_LAZY_EXPORTS = {
    "FONT": ("common", "FONT"),
    "MOVE_CONTENT": ("common", "MOVE_CONTENT"),
    "STEP_VALUES": ("common", "STEP_VALUES"),
    "JOYSTICK_PAGE": ("joystick.page", "PAGE"),
    "JoystickRef": ("joystick.page", "JoystickRef"),
    "render_joystick": ("joystick.page", "render"),
    "update_joystick": ("joystick.page", "update"),
    "STEP_PAGE": ("step.page", "PAGE"),
    "StepRef": ("step.page", "StepRef"),
    "render_step": ("step.page", "render"),
    "render_step_status": ("step.page", "update_status"),
}


def __getattr__(name):
    return resolve_lazy_export(
        globals(), name, _LAZY_EXPORTS, __package__)
