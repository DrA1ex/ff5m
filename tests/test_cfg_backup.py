"""Tests for the Forge-X configuration backup and restore utility."""

import importlib.util
import json
import multiprocessing
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]
CFG_PATH = ROOT / ".py" / "cfg_backup.py"
SPEC = importlib.util.spec_from_file_location("test_cfg_backup_module", CFG_PATH)
CFG = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CFG
SPEC.loader.exec_module(CFG)


def _remove_section_config():
    return (
        CFG.ConfigurationBuilder()
        .start_section("[remove_me]")
        .add_wildcard(CFG.Action.REMOVE)
        .build()
    )


def _save_value_config():
    return (
        CFG.ConfigurationBuilder()
        .start_section("[alpha]")
        .add("value", CFG.Action.ADD)
        .build()
    )


def _parallel_restore_worker(config_path, barrier, queue):
    original_rename = CFG.os.rename

    def synchronized_rename(src, dst):
        barrier.wait(5.)
        original_rename(src, dst)

    CFG.os.rename = synchronized_rename
    try:
        CFG.restore(config_path, {}, _remove_section_config())
        queue.put(None)
    except BaseException as exc:
        queue.put(repr(exc))


def _parallel_backup_worker(config_path, backup_path, barrier, queue):
    original_rename = CFG.os.rename

    def synchronized_rename(src, dst):
        barrier.wait(5.)
        original_rename(src, dst)

    CFG.os.rename = synchronized_rename
    try:
        CFG.backup(config_path, backup_path, _save_value_config())
        queue.put(None)
    except BaseException as exc:
        queue.put(repr(exc))


class CfgBackupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        CFG.DRY_RUN = False
        CFG.VERBOSE = False

    def _write(self, name, content):
        path = self.root / name
        path.write_text(content)
        return path

    def _rules(self, content):
        return CFG.parse_cmd_configuration(str(self._write("rules.cfg", content)))

    def _run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(CFG_PATH), *map(str, args)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=10.,
        )

    def test_printer_tokenizer_reports_structural_tokens(self):
        config = self._write(
            "printer.cfg",
            "[include base.cfg]\n"
            "# comment\n"
            "[alpha]\n"
            "value: 1\n"
            "  G1 X10\n"
            "\n"
            "#!#!# <---------------------- DEFERRED_INCLUDES ---------------------->\n"
            "[include late.cfg]\n"
            "#*# <---------------------- SAVE_CONFIG ---------------------->\n"
            "#*# value = 2\n",
        )
        tokens = []

        CFG.iterate_printer_config_tokens(
            str(config),
            callback=lambda token, **kwargs: tokens.append(token),
        )

        self.assertIn(CFG.CfgToken.INCLUDE, tokens)
        self.assertIn(CFG.CfgToken.COMMENT, tokens)
        self.assertIn(CFG.CfgToken.SECTION, tokens)
        self.assertIn(CFG.CfgToken.PARAMETER, tokens)
        self.assertIn(CFG.CfgToken.OTHER, tokens)
        self.assertIn(CFG.CfgToken.BREAK, tokens)
        self.assertIn(CFG.CfgToken.DEFERRED_BLOCK_BEGIN, tokens)
        self.assertIn(CFG.CfgToken.EDITABLE_BLOCK_END, tokens)

    def test_parameter_rules_support_sections_parameters_and_includes(self):
        cfg = self._rules(
            "[alpha] keep\n"
            "-[alpha] drop\n"
            "[beta]\n"
            "-[gamma]\n"
            "[include base.cfg]\n"
            "-[include old.cfg]\n"
            "[include late.cfg] defer\n"
        )

        self.assertTrue(cfg.is_saving("[alpha]", param_name="keep"))
        self.assertTrue(cfg.is_removing("[alpha]", param_name="drop"))
        self.assertTrue(cfg.is_saving("[beta]"))
        self.assertTrue(cfg.is_removing("[gamma]"))
        self.assertEqual(cfg.include_action("base.cfg"), CFG.Action.ADD)
        self.assertEqual(cfg.include_action("old.cfg"), CFG.Action.REMOVE)
        self.assertEqual(
            dict(cfg.deferred_includes)["late.cfg"], CFG.Action.ADD)

    def test_duplicate_parameter_rule_is_rejected(self):
        rules = self._write(
            "rules.cfg",
            "[alpha] value\n"
            "[alpha] value\n",
        )

        with self.assertRaisesRegex(Exception, "contains errors"):
            CFG.parse_cmd_configuration(str(rules))

    def test_duplicate_include_rule_is_rejected(self):
        rules = self._write(
            "rules.cfg",
            "[include base.cfg]\n"
            "[include base.cfg]\n",
        )

        with self.assertRaises(ValueError):
            CFG.parse_cmd_configuration(str(rules))

    def test_invalid_rule_only_warns(self):
        rules = self._write(
            "rules.cfg",
            "this is not a rule\n"
            "[alpha] value\n",
        )

        with mock.patch("sys.stderr") as stderr:
            cfg = CFG.parse_cmd_configuration(str(rules))

        self.assertTrue(cfg.is_saving("[alpha]", param_name="value"))
        self.assertTrue(stderr.write.called)

    def test_load_backup_resets_section_on_adjacent_section(self):
        cfg = self._rules("[alpha] value\n")
        data = self._write(
            "backup.cfg",
            "[alpha]\n"
            "value: expected\n"
            "[beta]\n"
            "value: must-not-leak\n",
        )

        loaded = CFG.load_backup(str(data), cfg)

        self.assertEqual(loaded, {"[alpha]": {"value": "expected"}})

    def test_backup_extracts_only_selected_values(self):
        config = self._write(
            "printer.cfg",
            "[alpha]\n"
            "keep: 1\n"
            "skip: 2\n"
            "\n"
            "[beta]\n"
            "one: 3\n"
            "two: 4\n",
        )
        backup = self.root / "backup.cfg"
        cfg = self._rules(
            "[alpha] keep\n"
            "[beta]\n",
        )

        CFG.backup(str(config), str(backup), cfg)

        self.assertEqual(
            backup.read_text(),
            "[alpha]\n"
            "keep: 1\n"
            "[beta]\n"
            "one: 3\n"
            "two: 4\n",
        )

    def test_backup_raises_when_no_selected_values_exist(self):
        config = self._write("printer.cfg", "[alpha]\nvalue: 1\n")
        backup = self.root / "backup.cfg"
        cfg = self._rules("[missing] value\n")

        with self.assertRaisesRegex(Exception, "Unable to find"):
            CFG.backup(str(config), str(backup), cfg)

        self.assertFalse(backup.exists())

    def test_restore_updates_saved_value_and_preserves_other_values(self):
        config = self._write(
            "printer.cfg",
            "[alpha]\n"
            "value: old\n"
            "untouched: keep\n",
        )
        data = self._write(
            "backup.cfg",
            "[alpha]\n"
            "value: new\n",
        )
        cfg = self._rules("[alpha] value\n")

        CFG.restore(
            str(config),
            CFG.load_backup(str(data), cfg),
            cfg,
        )

        self.assertEqual(
            config.read_text().rstrip(),
            "[alpha]\n"
            "value: new\n"
            "untouched: keep",
        )

    def test_restore_adds_missing_saved_section(self):
        config = self._write("printer.cfg", "[existing]\nvalue: 1\n")
        data = self._write("backup.cfg", "[added]\nvalue: 9\n")
        cfg = self._rules("[added]\n")

        CFG.restore(
            str(config),
            CFG.load_backup(str(data), cfg),
            cfg,
        )

        text = config.read_text()
        self.assertIn("[existing]\nvalue: 1", text)
        self.assertIn("[added]\nvalue: 9", text)

    def test_restore_removes_section_and_parameter(self):
        config = self._write(
            "printer.cfg",
            "[alpha]\n"
            "keep: 1\n"
            "drop: 2\n"
            "\n"
            "[remove_me]\n"
            "value: 3\n",
        )
        cfg = self._rules(
            "-[alpha] drop\n"
            "-[remove_me]\n",
        )

        CFG.restore(str(config), {}, cfg)

        text = config.read_text()
        self.assertIn("[alpha]\nkeep: 1", text)
        self.assertNotIn("drop: 2", text)
        self.assertNotIn("[remove_me]", text)

    def test_restore_adds_and_removes_includes(self):
        config = self._write(
            "printer.cfg",
            "[include old.cfg]\n"
            "[alpha]\n"
            "value: 1\n",
        )
        cfg = self._rules(
            "[include base.cfg]\n"
            "-[include old.cfg]\n",
        )

        CFG.restore(str(config), {}, cfg)

        text = config.read_text()
        self.assertTrue(text.startswith("[include base.cfg]\n"))
        self.assertNotIn("[include old.cfg]", text)

    def test_restore_creates_deferred_include_block(self):
        config = self._write(
            "printer.cfg",
            "[alpha]\n"
            "value: 1\n",
        )
        cfg = self._rules("[include late.cfg] defer\n")

        CFG.restore(str(config), {}, cfg)

        text = config.read_text()
        self.assertIn(CFG.DEFERRED_BLOCK_TEXT.strip(), text)
        self.assertIn("[include late.cfg]", text)

    def test_restore_moves_deferred_include_to_deferred_block(self):
        config = self._write(
            "printer.cfg",
            "[include late.cfg]\n"
            "[alpha]\n"
            "value: 1\n",
        )
        cfg = self._rules("[include late.cfg] defer\n")

        CFG.restore(str(config), {}, cfg)

        text = config.read_text()
        self.assertEqual(text.count("[include late.cfg]"), 1)
        self.assertGreater(
            text.index("[include late.cfg]"),
            text.index(CFG.DEFERRED_BLOCK_TEXT.strip()),
        )

    def test_has_changes_matches_restore_requirements(self):
        config = self._write(
            "printer.cfg",
            "[include base.cfg]\n"
            "[alpha]\n"
            "value: 1\n",
        )
        cfg = self._rules(
            "[include base.cfg]\n"
            "[alpha] value\n",
        )

        self.assertFalse(
            CFG.has_changes(
                str(config),
                {"[alpha]": {"value": "1"}},
                cfg,
            )
        )
        self.assertTrue(
            CFG.has_changes(
                str(config),
                {"[alpha]": {"value": "2"}},
                cfg,
            )
        )

    def test_verify_process_rejects_changed_config(self):
        config = self._write("printer.cfg", "[alpha]\nvalue: 1\n")
        data = self._write("backup.cfg", "[alpha]\nvalue: 2\n")
        rules = self._write("rules.cfg", "[alpha] value\n")
        params = CFG.ProcessingParams(
            config_path=str(config),
            data_path=str(data),
            params_path=str(rules),
            mode="verify",
            no_data=False,
            avoid_writes=False,
        )

        with self.assertRaisesRegex(Exception, "changed"):
            CFG.process(params)

        data.write_text("[alpha]\nvalue: 1\n")
        CFG.process(params)

    def test_process_rejects_missing_inputs(self):
        config = self._write("printer.cfg", "[alpha]\nvalue: 1\n")
        rules = self._write("rules.cfg", "[alpha] value\n")

        cases = [
            CFG.ProcessingParams(
                config_path=str(self.root / "missing.cfg"),
                data_path=None,
                params_path=str(rules),
                mode="restore",
                no_data=True,
                avoid_writes=False,
            ),
            CFG.ProcessingParams(
                config_path=str(config),
                data_path=None,
                params_path=str(self.root / "missing-rules.cfg"),
                mode="restore",
                no_data=True,
                avoid_writes=False,
            ),
            CFG.ProcessingParams(
                config_path=str(config),
                data_path=str(self.root / "missing-data.cfg"),
                params_path=str(rules),
                mode="restore",
                no_data=False,
                avoid_writes=False,
            ),
        ]

        for params in cases:
            with self.subTest(params=params):
                with self.assertRaises(Exception):
                    CFG.process(params)

    def test_avoid_writes_skips_restore_when_config_is_unchanged(self):
        config = self._write("printer.cfg", "[alpha]\nvalue: 1\n")
        rules = self._write("rules.cfg", "[alpha] value\n")
        data = self._write("backup.cfg", "[alpha]\nvalue: 1\n")
        params = CFG.ProcessingParams(
            config_path=str(config),
            data_path=str(data),
            params_path=str(rules),
            mode="restore",
            no_data=False,
            avoid_writes=True,
        )

        with mock.patch.object(CFG, "restore") as restore:
            CFG.process(params)

        restore.assert_not_called()

    def test_without_avoid_writes_restore_is_still_called(self):
        config = self._write("printer.cfg", "[alpha]\nvalue: 1\n")
        rules = self._write("rules.cfg", "[alpha] value\n")
        data = self._write("backup.cfg", "[alpha]\nvalue: 1\n")
        params = CFG.ProcessingParams(
            config_path=str(config),
            data_path=str(data),
            params_path=str(rules),
            mode="restore",
            no_data=False,
            avoid_writes=False,
        )

        with mock.patch.object(CFG, "has_changes") as has_changes:
            with mock.patch.object(CFG, "restore") as restore:
                CFG.process(params)

        has_changes.assert_not_called()
        restore.assert_called_once()

    def test_batch_json_avoid_writes_is_honored(self):
        config = self._write("printer.cfg", "[alpha]\nvalue: 1\n")
        rules = self._write("rules.cfg", "[alpha] value\n")
        data = self._write("backup.cfg", "[alpha]\nvalue: 1\n")
        batch = self._write(
            "batch.json",
            json.dumps([{
                "mode": "restore",
                "config": str(config),
                "params": str(rules),
                "data": str(data),
                "avoid_writes": True,
            }]),
        )

        result = self._run_cli("--batch", batch)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Verifying config", result.stdout)
        self.assertNotIn("Restoring config", result.stdout)

    def test_cli_avoid_writes_overrides_batch_entry(self):
        config = self._write("printer.cfg", "[alpha]\nvalue: 1\n")
        rules = self._write("rules.cfg", "[alpha] value\n")
        data = self._write("backup.cfg", "[alpha]\nvalue: 1\n")
        batch = self._write(
            "batch.json",
            json.dumps([{
                "mode": "restore",
                "config": str(config),
                "params": str(rules),
                "data": str(data),
                "avoid_writes": False,
            }]),
        )

        result = self._run_cli("--avoid_writes", "--batch", batch)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Verifying config", result.stdout)
        self.assertNotIn("Restoring config", result.stdout)

    def test_batch_reports_failure_but_continues_later_entries(self):
        config = self._write(
            "printer.cfg",
            "[remove_me]\n"
            "value: 1\n",
        )
        rules = self._write("rules.cfg", "-[remove_me]\n")
        batch = self._write(
            "batch.json",
            json.dumps([
                {
                    "mode": "restore",
                    "config": str(self.root / "missing.cfg"),
                    "params": str(rules),
                    "no_data": True,
                },
                {
                    "mode": "restore",
                    "config": str(config),
                    "params": str(rules),
                    "no_data": True,
                },
            ]),
        )

        result = self._run_cli("--batch", batch)

        self.assertEqual(result.returncode, 1)
        self.assertIn("Error:", result.stderr)
        self.assertNotIn("[remove_me]", config.read_text())

    def test_batch_rejects_empty_and_non_list_payloads(self):
        for payload, expected_code in [([], 3), ({"entry": {}}, 4)]:
            with self.subTest(payload=payload):
                batch = self._write("batch.json", json.dumps(payload))
                result = self._run_cli("--batch", batch)
                self.assertEqual(result.returncode, expected_code)

    def test_noop_restore_removes_staging_file(self):
        config = self._write("printer.cfg", "[alpha]\nvalue: 1\n")
        cfg = self._rules("-[missing]\n")

        CFG.restore(str(config), {}, cfg)

        self.assertEqual(config.read_text(), "[alpha]\nvalue: 1\n")
        self.assertEqual(
            list(self.root.glob("printer.cfg.tmp.*")),
            [],
        )

    def test_restore_dry_run_uses_pid_specific_staging_file(self):
        config = self._write(
            "printer.cfg",
            "[remove_me]\n"
            "value: 1\n",
        )
        cfg = _remove_section_config()
        staging = pathlib.Path(f"{config}.tmp.{os.getpid()}")

        CFG.restore(str(config), {}, cfg, dry=True)

        self.assertTrue(staging.exists())
        self.assertIn("[remove_me]", config.read_text())
        self.assertNotIn("[remove_me]", staging.read_text())
        staging.unlink()

    def test_backup_dry_run_uses_pid_specific_staging_file(self):
        config = self._write("printer.cfg", "[alpha]\nvalue: 1\n")
        backup = self.root / "backup.cfg"
        staging = pathlib.Path(f"{backup}.tmp.{os.getpid()}")

        CFG.backup(
            str(config),
            str(backup),
            _save_value_config(),
            dry=True,
        )

        self.assertFalse(backup.exists())
        self.assertTrue(staging.exists())
        self.assertEqual(staging.read_text(), "[alpha]\nvalue: 1\n")
        staging.unlink()

    @unittest.skipUnless(
        "fork" in multiprocessing.get_all_start_methods(),
        "parallel staging test requires fork",
    )
    def test_parallel_restore_processes_do_not_share_staging_file(self):
        ctx = multiprocessing.get_context("fork")
        config = self._write(
            "printer.cfg",
            "[remove_me]\n"
            "value: 1\n",
        )
        barrier = ctx.Barrier(2)
        queue = ctx.Queue()
        processes = [
            ctx.Process(
                target=_parallel_restore_worker,
                args=(str(config), barrier, queue),
            )
            for _ in range(2)
        ]

        for process in processes:
            process.start()
        results = [queue.get(timeout=10.) for _ in processes]
        for process in processes:
            process.join(10.)

        self.assertEqual(results, [None, None])
        self.assertTrue(all(process.exitcode == 0 for process in processes))
        self.assertNotIn("[remove_me]", config.read_text())
        self.assertEqual(list(self.root.glob("printer.cfg.tmp.*")), [])

    @unittest.skipUnless(
        "fork" in multiprocessing.get_all_start_methods(),
        "parallel staging test requires fork",
    )
    def test_parallel_backup_processes_do_not_share_staging_file(self):
        ctx = multiprocessing.get_context("fork")
        config = self._write("printer.cfg", "[alpha]\nvalue: 1\n")
        backup = self.root / "backup.cfg"
        barrier = ctx.Barrier(2)
        queue = ctx.Queue()
        processes = [
            ctx.Process(
                target=_parallel_backup_worker,
                args=(str(config), str(backup), barrier, queue),
            )
            for _ in range(2)
        ]

        for process in processes:
            process.start()
        results = [queue.get(timeout=10.) for _ in processes]
        for process in processes:
            process.join(10.)

        self.assertEqual(results, [None, None])
        self.assertTrue(all(process.exitcode == 0 for process in processes))
        self.assertEqual(backup.read_text(), "[alpha]\nvalue: 1\n")
        self.assertEqual(list(self.root.glob("backup.cfg.tmp.*")), [])


if __name__ == "__main__":
    unittest.main()
