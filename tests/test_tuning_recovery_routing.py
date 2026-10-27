"""Recovery routing while profilectl's hardware trial is unresolved.

Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
This file may be distributed under the terms of the GNU GPLv3 license.
"""

import importlib
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".py"))
recovery = importlib.import_module("recovery")


class ViewSession:
    def __init__(self, actions=()):
        self.actions = list(actions)
        self.view = None
    def start(self):
        pass
    def close(self):
        pass
    def send(self, commands):
        pass
    def next_tap(self, timeout=None):
        if not self.actions:
            raise AssertionError("Unexpected navigation after test actions")
        return "{}:{}".format(self.view.generation, self.actions.pop(0))


class RecoveryTrialGateTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.candidate = self.root / "candidate"
        for name, value in (("BOOT_FLAGS", str(self.root)), ("TRIAL_CANDIDATE", str(self.candidate))):
            patcher = mock.patch.object(recovery, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.tuning = mock.Mock()
        self.tuning.return_value.run.return_value = False
        modules = mock.patch.dict(sys.modules, {"overclocking_ui": types.SimpleNamespace(TuningUI=self.tuning)})
        modules.start()
        self.addCleanup(modules.stop)

    def ui(self, actions):
        session = ViewSession(actions)
        ui = recovery.RecoveryUI("Adventurer5M", str(self.root / "action"), session)
        session.view = ui.view
        return ui

    def action(self):
        return (self.root / "action").read_text().strip()

    def test_pending_trial_reboot_requests_exactly_another_recovery_entry(self):
        self.candidate.touch()
        for name in ("BOOT_FLAG_RECOVERY_FAILURE", "BOOT_FLAG_FAILURE", "stock-hard"):
            (self.root / name).touch()
        self.ui([]).reboot()
        self.assertEqual(self.action(), "recovery-reboot")
        self.assertTrue((self.root / "BOOT_FLAG_SKIP").exists())
        self.assertFalse((self.root / "BOOT_FLAG_RECOVERY_FAILURE").exists())
        self.assertFalse((self.root / "BOOT_FLAG_FAILURE").exists())
        self.assertTrue((self.root / "stock-hard").exists())

    def test_reboot_without_trial_is_an_ordinary_reboot(self):
        self.ui([]).reboot()
        self.assertEqual(self.action(), "reboot")
        self.assertFalse((self.root / "BOOT_FLAG_SKIP").exists())

    def test_next_entry_is_published_before_interrupted_recovery_protection_is_removed(self):
        (self.root / "BOOT_FLAG_RECOVERY_FAILURE").touch()
        seen = []
        def observe(path):
            seen.append((path, (self.root / "BOOT_FLAG_SKIP").exists(),
                         (self.root / "BOOT_FLAG_RECOVERY_FAILURE").exists()))
        with mock.patch.object(recovery, "sync_directory", side_effect=observe):
            recovery.request_next_recovery(True)
        self.assertEqual(seen[0], (str(self.root), True, True))
        self.assertEqual(seen[-1], (str(self.root), True, False))

    def test_pending_trial_opens_tuning_first_and_follows_it_on_reset(self):
        self.candidate.touch()
        self.tuning.return_value.run.return_value = True
        self.ui([]).run()
        view, reboot, follow_trial = self.tuning.call_args.args
        follow_trial()
        self.assertTrue((self.root / "BOOT_FLAG_SKIP").exists())
        self.candidate.unlink()
        follow_trial()
        self.assertFalse((self.root / "BOOT_FLAG_SKIP").exists())

    def test_ui_restart_inside_recovery_does_not_reopen_tuning_but_keeps_the_gate(self):
        self.candidate.touch()
        with mock.patch.dict(recovery.os.environ, {"RECOVERY_UI_RESTARTED": "1"}):
            ui = self.ui(["main.boot", "message.back", "main.reboot", "confirm.accept"])
        ui.run()
        self.assertEqual(self.tuning.return_value.run.call_count, 1)
        self.assertEqual(self.action(), "recovery-reboot")

    def test_main_menu_actions_that_leave_recovery_wait_for_the_trial(self):
        self.candidate.touch()
        ui = self.ui(["main.boot", "message.back", "main.reboot", "confirm.accept"])
        ui.run()
        self.assertEqual(self.tuning.return_value.run.call_count, 2)
        self.assertEqual(self.action(), "recovery-reboot")

    def test_broken_tuning_lifts_the_gate_and_stops_returning_to_recovery(self):
        self.candidate.touch()
        (self.root / "BOOT_FLAG_SKIP").touch()
        self.tuning.return_value.run.side_effect = ProfileFailure("profilectl is unavailable.")
        ui = self.ui(["message.back", "main.reboot", "confirm.accept"])
        ui.run()
        self.assertFalse((self.root / "BOOT_FLAG_SKIP").exists())
        self.assertEqual(self.action(), "reboot")

    def test_ordinary_recovery_does_not_load_tuning(self):
        del sys.modules["overclocking_ui"]
        self.ui(["main.reboot", "confirm.accept"]).run()
        self.assertNotIn("overclocking_ui", sys.modules)
        self.assertEqual(self.action(), "reboot")


class ProfileFailure(Exception):
    pass


class RecoveryShellRoutingTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.candidate = self.root / "candidate"
        script = (ROOT / ".shell/boot/recovery.sh").read_text()
        for source in ("common.sh", "boot/stock_identity.sh"):
            script = script.replace("/opt/config/mod/.shell/" + source, "/dev/null")
        script = script.replace("/opt/config/mod/.shell/boot/boot_mode.sh", str(ROOT / ".shell/boot/boot_mode.sh"))
        script = script.replace("/data/.forge-x-overclocking/profile/candidate", str(self.candidate))
        script = script.replace("/opt/config/mod/BOOT_FLASH_IMAGE", str(self.root / "flash-request"))
        self.script = self.root / "recovery.sh"
        self.script.write_text(script)
        # The real Python reboot() writes the action and boot flags the shell consumes.
        self.reboot_ui = " ".join(map(shlex.quote, [sys.executable, "-c", (
            "import sys; sys.path.insert(0, sys.argv[1]); import recovery; "
            "recovery.BOOT_FLAGS, recovery.TRIAL_CANDIDATE = sys.argv[2], sys.argv[3]; "
            "recovery.RecoveryUI('Adventurer5M', sys.argv[4], object()).reboot()"),
            str(ROOT / ".py"), str(self.root), str(self.candidate), str(self.root / "action")]))

    def run_shell(self, extra=""):
        body = '''
source "$1"
trap - EXIT HUP INT TERM
RUN_ROOT="$2"
RECOVERY_ACTION="$2/action"
BOOT_SKIP_F="$2/BOOT_FLAG_SKIP"
BOOT_RECOVERY_FAILURE_F="$2/BOOT_FLAG_RECOVERY_FAILURE"
BOOT_FAILURE_F="$2/BOOT_FLAG_FAILURE"
SKIP_MOD_F="$2/stock"
SKIP_MOD_SOFT_F="$2/stock-soft"
SKIP_MOD_HARD_F="$2/stock-hard"
INIT_FLAG="$2/ready"
SCRIPTS=/nonexistent
find_vendor_curl() { return 1; }
find_vendor_cacert() { return 1; }
load_stock_printer_identity() { FIRMWARE_MACHINE=Adventurer5M; }
mount_recovery_devpts() { :; }
mount_recovery_debugfs() { :; }
mount_recovery_chroot() { :; }
start_recovery_touch() { :; }
start_recovery_netd() { :; }
start_recovery_clock() { :; }
valid_firmware_path() { return 0; }
handoff_to_firmware_installer() { echo "installed $1" >> "$RUN_ROOT/events"; }
run_recovery_ui() {
    printf 'ui %s\\n' "$RECOVERY_NOTICE" >> "$RUN_ROOT/events"
    ''' + self.reboot_ui + '''
}
cleanup_recovery_runtime() { echo cleanup >> "$RUN_ROOT/events"; }
reboot() { echo reboot >> "$RUN_ROOT/events"; }
sync() { :; }
''' + extra + "\nrun_recovery_lifecycle"
        return subprocess.run(["bash", "-c", body, "recovery-routing", str(self.script), str(self.root)],
                              capture_output=True, text=True, timeout=20)

    def events(self):
        return (self.root / "events").read_text().splitlines()

    def test_pending_trial_reboot_returns_to_recovery_and_keeps_stock_escape_flags(self):
        self.candidate.touch()
        for name in ("BOOT_FLAG_RECOVERY_FAILURE", "stock-hard"):
            (self.root / name).touch()
        result = self.run_shell()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertTrue((self.root / "BOOT_FLAG_SKIP").exists())
        self.assertFalse((self.root / "BOOT_FLAG_RECOVERY_FAILURE").exists())
        self.assertTrue((self.root / "stock-hard").exists())
        self.assertEqual(self.events(), ["ui ", "cleanup", "reboot"])

    def test_reboot_without_trial_prepares_a_normal_boot(self):
        for name in ("BOOT_FLAG_SKIP", "BOOT_FLAG_RECOVERY_FAILURE", "BOOT_FLAG_FAILURE", "stock-hard"):
            (self.root / name).touch()
        result = self.run_shell()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        for name in ("BOOT_FLAG_SKIP", "BOOT_FLAG_RECOVERY_FAILURE", "BOOT_FLAG_FAILURE", "stock-hard"):
            self.assertFalse((self.root / name).exists(), name)
        self.assertEqual(self.events(), ["ui ", "cleanup", "reboot"])

    def test_pending_trial_postpones_automatic_firmware_installation(self):
        self.candidate.touch()
        (self.root / "flash-request").write_text("Adventurer5M-test.tgz\n")
        result = self.run_shell()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse((self.root / "flash-request").exists())
        events = self.events()
        self.assertFalse(any(event.startswith("installed") for event in events))
        self.assertIn("postponed", events[0])

    def test_firmware_request_without_trial_installs_as_before(self):
        (self.root / "flash-request").write_text("Adventurer5M-test.tgz\n")
        result = self.run_shell()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.events(), ["installed /data/forge-x-recovery/Adventurer5M-test.tgz"])
        self.assertFalse((self.root / "flash-request").exists())

    def test_only_the_first_ui_run_is_an_entry(self):
        self.candidate.touch()
        result = self.run_shell('''
start_recovery_ssh() { RECOVERY_SSH_ACTIVE=1; }
run_recovery_ui() {
    echo "ui restarted=$RECOVERY_UI_RESTARTED" >> "$RUN_ROOT/events"
    if [ "$RECOVERY_SSH_ACTIVE" -eq 0 ]; then echo ssh > "$RECOVERY_ACTION"; return 0; fi
    ''' + self.reboot_ui + '''
}''')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.events(), ["ui restarted=0", "ui restarted=1", "cleanup", "reboot"])

    def test_failing_recovery_never_requests_its_own_next_entry(self):
        self.candidate.touch()
        (self.root / "BOOT_FLAG_SKIP").touch()
        result = self.run_shell('run_recovery_ui() { return 7; }')
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertFalse((self.root / "BOOT_FLAG_SKIP").exists())


if __name__ == "__main__":
    unittest.main()
