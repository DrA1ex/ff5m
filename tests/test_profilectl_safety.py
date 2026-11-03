# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
# This file may be distributed under the terms of the GNU GPLv3 license.

import fcntl
from pathlib import Path
import tempfile
import unittest

from tests.profilectl_support import Fixture, meta


def simple_uboot(script, failed=None):
    """Run a boot script as the AD5M U-Boot does: it has no hush parser, so every ';'
    command runs even after a failure and if/then/fi are unknown commands. DRAM keeps
    the previous boot's images; bootm boots only valid images at both addresses."""
    memory = {'0x43000000': 'stale', '0x44864e70': 'stale'}
    log = []
    for command in script.split(';'):
        words = command.split()
        if not words:
            continue
        name = words[0]
        if name == 'mw.l':
            memory[words[1]] = 'cleared'
        elif name == 'part':
            log.append('part')
        elif name == 'fatload':
            log.append(words[4])
            if failed != words[4]:
                memory[words[3]] = 'loaded'
        elif name == 'bootm':
            log.append('bootm')
            if failed != 'bootm' and memory[words[1]] != 'cleared' and memory[words[3]] != 'cleared':
                return log, 'booted ' + '+'.join(memory[address] for address in (words[1], words[3]))
        elif name == 'reset':
            log.append('reset')
            return log, 'reset'
        else:
            log.append('unknown ' + name)
    return log, 'prompt'


class SafetyRegressions(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="t113-safety-")
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.fixture = Fixture(directory.name, "printer")

    def test_journal_read_failure_does_not_pass_candidate(self):
        fixture = self.fixture
        fixture.run('--apply', 'test', 'cpu', '1.2')
        fixture.simulate_candidate_boot()
        failed_reader = self.directory / 'dmesg'
        failed_reader.write_text('#!/bin/sh\nprintf "mmc0: timeout error\\n"\nexit 1\n')
        failed_reader.chmod(0o755)
        result = fixture.run('--apply', 'verify', ok=False, extra={
            'T113_DMESG_FILE': '', 'T113_DMESG_CMD': str(failed_reader),
        })
        self.assertIn('cannot read kernel journal', result.stdout)
        self.assertEqual(meta(fixture.state / 'candidate')['phase'], 'armed')
        fixture.run('--apply', 'verify')

    def test_explicit_unreadable_journal_does_not_fall_back(self):
        fixture = self.fixture
        fixture.run('--apply', 'test', 'cpu', '1.2')
        fixture.simulate_candidate_boot()
        for path in (self.directory / 'missing', self.directory):
            with self.subTest(path=path.name):
                fixture.run('--apply', 'verify', ok=False, extra={'T113_DMESG_FILE': str(path)})
                self.assertEqual(meta(fixture.state / 'candidate')['phase'], 'armed')


    def test_kernel_lock_blocks_mutation_without_a_pid_file(self):
        fixture = self.fixture
        fixture.lock.mkdir()
        owner = fixture.lock / 'owner'
        original_env = fixture.envdb.read_bytes()
        with owner.open('a') as locked:
            fcntl.flock(locked, fcntl.LOCK_EX | fcntl.LOCK_NB)
            identity = owner.stat().st_ino
            result = fixture.run('--apply', 'backup', ok=False)
            self.assertIn('another mutation is active', result.stdout)
            self.assertEqual(owner.stat().st_ino, identity)
            self.assertEqual(fixture.envdb.read_bytes(), original_env)
            self.assertFalse((fixture.state / 'original').exists())
        fixture.run('--apply', 'backup')


    def test_boot_script_never_boots_after_a_failed_load(self):
        fixture = self.fixture
        fixture.run('--apply', 'test', 'cpu', '1.2')
        script = fixture.env()['fgx_boot_test']
        slot = meta(fixture.state / 'candidate')['slot']
        loads = ['part', 'uImage', slot, 'bootm']
        for failed, expected in [
            ('uImage', (loads + ['reset'], 'reset')),
            (slot, (loads + ['reset'], 'reset')),
            ('bootm', (loads + ['reset'], 'reset')),
            (None, (loads, 'booted loaded+loaded')),
        ]:
            with self.subTest(failed=failed):
                self.assertEqual(simple_uboot(script, failed), expected)

    def test_candidate_armed_with_the_earlier_if_script_can_be_cancelled(self):
        fixture = self.fixture
        fixture.run('--apply', 'test', 'cpu', '1.2')
        slot = meta(fixture.state / 'candidate')['slot']
        fixture.setenv('fgx_boot_test', 'if part list mmc 0 && fatload mmc 0:1 0x43000000 uImage && '
                       f'fatload mmc 0:1 0x44864e70 {slot}; then bootm 0x43000000 - 0x44864e70; fi; reset')
        fixture.run('--apply', 'cancel')
        self.assertIn('boot state: clean-stock', fixture.run('status').stdout)

    def test_legacy_candidate_can_be_enabled_and_rolled_back(self):
        fixture = self.fixture
        fixture.run('--apply', 'test', 'cpu', '1.2')
        slot = meta(fixture.state / 'candidate')['slot']
        legacy = (
            'part list mmc 0;fatload mmc 0:1 0x43000000 uImage;'
            f'fatload mmc 0:1 0x44864e70 {slot};bootm 0x43000000 - 0x44864e70'
        )
        fixture.setenv('fgx_boot_test', legacy)
        fixture.simulate_candidate_boot()
        fixture.run('--apply', 'verify')
        fixture.run('--apply', 'enable')
        current = fixture.env()['fgx_boot_current']
        self.assertEqual(simple_uboot(current, slot)[1], 'reset')
        self.assertEqual(simple_uboot(current)[1], 'booted loaded+loaded')
        fixture.setenv('fgx_boot_current', legacy)
        self.assertIn('boot state: clean-managed', fixture.run('status').stdout)
        fixture.run('--apply', 'rollback')
        self.assertIn('boot state: clean-stock', fixture.run('status').stdout)



if __name__ == '__main__':
    unittest.main(verbosity=0, buffer=True)
