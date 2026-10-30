"""Public shell/native invocation protocol; filesystem policy is tested in Private.

Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
This file may be distributed under the terms of the GNU GPLv3 license.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ENTRY = Path(__file__).resolve().parents[1] / '.shell/klipper_overlay.sh'


class OverlayEntryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='overlay-entry-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        binary = self.root / 'overlay'
        binary.write_text('''#!/bin/sh
printf '%s\\0' "$@" --env "$KLIPPER_OVERLAY_SRC" "$KLIPPER_OVERLAY_TARGET" \\
    "$KLIPPER_USER_DIR" "$KLIPPER_USER_CONFIG" "$VAR_PATH" > "$CALLS"
exit "${TEST_RESULT:-0}"
''')
        binary.chmod(0o755)
        self.calls = self.root / 'calls'
        self.env = dict(os.environ, KLIPPER_OVERLAY_BIN=str(binary), CALLS=str(self.calls),
                        KLIPPER_OVERLAY_SRC=str(self.root / 'source with spaces'),
                        KLIPPER_OVERLAY_TARGET=str(self.root / 'target'),
                        KLIPPER_USER_DIR=str(self.root / 'plugins'),
                        KLIPPER_USER_CONFIG=str(self.root / 'plugins.cfg'),
                        VAR_PATH=str(self.root / 'variables.cfg'))

    def run_entry(self, command, **environment):
        result = subprocess.run(['bash', '-c', 'source "$1"; ' + command, 'entry', str(ENTRY)],
                                env=dict(self.env, **environment), capture_output=True)
        calls = self.calls.read_bytes().split(b'\0')[:-1] if self.calls.exists() else []
        return result.returncode, [os.fsdecode(item) for item in calls]

    def test_install_forwards_priority_and_paths(self):
        code, values = self.run_entry('apply_klipper_patches 1')
        self.assertEqual(code, 0)
        self.assertEqual(values[:3], ['install', '1', '--env'])
        self.assertEqual(values[3:], [self.env[key] for key in (
            'KLIPPER_OVERLAY_SRC', 'KLIPPER_OVERLAY_TARGET', 'KLIPPER_USER_DIR',
            'KLIPPER_USER_CONFIG', 'VAR_PATH')])

    def test_uninstall_selects_native_operation_and_preserves_arguments(self):
        code, values = self.run_entry('klipper_overlay_clean_links "source path" "target path" remove')
        self.assertEqual(code, 0)
        self.assertEqual(values[:4], ['uninstall', '--env', 'source path', 'target path'])

    def test_failed_native_operation_keeps_exit_status(self):
        code, values = self.run_entry('apply_klipper_patches', TEST_RESULT='17')
        self.assertEqual(code, 17)
        self.assertEqual(values[0], 'install')

    def test_clear_cache_forwards_exact_path(self):
        code, values = self.run_entry('klipper_overlay_clear_user_cache "a path/module.py"')
        self.assertEqual(code, 0)
        self.assertEqual(values[:2], ['clear-cache', 'a path/module.py'])
