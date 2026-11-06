## Contracts for showing and hiding the Forge-X camera entries through the Moonraker API.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / '.py'))
import set_webcams


class WebcamAPI:
    """The part of the Moonraker webcam API the client may use."""

    def __init__(self, *cams):
        self.cams = [dict(cam) for cam in cams]
        self.writes = []

    def __call__(self, method, endpoint, data=None):
        if (method, endpoint) == ('GET', '/server/webcams/list'):
            return {'webcams': [dict(cam) for cam in self.cams]}
        if (method, endpoint) != ('POST', '/server/webcams/item'):
            raise AssertionError((method, endpoint))
        self.writes.append(data)
        next(cam for cam in self.cams if cam['uid'] == data['uid']).update(data)
        return {}


def cam(name, enabled, source='database', **fields):
    return dict(fields, name=name, uid='uid-' + name, enabled=enabled, source=source)


class SetWebcamsTest(unittest.TestCase):
    def apply(self, api, camera, screen, wait=0):
        with redirect_stdout(io.StringIO()) as output:
            set_webcams.set_enabled(api, {'camera': camera, 'screen': screen}, wait)
        return output.getvalue()

    def state(self, api):
        return {c['name']: c['enabled'] for c in api.cams}

    def test_only_the_forge_x_entries_change_and_only_their_enabled_flag(self):
        api = WebcamAPI(
            cam('4gx_camera', False, rotation=90, stream_url='/custom/'),
            cam('4gx_screen', True, target_fps=12),
            cam('user_cam', True),
        )
        self.apply(api, camera=True, screen=False)
        self.assertEqual(self.state(api), {'4gx_camera': True, '4gx_screen': False, 'user_cam': True})
        self.assertEqual(api.cams[0]['rotation'], 90)
        self.assertEqual(api.cams[0]['stream_url'], '/custom/')
        self.assertEqual(api.cams[1]['target_fps'], 12)
        for write in api.writes:
            self.assertEqual(set(write), {'uid', 'enabled'})

    def test_entries_already_in_the_wanted_state_are_not_written(self):
        api = WebcamAPI(cam('4gx_camera', True), cam('4gx_screen', False))
        self.apply(api, camera=True, screen=False)
        self.assertEqual(api.writes, [])

    def test_a_manual_change_is_corrected_on_the_next_run(self):
        api = WebcamAPI(cam('4gx_camera', True), cam('4gx_screen', True))
        self.apply(api, camera=False, screen=True)
        self.assertEqual(self.state(api), {'4gx_camera': False, '4gx_screen': True})

    def test_missing_and_config_defined_entries_are_left_alone(self):
        api = WebcamAPI(cam('4gx_screen', False, source='config'))
        output = self.apply(api, camera=True, screen=True)
        self.assertEqual(api.writes, [])
        self.assertIn('4gx_camera', output)
        self.assertIn('4gx_screen', output)

    def test_startup_waits_for_the_api_and_a_later_update_does_not_retry(self):
        calls = []

        def starting(method, endpoint, data=None):
            calls.append(endpoint)
            if len(calls) < 3:
                raise OSError('starting')
            return {'webcams': []}

        with patch.object(set_webcams.time, 'sleep') as sleep:
            self.apply(starting, camera=True, screen=True, wait=30)
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleep.call_count, 2)

        def offline(*args):
            raise OSError('offline')

        with patch.object(set_webcams.time, 'sleep') as sleep:
            with self.assertRaises(OSError):
                self.apply(offline, camera=True, screen=True)
        sleep.assert_not_called()

    def test_command_line_reports_an_unavailable_api_with_a_failure_status(self):
        def offline(*args):
            raise OSError('offline')

        argv = ['set_webcams.py', '--camera', '1', '--screen', '0']
        with patch.object(sys, 'argv', argv), patch.object(set_webcams, 'api_request', offline):
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(set_webcams.main(), 1)
        self.assertIn('offline', output.getvalue())

    def test_command_line_applies_the_requested_flags(self):
        api = WebcamAPI(cam('4gx_camera', False), cam('4gx_screen', True))
        argv = ['set_webcams.py', '--camera', '1', '--screen', '0']
        with patch.object(sys, 'argv', argv), patch.object(set_webcams, 'api_request', api):
            self.assertEqual(set_webcams.main(), 0)
        self.assertEqual(self.state(api), {'4gx_camera': True, '4gx_screen': False})

    def test_http_client_sends_json_to_the_local_api(self):
        with patch.object(set_webcams, 'urlopen') as open_url:
            open_url.return_value.__enter__.return_value.read.return_value = b'{"result":{}}'
            data = {'uid': 'camera', 'enabled': False}
            self.assertEqual(set_webcams.api_request('POST', '/server/webcams/item', data), {})
            request = open_url.call_args.args[0]
            self.assertEqual(request.full_url, 'http://127.0.0.1:7125/server/webcams/item')
            self.assertEqual(json.loads(request.data), data)


if __name__ == '__main__':
    unittest.main()
