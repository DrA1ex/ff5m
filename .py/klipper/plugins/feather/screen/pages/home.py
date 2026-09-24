## Home and control-menu pages for Feather.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import os
import time

from ui.lazy import LazyModule
from ff5m_ui.screen import ScreenPage


home_page = LazyModule("ff5m_ui.home.page")
home_state = LazyModule("ff5m_ui.home.state")


class HomePagesMixin:
    def _render_home(self):
        return home_page.render(self)

    def _render_main_menu(self):
        self._component_benchmark_taps = 0
        self._component_benchmark_deadline = 0.0
        commands = self.renderer.begin_page("MAIN MENU", back=True)
        commands.append(self.renderer.action_hitbox(
            "menu.component_benchmark.tap", 170, 7, 450, 46))
        tiles = (("nav.files", 22, 82, "PRINT FILES"),
                 ("nav.control", 410, 82, "CONTROL"),
                 ("nav.filament", 22, 242, "FILAMENT"),
                 ("nav.network", 410, 242, "NETWORK"))
        for action, x, y, label in tiles:
            commands += self.renderer.button(action, x, y, 368, 138, label,
                                             font="JetBrainsMono 12pt")
        self.renderer.send(commands)

    def _handle_component_benchmark_tap(self):
        self._require_idle()
        now = self.reactor.monotonic()
        if now > self._component_benchmark_deadline:
            self._component_benchmark_taps = 0
        self._component_benchmark_taps += 1
        self._component_benchmark_deadline = now + 2.0
        if self._component_benchmark_taps == 5:
            self._component_benchmark_taps = 0
            self._show_page(ScreenPage.COMPONENT_BENCHMARK)

    def _update_dashboard(self, eventtime):
        return home_page.update(self, eventtime)

    def _dashboard_job(self, eventtime):
        return home_state.dashboard_job(self, eventtime)

    def _refresh_local_timezone(self):
        """Reload libc timezone data after SET_TIMEZONE replaces localtime."""
        try:
            stat_result = os.lstat("/etc/localtime")
            signature = (
                stat_result.st_ino, stat_result.st_mtime,
                os.readlink("/etc/localtime")
                if os.path.islink("/etc/localtime") else "")
        except OSError:
            signature = None
        if signature == getattr(self, "_timezone_signature", object()):
            return
        if hasattr(time, "tzset"):
            time.tzset()
        self._timezone_signature = signature

    def _render_control_home(self):
        commands = self.renderer.begin_page("Control menu", back=True)
        tiles = (("nav.move", 22, 82, "MOVE", "selected"),
                 ("nav.heat", 410, 82, "HEAT / FAN", "warning"),
                 ("nav.calibration", 22, 242, "CALIBRATION", "enabled"),
                 ("nav.settings", 410, 242, "SETTINGS", "enabled"))
        for action, x, y, label, state in tiles:
            commands += self.renderer.button(action, x, y, 368, 138, label,
                                             state=state,
                                             font="JetBrainsMono 12pt")
        self.renderer.send(commands)
