"""Flow layout, resize and Designer reflection contracts for Shaper pages."""
# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
# This file may be distributed under the terms of the GNU GPLv3 license.

import json
import pathlib
import sys
import unittest
from copy import deepcopy

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / '.py/klipper/plugins'))
from ff5m_ui.shaper import compare, page, progress
from ff5m_ui.shaper import history
from ff5m_ui.shaper.actions import ShaperAction
from ff5m_ui.shaper.components import SpectrumPlot
from ff5m_ui.shaper.results import ShaperResults
from ui import FeatherRenderer, Rect
from ui.reflection import reflect_page
from tests.feather_render_test_helper import RenderCapture


class ShaperLayoutTests(unittest.TestCase):
    def setUp(self):
        self.result = json.loads((ROOT/'tests/fixtures/shaper_reference.json').read_text())['cases'][0]
        self.renderer = FeatherRenderer()
        self.capture = RenderCapture(self.renderer)

    def test_flow_layout_resizes_canvas_and_preserves_touch_targets(self):
        widths = []
        for bounds in (Rect(0,56,720,386), Rect(0,56,800,386), Rect(0,56,1024,506)):
            with self.subTest(bounds=tuple(bounds)):
                view = ShaperResults(bounds)
                results = {'x': self.result}
                tree = view.write(results)
                self.renderer.send(self.renderer.begin_page(tree.title) + tree.draw(self.renderer, tree.state))
                canvas = tree.rect(page.ShaperRef.PLOT)
                widths.append(canvas.width)
                self.assertGreater(canvas.height, 100)
                for button in self.capture.latest.buttons.values():
                    area = button.bounds
                    self.assertGreaterEqual(area.width, 44)
                    self.assertGreaterEqual(area.height, 44)
                    self.assertGreaterEqual(area.x, bounds.x)
                    self.assertLessEqual(area.right, bounds.right)
                self.assertLess(canvas.bottom, tree.rect(page.ShaperRef.APPLY).y)
                view.select(ShaperAction('view', 'table'))
                table = view.write(results)
                self.renderer.send(self.renderer.begin_page(table.title) + table.draw(self.renderer, table.state))
                targets = [button.bounds for button in self.capture.latest.buttons.values()]
                for index, area in enumerate(targets):
                    self.assertGreaterEqual(area.width, 44)
                    self.assertGreaterEqual(area.height, 44)
                    self.assertGreaterEqual(area.x, bounds.x)
                    self.assertGreaterEqual(area.y, bounds.y)
                    self.assertLessEqual(area.right, bounds.right)
                    self.assertLessEqual(area.bottom, bounds.bottom)
                    for other in targets[index + 1:]:
                        self.assertFalse(area.x < other.right and other.x < area.right
                                         and area.y < other.bottom and other.y < area.bottom)
        self.assertEqual(widths, sorted(set(widths)))

    def test_all_pages_reflect_editable_components_and_chart_properties(self):
        for tree in (page.create_page(), compare.create_page(), deepcopy(progress.PAGE)):
            tree.draw(self.renderer)
            description = reflect_page(tree)
            self.assertEqual(description['page']['bounds'], list(tree.bounds))
            self.assertFalse(description['diagnostics'])
        chart = SpectrumPlot()
        self.assertTrue({'result','method','scale','before_color','after_color','line_width'} <=
                        {spec.name for spec in chart.property_schema})
        self.assertTrue(chart.draw(self.renderer, {}, Rect(0,0,100,80)))

    def test_five_history_rows_fit_the_screen_and_remain_editable(self):
        entries = [dict(id=str(i), saved_at=1793613222.-i*300, axes=['x', 'y']) for i in range(5)]
        for width in (720, 800, 1024):
            tree = deepcopy(history.PAGE)
            tree.bounds = Rect(0,56,width,386)
            self.renderer.send(tree.draw(self.renderer, {history.HistoryState.ENTRIES: entries}))
            self.assertEqual(sum(self.capture.latest.has_action('cal.shaper.record.%d'%i) for i in range(5)), 5)
            previous_bottom = tree.bounds.y
            for i in range(5):
                area = tree.rect('shaper.history.record.%d'%i)
                self.assertGreaterEqual(area.height,44)
                self.assertGreaterEqual(area.y,previous_bottom)
                self.assertLessEqual(area.bottom,tree.bounds.bottom)
                previous_bottom=area.bottom
            self.assertFalse(reflect_page(tree)['diagnostics'])

    def test_comparison_exposes_only_available_method_actions(self):
        view = ShaperResults()
        data = {'x': self.result}
        view.write(data)
        view.select(ShaperAction('view','table'))
        tree = view.write(data)
        self.renderer.send(self.renderer.begin_page('Compare')+tree.draw(self.renderer,tree.state))
        self.assertFalse(self.capture.latest.has_action('cal.shaper.method.zvd'))
        for method in view.available_methods:
            self.assertTrue(self.capture.latest.has_action(ShaperAction('method', method).wire_id))
        self.assertFalse(reflect_page(tree)['diagnostics'])

    def test_progress_stage_list_follows_requested_axes_and_cancel_state(self):
        for axes in (('x',),('y',),('x','y')):
            status=dict(job_id=1,state='processing',axis=axes[0],requested_axes=axes,completed_axes=(),
                        frequency=None,freq_start=5.,freq_end=100.,samples=32000,elapsed=40,cancel_requested=False)
            self.renderer.send(progress.render(self.renderer,status))
            self.assertTrue(self.capture.latest.has_action('cal.cancel'))
            status['cancel_requested'] = True
            self.renderer.send(progress.render(self.renderer,status))
            self.assertFalse(self.capture.latest.has_action('cal.cancel'))
