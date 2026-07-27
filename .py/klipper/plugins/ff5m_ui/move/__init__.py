from .actions import Axis, MoveCommand


def __getattr__(name):
    if name != "STEP_VALUES":
        raise AttributeError("module %r has no attribute %r" % (__name__, name))
    from .common import STEP_VALUES
    globals()[name] = STEP_VALUES
    return STEP_VALUES
