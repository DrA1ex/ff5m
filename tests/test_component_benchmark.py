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
from ff5m_ui.benchmark.layout_page import LayoutRef  # noqa: E402
from ff5m_ui.benchmark.page import BenchmarkRef  # noqa: E402
from ff5m_ui.print_state import PrintState  # noqa: E402
from ff5m_ui.screen import ScreenPage  # noqa: E402
from ui import FeatherRenderer  # noqa: E402
from ui.render_receipts import RenderReceipt  # noqa: E402


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


class Renderer(FeatherRenderer):
    def __init__(self):
        super().__init__()
        self.batches = []

    def send(self, commands, kind=None, key=None, receipt=None):
        self.batches.append((tuple(commands), kind, key, receipt))
        return True


class ComponentBenchmarkTest(unittest.TestCase):
    def test_five_menu_title_taps_open_only_the_component_benchmark(self):
        class Menu(HomePagesMixin):
            def __init__(self):
                self.reactor = Reactor()
                self.renderer = Renderer()
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
        renderer = Renderer()
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
            token = renderer.batches[-1][3]
            self.assertEqual(token, feature.tracker.pending.token)
            feature.on_render_receipt(
                RenderReceipt(token, True, 12000, 8000, 1000),
                reactor.now + 0.012)
            reactor.now += 0.04
            feature._tick(reactor.now)
            if frame == 15:
                self.assertEqual(tree.layout.rect(card).width, 460)
                self.assertLess(tree.layout.rect(description).height, narrow_height)
                wide_buttons = tuple(
                    tree.layout.rect(button) for button in actions.children)
                self.assertEqual({rect.y for rect in wide_buttons},
                                 {wide_buttons[0].y})
                self.assertGreater(wide_buttons[0].width,
                                   narrow_buttons[0].width)
            if frame == 20:
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
        self.assertEqual(renderer.batches[-1][2], "component-benchmark")
        feature.back(host.page)
        self.assertEqual(host.page, ScreenPage.MAIN_MENU)
        self.assertFalse(feature.active)
        self.assertIsNone(feature.tracker.pending)

    def test_timeout_stops_component_frames_and_reports_failure(self):
        reactor = Reactor()
        renderer = Renderer()
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
        self.assertEqual(renderer.batches[-1][2], "render-benchmark-error")

    def test_existing_render_benchmark_still_cycles_modes(self):
        reactor = Reactor()
        renderer = Renderer()
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
