## Observed timelapse contract shared by the screen and G-code macros.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from enum import Enum


class TimelapsePhase(Enum):
    NONE = "NONE"
    WAITING = "WAITING"
    HELD = "HELD"
    FRAME = "FRAME"
    FRAME_USER_PAUSE = "FRAME_USER_PAUSE"
    USER_PAUSE = "USER_PAUSE"


class TimelapseState:
    """Read required macro fields; a broken contract must never look idle."""

    def __init__(self, printer):
        self.printer = printer

    def _macro_status(self, name, fields, eventtime):
        status = self.printer.lookup_object("gcode_macro " + name).get_status(eventtime)
        missing = set(fields) - status.keys()
        if missing:
            raise RuntimeError("Timelapse contract: %s is missing %s"
                               % (name, ", ".join(sorted(missing))))
        return status

    def get_status(self, eventtime):
        guard = self._macro_status(
            "_TIMELAPSE_START_GUARD", ("waiting", "sd_held", "wait_status"), eventtime)
        frame = self._macro_status(
            "TIMELAPSE_TAKE_FRAME", ("is_paused", "user_pause_requested"), eventtime)
        paused = self.printer.lookup_object("pause_resume").get_status(eventtime)["is_paused"]
        user_pause = frame["user_pause_requested"] and paused
        if frame["is_paused"]:
            phase = TimelapsePhase.FRAME_USER_PAUSE if user_pause else TimelapsePhase.FRAME
        elif user_pause:
            phase = TimelapsePhase.USER_PAUSE
        elif guard["waiting"]:
            phase = TimelapsePhase.WAITING
        elif guard["sd_held"]:
            phase = TimelapsePhase.HELD
        else:
            phase = TimelapsePhase.NONE
        return {"phase": phase.value, "wait_status": guard["wait_status"],
                "sd_held": guard["sd_held"],
                "release_ready": not (frame["is_paused"] or paused
                                       or frame["user_pause_requested"])}


def load_config(config):
    printer = config.get_printer()
    state = TimelapseState(printer)
    printer.register_event_handler("klippy:connect", lambda:
        state.get_status(printer.get_reactor().monotonic()))
    return state
