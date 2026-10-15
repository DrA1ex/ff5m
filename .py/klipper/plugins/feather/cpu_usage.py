## System CPU sampling on Feather's existing file worker.
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


class CpuUsageSampler:
    """A worker-owned measurement; readers only copy its immutable snapshot."""

    def __init__(self, worker, read_counters=read_cpu_counters, sample_clock=None):
        self._read_counters = read_counters
        self._sample_clock = sample_clock
        self._previous = None
        self._snapshot = None
        self._next_sample = 0.0
        self._closed = False
        if not worker.add_service(self):
            raise RuntimeError("CPU sampler requires a running file worker")

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
        # No reader lock, callback queue, or reactor I/O: publish the complete
        # immutable pair with one reference replacement in CPython.
        sampled_at = self._sample_clock() if self._sample_clock is not None else now
        self._snapshot = (sampled_at, percent)
        return self._next_sample

    def get_percent(self, now):
        snapshot = self._snapshot
        if snapshot is None or not 0 <= now - snapshot[0] <= 3.0:
            return None
        return snapshot[1]

    def close(self):
        self._closed = True
        self._snapshot = None
        self._previous = None
