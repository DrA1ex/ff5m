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

    def test_cleanup_policy_guards_real_printer_locations(self):
        # The shipped policy must keep the system trees unreachable even when
        # a scan root covers their parent directory.
        pruned = RECOVERY._cleanup_scan_pruned
        self.assertTrue(pruned("/opt/config/mod/plugins/base.py"))
        self.assertTrue(pruned("/data/.mod/.forge-x/root/www"))
        self.assertFalse(pruned("/opt/config/mod_data/debug.tar.gz"))
        self.assertFalse(pruned("/data/gcode/model.gcode"))
        self.assertFalse(pruned("/data/logFiles/recovery.log"))
        self.assertFalse(pruned("/data/forge-x-recovery/image.tgz"))

        allowed = RECOVERY._cleanup_delete_allowed
        self.assertFalse(allowed("/opt"))
        self.assertFalse(allowed("/root"))
        self.assertFalse(allowed("/data"))
        self.assertFalse(allowed("/data/.mod/.forge-x"))
        self.assertFalse(allowed("/opt/config/mod"))
        self.assertFalse(allowed("/opt/config/mod/anything.py"))
        self.assertTrue(allowed("/data/gcode/model.gcode"))
        self.assertTrue(allowed("/data/forge-x-recovery/image.tgz"))
        self.assertTrue(allowed("/data/logFiles/recovery.log"))
        self.assertTrue(allowed("/opt/config/mod_data/debug.tar.gz"))
        self.assertTrue(allowed("/opt/config/settings.json"))

    def test_cleanup_scan_offers_data_and_config_but_not_hidden_or_excluded(self):
        data = self.root / "data"
        config = self.root / "config"
        for directory in (data / "logFiles", data / "gcode",
                          data / "gcode" / ".cache", data / ".hidden",
                          data / ".mod" / ".forge-x", config / "mod",
                          config / "mod_data"):
            directory.mkdir(parents=True)

        gcode = data / "gcode" / "print.gcode"
        gcode.write_bytes(b"x" * 1000)
        (data / "logFiles" / "recovery.log").write_bytes(b"l" * 900)
        (data / "toplevel.gcode").write_bytes(b"t" * 100)
        (data / "gcode" / ".cache" / "big.bin").write_bytes(b"c" * 3000)
        (data / ".hidden" / "secret.gcode").write_bytes(b"h" * 5000)
        (data / ".mod" / ".forge-x" / "chroot.bin").write_bytes(b"x" * 5000)
        (config / "mod" / "plugin.py").write_bytes(b"p" * 4000)
        (config / "mod_data" / "debug.tar.gz").write_bytes(b"d" * 800)
        (config / "mod_data" / "database").write_bytes(b"b" * 50)
        (data / "linked.gcode").symlink_to(gcode)
        (data / "linked-dir").symlink_to(data / "gcode")

        with mock.patch.object(
                RECOVERY, "CLEANUP_SCAN_ROOTS", (str(data), str(config))), \
                mock.patch.object(
                    RECOVERY, "CLEANUP_SEARCH_EXCLUDED",
                    (str(config / "mod"), str(data / ".mod" / ".forge-x"))), \
                mock.patch.object(
                    RECOVERY, "CLEANUP_DELETE_PROTECTED",
                    (str(data), str(data / "logFiles"))):
            entries = RECOVERY.scan_cleanup_files()

        paths = {item["path"] for item in entries}
        self.assertIn(str(gcode), paths)
        self.assertIn(str(data / "logFiles" / "recovery.log"), paths)
        self.assertIn(str(data / "toplevel.gcode"), paths)
        self.assertIn(str(config / "mod_data" / "debug.tar.gz"), paths)

        gcode_folder = next(
            item for item in entries if item["path"] == str(data / "gcode"))
        self.assertTrue(gcode_folder["directory"])
        self.assertEqual(gcode_folder["size"], 1000)
        mod_data_folder = next(
            item for item in entries
            if item["path"] == str(config / "mod_data"))
        self.assertEqual(mod_data_folder["size"], 850)

        # Hidden entries, excluded subtrees, and symlinks are never scanned,
        # so they do not appear as candidates and do not inflate totals.
        self.assertNotIn(str(data / "gcode" / ".cache" / "big.bin"), paths)
        self.assertNotIn(str(data / ".hidden" / "secret.gcode"), paths)
        self.assertNotIn(str(data / ".mod" / ".forge-x" / "chroot.bin"), paths)
        self.assertNotIn(str(config / "mod" / "plugin.py"), paths)
        self.assertNotIn(str(data / "linked.gcode"), paths)
        self.assertNotIn(str(data / "linked-dir"), paths)

        # Scan roots and delete-protected directories are never offered as
        # deletion targets themselves.
        self.assertNotIn(str(data), paths)
        self.assertNotIn(str(config), paths)
        self.assertNotIn(str(data / "logFiles"), paths)

    def test_cleanup_scan_keeps_only_the_largest_candidates(self):
        data = self.root / "data"
        data.mkdir()
        for index in range(120):
            (data / "log-{:03d}.log".format(index)).write_bytes(
                b"x" * (index + 1))

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

        with mock.patch.object(RECOVERY, "CLEANUP_SCAN_ROOTS", (str(data),)), \
                mock.patch.object(RECOVERY, "CLEANUP_SEARCH_EXCLUDED", ()), \
                mock.patch.object(RECOVERY, "CLEANUP_DELETE_PROTECTED", ()), \
                mock.patch.object(
                    RECOVERY.heapq, "heappush", side_effect=tracked_push), \
                mock.patch.object(
                    RECOVERY.heapq, "heapreplace", side_effect=tracked_replace):
            entries = RECOVERY.scan_cleanup_files(limit=10)

        self.assertEqual(len(entries), 10)
        self.assertLessEqual(largest_heap["size"], 10)
        self.assertEqual(
            [item["size"] for item in entries],
            sorted((item["size"] for item in entries), reverse=True))
        self.assertEqual(entries[0]["size"], 120)

    def test_cleanup_delete_removes_files_and_whole_folders_and_refuses_swaps(self):
        data = self.root / "data"
        folder = data / "prints"
        folder.mkdir(parents=True)
        first = folder / "a.gcode"
        second = folder / "b.gcode"
        first.write_bytes(b"a" * 100)
        second.write_bytes(b"b" * 100)

        with mock.patch.object(RECOVERY, "CLEANUP_SCAN_ROOTS", (str(data),)), \
                mock.patch.object(RECOVERY, "CLEANUP_SEARCH_EXCLUDED", ()), \
                mock.patch.object(RECOVERY, "CLEANUP_DELETE_PROTECTED", ()), \
                mock.patch.object(RECOVERY, "read_mounts", return_value=[]):
            by_path = {item["path"]: item for item in RECOVERY.scan_cleanup_files()}

            RECOVERY.delete_cleanup_entry(by_path[str(first)])
            self.assertFalse(first.exists())
            self.assertTrue(second.exists())

            RECOVERY.delete_cleanup_entry(by_path[str(folder)])
            self.assertFalse(folder.exists())
            self.assertTrue(data.exists())

            # A target replaced after the scan is refused, and the replacement
            # content stays untouched.
            folder.mkdir()
            replacement = folder / "new.gcode"
            replacement.write_bytes(b"n" * 10)
            with self.assertRaisesRegex(RuntimeError, "changed"):
                RECOVERY.delete_cleanup_entry(by_path[str(folder)])

        self.assertTrue(replacement.exists())

    def test_cleanup_delete_refuses_disallowed_targets(self):
        data = self.root / "data"
        folder = data / "prints"
        folder.mkdir(parents=True)
        (folder / "a.gcode").write_bytes(b"a" * 10)
        outside = self.root / "elsewhere"
        outside.mkdir()

        entry = {
            "path": "", "root": str(data), "directory": True,
            "size": 1, "dev": 0, "ino": 0,
        }
        with mock.patch.object(
                RECOVERY, "CLEANUP_SCAN_ROOTS", (str(data),)), \
                mock.patch.object(
                    RECOVERY, "CLEANUP_SEARCH_EXCLUDED", (str(data / "mod"),)), \
                mock.patch.object(
                    RECOVERY, "CLEANUP_DELETE_PROTECTED", (str(data),)):
            for path in (str(data), str(data / "mod"),
                         str(data / "mod" / "inner"), str(outside)):
                entry["path"] = path
                with self.assertRaisesRegex(
                        RuntimeError, "not an allowed deletion target"):
                    RECOVERY.delete_cleanup_entry(entry)

        self.assertTrue(folder.exists())
        self.assertTrue(outside.exists())

    def test_cleanup_delete_refuses_folder_containing_active_mount(self):
        data = self.root / "data"
        folder = data / "usb"
        folder.mkdir(parents=True)
        (folder / "file.bin").write_bytes(b"f" * 100)
        stick = folder / "stick"
        stick.mkdir()

        with mock.patch.object(RECOVERY, "CLEANUP_SCAN_ROOTS", (str(data),)), \
                mock.patch.object(RECOVERY, "CLEANUP_SEARCH_EXCLUDED", ()), \
                mock.patch.object(RECOVERY, "CLEANUP_DELETE_PROTECTED", ()), \
                mock.patch.object(
                    RECOVERY, "read_mounts",
                    return_value=[{
                        "source": "/dev/sda1", "target": str(stick),
                        "type": "ext4", "options": ["rw"],
                    }]):
            selected = next(
                item for item in RECOVERY.scan_cleanup_files()
                if item["path"] == str(folder))
            with self.assertRaisesRegex(RuntimeError, "active mount"):
                RECOVERY.delete_cleanup_entry(selected)

        self.assertTrue(folder.exists())
        self.assertTrue((folder / "file.bin").exists())

    def test_large_files_cleanup_deletes_folder_after_irreversible_warning(self):
        data = self.root / "data"
        folder = data / "prints"
        folder.mkdir(parents=True)
        (folder / "print.gcode").write_bytes(b"x" * 500)
        (data / "small.log").write_bytes(b"s" * 10)

        session = FakeTyperSession((
            "2:choose.0",
            "3:confirm.accept",
            "4:message.back",
            "6:choose.back",
        ))
        ui = RECOVERY.RecoveryUI(
            "Adventurer5M", str(self.root / "action"), session)

        with mock.patch.object(RECOVERY, "CLEANUP_SCAN_ROOTS", (str(data),)), \
                mock.patch.object(RECOVERY, "CLEANUP_SEARCH_EXCLUDED", ()), \
                mock.patch.object(RECOVERY, "CLEANUP_DELETE_PROTECTED", ()), \
                mock.patch.object(RECOVERY, "read_mounts", return_value=[]):
            ui.large_files_cleanup()

        listing = "\n".join(session.frames[1])
        self.assertIn("prints/", listing)

        confirmation = next(
            "\n".join(frame) for frame in session.frames
            if "This cannot be undone." in "\n".join(frame))
        self.assertIn("DELETE FOLDER", confirmation)
        self.assertIn(str(folder), confirmation)

        deleted = next(
            "\n".join(frame) for frame in session.frames
            if "Deleted {}.".format(folder) in "\n".join(frame))
        self.assertIn('"DELETED"', deleted)

        self.assertFalse(folder.exists())
        self.assertTrue((data / "small.log").exists())

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
