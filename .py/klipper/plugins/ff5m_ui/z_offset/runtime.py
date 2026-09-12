## Runtime facade for Z-offset page packages.

from ui.lazy import resolve_lazy_export
from .actions import (
    ACCEPT, CLOSER, DISCARD_CONFIRM, ENTER_ZONE, FARTHER, MOVE_SAFE_HALF,
    PROBE, RESET, SAFE_CALIBRATE, SAFE_HIGHER, SAFE_LOWER, SAFE_PROBE,
    SAFE_SAVE, SAFE_SKIP, SAVE, SELECTION_NEXT, Adjustment,
    AdjustmentRequest, ZONE_ACTIONS, Zone, ZoneRequest, ZOffsetCommand,
)
from .paper.state import PaperState
from .paper_briefing.state import PaperBriefingState
from .safe.state import SafeState
from .safe_briefing.state import SafeBriefingState
from .summary.state import SummaryState


_LAZY_EXPORTS = {
    "CONTENT": ("common", "CONTENT"),
    "FONT": ("common", "FONT"),
    "PAPER_DEFAULT_STEP": ("constants", "PAPER_DEFAULT_STEP"),
    "PAPER_STEPS": ("constants", "PAPER_STEPS"),
    "Z_WEIGHT_DANGER": ("constants", "Z_WEIGHT_DANGER"),
    "SAFE_BRIEFING_PAGE": ("safe_briefing.page", "PAGE"),
    "BRIEFING_PAGE": ("safe_briefing.page", "PAGE"),
    "render_safe_briefing": ("safe_briefing.page", "render"),
    "SAFE_PAGE": ("safe.page", "PAGE"),
    "render_safe": ("safe.page", "render"),
    "PAPER_PAGE": ("paper.page", "PAGE"),
    "render_paper": ("paper.page", "render"),
    "update_paper_gauge": ("paper.page", "update_gauge"),
    "PAPER_BRIEFING_PAGE": ("paper_briefing.page", "PAGE"),
    "render_paper_briefing": ("paper_briefing.page", "render"),
    "SUMMARY_PAGE": ("summary.page", "PAGE"),
    "render_summary": ("summary.page", "render"),
}


def __getattr__(name):
    return resolve_lazy_export(
        globals(), name, _LAZY_EXPORTS, __package__)
