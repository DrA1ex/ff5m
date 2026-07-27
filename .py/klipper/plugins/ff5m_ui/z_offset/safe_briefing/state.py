## Typed state used by the Safe Z briefing page.

from ui.bindings import state
from ui.identity import StateKey


class SafeBriefingState(StateKey):
    __key_namespace__ = "ui.pages.z_offset.safe_briefing.state.SafeBriefingState"
    CURRENT = state(float, default=10.0, minimum=0.0, unit="mm",
                    mutable=False, category="z_offset")
    START = state(float, default=20.0, minimum=0.0, unit="mm",
                  mutable=False, category="z_offset")
