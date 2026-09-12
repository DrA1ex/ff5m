## Tests for nested operation contexts and cooperative cancellation.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import configparser
import importlib.util
import pathlib
import sys
import unittest


MODULE_PATH = (pathlib.Path(__file__).parents[1] / ".py" / "klipper" /
               "plugins" / "operation_context.py")
SPEC = importlib.util.spec_from_file_location("operation_context", MODULE_PATH)
CONTEXT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTEXT)
sys.path.insert(0, str(MODULE_PATH.parent))
import feather_operation_context_fixtures as FIXTURES  # noqa: E402


class FakeGCode:
    error = RuntimeError

    def __init__(self):
        self.commands = {}
        self.immediate_commands = []
        self.responses = []
        self.scripts = []
        self.script_hook = None

    def register_command(self, name, handler, desc=None):
        self.commands[name] = (handler, desc)

    def register_immediate_command(self, name):
        self.immediate_commands.append(name)

    def respond_raw(self, message):
        self.responses.append(message)

    def run_script_from_command(self, script):
        self.scripts.append(script)
        if self.script_hook is not None:
            self.script_hook(script)


class FakePrinter:
    command_error = RuntimeError

    def __init__(self):
        self.gcode = FakeGCode()
        self.events = {}

    def lookup_object(self, name, default=None):
        if name == "gcode":
            return self.gcode
        return default

    def register_event_handler(self, event, handler):
        self.events.setdefault(event, []).append(handler)

    def send_event(self, event, *args):
        for handler in self.events.get(event, ()):
            handler(*args)


class FakeConfig:
    def __init__(self, printer, name="operation_context", values=None):
        self.printer = printer
        self.name = name
        self.values = values or {}

    def get_printer(self):
        return self.printer

    def get_name(self):
        return self.name

    def get(self, name, default=None):
        return self.values.get(name, default)

    def getboolean(self, name, default=False):
        value = self.values.get(name, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    def error(self, message):
        return ValueError(message)


class FakeCommand:
    error = RuntimeError

    def __init__(self, raw="", **params):
        self.params = params
        self.raw = raw
        self.responses = []

    def get(self, name, default=None):
        return self.params.get(name, default)

    def get_raw_command_parameters(self):
        return self.raw

    def respond_raw(self, message):
        self.responses.append(message)


class OperationContextManagerTest(unittest.TestCase):
    def setUp(self):
        self.printer = FakePrinter()
        self.manager = CONTEXT.OperationContextManager(
            FakeConfig(self.printer))
        self.register_type("outer", name="Outer")
        self.register_type("inner", name="Inner")
        self.register_type(
            "print", name="Print", cancel_mode="cancelable",
            on_cancel="CANCEL_PRINT")
        self.register_type(
            "clean", name="Nozzle Cleaning", on_cancel="CLEAN_NOZZLE_ABORT")
        self.register_type(
            "local", name="Local task", cancel_mode="cancelable",
            on_cancel="LOCAL_ABORT")
        self.register_type(
            "protected", name="Protected task",
            cancel_mode="non_interruptible")
        self.register_type(
            "bed_screws", name="Bed Screws", cancel_mode="cancelable")

    def register_type(self, type_id, name=None,
                      cancel_mode="interruptible", on_cancel=None):
        values = {"name": name or type_id, "cancel_mode": cancel_mode}
        if on_cancel is not None:
            values["on_cancel"] = on_cancel
        return self.manager.register_context_type(FakeConfig(
            self.printer, "operation_context_type %s" % type_id, values))

    def run_command(self, name, raw="", **params):
        command = FakeCommand(raw=raw, **params)
        handler = self.printer.gcode.commands[name][0]
        handler(command)
        return command

    def status(self):
        return self.manager.get_status(0.0)

    def test_registers_small_gcode_contract_and_immediate_cancel(self):
        self.assertEqual(set(self.printer.gcode.commands), {
            "_CONTEXT_BEGIN", "_CONTEXT_STATE", "_CONTEXT_END",
            "_CONTEXT_CANCEL", "_CONTEXT_CANCEL_POINT", "_CONTEXT_RESET",
        })
        self.assertEqual(
            self.printer.gcode.immediate_commands, ["_CONTEXT_CANCEL"])

    def test_fixture_snapshots_match_real_manager_semantics(self):
        actual = []
        commands = (
            ("_CONTEXT_BEGIN", {"TYPE": "bed_screws"}),
            ("_CONTEXT_STATE", {"NAME": "HOMING"}),
            ("_CONTEXT_STATE", {"NAME": "HEATING"}),
            ("_CONTEXT_STATE", {"NAME": "PROBING"}),
            ("_CONTEXT_END", {}),
        )
        for name, params in commands:
            self.run_command(name, **params)
            actual.append(FIXTURES.normalize_status(self.status()))

        expected = FIXTURES.expand_events(
            FIXTURES.SCREWS, ("NONE",))
        self.assertEqual(actual, expected)

    def test_registry_normalizes_type_and_keeps_metadata_separate(self):
        definition = self.register_type(
            "BED_LEVEL", name="Bed Mesh", cancel_mode="cancelable",
            on_cancel="MESH_ABORT")

        self.assertEqual(definition.type_id, "bed_level")
        self.assertEqual(definition.name, "Bed Mesh")
        self.assertEqual(definition.cancel_mode, "cancelable")
        self.assertEqual(definition.on_cancel, "MESH_ABORT")

    def test_registry_defaults_to_interruptible(self):
        definition = self.register_type("ordinary")
        self.assertEqual(definition.cancel_mode, "interruptible")

    def test_later_registry_section_overrides_the_type_definition(self):
        self.register_type(
            "outer", name="Customized", cancel_mode="cancelable")
        self.run_command("_CONTEXT_BEGIN", TYPE="outer")

        status = self.status()
        self.assertEqual(status["context_path"], ("Customized",))
        self.assertTrue(status["cancel_available"])

    def test_registry_rejects_unknown_cancel_mode(self):
        with self.assertRaisesRegex(ValueError, "invalid cancel_mode"):
            self.register_type("invalid", cancel_mode="sometimes")

    def test_repository_registry_applies_product_cancellation_policy(self):
        parser = configparser.RawConfigParser(
            inline_comment_prefixes=(";", "#"))
        parser.read(
            MODULE_PATH.parents[3] / "macros" / "operation_context.cfg",
            encoding="utf-8")
        printer = FakePrinter()
        manager = CONTEXT.OperationContextManager(FakeConfig(printer))
        for section in parser.sections():
            if not section.startswith("operation_context_type "):
                continue
            manager.register_context_type(FakeConfig(
                printer, section, dict(parser.items(section))))

        def run(name, **params):
            printer.gcode.commands[name][0](FakeCommand(**params))

        run("_CONTEXT_BEGIN", TYPE="pid_bed")
        self.assertFalse(manager.get_status(0.0)["cancel_available"])
        run("_CONTEXT_END")
        run("_CONTEXT_BEGIN", TYPE="print")
        status = manager.get_status(0.0)
        self.assertTrue(status["cancel_available"])
        self.assertEqual(status["cancel_target_type"], "print")

    def test_nested_context_restores_outer_state(self):
        self.run_command("_CONTEXT_BEGIN", TYPE="outer")
        self.run_command("_CONTEXT_STATE", NAME="PREPARING")
        self.run_command("_CONTEXT_BEGIN", TYPE="inner")
        self.run_command("_CONTEXT_STATE", NAME="homing")

        self.assertEqual(self.status()["context_path"], ("Outer", "Inner"))
        self.assertEqual(self.status()["context_types"], ("outer", "inner"))
        self.assertEqual(self.status()["current_state"], "HOMING")
        self.run_command("_CONTEXT_END")
        self.assertEqual(self.status()["context_path"], ("Outer",))
        self.assertEqual(self.status()["current_state"], "PREPARING")

    def test_adjacent_contexts_receive_distinct_runtime_ids(self):
        self.run_command("_CONTEXT_BEGIN", TYPE="outer")
        first_id = self.status()["contexts"][0]["id"]
        self.run_command("_CONTEXT_END")
        self.run_command("_CONTEXT_BEGIN", TYPE="inner")

        self.assertGreater(self.status()["contexts"][0]["id"], first_id)
        self.assertEqual(self.status()["context_path"], ("Inner",))

    def test_state_replaces_previous_value_without_push_pop(self):
        self.run_command("_CONTEXT_BEGIN", TYPE="outer")
        self.run_command("_CONTEXT_STATE", NAME="A")
        self.run_command("_CONTEXT_STATE", NAME="B")
        revision = self.manager.revision
        self.run_command("_CONTEXT_STATE", NAME="B")

        self.assertEqual(self.status()["current_state"], "B")
        self.assertEqual(self.manager.revision, revision)

    def test_status_exposes_semantic_frames_and_cancel_target(self):
        self.run_command("_CONTEXT_BEGIN", TYPE="print")
        self.run_command("_CONTEXT_STATE", NAME="HOMING")
        self.run_command("_CONTEXT_BEGIN", TYPE="clean")
        status = self.status()

        self.assertEqual(status["contexts"], ({
            "id": 1, "type": "print", "name": "Print",
            "current_state": "HOMING", "cancel_mode": "cancelable",
        }, {
            "id": 2, "type": "clean", "name": "Nozzle Cleaning",
            "current_state": None, "cancel_mode": "interruptible",
        }))
        self.assertTrue(status["cancel_available"])
        self.assertEqual(status["cancel_target_type"], "print")
        self.assertEqual(status["cancel_target_name"], "Print")
        self.assertEqual(status["cancel_target_mode"], "cancelable")
        self.assertIsNone(status["cancel_blocker_name"])

    def test_cancel_targets_nearest_cancelable_frame(self):
        self.run_command("_CONTEXT_BEGIN", TYPE="print")
        self.run_command("_CONTEXT_BEGIN", TYPE="local")
        self.run_command("_CONTEXT_BEGIN", TYPE="clean")

        result = self.manager.request_cancel()

        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["target_type"], "local")
        self.assertTrue(self.status()["cancel_pending"])

    def test_repeated_cancel_keeps_the_original_target(self):
        self.run_command("_CONTEXT_BEGIN", TYPE="print")
        first = self.manager.request_cancel()
        second = self.manager.request_cancel()

        self.assertEqual(first["target_id"], second["target_id"])
        self.assertEqual(first["request_id"], second["request_id"])
        self.assertEqual(second["status"], "already_pending")

    def test_interruptible_stack_targets_the_outermost_frame(self):
        self.run_command("_CONTEXT_BEGIN", TYPE="outer")
        self.run_command("_CONTEXT_BEGIN", TYPE="inner")

        result = self.manager.request_cancel()

        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["target_type"], "outer")
        self.assertEqual(result["target_mode"], "interruptible")
        self.assertTrue(self.status()["cancel_pending"])

    def test_non_interruptible_frame_blocks_outer_domain(self):
        self.run_command("_CONTEXT_BEGIN", TYPE="print")
        self.run_command("_CONTEXT_BEGIN", TYPE="protected")

        result = self.manager.request_cancel()

        self.assertEqual(result["status"], "non_interruptible")
        self.assertFalse(result["accepted"])
        self.assertEqual(result["blocker_type"], "protected")
        self.assertFalse(self.status()["cancel_available"])
        self.assertEqual(
            self.status()["cancel_blocker_name"], "Protected task")

    def test_nested_cancelable_domain_precedes_outer_barrier(self):
        self.run_command("_CONTEXT_BEGIN", TYPE="protected")
        self.run_command("_CONTEXT_BEGIN", TYPE="local")

        result = self.manager.request_cancel()

        self.assertTrue(result["accepted"])
        self.assertEqual(result["target_type"], "local")

    def test_cancel_command_warns_when_abort_is_the_only_safe_option(self):
        self.run_command("_CONTEXT_BEGIN", TYPE="protected")
        command = self.run_command("_CONTEXT_CANCEL")

        self.assertTrue(command.responses[0].startswith("!! "))
        self.assertIn("cannot be interrupted safely", command.responses[0])

    def test_safe_point_unwinds_children_runs_cleanup_and_aborts_chain(self):
        self.run_command("_CONTEXT_BEGIN", TYPE="outer")
        self.run_command("_CONTEXT_BEGIN", TYPE="print")
        self.run_command("_CONTEXT_BEGIN", TYPE="clean")
        self.manager.request_cancel()

        with self.assertRaisesRegex(RuntimeError, "Operation cancelled: Print"):
            self.run_command("_CONTEXT_CANCEL_POINT")

        self.assertEqual(self.printer.gcode.scripts, [
            "CLEAN_NOZZLE_ABORT", "CANCEL_PRINT"])
        self.assertEqual(self.status()["context_types"], ("outer",))
        self.assertFalse(self.status()["cancel_pending"])

    def test_begin_state_and_end_are_implicit_safe_points(self):
        for command, params in (
                ("_CONTEXT_BEGIN", {"TYPE": "inner"}),
                ("_CONTEXT_STATE", {"NAME": "NEXT"}),
                ("_CONTEXT_END", {})):
            with self.subTest(command=command):
                self.run_command("_CONTEXT_RESET")
                self.run_command("_CONTEXT_BEGIN", TYPE="print")
                self.manager.request_cancel()
                with self.assertRaisesRegex(RuntimeError, "Operation cancelled"):
                    self.run_command(command, **params)

    def test_temporary_state_restores_the_replaced_state(self):
        self.run_command("_CONTEXT_BEGIN", TYPE="outer")
        self.run_command("_CONTEXT_STATE", NAME="LEVELING")
        self.run_command(
            "_CONTEXT_STATE", NAME="HEATING BED", TEMPORARY=1)

        self.assertEqual(self.status()["current_state"], "HEATING BED")
        self.run_command("_CONTEXT_STATE", RESTORE=1)
        self.assertEqual(self.status()["current_state"], "LEVELING")

    def test_temporary_states_are_nested_and_restore_lifo(self):
        self.run_command("_CONTEXT_BEGIN", TYPE="outer")
        self.run_command("_CONTEXT_STATE", NAME="A")
        self.run_command("_CONTEXT_STATE", NAME="B", TEMPORARY=1)
        self.run_command("_CONTEXT_STATE", NAME="C", TEMPORARY=1)

        self.run_command("_CONTEXT_STATE", RESTORE=1)
        self.assertEqual(self.status()["current_state"], "B")
        self.run_command("_CONTEXT_STATE", RESTORE=1)
        self.assertEqual(self.status()["current_state"], "A")

    def test_restore_without_temporary_state_warns_and_is_a_no_op(self):
        self.run_command("_CONTEXT_BEGIN", TYPE="outer")
        revision = self.manager.revision
        command = self.run_command("_CONTEXT_STATE", RESTORE=1)

        self.assertEqual(self.manager.revision, revision)
        self.assertIn("requires a temporary state", command.responses[0])

    def test_invalid_state_flags_warn_without_raising_or_mutating(self):
        self.run_command("_CONTEXT_BEGIN", TYPE="outer")
        revision = self.manager.revision

        invalid = self.run_command(
            "_CONTEXT_STATE", NAME="HEATING", TEMPORARY="sometimes")
        combined = self.run_command(
            "_CONTEXT_STATE", RESTORE=1, TEMPORARY=1)

        self.assertEqual(self.manager.revision, revision)
        self.assertIn("must be 0 or 1", invalid.responses[0])
        self.assertIn("cannot combine", combined.responses[0])

    def test_state_requires_name_even_when_an_option_is_present(self):
        self.run_command("_CONTEXT_BEGIN", TYPE="outer")
        revision = self.manager.revision
        command = self.run_command("_CONTEXT_STATE", TEMPORARY=1)

        self.assertEqual(self.manager.revision, revision)
        self.assertIn("requires a non-empty state", command.responses[0])

    def test_end_with_temporary_state_warns_without_changing_outer_frame(self):
        self.run_command("_CONTEXT_BEGIN", TYPE="outer")
        self.run_command("_CONTEXT_STATE", NAME="OUTER")
        self.run_command("_CONTEXT_BEGIN", TYPE="inner")
        self.run_command("_CONTEXT_STATE", NAME="INNER")
        self.run_command(
            "_CONTEXT_STATE", NAME="HEATING BED", TEMPORARY=1)

        command = self.run_command("_CONTEXT_END")

        self.assertIn("temporary state", command.responses[0])
        self.assertEqual(self.status()["context_path"], ("Outer",))
        self.assertEqual(self.status()["current_state"], "OUTER")

    def test_pending_cancel_can_be_cleared_by_request_id(self):
        self.run_command("_CONTEXT_BEGIN", TYPE="outer")
        request = self.manager.request_cancel()

        result = self.manager.clear_cancel(request["request_id"])

        self.assertEqual(result["status"], "cleared")
        self.assertTrue(result["cleared"])
        self.assertFalse(self.status()["cancel_pending"])
        self.assertTrue(self.status()["cancel_available"])

    def test_stale_request_id_does_not_clear_pending_cancel(self):
        self.run_command("_CONTEXT_BEGIN", TYPE="outer")
        request = self.manager.request_cancel()

        result = self.manager.clear_cancel(request["request_id"] + 1)

        self.assertEqual(result["status"], "stale_request")
        self.assertFalse(result["cleared"])
        self.assertTrue(self.status()["cancel_pending"])

    def test_cancel_cannot_be_cleared_after_delivery_starts(self):
        self.run_command("_CONTEXT_BEGIN", TYPE="outer")
        request = self.manager.request_cancel()
        self.manager.cancelling = True

        result = self.manager.clear_cancel(request["request_id"])

        self.assertEqual(result["status"], "too_late")
        self.assertFalse(result["cleared"])
        self.assertTrue(self.status()["cancel_pending"])
        self.manager.cancelling = False

    def test_cleanup_failure_does_not_skip_remaining_cleanup(self):
        self.run_command("_CONTEXT_BEGIN", TYPE="print")
        self.run_command("_CONTEXT_BEGIN", TYPE="clean")
        self.manager.request_cancel()

        def fail_first(script):
            if script == "CLEAN_NOZZLE_ABORT":
                raise RuntimeError("cleanup broke")
        self.printer.gcode.script_hook = fail_first
        command = FakeCommand()
        with self.assertRaises(RuntimeError):
            self.manager.cmd_CONTEXT_CANCEL_POINT(command)

        self.assertEqual(self.printer.gcode.scripts, [
            "CLEAN_NOZZLE_ABORT", "CANCEL_PRINT"])
        self.assertTrue(any("cleanup" in item for item in command.responses))

    def test_invalid_commands_warn_and_do_not_mutate(self):
        commands = (
            ("_CONTEXT_END", {}),
            ("_CONTEXT_STATE", {"NAME": "HOMING"}),
            ("_CONTEXT_CANCEL", {}),
            ("_CONTEXT_CANCEL_POINT", {}),
        )
        for name, params in commands:
            with self.subTest(name=name):
                revision = self.manager.revision
                command = self.run_command(name, **params)
                self.assertEqual(self.manager.revision, revision)
                self.assertTrue(command.responses[0].startswith("!! "))

        unknown = self.run_command("_CONTEXT_BEGIN", TYPE="missing")
        missing_type = self.run_command("_CONTEXT_BEGIN")
        self.assertEqual(self.manager.contexts, [])
        self.assertTrue(unknown.responses[0].startswith("!! "))
        self.assertTrue(missing_type.responses[0].startswith("!! "))

    def test_each_lifecycle_event_resets_stack_and_pending_cancel(self):
        events = (
            ("gcode:command_error", ()),
            ("gcode:request_restart", (12.5,)),
            ("klippy:shutdown", ()),
            ("klippy:disconnect", ()),
        )
        for event, args in events:
            with self.subTest(event=event):
                self.run_command("_CONTEXT_BEGIN", TYPE="print")
                self.manager.request_cancel()
                revision = self.manager.revision
                self.printer.send_event(event, *args)
                self.assertEqual(self.manager.contexts, [])
                self.assertIsNone(self.manager.pending_cancel)
                self.assertEqual(self.manager.revision, revision + 1)

    def test_manual_reset_is_idempotent(self):
        revision = self.manager.revision
        self.run_command("_CONTEXT_RESET")
        self.assertEqual(self.manager.revision, revision)
        self.run_command("_CONTEXT_BEGIN", TYPE="outer")
        self.run_command("_CONTEXT_RESET")
        self.assertEqual(self.manager.contexts, [])


if __name__ == "__main__":
    unittest.main()
