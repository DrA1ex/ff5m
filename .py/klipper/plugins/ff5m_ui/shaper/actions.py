# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
# This file may be distributed under the terms of the GNU GPLv3 license.

from dataclasses import dataclass
from ui.actions import Action


@dataclass(frozen=True)
class ShaperAction(Action):
    route: str
    value: str = ""
    kind = "shaper_result"

    @property
    def wire_id(self):
        return "cal.shaper.%s%s" % (self.route, ("." + self.value) if self.value else "")

    def as_dict(self):
        return {"kind": self.kind, "route": self.route, "value": self.value}


@dataclass(frozen=True)
class ShaperCancelAction(Action):
    kind = "shaper_cancel"
    wire_id = "cal.cancel"

    def as_dict(self):
        return {"kind": self.kind}


CANCEL = ShaperCancelAction()
