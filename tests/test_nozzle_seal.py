## Behavioral tests for the shared nozzle sealing macros.
import unittest

from tests.gcode_macro_harness import MacroExecution, render_macro
from tests.test_gcode_macros import BASE, start_print_state
from tests.test_operation_context import FakeCommand, FakePrinter, load_repository_context_registry


class NozzleSealTest(unittest.TestCase):
    def harness(self, current=120., enabled=True, probe_z=-0.2, parked_z=10.4,
                gcode_probe_z=-0.6, parents=("print",), fail_lift=False):
        printer = start_print_state(seal_nozzle_on_heat=enabled,
                                    extruder_temperature=current)
        printer["toolhead"]["position"] = {"z": parked_z}
        printer["gcode_move"] = {"gcode_position": {"z": 10.}}
        printer["probe"] = {"last_z_result": 99.}

        context_printer = FakePrinter()
        manager = load_repository_context_registry(context_printer)
        for parent in parents:
            manager.cmd_CONTEXT_BEGIN(FakeCommand(TYPE=parent))

        def terminal(command):
            if command.startswith("_CONTEXT_BEGIN TYPE="):
                manager.cmd_CONTEXT_BEGIN(FakeCommand(TYPE=command.split("=", 1)[1]))
            elif command == "_CONTEXT_END":
                manager.cmd_CONTEXT_END(FakeCommand())
            elif command == "PROBE":
                printer["probe"]["last_z_result"] = probe_z
                printer["toolhead"]["position"]["z"] = probe_z
                printer["gcode_move"]["gcode_position"]["z"] = gcode_probe_z
            elif command.startswith("G1 Z"):
                if fail_lift:
                    context_printer.send_event("gcode:command_error")
                    raise RuntimeError("Move failed")
                printer["toolhead"]["position"]["z"] = parked_z
                printer["gcode_move"]["gcode_position"]["z"] = 10.

        names = ("_NOZZLE_SEAL_TAKE", "_NOZZLE_SEAL_SAVE_PROBE",
                 "_NOZZLE_SEAL_RELEASE")
        execution = MacroExecution([(BASE, name, "gcode_macro") for name in names],
                                   printer, terminal)
        context_printer.gcode.script_hook = execution.run
        return execution, manager

    def test_temperature_boundary_and_cooling(self):
        for current, target, enabled, expected in (
                (120, 220, True, True), (170, 220, True, True),
                (171, 220, True, False), (220, 220, True, False),
                (221, 220, True, False), (222, 220, True, True),
                (240, 220, True, True), (120, 0, True, False),
                (120, 220, False, False)):
            with self.subTest(current=current, target=target, enabled=enabled):
                h, manager = self.harness(current, enabled)
                h.run("_NOZZLE_SEAL_TAKE TEMP=%s" % target)
                self.assertEqual(h.commands.count("PROBE"), int(expected))
                self.assertEqual(manager.get_status(0)["context_types"],
                                 ("print", "nozzle_seal") if expected else ("print",))
                h.run("_NOZZLE_SEAL_RELEASE")
                moves = [c for c in h.commands if c.startswith("G1 Z")]
                self.assertEqual(len(moves), int(expected))
                self.assertEqual(manager.get_status(0)["context_types"], ("print",))

    def test_probe_is_observed_after_command_and_release_is_idempotent(self):
        h, manager = self.harness()
        h.run("_NOZZLE_SEAL_TAKE TEMP=220")
        self.assertEqual(h.printer["gcode_macro _NOZZLE_SEAL_TAKE"]["probe_z"], -0.2)
        h.run("_NOZZLE_SEAL_RELEASE")
        self.assertEqual(h.printer["toolhead"]["position"]["z"], 10.4)
        self.assertIsNone(h.printer["gcode_macro _NOZZLE_SEAL_TAKE"]["probe_z"])
        count = len(h.commands)
        h.run("_NOZZLE_SEAL_RELEASE")
        self.assertEqual(len(h.commands), count)
        self.assertEqual(manager.get_status(0)["context_types"], ("print",))

    def test_probe_is_saved_and_insufficient_clearance_rejected_on_release(self):
        h, _ = self.harness(probe_z=10.1, gcode_probe_z=9.7)
        h.run("_NOZZLE_SEAL_TAKE TEMP=220")
        self.assertEqual(h.printer["gcode_macro _NOZZLE_SEAL_TAKE"]["probe_z"], 10.1)
        self.assertNotIn("M104 S0", h.commands)
        self.assertFalse(any(c.startswith("_RAISE_WITH_PRINT_CANCEL") for c in h.commands))

        h.run("_NOZZLE_SEAL_RELEASE")
        self.assertFalse(any(c.startswith("G1 Z") for c in h.commands))
        self.assertIn("M104 S0", h.commands)
        self.assertTrue(any(c.startswith("_RAISE_WITH_PRINT_CANCEL") for c in h.commands))
        self.assertIsNone(h.printer["gcode_macro _NOZZLE_SEAL_TAKE"]["probe_z"])

    def test_unsafe_release_does_not_move_toward_bed(self):
        h, _ = self.harness()
        h.run("_NOZZLE_SEAL_TAKE TEMP=220")
        h.printer["gcode_move"]["gcode_position"]["z"] = 10.
        h.run("_NOZZLE_SEAL_RELEASE")
        self.assertFalse(any(c.startswith("G1 Z") for c in h.commands))
        self.assertIn("M104 S0", h.commands)
        self.assertTrue(any(c.startswith("_RAISE_WITH_PRINT_CANCEL") for c in h.commands))
        self.assertIsNone(h.printer["gcode_macro _NOZZLE_SEAL_TAKE"]["probe_z"])

    def test_new_take_discards_state_left_by_interrupted_operation(self):
        for enabled, target in ((False, 220), (True, 0), (True, 150), (True, 220)):
            with self.subTest(enabled=enabled, target=target):
                h, manager = self.harness()
                h.run("_NOZZLE_SEAL_TAKE TEMP=220")
                # A G-code error discards operation contexts, not macro variables.
                manager.printer.send_event("gcode:command_error")
                manager.cmd_CONTEXT_BEGIN(FakeCommand(TYPE="print"))
                h.printer["mod_params"]["variables"]["seal_nozzle_on_heat"] = enabled
                h.commands.clear()
                h.run("_NOZZLE_SEAL_TAKE TEMP=%s" % target)
                h.run("_NOZZLE_SEAL_RELEASE")
                expected = enabled and target == 220
                self.assertEqual(h.commands.count("PROBE"), int(expected))
                self.assertEqual(h.commands.count("G1 Z10.0 F1800"), int(expected))
                self.assertIsNone(h.printer["gcode_macro _NOZZLE_SEAL_TAKE"]["probe_z"])
                self.assertEqual(manager.get_status(0)["context_types"], ("print",))

    def test_release_without_z_homing_rejects_motion(self):
        h, _ = self.harness()
        h.run("_NOZZLE_SEAL_TAKE TEMP=220")
        h.printer["toolhead"]["homed_axes"] = "xy"
        h.run("_NOZZLE_SEAL_RELEASE")
        self.assertFalse(any(c.startswith("G1 Z") for c in h.commands))
        self.assertIn("M104 S0", h.commands)
        self.assertTrue(any(c.startswith("_RAISE_WITH_PRINT_CANCEL") for c in h.commands))
        self.assertIsNone(h.printer["gcode_macro _NOZZLE_SEAL_TAKE"]["probe_z"])

    def test_cancel_without_safe_lift_still_runs_parent_cleanup(self):
        for homed, z in (("xy", -0.6), ("xyz", 9.7)):
            with self.subTest(homed=homed, z=z):
                h, manager = self.harness()
                h.run("_NOZZLE_SEAL_TAKE TEMP=220")
                h.printer["toolhead"]["homed_axes"] = homed
                h.printer["gcode_move"]["gcode_position"]["z"] = z
                manager.request_cancel()
                with self.assertRaisesRegex(RuntimeError, "Operation cancelled"):
                    manager.cmd_CONTEXT_CANCEL_POINT(FakeCommand())
                self.assertFalse(any(c.startswith("G1 Z") for c in h.commands))
                self.assertIn("CANCEL_PRINT", h.commands)
                self.assertIsNone(h.printer["gcode_macro _NOZZLE_SEAL_TAKE"]["probe_z"])
                self.assertEqual(manager.get_status(0)["context_types"], ())

    def test_failed_release_does_not_leave_stale_seal(self):
        h, manager = self.harness(fail_lift=True)
        h.run("_NOZZLE_SEAL_TAKE TEMP=220")
        with self.assertRaisesRegex(RuntimeError, "Move failed"):
            h.run("_NOZZLE_SEAL_RELEASE")
        self.assertEqual(manager.get_status(0)["context_types"], ())
        self.assertIsNone(h.printer["gcode_macro _NOZZLE_SEAL_TAKE"]["probe_z"])
        count = len(h.commands)
        h.run("_NOZZLE_SEAL_RELEASE")
        self.assertEqual(len(h.commands), count)

    def test_disabled_sealing_is_silent_and_does_not_move(self):
        h, manager = self.harness(enabled=False)
        h.run("_NOZZLE_SEAL_TAKE TEMP=220")
        h.run("_NOZZLE_SEAL_RELEASE")
        h.run("_NOZZLE_SEAL_RELEASE ABORT=1")
        self.assertEqual(h.commands, [])
        self.assertEqual(manager.get_status(0)["context_types"], ("print",))

    def test_cancel_releases_seal_before_parent_cleanup(self):
        for parents, cleanup in (
                (("print",), "CANCEL_PRINT"),
                (("print", "nozzle_clean"), "CANCEL_PRINT"),
                (("auto_bed_level", "bed_level", "nozzle_clean"), "_STOP"),
                (("bed_screws", "nozzle_clean"), "_STOP"),
                (("nozzle_clean",), None)):
            with self.subTest(parents=parents):
                h, manager = self.harness(parents=parents)
                h.run("_NOZZLE_SEAL_TAKE TEMP=220")
                self.assertEqual(manager.get_status(0)["context_types"],
                                 parents + ("nozzle_seal",))
                request = manager.request_cancel()
                self.assertEqual(request["target_type"], parents[0])
                with self.assertRaisesRegex(RuntimeError, "Operation cancelled"):
                    manager.cmd_CONTEXT_CANCEL_POINT(FakeCommand())
                self.assertEqual(h.commands.count("G1 Z10.0 F1800"), 1)
                if cleanup:
                    self.assertLess(h.commands.index("G1 Z10.0 F1800"),
                                    h.commands.index(cleanup))
                self.assertNotIn("M104 S0", h.commands)
                self.assertIsNone(h.printer["gcode_macro _NOZZLE_SEAL_TAKE"]["probe_z"])
                self.assertEqual(manager.get_status(0)["context_types"], ())

    def test_cancel_before_probe_does_not_take_seal(self):
        h, manager = self.harness()
        manager.request_cancel()
        with self.assertRaisesRegex(RuntimeError, "Operation cancelled"):
            h.run("_NOZZLE_SEAL_TAKE TEMP=220")
        self.assertNotIn("PROBE", h.commands)
        self.assertIn("CANCEL_PRINT", h.commands)
        self.assertEqual(manager.get_status(0)["context_types"], ())

    def test_cleaning_starts_heating_and_tares_before_sealing(self):
        printer = start_print_state()
        commands = render_macro(BASE, "CLEAR_NOZZLE", printer=printer,
                                params={"EXTRUDER_TEMP": 220, "BED_TEMP": 60}).commands
        steps = ("_WAIT_TEMPERATURE CMD=M140 VALUE=60.0 BELOW=2 ABOVE=3",
                 "M104 S220.0", "LOAD_CELL_TARE", "_NOZZLE_SEAL_TAKE TEMP=220.0",
                 "_WAIT_TEMPERATURE CMD=M104 VALUE=220.0", "_NOZZLE_SEAL_RELEASE")
        indices = [commands.index(step) for step in steps]
        self.assertEqual(indices, sorted(indices))
        self.assertEqual(commands.count("LOAD_CELL_TARE"), 1)
