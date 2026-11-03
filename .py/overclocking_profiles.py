"""Small client for profilectl's public Recovery protocol and operations.

Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
This file may be distributed under the terms of the GNU GPLv3 license.
"""

import json
import os
import re
import signal
import subprocess
import sys
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


# Diagnostics are an allowlisted view of the protocol, not an environment dump.
_DIAGNOSTIC_FIELDS = (
    "hardware", "family", "cid_status", "model_identity",
    "runtime_family_evidence", "runtime_emmc_hz", "available_emmc_mhz",
    "runtime_timing", "runtime_voltage", "runtime_cpu_hz", "runtime_cpu_limit_hz",
    "boot_state", "pending_boot", "stock_helpers_valid", "stock_helper_mismatches",
    "boot_graph_variables", "boot_file_references",
    "missing_boot_dependencies", "unsafe_boot_paths", "unknown_boot_variables", "unknown_boot_files",
    "stock_dtb_sha256",
    "loaded_cpu_profile", "loaded_emmc_mhz", "loaded_emmc_family", "loaded_source_sha256",
    "current_present", "current_valid", "candidate_present", "candidate_valid", "candidate_phase",
    "manual_recovery_required", "cleanup_required", "cleanup_danger_required")


def diagnostic_text(value, limit=4000):
    """Make utility output safe to log. profilectl redacts named U-Boot values
    itself; this catches a raw CID (it holds the chip serial) or MAC address
    that reaches any message, and control characters that could forge records."""
    value = re.sub(r"(?i)(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])", "<cid-redacted>", value)
    value = re.sub(r"(?i)(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", "<mac-redacted>", value)
    lines = []
    for line in value.splitlines():
        if line.startswith("FGX_PROGRESS:"):
            continue
        lines.append("".join(char if char.isprintable() else "?" for char in line))
    value = "\n".join(lines)
    if len(value) > limit:
        value = "<truncated>\n" + value[-limit:]
    return value


def product_name(report):
    """The CID Product Name, six ASCII characters such as "58A398"."""
    identity = report.get("model_identity", "").split(":")
    if len(identity) == 4 and re.fullmatch(r"[0-9a-f]{12}", identity[2]):
        name = bytes.fromhex(identity[2]).decode("ascii", errors="replace")
        return "".join(char if char.isprintable() else "?" for char in name)
    return "unavailable"


def emmc_type(report):
    """The observed bus mode and signal voltage, as /sys/kernel/debug/mmc0/ios numbers them."""
    timing, voltage = report.get("runtime_timing") or "unknown", report.get("runtime_voltage") or "unknown"
    mode = {"0": "Legacy", "1": "HS52", "9": "HS200", "unknown": "timing unknown"}.get(
        timing, "timing spec " + timing)
    volts = {"0": "3.3 V", "1": "1.8 V", "unknown": "voltage unknown"}.get(
        voltage, "signal voltage " + voltage)
    return mode + " / " + volts


def emmc_description(report):
    return "{} / {}".format(product_name(report), emmc_type(report))


def log_diagnostic(event, **fields):
    print("Tuning diagnostic: " + json.dumps(dict(event=event, **fields), sort_keys=True),
          file=sys.stderr, flush=True)


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
    required = ("boot_state", "boot_id", "hardware", "family",
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

    def run(self, *arguments, apply=False, timeout=300, logged_stdout=4000):
        """logged_stdout bounds the stdout kept in the command record; 0 omits it."""
        command = [self.executable] + (["--apply"] if apply else []) + list(arguments)
        environment = dict(os.environ, T113_STATE_DIR=self.state, T113_PROGRESS="1")
        environment["PATH"] = "/opt/uboot:" + environment.get("PATH", "")
        started = time.monotonic()
        stage, displayed = "Starting command", None
        process, operation_error = None, None
        stdout, stderr, failure = "", "", "none"
        # Arguments can include local paths; the operation name/flags are sufficient.
        operation = " ".join(arg for arg in arguments if "/" not in arg)
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
            for line in stderr.splitlines():
                if line.startswith("FGX_PROGRESS:"):
                    stage = line[len("FGX_PROGRESS:"):][:160]
            if self.progress is not None:
                result_stage = "Command completed" if process.returncode == 0 else "Command failed"
                self.progress(" ".join(["t113-profilectl"] + command[1:]), result_stage,
                              int(time.monotonic() - started))
        except (OSError, subprocess.TimeoutExpired) as error:
            operation_error = error
            failure = "timeout" if isinstance(error, subprocess.TimeoutExpired) else "launch"
        except BaseException:
            failure = "client"
            raise
        finally:
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                # A finished communicate() has closed the pipes; Python 3.7.2
                # rejects a second call ("Invalid file object").
                if process.returncode is None:
                    stdout, stderr = process.communicate()
            returncode = process.returncode if process is not None else None
            if failure == "none" and returncode:
                failure = "exit"
            log_diagnostic("command", command="t113-profilectl " + diagnostic_text(operation, 160),
                           apply=apply, phase=diagnostic_text(stage, 160),
                           elapsed_s=round(time.monotonic() - started, 3), exit_code=returncode,
                           failure=failure, stdout=diagnostic_text(stdout, logged_stdout) if logged_stdout else "",
                           stderr=diagnostic_text(stderr))
        if operation_error is not None:
            detail = diagnostic_text(stderr).strip()
            raise ProfileError("Profile operation could not finish during {} after {} s: {}{}".format(
                stage, int(time.monotonic() - started), diagnostic_text(str(operation_error)),
                "\n" + detail if detail else "")) from operation_error
        if process.returncode:
            diagnostic = "\n".join(line for line in stderr.splitlines() if not line.startswith("FGX_PROGRESS:"))
            detail = diagnostic_text(diagnostic.strip() or stdout.strip(), 2000)
            raise ProfileError(detail or "Profile operation failed.", process.returncode)
        return stdout

    def prepare(self):
        # One canonical migration command owns the shared lock and original state.
        self.run("migrate-state", self.legacy, apply=True)
        report = self.snapshot()
        if report["boot_state"] == "promotion-cleanup-required":
            self.run("cleanup", apply=True)
            report = self.snapshot()
        if report["boot_state"] not in ("clean-stock", "clean-managed", "candidate-armed", "candidate-passed"):
            self.log_boot_diagnostics()
        return report

    def log_boot_diagnostics(self):
        """Record profilectl's full boot report for support. Observation only: a
        failure is logged and must not hide the repair screen that follows."""
        try:
            self.run("doctor", logged_stdout=16000)
        except ProfileError as error:
            log_diagnostic("boot-diagnostics-failed", detail=diagnostic_text(str(error)))

    def snapshot(self):
        # The contents are logged only after parsing and allowlisting below.
        output = self.run("snapshot", logged_stdout=0)
        try:
            report = parse_snapshot(output)
        except ProfileError as error:
            log_diagnostic("snapshot-invalid", detail=str(error))
            raise
        fields = {key: diagnostic_text(report.get(key, "unavailable"), 256) for key in _DIAGNOSTIC_FIELDS}
        fields["product_name"] = product_name(report)
        fields["stock_dtb_matches"] = report["stock_dtb_sha256"] == self.manifest["canonical_stock"]["sha256"]
        fields["restore_stock_available"] = self.can_restore_stock(report)
        log_diagnostic("snapshot", **fields)
        return report

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
        # profilectl owns eligibility: its list already applies the mode and model restrictions.
        choices = []
        for mhz in filter(None, report.get("available_emmc_mhz", "").split(",")):
            if int(mhz) == STOCK_EMMC_MHZ:
                choices.append(("stock", "STOCK eMMC  //  {} MHz".format(mhz), mhz + " MHz"))
            else:
                choices.append((mhz, "{:g}x  //  {} MHz".format(int(mhz) / STOCK_EMMC_MHZ, mhz), mhz + " MHz"))
        return choices

    def boot_problem(self, report):
        """A boot configuration that must be repaired before any tuning, or ""."""
        state = report["boot_state"]
        if state in ("unknown/custom", "stock-active-with-unknown-remnants"):
            detail = ("Stock boot is active; unrecognized boot objects were found." if
                      state == "stock-active-with-unknown-remnants" else "Custom boot settings must be restored before tuning.")
            for key, label in (("unknown_boot_files", "Boot files"), ("unknown_boot_variables", "Boot entries")):
                if report.get(key):
                    detail += "\n{}: {}".format(label, report[key].replace(",", ", "))
            return detail
        if state == "inconsistent":
            if report.get("stock_helpers_valid") == "0":
                return "Stock boot commands differ: {}. Manual boot repair is required.".format(
                    report.get("stock_helper_mismatches") or "details in diagnostics")
            if report.get("missing_boot_dependencies"):
                return "Boot commands are missing: " + report["missing_boot_dependencies"].replace(",", ", ")
            if report.get("unsafe_boot_paths"):
                return "Custom boot files use unsupported paths. Manual boot repair is required before tuning."
            return "Saved hardware settings or boot files are damaged or do not match. Manual boot repair is required."
        if state in ("known-cleanup-required", "stock-restore-cleanup-required"):
            return "A previous hardware settings change was interrupted. Restore stock settings before tuning."
        return ""

    def axis_problem(self, axis, report):
        """Why a new trial on this axis cannot start, or "". An unsupported eMMC
        blocks only new trials: deciding a pending trial and restoring stock remain."""
        problem = self.boot_problem(report)
        if problem:
            return problem
        # Every generated DTB states the eMMC family, so a CPU profile needs a known
        # mode too. An unknown mode is a property of the interface type, not of this model.
        if report["family"] == "unknown":
            return "This eMMC type is not supported: {}. CPU and eMMC profiles both write eMMC boot " \
                   "settings, so neither can be changed.".format(emmc_type(report))
        # A CPU trial keeps the current eMMC frequency; only a new one must be eligible.
        if axis == "emmc" and report["hardware"] != "supported":
            return "This eMMC model is not supported: {}. eMMC frequency cannot be changed.".format(
                emmc_description(report))
        return ""

    def can_restore_stock(self, report):
        return (report.get("manual_recovery_required") != "yes" and report["boot_state"] in (
                    "unknown/custom", "stock-active-with-unknown-remnants",
                    "known-cleanup-required", "stock-restore-cleanup-required")
                and report["stock_dtb_sha256"] == self.manifest["canonical_stock"]["sha256"])

    # Each operation decides from the report the user acted on; profilectl rechecks the
    # live state under its lock and refuses a stale request, so no snapshot is repeated here.
    def apply_trial(self, axis, value, report):
        problem = self.axis_problem(axis, report)
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
            raise ProfileError(self.boot_problem(report) or "Manual boot repair is required before restoring stock.")
        if report["boot_state"] == "clean-stock":
            return not self.confirmed_active(report)
        if report["boot_state"] in ("unknown/custom", "stock-active-with-unknown-remnants",
                                     "known-cleanup-required", "stock-restore-cleanup-required"):
            arguments = ("cleanup",)
            if report["boot_state"] in ("unknown/custom", "stock-active-with-unknown-remnants"):
                arguments = ("--danger-cleanup-unknown",) + arguments
            self.run(*arguments, apply=True)
            if self.snapshot()["boot_state"] != "clean-stock":
                self.log_boot_diagnostics()
                raise ProfileError("Boot configuration is not clean stock after recovery. No trial was started.")
            return True
        if report["candidate_present"] == "yes":
            self.run("cancel", apply=True)
        self.run("rollback", apply=True)
        return True
