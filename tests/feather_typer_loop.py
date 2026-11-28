## The renderer's frame loop over a fake Typer, for screen tests of what the loop draws.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import json
import os
import re
import shutil
import signal
import sys
import tempfile
import time
import traceback

from ui.engine import BinaryCommand, FrameLoop, feather_engine
from ui.renderer import MAX_ATOMIC_DRAW, MAX_BATCH_BYTES

FAKE_TYPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_typer.py")


def wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while True:
        value = condition()
        if value or time.monotonic() > deadline:
            return value
        time.sleep(0.01)


class TyperLoop:
    """Runs renderer's own frame loop over the fake Typer (launches: its
    behaviours, see fake_typer.py): start() and stop() run as on the printer,
    with only the Typer command line and FIFOs replaced.

    While it runs, the loop's thread takes snapshots of the screen between
    write transactions, so a write to the screen's tree or to a page state it
    reads outside every transaction is a race: the engine reports each one
    (watch_writes) and close() fails with the stack of the first."""

    def __init__(self, renderer, *launches, restart_handler=None):
        self.renderer = renderer
        self.directory = tempfile.mkdtemp(prefix="feather-loop-")
        self.log_path = os.path.join(self.directory, "log")
        config = os.path.join(self.directory, "config")
        with open(config, "w") as target:
            json.dump({"launches": list(launches) or [{}]}, target)
        draw, events = os.path.join(self.directory, "draw"), os.path.join(self.directory, "events")
        renderer._loop = FrameLoop(draw, events, "feather-test-no-such-process", max_frame=MAX_ATOMIC_DRAW,
                                   max_batch=MAX_BATCH_BYTES, backoff_initial=0.01, backoff_max=0.05)
        renderer._typer_arguments = lambda: [
            sys.executable, FAKE_TYPER, config, self.log_path, "--event-pipe", events, "batch", "--pipe", draw]
        renderer._font_manifest_loaded = True
        renderer.configure_worker(lambda old, new: None, restart_handler)
        self.events = []
        self.unsafe_writes = []

    def start(self):
        feather_engine.watch_writes(
            lambda what: self.unsafe_writes.append((what, "".join(traceback.format_stack()[:-1]))))
        self.renderer.start()
        return self

    def close(self):
        feather_engine.watch_writes(None)
        self.renderer.stop()
        # The loop leaves Typer running when it stops, as on the printer; these Typers are this process's children.
        for pid in {entry["pid"] for entry in self.entries()}:
            try:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            except (ProcessLookupError, ChildProcessError):
                pass
        shutil.rmtree(self.directory, ignore_errors=True)
        if self.unsafe_writes:
            what, stack = self.unsafe_writes[0]
            raise AssertionError("%d write(s) outside a screen transaction while the loop ran; the first, to a %s:\n%s"
                                 % (len(self.unsafe_writes), what, stack))

    def entries(self):
        if not os.path.exists(self.log_path):
            return []
        with open(self.log_path) as source:
            return [json.loads(line) for line in source]

    def history(self, launch=None):
        """The command batches the launch drew, in order; a binary command as a BinaryCommand."""
        return [[BinaryCommand(command["command"], bytes.fromhex(command["payload"]))
                 if isinstance(command, dict) else command for command in entry["commands"]]
                for entry in self.entries()
                if "commands" in entry and (launch is None or entry["launch"] == launch)]

    def wait_for_batch(self, after, matches, launch=None):
        """Return the matching new batch itself, not a later snapshot of the log."""
        return wait_for(lambda: next((batch for batch in self.history(launch)[after:]
                                      if matches(batch)), None))

    def drew(self, text, launch=None, *, after=None, without=()):
        """Wait for text in the last batch, or in a new batch after a history index."""
        def matches(batch):
            commands = [str(getattr(command, "command", command)) for command in batch]
            return (any(text in command for command in commands)
                    and all(excluded not in command for excluded in without for command in commands))

        if after is not None:
            return bool(self.wait_for_batch(after, matches, launch))
        return bool(wait_for(lambda: any(matches(batch) for batch in self.history(launch)[-1:])))

    def wire(self, action):
        """The id Typer reports a tap of action with: the one the last frames gave it."""
        for batch in reversed(self.history()):
            for command in batch:
                found = re.search(r"\S*:%s(?=\s|$)" % re.escape(action), str(command))
                if found:
                    return found.group()
        raise AssertionError("no frame gave %s an input region" % action)

    def tell(self, line):
        """Writes a line to the event FIFO, as Typer reports a touch."""
        events = os.open(os.path.join(self.directory, "events"), os.O_WRONLY | os.O_NONBLOCK)
        try:
            os.write(events, (line + "\n").encode("utf-8"))
        finally:
            os.close(events)

    def take(self, until, timeout=5.0):
        """The renderer's events (process_events) until until(events) holds."""
        def done():
            self.events.extend(self.renderer.process_events(time.monotonic()))
            return until(self.events)
        wait_for(done, timeout)
        return self.events
