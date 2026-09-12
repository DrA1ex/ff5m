## Typed semantic actions for the Feather home dashboard.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from dataclasses import dataclass
from enum import Enum

from ui.actions import Action


class HomeRoute(Enum):
    MENU = "nav.menu"
    HEAT = "nav.heat"
    NETWORK = "nav.network"
    JOB = "nav.job"
    LAST_JOB = "home.last_job"
    FILAMENT = "nav.filament"
    MOVE = "nav.move"


@dataclass(frozen=True)
class HomeNavigate(Action):
    """Typed dashboard route preserving the established wire identifiers."""

    route: HomeRoute
    kind = "home_navigate"

    def __post_init__(self):
        if not isinstance(self.route, HomeRoute):
            raise TypeError("HomeNavigate route must be a HomeRoute member")

    @property
    def wire_id(self):
        return self.route.value

    def as_dict(self):
        return {"kind": self.kind, "route": self.route.value}


MENU = HomeNavigate(HomeRoute.MENU)
HEAT = HomeNavigate(HomeRoute.HEAT)
NETWORK = HomeNavigate(HomeRoute.NETWORK)
JOB = HomeNavigate(HomeRoute.JOB)
LAST_JOB = HomeNavigate(HomeRoute.LAST_JOB)
FILAMENT = HomeNavigate(HomeRoute.FILAMENT)
MOVE = HomeNavigate(HomeRoute.MOVE)


__all__ = (
    "FILAMENT", "HEAT", "JOB", "LAST_JOB", "MENU", "MOVE", "NETWORK",
    "HomeNavigate", "HomeRoute",
)
