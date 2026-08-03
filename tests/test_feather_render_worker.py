"""Concurrency and lifecycle contracts for the Feather Typer worker."""

import errno
import pathlib
import subprocess
import sys
import threading
import unittest
from unittest import mock


PLUGINS = pathlib.Path(__file__).parents[1] / ".py" / "klipper" / "plugins"
sys.path.insert(0, str(PLUGINS))

from ui import (  # noqa: E402
    FeatherRenderer, MAX_ATOMIC_DRAW, MAX_BATCHES, RenderBatch,
    RenderBatchQueue, TyperRenderWorker,
)


def batch(value, kind="state", key=None, generation=1, control=None):
    commands = (str(value),) if value is not None else ()
    return RenderBatch(
        commands, kind, key, generation,
        sum(len(command) for command in commands), control)


class RenderQueueTest(unittest.TestCase):
    def test_renderer_status_exposes_bounded_queue_diagnostics(self):
        status = FeatherRenderer().get_status()
        self.assertEqual(set((
            "worker_state", "queue_depth", "queue_capacity",
            "queue_high_watermark", "submitted_batches",
            "rendered_batches", "coalesced_batches", "dropped_batches",
            "typer_restarts", "worker_last_error",
        )) - set(status), set())
        self.assertEqual(status["queue_capacity"], MAX_BATCHES)

    def test_animation_pressure_is_bounded_and_final_state_survives(self):
        queue = RenderBatchQueue()
        for index in range(MAX_BATCHES):
            self.assertTrue(queue.put_nowait(batch(
                "animation-%d" % index, "animation", "anim-%d" % index)))

        self.assertTrue(queue.put_nowait(batch(
            "toggle-final", "state", "toggle:fan")))
        snapshot = queue.snapshot()
        self.assertLessEqual(snapshot["queue_depth"], MAX_BATCHES)
        self.assertEqual(snapshot["queue_high_watermark"], MAX_BATCHES)
        self.assertGreaterEqual(snapshot["coalesced_batches"], 1)
        queued = [queue.get() for _ in range(snapshot["queue_depth"])]
        self.assertIn(("toggle-final",), [item.commands for item in queued])

    def test_critical_evicts_ordinary_and_is_dequeued_first(self):
        queue = RenderBatchQueue()
        queue.put_nowait(batch("old state", "state", generation=2))
        queue.put_nowait(batch("old animation", "animation", "pulse", 2))
        queue.put_nowait(batch("shutdown", "critical", "shutdown", 2))
        queue.put_nowait(batch("another critical", "critical", "restart", 2))

        self.assertEqual(queue.snapshot()["queue_depth"], 2)
        self.assertEqual(queue.get().commands, ("shutdown",))
        self.assertEqual(queue.get().commands, ("another critical",))

    def test_surface_replaces_old_generation_and_rejects_late_frames(self):
        queue = RenderBatchQueue()
        queue.put_nowait(batch("page 3", "surface", generation=3))
        queue.put_nowait(batch("state 3", "state", "status", 3))
        queue.put_nowait(batch("page 4", "surface", generation=4))
        self.assertFalse(queue.put_nowait(batch(
            "late animation", "animation", "pulse", 3)))

        self.assertEqual(queue.snapshot()["queue_depth"], 1)
        current = queue.get()
        self.assertEqual((current.commands, current.generation),
                         (("page 4",), 4))

    def test_concurrent_publishers_never_exceed_capacity(self):
        queue = RenderBatchQueue()

        def publish(worker_id):
            for index in range(400):
                queue.put_nowait(batch(
                    "%d-%d" % (worker_id, index), "animation",
                    "worker-%d" % worker_id, index // 20))

        threads = [threading.Thread(target=publish, args=(index,))
                   for index in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        queue.put_nowait(batch("shutdown", "critical", "shutdown", 100))

        snapshot = queue.snapshot()
        self.assertLessEqual(snapshot["queue_depth"], MAX_BATCHES)
        self.assertLessEqual(snapshot["queue_high_watermark"], MAX_BATCHES)
        self.assertEqual(queue.get().commands, ("shutdown",))


class RenderWorkerTest(unittest.TestCase):
    @staticmethod
    def worker(queue=None, blending=False):
        renderer = FeatherRenderer()
        return TyperRenderWorker(
            queue or RenderBatchQueue(), renderer._encode_frames, False,
            ("typer", "/tmp/draw", "/tmp/event", "/dev/input/touch"),
            lambda callback: callback(0.0), lambda old, new: None,
            blending=blending)

    def test_renderer_enables_blending_for_typer_by_default(self):
        renderer = FeatherRenderer()
        renderer.configure_worker(
            lambda callback: callback(0.0), lambda old, new: None)

        with mock.patch.object(
                TyperRenderWorker, "start", return_value=True):
            renderer.start()

        self.assertTrue(renderer._worker.blending)

        disabled = FeatherRenderer(blending=False)
        disabled.configure_worker(
            lambda callback: callback(0.0), lambda old, new: None)
        with mock.patch.object(
                TyperRenderWorker, "start", return_value=True):
            disabled.start()
        self.assertFalse(disabled._worker.blending)

    def test_pollout_backpressure_stays_in_worker_and_frames_are_atomic(self):
        worker = self.worker()
        worker.draw_fd = 7
        worker.process = mock.Mock()
        worker.process.poll.return_value = None
        commands = tuple("--batch text -t %s" % ("x" * 100)
                         for _ in range(48))
        item = RenderBatch(
            commands, "surface", None, 1,
            sum(len(command) for command in commands), None)
        writes = []

        def write(_fd, payload):
            if not writes:
                writes.append(None)
                raise BlockingIOError(errno.EAGAIN, "full")
            value = bytes(payload)
            writes.append(value)
            return len(value)

        poller = mock.Mock()
        poller.poll.return_value = [(7, 4)]
        with mock.patch("ui.render_worker.os.write", side_effect=write), \
                mock.patch("ui.render_worker.select.poll", return_value=poller):
            worker._render(item)

        frames = [value for value in writes if value is not None]
        self.assertGreater(len(frames), 1)
        self.assertTrue(all(len(value) <= MAX_ATOMIC_DRAW
                            for value in frames))
        poller.poll.assert_called()

    def test_single_oversized_protocol_command_is_rejected_before_write(self):
        queue = RenderBatchQueue()
        worker = self.worker(queue)
        worker.draw_fd = 7
        worker.process = mock.Mock()
        worker.process.poll.return_value = None
        item = batch("x" * MAX_ATOMIC_DRAW, "state", "oversized")

        with self.assertRaisesRegex(ValueError, "MAX_ATOMIC_DRAW"):
            worker._render(item)
        self.assertEqual(queue.snapshot()["rendered_batches"], 0)

    def test_crash_recovery_retries_pending_batch_without_main_thread_waits(self):
        queue = RenderBatchQueue()
        queue.put_nowait(batch("final state", "state", "toggle", 4))
        worker = self.worker(queue)
        launches = []
        renders = []

        def launch():
            launches.append(True)
            worker.process = mock.Mock()
            worker.process.poll.return_value = None
            worker._set_state("running")

        def render(item):
            renders.append(item.commands)
            if len(renders) == 1:
                worker.process.poll.return_value = 1
                raise RuntimeError("typer crashed")
            queue.rendered()
            queue.close()

        worker._launch = launch
        worker._render = render
        worker._recover = lambda exc, failures: None
        worker._close_transport = lambda: None
        worker._stop_owned_process = lambda: None
        worker._run()

        self.assertEqual(len(launches), 2)
        self.assertEqual(renders, [("final state",), ("final state",)])
        self.assertEqual(queue.snapshot()["rendered_batches"], 1)

    def test_touch_fd_handoff_is_acknowledged_before_close(self):
        events = []
        worker = TyperRenderWorker(
            RenderBatchQueue(), FeatherRenderer()._encode_frames, False,
            ("typer", "/tmp/draw", "/tmp/event", "/dev/input/touch"),
            lambda callback: callback(0.0),
            lambda old, new: events.append(("unregister", old, new)))
        worker.event_fd = 10
        worker.draw_fd = 11
        with mock.patch(
                "ui.render_worker.os.close",
                side_effect=lambda fd: events.append(("close", fd))):
            worker._close_transport()
        self.assertEqual(events, [
            ("unregister", 10, None), ("close", 10), ("close", 11)])

    def test_hung_owned_typer_escalates_term_to_kill_in_worker(self):
        worker = self.worker()
        process = mock.Mock()
        process.poll.return_value = None
        process.wait.side_effect = [
            subprocess.TimeoutExpired("typer", 2.0), 0]
        worker.process = process

        worker._stop_owned_process()

        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_args_list, [
            mock.call(timeout=2.0), mock.call(timeout=1.0)])

    def test_orphan_is_waited_out_before_fifo_recreation(self):
        worker = self.worker()
        events = []
        waits = iter((False, True))
        process = mock.Mock()
        process.poll.return_value = None
        worker._wait_for_orphan = lambda timeout: (
            events.append(("wait", timeout)) or next(waits))
        worker._prepare_fifos = lambda: events.append("fifos")
        worker._schedule_and_wait = (
            lambda old, new: events.append(("handoff", old, new)))

        with mock.patch(
                "ui.render_worker.subprocess.call",
                side_effect=lambda args, **kwargs: events.append(tuple(args))), \
                mock.patch("ui.render_worker.os.open", side_effect=(10, 11)), \
                mock.patch("ui.render_worker.subprocess.Popen",
                           return_value=process) as popen:
            worker._launch()

        self.assertEqual(events[:5], [
            ("killall", "typer"), ("wait", 1.0),
            ("killall", "-9", "typer"), ("wait", 0.5), "fifos"])
        self.assertEqual(events[-1], ("handoff", None, 10))
        self.assertNotIn("--blending", popen.call_args.args[0])

    def test_configured_blending_is_forwarded_to_typer(self):
        worker = self.worker(blending=True)
        process = mock.Mock()
        process.poll.return_value = None
        worker._wait_for_orphan = lambda timeout: True
        worker._prepare_fifos = lambda: None
        worker._schedule_and_wait = lambda old, new: None

        with mock.patch("ui.render_worker.subprocess.call"), \
                mock.patch("ui.render_worker.os.open", side_effect=(10, 11)), \
                mock.patch("ui.render_worker.subprocess.Popen",
                           return_value=process) as popen:
            worker._launch()

        args = popen.call_args.args[0]
        self.assertIn("--blending", args)
        self.assertLess(args.index("--blending"), args.index("batch"))


if __name__ == "__main__":
    unittest.main()
