## Feather print-file discovery and recency history
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import json
import logging
import os
import signal
import socket
import subprocess


DEFAULT_HISTORY_PATH = "/opt/config/mod_data/feather_print_history.json"
HISTORY_LIMIT = 512
MAX_DIRECTORY_DEPTH = 2
VALID_GCODE_EXTS = (".gcode", ".g", ".gco")
USB_HELPER_PATH = "/root/printer_data/scripts/commands/zusb_mount.sh"
USB_MOUNT_NAME = "USB"
USB_RETRY_MAX = 30.0
USB_HELPER_TIMEOUT = 15.0
USB_EVENT_SETTLE = 0.4
USB_EVENT_BUFFER = 16384
NETLINK_KOBJECT_UEVENT = 15
AF_NETLINK = getattr(socket, "AF_NETLINK", 16)


class FileEntry:
    """Compact mapping-compatible record for one flattened browser row."""

    __slots__ = ("name", "path", "directory", "size", "mtime")

    def __init__(self, name, path, directory=False, size=0, mtime=0):
        self.name = name
        self.path = path
        self.directory = bool(directory)
        self.size = size
        self.mtime = mtime

    def __getitem__(self, key):
        if key not in self.__slots__:
            raise KeyError(key)
        return getattr(self, key)


def _relative_path(root, path):
    root = os.path.realpath(root)
    path = os.path.realpath(path)
    if path == root or not path.startswith(root + os.sep):
        return None
    relative = os.path.relpath(path, root)
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        return None
    return relative.replace(os.sep, "/")


class PrintHistory:
    """Persistent last-print timestamps keyed by virtual-SD relative path."""

    def __init__(self, path=DEFAULT_HISTORY_PATH):
        self.path = path
        self.timestamps = {}
        self._load()

    def _load(self):
        if not self.path:
            return
        try:
            with open(self.path, "r") as stream:
                saved = json.load(stream)
            if not isinstance(saved, dict):
                raise ValueError("history root is not an object")
            self.timestamps = {
                str(name): float(timestamp)
                for name, timestamp in saved.items()
                if (isinstance(name, str) and name
                    and isinstance(timestamp, (int, float))
                    and timestamp >= 0)
            }
        except (IOError, OSError):
            return
        except (TypeError, ValueError):
            logging.exception("[feather_screen] invalid print history")

    def last_printed(self, relative_path):
        return self.timestamps.get(relative_path, 0.0)

    def record(self, root, path, timestamp):
        relative = _relative_path(root, path)
        if relative is None:
            return False
        try:
            timestamp = float(timestamp)
        except (TypeError, ValueError):
            return False
        if timestamp < 0:
            return False
        self.timestamps[relative] = timestamp
        if len(self.timestamps) > HISTORY_LIMIT:
            newest = sorted(
                self.timestamps.items(), key=lambda item: item[1],
                reverse=True)[:HISTORY_LIMIT]
            self.timestamps = dict(newest)
        self._save()
        return True

    def _save(self):
        if not self.path:
            return
        temporary_path = self.path + ".tmp"
        try:
            directory = os.path.dirname(self.path)
            if directory and not os.path.isdir(directory):
                os.makedirs(directory)
            with open(temporary_path, "w") as stream:
                json.dump(
                    self.timestamps, stream, ensure_ascii=False,
                    separators=(",", ":"), sort_keys=True)
            os.replace(temporary_path, self.path)
        except (IOError, OSError):
            logging.exception("[feather_screen] unable to save print history")
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


def scan_gcode_files(root, history=None, max_depth=MAX_DIRECTORY_DEPTH,
                     history_prefix="", excluded_paths=()):
    """Return a flat newest-first list without following directory symlinks."""
    root = os.path.realpath(root)
    excluded = frozenset(os.path.realpath(path) for path in excluded_paths)
    history_prefix = str(history_prefix or "").strip("/")
    entries = []
    pending = [(root, 0)]
    while pending:
        directory, depth = pending.pop()
        try:
            with os.scandir(directory) as listing:
                for child in listing:
                    if child.name.startswith("."):
                        continue
                    path = os.path.realpath(child.path)
                    if path != root and not path.startswith(root + os.sep):
                        continue
                    if child.is_dir(follow_symlinks=False):
                        if path not in excluded and depth < max_depth:
                            pending.append((path, depth + 1))
                        continue
                    if (not child.is_file(follow_symlinks=False)
                            or not child.name.lower().endswith(
                                VALID_GCODE_EXTS)):
                        continue
                    relative = _relative_path(root, path)
                    if relative is None:
                        continue
                    stat = child.stat(follow_symlinks=False)
                    entries.append(FileEntry(
                        relative, path, False, stat.st_size, stat.st_mtime))
        except OSError as exc:
            raise RuntimeError("Unable to list files: %s" % exc)

    def sort_key(item):
        history_name = (history_prefix + "/" + item.name
                        if history_prefix else item.name)
        printed = (history.last_printed(history_name)
                   if history is not None else 0.0)
        recency = max(float(item.mtime), float(printed))
        return (-recency, item.name.lower())

    entries.sort(key=sort_key)
    return entries


class UsbStorageMonitor:
    """Event-driven USB mount lifecycle for the Feather file browser."""

    __slots__ = (
        "mount_point", "helper_path", "reactor",
        "_popen", "_is_mount", "_socket_factory", "event_socket",
        "event_handle", "available", "device", "process",
        "process_started", "next_attempt", "next_socket_attempt", "dirty",
        "active", "failures", "stopped")

    def __init__(self, virtual_sd_root, reactor,
                 helper_path=USB_HELPER_PATH, popen=None, is_mount=None,
                 socket_factory=None):
        self.mount_point = os.path.join(
            os.path.realpath(virtual_sd_root), USB_MOUNT_NAME)
        self.helper_path = helper_path
        self.reactor = reactor
        self._popen = popen or subprocess.Popen
        self._is_mount = is_mount or os.path.ismount
        self._socket_factory = socket_factory or socket.socket

        self.event_socket = None
        self.event_handle = None
        self.available = False
        self.device = None
        self.process = None
        self.process_started = 0.0
        self.next_attempt = 0.0
        self.next_socket_attempt = 0.0
        self.dirty = True
        self.active = False
        self.failures = 0
        self.stopped = False

    def _open_events(self, eventtime):
        if self.event_socket is not None or self.stopped or not self.active:
            return
        event_socket = None
        try:
            event_socket = self._socket_factory(
                AF_NETLINK, socket.SOCK_DGRAM,
                NETLINK_KOBJECT_UEVENT)
            event_socket.setsockopt(
                socket.SOL_SOCKET, socket.SO_RCVBUF, USB_EVENT_BUFFER)
            event_socket.bind((0, 1))
            event_socket.setblocking(False)
            event_handle = self.reactor.register_fd(
                event_socket.fileno(), self._handle_events)
        except (OSError, ValueError):
            logging.exception(
                "[feather_screen] unable to subscribe to USB events")
            if event_socket is not None:
                try:
                    event_socket.close()
                except OSError:
                    pass
            self.next_socket_attempt = eventtime + USB_RETRY_MAX
            return
        self.event_socket = event_socket
        self.event_handle = event_handle
        self.next_socket_attempt = eventtime

    def _close_events(self):
        if self.event_handle is not None:
            try:
                self.reactor.unregister_fd(self.event_handle)
            except (OSError, ValueError):
                pass
            self.event_handle = None
        if self.event_socket is not None:
            try:
                self.event_socket.close()
            except OSError:
                pass
            self.event_socket = None

    @staticmethod
    def _is_usb_block_event(message):
        fields = {}
        for field in message.split(b"\0"):
            key, separator, value = field.partition(b"=")
            if separator:
                fields[key] = value
        return (fields.get(b"SUBSYSTEM") == b"block"
                and b"/usb" in fields.get(b"DEVPATH", b"")
                and fields.get(b"ACTION") in (
                    b"add", b"remove", b"change", b"move"))

    def _handle_events(self, eventtime):
        if not self.active or self.event_socket is None:
            return
        relevant = False
        for _unused in range(64):
            try:
                message = self.event_socket.recv(4096)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                logging.exception(
                    "[feather_screen] unable to read USB event")
                self._close_events()
                self.next_socket_attempt = eventtime + USB_RETRY_MAX
                self.dirty = True
                self.next_attempt = eventtime
                return
            relevant = self._is_usb_block_event(message) or relevant
        if relevant:
            self.dirty = True
            self.next_attempt = eventtime + USB_EVENT_SETTLE

    def _start(self, eventtime):
        if (self.process is not None or self.stopped or not self.active
                or not self.dirty):
            return
        try:
            self.process = self._popen(
                [self.helper_path, "attach", self.mount_point],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                start_new_session=True)
        except OSError:
            logging.exception(
                "[feather_screen] unable to start USB reconciliation")
            self.next_attempt = eventtime + USB_RETRY_MAX
            return
        self.dirty = False
        self.process_started = eventtime

    @staticmethod
    def _terminate(process):
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except OSError:
            pass

    def _finish(self, eventtime):
        if self.process is None or self.process.poll() is None:
            return False
        process = self.process
        self.process = None
        self.process_started = 0.0
        output = process.communicate()[0].decode("utf-8", errors="replace")
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        attached = next(
            (line for line in lines if line.startswith("ATTACHED ")), None)
        mounted = self._is_mount(self.mount_point)
        was_available = self.available
        previous_device = self.device
        self.available = bool(
            process.returncode == 0 and attached and mounted)
        if self.available:
            fields = attached.split()
            self.device = fields[1] if len(fields) > 1 else None
            self.failures = 0
            self.next_attempt = eventtime
            if not was_available or self.device != previous_device:
                logging.info(
                    "[feather_screen] USB files available from %s",
                    self.device or "unknown device")
        else:
            self.device = None
            busy = "BUSY" in lines
            retry = busy or any(line.startswith("ERROR ") for line in lines)
            if retry:
                self.failures += 1
                self.dirty = True
                self.next_attempt = eventtime + (
                    1.0 if busy else min(
                        USB_RETRY_MAX, 2.0 ** min(self.failures, 5)))
            else:
                self.failures = 0
                self.next_attempt = eventtime
            if not busy and lines and "NONE" not in lines:
                logging.info(
                    "[feather_screen] USB reconciliation deferred: %s",
                    lines[-1])
        return was_available != self.available

    def resume(self, eventtime):
        if self.stopped:
            return
        if not self.active:
            self.active = True
            self.dirty = True
            self.next_attempt = eventtime
        if self.event_socket is None and eventtime >= self.next_socket_attempt:
            self._open_events(eventtime)

    def pause(self):
        if not self.active:
            return
        self.active = False
        self.dirty = True
        self._close_events()
        self._terminate(self.process)

    def tick(self, eventtime):
        if self.stopped or not self.active:
            return False
        changed = self._finish(eventtime)
        if self.process is not None:
            if eventtime - self.process_started >= USB_HELPER_TIMEOUT:
                logging.error(
                    "[feather_screen] USB reconciliation timed out")
                self._terminate(self.process)
                self.process_started = eventtime
            return changed
        if self.event_socket is None and eventtime >= self.next_socket_attempt:
            self._open_events(eventtime)
        if self.dirty and eventtime >= self.next_attempt:
            self._start(eventtime)
        return changed

    def stop(self):
        if self.stopped:
            return
        self.stopped = True
        self.active = False
        self._close_events()
        self._terminate(self.process)
        self.process = None
        self.available = False
        self.device = None
        try:
            self._popen(
                [self.helper_path, "detach", self.mount_point],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
        except OSError:
            logging.exception("[feather_screen] unable to detach USB files")
