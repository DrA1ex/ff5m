## Feather print-file discovery and recency history
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import errno
import json
import logging
import os
import signal
import socket
import subprocess
import threading
import time


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
USB_EVENT_POLL = 0.2
USB_HELPER_STOP_TIMEOUT = 1.0
NETLINK_KOBJECT_UEVENT = 15
AF_NETLINK = getattr(socket, "AF_NETLINK", 16)


class FileTaskSuperseded(RuntimeError):
    """A queued worker task replaced by a newer request."""


class FileWorker:
    """Share one background thread for file tasks and resource lifecycles.

    Only the newest queued request is retained. A displaced queued request is
    completed with FileTaskSuperseded. A task already in progress is allowed
    to finish, but its controller token can discard the stale result.

    Services are not queued tasks: poll(now) returns their next deadline (or
    None), and close() always runs on worker shutdown. Service I/O never holds
    the caller's lock. A running file task finishes before services run again.
    """

    def __init__(self, schedule_async):
        self._schedule_async = schedule_async
        self._pending = None
        self._services = []
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stopped = False
        self._thread = threading.Thread(
            target=self._work, name="feather-file-worker")
        self._thread.daemon = True
        self._thread.start()

    def submit(self, task, callback):
        with self._lock:
            if self._stopped:
                return False
            superseded = self._pending
            self._pending = (task, callback)
            self._wake.set()
        if superseded is not None:
            _, dropped_callback = superseded
            self._deliver(
                dropped_callback, None,
                FileTaskSuperseded("Replaced by a newer worker task"))
        return True

    def add_service(self, service):
        with self._lock:
            if self._stopped:
                return False
            self._services.append(service)
            self._wake.set()
        return True

    def wake(self):
        self._wake.set()

    def stop(self):
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            self._pending = None
            self._wake.set()

    def _work(self):
        while True:
            with self._lock:
                self._wake.clear()
                stopped = self._stopped
                services = tuple(self._services)
            if stopped:
                for service in services:
                    try:
                        service.close()
                    except Exception:
                        logging.exception(
                            "[feather_screen] background service cleanup failed")
                return

            deadline = None
            for service in services:
                try:
                    next_poll = service.poll(time.monotonic())
                except Exception:
                    logging.exception(
                        "[feather_screen] background service poll failed")
                    next_poll = time.monotonic() + 1.0
                if next_poll is not None:
                    deadline = (next_poll if deadline is None
                                else min(deadline, next_poll))

            with self._lock:
                if self._stopped:
                    continue
                request = self._pending
                self._pending = None
            if request is None:
                timeout = (None if deadline is None
                           else max(0.0, deadline - time.monotonic()))
                self._wake.wait(timeout)
                continue

            task, callback = request
            result = None
            error = None
            try:
                result = task()
            except Exception as exc:
                error = exc

            with self._lock:
                if self._stopped:
                    continue

            self._deliver(callback, result, error)

    def _deliver(self, callback, result, error):
        def deliver(_eventtime, value=result, failure=error, done=callback):
            with self._lock:
                if self._stopped:
                    return
            done(value, failure)

        try:
            self._schedule_async(deliver)
        except (OSError, TypeError):
            logging.exception(
                "[feather_screen] unable to deliver file worker result")


class FileEntry:
    """Compact record for one flattened browser row."""

    __slots__ = ("name", "path", "directory", "size", "mtime")

    def __init__(self, name, path, directory=False, size=0, mtime=0):
        self.name = name
        self.path = path
        self.directory = bool(directory)
        self.size = size
        self.mtime = mtime

def _relative_path(root, path):
    root = os.path.realpath(root)
    path = os.path.realpath(path)
    return _relative_path_resolved(root, path)


def _relative_path_resolved(root, path):
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

    def latest_path(self):
        if not self.timestamps:
            return None
        return max(
            self.timestamps.items(), key=lambda item: (item[1], item[0]))[0]

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
                stream.write(json.dumps(
                    self.timestamps, ensure_ascii=False,
                    separators=(",", ":"), sort_keys=True))
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
                    # scandir roots are already canonical and symlinks are not
                    # followed below. Avoid two realpath/stat walks per row on
                    # the printer's slow flash storage.
                    path = os.path.abspath(child.path)
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
                    relative = _relative_path_resolved(root, path)
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
        if history is None:
            printed = 0.0
        elif hasattr(history, "last_printed"):
            printed = history.last_printed(history_name)
        else:
            printed = history.get(history_name, 0.0)
        recency = max(float(item.mtime), float(printed))
        return (-recency, item.name.lower())

    entries.sort(key=sort_key)
    return entries


class UsbStorageMonitor:
    """USB lifecycle serviced by FileWorker, with reactor-visible snapshots.

    The reactor only requests a mode and consumes completed state. The worker
    alone owns sockets, helpers and retries; pause retains the mounted drive.
    """

    __slots__ = (
        "mount_point", "helper_path", "_worker", "_state_lock",
        "_requested_active", "_stop_requested", "_mode_revision",
        "_applied_revision", "_process_revision", "_snapshot",
        "_visible_snapshot", "_changed",
        "_popen", "_is_mount", "_socket_factory", "event_socket",
        "process",
        "process_started", "next_attempt", "next_socket_attempt", "dirty",
        "active", "failures", "stopped")

    def __init__(self, virtual_sd_root, worker,
                 helper_path=USB_HELPER_PATH, popen=None, is_mount=None,
                 socket_factory=None):
        self.mount_point = os.path.join(
            os.path.realpath(virtual_sd_root), USB_MOUNT_NAME)
        self.helper_path = helper_path
        self._worker = worker
        self._state_lock = threading.Lock()
        self._requested_active = False
        self._stop_requested = False
        self._mode_revision = 0
        self._applied_revision = 0
        self._process_revision = 0
        self._snapshot = self._visible_snapshot = (False, None)
        self._changed = False
        self._popen = popen or subprocess.Popen
        self._is_mount = is_mount or os.path.ismount
        self._socket_factory = socket_factory or socket.socket

        self.event_socket = None
        self.process = None
        self.process_started = 0.0
        self.next_attempt = 0.0
        self.next_socket_attempt = 0.0
        self.dirty = True
        self.active = False
        self.failures = 0
        self.stopped = False
        if not worker.add_service(self):
            raise RuntimeError("USB monitor requires a running file worker")

    @property
    def available(self):
        return not self._stop_requested and self._visible_snapshot[0]

    @property
    def device(self):
        return None if self._stop_requested else self._visible_snapshot[1]

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
        self.next_socket_attempt = eventtime

    def _close_events(self):
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
            except OSError as exc:
                if exc.errno == errno.ENOBUFS:
                    logging.warning(
                        "[feather_screen] USB event queue overflowed; "
                        "reconciling current state")
                    self.dirty = True
                    self.next_attempt = eventtime
                    return
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
        with self._state_lock:
            if not self._requested_active or self._stop_requested:
                return
            self._process_revision = self._mode_revision
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

    @classmethod
    def _terminate(cls, process):
        if process is None or process.poll() is not None:
            return
        cls._kill_group(process, signal.SIGTERM)

    def _finish(self, eventtime):
        if self.process is None or self.process.poll() is None:
            return False
        process = self.process
        self.process = None
        self.process_started = 0.0
        output = self._collect_process(process)
        if output is None:
            self.dirty = True
            self.next_attempt = eventtime + USB_RETRY_MAX
            return False
        output = output.decode("utf-8", errors="replace")
        with self._state_lock:
            if (self._stop_requested or not self._requested_active
                    or self._process_revision != self._mode_revision):
                return False
            previous = self._snapshot
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        attached = next(
            (line for line in lines if line.startswith("ATTACHED ")), None)
        mounted = self._is_mount(self.mount_point)
        was_available, previous_device = previous
        available = bool(
            process.returncode == 0 and attached and mounted)
        if available:
            fields = attached.split()
            device = fields[1] if len(fields) > 1 else None
            self.failures = 0
            if not self.dirty:
                self.next_attempt = eventtime
            if not was_available or device != previous_device:
                logging.info(
                    "[feather_screen] USB files available from %s",
                    device or "unknown device")
        else:
            device = None
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
                if not self.dirty:
                    self.next_attempt = eventtime
            if not busy and lines and "NONE" not in lines:
                logging.info(
                    "[feather_screen] USB reconciliation deferred: %s",
                    lines[-1])
        with self._state_lock:
            if (self._stop_requested or not self._requested_active
                    or self._process_revision != self._mode_revision):
                return False
            current = (available, device)
            self._snapshot = current
            self._changed = self._changed or current != previous
        return current != previous

    def resume(self, eventtime):
        self._request_mode(True)

    def pause(self):
        self._request_mode(False)

    def _request_mode(self, active):
        with self._state_lock:
            if self._stop_requested or self._requested_active == active:
                return
            self._requested_active = active
            self._mode_revision += 1
        self._worker.wake()

    def tick(self, eventtime):
        with self._state_lock:
            if self._stop_requested or not self._requested_active:
                return False
            self._visible_snapshot = self._snapshot
            changed = self._changed
            self._changed = False
        return changed

    def poll(self, eventtime):
        """Called only by the shared worker, including socket event draining."""
        with self._state_lock:
            stop = self._stop_requested
            active = self._requested_active
            revision = self._mode_revision
        if stop:
            self.close()
            return None
        if revision != self._applied_revision:
            self.active = False
            self._close_events()
            self._cancel_process()
            self._applied_revision = revision
            self.active = active
            self.dirty = True
            self.next_attempt = eventtime
        if not self.active or self.stopped:
            return None

        if self.event_socket is None and eventtime >= self.next_socket_attempt:
            self._open_events(eventtime)
        self._handle_events(eventtime)
        self._finish(eventtime)
        if self.process is not None:
            if eventtime - self.process_started >= USB_HELPER_TIMEOUT:
                logging.error(
                    "[feather_screen] USB reconciliation timed out")
                self._cancel_process()
                self.dirty = True
                self.next_attempt = eventtime + USB_RETRY_MAX
            return eventtime + USB_EVENT_POLL
        if self.dirty and eventtime >= self.next_attempt:
            self._start(eventtime)
        return eventtime + USB_EVENT_POLL

    def stop(self):
        with self._state_lock:
            if self._stop_requested:
                return
            self._stop_requested = True
            self._requested_active = False
            self._visible_snapshot = (False, None)
        self._worker.wake()

    @staticmethod
    def _kill_group(process, signum):
        try:
            os.killpg(process.pid, signum)
        except OSError:
            pass

    def _cancel_process(self):
        process = self.process
        if process is None:
            return
        self.process = None
        self.process_started = 0.0
        self._terminate(process)
        self._collect_process(process)

    @classmethod
    def _collect_process(cls, process):
        # SIGKILL also covers descendants retaining a completed child's pipe.
        try:
            return process.communicate(timeout=USB_HELPER_STOP_TIMEOUT)[0]
        except subprocess.TimeoutExpired:
            cls._kill_group(process, signal.SIGKILL)
        try:
            return process.communicate(timeout=USB_HELPER_STOP_TIMEOUT)[0]
        except subprocess.TimeoutExpired:
            logging.error("[feather_screen] USB helper did not exit after SIGKILL")
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
            return None

    def close(self):
        """Canonical resource cleanup, called only on the worker thread."""
        if self.stopped:
            return
        with self._state_lock:
            self._stop_requested = True
            self._requested_active = False
        self.stopped = True
        self.active = False
        self._close_events()
        self._cancel_process()
        try:
            self._popen(
                [self.helper_path, "detach", self.mount_point],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
        except OSError:
            logging.exception("[feather_screen] unable to detach USB files")
