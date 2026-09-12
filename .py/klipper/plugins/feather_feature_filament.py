"""Lazy declarative filament workflow for Feather."""

import logging

from ui import Command
from feather_feature_manager import FeatureHostProxy
from ff5m_ui.filament import runtime as filament_ui
from ff5m_ui.screen import ScreenPage


PAGES = frozenset((ScreenPage.FILAMENT_MATERIAL, ScreenPage.FILAMENT_ACTION))


class FilamentFeature(FeatureHostProxy):
    name = "filament"

    def __init__(self, host):
        FeatureHostProxy.__init__(self, host)
        self._last_signature = None
        self._selected_target = None
        self._cooling_fan_active = False

    def _profiles(self):
        return tuple(
            (material, self._host._limited_preheat(material)[0])
            for material in self._host.heating_materials)

    def _values(self, eventtime):
        status = self._host.extruder.get_status(eventtime)
        temperature = float(status.get("temperature", 0.0))
        target = (float(self._selected_target)
                  if self._selected_target is not None
                  else float(status.get("target", 0.0)))
        effective_status = dict(status)
        effective_status["target"] = target
        cooling = target > 0.0 and temperature > target + 5.0
        ready = self._host._filament_temperature_ready(effective_status)
        values = {
            filament_ui.FilamentState.TEMPERATURE: temperature,
            filament_ui.FilamentState.TARGET: target,
            filament_ui.FilamentState.MATERIAL:
                str(self._host.filament_material),
            filament_ui.FilamentState.READY: ready,
            filament_ui.FilamentState.COOLING: cooling,
        }
        signature = (
            round(values[filament_ui.FilamentState.TEMPERATURE], 1),
            round(values[filament_ui.FilamentState.TARGET]),
            values[filament_ui.FilamentState.MATERIAL], ready, cooling,
        )
        return status, values, signature

    def _set_cooling_fan(self, enabled, best_effort=False):
        enabled = bool(enabled)
        if enabled == self._cooling_fan_active:
            return
        if getattr(self._host, "fan", None) is None:
            self._cooling_fan_active = False
            return
        command = ("SET_FAN_SPEED FAN=fanM106 SPEED=1.00" if enabled
                   else "SET_FAN_SPEED FAN=fanM106 SPEED=0.00")
        previous = self._cooling_fan_active
        # Mark the transition before dispatch: the controller may redraw the
        # current page while serializing G-code, and that redraw must not
        # recursively dispatch the same fan command.
        self._cooling_fan_active = enabled
        try:
            self._host._run_script(command)
        except Exception:
            self._cooling_fan_active = previous
            if not best_effort:
                raise
            logging.exception(
                "[feather_screen] unable to stop filament cooling fan")
            return

    def _sync_cooling_fan(self, status):
        if self._selected_target is None:
            self._set_cooling_fan(False)
            return
        temperature = float(status.get("temperature", 0.0))
        self._set_cooling_fan(
            temperature > float(self._selected_target) + 5.0)

    def _leave_workflow(self, best_effort=False):
        self._set_cooling_fan(False, best_effort=best_effort)
        self._selected_target = None

    def _semantic_page(self, page):
        if page == ScreenPage.FILAMENT_MATERIAL:
            return filament_ui.get_material_page(self._profiles())
        if page == ScreenPage.FILAMENT_ACTION:
            return filament_ui.get_action_page(
                self._host.filament_from_pause)
        return None

    def render(self, page):
        if page == ScreenPage.FILAMENT_MATERIAL:
            commands = self._host.renderer.begin_page(
                "Select material", back=True)
            commands += filament_ui.render_material(
                self._host.renderer, self._profiles())
            self._last_signature = None
        else:
            _status, values, self._last_signature = self._values(
                self._host.reactor.monotonic())
            commands = self._host.renderer.begin_page(
                "Filament - %s" % self._host.filament_material, back=True)
            commands += filament_ui.render_action(
                self._host.renderer, self._host.filament_from_pause, values)
        self._host.renderer.send(commands)

    def allows_action(self, page, action):
        return action == "nav.back"

    def handle_action(self, page, action):
        return False

    def resolve_semantic_action(self, page, wire_id):
        semantic_page = self._semantic_page(page)
        return (None if semantic_page is None
                else semantic_page.resolve_action(wire_id))

    def handle_semantic_action(self, page, action):
        if not isinstance(action, Command):
            raise KeyError("Unsupported filament action: %s" % action)
        if action.key == filament_ui.FilamentCommand.SELECT:
            target = self._host._limited_preheat(action.payload)[0]
            self._selected_target = float(target)
            try:
                self._host._handle_filament_action(
                    "filament.%s" % action.payload)
                self._sync_cooling_fan(self._host.extruder.get_status(
                    self._host.reactor.monotonic()))
            except Exception:
                self._leave_workflow(best_effort=True)
                raise
            return True
        legacy = filament_ui.LEGACY_ACTIONS.get(action.key)
        if legacy is None:
            raise KeyError("Unsupported filament command: %s" % action.key)
        if action.key in (filament_ui.FilamentCommand.DONE,
                          filament_ui.FilamentCommand.RESUME):
            self._leave_workflow()
        self._host._handle_filament_action(legacy)
        return True

    def back(self, page):
        if page == ScreenPage.FILAMENT_ACTION:
            # Returning to material choice is presentation-only. The selected
            # target stays active until the user finishes or leaves the flow.
            self._host._show_page(ScreenPage.FILAMENT_MATERIAL)
        elif page == ScreenPage.FILAMENT_MATERIAL:
            self._leave_workflow()
            if self._host.filament_from_pause:
                self._host._show_page(self._host.page_for_print_state())
            else:
                self._host._show_page(getattr(
                    self._host, "filament_return_page", ScreenPage.MAIN_MENU))
        else:
            return False
        return True

    def update(self, eventtime):
        if self._host.page not in PAGES:
            self._leave_workflow(best_effort=True)
            return
        status, values, signature = self._values(eventtime)
        self._sync_cooling_fan(status)
        if not self._host._page_paint_allowed(ScreenPage.FILAMENT_ACTION):
            return
        if signature == self._last_signature:
            return
        self._last_signature = signature
        commands = filament_ui.update_action(
            self._host.renderer, self._host.filament_from_pause, values)
        if commands:
            self._host.renderer.send(commands)

    def deactivate(self):
        self._leave_workflow(best_effort=True)
        self._last_signature = None
