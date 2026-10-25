## Behavioral checks for native Klipper helper synchronization.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license.

import io
import mmap
import os
import pathlib
import shutil
import subprocess
import tarfile
import tempfile
import unittest


ROOT = pathlib.Path(__file__).parents[1]
HELPER = pathlib.Path('.py/klipper/patches/chelper/c_helper.so')


class KlipperSyncTest(unittest.TestCase):
    def test_helper_is_packaged_for_full_and_lightweight_sync_profiles(self):
        for profile in ('all', 'klipper', 'light'):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as tmp:
                directory = pathlib.Path(tmp)
                worktree = directory / 'worktree'
                worktree.mkdir()
                shutil.copy2(ROOT / 'sync.sh', worktree / 'sync.sh')
                (worktree / 'sync_remote.sh').touch()
                helper = worktree / HELPER
                helper.parent.mkdir(parents=True)
                shutil.copy2(ROOT / HELPER, helper)
                subprocess.run(['git', 'init', '-q', str(worktree)], check=True)
                fake_bin = directory / 'bin'
                fake_bin.mkdir()
                for name, body in (
                        ('scp', 'cp "$2" "$CAPTURE_ARCHIVE"'),
                        ('ssh', 'printf "%s\\n" "$@" > "$CAPTURE_ARGUMENTS"')):
                    executable = fake_bin / name
                    executable.write_text('#!/bin/sh\n' + body + '\n')
                    executable.chmod(0o755)
                archive = directory / 'archive.tar.gz'
                arguments = directory / 'arguments'
                env = dict(os.environ, PATH=str(fake_bin) + ':' + os.environ['PATH'],
                           CAPTURE_ARCHIVE=str(archive), CAPTURE_ARGUMENTS=str(arguments))
                result = subprocess.run(
                    ['bash', 'sync.sh', '--host', 'test.invalid', '-p', profile],
                    cwd=worktree, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                with tarfile.open(archive) as packaged:
                    self.assertEqual(packaged.extractfile('./' + str(HELPER)).read(),
                                     (ROOT / HELPER).read_bytes())
                if profile == 'klipper':
                    # SSH arguments: destination, command, then the remote flags.
                    flags = arguments.read_text().splitlines()[2:]
                    self.assertEqual(flags[4:6], ['0', '1'])

    def run_remote(self, directory, failure=None):
        source = (ROOT / 'sync_remote.sh').read_text()
        for original, replacement in (
                ('/opt/config', str(directory)),
                ('/data/.mod/.forge-x/tmp/mod_skip_reboot', str(directory / 'skip-reboot')),
                ('/tmp/version_patch', str(directory / 'version-patch')),
                ('/etc/motd', str(directory / 'motd'))):
            source = source.replace(original, replacement)
        script = directory / 'sync_remote.sh'
        script.write_text(source)
        payload = b'updated shared object'
        with tarfile.open(directory / 'sync_test.tar.gz', 'w:gz') as archive:
            entry = tarfile.TarInfo('./' + str(HELPER))
            entry.size = len(payload)
            entry.mode = 0o644
            archive.addfile(entry, io.BytesIO(payload))
        motd = directory / 'mod/.shell/motd.sh'
        motd.parent.mkdir(parents=True, exist_ok=True)
        motd.write_text('#!/bin/sh\nexit 0\n')
        motd.chmod(0o755)
        # Only effectors are substituted; archive extraction, comparison and
        # replacement run through the actual remote script against this tree.
        wrapper = r'''
            sync() { :; }
            cp() {
                if [ "$FAILURE" = copy ] && [[ "$2" = *.so ]]; then
                    printf partial > "$3"
                    return 1
                fi
                command cp "$@"
            }
            mv() {
                if [ "$FAILURE" = rename ] && [[ "$3" = *.so ]]; then return 1; fi
                command mv "$@"
            }
            export -f sync cp mv
            exec bash "$@"
        '''
        result = subprocess.run(
            ['bash', '-c', wrapper, 'sync-test', str(script),
             '1', '1', '1', '1', '1', '1', str(directory), 'sync_test.tar.gz', '0', '0'],
            env=dict(os.environ, FAILURE=failure or ''), capture_output=True, text=True)
        return result, payload

    def test_replacement_preserves_loaded_inode_and_repeat_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = pathlib.Path(tmp)
            target = directory / 'mod' / HELPER
            target.parent.mkdir(parents=True)
            target.write_bytes(b'original shared object')
            with target.open('rb') as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as loaded:
                result, payload = self.run_remote(directory)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(loaded[:], b'original shared object')
                self.assertEqual(target.read_bytes(), payload)
                self.assertNotEqual(os.fstat(handle.fileno()).st_ino, target.stat().st_ino)
            inode = target.stat().st_ino
            result, _ = self.run_remote(directory)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(target.stat().st_ino, inode)

    def test_copy_or_rename_failure_preserves_old_helper_and_removes_partial_file(self):
        for failure in ('copy', 'rename'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                directory = pathlib.Path(tmp)
                target = directory / 'mod' / HELPER
                target.parent.mkdir(parents=True)
                target.write_bytes(b'original shared object')
                inode = target.stat().st_ino
                result, _ = self.run_remote(directory, failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(target.read_bytes(), b'original shared object')
                self.assertEqual(target.stat().st_ino, inode)
                self.assertEqual(list(target.parent.glob('*.sync.*')), [])


if __name__ == '__main__':
    unittest.main()
