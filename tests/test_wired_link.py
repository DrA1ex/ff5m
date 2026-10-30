"""Behavioral contracts for optional early Ethernet preparation.

Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
This file may be distributed under the terms of the GNU GPLv3 license.
"""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).parents[1]
INIT_MAIN = Path(os.environ.get('INIT_MAIN_TEST_SOURCE', ROOT / '.shell/init-main.sh'))
WIRED_LINK = ROOT / '.shell/boot/prepare-wired-link.sh'


class WiredLinkPreparationTest(unittest.TestCase):
    def prepare(self, interfaces, command_status=0):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            configuration = root / 'interfaces'
            if interfaces is not None:
                configuration.write_text(interfaces)
            calls = root / 'calls'
            environment = dict(os.environ, LINK_TEST_CALLS=str(calls),
                               LINK_TEST_STATUS=str(command_status))
            result = subprocess.run(
                ['bash', '-e', '-c', '''
                    function /bin/busybox() {
                        case "$1" in
                            awk) shift; command awk "$@" ;;
                            ifconfig)
                                shift
                                printf '%s\n' "$*" >> "$LINK_TEST_CALLS"
                                return "$LINK_TEST_STATUS"
                            ;;
                            *) return 127 ;;
                        esac
                    }
                    source "$1" "$2"
                    printf 'initialization continued\n'
                ''', 'link-preparation-test', str(WIRED_LINK), str(configuration)],
                env=environment, text=True, capture_output=True)
            return result, calls.read_text().splitlines() if calls.exists() else []

    def test_simple_auto_dhcp_only_brings_up_the_link(self):
        result, calls = self.prepare('auto lo eth0 # wired\niface lo inet loopback\niface eth0 inet dhcp\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ['eth0 up'])

    def test_configured_mac_is_set_before_bringing_up_the_link(self):
        result, calls = self.prepare('auto eth0\niface eth0 inet dhcp\nhwaddress ether 02:00:00:00:00:17\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ['eth0 hw ether 02:00:00:00:00:17 up'])

    def test_unrecognized_or_disabled_config_leaves_stock_startup_in_charge(self):
        for interfaces in (None, '', 'auto lo\niface lo inet loopback\n',
                           'iface eth0 inet dhcp\n',
                           'auto eth0\niface eth0 inet static\n',
                           'auto eth0\niface eth0 inet6 auto\n',
                           'auto eth0\niface eth0 inet dhcp\npre-up run-custom-command\n',
                           'auto eth0\niface eth0 inet dhcp\nsource other-interfaces\n',
                           'auto eth0\niface eth0 inet dhcp\nhwaddress ether invalid\n',
                           'auto eth0\niface eth0 inet dhcp\nmtu 1200\n',
                           'auto eth0\niface eth0 inet dhcp\niface eth0 inet static\n'):
            with self.subTest(interfaces=interfaces):
                result, calls = self.prepare(interfaces)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(calls, [])

    def test_failed_or_unavailable_up_command_does_not_abort_initialization(self):
        for status in (1, 127):
            with self.subTest(status=status):
                result, calls = self.prepare('auto eth0\niface eth0 inet dhcp\n', status)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(calls, ['eth0 up'])
                self.assertIn('initialization continued', result.stdout)


class WiredLinkInitializationTest(unittest.TestCase):
    def initialize(self, special_status=1, helper_status=0, helper_mode=0o755):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / 'scripts'
            (scripts / 'boot').mkdir(parents=True)
            screen = scripts / 'screen.sh'
            screen.write_text('#!/bin/sh\nexit 0\n')
            screen.chmod(0o755)
            if helper_status is not None:
                helper = scripts / 'boot/prepare-wired-link.sh'
                helper.write_text('#!/bin/sh\nprintf "helper\\n" >> "$TEST_CALLS"\nexit ' + str(helper_status) + '\n')
                helper.chmod(helper_mode)
            calls = root / 'calls'
            result = subprocess.run(['bash', '-e', '-c', '''
                source() { return 0; }
                builtin source "$1"
                rm() { :; }
                handle_special_boot_mode() { return "$SPECIAL_STATUS"; }
                date() { :; }
                mount_data_partition() {
                    printf 'normal initialization\n' >> "$TEST_CALLS"
                    exit 0
                }
                initialize
            ''', 'initializer-link-test', str(INIT_MAIN)],
                env=dict(os.environ, SCRIPTS=str(scripts), TEST_CALLS=str(calls),
                         SPECIAL_STATUS=str(special_status)), capture_output=True, text=True)
            return result, calls.read_text().splitlines() if calls.exists() else []

    def test_normal_path_prepares_link_before_remaining_initialization(self):
        result, calls = self.initialize()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ['helper', 'normal initialization'])

    def test_missing_failing_or_nonexecutable_helper_keeps_normal_initialization(self):
        for status, mode in ((None, 0o755), (1, 0o755), (127, 0o755), (0, 0o644)):
            with self.subTest(status=status, mode=mode):
                result, calls = self.initialize(helper_status=status, helper_mode=mode)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(calls[-1:], ['normal initialization'])
                self.assertIn('stock network startup will retry', result.stdout)

    def test_special_or_interrupted_boot_never_runs_link_preparation(self):
        for status, expected in ((0, 10), (2, 2), (137, 137)):
            with self.subTest(status=status):
                result, calls = self.initialize(special_status=status)
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
