## Guided extruder rotation-distance feature for Feather.

try:
    from .ui import Page
    from .feather_feature_manager import FeatureHostProxy
    from .feather_extruder_calibration import (
        ExtruderCalibrationSession, FeatherExtruderCalibrationMixin)
except (ImportError, ValueError):
    from ui import Page
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
        return action == "nav.back" or action.startswith("extruder.")

    def handle_action(self, page, action):
        if not action.startswith("extruder."):
            return False
        self._handle_extruder_calibration_action(action)
        return True

    def back(self, page):
        if page != Page.EXTRUDER_CALIBRATION:
            return False
        self._cancel_extruder_calibration()
        return True

    def update(self, eventtime):
        self._poll_extruder_calibration(eventtime)

    def deactivate(self):
        self.extruder_calibration.clear()
