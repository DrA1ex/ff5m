## Behavioral tests for the camera and framebuffer services.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).parents[1]


class StreamServicesTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.events = self.root / 'events'
        self.camera_pid = self.root / 'camera.pid'
        self.screen_pid = self.root / 'screen_stream.pid'
        self.variables = self.root / 'variables.cfg'
        self.variables.write_text('[Variables]\n')
        self.config = self.root / 'camera.conf'
        self.config.write_text('VIDEO=auto\nWIDTH=640\nHEIGHT=480\nFPS=15\nREDUCE_MEMORY=0\n')
        self.boot = self.root / 'boot_mode.sh'
        self.boot.write_text('forge_x_boot_mode() { echo "${TEST_BOOT_MODE:-mod}"; }\n')
        self.common = self.root / 'common.sh'
        self.common.write_text(f'''source {ROOT}/.shell/common.sh
BINS={self.root}
MOD={self.root}/chroot
PY={ROOT}/.py
SCRIPTS={ROOT}/.shell
CMDS={ROOT}/.shell/commands
CFG_SCRIPT={ROOT}/.shell/commands/zconf.sh
VAR_PATH={self.variables}
CAMERA_PID_F={self.camera_pid}
''')
        sleep = self.root / 'sleep'
        sleep.write_text('#!/bin/sh\n[ -n "$TEST_REAL_SLEEP" ] && exec /bin/sleep "$@"\nexit 0\n')
        sleep.chmod(0o755)
        daemon = self.root / 'start-stop-daemon'
        daemon.write_text(f'''#!{sys.executable}
import json, os, sys
args = sys.argv[1:]
with open(os.environ['TEST_EVENTS'], 'a') as output:
    output.write(json.dumps(args) + '\\n')
if '-K' in args:
    signal = args[args.index('-s') + 1] if '-s' in args else 'TERM'
    if signal in os.environ.get('TEST_DAEMON_HONORS', '').split(','):
        os.kill(int(open(args[args.index('-p') + 1]).read()), 9)
sys.exit(int(os.environ.get('TEST_DAEMON_FAILURE', '0')))
''')
        daemon.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.root) + ':' + os.environ['PATH'],
                        TEST_EVENTS=str(self.events))

    def run_service(self, name, action='start', *flags, **env):
        source = (ROOT / '.shell' / name).read_text()
        for old, new in (
            ('/opt/config/mod/.shell/boot/boot_mode.sh', self.boot),
            ('/opt/config/mod/.shell/common.sh', self.common),
            ('/opt/config/mod_data/camera.conf', self.config),
            ('/run/screen_stream.pid', self.screen_pid),
        ):
            source = source.replace(old, str(new))
        script = self.root / name
        script.write_text(source)
        return subprocess.run(['bash', str(script), action, *flags], env=dict(self.env, **env),
                              text=True, capture_output=True, timeout=10)

    def live_process(self):
        """A running process standing in for a streamer; it is reaped as soon as it dies."""
        process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        self.addCleanup(process.kill)
        threading.Thread(target=process.wait, daemon=True).start()
        return process

    def calls(self):
        return [json.loads(line) for line in self.events.read_text().splitlines()] if self.events.exists() else []

    @staticmethod
    def option(call, name):
        return call[call.index(name) + 1]

    def test_screen_enabled_by_default_uses_separate_framebuffer_instance(self):
        result = self.run_service('S98screen')
        self.assertEqual(result.returncode, 0, result.stderr)
        call, = self.calls()
        self.assertEqual(self.option(call, '-p'), str(self.screen_pid))
        self.assertEqual(self.option(call, '--exec'), str(self.root / 'forge_x_streamer'))
        self.assertEqual(self.option(call, '--source'), 'framebuffer')
        self.assertEqual(self.option(call, '--port'), '8081')

    def test_screen_stream_uses_raw_touch_even_without_a_local_ui(self):
        self.variables.write_text("[Variables]\ndisplay='HEADLESS'\nscreen_stream='ON'\n")
        result = self.run_service('S98screen')
        self.assertEqual(result.returncode, 0, result.stderr)
        call, = self.calls()
        self.assertEqual(self.option(call, '--touch-device'), '/dev/input/event0')
        self.assertEqual(self.option(call, '--touch-calibration'), '/opt/tslib-1.12/etc/pointercal')

    def test_both_streams_write_to_the_shared_log(self):
        self.variables.write_text('[Variables]\ncamera=1\n')
        for name in ('S98camera', 'S98screen'):
            with self.subTest(name=name):
                self.events.unlink(missing_ok=True)
                self.assertEqual(self.run_service(name).returncode, 0)
                call, = self.calls()
                self.assertEqual(self.option(call, '--log-file'), '/data/logFiles/streamer.log')

    def test_screen_stream_is_served_only_through_the_local_web_proxy(self):
        # The page accepts touch input, so it must not be exposed on a second port beside the web proxy.
        self.assertEqual(self.run_service('S98screen').returncode, 0)
        call, = self.calls()
        self.assertEqual(self.option(call, '--listen'), '127.0.0.1')

    def test_screen_uses_configured_frame_rate_and_quality(self):
        self.variables.write_text('[Variables]\nscreen_stream_fps=15\nscreen_stream_quality=75\n')
        result = self.run_service('S98screen')
        self.assertEqual(result.returncode, 0, result.stderr)
        call, = self.calls()
        self.assertEqual(self.option(call, '--fps'), '15')
        self.assertEqual(self.option(call, '--quality'), '75')

    def test_screen_jpeg_limit_is_lowered_only_up_to_quality_90(self):
        full_size_limit = 800 * 480 * 2
        for quality, lowered in ((75, True), (90, True), (91, False), (100, False)):
            with self.subTest(quality=quality):
                self.events.unlink(missing_ok=True)
                self.variables.write_text(f'[Variables]\nscreen_stream_quality={quality}\n')
                result = self.run_service('S98screen')
                self.assertEqual(result.returncode, 0, result.stderr)
                call, = self.calls()
                self.assertEqual('--frame-size' in call, lowered)
                if lowered:
                    self.assertLess(int(self.option(call, '--frame-size')), full_size_limit)

    def test_disabled_services_do_not_launch(self):
        self.variables.write_text('[Variables]\ncamera=0\nscreen_stream=OFF\n')
        for name in ('S98camera', 'S98screen'):
            self.assertEqual(self.run_service(name).returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_camera_passes_capture_settings(self):
        self.variables.write_text('[Variables]\ncamera=1\n')
        self.assertEqual(self.run_service('S98camera').returncode, 0)
        call, = self.calls()
        self.assertEqual(self.option(call, '-p'), str(self.camera_pid))
        self.assertEqual(self.option(call, '--device'), 'auto')
        self.assertEqual(self.option(call, '--resolution'), '640x480')
        self.assertEqual(self.option(call, '--fps'), '15')
        self.assertEqual(self.option(call, '--buffers'), '1')
        self.assertEqual(self.option(call, '--port'), '8080')

    def test_camera_uses_configured_buffer_count(self):
        self.variables.write_text('[Variables]\ncamera=1\n')
        with self.config.open('a') as config:
            config.write('BUFFERS=2\n')
        result = self.run_service('S98camera')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        call, = self.calls()
        self.assertEqual(self.option(call, '--buffers'), '2')

    def test_camera_frame_cap_only_for_explicit_one(self):
        self.variables.write_text('[Variables]\ncamera=1\n')
        for value, expected in (('1', True), ('0', False), ('', False), ('yes', False)):
            with self.subTest(value=value):
                self.events.unlink(missing_ok=True)
                self.config.write_text(f'WIDTH=640\nHEIGHT=480\nFPS=15\nREDUCE_MEMORY={value}\n')
                self.assertEqual(self.run_service('S98camera').returncode, 0)
                call, = self.calls()
                self.assertEqual('--frame-cap' in call, expected)

    def test_camera_device_defaults_to_auto_and_can_be_overridden(self):
        self.variables.write_text('[Variables]\ncamera=1\n')
        for config, device in (('', 'auto'), ('VIDEO=video2\n', '/dev/video2')):
            with self.subTest(config=config):
                self.events.unlink(missing_ok=True)
                self.config.write_text('WIDTH=640\nHEIGHT=480\nFPS=15\n' + config)
                self.assertEqual(self.run_service('S98camera').returncode, 0)
                call, = self.calls()
                self.assertEqual(self.option(call, '--device'), device)

    def test_silent_runs_succeed_quietly(self):
        self.variables.write_text('[Variables]\ncamera=0\nscreen_stream=OFF\n')
        for name in ('S98camera', 'S98screen'):
            with self.subTest(name=name):
                result = self.run_service(name, 'start', '--silent')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn('disabled', result.stdout)

    def test_camera_without_settings_uses_built_in_capture_defaults(self):
        self.variables.write_text('[Variables]\ncamera=1\n')
        self.config.write_text('')
        self.assertEqual(self.run_service('S98camera').returncode, 0)
        call, = self.calls()
        self.assertEqual((self.option(call, '--resolution'), self.option(call, '--fps')), ('640x480', '15'))

    def test_camera_reload_signals_the_running_streamer(self):
        self.variables.write_text('[Variables]\ncamera=1\n')
        for flags in ((), ('--silent',)):
            with self.subTest(flags=flags):
                # The default SIGHUP action ends this stand-in, so a signalled one is observable as terminated.
                streamer = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
                self.addCleanup(streamer.kill)
                self.camera_pid.write_text(str(streamer.pid))
                result = self.run_service('S98camera', 'reload', *flags)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.calls(), [])
                self.assertEqual(streamer.wait(timeout=5), -1)

    def test_start_of_a_running_instance_succeeds_without_launching_or_waiting(self):
        # Boot starts both services and must not be delayed: only a stop may wait.
        self.variables.write_text('[Variables]\ncamera=1\n')
        running = self.live_process()
        for name, pid in (('S98camera', self.camera_pid), ('S98screen', self.screen_pid)):
            with self.subTest(name=name):
                pid.write_text(str(running.pid))
                began = time.monotonic()
                result = self.run_service(name, 'start', TEST_REAL_SLEEP='1')
                self.assertLess(time.monotonic() - began, 3)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('Already running', result.stdout)
                self.assertEqual(self.calls(), [])

    def test_stop_returns_only_after_the_process_has_exited(self):
        streamer = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        self.addCleanup(streamer.kill)
        self.camera_pid.write_text(str(streamer.pid))
        # The daemon stub does not signal anything: the process ends on its own a little later.
        timer = threading.Timer(0.8, lambda: (streamer.kill(), streamer.wait()))
        timer.start()
        self.addCleanup(timer.cancel)

        result = self.run_service('S98camera', 'stop', TEST_REAL_SLEEP='1')

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNotNone(streamer.poll())
        self.assertNotIn('did not stop', result.stderr)
        self.assertFalse(self.camera_pid.exists())

    def test_stop_kills_a_process_that_ignores_the_request(self):
        for name, pid in (('S98camera', self.camera_pid), ('S98screen', self.screen_pid)):
            with self.subTest(name=name):
                self.events.unlink(missing_ok=True)
                stuck = self.live_process()
                pid.write_text(str(stuck.pid))
                result = self.run_service(name, 'stop', TEST_REAL_SLEEP='1', TEST_DAEMON_HONORS='KILL')
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('killing it', result.stderr)
                request, kill = self.calls()
                self.assertNotIn('-s', request)
                self.assertEqual(self.option(kill, '-s'), 'KILL')
                self.assertIsNotNone(stuck.poll())
                self.assertFalse(pid.exists())

    def test_stop_keeps_track_of_a_process_that_cannot_be_killed(self):
        for name, pid in (('S98camera', self.camera_pid), ('S98screen', self.screen_pid)):
            with self.subTest(name=name):
                stuck = self.live_process()
                pid.write_text(str(stuck.pid))
                result = self.run_service(name, 'stop')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('FAIL', result.stdout.splitlines())
                self.assertEqual(pid.read_text(), str(stuck.pid))

    def test_restart_does_not_launch_over_a_process_that_cannot_be_stopped(self):
        self.variables.write_text('[Variables]\ncamera=1\n')
        stuck = self.live_process()
        self.screen_pid.write_text(str(stuck.pid))
        result = self.run_service('S98screen', 'restart')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(arg.startswith('-S') for call in self.calls() for arg in call))

    def test_failed_launch_reports_failure_and_preserves_exit_code(self):
        self.variables.write_text('[Variables]\ncamera=1\n')
        for name in ('S98camera', 'S98screen'):
            for action in ('start', 'restart', 'reload'):
                with self.subTest(name=name, action=action):
                    self.events.unlink(missing_ok=True)
                    result = self.run_service(name, action, TEST_DAEMON_FAILURE='7')
                    call, = self.calls()
                    self.assertTrue(any(arg.startswith('-S') for arg in call))
                    self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
                    self.assertIn('FAIL', result.stdout.splitlines())
                    self.assertNotIn('OK', result.stdout.splitlines())

    def test_stop_targets_only_its_own_pid_file(self):
        self.camera_pid.touch()
        self.screen_pid.touch()
        self.assertEqual(self.run_service('S98screen', 'stop').returncode, 0)
        self.assertFalse(self.screen_pid.exists())
        self.assertTrue(self.camera_pid.exists())

        self.assertEqual(self.run_service('S98camera', 'stop').returncode, 0)
        screen_stop, camera_stop = self.calls()
        for call, pid in ((screen_stop, self.screen_pid), (camera_stop, self.camera_pid)):
            self.assertIn('-K', call)
            self.assertEqual(self.option(call, '-p'), str(pid))

    def test_restart_stops_a_running_instance_before_starting(self):
        self.variables.write_text('[Variables]\ncamera=1\n')
        for name, pid in (('S98camera', self.camera_pid), ('S98screen', self.screen_pid)):
            with self.subTest(name=name):
                self.events.unlink(missing_ok=True)
                running = self.live_process()
                pid.write_text(str(running.pid))
                result = self.run_service(name, 'restart', TEST_DAEMON_HONORS='TERM')
                self.assertEqual(result.returncode, 0, result.stderr)
                stopped, started = self.calls()
                self.assertIn('-K', stopped)
                self.assertTrue(any(arg.startswith('-S') for arg in started))

    def test_restart_of_a_stopped_instance_starts_without_a_stop(self):
        self.variables.write_text('[Variables]\ncamera=1\n')
        for name in ('S98camera', 'S98screen'):
            with self.subTest(name=name):
                self.events.unlink(missing_ok=True)
                result = self.run_service(name, 'restart')
                self.assertEqual(result.returncode, 0, result.stderr)
                started, = self.calls()
                self.assertTrue(any(arg.startswith('-S') for arg in started))

    def test_non_mod_boot_does_not_launch(self):
        for name in ('S98camera', 'S98screen'):
            self.assertEqual(self.run_service(name, TEST_BOOT_MODE='stock').returncode, 0)
        self.assertEqual(self.calls(), [])

