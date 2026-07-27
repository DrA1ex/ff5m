## Typed state used by the Safe Z calibration page.

from ui.bindings import state
from ui.identity import StateKey


class SafeState(StateKey):
    __key_namespace__ = "ui.pages.z_offset.safe.state.SafeState"
    CURRENT = state(float, default=10.0, minimum=0.0, unit="mm",
                    mutable=False, category="z_offset")
    TRIGGER = state(float, default=None, unit="mm", mutable=False,
                    category="z_offset")
    CANDIDATE = state(float, default=None, unit="mm", mutable=False,
                      category="z_offset")
    PROBING = state(bool, default=False, mutable=False, category="z_offset")
    READY = state(bool, default=False, mutable=False, category="z_offset")
