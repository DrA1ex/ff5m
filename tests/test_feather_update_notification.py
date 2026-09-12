## Behavioral tests for the Feather Forge-X update notification.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import pathlib
import sys
import unittest
from unittest import mock


PLUGINS = (pathlib.Path(__file__).parents[1] / ".py" / "klipper" /
           "plugins")
sys.path.insert(0, str(PLUGINS))

from feather_update_notification import (  # noqa: E402
    CHECK_TIMEOUT, DEFAULT_UPDATE_INTERVAL_MINUTES, FAILURE_RETRY_INTERVAL,
    MAX_FAILURE_RETRY_INTERVAL, ForgeXUpdateNotification, STARTUP_DELAY)
from ff5m_ui.print_state import PrintState  # noqa: E402
from ff5m_ui.screen import ScreenPage  # noqa: E402
from ui import FeatherRenderer  # noqa: E402


class Reactor:
    def __init__(self, now=100.0):
        self.now = float(now)
        self.callbacks = []
        self.sequence = 0

    def monotonic(self):
        return self.now

    def register_callback(self, callback, when=None):
        self.sequence += 1
        self.callbacks.append((
            self.now if when is None else float(when),
            self.sequence, callback))

    def run_until(self, deadline):
        deadline = float(deadline)
        while self.callbacks:
            scheduled, sequence, callback = min(
                self.callbacks, key=lambda item: (item[0], item[1]))
            if scheduled > deadline:
                break
            self.callbacks.remove((scheduled, sequence, callback))
            self.now = max(self.now, scheduled)
            callback(self.now)
        self.now = max(self.now, deadline)


class Webhooks:
    def __init__(self):
        self.endpoints = {}
        self.remote_calls = []

    def register_endpoint(self, path, callback):
        self.endpoints[path] = callback

    def call_remote_method(self, method, **kwargs):
        self.remote_calls.append((method, kwargs))


class WebRequest:
    def __init__(self, **values):
        self.values = values

    def get_int(self, name, default=None):
        return int(self.values.get(name, default))

    def get(self, name, default=None):
        return self.values.get(name, default)

    def get_str(self, name, default=None):
        value = self.values.get(name, default)
        return default if value is None else str(value)


class Host:
    def __init__(self):
        self.reactor = Reactor()
        self.webhooks = Webhooks()
        self.renderer = FeatherRenderer()
        self.draw_batches = []
        self.renderer.send = self.draw_batches.append
        self.page = ScreenPage.IDLE_HOME
        self.previous_page = ScreenPage.IDLE_HOME
        self.print_state = PrintState.IDLE
        self.print_active = False
        self.operation_active = False
        self.busy_message = None
        self.messages = []
        self.params = type("Params", (), {"variables": {
            "mod_check_update": True,
            "mod_check_update_interval": DEFAULT_UPDATE_INTERVAL_MINUTES,
        }})()
        self.update_notification = ForgeXUpdateNotification(
            self, self.webhooks)

    def _safety_print_active(self, _eventtime):
        return self.print_active

    def _update_notification_safe(self, _eventtime):
        return not (self.print_active or self.operation_active)

    def _show_page(self, page):
        old_page = self.page
        self.previous_page = old_page
        self.page = page
        self.update_notification.on_page_changed(old_page, page)
        if page == ScreenPage.UPDATE_NOTIFICATION:
            self.update_notification.render()

    def _show_message(self, message, return_page):
        self.messages.append((message, return_page))
        self._show_page(ScreenPage.MESSAGE)

    def page_for_print_state(self):
        return (ScreenPage.PRINTING if self.print_active
                else ScreenPage.IDLE_HOME)


def status_response(notification, version="1.4.3", installed="1.4.2",
                    changes=None, available=True, token=None, error="",
                    revision=None):
    if token is None:
        token = notification.check_token
    if revision is None:
        revision = "b" * 40
    if changes is None:
        changes = ["Fix Wi-Fi", "Improve boot splash"]
    return WebRequest(
        token=token, available=available,
        installed_version=installed, available_version=version,
        available_revision=revision,
        changes=changes, changes_total=len(changes), error=error)


class ForgeXUpdateNotificationTest(unittest.TestCase):
    def setUp(self):
        self.host = Host()
        self.notification = self.host.update_notification

    def request_and_respond(self, **kwargs):
        self.notification.request_check()
        request = status_response(self.notification, **kwargs)
        self.notification.handle_status_response(request)

    def block_update_with_conflicts(self, files):
        self.request_and_respond()
        self.notification.handle_action("update.install")
        self.notification.handle_update_progress(WebRequest(
            token=self.notification.install_token,
            state="failed", message="Git merge blocked",
            recovery_required=True, conflicting_files=files,
            conflicts_total=len(files)))

    def test_startup_check_waits_five_seconds(self):
        self.notification.start()

        self.host.reactor.run_until(100.0 + STARTUP_DELAY - 0.01)
        self.assertEqual(self.host.webhooks.remote_calls, [])

        self.host.reactor.run_until(100.0 + STARTUP_DELAY)
        self.assertEqual(
            self.host.webhooks.remote_calls[0][0],
            "feather_request_forge_x_update_status")

    def test_disabled_startup_schedules_no_check_and_runtime_enable_is_safe(self):
        self.host.params.variables["mod_check_update"] = False
        self.notification.start()
        self.host.reactor.run_until(100.0 + STARTUP_DELAY)
        self.assertEqual(self.host.webhooks.remote_calls, [])
        self.assertIsNone(self.notification._scheduled_at)

        self.host.print_active = True
        self.host.print_state = PrintState.PRINTING
        self.host.params.variables["mod_check_update"] = True
        self.notification.on_mod_params_changed()
        self.host.reactor.run_until(self.host.reactor.monotonic())
        self.assertEqual(self.host.webhooks.remote_calls, [])
        self.assertTrue(self.notification.check_pending)

        self.host.print_active = False
        self.host.print_state = PrintState.IDLE
        self.notification.on_print_state_changed(
            PrintState.PRINTING, PrintState.IDLE, "standby")
        self.assertEqual(len(self.host.webhooks.remote_calls), 1)

    def test_invalid_runtime_interval_falls_back_to_six_hours(self):
        for invalid in (0, -1, 3.5, "15", True):
            with self.subTest(invalid=invalid):
                self.host.params.variables[
                    "mod_check_update_interval"] = invalid
                enabled, interval = self.notification._read_settings()
                self.assertTrue(enabled)
                self.assertEqual(
                    interval, DEFAULT_UPDATE_INTERVAL_MINUTES * 60.0)

    def test_interval_change_replaces_idle_deadline_and_inflight_uses_new_value(self):
        self.notification.start()
        self.host.params.variables["mod_check_update_interval"] = 12
        self.notification.on_mod_params_changed()
        self.assertEqual(
            self.notification._scheduled_at,
            self.host.reactor.monotonic() + 12 * 60.0)

        self.notification.request_check()
        self.assertTrue(self.notification.check_in_flight)
        self.host.params.variables["mod_check_update_interval"] = 17
        self.notification.on_mod_params_changed()
        self.assertIsNone(self.notification._scheduled_at)
        self.assertEqual(len(self.host.webhooks.remote_calls), 1)

        self.notification.handle_status_response(status_response(
            self.notification, available=False, version="1.4.2", changes=[]))
        self.assertEqual(
            self.notification._scheduled_at,
            self.host.reactor.monotonic() + 17 * 60.0)

    def test_disable_invalidates_late_reply_even_after_reenable(self):
        self.notification.start()
        self.notification.request_check()
        old_token = self.notification.check_token

        self.host.params.variables["mod_check_update"] = False
        self.notification.on_mod_params_changed()
        self.assertFalse(self.notification.check_in_flight)
        self.assertIsNone(self.notification._scheduled_at)

        self.host.params.variables["mod_check_update"] = True
        self.notification.on_mod_params_changed()
        self.host.reactor.run_until(self.host.reactor.monotonic())
        new_token = self.notification.check_token
        self.assertGreater(new_token, old_token)
        self.assertTrue(self.notification.check_in_flight)

        self.notification.handle_status_response(status_response(
            self.notification, token=old_token))
        self.assertTrue(self.notification.check_in_flight)
        self.assertFalse(self.notification.dialog_visible)

        self.notification.handle_status_response(status_response(
            self.notification, token=new_token))
        self.assertTrue(self.notification.dialog_visible)

    def test_disable_hides_offer_but_preserves_dismissal_and_interval(self):
        self.notification.start()
        self.request_and_respond()
        self.notification.dismissed_offer = ("1.4.1", "a" * 40)
        self.host.params.variables["mod_check_update_interval"] = 19
        self.host.params.variables["mod_check_update"] = False

        self.notification.on_mod_params_changed()

        self.assertEqual(self.host.page, ScreenPage.IDLE_HOME)
        self.assertIsNone(self.notification.available_version)
        self.assertIsNone(self.notification.available_revision)
        self.assertIsNone(self.notification.installed_version)
        self.assertEqual(self.notification.changes, ())
        self.assertEqual(
            self.notification.dismissed_offer, ("1.4.1", "a" * 40))
        self.assertEqual(self.notification.check_interval, 19 * 60.0)
        self.assertIsNone(self.notification._scheduled_at)

    def test_startup_check_defers_until_printer_returns_idle(self):
        self.host.print_active = True
        self.host.print_state = PrintState.PRINTING
        self.notification.start()

        self.host.reactor.run_until(100.0 + STARTUP_DELAY)
        self.assertEqual(self.host.webhooks.remote_calls, [])
        self.assertEqual(self.host.reactor.callbacks, [])

        self.host.print_active = False
        self.host.print_state = PrintState.IDLE
        self.notification.on_print_state_changed(
            PrintState.PRINTING, PrintState.IDLE, "standby")
        self.assertEqual(len(self.host.webhooks.remote_calls), 1)

    def test_non_printing_printer_activity_defers_check_without_polling(self):
        self.host.operation_active = True
        self.notification.start()

        self.host.reactor.run_until(100.0 + STARTUP_DELAY)
        self.assertEqual(self.host.webhooks.remote_calls, [])
        self.assertTrue(self.notification.check_pending)

        self.host.operation_active = False
        self.host.reactor.run_until(
            100.0 + STARTUP_DELAY + 300.0)
        self.assertEqual(len(self.host.webhooks.remote_calls), 1)
        self.assertEqual(self.notification.failure_streak, 0)

    def test_no_update_available_does_not_open_dialog(self):
        self.request_and_respond(available=False, version="1.4.2", changes=[])

        self.assertEqual(self.host.page, ScreenPage.IDLE_HOME)
        self.assertFalse(self.notification.dialog_visible)

    def test_available_update_is_shown_only_from_safe_idle_home(self):
        self.host.page = ScreenPage.MESSAGE
        self.request_and_respond()
        self.assertEqual(self.host.page, ScreenPage.MESSAGE)

        self.host._show_page(ScreenPage.IDLE_HOME)
        self.assertEqual(self.host.page, ScreenPage.UPDATE_NOTIFICATION)
        self.assertTrue(self.notification.dialog_visible)
        self.assertEqual(self.notification.available_version, "1.4.3")

    def test_print_start_before_deferred_dialog_keeps_update_postponed(self):
        self.host.page = ScreenPage.MESSAGE
        self.request_and_respond()

        self.host.print_active = True
        self.host.print_state = PrintState.PRINTING
        self.notification.on_print_state_changed(
            PrintState.IDLE, PrintState.PRINTING, "printing")
        self.host._show_page(ScreenPage.IDLE_HOME)

        self.assertNotEqual(self.host.page, ScreenPage.UPDATE_NOTIFICATION)
        self.assertFalse(self.notification.dialog_visible)

        self.host.print_active = False
        self.host.print_state = PrintState.IDLE
        self.notification.on_print_state_changed(
            PrintState.PRINTING, PrintState.IDLE, "standby")
        self.host._show_page(ScreenPage.IDLE_HOME)
        self.assertEqual(self.host.page, ScreenPage.UPDATE_NOTIFICATION)

    def test_print_start_closes_dialog_without_dismissing_update(self):
        self.request_and_respond()
        self.assertTrue(self.notification.dialog_visible)

        self.host.print_active = True
        self.host.print_state = PrintState.PRINTING
        self.notification.on_print_state_changed(
            PrintState.IDLE, PrintState.PRINTING, "printing")

        self.assertEqual(self.host.page, ScreenPage.PRINTING)
        self.assertFalse(self.notification.dialog_visible)
        self.assertIsNone(self.notification.dismissed_offer)

        self.host.print_active = False
        self.host.print_state = PrintState.IDLE
        self.notification.on_print_state_changed(
            PrintState.PRINTING, PrintState.IDLE, "standby")
        self.host._show_page(ScreenPage.IDLE_HOME)
        self.assertEqual(self.host.page, ScreenPage.UPDATE_NOTIFICATION)

    def test_later_suppresses_only_the_displayed_offer(self):
        self.request_and_respond()
        self.notification.handle_action("update.later")

        self.assertEqual(
            self.notification.dismissed_offer, ("1.4.3", "b" * 40))
        self.assertEqual(self.host.page, ScreenPage.IDLE_HOME)
        self.notification.maybe_present()
        self.assertEqual(self.host.page, ScreenPage.IDLE_HOME)

        self.notification.request_check()
        self.notification.handle_status_response(status_response(
            self.notification, version="1.4.4", changes=["New fix"]))
        self.assertEqual(self.host.page, ScreenPage.UPDATE_NOTIFICATION)
        self.assertEqual(self.notification.available_version, "1.4.4")

    def test_later_does_not_suppress_new_revision_with_same_version(self):
        self.request_and_respond(revision="b" * 40)
        self.notification.handle_action("update.later")

        self.notification.request_check()
        self.notification.handle_status_response(status_response(
            self.notification, revision="c" * 40,
            changes=["New commit in the same version"]))

        self.assertEqual(self.host.page, ScreenPage.UPDATE_NOTIFICATION)
        self.assertEqual(self.notification.available_version, "1.4.3")

    def test_new_runtime_clears_in_memory_dismissal(self):
        self.request_and_respond()
        self.notification.handle_action("update.later")

        restarted = Host().update_notification
        self.assertIsNone(restarted.dismissed_offer)

    def test_duplicate_checks_and_responses_do_not_duplicate_dialogs(self):
        self.notification.request_check()
        token = self.notification.check_token
        self.notification.request_check()
        self.assertEqual(len(self.host.webhooks.remote_calls), 1)

        response = status_response(self.notification, token=token)
        self.notification.handle_status_response(response)
        draw_count = len(self.host.draw_batches)
        self.notification.handle_status_response(response)

        self.assertEqual(len(self.host.draw_batches), draw_count)
        self.assertTrue(self.notification.dialog_visible)

    def test_timeout_and_malformed_response_leave_printer_ui_usable(self):
        self.notification.request_check()
        self.host.reactor.run_until(159.99)
        self.assertTrue(self.notification.check_in_flight)
        self.host.reactor.run_until(160.0)
        self.assertFalse(self.notification.check_in_flight)
        self.assertEqual(self.host.page, ScreenPage.IDLE_HOME)

        self.notification.request_check()
        self.notification.handle_status_response(WebRequest(
            token=self.notification.check_token,
            available=True, installed_version=None,
            available_version=None, changes="not-a-list"))
        self.assertFalse(self.notification.dialog_visible)
        self.assertEqual(self.host.page, ScreenPage.IDLE_HOME)

    def test_status_endpoint_failure_does_not_escape_to_webhooks(self):
        class BrokenWebRequest(WebRequest):
            def get(self, name, default=None):
                if name == "available":
                    raise RuntimeError("broken payload")
                return super().get(name, default)

        self.notification.request_check()
        request = BrokenWebRequest(
            token=self.notification.check_token, error="")

        with self.assertLogs(level="ERROR"):
            result = self.notification.handle_status_response(request)

        self.assertEqual(result, "ok")
        self.assertFalse(self.notification.check_in_flight)
        self.assertEqual(self.host.page, ScreenPage.IDLE_HOME)

        self.host.reactor.run_until(
            self.host.reactor.monotonic() + FAILURE_RETRY_INTERVAL)
        self.assertEqual(len(self.host.webhooks.remote_calls), 2)

    def test_failures_back_off_exponentially_and_valid_reply_resets_cadence(self):
        expected = [5, 10, 20, 40, 80, 160, 320, 360, 360]
        for minutes in expected:
            self.notification.request_check()
            self.notification.handle_status_response(status_response(
                self.notification, error="Moonraker unavailable"))
            self.assertEqual(
                self.notification._scheduled_at,
                self.host.reactor.monotonic() + minutes * 60.0)

        self.host.params.variables["mod_check_update_interval"] = 23
        self.notification.on_mod_params_changed()
        self.notification.request_check()
        self.notification.handle_status_response(status_response(
            self.notification, available=False,
            version="1.4.2", changes=[]))
        self.assertEqual(self.notification.failure_streak, 0)
        self.assertEqual(
            self.notification._scheduled_at,
            self.host.reactor.monotonic() + 23 * 60.0)

    def test_missing_bridge_retries_and_recovers_without_ui_error(self):
        self.notification.webhooks = None
        self.assertFalse(self.notification.request_check())
        self.assertEqual(
            self.notification._scheduled_at,
            self.host.reactor.monotonic() + FAILURE_RETRY_INTERVAL)
        self.assertEqual(self.host.messages, [])

        self.notification.webhooks = self.host.webhooks
        self.host.reactor.run_until(self.notification._scheduled_at)
        self.assertEqual(len(self.host.webhooks.remote_calls), 1)

    def test_dispatch_timeout_and_malformed_reply_use_failure_retry(self):
        original_call = self.host.webhooks.call_remote_method
        self.host.webhooks.call_remote_method = mock.Mock(
            side_effect=RuntimeError("dispatch failed"))
        with self.assertLogs(level="ERROR"):
            self.assertFalse(self.notification.request_check())
        self.assertEqual(self.notification.failure_streak, 1)

        self.host.webhooks.call_remote_method = original_call
        self.notification.request_check()
        self.host.reactor.run_until(
            self.host.reactor.monotonic() + CHECK_TIMEOUT)
        self.assertEqual(self.notification.failure_streak, 2)

        self.notification.request_check()
        with self.assertLogs(level="ERROR"):
            self.notification.handle_status_response(WebRequest(
                token=self.notification.check_token, available="yes"))
        self.assertEqual(self.notification.failure_streak, 3)
        self.assertLessEqual(
            self.notification._scheduled_at - self.host.reactor.monotonic(),
            MAX_FAILURE_RETRY_INTERVAL)

    def test_long_changelog_is_bounded_scrollable_and_explicitly_truncated(self):
        changes = ["Change %02d" % index for index in range(80)]
        self.request_and_respond(changes=changes)

        self.assertIn("update.next", self.host.renderer._buttons)
        self.notification.handle_action("update.next")
        self.assertEqual(self.notification.change_page, 1)
        self.notification.handle_action("update.next")
        self.notification.handle_action("update.next")
        drawing = "\n".join(self.host.draw_batches[-1])

        self.assertIn("MORE CHANGES NOT SHOWN", drawing)
        self.assertNotIn("Change 79", drawing)

    def test_short_changelog_has_no_scroll_controls(self):
        self.request_and_respond(changes=["One", "Two"])

        self.assertNotIn("update.prev", self.host.renderer._buttons)
        self.assertNotIn("update.next", self.host.renderer._buttons)

    def test_missing_changelog_falls_back_to_available_version_message(self):
        self.request_and_respond(changes=[])
        drawing = "\n".join(self.host.draw_batches[-1])

        self.assertIn("CHANGELOG UNAVAILABLE", drawing)
        self.assertEqual(self.host.page, ScreenPage.UPDATE_NOTIFICATION)

    def test_update_rechecks_idle_before_delegating_to_moonraker(self):
        self.request_and_respond()
        self.host.print_active = True
        self.host.print_state = PrintState.PRINTING

        self.notification.handle_action("update.install")

        methods = [method for method, _params in self.host.webhooks.remote_calls]
        self.assertNotIn("feather_start_forge_x_update", methods)
        self.assertEqual(self.host.page, ScreenPage.PRINTING)
        self.assertIsNone(self.notification.dismissed_offer)

    def test_update_uses_existing_moonraker_ota_path(self):
        self.request_and_respond()

        self.notification.handle_action("update.install")

        method, params = self.host.webhooks.remote_calls[-1]
        self.assertEqual(method, "feather_start_forge_x_update")
        self.assertEqual(params["expected_version"], "1.4.3")
        self.assertEqual(params["expected_revision"], "b" * 40)
        self.assertEqual(params["token"], self.notification.install_token)
        self.assertEqual(self.host.page, ScreenPage.UPDATE_NOTIFICATION)
        self.assertTrue(self.notification.installing)
        self.assertEqual(
            self.host.busy_message, "PREPARING FORGE-X UPDATE...")

    def test_update_progress_replaces_loader_text_and_ignores_stale_token(self):
        self.request_and_respond()
        self.notification.handle_action("update.install")

        self.notification.handle_update_progress(WebRequest(
            token=self.notification.install_token - 1,
            state="progress", message="STALE"))
        self.assertNotEqual(self.host.busy_message, "STALE")

        self.notification.handle_update_progress(WebRequest(
            token=self.notification.install_token,
            state="progress", message="Receiving objects: 42%"))
        self.assertEqual(self.host.busy_message, "Receiving objects: 42%")
        self.assertTrue(self.notification.installing)

    def test_update_start_timeout_restores_retryable_dialog(self):
        self.request_and_respond()
        self.notification.handle_action("update.install")

        self.host.reactor.run_until(
            self.host.reactor.monotonic() + 14.99)
        self.assertTrue(self.notification.installing)
        self.host.reactor.run_until(
            self.host.reactor.monotonic() + 0.01)

        self.assertFalse(self.notification.installing)
        self.assertIsNone(self.host.busy_message)
        self.assertEqual(self.notification.available_version, "1.4.3")
        self.assertEqual(self.host.page, ScreenPage.MESSAGE)
        self.assertIn("did not respond", self.host.messages[-1][0])

    def test_update_failure_clears_loader_and_keeps_update_retryable(self):
        self.request_and_respond()
        self.notification.handle_action("update.install")

        self.notification.handle_update_progress(WebRequest(
            token=self.notification.install_token,
            state="failed", message="Network unavailable"))

        self.assertFalse(self.notification.installing)
        self.assertIsNone(self.host.busy_message)
        self.assertEqual(self.notification.available_version, "1.4.3")
        self.assertEqual(self.host.page, ScreenPage.MESSAGE)
        self.assertIn("Network unavailable", self.host.messages[-1][0])
        self.assertEqual(
            self.host.messages[-1][1], ScreenPage.UPDATE_NOTIFICATION)

    def test_git_conflict_paginates_files_and_requires_reset_confirmation(self):
        files = [
            ("UNTRACKED: path/to/file-%d.sh" % index)
            for index in range(30)]
        self.block_update_with_conflicts(files)

        self.assertFalse(self.notification.installing)
        self.assertEqual(self.notification.recovery_files, tuple(files))
        self.assertEqual(self.host.page, ScreenPage.UPDATE_NOTIFICATION)
        self.assertIn("update.reset", self.host.renderer._buttons)
        self.assertIn("update.next", self.host.renderer._buttons)
        first_page = "\n".join(self.host.draw_batches[-1])
        self.assertIn("file-0.sh", first_page)
        self.assertNotIn("file-29.sh", first_page)

        for _page in range(4):
            self.notification.handle_action("update.next")
        last_page = "\n".join(self.host.draw_batches[-1])
        self.assertIn("file-29.sh", last_page)

        calls_before_confirmation = list(self.host.webhooks.remote_calls)
        self.notification.handle_action("update.reset")
        self.assertTrue(self.notification.reset_confirmation)
        self.assertEqual(
            self.host.webhooks.remote_calls, calls_before_confirmation)
        self.assertIn("update.reset.back", self.host.renderer._buttons)
        self.assertIn("update.reset.confirm", self.host.renderer._buttons)

        self.notification.handle_action("update.reset.back")
        self.assertFalse(self.notification.reset_confirmation)
        self.notification.handle_action("update.reset.confirm")
        self.assertEqual(
            self.host.webhooks.remote_calls, calls_before_confirmation)

    def test_confirmed_reset_uses_moonraker_and_rechecks_after_completion(self):
        self.block_update_with_conflicts([
            "UNTRACKED: .shell/commands/ztheme_editor.sh",
            "MODIFIED: config/mod.cfg",
        ])
        self.notification.handle_action("update.reset")

        self.notification.handle_action("update.reset.confirm")

        method, params = self.host.webhooks.remote_calls[-1]
        self.assertEqual(method, "feather_reset_forge_x_update")
        self.assertEqual(params, {"token": self.notification.install_token})
        self.assertTrue(self.notification.installing)
        self.assertTrue(self.notification.resetting)

        self.notification.handle_update_progress(WebRequest(
            token=self.notification.install_token,
            state="accepted", message="Resetting repository"))
        self.notification.handle_update_progress(WebRequest(
            token=self.notification.install_token,
            state="reset_complete", message="Reset complete"))

        self.assertFalse(self.notification.installing)
        self.assertFalse(self.notification.resetting)
        self.assertEqual(self.notification.recovery_files, ())
        self.assertIsNone(self.notification.available_version)
        self.assertEqual(self.host.page, ScreenPage.IDLE_HOME)
        self.assertEqual(
            self.notification._scheduled_at, self.host.reactor.monotonic())

    def test_disabling_checks_does_not_interrupt_confirmed_installation(self):
        self.request_and_respond()
        self.notification.handle_action("update.install")
        self.notification.handle_update_progress(WebRequest(
            token=self.notification.install_token,
            state="accepted", message="Installing"))
        install_token = self.notification.install_token

        self.host.params.variables["mod_check_update"] = False
        self.notification.started = True
        self.notification.on_mod_params_changed()

        self.assertTrue(self.notification.installing)
        self.assertTrue(self.notification.install_confirmed)
        self.assertEqual(self.notification.install_token, install_token)
        self.assertEqual(self.host.busy_message, "Installing")
        self.assertEqual(self.host.page, ScreenPage.UPDATE_NOTIFICATION)

    def test_update_completion_replaces_loader_with_restart_modal(self):
        self.request_and_respond()
        self.notification.handle_action("update.install")

        with (mock.patch.object(
                self.host.renderer, "loader",
                wraps=self.host.renderer.loader) as loader,
              mock.patch.object(
                  self.host.renderer, "dialog",
                  wraps=self.host.renderer.dialog) as dialog):
            self.notification.handle_update_progress(WebRequest(
                token=self.notification.install_token,
                state="complete", message="Update Finished..."))
            self.notification.handle_update_progress(WebRequest(
                token=self.notification.install_token,
                state="progress", message="Late progress"))

        self.assertFalse(self.notification.installing)
        self.assertFalse(self.notification.install_confirmed)
        self.assertTrue(self.notification.restart_notice)
        self.assertIsNone(self.host.busy_message)
        loader.assert_not_called()
        dialog.assert_called_once()


if __name__ == "__main__":
    unittest.main()
