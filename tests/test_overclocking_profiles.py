"""Recovery client behavior at the profile utility's public protocol boundary.

Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
This file may be distributed under the terms of the GNU GPLv3 license.
"""

import contextlib
import importlib.util
import io
import json
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
            "hardware": "supported", "family": "hs2001v8",
            "available_emmc_mhz": "25,50,100,150",
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
        problem = self.tool.boot_problem(self.report)
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
        self.report.update(candidate_present="no", family="legacy3v3", boot_state="clean-stock",
                           available_emmc_mhz="25,50")
        with mock.patch.object(self.tool, "run") as run:
            with self.assertRaises(PROFILES.ProfileError):
                self.tool.apply_trial("emmc", "150", self.report)
            run.assert_not_called()
        self.assertEqual([choice[0] for choice in self.tool.choices("emmc", self.report)], ["stock", "50"])

    def test_unknown_emmc_mode_blocks_both_axes_and_names_only_the_interface_type(self):
        # Every generated DTB states the eMMC family, CPU profiles included.
        for timing, voltage, expected in (('9', '0', ('HS200', '3.3 V')),
                                          ('72', '83', ('72', '83')),
                                          ('unknown', 'unknown', ())):
            with self.subTest(timing=timing, voltage=voltage):
                report = dict(self.report, hardware='unsupported', family='unknown', available_emmc_mhz='',
                              candidate_present='no', boot_state='clean-stock',
                              runtime_timing=timing, runtime_voltage=voltage,
                              model_identity='d6:0103:353841333938:11')
                self.assertEqual(self.tool.boot_problem(report), '')
                for axis, value in (('cpu', '1.1'), ('emmc', '50')):
                    message = self.tool.axis_problem(axis, report)
                    for text in expected:
                        self.assertIn(text, message)
                    self.assertNotIn('58A398', message)
                    with mock.patch.object(self.tool, 'run') as run:
                        with self.assertRaises(PROFILES.ProfileError):
                            self.tool.apply_trial(axis, value, report)
                        run.assert_not_called()

    def test_model_excluded_by_the_manifest_blocks_only_new_emmc_trials(self):
        report = dict(self.report, hardware='unsupported', available_emmc_mhz='', candidate_present='no',
                      boot_state='clean-stock', runtime_timing='9', runtime_voltage='1',
                      model_identity='d6:0103:353841333938:11')
        self.assertEqual(self.tool.boot_problem(report), '')
        self.assertEqual(self.tool.axis_problem('cpu', report), '')
        message = self.tool.axis_problem('emmc', report)
        for value in ('58A398', 'HS200', '1.8 V'):
            self.assertIn(value, message)
        with mock.patch.object(self.tool, 'run') as run:
            with self.assertRaises(PROFILES.ProfileError):
                self.tool.apply_trial('emmc', '50', report)
            run.assert_not_called()
            self.tool.apply_trial('cpu', '1.1', report)
            run.assert_called_once_with('test', 'cpu', '1.1', apply=True)

    def test_choices_show_frequencies_and_their_gain_over_stock(self):
        self.report.update(family="hs2001v8")
        cpu = self.tool.choices("cpu", self.report)
        self.assertEqual(cpu[0][0], "stock")
        self.assertIn(str(PROFILES.STOCK_CPU_MHZ), cpu[0][2])
        for choice, row in zip(cpu[1:], self.tool.manifest["cpu"]):
            self.assertEqual(choice[0], row["alias"])
            self.assertIn(str(row["exact_mhz"]), choice[2])
            percentage = round((row["exact_mhz"] / PROFILES.STOCK_CPU_MHZ - 1) * 100)
            self.assertIn(str(percentage) + "%", choice[1])
        emmc = self.tool.choices("emmc", self.report)
        self.assertEqual([choice[0] for choice in emmc], ["stock", "50", "100", "150"])
        for choice in emmc[1:]:
            self.assertIn(choice[0], choice[2])
            self.assertIn("{:g}x".format(int(choice[0]) / PROFILES.STOCK_EMMC_MHZ), choice[1])

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

    def records(self, output):
        return [json.loads(line.split("Tuning diagnostic: ", 1)[1]) for line in output.splitlines()
                if line.startswith("Tuning diagnostic: ")]

    def test_repeated_entry_logs_each_observation_without_stdout_or_private_fields(self):
        protocol = "".join(key + "=" + value + "\n" for key, value in self.report.items())
        self.command_script("import sys\nif 'snapshot' in sys.argv:\n    print(" + repr(protocol) +
                            ", end='')\nelse:\n    print('No legacy profile state to migrate.')\n")
        log, console = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(log), contextlib.redirect_stdout(console):
            for _ in range(2):
                self.assertEqual(self.tool.prepare(), self.report)
        self.assertEqual(console.getvalue(), '')
        observations = [item for item in self.records(log.getvalue()) if item['event'] == 'snapshot']
        self.assertEqual(len(observations), 2)
        self.assertLess(len(log.getvalue()), 16000)

    def test_invalid_snapshot_logs_protocol_failure_without_dumping_input(self):
        self.command_script("print('private-fixture-value without a protocol separator')\n")
        log = io.StringIO()
        with contextlib.redirect_stderr(log):
            with self.assertRaises(PROFILES.ProfileError):
                self.tool.snapshot()
        self.assertEqual(self.records(log.getvalue())[-1]['event'], 'snapshot-invalid')
        self.assertNotIn('private-fixture-value', log.getvalue())

    def test_successful_snapshot_records_allowlisted_state_and_restore_verdict(self):
        report = dict(self.report, hardware="unsupported", boot_state="inconsistent",
                      manual_recovery_required="yes", stock_helpers_valid="0",
                      model_identity="d6:0103:353841333938:11",
                      runtime_timing="9", runtime_voltage="1", serial="private-fixture-id",
                      bootcmd="arbitrary_private_boot_value", stock_dtb_sha256="b" * 64)
        protocol = "".join(key + "=" + value + "\n" for key, value in report.items())
        self.command_script("print(" + repr(protocol) + ", end='')\n")
        log = io.StringIO()
        with contextlib.redirect_stderr(log):
            self.assertEqual(self.tool.snapshot(), report)
        command, observed = self.records(log.getvalue())
        self.assertEqual(command['exit_code'], 0)
        self.assertEqual(command['failure'], 'none')
        self.assertEqual(command['stdout'], '')
        self.assertEqual(observed['model_identity'], report['model_identity'])
        self.assertEqual(observed['product_name'], '58A398')
        self.assertEqual(observed['runtime_timing'], '9')
        self.assertEqual(observed['stock_dtb_matches'], False)
        self.assertEqual(observed['restore_stock_available'], False)
        self.assertNotIn('private-fixture-id', log.getvalue())
        self.assertNotIn('arbitrary_private_boot_value', log.getvalue())
        self.assertNotIn('serial', observed)

    def test_successful_utility_stdout_and_warnings_are_attributed_and_redacted(self):
        self.command_script("import sys\nprint('Candidate verification: PASS')\n"
                            "print('WARNING: fw_printenv: fixture warning', file=sys.stderr)\n"
                            "print('cid=d60103353841333938118ae86aa09a81 "
                            "ethaddr=02:00:00:00:00:01', file=sys.stderr)\n")
        log = io.StringIO()
        with contextlib.redirect_stderr(log):
            result = self.tool.run('detect')
        self.assertIn('PASS', result)
        record, = self.records(log.getvalue())
        self.assertEqual(record['command'], 't113-profilectl detect')
        self.assertIn('Candidate verification: PASS', record['stdout'])
        self.assertIn('fw_printenv: fixture warning', record['stderr'])
        self.assertNotIn('d60103353841333938118ae86aa09a81', log.getvalue())
        self.assertNotIn('02:00:00:00:00:01', log.getvalue())

    def test_output_is_bounded_but_failure_tail_and_exit_code_survive(self):
        self.command_script("import sys\nprint('x' * 20000)\n"
                            "print('y' * 20000 + ' fw_printenv: fixture failure', file=sys.stderr)\n"
                            "sys.exit(7)\n")
        log = io.StringIO()
        with contextlib.redirect_stderr(log):
            with self.assertRaisesRegex(PROFILES.ProfileError, 'fixture failure') as failed:
                self.tool.run('verify')
        self.assertEqual(failed.exception.returncode, 7)
        record, = self.records(log.getvalue())
        self.assertEqual(record['failure'], 'exit')
        self.assertEqual(record['exit_code'], 7)
        self.assertIn('fixture failure', record['stderr'])
        self.assertLess(len(log.getvalue()), 9000)
        self.assertIn('<truncated>', log.getvalue())

    def test_timeout_keeps_partial_diagnostic_and_reaps_child(self):
        self.command_script("")
        pathlib.Path(self.tool.executable).write_text(
            "#!/bin/sh\nprintf 'fw_printenv: still waiting\\n' >&2\nsleep 60\n")
        log = io.StringIO()
        children, launch = [], subprocess.Popen
        def spawn(*args, **kwargs):
            child = launch(*args, **kwargs)
            children.append(child)
            return child
        with contextlib.redirect_stderr(log), mock.patch.object(PROFILES.subprocess, 'Popen', side_effect=spawn):
            with self.assertRaisesRegex(PROFILES.ProfileError, 'still waiting'):
                self.tool.run('snapshot', timeout=1)
        child, = children
        self.assertIsNotNone(child.returncode)
        self.assertTrue(child.stdout.closed)
        self.assertTrue(child.stderr.closed)
        record, = self.records(log.getvalue())
        self.assertEqual(record['failure'], 'timeout')
        self.assertEqual(record['exit_code'], child.returncode)
        self.assertIn('fw_printenv: still waiting', record['stderr'])

    def test_launch_error_is_distinguished_from_utility_failure(self):
        self.tool.executable = '/nonexistent-fixture-directory/profilectl'
        log = io.StringIO()
        with contextlib.redirect_stderr(log):
            with self.assertRaises(PROFILES.ProfileError):
                self.tool.run('snapshot')
        record, = self.records(log.getvalue())
        self.assertEqual(record['failure'], 'launch')
        self.assertIsNone(record['exit_code'])

    def test_choices_use_utility_eligibility_including_empty_lists_and_no_stock(self):
        self.report["available_emmc_mhz"] = "50"
        self.assertEqual([choice[0] for choice in self.tool.choices('emmc', self.report)], ['50'])
        for value in ('', None):
            if value is None:
                self.report.pop('available_emmc_mhz')
            else:
                self.report['available_emmc_mhz'] = value
            self.assertEqual(self.tool.choices('emmc', self.report), [])

    def test_abnormal_entry_records_the_full_boot_report_once(self):
        for state, expected in (('inconsistent', 1), ('clean-stock', 0)):
            with self.subTest(state=state):
                protocol = "".join(key + "=" + value + "\n" for key, value in
                                   dict(self.report, boot_state=state).items())
                self.command_script("import sys\nif 'doctor' in sys.argv: print('bootcmd=run fixture_flow')\n"
                                    "elif 'snapshot' in sys.argv: print(" + repr(protocol) + ", end='')\n")
                log = io.StringIO()
                with contextlib.redirect_stderr(log):
                    self.assertEqual(self.tool.prepare()['boot_state'], state)
                reports = [record['stdout'] for record in self.records(log.getvalue())
                           if record.get('command') == 't113-profilectl doctor']
                self.assertEqual(len(reports), expected)
                for report in reports:
                    self.assertIn('bootcmd=run fixture_flow', report)

    def test_boot_diagnostic_failure_does_not_hide_the_repair_report(self):
        report = dict(self.report, boot_state='inconsistent', stock_helpers_valid='0',
                      stock_helper_mismatches='boot_uImage')
        protocol = ''.join(key + '=' + value + '\n' for key, value in report.items())
        with mock.patch.object(self.tool, 'run',
                               side_effect=['', protocol, PROFILES.ProfileError('fixture doctor failure')]), \
                contextlib.redirect_stderr(io.StringIO()) as log:
            self.assertEqual(self.tool.prepare(), report)
        self.assertTrue(any(record['event'] == 'boot-diagnostics-failed'
                            for record in self.records(log.getvalue())))
        self.assertIn('boot_uImage', self.tool.boot_problem(report))


if __name__ == "__main__":
    unittest.main()
