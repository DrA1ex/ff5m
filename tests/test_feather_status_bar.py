## CPU telemetry and compact FF5M status bar behavior.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
## This file may be distributed under the terms of the GNU GPLv3 license

import io
import pathlib
import queue
import shlex
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / '.py/klipper/plugins'))

from feather.cpu_usage import CpuUsageSampler, read_cpu_counters
from feather.files import FileWorker
from ff5m_ui.status_bar import PrinterRenderer, footer_values
from tests.feather_render_test_helper import RenderCapture
from ui import ThemeColor, ThemeRole


class WorkerServices:
    def __init__(self):
        self.services = []

    def add_service(self, service):
        self.services.append(service)
        return True


class CpuUsageTest(unittest.TestCase):
    def sampler(self, *samples):
        reader = mock.Mock(side_effect=samples)
        worker = WorkerServices()
        sampler = CpuUsageSampler(worker, reader)
        self.assertEqual(worker.services, [sampler])
        return sampler, reader

    def test_aggregate_reader_excludes_already_counted_guest_time(self):
        with mock.patch('builtins.open', return_value=io.StringIO(
                'cpu 200 20 30 40 10 5 5 2 90 10\ncpu0 999 999 999 999\n')):
            self.assertEqual(read_cpu_counters(), (312, 50))

    def test_reader_accepts_old_kernel_counters_and_rejects_invalid_samples(self):
        with mock.patch('builtins.open', return_value=io.StringIO('cpu 10 20 30 40\n')):
            self.assertEqual(read_cpu_counters(), (100, 40))
        for line in ('', 'cpu0 1 2 3 4', 'cpu 1 2 3', 'cpu 1 2 x 4', 'cpu -1 2 3 4'):
            with self.subTest(line=line), mock.patch('builtins.open', return_value=io.StringIO(line)):
                with self.assertRaises(ValueError):
                    read_cpu_counters()

    def test_two_core_aggregate_is_normalized_to_zero_through_one_hundred(self):
        # Each interval advances 200 total ticks across two cores.
        sampler, reader = self.sampler((200, 100), (400, 200), (600, 200), (800, 400))
        self.assertIsNone(sampler.get_percent(0))
        for now, expected in ((0, None), (1, 50), (2, 100), (3, 0)):
            sampler.poll(now)
            self.assertEqual(sampler.get_percent(now), expected)
        self.assertEqual(reader.call_count, 4)

    def test_early_poll_and_cached_reads_do_not_collect_or_enqueue_work(self):
        sampler, reader = self.sampler((100, 50), (200, 75))
        self.assertEqual(sampler.poll(0), 1)
        self.assertEqual(sampler.poll(0.5), 1)
        sampler.poll(1)
        reader.reset_mock()
        for _ in range(100):
            self.assertEqual(sampler.get_percent(2), 75)
        reader.assert_not_called()
        self.assertEqual(sampler.get_percent(4), 75)
        self.assertIsNone(sampler.get_percent(4.01))
        self.assertIsNone(sampler.get_percent(0.99))

    def test_error_counter_reset_and_zero_delta_recover_without_bogus_percent(self):
        for invalid in (OSError('read failed'), ValueError('bad data'), (5, 1), (100, 50), (200, 40)):
            with self.subTest(invalid=invalid):
                sampler, _ = self.sampler((100, 50), invalid, (300, 100), (400, 125))
                sampler.poll(0)
                sampler.poll(1)
                self.assertIsNone(sampler.get_percent(1))
                sampler.poll(2)
                sampler.poll(3)
                self.assertEqual(sampler.get_percent(3), 75)

    def test_sample_age_uses_reactor_clock_instead_of_worker_scheduling_clock(self):
        reader = mock.Mock(side_effect=((100, 50), (200, 75)))
        clock = mock.Mock(side_effect=(1000.0, 1001.0))
        sampler = CpuUsageSampler(WorkerServices(), reader, sample_clock=clock)
        self.assertEqual(sampler.poll(100.0), 101.0)
        self.assertEqual(sampler.poll(101.0), 102.0)
        self.assertEqual(sampler.get_percent(1002.0), 75)
        self.assertIsNone(sampler.get_percent(1004.1))

    def test_worker_close_clears_sample_and_stops_reads(self):
        sampler, reader = self.sampler((100, 50), (200, 75))
        sampler.poll(0)
        sampler.poll(1)
        sampler.close()
        self.assertIsNone(sampler.get_percent(1))
        self.assertIsNone(sampler.poll(2))
        self.assertEqual(reader.call_count, 2)

    def test_busy_file_worker_never_blocks_cached_reader_and_shutdown_closes_service(self):
        entered, release = threading.Event(), threading.Event()
        callbacks = queue.Queue()
        calls = []

        def blocked_read():
            calls.append(threading.get_ident())
            entered.set()
            release.wait(3)
            return 300, 100

        sampler, _ = self.sampler((100, 50), (200, 75))
        now = time.monotonic()
        sampler.poll(now - 2)
        sampler.poll(now - 1)
        sampler._read_counters = blocked_read
        worker = FileWorker(callbacks.put)
        results = queue.Queue()
        consumer = threading.Thread(target=lambda: results.put(sampler.get_percent(time.monotonic())))
        try:
            worker.add_service(sampler)
            self.assertTrue(entered.wait(1))
            consumer.start()
            self.assertEqual(results.get(timeout=1), 75)
            self.assertNotEqual(calls[0], threading.get_ident())
            self.assertTrue(callbacks.empty())
            worker.stop()
        finally:
            release.set()
            worker.stop()
            worker._thread.join(3)
            if consumer.ident is not None:
                consumer.join(1)
        self.assertFalse(worker._thread.is_alive())
        self.assertIsNone(sampler.get_percent(time.monotonic()))


class StatusBarTest(unittest.TestCase):
    def values(self, cpu=100, memory=128 * 1024, network='255.255.255.255', status='cancelled', style='METRICS'):
        return footer_values(dict(temperature=300, target=300), dict(temperature=120, target=120),
                             cpu, memory, network, status, style)

    def test_colorful_retains_full_labels_and_vanilla_retains_original_presentation(self):
        renderer = PrinterRenderer()
        capture = RenderCapture(renderer)
        for theme in ('DEFAULT', 'CLASSIC'):
            renderer.set_theme(theme)
            renderer.footer(*self.values(style='COLORFUL'))
            self.assertEqual([text.value for text in capture.latest.texts], [
                'NOZZLE 300/300C', '·', 'BED 120/120C', '255.255.255.255', '·', 'CANCELLED'])
            colored = [shlex.split(command) for command in capture.batches[-1]
                       if command.startswith('--batch text ')]
            expected = (ThemeColor.SECONDARY, ThemeColor.DIM, ThemeColor.WARNING,
                        ThemeColor.TEXT, ThemeColor.DIM, ThemeColor.PRIMARY)
            self.assertEqual([tokens[tokens.index('-c')+1] for tokens in colored],
                             [renderer.color(color) for color in expected])
            previous_right = 19
            for text in capture.latest.texts:
                self.assertGreaterEqual(text.x, previous_right)
                previous_right = text.x + renderer.text_width(text.value, text.font)
                self.assertLessEqual(previous_right, 780)
            renderer.footer(*self.values(style='VANILLA'))
            self.assertEqual([text.value for text in capture.latest.texts], [
                'NOZZLE 300/300C | BED 120/120C', '255.255.255.255 | CANCELLED'])
            commands = [shlex.split(command) for command in capture.batches[-1]
                        if command.startswith('--batch text ')]
            self.assertTrue(all(tokens[tokens.index('-c')+1] == renderer.color(ThemeColor.PRIMARY)
                                for tokens in commands))
        self.assertTrue(all((batch.kind, batch.key) == ('state', 'footer')
                            and len(renderer._encode_frames(batch.commands)) == 1
                            for batch in capture.submitted))

    def test_memory_rounds_to_whole_mib_with_exact_halves_down(self):
        for mib, expected in ((0, 0), (0.5, 0), (0.6, 1), (1.4, 1), (1.5, 1),
                              (1.6, 2), (2.5, 2), (86.5, 86), (86.6, 87), (128, 128)):
            with self.subTest(mib=mib):
                self.assertIn('FREE %dMB' % expected, self.values(memory=mib * 1024)[0])
        for memory in (None, -1, float('nan'), float('inf')):
            with self.subTest(memory=memory):
                self.assertIn('FREE --MB', self.values(memory=memory)[0])
        self.assertIn('CPU --%', self.values(cpu=None)[0])

    def test_cpu_digit_changes_center_metrics_in_remaining_space_without_padding(self):
        renderer = PrinterRenderer()
        capture = RenderCapture(renderer)
        outer_positions = None
        for cpu in (None, 0, 9, 10, 99, 100):
            with self.subTest(cpu=cpu):
                renderer.footer(*self.values(cpu=cpu))
                texts = capture.latest.texts
                cpu_text = next(text for text in texts if text.value.startswith('CPU '))
                self.assertEqual(cpu_text.value, 'CPU %s%%' % ('--' if cpu is None else cpu))
                bed = capture.latest.text('B 120/120C')
                free = capture.latest.text('FREE 128MB')
                network = capture.latest.text('255.255.255.255')
                status = capture.latest.text('CANCELLED')
                expected_center = (bed.x + renderer.text_width(bed.value, bed.font) + network.x) / 2
                self.assertAlmostEqual((cpu_text.x + free.x + renderer.text_width(free.value, free.font)) / 2,
                                       expected_center, delta=0.5)
                current = [text.x for text in (texts[0], bed, network, status)]
                if outer_positions is None:
                    outer_positions = current
                self.assertEqual(current, outer_positions)

    def test_metrics_groups_anchor_left_center_and_right_without_padding_memory(self):
        renderer = PrinterRenderer()
        capture = RenderCapture(renderer)
        outer_positions = None
        for memory in (0, 9, 20, 128):
            with self.subTest(memory=memory):
                renderer.footer(*self.values(cpu=9, memory=memory * 1024, network='192.168.2.124'))
                frame = capture.latest
                nozzle = frame.text('N 300/300C')
                bed = frame.text('B 120/120C')
                cpu = next(text for text in frame.texts if text.value.startswith('CPU '))
                free = frame.text('FREE %dMB' % memory)
                network = frame.text('192.168.2.124')
                status = frame.text('CANCELLED')
                self.assertEqual(nozzle.x, 20)
                expected_center = (bed.x + renderer.text_width(bed.value, bed.font) + network.x) / 2
                self.assertAlmostEqual((cpu.x + free.x + renderer.text_width(free.value, free.font)) / 2,
                                       expected_center, delta=0.5)
                self.assertEqual(status.x + renderer.text_width(status.value, status.font), 780)
                current = [text.x for text in (nozzle, bed, network, status)]
                if outer_positions is None:
                    outer_positions = current
                self.assertEqual(current, outer_positions)

    def test_center_column_adapts_to_temperature_network_and_state_widths(self):
        renderer = PrinterRenderer()
        capture = RenderCapture(renderer)
        for temperature, network, status in ((0, 'OFFLINE', 'idle'),
                                             (100, '192.168.2.124', 'printing'),
                                             (300, '255.255.255.255', 'cancelled')):
            with self.subTest(temperature=temperature, network=network, status=status):
                values = footer_values(dict(temperature=temperature, target=300),
                                       dict(temperature=20, target=0), 9, 20 * 1024, network, status)
                renderer.footer(*values)
                frame = capture.latest
                bed = frame.text('B 20/0C')
                cpu = frame.text('CPU 9%')
                free = frame.text('FREE 20MB')
                address = frame.text(network)
                expected_center = (bed.x + renderer.text_width(bed.value, bed.font) + address.x) / 2
                self.assertAlmostEqual((cpu.x + free.x + renderer.text_width(free.value, free.font)) / 2,
                                       expected_center, delta=0.5)

    def test_long_ip_and_state_leave_a_visible_gap_beside_metrics(self):
        renderer = PrinterRenderer()
        capture = RenderCapture(renderer)
        renderer.footer(*self.values())
        frame = capture.latest
        bed = frame.text('B 120/120C')
        cpu = frame.text('CPU 100%')
        free = frame.text('FREE 128MB')
        network = frame.text('255.255.255.255')
        gap = renderer.text_width('·', free.font)
        self.assertGreaterEqual(cpu.x - bed.x - renderer.text_width(bed.value, bed.font), gap)
        self.assertGreaterEqual(network.x - free.x - renderer.text_width(free.value, free.font), gap)

    def test_all_colored_fields_are_one_cached_footer_batch(self):
        renderer = PrinterRenderer()
        capture = RenderCapture(renderer)
        renderer.footer(*self.values())
        renderer.footer(*self.values())
        self.assertEqual(len(capture.submitted), 1)
        batch = capture.submitted[0]
        self.assertEqual((batch.kind, batch.key), ('state', 'footer'))
        self.assertEqual(len(renderer._encode_frames(batch.commands)), 1)
        self.assertEqual([text.value for text in capture.latest.texts], [
            'N 300/300C', '·', 'B 120/120C', 'CPU 100%', '·',
            'FREE 128MB', '255.255.255.255', '·', 'CANCELLED'])
        renderer.footer(*self.values(cpu=99))
        self.assertEqual(len(capture.submitted), 2)
        self.assertTrue(capture.latest.has_text('CPU 99%'))

    def test_fields_and_middle_dots_fit_longest_values_and_follow_every_theme(self):
        renderer = PrinterRenderer()
        capture = RenderCapture(renderer)
        for theme in renderer.theme_names():
            with self.subTest(theme=theme):
                renderer.set_theme(theme)
                renderer.invalidate_footer()
                renderer.footer(*self.values())
                texts = capture.latest.texts
                self.assertEqual(len(texts), 9)
                previous_right = 19
                for text in texts:
                    self.assertGreaterEqual(text.x, previous_right)
                    previous_right = text.x + renderer.text_width(text.value, text.font)
                    self.assertLessEqual(previous_right, 780)
                colors = [ThemeRole.TEMPERATURE_NOZZLE, ThemeRole.TEMPERATURE_BED, ThemeColor.TEXT,
                          ThemeColor.TEXT, ThemeColor.SUCCESS, ThemeColor.PRIMARY]
                commands = [shlex.split(command) for command in capture.batches[-1]
                            if command.startswith('--batch text ')]
                expected = []
                for index, color in enumerate(colors):
                    if index % 2:
                        expected.append(renderer.color(ThemeColor.DIM))
                    expected.append(renderer.color(color))
                self.assertEqual([tokens[tokens.index('-c') + 1] for tokens in commands], expected)

    def test_hidden_update_restores_latest_footer_with_the_next_page(self):
        renderer = PrinterRenderer()
        capture = RenderCapture(renderer)
        renderer.footer(*self.values())
        renderer.invalidate_footer()
        renderer.footer(*self.values(cpu=15), paint=False)
        self.assertEqual(len(capture.submitted), 1)
        renderer.send(renderer.begin_page('Settings'))
        self.assertTrue(capture.latest.has_text('CPU 15%'))
        self.assertTrue(capture.latest.has_text('CANCELLED'))


if __name__ == '__main__':
    unittest.main()
