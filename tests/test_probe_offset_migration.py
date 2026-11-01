"""Regression coverage for included probe offsets blocking SAVE_CONFIG."""
import configparser
import importlib.util
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration = load('probe_offset_migration', ROOT / '.py/migrate_probe_offset.py')
klipper = load('probe_offset_configfile', ROOT / '.py/klipper/patches/configfile.py')


class ProbeOffsetMigrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.main = self.root / 'printer.cfg'
        self.base = self.root / 'printer.base.cfg'
        self.main.write_text('[include printer.base.cfg]\n[extruder]\nrotation_distance: 4.269\n')
        self.base.write_text('[probe]\npin: !PB4\nz_offset: -0.25\nspeed: 2\n')
        self.restarts = []
        self.gcode = SimpleNamespace(error=RuntimeError,
                                    register_command=lambda *a, **k: None,
                                    request_restart=self.restarts.append)
        self.printer = SimpleNamespace(
            lookup_object=lambda name: self.gcode,
            get_start_args=lambda: {'config_file': str(self.main)})

    def reader(self):
        reader = klipper.PrinterConfig(self.printer)
        # Compatibility with Klipper's Python 3.11 parser on newer host Python.
        with mock.patch.object(configparser.RawConfigParser, 'readfp',
                               lambda p, f, filename: p.read_file(f, source=filename), create=True):
            config = reader.read_main_config()
        return reader, config

    def save(self, reader, value):
        reader.set('probe', 'z_offset', value)
        with mock.patch.object(configparser.RawConfigParser, 'readfp',
                               lambda p, f, filename: p.read_file(f, source=filename), create=True):
            reader.cmd_SAVE_CONFIG(None)

    def test_reproduces_conflict_then_allows_repeated_saves_and_reloads(self):
        reader, _ = self.reader()
        with self.assertRaisesRegex(RuntimeError, 'conflicts with included value'):
            self.save(reader, '-1.426')
        self.assertTrue(migration.migrate(self.root))
        for value in ('-1.426', '-1.401'):
            reader, _ = self.reader()
            self.save(reader, value)
            _, config = self.reader()
            self.assertEqual(config.getsection('probe').get('z_offset'), value)
            self.assertEqual(config.getsection('probe').get('pin'), '!PB4')
            self.assertEqual(config.getsection('extruder').get('rotation_distance'), '4.269')
        self.assertEqual(self.restarts, ['restart', 'restart'])

    def test_preserves_main_override_and_is_idempotent(self):
        self.main.write_text(self.main.read_text() + '\n[probe]\nz_offset: -0.72\n')
        migration.migrate(self.root)
        self.assertEqual(self.reader()[1].getsection('probe').get('z_offset'), '-0.72')
        before = (self.main.read_bytes(), self.base.read_bytes(), self.main.stat().st_mtime_ns)
        self.assertFalse(migration.migrate(self.root))
        self.assertEqual(before, (self.main.read_bytes(), self.base.read_bytes(), self.main.stat().st_mtime_ns))

    def test_preserves_autosaved_offset_and_mesh(self):
        saved = klipper.AUTOSAVE_HEADER + '\n#*# [probe]\n#*# z_offset = -1.426\n#*#\n#*# [bed_mesh auto]\n#*# points = 1, 2, 3\n'
        self.main.write_text(self.main.read_text() + saved)
        migration.migrate(self.root)
        self.assertIn(saved, self.main.read_text())
        self.assertEqual(self.reader()[1].getsection('probe').get('z_offset'), '-1.426')

    def test_partial_main_probe_section_keeps_options_from_base(self):
        self.main.write_text(self.main.read_text() + '\n[probe]\nsamples: 2\n')
        migration.migrate(self.root)
        config = self.reader()[1].getsection('probe')
        self.assertEqual(config.get('z_offset'), '-0.25')
        self.assertEqual(config.get('samples'), '2')
        self.assertEqual(config.get('pin'), '!PB4')

    def test_destination_write_failure_does_not_remove_source(self):
        with mock.patch.object(migration, 'atomic_write', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                migration.migrate(self.root)
        self.assertIn('z_offset: -0.25', self.base.read_text())

    def test_interrupted_source_write_is_recoverable(self):
        write = migration.atomic_write
        def fail_source(path, text):
            if path == self.base:
                raise OSError('interrupted')
            write(path, text)
        with mock.patch.object(migration, 'atomic_write', side_effect=fail_source):
            with self.assertRaises(OSError):
                migration.migrate(self.root)
        migration.migrate(self.root)
        self.save(self.reader()[0], '-1.426')

    def test_new_install_templates_allow_save_config_for_both_models(self):
        for model in ('Adventurer5M', 'Adventurer5MPro'):
            with self.subTest(model=model):
                source = ROOT / '.cfg/default/printer' / model
                self.main.write_text((source / 'printer.cfg').read_text())
                self.base.write_text((source / 'printer.base.cfg').read_text())
                self.save(self.reader()[0], '-1.426')
                self.assertEqual(self.reader()[1].getsection('probe').get('z_offset'), '-1.426')

    def test_boot_and_reload_run_migration(self):
        source = (ROOT / '.shell/init-main.sh').read_text()
        function = source[source.index('migrate_probe_offset() {'):source.index('fix_config() {')]
        function = function.replace('/opt/config', str(self.root))
        result = subprocess.run(['bash', '-e', '-c', function + '''
chroot() { shift 3; "$HOST_PYTHON" "$MIGRATION" "$@" --config-dir "$CONFIG_DIR"; }
migrate_probe_offset
'''], env={'HOST_PYTHON': __import__('sys').executable,
           'MIGRATION': str(ROOT / '.py/migrate_probe_offset.py'), 'CONFIG_DIR': str(self.root),
           'PATH': __import__('os').environ['PATH'], 'MOD': '/unused', 'PY': '/unused'},
           capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('z_offset:', self.base.read_text())
        self.assertIn('    migrate_probe_offset\n', source[source.index('restore_legacy_config\n'):])


if __name__ == '__main__':
    unittest.main()
