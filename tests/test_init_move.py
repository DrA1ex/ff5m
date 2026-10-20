## Tests for section-based initialization migration
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import configparser
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CFG_DIR = ROOT / ".cfg"


class InitMoveTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="init-move-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "opt/config"
        self.rules = self.config / "mod/.cfg"
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.rules.mkdir(parents=True)
        self.scripts = self.root / "scripts/commands"
        self.scripts.mkdir(parents=True)
        for name, answer in (("zdisplay.sh", "STOCK"), ("zconf.sh", "0")):
            script = self.scripts / name
            script.write_text("#!/bin/sh\necho " + answer + "\n")
            script.chmod(0o755)
        for name in (
            "init.move.cfg", "data.init.move.cfg", "init.display.stock.cfg",
            "init.cfg", "data.init.cfg", "init.base.cfg", "data.init.base.cfg",
            "tuning.off.cfg", "init.plugins.cfg",
        ):
            shutil.copyfile(CFG_DIR / name, self.rules / name)

    def write(self, name, content):
        path = self.config / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def run_shell(self, command):
        source = (ROOT / ".shell/init-main.sh").read_text()
        function = source[source.index("extract_move_section() {"):source.index("rotate_logs() {")]
        function = function.replace("/tmp/", str(self.runtime) + "/")
        function = function.replace("/opt/config", str(self.config))
        command = function + "\n" + command
        env = dict(os.environ,
                   MOD=str(self.root / "chroot"),
                   PY="/opt/config/mod/.py",
                   SCRIPTS=str(self.root / "scripts"),
                   CMDS=str(self.scripts),
                   VAR_PATH=str(self.root / "variables"),
                   KLIPPER_USER_CONFIG=str(self.config / "mod_data/plugins.cfg"),
                   HOST_PYTHON=sys.executable,
                   CFG_BACKUP_SCRIPT=str(ROOT / ".py/cfg_backup.py"))
        return subprocess.run(["bash", "-e", "-c", command], env=env,
                              cwd=self.runtime, capture_output=True, text=True, timeout=20)

    def run_fix_config(self):
        return self.run_shell("""
            sed() {
                "$HOST_PYTHON" -c 'from pathlib import Path; import sys; p=Path(sys.argv[1]); s=p.read_text(); p.write_text(s[:-2]+"\\n" if s.endswith(",\\n") else s)' "$3"
            }
            chroot() {
                case "$3" in
                    */cfg_backup.py) shift 3; "$HOST_PYTHON" "$CFG_BACKUP_SCRIPT" "$@" ;;
                    *) return 1 ;;
                esac
            }
            sync() { :; }
            fix_config
        """)

    def test_existing_sections_use_one_awk_pass_without_sources_or_temp_files(self):
        self.write("printer.cfg", "  [heater_bed]  \n[fan_generic auxiliary]\n")
        (self.rules / "init.move.cfg").write_text(
            "# Section migration\n [heater_bed] # Keep existing PID\n\n[fan_generic auxiliary]\n")
        (self.rules / "data.init.move.cfg").unlink()

        result = self.run_shell("""
            awk() { echo call >> awk.calls; command awk "$@"; }
            if prepare_init_move actual.cfg data.cfg; then exit 1; fi
        """)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout + result.stderr, "")
        self.assertEqual((self.runtime / "awk.calls").read_text().splitlines(), ["call"])
        self.assertFalse((self.runtime / "actual.cfg").exists())
        self.assertFalse((self.runtime / "data.cfg").exists())

    def test_first_install_migrates_every_real_rule_from_defaults(self):
        self.write("printer.cfg", "")
        self.write("printer.base.cfg", "")
        rules = configparser.ConfigParser()
        rules.read(self.rules / "init.move.cfg")

        result = self.run_fix_config()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("@@", result.stdout + result.stderr)
        migrated = configparser.ConfigParser(interpolation=None)
        migrated.read(self.config / "printer.cfg")
        for section in rules.sections():
            with self.subTest(section=section):
                self.assertTrue(migrated.has_section(section))
                self.assertTrue(dict(migrated[section]))

    def test_existing_bed_section_skips_migration_even_with_saved_pid(self):
        original = (
            "[heater_bed]\nheater_pin: CUSTOM\npid_Kp: 41.0\n"
            "#*# <---------------------- SAVE_CONFIG ---------------------->\n"
            "#*# [heater_bed]\n#*# pid_ki = 5.5\n"
        )
        self.write("printer.cfg", original)
        self.write("printer.base.cfg", (CFG_DIR / "data.init.move.cfg").read_text())

        result = self.run_fix_config()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        updated = (self.config / "printer.cfg").read_text()
        self.assertIn("heater_pin: CUSTOM", updated)
        self.assertIn("pid_Kp: 41.0", updated)
        self.assertIn("#*# pid_ki = 5.5", updated)
        self.assertNotIn("pid_Kp: 32.79", updated)
        self.assertFalse((self.runtime / "init.move.actual.cfg").exists())

    def test_missing_bed_moves_from_base_and_then_is_idempotent(self):
        self.write("printer.cfg", "")
        self.write("printer.base.cfg", "[heater_bed]\nheater_pin: CUSTOM\npid_Kp: 41.0\n")

        first = self.run_fix_config()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        updated = (self.config / "printer.cfg").read_text()
        self.assertIn("[heater_bed]\nheater_pin: CUSTOM\npid_Kp: 41.0", updated)
        self.assertNotIn("[heater_bed]", (self.config / "printer.base.cfg").read_text())

        second = self.run_fix_config()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual((self.config / "printer.cfg").read_text(), updated)

    def test_new_section_moves_without_reapplying_existing_bed(self):
        self.write("printer.cfg", "[heater_bed]\npid_Kp: 41.0\n")
        self.write("printer.base.cfg", "[heater_bed]\npid_Kp: 32.79\n[fan_generic auxiliary]\npin: PA1\n")
        (self.rules / "init.move.cfg").write_text("[heater_bed]\n[fan_generic auxiliary]\n")
        with (self.rules / "init.base.cfg").open("a") as out:
            out.write("\n-[fan_generic auxiliary]\n")

        result = self.run_fix_config()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        updated = (self.config / "printer.cfg").read_text()
        self.assertIn("pid_Kp: 41.0", updated)
        self.assertNotIn("pid_Kp: 32.79", updated)
        self.assertIn("[fan_generic auxiliary]\npin: PA1", updated)

    def test_missing_sections_use_base_and_defaults_independently(self):
        self.write("printer.cfg", "[mcu]\nserial: /tmp/klipper\n")
        self.write("printer.base.cfg", "[fan_generic auxiliary]\npin: PA1\n")
        (self.rules / "init.move.cfg").write_text("[heater_bed]\n[fan_generic auxiliary]\n")
        with (self.rules / "init.base.cfg").open("a") as out:
            out.write("\n-[fan_generic auxiliary]\n")

        result = self.run_fix_config()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        updated = (self.config / "printer.cfg").read_text()
        self.assertIn("[heater_bed]\nheater_pin: PB9", updated)
        self.assertIn("[fan_generic auxiliary]\npin: PA1", updated)
        self.assertNotIn("[fan_generic auxiliary]", (self.config / "printer.base.cfg").read_text())

    def test_missing_source_warns_and_other_config_jobs_continue(self):
        self.write("printer.cfg", "[mcu]\nserial: /tmp/klipper\n")
        self.write("printer.base.cfg", "[mcu]\nserial: /tmp/klipper\n")
        (self.rules / "init.move.cfg").write_text("[missing_section]\n")

        result = self.run_fix_config()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("@@ No migration source for [missing_section]", result.stdout)
        self.assertIn("[include printer.base.cfg]", (self.config / "printer.cfg").read_text())
        self.assertNotIn("[missing_section]", (self.config / "printer.cfg").read_text())
