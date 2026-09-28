## Typed page identities for Feather product pages.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from ui.identity import PageKey


class AppPage(PageKey):
    __key_namespace__ = "ui.pages.keys.AppPage"
    SCREEN_ROOT = "screen.root"
    HOME = "home.dashboard"
    PRINTING = "printing.active"
    HEAT = "heat.control"
    FILAMENT_MATERIAL = "filament.material"
    FILAMENT_ACTION = "filament.action"
    MOVE_STEP = "move.step"
    MOVE_JOYSTICK = "move.joystick"
    Z_OFFSET_SUMMARY = "z_offset.summary"
    Z_OFFSET_PAPER_BRIEFING = "z_offset.paper_briefing"
    Z_OFFSET_PAPER = "z_offset.paper"
    SAFE_Z_BRIEFING = "z_offset.safe_briefing"
    SAFE_Z_CALIBRATION = "z_offset.safe"
    CALIBRATION_SCREWS_RESULT = "calibration.screws.result"
    RENDER_BENCHMARK = "benchmark.render"
    COMPONENT_BENCHMARK = "benchmark.components"
