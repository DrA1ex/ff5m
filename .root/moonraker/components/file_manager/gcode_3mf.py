# OrcaSlicer packaged G-code (.gcode.3mf) support
#
# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import zipfile
from typing import List, Optional, Tuple


GCODE_3MF_SUFFIX = ".gcode.3mf"
PLATE_GCODE_RE = re.compile(r"^Metadata/plate_([1-9][0-9]*)\.gcode$")
MD5_RE = re.compile(r"^[0-9a-fA-F]{32}$")
COPY_CHUNK_SIZE = 64 * 1024


class GCode3MFError(Exception):
    pass


def is_gcode_3mf(filename: str) -> bool:
    return filename.lower().endswith(GCODE_3MF_SUFFIX)


def output_filename(filename: str) -> str:
    if not is_gcode_3mf(filename):
        raise GCode3MFError(f"Not a packaged G-code file: {filename}")
    return filename[:-len(".3mf")]


def _find_plates(zf: zipfile.ZipFile) -> List[Tuple[int, str]]:
    plates = []
    for name in zf.namelist():
        match = PLATE_GCODE_RE.match(name)
        if match is not None:
            plates.append((int(match.group(1)), name))
    return sorted(plates)


def _select_plate(zf: zipfile.ZipFile, plate_index: Optional[str]) -> str:
    plates = _find_plates(zf)
    if not plates:
        raise GCode3MFError("No printable G-code found in 3MF")

    if plate_index not in (None, ""):
        try:
            index = int(plate_index)
        except (TypeError, ValueError):
            raise GCode3MFError(f"Invalid plateindex: {plate_index}")
        if index < 1:
            raise GCode3MFError(f"Invalid plateindex: {plate_index}")

        plate_path = f"Metadata/plate_{index}.gcode"
        if plate_path not in {name for _, name in plates}:
            raise GCode3MFError(f"Plate {index} not found in 3MF")
        return plate_path

    if len(plates) != 1:
        raise GCode3MFError(
            "Multiple printable plates found in 3MF, plateindex is required")
    return plates[0][1]


def _read_expected_md5(
    zf: zipfile.ZipFile, plate_path: str
) -> Optional[str]:
    md5_path = plate_path + ".md5"
    try:
        raw_md5 = zf.read(md5_path)
    except KeyError:
        return None

    try:
        expected_md5 = raw_md5.decode("ascii").strip()
    except UnicodeDecodeError:
        raise GCode3MFError(f"Invalid MD5 data in {md5_path}")

    if MD5_RE.fullmatch(expected_md5) is None:
        raise GCode3MFError(f"Invalid MD5 data in {md5_path}")
    return expected_md5.lower()


def extract_gcode_3mf(
    source_path: str,
    dest_path: str,
    plate_index: Optional[str] = None,
) -> str:
    tmp_path = None
    try:
        with zipfile.ZipFile(source_path) as zf:
            plate_path = _select_plate(zf, plate_index)
            expected_md5 = _read_expected_md5(zf, plate_path)

            dest_dir = os.path.dirname(dest_path) or "."
            with tempfile.NamedTemporaryFile(
                mode="w+b", dir=dest_dir, delete=False
            ) as out_file:
                tmp_path = out_file.name
                if expected_md5 is not None:
                    out_file.write(
                        b"; MD5:" + expected_md5.encode("ascii") + b"\n")

                digest = hashlib.md5()
                with zf.open(plate_path) as gcode_file:
                    while True:
                        chunk = gcode_file.read(COPY_CHUNK_SIZE)
                        if not chunk:
                            break
                        digest.update(chunk)
                        out_file.write(chunk)

            if expected_md5 is not None and digest.hexdigest() != expected_md5:
                raise GCode3MFError(
                    f"MD5 mismatch for {plate_path}: "
                    f"{expected_md5} != {digest.hexdigest()}")

        os.replace(tmp_path, dest_path)
        tmp_path = None
        return plate_path
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise GCode3MFError(f"Invalid G-code 3MF: {exc}")
    finally:
        if tmp_path is not None:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
