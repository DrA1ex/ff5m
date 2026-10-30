## Tests for Forge-X user Klipper plugins and patch priority.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Host-side lifecycle tests; no printer, MCU, chroot or Moonraker required."""
import importlib.util
import itertools
import json
import os
import re
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / ".shell/klipper_overlay.sh"

def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

configfile = load_module("configfile", ROOT / ".py/klipper/patches/configfile.py")
# Stock Klipper uses Python 3.7's readfp; host Python >=3.12 removed the alias.
if not hasattr(configfile.configparser.RawConfigParser, "readfp"):
    configfile.configparser.RawConfigParser.readfp = configfile.configparser.RawConfigParser.read_file

class OverlayTests(unittest.TestCase):
    def setUp(self):
        if not os.environ.get("NATIVE_OVERLAY_BINARY"):
            self.skipTest("native host binary required; run integration from ForgeX Private")
        self.temp = tempfile.TemporaryDirectory(prefix="fx-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.src = self.root / "overlay"
        self.target = self.root / "klippy"
        self.data = self.root / "mod_data"
        self.packages = self.data / "plugins"
        self.runtime = self.root / "runtime"
        self.env = dict(os.environ,
                        KLIPPER_OVERLAY_BIN=os.environ.get("NATIVE_OVERLAY_BINARY", str(ROOT / ".bin/exec/overlay")),
                        FORGE_X_OVERLAY_NO_SYNC="1",
                        KLIPPER_OVERLAY_SRC=str(self.src),
                        KLIPPER_OVERLAY_TARGET=str(self.target),
                        KLIPPER_USER_DIR=str(self.packages),
                        KLIPPER_USER_CONFIG=str(self.data / "plugins.cfg"))
        self.build_tree()

    def build_tree(self):
        for path in (self.src, self.target, self.data, self.runtime):
            if path.exists():
                shutil.rmtree(path)
        for path in (self.src / "plugins", self.src / "patches/extras",
                     self.target / "extras", self.packages, self.runtime):
            path.mkdir(parents=True)
        self.write(self.src / "plugins/mod_params.py", "forge params")
        self.write(self.src / "patches/extras/virtual_sdcard.py", "forge sd")
        self.write(self.src / "patches/gcode.py", "forge core")
        self.write(self.target / "extras/virtual_sdcard.py", "stock sd")
        self.write(self.target / "extras/heater.py", "stock heater")
        self.write(self.target / "gcode.py", "stock core")

    @staticmethod
    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def package(self, name, plugins=None, patches=None, config=""):
        path = self.packages / name
        self.write(path / "config.cfg", config)
        for filename, contents in (plugins or {}).items():
            self.write(path / "plugins" / filename, contents)
        for filename, contents in (patches or {}).items():
            self.write(path / "patches" / filename, contents)
        return path

    def shell(self, commands):
        result = subprocess.run(
            ["bash", "-e", "-c", 'source "$1"; sync() { :; }; ' + commands,
             "test", str(LIBRARY)], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def aggregate(self):
        return (self.data / "plugins.cfg").read_text()

    def test_package_config_relative_includes_and_batch(self):
        for i in range(50):
            package = self.package("pack_%02d" % i, {"custom_%02d.py" % i: "plugin"},
                                   config="[include settings.cfg]\n")
            self.write(package / "settings.cfg", "[test_%d]\nvalue: %d\n" % (i, i))
        self.shell('apply_klipper_patches')
        self.assertEqual(self.aggregate().count("[include "), 50)
        self.assertEqual(len(list((self.target / "extras").glob("custom_*.py"))), 50)
        # Exercise the actual Forge-X parser, through user.cfg -> aggregate ->
        # source config -> relative sibling. No symlink-based config resolution.
        user = self.write(self.data / "user.cfg", "[stepper_x]\nrotation_distance: 39.9\n[include plugins.cfg]\n")
        parser = configfile.PrinterConfig.__new__(configfile.PrinterConfig)
        parser.printer = None
        parsed = parser.read_config(str(user)).fileconfig
        self.assertEqual(parsed.get("test_49", "value"), "49")
        self.shell(': > "$KLIPPER_USER_CONFIG"')
        parsed = parser.read_config(str(user)).fileconfig
        self.assertEqual(parsed.sections(), ["stepper_x"])
        self.assertEqual(parsed.get("stepper_x", "rotation_distance"), "39.9")
        self.assertEqual(user.read_text(), "[stepper_x]\nrotation_distance: 39.9\n[include plugins.cfg]\n")




    def test_stock_restore_creates_or_clears_aggregate_before_restore_even_on_failure(self):
        package = self.package("mine", {"custom.py": "module"}, {"gcode.py": "user core"})
        self.shell("apply_klipper_patches 1")
        user = self.write(self.data / "user.cfg", "[stepper_x]\nrotation_distance: 39.9\n[include plugins.cfg]\n")
        original_user = user.read_text()
        source = (ROOT / ".shell/S55boot").read_text()
        function = source[source.index("restore_stock_config() {"):source.index("report_stock_boot() {")]
        function = function.replace("/opt/config/mod_data", str(self.data))
        for exists, status in itertools.product((False, True), (0, 17, 124)):
            with self.subTest(exists=exists, restore_status=status):
                config = self.data / "plugins.cfg"
                if exists:
                    config.write_text("[include /no/longer/existing/config.cfg]\n")
                else:
                    config.unlink(missing_ok=True)
                self.shell(function + '\n'
                           'mount() { return 0; }; dispose_chroot() { :; }; umount() { :; }; '
                           'run_stock_restore() { test -f "$KLIPPER_USER_CONFIG"; '
                           'test ! -s "$KLIPPER_USER_CONFIG"; return ' + str(status) + '; }; '
                           'actual=0; restore_stock_config || actual=$?; test "$actual" = ' + str(status))
                self.assertEqual(config.read_text(), "")
                self.assertEqual(user.read_text(), original_user)
                self.assertEqual((self.target / "gcode.py").read_text(), "user core")
                self.assertTrue((self.target / "extras/custom.py").is_symlink())
                self.assertTrue((package / "config.cfg").exists())
                parser = configfile.PrinterConfig.__new__(configfile.PrinterConfig)
                parser.printer = None
                self.assertEqual(parser.read_config(str(user)).fileconfig.sections(), ["stepper_x"])

    def test_config_repair_creates_aggregate_before_adding_include_and_keeps_existing_config(self):
        config_root, function, sed = self._config_repair_fixture()
        self.write(config_root / "mod/.cfg/init.plugins.cfg", (ROOT / ".cfg/init.plugins.cfg").read_text())
        tool = self.write(self.root / "batch_tool.py", '\n'.join([
            'import json, pathlib, subprocess, sys',
            'data = pathlib.Path(sys.argv[2])',
            'assert (data / "plugins.cfg").is_file()',
            'assert "[include plugins.cfg]" not in (data / "user.cfg").read_text()',
            'jobs = json.loads(pathlib.Path(sys.argv[1]).read_text())',
            'selected = [job for job in jobs if job["config"] == str(data / "user.cfg")]',
            'assert len(selected) == 1',
            'batch = data / "test_batch.json"',
            'batch.write_text(json.dumps(selected))',
            'subprocess.run([sys.executable, sys.argv[3], "--batch", str(batch)], check=True)',
        ]))
        for exists in (False, True):
            with self.subTest(aggregate_exists=exists):
                user = self.write(self.data / "user.cfg", "[stepper_x]\nrotation_distance: 39.9\n")
                cfg = self.data / "plugins.cfg"
                expected = "[gcode_macro CUSTOM]\ngcode: M117 Test\n" if exists else ""
                if exists:
                    cfg.write_text(expected)
                else:
                    cfg.unlink(missing_ok=True)
                # Hardware/chroot work is stubbed; the actual cfg_backup tool
                # applies the real user.cfg job generated by fix_config.
                command = (function + '\n' + sed +
                           'chroot() { if [ "$4" = --batch ]; then '
                           + json.dumps(sys.executable) + ' ' + json.dumps(str(tool)) + ' "$5" '
                           + json.dumps(str(self.data)) + ' ' + json.dumps(str(ROOT / ".py/cfg_backup.py"))
                           + '; else return 1; fi; }; fix_config')
                self.shell(command)
                self.assertEqual(cfg.read_text(), expected)
                self.assertEqual(user.read_text().count("[include plugins.cfg]"), 1)
                self.assertIn("rotation_distance: 39.9", user.read_text())
                parser = configfile.PrinterConfig.__new__(configfile.PrinterConfig)
                parser.printer = None
                self.assertEqual(parser.read_config(str(user)).fileconfig.get("stepper_x", "rotation_distance"), "39.9")









    def test_stock_plugin_config_failure_still_runs_stock_restore_and_cleanup(self):
        source = (ROOT / ".shell/S55boot").read_text()
        function = source[source.index("restore_stock_config() {"):source.index("report_stock_boot() {")]
        function = function.replace("/opt/config/mod_data", str(self.data))
        for failure in ("mkdir", "truncate"):
            with self.subTest(failure=failure):
                config = self.data / "plugins.cfg"
                if failure == "truncate":
                    config.mkdir()
                    stub = ""
                else:
                    stub = 'mkdir() { return 1; }; '
                output = self.shell(function + '\n' + stub +
                                    'mount() { :; }; restored=0; disposed=0; '
                                    'run_stock_restore() { restored=1; }; '
                                    'dispose_chroot() { disposed=1; }; umount() { :; }; '
                                    'restore_stock_config; test "$restored" -eq 1; test "$disposed" -eq 1')
                self.assertIn("@@ Cannot clear user plugin config", output)
                if failure == "truncate":
                    config.rmdir()

    def _config_repair_fixture(self):
        config_root = self.root / "opt/config"
        scripts = self.root / "scripts"
        for name, value in (("zdisplay.sh", "STOCK"), ("zconf.sh", "0")):
            script = self.write(scripts / "commands" / name, "#!/bin/sh\necho " + value + "\n")
            script.chmod(0o755)
        self.env.update(SCRIPTS=str(scripts), CMDS=str(scripts / "commands"))
        source = (ROOT / ".shell/init-main.sh").read_text()
        function = source[source.index("extract_move_section() {"):source.index("rotate_logs() {")]
        function = function.replace("/tmp/", str(self.runtime) + "/")
        function = function.replace("/opt/config/mod_data", str(self.data)).replace("/opt/config", str(config_root))
        # Keep the existing GNU sed batch finalizer portable on host macOS.
        sed = ('sed() { ' + json.dumps(sys.executable) + ' -c '
               + "'from pathlib import Path; import sys; p=Path(sys.argv[1]); s=p.read_text(); p.write_text(s[:-2]+chr(10) if s.endswith(chr(44)+chr(10)) else s)'"
               + ' "$3"; }; ')
        return config_root, function, sed

    def test_plugin_prepare_failures_skip_only_plugin_job_and_keep_core_batch(self):
        config_root, function, sed = self._config_repair_fixture()
        tool = self.write(self.root / "capture_batch.py", '\n'.join([
            'import json, pathlib, sys',
            'jobs = json.loads(pathlib.Path(sys.argv[1]).read_text())',
            'assert not any(job["config"].endswith("/user.cfg") for job in jobs)',
            'assert any(job["params"].endswith("/init.display.stock.cfg") for job in jobs)',
            'assert any(job["params"].endswith("/init.cfg") for job in jobs)',
            'assert any(job["params"].endswith("/init.base.cfg") for job in jobs)',
            'assert any(job["params"].endswith("/tuning.off.cfg") for job in jobs)',
            'pathlib.Path(sys.argv[2]).write_text("core batch ran")',
        ]))
        marker = self.runtime / "batch.called"
        for failure in ("mkdir", "truncate", "touch"):
            with self.subTest(failure=failure):
                marker.unlink(missing_ok=True)
                cfg = self.data / "plugins.cfg"
                cfg.unlink(missing_ok=True)
                user = self.data / "user.cfg"
                user.unlink(missing_ok=True)
                stub = ''
                if failure == "mkdir":
                    stub = 'mkdir() { return 1; }; '
                elif failure == "truncate":
                    cfg.mkdir()
                else:
                    stub = 'touch() { return 1; }; '
                output = self.shell(function + '\n' + sed + stub +
                                    'chroot() { if [ "$4" = --batch ]; then '
                                    + json.dumps(sys.executable) + ' ' + json.dumps(str(tool))
                                    + ' "$5" ' + json.dumps(str(marker))
                                    + '; else return 1; fi; }; fix_config')
                self.assertIn("@@ Cannot prepare user plugin config", output)
                self.assertEqual(marker.read_text(), "core batch ran")
                self.assertFalse((self.runtime / "cfg_backup_batch.json").exists())
                self.assertFalse(user.exists())
                if failure == "truncate":
                    cfg.rmdir()

    def test_failed_existing_batch_retains_legacy_reload_behavior_and_removes_temporary_files(self):
        config_root, function, sed = self._config_repair_fixture()
        source = (ROOT / ".shell/init-main.sh").read_text()
        reload = source[source.index("init_main() {"):source.index('if [ "${BASH_SOURCE[0]}"')]
        marker = self.runtime / "batch.called"
        output = self.shell('set +e; ' + function + '\n' + reload + '\n' + sed +
                            'apply_klipper_patches() { :; }; logged() { cat; }; '
                            'chroot() { if [ "$4" = --batch ]; then touch '
                            + json.dumps(str(marker)) + '; return 17; else return 1; fi; }; '
                            'init_main reload')
        self.assertTrue(marker.exists())
        self.assertIn("// Configuration updated", output)
        self.assertFalse((self.runtime / "cfg_backup_batch.json").exists())
        self.assertFalse((self.runtime / "printer.tmp.cfg").exists())















    def _check_complete_uninstall(self, soft, shell_command_backup=True):
        # Execute the real uninstall script with every filesystem path remapped
        # into a temporary printer tree and hardware/chroot services stubbed.
        # Verify stock restoration and the final removal of nonstock modules,
        # even when the generic overlay rollback restored them from .bak first.
        fs = self.root / "printer"
        mod = fs / "opt/config/mod"
        data = fs / "opt/config/mod_data"
        target = fs / "opt/klipper/klippy"
        source = mod / ".py/klipper"
        for destination in (data.parent, target.parent, source.parent):
            destination.mkdir(parents=True, exist_ok=True)
        shutil.move(self.target, target)
        shutil.move(self.src, source)
        shutil.move(self.data, data)
        self.target, self.src, self.data, self.packages = target, source, data, data / "plugins"
        self.env.update(KLIPPER_OVERLAY_SRC=str(source), KLIPPER_OVERLAY_TARGET=str(target),
                        KLIPPER_USER_DIR=str(self.packages),
                        KLIPPER_USER_CONFIG=str(data / "plugins.cfg"))
        self.write(target / "extras/gcode_shell_command.py", "original shell command")
        self.write(source / "patches/extras/gcode_shell_command.py", "forge shell command")
        package = self.package("mine", {"custom.py": "module"},
                               {"gcode.py": "user core", "extras/heater.py": "user heater",
                                "extras/gcode_shell_command.py": "user shell command"})
        user = self.write(data / "user.cfg", "[stepper_x]\nrotation_distance: 39.9\n[include plugins.cfg]\n")
        self.shell("apply_klipper_patches 1")
        if not shell_command_backup:
            # Older installs could leave a regular nonstock copy of this extra.
            (target / "extras/gcode_shell_command.py").unlink()
            (target / "extras/gcode_shell_command.py.bak").unlink()
            self.write(target / "extras/gcode_shell_command.py", "legacy mod shell command")
        shutil.rmtree(source)
        shutil.rmtree(package)
        for path in ("etc/init.d", "dev", "data/logFiles"):
            (fs / path).mkdir(parents=True, exist_ok=True)
        fake_bin = fs / "fake_bin"
        umount = self.write(fake_bin / "umount", "#!/bin/sh\nexit 0\n")
        umount.chmod(0o755)
        self.write(mod / ".shell/S60dropbear", "#!/bin/sh\n")
        restore_params = mod / ".cfg/restore.plugins.cfg"
        self.write(restore_params, (ROOT / ".cfg/restore.plugins.cfg").read_text())
        common = '\n'.join([
            'SCRIPTS=' + json.dumps(str(ROOT / ".shell")),
            'CMDS=' + json.dumps(str(mod / ".shell/commands")),
            'PY=' + json.dumps(str(ROOT / ".py")),
            'MOD_DATA=' + json.dumps(str(data)),
            'MOD=' + json.dumps(str(fs / "data/.mod/.forge-x")),
            'export PATH=' + json.dumps(str(fake_bin)) + ':"$PATH"',
            'logged() { cat; }', 'sync() { :; }', 'sleep() { :; }',
            'xzcat() { :; }', 'reboot() { :; }', 'TEST_MOUNTED=1',
            'umount() { TEST_MOUNTED=0; }', 'lsof() { return 1; }',
            'mount() { if [ "$TEST_MOUNTED" = "1" ]; then echo "$MOD/sys"; fi; }',
            'chroot() { case "$*" in *"--config $MOD_DATA/user.cfg"*) '
            + json.dumps(sys.executable) + ' ' + json.dumps(str(ROOT / ".py/cfg_backup.py"))
            + ' "${@:4}";; *) return 0;; esac; }',
        ])
        self.write(mod / ".shell/common.sh", common)
        script = (ROOT / ".shell/uninstall.sh").read_text()
        script = re.sub(r"/(opt|etc|root|usr|bin|data|tmp)(?=/|\b)",
                        lambda match: str(fs) + match.group(0), script)
        script = script.replace("/dev/fb0", str(fs / "dev/fb0"))
        uninstall = self.write(self.root / "uninstall.sh", script)
        result = subprocess.run(["bash", str(uninstall)] + (["--soft"] if soft else []),
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("@@", result.stdout)
        shell_command = target / "extras/gcode_shell_command.py"
        if soft and shell_command_backup:
            self.assertEqual(shell_command.read_text(), "original shell command")
        else:
            self.assertFalse(shell_command.exists())
            self.assertFalse(shell_command.is_symlink())
        self.assertEqual((target / "gcode.py").read_text(), "stock core")
        self.assertEqual((target / "extras/virtual_sdcard.py").read_text(), "stock sd")
        self.assertEqual((target / "extras/heater.py").read_text(), "stock heater")
        self.assertFalse(list(target.rglob("*.bak")))
        self.assertFalse((target / "extras/custom.py").is_symlink())
        self.assertFalse((data / "plugins.cfg").exists())
        if soft:
            self.assertIn("rotation_distance: 39.9", user.read_text())
            self.assertNotIn("[include plugins.cfg]", user.read_text())
        else:
            self.assertFalse(data.exists())

    def test_complete_soft_uninstall_restores_stock_and_removes_generated_include(self):
        self._check_complete_uninstall(soft=True)

    def test_complete_hard_uninstall_removes_nonstock_shell_command_after_backup_restore(self):
        self._check_complete_uninstall(soft=False)

    def test_complete_hard_uninstall_removes_nonstock_shell_command_without_backup(self):
        self._check_complete_uninstall(soft=False, shell_command_backup=False)


    def test_user_cfg_include_added_once_with_existing_batch_tool(self):
        user = self.write(self.data / "user.cfg", "# Keep tuning\n[stepper_x]\nrotation_distance: 39.9\n")
        batch = self.write(self.root / "batch.json", json.dumps([{
            "mode": "restore", "config": str(user), "no_data": True,
            "params": str(ROOT / ".cfg/init.plugins.cfg"), "avoid_writes": True}]))
        for _ in range(2):
            result = subprocess.run([sys.executable, str(ROOT / ".py/cfg_backup.py"),
                                     "--batch", str(batch)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(user.read_text().count("[include plugins.cfg]"), 1)
        self.assertIn("rotation_distance: 39.9", user.read_text())
        self.assertIn("# Keep tuning", user.read_text())



















if __name__ == "__main__":
    unittest.main()
