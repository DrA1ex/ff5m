## Typed telemetry for the declarative Heat/Fan page.

from ui.bindings import state
from ui.identity import StateKey


class HeatState(StateKey):
    __key_namespace__ = "ui.pages.heat.state.HeatState"
    NOZZLE = state(float, default=0.0, unit="C", category="temperature")
    NOZZLE_TARGET = state(
        float, default=0.0, minimum=0.0, unit="C", category="temperature")
    BED = state(float, default=0.0, unit="C", category="temperature")
    BED_TARGET = state(
        float, default=0.0, minimum=0.0, unit="C", category="temperature")
    FAN = state(float, default=0.0, minimum=0.0, maximum=100.0,
                unit="percent", category="fan")
    FAN_AVAILABLE = state(bool, default=False, category="fan")

