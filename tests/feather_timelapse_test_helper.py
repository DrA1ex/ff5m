## Timelapse status fixtures built on the shipped macro defaults.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from pathlib import Path
from types import SimpleNamespace

from tests.gcode_macro_harness import load_macro


_MACROS = Path(__file__).parents[1] / "macros/timelapse.cfg"
_DEFAULTS = {name: load_macro(_MACROS, name).variables
             for name in ("_TIMELAPSE_START_GUARD", "TIMELAPSE_TAKE_FRAME")}


def _macro_status(name, override):
    def get_status(eventtime):
        current = (override.get_status(eventtime)
                   if hasattr(override, "get_status") else override or {})
        return {**_DEFAULTS[name], **current}
    return SimpleNamespace(get_status=get_status)


def make_timelapse_state(guard=None, frame=None, paused=False):
    """Return the real observer over shipped macro variables.

    Each override is a dict or a live status object, so a test can change the
    observed guard, frame, or pause state after the screen was built.
    """
    # Loading feather_screen puts the plugin root on sys.path.
    from timelapse_state import TimelapseState

    pause = (paused if hasattr(paused, "get_status")
             else SimpleNamespace(get_status=lambda eventtime: {"is_paused": bool(paused)}))
    objects = {
        "gcode_macro _TIMELAPSE_START_GUARD": _macro_status("_TIMELAPSE_START_GUARD", guard),
        "gcode_macro TIMELAPSE_TAKE_FRAME": _macro_status("TIMELAPSE_TAKE_FRAME", frame),
        "pause_resume": pause,
    }
    return TimelapseState(SimpleNamespace(lookup_object=objects.__getitem__))
