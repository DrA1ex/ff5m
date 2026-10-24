## Behavioral tests for the Moonraker service lifecycle.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license.

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).parents[1]


class MoonrakerServiceTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.pidfile = self.root / 'moonraker.pid'
        self.running = self.root / 'running'
        self.events = self.root / 'events'
        commands = self.root / 'bin'
        commands.mkdir()
        scripts = {
            'zconf': '#!/bin/sh\nprintf "0\\n"\n',
            'python3': '#!/bin/sh\nexit 0\n',
            'sleep': '#!/bin/sh\nexit 0\n',
            'start-stop-daemon': '''#!/bin/sh
case " $* " in
    *" -t "*) test -f "$TEST_RUNNING" ;;
    *" -S "*)
        echo start >> "$TEST_EVENTS"
        touch "$TEST_RUNNING"
        echo 123 > "$TEST_PIDFILE"
        ;;
    *" -K "*)
        echo stop >> "$TEST_EVENTS"
        rm -f "$TEST_RUNNING"
        ;;
    *) exit 2 ;;
esac
''',
        }
        for name, text in scripts.items():
            path = commands / name
            path.write_text(text)
            path.chmod(0o755)
        source = (ROOT / '.root/S65moonraker').read_text()
        for original, replacement in (
                ('/root/printer_data', str(self.root / 'printer_data')),
                ('/run/moonraker.pid', str(self.pidfile)),
                ('/opt/config/mod/.shell/commands/zconf.sh', str(commands / 'zconf')),
                ('/bin/python3 ', str(commands / 'python3') + ' ')):
            source = source.replace(original, replacement)
        self.script = self.root / 'S65moonraker'
        self.script.write_text(source)
        self.environment = dict(os.environ, TEST_RUNNING=str(self.running),
                                TEST_EVENTS=str(self.events), TEST_PIDFILE=str(self.pidfile),
                                PATH=str(commands) + os.pathsep + os.environ['PATH'])

    def run_service(self, action):
        result = subprocess.run(['sh', str(self.script), action], env=self.environment,
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return self.events.read_text().splitlines() if self.events.exists() else []

    def test_try_restart_does_not_start_a_stopped_service(self):
        self.assertEqual(self.run_service('try-restart'), [])
        self.assertFalse(self.running.exists())
        self.assertFalse((self.root / 'printer_data').exists())

    def test_try_restart_ignores_a_stale_pidfile(self):
        self.pidfile.write_text('123\n')
        self.assertEqual(self.run_service('try-restart'), [])
        self.assertFalse(self.running.exists())

    def test_try_restart_restarts_a_running_service(self):
        self.pidfile.write_text('123\n')
        self.running.touch()
        self.assertEqual(self.run_service('try-restart'), ['stop', 'start'])
        self.assertTrue(self.running.exists())

    def test_explicit_restart_still_starts_a_stopped_service(self):
        self.assertEqual(self.run_service('restart'), ['start'])
        self.assertTrue(self.running.exists())
