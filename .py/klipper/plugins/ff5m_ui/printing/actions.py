## Semantic actions for the declarative active-print page.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from dataclasses import dataclass
from enum import Enum

from ui.actions import Action


class PrintingRoute(Enum):
    HOME = "nav.home"
    PAUSE = "print.pause"
    RESUME = "print.resume"
    FILAMENT = "print.filament"
    Z_ADJUST = "print.z"
    CANCEL = "print.cancel"
    MORE = "print.more"
    MORE_CLOSE = "more.close"
    MORE_FILAMENT = "more.filament"
    MORE_EXCLUDE = "more.exclude"
    EXCLUDE_BACK = "exclude.back"
    EXCLUDE_PREV = "exclude.prev"
    EXCLUDE_NEXT = "exclude.next"
    EXCLUDE_REQUEST = "exclude.request"


@dataclass(frozen=True)
class PrintingAction(Action):
    """Typed print-page action preserving the established wire identifier."""

    route: PrintingRoute
    kind = "printing_action"

    def __post_init__(self):
        if not isinstance(self.route, PrintingRoute):
            raise TypeError("PrintingAction route must be a PrintingRoute member")

    @property
    def wire_id(self):
        return self.route.value

    def as_dict(self):
        return {"kind": self.kind, "route": self.route.value}


HOME = PrintingAction(PrintingRoute.HOME)
PAUSE = PrintingAction(PrintingRoute.PAUSE)
RESUME = PrintingAction(PrintingRoute.RESUME)
FILAMENT = PrintingAction(PrintingRoute.FILAMENT)
Z_ADJUST = PrintingAction(PrintingRoute.Z_ADJUST)
CANCEL = PrintingAction(PrintingRoute.CANCEL)
MORE = PrintingAction(PrintingRoute.MORE)


__all__ = (
    "CANCEL", "FILAMENT", "HOME", "MORE", "PAUSE", "RESUME", "Z_ADJUST",
    "PrintingAction", "PrintingRoute",
)

