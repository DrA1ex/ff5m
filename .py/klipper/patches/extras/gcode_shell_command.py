# Run a shell command via gcode
#
# Changes:
# - Added queued and streaming background modes
# - Added optional linewise raw responses for action-command protocols
#
# Copyright (C) 2025, Alexander K <https://github.com/drA1ex>
#
# Copyright (C) 2019  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.


import dataclasses
import codecs
import enum
import os
import select
import shlex
import signal
import subprocess
import logging
import threading
import time

from typing import List, Optional


class ShellMode(enum.Enum):
    SYNC = "sync"
    BACKGROUND = "background"
    STREAM = "stream"
    QUEUE = "queue"
    DAEMON = "daemon"


class AsyncRunHelper:
    @dataclasses.dataclass
    class Task:
        program: List[str]
        args: List[str]

        proc = None

    _wait_interval = 5.0
    _stream_interval = 0.1
    _max_partial_output = 65536

    def __init__(self, printer, cmd):
        self.printer = printer
        self.cmd = cmd

        self.mode = cmd.mode
        self.timeout = cmd.timeout
        self.name = cmd.name
        self.linewise = cmd.linewise

        self.reactor = printer.get_reactor()
        self.gcode = printer.lookup_object("gcode")

        self._queue: List[AsyncRunHelper.Task] = []
        self._queue_event = threading.Event()
        self._lock = threading.Lock()

        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._terminate = False

    def run(self, program: List[str], args: List[str]):
        if self._terminate: raise InterruptedError("Async runner terminated")

        if self.mode == ShellMode.DAEMON:
            proc = self._run_task(program, args)
            logging.info(f"[gcode_shell_command {self.name}]: run deemon process {proc.pid!r}.")
            return

        with self._lock:
            if self._running and self.mode in {
                    ShellMode.BACKGROUND, ShellMode.STREAM}:
                raise self.gcode.error(f"Command {self.name} already running!")

            self._queue.append(self.Task(program=program, args=args))

            if not self._running:
                if self._thread is None:
                    if self.cmd.debug: self.gcode.respond_info(f"Run command's {self.name} thread")

                    self._thread = threading.Thread(target=self._bg_thread)
                    self._thread.start()

                self._queue_event.set()
                self._running = True
            elif self.cmd.debug:
                self.gcode.respond_info(f"Add command {self.name} to Queue {program} {args}")

    def shutdown(self):
        logging.info(f"[gcode_shell_command {self.name}]: received shutdown request.")

        if not self._thread: return
        with self._lock:
            self._queue.clear()
            self._terminate = True
            self._queue_event.set()

    @property
    def running(self):
        return self._running

    def _bg_thread(self):
        logging.info(f"[gcode_shell_command {self.name}]: background thread started.")

        while True:
            self._queue_event.wait()
            if self._terminate: break

            with self._lock:
                if len(self._queue) == 0:
                    if self.cmd.debug and self.mode == ShellMode.QUEUE:
                        self._async_response(f"Command {self.name}: queue is empty")

                    self._running = False
                    self._queue_event.clear()

                    continue

                task = self._queue.pop(0)

            task.proc = self._run_task(task.program, task.args)
            if task.proc:
                logging.info(f"[gcode_shell_command {self.name}]: run process {task.proc.pid!r}.")
                try:
                    self._bg_process_task(task)
                except InterruptedError:
                    if self._terminate:
                        break
                    logging.exception(
                        f"[gcode_shell_command {self.name}]: interrupted")
                except Exception:
                    logging.exception(
                        f"[gcode_shell_command {self.name}]: runner failed")
                    self._terminate_process(task.proc)
                    self._async_response(
                        f"!! Process {self.name} runner failed.")

        logging.info(f"[gcode_shell_command {self.name}]: background thread exit.")

    def _bg_process_task(self, task: Task):
        proc = task.proc
        proc_fd = proc.stdout.fileno()
        terminated = False

        try:
            try:
                if self.mode == ShellMode.STREAM:
                    wait_success = self._proc_stream(proc, proc_fd)
                else:
                    wait_success = self._proc_wait(proc)
            except InterruptedError:
                logging.info(
                    "[gcode_shell_command %s]: interrupted by shutdown",
                    self.name)
                self._terminate_process(proc)
                raise

            if wait_success:
                logging.info(f"[gcode_shell_command {self.name}]: process \"{proc.pid}\" done.")
            else:
                logging.info(f"[gcode_shell_command {self.name}]: process \"{proc.pid}\" not finished within timeout. Terminated.")
                self._terminate_process(proc)
                terminated = True

            if self.cmd.verbose and self.mode != ShellMode.STREAM:
                output = self._read_stdout(proc_fd)
                self._async_response(output)

            if terminated:
                self._async_response(f"!! Process {self.name} terminated due to timeout.")
            elif (self.mode == ShellMode.STREAM and
                  proc.returncode is not None and proc.returncode < 0):
                self._async_response(
                    f"!! Process {self.name} terminated by signal "
                    f"{-proc.returncode}.")
            elif (self.mode == ShellMode.STREAM and
                  proc.returncode is not None and proc.returncode > 0):
                self._async_response(
                    f"!! Process {self.name} exited with status "
                    f"{proc.returncode}.")
            elif self.cmd.debug:
                self._async_response(f"// Process {self.name} done.")
        finally:
            proc.stdout.close()

    def _proc_wait(self, proc):
        remaining = self.timeout

        while remaining > 0:
            if self._terminate: raise InterruptedError("Requested shutdown. Terminate background process")

            wait_time = min(self._wait_interval, remaining)
            try:
                proc.wait(wait_time)
                return True
            except subprocess.TimeoutExpired:
                pass

            remaining -= wait_time

        return False

    def _proc_stream(self, proc, proc_fd):
        deadline = time.monotonic() + self.timeout
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        partial_output = ""
        stdout_eof = False

        while True:
            if self._terminate:
                raise InterruptedError(
                    "Requested shutdown. Terminate background process")

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                partial_output = self._stream_output(
                    partial_output, decoder.decode(b"", final=True))
                if self.cmd.verbose and partial_output:
                    self._async_response(partial_output)
                return False

            if stdout_eof:
                try:
                    proc.wait(min(self._stream_interval, remaining))
                except subprocess.TimeoutExpired:
                    continue
            else:
                readable, _, _ = select.select(
                    [proc_fd], [], [],
                    min(self._stream_interval, remaining))
                if readable:
                    data = os.read(proc_fd, 4096)
                    if data:
                        partial_output = self._stream_output(
                            partial_output, decoder.decode(data))
                    else:
                        stdout_eof = True

            if proc.poll() is None:
                continue

            # Drain bytes already buffered in the pipe after process exit.
            while (not stdout_eof and
                   select.select([proc_fd], [], [], 0)[0]):
                data = os.read(proc_fd, 4096)
                if not data:
                    stdout_eof = True
                    break
                partial_output = self._stream_output(
                    partial_output, decoder.decode(data))

            partial_output = self._stream_output(
                partial_output, decoder.decode(b"", final=True))
            if self.cmd.verbose and partial_output:
                self._async_response(partial_output)
            return True

    def _stream_output(self, partial_output, data):
        output = partial_output + data
        split = output.rfind("\n")
        if split >= 0:
            if self.cmd.verbose:
                self._async_response(output[:split + 1])
            output = output[split + 1:]

        # Prevent tools that rewrite one line or never print a newline from
        # growing the runner's buffer without a bound.
        if len(output) >= self._max_partial_output:
            if self.cmd.verbose:
                self._async_response(output)
            return ""
        return output

    @staticmethod
    def _terminate_process(proc):
        if proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            try:
                proc.terminate()
            except OSError:
                return

        try:
            proc.wait(1.0)
            return
        except subprocess.TimeoutExpired:
            pass

        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            try:
                proc.kill()
            except OSError:
                return
        try:
            proc.wait(1.0)
        except subprocess.TimeoutExpired:
            logging.error("Unable to terminate process %s", proc.pid)

    def _run_task(self, program: List[str], args: List[str]):
        mode_str = self.mode.name.capitalize()

        if self.cmd.debug:
            self._async_response(f"Running {self.name} {mode_str} command: {program} {args}")

        try:
            return subprocess.Popen(
                program + args,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                start_new_session=True
            )
        except:
            logging.exception(f"shell_command {self.name}: {mode_str} command failed: {self.name!r}")
            self._async_response(f"!! Error running {self.name} {mode_str} command {self.name!r}")

        return None

    def _async_response(self, message):
        if not message: return

        responses = message.splitlines() if self.linewise else [message]
        for response in responses:
            if not response:
                continue
            self.reactor.register_async_callback(
                lambda e, _gc=self.gcode, s=response: _gc.respond_raw(s)
            )

    @staticmethod
    def _read_stdout(fd):
        result = ""
        while os.path.exists(fd):
            data = os.read(fd, 4096)
            if not data: break

            result += data.decode()

        return result


class ShellCommand:
    def __init__(self, config):
        self.name = config.get_name().split()[-1]
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')

        cmd = config.get('command')
        cmd = os.path.expanduser(cmd)
        self.command = shlex.split(cmd)

        self.timeout = config.getfloat('timeout', 2., above=0.)
        self.mode = ShellMode(config.get('mode', ShellMode.SYNC.value))
        self.verbose = config.getboolean('verbose', True)
        self.debug = config.getboolean('debug', False)
        self.linewise = config.getboolean('linewise', False)

        self.proc_fd = None
        self.partial_output = ""

        self._async_helper = None
        if self.mode in {
                ShellMode.BACKGROUND, ShellMode.STREAM,
                ShellMode.QUEUE}:
            self._async_helper = AsyncRunHelper(self.printer, self)
            self.printer.register_event_handler("klippy:disconnect", lambda: self._async_helper.shutdown())

        self.gcode.register_mux_command(
            "RUN_SHELL_COMMAND", "CMD", self.name,
            self.cmd_RUN_SHELL_COMMAND,
            desc=self.cmd_RUN_SHELL_COMMAND_help)

    cmd_RUN_SHELL_COMMAND_help = "Run a linux shell command"

    def cmd_RUN_SHELL_COMMAND(self, params):
        gcode_params = params.get('PARAMS', '')
        gcode_params = shlex.split(gcode_params)

        if self.mode in {
                ShellMode.BACKGROUND, ShellMode.STREAM,
                ShellMode.QUEUE, ShellMode.DAEMON}:
            return self._async_helper.run(self.command, gcode_params)

        reactor = self.printer.get_reactor()

        try:
            proc = subprocess.Popen(
                self.command + gcode_params, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        except Exception:
            logging.exception(
                "shell_command: Command {%s} failed" % (self.name))
            raise self.gcode.error("Error running command {%s}" % (self.name))

        if self.debug:
            self.gcode.respond_info("Running Command {%s}...:" % (self.name))

        if self.verbose:
            self.proc_fd = proc.stdout.fileno()
            hdl = reactor.register_fd(self.proc_fd, self._process_output)

        eventtime = reactor.monotonic()
        endtime = eventtime + self.timeout
        complete = False

        while eventtime < endtime:
            eventtime = reactor.pause(eventtime + .05)
            if proc.poll() is not None:
                complete = True
                break

        if not complete:
            proc.terminate()

        if self.verbose:
            if self.partial_output:
                self.gcode.respond_info(self.partial_output)
                self.partial_output = ""

            reactor.unregister_fd(hdl)
            self.proc_fd = None

        if not complete:
            self.gcode.respond_info("Command {%s} timed out" % self.name)
        elif self.debug:
            self.gcode.respond_info("Command {%s} finished\n" % self.name)

    def _process_output(self, eventime):
        if self.proc_fd is None:
            return

        try:
            data = os.read(self.proc_fd, 4096)
        except Exception:
            pass

        data = self.partial_output + data.decode()

        if '\n' not in data:
            self.partial_output = data
            return
        elif data[-1] != '\n':
            split = data.rfind('\n') + 1
            self.partial_output = data[split:]
            data = data[:split]
        else:
            self.partial_output = ""

        self.gcode.respond_info(data)


def load_config_prefix(config):
    return ShellCommand(config)
