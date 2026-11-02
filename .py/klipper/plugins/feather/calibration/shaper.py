## Native input-shaper flow on the Feather calibration pages.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import math

from ui.lazy import LazyModule
from ff5m_ui.screen import ScreenDialog, ScreenPage
from ff5m_ui.shaper.actions import ShaperAction
from ff5m_ui.shaper.state import APPLICABLE_METHODS


results_ui = LazyModule("ff5m_ui.shaper.results")
progress_ui = LazyModule("ff5m_ui.shaper.progress")
start_ui = LazyModule("ff5m_ui.shaper.start")
history_ui = LazyModule("ff5m_ui.shaper.history")


def pending_shapers(pending):
    """Read selected axes from Klipper, independently of the viewed result."""
    options = (pending or {}).get("input_shaper") or {}
    selected = {}
    for axis in ("x", "y"):
        name = options.get("shaper_type_" + axis)
        try:
            frequency = float(options.get("shaper_freq_" + axis, 0))
        except (TypeError, ValueError):
            continue
        if name in APPLICABLE_METHODS and math.isfinite(frequency) and frequency > 0:
            selected[axis] = {"name": name, "freq": round(frequency, 1)}
    return selected


def confirmation_dialog(kind, selections):
    """Content for the existing Feather message dialog, with no printer calls."""
    lines = []
    for axis in ("x", "y"):
        selected = selections.get(axis)
        if selected:
            lines.append("%s: %s, %.1f Hz" % (axis.upper(), selected["name"].upper(), selected["freq"]))
        elif kind == "save":
            lines.append("%s: unchanged" % axis.upper())
    if kind == "save":
        lines.extend(("", "Klipper will restart after saving."))
        return "SAVE INPUT SHAPERS?", "\n".join(lines), (
            ("message.ok", "CANCEL", "enabled"), ("shaper.save.confirm", "SAVE & RESTART", "warning"))
    if kind != "leave":
        raise ValueError("Unknown shaper confirmation: %s" % kind)
    if selections:
        lines.insert(0, "Unsaved shaper settings:")
        lines.append("A Klipper restart will discard them.")
    else:
        lines.append("No shaper selected.")
    actions = [("shaper.leave.confirm", "EXIT", "enabled")]
    if selections:
        actions.append(("shaper.save.confirm", "SAVE & RESTART", "warning"))
    actions.append(("message.ok", "STAY", "enabled"))
    return "LEAVE SHAPER RESULTS?", "\n".join(lines), tuple(actions)


class ShaperSession:
    """Screen state of the native shaper flow.

    Measurements, the open record and staged settings stay with forge_shaper
    and Klipper's configfile; this object keeps only what the pages show.
    """

    def __init__(self, feature):
        self.feature = feature
        # Open result page and its raster cache.
        self.view = None
        # Saved records listed in place of the start page.
        self.history = None
        # Pending selections when a saved record was opened. None for a fresh
        # measurement, which always asks before leaving its results.
        self.opened_selections = None
        # Plugin counters when the screen started its job: progress and
        # results of earlier jobs are not this job's outcome.
        self.job_id, self.revision = 0, None
        self.last_progress = None

    @property
    def plugin(self):
        return self.feature._forge_shaper()

    @property
    def selections(self):
        configfile = self.feature.printer.lookup_object("configfile", None)
        if configfile is None:
            return {}
        status = configfile.get_status(self.feature.reactor.monotonic())
        return pending_shapers(status.get("save_config_pending_items"))

    def follow_new_job(self):
        plugin = self.plugin
        # A missed begin event may be reconciled after acquisition has started.
        self.job_id = plugin.job.id - int(plugin.busy)
        self.revision = plugin.revision

    def apply_job_outcome(self):
        """Prefer the plugin's outcome of this screen's job over the context outcome."""
        plugin, feature = self.plugin, self.feature
        if plugin.job.id <= self.job_id:
            return
        if plugin.state == "cancelled":
            feature.calibration_cancelled, feature.calibration_error = True, None
        elif plugin.state == "error":
            feature.calibration_error = plugin.error

    def cancel(self):
        plugin, feature = self.plugin, self.feature
        if not plugin.busy:
            return False
        feature.calibration_cancel_requested = feature.calibration_cancel_dispatched = True
        plugin.cancel()
        feature._render_screen()
        return True

    def release_view(self):
        if self.view is not None:
            self.view.plot.clear_cache()
        self.view = None

    def on_page_changed(self, new_page):
        if new_page != ScreenPage.CALIBRATION_RESULT:
            self.release_view()
        if new_page not in (ScreenPage.CALIBRATION_RESULT, ScreenPage.CALIBRATION_CONFIRM):
            self.history = None

    def deactivate(self):
        self.release_view()
        self.history = self.last_progress = None

    def render_start(self):
        renderer = self.feature.renderer
        if self.history is not None:
            renderer.send(history_ui.render(renderer, self.history))
            return
        entries = self.plugin.get_history()
        renderer.send(start_ui.render(renderer, entries[0] if entries else None))

    def _progress(self):
        plugin = self.plugin
        if plugin.busy and plugin.job.id > self.job_id:
            return progress_ui.snapshot(plugin.get_status(self.feature.reactor.monotonic()))
        return None

    def _paint_progress(self, status):
        renderer = self.feature.renderer
        renderer.send(progress_ui.render(renderer, status))
        self.last_progress = status

    def render_progress(self):
        status = self._progress()
        if status is None:
            return False
        self._paint_progress(status)
        return True

    def update_progress(self):
        status = self._progress()
        if status is None:
            return False
        if status != self.last_progress:
            self._paint_progress(status)
        return True

    def _result_plugin(self):
        """The plugin while its open record is this flow's result."""
        plugin, feature = self.plugin, self.feature
        if (plugin.state == "complete" and plugin.record is not None
                and not feature.calibration_error and not feature.calibration_cancelled
                and (self.revision is None or plugin.revision > self.revision)):
            return plugin
        return None

    def _write_view(self, plugin):
        record, reference = plugin.record, plugin.reference
        return self.view.write(record.results, self.selections, saved_at=record.saved_at,
                               reference=reference.results if reference else None,
                               reference_at=reference.saved_at if reference else None)

    def _viewed_plugin(self):
        """The plugin while the open view still shows its record."""
        plugin = self._result_plugin() if self.view is not None else None
        return plugin if plugin is not None and self.view.results is plugin.record.results else None

    def render_result(self):
        plugin = self._result_plugin()
        if plugin is None:
            return False
        if self.view is None:
            self.view = results_ui.ShaperResults()
        page = self._write_view(plugin)
        renderer = self.feature.renderer
        commands = renderer.begin_page(page.title, back=page.show_back)
        commands.extend(page.draw(renderer, page.state))
        renderer.send(commands)
        return True

    def update_result(self):
        if self._viewed_plugin() is not None and self.view.selections != self.selections:
            self.render_result()

    def resolve(self, page, wire_id):
        if page == ScreenPage.CALIBRATION_CONFIRM:
            return (history_ui if self.history is not None else start_ui).PAGE.resolve_action(wire_id)
        if page == ScreenPage.CALIBRATION_RESULT and self._viewed_plugin() is not None:
            return self.view.page.resolve_action(wire_id)
        return None

    def handle(self, page, action):
        if not isinstance(action, ShaperAction):
            return False
        if page == ScreenPage.CALIBRATION_CONFIRM:
            return self._handle_start(action)
        if page == ScreenPage.CALIBRATION_RESULT:
            return self._handle_result(action)
        return False

    def _handle_start(self, action):
        plugin, feature = self.plugin, self.feature
        if action.route == "start":
            feature._handle_calibration_action("cal.confirm")
            return True
        if plugin.busy:
            return False
        if action.route == "last":
            entries = plugin.get_history()
            if not entries:
                return False
            feature._require_idle()
            self.history = entries
            feature._render_screen()
            return True
        if action.route != "record" or self.history is None:
            return False
        if action.value not in tuple(str(i) for i in range(len(self.history))):
            return False
        feature._require_idle()
        plugin.restore_saved(self.history[int(action.value)]["id"])
        feature.calibration_error = None
        feature.calibration_cancelled = False
        feature.calibration_cancel_requested = feature.calibration_cancel_dispatched = False
        self.release_view()
        self.revision = None
        self.opened_selections = self.selections
        feature._show_page(ScreenPage.CALIBRATION_RESULT)
        return True

    def _handle_result(self, action):
        plugin, feature, view = self._viewed_plugin(), self.feature, self.view
        if plugin is None:
            return False
        if action.route == "save":
            if not view.can_save:
                return False
            feature._require_idle()
            self._show_confirmation("save")
            return True
        if action.route == "apply":
            if not view.can_apply:
                return False
            feature._require_idle()
            feature._run_script("FORGE_SHAPER_APPLY AXIS=%s SHAPER=%s" % (view.axis, view.method))
        elif not view.select(action):
            return False
        elif (view.show_table and action.route in ("method", "step", "axis")
              and view.table_page is not None and view.table_page.initialized):
            # Update the table rows in place; a full repaint would reset touch feedback.
            page, renderer = self._write_view(plugin), feature.renderer
            with renderer.compose(preserve_input=True):
                renderer.send(page.update(renderer, page.state))
            return True
        feature._render_screen()
        return True

    def back(self, page):
        if page == ScreenPage.CALIBRATION_RESULT and self.view is not None:
            if self.view.show_table:
                self.view.select(ShaperAction("view", "graph"))
                self.feature._render_screen()
                return True
            selections = self.selections
            if self.opened_selections is None or (selections and selections != self.opened_selections):
                self._show_confirmation("leave")
            else:
                self._leave_results()
            return True
        if page == ScreenPage.CALIBRATION_CONFIRM and self.history is not None:
            self.history = None
            self.feature._render_screen()
            return True
        return False

    def _leave_results(self):
        self.release_view()
        self.feature._show_page(ScreenPage.CALIBRATION_CONFIRM)

    def _show_confirmation(self, kind):
        feature, selections = self.feature, self.selections
        if kind == "save" and not selections:
            dialog = feature._find_dialog(ScreenDialog.MESSAGE)
            if dialog is not None and dialog.content.get("shaper_confirmation", {}).get("kind") == "save":
                feature._close_dialog(dialog)
            feature._render_screen()
            return
        title, message, actions = confirmation_dialog(kind, selections)
        dialog = feature._show_message(message, feature.page, title=title, actions=actions)
        if dialog is not None:
            dialog.content["shaper_confirmation"] = {"kind": kind, "selections": selections}

    def confirm(self, kind):
        """Act on a confirmed save/leave dialog if the staged settings it showed are still current."""
        feature = self.feature
        if feature.page != ScreenPage.CALIBRATION_RESULT:
            return False
        dialog = feature._find_dialog(ScreenDialog.MESSAGE)
        confirmation = dialog.content.get("shaper_confirmation") if dialog is not None else None
        if not confirmation or kind not in ("save", "leave"):
            return False
        origin = confirmation["kind"]
        save_on_exit = kind == "save" and origin == "leave" and bool(confirmation["selections"])
        if origin != kind and not save_on_exit:
            return False
        if kind == "save":
            feature._require_idle()
        if confirmation["selections"] != self.selections:
            self._show_confirmation(origin)
            return False
        if kind == "save":
            feature._restart_klipper("SAVE_CONFIG")
        else:
            feature._close_dialog(dialog)
            self._leave_results()
        return True
