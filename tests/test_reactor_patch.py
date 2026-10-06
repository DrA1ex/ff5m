## Behavioral tests for the Forge-X legacy Klipper reactor patch.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import importlib.util
import os
import pathlib
import sys
import time
import types
import unittest
from unittest import mock


MODULE_PATH = (pathlib.Path(__file__).parents[1] / ".py" / "klipper" /
               "patches" / "reactor.py")


class ReadyEvents:
    """Deliver a kernel readiness batch even if a callback unregisters an fd."""

    def __init__(self):
        self.registered = {}
        self.batches = []

    @staticmethod
    def descriptor(handle):
        return handle if isinstance(handle, int) else handle.fileno()

    def register(self, handle, flags):
        self.registered[self.descriptor(handle)] = flags

    def unregister(self, handle):
        del self.registered[self.descriptor(handle)]

    def modify(self, handle, flags):
        descriptor = self.descriptor(handle)
        if descriptor not in self.registered:
            raise AssertionError("cannot modify an unregistered descriptor")
        self.registered[descriptor] = flags

    def poll(self, timeout):
        if not self.batches:
            raise AssertionError("reactor did not stop after the supplied events")
        return self.batches.pop(0)


class SelectEvents:
    def __init__(self):
        self.batches = []

    def select(self, readable, writeable, exceptional, timeout):
        if not self.batches:
            raise AssertionError("reactor did not stop after the supplied events")
        return self.batches.pop(0)


class DispatchGreenlet:
    """Run non-yielding callbacks without requiring the printer's greenlet ABI."""

    def __init__(self, run):
        self.run = run

    def switch(self):
        return self.run()


def load_reactor():
    dependencies = {
        "greenlet": types.SimpleNamespace(
            greenlet=DispatchGreenlet, getcurrent=lambda: None),
        "chelper": types.SimpleNamespace(
            get_ffi=lambda: (None, types.SimpleNamespace(
                get_monotonic=time.monotonic))),
        "util": types.SimpleNamespace(
            set_nonblock=lambda fd: os.set_blocking(fd, False)),
        "select": types.SimpleNamespace(
            POLLIN=1, POLLOUT=4, POLLHUP=16,
            EPOLLIN=1, EPOLLOUT=4, EPOLLHUP=16),
    }
    spec = importlib.util.spec_from_file_location("forge_x_reactor", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, dependencies):
        spec.loader.exec_module(module)
    return module


REACTOR = load_reactor()


class ReactorDispatchTest(unittest.TestCase):
    def make_reactor(self, backend):
        events = SelectEvents() if backend == "select" else ReadyEvents()
        api = types.SimpleNamespace(
            POLLIN=1, POLLOUT=4, POLLHUP=16,
            EPOLLIN=1, EPOLLOUT=4, EPOLLHUP=16,
            poll=lambda: events, epoll=lambda: events)
        if backend == "select":
            api.select = events.select
        reactor_class = getattr(REACTOR, {
            "select": "SelectReactor", "poll": "PollReactor",
            "epoll": "EPollReactor"}[backend])
        with mock.patch.object(REACTOR, "select", api):
            reactor = reactor_class()
        # Async cross-thread delivery is independent of the readiness race.
        reactor._setup_async_callbacks = lambda: None
        return reactor, events, api

    def run_batches(self, backend, reactor, events, api, batches):
        for batch in batches:
            if backend == "select":
                events.batches.append((
                    [fd for fd, flags in batch if flags & (1 | 16)],
                    [fd for fd, flags in batch if flags & 4], []))
            else:
                events.batches.append(batch)
        reactor.register_fd(99, lambda eventtime: reactor.end())
        if backend == "select":
            events.batches.append(([99], [], []))
        else:
            events.batches.append([(99, 1)])
        with mock.patch.object(REACTOR, "select", api):
            reactor.run()

    def test_read_write_and_hangup_events_keep_their_order(self):
        for backend in ("select", "poll", "epoll"):
            with self.subTest(backend=backend):
                reactor, events, api = self.make_reactor(backend)
                calls = []
                handle = reactor.register_fd(
                    17, lambda t: calls.append("read"),
                    lambda t: calls.append("write"))
                reactor.set_fd_wake(handle, True, True)
                self.run_batches(backend, reactor, events, api,
                                 [[(17, 1 | 4)], [(17, 16)]])
                self.assertEqual(calls, ["read", "write", "read"])

    def test_response_closing_another_client_skips_its_ready_event(self):
        for backend in ("select", "poll", "epoll"):
            for flags in (1, 4, 16):
                with self.subTest(backend=backend, flags=flags):
                    reactor, events, api = self.make_reactor(backend)
                    calls = []
                    client = reactor.register_fd(
                        17, lambda t: calls.append("closed-read"),
                        lambda t: calls.append("closed-write"))

                    def response(eventtime):
                        calls.append("response")
                        reactor.unregister_fd(client)

                    reactor.register_fd(10, response)
                    reactor.register_fd(18, lambda t: calls.append("survivor"))
                    self.run_batches(backend, reactor, events, api,
                                     [[(10, 1), (17, flags), (18, 1)]])
                    self.assertEqual(calls, ["response", "survivor"])

    def test_read_closing_its_own_client_skips_the_write_event(self):
        for backend in ("select", "poll", "epoll"):
            with self.subTest(backend=backend):
                reactor, events, api = self.make_reactor(backend)
                calls = []

                def read(eventtime):
                    calls.append("read")
                    reactor.unregister_fd(client)

                client = reactor.register_fd(
                    17, read, lambda t: calls.append("closed-write"))
                self.run_batches(backend, reactor, events, api,
                                 [[(17, 1 | 4)]])
                self.assertEqual(calls, ["read"])

    def test_reused_descriptor_only_receives_events_from_the_next_batch(self):
        for backend in ("select", "poll", "epoll"):
            with self.subTest(backend=backend):
                reactor, events, api = self.make_reactor(backend)
                calls = []
                old = reactor.register_fd(17, lambda t: calls.append("old"))

                def reconnect(eventtime):
                    calls.append("reconnect")
                    reactor.unregister_fd(old)
                    reactor.register_fd(17, lambda t: calls.append("new"))

                reactor.register_fd(10, reconnect)
                self.run_batches(backend, reactor, events, api,
                                 [[(10, 1), (17, 1)], [(17, 1)]])
                self.assertEqual(calls, ["reconnect", "new"])

    def test_reuse_during_read_cannot_deliver_old_write_to_a_new_client(self):
        for backend in ("select", "poll", "epoll"):
            with self.subTest(backend=backend):
                reactor, events, api = self.make_reactor(backend)
                calls = []

                def read(eventtime):
                    calls.append("old-read")
                    reactor.unregister_fd(old)
                    new = reactor.register_fd(
                        17, lambda t: calls.append("new-read"),
                        lambda t: calls.append("new-write"))
                    reactor.set_fd_wake(new, True, True)

                old = reactor.register_fd(
                    17, read, lambda t: calls.append("old-write"))
                self.run_batches(backend, reactor, events, api,
                                 [[(17, 1 | 4)], [(17, 4)]])
                self.assertEqual(calls, ["old-read", "new-write"])

    def test_callback_errors_still_propagate(self):
        for backend in ("select", "poll", "epoll"):
            with self.subTest(backend=backend):
                reactor, events, api = self.make_reactor(backend)

                def fail(eventtime):
                    raise KeyError("callback defect")

                reactor.register_fd(17, fail)
                with self.assertRaisesRegex(KeyError, "callback defect"):
                    self.run_batches(backend, reactor, events, api,
                                     [[(17, 1)]])

    def test_dispatch_switch_discards_the_remaining_old_ready_batch(self):
        for backend in ("select", "poll", "epoll"):
            with self.subTest(backend=backend):
                reactor, events, api = self.make_reactor(backend)
                calls = []

                def yield_dispatch(eventtime):
                    calls.append("yield")
                    reactor._g_dispatch = object()

                def resume(previous):
                    calls.append("resume")
                    reactor._g_dispatch = previous

                # Model the boundary at which the legacy greenlet machinery
                # hands control back to this dispatcher after a callback yields.
                reactor._end_greenlet = resume
                reactor.register_fd(10, yield_dispatch)
                reactor.register_fd(17, lambda t: calls.append("next-batch"))
                self.run_batches(backend, reactor, events, api,
                                 [[(10, 1), (17, 1)], [(17, 1)]])
                self.assertEqual(calls, ["yield", "resume", "next-batch"])


class ReactorDispatchTimeTest(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.reactor = REACTOR.SelectReactor()
        self.reactor.monotonic = mock.Mock(side_effect=lambda: self.now)

    def test_timer_callbacks_and_resumption_have_fresh_wall_time(self):
        seen = []

        def first(eventtime):
            seen.append(self.reactor.get_dispatch_time())
            self.now = 10.250
            return self.reactor.NEVER

        def resumed(eventtime):
            seen.append(self.reactor.get_dispatch_time())
            return self.reactor.NEVER

        self.reactor.register_timer(first, 10.0)
        self.reactor.register_timer(resumed, 10.0)
        self.reactor._check_timers(10.0, True)

        self.assertEqual(seen, [10.0, 10.250])
        self.assertEqual(self.reactor.get_dispatch_time(), 10.250)

    def test_io_after_idle_and_write_after_read_are_new_dispatch_boundaries(self):
        seen = []
        self.now = 70.0

        def read(eventtime):
            seen.append(self.reactor.get_dispatch_time())
            self.now = 70.030

        self.reactor.register_fd(
            17, read, lambda t: seen.append(self.reactor.get_dispatch_time()))
        self.reactor._check_fds(70.0, [(17, self.reactor._READ | self.reactor._WRITE)])

        self.assertEqual(seen, [70.0, 70.030])


if __name__ == "__main__":
    unittest.main()
