"""Behavioral contracts for firmware selection and one-shot Recovery flashing.

Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
This file may be distributed under the terms of the GNU GPLv3 license.
"""

import hashlib
import io
import os
import pathlib
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / ".py"))
import firmware_catalog  # noqa: E402
import firmware_download  # noqa: E402
import zflash  # noqa: E402


def firmware_archive():
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        content = b"#!/bin/sh\nexit 0\n"
        member = tarfile.TarInfo("flashforge_init.sh")
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))
    return output.getvalue()


class FirmwareCatalogTest(unittest.TestCase):
    def test_service_packages_are_not_offered_as_forgex_releases(self):
        release = {
            "tag_name": "1.2.0",
            "assets": [
                {"name": "Adventurer5M-ForgeX-Feather-off.tgz"},
                {"name": "Adventurer5MPro-ForgeX-uninstall.tgz"},
                {"name": "Adventurer5M-ForgeX-1.2.0.tgz"},
            ],
        }
        self.assertEqual(
            firmware_catalog.forgex_asset(release, "Adventurer5M")[1],
            "Adventurer5M-ForgeX-1.2.0.tgz")
        self.assertEqual(
            firmware_catalog.forgex_asset(release, "Adventurer5MPro")[1],
            "Adventurer5MPro-ForgeX-1.2.0.tgz")
        release["assets"].pop()
        self.assertIsNone(
            firmware_catalog.forgex_asset(release, "Adventurer5M"))
        self.assertIsNone(
            firmware_catalog.forgex_asset(release, "Adventurer5MPro"))


class FirmwareSelectionTest(unittest.TestCase):
    def test_static_lists_match_recovery_without_network(self):
        with mock.patch.object(zflash, "request_json",
                               side_effect=AssertionError("network")), \
                mock.patch.object(zflash, "emit") as emit:
            zflash.show_catalog("RECOVERY", "Adventurer5MPro", "1")

        buttons = [call.args[0] for call in emit.call_args_list
                   if "action:prompt_button " in call.args[0]]
        self.assertEqual(buttons, [
            "// action:prompt_button Recovery dry run|FLASH_FIRMWARE TYPE=RECOVERY VERSION=dry|primary",
            "// action:prompt_button Full file recovery|FLASH_FIRMWARE TYPE=RECOVERY VERSION=full|primary",
        ])

    def archive_release(self):
        def asset(name, size=1):
            return {"name": name, "size": size, "digest": "sha256:" + "A" * 64,
                    "browser_download_url": "https://example.test/" + name}
        return {"assets": [
            asset("Adventurer5M-5.1.9.tgz"), asset("Adventurer5MPro-5.1.9.tgz", 42),
            asset("Adventurer5MPro-10.0.1.tgz"), asset("Adventurer5MPro-3.1.3-Factory.tgz"),
            asset("Adventurer5MPro-debug.tgz"), asset("Adventurer5MPro-5.2.0.tar.xz"),
            asset("notes.txt"),
        ]}

    def test_archive_lists_only_published_images_of_this_model_newest_first(self):
        with mock.patch.object(zflash, "request_json", return_value=self.archive_release()) as request:
            entries = zflash.catalog("FACTORY", "Adventurer5MPro")

        request.assert_called_once_with(firmware_catalog.FORGEX_API + "/tags/factory")
        self.assertEqual([(entry["id"], entry["version"]) for entry in entries],
                         [("10.0.1", "10.0.1"), ("5.1.9", "5.1.9"), ("3.1.3", "3.1.3 Factory")])

    def test_archive_selection_uses_the_listed_asset_and_its_published_metadata(self):
        with mock.patch.object(zflash, "request_json", return_value=self.archive_release()) as request:
            entry = zflash.selected_image("STOCK", "5.1.9", "Adventurer5MPro")

        request.assert_called_once_with(firmware_catalog.FORGEX_API + "/tags/Stock")
        self.assertEqual(entry["name"], "Adventurer5MPro-5.1.9.tgz")
        self.assertEqual(entry["url"], "https://example.test/Adventurer5MPro-5.1.9.tgz")
        self.assertEqual((entry["size"], entry["sha256"]), (42, "a" * 64))

        with mock.patch.object(zflash, "request_json", return_value=self.archive_release()):
            with self.assertRaisesRegex(RuntimeError, "not found"):
                zflash.selected_image("STOCK", "9.9.9", "Adventurer5MPro")

    def test_second_download_is_refused_while_first_holds_the_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = os.path.join(directory, "lock")
            with open(lock_path, "w") as held, \
                    mock.patch.object(zflash, "LOCK", lock_path), \
                    mock.patch.object(zflash, "prepare_download") as prepare:
                zflash.fcntl.flock(held, zflash.fcntl.LOCK_EX)
                with self.assertRaisesRegex(RuntimeError, "in progress"):
                    zflash.main(["Adventurer5M", "FORGEX", "latest"])
                prepare.assert_not_called()

                zflash.fcntl.flock(held, zflash.fcntl.LOCK_UN)
                zflash.main(["Adventurer5M", "FORGEX", "latest"])
                prepare.assert_called_once_with("FORGEX", "latest", "Adventurer5M")


class FirmwareDownloadTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.payload = firmware_archive()
        self.name = "Adventurer5M-ForgeX-2.0.0.tgz"
        self.destination = self.root / self.name
        self.entry = {
            "name": self.name,
            "size": len(self.payload),
            "sha256": hashlib.sha256(self.payload).hexdigest(),
            "url": "https://example.test/image",
        }
        self.directory_patch = mock.patch.object(zflash, "DOWNLOAD_DIR", str(self.root))
        self.directory_patch.start()
        self.addCleanup(self.directory_patch.stop)

    def prepare(self):
        with mock.patch.object(zflash, "selected_image",
                               return_value=self.entry), \
                mock.patch.object(zflash, "emit"):
            zflash.prepare_download("FORGEX", "2.0.0", "Adventurer5M")

    def test_cache_requires_matching_size_and_sha256(self):
        calls = []

        def download(_url, path, _size, _digest):
            calls.append(path)
            pathlib.Path(path).write_bytes(self.payload)

        with mock.patch.object(zflash, "download", side_effect=download):
            self.prepare()
            self.assertEqual(len(calls), 1)
            self.prepare()
            self.assertEqual(len(calls), 1)

            self.destination.write_bytes(b"x")
            self.prepare()
            self.assertEqual(len(calls), 2)

            self.destination.write_bytes(b"x" * len(self.payload))
            self.prepare()
            self.assertEqual(len(calls), 3)

        self.assertEqual(self.destination.read_bytes(), self.payload)

    def test_full_image_checks_are_reported_before_they_run(self):
        progress = mock.Mock()
        checks = []

        def write_image(_url, path, _size, _digest):
            pathlib.Path(path).write_bytes(self.payload)

        def checksum(path):
            checks.append(("checksum", progress.call_args.args))
            return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()

        def archive(_path):
            checks.append(("archive", progress.call_args.args))

        with mock.patch.object(zflash, "download", side_effect=write_image), \
                mock.patch.object(zflash, "emit_progress", progress), \
                mock.patch.object(zflash, "sha256", side_effect=checksum), \
                mock.patch.object(zflash, "verify_archive", side_effect=archive):
            self.prepare()
            self.prepare()

        # A fresh download, then the cached image: each check follows a
        # completed (100%) step that names it instead of a download frame.
        self.assertEqual([kind for kind, _args in checks], ["archive", "checksum", "archive"])
        for _kind, (percent, message) in checks:
            self.assertEqual(percent, 100)
            self.assertIn("Checking", message)

    def test_failed_download_removes_part_and_does_not_publish_image(self):
        def truncated(_url, path, _size, _digest):
            pathlib.Path(path).write_bytes(self.payload[:10])
            raise RuntimeError("Downloaded firmware size is incorrect.")

        with mock.patch.object(zflash, "download", side_effect=truncated):
            with self.assertRaisesRegex(RuntimeError, "size is incorrect"):
                self.prepare()

        self.assertFalse(self.destination.exists())
        self.assertFalse((self.root / (self.name + ".part")).exists())

    def test_stale_cache_and_part_are_removed_before_free_space_check(self):
        self.destination.write_bytes(b"stale")
        partial = self.root / (self.name + ".part")
        partial.write_bytes(b"partial")

        def disk_usage(_path):
            self.assertFalse(self.destination.exists())
            self.assertFalse(partial.exists())
            return mock.Mock(free=len(self.payload) + zflash.RESERVE_BYTES)

        def write_image(_url, path, _size, _digest):
            pathlib.Path(path).write_bytes(self.payload)

        with mock.patch.object(zflash.shutil, "disk_usage",
                               side_effect=disk_usage), \
                mock.patch.object(zflash, "download", side_effect=write_image):
            self.prepare()

        self.assertEqual(self.destination.read_bytes(), self.payload)

    def test_checksum_failure_removes_partial_download(self):
        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        corrupted = b"x" * len(self.payload)
        with mock.patch.object(zflash, "selected_image",
                               return_value=self.entry), \
                mock.patch.object(firmware_download.urllib.request, "urlopen",
                                  return_value=Response(corrupted)), \
                mock.patch.object(zflash, "emit"):
            with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
                zflash.prepare_download("FORGEX", "2.0.0", "Adventurer5M")

        self.assertFalse(self.destination.exists())
        self.assertFalse((self.root / (self.name + ".part")).exists())

    def test_download_checks_size_and_sha256(self):
        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        part = self.root / "image.part"
        with mock.patch.object(firmware_download.urllib.request, "urlopen",
                               return_value=Response(self.payload[:-1])):
            with self.assertRaisesRegex(RuntimeError, "size is incorrect"):
                firmware_download.download("https://example.test/image", part,
                                           len(self.payload))

        with mock.patch.object(firmware_download.urllib.request, "urlopen",
                               return_value=Response(self.payload)):
            with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
                firmware_download.download("https://example.test/image", part,
                                           len(self.payload), "0" * 64)


class FirmwareShellTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.mod = self.root / "mod"
        (self.mod / "bin").mkdir(parents=True)
        (self.mod / "bin" / "python3").touch(mode=0o755)
        self.py = self.root / "py"
        self.py.mkdir()
        (self.py / "zflash.py").touch()
        self.request = self.root / "BOOT_FLASH_IMAGE"
        self.flag = self.root / "BOOT_FLAG_SKIP"
        self.name = "Adventurer5M-ForgeX-2.0.0.tgz"
        common = self.root / "common.sh"
        common.write_text(
            "source '{}'\nMOD='{}'\nPY='{}'\n".format(
                ROOT / ".shell" / "common.sh", self.mod, self.py),
            encoding="utf-8")
        identity = self.root / "identity.sh"
        identity.write_text(
            "load_stock_printer_identity() { FIRMWARE_MACHINE=Adventurer5M; }\n",
            encoding="utf-8")
        source = (ROOT / ".shell" / "commands" / "zflash.sh").read_text(
            encoding="utf-8")
        for old, new in (
                ("/opt/config/mod/.shell/common.sh", str(common)),
                ('"$SCRIPTS/boot/stock_identity.sh"', "'" + str(identity) + "'"),
                ("/opt/config/mod/BOOT_FLASH_IMAGE", str(self.request)),
                ("/opt/config/mod/BOOT_FLAG_SKIP", str(self.flag))):
            self.assertIn(old, source)
            source = source.replace(old, new)
        self.script = self.root / "zflash.sh"
        self.script.write_text(source, encoding="utf-8")
        self.write_command("chroot",
                           '#!/bin/bash\n'
                           'if [ "$TEST_FLASH_RESULT" = fail ]; then\n'
                           '  echo "@@PROGRESS|25|Downloading image"\n'
                           '  echo "!! Downloaded firmware SHA-256 mismatch."\n'
                           '  exit 1\n'
                           'fi\n'
                           'echo "@@PROGRESS|50|Downloading image"\n'
                           'echo "@@IMAGE|Adventurer5M-ForgeX-2.0.0.tgz"\n')
        self.write_command("reboot", '#!/bin/bash\necho rebooted\n')
        self.write_command("sync", '#!/bin/bash\nexit 0\n')

    def write_command(self, name, content):
        path = self.bin / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)

    def run_flash(self, result):
        return subprocess.run(
            ["bash", str(self.script), "FORGEX", "2.0.0"],
            env={**os.environ, "PATH": str(self.bin) + os.pathsep +
                 os.environ["PATH"], "TEST_FLASH_RESULT": result},
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)

    def test_recovery_is_armed_only_after_verified_download(self):
        failed = self.run_flash("fail")
        self.assertNotEqual(failed.returncode, 0, failed.stdout)
        self.assertIn("action:prompt_text Downloaded firmware SHA-256 mismatch.",
                      failed.stdout)
        self.assertIn("[#####---------------] 25%", failed.stdout)
        self.assertNotIn("@@PROGRESS|", failed.stdout)
        self.assertFalse(self.request.exists())
        self.assertFalse(self.flag.exists())

        succeeded = self.run_flash("success")
        self.assertEqual(succeeded.returncode, 0, succeeded.stdout)
        self.assertEqual(self.request.read_text(encoding="utf-8"),
                         self.name + "\n")
        self.assertTrue(self.flag.exists())
        self.assertIn("rebooted", succeeded.stdout)

    def test_firmware_request_does_not_depend_on_tuning(self):
        result = self.run_flash("success")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.request.read_text(), self.name + "\n")
        self.assertTrue(self.flag.exists())
        self.assertIn("rebooted", result.stdout)

    def test_unpersisted_request_arms_nothing_and_does_not_reboot(self):
        self.write_command("sync", '#!/bin/bash\nexit 1\n')

        result = self.run_flash("success")

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("Cannot request firmware installation", result.stdout)
        self.assertNotIn("rebooted", result.stdout)
        self.assertFalse(self.request.exists())
        self.assertFalse(self.flag.exists())


class RecoveryRequestTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.images = self.root / "images"
        self.images.mkdir()
        self.request = self.root / "BOOT_FLASH_IMAGE"
        source = (ROOT / ".shell" / "boot" / "recovery.sh").read_text(
            encoding="utf-8")
        for old, new in (
                ("/opt/config/mod/.shell/boot/boot_mode.sh", "/dev/null"),
                ("/opt/config/mod/.shell/common.sh", "/dev/null"),
                ("/opt/config/mod/.shell/boot/stock_identity.sh", "/dev/null"),
                ("/opt/config/mod/BOOT_FLASH_IMAGE", str(self.request)),
                ("/data/forge-x-recovery/", str(self.images) + "/")):
            self.assertIn(old, source)
            source = source.replace(old, new)
        self.script = self.root / "recovery.sh"
        self.script.write_text(source, encoding="utf-8")

    def run_recovery(self, *, fail_early=False):
        command = """
source "$1"
trap - EXIT HUP INT TERM
RECOVERY_ACTION="$2/action"
RUN_ROOT="$2"
FIRMWARE_MACHINE=Adventurer5M
RECOVERY_PYTHON=/bin/true
find_vendor_curl() { return 1; }
find_vendor_cacert() { return 1; }
load_stock_printer_identity() { %s; }
mount_recovery_devpts() { :; }
mount_recovery_debugfs() { :; }
mount_recovery_chroot() { :; }
start_recovery_touch() { :; }
start_recovery_netd() { :; }
start_recovery_clock() { :; }
run_recovery_ui() {
    printf '%%s\\n' "$RECOVERY_NOTICE" > "$RUN_ROOT/notice"
    echo stock-soft > "$RECOVERY_ACTION"
}
forge_x_publish_stock_mode() { :; }
stop_recovery_ssh() { echo stop >> "$RUN_ROOT/lifecycle"; }
cleanup_recovery_runtime() { echo cleanup >> "$RUN_ROOT/lifecycle"; }
clear_recovery_screen() { echo clear >> "$RUN_ROOT/lifecycle"; }
sync() { :; }
run_firmware_installer() { echo "$1" > "$RUN_ROOT/installed"; }
run_recovery_lifecycle
""" % ("return 1" if fail_early else ":")
        return subprocess.run(
            ["bash", "-c", command, "recovery-request-test",
             str(self.script), str(self.root)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)

    def test_request_is_consumed_even_when_preparation_fails(self):
        self.request.write_text("Adventurer5M-ForgeX-2.0.0.tgz\n",
                                encoding="utf-8")
        result = self.run_recovery(fail_early=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.request.exists())
        self.assertFalse((self.root / "installed").exists())

    def test_valid_missing_and_unsafe_requests(self):
        name = "Adventurer5M-ForgeX-2.0.0.tgz"
        image = self.images / name
        image.touch()
        self.request.write_text(name + "\n", encoding="utf-8")
        result = self.run_recovery()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual((self.root / "installed").read_text(encoding="utf-8"),
                         str(image) + "\n")
        self.assertFalse(self.request.exists())

        (self.root / "installed").unlink()
        image.unlink()
        self.request.write_text(name + "\n", encoding="utf-8")
        result = self.run_recovery()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("missing or unsafe",
                      (self.root / "notice").read_text(encoding="utf-8"))
        self.assertFalse((self.root / "installed").exists())

        self.request.write_text("Adventurer5M-../x.tgz\n", encoding="utf-8")
        result = self.run_recovery()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Invalid firmware request",
                      (self.root / "notice").read_text(encoding="utf-8"))
        self.assertFalse((self.root / "installed").exists())


if __name__ == "__main__":
    unittest.main()
