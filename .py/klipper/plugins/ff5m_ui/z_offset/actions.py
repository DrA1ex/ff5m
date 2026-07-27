## Product Z-offset actions expressed through portable framework semantics.

from dataclasses import dataclass
from enum import Enum

from ui.actions import Command, MovementHint, ProbingHint
from ui.identity import CommandKey


class Zone(Enum):
    REAR_LEFT = "rear_left"
    CENTER = "center"
    REAR_RIGHT = "rear_right"
    FRONT_LEFT = "front_left"
    FRONT_RIGHT = "front_right"


class Adjustment(Enum):
    CLOSER = "closer"
    FARTHER = "farther"


@dataclass(frozen=True)
class ZoneRequest:
    zone: Zone


@dataclass(frozen=True)
class AdjustmentRequest:
    direction: Adjustment


class ZOffsetCommand(CommandKey):
    __key_namespace__ = "ui.pages.z_offset.actions.ZOffsetCommand"
    ZONE_REAR_LEFT = "z.zone.rear_left"
    ZONE_CENTER = "z.zone.center"
    ZONE_REAR_RIGHT = "z.zone.rear_right"
    ZONE_FRONT_LEFT = "z.zone.front_left"
    ZONE_FRONT_RIGHT = "z.zone.front_right"
    SELECTION_NEXT = "z.selection.next"
    SAVE = "z.save"
    DISCARD_CONFIRM = "z.discard.confirm"
    ENTER_ZONE = "z.paper_briefing.continue"
    PROBE = "z.probe"
    MOVE_SAFE_HALF = "z.move_safe_half"
    CLOSER = "z.closer"
    FARTHER = "z.farther"
    RESET = "z.reset"
    ACCEPT = "z.accept"
    SAFE_CALIBRATE = "z.safe.calibrate"
    SAFE_SKIP = "z.safe.skip"
    SAFE_PROBE = "z.safe.probe"
    SAFE_HIGHER = "z.safe.higher"
    SAFE_LOWER = "z.safe.lower"
    SAFE_SAVE = "z.safe.save"


_ZONE_KEYS = {
    Zone.REAR_LEFT: ZOffsetCommand.ZONE_REAR_LEFT,
    Zone.CENTER: ZOffsetCommand.ZONE_CENTER,
    Zone.REAR_RIGHT: ZOffsetCommand.ZONE_REAR_RIGHT,
    Zone.FRONT_LEFT: ZOffsetCommand.ZONE_FRONT_LEFT,
    Zone.FRONT_RIGHT: ZOffsetCommand.ZONE_FRONT_RIGHT,
}


def select_zone(zone):
    return Command(_ZONE_KEYS[zone], ZoneRequest(zone))


ZONE_ACTIONS = dict((zone.value, select_zone(zone)) for zone in Zone)
SELECTION_NEXT = Command(ZOffsetCommand.SELECTION_NEXT)
SAVE = Command(ZOffsetCommand.SAVE)
DISCARD_CONFIRM = Command(ZOffsetCommand.DISCARD_CONFIRM)
ENTER_ZONE = Command(ZOffsetCommand.ENTER_ZONE)
PROBE = Command(ZOffsetCommand.PROBE, hint=ProbingHint(axis="z"))
MOVE_SAFE_HALF = Command(
    ZOffsetCommand.MOVE_SAFE_HALF,
    hint=MovementHint(axis="z", speed=600))
CLOSER = Command(
    ZOffsetCommand.CLOSER, AdjustmentRequest(Adjustment.CLOSER),
    hint=MovementHint(axis="z"))
FARTHER = Command(
    ZOffsetCommand.FARTHER, AdjustmentRequest(Adjustment.FARTHER),
    hint=MovementHint(axis="z"))
RESET = Command(ZOffsetCommand.RESET)
ACCEPT = Command(ZOffsetCommand.ACCEPT)
SAFE_CALIBRATE = Command(ZOffsetCommand.SAFE_CALIBRATE)
SAFE_SKIP = Command(ZOffsetCommand.SAFE_SKIP)
SAFE_PROBE = Command(
    ZOffsetCommand.SAFE_PROBE, hint=ProbingHint(axis="z"))
SAFE_HIGHER = Command(
    ZOffsetCommand.SAFE_HIGHER,
    hint=MovementHint(axis="z", distance=1.0, speed=300))
SAFE_LOWER = Command(
    ZOffsetCommand.SAFE_LOWER,
    hint=MovementHint(axis="z", distance=-1.0, speed=300))
SAFE_SAVE = Command(ZOffsetCommand.SAFE_SAVE)
