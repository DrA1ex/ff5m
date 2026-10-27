"""Guided single-axis Recovery tuning using the existing Recovery view.

Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
This file may be distributed under the terms of the GNU GPLv3 license.

profilectl's candidate is the only unresolved decision: while it exists,
Recovery opens this page on entry and every reboot returns here. Opening the
page changes nothing; accept, discard and restore are explicit actions.
"""

import os
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, wait

import overclocking_tests
from overclocking_profiles import ProfileTool, ProfileError
from overclocking_results import TestResults
from overclocking_tests import REFERENCE_BYTES, cleanup_owned_files, reference_path


def profile_identity(report):
    if report.get("candidate_present") == "yes":
        return report["candidate_dtb_sha256"]
    return report.get("current_dtb_sha256", report["stock_dtb_sha256"])


def mhz(value):
    try:
        return "{:g} MHz".format(int(value) / 1000000)
    except (TypeError, ValueError):
        return "unavailable"


AXIS_NAMES = {"cpu": "CPU", "emmc": "eMMC"}
TEST_NAMES = {"cpu-stability": "CPU STABILITY", "emmc-prepare": "eMMC REFERENCE", "emmc-integrity": "eMMC INTEGRITY"}
# Within one axis's results the axis is implied: "INTEGRITY", not "eMMC INTEGRITY". Profiles accepted
# before the eMMC test became one read-then-write run still list its two parts.
SHORT_TEST_NAMES = {"cpu-stability": "STABILITY", "emmc-integrity": "INTEGRITY", "emmc-read": "READ",
                    "emmc-write": "WRITE"}
REFERENCE_MIB = REFERENCE_BYTES // (1024 * 1024)
TEST_DESCRIPTIONS = {"cpu-stability": "180 s CPU and memory load on 2 cores",
                     "emmc-prepare": "Writing {} MiB of known data, then reading it back".format(REFERENCE_MIB),
                     "emmc-integrity": "{0} MiB read check, then {0} MiB write check"
                     .format(REFERENCE_MIB)}


def outcome_label(status):
    return status.upper().replace("-", " ") if status else "NOT RUN"


def outcomes_text(outcomes):
    return ", ".join(SHORT_TEST_NAMES.get(kind, kind) + " " + outcome_label(status) for kind, status in outcomes.items())


def outcome_color(view, status):
    return {"passed": view.SUCCESS, "failed": view.DANGER}.get(
        status, view.MUTED if status in (None, "not-run") else view.WARNING)


class TuningOperation:
    """One blocking operation, with progress drawn only by its calling thread."""

    def __init__(self, detail, draw):
        self.detail, self.draw = detail, draw
        self.started = time.monotonic()
        self._lock = threading.Lock()
        self._phase = ("Starting operation. Please wait...", "")

    def report(self, stage, activity=""):
        with self._lock:
            if self._phase == (stage, activity):
                return
            self._phase = (stage, activity)
        print("Tuning phase: {} / {} / {}".format(self.detail, stage, activity), file=sys.stderr, flush=True)

    def snapshot(self):
        with self._lock:
            return self._phase + (int(time.monotonic() - self.started),)

    def run(self, function, *args, **kwargs):
        print("Tuning started: " + self.detail, file=sys.stderr, flush=True)
        displayed = self.snapshot()
        self.draw(self.detail, *displayed)
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="hardware-tuning") as worker:
            task = worker.submit(function, *args, **kwargs)
            try:
                while not task.done():
                    wait((task,), timeout=0.2)
                    update = self.snapshot()
                    if not task.done() and update != displayed:
                        self.draw(self.detail, *update)
                        displayed = update
                result = task.result()
            except Exception as error:
                if not task.done() or task.exception() is not error:
                    # A display failure lets the work finish: killing profilectl
                    # mid-mutation is worse than a missing frame.
                    print("Tuning display failed; waiting for: " + self.detail, file=sys.stderr, flush=True)
                    wait((task,))
                    print("Tuning work after display failure: {}".format(task.exception() or "completed"),
                          file=sys.stderr, flush=True)
                stage, activity, elapsed = self.snapshot()
                # The cause comes first: short error labels clip the context, not the cause.
                message = "{}\n{} {}{} ({} s)".format(
                    str(error) or type(error).__name__, self.detail, stage,
                    ": " + activity if activity else "", elapsed)
                # The caller that handles the chained error logs its traceback.
                print("Tuning failed: " + message, file=sys.stderr, flush=True)
                if isinstance(error, ProfileError):
                    raise ProfileError(message, error.returncode) from error
                raise RuntimeError(message) from error
        print("Tuning completed: {} ({:.1f} s)".format(self.detail, time.monotonic() - self.started),
              file=sys.stderr, flush=True)
        return result


class TuningUI:
    def __init__(self, view, reboot, follow_trial, profiles=None, results=None,
                 workload=overclocking_tests.run):
        """reboot() restarts the printer, returning to Recovery while a trial is
        pending; follow_trial() makes a reset do the same for the current state."""
        self.view, self.reboot_printer, self.follow_trial = view, reboot, follow_trial
        self.profiles = profiles or ProfileTool(progress=self.profile_progress)
        self.results = results or TestResults()
        self.workload = workload
        self._operation = None

    def operation(self, detail, function, *args, **kwargs):
        self._operation = TuningOperation(detail, self.draw_progress)
        try:
            return self._operation.run(function, *args, **kwargs)
        finally:
            self._operation = None

    def report_progress(self, stage, activity=""):
        # Every blocking call runs in operation(); only its worker reports.
        if self._operation is not None:
            self._operation.report(stage, activity)

    def profile_progress(self, command, stage, elapsed):
        self.report_progress(stage, command)

    def draw_progress(self, detail, stage, activity, elapsed=None):
        """Without elapsed, the frame is static: no timer that would look stuck at 0 s."""
        v = self.view
        commands = v.begin_page("HARDWARE TUNING", "PLEASE WAIT")
        commands += [v.text(400, 120, detail, max_width=700, max_height=80, wrap=True),
                     v.text(400, 220, stage, font=v.FONT_MEDIUM, max_width=700, max_height=70, wrap=True),
                     v.text(400, 310, activity, color=v.MUTED, font=v.FONT_LABEL, max_width=740,
                            max_height=60, wrap=True)]
        if elapsed is not None:
            commands.append(v.text(400, 400, "ELAPSED {} s".format(elapsed), color=v.ACCENT, font=v.FONT_MEDIUM))
        v.send(commands)

    def reboot(self):
        self.view.progress("RESTARTING PRINTER", "Please wait...")
        self.reboot_printer()
        return True

    def detail_rows(self, y, detail, max_lines=3, quiet_notes=False):
        """Draw a detail from y: its sentences, then its "Label: value" lines
        as a table. Return the commands and the y below them. With quiet_notes,
        sentences beside a table are small and muted: the table is the result.
        A value's secondary part after " | ", such as a ratio to stock, gets
        its own muted column."""
        v = self.view
        lines = detail.split("\n")
        # A one-line detail, such as an error, is a single paragraph.
        sentences = [line for line in lines if len(lines) == 1 or ": " not in line]
        facts = [line.split(": ", 1) for line in lines if len(lines) > 1 and ": " in line]
        quiet = quiet_notes and bool(facts)
        font, color = (v.FONT_LABEL, v.MUTED) if quiet else (v.FONT_MEDIUM, v.TEXT)
        step = v.line_height(v.FONT_MEDIUM)
        commands = []
        for sentence in sentences:
            count = min(max_lines, v.wrapped_lines(sentence, 740, font))
            commands.append(v.text(30, y, sentence, color=color, font=font, h_align="left", v_align="top",
                                   max_width=740, max_height=count * v.line_height(font), wrap=True))
            y += count * v.line_height(font)
        if facts:
            commands.append(v.rule(y + 6))
            y += 16
        for label, value in facts:
            value, _, secondary = value.partition(" | ")
            width = 210 if secondary else 470
            count = min(2, v.wrapped_lines(value, width, v.FONT_MEDIUM))
            # The label's baseline matches the first line of the value.
            commands += [v.text(30, y + 5, label.upper(), color=v.MUTED, font=v.FONT_LABEL, h_align="left",
                                v_align="top", max_width=260),
                         v.text(300, y, value, font=v.FONT_MEDIUM, h_align="left", v_align="top", max_width=width,
                                max_height=count * step, wrap=True)]
            if secondary:
                commands.append(v.text(520, y, secondary, color=v.MUTED, font=v.FONT_MEDIUM, h_align="left",
                                       v_align="top", max_width=250))
            y += 28 + (count - 1) * step
        return commands, y

    def recover_configuration(self, report):
        problem = self.profiles.hardware_problem(report)
        available = self.profiles.can_restore_stock(report)
        v = self.view
        commands = v.begin_page("HARDWARE SETTINGS NEED REPAIR")
        rows, y = self.detail_rows(84, problem)
        commands += rows + [v.rule(y + 10)]
        if available:
            commands.append(v.text(30, y + 30, "RESTORE STOCK WILL", color=v.ACCENT, font=v.FONT_LABEL,
                                   h_align="left", v_align="top"))
            steps = ("Return CPU and eMMC to stock settings.", "Remove old boot entries and files; keep backups.")
        else:
            commands.append(v.text(30, y + 30, "AUTOMATIC RESTORE IS UNAVAILABLE", color=v.WARNING,
                                   font=v.FONT_LABEL, h_align="left", v_align="top"))
            steps = ("Keep this firmware; collect diagnostics for support.",)
        for index, line in enumerate(steps):
            commands.append(v.text(30, y + 56 + index * 31, line, font=v.FONT_MEDIUM, h_align="left",
                                   v_align="top", max_width=740))
        commands.extend(v.action_row((("recovery.back", "BACK"),) +
                                     ((("recovery.restore", "RESTORE STOCK", True),) if available else ())))
        v.send(commands)
        if v.wait_action() != "recovery.restore" or not available:
            return False
        if not v.confirm("RESTORE STOCK", "The custom boot entries and files listed on the previous screen are "
                         "removed. Original backups are preserved.", "RESTORE & REBOOT",
                         headline="Restore stock CPU and eMMC settings?"):
            return False
        self.operation("Restoring stock hardware settings.", self.profiles.restore_stock, report)
        return True

    def run(self):
        """Return True after requesting a reboot, False to return to the Recovery menu."""
        report = self.operation("Step 1/5: Checking hardware and saved settings.", self.profiles.prepare)
        # This Recovery draws frames, so a reset during a trial may return here.
        self.operation("Step 2/5: Preparing the Recovery return path.", self.follow_trial)
        interrupted = self.operation("Step 3/5: Checking interrupted tests.", self.results.take_interrupted)
        if interrupted:
            self.view.message("INTERRUPTED TUNING TEST", "It did not record completion on the previous run. The "
                              "cause of the reset or process exit is unknown.",
                              headline=TEST_NAMES.get(interrupted, interrupted) + " was interrupted",
                              headline_color=self.view.WARNING)
        self.operation("Step 4/5: Cleaning up temporary test files.", cleanup_owned_files, self.results.root,
                       progress=self.report_progress)
        self.draw_progress("Step 5/5: Opening hardware tuning.", "Checking the active profile", "")
        print("Tuning: opening the hardware settings page", file=sys.stderr, flush=True)

        while True:
            try:
                if self.profiles.hardware_problem(report):
                    return self.recover_configuration(report) and self.reboot()
                if report["candidate_present"] == "yes":
                    decision = self.trial(report)
                    if decision is None:
                        return False
                    if decision:
                        return True
                    # The trial was accepted or discarded: observe the new confirmed profile.
                    report = self.operation("Checking the saved settings.", self.profiles.snapshot)
                    continue
                if not self.profiles.confirmed_active(report):
                    return self.confirmed_mismatch(report)
                # Tests, a cancelled choice and a declined confirmation change no profile
                # state, so the page is redrawn from the same observation.
                action = self.tuning_page(report)
                if action == "tuning.back":
                    return False
                if action in ("tuning.cpu", "tuning.emmc"):
                    if self.select_profile(action.split(".")[1], report):
                        return True
                elif action == "tuning.tests":
                    self.system_tests(report)
                elif action == "tuning.stock":
                    if self.view.confirm("RESTORE STOCK", "This replaces the confirmed overclock for the installed "
                                         "firmware. Original backups are preserved.", "RESTORE & REBOOT",
                                         headline="Restore stock CPU and eMMC settings?"):
                        if self.operation("Restoring stock hardware settings.", self.profiles.restore_stock, report):
                            return self.reboot()
                        self.view.message("RESTORE STOCK", "Nothing was changed.",
                                          headline="Stock settings are already selected.")
                elif action == "tuning.finish":
                    return self.reboot()
            except Exception as error:
                traceback.print_exc()
                # An operation error is its cause, then the step it happened in.
                cause, _, context = (str(error) or type(error).__name__).partition("\n")
                self.view.message("TUNING OPERATION FAILED", context, headline=cause, headline_color=self.view.DANGER)
                return False

    def confirmed_mismatch(self, report):
        v = self.view
        saved_cpu, saved_emmc = mhz(report.get("confirmed_cpu_hz")), str(report.get("confirmed_emmc_mhz")) + " MHz"
        active_cpu, active_emmc = mhz(report.get("runtime_cpu_limit_hz")), mhz(report.get("runtime_emmc_hz"))
        stock = " (STOCK)" if report.get("confirmed_cpu_profile") == "stock" else ""
        commands = v.begin_page("HARDWARE SETTINGS CHECK")
        commands += [v.text(250, 88, "CPU", color=v.ACCENT, font=v.FONT_LABEL, h_align="left"),
                     v.text(560, 88, "eMMC", color=v.ACCENT, font=v.FONT_LABEL, h_align="left")]
        rows = (("SAVED PROFILE", saved_cpu + stock, saved_emmc, v.TEXT, v.TEXT),
                ("ACTIVE LIMIT", active_cpu, active_emmc, v.TEXT if active_cpu == saved_cpu else v.WARNING,
                 v.TEXT if active_emmc == saved_emmc else v.WARNING),
                ("CPU NOW", mhz(report.get("runtime_cpu_hz")), "", v.MUTED, v.MUTED))
        for index, (label, cpu, emmc, cpu_color, emmc_color) in enumerate(rows):
            y = 122 + index * 38
            commands += [v.text(30, y, label, color=v.MUTED, font=v.FONT_LABEL, h_align="left", max_width=210),
                         v.text(250, y, cpu, color=cpu_color, font=v.FONT_VALUE, h_align="left", max_width=290),
                         v.text(560, y, emmc, color=emmc_color, font=v.FONT_VALUE, h_align="left", max_width=210)]
        commands += [v.rule(226),
                     v.text(30, 242, "Active clocks differ from the saved profile or cannot be read. Nothing "
                            "was changed. BACK leaves without rebooting.",
                            font=v.FONT_MEDIUM, h_align="left", v_align="top", max_width=740, max_height=90,
                            wrap=True)]
        commands.extend(v.action_row((("mismatch.back", "BACK TO MENU"), ("mismatch.stock", "RESTORE STOCK", True),
                                      ("mismatch.reboot", "REBOOT"))))
        v.send(commands)
        action = v.wait_action()
        if action == "mismatch.reboot":
            return self.reboot()
        if action == "mismatch.stock" and v.confirm(
                "RESTORE STOCK", "The printer reboots. Original backups are preserved.", "RESTORE & REBOOT",
                headline="Restore stock CPU and eMMC settings?"):
            if self.operation("Restoring stock hardware settings.", self.profiles.restore_stock, report):
                return self.reboot()
        return False

    def tuning_page(self, report):
        v = self.view
        acceptance = self.results.acceptance(profile_identity(report))
        outcomes = acceptance["tests"] if acceptance else {}
        cpu = mhz(report["confirmed_cpu_hz"])
        if report.get("confirmed_cpu_profile") == "stock":
            cpu += " (STOCK)"
        commands = v.begin_page("HARDWARE TUNING", "CONFIRMED PROFILE")
        commands += [v.text(30, 92, "CURRENT HARDWARE SETTINGS", color=v.ACCENT, font=v.FONT_LABEL,
                            h_align="left", max_width=740),
                     v.text(30, 126, "CPU " + cpu, font=v.FONT_VALUE, h_align="left", max_width=370),
                     v.text(415, 126, "eMMC " + report["confirmed_emmc_mhz"] + " MHz", font=v.FONT_VALUE,
                            h_align="left", max_width=355),
                     v.rule(152),
                     v.text(30, 180, "TESTS", color=v.MUTED, font=v.FONT_MEDIUM, h_align="left")]
        # The tests recorded when this profile was accepted, one value each colored by its outcome,
        # in the label's font so the row shares one baseline.
        x = 130
        for kind, outcome in outcomes.items():
            text = "{} {} {}".format("CPU" if kind.startswith("cpu") else "eMMC", SHORT_TEST_NAMES.get(kind, kind),
                                     outcome_label(outcome))
            commands.append(v.text(x, 180, text, color=outcome_color(v, outcome), font=v.FONT_MEDIUM, h_align="left",
                                   max_width=max(1, 770 - x)))
            x += v.text_width(text + "  ", v.FONT_MEDIUM)
        if not outcomes:
            commands.append(v.text(x, 180, "NOT RECORDED", color=v.MUTED, font=v.FONT_MEDIUM, h_align="left"))
        actions = (("tuning.cpu", "CPU FREQUENCY"), ("tuning.emmc", "eMMC FREQUENCY"),
                   ("tuning.tests", "SYSTEM TESTS"), ("tuning.stock", "RESTORE STOCK", True),
                   ("tuning.finish", "BOOT FORGE-X", False, True), ("tuning.back", "BACK"))
        for index, item in enumerate(actions):
            commands.append(v.button(item[0], 30 + index % 2 * 385, 216 + index // 2 * 76,
                                     355, 65, item[1], *item[2:]))
        v.send(commands)
        return v.wait_action()

    def select_profile(self, axis, report):
        problem = self.profiles.hardware_problem(report)
        if problem:
            raise ProfileError(problem)
        choices = self.profiles.choices(axis, report)
        if not choices:
            raise ProfileError("No supported frequencies for this hardware / " + report["family"])
        selected = self.view.choose(AXIS_NAMES[axis] + " FREQUENCY", choices, lambda item: item[1])
        if selected is None:
            return False
        detail = "Only this setting changes. The printer reboots once into Recovery to check it; the confirmed " \
                 "profile stays as the fallback."
        if "EXPERIMENTAL" in selected[1]:
            detail += " This frequency is unstable on some printers."
        reference = axis == "emmc" and not os.path.exists(reference_path(self.results.root))
        if reference:
            detail += " A {} MiB eMMC test file is written first.".format(REFERENCE_MIB)
        if not self.view.confirm("APPLY TRIAL PROFILE", detail, "APPLY & REBOOT",
                                 headline="Try {} {}?".format(AXIS_NAMES[axis], selected[2])):
            return False
        if reference and not self.prepare_reference(report):
            return False
        self.operation("Preparing the trial boot settings.", self.profiles.apply_trial, axis, selected[0], report)
        return self.reboot()

    def result(self, kind, report):
        return self.results.result(kind, profile_identity(report), report["boot_id"])

    def trial(self, report):
        """Return True after requesting a reboot, None to leave, False to re-observe
        after a decision changed the trial. Other actions redraw this page."""
        state = self.profiles.trial_state(report)
        axis = report.get("candidate_axis", "cpu")
        if state == "reboot-required":
            action = self.view.message("TRIAL READY TO BOOT", "Reboot into Recovery to apply and inspect the selected profile.",
                                       extra=("trial.reboot", "REBOOT TO APPLY"))
            return self.reboot() if action == "trial.reboot" else None
        if state != "applied":
            while True:
                if self.not_applied(report, axis, state) != "trial.discard":
                    return None
                if self.view.confirm("DISCARD UNCONFIRMED TRIAL", "", "DISCARD",
                                     headline="Return to the last confirmed settings?"):
                    return self.discard(report)
        # Keep mandatory boot/artifact checks separate from optional stress outcomes.
        mandatory_error = ""
        try:
            self.operation("Verifying the trial boot settings.", self.profiles.verify_trial, report)
        except ProfileError as error:
            mandatory_error = str(error)
        kinds = ("cpu-stability",) if axis == "cpu" else ("emmc-integrity",)
        if axis == "cpu":
            before, now = mhz(report["confirmed_cpu_hz"]), mhz(report["candidate_cpu_hz"])
            unchanged = "eMMC {} MHz".format(report["candidate_emmc_mhz"])
        else:
            before, now = report["confirmed_emmc_mhz"] + " MHz", report["candidate_emmc_mhz"] + " MHz"
            unchanged = "CPU " + mhz(report["candidate_cpu_hz"])
        v = self.view
        while True:
            # Re-read on every draw: a test run from this page records new results.
            results = {kind: self.result(kind, report) for kind in kinds}
            outcomes = {kind: result["status"] if result else "not-run" for kind, result in results.items()}
            commands = v.begin_page(AXIS_NAMES[axis] + " TRIAL", "UNCONFIRMED PROFILE")
            commands += [v.text(30, 96, "{} {} APPLIED".format(AXIS_NAMES[axis], now), color=v.ACCENT,
                                font=v.FONT_HEADLINE, h_align="left", max_width=740),
                         v.text(30, 136, "BEFORE " + before, color=v.MUTED, font=v.FONT_MEDIUM, h_align="left",
                                max_width=340),
                         v.text(400, 136, "NOW " + now, color=v.SUCCESS, font=v.FONT_MEDIUM, h_align="left",
                                max_width=370)]
            if mandatory_error:
                commands.append(v.text(30, 158, "BOOT / DTB CHECK FAILED: " + mandatory_error, color=v.DANGER,
                                       font=v.FONT_LABEL, h_align="left", v_align="top", max_width=740,
                                       max_height=48, wrap=True))
            else:
                commands.append(v.text(30, 170, "UNCHANGED: {}   BOOT / DTB VERIFIED".format(unchanged),
                                       color=v.MUTED, font=v.FONT_LABEL, h_align="left", max_width=740))
            commands.append(v.rule(212))
            # One row per test: its name, its outcome and the speed it measured.
            for index, (kind, outcome) in enumerate(outcomes.items()):
                y = 232 + index * 32
                commands += [v.text(30, y, TEST_NAMES[kind], font=v.FONT_MEDIUM, h_align="left", max_width=210),
                             v.text(250, y, outcome_label(outcome), color=outcome_color(v, outcome),
                                    font=v.FONT_MEDIUM, h_align="left", max_width=170)]
                speed = results[kind] and results[kind].get("speed")
                if speed:
                    commands.append(v.text(430, y, speed, font=v.FONT_MEDIUM, h_align="left", max_width=340))
            # Accepting is always possible; its label states what is being waived, and its
            # color marks it as the decision this page waits for.
            if all(value == "passed" for value in outcomes.values()):
                accept_label = "ACCEPT PROFILE"
            elif all(value == "not-run" for value in outcomes.values()):
                accept_label = "ACCEPT UNTESTED"
            else:
                accept_label = "ACCEPT ANYWAY"
            # Two columns: the test spans ACCEPT and DISCARD, DETAILS sits above BACK.
            commands += [v.button("trial.test", 30, 290, 485, 56, "RUN " + AXIS_NAMES[axis] + " TEST"),
                         v.button("trial.details", 540, 290, 230, 56, "RESULT DETAILS")]
            commands.extend(v.action_row((("trial.accept", accept_label, False, True),
                                          ("trial.discard", "DISCARD PROFILE", True),
                                          ("trial.back", "BACK TO MENU")), y=364, height=76))
            v.send(commands)
            action = v.wait_action()
            if action == "trial.back":
                return None
            if action == "trial.test":
                self.axis_test(axis, report)
            elif action == "trial.details":
                for kind, result in results.items():
                    self.result_page(kind, result)
            elif action == "trial.discard":
                if v.confirm("DISCARD PROFILE", "", "DISCARD", headline="Return to the last confirmed settings?"):
                    return self.discard(report)
            elif action == "trial.accept":
                if mandatory_error:
                    raise ProfileError("Required boot/profile verification failed: " + mandatory_error)
                warning = "Tests: {}.".format(outcomes_text(outcomes))
                if any(value != "passed" for value in outcomes.values()):
                    warning += " Stability is unverified: accept only if you take the risk of crashes or data corruption."
                if v.confirm("CONFIRM PROFILE", warning, "CONFIRM ACCEPT",
                             headline="Save {} {} as the confirmed profile?".format(AXIS_NAMES[axis], now)):
                    self.operation("Saving test evidence for acceptance.", self.results.accept,
                                   profile_identity(report), outcomes)
                    self.operation("Saving the confirmed hardware settings.", self.profiles.accept)
                    self.operation("Updating the Recovery return path.", self.follow_trial)
                    return False

    def not_applied(self, report, axis, state):
        v = self.view
        commands = v.begin_page(AXIS_NAMES[axis] + " TRIAL", "UNCONFIRMED PROFILE")
        commands.append(v.text(30, 96, "TRIAL DID NOT APPLY", color=v.DANGER, font=v.FONT_HEADLINE,
                               h_align="left", max_width=740))
        rows = (("REQUESTED", "CPU {} / eMMC {} MHz".format(mhz(report.get("candidate_cpu_hz")),
                                                          report.get("candidate_emmc_mhz")), v.TEXT),
                ("ACTIVE", "CPU {} / eMMC {}".format(mhz(report.get("runtime_cpu_limit_hz")),
                                                     mhz(report.get("runtime_emmc_hz"))), v.TEXT),
                ("STATUS", state.upper(), v.WARNING))
        for index, (label, value, color) in enumerate(rows):
            y = 142 + index * 38
            commands += [v.text(30, y, label, color=v.MUTED, font=v.FONT_LABEL, h_align="left", max_width=170),
                         v.text(210, y, value, color=color, font=v.FONT_VALUE, h_align="left", max_width=560)]
        commands += [v.rule(240),
                     v.text(30, 256, "It is not retried automatically. Discard it to return to the confirmed "
                            "profile.", color=v.WARNING, font=v.FONT_MEDIUM,
                            h_align="left", v_align="top", max_width=740, max_height=90, wrap=True)]
        commands.extend(v.action_row((("trial.back", "BACK TO MENU"), ("trial.discard", "DISCARD PROFILE", True))))
        v.send(commands)
        return v.wait_action()

    def discard(self, report):
        if self.operation("Restoring the last confirmed settings.", self.profiles.discard, report):
            return self.reboot()
        self.follow_trial()
        return False

    def result_page(self, kind, result):
        v = self.view
        status = result["status"] if result else None
        detail = result["detail"] if result else "Not run for this profile in this boot. Tests are optional."
        if "\n" not in detail:
            # A one-line detail, such as an error, explains the outcome: drawn like a dialog.
            return v.message(TEST_NAMES.get(kind, kind.upper()), detail, headline=outcome_label(status),
                             headline_color=outcome_color(v, status), headline_font=v.FONT_HEADLINE)
        commands = v.begin_page(TEST_NAMES.get(kind, kind.upper()), "TEST RESULT")
        commands.append(v.text(30, 94, outcome_label(status), color=outcome_color(v, status), font=v.FONT_HEADLINE,
                               h_align="left", max_width=740))
        commands += self.detail_rows(122, detail, max_lines=8, quiet_notes=True)[0]
        commands.extend(v.action_row((("result.back", "BACK"),), y=405, height=50))
        v.send(commands)
        return v.wait_action()

    def system_tests(self, report):
        while True:
            action = self.view.menu("SYSTEM TESTS", (("tests.cpu", "CPU STABILITY"),
                                    ("tests.emmc", "eMMC INTEGRITY"), ("tests.results", "RESULT DETAILS"),
                                    ("tests.back", "BACK TO TUNING")),
                                    "CONFIRMED PROFILE")
            if action == "tests.back":
                return
            if action in ("tests.cpu", "tests.emmc"):
                self.axis_test(action.split(".")[1], report)
            elif action == "tests.results":
                for kind in ("cpu-stability", "emmc-integrity"):
                    self.result_page(kind, self.result(kind, report))

    def axis_test(self, axis, report):
        if axis == "cpu":
            self.run_test("cpu-stability", report)
            return
        if not os.path.exists(reference_path(self.results.root)):
            if report["candidate_present"] == "yes":
                raise ProfileError("Reference is missing. Discard the trial and prepare it on confirmed settings.")
            if not self.prepare_reference(report):
                return
        # The workload writes its own temporary file only after the reference reads back intact.
        self.run_test("emmc-integrity", report)

    def prepare_reference(self, report):
        """Write the eMMC reference as one step of the action that needs it.
        Only a failure is shown; a usable reference continues that action."""
        return self.run_test("emmc-prepare", report, show_usable=False)["status"] in ("passed", "inconclusive")

    def run_test(self, kind, report, show_usable=True):
        target = "candidate_cpu_hz" if report["candidate_present"] == "yes" else "confirmed_cpu_hz"
        self.results.start(kind, profile_identity(report), report["boot_id"])
        work = self.workload(kind, self.results.root,
                             cpu_target_hz=int(report[target]) if kind == "cpu-stability" else None)
        v = self.view
        last_percent = -1
        try:
            while True:
                try:
                    progress = next(work)
                except StopIteration as done:
                    result = done.value
                    break
                except Exception as error:
                    result = {"status": "failed", "detail": str(error) or error.__class__.__name__}
                    break
                percent = int(progress * 100)
                if percent != last_percent:
                    commands = v.begin_page(TEST_NAMES[kind], "TEST RUNNING")
                    commands += [v.text(400, 130, TEST_DESCRIPTIONS[kind], color=v.MUTED, font=v.FONT_MEDIUM,
                                        max_width=740)]
                    commands += v.progress_bar(percent, 190)
                    commands += [v.text(400, 280, "{}%".format(percent), color=v.ACCENT, font=v.FONT_HEADLINE),
                                 v.text(400, 330, "CANCEL stops the test and records it as cancelled.",
                                        color=v.MUTED, font=v.FONT_LABEL, max_width=740)]
                    commands.extend(v.action_row((("test.cancel", "CANCEL TEST", True),), y=405, height=50))
                    v.send(commands)
                    last_percent = percent
                if v.wait_action(timeout=0) == "test.cancel":
                    v.progress("STOPPING HARDWARE TEST", "Stopping the test and saving its result. Please wait...")
                    result = {"status": "cancelled", "detail": "Test cancelled; verification did not complete."}
                    break
        finally:
            # Stops the CPU helper and removes owned temporary files. A display
            # failure leaves the test recorded as running, i.e. interrupted.
            work.close()
        self.results.finish(result["status"], result["detail"], result.get("speed"))
        if show_usable or result["status"] not in ("passed", "inconclusive"):
            self.result_page(kind, result)
        return result
