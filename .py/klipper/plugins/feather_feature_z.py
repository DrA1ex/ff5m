## Z-offset, Safe Z, paper-test, and Live Z feature for Feather.

try:
    from .ui import Page
    from .feather_feature_manager import FeatureHostProxy
    from .feather_screen_controls import FeatherControlsMixin
    from .feather_z_calibration import (
        FeatherZCalibrationMixin, ZCalibrationSession)
except (ImportError, ValueError):
    from ui import Page
    from feather_feature_manager import FeatureHostProxy
    from feather_screen_controls import FeatherControlsMixin
    from feather_z_calibration import (
        FeatherZCalibrationMixin, ZCalibrationSession)


PAGES = frozenset((
    Page.CALIBRATION_Z, Page.Z_OFFSET_SUMMARY,
    Page.Z_OFFSET_PAPER_BRIEFING, Page.Z_OFFSET_PAPER,
    Page.SAFE_Z_BRIEFING, Page.SAFE_Z_CALIBRATION,
    Page.LIVE_Z_OFFSET,
))


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

    def _calibration_feature(self):
        return self.feature_manager.get("calibration")

    def __getattr__(self, name):
        if name.startswith("calibration_"):
            return getattr(self._calibration_feature(), name)
        return FeatureHostProxy.__getattr__(self, name)

    def __setattr__(self, name, value):
        if (name.startswith("calibration_") and
                "_host" in self.__dict__):
            setattr(self._calibration_feature(), name, value)
            return
        FeatureHostProxy.__setattr__(self, name, value)

    def start_calibration(self):
        self._start_z_calibration()

    def open_live_z(self):
        if not self._live_z_adjust_allowed(self.reactor.monotonic()):
            raise RuntimeError("Z adjust is not available yet")
        self.live_z_dialog = None
        self._begin_z_weight_gauge()
        self._show_page(Page.LIVE_Z_OFFSET)

    def render(self, page):
        {
            Page.CALIBRATION_Z: self._render_z_summary,
            Page.Z_OFFSET_SUMMARY: self._render_z_summary,
            Page.Z_OFFSET_PAPER_BRIEFING: self._render_z_paper_briefing,
            Page.Z_OFFSET_PAPER: self._render_z_paper,
            Page.SAFE_Z_BRIEFING: self._render_safe_z_briefing,
            Page.SAFE_Z_CALIBRATION: self._render_safe_z,
            Page.LIVE_Z_OFFSET: self._render_live_z_offset,
        }[page]()

    def allows_action(self, page, action):
        exact = {
            Page.CALIBRATION_Z: ("nav.back",),
            Page.Z_OFFSET_SUMMARY: ("nav.back",),
            Page.Z_OFFSET_PAPER_BRIEFING: ("nav.back",),
            Page.Z_OFFSET_PAPER: ("nav.back",),
            Page.SAFE_Z_BRIEFING: ("nav.back",),
            Page.SAFE_Z_CALIBRATION: ("nav.back",),
            Page.LIVE_Z_OFFSET: (
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
        if page not in (Page.Z_OFFSET_PAPER_BRIEFING, Page.Z_OFFSET_PAPER,
                        Page.SAFE_Z_BRIEFING, Page.SAFE_Z_CALIBRATION,
                        Page.LIVE_Z_OFFSET):
            return ()
        return ("z-controls",) if self._homed_motion_available(
            eventtime) else ()

    def back(self, page):
        session = self.z_calibration
        if page in (Page.CALIBRATION_Z, Page.Z_OFFSET_SUMMARY):
            if session.results:
                session.dialog = "discard"
                self._render_z_summary()
            elif page == Page.Z_OFFSET_SUMMARY and session.active:
                self._begin_safe_z_calibration(preserve_result=True)
            else:
                self._cancel_z_calibration()
        elif page == Page.SAFE_Z_BRIEFING:
            self._cancel_z_calibration()
        elif page == Page.SAFE_Z_CALIBRATION:
            self._run_blocking_gcode(
                "MOVE_SAFE Z=%g ABSOLUTE=1 F=600" %
                self._safe_z_preparation_height(), "LIFTING Z...")
            self._show_page(Page.SAFE_Z_BRIEFING)
        elif page == Page.Z_OFFSET_PAPER_BRIEFING:
            self._show_page(Page.Z_OFFSET_SUMMARY)
        elif page == Page.Z_OFFSET_PAPER:
            session.dialog = None
            self._run_blocking_gcode(self._safe_z_move_command(), "LIFTING Z...")
            self._show_page(Page.Z_OFFSET_SUMMARY)
        elif page == Page.LIVE_Z_OFFSET:
            self.live_z_dialog = None
            self._show_page(self.page_for_print_state())
        else:
            return False
        return True

    def update(self, eventtime):
        if self.page in (Page.CALIBRATION_Z, Page.Z_OFFSET_PAPER,
                         Page.LIVE_Z_OFFSET):
            if not (self.page == Page.LIVE_Z_OFFSET and
                    self.live_z_dialog is not None) and not (
                        self.page == Page.Z_OFFSET_PAPER and
                        self.z_calibration.dialog is not None):
                self._update_z_weight_status(eventtime)

    def on_print_state_changed(self, old_state, new_state, stats_state):
        if new_state.name in ("PREPARING", "PRINTING") and old_state.name not in (
                "PREPARING", "PRINTING", "PAUSED"):
            self.live_z_limit_warned = False
            self.live_z_dialog = None

    def deactivate(self):
        self.z_calibration.clear()
        self.live_z_dialog = None
