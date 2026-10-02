## Print actions and cancellation pages for Feather.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import logging

from timelapse_state import TimelapsePhase
from ui import ThemeColor
from ff5m_ui.screen import ScreenPage


class PrintCancelPagesMixin:
    cmd_FEATHER_ABORT_help = "Request cancellation of the active operation"

    def _request_print_cancel(self, confirmed=False):
        """Cancel the active print; ``confirmed`` skips the confirmation step."""
        eventtime = self.reactor.monotonic()
        state = self.print_stats.get_status(eventtime)["state"]
        if state not in ("printing", "paused"):
            return
        # CANCEL PRINT on the dedicated wait page is already an explicit choice.
        confirmed = confirmed or self.page == ScreenPage.TIMELAPSE_WAIT
        phase = self._timelapse_phase()
        operation = self._operation_context_status(eventtime)
        held = phase in (TimelapsePhase.WAITING, TimelapsePhase.HELD)

        # A held file has no cooperative cancel point: CANCEL_PRINT releases it.
        if held and not operation["contexts"]:
            if confirmed:
                self._cancel_held_print()
            else:
                self._show_message(
                    ("Cancel this print while it waits for the previous timelapse?"
                     if phase == TimelapsePhase.WAITING
                     else "Cancel this print during preparation?"),
                    self.page,
                    actions=(("timelapse.wait.cancel", "CANCEL PRINT", "danger"),
                             ("message.ok", "GO BACK", "enabled")),
                    title="Cancel print?")
            return

        return_page = self.page if held else self.page_for_print_state()
        self._open_operation_cancel(return_page,
                                    self._accept_print_operation_cancel,
                                    self._clear_print_operation_cancel)
        # The cancel page still shows progress for an already confirmed request.
        if confirmed and operation["cancel_available"]:
            self._handle_operation_cancel_action("operation.cancel.confirm")

    def _cancel_held_print(self):
        self._mark_print_cancel_pending()
        if self.page == ScreenPage.TIMELAPSE_WAIT:
            self._show_page(self.page)
        try:
            self._run_script("CANCEL_PRINT", show_notice=False)
        except Exception:
            self._clear_print_cancel_pending()
            raise
        self._reconcile_print_action()

    def cmd_FEATHER_ABORT(self, gcmd):
        """Request cooperative cancellation outside the G-code mutex."""
        result = self._request_operation_cancel()
        if result["accepted"]:
            gcmd.respond_raw(
                "Feather cancellation requested: %s"
                % (result["target_name"],))
            if self._temperature_wait_active():
                self._run_immediate_command("M108")
        else:
            gcmd.respond_raw(
                "The active operation cannot be cancelled safely")

    def _handle_print_action(self, action):
        stats = self.print_stats.get_status(self.reactor.monotonic())["state"]
        phase = self._timelapse_phase()
        if action == "print.cancel":
            self._request_print_cancel()
            return
        stats = phase.print_state(stats)
        if action == "print.resume":
            if phase in (TimelapsePhase.WAITING, TimelapsePhase.HELD):
                self._toast("Print preparation is in progress")
                return
            if phase in (TimelapsePhase.FRAME, TimelapsePhase.FRAME_USER_PAUSE):
                self._toast("Timelapse frame is still being captured")
                return
        if action in ("print.pause", "print.filament"):
            if not self._print_controls_ready():
                self._toast("Available after print preparation")
                return
        if action == "print.pause" and stats == "printing":
            self.pending_action = action
            self.pending_until = self.reactor.monotonic() + 10.0
            self._render_print_page()
            try:
                self._run_script("PAUSE")
            except Exception:
                self.pending_action = None
                raise
            self._reconcile_print_action()
        elif action == "print.resume" and stats == "paused":
            self.pending_action = action
            self.pending_until = self.reactor.monotonic() + 10.0
            self._render_print_page()
            try:
                self._run_blocking_gcode("RESUME", "RESUMING PRINT...")
            except Exception:
                self.pending_action = None
                raise
            self._reconcile_print_action()
        elif action == "print.filament" and stats in ("printing", "paused"):
            if stats == "printing":
                self._filament_request_token = getattr(
                    self, "_filament_request_token", 0) + 1
                token = self._filament_request_token
                self._run_script("PAUSE")
                current = self.print_stats.get_status(
                    self.reactor.monotonic())["state"]
                if (token != self._filament_request_token
                        or current != "paused"
                        or self.cancel_requested
                        or self.page not in (ScreenPage.PRINTING, ScreenPage.PAUSED)):
                    logging.info(
                        "[feather_screen] stale filament request discarded "
                        "token=%s current=%s page=%s cancel=%s",
                        token, current, self.page.name, self.cancel_requested)
                    return
            self._open_filament(True)
        elif action == "print.z" and stats in ("printing", "paused"):
            manager = getattr(self, "feature_manager", None)
            if manager is not None:
                manager.get("z").open_live_z()
            else:
                if not self._live_z_adjust_allowed(self.reactor.monotonic()):
                    raise RuntimeError("Z adjust is not available yet")
                self.live_z_dialog = None
                self._begin_z_weight_gauge()
                self._show_page(ScreenPage.LIVE_Z_OFFSET)
    def _timelapse_status(self):
        return self.timelapse_state.get_status(self.reactor.monotonic())

    def _timelapse_phase(self):
        return TimelapsePhase(self._timelapse_status()["phase"])

    def _reconcile_print_action(self):
        """Show the state reached by a completed operation immediately."""
        eventtime = self.reactor.monotonic()
        state = self._reconcile_print_state(eventtime)
        self._reconcile_pending_action(
            eventtime, state, self.virtual_sdcard.is_active())

    def _open_operation_cancel(
            self, return_page, on_accept=None, on_clear=None):
        operation = self._operation_context_status()
        self.operation_cancel_on_accept = on_accept
        self.operation_cancel_on_clear = on_clear
        self.operation_cancel_request_id = None
        self.operation_cancel_target_name = (
            operation.get("cancel_target_name")
            or operation.get("cancel_blocker_name")
            or (operation.get("context_path") or (None,))[-1])
        self.operation_cancel_target_mode = operation.get(
            "cancel_target_mode")
        self.cancel_mode = ("confirm" if operation["cancel_available"]
                            else "not_cancelable")
        self.operation_cancel_return_page = return_page
        self._show_page(ScreenPage.OPERATION_CANCEL)

    def _close_operation_cancel(self):
        if getattr(self, "cancel_mode", None) == "pending":
            return
        return_page = getattr(self, "operation_cancel_return_page", None)
        self._reset_operation_cancel()
        if self.page == ScreenPage.OPERATION_CANCEL:
            if return_page == ScreenPage.TIMELAPSE_WAIT:
                return_page = self.page_for_print_state()
            self._show_page(return_page or ScreenPage.IDLE_HOME)

    def _reset_operation_cancel(self):
        self.cancel_mode = None
        self.operation_cancel_on_accept = None
        self.operation_cancel_on_clear = None
        self.operation_cancel_request_id = None
        self.operation_cancel_target_name = None
        self.operation_cancel_target_mode = None
        self.operation_cancel_return_page = None

    def _handle_operation_cancel_action(self, action):
        if action == "operation.cancel.back":
            self._close_operation_cancel()
            return
        if (action == "operation.cancel.continue"
                and self.cancel_mode == "pending"):
            result = self._clear_operation_cancel_request()
            if not result.get("cleared", False):
                # Losing this race is normal: the safe point may already have
                # taken the request. Repainting alone looked like a dead button.
                self._render_cancel_confirm()
                self._toast("CANCELLATION ALREADY STARTED")
                return
            callback = self.operation_cancel_on_clear
            if callback is not None:
                callback(result)
            self.cancel_mode = "cleared"
            self._close_operation_cancel()
            return
        if action != "operation.cancel.confirm" or self.cancel_mode != "confirm":
            return
        result = self._request_operation_cancel()
        if not result["accepted"]:
            self.cancel_mode = "not_cancelable"
            self.operation_cancel_target_name = (
                result.get("blocker_name") or result.get("target_name")
                or self.operation_cancel_target_name)
            self._render_cancel_confirm()
            return
        self.cancel_mode = "pending"
        self.operation_cancel_request_id = result.get("request_id")
        self.operation_cancel_target_name = result.get("target_name")
        self.operation_cancel_target_mode = result.get("target_mode")
        # on_accept may block for seconds on the G-code mutex a running print
        # holds, so paint first. An interrupted wait ends immediately and may
        # close this page instead, so that path paints after the dispatch.
        interrupting_wait = self._temperature_wait_active()
        if not interrupting_wait:
            self._render_cancel_confirm()
        callback = self.operation_cancel_on_accept
        if callback is not None:
            callback(result)
        if interrupting_wait:
            self._run_immediate_command("M108")
            if (self.page == ScreenPage.OPERATION_CANCEL
                    and self.cancel_mode == "pending"):
                self._render_cancel_confirm()

    def _mark_print_cancel_pending(self):
        self._filament_request_token = getattr(
            self, "_filament_request_token", 0) + 1
        self.pending_action = "print.cancel.confirm"
        self.pending_until = self.reactor.monotonic() + 30.0
        self.cancel_requested = True
        self.cancel_waiting_for_heat = self._temperature_wait_active()

    def _clear_print_cancel_pending(self):
        self.pending_action = None
        self.cancel_requested = False
        self.cancel_waiting_for_heat = False

    def _accept_print_operation_cancel(self, result):
        del result
        self._mark_print_cancel_pending()
        started = bool(getattr(
            getattr(self, "start_print_macro", None), "variables", {}
        ).get("print_started", False))
        if started and not self.cancel_waiting_for_heat:
            try:
                self._run_script("_CONTEXT_CANCEL_POINT")
            except Exception as exc:
                # Successful cooperative interruption also raises. The manager
                # reports cleanup failure separately; print state confirms stop.
                logging.info("[feather_screen] cancellation dispatch ended: %s", exc)
                error = self._operation_context_status().get("cancel_error")
                if error and self.pending_action == "print.cancel.confirm":
                    self._show_print_cancel_failure(error)
            self._reconcile_print_action()

    def _show_print_cancel_failure(self, error):
        eventtime = self.reactor.monotonic()
        state = self.print_stats.get_status(eventtime).get("state")
        stopped = (state in ("cancelled", "complete", "error", "standby")
                   and not self.virtual_sdcard.is_active())
        self._clear_print_cancel_pending()
        self._reset_operation_cancel()
        self._reconcile_print_state(eventtime)
        self._show_message(
            str(error) + ("" if stopped else "\nThe print is still active. Use ABORT to stop it."),
            self.page_for_print_state(),
            title="Print stopped; cleanup failed" if stopped else "Print cancellation failed")

    def _clear_print_operation_cancel(self, result):
        del result
        self._clear_print_cancel_pending()

    def _render_cancel_confirm(self):
        target = str(getattr(
            self, "operation_cancel_target_name", None) or "operation")
        interrupt = (getattr(
            self, "operation_cancel_target_mode", None) == "interruptible")
        if self.cancel_mode == "not_cancelable":
            commands = self.renderer.begin_page("Cannot cancel safely")
            commands.append(self.renderer.text(
                400, 175, "THIS OPERATION HAS NO SAFE CANCEL POINT",
                ThemeColor.WARNING, "JetBrainsMono Bold 12pt", "center",
                "middle", max_width=720, truncate=True))
            commands.append(self.renderer.text(
                400, 225, "ABORT STOPS THE PRINTER IMMEDIATELY (M112)",
                ThemeColor.DIM, "JetBrainsMono 8pt", "center", "middle"))
            commands += self.renderer.button(
                "operation.cancel.back", 100, 285, 260, 100,
                "CONTINUE", font="Roboto Bold 16pt")
            commands += self.renderer.button(
                "operation.cancel.force", 440, 285, 260, 100,
                "ABORT NOW", state="danger", font="Roboto Bold 16pt")
            self.renderer.send(commands)
            return
        if self.cancel_mode == "pending":
            label = self._cancel_progress_label()
            commands = self.renderer.begin_page(
                "%s %s" % (
                    "INTERRUPTING" if interrupt else "CANCELLING",
                    target.upper()))
            commands.append(self.renderer.text(
                400, 170, label, ThemeColor.WARNING,
                "JetBrainsMono Bold 16pt", "center", "middle",
                max_width=700, truncate=True))
            commands.append(self.renderer.text(
                400, 225,
                ("INTERRUPT REQUEST ACCEPTED" if interrupt
                 else "CANCEL REQUEST ACCEPTED"),
                ThemeColor.PRIMARY,
                "JetBrainsMono 12pt", "center", "middle"))
            commands += self.renderer.button(
                "operation.cancel.continue", 85, 285, 290, 62,
                "CONTINUE OPERATION", font="JetBrainsMono Bold 8pt")
            commands += self.renderer.button(
                "operation.cancel.force", 425, 285, 290, 62,
                "ABORT NOW (M112)", state="danger",
                font="JetBrainsMono Bold 8pt")
            loader_y = 385
            for index in range(5):
                commands.append(self.renderer.fill(
                    290 + index * 48, loader_y, 32, 12,
                    ThemeColor.PRIMARY if index == self.busy_phase % 5 else ThemeColor.MUTED))
            self.renderer.send(commands)
            self._last_cancel_label = label
            return
        commands = self.renderer.begin_page(
            "%s %s?" % (
                "Interrupt" if interrupt else "Cancel", target))
        commands.append(self.renderer.text(400, 170,
                                           "The operation will stop at a safe point",
                                           ThemeColor.WARNING, "Roboto 16pt", "center", "middle"))
        commands += self.renderer.button("operation.cancel.back", 100, 285, 260, 100,
                                         "GO BACK", font="Roboto Bold 16pt")
        commands += self.renderer.button("operation.cancel.confirm", 440, 285, 260, 100,
                                         "INTERRUPT" if interrupt else "CANCEL",
                                         state="danger",
                                         font="Roboto Bold 16pt")
        self.renderer.send(commands)

    def _cancel_progress_label(self):
        operation = self._operation_context_status()
        state = str(operation.get("current_state") or "").strip().upper()
        if (self._temperature_wait_active() or
                getattr(self, "cancel_waiting_for_heat", False)):
            if state:
                return "INTERRUPTING %s..." % (state,)
            path = operation.get("context_path") or ()
            if path:
                return "INTERRUPTING %s..." % str(path[-1]).strip().upper()
            return "INTERRUPTING TEMPERATURE WAIT..."
        if state:
            return "WILL STOP AFTER %s" % (state,)
        return "WILL STOP AT THE NEXT STEP"

    def _update_cancel_progress(self):
        if (self.page != ScreenPage.OPERATION_CANCEL
                or self.cancel_mode != "pending"):
            return
        label = self._cancel_progress_label()
        self.busy_phase = (self.busy_phase + 1) % 5
        if label == self._last_cancel_label:
            commands = []
        else:
            self._last_cancel_label = label
            commands = [self.renderer.fill(100, 140, 600, 65, ThemeColor.BACKGROUND),
                        self.renderer.text(400, 170, label, ThemeColor.WARNING,
                                           "JetBrainsMono Bold 16pt", "center",
                                           "middle", max_width=700,
                                           truncate=True)]
        loader_y = 385
        for index in range(5):
            commands.append(self.renderer.fill(
                290 + index * 48, loader_y, 32, 12,
                ThemeColor.PRIMARY if index == self.busy_phase % 5 else ThemeColor.MUTED))
        self.renderer.send(commands)
