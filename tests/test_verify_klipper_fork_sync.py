#!/usr/bin/env python3
"""Integration tests using a synthetic local Git fork (no network required)."""
import contextlib
import hashlib
import importlib.util
import io
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    'klipper_fork_sync', pathlib.Path(__file__).with_name('verify_klipper_fork.py'))
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo)] + list(args)).decode().strip()


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name) / 'mod'
        self.repo = pathlib.Path(self.tmp.name) / 'fork'
        self.repo.mkdir()
        git(self.repo, 'init', '-q')
        git(self.repo, 'config', 'user.email', 'test@example.com')
        git(self.repo, 'config', 'user.name', 'Test')
        self.fork_file('klippy/extras/a.py', b'A = 1\n')
        self.fork_file('klippy/extras/stable.py', b'STABLE = True\n')
        self.fork_file('klippy/extras/removed.py', b'OLD = 1\n')
        self.fork_file('klippy/mcu.py', b'MCU = 1\n')
        self.fork_file('klippy/extras/untouched_stock.py', b'STOCK = 1\n')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-q', '-m', 'old')
        self.old = git(self.repo, 'rev-parse', 'HEAD')
        self.fork_file('klippy/extras/a.py', b'A = 2\n')
        self.fork_file('klippy/extras/new.py', b'NEW = 1\n')
        self.fork_file('klippy/mcu.py', b'MCU = 2\n')
        (self.repo / 'klippy/extras/removed.py').unlink()
        self.fork_file('README.md', b'Unrelated file.\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'new')
        self.new = git(self.repo, 'rev-parse', 'HEAD')
        self.patches = self.root / '.py/klipper/patches'
        self.manifest = self.root / 'docs/klipper-ad5m-manifest.json'
        self.patch('extras/a.py', b'A = 1\n')
        self.patch('extras/stable.py', b'STABLE = True\n')
        self.patch('extras/removed.py', b'OLD = 1\n')
        # A binary supplied by the stock firmware, not present in the fork.
        self.patch('chelper/c_helper.so', b'\x7fELF\x00\x01')
        self.original = self.manifest_content(self.old)
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        self.manifest.write_text(json.dumps(self.original, indent=2) + '\n')
        self.patches_backup = sync.PATCHES
        self.manifest_backup = sync.MANIFEST
        self.root_backup = sync.ROOT
        sync.ROOT, sync.PATCHES, sync.MANIFEST = self.root, self.patches, self.manifest
        self.addCleanup(self.restore_globals)

    def restore_globals(self):
        sync.ROOT, sync.PATCHES, sync.MANIFEST = (
            self.root_backup, self.patches_backup, self.manifest_backup)

    def fork_file(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def patch(self, name, content):
        path = self.patches / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def manifest_content(self, revision):
        sources = {}
        binaries = {}
        for name, content in (
            ('klippy/extras/a.py', b'A = 1\n'),
            ('klippy/extras/stable.py', b'STABLE = True\n'),
            ('klippy/extras/removed.py', b'OLD = 1\n'),
            ('klippy/chelper/c_helper.so', b'\x7fELF\x00\x01')
        ):
            collection = binaries if name.endswith('.so') else sources
            collection[name] = sync.manifest_entry(name, hashlib.sha256(content).hexdigest())
        return dict(fork=str(self.repo), commit=revision, sources=sources,
                    binaries=binaries, base_commit='base', stock_commit='stock')

    def run_sync(self, commit=None, extra=None):
        args = ['--update', '--fork', str(self.repo), '--commit', commit or self.new]
        args += (extra if extra is not None else ['--yes'])
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = sync.main(args)
        return code, out.getvalue(), err.getvalue()

    def test_update_downloads_changed_files_removes_deleted_and_adds_new(self):
        code, output, err = self.run_sync()
        self.assertEqual(code, 0, err)
        self.assertEqual((self.patches / 'extras/a.py').read_bytes(), b'A = 2\n')
        self.assertFalse((self.patches / 'extras/removed.py').exists())
        self.assertEqual((self.patches / 'extras/new.py').read_bytes(), b'NEW = 1\n')
        self.assertEqual((self.patches / 'mcu.py').read_bytes(), b'MCU = 2\n')
        self.assertEqual((self.patches / 'extras/stable.py').read_bytes(), b'STABLE = True\n')
        self.assertEqual((self.patches / 'chelper/c_helper.so').read_bytes(), b'\x7fELF\x00\x01')
        self.assertEqual(sync.load_manifest()['commit'], self.new)
        self.assertIn('UPDATE', output)
        self.assertEqual(sync.check_manifest(sync.load_manifest()), [])
        self.assertEqual(sync.check_fork(sync.load_manifest(), str(self.repo)), [])

    def test_same_commit_repairs_locally_modified_file(self):
        code, _, err = self.run_sync()
        self.assertEqual(code, 0, err)
        self.patch('extras/a.py', b'A = 999\n')
        self.assertTrue(sync.check_manifest(sync.load_manifest()))
        code, output, err = self.run_sync(self.new)
        self.assertEqual(code, 0, err)
        self.assertIn('LOCAL EDITS', output)
        self.assertEqual((self.patches / 'extras/a.py').read_bytes(), b'A = 2\n')
        self.assertEqual(sync.check_manifest(sync.load_manifest()), [])

    def test_same_commit_restores_missing_tracked_file(self):
        self.run_sync()
        (self.patches / 'extras/a.py').unlink()
        code, _, err = self.run_sync(self.new)
        self.assertEqual(code, 0, err)
        self.assertEqual((self.patches / 'extras/a.py').read_bytes(), b'A = 2\n')

    def test_dry_run_does_not_modify_anything(self):
        before = self.manifest.read_bytes()
        code, out, err = self.run_sync(extra=['--dry-run'])
        self.assertEqual(code, 0, err)
        self.assertIn('Dry run', out)
        self.assertEqual(self.manifest.read_bytes(), before)
        self.assertEqual((self.patches / 'extras/a.py').read_bytes(), b'A = 1\n')
        self.assertFalse((self.patches / 'extras/new.py').exists())

    def test_noninteractive_does_not_overwrite(self):
        before = self.manifest.read_bytes()
        with mock.patch.object(sync.sys.stdin, 'isatty', return_value=False):
            code, _out, err = self.run_sync(extra=[])
        self.assertEqual(code, 1)
        self.assertIn('Not an interactive terminal', err)
        self.assertEqual(self.manifest.read_bytes(), before)
        self.assertEqual((self.patches / 'extras/a.py').read_bytes(), b'A = 1\n')

    def test_interactive_decline_preserves_files(self):
        with mock.patch.object(sync.sys.stdin, 'isatty', return_value=True), \
             mock.patch('builtins.input', return_value='no'):
            code, out, err = self.run_sync(extra=[])
        self.assertEqual(code, 1)
        self.assertIn('Synchronization cancelled', out)
        self.assertEqual((self.patches / 'extras/a.py').read_bytes(), b'A = 1\n')

    def test_interactive_yes_applies_changes(self):
        with mock.patch.object(sync.sys.stdin, 'isatty', return_value=True), \
             mock.patch('builtins.input', return_value='yes'):
            code, out, err = self.run_sync(extra=[])
        self.assertEqual(code, 0, err)
        self.assertEqual((self.patches / 'extras/a.py').read_bytes(), b'A = 2\n')

    def test_invalid_target_does_not_change_overlay_or_manifest(self):
        before = self.manifest.read_bytes()
        code, out, err = self.run_sync(commit='deadbeef0000')
        self.assertEqual(code, 1)
        self.assertIn('ERROR:', err)
        self.assertEqual(self.manifest.read_bytes(), before)
        self.assertEqual((self.patches / 'extras/a.py').read_bytes(), b'A = 1\n')

    def test_malicious_manifest_path_is_rejected(self):
        m = sync.load_manifest()
        m['sources']['klippy/../../secret.py'] = dict(ff5m='fake', sha256='0' * 64)
        self.manifest.write_text(json.dumps(m))
        code, out, err = self.run_sync()
        self.assertEqual(code, 1)
        self.assertIn('Unsafe', err)

    def test_local_only_python_file_is_not_deleted(self):
        self.patch('extras/local.py', b'LOCAL = True\n')
        code, out, err = self.run_sync()
        self.assertEqual(code, 1)
        self.assertIn('Local-only source', err)
        self.assertEqual((self.patches / 'extras/local.py').read_bytes(), b'LOCAL = True\n')

    def test_check_does_not_write_and_reports_fork_mismatch(self):
        m = sync.load_manifest()
        self.patch('extras/a.py', b'LOCAL = 3\n')
        self.assertTrue(sync.check_manifest(m))
        self.assertEqual(sync.check_fork(m, str(self.repo)), [])
        self.assertEqual(self.manifest.read_text(), json.dumps(self.original, indent=2) + '\n')

    def test_manifest_not_advanced_when_write_fails(self):
        original_write = sync.atomic_write
        count = [0]
        def flaky_write(path, data):
            count[0] += 1
            if count[0] == 2:
                raise OSError('disk failed')
            return original_write(path, data)
        with mock.patch.object(sync, 'atomic_write', side_effect=flaky_write):
            code, out, err = self.run_sync()
        self.assertEqual(code, 1)
        self.assertIn('disk failed', err)
        self.assertEqual((self.patches / 'extras/a.py').read_bytes(), b'A = 1\n')
        self.assertEqual(self.manifest.read_text(), json.dumps(self.original, indent=2) + '\n')

    def test_local_file_existing_in_fork_but_absent_from_manifest_is_repaired(self):
        self.patch('extras/new.py', b'LOCAL NEW = 42\n')
        code, output, err = self.run_sync()
        self.assertEqual(code, 0, err)
        self.assertIn('LOCAL EDITS', output)
        self.assertEqual((self.patches / 'extras/new.py').read_bytes(), b'NEW = 1\n')
        self.assertIn('klippy/extras/new.py', sync.load_manifest()['sources'])

    def test_manifest_check_is_offline_and_read_only(self):
        before = self.manifest.read_bytes()
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = sync.main([])
        self.assertEqual(code, 0, errors.getvalue())
        self.assertIn('OK:', output.getvalue())
        self.assertEqual(self.manifest.read_bytes(), before)

    def test_symlink_target_outside_overlay_is_rejected(self):
        external = pathlib.Path(self.tmp.name) / 'outside.py'
        external.write_bytes(b'EXTERNAL = True\n')
        (self.patches / 'extras/a.py').unlink()
        (self.patches / 'extras/a.py').symlink_to(external)
        code, output, errors = self.run_sync()
        self.assertEqual(code, 1)
        self.assertIn('outside patches', errors)
        self.assertEqual(external.read_bytes(), b'EXTERNAL = True\n')

    def test_reverification_after_sync(self):
        self.run_sync()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = sync.main(['--fork', str(self.repo)])
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn('OK:', out.getvalue())

    def test_manifest_write_failure_restores_all_overlay_files(self):
        original_write = sync.atomic_write
        def flaky_write(path, data):
            if path == self.manifest:
                raise OSError('manifest write failed')
            return original_write(path, data)
        with mock.patch.object(sync, 'atomic_write', side_effect=flaky_write):
            code, out, err = self.run_sync()
        self.assertEqual(code, 1)
        self.assertIn('manifest write failed', err)
        self.assertEqual((self.patches / 'extras/a.py').read_bytes(), b'A = 1\n')
        self.assertTrue((self.patches / 'extras/removed.py').exists())
        self.assertFalse((self.patches / 'extras/new.py').exists())
        self.assertEqual(self.manifest.read_text(), json.dumps(self.original, indent=2) + '\n')

    def test_dirty_deleted_file_requires_confirmation(self):
        self.patch('extras/removed.py', b'UNSAVED = 42\n')
        with mock.patch.object(sync.sys.stdin, 'isatty', return_value=False):
            code, output, err = self.run_sync(extra=[])
        self.assertEqual(code, 1)
        self.assertIn('DELETE  ', output)
        self.assertIn('LOCAL EDITS', output)
        self.assertEqual((self.patches / 'extras/removed.py').read_bytes(), b'UNSAVED = 42\n')

    def test_short_commit_ref_is_rejected_clearly(self):
        code, out, err = self.run_sync(commit=self.new[:12])
        self.assertEqual(code, 1)
        self.assertIn('full 40-character', err)
        self.assertEqual(self.manifest.read_text(), json.dumps(self.original, indent=2) + '\n')

    def test_sync_without_commit_repairs_current_manifest_revision(self):
        code, _, err = self.run_sync()
        self.assertEqual(code, 0, err)
        self.patch('extras/a.py', b'UNSAVED = 100\n')
        out, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(errors):
            code = sync.main(['--sync', '--fork', str(self.repo), '--yes'])
        self.assertEqual(code, 0, errors.getvalue())
        self.assertEqual((self.patches / 'extras/a.py').read_bytes(), b'A = 2\n')

    def test_second_sync_is_noop(self):
        self.run_sync()
        code, out, err = self.run_sync(extra=[])
        self.assertEqual(code, 0, err)
        self.assertIn('Already synchronized', out)

    def test_imports_additions_from_commit_diff_not_entire_repository(self):
        self.run_sync()
        self.assertFalse((self.patches / 'klippy.py').exists())
        self.assertFalse((self.patches / 'README.md').exists())
        self.assertFalse((self.patches / 'extras/untouched_stock.py').exists())


if __name__ == '__main__':
    unittest.main()
