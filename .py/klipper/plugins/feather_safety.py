## Bounded safety-state composition for Feather.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import logging


class SafetyDecision:
    """Immutable result of one safety evaluation."""

    __slots__ = ("visible", "global_reasons", "armed_reasons")

    def __init__(self, visible, global_reasons=(), armed_reasons=()):
        self.visible = bool(visible)
        self.global_reasons = tuple(global_reasons)
        self.armed_reasons = tuple(armed_reasons)

    @property
    def reasons(self):
        return self.global_reasons + self.armed_reasons


class _SafetyLease:
    __slots__ = ("_registry", "name", "_released")

    def __init__(self, registry, name):
        self._registry = registry
        self.name = str(name)
        self._released = False
        registry._acquire(self.name)

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.release()
        return False

    def release(self):
        if self._released:
            return False
        self._released = True
        self._registry._release(self.name)
        return True


class SafetyRegistry:
    """Compose observable activity, owned operations, and route policy.

    The registry deliberately retains no samples or transition history. Its
    memory is bounded by the registered provider names and the set of names
    which currently hold an activity lease.
    """

    def __init__(self, excluded_routes=()):
        self._excluded_routes = frozenset(excluded_routes)
        self._sources = {}
        self._leases = {}
        self._source_failures = {}

    def register_source(self, name, predicate):
        name = str(name)
        if not name:
            raise ValueError("Safety source name must not be empty")
        if name in self._sources:
            raise ValueError("Duplicate safety source: %s" % name)
        if not callable(predicate):
            raise TypeError("Safety source must be callable")
        self._sources[name] = predicate

    def activity(self, name):
        """Acquire an idempotently releasable, reference-counted lease."""
        return _SafetyLease(self, name)

    def _acquire(self, name):
        self._leases[name] = self._leases.get(name, 0) + 1

    def _release(self, name):
        count = self._leases.get(name, 0)
        if count <= 1:
            self._leases.pop(name, None)
        else:
            self._leases[name] = count - 1

    def reset(self):
        self._leases.clear()
        self._source_failures.clear()

    @property
    def source_count(self):
        return len(self._sources)

    @property
    def lease_count(self):
        return sum(self._leases.values())

    def evaluate(self, route, eventtime, armed_reasons=(), enabled=True):
        if not enabled or route in self._excluded_routes:
            return SafetyDecision(False)

        global_reasons = list(sorted(
            name for name, count in self._leases.items() if count > 0))
        for name, predicate in self._sources.items():
            try:
                active = bool(predicate(eventtime))
            except Exception:
                failures = self._source_failures.get(name, 0) + 1
                self._source_failures[name] = failures
                # A broken status provider must not hide the emergency action.
                # Keep evidence without flooding klippy.log every second.
                if failures == 1 or failures % 60 == 0:
                    logging.exception(
                        "[feather_screen] safety source failed name=%s "
                        "failures=%d", name, failures)
                active = True
            else:
                self._source_failures.pop(name, None)
            if active and name not in global_reasons:
                global_reasons.append(name)

        armed = tuple(sorted(set(str(reason) for reason in armed_reasons
                                 if reason)))
        global_reasons = tuple(sorted(global_reasons))
        return SafetyDecision(bool(global_reasons or armed),
                              global_reasons, armed)
