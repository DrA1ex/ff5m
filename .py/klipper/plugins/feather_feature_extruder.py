## Guided extruder rotation-distance feature for Feather.

from ff5m_ui.screen import ScreenPage
from feather_feature_manager import FeatureHostProxy
from feather_extruder_calibration import (
        ExtruderCalibrationSession, FeatherExtruderCalibrationMixin)


class ExtruderCalibrationFeature(FeatherExtruderCalibrationMixin,
                                 FeatureHostProxy):
    name = "extruder"

    def __init__(self, host):
        FeatureHostProxy.__init__(self, host)
        self.extruder_calibration = ExtruderCalibrationSession()

    def start_calibration(self):
        self._start_extruder_calibration()

    def render(self, page):
        self._render_extruder_calibration()

    def allows_action(self, page, action):
        if action == "nav.back":
            return True
        if action.startswith("extruder.material."):
            return action.rsplit(".", 1)[1] in self.cold_pull_materials
        if action == "extruder.coldpull":
            return bool(self.cold_pull_materials)
        return action.startswith("extruder.")

    def handle_action(self, page, action):
        if not action.startswith("extruder."):
            return False
        self._handle_extruder_calibration_action(action)
        return True

    def handle_immediate_action(self, page, action):
        if (action == "extruder.coldpull.cancel"
                and page == ScreenPage.EXTRUDER_CALIBRATION
                and self.extruder_calibration.phase == "cold_pull"):
            self._open_cold_pull_cancel()
            return True
        return False

    def back(self, page):
        if page != ScreenPage.EXTRUDER_CALIBRATION:
            return False
        self._cancel_extruder_calibration()
        return True

    def update(self, eventtime):
        self._poll_extruder_calibration(eventtime)

    def safety_armed_reasons(self, page, eventtime):
        return (("extruder-controls",)
                if page == ScreenPage.EXTRUDER_CALIBRATION else ())

    def deactivate(self):
        self.extruder_calibration.clear()
