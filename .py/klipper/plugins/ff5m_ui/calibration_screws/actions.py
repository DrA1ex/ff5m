## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from dataclasses import dataclass
from enum import Enum

from ui.actions import Action


class ScrewResultRoute(Enum):
    REPEAT = "cal.repeat"
    DONE = "cal.done"


@dataclass(frozen=True)
class ScrewResultAction(Action):
    route: ScrewResultRoute
    kind = "screw_result"

    def __post_init__(self):
        if not isinstance(self.route, ScrewResultRoute):
            raise TypeError("ScrewResultAction route must be ScrewResultRoute")

    @property
    def wire_id(self):
        return self.route.value

    def as_dict(self):
        return {"kind": self.kind, "route": self.route.value}


REPEAT = ScrewResultAction(ScrewResultRoute.REPEAT)
DONE = ScrewResultAction(ScrewResultRoute.DONE)
