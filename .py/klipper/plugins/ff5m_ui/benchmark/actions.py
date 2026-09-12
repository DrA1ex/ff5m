"""Semantic actions for the render benchmark page."""

from dataclasses import dataclass
from enum import Enum

from ui.actions import Action


class BenchmarkRoute(Enum):
    NEXT_MODE = "benchmark.mode.next"


@dataclass(frozen=True)
class BenchmarkAction(Action):
    route: BenchmarkRoute
    kind = "benchmark_action"

    def __post_init__(self):
        if not isinstance(self.route, BenchmarkRoute):
            raise TypeError("BenchmarkAction route must be a BenchmarkRoute member")

    @property
    def wire_id(self):
        return self.route.value

    def as_dict(self):
        return {"kind": self.kind, "route": self.route.value}


NEXT_MODE = BenchmarkAction(BenchmarkRoute.NEXT_MODE)


__all__ = (
    "BenchmarkAction", "BenchmarkRoute", "NEXT_MODE",
)
