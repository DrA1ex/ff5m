"""Behavioral contracts for boot configuration repair.

Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
This file may be distributed under the terms of the GNU GPLv3 license.
"""

import configparser
import os
from pathlib import Path
import re
import subprocess
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).parents[1]
INIT_MAIN = Path(os.environ.get('INIT_MAIN_TEST_SOURCE', ROOT / '.shell/init-main.sh'))
CFG_BACKUP = ROOT / '.py/cfg_backup.py'


class LegacyRestoreTest(unittest.TestCase):
    def repair(self, rules, backup=True):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'config'
            commands = root / 'scripts/commands'
            params = config / 'mod/.cfg'
            data = config / 'mod_data'
            for path in (params, data, commands, root / 'tmp'):
                path.mkdir(parents=True, exist_ok=True)
            for name in ('printer.cfg', 'printer.base.cfg'):
                (config / name).write_text('[printer]\nmax_velocity: 100\n')
            if backup:
                (config / 'printer.base.cfg.bak').write_text(
                    '[printer]\nmax_velocity: 300\n')
            if rules is not None:
                (data / 'backup.params.cfg').write_text(rules)
            for name in ('init.display.feather.cfg', 'init.cfg', 'init.base.cfg',
                         'tuning.off.cfg', 'init.plugins.cfg'):
                (params / name).write_text('-[obsolete]\n')
            for name in ('data.init.cfg', 'data.init.base.cfg'):
                (params / name).write_text('')
            for name, output in (('zdisplay.sh', 'FEATHER'), ('zconf.sh', '0')):
                path = commands / name
                path.write_text('#!/bin/sh\nprintf "%s\\n" "' + output + '"\n')
                path.chmod(0o755)
            gnu_sed = shutil.which('gsed')
            if gnu_sed:
                (commands / 'sed').symlink_to(gnu_sed)
            candidate = root / 'init-main.sh'
            paths = {'/tmp/': str(root / 'tmp') + '/',
                     '/opt/config/': str(config) + '/'}
            candidate.write_text(re.sub(
                r'/tmp/|/opt/config/', lambda match: paths[match.group()],
                INIT_MAIN.read_text()))
            environment = dict(os.environ, PY=str(CFG_BACKUP.parent),
                               CMDS=str(commands), SCRIPTS=str(commands.parent),
                               VAR_PATH=str(data / 'variables.cfg'), MOD='/unused',
                               KLIPPER_USER_CONFIG=str(data / 'plugins.cfg'),
                               TEST_CALLS=str(root / 'calls'),
                               PATH=str(commands) + os.pathsep + os.environ['PATH'],
                               TEST_PYTHON=os.environ.get('CFG_TEST_PYTHON', sys.executable))
            result = subprocess.run(
                ['bash', '-e', '-c', '''
                    source() { return 0; }
                    builtin source "$1"
                    prepare_init_move() { return 1; }
                    sync() { :; }
                    chroot() {
                        shift
                        shift
                        printf 'call\n' >> "$TEST_CALLS"
                        "$TEST_PYTHON" "$@"
                    }
                    fix_config
                ''', 'config-repair-test', str(candidate)],
                env=environment, text=True, capture_output=True)
            restored = configparser.RawConfigParser()
            restored.read(config / 'printer.base.cfg')
            calls = (root / 'calls').read_text().splitlines() if (root / 'calls').exists() else []
            return result, restored.getint('printer', 'max_velocity'), len(calls)

    def test_blank_and_comment_only_rules_do_not_launch_legacy_restore(self):
        for rules in ('', '\n \t\n', '# [printer] max_velocity\n  # example\n'):
            with self.subTest(rules=rules):
                result, velocity, calls = self.repair(rules)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(velocity, 100)
                self.assertEqual(calls, 1)

    def test_real_rules_restore_values_from_the_legacy_backup(self):
        result, velocity, calls = self.repair('# example\n [printer] max_velocity\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(velocity, 300)
        self.assertEqual(calls, 2)

    def test_missing_rules_do_not_launch_legacy_restore(self):
        result, velocity, calls = self.repair(None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(velocity, 100)
        self.assertEqual(calls, 1)

    def test_missing_backup_does_not_launch_legacy_restore(self):
        result, velocity, calls = self.repair('[printer] max_velocity\n', backup=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(velocity, 100)
        self.assertEqual(calls, 1)

    def test_invalid_noncomment_rules_still_use_the_parser_and_are_nonfatal(self):
        result, velocity, calls = self.repair('invalid rule\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(velocity, 100)
        self.assertEqual(calls, 2)


if __name__ == '__main__':
    unittest.main()
