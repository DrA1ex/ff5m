## Tests that the Klipper replacement files match the published history repo.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import importlib.util
import pathlib
import re
import unittest


SCRIPT = pathlib.Path(__file__).with_name("verify_klipper_fork.py")
SPEC = importlib.util.spec_from_file_location("verify_klipper_fork", SCRIPT)
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


class KlipperForkManifestTest(unittest.TestCase):
    def setUp(self):
        self.manifest = VERIFY.load_manifest()

    def test_overlay_matches_manifest(self):
        self.assertEqual([], VERIFY.check_manifest(self.manifest))

    def test_manifest_records_fork_identity(self):
        self.assertEqual(VERIFY.FORK_URL, self.manifest["fork"])
        for key in ("base_commit", "stock_commit", "commit"):
            self.assertRegex(self.manifest[key], r"^[0-9a-f]{40}$", key)

    def test_entries_are_complete(self):
        for group in ("sources", "binaries"):
            for name, entry in self.manifest[group].items():
                self.assertTrue(name.startswith("klippy/"), name)
                self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$", name)
                self.assertTrue(
                    (VERIFY.ROOT / entry["ff5m"]).is_file(), entry["ff5m"])

    def test_sources_are_python_and_binaries_are_not(self):
        self.assertTrue(all(n.endswith(".py") for n in self.manifest["sources"]))
        self.assertFalse(
            any(n.endswith(".py") for n in self.manifest["binaries"]))

    def test_every_patch_file_is_covered(self):
        listed = set(self.manifest["sources"]) | set(self.manifest["binaries"])
        for rel in VERIFY.overlay_files():
            self.assertIn("klippy/" + rel, listed)
        self.assertIsNotNone(re.match(r"^https://", self.manifest["fork"]))


if __name__ == "__main__":
    unittest.main()
