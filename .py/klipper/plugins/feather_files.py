## Feather print-file discovery and recency history
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import json
import logging
import os


DEFAULT_HISTORY_PATH = "/opt/config/mod_data/feather_print_history.json"
HISTORY_LIMIT = 512
MAX_DIRECTORY_DEPTH = 2
VALID_GCODE_EXTS = (".gcode", ".g", ".gco")


class FileEntry:
    """Compact mapping-compatible record for one flattened browser row."""

    __slots__ = ("name", "path", "directory", "size", "mtime")

    def __init__(self, name, path, directory=False, size=0, mtime=0):
        self.name = name
        self.path = path
        self.directory = bool(directory)
        self.size = size
        self.mtime = mtime

    def __getitem__(self, key):
        if key not in self.__slots__:
            raise KeyError(key)
        return getattr(self, key)


def _relative_path(root, path):
    root = os.path.realpath(root)
    path = os.path.realpath(path)
    if path == root or not path.startswith(root + os.sep):
        return None
    relative = os.path.relpath(path, root)
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        return None
    return relative.replace(os.sep, "/")


class PrintHistory:
    """Persistent last-print timestamps keyed by virtual-SD relative path."""

    def __init__(self, path=DEFAULT_HISTORY_PATH):
        self.path = path
        self.timestamps = {}
        self._load()

    def _load(self):
        if not self.path:
            return
        try:
            with open(self.path, "r") as stream:
                saved = json.load(stream)
            if not isinstance(saved, dict):
                raise ValueError("history root is not an object")
            self.timestamps = {
                str(name): float(timestamp)
                for name, timestamp in saved.items()
                if (isinstance(name, str) and name
                    and isinstance(timestamp, (int, float))
                    and timestamp >= 0)
            }
        except (IOError, OSError):
            return
        except (TypeError, ValueError):
            logging.exception("[feather_screen] invalid print history")

    def last_printed(self, relative_path):
        return self.timestamps.get(relative_path, 0.0)

    def record(self, root, path, timestamp):
        relative = _relative_path(root, path)
        if relative is None:
            return False
        try:
            timestamp = float(timestamp)
        except (TypeError, ValueError):
            return False
        if timestamp < 0:
            return False
        self.timestamps[relative] = timestamp
        if len(self.timestamps) > HISTORY_LIMIT:
            newest = sorted(
                self.timestamps.items(), key=lambda item: item[1],
                reverse=True)[:HISTORY_LIMIT]
            self.timestamps = dict(newest)
        self._save()
        return True

    def _save(self):
        if not self.path:
            return
        temporary_path = self.path + ".tmp"
        try:
            directory = os.path.dirname(self.path)
            if directory and not os.path.isdir(directory):
                os.makedirs(directory)
            with open(temporary_path, "w") as stream:
                json.dump(
                    self.timestamps, stream, ensure_ascii=False,
                    separators=(",", ":"), sort_keys=True)
            os.replace(temporary_path, self.path)
        except (IOError, OSError):
            logging.exception("[feather_screen] unable to save print history")
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


def scan_gcode_files(root, history=None, max_depth=MAX_DIRECTORY_DEPTH):
    """Return a flat newest-first list without following directory symlinks."""
    root = os.path.realpath(root)
    entries = []
    pending = [(root, 0)]
    while pending:
        directory, depth = pending.pop()
        try:
            with os.scandir(directory) as listing:
                for child in listing:
                    if child.name.startswith("."):
                        continue
                    path = os.path.realpath(child.path)
                    if path != root and not path.startswith(root + os.sep):
                        continue
                    if child.is_dir(follow_symlinks=False):
                        if depth < max_depth:
                            pending.append((path, depth + 1))
                        continue
                    if (not child.is_file(follow_symlinks=False)
                            or not child.name.lower().endswith(
                                VALID_GCODE_EXTS)):
                        continue
                    relative = _relative_path(root, path)
                    if relative is None:
                        continue
                    stat = child.stat(follow_symlinks=False)
                    entries.append(FileEntry(
                        relative, path, False, stat.st_size, stat.st_mtime))
        except OSError as exc:
            raise RuntimeError("Unable to list files: %s" % exc)

    def sort_key(item):
        printed = (history.last_printed(item.name)
                   if history is not None else 0.0)
        recency = max(float(item.mtime), float(printed))
        return (-recency, item.name.lower())

    entries.sort(key=sort_key)
    return entries
