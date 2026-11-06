## Observable screen and touch requirements resolved by the real policy file.
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
SCREEN_POLICY = ROOT / '.shell/screen_policy.sh'


class ScreenPolicyTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.variables = Path(directory.name) / 'variables.cfg'
        # The policy reads the screen through the display switch's own reader, which needs only these two parameters.
        commands = Path(directory.name) / 'commands'
        commands.mkdir()
        (commands / 'zconf.sh').write_text((ROOT / '.shell/commands/zconf.sh').read_text())
        (commands / 'zdisplay.sh').write_text((ROOT / '.shell/commands/zdisplay.sh').read_text()
                                              .replace('source /opt/config/mod/.shell/common.sh', ''))
        for script in commands.iterdir():
            script.chmod(0o755)
        self.environment = dict(os.environ, CFG_SCRIPT=str(commands / 'zconf.sh'), CMDS=str(commands),
                                VAR_PATH=str(self.variables))

    def query(self):
        return subprocess.run([
            'bash', '-c', 'source "$1"; screen_policy || exit $?; '
            'printf "%s\\n" "$SCREEN_STREAM_ENABLED" "$SCREEN_TOUCH_ENABLED"',
            'bash', str(SCREEN_POLICY),
        ], env=self.environment, text=True, capture_output=True, timeout=10)

    def state(self, **values):
        self.variables.write_text('[Variables]\n' + ''.join(f"{key}='{value}'\n" for key, value in values.items()))
        result = self.query()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        stream, touch = result.stdout.splitlines()
        return bool(int(stream)), bool(int(touch))

    def test_defaults_stream_feather_and_enable_its_touch(self):
        self.assertEqual(self.state(), (True, True))

    def test_auto_streams_known_screens_but_not_plain_headless(self):
        for display in ('STOCK', 'FEATHER', 'GUPPY'):
            with self.subTest(display=display):
                self.assertTrue(self.state(display=display)[0])
        self.assertEqual(self.state(display='HEADLESS'), (False, False))

    def test_stream_override_does_not_change_local_input_requirements(self):
        for display in ('STOCK', 'FEATHER', 'GUPPY', 'HEADLESS'):
            _, automatic_touch = self.state(display=display)
            for mode, enabled in (('ON', True), ('OFF', False)):
                with self.subTest(display=display, mode=mode):
                    self.assertEqual(self.state(display=display, screen_stream=mode), (enabled, automatic_touch))

    def test_unknown_display_is_treated_as_the_default_screen(self):
        self.assertEqual(self.state(display='broken'), self.state())

    def test_local_alternative_screens_keep_input_without_streaming(self):
        for display in ('FEATHER', 'GUPPY'):
            with self.subTest(display=display):
                self.assertEqual(self.state(display=display, screen_stream='OFF'), (False, True))
        self.assertFalse(self.state(display='STOCK')[1])

    def test_explicit_touch_adds_input_on_any_screen_without_changing_stream_selection(self):
        for display in ('STOCK', 'FEATHER', 'GUPPY', 'HEADLESS'):
            with self.subTest(display=display):
                automatic_stream, _ = self.state(display=display)
                self.assertEqual(self.state(display=display, touch_interface='ON'), (automatic_stream, True))

    def test_invalid_modes_are_rejected_without_publishing_state(self):
        for key, values in (('screen_stream', ('1', '0', 'broken')), ('touch_interface', ('OFF', 'broken'))):
            for value in values:
                with self.subTest(key=key, value=value):
                    self.variables.write_text(f"[Variables]\n{key}='{value}'\n")
                    result = self.query()
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, '')
