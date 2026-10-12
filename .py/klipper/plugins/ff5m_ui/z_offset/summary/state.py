## Typed state used by the Z-offset summary page.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from ui.bindings import state
from ui.identity import StateKey


class SummaryState(StateKey):
    __key_namespace__ = "ui.pages.z_offset.summary.state.SummaryState"
    ZONE_LABELS = state(
        dict,
        default={
            "rear_left": "REAR LEFT",
            "center": "CENTER",
            "rear_right": "REAR RIGHT",
            "front_left": "FRONT LEFT",
            "front_right": "FRONT RIGHT",
        },
        mutable=False, category="z_offset")
    RESULTS = state(dict, default={}, mutable=False, category="z_offset")
    CAN_MEASURE = state(bool, default=True, mutable=False, category="z_offset")
    SPREAD = state(float, default=0.0, minimum=0.0, unit="mm",
                   category="z_offset")
    POSITIONAL_WARNING = state(float, default=0.025, minimum=0.0,
                               unit="mm", category="z_offset")
    SELECTED = state(str, default=None, category="z_offset")
    AVERAGE = state(float, default=None, unit="mm", category="z_offset")
    LOAD_ZOFFSET = state(bool, default=False, category="z_offset")
    DIALOG = state(str, default=None, category="z_offset")
