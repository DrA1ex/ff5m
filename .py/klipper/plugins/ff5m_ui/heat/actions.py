## Semantic actions for the declarative Heat/Fan page.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
## This file may be distributed under the terms of the GNU GPLv3 license

from ui.actions import Command, CoolingHint, HeatingHint
from ui.identity import CommandKey


class HeatCommand(CommandKey):
    __key_namespace__ = "ui.pages.heat.actions.HeatCommand"
    NOZZLE_MINUS = "heat.nozzle.minus"
    NOZZLE_PLUS = "heat.nozzle.plus"
    NOZZLE_OFF = "heat.nozzle.off"
    BED_MINUS = "heat.bed.minus"
    BED_PLUS = "heat.bed.plus"
    BED_OFF = "heat.bed.off"
    EDIT_NOZZLE_TARGET = "heat.nozzle.edit_target"
    EDIT_BED_TARGET = "heat.bed.edit_target"
    TARGET_KEY = "heat.target.key"
    APPLY_TARGET = "heat.target.apply"
    CANCEL_TARGET = "heat.target.cancel"
    FAN_0 = "heat.fan.0"
    FAN_50 = "heat.fan.50"
    FAN_100 = "heat.fan.100"
    PREHEAT = "heat.preheat"
    COOLDOWN = "heat.cooldown"


NOZZLE_MINUS = Command(
    HeatCommand.NOZZLE_MINUS, hint=HeatingHint("extruder", "-5"))
NOZZLE_PLUS = Command(
    HeatCommand.NOZZLE_PLUS, hint=HeatingHint("extruder", "+5"))
NOZZLE_OFF = Command(
    HeatCommand.NOZZLE_OFF, hint=CoolingHint("extruder"))
BED_MINUS = Command(
    HeatCommand.BED_MINUS, hint=HeatingHint("heater_bed", "-5"))
BED_PLUS = Command(
    HeatCommand.BED_PLUS, hint=HeatingHint("heater_bed", "+5"))
BED_OFF = Command(
    HeatCommand.BED_OFF, hint=CoolingHint("heater_bed"))
EDIT_NOZZLE_TARGET = Command(HeatCommand.EDIT_NOZZLE_TARGET)
EDIT_BED_TARGET = Command(HeatCommand.EDIT_BED_TARGET)
APPLY_TARGET = Command(HeatCommand.APPLY_TARGET)
CANCEL_TARGET = Command(HeatCommand.CANCEL_TARGET)
FAN_0 = Command(HeatCommand.FAN_0)
FAN_50 = Command(HeatCommand.FAN_50)
FAN_100 = Command(HeatCommand.FAN_100)
COOLDOWN = Command(HeatCommand.COOLDOWN, hint=CoolingHint())


def preheat(material):
    return Command(
        HeatCommand.PREHEAT, str(material),
        hint=HeatingHint("material", str(material)))

