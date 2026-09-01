"""Behavioral tests for the Recovery UI RFC and follow-up hardening.

Copyright (C) 2026, Alexander K <https://github.com/drA1ex>

This file may be distributed under the terms of the GNU GPLv3 license.
"""

import importlib.util
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]
RECOVERY_PY = ROOT / ".py" / "recovery.py"
ZBACKUP_SH = ROOT / ".shell" / "commands" / "zbackup.sh"
COMMON_SH = ROOT / ".shell" / "common.sh"
RESET_CONFIG_SH = ROOT / ".shell" / "commands" / "zreset_config.sh"
INTEGRATION_NOTES = (ROOT / "openwiki" / "workflows" /
                     "recovery-integration-notes.md")
FONT_METRICS = ROOT / ".py" / "klipper" / "plugins" / "ui" / "font_metrics.json"
SPEC = importlib.util.spec_from_file_location("forge_x_recovery_rfc", RECOVERY_PY)
RECOVERY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RECOVERY)


class FakeTyperSession:
    def __init__(self, taps=()):
        self.taps = list(taps)
        self.frames = []

    def start(self):
        pass

    def close(self):
        pass

    def send(self, commands):
        self.frames.append(tuple(commands))

    def next_tap(self):
        return self.taps.pop(0)


class RecoveryRFCToolsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_main_menu_matches_rfc_and_network_refresh_is_removed(self):
        session = FakeTyperSession(("2:network.back",))
        ui = RECOVERY.RecoveryUI(
            "Adventurer5M", str(self.root / "action"), session)
        ui.render_main()
        ui.network_status = mock.Mock(return_value={
            "state": "CONNECTED", "ssid": "Workshop", "ip": "192.0.2.10",
        })
        ui.network_menu()

        main = "\n".join(session.frames[0])
        for label in (
                "BOOT OPTIONS", "SYSTEM / DIAGNOSTICS", "FIRMWARE / RESTORE",
                "BACKUP / RESET", "NETWORK / SSH", "REBOOT"):
            self.assertIn('"{}"'.format(label), main)
        self.assertNotIn('"START STOCK"', main)
        network = "\n".join(session.frames[1])
        self.assertNotIn("REFRESH STATUS", network)
        self.assertIn("START SSH SHELL", network)

    def test_backup_menu_offers_reset_but_not_backup_restore(self):
        session = FakeTyperSession(("1:backup.back",))
        ui = RECOVERY.RecoveryUI(
            "Adventurer5M", str(self.root / "action"), session)
        ui.backup_reset_menu()
        frame = "\n".join(session.frames[0])

        self.assertIn("CREATE CONFIG BACKUP", frame)
        self.assertIn("RESET CONFIGURATION", frame)
        self.assertIn("UNINSTALL FORGE-X", frame)
        self.assertNotIn("RESTORE CONFIG BACKUP", frame)

    def test_reset_confirmation_uses_only_packaged_fonts(self):
        session = FakeTyperSession(("1:reset.back",))
        ui = RECOVERY.RecoveryUI(
            "Adventurer5M", str(self.root / "action"), session)

        ui.reset_configuration_confirmation()

        frame = "\n".join(session.frames[0])
        referenced = set(re.findall(r'-f "([^"]+)"', frame))
        metrics = json.loads(FONT_METRICS.read_text(encoding="utf-8"))
        packaged = {font["name"] for font in metrics["fonts"]}
        self.assertTrue(referenced)
        self.assertLessEqual(referenced, packaged)

    def test_reset_confirmation_offers_backup_before_destructive_action(self):
        action_file = self.root / "action"
        session = FakeTyperSession((
            "1:backup.reset",
            "2:reset.backup",
            "3:reset.back",
            "4:backup.back",
        ))
        ui = RECOVERY.RecoveryUI(
            "Adventurer5MPro", str(action_file), session)
        ui.create_config_backup_ui = mock.Mock()

        ui.backup_reset_menu()

        confirmation = next(
            "\n".join(frame) for frame in session.frames
            if "Create and download a backup first" in "\n".join(frame))
        self.assertIn("Create and download a backup first", confirmation)
        self.assertIn("CREATE BACKUP", confirmation)
        self.assertIn("RESET", confirmation)
        ui.create_config_backup_ui.assert_called_once_with()
        self.assertFalse(action_file.exists())

    def test_reset_confirmation_hands_off_to_shell_lifecycle(self):
        action_file = self.root / "action"
        session = FakeTyperSession((
            "1:backup.reset",
            "2:reset.accept",
        ))
        ui = RECOVERY.RecoveryUI(
            "Adventurer5M", str(action_file), session)

        with self.assertRaises(SystemExit):
            ui.backup_reset_menu()

        self.assertEqual(action_file.read_text(encoding="utf-8"),
                         "reset-config\n")

    def test_config_reset_restores_model_and_forgex_defaults(self):
        reset_root = self.root / "root"
        config = reset_root / "opt" / "config"
        defaults = config / "mod" / ".cfg" / "default"
        shutil.copytree(ROOT / ".cfg" / "default", defaults)
        (defaults / "printer" / "Adventurer5M" / "printer.cfg").write_text(
            "non-pro default\n", encoding="utf-8")
        (defaults / "printer" / "Adventurer5MPro" / "printer.cfg").write_text(
            "pro default\n", encoding="utf-8")
        mod_data = config / "mod_data"
        mod_data.mkdir()
        targets = {
            "printer.cfg": "printer/Adventurer5MPro/printer.cfg",
            "printer.base.cfg": "printer/Adventurer5MPro/printer.base.cfg",
            "mod_data/backup.params.cfg": "backup.params.cfg",
            "mod_data/camera.conf": "camera.conf",
            "mod_data/ssh.conf": "ssh.conf",
            "mod_data/web.conf": "web.conf",
            "mod_data/user.cfg": "user.cfg",
            "mod_data/user.moonraker.conf": "user.moonraker.conf",
            "mod_data/variables.cfg": "variables.cfg",
        }
        for target in targets:
            path = config / target
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("custom\n", encoding="utf-8")
        (config / "printer.base.cfg.bak").write_text(
            "old calibration\n", encoding="utf-8")

        result = subprocess.run(
            [str(RESET_CONFIG_SH), "Adventurer5MPro"],
            env={**os.environ, "FORGE_X_RESET_ROOT": str(reset_root)},
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)

        self.assertEqual(result.returncode, 0, result.stdout)
        for target, source in targets.items():
            self.assertEqual((config / target).read_bytes(),
                             (defaults / source).read_bytes())
        self.assertFalse((config / "printer.base.cfg.bak").exists())
        self.assertEqual(list(config.glob(".forge-x-reset.*")), [])

    def test_config_reset_validates_every_default_before_writing(self):
        reset_root = self.root / "root"
        config = reset_root / "opt" / "config"
        defaults = config / "mod" / ".cfg" / "default"
        shutil.copytree(ROOT / ".cfg" / "default", defaults)
        (config / "mod_data").mkdir()
        original = config / "printer.cfg"
        original.write_text("keep\n", encoding="utf-8")
        (defaults / "variables.cfg").unlink()

        result = subprocess.run(
            [str(RESET_CONFIG_SH), "Adventurer5M"],
            env={**os.environ, "FORGE_X_RESET_ROOT": str(reset_root)},
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(original.read_text(encoding="utf-8"), "keep\n")
        self.assertEqual(list((config / "mod_data").iterdir()), [])

    def test_recovery_backup_delegates_to_existing_zbackup_archive_logic(self):
        fake = self.root / "zbackup.sh"
        fake.write_text(
            "#!/bin/bash\n"
            "[ \"$1\" = \"--tar-backup-to\" ] || exit 7\n"
            "printf 'forge-x-backup' > \"$2\"\n",
            encoding="utf-8")
        destination_dir = self.root / "recovery"

        with mock.patch.object(RECOVERY, "ZBACKUP", str(fake)):
            archive = pathlib.Path(RECOVERY.create_config_backup(
                str(destination_dir)))

        self.assertEqual(archive.read_bytes(), b"forge-x-backup")
        self.assertEqual(archive.name, "backup.tar.gz")

    def test_zbackup_recovery_backup_reuses_normal_private_user_policy(self):
        script = ZBACKUP_SH.read_text(encoding="utf-8")
        self.assertIn("./mod_data/ssh.conf", script)
        self.assertIn("./mod_data/ssh.key", script)
        self.assertIn("./mod_data/ssh.pub.txt", script)
        self.assertIn("./mod_data/user.moonraker.conf", script)
        self.assertIn('recovery_archive backup TAR_BACKUP_PARAMS "$1"', script)

    def test_zbackup_normal_and_recovery_debug_share_runtime_snapshots(self):
        script = ZBACKUP_SH.read_text(encoding="utf-8")
        self.assertIn('create_debug_archive "$1"', script)
        self.assertIn('recovery_archive debug TAR_DEBUG_PARAMS "$output"', script)
        self.assertIn("/data/logFiles/recovery.log*", script)
        self.assertIn("/data/logFiles/filesystem-usage.txt", script)
        self.assertIn("/data/logFiles/dmesg-recovery.log", script)
        self.assertIn('df -P > "$usage_part"', script)
        self.assertIn('dmesg > "$kernel_part"', script)
        self.assertNotIn("--recovery-debug", script)
        self.assertNotIn("forge-x-recovery-debug", script)

        debug_case = script.split("--tar-debug)", 1)[1].split(";;", 1)[0]
        recovery_debug_case = script.split("--tar-debug-to)", 1)[1].split(";;", 1)[0]
        self.assertIn("create_debug_archive", debug_case)
        self.assertIn("create_debug_archive", recovery_debug_case)
        self.assertNotIn("ensure_backup_params", debug_case)
        self.assertNotIn("ensure_backup_params", recovery_debug_case)

    def test_archive_timeout_removes_partial_output(self):
        fake = self.root / "slow-zbackup.sh"
        fake.write_text(
            "#!/bin/bash\n"
            "out=\"$2\"\n"
            "printf partial > \"$out.part\"\n"
            "trap 'rm -f \"$out.part\"; exit 1' HUP INT TERM\n"
            "sleep 30\n",
            encoding="utf-8")
        destination = self.root / "diagnostics.tar.gz"

        with mock.patch.object(RECOVERY, "ZBACKUP", str(fake)):
            started = time.monotonic()
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                RECOVERY._run_recovery_archive(
                    "--tar-debug-to", str(destination), timeout=0.15)
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 3.0)
        self.assertFalse(pathlib.Path(str(destination) + ".part").exists())

    def test_cleanup_scan_is_bounded_includes_forgex_logs_and_revalidates_delete(self):
        logs = self.root / "logs"
        forge_logs = self.root / "forge-logs"
        downloads = self.root / "downloads"
        archives = self.root / "archives"
        for directory in (logs, forge_logs, downloads, archives):
            directory.mkdir()

        for index in range(120):
            (logs / "log-{:03d}.log".format(index)).write_bytes(
                b"x" * (index + 1))
        forge_log = forge_logs / "forge-x.log"
        forge_log.write_bytes(b"f" * 1000)
        (logs / "link.log").symlink_to(logs / "log-119.log")
        image = downloads / "Adventurer5M-image.tgz"
        image.write_bytes(b"y" * 800)
        generated = archives / "debug_20260101.tar.gz"
        generated.write_bytes(b"z" * 700)
        (archives / "user.cfg").write_text("keep\n", encoding="utf-8")

        real_push = RECOVERY.heapq.heappush
        real_replace = RECOVERY.heapq.heapreplace
        largest_heap = {"size": 0}

        def tracked_push(heap, item):
            real_push(heap, item)
            largest_heap["size"] = max(largest_heap["size"], len(heap))

        def tracked_replace(heap, item):
            result = real_replace(heap, item)
            largest_heap["size"] = max(largest_heap["size"], len(heap))
            return result

        with mock.patch.object(RECOVERY, "CLEANUP_LOG_ROOT", str(logs)), \
                mock.patch.object(
                    RECOVERY, "CLEANUP_FORGE_X_LOG_ROOT", str(forge_logs)), \
                mock.patch.object(RECOVERY, "DOWNLOAD_DIR", str(downloads)), \
                mock.patch.object(
                    RECOVERY, "CLEANUP_ARCHIVE_ROOT", str(archives)), \
                mock.patch.object(
                    RECOVERY.heapq, "heappush", side_effect=tracked_push), \
                mock.patch.object(
                    RECOVERY.heapq, "heapreplace", side_effect=tracked_replace):
            entries = RECOVERY.scan_cleanup_files(limit=10)

            self.assertEqual(len(entries), 10)
            self.assertLessEqual(largest_heap["size"], 10)
            paths = {item["path"] for item in entries}
            self.assertIn(str(forge_log), paths)
            self.assertIn(str(image), paths)
            self.assertIn(str(generated), paths)

            selected = next(item for item in entries if item["path"] == str(forge_log))
            old_log = forge_logs / "forge-x-old.log"
            forge_log.rename(old_log)
            forge_log.write_bytes(b"replacement")
            with self.assertRaisesRegex(RuntimeError, "changed"):
                RECOVERY.delete_cleanup_file(selected)
            self.assertEqual(forge_log.read_bytes(), b"replacement")

        self.assertEqual(
            RECOVERY.CLEANUP_FORGE_X_LOG_ROOT, "/opt/config/mod_data/log")

    def test_filesystem_check_uses_read_only_fsck_for_mounted_targets(self):
        mounts = self.root / "mounts"
        mounts.write_text(
            "/dev/mmcblk0p6 / ext4 rw,relatime 0 0\n"
            "/dev/mmcblk0p7 /data ext4 rw,relatime 0 0\n",
            encoding="utf-8")
        fsck = self.root / "fsck"
        fsck.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fsck.chmod(0o755)
        completed = subprocess.CompletedProcess([], 0, stdout="clean\n", stderr=None)

        with mock.patch.object(RECOVERY, "FSCK", str(fsck)), \
                mock.patch.object(
                    RECOVERY.subprocess, "run", return_value=completed) as run:
            results = RECOVERY.check_filesystems(str(mounts))

        self.assertEqual(len(results), 2)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands, [
            [str(fsck), "-n", "/dev/mmcblk0p6"],
            [str(fsck), "-n", "/dev/mmcblk0p7"],
        ])
        self.assertFalse(any(
            flag in command for command in commands for flag in ("-y", "-a", "-p")))

    def test_filesystem_check_resolves_the_printer_root_device_alias(self):
        mounts = self.root / "mounts"
        mounts.write_text(
            "/dev/root / ext4 rw,relatime 0 0\n"
            "/dev/mmcblk0p7 /data ext4 rw,relatime 0 0\n",
            encoding="utf-8")
        fsck = self.root / "fsck"
        fsck.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fsck.chmod(0o755)
        completed = subprocess.CompletedProcess([], 0, stdout="clean\n", stderr=None)

        with mock.patch.object(RECOVERY, "FSCK", str(fsck)), \
                mock.patch.object(
                    RECOVERY.os.path, "exists",
                    side_effect=lambda path: path == "/dev/mmcblk0p6"), \
                mock.patch.object(
                    RECOVERY.subprocess, "run", return_value=completed) as run:
            RECOVERY.check_filesystems(str(mounts))

        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0], [str(fsck), "-n", "/dev/mmcblk0p6"])

    def test_existing_startup_data_fsck_repair_exception_is_preserved(self):
        common = COMMON_SH.read_text(encoding="utf-8")
        self.assertIn("fsck -y /dev/mmcblk0p7 || true", common)
        self.assertIn("mount /dev/mmcblk0p7 /data", common)

    def test_http_download_exposes_only_exact_file_and_has_no_upload_mode(self):
        artifact = self.root / "diagnostics.tar.gz"
        artifact.write_bytes(b"diagnostics")
        server = RECOVERY.RecoveryFileServer(host="127.0.0.1", port=0)
        try:
            server.start_download(str(artifact))
            url = "http://127.0.0.1:{}/diagnostics.tar.gz".format(server.port)
            with urllib.request.urlopen(url, timeout=3) as response:
                self.assertEqual(response.read(), b"diagnostics")
            with self.assertRaises(urllib.error.HTTPError) as blocked:
                urllib.request.urlopen(
                    "http://127.0.0.1:{}/".format(server.port), timeout=3)
            self.assertEqual(blocked.exception.code, 404)
            upload = urllib.request.Request(
                url, data=b"replacement", method="PUT")
            with self.assertRaises(urllib.error.HTTPError) as rejected:
                urllib.request.urlopen(upload, timeout=3)
            self.assertEqual(rejected.exception.code, 501)
            self.assertEqual(artifact.read_bytes(), b"diagnostics")
        finally:
            server.stop()

    def test_http_stop_closes_stalled_download_without_waiting_for_client(self):
        artifact = self.root / "large-diagnostics.tar.gz"
        with artifact.open("wb") as output:
            output.seek(32 * 1024 * 1024 - 1)
            output.write(b"x")

        server = RECOVERY.RecoveryFileServer(host="127.0.0.1", port=0)
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(2.0)
        try:
            server.start_download(str(artifact))
            client.connect(("127.0.0.1", server.port))
            client.sendall(
                b"GET /large-diagnostics.tar.gz HTTP/1.1\r\n"
                b"Host: localhost\r\nConnection: close\r\n\r\n")
            client.recv(1024)

            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                with server.server.active_requests_lock:
                    if server.server.active_requests:
                        break
                time.sleep(0.01)

            started = time.monotonic()
            server.stop()
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 1.5)
        finally:
            try:
                client.close()
            finally:
                server.stop()

    def test_integration_notes_record_reset_and_fsck_validation(self):
        notes = INTEGRATION_NOTES.read_text(encoding="utf-8")
        self.assertIn("Adventurer 5M", notes)
        self.assertIn("Adventurer 5M Pro", notes)
        self.assertIn("RESET CONFIGURATION", notes)
        self.assertIn("printer.base.cfg.bak", notes)
        self.assertIn("CREATE BACKUP", notes)
        self.assertIn("CHECK FILESYSTEM", notes)
        self.assertIn("filesystem types", notes)
        self.assertIn("fsck -n", notes)
        self.assertIn("fsck -y /dev/mmcblk0p7", notes)


if __name__ == "__main__":
    unittest.main()
