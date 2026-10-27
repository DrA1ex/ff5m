"""Correctness, cancellation and durable results of Recovery workloads.

Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
This file may be distributed under the terms of the GNU GPLv3 license.
"""
import importlib
import itertools
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.py'))
work = importlib.import_module('overclocking_tests')


def drain(test):
    """Run a workload generator to completion; return (result, progress values)."""
    progress = []
    while True:
        try:
            progress.append(next(test))
        except StopIteration as done:
            return done.value, progress


class StorageWorkloadTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'reference.bin'
        self.path.write_bytes(work.expected_block(0) + work.expected_block(1))

    def verify(self, path=None, size=2 * work.BLOCK, evicted=True):
        return drain(work.verify_known(path or self.path, size, evict=lambda path: evicted))[0]

    def emmc(self, evictions=None, size=2 * work.BLOCK):
        """Run the eMMC test on a two-MiB reference; record which files it evicted."""
        reference = Path(work.reference_path(str(self.root)))
        if not reference.exists():
            reference.parent.mkdir(exist_ok=True)
            reference.write_bytes(self.path.read_bytes())
        evicted = []
        answers = iter(evictions or itertools.repeat(True))
        def evict(path):
            evicted.append(Path(path).name)
            return next(answers)
        result, progress = drain(work.emmc_test(str(self.root), size, evict=evict))
        return result, progress, evicted

    def test_read_then_write_is_one_test_with_both_speeds(self):
        reference = Path(work.reference_path(str(self.root)))
        result, progress, evicted = self.emmc()
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(progress, sorted(progress))
        self.assertEqual(progress[-1], 1.0)
        self.assertRegex(result['detail'], r'^Read: [\d.]+ MiB/s \| [\d.]+x stock\nWrite: [\d.]+ MiB/s \| [\d.]+x stock$')
        self.assertRegex(result['speed'], r'^[\d.]+x stock$')
        # Every read starts from storage: the timed read, the reference check and the written-file check.
        self.assertEqual(evicted[:2], [reference.name] * 2)
        self.assertTrue(evicted[2].startswith('.write-test-'))
        self.assertEqual(reference.read_bytes(), self.path.read_bytes())
        self.assertEqual(sorted(path.name for path in reference.parent.iterdir()), [reference.name])

    def test_damaged_reference_fails_without_writing(self):
        reference = Path(work.reference_path(str(self.root)))
        reference.parent.mkdir()
        reference.write_bytes(work.expected_block(0) + b'x' * work.BLOCK)
        with mock.patch.object(work, 'write_known') as write:
            result, _, _ = self.emmc()
        self.assertEqual(result['status'], 'failed')
        self.assertIn('Read: failed, reference data mismatch at byte 1048576.', result['detail'])
        self.assertIn('Write: not run', result['detail'])
        write.assert_not_called()

    def test_failed_write_keeps_the_passed_read_visible_and_cleans_up(self):
        free = mock.Mock(free=1)
        with mock.patch.object(work.shutil, 'disk_usage', return_value=free):
            result, _, _ = self.emmc()
        self.assertEqual(result['status'], 'failed')
        self.assertRegex(result['detail'], r'Read: [\d.]+ MiB/s')
        self.assertIn('Write: failed, not enough free space', result['detail'])
        self.assertEqual([path.name for path in (self.root / 'reference').iterdir()],
                         [Path(work.reference_path(str(self.root))).name])

    def test_corrupted_written_file_fails_the_test(self):
        def corrupt(path, size, offset=0.0, span=1.0):
            Path(path).write_bytes(b'x' * size)
            yield offset + span
            return 1.0
        with mock.patch.object(work, 'write_known', side_effect=corrupt):
            result, _, _ = self.emmc()
        self.assertEqual(result['status'], 'failed')
        self.assertIn('Write: failed, written file data mismatch at byte 0.', result['detail'])

    def test_reads_that_may_come_from_ram_are_inconclusive(self):
        for evictions in ((True, False, True), (True, True, False)):
            with self.subTest(evictions=evictions):
                result, _, _ = self.emmc(evictions)
                self.assertEqual(result['status'], 'inconclusive')
                self.assertIn('Cache: not cleared', result['detail'])

    def test_speed_ratio_is_a_separate_part_only_once_stock_is_known(self):
        self.assertEqual(work.speed_fact(65.2, 'MiB/s', None), '65.2 MiB/s')
        self.assertEqual(work.speed_fact(65.2, 'MiB/s', 8.56), '65.2 MiB/s | 7.62x stock')
        self.assertEqual(work.short_speed(65.2, 'MiB/s', 8.56), '7.62x stock')
        self.assertEqual(work.short_speed(812.4, 'MiB/s', None), '812 MiB/s')

    def test_misplaced_or_corrupted_blocks_fail(self):
        for data in (work.expected_block(1) + work.expected_block(0),
                     work.expected_block(0) + b'x' * work.BLOCK):
            self.path.write_bytes(data)
            with self.assertRaisesRegex(RuntimeError, 'data mismatch'):
                self.verify()

    def test_sector_replay_or_swap_inside_one_mib_is_detected(self):
        original = self.path.read_bytes()
        sector_size = 512
        mutations = [
            original[:sector_size] + original[:sector_size] + original[2 * sector_size:],
            original[sector_size:2 * sector_size] + original[:sector_size] + original[2 * sector_size:]]
        for index, mutated in enumerate(mutations):
            with self.subTest(mutation=index):
                self.path.write_bytes(mutated)
                with self.assertRaisesRegex(RuntimeError, 'data mismatch'):
                    self.verify()

    def test_truncated_and_symlinked_reference_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'size'):
            self.verify(size=work.BLOCK)
        link = self.root / 'link'
        link.symlink_to(self.path)
        with self.assertRaises(OSError):
            self.verify(link)

    def test_cancelled_write_is_cleaned_without_reading(self):
        work.owned_directory(str(self.root))
        reference = Path(work.reference_path(str(self.root)))
        reference.write_bytes(self.path.read_bytes())
        test = work.emmc_test(str(self.root), size=2 * work.BLOCK, evict=lambda path: True)
        # The first written block of two.
        while next(test) < 0.5:
            pass
        self.assertTrue([path for path in reference.parent.iterdir() if path.name.startswith('.write-test-')])
        test.close()
        self.assertEqual([path.name for path in reference.parent.iterdir()], [reference.name])

    def test_low_space_prevents_the_reference_write(self):
        free = mock.Mock(free=1)
        with mock.patch.object(work.shutil, 'disk_usage', return_value=free):
            with self.assertRaisesRegex(RuntimeError, 'free space'):
                drain(work.prepare_reference(str(self.root), size=work.BLOCK))
        self.assertEqual(list((self.root / 'reference').iterdir()), [])

    def test_cleanup_keeps_reference_backups_unknown_files_and_symlinks(self):
        directory = Path(work.owned_directory(str(self.root)))
        (directory / '.write-test-owned').touch()
        (directory / '.prepare-owned').touch()
        (directory / 'user-file').touch()
        (directory / '.write-test-link').symlink_to(self.path)
        work.cleanup_owned_files(str(self.root))
        self.assertEqual({path.name for path in directory.iterdir()}, {'user-file', '.write-test-link'})
        self.assertTrue(self.path.exists())

    def test_cleanup_reports_file_before_removing_it_and_reports_completion(self):
        directory = Path(work.owned_directory(str(self.root)))
        temporary = directory / '.write-test-owned'
        temporary.touch()
        updates = []
        def progress(stage, activity):
            updates.append((stage, activity, temporary.exists()))
        work.cleanup_owned_files(str(self.root), progress=progress)
        removing = [update for update in updates if 'Removing' in update[0]]
        self.assertEqual(len(removing), 1)
        self.assertIn(temporary.name, removing[0][1])
        self.assertTrue(removing[0][2])
        self.assertFalse(updates[-1][2])
        self.assertIn('1 removed', updates[-1][1])

    def test_cleanup_does_not_hide_directory_read_errors(self):
        work.owned_directory(str(self.root))
        with mock.patch.object(work.os, 'scandir', side_effect=OSError('eMMC scan failed')):
            with self.assertRaisesRegex(OSError, 'eMMC scan failed'):
                work.cleanup_owned_files(str(self.root))

    def test_cleanup_does_not_hide_directory_stat_errors(self):
        with mock.patch.object(work.os, 'lstat', side_effect=OSError('eMMC metadata failed')):
            with self.assertRaisesRegex(OSError, 'eMMC metadata failed'):
                work.cleanup_owned_files(str(self.root))

    def test_cleanup_missing_or_symlinked_directory_does_not_remove_files(self):
        updates = []
        work.cleanup_owned_files(str(self.root), progress=lambda *update: updates.append(update))
        self.assertIn('Nothing to remove', updates[-1])
        target = self.root / 'outside'
        target.mkdir()
        owned_name = target / '.write-test-keep'
        owned_name.touch()
        (self.root / 'reference').symlink_to(target, target_is_directory=True)
        work.cleanup_owned_files(str(self.root))
        self.assertTrue(owned_name.exists())

    def test_new_reference_keeps_legacy_data_and_is_verified_before_publication(self):
        directory = Path(work.owned_directory(str(self.root)))
        legacy = directory / 'emmc-reference-v1.bin'
        legacy.write_bytes(b'legacy reference')
        result, _ = drain(work.prepare_reference(str(self.root), size=work.BLOCK))
        self.assertIn(result['status'], ('passed', 'inconclusive'))
        self.assertEqual(legacy.read_bytes(), b'legacy reference')
        current = Path(work.reference_path(str(self.root)))
        self.assertNotEqual(current, legacy)
        self.assertTrue(self.verify(current, work.BLOCK))


class CpuConditionsTest(unittest.TestCase):
    def setUp(self):
        self.points = 300.0
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.helper = self.root / 'cpu-helper'
        self.helper.write_text('#!' + sys.executable + '\n' +
            'import json, time\n'
            'for index in range(10):\n'
            ' print(json.dumps({"progress": (index + 1) / 10}), flush=True)\n'
            ' time.sleep(.02)\n'
            'print(json.dumps({"status":"passed", "detail":"verified workload", "rounds":10, "parallel_ratio":1.9,'
            ' "ram_mib":32, "read_mibs":800.0}), flush=True)\n'
            'time.sleep(.02)\n')
        self.helper.chmod(0o755)

    def run_cpu(self, observations, final_status='passed', target_hz=1248000000):
        if final_status != 'passed':
            self.helper.write_text(self.helper.read_text().replace('"status":"passed"', '"status":' + repr(final_status)))
        samples = itertools.chain(observations, itertools.repeat(observations[-1]))
        with mock.patch.object(work, 'cpu_observation', side_effect=lambda: next(samples)) as observe:
            result, _ = drain(work.cpu_test(target_hz, duration=1, helper=str(self.helper),
                                            measure_points=lambda: self.points))
        self.assertGreaterEqual(observe.call_count, 10)
        return result

    def test_initial_target_spike_followed_by_throttling_is_inconclusive(self):
        result = self.run_cpu([(60, 1248000, 1248000)] + [(65, 480000, 1248000)] * 9)
        self.assertEqual(result['status'], 'inconclusive')
        self.assertTrue(result['detail'].startswith('Verified workload.\nFull stress conditions were not established.'))

    def test_sustained_frequency_and_correctness_remain_passed(self):
        self.assertEqual(self.run_cpu([(60, 1248000, 1248000)] * 10)['status'], 'passed')

    def test_computational_failure_is_not_relabelled_as_insufficient_conditions(self):
        result = self.run_cpu([(65, 480000, 1248000)] * 10, final_status='failed')
        self.assertEqual(result['status'], 'failed')

    def test_constantly_reduced_policy_cannot_replace_the_selected_target(self):
        result = self.run_cpu([(65, 480000, 480000)] * 10)
        self.assertEqual(result['status'], 'inconclusive')

    def test_unknown_target_and_missing_frequency_are_inconclusive(self):
        for observations, target in (([(60, 1248000, 1248000)] * 10, None),
                                     ([(60, None, 1248000)] * 10, 1248000000)):
            with self.subTest(target=target):
                self.assertEqual(self.run_cpu(observations, target_hz=target)['status'], 'inconclusive')

    def test_ramp_up_before_reaching_the_target_does_not_count(self):
        result = self.run_cpu([(60, 480000, 1248000)] + [(60, 1248000, 1248000)] * 9)
        self.assertEqual(result['status'], 'passed')
        self.assertIn('Clock: 1248 MHz held, no throttling', result['detail'])

    def test_any_drop_after_reaching_the_target_is_inconclusive_and_never_shown_as_100(self):
        at_target, dip = (60, 1248000, 1248000), (60, 1200000, 1248000)
        result = self.run_cpu([at_target] * 5 + [dip] + [at_target] * 4)
        self.assertEqual(result['status'], 'inconclusive')
        self.assertRegex(result['detail'], r'Clock: (?!100%)\d+(\.\d+)?% at 1248 MHz')

    def test_a_nearly_complete_share_is_never_rounded_up_to_100(self):
        self.assertEqual(work.percent_text(1999 / 2000), '99.95%')
        self.assertEqual(work.percent_text(1.0), '100%')

    def test_cpu_speed_is_reported_without_changing_the_verdict(self):
        with mock.patch.object(work, 'STOCK_CPU_POINTS', 240.0), mock.patch.object(work, 'STOCK_MEMORY_READ_MIBS', 800.0):
            result = self.run_cpu([(60, 1248000, 1248000)] * 10)
            self.assertEqual(result['status'], 'passed')
            self.assertIn('CPU: 300 points | 1.25x stock', result['detail'])
            # The helper's verdict leads; no note about how stock was measured.
            self.assertTrue(result['detail'].startswith('Verified workload.\nSpeed: '))
            self.assertIn('Memory checked: 10 cross-core passes, 0.3 GiB', result['detail'])
            self.points = None
            result = self.run_cpu([(60, 1248000, 1248000)] * 10)
        self.assertEqual(result['status'], 'passed')
        self.assertNotIn('speed', result)
        self.assertIn('CPU: unavailable', result['detail'])

    def test_overall_speed_combines_cpu_and_memory_once_both_stock_speeds_are_known(self):
        with mock.patch.object(work, 'STOCK_CPU_POINTS', 200.0), mock.patch.object(work, 'STOCK_MEMORY_READ_MIBS', 800.0):
            result = self.run_cpu([(60, 1248000, 1248000)] * 10)
        # sqrt(300 / 200 * 800 / 800)
        self.assertEqual(result['speed'], '1.22x stock')
        self.assertIn('Speed: 1.22x stock', result['detail'])
        self.assertIn('Memory read: 800 MiB/s | 1.00x stock', result['detail'])

    def test_temperature_reports_peak_and_start(self):
        result = self.run_cpu([(45, 1248000, 1248000)] + [(61.5, 1248000, 1248000)] * 9)
        self.assertIn('Temperature: 61.5 C peak, 45.0 C at start', result['detail'])

    def test_cpu_points_come_from_a_real_child_measurement(self):
        self.assertGreater(work.cpu_points(), 0)
        with mock.patch.object(work.subprocess, 'run', side_effect=OSError('no interpreter')):
            self.assertIsNone(work.cpu_points())

    def test_policy_reduction_is_visible_and_inconclusive(self):
        result = self.run_cpu([(60, 1248000, 1248000)] + [(65, 1008000, 1008000)] * 9)
        self.assertEqual(result['status'], 'inconclusive')
        self.assertRegex(result['detail'], r'Clock: [\d.]+% at 1248 MHz, throttled')

    def test_cancel_stops_the_helper_before_returning(self):
        pid_file = self.root / 'helper-pid'
        self.helper.write_text('#!' + sys.executable + '\n'
                               'import json, os, pathlib, time\n'
                               'pathlib.Path(' + repr(str(pid_file)) + ').write_text(str(os.getpid()))\n'
                               'while True:\n'
                               ' print(json.dumps({"progress": 0.5}), flush=True)\n'
                               ' time.sleep(.05)\n')
        test = work.cpu_test(1248000000, duration=60, helper=str(self.helper))
        with mock.patch.object(work, 'cpu_observation', return_value=(60, 1248000, 1248000)):
            deadline = time.monotonic() + 5
            while not pid_file.exists() and time.monotonic() < deadline:
                next(test)
            test.close()
        pid = int(pid_file.read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)


class WorkloadRunTest(unittest.TestCase):
    def test_new_kernel_storage_errors_fail_a_passing_test(self):
        def passing(*_args):
            yield 1.0
            return {'status': 'passed', 'detail': 'verified'}
        with mock.patch.object(work, 'kernel_errors', side_effect=[set(), {'mmc0: CRC error'}]), \
                mock.patch.object(work, 'cpu_test', side_effect=passing):
            result, _ = drain(work.run('cpu-stability', '/unused', 1248000000))
        self.assertEqual(result['status'], 'failed')
        self.assertIn('CRC error', result['detail'])

    def passing_emmc(self, kernel):
        def passing(*_args):
            yield 1.0
            return {'status': 'passed', 'detail': 'Read: passed\nWrite: passed', 'speed': '65.2 MiB/s'}
        with mock.patch.object(work, 'kernel_errors', side_effect=kernel), \
                mock.patch.object(work, 'emmc_test', side_effect=passing):
            return drain(work.run('emmc-integrity', '/unused'))[0]

    def test_storage_pass_without_kernel_observation_is_inconclusive(self):
        result = self.passing_emmc([None, None])
        self.assertEqual(result['status'], 'inconclusive')
        self.assertIn('Kernel log: unavailable', result['detail'])

    def test_storage_pass_without_new_kernel_errors_keeps_its_result(self):
        self.assertEqual(self.passing_emmc([set(), set()]),
                         {'status': 'passed', 'detail': 'Read: passed\nWrite: passed', 'speed': '65.2 MiB/s'})
