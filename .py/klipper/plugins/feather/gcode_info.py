## File details for the pre-print model preview.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import math
import os
import re

from feather.previews import PreviewCancelled


def read_gcode_info(path, cancel):
    """Read bounded slicer headers and footers without executing G-code."""
    with open(path, "rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        if cancel.is_set():
            raise PreviewCancelled()
        header = stream.read(128 * 1024)
        stream.seek(max(len(header), size - 128 * 1024))
        footer = stream.read(128 * 1024)
    if cancel.is_set():
        raise PreviewCancelled()
    text = (header + b"\n" + footer).decode("utf-8", "replace")

    def field(pattern):
        found = re.search(r"^;\s*(?:" + pattern + r")\s*[:=]\s*([^\r\n]+)",
                          text, re.IGNORECASE | re.MULTILINE)
        return found.group(1).strip() if found else None

    def number(value):
        found = re.match(r"\s*(\d+(?:\.\d+)?)", value or "")
        result = float(found.group(1)) if found else None
        return result if result is not None and math.isfinite(result) else None

    duration = field(r"(?:OrcaSlicer\s+)?estimated (?:printing|extrusion) time[^:=\r\n]*")
    seconds = None
    if duration:
        units = {"d": 86400, "h": 3600, "m": 60, "s": 1}
        parts = re.findall(r"(\d+(?:\.\d+)?)\s*([dhms])", duration.lower())
        if parts:
            seconds = sum(float(value) * units[unit] for value, unit in parts)
            if not math.isfinite(seconds):
                seconds = None
    if seconds is None:
        seconds = number(field(r"TIME"))
    layers = number(field(r"LAYER_COUNT|total layer number|total_layers"))
    dimensions = field(r"model_size")
    if dimensions:
        sizes = re.findall(r"\d+(?:\.\d+)?", dimensions)
        dimensions = ("x".join("%g" % float(value) for value in sizes[:3]) + " MM"
                      if len(sizes) >= 3 and all(math.isfinite(float(value)) for value in sizes[:3]) else None)
    else:
        sizes = []
        for axis in "XYZ":
            minimum = field("MIN" + axis)
            maximum = field("MAX" + axis)
            try:
                extent = float(maximum) - float(minimum)
            except (TypeError, ValueError):
                break
            if not math.isfinite(extent) or extent < 0:
                break
            sizes.append(extent)
        dimensions = "x".join("%g" % value for value in sizes) + " MM" if len(sizes) == 3 else None
    weight = number(field(r"filament used \[g\]|filament weight(?: \[g\])?"))
    length = number(field(r"filament used \[mm\]|filament used"))
    material = field(r"filament_type|Filament type|MATERIAL")
    filament = ("%g G" % weight if weight is not None else
                "%.2f M" % (length / 1000) if length is not None else "--")
    if material:
        filament += " " + material.split(";")[0].strip()
    return {"size": size, "seconds": seconds, "layers": int(layers) if layers is not None else None,
            "dimensions": dimensions, "filament": filament}
