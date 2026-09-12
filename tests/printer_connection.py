## Small host-side Moonraker and SSH connection primitive.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Small host-side Moonraker and SSH connection primitive."""

import json
import math
import re
import subprocess
import urllib.error
import urllib.request


SAFE_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,252}$")
ARTIFACT_ROOT = "/data/feather-ui-tests"


class PrinterConnectionError(RuntimeError):
    pass


class PrinterConnection:
    """Own one host's concrete Moonraker and SSH transport details."""

    def __init__(self, host, timeout=10, requester=None,
                 command_runner=None):
        self.host = str(host or "").strip()
        self.timeout = float(timeout)
        self.requester = requester or urllib.request.urlopen
        self.command_runner = command_runner or subprocess.run
        if not SAFE_HOST.match(self.host) or "@" in self.host:
            raise PrinterConnectionError("invalid printer host")

    @property
    def base_url(self):
        host = self.host
        if ":" in host and not host.startswith("["):
            host = "[" + host + "]"
        return "http://%s:7125" % host

    @property
    def web_base_url(self):
        host = self.host
        if ":" in host and not host.startswith("["):
            host = "[" + host + "]"
        return "http://%s/" % host

    @property
    def ssh_target(self):
        return "root@" + self.host

    @property
    def scp_target(self):
        if ":" in self.host and not self.host.startswith("["):
            return "root@[%s]" % self.host
        return self.ssh_target

    def request_json(self, method, path, payload=None, timeout=None):
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path, data=data, headers=headers, method=method)
        try:
            response = self.requester(
                request,
                timeout=self.timeout if timeout is None else float(timeout))
            with response:
                value = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise PrinterConnectionError(
                "printer API is unavailable: %s" % exc) from exc
        if not isinstance(value, dict) or value.get("error"):
            raise PrinterConnectionError("printer API returned an error")
        return value

    def ssh(self, remote_command, timeout=None):
        try:
            result = self.command_runner(
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                 self.ssh_target, remote_command],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=timeout or self.timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            raise PrinterConnectionError("SSH command failed") from exc
        if result.returncode != 0:
            raise PrinterConnectionError(
                "SSH command failed: %s" %
                " ".join(result.stderr.split())[:300])
        return result.stdout.strip()

    def printer_status(self):
        value = self.request_json(
            "GET", "/printer/objects/query?print_stats&extruder&heater_bed"
            "&virtual_sdcard&pause_resume")
        result = value.get("result")
        if not isinstance(result, dict) or not isinstance(
                result.get("status"), dict):
            raise PrinterConnectionError("printer safety status is incomplete")
        status = dict(result["status"])
        if any(name not in status for name in (
                "print_stats", "extruder", "heater_bed", "virtual_sdcard",
                "pause_resume")):
            raise PrinterConnectionError("printer safety status is incomplete")
        try:
            print_stats = dict(status["print_stats"])
            extruder = dict(status["extruder"])
            heater_bed = dict(status["heater_bed"])
            virtual_sd = dict(status["virtual_sdcard"])
            pause_resume = dict(status["pause_resume"])
        except (TypeError, ValueError) as exc:
            raise PrinterConnectionError(
                "printer safety status is incomplete") from exc
        raw_print_state = print_stats.get("state")
        if not isinstance(raw_print_state, str) or not raw_print_state.strip():
            raise PrinterConnectionError("printer safety status is incomplete")
        print_state = raw_print_state.lower()
        targets = {}
        for name, heater in (("extruder", extruder),
                             ("heater_bed", heater_bed)):
            if "target" not in heater:
                raise PrinterConnectionError(
                    "printer safety status is incomplete")
            try:
                target = float(heater.get("target"))
            except (TypeError, ValueError) as exc:
                raise PrinterConnectionError(
                    "printer safety status is incomplete") from exc
            if not math.isfinite(target) or target < 0.0:
                raise PrinterConnectionError(
                    "printer safety status is incomplete")
            targets[name] = target
        if ("is_active" not in virtual_sd
                or not isinstance(virtual_sd["is_active"], bool)):
            raise PrinterConnectionError("printer safety status is incomplete")
        virtual_sd_active = virtual_sd["is_active"]
        if ("is_paused" not in pause_resume
                or not isinstance(pause_resume["is_paused"], bool)):
            raise PrinterConnectionError("printer safety status is incomplete")
        return {
            "print_state": print_state or "unknown",
            "heater_targets": targets,
            "virtual_sd_active": virtual_sd_active,
            "paused": pause_resume["is_paused"],
        }

    def require_safe_idle(self):
        status = self.printer_status()
        if status["print_state"] in ("printing", "paused"):
            raise PrinterConnectionError("a print is active")
        if any(value > 0.0 for value in status["heater_targets"].values()):
            raise PrinterConnectionError("turn heaters off before testing")
        if status["virtual_sd_active"]:
            raise PrinterConnectionError("virtual SD is active")
        # A pause flag outlives the print that set it and makes every later
        # PAUSE a no-op, so an idle printer must not carry one.
        if status["paused"]:
            raise PrinterConnectionError(
                "printer is left paused, run CLEAR_PAUSE")
        return {
            "print_state": status["print_state"],
            "heaters_off": True,
            "virtual_sd_inactive": True,
            "not_paused": True,
        }
