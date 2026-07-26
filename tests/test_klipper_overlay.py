## Tests for Forge-X Klipper plugin and patch deployment.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import ast
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).parents[1]
OVERLAY = ROOT / ".shell" / "klipper_overlay.sh"
INIT = ROOT / ".shell" / "S00init"
TUNING = ROOT / ".shell" / "commands" / "ztune_klipper.sh"
MCU = ROOT / ".py" / "klipper" / "patches" / "mcu.py"


class OverlayTree:
    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.source = self.root / "mod" / ".py" / "klipper"
        self.target = self.root / "opt" / "klipper" / "klippy"
        self.plugins = self.source / "plugins"
        self.patches = self.source / "patches"
        self.extras = self.target / "extras"
        self.tune = self.root / "tune.sh"

        self.plugins.mkdir(parents=True)
        self.patches.mkdir(parents=True)
        self.extras.mkdir(parents=True)
        self.tune.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.tune.chmod(0o755)

    def run(self):
        env = dict(os.environ)
        env.update({
            "KLIPPER_SRC_DIR": str(self.source),
            "KLIPPER_TARGET_DIR": str(self.target),
            "KLIPPER_TUNE_CMD": str(self.tune),
        })
        return subprocess.run(
            ["bash", "-c",
             'source "$1"; sync() { :; }; apply_klipper_patches',
             "overlay-test", str(OVERLAY)],
            env=env, text=True, stdout=subprocess.PIPE,
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


class KlipperOverlayTest(unittest.TestCase):
    def test_scripts_have_valid_bash_syntax(self):
        subprocess.run(
            ["bash", "-n", str(OVERLAY), str(INIT), str(TUNING)],
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
            tune = root / "tune.sh"
            tune.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            tune.chmod(0o755)

            source = ROOT / ".py" / "klipper"
            patch_files = [
                path for path in (source / "patches").rglob("*.py")
                if "__pycache__" not in path.parts
            ]
            for path in patch_files:
                relative = path.relative_to(source / "patches")
                stock = target / relative
                stock.parent.mkdir(parents=True, exist_ok=True)
                stock.write_text("STOCK = True\n", encoding="utf-8")

            env = dict(os.environ)
            env.update({
                "KLIPPER_SRC_DIR": str(source),
                "KLIPPER_TARGET_DIR": str(target),
                "KLIPPER_TUNE_CMD": str(tune),
            })
            result = subprocess.run(
                ["bash", "-c",
                 'source "$1"; sync() { :; }; apply_klipper_patches',
                 "repository-overlay-test", str(OVERLAY)],
                env=env, text=True, stdout=subprocess.PIPE,
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
        source = MCU.read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_load_trsync_timeout")
        module = ast.Module(body=[function], type_ignores=[])
        namespace = {}
        exec(compile(module, str(MCU), "exec"), namespace)
        cls.load_timeout = staticmethod(namespace["_load_trsync_timeout"])

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


if __name__ == "__main__":
    unittest.main()
