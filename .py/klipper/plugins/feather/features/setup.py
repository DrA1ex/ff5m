## First-run setup: a hub of four steps over the existing settings and calibration paths.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import glob
import os
import time

from ui import Command
from ui.lazy import LazyModule
from ff5m_ui.screen import ScreenDialog, ScreenPage
from ff5m_ui import dialogs as dialog_ui
from feather.features.manager import FeatureHostProxy
from feather.screen.pagination import Pagination
from feather.settings import mod
from feather import setup as model


setup_ui = LazyModule("ff5m_ui.setup")

PAGES = (ScreenPage.SETUP, ScreenPage.SETUP_OPTIONS, ScreenPage.SETUP_CALIBRATION,
         ScreenPage.SETUP_TIMEZONE, ScreenPage.SETUP_WELCOME)

CALIBRATION_TITLES = {model.SAFE_Z_STEP: "SAFE Z", "mesh": "BED MESH", "shaper": "INPUT SHAPER"}
CALIBRATION_NOTES = {
    model.SAFE_Z_STEP: "Sets the height for safe travel over the bed.",
    "mesh": "Measures the whole bed surface. Takes a few minutes.",
    "shaper": "Reduces ringing at higher speeds.",
}
ADVISED_CALIBRATION = {model.SAFE_Z_STEP: "Calibrate Safe Z", "mesh": "Measure Bed Mesh"}

## How many cities fit one picker page, and when a region is split by letter.
CITY_COLUMNS = 3
CITY_ROWS = 4
CITY_CAPACITY = CITY_COLUMNS * CITY_ROWS


class SetupFeature(FeatureHostProxy):
    name = "setup"

    def __init__(self, host):
        super().__init__(host)
        self.progress = model.SetupProgress(
            os.path.join(os.path.dirname(self.params.filename), "feather_setup.json"),
            self.params.normalize_value)
        self.active = False
        self.return_page = ScreenPage.IDLE_HOME
        self.applying = False
        self.calibration_moved = False
        ## Where the option list stands: a group id, or the entry list of groups.
        self.options_level = model.ESSENTIALS.id
        self.options_page = 0
        ## Where the timezone picker stands: ("regions",), ("letters", region),
        ## ("cities", region, prefix) or ("fixed",).
        self.zone_level = ("regions",)
        self.zone_page = 0
        self._last_values = None
        self._camera_observed = None

    ## ------------------------------------------------------------------ entry

    def startup(self):
        if self.progress.recovery_required:
            self._toast("Setup progress needs review. Open Setup in Settings.")
        elif self.progress.data["pending"] is not None:
            self.open()
        elif not self.progress.complete and not self.progress.data["skipped"]:
            self.open(intro=True)

    def open(self, return_page=ScreenPage.IDLE_HOME, *, intro=False):
        self._require_setup_idle()
        resume = self.progress.data["pending"] == "saving"
        self.recover()
        self.active = True
        self.return_page = return_page
        if intro:
            self._show_intro("welcome")
        else:
            self._show_page(ScreenPage.SETUP_CALIBRATION if resume else ScreenPage.SETUP)
        if self.progress.data["error"]:
            self._toast(self.progress.data["error"])

    def go_to_intro(self, step):
        if step != self.progress.data["intro"]:
            self.progress.write(intro=step)
        self._show_intro(step)

    def _show_intro(self, step):
        self._host.network_parent_page = ScreenPage.SETUP_NETWORK if step == "network" else ScreenPage.MAIN_MENU
        if step == "theme":
            settings = self.feature_manager.get("settings")
            settings.open_parameter_editor(self.params.params_map["feather_theme"], ScreenPage.SETUP_NETWORK)
        elif step == "network":
            self._request_network_snapshot()
            self._show_page(ScreenPage.SETUP_NETWORK)
        else:
            self._show_page(ScreenPage.SETUP_WELCOME if step == "welcome" else ScreenPage.SETUP)

    def recover(self):
        """An interrupted operation has one known useful response each."""
        data = self.progress.data
        pending = data["pending"]
        if pending == "settings":
            actual = self.params.snapshot(model.STAGED_KEYS)
            if any(actual[key] != value for key, value in data["draft"].items()):
                self.progress.write(pending=None,
                                    error="Settings were not applied. Review the options and try again.")
            else:
                # Reaching klippy:ready means the committed configuration loaded.
                self.progress.write(pending=None, applying_values={}, draft={}, error="")
        elif pending == "applying":
            self.progress.write(pending=None, error="Applying settings was interrupted. Try again.")
        elif pending == "saving":
            status = self._config()
            step = data["running_step"]
            if (not status["save_config_pending"]
                    and model.config_fingerprint(status["config"], step) == data["expected"]):
                self.progress.record(step, True)
            else:
                self.progress.write(pending=None, running_step=None, expected=None,
                                    error="Calibration was not saved. Run it again.")
        elif pending == "running":
            self.progress.write(pending=None, running_step=None, error="")

    def leave(self, *, complete=False, skip=False):
        if self.applying:
            return
        if self.calibration_moved:
            self._require_setup_idle()
            self._run_script("M84")
            self.calibration_moved = False
        if complete:
            self.progress.finish()
        elif skip:
            self.progress.write(skipped=True)
        self.active = False
        self._show_page(self.return_page)

    def _require_setup_idle(self):
        self._require_idle()
        if self._operation_context_status().get("contexts"):
            raise RuntimeError("Finish the current operation before setup")
        message, category = self.printer.get_state_message()
        if str(category).lower() != "ready":
            raise RuntimeError(message or "Klipper is not ready")

    ## ------------------------------------------------------------- hub status

    def _config(self):
        return self.printer.lookup_object("configfile").get_status(self.reactor.monotonic())

    def _values(self):
        return dict(self.params.snapshot(model.EDITABLE_KEYS), **self.progress.data["draft"])

    def _timezone(self):
        from system_time import current_zone
        offset = time.strftime("%z")
        zone = current_zone()
        name = zone
        if zone.startswith(("UTC+", "UTC-")):
            abbreviation = time.strftime("%Z")
            name = abbreviation if abbreviation.isalpha() else ""
        return zone, name, "UTC%s:%s" % (offset[:3], offset[3:])

    def _timezone_row(self):
        _zone, name, offset = self._timezone()
        label = "%s (%s)" % (name, offset) if name else offset
        status = "%s   %s" % (time.strftime("%H:%M"), label)
        return "plain", status, "CHANGE"

    def _options_row(self, essentials):
        count = sum((key in model.ESSENTIALS.keys) == essentials for key in self.progress.data["draft"])
        if count:
            return "advised", "%d change%s awaiting restart." % (count, "" if count == 1 else "s"), "PENDING"
        if not essentials:
            return "plain", "Print preparation, protection, camera and lighting.", "REVIEW"
        recommended = {key: value for key, value in model.RECOMMENDED.items() if key in model.ESSENTIALS.keys}
        if self.params.snapshot(recommended) != recommended:
            return "plain", "Review tuning and first-print settings.", "REVIEW"
        return "done", "Your settings match the recommendations.", "READY"

    def _calibration_row(self, mesh_required):
        if mesh_required:
            return "required", "Save a bed mesh before finishing setup.", "REQUIRED"
        if all(step in self.progress.data["completed"] for step in ADVISED_CALIBRATION):
            return "done", "Safe Z and bed mesh calibrated in setup.", "DONE"
        return "plain", "Check Safe Z and bed leveling.", "REVIEW"

    def _finish_button(self, mesh_required):
        if mesh_required:
            return "NOT FINISHED", "disabled"
        return ("DONE" if self.progress.complete else "FINISH SETUP"), "selected"

    def _hub_values(self):
        state = setup_ui.HubState
        mesh_required = self._mesh_profile() is None
        summaries = (
            ("timezone", "TIME ZONE", self._timezone_row()),
            ("options", "ESSENTIAL OPTIONS", self._options_row(essentials=True)),
            ("additional", "ADDITIONAL OPTIONS", self._options_row(essentials=False)),
            ("calibration", "CALIBRATION", self._calibration_row(mesh_required)),
        )
        rows = [dict(id=step, kind=kind, title=title, status=status, chip=chip)
                for step, title, (kind, status, chip) in summaries]
        complete = self.progress.complete
        pending = bool(self.progress.data["draft"])
        action, action_state = ("APPLY & RESTART", "selected") if pending else self._finish_button(mesh_required)
        exit_label = "" if complete and not (pending or mesh_required) else (
            "CLOSE" if complete or self.progress.data["skipped"] else "I'LL SET IT UP MYSELF")
        return {state.ROWS: rows, state.EXIT: exit_label,
                state.ACTION: action, state.ACTION_STATE: action_state}

    ## ----------------------------------------------------------- option rows

    def _visible_keys(self, group, values):
        return [key for key in group.keys
                if mod.parameter_is_visible(self.params, self.params.params_map[key], values)]

    def _option_note(self, param):
        if param.key == "camera":
            return self._camera_status()
        return param.setup_description or mod.description(param)

    def _option_row(self, key, values):
        param = self.params.params_map[key]
        boolean = param.type is bool
        wanted = model.RECOMMENDED.get(key)
        advised = wanted is not None and values[key] != self.params.normalize_value(key, wanted)
        staged = key in self.progress.data["draft"]
        kind = ("toggle" if boolean else "value") + ("-advised" if advised else "")
        return dict(id=key, kind=kind, title=str(param.label).upper(),
                    note=self._option_note(param),
                    chip="PENDING" if staged else ("SUGGEST ON" if advised else ""),
                    active=mod.bool_display_active(param, values[key]) if boolean else False,
                    value=mod.display_value(self.params, param, values))

    def _options_values(self):
        from ff5m_ui.setup.common import OPTION_CAPACITY

        if self.options_level == "groups":
            state = setup_ui.OptionGroupsState
            rows = [dict(id="group:" + group.id, kind="group", title=group.label, status=group.summary)
                    for group in model.OPTION_GROUPS[1:]]
            action = "APPLY & RESTART" if self.progress.data["draft"] else "REVIEW CALIBRATION"
        else:
            state = setup_ui.OptionsState
            values = self._values()
            group = model.GROUP_BY_ID[self.options_level]
            rows = [self._option_row(key, values) for key in self._visible_keys(group, values)]
            if group == model.ESSENTIALS:
                action = "ADDITIONAL OPTIONS"
            else:
                action = "DONE" if self._next_options_level() == "groups" else "NEXT"
        items = Pagination(rows, self.options_page, OPTION_CAPACITY)
        self.options_page = items.page
        values = {state.ACTION: action,
                  state.PAGE: items.page, state.PAGE_COUNT: items.page_count,
                  state.PREVIOUS: "enabled" if items.has_previous else "disabled",
                  state.NEXT: "enabled" if items.has_next else "disabled", state.ROWS: items.visible}
        if state == setup_ui.OptionsState:
            values[state.EMPTY_MESSAGE] = ("Enable Camera in Peripherals to configure timelapse."
                                           if self.options_level == "timelapse" and not rows else "")
        return values

    def _options_title(self):
        if self.options_level == model.ESSENTIALS.id:
            return "Essential options"
        return model.GROUP_BY_ID[self.options_level].label.title()

    ## ------------------------------------------------------- calibration list

    def _mesh_profile(self):
        mesh = getattr(self, "bed_mesh", None)
        if mesh is None:
            return None
        profiles = mesh.get_status(self.reactor.monotonic()).get("profiles", {})
        if not profiles:
            return None
        status = self._config()
        config, pending = status["config"], status["save_config_pending_items"]
        # Klipper exposes a measured profile before SAVE_CONFIG persists it.
        saved = [name for name in profiles if ("bed_mesh " + name not in pending
                                               or "bed_mesh " + name in config)]
        return "auto" if "auto" in saved else next(iter(saved), None)

    def _shaper_summary(self):
        config = self._config()["config"].get("input_shaper") or {}
        axes = []
        for axis in ("x", "y"):
            name = config.get("shaper_type_" + axis)
            frequency = config.get("shaper_freq_" + axis)
            if name and frequency:
                axes.append("%s %s %s Hz" % (axis.upper(), str(name).upper(), frequency))
        return " - ".join(axes)

    def _calibration_status(self, step, profile):
        if step == model.SAFE_Z_STEP:
            return "Saved Safe Z: %.3f mm" % self._setting("safe_z", 10.0)
        if step == "mesh":
            return "Saved profile: %s" % profile if profile else "No saved mesh"
        summary = self._shaper_summary()
        return summary if summary else "Not measured"

    def _calibration_values(self):
        state = setup_ui.CalibrationState
        done = self.progress.data["completed"]
        blocked = bool(self.progress.data["draft"])
        profile = self._mesh_profile()
        rows = []
        for step in model.CALIBRATION_STEPS:
            if step == "mesh" and profile is None:
                kind, chip = "required", "REQUIRED"
            elif blocked and step not in done:
                kind, chip = "blocked", ""
            elif step in done:
                kind, chip = "done", "DONE"
            else:
                kind = "advised" if step in ADVISED_CALIBRATION else "optional"
                chip = "RECOMMENDED" if step in ADVISED_CALIBRATION else "OPTIONAL"
            rows.append(dict(id=step, kind=kind,
                             enabled=not blocked, title=CALIBRATION_TITLES[step],
                             status=self._calibration_status(step, profile),
                             note=CALIBRATION_NOTES[step], chip=chip))
        action, action_state = ("PENDING OPTIONS", "selected") if blocked else self._finish_button(profile is None)
        return {state.ROWS: rows, state.ACTION: action, state.ACTION_STATE: action_state}

    ## ----------------------------------------------------------- timezone list

    def _zone_cells(self):
        """The choices of the current level, as (value, label, note) triples."""
        from system_time import canonical_timezone, fixed_offsets, timezone_cities, timezone_letter_ranges, timezone_regions
        level = self.zone_level[0]
        if level == "regions":
            return [(region, region.upper(), None) for region in timezone_regions()], 3
        if level == "letters":
            region = self.zone_level[1]
            return [("letters:" + first + last, "%s - %s" % (first, last), None)
                    for first, last in timezone_letter_ranges(region, CITY_CAPACITY, CITY_CAPACITY)], 3
        if level == "fixed":
            from ff5m_ui.setup.common import FIXED_UTC_COLUMNS, FIXED_UTC_ROWS

            offsets = fixed_offsets()
            # Place UTC in the middle column of the upper middle row.
            center = ((FIXED_UTC_ROWS - 1) // 2) * FIXED_UTC_COLUMNS + FIXED_UTC_COLUMNS // 2
            cells = [(canonical_timezone(name), name[3:] if name != "UTC" else "UTC", None)
                     for name in offsets]
            padding = center - offsets.index("UTC")
            left, right = (padding + 1) // 2, padding // 2
            first_row = FIXED_UTC_COLUMNS - padding
            return [None] * left + cells[:first_row] + [None] * right + cells[first_row:], FIXED_UTC_COLUMNS
        region, prefix = self.zone_level[1], self.zone_level[2]
        cities = timezone_cities(region, prefix)
        return [(name, label.upper(), None) for name, label in cities], CITY_COLUMNS

    def _zone_values(self):
        from system_time import canonical_timezone
        from ff5m_ui.setup.common import FIXED_UTC_ROWS
        state = setup_ui.TimezoneState
        cells, columns = self._zone_cells()
        fixed = self.zone_level[0] == "fixed"
        capacity = columns * (FIXED_UTC_ROWS if fixed else CITY_ROWS)
        items = Pagination(cells, self.zone_page, capacity)
        self.zone_page = items.page
        shown = items.visible
        kind = "grid%d" % columns
        if self.zone_level[0] == "cities":
            kind = "cities"
            from system_time import zone_offsets
            offsets = zone_offsets([name for name, _label, _note in shown])
            shown = [(name, label, offsets.get(name)) for name, label, _note in shown]
        zone, name, offset = self._timezone()
        current = canonical_timezone(zone)
        rows = []
        for index in range(0, len(shown), columns):
            row = dict(id=str(self.zone_page) + ":" + str(index), kind=kind)
            for column in range(columns):
                cell = shown[index + column] if index + column < len(shown) else None
                row["zone%d" % column] = cell[0] if cell else ""
                row["label%d" % column] = cell[1] if cell else ""
                row["note%d" % column] = (cell[2] or "") if cell else ""
                row["state%d" % column] = ("disabled" if not cell else
                                           "selected" if cell[0] == current else "enabled")
            rows.append(row)
        actions = []
        if items.page_count > 1:
            actions.append(dict(id="pager", counter="%d / %d" % (items.page + 1, items.page_count),
                                previous="enabled" if items.has_previous else "disabled",
                                next="enabled" if items.has_next else "disabled"))
        if self.zone_level[0] == "regions":
            actions.append({"id": "fixed"})
        return {state.FIXED: fixed, state.CURRENT_NAME: tuple(name.split("/")) if name else (offset,),
                state.CURRENT_CAPTION: "CURRENT · %s" % offset if name else "CURRENT OFFSET",
                state.ACTIONS: actions, state.ROWS: rows}

    def _zone_title(self):
        level = self.zone_level[0]
        if level == "regions":
            return "Time zone"
        if level == "fixed":
            return "Fixed UTC offset"
        return "Time zone - %s" % self.zone_level[1]

    def _select_zone(self, value):
        from system_time import canonical_timezone, timezone_cities
        if value.startswith("letters:"):
            self.zone_level = ("cities", self.zone_level[1], value[len("letters:"):])
            self.zone_page = 0
        elif value == "fixed":
            self.zone_level = ("fixed",)
            self.zone_page = 0
        elif self.zone_level[0] == "regions":
            cities = timezone_cities(value)
            if len(cities) > CITY_CAPACITY:
                self.zone_level = ("letters", value)
            else:
                self.zone_level = ("cities", value, None)
            self.zone_page = 0
        else:
            self._run_script("SET_TIMEZONE ZONE=%s" % value)
            if not self.progress.complete:
                self.progress.write(time_zone=canonical_timezone(value))
            self._toast("Time zone: %s" % value)
            self._show_page(ScreenPage.SETUP)
            return
        self._show_page(ScreenPage.SETUP_TIMEZONE)

    ## --------------------------------------------------------------- options

    def _camera_status(self):
        # Re-observed, but not on every screen update: this reads the device
        # list and the service's pid file.
        now = self.reactor.monotonic()
        if self._camera_observed is not None and now - self._camera_observed[0] < 2.0:
            return self._camera_observed[1]
        status = self._observe_camera()
        self._camera_observed = (now, status)
        return status

    def _observe_camera(self):
        detected = bool(glob.glob("/sys/class/video4linux/video*"))
        try:
            with open("/run/camera.pid") as stream:
                pid = int(stream.read().strip())
            if pid <= 0:
                raise ValueError("Invalid camera PID")
            os.kill(pid, 0)
            running = True
        except (OSError, ValueError):
            running = False
        if not detected:
            return "No camera detected."
        return "Camera detected and streaming." if running else "Camera detected but not running."

    def _set_option(self, key, value):
        """Restart-only options wait for one explicit Apply; the rest take effect now."""
        value = self.params.normalize_value(key, value)
        if key in model.STAGED_KEYS:
            actual = self.params.snapshot(model.STAGED_KEYS)
            draft = dict(self.progress.data["draft"])
            if actual[key] == value:
                draft.pop(key, None)
            else:
                draft[key] = value
            # A failed effect still needs retrying even if persistence already succeeded.
            retry = dict(self.progress.data["applying_values"])
            if key in retry:
                retry[key] = value
                draft[key] = value
            self.progress.record_options({key: value}, draft=draft, applying_values=retry, error="")
            return
        changes = {key: value}
        other = {"use_kamp": "print_leveling", "print_leveling": "use_kamp"}.get(key)
        if other is not None and value and self._values()[other]:
            # The latest explicit choice owns meshing; never persist both modes enabled.
            changes[other] = 0
        self.progress.record_options(self.params.set_values(changes))
        if len(changes) > 1:
            self._toast("%s turned off" % self.params.params_map[other].label)

    def _option_help(self, key):
        param = self.params.params_map[key]
        text = param.setup_help or mod.description(param)
        if key == "camera":
            text = self._camera_status() + "\n\n" + text
        if key in model.STAGED_KEYS:
            text += "\n\nThis option is applied together with the others when you confirm the restart."
        self._show_message(text, ScreenPage.SETUP_OPTIONS, title=str(param.label))

    def _open_options(self, payload):
        if payload.startswith("group:"):
            self._show_options(payload[len("group:"):])

    def _show_options(self, level, page=0):
        self.options_level = level
        self.options_page = page
        self._show_page(ScreenPage.SETUP_OPTIONS)

    def _return_to_option_groups(self):
        from ff5m_ui.setup.common import OPTION_CAPACITY

        group = model.GROUP_BY_ID.get(self.options_level)
        additional = model.OPTION_GROUPS[1:]
        page = additional.index(group) // OPTION_CAPACITY if group in additional else 0
        self._show_options("groups", page)

    def _next_options_level(self):
        for group, following in zip(model.OPTION_GROUPS[1:], model.OPTION_GROUPS[2:]):
            if group.id == self.options_level:
                return following.id
        return "groups"

    ## ------------------------------------------------------- applying options

    def apply_settings(self):
        self._require_setup_idle()
        changes = dict(self.progress.data["draft"])
        if self._config()["save_config_pending"]:
            raise RuntimeError("Save or discard pending calibration before restarting Klipper")
        if not changes:
            return
        self.progress.write(pending="applying", applying_values=changes, error="")
        self.applying = True
        try:
            self.params.set_values(changes, notify=False)
            self.params.publish_changes()
            # These are the only setup selections with a pre-restart file effect.
            # Timing and other parameters are consumed directly at Klipper startup.
            for key in ("tune_config", "power_loss_recovery"):
                if key in changes:
                    hook = self.printer.lookup_object("gcode_shell_command setup_config")
                    hook.returncode = None
                    self._run_script('RUN_SHELL_COMMAND CMD=setup_config PARAMS="%s %s --apply-only"'
                                     % (key, changes[key]))
                    if hook.returncode != 0:
                        raise RuntimeError("Unable to apply %s; try again" % key)
            completed = self.progress.data["completed"]
            if "tune_config" in changes:
                completed = [step for step in completed if step not in ADVISED_CALIBRATION]
            self.progress.write(pending="settings", completed=completed, error="")
            command = ('RUN_SHELL_COMMAND CMD=restart_klipper PARAMS="--hard"'
                       if "tune_klipper" in changes else "RESTART")
            self._request_restart(command)
        except Exception as error:
            self.progress.write(pending=None, error=str(error))
            self._show_page(ScreenPage.SETUP_OPTIONS)
            raise
        finally:
            self.applying = False

    def _confirm_apply(self):
        changes = self.progress.data["draft"]
        if not changes:
            return
        if self._config()["save_config_pending"]:
            raise RuntimeError("Save or discard pending calibration before restarting Klipper")
        lines = ["These options only take effect after Klipper restarts:", ""]
        lines.extend("  %s: %s" % (self.params.params_map[key].label,
                                   mod.display_value(self.params, self.params.params_map[key], changes))
                     for key in changes)
        lines.append("")
        lines.append("Setup will reopen after Klipper restarts.")
        dialog = self._show_message("\n".join(lines), self.page, title="Apply and restart?",
                                    actions=((dialog_ui.OK, "NOT NOW", "enabled"),
                                             (dialog_ui.SETUP_APPLY, "APPLY & RESTART", "warning")))
        if dialog is not None:
            dialog.content["setup_confirmation"] = dict(changes)

    def confirm_dialog(self, route):
        """Handle the shared setup confirmation dialogs."""
        if not self.active:
            return False
        if route == dialog_ui.DialogRoute.SETUP_FINISH:
            return self.complete()
        if route == dialog_ui.DialogRoute.SETUP_SKIP:
            self._close_dialog(ScreenDialog.MESSAGE)
            self.leave(skip=True)
            return True
        dialog = self._find_dialog(ScreenDialog.MESSAGE)
        confirmation = dialog.content.get("setup_confirmation") if dialog is not None else None
        if confirmation is None or confirmation != self.progress.data["draft"]:
            raise RuntimeError("The selected options changed. Review them again.")
        self._close_dialog(dialog)
        self.apply_settings()
        return True

    def _request_restart(self, command):
        # Checkpoint first; execute synchronously so a rejected restart cannot
        # leave the screen latched in an endless startup overlay.
        if not self._begin_restart_ui():
            raise RuntimeError("Klipper is already restarting")
        try:
            self._run_script(command, show_notice=False)
        except Exception:
            self._cancel_restart_ui()
            raise
        self.reactor.register_callback(self._restart_timeout, self.reactor.monotonic() + 30)

    def _restart_timeout(self, eventtime):
        if not self.active or not self.restart_pending:
            return
        self._cancel_restart_ui()
        saving = self.progress.data["pending"] == "saving"
        self.progress.write(pending=None, running_step=None, expected=None,
                            error="Klipper did not restart. Review and try again.")
        self._show_page(ScreenPage.SETUP_CALIBRATION if saving else ScreenPage.SETUP_OPTIONS)
        self._show_message(self.progress.data["error"], self.page, title="Restart timed out")

    ## ------------------------------------------------------------ calibration

    def _start_calibration(self, step):
        if self.progress.data["draft"]:
            raise RuntimeError("Apply the pending options before calibrating")
        self._require_setup_idle()
        self.progress.write(pending="running", running_step=step, error="")
        try:
            if step == model.SAFE_Z_STEP:
                self.feature_manager.get("z").start_calibration()
            elif step == "mesh":
                self.feature_manager.get("calibration").start_mesh()
            else:
                self.feature_manager.get("calibration").start_shaper()
        except Exception as error:
            self.progress.write(pending=None, running_step=None, error=str(error))
            self._show_page(ScreenPage.SETUP_CALIBRATION)
            raise

    def running_step(self):
        return self.progress.data["running_step"] if self.active else None

    def calibration_started(self):
        self.calibration_moved = True

    def save_calibration(self):
        step = self.running_step()
        if step not in ("mesh", "shaper"):
            return
        self._require_idle()
        expected = model.config_fingerprint(model.effective_config(self._config()), step)
        if expected is None:
            raise RuntimeError("No calibration result to save")
        self.progress.write(pending="saving", expected=expected)
        try:
            self._request_restart("SAVE_CONFIG")
        except Exception:
            self.progress.write(pending="running", expected=None)
            raise

    def calibration_return(self, done):
        self.progress.record(self.running_step(), done)
        self._show_page(ScreenPage.SETUP_CALIBRATION)

    ## ---------------------------------------------------------------- finish

    def finish(self):
        if self.progress.data["draft"]:
            self._confirm_apply()
            return
        if self._mesh_profile() is None:
            return
        if self.progress.complete:
            self.leave()
            return
        missing = [label for step, label in ADVISED_CALIBRATION.items()
                   if step not in self.progress.data["completed"]]
        if missing:
            items = "\n".join("• " + name for name in missing)
            self._show_message(
                "You can finish later in Control > Calibration.\n\n" + items,
                self.page, title="Calibration incomplete", min_height=304,
                actions=((dialog_ui.OK, "KEEP SETTING UP", "selected"),
                         (dialog_ui.SETUP_FINISH, "EXIT WIZARD", "enabled")))
            return
        self.complete()

    def complete(self):
        dialog = self._find_dialog(ScreenDialog.MESSAGE)
        if dialog is not None:
            self._close_dialog(dialog)
        if self.progress.data["draft"] or self._mesh_profile() is None:
            return False
        self.leave(complete=True)
        self._toast("Setup complete")
        return True

    ## ------------------------------------------------------- screen protocol

    def page_content(self, page):
        if page == ScreenPage.SETUP:
            return setup_ui.HUB_PAGE, "Printer setup", False
        if page == ScreenPage.SETUP_WELCOME:
            return setup_ui.WELCOME_PAGE, "Welcome to Forge-X", False
        if page == ScreenPage.SETUP_OPTIONS:
            if self.options_level == "groups":
                return setup_ui.OPTION_GROUPS_PAGE, "Additional options", True
            return setup_ui.OPTIONS_PAGE, self._options_title(), True
        if page == ScreenPage.SETUP_CALIBRATION:
            return setup_ui.CALIBRATION_PAGE, "Calibration", True
        if page == ScreenPage.SETUP_TIMEZONE:
            return setup_ui.TIMEZONE_PAGE, self._zone_title(), True
        return None

    def _page_values(self, page):
        return {ScreenPage.SETUP: self._hub_values,
                ScreenPage.SETUP_WELCOME: dict,
                ScreenPage.SETUP_OPTIONS: self._options_values,
                ScreenPage.SETUP_CALIBRATION: self._calibration_values,
                ScreenPage.SETUP_TIMEZONE: self._zone_values}[page]()

    def write_page_values(self, page):
        values = self._page_values(page)
        tree = self.page_content(page)[0]
        tree.state.update(values)
        self._last_values = (page, values)

    def allows_action(self, page, action):
        return action == "nav.back" and page in PAGES and page not in (ScreenPage.SETUP, ScreenPage.SETUP_WELCOME)

    def resolve_semantic_action(self, page, wire_id):
        content = self.page_content(page)
        return None if content is None else content[0].resolve_action(wire_id)

    def handle_semantic_action(self, page, action):
        if not isinstance(action, Command) or not isinstance(action.key, setup_ui.SetupCommand):
            return False
        if self.applying:
            return True
        command = setup_ui.SetupCommand
        key, payload = action.key, action.payload
        if key == command.LEAVE:
            if self.progress.complete or self.progress.data["skipped"]:
                self.leave()
            else:
                self._show_message(
                    "Calibration is recommended before the first print. Bed meshes saved before installing "
                    "Forge-X may be inaccurate, so a new bed mesh is recommended.\n\n"
                    "Setup Wizard is available under Settings.",
                    self.page, title="Skip initial setup?", tone="warning",
                    actions=((dialog_ui.OK, "CONTINUE SETUP", "selected"),
                             (dialog_ui.SETUP_SKIP, "SKIP SETUP", "enabled")))
            return True
        if key == command.HELP:
            self._option_help(payload)
            return True
        if key in (command.PREVIOUS, command.NEXT):
            self._page(page, 1 if key == command.NEXT else -1)
            return True
        self._require_setup_idle()
        if key == command.OPEN:
            self._open(page, payload)
        elif key == command.TOGGLE:
            self._set_option(payload, int(not self._values()[payload]))
            self._paint_page_values()
        elif key == command.EDIT:
            self.feature_manager.get("settings").open_parameter_editor(self.params.params_map[payload], page)
        elif key == command.ZONE:
            self._select_zone(payload)
        elif key == command.ACTION:
            if page == ScreenPage.SETUP_WELCOME:
                self.go_to_intro("network")
            elif page == ScreenPage.SETUP_OPTIONS:
                if self.options_level != "groups":
                    following = self._next_options_level()
                    if following == "groups":
                        self._return_to_option_groups()
                    else:
                        self._show_options(following)
                elif self.progress.data["draft"]:
                    self._confirm_apply()
                else:
                    self._show_page(ScreenPage.SETUP_CALIBRATION)
            elif page == ScreenPage.SETUP_CALIBRATION and self.progress.data["draft"]:
                self._show_options("groups")
            else:
                self.finish()
        return True

    def _open(self, page, payload):
        if page == ScreenPage.SETUP:
            self._open_step(payload)
        elif page == ScreenPage.SETUP_CALIBRATION:
            self._start_calibration(payload)
        else:
            self._open_options(payload)

    def _open_step(self, step):
        if step == "timezone":
            self.zone_level = ("regions",)
            self.zone_page = 0
            self._show_page(ScreenPage.SETUP_TIMEZONE)
        elif step in ("options", "additional"):
            self._show_options(model.ESSENTIALS.id if step == "options" else "groups")
        else:
            self._show_page(ScreenPage.SETUP_CALIBRATION)

    def _page(self, page, delta):
        if page == ScreenPage.SETUP_TIMEZONE:
            self.zone_page += delta
        else:
            self.options_page += delta
        self._show_page(page)

    def back(self, page):
        if self.applying or page not in PAGES:
            return False
        if page == ScreenPage.SETUP_OPTIONS:
            if self.options_level in (model.ESSENTIALS.id, "groups"):
                self._show_page(ScreenPage.SETUP)
                return True
            self._return_to_option_groups()
            return True
        if page == ScreenPage.SETUP_TIMEZONE:
            level = self.zone_level[0]
            if level == "cities" and self.zone_level[2] is not None:
                self.zone_level = ("letters", self.zone_level[1])
            elif level in ("cities", "letters", "fixed"):
                self.zone_level = ("regions",)
            else:
                self._show_page(ScreenPage.SETUP)
                return True
            self.zone_page = 0
            self._show_page(ScreenPage.SETUP_TIMEZONE)
            return True
        if page == ScreenPage.SETUP_CALIBRATION:
            self._show_page(ScreenPage.SETUP)
            return True
        return True

    def update(self, eventtime):
        if (not self.active or self.applying or self.page not in PAGES
                or self.page == ScreenPage.SETUP_TIMEZONE or self._current_dialog() is not None):
            return
        values = self._page_values(self.page)
        if self._last_values != (self.page, values):
            self._paint_page_values()

    @property
    def input_blocked(self):
        return self.applying
