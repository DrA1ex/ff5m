## Tests for OrcaSlicer packaged G-code extraction.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import hashlib
import importlib.util
import pathlib
import tempfile
import unittest
import zipfile


MODULE_PATH = (pathlib.Path(__file__).parents[1] / ".root" / "moonraker" /
               "components" / "file_manager" / "gcode_3mf.py")
SPEC = importlib.util.spec_from_file_location("forge_x_gcode_3mf", MODULE_PATH)
GCODE_3MF = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GCODE_3MF)


class GCode3MFTest(unittest.TestCase):
    def make_archive(self, path, plates, include_md5=True, bad_md5=None):
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for index, data in plates.items():
                plate_path = f"Metadata/plate_{index}.gcode"
                zf.writestr(plate_path, data)
                if include_md5:
                    digest = hashlib.md5(data).hexdigest()
                    if bad_md5 == index:
                        digest = "0" * 32
                    zf.writestr(plate_path + ".md5", digest.upper())

    def test_single_plate_is_selected_and_md5_header_is_added(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = pathlib.Path(tmp) / "job.gcode.3mf"
            dest = pathlib.Path(tmp) / "job.gcode"
            gcode = b"G28\nG1 X10 Y20\n"
            self.make_archive(source, {1: gcode})

            selected = GCODE_3MF.extract_gcode_3mf(str(source), str(dest))

            self.assertEqual(selected, "Metadata/plate_1.gcode")
            output = dest.read_bytes()
            header, body = output.split(b"\n", 1)
            self.assertEqual(
                header,
                b"; MD5:" + hashlib.md5(gcode).hexdigest().encode()
            )
            self.assertEqual(body, gcode)

    def test_plateindex_selects_requested_plate(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = pathlib.Path(tmp) / "job.gcode.3mf"
            dest = pathlib.Path(tmp) / "job.gcode"
            second = b"; plate two\nG28\n"
            self.make_archive(source, {1: b"; plate one\n", 2: second})

            selected = GCODE_3MF.extract_gcode_3mf(
                str(source), str(dest), "2")

            self.assertEqual(selected, "Metadata/plate_2.gcode")
            self.assertTrue(dest.read_bytes().endswith(second))

    def test_multiple_plates_require_plateindex(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = pathlib.Path(tmp) / "job.gcode.3mf"
            dest = pathlib.Path(tmp) / "job.gcode"
            self.make_archive(source, {1: b"G28\n", 2: b"G28\n"})

            with self.assertRaisesRegex(
                GCODE_3MF.GCode3MFError, "plateindex is required"
            ):
                GCODE_3MF.extract_gcode_3mf(str(source), str(dest))

            self.assertFalse(dest.exists())

    def test_md5_mismatch_is_rejected_without_replacing_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = pathlib.Path(tmp) / "job.gcode.3mf"
            dest = pathlib.Path(tmp) / "job.gcode"
            dest.write_bytes(b"existing\n")
            self.make_archive(source, {1: b"G28\n"}, bad_md5=1)

            with self.assertRaisesRegex(
                GCODE_3MF.GCode3MFError, "MD5 mismatch"
            ):
                GCODE_3MF.extract_gcode_3mf(str(source), str(dest))

            self.assertEqual(dest.read_bytes(), b"existing\n")

    def test_archive_without_md5_is_extracted_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = pathlib.Path(tmp) / "job.gcode.3mf"
            dest = pathlib.Path(tmp) / "job.gcode"
            gcode = b"G28\n"
            self.make_archive(source, {1: gcode}, include_md5=False)

            GCODE_3MF.extract_gcode_3mf(str(source), str(dest))

            self.assertEqual(dest.read_bytes(), gcode)

    def test_output_filename_only_strips_3mf_suffix(self):
        self.assertEqual(
            GCODE_3MF.output_filename("folder/model.gcode.3mf"),
            "folder/model.gcode",
        )
        self.assertTrue(GCODE_3MF.is_gcode_3mf("MODEL.GCODE.3MF"))


if __name__ == "__main__":
    unittest.main()
