## Tests for Forge-X network scripts.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import os
import pathlib
import re
import shlex
import subprocess
import tempfile
import textwrap
import unittest


ROOT = pathlib.Path(__file__).parents[1]
COMMON = ROOT / ".shell" / "network_common.sh"
BOOT = ROOT / ".shell" / "boot" / "boot.sh"
WIFI_CONNECT = ROOT / ".shell" / "boot" / "wifi_connect.sh"
HELPER = ROOT / ".shell" / "commands" / "znetwork.sh"


NETWORK_SCRIPTS = (COMMON, BOOT, WIFI_CONNECT, HELPER)


def write_executable(path, content):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    path.chmod(0o755)


def extract_shell_function(script, name):
    """Return a top-level shell function whose closing brace is unindented."""
    lines = script.splitlines(keepends=True)
    start = None
    for index, line in enumerate(lines):
        if re.match(rf"^{re.escape(name)}\s*\(\)\s*\{{\s*$", line):
            start = index
            break
    if start is None:
        raise AssertionError(f"Function {name!r} was not found")

    for index in range(start + 1, len(lines)):
        if lines[index].rstrip("\n") == "}":
            return "".join(lines[start:index + 1])

    raise AssertionError(f"Function {name!r} is not terminated")


def local_common_copy(directory):
    """Copy network_common.sh while omitting its target-only common.sh import."""
    content = COMMON.read_text(encoding="utf-8")
    content, replacements = re.subn(
        r"^\s*(?:source|\.)\s+/opt/config/mod/\.shell/common\.sh\s*$",
        ": # target common.sh is not needed by these isolated tests",
        content,
        flags=re.MULTILINE,
    )
    if replacements != 1:
        raise AssertionError("Unexpected network_common.sh import layout")

    target = pathlib.Path(directory) / "network_common.local.sh"
    target.write_text(content, encoding="utf-8")
    return target


def run(command, *, env=None, cwd=None):
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


class ZNetworkSandbox:
    """Local znetwork.sh harness with printer commands replaced by stubs."""

    def __init__(self, directory):
        self.root = pathlib.Path(directory)
        self.shell = self.root / ".shell"
        self.boot = self.shell / "boot"
        self.commands = self.shell / "commands"
        self.bin = self.root / "bin"
        self.mod_data = self.root / "mod_data"
        self.vendor = self.root / "vendor"
        self.events = self.root / "events.log"
        self.mode_file = self.mod_data / "network_mode"
        self.wpa_config = self.root / "wpa_supplicant.conf"
        self.wpa_backup = self.root / "wpa_supplicant.conf.feather-stock"
        self.wifi_marker = self.root / "wifi_connected"
        self.ethernet_marker = self.root / "ethernet_connected"
        self.ip_file = self.root / "net_ip"
        self.candidate = self.root / "wpa_candidate.conf"
        self.script = self.commands / "znetwork.sh"

        for path in (self.boot, self.commands, self.bin, self.mod_data, self.vendor):
            path.mkdir(parents=True, exist_ok=True)

        self._write_network_common_stub()
        self._write_command_stubs()
        self._write_wifi_connect_stub()
        self._write_staged_helper()

    @staticmethod
    def _quoted(path):
        return shlex.quote(str(path))

    def _replace_assignment(self, script, name, value):
        replacement = f"{name}={self._quoted(value)}"
        script, count = re.subn(
            rf"^{re.escape(name)}=.*$",
            replacement,
            script,
            count=1,
            flags=re.MULTILINE,
        )
        if count != 1:
            raise AssertionError(f"Assignment {name} was not found")
        return script

    def _write_staged_helper(self):
        script = HELPER.read_text(encoding="utf-8")
        path_value = f"{self.bin}:/usr/sbin:/usr/bin:/sbin:/bin"
        script = self._replace_assignment(script, "PATH", path_value)
        script = self._replace_assignment(script, "SCRIPTS", self.shell)
        script = self._replace_assignment(script, "MOD_DATA", self.mod_data)
        script = self._replace_assignment(script, "MODE_FILE", self.mode_file)
        script = self._replace_assignment(script, "WPA_CONFIG", self.wpa_config)
        script = self._replace_assignment(script, "WPA_BACKUP", self.wpa_backup)
        script = self._replace_assignment(script, "WIFI_MARKER", self.wifi_marker)
        script = self._replace_assignment(script, "ETHERNET_MARKER", self.ethernet_marker)
        script = self._replace_assignment(script, "IP_FILE", self.ip_file)
        script = script.replace(
            "/tmp/feather-wpa-active.conf",
            str(self.candidate),
        )
        script = script.replace(
            "/opt/config/Adventurer5M*.json",
            str(self.vendor / "Adventurer5M*.json"),
        )
        self.script.write_text(script, encoding="utf-8")
        self.script.chmod(0o755)

    def _write_network_common_stub(self):
        write_executable(
            self.shell / "network_common.sh",
            r"""
            network_ipv4() {
                case "$1" in
                    wlan0) printf '%s\n' "${TEST_WLAN_IP:-}" ;;
                    eth0) printf '%s\n' "${TEST_ETH_IP:-}" ;;
                esac
            }

            network_activate_dhcp() {
                printf 'dhcp:%s\n' "$1" >> "$TEST_EVENT_LOG"
                [ "${TEST_DHCP_FAIL:-}" != "$1" ]
            }

            network_deactivate_interface() {
                mode=""
                [ -f "$MODE_FILE" ] && mode=$(head -n 1 "$MODE_FILE")
                wpa_state=missing
                [ -f "$WPA_CONFIG" ] && wpa_state=present
                printf 'deactivate:%s:mode=%s:wpa=%s\n' \
                    "$1" "$mode" "$wpa_state" >> "$TEST_EVENT_LOG"
            }
            """,
        )

    def _write_command_stubs(self):
        write_executable(
            self.bin / "wpa_cli",
            r"""
            #!/bin/sh
            case " $* " in
                *" status "*)
                    printf 'wpa_state=COMPLETED\nssid=TestNetwork\n'
                    ;;
                *" signal_poll "*)
                    printf 'RSSI=-45\n'
                    ;;
            esac
            exit 0
            """,
        )
        write_executable(
            self.bin / "ip",
            """
            #!/bin/sh
            exit 0
            """,
        )
        for command in ("killall", "sync", "insmod", "modprobe", "wpa_supplicant"):
            write_executable(
                self.bin / command,
                """
                #!/bin/sh
                exit 0
                """,
            )

    def _write_wifi_connect_stub(self):
        write_executable(
            self.boot / "wifi_connect.sh",
            """
            #!/bin/sh
            exit "${TEST_WIFI_CONNECT_RESULT:-0}"
            """,
        )

    def environment(self, **values):
        env = os.environ.copy()
        env.update({
            "TEST_EVENT_LOG": str(self.events),
            "TEST_WIFI_CONNECT_RESULT": "0",
            "TEST_WLAN_IP": "",
            "TEST_ETH_IP": "",
            "TEST_DHCP_FAIL": "",
        })
        env.update({key: str(value) for key, value in values.items()})
        return env

    def invoke(self, *arguments, env=None):
        return run(
            ["sh", str(self.script), *map(str, arguments)],
            env=env or self.environment(),
        )

    def event_lines(self):
        if not self.events.exists():
            return []
        return self.events.read_text(encoding="utf-8").splitlines()


class NetworkScriptsTest(unittest.TestCase):
    def test_shell_scripts_have_valid_syntax(self):
        for script in NETWORK_SCRIPTS:
            subprocess.run(["bash", "-n", str(script)], check=True)
        for script in (COMMON, HELPER):
            subprocess.run(["sh", "-n", str(script)], check=True)

    def test_network_common_loads_in_local_posix_harness(self):
        with tempfile.TemporaryDirectory() as directory:
            common = local_common_copy(directory)
            result = run([
                "sh", "-c",
                '. "$1"; command -v network_ipv4 >/dev/null; '
                'command -v network_clear_dns >/dev/null; '
                'command -v network_activate_dhcp >/dev/null',
                "network-test", str(common),
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")

    def test_dns_cleanup_removes_only_requested_interface_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            common = local_common_copy(directory)
            resolv = directory / "resolv.conf"
            resolv.write_text(
                "nameserver 1.1.1.1 # wlan0\n"
                "nameserver 192.168.2.1 # eth0\n"
                "search lan # wlan0\n",
                encoding="utf-8",
            )
            result = run([
                "sh", "-c",
                'NETWORK_RESOLV_CONF="$1"; . "$2"; '
                'network_clear_dns wlan0',
                "network-test", str(resolv), str(common),
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertEqual(
                resolv.read_text(encoding="utf-8"),
                "nameserver 192.168.2.1 # eth0\n",
            )

    def test_dns_cleanup_preserves_original_when_filter_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            common = local_common_copy(directory)
            resolv = directory / "resolv.conf"
            original = "nameserver 1.1.1.1 # wlan0\n"
            resolv.write_text(original, encoding="utf-8")

            stub_bin = directory / "bin"
            write_executable(
                stub_bin / "grep",
                """
                #!/bin/sh
                exit 2
                """,
            )
            env = os.environ.copy()
            env["PATH"] = f"{stub_bin}:/usr/bin:/bin"
            result = run([
                "sh", "-c",
                'NETWORK_RESOLV_CONF="$1"; . "$2"; '
                'network_clear_dns wlan0',
                "network-test", str(resolv), str(common),
            ], env=env)

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(resolv.read_text(encoding="utf-8"), original)

    def test_network_ipv4_returns_first_ipv4_address(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            common = local_common_copy(directory)
            stub_bin = directory / "bin"
            write_executable(
                stub_bin / "ip",
                """
                #!/bin/sh
                cat <<'EOF'
                3: wlan0: <UP> mtu 1500
                    inet6 fe80::1/64 scope link
                    inet 192.168.10.25/24 scope global wlan0
                    inet 192.168.10.26/24 scope global secondary wlan0
                EOF
                """,
            )
            env = os.environ.copy()
            env["PATH"] = f"{stub_bin}:/usr/bin:/bin"
            result = run([
                "sh", "-c",
                '. "$1"; network_ipv4 wlan0',
                "network-test", str(common),
            ], env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "192.168.10.25\n")

    def test_dhcp_success_does_not_run_failure_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            common = local_common_copy(directory)
            events = directory / "events.log"
            stub_bin = directory / "bin"
            write_executable(
                stub_bin / "udhcpc",
                """
                #!/bin/sh
                exit 0
                """,
            )
            env = os.environ.copy()
            env["PATH"] = f"{stub_bin}:/usr/bin:/bin"
            result = run([
                "sh", "-c",
                textwrap.dedent(r"""
                    events=$1
                    . "$2"
                    network_prepare_interface() {
                        printf 'prepare:%s\n' "$1" >> "$events"
                    }
                    network_ipv4() {
                        printf '192.168.1.20\n'
                    }
                    network_stop_udhcpc() {
                        printf 'stop:%s\n' "$1" >> "$events"
                    }
                    network_clear_interface() {
                        printf 'clear:%s\n' "$1" >> "$events"
                    }
                    network_activate_dhcp wlan0 1
                """),
                "network-test", str(events), str(common),
            ], env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(events.read_text(encoding="utf-8"), "prepare:wlan0\n")

    def test_dhcp_timeout_stops_client_and_clears_interface(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            common = local_common_copy(directory)
            events = directory / "events.log"
            stub_bin = directory / "bin"
            write_executable(
                stub_bin / "udhcpc",
                """
                #!/bin/sh
                exit 0
                """,
            )
            env = os.environ.copy()
            env["PATH"] = f"{stub_bin}:/usr/bin:/bin"
            result = run([
                "sh", "-c",
                textwrap.dedent(r"""
                    events=$1
                    . "$2"
                    network_prepare_interface() {
                        printf 'prepare:%s\n' "$1" >> "$events"
                    }
                    network_ipv4() {
                        return 0
                    }
                    network_stop_udhcpc() {
                        printf 'stop:%s\n' "$1" >> "$events"
                    }
                    network_clear_interface() {
                        printf 'clear:%s\n' "$1" >> "$events"
                    }
                    network_activate_dhcp wlan0 0
                """),
                "network-test", str(events), str(common),
            ], env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(
                events.read_text(encoding="utf-8"),
                "prepare:wlan0\nstop:wlan0\nclear:wlan0\n",
            )

    def test_boot_prefers_valid_persisted_mode_over_vendor_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            mode_file = directory / "network_mode"
            config_file = directory / "Adventurer5M.json"
            events = directory / "events.log"
            mode_file.write_text("WIFI\n", encoding="utf-8")
            config_file.write_text(
                '{"ethernetStatus": true, "other": 0}\n',
                encoding="utf-8",
            )

            function = extract_shell_function(
                BOOT.read_text(encoding="utf-8"),
                "network_init",
            )
            function = function.replace(
                "/opt/config/mod_data/network_mode",
                shlex.quote(str(mode_file)),
            )
            function = function.replace(
                "/opt/config/Adventurer5M*.json",
                shlex.quote(str(config_file)),
            )

            harness = textwrap.dedent(r"""
                events=$1
                wifi_init() {
                    printf 'wifi:%s\n' "$1" >> "$events"
                }
                ethernet_init() {
                    printf 'ethernet\n' >> "$events"
                }
                save_network_ip() {
                    printf 'save:%s\n' "$1" >> "$events"
                }
            """) + function + "\nnetwork_init 1\n"
            result = run([
                "bash", "-c", harness,
                "network-test", str(events),
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                events.read_text(encoding="utf-8"),
                "wifi:1\nsave:wlan0\n",
            )

    def test_boot_uses_vendor_mode_when_persisted_mode_is_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            mode_file = directory / "network_mode"
            config_file = directory / "Adventurer5M.json"
            events = directory / "events.log"
            mode_file.write_text("INVALID\n", encoding="utf-8")
            config_file.write_text(
                '{"ethernetStatus": true, "other": 0}\n',
                encoding="utf-8",
            )

            function = extract_shell_function(
                BOOT.read_text(encoding="utf-8"),
                "network_init",
            )
            function = function.replace(
                "/opt/config/mod_data/network_mode",
                shlex.quote(str(mode_file)),
            )
            function = function.replace(
                "/opt/config/Adventurer5M*.json",
                shlex.quote(str(config_file)),
            )

            harness = textwrap.dedent(r"""
                events=$1
                wifi_init() {
                    printf 'wifi:%s\n' "$1" >> "$events"
                }
                ethernet_init() {
                    printf 'ethernet\n' >> "$events"
                }
                save_network_ip() {
                    printf 'save:%s\n' "$1" >> "$events"
                }
            """) + function + "\nnetwork_init 0\n"
            result = run([
                "bash", "-c", harness,
                "network-test", str(events),
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                events.read_text(encoding="utf-8"),
                "ethernet\nsave:eth0\n",
            )

    def test_wifi_logging_policy_uses_screen_only_for_blocking_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            scripts = directory / ".shell"
            boot_dir = scripts / "boot"
            boot_dir.mkdir(parents=True)
            config = directory / "wpa_supplicant.conf"
            config.write_text("network={}\n", encoding="utf-8")
            events = directory / "events.log"
            wifi_marker = directory / "wifi.marker"
            ethernet_marker = directory / "ethernet.marker"

            write_executable(
                boot_dir / "wifi_connect.sh",
                """
                #!/bin/sh
                echo '// Stub Wi-Fi connection'
                exit 0
                """,
            )

            function = extract_shell_function(
                BOOT.read_text(encoding="utf-8"),
                "wifi_init",
            ).replace(
                "/etc/wpa_supplicant.conf",
                shlex.quote(str(config)),
            )

            harness = textwrap.dedent(rf"""
                events=$1
                SCRIPTS={shlex.quote(str(scripts))}
                WIFI_CONNECTED_F={shlex.quote(str(wifi_marker))}
                ETHERNET_CONNECTED_F={shlex.quote(str(ethernet_marker))}
                ip() {{ return 0; }}
                insmod() {{ return 0; }}
                modprobe() {{ return 0; }}
                logged() {{
                    printf 'logged:%s\n' "$*" >> "$events"
                    cat >/dev/null
                }}
                network_deactivate_interface() {{
                    printf 'deactivate:%s\n' "$1" >> "$events"
                }}
                sync() {{ :; }}
                killall() {{ :; }}
                wpa_cli() {{ :; }}
            """) + function + textwrap.dedent(r"""
                wifi_init "$2"
            """)

            blocking = run([
                "bash", "-c", harness,
                "network-test", str(events), "1",
            ])
            self.assertEqual(blocking.returncode, 0, blocking.stderr)
            blocking_events = events.read_text(encoding="utf-8")
            self.assertIn("--no-print --send-to-screen", blocking_events)
            self.assertIn("deactivate:eth0", blocking_events)

            events.unlink()
            quiet = run([
                "bash", "-c", harness,
                "network-test", str(events), "0",
            ])
            self.assertEqual(quiet.returncode, 0, quiet.stderr)
            quiet_events = events.read_text(encoding="utf-8")
            self.assertIn("--no-print", quiet_events)
            self.assertNotIn("--send-to-screen", quiet_events)

    def test_wifi_switch_commits_credentials_and_mode_before_retiring_ethernet(self):
        with tempfile.TemporaryDirectory() as directory:
            sandbox = ZNetworkSandbox(directory)
            sandbox.wpa_config.write_text("old configuration\n", encoding="utf-8")
            with tempfile.NamedTemporaryFile(
                    mode="w", prefix="feather-wifi-", dir="/tmp",
                    delete=False, encoding="utf-8") as credentials_file:
                credentials_file.write("TestNetwork\npassword123\n")
                credentials = pathlib.Path(credentials_file.name)
            self.addCleanup(credentials.unlink, missing_ok=True)

            result = sandbox.invoke(
                "connect-wifi", credentials,
                env=sandbox.environment(TEST_WLAN_IP="192.168.1.44"),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                sandbox.mode_file.read_text(encoding="utf-8"),
                "WIFI\n",
            )
            installed = sandbox.wpa_config.read_text(encoding="utf-8")
            self.assertIn('ssid="TestNetwork"', installed)
            self.assertIn('psk="password123"', installed)
            self.assertIn(
                "deactivate:eth0:mode=WIFI:wpa=present",
                sandbox.event_lines(),
            )

    def test_wifi_failure_does_not_commit_credentials_or_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            sandbox = ZNetworkSandbox(directory)
            sandbox.mode_file.write_text("ETHERNET\n", encoding="utf-8")
            sandbox.wpa_config.write_text("old configuration\n", encoding="utf-8")
            with tempfile.NamedTemporaryFile(
                    mode="w", prefix="feather-wifi-", dir="/tmp",
                    delete=False, encoding="utf-8") as credentials_file:
                credentials_file.write("TestNetwork\npassword123\n")
                credentials = pathlib.Path(credentials_file.name)
            self.addCleanup(credentials.unlink, missing_ok=True)

            result = sandbox.invoke(
                "connect-wifi", credentials,
                env=sandbox.environment(TEST_WIFI_CONNECT_RESULT="1"),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(
                sandbox.mode_file.read_text(encoding="utf-8"),
                "ETHERNET\n",
            )
            self.assertEqual(
                sandbox.wpa_config.read_text(encoding="utf-8"),
                "old configuration\n",
            )
            self.assertNotIn(
                "deactivate:eth0:mode=WIFI:wpa=present",
                sandbox.event_lines(),
            )

    def test_ethernet_failure_preserves_wifi_mode_and_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            sandbox = ZNetworkSandbox(directory)
            sandbox.mode_file.write_text("WIFI\n", encoding="utf-8")

            result = sandbox.invoke(
                "use-ethernet",
                env=sandbox.environment(TEST_DHCP_FAIL="eth0"),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(
                sandbox.mode_file.read_text(encoding="utf-8"),
                "WIFI\n",
            )
            self.assertEqual(sandbox.event_lines(), ["dhcp:eth0"])

    def test_ethernet_success_commits_mode_before_retiring_wifi(self):
        with tempfile.TemporaryDirectory() as directory:
            sandbox = ZNetworkSandbox(directory)
            sandbox.mode_file.write_text("WIFI\n", encoding="utf-8")

            result = sandbox.invoke(
                "use-ethernet",
                env=sandbox.environment(TEST_ETH_IP="192.168.1.50"),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                sandbox.mode_file.read_text(encoding="utf-8"),
                "ETHERNET\n",
            )
            self.assertIn(
                "deactivate:wlan0:mode=ETHERNET:wpa=missing",
                sandbox.event_lines(),
            )

    def test_status_uses_persisted_mode_without_tmp_markers(self):
        with tempfile.TemporaryDirectory() as directory:
            sandbox = ZNetworkSandbox(directory)
            sandbox.mode_file.write_text("WIFI\n", encoding="utf-8")

            result = sandbox.invoke(
                "status",
                env=sandbox.environment(TEST_WLAN_IP="192.168.1.60"),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    "MODE=WIFI",
                    "SSID=TestNetwork",
                    "SIGNAL=-45",
                    "IP=192.168.1.60",
                ],
            )

    def test_switches_do_not_globally_kill_dhcp_clients(self):
        command = re.compile(r"^\s*killall\s+[\"']?udhcpc(?:[\"']?\s|$)")
        for path in NETWORK_SCRIPTS:
            for line_number, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), start=1):
                self.assertIsNone(
                    command.search(line),
                    f"{path}:{line_number} globally kills all DHCP clients",
                )

    def test_boot_network_policy_keeps_feather_async_and_other_modes_blocking(self):
        script = re.sub(
            r"\\\s*\n",
            " ",
            BOOT.read_text(encoding="utf-8"),
        )
        self.assertRegex(
            script,
            r"network_init\s+0\s*</dev/null\s*>/dev/null\s*2>&1\s*&",
        )
        self.assertRegex(
            script,
            r"elif\s+!\s+network_init\s+1\s*;\s*then",
        )
        self.assertRegex(
            script,
            r"zdisplay\.sh\s+stock\s+--skip-reboot",
        )


if __name__ == "__main__":
    unittest.main()
