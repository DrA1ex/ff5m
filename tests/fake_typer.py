## A stand-in for Typer's batch mode that the renderer's frame loop launches in screen tests.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Reads the draw FIFO, logs each flushed transaction's commands and answers
its receipt at once, as Typer publishing without deferred presentation does.
A binary command is logged as {"command": ..., "payload": hex}; every entry
carries the launch's pid. Launches are counted by the log; the config file's {"launches": [behaviour,
...]} picks each launch's behaviour (the last repeats), where exit_at is the
flush (from 1) after which it exits without an answer, as a crashing Typer.

Usage: fake_typer.py CONFIG LOG --event-pipe EVENTS batch --pipe DRAW
"""

import fcntl
import json
import os
import select
import sys


def main():
    config_path, log_path = sys.argv[1], sys.argv[2]
    event_path = sys.argv[sys.argv.index("--event-pipe") + 1]
    draw_path = sys.argv[sys.argv.index("--pipe") + 1]
    with open(config_path) as source:
        launches = json.load(source)["launches"]
    launch = 0
    if os.path.exists(log_path):
        with open(log_path) as source:
            fcntl.flock(source, fcntl.LOCK_SH)
            launch = sum(1 for line in source if json.loads(line).get("start"))
    behaviour = launches[min(launch, len(launches) - 1)]

    def log(entry):
        entry.update(pid=os.getpid(), launch=launch)
        with open(log_path, "a") as target:
            fcntl.flock(target, fcntl.LOCK_EX)
            target.write(json.dumps(entry) + "\n")

    log({"start": True})
    # Typer opens both FIFOs itself; the draw FIFO read-write, so it never sees an end of file.
    draw = os.open(draw_path, os.O_RDWR)
    events = os.open(event_path, os.O_WRONLY)
    buffer, commands, flushes = b"", [], 0
    parent = os.getppid()

    def read_more():
        nonlocal buffer
        # The loop leaves Typer running when it stops, as on the printer, and the test kills it; a run interrupted
        # before that must not leave this process behind, so it exits once the test process is gone.
        while not select.select([draw], [], [], 0.5)[0]:
            if os.getppid() != parent:
                sys.exit()
        buffer += os.read(draw, 65536)

    def line():
        nonlocal buffer
        while b"\n" not in buffer:
            read_more()
        text, buffer = buffer.split(b"\n", 1)
        return text.decode("utf-8")

    def payload(size):
        nonlocal buffer
        while len(buffer) < size:
            read_more()
        data, buffer = buffer[:size], buffer[size:]
        return data

    while True:
        text = line()
        if text == "--end":
            continue
        if text.startswith("--binary "):
            _binary, size, command = text.split(" ", 2)
            commands.append({"command": command, "payload": payload(int(size)).hex()})
            continue
        if not text.startswith("--batch flush"):
            commands.append(text)
            continue
        line()  # the flush ends its frame
        flushes += 1
        words = text.split()
        token = words[words.index("--receipt") + 1] if "--receipt" in words else None
        log({"commands": commands, "token": token})
        commands = []
        if flushes == behaviour.get("exit_at"):
            return
        if token is not None:
            os.write(events, ("render %s ok 3 2 1\n" % token).encode("utf-8"))


if __name__ == "__main__":
    main()
