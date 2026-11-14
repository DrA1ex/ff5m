## Screen capture and report artifacts for Feather UI test runs.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import csv
import hashlib
import http.client
import json
import logging
import os
import queue
import re
import shutil
import threading
import time
from datetime import datetime


ARTIFACT_ROOT = "/data/feather-ui-tests"
ACTIVE_MARKER = os.path.join(ARTIFACT_ROOT, "active.json")
PRINTER_LOG = "/data/logFiles/printer.log"
SCREEN_WIDTH = 800
SCREEN_HEIGHT = 480
# The screen streamer started by S98screen, on loopback only. Its snapshot shows the screen as it is after the
# request, already encoded by the hardware JPEG encoder: a capture costs a few milliseconds and a small file,
# where reading the framebuffer and writing a 1.5 MiB BMP cost 100 ms and dominated the artifact transfer.
SCREEN_STREAMER = ("127.0.0.1", 8081)
SNAPSHOT_PATH = "/?action=snapshot"
# An older streamer answers with its latest published frame, which can predate the change being captured.
SNAPSHOT_READ_HEADER = "X-Snapshot-Read"
SNAPSHOT_TIMEOUT = 5.0
MAX_SNAPSHOT_BYTES = 1024 * 1024
FRAME_SAMPLE_INTERVAL = 0.05
FRAME_SETTLE_INTERVAL = 0.25
FRAME_SETTLE_TIMEOUT = 3.0
MAX_RUNS = 10
MAX_BYTES = 512 * 1024 * 1024
# Klipper lines that are never part of a healthy run.  A run whose own log
# slice contains one of them fails even when every step passed: an exception
# swallowed by a background timer or an MCU shutdown during cleanup leaves the
# printer broken without failing any step.  The patterns are deliberately
# narrow and case-sensitive; healthy homing prints "error=None".
PRINTER_LOG_FAILURE = re.compile(
    r"^Traceback \(most recent call last\):|"
    r"^Transition to shutdown state:|"
    r"^MCU '[^']+' shutdown:|"
    r"^Lost communication with MCU|"
    r"^Timeout with MCU|"
    r"^Internal error on command:|"
    r"^TMC '[^']+' reports error:")
MAX_LOG_FAILURES = 20


def _jsonable(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return dict((str(key), _jsonable(item))
                    for key, item in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    name = getattr(value, "name", None)
    return str(name if name is not None else value)


def _atomic_json(path, value):
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(_jsonable(value), stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def printer_log_failures(path):
    """Return the run's fatal Klipper log lines as ``{"line", "text"}``."""
    failures = []
    previous = ""
    with open(path, "r", encoding="utf-8", errors="replace") as stream:
        for number, text in enumerate(stream, 1):
            # A failed step logs its own traceback; the step failure already
            # fails the run and is not a second, unobserved problem.
            if (PRINTER_LOG_FAILURE.match(text) and not previous.startswith(
                    "[feather_ui_test] step failed:")):
                failures.append({"line": number, "text": text.rstrip()})
            previous = text
    return failures


def _directory_size(path):
    total = 0
    for root, _directories, files in os.walk(path):
        for filename in files:
            try:
                total += os.path.getsize(os.path.join(root, filename))
            except OSError:
                pass
    return total


class ArtifactWorker:
    """Perform screen capture and artifact IO away from Klipper's reactor."""

    def __init__(self, reactor, run_directory, printer_log=PRINTER_LOG,
                 screen_streamer=SCREEN_STREAMER):
        self.reactor = reactor
        self.run_directory = run_directory
        self.screen_streamer = screen_streamer
        self.printer_log = printer_log
        # SimpleQueue.put() is unbounded and never waits for the artifact
        # thread. Reactor callbacks only enqueue short work descriptions;
        # capture, telemetry and filesystem work remain off-thread.
        self.tasks = queue.SimpleQueue()
        self.records = []
        self.log_start = self._file_size(printer_log)
        # Cumulative capture accounting, so a reactor sample can distinguish a
        # queued request from capture work that actually started while the
        # reactor was late.  Only the reactor thread advances captures_queued;
        # only the artifact thread advances captures_started and
        # captures_finished, so none of the counters needs a lock.  The
        # per-capture artifact_timing.csv row is written only after a capture
        # returns and therefore cannot describe one interrupted by an MCU
        # shutdown.
        self.captures_queued = 0
        self.captures_started = 0
        self.captures_finished = 0
        self.thread = threading.Thread(
            target=self._work, name="feather-ui-artifacts")
        self.thread.daemon = True
        self.thread.start()

    @staticmethod
    def _file_size(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    def _reactor_callback(self, callback, value):
        def deliver(_eventtime):
            callback(value)
        register = getattr(self.reactor, "register_async_callback", None)
        if register is not None:
            register(deliver)
        else:
            self.reactor.register_callback(deliver)

    def log(self, message):
        self.tasks.put(("log", (
            time.time(), time.monotonic(), str(message)), None))

    def marker(self, value):
        self.tasks.put(("marker", value, None))

    def telemetry(self, name, fields, values):
        self.tasks.put(("telemetry", (name, fields, values), None))

    def capture(self, number, label, metadata, callback, settle=True):
        self.captures_queued += 1
        self.tasks.put((
            "capture", (
                number, label, metadata, bool(settle),
                time.time(), time.monotonic()), callback))

    def finish(self, summary, callback):
        self.tasks.put(("finish", summary, callback))

    def stop(self):
        self.tasks.put(("stop", None, None))

    def _work(self):
        while True:
            kind, payload, callback = self.tasks.get()
            capture_started = None
            value = None
            try:
                if kind == "stop":
                    return
                if kind == "log":
                    self._append_log(*payload)
                    continue
                if kind == "marker":
                    _atomic_json(ACTIVE_MARKER, payload)
                    continue
                if kind == "telemetry":
                    self._append_telemetry(*payload)
                    continue
                if kind == "capture":
                    capture_started = (time.time(), time.monotonic())
                    self.captures_started += 1
                    value = self._capture(*payload[:4])
                elif kind == "finish":
                    value = self._finish(payload)
                else:
                    raise RuntimeError("Unknown artifact task: %s" % kind)
            except Exception as exc:
                logging.exception("[feather_ui_test] artifact task failed")
                value = exc
            if kind == "capture" and capture_started is not None:
                finished_wall = time.time()
                finished_monotonic = time.monotonic()
                metadata = payload[2]
                try:
                    self._append_capture_timing({
                        "number": payload[0], "label": payload[1],
                        "capture_kind": metadata.get(
                            "capture_kind", "semantic"),
                        "phase": metadata.get("phase", ""),
                        "page": metadata.get("page", ""),
                        "settle": bool(payload[3]),
                        "queued_time": payload[4],
                        "queued_monotonic": payload[5],
                        "started_time": capture_started[0],
                        "started_monotonic": capture_started[1],
                        "finished_time": finished_wall,
                        "finished_monotonic": finished_monotonic,
                        "queue_delay_ms": (
                            (capture_started[1] - payload[5]) * 1000.0),
                        "duration_ms": (
                            (finished_monotonic - capture_started[1])
                            * 1000.0),
                        "success": not isinstance(value, Exception),
                        "file": (value.get("file", "")
                                 if isinstance(value, dict) else ""),
                        "error": (str(value)
                                  if isinstance(value, Exception) else ""),
                    })
                except Exception as exc:
                    logging.exception(
                        "[feather_ui_test] capture timing write failed")
                    if not isinstance(value, Exception):
                        value = exc
                self.captures_finished += 1
            if callback is not None:
                self._reactor_callback(callback, value)

    def _append_log(self, observed_wall, observed_monotonic, message):
        with open(os.path.join(self.run_directory, "run.log"), "a",
                  encoding="utf-8") as stream:
            observed = datetime.fromtimestamp(observed_wall).isoformat(
                timespec="milliseconds")
            stream.write("%s monotonic=%.6f %s\n" % (
                observed, observed_monotonic, message))

    def _append_capture_timing(self, record):
        fields = (
            "number", "label", "capture_kind", "phase", "page", "settle",
            "queued_time",
            "queued_monotonic", "started_time", "started_monotonic",
            "finished_time", "finished_monotonic", "queue_delay_ms",
            "duration_ms", "success", "file", "error",
        )
        path = os.path.join(self.run_directory, "artifact_timing.csv")
        new_file = not os.path.exists(path)
        with open(path, "a", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            if new_file:
                writer.writeheader()
            writer.writerow(record)

    def _append_telemetry(self, name, fields, values):
        path = os.path.join(self.run_directory, "%s.csv" % name)
        new_file = not os.path.exists(path)
        with open(path, "a", encoding="utf-8") as stream:
            if new_file:
                stream.write(",".join(fields) + "\n")
            stream.write(",".join(str(values.get(field, ""))
                                  for field in fields) + "\n")

    def _read_frame(self):
        """One JPEG of the screen as it is now, from the screen streamer."""
        host, port = self.screen_streamer
        connection = http.client.HTTPConnection(host, port, timeout=SNAPSHOT_TIMEOUT)
        try:
            connection.request("GET", SNAPSHOT_PATH)
            response = connection.getresponse()
            data = response.read(MAX_SNAPSHOT_BYTES + 1)
        except (OSError, http.client.HTTPException) as exc:
            raise RuntimeError(
                "Screen streamer is unavailable at %s:%d (%s); it runs while "
                "screen_stream is enabled" % (host, port, exc)) from exc
        finally:
            connection.close()
        if response.status != 200:
            raise RuntimeError("Screen streamer answered %d %s" % (
                response.status, response.reason))
        if response.getheader(SNAPSHOT_READ_HEADER) != "after-request":
            raise RuntimeError(
                "Screen streamer can return a stale snapshot; restart S98screen "
                "after updating forge_x_streamer")
        if len(data) > MAX_SNAPSHOT_BYTES or not data.startswith(b"\xff\xd8"):
            raise RuntimeError("Screen streamer did not return a JPEG")
        return data

    def _stable_frame(self):
        # Each snapshot already shows a complete picture taken after the
        # request; a stage capture also waits for the screen to stop changing,
        # because nothing tells it when the new stage has been drawn.  Equal
        # pictures encode to equal JPEG bytes.
        deadline = time.monotonic() + FRAME_SETTLE_TIMEOUT
        previous = None
        changed_at = time.monotonic()
        data = None
        while True:
            data = self._read_frame()
            now = time.monotonic()
            if data != previous:
                previous = data
                changed_at = now
            elif now - changed_at >= FRAME_SETTLE_INTERVAL:
                break
            if now >= deadline:
                break
            # This wait belongs only to the artifact thread, never Klipper's
            # reactor.
            time.sleep(FRAME_SAMPLE_INTERVAL)
        observed_time = time.time()
        return data, hashlib.sha256(data).hexdigest(), observed_time

    def _capture(self, number, label, metadata, settle):
        if settle:
            data, digest, observed_time = self._stable_frame()
        else:
            data = self._read_frame()
            observed_time = time.time()
            digest = hashlib.sha256(data).hexdigest()
        safe_label = "".join(
            char.lower() if char.isalnum() else "_" for char in label)
        safe_label = "_".join(filter(None, safe_label.split("_")))[:48]
        filename = "%03d-%s.jpg" % (number, safe_label or "screen")
        with open(os.path.join(self.run_directory, filename), "wb") as stream:
            stream.write(data)
        record = dict(metadata)
        record.update({
            "number": number, "label": label, "file": filename,
            "time": observed_time,
            "sha256": digest, "frame_bytes": len(data), "passed": True,
        })
        self.records.append(record)
        return record

    def _copy_printer_log(self):
        if not os.path.isfile(self.printer_log):
            return None
        current_size = os.path.getsize(self.printer_log)
        offset = self.log_start if current_size >= self.log_start else 0
        target = os.path.join(self.run_directory, "printer.log")
        with open(self.printer_log, "rb") as source:
            source.seek(offset)
            with open(target, "wb") as destination:
                shutil.copyfileobj(source, destination, 64 * 1024)
        return os.path.basename(target)

    def _finish(self, summary):
        summary = dict(summary)
        # Per-capture manifest fsyncs can stall the small printer host during
        # timing-sensitive probing. The worker already owns every record, so
        # publish the complete manifest once at its canonical finalization.
        _atomic_json(os.path.join(self.run_directory, "manifest.json"),
                     self.records)
        operation_context = summary.pop("_operation_context_artifact", None)
        if operation_context is not None:
            _atomic_json(os.path.join(
                self.run_directory, "operation_context.json"),
                operation_context)
        summary["screenshots"] = len(self.records)
        summary["printer_log"] = self._copy_printer_log()
        # The log slice is complete only here, after cleanup has run, so this
        # is the one place that can still turn a passed run into a failure.
        if summary["printer_log"] is not None:
            failures = printer_log_failures(os.path.join(
                self.run_directory, summary["printer_log"]))
            summary["printer_log_failures"] = failures[:MAX_LOG_FAILURES]
            if failures:
                error = "%d fatal printer log line(s); first at line %d: %s" % (
                    len(failures), failures[0]["line"], failures[0]["text"])
                summary["failures"] = list(summary.get("failures", ())) + [
                    {"step": "printer-log", "error": error}]
                if summary.get("outcome") == "passed":
                    summary["outcome"] = "failed"
                    summary["reason"] = error
        _atomic_json(os.path.join(self.run_directory, "summary.json"), summary)
        try:
            os.unlink(ACTIVE_MARKER)
        except OSError:
            pass
        self._retain()
        return summary

    def _retain(self):
        root = os.path.dirname(self.run_directory)
        entries = []
        try:
            names = sorted(os.listdir(root))
        except OSError:
            return
        for name in names:
            path = os.path.join(root, name)
            if path == self.run_directory or not os.path.isdir(path):
                continue
            if re.match(
                    r"^\d{8}-\d{6}-\d{6}-(full|ui|leak|component|render|motion|heat|screws|mesh|z|context_print|context_material)$",
                    name) is None:
                continue
            summary_path = os.path.join(path, "summary.json")
            if not os.path.isfile(summary_path):
                continue
            try:
                with open(summary_path, "r", encoding="utf-8") as stream:
                    outcome = json.load(stream).get("outcome")
            except Exception:
                outcome = "failed"
            entries.append({
                "path": path, "name": name, "size": _directory_size(path),
                "failed": outcome != "passed",
            })
        # Preserve the newest failed run for post-mortem inspection.
        newest_failed = next((item["path"] for item in reversed(entries)
                              if item["failed"]), None)
        total = sum(item["size"] for item in entries)
        while entries and (len(entries) + 1 > MAX_RUNS
                           or total + _directory_size(
                               self.run_directory) > MAX_BYTES):
            victim = next((item for item in entries
                           if item["path"] != newest_failed), None)
            if victim is None:
                break
            entries.remove(victim)
            total -= victim["size"]
            shutil.rmtree(victim["path"])
