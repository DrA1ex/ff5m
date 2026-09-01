## Tests for Forge-X network scripts.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).parents[1]
COMMON = ROOT / ".shell" / "common.sh"
BOOT = ROOT / ".shell" / "boot" / "boot.sh"
ETHERNET_MAC_HELPER = ROOT / ".shell" / "commands" / "zeth0_mac.sh"
DISPLAY = ROOT / ".shell" / "commands" / "zdisplay.sh"
BACKUP = ROOT / ".shell" / "commands" / "zbackup.sh"
SYNC_REMOTE = ROOT / "sync_remote.sh"
INIT = ROOT / ".shell" / "S00init"
UNINSTALL = ROOT / ".shell" / "uninstall.sh"


def run(command, *, env=None):
    return subprocess.run(
        command,
        env=env,
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def patched_ethernet_helper(directory, sys_class_net, interfaces):
    source = ETHERNET_MAC_HELPER.read_text(encoding="utf-8")
    replacements = {
        "SYS_CLASS_NET=/sys/class/net": "SYS_CLASS_NET=%s" % sys_class_net,
        "INTERFACES_FILE=/etc/network/interfaces":
            "INTERFACES_FILE=%s" % interfaces,
    }
    for old, new in replacements.items():
        if source.count(old) != 1:
            raise AssertionError("expected one %r in zeth0_mac.sh" % old)
        source = source.replace(old, new, 1)

    script = pathlib.Path(directory) / "zeth0_mac.sh"
    script.write_text(source, encoding="utf-8")
    script.chmod(ETHERNET_MAC_HELPER.stat().st_mode & 0o777)
    return script


class NetworkScriptsTest(unittest.TestCase):
    def test_common_script_preserves_the_shell_command_builtin(self):
        with tempfile.TemporaryDirectory() as directory:
            netd = pathlib.Path(directory) / "netd"
            netd.touch()

            env = os.environ.copy()
            env["PATH"] = f"{directory}:{env['PATH']}"
            result = run([
                "bash", "-c", 'source "$1" && command -v netd',
                "bash", str(COMMON),
            ], env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), str(netd))

    def test_active_shell_scripts_have_valid_syntax(self):
        for script in (BOOT, ETHERNET_MAC_HELPER, DISPLAY, BACKUP, SYNC_REMOTE,
                       INIT, UNINSTALL):
            subprocess.run(["bash", "-n", str(script)], check=True)
        subprocess.run(["sh", "-n", str(ETHERNET_MAC_HELPER)], check=True)

    def test_ethernet_mac_helper_adds_a_complete_missing_stanza(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            sys_class_net = directory / "sys" / "class" / "net"
            (sys_class_net / "eth0").mkdir(parents=True)
            (sys_class_net / "eth0" / "address").write_text(
                "02:11:22:33:44:55\n", encoding="utf-8")
            interfaces = directory / "interfaces"
            interfaces.write_text(
                "auto lo\niface lo inet loopback\n", encoding="utf-8")

            helper = patched_ethernet_helper(
                directory, sys_class_net, interfaces)
            result = run(["sh", str(helper)])

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                interfaces.read_text(encoding="utf-8"),
                "auto lo\niface lo inet loopback\n\n"
                "auto eth0\niface eth0 inet dhcp\n"
                "    hwaddress ether 02:11:22:33:44:55\n",
            )
            self.assertEqual(
                len(list(directory.glob("interfaces.backup.*"))), 1)

    def test_ethernet_mac_helper_inserts_only_in_the_target_stanza(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            sys_class_net = directory / "sys" / "class" / "net"
            (sys_class_net / "eth0").mkdir(parents=True)
            (sys_class_net / "eth0" / "address").write_text(
                "02:aa:bb:cc:dd:ee\n", encoding="utf-8")
            interfaces = directory / "interfaces"
            interfaces.write_text(
                "iface wlan0 inet dhcp\n"
                "    hwaddress ether 02:00:00:00:00:01\n"
                "iface eth0 inet dhcp\n"
                "    metric 10\n"
                "iface usb0 inet static\n",
                encoding="utf-8",
            )

            helper = patched_ethernet_helper(
                directory, sys_class_net, interfaces)
            result = run(["sh", str(helper), "eth0"])

            self.assertEqual(result.returncode, 0, result.stderr)
            material = interfaces.read_text(encoding="utf-8")
            self.assertIn(
                "iface eth0 inet dhcp\n"
                "    hwaddress ether 02:aa:bb:cc:dd:ee\n"
                "    metric 10\n",
                material,
            )
            self.assertEqual(material.count("hwaddress ether"), 2)

    def test_ethernet_mac_helper_is_idempotent_when_mac_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            sys_class_net = directory / "sys" / "class" / "net"
            (sys_class_net / "eth0").mkdir(parents=True)
            (sys_class_net / "eth0" / "address").write_text(
                "02:11:22:33:44:55\n", encoding="utf-8")
            interfaces = directory / "interfaces"
            original = (
                "iface eth0 inet dhcp\n"
                "    hwaddress ether 02:de:ad:be:ef:00\n"
            )
            interfaces.write_text(original, encoding="utf-8")

            helper = patched_ethernet_helper(
                directory, sys_class_net, interfaces)
            result = run(["sh", str(helper)])

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(interfaces.read_text(encoding="utf-8"), original)
            self.assertEqual(list(directory.glob("interfaces.backup.*")), [])

if __name__ == "__main__":
    unittest.main()
