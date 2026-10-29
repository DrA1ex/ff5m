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
    "hardware", "family", "hardware_conflict", "cid_status", "model_identity", "model_support",
    "matched_family", "runtime_family_evidence", "runtime_emmc_hz",
    "runtime_timing", "runtime_voltage", "runtime_cpu_hz", "runtime_cpu_limit_hz",
    "boot_state", "pending_boot", "stock_helpers_valid", "boot_graph_variables", "boot_file_references",
    "missing_boot_dependencies", "unsafe_boot_paths", "unknown_boot_variables", "unknown_boot_files",
    "stock_dtb_sha256",
    "loaded_cpu_profile", "loaded_emmc_mhz", "loaded_emmc_family", "loaded_source_sha256",
    "current_present", "current_valid", "candidate_present", "candidate_valid", "candidate_phase",
    "manual_recovery_required", "cleanup_required", "cleanup_danger_required")


def diagnostic_text(value, limit=4000):
    """Keep useful utility output without specimen IDs or raw boot helper values."""
    value = re.sub(r"(?i)(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])", "<cid-redacted>", value)
    value = re.sub(r"(?i)(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", "<mac-redacted>", value)
    value = re.sub(r"(?im)\b(serial[#_a-z0-9]*|snum|mac(?:_addr)?|wifi_mac|bt_mac|ethaddr)\s*[=:]\s*[^\s;]+",
                   r"\1=<redacted>", value)
    value = re.sub(r"(?im)(\bbootcmd\s*[=:]\s*).*$", r"\1<boot-command-omitted>", value)
    lines, helper_value = [], False
    for line in value.splitlines():
        if line.startswith("FGX_PROGRESS:"):
            continue
        if line in ("Expected:", "Actual:"):
            helper_value = True
            lines.append(line)
        elif helper_value and line.startswith("  "):
            lines.append("  <boot-helper-value-omitted>")
        else:
            helper_value = False
            # Strip control characters so output cannot forge terminal/log records.
            lines.append("".join(char if char.isprintable() else "?" for char in line))
    value = "\n".join(lines)
    if len(value) > limit:
        value = "<truncated>\n" + value[-limit:]
    return value


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
            # snapshot contents are logged only after parsing and allowlisting below.
            log_diagnostic("command", command="t113-profilectl " + diagnostic_text(operation, 160),
                           apply=apply, phase=diagnostic_text(stage, 160),
                           elapsed_s=round(time.monotonic() - started, 3), exit_code=returncode,
                           failure=failure,
                           stdout="" if any(arg in ("snapshot", "doctor") for arg in arguments)
                           else diagnostic_text(stdout), stderr=diagnostic_text(stderr))
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
        return report

    def snapshot(self):
        output = self.run("snapshot")
        try:
            report = parse_snapshot(output)
        except ProfileError as error:
            log_diagnostic("snapshot-invalid", detail=str(error))
            raise
        fields = {key: diagnostic_text(report.get(key, "unavailable"), 256) for key in _DIAGNOSTIC_FIELDS}
        identity = report.get("model_identity", "").split(":")
        if len(identity) == 4 and re.fullmatch(r"[0-9a-f]{12}", identity[2]):
            fields["product_name"] = diagnostic_text(bytes.fromhex(identity[2]).decode("ascii", errors="replace"), 32)
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
