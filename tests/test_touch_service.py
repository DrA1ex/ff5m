## Behavioral contracts for the calibrated touch device names.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).parents[1]


class TouchServiceTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.commands = self.root / 'bin'
        self.commands.mkdir()
        self.inputs = self.root / 'input'
        self.inputs.mkdir()
        self.device = self.inputs / 'event3'
        self.device.touch()
        sys_device = self.root / 'sys/event3/device'
        sys_device.mkdir(parents=True)
        (sys_device / 'name').write_text('ts_uinput\n')
        self.pid = self.root / 'run/ts_uinput.pid'
        # The chroot's /run is a bind mount that already exists before the service runs.
        self.pid.parent.mkdir()
        self.events = self.root / 'events'
        for name, body in {
            'lsmod': 'echo uinput\n',
            'start-stop-daemon': '''while [ "$#" -gt 0 ]; do
    case "$1" in
        -S) action=start ;;
        -K) action=stop ;;
        -p) pid_file=$2; shift ;;
    esac
    shift
done
[ -n "$action" ] && [ -n "$pid_file" ] || exit 2
echo "$action" >> "$TEST_EVENTS"
case "$action" in
    start)
        [ "${TEST_START_FAILURE:-0}" = 0 ] || exit "$TEST_START_FAILURE"
        echo "$TEST_PID" > "$pid_file"
        ;;
    stop) if [ "$TEST_STOP_SIGNALS" = 1 ]; then kill -TERM "$(cat "$pid_file")"; fi ;;
esac
''',
        }.items():
            self.command(name, body)
        source = (ROOT / '.root/S35tslib').read_text()
        for old, new in (('/dev/input/', str(self.inputs) + '/'),
                         ('/sys/class/input/', str(self.root / 'sys') + '/'),
                         ('/run/', str(self.root / 'run') + '/')):
            source = source.replace(old, new)
        self.script = self.root / 'S35tslib'
        self.script.write_text(source)
        self.script.chmod(0o755)
        self.environment = dict(os.environ, PATH=str(self.commands) + ':' + os.environ['PATH'],
                                TEST_EVENTS=str(self.events), TEST_PID=str(os.getpid()))

    def command(self, name, body):
        command = self.commands / name
        command.write_text('#!/bin/sh\n' + body)
        command.chmod(0o755)

    def run_service(self, action, expected=0):
        result = subprocess.run([str(self.script), action], env=self.environment,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)

    def test_primary_name_and_compatibility_alias_share_one_device_and_one_process(self):
        self.run_service('start')
        self.assertEqual((self.inputs / 'fgx_touch').resolve(), self.device.resolve())
        self.assertEqual((self.inputs / 'guppy').resolve(), self.device.resolve())
        self.run_service('start')
        self.assertEqual(self.events.read_text().splitlines(), ['start'])

    def test_start_reports_a_daemon_launch_failure(self):
        self.environment['TEST_START_FAILURE'] = '1'
        self.run_service('start', expected=1)
        self.assertFalse(self.pid.exists())
        self.assertFalse((self.inputs / 'fgx_touch').is_symlink())

    def test_start_fails_without_a_device_and_a_dead_daemon_does_not_block_the_next_start(self):
        self.command('sleep', 'exit 0\n')
        name = self.root / 'sys/event3/device/name'
        name.write_text('Other input\n')
        with subprocess.Popen(['/bin/sh', '-c', 'exit 0']) as daemon:
            daemon.wait(timeout=5)
            self.environment['TEST_PID'] = str(daemon.pid)
        self.run_service('start', expected=1)
        self.assertFalse((self.inputs / 'fgx_touch').is_symlink())

        name.write_text('ts_uinput\n')
        self.environment['TEST_PID'] = str(os.getpid())
        self.run_service('start')
        self.assertEqual(self.events.read_text().splitlines(), ['start', 'start'])
        self.assertEqual((self.inputs / 'fgx_touch').resolve(), self.device.resolve())

    def test_stop_waits_for_the_process_to_release_the_device_before_cleanup(self):
        daemon = subprocess.Popen(
            [sys.executable, '-c', '''import signal, sys, time
from pathlib import Path

def release(*_):
    time.sleep(0.2)
    pid, touch, guppy = map(Path, sys.argv[1:])
    sys.exit(0 if pid.exists() and touch.is_symlink() and guppy.is_symlink() else 1)

signal.signal(signal.SIGTERM, release)
print('ready', flush=True)
signal.pause()
''', str(self.pid), str(self.inputs / 'fgx_touch'), str(self.inputs / 'guppy')],
            stdout=subprocess.PIPE, text=True,
        )
        self.addCleanup(daemon.stdout.close)
        self.addCleanup(daemon.wait, timeout=5)
        self.addCleanup(daemon.kill)
        self.assertTrue(select.select([daemon.stdout], [], [], 5)[0], 'test daemon did not become ready')
        self.assertEqual(daemon.stdout.readline().strip(), 'ready')
        reaper = threading.Thread(target=daemon.wait, daemon=True)
        reaper.start()
        self.environment.update(TEST_PID=str(daemon.pid), TEST_STOP_SIGNALS='1')

        self.run_service('start')
        self.run_service('stop')
        self.assertEqual(daemon.poll(), 0)
        reaper.join(timeout=5)
        for name in ('fgx_touch', 'guppy'):
            self.assertFalse((self.inputs / name).is_symlink())
        self.assertFalse(self.pid.exists())

    def test_failed_restart_keeps_the_pid_and_device_names_for_a_retry(self):
        self.command('sleep', 'exit 0\n')
        self.run_service('start')
        self.run_service('restart', expected=1)
        self.assertEqual(self.events.read_text().splitlines(), ['start', 'stop'])
        self.assertEqual(self.pid.read_text().strip(), str(os.getpid()))
        for name in ('fgx_touch', 'guppy'):
            self.assertEqual((self.inputs / name).resolve(), self.device.resolve())
