"""Host-only queue regressions; all printer I/O is replaced by fakes."""

import ast
import asyncio
import copy
import importlib.util
import inspect
import pathlib
import sys
import types
import unittest
from enum import Enum, Flag, auto


ROOT = pathlib.Path(__file__).parents[1] / ".root" / "moonraker"
PACKAGE = "_ff5m_test_job_queue"
for suffix in ("", ".components"):
    module = types.ModuleType(PACKAGE + suffix)
    module.__path__ = []
    sys.modules[module.__name__] = module

# Use Moonraker's real event enums without importing its optional dependencies.
common = types.ModuleType(PACKAGE + ".common")
common.__dict__.update(Enum=Enum, Flag=Flag, auto=auto, sys=sys)
enum_names = {"ExtendedFlag", "RequestType", "ExtendedEnum", "JobEvent",
              "KlippyState"}
common_tree = ast.parse((ROOT / "common.py").read_text())
enum_tree = ast.Module(body=[
    ast.ImportFrom(module="__future__", names=[
        ast.alias(name="annotations")], level=0),
    *[node for node in common_tree.body
      if isinstance(node, ast.ClassDef) and node.name in enum_names],
], type_ignores=[])
exec(compile(ast.fix_missing_locations(enum_tree), str(ROOT / "common.py"),
             "exec"), common.__dict__)
sys.modules[common.__name__] = common


def load_component(name):
    spec = importlib.util.spec_from_file_location(
        PACKAGE + ".components." + name, ROOT / "components" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


JobQueue = load_component("job_queue").JobQueue
JobState = load_component("job_state").JobState
JobEvent = common.JobEvent
KlippyState = common.KlippyState


class ServerError(Exception):
    pass


class Timer:
    def __init__(self, when, callback, args, kwargs):
        self.when = when
        self.callback = callback
        self.args = args
        self.kwargs = kwargs
        self.cancelled = False
        self.fired = False

    def cancel(self):
        self.cancelled = True

    async def fire(self):
        if self.cancelled or self.fired:
            return
        self.fired = True
        result = self.callback(*self.args, **self.kwargs)
        if inspect.isawaitable(result):
            await result


class EventLoop:
    """Advance timers explicitly, without wall-clock sleeps or live commands."""

    def __init__(self):
        self.now = 0.0
        self.timers = []

    def delay_callback(self, delay, callback, *args, **kwargs):
        timer = Timer(self.now + delay, callback, args, kwargs)
        self.timers.append(timer)
        return timer

    def pending(self):
        return [timer for timer in self.timers
                if not timer.cancelled and not timer.fired]

    async def run_all(self):
        for _ in range(100):
            pending = self.pending()
            if not pending:
                return
            timer = min(pending, key=lambda item: item.when)
            self.now = timer.when
            await timer.fire()
        raise AssertionError("Unbounded callbacks in queue test")


class PrinterAPI:
    def __init__(self):
        self.print_state = "standby"
        self.query_entered = asyncio.Event()
        self.query_release = None
        self.query_error = None
        self.started = []
        self.start_entered = asyncio.Event()
        self.start_release = None
        self.start_error = None
        self.gcodes = []
        self.transition_entered = asyncio.Event()
        self.transition_release = None
        self.subscription_results = []
        self.subscription_calls = 0
        self.subscription_entered = asyncio.Event()
        self.subscription_release = None
        self.callbacks = []

    async def query_objects(self, objects):
        assert objects == {"print_stats": None}
        self.query_entered.set()
        if self.query_release is not None:
            await self.query_release.wait()
        if self.query_error is not None:
            raise self.query_error
        return {"print_stats": {"state": self.print_state}}

    async def start_print(self, filename, wait_klippy_started=False, user=None):
        self.start_entered.set()
        if self.start_release is not None:
            await self.start_release.wait()
        if self.start_error is not None:
            raise self.start_error
        self.started.append((filename, wait_klippy_started, user))
        self.print_state = "printing"

    async def run_gcode(self, gcode):
        self.gcodes.append(gcode)
        self.transition_entered.set()
        if self.transition_release is not None:
            await self.transition_release.wait()

    async def subscribe_objects(self, objects, callback):
        assert objects == {"print_stats": None}
        self.subscription_calls += 1
        result = (self.subscription_results.pop(0)
                  if self.subscription_results else {
                      "print_stats": {"state": self.print_state}})
        self.subscription_entered.set()
        if self.subscription_release is not None:
            await self.subscription_release.wait()
        if isinstance(result, Exception):
            raise result
        self.callbacks.append(callback)
        return copy.deepcopy(result)


class FileManager:
    def check_file_exists(self, root, name):
        return root == "gcodes" and name != "missing.gcode"


class Server:
    error = ServerError

    def __init__(self):
        self.loop = EventLoop()
        self.api = PrinterAPI()
        self.components = {"klippy_apis": self.api,
                           "file_manager": FileManager()}
        self.events = []
        self.handlers = {}

    def get_event_loop(self):
        return self.loop

    def lookup_component(self, name):
        return self.components[name]

    def register_event_handler(self, name, callback):
        self.handlers.setdefault(name, []).append(callback)

    def register_notification(self, *args):
        pass

    def register_remote_method(self, *args):
        pass

    def register_endpoint(self, *args):
        pass

    def send_event(self, name, *args):
        self.events.append((name, args))
        self.loop.delay_callback(0, self.dispatch_event, name, *args)

    async def dispatch_event(self, name, *args):
        for handler in self.handlers.get(name, []):
            result = handler(*args)
            if inspect.isawaitable(result):
                await result


class Config:
    error = ServerError

    def __init__(self, server, **options):
        self.server = server
        self.options = options

    def get_server(self):
        return self.server

    def get(self, name, default):
        return self.options.get(name, default)

    getboolean = get
    getfloat = get


class Request:
    def __init__(self, **args):
        self.args = args

    def get(self, name):
        return self.args[name]


class QueueTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = Server()
        self.state = JobState(Config(self.server))
        self.server.components["job_state"] = self.state
        self.queue = JobQueue(Config(self.server))
        self.api = self.server.api

    async def test_manual_start_loads_one_job_and_preserves_user(self):
        user = object()
        await self.queue.queue_job(["first.gcode", "second.gcode"], user=user)
        await self.queue.start_queue()
        await self.server.loop.run_all()
        self.assertEqual(self.api.started, [("first.gcode", True, user)])
        self.assertEqual(self.queue.queue_state, "paused")
        self.assertEqual([str(job) for job in self.queue.queued_jobs.values()],
                         ["second.gcode"])

    async def test_completion_does_not_enable_automatic_transition_by_default(self):
        await self.queue.queue_job(["first.gcode", "second.gcode"])
        await self.queue.start_queue()
        await self.server.loop.run_all()
        self.api.print_state = "complete"
        await self.server.dispatch_event("job_state:state_changed", JobEvent.COMPLETE)
        await self.server.loop.run_all()
        self.assertEqual(len(self.api.started), 1)
        await self.queue.start_queue()
        await self.server.loop.run_all()
        self.assertEqual([job[0] for job in self.api.started],
                         ["first.gcode", "second.gcode"])

    async def test_armed_automatic_queue_advances_on_real_completion_event(self):
        self.queue.automatic = True
        self.queue.job_transition_gcode = "TEST_CLEAR_BED"
        await self.state._handle_started(KlippyState.READY)
        await self.queue.queue_job(["first.gcode", "second.gcode"])
        await self.queue.start_queue()
        await self.server.loop.run_all()
        self.assertEqual(self.api.gcodes, [])
        await self.state._status_update({"print_stats": {
            "state": "printing", "filename": "first.gcode", "total_duration": 1}}, 0)
        await self.server.loop.run_all()
        self.api.print_state = "complete"
        await self.state._status_update({"print_stats": {"state": "complete"}}, 1)
        await self.server.loop.run_all()
        self.assertEqual([job[0] for job in self.api.started],
                         ["first.gcode", "second.gcode"])
        self.assertEqual(self.api.gcodes, ["TEST_CLEAR_BED"])
        self.assertFalse(self.queue.queued_jobs)

    async def test_active_or_paused_print_cannot_be_replaced(self):
        await self.queue.queue_job("next.gcode")
        for state in ("printing", "paused"):
            with self.subTest(state=state):
                self.api.print_state = state
                await self.queue.start_queue()
                await self.server.loop.run_all()
                self.assertFalse(self.api.started)
                self.assertEqual(self.queue.queue_state, "paused")

    async def test_pause_cancels_pending_load(self):
        await self.queue.queue_job("next.gcode")
        await self.queue.start_queue()
        await self.queue.pause_queue()
        await self.server.loop.run_all()
        self.assertFalse(self.api.started)
        self.assertEqual(self.queue.queue_state, "paused")
        self.assertIsNone(self.queue.pop_queue_handle)

    async def assert_pause_wins_readiness_race(self, print_state):
        await self.queue.queue_job("next.gcode")
        self.queue.automatic = True
        self.api.print_state = print_state
        self.api.query_release = asyncio.Event()
        start = asyncio.create_task(self.queue.start_queue())
        await self.api.query_entered.wait()
        pause = asyncio.create_task(self.queue.pause_queue())
        await asyncio.sleep(0)  # Let Pause run while Start owns the queue lock.
        self.api.query_release.set()
        await asyncio.gather(start, pause)
        self.assertEqual(self.queue.queue_state, "paused")
        self.assertIsNone(self.queue.pop_queue_handle)
        await self.server.loop.run_all()
        self.assertFalse(self.api.started)
        self.assertEqual(len(self.queue.queued_jobs), 1)

    async def test_pause_during_idle_readiness_query_prevents_start(self):
        await self.assert_pause_wins_readiness_race("complete")

    async def test_pause_during_busy_readiness_query_does_not_rearm_queue(self):
        await self.assert_pause_wins_readiness_race("printing")

    async def test_pause_during_transition_prevents_print(self):
        await self.queue.queue_job("next.gcode")
        self.queue.job_transition_gcode = "TEST_CLEAR_BED"
        self.queue.queue_state = "loading"
        self.api.transition_release = asyncio.Event()
        load = asyncio.create_task(self.queue._pop_job())
        await self.api.transition_entered.wait()
        pause = asyncio.create_task(self.queue.pause_queue())
        await asyncio.sleep(0)
        self.api.transition_release.set()
        with self.assertLogs(level="ERROR"):
            await asyncio.gather(load, pause)
        self.assertFalse(self.api.started)
        self.assertEqual(self.queue.queue_state, "paused")

    async def test_pause_does_not_requeue_already_submitted_print(self):
        await self.queue.queue_job("next.gcode")
        self.queue.queue_state = "loading"
        self.api.start_release = asyncio.Event()
        load = asyncio.create_task(self.queue._pop_job(False))
        await self.api.start_entered.wait()
        pause = asyncio.create_task(self.queue.pause_queue())
        await asyncio.sleep(0)
        self.api.start_release.set()
        await asyncio.gather(load, pause)
        self.assertEqual(len(self.api.started), 1)
        self.assertFalse(self.queue.queued_jobs)
        self.assertEqual(self.queue.queue_state, "paused")

    async def test_start_errors_keep_job_and_allow_explicit_retry(self):
        await self.queue.queue_job("next.gcode")
        for error in (ServerError("not ready"), FileNotFoundError("printer.cfg"),
                      RuntimeError("unexpected start failure")):
            with self.subTest(error=type(error).__name__):
                self.api.start_error = error
                await self.queue.start_queue()
                with self.assertLogs(level="ERROR"):
                    await self.server.loop.run_all()
                self.assertEqual(self.queue.queue_state, "paused")
                self.assertEqual(len(self.queue.queued_jobs), 1)
        self.api.start_error = None
        await self.queue.start_queue()
        await self.server.loop.run_all()
        self.assertEqual(len(self.api.started), 1)
        self.assertFalse(self.queue.queued_jobs)

    async def test_cancelled_start_pauses_queue_and_preserves_cancellation(self):
        await self.queue.queue_job("next.gcode")
        self.queue.queue_state = "loading"
        self.api.start_error = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.queue._pop_job(False)
        self.assertEqual(self.queue.queue_state, "paused")
        self.assertEqual(len(self.queue.queued_jobs), 1)

    async def test_abort_prevents_automatic_loading(self):
        self.queue.automatic = True
        await self.queue.queue_job("next.gcode")
        self.queue.queue_state = "ready"
        await self.server.dispatch_event("job_state:state_changed", JobEvent.ERROR)
        await self.server.dispatch_event("job_state:state_changed", JobEvent.COMPLETE)
        await self.server.loop.run_all()
        self.assertFalse(self.api.started)
        self.assertEqual(self.queue.queue_state, "paused")

    async def test_load_on_startup_remains_explicit_opt_in(self):
        await self.queue.queue_job("next.gcode")
        await self.queue.pause_queue()
        await self.queue._handle_ready()
        await self.server.loop.run_all()
        self.assertFalse(self.api.started)
        self.queue.load_on_start = True
        await self.queue._handle_ready()
        await self.server.loop.run_all()
        self.assertEqual(len(self.api.started), 1)

    async def test_jump_broadcasts_full_reordered_queue(self):
        await self.queue.queue_job(["first.gcode", "second.gcode"])
        await self.server.loop.run_all()
        self.server.events.clear()
        job_id = list(self.queue.queued_jobs)[1]
        response = await self.queue._handle_jump(Request(job_id=job_id))
        await self.server.loop.run_all()
        events = [args[0] for name, args in self.server.events
                  if name == "job_queue:job_queue_changed"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["action"], "jobs_updated")
        # Queue ages are measured separately for the response and notification.
        for actual, expected in zip(events[0]["updated_queue"], response["queued_jobs"]):
            self.assertEqual(set(actual), set(expected))
            for key in ("job_id", "filename", "time_added"):
                self.assertEqual(actual[key], expected[key])
            self.assertGreaterEqual(actual["time_in_queue"], 0)
        self.assertEqual(len(events[0]["updated_queue"]), 2)
        self.assertEqual([job["filename"] for job in response["queued_jobs"]],
                         ["second.gcode", "first.gcode"])

    async def test_invalid_jump_does_not_change_queue(self):
        await self.queue.queue_job("first.gcode")
        original = list(self.queue.queued_jobs)
        with self.assertRaises(ServerError):
            await self.queue._handle_jump(Request(job_id="invalid"))
        self.assertEqual(list(self.queue.queued_jobs), original)

    async def test_missing_file_does_not_partially_queue_batch(self):
        with self.assertRaises(ServerError):
            await self.queue.queue_job(["first.gcode", "missing.gcode"])
        self.assertFalse(self.queue.queued_jobs)


class JobStateTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = Server()
        self.state = JobState(Config(self.server))
        self.api = self.server.api

    async def test_successful_subscription_initializes_and_reports_completion(self):
        self.api.print_state = "printing"
        await self.state._handle_started(KlippyState.READY)
        await self.state._status_update({"print_stats": {"state": "complete"}}, 0)
        self.assertEqual(self.state.get_last_job_event(), JobEvent.COMPLETE)
        self.assertEqual(self.state.get_last_stats()["state"], "complete")

    async def test_failed_subscription_retries_and_recovers_completion_events(self):
        self.api.subscription_results = [ServerError("disconnected"),
                                         {"print_stats": {"state": "printing"}}]
        await self.state._handle_started(KlippyState.READY)
        self.assertEqual(self.state.get_last_stats(), {})
        await self.server.loop.run_all()
        self.assertEqual(self.api.subscription_calls, 2)
        self.assertEqual(len(self.api.callbacks), 1)
        await self.api.callbacks[0]({"print_stats": {"state": "complete"}}, 0)
        self.assertEqual(self.state.get_last_job_event(), JobEvent.COMPLETE)

    async def test_subscription_retry_is_bounded(self):
        self.api.subscription_results = [ServerError("not ready")] * 10
        await self.state._handle_started(KlippyState.READY)
        with self.assertLogs(level="WARNING"):
            await self.server.loop.run_all()
        self.assertEqual(self.api.subscription_calls, 3)
        self.assertFalse(self.server.loop.pending())

    async def test_disconnect_cancels_subscription_retry(self):
        self.api.subscription_results = [ServerError("not ready")]
        await self.state._handle_started(KlippyState.READY)
        self.state._handle_disconnect()
        await self.server.loop.run_all()
        self.assertEqual(self.api.subscription_calls, 1)

    async def test_new_connection_cancels_old_retry(self):
        self.api.subscription_results = [ServerError("not ready"),
                                         {"print_stats": {"state": "complete"}}]
        await self.state._handle_started(KlippyState.READY)
        await self.state._handle_started(KlippyState.READY)
        await self.server.loop.run_all()
        self.assertEqual(self.api.subscription_calls, 2)
        self.assertEqual(self.state.get_last_stats()["state"], "complete")

    async def test_disconnected_inflight_response_cannot_overwrite_error_state(self):
        self.state.last_print_stats = {"state": "printing"}
        self.api.subscription_results = [{"print_stats": {"state": "printing"}}]
        self.api.subscription_release = asyncio.Event()
        subscription = asyncio.create_task(self.state._handle_started(KlippyState.READY))
        await self.api.subscription_entered.wait()
        self.state._handle_disconnect()
        self.api.subscription_release.set()
        await subscription
        self.assertEqual(self.state.get_last_stats()["state"], "error")

    async def test_close_cancels_subscription_retry(self):
        self.api.subscription_results = [ServerError("not ready")]
        await self.state._handle_started(KlippyState.READY)
        await self.state.close()
        await self.server.loop.run_all()
        self.assertEqual(self.api.subscription_calls, 1)

    async def test_not_ready_does_not_subscribe(self):
        await self.state._handle_started(KlippyState.SHUTDOWN)
        await self.server.loop.run_all()
        self.assertEqual(self.api.subscription_calls, 0)


if __name__ == "__main__":
    unittest.main()
