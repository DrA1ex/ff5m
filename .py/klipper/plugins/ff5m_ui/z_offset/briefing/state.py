## Typed state used by the declarative Z-offset briefing page.

from ui.bindings import state
from ui.identity import StateKey


class BriefingState(StateKey):
    __key_namespace__ = "ui.pages.z_offset.briefing.state.BriefingState"
    SAFE_Z = state(float, default=10.0, minimum=0.0, unit="mm",
                   mutable=False, category="z_offset")
