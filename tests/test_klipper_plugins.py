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
        self.temp = tempfile.TemporaryDirectory(prefix="fx-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.src = self.root / "overlay"
        self.target = self.root / "klippy"
        self.data = self.root / "mod_data"
        self.packages = self.data / "plugins"
        self.runtime = self.root / "runtime"
        self.env = dict(os.environ,
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
        self.shell('klipper_overlay_publish_config() { '
                   'echo publish >> "$KLIPPER_USER_CONFIG.calls"; printf %s "$1" > "$KLIPPER_USER_CONFIG"; }; '
                   'apply_klipper_patches')
        self.assertEqual((self.data / "plugins.cfg.calls").read_text(), "publish\n")
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

    def test_missing_plugins_root_is_created_and_keeps_user_tuning(self):
        shutil.rmtree(self.packages)
        user = self.write(self.data / "user.cfg", "[stepper_x]\nrotation_distance: 39.9\n[include plugins.cfg]\n")
        self.shell("apply_klipper_patches")
        self.assertTrue(self.packages.is_dir())
        self.assertEqual(self.aggregate(), "")
        parser = configfile.PrinterConfig.__new__(configfile.PrinterConfig)
        parser.printer = None
        self.assertEqual(parser.read_config(str(user)).fileconfig.get("stepper_x", "rotation_distance"), "39.9")
        (self.data / "plugins.cfg").unlink()
        self.shell("apply_klipper_patches")
        self.assertTrue(self.packages.is_dir())
        self.assertEqual(self.aggregate(), "")

    def test_unavailable_plugins_directory_does_not_block_builtin_overlay(self):
        self.packages.rmdir()
        self.packages.write_text("blocked")
        output = self.shell("apply_klipper_patches")
        self.assertIn("@@ Cannot create user plugins directory", output)
        self.assertEqual(self.packages.read_text(), "blocked")
        self.assertEqual((self.target / "extras/mod_params.py").read_text(), "forge params")
        self.assertEqual((self.target / "extras/virtual_sdcard.py").read_text(), "forge sd")
        self.assertEqual((self.target / "gcode.py").read_text(), "forge core")
        self.assertEqual((self.target / "gcode.py.bak").read_text(), "stock core")
        self.assertEqual(self.aggregate(), "")
        self.packages.unlink()
        self.shell("apply_klipper_patches")
        self.assertTrue(self.packages.is_dir())

    def test_removed_component_directories_restore_only_their_files(self):
        package = self.package("mine", {"custom.py": "extra"}, {"gcode.py": "user core"}, "[custom]\n")
        self.shell("apply_klipper_patches 1")
        shutil.rmtree(package / "patches")
        self.shell("apply_klipper_patches 1")
        self.assertEqual((self.target / "gcode.py").read_text(), "forge core")
        self.assertTrue((self.target / "extras/custom.py").is_symlink())
        self.assertIn(str(package), self.aggregate())
        shutil.rmtree(package / "plugins")
        self.shell("apply_klipper_patches 1")
        self.assertFalse((self.target / "extras/custom.py").is_symlink())
        self.assertEqual((self.target / "gcode.py.bak").read_text(), "stock core")
        self.assertIn(str(package), self.aggregate())

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

    def test_foreign_backups_never_become_modules_or_replace_regular_files(self):
        leftover = self.write(self.target / "extras/leftover.py.bak", "foreign backup")
        current = self.write(self.target / "extras/foo.py", "current stock")
        backup = self.write(self.target / "extras/foo.py.bak", "older stock")
        self.shell('apply_klipper_patches; klipper_overlay_clean_links "$KLIPPER_OVERLAY_SRC" "$KLIPPER_OVERLAY_TARGET" remove')
        self.assertFalse((self.target / "extras/leftover.py").exists())
        self.assertEqual(leftover.read_text(), "foreign backup")
        self.assertEqual(current.read_text(), "current stock")
        self.assertEqual(backup.read_text(), "older stock")

    def test_cleanup_preserves_backups_when_ownership_links_are_gone(self):
        self.shell("apply_klipper_patches")
        (self.target / "gcode.py").unlink()
        (self.target / "extras/virtual_sdcard.py").unlink()
        current = self.write(self.target / "extras/virtual_sdcard.py", "replacement regular file")
        shutil.rmtree(self.src)
        self.shell('klipper_overlay_clean_links "$KLIPPER_OVERLAY_SRC" "$KLIPPER_OVERLAY_TARGET"; '
                   'klipper_overlay_clean_links "$KLIPPER_OVERLAY_SRC" "$KLIPPER_OVERLAY_TARGET" remove')
        self.assertFalse((self.target / "gcode.py").exists())
        self.assertEqual((self.target / "gcode.py.bak").read_text(), "stock core")
        self.assertEqual(current.read_text(), "replacement regular file")
        self.assertEqual((self.target / "extras/virtual_sdcard.py.bak").read_text(), "stock sd")

    def test_remove_restores_missing_targets_from_surviving_patch_paths_only(self):
        self.write(self.src / "patches/chelper/c_helper.so", "forge binary")
        self.write(self.target / "chelper/c_helper.so", "stock binary")
        self.shell("apply_klipper_patches")
        missing = [self.target / "gcode.py", self.target / "chelper/c_helper.so"]
        for target in missing:
            target.unlink()
        regular = self.target / "extras/virtual_sdcard.py"
        regular.unlink()
        regular.write_text("replacement regular file")
        self.shell('klipper_overlay_clean_links "$KLIPPER_OVERLAY_SRC" "$KLIPPER_OVERLAY_TARGET"')
        for target in missing:
            self.assertFalse(target.exists())
            self.assertTrue(Path(str(target) + ".bak").is_file())
        self.shell('klipper_overlay_clean_links "$KLIPPER_OVERLAY_SRC" "$KLIPPER_OVERLAY_TARGET" remove')
        self.assertEqual(missing[0].read_text(), "stock core")
        self.assertEqual(missing[1].read_text(), "stock binary")
        for target in missing:
            self.assertFalse(Path(str(target) + ".bak").exists())
        self.assertEqual(regular.read_text(), "replacement regular file")
        self.assertEqual(Path(str(regular) + ".bak").read_text(), "stock sd")

    def test_missing_patch_target_is_rejected_even_for_shell_command_extra(self):
        self.write(self.src / "patches/extras/gcode_shell_command.py", "forge shell command")
        output = self.shell('status=0; apply_klipper_patches || status=$?; test "$status" -eq 1')
        self.assertIn("@@ Missing klipper patch target and backup", output)
        self.assertFalse((self.target / "extras/gcode_shell_command.py").is_symlink())

    def test_disabling_overrides_returns_links_accepted_by_builtin_patch_linker(self):
        self.package("extras_only", {"custom.py": "module"})
        self.package("mine", patches={"gcode.py": "user core"})
        # The old overlay reaches the same built-in linkers without user-link
        # cleanup. Check that boundary without Git history in shallow CI clones.
        def link_builtin_only():
            return subprocess.run(
                ["bash", "-c", 'source "$1"; '
                 'klipper_overlay_link_plugins "$KLIPPER_OVERLAY_SRC" "$KLIPPER_OVERLAY_TARGET" && '
                 'klipper_overlay_link_patches "$KLIPPER_OVERLAY_SRC" "$KLIPPER_OVERLAY_TARGET"',
                 "builtin", str(LIBRARY)], env=self.env, capture_output=True, text=True)
        self.shell("apply_klipper_patches 1")
        rejected = link_builtin_only()
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("@@ Refusing to overwrite unmanaged klipper symlink", rejected.stdout)
        self.shell("apply_klipper_patches 0")
        accepted = link_builtin_only()
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        self.assertEqual((self.target / "gcode.py").read_text(), "forge core")
        self.assertEqual((self.target / "extras/custom.py").read_text(), "module")
        self.assertEqual((self.target / "gcode.py.bak").read_text(), "stock core")

    def test_optional_overlay_failure_keeps_builtin_files_and_cleans_temporary_batch(self):
        self.package("mine", {"custom.py": "module"}, config="[custom]\n")
        self.shell("apply_klipper_patches")
        self.assertIn("[include ", self.aggregate())
        output = self.shell('klipper_overlay_publish_config() { return 1; }; apply_klipper_patches')
        self.assertEqual(self.aggregate(), "")
        self.assertIn("@@ Failed to apply user plugin files/config", output)
        self.assertEqual((self.target / "gcode.py").read_text(), "forge core")
        self.assertEqual((self.target / "gcode.py.bak").read_text(), "stock core")
        self.assertFalse(list(self.data.glob(".plugins.cfg.*")))

    def test_link_failure_clears_stale_package_config_and_keeps_user_tuning(self):
        self.package("mine", {"custom.py": "module"}, config="[custom]\n")
        user = self.write(self.data / "user.cfg", "[stepper_x]\nrotation_distance: 39.9\n[include plugins.cfg]\n")
        self.shell("apply_klipper_patches")
        parser = configfile.PrinterConfig.__new__(configfile.PrinterConfig)
        parser.printer = None
        self.assertTrue(parser.read_config(str(user)).has_section("custom"))
        self.write(self.packages / "mine/plugins/second.py", "module")
        output = self.shell('ln() { case "${@: -1}" in "$KLIPPER_OVERLAY_TARGET/extras/second.py"*) '
                            'return 1;; esac; command ln "$@"; }; apply_klipper_patches')
        self.assertIn("@@ Failed to apply user plugin files/config", output)
        self.assertEqual(self.aggregate(), "")
        self.assertFalse(os.path.lexists(self.target / "extras/second.py"))
        self.assertFalse(list((self.target / "extras").glob("*.fx-new")))
        self.assertEqual((self.target / "gcode.py").read_text(), "forge core")
        self.assertFalse(list(self.data.glob(".plugins.cfg.*")))
        parsed = parser.read_config(str(user)).fileconfig
        self.assertEqual(parsed.sections(), ["stepper_x"])
        self.assertEqual(parsed.get("stepper_x", "rotation_distance"), "39.9")

    def test_stock_patch_link_failure_restores_module_and_clears_config(self):
        self.write(self.target / "klippy.py", "stock core")
        self.package("stock", patches={"klippy.py": "user core"}, config="[user_section]\n")
        failures = {
            "backup": 'ln() { [ "${@: -1}" != "$KLIPPER_OVERLAY_TARGET/klippy.py.bak" ] || return 1; command ln "$@"; }; ',
            "link": 'ln() { [ "${@: -1}" != "$KLIPPER_OVERLAY_TARGET/klippy.py.fx-new" ] || return 1; command ln "$@"; }; ',
            "rename": 'mv() { [ "${@: -1}" != "$KLIPPER_OVERLAY_TARGET/klippy.py" ] || return 1; command mv "$@"; }; ',
        }
        for step, stub in failures.items():
            with self.subTest(step=step):
                output = self.shell(stub + 'apply_klipper_patches')
                self.assertIn("@@ Failed to apply user plugin files/config", output)
                target = self.target / "klippy.py"
                self.assertFalse(target.is_symlink())
                self.assertEqual(target.read_text(), "stock core")
                self.assertFalse(os.path.lexists(str(target) + ".bak"))
                self.assertFalse(os.path.lexists(str(target) + ".fx-new"))
                self.assertEqual(self.aggregate(), "")
        self.shell("apply_klipper_patches")
        self.assertEqual((self.target / "klippy.py").read_text(), "user core")
        self.assertEqual((self.target / "klippy.py.bak").read_text(), "stock core")

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
        function = source[source.index("fix_config() {"):source.index("rotate_logs() {")]
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

    def test_priority_override_never_changes_stock_backup(self):
        package = self.package("override", {"custom.py": "extra"},
                               {"extras/virtual_sdcard.py": "user sd", "gcode.py": "user core"})
        self.shell("apply_klipper_patches 0")
        self.assertNotIn(str(package), self.aggregate())
        self.assertFalse((self.target / "extras/custom.py").exists())
        self.assertEqual((self.target / "extras/virtual_sdcard.py").read_text(), "forge sd")
        self.assertEqual((self.target / "gcode.py").read_text(), "forge core")
        backups = [self.target / "extras/virtual_sdcard.py.bak", self.target / "gcode.py.bak"]
        original_stats = [(file.stat().st_ino, file.stat().st_mtime_ns) for file in backups]
        for _ in range(3):
            self.shell("apply_klipper_patches 1")
            self.assertEqual([(file.stat().st_ino, file.stat().st_mtime_ns) for file in backups], original_stats)
            self.assertEqual((self.target / "extras/virtual_sdcard.py").read_text(), "user sd")
            self.assertEqual((self.target / "gcode.py").read_text(), "user core")
            self.assertEqual((self.target / "extras/virtual_sdcard.py.bak").read_text(), "stock sd")
            self.assertEqual((self.target / "gcode.py.bak").read_text(), "stock core")
            self.shell('klipper_overlay_clean_links "$KLIPPER_OVERLAY_SRC" "$KLIPPER_OVERLAY_TARGET"')
            self.assertEqual((self.target / "extras/virtual_sdcard.py").read_text(), "forge sd")
            self.assertEqual((self.target / "gcode.py").read_text(), "forge core")
        self.shell("apply_klipper_patches 1; klipper_overlay_clean_links \"$KLIPPER_OVERLAY_SRC\" \"$KLIPPER_OVERLAY_TARGET\" remove")
        self.assertEqual((self.target / "extras/virtual_sdcard.py").read_text(), "stock sd")
        self.assertEqual((self.target / "gcode.py").read_text(), "stock core")
        self.assertFalse((self.target / "extras/mod_params.py").exists())
        self.assertEqual((package / "patches/gcode.py").read_text(), "user core")

    def test_plugin_collisions_skip_whole_packages_and_keep_other_packages(self):
        for name in ("heater", "mod_params", "gcode", "__init__"):
            self.package(name, {name + ".py": "bad", "other_" + name + ".py": "good"},
                         patches={"extras/heater.py": "must not patch"})
        good = self.package("good", {"accepted.py": "accepted"})
        output = self.shell("apply_klipper_patches 1")
        self.assertEqual(self.aggregate(), "[include %s/config.cfg]\n" % good)
        self.assertEqual(output.count("@@ Skipping user package:"), 4)
        self.assertEqual((self.target / "extras/heater.py").read_text(), "stock heater")
        self.assertEqual((self.target / "extras/mod_params.py").read_text(), "forge params")
        self.assertFalse(list((self.target / "extras").glob("other_*.py")))
        self.assertEqual((self.target / "extras/accepted.py").read_text(), "accepted")

    def test_duplicate_modules_skip_the_entire_later_package(self):
        first = self.package("a", {"same.py": "first"})
        second = self.package("b", {"same.py": "second", "unique.py": "second"},
                              patches={"extras/heater.py": "must not patch"})
        missing = self.package("missing", {"unconfigured.py": "module"})
        (missing / "config.cfg").unlink()
        self.shell("apply_klipper_patches")
        self.assertIn(str(first), self.aggregate())
        self.assertNotIn(str(second), self.aggregate())
        self.assertNotIn("[include " + str(missing), self.aggregate())
        self.assertEqual((self.target / "extras/same.py").read_text(), "first")
        self.assertFalse((self.target / "extras/unique.py").exists())
        self.assertEqual((self.target / "extras/heater.py").read_text(), "stock heater")
        self.assertEqual((self.target / "extras/unconfigured.py").read_text(), "module")

    def test_all_combinations_of_optional_config_plugins_and_patches(self):
        for rel, overwrite, original in (("extras/heater.py", 0, "stock heater"),
                                         ("extras/virtual_sdcard.py", 1, "forge sd")):
            for cfg, plugins, patches in itertools.product((False, True), repeat=3):
                with self.subTest(overwrite=overwrite, config=cfg, plugins=plugins, patches=patches):
                    package = self.package("case_%d_%d%d%d" % (overwrite, cfg, plugins, patches),
                                           {"optional.py": "user extra"} if plugins else None,
                                           {rel: "user patch"} if patches else None,
                                           config="[optional_section]\nvalue: 1\n")
                    if not cfg:
                        (package / "config.cfg").unlink()
                    output = self.shell("apply_klipper_patches %d" % overwrite)
                    self.assertNotIn("@@", output)
                    self.assertEqual((self.target / "extras/optional.py").is_symlink(), plugins)
                    self.assertEqual((self.target / rel).read_text(), "user patch" if patches else original)
                    self.assertEqual(self.aggregate().count("[include "), int(cfg))
                    backup = Path(str(self.target / rel) + ".bak")
                    self.assertEqual(backup.exists(), patches or bool(overwrite))
                    if backup.exists():
                        self.assertEqual(backup.read_text(), "stock sd" if overwrite else original)
                    self.write(package / "disabled", "")

    def test_override_option_allows_forge_patches_but_not_plugin_collisions(self):
        blocked = self.package("blocked", {"virtual_sdcard.py": "bad plugin", "unused.py": "extra"})
        package = self.package("mixed", {"valid.py": "extra"},
                               {"extras/virtual_sdcard.py": "user sd", "extras/heater.py": "user heater"})
        for overwrite in (0, 1, 0):
            output = self.shell("apply_klipper_patches %d" % overwrite)
            self.assertIn("@@ Conflicting user plugin", output)
            self.assertNotIn(str(blocked), self.aggregate())
            self.assertFalse((self.target / "extras/unused.py").exists())
            self.assertEqual((self.target / "extras/valid.py").exists(), bool(overwrite))
            self.assertEqual((self.target / "extras/virtual_sdcard.py").read_text(),
                             "user sd" if overwrite else "forge sd")
            self.assertEqual((self.target / "extras/heater.py").read_text(),
                             "user heater" if overwrite else "stock heater")
            self.assertEqual(str(package) in self.aggregate(), bool(overwrite))
            self.assertEqual((self.target / "extras/heater.py.bak").exists(), bool(overwrite))

    def test_invalid_config_skips_all_code_in_its_package(self):
        package = self.package("invalid", {"valid.py": "extra"},
                               {"extras/heater.py": "user heater"})
        (package / "config.cfg").unlink()
        (package / "config.cfg").symlink_to(self.write(self.root / "external.cfg", ""))
        output = self.shell("apply_klipper_patches 1")
        self.assertIn("@@ Invalid user config", output)
        self.assertEqual(self.aggregate(), "")
        self.assertFalse((self.target / "extras/valid.py").exists())
        self.assertEqual((self.target / "extras/heater.py").read_text(), "stock heater")

    def test_disabled_package_and_removed_package_cleanup(self):
        package = self.package("mine", {"custom.py": "module"})
        self.shell("apply_klipper_patches")
        self.write(package / "disabled", "")
        self.shell("apply_klipper_patches")
        self.assertFalse((self.target / "extras/custom.py").is_symlink())
        self.assertNotIn(str(package), self.aggregate())
        (package / "disabled").unlink()
        self.shell("apply_klipper_patches")
        (package / "plugins/custom.py").unlink()
        self.shell("apply_klipper_patches")
        self.assertFalse((self.target / "extras/custom.py").is_symlink())

    def test_foreign_links_and_files_survive_cleanup_and_uninstall(self):
        foreign = self.write(self.root / "foreign.py", "foreign")
        (self.target / "extras/foreign.py").symlink_to(foreign)
        self.package("foreign", {"foreign.py": "bad"})
        self.shell("apply_klipper_patches; klipper_overlay_clean_links \"$KLIPPER_OVERLAY_SRC\" \"$KLIPPER_OVERLAY_TARGET\" remove")
        self.assertEqual((self.target / "extras/foreign.py").read_text(), "foreign")
        self.assertTrue((self.target / "extras/foreign.py").is_symlink())
        self.assertEqual((self.target / "extras/heater.py").read_text(), "stock heater")

    def test_removed_forge_patch_becomes_stock_patch_without_override_option(self):
        package = self.package("mine", patches={"extras/virtual_sdcard.py": "user sd"})
        self.shell("apply_klipper_patches 1")
        (self.src / "patches/extras/virtual_sdcard.py").unlink()
        self.shell("apply_klipper_patches 0")
        self.assertEqual((self.target / "extras/virtual_sdcard.py").read_text(), "user sd")
        self.assertEqual((self.target / "extras/virtual_sdcard.py.bak").read_text(), "stock sd")
        (package / "patches/extras/virtual_sdcard.py").unlink()
        self.shell("apply_klipper_patches 0")
        self.assertEqual((self.target / "extras/virtual_sdcard.py").read_text(), "stock sd")
        self.assertFalse((self.target / "extras/virtual_sdcard.py.bak").exists())

    def test_deleted_user_files_are_reverted_and_remaining_files_stay(self):
        package = self.package("mine", {"removed.py": "old", "retained.py": "keep"},
                               {"extras/virtual_sdcard.py": "user sd"})
        self.shell("apply_klipper_patches 1")
        (package / "plugins/removed.py").unlink()
        (package / "patches/extras/virtual_sdcard.py").unlink()
        (package / "config.cfg").unlink()
        self.shell("apply_klipper_patches 1")
        self.assertFalse((self.target / "extras/removed.py").is_symlink())
        self.assertEqual((self.target / "extras/retained.py").read_text(), "keep")
        self.assertEqual((self.target / "extras/virtual_sdcard.py").read_text(), "forge sd")
        self.assertEqual((self.target / "extras/virtual_sdcard.py.bak").read_text(), "stock sd")
        self.assertEqual(self.aggregate().count("[include "), 0)

    def test_deleted_whole_package_is_reverted_on_overlay_restart(self):
        package = self.package("mine", {"removed.py": "old"}, {"gcode.py": "user core"})
        self.shell("apply_klipper_patches 1")
        shutil.rmtree(package)
        self.shell("apply_klipper_patches 1")
        self.assertFalse((self.target / "extras/removed.py").is_symlink())
        self.assertEqual((self.target / "gcode.py").read_text(), "forge core")
        self.assertEqual((self.target / "gcode.py.bak").read_text(), "stock core")

    def test_uninstall_restores_backups_after_all_overlay_sources_are_deleted(self):
        self.package("mine", {"custom.py": "module"}, {"gcode.py": "user core"})
        self.shell("apply_klipper_patches 1")
        shutil.rmtree(self.src)
        shutil.rmtree(self.packages)
        self.shell("klipper_overlay_clean_links \"$KLIPPER_OVERLAY_SRC\" \"$KLIPPER_OVERLAY_TARGET\" remove; klipper_overlay_clean_links \"$KLIPPER_OVERLAY_SRC\" \"$KLIPPER_OVERLAY_TARGET\" remove")
        self.assertEqual((self.target / "gcode.py").read_text(), "stock core")
        self.assertEqual((self.target / "extras/virtual_sdcard.py").read_text(), "stock sd")
        self.assertFalse(list(self.target.rglob("*.bak")))
        self.assertFalse(list((self.target / "extras").glob("custom.py")))
        self.assertFalse((self.target / "extras/mod_params.py").is_symlink())

    def test_backup_restore_never_moves_into_a_symlinked_directory(self):
        self.shell("apply_klipper_patches")
        target = self.target / "gcode.py"
        target.unlink()
        package = self.packages / "mine"
        foreign_dir = package / "patches/gcode.py"
        foreign_dir.mkdir(parents=True)
        target.symlink_to(foreign_dir, target_is_directory=True)
        self.shell("klipper_overlay_clean_links \"$KLIPPER_OVERLAY_SRC\" \"$KLIPPER_OVERLAY_TARGET\" remove")
        self.assertEqual(target.read_text(), "stock core")
        self.assertFalse(list(foreign_dir.iterdir()))

    def test_deleted_plugins_root_removes_all_user_links_and_config(self):
        self.package("mine", {"custom.py": "module"}, {"gcode.py": "user core"})
        self.shell("apply_klipper_patches 1")
        shutil.rmtree(self.packages)
        self.shell("apply_klipper_patches 1")
        self.assertFalse((self.target / "extras/custom.py").is_symlink())
        self.assertEqual((self.target / "gcode.py").read_text(), "forge core")
        self.assertEqual((self.target / "gcode.py.bak").read_text(), "stock core")
        self.assertNotIn("[include ", self.aggregate())

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

    def test_patch_priority_ignores_stale_bytecode_with_same_mtime_and_size(self):
        forge = self.write(self.src / "patches/gcode.py", "value = 'forge'\n")
        package = self.package("mine", patches={"gcode.py": "value = 'users'\n"})
        user = package / "patches/gcode.py"
        for file in (forge, user):
            os.utime(file, (1700000000, 1700000000))
        self.shell("apply_klipper_patches 0")
        target = self.target / "gcode.py"
        with mock.patch.object(sys, "dont_write_bytecode", False):
            self.assertEqual(load_module("priority", target).value, "forge")
            self.assertTrue(list((self.target / "__pycache__").glob("gcode.*.pyc")))
            self.shell("apply_klipper_patches 1")
            self.assertEqual(load_module("priority", target).value, "users")
            self.shell('klipper_overlay_clean_links "$KLIPPER_OVERLAY_SRC" "$KLIPPER_OVERLAY_TARGET"')
            self.assertEqual(load_module("priority", target).value, "forge")

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

    def test_user_overlay_preserves_recursive_builtin_packages_and_native_patches(self):
        self.write(self.src / "plugins/ui/__init__.py", "ui package")
        resource = self.write(self.src / "plugins/ui/themes/default.json", '{"theme":"default"}')
        self.write(self.src / "patches/chelper/__init__.py", "forge helper")
        self.write(self.src / "patches/chelper/c_helper.so", "forge binary")
        self.write(self.target / "chelper/__init__.py", "stock helper")
        self.write(self.target / "chelper/c_helper.so", "stock binary")
        self.package("mine", {"custom.py": "module"}, {"gcode.py": "user core"})
        self.package("blocked", {"ui.py": "conflicting"})
        self.assertIn("@@ Conflicting user plugin", self.shell("apply_klipper_patches 1"))
        theme = self.target / "extras/ui/themes/default.json"
        native = self.target / "chelper/c_helper.so"
        self.assertEqual(theme.resolve(), resource.resolve())
        self.assertFalse((self.target / "extras/ui").is_symlink())
        self.assertEqual(native.read_text(), "forge binary")
        mtimes = [os.lstat(path).st_mtime_ns for path in (theme, native)]
        self.shell("apply_klipper_patches 1")
        self.assertEqual(mtimes, [os.lstat(path).st_mtime_ns for path in (theme, native)])
        shutil.rmtree(self.src)
        shutil.rmtree(self.packages)
        self.shell("klipper_overlay_clean_links \"$KLIPPER_OVERLAY_SRC\" \"$KLIPPER_OVERLAY_TARGET\" remove")
        self.assertFalse(theme.is_symlink())
        self.assertEqual(native.read_text(), "stock binary")
        self.assertEqual((self.target / "chelper/__init__.py").read_text(), "stock helper")
        self.assertFalse(list(self.target.rglob("*.bak")))

    def test_stock_core_extras_and_package_initializers_can_be_patched_without_option(self):
        originals = {"klippy.py": "stock core", "extras/heater.py": "stock heater",
                     "kinematics/__init__.py": "stock package", "kinematics/cartesian.py": "stock kinematics"}
        for rel, content in originals.items():
            self.write(self.target / rel, content)
        package = self.package("stock", {"new_module.py": "extra"},
                               {rel: "user " + rel for rel in originals}, "[new_module]\n")
        for _ in range(3):
            output = self.shell("apply_klipper_patches")
            self.assertNotIn("@@", output)
            for rel, content in originals.items():
                self.assertEqual((self.target / rel).read_text(), "user " + rel)
                self.assertEqual(Path(str(self.target / rel) + ".bak").read_text(), content)
            self.assertEqual((self.target / "extras/new_module.py").read_text(), "extra")
            self.assertIn(str(package), self.aggregate())
        shutil.rmtree(package)
        self.shell("apply_klipper_patches")
        for rel, content in originals.items():
            self.assertEqual((self.target / rel).read_text(), content)
        for rel in originals:
            self.assertFalse(Path(str(self.target / rel) + ".bak").exists())
        self.assertFalse((self.target / "extras/new_module.py").exists())
        self.assertEqual(self.aggregate(), "")

    def test_deleted_stock_patch_file_restores_backup_on_next_initialization(self):
        package = self.package("stock", {"retained.py": "keep"},
                               {"extras/heater.py": "user heater"})
        self.shell("apply_klipper_patches")
        (package / "patches/extras/heater.py").unlink()
        self.shell("apply_klipper_patches")
        self.assertEqual((self.target / "extras/heater.py").read_text(), "stock heater")
        self.assertFalse((self.target / "extras/heater.py.bak").exists())
        self.assertEqual((self.target / "extras/retained.py").read_text(), "keep")
        self.assertIn(str(package), self.aggregate())

    def test_duplicate_stock_patch_skips_all_files_and_config_of_later_package(self):
        first = self.package("a", patches={"extras/heater.py": "first"})
        second = self.package("b", {"unwanted.py": "second"}, patches={"extras/heater.py": "second"})
        output = self.shell("apply_klipper_patches")
        self.assertIn("@@", output)
        self.assertEqual((self.target / "extras/heater.py").read_text(), "first")
        self.assertEqual((self.target / "extras/heater.py.bak").read_text(), "stock heater")
        self.assertIn(str(first), self.aggregate())
        self.assertNotIn(str(second), self.aggregate())
        self.assertFalse((self.target / "extras/unwanted.py").exists())

    def test_new_conflict_rolls_back_previously_accepted_whole_package(self):
        package = self.package("mine", {"custom.py": "extra"}, patches={"extras/heater.py": "user heater"})
        self.shell("apply_klipper_patches")
        self.write(package / "patches/gcode.py", "forbidden without override")
        output = self.shell("apply_klipper_patches")
        self.assertIn("@@ Skipping user package:", output)
        self.assertEqual((self.target / "extras/heater.py").read_text(), "stock heater")
        self.assertFalse((self.target / "extras/heater.py.bak").exists())
        self.assertFalse((self.target / "extras/custom.py").exists())
        self.assertEqual(self.aggregate(), "")

    def test_forge_plugins_cannot_be_patched_even_with_option(self):
        self.write(self.src / "plugins/ui/__init__.py", "forge package")
        self.write(self.src / "plugins/ui/render.py", "forge render")
        for overwrite, rel in itertools.product((0, 1), ("extras/mod_params.py", "extras/ui/render.py")):
            with self.subTest(overwrite=overwrite, rel=rel):
                package = self.package("blocked", {"unwanted.py": "extra"}, patches={rel: "user"})
                output = self.shell("apply_klipper_patches %d" % overwrite)
                self.assertIn("@@", output)
                self.assertFalse((self.target / "extras/unwanted.py").exists())
                self.assertNotIn(str(package), self.aggregate())
                self.assertTrue((self.target / rel).read_text().startswith("forge"))
                shutil.rmtree(package)

    def test_patch_of_missing_module_foreign_link_or_invalid_backup_skips_package(self):
        external = self.write(self.root / "external.py", "external")
        (self.target / "extras/foreign.py").symlink_to(external)
        (self.target / "extras/heater.py.bak").symlink_to(external)
        for rel in ("missing.py", "extras/foreign.py", "extras/heater.py"):
            with self.subTest(rel=rel):
                package = self.package("blocked", {"unwanted.py": "extra"}, patches={rel: "user"})
                self.assertIn("@@", self.shell("apply_klipper_patches 1"))
                self.assertFalse((self.target / "extras/unwanted.py").exists())
                self.assertNotIn(str(package), self.aggregate())
                shutil.rmtree(package)
        self.assertEqual(external.read_text(), "external")
        self.assertEqual((self.target / "extras/heater.py").read_text(), "stock heater")

    def test_patch_cannot_write_through_foreign_directory_symlink(self):
        external = self.write(self.root / "external/module.py", "foreign")
        (self.target / "foreign").symlink_to(external.parent, target_is_directory=True)
        package = self.package("blocked", {"unwanted.py": "extra"}, patches={"foreign/module.py": "user"})
        self.assertIn("@@", self.shell("apply_klipper_patches"))
        self.assertEqual(external.read_text(), "foreign")
        self.assertFalse(Path(str(external) + ".bak").exists())
        self.assertFalse((self.target / "extras/unwanted.py").exists())
        self.assertNotIn(str(package), self.aggregate())

    def test_uninstall_stock_user_patch_restores_backup_after_sources_deleted(self):
        package = self.package("stock", patches={"extras/heater.py": "user heater"})
        self.shell("apply_klipper_patches")
        shutil.rmtree(package)
        self.shell('klipper_overlay_clean_links "$KLIPPER_OVERLAY_SRC" "$KLIPPER_OVERLAY_TARGET" remove')
        self.assertEqual((self.target / "extras/heater.py").read_text(), "stock heater")
        self.assertFalse((self.target / "extras/heater.py.bak").exists())

    def test_uninstall_restores_missing_stock_target_from_surviving_user_patch_path(self):
        self.package("stock", patches={"extras/heater.py": "user heater"})
        self.shell("apply_klipper_patches")
        (self.target / "extras/heater.py").unlink()
        self.shell('klipper_overlay_clean_links "$KLIPPER_OVERLAY_SRC" "$KLIPPER_OVERLAY_TARGET" remove')
        self.assertEqual((self.target / "extras/heater.py").read_text(), "stock heater")
        self.assertFalse((self.target / "extras/heater.py.bak").exists())

    def tree_identity(self):
        # Any write, rename, recreation or temporary file changes an inode,
        # mtime or ctime here, including the parent directory entries.
        identity = {}
        for base in (self.target, self.data):
            for path in [base, *base.rglob("*")]:
                info = os.lstat(path)
                identity[str(path)] = (info.st_ino, info.st_mtime_ns, info.st_ctime_ns)
        return identity

    def overlay_view(self):
        view = {"plugins.cfg": self.aggregate()}
        for path in sorted(self.target.rglob("*")):
            if "__pycache__" in path.parts:
                continue
            rel = str(path.relative_to(self.target))
            if path.is_symlink():
                view[rel] = ("link", os.readlink(path))
            elif path.is_dir():
                view[rel] = ("dir",)
            else:
                view[rel] = ("file", path.read_text())
        return view

    def install_packages(self, packages, delete=False):
        # Packages left out are disabled, or deleted as by a user with the printer off.
        for path in list(self.packages.iterdir()):
            if path.name in packages:
                continue
            if delete:
                shutil.rmtree(path)
            else:
                self.write(path / "disabled", "")
        for name in packages:
            (self.packages / name / "disabled").unlink(missing_ok=True)
            self.package(name, {"custom.py": name + " extra"},
                         {"extras/heater.py": name + " heater", "extras/virtual_sdcard.py": name + " sd",
                          "gcode.py": name + " core"}, "[custom]\n")

    def apply_interrupted(self, crash_at):
        # Power loss: SIGKILL right after the N-th completed filesystem change.
        stubs = ('__n=0; __crash() { __n=$((__n + 1)); [ "$__n" -lt "$CRASH_AT" ] || kill -KILL $$; }; '
                 'ln() { command ln "$@" || return; __crash; }; '
                 'mv() { command mv "$@" || return; __crash; }; '
                 'rm() { command rm "$@" || return; __crash; }; ')
        result = subprocess.run(
            ["bash", "-c", 'source "$1"; sync() { :; }; ' + stubs + "apply_klipper_patches 1",
             "test", str(LIBRARY)], env=dict(self.env, CRASH_AT=str(crash_at)),
            capture_output=True, text=True)
        return result.returncode == 0

    def test_unchanged_packages_cause_no_filesystem_writes(self):
        self.install_packages(["a"])
        self.package("b", config="[other]\n")
        self.package("c", {"own.py": "c extra"}, {"klippy.py": "c core"})
        self.write(self.target / "klippy.py", "stock klippy")
        self.shell("apply_klipper_patches 1")
        before = self.tree_identity()
        output = self.shell("apply_klipper_patches 1")
        self.assertEqual(self.tree_identity(), before)
        self.assertNotIn("//", output)
        self.assertNotIn("@@", output)

    def test_interrupted_changes_never_hide_a_module_and_converge(self):
        modules = ("gcode.py", "extras/heater.py", "extras/virtual_sdcard.py")
        # initial, interrupted and final package sets; whether packages left out
        # of the interrupted/final sets are deleted instead of disabled.
        transitions = {
            "install": ([], ["a"], ["a"], False, False),
            "install-then-deleted": ([], ["a"], [], False, True),
            "retarget": (["a"], ["b"], ["b"], False, False),
            "remove": (["a"], [], [], False, False),
            "deleted": (["a"], [], [], True, True),
        }
        for name, (initial, interrupted, final, delete_first, delete_last) in transitions.items():
            self.build_tree()
            self.install_packages(final)
            self.shell("apply_klipper_patches 1")
            expected = self.overlay_view()
            crash_at = 1
            while True:
                with self.subTest(transition=name, crash_at=crash_at):
                    self.build_tree()
                    self.install_packages(initial)
                    self.shell("apply_klipper_patches 1")
                    self.install_packages(interrupted, delete_first)
                    completed = self.apply_interrupted(crash_at)
                    for rel in modules:
                        # A deleted package leaves dangling links until the
                        # overlay runs; the module name itself never disappears.
                        path = self.target / rel
                        self.assertTrue(os.path.lexists(path) if delete_first else path.is_file(), rel)
                    self.install_packages(final, delete_last)
                    self.shell("apply_klipper_patches 1")
                    self.assertEqual(self.overlay_view(), expected)
                    self.assertFalse(list(self.target.rglob("*.fx-new")))
                    self.assertFalse(list(self.data.glob(".plugins.cfg.*")))
                if completed:
                    break
                crash_at += 1
            self.assertGreater(crash_at, 3, name)

    def test_package_order_decides_owner_regardless_of_previous_boot(self):
        self.package("b", patches={"extras/heater.py": "b heater"})
        self.shell("apply_klipper_patches")
        backup = self.target / "extras/heater.py.bak"
        original = os.lstat(backup).st_ino
        self.package("a", patches={"extras/heater.py": "a heater"})
        output = self.shell("apply_klipper_patches")
        self.assertIn("@@ Skipping user package: %s" % (self.packages / "b"), output)
        self.assertEqual((self.target / "extras/heater.py").read_text(), "a heater")
        self.assertEqual(os.lstat(backup).st_ino, original)
        self.assertEqual(backup.read_text(), "stock heater")

    def test_planned_owner_is_linked_even_when_files_share_an_inode(self):
        owner = self.package("b", patches={"extras/heater.py": "shared heater"})
        self.shell("apply_klipper_patches")
        winner = self.packages / "a/patches/extras/heater.py"
        winner.parent.mkdir(parents=True)
        os.link(owner / "patches/extras/heater.py", winner)
        self.shell("apply_klipper_patches")
        self.assertEqual(os.readlink(self.target / "extras/heater.py"), str(winner))

if __name__ == "__main__":
    unittest.main()
