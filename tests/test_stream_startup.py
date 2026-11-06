## Behavioral contracts for the stream startup hook, parameter changes, and legacy camera cleanup.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import configparser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).parents[1]


class StreamStartupTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.events = self.root / 'events'
        self.commands = self.root / 'bin'
        self.commands.mkdir()
        self.scripts = self.root / 'scripts'
        (self.scripts / 'commands').mkdir(parents=True)
        self.variables = self.root / 'variables.cfg'
        self.variables.write_text("[Variables]\ndisplay='FEATHER'\n")
        self.moonraker_url = 'http://127.0.0.1:1'
        self.boot = self.root / 'boot_mode.sh'
        self.boot.write_text('forge_x_boot_mode() { echo mod; }\n')
        self.common = self.root / 'common.sh'
        self.common.write_text(f'''source {ROOT}/.shell/common.sh
MOD={self.root}/chroot
PY={ROOT}/.py
SCRIPTS={self.scripts}
CMDS={self.scripts}/commands
VAR_PATH={self.variables}
CFG_SCRIPT={ROOT}/.shell/commands/zconf.sh
NOT_FIRST_LAUNCH_F={self.root}/not-first
CUSTOM_BOOT_F={self.root}/custom
SCREEN_FOLLOW_UP_LOG={self.root}/screen-log
BOOT_FAILURE_F={self.root}/boot-failure
FORGE_X_SCREEN_BUSY_F={self.root}/screen-busy
CAMERA_PID_F={self.root}/camera.pid
logged() {{ cat; }}
message() {{ echo "$*" >> {self.root}/messages; }}
printer_command() {{ echo "$*" >> {self.root}/prompts; }}
''')
        (self.root / 'not-first').touch()
        self.environment = dict(os.environ, TEST_EVENTS=str(self.events),
                                PATH=str(self.commands) + ':' + os.environ['PATH'])
        self.command('chroot', '''case "$*" in
    *S35tslib*) shift; shift; echo "touch $*" >> "$TEST_EVENTS" ;;
    *set_webcams.py*)
        shift; shift; shift
        echo "webcams $*" >> "$TEST_EVENTS"
        exit "${TEST_WEBCAM_FAILURE:-0}"
        ;;
    *start.sh*)
        echo services >> "$TEST_EVENTS"
        exit "${TEST_SERVICE_FAILURE:-0}"
        ;;
esac
''')
        self.command('ss', 'echo "$TEST_LISTENING"\n')
        self.command('ps', 'exit 0\n')
        self.screen = self.command('screen', 'echo "screen $*" >> "$TEST_EVENTS"\nexit "${TEST_SCREEN_FAILURE:-0}"\n')
        (self.scripts / 'screen_policy.sh').write_text((ROOT / '.shell/screen_policy.sh').read_text())
        for name in ('zconf.sh', 'zdisplay.sh'):
            helper = self.scripts / 'commands' / name
            helper.write_text((ROOT / '.shell/commands' / name).read_text()
                              .replace('/opt/config/mod/.shell/common.sh', str(self.common)))
            helper.chmod(0o755)
        source = (ROOT / '.shell/commands/zstreams.sh').read_text()
        source = source.replace('/opt/config/mod/.shell/common.sh', str(self.common))
        source = source.replace('/etc/init.d/S98screen', str(self.screen))
        helper = self.scripts / 'commands/zstreams.sh'
        helper.write_text(source)
        helper.chmod(0o755)

    def command(self, name, body):
        command = self.commands / name
        command.write_text('#!/bin/sh\n' + body)
        command.chmod(0o755)
        return command

    def run_script(self, path, args, replacements=(), **environment):
        source = path.read_text()
        for old, new in (
            ('/opt/config/mod/.shell/common.sh', self.common),
            ('/opt/config/mod/.shell/boot/boot_mode.sh', self.boot),
            *replacements,
        ):
            source = source.replace(old, str(new))
        script = self.root / path.name
        script.write_text(source)
        return subprocess.run(['bash', str(script), *args],
                              env=dict(self.environment, **environment),
                              text=True, capture_output=True, timeout=10)

    def calls(self):
        return self.events.read_text().splitlines() if self.events.exists() else []

    def webcam_flags(self, call):
        command, *args = call.split()
        self.assertEqual(command, 'webcams')
        return dict(zip(args[::2], args[1::2], strict=True))

    def start(self, **environment):
        migration = self.command('migrate-db', 'exit 0\n')
        return self.run_script(ROOT / '.shell/S99root', ['start'], (
            ('/opt/config/mod/.shell/migrate_db.sh', migration),
            ('/opt/ffmpeg-4.0.2', self.root / 'unavailable-encoder'),
        ), **environment)

    def test_startup_prepares_input_before_services_then_shows_the_wanted_entries(self):
        self.variables.write_text("[Variables]\ndisplay='FEATHER'\ncamera=1\n")
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls()
        self.assertEqual(calls[:2], ['touch start', 'services'])
        flags = self.webcam_flags(calls[2])
        self.assertEqual((flags['--camera'], flags['--screen']), ('1', '1'))
        self.assertGreater(int(flags['--wait']), 0)
        self.assertEqual(len(calls), 3, 'the services started by init must not be restarted')

    def test_entries_are_left_alone_when_moonraker_is_disabled(self):
        self.variables.write_text("[Variables]\ndisplay='FEATHER'\ncamera=1\ndisable_moonraker=1\n")
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls(), ['touch start', 'services'])

        self.events.unlink()
        camera = self.command('camera', 'echo "camera $*" >> "$TEST_EVENTS"\n')
        result = self.run_script(ROOT / '.shell/commands/zchanges.sh', ['camera', '1'], (
            ('/etc/init.d/S98camera', camera),
        ))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls(), ['camera start'])

    def test_optional_webcam_failure_does_not_block_printer_startup(self):
        result = self.start(TEST_WEBCAM_FAILURE='1')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('services', self.calls())
        self.assertFalse((self.root / 'boot-failure').exists())

    def test_service_start_failure_does_not_attempt_webcam_registration(self):
        result = self.start(TEST_SERVICE_FAILURE='7')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), ['touch start', 'services'])

    def test_camera_change_controls_only_the_camera_and_its_entry(self):
        camera = self.command('camera', 'echo "camera $*" >> "$TEST_EVENTS"\n')
        for enabled, action in (('1', 'start'), ('0', 'stop')):
            with self.subTest(enabled=enabled):
                self.events.unlink(missing_ok=True)
                self.variables.write_text(f"[Variables]\ndisplay='HEADLESS'\ncamera={enabled}\n")
                result = self.run_script(ROOT / '.shell/commands/zchanges.sh', ['camera', enabled], (
                    ('/etc/init.d/S98camera', camera),
                ))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                camera_call, webcams = self.calls()
                self.assertEqual(camera_call, f'camera {action}')
                self.assertEqual(self.webcam_flags(webcams), {'--camera': enabled, '--screen': '0'})

    def test_camera_is_not_started_while_another_camera_holds_the_port(self):
        camera = self.command('camera', 'echo "camera $*" >> "$TEST_EVENTS"\n')
        self.variables.write_text("[Variables]\ndisplay='HEADLESS'\ncamera=1\n")
        result = self.run_script(ROOT / '.shell/commands/zchanges.sh', ['camera', '1'], (
            ('/etc/init.d/S98camera', camera),
        ), TEST_LISTENING='tcp LISTEN 0 5 0.0.0.0:8080 0.0.0.0:*')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [])
        self.assertIn('in use', (self.root / 'prompts').read_text())

    def test_other_ports_do_not_look_like_a_busy_camera_port(self):
        camera = self.command('camera', 'echo "camera $*" >> "$TEST_EVENTS"\n')
        self.variables.write_text("[Variables]\ndisplay='HEADLESS'\ncamera=1\n")
        result = self.run_script(ROOT / '.shell/commands/zchanges.sh', ['camera', '1'], (
            ('/etc/init.d/S98camera', camera),
        ), TEST_LISTENING='tcp LISTEN 0 5 0.0.0.0:18080 0.0.0.0:*')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls()[0], 'camera start')

    def test_own_running_camera_does_not_count_as_another_camera(self):
        camera = self.command('camera', 'echo "camera $*" >> "$TEST_EVENTS"\n')
        (self.root / 'camera.pid').write_text(str(os.getpid()))
        self.variables.write_text("[Variables]\ndisplay='HEADLESS'\ncamera=1\n")
        result = self.run_script(ROOT / '.shell/commands/zchanges.sh', ['camera', '1'], (
            ('/etc/init.d/S98camera', camera),
        ), TEST_LISTENING='tcp LISTEN 0 5 0.0.0.0:8080 0.0.0.0:*')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls()[0], 'camera start')

    def test_camera_start_failure_is_reported_and_the_entries_are_still_updated(self):
        camera = self.command('camera', 'exit 3\n')
        self.variables.write_text("[Variables]\ndisplay='HEADLESS'\ncamera=1\n")
        result = self.run_script(ROOT / '.shell/commands/zchanges.sh', ['camera', '1'], (
            ('/etc/init.d/S98camera', camera),
        ))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('failed to start', (self.root / 'messages').read_text())
        webcams, = self.calls()
        self.assertEqual(self.webcam_flags(webcams), {'--camera': '1', '--screen': '0'})

    def test_screen_stream_start_failure_is_reported_and_the_rest_of_the_change_continues(self):
        self.variables.write_text("[Variables]\ndisplay='HEADLESS'\nscreen_stream='ON'\n")
        result = self.run_script(ROOT / '.shell/commands/zchanges.sh', ['screen_stream', 'ON'],
                                 TEST_SCREEN_FAILURE='1')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Screen stream failed to start', result.stderr)
        touch, screen, webcams = self.calls()
        self.assertEqual(self.webcam_flags(webcams), {'--camera': '0', '--screen': '1'})

    def test_screen_stream_change_applies_input_stream_and_entries_together(self):
        for mode, stream in (('AUTO', 0), ('ON', 1), ('OFF', 0)):
            with self.subTest(mode=mode):
                self.events.unlink(missing_ok=True)
                self.variables.write_text(f"[Variables]\ndisplay='HEADLESS'\nscreen_stream='{mode}'\n")
                result = self.run_script(ROOT / '.shell/commands/zchanges.sh', ['screen_stream', mode])
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                touch, screen, webcams = self.calls()
                self.assertEqual((touch, screen), ('touch stop', 'screen restart'))
                self.assertEqual(self.webcam_flags(webcams), {'--camera': '0', '--screen': str(stream)})

    def test_touch_interface_change_controls_only_the_touch_service(self):
        for display, mode, action in (('STOCK', 'ON', 'start'), ('HEADLESS', 'ON', 'start'), ('STOCK', 'AUTO', 'stop')):
            with self.subTest(display=display, mode=mode):
                self.events.unlink(missing_ok=True)
                self.variables.write_text(f"[Variables]\ndisplay='{display}'\ntouch_interface='{mode}'\n")
                result = self.run_script(ROOT / '.shell/commands/zchanges.sh', ['touch_interface', mode])
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.calls(), [f'touch {action}'])

    def test_entry_update_failure_is_reported_without_failing_the_change(self):
        self.variables.write_text("[Variables]\ndisplay='HEADLESS'\n")
        result = self.run_script(ROOT / '.shell/commands/zchanges.sh', ['screen_stream', 'ON'],
                                 TEST_WEBCAM_FAILURE='1')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Camera entries were not updated', result.stdout)

    def test_invalid_preferences_do_not_change_running_services(self):
        self.variables.write_text("[Variables]\ntouch_interface='broken'\n")
        result = self.run_script(ROOT / '.shell/commands/zstreams.sh', ['apply'], (
            ('/etc/init.d/S98screen', self.screen),
        ))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_capture_setting_changes_restart_only_the_stream(self):
        for parameter in ('screen_stream_fps', 'screen_stream_quality'):
            with self.subTest(parameter=parameter):
                self.events.unlink(missing_ok=True)
                result = self.run_script(ROOT / '.shell/commands/zchanges.sh', [parameter, '30'], (
                    ('/etc/init.d/S98screen', self.screen),
                ))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.calls(), ['screen restart'])

    def test_display_change_applies_services_before_restarting_klipper(self):
        self.command('pidof', 'exit 1\n')
        self.command('killall', 'exit 0\n')
        self.command('start-stop-daemon', 'exit 0\n')
        self.command('netd', 'exit 0\n')
        init = self.command('init-main', 'exit 0\n')
        for name, body in (
            ('screen.sh', 'exit 0\n'),
            ('restart_klipper.sh', 'echo klipper-restart >> "$TEST_EVENTS"\n'),
        ):
            script = self.scripts / name
            script.write_text('#!/bin/sh\n' + body)
            script.chmod(0o755)
        self.variables.write_text("[Variables]\ndisplay='FEATHER'\n")
        result = self.run_script(ROOT / '.shell/commands/zdisplay.sh', ['apply'], (
            ('/opt/config/mod/.shell/init-main.sh', init),
            ('/run/netd.sock', self.root / 'netd.sock'),
        ))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        touch, screen, webcams, klipper = self.calls()
        self.assertEqual((touch, screen, klipper), ('touch start', 'screen restart', 'klipper-restart'))
        self.assertEqual(self.webcam_flags(webcams), {'--camera': '0', '--screen': '1'})

    def test_guppy_start_and_restart_prepare_touch_before_the_ui(self):
        touch = self.command('touch', 'echo "touch $*" >> "$TEST_EVENTS"\n')
        ui = self.command('guppy-ui', 'echo "guppy $*" >> "$TEST_EVENTS"\n')
        for action in ('start', 'restart', 'stop'):
            with self.subTest(action=action):
                self.events.unlink(missing_ok=True)
                result = self.run_script(ROOT / '.root/guppyscreen', [action], (
                    ('/opt/config/mod/.root/S35tslib', touch),
                    ('/opt/config/mod/.root/S80guppyscreen', ui),
                ))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                expected = [f'guppy {action}'] if action == 'stop' else ['touch start', f'guppy {action}']
                self.assertEqual(self.calls(), expected)

    def serve_webcam_api(self, *cams):
        """Serve the part of the Moonraker webcam API used by the legacy camera import."""
        api = SimpleNamespace(cams=[dict(cam) for cam in cams], writes=[])

        class Handler(BaseHTTPRequestHandler):
            def reply(self, result):
                body = json.dumps({'result': result}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self.reply({'webcams': api.cams})

            def do_POST(self):
                api.writes.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.reply({})

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.moonraker_url = f'http://127.0.0.1:{server.server_port}'
        return api

    def run_root_start(self, moonraker_conf=None, disable_moonraker=0):
        conf = moonraker_conf or self.root / 'user.moonraker.conf'
        self.variables.write_text(f"[Variables]\ndisplay='HEADLESS'\ndisable_moonraker={disable_moonraker}\n")
        api_client = self.root / 'py'
        api_client.mkdir(exist_ok=True)
        (api_client / 'set_webcams.py').write_text(
            (ROOT / '.py/set_webcams.py').read_text().replace('http://127.0.0.1:7125', self.moonraker_url))
        init = self.root / 'init.d'
        init.mkdir(exist_ok=True)
        proxy = self.root / 'httpd.conf'
        loaded = self.root / 'httpd.loaded.conf'
        # Mainsail metadata edits are outside this contract. The printer's sed edits files in place with a
        # bare -i; the host's BSD or GNU sed needs the suffix argument spelled to match.
        self.command('sed', '''case "$*" in *release_info.json*) exit 0 ;; esac
if [ "$1" = -i ]; then
    shift
    if /usr/bin/sed --version > /dev/null 2>&1; then exec /usr/bin/sed -i "$@"; fi
    exec /usr/bin/sed -i "" "$@"
fi
exec /usr/bin/sed "$@"
''')
        # Moonraker reads its configuration whenever it starts, so record what each start would see.
        self.command('S65moonraker', 'cp "$TEST_CONF" "$TEST_CONF_SEEN.$1" 2>/dev/null\necho "moonraker $*" >> "$TEST_EVENTS"\n')
        self.command('curl', 'exit 0\n')
        self.command('sleep', 'exit 0\n')
        self.command('S45ntpd', 'echo "ntpd $*" >> "$TEST_EVENTS"\n')
        self.command('S70httpd', 'cp "$TEST_HTTPD" "$TEST_HTTPD_LOADED"\necho "httpd $*" >> "$TEST_EVENTS"\n')
        result = self.run_script(ROOT / '.root/start.sh', [], (
            ('/opt/config/mod/.shell/commands/zconf.sh', ROOT / '.shell/commands/zconf.sh'),
            ('/opt/config/mod_data/variables.cfg', self.variables),
            ('/opt/config/mod/.root/', str(self.commands) + '/'),
            ('/opt/config/mod/.py', api_client),
            ('/bin/python3', sys.executable),
            ('/usr/bin/ip', self.root / 'ip'),
            ('/usr/bin/tc', self.root / 'tc'),
            ('/usr/bin/sudo', self.root / 'sudo'),
            ('/root/www/mainsail/release_info.json', self.root / 'release_info.json'),
            ('/etc/httpd.conf', proxy),
            ('/etc/init.d', init),
            ('/opt/config/mod_data/user.moonraker.conf', conf),
        ), TEST_HTTPD=str(proxy), TEST_HTTPD_LOADED=str(loaded),
            TEST_CONF=str(conf), TEST_CONF_SEEN=str(self.root / 'conf.seen'))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('httpd start', self.calls())
        self.assertEqual(loaded.read_bytes(), proxy.read_bytes())
        return proxy

    def test_root_start_adds_missing_proxy_before_httpd_and_does_not_rewrite_it(self):
        proxy = self.root / 'httpd.conf'
        original = 'P:/webcam/:localhost:8080/\n# User configuration'
        proxy.write_text(original)
        proxy.chmod(0o640)
        self.run_root_start()
        prepared = proxy.read_text()
        self.assertTrue(prepared.startswith(original))
        self.assertEqual(prepared[len(original):].strip(), 'P:/screen/:localhost:8081/')
        self.assertEqual(proxy.stat().st_mode & 0o777, 0o640)
        published = proxy.stat()
        self.run_root_start()
        self.assertEqual(proxy.read_text(), prepared)
        self.assertEqual(proxy.stat().st_ino, published.st_ino)
        self.assertEqual(proxy.stat().st_mtime_ns, published.st_mtime_ns)

    def test_root_start_preserves_custom_proxy_backends(self):
        proxy = self.root / 'httpd.conf'
        original = 'P:/webcam/:camera-host:8888/\n  P:/screen/:screen-host:9999/\n'
        proxy.write_text(original)
        self.run_root_start()
        self.assertEqual(proxy.read_text(), original)

    def test_root_start_creates_both_missing_proxies(self):
        proxy = self.run_root_start()
        self.assertCountEqual(proxy.read_text().split(), (
            'P:/webcam/:localhost:8080/', 'P:/screen/:localhost:8081/',
        ))

    def moonraker_sections(self, path):
        parsed = configparser.ConfigParser(interpolation=None)
        parsed.read(path)
        return parsed.sections()

    LEGACY_CAM = {'name': 'cam', 'uid': 'config-uid', 'source': 'config', 'enabled': True,
                  'rotation': 90, 'flip_horizontal': True, 'stream_url': '/custom/?action=stream'}
    NEW_CAM = {'name': '4gx_camera', 'uid': 'entry-uid', 'source': 'database', 'enabled': False,
               'rotation': 0, 'flip_horizontal': False, 'stream_url': '/webcam/?action=stream'}

    def test_root_start_moves_the_legacy_camera_settings_to_the_entry(self):
        legacy = ('[authorization]\ntrusted_clients:\n  0.0.0.0/0\n\n'
                  '[webcam cam]\nenabled: true\nrotation: 90\nstream_url: /custom/?action=stream\n\n'
                  '[webcam external]\nstream_url: http://elsewhere/stream\n')
        conf = self.root / 'user.moonraker.conf'
        conf.write_text(legacy)
        conf.chmod(0o640)
        api = self.serve_webcam_api(self.LEGACY_CAM, self.NEW_CAM)
        self.run_root_start()

        # Moonraker has to read the section for the settings to be available through its API.
        self.assertIn('webcam cam', self.moonraker_sections(self.root / 'conf.seen.start'))
        self.assertEqual(len(api.writes), 1)
        write = api.writes[0]
        self.assertEqual((write['uid'], write['name']), ('entry-uid', '4gx_camera'))
        self.assertEqual((write['rotation'], write['flip_horizontal']), (90, True))
        self.assertEqual(write['stream_url'], '/custom/?action=stream')
        self.assertIs(write['enabled'], False, 'the camera parameter owns the flag')

        self.assertEqual(self.moonraker_sections(conf), ['authorization', 'webcam external'])
        result = conf.read_text()
        self.assertIn('# rotation: 90', result)
        self.assertTrue(result.startswith(legacy.split('[webcam cam]')[0]))
        self.assertTrue(result.endswith('[webcam external]\nstream_url: http://elsewhere/stream\n'))
        self.assertEqual(conf.stat().st_mode & 0o777, 0o640)

        # It keeps the section it has read, so it is restarted once to stop listing the old camera.
        self.assertEqual(self.moonraker_sections(self.root / 'conf.seen.restart'), ['authorization', 'webcam external'])
        self.assertEqual([call for call in self.calls() if call.startswith('moonraker')],
                         ['moonraker start', 'moonraker restart'])

        self.events.unlink()
        self.run_root_start()
        self.assertEqual(len(api.writes), 1, 'the settings are copied once')
        self.assertNotIn('moonraker restart', self.calls(), 'nothing is left to drop')

    def test_root_start_comments_a_legacy_camera_at_the_end_of_the_file(self):
        conf = self.root / 'user.moonraker.conf'
        conf.write_text('[authorization]\ntrusted_clients: 127.0.0.1\n\n[webcam cam]\nenabled: true\n')
        self.serve_webcam_api(self.LEGACY_CAM, self.NEW_CAM)
        self.run_root_start()
        self.assertEqual(self.moonraker_sections(conf), ['authorization'])
        self.assertIn('# enabled: true', conf.read_text())

    def test_root_start_keeps_the_legacy_camera_until_its_settings_can_be_copied(self):
        text = '[webcam cam]\nrotation: 90\n'
        conf = self.root / 'user.moonraker.conf'
        conf.write_text(text)

        with self.subTest('the migration has not created the entry yet'):
            api = self.serve_webcam_api(self.LEGACY_CAM)
            self.run_root_start()
            self.assertEqual(api.writes, [])
            self.assertEqual(conf.read_text(), text)

        with self.subTest('the API is unavailable'):
            self.moonraker_url = 'http://127.0.0.1:1'
            self.run_root_start()
            self.assertEqual(conf.read_text(), text)

        self.assertNotIn('moonraker restart', self.calls(), 'the old camera stays until it can be migrated')

    def test_root_start_comments_the_legacy_camera_when_moonraker_is_disabled(self):
        conf = self.root / 'user.moonraker.conf'
        conf.write_text('[authorization]\ntrusted_clients: 127.0.0.1\n\n[webcam cam]\nrotation: 90\n\n'
                        '[webcam external]\nstream_url: http://elsewhere/stream\n')
        api = self.serve_webcam_api(self.LEGACY_CAM, self.NEW_CAM)
        self.run_root_start(disable_moonraker=1)
        self.assertEqual(self.moonraker_sections(conf), ['authorization', 'webcam external'])
        self.assertIn('# rotation: 90', conf.read_text())
        self.assertEqual(api.writes, [], 'there is no Moonraker to copy the settings through')
        self.assertFalse([call for call in self.calls() if call.startswith('moonraker')])

    def test_root_start_leaves_configs_without_an_active_legacy_camera_untouched(self):
        conf = self.root / 'user.moonraker.conf'
        for text in ('[authorization]\ntrusted_clients: 127.0.0.1\n',
                     '# [webcam cam]\n# enabled: true\n[webcam other]\nstream_url: /x\n'):
            with self.subTest(text=text):
                conf.write_text(text)
                before = conf.stat()
                self.run_root_start()
                self.assertEqual(conf.read_text(), text)
                self.assertEqual(conf.stat().st_ino, before.st_ino)
                self.assertEqual(conf.stat().st_mtime_ns, before.st_mtime_ns)

    def test_root_start_survives_a_missing_moonraker_config(self):
        self.run_root_start(moonraker_conf=self.root / 'absent.conf')
        self.assertFalse((self.root / 'absent.conf').exists())
