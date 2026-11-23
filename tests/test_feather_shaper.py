# Feather input-shaper inspection and command routing.
# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
# This file may be distributed under the terms of the GNU GPLv3 license.

import copy
import gc
import tracemalloc
import json
import math
import pathlib
import shlex
import sys
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / ".py/klipper/plugins"))
from feather.calibration.shaper import pending_shapers
from feather.features.calibration import CalibrationFeature
from feather.features.manager import FeatureSpec, LazyFeatureManager
from ff5m_ui.screen import ScreenPage
from ff5m_ui.shaper.actions import ShaperAction
from ff5m_ui.shaper.results import ShaperResults
from ff5m_ui.shaper.state import ShaperState
from ff5m_ui.shaper.components import SpectrumPlot, spectrum_commands, spectrum_scale
from ui import Rect
from ff5m_ui.shaper import progress as progress_ui
from ui import FeatherRenderer, ThemeColor
from tests.test_forge_shaper import make_plugin, plugin, FakeCommand
from tests.test_shaper_plot import raster_pixels
from tests.feather_render_test_helper import RenderCapture, accepted_by_loop
from tests.test_feather_workflows import base_controller, composed_controller_surface, feature_on_screen
from tests.gcode_macro_harness import MacroActionError, MacroExecution
from feather.previews import decode_fxi1


TEST_PLOT = Rect(68, 146, 700, 210)


def show_feature_page(feature, page):
    """Paint page as the screen does: the tree and values the feature gives, or its painter."""
    content = feature.page_content(page)
    if content is None:
        feature.render(page)
        return
    tree, title, back = content
    feature.write_page_values(page)
    commands = feature.renderer.begin_page(title, back=back)
    commands += tree.draw(feature.renderer, tree.state)
    feature.renderer.send(commands)


def pending_config(host, native):
    """Route UI actions through the real plugin into a configfile stand-in."""
    pending = {}
    def set_config(section, key, value):
        pending.setdefault(section, {})[key] = value
    configfile = types.SimpleNamespace(set=set_config,
        get_status=lambda now: {'save_config_pending_items': pending})
    host.printer = types.SimpleNamespace(lookup_object=lambda name, default=None:
        configfile if name == 'configfile' else native if name == 'forge_shaper' else default)
    native.printer = host.printer
    original_run = host.gcode.run_script_from_command
    def run(command):
        original_run(command)
        name, *arguments = shlex.split(command)
        if name in native.gcode.handlers:
            params = dict(part.split('=', 1) for part in arguments)
            native.gcode.handlers[name](FakeCommand(params))
    host.gcode.run_script_from_command = run
    return pending


def opened_result(results, **fields):
    """Real backend with an already acquired record; no hardware or disk writes."""
    native, _, _, _, _ = make_plugin(ROOT / 'tests/fixtures/no-shaper-history', sys.executable)
    native.state, native.revision, native.job.id = "complete", 2, 3
    native.record = plugin.Record("latest", 1., results)
    for name, value in fields.items():
        setattr(native, name, value)
    return native


class ShaperInspectionTests(unittest.TestCase):
    def setUp(self):
        reference = json.loads((ROOT / "tests/fixtures/shaper_reference.json").read_text())["cases"]
        self.results = {"x": copy.deepcopy(reference[1]), "y": copy.deepcopy(reference[0])}
        self.view = ShaperResults()
        self.selections = {}
        self.renderer = FeatherRenderer()
        self.capture = RenderCapture(self.renderer)

    def paint(self):
        page = self.view.write(self.results, self.selections)
        commands = self.renderer.begin_page(page.title, back=page.show_back)
        commands += page.draw(self.renderer, page.state)
        self.renderer.send(commands)

    def select_current(self):
        fit = next(f for f in self.results[self.view.axis]['shapers'] if f['name'] == self.view.method)
        self.selections[self.view.axis] = {'name': self.view.method, 'freq': round(fit['freq'], 1)}
        self.paint()

    def test_method_switch_changes_compensation_and_metrics_without_starting_a_worker(self):
        with mock.patch("subprocess.Popen", side_effect=AssertionError("Unexpected computation")):
            self.paint()
            original = raster_pixels(self.view.plot.commands)
            original_metrics = self.view.page.state[ShaperState.METRICS]
            self.view.select(ShaperAction("view", "table"))
            self.paint()
            self.assertTrue(self.capture.latest.has_action("cal.shaper.method.ei"))
            self.view.select(ShaperAction("method", "ei"))
            self.view.select(ShaperAction("view", "graph"))
            self.paint()
        self.assertEqual(self.view.method, "ei")
        self.assertNotEqual(raster_pixels(self.view.plot.commands), original)
        self.assertNotEqual(self.view.page.state[ShaperState.METRICS], original_metrics)
    def test_axis_switch_uses_its_own_recommendation_and_disables_unmeasured_axis(self):
        self.paint()
        self.view.select(ShaperAction("method", "3hump_ei"))
        self.view.select(ShaperAction("axis", "y"))
        self.paint()
        self.assertEqual(self.view.axis, "y")
        self.assertEqual(self.view.method, self.results["y"]["recommended"])
        self.results = {"x": self.results["x"]}
        self.paint()
        self.assertEqual(self.view.axis, "x")
        self.assertFalse(self.capture.latest.has_action("cal.shaper.axis.y"))

    def test_new_results_open_on_the_graph(self):
        self.paint()
        self.view.select(ShaperAction("view", "table"))
        self.paint()
        tree = self.view.page_for(self.results)
        commands = self.renderer.begin_page(tree.title)
        commands += tree.draw(self.renderer, tree.state)
        self.renderer.send(commands)
        self.assertTrue(self.capture.latest.has_action('cal.shaper.method.ei'))
        new_results = copy.deepcopy(self.results)
        tree = self.view.page_for(new_results)
        self.view.write(new_results)
        commands = self.renderer.begin_page(tree.title)
        commands += tree.draw(self.renderer, tree.state)
        self.renderer.send(commands)
        self.assertTrue(self.capture.latest.has_action('cal.shaper.view.table'))
        self.assertFalse(self.capture.latest.has_action('cal.shaper.method.ei'))

    def test_feature_renders_real_result_and_only_apply_routes_a_setting_command(self):
        host = base_controller()
        host.renderer = self.renderer
        native = opened_result(self.results)
        pending = pending_config(host, native)
        host._render_screen = host._paint_page_values = lambda: show_feature_page(feature, ScreenPage.CALIBRATION_RESULT)
        feature = CalibrationFeature(host)
        feature.calibration_kind = "shaper"
        show_feature_page(feature, ScreenPage.CALIBRATION_RESULT)
        feature.handle_semantic_action(ScreenPage.CALIBRATION_RESULT, ShaperAction("view", "table"))
        action = feature.resolve_semantic_action(ScreenPage.CALIBRATION_RESULT, "cal.shaper.method.ei")
        self.assertTrue(feature.handle_semantic_action(ScreenPage.CALIBRATION_RESULT, action))
        self.assertEqual(host.gcode.commands, [])
        self.assertEqual(feature.shaper.view.method, "ei")
        feature.handle_semantic_action(ScreenPage.CALIBRATION_RESULT, ShaperAction("apply"))
        fit = next(f for f in self.results['x']['shapers'] if f['name'] == 'ei')
        self.assertEqual(pending['input_shaper'], {'shaper_type_x': 'ei', 'shaper_freq_x': '%.1f' % fit['freq']})
        self.assertTrue(feature.shaper.view.page.state[ShaperState.IS_SELECTED])

    def test_feature_cancellation_uses_plugin_directly_while_sweep_is_running(self):
        host = base_controller()
        host._render_screen = mock.Mock()
        native = types.SimpleNamespace(busy=True, cancel=mock.Mock())
        host.printer = types.SimpleNamespace(lookup_object=lambda name, default=None: native)
        feature = CalibrationFeature(host)
        feature.calibration_kind = "shaper"
        self.assertTrue(feature.handle_immediate_action(ScreenPage.CALIBRATION_PROGRESS, "cal.cancel"))
        native.cancel.assert_called_once_with()
        self.assertEqual(host.gcode.commands, [])

    def test_pending_settings_exclude_reference_methods_and_invalid_frequencies(self):
        self.assertEqual(pending_shapers(None), {})
        for name, freq in (('zvd', '42.0'), ('mzv', 'nan'), ('mzv', '-1'), ('ei', 'invalid')):
            with self.subTest(name=name, freq=freq):
                self.assertEqual(pending_shapers({'input_shaper': {
                    'shaper_type_x': name, 'shaper_freq_x': freq}}), {})

    def test_unrecognized_shaper_actions_are_not_accepted_as_successful_updates(self):
        self.paint()
        self.assertFalse(self.view.select(ShaperAction('done')))
        self.assertFalse(self.view.select(ShaperAction('axis', 'z')))

    def test_save_availability_tracks_selected_axes_independently_of_browsing(self):
        self.paint()
        self.assertFalse(self.view.can_save)
        self.assertFalse(self.capture.latest.has_action('cal.shaper.save'))
        self.select_current()
        self.paint()
        self.assertEqual(set(self.view.selections), {'x'})
        self.assertTrue(self.capture.latest.has_action('cal.shaper.save'))
        self.assertTrue(self.view.page.state[ShaperState.IS_SELECTED])
        original = copy.deepcopy(self.view.selections)
        self.view.select(ShaperAction('method', 'ei'))
        self.paint()
        self.assertFalse(self.view.page.state[ShaperState.IS_SELECTED])
        self.assertEqual(self.view.selections, original)
        self.view.select(ShaperAction('axis', 'y'))
        self.paint()
        self.select_current()
        self.paint()
        self.assertEqual(set(self.view.selections), {'x', 'y'})
        self.assertTrue(self.capture.latest.has_action('cal.shaper.save'))
        self.assertEqual(self.view.selections['x'], original['x'])
        self.assertTrue(self.view.page.state[ShaperState.IS_SELECTED])

    def test_log_scale_changes_only_the_spectrum_and_retains_method_scale_across_axes(self):
        self.paint()
        linear = raster_pixels(self.view.plot.commands)
        with mock.patch("subprocess.Popen", side_effect=AssertionError("Unexpected computation")):
            self.view.select(ShaperAction("scale"))
            self.paint()
            logarithmic = raster_pixels(self.view.plot.commands)
            self.assertNotEqual(linear, logarithmic)
            self.assertEqual(self.view.page.state[ShaperState.SCALE], 'log')
            self.assertEqual(self.view.method, self.results["x"]["recommended"])
            self.view.select(ShaperAction("axis", "y"))
            self.paint()
            self.assertEqual(self.view.page.state[ShaperState.SCALE], "log")
            self.view.select(ShaperAction("scale"))
            self.view.select(ShaperAction("axis", "x"))
            self.paint()
            self.assertEqual(raster_pixels(self.view.plot.commands), linear)

    def assert_curve_point(self, pixels, color, x_fraction, y_fraction):
        expected = int(self.renderer.color(color), 16) & 0xffffff
        x = TEST_PLOT.x + TEST_PLOT.width * x_fraction
        y = TEST_PLOT.bottom - TEST_PLOT.height * y_fraction
        self.assertTrue(any(abs(px-x) <= 3 and abs(py-y) <= 3 and rgb & 0xffffff == expected
                            for (px, py), rgb in pixels.items()), (color, x, y))

    def test_shaped_curve_renders_power_rather_than_amplitude(self):
        result = dict(freq_bins=[0., 100., 200.], psd_sum=[0., 10., 20.],
                      shapers=[dict(name="mzv", response=[1., .5, 0.])])
        linear = raster_pixels(spectrum_commands(self.renderer, result, "mzv", "linear", TEST_PLOT))
        self.assert_curve_point(linear, ThemeColor.PRIMARY, .5, .5)
        # At 100 Hz the power is 10 * 0.5² = 2.5, on the same 0..20 axis.
        self.assert_curve_point(linear, ThemeColor.SECONDARY, .5, .125)
        log = raster_pixels(spectrum_commands(self.renderer, result, "mzv", "log", TEST_PLOT))
        self.assert_curve_point(log, ThemeColor.PRIMARY, .5, 5./6.)
        self.assert_curve_point(log, ThemeColor.SECONDARY, .5, (4. + math.log10(2.5))/6.)

    def test_reference_and_measurement_render_on_one_absolute_axis(self):
        result = dict(freq_bins=[0., 100., 200.], psd_sum=[0., 10., 20.],
                      shapers=[dict(name='mzv', response=[1., .5, 0.])])
        reference = dict(freq_bins=[0., 80., 160., 200.], psd_sum=[0., 100., 50., 0.])
        pixels = raster_pixels(spectrum_commands(self.renderer, result, 'mzv', 'linear', TEST_PLOT,
                                                 reference=reference))
        self.assert_curve_point(pixels, ThemeColor.DIM, .4, 1.)
        self.assert_curve_point(pixels, ThemeColor.PRIMARY, .5, .1)
        self.assert_curve_point(pixels, ThemeColor.SECONDARY, .5, .025)

    def test_reference_changes_the_graph_and_missing_reference_matches_an_uncompared_graph(self):
        tree = self.view.write(self.results, reference=self.results, reference_at=1.)
        tree.draw(self.renderer, tree.state)
        original = raster_pixels(self.view.plot.commands)
        changed = copy.deepcopy(self.results)
        changed['x']['psd_sum'] = [v * 2 for v in changed['x']['psd_sum']]
        tree = self.view.write(self.results, reference=changed, reference_at=2.)
        tree.draw(self.renderer, tree.state)
        self.assertNotEqual(raster_pixels(self.view.plot.commands), original)
        self.view.select(ShaperAction('axis', 'y'))
        tree = self.view.write(self.results, reference={'x': changed['x']}, reference_at=2.)
        tree.draw(self.renderer, tree.state)
        compared = raster_pixels(self.view.plot.commands)
        tree = self.view.write(self.results)
        tree.draw(self.renderer, tree.state)
        self.assertEqual(raster_pixels(self.view.plot.commands), compared)

    def test_absolute_scale_tracks_peak_and_zero_input_has_no_large_filter_curve(self):
        first = spectrum_scale([0., 10000., 15000.], "linear")
        second = spectrum_scale([0., 20000., 30000.], "linear")
        self.assertEqual(first["top"], 15000.)
        self.assertEqual(second["top"], 2 * first["top"])
        for scale in ("linear", "log"):
            commands = spectrum_commands(self.renderer, dict(freq_bins=[0., 100., 200.], psd_sum=[0., 0., 0.],
                shapers=[dict(name="mzv", response=[1., .5, 1.])]), "mzv", scale, TEST_PLOT)
            strokes = [c for c in commands if hasattr(c, 'payload')]
            self.assertTrue(strokes)
            for command in strokes:
                tokens = shlex.split(command)
                top = int(tokens[tokens.index('-p') + 2])
                self.assertGreaterEqual(top, TEST_PLOT.bottom - 1)
                self.assertLessEqual(top + decode_fxi1(command.payload)['height']-1, TEST_PLOT.bottom)

    def test_actual_result_fits_release_queue_and_every_binary_transport_frame(self):
        self.paint()
        commands = self.renderer.begin_page('Input shaper', back=True)
        commands += self.view.page.draw(self.renderer, self.view.page.state)
        self.assertTrue(accepted_by_loop(commands, max_batch=65536))

    def test_changed_data_updates_pixels_and_repeated_axis_switches_keep_the_right_graph(self):
        self.paint()
        original = raster_pixels(self.view.plot.commands)
        for _ in range(3):
            self.view.select(ShaperAction('axis', 'y'))
            self.paint()
            self.view.select(ShaperAction('axis', 'x'))
            self.paint()
            self.assertEqual(raster_pixels(self.view.plot.commands), original)
        self.results['x']['psd_sum'][20] *= 100
        self.paint()
        self.assertNotEqual(raster_pixels(self.view.plot.commands), original)

    def test_many_different_graphs_do_not_retain_memory_proportional_to_their_count(self):
        result = copy.deepcopy(self.results['x'])
        plot = SpectrumPlot(result, method=result['recommended'])
        bounds = Rect(0, 0, 80, 40)
        tracemalloc.start()
        self.addCleanup(tracemalloc.stop)
        sizes = []
        for i in range(96):
            plot.result = dict(result, psd_sum=[p*(i+1) for p in result['psd_sum']])
            plot.draw(self.renderer, {}, bounds)
            if i in (31, 95):
                gc.collect()
                sizes.append(tracemalloc.get_traced_memory()[0])
        self.assertLess(sizes[1], sizes[0] * 2)

    def test_comparison_releases_graphs_and_returning_restores_the_same_pixels(self):
        self.paint()
        original = raster_pixels(self.view.plot.commands)
        self.view.select(ShaperAction('view', 'table'))
        self.paint()
        self.assertFalse(self.view.plot.commands)
        self.view.select(ShaperAction('view', 'graph'))
        self.paint()
        self.assertEqual(raster_pixels(self.view.plot.commands), original)

    def test_reference_zvd_cannot_replace_the_selected_applicable_shaper(self):
        self.results['x']['shapers'].append(dict(self.results['x']['shapers'][0],
                                               name='zvd', reference_only=True))
        self.paint()
        self.select_current()
        self.view.select(ShaperAction('method', 'zvd'))
        self.paint()
        self.assertFalse(self.view.can_apply)
        self.assertTrue(self.view.can_save)
        self.assertNotEqual(self.view.selections['x']['name'], 'zvd')
        self.assertFalse(self.view.page.state[ShaperState.IS_SELECTED])
        self.assertFalse(self.capture.latest.has_action('cal.shaper.apply'))
        self.assertTrue(self.capture.latest.has_action('cal.shaper.save'))
        self.assertEqual(self.view.method, 'zvd')
        self.view.select(ShaperAction('view', 'table'))
        self.paint()
        self.assertTrue(self.capture.latest.has_action('cal.shaper.method.zvd'))
        self.assertTrue(self.view.page.state[ShaperState.HAS_ZVD])
        self.view.select(ShaperAction('axis', 'y'))
        self.view.select(ShaperAction('step', 'prev'))
        self.assertNotEqual(self.view.method, 'zvd')
        self.view.select(ShaperAction('method', 'zvd'))
        self.assertNotEqual(self.view.method, 'zvd')

    def test_forged_reference_apply_and_unselected_save_send_no_commands(self):
        self.results['x']['shapers'].append(dict(self.results['x']['shapers'][0],
                                               name='zvd', reference_only=True))
        host = base_controller()
        host.renderer = self.renderer
        native = opened_result(self.results)
        pending_config(host, native)
        feature = CalibrationFeature(host)
        feature.calibration_kind = 'shaper'
        show_feature_page(feature, ScreenPage.CALIBRATION_RESULT)
        feature.shaper.view.select(ShaperAction('method', 'zvd'))
        for route in ('apply', 'save'):
            self.assertFalse(feature.handle_semantic_action(ScreenPage.CALIBRATION_RESULT, ShaperAction(route)))
        self.assertEqual(host.gcode.commands, [])

    def test_method_steps_and_comparison_page_keep_selection(self):
        self.paint()
        self.assertTrue(self.capture.latest.has_action("cal.shaper.step.next"))
        with mock.patch("subprocess.Popen", side_effect=AssertionError("Unexpected computation")):
            self.view.select(ShaperAction("step", "prev"))
            self.view.select(ShaperAction("step", "next"))
            self.assertEqual(self.view.method, self.results["x"]["recommended"])
            self.view.select(ShaperAction("view", "table"))
            self.paint()
            self.assertTrue(self.capture.latest.has_action("cal.shaper.method.3hump_ei"))
            self.view.select(ShaperAction("method", "ei"))
            self.paint()
            self.view.select(ShaperAction("view", "graph"))
            self.paint()
        self.assertEqual(self.view.method, "ei")

    def test_new_result_releases_previous_record_and_resets_save_availability(self):
        self.paint()
        self.select_current()
        self.assertTrue(self.view.can_save)
        self.results = {"x": copy.deepcopy(self.results["x"])}
        self.selections = {}
        self.paint()
        self.assertIs(self.view.results, self.results)
        self.assertFalse(self.view.can_save)


class ShaperWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.host = base_controller()
        self.host.renderer = FeatherRenderer()
        self.capture = RenderCapture(self.host.renderer)
        self.native = opened_result(
            {"x": json.loads((ROOT / "tests/fixtures/shaper_reference.json").read_text())["cases"][0]},
            cancel=mock.Mock())
        self.status = dict(job_id=4, state="measuring", axis="x", requested_axes=("x", "y"),
                           completed_axes=(), frequency=43., freq_start=5., freq_end=100.,
                           samples=32000, elapsed=50., cancel_requested=False)
        self.native.get_status = lambda eventtime: dict(self.status)
        self.native.get_history = lambda: [dict(id='latest', saved_at=1., axes=['x'], latest=True)]
        self.pending = pending_config(self.host, self.native)
        self.feature = CalibrationFeature(self.host)
        self.feature.calibration_kind = "shaper"
        self.host._render_screen = self.host._paint_page_values = lambda: show_feature_page(self.feature, self.host.page)
        def show(page):
            self.host.page = page
            show_feature_page(self.feature, page)
        self.host._show_page = show

    def compose_dialogs(self):
        self.host.feature_manager = LazyFeatureManager(self.host, (FeatureSpec(
            'calibration', 'feather.features.calibration', 'CalibrationFeature',
            (ScreenPage.CALIBRATION_RESULT, ScreenPage.CALIBRATION_CONFIRM)),))
        self.host.feature_manager._instances['calibration'] = self.feature
        self.host._restart_klipper = mock.Mock()
        self.host.page = ScreenPage.CALIBRATION_RESULT
        self.capture = composed_controller_surface(self.host, lambda: show_feature_page(self.feature, self.host.page))
        self.host._render_screen()

    def press(self, action):
        self.host.last_action_time = -100.
        self.host._dispatch_action(action)

    def open_saved_result(self):
        """Open the latest saved record through the start and history pages."""
        def restore(result_id=None):
            self.native.state = 'complete'
            self.native.revision += 1
        self.native.restore_saved = mock.Mock(side_effect=restore)
        self.host._show_page(ScreenPage.CALIBRATION_CONFIRM)
        for wire_id in ('cal.shaper.last', 'cal.shaper.record.0'):
            action = self.feature.resolve_semantic_action(self.host.page, wire_id)
            self.assertTrue(self.feature.handle_semantic_action(self.host.page, action))
        self.assertEqual(self.host.page, ScreenPage.CALIBRATION_RESULT)

    def test_saved_result_entry_opens_results_without_starting_calibration(self):
        self.native.state = 'idle'
        def restore(result_id=None):
            self.native.state = 'complete'
            self.native.revision += 1
        self.native.restore_saved = mock.Mock(side_effect=restore)
        self.host.page = ScreenPage.CALIBRATION_CONFIRM
        show_feature_page(self.feature, self.host.page)
        self.assertTrue(self.capture.latest.has_action('cal.shaper.last'))
        action = self.feature.resolve_semantic_action(self.host.page, 'cal.shaper.last')
        with mock.patch('subprocess.Popen', side_effect=AssertionError('Started acquisition')):
            self.assertTrue(self.feature.handle_semantic_action(self.host.page, action))
        self.native.restore_saved.assert_not_called()
        self.assertEqual(self.host.page, ScreenPage.CALIBRATION_CONFIRM)
        self.assertTrue(self.capture.latest.has_action('cal.shaper.record.0'))
        action = self.feature.resolve_semantic_action(self.host.page, 'cal.shaper.record.0')
        self.assertTrue(self.feature.handle_semantic_action(self.host.page, action))
        self.native.restore_saved.assert_called_once_with('latest')
        self.assertEqual(self.host.page, ScreenPage.CALIBRATION_RESULT)
        self.assertIs(self.feature.shaper.view.results, self.native.record.results)
        self.assertTrue(self.capture.latest.has_action('cal.shaper.apply'))
        self.assertEqual(self.host.gcode.commands, [])

    def test_history_lists_the_saved_records(self):
        self.native.get_history = lambda: [dict(id=name, saved_at=1., axes=['x'], latest=False) for name in ('a', 'b')]
        self.host._show_page(ScreenPage.CALIBRATION_CONFIRM)
        self.assertTrue(self.feature.handle_semantic_action(self.host.page, ShaperAction('last')))
        self.assertTrue(self.capture.latest.has_action('cal.shaper.record.1'))

    def test_progress_without_a_running_job_is_left_to_the_painter(self):
        self.native.busy = False
        self.assertIsNone(self.feature.page_content(ScreenPage.CALIBRATION_PROGRESS))
        self.native.busy, self.native.job.id = True, 4
        self.assertIsNotNone(self.feature.page_content(ScreenPage.CALIBRATION_PROGRESS))

    def test_last_result_button_is_disabled_without_a_saved_result(self):
        self.native.get_history = lambda: []
        self.host.page = ScreenPage.CALIBRATION_CONFIRM
        show_feature_page(self.feature, self.host.page)
        self.assertFalse(self.capture.latest.has_action('cal.shaper.last'))
        self.assertFalse(self.feature.handle_semantic_action(self.host.page, ShaperAction('last')))
        self.assertEqual(self.host.gcode.commands, [])

    def start_measurement(self, finish=None):
        """Press START and run the queued calibration callback; returns the command it ran."""
        callbacks = []
        self.host.reactor.register_callback = callbacks.append
        self.host._run_script = mock.Mock(side_effect=finish)
        action = self.feature.resolve_semantic_action(self.host.page, "cal.shaper.start")
        self.assertTrue(self.feature.handle_semantic_action(self.host.page, action))
        self.assertEqual(len(callbacks), 1)
        callbacks.pop()(self.host.reactor.monotonic())
        measurement, cleanup = self.host._run_script.call_args_list
        self.assertEqual(cleanup, mock.call("_SCHEDULE_MOTOR_OFF"))
        return measurement.args[0]

    def selected_vibrations(self):
        """The one vibration method the start page shows selected."""
        selected = [method for method in ("standard", "sweeping")
                    if self.capture.latest.button("cal.shaper.vibrations." + method).state == "selected"]
        self.assertEqual(len(selected), 1, selected)
        return selected[0]

    def choose_vibrations(self, method):
        self.host._show_page(ScreenPage.CALIBRATION_CONFIRM)
        action = self.feature.resolve_semantic_action(self.host.page, "cal.shaper.vibrations." + method)
        self.assertTrue(self.feature.handle_semantic_action(self.host.page, action))
        self.assertEqual(self.host.page, ScreenPage.CALIBRATION_CONFIRM)

    def test_start_runs_the_vibration_method_the_page_shows_selected(self):
        commands = {"standard": "FORGE_SHAPER_CALIBRATE SWEEP=0", "sweeping": "FORGE_SHAPER_CALIBRATE SWEEP=1"}
        # Whichever method is the default, the selected button is the one START runs.
        self.host._show_page(ScreenPage.CALIBRATION_CONFIRM)
        selected = self.selected_vibrations()
        self.assertEqual(self.start_measurement(), commands[selected])

        for method in ("sweeping", "standard", "sweeping"):
            self.choose_vibrations(method)
            self.assertEqual(self.selected_vibrations(), method)
            self.assertEqual(self.start_measurement(), commands[method])

        # Neither the finished job nor another calibration resets the choice.
        self.feature.calibration_kind = "pid_bed"
        self.host.reactor.register_callback = lambda callback: None
        self.feature._start_calibration()
        self.feature.calibration_kind = "shaper"
        self.host._show_page(ScreenPage.CALIBRATION_CONFIRM)
        self.assertEqual(self.selected_vibrations(), "sweeping")
        self.assertEqual(self.start_measurement(), commands["sweeping"])

    def test_fresh_measurement_after_a_saved_result_asks_before_leaving(self):
        self.pending['input_shaper'] = {'shaper_type_y': 'mzv', 'shaper_freq_y': '55.2'}
        self.open_saved_result()
        self.host._show_message = mock.Mock(return_value=None)
        self.feature.back(self.host.page)
        self.host._show_message.assert_not_called()
        self.assertEqual(self.host.page, ScreenPage.CALIBRATION_CONFIRM)
        # Leave the saved-record list for the start page.
        self.assertTrue(self.feature.back(self.host.page))

        def finish(_command):
            self.native.job = plugin.Job(self.native.job.id + 1, ("x",))
            self.native.state, self.native.revision = "complete", self.native.revision + 1
            self.native.record = plugin.Record("fresh", 2., self.native.record.results)
        self.start_measurement(finish)
        self.assertEqual(self.host.page, ScreenPage.CALIBRATION_RESULT)
        self.assertIs(self.feature.shaper.view.results, self.native.record.results)
        self.feature.back(self.host.page)
        self.host._show_message.assert_called_once()
        self.assertEqual(self.host.page, ScreenPage.CALIBRATION_RESULT)

    def test_history_back_returns_to_start_and_invalid_slots_cannot_open_records(self):
        self.native.restore_saved = mock.Mock()
        self.host.page = ScreenPage.CALIBRATION_CONFIRM
        self.feature.handle_semantic_action(self.host.page, ShaperAction('last'))
        self.assertTrue(self.capture.latest.has_action('cal.shaper.record.0'))
        self.assertFalse(self.feature.handle_semantic_action(self.host.page, ShaperAction('record', '-1')))
        self.assertFalse(self.feature.handle_semantic_action(self.host.page, ShaperAction('record', '5')))
        self.native.restore_saved.assert_not_called()
        self.feature.back(self.host.page)
        self.assertIsNone(self.feature.shaper.history)
        self.assertTrue(self.capture.latest.has_action('cal.shaper.last'))

    def test_progress_updates_visible_output_and_disables_cancel_when_requested(self):
        self.feature._prepare_calibration_progress()
        self.native.busy, self.native.job.id = True, 4
        self.host.page = ScreenPage.CALIBRATION_PROGRESS
        show_feature_page(self.feature, self.host.page)
        self.assertTrue(self.capture.latest.has_action('cal.cancel'))
        for state, axis, completed in (("processing", "x", ()), ("measuring", "y", ("x",)),
                                       ("processing", "y", ("x",))):
            previous = tuple(text.value for text in self.capture.latest.texts)
            self.status.update(state=state, axis=axis, completed_axes=completed, elapsed=100.)
            self.feature.update(100.)
            self.assertNotEqual(tuple(text.value for text in self.capture.latest.texts), previous)
        self.native.cancel.side_effect = lambda: self.status.update(cancel_requested=True)
        self.assertTrue(self.feature.handle_immediate_action(self.host.page, "cal.cancel"))
        self.native.cancel.assert_called_once_with()
        self.assertFalse(self.capture.latest.has_action('cal.cancel'))
        self.assertEqual(self.host.gcode.commands, [])

    def test_console_apply_refreshes_save_availability_without_a_new_measurement(self):
        self.host._show_page(ScreenPage.CALIBRATION_RESULT)
        self.assertFalse(self.capture.latest.has_action('cal.shaper.save'))
        self.host.gcode.run_script_from_command('FORGE_SHAPER_APPLY')
        self.feature.update(100.)
        self.assertTrue(self.capture.latest.has_action('cal.shaper.save'))
        self.assertEqual(set(self.feature.shaper.selections), {'x'})
        self.assertTrue(self.feature.shaper.view.can_save)

    def test_stale_previous_result_is_hidden_after_legacy_or_failed_run(self):
        show_feature_page(self.feature, ScreenPage.CALIBRATION_RESULT)
        self.assertIsNotNone(self.feature.shaper.view)
        self.feature._prepare_calibration_progress()
        self.assertIsNone(self.feature.shaper.view)
        show_feature_page(self.feature, ScreenPage.CALIBRATION_RESULT)
        self.assertIsNone(self.feature.shaper.view)
        self.assertIsNone(self.feature.resolve_semantic_action(ScreenPage.CALIBRATION_RESULT, "cal.shaper.apply"))

    def test_external_cancel_and_error_use_current_native_outcome(self):
        for state in ("cancelled", "error"):
            self.feature._prepare_calibration_progress()
            self.feature.external_context_id = 7
            self.native.job.id += 1
            self.native.state, self.native.error = state, "Disk full"
            self.host.page = ScreenPage.CALIBRATION_PROGRESS
            self.feature._finish_external(7, "interrupted", 0.)
            self.assertEqual(self.feature.calibration_cancelled, state == "cancelled")
            self.assertEqual(self.feature.calibration_error, self.native.error if state == "error" else None)
            self.assertIsNone(self.feature.shaper.view)

    def test_view_does_not_invent_a_selection_when_configfile_did_not_change(self):
        self.host._show_page(ScreenPage.CALIBRATION_RESULT)
        self.host._run_script = mock.Mock()
        self.feature.handle_semantic_action(self.host.page, ShaperAction('apply'))
        self.assertEqual(self.feature.shaper.selections, {})
        self.assertFalse(self.feature.shaper.view.can_save)
        self.assertFalse(self.feature.shaper.view.page.state[ShaperState.IS_SELECTED])

    def test_pending_selection_survives_reopening_and_save_refreshes_changed_values(self):
        self.pending['input_shaper'] = {'shaper_type_y': 'mzv', 'shaper_freq_y': '55.2'}
        self.compose_dialogs()
        self.assertTrue(self.capture.latest.has_action('cal.shaper.save'))
        self.feature.shaper.release_view()
        show_feature_page(self.feature, ScreenPage.CALIBRATION_RESULT)
        self.assertEqual(self.feature.shaper.view.selections, {'y': {'name': 'mzv', 'freq': 55.2}})
        self.feature.handle_semantic_action(ScreenPage.CALIBRATION_RESULT, ShaperAction('save'))
        self.pending['input_shaper']['shaper_freq_y'] = '56.0'
        self.press('shaper.save.confirm')
        self.host._restart_klipper.assert_not_called()
        self.assertTrue(self.capture.latest.has_action('shaper.save.confirm'))
        self.assertTrue(any('56.0' in text.value for text in self.capture.latest.texts))
        self.press('shaper.save.confirm')
        self.host._restart_klipper.assert_called_once_with('SAVE_CONFIG')

    def test_cleared_pending_settings_disable_save_again(self):
        self.host._show_page(ScreenPage.CALIBRATION_RESULT)
        self.feature.handle_semantic_action(ScreenPage.CALIBRATION_RESULT, ShaperAction('apply'))
        configfile = types.SimpleNamespace(get_status=lambda now: {'save_config_pending_items': {}})
        self.host.printer.lookup_object = lambda name, default=None: configfile if name == 'configfile' else self.native
        show_feature_page(self.feature, ScreenPage.CALIBRATION_RESULT)
        self.assertFalse(self.feature.shaper.view.can_save)
        self.assertFalse(self.capture.latest.has_action('cal.shaper.save'))

    def test_real_save_dialog_routes_confirmation_and_cancel(self):
        self.compose_dialogs()
        self.assertFalse(self.feature.handle_semantic_action(ScreenPage.CALIBRATION_RESULT, ShaperAction('save')))
        self.feature.handle_semantic_action(ScreenPage.CALIBRATION_RESULT, ShaperAction('apply'))
        self.feature.handle_semantic_action(ScreenPage.CALIBRATION_RESULT, ShaperAction('save'))
        self.assertIsNotNone(self.host._current_dialog())
        self.assertTrue(self.capture.latest.has_action('shaper.save.confirm'))
        self.assertFalse(self.feature.confirm_shaper_action('leave'))
        self.host._restart_klipper.assert_not_called()
        self.host.last_action_time = -100.
        self.host._dispatch_action('message.ok')
        self.assertIsNone(self.host._current_dialog())
        self.host._restart_klipper.assert_not_called()
        self.feature.handle_semantic_action(ScreenPage.CALIBRATION_RESULT, ShaperAction('save'))
        self.host.last_action_time = -100.
        self.host._dispatch_action('shaper.save.confirm')
        self.host._restart_klipper.assert_called_once_with('SAVE_CONFIG')

    def show_on_the_real_screen(self):
        """The result page through the controller's own screen over a real renderer."""
        for method in ("_show_page", "_paint_page_values"):
            self.host.__dict__.pop(method, None)
        self.feature, self.capture = feature_on_screen(self.host, "calibration")
        self.feature.calibration_kind = "shaper"
        self.host.page = ScreenPage.CALIBRATION_RESULT
        self.host._render_screen()

    def test_table_method_selection_keeps_action_generation_and_sends_no_commands(self):
        self.show_on_the_real_screen()
        self.feature.handle_semantic_action(ScreenPage.CALIBRATION_RESULT, ShaperAction('view', 'table'))
        previous = self.feature.shaper.view.method
        self.assertNotEqual(previous, 'ei')
        generation = self.host.renderer.generation

        self.feature.handle_semantic_action(ScreenPage.CALIBRATION_RESULT, ShaperAction('method', 'ei'))

        self.assertEqual(self.feature.shaper.view.method, 'ei')
        self.assertEqual(self.host.renderer.generation, generation)
        self.assertEqual(self.host.gcode.commands, [])

    def test_table_no_op_axis_selection_keeps_touch_generation_and_row_feedback(self):
        self.show_on_the_real_screen()
        self.feature.handle_semantic_action(ScreenPage.CALIBRATION_RESULT, ShaperAction('view', 'table'))
        generation = self.host.renderer.generation
        for _ in range(4):
            shown = len(self.capture.batches)
            self.feature.handle_semantic_action(ScreenPage.CALIBRATION_RESULT, ShaperAction('axis', 'x'))
            self.assertEqual(self.capture.batches[shown:], [])
            self.assertEqual(self.host.renderer.generation, generation)
            self.assertEqual(self.host.renderer.decode_action('%d:cal.shaper.method.ei' % generation),
                             'cal.shaper.method.ei')

    def test_back_from_unchanged_saved_results_releases_view_and_returns_to_shaper_start(self):
        self.open_saved_result()
        self.feature.handle_semantic_action(self.host.page, ShaperAction('method', 'ei'))
        plot = self.feature.shaper.view.plot
        self.assertTrue(plot.commands)
        self.assertTrue(self.feature.allows_action(ScreenPage.CALIBRATION_RESULT, "nav.back"))
        self.assertTrue(self.feature.back(ScreenPage.CALIBRATION_RESULT))
        self.assertEqual(self.host.page, ScreenPage.CALIBRATION_CONFIRM)
        self.assertIsNone(self.feature.shaper.view)
        self.assertFalse(plot.commands)
        self.assertEqual(self.host.gcode.commands, [])

    def test_list_back_returns_to_graph_without_warning_or_leaving_results(self):
        self.host._show_page(ScreenPage.CALIBRATION_RESULT)
        self.feature.handle_semantic_action(self.host.page, ShaperAction('view', 'table'))
        view = self.feature.shaper.view
        self.host._show_message = mock.Mock()
        self.assertFalse(self.capture.latest.has_action('cal.shaper.view.graph'))
        self.assertTrue(self.feature.back(self.host.page))
        self.assertEqual(self.host.page, ScreenPage.CALIBRATION_RESULT)
        self.assertIs(self.feature.shaper.view, view)
        self.assertEqual(view.mode, 'graph')
        self.host._show_message.assert_not_called()
        self.assertTrue(view.plot.commands)

    def test_saved_result_warns_after_selection_changes_and_leaving_keeps_pending_settings(self):
        self.open_saved_result()
        self.compose_dialogs()
        self.feature.handle_semantic_action(self.host.page, ShaperAction('method', 'ei'))
        self.feature.handle_semantic_action(self.host.page, ShaperAction('apply'))
        selected = copy.deepcopy(self.feature.shaper.selections)
        self.feature.back(self.host.page)
        self.assertTrue(self.capture.latest.has_action('shaper.leave.confirm'))
        self.assertEqual(self.host.page, ScreenPage.CALIBRATION_RESULT)
        self.press('shaper.leave.confirm')
        self.assertEqual(self.host.page, ScreenPage.CALIBRATION_CONFIRM)
        self.assertEqual(self.feature.shaper.selections, selected)
        self.host._restart_klipper.assert_not_called()

    def test_unchanged_existing_pending_selection_does_not_warn_when_leaving_saved_result(self):
        self.pending['input_shaper'] = {'shaper_type_y': 'mzv', 'shaper_freq_y': '55.2'}
        self.open_saved_result()
        self.host._show_message = mock.Mock()
        self.feature.back(self.host.page)
        self.host._show_message.assert_not_called()
        self.assertEqual(self.host.page, ScreenPage.CALIBRATION_CONFIRM)

    def test_real_exit_dialog_can_stay_or_leave_without_applying_or_saving(self):
        self.compose_dialogs()
        view = self.feature.shaper.view
        self.feature.back(self.host.page)
        self.assertTrue(self.capture.latest.has_action('shaper.leave.confirm'))
        self.host.last_action_time = -100.
        self.host._dispatch_action('message.ok')
        self.assertIs(self.feature.shaper.view, view)
        self.assertEqual(self.host.page, ScreenPage.CALIBRATION_RESULT)
        self.feature.back(self.host.page)
        self.host.last_action_time = -100.
        self.host._dispatch_action('shaper.leave.confirm')
        self.assertEqual(self.host.page, ScreenPage.CALIBRATION_CONFIRM)
        self.assertIsNone(self.feature.shaper.view)
        self.assertFalse(view.plot.commands)
        self.assertEqual(self.host.gcode.commands, [])
        self.host._restart_klipper.assert_not_called()

    def test_two_axis_exit_confirmation_can_save_without_another_prompt(self):
        self.pending['input_shaper'] = {'shaper_type_x': 'mzv', 'shaper_freq_x': '64.2',
                                        'shaper_type_y': 'zv', 'shaper_freq_y': '53.8'}
        self.compose_dialogs()
        self.feature.back(self.host.page)
        for frequency in ('64.2', '53.8'):
            self.assertTrue(any(frequency in text.value for text in self.capture.latest.texts))
        for action in ('shaper.leave.confirm', 'shaper.save.confirm', 'message.ok'):
            self.assertTrue(self.capture.latest.has_action(action))
        self.host._restart_klipper.assert_not_called()
        self.host.last_action_time = -100.
        self.host._dispatch_action('shaper.save.confirm')
        self.host._restart_klipper.assert_called_once_with('SAVE_CONFIG')
        self.assertEqual(self.host.gcode.commands, [])

    def test_save_from_exit_refreshes_the_exit_prompt_if_pending_values_changed(self):
        self.pending['input_shaper'] = {'shaper_type_x': 'mzv', 'shaper_freq_x': '64.2'}
        self.compose_dialogs()
        self.feature.back(self.host.page)
        self.pending['input_shaper']['shaper_freq_x'] = '65.0'
        self.press('shaper.save.confirm')
        self.host._restart_klipper.assert_not_called()
        self.assertTrue(self.capture.latest.has_action('shaper.leave.confirm'))
        self.assertTrue(self.capture.latest.has_action('shaper.save.confirm'))
        self.assertTrue(any('65.0' in text.value for text in self.capture.latest.texts))
        self.press('shaper.save.confirm')
        self.host._restart_klipper.assert_called_once_with('SAVE_CONFIG')

    def test_exit_dialog_refreshes_if_pending_selection_changed(self):
        self.compose_dialogs()
        self.feature.back(self.host.page)
        self.pending['input_shaper'] = {'shaper_type_x': 'ei', 'shaper_freq_x': '57.6'}
        self.press('shaper.leave.confirm')
        self.assertEqual(self.host.page, ScreenPage.CALIBRATION_RESULT)
        self.assertTrue(self.capture.latest.has_action('shaper.leave.confirm'))
        self.assertTrue(any('57.6' in text.value for text in self.capture.latest.texts))
        self.press('shaper.leave.confirm')
        self.assertEqual(self.host.page, ScreenPage.CALIBRATION_CONFIRM)
        self.assertEqual(self.feature.shaper.selections['x']['name'], 'ei')

    def test_any_navigation_away_from_results_releases_cache_and_preserves_results(self):
        saved = self.native.record.results
        for destination in (ScreenPage.CALIBRATION_HOME, ScreenPage.CONTROL_HOME,
                            ScreenPage.CALIBRATION_PROGRESS, ScreenPage.PRINTING):
            with self.subTest(destination=destination):
                show_feature_page(self.feature, ScreenPage.CALIBRATION_RESULT)
                plot = self.feature.shaper.view.plot
                self.assertTrue(plot.commands)
                self.feature.on_page_changed(ScreenPage.CALIBRATION_RESULT, ScreenPage.CALIBRATION_RESULT)
                self.assertTrue(plot.commands)
                self.feature.on_page_changed(ScreenPage.CALIBRATION_RESULT, destination)
                self.assertIsNone(self.feature.shaper.view)
                self.assertFalse(plot.commands)
                self.assertIs(self.native.record.results, saved)

    def test_deactivation_clears_graph_cache_even_if_the_view_is_still_referenced(self):
        show_feature_page(self.feature, ScreenPage.CALIBRATION_RESULT)
        plot = self.feature.shaper.view.plot
        self.assertTrue(plot.commands)
        self.feature.deactivate()
        self.feature.deactivate()
        self.assertIsNone(self.feature.shaper.view)
        self.assertFalse(plot.commands)

    def test_controller_navigation_notifies_calibration_to_release_the_graph(self):
        self.host._show_page(ScreenPage.CALIBRATION_RESULT)
        plot = self.feature.shaper.view.plot
        self.assertTrue(plot.commands)
        self.host.feature_manager = LazyFeatureManager(self.host, ())
        self.host.feature_manager._instances['calibration'] = self.feature
        self.host._render_screen = mock.Mock()
        type(self.host)._show_page(self.host, ScreenPage.CONTROL_HOME)
        self.assertEqual(self.host.page, ScreenPage.CONTROL_HOME)
        self.assertIsNone(self.feature.shaper.view)
        self.assertFalse(plot.commands)

    def test_progress_snapshot_keeps_single_axis_and_whole_elapsed_seconds(self):
        status = dict(self.status, requested_axes=("y",), axis="y", elapsed=61.9)
        snapshot = progress_ui.snapshot(status)
        self.assertEqual(snapshot['elapsed'], 61)
        self.assertEqual(snapshot['requested_axes'], ('y',))
        renderer = self.host.renderer
        commands = renderer.begin_page(progress_ui.PAGE.title)
        commands += progress_ui.PAGE.draw(renderer, progress_ui.values(snapshot))
        renderer.send(commands)
        self.assertTrue(self.capture.latest.has_action('cal.cancel'))


class ShaperMacroTests(unittest.TestCase):
    def execution(self, state):
        return MacroExecution([(ROOT / 'macros/shaper.cfg', name, 'gcode_macro')
                               for name in ('FORGE_SHAPER', 'FORGE_SHAPER_CALIBRATE')],
                              {'print_stats': {'state': state}}, terminal=lambda command: None)

    def test_macro_rejects_prints_before_any_command(self):
        for state in ('printing', 'paused'):
            with self.subTest(state=state):
                execution = self.execution(state)
                with self.assertRaises(MacroActionError):
                    execution.run('FORGE_SHAPER')
                self.assertEqual(execution.commands, [])

if __name__ == "__main__":
    unittest.main()
