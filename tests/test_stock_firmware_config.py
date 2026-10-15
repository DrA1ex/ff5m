"""Regression tests for the stock firmware config migrator."""
import importlib.util
import pathlib
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = HERE.parent / '.py' / 'stock_firmware_config.py'
spec = importlib.util.spec_from_file_location('stock_firmware_config', SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class MigrationTests(unittest.TestCase):
    def test_bash_migration_failure_never_stops_initialization(self):
        import subprocess

        source = (HERE.parent / '.shell' / 'init-main.sh').read_text()
        guarded_call = (
            '    if ! migrate_stock_config; then\n'
            '        echo "@@ Stock firmware migration checks failed; continuing initialization."\n'
            '    fi')
        self.assertIn(guarded_call, source)

        for body in ('false; return 17', 'false; echo AFTER_FAILURE'):
            script = ('set -e\nmigrate_stock_config() { ' + body + '; }\n'
                      + guarded_call + '\necho BOOT_CONTINUES\n')
            process = subprocess.run(
                ['bash', '-c', script], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, universal_newlines=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertIn('BOOT_CONTINUES', process.stdout)
            if 'return 17' in body:
                self.assertIn('continuing initialization', process.stdout)
            else:
                self.assertIn('AFTER_FAILURE', process.stdout)

    def test_semantic_sync_ignores_order_and_deleted_sections(self):
        current = '[servo my_servo]\npin: PB4\ninitial_angle: 95\n[led chamber_light]\nwhite_pin: PA8\n'
        stock = '[led chamber_led]\nwhite_pin: PA8\n[servo my_servo]\ninitial_angle: 115\npin: PB4\n'
        result, changes = m.sync_config(current, stock)
        self.assertIn('initial_angle: 115', result)
        self.assertIn('[led chamber_light]', result)
        self.assertNotIn('[led chamber_led]', result)
        self.assertEqual([x[2] for x in changes], ['initial_angle'])

    def test_only_add_parameters_in_existing_sections(self):
        current = '[servo my_servo]\npin: PB4\n'
        stock = '[servo my_servo]\npin: PB4\ninitial_angle: 115\n[led chamber_led]\nwhite_pin: PA8\n'
        result, changes = m.sync_config(current, stock)
        self.assertIn('initial_angle: 115', result)
        self.assertNotIn('[led chamber_led]', result)
        self.assertEqual(changes[0][0], 'Added')

    def test_multiline_gcode_is_one_parameter(self):
        current = '[gcode_macro X]\ngcode:\n  G28\n  M105\n'
        stock = '[gcode_macro X]\ngcode:\n  G28\n  M106\n'
        result, changes = m.sync_config(current, stock)
        self.assertIn('  M106\n', result)
        self.assertEqual(changes[0][2], 'gcode')

    def test_preserve_forge_x_specific_parameters(self):
        result, _ = m.sync_config('[servo my_servo]\ninitial_angle: 95\ncustom: yes\n',
                                  '[servo my_servo]\ninitial_angle: 115\n')
        self.assertIn('custom: yes', result)

    def test_padding_idempotent_and_outside_save_config(self):
        current = '[mcu]\nserial: /tmp/klipper\n#*# <----- SAVE_CONFIG ----->\n#*# a = 1\n'
        first = m.pad_config(current, 5998)
        self.assertEqual(m.pad_config(first, 5998), first)
        self.assertGreaterEqual(len(first.encode()), 6254)
        self.assertLess(first.index(m.PAD_START), first.index('SAVE_CONFIG'))

    def test_duplicate_parameters_rejected(self):
        with self.assertRaises(ValueError):
            m.parse_config('[mcu]\nserial: /tmp/a\nserial: /tmp/b\n')

    def test_detects_only_installed_version_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            version_dir = root / '3.1.5'
            version_dir.mkdir()
            binary = version_dir / 'firmwareExe'
            binary.write_bytes(b'firmware')
            staging_dir = root / 'temp'
            staging_dir.mkdir()
            (staging_dir / 'firmwareExe').write_bytes(b'staging')

            self.assertEqual(m.find_firmware_binary(root), binary)

            another_dir = root / '5.1.9'
            another_dir.mkdir()
            (another_dir / 'firmwareExe').write_bytes(b'old')

            with self.assertRaises(RuntimeError):
                m.find_firmware_binary(root)

            binary.unlink()
            (another_dir / 'firmwareExe').unlink()
            with self.assertRaises(RuntimeError):
                m.find_firmware_binary(root)

    def test_version_identifier(self):
        self.assertEqual(m.firmware_version_from_path('/opt/PROGRAM/software/3.1.5/firmwareExe'), '3.1.5')
        with self.assertRaises(ValueError):
            m.firmware_version_from_path('/opt/PROGRAM/software/temp/firmwareExe')

    def test_qt_resource_rejects_invalid_binary(self):
        with self.assertRaises(ValueError):
            m.qt_config(b'not an ELF')

    def test_resource_and_migration_integration(self):
        # Optional on developer machines: no firmware binary is committed.
        import os
        firmware = os.environ.get('STOCK_FIRMWARE_TEST_BINARY')
        if not firmware:
            self.skipTest('Set STOCK_FIRMWARE_TEST_BINARY to test real Qt resources')
        binary = pathlib.Path(firmware).read_bytes()
        stock = m.qt_config(binary)
        self.assertIn(b'[mcu]', stock)
        with tempfile.TemporaryDirectory() as directory:
            version_name = os.environ.get('STOCK_FIRMWARE_TEST_VERSION', '5.1.9')
            version_dir = pathlib.Path(directory) / version_name
            version_dir.mkdir()
            binary_path = version_dir / 'firmwareExe'
            binary_path.symlink_to(pathlib.Path(firmware).resolve())
            cfg = pathlib.Path(directory) / 'printer.base.cfg'
            version = pathlib.Path(directory) / 'version'
            cfg.write_text('[servo my_servo]\ninitial_angle: 95\n')
            version.write_text('3.1.3\n')
            m.run(binary_path, cfg, version)
            self.assertEqual(version.read_text().strip(), version_name)
            after = cfg.read_bytes()
            m.run(binary_path, cfg, version)
            self.assertEqual(after, cfg.read_bytes())


if __name__ == '__main__':
    unittest.main()
