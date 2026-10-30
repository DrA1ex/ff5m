# Behavioral tests for Klipper's persistent Jinja2 template cache.
#
# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import configparser
import importlib.util
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / '.py/klipper/patches/extras/gcode_macro.py'
if not SOURCE.is_file():
    SOURCE = Path(__file__).resolve().with_name('gcode_macro.py')
SOURCE = Path(os.environ.get('GCODE_MACRO_TEST_SOURCE', str(SOURCE)))

spec = importlib.util.spec_from_file_location('cached_gcode_macro', SOURCE)
macro = importlib.util.module_from_spec(spec)
spec.loader.exec_module(macro)

CONFIG_SOURCE = REPO_ROOT / '.py/klipper/patches/configfile.py'
configfile = None
if CONFIG_SOURCE.is_file():
    spec = importlib.util.spec_from_file_location('cached_template_configfile', CONFIG_SOURCE)
    configfile = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(configfile)


class Config:
    def __init__(self, printer, script='', name='gcode_macro TEST'):
        self.printer = printer
        self.script = script
        self.name = name

    def get_printer(self):
        return self.printer

    def get_name(self):
        return self.name

    def get(self, option, default=None):
        return self.script


class Printer:
    config_error = RuntimeError

    def __init__(self, cache_dir):
        self.cache_dir = Path(cache_dir)
        self.temperature = 20
        self.gcode = SimpleNamespace(error=RuntimeError, register_command=lambda *args, **kwargs: None)
        # Use the actual on-disk cache, but keep tests independent of the
        # machine's configured cache location and filesystem permissions.
        with mock.patch.object(macro, 'template_cache',
                               return_value=macro.TemplateBytecodeCache(str(self.cache_dir))):
            self.macros = macro.PrinterGCodeMacro(Config(self))

    def get_start_args(self):
        return {'config_file': str(self.cache_dir.parent / 'printer.cfg')}

    def lookup_object(self, name, default=None):
        if name == 'gcode':
            return self.gcode
        if name == 'gcode_macro':
            return self.macros
        if name == 'extruder':
            return SimpleNamespace(get_status=lambda eventtime: {'temperature': self.temperature})
        return default

    def get_reactor(self):
        return SimpleNamespace(monotonic=lambda: 1.0)

    def template(self, script, name='gcode_macro TEST'):
        return self.macros.load_template(Config(self, script, name), 'gcode')


def read_config_template(root, cache_dir, name='TEST', forbid_compile=False):
    printer = Printer(cache_dir)
    reader = configfile.PrinterConfig(printer)
    with mock.patch.object(configparser.RawConfigParser, 'readfp',
                           lambda parser, stream, filename: parser.read_file(stream, source=filename), create=True):
        config = reader.read_main_config()
    section = config.getsection('gcode_macro ' + name)
    if forbid_compile:
        with mock.patch.object(printer.macros.env, 'compile', side_effect=AssertionError('cache miss')):
            return printer.macros.load_template(section, 'gcode')
    return printer.macros.load_template(section, 'gcode')


class TemplateCacheTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.cache_dir = Path(directory.name) / 'bytecode'
        self.cache_dir.mkdir()
        self.printer = Printer(self.cache_dir)
        self.logs = mock.patch.object(macro.logging, 'exception')
        self.logs.start()
        self.addCleanup(self.logs.stop)

    def test_reuses_compiled_template_after_restart(self):
        script = 'M117 {params.value}'
        self.assertEqual(self.printer.template(script).render({'params': {'value': 1}}), 'M117 1')
        restarted = Printer(self.cache_dir)
        with mock.patch.object(restarted.macros.env, 'compile', side_effect=AssertionError('cache miss')):
            self.assertEqual(restarted.template(script).render({'params': {'value': 2}}), 'M117 2')

    def test_source_change_invalidates_cache_without_accumulating_files(self):
        for script in ('M117 old', 'M117 new', 'M117 newer'):
            self.assertEqual(Printer(self.cache_dir).template(script).render(), script)
            self.assertEqual(len(list(self.cache_dir.iterdir())), 1)

        restarted = Printer(self.cache_dir)
        with mock.patch.object(restarted.macros.env, 'compile', side_effect=AssertionError('cache miss')):
            self.assertEqual(restarted.template('M117 newer').render(), 'M117 newer')

    def test_updating_one_macro_does_not_invalidate_another(self):
        self.printer.template('M117 original', 'gcode_macro FIRST')
        self.printer.template('M118 stable', 'gcode_macro SECOND')
        restarted = Printer(self.cache_dir)
        self.assertEqual(restarted.template('M117 updated', 'gcode_macro FIRST').render(), 'M117 updated')
        with mock.patch.object(restarted.macros.env, 'compile', side_effect=AssertionError('cache miss')):
            self.assertEqual(restarted.template('M118 stable', 'gcode_macro SECOND').render(), 'M118 stable')

    def test_cached_template_reads_current_printer_state(self):
        script = 'M117 {printer.extruder.temperature}'
        self.printer.template(script)
        restarted = Printer(self.cache_dir)
        template = restarted.template(script)
        self.assertEqual(template.render(), 'M117 20')
        restarted.temperature = 45
        self.assertEqual(template.render(), 'M117 45')

    def test_cached_template_uses_current_environment_globals(self):
        script = 'M117 {label}'
        self.printer.macros.env.globals['label'] = 'old'
        self.printer.template(script)
        restarted = Printer(self.cache_dir)
        restarted.macros.env.globals['label'] = 'current'
        with mock.patch.object(restarted.macros.env, 'compile', side_effect=AssertionError('cache miss')):
            self.assertEqual(restarted.template(script).render(), 'M117 current')

    def test_template_errors_are_not_hidden_by_cache(self):
        self.printer.template('M117 valid')
        with self.assertRaisesRegex(RuntimeError, 'Error loading template'):
            Printer(self.cache_dir).template('{% if %}')
        self.printer.template('{1 / params.divisor}')
        with self.assertRaisesRegex(RuntimeError, 'Error evaluating'):
            Printer(self.cache_dir).template('{1 / params.divisor}').render({'params': {'divisor': 0}})

    def test_cache_does_not_change_template_loader_policy(self):
        script = '{% include "another-macro" %}'
        self.printer.template(script)
        with self.assertRaisesRegex(RuntimeError, 'Error evaluating'):
            Printer(self.cache_dir).template(script).render()

    def test_truncated_cache_recompiles_and_repairs(self):
        script = 'M117 valid'
        self.printer.template(script)
        (next(self.cache_dir.iterdir())).write_bytes(b'broken bytecode')
        restarted = Printer(self.cache_dir)
        with mock.patch.object(restarted.macros.env, 'compile', wraps=restarted.macros.env.compile) as compile:
            self.assertEqual(restarted.template(script).render(), script)
            compile.assert_called_once_with(script)
        repaired = Printer(self.cache_dir)
        with mock.patch.object(repaired.macros.env, 'compile', side_effect=AssertionError('not repaired')):
            self.assertEqual(repaired.template(script).render(), script)

    def test_invalid_cached_code_recompiles_and_repairs(self):
        script = 'M117 valid'
        self.printer.template(script)
        cache = self.printer.macros.env.bytecode_cache
        bucket = cache.get_bucket(self.printer.macros.env, 'gcode_macro TEST:gcode', None, script)
        bucket.code = 42  # marshal can decode this, but Jinja cannot instantiate it.
        cache.set_bucket(bucket)
        restarted = Printer(self.cache_dir)
        with mock.patch.object(restarted.macros.env, 'compile', wraps=restarted.macros.env.compile) as compile:
            self.assertEqual(restarted.template(script).render(), script)
            compile.assert_called_once_with(script)
        repaired = Printer(self.cache_dir)
        with mock.patch.object(repaired.macros.env, 'compile', side_effect=AssertionError('not repaired')):
            self.assertEqual(repaired.template(script).render(), script)

    def test_failed_cache_deserialization_recompiles_template(self):
        script = 'M117 valid'
        restarted = Printer(self.cache_dir)
        cache = restarted.macros.env.bytecode_cache
        with mock.patch.object(cache, 'get_bucket', side_effect=ModuleNotFoundError('invalid cached pickle')):
            self.assertEqual(restarted.template(script).render(), script)

    def test_cache_read_and_write_failures_do_not_block_valid_templates(self):
        for operation in ('load_bytecode', 'dump_bytecode'):
            with self.subTest(operation=operation):
                script = 'M117 ' + operation
                printer = Printer(self.cache_dir)
                with mock.patch.object(macro.jinja2.FileSystemBytecodeCache, operation,
                                       side_effect=OSError(28, 'disk full')):
                    self.assertEqual(printer.template(script).render(), script)

    def test_unavailable_cache_directory_falls_back_to_compilation(self):
        with mock.patch.object(macro.os, 'makedirs', side_effect=PermissionError('read-only')):
            self.assertIsNone(macro.template_cache(self.printer))


@unittest.skipUnless(configfile is not None, 'Requires Klipper configfile.py from the repository')
class TemplateConfigReloadTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.cache_dir = self.root / 'bytecode'
        self.cache_dir.mkdir()
        (self.root / 'printer.cfg').write_text('[include user.cfg]\n')
        self.user = self.root / 'user.cfg'
        self.user.write_text('[gcode_macro TEST]\ngcode:\n  M117 old {params.value}\n'
                             '[gcode_macro STABLE]\ngcode: M118 stable\n')

    def edit_preserving_size_and_mtime(self):
        before = self.user.read_text()
        info = self.user.stat()
        self.user.write_text(before.replace('old', 'new'))
        os.utime(self.user, ns=(info.st_atime_ns, info.st_mtime_ns))

    def test_included_macro_changed_without_metadata_change(self):
        first = read_config_template(self.root, self.cache_dir)
        self.assertEqual(first.render({'params': {'value': 10}}), '\nM117 old 10')
        self.edit_preserving_size_and_mtime()
        changed = read_config_template(self.root, self.cache_dir)
        self.assertEqual(changed.render({'params': {'value': 20}}), '\nM117 new 20')
        cached = read_config_template(self.root, self.cache_dir, forbid_compile=True)
        self.assertEqual(cached.render({'params': {'value': 30}}), '\nM117 new 30')

    def test_unchanged_included_macro_reuses_cache(self):
        read_config_template(self.root, self.cache_dir)
        cached = read_config_template(self.root, self.cache_dir, forbid_compile=True)
        self.assertEqual(cached.render({'params': {'value': 40}}), '\nM117 old 40')

    def test_edit_does_not_invalidate_other_included_macro(self):
        read_config_template(self.root, self.cache_dir)
        read_config_template(self.root, self.cache_dir, name='STABLE')
        self.edit_preserving_size_and_mtime()
        stable = read_config_template(self.root, self.cache_dir, name='STABLE', forbid_compile=True)
        self.assertEqual(stable.render(), 'M118 stable')

    def test_loaded_template_remains_unchanged_until_config_reload(self):
        running = read_config_template(self.root, self.cache_dir)
        self.edit_preserving_size_and_mtime()
        self.assertEqual(running.render({'params': {'value': 10}}), '\nM117 old 10')
        self.assertEqual(read_config_template(self.root, self.cache_dir).render({'params': {'value': 20}}),
                         '\nM117 new 20')


if __name__ == '__main__':
    unittest.main()
