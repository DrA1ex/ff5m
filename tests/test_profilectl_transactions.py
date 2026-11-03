# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
# This file may be distributed under the terms of the GNU GPLv3 license.

"""Public profilectl transaction and recovery contracts with real native helpers."""

import os
from pathlib import Path
import shutil
import shlex
import subprocess
import tempfile
import unittest

from tests.profilectl_support import (
    FGX, FIXTURES, MANAGED_BOOT, STOCK_BOOT, STOCK_SETARGS, STOCK_SHA, TOOL,
    Fixture, check, meta, prepare_verified_managed, sha)

class ProfilectlTransactionTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="t113-profilectl-tests-")
        self.addCleanup(self.directory.cleanup)
        self.tmp = self.directory.name


    def test_bundle_validates_with_the_published_runtime_inputs(self):
        fixture = Fixture(self.tmp, 'bundle')
        result = fixture.run('validate-bundle')
        self.assertIn('Bundle validation: PASS', result.stdout)
        self.assertIn('boot state: clean-stock', fixture.run('status').stdout)

    def test_runtime_validation_rejects_malformed_stock_helper_records(self):
        fixture = Fixture(self.tmp, 'invalid-environment')
        valid = [name + '=' + fixture.env()[name] for name in ('setargs_mmc', 'boot_dsp0', 'boot_uImage')]
        cases = {
            'duplicate': valid + valid[:1],
            'missing': valid[:2],
            'empty': valid[:2] + ['boot_uImage='],
            'missing-dtb-load': valid[:2] + ['boot_uImage=bootm 0x43000000 - 0x44864e70'],
            'command-after-boot': valid[:2] + [valid[2] + ';echo unexpected'],
        }
        for name, rows in cases.items():
            with self.subTest(case=name):
                source = Path(self.tmp) / (name + '.env')
                source.write_text('\n'.join(rows) + '\n')
                fixture.run('validate-bundle', ok=False, extra={'T113_STOCK_ENV_FILE': str(source)})

    def test_invalid_or_inapplicable_cli_overrides_leave_profiles_unchanged(self):
        fixture = Fixture(self.tmp, 'invalid-flags')
        before = fixture.envdb.read_bytes()
        for arguments in (('--force', 'test', 'cpu', '1.2'),
                          ('--danger-cleanup-unknown', 'status'),
                          ('--apply', '--danger-allow-untested-emmc', 'test', 'cpu', '1.2'),
                          ('--apply', '--danger-assume-stock-axes', 'test', 'cpu', '1.2')):
            with self.subTest(arguments=arguments):
                fixture.run(*arguments, ok=False)
                self.assertEqual(fixture.envdb.read_bytes(), before)
                self.assertFalse((fixture.state / 'candidate').exists())

    def test_validation_cache_avoids_revalidation_until_explicit_check_or_new_boot(self):
        fixture = Fixture(self.tmp, 'cache')
        fixture.baseenv['T113_TEST_ASSUME_BUNDLE_VALID'] = '0'
        wrapper, log = fixture.d / 'fgx-wrapper', fixture.d / 'native-calls'
        wrapper.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> " + shlex.quote(str(log)) +
                           '\nexec ' + shlex.quote(str(FGX)) + ' "$@"\n')
        wrapper.chmod(0o755)
        fixture.baseenv['T113_FGX_FDT'] = str(wrapper)
        def builds():
            calls = log.read_text().splitlines()
            log.write_text('')
            return sum(call.startswith('build ') for call in calls)
        fixture.run('--apply', 'backup')
        validation_builds = builds()
        self.assertGreater(validation_builds, 1)
        fixture.run('--apply', 'test', 'cpu', '1.2')
        self.assertEqual(builds(), 1, 'A cached trial should build only its own DTB')
        fixture.run('validate-bundle')
        self.assertEqual(builds(), validation_builds)
        fixture.run('--apply', 'cancel')
        fixture.bootid.write_text('new-boot\n')
        fixture.run('--apply', 'test', 'cpu', '1.2')
        self.assertEqual(builds(), validation_builds + 1)

    def test_unknown_and_assumption(self):
        tmp = self.tmp
        f=Fixture(tmp,'unknown','hs2001v8',cid='deadbeef'*4)
        f.run('--apply','test','emmc','50')
        check('candidate_valid=yes' in f.run('snapshot').stdout,'new candidate was rejected after mode-based eligibility')
        f.run('--apply','cancel')
        g=Fixture(tmp,'assume','hs2001v8'); g.clk.unlink()
        p=g.run('--apply','test','cpu','1.2',ok=False); check('--danger-assume-stock-axes' in p.stdout,'missing axes did not require explicit danger assumption')
        g.run('--apply','--danger-assume-stock-axes','test','cpu','1.2'); check(meta(g.state/'candidate')['cpu_hz']=='1200000000','danger stock assumption failed'); g.run('--apply','cancel')
        h=Fixture(tmp,'assume-custom','hs2001v8'); h.clk.unlink(); h.setenv('bootcmd','run custom_boot'); h.setenv('custom_boot','fatload mmc 0:1 0x44864e70 custom.DTB;bootm 0x43000000 - 0x44864e70')
        p=h.run('--apply','--danger-assume-stock-axes','test','cpu','1.2',ok=False); check('unknown/custom boot state' in p.stdout,'stock assumption hid custom boot state')

    def test_independent_axes(self):
        tmp = self.tmp
        # legacy: eMMC first, then CPU.
        f=Fixture(tmp,'legacy-forward','legacy3v3'); f.arm_verify_enable('emmc','50','boot-b')
        cur=meta(f.state/'current'); check(cur['cpu_profile']=='stock' and cur['emmc_mhz']=='50','eMMC-first changed CPU')
        f.run('--apply','test','cpu','1.2'); c=meta(f.state/'candidate'); check(c['emmc_mhz']=='50','CPU test did not preserve eMMC'); f.simulate_candidate_boot('boot-c'); f.run('--apply','verify'); f.run('--apply','enable')
        cur=meta(f.state/'current'); check(cur['cpu_profile']=='1200' and cur['emmc_mhz']=='50','forward axis state wrong')
        check(len(list(f.boot.glob('FORGE_X_[AB].DTB')))==1,'forward flow did not retain exactly one slot')
        # HS200: CPU first, then eMMC 150.
        g=Fixture(tmp,'hs-reverse','hs2001v8'); g.arm_verify_enable('cpu','1.3','boot-b')
        cur=meta(g.state/'current'); check(cur['cpu_profile']=='1296' and cur['emmc_mhz']=='25','CPU-first changed eMMC')
        g.run('--apply','test','emmc','150'); c=meta(g.state/'candidate'); check(c['cpu_profile']=='1296','eMMC test did not preserve CPU'); g.simulate_candidate_boot('boot-c'); g.run('--apply','verify'); g.run('--apply','enable')
        cur=meta(g.state/'current'); check(cur['cpu_profile']=='1296' and cur['emmc_mhz']=='150','reverse axis state wrong')
        check(len(list(g.boot.glob('FORGE_X_[AB].DTB')))==1,'reverse flow did not retain exactly one slot')
        # Candidate must be regenerated from canonical source (provenance + exact semantics already checked by helper); inspect slot.
        subprocess.run([str(FGX), 'verify', '--file', str(g.boot/cur['slot']), '--cpu', '1296',
                        '--family', 'hs2001v8', '--emmc', '150'], check=True, capture_output=True)

    def test_verify_failures(self):
        tmp = self.tmp
        f=Fixture(tmp,'verify-fail','hs2001v8'); f.run('--apply','test','emmc','150'); f.simulate_candidate_boot('boot-b',emmc=25)
        p=f.run('--apply','verify',ok=False); check('clock mismatch' in p.stdout,'eMMC fallback to 25 was not FAIL'); f.run('--apply','cancel')
        g=Fixture(tmp,'thermal','hs2001v8'); g.run('--apply','test','cpu','1.3'); g.simulate_candidate_boot('boot-b'); g.policy(1296,temp=71000,current=1200)
        p=g.run('--apply','verify',ok=False); check(p.returncode==2 and 'INCONCLUSIVE' in p.stdout,'thermal throttling was not INCONCLUSIVE')
        g.policy(1296,temp=45000,current=1296); g.run('--apply','verify')
        # fatal dmesg rejection.
        h=Fixture(tmp,'dmesg','hs2001v8'); bad=h.d/'bad-dmesg'; bad.write_text('mmc0: timeout error\n'); h.baseenv['T113_DMESG_FILE']=str(bad); h.run('--apply','test','cpu','1.2'); h.simulate_candidate_boot('boot-b'); p=h.run('--apply','verify',ok=False); check('fatal kernel/MMC errors' in p.stdout,'fatal dmesg was accepted')

    def test_atomic_and_cleanup(self):
        tmp = self.tmp
        # Candidate published but metadata interrupted -> normal cleanup of known remnants.
        f=Fixture(tmp,'atomic-candidate','hs2001v8'); p=f.run('--apply','test','cpu','1.2',ok=False,extra={'T113_FAIL_POINT':'meta:candidate:pre-rename'}); check('injected fault' in p.stdout,'candidate fault not injected')
        check(not (f.state/'candidate').exists(),'partial candidate metadata published'); check((f.state/'original').is_dir(),'original was not published first')
        f.run('--apply','cleanup'); check(not list(f.boot.glob('FORGE_X_[AB].DTB')),'known candidate slot remnant not cleaned'); check(not any(k.startswith('fgx_') for k in f.env()),'known helper remnants not cleaned')
        # Original publication interruption is retry-safe and stale temp does not accumulate.
        g=Fixture(tmp,'atomic-original','hs2001v8'); p=g.run('--apply','backup',ok=False,extra={'T113_FAIL_POINT':'original:pre-publish'}); check('injected fault' in p.stdout,'original fault not injected'); check(not (g.state/'original').exists(),'partial original published')
        check(list(g.state.glob('.original.tmp.*')),'stale original temp missing for retry test'); g.run('--apply','backup'); check((g.state/'original'/'SHA256SUMS').is_file(),'original retry failed'); check(not list(g.state.glob('.original.tmp.*')),'stale original temp accumulated')
        # Stale lock reclaimed, live lock blocks.
        h=Fixture(tmp,'locks'); h.lock.mkdir(); (h.lock/'pid').write_text('999999\n'); h.run('--apply','backup'); check(not (h.lock/'pid').exists(),'stale owner metadata not released')
        (h.lock/'pid').write_text(str(os.getpid())+'\n'); p=h.run('--apply','cleanup',ok=False); check('another mutation is active' in p.stdout,'live lock did not block mutation'); shutil.rmtree(h.lock)

    def test_enable_faults(self):
        tmp = self.tmp
        damaged=prepare_verified_managed(tmp,'enable-damaged-helper')
        damaged.setenv('fgx_boot_test','echo CORRUPT')
        p=damaged.run('--apply','enable',ok=False); check('refusing permanent commit' in p.stdout,'enable accepted a corrupted candidate helper before commit')
        check(damaged.env()['bootcmd']==MANAGED_BOOT,'corrupted helper attempt changed permanent bootcmd')
        points=['enable:phase-committing','enable:functional-commit','enable:after-current','enable:after-current-helper','enable:after-final-bootcmd','enable:after-helper-cleanup','enable:after-candidate-cleanup','enable:after-old-slot-cleanup']
        for i,pt in enumerate(points):
            f=prepare_verified_managed(tmp,f'enable-fault-{i}')
            p=f.run('--apply','enable',ok=False,extra={'T113_FAIL_POINT':pt}); check('injected fault' in p.stdout,f'{pt} did not fault')
            st=f.run('status').stdout
            if pt=='enable:phase-committing':
                check('boot state: promotion-not-committed' in st,f'{pt}: wrong classification\n{st}')
                # pre-commit can be cancelled; old current remains.
                f.run('--apply','cancel'); cur=meta(f.state/'current'); check(cur['cpu_profile']=='1200',f'{pt}: cancel damaged old current'); continue
            check(('cleanup required: yes' in st) or ('boot state: clean-managed' in st),f'{pt}: committed state not classified clean/cleanup-required\n{st}')
            f.run('--apply','cleanup'); f.run('--apply','cleanup')
            st=f.run('status').stdout; check('boot state: clean-managed' in st and 'cleanup required: no' in st,f'{pt}: cleanup did not normalize idempotently\n{st}')
            cur=meta(f.state/'current'); check(cur['cpu_profile']=='1296',f'{pt}: committed candidate not retained'); check(len(list(f.boot.glob('FORGE_X_[AB].DTB')))==1,f'{pt}: slot cleanup wrong')

    def test_custom_cleanup_and_rollback(self):
        tmp = self.tmp
        f=Fixture(tmp,'custom-clean','hs2001v8'); f.setenv('custom_boot','fatload mmc 0:1 0x44864e70 CUSTOM.DTB;bootm 0x43000000 - 0x44864e70'); f.setenv('bootcmd','run custom_boot'); (f.boot/'CUSTOM.DTB').write_bytes(b'custom')
        p=f.run('--apply','cleanup',ok=False); check('--danger-cleanup-unknown' in p.stdout,'custom cleanup lacked danger gate')
        p=f.run('--apply','--danger-cleanup-unknown','cleanup'); check('Will remove U-Boot variables:' in p.stdout and 'custom_boot' in p.stdout and 'CUSTOM.DTB' in p.stdout,'danger cleanup plan incomplete')
        e=f.env(); check(e['bootcmd']==STOCK_BOOT,'danger cleanup did not restore exact stock bootcmd'); check('custom_boot' not in e,'custom helper survived danger cleanup'); check('ethaddr' in e and 'setargs_mmc' in e and 'boot_dsp0' in e and 'boot_uImage' in e,'unrelated/protected env was deleted')
        writes=f.log.read_text().splitlines(); check(writes and writes[0]=='bootcmd','destructive cleanup deleted path before switching bootcmd')
        check((f.state/'original'/'SHA256SUMS').is_file(),'danger cleanup did not create original first'); check(not (f.boot/'CUSTOM.DTB').exists(),'custom DTB survived cleanup'); check(f.run('status').stdout.find('clean-stock')>=0,'post-cleanup not stock')
        # Unknown/custom sunxi.dtb is restored only under explicit danger cleanup.
        g=Fixture(tmp,'custom-sunxi'); (g.boot/'sunxi.dtb').write_bytes(b'custom stock replacement')
        p=g.run('--apply','test','cpu','1.2',ok=False); check('unknown/custom boot state' in p.stdout,'custom stock DTB did not block mutation')
        g.run('--apply','--danger-cleanup-unknown','cleanup'); check(sha(g.boot/'sunxi.dtb')==STOCK_SHA,'canonical stock DTB not restored')
        # Rollback always returns vendor stock, not previous managed profile.
        h=Fixture(tmp,'rollback','hs2001v8'); h.arm_verify_enable('cpu','1.3','boot-b'); h.run('--apply','rollback'); e=h.env(); check(e['bootcmd']==STOCK_BOOT,'rollback bootcmd not exact stock'); check(not any(k.startswith('fgx_') for k in e),'rollback left fgx variables'); check(not (h.state/'current').exists() and not (h.state/'candidate').exists(),'rollback left active metadata'); check((h.state/'original').is_dir(),'rollback removed immutable original'); check(not list(h.boot.glob('FORGE_X_[AB].DTB')),'rollback left managed slots'); check(sha(h.boot/'sunxi.dtb')==STOCK_SHA,'normal rollback changed stock sunxi')
        # Rollback without danger activates stock successfully but deliberately leaves unknown remnants.
        r=Fixture(tmp,'rollback-remnants','hs2001v8'); r.arm_verify_enable('cpu','1.2','boot-b')
        r.setenv('custom_boot','fatload mmc 0:1 0x43000000 CUSTOM.BIN;bootm 0x43000000'); (r.boot/'CUSTOM.BIN').write_bytes(b'custom')
        q=r.run('--apply','rollback'); check('Rollback complete' in q.stdout,'rollback with unknown remnants returned failure')
        e=r.env(); check(e['bootcmd']==STOCK_BOOT and 'custom_boot' in e and (r.boot/'CUSTOM.BIN').exists(),'rollback removed unknown remnants without danger or failed to activate stock')
        st=r.run('status').stdout
        check('boot state: stock-active-with-unknown-remnants' in st and 'cleanup required: yes' in st and 'cleanup danger required: yes' in st,'post-rollback unknown-remnant state is wrong')
        q=r.run('--apply','cleanup',ok=False); check('--danger-cleanup-unknown' in q.stdout,'unknown remnants did not retain danger gate')
        r.run('--apply','--danger-cleanup-unknown','cleanup'); check('boot state: clean-stock' in r.run('status').stdout,'danger cleanup did not finish post-rollback remnants')

    def test_custom_cleanup_follows_recursive_helpers_and_non_dtb_loads(self):
        fixture = Fixture(self.tmp, 'recursive-cleanup')
        fixture.setenv('foo', 'run bar')
        fixture.setenv('bar', 'fatload mmc 0:1 0x43000000 CUSTOM.BIN;bootm 0x43000000')
        fixture.setenv('bootcmd', 'run foo')
        artifact = fixture.boot / 'CUSTOM.BIN'
        artifact.write_bytes(b'custom kernel')
        fixture.run('--apply', '--danger-cleanup-unknown', 'cleanup')
        self.assertEqual(fixture.env()['bootcmd'], STOCK_BOOT)
        self.assertFalse({'foo', 'bar'} & fixture.env().keys())
        self.assertFalse(artifact.exists())

    def test_modified_stock_helpers_block_trials_cleanup_and_rollback(self):
        for managed in (False, True):
            for helper in ('setargs_mmc', 'boot_dsp0', 'boot_uImage'):
                with self.subTest(managed=managed, helper=helper):
                    fixture = Fixture(self.tmp, 'modified-' + str(managed) + helper)
                    if managed:
                        fixture.arm_verify_enable('cpu', '1.2')
                    else:
                        fixture.run('--apply', 'backup')
                        fixture.clk.unlink()
                    fixture.setenv(helper, 'echo modified')
                    before = fixture.envdb.read_bytes()
                    for arguments in (('--danger-assume-stock-axes', 'test', 'cpu', '1.3'),
                                      ('--danger-cleanup-unknown', 'cleanup'), ('rollback',)):
                        fixture.run('--apply', *arguments, ok=False)
                        self.assertEqual(fixture.envdb.read_bytes(), before)

    def test_helper_diagnostics_identify_exact_changed_values(self):
        fixture = Fixture(self.tmp, 'changed-whitespace')
        changed = STOCK_SETARGS.replace('setenv  bootargs', 'setenv   bootargs', 1)
        fixture.setenv('setargs_mmc', changed)
        report = dict(line.split('=', 1) for line in fixture.run('snapshot').stdout.splitlines())
        self.assertEqual(report['boot_state'], 'inconsistent')
        self.assertEqual(report['stock_helper_mismatches'], 'setargs_mmc')
        diagnostic = fixture.run('doctor').stdout
        self.assertIn(STOCK_SETARGS, diagnostic)
        self.assertIn(changed, diagnostic)

    def test_cancel_rejects_slot_traversal_and_preserves_stock_dtb(self):
        fixture = Fixture(self.tmp, 'unsafe-slot')
        fixture.run('--apply', 'test', 'cpu', '1.2')
        candidate = fixture.state / 'candidate'
        record = meta(candidate)
        candidate.write_text(candidate.read_text().replace('slot=' + record['slot'], 'slot=../boot/sunxi.dtb'))
        before = (fixture.boot / 'sunxi.dtb').read_bytes()
        fixture.run('--apply', 'cancel', ok=False)
        self.assertEqual((fixture.boot / 'sunxi.dtb').read_bytes(), before)

    def test_boot_access_checks_the_mount_type_and_backing_device(self):
        fixture = Fixture(self.tmp, 'production-mount')
        mounts, log = fixture.d / 'mounts', fixture.d / 'mount.log'
        environment = dict(fixture.baseenv, T113_TEST_MODE='0', T113_MOUNTS_FILE=str(mounts),
                           T113_FAKE_MOUNT_LOG=str(log), T113_MOUNT_CMD=str(FIXTURES / 'bin/mount'),
                           T113_UMOUNT_CMD=str(FIXTURES / 'bin/umount'))
        for source, filesystem, accepted in ((None, None, True), (fixture.p1, 'msdos', False),
                                             ('/dev/not-boot', 'vfat', False)):
            with self.subTest(source=source, filesystem=filesystem):
                initial = '' if source is None else '{} {} {} rw 0 0\n'.format(source, fixture.boot, filesystem)
                mounts.write_text(initial)
                log.write_text('')
                result = subprocess.run([str(TOOL), 'status'], env=environment, capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, accepted, result.stdout + result.stderr)
                self.assertEqual(mounts.read_text(), initial)

    def test_stock_restore_recovers_each_durable_transition(self):
        tmp = self.tmp
        # Dangerous stock-DTB replacement must first switch to an independent canonical DTB slot.
        for point in ['stock-restore:transition-active','stock-restore:stock-dtb-published','stock-restore:exact-stock-active','stock-restore:before-custom-delete']:
            r=Fixture(tmp,'stock-fault-'+point.split(':')[1])
            r.setenv('foo','run bar'); r.setenv('bar','fatload mmc 0:1 0x43000000 CUSTOM.BIN;bootm 0x43000000'); r.setenv('bootcmd','run foo')
            (r.boot/'CUSTOM.BIN').write_bytes(b'x'); (r.boot/'sunxi.dtb').write_bytes(b'custom-sunxi')
            q=r.run('--apply','--danger-cleanup-unknown','cleanup',ok=False,extra={'T113_FAIL_POINT':point})
            check('injected fault' in q.stdout,f'{point}: fault was not injected')
            e=r.env()
            if point in ('stock-restore:transition-active','stock-restore:stock-dtb-published'):
                check(e['bootcmd']=='run setargs_mmc boot_dsp0 fgx_boot_stock',f'{point}: no independent transitional bootcmd')
                check((r.boot/'FORGE_X_STOCK.DTB').exists() and sha(r.boot/'FORGE_X_STOCK.DTB')==STOCK_SHA,f'{point}: transitional canonical DTB missing/corrupt')
                check('boot state: stock-restore-cleanup-required' in r.run('status').stdout,f'{point}: transition not recognized for recovery')
                r.run('--apply','cleanup')
            else:
                check(e['bootcmd']==STOCK_BOOT and sha(r.boot/'sunxi.dtb')==STOCK_SHA,f'{point}: exact stock path was not already safe')
            # Custom objects still require explicit danger authorization after a crash; retry must finish safely.
            if 'foo' in r.env() or (r.boot/'CUSTOM.BIN').exists(): r.run('--apply','--danger-cleanup-unknown','cleanup')
            check(r.env()['bootcmd']==STOCK_BOOT and 'foo' not in r.env() and 'bar' not in r.env(),'stock restore retry left custom variables')
            check(not (r.boot/'CUSTOM.BIN').exists() and not (r.boot/'FORGE_X_STOCK.DTB').exists(),'stock restore retry left custom/transitional files')
            check(sha(r.boot/'sunxi.dtb')==STOCK_SHA and 'boot state: clean-stock' in r.run('status').stdout,'stock restore retry did not end clean-stock')


    def test_repeated_profile_changes_reclaim_old_slots_and_keep_the_original_backup(self):
        fixture = Fixture(self.tmp, 'cycles')
        fixture.run('--apply', 'backup')
        original = (fixture.state / 'original/SHA256SUMS').read_bytes()
        for boot, value in enumerate(('1.2', '1.3', '1.2'), 1):
            with self.subTest(boot=boot, value=value):
                fixture.arm_verify_enable('cpu', value, 'cycle-' + str(boot))
                current = meta(fixture.state / 'current')
                self.assertEqual({path.name for path in fixture.boot.glob('FORGE_X_[AB].DTB')}, {current['slot']})
                self.assertFalse((fixture.state / 'candidate').exists())
                self.assertEqual((fixture.state / 'original/SHA256SUMS').read_bytes(), original)
