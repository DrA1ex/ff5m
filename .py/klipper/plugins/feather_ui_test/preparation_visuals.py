"""Calibration confirmation fixtures for the non-physical UI suite."""

# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
# This file may be distributed under the terms of the GNU GPLv3 license.

# Capture label suffix, calibration kind, skip cleaning selected, empty material list.
CASES = (
    ("z-offset", "z", False, False),
    ("z-offset-skip", "z", True, False),
    ("bed-screws", "screws", False, False),
    ("bed-screws-skip", "screws", True, False),
    ("bed-screws-empty", "screws", False, True),
    ("bed-mesh", "mesh", False, False),
    ("bed-mesh-empty", "mesh", False, True),
    ("bed-pid", "pid_bed", False, False),
    ("hotend-pid", "pid_extruder", False, False),
    ("legacy-shaper", "shaper", False, False),
)
