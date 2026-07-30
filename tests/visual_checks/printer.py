## Explicit, opt-in collection of non-physical printer UI artifacts.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Explicit, opt-in collection of non-physical printer UI artifacts."""

import base64
import json
import pathlib
import re
import subprocess
import time
import urllib.error
import urllib.request


SAFE_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,252}$")
SAFE_SUITES = frozenset(("UI", "COMPONENT"))
ARTIFACT_ROOT = "/data/feather-ui-tests"


class PrinterCollectionError(RuntimeError):
    pass


class PrinterCollector:
    def __init__(self, host, confirmed_idle=False, timeout=10,
                 requester=None, command_runner=None, clock=None, sleeper=None):
        self.host = str(host or "").strip()
        self.confirmed_idle = bool(confirmed_idle)
        self.timeout = float(timeout)
        self.requester = requester or urllib.request.urlopen
        self.command_runner = command_runner or subprocess.run
        self.clock = clock or time.monotonic
        self.sleeper = sleeper or time.sleep
        if not SAFE_HOST.match(self.host) or "@" in self.host:
            raise PrinterCollectionError("invalid printer host")

    @property
    def base_url(self):
        host = self.host
        if ":" in host and not host.startswith("["):
            host = "[" + host + "]"
        return "http://%s:7125" % host

    def _json(self, method, path, payload=None):
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path, data=data, headers=headers, method=method)
        try:
            response = self.requester(request, timeout=self.timeout)
            with response:
                value = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise PrinterCollectionError(
                "printer API is unavailable: %s" % exc) from exc
        if not isinstance(value, dict) or value.get("error"):
            raise PrinterCollectionError("printer API returned an error")
        return value

    def preflight(self):
        if not self.confirmed_idle:
            raise PrinterCollectionError(
                "live collection requires --confirm-printer-idle")
        value = self._json(
            "GET", "/printer/objects/query?print_stats&extruder&heater_bed"
            "&virtual_sdcard")
        status = dict(value.get("result", {}).get("status", {}))
        print_state = str(
            dict(status.get("print_stats", {})).get("state", "")).lower()
        if print_state in ("printing", "paused"):
            raise PrinterCollectionError("a print is active")
        for name in ("extruder", "heater_bed"):
            target = float(dict(status.get(name, {})).get("target", 0.0) or 0.0)
            if target > 0.0:
                raise PrinterCollectionError("turn heaters off before testing")
        virtual_sd = dict(status.get("virtual_sdcard", {}))
        if bool(virtual_sd.get("is_active", False)):
            raise PrinterCollectionError("virtual SD is active")
        return {
            "print_state": print_state or "unknown",
            "heaters_off": True,
            "virtual_sd_inactive": True,
        }

    def _ssh(self, remote_command, timeout=None):
        result = self.command_runner(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
             "root@" + self.host, remote_command],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout or self.timeout)
        if result.returncode != 0:
            raise PrinterCollectionError(
                "read-only SSH query failed: %s" %
                " ".join(result.stderr.split())[:300])
        return result.stdout.strip()

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
