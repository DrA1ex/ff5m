## Lazy Klipper feature facade for the Feather on-printer regression runner.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import os
import time

from ff5m_ui.print_state import PrintState
from .runner import UITestRun


class UITestFeature:
    """Expose a UI regression run or CPU profile through the lazy feature manager."""

    name = "ui_test"

    def __init__(self, host):
        self.host = host
        self.session_id = "%d-%.6f" % (os.getpid(), time.time())
        self.current_run = None
        self.cpu_profile = None

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
        profile = self.cpu_profile
        if profile is not None and profile.running:
            try:
                allowed = self._profile_safe()
            except Exception:
                profile.stop("safety_check_failed", save=False)
            else:
                if not allowed:
                    profile.stop("printer_not_idle", save=False)
        run = self.current_run
        if run is not None:
            run.update(eventtime)

    def on_gcode_output(self, message):
        run = self.current_run
        if run is not None:
            run.on_gcode_output(message)

    # The component suite's preview page (ScreenPage.UI_TEST_PREVIEW): the run's
    # current page is the screen's content, as a product page is.
    def page_content(self, page):
        run = self.current_run
        return None if run is None else run.scenarios.preview_content()

    def write_page_values(self, page):
        run = self.current_run
        if run is not None:
            run.scenarios.write_preview_values()

    def render(self, page):
        # No run shows a page there: an empty one.
        renderer = self.host.renderer
        renderer.send(renderer.begin_page("UI test", back=False))

    def allows_action(self, page, action):
        return False

    def handle_action(self, page, action):
        return False

    def resolve_semantic_action(self, page, wire_id):
        return None

    def handle_semantic_action(self, page, action):
        return False

    def back(self, page):
        return False

    def on_render_receipt(self, receipt, eventtime):
        run = self.current_run
        if run is not None:
            run.on_render_receipt(receipt, eventtime)

    def on_print_state_changed(self, old_state, new_state, stats_state):
        if new_state != PrintState.IDLE and self.cpu_profile is not None:
            self.cpu_profile.stop("printer_state_changed", save=False)
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
        if self.cpu_profile is not None and self.cpu_profile.running:
            gcmd.respond_info("Feather CPU profile: %s" % self.cpu_profile.get_status())
            return
        run = self.current_run
        if run is None or not run.running:
            gcmd.respond_info("Feather UI test: idle")
            return
        run.respond_status(gcmd)

    def get_status(self):
        run = self.current_run
        status = {"running": False} if run is None else run.get_status()
        if self.cpu_profile is not None:
            status = dict(status)
            status["cpu_profile"] = self.cpu_profile.get_status()
        return status

    def abort(self, gcmd):
        if self.cpu_profile is not None and self.cpu_profile.running:
            self.stop_cpu_profile(gcmd, reason="aborted")
            return
        run = self.current_run
        if run is None or not run.running:
            gcmd.respond_info("Feather UI test: nothing to abort")
            return
        run.abort(gcmd)

    def run(self, gcmd, suite, material, confirm, encoded_cases="",
            screen_capture_interval=0.0, capture_screenshots=True):
        if self.cpu_profile is not None and self.cpu_profile.running:
            raise gcmd.error("A Feather CPU profile is running")
        if self.running:
            raise gcmd.error("Feather UI test is already running")
        candidate = UITestRun(
            self.host, session_id=self.session_id,
            on_finished=self._run_finished)
        candidate.run(
            gcmd, suite, material, confirm, encoded_cases,
            screen_capture_interval, capture_screenshots)
        self.current_run = candidate

    def _profile_safe(self):
        host = self.host
        eventtime = host.reactor.monotonic()
        return (host.print_state == PrintState.IDLE
                and not host._safety_print_active(eventtime)
                and not host._safety_heaters_active(eventtime)
                and not host._safety_motion_active(eventtime)
                and not host.pause_resume.get_status(eventtime).get("is_paused")
                and not getattr(host, "command_depth", 0))

    def start_cpu_profile(self, gcmd, duration):
        if self.running or self.finalizing:
            raise gcmd.error("A Feather UI test is running")
        if self.cpu_profile is not None and self.cpu_profile.running:
            raise gcmd.error("A Feather CPU profile is already running")
        from .profiling import CPUProfile
        candidate = CPUProfile(self.host.reactor, self._profile_safe)
        candidate.start(duration)
        self.cpu_profile = candidate
        gcmd.respond_info("Feather CPU profile started: %s" % candidate.directory)

    def stop_cpu_profile(self, gcmd, reason="requested"):
        profile = self.cpu_profile
        if profile is None:
            gcmd.respond_info("Feather CPU profile: idle")
            return
        if profile.running:
            try:
                allowed = self._profile_safe()
            except Exception:
                profile.stop("safety_check_failed", save=False)
            else:
                profile.stop(reason, save=allowed)
        gcmd.respond_info("Feather CPU profile: %s" % profile.get_status())

    def memory_report(self, gcmd, trim, attribute=False):
        if self.running or self.finalizing:
            raise gcmd.error("A Feather UI test is running")
        if not self._profile_safe():
            raise gcmd.error("The memory report requires an idle printer with heaters off")
        from . import memory
        result = memory.report(trim, attribute=attribute)
        path = memory.save(result)
        before = result["before"]["process"]
        gcmd.respond_info("Feather memory report: %s (anon %s kB, file %s kB, census %s)" % (
            path, before.get("RssAnon"), before.get("RssFile"), result["census"]))

    def _run_finished(self, run):
        if self.current_run is run:
            self.current_run = None

    def deactivate(self):
        if self.cpu_profile is not None:
            self.cpu_profile.stop("deactivated", save=False)
        run = self.current_run
        self.current_run = None
        if run is not None:
            run.deactivate()
