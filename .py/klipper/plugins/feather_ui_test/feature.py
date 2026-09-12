## Lazy Klipper feature facade for the Feather on-printer regression runner.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import os
import time

from .runner import UITestRun


class UITestFeature:
    """Expose one current test run to Feather's lazy feature manager."""

    name = "ui_test"

    def __init__(self, host):
        self.host = host
        self.session_id = "%d-%.6f" % (os.getpid(), time.time())
        self.current_run = None

    @property
    def running(self):
        return bool(self.current_run is not None
                    and self.current_run.running)

    @property
    def finalizing(self):
        return bool(self.current_run is not None
                    and self.current_run.finalizing)

    @property
    def input_blocked(self):
        return bool(self.current_run is not None
                    and self.current_run.input_blocked)

    @property
    def theme_update_blocked(self):
        return bool(self.current_run is not None
                    and self.current_run.theme_update_blocked)

    def blocks_action(self, action):
        return bool(self.current_run is not None
                    and self.current_run.blocks_action(action))

    def handle_immediate_action(self, page, action):
        run = self.current_run
        return False if run is None else run.handle_immediate_action(
            page, action)

    def update(self, eventtime):
        run = self.current_run
        if run is not None:
            run.update(eventtime)

    def on_gcode_output(self, message):
        run = self.current_run
        if run is not None:
            run.on_gcode_output(message)

    def on_render_receipt(self, receipt, eventtime):
        run = self.current_run
        if run is not None:
            run.on_render_receipt(receipt, eventtime)

    def on_print_state_changed(self, old_state, new_state, stats_state):
        run = self.current_run
        if run is not None:
            run.on_print_state_changed(old_state, new_state, stats_state)

    def safety_active_reasons(self, eventtime):
        run = self.current_run
        return () if run is None else run.safety_active_reasons(eventtime)

    def safety_armed_reasons(self, page, eventtime):
        run = self.current_run
        return () if run is None else run.safety_armed_reasons(
            page, eventtime)

    def respond_status(self, gcmd):
        run = self.current_run
        if run is None or not run.running:
            gcmd.respond_info("Feather UI test: idle")
            return
        run.respond_status(gcmd)

    def get_status(self):
        run = self.current_run
        if run is None:
            return {"running": False}
        return run.get_status()

    def abort(self, gcmd):
        run = self.current_run
        if run is None or not run.running:
            gcmd.respond_info("Feather UI test: nothing to abort")
            return
        run.abort(gcmd)

    def run(self, gcmd, suite, material, confirm, encoded_cases="",
            screen_capture_interval=0.0):
        if self.running:
            raise gcmd.error("Feather UI test is already running")
        candidate = UITestRun(
            self.host, session_id=self.session_id,
            on_finished=self._run_finished)
        candidate.run(
            gcmd, suite, material, confirm, encoded_cases,
            screen_capture_interval)
        self.current_run = candidate

    def _run_finished(self, run):
        if self.current_run is run:
            self.current_run = None

    def deactivate(self):
        run = self.current_run
        self.current_run = None
        if run is not None:
            run.deactivate()
