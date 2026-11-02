"""Bounded disk history, atomic replacement and reference selection."""
# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
# This file may be distributed under the terms of the GNU GPLv3 license.
import copy
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / '.py/klipper/plugins'))
import shaper_storage as storage


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = pathlib.Path(self.temp.name)
        result = json.loads((ROOT/'tests/fixtures/shaper_reference.json').read_text())['cases'][0]
        self.results = {'x': dict(result, schema=1, kind='input_shaper', axis='x')}
        self.history = storage.ResultHistory(str(self.directory))

    def test_keeps_five_results_with_stable_ids_and_exact_numeric_roundtrip(self):
        saved = {}
        for i in range(1, 8):
            results = copy.deepcopy(self.results)
            results['x']['psd_sum'][1] += i
            entries = self.history.save(results, float(i))
            saved[entries[0]['id']] = results
        self.assertEqual([e['saved_at'] for e in entries], [7., 6., 5., 4., 3.])
        self.assertEqual(len(list(self.directory.glob('*.fxs'))), 5)
        for entry in entries:
            self.assertEqual(self.history.load(entry['id']), (saved[entry['id']], entry['saved_at']))
        for expired in set(saved) - {e['id'] for e in entries}:
            with self.assertRaises(ValueError):
                self.history.load(expired)
        self.assertEqual(storage.ResultHistory(str(self.directory)).entries(), entries)

    def test_existing_last_file_is_readable_and_archived_on_the_next_measurement(self):
        storage.save_results(self.directory, self.results, 1.)
        previous = self.history.entries()[0]
        self.history.save(self.results, 2.)
        self.assertEqual(self.history.entries()[1]['id'], previous['id'])
        self.assertEqual(self.history.load(previous['id']), (self.results, 1.))

    def test_history_rejects_arbitrary_paths(self):
        self.history.save(self.results, 1.)
        for invalid in ('../last', '/tmp/last.fxs', 'unknown'):
            with self.assertRaises(ValueError):
                self.history.load(invalid)

    def test_failed_atomic_replacement_keeps_all_previous_results(self):
        self.history.save(self.results, 1.)
        self.history.save(self.results, 2.)
        before = self.history.entries()
        blob = (self.directory/storage.LAST_RESULT).read_bytes()
        replace = storage.os.replace
        def fail_latest(source, target):
            if pathlib.Path(target).name == storage.LAST_RESULT:
                raise OSError('disk full')
            return replace(source, target)
        with mock.patch.object(storage.os, 'replace', side_effect=fail_latest):
            with self.assertRaises(OSError):
                self.history.save(self.results, 3.)
        self.assertEqual(self.history.entries(), before)
        self.assertEqual((self.directory/storage.LAST_RESULT).read_bytes(), blob)
        self.assertEqual(list(self.directory.glob('*.part')), [])

    def test_corrupt_latest_does_not_hide_archives_or_prevent_a_new_save(self):
        self.history.save(self.results, 1.)
        self.history.save(self.results, 2.)
        (self.directory/storage.LAST_RESULT).write_bytes(b'broken')
        self.assertEqual([e['saved_at'] for e in self.history.entries()], [1.])
        entries = self.history.save(self.results, 3.)
        self.assertEqual([e['saved_at'] for e in entries], [3., 1.])

    def test_same_snapshot_is_not_duplicated_and_other_files_are_not_pruned(self):
        (self.directory/'notes.txt').write_text('keep')
        for _ in range(3):
            self.history.save(self.results, 1.)
        self.assertEqual(len(self.history.entries()), 1)
        self.assertEqual(len(list(self.directory.glob('*.fxs'))), 1)
        self.assertEqual((self.directory/'notes.txt').read_text(), 'keep')
