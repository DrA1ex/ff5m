## Behavioral coverage for the component layout benchmark.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import pathlib
import sys
import unittest
from types import SimpleNamespace


PLUGINS = pathlib.Path(__file__).parents[1] / ".py" / "klipper" / "plugins"
sys.path.insert(0, str(PLUGINS))

from feather.features.benchmark import BenchmarkFeature  # noqa: E402
from feather.screen.pages.home import HomePagesMixin  # noqa: E402
from ff5m_ui.benchmark import layout_page  # noqa: E402
from ff5m_ui.benchmark.layout_page import LayoutRef  # noqa: E402
from ff5m_ui.benchmark.page import BenchmarkRef  # noqa: E402
from ff5m_ui.print_state import PrintState  # noqa: E402
from ff5m_ui.screen import ScreenPage  # noqa: E402
from ui import FeatherRenderer  # noqa: E402
from ui.render_receipts import RenderReceipt  # noqa: E402
from tests.feather_render_test_helper import (  # noqa: E402
    RenderCapture, full_render_queue)


class Reactor:
    NEVER = float("inf")

    def __init__(self):
        self.now = 0.0
        self.waketime = self.NEVER

    def monotonic(self):
        return self.now

    def register_timer(self, callback, when):
        self.waketime = when
        return callback

    def update_timer(self, callback, when):
        self.waketime = when


class ComponentBenchmarkTest(unittest.TestCase):
    def feature(self):
        host = SimpleNamespace(
            reactor=Reactor(), renderer=FeatherRenderer(),
            page=ScreenPage.COMPONENT_BENCHMARK, print_state=PrintState.IDLE)
        host._show_page = lambda page: setattr(host, "page", page)
        host.rendering = RenderCapture(host.renderer)
        feature = BenchmarkFeature(host)
        feature.initialize()
        feature.render(host.page)
        return feature, host

    def test_animation_speed_depends_on_elapsed_time_not_frame_count(self):
        snapshots = []
        for frame_count in (40, 120):
            feature, host = self.feature()
            feature.session_started = 10.0
            feature.frame = frame_count
            feature._build_frame(11.5)
            tree = feature.page_tree
            snapshots.append((tree.rect(LayoutRef.CARD), tuple(
                tree.state[key] for key in (layout_page.LayoutState.ROW_ONE,
                                           layout_page.LayoutState.ROW_TWO))))
            # Three seconds to expand and another three to return.
            feature._build_frame(13.0)
            self.assertEqual(tree.rect(LayoutRef.CARD).width, 510)
            feature._build_frame(16.0)
            self.assertEqual(tree.rect(LayoutRef.CARD).width, 300)
        self.assertEqual(snapshots[0], snapshots[1])
        self.assertEqual(snapshots[0][0].width, 405)

    def test_rows_remain_readable_and_restart_resets_animation(self):
        feature, host = self.feature()
        key = layout_page.LayoutState.ROW_ONE
        original = feature.page_tree.state[key]
        feature._build_frame(1.99)
        self.assertEqual(feature.page_tree.state[key], original)
        feature._build_frame(2.0)
        self.assertNotEqual(feature.page_tree.state[key], original)
        host.reactor.now = 8.0
        feature._cycle_mode(host.reactor.now)
        self.assertEqual(feature.page_tree.state[key], original)
        self.assertEqual(feature.page_tree.rect(LayoutRef.CARD).width, 300)
        self.assertIsNone(feature.display_stats.commit_fps)
        self.assertIsNone(feature.display_stats.fps_loss_percent)

    def test_fps_loss_measures_commit_rate_instead_of_ack_latency(self):
        for fps in (20, 40, 60):
            with self.subTest(fps=fps):
                feature, host = self.feature()
                for frame in range(1, fps * 3 + 1):
                    host.reactor.now = frame / fps
                    token = feature.tracker.pending.token
                    feature.on_render_receipt(
                        RenderReceipt(token, True, 12000, 8000, 1000), host.reactor.now)
                    if frame < fps * 3:
                        feature._tick(host.reactor.now)
                feature._refresh_display(host.reactor.now)
                self.assertEqual(feature.display_stats.commit_fps, fps)
                self.assertAlmostEqual(feature.display_stats.fps_loss_percent,
                                       100.0 * (1.0 - fps / 60.0))
                self.assertAlmostEqual(feature.display_stats.frame_median_ms, 1000.0 / fps)
                commands = feature._build_frame(host.reactor.now, full=True).commands
                self.assertTrue(any('FPS LOSS' in command for command in commands))
                self.assertTrue(any('TARGET 60 FPS' in command for command in commands))

    def test_first_live_stats_normalize_a_window_shorter_than_one_second(self):
        feature, host = self.feature()
        for frame in range(1, feature.WARMUP_FRAMES + 2):
            host.reactor.now = frame / 60.0
            token = feature.tracker.pending.token
            feature.on_render_receipt(
                RenderReceipt(token, True, 12000, 8000, 1000), host.reactor.now)
            if frame <= feature.WARMUP_FRAMES:
                feature._tick(host.reactor.now)
        feature._refresh_display(host.reactor.now)
        self.assertLess(host.reactor.now, 1.0)
        self.assertAlmostEqual(feature.display_stats.commit_fps, 60.0)
        self.assertAlmostEqual(feature.display_stats.fps_loss_percent, 0.0)

    def test_prepares_one_successor_without_submitting_or_counting_it(self):
        feature, host = self.feature()
        pending = feature.tracker.pending
        feature._tick(host.reactor.now)
        prepared = feature.prepared_frame
        for _ in range(3):
            self.assertEqual(feature._tick(host.reactor.now), pending.deadline)
        self.assertIs(feature.prepared_frame, prepared)
        self.assertEqual(len(host.rendering.submitted), 1)
        self.assertEqual(feature.frame, 1)
        self.assertFalse(feature.receipt_times)

        host.reactor.now = 0.02
        feature.on_render_receipt(RenderReceipt(pending.token, True, 14700, 14000, 10000), 0.02)
        feature._tick(host.reactor.now)
        self.assertEqual(host.rendering.submitted[-1].commands, tuple(prepared.commands))
        self.assertEqual(feature.frame, 2)
        self.assertEqual(len(feature.receipt_times), 1)

    def test_rejected_prepared_delta_is_retried_unchanged(self):
        feature, host = self.feature()
        feature._tick(0)
        prepared = feature.prepared_frame
        token = feature.tracker.pending.token
        host.reactor.now = 0.02
        feature.on_render_receipt(RenderReceipt(token, True, 12000, 8000, 1000), 0.02)
        with full_render_queue(host.renderer):
            feature._tick(0.02)
        self.assertIs(feature.prepared_frame, prepared)
        self.assertIsNone(feature.tracker.pending)
        self.assertEqual(feature.frame, 1)
        host.reactor.now = 0.04
        feature._tick(0.04)
        self.assertEqual(host.rendering.submitted[-1].commands, tuple(prepared.commands))

    def test_cancel_reset_and_timeout_discard_prepared_geometry(self):
        for operation in ("back", "reset", "timeout", "failed"):
            with self.subTest(operation=operation):
                feature, host = self.feature()
                feature._tick(0)
                old_token = feature.tracker.pending.token
                if operation == "back":
                    feature.back(host.page)
                    self.assertFalse(feature.active)
                elif operation == "reset":
                    feature._cycle_mode(0.01)
                    self.assertNotEqual(feature.tracker.pending.token, old_token)
                elif operation == "timeout":
                    host.reactor.now = 1.1
                    feature._tick(host.reactor.now)
                else:
                    feature.on_render_receipt(RenderReceipt(old_token, False, 0, 0, 0), 0.01)
                self.assertIsNone(feature.prepared_frame)
                if operation in ("timeout", "failed"):
                    self.assertFalse(feature.active)
                    self.assertTrue(any('-s 800 442' in command for command in host.rendering.submitted[-1].commands))
                count = len(host.rendering.submitted)
                feature.on_render_receipt(RenderReceipt(old_token, True, 1, 1, 0), 1.2)
                self.assertEqual(len(host.rendering.submitted), count)

    def test_pipeline_overlaps_build_and_render_without_exceeding_target(self):
        # Deterministic stage timings, not a host-speed performance assertion.
        for build_seconds, render_seconds in ((0.0185, 0.0147), (0.002, 0.003)):
            with self.subTest(build_seconds=build_seconds):
                host = SimpleNamespace(reactor=Reactor(), renderer=FeatherRenderer(),
                                       page=ScreenPage.COMPONENT_BENCHMARK, print_state=PrintState.IDLE)
                RenderCapture(host.renderer)
                feature = BenchmarkFeature(host)
                original_build = feature._build_frame
                def build(*args, **kwargs):
                    result = original_build(*args, **kwargs)
                    host.reactor.now += build_seconds
                    return result
                feature._build_frame = build
                original_send = host.renderer.send
                deliveries = []
                def send(*args, **kwargs):
                    deliveries.append((host.reactor.now + render_seconds, kwargs["receipt"]))
                    return original_send(*args, **kwargs)
                host.renderer.send = send
                feature.initialize()
                feature.render(host.page)
                confirmed = 0
                while host.reactor.now < 1.0:
                    receipt_at = deliveries[0][0] if deliveries else float("inf")
                    host.reactor.now = max(host.reactor.now, min(receipt_at, host.reactor.waketime))
                    if receipt_at <= host.reactor.waketime:
                        _, token = deliveries.pop(0)
                        feature.on_render_receipt(RenderReceipt(token, True, 14700, 14000, 10000), host.reactor.now)
                        confirmed += 1
                    else:
                        host.reactor.waketime = feature._tick(host.reactor.now)
                    self.assertLessEqual(len(deliveries), 1)
                self.assertGreaterEqual(confirmed, 50)
                self.assertLessEqual(confirmed, 60)

    def test_targeted_frames_match_normal_component_updates(self):
        host = SimpleNamespace(
            reactor=Reactor(), renderer=FeatherRenderer(),
            page=ScreenPage.COMPONENT_BENCHMARK,
            print_state=PrintState.IDLE,
        )
        feature = BenchmarkFeature(host)
        regular = layout_page.create_page()
        targeted = layout_page.create_page()
        regular_renderer = FeatherRenderer()
        targeted_renderer = FeatherRenderer()
        initial = feature._layout_state(0.0, True)
        regular_renderer.begin_page("Component benchmark", back=True)
        targeted_renderer.begin_page("Component benchmark", back=True)
        self.assertEqual(regular.draw(regular_renderer, initial),
                         targeted.draw(targeted_renderer, initial))

        for frame in range(1, 44):
            feature.frame = frame
            include_stats = frame % 7 == 0
            state = feature._layout_state(frame / 7.0, include_stats)
            refs = [LayoutRef.CARD]
            if include_stats:
                refs.append(BenchmarkRef.STATS)
            for page in (regular, targeted):
                page.node(LayoutRef.CARD).width(feature._layout_width(frame / 7.0))
            expected = regular.update(regular_renderer, state)
            actual = targeted.update_refs(targeted_renderer, state, refs)
            self.assertEqual(actual, expected, frame)
            for ref in (LayoutRef.CARD, LayoutRef.DESCRIPTION,
                        LayoutRef.ACTIONS, BenchmarkRef.STATS):
                self.assertEqual(targeted.rect(ref), regular.rect(ref),
                                 (frame, ref))

    def test_five_menu_title_taps_open_only_the_component_benchmark(self):
        class Menu(HomePagesMixin):
            def __init__(self):
                self.reactor = Reactor()
                self.renderer = FeatherRenderer()
                RenderCapture(self.renderer)
                self.opened = []

            def _require_idle(self):
                return None

            def _show_page(self, page):
                self.opened.append(page)

        menu = Menu()
        menu._render_main_menu()
        self.assertIn("menu.component_benchmark.tap", menu.renderer._hitboxes)
        for index in range(4):
            menu.reactor.now = index * 0.2
            menu._handle_component_benchmark_tap()
        self.assertEqual(menu.opened, [])
        menu.reactor.now = 0.8
        menu._handle_component_benchmark_tap()
        self.assertEqual(menu.opened, [ScreenPage.COMPONENT_BENCHMARK])

        menu._render_main_menu()
        menu.reactor.now = 4.0
        menu._handle_component_benchmark_tap()
        menu.reactor.now = 6.1
        menu._handle_component_benchmark_tap()
        self.assertEqual(menu._component_benchmark_taps, 1)

    def test_frames_reflow_wrapped_text_and_button_columns_and_publish_metrics(self):
        reactor = Reactor()
        renderer = FeatherRenderer()
        rendering = RenderCapture(renderer)
        host = SimpleNamespace(
            reactor=reactor, renderer=renderer,
            page=ScreenPage.COMPONENT_BENCHMARK,
            print_state=PrintState.IDLE,
        )
        host._show_page = lambda page: setattr(host, "page", page)
        feature = BenchmarkFeature(host)
        feature.initialize()
        feature.render(host.page)
        tree = feature.page_tree
        card = tree.node(LayoutRef.CARD)
        description = tree.node(LayoutRef.DESCRIPTION)
        actions = tree.node(LayoutRef.ACTIONS)
        narrow_height = tree.layout.rect(description).height
        narrow_buttons = tuple(tree.layout.rect(button) for button in actions.children)
        self.assertEqual(tree.layout.rect(card).width, 300)
        self.assertGreaterEqual(narrow_buttons[0].width, 120)
        self.assertEqual(narrow_buttons[0].y, narrow_buttons[1].y)
        self.assertGreater(narrow_buttons[2].y, narrow_buttons[0].y)

        for frame in range(65):
            previous_card = tree.layout.rect(card)
            token = rendering.submitted[-1].receipt
            self.assertEqual(token, feature.tracker.pending.token)
            feature.on_render_receipt(
                RenderReceipt(token, True, 12000, 8000, 1000),
                reactor.now + 0.012)
            reactor.now += 0.15
            feature._tick(reactor.now)
            commands = rendering.submitted[-1].commands
            if frame == 0:
                current_card = tree.layout.rect(card)
                for exposed in previous_card.subtract(current_card):
                    self.assertIn(renderer.fill(*exposed), commands)
                self.assertNotIn(renderer.fill(*current_card), commands)
                self.assertFalse(any("COMMIT FPS" in command for command in commands))
                self.assertFalse(any("-s 800 442" in command for command in commands))
            if frame == 15:
                self.assertGreater(tree.layout.rect(card).width, 460)
                self.assertLess(tree.layout.rect(description).height, narrow_height)
                wide_buttons = tuple(
                    tree.layout.rect(button) for button in actions.children)
                self.assertEqual({rect.y for rect in wide_buttons},
                                 {wide_buttons[0].y})
                self.assertGreater(wide_buttons[0].width,
                                   narrow_buttons[0].width)
            if frame == 19:
                card_bounds = tree.layout.rect(card)
                stats_bounds = tree.layout.rect(
                    tree.node(BenchmarkRef.STATS))
                self.assertGreater(card_bounds.width, 460)
                self.assertGreaterEqual(stats_bounds.x - card_bounds.right, 20)
                self.assertLessEqual(stats_bounds.x - card_bounds.right, 30)
                self.assertGreater(tree.layout.rect(actions.children[0]).width,
                                   wide_buttons[0].width)

        self.assertGreater(feature.display_stats.commit_fps, 0)
        self.assertGreater(feature.display_stats.frame_median_ms, 0)
        self.assertGreater(feature.display_stats.python_ms, 0)
        self.assertTrue(any(
            "COMMIT FPS" in command
            for batch in rendering.batches[1:] for command in batch))
        self.assertFalse(any(
            "-s 800 442" in command
            for batch in rendering.batches[1:] for command in batch))
        self.assertEqual(rendering.submitted[-1].key, "component-benchmark")
        feature.back(host.page)
        self.assertEqual(host.page, ScreenPage.MAIN_MENU)
        self.assertFalse(feature.active)
        self.assertIsNone(feature.tracker.pending)

    def test_timeout_stops_component_frames_and_reports_failure(self):
        reactor = Reactor()
        renderer = FeatherRenderer()
        rendering = RenderCapture(renderer)
        host = SimpleNamespace(
            reactor=reactor, renderer=renderer,
            page=ScreenPage.COMPONENT_BENCHMARK,
            print_state=PrintState.IDLE,
        )
        feature = BenchmarkFeature(host)
        feature.initialize()
        feature.render(host.page)

        reactor.now = feature.RECEIPT_TIMEOUT + 0.01
        feature._tick(reactor.now)

        self.assertFalse(feature.active)
        self.assertIsNone(feature.tracker.pending)
        self.assertEqual(feature.display_status, "RECEIPT TIMEOUT")
        self.assertEqual(reactor.waketime, reactor.NEVER)
        self.assertEqual(rendering.submitted[-1].key, "render-benchmark-error")

    def test_existing_render_benchmark_still_cycles_modes(self):
        reactor = Reactor()
        renderer = FeatherRenderer()
        rendering = RenderCapture(renderer)
        host = SimpleNamespace(
            reactor=reactor, renderer=renderer,
            page=ScreenPage.RENDER_BENCHMARK,
            print_state=PrintState.IDLE,
        )
        host._show_page = lambda page: setattr(host, "page", page)
        feature = BenchmarkFeature(host)
        feature.initialize()
        feature.render(host.page)
        first_mode = feature.mode
        token = feature.tracker.pending.token
        feature.on_render_receipt(
            RenderReceipt(token, True, 12000, 8000, 1000), 0.012)
        feature._cycle_mode(0.02)

        self.assertNotEqual(feature.mode, first_mode)
        self.assertEqual(feature.frame, 1)
        feature.back(host.page)
        self.assertEqual(host.page, ScreenPage.SETTINGS)


if __name__ == "__main__":
    unittest.main()
