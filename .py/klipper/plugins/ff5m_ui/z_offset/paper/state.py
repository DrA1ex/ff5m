## Typed state used by the Z-offset paper-test page.

from ui.bindings import state
from ui.identity import StateKey
from ..constants import PAPER_DEFAULT_STEP, PAPER_STEPS


class PaperState(StateKey):
    __key_namespace__ = "ui.pages.z_offset.paper.state.PaperState"
    MANUAL = state(bool, default=False, category="z_offset")
    REFERENCE = state(str, default="--", mutable=False, unit="mm",
                      category="z_offset")
    NOZZLE = state(str, default="--", mutable=False, unit="mm",
                   category="z_offset")
    CANDIDATE = state(str, default="--", mutable=False, unit="mm",
                      category="z_offset")
    PROBING = state(bool, default=False, mutable=False, category="z_offset")
    MOVING_TO_START = state(bool, default=False, mutable=False,
                            category="z_offset")
    STEP = state(float, default=PAPER_DEFAULT_STEP,
                 choices=PAPER_STEPS, unit="mm",
                 category="z_offset")
    MANUAL_START = state(float, default=5.0, minimum=0.0, unit="mm",
                         mutable=False, category="z_offset")
    READY = state(bool, default=False, mutable=False, category="z_offset")
    GAUGE = state(dict, default=None, mutable=False, category="z_offset")
    DIALOG = state(str, default=None, category="z_offset")
    DIALOG_WEIGHT = state(float, default=0.0, minimum=0.0, unit="g",
                          mutable=False, category="z_offset")
