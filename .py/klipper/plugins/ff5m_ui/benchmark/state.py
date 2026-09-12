## Typed state for the render benchmark page.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from collections import namedtuple

from ui.bindings import state
from ui.identity import StateKey

from .constants import BENCHMARK_MODES


BenchmarkStats = namedtuple(
    "BenchmarkStats",
    (
        "commit_fps",
        "frame_median_ms",
        "frame_p95_ms",
        "typer_ms",
        "cpu_ms",
        "flush_ms",
        "python_ms",
        "missed_percent",
        "raster",
    ),
)


class BenchmarkState(StateKey):
    __key_namespace__ = "ui.pages.benchmark.state.BenchmarkState"

    ANGLE_X = state(float, default=0.0)
    ANGLE_Y = state(float, default=0.0)
    ANGLE_Z = state(float, default=0.0)
    MODE = state(str, default=BENCHMARK_MODES[0], choices=BENCHMARK_MODES)
    COMMIT_FPS = state(float, default=None, unit="fps", category="benchmark")
    FRAME_MEDIAN_MS = state(
        float, default=None, unit="ms", category="benchmark")
    FRAME_P95_MS = state(
        float, default=None, unit="ms", category="benchmark")
    TYPER_MS = state(float, default=None, unit="ms", category="benchmark")
    CPU_MS = state(float, default=None, unit="ms", category="benchmark")
    FLUSH_MS = state(float, default=None, unit="ms", category="benchmark")
    PYTHON_MS = state(float, default=None, unit="ms", category="benchmark")
    MISSED_PERCENT = state(
        float, default=None, unit="percent", category="benchmark")
    RASTER = state(str, default="SCALAR", category="benchmark")
    STATUS = state(str, default="WARMUP 0/30")


def empty_stats(raster):
    return BenchmarkStats(
        commit_fps=None,
        frame_median_ms=None,
        frame_p95_ms=None,
        typer_ms=None,
        cpu_ms=None,
        flush_ms=None,
        python_ms=None,
        missed_percent=None,
        raster=str(raster),
    )


def stats_values(stats):
    if not isinstance(stats, BenchmarkStats):
        raise TypeError("stats must be a BenchmarkStats value")
    return {
        BenchmarkState.COMMIT_FPS: stats.commit_fps,
        BenchmarkState.FRAME_MEDIAN_MS: stats.frame_median_ms,
        BenchmarkState.FRAME_P95_MS: stats.frame_p95_ms,
        BenchmarkState.TYPER_MS: stats.typer_ms,
        BenchmarkState.CPU_MS: stats.cpu_ms,
        BenchmarkState.FLUSH_MS: stats.flush_ms,
        BenchmarkState.PYTHON_MS: stats.python_ms,
        BenchmarkState.MISSED_PERCENT: stats.missed_percent,
        BenchmarkState.RASTER: stats.raster,
    }


__all__ = (
    "BenchmarkState", "BenchmarkStats", "empty_stats", "stats_values",
)
