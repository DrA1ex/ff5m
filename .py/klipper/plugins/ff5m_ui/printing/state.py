## Typed telemetry for the declarative active-print page.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from ui.bindings import state
from ui.identity import StateKey


class PrintingState(StateKey):
    __key_namespace__ = "ui.pages.printing.state.PrintingState"
    FILENAME = state(str, default="Unknown", category="job")
    STATUS = state(str, default="", category="job")
    PROGRESS = state(int, default=0, minimum=0, maximum=100,
                     unit="percent", category="job")
    ELAPSED = state(str, default="--:--:--", category="job")
    REMAINING = state(str, default="--:--:--", category="job")
    LAYER = state(str, default="? / ?", category="job")
    HEIGHT = state(str, default="0.00 MM", category="job")
    PAUSED = state(bool, default=False, category="job")
    CONTROLS_READY = state(bool, default=True, category="job")
    PENDING_ACTION = state(str, default="", category="job")
    LIVE_Z_ALLOWED = state(bool, default=False, category="job")
    PREVIEW_STATUS = state(
        str, default="none", choices=("none", "loading", "ready", "failed"),
        category="preview")

