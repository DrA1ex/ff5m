## Common calibration feature for Feather.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import logging

from ui.lazy import LazyModule
from ff5m_ui.screen import ScreenPage
from feather.features.manager import FeatureHostProxy
from feather.screen.controls import FeatherControlsMixin


PAGES = frozenset((
    ScreenPage.CALIBRATION_HOME, ScreenPage.CALIBRATION_GUIDE,
    ScreenPage.CALIBRATION_CONFIRM, ScreenPage.CALIBRATION_PROGRESS,
    ScreenPage.CALIBRATION_RESULT,
))
calibration_screws_ui = LazyModule("ff5m_ui.calibration_screws.runtime")


class CalibrationFeature(FeatherControlsMixin, FeatureHostProxy):
    name = "calibration"

    def __init__(self, host):
        FeatureHostProxy.__init__(self, host)
        self.calibration_kind = None
        self.external_context_id = None
        self.calibration_page = 0
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

    def resolve_semantic_action(self, page, wire_id):
        if (page != ScreenPage.CALIBRATION_RESULT
                or self.calibration_kind != "screws"
                or not self.calibration_results):
            return None
        return calibration_screws_ui.PAGE.resolve_action(wire_id)

    def handle_semantic_action(self, page, action):
        del page
        if not isinstance(action, calibration_screws_ui.ScrewResultAction):
            return False
        self._handle_calibration_action(action.wire_id)
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

    def begin_external(self, frame_id, kind):
        if self.external_context_id is not None:
            return
        self.external_context_id = frame_id
        self.calibration_kind = kind
        self.calibration_repeat_probe = False
        self._prepare_calibration_progress()
        try:
            self._show_page(ScreenPage.CALIBRATION_PROGRESS)
        except Exception:
            self.external_context_id = None
            raise

    def on_operation_end(self, frame_id, outcome):
        if frame_id != self.external_context_id:
            return
        # Recovery starts the virtual SD and cleanup updates its status after
        # ending the context. Observe those effects after the command returns.
        self.reactor.register_callback(
            lambda eventtime: self._finish_external(frame_id, outcome, eventtime))

    def _finish_external(self, frame_id, outcome, eventtime):
        if frame_id != self.external_context_id:
            return
        self.external_context_id = None
        try:
            cancel_page = (self.page == ScreenPage.CANCEL_CONFIRM
                           and self.operation_cancel_return_page
                           == ScreenPage.CALIBRATION_PROGRESS)
            if (self.page != ScreenPage.CALIBRATION_PROGRESS and not cancel_page
                    or self._safety_print_active(eventtime)):
                return
            if cancel_page:
                self._reset_operation_cancel()
            self.calibration_cancelled = outcome == "cancelled"
            self.calibration_error = (
                "Operation interrupted" if outcome == "interrupted" else None)
            if self.calibration_kind == "recovery":
                status = (self.resurrection.get_status(eventtime)
                          if self.resurrection is not None else {})
                if status.get("state") == "printing":
                    return
                failed = (outcome != "completed"
                          or status.get("state") != "idle")
                self._show_message(
                    "Recovery interrupted" if failed else "Recovery data cleaned up",
                    ScreenPage.RECOVERY_PROMPT if failed else ScreenPage.IDLE_HOME)
                return
            if self.calibration_kind == "mesh" and outcome == "completed":
                self.calibration_mesh = self._read_mesh_matrix(eventtime)
            self._show_page(ScreenPage.CALIBRATION_RESULT)
        except Exception:
            logging.exception("[feather_screen] unable to finish external operation")

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
        self.external_context_id = None
        self.calibration_cancel_requested = False
        self.calibration_cancel_dispatched = False
