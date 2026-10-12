## Z-offset, Safe Z, paper-test, and Live Z feature for Feather.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from ff5m_ui.screen import ScreenPage
from feather.features.manager import FeatureHostProxy
from feather.screen.controls import FeatherControlsMixin
from feather.calibration.z import (
        FeatherZCalibrationMixin, ZCalibrationSession)


PAGES = frozenset((
    ScreenPage.CALIBRATION_Z, ScreenPage.Z_OFFSET_SUMMARY,
    ScreenPage.Z_OFFSET_PAPER_BRIEFING, ScreenPage.Z_OFFSET_PAPER,
    ScreenPage.SAFE_Z_BRIEFING, ScreenPage.SAFE_Z_CALIBRATION,
    ScreenPage.LIVE_Z_OFFSET,
))


def _calibration_property(name):
    """Declare one exact field shared with the calibration feature."""
    def get_value(feature):
        return getattr(feature._calibration_feature(), name)

    def set_value(feature, value):
        setattr(feature._calibration_feature(), name, value)

    return property(get_value, set_value)


class ZCalibrationFeature(FeatherZCalibrationMixin, FeatherControlsMixin,
                          FeatureHostProxy):
    name = "z"

    def __init__(self, host):
        FeatureHostProxy.__init__(self, host)
        self.z_calibration = ZCalibrationSession()
        self.live_z_step = 0.01
        self.live_z_dialog = None
        self.live_z_limit_warned = False
        self.z_weight_gauge = None

    calibration_cancel_dispatched = _calibration_property(
        "calibration_cancel_dispatched")
    calibration_cancel_requested = _calibration_property(
        "calibration_cancel_requested")
    calibration_cancelled = _calibration_property("calibration_cancelled")
    calibration_clean_nozzle = _calibration_property(
        "calibration_clean_nozzle")
    calibration_error = _calibration_property("calibration_error")
    calibration_material = _calibration_property("calibration_material")

    def _calibration_feature(self):
        return self.feature_manager.get("calibration")

    def start_calibration(self):
        self._start_z_calibration()

    def open_live_z(self):
        if not self._live_z_adjust_allowed(self.reactor.monotonic()):
            raise RuntimeError("Z adjust is not available yet")
        self.live_z_dialog = None
        self._begin_z_weight_gauge()
        self._show_page(ScreenPage.LIVE_Z_OFFSET)

    def render(self, page):
        {
            ScreenPage.CALIBRATION_Z: self._render_z_summary,
            ScreenPage.Z_OFFSET_SUMMARY: self._render_z_summary,
            ScreenPage.Z_OFFSET_PAPER_BRIEFING: self._render_z_paper_briefing,
            ScreenPage.Z_OFFSET_PAPER: self._render_z_paper,
            ScreenPage.SAFE_Z_BRIEFING: self._render_safe_z_briefing,
            ScreenPage.SAFE_Z_CALIBRATION: self._render_safe_z,
            ScreenPage.LIVE_Z_OFFSET: self._render_live_z_offset,
        }[page]()

    def allows_action(self, page, action):
        exact = {
            ScreenPage.CALIBRATION_Z: ("nav.back",),
            ScreenPage.Z_OFFSET_SUMMARY: ("nav.back",),
            ScreenPage.Z_OFFSET_PAPER_BRIEFING: ("nav.back",),
            ScreenPage.Z_OFFSET_PAPER: ("nav.back",),
            ScreenPage.SAFE_Z_BRIEFING: ("nav.back",),
            ScreenPage.SAFE_Z_CALIBRATION: ("nav.back",),
            ScreenPage.LIVE_Z_OFFSET: (
                "nav.back", "live_z.step.0005", "live_z.step.001",
                "live_z.step.005", "live_z.closer", "live_z.farther",
                "live_z.save", "live_z.warning.ok", "live_z.save.no",
                "live_z.save.yes"),
        }
        return action in exact.get(page, ())

    def handle_action(self, page, action):
        if action.startswith("live_z."):
            self._handle_live_z_action(action)
            return True
        return False

    def resolve_semantic_action(self, page, wire_id):
        semantic_page = self._semantic_ui_page()
        return (None if semantic_page is None
                else semantic_page.resolve_action(wire_id))

    def handle_semantic_action(self, page, action):
        self._dispatch_semantic_ui_action(action)
        return True

    def safety_armed_reasons(self, page, eventtime):
        return ("z-controls",) if page in PAGES else ()

    def back(self, page):
        if page not in PAGES:
            return False
        if page == ScreenPage.LIVE_Z_OFFSET:
            self.live_z_dialog = None
            self._show_page(self.page_for_print_state())
            return True

        session = self.z_calibration
        summary_pages = (ScreenPage.CALIBRATION_Z, ScreenPage.Z_OFFSET_SUMMARY)
        homed, _position = self._z_offset_head_state()
        if page in summary_pages:
            if session.results:
                session.dialog = "discard"
                self._render_z_summary()
            else:
                self._cancel_z_calibration()
            return True

        if not homed and not session.results:
            self._cancel_z_calibration()
            return True

        session.dialog = None
        if homed and page in (ScreenPage.SAFE_Z_CALIBRATION, ScreenPage.Z_OFFSET_PAPER):
            height = (self._safe_z_preparation_height()
                      if page == ScreenPage.SAFE_Z_CALIBRATION else self._safe_z())
            self._run_blocking_gcode(
                "MOVE_SAFE Z=%g ABSOLUTE=1 F=600" % height, "LIFTING Z...")

        if session.results or page in (ScreenPage.Z_OFFSET_PAPER_BRIEFING, ScreenPage.Z_OFFSET_PAPER):
            self._show_page(ScreenPage.Z_OFFSET_SUMMARY)
        elif page == ScreenPage.SAFE_Z_CALIBRATION:
            self._show_page(ScreenPage.SAFE_Z_BRIEFING)
        else:
            self._cancel_z_calibration()
        return True

    def update(self, eventtime):
        if not self._page_paint_allowed(
                ScreenPage.CALIBRATION_Z, ScreenPage.Z_OFFSET_PAPER,
                ScreenPage.LIVE_Z_OFFSET):
            return
        if (self.page == ScreenPage.LIVE_Z_OFFSET and
                self.live_z_dialog is not None):
            return
        if (self.page == ScreenPage.Z_OFFSET_PAPER and
                self.z_calibration.dialog is not None):
            return
        self._update_z_weight_status(eventtime)

    def on_print_state_changed(self, old_state, new_state, stats_state):
        if new_state.name in ("PREPARING", "PRINTING") and old_state.name not in (
                "PREPARING", "PRINTING", "PAUSED"):
            self.live_z_limit_warned = False
            self.live_z_dialog = None

    def deactivate(self):
        self.z_calibration.clear()
        self.live_z_dialog = None
