## Semantic actions for the declarative Heat/Fan page.

from enum import Enum

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
FAN_0 = Command(HeatCommand.FAN_0)
FAN_50 = Command(HeatCommand.FAN_50)
FAN_100 = Command(HeatCommand.FAN_100)
COOLDOWN = Command(HeatCommand.COOLDOWN, hint=CoolingHint())


def preheat(material):
    return Command(
        HeatCommand.PREHEAT, str(material),
        hint=HeatingHint("material", str(material)))


LEGACY_ACTIONS = {
    HeatCommand.NOZZLE_MINUS: "heat.eminus",
    HeatCommand.NOZZLE_PLUS: "heat.eplus",
    HeatCommand.NOZZLE_OFF: "heat.eoff",
    HeatCommand.BED_MINUS: "heat.bminus",
    HeatCommand.BED_PLUS: "heat.bplus",
    HeatCommand.BED_OFF: "heat.boff",
    HeatCommand.FAN_0: "heat.fan0",
    HeatCommand.FAN_50: "heat.fan50",
    HeatCommand.FAN_100: "heat.fan100",
    HeatCommand.COOLDOWN: "heat.alloff",
}

