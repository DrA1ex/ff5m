## Lazy interactive framebuffer benchmark for Feather.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import math
import statistics
import time
from collections import deque, namedtuple

from ui import ReceiptTracker
from ui.lazy import LazyModule
from feather_feature_manager import FeatureHostProxy
from ff5m_ui.benchmark.actions import BenchmarkAction, BenchmarkRoute
from ff5m_ui.benchmark.constants import BENCHMARK_MODES
from ff5m_ui.screen import ScreenPage
from ff5m_ui.print_state import PrintState


benchmark_page = LazyModule("ff5m_ui.benchmark.page")
benchmark_state = LazyModule("ff5m_ui.benchmark.state")


BenchmarkSample = namedtuple(
    "BenchmarkSample",
    ("latency_ms", "typer_ms", "cpu_ms", "flush_ms", "python_ms"),
)


class BenchmarkFeature(FeatureHostProxy):
    name = "benchmark"

    TARGET_FPS = 60.0
    FRAME_INTERVAL = 1.0 / TARGET_FPS
    RECEIPT_TIMEOUT = 1.0
    WARMUP_FRAMES = 30
    WINDOW_FRAMES = 120
    STATS_PERIOD = 1.0
    MODES = BENCHMARK_MODES

    def __init__(self, host):
        super().__init__(host)

        self.timer = None
        self.active = False
        self.page_tree = None
        self.surface_node = None
        self.stats_node = None
        self.tracker = ReceiptTracker(self.RECEIPT_TIMEOUT)
        self.session = 0
        self.frame = 0
        self.session_started = 0.0
        self.next_target = 0.0
        self.next_stats_at = 0.0
        self.actual_fps = 0.0
        self.samples = deque(maxlen=self.WINDOW_FRAMES)
        self.receipt_times = deque(maxlen=self.WINDOW_FRAMES * 2)
        self.mode = self.MODES[0]
        self.display_stats = self._empty_display_stats()
        self.display_status = self._warmup_status()
        self.stats_dirty = True

    def initialize(self):
        self.timer = self.reactor.register_timer(
            self._tick, self.reactor.NEVER)

    def render(self, page):
        if page != ScreenPage.RENDER_BENCHMARK:
            raise ValueError("benchmark feature cannot render %s" % page)
        
        if self.print_state != PrintState.IDLE:
            raise RuntimeError("Render benchmark requires an idle printer")

        self.mode = self.MODES[0]
        self.page_tree = benchmark_page.create_page()
        self.surface_node = self.page_tree.node(
            benchmark_page.BenchmarkRef.SURFACE)
        self.stats_node = self.page_tree.node(
            benchmark_page.BenchmarkRef.STATS)
        self.active = True
        now = self.reactor.monotonic()
        self._reset_measurements(now)
        self._submit_frame(now, full=True)

    def allows_action(self, page, action):
        return page == ScreenPage.RENDER_BENCHMARK and action == "nav.back"

    def handle_action(self, page, action):
        return False

    def resolve_semantic_action(self, page, wire_id):
        if page != ScreenPage.RENDER_BENCHMARK or self.page_tree is None:
            return None
        
        return self.page_tree.resolve_action(wire_id)

    def handle_semantic_action(self, page, action):
        if page != ScreenPage.RENDER_BENCHMARK:
            return False
        
        if (isinstance(action, BenchmarkAction)
                and action.route == BenchmarkRoute.NEXT_MODE):
            self._cycle_mode(self.reactor.monotonic())
            return True
        
        raise KeyError("Unsupported benchmark action: %s" % action)

    def back(self, page):
        if page != ScreenPage.RENDER_BENCHMARK:
            return False
        
        self._stop_session()
        self._show_page(ScreenPage.SETTINGS)

        return True

    def update(self, eventtime):
        if self.active and self.print_state != PrintState.IDLE:
            self._stop_session()
            self._show_page(self.page_for_print_state())

    def on_page_changed(self, old_page, new_page):
        if old_page == ScreenPage.RENDER_BENCHMARK and new_page != old_page:
            self._stop_session()

    def on_print_state_changed(self, old_state, new_state, stats_state):
        if self.active and new_state != PrintState.IDLE:
            self._stop_session()

    def on_renderer_restarted(self):
        if self.page == ScreenPage.RENDER_BENCHMARK:
            self._stop_session()

    def on_render_receipt(self, receipt, eventtime):
        if not self.active:
            return
        
        measurement = self.tracker.resolve(receipt, eventtime)
        if measurement is None:
            return
        
        if not receipt.success:
            self._fail("FAILED RECEIPT")
            return

        metadata = measurement.metadata or {}
        sample = BenchmarkSample(
            latency_ms=measurement.latency_ms,
            typer_ms=receipt.total_us / 1000.0,
            cpu_ms=receipt.cpu_us / 1000.0,
            flush_ms=receipt.flush_us / 1000.0,
            python_ms=float(metadata.get("python_ms", 0.0)),
        )

        self.receipt_times.append(float(eventtime))
        completed = self.frame
        if completed > self.WARMUP_FRAMES:
            self.samples.append(sample)
        
        if completed == self.WARMUP_FRAMES:
            self._refresh_display(eventtime)
            self.next_stats_at = float(eventtime) + self.STATS_PERIOD
        elif (completed > self.WARMUP_FRAMES
              and eventtime >= self.next_stats_at):
            self._refresh_display(eventtime)
            self.next_stats_at = float(eventtime) + self.STATS_PERIOD

        if eventtime >= self.next_target:
            self.next_target = float(eventtime)
            waketime = float(eventtime)
        else:
            waketime = self.next_target
        
        self.reactor.update_timer(self.timer, waketime)

    def deactivate(self):
        self._stop_session()
        if self.timer is not None:
            try:
                self.reactor.unregister_timer(self.timer)
            except Exception:
                pass
            
            self.timer = None

    def _warmup_status(self):
        return "WARMUP %d/%d" % (
            min(self.frame, self.WARMUP_FRAMES), self.WARMUP_FRAMES)

    def _live_status(self):
        if self.actual_fps >= self.TARGET_FPS:
            return f"LIVE / {self.TARGET_FPS} FPS"

        return "SKIPPED FRAMES"

    def _raster_name(self):
        return str(self.renderer.raster_acceleration).strip().upper()

    def _empty_display_stats(self):
        return benchmark_state.empty_stats(self._raster_name())

    def _reset_measurements(self, eventtime):
        self.tracker.cancel()
        self.session += 1
        self.frame = 0
        self.session_started = float(eventtime)
        self.next_target = float(eventtime)
        self.next_stats_at = float(eventtime) + self.STATS_PERIOD
        self.samples.clear()
        self.receipt_times.clear()
        self.display_stats = self._empty_display_stats()
        self.display_status = self._warmup_status()
        self.stats_dirty = True

    def _stop_session(self):
        self.active = False
        self.tracker.cancel()
        self.samples.clear()
        self.receipt_times.clear()
        self.page_tree = None
        self.surface_node = None
        self.stats_node = None

        if self.timer is not None:
            try:
                self.reactor.update_timer(self.timer, self.reactor.NEVER)
            except Exception:
                pass

    def _state(self, eventtime, include_stats=False, include_mode=False):
        elapsed = max(0.0, float(eventtime) - self.session_started)
        angles = (
            elapsed * 0.83,
            elapsed * 1.17,
            elapsed * 0.29,
        )

        state = benchmark_state.BenchmarkState
        values = {
            state.ANGLE_X: angles[0],
            state.ANGLE_Y: angles[1],
            state.ANGLE_Z: angles[2],
        }

        if include_mode:
            values[state.MODE] = self.mode

        if include_stats:
            values.update(benchmark_state.stats_values(self.display_stats))
            values[state.STATUS] = self.display_status
        
        return values

    def _render_animation(self, state):
        """Render only benchmark repaint boundaries on animation frames.

        A normal ``PageTree.update()`` intentionally walks every declarative
        node to discover changed bindings.  That is useful for regular pages,
        but it is measurable noise in this benchmark now that the statistics
        panel is composed from many editable Text primitives.  The animation
        has two explicit repaint boundaries, so update the shared state store
        and render those boundaries directly instead of traversing the page.
        """
        page_tree = self.page_tree
        surface = self.surface_node
        if page_tree is None or surface is None:
            return []

        page_tree.state.update(state)
        return surface.render(
            self.renderer, page_tree.state, page_tree.layout)

    def _render_stats(self):
        page_tree = self.page_tree
        stats = self.stats_node
        if page_tree is None or stats is None:
            return []

        state = benchmark_state.BenchmarkState
        values = benchmark_state.stats_values(self.display_stats)
        values[state.STATUS] = self.display_status
        page_tree.state.update(values)
        return stats.render(
            self.renderer, page_tree.state, page_tree.layout)

    def _token(self):
        return "%x:%d" % (self.session, self.frame + 1)

    def _submit_frame(self, eventtime, full=False):
        if not self.active or self.page != ScreenPage.RENDER_BENCHMARK:
            return self.reactor.NEVER
        if self.tracker.pending is not None:
            return self.tracker.pending.deadline

        frame_started = self.reactor.monotonic()
        build_started = time.perf_counter()
        include_stats = bool(full or self.stats_dirty)

        if full:
            state = self._state(
                eventtime, include_stats=True, include_mode=True)
            commands = self.renderer.begin_page(
                "Render benchmark", back=True)
            commands += self.page_tree.draw(self.renderer, state)
            python_ms = (time.perf_counter() - build_started) * 1000.0
            kind, key = "surface", None
        else:
            # Measure the benchmark surface itself.  Statistics rendering is
            # deliberately excluded from the Python metric and happens only
            # on the much slower stats cadence.
            animation_state = self._state(
                eventtime, include_stats=False, include_mode=False)
            commands = self._render_animation(animation_state)
            python_ms = (time.perf_counter() - build_started) * 1000.0
            if include_stats:
                commands.extend(self._render_stats())
            kind, key = "animation", "render-benchmark"
        
        if include_stats:
            self.stats_dirty = False
            
        token = self._token()
        submitted_at = self.reactor.monotonic()
        self.tracker.expect(token, submitted_at, {"python_ms": python_ms})
        accepted = self.renderer.send(commands, kind=kind, key=key, receipt=token)
        
        if not accepted:
            self.tracker.cancel()
            self.display_status = "QUEUE BUSY"
            self.stats_dirty = True
            self.reactor.update_timer(self.timer, submitted_at + self.FRAME_INTERVAL)
            
            return submitted_at + self.FRAME_INTERVAL

        self.frame += 1
        self.next_target = max(self.next_target, frame_started) + self.FRAME_INTERVAL
        deadline = self.tracker.pending.deadline
        self.reactor.update_timer(self.timer, deadline)

        return deadline

    def _tick(self, eventtime):
        if not self.active:
            return self.reactor.NEVER
        
        if self.tracker.expired(eventtime):
            self._fail("RECEIPT TIMEOUT")
            return self.reactor.NEVER
        
        if self.tracker.pending is not None:
            return self.tracker.pending.deadline
        
        self._submit_frame(eventtime)
        return (self.tracker.pending.deadline
                if self.tracker.pending is not None else
                eventtime + self.FRAME_INTERVAL)

    @staticmethod
    def _percentile(values, percentile):
        ordered = sorted(values)
        if not ordered:
            return 0.0
        
        index = int(math.ceil(percentile * len(ordered))) - 1
        return ordered[max(0, min(len(ordered) - 1, index))]

    def _refresh_display(self, eventtime):
        completed = self.frame
        if completed <= self.WARMUP_FRAMES:
            self.display_status = self._warmup_status()
            self.stats_dirty = True
            return
        
        samples = tuple(self.samples)
        if not samples:
            return
        
        recent_receipts = tuple(
            value for value in self.receipt_times
            if value >= float(eventtime) - 1.0)
        
        latency = tuple(item.latency_ms for item in samples)
        missed = 100.0 * sum(
            value > self.FRAME_INTERVAL * 1000.0 for value in latency
        ) / len(latency)

        self.actual_fps = len(recent_receipts)
        self.display_stats = benchmark_state.BenchmarkStats(
            commit_fps=self.actual_fps,
            frame_median_ms=statistics.median(latency),
            frame_p95_ms=self._percentile(latency, 0.95),
            typer_ms=statistics.median(item.typer_ms for item in samples),
            cpu_ms=statistics.median(item.cpu_ms for item in samples),
            flush_ms=statistics.median(item.flush_ms for item in samples),
            python_ms=statistics.median(item.python_ms for item in samples),
            missed_percent=missed,
            raster=self._raster_name(),
        )
        
        self.display_status = self._live_status()
        self.stats_dirty = True

    def _cycle_mode(self, eventtime):
        if not self.active or self.page != ScreenPage.RENDER_BENCHMARK:
            return
        
        index = self.MODES.index(self.mode)
        self.mode = self.MODES[(index + 1) % len(self.MODES)]
        self._reset_measurements(eventtime)
        self._submit_frame(eventtime, full=True)

    def _fail(self, status):
        self.tracker.cancel()
        self.active = False
        self.display_status = str(status)
        if self.timer is not None:
            self.reactor.update_timer(self.timer, self.reactor.NEVER)

        page_tree = self.page_tree
        if page_tree is None or self.page != ScreenPage.RENDER_BENCHMARK:
            return
        
        commands = self._render_stats()

        self.renderer.send_animation(commands, "render-benchmark-error")
