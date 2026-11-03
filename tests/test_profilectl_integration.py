"""Profile-tool contracts used by the standalone Recovery integration.

Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
This file may be distributed under the terms of the GNU GPLv3 license.
"""

import fcntl
import hashlib
import json
import pathlib
import subprocess
import tempfile
import unittest

from tests.profilectl_support import Fixture, ROOT, STOCK_BOOT, STOCK_SHA, meta


def write_manifest(path, manifest):
    fields = []
    for key, value in manifest.items():
        encoded = json.dumps(value, separators=(',', ':')) if not isinstance(value, list) else (
            "[\n" + ",\n".join(json.dumps(row, separators=(',', ':')) for row in value) + "\n]")
        fields.append(json.dumps(key) + ":" + encoded)
    path.write_text("{\n" + ",\n".join(fields) + "\n}\n")


def snapshot(fixture):
    lines = fixture.run('snapshot').stdout.splitlines()
    result = {}
    for line in lines:
        key, value = line.split('=', 1)
        if key in result:
            raise AssertionError('duplicate snapshot field: ' + key)
        result[key] = value
    return result


def contents(directory):
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob('*') if path.is_file()}


class RecoveryIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='t113-recovery-')
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.fixture = Fixture(self.root, 'printer')
        self.dt = self.fixture.d / 'loaded-tree'
        self.dt.mkdir()
        self.fixture.baseenv['T113_DT_ROOT'] = str(self.dt)

    def loaded_candidate(self):
        candidate = meta(self.fixture.state / 'candidate')
        for name, value in (
                ('source-sha256', '27501dc48c5ad4c71e3a12742e3ef2a162d6a2d8285bbbac82488c7fade79e21'),
                ('cpu-profile', candidate['cpu_profile']),
                ('emmc-mhz', candidate['emmc_mhz']),
                ('emmc-family', candidate['family'])):
            (self.dt / ('forge-x,' + name)).write_bytes(value.encode() + b'\0')

    def test_diagnostics_need_no_boot_writes_and_preserve_boot_files_and_environment(self):
        fixture = self.fixture
        (fixture.boot / '.sunxi.dtb.tmp.other').write_bytes(b'keep this file')
        boot_before = contents(fixture.boot)
        environment_before = fixture.envdb.read_bytes()
        fixture.run('--apply', 'diagnostics', extra={'T113_TEST_BOOT_MOUNT_STATE': 'ro'})
        self.assertEqual(contents(fixture.boot), boot_before)
        self.assertEqual(fixture.envdb.read_bytes(), environment_before)
        reports = list((fixture.state / 'diagnostics').glob('doctor-*.txt'))
        self.assertEqual(len(reports), 1)
        self.assertIn('boot state: clean-stock', reports[0].read_text())

    def test_accept_and_cancel_refuse_read_only_boot_without_partial_profile_changes(self):
        fixture = self.fixture
        fixture.run('--apply', 'test', 'emmc', '150')
        fixture.simulate_candidate_boot()
        fixture.run('--apply', 'verify', extra={'T113_TEST_BOOT_MOUNT_STATE': 'ro'})
        before = contents(fixture.d)
        for command in ('enable', 'cancel'):
            with self.subTest(command=command):
                result = fixture.run('--apply', command, ok=False, extra={'T113_TEST_BOOT_MOUNT_STATE': 'ro'})
                self.assertIn('read-only', result.stdout)
                self.assertEqual(contents(fixture.d), before)

    def test_missing_legacy_override_remains_valid_through_verification_and_promotion(self):
        fixture = self.fixture
        fixture.run('--apply', 'test', 'emmc', '150')
        candidate = fixture.state / 'candidate'
        candidate.write_text(candidate.read_text().replace('untested_emmc_override=0\n', ''))
        self.assertEqual(snapshot(fixture)['candidate_valid'], 'yes')
        fixture.simulate_candidate_boot()
        self.loaded_candidate()
        fixture.run('--apply', 'verify')
        self.assertEqual(meta(candidate)['untested_emmc_override'], '0')
        fixture.run('--apply', 'enable')
        current = fixture.state / 'current'
        current.write_text(current.read_text().replace('untested_emmc_override=0\n', ''))
        before = contents(fixture.d)
        report = snapshot(fixture)
        self.assertEqual(report['current_valid'], 'yes')
        self.assertEqual(report['boot_state'], 'clean-managed')
        self.assertEqual(contents(fixture.d), before)

    def test_new_candidate_without_legacy_override_can_be_discarded_before_or_after_boot(self):
        for booted in (False, True):
            with self.subTest(booted=booted):
                fixture = Fixture(self.root, 'discard-' + str(booted))
                fixture.run('--apply', 'test', 'emmc', '150')
                if booted:
                    fixture.simulate_candidate_boot()
                report = snapshot(fixture)
                self.assertEqual(report['candidate_valid'], 'yes')
                self.assertEqual(report['boot_state'], 'candidate-armed')
                fixture.run('--apply', 'cancel')
                self.assertFalse((fixture.state / 'candidate').exists())
                self.assertEqual(snapshot(fixture)['boot_state'], 'clean-stock')

    def test_original_backup_preserves_the_complete_environment_and_stays_immutable(self):
        fixture = self.fixture
        fixture.setenv('boot_normal', 'run vendor_optional_dependency')
        fixture.setenv('boot_recovery', 'sunxi_flash read 43000000 recovery;bootm 43000000')
        fixture.setenv('empty_vendor_value', '')
        fixture.setenv('vendor_diagnostic_value', '  value=with spaces; ${literal_reference}  ')
        fixture.setenv('long_vendor_value', 'fixture-value-' * 1000)
        expected = fixture.envdb.read_bytes()
        fixture.run('--apply', 'backup')
        original = fixture.state / 'original'
        environment = original / 'environment.txt'
        self.assertEqual(environment.read_bytes(), expected)
        self.assertEqual(environment.stat().st_mode & 0o077, 0)
        self.assertEqual(original.stat().st_mode & 0o077, 0)
        sums = dict((name, digest) for digest, name in
                    (line.split(None, 1) for line in (original / 'SHA256SUMS').read_text().splitlines()))
        self.assertEqual(sums['environment.txt'], hashlib.sha256(expected).hexdigest())
        fixture.setenv('vendor_diagnostic_value', 'changed after the original backup')
        fixture.run('--apply', 'backup')
        self.assertEqual(environment.read_bytes(), expected)
        self.assertEqual(fixture.log.read_text(), '')
        environment.write_bytes(expected + b'unexpected_change=fixture\n')
        result = fixture.run('--apply', 'backup', ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(fixture.log.read_text(), '')
        self.assertEqual(environment.read_bytes(), expected + b'unexpected_change=fixture\n')

    def test_optional_vendor_helpers_do_not_gate_trials_or_change_during_recovery(self):
        fixture = self.fixture
        # Neither helper is created when absent, including during rollback.
        fixture.run('--apply', 'test', 'cpu', '1.2')
        fixture.run('--apply', 'cancel')
        fixture.run('--apply', 'rollback')
        for name in ('boot_normal', 'boot_recovery'):
            self.assertNotIn(name, fixture.env())
        values = {'boot_normal': 'run absent_vendor_dependency;fatload mmc 0:1 0x43000000 ../VENDOR.BIN',
                  'boot_recovery': 'run boot_recovery;setenv bootcmd vendor-private-flow'}
        for name, value in values.items():
            fixture.setenv(name, value)
        before = fixture.envdb.read_bytes()
        report = snapshot(fixture)
        self.assertEqual(report['boot_state'], 'clean-stock')
        self.assertEqual(report['unknown_boot_variables'], '')
        self.assertEqual(report['missing_boot_dependencies'], '')
        self.assertEqual(report['unsafe_boot_paths'], '')
        self.assertEqual(report['stock_helpers_valid'], '1')
        self.assertEqual(fixture.envdb.read_bytes(), before)
        fixture.run('--apply', 'test', 'emmc', '50')
        fixture.run('--apply', 'cancel')
        fixture.run('--apply', 'rollback')
        fixture.run('--apply', 'cleanup')
        for name, value in values.items():
            self.assertEqual(fixture.env().get(name), value)
        writes = fixture.log.read_text().splitlines()
        self.assertFalse(set(values) & set(writes))

    def test_unknown_cleanup_preserves_ignored_helpers_and_their_opaque_references(self):
        fixture = self.fixture
        fixture.setenv('boot_normal', 'fatload mmc 0:1 0x43000000 VENDOR.BIN;run missing_vendor_helper')
        fixture.setenv('boot_recovery', '')
        (fixture.boot / 'VENDOR.BIN').write_bytes(b'vendor image')
        fixture.setenv('custom_boot', 'run boot_normal boot_recovery')
        report = snapshot(fixture)
        self.assertEqual(report['boot_state'], 'stock-active-with-unknown-remnants')
        self.assertEqual(report['unknown_boot_variables'], 'custom_boot')
        self.assertEqual(report['unknown_boot_files'], '')
        self.assertEqual(report['missing_boot_dependencies'], '')
        fixture.run('--apply', '--danger-cleanup-unknown', 'cleanup')
        self.assertNotIn('custom_boot', fixture.env())
        self.assertEqual(fixture.env()['boot_normal'],
                         'fatload mmc 0:1 0x43000000 VENDOR.BIN;run missing_vendor_helper')
        self.assertEqual(fixture.env()['boot_recovery'], '')
        self.assertEqual((fixture.boot / 'VENDOR.BIN').read_bytes(), b'vendor image')
        self.assertEqual(snapshot(fixture)['boot_state'], 'clean-stock')

    def test_snapshot_reads_environment_once_and_a_later_command_observes_external_changes(self):
        reads = self.root / 'reads'
        self.fixture.baseenv['T113_ENV_READ_LOG'] = str(reads)
        self.assertEqual(snapshot(self.fixture)['boot_state'], 'clean-stock')
        self.assertEqual(reads.read_text().splitlines(), ['read'])
        self.fixture.setenv('custom_boot', 'run boot_uImage')
        report = snapshot(self.fixture)
        self.assertEqual(report['boot_state'], 'stock-active-with-unknown-remnants')
        self.assertEqual(len(reads.read_text().splitlines()), 2)

    def test_stock_cpu_is_the_1008_mhz_boot_clock(self):
        self.assertEqual(snapshot(self.fixture)['confirmed_cpu_hz'], '1008000000')
        self.fixture.run('--apply', 'test', 'emmc', '50')
        candidate = self.fixture.state / 'candidate'
        self.assertEqual(meta(candidate)['cpu_hz'], '1008000000')
        self.assertEqual(snapshot(self.fixture)['candidate_cpu_hz'], '1008000000')
        self.fixture.simulate_candidate_boot()
        self.loaded_candidate()
        self.fixture.run('--apply', 'verify')

    def test_stock_remnant_cleanup_only_writes_changes_and_reloads_after_each_write(self):
        self.fixture.run('--apply', 'backup')
        self.fixture.setenv('custom_boot', 'run boot_uImage')
        reads = self.root / 'reads'
        self.fixture.baseenv['T113_ENV_READ_LOG'] = str(reads)
        self.fixture.run('--apply', '--danger-cleanup-unknown', 'cleanup')
        writes = self.fixture.log.read_text().splitlines()
        self.assertEqual(writes, ['custom_boot'])
        self.assertEqual(len(reads.read_text().splitlines()), len(writes) + 1)
        self.assertNotIn('custom_boot', self.fixture.env())

    def test_environment_readback_detects_a_write_that_did_not_persist(self):
        before = self.fixture.env()
        result = self.fixture.run('--apply', 'test', 'cpu', '1.2', ok=False,
                                  extra={'T113_SETENV_NO_EFFECT_NAME': 'fgx_boot_test'})
        self.assertIn('readback mismatch', result.stdout)
        self.assertEqual(self.fixture.env(), before)
        self.assertFalse((self.fixture.state / 'candidate').exists())

    def test_snapshot_reports_stock_without_changing_boot_or_state(self):
        before = contents(self.fixture.d)
        report = snapshot(self.fixture)
        self.assertEqual(report['format'], '1')
        self.assertEqual(report['boot_state'], 'clean-stock')
        self.assertEqual(report['hardware'], 'supported')
        self.assertEqual(report['family'], 'hs2001v8')
        self.assertEqual(report['runtime_cpu_hz'], '1008000000')
        self.assertEqual(report['runtime_cpu_limit_hz'], 'unknown')
        self.assertEqual(report['runtime_emmc_hz'], '25000000')
        self.assertEqual(report['candidate_present'], 'no')
        self.assertEqual(report['current_present'], 'no')
        self.assertEqual(contents(self.fixture.d), before)

    def test_snapshot_distinguishes_loaded_trial_from_persisted_fallback(self):
        self.fixture.run('--apply', 'test', 'cpu', '1.25')
        self.fixture.simulate_candidate_boot()
        self.loaded_candidate()
        report = snapshot(self.fixture)
        self.assertEqual(report['pending_boot'], 'confirmed')
        self.assertEqual(report['boot_state'], 'candidate-armed')
        self.assertEqual(report['candidate_cpu_hz'], '1248000000')
        self.assertEqual(report['loaded_cpu_profile'], '1248')
        self.assertEqual(report['runtime_cpu_limit_hz'], '1248000000')
        self.fixture.run('--apply', 'verify')
        self.fixture.run('--apply', 'enable')
        accepted = snapshot(self.fixture)
        self.assertEqual(accepted['current_cpu_hz'], '1248000000')
        self.assertEqual(accepted['candidate_present'], 'no')

    def test_remnants_are_named_without_mutation_then_explicit_cleanup_preserves_original(self):
        self.fixture.run('--apply', 'backup')
        original = contents(self.fixture.state / 'original')
        self.fixture.setenv('custom_boot', 'fatload mmc 0:1 0x44864e70 OLD.DTB')
        (self.fixture.boot / 'OLD.DTB').write_bytes(b'old profile')
        before = contents(self.fixture.d)
        report = snapshot(self.fixture)
        self.assertEqual(report['boot_state'], 'stock-active-with-unknown-remnants')
        self.assertEqual(report['unknown_boot_variables'], 'custom_boot')
        self.assertEqual(report['unknown_boot_files'], 'OLD.DTB')
        self.assertEqual(report['stock_helpers_valid'], '1')
        self.assertEqual(report['manual_recovery_required'], 'no')
        self.assertEqual(contents(self.fixture.d), before)
        self.fixture.run('--apply', '--danger-cleanup-unknown', 'cleanup')
        self.assertEqual(snapshot(self.fixture)['boot_state'], 'clean-stock')
        self.assertEqual(contents(self.fixture.state / 'original'), original)
        self.assertNotIn('custom_boot', self.fixture.env())
        self.assertFalse((self.fixture.boot / 'OLD.DTB').exists())

    def test_modified_protected_boot_helper_is_explained_and_cleanup_refused(self):
        self.fixture.setenv('boot_uImage', 'echo changed')
        report = snapshot(self.fixture)
        self.assertEqual(report['boot_state'], 'inconsistent')
        self.assertEqual(report['stock_helpers_valid'], '0')
        self.assertEqual(report['manual_recovery_required'], 'yes')
        boot = contents(self.fixture.boot)
        metadata = contents(self.fixture.state)
        environment = self.fixture.envdb.read_bytes()
        self.fixture.run('--apply', '--danger-cleanup-unknown', 'cleanup', ok=False)
        self.assertEqual(contents(self.fixture.boot), boot)
        self.assertEqual(contents(self.fixture.state), metadata)
        self.assertEqual(self.fixture.envdb.read_bytes(), environment)
        self.assertEqual(self.fixture.log.read_text(), '')

    def test_loaded_provenance_and_policy_allow_idle_cpu_during_verify(self):
        self.fixture.run('--apply', 'test', 'cpu', '1.3')
        self.fixture.simulate_candidate_boot()
        self.loaded_candidate()
        self.fixture.cpu.write_text('480000\n')
        self.fixture.run('--apply', 'verify')
        self.assertEqual(meta(self.fixture.state / 'candidate')['phase'], 'passed')

    def test_wrong_loaded_profile_cannot_pass_with_expected_clock(self):
        self.fixture.run('--apply', 'test', 'cpu', '1.3')
        self.fixture.simulate_candidate_boot()
        self.loaded_candidate()
        (self.dt / 'forge-x,cpu-profile').write_bytes(b'1200\0')
        result = self.fixture.run('--apply', 'verify', ok=False)
        self.assertIn('booted DTB', result.stdout)
        self.assertEqual(meta(self.fixture.state / 'candidate')['phase'], 'armed')

    def test_stock_trial_verifies_the_boot_clock_without_cpufreq(self):
        self.fixture.run('--apply', 'test', 'emmc', '50')
        self.fixture.simulate_candidate_boot()
        self.loaded_candidate()
        self.fixture.clock(1104)
        result = self.fixture.run('--apply', 'verify', ok=False)
        self.assertIn('expected 1008000000', result.stdout)
        self.fixture.clock(1008)
        self.fixture.run('--apply', 'verify')
        self.assertEqual(meta(self.fixture.state / 'candidate')['phase'], 'passed')

    def test_snapshot_does_not_publish_unvalidated_candidate_fields(self):
        self.fixture.run('--apply', 'test', 'cpu', '1.2')
        with (self.fixture.state / 'candidate').open('a') as output:
            output.write('unexpected=ignored\n')
        report = snapshot(self.fixture)
        self.assertNotIn('unexpected', report)
        path = self.fixture.state / 'candidate'
        path.write_text(path.read_text().replace('cpu_hz=1200000000', 'cpu_hz=invalid'))
        report = snapshot(self.fixture)
        self.assertEqual(report['boot_state'], 'inconsistent')
        self.assertEqual(report['candidate_valid'], 'no')
        self.assertNotIn('candidate_cpu_hz', report)

    def test_stock_cpu_trial_preserves_confirmed_emmc_and_original_backup(self):
        self.fixture.arm_verify_enable('cpu', '1.2')
        self.fixture.arm_verify_enable('emmc', '50', 'boot-c')
        original = contents(self.fixture.state / 'original')
        self.fixture.run('--apply', 'test', 'cpu', 'stock')
        report = snapshot(self.fixture)
        self.assertEqual(report['candidate_cpu_profile'], 'stock')
        self.assertEqual(report['candidate_emmc_mhz'], '50')
        self.fixture.simulate_candidate_boot('boot-d')
        self.loaded_candidate()
        self.fixture.run('--apply', 'verify')
        self.fixture.run('--apply', 'enable')
        self.assertEqual(contents(self.fixture.state / 'original'), original)

    def test_stock_emmc_trial_preserves_confirmed_cpu(self):
        self.fixture.arm_verify_enable('cpu', '1.25')
        self.fixture.run('--apply', 'test', 'emmc', 'stock')
        report = snapshot(self.fixture)
        self.assertEqual(report['candidate_cpu_profile'], '1248')
        self.assertEqual(report['candidate_emmc_mhz'], '25')

    def migration(self, **extra):
        target = self.fixture.d / 'overclocking' / 'profile'
        self.fixture.run('--apply', 'migrate-state', self.fixture.state,
                         extra={'T113_STATE_DIR': str(target), **extra})
        return target

    def test_migration_preserves_original_and_confirmed_profile_without_boot_writes(self):
        self.fixture.arm_verify_enable('cpu', '1.2')
        before = contents(self.fixture.state)
        environment = self.fixture.envdb.read_bytes()
        boot = contents(self.fixture.boot)
        target = self.migration()
        self.assertFalse(self.fixture.state.exists())
        self.assertEqual(contents(target), before)
        self.assertEqual(self.fixture.envdb.read_bytes(), environment)
        self.assertEqual(contents(self.fixture.boot), boot)
        self.fixture.baseenv['T113_STATE_DIR'] = str(target)
        report = snapshot(self.fixture)
        self.assertEqual(report['boot_state'], 'clean-managed')
        self.assertEqual(report['current_cpu_hz'], '1200000000')

    def test_migration_conflict_refuses_to_merge_or_replace_originals(self):
        self.fixture.run('--apply', 'backup')
        before = contents(self.fixture.state)
        target = self.fixture.d / 'overclocking' / 'profile'
        target.mkdir(parents=True)
        (target / 'original').mkdir()
        (target / 'original' / 'foreign').write_bytes(b'do not replace')
        self.fixture.run('--apply', 'migrate-state', self.fixture.state, ok=False,
                         extra={'T113_STATE_DIR': str(target)})
        self.assertEqual(contents(self.fixture.state), before)
        self.assertEqual((target / 'original' / 'foreign').read_bytes(), b'do not replace')

    def test_migration_uses_existing_profile_mutation_lock(self):
        self.fixture.lock.mkdir()
        with (self.fixture.lock / 'owner').open('a') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            target = self.fixture.d / 'overclocking' / 'profile'
            result = self.fixture.run('--apply', 'migrate-state', self.fixture.state, ok=False,
                                      extra={'T113_STATE_DIR': str(target)})
            self.assertIn('another mutation', result.stdout)
            self.assertFalse(target.exists())
        self.migration()

    def test_interrupted_migration_is_atomic_and_retry_is_idempotent(self):
        self.fixture.run('--apply', 'backup')
        before = contents(self.fixture.state)
        target = self.fixture.d / 'overclocking' / 'profile'
        for point in ('migration:before-rename', 'migration:after-rename'):
            self.fixture.run('--apply', 'migrate-state', self.fixture.state, ok=False,
                             extra={'T113_STATE_DIR': str(target), 'T113_FAIL_POINT': point})
            owner = target if target.exists() else self.fixture.state
            self.assertEqual(contents(owner), before)
        self.migration()
        self.assertEqual(contents(target), before)

    def test_dry_run_does_not_create_target_or_modify_legacy_state(self):
        target = self.fixture.d / 'overclocking' / 'profile'
        before = contents(self.fixture.d)
        self.fixture.run('--dry-run', 'migrate-state', self.fixture.state,
                         extra={'T113_STATE_DIR': str(target)})
        self.assertEqual(contents(self.fixture.d), before)
        self.assertFalse(target.parent.exists())


class IdentityAndCustomBootAuditTest(unittest.TestCase):
    """Real CLI fixtures: identification fixes and unchanged restore classification."""

    CUSTOM = ('part list mmc 0;fatload mmc 0:1 0x43000000 uImage;'
              'fatload mmc 0:1 0x44864e70 sunxi.dtb;fdt addr 0x44864e70;fdt resize;'
              'fdt set /soc@3000000/sdmmc@4020000 max-frequency <0x08f0d180>;'
              'bootm 0x43000000 - 0x44864e70')
    USER_CID = 'd6010335384133393811000000000001'

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='t113-audit-')
        self.addCleanup(directory.cleanup)
        self.root = pathlib.Path(directory.name)

    def fixture(self, name='printer', **kwargs):
        return Fixture(self.root, name, **kwargs)

    def test_same_model_different_specimens_and_normalized_case(self):
        cids = ('d6010335384133393811000000000002', self.USER_CID,
                self.USER_CID.upper(), '  ' + self.USER_CID + '  ')
        for index, cid in enumerate(cids):
            with self.subTest(cid=cid):
                fixture = self.fixture(str(index), cid=cid)
                report = snapshot(fixture)
                self.assertEqual(report['model_identity'], 'd6:0103:353841333938:11')
                self.assertEqual(report['hardware'], 'supported')
                self.assertEqual(report['family'], 'hs2001v8')
                self.assertEqual(report['runtime_family_evidence'], 'hs2001v8')
                self.assertNotIn(cid.strip().lower(), fixture.run('snapshot').stdout)

    def test_recognized_model_still_requires_trial_and_verification(self):
        fixture = self.fixture(cid=self.USER_CID)
        fixture.run('--apply', 'test', 'emmc', '50')
        candidate = meta(fixture.state / 'candidate')
        self.assertEqual(candidate['phase'], 'armed')
        self.assertEqual(snapshot(fixture)['candidate_valid'], 'yes')
        fixture.run('--apply', 'enable', ok=False)
        self.assertFalse((fixture.state / 'current').exists())
        fixture.run('--apply', 'cancel')

    def test_environment_stderr_survives_without_polluting_snapshot_protocol(self):
        fixture = self.fixture()
        environment = dict(fixture.baseenv, T113_TEST_ENV_STDERR='fw_printenv: fixture diagnostic')
        tool = ROOT / 'bin/t113-profilectl'
        result = subprocess.run([str(tool), 'snapshot'], env=environment, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn('fw_printenv: fixture diagnostic', result.stderr)
        self.assertNotIn('fixture diagnostic', result.stdout)
        self.assertEqual(dict(line.split('=', 1) for line in result.stdout.splitlines())['format'], '1')
        environment['T113_TEST_ENV_EXIT'] = '7'
        result = subprocess.run([str(tool), 'snapshot'], env=environment, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('fw_printenv: fixture diagnostic', result.stderr)
        self.assertIn('cannot read U-Boot environment', result.stderr)


    def test_missing_malformed_and_unreadable_cids_do_not_block_supported_modes(self):
        for index, value in enumerate(('', 'd6', self.USER_CID + '00', 'g' * 32,
                                       self.USER_CID[:10] + ' ' + self.USER_CID[10:],
                                       self.USER_CID + '\n' + self.USER_CID, None, 'directory')):
            with self.subTest(value=value):
                fixture = self.fixture(str(index))
                if value is None:
                    fixture.cid.unlink()
                elif value == 'directory':
                    fixture.cid.unlink()
                    fixture.cid.mkdir()
                else:
                    fixture.cid.write_text(value + '\n')
                report = snapshot(fixture)
                self.assertIn(report['cid_status'], ('invalid', 'unavailable'))
                self.assertEqual(report['model_identity'], 'unknown')
                self.assertEqual(report['hardware'], 'supported')
                fixture.run('--apply', 'test', 'emmc', '50')
                self.assertEqual(meta(fixture.state / 'candidate')['emmc_mhz'], '50')

    def test_identity_does_not_substitute_for_or_override_runtime_evidence(self):
        fixture = self.fixture(cid=self.USER_CID)
        fixture.ios.unlink()
        report = snapshot(fixture)
        self.assertEqual(report['hardware'], 'unsupported')
        self.assertEqual(report['runtime_family_evidence'], 'unknown')
        fixture.run('--apply', 'test', 'emmc', '50', ok=False)
        fixture.set_ios(25, 0, 0)
        report = snapshot(fixture)
        self.assertEqual(report['runtime_family_evidence'], 'legacy3v3')
        self.assertEqual(report['family'], 'legacy3v3')
        fixture.run('--apply', 'test', 'emmc', '50')
        self.assertEqual(meta(fixture.state / 'candidate')['family'], 'legacy3v3')

    def test_optional_model_restrictions_apply_to_snapshot_list_and_direct_mutation(self):
        fixture = self.fixture(cid=self.USER_CID)
        manifest = json.loads((ROOT / 'manifest-v2.json').read_text())
        model = 'd6:0103:353841333938:11'
        manifest['hardware'].extend(dict(manifest['hardware'][0], models=model, frequencies_mhz=f)
                                    for f in ('25,50,100', '50,150'))
        path = self.root / 'restricted.json'
        write_manifest(path, manifest)
        fixture.baseenv['T113_MANIFEST'] = str(path)
        self.assertEqual(snapshot(fixture)['available_emmc_mhz'], '50')
        output = fixture.run('list', 'emmc').stdout
        self.assertEqual([int(word) for word in output.split() if word.isdecimal()], [50])
        for frequency in ('stock', '25', '100', '150'):
            result = fixture.run('--apply', 'test', 'emmc', frequency, ok=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(fixture.log.read_text(), '')
            self.assertFalse((fixture.state / 'candidate').exists())
        fixture.run('--apply', 'test', 'emmc', '50')
        self.assertEqual(meta(fixture.state / 'candidate')['emmc_mhz'], '50')
        fixture.simulate_candidate_boot()
        fixture.run('--apply', 'verify')
        fixture.run('--apply', 'enable')
        self.assertEqual(meta(fixture.state / 'current')['emmc_mhz'], '50')

    def test_supplied_partition_helpers_stay_visible_in_redacted_diagnostics(self):
        fixture = self.fixture()
        fixture.setenv('boot_normal', 'sunxi_flash read 43000000 boot;bootm 43000000')
        fixture.setenv('boot_recovery', 'sunxi_flash read 43000000 recovery;bootm 43000000')
        fixture.setenv('custom_boot', "setenv password 'private fixture; hidden tail';run boot_normal")
        fixture.setenv('serial_boot', 'echo serial#=fixture-hidden-serial;run boot_normal')
        report = snapshot(fixture)
        self.assertEqual(report['boot_state'], 'stock-active-with-unknown-remnants')
        self.assertEqual(set(report['unknown_boot_variables'].split(',')), {'custom_boot', 'serial_boot'})
        output = fixture.run('doctor').stdout
        self.assertIn('boot_normal=sunxi_flash read 43000000 boot;bootm 43000000', output)
        self.assertIn('boot_recovery=sunxi_flash read 43000000 recovery;bootm 43000000', output)
        self.assertIn('expected_stock_bootcmd=' + STOCK_BOOT, output)
        self.assertNotIn('private fixture', output)
        self.assertNotIn('hidden tail', output)
        self.assertNotIn('fixture-hidden-serial', output)
        self.assertEqual(fixture.log.read_text(), '')

    def test_both_supplied_boot_chains_are_custom_with_no_structural_blocker(self):
        for name, bootcmd in (
                ('permanent', 'run setargs_mmc boot_dsp0 boot_uImage_test'),
                ('transitional', "setenv bootcmd '" + STOCK_BOOT +
                 "';saveenv;run setargs_mmc boot_dsp0 boot_uImage_test")):
            with self.subTest(chain=name):
                fixture = self.fixture(name, cid=self.USER_CID)
                fixture.setenv('boot_uImage_test', self.CUSTOM)
                fixture.setenv('bootcmd', bootcmd)
                before = contents(fixture.d)
                report = snapshot(fixture)
                self.assertEqual(report['boot_state'], 'unknown/custom')
                self.assertEqual(report['stock_helpers_valid'], '1')
                self.assertEqual(report['unknown_boot_variables'], 'boot_uImage_test')
                self.assertEqual(report['boot_graph_variables'], 'boot_dsp0,boot_uImage_test,bootcmd,setargs_mmc')
                self.assertEqual(report['boot_file_references'], 'sunxi.dtb,uImage')
                self.assertEqual(report['unknown_boot_files'], '')
                self.assertEqual(report['missing_boot_dependencies'], '')
                self.assertEqual(report['unsafe_boot_paths'], '')
                self.assertEqual(report['manual_recovery_required'], 'no')
                self.assertEqual(report['stock_dtb_sha256'], STOCK_SHA)
                self.assertEqual(contents(fixture.d), before)

    def test_restore_blockers_are_independent_from_the_custom_chain(self):
        for name in ('dtb', 'helper', 'missing', 'path'):
            with self.subTest(blocker=name):
                fixture = self.fixture(name, cid=self.USER_CID)
                fixture.setenv('boot_uImage_test', self.CUSTOM)
                fixture.setenv('bootcmd', 'run setargs_mmc boot_dsp0 boot_uImage_test')
                if name == 'dtb':
                    (fixture.boot / 'sunxi.dtb').write_bytes(b'changed')
                elif name == 'helper':
                    fixture.setenv('boot_uImage', 'echo changed')
                elif name == 'missing':
                    fixture.setenv('boot_uImage_test')
                else:
                    fixture.setenv('boot_uImage_test', self.CUSTOM.replace(' sunxi.dtb;', ' ../sunxi.dtb;'))
                before = contents(fixture.d)
                report = snapshot(fixture)
                if name == 'dtb':
                    self.assertEqual(report['boot_state'], 'unknown/custom')
                    self.assertNotEqual(report['stock_dtb_sha256'], STOCK_SHA)
                    self.assertEqual(report['manual_recovery_required'], 'no')
                else:
                    self.assertEqual(report['boot_state'], 'inconsistent')
                    self.assertEqual(report['manual_recovery_required'], 'yes')
                if name == 'helper': self.assertEqual(report['stock_helpers_valid'], '0')
                if name == 'missing': self.assertEqual(report['missing_boot_dependencies'], 'boot_uImage_test')
                if name == 'path': self.assertEqual(report['unsafe_boot_paths'], '../sunxi.dtb')
                self.assertEqual(contents(fixture.d), before)

    def test_read_only_commands_do_not_change_boot_profile_or_clocks(self):
        fixture = self.fixture()
        before = contents(fixture.d)
        for command in ('snapshot', 'status', 'detect', 'doctor'):
            fixture.run(command)
            self.assertEqual(contents(fixture.d), before)
