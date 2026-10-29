"""The real profile utility against fake sysfs, debugfs, kernel journal and boot files.

Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
This file may be distributed under the terms of the GNU GPLv3 license.
"""

import hashlib
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
        if cid_text is not None:
            (self.root / "cid").write_text(cid_text + "\n")
        if ios is not None:
            (self.root / "ios").write_text(ios)
        result = subprocess.run([str(PROFILECTL), "detect"], env=environment, capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

    def test_another_specimen_of_a_supported_model_is_known(self):
        for model, ios, family in (("15:0100:485332303047:00", HS200_IOS, "hs2001v8"),
                                   ("d6:0103:353841333938:11", HS200_IOS, "hs2001v8"),
                                   ("15:0100:4d3458464955:00", LEGACY_IOS, "legacy3v3")):
            with self.subTest(model=model):
                detected = self.detect(cid(model).upper(), ios)
                self.assertEqual((detected["hardware"], detected["family"]), ("known", family))

    def test_other_revision_of_a_supported_model_is_untested(self):
        detected = self.detect(cid("15:0100:485332303047:01"), HS200_IOS)
        self.assertEqual((detected["hardware"], detected["family"]), ("untested", "hs2001v8"))

    def test_supported_model_needs_matching_runtime_evidence(self):
        missing = self.detect(cid("15:0100:485332303047:00"))
        self.assertEqual((missing["hardware"], missing["family"]), ("untested", "hs2001v8"))
        self.assertEqual(missing["hardware_conflict"], "0")
        conflict = self.detect(ios=LEGACY_IOS)
        self.assertEqual((conflict["hardware"], conflict["hardware_conflict"]), ("untested", "1"))

    def test_malformed_cid_is_not_matched(self):
        for text in (cid("15:0100:485332303047:00")[:-1], cid("15:0100:485332303047:00")[:-1] + "g"):
            with self.subTest(cid=text):
                detected = self.detect(text, HS200_IOS)
                self.assertEqual(detected["hardware"], "untested")
                self.assertEqual(detected["cid"], "unknown")



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

    def verify(self, kernel_journal, *options):
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
        return subprocess.run([str(PROFILECTL)] + list(options or ("--apply",)) + ["verify"], env=environment,
                              capture_output=True, text=True, timeout=30)

    def test_failed_verify_keeps_the_whole_journal_with_the_trial_settings(self):
        failing = ("[    6.80] sunxi-mmc 4022000.sdmmc: smc 2 p0 err, cmd 25, DCE !!\n"
                   "[    6.85] Buffer I/O error on dev mmcblk0p7, logical block 4456448, lost async page write\n")
        result = self.verify(failing)
        self.assertEqual(result.returncode, 1)
        self.assertIn("new fatal kernel/MMC errors found", result.stderr)
        self.assertIn(str(self.journal), result.stderr)
        saved = self.journal.read_text()
        self.assertIn("eMMC=100 MHz family=hs2001v8", saved)
        self.assertIn("boot_id=boot-b", saved)
        self.assertIn("smc 2 p0 err, cmd 25", saved)
        self.assertIn("lost async page write", saved)

        for block in ("77", "78", "79"):
            self.assertEqual(self.verify(failing.replace("4456448", block)).returncode, 1)
        generations = [self.journal.with_name(name) for name in ("verify-dmesg.log", "verify-dmesg.log.1",
                                                                  "verify-dmesg.log.2", "verify-dmesg.log.3")]
        self.assertFalse(generations[3].exists())
        for path, block in zip(generations, ("79", "78", "77")):
            self.assertIn("block {},".format(block), path.read_text())

    def test_passed_or_dry_run_verify_saves_no_journal(self):
        dry_run = self.verify("Buffer I/O error on dev mmcblk0p7\n", "--dry-run")
        self.assertIn("new fatal kernel/MMC errors found", dry_run.stderr)
        self.assertFalse(self.journal.exists())
        result = self.verify("[    1.00] mmc0: new HS200 MMC card at address 0001\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Candidate verification: PASS", result.stdout)
        self.assertFalse(self.journal.exists())


if __name__ == "__main__":
    unittest.main()
