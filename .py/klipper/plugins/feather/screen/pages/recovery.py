## Power-loss recovery pages for Feather.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import logging

from ui import ThemeColor
from ff5m_ui.screen import ScreenPage


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
        cleanup = self.recovery_action == "cleanup"
        commands = self.renderer.begin_page("Confirm recovery", back=True)
        text = ("Cleanup will heat and home, then permanently remove recovery data."
                if cleanup else
                "Restore will heat, home and continue the interrupted print.")
        commands.append(self.renderer.text(
            400, 150, text, ThemeColor.BRIGHT, "JetBrainsMono 12pt", "center",
            "middle", max_width=640, max_height=100, wrap=True,
            truncate=True))
        commands += self.renderer.button("recovery.confirm", 220, 300, 360, 100,
                                         "CLEANUP" if cleanup else "RESTORE",
                                         state="danger" if cleanup else "enabled",
                                         font="Roboto Bold 16pt")
        self.renderer.send(commands)

    def _handle_recovery_action(self, action):
        if action == "recovery.later":
            self._run_script(
                "RESPOND TYPE=command MSG=action:prompt_end")
        elif action in ("recovery.restore", "recovery.cleanup"):
            self.recovery_action = action.split(".", 1)[1]
            self._show_page(ScreenPage.RECOVERY_CONFIRM)
        elif action == "recovery.confirm":
            command = "RESURRECT" if self.recovery_action == "restore" else "RESURRECT_ABORT"
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

    def _run_recovery(self, eventtime, command):
        try:
            self._run_script(command)
        except Exception as exc:
            logging.exception("[feather_screen] recovery failed")
            self._show_message(str(exc), ScreenPage.RECOVERY_PROMPT)
            return
        status = (self.resurrection.get_status(self.reactor.monotonic())
                  if self.resurrection is not None else {})
        if command == "RESURRECT_ABORT" and status.get("available"):
            self._show_message("Recovery cleanup failed", ScreenPage.RECOVERY_PROMPT)
        elif command == "RESURRECT_ABORT":
            self._show_message("Recovery data cleaned up", ScreenPage.IDLE_HOME)
        elif status.get("state") != "printing":
            self._show_message("Restore did not start printing", ScreenPage.RECOVERY_PROMPT)
