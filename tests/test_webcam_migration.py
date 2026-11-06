## Contracts for the SQL migration that registers the Forge-X camera entries.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock
import uuid

ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / 'sql/00022-forge-x-webcams.sql'


def moonraker_record(value):
    """Moonraker writes records as BLOBs, unlike the text inserted by the migration."""
    return json.dumps(value).encode()


def load_moonraker_webcam():
    """Load Moonraker's real webcam component without the rest of Moonraker."""
    package = types.ModuleType('moonraker_stub')
    package.__path__ = []
    components = types.ModuleType('moonraker_stub.components')
    components.__path__ = []
    common = types.ModuleType('moonraker_stub.common')
    common.RequestType = Mock()
    for module in (package, components, common):
        sys.modules[module.__name__] = module
    spec = importlib.util.spec_from_file_location(
        'moonraker_stub.components.webcam', ROOT / '.root/moonraker/components/webcam.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class WebcamMigrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.moonraker = load_moonraker_webcam()

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = sqlite3.connect(Path(directory.name) / 'moonraker-sql.db')
        self.addCleanup(self.database.close)
        self.database.execute(
            'CREATE TABLE namespace_store (namespace TEXT NOT NULL, key TEXT NOT NULL, '
            'value record NOT NULL, PRIMARY KEY (namespace, key))')

    def migrate(self):
        self.database.executescript(MIGRATION.read_text())

    def webcams(self):
        rows = self.database.execute(
            "SELECT key, value FROM namespace_store WHERE namespace = 'webcams'").fetchall()
        return {json.loads(value)['name']: (key, json.loads(value)) for key, value in rows}

    def test_registers_both_entries_hidden_and_loadable_by_moonraker(self):
        self.migrate()
        entries = self.webcams()
        self.assertEqual(set(entries), {'4gx_camera', '4gx_screen'})
        for name, (key, record) in entries.items():
            with self.subTest(name=name):
                self.assertEqual(str(uuid.UUID(key, version=4)), key)
                self.assertIs(record['enabled'], False)
                webcam = self.moonraker.WebCam.from_database(Mock(), dict(record, uid=key))
                self.assertEqual((webcam.name, webcam.uid, webcam.source), (name, key, 'database'))
        camera = self.moonraker.WebCam.from_database(Mock(), dict(entries['4gx_camera'][1], uid='x'))
        self.assertEqual(camera.stream_url, '/webcam/?action=stream')
        screen = self.moonraker.WebCam.from_database(Mock(), dict(entries['4gx_screen'][1], uid='x'))
        self.assertEqual((screen.service, screen.stream_url), ('iframe', '/screen/'))

    def test_repeating_the_migration_keeps_entries_and_their_user_edits(self):
        self.migrate()
        key, record = self.webcams()['4gx_camera']
        edited = dict(record, rotation=90, enabled=True, urlStream='/elsewhere/')
        self.database.execute("UPDATE namespace_store SET value = ? WHERE key = ?", (moonraker_record(edited), key))
        self.migrate()
        entries = self.webcams()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries['4gx_camera'], (key, edited))

    def test_other_namespaces_and_webcams_are_untouched(self):
        other = [('webcams', 'user-uid', moonraker_record({'name': 'user_cam', 'urlStream': '/x'})),
                 ('fluidd', 'macros', moonraker_record({'stored': []}))]
        self.database.executemany('INSERT INTO namespace_store VALUES (?, ?, ?)', other)
        self.migrate()
        rows = self.database.execute("SELECT namespace, key, value FROM namespace_store").fetchall()
        for row in other:
            self.assertIn(row, rows)
        self.assertEqual(len(rows), len(other) + 2)

    def test_each_database_gets_its_own_identifiers(self):
        self.migrate()
        first = {key for key, _ in self.webcams().values()}
        self.database.execute("DELETE FROM namespace_store")
        self.migrate()
        self.assertTrue(first.isdisjoint({key for key, _ in self.webcams().values()}))


if __name__ == '__main__':
    unittest.main()
