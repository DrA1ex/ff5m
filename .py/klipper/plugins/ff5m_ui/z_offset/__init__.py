## Lightweight public facade for Z-offset constants and actions.

from .constants import PAPER_DEFAULT_STEP, PAPER_STEPS, Z_WEIGHT_DANGER


_LAZY_EXPORTS = {
    "Adjustment": ("actions", "Adjustment"),
    "Zone": ("actions", "Zone"),
    "ZOffsetCommand": ("actions", "ZOffsetCommand"),
}


__all__ = (
    "Adjustment", "Zone", "ZOffsetCommand",
    "PAPER_DEFAULT_STEP", "PAPER_STEPS", "Z_WEIGHT_DANGER",
)


def __getattr__(name):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError("module %r has no attribute %r" % (__name__, name))
    module = __import__("%s.%s" % (__package__, target[0]),
                        fromlist=(target[1],))
    value = getattr(module, target[1])
    globals()[name] = value
    return value
