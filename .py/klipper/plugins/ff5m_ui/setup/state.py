## Commands and typed state of the first-run setup pages.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from ui.bindings import state
from ui.identity import CommandKey, StateKey


class SetupCommand(CommandKey):
    __key_namespace__ = "ff5m.setup.commands"
    ## The hub and the calibration list carry the step they open as a payload.
    OPEN = "setup.open"
    ## The main action of the page below its content.
    ACTION = "setup.action"
    ## Postpone setup and return to the calling screen.
    LEAVE = "setup.leave"
    ## One option row: its switch, its editor, and its explanation.
    TOGGLE = "setup.option.toggle"
    EDIT = "setup.option.edit"
    HELP = "setup.option.help"
    ## List paging, shared by the option list and the timezone city list.
    PREVIOUS = "setup.page.previous"
    NEXT = "setup.page.next"
    ## One timezone choice: a region, a letter range, a city or a fixed offset.
    ZONE = "setup.zone"


class HubState(StateKey):
    __key_namespace__ = "ff5m.setup.hub"
    ROWS = state(list, default=[
        dict(id="timezone", kind="plain", title="TIME ZONE",
             status="Europe/Kyiv - UTC+02:00 - 16:45", chip="CHANGE"),
        dict(id="options", kind="advised", title="ESSENTIAL OPTIONS",
             status="2 recommended changes available", chip="REVIEW"),
        dict(id="additional", kind="plain", title="ADDITIONAL OPTIONS",
             status="Print preparation, protection, camera and lighting.", chip="REVIEW"),
        dict(id="calibration", kind="required", title="CALIBRATION",
             status="Save a bed mesh before finishing setup.", chip="REQUIRED"),
    ])
    ACTION = state(str, default="NOT FINISHED")
    ACTION_STATE = state(str, default="disabled")
    EXIT = state(str, default="I'LL SET IT UP MYSELF")


class OptionGroupsState(StateKey):
    __key_namespace__ = "ff5m.setup.option_groups"
    ROWS = state(list, default=[
        dict(id="group:protection", kind="group", title="PROTECTION", status="Recovery and sensors"),
        dict(id="group:mesh", kind="group", title="BED MESH USE", status="Leveling and mesh validation"),
        dict(id="group:preparation", kind="group", title="PRINT PREPARATION", status="Priming and nozzle cleaning"),
        dict(id="group:peripherals", kind="group", title="PERIPHERALS",
             status="Camera, chamber light and screen sharing"),
    ])
    ACTION = state(str, default="REVIEW CALIBRATION")
    PAGE = state(int, default=0)
    PAGE_COUNT = state(int, default=1)
    PREVIOUS = state(str, default="disabled")
    NEXT = state(str, default="disabled")


class OptionsState(StateKey):
    __key_namespace__ = "ff5m.setup.options"
    ROWS = state(list, default=[
        dict(id="tune_klipper", kind="toggle", title="KLIPPER TUNING",
             note="Fewer communication errors while printing.", chip="", active=True, value=""),
        dict(id="tune_config", kind="toggle-advised", title="PRINTER TUNING",
             note="Better first layer. Recalibrate the mesh after.", chip="SUGGEST ON", active=False, value=""),
        dict(id="load_zoffset", kind="toggle-advised",
             title="RESTORE Z OFFSET", note="Applies the saved Z offset after every restart.",
             chip="", active=False, value="OFF"),
        dict(id="check_md5", kind="toggle", title="MD5 VERIFICATION",
             note="Check files from a slicer with addMD5.", chip="", active=True, value=""),
    ])
    ACTION = state(str, default="ADDITIONAL OPTIONS")
    EMPTY_MESSAGE = state(str, default="")
    PAGE = state(int, default=0)
    PAGE_COUNT = state(int, default=1)
    PREVIOUS = state(str, default="disabled")
    NEXT = state(str, default="disabled")


class CalibrationState(StateKey):
    __key_namespace__ = "ff5m.setup.calibration"
    ROWS = state(list, default=[
        dict(id="z_offset", kind="advised", title="SAFE Z", enabled=True,
             status="Saved Safe Z: 10.000 mm", note="Sets the height for safe travel over the bed.",
             chip="RECOMMENDED"),
        dict(id="mesh", kind="required", title="BED MESH", enabled=True,
             status="No saved mesh", note="Measures the whole bed surface. Takes a few minutes.",
             chip="REQUIRED"),
        dict(id="shaper", kind="optional", title="INPUT SHAPER", enabled=True,
             status="Not measured", note="Reduces ringing at higher speeds.", chip="OPTIONAL"),
    ])
    ACTION = state(str, default="NOT FINISHED")
    ACTION_STATE = state(str, default="disabled")


class TimezoneState(StateKey):
    __key_namespace__ = "ff5m.setup.timezone"
    FIXED = state(bool, default=False)
    CURRENT_NAME = state(tuple, default=("Europe", "Tallinn"))
    CURRENT_CAPTION = state(str, default="CURRENT · UTC+02:00")
    ACTIONS = state(list, default=[dict(id="fixed")])
    ## Region, letter-range and offset choices are a grid; cities are a list.
    ROWS = state(list, default=[
        dict(id="0", kind="grid3", label0="AFRICA", zone0="Africa", state0="enabled", note0=None,
             label1="AMERICA", zone1="America", state1="enabled", note1=None,
             label2="ASIA", zone2="Asia", state2="enabled", note2=None),
        dict(id="1", kind="grid3", label0="AUSTRALIA", zone0="Australia", state0="enabled", note0=None,
             label1="EUROPE", zone1="Europe", state1="selected", note1=None,
             label2="PACIFIC", zone2="Pacific", state2="enabled", note2=None),
        dict(id="2", kind="grid3", label0="ATLANTIC", zone0="Atlantic", state0="enabled", note0=None,
             label1="INDIAN", zone1="Indian", state1="enabled", note1=None,
             label2="ANTARCTICA", zone2="Antarctica", state2="enabled", note2=None),
    ])
