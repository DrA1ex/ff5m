## Shared background worker and USB lifecycle contracts.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import queue
import threading
import time
import unittest

from tests.test_feather_workflows import FILES, UsbEventSocket, UsbProcess


class UsbWorkerTest(unittest.TestCase):
    def setUp(self):
        self.callbacks = queue.Queue()
        self.worker = FILES.FileWorker(self.callbacks.put)
        self.commands = []
        self.io_threads = []
        self.processes = queue.Queue()
        self.sockets = []
        self.mounted = False
        self.releases = []
        self.monitor = FILES.UsbStorageMonitor(
            "/data", self.worker, popen=self._popen,
            is_mount=self._is_mount, socket_factory=self._socket)

    def tearDown(self):
        for release in self.releases:
            release.set()
        self.worker.stop()
        self.worker._thread.join(3.0)
        self.assertFalse(self.worker._thread.is_alive())

    def _record_io(self):
        self.io_threads.append(threading.get_ident())

    def _popen(self, command, **kwargs):
        self._record_io()
        self.commands.append(command)
        if command[1] == "detach":
            return UsbProcess("DETACHED\n")
        process = self.processes.get_nowait()
        original_poll = process.poll
        original_communicate = process.communicate

        def poll():
            self._record_io()
            return original_poll()

        def communicate(timeout=None):
            self._record_io()
            return original_communicate(timeout=timeout)

        process.poll = poll
        process.communicate = communicate
        return process

    def _is_mount(self, path):
        self._record_io()
        return self.mounted

    def _socket(self, *args):
        self._record_io()
        events = UsbEventSocket()
        for method in ("setsockopt", "bind", "setblocking", "recv", "close"):
            original = getattr(events, method)

            def call(*args, original=original):
                self._record_io()
                return original(*args)

            setattr(events, method, call)
        self.sockets.append(events)
        return events

    def _barrier(self):
        completed = []
        self.assertTrue(self.worker.submit(
            lambda: "barrier",
            lambda value, error: completed.append((value, error))))
        self.callbacks.get(timeout=3.0)(0.0)
        self.assertEqual(completed, [("barrier", None)])

    def _attach(self, device="/dev/sda1"):
        self.processes.put(UsbProcess("ATTACHED %s vfat\n" % device))
        self.mounted = True
        self.monitor.resume(0.0)
        self._barrier()
        self._barrier()
        self.assertTrue(self.monitor.tick(0.0))
        self.assertTrue(self.monitor.available)

    def test_socket_helper_and_mount_io_share_the_existing_file_thread(self):
        self._attach()
        self.monitor.pause()
        self._barrier()
        self.assertTrue(self.sockets[-1].closed)
        self.assertTrue(self.monitor.available)
        self.assertEqual([command[1] for command in self.commands], ["attach"])
        self.monitor.stop()
        self._barrier()
        self.assertFalse(self.monitor.available)
        self.assertEqual([command[1] for command in self.commands], ["attach", "detach"])
        self.assertEqual(set(self.io_threads), {self.worker._thread.ident})

    def test_pause_tick_resume_and_stop_return_while_socket_close_is_blocked(self):
        self._attach()
        entered = threading.Event()
        release = threading.Event()
        self.releases.append(release)
        events = self.sockets[-1]
        close = events.close

        def blocked_close():
            entered.set()
            if not release.wait(3.0):
                raise RuntimeError("test did not release socket close")
            close()

        events.close = blocked_close
        self.monitor.pause()
        self.assertTrue(entered.wait(3.0))
        self.assertFalse(self.monitor.tick(0.0))
        self.monitor.resume(0.0)
        self.monitor.pause()
        self.monitor.stop()
        self.worker.stop()
        self.assertFalse(release.is_set())
        release.set()
        self.worker._thread.join(3.0)
        self.assertFalse(self.worker._thread.is_alive())
        self.assertEqual([command[1] for command in self.commands], ["attach", "detach"])

    def test_pause_during_helper_result_rejects_the_late_snapshot(self):
        entered = threading.Event()
        release = threading.Event()
        self.releases.append(release)
        process = UsbProcess("ATTACHED /dev/sda1 vfat\n")
        communicate = process.communicate

        def blocked_result(timeout=None):
            entered.set()
            if not release.wait(3.0):
                raise RuntimeError("test did not release helper result")
            return communicate(timeout=timeout)

        process.communicate = blocked_result
        self.processes.put(process)
        self.mounted = True
        self.monitor.resume(0.0)
        self.assertTrue(entered.wait(3.0))
        self.monitor.pause()
        self.monitor.resume(0.0)
        self.processes.put(UsbProcess("NONE\n", returncode=2))
        self.mounted = False
        release.set()
        self._barrier()
        self._barrier()
        self.assertFalse(self.monitor.tick(0.0))
        self.assertFalse(self.monitor.available)
        self.assertIsNone(self.monitor.device)
        self.assertEqual(len(self.sockets), 2)

    def test_stop_during_file_task_cleans_usb_and_discards_queued_files(self):
        self._attach()
        entered = threading.Event()
        release = threading.Event()
        self.releases.append(release)
        ran = []

        def running_file():
            entered.set()
            release.wait(3.0)
            return "running"

        self.worker.submit(running_file, lambda value, error: ran.append(value))
        self.assertTrue(entered.wait(3.0))
        self.worker.submit(lambda: ran.append("obsolete"), lambda *args: None)
        self.worker.submit(lambda: ran.append("newest"), lambda *args: None)
        self.monitor.stop()
        self.worker.stop()
        release.set()
        self.worker._thread.join(3.0)
        self.assertFalse(self.worker._thread.is_alive())
        self.assertEqual(ran, [])
        self.assertTrue(self.sockets[-1].closed)
        self.assertEqual([command[1] for command in self.commands], ["attach", "detach"])

    def test_unchanged_snapshots_and_repeated_modes_do_not_repeat_usb_io(self):
        self._attach()
        io_count = len(self.io_threads)
        for _ in range(20):
            self.monitor.resume(0.0)
            self.assertFalse(self.monitor.tick(0.0))
        self.assertEqual(len(self.io_threads), io_count)
        self.monitor.pause()
        self._barrier()
        socket_count = len(self.sockets)
        for _ in range(20):
            self.monitor.pause()
            self.assertFalse(self.monitor.tick(0.0))
        self._barrier()
        self.assertEqual(len(self.sockets), socket_count)
        self.assertEqual(len(self.commands), 1)

    def test_result_already_queued_to_reactor_is_suppressed_after_worker_stop(self):
        received = []
        self.worker.submit(lambda: "obsolete", lambda value, error: received.append(value))
        callback = self.callbacks.get(timeout=3.0)
        self.worker.stop()
        callback(0.0)
        self.assertEqual(received, [])


class ServiceSchedulingTest(unittest.TestCase):
    def test_failed_write_is_logged_and_later_writes_still_run(self):
        done = threading.Event()
        worker = FILES.FileWorker(lambda callback: None)
        try:
            def fail():
                raise OSError("disk full")

            with self.assertLogs(level="ERROR"):
                worker.submit_write(fail)
                worker.submit_write(done.set)
                self.assertTrue(done.wait(3.0))
        finally:
            worker.stop()
            worker._thread.join(3.0)

    def test_writes_survive_preview_replacement_and_drain_on_stop(self):
        entered, release = threading.Event(), threading.Event()
        writes = []
        worker = FILES.FileWorker(lambda callback: None)
        try:
            def busy():
                entered.set()
                release.wait(3.0)

            worker.submit(busy, lambda *args: None)
            self.assertTrue(entered.wait(3.0))
            worker.submit_write(lambda: writes.append((1, threading.get_ident())))
            worker.submit(lambda: writes.append(("old preview", 0)), lambda *args: None)
            worker.submit_write(lambda: writes.append((2, threading.get_ident())))
            worker.submit(lambda: writes.append(("new preview", 0)), lambda *args: None)
            worker.stop()
            self.assertFalse(worker.submit_write(lambda: None))
            release.set()
            worker._thread.join(3.0)
            self.assertFalse(worker._thread.is_alive())
            self.assertEqual(writes, [(1, worker._thread.ident), (2, worker._thread.ident)])
        finally:
            release.set()
            worker.stop()
            worker._thread.join(3.0)

    def test_deadline_wakes_an_idle_worker_and_shutdown_closes_each_service(self):
        calls = queue.Queue()

        class Service:
            def poll(self, now):
                calls.put(("poll", now, threading.get_ident()))
                return now + 0.02

            def close(self):
                calls.put(("close", time.monotonic(), threading.get_ident()))

        worker = FILES.FileWorker(lambda callback: None)
        try:
            self.assertTrue(worker.add_service(Service()))
            first = calls.get(timeout=3.0)
            second = calls.get(timeout=3.0)
            self.assertEqual((first[0], second[0]), ("poll", "poll"))
            self.assertGreaterEqual(second[1] - first[1], 0.01)
            worker.stop()
            worker._thread.join(3.0)
            self.assertFalse(worker._thread.is_alive())
            records = [first, second]
            while not calls.empty():
                records.append(calls.get_nowait())
            self.assertEqual(records[-1][0], "close")
            self.assertEqual(set(record[2] for record in records), {worker._thread.ident})
            self.assertFalse(worker.add_service(Service()))
        finally:
            worker.stop()
            worker._thread.join(3.0)

    def test_a_failed_service_does_not_prevent_other_service_or_file_work(self):
        callbacks = queue.Queue()
        closed = []

        class FailingService:
            def poll(self, now):
                raise OSError("unavailable")

            def close(self):
                closed.append("failing")
                raise OSError("cleanup unavailable")

        class HealthyService:
            def poll(self, now):
                return None

            def close(self):
                closed.append("healthy")

        worker = FILES.FileWorker(callbacks.put)
        try:
            with self.assertLogs(level="ERROR"):
                worker.add_service(FailingService())
                worker.add_service(HealthyService())
                worker.submit(lambda: "ok", lambda value, error: closed.append(value))
                callbacks.get(timeout=3.0)(0.0)
                worker.stop()
                worker._thread.join(3.0)
            self.assertEqual(closed, ["ok", "failing", "healthy"])
        finally:
            worker.stop()
            worker._thread.join(3.0)
