## Declarative render benchmark page.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from enum import Enum

from ui import (
    FLEX, Column, Equal, Fill, Flex, Grid, Hitbox, Overlay, PageTree, Panel,
    Rect, Spacer, Stroke, Text, ThemeColor,
)
from ui.bindings import bind, derived

from ..keys import AppPage
from .actions import NEXT_MODE
from .components import TextCube
from .constants import BENCHMARK_MODES
from .state import BenchmarkState


PAGE_TITLE = "Render benchmark"
CONTENT = Rect(18, 56, 764, 386)
CUBE_WIDTH = 460
CUBE_HEIGHT = 300
STATS_WIDTH = 210
FONT = "JetBrainsMono 8pt"
VALUE_FONT = "JetBrainsMono Bold 8pt"


class BenchmarkRef(Enum):
    ROOT = "benchmark.root"
    SURFACE_LAYOUT = "benchmark.surface.layout"
    SURFACE = "benchmark.surface"
    SURFACE_BACKGROUND = "benchmark.surface.background"
    SURFACE_BORDER = "benchmark.surface.border"
    CUBE = "benchmark.cube"
    MODE_POSITION = "benchmark.mode.position"
    SWITCH_HINT = "benchmark.mode.switch_hint"
    MODE_HITBOX = "benchmark.mode.hitbox"
    STATS = "benchmark.stats"
    STATS_PANEL = "benchmark.stats.panel"
    STATS_LAYOUT = "benchmark.stats.layout"
    STATS_GRID = "benchmark.stats.grid"
    STATS_SPACER = "benchmark.stats.spacer"
    STATS_FOOTER = "benchmark.stats.footer"
    COMMIT_FPS_LABEL = "benchmark.stats.commit_fps.label"
    COMMIT_FPS_VALUE = "benchmark.stats.commit_fps.value"
    FRAME_MEDIAN_LABEL = "benchmark.stats.frame_median.label"
    FRAME_MEDIAN_VALUE = "benchmark.stats.frame_median.value"
    FRAME_P95_LABEL = "benchmark.stats.frame_p95.label"
    FRAME_P95_VALUE = "benchmark.stats.frame_p95.value"
    TYPER_LABEL = "benchmark.stats.typer.label"
    TYPER_VALUE = "benchmark.stats.typer.value"
    CPU_LABEL = "benchmark.stats.cpu.label"
    CPU_VALUE = "benchmark.stats.cpu.value"
    FLUSH_LABEL = "benchmark.stats.flush.label"
    FLUSH_VALUE = "benchmark.stats.flush.value"
    PYTHON_LABEL = "benchmark.stats.python.label"
    PYTHON_VALUE = "benchmark.stats.python.value"
    MISSED_LABEL = "benchmark.stats.missed.label"
    MISSED_VALUE = "benchmark.stats.missed.value"
    RASTER_LABEL = "benchmark.stats.raster.label"
    RASTER_VALUE = "benchmark.stats.raster.value"
    MODE = "benchmark.stats.mode"
    STATUS = "benchmark.stats.status"


def _format_number(value, digits=1, suffix=""):
    if value is None:
        return "--"
    return ("%5.*f%s" % (digits, float(value), suffix)).strip()


def _format_fps(value):
    return _format_number(value)


def _format_ms(value):
    return _format_number(value, suffix=" MS")


def _format_precise_ms(value):
    return _format_number(value, digits=2, suffix=" MS")


def _format_percent(value):
    return _format_number(value, suffix="%")


def _format_raster(value):
    return str(value).upper()


def _mode_label(mode):
    return str(mode).upper()


def _mode_position(mode):
    try:
        index = BENCHMARK_MODES.index(str(mode).lower())
    except ValueError:
        index = 0
    return "%d of %d" % (index + 1, len(BENCHMARK_MODES))


def _status_color(status):
    status = str(status).upper()
    if "WARMUP" in status or "QUEUE" in status:
        return ThemeColor.WARNING
    if "SKIPPED" in status or "TIMEOUT" in status or "FAILED" in status:
        return ThemeColor.DANGER
    return ThemeColor.SUCCESS


def _surface():
    canvas = Overlay(
        Fill(ThemeColor.BACKGROUND).ref(BenchmarkRef.SURFACE_BACKGROUND),
        Stroke(ThemeColor.BORDER, line_width=1).ref(BenchmarkRef.SURFACE_BORDER),
        TextCube(
            bind(BenchmarkState.ANGLE_X),
            bind(BenchmarkState.ANGLE_Y),
            bind(BenchmarkState.ANGLE_Z),
            bind(BenchmarkState.MODE),
        ).ref(BenchmarkRef.CUBE),
    ).size(CUBE_WIDTH, CUBE_HEIGHT) \
     .align(horizontal="center", vertical="center") \
     .repaint_boundary().ref(BenchmarkRef.SURFACE)

    content = Column(
        canvas,
        Column(
            Text(
                derived(_mode_position, bind(BenchmarkState.MODE)),
                color=ThemeColor.TEXT, font=VALUE_FONT,
            ).height(22).ref(BenchmarkRef.MODE_POSITION),
            Text(
                "Click to switch", color=ThemeColor.DIM, font=FONT,
            ).height(20).ref(BenchmarkRef.SWITCH_HINT),
            gap=0,
        ).height(42),
        gap=4,
    ).ref(BenchmarkRef.SURFACE_LAYOUT)

    # Clicking anywhere in the left benchmark column cycles the primitive.
    return Overlay(
        content,
        Hitbox(NEXT_MODE).ref(BenchmarkRef.MODE_HITBOX),
    )


def _metric_row(label, state_key, formatter, label_ref, value_ref):
    return (
        Text(
            label, color=ThemeColor.DIM, font=FONT,
            horizontal="left", vertical="top",
        ).ref(label_ref),
        Text(
            derived(formatter, bind(state_key)), color=ThemeColor.BRIGHT,
            font=VALUE_FONT, horizontal="right", vertical="top",
        ).ref(value_ref),
    )


def _stats():
    metrics = Grid(
        matrix=(
            _metric_row(
                "COMMIT FPS", BenchmarkState.COMMIT_FPS, _format_fps,
                BenchmarkRef.COMMIT_FPS_LABEL, BenchmarkRef.COMMIT_FPS_VALUE),
            _metric_row(
                "F.MED", BenchmarkState.FRAME_MEDIAN_MS, _format_ms,
                BenchmarkRef.FRAME_MEDIAN_LABEL,
                BenchmarkRef.FRAME_MEDIAN_VALUE),
            _metric_row(
                "F.P95", BenchmarkState.FRAME_P95_MS, _format_ms,
                BenchmarkRef.FRAME_P95_LABEL, BenchmarkRef.FRAME_P95_VALUE),
            _metric_row(
                "TYPER", BenchmarkState.TYPER_MS, _format_ms,
                BenchmarkRef.TYPER_LABEL, BenchmarkRef.TYPER_VALUE),
            _metric_row(
                "CPU", BenchmarkState.CPU_MS, _format_ms,
                BenchmarkRef.CPU_LABEL, BenchmarkRef.CPU_VALUE),
            _metric_row(
                "FLUSH", BenchmarkState.FLUSH_MS, _format_precise_ms,
                BenchmarkRef.FLUSH_LABEL, BenchmarkRef.FLUSH_VALUE),
            _metric_row(
                "PYTHON", BenchmarkState.PYTHON_MS, _format_precise_ms,
                BenchmarkRef.PYTHON_LABEL, BenchmarkRef.PYTHON_VALUE),
            _metric_row(
                "MISSED", BenchmarkState.MISSED_PERCENT, _format_percent,
                BenchmarkRef.MISSED_LABEL, BenchmarkRef.MISSED_VALUE),
            _metric_row(
                "RASTER", BenchmarkState.RASTER, _format_raster,
                BenchmarkRef.RASTER_LABEL, BenchmarkRef.RASTER_VALUE),
        ),
        columns=(Flex(2), Flex(1)), rows=Equal(9), gap=(0, 0),
    ).height(272).margin(left=2, right=2).ref(BenchmarkRef.STATS_GRID)

    footer = Column(
        Text(
            derived(_mode_label, bind(BenchmarkState.MODE)),
            color=ThemeColor.SECONDARY, font=VALUE_FONT,
            max_width=STATS_WIDTH - 24, truncate=True,
        ).height(24).ref(BenchmarkRef.MODE),
        Text(
            bind(BenchmarkState.STATUS),
            color=derived(_status_color, bind(BenchmarkState.STATUS)),
            font=VALUE_FONT, max_width=STATS_WIDTH - 24, truncate=True,
        ).height(24).ref(BenchmarkRef.STATUS),
        gap=1,
    ).height(49).ref(BenchmarkRef.STATS_FOOTER)

    layout = Column(
        metrics,
        Spacer().ref(BenchmarkRef.STATS_SPACER),
        footer,
        gap=0,
    ).padding(left=12, top=15, right=12, bottom=9) \
     .ref(BenchmarkRef.STATS_LAYOUT)

    return Overlay(
        Panel(
            border=ThemeColor.BORDER, background=ThemeColor.PANEL,
            line_width=1,
        ).ref(BenchmarkRef.STATS_PANEL),
        layout,
    ).repaint_boundary().ref(BenchmarkRef.STATS)


def create_page():
    root = Grid(
        matrix=((_surface(), _stats()),),
        columns=(FLEX, STATS_WIDTH), rows=(FLEX,), gap=10,
    ).padding(left=8, top=8, right=8, bottom=8).ref(BenchmarkRef.ROOT)
    page = PageTree(root, CONTENT, page_id=AppPage.RENDER_BENCHMARK)
    page.title = PAGE_TITLE
    page.show_back = True
    return page


# Designer discovery intentionally imports page modules without calling their
# factories. Publish one declarative instance like the other static pages; the
# runtime still creates a fresh tree/state store for every benchmark session.
PAGE = create_page()
