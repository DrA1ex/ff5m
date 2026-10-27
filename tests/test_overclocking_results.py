"""Durable hardware-test evidence: interruption, binding and acceptance.

Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
This file may be distributed under the terms of the GNU GPLv3 license.
"""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.py'))
from overclocking_results import TestResults


class TestResultsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'data'
        self.profile = 'a' * 64

    def store(self):
        # A fresh object per step mirrors a new Recovery process after a reset.
        return TestResults(self.root)

    def test_unfinished_test_is_reported_once_as_interrupted(self):
        self.store().start('cpu-stability', self.profile, 'boot-a')
        self.assertEqual(self.store().take_interrupted(), 'cpu-stability')
        result = self.store().result('cpu-stability', self.profile, 'boot-a')
        self.assertEqual(result['status'], 'interrupted')
        self.assertIsNone(self.store().take_interrupted())

    def test_result_counts_only_for_the_tested_profile_and_boot(self):
        self.store().start('emmc-read', self.profile, 'boot-a')
        self.store().finish('passed', 'verified')
        self.assertEqual(self.store().result('emmc-read', self.profile, 'boot-a')['status'], 'passed')
        self.assertIsNone(self.store().result('emmc-read', self.profile, 'boot-b'))
        self.assertIsNone(self.store().result('emmc-read', 'b' * 64, 'boot-a'))

    def test_measured_speed_survives_a_new_process(self):
        self.store().start('emmc-read', self.profile, 'boot-a')
        self.store().finish('passed', 'verified', '65.2 MiB/s')
        self.assertEqual(self.store().result('emmc-read', self.profile, 'boot-a')['speed'], '65.2 MiB/s')

    def test_repeat_never_shows_the_previous_pass_while_running(self):
        self.store().start('emmc-read', self.profile, 'boot-a')
        self.store().finish('passed', 'verified')
        self.store().start('emmc-read', self.profile, 'boot-a')
        self.assertIsNone(self.store().result('emmc-read', self.profile, 'boot-a'))

    def test_acceptance_is_bound_to_the_accepted_profile(self):
        self.store().accept(self.profile, {'cpu-stability': 'not-run'})
        self.assertEqual(self.store().acceptance(self.profile)['tests'], {'cpu-stability': 'not-run'})
        self.assertIsNone(self.store().acceptance('b' * 64))

    def test_unreadable_evidence_is_treated_as_absent(self):
        self.root.mkdir()
        (self.root / 'tests.json').write_text('{broken')
        self.assertIsNone(self.store().take_interrupted())
        self.store().start('cpu-stability', self.profile, 'boot-a')
        self.store().finish('failed', 'mismatch')
        self.assertEqual(self.store().result('cpu-stability', self.profile, 'boot-a')['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
