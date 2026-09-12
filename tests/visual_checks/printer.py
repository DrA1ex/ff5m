## Explicit, opt-in collection of non-physical printer UI artifacts.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Explicit, opt-in collection of non-physical printer UI artifacts."""

import base64
import json
import pathlib
import subprocess
import time

from tests.printer_connection import (
    ARTIFACT_ROOT,
    SAFE_HOST,
    PrinterConnection,
    PrinterConnectionError,
)


SAFE_SUITES = frozenset(("UI", "COMPONENT"))
PrinterCollectionError = PrinterConnectionError


class PrinterCollector:
    def __init__(self, host, confirmed_idle=False, timeout=10,
                 requester=None, command_runner=None, clock=None, sleeper=None):
        self.host = str(host or "").strip()
        self.confirmed_idle = bool(confirmed_idle)
        self.timeout = float(timeout)
        self.clock = clock or time.monotonic
        self.sleeper = sleeper or time.sleep
        self.connection = PrinterConnection(
            self.host, timeout=self.timeout, requester=requester,
            command_runner=command_runner)

    @property
    def requester(self):
        return self.connection.requester

    @requester.setter
    def requester(self, value):
        self.connection.requester = value

    @property
    def command_runner(self):
        return self.connection.command_runner

    @command_runner.setter
    def command_runner(self, value):
        self.connection.command_runner = value

    @property
    def base_url(self):
        return self.connection.base_url

    def _json(self, method, path, payload=None):
        return self.connection.request_json(method, path, payload)

    def preflight(self):
        if not self.confirmed_idle:
            raise PrinterCollectionError(
                "live collection requires --confirm-printer-idle")
        return self.connection.require_safe_idle()

    def _ssh(self, remote_command, timeout=None):
        return self.connection.ssh(remote_command, timeout=timeout)

    def _latest(self, suite):
        suffix = "-" + suite.lower()
        command = (
            "find %s -mindepth 1 -maxdepth 1 -type d -name '*%s' "
            "| sort | tail -n 1" % (ARTIFACT_ROOT, suffix))
        return self._ssh(command)

    def _active(self):
        return self._ssh(
            "if [ -f %s/active.json ]; then echo active; "
            "else echo idle; fi" % ARTIFACT_ROOT) == "active"

    def collect(self, suite, output_root, run_timeout=240,
                component_cases=None):
        suite = str(suite).upper()
        if suite not in SAFE_SUITES:
            raise PrinterCollectionError("unsupported non-physical suite")
        self.preflight()
        before = self._latest(suite)
        command = "_FEATHER_UI_TEST ACTION=RUN SUITE=%s CONFIRM=1" % suite
        if component_cases:
            if suite != "COMPONENT":
                raise PrinterCollectionError(
                    "typed component cases require COMPONENT suite")
            raw = json.dumps(
                list(component_cases), separators=(",", ":"),
                ensure_ascii=True).encode("utf-8")
            encoded = base64.urlsafe_b64encode(raw).decode("ascii")
            command += " CASES=" + encoded
        self._json("POST", "/printer/gcode/script", {"script": command})
        deadline = self.clock() + float(run_timeout)
        active_seen = False
        while self.clock() < deadline:
            active = self._active()
            active_seen = active_seen or active
            latest = self._latest(suite)
            if active_seen and not active and latest and latest != before:
                break
            self.sleeper(1.0)
        else:
            raise PrinterCollectionError(
                "printer UI suite did not finish before timeout")
        remote = latest
        name = pathlib.PurePosixPath(remote).name
        if not name or not remote.startswith(ARTIFACT_ROOT + "/"):
            raise PrinterCollectionError("printer returned an unsafe run path")
        output_root = pathlib.Path(output_root).resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        result = self.command_runner(
            ["scp", "-O", "-r", "root@%s:%s" % (self.host, remote),
             str(output_root)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=120)
        if result.returncode != 0:
            raise PrinterCollectionError(
                "unable to copy printer artifacts: %s" %
                " ".join(result.stderr.split())[:300])
        local = output_root / name
        if not (local / "manifest.json").is_file():
            raise PrinterCollectionError(
                "copied printer run has no manifest")
        return local
