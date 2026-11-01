"""The manifest owns eMMC model lists; the schema checks their format.

Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
This file may be distributed under the terms of the GNU GPLv3 license.
"""

import copy
import json
import pathlib
import re
import subprocess
import unittest

from jsonschema import Draft202012Validator, ValidationError


BUNDLE = pathlib.Path(__file__).parents[1] / ".bin/tools/overclocking"


class TuningManifestTest(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((BUNDLE / "manifest-v2.json").read_text())
        schema = json.loads((BUNDLE / "schema/manifest-v2.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        self.validator = Draft202012Validator(schema)

    def test_current_manifest_matches_schema(self):
        self.validator.validate(self.manifest)

    def test_model_can_be_added_to_either_family_without_changing_schema(self):
        for index in range(len(self.manifest["hardware"])):
            with self.subTest(family=index):
                manifest = copy.deepcopy(self.manifest)
                manifest["hardware"][index]["models"] += ",aa:0001:414243444546:02"
                self.validator.validate(manifest)

    def test_cpu_records_share_structure_without_pinning_aliases_or_order(self):
        self.manifest["cpu"].reverse()
        self.manifest["cpu"][0].update(alias="1.392", accepted_aliases="1392", warning="Trial required")
        self.validator.validate(self.manifest)

    def test_published_cpu_profiles_agree_with_profilectl(self):
        result = subprocess.run([str(BUNDLE / "bin/t113-profilectl"), "list", "cpu"],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        offered = re.findall(r"([0-9.]+) -> ([0-9]+) MHz", result.stdout)
        self.assertCountEqual(offered, [(row["alias"], str(row["exact_mhz"])) for row in self.manifest["cpu"]])
        for row in self.manifest["cpu"]:
            self.assertEqual(row["exact_hz"], row["exact_mhz"] * 1000000)

    def test_model_format_and_existing_constraints_are_preserved(self):
        for field, value in (("models", "15:0100:4d3458464955:*"), ("models", "bad-id"),
                             ("models", []), ("frequencies_mhz", "25,200"), ("kind", "other")):
            with self.subTest(field=field, value=value):
                manifest = copy.deepcopy(self.manifest)
                manifest["hardware"][0][field] = value
                with self.assertRaises(ValidationError):
                    self.validator.validate(manifest)
        for field, value in (("exact_mhz", 1128), ("exact_hz", 1400000000)):
            with self.subTest(field=field, value=value):
                manifest = copy.deepcopy(self.manifest)
                manifest["cpu"][0][field] = value
                with self.assertRaises(ValidationError):
                    self.validator.validate(manifest)
        del self.manifest["cpu"][-1]["warning"]
        with self.assertRaises(ValidationError):
            self.validator.validate(self.manifest)


if __name__ == "__main__":
    unittest.main()
