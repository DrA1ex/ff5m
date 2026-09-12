"""Asynchronous Typer transport and process lifecycle for Feather."""

## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import errno
import logging
import os
import select
import stat
import subprocess
import threading
import time
from collections import deque, namedtuple


MAX_BATCHES = 16
MAX_BATCH_BYTES = 64 * 1024
RENDER_STALL_TIMEOUT = 5.0
HANDOFF_TIMEOUT = 5.0
PRESENT_GUARD_US = 3000

RenderBatch = namedtuple(
    "RenderBatch",
    "commands kind key generation serialized_size control receipt",
    defaults=(None,))


class _RestartRequested(Exception):
    pass


class _ReactorStopped(RuntimeError):
    pass


class RenderBatchQueue:
    """A small priority/coalescing queue safe for reactor submissions."""

    def __init__(self, max_batches=MAX_BATCHES,
                 max_batch_bytes=MAX_BATCH_BYTES):
        self.max_batches = int(max_batches)
        self.max_batch_bytes = int(max_batch_bytes)
        self._items = deque()
        self._condition = threading.Condition()
        self._closed = False
        self._latest_generation = -1
        self._metrics = {
            "submitted_batches": 0,
            "rendered_batches": 0,
            "coalesced_batches": 0,
            "dropped_batches": 0,
            "queue_high_watermark": 0,
        }

    def _remove(self, predicate, coalesced=True):
        kept = deque()
        removed = 0
        while self._items:
            item = self._items.popleft()
            if predicate(item):
                removed += 1
            else:
                kept.append(item)
        self._items = kept
        if removed:
            metric = "coalesced_batches" if coalesced else "dropped_batches"
            self._metrics[metric] += removed
        return removed

    def _evict_one(self, kinds, coalesced=False):
        for index, item in enumerate(self._items):
            if item.kind in kinds:
                del self._items[index]
                metric = ("coalesced_batches" if coalesced
                          else "dropped_batches")
                self._metrics[metric] += 1
                return True
        return False

    def put_nowait(self, batch):
        """Publish without performing transport work or waiting for space."""
        with self._condition:
            self._metrics["submitted_batches"] += 1
            if self._closed or batch.serialized_size > self.max_batch_bytes:
                self._metrics["dropped_batches"] += 1
                return False

            if batch.kind == "critical":
                # A restart/error/shutdown surface owns the display. No
                # untouched ordinary frame may be painted after it.
                self._remove(
                    lambda queued: queued.kind != "critical",
                    coalesced=False)
            if batch.kind != "critical":
                if batch.generation < self._latest_generation:
                    self._metrics["dropped_batches"] += 1
                    return False
                if batch.kind == "surface":
                    self._latest_generation = max(
                        self._latest_generation, batch.generation)
                    # A complete surface makes every untouched update for the
                    # same or an older interaction generation obsolete.
                    self._remove(lambda queued: (
                        queued.kind != "critical"
                        and queued.generation <= batch.generation))
                elif batch.kind == "animation" and batch.key is not None:
                    self._remove(lambda queued: (
                        queued.kind == "animation"
                        and queued.key == batch.key
                        and queued.generation == batch.generation))
                elif batch.kind == "state" and batch.key is not None:
                    self._remove(lambda queued: (
                        queued.kind == "state"
                        and queued.key == batch.key
                        and queued.generation == batch.generation))

            while len(self._items) >= self.max_batches:
                if batch.kind == "animation":
                    self._metrics["dropped_batches"] += 1
                    return False
                if self._evict_one(("animation",), coalesced=True):
                    continue
                if batch.kind == "critical":
                    if self._evict_one(("state", "surface")):
                        continue
                    # Critical messages are latest-wins when the queue is
                    # composed entirely of untouched critical messages.
                    self._items.popleft()
                    self._metrics["coalesced_batches"] += 1
                    continue
                if batch.kind == "surface":
                    if self._evict_one(("state", "surface")):
                        continue
                elif batch.kind == "state":
                    if self._evict_one(("state",)):
                        continue
                self._metrics["dropped_batches"] += 1
                return False

            self._items.append(batch)
            depth = len(self._items)
            self._metrics["queue_high_watermark"] = max(
                self._metrics["queue_high_watermark"], depth)
            self._condition.notify()
            return True

    def reject_submission(self):
        """Account for a batch rejected before an immutable item exists."""
        with self._condition:
            self._metrics["submitted_batches"] += 1
            self._metrics["dropped_batches"] += 1

    def get(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while not self._items and not self._closed:
                remaining = (None if deadline is None
                             else max(0.0, deadline - time.monotonic()))
                if remaining == 0.0:
                    return None
                self._condition.wait(remaining)
            if not self._items:
                return None
            # Safety and lifecycle batches jump over queued visual updates.
            for index, item in enumerate(self._items):
                if item.kind == "critical":
                    del self._items[index]
                    return item
            return self._items.popleft()

    def discard_noncritical(self):
        with self._condition:
            self._remove(lambda item: item.kind != "critical", coalesced=False)

    def rendered(self):
        with self._condition:
            self._metrics["rendered_batches"] += 1

    def drop_render(self):
        with self._condition:
            self._metrics["dropped_batches"] += 1

    def obsolete(self, batch):
        with self._condition:
            return (batch.kind != "critical"
                    and batch.generation < self._latest_generation)

    def has_critical(self):
        with self._condition:
            return any(item.kind == "critical" for item in self._items)

    def close(self):
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    @property
    def closed(self):
        with self._condition:
            return self._closed

    def snapshot(self):
        with self._condition:
            result = dict(self._metrics)
            result["queue_depth"] = len(self._items)
            result["queue_capacity"] = self.max_batches
            return result


class TyperRenderWorker:
    """Own Typer, encode batches, and block on FIFO readiness off-reactor."""

    def __init__(self, batch_queue, encode_frames, debug, paths,
                 schedule_async, event_fd_changed, restarted=None,
                 font_loader=None, blending=False,
                 raster_acceleration="scalar"):
        self.queue = batch_queue
        self.encode_frames = encode_frames
        self.debug = bool(debug)
        (self.typer_binary, self.draw_pipe, self.event_pipe,
         self.touch_device) = paths
        self.schedule_async = schedule_async
        self.event_fd_changed = event_fd_changed
        self.restarted = restarted
        self.font_loader = font_loader
        self.blending = bool(blending)
        self.raster_acceleration = str(raster_acceleration).strip().lower()
        if self.raster_acceleration not in ("scalar", "neon"):
            raise ValueError("invalid Typer raster acceleration: %s" %
                             raster_acceleration)
        self.thread = None
        self.process = None
        self.draw_fd = None
        self.event_fd = None
        self._state_lock = threading.Lock()
        self._state = "stopped"
        self._last_error = ""
        self._restart_count = 0
        self._ever_started = False
        self._restart_requested = False

    def _set_state(self, state, error=None):
        with self._state_lock:
            self._state = state
            if error is not None:
                self._last_error = str(error)

    @property
    def active(self):
        with self._state_lock:
            return self._state == "running"

    def snapshot(self):
        with self._state_lock:
            result = {
                "worker_state": self._state,
                "typer_restarts": self._restart_count,
                "worker_last_error": self._last_error,
            }
        result.update(self.queue.snapshot())
        return result

    def start(self):
        with self._state_lock:
            if self.thread is not None:
                return False
            self._state = "starting"
            self.thread = threading.Thread(
                target=self._run, name="feather-typer-render")
            self.thread.daemon = True
            self.thread.start()
            return True

    def request_restart(self):
        batch = RenderBatch(
            (), "critical", "restart", -1, 0, "restart", None)
        return self.queue.put_nowait(batch)

    def request_stop(self):
        self.queue.close()

    @staticmethod
    def _make_fifo(path):
        if os.path.exists(path) and not stat.S_ISFIFO(os.stat(path).st_mode):
            os.unlink(path)
        if not os.path.exists(path):
            os.mkfifo(path, 0o666)

    @staticmethod
    def _typer_is_running():
        try:
            entries = os.listdir("/proc")
        except OSError:
            return False
        for entry in entries:
            if not entry.isdigit():
                continue
            try:
                with open("/proc/%s/comm" % entry, "r") as stream:
                    if stream.read().strip() == "typer":
                        return True
            except OSError:
                continue
        return False

    @classmethod
    def _wait_for_orphan(cls, timeout):
        deadline = time.monotonic() + timeout
        while cls._typer_is_running():
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                return False
            threading.Event().wait(min(0.05, remaining))
        return True

    def _schedule_and_wait(self, old_fd, new_fd):
        acknowledged = threading.Event()
        callback_error = []

        def deliver(eventtime):
            try:
                self.event_fd_changed(old_fd, new_fd)
            except Exception as exc:
                callback_error.append(exc)
            finally:
                acknowledged.set()

        try:
            self.schedule_async(deliver)
        except (TypeError, OSError) as exc:
            raise _ReactorStopped("reactor is stopped") from exc
        if not acknowledged.wait(HANDOFF_TIMEOUT):
            raise RuntimeError("reactor did not acknowledge touch FIFO handoff")
        if callback_error:
            raise RuntimeError(
                "reactor rejected touch FIFO handoff: %s" % callback_error[0])

    def _schedule_restart_notice(self):
        if self.restarted is None:
            return

        acknowledged = threading.Event()
        callback_error = []

        def deliver(eventtime):
            try:
                self.restarted()
            except Exception as exc:
                callback_error.append(exc)
            finally:
                acknowledged.set()

        try:
            self.schedule_async(deliver)
        except (TypeError, OSError) as exc:
            raise _ReactorStopped("reactor is stopped") from exc
        if not acknowledged.wait(HANDOFF_TIMEOUT):
            raise RuntimeError("reactor did not acknowledge typer restart")
        if callback_error:
            raise RuntimeError(
                "reactor rejected typer restart redraw: %s" %
                callback_error[0])

    def _stop_owned_process(self):
        process = self.process
        self.process = None
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1.0)
        except Exception as exc:
            logging.warning("[feather_screen] unable to stop typer: %s", exc)

    def _close_transport(self):
        if self.event_fd is not None:
            old_fd = self.event_fd
            # The reactor must stop reading the descriptor before it is closed
            # and before Typer can unlink/recreate the FIFO path.
            self._schedule_and_wait(old_fd, None)
            os.close(old_fd)
            self.event_fd = None
        if self.draw_fd is not None:
            os.close(self.draw_fd)
            self.draw_fd = None

    def _prepare_fifos(self):
        for path in (self.draw_pipe, self.event_pipe):
            try:
                os.unlink(path)
            except OSError as exc:
                if exc.errno != errno.ENOENT:
                    raise
        self._make_fifo(self.draw_pipe)
        self._make_fifo(self.event_pipe)

    def _launch(self):
        self._set_state("starting")
        self._close_transport()
        self._stop_owned_process()
        # Remove an orphan left by a previous Klippy process. This and all
        # waiting happen exclusively in the render worker.
        subprocess.call(["killall", "typer"], stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL)
        if not self._wait_for_orphan(1.0):
            subprocess.call(
                ["killall", "-9", "typer"], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
            if not self._wait_for_orphan(0.5):
                raise RuntimeError("old typer did not exit")
        self._prepare_fifos()
        event_fd = os.open(self.event_pipe, os.O_RDWR | os.O_NONBLOCK)
        args = [self.typer_binary]
        if self.debug:
            args.append("--debug")
        args += ["--deferred-page-publish", "auto",
                 "--present-guard-us", str(PRESENT_GUARD_US)]
        if self.blending:
            args.append("--blending")
        if self.raster_acceleration != "scalar":
            args += ["--raster-acceleration", self.raster_acceleration]
        args += ["--double-buffered", "--touch-device", self.touch_device,
                 "--event-pipe", self.event_pipe, "batch", "--pipe",
                 self.draw_pipe]
        try:
            process = subprocess.Popen(
                args, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
            draw_fd = os.open(self.draw_pipe, os.O_RDWR | os.O_NONBLOCK)
        except Exception:
            os.close(event_fd)
            raise
        self.process = process
        self.draw_fd = draw_fd
        self.event_fd = event_fd
        try:
            self._schedule_and_wait(None, event_fd)
        except Exception:
            self._close_transport()
            self._stop_owned_process()
            raise
        if self._ever_started:
            with self._state_lock:
                self._restart_count += 1
            self._schedule_restart_notice()
        self._ever_started = True
        self._set_state("running")
        logging.info("[feather_screen] typer render worker started")

    def _process_alive(self):
        return self.process is not None and self.process.poll() is None

    def _write_frame(self, frame):
        view = memoryview(frame)
        deadline = time.monotonic() + RENDER_STALL_TIMEOUT
        poller = select.poll()
        poller.register(self.draw_fd, select.POLLOUT | select.POLLERR |
                        select.POLLHUP)
        while view:
            if not self._process_alive():
                raise RuntimeError("typer exited while rendering")
            try:
                written = os.write(self.draw_fd, view)
                if written <= 0:
                    raise RuntimeError("typer draw FIFO closed")
                view = view[written:]
                deadline = time.monotonic() + RENDER_STALL_TIMEOUT
                continue
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                    raise
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                raise RuntimeError("typer draw FIFO stalled")
            events = poller.poll(max(1, int(remaining * 1000.0)))
            if not events:
                raise RuntimeError("typer draw FIFO stalled")
            if events[0][1] & (select.POLLERR | select.POLLHUP):
                raise RuntimeError("typer draw FIFO closed")

    def _render(self, batch):
        if batch.receipt is None:
            frames = self.encode_frames(batch.commands)
        else:
            frames = self.encode_frames(batch.commands, batch.receipt)
        for frame in frames:
            self._write_frame(frame)
        self.queue.rendered()

    def _recover(self, exc, failures):
        expected_restart = isinstance(exc, _RestartRequested)
        if expected_restart:
            logging.info("[feather_screen] typer render worker restarting")
            self._set_state("backoff")
        else:
            logging.exception("[feather_screen] typer render worker recovery")
            self._set_state("backoff", exc)
        try:
            self._close_transport()
        except _ReactorStopped:
            raise
        except Exception as close_exc:
            self._set_state("backoff", close_exc)
        self._stop_owned_process()
        delay = (0.0 if expected_restart else
                 min(5.0, 0.25 * (2 ** min(failures, 5))))
        deadline = time.monotonic() + delay
        while not self.queue.closed and time.monotonic() < deadline:
            # A condition wait is interruptible by a critical restart/stop and
            # does not spin or sleep in the Klipper reactor.
            with self.queue._condition:
                self.queue._condition.wait(
                    max(0.0, deadline - time.monotonic()))
            if self.queue.has_critical():
                break

    def _run(self):
        pending = None
        failures = 0
        try:
            if self.font_loader is not None:
                self.font_loader()
            while not self.queue.closed:
                try:
                    if not self._process_alive():
                        self._launch()
                        failures = 0
                    if pending is None:
                        pending = self.queue.get(timeout=0.5)
                        if pending is None:
                            continue
                    if pending.control == "restart":
                        self.queue.rendered()
                        pending = None
                        raise _RestartRequested()
                    if self.queue.obsolete(pending):
                        self.queue.drop_render()
                    else:
                        try:
                            self._render(pending)
                        except ValueError as exc:
                            self.queue.drop_render()
                            self._set_state("running", exc)
                    pending = None
                except _ReactorStopped:
                    self.queue.close()
                    break
                except _RestartRequested as exc:
                    self._recover(exc, failures)
                except Exception as exc:
                    failures += 1
                    # The process may have consumed an arbitrary prefix of the
                    # current logical batch. Ordinary state is replaced by a
                    # fresh complete surface from the acknowledged restart
                    # callback. A critical frozen surface is the exception:
                    # after the old process has been stopped, replaying that
                    # immutable batch into the new Typer is both safe and
                    # necessary because the controller intentionally refuses
                    # to replace it with an ordinary page.
                    if pending is not None and pending.kind != "critical":
                        self.queue.drop_render()
                        pending = None
                    self.queue.discard_noncritical()
                    self._recover(exc, failures)
        finally:
            self._set_state("stopping")
            try:
                self._close_transport()
            except _ReactorStopped:
                pass
            except Exception:
                logging.exception("[feather_screen] touch FIFO cleanup failed")
            self._stop_owned_process()
            self._set_state("stopped")
