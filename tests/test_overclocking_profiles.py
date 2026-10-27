"""Recovery client behavior at the profile utility's public protocol boundary.

Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
This file may be distributed under the terms of the GNU GPLv3 license.
"""

import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("overclocking_profiles", ROOT / ".py/overclocking_profiles.py")
PROFILES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROFILES)


class Python372Popen(subprocess.Popen):
    """Recovery's stock Python 3.7.2 cannot communicate() over closed pipes."""
    def communicate(self, *args, **kwargs):
        for stream in (self.stdout, self.stderr):
            if stream is not None and stream.closed:
                raise ValueError('Invalid file object: {!r}'.format(stream))
        return super().communicate(*args, **kwargs)


class OverclockingProfilesTest(unittest.TestCase):
    def setUp(self):
        self.tool = PROFILES.ProfileTool(bundle=ROOT / ".bin/tools/overclocking", state="/data/test/profile")
        self.report = {
            "format": "1", "boot_state": "candidate-armed", "boot_id": "boot-b",
            "hardware": "known", "family": "hs2001v8", "hardware_conflict": "0",
            "current_present": "no", "candidate_present": "yes", "candidate_valid": "yes",
            "candidate_armed_boot_id": "boot-a", "candidate_phase": "armed",
            "candidate_cpu_hz": "1248000000", "candidate_cpu_profile": "1248",
            "candidate_emmc_mhz": "25", "candidate_family": "hs2001v8",
            "runtime_cpu_limit_hz": "1248000000", "runtime_cpu_hz": "480000000",
            "runtime_emmc_hz": "25000000", "loaded_cpu_profile": "1248",
            "loaded_emmc_mhz": "25", "loaded_emmc_family": "hs2001v8",
            "confirmed_cpu_profile": "stock", "confirmed_cpu_hz": "1008000000",
            "confirmed_emmc_mhz": "25", "confirmed_family": "hs2001v8",
            "stock_dtb_sha256": self.tool.manifest["canonical_stock"]["sha256"],
        }

    def test_snapshot_parser_accepts_public_fields_without_evaluating_data(self):
        text = "".join(key + "=" + value + "\n" for key, value in self.report.items())
        self.assertEqual(PROFILES.parse_snapshot(text), self.report)
        for invalid in (text + "format=1\n", text.replace("family=hs2001v8", "family=$(touch /tmp/injection)"),
                        "format=2\n", text + "human readable diagnostic\n"):
            with self.assertRaises(PROFILES.ProfileError):
                PROFILES.parse_snapshot(invalid)

    def test_trial_is_applied_even_though_persistent_boot_already_selects_fallback(self):
        self.report["pending_boot"] = "confirmed"
        self.assertEqual(self.tool.trial_state(self.report), "applied")
        self.assertFalse(self.tool.confirmed_active(self.report))

    def test_confirmed_fallback_is_detected_from_loaded_settings(self):
        self.report.update(runtime_cpu_limit_hz="unknown", runtime_cpu_hz="1008000000", loaded_cpu_profile="stock")
        self.assertEqual(self.tool.trial_state(self.report), "fallback")
        self.assertTrue(self.tool.confirmed_active(self.report))
        self.report.update(runtime_cpu_limit_hz="912000000", loaded_cpu_profile="1200")
        self.assertEqual(self.tool.trial_state(self.report), "unconfirmed")
        self.assertFalse(self.tool.confirmed_active(self.report))

    def test_low_policy_limit_does_not_mistake_loaded_trial_for_confirmed_fallback(self):
        self.report["runtime_cpu_limit_hz"] = self.report["confirmed_cpu_hz"]
        self.assertEqual(self.tool.trial_state(self.report), "unconfirmed")
        with mock.patch.object(self.tool, "run") as run:
            self.assertTrue(self.tool.discard(self.report))
            run.assert_called_once_with("cancel", apply=True)

    def test_acceptance_uses_mandatory_verify_and_enable_without_optional_test_gate(self):
        with mock.patch.object(self.tool, "run") as run:
            self.tool.verify_trial(self.report)
            self.tool.accept()
            self.assertEqual(run.call_args_list, [mock.call("verify", apply=True), mock.call("enable", apply=True)])

    def test_trial_verified_in_this_boot_is_not_verified_again(self):
        self.report.update(boot_state="candidate-passed", candidate_phase="passed",
                           candidate_verified_boot_id="boot-b")
        with mock.patch.object(self.tool, "run") as run:
            self.tool.verify_trial(self.report)
            run.assert_not_called()

    def test_previous_boot_verification_cannot_accept_a_current_unconfirmed_trial(self):
        self.report.update(boot_state="candidate-passed", candidate_phase="passed",
                           candidate_verified_boot_id="boot-a")
        with mock.patch.object(self.tool, "run") as run:
            with self.assertRaisesRegex(PROFILES.ProfileError, "previous boot"):
                self.tool.verify_trial(self.report)
            run.assert_not_called()

    def test_discard_requires_no_reboot_if_confirmed_profile_is_already_active(self):
        self.report.update(runtime_cpu_limit_hz="unknown", runtime_cpu_hz="1008000000", loaded_cpu_profile="stock")
        with mock.patch.object(self.tool, "run") as run:
            self.assertFalse(self.tool.discard(self.report))
            run.assert_called_once_with("cancel", apply=True)

    def test_restore_stock_never_restores_historical_dtb_over_changed_firmware(self):
        self.report["stock_dtb_sha256"] = "b" * 64
        with mock.patch.object(self.tool, "run") as run:
            with self.assertRaisesRegex(PROFILES.ProfileError, "Installed stock boot artifacts differ"):
                self.tool.restore_stock(self.report)
            run.assert_not_called()

    def test_restore_stock_uses_canonical_cancel_and_rollback(self):
        with mock.patch.object(self.tool, "run") as run:
            self.assertTrue(self.tool.restore_stock(self.report))
            self.assertEqual(run.call_args_list, [mock.call("cancel", apply=True), mock.call("rollback", apply=True)])

    def test_unknown_remnants_are_explained_and_recovered_by_explicit_cleanup(self):
        self.report.update(boot_state="stock-active-with-unknown-remnants", candidate_present="no",
                           manual_recovery_required="no", unknown_boot_files="OLD.DTB",
                           unknown_boot_variables="custom_boot")
        problem = self.tool.hardware_problem(self.report)
        self.assertIn("OLD.DTB", problem)
        self.assertIn("custom_boot", problem)
        self.assertTrue(self.tool.can_restore_stock(self.report))
        clean = dict(self.report, boot_state="clean-stock")
        with mock.patch.object(self.tool, "snapshot", return_value=clean), \
                mock.patch.object(self.tool, "run") as run:
            self.assertTrue(self.tool.restore_stock(self.report))
            run.assert_called_once_with("--danger-cleanup-unknown", "cleanup", apply=True)

    def test_manual_recovery_and_incompatible_stock_are_not_offered_automatic_restore(self):
        for changes in ({"boot_state": "inconsistent", "manual_recovery_required": "yes"},
                        {"stock_dtb_sha256": "f" * 64}):
            with self.subTest(changes=changes):
                report = dict(self.report, **changes)
                self.assertFalse(self.tool.can_restore_stock(report))

    def test_restore_stock_must_observe_clean_stock_after_cleanup(self):
        self.report.update(boot_state="unknown/custom", candidate_present="no", manual_recovery_required="no")
        with mock.patch.object(self.tool, "snapshot", return_value=self.report), \
                mock.patch.object(self.tool, "run"):
            with self.assertRaisesRegex(PROFILES.ProfileError, "not.*stock"):
                self.tool.restore_stock(self.report)

    def test_known_interrupted_stock_cleanup_needs_no_unknown_cleanup_override(self):
        for state in ('known-cleanup-required', 'stock-restore-cleanup-required'):
            with self.subTest(state=state):
                report = dict(self.report, boot_state=state, candidate_present='no', manual_recovery_required='no')
                self.assertTrue(self.tool.can_restore_stock(report))
                with mock.patch.object(self.tool, 'snapshot', return_value=dict(report, boot_state='clean-stock')), \
                        mock.patch.object(self.tool, 'run') as run:
                    self.assertTrue(self.tool.restore_stock(report))
                    run.assert_called_once_with('cleanup', apply=True)

    def test_unsupported_emmc_choice_is_explained_before_mutation(self):
        self.report.update(candidate_present="no", family="legacy3v3", boot_state="clean-stock")
        with mock.patch.object(self.tool, "run") as run:
            with self.assertRaisesRegex(PROFILES.ProfileError, "unavailable"):
                self.tool.apply_trial("emmc", "150", self.report)
            run.assert_not_called()
        self.assertEqual([choice[0] for choice in self.tool.choices("emmc", self.report)], ["stock", "50"])

    def test_choices_lead_with_their_gain_over_stock(self):
        self.report.update(family="hs2001v8")
        cpu = self.tool.choices("cpu", self.report)
        self.assertEqual(cpu[0][1:], ("STOCK CPU  //  1008 MHz", "1008 MHz"))
        # The first step above stock is the stock OPP table's own 1104 MHz.
        self.assertEqual(cpu[1], ("1.1", "+10%  //  1104 MHz", "1104 MHz"))
        self.assertTrue(cpu[-1][1].endswith("//  EXPERIMENTAL"))
        emmc = self.tool.choices("emmc", self.report)
        self.assertEqual([label for _, label, _ in emmc],
                         ["STOCK eMMC  //  25 MHz", "2x  //  50 MHz", "4x  //  100 MHz", "6x  //  150 MHz"])

    def test_commands_use_integrated_state_and_propagate_cli_error(self):
        self.command_script("import sys\nprint('ERROR: candidate artifact is corrupt', file=sys.stderr)\nsys.exit(1)\n")
        with mock.patch.object(PROFILES.subprocess, "Popen", wraps=subprocess.Popen) as run:
            with self.assertRaisesRegex(PROFILES.ProfileError, "artifact is corrupt"):
                self.tool.run("verify", apply=True)
        arguments, options = run.call_args
        self.assertEqual(arguments[0], [self.tool.executable, "--apply", "verify"])
        self.assertEqual(options["env"]["T113_STATE_DIR"], self.tool.state)

    def command_script(self, source):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        executable = pathlib.Path(directory.name) / 'profile-tool'
        executable.write_text('#!' + sys.executable + '\n' + source)
        executable.chmod(0o755)
        self.tool.executable = str(executable)

    def test_running_command_reports_phase_elapsed_and_separate_protocol_output(self):
        self.command_script("import sys, time\nprint('FGX_PROGRESS:Checking boot files', file=sys.stderr, flush=True)\n"
                            "time.sleep(1.1)\nprint('format=1')\n")
        updates = []
        self.tool.progress = lambda *args: updates.append(args)
        self.assertEqual(self.tool.run('snapshot'), 'format=1\n')
        self.assertTrue(any(stage == 'Checking boot files' for _, stage, _ in updates))
        self.assertTrue(any(elapsed >= 1 for _, _, elapsed in updates))
        self.assertTrue(all(command == 't113-profilectl snapshot' for command, _, _ in updates))

    def test_completed_command_is_not_communicated_with_again(self):
        for source, expected in (("print('format=1')\n", None),
                                 ("import sys\nprint('ERROR: busy', file=sys.stderr)\nsys.exit(1)\n", 'busy')):
            with self.subTest(expected=expected):
                self.command_script(source)
                with mock.patch.object(PROFILES.subprocess, 'Popen', Python372Popen):
                    if expected is None:
                        self.assertEqual(self.tool.run('snapshot'), 'format=1\n')
                    else:
                        with self.assertRaisesRegex(PROFILES.ProfileError, expected):
                            self.tool.run('snapshot')

    def test_failed_progress_display_reaps_the_running_command(self):
        self.command_script("import time\ntime.sleep(60)\n")
        self.tool.progress = mock.Mock(side_effect=RuntimeError('display failed'))
        children, launch = [], subprocess.Popen
        def spawn(*args, **kwargs):
            child = launch(*args, **kwargs)
            children.append(child)
            return child
        with mock.patch.object(PROFILES.subprocess, 'Popen', side_effect=spawn):
            with self.assertRaisesRegex(RuntimeError, 'display failed'):
                self.tool.run('snapshot')
        self.assertIsNotNone(children[0].returncode)
        self.assertTrue(children[0].stdout.closed)
        self.assertTrue(children[0].stderr.closed)

    def test_command_timeout_does_not_leave_a_child_running(self):
        self.command_script("import time\ntime.sleep(60)\n")
        children, launch = [], subprocess.Popen
        def spawn(*args, **kwargs):
            child = launch(*args, **kwargs)
            children.append(child)
            return child
        with mock.patch.object(PROFILES.subprocess, 'Popen', side_effect=spawn):
            with self.assertRaisesRegex(PROFILES.ProfileError, 'could not finish'):
                self.tool.run('snapshot', timeout=0.1)
        self.assertIsNotNone(children[0].returncode)
        self.assertTrue(children[0].stdout.closed)


if __name__ == "__main__":
    unittest.main()
