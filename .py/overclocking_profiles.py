"""Small client for profilectl's public Recovery protocol and operations.

Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
This file may be distributed under the terms of the GNU GPLv3 license.
"""

import json
import os
import re
import signal
import subprocess
import time


BUNDLE = "/opt/config/mod/.bin/tools/overclocking"
STATE = "/data/.forge-x-overclocking/profile"
LEGACY_STATE = "/data/.t113-profilectl"
_FIELD = re.compile(r"[a-z][a-z0-9_]*\Z")
_VALUE = re.compile(r"[A-Za-z0-9._:/,-]*\Z")
# profilectl's stock settings: stock CPU runs at U-Boot's boot clock, and the stock eMMC
# profile is its 25 MHz mode, so a separate 25 MHz entry would repeat it.
STOCK_CPU_MHZ = 1008
STOCK_EMMC_MHZ = 25


class ProfileError(RuntimeError):
    def __init__(self, message, returncode=1):
        super().__init__(message)
        self.returncode = returncode


def parse_snapshot(output):
    result = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if not separator or not _FIELD.fullmatch(key) or not _VALUE.fullmatch(value) or key in result:
            raise ProfileError("Invalid profile-tool snapshot.")
        result[key] = value
    required = ("boot_state", "boot_id", "hardware", "family", "hardware_conflict",
                "current_present", "candidate_present", "stock_dtb_sha256")
    if result.get("format") != "1" or any(key not in result for key in required):
        raise ProfileError("Incomplete or unsupported profile-tool snapshot.")
    return result


class ProfileTool:
    """profilectl remains the sole owner of profiles, backup and boot mutations."""

    def __init__(self, bundle=BUNDLE, state=STATE, legacy=LEGACY_STATE, progress=None):
        self.bundle = os.fspath(bundle)
        self.state = os.fspath(state)
        self.legacy = os.fspath(legacy)
        self.progress = progress
        self.executable = os.path.join(self.bundle, "bin", "t113-profilectl")
        with open(os.path.join(self.bundle, "manifest-v2.json"), encoding="utf-8") as source:
            self.manifest = json.load(source)

    def run(self, *arguments, apply=False, timeout=300):
        command = [self.executable] + (["--apply"] if apply else []) + list(arguments)
        environment = dict(os.environ, T113_STATE_DIR=self.state, T113_PROGRESS="1")
        environment["PATH"] = "/opt/uboot:" + environment.get("PATH", "")
        started = time.monotonic()
        stage, displayed = "Starting command", None
        process = None
        try:
            process = subprocess.Popen(
                command, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, start_new_session=True)
            while True:
                elapsed = time.monotonic() - started
                if elapsed >= timeout:
                    raise subprocess.TimeoutExpired(command, timeout)
                update = (stage, int(elapsed))
                if self.progress is not None and update != displayed:
                    self.progress(" ".join(["t113-profilectl"] + command[1:]), stage, int(elapsed))
                    displayed = update
                try:
                    stdout, stderr = process.communicate(timeout=min(0.2, timeout - elapsed))
                    break
                except subprocess.TimeoutExpired as pending:
                    diagnostic = pending.stderr or b""
                    if isinstance(diagnostic, bytes):
                        diagnostic = diagnostic.decode("utf-8", errors="replace")
                    for line in diagnostic.splitlines():
                        if line.startswith("FGX_PROGRESS:"):
                            stage = line[len("FGX_PROGRESS:"):][:160]
            if self.progress is not None:
                stage = "Command completed" if process.returncode == 0 else "Command failed"
                self.progress(" ".join(["t113-profilectl"] + command[1:]), stage, int(time.monotonic() - started))
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ProfileError("Profile operation could not finish during {} after {} s: {}".format(
                stage, int(time.monotonic() - started), error)) from error
        finally:
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                # A finished communicate() has closed the pipes; Python 3.7.2
                # rejects a second call ("Invalid file object").
                if process.returncode is None:
                    process.communicate()
        if process.returncode:
            diagnostic = "\n".join(line for line in stderr.splitlines() if not line.startswith("FGX_PROGRESS:"))
            detail = (diagnostic.strip() or stdout.strip())[-2000:]
            raise ProfileError(detail or "Profile operation failed.", process.returncode)
        return stdout

    def prepare(self):
        # One canonical migration command owns the shared lock and original state.
        self.run("migrate-state", self.legacy, apply=True)
        report = self.snapshot()
        if report["boot_state"] == "promotion-cleanup-required":
            self.run("cleanup", apply=True)
            report = self.snapshot()
        return report

    def snapshot(self):
        return parse_snapshot(self.run("snapshot"))

    def choices(self, axis, report):
        """(profile value, list label, frequency) per choice. Labels lead with the gain over
        stock: a CPU percentage, an eMMC multiple."""
        if axis == "cpu":
            return [("stock", "STOCK CPU  //  {} MHz".format(STOCK_CPU_MHZ), "{} MHz".format(STOCK_CPU_MHZ))] + [
                (row["alias"], "+{}%  //  {} MHz{}".format(
                    round((row["exact_mhz"] / STOCK_CPU_MHZ - 1) * 100), row["exact_mhz"],
                    "  //  EXPERIMENTAL" if row.get("warning") else ""), "{} MHz".format(row["exact_mhz"]))
                for row in self.manifest["cpu"]]
        if axis != "emmc":
            raise ProfileError("Unknown hardware setting.")
        family = next((row for row in self.manifest["hardware"]
                       if row["family_id"] == report["family"]), None)
        if family is None:
            return []
        return [("stock", "STOCK eMMC  //  {} MHz".format(STOCK_EMMC_MHZ), "{} MHz".format(STOCK_EMMC_MHZ))] + [
            (mhz, "{:g}x  //  {} MHz".format(int(mhz) / STOCK_EMMC_MHZ, mhz), mhz + " MHz")
            for mhz in family["frequencies_mhz"].split(",") if int(mhz) != STOCK_EMMC_MHZ]

    def hardware_problem(self, report):
        if report["hardware_conflict"] != "0":
            return "Detected eMMC identity and runtime mode disagree. No profile was changed."
        if report["hardware"] != "known":
            return "This eMMC has not been validated with the available profiles."
        state = report["boot_state"]
        if state in ("unknown/custom", "stock-active-with-unknown-remnants"):
            detail = ("Stock is active, but old custom boot entries remain." if
                      state == "stock-active-with-unknown-remnants" else "Custom boot settings must be restored before tuning.")
            for key, label in (("unknown_boot_files", "Boot files"), ("unknown_boot_variables", "Boot entries")):
                if report.get(key):
                    detail += "\n{}: {}".format(label, report[key].replace(",", ", "))
            return detail
        if state == "inconsistent":
            if report.get("stock_helpers_valid") == "0":
                return "Protected stock boot commands were modified. Manual boot repair is required before tuning."
            if report.get("missing_boot_dependencies"):
                return "Boot commands are missing: " + report["missing_boot_dependencies"].replace(",", ", ")
            if report.get("unsafe_boot_paths"):
                return "Custom boot files use unsupported paths. Manual boot repair is required before tuning."
            return "Saved hardware settings or boot files are damaged or do not match. Manual boot repair is required."
        if state in ("known-cleanup-required", "stock-restore-cleanup-required"):
            return "A previous hardware settings change was interrupted. Restore stock settings before tuning."
        return ""

    def can_restore_stock(self, report):
        return (report.get("manual_recovery_required") != "yes" and report["boot_state"] in (
                    "unknown/custom", "stock-active-with-unknown-remnants",
                    "known-cleanup-required", "stock-restore-cleanup-required")
                and report["stock_dtb_sha256"] == self.manifest["canonical_stock"]["sha256"])

    # Each operation decides from the report the user acted on; profilectl rechecks the
    # live state under its lock and refuses a stale request, so no snapshot is repeated here.
    def apply_trial(self, axis, value, report):
        problem = self.hardware_problem(report)
        if problem:
            raise ProfileError(problem)
        if report["candidate_present"] != "no":
            raise ProfileError("Accept or discard the current trial first.")
        if value not in [choice[0] for choice in self.choices(axis, report)]:
            raise ProfileError("This frequency is unavailable for the detected hardware.")
        self.run("test", axis, value, apply=True)

    def trial_state(self, report):
        """Observe actual boot settings; persistent fallback alone is not a verdict."""
        if report["candidate_present"] != "yes":
            return "none"
        if report.get("candidate_valid") != "yes":
            return "inconsistent"
        if report["boot_id"] == report.get("candidate_armed_boot_id"):
            return "reboot-required"
        if self.active(report, "candidate_"):
            return "applied"
        return "fallback" if self.confirmed_active(report) else "unconfirmed"

    def confirmed_active(self, report):
        return self.active(report, "confirmed_")

    @staticmethod
    def active(report, prefix):
        """Whether the loaded DTB and observed clocks match the candidate_ or confirmed_ profile."""
        expected_cpu, expected_emmc = report.get(prefix + "cpu_hz"), report.get(prefix + "emmc_mhz")
        if expected_cpu is None or expected_emmc is None:
            return False
        for loaded, expected in (("cpu_profile", "cpu_profile"), ("emmc_mhz", "emmc_mhz"),
                                 ("emmc_family", "family")):
            actual = report.get("loaded_" + loaded, "unknown")
            if actual != "unknown" and actual != report.get(prefix + expected):
                return False
        cpu = report.get("runtime_cpu_limit_hz", "unknown")
        if cpu == "unknown":
            cpu = report.get("runtime_cpu_hz", "unknown")
        return cpu == expected_cpu and report.get("runtime_emmc_hz") == str(int(expected_emmc) * 1000000)

    def verify_trial(self, report):
        if self.trial_state(report) != "applied":
            raise ProfileError("The requested trial is not active in this boot.")
        if report["boot_state"] not in ("candidate-armed", "candidate-passed"):
            raise ProfileError("Trial boot artifacts are inconsistent: " + report["boot_state"])
        if report.get("candidate_phase") == "passed":
            if report.get("candidate_verified_boot_id") != report["boot_id"]:
                raise ProfileError("The trial was verified in a previous boot. Discard it and retry explicitly.")
        else:
            self.run("verify", apply=True)

    def accept(self):
        """Promote a trial verified in this boot; profilectl refuses an unverified one."""
        self.run("enable", apply=True)

    def discard(self, report):
        if report["candidate_present"] != "yes":
            return False
        reboot = self.trial_state(report) != "reboot-required" and not self.confirmed_active(report)
        self.run("cancel", apply=True)
        return reboot

    def restore_stock(self, report):
        # Never replace the current firmware's stock DTB with a historical one.
        if report["stock_dtb_sha256"] != self.manifest["canonical_stock"]["sha256"]:
            raise ProfileError(
                "Installed stock boot artifacts differ from this tuning bundle. "
                "Use the installed firmware's restore mechanism; original backups are preserved.")
        if report["boot_state"] == "inconsistent" or report.get("manual_recovery_required") == "yes":
            raise ProfileError(self.hardware_problem(report) or "Manual boot repair is required before restoring stock.")
        if report["boot_state"] == "clean-stock":
            return not self.confirmed_active(report)
        if report["boot_state"] in ("unknown/custom", "stock-active-with-unknown-remnants",
                                     "known-cleanup-required", "stock-restore-cleanup-required"):
            arguments = ("cleanup",)
            if report["boot_state"] in ("unknown/custom", "stock-active-with-unknown-remnants"):
                arguments = ("--danger-cleanup-unknown",) + arguments
            self.run(*arguments, apply=True)
            if self.snapshot()["boot_state"] != "clean-stock":
                raise ProfileError("Boot configuration is not clean stock after recovery. No trial was started.")
            return True
        if report["candidate_present"] == "yes":
            self.run("cancel", apply=True)
        self.run("rollback", apply=True)
        return True
