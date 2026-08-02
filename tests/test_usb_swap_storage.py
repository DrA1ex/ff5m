"""Contracts for early USB discovery, boot flags, swap, and preparation."""

import os
import pathlib
import re
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).parents[1]
HELPER = ROOT / ".shell" / "boot" / "usb_storage.sh"
INIT_SWAP = ROOT / ".shell" / "boot" / "init_swap.sh"
INIT_BOOT_FLAG = ROOT / ".shell" / "boot" / "init_boot_flag.sh"
PREPARE_USB = ROOT / ".shell" / "commands" / "zusb.sh"
MOUNT_USB = ROOT / ".shell" / "commands" / "zusb_mount.sh"
BASE_MACROS = ROOT / "macros" / "base.cfg"
SHELL_MACROS = ROOT / "macros" / "shell.cfg"


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
        self.environment = dict(os.environ)
        self.environment.update({
            "USB_STORAGE_SYS_BLOCK_ROOT": str(self.sys_block),
            "USB_STORAGE_DEV_ROOT": str(self.dev),
            "USB_STORAGE_PROC_PARTITIONS": str(self.proc_partitions),
            "USB_STORAGE_PROC_MOUNTS": str(self.proc_mounts),
            "USB_STORAGE_MOUNT_ROOT": str(self.mount_root),
            "USB_STORAGE_LSBLK": str(self.lsblk),
            "USB_STORAGE_UDEVADM": str(self.no_udevadm),
            "USB_SWAP_WAIT_SECONDS": "0",
            "BOOT_FLAG_USB_WAIT_SECONDS": "0",
            "USB_PREPARE_WAIT_SECONDS": "0",
            "USB_PREPARE_PROC_SWAPS": str(self.proc_swaps),
            "USB_BROWSER_PROC_SWAPS": str(self.proc_swaps),
            "USB_BROWSER_WAIT_SECONDS": "0",
            "USB_BROWSER_DATA_ROOT": str(self.root / "data"),
            "USB_BROWSER_CHROOT_ROOT": "",
            "USB_STORAGE_REQUIRE_BLOCK_DEVICES": "0",
            "USB_STORAGE_OPERATION_LOCK": str(self.operation_lock),
            "SWAP_SIZE": "64M",
            "COMMON_SCRIPT": "/dev/null",
            "INIT_SWAP_LIBRARY_ONLY": "1",
            "INIT_BOOT_FLAG_LIBRARY_ONLY": "1",
        })

    def tearDown(self):
        self.temporary.cleanup()

    def _script(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/sh\nset -eu\n" + body, encoding="utf-8")
        path.chmod(0o755)
        return path

    def _run(self, source, body):
        return subprocess.run(
            ["bash", "-c", 'source "$1"\n' + body,
             "usb-storage-test", str(source)],
            env=self.environment, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, check=False)

    def _run_prepare(self, *args):
        return subprocess.run(
            ["bash", str(PREPARE_USB), *args], env=self.environment,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)

    def _run_mount(self, *args):
        return subprocess.run(
            ["bash", str(MOUNT_USB), *args], env=self.environment,
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
            "USB_BROWSER_CHROOT_ROOT": str(chroot_root),
        })

        result = self._run_mount("attach", str(target))

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
            "USB_BROWSER_CHROOT_ROOT": str(chroot_root),
        })

        result = self._run_mount("detach", str(target))

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
        self.environment["USB_STORAGE_REQUIRE_BLOCK_DEVICES"] = "1"

        result = self._run(HELPER, "usb_storage_disks; usb_storage_candidates\n")

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
            umount() { return 0; }
            record_flag() { echo "callback=$1"; }
            search_special_boot_flag_usb record_flag
        ''')

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Found USB storage: %s" % (self.dev / "sdc"), result.stdout)
        self.assertIn("callback=FIRMWARE_IMAGE", result.stdout)

    def test_prepare_prompt_identifies_drive_and_has_two_stage_actions(self):
        result = self._run_prepare("prompt")

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertRegex(result.stdout, r"Found %s: 512[.,]0 MiB" % re.escape(str(self.dev / "sda")))
        self.assertIn("_PREPARE_USB_CONFIRM FORMAT=FAT32", result.stdout)
        self.assertIn("_PREPARE_USB_CONFIRM FORMAT=EXT", result.stdout)
        self.assertLess(result.stdout.index("FORMAT=FAT32"),
                        result.stdout.index("FORMAT=EXT"))
        self.assertRegex(result.stdout, r"\bID=[0-9]+")

        macros = BASE_MACROS.read_text(encoding="utf-8")
        shell = SHELL_MACROS.read_text(encoding="utf-8")
        self.assertIn("[gcode_macro PREPARE_USB]", macros)
        self.assertIn("[gcode_macro _PREPARE_USB_CONFIRM]", macros)
        self.assertIn("_PREPARE_USB_EXECUTE FORMAT={format}", macros)
        self.assertNotIn("TOKEN=", macros)
        self.assertIn("[gcode_shell_command zusb_format]", shell)
        zusb_block = shell.split(
            "[gcode_shell_command zusb]", 1)[1].split(
                "[gcode_shell_command", 1)[0]
        format_block = shell.split(
            "[gcode_shell_command zusb_format]", 1)[1].split(
                "[gcode_shell_command", 1)[0]
        self.assertIn("mode: background", zusb_block)
        self.assertIn("linewise: True", zusb_block)
        self.assertIn("mode: stream", format_block)
        self.assertIn("linewise: True", format_block)
        self.assertIn("action:prompt_begin", macros)

    def test_prepare_rejects_changed_drive_before_erasing(self):
        prompt = self._run_prepare("prompt")
        self.assertEqual(prompt.returncode, 0, prompt.stdout)
        identity = self._prepare_identity(prompt)
        device = "sda"
        self.proc_partitions.write_text(
            self.proc_partitions.read_text(encoding="utf-8").replace(
                "524288 sda", "524289 sda"), encoding="utf-8")
        eraser = self._script("eraser", "echo ERASED\n")
        self.environment["USB_PREPARE_DD"] = str(eraser)

        result = self._run_prepare("format", "EXT", device, identity)

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
        self.environment["USB_PREPARE_DD"] = str(eraser)

        result = self._run_prepare("format", "FAT32", device, identity)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("changed or disappeared", result.stdout)
        self.assertNotIn("ERASED", result.stdout)

    def test_prepare_formats_confirmed_drive_as_fat32(self):
        prompt = self._run_prepare("prompt")
        self.assertEqual(prompt.returncode, 0, prompt.stdout)
        identity = self._prepare_identity(prompt)
        device = "sda"
        self.environment["USB_PREPARE_DD"] = str(
            self._script("dd-mock", "exit 0\n"))
        self.environment["USB_PREPARE_FDISK"] = str(self._script(
            "fdisk-mock", 'echo "fdisk success details"\ntouch "$1"1\n'))
        self.environment["USB_PREPARE_MKDOSFS"] = str(self._script(
            "mkdosfs-mock", 'echo "mkdosfs $*"\n'))

        result = self._run_prepare("format", "FAT32", device, identity)

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
        self.environment["USB_PREPARE_DD"] = str(
            self._script("dd-mock", "exit 0\n"))
        self.environment["USB_PREPARE_FDISK"] = str(self._script(
            "fdisk-failure", 'echo "fdisk diagnostic"\nexit 3\n'))

        result = self._run_prepare("format", "FAT32", "sda", identity)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("fdisk diagnostic", result.stdout)
        self.assertIn("action:prompt_begin", result.stdout)
        self.assertIn("action:prompt_show", result.stdout)
        self.assertFalse(self.operation_lock.exists())

    def test_prepare_failure_emits_completion_dialog(self):
        prompt = self._run_prepare("prompt")
        self.assertEqual(prompt.returncode, 0, prompt.stdout)
        identity = self._prepare_identity(prompt)
        self.environment["USB_PREPARE_DD"] = str(
            self._script("dd-failure", "exit 1\n"))

        result = self._run_prepare("format", "FAT32", "sda", identity)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("action:prompt_begin", result.stdout)
        self.assertIn("action:prompt_show", result.stdout)


if __name__ == "__main__":
    unittest.main()
