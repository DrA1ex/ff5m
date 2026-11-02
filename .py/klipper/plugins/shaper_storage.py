# Binary storage and bounded history for Forge-X shaper measurements.
# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
# This file may be distributed under the terms of the GNU GPLv3 license.

import hashlib
import json
import logging
import math
import os
import shutil
import struct
import sys
import tempfile

METHODS = ("zv", "mzv", "ei", "2hump_ei", "3hump_ei")
REFERENCE_METHODS = ("zvd",)
MAX_RESULT_BYTES = 256 * 1024
LAST_RESULT = "last.fxs"
SAVED_MAGIC = b"FXSRES1\n"
MAX_SAVED_BYTES = 2 * MAX_RESULT_BYTES + 1024
HISTORY_LIMIT = 5


def disk_directory(path):
    """Reject RAM filesystems, including symlinks and the chroot's bound /tmp."""
    path = os.path.realpath(path)
    if sys.platform.startswith("linux"):
        mount, filesystem = "", ""
        with open("/proc/mounts") as source:
            for line in source:
                fields = line.split()
                target = fields[1].replace("\\040", " ").replace("\\134", "\\")
                if (path == target or path.startswith(target.rstrip("/") + "/")) and len(target) > len(mount):
                    mount, filesystem = target, fields[2]
        if not mount or filesystem in ("tmpfs", "ramfs"):
            raise ValueError("Shaper recordings require a disk-backed directory")
    os.makedirs(path, exist_ok=True)
    return path


def load_result(path):
    with open(path, "rb") as source:
        encoded = source.read(MAX_RESULT_BYTES + 1)
    if len(encoded) > MAX_RESULT_BYTES:
        raise ValueError("Shaper result is too large")
    result = json.loads(encoded.decode("utf-8"))
    return validate_result(result)


def validate_result(result):
    if result.get("schema") != 1 or result.get("kind") != "input_shaper" or result.get("axis") not in ("x", "y"):
        raise ValueError("Unsupported shaper result")
    frequencies = result["freq_bins"]
    if not 2 <= len(frequencies) <= 512 or frequencies[0] != 0:
        raise ValueError("Invalid spectrum size")
    if any(not math.isfinite(v) or v < 0 or v > 200 for v in frequencies):
        raise ValueError("Invalid frequencies")
    if any(a >= b for a, b in zip(frequencies, frequencies[1:])):
        raise ValueError("Unordered frequencies")
    def curve(values):
        if len(values) != len(frequencies) or any(not math.isfinite(v) or v < 0 for v in values):
            raise ValueError("Invalid spectrum values")
    for name in ("psd_sum", "psd_x", "psd_y", "psd_z"):
        curve(result[name])
    names = tuple(s["name"] for s in result["shapers"])
    if names not in (METHODS, METHODS + REFERENCE_METHODS) or result["recommended"] not in METHODS:
        raise ValueError("Invalid shaper methods")
    for shaper in result["shapers"]:
        if shaper["name"] in REFERENCE_METHODS and shaper.get("reference_only") is not True:
            raise ValueError("Missing reference-only shaper marker")
        if shaper["name"] in METHODS and shaper.get("reference_only", False) is not False:
            raise ValueError("Invalid reference-only shaper marker")
        curve(shaper["response"])
        for name in ("freq", "vibrs", "smoothing", "score", "max_accel"):
            if not math.isfinite(shaper[name]) or shaper[name] < 0:
                raise ValueError("Invalid shaper metric")
    return result


SPECTRUM_FIELDS = ("freq_bins", "psd_sum", "psd_x", "psd_y", "psd_z")


def save_results(directory, results, saved_at):
    """One atomic binary snapshot; doubles remain exact, no zlib dependency."""
    metadata = dict(schema=1, saved_at=saved_at, results={})
    arrays = bytearray()
    for axis, result in sorted(results.items()):
        validate_result(result)
        compact = {k: v for k, v in result.items() if k not in SPECTRUM_FIELDS and k != "shapers"}
        compact["shapers"] = [{k: v for k, v in fit.items() if k != "response"} for fit in result["shapers"]]
        metadata["results"][axis] = compact
        count = len(result["freq_bins"])
        arrays.extend(struct.pack("<H", count))
        for values in [result[key] for key in SPECTRUM_FIELDS] + [fit["response"] for fit in result["shapers"]]:
            arrays.extend(struct.pack("<%dd" % count, *values))
    encoded = json.dumps(metadata, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) > 32 * 1024:
        raise ValueError("Saved shaper metadata is too large")
    body = struct.pack("<I", len(encoded)) + encoded + arrays
    blob = SAVED_MAGIC + body + hashlib.sha256(body).digest()
    if len(blob) > MAX_SAVED_BYTES:
        raise ValueError("Saved shaper result is too large")
    fd, temporary = tempfile.mkstemp(prefix=".last-", suffix=".part", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(blob)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, os.path.join(directory, LAST_RESULT))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read_snapshot(path):
    with open(path, "rb") as stream:
        blob = stream.read(MAX_SAVED_BYTES + 1)
    if len(blob) > MAX_SAVED_BYTES or len(blob) < 44 or not blob.startswith(SAVED_MAGIC):
        raise ValueError("Unsupported saved shaper result")
    body = blob[len(SAVED_MAGIC):-32]
    digest = hashlib.sha256(body).digest()
    if digest != blob[-32:]:
        raise ValueError("Saved shaper checksum mismatch")
    size, = struct.unpack_from("<I", body)
    if size > 32 * 1024 or size > len(body) - 4:
        raise ValueError("Invalid saved metadata size")
    saved = json.loads(body[4:4 + size].decode("utf-8"))
    if not isinstance(saved, dict) or saved.get("schema") != 1 or not isinstance(saved.get("results"), dict):
        raise ValueError("Unsupported saved shaper schema")
    results = saved["results"]
    if not results or set(results) - {"x", "y"}:
        raise ValueError("Invalid saved axes")
    if not isinstance(saved.get("saved_at"), (int, float)) or not math.isfinite(saved["saved_at"]) or saved["saved_at"] <= 0:
        raise ValueError("Invalid saved timestamp")
    return saved, body[4 + size:], digest


def load_snapshot(path):
    saved, body, _ = _read_snapshot(path)
    results = saved["results"]
    offset = 0
    for axis, result in sorted(results.items()):
        shapers = result["shapers"]
        if not 5 <= len(shapers) <= 6:
            raise ValueError("Invalid saved shapers")
        count, = struct.unpack_from("<H", body, offset)
        offset += 2
        if not 2 <= count <= 512:
            raise ValueError("Invalid saved spectrum size")
        curves = []
        for _ in range(len(SPECTRUM_FIELDS) + len(shapers)):
            curves.append(list(struct.unpack_from("<%dd" % count, body, offset)))
            offset += count * 8
        result.update(zip(SPECTRUM_FIELDS, curves))
        for fit, response in zip(shapers, curves[len(SPECTRUM_FIELDS):]):
            fit["response"] = response
        validate_result(result)
        if result["axis"] != axis:
            raise ValueError("Saved axis mismatch")
    if offset != len(body):
        raise ValueError("Unexpected saved data")
    return results, saved["saved_at"]


def _summary(path):
    """Read dates and axes without allocating spectrum arrays."""
    saved, _, digest = _read_snapshot(path)
    return {"id": digest.hex(), "saved_at": saved["saved_at"], "axes": sorted(saved["results"])}


class ResultHistory:
    """Latest snapshot plus four immutable archives, with stable content IDs."""

    def __init__(self, directory):
        self.directory = directory

    def _archives(self):
        try:
            names = os.listdir(self.directory)
        except FileNotFoundError:
            return []
        return [name for name in names if name.startswith("result-") and name.endswith(".fxs")
                and len(name) == len("result-") + 64 + len(".fxs")
                and all(c in "0123456789abcdef" for c in name[7:-4])]

    def entries(self):
        latest, older, seen = [], [], set()
        for name in [LAST_RESULT] + self._archives():
            path = os.path.join(self.directory, name)
            if os.path.islink(path):
                continue
            try:
                entry = _summary(path)
            except FileNotFoundError:
                continue
            except (OSError, ValueError, TypeError, struct.error) as error:
                logging.warning("[forge_shaper] Invalid history file %s: %s", name, error)
                continue
            if name != LAST_RESULT and name != "result-" + entry["id"] + ".fxs":
                continue
            if entry["id"] not in seen:
                entry["latest"] = name == LAST_RESULT
                (latest if entry["latest"] else older).append(entry)
                seen.add(entry["id"])
        # last.fxs is the most recently acquired result even if the clock changed.
        return (latest + sorted(older, key=lambda e: (e["saved_at"], e["id"]), reverse=True))[:HISTORY_LIMIT]

    def load(self, result_id):
        entry = next((e for e in self.entries() if e["id"] == result_id), None)
        if entry is None:
            raise ValueError("Shaper result is no longer available")
        latest = os.path.join(self.directory, LAST_RESULT)
        path = latest if entry["latest"] else os.path.join(
            self.directory, "result-" + result_id + ".fxs")
        return load_snapshot(path)

    def save(self, results, saved_at):
        latest = os.path.join(self.directory, LAST_RESULT)
        archived = None
        previous = None
        if os.path.isfile(latest):
            try:
                previous = _summary(latest)
            except (OSError, ValueError, TypeError, struct.error) as error:
                logging.warning("[forge_shaper] Replacing invalid latest result: %s", error)
        if previous is not None:
            archived = os.path.join(self.directory, "result-" + previous["id"] + ".fxs")
            if not os.path.exists(archived):
                fd, temporary = tempfile.mkstemp(prefix=".history-", suffix=".part", dir=self.directory)
                try:
                    with os.fdopen(fd, "wb") as target, open(latest, "rb") as source:
                        shutil.copyfileobj(source, target, 64 * 1024)
                        target.flush()
                        os.fsync(target.fileno())
                    os.replace(temporary, archived)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
        try:
            save_results(self.directory, results, saved_at)
        except Exception:
            # The previous latest is still intact; its extra archive is unnecessary.
            if archived is not None:
                try:
                    os.unlink(archived)
                except OSError:
                    pass
            raise
        entries = self.entries()
        keep = {"result-" + e["id"] + ".fxs" for e in entries[1:]}
        for name in self._archives():
            if name not in keep:
                try:
                    os.unlink(os.path.join(self.directory, name))
                except OSError as error:
                    logging.warning("[forge_shaper] Unable to prune history: %s", error)
        return entries

