## Behavioral contracts for the calibrated touch device names.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).parents[1]


class TouchServiceTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        commands = self.root / 'bin'
        commands.mkdir()
        self.inputs = self.root / 'input'
        self.inputs.mkdir()
        self.device = self.inputs / 'event3'
        self.device.touch()
        sys_device = self.root / 'sys/event3/device'
        sys_device.mkdir(parents=True)
        (sys_device / 'name').write_text('ts_uinput\n')
        self.pid = self.root / 'run/ts_uinput.pid'
        self.events = self.root / 'events'
        for name, body in {
            'lsmod': 'echo uinput\n',
            'start-stop-daemon': '''echo "$1" >> "$TEST_EVENTS"
if [ "$1" = -S ]; then echo "$TEST_PID" > "$TEST_PIDFILE"; fi
''',
        }.items():
            command = commands / name
            command.write_text('#!/bin/sh\n' + body)
            command.chmod(0o755)
        source = (ROOT / '.root/S35tslib').read_text()
        for old, new in (('/dev/input/', str(self.inputs) + '/'),
                         ('/sys/class/input/', str(self.root / 'sys') + '/'),
                         ('/var/run/', str(self.root / 'run') + '/')):
            source = source.replace(old, new)
        self.script = self.root / 'S35tslib'
        self.script.write_text(source)
        self.environment = dict(os.environ, PATH=str(commands) + ':' + os.environ['PATH'],
                                TEST_EVENTS=str(self.events), TEST_PID=str(os.getpid()),
                                TEST_PIDFILE=str(self.pid))

    def run_service(self, action):
        result = subprocess.run(['sh', str(self.script), action], env=self.environment,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_primary_name_and_compatibility_alias_share_one_device_and_one_process(self):
        self.run_service('start')
        self.assertEqual((self.inputs / 'fgx_touch').resolve(), self.device.resolve())
        self.assertEqual((self.inputs / 'guppy').resolve(), self.device.resolve())
        self.run_service('start')
        self.assertEqual(self.events.read_text().splitlines(), ['-S'])

    def test_stop_removes_both_names(self):
        self.run_service('start')
        self.run_service('stop')
        for name in ('fgx_touch', 'guppy'):
            self.assertFalse((self.inputs / name).is_symlink())
        self.assertFalse(self.pid.exists())
