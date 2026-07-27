## Runtime facade for Z-offset page packages.

from .actions import (
    ACCEPT, CLOSER, DISCARD_CONFIRM, ENTER_ZONE, FARTHER, MOVE_1_5, PROBE,
    RESET, SAVE, SELECTION_NEXT, Adjustment, AdjustmentRequest, ZONE_ACTIONS,
    Zone, ZoneRequest, ZOffsetCommand,
)
from .paper.state import PaperState
from .paper_briefing.state import PaperBriefingState
from .summary.state import SummaryState


_LAZY_EXPORTS = {
    "CONTENT": ("common", "CONTENT"),
    "FONT": ("common", "FONT"),
    "PAPER_STEPS": ("constants", "PAPER_STEPS"),
    "Z_WEIGHT_DANGER": ("constants", "Z_WEIGHT_DANGER"),
    "BRIEFING_PAGE": ("briefing.page", "PAGE"),
    "render_briefing": ("briefing.page", "render"),
    "PAPER_PAGE": ("paper.page", "PAGE"),
    "render_paper": ("paper.page", "render"),
    "update_paper_gauge": ("paper.page", "update_gauge"),
    "PAPER_BRIEFING_PAGE": ("paper_briefing.page", "PAGE"),
    "render_paper_briefing": ("paper_briefing.page", "render"),
    "SUMMARY_PAGE": ("summary.page", "PAGE"),
    "render_summary": ("summary.page", "render"),
}


def __getattr__(name):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError("module %r has no attribute %r" % (__name__, name))
    module = __import__("%s.%s" % (__package__, target[0]),
                        fromlist=(target[1],))
    value = getattr(module, target[1])
    globals()[name] = value
    return value
