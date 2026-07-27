## Typed state used by movement pages.

from ui.bindings import state
from ui.identity import StateKey


class ToolheadState(StateKey):
    __key_namespace__ = "ui.pages.move.state.ToolheadState"
    X = state(float, default=0.0, minimum=-110.0, maximum=110.0,
              unit="mm", category="toolhead",
              simulation_role="position.x", simulation_home=110.0)
    Y = state(float, default=0.0, minimum=-110.0, maximum=110.0,
              unit="mm", category="toolhead",
              simulation_role="position.y", simulation_home=110.0)
    Z = state(float, default=0.0, minimum=0.0, maximum=220.0,
              unit="mm", category="toolhead",
              simulation_role="position.z", simulation_home=220.0)
    HOMED_X = state(bool, default=False, category="toolhead",
                    simulation_role="homed.x")
    HOMED_Y = state(bool, default=False, category="toolhead",
                    simulation_role="homed.y")
    HOMED_Z = state(bool, default=False, category="toolhead",
                    simulation_role="homed.z")


class MoveState(StateKey):
    __key_namespace__ = "ui.pages.move.state.MoveState"
    JOG_STEP = state(
        float, default=1.0, choices=(0.1, 1.0, 10.0), unit="mm",
        category="movement", simulation_role="movement.step")
    INERTIA = state(float, default=0.0, minimum=0.0,
                    category="movement", simulation_role="movement.inertia")
    CURSOR = state(tuple, default=None, mutable=False,
                   category="movement", simulation_role="input.cursor")
    CAUTION_ACKNOWLEDGED = state(bool, default=False, category="safety")
    CAUTION_Z = state(float, default=5.0, minimum=0.0, unit="mm",
                      mutable=False, category="safety")
    AUTO_PROFILE_STATE = state(
        str, default="missing", choices=("missing", "available", "active"),
        category="safety")


def snapshot_values(snapshot):
    """Convert the existing controller snapshot into typed state values."""
    x, y, z, _status, homed_xy, homed_z = snapshot
    return {
        ToolheadState.X: float(x),
        ToolheadState.Y: float(y),
        ToolheadState.Z: float(z),
        ToolheadState.HOMED_X: bool(homed_xy),
        ToolheadState.HOMED_Y: bool(homed_xy),
        ToolheadState.HOMED_Z: bool(homed_z),
    }
