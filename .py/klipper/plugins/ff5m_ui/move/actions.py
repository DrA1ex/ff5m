## Product movement actions expressed through portable framework semantics.

from dataclasses import dataclass
from enum import Enum

from ui.actions import (
    Command, ContinuousMovementHint, HomingHint, MotorStateHint, MovementHint,
)
from ui.identity import CommandKey


class Axis(Enum):
    X = "x"
    Y = "y"
    Z = "z"


class ProfileMode(Enum):
    LOAD_AUTO = "load_auto"
    UNLOAD = "unload"
    DISMISS = "dismiss"


@dataclass(frozen=True)
class JogRequest:
    axis: Axis
    direction: int

    def __post_init__(self):
        if self.direction not in (-1, 1):
            raise ValueError("Jog direction must be -1 or 1")


@dataclass(frozen=True)
class HomeRequest:
    axes: tuple

    def __post_init__(self):
        if not self.axes or not all(isinstance(axis, Axis) for axis in self.axes):
            raise TypeError("Home axes must contain Axis members")


@dataclass(frozen=True)
class JoystickRequest:
    axes: tuple

    def __post_init__(self):
        if not self.axes or not all(isinstance(axis, Axis) for axis in self.axes):
            raise TypeError("Joystick axes must contain Axis members")


@dataclass(frozen=True)
class ProfileRequest:
    mode: ProfileMode


class MoveCommand(CommandKey):
    __key_namespace__ = "ui.pages.move.actions.MoveCommand"
    X_PLUS = "move.xp"
    X_MINUS = "move.xm"
    Y_PLUS = "move.yp"
    Y_MINUS = "move.ym"
    Z_PLUS = "move.zp"
    Z_MINUS = "move.zm"
    HOME_ALL = "move.homeall"
    HOME_XY = "move.homexy"
    HOME_Z = "move.homez"
    DISABLE_MOTORS = "move.motors"
    JOYSTICK_XY = "move.joy.xy"
    JOYSTICK_Z = "move.joy.z"
    CAUTION_DISMISS = "move.caution.dismiss"
    CAUTION_AUTO = "move.caution.auto"
    CAUTION_UNLOAD = "move.caution.unload"


def _jog(key, axis, direction, speed):
    return Command(
        key, JogRequest(axis, direction),
        hint=MovementHint(axis=axis, speed=speed))


X_PLUS = _jog(MoveCommand.X_PLUS, Axis.X, 1, 6000)
X_MINUS = _jog(MoveCommand.X_MINUS, Axis.X, -1, 6000)
Y_PLUS = _jog(MoveCommand.Y_PLUS, Axis.Y, 1, 6000)
Y_MINUS = _jog(MoveCommand.Y_MINUS, Axis.Y, -1, 6000)
Z_PLUS = _jog(MoveCommand.Z_PLUS, Axis.Z, 1, 600)
Z_MINUS = _jog(MoveCommand.Z_MINUS, Axis.Z, -1, 600)
HOME_ALL = Command(
    MoveCommand.HOME_ALL,
    HomeRequest((Axis.X, Axis.Y, Axis.Z)),
    hint=HomingHint((Axis.X, Axis.Y, Axis.Z), (Axis.Z, Axis.X, Axis.Y)))
HOME_XY = Command(
    MoveCommand.HOME_XY, HomeRequest((Axis.X, Axis.Y)),
    hint=HomingHint((Axis.X, Axis.Y), (Axis.X, Axis.Y)))
HOME_Z = Command(
    MoveCommand.HOME_Z, HomeRequest((Axis.Z,)),
    hint=HomingHint((Axis.Z,), (Axis.Z,)))
DISABLE_MOTORS = Command(
    MoveCommand.DISABLE_MOTORS,
    hint=MotorStateHint(False, (Axis.X, Axis.Y, Axis.Z)))
JOYSTICK_XY = Command(
    MoveCommand.JOYSTICK_XY, JoystickRequest((Axis.X, Axis.Y)),
    hint=ContinuousMovementHint(
        (Axis.X, Axis.Y), direction_signs=(1, 1), release_duration=0.34))
JOYSTICK_Z = Command(
    MoveCommand.JOYSTICK_Z, JoystickRequest((Axis.Z,)),
    hint=ContinuousMovementHint(
        (Axis.Z,), direction_signs=(-1,), release_duration=0.34))
CAUTION_DISMISS = Command(
    MoveCommand.CAUTION_DISMISS, ProfileRequest(ProfileMode.DISMISS))
CAUTION_AUTO = Command(
    MoveCommand.CAUTION_AUTO, ProfileRequest(ProfileMode.LOAD_AUTO))
CAUTION_UNLOAD = Command(
    MoveCommand.CAUTION_UNLOAD, ProfileRequest(ProfileMode.UNLOAD))
