"""Contracts for early USB discovery, boot flags, swap, and preparation."""

import os
import pathlib
import re
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).parents[1]
INIT_MAIN = ROOT / ".shell" / "init-main.sh"
HELPER = ROOT / ".shell" / "boot" / "usb_storage.sh"
INIT_SWAP = ROOT / ".shell" / "boot" / "init_swap.sh"
INIT_BOOT_FLAG = ROOT / ".shell" / "boot" / "init_boot_flag.sh"
BOOT_MODE = ROOT / ".shell" / "boot" / "boot_mode.sh"
INSTALL_IMAGE = ROOT / ".shell" / "boot" / "install-image.sh"
INSTALL_IMAGE_RUNNER = ROOT / ".shell" / "boot" / "install-image-runner.sh"
STOCK_IDENTITY = ROOT / ".shell" / "boot" / "stock_identity.sh"
PREPARE_USB = ROOT / ".shell" / "commands" / "zusb.sh"
MOUNT_USB = ROOT / ".shell" / "commands" / "zusb_mount.sh"


class UsbStorageTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)
        self.sys_block = self.root / "sys" / "block"
        self.dev = self.root / "dev"
        self.mount_root = self.root / "mounts"
        self.proc_partitions = self.root / "partitions"
        self.proc_mounts = self.root / "mount-table"
        self.proc_swaps = self.root / "swaps"
        self.operation_lock = self.root / "usb-operation"
        self.bin = self.root / "bin"
        for path in (self.sys_block, self.dev, self.mount_root, self.bin):
            path.mkdir(parents=True)
        self.proc_mounts.write_text("", encoding="utf-8")
        self.proc_swaps.write_text(
            "Filename Type Size Used Priority\n", encoding="utf-8")

        usb = (self.root / "devices" / "platform" / "usb1" / "1-1"
               / "host0" / "target0" / "block" / "sda")
        usb.mkdir(parents=True)
        (usb / "device").mkdir()
        (usb / "device" / "model").write_text("TEST USB\n", encoding="utf-8")
        (usb.parents[3] / "serial").write_text(
            "TEST-USB-0001\n", encoding="utf-8")
        for number in (1, 2):
            partition = usb / ("sda%d" % number)
            partition.mkdir()
            (partition / "partition").write_text(
                "%d\n" % number, encoding="utf-8")
        (self.sys_block / "sda").symlink_to(usb, target_is_directory=True)
        non_usb = self.root / "devices" / "platform" / "sata" / "block" / "sdb"
        non_usb.mkdir(parents=True)
        (self.sys_block / "sdb").symlink_to(
            non_usb, target_is_directory=True)

        for name in ("sda", "sda1", "sda2", "sdb", "sdb1"):
            (self.dev / name).touch()
        self.proc_partitions.write_text(
            "major minor  #blocks  name\n"
            "   8        0     524288 sda\n"
            "   8        1     131072 sda1\n"
            "   8        2     262144 sda2\n"
            "   8       16    1048576 sdb\n"
            "   8       17    1048000 sdb1\n",
            encoding="utf-8")

        self.lsblk = self._script(
            "lsblk", "case \"$*\" in\n"
            "  *sda1*) echo ext3 ;;\n"
            "  *sda2*) echo ext4 ;;\n"
            "  *) echo ;;\n"
            "esac\n")
        self.no_udevadm = self.root / "missing-udevadm"
        version_file = self.root / "version"
        software_dir = self.root / "software"
        launcher = software_dir / "3.1.5" / "auto_run.sh"
        version_file.write_text("3.1.5\n", encoding="utf-8")
        launcher.parent.mkdir(parents=True)
        launcher.write_text(
            "#!/bin/sh\nMACHINE=Adventurer5M\nPID=0023\n",
            encoding="utf-8")
        self.stock_identity = self.root / "stock_identity.sh"
        stock_identity = STOCK_IDENTITY.read_text(encoding="utf-8")
        stock_identity = stock_identity.replace("/root/version", str(version_file))
        stock_identity = stock_identity.replace(
            "/opt/PROGRAM/software", str(software_dir))
        self.stock_identity.write_text(stock_identity, encoding="utf-8")
        self.helper_replacements = {
            "USB_STORAGE_SYS_BLOCK_ROOT=/sys/block":
                "USB_STORAGE_SYS_BLOCK_ROOT=%s" % self.sys_block,
            "USB_STORAGE_DEV_ROOT=/dev":
                "USB_STORAGE_DEV_ROOT=%s" % self.dev,
            "USB_STORAGE_PROC_PARTITIONS=/proc/partitions":
                "USB_STORAGE_PROC_PARTITIONS=%s" % self.proc_partitions,
            "USB_STORAGE_PROC_MOUNTS=/proc/mounts":
                "USB_STORAGE_PROC_MOUNTS=%s" % self.proc_mounts,
            "USB_STORAGE_MOUNT_ROOT=/tmp":
                "USB_STORAGE_MOUNT_ROOT=%s" % self.mount_root,
            "USB_STORAGE_LSBLK=lsblk":
                "USB_STORAGE_LSBLK=%s" % self.lsblk,
            "USB_STORAGE_UDEVADM=udevadm":
                "USB_STORAGE_UDEVADM=%s" % self.no_udevadm,
            "USB_STORAGE_OPERATION_LOCK=/tmp/forge-x-usb-operation":
                "USB_STORAGE_OPERATION_LOCK=%s" % self.operation_lock,
            "USB_STORAGE_REQUIRE_BLOCK_DEVICES=1":
                "USB_STORAGE_REQUIRE_BLOCK_DEVICES=0",
        }
        self.helper = self._patched_script(
            HELPER, "usb_storage.sh", self.helper_replacements)

        self.init_swap = self._patched_script(
            INIT_SWAP, "init_swap.sh", {
                'source "$SWAP_SCRIPT_DIR/usb_storage.sh"':
                    'source "%s"' % self.helper,
                'SWAP_SIZE="${1-64M}"': "SWAP_SIZE=64M",
                "    wait_seconds=10": "    wait_seconds=0",
                'swap=$($CFG_SCRIPT  $CFG_PATH --get "use_swap" "MMC")':
                    'return 0 2>/dev/null || exit 0\n\n'
                    'swap=$($CFG_SCRIPT  $CFG_PATH --get "use_swap" "MMC")',
            })

        self.prepare_usb = self._patched_script(
            PREPARE_USB, "zusb.sh", {
                'source "$USB_PREPARE_SCRIPT_DIR/../boot/usb_storage.sh"':
                    'source "%s"' % self.helper,
                "USB_PREPARE_PROC_SWAPS=/proc/swaps":
                    "USB_PREPARE_PROC_SWAPS=%s" % self.proc_swaps,
                "    wait_seconds=3": "    wait_seconds=0",
            })
        self.mount_usb = self._patched_script(
            MOUNT_USB, "zusb_mount.sh", {
                'source "$USB_BROWSER_SCRIPT_DIR/../boot/usb_storage.sh"':
                    'source "%s"' % self.helper,
                "USB_BROWSER_PROC_SWAPS=/proc/swaps":
                    "USB_BROWSER_PROC_SWAPS=%s" % self.proc_swaps,
                "USB_BROWSER_WAIT_SECONDS=2": "USB_BROWSER_WAIT_SECONDS=0",
                "USB_BROWSER_DATA_ROOT=/data":
                    "USB_BROWSER_DATA_ROOT=%s" % (self.root / "data"),
                "USB_BROWSER_CHROOT_ROOT=/data/.mod/.forge-x":
                    "USB_BROWSER_CHROOT_ROOT=",
            })

        self.install_image = self._patched_script(
            INSTALL_IMAGE, "install-image.sh", {
                "/opt/config/mod/.shell/boot/stock_identity.sh":
                    str(self.stock_identity),
                "/opt/config/mod/.shell/common.sh": "/dev/null",
                "/opt/config/mod/.shell/boot/install-image-runner.sh":
                    str(INSTALL_IMAGE_RUNNER),
                "FIRMWARE_INSTALL_STAGING_DIR=/data/.firmware":
                    "FIRMWARE_INSTALL_STAGING_DIR=%s" % (self.root / ".firmware"),
                "FIRMWARE_INSTALL_SCREEN_SCRIPT=/opt/config/mod/.shell/screen.sh":
                    "FIRMWARE_INSTALL_SCREEN_SCRIPT=/usr/bin/true",
                "FIRMWARE_INSTALL_TYPER=/opt/config/mod/.bin/exec/typer":
                    "FIRMWARE_INSTALL_TYPER=/usr/bin/true",
                "stop_firmware_parent() {\n":
                    "stop_firmware_parent() {\n    return 0\n",
            })

        self.boot_mode = self._patched_script(
            BOOT_MODE, "boot_mode.sh", {
                "/opt/config/mod/BOOT_FLAG_FAILURE": str(self.root / "boot-failure"),
                "/opt/config/mod/BOOT_FLAG_SKIP": str(self.root / "boot-skip"),
                "/opt/config/mod/BOOT_FLAG_RECOVERY_FAILURE": str(
                    self.root / "recovery-failure"),
                "/tmp/init_finished_f": str(self.root / "init-finished"),
                "/tmp/SKIP_MOD_SOFT": str(self.root / "skip-mod-soft"),
                "/tmp/SKIP_MOD_HARD": str(self.root / "skip-mod-hard"),
                "/tmp/SKIP_MOD": str(self.root / "skip-mod"),
            })
        boot_mode_path = "/opt/config/mod/.shell/boot/boot_mode.sh"
        self.init_boot_flag = self._patched_script(
            INIT_BOOT_FLAG, "init_boot_flag.sh", {
                boot_mode_path: str(self.boot_mode),
                "/opt/config/mod/.shell/common.sh": "/dev/null",
                "/opt/config/mod/.shell/boot/usb_storage.sh": str(self.helper),
                "INSTALL_IMAGE_SCRIPT=/opt/config/mod/.shell/boot/install-image.sh":
                    "INSTALL_IMAGE_SCRIPT=%s" % self.install_image,
                "    wait_seconds=10": "    wait_seconds=0",
            })
        self.init_main = self._patched_script(
            INIT_MAIN, "init-main.sh", {
                boot_mode_path: str(self.boot_mode),
                "/opt/config/mod/.shell/common.sh": "/dev/null",
                "/opt/config/mod/.shell/klipper_overlay.sh": "/dev/null",
            })

        self.special_boot_success = self.root / "special-boot-success.sh"
        self.special_boot_success.write_text(
            "#!/bin/sh\n"
            "if [ -n \"${SPECIAL_BOOT_PID_LOG:-}\" ]; then\n"
            "    printf '%s\\n' \"$FIRMWARE_INSTALL_PARENT_PID\" "
            "> \"$SPECIAL_BOOT_PID_LOG\"\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8")
        self.special_boot_success.chmod(0o755)

        self.environment = dict(os.environ)

    def tearDown(self):
        self.temporary.cleanup()

    def _patched_script(self, source, name, replacements):
        text = source.read_text(encoding="utf-8")
        for old, new in replacements.items():
            self.assertEqual(text.count(old), 1, old)
            text = text.replace(old, new, 1)

        destination = self.root / name
        destination.write_text(text, encoding="utf-8")
        destination.chmod(source.stat().st_mode & 0o777)
        return destination

    def _script(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/sh\nset -eu\n" + body, encoding="utf-8")
        path.chmod(0o755)
        return path

    def _run(self, source, body):
        source = {
            HELPER: self.helper,
            INIT_SWAP: self.init_swap,
            INIT_BOOT_FLAG: self.init_boot_flag,
        }.get(source, source)
        return subprocess.run(
            ["bash", "-c", 'source "$1"\n' + body,
             "usb-storage-test", str(source)],
            env=self.environment, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, check=False)

    def _run_prepare(self, *args, script=None):
        return subprocess.run(
            ["bash", str(script or self.prepare_usb), *args], env=self.environment,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)

    def _run_mount(self, *args, script=None):
        return subprocess.run(
            ["bash", str(script or self.mount_usb), *args], env=self.environment,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)

    def _prepare_identity(self, prompt):
        match = re.search(r"\bID=([0-9]+)", prompt.stdout)
        self.assertIsNotNone(match, prompt.stdout)
        return match.group(1)

    def test_scripts_have_valid_bash_syntax(self):
        subprocess.run(
            ["bash", "-n", str(HELPER), str(INIT_SWAP),
             str(INIT_BOOT_FLAG), str(PREPARE_USB), str(MOUNT_USB)],
            check=True)
        self.assertTrue(os.access(PREPARE_USB, os.X_OK))
        self.assertTrue(os.access(MOUNT_USB, os.X_OK))

    def test_browser_bind_mount_reuses_existing_storage_mount(self):
        existing = self.root / "existing-usb"
        target = self.root / "data" / "USB"
        existing.mkdir()
        target.parent.mkdir()
        self.proc_mounts.write_text(
            "%s %s ext4 rw 0 0\n" % (self.dev / "sda2", existing),
            encoding="utf-8")
        mount_log = self.root / "mount-log"
        self._script("mount", 'echo "$*" >> "$USB_TEST_MOUNT_LOG"\n')
        self.environment.update({
            "PATH": "%s:%s" % (self.bin, self.environment["PATH"]),
            "USB_TEST_MOUNT_LOG": str(mount_log),
        })
        result = self._run_mount("attach", str(target))

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("ATTACHED %s ext4" % (self.dev / "sda2"), result.stdout)
        self.assertEqual(
            mount_log.read_text(encoding="utf-8").strip(),
            "-o bind %s %s" % (existing, target))
        self.assertFalse(self.operation_lock.exists())

    def test_browser_direct_mount_is_writable_for_usb_swap_compatibility(self):
        target = self.root / "data" / "USB"
        target.parent.mkdir()
        mount_log = self.root / "mount-log"
        self._script("mount", 'echo "$*" >> "$USB_TEST_MOUNT_LOG"\n')
        self.environment.update({
            "PATH": "%s:%s" % (self.bin, self.environment["PATH"]),
            "USB_TEST_MOUNT_LOG": str(mount_log),
        })

        result = self._run_mount("attach", str(target))

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("ATTACHED %s ext4" % (self.dev / "sda2"), result.stdout)
        self.assertIn("-o rw,noatime", mount_log.read_text(encoding="utf-8"))

    def test_browser_mirrors_usb_mount_into_forge_x_data(self):
        target = self.root / "data" / "USB"
        target.parent.mkdir()
        chroot_root = self.root / "forge-x"
        (chroot_root / "data").mkdir(parents=True)
        existing = self.root / "existing-usb"
        existing.mkdir()
        self.proc_mounts.write_text(
            "%s %s ext4 rw 0 0\n" % (self.dev / "sda2", existing),
            encoding="utf-8")
        mount_log = self.root / "mount-log"
        self._script("mount", 'echo "$*" >> "$USB_TEST_MOUNT_LOG"\n')
        self.environment.update({
            "PATH": "%s:%s" % (self.bin, self.environment["PATH"]),
            "USB_TEST_MOUNT_LOG": str(mount_log),
        })
        mount_script = self._patched_script(
            self.mount_usb, "zusb_mount-chroot-attach.sh", {
                "USB_BROWSER_CHROOT_ROOT=":
                    "USB_BROWSER_CHROOT_ROOT=%s" % chroot_root,
            })

        result = self._run_mount("attach", str(target), script=mount_script)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(mount_log.read_text(encoding="utf-8").splitlines(), [
            "-o bind %s %s" % (existing, target),
            "-o bind %s %s" % (target, chroot_root / "data" / "USB"),
        ])

    def test_browser_attach_respects_exclusive_usb_operation(self):
        target = self.root / "data" / "USB"
        target.parent.mkdir()
        self.operation_lock.mkdir()

        result = self._run_mount("attach", str(target))

        self.assertEqual(result.returncode, 3, result.stdout)
        self.assertEqual(result.stdout.strip(), "BUSY")

    def test_browser_recovers_lock_left_by_dead_process(self):
        target = self.root / "data" / "USB"
        target.parent.mkdir()
        self.operation_lock.mkdir()
        (self.operation_lock / "owner").write_text(
            "999999999\n", encoding="utf-8")
        mount_log = self.root / "mount-log"
        self._script("mount", 'echo "$*" >> "$USB_TEST_MOUNT_LOG"\n')
        self.environment.update({
            "PATH": "%s:%s" % (self.bin, self.environment["PATH"]),
            "USB_TEST_MOUNT_LOG": str(mount_log),
        })

        result = self._run_mount("attach", str(target))

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("ATTACHED", result.stdout)
        self.assertFalse(self.operation_lock.exists())

    def test_usb_lock_can_only_be_released_by_owner(self):
        result = self._run(HELPER, r'''
            usb_storage_operation_acquire || exit 20
            echo 999999999 > "$USB_STORAGE_OPERATION_LOCK/owner"
            ! usb_storage_operation_release || exit 21
            test -d "$USB_STORAGE_OPERATION_LOCK" || exit 22
        ''')

        self.assertEqual(result.returncode, 0, result.stdout)

    def test_browser_refuses_to_cover_nonempty_directory(self):
        target = self.root / "data" / "USB"
        target.mkdir(parents=True)
        (target / "owned-by-user").touch()

        result = self._run_mount("attach", str(target))

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("Refusing to cover non-empty directory", result.stdout)
        self.assertFalse(self.operation_lock.exists())

    def test_browser_refuses_to_replace_unmanaged_usb_link(self):
        target = self.root / "data" / "USB"
        target.parent.mkdir(parents=True)
        user_directory = self.root / "user-directory"
        user_directory.mkdir()
        target.symlink_to(user_directory, target_is_directory=True)

        result = self._run_mount("attach", str(target))

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("Refusing to replace existing", result.stdout)
        self.assertEqual(os.readlink(target), str(user_directory))

    def test_browser_rejects_mount_point_outside_feather_namespace(self):
        target = self.root / "data" / "usb"

        result = self._run_mount("attach", str(target))

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("Invalid Feather USB mount point", result.stdout)

    def test_browser_detach_only_unmounts_its_own_target(self):
        target = self.root / "data" / "USB"
        target.mkdir(parents=True)
        self.proc_mounts.write_text(
            "%s %s ext4 rw 0 0\n%s %s ext4 rw 0 0\n" % (
                self.dev / "sda2", self.root / "shared-usb",
                self.dev / "sda2", target), encoding="utf-8")
        umount_log = self.root / "umount-log"
        self._script("umount", 'echo "$*" >> "$USB_TEST_UMOUNT_LOG"\n')
        self.environment.update({
            "PATH": "%s:%s" % (self.bin, self.environment["PATH"]),
            "USB_TEST_UMOUNT_LOG": str(umount_log),
        })

        result = self._run_mount("detach", str(target))

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(
            umount_log.read_text(encoding="utf-8").strip(), str(target))

    def test_browser_detaches_chroot_mirror_before_primary_mount(self):
        target = self.root / "data" / "USB"
        target.mkdir(parents=True)
        chroot_root = self.root / "forge-x"
        mirror = chroot_root / "data" / "USB"
        mirror.mkdir(parents=True)
        self.proc_mounts.write_text(
            "%s %s ext4 rw 0 0\n%s %s ext4 rw 0 0\n" % (
                self.dev / "sda2", target,
                self.dev / "sda2", mirror), encoding="utf-8")
        umount_log = self.root / "umount-log"
        self._script("umount", 'echo "$*" >> "$USB_TEST_UMOUNT_LOG"\n')
        self.environment.update({
            "PATH": "%s:%s" % (self.bin, self.environment["PATH"]),
            "USB_TEST_UMOUNT_LOG": str(umount_log),
        })
        mount_script = self._patched_script(
            self.mount_usb, "zusb_mount-chroot-detach.sh", {
                "USB_BROWSER_CHROOT_ROOT=":
                    "USB_BROWSER_CHROOT_ROOT=%s" % chroot_root,
            })

        result = self._run_mount("detach", str(target), script=mount_script)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(umount_log.read_text(encoding="utf-8").splitlines(), [
            str(mirror), str(target)])

    def test_browser_retains_mount_backing_active_swap(self):
        target = self.root / "data" / "USB"
        target.mkdir(parents=True)
        self.proc_mounts.write_text(
            "%s %s ext4 rw 0 0\n" % (self.dev / "sda2", target),
            encoding="utf-8")
        self.proc_swaps.write_text(
            "Filename Type Size Used Priority\n%s/swap file 65532 0 -2\n"
            % target, encoding="utf-8")
        umount_log = self.root / "umount-log"
        self._script("umount", 'echo "$*" >> "$USB_TEST_UMOUNT_LOG"\n')
        self.environment.update({
            "PATH": "%s:%s" % (self.bin, self.environment["PATH"]),
            "USB_TEST_UMOUNT_LOG": str(umount_log),
        })

        result = self._run_mount("detach", str(target))

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("RETAINED active-swap", result.stdout)
        self.assertFalse(umount_log.exists())

    def test_disks_and_candidates_include_only_usb_largest_first(self):
        result = self._run(HELPER, "usb_storage_disks\nusb_storage_candidates\n")

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout.splitlines(), [
            "524288 %s" % (self.dev / "sda"),
            "262144 %s" % (self.dev / "sda2"),
            "131072 %s" % (self.dev / "sda1"),
        ])

    def test_partition_is_not_ready_until_device_node_exists(self):
        (self.dev / "sda2").unlink()
        result = self._run(HELPER, "usb_storage_candidates\n")

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(
            result.stdout.splitlines(),
            ["131072 %s" % (self.dev / "sda1")])

    def test_regular_files_are_not_accepted_as_device_nodes_in_production(self):
        strict_helper = self._patched_script(
            self.helper, "usb_storage-strict.sh", {
                "USB_STORAGE_REQUIRE_BLOCK_DEVICES=0":
                    "USB_STORAGE_REQUIRE_BLOCK_DEVICES=1",
            })

        result = self._run(
            strict_helper, "usb_storage_disks; usb_storage_candidates\n")

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout, "")

    def test_partition_discovery_uses_sysfs_not_device_name_pattern(self):
        disk = (self.root / "devices" / "platform" / "usb2" / "2-1"
                / "host1" / "target1" / "block" / "mmcblk9")
        disk.mkdir(parents=True)
        (disk / "device").mkdir()
        (disk / "device" / "model").write_text(
            "USB READER\n", encoding="utf-8")
        partition = disk / "mmcblk9p1"
        partition.mkdir()
        (partition / "partition").write_text("1\n", encoding="utf-8")
        (self.sys_block / "mmcblk9").symlink_to(
            disk, target_is_directory=True)
        (self.dev / "mmcblk9").touch()
        (self.dev / "mmcblk9p1").touch()
        with self.proc_partitions.open("a", encoding="utf-8") as partitions:
            partitions.write(
                " 179       72    2097152 mmcblk9\n"
                " 179       73    2097000 mmcblk9p1\n")

        result = self._run(HELPER, r'''
            usb_storage_disks
            usb_storage_candidates
            usb_storage_partition_path "$USB_STORAGE_DEV_ROOT/mmcblk9" 1
        ''')

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("2097152 %s" % (self.dev / "mmcblk9"), result.stdout)
        self.assertIn("2097000 %s" % (self.dev / "mmcblk9p1"), result.stdout)
        self.assertEqual(result.stdout.splitlines()[-1], str(self.dev / "mmcblk9p1"))

    def test_logical_partition_is_discovered_from_sysfs(self):
        disk = self.sys_block / "sda"
        partition = disk / "sda5"
        partition.mkdir()
        (partition / "partition").write_text("5\n", encoding="utf-8")
        (self.dev / "sda5").touch()
        with self.proc_partitions.open("a", encoding="utf-8") as partitions:
            partitions.write("   8        5      65536 sda5\n")

        result = self._run(HELPER, "usb_storage_candidates\n")

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("65536 %s" % (self.dev / "sda5"), result.stdout)

    def test_empty_card_slot_and_usb_optical_drive_are_ignored(self):
        for name, device_type, size in (("sdc", "0", 0), ("sr0", "5", 700000)):
            disk = (self.root / "devices" / "platform" / "usb2" / name
                    / "host" / "target" / "block" / name)
            disk.mkdir(parents=True)
            (disk / "device").mkdir()
            (disk / "device" / "type").write_text(
                device_type + "\n", encoding="utf-8")
            (self.sys_block / name).symlink_to(disk, target_is_directory=True)
            (self.dev / name).touch()
            with self.proc_partitions.open("a", encoding="utf-8") as partitions:
                partitions.write("  11        0 %10d %s\n" % (size, name))

        result = self._run(HELPER, "usb_storage_disks\nusb_storage_candidates\n")

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn(str(self.dev / "sdc"), result.stdout)
        self.assertNotIn(str(self.dev / "sr0"), result.stdout)

    def test_whole_disk_filesystem_without_partition_table_is_candidate(self):
        disk = (self.root / "devices" / "platform" / "usb2" / "2-2"
                / "host2" / "target2" / "block" / "sdc")
        disk.mkdir(parents=True)
        (disk / "device").mkdir()
        (disk / "device" / "model").write_text(
            "SUPERFLOPPY\n", encoding="utf-8")
        (self.sys_block / "sdc").symlink_to(disk, target_is_directory=True)
        (self.dev / "sdc").touch()
        with self.proc_partitions.open("a", encoding="utf-8") as partitions:
            partitions.write("   8       32    1048576 sdc\n")

        result = self._run(HELPER, "usb_storage_candidates\n")

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("1048576 %s" % (self.dev / "sdc"), result.stdout)

    def test_wait_observes_partition_node_created_later(self):
        (self.dev / "sda1").unlink()
        (self.dev / "sda2").unlink()
        result = self._run(HELPER, r'''
            sleep() { : > "$USB_STORAGE_DEV_ROOT/sda2"; }
            usb_storage_wait_for_candidates 1
            usb_storage_candidates
        ''')

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(
            result.stdout.splitlines(),
            ["262144 %s" % (self.dev / "sda2")])

    def test_supported_mount_filesystems_are_explicit(self):
        result = self._run(HELPER, r'''
            for filesystem in ext2 ext3 ext4 vfat msdos fat fat32; do
                usb_storage_supports_mount "$filesystem" || exit 10
            done
            for filesystem in fuseblk swap unknown; do
                ! usb_storage_supports_mount "$filesystem" || exit 11
            done
        ''')

        self.assertEqual(result.returncode, 0, result.stdout)

    def test_existing_writable_mount_is_reused_and_not_owned(self):
        stock_mount = self.root / "stock-usb"
        stock_mount.mkdir()
        self.proc_mounts.write_text(
            "%s %s ext4 rw,relatime 0 0\n"
            % (self.dev / "sda1", stock_mount), encoding="utf-8")
        result = self._run(HELPER, r'''
            usb_storage_mount_candidate "$USB_STORAGE_DEV_ROOT/sda1" ext4 test || exit 20
            echo "point=$USB_STORAGE_MOUNT_POINT fs=$USB_STORAGE_MOUNT_FILESYSTEM owned=$USB_STORAGE_MOUNTED_BY_US"
        ''')

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn(
            "point=%s fs=ext4 owned=0" % stock_mount, result.stdout)

    def test_usb_is_mounted_before_swap_creation(self):
        result = self._run(INIT_SWAP, r'''
            mount() { echo "mounted=${@: -2:1}"; return 0; }
            df() { printf 'Filesystem 1024-blocks Used Available Capacity Mounted\nmock 999999 0 999999 0%% /mock\n'; }
            make_swap() { echo "swap-target=$1 fs=$2"; return 0; }
            activate_usb_swap
        ''')

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Waiting up to 0s for USB storage", result.stdout)
        self.assertIn(
            "swap-target=%s/forge-x-usb-swap-sda2/swap fs=ext4"
            % self.mount_root, result.stdout)

    def test_fat32_is_attempted_as_a_real_swap_file(self):
        self.lsblk.write_text("#!/bin/sh\necho vfat\n", encoding="utf-8")
        result = self._run(INIT_SWAP, r'''
            mount() { return 0; }
            df() { printf 'Filesystem 1024-blocks Used Available Capacity Mounted\nmock 999999 0 999999 0%% /mock\n'; }
            make_swap() { echo "swap-target=$1 fs=$2"; return 0; }
            activate_usb_swap
        ''')

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("fs=vfat", result.stdout)
        self.assertNotIn("cannot host", result.stdout)

    def test_fat_swap_file_uses_dd_not_fallocate(self):
        result = self._run(INIT_SWAP, r'''
            dd() { echo "dd $*"; return 0; }
            fallocate() { echo unexpected-fallocate; return 1; }
            allocate_swap_file /mock/swap vfat
        ''')

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("bs=1048576 count=64", result.stdout)
        self.assertNotIn("unexpected-fallocate", result.stdout)

    def test_swap_commands_fall_back_to_busybox_applets(self):
        result = self._run(INIT_SWAP, r'''
            command() { return 1; }
            busybox() { echo "busybox $*"; }
            swap_mkswap /mock/swap
            swap_swapon /mock/swap
            swap_swapoff /mock/swap
        ''')

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout.splitlines(), [
            "busybox mkswap /mock/swap",
            "busybox swapon /mock/swap",
            "busybox swapoff /mock/swap",
        ])

    def test_dedicated_usb_swap_partition_does_not_mount(self):
        self.lsblk.write_text("#!/bin/sh\necho swap\n", encoding="utf-8")
        result = self._run(INIT_SWAP, r'''
            mount() { echo unexpected-mount; return 1; }
            swap_swapoff() { :; }
            swap_swapon() { echo "swapon-target=$1"; return 0; }
            activate_usb_swap
        ''')

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("unexpected-mount", result.stdout)
        self.assertIn("swapon-target=%s" % (self.dev / "sda2"), result.stdout)

    def test_boot_flag_scans_ext_partition_and_releases_temporary_mount(self):
        result = self._run(INIT_BOOT_FLAG, r'''
            mount() { echo "mount-args=$*"; touch "${@: -1}/SKIP_MOD"; return 0; }
            umount() { echo "unmounted=$1"; return 0; }
            record_flag() { echo "callback=$1"; }
            search_special_boot_flag_usb record_flag
        ''')

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("callback=SKIP_MOD", result.stdout)
        self.assertIn("-o ro,noatime", result.stdout)
        self.assertIn("unmounted=%s/forge-x-boot-flag-sda2"
                      % self.mount_root, result.stdout)

    def test_boot_flag_reuses_existing_read_only_stock_mount(self):
        stock_mount = self.root / "stock-usb"
        stock_mount.mkdir()
        (stock_mount / "SKIP_MOD").touch()
        self.proc_mounts.write_text(
            "%s %s ext4 ro,relatime 0 0\n"
            % (self.dev / "sda2", stock_mount), encoding="utf-8")
        result = self._run(INIT_BOOT_FLAG, r'''
            mount() { echo unexpected-mount; return 1; }
            umount() { echo unexpected-unmount; return 1; }
            record_flag() { echo "callback=$1"; }
            search_special_boot_flag_usb record_flag
        ''')

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("callback=SKIP_MOD", result.stdout)
        self.assertNotIn("unexpected-", result.stdout)

    def test_boot_firmware_image_on_whole_disk_filesystem(self):
        (self.sys_block / "sda").unlink()
        disk = (self.root / "devices" / "platform" / "usb2" / "2-2"
                / "host2" / "target2" / "block" / "sdc")
        disk.mkdir(parents=True)
        (disk / "device").mkdir()
        (disk / "device" / "model").write_text(
            "SUPERFLOPPY\n", encoding="utf-8")
        (self.sys_block / "sdc").symlink_to(disk, target_is_directory=True)
        (self.dev / "sdc").touch()
        self.proc_partitions.write_text(
            "major minor  #blocks  name\n"
            "   8       32    1048576 sdc\n", encoding="utf-8")
        self.lsblk.write_text("#!/bin/sh\necho vfat\n", encoding="utf-8")

        result = self._run(INIT_BOOT_FLAG, r'''
            mount() {
                touch "${@: -1}/Adventurer5M-test.tgz"
                return 0
            }
            umount() { echo "unmounted=$1"; return 0; }
            record_flag() {
                echo "callback=$1"
                echo "mount=$2"
                usb_storage_release_mount
                exit 0
            }
            search_special_boot_flag_usb record_flag
        ''')

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("USB %s:" % (self.dev / "sdc"), result.stdout)
        self.assertIn("callback=FIRMWARE_IMAGE", result.stdout)
        self.assertIn(
            "mount=%s/forge-x-boot-flag-sdc" % self.mount_root,
            result.stdout)
        self.assertIn(
            "unmounted=%s/forge-x-boot-flag-sdc" % self.mount_root,
            result.stdout)

    def test_print_firmware_flag_releases_temporary_mount(self):
        result = self._run(INIT_BOOT_FLAG, r'''
            mount() {
                touch "${@: -1}/Adventurer5M-test.tgz"
                return 0
            }
            umount() { echo "unmounted=$1"; return 0; }
            search_special_boot_flag_usb print_special_boot_flag
        ''')

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Flag: FIRMWARE_IMAGE", result.stdout)
        self.assertIn(
            "unmounted=%s/forge-x-boot-flag-sda2" % self.mount_root,
            result.stdout)

    def test_missing_boot_flag_releases_temporary_mount_and_continues(self):
        result = self._run(INIT_BOOT_FLAG, r'''
            mount() { return 0; }
            umount() { echo "unmounted=$1"; return 0; }
            record_flag() { echo unexpected-callback; }
            search_special_boot_flag_usb record_flag
            echo "continued=$?"
        ''')

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("unexpected-callback", result.stdout)
        self.assertIn(
            "unmounted=%s/forge-x-boot-flag-sda2" % self.mount_root,
            result.stdout)
        self.assertIn("continued=1", result.stdout)

    def test_firmware_installer_failure_does_not_resume_normal_boot(self):
        result = self._run(INIT_BOOT_FLAG, r'''
            handle_special_boot_flag FIRMWARE_IMAGE \
                /mock/missing-firmware-directory
        ''')

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Firmware image not found", result.stdout)

    def test_special_boot_success_stops_normal_initialize_pipeline(self):
        scripts = self.root / "scripts"
        scripts.mkdir()
        screen = scripts / "screen.sh"
        screen.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        screen.chmod(0o755)
        parent_pid_log = self.root / "special-boot-parent-pid"

        result = subprocess.run(
            ["bash", "-c", r'''
                expected_parent_pid=$PPID
                export FIRMWARE_INSTALL_PARENT_PID="$expected_parent_pid"
                VERSION_PATCH_F="$2/version-patch"
                BOOT_FAILURE_F="$2/boot-failure"
                SCRIPTS="$2/scripts"
                export SPECIAL_BOOT_PID_LOG="$2/special-boot-parent-pid"
                source "$1"
                SPECIAL_BOOT_SCRIPT="$2/special-boot-success.sh"
                logged() { cat; }
                date() { echo normal-initialize-continued; }
                mount_data_partition() { echo normal-mount-continued; }
                initialize 2>&1 | logged
                status=${PIPESTATUS[0]}
                echo initialize-status=$status
                if [ "$(cat "$SPECIAL_BOOT_PID_LOG")" = "$expected_parent_pid" ]; then
                    echo stock-parent-pid-forwarded
                fi
                echo s00-wrapper-finished
            ''', "s00-lifecycle-test", str(self.init_main), str(self.root)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            env=self.environment,
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("s00-wrapper-finished", result.stdout)
        self.assertIn("stock-parent-pid-forwarded", result.stdout)
        self.assertIn("initialize-status=10", result.stdout)
        self.assertTrue(parent_pid_log.exists())
        self.assertNotIn("normal-initialize-continued", result.stdout)
        self.assertNotIn("normal-mount-continued", result.stdout)

    def test_broken_special_boot_handler_stops_normal_initialize_pipeline(self):
        scripts = self.root / "scripts"
        scripts.mkdir()
        screen = scripts / "screen.sh"
        screen.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        screen.chmod(0o755)

        result = subprocess.run(
            ["bash", "-c", r'''
                VERSION_PATCH_F="$2/version-patch"
                SCRIPTS="$2/scripts"
                source "$1"
                SPECIAL_BOOT_SCRIPT="$2/missing-special-boot-handler"
                date() { echo normal-initialize-continued; }
                mount_data_partition() { echo normal-mount-continued; }
                logged() { cat; }
                initialize 2>&1 | logged
                echo initialize-status=${PIPESTATUS[0]}
            ''', "broken-special-boot-test", str(self.init_main), str(self.root)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False,
            env=self.environment,
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("initialize-status=127", result.stdout)
        self.assertNotIn("normal-initialize-continued", result.stdout)
        self.assertNotIn("normal-mount-continued", result.stdout)

    def test_prepare_prompt_identifies_drive_and_has_two_stage_actions(self):
        result = self._run_prepare("prompt")

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertRegex(result.stdout, r"Found %s: 512[.,]0 MiB" % re.escape(str(self.dev / "sda")))
        self.assertIn("_PREPARE_USB_CONFIRM FORMAT=FAT32", result.stdout)
        self.assertIn("_PREPARE_USB_CONFIRM FORMAT=EXT", result.stdout)
        self.assertLess(result.stdout.index("FORMAT=FAT32"),
                        result.stdout.index("FORMAT=EXT"))
        self.assertRegex(result.stdout, r"\bID=[0-9]+")

    def test_prepare_rejects_changed_drive_before_erasing(self):
        prompt = self._run_prepare("prompt")
        self.assertEqual(prompt.returncode, 0, prompt.stdout)
        identity = self._prepare_identity(prompt)
        device = "sda"
        self.proc_partitions.write_text(
            self.proc_partitions.read_text(encoding="utf-8").replace(
                "524288 sda", "524289 sda"), encoding="utf-8")
        eraser = self._script("eraser", "echo ERASED\n")
        prepare_script = self._patched_script(
            self.prepare_usb, "zusb-changed-drive.sh", {
                "USB_PREPARE_DD=dd": "USB_PREPARE_DD=%s" % eraser,
            })

        result = self._run_prepare(
            "format", "EXT", device, identity, script=prepare_script)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("changed or disappeared", result.stdout)
        self.assertNotIn("ERASED", result.stdout)

    def test_prepare_rejects_replaced_same_size_drive_by_serial(self):
        prompt = self._run_prepare("prompt")
        self.assertEqual(prompt.returncode, 0, prompt.stdout)
        identity = self._prepare_identity(prompt)
        device = "sda"
        serial = self.root / "devices" / "platform" / "usb1" / "1-1" / "serial"
        serial.write_text("TEST-USB-0002\n", encoding="utf-8")
        eraser = self._script("eraser", "echo ERASED\n")
        prepare_script = self._patched_script(
            self.prepare_usb, "zusb-replaced-drive.sh", {
                "USB_PREPARE_DD=dd": "USB_PREPARE_DD=%s" % eraser,
            })

        result = self._run_prepare(
            "format", "FAT32", device, identity, script=prepare_script)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("changed or disappeared", result.stdout)
        self.assertNotIn("ERASED", result.stdout)

    def test_prepare_formats_confirmed_drive_as_fat32(self):
        prompt = self._run_prepare("prompt")
        self.assertEqual(prompt.returncode, 0, prompt.stdout)
        identity = self._prepare_identity(prompt)
        device = "sda"
        dd = self._script("dd-mock", "exit 0\n")
        fdisk = self._script(
            "fdisk-mock", 'echo "fdisk success details"\ntouch "$1"1\n')
        mkdosfs = self._script("mkdosfs-mock", 'echo "mkdosfs $*"\n')
        prepare_script = self._patched_script(
            self.prepare_usb, "zusb-fat32.sh", {
                "USB_PREPARE_DD=dd": "USB_PREPARE_DD=%s" % dd,
                'USB_PREPARE_FDISK="busybox fdisk"':
                    "USB_PREPARE_FDISK=%s" % fdisk,
                'USB_PREPARE_MKDOSFS="busybox mkdosfs"':
                    "USB_PREPARE_MKDOSFS=%s" % mkdosfs,
            })

        result = self._run_prepare(
            "format", "FAT32", device, identity, script=prepare_script)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("mkdosfs -n FORGEX %s/sda1" % self.dev, result.stdout)
        self.assertIn("action:prompt_begin", result.stdout)
        self.assertIn("action:prompt_show", result.stdout)
        self.assertNotIn("fdisk success details", result.stdout)
        self.assertFalse(self.operation_lock.exists())

    def test_prepare_shows_fdisk_details_only_on_failure(self):
        prompt = self._run_prepare("prompt")
        self.assertEqual(prompt.returncode, 0, prompt.stdout)
        identity = self._prepare_identity(prompt)
        dd = self._script("dd-mock", "exit 0\n")
        fdisk = self._script(
            "fdisk-failure", 'echo "fdisk diagnostic"\nexit 3\n')
        prepare_script = self._patched_script(
            self.prepare_usb, "zusb-fdisk-failure.sh", {
                "USB_PREPARE_DD=dd": "USB_PREPARE_DD=%s" % dd,
                'USB_PREPARE_FDISK="busybox fdisk"':
                    "USB_PREPARE_FDISK=%s" % fdisk,
            })

        result = self._run_prepare(
            "format", "FAT32", "sda", identity, script=prepare_script)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("fdisk diagnostic", result.stdout)
        self.assertIn("action:prompt_begin", result.stdout)
        self.assertIn("action:prompt_show", result.stdout)
        self.assertFalse(self.operation_lock.exists())

    def test_prepare_failure_emits_completion_dialog(self):
        prompt = self._run_prepare("prompt")
        self.assertEqual(prompt.returncode, 0, prompt.stdout)
        identity = self._prepare_identity(prompt)
        dd = self._script("dd-failure", "exit 1\n")
        prepare_script = self._patched_script(
            self.prepare_usb, "zusb-dd-failure.sh", {
                "USB_PREPARE_DD=dd": "USB_PREPARE_DD=%s" % dd,
            })

        result = self._run_prepare(
            "format", "FAT32", "sda", identity, script=prepare_script)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("action:prompt_begin", result.stdout)
        self.assertIn("action:prompt_show", result.stdout)


if __name__ == "__main__":
    unittest.main()
