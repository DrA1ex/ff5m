## Tests for Forge-X Klipper plugin and patch deployment.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import importlib.util
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]
OVERLAY = ROOT / ".shell" / "klipper_overlay.sh"
INIT = ROOT / ".shell" / "S00init"
CHANGES = ROOT / ".shell" / "commands" / "zchanges.sh"
MCU = ROOT / ".py" / "klipper" / "patches" / "mcu.py"
TOOLHEAD = ROOT / ".py" / "klipper" / "patches" / "toolhead.py"
CHELPER_INIT = (ROOT / ".py" / "klipper" / "patches" / "chelper" /
                "__init__.py")
VIRTUAL_SD = (ROOT / ".py" / "klipper" / "patches" / "extras" /
              "virtual_sdcard.py")
ORCA_FIXTURE = (ROOT / ".py" / "klipper" / "plugins" /
                "feather_ui_test" / "fixtures" /
                "context_recovery_open_box.gcode")


class OverlayTree:
    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.source = self.root / "mod" / ".py" / "klipper"
        self.target = self.root / "opt" / "klipper" / "klippy"
        self.plugins = self.source / "plugins"
        self.patches = self.source / "patches"
        self.extras = self.target / "extras"

        self.plugins.mkdir(parents=True)
        self.patches.mkdir(parents=True)
        self.extras.mkdir(parents=True)

    def run(self):
        script = self.root / "klipper_overlay.sh"
        source = OVERLAY.read_text(encoding="utf-8")
        replacements = {
            "/opt/config/mod/.py/klipper": str(self.source),
            "/opt/klipper/klippy": str(self.target),
        }
        for old, replacement in replacements.items():
            if source.count(old) != 1:
                raise AssertionError("expected one %r in klipper_overlay.sh" % old)
            source = source.replace(old, replacement, 1)
        script.write_text(source, encoding="utf-8")
        script.chmod(OVERLAY.stat().st_mode & 0o777)
        return subprocess.run(
            ["bash", "-c",
             'source "$1"; sync() { :; }; apply_klipper_patches',
             "overlay-test", str(script)],
            text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, check=False)

    def add_standard_overlay(self):
        (self.plugins / "top_level.py").write_text(
            "PLUGIN = True\n", encoding="utf-8")
        package = self.plugins / "ui"
        package.mkdir()
        (package / "__init__.py").write_text(
            "PACKAGE = True\n", encoding="utf-8")
        themes = package / "themes"
        themes.mkdir()
        (themes / "default.json").write_text(
            '{"name": "default"}\n', encoding="utf-8")

        plugin_cache = self.plugins / "__pycache__"
        plugin_cache.mkdir()
        (plugin_cache / "top_level.cpython-314.pyc").write_bytes(b"cache")
        (package / ".hidden.py").write_text(
            "HIDDEN = True\n", encoding="utf-8")

        (self.patches / "mcu.py").write_text(
            "PATCHED = True\n", encoding="utf-8")
        patch_cache = self.patches / "__pycache__"
        patch_cache.mkdir()
        (patch_cache / "mcu.cpython-314.pyc").write_bytes(b"cache")

    def add_adaptive_pa_patch(self):
        source_dir = self.patches / "chelper"
        source_dir.mkdir(parents=True)
        source_init = source_dir / "__init__.py"
        source_init.write_text(
            CHELPER_INIT.read_text(encoding="utf-8"), encoding="utf-8")
        source_helper = source_dir / "c_helper.so"
        source_helper.write_bytes(b"PATCHED ARM SHARED OBJECT")

        target_dir = self.target / "chelper"
        target_dir.mkdir(parents=True)
        target_init = target_dir / "__init__.py"
        target_init.write_text("STOCK = True\n", encoding="utf-8")
        target_helper = target_dir / "c_helper.so"
        target_helper.write_bytes(b"STOCK ARM SHARED OBJECT")
        return source_helper, target_init, target_helper


class KlipperOverlayTest(unittest.TestCase):
    def test_scripts_have_valid_bash_syntax(self):
        subprocess.run(
            ["bash", "-n", str(OVERLAY), str(INIT), str(CHANGES)],
            check=True)

    def test_files_are_linked_recursively_and_repeat_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            tree = OverlayTree(directory)
            tree.add_standard_overlay()
            (tree.target / "mcu.py").write_text(
                "STOCK = True\n", encoding="utf-8")

            first = tree.run()

            self.assertEqual(first.returncode, 0, first.stdout)
            top_level = tree.extras / "top_level.py"
            package = tree.extras / "ui"
            init = package / "__init__.py"
            theme = package / "themes" / "default.json"
            patched_mcu = tree.target / "mcu.py"

            self.assertTrue(top_level.is_symlink())
            self.assertTrue(package.is_dir())
            self.assertFalse(package.is_symlink())
            self.assertTrue(init.is_symlink())
            self.assertTrue(theme.is_symlink())
            self.assertTrue(patched_mcu.is_symlink())
            self.assertEqual(
                (tree.target / "mcu.py.bak").read_text(encoding="utf-8"),
                "STOCK = True\n")
            self.assertFalse((tree.extras / "__pycache__").exists())
            self.assertFalse((package / ".hidden.py").exists())
            self.assertFalse((tree.target / "__pycache__").exists())

            mtimes = {
                path: os.lstat(path).st_mtime_ns
                for path in (top_level, init, theme, patched_mcu)
            }

            second = tree.run()

            self.assertEqual(second.returncode, 0, second.stdout)
            self.assertEqual(
                mtimes,
                {path: os.lstat(path).st_mtime_ns for path in mtimes})

    def test_repository_overlay_maps_every_supported_source_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / "klippy"
            extras = target / "extras"
            extras.mkdir(parents=True)

            source = ROOT / ".py" / "klipper"
            patch_files = [
                path for path in (source / "patches").rglob("*")
                if (path.is_file()
                    and path.suffix in (".py", ".so")
                    and "__pycache__" not in path.parts)
            ]
            for path in patch_files:
                relative = path.relative_to(source / "patches")
                stock = target / relative
                stock.parent.mkdir(parents=True, exist_ok=True)
                stock.write_text("STOCK = True\n", encoding="utf-8")

            overlay = root / "klipper_overlay.sh"
            overlay_source = OVERLAY.read_text(encoding="utf-8")
            replacements = {
                "/opt/config/mod/.py/klipper": str(source),
                "/opt/klipper/klippy": str(target),
            }
            for old, replacement in replacements.items():
                self.assertEqual(overlay_source.count(old), 1)
                overlay_source = overlay_source.replace(old, replacement, 1)
            overlay.write_text(overlay_source, encoding="utf-8")
            overlay.chmod(OVERLAY.stat().st_mode & 0o777)
            result = subprocess.run(
                ["bash", "-c",
                 'source "$1"; sync() { :; }; apply_klipper_patches',
                 "repository-overlay-test", str(overlay)],
                text=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, check=False)

            self.assertEqual(result.returncode, 0, result.stdout)
            for path in patch_files:
                relative = path.relative_to(source / "patches")
                installed = target / relative
                self.assertTrue(installed.is_symlink(), relative)
                self.assertEqual(installed.resolve(), path.resolve())

            for path in (source / "plugins").rglob("*"):
                if not path.is_file():
                    continue
                relative = path.relative_to(source / "plugins")
                if ("__pycache__" in relative.parts
                        or path.name.startswith(".")
                        or path.suffix == ".pyc"
                        or any(part.startswith(".")
                               for part in relative.parts)):
                    continue
                installed = extras / relative
                self.assertTrue(installed.is_symlink(), relative)
                self.assertEqual(installed.resolve(), path.resolve())

    def test_adaptive_pa_wrapper_loads_managed_helper_from_active_tree(self):
        class FakeLib:
            def set_python_logging_callback(self, callback):
                self.callback = callback

        class FakeFFI:
            loaded_paths = []

            def cdef(self, definition):
                pass

            def dlopen(self, path):
                self.loaded_paths.append(path)
                return FakeLib()

            def callback(self, signature, callback):
                return callback

        with tempfile.TemporaryDirectory() as directory:
            tree = OverlayTree(directory)
            source_helper, target_init, target_helper = (
                tree.add_adaptive_pa_patch())

            installed = tree.run()

            self.assertEqual(installed.returncode, 0, installed.stdout)
            self.assertTrue(target_init.is_symlink())
            self.assertTrue(target_helper.is_symlink())
            self.assertEqual(target_helper.resolve(), source_helper.resolve())

            fake_cffi = types.ModuleType("cffi")
            fake_cffi.FFI = FakeFFI
            spec = importlib.util.spec_from_file_location(
                "ff5m_active_chelper_test", target_init)
            module = importlib.util.module_from_spec(spec)
            with mock.patch.dict(sys.modules, {"cffi": fake_cffi}):
                spec.loader.exec_module(module)
                module.get_ffi()

            self.assertEqual(FakeFFI.loaded_paths, [str(target_helper)])

    def test_shared_object_patch_has_the_same_backup_and_restore_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory:
            tree = OverlayTree(directory)
            source, _, target = tree.add_adaptive_pa_patch()

            installed = tree.run()

            self.assertEqual(installed.returncode, 0, installed.stdout)
            self.assertTrue(target.is_symlink())
            self.assertEqual(target.resolve(), source.resolve())
            self.assertEqual(
                (target.parent / "c_helper.so.bak").read_bytes(),
                b"STOCK ARM SHARED OBJECT")

            source.unlink()
            restored = tree.run()

            self.assertEqual(restored.returncode, 0, restored.stdout)
            self.assertFalse(target.is_symlink())
            self.assertEqual(target.read_bytes(), b"STOCK ARM SHARED OBJECT")
            self.assertFalse((target.parent / "c_helper.so.bak").exists())

    def test_current_dev_directory_links_and_mcu_file_are_repaired(self):
        with tempfile.TemporaryDirectory() as directory:
            tree = OverlayTree(directory)
            tree.add_standard_overlay()
            (tree.target / "mcu.py").write_text(
                "PATCHED_TIMEOUT = 0.05\n", encoding="utf-8")
            (tree.target / "mcu.py.bak").write_text(
                "STOCK_TIMEOUT = 0.05\n", encoding="utf-8")
            os.symlink(tree.plugins / "ui", tree.extras / "ui")

            result = tree.run()

            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertTrue((tree.target / "mcu.py").is_symlink())
            self.assertEqual(
                (tree.target / "mcu.py").resolve(),
                (tree.patches / "mcu.py").resolve())
            self.assertFalse((tree.extras / "ui").is_symlink())
            self.assertTrue((tree.extras / "ui" / "__init__.py").is_symlink())
            self.assertEqual(
                (tree.target / "mcu.py.bak").read_text(encoding="utf-8"),
                "STOCK_TIMEOUT = 0.05\n")

    def test_broken_links_are_restored_or_removed_and_live_external_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            tree = OverlayTree(directory)

            recover = tree.extras / "recover.py"
            os.symlink(tree.root / "missing-recover.py", recover)
            (tree.extras / "recover.py.old").write_text(
                "RECOVERED = True\n", encoding="utf-8")

            discard = tree.extras / "discard.py"
            os.symlink(tree.root / "missing-discard.py", discard)

            external = tree.root / "external.py"
            external.write_text("EXTERNAL = True\n", encoding="utf-8")
            live = tree.extras / "external.py"
            os.symlink(external, live)

            result = tree.run()

            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertFalse(recover.is_symlink())
            self.assertEqual(
                recover.read_text(encoding="utf-8"),
                "RECOVERED = True\n")
            self.assertFalse(discard.exists())
            self.assertTrue(live.is_symlink())
            self.assertEqual(live.resolve(), external.resolve())

    def test_legacy_tuned_toolhead_backup_restores_stock_value(self):
        with tempfile.TemporaryDirectory() as directory:
            tree = OverlayTree(directory)
            patched = tree.patches / "toolhead.py"
            patched.write_text("PATCHED = True\n", encoding="utf-8")
            stock = tree.target / "toolhead.py"
            stock.write_text(
                "LOOKAHEAD_FLUSH_TIME = 0.150\n", encoding="utf-8")

            install = tree.run()

            self.assertEqual(install.returncode, 0, install.stdout)
            self.assertTrue(stock.is_symlink())
            self.assertEqual(
                (tree.target / "toolhead.py.bak").read_text(
                    encoding="utf-8"),
                "LOOKAHEAD_FLUSH_TIME = 0.5\n")

            patched.unlink()
            restore = tree.run()

            self.assertEqual(restore.returncode, 0, restore.stdout)
            self.assertFalse(stock.is_symlink())
            self.assertEqual(
                stock.read_text(encoding="utf-8"),
                "LOOKAHEAD_FLUSH_TIME = 0.5\n")

    def test_main_style_cleanup_restores_mcu_and_removes_nested_links(self):
        with tempfile.TemporaryDirectory() as directory:
            tree = OverlayTree(directory)
            tree.add_standard_overlay()
            (tree.target / "mcu.py").write_text(
                "STOCK = True\n", encoding="utf-8")
            result = tree.run()
            self.assertEqual(result.returncode, 0, result.stdout)

            shutil.rmtree(tree.plugins / "ui")
            (tree.plugins / "top_level.py").unlink()
            (tree.patches / "mcu.py").unlink()

            rollback = subprocess.run(
                ["bash", "-c", r'''
                    src_dir="$1"
                    target_dir="$2"
                    find "$target_dir" -type l | while read -r file; do
                        rel_path=${file#"$target_dir/"}
                        file_name=${file##*/}
                        if [ -f "$file.bak" ]; then
                            if [ ! -f "$src_dir/patches/$rel_path" ]; then
                                mv "$file.bak" "$file"
                            fi
                        elif [ ! -f "$src_dir/plugins/$file_name" ]; then
                            rm -f "$file"
                        fi
                    done
                ''', "main-cleanup", str(tree.source), str(tree.target)],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                check=False)

            self.assertEqual(rollback.returncode, 0, rollback.stdout)
            self.assertFalse((tree.target / "mcu.py").is_symlink())
            self.assertEqual(
                (tree.target / "mcu.py").read_text(encoding="utf-8"),
                "STOCK = True\n")
            self.assertFalse((tree.extras / "top_level.py").exists())
            self.assertFalse((tree.extras / "ui" / "__init__.py").exists())
            self.assertFalse(
                (tree.extras / "ui" / "themes" / "default.json").exists())


class McuTuningTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dependencies = {
            name: types.ModuleType(name)
            for name in ("serialhdl", "msgproto", "pins", "chelper",
                         "clocksync")
        }
        dependencies["serialhdl"].error = RuntimeError
        spec = importlib.util.spec_from_file_location(
            "ff5m_mcu_patch_test", MCU)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, dependencies):
            spec.loader.exec_module(module)
        cls.load_timeout = staticmethod(module._load_trsync_timeout)

    def test_enabled_value_uses_relaxed_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            variables = pathlib.Path(directory) / "variables.cfg"
            variables.write_text(
                "[Variables]\ntune_klipper = 1\n", encoding="utf-8")

            self.assertEqual(self.load_timeout(str(variables)), 0.05)

    def test_disabled_missing_and_invalid_values_use_default(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            disabled = root / "disabled.cfg"
            disabled.write_text(
                "[Variables]\ntune_klipper = 0\n", encoding="utf-8")
            invalid = root / "invalid.cfg"
            invalid.write_text(
                "[Variables]\ntune_klipper = perhaps\n", encoding="utf-8")
            wrong_section = root / "wrong.cfg"
            wrong_section.write_text(
                "[Other]\ntune_klipper = 1\n", encoding="utf-8")

            self.assertEqual(self.load_timeout(str(disabled)), 0.025)
            self.assertEqual(self.load_timeout(str(invalid)), 0.025)
            self.assertEqual(self.load_timeout(str(wrong_section)), 0.025)
            self.assertEqual(
                self.load_timeout(str(root / "missing.cfg")), 0.025)


class ToolheadTuningTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dependencies = {
            name: types.ModuleType(name)
            for name in ("mcu", "chelper", "kinematics",
                         "kinematics.extruder")
        }
        dependencies["kinematics"].extruder = dependencies[
            "kinematics.extruder"]
        spec = importlib.util.spec_from_file_location(
            "ff5m_toolhead_patch_test", TOOLHEAD)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, dependencies):
            spec.loader.exec_module(module)
        cls.load_flush_time = staticmethod(
            module._load_lookahead_flush_time)
        cls.MoveQueue = module.MoveQueue
        cls.ToolHead = module.ToolHead

    def test_enabled_value_uses_tuned_lookahead(self):
        with tempfile.TemporaryDirectory() as directory:
            variables = pathlib.Path(directory) / "variables.cfg"
            variables.write_text(
                "[Variables]\ntune_klipper = 1\n", encoding="utf-8")

            self.assertEqual(self.load_flush_time(str(variables)), 0.150)

    def test_disabled_missing_and_invalid_values_use_stock_lookahead(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            disabled = root / "disabled.cfg"
            disabled.write_text(
                "[Variables]\ntune_klipper = 0\n", encoding="utf-8")
            invalid = root / "invalid.cfg"
            invalid.write_text(
                "[Variables]\ntune_klipper = perhaps\n", encoding="utf-8")
            wrong_section = root / "wrong.cfg"
            wrong_section.write_text(
                "[Other]\ntune_klipper = 1\n", encoding="utf-8")

            self.assertEqual(self.load_flush_time(str(disabled)), 0.5)
            self.assertEqual(self.load_flush_time(str(invalid)), 0.5)
            self.assertEqual(self.load_flush_time(str(wrong_section)), 0.5)
            self.assertEqual(
                self.load_flush_time(str(root / "missing.cfg")), 0.5)

    def test_move_queue_restores_startup_lookahead_after_reset_and_flush(self):
        queue = self.MoveQueue(mock.Mock(), 0.150)

        queue.set_flush_time(2.0)
        queue.reset()
        self.assertEqual(queue.junction_flush, 0.150)

        queue.set_flush_time(2.0)
        queue.flush()
        self.assertEqual(queue.junction_flush, 0.150)

    def test_stall_check_yields_when_buffer_is_not_full(self):
        reactor = mock.Mock()
        reactor.NOW = object()
        reactor.NEVER = object()
        reactor.monotonic.return_value = 10.0
        reactor.pause.side_effect = lambda waketime: waketime
        mcu = mock.Mock()
        mcu.estimated_print_time.return_value = 10.0
        toolhead = object.__new__(self.ToolHead)
        toolhead.reactor = reactor
        toolhead.mcu = mcu
        toolhead.special_queuing_state = ""
        toolhead.print_time = 10.0
        toolhead.buffer_time_high = 2.0
        toolhead.can_pause = True
        toolhead.need_check_stall = -1.0

        toolhead._check_stall()

        reactor.pause.assert_called_once_with(reactor.NOW)
        self.assertEqual(toolhead.need_check_stall, toolhead.print_time)

    def test_stall_pause_has_minimum_delay_without_duplicate_yield(self):
        class Reactor:
            NOW = object()
            NEVER = object()

            def __init__(self):
                self.pauses = []

            def monotonic(self):
                return 10.0

            def pause(self, waketime):
                self.pauses.append(waketime)
                return waketime

        reactor = Reactor()
        mcu = mock.Mock()
        mcu.estimated_print_time.side_effect = lambda eventtime: eventtime
        toolhead = object.__new__(self.ToolHead)
        toolhead.reactor = reactor
        toolhead.mcu = mcu
        toolhead.special_queuing_state = ""
        toolhead.print_time = 12.002
        toolhead.buffer_time_high = 2.0
        toolhead.can_pause = True
        toolhead.need_check_stall = -1.0

        toolhead._check_stall()

        self.assertEqual(reactor.pauses, [10.005])
        self.assertEqual(toolhead.need_check_stall, toolhead.print_time)


if __name__ == "__main__":
    unittest.main()
