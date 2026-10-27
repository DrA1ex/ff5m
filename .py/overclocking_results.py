"""Durable hardware-test evidence for Recovery tuning.

Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
This file may be distributed under the terms of the GNU GPLv3 license.

The pending trial itself is profilectl's candidate; this file only keeps what
the tests established. Evidence never authorizes a boot route or a profile.
"""

import json
import os
import tempfile


ROOT = "/data/.forge-x-overclocking"


class TestResults:
    """The running test, the latest result per kind and the last acceptance."""

    def __init__(self, root=ROOT):
        self.root = os.fspath(root)
        self.path = os.path.join(self.root, "tests.json")

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as source:
                data = json.load(source)
        except FileNotFoundError:
            data = {}
        except (OSError, ValueError):
            # Unreadable evidence is treated as absent: nothing depends on it
            # except the displayed history, which the next test rewrites.
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("running", None)
        data.setdefault("results", {})
        data.setdefault("acceptance", None)
        return data

    def _save(self, data):
        os.makedirs(self.root, mode=0o700, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".tests-", dir=self.root)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(data, output, sort_keys=True)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def start(self, kind, profile, boot_id):
        """Record the test before it runs, so a reset reports it as interrupted."""
        data = self._load()
        data["results"].pop(kind, None)
        data["running"] = {"kind": kind, "profile": profile, "boot_id": boot_id}
        self._save(data)

    def finish(self, status, detail, speed=None):
        data = self._load()
        running = data["running"]
        if running is None:
            return
        data["results"][running["kind"]] = dict(running, status=status, detail=detail[:512])
        if speed:
            data["results"][running["kind"]]["speed"] = speed
        data["running"] = None
        self._save(data)

    def take_interrupted(self):
        """Close a test that was still running when its Recovery process ended."""
        running = self._load()["running"]
        if running is None:
            return None
        self.finish("interrupted", "Test completion was not recorded.")
        return running["kind"]

    def result(self, kind, profile, boot_id):
        """A result counts only for the profile and boot it actually tested."""
        result = self._load()["results"].get(kind)
        if result and result["profile"] == profile and result["boot_id"] == boot_id:
            return result
        return None

    def accept(self, profile, outcomes):
        data = self._load()
        data["acceptance"] = {"profile": profile, "tests": outcomes}
        self._save(data)

    def acceptance(self, profile):
        acceptance = self._load()["acceptance"]
        return acceptance if acceptance and acceptance["profile"] == profile else None
