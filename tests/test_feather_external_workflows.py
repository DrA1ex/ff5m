## External operation lifecycle and Feather workflow priority contracts.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import unittest
from unittest import mock

try:
    from tests import test_feather_lazy_features as lazy_tests
    from tests.test_operation_context import CONTEXT, FakePrinter, FakeConfig, FakeCommand
    from tests.test_feather_screen import StatusObject
except ImportError:
    import test_feather_lazy_features as lazy_tests
    from test_operation_context import CONTEXT, FakePrinter, FakeConfig, FakeCommand
    from test_feather_screen import StatusObject


FEATHER = lazy_tests.FEATHER
Page = FEATHER.ScreenPage


class ExternalWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.host = lazy_tests.ControllerFeatureRoutingTest.controller()
        self.host._show_page = self.show_page
        self.host._show_message = mock.Mock()
        self.host._run_script = mock.Mock()
        self.host.print_stats = StatusObject({"state": "standby"})
        self.host.virtual_sdcard = mock.Mock(is_active=lambda: False)
        self.host.resurrection = StatusObject({"state": "resurrection", "available": True})
        self.callbacks = []
        self.host.reactor.register_callback = self.callbacks.append
        self.printer = FakePrinter()
        self.context = CONTEXT.OperationContextManager(FakeConfig(self.printer))
        self.host.operation_context = self.context
        self.printer.register_event_handler(
            "operation_context:begin", self.host._handle_operation_begin)
        self.printer.register_event_handler(
            "operation_context:end", self.host._handle_operation_end)

    def show_page(self, page):
        self.host.page = page

    def begin(self, kind):
        self.context.register_context_type(FakeConfig(
            self.printer, "operation_context_type " + kind))
        self.context.cmd_CONTEXT_BEGIN(FakeCommand(TYPE=kind))

    def finish(self):
        self.context.cmd_CONTEXT_END(FakeCommand())

    def flush(self):
        callbacks = self.callbacks[:]
        self.callbacks.clear()
        for callback in callbacks:
            callback(100.0)

    def feature(self):
        return self.host.feature_manager.peek("calibration")

    def test_external_calibrations_show_progress_without_running_macro_again(self):
        for context_type, kind in (
                ("bed_screws", "screws"), ("auto_bed_level", "mesh"),
                ("bed_level", "mesh"), ("pid_bed", "pid_bed"),
                ("pid_extruder", "pid_extruder"), ("input_shaper", "shaper"),
                ("z_offset", "z")):
            with self.subTest(context_type=context_type):
                self.host.page = Page.IDLE_HOME
                self.begin(context_type)
                self.assertEqual(self.host.page, Page.CALIBRATION_PROGRESS)
                self.assertEqual(self.feature().calibration_kind, kind)
                self.finish()
                self.flush()
                self.assertEqual(self.host.page, Page.CALIBRATION_RESULT)
                self.assertIsNone(self.feature().calibration_error)
        self.host._run_script.assert_not_called()

    def test_active_bed_screws_is_adopted_from_operation_context_snapshot(self):
        begin_handlers = self.printer.events["operation_context:begin"]
        self.printer.events["operation_context:begin"] = []
        try:
            self.begin("bed_screws")
        finally:
            self.printer.events["operation_context:begin"] = begin_handlers
        self.assertIsNone(self.feature())

        self.host._update_operation_context(100.0)

        self.assertEqual(self.host.page, Page.CALIBRATION_PROGRESS)
        self.assertEqual(self.feature().calibration_kind, "screws")
        self.assertEqual(
            self.feature().external_context_id,
            self.context.get_status(100.0)["contexts"][0]["id"])
        self.finish()
        self.flush()
        self.assertEqual(self.host.page, Page.CALIBRATION_RESULT)
        self.assertIsNone(self.feature().external_context_id)
        self.host._run_script.assert_not_called()

    def test_rejected_context_is_not_adopted_on_a_later_state_revision(self):
        self.host.page = Page.ERROR
        self.begin("bed_screws")
        self.assertIsNone(self.feature())

        self.host.page = Page.IDLE_HOME
        self.context.cmd_CONTEXT_STATE(FakeCommand(NAME="PROBING"))
        self.host._update_operation_context(100.0)

        self.assertEqual(self.host.page, Page.IDLE_HOME)
        self.assertIsNone(self.feature())
        self.finish()
        self.flush()
        self.assertEqual(self.host.page, Page.IDLE_HOME)

    def test_nested_calibration_keeps_outer_workflow_and_finishes_only_with_root(self):
        self.begin("bed_screws")
        self.begin("pid_bed")
        self.assertEqual(self.feature().calibration_kind, "screws")
        self.finish()
        self.flush()
        self.assertEqual(self.host.page, Page.CALIBRATION_PROGRESS)
        self.feature().on_gcode_output("front: adjust CW 00:15")
        self.finish()
        self.flush()
        self.assertEqual(self.host.page, Page.CALIBRATION_RESULT)
        self.assertEqual(self.feature().calibration_results,
                         [{"name": "front", "direction": "CW", "turns": "00:15"}])

    def test_nested_calibration_under_print_or_other_operation_is_ignored(self):
        for root in ("print", "filament", "cold_pull", "nozzle_clean"):
            with self.subTest(root=root):
                self.begin(root)
                self.begin("auto_bed_level")
                self.assertIsNone(self.feature())
                self.finish()
                self.finish()
        self.assertEqual(self.host.page, Page.CONTROL_HOME)

    def test_existing_workflow_pages_keep_priority(self):
        for page in (
                Page.PRINTING, Page.PAUSED, Page.CALIBRATION_PROGRESS,
                Page.CALIBRATION_CONFIRM, Page.CALIBRATION_RESULT,
                Page.CALIBRATION_Z, Page.SAFE_Z_CALIBRATION,
                Page.EXTRUDER_CALIBRATION, Page.FILAMENT_ACTION,
                Page.NETWORK_PROGRESS, Page.CANCEL_CONFIRM,
                Page.ACTION_PROMPT, Page.ERROR, Page.MESSAGE):
            with self.subTest(page=page):
                self.host.page = page
                self.begin("pid_bed")
                self.assertEqual(self.host.page, page)
                self.finish()
                self.flush()
                self.assertEqual(self.host.page, page)
                self.assertIsNone(self.feature())

    def test_print_status_and_virtual_sd_protect_even_an_idle_home_page(self):
        for state, sd_active in (("printing", False), ("paused", False),
                                 ("standby", True)):
            with self.subTest(state=state, sd_active=sd_active):
                self.host.page = Page.IDLE_HOME
                self.host.print_stats = StatusObject({"state": state})
                self.host.virtual_sdcard.is_active = lambda: sd_active
                self.begin("pid_bed")
                self.finish()
                self.flush()
                self.assertEqual(self.host.page, Page.IDLE_HOME)
                self.assertIsNone(self.feature())

    def test_busy_ui_does_not_adopt_operation_later(self):
        for field, value in (("command_depth", 1), ("busy_message", "Working"),
                             ("pending_action", "print.resume")):
            with self.subTest(field=field):
                original = getattr(self.host, field)
                setattr(self.host, field, value)
                self.begin("pid_bed")
                setattr(self.host, field, original)
                self.begin("bed_screws")
                self.finish()
                self.finish()
                self.flush()
                self.assertIsNone(self.feature())

    def test_mesh_result_reads_observed_matrix(self):
        self.host.bed_mesh = StatusObject({"probed_matrix": [[0.1, 0.2], [0.3, 0.4]]})
        self.begin("auto_bed_level")
        self.finish()
        self.flush()
        self.assertEqual(self.feature().calibration_mesh, [[0.1, 0.2], [0.3, 0.4]])
        self.assertTrue(self.feature()._mesh_save_available())

    def test_progress_renders_live_phase_and_mesh_result_uses_actual_profile(self):
        self.host._show_page = FEATHER.FeatherScreen._show_page.__get__(self.host)
        self.host.bed_mesh = StatusObject({
            "probed_matrix": [[0.1, 0.2], [0.3, 0.4]],
            "profile_name": "custom",
        })
        with mock.patch.object(self.host.renderer, "text",
                               wraps=self.host.renderer.text) as text:
            self.begin("auto_bed_level")
            self.assertIsNotNone(self.feature().external_context_id)
            self.context.cmd_CONTEXT_STATE(FakeCommand(NAME="LEVELING"))
            self.host.feature_manager.update(100.0)
            labels = [call.args[2] for call in text.call_args_list]
            self.assertTrue(any("LEVELING" in label for label in labels))
            self.finish()
            self.flush()
            labels = [call.args[2] for call in text.call_args_list]
            self.assertIn("PROFILE CUSTOM", labels)
        self.assertEqual(self.host.page, Page.CALIBRATION_RESULT)

    def test_ui_started_calibration_retains_its_results_and_completion(self):
        feature = self.host.feature_manager.get("calibration")
        feature.calibration_kind = "screws"
        feature.calibration_results = [{"name": "existing"}]
        self.host.page = Page.CALIBRATION_PROGRESS
        self.begin("bed_screws")
        self.finish()
        self.flush()
        self.assertIsNone(feature.external_context_id)
        self.assertEqual(feature.calibration_results, [{"name": "existing"}])
        self.assertEqual(self.host.page, Page.CALIBRATION_PROGRESS)

    def test_cancel_confirmation_closes_when_external_operation_finishes(self):
        self.begin("bed_screws")
        self.host.page = Page.CANCEL_CONFIRM
        self.host.operation_cancel_return_page = Page.CALIBRATION_PROGRESS
        self.host.cancel_mode = "confirm"
        self.finish()
        self.flush()
        self.assertEqual(self.host.page, Page.CALIBRATION_RESULT)
        self.assertIsNone(self.host.cancel_mode)

    def test_command_error_does_not_report_success(self):
        self.begin("pid_bed")
        self.printer.send_event("gcode:command_error")
        self.flush()
        self.assertEqual(self.host.page, Page.CALIBRATION_RESULT)
        self.assertEqual(self.feature().calibration_error, "Operation interrupted")
        self.assertFalse(self.feature()._tuning_save_available())

    def test_cancel_from_fluidd_finishes_as_cancelled(self):
        self.begin("bed_screws")
        self.context.request_cancel()
        with self.assertRaisesRegex(RuntimeError, "Operation cancelled"):
            self.context.cmd_CONTEXT_CANCEL_POINT(FakeCommand())
        self.printer.send_event("gcode:command_error")
        self.flush()
        self.assertEqual(self.host.page, Page.CALIBRATION_RESULT)
        self.assertTrue(self.feature().calibration_cancelled)
        self.assertIsNone(self.feature().calibration_error)

    def test_finishing_does_not_replace_print_or_error_screen(self):
        for page in (Page.PRINTING, Page.ERROR):
            with self.subTest(page=page):
                self.host.page = Page.IDLE_HOME
                self.begin("bed_screws")
                self.finish()
                self.host.page = page
                self.flush()
                self.assertEqual(self.host.page, page)

    def test_deactivation_discards_pending_completion(self):
        self.begin("bed_screws")
        self.finish()
        self.feature().deactivate()
        self.flush()
        self.assertEqual(self.host.page, Page.CALIBRATION_PROGRESS)

    def test_external_recovery_survives_prompt_end_and_hands_over_to_print(self):
        self.host.page = Page.RECOVERY_PROMPT
        self.host.action_prompt = {"title": "Resurrection"}
        self.host.action_prompt_visible = True
        self.begin("recovery")
        self.host._handle_gcode_output("// action:prompt_end")
        self.assertEqual(self.host.page, Page.CALIBRATION_PROGRESS)
        self.assertEqual(self.feature().calibration_kind, "recovery")
        self.finish()
        self.host.resurrection = StatusObject({"state": "printing", "available": False})
        self.host.print_stats = StatusObject({"state": "printing"})
        self.flush()
        self.host._show_message.assert_not_called()
        self.assertIsNone(self.feature().external_context_id)

    def test_recovery_cleanup_observes_status_after_context_end(self):
        self.begin("recovery")
        self.finish()
        self.host.resurrection = StatusObject({"state": "idle", "available": False})
        self.flush()
        self.host._show_message.assert_called_once_with(
            "Recovery data cleaned up", Page.IDLE_HOME)

    def test_failed_recovery_returns_to_recovery_prompt(self):
        self.begin("recovery")
        self.context.cmd_CONTEXT_RESET(FakeCommand())
        self.flush()
        self.host._show_message.assert_called_once_with(
            "Recovery interrupted", Page.RECOVERY_PROMPT)

    def test_recovery_failure_after_context_end_is_not_cleanup_success(self):
        self.begin("recovery")
        self.finish()
        self.host.resurrection = StatusObject({"state": "error", "available": False})
        self.flush()
        self.host._show_message.assert_called_once_with(
            "Recovery interrupted", Page.RECOVERY_PROMPT)

    def test_display_failure_does_not_abort_macro(self):
        self.host._show_page = mock.Mock(side_effect=RuntimeError("display failed"))
        self.begin("pid_bed")
        self.context.cmd_CONTEXT_STATE(FakeCommand(NAME="TUNING"))
        self.assertEqual(self.context.get_status(100.0)["current_state"], "TUNING")
        self.finish()
        self.flush()


if __name__ == "__main__":
    unittest.main()
