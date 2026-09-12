## Lightweight public facade for Z-offset constants and actions.

from ui.lazy import resolve_lazy_export
from .constants import PAPER_DEFAULT_STEP, PAPER_STEPS, Z_WEIGHT_DANGER


_LAZY_EXPORTS = {
    "Adjustment": "actions",
    "Zone": "actions",
    "ZOffsetCommand": "actions",
}


__all__ = (
    "Adjustment", "Zone", "ZOffsetCommand",
    "PAPER_DEFAULT_STEP", "PAPER_STEPS", "Z_WEIGHT_DANGER",
)


def __getattr__(name):
    return resolve_lazy_export(
        globals(), name, _LAZY_EXPORTS, __package__)
