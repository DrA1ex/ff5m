## Framebuffer capture and report artifacts for Feather UI test runs.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import csv
import hashlib
import json
import logging
import os
import queue
import re
import shutil
import struct
import threading
import time
from datetime import datetime


ARTIFACT_ROOT = "/data/feather-ui-tests"
ACTIVE_MARKER = os.path.join(ARTIFACT_ROOT, "active.json")
FRAMEBUFFER = "/dev/fb0"
FRAMEBUFFER_PAN = "/sys/class/graphics/fb0/pan"
FRAMEBUFFER_STRIDE = "/sys/class/graphics/fb0/stride"
PRINTER_LOG = "/data/logFiles/printer.log"
SCREEN_WIDTH = 800
SCREEN_HEIGHT = 480
FRAME_BYTES_PER_PIXEL = 4
FRAME_BYTES = SCREEN_WIDTH * SCREEN_HEIGHT * 4
FRAME_PAN_RETRIES = 4
FRAME_SAMPLE_INTERVAL = 0.05
FRAME_SETTLE_INTERVAL = 0.25
FRAME_SETTLE_TIMEOUT = 3.0
MAX_RUNS = 10
MAX_BYTES = 512 * 1024 * 1024


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
    """Perform framebuffer and artifact IO away from Klipper's reactor."""

    def __init__(self, reactor, run_directory, framebuffer=FRAMEBUFFER,
                 printer_log=PRINTER_LOG, framebuffer_pan=FRAMEBUFFER_PAN,
                 framebuffer_stride=FRAMEBUFFER_STRIDE):
        self.reactor = reactor
        self.run_directory = run_directory
        self.framebuffer = framebuffer
        self.framebuffer_pan = framebuffer_pan
        self.framebuffer_stride = framebuffer_stride
        self.printer_log = printer_log
        # SimpleQueue.put() is unbounded and never waits for the artifact
        # thread. Reactor callbacks only enqueue short work descriptions;
        # framebuffer, telemetry and filesystem work remain off-thread.
        self.tasks = queue.SimpleQueue()
        self.records = []
        self.log_start = self._file_size(printer_log)
        # Cumulative capture accounting, so a reactor sample can distinguish a
        # queued request from framebuffer work that actually started while the
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

    def _read_pan(self):
        try:
            with open(self.framebuffer_pan, "r", encoding="ascii") as stream:
                fields = stream.read(64).strip().split(",")
        except OSError as exc:
            if self.framebuffer == FRAMEBUFFER:
                raise RuntimeError("Framebuffer pan is unavailable") from exc
            return 0, 0
        try:
            if len(fields) != 2:
                raise ValueError()
            xoffset, yoffset = (int(field) for field in fields)
        except ValueError as exc:
            raise RuntimeError("Framebuffer pan is invalid") from exc
        if xoffset < 0 or yoffset < 0:
            raise RuntimeError("Framebuffer pan is invalid")
        return xoffset, yoffset

    def _read_stride(self):
        try:
            with open(self.framebuffer_stride, "r",
                      encoding="ascii") as stream:
                stride = int(stream.read(64).strip())
        except OSError as exc:
            if self.framebuffer == FRAMEBUFFER:
                raise RuntimeError(
                    "Framebuffer stride is unavailable") from exc
            stride = SCREEN_WIDTH * FRAME_BYTES_PER_PIXEL
        except ValueError as exc:
            raise RuntimeError("Framebuffer stride is invalid") from exc
        if stride < SCREEN_WIDTH * FRAME_BYTES_PER_PIXEL:
            raise RuntimeError("Framebuffer stride is invalid")
        return stride

    def _read_frame_at(self, pan, stride):
        xoffset, yoffset = pan
        row_bytes = SCREEN_WIDTH * FRAME_BYTES_PER_PIXEL
        if (xoffset + SCREEN_WIDTH) * FRAME_BYTES_PER_PIXEL > stride:
            raise RuntimeError("Framebuffer pan exceeds its stride")
        rows = []
        with open(self.framebuffer, "rb", buffering=0) as stream:
            if stride == row_bytes and xoffset == 0:
                stream.seek(yoffset * stride)
                data = stream.read(FRAME_BYTES)
                if len(data) != FRAME_BYTES:
                    raise RuntimeError(
                        "Framebuffer has %d bytes, expected %d" %
                        (len(data), FRAME_BYTES))
                return data
            for row in range(SCREEN_HEIGHT):
                stream.seek(
                    (yoffset + row) * stride
                    + xoffset * FRAME_BYTES_PER_PIXEL)
                data = stream.read(row_bytes)
                if len(data) != row_bytes:
                    raise RuntimeError(
                        "Framebuffer row has %d bytes, expected %d" %
                        (len(data), row_bytes))
                rows.append(data)
        return b"".join(rows)

    def _read_frame(self):
        stride = self._read_stride()
        for _attempt in range(FRAME_PAN_RETRIES):
            before = self._read_pan()
            data = self._read_frame_at(before, stride)
            if self._read_pan() == before:
                self.last_frame_geometry = {
                    "xoffset": before[0], "yoffset": before[1],
                    "stride": stride,
                }
                return data
        raise RuntimeError("Framebuffer page changed during capture")

    def _stable_frame(self):
        deadline = time.monotonic() + FRAME_SETTLE_TIMEOUT
        previous = None
        changed_at = time.monotonic()
        data = None
        while True:
            data = self._read_frame()
            # Settling only needs to notice that the framebuffer moved, so it
            # uses the interpreter's own bytes hash: it runs in C over the
            # whole frame, costs a fraction of SHA-256 on the printer's
            # Cortex-A7 and keeps one integer instead of a second 1.5 MiB
            # frame to diff against.  zlib is absent from this Python build,
            # so crc32 is not available here.  The value is process-local by
            # design; the published digest is still SHA-256, taken once below
            # on the frame the loop accepts.
            token = hash(data)
            now = time.monotonic()
            if token != previous:
                previous = token
                changed_at = now
            elif now - changed_at >= FRAME_SETTLE_INTERVAL:
                break
            if now >= deadline:
                break
            # Typer may still be consuming later protocol frames after the
            # render worker has completed its FIFO writes.  Require a quiet
            # framebuffer window instead of accepting the first equal pair;
            # this wait belongs only to the artifact thread, never Klipper's
            # reactor.
            time.sleep(FRAME_SAMPLE_INTERVAL)
        observed_time = time.time()
        return data, hashlib.sha256(data).hexdigest(), observed_time

    @staticmethod
    def _bmp_header(data_size):
        size = 54 + data_size
        header = struct.pack("<2sIHHI", b"BM", size, 0, 0, 54)
        header += struct.pack(
            "<IiiHHIIIIII", 40, SCREEN_WIDTH, -SCREEN_HEIGHT,
            1, 32, 0, data_size, 2835, 2835, 0, 0)
        return header

    def _capture(self, number, label, metadata, settle):
        if settle:
            data, digest, observed_time = self._stable_frame()
        else:
            data = self._read_frame()
            observed_time = time.time()
            digest = hashlib.sha256(data).hexdigest()
        if not any(data):
            raise RuntimeError("Framebuffer is blank")
        safe_label = "".join(
            char.lower() if char.isalnum() else "_" for char in label)
        safe_label = "_".join(filter(None, safe_label.split("_")))[:48]
        filename = "%03d-%s.bmp" % (number, safe_label or "screen")
        with open(os.path.join(self.run_directory, filename), "wb") as stream:
            stream.write(self._bmp_header(len(data)))
            stream.write(data)
        record = dict(metadata)
        record.update({
            "number": number, "label": label, "file": filename,
            "time": observed_time,
            "sha256": digest, "frame_bytes": len(data), "passed": True,
            "framebuffer": getattr(self, "last_frame_geometry", None),
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
                    r"^\d{8}-\d{6}-\d{6}-(full|ui|component|render|motion|heat|screws|mesh|z|context_print|context_material)$",
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
