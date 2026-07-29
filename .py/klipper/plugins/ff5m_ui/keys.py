## Typed page identities for Feather product pages.

from ui.identity import PageKey


class AppPage(PageKey):
    __key_namespace__ = "ui.pages.keys.AppPage"
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
