## Forge-X update adapter for Feather
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations

import logging

from ..common import WebRequest

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from .application import InternalTransport
    from .job_state import JobState
    from .klippy_connection import KlippyConnection
    from .shell_command import ShellCommandFactory


MAX_COMMIT_SUBJECTS = 24
MAX_UPDATE_KEY_LENGTH = 64
MAX_VERSION_LENGTH = 64
MAX_REVISION_LENGTH = 128
MAX_SUBJECT_LENGTH = 160
MAX_ERROR_LENGTH = 160
MAX_PROGRESS_LENGTH = 160
MAX_CONFLICT_FILES = 64
MAX_PATH_LENGTH = 160
MAX_GIT_OUTPUT_LENGTH = 32768
RESTARTING_MESSAGE = "RESTARTING PRINTER..."

_CONFLICT_MARKERS = (
    ("The following untracked working tree files would be overwritten by merge:",
     "UNTRACKED"),
    ("Your local changes to the following files would be overwritten by merge:",
     "MODIFIED"),
)


def _version(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value == "?" or len(value) > MAX_VERSION_LENGTH:
        return None
    return value


def _revision(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value == "?" or len(value) > MAX_REVISION_LENGTH:
        return None
    return value


def git_conflicting_files(message: Any) -> Tuple[List[str], int]:
    """Extract the paths Git reports as blocking a merge."""
    if not isinstance(message, str):
        return [], 0

    kind: Optional[str] = None
    conflicts: List[str] = []
    seen = set()
    for raw_line in message.splitlines():
        line = raw_line.strip()
        matched_kind = None
        for marker, label in _CONFLICT_MARKERS:
            if marker in line:
                matched_kind = label
                break
        if matched_kind is not None:
            kind = matched_kind
            continue
        if kind is None:
            continue
        if (not line or line.startswith("Please ")
                or line == "Aborting" or line.startswith("error:")):
            kind = None
            continue

        path = line[:MAX_PATH_LENGTH]
        entry = "%s: %s" % (kind, path)
        if entry not in seen:
            seen.add(entry)
            conflicts.append(entry)

    return conflicts[:MAX_CONFLICT_FILES], len(conflicts)


def forge_x_update_snapshot(status: Any) -> Dict[str, Any]:
    """Return the bounded subset of update status consumed by Feather."""
    if not isinstance(status, dict):
        raise ValueError("Forge-X update status is not an object")
    installed = _version(status.get("version"))
    remote = _version(status.get("remote_version"))
    if installed is None or remote is None:
        raise ValueError("Forge-X version information is unavailable")

    try:
        behind = max(0, int(status.get("commits_behind_count", 0)))
    except (TypeError, ValueError):
        raise ValueError("Forge-X commit count is invalid")
    current_revision = _revision(status.get("current_hash"))
    remote_revision = _revision(status.get("remote_hash"))
    available = bool(
        status.get("is_valid", False)
        and not status.get("corrupt", False)
        and not status.get("is_dirty", False)
        and installed != remote
        and behind > 0
        and current_revision is not None
        and remote_revision is not None
        and current_revision != remote_revision)

    subjects = []
    commits = status.get("commits_behind", ())
    if isinstance(commits, (tuple, list)):
        for commit in commits[:MAX_COMMIT_SUBJECTS]:
            if not isinstance(commit, dict):
                continue
            subject = commit.get("subject")
            if not isinstance(subject, str):
                continue
            subject = " ".join(subject.split())
            if subject:
                subjects.append(subject[:MAX_SUBJECT_LENGTH])

    return {
        "available": available,
        "installed_version": installed,
        "available_version": remote,
        "available_revision": remote_revision or "",
        "changes": subjects,
        "changes_total": behind,
    }


class FeatherUpdates:
    """Adapt Moonraker's public update API to Klipper remote methods."""

    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.update_key = config.get("update_key", "forge-x").strip()
        if (not self.update_key
                or len(self.update_key) > MAX_UPDATE_KEY_LENGTH):
            raise config.error(
                "[feather_updates]: update_key must contain 1 to %d characters"
                % MAX_UPDATE_KEY_LENGTH)
        self.internal_transport: InternalTransport
        self.internal_transport = self.server.lookup_component(
            "internal_transport")
        self.klippy_connection: KlippyConnection
        self.klippy_connection = self.server.lookup_component(
            "klippy_connection")
        self.job_state: JobState
        self.job_state = self.server.lookup_component("job_state")
        self.shell_command: ShellCommandFactory
        self.shell_command = self.server.lookup_component("shell_command")
        update_config = config.getsection(
            "update_manager " + self.update_key)
        self.repo_path = update_config.getpath("path")
        self.update_token: Optional[int] = None
        self.update_operation: Optional[str] = None

        self.server.register_remote_method(
            "feather_request_forge_x_update_status",
            self._handle_status_request)
        self.server.register_remote_method(
            "feather_start_forge_x_update",
            self._handle_update_request)
        self.server.register_remote_method(
            "feather_reset_forge_x_update",
            self._handle_reset_request)
        self.server.register_event_handler(
            "update_manager:update_response", self._handle_update_progress)

    def _printer_busy(self) -> bool:
        try:
            state = self.job_state.get_last_stats().get("state", "")
            return not self.klippy_connection.is_ready() or state in (
                "printing", "paused")
        except Exception:
            return True

    async def _cached_forge_x_status(self) -> Dict[str, Any]:
        status = await self.internal_transport.call_method(
            "machine.update.status", refresh=False)
        return self._forge_x_snapshot(status)

    async def _fresh_forge_x_status(self) -> Dict[str, Any]:
        status = await self.internal_transport.call_method(
            "machine.update.refresh", name=self.update_key)
        return self._forge_x_snapshot(status)

    def _forge_x_snapshot(self, status: Any) -> Dict[str, Any]:
        return forge_x_update_snapshot(self._forge_x_info(status))

    def _forge_x_info(self, status: Any) -> Dict[str, Any]:
        if not isinstance(status, dict):
            raise ValueError("Moonraker update status is unavailable")
        versions = status.get("version_info")
        if not isinstance(versions, dict) or self.update_key not in versions:
            raise ValueError(
                "Configured update source is unavailable: %s"
                % self.update_key)
        info = versions[self.update_key]
        if not isinstance(info, dict):
            raise ValueError("Configured update status is invalid")
        return info

    async def _handle_status_request(self, token: int) -> None:
        response: Dict[str, Any] = {"token": int(token), "error": ""}
        try:
            response.update(await self._fresh_forge_x_status())
        except Exception as exc:
            logging.info("Feather update status unavailable: %s", exc)
            response["error"] = str(exc)[:MAX_ERROR_LENGTH]

        try:
            await self.klippy_connection.request(WebRequest(
                "feather/update_status", response))
        except Exception:
            logging.exception(
                "Unable to return Forge-X update status to Feather")

    @staticmethod
    def _progress_message(value: Any) -> str:
        if not isinstance(value, (str, bytes)):
            return ""
        if isinstance(value, bytes):
            value = value.decode(errors="replace")
        message = " ".join(value.split())[:MAX_PROGRESS_LENGTH]
        if "restarting service forge-x" in message.lower():
            return RESTARTING_MESSAGE
        return message

    async def _send_update_state(
        self, token: int, state: str, message: str, **fields: Any
    ) -> None:
        payload: Dict[str, Any] = {
            "token": token,
            "state": state,
            "message": self._progress_message(message),
        }
        payload.update(fields)
        try:
            await self.klippy_connection.request(WebRequest(
                "feather/update_progress", payload))
        except Exception:
            logging.exception(
                "Unable to return Forge-X update progress to Feather")

    async def _cached_pull_conflicts(self) -> Tuple[List[str], int]:
        try:
            status = await self.internal_transport.call_method(
                "machine.update.status", refresh=False)
            messages = self._forge_x_info(status).get("git_messages", ())
        except Exception as exc:
            logging.info("Unable to read Forge-X Git failure details: %s", exc)
            return [], 0
        if not isinstance(messages, (tuple, list)):
            return [], 0

        output = []
        remaining = MAX_GIT_OUTPUT_LENGTH
        for raw_message in messages:
            if remaining <= 0:
                break
            if not isinstance(raw_message, str):
                continue
            message = raw_message[:remaining]
            if message:
                output.append(message)
                remaining -= len(message)
        return git_conflicting_files("\n".join(output))

    async def _send_update_failure(self, token: int, error: Any) -> None:
        message = str(error)
        conflicts, total = git_conflicting_files(message)
        if (not conflicts
                and "Git Command 'pull --progress" in message):
            conflicts, total = await self._cached_pull_conflicts()
        await self._send_update_state(
            token, "failed", message,
            recovery_required=bool(conflicts),
            conflicting_files=conflicts,
            conflicts_total=total)

    def _handle_update_progress(self, update: Any) -> Any:
        if (self.update_token is None or self.update_operation != "update"
                or not isinstance(update, dict)
                or update.get("application") != self.update_key):
            return None
        message = self._progress_message(update.get("message", ""))
        complete = update.get("complete") is True
        if not message and not complete:
            return None
        if complete:
            state = "complete"
            message = message or "UPDATE COMPLETE. RESTARTING PRINTER..."
        else:
            state = ("restarting" if message == RESTARTING_MESSAGE
                     else "progress")
        return self._send_update_state(
            self.update_token, state, message)

    async def _handle_update_request(
        self, expected_version: str, expected_revision: str, token: int
    ) -> None:
        try:
            token = int(token)
        except (TypeError, ValueError):
            logging.info("Feather Forge-X update refused: invalid token")
            return
        if self.update_token is not None:
            await self._send_update_state(
                token, "failed", "Another update is already in progress")
            return
        if self._printer_busy():
            logging.info(
                "Feather Forge-X update refused: Klippy is not safely idle")
            await self._send_update_state(
                token, "failed", "Printer is not safely idle")
            return

        expected_offer = (
            _version(expected_version), _revision(expected_revision))
        if None in expected_offer:
            await self._send_update_state(
                token, "failed", "Invalid update offer; check again")
            return

        try:
            snapshot = await self._cached_forge_x_status()
        except Exception as exc:
            logging.exception("Unable to verify Forge-X update status")
            await self._send_update_state(token, "failed", str(exc))
            return
        available_offer = (
            snapshot["available_version"], snapshot["available_revision"])
        if not snapshot["available"] or available_offer != expected_offer:
            logging.info(
                "Feather Forge-X update refused: available offer changed")
            await self._send_update_state(
                token, "failed", "Available update changed; check again")
            return
        if self._printer_busy():
            logging.info(
                "Feather Forge-X update refused: Klippy is not safely idle")
            await self._send_update_state(
                token, "failed", "Printer is not safely idle")
            return

        self.update_token = token
        self.update_operation = "update"
        await self._send_update_state(
            token, "accepted", "PREPARING FORGE-X UPDATE...")
        try:
            result = await self.internal_transport.call_method(
                "machine.update.client", name=self.update_key)
        except Exception as exc:
            logging.exception("Forge-X update failed")
            await self._send_update_failure(token, exc)
        else:
            if result != "ok":
                logging.info("Forge-X update refused: %s", result)
                await self._send_update_failure(token, result)
                return
            await self._send_update_state(
                token, "complete", "UPDATE COMPLETE. RESTARTING PRINTER...")
        finally:
            self.update_token = None
            self.update_operation = None

    async def _handle_reset_request(self, token: int) -> None:
        try:
            token = int(token)
        except (TypeError, ValueError):
            logging.info("Feather Forge-X reset refused: invalid token")
            return
        if self.update_token is not None:
            await self._send_update_state(
                token, "failed", "Another update operation is in progress")
            return
        if self._printer_busy():
            await self._send_update_state(
                token, "failed", "Printer is not safely idle")
            return

        self.update_token = token
        self.update_operation = "reset"
        try:
            status = await self.internal_transport.call_method(
                "machine.update.status", refresh=False)
            if not isinstance(status, dict) or status.get("busy"):
                await self._send_update_state(
                    token, "failed", "Moonraker update manager is busy")
                return
            if self._printer_busy():
                await self._send_update_state(
                    token, "failed", "Printer is not safely idle")
                return

            await self._send_update_state(
                token, "accepted", "RESETTING FORGE-X REPOSITORY...")
            cwd = str(self.repo_path)
            await self.shell_command.exec_cmd(
                "git reset --hard HEAD", timeout=30., cwd=cwd)
            await self.shell_command.exec_cmd(
                "git clean -fd", timeout=30., cwd=cwd)
        except Exception as exc:
            logging.exception("Forge-X repository reset failed")
            await self._send_update_state(token, "failed", str(exc))
        else:
            await self._send_update_state(
                token, "reset_complete", "FORGE-X RESET COMPLETE")
        finally:
            self.update_token = None
            self.update_operation = None


def load_component(config: ConfigHelper) -> FeatherUpdates:
    return FeatherUpdates(config)
