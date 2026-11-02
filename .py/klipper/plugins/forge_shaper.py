# Bounded acquisition and native input-shaper calibration.
# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
# This file may be distributed under the terms of the GNU GPLv3 license.

import collections
import importlib
import logging
import math
import os
import re
import shlex
import shutil
import struct
import subprocess
import tempfile
import time

from .shaper_storage import METHODS, REFERENCE_METHODS, ResultHistory, disk_directory, load_result

MAX_PENDING_BYTES = 64 * 1024
SAMPLE = struct.Struct("<4d")
PLOT_HISTORY_SIZE = 5

class NativeProcess:
    """One finite child, polled cooperatively; no result objects travel in pipes."""
    def __init__(self, reactor, args, log_path, capture=False):
        self.reactor = reactor
        self.log_path = log_path
        with open(log_path, "wb") as log:
            self.process = subprocess.Popen(args, stdin=subprocess.PIPE if capture else subprocess.DEVNULL,
                                            stdout=subprocess.DEVNULL, stderr=log, close_fds=True)

    def wait(self, cancelled, timeout):
        deadline = self.reactor.monotonic() + timeout
        while self.process.poll() is None:
            if cancelled():
                raise RuntimeError("Shaper calibration cancelled")
            now = self.reactor.monotonic()
            if now >= deadline:
                raise RuntimeError("Shaper worker timed out")
            self.reactor.pause(now + .05)
        if self.process.returncode:
            with open(self.log_path, "rb") as log:
                log.seek(0, os.SEEK_END)
                log.seek(max(0, log.tell() - 2048))
                detail = log.read().decode("utf-8", "replace").strip()
            raise RuntimeError("Shaper worker exited %s: %s" % (self.process.returncode, detail))

    def close(self):
        pipe = self.process.stdin
        if pipe is not None and not pipe.closed:
            pipe.close()
        if self.process.poll() is None:
            self.process.terminate()
            deadline = self.reactor.monotonic() + 1.
            while self.process.poll() is None and self.reactor.monotonic() < deadline:
                self.reactor.pause(self.reactor.monotonic() + .02)
            if self.process.poll() is None:
                self.process.kill()
        self.process.wait()


class CaptureClient:
    """An ADXL API client whose retained Python storage is bounded by one queue."""
    def __init__(self, reactor, executable, path, log_path):
        self.reactor = reactor
        self.worker = NativeProcess(reactor, [executable, "capture", path], log_path, capture=True)
        self.pending = bytearray()
        self.done = False
        self.error = None
        self.errors = self.overflows = 0
        self.samples = 0
        self.peak_pending = 0
        self.handler = None
        try:
            fd = self.worker.process.stdin.fileno()
            os.set_blocking(fd, False)
            self.handler = reactor.register_fd(fd, None, self._flush)
            reactor.set_fd_wake(self.handler, False, False)
        except Exception:
            self.worker.close()
            raise

    def get_client_connection(self):
        return self

    def get_dict(self, _name, _default):
        return {}

    def is_closed(self):
        return self.done or self.error is not None

    def send(self, message):
        if self.is_closed():
            return
        try:
            params = message["params"]
            errors, overflows = params.get("errors", 0), params.get("overflows", 0)
            self.errors, self.overflows = errors, overflows
            if self.errors or self.overflows:
                raise RuntimeError("Accelerometer reported errors or overflow")
            data = params["data"]
            if len(self.pending) + len(data) * SAMPLE.size > MAX_PENDING_BYTES:
                raise RuntimeError("Shaper capture queue full; storage cannot keep up")
            for row in data:
                self.pending.extend(SAMPLE.pack(*row))
            self.samples += len(data)
            self.peak_pending = max(self.peak_pending, len(self.pending))
            self._flush(self.reactor.monotonic())
        except Exception as error:
            self.error = str(error)

    def _flush(self, _eventtime):
        if self.error:
            return
        try:
            while self.pending:
                try:
                    count = os.write(self.worker.process.stdin.fileno(), self.pending)
                except BlockingIOError:
                    break
                if count <= 0:
                    raise RuntimeError("Shaper capture pipe closed")
                del self.pending[:count]
            self.reactor.set_fd_wake(self.handler, False, bool(self.pending))
        except Exception as error:
            self.error = str(error)
            self.reactor.set_fd_wake(self.handler, False, False)

    def finish(self, cancelled):
        self.done = True
        deadline = self.reactor.monotonic() + 5.
        while self.pending and not self.error:
            if cancelled():
                raise RuntimeError("Shaper calibration cancelled")
            self._flush(self.reactor.monotonic())
            if self.reactor.monotonic() >= deadline:
                raise RuntimeError("Shaper capture drain timed out")
            if self.pending:
                self.reactor.pause(self.reactor.monotonic() + .02)
        if self.error:
            raise RuntimeError(self.error)
        self.reactor.unregister_fd(self.handler)
        self.handler = None
        self.worker.process.stdin.close()
        self.worker.wait(cancelled, 5.)

    def close(self):
        self.done = True
        if self.handler is not None:
            self.reactor.unregister_fd(self.handler)
            self.handler = None
        self.worker.close()
        self.pending.clear()


class SweepCommand:
    """Check the job between sweep frequency reports without altering its moves."""
    def __init__(self, command, check, progress):
        self.command, self.check, self.progress = command, check, progress

    def __getattr__(self, name):
        return getattr(self.command, name)

    def respond_info(self, *args, **kwargs):
        self.check()
        if args:
            self.progress(args[0])
        return self.command.respond_info(*args, **kwargs)


class Job:
    """Progress of one native measurement, published until the next one starts."""
    def __init__(self, job_id=0, axes=(), started_at=None):
        self.id = job_id
        self.requested_axes, self.completed_axes = tuple(axes), ()
        self.axis = ""
        self.frequency = self.freq_start = self.freq_end = None
        self.samples = 0
        self.started_at, self.finished_at = started_at, None
        self.cancelled = False

    def elapsed(self, eventtime):
        if self.started_at is None:
            return 0.
        end = self.finished_at if self.finished_at is not None else eventtime
        return max(0., end - self.started_at)


# One saved history record, held in memory only while it is open.
Record = collections.namedtuple("Record", "id saved_at results")


def result_date(saved_at):
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(saved_at))


def format_results(record, axes):
    lines = ["Shaper result %s (%s)" % (record.id, result_date(record.saved_at))]
    for axis in axes:
        result = record.results[axis]
        lines.extend(("", "%s axis; square_corner_velocity %.1f mm/s" % (axis.upper(), result["scv"]),
                      "Shaper        Hz     Vibrations  Smoothing  Max accel (mm/s^2)"))
        for fit in result["shapers"]:
            mark = "*" if fit["name"] == result["recommended"] else " "
            note = " (reference only)" if fit["name"] in REFERENCE_METHODS else ""
            lines.append("%s%-10s %6.1f %9.2f%% %10.3f %10.0f%s" % (
                mark, fit["name"].upper(), fit["freq"], fit["vibrs"] * 100.,
                fit["smoothing"], fit["max_accel"], note))
    return "\n".join(lines + ["", "* Recommended."])


class ForgeShaper:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        self.executable = config.get("executable", "/opt/config/mod/.bin/exec/forge-shaper")
        self.directory = config.get("directory", "/opt/config/mod_data/shaper")
        self.timeout = config.getfloat("timeout", 180., above=0.)
        self.keep_raw = config.getboolean("keep_raw", False)
        self.plot_command = shlex.split(config.get(
            "plot_command", "/bin/bash /opt/config/mod/.shell/commands/zshaper.sh --plot"))
        # Saved results belong to the history on disk; only the open record
        # and the record it is compared with are kept in memory.
        self.history = ResultHistory(self.directory)
        self.record = self.reference = None
        self.revision = 0
        self.state, self.error, self.busy = "idle", "", False
        self.job = Job()
        self.worker = self.capture = None
        self.gcode.register_command("_FORGE_SHAPER_MEASURE", self.cmd_CALIBRATE,
                                    desc="Run bounded native shaper calibration")
        self.gcode.register_command("FORGE_SHAPER_APPLY", self.cmd_APPLY,
                                    desc="Stage a selected shaper for SAVE_CONFIG")
        self.gcode.register_command("FORGE_SHAPER_RESULTS", self.cmd_RESULTS,
                                    desc="Show saved shaper metrics without changing the selected result")
        self.gcode.register_command("FORGE_SHAPER_HISTORY", self.cmd_HISTORY,
                                    desc="List saved shaper result IDs, dates and measured axes")
        self.gcode.register_command("FORGE_SHAPER_CANCEL", lambda gcmd: self.cancel(),
                                    desc="Cancel a native shaper job at a sweep boundary")
        immediate = getattr(self.gcode, "register_immediate_command", None)
        if immediate is not None:
            immediate("FORGE_SHAPER_CANCEL")
        self.printer.register_event_handler("klippy:disconnect", self.disconnect)

    def get_history(self):
        return self.history.entries()

    def _open(self, entries, index, results=None):
        """Open entries[index] for inspection and FORGE_SHAPER_APPLY.

        The latest record is compared with its predecessor, an older record
        with the latest one.
        """
        entry = entries[index]
        if results is None:
            results, _ = self.history.load(entry["id"])
        reference = None
        if len(entries) > 1:
            other = entries[1 if index == 0 else 0]
            try:
                reference = Record(other["id"], other["saved_at"], self.history.load(other["id"])[0])
            except (OSError, ValueError, KeyError, struct.error) as error:
                # A damaged comparison record must not hide the selected one.
                logging.warning("[forge_shaper] Unable to load reference result: %s", error)
        self.record, self.reference = Record(entry["id"], entry["saved_at"], results), reference
        self.state, self.error = "complete", ""
        self.revision += 1

    def restore_saved(self, result_id=None):
        if self.busy:
            raise ValueError("Shaper calibration is running")
        entries = self.history.entries()
        if not entries:
            raise ValueError("No saved shaper result")
        index = next((i for i, e in enumerate(entries) if e["id"] == result_id), None) if result_id else 0
        if index is None:
            raise ValueError("Shaper result is no longer available")
        self._open(entries, index)

    def _read_result(self, result_id=None):
        """Inspection must not switch Feather's open record or its comparison."""
        if self.busy:
            raise ValueError("Shaper calibration is running")
        if result_id is None and self.record is not None:
            return self.record
        entries = self.history.entries()
        if not entries:
            raise ValueError("No saved shaper result")
        result_id = result_id or entries[0]["id"]
        results, saved_at = self.history.load(result_id)
        return Record(result_id, saved_at, results)

    @staticmethod
    def _result_axes(axis, record):
        axes = axis.lower() or "".join(sorted(record.results))
        if axes not in ("x", "y", "xy"):
            raise ValueError("AXIS must be x, y or xy")
        if any(a not in record.results for a in axes):
            raise ValueError("Requested axis is not present in this shaper result")
        return axes

    def cmd_RESULTS(self, gcmd):
        plot = gcmd.get_int("PLOT", 0, minval=0, maxval=1)
        try:
            record = self._read_result(gcmd.get("RESULT", None))
            axes = self._result_axes(gcmd.get("AXIS", ""), record)
            gcmd.respond_info(format_results(record, axes))
            if plot:
                self._require_idle(gcmd)
                gcmd.respond_info("Generating PNG graphs; this may take a minute. Please wait...")
                plots = self._export_plots(record, axes)
                gcmd.respond_info("PNG graphs created:\n%s\nOpen Configuration -> mod_data -> shaper -> plots." %
                                  "\n".join(plots))
        except (OSError, ValueError, KeyError, TypeError, struct.error, RuntimeError) as error:
            raise gcmd.error(str(error))

    def cmd_HISTORY(self, gcmd):
        try:
            entries = self.history.entries()
        except (OSError, ValueError) as error:
            raise gcmd.error(str(error))
        if not entries:
            gcmd.respond_info("No saved shaper result")
            return
        gcmd.respond_info("\n".join("%s  %s  %s%s%s" % (
            e["id"], result_date(e["saved_at"]), "".join(e["axes"]).upper(),
            " latest" if e["latest"] else "", " open" if self.record and e["id"] == self.record.id else "")
            for e in entries))

    def _export_plots(self, record, axes):
        if self.worker is not None:
            raise ValueError("Shaper worker is already running")
        directory = disk_directory(self.directory)
        plots = os.path.join(directory, "plots")
        os.makedirs(plots, exist_ok=True)
        job = tempfile.mkdtemp(prefix="plot-", dir=directory)
        created = []
        try:
            args = self.plot_command + [directory, record.id, "".join(axes), job]
            self._run_worker(args, os.path.join(job, "plot.log"), lambda: False)
            for axis in axes:
                source = os.path.join(job, axis + ".png")
                if not os.path.isfile(source) or not os.path.getsize(source):
                    raise RuntimeError("PNG renderer did not create a graph for axis %s" % axis.upper())
            stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime(record.saved_at))
            for axis in axes:
                path = os.path.join(plots, "shaper_%s_%s_%s.png" % (axis, stamp, record.id))
                os.replace(os.path.join(job, axis + ".png"), path)
                created.append(path)
                self._prune_plots(plots, axis, path)
        except (OSError, RuntimeError):
            logging.exception("[forge_shaper] PNG export failed for result %s", record.id)
            raise
        finally:
            shutil.rmtree(job, ignore_errors=True)
        return created

    @staticmethod
    def _prune_plots(directory, axis, current):
        # Only our dated images are managed; user files remain untouched.
        pattern = re.compile(r"shaper_%s_\d{8}_\d{6}_[0-9a-f]{64}\.png" % axis)
        paths = sorted((os.path.join(directory, name) for name in os.listdir(directory)
                        if pattern.fullmatch(name) and os.path.join(directory, name) != current), reverse=True)
        for path in paths[PLOT_HISTORY_SIZE - 1:]:
            os.unlink(path)
        legacy = os.path.join(directory, axis + ".png")
        if os.path.isfile(legacy):
            os.unlink(legacy)

    def _run_worker(self, args, log, cancelled):
        self.worker = NativeProcess(self.reactor, args, log)
        try:
            self.worker.wait(cancelled, self.timeout)
        finally:
            self.worker.close()
            self.worker = None

    def cancel(self):
        if self.busy:
            self.job.cancelled = True

    def disconnect(self):
        self.cancel()
        if self.capture:
            self.capture.close()
        if self.worker:
            self.worker.close()

    def _check(self):
        if self.job.cancelled:
            raise RuntimeError("Shaper calibration cancelled")
        if self.capture and self.capture.error:
            raise RuntimeError(self.capture.error)

    def _set_phase(self, phase):
        self.state = phase
        context = self.printer.lookup_object("operation_context", None)
        types = context.get_status(self.reactor.monotonic()).get("context_types", ()) if context else ()
        if types and types[-1] == "input_shaper":
            self.gcode.run_script_from_command("_CONTEXT_STATE NAME=" + phase.upper())

    def _require_idle(self, gcmd):
        now = self.reactor.monotonic()
        stats = self.printer.lookup_object("print_stats", None)
        sd = self.printer.lookup_object("virtual_sdcard", None)
        if (stats is not None and stats.get_status(now).get("state") in ("printing", "paused")) or (
                sd is not None and sd.is_active()):
            raise gcmd.error("Shaper measurement and graph generation are unavailable during a print")

    def cmd_CALIBRATE(self, gcmd):
        if self.busy or self.worker is not None:
            raise gcmd.error("Shaper calibration is already running")
        axes = gcmd.get("AXIS", "xy").lower()
        if axes not in ("x", "y", "xy"):
            raise gcmd.error("AXIS must be x, y or xy")
        self._require_idle(gcmd)
        tester = self.printer.lookup_object("resonance_tester")
        if len(tester.test.get_start_test_points()) != 1:
            raise gcmd.error("Native shaper calibration supports one probe point; use standard calibration for multiple points")
        axis_type = importlib.import_module(tester.__class__.__module__).TestAxis
        chips = {}
        for axis in axes:
            matching = [chip for chip_axis, chip in tester.accel_chips if axis_type(axis).matches(chip_axis)]
            if len(matching) != 1 or not hasattr(matching[0], "api_dump"):
                raise gcmd.error("Native shaper calibration requires one ADXL API sensor per axis")
            if matching[0].is_measuring():
                raise gcmd.error("Accelerometer is already in use")
            chips[axis] = matching[0]
        if not os.path.isfile(self.executable) or not os.access(self.executable, os.X_OK):
            raise gcmd.error("Native shaper executable is not installed")
        limit = gcmd.get_float("MAX_SMOOTHING", tester.max_smoothing or 0., minval=0.)
        try:
            directory = disk_directory(self.directory)
            job = tempfile.mkdtemp(prefix="job-", dir=directory)
        except (OSError, ValueError) as error:
            raise gcmd.error("Shaper storage unavailable: %s" % error)
        self.busy, self.state, self.error = True, "preparing", ""
        self.job = Job(self.job.id + 1, axes, self.reactor.monotonic())
        results = {}
        try:
            tester.test.prepare_test(gcmd)
            self.job.freq_start = tester.test.freq_start
            self.job.freq_end = tester.test.freq_end
            for axis in axes:
                self._check()
                self.job.axis, self.job.frequency, self.job.samples = axis, None, 0
                source = os.path.join(job, axis + ".samples")
                start, end = self._measure(gcmd, tester, axis_type(axis), chips[axis], source)
                self._check()
                self._set_phase("processing")
                output = os.path.join(job, axis + ".json")
                scv = self.printer.lookup_object("toolhead").square_corner_velocity
                args = [self.executable, "analyze", source, output, "--axis", axis,
                        "--scv", str(scv), "--max-smoothing", str(limit),
                        "--start", str(start), "--end", str(end)]
                self._run_worker(args, os.path.join(job, axis + ".analysis.log"), lambda: self.job.cancelled)
                results[axis] = load_result(output)
                if results[axis]["axis"] != axis:
                    raise RuntimeError("Shaper result axis mismatch")
                self.job.completed_axes = tuple(results)
                if not self.keep_raw:
                    os.unlink(source)
            self._check()
            entries = self.history.save(results, time.time())
            # No partial result is published and no printer setting changes automatically.
            self._open(entries, 0, results)
            for axis, result in results.items():
                best = next(s for s in result["shapers"] if s["name"] == result["recommended"])
                gcmd.respond_info("%s: recommended %s at %.1f Hz" % (
                    axis.upper(), best["name"], best["freq"]))
        except Exception as error:
            self.state = "cancelled" if self.job.cancelled else "error"
            self.error = str(error)
            raise gcmd.error(self.error)
        finally:
            if not self.keep_raw or self.state != "complete":
                shutil.rmtree(job, ignore_errors=True)
            try:
                self.printer.lookup_object("toolhead").wait_moves()
                self.gcode.run_script_from_command("M84")
            finally:
                self.job.finished_at = self.reactor.monotonic()
                self.busy = False

    def _sweep_progress(self, message):
        # VibrationPulseTest exposes frequency reports, not a progress hook.
        # Observe its bounded reports without changing the movement generator.
        if message.startswith("Testing frequency ") and message.endswith(" Hz"):
            try:
                frequency = float(message[len("Testing frequency "):-len(" Hz")])
            except ValueError:
                return
            if math.isfinite(frequency):
                self.job.frequency = max(self.job.freq_start, min(self.job.freq_end, frequency))

    def _measure(self, gcmd, tester, axis, chip, path):
        self._set_phase("positioning")
        toolhead = self.printer.lookup_object("toolhead")
        toolhead.manual_move(tester.test.get_start_test_points()[0], tester.move_speed)
        toolhead.wait_moves()
        toolhead.dwell(.5)
        before = toolhead.get_status(self.reactor.monotonic())
        start = toolhead.get_last_move_time()
        self.capture = CaptureClient(self.reactor, self.executable, path, path + ".log")
        try:
            chip.api_dump.add_client(self.capture)
            self._set_phase("measuring")
            try:
                tester.test.run_test(axis, SweepCommand(gcmd, self._check, self._sweep_progress))
            except Exception:
                self.gcode.run_script_from_command("SET_VELOCITY_LIMIT ACCEL=%.9g ACCEL_TO_DECEL=%.9g" % (
                    before["max_accel"], before["max_accel_to_decel"]))
                shaper = self.printer.lookup_object("input_shaper", None)
                if shaper is not None and not gcmd.get_int("INPUT_SHAPING", 0):
                    shaper.enable_shaping()
                raise
            end = toolhead.get_last_move_time()
            toolhead.wait_moves()
            self.capture.finish(lambda: self.job.cancelled)
            return start, end
        finally:
            self.job.samples = self.capture.samples
            self.capture.close()
            self.capture = None

    def cmd_APPLY(self, gcmd):
        method = gcmd.get("SHAPER", "auto").lower()
        try:
            record = self._read_result(gcmd.get("RESULT", None))
            axes = self._result_axes(gcmd.get("AXIS", ""), record)
            chosen = self._apply_shapers(record, axes, method)
        except (OSError, ValueError, KeyError, TypeError, struct.error) as error:
            raise gcmd.error(str(error))
        gcmd.respond_info("Staged %s. SAVE_CONFIG saves and restarts Klipper." % ", ".join(
            "%s: %s %.1f Hz" % (axis.upper(), fit["name"], fit["freq"]) for axis, fit in chosen))

    def _apply_shapers(self, record, axes, method):
        if method in REFERENCE_METHODS:
            raise ValueError("ZVD is reference only; firmware support has not been enabled")
        if method not in METHODS + ("auto",):
            raise ValueError("Unknown shaper method")
        # Validate every axis before staging either axis's settings.
        chosen = []
        for axis in axes:
            result = record.results[axis]
            name = result["recommended"] if method == "auto" else method
            fit = next((s for s in result["shapers"] if s["name"] == name), None)
            if name not in METHODS or fit is None or fit.get("reference_only", False):
                raise ValueError("No applicable %s shaper for axis %s" % (name, axis))
            chosen.append((axis, fit))
        configfile = self.printer.lookup_object("configfile")
        for axis, fit in chosen:
            configfile.set("input_shaper", "shaper_type_" + axis, fit["name"])
            configfile.set("input_shaper", "shaper_freq_" + axis, "%.1f" % fit["freq"])
        return chosen

    def get_status(self, eventtime):
        job = self.job
        return {"state": self.state, "error": self.error, "busy": self.busy,
                "revision": self.revision, "axes": sorted(self.record.results) if self.record else [],
                "job_id": job.id, "axis": job.axis,
                "requested_axes": job.requested_axes, "completed_axes": job.completed_axes,
                "frequency": job.frequency, "freq_start": job.freq_start, "freq_end": job.freq_end,
                "samples": self.capture.samples if self.capture else job.samples,
                "elapsed": job.elapsed(eventtime), "cancel_requested": job.cancelled}


def load_config(config):
    return ForgeShaper(config)
