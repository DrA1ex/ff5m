"""Tuning decisions, truthful test waivers and prototype screen contracts.

Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
This file may be distributed under the terms of the GNU GPLv3 license.
"""
import importlib
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.py'))
from overclocking_profiles import ProfileError, ProfileTool
from overclocking_results import TestResults
from overclocking_tests import reference_path
from overclocking_ui import TuningUI
recovery = importlib.import_module('recovery')


class ViewSession:
    """Taps arrive for the current frame unless an action names a stale one."""
    def __init__(self, actions=()):
        self.actions = list(actions)
        self.frames = []
        self.view = None
    def start(self):
        pass
    def close(self):
        pass
    def send(self, commands):
        self.frames.append(tuple(commands))
    def next_tap(self, timeout=None):
        if self.actions:
            action = self.actions.pop(0)
            return action if ':' in action else '{}:{}'.format(self.view.generation, action)
        if timeout is not None:
            return None
        raise AssertionError('Unexpected navigation after test actions')


class TuningUITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.results = TestResults(self.root / 'data')
        self.report = {'candidate_present': 'yes', 'candidate_axis': 'cpu',
                       'candidate_cpu_hz': '1248000000', 'candidate_emmc_mhz': '25',
                       'candidate_dtb_sha256': 'b' * 64, 'confirmed_cpu_hz': '1008000000',
                       'confirmed_emmc_mhz': '25', 'boot_id': 'boot-b', 'stock_dtb_sha256': 'a' * 64}
        self.profiles = mock.Mock()
        self.profiles.boot_problem.return_value = ''
        self.profiles.axis_problem.return_value = ''
        self.profiles.trial_state.return_value = 'applied'
        self.reboots = []
        self.follow_trial = mock.Mock()
        self.view = mock.Mock()
        self.view.begin_page.return_value = []
        self.view.text.return_value = 'text'
        self.view.button.return_value = 'button'
        self.view.action_row.return_value = []
        self.view.progress_bar.return_value = []
        self.view.wrapped_lines.return_value = 1
        self.view.line_height.return_value = 31
        self.view.text_width.return_value = 100
        self.view.wait_action.return_value = 'trial.accept'
        self.view.confirm.return_value = True
        self.ui = TuningUI(self.view, lambda: self.reboots.append('reboot'), self.follow_trial,
                           self.profiles, self.results)

    def test_emmc_chooser_shows_product_mode_voltage_and_actual_clock(self):
        for timing, voltage, mode, volts in (('9', '1', 'HS200', '1.8 V'),
                                             ('0', '0', 'Legacy', '3.3 V'),
                                             ('1', '0', 'HS52', '3.3 V')):
            with self.subTest(mode=mode):
                session = ViewSession(['choose.back'])
                self.ui.view = session.view = recovery.RecoveryView(session)
                self.profiles.choices.return_value = [('stock', 'STOCK eMMC // 25 MHz', '25 MHz')]
                report = dict(self.report, model_identity='d6:0103:353841333938:11',
                              runtime_timing=timing, runtime_voltage=voltage, runtime_emmc_hz='50000000')
                self.assertFalse(self.ui.select_profile('emmc', report))
                frame = ' '.join(session.frames[0])
                for value in ('58A398', mode, volts, '50 MHz'):
                    self.assertIn(value, frame)
                self.profiles.apply_trial.assert_not_called()

    def test_generic_repair_tuning_and_cpu_chooser_do_not_show_emmc_identity(self):
        report = dict(self.report, model_identity='d6:0103:353841333938:11',
                      runtime_timing='9', runtime_voltage='1', runtime_emmc_hz='50000000')
        for page in ('repair', 'tuning', 'cpu'):
            with self.subTest(page=page):
                session = ViewSession([{'repair': 'recovery.back', 'tuning': 'tuning.back',
                                        'cpu': 'choose.back'}[page]])
                self.ui.view = session.view = recovery.RecoveryView(session)
                if page == 'repair':
                    self.profiles.boot_problem.return_value = 'Stock boot commands differ: boot_uImage.'
                    self.profiles.can_restore_stock.return_value = False
                    self.ui.recover_configuration(report)
                elif page == 'tuning':
                    self.ui.tuning_page(report)
                else:
                    self.profiles.choices.return_value = [('stock', 'STOCK CPU // 1008 MHz', '1008 MHz')]
                    self.assertFalse(self.ui.select_profile('cpu', report))
                frame = ' '.join(session.frames[0])
                for value in ('58A398', 'HS200', '1.8 V'):
                    self.assertNotIn(value, frame)

    def test_experimental_choice_adds_warning_before_any_profile_write(self):
        tool = ProfileTool(bundle=ROOT / '.bin/tools/overclocking')
        choices = tool.choices('cpu', self.report)
        alias = next(row['alias'] for row in tool.manifest['cpu'] if row.get('warning'))
        experimental = next(choice for choice in choices if choice[0] == alias)
        self.profiles.choices.return_value = choices
        self.view.confirm.return_value = False
        details = []
        for choice in (choices[0], experimental):
            self.view.choose.return_value = choice
            self.assertFalse(self.ui.select_profile('cpu', self.report))
            details.append(self.view.confirm.call_args.args[1])
        self.assertNotEqual(details[0], details[1])
        self.profiles.apply_trial.assert_not_called()
        self.assertEqual(self.reboots, [])

    def finish_test(self, outcome, boot='boot-b'):
        self.results.start('cpu-stability', 'b' * 64, boot)
        self.results.finish(outcome, 'Fixture computational mismatch')

    def test_explicit_untested_acceptance_preserves_not_run_evidence(self):
        self.assertFalse(self.ui.trial(self.report))
        self.profiles.accept.assert_called_once_with()
        detail = self.view.confirm.call_args.args[1]
        self.assertIn('NOT RUN', detail)
        self.assertIn('risk', detail)
        self.assertEqual(self.view.confirm.call_args.kwargs['headline'], 'Save CPU 1248 MHz as the confirmed profile?')
        self.assertEqual(self.results.acceptance('b' * 64)['tests']['cpu-stability'], 'not-run')
        # Acceptance returns to tuning and stops routing resets to Recovery.
        self.assertEqual(self.reboots, [])
        self.follow_trial.assert_called_once_with()

    def test_failed_interrupted_inconclusive_results_survive_acceptance(self):
        for outcome in ('failed', 'interrupted', 'inconclusive', 'cancelled'):
            with self.subTest(outcome=outcome):
                self.finish_test(outcome)
                self.ui.trial(self.report)
                detail = self.view.confirm.call_args.args[1]
                self.assertIn(outcome.upper(), detail)
                self.assertEqual(self.ui.result('cpu-stability', self.report)['status'], outcome)
                self.assertEqual(self.results.acceptance('b' * 64)['tests']['cpu-stability'], outcome)

    def test_cancelled_confirmation_does_not_promote_and_keeps_the_trial_page(self):
        self.view.confirm.return_value = False
        self.view.wait_action.side_effect = ['trial.accept', 'trial.back']
        self.assertIsNone(self.ui.trial(self.report))
        self.profiles.accept.assert_not_called()
        self.assertIsNone(self.results.acceptance('b' * 64))
        self.profiles.verify_trial.assert_called_once_with(self.report)

    def test_test_run_from_trial_page_redraws_without_reobserving(self):
        self.profiles.prepare.return_value = self.report
        self.view.wait_action.side_effect = ['trial.test', 'trial.back']
        self.ui.axis_test = mock.Mock(side_effect=lambda axis, report: self.finish_test('passed'))
        self.assertFalse(self.ui.run())
        self.ui.axis_test.assert_called_once_with('cpu', self.report)
        self.profiles.prepare.assert_called_once_with()
        self.profiles.verify_trial.assert_called_once_with(self.report)
        self.profiles.snapshot.assert_not_called()
        # The redrawn page shows the new result.
        labels = [call.args[2] for call in self.view.text.call_args_list if call.args[2] in ('PASSED', 'NOT RUN')]
        self.assertEqual(labels, ['NOT RUN', 'PASSED'])

    def test_accepting_observes_the_new_profile_once(self):
        confirmed = dict(self.report, candidate_present='no')
        self.profiles.prepare.return_value = self.report
        self.profiles.snapshot.return_value = confirmed
        self.profiles.confirmed_active.return_value = True
        self.view.wait_action.side_effect = ['trial.accept']
        self.ui.tuning_page = mock.Mock(return_value='tuning.back')
        self.assertFalse(self.ui.run())
        self.profiles.accept.assert_called_once_with()
        self.profiles.prepare.assert_called_once_with()
        self.profiles.snapshot.assert_called_once_with()
        self.ui.tuning_page.assert_called_once_with(confirmed)

    def test_returning_from_system_tests_keeps_the_observed_profile(self):
        self.profiles.prepare.return_value = dict(self.report, candidate_present='no')
        self.profiles.confirmed_active.return_value = True
        self.ui.tuning_page = mock.Mock(side_effect=['tuning.tests', 'tuning.emmc', 'tuning.back'])
        self.ui.system_tests = mock.Mock()
        self.ui.select_profile = mock.Mock(return_value=False)
        self.assertFalse(self.ui.run())
        self.profiles.prepare.assert_called_once_with()
        self.assertEqual(self.ui.tuning_page.call_count, 3)

    def test_finished_test_records_its_measured_speed(self):
        def workload(kind, root, cpu_target_hz=None):
            yield 1.0
            return {'status': 'passed', 'detail': 'Verified.', 'speed': '65.2 MiB/s'}
        self.ui.workload = workload
        self.view.wait_action.return_value = 'result.back'
        self.ui.run_test('emmc-integrity', self.report)
        self.assertEqual(self.ui.result('emmc-integrity', self.report)['speed'], '65.2 MiB/s')

    def test_trial_page_shows_the_speed_each_test_measured(self):
        self.results.start('cpu-stability', 'b' * 64, 'boot-b')
        self.results.finish('passed', 'Verified.', '310 points, 1.24x stock')
        self.view.wait_action.return_value = 'trial.back'
        self.ui.trial(self.report)
        texts = [call.args[2] for call in self.view.text.call_args_list]
        self.assertIn('310 points, 1.24x stock', texts)

    def test_accepted_tests_are_one_short_value_each(self):
        self.results.accept('a' * 64, {'emmc-read': 'passed', 'emmc-write': 'not-run'})
        report = dict(self.report, candidate_present='no', confirmed_cpu_profile='stock', confirmed_emmc_mhz='150')
        self.view.wait_action.return_value = 'tuning.back'
        self.ui.tuning_page(report)
        texts = [call.args[2] for call in self.view.text.call_args_list]
        self.assertIn('eMMC READ PASSED', texts)
        self.assertIn('eMMC WRITE NOT RUN', texts)
        self.assertFalse([text for text in texts if 'ACCEPTANCE' in text])


    def test_accepted_emmc_test_is_one_value(self):
        self.results.accept('a' * 64, {'emmc-integrity': 'passed'})
        report = dict(self.report, candidate_present='no', confirmed_emmc_mhz='150')
        self.view.wait_action.return_value = 'tuning.back'
        self.ui.tuning_page(report)
        texts = [call.args[2] for call in self.view.text.call_args_list]
        self.assertIn('eMMC INTEGRITY PASSED', texts)

    def test_required_artifact_failure_cannot_be_waived(self):
        self.profiles.verify_trial.side_effect = ProfileError('DTB mismatch')
        with self.assertRaisesRegex(ProfileError, 'Required'):
            self.ui.trial(self.report)
        self.profiles.accept.assert_not_called()

    def test_old_boot_pass_is_not_used_for_current_trial(self):
        self.finish_test('passed', boot='old-boot')
        self.assertIsNone(self.ui.result('cpu-stability', self.report))
        self.ui.trial(self.report)
        self.assertIn('NOT RUN', self.view.confirm.call_args.args[1])

    def test_fallback_reports_failure_without_automatic_candidate_retry(self):
        session = ViewSession(['trial.discard'])
        self.ui.view = session.view = recovery.RecoveryView(session)
        self.ui.view.confirm = mock.Mock(return_value=True)
        self.profiles.trial_state.return_value = 'fallback'
        self.profiles.discard.return_value = False
        self.assertFalse(self.ui.trial(self.report))
        self.assertIn('FALLBACK', ' '.join(session.frames[0]))
        self.profiles.apply_trial.assert_not_called()
        self.profiles.discard.assert_called_once_with(self.report)
        self.assertEqual(self.reboots, [])
        self.follow_trial.assert_called_once_with()

    def test_discard_applied_candidate_reboots(self):
        self.view.wait_action.return_value = 'trial.discard'
        self.profiles.discard.return_value = True
        self.assertTrue(self.ui.trial(self.report))
        self.assertEqual(self.reboots, ['reboot'])

    def test_reset_route_follows_the_trial_only_after_a_frame_is_drawn(self):
        session = ViewSession(['trial.back'])
        self.ui.view = session.view = recovery.RecoveryView(session)
        self.follow_trial.side_effect = lambda: self.assertTrue(session.frames)
        self.profiles.prepare.return_value = self.report
        self.assertFalse(self.ui.run())
        self.follow_trial.assert_called_once_with()

    def test_failed_first_frame_does_not_route_resets_to_recovery(self):
        session = ViewSession()
        session.send = mock.Mock(side_effect=RuntimeError('display unavailable'))
        self.ui.view = recovery.RecoveryView(session)
        with self.assertRaisesRegex(RuntimeError, 'display unavailable'):
            self.ui.run()
        self.follow_trial.assert_not_called()

    def test_blocked_operation_keeps_elapsed_and_last_phase_visible_on_ui_thread(self):
        session = ViewSession()
        self.ui.view = session.view = recovery.RecoveryView(session)
        release = threading.Event()
        ui_thread = threading.get_ident()
        draws = []
        send = session.send
        def display(commands):
            draws.append(threading.get_ident())
            send(commands)
            if 'ELAPSED 1 s' in ' '.join(commands):
                release.set()
        session.send = display
        def blocked_cleanup():
            self.ui.report_progress('Removing a temporary file', '.write-test-blocked')
            if not release.wait(timeout=5):
                raise RuntimeError('Progress did not advance while the file operation was blocked')
            return 42
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.ui.operation('Cleaning temporary files.', blocked_cleanup), 42)
        timed = [frame for frame in session.frames if 'ELAPSED 1 s' in ' '.join(frame)]
        self.assertTrue(timed)
        self.assertIn('.write-test-blocked', ' '.join(timed[0]))
        self.assertEqual(set(draws), {ui_thread})

    def test_operation_error_names_step_file_and_original_failure_in_ui_and_log(self):
        directory = Path(self.results.root) / 'reference'
        directory.mkdir(parents=True)
        (directory / '.write-test-owned').touch()
        self.profiles.prepare.return_value = self.report
        log = io.StringIO()
        with mock.patch('overclocking_tests.os.unlink', side_effect=OSError('eMMC I/O failed')), \
                contextlib.redirect_stderr(log):
            with self.assertRaisesRegex(RuntimeError, 'eMMC I/O failed') as failure:
                self.ui.run()
        for expected in ('Step 4/5', 'Removing a temporary file', '.write-test-owned', 'eMMC I/O failed'):
            self.assertIn(expected, str(failure.exception))
            self.assertIn(expected, log.getvalue())
        self.assertTrue(str(failure.exception).startswith('eMMC I/O failed'))
        self.assertIsInstance(failure.exception.__cause__, OSError)

    def test_trial_verification_failure_shows_its_cause_first(self):
        session = ViewSession(['trial.back'])
        self.ui.view = session.view = recovery.RecoveryView(session)
        self.profiles.verify_trial.side_effect = ProfileError('DTB mismatch')
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(self.ui.trial(self.report))
        trial = next(frame for frame in session.frames if 'UNCONFIRMED PROFILE' in ' '.join(frame))
        self.assertIn('BOOT / DTB CHECK FAILED: DTB mismatch', ' '.join(trial))

    def test_operation_preserves_profile_error_status(self):
        def fail():
            raise ProfileError('Boot validation failed', 7)
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(ProfileError) as failure:
                self.ui.operation('Verifying trial boot settings.', fail)
        self.assertEqual(failure.exception.returncode, 7)
        self.assertIn('Verifying trial', str(failure.exception))

    def test_display_failure_lets_work_finish_before_returning(self):
        sent = threading.Event()
        failed = threading.Event()
        finished = threading.Event()
        def display(commands):
            if sent.is_set():
                failed.set()
                raise RuntimeError('display unavailable')
            sent.set()
        self.view.send.side_effect = display
        def work():
            self.ui.report_progress('Checking boot files', 't113-profilectl enable')
            if not failed.wait(timeout=5):
                raise RuntimeError('The display did not fail during the work')
            # Progress after the display failed must not interrupt the mutation.
            self.ui.report_progress('Writing boot settings', 't113-profilectl enable')
            finished.wait(0.3)
            finished.set()
        log = io.StringIO()
        with contextlib.redirect_stderr(log):
            with self.assertRaisesRegex(RuntimeError, 'display unavailable'):
                self.ui.operation('Saving the confirmed hardware settings.', work)
        self.assertTrue(finished.is_set())
        self.assertIn('completed', log.getvalue())

    def test_worker_exit_is_propagated_and_next_operation_can_run(self):
        def exit_worker():
            raise SystemExit('helper exited')
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, 'helper exited'):
                self.ui.operation('Checking the helper.', exit_worker)
            self.assertEqual(self.ui.operation('Checking saved results.', lambda: 42), 42)


    def test_emmc_test_reads_and_writes_in_one_run_without_a_second_confirmation(self):
        path = Path(reference_path(self.results.root))
        path.parent.mkdir(parents=True)
        path.touch()
        self.ui.run_test = mock.Mock(return_value={'status': 'passed', 'detail': 'Read: passed\nWrite: passed'})
        self.ui.axis_test('emmc', dict(self.report, candidate_axis='emmc'))
        self.assertEqual([call.args[0] for call in self.ui.run_test.call_args_list], ['emmc-integrity'])
        self.view.confirm.assert_not_called()

    def test_emmc_trial_shows_one_test_and_one_result_page(self):
        report = dict(self.report, candidate_axis='emmc', candidate_emmc_mhz='150')
        self.results.start('emmc-integrity', 'b' * 64, 'boot-b')
        self.results.finish('passed', 'Read: passed, 72.4 MiB/s\nWrite: passed, 18.2 MiB/s', '72.4 MiB/s')
        self.view.wait_action.side_effect = ['trial.details', 'trial.back']
        self.ui.result_page = mock.Mock()
        self.assertIsNone(self.ui.trial(report))
        texts = [call.args[2] for call in self.view.text.call_args_list]
        self.assertIn('eMMC INTEGRITY', texts)
        self.assertEqual([call.args[0] for call in self.ui.result_page.call_args_list], ['emmc-integrity'])

    def emmc_trial_without_reference(self, outcome):
        def workload(kind, root, cpu_target_hz=None):
            yield 1.0
            return {'status': outcome, 'detail': 'Fixture reference ' + outcome}
        self.ui.workload = workload
        self.profiles.choices.return_value = [('stock', 'STOCK eMMC  //  25 MHz', '25 MHz'),
                                              ('150', '6x  //  150 MHz', '150 MHz')]
        self.view.choose.return_value = ('150', '6x  //  150 MHz', '150 MHz')
        self.view.wait_action.return_value = 'result.back'
        report = dict(self.report, candidate_present='no', family='hs2001v8')
        return self.ui.select_profile('emmc', report)

    def result_pages(self):
        """Titles of shown results: a table page, or a one-line result drawn as a message."""
        return [call.args[0] for call in self.view.begin_page.call_args_list
                if call.args[1:] == ('TEST RESULT',)] + \
            [call.args[0] for call in self.view.message.call_args_list if 'headline' in call.kwargs]

    def test_emmc_trial_writes_missing_reference_within_its_single_confirmation(self):
        self.assertTrue(self.emmc_trial_without_reference('passed'))
        self.assertEqual(self.view.confirm.call_count, 1)
        self.assertIn('MiB eMMC test file', self.view.confirm.call_args.args[1])
        self.assertEqual(self.view.confirm.call_args.kwargs['headline'], 'Try eMMC 150 MHz?')
        self.assertEqual(self.result_pages(), [])
        self.profiles.apply_trial.assert_called_once_with('emmc', '150', mock.ANY)
        self.assertEqual(self.reboots, ['reboot'])

    def test_failed_reference_is_shown_and_no_trial_is_applied(self):
        self.assertFalse(self.emmc_trial_without_reference('failed'))
        self.assertEqual(self.result_pages(), ['eMMC REFERENCE'])
        self.profiles.apply_trial.assert_not_called()
        self.assertEqual(self.reboots, [])

    def test_boot_forge_x_reboots_from_confirmed_profile(self):
        self.profiles.prepare.return_value = dict(self.report, candidate_present='no')
        self.profiles.confirmed_active.return_value = True
        self.ui.tuning_page = mock.Mock(return_value='tuning.finish')
        self.assertTrue(self.ui.run())
        self.assertEqual(self.reboots, ['reboot'])

    def test_opening_and_leaving_tuning_changes_nothing(self):
        self.profiles.prepare.return_value = dict(self.report, candidate_present='no')
        self.profiles.confirmed_active.return_value = True
        self.ui.tuning_page = mock.Mock(return_value='tuning.back')
        for _ in range(2):
            self.assertFalse(self.ui.run())
        self.assertEqual(self.reboots, [])
        for operation in ('apply_trial', 'accept', 'discard', 'restore_stock'):
            getattr(self.profiles, operation).assert_not_called()

    def test_reentry_observes_expired_one_shot_without_retrying_candidate(self):
        fallback = dict(self.report, runtime_cpu_limit_hz='1008000000', boot_id='boot-c')
        self.profiles.prepare.side_effect = [self.report, fallback]
        self.ui.trial = mock.Mock(return_value=None)
        self.ui.run()
        self.ui.run()
        self.assertEqual(self.ui.trial.call_args_list, [mock.call(self.report), mock.call(fallback)])
        self.profiles.apply_trial.assert_not_called()

    def test_interrupted_test_is_reported_on_next_entry(self):
        self.results.start('emmc-integrity', 'b' * 64, 'boot-a')
        self.profiles.prepare.return_value = self.report
        self.ui.trial = mock.Mock(return_value=None)
        self.ui.run()
        self.assertEqual(self.view.message.call_args.args[0], 'INTERRUPTED TUNING TEST')
        self.assertEqual(self.results.result('emmc-integrity', 'b' * 64, 'boot-a')['status'], 'interrupted')

    def test_tuning_frame_contains_settings_and_single_direct_actions(self):
        session = ViewSession(['1:tuning.back'])
        view = recovery.RecoveryView(session)
        self.ui.view = view
        self.assertEqual(self.ui.tuning_page(self.report), 'tuning.back')
        labels = [item[4] for item in view.buttons.values()]
        self.assertEqual(labels, ['CPU FREQUENCY', 'eMMC FREQUENCY', 'SYSTEM TESTS',
                                  'RESTORE STOCK', 'BOOT FORGE-X', 'BACK'])
        self.assertTrue(all(spec[1] + spec[3] <= 480 for spec in view.buttons.values()))

    def test_accept_label_states_which_test_outcomes_are_waived(self):
        for outcome, label in ((None, 'ACCEPT UNTESTED'), ('failed', 'ACCEPT ANYWAY'),
                               ('passed', 'ACCEPT PROFILE')):
            with self.subTest(outcome=outcome):
                if outcome:
                    self.finish_test(outcome)
                session = ViewSession(['trial.back'])
                self.ui.view = session.view = recovery.RecoveryView(session)
                self.assertIsNone(self.ui.trial(self.report))
                self.assertTrue(any(label in command for frame in session.frames for command in frame))
                self.assertIn('trial.accept', self.ui.view.buttons)

    def test_finished_test_shows_its_status_verdict_and_facts(self):
        session = ViewSession(['result.back'])
        self.ui.view = session.view = recovery.RecoveryView(session)
        def run(kind, root, cpu_target_hz=None):
            return {'status': 'inconclusive', 'detail': 'Full stress conditions were not established.\n'
                                                        'Clock: 97.5% at 1248 MHz, throttled'}
            yield
        self.ui.workload = run
        self.ui.run_test('cpu-stability', self.report)
        frame = ' '.join(command for frame in session.frames for command in frame)
        for text in ('INCONCLUSIVE', 'Full stress conditions were not established.', 'CLOCK', 'throttled'):
            self.assertIn(text, frame)

    def test_one_line_result_keeps_the_failure_explanation_and_back_navigation(self):
        session = ViewSession(['message.back'])
        self.ui.view = session.view = recovery.RecoveryView(session)
        self.ui.result_page('cpu-stability', {'status': 'failed', 'detail': 'CPU helper did not finish.'})
        frame = ' '.join(session.frames[0])
        self.assertIn('FAILED', frame)
        self.assertIn('CPU helper did not finish.', frame)
        self.assertEqual(set(self.ui.view.buttons), {'message.back'})

    def workload(self, result=None, error=None, steps=2):
        state = {'closed': False, 'calls': []}
        def run(kind, root, cpu_target_hz=None):
            state['calls'].append((kind, cpu_target_hz))
            try:
                for index in range(steps):
                    yield (index + 1) / steps
                if error:
                    raise error
                return result or {'status': 'passed', 'detail': 'fixture verification'}
            finally:
                state['closed'] = True
        self.ui.workload = run
        self.view.wait_action.return_value = None
        return state

    def test_cpu_workload_receives_selected_profile_frequency(self):
        for candidate, expected in (('yes', 1248000000), ('no', 1008000000)):
            with self.subTest(candidate=candidate):
                state = self.workload()
                result = self.ui.run_test('cpu-stability', dict(self.report, candidate_present=candidate))
                self.assertEqual(result['status'], 'passed')
                self.assertEqual(state['calls'], [('cpu-stability', expected)])
                self.assertTrue(state['closed'])

    def test_test_result_is_recorded_for_the_tested_profile_and_boot(self):
        self.workload()
        self.ui.run_test('cpu-stability', self.report)
        self.assertEqual(self.ui.result('cpu-stability', self.report)['status'], 'passed')
        self.assertIsNone(self.results.take_interrupted())

    def test_cancel_stops_the_workload_and_records_cancelled(self):
        state = self.workload(steps=100)
        self.view.wait_action.return_value = 'test.cancel'
        result = self.ui.run_test('emmc-integrity', self.report)
        self.assertEqual(result['status'], 'cancelled')
        self.assertTrue(state['closed'])
        self.assertEqual(self.ui.result('emmc-integrity', self.report)['status'], 'cancelled')

    def test_workload_error_is_a_failed_result(self):
        self.workload(error=RuntimeError('eMMC data mismatch at byte 0.'))
        result = self.ui.run_test('emmc-integrity', self.report)
        self.assertEqual(result, {'status': 'failed', 'detail': 'eMMC data mismatch at byte 0.'})
        self.assertEqual(self.ui.result('emmc-integrity', self.report)['status'], 'failed')

    def test_display_failure_closes_workload_and_leaves_it_interrupted(self):
        state = self.workload()
        self.view.send.side_effect = RuntimeError('display unavailable')
        with self.assertRaisesRegex(RuntimeError, 'display unavailable'):
            self.ui.run_test('cpu-stability', self.report)
        self.assertTrue(state['closed'])
        self.assertEqual(self.results.take_interrupted(), 'cpu-stability')

    def test_wrapped_line_count_follows_word_wrap(self):
        font = recovery.UI_FONT_MEDIUM  # 52 columns in 740 px
        for text, lines in (('a' * 52, 1), ('a' * 53, 2), ('a' * 26 + ' ' + 'b' * 25, 1),
                            ('a' * 26 + ' ' + 'b' * 26, 2), ('a' * 110, 3)):
            with self.subTest(text=text):
                self.assertEqual(recovery.RecoveryView.wrapped_lines(text, 740, font), lines)

    def test_stale_tap_during_polled_test_is_ignored(self):
        session = ViewSession(['0:test.cancel', '1:test.cancel'])
        view = recovery.RecoveryView(session)
        view.begin_page('TEST')
        view.button('test.cancel', 10, 10, 100, 50, 'CANCEL')
        self.assertEqual(view.wait_action(timeout=.1), 'test.cancel')

    def test_profile_operations_replace_buttons_with_wait_screen_before_blocking(self):
        session = ViewSession(['1:tuning.cpu', 'tuning.back'])
        self.ui.view = session.view = recovery.RecoveryView(session)
        self.ui.view.begin_page('OLD MENU')
        self.ui.view.button('tuning.cpu', 10, 10, 100, 50, 'CPU')
        def prepare():
            self.assertTrue(session.frames)
            self.assertEqual(self.ui.view.buttons, {})
            self.assertIn('Please wait', ' '.join(session.frames[-1]))
            return dict(self.report, candidate_present='no')
        self.profiles.prepare.side_effect = prepare
        self.profiles.confirmed_active.return_value = True
        self.assertFalse(self.ui.run())
        self.profiles.prepare.assert_called_once_with()
        self.profiles.apply_trial.assert_not_called()

    def test_entry_offers_stock_recovery_for_damaged_configuration(self):
        self.profiles.prepare.return_value = self.report
        self.profiles.boot_problem.return_value = 'Stock is active; old boot files remain: OLD.DTB.'
        self.profiles.can_restore_stock.return_value = True
        self.view.wait_action.return_value = 'recovery.restore'
        self.assertTrue(self.ui.run())
        self.assertEqual(self.reboots, ['reboot'])
        self.profiles.restore_stock.assert_called_once_with(self.report)
        self.profiles.apply_trial.assert_not_called()

    def test_cancelled_or_declined_repair_never_changes_profiles(self):
        self.profiles.prepare.return_value = self.report
        self.profiles.boot_problem.return_value = 'Old boot files remain.'
        self.profiles.can_restore_stock.return_value = True
        for action, confirmed in (('recovery.back', True), ('recovery.restore', False)):
            with self.subTest(action=action, confirmed=confirmed):
                self.view.wait_action.return_value = action
                self.view.confirm.return_value = confirmed
                self.assertFalse(self.ui.run())
        self.profiles.restore_stock.assert_not_called()
        self.assertEqual(self.reboots, [])

    def test_failed_repair_reports_the_error_without_rebooting(self):
        self.profiles.prepare.return_value = self.report
        self.profiles.boot_problem.return_value = 'Old boot files remain.'
        self.profiles.can_restore_stock.return_value = True
        self.profiles.restore_stock.side_effect = ProfileError('Stock boot validation failed')
        self.view.wait_action.return_value = 'recovery.restore'
        self.assertFalse(self.ui.run())
        self.assertIn('validation failed', self.view.message.call_args.kwargs['headline'])
        self.assertEqual(self.reboots, [])

    def test_manual_recovery_has_explanation_without_automatic_restore_action(self):
        session = ViewSession(['recovery.back'])
        self.ui.view = session.view = recovery.RecoveryView(session)
        self.profiles.boot_problem.return_value = 'Protected stock boot commands were modified.'
        self.profiles.can_restore_stock.return_value = False
        self.assertFalse(self.ui.recover_configuration(self.report))
        self.assertIn('Protected stock boot commands were modified.', ' '.join(session.frames[0]))
        self.assertEqual(set(self.ui.view.buttons), {'recovery.back'})
        self.profiles.restore_stock.assert_not_called()

    def test_unsupported_emmc_keeps_tuning_open_and_refuses_only_that_axis(self):
        # The real policy, with an excluded eMMC model on a clean stock boot.
        tool = ProfileTool(bundle=ROOT / '.bin/tools/overclocking')
        report = dict(self.report, candidate_present='no', boot_state='clean-stock', family='hs2001v8',
                      hardware='unsupported', available_emmc_mhz='', runtime_timing='9', runtime_voltage='1')
        for name in ('boot_problem', 'axis_problem', 'choices'):
            getattr(self.profiles, name).side_effect = getattr(tool, name)
        self.profiles.prepare.return_value = report
        self.profiles.confirmed_active.return_value = True
        self.view.wait_action.side_effect = ['tuning.emmc', 'tuning.cpu', 'tuning.back']
        self.view.choose.return_value = None
        self.assertFalse(self.ui.run())
        self.assertIn('eMMC', self.view.message.call_args.args[0])
        self.view.choose.assert_called_once()
        self.assertEqual(self.view.choose.call_args.args[0], 'CPU FREQUENCY')
        self.profiles.restore_stock.assert_not_called()
        self.profiles.apply_trial.assert_not_called()

    def test_pending_trial_is_decided_before_an_unsupported_emmc_matters(self):
        report = dict(self.report, candidate_axis='emmc', family='unknown', hardware='unsupported')
        self.profiles.prepare.return_value = report
        self.profiles.trial_state.return_value = 'unconfirmed'
        self.profiles.axis_problem.return_value = 'This eMMC type is not supported.'
        self.profiles.discard.return_value = False
        self.view.wait_action.side_effect = ['trial.discard', 'tuning.back']
        self.profiles.snapshot.return_value = dict(report, candidate_present='no')
        self.profiles.confirmed_active.return_value = True
        self.assertFalse(self.ui.run())
        self.profiles.discard.assert_called_once_with(report)

    def test_diagnostics_is_blue_and_tuning_is_orange_in_system_menu(self):
        session = ViewSession(['1:system.back'])
        ui = recovery.RecoveryUI('Adventurer5M', str(self.root / 'action'), session)
        ui.system_diagnostics_menu()
        self.assertEqual(ui.view.buttons['system.diagnostics'][5:], (False, False))
        self.assertEqual(ui.view.buttons['system.tuning'][5:], (False, True))

    def test_confirmed_mismatch_back_returns_to_menu_without_reboot(self):
        self.profiles.prepare.return_value = dict(self.report, candidate_present='no')
        self.profiles.confirmed_active.return_value = False
        self.view.wait_action.return_value = 'mismatch.back'
        self.assertFalse(self.ui.run())
        self.assertEqual(self.reboots, [])

    def test_confirmed_mismatch_reboot_is_explicit_and_accepts_nothing(self):
        self.profiles.prepare.return_value = dict(self.report, candidate_present='no')
        self.profiles.confirmed_active.return_value = False
        self.view.wait_action.return_value = 'mismatch.reboot'
        self.assertTrue(self.ui.run())
        self.assertEqual(self.reboots, ['reboot'])
        self.profiles.accept.assert_not_called()

    def test_ready_trial_back_never_reboots(self):
        self.profiles.prepare.return_value = self.report
        self.profiles.trial_state.return_value = 'reboot-required'
        self.view.message.return_value = 'message.back'
        self.assertFalse(self.ui.run())
        self.assertEqual(self.reboots, [])

    def test_applied_trial_back_never_reboots_accepts_or_discards(self):
        self.profiles.prepare.return_value = self.report
        self.view.wait_action.return_value = 'trial.back'
        self.assertFalse(self.ui.run())
        self.assertEqual(self.reboots, [])
        self.profiles.accept.assert_not_called()
        self.profiles.discard.assert_not_called()

    def test_mismatch_screen_has_explicit_actions_and_back_does_not_restart(self):
        session = ViewSession(['1:mismatch.back'])
        self.ui.view = recovery.RecoveryView(session)
        report = dict(self.report, candidate_present='no', boot_state='clean-stock',
                      runtime_cpu_limit_hz='912000000', runtime_cpu_hz='480000000', runtime_emmc_hz='25000000')
        self.assertFalse(self.ui.confirmed_mismatch(report))
        self.assertEqual(set(self.ui.view.buttons), {'mismatch.back', 'mismatch.stock', 'mismatch.reboot'})
        self.assertEqual(self.reboots, [])


if __name__ == '__main__':
    unittest.main()
