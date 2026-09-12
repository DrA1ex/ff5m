## Common calibration feature for Feather.

from ff5m_ui.screen import ScreenPage
from feather_feature_manager import FeatureHostProxy
from feather_screen_controls import FeatherControlsMixin


PAGES = frozenset((
    ScreenPage.CALIBRATION_HOME, ScreenPage.CALIBRATION_GUIDE,
    ScreenPage.CALIBRATION_CONFIRM, ScreenPage.CALIBRATION_PROGRESS,
    ScreenPage.CALIBRATION_RESULT,
))


class CalibrationFeature(FeatherControlsMixin, FeatureHostProxy):
    name = "calibration"

    def __init__(self, host):
        FeatureHostProxy.__init__(self, host)
        self.calibration_kind = None
        self.calibration_page = 0
        self.calibration_guide_kind = None
        materials = getattr(host, "heating_materials", ())
        self.calibration_material = materials[0] if materials else "n/a"
        self.calibration_clean_nozzle = True
        self.calibration_repeat_probe = False
        self.calibration_results = []
        self.calibration_mesh = []
        self.calibration_error = None
        self.calibration_cancel_requested = False
        self.calibration_cancel_dispatched = False
        self.calibration_cancelled = False
        self.calibration_starting_text = "STARTING..."
        self._last_calibration_label = None
        self._last_calibration_cancel_visible = False

    def render(self, page):
        {
            ScreenPage.CALIBRATION_HOME: self._render_calibration_home,
            ScreenPage.CALIBRATION_GUIDE: self._render_calibration_guide,
            ScreenPage.CALIBRATION_CONFIRM: self._render_calibration_confirm,
            ScreenPage.CALIBRATION_PROGRESS: self._render_calibration_progress,
            ScreenPage.CALIBRATION_RESULT: self._render_calibration_result,
        }[page]()

    def allows_action(self, page, action):
        exact = {
            ScreenPage.CALIBRATION_HOME: (
                "nav.back", "cal.prev", "cal.next", "cal.z", "cal.screws",
                "cal.mesh", "cal.extruder", "cal.shaper", "cal.axes",
                "cal.pid_bed", "cal.pid_extruder"),
            ScreenPage.CALIBRATION_GUIDE: ("nav.back",),
            ScreenPage.CALIBRATION_CONFIRM: (
                "nav.back", "cal.confirm", "cal.clean.skip"),
            ScreenPage.CALIBRATION_PROGRESS: ("cal.cancel",),
            ScreenPage.CALIBRATION_RESULT: (
                "cal.repeat", "cal.done", "cal.mesh.discard",
                "cal.mesh.save", "cal.tuning.discard", "cal.tuning.save"),
        }
        return (action in exact.get(page, ()) or
                (page == ScreenPage.CALIBRATION_CONFIRM and
                 action.startswith("cal.material.") and
                 action.rsplit(".", 1)[1] in self.heating_materials))

    def handle_action(self, page, action):
        if not action.startswith("cal."):
            return False
        self._handle_calibration_action(action)
        return True

    def _start_z_calibration(self):
        self.feature_manager.get("z").start_calibration()

    def _start_extruder_calibration(self):
        self.feature_manager.get("extruder").start_calibration()

    def back(self, page):
        if page == ScreenPage.CALIBRATION_HOME:
            self._show_page(ScreenPage.CONTROL_HOME)
        elif page == ScreenPage.CALIBRATION_GUIDE:
            self._show_page(ScreenPage.CALIBRATION_HOME)
        elif page == ScreenPage.CALIBRATION_CONFIRM:
            self._show_page(ScreenPage.CALIBRATION_HOME)
        else:
            return False
        return True

    def begin_recovery(self):
        self.calibration_kind = "recovery"
        self.calibration_starting_text = "STARTING..."
        self._reset_calibration_progress()

    def update(self, eventtime):
        if self._page_paint_allowed(ScreenPage.CALIBRATION_PROGRESS):
            self._update_calibration_progress()

    def on_gcode_output(self, message):
        if (self.calibration_kind == "screws" and
                self.page == ScreenPage.CALIBRATION_PROGRESS):
            result = self.parse_screw_result(message)
            if result:
                self.calibration_results.append(result)

    def handle_immediate_action(self, page, action):
        if (action == "cal.cancel" and
                page == ScreenPage.CALIBRATION_PROGRESS and
                self.calibration_kind in ("screws", "mesh", "z")):
            self._open_calibration_cancel()
            return True
        return False

    def safety_armed_reasons(self, page, eventtime):
        return (("calibration-controls",)
                if page == ScreenPage.CALIBRATION_PROGRESS else ())

    def deactivate(self):
        self.calibration_cancel_requested = False
        self.calibration_cancel_dispatched = False
