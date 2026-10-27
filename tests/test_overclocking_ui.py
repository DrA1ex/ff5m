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
from overclocking_profiles import ProfileError
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
        self.profiles.hardware_problem.return_value = ''
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

    def test_test_row_label_and_values_share_one_font_and_line(self):
        session = ViewSession(['tuning.back'])
        self.ui.view = session.view = recovery.RecoveryView(session)
        self.results.accept('a' * 64, {'cpu-stability': 'passed'})
        self.ui.tuning_page(dict(self.report, candidate_present='no'))
        row = [command for command in session.frames[0] if '"TESTS"' in command or 'STABILITY PASSED' in command]
        self.assertEqual(len(row), 2)
        for part in ('-f "JetBrainsMono 10pt"', ' 180 '):
            self.assertTrue(all(part in command for command in row), row)
        # No hint or note line competes with the settings.
        self.assertNotIn('Tune one setting', ' '.join(session.frames[0]))

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

    def test_entry_progress_moves_past_cleanup_before_building_the_menu(self):
        session = ViewSession(['tuning.back'])
        self.ui.view = session.view = recovery.RecoveryView(session)
        self.profiles.prepare.return_value = dict(self.report, candidate_present='no')
        self.profiles.confirmed_active.return_value = True
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(self.ui.run())
        frames = [' '.join(frame) for frame in session.frames]
        steps = [next(index for index, frame in enumerate(frames) if 'Step {}/5'.format(step) in frame)
                 for step in range(1, 6)]
        self.assertEqual(steps, sorted(steps))
        menu = next(index for index, frame in enumerate(frames) if 'CONFIRMED PROFILE' in frame)
        self.assertGreater(menu, steps[-1])

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
        self.assertEqual([text for text in texts if text.startswith('eMMC ') and 'APPLIED' not in text],
                         ['eMMC INTEGRITY'] * 2)
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

    def test_accept_label_states_which_test_outcomes_are_waived_and_is_always_highlighted(self):
        for outcome, label in ((None, 'ACCEPT UNTESTED'), ('failed', 'ACCEPT ANYWAY'),
                               ('passed', 'ACCEPT PROFILE')):
            with self.subTest(outcome=outcome):
                if outcome:
                    self.finish_test(outcome)
                session = ViewSession(['trial.back'])
                self.ui.view = session.view = recovery.RecoveryView(session)
                self.assertIsNone(self.ui.trial(self.report))
                self.assertEqual(self.ui.view.buttons['trial.accept'][4:], (label, False, True))

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
        # The verdict sentence beside the table is a small muted note; the table is the result.
        note = next(command for frame in session.frames for command in frame if 'Full stress' in command)
        self.assertIn('-c ' + recovery.UI_MUTED, note)
        self.assertIn(recovery.UI_FONT_SMALL, note)

    def test_one_line_result_is_a_large_outcome_with_its_explanation(self):
        session = ViewSession(['message.back'])
        self.ui.view = session.view = recovery.RecoveryView(session)
        self.ui.result_page('cpu-stability', {'status': 'failed', 'detail': 'CPU helper did not finish within 240 s.'})
        frame = session.frames[0]
        outcome = next(command for command in frame if '-t "FAILED"' in command)
        self.assertIn('-c ' + recovery.UI_DANGER, outcome)
        self.assertIn(recovery.UI_FONT_TITLE_BOLD, outcome)
        self.assertTrue(any('CPU helper did not finish' in command for command in frame))
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
        self.profiles.hardware_problem.return_value = 'Stock is active; old boot files remain: OLD.DTB.'
        self.profiles.can_restore_stock.return_value = True
        self.view.wait_action.return_value = 'recovery.restore'
        self.assertTrue(self.ui.run())
        self.assertEqual(self.reboots, ['reboot'])
        self.profiles.restore_stock.assert_called_once_with(self.report)
        self.profiles.apply_trial.assert_not_called()

    def test_cancelled_or_declined_repair_never_changes_profiles(self):
        self.profiles.prepare.return_value = self.report
        self.profiles.hardware_problem.return_value = 'Old boot files remain.'
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
        self.profiles.hardware_problem.return_value = 'Old boot files remain.'
        self.profiles.can_restore_stock.return_value = True
        self.profiles.restore_stock.side_effect = ProfileError('Stock boot validation failed')
        self.view.wait_action.return_value = 'recovery.restore'
        self.assertFalse(self.ui.run())
        self.assertIn('validation failed', self.view.message.call_args.kwargs['headline'])
        self.assertEqual(self.reboots, [])

    def test_manual_recovery_has_explanation_without_automatic_restore_action(self):
        session = ViewSession(['recovery.back'])
        self.ui.view = session.view = recovery.RecoveryView(session)
        self.profiles.hardware_problem.return_value = 'Protected stock boot commands were modified.'
        self.profiles.can_restore_stock.return_value = False
        self.assertFalse(self.ui.recover_configuration(self.report))
        self.assertIn('Protected stock boot commands were modified.', ' '.join(session.frames[0]))
        self.assertEqual(set(self.ui.view.buttons), {'recovery.back'})
        self.profiles.restore_stock.assert_not_called()

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
