## Typed state used by the Z-offset paper briefing page.

from ui.bindings import state
from ui.identity import StateKey


class PaperBriefingState(StateKey):
    __key_namespace__ = "ui.pages.z_offset.paper_briefing.state.PaperBriefingState"
    ZONE_LABEL = state(str, default="", mutable=False, category="z_offset")
    MANUAL_START = state(float, default=5.0, minimum=0.0, unit="mm",
                         mutable=False, category="z_offset")
