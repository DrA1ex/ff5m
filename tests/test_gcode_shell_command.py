"""Concurrency contracts for the Forge-X G-code shell command runner."""

import importlib.util
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]
PLUGIN_PATH = (ROOT / ".py" / "klipper" / "patches" / "extras"
               / "gcode_shell_command.py")
SPEC = importlib.util.spec_from_file_location(
    "test_gcode_shell_command_plugin", PLUGIN_PATH)
PLUGIN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PLUGIN)


class ResponseHarness:
    def __init__(self):
        self.messages = []
        self.received = threading.Event()

    def register_async_callback(self, callback):
        callback(0.)

    def respond_raw(self, message):
        self.messages.append(message)
        self.received.set()

    def error(self, message):
        return RuntimeError(message)


class PrinterHarness:
    def __init__(self, responses):
        self.responses = responses

    def get_reactor(self):
        return self.responses

    def lookup_object(self, name):
        assert name == "gcode"
        return self.responses


class GcodeShellCommandTest(unittest.TestCase):
    def _helper(self, mode, timeout=2., linewise=True):
        responses = ResponseHarness()
        helper = PLUGIN.AsyncRunHelper.__new__(PLUGIN.AsyncRunHelper)
        helper.mode = mode
        helper.timeout = timeout
        helper.name = "test"
        helper.linewise = linewise
        helper.reactor = responses
        helper.gcode = responses
        helper.cmd = types.SimpleNamespace(verbose=True, debug=False)
        helper._terminate = False
        return helper, responses

    def _live_helper(self, mode, timeout=2., linewise=True,
                     verbose=True):
        responses = ResponseHarness()
        command = types.SimpleNamespace(
            mode=mode, timeout=timeout, name="test", linewise=linewise,
            verbose=verbose, debug=False)
        helper = PLUGIN.AsyncRunHelper(PrinterHarness(responses), command)
        return helper, responses

    def _task(self, command):
        proc = subprocess.Popen(
            ["sh", "-c", command], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True)
        task = PLUGIN.AsyncRunHelper.Task(program=[], args=[])
        task.proc = proc
        return task

    def _close_task(self, task):
        if task.proc.stdin is not None:
            task.proc.stdin.close()
        if task.proc.stdout is not None:
            task.proc.stdout.close()

    def _wait_until(self, predicate, timeout=2.):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(.01)
        return predicate()

    def _stop_helper(self, helper):
        helper.shutdown()
        if helper._thread is not None:
            helper._thread.join(3.)
            self.assertFalse(helper._thread.is_alive())

    def test_stream_publishes_complete_lines_before_process_exit(self):
        helper, responses = self._helper(PLUGIN.ShellMode.STREAM)
        task = self._task(
            "printf first; read first_gate; "
            "printf '\nsecond\n'; read second_gate")
        runner = threading.Thread(target=helper._bg_process_task, args=(task,))
        runner.start()
        try:
            self.assertFalse(responses.received.wait(.15))
            task.proc.stdin.write(b"continue\n")
            task.proc.stdin.flush()
            self.assertTrue(responses.received.wait(1.))
            self.assertIsNone(task.proc.poll())
            self.assertEqual(responses.messages, ["first", "second"])

            task.proc.stdin.write(b"finish\n")
            task.proc.stdin.flush()
            runner.join(2.)
            self.assertFalse(runner.is_alive())
            self.assertEqual(task.proc.returncode, 0)
        finally:
            if task.proc.poll() is None:
                helper._terminate_process(task.proc)
            runner.join(2.)
            self._close_task(task)

    def test_stream_flushes_final_line_without_newline(self):
        helper, responses = self._helper(PLUGIN.ShellMode.STREAM)
        task = self._task("printf final")
        try:
            helper._bg_process_task(task)
            self.assertEqual(responses.messages, ["final"])
        finally:
            if task.proc.poll() is None:
                helper._terminate_process(task.proc)
            self._close_task(task)

    def test_stream_closed_stdout_waits_without_spinning(self):
        helper, responses = self._helper(PLUGIN.ShellMode.STREAM)
        task = self._task("exec 1>&-; sleep .25")
        original_select = PLUGIN.select.select
        started = time.monotonic()
        try:
            with mock.patch.object(
                    PLUGIN.select, "select", wraps=original_select) as select_fn:
                helper._bg_process_task(task)
            elapsed = time.monotonic() - started

            self.assertGreaterEqual(elapsed, .2)
            self.assertLess(select_fn.call_count, 10)
            self.assertEqual(task.proc.returncode, 0)
            self.assertEqual(responses.messages, [])
        finally:
            if task.proc.poll() is None:
                helper._terminate_process(task.proc)
            self._close_task(task)

    def test_stream_preserves_utf8_split_across_reads(self):
        helper, responses = self._helper(PLUGIN.ShellMode.STREAM)
        script = (
            "import os,time; "
            "os.write(1, b'\\xd0'); time.sleep(.1); "
            "os.write(1, b'\\x9f\\xd1\\x80\\xd0\\xb8\\xd0\\xb2\\xd0\\xb5\\xd1\\x82\\n')")
        proc = subprocess.Popen(
            [sys.executable, "-c", script], stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, start_new_session=True)
        task = PLUGIN.AsyncRunHelper.Task(program=[], args=[])
        task.proc = proc
        try:
            helper._bg_process_task(task)
            self.assertEqual(responses.messages, ["Привет"])
        finally:
            if task.proc.poll() is None:
                helper._terminate_process(task.proc)
            task.proc.stdout.close()

    def test_stream_timeout_terminates_process_group(self):
        helper, responses = self._helper(
            PLUGIN.ShellMode.STREAM, timeout=.2)
        task = self._task("echo started; sleep 10")

        started = time.monotonic()
        try:
            helper._bg_process_task(task)
            elapsed = time.monotonic() - started

            self.assertLess(elapsed, 2.)
            self.assertIsNotNone(task.proc.poll())
            self.assertIn("started", responses.messages)
            self.assertIn(
                "!! Process test terminated due to timeout.",
                responses.messages)
        finally:
            if task.proc.poll() is None:
                helper._terminate_process(task.proc)
            self._close_task(task)

    def test_stream_timeout_does_not_wedge_next_run(self):
        helper, responses = self._live_helper(
            PLUGIN.ShellMode.STREAM, timeout=.15)
        try:
            helper.run(["sh", "-c"], ["echo slow; sleep 10"])
            self.assertTrue(self._wait_until(lambda: not helper.running))
            self.assertIn(
                "!! Process test terminated due to timeout.",
                responses.messages)

            responses.received.clear()
            helper.run(["sh", "-c"], ["echo recovered"])
            self.assertTrue(responses.received.wait(1.))
            self.assertTrue(self._wait_until(lambda: not helper.running))
            self.assertIn("recovered", responses.messages)
        finally:
            self._stop_helper(helper)

    def test_stream_drains_large_silent_output_without_deadlock(self):
        helper, responses = self._live_helper(
            PLUGIN.ShellMode.STREAM, timeout=2., verbose=False)
        try:
            helper.run(
                [sys.executable, "-c"],
                ["import os; os.write(1, b'x' * 262144)"])
            self.assertTrue(self._wait_until(lambda: not helper.running))
            self.assertEqual(responses.messages, [])
        finally:
            self._stop_helper(helper)

    def test_stream_rejects_overlap_then_can_run_again(self):
        helper, responses = self._live_helper(PLUGIN.ShellMode.STREAM)
        try:
            helper.run(["sh", "-c"], ["echo active; sleep .3"])
            self.assertTrue(responses.received.wait(1.))
            with self.assertRaisesRegex(RuntimeError, "already running"):
                helper.run(["sh", "-c"], ["echo overlap"])
            self.assertTrue(self._wait_until(lambda: not helper.running))

            responses.received.clear()
            helper.run(["sh", "-c"], ["echo second"])
            self.assertTrue(responses.received.wait(1.))
            self.assertTrue(self._wait_until(lambda: not helper.running))
            self.assertEqual(responses.messages, ["active", "second"])
        finally:
            self._stop_helper(helper)

    def test_stream_shutdown_terminates_running_process(self):
        helper, responses = self._live_helper(
            PLUGIN.ShellMode.STREAM, timeout=10.)
        processes = []
        original_run_task = helper._run_task

        def capture_process(program, args):
            proc = original_run_task(program, args)
            processes.append(proc)
            return proc

        helper._run_task = capture_process
        helper.run(["sh", "-c"], ["echo ready; sleep 10"])
        self.assertTrue(responses.received.wait(1.))
        helper.shutdown()
        helper._thread.join(3.)

        self.assertFalse(helper._thread.is_alive())
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll())
        processes[0].stdout.close()

    def test_stream_shutdown_allows_process_to_handle_sigterm(self):
        helper, responses = self._live_helper(
            PLUGIN.ShellMode.STREAM, timeout=10.)
        with tempfile.TemporaryDirectory() as temp_dir:
            marker = pathlib.Path(temp_dir) / "term-handled"
            script = "\n".join([
                "import pathlib, signal, sys, time",
                "marker = pathlib.Path(%r)" % str(marker),
                "def stop(signum, frame):",
                "    marker.write_text('TERM')",
                "    sys.exit(0)",
                "signal.signal(signal.SIGTERM, stop)",
                "print('ready', flush=True)",
                "while True: time.sleep(1)",
            ])
            helper.run([sys.executable, "-c"], [script])
            self.assertTrue(responses.received.wait(1.))

            helper.shutdown()
            helper._thread.join(3.)

            self.assertFalse(helper._thread.is_alive())
            self.assertTrue(marker.exists())
            self.assertEqual(marker.read_text(), "TERM")

    def test_external_sigkill_does_not_wedge_stream_runner(self):
        helper, responses = self._live_helper(
            PLUGIN.ShellMode.STREAM, timeout=10.)
        processes = []
        original_run_task = helper._run_task

        def capture_process(program, args):
            proc = original_run_task(program, args)
            processes.append(proc)
            return proc

        helper._run_task = capture_process
        try:
            helper.run(["sh", "-c"], ["echo ready; sleep 30"])
            self.assertTrue(responses.received.wait(1.))
            self.assertEqual(len(processes), 1)

            os.killpg(processes[0].pid, signal.SIGKILL)

            self.assertTrue(self._wait_until(lambda: not helper.running))
            self.assertLess(processes[0].returncode, 0)
            self.assertIn(
                "!! Process test terminated by signal %d." % signal.SIGKILL,
                responses.messages)
            responses.received.clear()
            helper.run(["sh", "-c"], ["echo recovered"])
            self.assertTrue(responses.received.wait(1.))
            self.assertTrue(self._wait_until(lambda: not helper.running))
            self.assertIn("recovered", responses.messages)
        finally:
            self._stop_helper(helper)

    def test_nonzero_exit_does_not_wedge_stream_runner(self):
        helper, responses = self._live_helper(PLUGIN.ShellMode.STREAM)
        try:
            helper.run(["sh", "-c"], ["echo failed; exit 7"])
            self.assertTrue(self._wait_until(lambda: not helper.running))
            self.assertIn("failed", responses.messages)
            self.assertIn(
                "!! Process test exited with status 7.", responses.messages)

            responses.received.clear()
            helper.run(["sh", "-c"], ["echo recovered"])
            self.assertTrue(responses.received.wait(1.))
            self.assertTrue(self._wait_until(lambda: not helper.running))
            self.assertIn("recovered", responses.messages)
        finally:
            self._stop_helper(helper)

    def test_buffered_background_behavior_is_unchanged(self):
        helper, responses = self._helper(
            PLUGIN.ShellMode.BACKGROUND, linewise=False)
        task = self._task("printf 'first\nsecond\n'")

        try:
            helper._bg_process_task(task)
            self.assertEqual(responses.messages, ["first\nsecond\n"])
        finally:
            if task.proc.poll() is None:
                helper._terminate_process(task.proc)
            self._close_task(task)

    def test_queue_still_accepts_and_orders_overlapping_runs(self):
        helper, responses = self._live_helper(PLUGIN.ShellMode.QUEUE)
        try:
            helper.run(["sh", "-c"], ["sleep .1; echo first"])
            helper.run(["sh", "-c"], ["echo second"])
            self.assertTrue(self._wait_until(
                lambda: len(responses.messages) == 2))
            self.assertTrue(self._wait_until(lambda: not helper.running))
            self.assertEqual(responses.messages, ["first", "second"])
        finally:
            self._stop_helper(helper)

    def test_spawn_failure_does_not_stop_worker_thread(self):
        helper, responses = self._live_helper(PLUGIN.ShellMode.STREAM)
        try:
            with self.assertLogs(level="ERROR"):
                helper.run(["/definitely/missing/forge-x-command"], [])
                self.assertTrue(self._wait_until(lambda: not helper.running))
            self.assertTrue(any(
                "Error running test Stream command" in message
                for message in responses.messages))

            responses.received.clear()
            helper.run(["sh", "-c"], ["echo recovered"])
            self.assertTrue(responses.received.wait(1.))
            self.assertTrue(self._wait_until(lambda: not helper.running))
            self.assertIn("recovered", responses.messages)
        finally:
            self._stop_helper(helper)

    def test_internal_stream_failure_terminates_process_and_recovers(self):
        helper, responses = self._live_helper(
            PLUGIN.ShellMode.STREAM, timeout=10.)
        processes = []
        original_run_task = helper._run_task
        original_proc_stream = helper._proc_stream

        def capture_process(program, args):
            proc = original_run_task(program, args)
            processes.append(proc)
            return proc

        def fail_stream(proc, proc_fd):
            raise RuntimeError("injected stream failure")

        helper._run_task = capture_process
        helper._proc_stream = fail_stream
        try:
            with self.assertLogs(level="ERROR"):
                helper.run(["sh", "-c"], ["sleep 30"])
                self.assertTrue(self._wait_until(lambda: not helper.running))
            self.assertEqual(len(processes), 1)
            self.assertIsNotNone(processes[0].poll())
            self.assertIn(
                "!! Process test runner failed.", responses.messages)

            helper._proc_stream = original_proc_stream
            responses.received.clear()
            helper.run(["sh", "-c"], ["echo recovered"])
            self.assertTrue(responses.received.wait(1.))
            self.assertTrue(self._wait_until(lambda: not helper.running))
            self.assertIn("recovered", responses.messages)
        finally:
            self._stop_helper(helper)

    def test_stream_partial_buffer_is_bounded(self):
        helper, responses = self._helper(PLUGIN.ShellMode.STREAM)
        helper._max_partial_output = 8

        partial = helper._stream_output("", "12345678")

        self.assertEqual(partial, "")
        self.assertEqual(responses.messages, ["12345678"])

    def test_stream_without_linewise_retains_multiline_response(self):
        helper, responses = self._helper(
            PLUGIN.ShellMode.STREAM, linewise=False)

        partial = helper._stream_output("", "first\nsecond\n")

        self.assertEqual(partial, "")
        self.assertEqual(responses.messages, ["first\nsecond\n"])

    def test_linewise_async_response_preserves_order(self):
        helper, responses = self._helper(PLUGIN.ShellMode.BACKGROUND)

        helper._async_response(
            "// action:prompt_begin USB\n// action:prompt_show\n")

        self.assertEqual(responses.messages, [
            "// action:prompt_begin USB",
            "// action:prompt_show",
        ])

if __name__ == "__main__":
    unittest.main()
