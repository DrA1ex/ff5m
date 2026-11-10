## System CPU and memory sampling on Feather's existing file worker.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
## This file may be distributed under the terms of the GNU GPLv3 license


def read_cpu_counters():
    with open("/proc/stat", "r") as source:
        fields = source.readline().split()
    if not fields or fields[0] != "cpu" or len(fields) < 5:
        raise ValueError("Missing aggregate CPU counters")
    counters = tuple(int(value) for value in fields[1:9])
    if min(counters) < 0:
        raise ValueError("Negative CPU counter")
    # guest and guest_nice are already included in user and nice. The
    # aggregate line includes every core, so its ratio is already 0..100%.
    total = sum(counters)
    idle = counters[3] + (counters[4] if len(counters) > 4 else 0)
    return total, idle


def read_free_cache_kib():
    """Return MemFree + file cache + reclaimable slab in KiB from one /proc/meminfo read."""
    with open("/proc/meminfo", "r") as source:
        lines = source.read().splitlines()
    kib = {}
    for line in lines:
        field, _, raw = line.partition(":")
        if field in ("MemFree", "Active(file)", "Inactive(file)", "KReclaimable", "SReclaimable"):
            kib[field] = int(raw.replace("kB", ""))  # ValueError on a malformed value

    try:
        # KReclaimable already includes SReclaimable.
        reclaimable = kib["KReclaimable"] if "KReclaimable" in kib else kib["SReclaimable"]
        return kib["MemFree"] + kib["Active(file)"] + kib["Inactive(file)"] + reclaimable
    except KeyError as error:
        raise ValueError("Missing memory counters") from error


class SystemUsageSampler:
    """A worker-owned measurement; readers only copy its immutable snapshot."""

    def __init__(self, worker, read_counters=read_cpu_counters,
                 read_memory=read_free_cache_kib, sample_clock=None):
        self._read_counters = read_counters
        self._read_memory = read_memory
        self._sample_clock = sample_clock
        self._previous = None
        self._snapshot = None
        self._next_sample = 0.0
        self._closed = False
        if not worker.add_service(self):
            raise RuntimeError("System usage sampler requires a running file worker")

    def poll(self, now):
        if self._closed:
            return None
        if now < self._next_sample:
            return self._next_sample
        self._next_sample = now + 1.0
        percent = None
        try:
            current = self._read_counters()
            if self._previous is not None:
                total = current[0] - self._previous[0]
                idle = current[1] - self._previous[1]
                if total > 0 and 0 <= idle <= total:
                    percent = round(100.0 * (total - idle) / total)
            self._previous = current
        except (OSError, ValueError):
            self._previous = None
        try:
            memory = self._read_memory()
        except (OSError, ValueError):
            memory = None
        # No reader lock, callback queue, or reactor I/O: publish the complete
        # immutable snapshot with one reference replacement in CPython.
        sampled_at = self._sample_clock() if self._sample_clock is not None else now
        self._snapshot = (sampled_at, percent, memory)
        return self._next_sample

    def get_usage(self, now):
        """Return (CPU percent, free+cache KiB); either is None when unknown or stale."""
        snapshot = self._snapshot
        if snapshot is None or not 0 <= now - snapshot[0] <= 3.0:
            return None, None
        return snapshot[1:]

    def close(self):
        self._closed = True
        self._snapshot = None
        self._previous = None
