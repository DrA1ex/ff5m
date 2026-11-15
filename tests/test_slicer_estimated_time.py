"""Regression coverage for slicer ETA comments in saved G-code settings."""

import ast
import copy
import io
import os
import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).parents[1]
TARGETS = {
    "moonraker": (
        ".root/moonraker/components/file_manager/metadata.py", "PrusaSlicer"),
    "virtual_sdcard": (
        ".py/klipper/patches/extras/virtual_sdcard.py", "VirtualSD"),
}
SAVED_TEMPLATE = (
    "; file_start_gcode = ; estimated printing time (normal mode) = "
    "{print_time_day}d {print_time_hour}h {print_time_minute}m "
    "{print_time_sec}s\n")
REAL_ESTIMATE = "; estimated printing time (normal mode) = 5h 1m 52s\n"


def load_estimated_time_method(path, class_name):
    # Execute the actual pure parser without loading printer-only dependencies.
    tree = ast.parse((ROOT / path).read_text())
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = copy.deepcopy(next(
        node for node in cls.body if isinstance(node, ast.FunctionDef)
        and node.name == "parse_estimated_time"))
    method.returns = None
    for argument in method.args.args:
        argument.annotation = None
    module = ast.Module(body=[method], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"re": re, "os": os}
    exec(compile(module, str(ROOT / path), "exec"), namespace)
    return namespace["parse_estimated_time"]


class SlicerEstimatedTimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.methods = {
            label: load_estimated_time_method(path, class_name)
            for label, (path, class_name) in TARGETS.items()
        }

    def assert_both(self, footer, expected):
        for label, method in self.methods.items():
            with self.subTest(parser=label):
                if label == "moonraker":
                    holder = type("Footer", (), {"footer_data": footer})()
                    result = method(holder)
                else:
                    result = method(None, io.StringIO(footer), len(footer))
                self.assertEqual(result, expected)

    def test_completed_four_part_plate_footer(self):
        # Reduced exact footer from an Orca 2.5.1 plate: 5*3600 + 60 + 52.
        self.assert_both(SAVED_TEMPLATE + REAL_ESTIMATE, 18112)

    def test_saved_template_without_real_time_is_unknown(self):
        self.assert_both(SAVED_TEMPLATE, None)

    def test_nested_setting_comment_is_not_an_estimate(self):
        self.assert_both(
            "; file_start_gcode = ; estimated printing time = 9h\n"
            + REAL_ESTIMATE, 18112)

    def test_invalid_candidate_does_not_hide_later_valid_time(self):
        for value in ("", "unknown", "18112", "{print_time_hour}h",
                      "0d 0h {print_time_minute}m 0s"):
            with self.subTest(value=value):
                self.assert_both(
                    "; estimated printing time = " + value + "\n"
                    + REAL_ESTIMATE, 18112)

    def test_normal_duration_units(self):
        for duration, expected in (
                ("2d 3h 4m 5s", 183845), ("3h", 10800), ("7m", 420),
                ("59s", 59), ("1h2m3s", 3723), ("0d 0h 5m 0s", 300)):
            with self.subTest(duration=duration):
                self.assert_both(
                    "; estimated printing time = " + duration + "\n",
                    expected)

    def test_orca_extrusion_and_comment_formats(self):
        for line in (
                "; OrcaSlicer estimated extrusion time = 1h 2m 3s\n",
                "  ; estimated printing time = 1h 2m 3s\r\n",
                ";estimated printing time = 1h 2m 3s\n"):
            with self.subTest(line=line):
                self.assert_both(line, 3723)

    def test_explicit_zero_remains_zero(self):
        self.assert_both("; estimated printing time = 0s\n", 0)

    def test_missing_time_is_unknown(self):
        self.assert_both("; no duration metadata\nG1 X10\n", None)

    def test_virtual_sdcard_preserves_footer_window(self):
        method = self.methods["virtual_sdcard"]
        data = "; estimated printing time = 9h\n" + "; filler\n" * 30000
        self.assertIsNone(method(None, io.StringIO(data), len(data)))
        data += "; estimated printing time = 7m\n"
        self.assertEqual(method(None, io.StringIO(data), len(data)), 420)


if __name__ == "__main__":
    unittest.main()
