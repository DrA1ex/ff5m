## Behavioral tests for the Moonraker Feather update adapter.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import asyncio
import importlib.util
import pathlib
import sys
import types
import unittest


class WebRequest:
    def __init__(self, endpoint, args):
        self.endpoint = endpoint
        self.args = args

    def get_endpoint(self):
        return self.endpoint

    def get_args(self):
        return self.args


PACKAGE = "_ff5m_test_moonraker_package"
ROOT_PACKAGE = types.ModuleType(PACKAGE)
ROOT_PACKAGE.__path__ = []
COMPONENTS_PACKAGE = types.ModuleType(PACKAGE + ".components")
COMPONENTS_PACKAGE.__path__ = []
COMMON_MODULE = types.ModuleType(PACKAGE + ".common")
COMMON_MODULE.WebRequest = WebRequest
sys.modules[PACKAGE] = ROOT_PACKAGE
sys.modules[PACKAGE + ".components"] = COMPONENTS_PACKAGE
sys.modules[PACKAGE + ".common"] = COMMON_MODULE

MODULE_PATH = (pathlib.Path(__file__).parents[1] / ".root" / "moonraker" /
               "components" / "feather_updates.py")
SPEC = importlib.util.spec_from_file_location(
    PACKAGE + ".components.feather_updates", MODULE_PATH)
FEATHER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FEATHER)


def update_status(**values):
    status = {
        "is_valid": True,
        "corrupt": False,
        "version": "1.4.2",
        "remote_version": "1.4.3",
        "current_hash": "a" * 40,
        "remote_hash": "b" * 40,
        "commits_behind_count": 2,
        "commits_behind": [
            {"subject": "Fix Wi-Fi", "sha": "1" * 40},
            {"subject": "Improve boot splash", "sha": "2" * 40},
        ],
    }
    status.update(values)
    return status


class InternalTransport:
    def __init__(self, status=None):
        self.status = status or {
            "version_info": {"forge-x": update_status()}}
        self.calls = []
        self.status_error = None
        self.update_error = None
        self.update_result = "ok"
        self.after_status = None
        self.update_event = None

    async def call_method(self, method, **kwargs):
        self.calls.append((method, kwargs))
        if method in ("machine.update.status", "machine.update.refresh"):
            if self.status_error is not None:
                raise self.status_error
            if self.after_status is not None:
                self.after_status()
            return self.status
        if method == "machine.update.client":
            if self.update_error is not None:
                raise self.update_error
            if self.update_event is not None:
                result = self.update_event()
                if result is not None:
                    await result
            return self.update_result
        raise AssertionError("unexpected internal method: " + method)


class ShellCommand:
    def __init__(self):
        self.calls = []
        self.error_command = None

    async def exec_cmd(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command == self.error_command:
            raise RuntimeError("command failed")
        return ""


class KlippyConnection:
    def __init__(self):
        self.ready = True
        self.requests = []

    def is_ready(self):
        return self.ready

    async def request(self, request):
        self.requests.append(request)
        return "ok"


class JobState:
    def __init__(self):
        self.state = "standby"

    def get_last_stats(self):
        return {"state": self.state}


class Server:
    error = RuntimeError

    def __init__(self):
        self.internal_transport = InternalTransport()
        self.klippy_connection = KlippyConnection()
        self.job_state = JobState()
        self.shell_command = ShellCommand()
        self.remote_methods = {}
        self.event_handlers = {}

    def lookup_component(self, name):
        return getattr(self, name)

    def register_remote_method(self, name, callback):
        self.remote_methods[name] = callback

    def register_event_handler(self, name, callback):
        self.event_handlers[name] = callback

    def send_event(self, name, value):
        return self.event_handlers[name](value)


class UpdateConfig:
    def getpath(self, option):
        if option != "path":
            raise AssertionError("unexpected config option: " + option)
        return pathlib.Path("/root/printer_data/config/mod")


class Config:
    def __init__(self, server, update_key="forge-x"):
        self.server = server
        self.update_key = update_key

    def get_server(self):
        return self.server

    def get(self, name, default=None):
        if name == "update_key":
            return self.update_key
        return default

    def getsection(self, name):
        if name != "update_manager " + self.update_key:
            raise AssertionError("unexpected config section: " + name)
        return UpdateConfig()

    def error(self, message):
        return ValueError(message)


class ForgeXUpdateSnapshotTest(unittest.TestCase):
    def test_git_conflict_parser_labels_and_deduplicates_paths(self):
        message = """Updating 49dfc24..b36d9d9
error: The following untracked working tree files would be overwritten by merge:
    .shell/commands/ztheme_editor.sh
Please move or remove them before you merge.
Aborting
error: Your local changes to the following files would be overwritten by merge:
    config/mod.cfg
Please commit your changes or stash them before you merge.
Aborting
error: The following untracked working tree files would be overwritten by merge:
    .shell/commands/ztheme_editor.sh
Please move or remove them before you merge.
Aborting"""

        conflicts, total = FEATHER.git_conflicting_files(message)

        self.assertEqual(conflicts, [
            "UNTRACKED: .shell/commands/ztheme_editor.sh",
            "MODIFIED: config/mod.cfg",
        ])
        self.assertEqual(total, 2)

    def test_git_conflict_parser_bounds_the_returned_payload(self):
        paths = ["    generated/path-%d.cfg" % index for index in range(80)]
        message = "\n".join([
            "error: The following untracked working tree files would be overwritten by merge:",
        ] + paths + ["Please move or remove them before you merge."])

        conflicts, total = FEATHER.git_conflicting_files(message)

        self.assertEqual(len(conflicts), FEATHER.MAX_CONFLICT_FILES)
        self.assertEqual(total, 80)

    def test_available_update_uses_cached_versions_and_commit_subjects(self):
        snapshot = FEATHER.forge_x_update_snapshot(update_status())

        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["installed_version"], "1.4.2")
        self.assertEqual(snapshot["available_version"], "1.4.3")
        self.assertEqual(snapshot["available_revision"], "b" * 40)
        self.assertEqual(snapshot["changes"], [
            "Fix Wi-Fi", "Improve boot splash"])
        self.assertNotIn("sha", snapshot)

    def test_equal_or_invalid_repository_state_is_not_offered(self):
        equal = FEATHER.forge_x_update_snapshot(update_status(
            remote_version="1.4.2", remote_hash="a" * 40,
            commits_behind_count=0, commits_behind=[]))
        invalid = FEATHER.forge_x_update_snapshot(update_status(
            is_valid=False))
        dirty = FEATHER.forge_x_update_snapshot(update_status(
            is_dirty=True))

        self.assertFalse(equal["available"])
        self.assertFalse(invalid["available"])
        self.assertFalse(dirty["available"])

    def test_missing_changelog_does_not_hide_valid_update(self):
        snapshot = FEATHER.forge_x_update_snapshot(update_status(
            commits_behind=None))

        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["changes"], [])
        self.assertEqual(snapshot["changes_total"], 2)

    def test_projection_bounds_pathological_commit_data(self):
        commits = [
            {"subject": ("Change %d " % index) + "x" * 500}
            for index in range(250)]
        snapshot = FEATHER.forge_x_update_snapshot(update_status(
            commits_behind_count=250, commits_behind=commits))

        self.assertEqual(
            len(snapshot["changes"]), FEATHER.MAX_COMMIT_SUBJECTS)
        self.assertTrue(all(
            len(subject) <= FEATHER.MAX_SUBJECT_LENGTH
            for subject in snapshot["changes"]))
        self.assertEqual(snapshot["changes_total"], 250)

    def test_malformed_versions_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "version information"):
            FEATHER.forge_x_update_snapshot(update_status(
                remote_version=None))


class FeatherUpdatesComponentTest(unittest.TestCase):
    def setUp(self):
        self.server = Server()
        self.component = FEATHER.FeatherUpdates(Config(self.server))

    def test_registers_remote_methods_and_update_progress_handler(self):
        self.assertEqual(set(self.server.remote_methods), {
            "feather_request_forge_x_update_status",
            "feather_start_forge_x_update",
            "feather_reset_forge_x_update",
        })
        self.assertIn(
            "update_manager:update_response", self.server.event_handlers)

    def test_status_refreshes_only_configured_updater_and_replies_to_klipper(self):
        asyncio.run(self.component._handle_status_request(17))

        self.assertEqual(self.server.internal_transport.calls, [
            ("machine.update.refresh", {"name": "forge-x"})])
        request = self.server.klippy_connection.requests[0]
        self.assertEqual(request.get_endpoint(), "feather/update_status")
        self.assertEqual(request.get_args()["token"], 17)
        self.assertEqual(request.get_args()["available_version"], "1.4.3")
        self.assertEqual(request.get_args()["available_revision"], "b" * 40)
        self.assertEqual(request.get_args()["error"], "")

    def test_status_failure_returns_error_without_raising(self):
        self.server.internal_transport.status_error = RuntimeError(
            "status unavailable")

        asyncio.run(self.component._handle_status_request(21))

        response = self.server.klippy_connection.requests[0].get_args()
        self.assertEqual(response["token"], 21)
        self.assertEqual(response["error"], "status unavailable")

    def test_update_delegates_to_canonical_internal_api(self):
        asyncio.run(self.component._handle_update_request(
            "1.4.3", "b" * 40, 31))

        self.assertEqual(self.server.internal_transport.calls, [
            ("machine.update.status", {"refresh": False}),
            ("machine.update.client", {"name": "forge-x"}),
        ])
        states = [
            request.get_args()["state"]
            for request in self.server.klippy_connection.requests]
        self.assertEqual(states, ["accepted", "complete"])

    def test_update_forwards_only_its_own_progress(self):
        def send_progress():
            unrelated = self.server.send_event(
                "update_manager:update_response", {
                    "application": "mainsail", "message": "Ignore me"})
            self.assertIsNone(unrelated)
            return self.server.send_event("update_manager:update_response", {
                "application": "forge-x",
                "message": "Receiving objects: 42%",
            })

        self.server.internal_transport.update_event = send_progress

        asyncio.run(self.component._handle_update_request(
            "1.4.3", "b" * 40, 32))

        progress = [
            request.get_args()
            for request in self.server.klippy_connection.requests
            if request.get_args()["state"] == "progress"]
        self.assertEqual(progress, [{
            "token": 32,
            "state": "progress",
            "message": "Receiving objects: 42%",
        }])

    def test_managed_service_restart_is_forwarded_as_terminal_stage(self):
        def send_restart():
            return self.server.send_event("update_manager:update_response", {
                "application": "forge-x",
                "message": "Git Repo forge-x: Restarting service forge-x...",
            })

        self.server.internal_transport.update_event = send_restart

        asyncio.run(self.component._handle_update_request(
            "1.4.3", "b" * 40, 39))

        restarting = [
            request.get_args()
            for request in self.server.klippy_connection.requests
            if request.get_args()["state"] == "restarting"]
        self.assertEqual(restarting, [{
            "token": 39,
            "state": "restarting",
            "message": "RESTARTING PRINTER...",
        }])

    def test_update_manager_completion_is_forwarded_as_completion(self):
        self.component.update_token = 40
        self.component.update_operation = "update"

        completion = self.component._handle_update_progress({
            "application": "forge-x",
            "message": "Update Finished...",
            "complete": True,
        })
        asyncio.run(completion)

        response = self.server.klippy_connection.requests[-1].get_args()
        self.assertEqual(response["token"], 40)
        self.assertEqual(response["state"], "complete")

    def test_configured_key_selects_status_and_canonical_update_target(self):
        server = Server()
        server.internal_transport.status = {
            "version_info": {"feather-release": update_status()}}
        component = FEATHER.FeatherUpdates(Config(
            server, update_key="feather-release"))

        asyncio.run(component._handle_update_request(
            "1.4.3", "b" * 40, 33))

        self.assertEqual(server.internal_transport.calls, [
            ("machine.update.status", {"refresh": False}),
            ("machine.update.client", {"name": "feather-release"}),
        ])

        server.internal_transport.calls.clear()
        asyncio.run(component._handle_status_request(34))
        self.assertEqual(server.internal_transport.calls, [
            ("machine.update.refresh", {"name": "feather-release"}),
        ])

    def test_empty_configured_key_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "update_key"):
            FEATHER.FeatherUpdates(Config(self.server, update_key=""))

    def test_update_is_rejected_while_paused(self):
        self.server.job_state.state = "paused"

        asyncio.run(self.component._handle_update_request(
            "1.4.3", "b" * 40, 34))

        self.assertEqual(self.server.internal_transport.calls, [])
        response = self.server.klippy_connection.requests[-1].get_args()
        self.assertEqual(response["state"], "failed")
        self.assertIn("idle", response["message"])

    def test_update_is_rejected_if_available_version_changed(self):
        asyncio.run(self.component._handle_update_request(
            "1.4.4", "b" * 40, 35))

        self.assertEqual(self.server.internal_transport.calls, [
            ("machine.update.status", {"refresh": False})])
        self.assertEqual(
            self.server.klippy_connection.requests[-1].get_args()["state"],
            "failed")

    def test_update_is_rejected_if_available_revision_changed(self):
        asyncio.run(self.component._handle_update_request(
            "1.4.3", "c" * 40, 46))

        self.assertEqual(self.server.internal_transport.calls, [
            ("machine.update.status", {"refresh": False})])
        response = self.server.klippy_connection.requests[-1].get_args()
        self.assertEqual(response["state"], "failed")

    def test_update_rechecks_idle_after_reading_status(self):
        self.server.internal_transport.after_status = lambda: setattr(
            self.server.job_state, "state", "printing")

        asyncio.run(self.component._handle_update_request(
            "1.4.3", "b" * 40, 36))

        self.assertEqual(self.server.internal_transport.calls, [
            ("machine.update.status", {"refresh": False})])

    def test_update_failure_does_not_escape_to_klipper(self):
        self.server.internal_transport.update_error = RuntimeError(
            "update unavailable")

        with self.assertLogs(level="ERROR"):
            asyncio.run(self.component._handle_update_request(
                "1.4.3", "b" * 40, 37))

        self.assertEqual(
            self.server.internal_transport.calls[-1][0],
            "machine.update.client")
        response = self.server.klippy_connection.requests[-1].get_args()
        self.assertEqual(response["state"], "failed")
        self.assertIn("update unavailable", response["message"])

    def test_git_conflict_failure_returns_reset_details(self):
        self.server.internal_transport.update_error = RuntimeError("""
error: The following untracked working tree files would be overwritten by merge:
    .shell/commands/ztheme_editor.sh
Please move or remove them before you merge.
Aborting""")

        with self.assertLogs(level="ERROR"):
            asyncio.run(self.component._handle_update_request(
                "1.4.3", "b" * 40, 41))

        response = self.server.klippy_connection.requests[-1].get_args()
        self.assertEqual(response["state"], "failed")
        self.assertTrue(response["recovery_required"])
        self.assertEqual(response["conflicting_files"], [
            "UNTRACKED: .shell/commands/ztheme_editor.sh"])
        self.assertEqual(response["conflicts_total"], 1)

    def test_generic_pull_failure_recovers_conflicts_from_cached_git_output(self):
        self.server.internal_transport.status["version_info"]["forge-x"][
            "git_messages"] = [
                "error: The following untracked working tree files would be overwritten by merge:",
                ".py/klipper/plugins/ui/themes/www/index.html",
                ".py/klipper/plugins/ui/themes/www/theme-editor.css",
                ".py/klipper/plugins/ui/themes/www/theme-editor.js",
                ".shell/commands/ztheme_editor.sh",
                "Please move or remove them before you merge.",
                "Aborting",
                "Updating 49dfc24..b36d9d9",
            ]
        self.server.internal_transport.update_error = RuntimeError(
            "Git Command 'pull --progress' failed")

        with self.assertLogs(level="ERROR"):
            asyncio.run(self.component._handle_update_request(
                "1.4.3", "b" * 40, 45))

        self.assertEqual(self.server.internal_transport.calls[-1], (
            "machine.update.status", {"refresh": False}))
        response = self.server.klippy_connection.requests[-1].get_args()
        self.assertTrue(response["recovery_required"])
        self.assertEqual(response["conflicting_files"], [
            "UNTRACKED: .py/klipper/plugins/ui/themes/www/index.html",
            "UNTRACKED: .py/klipper/plugins/ui/themes/www/theme-editor.css",
            "UNTRACKED: .py/klipper/plugins/ui/themes/www/theme-editor.js",
            "UNTRACKED: .shell/commands/ztheme_editor.sh",
        ])
        self.assertEqual(response["conflicts_total"], 4)

    def test_reset_restores_the_existing_checkout_without_clone_recovery(self):
        asyncio.run(self.component._handle_reset_request(42))

        self.assertEqual(self.server.internal_transport.calls, [
            ("machine.update.status", {"refresh": False}),
        ])
        self.assertEqual(self.server.shell_command.calls, [
            ("git reset --hard HEAD", {
                "timeout": 30., "cwd": "/root/printer_data/config/mod"}),
            ("git clean -fd", {
                "timeout": 30., "cwd": "/root/printer_data/config/mod"}),
        ])
        states = [
            request.get_args()["state"]
            for request in self.server.klippy_connection.requests]
        self.assertEqual(states, ["accepted", "reset_complete"])

    def test_reset_command_failure_is_reported_without_continuing(self):
        self.server.shell_command.error_command = "git reset --hard HEAD"

        with self.assertLogs(level="ERROR"):
            asyncio.run(self.component._handle_reset_request(44))

        self.assertEqual(len(self.server.shell_command.calls), 1)
        states = [
            request.get_args()["state"]
            for request in self.server.klippy_connection.requests]
        self.assertEqual(states, ["accepted", "failed"])

    def test_reset_is_rejected_while_update_manager_is_busy(self):
        self.server.internal_transport.status["busy"] = True

        asyncio.run(self.component._handle_reset_request(46))

        self.assertEqual(self.server.shell_command.calls, [])
        response = self.server.klippy_connection.requests[-1].get_args()
        self.assertEqual(response["state"], "failed")

    def test_reset_is_rejected_while_printer_is_not_idle(self):
        self.server.job_state.state = "printing"

        asyncio.run(self.component._handle_reset_request(43))

        self.assertEqual(self.server.internal_transport.calls, [])
        response = self.server.klippy_connection.requests[-1].get_args()
        self.assertEqual(response["state"], "failed")
        self.assertIn("idle", response["message"])

    def test_update_manager_refusal_is_reported_as_failure(self):
        self.server.internal_transport.update_result = (
            "Object forge-x is currently being updated")

        asyncio.run(self.component._handle_update_request(
            "1.4.3", "b" * 40, 38))

        response = self.server.klippy_connection.requests[-1].get_args()
        self.assertEqual(response["state"], "failed")
        self.assertIn("currently being updated", response["message"])


if __name__ == "__main__":
    unittest.main()
