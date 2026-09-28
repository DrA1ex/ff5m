## Behavioral tests for the observed timelapse contract and recovery dialogs.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import unittest
from types import SimpleNamespace
from unittest import mock

from tests.test_feather_screen import FEATHER, Reactor, StatusObject
from tests.feather_render_test_helper import RenderCapture
from timelapse_state import TimelapsePhase, TimelapseState


class TimelapseContractTest(unittest.TestCase):
    def status(self, waiting=False, held=False, frame=False, user=False, paused=False):
        self.objects = {
            "gcode_macro _TIMELAPSE_START_GUARD": StatusObject({
                "waiting": waiting, "sd_held": held, "wait_status": "busy"}),
            "gcode_macro TIMELAPSE_TAKE_FRAME": StatusObject({
                "is_paused": frame, "user_pause_requested": user}),
            "pause_resume": StatusObject({"is_paused": paused}),
        }
        self.adapter = TimelapseState(SimpleNamespace(lookup_object=self.objects.__getitem__))
        return self.adapter.get_status(1)

    def test_wait_preparation_capture_and_user_handoff_have_distinct_phases(self):
        cases = (
            ({}, TimelapsePhase.NONE),
            ({"waiting": True, "held": True}, TimelapsePhase.WAITING),
            ({"held": True, "paused": True}, TimelapsePhase.HELD),
            ({"held": True, "frame": True, "paused": True}, TimelapsePhase.FRAME),
            ({"held": True, "frame": True, "user": True, "paused": True}, TimelapsePhase.FRAME_USER_PAUSE),
            ({"held": True, "user": True, "paused": True}, TimelapsePhase.USER_PAUSE),
            ({"user": True}, TimelapsePhase.NONE),
            ({"paused": True}, TimelapsePhase.NONE),
        )
        for inputs, expected in cases:
            with self.subTest(inputs=inputs):
                self.assertEqual(self.status(**inputs)["phase"], expected.value)

    def test_frame_capture_keeps_print_page_and_blocks_controls_until_user_handoff(self):
        screen = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        screen.reactor = Reactor()
        screen.print_stats = StatusObject({"state": "paused", "print_duration": 20})
        screen.print_state = FEATHER.PrintState.PRINTING
        screen._timelapse_phase = lambda: TimelapsePhase.FRAME
        screen._change_print_state = mock.Mock()
        self.assertEqual(screen.page_for_print_state(), FEATHER.ScreenPage.PRINTING)
        self.assertEqual(screen._reconcile_print_state(100), "printing")
        self.assertFalse(screen._print_controls_ready())
        screen._timelapse_phase = lambda: TimelapsePhase.FRAME_USER_PAUSE
        self.assertEqual(screen.page_for_print_state(), FEATHER.ScreenPage.PAUSED)
        self.assertFalse(screen._print_controls_ready())
        screen._timelapse_phase = lambda: TimelapsePhase.USER_PAUSE
        self.assertTrue(screen._print_controls_ready())

    def test_missing_macro_or_renamed_variable_cannot_report_idle(self):
        for name, field in (("gcode_macro _TIMELAPSE_START_GUARD", "waiting"),
                            ("gcode_macro TIMELAPSE_TAKE_FRAME", "user_pause_requested")):
            with self.subTest(name=name):
                self.status()
                del self.objects[name].status[field]
                with self.assertRaisesRegex(RuntimeError, field):
                    self.adapter.get_status(1)
                del self.objects[name]
                with self.assertRaises(KeyError):
                    self.adapter.get_status(1)


class RecoveryDialogPriorityTest(unittest.TestCase):
    def setUp(self):
        self.screen = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        self.screen.renderer = FEATHER.FeatherRenderer()
        self.screen.reactor = Reactor()
        self.screen.page = FEATHER.ScreenPage.IDLE_HOME
        self.screen._paint_page = lambda: self.screen.renderer.send(
            self.screen.renderer.begin_page("Home"))
        self.screen.print_state = FEATHER.PrintState.IDLE
        self.screen.pending_action = None
        self.screen.last_action_time = 0
        self.capture = RenderCapture(self.screen.renderer)

    def test_message_and_prompt_cannot_remove_configuration_error_or_restart(self):
        self.screen._show_error("Invalid printer configuration", "error")
        error = self.screen._current_dialog_instance()
        generation = self.screen.renderer.generation
        self.assertIsNone(self.screen._show_message("Printer notice", self.screen.page))
        self.screen._handle_gcode_output("\n".join((
            "// action:prompt_begin Other operation",
            "// action:prompt_footer_button OK|DO_SOMETHING",
            "// action:prompt_show")))
        self.assertIs(self.screen._current_dialog_instance(), error)
        self.assertEqual(self.screen.renderer.generation, generation)
        self.assertTrue(self.capture.latest.has_action("error.restart"))
        self.screen._restart_klipper = mock.Mock()
        self.screen._dispatch_action("error.restart")
        self.screen._restart_klipper.assert_called_once_with("RESTART")

    def test_touch_warning_survives_notification_then_reveals_error(self):
        self.screen._show_error("Invalid printer configuration", "error")
        error = self.screen._current_dialog_instance()
        touch = self.screen._show_dialog(FEATHER.ScreenDialog.TOUCH_UNAVAILABLE, content={})
        self.assertIsNone(self.screen._show_message("Printer notice", self.screen.page))
        self.assertIs(self.screen._current_dialog_instance(), touch)
        self.screen._close_dialog(touch)
        self.assertIs(self.screen._current_dialog_instance(), error)
        self.assertTrue(self.capture.latest.has_action("error.restart"))


if __name__ == "__main__":
    unittest.main()
