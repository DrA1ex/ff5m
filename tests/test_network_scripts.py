## Tests for Forge-X network scripts.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).parents[1]
COMMON = ROOT / ".shell" / "network_common.sh"
BOOT = ROOT / ".shell" / "boot" / "boot.sh"
WIFI_CONNECT = ROOT / ".shell" / "boot" / "wifi_connect.sh"
HELPER = ROOT / ".shell" / "commands" / "znetwork.sh"


def function_body(script, name, next_name):
    start = script.index("%s() {" % name)
    end = script.index("%s() {" % next_name, start)
    return script[start:end]


class NetworkScriptsTest(unittest.TestCase):
    def test_shell_scripts_have_valid_syntax(self):
        scripts = [str(COMMON), str(BOOT), str(WIFI_CONNECT), str(HELPER)]
        subprocess.run(["bash", "-n"] + scripts, check=True)
        subprocess.run(["sh", "-n", str(COMMON), str(HELPER)], check=True)

    def test_dns_cleanup_removes_only_inactive_interface_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            resolv = pathlib.Path(directory) / "resolv.conf"
            resolv.write_text(
                "nameserver 1.1.1.1 # wlan0\n"
                "nameserver 192.168.2.1 # eth0\n"
                "search lan # wlan0\n",
                encoding="utf-8")
            subprocess.run(
                ["sh", "-c",
                 'NETWORK_RESOLV_CONF="$1"; . "$2"; '
                 'network_clear_dns wlan0',
                 "network-test", str(resolv), str(COMMON)],
                check=True)
            self.assertEqual(
                resolv.read_text(encoding="utf-8"),
                "nameserver 192.168.2.1 # eth0\n")

    def test_wifi_switch_commits_mode_before_retiring_ethernet(self):
        script = HELPER.read_text(encoding="utf-8")
        body = function_body(script, "connect_wifi", "use_ethernet")
        steps = (
            '"$SCRIPTS/boot/wifi_connect.sh"',
            "update_ip wlan0",
            "save_mode WIFI",
            "network_deactivate_interface eth0",
        )
        positions = [body.index(step) for step in steps]
        self.assertEqual(positions, sorted(positions))

    def test_wifi_config_is_committed_only_after_wpa_and_dhcp(self):
        script = HELPER.read_text(encoding="utf-8")
        body = function_body(script, "connect_wifi", "use_ethernet")
        candidate_connect = body.index(
            '"$SCRIPTS/boot/wifi_connect.sh" "$candidate"')
        address_ready = body.index("update_ip wlan0", candidate_connect)
        persistent_move = body.index('mv -f "$new_config" "$WPA_CONFIG"',
                                     address_ready)
        mode_commit = body.index("save_mode WIFI", persistent_move)
        self.assertLess(candidate_connect, address_ready)
        self.assertLess(address_ready, persistent_move)
        self.assertLess(persistent_move, mode_commit)
        self.assertNotIn('> "$WPA_CONFIG"', body[:candidate_connect])

    def test_ethernet_switch_commits_mode_before_retiring_wifi(self):
        script = HELPER.read_text(encoding="utf-8")
        body = function_body(script, "use_ethernet", "status")
        steps = (
            "network_activate_dhcp eth0 25",
            "update_ip eth0",
            "save_mode ETHERNET",
            "network_deactivate_interface wlan0",
        )
        positions = [body.index(step) for step in steps]
        self.assertEqual(positions, sorted(positions))

    def test_switches_do_not_globally_kill_dhcp_clients(self):
        for path in (BOOT, WIFI_CONNECT, HELPER):
            self.assertNotIn("killall udhcpc", path.read_text(encoding="utf-8"))

    def test_boot_prefers_valid_persisted_mode(self):
        script = BOOT.read_text(encoding="utf-8")
        read_mode = script.index("mod_data/network_mode")
        validate_mode = script.index("WIFI|ETHERNET", read_mode)
        wifi_branch = script.index('NETWORK_MODE" = "WIFI', validate_mode)
        ethernet_branch = script.index('NETWORK_MODE" = "ETHERNET', validate_mode)
        vendor_fallback = script.index("ethernetStatus", wifi_branch)
        self.assertLess(validate_mode, ethernet_branch)
        self.assertLess(validate_mode, wifi_branch)
        self.assertLess(wifi_branch, vendor_fallback)

    def test_status_reports_persisted_mode_even_without_tmp_markers(self):
        script = HELPER.read_text(encoding="utf-8")
        start = script.index("status() {")
        body = script[start:script.index('\ncase "$1" in', start)]
        mode_read = body.index('configured_mode=$(head -n 1 "$MODE_FILE")')
        ethernet = body.index('configured_mode" = "ETHERNET', mode_read)
        wifi = body.index('configured_mode" = "WIFI', ethernet)
        self.assertLess(mode_read, ethernet)
        self.assertLess(ethernet, wifi)
        self.assertGreaterEqual(body.count("echo 'IP='"), 2)


if __name__ == "__main__":
    unittest.main()
