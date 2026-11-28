## Power-loss recovery pages for Feather.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import logging

from ui import ThemeColor
from ff5m_ui.screen import ScreenPage
from ff5m_ui.print_state import PrintState


class RecoveryPagesMixin:
    def _render_recovery_prompt(self):
        status = self.recovery_status or {}
        commands = self.renderer.begin_page("Power loss recovery")
        commands.append(self.renderer.text(
            400, 90, status.get("filename", "Unknown"), ThemeColor.BRIGHT,
            "Roboto Bold 14pt", "center", max_width=720, truncate=True))
        commands.append(self.renderer.text(400, 140, "Saved progress: %.1f%%" %
                                           (float(status.get("progress", 0.0)) * 100),
                                           ThemeColor.PRIMARY, "Roboto 12pt", "center"))
        commands.append(self.renderer.text(
            400, 185, "Nozzle %.0fC   Bed %.0fC   Mesh %s" %
            (status.get("extruder_target", 0), status.get("bed_target", 0),
             status.get("mesh", "?")), ThemeColor.BRIGHT, "Roboto 10pt", "center"))
        commands += self.renderer.button("recovery.restore", 35, 285, 220, 100,
                                         "RESTORE", font="Roboto Bold 14pt")
        commands += self.renderer.button("recovery.later", 290, 285, 220, 100,
                                         "LATER", font="Roboto Bold 14pt")
        commands += self.renderer.button("recovery.cleanup", 545, 285, 220, 100,
                                         "CLEANUP", state="danger",
                                         font="Roboto Bold 14pt")
        self.renderer.send(commands)

    def _render_recovery_confirm(self):
        commands = self.renderer.begin_page("Confirm recovery", back=True)
        text = "Cleanup will heat and home, then permanently remove recovery data."
        commands.append(self.renderer.text(
            400, 150, text, ThemeColor.BRIGHT, "JetBrainsMono 12pt", "center",
            "middle", max_width=640, max_height=100, wrap=True,
            truncate=True))
        commands += self.renderer.button("recovery.confirm", 220, 300, 360, 100,
                                         "CLEANUP",
                                         state="danger",
                                         font="Roboto Bold 16pt")
        self.renderer.send(commands)

    def _handle_recovery_action(self, action):
        logging.info("[resurrection-ui] action=%s page=%s", action, self.page)
        if action == "recovery.later":
            self._run_script(
                "RESPOND TYPE=command MSG=action:prompt_end")
        elif action == "recovery.restore":
            self.recovery_action = "restore"
            self._begin_recovery_action("RESURRECT")
        elif action == "recovery.cleanup":
            self.recovery_action = "cleanup"
            self._show_page(ScreenPage.RECOVERY_CONFIRM)
        elif action == "recovery.confirm" and self.recovery_action == "cleanup":
            self._begin_recovery_action("RESURRECT_ABORT")

    def _begin_recovery_action(self, command):
        manager = getattr(self, "feature_manager", None)
        if manager is not None:
            manager.get("calibration").begin_recovery()
        else:
            self.calibration_kind = "recovery"
            self.calibration_starting_text = "STARTING..."
            self._reset_calibration_progress()
        self._show_page(ScreenPage.CALIBRATION_PROGRESS)
        self.reactor.register_callback(
            lambda eventtime, cmd=command: self._run_recovery(eventtime, cmd))

    def _recovery_ui_stopped(self):
        return (getattr(self, "shutdown_active", False)
                or getattr(self, "system_shutdown_active", False)
                or getattr(self, "restart_pending", False)
                or self.print_state in (PrintState.INACTIVE, PrintState.DESTROYED))

    def _show_recovery_result(self, message, page):
        if self._recovery_ui_stopped():
            return
        # A temporarily unavailable touch device freezes output. Apply the
        # result once it reconnects, rather than leaving the progress page.
        if self.renderer.output_frozen:
            self._pending_recovery_result = (message, page)
            return
        if self.page == ScreenPage.CALIBRATION_PROGRESS:
            self._show_page(page)
        if message is not None:
            self._show_message(message, page)

    def _flush_recovery_result(self):
        result = getattr(self, "_pending_recovery_result", None)
        if result is not None and not self.renderer.output_frozen:
            self._pending_recovery_result = None
            self._show_recovery_result(*result)

    def _finish_recovery_ui(self, command=None, error=None):
        if self._recovery_ui_stopped():
            return
        try:
            status = (self.resurrection.get_status(self.reactor.monotonic())
                      if self.resurrection is not None else {})
        except Exception as exc:
            logging.exception("[feather_screen] recovery status failed")
            status = {}
            error = error or str(exc) or exc.__class__.__name__
        state = status.get("state")
        error = getattr(self.resurrection, "last_error", None) or error
        logging.info("[resurrection-ui] finished command=%s state=%s page=%s error=%r",
                     command, state, self.page, error)

        if state == "printing" and error is None:
            self._show_recovery_result(None, ScreenPage.PRINTING)
            return
        if state == "idle" and error is None and command != "RESURRECT":
            self._show_recovery_result("Recovery data cleaned up", ScreenPage.IDLE_HOME)
            return

        fallback = ("Restore did not start printing" if command == "RESURRECT"
                    else "Recovery cleanup failed" if command == "RESURRECT_ABORT"
                    else "Recovery interrupted")
        self._show_recovery_result(
            error or fallback,
            ScreenPage.IDLE_HOME if state == "idle" else ScreenPage.RECOVERY_PROMPT)

    def _run_recovery(self, eventtime, command):
        if self._recovery_ui_stopped():
            return
        error = None
        try:
            self._run_script(command)
        except Exception as exc:
            logging.exception("[feather_screen] recovery failed")
            error = str(exc) or exc.__class__.__name__
        self._finish_recovery_ui(command=command, error=error)
