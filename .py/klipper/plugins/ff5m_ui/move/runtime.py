## Runtime facade for movement page packages.

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
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError("module %r has no attribute %r" % (__name__, name))
    module = __import__("%s.%s" % (__package__, target[0]),
                        fromlist=(target[1],))
    value = getattr(module, target[1])
    globals()[name] = value
    return value
