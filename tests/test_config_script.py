"""Behavioral contracts for the shell configuration helper."""

## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).parents[1]
ZCONF = ROOT / ".shell" / "commands" / "zconf.sh"


def run(*arguments):
    return subprocess.run(
        ["bash", str(ZCONF), *map(str, arguments)],
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


class ConfigScriptTest(unittest.TestCase):
    def test_get_uses_default_when_file_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = pathlib.Path(directory) / "variables.cfg"

            result = run(missing, "--get", "use_swap", "MMC")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "MMC")

    def test_set_rejects_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = pathlib.Path(directory) / "variables.cfg"

            result = run(missing, "--set", "use_swap=MMC")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("doesn't exists", result.stdout)


if __name__ == "__main__":
    unittest.main()
