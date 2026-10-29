"""eMMC recognition by the real profile utility from sysfs CID and debugfs ios.

Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
This file may be distributed under the terms of the GNU GPLv3 license.
"""

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


if __name__ == "__main__":
    unittest.main()
