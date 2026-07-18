## Timezone helper and macro integration tests.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).parents[1]
HELPER = ROOT / ".shell" / "commands" / "ztimezone.sh"


class TimezoneHelperTest(unittest.TestCase):
    def _run(self, zone, zoneinfo, localtime):
        env = dict(os.environ)
        env.update({
            "ZONEINFO_ROOT": str(zoneinfo),
            "LOCALTIME_PATH": str(localtime),
        })
        return subprocess.run(
            [str(HELPER), zone], env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)

    def test_valid_zone_is_installed_as_an_atomic_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            zoneinfo = root / "zoneinfo"
            zone = zoneinfo / "Asia" / "Yekaterinburg"
            zone.parent.mkdir(parents=True)
            zone.write_bytes(b"TZif")
            localtime = root / "localtime"
            localtime.write_bytes(b"old")

            result = self._run("Asia/Yekaterinburg", zoneinfo, localtime)

            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertTrue(localtime.is_symlink())
            self.assertEqual(localtime.resolve(), zone.resolve())
            self.assertIn("TIMEZONE=Asia/Yekaterinburg", result.stdout)

    def test_invalid_zone_does_not_remove_existing_localtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            zoneinfo = root / "zoneinfo"
            zoneinfo.mkdir()
            localtime = root / "localtime"
            localtime.write_bytes(b"keep")

            result = self._run("../../etc/passwd", zoneinfo, localtime)

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(localtime.read_bytes(), b"keep")
            self.assertIn("ERROR=Invalid timezone name", result.stdout)

    def test_macro_uses_validating_helper_instead_of_rm_then_ln(self):
        macro = (ROOT / "macros" / "base.cfg").read_text(encoding="utf-8")
        block = macro.split("[gcode_macro SET_TIMEZONE]", 1)[1].split(
            "[gcode_macro KAMP]", 1)[0]
        self.assertIn("RUN_SHELL_COMMAND CMD=ztimezone", block)
        self.assertNotIn("CMD=rm", block)
        self.assertNotIn("CMD=ln", block)


if __name__ == "__main__":
    unittest.main()
