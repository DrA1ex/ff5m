"""The real profile utility against fake sysfs, debugfs, kernel journal and boot files.

Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
This file may be distributed under the terms of the GNU GPLv3 license.
"""

import hashlib
import json
import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).parents[1]
PROFILECTL = ROOT / ".bin/tools/overclocking/bin/t113-profilectl"

HS200_IOS = "clock:\t\t25000000 Hz\ntiming spec:\t9 (mmc HS200)\nsignal voltage:\t1 (1.80 V)\n"
LEGACY_IOS = "clock:\t\t25000000 Hz\ntiming spec:\t1 (mmc high-speed)\nsignal voltage:\t0 (3.30 V)\n"


def cid(model, serial="a1b2c3d4", date="5a", crc="ff"):
    """Compose a sysfs CID: MID OEM PNM PRV, then specimen serial, date and CRC."""
    return model.replace(":", "") + serial + date + crc


def write_manifest(path, manifest):
    fields = []
    for key, value in manifest.items():
        encoded = json.dumps(value, separators=(',', ':')) if not isinstance(value, list) else (
            "[\n" + ",\n".join(json.dumps(row, separators=(',', ':')) for row in value) + "\n]")
        fields.append(json.dumps(key) + ":" + encoded)
    path.write_text("{\n" + ",\n".join(fields) + "\n}\n")


class ProfilectlHardwareTest(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        tools = self.root / "bin"
        tools.mkdir()
        (tools / "fw_printenv").write_text("#!/bin/sh\nexit 1\n")
        (tools / "fw_printenv").chmod(0o755)
        self.path = str(tools) + os.pathsep + os.environ.get("PATH", "")

    def detect(self, cid_text=None, ios=None):
        environment = dict(os.environ, PATH=self.path, T113_STATE_DIR=str(self.root / "state"),
                           T113_CID_FILE=str(self.root / "cid"), T113_IOS_FILE=str(self.root / "ios"))
        if (self.root / "manifest.json").exists():
            environment["T113_MANIFEST"] = str(self.root / "manifest.json")
        if cid_text is not None:
            (self.root / "cid").write_text(cid_text + "\n")
        if ios is not None:
            (self.root / "ios").write_text(ios)
        result = subprocess.run([str(PROFILECTL), "detect"], env=environment, capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

    def manifest(self, *restrictions):
        source = PROFILECTL.parent.parent / "manifest-v2.json"
        manifest = json.loads(source.read_text())
        manifest["hardware"].extend(restrictions)
        write_manifest(self.root / "manifest.json", manifest)

    def frequencies(self):
        result = subprocess.run([str(PROFILECTL), "list", "emmc"],
                                env=dict(os.environ, PATH=self.path, T113_CID_FILE=str(self.root / "cid"),
                                         T113_IOS_FILE=str(self.root / "ios"),
                                         T113_MANIFEST=str(self.root / "manifest.json") if
                                         (self.root / "manifest.json").exists() else
                                         str(PROFILECTL.parent.parent / "manifest-v2.json")),
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return [int(line.split()[0]) for line in result.stdout.splitlines()]

    def test_mode_defines_frequencies_for_any_model_revision_or_missing_cid(self):
        for model in ("15:0100:485332303047:00", "aa:0001:414243444546:02", None):
            for ios, family, frequencies in ((HS200_IOS, "hs2001v8", [25, 50, 100, 150]),
                                              (LEGACY_IOS, "legacy3v3", [25, 50])):
                with self.subTest(model=model, family=family):
                    if (self.root / "cid").exists():
                        (self.root / "cid").unlink()
                    detected = self.detect(cid(model) if model else None, ios)
                    self.assertEqual((detected["hardware"], detected["family"]), ("supported", family))
                    self.assertEqual(self.frequencies(), frequencies)

    def test_invalid_cid_does_not_prevent_mode_detection(self):
        detected = self.detect("invalid", HS200_IOS)
        self.assertEqual(detected["hardware"], "supported")
        self.assertEqual(detected["cid"], "unknown")
        self.assertEqual(self.frequencies(), [25, 50, 100, 150])

    def test_matching_optional_records_intersect_without_changing_detected_mode(self):
        model = "aa:0001:414243444546:02"
        self.manifest(dict(kind="hardware_family", family_id="hs2001v8", models=model,
                           frequencies_mhz="25,50,100", runtime="HS200,1.8V"),
                      dict(kind="hardware_family", family_id="hs2001v8", models=model,
                           frequencies_mhz="50,150", runtime="HS200,1.8V"))
        self.detect(cid(model), HS200_IOS)
        self.assertEqual(self.frequencies(), [50])
        self.detect(cid("aa:0001:414243444546:03"), HS200_IOS)
        self.assertEqual(self.frequencies(), [25, 50, 100, 150])
        self.detect(cid(model), LEGACY_IOS)
        self.assertEqual(self.frequencies(), [25, 50])

    def test_empty_intersection_offers_no_frequencies(self):
        model = "aa:0001:414243444546:02"
        self.manifest(*(dict(kind="hardware_family", family_id="hs2001v8", models=model,
                             frequencies_mhz=value, runtime="HS200,1.8V") for value in ("25", "50")))
        detected = self.detect(cid(model), HS200_IOS)
        self.assertEqual((detected["hardware"], detected["family"]), ("unsupported", "hs2001v8"))
        self.assertEqual(self.frequencies(), [])

    def test_identity_never_substitutes_for_missing_or_unsupported_mode(self):
        for ios in ("", HS200_IOS.replace("1 (1.80 V)", "0 (3.30 V)"),
                    HS200_IOS.replace("9 (mmc HS200)", "2 (unsupported)"),
                    HS200_IOS + "timing spec:\t1 (conflicting observation)\n"):
            with self.subTest(ios=ios):
                detected = self.detect(cid("15:0100:485332303047:00"), ios)
                self.assertEqual((detected["hardware"], detected["family"]), ("unsupported", "unknown"))


class ProfilectlBootEnvironmentTest(unittest.TestCase):
    def setUp(self):
        self.root = root = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        (root / 'bin').mkdir()
        (root / 'boot').mkdir()
        tool = root / 'bin/fw_printenv'
        tool.write_text('#!/bin/sh\ncat "$T113_FAKE_ENV_DB"\n')
        tool.chmod(0o755)
        bundle = PROFILECTL.parent.parent
        (root / 'boot/sunxi.dtb').write_bytes((bundle / 'profiles/source/sunxi-stock.dtb').read_bytes())
        (root / 'boot/uImage').write_bytes(b'fixture kernel')
        self.stock_environment = ('bootcmd=run setargs_mmc boot_dsp0 boot_uImage\n' +
                                  (bundle / 'profiles/source/stock-uboot.env').read_text())
        self.environment = dict(os.environ, PATH=str(root / 'bin') + os.pathsep + os.environ.get('PATH', ''),
                                T113_FAKE_ENV_DB=str(root / 'environment'), T113_TEST_MODE='1',
                                T113_BOOT_DIR=str(root / 'boot'), T113_STATE_DIR=str(root / 'state'),
                                T113_CID_FILE=str(root / 'cid'), T113_IOS_FILE=str(root / 'ios'),
                                T113_BOOT_ID_FILE=str(root / 'boot-id'), T113_DT_ROOT=str(root / 'dt'))

    def test_optional_legacy_override_defaults_only_when_absent(self):
        (self.root / 'environment').write_text(self.stock_environment)
        state = self.root / 'state'
        state.mkdir()
        common = ('format=2\ncpu_profile=stock\ncpu_hz=1008000000\nemmc_mhz=150\nfamily=hs2001v8\n'
                  'slot=FORGE_X_A.DTB\ndtb_sha256=' + 'a' * 64 + '\n')
        for kind, metadata in (('current', common + 'phase=current\n'),
                               ('candidate', common + 'phase=armed\naxis=emmc\nvalue=150\n'
                                'armed_boot_id=boot-a\nfallback_bootcmd=run setargs_mmc boot_dsp0 boot_uImage\n')):
            for value, valid in ((None, 'yes'), ('0', 'yes'), ('1', 'yes'), ('', 'no'), ('2', 'no')):
                with self.subTest(kind=kind, override=value):
                    source = metadata + ('' if value is None else 'untested_emmc_override=' + value + '\n')
                    path = state / kind
                    path.write_text(source)
                    result = subprocess.run([str(PROFILECTL), 'snapshot'], env=self.environment,
                                            capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    report = dict(line.split('=', 1) for line in result.stdout.splitlines())
                    self.assertEqual(report[kind + '_valid'], valid)
                    self.assertEqual(path.read_text(), source)
                    path.unlink()

    def test_doctor_releases_its_mount_and_preserves_a_borrowed_mount_on_success_or_failure(self):
        mounts = self.root / 'mounts'
        log = self.root / 'mount.log'
        wrappers = ROOT / 'tests/fixtures/profilectl/bin'
        environment = dict(self.environment, T113_TEST_MODE='0', T113_MOUNTS_FILE=str(mounts),
                           T113_DEV_BOOT='/dev/fake-boot', T113_FAKE_MOUNT_LOG=str(log),
                           T113_MOUNT_CMD=str(wrappers / 'mount'), T113_UMOUNT_CMD=str(wrappers / 'umount'))
        for borrowed in (False, True):
            for failed in (False, True):
                with self.subTest(borrowed=borrowed, failed=failed):
                    initial = ('/dev/fake-boot {} vfat ro,iocharset=cp936 0 0\n'.format(self.root / 'boot')
                               if borrowed else '')
                    mounts.write_text(initial)
                    log.write_text('')
                    # A missing fixture environment makes fw_printenv fail after the mount.
                    source = self.root / 'environment'
                    if failed:
                        source.unlink(missing_ok=True)
                    else:
                        source.write_text(self.stock_environment)
                    result = subprocess.run([str(PROFILECTL), 'doctor'], env=environment,
                                            capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode == 0, not failed, result.stderr)
                    self.assertEqual(mounts.read_text(), initial)
                    if borrowed:
                        self.assertEqual(log.read_text(), '')
                    else:
                        self.assertEqual(len(log.read_text().splitlines()), 2)
                        self.assertIn('umount ', log.read_text())

    def test_optional_stock_helpers_are_opaque_and_never_created_by_inspection(self):
        for extra in ('', 'boot_normal=\nboot_recovery=\n',
                      'boot_normal=run absent_helper;fatload mmc 0:1 0x43000000 ../VENDOR.BIN\n'
                      'boot_recovery=run boot_recovery;setenv bootcmd opaque-vendor-flow\n'):
            with self.subTest(extra=extra):
                source = self.stock_environment + extra
                path = self.root / 'environment'
                path.write_text(source)
                result = subprocess.run([str(PROFILECTL), 'snapshot'], env=self.environment,
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                report = dict(line.split('=', 1) for line in result.stdout.splitlines())
                self.assertEqual(report['boot_state'], 'clean-stock')
                for key in ('unknown_boot_variables', 'missing_boot_dependencies', 'unsafe_boot_paths'):
                    self.assertEqual(report[key], '')
                self.assertEqual(report['cleanup_required'], 'no')
                self.assertEqual(path.read_text(), source)

    def test_doctor_keeps_boot_commands_and_redacts_named_private_values(self):
        (self.root / 'environment').write_text(
            self.stock_environment.replace('boot_uImage\n', 'boot_uImage custom_flow\n', 1) +
            "custom_flow=run custom_net;setenv password 'private fixture secret'\n"
            'custom_net=bootm 43000000 machine=AD5M serial#=fixture-serial-number\n'
            'custom_ids=run custom_flow wifi_mac=fixture-wifi-address\n')
        result = subprocess.run([str(PROFILECTL), 'doctor'], env=self.environment,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        for retained in ('run setargs_mmc boot_dsp0 boot_uImage custom_flow', 'run custom_net',
                         'bootm 43000000', 'machine=AD5M'):
            self.assertIn(retained, result.stdout)
        for private in ('private fixture secret', 'fixture-serial-number', 'fixture-wifi-address'):
            self.assertNotIn(private, result.stdout)

    def test_unsupported_emmc_refuses_only_the_trials_it_cannot_build(self):
        root = self.root
        for name in ('fgx-fdt', 'fgx-lock'):
            (root / 'bin' / name).write_text('#!/bin/sh\nexit 0\n')
            (root / 'bin' / name).chmod(0o755)
        (root / 'environment').write_text(self.stock_environment)
        (root / 'cpu').write_text('1008000\n')
        (root / 'boot-id').write_text('boot-a\n')
        model = 'aa:0001:414243444546:02'
        (root / 'cid').write_text(cid(model) + '\n')
        manifest = json.loads((PROFILECTL.parent.parent / 'manifest-v2.json').read_text())
        manifest['hardware'].extend(dict(kind='hardware_family', family_id='hs2001v8', models=model,
                                         frequencies_mhz=value, runtime='HS200,1.8V') for value in ('25', '50'))
        write_manifest(root / 'manifest.json', manifest)
        environment = dict(self.environment, T113_TEST_ASSUME_BUNDLE_VALID='1',
                           T113_MANIFEST=str(root / 'manifest.json'), T113_FGX_FDT=str(root / 'bin/fgx-fdt'),
                           T113_FGX_LOCK=str(root / 'bin/fgx-lock'), T113_LOCK_DIR=str(root / 'lock'),
                           T113_CPU_CUR_FILE=str(root / 'cpu'), T113_CPU_MAX_FILE=str(root / 'absent'),
                           TMPDIR=str(root))
        # An excluded model keeps the effective eMMC frequency for a CPU trial; an unknown
        # mode leaves no family for any generated DTB.
        for ios, axis, value, armed in ((HS200_IOS, 'cpu', '1.1', True), (HS200_IOS, 'emmc', '50', False),
                                        (HS200_IOS, 'emmc', 'stock', False), ('', 'cpu', '1.1', False)):
            with self.subTest(ios=bool(ios), axis=axis, value=value):
                (root / 'ios').write_text(ios)
                result = subprocess.run([str(PROFILECTL), '--dry-run', 'test', axis, value], env=environment,
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode == 0, armed, result.stdout + result.stderr)
                self.assertEqual('Candidate armed' in result.stdout, armed)


class ProfilectlVerifyJournalTest(unittest.TestCase):
    """A trial boot at eMMC 100 MHz, verified through profilectl's test mode."""

    def setUp(self):
        self.root = root = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name in ("bin", "boot", "state", "dt"):
            (root / name).mkdir()
        fallback = "run setargs_mmc boot_dsp0 boot_uImage"
        self.tool("fw_printenv", "printf '%s\\n' 'bootcmd={}'".format(fallback))
        self.tool("fw_setenv", "exit 0")
        self.tool("fgx-fdt", "exit 0")
        self.tool("fgx-lock", "exit 0")
        for name in ("p1", "p2", "p3"):
            (root / name).write_bytes(b"")
        (root / "boot/FORGE_X_A.DTB").write_bytes(b"candidate")
        digest = hashlib.sha256(b"candidate").hexdigest()
        (root / "state/candidate").write_text("\n".join((
            "format=2", "phase=armed", "axis=emmc", "value=100", "cpu_profile=stock", "cpu_hz=1008000000",
            "emmc_mhz=100", "family=hs2001v8", "slot=FORGE_X_A.DTB", "dtb_sha256=" + digest,
            "armed_boot_id=boot-a", "fallback_bootcmd=" + fallback, "untested_emmc_override=0")) + "\n")
        (root / "boot_id").write_text("boot-b\n")
        (root / "cpu").write_text("1008000\n")
        (root / "ios").write_text(HS200_IOS.replace("25000000", "100000000"))
        self.journal = root / "state/diagnostics/verify-dmesg.log"

    def tool(self, name, body):
        path = self.root / "bin" / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)

    def verify(self, kernel_journal, *options, **environment_overrides):
        (self.root / "dmesg").write_text(kernel_journal)
        root = self.root
        environment = dict(
            os.environ, PATH=str(root / "bin") + os.pathsep + os.environ.get("PATH", ""), T113_TEST_MODE="1",
            T113_TEST_SKIP_SYNC="1", T113_STATE_DIR=str(root / "state"), T113_BOOT_DIR=str(root / "boot"),
            T113_DEV_BOOT=str(root / "p1"), T113_DEV_ENV=str(root / "p2"), T113_DEV_ENV_REDUND=str(root / "p3"),
            T113_FGX_FDT=str(root / "bin/fgx-fdt"), T113_FGX_LOCK=str(root / "bin/fgx-lock"),
            T113_LOCK_DIR=str(root / "lock"), T113_BOOT_ID_FILE=str(root / "boot_id"),
            T113_IOS_FILE=str(root / "ios"), T113_CID_FILE=str(root / "cid"), T113_DT_ROOT=str(root / "dt"),
            T113_CPU_CUR_FILE=str(root / "cpu"), T113_CPU_MAX_FILE=str(root / "absent"),
            T113_DMESG_FILE=str(root / "dmesg"), TMPDIR=str(root))
        environment.update(environment_overrides)
        return subprocess.run([str(PROFILECTL)] + list(options or ("--apply",)) + ["verify"], env=environment,
                              capture_output=True, text=True, timeout=30)

    def test_failed_verify_keeps_the_whole_journal_with_the_trial_settings(self):
        failing = ("[    6.80] sunxi-mmc 4022000.sdmmc: smc 2 p0 err, cmd 25, DCE !!\n"
                   "[    6.85] Buffer I/O error on dev mmcblk0p7, logical block 4456448, lost async page write\n")
        result = self.verify(failing)
        self.assertEqual(result.returncode, 1)
        self.assertIn(str(self.journal), result.stderr)
        saved = self.journal.read_text()
        self.assertIn("boot-b", saved)
        self.assertIn(failing, saved)
        self.assertEqual(dict(line.split("=", 1) for line in (self.root / "state/candidate").read_text().splitlines())["phase"], "armed")

        for block in ("77", "78", "79"):
            self.assertEqual(self.verify(failing.replace("4456448", block)).returncode, 1)
        generations = [self.journal.with_name(name) for name in ("verify-dmesg.log", "verify-dmesg.log.1",
                                                                  "verify-dmesg.log.2", "verify-dmesg.log.3")]
        self.assertFalse(generations[3].exists())
        for path, block in zip(generations, ("79", "78", "77")):
            self.assertIn("block {},".format(block), path.read_text())

    def test_passed_or_dry_run_verify_saves_no_journal(self):
        dry_run = self.verify("Buffer I/O error on dev mmcblk0p7\n", "--dry-run")
        self.assertNotEqual(dry_run.returncode, 0)
        self.assertFalse(self.journal.exists())
        result = self.verify("[    1.00] mmc0: new HS200 MMC card at address 0001\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(dict(line.split("=", 1) for line in (self.root / "state/candidate").read_text().splitlines())["phase"], "passed")
        self.assertFalse(self.journal.exists())

    def test_verify_preserves_default_for_a_candidate_without_legacy_override(self):
        path = self.root / 'state/candidate'
        path.write_text(path.read_text().replace('untested_emmc_override=0\n', ''))
        result = self.verify('[    1.00] mmc0: new HS200 MMC card at address 0001\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        verified = dict(line.split('=', 1) for line in path.read_text().splitlines())
        self.assertEqual(verified['phase'], 'passed')
        self.assertEqual(verified['untested_emmc_override'], '0')
        self.assertFalse(self.journal.exists())

    def test_verify_needs_only_read_access_to_boot_and_preserves_all_boot_files(self):
        temporary = self.root / 'boot/.sunxi.dtb.tmp.keep'
        temporary.write_bytes(b'owned by another operation')
        before = {path.name: path.read_bytes() for path in (self.root / 'boot').iterdir()}
        result = self.verify('[    1.00] mmc0: new HS200 MMC card at address 0001\n',
                             T113_TEST_BOOT_MOUNT_STATE='ro')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({path.name: path.read_bytes() for path in (self.root / 'boot').iterdir()}, before)
        verified = dict(line.split('=', 1) for line in (self.root / 'state/candidate').read_text().splitlines())
        self.assertEqual(verified['phase'], 'passed')


if __name__ == "__main__":
    unittest.main()
