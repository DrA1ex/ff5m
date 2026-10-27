"""Bounded Recovery hardware tests.

Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
This file may be distributed under the terms of the GNU GPLv3 license.

Every test is a generator that yields its progress (0..1) and returns a
{"status", "detail"} result. The Recovery UI runs it step by step, so a cancel
is simply close(): the generator's cleanup stops the helper and removes owned
temporary files. No test outlives the Recovery process that started it.
"""

import collections
import glob
import hashlib
import json
import os
import select
import shutil
import stat
import subprocess
import sys
import tempfile
import time

from speed_test_rand import drop_caches

REFERENCE_BYTES = 128 * 1024 * 1024
BLOCK = 1024 * 1024
SECTOR = 512
CPU_HELPER = "/opt/config/mod/.bin/exec/fgx-stress"
CPU_SECONDS = 180
READ_CHUNK = 4 * BLOCK
# Written data is synced in chunks, so the timed writes reach the eMMC and pattern generation between
# them is not counted.
WRITE_CHUNK = 8 * BLOCK
# Measured on one stock Adventurer 5M (CPU 1008 MHz, eMMC 25 MHz HS200 4-bit) with the methods
# below. 2026-10-27: eMMC read 11.5 MiB/s in three of three runs, CPU 240.5 points in four of six.
# 2026-10-28: eMMC read 11.5-11.6 and write 8.0-8.4 MiB/s in three runs, memory read 836.6-841.2
# MiB/s in five, CPU 232.9-241.0 points in four.
STOCK_EMMC_READ_MIBS = 11.5
STOCK_EMMC_WRITE_MIBS = 8.3
STOCK_CPU_POINTS = 240.0
STOCK_MEMORY_READ_MIBS = 837.0
# A fixed interpreter loop in a child process, timed by its own CPU time: the clock bounds it, not
# memory, and other processes using the screen or the second core do not count against it.
CPU_SPEED_PROGRAM = """
import time
best = 0.0
for attempt in range(3):
    started, value = time.process_time(), 0
    for index in range(100000):
        value = (value * 31 + index) & 0xffffffff
    best = max(best, 100000 / (time.process_time() - started))
print(best)
"""


def speed_fact(value, unit, stock):
    """A speed fact's value: the speed, then its ratio to stock as the " | " secondary part once the
    stock speed is known."""
    text = "{:.1f} {}".format(value, unit) if value < 100 else "{:.0f} {}".format(value, unit)
    return text if not stock else "{} | {:.2f}x stock".format(text, value / stock)


def short_speed(value, unit, stock):
    """The speed shown beside a test's outcome: its ratio to stock when known, else the speed."""
    return "{:.2f}x stock".format(value / stock) if stock else speed_fact(value, unit, None)


def expected_block(index):
    # Include every sector's file address so within-MiB replay/aliasing is visible.
    sectors = []
    for address in range(index * (BLOCK // SECTOR), (index + 1) * (BLOCK // SECTOR)):
        seed = hashlib.sha256(b"Forge-X eMMC reference v2:" + str(address).encode("ascii")).digest()
        sectors.append(seed * (SECTOR // len(seed)))
    return b"".join(sectors)


def reference_path(root):
    return os.path.join(root, "reference", "emmc-reference-v2.bin")


def owned_directory(root):
    directory = os.path.join(root, "reference")
    if not os.path.lexists(directory):
        os.makedirs(directory, 0o700)
    if not stat.S_ISDIR(os.lstat(directory).st_mode):
        raise RuntimeError("Test directory must be a real directory.")
    return directory


def write_known(path, size, offset=0.0, span=1.0):
    """Write the known pattern durably and return the seconds spent writing and syncing it."""
    if shutil.disk_usage(os.path.dirname(path)).free < size + 64 * BLOCK:
        raise RuntimeError("Not enough free space for the owned test file plus 64 MiB reserve.")
    elapsed = 0.0
    with open(path, "wb", buffering=0) as destination:
        for first in range(0, size // BLOCK, WRITE_CHUNK // BLOCK):
            chunk = []
            for index in range(first, min(size // BLOCK, first + WRITE_CHUNK // BLOCK)):
                chunk.append(expected_block(index))
                yield offset + span * (index + 1) * BLOCK / size
            data = b"".join(chunk)
            started = time.monotonic()
            if destination.write(data) != len(data):
                raise RuntimeError("Incomplete test-file write.")
            os.fsync(destination.fileno())
            elapsed += time.monotonic() - started
    return elapsed


def open_known(path, size):
    source = os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb", buffering=0)
    info = os.fstat(source.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_size != size:
        source.close()
        raise RuntimeError("Reference size or file type is invalid; prepare it on confirmed settings.")
    return source


def timed_read(path, size, offset=0.0, span=1.0, evict=drop_caches):
    """Read the file once without checking it and return (MiB/s, whether the cache was evicted).
    Verification is CPU-bound and would hide the eMMC, so only the reads themselves are timed."""
    with open_known(path, size) as source:
        evicted = evict(path)
        buffer, done, elapsed = bytearray(READ_CHUNK), 0, 0.0
        while done < size:
            started = time.monotonic()
            count = source.readinto(buffer)
            elapsed += time.monotonic() - started
            if not count:
                raise RuntimeError("Reference ended before its recorded size.")
            done += count
            yield offset + span * done / size
    return size / BLOCK / max(0.001, elapsed), evicted


def verify_known(path, size, offset=0.0, span=1.0, evict=drop_caches, name="eMMC"):
    """Check every block against the known pattern; return whether the cache was evicted first."""
    with open_known(path, size) as source:
        evicted = evict(path)
        for index in range(size // BLOCK):
            if source.read(BLOCK) != expected_block(index):
                raise RuntimeError("{} data mismatch at byte {}.".format(name, index * BLOCK))
            yield offset + span * (index + 1) * BLOCK / size
    return evicted


CACHE_FACT = "Cache: not cleared, reads may come from RAM"


def reference_result(evicted):
    return {"status": "passed" if evicted else "inconclusive",
            "detail": "Known data read back intact." + ("" if evicted else "\n" + CACHE_FACT)}


def prepare_reference(root, size=REFERENCE_BYTES):
    directory = owned_directory(root)
    path = reference_path(root)
    if os.path.lexists(path):
        # Reuse only verified known data; never replace an unexplained file.
        return reference_result((yield from verify_known(path, size)))
    descriptor, temporary = tempfile.mkstemp(prefix=".prepare-", dir=directory)
    os.close(descriptor)
    try:
        yield from write_known(temporary, size, 0.0, 0.5)
        evicted = yield from verify_known(temporary, size, 0.5, 0.5)
        os.replace(temporary, path)
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return reference_result(evicted)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def failed(error):
    """A stage's failure as the value of its "Stage: value" line."""
    text = str(error) or type(error).__name__
    return "failed, " + text[:1].lower() + text[1:]


def emmc_test(root, size=REFERENCE_BYTES, evict=drop_caches):
    """Read and check the reference; once it is intact, write, check and remove a separate owned file.
    The result keeps each stage's outcome and speed, so a failed write still shows the read."""
    try:
        read_speed, evicted = yield from timed_read(reference_path(root), size, 0.0, 0.05, evict)
        evicted = (yield from verify_known(reference_path(root), size, 0.05, 0.3, evict, "Reference")) and evicted
    except (OSError, RuntimeError) as error:
        return {"status": "failed", "detail": "Read: {}\nWrite: not run".format(failed(error))}

    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".write-test-", dir=owned_directory(root))
        os.close(descriptor)
        seconds = yield from write_known(temporary, size, 0.35, 0.3)
        evicted = (yield from verify_known(temporary, size, 0.65, 0.35, evict, "Written file")) and evicted
        write, status = "Write: " + speed_fact(size / BLOCK / max(0.001, seconds), "MiB/s", STOCK_EMMC_WRITE_MIBS), "passed"
    except (OSError, RuntimeError) as error:
        write, status = "Write: " + failed(error), "failed"
    finally:
        if temporary:
            os.unlink(temporary)
    # A passed stage shows its speed: the outcome itself is the page's headline.
    lines = ["Read: " + speed_fact(read_speed, "MiB/s", STOCK_EMMC_READ_MIBS), write]
    if not evicted:
        lines.append(CACHE_FACT)
        status = "inconclusive" if status == "passed" else status
    return {"status": status, "speed": short_speed(read_speed, "MiB/s", STOCK_EMMC_READ_MIBS),
            "detail": "\n".join(lines)}


def cleanup_owned_files(root, progress=lambda stage, activity="": None):
    directory = os.path.join(root, "reference")
    progress("Checking the test directory", directory)
    try:
        mode = os.lstat(directory).st_mode
    except FileNotFoundError:
        progress("No temporary test directory", "Nothing to remove")
        return
    if not stat.S_ISDIR(mode):
        progress("Skipping the test directory", "Not a real directory")
        return
    removed = 0
    progress("Scanning temporary test files", directory)
    with os.scandir(directory) as entries:
        for entry in entries:
            if not entry.name.startswith((".prepare-", ".write-test-")):
                continue
            progress("Checking a temporary file", entry.name)
            if stat.S_ISREG(entry.stat(follow_symlinks=False).st_mode):
                progress("Removing a temporary file", "{} ({} removed)".format(entry.name, removed))
                os.unlink(entry.path)
                removed += 1
    progress("Temporary test files cleaned", "{} removed".format(removed))


def read_number(path):
    try:
        with open(path) as source:
            return int(source.read().strip())
    except (OSError, ValueError):
        return None


def cpu_observation():
    temperatures = [read_number(path) for path in glob.glob("/sys/class/thermal/thermal_zone*/temp")]
    temperatures = [value / 1000 for value in temperatures if value is not None]
    frequency = read_number("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq")
    limit = read_number("/sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq")
    return max(temperatures) if temperatures else None, frequency, limit


def clock_text(target_khz, share, throttled):
    """Whether the CPU held its target: one value, since a full share means no throttling was seen."""
    if not target_khz:
        return "target unknown"
    held = "{:g} MHz".format(target_khz / 1000)
    if share >= 1 and not throttled:
        return held + " held, no throttling"
    return "{} at {}".format(percent_text(share), held) + (", throttled" if throttled else "")


def percent_text(share):
    """A share as a percentage that reads 100% only when nothing was missed."""
    return "100%" if share >= 1 else "{:g}%".format(int(share * 10000) / 100)


def cpu_points():
    """Thousands of interpreter loops per CPU second, or None if a child could not run. The best
    of three children: one process's memory layout alone moves the result by several percent."""
    try:
        return max(float(subprocess.run([sys.executable, "-I", "-c", CPU_SPEED_PROGRAM], stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, universal_newlines=True, timeout=60,
                                        check=True).stdout) for _ in range(3)) / 1000
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def cpu_test(target_hz, duration=CPU_SECONDS, helper=CPU_HELPER, measure_points=cpu_points):
    # Show the running page before the speed measurement blocks for a few seconds.
    yield 0.0
    points = measure_points()
    temperatures, limits = (collections.deque(maxlen=1201) for _ in range(2))
    load_samples = collections.deque(maxlen=1201)
    final, progress = None, 0.0
    child = subprocess.Popen([helper, str(duration), "32", "2"], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, universal_newlines=True, bufsize=1)
    try:
        deadline = time.monotonic() + duration + 60
        while child.poll() is None:
            if time.monotonic() > deadline:
                raise RuntimeError("CPU helper did not finish within {} s.".format(duration + 60))
            ready, _, _ = select.select([child.stdout], [], [], 0.2)
            if ready:
                line = child.stdout.readline()
                if line:
                    report = json.loads(line)
                    if "status" in report:
                        final = report
                    elif "progress" in report:
                        progress = max(0.0, min(1.0, float(report["progress"])))
            temperature, frequency, limit = cpu_observation()
            load_samples.append(frequency)
            if temperature is not None:
                temperatures.append(temperature)
            if limit is not None:
                limits.append(limit)
            yield progress
        for line in child.stdout:
            report = json.loads(line)
            if "status" in report:
                final = report
        if final is None:
            raise RuntimeError("CPU helper ended without verified results: " + child.stderr.read()[-200:])
        if child.returncode != 0 and final["status"] == "passed":
            final = {"status": "interrupted", "detail": "CPU helper exited abnormally after its result.",
                     "rounds": final["rounds"], "parallel_ratio": final["parallel_ratio"]}
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        child.stdout.close()
        child.stderr.close()
    target_khz = target_hz / 1000 if target_hz else None
    # A sample is at target within PLL rounding. Samples before the first one at target are the
    # governor's ramp-up; after it, every sample must stay at target.
    at_target = [bool(target_khz and value is not None and value >= target_khz * 0.99) for value in load_samples]
    held = at_target[at_target.index(True):] if True in at_target else at_target
    share = sum(held) / len(held) if held else 0.0
    sustained = len(held) >= 2 and all(held)
    throttled = bool(limits and (min(limits) < max(limits) or (target_khz and min(limits) < target_khz)))
    # The detail is the helper's verdict, then any missing stress conditions, then "Label: value" facts.
    lines = [final["detail"][:1].upper() + final["detail"][1:].rstrip(".") + "."]
    if final["status"] == "passed" and (not temperatures or not limits or not sustained or throttled):
        final["status"] = "inconclusive"
        lines.append("Full stress conditions were not established.")
    memory = final.get("read_mibs")
    # The overall speed weighs the CPU and memory ratios equally (their geometric mean).
    score = (points / STOCK_CPU_POINTS * memory / STOCK_MEMORY_READ_MIBS) ** 0.5 \
        if points and memory and STOCK_CPU_POINTS and STOCK_MEMORY_READ_MIBS else None
    if score:
        lines.append("Speed: {:.2f}x stock".format(score))
    lines += ["CPU: " + (speed_fact(points, "points", STOCK_CPU_POINTS) if points else "unavailable"),
              "Memory read: " + (speed_fact(memory, "MiB/s", STOCK_MEMORY_READ_MIBS) if memory else "unavailable"),
              "Clock: " + clock_text(target_khz, share, throttled)]
    if temperatures:
        lines.append("Temperature: {:.1f} C peak, {:.1f} C at start".format(max(temperatures), temperatures[0]))
    else:
        lines.append("Temperature: unavailable")
    if final.get("rounds") and final.get("ram_mib"):
        lines.append("Memory checked: {} cross-core passes, {:.1f} GiB".format(
            final["rounds"], final["rounds"] * final["ram_mib"] / 1024))
    final["detail"] = "\n".join(lines)
    if score:
        final["speed"] = "{:.2f}x stock".format(score)
    elif points:
        final["speed"] = short_speed(points, "points", STOCK_CPU_POINTS)
    return final


def kernel_errors():
    try:
        result = subprocess.run(["dmesg"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                universal_newlines=True, timeout=3)
        if result.returncode:
            return None
        return set(line for line in result.stdout.splitlines()
                   if ("mmc" in line.lower() or "i/o" in line.lower()) and
                   any(word in line.lower() for word in ("error", "timeout", "crc", "corrupt")))
    except (OSError, subprocess.TimeoutExpired):
        return None


def run(kind, root, cpu_target_hz=None):
    """One test of the given kind, with new kernel storage errors as a failure."""
    before = kernel_errors()
    if kind == "cpu-stability":
        result = yield from cpu_test(cpu_target_hz)
    elif kind == "emmc-prepare":
        result = yield from prepare_reference(root)
    elif kind == "emmc-integrity":
        result = yield from emmc_test(root)
    else:
        raise RuntimeError("Unknown hardware test.")
    after = kernel_errors()
    if before is not None and after is not None and after - before:
        return {"status": "failed", "detail": "New kernel storage errors: " + "; ".join(sorted(after - before))[:250]}
    # Without the kernel log a storage pass is unconfirmed; with it, no new errors need no line.
    if kind != "cpu-stability" and (before is None or after is None):
        if result["status"] == "passed":
            result["status"] = "inconclusive"
        result["detail"] += "\nKernel log: unavailable"
    return result
