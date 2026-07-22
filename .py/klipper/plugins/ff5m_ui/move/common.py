## Shared movement layout constants.

from ui.actions import SetValue
from ui.bindings import bind, derived
from ui.components import ButtonStyle, Dialog
from ui.layout import Overlay, Override, Rect, When
from .actions import CAUTION_AUTO, CAUTION_UNLOAD
from .state import MoveState, ToolheadState


MOVE_CONTENT = Rect(12, 64, 776, 364)
FONT = "JetBrainsMono 8pt"
COMPACT_BUTTONS = ButtonStyle(font=FONT)
STEP_VALUES = (0.1, 1.0, 10.0)


def compact(content):
    return Override(content).with_button_style(COMPACT_BUTTONS).apply()


def _caution_visible(z, homed_z, acknowledged):
    return bool(homed_z) and float(z) < 5.0 and not bool(acknowledged)


def _caution_for(profile):
    return lambda z, homed_z, acknowledged, current: (
        _caution_visible(z, homed_z, acknowledged) and current == profile)


CAUTION_WIDTH = 420
CAUTION_HEIGHT = 266


def caution_layers():
    """Product-owned low-Z warning rendered by the normal page tree.

    The external Designer sees this only through framework reflection, typed
    state and semantic actions. No preview-only warning implementation exists.
    """
    inputs = (
        bind(ToolheadState.Z),
        bind(ToolheadState.HOMED_Z),
        bind(MoveState.CAUTION_ACKNOWLEDGED),
        bind(MoveState.AUTO_PROFILE_STATE),
    )
    dismiss = SetValue(MoveState.CAUTION_ACKNOWLEDGED, True)
    return (
        When(derived(_caution_for("active"), *inputs), Dialog(
            "CAUTION",
            ("Z IS BELOW 5 MM", "XY MOTION MAY SCRATCH THE BED",
             "BED PROFILE 'AUTO' IS LOADED"),
            ((CAUTION_UNLOAD, "UNLOAD", "warning"),
             (dismiss, "OK", "enabled")),
            tone="warning", modal=False,
        )),
        When(derived(_caution_for("available"), *inputs), Dialog(
            "CAUTION",
            ("Z IS BELOW 5 MM", "XY MOTION MAY SCRATCH THE BED",
             "LOAD BED PROFILE 'AUTO'?"),
            ((dismiss, "CONTINUE", "enabled"),
             (CAUTION_AUTO, "LOAD", "warning")),
            tone="warning", modal=False,
        )),
        When(derived(_caution_for("missing"), *inputs), Dialog(
            "CAUTION",
            ("Z IS BELOW 5 MM", "XY MOTION MAY SCRATCH THE BED",
             "PROFILE 'AUTO' IS NOT AVAILABLE"),
            ((dismiss, "CONTINUE", "enabled"),),
            tone="warning", modal=False,
        )),
    )


def caution_overlay():
    """Product-owned warning composition shared by Move page layouts."""
    return Overlay(*caution_layers())
