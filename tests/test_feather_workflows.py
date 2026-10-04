## Tests for Feather screen workflows.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import ast
import errno
import os
import pathlib
import queue
import shlex
import tempfile
import threading
import unittest
from unittest import mock

try:
    from tests.test_feather_screen import (
        FEATHER, RESURRECTION, GCodeRecorder, Reactor, StatusObject)
    from tests.feather_render_test_helper import RenderCapture
except ImportError:
    from test_feather_screen import (
        FEATHER, RESURRECTION, GCodeRecorder, Reactor, StatusObject)
    from feather_render_test_helper import RenderCapture

from ui import CONTENT_BOTTOM, Increment
from ui.font_metrics import get_font_metrics
from ff5m_ui.move import runtime as MOVE_UI
from ff5m_ui.move.step import page as MOVE_STEP_PAGE
from ff5m_ui.heat import runtime as HEAT_UI
from ff5m_ui.filament import runtime as FILAMENT_UI
from ff5m_ui.filament.actions import select as select_filament
from ff5m_ui.z_offset import runtime as Z_OFFSET_UI
from feather.features.filament import FilamentFeature
from feather.features.z import ZCalibrationFeature
from feather.calibration.z import (
    FeatherZCalibrationMixin, ZCalibrationSession)
from feather.calibration.extruder import FeatherExtruderCalibrationMixin


class ScenarioController(FeatherZCalibrationMixin,
                         FeatherExtruderCalibrationMixin,
                         FEATHER.FeatherScreen):
    """Test harness for scenario implementations no longer on the host."""

    boot_screen_held = False
    touch_available = None
    touch_warning_visible = False
    system_shutdown_active = False


from feather import files as FILES
from feather.previews import PreviewCache
from feather.screen import pages as PAGES
from feather.screen.pages import files as FILE_PAGES
from feather.network import client as NETWORK
from feather.network import protocol as NETWORK_PROTOCOL
from feather.network import pages as NETWORK_UI


class VirtualSD:
    def __init__(self, root=None, active=False, file_path=None):
        self.sdcard_dirname = root
        self.active = active
        self.current_path = file_path

    def is_active(self):
        return self.active

    def file_path(self):
        return self.current_path


class UsbProcess:
    next_pid = 9000

    def __init__(self, output, returncode=0, running=False):
        self.output = output.encode("utf-8")
        self.returncode = None if running else returncode
        self.pid = UsbProcess.next_pid
        UsbProcess.next_pid += 1

    def poll(self):
        return self.returncode

    def communicate(self):
        return (self.output, None)


class UsbEventSocket:
    def __init__(self):
        self.messages = []
        self.closed = False
        self.bound = None

    def setsockopt(self, *args):
        pass

    def bind(self, address):
        self.bound = address

    def setblocking(self, enabled):
        pass

    def fileno(self):
        return 73

    def recv(self, size):
        if not self.messages:
            raise BlockingIOError()
        message = self.messages.pop(0)
        if isinstance(message, BaseException):
            raise message
        return message

    def close(self):
        self.closed = True


class UsbReactor:
    def __init__(self):
        self.registered = []
        self.unregistered = []

    def register_fd(self, fd, callback):
        handle = (fd, callback)
        self.registered.append(handle)
        return handle

    def unregister_fd(self, handle):
        self.unregistered.append(handle)


class OperationContextStub(StatusObject):
    def request_cancel(self):
        if self.status.get("cancel_pending", False):
            state = "already_pending"
        elif self.status.get("cancel_available", False):
            state = "accepted"
            self.status["cancel_pending"] = True
            self.status["cancel_request_id"] = (
                self.status.get("cancel_request_id") or 1)
            self.status["revision"] = self.status.get("revision", 0) + 1
        else:
            return {"status": "not_cancelable", "accepted": False,
                    "request_id": None, "target_name": None,
                    "blocker_name": self.status.get("cancel_blocker_name")}
        return {
            "status": state, "accepted": True,
            "request_id": self.status.get("cancel_request_id"),
            "target_name": self.status.get("cancel_target_name", "Print"),
            "target_mode": self.status.get("cancel_target_mode"),
        }

    def clear_cancel(self, request_id=None):
        active_id = self.status.get("cancel_request_id")
        if not self.status.get("cancel_pending", False):
            return {"status": "not_pending", "cleared": False,
                    "request_id": None}
        if request_id is not None and request_id != active_id:
            return {"status": "stale_request", "cleared": False,
                    "request_id": active_id}
        self.status["cancel_pending"] = False
        self.status["cancel_request_id"] = None
        self.status["revision"] = self.status.get("revision", 0) + 1
        return {"status": "cleared", "cleared": True,
                "request_id": active_id}


def base_controller(state="idle"):
    controller = ScenarioController.__new__(ScenarioController)
    controller.reactor = Reactor()
    controller.reactor.register_callback = lambda callback, waketime=None: None
    controller.reactor.register_fd = lambda fd, callback: "netd-fd"
    controller.reactor.unregister_fd = lambda handle: None
    controller.gcode = GCodeRecorder()
    controller.print_stats = StatusObject(
        {"state": state,
         "print_duration": 1.0 if state in ("printing", "paused") else 0.0,
         "info": {"current_layer": 1, "total_layer": 10}})
    controller.virtual_sdcard = VirtualSD(active=state in ("printing", "paused"))
    controller.pending_action = None
    controller.pending_until = 0
    controller.cancel_requested = False
    controller.cancel_waiting_for_heat = False
    controller.cancel_mode = None
    controller.cancel_phase = None
    controller.operation_cancel_on_accept = None
    controller.operation_cancel_on_clear = None
    controller.operation_cancel_request_id = None
    controller.operation_cancel_target_name = None
    controller.operation_cancel_target_mode = None
    controller.dialogs = []
    controller.renderer = mock.MagicMock()
    controller.renderer.output_frozen = False
    controller.renderer.set_header_action.return_value = False
    controller._render_dialog = lambda: None
    controller._paint_page = lambda: None
    controller._render_screen = lambda feature=None: controller._render_dialog()
    controller._last_context_cancel_result = None
    controller._filament_request_token = 0
    controller.busy_phase = 0
    controller.temperature_wait = type("Wait", (), {"variables": {
        "active": False, "cancel": False}})()
    printing = state in ("printing", "paused")
    controller.operation_context = OperationContextStub({
        "contexts": (),
        "context_path": ("Print",) if printing else (),
        "context_types": ("print",) if printing else (),
        "current_state": "PRINTING" if printing else None,
        "cancel_available": printing,
        "cancel_pending": False,
        "cancel_request_id": None,
        "cancel_target_type": "print" if printing else None,
        "cancel_target_name": "Print" if printing else None,
        "cancel_target_mode": "cancelable" if printing else None,
        "cancel_blocker_type": None,
        "cancel_blocker_name": None,
        "revision": 0,
    })
    controller._last_operation_revision = -1
    controller.start_print_macro = type("Start", (), {"variables": {
        "print_started": state in ("printing", "paused")}})()
    controller.page = FEATHER.ScreenPage.IDLE_HOME
    controller.print_state = {
        "idle": FEATHER.PrintState.IDLE,
        "printing": FEATHER.PrintState.PRINTING,
        "paused": FEATHER.PrintState.PAUSED,
    }.get(state, FEATHER.PrintState.IDLE)
    controller.debug = False
    controller.toast_until = 0
    controller.toast_message = ""
    controller._toast = lambda message: None
    controller._render_print_page = lambda: None
    controller._render_cancel_confirm = lambda: None
    controller.file_page = 0
    controller.file_view = "list"
    controller.file_entries = []
    controller.file_entry_cache = {}
    controller.file_entry_loaded_at = {}
    controller.file_scan_loading = False
    controller.file_scan_source = None
    controller.file_scan_phase = 0
    controller.file_scan_token = 0
    controller.file_worker = None
    controller.preview_cache = PreviewCache(256 * 1024)
    controller.file_preview_request = None
    controller.file_preview_failures = set()
    controller.file_preview_preload_signature = ()
    controller.file_preview_preload_attempted = set()
    controller.file_preview_visible_attempted = set()
    controller.selected_file = None
    controller.file_source = "internal"
    controller.usb_storage = None
    controller.network_client = None
    controller.network_operation = None
    controller.network_return_page = FEATHER.ScreenPage.NETWORK_HOME
    controller.network_parent_page = FEATHER.ScreenPage.MAIN_MENU
    controller.network_deadline = 0.0
    controller.network_probe_pending = False
    controller.network_cancel_pending = False
    controller.networks = []
    controller.network_page = 0
    controller.selected_network = None
    controller.password = ""
    controller.network_status = NETWORK_PROTOCOL.blank_status()
    return controller


class FakeNetworkSocket:
    """A netd socket that records commands and replays scripted lines."""

    def __init__(self, replies=()):
        self.sent = []
        self.pending = list(replies)
        self.closed = False

    def sendall(self, data):
        if self.closed:
            raise OSError(errno.EPIPE, "closed")
        self.sent.append(data.decode("utf-8").strip())

    def recv(self, size):
        if not self.pending:
            raise BlockingIOError(errno.EAGAIN, "no data")
        return self.pending.pop(0).encode("utf-8")

    def fileno(self):
        return 42

    def close(self):
        self.closed = True

    def settimeout(self, value):
        pass


def attach_network(controller, replies=(), sock=None):
    """Give the controller a live client over a fake socket.

    The dashboard is stubbed unless the test already replaced it. A published
    status line repaints whatever page is open, and the harness starts on
    IDLE_HOME, so the real dashboard would run and reach printer objects these
    tests have no reason to build. The two tests that assert on the repaint
    install their own stub before calling this.
    """
    if "_update_dashboard" not in controller.__dict__:
        controller._update_dashboard = lambda eventtime: None
    if not hasattr(controller.reactor, "register_fd"):
        controller.reactor.register_fd = lambda fd, callback: "network"
    if not hasattr(controller.reactor, "unregister_fd"):
        controller.reactor.unregister_fd = lambda handle: None
    sock = sock if sock is not None else FakeNetworkSocket(replies)
    previous_status = dict(controller.network_status)
    controller.network_client = NETWORK.NetworkClient(
        controller.reactor, controller._on_network_event,
        opener=lambda: sock)
    controller.network_client.status.update(previous_status)
    controller.network_status = controller.network_client.status
    controller.network_client._attach()
    sock.sent.clear()
    return sock


def composed_controller_surface(controller, painter):
    """Keep frame admission real while replacing printer-dependent page bodies."""
    controller.renderer = FEATHER.FeatherRenderer()
    rendering = RenderCapture(controller.renderer)
    controller._paint_page = painter
    controller._render_screen = lambda feature=None: (
        FEATHER.FeatherScreen._render_screen(controller, feature))
    controller._show_touch_unavailable = lambda: None
    controller._ensure_screen_root()
    return rendering


class ScreenCompositionWorkflowTest(unittest.TestCase):
    def test_periodic_data_and_footer_are_revealed_after_modal_close(self):
        controller = base_controller()
        value = {"text": "OLD"}
        def paint():
            controller.renderer.send(controller.renderer.begin_page("Home") + [
                controller.renderer.text(40, 100, value["text"])])
        rendering = composed_controller_surface(controller, paint)
        controller._show_message("Foreground", controller.page)
        controller.extruder = StatusObject({"temperature": 42, "target": 0})
        controller.heater_bed = StatusObject({"temperature": 30, "target": 0})
        controller.filament_sensor = None
        controller.busy_message = None
        for name in ("_service_network", "_update_eco_backlight",
                     "_poll_usb_storage",
                     "_update_operation_context", "_refresh_emergency_stop"):
            setattr(controller, name, lambda *args: None)
        controller._sync_timelapse_wait_page = lambda: None
        controller._reconcile_pending_action = lambda *args: None
        def update_dashboard(eventtime):
            value["text"] = "LATEST"
            controller.renderer.send([controller.renderer.text(40, 100, value["text"])])
        controller._update_dashboard = update_dashboard

        frames = len(rendering.frames)
        tap = controller.renderer._wire_action("message.ok")
        controller._update_cycle(100)

        self.assertEqual(value["text"], "LATEST")
        self.assertEqual(len(rendering.frames), frames)
        self.assertTrue(rendering.latest.has_text("OLD"))
        self.assertTrue(rendering.latest.has_text("Foreground"))
        self.assertEqual(controller.renderer.decode_action(tap), "message.ok")
        self.assertEqual(set(controller.renderer._buttons), {"message.ok"})
        controller._close_dialog(FEATHER.ScreenDialog.MESSAGE)
        self.assertTrue(rendering.latest.has_text("LATEST"))
        self.assertTrue(rendering.latest.has_text("NOZZLE 42/0C | BED 30/0C"))

    def test_same_page_layout_refresh_is_deferred_but_page_transition_paints(self):
        controller, _ = ActionPromptProtocolTest.controller_with_navigation()
        rendering = RenderCapture(controller.renderer)
        content = {"text": "FIRST", "x": 40}
        def paint():
            controller.renderer.send(controller.renderer.begin_page(controller.page.name) + [
                controller.renderer.text(content["x"], 100, content["text"])])
        controller._render_home = paint
        controller._render_print_page = paint
        controller._show_message("Foreground", controller.page)
        frames = len(rendering.frames)
        content.update(text="LATEST", x=180)

        controller._render_screen()
        controller._show_page(controller.page)

        self.assertEqual(len(rendering.frames), frames)
        self.assertTrue(rendering.latest.has_text("FIRST"))
        controller._show_page(FEATHER.ScreenPage.PRINTING)
        self.assertEqual(len(rendering.frames), frames + 1)
        self.assertTrue(rendering.latest.has_text("PRINTING"))
        self.assertTrue(rendering.latest.has_text("LATEST"))
        self.assertTrue(rendering.latest.has_text("Foreground"))
        self.assertEqual(set(controller.renderer._buttons), {"message.ok"})
        controller._render_screen()
        self.assertEqual(len(rendering.frames), frames + 1)

        content["text"] = "AFTER TRANSITION"
        controller._close_dialog(FEATHER.ScreenDialog.MESSAGE)
        self.assertTrue(rendering.latest.has_text("AFTER TRANSITION"))
        self.assertFalse(rendering.latest.has_text("Foreground"))

    def test_page_steps_in_one_flow_use_the_same_transition_rule(self):
        controller, _ = ActionPromptProtocolTest.controller_with_navigation()
        rendering = RenderCapture(controller.renderer)
        controller._paint_page = lambda: controller.renderer.send(
            controller.renderer.begin_page(controller.page.name))
        controller._show_page(FEATHER.ScreenPage.CALIBRATION_CONFIRM)
        controller._show_message("Foreground", controller.page)
        frames = len(rendering.frames)

        controller._show_page(FEATHER.ScreenPage.CALIBRATION_PROGRESS)

        self.assertEqual(len(rendering.frames), frames + 1)
        self.assertTrue(rendering.latest.has_text("CALIBRATION_PROGRESS"))
        self.assertTrue(rendering.latest.has_text("Foreground"))
        controller._show_page(controller.page)
        self.assertEqual(len(rendering.frames), frames + 1)

    def test_renderer_recovery_restores_an_unchanged_modal(self):
        controller, _ = ActionPromptProtocolTest.controller_with_navigation()
        rendering = RenderCapture(controller.renderer)
        controller._show_message("Foreground", controller.page)
        frames = len(rendering.frames)

        controller._renderer_restarted()
        controller._redraw_after_dropped_frame()

        self.assertEqual(len(rendering.frames), frames + 2)
        self.assertTrue(rendering.latest.has_text("Foreground"))
        self.assertEqual(set(controller.renderer._buttons), {"message.ok"})

    def test_blocking_operation_suspends_and_restores_the_same_prompt(self):
        controller = base_controller()
        controller.busy_message = None
        def paint():
            if controller.busy_message:
                controller.renderer.loader(controller.busy_message)
            else:
                controller.renderer.send(controller.renderer.begin_page("Home"))
        rendering = composed_controller_surface(controller, paint)
        controller._show_page = lambda page: controller._render_screen()
        controller._start_action_prompt("Retained")
        controller._append_action_prompt_button("CONFIRM|CONFIRM_COMMAND")
        controller._show_action_prompt()
        prompt = controller._current_dialog_instance()
        def run(command):
            self.assertTrue(rendering.latest.has_text("HOMING..."))
            self.assertNotIn("prompt.button.0", controller.renderer._buttons)
            self.assertIsNone(controller._current_dialog())
        controller._run_script = run

        controller._run_blocking_gcode("G28", "HOMING...")

        self.assertIs(controller._current_dialog_instance(), prompt)
        self.assertTrue(rendering.latest.has_action("prompt.button.0"))


class FileWorkflowTest(unittest.TestCase):
    def test_scan_completion_preserves_dialog_and_reveals_latest_files(self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.FILE_BROWSER
        def paint_files():
            controller.renderer.send(controller.renderer.begin_page("Files") + [
                controller.renderer.text(40, 100, entry.name)
                for entry in controller.file_entries])
        controller._render_file_browser = paint_files
        rendering = composed_controller_surface(controller, paint_files)
        controller._show_message("Scanning files", controller.page)
        entries = [FILES.FileEntry("new.gcode", "/data/new.gcode")]

        controller._finish_file_scan(0, "internal", entries, None)

        self.assertIs(controller.file_entry_cache["internal"], entries)
        frame = rendering.latest
        self.assertFalse(frame.has_text("new.gcode"))
        self.assertEqual(frame.texts[-1].value, "Scanning files")
        self.assertEqual(set(controller.renderer._buttons), {"message.ok"})
        controller._close_dialog(FEATHER.ScreenDialog.MESSAGE)
        self.assertTrue(rendering.latest.has_text("new.gcode"))
        self.assertFalse(rendering.latest.has_text("Scanning files"))

    def test_file_worker_keeps_io_off_caller_and_delivers_on_scheduler(self):
        callbacks = queue.Queue()
        delivered = []
        worker = FILES.FileWorker(callbacks.put)
        try:
            self.assertTrue(worker.submit(
                lambda: threading.current_thread().name,
                lambda value, error: delivered.append((value, error))))
            callback = callbacks.get(timeout=1.0)
            self.assertEqual(delivered, [])
            callback(0.0)
            self.assertEqual(delivered, [("feather-file-worker", None)])
        finally:
            worker.stop()
        self.assertFalse(worker.submit(lambda: None, lambda value, error: None))

    def test_file_worker_reports_a_superseded_queued_task(self):
        callbacks = queue.Queue()
        started = threading.Event()
        release = threading.Event()
        delivered = []
        worker = FILES.FileWorker(callbacks.put)
        try:
            def blocking_task():
                started.set()
                release.wait(1.0)
                return "first"

            self.assertTrue(worker.submit(
                blocking_task,
                lambda value, error: delivered.append(("first", error))))
            self.assertTrue(started.wait(1.0))
            self.assertTrue(worker.submit(
                lambda: "second",
                lambda value, error: delivered.append(("second", error))))
            self.assertTrue(worker.submit(
                lambda: "third",
                lambda value, error: delivered.append(("third", error))))

            callbacks.get(timeout=1.0)(0.0)
            self.assertEqual(delivered[0][0], "second")
            self.assertIsInstance(
                delivered[0][1], FILES.FileTaskSuperseded)
        finally:
            release.set()
            worker.stop()

    def test_file_browser_loads_in_background_and_pages_use_cached_scan(self):
        class PendingWorker:
            def __init__(self):
                self.requests = []

            def submit(self, task, callback):
                self.requests.append((task, callback))
                return True

        controller = base_controller()
        controller.page = FEATHER.ScreenPage.FILE_BROWSER
        controller.file_source = "internal"
        controller.file_page = 0
        controller.file_entries = []
        controller.file_entry_cache = {}
        controller.file_entry_loaded_at = {}
        controller.file_scan_loading = False
        controller.file_scan_source = None
        controller.file_scan_phase = 0
        controller.file_scan_token = 0
        controller.usb_storage = None
        controller.file_worker = PendingWorker()
        controller.renderer = FEATHER.FeatherRenderer()
        rendering = RenderCapture(controller.renderer)

        old_entries = [FILES.FileEntry(
            "old-%d.gcode" % index, "/data/old-%d.gcode" % index)
            for index in range(7)]
        new_entries = [FILES.FileEntry(
            "new-%d.gcode" % index, "/data/new-%d.gcode" % index)
            for index in range(7)]
        controller._build_file_scan_task = lambda source: lambda: old_entries

        controller._render_file_browser()

        self.assertEqual(len(controller.file_worker.requests), 1)
        self.assertTrue(controller.file_scan_loading)

        # A newer request supersedes an in-flight result. This covers USB
        # changes and explicit refreshes racing a slow flash scan.
        first_callback = controller.file_worker.requests[0][1]
        controller._invalidate_file_entries("internal")
        controller.file_scan_loading = False
        controller._build_file_scan_task = lambda source: lambda: new_entries
        controller._start_file_scan("internal")
        second_callback = controller.file_worker.requests[1][1]
        first_callback(old_entries, None)
        self.assertNotIn("internal", controller.file_entry_cache)
        second_callback(new_entries, None)

        self.assertFalse(controller.file_scan_loading)
        self.assertIs(controller.file_entry_cache["internal"], new_entries)
        self.assertTrue(rendering.latest.has_action("file.refresh"))
        requests_before_page_change = len(
            controller.file_worker.requests)

        # A browser left open keeps its snapshot even after the reopen TTL.
        controller.reactor.now += FILE_PAGES.FILE_CACHE_TTL + 1.0
        controller._handle_file_action("file.next")

        self.assertEqual(controller.file_page, 1)
        self.assertEqual(
            len(controller.file_worker.requests),
            requests_before_page_change)

        controller._build_file_scan_task = lambda source: lambda: new_entries
        controller._handle_file_action("file.refresh")

        self.assertEqual(controller.file_source, "internal")
        self.assertEqual(controller.file_page, 0)
        self.assertEqual(
            len(controller.file_worker.requests),
            requests_before_page_change + 1)

    def test_file_scan_drops_failures_for_changed_non_preloaded_files(self):
        controller = base_controller()
        entries = [
            FILES.FileEntry(
                "part-%d.gcode" % index, "/data/part-%d.gcode" % index,
                size=100 + index, mtime=10 + index)
            for index in range(16)
        ]
        stale_key = controller._file_preview_key(entries[-1])
        usb_entry = FILES.FileEntry(
            "usb.gcode", "/data/USB/usb.gcode", size=10, mtime=5)
        usb_key = controller._file_preview_key(usb_entry)
        controller.file_entry_cache["usb"] = [usb_entry]
        controller.file_preview_failures.update((stale_key, usb_key))
        controller.file_preview_preload_signature = tuple(
            controller._file_preview_key(entry) for entry in entries[:15])
        entries[-1] = FILES.FileEntry(
            "part-15.gcode", "/data/part-15.gcode", size=999, mtime=99)

        controller._finish_file_scan(0, "internal", entries, None)

        self.assertNotIn(stale_key, controller.file_preview_failures)
        self.assertIn(usb_key, controller.file_preview_failures)

    def test_file_tiles_defer_preview_work_and_show_large_placeholders(self):
        class PendingWorker:
            def __init__(self):
                self.requests = []

            def submit(self, task, callback):
                self.requests.append((task, callback))
                return True

        controller = base_controller()
        controller.page = FEATHER.ScreenPage.FILE_BROWSER
        controller.file_view = "tiles"
        controller.file_page = 0
        controller.file_entries = [
            FILES.FileEntry(
                "part-%d.gcode" % index, "/data/part-%d.gcode" % index,
                size=100 + index, mtime=10 + index)
            for index in range(4)
        ]
        controller.file_worker = PendingWorker()
        controller.renderer = FEATHER.FeatherRenderer()
        rendering = RenderCapture(controller.renderer)

        with mock.patch.object(
                FILE_PAGES, "load_preview",
                side_effect=AssertionError("preview ran in reactor")):
            controller._render_file_entries()

        self.assertEqual(len(controller.file_worker.requests), 1)
        self.assertTrue(rendering.latest.has_text("LOADING..."))
        self.assertEqual(
            rendering.latest.button("file.view.tiles").state, "selected")
        self.assertEqual(
            rendering.latest.button("file.view.list").bounds.height, 46)
        for action in (
                "file.view.list", "file.view.tiles", "file.refresh"):
            button = rendering.latest.button(action)
            self.assertLessEqual(
                get_font_metrics().text_width(button.label, button.font),
                button.bounds.width
                - 2 * FEATHER.FeatherRenderer.BUTTON_TEXT_PADDING)
        for index in range(3):
            tile = rendering.latest.button("file.item%d" % index).bounds
            self.assertGreaterEqual(tile.width, 200)
            self.assertGreaterEqual(tile.height, 200)

    def test_file_tiles_preload_first_fifteen_files_one_at_a_time(self):
        class PendingWorker:
            def __init__(self):
                self.requests = []

            def submit(self, task, callback):
                self.requests.append((task, callback))
                return True

        controller = base_controller()
        controller.page = FEATHER.ScreenPage.FILE_BROWSER
        controller.file_view = "tiles"
        controller.file_page = 0
        controller.file_entries = [
            FILES.FileEntry(
                "part-%d.gcode" % index, "/data/part-%d.gcode" % index,
                size=100 + index, mtime=10 + index)
            for index in range(18)
        ]
        controller.renderer = FEATHER.FeatherRenderer()
        RenderCapture(controller.renderer)
        controller.file_worker = PendingWorker()
        controller.preview_cache = PreviewCache(64 * 1024)
        controller._render_file_entries()

        loaded = []
        foreground_index = 0
        while controller.file_preview_request is not None:
            loaded.append(controller.file_preview_request["key"][0])
            callback = controller.file_worker.requests[
                foreground_index][1]
            foreground_index += 1
            blob = bytes((foreground_index,)) * 20000
            callback({
                "image": {},
                "color": controller.renderer.color(FEATHER.ThemeColor.PRIMARY),
                "blob": blob,
            }, None)
        self.assertEqual(
            loaded, ["/data/part-%d.gcode" % index for index in range(15)])
        self.assertIsNone(controller.file_preview_request)
        self.assertLess(len(controller.preview_cache), 15)
        self.assertGreater(len(controller.preview_cache), 0)
        self.assertEqual(len(controller.file_worker.requests), 15)

    def test_scan_cancels_visible_or_preloaded_preview(self):
        for cached_visible in (False, True):
            with self.subTest(cached_visible=cached_visible):
                controller = base_controller()
                controller.page = FEATHER.ScreenPage.FILE_BROWSER
                controller.file_view = "tiles"
                controller.file_entries = [
                    FILES.FileEntry(str(i), "/data/%d.gcode" % i)
                    for i in range(5)]
                controller.renderer = FEATHER.FeatherRenderer()
                RenderCapture(controller.renderer)
                controller.file_worker = mock.Mock()
                if cached_visible:
                    for entry in controller.file_entries[:3]:
                        controller.preview_cache.store(
                            controller._file_preview_key(entry), None)
                controller._render_file_entries()
                request = controller.file_preview_request
                task, callback = controller.file_worker.submit.call_args.args
                controller._build_file_scan_task = lambda source: lambda: []
                controller._start_file_scan("internal")

                with mock.patch.object(FILE_PAGES, "load_preview") as load:
                    with self.assertRaises(FILE_PAGES.PreviewCancelled):
                        task()
                load.assert_not_called()
                callback(None, RuntimeError("late failure"))
                self.assertNotIn(request["key"], controller.file_preview_failures)
                self.assertTrue(controller.file_scan_loading)

    def test_page_change_cancels_old_preview_and_ignores_its_result(self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.FILE_BROWSER
        controller.file_view = "tiles"
        controller.file_entries = [
            FILES.FileEntry(str(i), "/data/%d.gcode" % i)
            for i in range(6)]
        controller.renderer = FEATHER.FeatherRenderer()
        RenderCapture(controller.renderer)
        controller.file_worker = mock.Mock()
        controller._render_file_entries()
        old_request = controller.file_preview_request
        old_callback = controller.file_worker.submit.call_args.args[1]
        controller.file_page = 1
        controller._render_file_entries()
        current = controller.file_preview_request

        self.assertTrue(old_request["cancel"].is_set())
        self.assertEqual(current["key"][0], "/data/3.gcode")
        old_callback(None, RuntimeError("late failure"))
        self.assertIs(controller.file_preview_request, current)
        self.assertEqual(controller.file_preview_failures, set())

    def test_theme_recolor_is_submitted_to_the_worker(self):
        controller = base_controller()
        controller.renderer = FEATHER.FeatherRenderer()
        entry = FILES.FileEntry(
            "part.gcode", "/data/part.gcode", size=100, mtime=10)
        key = controller._file_preview_key(entry)
        controller.preview_cache.store(key, {
            "image": {}, "color": "old-theme", "blob": b"old",
        })
        controller.file_worker = mock.Mock()

        with mock.patch.object(
                FILE_PAGES, "colorize_preview",
                side_effect=AssertionError("recolor ran in reactor")):
            found, blob = controller._cached_file_preview(entry)

        self.assertFalse(found)
        self.assertIsNone(blob)
        task, callback = controller.file_worker.submit.call_args.args
        with mock.patch.object(
                FILE_PAGES, "colorize_preview",
                return_value=(b"recolored",)) as colorize:
            value = task()
        colorize.assert_called_once()
        callback(value, None)
        found, blob = controller._cached_file_preview(entry)
        self.assertTrue(found)
        self.assertEqual(blob, b"recolored")

    def test_theme_recolor_failure_waits_for_explicit_scan(self):
        controller = base_controller()
        controller.renderer = FEATHER.FeatherRenderer()
        entry = FILES.FileEntry(
            "part.gcode", "/data/part.gcode", size=100, mtime=10)
        key = controller._file_preview_key(entry)
        controller.preview_cache.store(key, {
            "image": {}, "color": "old-theme", "blob": b"old",
        })
        controller.file_worker = mock.Mock()

        self.assertEqual(controller._cached_file_preview(entry), (False, None))
        callback = controller.file_worker.submit.call_args.args[1]
        callback(None, RuntimeError("recolor failed"))

        self.assertEqual(controller._cached_file_preview(entry), (True, None))
        self.assertEqual(controller.file_worker.submit.call_count, 1)

    def test_preload_candidate_collection_stops_after_fifteen_files(self):
        class LargeEntries:
            def __init__(self):
                self.visited = 0

            def __iter__(self):
                for index in range(100):
                    self.visited += 1
                    if self.visited > FILE_PAGES.FILE_PRELOAD_LIMIT:
                        raise AssertionError("walked past preload limit")
                    yield FILES.FileEntry(
                        "part-%d.gcode" % index,
                        "/data/part-%d.gcode" % index)

        controller = base_controller()
        entries = LargeEntries()
        controller.file_entries = entries

        _visible, preload = controller._file_preview_candidates([])

        self.assertEqual(len(preload), FILE_PAGES.FILE_PRELOAD_LIMIT)
        self.assertEqual(entries.visited, FILE_PAGES.FILE_PRELOAD_LIMIT)

    def test_visible_page_takes_priority_over_background_preload(self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.FILE_BROWSER
        controller.file_view = "tiles"
        controller.file_page = 0
        controller.file_entries = [
            FILES.FileEntry(
                "part-%d.gcode" % index, "/data/part-%d.gcode" % index,
                size=100 + index, mtime=10 + index)
            for index in range(18)
        ]
        controller.renderer = FEATHER.FeatherRenderer()
        RenderCapture(controller.renderer)
        controller.file_worker = mock.Mock()
        controller._render_file_entries()
        first_callback = controller.file_worker.submit.call_args.args[1]

        controller.file_page = 5
        controller._render_file_entries()
        first_callback({
            "image": {},
            "color": controller.renderer.color(FEATHER.ThemeColor.PRIMARY),
            "blob": b"first",
        }, None)

        self.assertEqual(
            controller.file_preview_request["key"][0],
            "/data/part-15.gcode")

    def test_file_tile_cache_survives_navigation_and_invalidates_by_metadata(self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.FILE_BROWSER
        controller.file_view = "tiles"
        controller.file_page = 0
        entry = FILES.FileEntry(
            "a-very-long-print-filename.gcode", "/data/part.gcode",
            size=100, mtime=10)
        controller.file_entries = [entry, FILES.FileEntry(
            "second.gcode", "/data/second.gcode", size=200, mtime=20)]
        controller.renderer = FEATHER.FeatherRenderer()
        rendering = RenderCapture(controller.renderer)
        controller.file_worker = mock.Mock()
        key = controller._file_preview_key(entry)
        controller.preview_cache.store(key, {
            "image": {},
            "color": controller.renderer.color(FEATHER.ThemeColor.PRIMARY),
            "blob": b"cached",
        })

        controller._render_file_entries()

        self.assertTrue(rendering.latest.has_text(
            "a-very-long-print-filename"))
        label = rendering.latest.text("a-very-long-print-filename")
        self.assertEqual(label.font, "Roboto Bold 12pt")
        self.assertEqual(label.max_height, 68)
        self.assertEqual(
            controller.file_preview_request["key"][0], "/data/second.gcode")

        feedback = []
        send = controller.renderer.send
        def capture_feedback(commands, **kwargs):
            feedback.append(list(commands))
            return send(commands, **kwargs)
        controller.renderer.send = capture_feedback
        self.assertTrue(controller.renderer.flash_button("file.item0"))
        self.assertTrue(controller.renderer.restore_button("file.item0"))
        self.assertEqual(len(feedback), 2)
        for batch in feedback:
            self.assertTrue(any(
                getattr(command, "payload", None) == b"cached"
                for command in batch))
            self.assertTrue(any(
                "a-very-long-print-filename" in command
                for command in batch))

        second_callback = controller.file_worker.submit.call_args.args[1]
        generation = controller.renderer._generation
        self.assertTrue(controller.renderer.flash_button("file.item1"))
        entry.mtime = 30
        second_callback({
            "image": {},
            "color": controller.renderer.color(FEATHER.ThemeColor.PRIMARY),
            "blob": b"second",
        }, None)
        self.assertEqual(controller.renderer._generation, generation)
        self.assertTrue(any(
            getattr(command, "payload", None) == b"second"
            for command in feedback[-1]))
        self.assertTrue(controller.renderer.restore_button("file.item1"))
        self.assertTrue(any(
            getattr(command, "payload", None) == b"second"
            for command in feedback[-1]))
        self.assertEqual(
            controller.file_preview_request["key"],
            controller._file_preview_key(entry))

    def test_preview_failure_retries_only_after_explicit_scan(self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.FILE_BROWSER
        controller.file_view = "tiles"
        controller.file_page = 0
        entry = FILES.FileEntry(
            "part.gcode", "/data/part.gcode", size=100, mtime=10)
        controller.file_entries = [entry]
        controller.renderer = FEATHER.FeatherRenderer()
        RenderCapture(controller.renderer)
        controller.file_worker = mock.Mock()
        controller._render_file_entries()
        callback = controller.file_worker.submit.call_args.args[1]

        callback(None, RuntimeError("temporary helper failure"))

        self.assertIsNone(controller.file_preview_request)
        self.assertFalse(
            controller.preview_cache.contains(
                controller._file_preview_key(entry)))
        self.assertIn(
            controller._file_preview_key(entry),
            controller.file_preview_failures)

        controller._render_file_browser = controller._render_file_entries
        controller._handle_file_action("file.refresh")

        self.assertEqual(controller.file_preview_failures, set())
        self.assertEqual(controller.file_worker.submit.call_count, 2)
        self.assertEqual(
            controller.file_preview_request["key"],
            controller._file_preview_key(entry))

    def test_scan_ignores_the_previous_preview_result(self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.FILE_BROWSER
        controller.file_view = "tiles"
        controller.file_page = 0
        entry = FILES.FileEntry(
            "part.gcode", "/data/part.gcode", size=100, mtime=10)
        controller.file_entries = [entry]
        controller.renderer = FEATHER.FeatherRenderer()
        RenderCapture(controller.renderer)
        controller.file_worker = mock.Mock()
        controller._render_file_entries()
        old_request = controller.file_preview_request
        old_callback = controller.file_worker.submit.call_args.args[1]
        controller._render_file_browser = controller._render_file_entries

        controller._handle_file_action("file.refresh")
        new_request = controller.file_preview_request
        old_callback(None, RuntimeError("old helper failure"))

        self.assertIsNot(new_request, old_request)
        self.assertIs(controller.file_preview_request, new_request)
        self.assertEqual(controller.file_preview_failures, set())

    def test_missing_preview_is_cached_without_repeated_helper_calls(self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.FILE_BROWSER
        controller.file_view = "tiles"
        controller.file_page = 0
        entry = FILES.FileEntry(
            "part.gcode", "/data/part.gcode", size=100, mtime=10)
        controller.file_entries = [entry]
        controller.renderer = FEATHER.FeatherRenderer()
        RenderCapture(controller.renderer)
        controller.file_worker = mock.Mock()
        controller._render_file_entries()
        callback = controller.file_worker.submit.call_args.args[1]

        callback(None, None)
        controller._render_file_entries()

        self.assertTrue(
            controller.preview_cache.contains(
                controller._file_preview_key(entry)))
        self.assertEqual(controller.file_worker.submit.call_count, 1)

    def test_file_view_changes_page_size_without_changing_entry_actions(self):
        controller = base_controller()
        controller.file_view = "list"
        saved = []

        class Params:
            variables = {"feather_file_view": "list"}

            def set_value(self, key, value):
                self.variables[key] = value
                saved.append((key, value))

        controller.params = Params()
        controller.file_page = 2
        controller.file_entries = [
            FILES.FileEntry(
                "part-%d.gcode" % index, "/data/part-%d.gcode" % index)
            for index in range(4)
        ]
        rendered = []
        controller._render_file_browser = lambda: rendered.append(
            (controller.file_view, controller.file_page))

        controller._handle_file_action("file.view.tiles")

        self.assertEqual(controller.file_view, "tiles")
        self.assertEqual(
            controller.params.variables["feather_file_view"], "tiles")
        self.assertEqual(saved, [("feather_file_view", "tiles")])
        self.assertEqual(controller.file_page, 0)
        self.assertEqual(rendered, [("tiles", 0)])

        shown = []
        controller.file_page = 1
        controller._show_page = shown.append
        controller._handle_file_action("file.item0")

        self.assertIs(controller.selected_file, controller.file_entries[3])
        self.assertEqual(shown, [FEATHER.ScreenPage.FILE_CONFIRM])

    def test_configured_file_view_accepts_only_supported_values(self):
        controller = base_controller()
        controller.params = type("Params", (), {
            "variables": {"feather_file_view": "tiles"},
        })()
        self.assertEqual(controller._configured_file_view(), "tiles")

        controller.params.variables["feather_file_view"] = "broken"
        self.assertEqual(controller._configured_file_view(), "list")

    def test_file_view_stays_unchanged_when_persistence_fails(self):
        controller = base_controller()
        controller.file_view = "list"
        controller.file_page = 2

        class Params:
            def set_value(self, key, value):
                raise RuntimeError("save failed")

        controller.params = Params()
        rendered = []
        controller._render_file_browser = lambda: rendered.append(True)

        with self.assertRaisesRegex(RuntimeError, "save failed"):
            controller._handle_file_action("file.view.tiles")

        self.assertEqual((controller.file_view, controller.file_page), ("list", 2))
        self.assertEqual(rendered, [])

    def test_file_cache_ttl_applies_when_browser_is_reopened(self):
        controller = base_controller()
        entries = [FILES.FileEntry("part.gcode", "/data/part.gcode")]
        controller.file_entry_cache = {"internal": entries, "usb": entries}
        controller.file_entry_loaded_at = {"internal": 100.0, "usb": 100.0}

        controller.reactor.now = 100.0 + FILE_PAGES.FILE_CACHE_TTL - 0.01
        self.assertFalse(
            controller._expire_file_entries_if_stale("internal"))
        self.assertIn("internal", controller.file_entry_cache)

        controller.reactor.now = 100.0 + FILE_PAGES.FILE_CACHE_TTL
        self.assertTrue(
            controller._expire_file_entries_if_stale("internal"))
        self.assertNotIn("internal", controller.file_entry_cache)
        self.assertNotIn("internal", controller.file_entry_loaded_at)
        self.assertIn("usb", controller.file_entry_cache)

    def test_file_browser_flattens_two_levels_and_skips_hidden_trees(self):
        with tempfile.TemporaryDirectory() as root:
            level_one = os.path.join(root, "models")
            level_two = os.path.join(level_one, "project")
            level_three = os.path.join(level_two, "archive")
            hidden = os.path.join(root, ".hidden")
            hidden_nested = os.path.join(level_one, ".cache")
            os.makedirs(level_three)
            os.mkdir(hidden)
            os.mkdir(hidden_nested)
            os.symlink(level_two, os.path.join(root, "linked-project"))
            paths = (
                os.path.join(root, "root.gcode"),
                os.path.join(level_one, "level-one.gco"),
                os.path.join(level_two, "level-two.g"),
                os.path.join(level_three, "too-deep.gcode"),
                os.path.join(hidden, "secret.gcode"),
                os.path.join(hidden_nested, "cached.gcode"),
                os.path.join(level_one, "notes.txt"),
            )
            for path in paths:
                pathlib.Path(path).write_text("G28\n", encoding="utf-8")

            controller = base_controller()
            controller.virtual_sdcard = VirtualSD(root)
            controller._load_file_entries()
            self.assertEqual(
                {entry.name for entry in controller.file_entries},
                {"root.gcode", "models/level-one.gco",
                 "models/project/level-two.g"})
            self.assertTrue(all(
                not entry.directory for entry in controller.file_entries))

    def test_file_browser_uses_newest_of_print_and_file_times(self):
        with tempfile.TemporaryDirectory() as root:
            old_printed = os.path.join(root, "old-printed.gcode")
            recently_added = os.path.join(root, "recently-added.gcode")
            for path in (old_printed, recently_added):
                pathlib.Path(path).write_text("G28\n", encoding="utf-8")
            os.utime(old_printed, (10, 10))
            os.utime(recently_added, (30, 30))
            history_path = os.path.join(root, ".feather-history.json")
            history = FEATHER.PrintHistory(history_path)
            history.record(root, old_printed, 40)
            history.record(root, recently_added, 20)
            self.assertEqual(history.latest_path(), "old-printed.gcode")

            controller = base_controller()
            controller.virtual_sdcard = VirtualSD(root)
            controller.print_history = FEATHER.PrintHistory(history_path)
            controller._load_file_entries()

            self.assertEqual(
                [entry.name for entry in controller.file_entries],
                ["old-printed.gcode", "recently-added.gcode"])

    def test_real_print_transition_persists_history(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "models", "part.gcode")
            os.makedirs(os.path.dirname(path))
            pathlib.Path(path).write_text("G28\n", encoding="utf-8")
            history_path = os.path.join(root, ".feather-history.json")
            controller = base_controller()
            controller.virtual_sdcard = VirtualSD(root, True, path)
            controller.print_history = FEATHER.PrintHistory(history_path)
            controller._show_page = lambda page: None

            with mock.patch("feather.screen.pages.files.time.time",
                            return_value=1234.0):
                controller._change_print_state(
                    FEATHER.PrintState.PRINTING, "printing")

            reloaded = FEATHER.PrintHistory(history_path)
            self.assertEqual(
                reloaded.last_printed("models/part.gcode"), 1234.0)
            self.assertEqual(reloaded.latest_path(), "models/part.gcode")
            self.assertEqual(controller.last_job_path, "models/part.gcode")
            self.assertEqual(controller.last_job_name, "part.gcode")

    def test_idle_last_job_opens_repeat_print_confirmation(self):
        with tempfile.TemporaryDirectory() as root:
            relative = "models/part.gcode"
            path = os.path.join(root, relative)
            os.makedirs(os.path.dirname(path))
            pathlib.Path(path).write_text("G28\n", encoding="utf-8")
            controller = base_controller()
            controller.virtual_sdcard = VirtualSD(root)
            controller.last_job_path = relative
            controller.last_job_name = "part.gcode"
            shown = []
            controller._show_page = shown.append

            action = controller._resolve_semantic_ui_action("home.last_job")
            controller._dispatch_semantic_ui_action(action)

            self.assertEqual(shown, [FEATHER.ScreenPage.FILE_CONFIRM])
            self.assertEqual(
                controller.selected_file.path, os.path.realpath(path))
            self.assertEqual(controller.selected_file.name, "part.gcode")
            self.assertEqual(
                controller.file_confirm_return_page, FEATHER.ScreenPage.IDLE_HOME)
            self.assertTrue(controller.file_confirm_repeat)

    def test_last_job_is_inactive_while_printing(self):
        controller = base_controller("printing")
        opened = []
        controller._open_last_job = lambda: opened.append(True)

        action = controller._resolve_semantic_ui_action("home.last_job")
        controller._dispatch_semantic_ui_action(action)

        self.assertEqual(opened, [])

        controller = base_controller()
        controller.print_state = FEATHER.PrintState.PREPARING
        controller._open_last_job = lambda: opened.append(True)
        action = controller._resolve_semantic_ui_action("home.last_job")
        controller._dispatch_semantic_ui_action(action)

        self.assertEqual(opened, [])

    def test_missing_last_job_file_is_rejected_before_confirmation(self):
        with tempfile.TemporaryDirectory() as root:
            controller = base_controller()
            controller.virtual_sdcard = VirtualSD(root)
            controller.last_job_path = "removed.gcode"

            with self.assertRaisesRegex(
                    RuntimeError, "no longer available"):
                controller._open_last_job()

    def test_repeat_confirmation_returns_to_dashboard(self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.FILE_CONFIRM
        controller.selected_file = FILES.FileEntry(
            "part.gcode", "/data/part.gcode")
        controller.file_confirm_return_page = FEATHER.ScreenPage.IDLE_HOME
        controller.file_confirm_repeat = True
        shown = []
        controller._show_page = shown.append

        controller._go_back()

        self.assertEqual(shown, [FEATHER.ScreenPage.IDLE_HOME])
        self.assertIsNone(controller.selected_file)
        self.assertFalse(controller.file_confirm_repeat)

    def test_repeat_confirmation_changes_the_start_control(self):
        controller = base_controller()
        controller.renderer = FEATHER.FeatherRenderer()
        controller.selected_file = FILES.FileEntry(
            "part.gcode", "/data/part.gcode", size=1024)
        controller.file_confirm_repeat = True
        rendering = RenderCapture(controller.renderer)

        controller._render_file_confirm()
        repeat_label = rendering.latest.button("file.start").label
        controller.file_confirm_repeat = False
        controller._render_file_confirm()

        self.assertNotEqual(
            repeat_label, rendering.latest.button("file.start").label)

    def test_file_confirmation_reveals_auto_profile_only_after_mesh_rebuild(self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.FILE_CONFIRM
        controller.renderer = FEATHER.FeatherRenderer()
        controller.selected_file = FILES.FileEntry(
            "part.gcode", "/data/part.gcode", size=1024)
        controller.file_confirm_rebuild_mesh = False
        controller.file_confirm_auto_mesh = False
        rendering = RenderCapture(controller.renderer)

        controller._render_file_confirm()

        self.assertTrue(rendering.latest.has_action("file.mesh.rebuild"))
        self.assertFalse(rendering.latest.has_action("file.mesh.auto"))

        controller._handle_file_action("file.mesh.rebuild")

        self.assertTrue(controller.file_confirm_rebuild_mesh)
        self.assertTrue(rendering.latest.has_action("file.mesh.auto"))

        controller._handle_file_action("file.mesh.auto")
        self.assertTrue(controller.file_confirm_auto_mesh)

        controller._handle_file_action("file.mesh.rebuild")
        self.assertFalse(controller.file_confirm_rebuild_mesh)
        self.assertFalse(controller.file_confirm_auto_mesh)
        self.assertFalse(rendering.latest.has_action("file.mesh.auto"))

    def test_file_confirmation_explains_full_mesh_override_when_kamp_enabled(self):
        controller = base_controller()
        controller.renderer = FEATHER.FeatherRenderer()
        controller.selected_file = FILES.FileEntry(
            "part.gcode", "/data/part.gcode", size=1024)
        controller.params = type("Params", (), {
            "variables": {"use_kamp": False}})()
        rendering = RenderCapture(controller.renderer)

        controller._render_file_confirm()
        normal_copy = tuple(text.value for text in rendering.latest.texts)
        controller.params.variables["use_kamp"] = True
        controller._render_file_confirm()

        kamp_copy = tuple(text.value for text in rendering.latest.texts)
        self.assertEqual(len(kamp_copy), len(normal_copy))
        self.assertEqual(sum(
            before != after
            for before, after in zip(normal_copy, kamp_copy)), 1)

    def test_file_confirmation_controls_stay_inside_content_area(self):
        controller = base_controller()
        controller.renderer = FEATHER.FeatherRenderer()
        controller.selected_file = FILES.FileEntry(
            "a-very-long-print-file-name.gcode", "/data/part.gcode",
            size=1024)
        controller.file_confirm_rebuild_mesh = True
        controller.file_confirm_auto_mesh = True
        rendering = RenderCapture(controller.renderer)

        controller._render_file_confirm()

        toggles = []
        for action in ("file.mesh.rebuild", "file.mesh.auto"):
            bounds = rendering.latest.toggle(action).bounds
            self.assertGreaterEqual(bounds.x, 0)
            self.assertGreaterEqual(bounds.y, 54)
            self.assertLessEqual(bounds.right, 800)
            self.assertLessEqual(bounds.bottom, 430)
            toggles.append(bounds)
        start = rendering.latest.button("file.start").bounds
        self.assertLess(toggles[0].bottom, toggles[1].y)
        self.assertLess(toggles[1].bottom, start.y)
        self.assertLessEqual(start.bottom, 430)

    def test_file_confirmation_uses_compact_unboxed_layout(self):
        controller = base_controller()
        controller.renderer = FEATHER.FeatherRenderer()
        controller.selected_file = FILES.FileEntry(
            "part.gcode", "/data/part.gcode", size=1024)
        controller.file_confirm_rebuild_mesh = True
        controller.file_confirm_auto_mesh = True
        rendering = RenderCapture(controller.renderer)

        controller._render_file_confirm()

        frame = rendering.latest
        filename = frame.text(controller.selected_file.name)
        size = frame.text(controller._format_size(controller.selected_file.size))
        first_toggle = frame.toggle("file.mesh.rebuild").bounds
        start = frame.button("file.start").bounds
        full_width_option_panels = [
            shape for shape in frame.shapes
            if (size.y < shape.bounds.y < start.y
                and shape.bounds.width >= 700
                and shape.bounds.height >= 20)]

        self.assertEqual(full_width_option_panels, [])
        self.assertGreaterEqual(filename.y, 94)
        self.assertGreaterEqual(size.y - filename.y, 40)
        self.assertGreaterEqual(first_toggle.y - size.y, 30)
        self.assertGreaterEqual(start.height, 72)
        self.assertLessEqual(start.height, 96)
        self.assertGreaterEqual(CONTENT_BOTTOM - start.bottom, 24)
        self.assertLessEqual(CONTENT_BOTTOM - start.bottom, 36)

    def test_back_from_file_confirmation_discards_mesh_options(self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.FILE_CONFIRM
        controller.selected_file = FILES.FileEntry(
            "part.gcode", "/data/part.gcode")
        controller.file_confirm_rebuild_mesh = True
        controller.file_confirm_auto_mesh = True
        shown = []
        controller._show_page = shown.append

        controller._go_back()

        self.assertFalse(controller.file_confirm_rebuild_mesh)
        self.assertFalse(controller.file_confirm_auto_mesh)
        self.assertEqual(shown, [FEATHER.ScreenPage.FILE_BROWSER])

    def test_start_file_rechecks_path_and_escapes_filename(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, 'part "one".gcode')
            pathlib.Path(path).write_text("G28\n", encoding="utf-8")
            controller = base_controller()
            controller.virtual_sdcard = VirtualSD(root)
            controller.selected_file = FILES.FileEntry(
                os.path.basename(path), path, size=1, mtime=1)
            controller._start_selected_file()
            file_stat = os.stat(path)
            self.assertEqual(
                controller.gcode.commands[0].splitlines(), [
                    'SDCARD_PRINT_FILE FILENAME="part \\"one\\".gcode"',
                    "SET_GCODE_VARIABLE MACRO=START_PRINT "
                    "VARIABLE=feather_force_leveling VALUE=None",
                    "SET_GCODE_VARIABLE MACRO=START_PRINT "
                    "VARIABLE=feather_mesh_name VALUE=None",
                ])
            self.assertEqual(controller.last_job_path, 'part "one".gcode')
            self.assertEqual(controller.selected_file.size, file_stat.st_size)
            self.assertEqual(
                controller.selected_file.mtime, file_stat.st_mtime)
            os.unlink(path)
            with self.assertRaisesRegex(RuntimeError, "no longer available"):
                controller._start_selected_file()

    def test_start_file_passes_one_print_mesh_options_after_file_load(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "part.gcode")
            pathlib.Path(path).write_text("G28\n", encoding="utf-8")
            controller = base_controller()
            controller.virtual_sdcard = VirtualSD(root)
            controller.selected_file = FILES.FileEntry(
                "part.gcode", path)
            controller.file_confirm_rebuild_mesh = True
            controller.file_confirm_auto_mesh = True

            controller._start_selected_file()

            self.assertEqual(controller.gcode.commands[0].splitlines(), [
                'SDCARD_PRINT_FILE FILENAME="part.gcode"',
                "SET_GCODE_VARIABLE MACRO=START_PRINT "
                "VARIABLE=feather_force_leveling VALUE=True",
                "SET_GCODE_VARIABLE MACRO=START_PRINT "
                "VARIABLE=feather_mesh_name VALUE='\"auto\"'",
            ])
            self.assertFalse(controller.file_confirm_rebuild_mesh)
            self.assertFalse(controller.file_confirm_auto_mesh)

    def test_start_file_string_option_survives_klipper_parameter_parsing(self):
        class LiteralParsingGCode:
            def __init__(self):
                self.variables = {}

            def run_script(self, script):
                for command in script.splitlines():
                    arguments = shlex.split(command)
                    if arguments[0] != "SET_GCODE_VARIABLE":
                        continue
                    params = dict(
                        argument.split("=", 1) for argument in arguments[1:])
                    self.variables[params["VARIABLE"]] = ast.literal_eval(
                        params["VALUE"])

        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "part.gcode")
            pathlib.Path(path).write_text("G28\n", encoding="utf-8")
            controller = base_controller()
            controller.gcode = LiteralParsingGCode()
            controller.virtual_sdcard = VirtualSD(root)
            controller.selected_file = FILES.FileEntry(
                "part.gcode", path)
            controller.file_confirm_rebuild_mesh = True
            controller.file_confirm_auto_mesh = True

            controller._start_selected_file()

            self.assertEqual(controller.gcode.variables, {
                "feather_force_leveling": True,
                "feather_mesh_name": "auto",
            })

    def test_completed_forced_auto_mesh_offers_save(self):
        controller = base_controller("printing")
        controller.start_print_macro.variables.update({
            "zforce_leveling": True,
            "zskip_leveling": False,
            "zmesh": "auto",
            "zmesh_generated": "auto",
        })
        controller.bed_mesh = StatusObject({"profile_name": "auto"})
        restarts = []
        messages = []
        controller._restart_klipper = restarts.append
        controller._show_message = lambda message, page, actions=None, title=None: (
            messages.append((message, page, actions, title)))

        controller._change_print_state(FEATHER.PrintState.IDLE, "complete")

        self.assertEqual(restarts, [])
        self.assertEqual(len(messages), 1)
        self.assertIn("ACTIVE FOR THIS SESSION", messages[0][0])
        self.assertEqual(messages[0][2], (
            ("mesh.save", "SAVE & RESTART", "enabled"),
            ("message.ok", "LATER", "enabled")))

    def test_completed_fallback_auto_mesh_offers_save(self):
        # A pure default print start: no mesh argument was given, no profile
        # was loaded, and the workflow generated the persistent 'auto' mesh.
        controller = base_controller("printing")
        controller.start_print_macro.variables.update({
            "zforce_leveling": False,
            "zskip_leveling": False,
            "zmesh": "",
            "zmesh_generated": "auto",
        })
        controller.bed_mesh = StatusObject({"profile_name": "auto"})
        messages = []
        controller._show_message = lambda message, page, actions=None, title=None: (
            messages.append((message, page, actions, title)))

        controller._change_print_state(FEATHER.PrintState.IDLE, "complete")

        self.assertEqual(len(messages), 1)
        self.assertIn("ACTIVE FOR THIS SESSION", messages[0][0])
        self.assertEqual(messages[0][2], (
            ("mesh.save", "SAVE & RESTART", "enabled"),
            ("message.ok", "LATER", "enabled")))

    def test_print_leveling_policy_suppresses_mesh_save_offer(self):
        # print_leveling re-measures the mesh on every print, so persisting
        # the freshly generated 'auto' profile would be pointless noise.
        controller = base_controller("printing")
        controller.params = type("Params", (), {
            "variables": {"print_leveling": 1}})()
        controller.start_print_macro.variables.update({
            "zmesh_generated": "auto",
        })
        controller.bed_mesh = StatusObject({"profile_name": "auto"})
        messages = []
        controller._show_message = lambda message, page, actions=None, title=None: (
            messages.append((message, page, actions, title)))

        controller._change_print_state(FEATHER.PrintState.IDLE, "complete")

        self.assertEqual(len(messages), 1)
        self.assertIsNone(messages[0][2])
        self.assertEqual(messages[0][3], "Print finished")

    def test_mesh_save_prompt_buttons_fit_message_dialog(self):
        controller = base_controller()
        controller.renderer = FEATHER.FeatherRenderer()
        controller.message = (
            "THE NEW AUTO BED MESH IS ACTIVE FOR THIS SESSION. "
            "SAVE IT TO PRINTER.CFG? KLIPPER WILL RESTART.")
        controller.message_actions = (
            ("mesh.save", "SAVE & RESTART", "enabled"),
            ("message.ok", "LATER", "enabled"))
        rendering = RenderCapture(controller.renderer)

        controller._render_message()

        frame = rendering.latest
        body_lines = [text for text in frame.texts
                      if text.font == "JetBrainsMono 8pt"]
        self.assertEqual(" ".join(text.value for text in body_lines),
                         controller.message)
        self.assertTrue(all(text.truncate for text in body_lines))
        self.assertTrue(all(text.max_width is not None
                            and 0 < text.max_width <= 700
                            for text in body_lines))
        save = frame.button("mesh.save").bounds
        later = frame.button("message.ok").bounds
        for button in (save, later):
            self.assertGreaterEqual(button.x, 0)
            self.assertGreaterEqual(button.y, 54)
            self.assertLessEqual(button.right, 800)
            self.assertLessEqual(button.bottom, 430)
        self.assertLess(save.right, later.x)

    def test_message_preserves_explicit_line_breaks(self):
        controller = base_controller()
        controller.renderer = FEATHER.FeatherRenderer()
        lines = ("First line", "Second line")
        controller.message = "\n".join(lines)
        controller.message_actions = (("message.ok", "OK", "enabled"),)
        rendering = RenderCapture(controller.renderer)

        controller._render_message()

        body_lines = [text for text in rendering.latest.texts
                      if text.font == "JetBrainsMono 8pt"
                      and text.max_width == 564]
        self.assertEqual(tuple(text.value for text in body_lines), lines)
        self.assertLess(body_lines[0].y, body_lines[1].y)

    def test_incomplete_generated_auto_mesh_does_not_offer_save(self):
        for stats_state in ("cancelled", "error"):
            with self.subTest(stats_state=stats_state):
                controller = base_controller("printing")
                controller.start_print_macro.variables.update({
                    "zforce_leveling": True,
                    "zskip_leveling": False,
                    "zmesh": "auto",
                    "zmesh_generated": "auto",
                })
                controller.bed_mesh = StatusObject({"profile_name": "auto"})
                restarts = []
                messages = []
                controller._restart_klipper = restarts.append
                controller._show_message = lambda message, page, actions=None, title=None: (
                    messages.append((message, page, actions, title)))

                controller._change_print_state(
                    FEATHER.PrintState.IDLE, stats_state)

                self.assertEqual(restarts, [])
                self.assertEqual(len(messages), 1)
                self.assertIsNone(messages[0][2])
                self.assertEqual(messages[0][3], (
                    "Print cancelled" if stats_state == "cancelled"
                    else "Print failed"))

    def test_completed_print_does_not_offer_save_without_generated_auto(self):
        cases = (
            # A loaded or reused 'auto' profile was not measured this print.
            ({"zmesh_generated": ""}, "auto"),
            # A temporary mesh (KAMP, forced rebuild, stock fallback) is never
            # a persistence candidate even while active.
            ({"zmesh_generated": "default"}, "auto"),
            # A generated mesh that is no longer the active profile.
            ({"zmesh_generated": "auto"}, ""),
            # Legacy forced-leveling variables alone do not trigger the offer.
            ({"zforce_leveling": True, "zskip_leveling": False,
              "zmesh": "auto"}, "auto"),
        )
        for variables, profile_name in cases:
            with self.subTest(variables=variables, profile_name=profile_name):
                controller = base_controller("printing")
                controller.start_print_macro.variables.update(variables)
                controller.bed_mesh = StatusObject({
                    "profile_name": profile_name})
                messages = []
                controller._show_message = (
                    lambda message, page, actions=None, title=None:
                    messages.append((message, page, actions, title)))

                controller._change_print_state(
                    FEATHER.PrintState.IDLE, "complete")

                self.assertEqual(len(messages), 1)
                self.assertIsNone(messages[0][2])
                self.assertEqual(messages[0][3], "Print finished")

    def test_mesh_save_action_requires_idle_and_runs_save_config(self):
        controller = base_controller()
        controller.dialogs = [FEATHER.ScreenDialog.MESSAGE]
        controller.last_action_time = -1
        controller.message_actions = (
            ("mesh.save", "SAVE & RESTART", "enabled"),
            ("message.ok", "LATER", "enabled"))
        checked = []
        restarts = []
        controller._require_idle = lambda: checked.append(True)
        controller._restart_klipper = restarts.append
        controller._blocking_operation_active = lambda: False
        controller.feature_manager = None
        controller.bed_mesh = StatusObject({"profile_name": "auto"})

        controller._dispatch_action("mesh.save")

        self.assertEqual(checked, [True])
        self.assertEqual(restarts, ["SAVE_CONFIG"])

    def test_usb_directory_is_first_and_keeps_internal_list_flat(self):
        with tempfile.TemporaryDirectory() as root:
            internal = os.path.join(root, "internal.gcode")
            usb_root = os.path.join(root, FILES.USB_MOUNT_NAME)
            os.mkdir(usb_root)
            pathlib.Path(internal).write_text("G28\n", encoding="utf-8")
            pathlib.Path(usb_root, "usb.gcode").write_text(
                "G28\n", encoding="utf-8")
            controller = base_controller()
            controller.virtual_sdcard = VirtualSD(root)
            controller.file_source = "internal"
            controller.usb_storage = type("USB", (), {
                "available": True, "mount_point": usb_root})()

            controller._load_file_entries()

            self.assertEqual(controller.file_entries[0].name, "USB")
            self.assertTrue(controller.file_entries[0].directory)
            self.assertEqual(
                [entry.name for entry in controller.file_entries[1:]],
                ["internal.gcode"])

    def test_usb_files_use_flat_recency_sort_and_virtual_sd_path(self):
        with tempfile.TemporaryDirectory() as root:
            usb_root = os.path.join(root, FILES.USB_MOUNT_NAME)
            nested = os.path.join(usb_root, "models")
            os.makedirs(nested)
            old_printed = os.path.join(nested, "old.gcode")
            newest_file = os.path.join(usb_root, "new.gcode")
            pathlib.Path(old_printed).write_text("G28\n", encoding="utf-8")
            pathlib.Path(newest_file).write_text("G28\n", encoding="utf-8")
            os.utime(old_printed, (10, 10))
            os.utime(newest_file, (30, 30))
            history = FEATHER.PrintHistory(os.path.join(root, ".history.json"))
            history.record(root, old_printed, 40)
            controller = base_controller()
            controller.virtual_sdcard = VirtualSD(root)
            controller.print_history = history
            controller.file_source = "usb"
            controller.usb_storage = type("USB", (), {
                "available": True, "mount_point": usb_root})()

            controller._load_file_entries()

            self.assertEqual(
                [entry.name for entry in controller.file_entries],
                ["models/old.gcode", "new.gcode"])
            controller.selected_file = controller.file_entries[0]
            controller._start_selected_file()
            self.assertEqual(controller.gcode.commands[0].splitlines(), [
                'SDCARD_PRINT_FILE FILENAME="USB/models/old.gcode"',
                "SET_GCODE_VARIABLE MACRO=START_PRINT "
                "VARIABLE=feather_force_leveling VALUE=None",
                "SET_GCODE_VARIABLE MACRO=START_PRINT "
                "VARIABLE=feather_mesh_name VALUE=None",
            ])

    def test_usb_directory_navigation_and_removal_return_to_root(self):
        controller = base_controller()
        controller.file_source = "internal"
        controller.file_page = 0
        controller.file_entries = [FILES.FileEntry(
            "USB", "/data/USB", directory=True)]
        rendered = []
        controller._render_file_browser = lambda: rendered.append(
            controller.file_source)

        controller._handle_file_action("file.item0")
        self.assertEqual(controller.file_source, "usb")
        self.assertEqual(rendered, ["usb"])

        controller.file_page = 3
        controller._handle_file_action("file.refresh")
        self.assertEqual(controller.file_source, "usb")
        self.assertEqual(controller.file_page, 0)
        self.assertEqual(rendered, ["usb", "usb"])

        controller.page = FEATHER.ScreenPage.FILE_BROWSER
        shown = []
        controller._show_page = lambda page: shown.append(page)
        controller._go_back()
        self.assertEqual(controller.file_source, "internal")
        self.assertEqual(shown, [FEATHER.ScreenPage.FILE_BROWSER])

        controller.file_source = "usb"
        controller.page = FEATHER.ScreenPage.FILE_BROWSER
        controller.usb_storage = type("USB", (), {
            "available": False,
            "resume": lambda self, eventtime: None,
            "pause": lambda self: None,
            "tick": lambda self, eventtime: True})()
        controller._render_file_browser = lambda: rendered.append(
            controller.file_source)
        controller._poll_usb_storage(10.0)
        self.assertEqual(controller.file_source, "internal")
        self.assertEqual(rendered[-1], "internal")

    def test_usb_scan_race_keeps_browser_alive(self):
        controller = base_controller()
        controller.virtual_sdcard = VirtualSD("/data")
        controller.file_source = "usb"
        controller.usb_storage = type("USB", (), {
            "available": True, "mount_point": "/data/USB"})()

        with mock.patch.object(
                FILE_PAGES, "scan_gcode_files",
                side_effect=RuntimeError("device disappeared")):
            controller._load_file_entries()

        self.assertEqual(controller.file_source, "usb")
        self.assertEqual(controller.file_entries, [])

    def test_usb_removal_from_confirmation_shows_message(self):
        controller = base_controller()
        controller.file_source = "usb"
        controller.page = FEATHER.ScreenPage.FILE_CONFIRM
        controller.selected_file = FILES.FileEntry(
            "job.gcode", "/data/USB/job.gcode")
        controller.usb_storage = type("USB", (), {
            "available": False,
            "resume": lambda self, eventtime: None,
            "pause": lambda self: None,
            "tick": lambda self, eventtime: True})()
        messages = []
        controller._show_message = lambda message, page: messages.append(
            (message, page))

        controller._poll_usb_storage(10.0)

        self.assertEqual(controller.file_source, "internal")
        self.assertIsNone(controller.selected_file)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0][1], FEATHER.ScreenPage.FILE_BROWSER)


class UsbStorageMonitorTest(unittest.TestCase):
    @staticmethod
    def _event(action="add", subsystem="block", usb=True):
        path = ("/devices/platform/usb1/1-1/block/sda"
                if usb else "/devices/platform/mmc/block/mmcblk0")
        return ("ACTION=%s\0SUBSYSTEM=%s\0DEVPATH=%s\0DEVNAME=sda\0"
                % (action, subsystem, path)).encode("ascii")

    def _monitor(self, processes, mounted):
        calls = []
        reactor = UsbReactor()
        event_socket = UsbEventSocket()

        def popen(command, **kwargs):
            calls.append(command)
            return processes.pop(0)

        monitor = FEATHER.UsbStorageMonitor(
            "/data", reactor, popen=popen,
            is_mount=lambda path: mounted[0],
            socket_factory=lambda *args: event_socket)
        return monitor, calls, reactor, event_socket

    def test_initial_reconcile_remove_and_reinsert_are_nonblocking(self):
        mounted = [False]
        processes = [
            UsbProcess("ATTACHED /dev/sda1 vfat\n"),
            UsbProcess("NONE\n", returncode=2),
            UsbProcess("ATTACHED /dev/sdb1 ext4\n"),
        ]
        monitor, calls, _reactor, events = self._monitor(processes, mounted)

        monitor.resume(0.0)
        self.assertFalse(monitor.tick(0.0))
        self.assertEqual(calls[-1][1], "attach")
        mounted[0] = True
        self.assertTrue(monitor.tick(1.0))
        self.assertTrue(monitor.available)
        self.assertEqual(monitor.device, "/dev/sda1")

        events.messages.append(self._event("remove"))
        monitor._handle_events(2.0)
        mounted[0] = False
        monitor.tick(3.0)
        self.assertEqual(calls[-1][1], "attach")
        self.assertTrue(monitor.tick(4.0))
        self.assertFalse(monitor.available)

        events.messages.append(self._event("add"))
        monitor._handle_events(5.0)
        monitor.tick(6.0)
        self.assertEqual(calls[-1][1], "attach")
        mounted[0] = True
        self.assertTrue(monitor.tick(7.0))
        self.assertTrue(monitor.available)
        self.assertEqual(monitor.device, "/dev/sdb1")

    def test_busy_attach_retries_without_marking_drive_available(self):
        mounted = [False]
        processes = [UsbProcess("BUSY\n", returncode=3),
                     UsbProcess("ATTACHED /dev/sda1 vfat\n")]
        monitor, calls, _reactor, _events = self._monitor(processes, mounted)

        monitor.resume(0.0)
        monitor.tick(0.0)
        monitor.tick(1.0)
        self.assertFalse(monitor.available)
        self.assertEqual(len(calls), 1)
        monitor.tick(2.0)
        self.assertEqual(len(calls), 2)
        mounted[0] = True
        self.assertTrue(monitor.tick(3.0))

    def test_irrelevant_events_do_not_start_reconciliation(self):
        mounted = [False]
        monitor, calls, _reactor, events = self._monitor(
            [UsbProcess("NONE\n", returncode=2)], mounted)
        monitor.resume(0.0)
        monitor.tick(0.0)
        monitor.tick(1.0)
        self.assertEqual(len(calls), 1)

        events.messages.extend([
            self._event("change", usb=False),
            self._event("add", subsystem="net"),
        ])
        monitor._handle_events(2.0)
        monitor.tick(3.0)

        self.assertEqual(len(calls), 1)

    def test_event_overflow_keeps_subscription_and_reconciles_state(self):
        mounted = [False]
        monitor, calls, reactor, events = self._monitor(
            [UsbProcess("NONE\n", returncode=2),
             UsbProcess("NONE\n", returncode=2)], mounted)
        monitor.resume(0.0)
        monitor.tick(0.0)
        monitor.tick(1.0)
        self.assertEqual(len(calls), 1)

        events.messages.append(OSError(errno.ENOBUFS, "queue overflowed"))
        with self.assertLogs(level="WARNING") as logs:
            monitor._handle_events(2.0)

        self.assertIn("reconciling current state", logs.output[-1])
        self.assertIs(monitor.event_socket, events)
        self.assertFalse(events.closed)
        self.assertEqual(reactor.unregistered, [])

        monitor.tick(2.0)
        self.assertEqual(len(calls), 2)

    def test_pause_closes_events_and_resume_forces_reconciliation(self):
        mounted = [False]
        monitor, calls, reactor, events = self._monitor(
            [UsbProcess("NONE\n", returncode=2),
             UsbProcess("ATTACHED /dev/sda1 vfat\n")], mounted)
        monitor.resume(0.0)
        monitor.tick(0.0)
        monitor.tick(1.0)

        monitor.pause()
        events.messages.append(self._event("add"))
        self.assertTrue(events.closed)
        self.assertEqual(reactor.unregistered, reactor.registered)
        self.assertFalse(monitor.tick(2.0))
        self.assertEqual(len(calls), 1)

        replacement = UsbEventSocket()
        monitor._socket_factory = lambda *args: replacement
        monitor.resume(3.0)
        monitor.tick(3.0)
        mounted[0] = True
        self.assertTrue(monitor.tick(4.0))
        self.assertEqual(calls[-1][1], "attach")
        self.assertTrue(monitor.available)

    def test_controller_does_no_usb_work_while_printing(self):
        for print_state in (
                FEATHER.PrintState.PREPARING, FEATHER.PrintState.PRINTING,
                FEATHER.PrintState.PAUSED):
            controller = base_controller("printing")
            controller.print_state = print_state
            calls = []
            controller.usb_storage = type("USB", (), {
                "available": True,
                "resume": lambda self, eventtime: calls.append("resume"),
                "pause": lambda self: calls.append("pause"),
                "tick": lambda self, eventtime: calls.append("tick")})()

            controller._poll_usb_storage(10.0)

            self.assertEqual(calls, ["pause"])

    def test_pause_terminates_inflight_reconciliation(self):
        running = UsbProcess("", running=True)
        monitor, _calls, reactor, events = self._monitor(
            [running], [False])
        monitor.resume(0.0)
        monitor.tick(0.0)

        with mock.patch("feather.files.os.killpg") as killpg:
            monitor.pause()

        killpg.assert_called_once_with(running.pid, FEATHER.signal.SIGTERM)
        self.assertFalse(monitor.active)
        self.assertTrue(events.closed)
        self.assertEqual(reactor.unregistered, reactor.registered)

    def test_stuck_helper_is_terminated_after_timeout(self):
        running = UsbProcess("", running=True)
        monitor, _calls, _reactor, _events = self._monitor(
            [running], [False])
        monitor.resume(0.0)
        monitor.tick(0.0)

        with mock.patch("feather.files.os.killpg") as killpg:
            monitor.tick(FILES.USB_HELPER_TIMEOUT)

        killpg.assert_called_once_with(running.pid, FEATHER.signal.SIGTERM)
        self.assertFalse(monitor.available)

    def test_stop_signals_active_helper_and_starts_detach(self):
        running = UsbProcess("", running=True)
        detached = UsbProcess("DETACHED\n")
        monitor, calls, reactor, events = self._monitor([detached], [False])
        monitor.resume(0.0)
        monitor.process = running

        with mock.patch("feather.files.os.killpg") as killpg:
            monitor.stop()

        killpg.assert_called_once_with(running.pid, FEATHER.signal.SIGTERM)
        self.assertEqual(calls[-1][1], "detach")
        self.assertTrue(monitor.stopped)
        self.assertTrue(events.closed)
        self.assertEqual(reactor.unregistered, reactor.registered)


class PrintWorkflowTest(unittest.TestCase):
    @staticmethod
    def timelapse_wait_controller(wait_status="unavailable"):
        controller = base_controller("paused")
        controller.page = FEATHER.ScreenPage.TIMELAPSE_WAIT
        controller.last_action_time = -1.0
        guard = StatusObject({"waiting": True, "sd_held": True,
                              "prompt_open": True, "wait_status": wait_status})
        controller.printer = mock.Mock()
        controller.printer.lookup_object.side_effect = (
            lambda name, default=None: guard if name ==
            "gcode_macro _TIMELAPSE_START_GUARD" else default)
        capture = composed_controller_surface(
            controller, controller._render_timelapse_wait)
        controller._apply_safety_visibility()
        controller._render_screen()
        return controller, guard, capture

    def test_timelapse_wait_cancels_print_without_confirmation(self):
        for status in ("busy", "unavailable", "idle"):
            with self.subTest(status=status):
                controller, guard, capture = self.timelapse_wait_controller(status)
                tap = controller.renderer._wire_action("print.cancel")

                controller._dispatch_action(controller.renderer.decode_action(tap))

                self.assertEqual(controller.gcode.commands, ["CANCEL_PRINT"])
                self.assertIsNone(controller._current_dialog())
                self.assertEqual(controller.page, FEATHER.ScreenPage.TIMELAPSE_WAIT)
                self.assertEqual(controller.pending_action, "print.cancel.confirm")
                self.assertTrue(controller.cancel_requested)
                self.assertTrue(capture.latest.has_text("PLEASE WAIT WHILE THE PRINT STOPS"))
                self.assertFalse(capture.latest.has_action("timelapse.wait.cancel_render"))

                controller.reactor.now += 1.0
                controller._dispatch_action("print.cancel")
                self.assertEqual(controller.gcode.commands, ["CANCEL_PRINT"])

    def test_wait_choice_keeps_unavailable_reason_visible_on_the_wait_page(self):
        from tests.gcode_macro_harness import MacroExecution

        controller, guard, capture = self.timelapse_wait_controller()
        def respond(command):
            parts = shlex.split(command)
            for part in parts:
                if part.startswith("MSG=action:prompt_"):
                    controller._handle_gcode_output("// " + part[4:])
        runtime = MacroExecution(
            [(pathlib.Path(__file__).parents[1] / "macros/timelapse.cfg",
              "_TIMELAPSE_START_WAIT_CHOICE", "gcode_macro")],
            {"gcode_macro _TIMELAPSE_START_GUARD": guard.status}, respond)
        controller._run_script = lambda command, **kwargs: runtime.run(command)
        controller._handle_gcode_output("\n".join((
            "// action:prompt_begin Previous timelapse",
            "// action:prompt_footer_button WAIT|_TIMELAPSE_START_WAIT_CHOICE",
            "// action:prompt_show")))

        controller._dispatch_action("prompt.button.0")

        self.assertIsNone(controller._current_dialog())
        self.assertEqual(controller.page, FEATHER.ScreenPage.TIMELAPSE_WAIT)
        self.assertFalse(capture.latest.has_action("timelapse.wait.keep"))
        self.assertTrue(capture.latest.has_action("print.cancel"))
        self.assertTrue(capture.latest.has_action("timelapse.wait.cancel_render"))
        self.assertTrue(capture.latest.has_text("TIMELAPSE STATUS UNAVAILABLE"))
        self.assertTrue(capture.latest.has_text(
            "CHECKING MOONRAKER. PRINT WAITS FOR CONFIRMED IDLE"))
        self.assertFalse(capture.latest.has_text(
            "THE PRINT STARTS WHEN THE TIMELAPSE FINISHES"))
        self.assertTrue(guard.status["waiting"])
        self.assertEqual(controller.gcode.commands, [])

    def test_wait_reason_updates_preserve_choices_and_crossing_taps(self):
        controller, guard, capture = self.timelapse_wait_controller("busy")
        self.assertTrue(capture.latest.has_text("WAITING FOR THE PREVIOUS TIMELAPSE"))
        tap = controller.renderer._wire_action("print.cancel")
        for status, headline in (
                ("unavailable", "TIMELAPSE STATUS UNAVAILABLE"),
                ("busy", "WAITING FOR THE PREVIOUS TIMELAPSE"),
                ("idle", "TIMELAPSE IS READY")):
            with self.subTest(status=status):
                guard.status["wait_status"] = status
                start = len(capture.frames)

                controller._update_timelapse_wait()

                self.assertTrue(any(frame.has_text(headline)
                                    for frame in capture.frames[start:]))
                self.assertEqual(controller.renderer.decode_action(tap), "print.cancel")
                self.assertFalse(capture.latest.has_action("timelapse.wait.keep"))
                for action in ("print.cancel",
                               "timelapse.wait.cancel_render", "global.abort"):
                    self.assertTrue(capture.latest.has_action(action))
                self.assertEqual(controller.gcode.commands, [])

    def test_wait_page_recovers_under_modal_and_preserves_cancellation_text(self):
        controller, guard, capture = self.timelapse_wait_controller()
        controller._show_message("Notice", controller.page)
        count = len(capture.frames)
        guard.status["wait_status"] = "busy"

        controller._update_timelapse_wait()

        self.assertEqual(len(capture.frames), count)
        controller._close_dialog(FEATHER.ScreenDialog.MESSAGE)
        self.assertTrue(capture.latest.has_text("WAITING FOR THE PREVIOUS TIMELAPSE"))
        controller.pending_action = "print.cancel.confirm"
        controller._render_screen()
        guard.status["wait_status"] = "unavailable"
        controller._update_timelapse_wait()
        self.assertFalse(any(frame.has_text("TIMELAPSE STATUS UNAVAILABLE")
                             for frame in capture.frames[count:]))
        self.assertTrue(any(frame.has_text("PLEASE WAIT WHILE THE PRINT STOPS")
                            for frame in capture.frames[count:]))
        self.assertFalse(capture.latest.has_action("timelapse.wait.cancel_render"))

    def test_missing_wait_reason_does_not_claim_the_previous_timelapse_is_busy(self):
        for reason in (None, "", "unknown"):
            with self.subTest(reason=reason):
                controller, guard, capture = self.timelapse_wait_controller(reason)
                self.assertTrue(capture.latest.has_text("TIMELAPSE STATUS UNAVAILABLE"))
                self.assertFalse(capture.latest.has_text(
                    "THE PRINT STARTS WHEN THE TIMELAPSE FINISHES"))

    def test_print_page_controls_follow_preparation_and_pause_state(self):
        cases = (
            ("ordinary preparation", "printing", FEATHER.PrintState.PREPARING,
             False, False, "print.pause", False),
            ("timelapse preparation", "paused", FEATHER.PrintState.PREPARING,
             True, False, "print.pause", False),
            ("virtual SD still held", "printing", FEATHER.PrintState.PRINTING,
             True, True, "print.pause", False),
            ("ordinary printing", "printing", FEATHER.PrintState.PRINTING,
             False, True, "print.pause", True),
            ("manual pause", "paused", FEATHER.PrintState.PAUSED,
             False, True, "print.resume", True),
        )
        for (name, stats, print_state, held, started, primary,
             controls_ready) in cases:
            with self.subTest(name=name):
                controller = base_controller(stats)
                controller.page = (FEATHER.ScreenPage.PAUSED
                                   if print_state == FEATHER.PrintState.PAUSED
                                   else FEATHER.ScreenPage.PRINTING)
                controller.print_state = print_state
                controller.start_print_macro.variables["print_started"] = started
                controller.toolhead = StatusObject({"homed_axes": "xyz"})
                guard = StatusObject({"waiting": False, "sd_held": held})
                controller.printer = mock.Mock()
                controller.printer.lookup_object.side_effect = (
                    lambda key, default=None: guard if key ==
                    "gcode_macro _TIMELAPSE_START_GUARD" else default)
                controller._prepare_gcode_preview = lambda stats=None: None
                controller._current_print_progress_values = (
                    lambda *args: (0, ("0s", "0s", "0 / 10", 0.0)))
                controller._display_status_text = lambda eventtime: name
                controller._render_print_page = lambda: (
                    FEATHER.FeatherScreen._render_print_page(controller))
                rendering = composed_controller_surface(
                    controller, controller._render_print_page)

                controller._render_print_page()

                for action in (primary, "print.filament", "print.z"):
                    self.assertEqual(rendering.latest.has_action(action),
                                     controls_ready, action)
                self.assertTrue(rendering.latest.has_action("print.cancel"))

                if not controls_ready:
                    controller._handle_print_action("print.pause")
                    controller._handle_print_action("print.filament")
                    with self.assertRaisesRegex(RuntimeError, "Z adjust"):
                        controller._handle_print_action("print.z")
                    self.assertEqual(controller.gcode.commands, [])

    def test_timelapse_wait_returns_to_print_page_during_preparation(self):
        controller = base_controller("paused")
        controller.page = FEATHER.ScreenPage.TIMELAPSE_WAIT
        controller.print_state = FEATHER.PrintState.PAUSED
        controller.start_print_macro.variables["print_started"] = False
        guard = StatusObject({"waiting": True, "sd_held": True})
        controller.printer = mock.Mock()
        controller.printer.lookup_object.side_effect = lambda name, default=None: {
            "gcode_macro _TIMELAPSE_START_GUARD": guard,
        }.get(name, default)
        controller._show_page = lambda page: setattr(controller, "page", page)

        controller._sync_timelapse_wait_page()
        self.assertEqual(controller.page, FEATHER.ScreenPage.TIMELAPSE_WAIT)

        guard.status["waiting"] = False
        controller._reconcile_print_state(100)
        controller._sync_timelapse_wait_page()

        self.assertEqual(controller.print_state, FEATHER.PrintState.PREPARING)
        self.assertEqual(controller.page, FEATHER.ScreenPage.PRINTING)
        self.assertEqual(controller.page_for_print_state(), FEATHER.ScreenPage.PRINTING)
        self.assertFalse(controller._print_controls_ready())

        controller.print_stats.status["state"] = "printing"
        controller.start_print_macro.variables["print_started"] = True
        controller._reconcile_print_state(101)
        self.assertFalse(controller._print_controls_ready())
        self.assertFalse(controller._live_z_adjust_allowed(101))

        guard.status["sd_held"] = False
        controller._sync_timelapse_wait_page()
        self.assertEqual(controller.page, FEATHER.ScreenPage.PRINTING)
        self.assertEqual(controller.print_state, FEATHER.PrintState.PRINTING)
        self.assertTrue(controller._print_controls_ready())

    def test_first_capture_user_pause_shows_pause_and_enables_resume_after_restore(self):
        controller = base_controller("paused")
        controller.start_print_macro.variables["print_started"] = False
        controller.page = FEATHER.ScreenPage.TIMELAPSE_WAIT
        controller.print_stats.status["state"] = "printing"
        controller.print_state = FEATHER.PrintState.PREPARING
        controller.virtual_sdcard.active = False
        guard = StatusObject({"waiting": False, "sd_held": True})
        frame = StatusObject({"is_paused": True, "user_pause_requested": True})
        controller.pause_resume = StatusObject({"is_paused": True})
        controller.printer = mock.Mock()
        controller.printer.lookup_object.side_effect = lambda name, default=None: {
            "gcode_macro _TIMELAPSE_START_GUARD": guard,
            "gcode_macro TIMELAPSE_TAKE_FRAME": frame,
        }.get(name, default)
        controller.toolhead = StatusObject({"homed_axes": "xyz"})
        controller._prepare_gcode_preview = lambda stats=None: None
        controller._current_print_progress_values = lambda *args: (0, ("0s", "0s", "0 / 10", 0.0))
        controller._display_status_text = lambda eventtime: "Paused"
        controller._render_print_page = lambda: FEATHER.FeatherScreen._render_print_page(controller)
        rendering = composed_controller_surface(controller, controller._render_print_page)
        controller._show_page = lambda page: (setattr(controller, "page", page), controller._render_screen())

        self.assertEqual(controller._reconcile_print_state(100), "paused")
        controller._sync_timelapse_wait_page()

        self.assertEqual(controller.page, FEATHER.ScreenPage.PAUSED)
        self.assertFalse(rendering.latest.has_action("print.resume"))
        self.assertFalse(rendering.latest.has_action("print.filament"))
        self.assertFalse(rendering.latest.has_action("print.z"))
        controller._handle_print_action("print.resume")
        self.assertEqual(controller.gcode.commands, [])
        frame.status["is_paused"] = False
        controller._update_print_progress(controller.reactor.monotonic())
        self.assertEqual(rendering.latest.button("print.resume").state, "enabled")
        self.assertTrue(rendering.latest.has_action("print.filament"))
        self.assertTrue(rendering.latest.has_action("print.z"))
        commands = []
        def resume(command, message):
            commands.append(command)
            guard.status["sd_held"] = False
            frame.status["user_pause_requested"] = False
            controller.pause_resume.status["is_paused"] = False
            controller.print_stats.status["state"] = "printing"
            controller.virtual_sdcard.active = True
        controller._run_blocking_gcode = resume
        controller._handle_print_action("print.resume")
        self.assertEqual(commands, ["RESUME"])
        self.assertEqual(controller.page_for_print_state(), FEATHER.ScreenPage.PRINTING)

    def test_filament_is_rejected_during_start_print_preparation(self):
        controller = base_controller("printing")
        controller.start_print_macro.variables["print_started"] = False
        notices = []
        controller._toast = notices.append

        controller._handle_print_action("print.filament")

        self.assertEqual(controller.gcode.commands, [])
        self.assertEqual(len(notices), 1)

    def test_cancel_invalidates_filament_request_waiting_on_pause(self):
        controller = base_controller("printing")
        controller.page = FEATHER.ScreenPage.PRINTING
        opened = []
        controller._open_filament = lambda from_pause: opened.append(from_pause)
        commands = []

        def run(command):
            commands.append(command)
            if command == "PAUSE":
                controller._handle_print_action("print.cancel")
                controller._handle_operation_cancel_action(
                    "operation.cancel.confirm")
            elif command == "_CONTEXT_CANCEL_POINT":
                controller.print_stats.status["state"] = "cancelled"

        controller._run_script = run
        controller._handle_print_action("print.filament")

        self.assertEqual(commands, ["PAUSE", "_CONTEXT_CANCEL_POINT"])
        self.assertEqual(opened, [])

    def test_pause_resume_and_cancel_are_state_gated(self):
        controller = base_controller("printing")
        controller._handle_print_action("print.pause")
        self.assertEqual(controller.gcode.commands, ["PAUSE"])
        self.assertEqual(controller.pending_action, "print.pause")

        controller.gcode.commands[:] = []
        controller.pending_action = None
        controller._handle_print_action("print.resume")
        self.assertEqual(controller.gcode.commands, [])

        controller._handle_print_action("print.cancel")
        self.assertEqual(controller.page, FEATHER.ScreenPage.OPERATION_CANCEL)
        self.assertIsNone(controller._current_dialog())

    def test_pause_refreshes_resume_button_before_next_periodic_update(self):
        controller = base_controller("printing")
        controller.page = FEATHER.ScreenPage.PRINTING
        pages = []

        def show(page):
            controller.page = page
            pages.append((page, controller.pending_action))

        def pause(command):
            self.assertEqual(command, "PAUSE")
            controller.print_stats.status["state"] = "paused"

        controller._show_page = show
        controller._run_script = pause
        controller._handle_print_action("print.pause")

        self.assertEqual(controller.print_state, FEATHER.PrintState.PAUSED)
        self.assertIsNone(controller.pending_action)
        self.assertEqual(pages[-1], (FEATHER.ScreenPage.PAUSED, None))

    def test_resume_refreshes_pause_button_after_blocking_dialog(self):
        controller = base_controller("paused")
        controller.page = FEATHER.ScreenPage.PAUSED
        pages = []

        def show(page):
            controller.page = page
            pages.append((page, controller.pending_action))

        def resume(command, message):
            self.assertEqual((command, message),
                             ("RESUME", "RESUMING PRINT..."))
            controller.print_stats.status.update(
                state="printing", print_duration=1.0)

        controller._show_page = show
        controller._run_blocking_gcode = resume
        controller._handle_print_action("print.resume")

        self.assertEqual(controller.print_state, FEATHER.PrintState.PRINTING)
        self.assertIsNone(controller.pending_action)
        self.assertEqual(pages[-1], (FEATHER.ScreenPage.PRINTING, None))

    def test_resume_rejection_is_shown_on_feather(self):
        controller = base_controller("paused")
        controller.page = FEATHER.ScreenPage.PAUSED
        controller.last_action_time = -100
        controller._resolve_semantic_ui_action = lambda action: None
        controller._action_allowed = lambda page, action: True
        messages = []
        controller._show_message = lambda message, page: messages.append(
            (message, page))

        def reject(command):
            self.assertEqual(command, "RESUME")
            raise RuntimeError(
                "No filament detected. Load filament and press Resume.")

        controller._run_script = reject
        with mock.patch("feather_screen.logging.exception"):
            controller._dispatch_action("print.resume")

        self.assertEqual(messages, [(
            "No filament detected. Load filament and press Resume.",
            FEATHER.ScreenPage.PAUSED)])
        self.assertEqual(controller.print_stats.status["state"], "paused")
        self.assertIsNone(controller.pending_action)

    def test_resume_shows_blocking_wait_with_emergency_abort(self):
        controller = base_controller("paused")
        controller.page = FEATHER.ScreenPage.PAUSED
        controller.busy_message = None
        composed_controller_surface(controller, lambda:
            controller.renderer.loader(controller.busy_message, 0))
        controller._show_page = lambda page: None
        immediate = []
        controller._run_immediate_command = lambda command: immediate.append(
            command)
        observed = []

        def run(command):
            observed.append((
                command, controller.busy_message,
                controller._blocking_operation_active(),
                controller._safety_decision().visible,
                controller.renderer._loader_active,
            ))
            controller._handle_touch_action("global.abort")

        controller.gcode.run_script = run
        controller._handle_print_action("print.resume")

        self.assertEqual(observed, [
            ("RESUME", "RESUMING PRINT...", True, True, True)])
        self.assertEqual(immediate, ["M112"])
        self.assertIsNone(controller.busy_message)

    def test_cancel_requires_confirmation_before_macro(self):
        controller = base_controller("paused")
        pages = []
        controller._show_page = pages.append
        controller._handle_print_action("print.cancel")
        self.assertEqual(pages, [FEATHER.ScreenPage.OPERATION_CANCEL])
        self.assertIsNone(controller._current_dialog())
        self.assertEqual(controller.gcode.commands, [])
        controller._handle_operation_cancel_action(
            "operation.cancel.confirm")
        self.assertEqual(
            controller.gcode.commands, ["_CONTEXT_CANCEL_POINT"])

    def test_started_print_requests_cancel_then_queues_safe_point(self):
        controller = base_controller("printing")
        controller.start_print_macro.variables["print_started"] = True

        controller._handle_print_action("print.cancel")
        controller._handle_operation_cancel_action(
            "operation.cancel.confirm")
        self.assertEqual(controller.gcode.commands,
                         ["_CONTEXT_CANCEL_POINT"])
        self.assertTrue(
            controller.operation_context.status["cancel_pending"])
        self.assertEqual(controller.cancel_mode, "pending")

    def test_accepted_cancel_is_painted_before_the_safe_point_dispatch(self):
        controller = base_controller("printing")
        controller.start_print_macro.variables["print_started"] = True
        controller._handle_print_action("print.cancel")
        events = []
        controller._render_cancel_confirm = lambda: events.append("render")
        controller._run_script = lambda command, show_notice=True: (
            events.append(command))

        controller._handle_operation_cancel_action(
            "operation.cancel.confirm")

        self.assertEqual(events, ["render", "_CONTEXT_CANCEL_POINT"])

    def test_delivered_cancellation_is_not_reported_as_an_action_failure(self):
        controller = base_controller("printing")
        controller.start_print_macro.variables["print_started"] = True
        messages = []
        controller._show_message = lambda message, page: messages.append(
            message)

        def deliver(command, show_notice=True):
            raise RuntimeError("Operation cancelled: Print")

        controller._run_script = deliver
        controller._handle_print_action("print.cancel")

        controller._handle_operation_cancel_action(
            "operation.cancel.confirm")

        self.assertEqual(messages, [])
        self.assertEqual(controller.cancel_mode, "pending")

    def test_continue_that_lost_the_race_reports_it_instead_of_doing_nothing(
            self):
        controller = base_controller("printing")
        controller.start_print_macro.variables["print_started"] = True
        controller._handle_print_action("print.cancel")
        controller._handle_operation_cancel_action(
            "operation.cancel.confirm")
        # The safe point consumed the request before the tap arrived.
        controller.operation_context.status["cancel_pending"] = False
        toasts = []
        controller._toast = toasts.append

        controller._handle_operation_cancel_action(
            "operation.cancel.continue")

        self.assertEqual(toasts, ["CANCELLATION ALREADY STARTED"])
        self.assertEqual(controller.cancel_mode, "pending")
        self.assertTrue(controller.cancel_requested)

    def test_cancel_during_preparation_wait_dispatches_immediate_m108(self):
        controller = base_controller("printing")
        controller.start_print_macro.variables["print_started"] = False
        controller.temperature_wait = type("Wait", (), {
            "variables": {"active": True, "cancel": False}})()
        recorder = GCodeRecorder()
        controller.gcode = recorder
        controller._handle_print_action("print.cancel")
        controller._handle_operation_cancel_action(
            "operation.cancel.confirm")
        self.assertEqual(recorder.commands, ["M108"])
        self.assertTrue(controller.cancel_requested)
        self.assertTrue(controller.cancel_waiting_for_heat)
        self.assertTrue(
            controller.operation_context.status["cancel_pending"])

    def test_non_cancelable_operation_offers_abort_without_dispatching_it(self):
        controller = base_controller("printing")
        controller.operation_context.status.update(
            cancel_available=False, cancel_target_type=None,
            cancel_target_name=None)

        controller._handle_print_action("print.cancel")

        self.assertEqual(controller.gcode.commands, [])
        self.assertEqual(controller.cancel_mode, "not_cancelable")
        self.assertFalse(controller.cancel_requested)

    def test_non_cancelable_page_offers_continue_or_confirmed_m112(self):
        controller = base_controller("printing")
        controller.page = FEATHER.ScreenPage.OPERATION_CANCEL
        controller.cancel_mode = "not_cancelable"
        controller.renderer = FEATHER.FeatherRenderer()
        rendering = RenderCapture(controller.renderer)

        FEATHER.FeatherScreen._render_cancel_confirm(controller)

        self.assertTrue(rendering.latest.has_action("operation.cancel.back"))
        self.assertTrue(rendering.latest.has_action("operation.cancel.force"))
        frame = rendering.latest
        title = frame.text("CANNOT CANCEL SAFELY")
        explanation = frame.text("THIS OPERATION HAS NO SAFE CANCEL POINT")
        consequence = frame.text(
            "ABORT STOPS THE PRINTER IMMEDIATELY (M112)")
        self.assertGreaterEqual(explanation.y - title.y, 50)
        self.assertGreaterEqual(consequence.y - explanation.y, 45)
        self.assertGreaterEqual(
            frame.button("operation.cancel.back").bounds.y - consequence.y,
            50)

        commands = []
        controller._run_immediate_command = commands.append
        controller._handle_touch_action("operation.cancel.force")
        self.assertEqual(commands, ["M112"])

    def test_pending_cancel_page_does_not_repaint_over_message(self):
        controller = base_controller("printing")
        controller.page = FEATHER.ScreenPage.OPERATION_CANCEL
        controller.cancel_mode = "pending"
        controller.dialogs = [FEATHER.ScreenDialog.MESSAGE]

        controller._update_cancel_progress()

        controller.renderer.send.assert_not_called()

    def test_operation_cancel_uses_full_page_without_modal_scrim(self):
        controller = base_controller("printing")
        controller.page = FEATHER.ScreenPage.OPERATION_CANCEL
        controller.cancel_mode = "not_cancelable"
        controller.renderer = FEATHER.FeatherRenderer()

        FEATHER.FeatherScreen._render_cancel_confirm(controller)

        frame = controller.renderer._batch_queue.get(timeout=0)
        self.assertNotIn(controller.renderer.modal_scrim(), frame.commands)
        self.assertTrue(any('"CANNOT CANCEL SAFELY"' in command
                            for command in frame.commands))
        self.assertIsNone(controller._current_dialog())

    def test_cancel_during_atomic_homing_waits_for_next_context_point(self):
        controller = base_controller("printing")
        controller.start_print_macro.variables["print_started"] = False
        calls = []

        def serialized(command):
            calls.append(("serialized", command))

        controller.gcode.run_script = serialized
        controller._handle_print_action("print.cancel")
        controller._handle_operation_cancel_action(
            "operation.cancel.confirm")
        self.assertEqual(calls, [])
        self.assertTrue(
            controller.operation_context.status["cancel_pending"])
        self.assertFalse(controller.cancel_waiting_for_heat)

    def test_normal_cancel_is_rejected_by_immediate_dispatch(self):
        controller = base_controller()
        with self.assertRaisesRegex(ValueError, "Unsupported immediate"):
            controller._run_immediate_command("CANCEL_PRINT")

    def test_feather_prefers_mutex_serialized_gcode_runner(self):
        controller = base_controller()
        calls = []
        controller.gcode.run_script = lambda command: calls.append(("serialized", command))
        controller.gcode.run_script_from_command = (
            lambda command: calls.append(("direct", command)))
        controller._run_script("G28")
        self.assertEqual(calls, [("serialized", "G28")])

    def test_pending_cancel_page_offers_continue_and_force_abort(self):
        controller = base_controller("paused")
        controller.pending_action = "print.cancel.confirm"
        controller.cancel_mode = "pending"
        controller.renderer = FEATHER.FeatherRenderer()
        rendering = RenderCapture(controller.renderer)
        controller.renderer.set_header_action("global.abort", "ABORT")
        FEATHER.FeatherScreen._render_cancel_confirm(controller)
        frame = rendering.latest
        self.assertFalse(frame.has_action("print.cancel.confirm"))
        self.assertFalse(frame.has_action("nav.back"))
        self.assertTrue(frame.has_action("operation.cancel.continue"))
        self.assertTrue(frame.has_action("operation.cancel.force"))
        self.assertTrue(frame.has_action("global.abort"))

    def test_force_abort_is_added_after_any_cancel_is_accepted(self):
        controller = base_controller("paused")
        controller.cancel_mode = "confirm"
        controller.operation_cancel_target_name = "Cold Pull"
        controller.renderer = FEATHER.FeatherRenderer()
        rendering = RenderCapture(controller.renderer)

        FEATHER.FeatherScreen._render_cancel_confirm(controller)
        self.assertFalse(
            rendering.latest.has_action("operation.cancel.force"))

        controller.cancel_mode = "pending"
        FEATHER.FeatherScreen._render_cancel_confirm(controller)
        self.assertTrue(
            rendering.latest.has_action("operation.cancel.continue"))
        self.assertTrue(
            rendering.latest.has_action("operation.cancel.force"))

    def test_interruptible_target_changes_the_confirmation_control(self):
        controller = base_controller("paused")
        controller.cancel_mode = "confirm"
        controller.operation_cancel_target_name = "Nozzle Cleaning"
        controller.operation_cancel_target_mode = "interruptible"
        controller.renderer = FEATHER.FeatherRenderer()
        rendering = RenderCapture(controller.renderer)

        FEATHER.FeatherScreen._render_cancel_confirm(controller)
        interrupt_label = rendering.latest.button(
            "operation.cancel.confirm").label
        controller.operation_cancel_target_mode = "cancelable"
        FEATHER.FeatherScreen._render_cancel_confirm(controller)

        self.assertNotEqual(
            interrupt_label,
            rendering.latest.button("operation.cancel.confirm").label)

    def test_continue_clears_pending_request_and_print_cancel_latch(self):
        controller = base_controller("printing")
        controller.start_print_macro.variables["print_started"] = False
        controller._show_page = lambda page: setattr(controller, "page", page)

        controller._handle_print_action("print.cancel")
        controller._handle_operation_cancel_action(
            "operation.cancel.confirm")
        request_id = controller.operation_cancel_request_id
        self.assertTrue(controller.cancel_requested)

        controller._handle_operation_cancel_action(
            "operation.cancel.continue")

        self.assertIsNotNone(request_id)
        self.assertFalse(
            controller.operation_context.status["cancel_pending"])
        self.assertFalse(controller.cancel_requested)
        self.assertIsNone(controller.cancel_mode)
        self.assertEqual(controller.page, FEATHER.ScreenPage.PRINTING)

    def test_cancel_progress_uses_wait_state_before_contextual_stage(self):
        controller = base_controller("printing")
        controller.operation_context.status.update(
            context_path=("Bed Mesh",), current_state="LEVELING",
            revision=1)

        self.assertEqual(controller._cancel_progress_label(),
                         "WILL STOP AFTER LEVELING")

        controller.temperature_wait.variables.update(active=True)
        controller.operation_context.status.update(
            context_path=("Calibration", "Cold Pull"),
            current_state="HEATING NOZZLE", revision=2)
        self.assertEqual(controller._cancel_progress_label(),
                         "INTERRUPTING HEATING NOZZLE...")

        controller.operation_context.status.update(
            context_path=("Bed Mesh", "Heating Bed"), current_state=None,
            revision=3)
        self.assertEqual(controller._cancel_progress_label(),
                         "INTERRUPTING HEATING BED...")

        controller.operation_context.status.update(
            context_path=(), current_state=None, revision=4)
        self.assertEqual(controller._cancel_progress_label(),
                         "INTERRUPTING TEMPERATURE WAIT...")

    def test_operation_context_status_is_semantic_and_formatted_only_for_ui(self):
        controller = base_controller("printing")
        controller.operation_context.status.update(
            context_path=("CALIBRATION", "NOZZLE CLEANING"),
            current_state="HEATING NOZZLE", revision=7)
        controller.renderer = FEATHER.FeatherRenderer()

        status = controller.get_status(10.0)

        self.assertEqual(
            status["context_path"],
            ("CALIBRATION", "NOZZLE CLEANING"))
        self.assertEqual(status["current_state"], "HEATING NOZZLE")
        self.assertEqual(status["operation_revision"], 7)
        self.assertEqual(
            controller._operation_context_text(10.0),
            "CALIBRATION -> NOZZLE CLEANING -> HEATING NOZZLE")

    def test_status_exposes_loaded_ui_test_without_loading_it(self):
        controller = base_controller()
        controller.renderer = FEATHER.FeatherRenderer()
        feature = type("Feature", (), {
            "get_status": lambda self: {
                "running": True, "suite": "UI", "phase": "render",
                "step": "ui-home", "step_index": 4, "step_count": 10,
            },
        })()
        controller.feature_manager = type("Features", (), {
            "peek": lambda self, name: feature if name == "ui_test" else None,
        })()

        status = controller.get_status(10.0)

        self.assertEqual(status["ui_test"]["step"], "ui-home")

    def test_mesh_recalibration_breadcrumb_fits_print_status(self):
        from feather_ui_test.context_fixtures import VISUAL_CONTEXTS
        from feather_ui_test.scenarios import ScenarioCatalog

        specifications = [item for item in VISUAL_CONTEXTS if item.get('label_type', '').startswith('mesh_recalibration')]
        for specification in specifications:
            for state in specification['states']:
                with self.subTest(path=specification['path'], state=state):
                    controller = base_controller('printing')
                    controller.renderer = FEATHER.FeatherRenderer()
                    capture = RenderCapture(controller.renderer)
                    operation = ScenarioCatalog._operation_context_snapshot(specification, state)
                    label = controller._display_status_text(status=operation)
                    controller._draw_print_status(label)
                    text = capture.latest.text(label)
                    height = get_font_metrics().text_height(text.value, text.font, text.max_width, wrap=text.wrap)
                    self.assertLessEqual(height, text.max_height)

    def test_operation_revision_redraws_print_status_once(self):
        controller = base_controller("printing")
        controller.page = FEATHER.ScreenPage.PRINTING
        controller.operation_context.status.update(
            context_types=("print",), context_path=("PRINT PREP",),
            current_state="HOMING",
            revision=3)
        drawn = []
        controller._draw_print_status = drawn.append

        controller._update_operation_context(10.0)
        controller._update_operation_context(11.0)
        controller.operation_context.status.update(
            current_state="HEATING BED", revision=4)
        controller._update_operation_context(12.0)

        self.assertEqual(drawn, [
            "PRINT PREP -> HOMING", "PRINT PREP -> HEATING BED"])

    def test_print_state_transition_selects_correct_page(self):
        controller = base_controller("idle")
        controller._progress_floor = 0.75
        controller._m73_active = True
        pages = []
        controller._show_page = pages.append
        controller._change_print_state(FEATHER.PrintState.PRINTING, "printing")
        controller._change_print_state(FEATHER.PrintState.PAUSED, "paused")
        self.assertEqual(pages, [FEATHER.ScreenPage.PRINTING, FEATHER.ScreenPage.PAUSED])
        self.assertEqual(controller._progress_floor, 0.0)
        self.assertFalse(controller._m73_active)

    def test_new_print_closes_previous_print_result_dialog(self):
        cases = (
            ("complete", FEATHER.PrintState.PREPARING, FEATHER.ScreenPage.PRINTING),
            ("cancelled", FEATHER.PrintState.PRINTING, FEATHER.ScreenPage.PRINTING),
            ("error", FEATHER.PrintState.PAUSED, FEATHER.ScreenPage.PAUSED),
            ("standby", FEATHER.PrintState.PRINTING, FEATHER.ScreenPage.PRINTING),
        )
        for result, new_state, expected_page in cases:
            with self.subTest(result=result, new_state=new_state):
                controller = base_controller("printing")
                rendering = composed_controller_surface(
                    controller, lambda: controller.renderer.send(
                        controller.renderer.begin_page(controller.page.name)
                        + [controller.renderer.text(40, 100, controller.page.name)]))

                controller._change_print_state(FEATHER.PrintState.IDLE, result)
                self.assertEqual(controller._current_dialog(),
                                 FEATHER.ScreenDialog.MESSAGE)
                self.assertTrue(rendering.latest.has_action("message.ok"))

                controller._change_print_state(new_state, "printing")

                self.assertEqual(controller.page, expected_page)
                self.assertIsNone(controller._current_dialog())
                self.assertTrue(rendering.latest.has_text(expected_page.name))
                self.assertFalse(rendering.latest.has_action("message.ok"))

    def test_new_print_keeps_unrelated_message_dialog(self):
        controller = base_controller()
        rendering = composed_controller_surface(
            controller, lambda: controller.renderer.send(
                controller.renderer.begin_page(controller.page.name)))
        controller._show_message("", controller.page, title="Other notice")

        controller._change_print_state(FEATHER.PrintState.PRINTING, "printing")

        self.assertEqual(controller._current_dialog(),
                         FEATHER.ScreenDialog.MESSAGE)
        self.assertTrue(rendering.latest.has_text("OTHER NOTICE"))
        self.assertTrue(rendering.latest.has_action("message.ok"))

    def test_dashboard_remains_visible_after_explicit_print_home(self):
        controller = base_controller("printing")
        controller.page = FEATHER.ScreenPage.IDLE_HOME
        controller.home_during_print = True
        pages = []
        controller._show_page = pages.append

        controller._change_print_state(FEATHER.PrintState.PAUSED, "paused")
        controller._change_print_state(FEATHER.PrintState.PRINTING, "printing")

        self.assertEqual(pages, [])

    def test_dashboard_uses_only_the_active_print_context_for_detail(self):
        controller = base_controller("printing")
        controller.last_job_name = "part.gcode"
        controller._print_progress = lambda eventtime, stats: 0.25
        controller._print_time_values = (
            lambda eventtime, stats, progress: (60.0, 180.0))
        controller.operation_context.status.update(
            context_types=("pid_bed",), context_path=("Bed PID",),
            current_state="COMPLETE", revision=2)

        ordinary = controller._dashboard_job(1.0)

        self.assertEqual(ordinary.detail, "PRINTING")

        controller.operation_context.status.update(
            context_types=("print",), context_path=("Print",),
            current_state="HEATING BED", revision=3)

        managed = controller._dashboard_job(2.0)

        self.assertEqual(managed.detail, "PRINT -> HEATING BED")

    def test_filament_back_uses_live_terminal_state(self):
        controller = base_controller("paused")
        controller.page = FEATHER.ScreenPage.FILAMENT_MATERIAL
        controller.filament_from_pause = True
        controller.print_stats.status["state"] = "cancelled"
        pages = []
        controller._show_page = pages.append

        controller._go_back()

        self.assertEqual(pages, [FEATHER.ScreenPage.IDLE_HOME])

    def test_print_state_change_does_not_drop_accepted_cancel(self):
        controller = base_controller("printing")
        controller.pending_action = "print.cancel.confirm"
        controller.cancel_requested = True
        controller.page = FEATHER.ScreenPage.OPERATION_CANCEL
        controller._show_page = lambda page: None
        controller._change_print_state(FEATHER.PrintState.PAUSED, "paused")
        self.assertEqual(controller.pending_action, "print.cancel.confirm")
        self.assertTrue(controller.cancel_requested)

    def test_cancel_context_end_waits_for_observed_print_stop(self):
        controller = base_controller("paused")
        controller.page = FEATHER.ScreenPage.OPERATION_CANCEL
        controller.pending_action = "print.cancel.confirm"
        controller.cancel_mode = "pending"
        controller.cancel_requested = True
        controller._show_page = lambda page: setattr(controller, "page", page)

        controller.pending_until = 200
        controller._last_cancel_label = None
        controller._handle_operation_end(1, "cancelled")
        controller._reconcile_pending_action(90.0, "paused", False)

        self.assertEqual(controller.pending_action, "print.cancel.confirm")
        self.assertEqual(controller.page, FEATHER.ScreenPage.OPERATION_CANCEL)
        self.assertIsNone(controller._current_dialog())
        controller.print_stats.status["state"] = "cancelled"
        controller.virtual_sdcard.active = True
        controller._reconcile_pending_action(90.0, "cancelled", True)
        self.assertEqual(controller.pending_action, "print.cancel.confirm")
        controller.virtual_sdcard.active = False
        controller._reconcile_pending_action(90.0, "cancelled", False)

        self.assertIsNone(controller.pending_action)
        self.assertIsNone(controller.cancel_mode)
        self.assertFalse(controller.cancel_requested)
        self.assertEqual(controller.page, FEATHER.ScreenPage.IDLE_HOME)
        self.assertEqual(controller._current_dialog(),
                         FEATHER.ScreenDialog.MESSAGE)
        self.assertEqual(controller._dialog_content(
            FEATHER.ScreenDialog.MESSAGE)["title"], "Print cancelled")

        controller._change_print_state(FEATHER.PrintState.PRINTING, "printing")
        self.assertIsNone(controller._current_dialog())

    def test_failed_print_cleanup_never_reports_success_for_an_active_print(self):
        controller = base_controller("printing")
        controller.page = FEATHER.ScreenPage.OPERATION_CANCEL
        controller.pending_action = "print.cancel.confirm"
        controller.cancel_mode = "pending"
        controller.cancel_requested = True
        controller.operation_context.status["cancel_error"] = "CANCEL_PRINT: heater cleanup failed"
        controller._show_page = lambda page: setattr(controller, "page", page)

        controller._handle_operation_end(1, "cancel_failed")

        self.assertIsNone(controller.pending_action)
        self.assertEqual(controller.print_stats.status["state"], "printing")
        message = controller._dialog_content(FEATHER.ScreenDialog.MESSAGE)
        self.assertEqual(message["title"], "Print cancellation failed")
        self.assertIn("heater cleanup failed", message["message"])

    def test_cancel_dispatch_failure_reaches_the_screen_and_keeps_abort_available(self):
        from tests.test_operation_context import CONTEXT, FakePrinter, FakeConfig, FakeCommand

        controller = base_controller("printing")
        controller.page = FEATHER.ScreenPage.PRINTING
        printer = FakePrinter()
        manager = CONTEXT.OperationContextManager(FakeConfig(printer))
        manager.register_context_type(FakeConfig(printer, "operation_context_type print", {
            "name": "Print", "cancel_mode": "cancelable", "on_cancel": "CANCEL_PRINT"}))
        manager.cmd_CONTEXT_BEGIN(FakeCommand(TYPE="print"))
        controller.operation_context = manager
        printer.register_event_handler("operation_context:end", controller._handle_operation_end)
        def fail_cancel(script):
            self.assertEqual(script, "CANCEL_PRINT")
            raise RuntimeError("CANCEL_PRINT failed before stopping SD")
        printer.gcode.script_hook = fail_cancel
        controller._run_script = lambda command: manager.cmd_CONTEXT_CANCEL_POINT(FakeCommand())
        rendering = composed_controller_surface(controller, lambda:
            controller.renderer.send(controller.renderer.begin_page("Printing")))
        controller._show_page = FEATHER.FeatherScreen._show_page.__get__(controller)

        controller._handle_print_action("print.cancel")
        controller._handle_operation_cancel_action("operation.cancel.confirm")
        controller._reconcile_print_action()

        self.assertEqual(controller.print_stats.status["state"], "printing")
        self.assertTrue(controller.virtual_sdcard.is_active())
        self.assertIsNone(controller.pending_action)
        message = controller._dialog_content(FEATHER.ScreenDialog.MESSAGE)
        self.assertEqual(message["title"], "Print cancellation failed")
        self.assertIn("failed before stopping SD", message["message"])
        self.assertTrue(rendering.latest.has_action("global.abort"))
        self.assertIn("CANCEL_PRINT failed", controller.get_status(100)["cancel_error"])

    def test_cleanup_failure_is_retained_after_observing_a_stopped_print(self):
        controller = base_controller("paused")
        controller.page = FEATHER.ScreenPage.OPERATION_CANCEL
        controller.pending_action = "print.cancel.confirm"
        controller.cancel_requested = True
        controller.print_stats.status["state"] = "cancelled"
        controller.virtual_sdcard.active = False
        controller.operation_context.status["cancel_error"] = "cleanup failed after SD stopped"
        controller._show_page = lambda page: setattr(controller, "page", page)

        controller._handle_operation_end(1, "cancel_failed")
        controller._reconcile_print_action()

        message = controller._dialog_content(FEATHER.ScreenDialog.MESSAGE)
        self.assertEqual(message["title"], "Print stopped; cleanup failed")
        self.assertIn("cleanup failed", message["message"])
        self.assertEqual(controller.print_state, FEATHER.PrintState.IDLE)

    def test_cancel_waits_for_inactive_sd_even_when_stats_report_cancelled(self):
        controller = base_controller("printing")
        controller.pending_action = "print.cancel.confirm"
        controller.pending_until = 200
        controller.cancel_requested = True
        controller.print_stats.status["state"] = "cancelled"
        controller._reconcile_print_action()
        self.assertEqual(controller.print_state, FEATHER.PrintState.PRINTING)
        self.assertEqual(controller.pending_action, "print.cancel.confirm")
        self.assertIsNone(controller._current_dialog())

    def test_feather_abort_requests_nearest_context_domain(self):
        controller = base_controller("printing")
        responses = []
        gcmd = type("GCmd", (), {
            "error": RuntimeError,
            "respond_raw": responses.append,
        })()

        controller.cmd_FEATHER_ABORT(gcmd)

        self.assertTrue(
            controller.operation_context.status["cancel_pending"])
        self.assertEqual(
            responses, ["Feather cancellation requested: Print"])

    def test_feather_abort_interrupts_an_active_managed_wait(self):
        controller = base_controller("printing")
        controller.temperature_wait.variables["active"] = True
        immediate = []
        controller._run_immediate_command = immediate.append
        gcmd = type("GCmd", (), {
            "error": RuntimeError,
            "respond_raw": lambda self, message: None,
        })()

        controller.cmd_FEATHER_ABORT(gcmd)

        self.assertEqual(immediate, ["M108"])


class MotionHeatSettingsTest(unittest.TestCase):
    def test_manual_control_page_navigation_cancels_delayed_tasks(self):
        cases = (
            ("nav.move", FEATHER.ScreenPage.CONTROL_HOME,
             FEATHER.ScreenPage.CONTROL_MOVE),
            ("nav.move", FEATHER.ScreenPage.IDLE_HOME,
             FEATHER.ScreenPage.CONTROL_MOVE),
            ("nav.heat", FEATHER.ScreenPage.IDLE_HOME,
             FEATHER.ScreenPage.CONTROL_HEAT),
            ("nav.heat", FEATHER.ScreenPage.CONTROL_HOME,
             FEATHER.ScreenPage.CONTROL_HEAT),
        )
        for action, source_page, target_page in cases:
            with self.subTest(action=action):
                controller = base_controller()
                controller.page = source_page
                controller.last_action_time = -1.0
                pages = []
                controller._show_page = pages.append

                controller._dispatch_action(action)

                self.assertEqual(
                    controller.gcode.commands, ["_CANCEL_DELAYED_COMMANDS"])
                self.assertEqual(pages, [target_page])

    def test_manual_control_navigation_bypasses_busy_gcode_queue(self):
        class BusyGCode:
            def __init__(self):
                self.commands = []

            def run_script(self, command):
                raise AssertionError("navigation entered the busy G-code queue")

            def run_script_from_command(self, command):
                self.commands.append(command)

        controller = base_controller()
        controller.gcode = BusyGCode()
        controller.page = FEATHER.ScreenPage.CONTROL_HOME
        controller.last_action_time = -1.0
        pages = []
        controller._show_page = pages.append

        controller._dispatch_action("nav.heat")

        self.assertEqual(
            controller.gcode.commands, ["_CANCEL_DELAYED_COMMANDS"])
        self.assertEqual(pages, [FEATHER.ScreenPage.CONTROL_HEAT])

    def test_manual_control_navigation_is_rejected_locally_during_print(self):
        cases = (
            ("nav.move", FEATHER.ScreenPage.IDLE_HOME),
            ("nav.heat", FEATHER.ScreenPage.IDLE_HOME),
            ("nav.filament", FEATHER.ScreenPage.IDLE_HOME),
            ("home.last_job", FEATHER.ScreenPage.IDLE_HOME),
            ("nav.files", FEATHER.ScreenPage.MAIN_MENU),
            ("nav.control", FEATHER.ScreenPage.MAIN_MENU),
            ("nav.calibration", FEATHER.ScreenPage.CONTROL_HOME),
            ("nav.settings", FEATHER.ScreenPage.CONTROL_HOME),
        )
        states = (
            (FEATHER.PrintState.PREPARING, "printing"),
            (FEATHER.PrintState.PRINTING, "printing"),
            (FEATHER.PrintState.PAUSED, "paused"),
        )
        for print_state, stats_state in states:
            for action, source_page in cases:
                with self.subTest(state=print_state.name, action=action):
                    controller = base_controller(stats_state)
                    controller.print_state = print_state
                    controller.page = source_page
                    controller.last_action_time = -1.0
                    pages = []
                    notices = []
                    controller._show_page = pages.append
                    controller._toast = notices.append

                    controller._dispatch_action(action)

                    self.assertEqual(pages, [])
                    self.assertEqual(controller.gcode.commands, [])
                    self.assertEqual(notices, ["UNAVAILABLE DURING PRINT"])

    def test_idle_requirement_includes_controller_preparing_state(self):
        controller = base_controller("idle")
        controller.print_state = FEATHER.PrintState.PREPARING

        with self.assertRaisesRegex(RuntimeError, "only while idle"):
            controller._require_idle()

    def test_dashboard_material_opens_filament_and_returns_home(self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.IDLE_HOME
        controller.last_action_time = -1.0
        pages = []
        controller._show_page = pages.append
        controller._require_idle = lambda: None
        controller.extruder = StatusObject({"target": 0.0})

        controller._dispatch_action("nav.filament")

        self.assertEqual(pages, [FEATHER.ScreenPage.FILAMENT_MATERIAL])
        self.assertEqual(controller.filament_return_page, FEATHER.ScreenPage.IDLE_HOME)

        controller.page = FEATHER.ScreenPage.FILAMENT_MATERIAL
        controller.filament_from_pause = False
        controller._go_back()
        self.assertEqual(pages[-1], FEATHER.ScreenPage.IDLE_HOME)

    def test_dashboard_move_returns_home(self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.IDLE_HOME
        controller.last_action_time = -1.0
        pages = []
        controller._show_page = pages.append

        controller._dispatch_action("nav.move")

        self.assertEqual(pages, [FEATHER.ScreenPage.CONTROL_MOVE])
        self.assertEqual(controller.move_return_page, FEATHER.ScreenPage.IDLE_HOME)
        controller.page = FEATHER.ScreenPage.CONTROL_MOVE
        controller._go_back()
        self.assertEqual(pages[-1], FEATHER.ScreenPage.IDLE_HOME)

    def test_calibration_start_cancels_delayed_tasks_again(self):
        controller = base_controller()
        controller.calibration_kind = "screws"
        controller.calibration_repeat_probe = False
        pages = []
        controller._show_page = pages.append

        controller._start_calibration()

        self.assertEqual(
            controller.gcode.commands, ["_CANCEL_DELAYED_COMMANDS"])
        self.assertEqual(pages, [FEATHER.ScreenPage.CALIBRATION_PROGRESS])

    def test_continuous_touch_updates_planner_and_release(self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.CONTROL_MOVE
        controller.move_mode = "joystick"
        controller.command_depth = 0
        controller.dimmed = False
        controller.last_touch_time = 0.0
        controller.joystick_suppressed = None
        controller.joystick_action = None
        controller.joystick_timer = object()
        controller.joystick_timer_active = False
        updates = []
        controller.reactor.NOW = 0.0
        controller.reactor.update_timer = (
            lambda timer, when: updates.append((timer, when)))
        controller.renderer = type("Renderer", (), {
            "decode_action": lambda self, action: action.split(":", 1)[-1],
        })()
        controller.toolhead = StatusObject({"homed_axes": "xyz"})

        class Planner:
            def __init__(self):
                self.events = []

            def set_xy(self, *args):
                self.events.append(("xy", args[0], args[1]))

            def set_z(self, *args):
                self.events.append(("z", args[0]))

            def release(self):
                self.events.append(("release",))

        controller.joystick = Planner()
        controller._handle_continuous_touch(
            "touch 7:%s begin 365 220" % MOVE_UI.JOYSTICK_XY.wire_id)
        controller._handle_continuous_touch(
            "touch 7:%s move 400 180" % MOVE_UI.JOYSTICK_XY.wire_id)
        controller._handle_continuous_touch(
            "touch 7:%s end 400 180" % MOVE_UI.JOYSTICK_XY.wire_id)

        self.assertEqual(controller.joystick.events,
                         [("xy", 365, 220), ("xy", 400, 180), ("release",)])
        self.assertIsNone(controller.joystick_action)
        # Raw touch updates only replace the latest vector. The fixed-rate
        # motion loop is started once instead of being forced to NOW for every
        # coordinate report.
        self.assertEqual(len(updates), 1)
        self.assertTrue(controller.joystick_timer_active)

    def test_low_z_warning_blocks_new_xy_touch_without_interrupting_z(self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.CONTROL_MOVE
        controller.move_mode = "joystick"
        controller.command_depth = 0
        controller.dimmed = False
        controller.last_touch_time = 0.0
        controller.joystick_suppressed = None
        controller.joystick_action = None
        controller.joystick_timer = object()
        controller.joystick_timer_active = False
        controller.move_caution_signature = (True, "active")
        controller.reactor.NOW = 0.0
        controller.reactor.update_timer = lambda timer, when: None
        controller.renderer = type("Renderer", (), {
            "decode_action": lambda self, action: action.split(":", 1)[-1],
        })()
        controller.toolhead = StatusObject({"homed_axes": "xyz"})

        class Planner:
            def __init__(self):
                self.events = []

            def set_xy(self, *args):
                self.events.append(("xy",))

            def set_z(self, *args):
                self.events.append(("z", args[0]))

            def release(self):
                self.events.append(("release",))

        controller.joystick = Planner()
        controller._update_joystick_feedback = lambda *args, **kwargs: None

        controller._handle_continuous_touch(
            "touch 9:%s begin 240 229" % MOVE_UI.JOYSTICK_XY.wire_id)
        controller._handle_continuous_touch(
            "touch 9:%s move 300 229" % MOVE_UI.JOYSTICK_XY.wire_id)
        controller._handle_continuous_touch(
            "touch 9:%s end 300 229" % MOVE_UI.JOYSTICK_XY.wire_id)
        controller._handle_continuous_touch(
            "touch 9:%s begin 510 180" % MOVE_UI.JOYSTICK_Z.wire_id)
        controller._handle_continuous_touch(
            "touch 9:%s move 510 160" % MOVE_UI.JOYSTICK_Z.wire_id)
        controller._handle_continuous_touch(
            "touch 9:%s end 510 160" % MOVE_UI.JOYSTICK_Z.wire_id)

        self.assertEqual(
            controller.joystick.events,
            [("z", 180), ("z", 160), ("release",)])
        self.assertIsNone(controller.joystick_action)
        self.assertIsNone(controller.joystick_suppressed)

    def test_dimmed_joystick_gesture_only_wakes_until_release(self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.CONTROL_MOVE
        controller.move_mode = "joystick"
        controller.command_depth = 0
        controller.dimmed = True
        controller.last_touch_time = 0.0
        controller.joystick_suppressed = None
        controller.joystick_action = None
        controller.renderer = type("Renderer", (), {
            "decode_action": lambda self, action: action.split(":", 1)[-1],
        })()
        controller._wake_if_dimmed = lambda: True
        controller.joystick = mock.Mock()

        controller._handle_continuous_touch(
            "touch 8:%s begin 540 100" % MOVE_UI.JOYSTICK_Z.wire_id)
        controller._handle_continuous_touch(
            "touch 8:%s move 540 90" % MOVE_UI.JOYSTICK_Z.wire_id)
        controller._handle_continuous_touch(
            "touch 8:%s end 540 90" % MOVE_UI.JOYSTICK_Z.wire_id)

        controller.joystick.assert_not_called()
        self.assertIsNone(controller.joystick_suppressed)

    def test_joystick_tick_queues_direct_motion_and_restores_toolhead_accel(self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.CONTROL_MOVE
        controller.move_mode = "joystick"
        controller.print_state = FEATHER.PrintState.IDLE
        controller.joystick_action = "move.joy.xy"
        controller.joystick_queued = False
        controller.joystick_timer = object()
        controller.reactor.NEVER = 1.0e30

        class Toolhead:
            def __init__(self):
                self.max_accel = 20000.0
                self.requested_accel_to_decel = 5000.0
                self.max_accel_to_decel = 5000.0
                self.buffer_time_start = 0.250
                self.buffer_time_low = 1.000
                self.move_flush_time = 0.050
                self.low_latency_saved_move_flush_time = None
                self.position = [0.0, 0.0, 100.0, 0.0]
                self.moves = []
                self.flushes = 0
                self.move_queue = type("MoveQueue", (), {
                    "queue": [],
                    "set_flush_time": lambda queue, duration: setattr(
                        queue, "junction_flush", duration),
                })()

            def _calc_junction_deviation(self):
                self.max_accel_to_decel = min(
                    self.requested_accel_to_decel, self.max_accel)

            def get_status(self, eventtime):
                return {"homed_axes": "xyz"}

            def check_busy(self, eventtime):
                pending = sum(getattr(move, "min_move_t", 0.0)
                              for move in self.move_queue.queue)
                return 0.0, -pending, not self.move_queue.queue

            def get_position(self):
                return list(self.position)

            def manual_move(self, position, speed):
                self.moves.append((list(position), speed, self.max_accel))
                self.position[:3] = position
                self.move_queue.queue.append(
                    type("Move", (), {
                        "min_move_t": FEATHER.joystick_ui.PERIOD})())

            def flush_step_generation(self):
                self.flushes += 1
                self.move_queue.queue[:] = []

        controller.toolhead = Toolhead()
        controller.joystick = FEATHER.joystick_ui.JoystickPlanner(
            600.0, 10000.0, 25.0, 250.0,
            ((-110.0, 110.0), (-110.0, 110.0), (0.0, 220.0)))
        controller.joystick.set_xy(365, 220, 100.0, 240, 220, 125)

        next_wake = controller._joystick_tick(100.0)

        self.assertAlmostEqual(
            next_wake, 100.0 + FEATHER.joystick_ui.PERIOD)
        self.assertGreaterEqual(len(controller.toolhead.moves), 2)
        self.assertLessEqual(len(controller.toolhead.moves),
                             FEATHER.joystick_motion.MAX_REFILL_SEGMENTS)
        self.assertGreater(controller.toolhead.moves[0][2], 0.0)
        self.assertEqual(controller.toolhead.moves[0][2],
                         controller.joystick.xy_accel)
        self.assertEqual(controller.toolhead.max_accel, 20000.0)
        self.assertTrue(controller.joystick_queued)

    def test_joystick_start_waits_for_short_toolhead_tail(self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.CONTROL_MOVE
        controller.move_mode = "joystick"
        controller.joystick_action = "move.joy.xy"
        controller.joystick_timer_active = True
        controller.joystick_busy_since = None
        controller.reactor.NEVER = 1.0e30
        controller.toolhead = StatusObject({"homed_axes": "xyz"})
        controller._update_joystick_feedback = lambda *args, **kwargs: None
        notices = []
        controller._toast = notices.append

        class Planner:
            held = True

            def __init__(self):
                self.released = False

            def watchdog(self, eventtime):
                return False

            def is_moving(self):
                return not self.released

            def release(self):
                self.held = False
                self.released = True

        class BusyStream:
            active = False

            def start(self, eventtime):
                raise FEATHER.joystick_motion.StreamBusy()

        controller.joystick = Planner()
        controller.joystick_stream = BusyStream()

        retry = controller._joystick_tick(100.0)

        self.assertAlmostEqual(
            retry, 100.0 + FEATHER.joystick_ui.QUEUE_RETRY)
        self.assertFalse(controller.joystick.released)
        self.assertEqual(controller.joystick_action, "move.joy.xy")
        self.assertEqual(notices, [])

        stopped = controller._joystick_tick(
            100.0 + FEATHER.joystick_motion.START_BUSY_GRACE + 0.001)

        self.assertEqual(stopped, controller.reactor.NEVER)
        self.assertTrue(controller.joystick.released)
        self.assertIsNone(controller.joystick_action)
        self.assertEqual(len(notices), 1)

    def test_joystick_uses_feather_limits_and_actual_z_limits(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.reactor = Reactor()

        class Kinematics:
            max_z_velocity = 25.0
            max_z_accel = 500.0

        class Toolhead:
            def get_status(self, eventtime):
                return {
                    "axis_minimum": (-120.0, -120.0, 5.0),
                    "axis_maximum": (120.0, 120.0, 230.0),
                    "max_velocity": 600.0,
                    "max_accel": 20000.0,
                }

            def get_kinematics(self):
                return Kinematics()

        controller.toolhead = Toolhead()
        controller.joystick_limits = (
            (-97.0, 103.0), (-89.0, 91.0), (0.0, 220.0))
        controller._create_joystick_planner()

        self.assertEqual(controller.joystick.xy_speed, 300.0)
        self.assertEqual(controller.joystick.z_speed, 12.5)
        self.assertEqual(controller.joystick.xy_accel, 10000.0)
        self.assertEqual(controller.joystick.z_accel, 250.0)
        self.assertEqual(
            controller.joystick.limits,
            ((-97.0, 103.0), (-89.0, 91.0), (5.0, 220.0)))

    def test_heat_page_draws_values_immediately_and_refreshes_fan(self):
        controller = base_controller()
        controller.renderer = FEATHER.FeatherRenderer()
        rendering = RenderCapture(controller.renderer)
        controller.extruder = StatusObject({"temperature": 21.5, "target": 220})
        controller.heater_bed = StatusObject({"temperature": 24.0, "target": 60})
        controller.fan = StatusObject({"speed": 0.25})

        controller._render_heat()

        actions = tuple(
            action for action in HEAT_UI.get_page(
                controller.heating_materials).actions.values()
            if action.key == HEAT_UI.HeatCommand.PREHEAT)
        self.assertEqual(
            set(action.payload for action in actions),
            set(controller.heating_materials))
        controller.fan.status["speed"] = 0.5
        controller._update_heat_status(101)
        self.assertGreater(len(rendering.frames), 1)

    def test_empty_heating_keeps_manual_heat_controls_without_preset_hitboxes(self):
        controller = base_controller()
        controller.heating_materials = ()
        controller.heating_profiles = {}
        controller.renderer = FEATHER.FeatherRenderer()
        RenderCapture(controller.renderer)
        controller.extruder = StatusObject({"temperature": 21.5, "target": 0})
        controller.heater_bed = StatusObject({"temperature": 24.0, "target": 0})
        controller.fan = StatusObject({"speed": 0.0})

        controller._render_heat()

        actions = tuple(
            action.key for action in HEAT_UI.get_page(()).actions.values())
        self.assertIn(HEAT_UI.HeatCommand.NOZZLE_PLUS, actions)
        self.assertIn(HEAT_UI.HeatCommand.BED_PLUS, actions)
        self.assertIn(HEAT_UI.HeatCommand.COOLDOWN, actions)
        self.assertNotIn(HEAT_UI.HeatCommand.PREHEAT, actions)

    def test_move_offers_only_combined_homing_commands(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller.jog_step = 1.0
        controller._require_idle = lambda: None
        blocking = []
        controller._run_blocking_gcode = (
            lambda command, message: blocking.append((command, message)))
        controller._toast = lambda message: None

        controller._handle_move_command(MOVE_UI.HOME_ALL)
        controller._handle_move_command(MOVE_UI.HOME_XY)

        self.assertEqual([command for command, _message in blocking],
                         ["G28", "G28 X Y"])

    def test_step_adjustment_uses_magnitude_bands_in_both_directions(self):
        controller = ScenarioController.__new__(ScenarioController)
        controller._render_move = lambda: None
        plus = Increment(MOVE_UI.MoveState.JOG_STEP, 1)
        minus = Increment(MOVE_UI.MoveState.JOG_STEP, -1)

        expected = (
            (0.1, plus, 0.2),
            (0.9, plus, 1.0),
            (1.0, plus, 2.0),
            (9.0, plus, 10.0),
            (10.0, plus, 20.0),
            (20.0, minus, 10.0),
            (10.0, minus, 9.0),
            (2.0, minus, 1.0),
            (1.0, minus, 0.9),
            (0.1, minus, 0.1),
            (100.0, plus, 100.0),
        )
        for current, action, result in expected:
            with self.subTest(current=current, amount=action.amount):
                controller.jog_step = current
                controller._dispatch_semantic_ui_action(action)
                self.assertEqual(controller.jog_step, result)

    def test_step_presets_follow_the_active_magnitude_band(self):
        selected = MOVE_STEP_PAGE._step_preset_selected

        self.assertEqual(selected(0.9, 0.1), "selected")
        self.assertEqual(selected(2.0, 1.0), "selected")
        self.assertEqual(selected(20.0, 10.0), "selected")
        self.assertEqual(selected(2.0, 0.1), "enabled")

    def test_move_requires_homed_axis_and_uses_conservative_speed(self):
        controller = base_controller()
        controller.jog_step = 10.0
        controller.toolhead = StatusObject({
            "homed_axes": "y", "position": (0.0, 0.0, 0.0)})
        controller.gcode_move = StatusObject({
            "gcode_position": (0.0, 0.0, 0.0)})
        with self.assertRaisesRegex(RuntimeError, "Home X"):
            controller._handle_move_command(MOVE_UI.X_PLUS)
        controller.toolhead.status["homed_axes"] = "xyz"
        controller._handle_move_command(MOVE_UI.X_MINUS)
        controller._handle_move_command(MOVE_UI.Z_PLUS)
        self.assertEqual(controller.gcode.commands,
                         ["MOVE_SAFE X=-10 ABSOLUTE=1 F=6000",
                          "MOVE_SAFE Z=10 ABSOLUTE=1 F=600"])

    def test_step_controls_share_joystick_limits(self):
        controller = base_controller()
        controller.jog_step = 10.0
        controller.joystick_limits = (
            (-100.0, 100.0), (-90.0, 90.0), (0.0, 210.0))
        controller.toolhead = StatusObject({
            "homed_axes": "xyz",
            "position": (95.0, -85.0, 205.0),
            "axis_minimum": (-120.0, -120.0, 0.0),
            "axis_maximum": (120.0, 120.0, 220.0),
        })
        controller.gcode_move = StatusObject({
            "gcode_position": (95.0, -85.0, 205.0)})

        controller._handle_move_command(MOVE_UI.X_PLUS)
        controller._handle_move_command(MOVE_UI.Y_MINUS)
        controller._handle_move_command(MOVE_UI.Z_PLUS)

        self.assertEqual(
            controller.gcode.commands,
            ["MOVE_SAFE X=100 ABSOLUTE=1 F=6000",
             "MOVE_SAFE Y=-90 ABSOLUTE=1 F=6000",
             "MOVE_SAFE Z=210 ABSOLUTE=1 F=600"])

    def test_step_z_respects_toolhead_range_and_does_not_move_at_limit(self):
        controller = base_controller()
        controller.jog_step = 10.0
        controller.joystick_limits = (
            (-100.0, 100.0), (-90.0, 90.0), (0.0, 220.0))
        controller.toolhead = StatusObject({
            "homed_axes": "xyz",
            "position": (0.0, 0.0, 205.0),
            "axis_minimum": (-120.0, -120.0, 5.0),
            "axis_maximum": (120.0, 120.0, 220.0),
        })
        controller.gcode_move = StatusObject({
            "gcode_position": (0.0, 0.0, 205.0)})
        notices = []
        controller._toast = notices.append

        controller._handle_move_command(MOVE_UI.Z_PLUS)
        controller.gcode_move.status["gcode_position"] = (0.0, 0.0, 210.0)
        controller._handle_move_command(MOVE_UI.Z_PLUS)

        self.assertEqual(controller.gcode.commands,
                         ["MOVE_SAFE Z=210 ABSOLUTE=1 F=600"])
        self.assertEqual(len(notices), 2)

    def test_step_does_not_reverse_when_current_position_is_outside_limit(self):
        controller = base_controller()
        controller.jog_step = 10.0
        controller.joystick_limits = (
            (-100.0, 100.0), (-90.0, 90.0), (0.0, 220.0))
        controller.toolhead = StatusObject({
            "homed_axes": "xyz",
            "position": (120.0, 0.0, 230.0),
            "axis_minimum": (-120.0, -120.0, 0.0),
            "axis_maximum": (120.0, 120.0, 230.0),
        })
        controller.gcode_move = StatusObject({
            "gcode_position": (120.0, 0.0, 230.0)})
        notices = []
        controller._toast = notices.append

        controller._handle_move_command(MOVE_UI.X_PLUS)
        controller._handle_move_command(MOVE_UI.X_MINUS)
        controller._handle_move_command(MOVE_UI.Z_PLUS)
        controller._handle_move_command(MOVE_UI.Z_MINUS)

        self.assertEqual(controller.gcode.commands,
                         ["MOVE_SAFE X=110 ABSOLUTE=1 F=6000",
                          "MOVE_SAFE Z=220 ABSOLUTE=1 F=600"])
        self.assertEqual(len(notices), 4)

    def test_step_jog_targets_gcode_coordinates_with_active_mesh(self):
        controller = base_controller()
        controller.jog_step = 1.0
        controller.toolhead = StatusObject({
            "homed_axes": "xyz",
            "position": (105.0, 105.0, 60.056863)})
        # A loaded bed mesh keeps the machine Z above the G-code Z; the jog
        # must dispatch the G-code target or every move lands off by the
        # mesh value at the current XY.
        controller.gcode_move = StatusObject({
            "gcode_position": (105.0, 105.0, 60.0)})

        controller._handle_move_command(MOVE_UI.Z_PLUS)

        self.assertEqual(controller.gcode.commands,
                         ["MOVE_SAFE Z=61 ABSOLUTE=1 F=600"])

    def test_low_z_warning_blocks_step_xy_but_keeps_step_z_available(self):
        controller = base_controller()
        controller.jog_step = 1.0
        controller.move_caution_signature = (True, "available")
        controller.toolhead = StatusObject({
            "homed_axes": "xyz", "position": (0.0, 0.0, 20.0)})
        controller.gcode_move = StatusObject({
            "gcode_position": (0.0, 0.0, 20.0)})

        controller._handle_move_command(MOVE_UI.X_PLUS)
        controller._handle_move_command(MOVE_UI.Y_PLUS)
        controller._handle_move_command(MOVE_UI.HOME_ALL)
        controller._handle_move_command(MOVE_UI.Z_MINUS)

        self.assertEqual(
            controller.gcode.commands, ["MOVE_SAFE Z=19 ABSOLUTE=1 F=600"])

    def test_preheat_fan_and_cooldown_commands(self):
        controller = base_controller()
        extruder = StatusObject({"temperature": 20, "target": 0})
        extruder.heater = type("Heater", (), {"min_temp": 0, "max_temp": 251})()
        controller.extruder = extruder
        controller.heater_bed = StatusObject({"temperature": 20, "target": 0})
        controller.heater_bed.min_temp = 0
        controller.heater_bed.max_temp = 91
        controller.fan = StatusObject({"speed": 0.0})

        controller._handle_heat_action("heat.preheat.ABS")
        controller._handle_heat_action("heat.fan50")
        controller._handle_heat_action("heat.alloff")
        self.assertEqual(controller.gcode.commands, [
            "PREHEAT_MATERIAL MATERIAL=ABS EXTRUDER_TEMP=250 BED_TEMP=85",
            "SET_FAN_SPEED FAN=fanM106 SPEED=0.50", "TURN_OFF_HEATERS"])

    def test_settings_clamp_values_toggle_sound_and_adjust_light(self):
        controller = base_controller()
        controller.params = type("Params", (), {
            "variables": {
                "backlight": 100, "backlight_eco": 1, "sound": 1,
                "chamber_light": 55}})()
        controller.chamber_light = StatusObject({
            "color_data": [(0.0, 0.0, 0.0, 0.0)]})
        controller._render_settings = lambda: None
        backlight = []
        controller._set_backlight = backlight.append
        controller._handle_settings_action("settings.brightness.plus")
        controller._handle_settings_action("settings.sound")
        controller._handle_settings_action("settings.led.minus")
        self.assertEqual(controller.gcode.commands, [
            "SET_MOD PARAM=backlight VALUE=100",
            "SET_MOD PARAM=sound VALUE=0",
            "SET_MOD PARAM=chamber_light VALUE=50"])
        self.assertEqual(backlight, [100])

    def test_settings_render_exposes_chamber_light_value_and_controls(self):
        controller = base_controller()
        controller.renderer = FEATHER.FeatherRenderer()
        controller.params = type("Params", (), {
            "variables": {
                "backlight": 50, "backlight_eco": 10, "sound": 1,
                "chamber_light": 42,
                "chamber_light_mode": "AT_BOOT"}})()
        controller.chamber_light = StatusObject({
            "color_data": [(0.0, 0.0, 0.0, 0.0)]})
        rendering = RenderCapture(controller.renderer)

        controller._render_settings()

        frame = rendering.latest
        self.assertTrue(frame.has_text(
            "%d%%" % controller.params.variables["chamber_light"]))
        self.assertTrue(frame.has_action("settings.led.minus"))
        self.assertTrue(frame.has_action("settings.led.plus"))

    def test_backlight_enable_is_separate_from_brightness(self):
        controller = base_controller()
        device = mock.mock_open()
        enable_error = PermissionError(FEATHER.errno.EPERM, "already enabled")
        with mock.patch("builtins.open", device), mock.patch.object(
                FEATHER.fcntl, "ioctl",
                side_effect=[enable_error, 0]) as ioctl:
            controller._enable_backlight()
            controller._set_backlight(65)
        self.assertEqual(controller.gcode.commands, [])
        self.assertEqual(ioctl.call_count, 2)
        self.assertEqual(ioctl.call_args_list[0].args[1],
                         FEATHER.DISP_LCD_BACKLIGHT_ENABLE)
        self.assertEqual(ioctl.call_args_list[1].args[1],
                         FEATHER.DISP_LCD_SET_BRIGHTNESS)

    def test_backlight_unexpected_failure_does_not_use_gcode(self):
        controller = base_controller()
        with self.assertLogs(level="ERROR") as logs, mock.patch(
                "builtins.open", side_effect=OSError("cannot open")):
            controller._set_backlight(45)
        self.assertEqual(controller.gcode.commands, [])
        self.assertIn("backlight update failed", "\n".join(logs.output))


class FilamentAndCalibrationWorkflowTest(unittest.TestCase):
    def test_filament_page_uses_complete_shared_material_selector(self):
        controller = base_controller()
        controller.renderer = FEATHER.FeatherRenderer()
        rendering = RenderCapture(controller.renderer)
        controller.extruder = type("Extruder", (), {
            "heater": type(
                "Heater", (), {"min_temp": 0, "max_temp": 300})()})()
        controller.heater_bed = type(
            "Bed", (), {"min_temp": 0, "max_temp": 130})()

        FilamentFeature(controller).render(FEATHER.ScreenPage.FILAMENT_MATERIAL)

        actions = tuple(
            action for action in FILAMENT_UI.get_material_page(
                tuple((material, controller._limited_preheat(material)[0])
                      for material in controller.heating_materials)).actions.values()
            if action.key == FILAMENT_UI.FilamentCommand.SELECT)
        self.assertEqual(
            set(action.payload for action in actions),
            set(controller.heating_materials))
        self.assertEqual(len(rendering.frames), 1)

    def test_empty_heating_filament_page_has_only_empty_state_and_back(self):
        controller = base_controller()
        controller.heating_materials = ()
        controller.heating_profiles = {}
        controller.renderer = FEATHER.FeatherRenderer()
        rendering = RenderCapture(controller.renderer)

        FilamentFeature(controller).render(FEATHER.ScreenPage.FILAMENT_MATERIAL)

        self.assertTrue(rendering.latest.has_action("nav.back"))
        self.assertFalse(FILAMENT_UI.get_material_page(()).actions)

    def test_filament_action_back_preserves_heat_and_returns_to_materials(self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.FILAMENT_ACTION
        controller.filament_from_pause = False
        controller.filament_material = "PETG"
        controller.extruder = StatusObject({
            "temperature": 130.4, "target": 250.0})
        pages = []
        controller._show_page = pages.append
        controller.feature_manager = FEATHER.LazyFeatureManager(
            controller, FEATHER.FEATURE_SPECS)
        controller.feature_manager.get("filament")

        controller._go_back()

        self.assertEqual(pages, [FEATHER.ScreenPage.FILAMENT_MATERIAL])
        self.assertEqual(controller.gcode.commands, [])
        self.assertEqual(controller.extruder.status["target"], 250.0)

    def test_filament_cooling_fan_is_bounded_and_survives_action_back(self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.FILAMENT_MATERIAL
        controller.filament_from_pause = False
        controller.filament_material = "PETG"
        controller.extruder = StatusObject({
            "temperature": 260.0, "target": 250.0})
        controller.extruder.min_extrude_temp = 170.0
        controller.fan = StatusObject({"speed": 0.0})
        commands = []
        pages = []
        controller._run_script = commands.append
        controller._show_page = pages.append
        feature = FilamentFeature(controller)
        feature._selected_target = 250.0

        feature.update(1.0)
        feature.update(2.0)
        feature.back(FEATHER.ScreenPage.FILAMENT_ACTION)

        self.assertEqual(commands, [
            "SET_FAN_SPEED FAN=fanM106 SPEED=1.00"])
        self.assertTrue(feature._cooling_fan_active)
        self.assertEqual(pages, [FEATHER.ScreenPage.FILAMENT_MATERIAL])

        feature.back(FEATHER.ScreenPage.FILAMENT_MATERIAL)

        self.assertEqual(commands[-1],
                         "SET_FAN_SPEED FAN=fanM106 SPEED=0.00")
        self.assertFalse(feature._cooling_fan_active)
        self.assertIsNone(feature._selected_target)

    def test_filament_update_preserves_loader_and_updates_cooling(
            self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.FILAMENT_ACTION
        controller.filament_from_pause = False
        controller.filament_material = "PETG"
        controller.extruder = StatusObject({
            "temperature": 260.0, "target": 250.0})
        controller.extruder.min_extrude_temp = 170.0
        controller.fan = StatusObject({"speed": 0.0})
        controller.busy_message = "PURGING..."
        commands = []
        controller._run_script = commands.append
        rendering = composed_controller_surface(controller, lambda:
            controller.renderer.loader(controller.busy_message, 0))
        controller._screen_root.paint()
        feature = FilamentFeature(controller)
        feature._selected_target = 250.0

        feature.update(1.0)

        self.assertEqual(commands, [
            "SET_FAN_SPEED FAN=fanM106 SPEED=1.00"])
        self.assertTrue(rendering.latest.has_text("PURGING..."))
        self.assertEqual(controller.renderer._buttons, {})
        self.assertIsNotNone(feature._last_signature)

    def test_filament_cooling_fan_stops_at_five_degree_threshold(self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.FILAMENT_MATERIAL
        controller.filament_material = "PETG"
        controller.extruder = StatusObject({
            "temperature": 255.1, "target": 250.0})
        controller.extruder.min_extrude_temp = 170.0
        controller.fan = StatusObject({"speed": 0.0})
        commands = []
        controller._run_script = commands.append
        feature = FilamentFeature(controller)
        feature._selected_target = 250.0

        feature.update(1.0)
        controller.extruder.status["temperature"] = 255.0
        feature.update(2.0)

        self.assertEqual(commands, [
            "SET_FAN_SPEED FAN=fanM106 SPEED=1.00",
            "SET_FAN_SPEED FAN=fanM106 SPEED=0.00",
        ])
        self.assertFalse(feature._cooling_fan_active)

    def test_material_selection_arms_cooling_before_action_updates(self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.FILAMENT_MATERIAL
        controller.filament_material = "PLA"
        controller.heating_materials = ("PETG",)
        controller.heating_profiles = {"PETG": (250, 80)}
        controller.extruder = StatusObject({
            "temperature": 260.0, "target": 0.0})
        controller.extruder.heater = type(
            "Heater", (), {"min_temp": 0, "max_temp": 300})()
        controller.heater_bed = type(
            "Bed", (), {"min_temp": 0, "max_temp": 130})()
        controller.fan = StatusObject({"speed": 0.0})
        commands = []
        handled = []
        controller._run_script = commands.append

        def select(action):
            handled.append(action)
            controller.filament_material = "PETG"
            controller.extruder.status["target"] = 250.0

        controller._handle_filament_action = select
        feature = FilamentFeature(controller)

        feature.handle_semantic_action(
            FEATHER.ScreenPage.FILAMENT_MATERIAL, select_filament("PETG"))

        self.assertEqual(handled, ["filament.PETG"])
        self.assertEqual(commands, [
            "SET_FAN_SPEED FAN=fanM106 SPEED=1.00"])
        self.assertEqual(feature._selected_target, 250.0)
        self.assertTrue(feature._cooling_fan_active)

    def test_safe_z_stage_positions_probes_adjusts_saves_then_continues(self):
        controller = base_controller()
        controller.params = type("Params", (), {
            "variables": {"safe_z": 8.0}})()
        controller.z_calibration = ZCalibrationSession()
        controller.z_calibration.begin(
            0.0, None, "", -0.25, False, safe_z=8.0)
        controller.probe = StatusObject({"last_z_result": -0.4})
        controller._render_safe_z = lambda: None
        moves = []
        pages = []
        callbacks = []
        controller.reactor.register_callback = callbacks.append
        controller._run_blocking_gcode = (
            lambda command, message: moves.append((command, message)))
        controller._show_page = pages.append

        controller._handle_z_offset_command(Z_OFFSET_UI.SAFE_CALIBRATE)
        controller._handle_z_offset_command(Z_OFFSET_UI.SAFE_PROBE)

        self.assertEqual(moves, [
            ("_HOME_IF_NEEDED\n"
             "MOVE_SAFE Z=16 ABSOLUTE=1 F=600\n"
             "MOVE_SAFE X=0 Y=0 ABSOLUTE=1 F=6000\n"
             "LOAD_CELL_TARE",
             "POSITIONING HEAD..."),
            ("PROBE SAMPLES=2", "PROBING..."),
        ])
        self.assertEqual(controller.gcode.commands, [
            "MOVE_SAFE Z=4.600000 ABSOLUTE=1 F=300"])
        self.assertAlmostEqual(controller.z_calibration.safe_z_candidate, 4.6)

        controller._handle_z_offset_command(Z_OFFSET_UI.SAFE_HIGHER)
        controller._handle_z_offset_command(Z_OFFSET_UI.SAFE_LOWER)
        controller._handle_z_offset_command(Z_OFFSET_UI.SAFE_SAVE)

        self.assertEqual(controller.gcode.commands[-3:], [
            "MOVE_SAFE Z=5.600000 ABSOLUTE=1 F=300",
            "MOVE_SAFE Z=4.600000 ABSOLUTE=1 F=300",
            "SET_MOD PARAM=safe_z VALUE=4.600",
        ])
        self.assertAlmostEqual(controller.z_calibration.safe_z, 4.6)
        self.assertEqual(pages, [FEATHER.ScreenPage.SAFE_Z_CALIBRATION,
                                 FEATHER.ScreenPage.CALIBRATION_PROGRESS])
        self.assertEqual(callbacks,
                         [controller._run_z_calibration_preparation])

    def test_safe_z_stage_can_be_skipped_without_changing_setting(self):
        controller = base_controller()
        controller.z_calibration = ZCalibrationSession()
        controller.z_calibration.begin(
            0.0, None, "", -0.25, False, safe_z=9.0)
        pages = []
        callbacks = []
        controller.reactor.register_callback = callbacks.append
        controller._show_page = pages.append

        controller._handle_z_offset_command(Z_OFFSET_UI.SAFE_SKIP)

        self.assertEqual(controller.z_calibration.safe_z, 9.0)
        self.assertEqual(controller.gcode.commands, [])
        self.assertEqual(pages, [FEATHER.ScreenPage.CALIBRATION_PROGRESS])
        self.assertEqual(callbacks,
                         [controller._run_z_calibration_preparation])

    def test_zone_selection_back_returns_to_saved_safe_z_without_repreparing(self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.Z_OFFSET_SUMMARY
        controller.z_calibration = ZCalibrationSession()
        controller.z_calibration.begin(
            0.0, None, "", -0.25, False, safe_z=6.0)
        controller.z_calibration.prepared = True
        controller.z_calibration.set_safe_z_trigger(1.0)
        controller.z_calibration.accept_safe_z()
        moves = []
        pages = []
        callbacks = []
        controller.reactor.register_callback = callbacks.append
        controller._run_blocking_gcode = (
            lambda command, message: moves.append((command, message)))
        controller._show_page = pages.append
        feature = ZCalibrationFeature(controller)
        feature.z_calibration = controller.z_calibration
        feature._render_safe_z = lambda: None

        feature.back(FEATHER.ScreenPage.Z_OFFSET_SUMMARY)

        self.assertEqual(moves, [(
            "_HOME_IF_NEEDED\nMOVE_SAFE Z=12 ABSOLUTE=1 F=600\n"
            "MOVE_SAFE X=0 Y=0 ABSOLUTE=1 F=6000\n"
            "LOAD_CELL_TARE\nMOVE_SAFE Z=6.000000 ABSOLUTE=1 F=300",
            "POSITIONING HEAD...")])
        self.assertEqual(pages, [FEATHER.ScreenPage.SAFE_Z_CALIBRATION])
        self.assertTrue(controller.z_calibration.safe_z_ready)

        feature._handle_z_offset_command(Z_OFFSET_UI.SAFE_HIGHER)
        feature._handle_z_offset_command(Z_OFFSET_UI.SAFE_SAVE)

        self.assertEqual(controller.gcode.commands[-2:], [
            "MOVE_SAFE Z=7.000000 ABSOLUTE=1 F=300",
            "SET_MOD PARAM=safe_z VALUE=7.000",
        ])
        self.assertEqual(pages[-1], FEATHER.ScreenPage.Z_OFFSET_SUMMARY)
        self.assertEqual(callbacks, [])

    def test_filament_page_cannot_replace_cancel_confirmation(self):
        controller = base_controller("paused")
        controller.page = FEATHER.ScreenPage.OPERATION_CANCEL
        pages = []
        controller._show_page = pages.append

        opened = controller._open_filament(True)

        self.assertFalse(opened)
        self.assertEqual(pages, [])

    def test_material_selection_heats_and_opens_action_page(self):
        controller = base_controller("paused")
        controller.filament_from_pause = True
        extruder = StatusObject({"temperature": 25, "target": 210})
        extruder.heater = type("Heater", (), {"min_temp": 0, "max_temp": 300})()
        controller.extruder = extruder
        controller.heater_bed = type("Bed", (), {"min_temp": 0, "max_temp": 130})()
        pages = []
        controller._show_page = pages.append
        controller._handle_filament_action("filament.PETG")
        self.assertEqual(controller.gcode.commands,
                         ["SET_MATERIAL MATERIAL=PETG\nM104 S250"])
        self.assertEqual(pages, [FEATHER.ScreenPage.FILAMENT_ACTION])

    def test_paused_filament_done_restores_target_and_resumes(self):
        controller = base_controller("paused")
        controller.filament_from_pause = True
        controller.filament_original_target = 215
        pages = []
        controller._show_page = pages.append
        controller._finish_filament(True)
        self.assertEqual(controller.gcode.commands, ["RESUME"])
        self.assertEqual(pages, [FEATHER.ScreenPage.PAUSED])

    def test_paused_filament_resume_uses_blocking_wait(self):
        controller = base_controller("paused")
        controller.filament_from_pause = True
        controller.filament_original_target = 215
        calls = []
        controller._run_blocking_gcode = lambda command, message: calls.append(
            (command, message))
        controller._show_page = lambda page: None

        controller._finish_filament(True)

        self.assertEqual(calls, [("RESUME", "RESUMING PRINT...")])

    def test_filament_workflow_restores_target_when_print_resumed_externally(self):
        controller = base_controller("printing")
        controller.filament_from_pause = True
        controller.filament_original_target = 215
        pages = []
        controller._show_page = pages.append

        controller._finish_filament(True)

        self.assertEqual(controller.gcode.commands, ["M104 S215"])
        self.assertEqual(pages, [FEATHER.ScreenPage.PRINTING])

    def test_cancelled_filament_flow_does_not_reheat_or_return_to_print(self):
        controller = base_controller("paused")
        controller.filament_from_pause = True
        controller.filament_original_target = 215
        controller.print_stats.status["state"] = "cancelled"
        pages = []
        controller._show_page = pages.append

        controller._finish_filament(False)

        self.assertEqual(controller.gcode.commands, [])
        self.assertEqual(pages, [FEATHER.ScreenPage.IDLE_HOME])
        self.assertFalse(controller.filament_from_pause)

    def test_load_persists_selected_material(self):
        controller = base_controller("paused")
        controller.filament_from_pause = True
        controller.filament_material = "ABS-PC"
        controller.extruder = StatusObject({
            "temperature": 270, "target": 270})
        controller.extruder.min_extrude_temp = 170
        calls = []
        controller._run_blocking_gcode = (
            lambda command, message: calls.append((command, message)))
        controller._handle_filament_action("filament.load")
        self.assertEqual(calls, [("LOAD_FILAMENT MATERIAL=ABS-PC",
                                  "LOAD FILAMENT...")])

    def test_filament_action_rejects_extrusion_before_profile_target(self):
        controller = base_controller("paused")
        controller.filament_from_pause = True
        controller.filament_material = "PETG"
        controller.extruder = StatusObject({
            "temperature": 200, "target": 250})
        controller.extruder.min_extrude_temp = 170

        with self.assertRaisesRegex(
                RuntimeError, "has not reached the target"):
            controller._handle_filament_action("filament.load")
        self.assertEqual(controller.gcode.commands, [])

        controller.extruder.status["temperature"] = 256
        with self.assertRaisesRegex(
                RuntimeError, "has not reached the target"):
            controller._handle_filament_action("filament.load")
        self.assertEqual(controller.gcode.commands, [])

    def test_paper_closer_moves_to_smaller_local_z_without_loader(self):
        controller = base_controller()
        controller.z_calibration = ZCalibrationSession()
        controller.z_calibration.begin(0.2, None, "", -0.25, False)
        controller.z_calibration.choose_zone("center")
        controller.z_calibration.set_trigger(-0.5)
        controller.z_calibration.step = 0.010
        controller._render_z_paper = lambda: None
        blocking = []
        controller._run_blocking_gcode = (
            lambda command, message: blocking.append((command, message)))

        controller._handle_z_offset_command(Z_OFFSET_UI.CLOSER)

        self.assertEqual(controller.gcode.commands,
                         ["MOVE_SAFE Z=-0.010000 ABSOLUTE=1 F=300"])
        self.assertAlmostEqual(controller.z_calibration.local_z, 0.49)
        self.assertEqual(blocking, [])

    def test_paper_farther_moves_to_larger_local_z(self):
        controller = base_controller()
        controller.z_calibration = ZCalibrationSession()
        controller.z_calibration.begin(0.2, None, "", -0.25, False)
        controller.z_calibration.choose_zone("center")
        controller.z_calibration.set_trigger(-0.5)
        controller.z_calibration.step = 0.050
        controller._render_z_paper = lambda: None

        controller._handle_z_offset_command(Z_OFFSET_UI.FARTHER)

        self.assertEqual(controller.gcode.commands,
                         ["MOVE_SAFE Z=0.050000 ABSOLUTE=1 F=300"])

    def test_live_z_adjust_uses_original_macro_without_saving(self):
        controller = base_controller("printing")
        controller.page = FEATHER.ScreenPage.LIVE_Z_OFFSET
        controller.toolhead = StatusObject({"homed_axes": "xyz"})
        controller.gcode_move = StatusObject(
            {"homing_origin": (0.0, 0.0, 0.2)})
        controller.params = type("Params", (), {
            "variables": {"z_offset": 0.1, "load_zoffset": 1}})()
        controller.z_offset_limit = 2.0
        controller.z_adjust_warning_threshold = 0.3
        controller.live_z_limit_warned = False
        controller.live_z_dialog = None
        controller.live_z_step = 0.01
        controller._render_live_z_offset = lambda: None

        controller._handle_live_z_action("live_z.closer")
        controller._handle_live_z_action("live_z.farther")

        self.assertEqual(controller.gcode.commands, [
            "_SET_GCODE_OFFSET Z_ADJUST=-0.010 MOVE=1",
            "_SET_GCODE_OFFSET Z_ADJUST=+0.010 MOVE=1",
        ])
        self.assertNotIn("SET_MOD", "\n".join(controller.gcode.commands))

    def test_live_z_warns_once_after_crossing_half_mm(self):
        controller = base_controller("printing")
        controller.page = FEATHER.ScreenPage.LIVE_Z_OFFSET
        controller.toolhead = StatusObject({"homed_axes": "xyz"})
        controller.gcode_move = StatusObject(
            {"homing_origin": (0.0, 0.0, 0.29)})
        controller.params = type("Params", (), {
            "variables": {"z_offset": 0.0, "load_zoffset": 1}})()
        controller.z_offset_limit = 2.0
        controller.z_adjust_warning_threshold = 0.3
        controller.live_z_limit_warned = False
        controller.live_z_dialog = None
        controller._render_live_z_offset = lambda: None

        def run(command, _message):
            controller.gcode.commands.append(command)
            controller.gcode_move.status["homing_origin"] = (0.0, 0.0, 0.34)

        controller._run_blocking_gcode = run
        controller._apply_live_z_adjust(0.05)

        self.assertEqual(controller.live_z_dialog, "limit")
        self.assertTrue(controller.live_z_limit_warned)
        controller.live_z_dialog = None
        controller._apply_live_z_adjust(0.05)
        self.assertIsNone(controller.live_z_dialog)

    def test_live_z_save_can_enable_auto_load(self):
        controller = base_controller("paused")
        controller.page = FEATHER.ScreenPage.LIVE_Z_OFFSET
        controller.toolhead = StatusObject({"homed_axes": "xyz"})
        controller.gcode_move = StatusObject(
            {"homing_origin": (0.0, 0.0, 0.235)})
        controller.params = type("Params", (), {
            "variables": {"z_offset": 0.1, "load_zoffset": 0}})()
        controller.live_z_dialog = "save"
        controller._render_live_z_offset = lambda: None

        controller._handle_live_z_action("live_z.save.yes")

        self.assertEqual(controller.gcode.commands, [
            "SET_MOD PARAM=z_offset VALUE=0.235\n"
            "SET_MOD PARAM=load_zoffset VALUE=1"])
        self.assertIsNone(controller.live_z_dialog)

    def test_z_offset_entry_opens_preparation_without_moving_or_live_change(self):
        controller = base_controller()
        controller.z_calibration = ZCalibrationSession()
        controller.toolhead = StatusObject({
            "homed_axes": "", "position": (20.0, 30.0, 0.0, 0.0)})
        pages = []
        controller._show_page = pages.append
        controller._current_material = lambda: "ABS-PC"

        controller._handle_calibration_action("cal.z")

        self.assertEqual(controller.gcode.commands, [])
        self.assertEqual(controller.calibration_material, "ABS-PC")
        self.assertEqual(pages, [FEATHER.ScreenPage.CALIBRATION_CONFIRM])
        self.assertFalse(controller.z_calibration.active)

    def test_z_offset_bed_point_uses_configured_safe_z_before_xy_move(self):
        controller = base_controller()
        controller.params = type("Params", (), {
            "variables": {"safe_z": 12.5}})()
        controller.z_calibration = ZCalibrationSession()
        controller.z_calibration.begin(
            0.2, None, "", -0.25, False, safe_z=12.5)
        controller.z_calibration.prepared = True
        moves = []
        controller._run_blocking_gcode = (
            lambda command, message: moves.append((command, message)))
        pages = []
        controller._show_page = pages.append

        controller._handle_z_offset_command(Z_OFFSET_UI.ZONE_ACTIONS["rear_right"])

        self.assertEqual(moves, [])
        self.assertEqual(pages, [FEATHER.ScreenPage.Z_OFFSET_PAPER_BRIEFING])

        controller._handle_z_offset_command(Z_OFFSET_UI.ENTER_ZONE)

        self.assertEqual(moves, [(
            "MOVE_SAFE Z=12.5 ABSOLUTE=1 F=600\n"
            "MOVE_SAFE X=94.0 Y=94.0 ABSOLUTE=1 F=6000",
            "POSITIONING HEAD...")])
        self.assertEqual(pages, [FEATHER.ScreenPage.Z_OFFSET_PAPER_BRIEFING,
                                 FEATHER.ScreenPage.Z_OFFSET_PAPER])

    def test_zone_selection_opens_paper_briefing_for_each_zone(self):
        controller = base_controller()
        controller.z_calibration = ZCalibrationSession()
        controller.z_calibration.begin(0.2, None, "", -0.25, False)
        pages = []
        moves = []
        controller._show_page = pages.append
        controller._move_z_offset_head = (
            lambda x, y: moves.append((x, y)))

        controller._handle_z_offset_command(Z_OFFSET_UI.ZONE_ACTIONS["front_left"])
        self.assertEqual(pages, [FEATHER.ScreenPage.Z_OFFSET_PAPER_BRIEFING])
        self.assertEqual(moves, [])

        controller._handle_z_offset_command(Z_OFFSET_UI.ENTER_ZONE)
        self.assertEqual(moves, [(-94.0, -94.0)])
        self.assertEqual(pages[-1], FEATHER.ScreenPage.Z_OFFSET_PAPER)

        controller._handle_z_offset_command(Z_OFFSET_UI.ZONE_ACTIONS["center"])
        self.assertEqual(moves, [(-94.0, -94.0)])
        self.assertEqual(pages[-1], FEATHER.ScreenPage.Z_OFFSET_PAPER_BRIEFING)

        controller._handle_z_offset_command(Z_OFFSET_UI.ENTER_ZONE)
        self.assertEqual(moves[-1], (0.0, 0.0))
        self.assertEqual(pages.count(FEATHER.ScreenPage.Z_OFFSET_PAPER_BRIEFING), 2)

    def test_z_offset_reset_moves_to_zero_candidate_position(self):
        controller = base_controller()
        controller.z_calibration = ZCalibrationSession()
        controller.z_calibration.begin(0.2, None, "", -0.25, False)
        controller.z_calibration.choose_zone("front_left")
        controller.z_calibration.set_trigger(-0.5)
        rendered = []
        controller._render_z_paper = lambda: rendered.append(True)

        controller._handle_z_offset_command(Z_OFFSET_UI.RESET)

        self.assertEqual(controller.gcode.commands, [
            "MOVE_SAFE Z=-0.250000 ABSOLUTE=1 F=300"])
        self.assertEqual(controller.z_calibration.candidate, 0.0)
        self.assertEqual(rendered, [True])

    def test_probe_uses_two_samples_records_trigger_and_retracts_half_mm(self):
        controller = base_controller()
        controller.z_calibration = ZCalibrationSession()
        controller.z_calibration.begin(0.2, None, "", -0.25, False)
        controller.z_calibration.choose_zone("front_right")
        controller.probe = StatusObject({"last_z_result": -0.625})
        controller._render_z_paper = lambda: None
        controller._check_z_pressure = lambda eventtime: False
        probing = []
        controller._run_blocking_gcode = (
            lambda command, message: probing.append((command, message)))

        controller._probe_z_zone()

        self.assertEqual([command for command, _message in probing],
                         ["PROBE SAMPLES=2"])
        self.assertEqual(controller.gcode.commands, [
            "MOVE_SAFE Z=-0.125000 ABSOLUTE=1 F=300"])
        self.assertAlmostEqual(controller.z_calibration.trigger_z, -0.625)
        self.assertAlmostEqual(controller.z_calibration.local_z, 0.5)
        self.assertAlmostEqual(controller.z_calibration.candidate, 0.25)

    def test_manual_paper_start_moves_to_half_safe_z_and_enables_controls(self):
        controller = base_controller()
        controller.z_calibration = ZCalibrationSession()
        controller.z_calibration.begin(0.2, None, "", -0.25, False)
        controller.z_calibration.choose_zone("front_right")
        controller._render_z_paper = lambda: None
        moves = []
        controller._run_blocking_gcode = (
            lambda command, message: moves.append((command, message)))

        controller._handle_z_offset_command(Z_OFFSET_UI.MOVE_SAFE_HALF)

        self.assertEqual(moves, [(
            "MOVE_SAFE Z=5.000000 ABSOLUTE=1 F=300",
            "MOVING TO 5.000 MM...")])
        self.assertEqual(controller.z_calibration.start_mode, "manual")
        self.assertTrue(controller.z_calibration.ready_for_paper_test)
        self.assertAlmostEqual(controller.z_calibration.reference_z, 5.0)
        self.assertAlmostEqual(controller.z_calibration.paper_contact_z, 5.0)
        self.assertAlmostEqual(controller.z_calibration.candidate, 4.75)

    def test_manual_paper_start_tracks_custom_safe_z(self):
        controller = base_controller()
        controller.z_calibration = ZCalibrationSession()
        controller.z_calibration.begin(
            0.2, None, "", -0.25, False, safe_z=7.5)
        controller.z_calibration.choose_zone("center")
        controller._render_z_paper = lambda: None
        moves = []
        controller._run_blocking_gcode = (
            lambda command, message: moves.append((command, message)))

        controller._handle_z_offset_command(Z_OFFSET_UI.MOVE_SAFE_HALF)

        self.assertEqual(moves, [(
            "MOVE_SAFE Z=3.750000 ABSOLUTE=1 F=300",
            "MOVING TO 3.750 MM...")])

    def test_paper_step_uses_no_loader_or_busy_notice(self):
        controller = base_controller()
        notices = []
        controller.renderer = type("Renderer", (), {
            "busy_notice": lambda self, label: notices.append(label),
            "clear_busy_notice": lambda self: notices.append("clear"),
        })()
        controller.z_calibration = ZCalibrationSession()
        controller.z_calibration.begin(0.2, None, "", -0.25, False)
        controller.z_calibration.choose_zone("center")
        controller.z_calibration.set_trigger(-0.5)
        controller._render_z_paper = lambda: None

        controller._move_z_paper(-0.005)

        self.assertEqual(notices, [])
        self.assertEqual(controller.gcode.commands, [
            "MOVE_SAFE Z=-0.005000 ABSOLUTE=1 F=300"])

    def test_screw_output_is_collected_only_during_workflow(self):
        controller = base_controller()
        controller.calibration_results = []
        controller.calibration_kind = "screws"
        controller.page = FEATHER.ScreenPage.CALIBRATION_PROGRESS
        controller._handle_gcode_output(
            "rear : x=1, y=2, z=0.1 : adjust CW 00:05")
        self.assertEqual(controller.calibration_results[0]["turns"], "00:05")
        controller.page = FEATHER.ScreenPage.IDLE_HOME
        controller._handle_gcode_output(
            "front : x=1, y=2, z=0.1 : adjust CCW 00:10")
        self.assertEqual(len(controller.calibration_results), 1)


class NetworkWorkflowTest(unittest.TestCase):
    def test_snapshot_preserves_dialog_and_reveals_latest_network_state(self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.NETWORK_HOME
        def paint_network():
            controller.renderer.send(controller.renderer.begin_page("Network") + [
                controller.renderer.text(40, 100, controller.network_status["state"])])
        controller._render_network_home = paint_network
        rendering = composed_controller_surface(controller, paint_network)
        controller._show_message("Network message", controller.page)
        controller.network_status["state"] = "CONNECTED"
        frames = len(rendering.frames)

        controller._on_network_event("snapshot", True, 100)

        self.assertEqual(len(rendering.frames), frames)
        self.assertFalse(rendering.latest.has_text("CONNECTED"))
        self.assertEqual(rendering.latest.texts[-1].value, "Network message")
        self.assertEqual(set(controller.renderer._buttons), {"message.ok"})
        controller._close_dialog(FEATHER.ScreenDialog.MESSAGE)
        self.assertTrue(rendering.latest.has_text("CONNECTED"))
        self.assertFalse(rendering.latest.has_text("Network message"))

    """The page is a subscriber: netd decides, these tests feed its lines.

    Nothing here fakes a helper process, a pidfile or a marker directory,
    because the page no longer starts one. What is asserted is the contract at
    the socket -- which commands go out, and what each published line does to
    the screen.
    """

    def test_dashboard_network_card_uses_connection_aware_route(self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.IDLE_HOME
        controller.network_status.update({
            "mode": "WIFI", "state": "CONNECTING", "ssid": "Workshop",
            "progress": "RECOVERY"})
        pages = []
        controller._show_page = pages.append

        action = controller._resolve_semantic_ui_action("nav.network")
        controller._dispatch_semantic_ui_action(action)

        self.assertEqual(controller.network_parent_page, FEATHER.ScreenPage.IDLE_HOME)
        self.assertEqual(pages, [FEATHER.ScreenPage.NETWORK_PROGRESS])

        controller.network_status["state"] = "DISCONNECTED"
        pages.clear()
        controller._dispatch_semantic_ui_action(action)

        self.assertEqual(pages, [FEATHER.ScreenPage.NETWORK_HOME])

    def test_network_home_explains_that_daemon_is_unavailable(self):
        controller = base_controller()
        controller.renderer = FEATHER.FeatherRenderer()
        rendering = RenderCapture(controller.renderer)

        controller._render_network_home()
        unavailable_line_count = len(rendering.latest.texts)

        attach_network(controller)
        controller._render_network_home()

        self.assertEqual(unavailable_line_count, len(rendering.latest.texts) + 1)

    def test_pushed_snapshot_repaints_the_open_network_page(self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.NETWORK_HOME
        renders = []
        controller._render_network_home = lambda: renders.append(True)
        attach_network(controller, [
            "MODE=WIFI\nSTATE=CONNECTED\nSIGNAL=-45\nIP=192.168.2.124\n"])

        controller.network_client._on_readable(101)

        self.assertEqual(controller.network_status["mode"], "WIFI")
        self.assertEqual(controller.network_status["ip"], "192.168.2.124")
        # One line per changed field, and the page is repainted for each: the
        # daemon sends a snapshot as a burst, so this is a single read.
        self.assertEqual(len(renders), 4)

    def test_snapshot_updates_the_dashboard_without_navigating(self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.IDLE_HOME
        updates = []
        controller._update_dashboard = updates.append
        controller._show_page = lambda page: self.fail(
            "a pushed snapshot must not navigate")
        attach_network(controller, ["MODE=ETHERNET\nIP=192.168.2.124\n"])

        controller.network_client._on_readable(101)

        self.assertEqual(controller.network_status["mode"], "ETHERNET")
        self.assertEqual(updates, [101, 101])

    def test_unchanged_field_does_not_repaint(self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.NETWORK_HOME
        controller.network_status["mode"] = "WIFI"
        renders = []
        controller._render_network_home = lambda: renders.append(True)
        attach_network(controller, ["MODE=WIFI\n"])

        controller.network_client._on_readable(101)

        self.assertEqual(renders, [])

    def test_state_arrives_by_event_and_not_by_polling(self):
        # The old page re-ran a status helper on a timer whose interval depended
        # on the mode. A subscription has no interval: servicing an attached
        # client sends nothing at all.
        controller = base_controller()
        sock = attach_network(controller)

        for eventtime in (101, 102, 111, 200):
            controller._service_network(eventtime)

        self.assertEqual(sock.sent, [])

    def test_network_home_disables_the_selected_ethernet_route(self):
        controller = base_controller()
        controller.renderer = FEATHER.FeatherRenderer()
        rendering = RenderCapture(controller.renderer)
        attach_network(controller)
        controller.network_status.update({
            "mode": "ETHERNET", "state": "CONNECTED",
            "ip": "192.168.2.124"})

        controller._render_network_home()

        self.assertFalse(rendering.latest.has_action("net.retry"))
        self.assertFalse(rendering.latest.has_action("net.ethernet"))

        controller.renderer = FEATHER.FeatherRenderer()
        rendering = RenderCapture(controller.renderer)
        controller.network_status.update({
            "mode": "WIFI", "state": "CONNECTED", "ssid": "Workshop"})

        controller._render_network_home()

        self.assertTrue(rendering.latest.has_action("net.ethernet"))

    def test_boot_attempt_is_visible_as_state_not_as_a_marker_file(self):
        # A connect started at boot or from the CLI is simply CONNECTING in the
        # published snapshot. There is no directory to stat and so no window in
        # which an attempt in flight looks like an idle printer.
        controller = base_controller()
        pages = []
        controller._show_page = pages.append
        sock = attach_network(controller, ["STATE=CONNECTING\n"])
        controller.network_client._on_readable(100)

        controller._open_network_page()

        self.assertEqual(pages, [FEATHER.ScreenPage.NETWORK_PROGRESS])
        self.assertIsNone(controller.network_operation)
        self.assertEqual(sock.sent, [])

    def test_reconnect_replaces_disabled_home_with_cancelable_progress(self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.NETWORK_HOME
        controller._render_network_home = lambda: None
        controller._render_network_progress = lambda: None
        pages = []

        def show_page(page):
            controller.page = page
            pages.append(page)

        controller._show_page = show_page
        sock = attach_network(controller, [
            "MODE=WIFI\nSTATE=CONNECTING\nPROGRESS=RECOVERY\n"])
        controller.network_client._on_readable(100)

        self.assertEqual(pages, [FEATHER.ScreenPage.NETWORK_PROGRESS])
        self.assertIsNone(controller.network_operation)

        controller._handle_network_action("net.cancel")
        self.assertEqual(sock.sent, ["CANCEL"])
        self.assertTrue(controller.network_cancel_pending)

        sock.pending.append(
            "STATE=DISCONNECTED\nREASON=CANCELLED\nOK CANCELLED\n")
        controller.network_client._on_readable(101)

        self.assertEqual(pages, [
            FEATHER.ScreenPage.NETWORK_PROGRESS, FEATHER.ScreenPage.NETWORK_HOME])
        self.assertEqual(sock.sent, ["CANCEL"])
        self.assertFalse(controller.network_cancel_pending)

    def test_progress_page_names_the_phase_the_daemon_published(self):
        controller = base_controller()
        controller.network_operation = "wifi"
        controller.renderer = FEATHER.FeatherRenderer()
        rendering = RenderCapture(controller.renderer)
        attach_network(controller, ["PROGRESS=FINDING_NETWORK\n"])
        controller.network_client._on_readable(100)
        controller._render_network_progress()

        self.assertTrue(rendering.latest.has_text(
            NETWORK_UI.NETWORK_PHASES["FINDING_NETWORK"]))

    def test_progress_page_shows_the_fresh_connection_attempt(self):
        controller = base_controller()
        controller.network_operation = "wifi"
        controller.renderer = FEATHER.FeatherRenderer()
        rendering = RenderCapture(controller.renderer)
        sock = attach_network(controller, ["PROGRESS=HANDSHAKE\n"])
        controller.network_client._on_readable(100)
        controller._render_network_progress()
        without_attempt = len(rendering.latest.texts)

        sock.pending.append("ATTEMPT=2/3\n")
        controller.network_client._on_readable(101)
        controller._render_network_progress()

        self.assertTrue(rendering.latest.has_text(
            NETWORK_UI.NETWORK_PHASES["HANDSHAKE"]))
        self.assertEqual(len(rendering.latest.texts), without_attempt + 1)

    def test_startup_progress_explains_existing_network_check(self):
        controller = base_controller()
        controller.renderer = FEATHER.FeatherRenderer()
        rendering = RenderCapture(controller.renderer)
        attach_network(controller, ["PROGRESS=STARTUP\n"])
        controller.network_client._on_readable(100)

        controller._render_network_progress()

        self.assertTrue(rendering.latest.has_text(
            NETWORK_UI.NETWORK_PHASES["STARTUP"]))

    def test_cancelling_snapshot_keeps_ui_busy_until_terminal_verdict(self):
        controller = base_controller()
        controller.network_operation = "wifi"
        attach_network(controller, [
            "STATE=CONNECTING\nPROGRESS=CANCELLING\nREASON=CANCELLED\n"])
        controller.network_client._on_readable(100)

        self.assertEqual(controller.network_operation, "wifi")
        self.assertTrue(controller._network_busy())
        self.assertEqual(NETWORK_UI.NETWORK_PHASES[
            controller.network_status["progress"]],
            "Cancelling network operation...")

    def test_scan_rows_keep_only_passphrase_networks_and_sort_by_signal(self):
        # Deduplication and hidden-name filtering are the daemon's; what the page
        # still owns is that it has no keyboard for an open or enterprise network,
        # and that the strongest signal is offered first.
        def row(ssid, frequency, signal, security, saved=0):
            return ("FREQUENCY=%s SIGNAL=%s SECURITY=%s SAVED=%d NETWORK=%s"
                    % (frequency, signal, security, saved,
                       NETWORK_PROTOCOL.encode_field(ssid)))

        controller = base_controller()
        controller.network_operation = "scan"
        controller.network_return_page = FEATHER.ScreenPage.NETWORK_HOME
        pages = []
        controller._show_page = pages.append
        attach_network(controller, ["\n".join([
            row("Lab", 2462, -55, "[WPA-PSK-TKIP][ESS]"),
            row("Shop", 5180, -45, "[WPA2-PSK-CCMP][ESS]", saved=1),
            row("Open", 2412, -20, "[ESS]"),
            row("Corp", 5200, -30, "[WPA2-EAP-CCMP][ESS]"),
            "OK", ""])])

        controller.network_client._on_readable(100)

        self.assertEqual([(item["ssid"], item["signal"])
                          for item in controller.networks],
                         [("Shop", -45), ("Lab", -55)])
        self.assertEqual(controller.networks[0]["frequency"], 5180)
        self.assertTrue(controller.networks[0]["saved"])
        self.assertEqual(pages, [FEATHER.ScreenPage.WIFI_SCAN])
        self.assertEqual(controller.network_page, 0)

    def test_scan_row_with_a_spaced_name_survives_the_wire(self):
        controller = base_controller()
        controller.network_operation = "scan"
        controller._show_page = lambda page: None
        attach_network(controller, [
            "FREQUENCY=2437 SIGNAL=-40 SECURITY=[WPA2-PSK-CCMP][ESS] "
            "SAVED=0 NETWORK=%s\nOK\n" % NETWORK_PROTOCOL.encode_field("my home network")])

        controller.network_client._on_readable(100)

        self.assertEqual([item["ssid"] for item in controller.networks],
                         ["my home network"])

    def test_error_reply_maps_a_closed_reason_to_its_message(self):
        for reason, expected in (
                ("WRONG_KEY", "Wrong Wi-Fi password"),
                ("DHCP_TIMEOUT", "No address received from DHCP"),
                ("BUSY", "Another network operation is already running"),
                ("NO_PROFILE", "Saved Wi-Fi profile is unavailable"),
                ("WPA_CONTROL_FAILED", "Wi-Fi service did not respond"),
                ("WPA_CONFIG_FAILED",
                 "Unable to configure the Wi-Fi connection"),
                ("WPA_SELECT_FAILED",
                 "Unable to select the Wi-Fi network")):
            controller = base_controller()
            controller.network_operation = "wifi"
            controller.network_return_page = FEATHER.ScreenPage.WIFI_SCAN
            messages = []
            controller._show_message = (
                lambda message, page: messages.append((message, page)))
            attach_network(controller, ["ERR %s\n" % reason])

            controller.network_client._on_readable(100)

            self.assertEqual(messages, [(expected, FEATHER.ScreenPage.WIFI_SCAN)])
            self.assertIsNone(controller.network_operation)
            self.assertEqual(controller.network_deadline, 0.0)

    def test_unrecognised_error_reason_still_reports(self):
        controller = base_controller()
        controller.network_operation = "ethernet"
        controller.network_return_page = FEATHER.ScreenPage.NETWORK_HOME
        messages = []
        controller._show_message = (
            lambda message, page: messages.append((message, page)))
        attach_network(controller, ["ERR SOMETHING_NEW\n"])

        controller.network_client._on_readable(100)

        self.assertEqual(messages,
                         [("Network operation failed",
                           FEATHER.ScreenPage.NETWORK_HOME)])

    def test_connect_toast_is_raised_after_the_page_it_belongs_to(self):
        # A full repaint clears the toast overlay. The previous supervisor set
        # the toast from a poll callback that ran before _show_page, so it was
        # always wiped; ordering the two the other way is the fix.
        controller = base_controller()
        controller.network_operation = "wifi"
        events = []
        controller._show_page = lambda page: events.append(("page", page))
        controller._toast = lambda message: events.append(("toast", message))
        attach_network(controller, ["STATE=CONNECTED\nOK\n"])

        controller.network_client._on_readable(100)

        self.assertEqual(events[-2:], [
            ("page", FEATHER.ScreenPage.NETWORK_HOME),
            ("toast", "Network connected"),
        ])

    def test_failed_connect_does_not_toast(self):
        controller = base_controller()
        controller.network_operation = "wifi"
        controller.network_return_page = FEATHER.ScreenPage.WIFI_SCAN
        controller._show_message = lambda message, page: None
        toasts = []
        controller._toast = toasts.append
        attach_network(controller, ["STATE=DISCONNECTED\nERR WRONG_KEY\n"])

        controller.network_client._on_readable(100)

        self.assertEqual(toasts, [])

    def test_rollback_is_the_daemons_and_the_page_only_sends_cancel(self):
        # The incumbent netblock and the DHCP client belong to the process that
        # installed them. This side has nothing to restore and issues no cleanup.
        controller = base_controller()
        controller.network_operation = "wifi"
        controller.network_status.update(
            {"mode": "WIFI", "state": "CONNECTING", "ssid": "Workshop"})
        sock = attach_network(controller)

        controller._cancel_network_operation()

        self.assertEqual(sock.sent, ["CANCEL"])
        # Still in flight: the operation ends when the daemon reports its verdict,
        # not when the request to abandon it is written.
        self.assertEqual(controller.network_operation, "wifi")

    def test_cancel_verdict_reports_the_daemons_reason(self):
        controller = base_controller()
        controller.network_operation = "wifi"
        controller.network_return_page = FEATHER.ScreenPage.WIFI_SCAN
        messages = []
        controller._show_message = (
            lambda message, page: messages.append((message, page)))
        sock = attach_network(controller)
        controller._cancel_network_operation()

        sock.pending.append("STATE=DISCONNECTED\nERR CANCELLED\n")
        controller.network_client._on_readable(101)

        self.assertEqual(messages, [("Network operation cancelled",
                                     FEATHER.ScreenPage.WIFI_SCAN)])
        self.assertIsNone(controller.network_operation)

    def test_repeated_cancel_sends_one_pending_request(self):
        controller = base_controller()
        controller.network_operation = "wifi"
        controller._show_message = lambda message, page: None
        sock = attach_network(controller)

        self.assertTrue(controller._cancel_network_operation())
        self.assertTrue(controller._cancel_network_operation())

        self.assertEqual(sock.sent, ["CANCEL"])
        self.assertTrue(controller.network_cancel_pending)

        sock.pending.append("STATE=DISCONNECTED\nERR CANCELLED\n")
        controller.network_client._on_readable(101)
        self.assertFalse(controller.network_cancel_pending)

    def test_cancel_without_a_daemon_clears_the_operation_locally(self):
        controller = base_controller()
        controller.network_operation = "wifi"
        controller.network_deadline = 190

        controller._cancel_network_operation()

        self.assertIsNone(controller.network_operation)
        self.assertEqual(controller.network_deadline, 0.0)

    def test_second_operation_is_refused_while_one_is_in_flight(self):
        controller = base_controller()
        controller._show_page = lambda page: None
        sock = attach_network(controller)
        controller._start_scan()

        with self.assertRaisesRegex(RuntimeError, "already running"):
            controller._start_ethernet()

        self.assertEqual(sock.sent, ["SCAN"])

    def test_operation_is_refused_when_the_daemon_is_not_listening(self):
        controller = base_controller()
        controller._show_page = lambda page: None

        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            controller._start_scan()

        self.assertIsNone(controller.network_operation)

    def test_an_attempt_started_elsewhere_blocks_a_new_one(self):
        controller = base_controller()
        attach_network(controller, ["STATE=CONNECTING\n"])
        controller.network_client._on_readable(100)

        self.assertTrue(controller._network_busy())
        self.assertIsNone(controller.network_operation)

    def test_starting_a_print_cancels_the_attempt_but_keeps_the_subscription(self):
        controller = base_controller()
        controller.network_operation = "scan"
        controller.network_deadline = 115
        sock = attach_network(controller)

        controller._change_print_state(FEATHER.PrintState.PRINTING, "printing")

        self.assertEqual(sock.sent, ["CANCEL"])
        # The link must keep rendering on the dashboard while printing.
        self.assertTrue(controller._network_available())

    def test_printing_without_an_attempt_sends_nothing(self):
        controller = base_controller()
        sock = attach_network(controller)

        controller._change_print_state(FEATHER.PrintState.PRINTING, "printing")

        self.assertEqual(sock.sent, [])

    def test_shutdown_closes_only_the_subscription_socket(self):
        controller = base_controller()
        controller.network_operation = "wifi"
        unregistered = []
        controller.reactor.unregister_fd = unregistered.append
        sock = attach_network(controller)

        controller._stop_network_client()

        self.assertTrue(sock.closed)
        self.assertEqual(unregistered, ["netd-fd"])
        self.assertFalse(controller.network_client.connected)
        self.assertIsNone(controller.network_operation)

    def test_daemon_eof_fails_the_attempt_and_clears_the_snapshot(self):
        controller = base_controller()
        controller.network_operation = "wifi"
        controller.network_return_page = FEATHER.ScreenPage.WIFI_SCAN
        controller.network_status.update(
            {"mode": "WIFI", "state": "CONNECTING", "ip": "192.168.2.124"})
        messages = []
        controller._show_message = (
            lambda message, page: messages.append((message, page)))
        controller.reactor.unregister_fd = lambda handle: None
        attach_network(controller, [""])

        controller.network_client._on_readable(101)

        self.assertEqual(messages, [("Network service is unavailable",
                                     FEATHER.ScreenPage.WIFI_SCAN)])
        self.assertEqual(controller.network_status, NETWORK_PROTOCOL.blank_status())
        self.assertFalse(controller.network_client.connected)

    def test_reattach_is_rate_limited_and_resubscribes(self):
        controller = base_controller()
        controller.reactor.register_fd = lambda fd, callback: "handle"
        sockets = []

        def opener():
            sockets.append(FakeNetworkSocket())
            return sockets[-1]

        controller.network_client = NETWORK.NetworkClient(
            controller.reactor, controller._on_network_event,
            opener=opener)
        controller.network_status = controller.network_client.status

        controller._service_network(100)
        self.assertEqual(sockets[0].sent, ["SUBSCRIBE"])

        # Already attached: servicing again neither reconnects nor re-subscribes.
        controller._service_network(101)
        self.assertEqual(len(sockets), 1)

        sockets[0].pending.append("")
        controller.network_client._on_readable(101)
        controller._service_network(101)
        self.assertEqual(len(sockets), 1)

        controller._service_network(105)
        self.assertEqual(len(sockets), 2)
        self.assertEqual(sockets[1].sent, ["SUBSCRIBE"])

    def test_silent_daemon_ends_the_operation_without_retrying(self):
        # A watchdog on the socket, not on the network: it stops the page waiting
        # and never rolls anything back or starts a second attempt.
        controller = base_controller()
        controller._show_page = lambda page: None
        sock = attach_network(controller)
        controller.selected_network = {"ssid": "Workshop"}
        controller._connect_saved_wifi()
        messages = []
        controller._show_message = (
            lambda message, page: messages.append((message, page)))

        controller._service_network(100 + NETWORK_UI.NETWORK_WATCHDOG - 1)
        self.assertEqual(messages, [])

        controller._service_network(100 + NETWORK_UI.NETWORK_WATCHDOG)
        self.assertEqual(messages, [])

        controller._service_network(
            100 + NETWORK_UI.NETWORK_WATCHDOG
            + NETWORK_UI.NETWORK_WATCHDOG_PROBE)

        self.assertEqual(messages, [("Network service stopped responding",
                                     FEATHER.ScreenPage.WIFI_SCAN)])
        self.assertEqual(sock.sent, [
            "CONNECT_WIFI ssid=%s" % NETWORK_PROTOCOL.encode_field("Workshop"),
            "GET",
        ])
        self.assertTrue(sock.closed)
        self.assertFalse(controller.network_client.connected)
        self.assertEqual(controller.network_status,
                         NETWORK_PROTOCOL.blank_status())

    def test_wifi_credentials_travel_on_the_socket_and_never_reach_argv(self):
        controller = base_controller()
        controller.selected_network = {"ssid": "Workshop"}
        controller.password = "secret123"
        controller._show_page = lambda page: None
        with mock.patch("subprocess.Popen") as popen:
            sock = attach_network(controller)
            controller._connect_wifi()

        popen.assert_not_called()
        self.assertEqual(controller.network_operation, "wifi")
        self.assertEqual(controller.network_return_page,
                         FEATHER.ScreenPage.WIFI_SCAN)
        self.assertEqual(len(sock.sent), 1)
        self.assertNotIn("secret123", sock.sent[0])
        self.assertNotIn("Workshop", sock.sent[0])
        self.assertEqual(
            sock.sent[0],
            "CONNECT_WIFI ssid=%s psk=%s" % (
                NETWORK_PROTOCOL.encode_field("Workshop"),
                NETWORK_PROTOCOL.encode_field("secret123")))
        # Dropped from memory here: there is no 0600 temp file left to clean up.
        self.assertEqual(controller.password, "")

    def test_weak_passphrase_is_rejected_before_it_reaches_the_socket(self):
        controller = base_controller()
        controller.selected_network = {"ssid": "Workshop"}
        controller.password = "short"
        sock = attach_network(controller)

        with self.assertRaisesRegex(RuntimeError, "8-63 ASCII"):
            controller._connect_wifi()

        self.assertEqual(sock.sent, [])

    def test_selecting_saved_wifi_reconnects_without_password_prompt(self):
        controller = base_controller()
        controller.networks = [{
            "ssid": "Workshop", "signal": -45, "frequency": 5180,
            "saved": True}]
        controller._show_page = lambda page: None
        sock = attach_network(controller)

        controller._handle_network_action("net.item0")

        self.assertEqual(sock.sent, [
            "CONNECT_WIFI ssid=%s" % NETWORK_PROTOCOL.encode_field("Workshop")])
        self.assertEqual(controller.network_operation, "wifi-saved")
        self.assertEqual(controller.network_return_page,
                         FEATHER.ScreenPage.WIFI_SCAN)

    def test_wrong_saved_password_offers_reset_for_that_network(self):
        controller = base_controller()
        other = {
            "ssid": "Other", "signal": -40, "frequency": 2412,
            "saved": True}
        failed = {
            "ssid": "Workshop", "signal": -45, "frequency": 5180,
            "saved": True}
        controller.networks = [other, failed]
        controller.selected_network = failed
        controller.network_operation = "wifi-saved"
        controller.network_return_page = FEATHER.ScreenPage.WIFI_SCAN
        messages = []
        controller._show_message = (
            lambda message, page, actions=None:
            messages.append((message, page, actions)))
        attach_network(controller, ["ERR WRONG_KEY\n"])

        controller.network_client._on_readable(100)

        self.assertTrue(messages)
        actions = messages[-1][2]
        self.assertIn("net.reset.saved", [action[0] for action in actions])

        pages = []
        controller._show_page = pages.append
        controller._handle_network_action("net.reset.saved")

        self.assertIs(controller.selected_network, failed)
        self.assertEqual(pages, [FEATHER.ScreenPage.WIFI_PASSWORD])

    def test_wrong_unsaved_password_does_not_offer_reset(self):
        controller = base_controller()
        controller.selected_network = {
            "ssid": "New", "signal": -45, "frequency": 5180,
            "saved": False}
        controller.network_operation = "wifi"
        controller.network_return_page = FEATHER.ScreenPage.WIFI_SCAN
        messages = []
        controller._show_message = (
            lambda message, page, actions=None:
            messages.append((message, page, actions)))
        attach_network(controller, ["ERR WRONG_KEY\n"])

        controller.network_client._on_readable(100)

        self.assertEqual(messages, [
            ("Wrong Wi-Fi password", FEATHER.ScreenPage.WIFI_SCAN, None)])
        with self.assertRaisesRegex(RuntimeError, "No saved Wi-Fi password"):
            controller._handle_network_action("net.reset.saved")

    def test_status_block_still_parses_for_the_shared_callers(self):
        # The daemon snapshot is a version-tolerant wire contract: known keys
        # retain their meaning while future fields remain ignorable.
        status = PAGES.FeatherPagesMixin.parse_network_status(
            "MODE=WIFI\n"
            "STATE=CONNECTED\n"
            "SSID=%s\n"
            "SIGNAL=-52\n"
            "IP=192.168.1.42\n"
            "PROGRESS=ONLINE\n"
            "FUTURE_FIELD=whatever\n"
            "this line has no separator\n" % NETWORK_PROTOCOL.encode_field("Workshop"))

        self.assertEqual(status["mode"], "WIFI")
        self.assertEqual(status["state"], "CONNECTED")
        self.assertEqual(status["ssid"], "Workshop")
        self.assertEqual(status["ip"], "192.168.1.42")
        self.assertEqual(status["progress"], "ONLINE")
        self.assertEqual(status["attempt"], "")
        self.assertNotIn("future_field", status)

    def test_truncated_status_block_never_reads_as_online(self):
        for text in ("", "MODE=\n", "garbage\n"):
            status = PAGES.FeatherPagesMixin.parse_network_status(text)
            self.assertEqual(status["state"], "DISCONNECTED")
            self.assertEqual(status["ip"], "")


class TouchEventBridgeTest(unittest.TestCase):
    def test_stale_action_does_not_delay_next_valid_tap(self):
        controller = base_controller()
        controller.last_action_time = -1.0
        controller.pending_action = None
        controller.reactor.now = 100.0
        pages = []
        controller._show_page = pages.append

        controller._dispatch_action("print.cancel.confirm")
        controller._dispatch_action("nav.menu")

        self.assertEqual(pages, [FEATHER.ScreenPage.MAIN_MENU])

    def test_released_button_dispatches_without_flash_or_delayed_callback(self):
        controller = base_controller()
        callbacks = []
        controller.reactor.register_callback = (
            lambda callback, waketime=None: callbacks.append(callback))
        events = []

        class Renderer:
            generation = 3

            def flash_button(self, action):
                events.append(("down", action))
                return True

            def restore_button(self, action):
                events.append(("up", action))

        controller.renderer = Renderer()
        controller._dispatch_action = lambda action: events.append(("action", action))
        controller._handle_touch_action("nav.control")
        self.assertEqual(events, [("action", "nav.control")])
        self.assertEqual(callbacks, [])

    def test_busy_klipper_rejects_normal_tap_but_keeps_cancel_interruptible(self):
        controller = base_controller("printing")
        notices = []
        controller.renderer = type("Renderer", (), {
            "busy_notice": lambda self, label: notices.append(label),
            "flash_button": lambda self, action: False,
        })()
        controller.command_depth = 1
        actions = []
        controller._dispatch_action = actions.append
        controller._handle_touch_action("print.pause")
        self.assertEqual(actions, [])
        self.assertEqual(len(notices), 1)
        controller._handle_touch_action("print.cancel")
        self.assertEqual(actions, ["print.cancel"])
        controller.page = FEATHER.ScreenPage.OPERATION_CANCEL
        controller._handle_touch_action("operation.cancel.back")
        self.assertEqual(actions, ["print.cancel", "operation.cancel.back"])
        controller._handle_touch_action("nav.back")
        self.assertEqual(actions, [
            "print.cancel", "operation.cancel.back"])

    def test_blocking_loader_rejects_touch_and_direct_dispatch(self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.CONTROL_MOVE
        controller.busy_message = "HOMING..."
        controller.command_depth = 0
        controller.renderer = type("Renderer", (), {"generation": 4})()
        pages = []
        controller._go_back = lambda: pages.append("back")

        # The direct touch gate protects raw/stale events which bypass Typer's
        # now-cleared loader hitboxes.
        controller._handle_touch_action("nav.back")
        # Direct dispatch must retain the same safety gate.
        controller._dispatch_action("nav.back")

        self.assertEqual(pages, [])

    def test_periodic_page_update_preserves_blocking_loader(self):
        controller = base_controller("printing")
        controller.page = FEATHER.ScreenPage.PRINTING
        rendering = composed_controller_surface(controller, lambda:
            controller.renderer.loader(controller.busy_message, 0)
            if controller.busy_message else controller.renderer.send(
                controller.renderer.begin_page("Printing")))
        controller.busy_message = "HOMING..."
        controller.operation_context.status["revision"] = 1

        controller._update_operation_context(controller.reactor.monotonic())

        self.assertTrue(rendering.latest.has_text("HOMING..."))
        self.assertEqual(controller.renderer._buttons, {})
        rendering.frames.clear()

        controller.busy_message = None
        controller._render_screen()
        controller.operation_context.status["revision"] = 2
        controller._update_operation_context(controller.reactor.monotonic())

        self.assertEqual(len(rendering.frames), 1)

    def test_calibration_progress_rejects_back_during_dispatcher_macro(self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.CALIBRATION_PROGRESS
        controller.command_depth = 1
        controller.busy_message = None
        controller.renderer = type("Renderer", (), {
            "busy_notice": lambda self, label: None,
        })()
        pages = []
        controller._go_back = lambda: pages.append("back")

        controller._handle_touch_action("nav.back")

        self.assertEqual(pages, [])

    def test_gcode_busy_badge_is_cleared_on_success_and_error(self):
        controller = base_controller()
        events = []
        controller.renderer = type("Renderer", (), {
            "busy_notice": lambda self, label: events.append(("busy", None)),
            "clear_busy_notice": lambda self: events.append(("clear", None)),
        })()
        controller.gcode.run_script = lambda command: events.append(
            ("run", command, controller.command_depth,
             controller._ensure_safety_registry().lease_count))
        controller._run_script("G28")
        self.assertEqual(events, [("busy", None),
                                  ("run", "G28", 1, 0), ("clear", None)])
        self.assertEqual(controller.command_depth, 0)
        self.assertEqual(controller.safety.lease_count, 0)

        events[:] = []
        def fail(command):
            raise RuntimeError("failed")
        controller.gcode.run_script = fail
        with self.assertRaisesRegex(RuntimeError, "failed"):
            controller._run_script("M84")
        self.assertEqual(events, [("busy", None), ("clear", None)])
        self.assertEqual(controller.command_depth, 0)
        self.assertEqual(controller.safety.lease_count, 0)

    def test_blocking_gcode_owns_stable_abort_lease(self):
        controller = base_controller()
        controller.page = FEATHER.ScreenPage.CONTROL_MOVE
        controller.busy_message = None
        controller.toolhead = StatusObject({"homed_axes": ""})
        composed_controller_surface(controller, lambda:
            controller.renderer.loader(controller.busy_message, 0))
        controller._show_page = lambda page: None
        observed = []

        def run(command):
            observed.append((
                command,
                controller.safety.lease_count,
                controller._safety_decision().visible,
                controller._blocking_operation_active(),
            ))
            controller._dispatch_action("nav.back")
            controller.toolhead.status["homed_axes"] = "xyz"

        controller.gcode.run_script = run
        controller._run_blocking_gcode("G28", "HOMING...")

        self.assertEqual(observed, [("G28", 1, True, True)])
        self.assertEqual(controller.page, FEATHER.ScreenPage.CONTROL_MOVE)
        self.assertFalse(controller._blocking_operation_active())
        self.assertEqual(controller.safety.lease_count, 0)
        self.assertTrue(controller._safety_decision().visible)

    def test_fragmented_cpp_tap_events_are_reassembled(self):
        controller = base_controller()
        controller.renderer = type("Renderer", (), {"event_fd": 7})()
        controller.event_partial = ""
        controller.last_touch_time = 0
        controller.dimmed = False
        controller._wake_if_dimmed = lambda: False
        actions = []
        controller._dispatch_action = actions.append
        with mock.patch("os.read", side_effect=[
                b"tap nav.fi", b"les\ntap nav.control\nignored\n"]):
            controller._process_touch_events(1)
            self.assertEqual(actions, [])
            controller._process_touch_events(2)
        self.assertEqual(actions, ["nav.files", "nav.control"])
        self.assertEqual(controller.event_partial, "")

    def test_partial_touch_event_memory_is_bounded(self):
        controller = base_controller()
        controller.renderer = type("Renderer", (), {"event_fd": 7})()
        controller.event_partial = ""
        with self.assertLogs(level="WARNING") as logs, mock.patch(
                "os.read", return_value=b"x" * (FEATHER.MAX_TOUCH_EVENT + 1)):
            controller._process_touch_events(1)
        self.assertEqual(controller.event_partial, "")
        self.assertIn("oversized partial touch event", "\n".join(logs.output))

    def test_first_cpp_tap_after_dim_wakes_and_dispatches(self):
        controller = base_controller()
        controller.renderer = type("Renderer", (), {"event_fd": 7})()
        controller.event_partial = ""
        controller.last_touch_time = 0
        controller.dimmed = True
        controller.params = type("Params", (), {"variables": {"backlight": 65}})()
        backlight = []
        controller._set_backlight = backlight.append
        actions = []
        controller._dispatch_action = actions.append
        with mock.patch("os.read", return_value=b"tap nav.files\n"):
            controller._process_touch_events(1)
        self.assertEqual(backlight, [65])
        self.assertEqual(actions, ["nav.files"])

    def test_touch_packets_draw_on_press_and_dispatch_on_release(self):
        controller = base_controller()
        callbacks = []
        controller.reactor.register_callback = (
            lambda callback, waketime=None: callbacks.append(callback))
        rendered = []
        controller.renderer = type("Renderer", (), {
            "event_fd": 7,
            "decode_action": lambda self, action: action,
            "flash_button": lambda self, action: rendered.append(("down", action)) or True,
            "restore_button": lambda self, action: rendered.append(("up", action)) or True,
        })()
        actions = []
        def dispatch(action):
            actions.append(action)
        controller._dispatch_action = dispatch
        controller.event_partial = ""
        controller.dimmed = False
        with mock.patch("os.read", return_value=b"button nav.control down\n"):
            controller._process_touch_events(1)
        self.assertEqual(rendered, [("down", "nav.control")])
        self.assertEqual(actions, [])
        with mock.patch("os.read", return_value=b"button nav.control up\ntap nav.control\n"):
            controller._process_touch_events(2)
        self.assertEqual(rendered, [("down", "nav.control"),
                                    ("up", "nav.control")])
        self.assertEqual(actions, ["nav.control"])
        self.assertEqual(callbacks, [])
        # A valid tap on the newly rendered page is accepted immediately;
        # stale taps are rejected by the renderer generation instead of a
        # global 350 ms dead period.
        controller._handle_touch_action("nav.files")
        self.assertEqual(actions, ["nav.control", "nav.files"])
        self.assertEqual(callbacks, [])


class RecoveryRobustnessTest(unittest.TestCase):
    def test_corrupt_recovery_file_is_not_advertised(self):
        with tempfile.NamedTemporaryFile(mode="w") as stream:
            stream.write("not-json")
            stream.flush()
            resurrector = RESURRECTION.Resurrector.__new__(RESURRECTION.Resurrector)
            resurrector.state = RESURRECTION.ResurrectorState.RESURRECTION
            resurrector._recovery_active = False
            resurrector._checkpoint_cache_loaded = False
            resurrector.file_path = stream.name
            with self.assertLogs(level="ERROR"):
                status = resurrector.get_status(0)
        self.assertFalse(status["available"])
        self.assertEqual(status["state"], "error")
        self.assertNotIn("file_path", status)

    def test_later_keeps_recovery_data_and_closes_shared_prompt(self):
        controller = base_controller()
        pages = []
        controller._show_page = pages.append
        controller._handle_recovery_action("recovery.later")
        self.assertEqual(pages, [])
        self.assertEqual(controller.gcode.commands, [
            "RESPOND TYPE=command MSG=action:prompt_end"])

    def test_restore_uses_owned_recovery_state_while_print_stats_lags(self):
        controller = base_controller(state="idle")
        controller.resurrection = StatusObject({
            "state": "printing", "available": False})
        commands = []
        messages = []
        controller._run_script = commands.append
        controller._show_message = lambda message, page: messages.append(
            (message, page))

        controller._run_recovery(0., "RESURRECT")

        self.assertEqual(commands, ["RESURRECT"])
        self.assertEqual(messages, [])

    def test_restore_reports_recovery_rollback(self):
        controller = base_controller(state="idle")
        controller.resurrection = StatusObject({
            "state": "resurrection", "available": True})
        messages = []
        controller._run_script = lambda command: None
        controller._show_message = lambda message, page: messages.append(
            (message, page))

        controller._run_recovery(0., "RESURRECT")

        self.assertEqual(messages, [
            ("Restore did not start printing", FEATHER.ScreenPage.RECOVERY_PROMPT)])


class ActionPromptProtocolTest(unittest.TestCase):
    @staticmethod
    def controller():
        controller = base_controller()
        controller.action_prompt = None
        controller.action_prompt_page = 0
        controller.recovery_action = None
        controller.resurrection = None
        shown = []

        def show_page(page):
            controller.page = page
            if getattr(controller, "_screen_root", None) is not None:
                controller._sync_dialog_layers()
                controller._screen_root.invalidate()
                controller._screen_root.paint()
            else:
                shown.append(page)

        controller._show_page = show_page
        def render_dialog():
            if controller._current_dialog() is not None:
                shown.append(controller._current_dialog())
            if isinstance(getattr(controller, "renderer", None),
                          FEATHER.FeatherRenderer):
                FEATHER.FeatherScreen._render_dialog(controller)
        controller._render_dialog = render_dialog
        controller._paint_page = lambda: (
            shown.append(controller.page) if not any(not layer.suspended for layer in controller.dialogs) else None)
        def paint_dialog(instance):
            shown.append(instance.kind)
            if isinstance(controller.renderer, FEATHER.FeatherRenderer):
                FEATHER.FeatherScreen._paint_dialog(controller, instance)
        controller._paint_dialog = paint_dialog
        controller._render_screen = lambda: (
            render_dialog() if controller._current_dialog() is not None
            else show_page(controller.page))
        return controller, shown

    @staticmethod
    def controller_with_navigation():
        controller, _ = ActionPromptProtocolTest.controller()
        del controller._show_page
        del controller._paint_page
        controller._render_screen = (
            FEATHER.FeatherScreen._render_screen.__get__(controller))
        shown = []
        controller.renderer = FEATHER.FeatherRenderer()
        controller._cancel_file_preview = lambda: None
        controller._apply_safety_visibility = lambda page: None
        controller._show_touch_unavailable = lambda: None
        controller._notify_features = lambda *args: None
        def paint_dialog(instance):
            shown.append(instance.kind)
            FEATHER.FeatherScreen._paint_dialog(controller, instance)
        controller._paint_dialog = paint_dialog
        def paint_page(page):
            if not any(not layer.suspended for layer in controller.dialogs):
                shown.append(page)
            controller.renderer.send(controller.renderer.begin_page(page.name))
        controller._render_main_menu = lambda: paint_page(FEATHER.ScreenPage.MAIN_MENU)
        controller._render_home = lambda: paint_page(FEATHER.ScreenPage.IDLE_HOME)
        controller._render_control_home = lambda: paint_page(FEATHER.ScreenPage.CONTROL_HOME)
        controller._render_print_page = lambda: paint_page(controller.page)
        return controller, shown

    def test_prompt_responses_build_show_and_close_dialog(self):
        controller, shown = self.controller()

        controller._handle_gcode_output("\n".join([
            "// action:prompt_begin Choose material",
            "// action:prompt_text Select a profile",
            "// action:prompt_button_group_start",
            "// action:prompt_button PLA|SET_MATERIAL MATERIAL=PLA|primary",
            "// action:prompt_button PETG|SET_MATERIAL MATERIAL=PETG|warning",
            "// action:prompt_button_group_end",
            "// action:prompt_footer_button Cancel|"
            "RESPOND TYPE=command MSG=action:prompt_end|secondary",
            "// action:prompt_show",
        ]))

        self.assertEqual(shown, [FEATHER.ScreenDialog.ACTION_PROMPT])
        self.assertIn(FEATHER.ScreenDialog.ACTION_PROMPT,
                      [layer.kind for layer in controller.dialogs])
        self.assertEqual(controller.action_prompt["title"], "Choose material")
        self.assertEqual(len(controller.action_prompt["rows"]), 1)
        self.assertEqual(
            [button["label"] for button in controller.action_prompt["rows"][0]],
            ["PLA", "PETG"])
        self.assertEqual(
            controller.action_prompt["footer"][0]["command"],
            "RESPOND TYPE=command MSG=action:prompt_end")

        controller._handle_gcode_output("// action:prompt_end")

        self.assertEqual(controller.page, FEATHER.ScreenPage.IDLE_HOME)
        self.assertIsNone(controller._current_dialog())
        self.assertNotIn(FEATHER.ScreenDialog.ACTION_PROMPT,
                         [layer.kind for layer in controller.dialogs])
        self.assertIsNone(controller.action_prompt)

    def test_ending_unshown_draft_preserves_visible_prompt_and_its_buttons(self):
        for end in ("// action:prompt_end B", "// action:prompt_end"):
            with self.subTest(end=end):
                controller, _ = self.controller_with_navigation()
                controller._run_script = controller.gcode.commands.append
                controller._handle_gcode_output("\n".join((
                    "// action:prompt_begin A",
                    "// action:prompt_button Keep|KEEP_A",
                    "// action:prompt_show")))
                visible = controller._current_dialog_instance()
                controller._handle_gcode_output("// action:prompt_begin B")
                controller._handle_gcode_output(end)

                self.assertIsNone(controller.action_prompt)
                self.assertIs(controller._current_dialog_instance(), visible)
                controller._handle_action_prompt_action("prompt.button.0")
                self.assertEqual(controller.gcode.commands, ["KEEP_A"])

    def test_surviving_prompt_close_button_can_end_it_without_a_draft(self):
        for end in ("// action:prompt_end B", "// action:prompt_end"):
            with self.subTest(end=end):
                controller, _ = self.controller_with_navigation()
                capture = RenderCapture(controller.renderer)
                close = "RESPOND TYPE=command MSG=action:prompt_end"
                def run(command):
                    controller.gcode.commands.append(command)
                    self.assertEqual(command, close)
                    controller._handle_gcode_output("// action:prompt_end")
                controller._run_script = run
                controller._handle_gcode_output("\n".join((
                    "// action:prompt_begin A",
                    "// action:prompt_footer_button CLOSE|" + close,
                    "// action:prompt_show",
                    "// action:prompt_begin B", end)))
                controller._handle_gcode_output("// action:prompt_end B")
                self.assertTrue(capture.latest.has_action("prompt.button.0"))
                self.assertIsNone(controller.action_prompt)

                controller._handle_action_prompt_action("prompt.button.0")

                self.assertEqual(controller.gcode.commands, [close])
                self.assertIsNone(controller._current_dialog_instance())
                self.assertFalse(capture.latest.has_action("prompt.button.0"))

    def test_unaddressed_end_closes_surviving_prompt_under_a_message(self):
        controller, _ = self.controller_with_navigation()
        controller._handle_gcode_output("\n".join((
            "// action:prompt_begin A", "// action:prompt_show",
            "// action:prompt_begin B", "// action:prompt_end B")))
        message = controller._show_message("Notice", controller.page)

        controller._handle_gcode_output("// action:prompt_end")

        self.assertIs(controller._current_dialog_instance(), message)
        controller._close_dialog(message)
        self.assertIsNone(controller._current_dialog_instance())

    def test_addressed_end_closes_shown_prompt_while_preserving_other_draft(self):
        controller, _ = self.controller_with_navigation()
        controller._handle_gcode_output("\n".join((
            "// action:prompt_begin A", "// action:prompt_show",
            "// action:prompt_begin B", "// action:prompt_text Still building")))
        draft = controller.action_prompt

        controller._handle_gcode_output("// action:prompt_end A")

        self.assertIsNone(controller._current_dialog_instance())
        self.assertIs(controller.action_prompt, draft)
        controller._handle_gcode_output("// action:prompt_show")
        self.assertEqual(controller._current_dialog_instance().content["title"], "B")

    def test_grouped_prompt_pages_expose_the_entire_instruction_and_commands(self):
        controller, _ = self.controller_with_navigation()
        capture = RenderCapture(controller.renderer)
        instruction = "Follow every step before choosing a material. " * 35
        controller._run_script = controller.gcode.commands.append
        controller._handle_gcode_output("\n".join((
            "// action:prompt_begin Material selection",
            "// action:prompt_text " + instruction,
            "// action:prompt_button_group_start",
            "// action:prompt_button PLA|SELECT_PLA",
            "// action:prompt_button PETG|SELECT_PETG",
            "// action:prompt_button_group_end",
            "// action:prompt_footer_button Cancel|CANCEL_SELECTION",
            "// action:prompt_show")))
        self.assertTrue(capture.latest.has_action("prompt.next"))
        seen = []
        for _ in range(100):
            frame = capture.latest
            seen.extend(text.value for text in frame.texts
                        if text.font == "JetBrainsMono 8pt" and not text.value.isdigit())
            self.assertTrue(frame.has_action("prompt.button.2"))
            if not frame.has_action("prompt.next"):
                break
            controller._handle_action_prompt_action("prompt.next")
        else:
            self.fail("Prompt pagination did not reach the final page")
        self.assertEqual(" ".join(seen), instruction.strip())
        self.assertTrue(frame.has_action("prompt.button.0"))
        self.assertTrue(frame.has_action("prompt.button.1"))
        controller._handle_action_prompt_action("prompt.button.1")
        self.assertEqual(controller.gcode.commands, ["SELECT_PETG"])
        controller._handle_action_prompt_action("prompt.prev")
        self.assertTrue(capture.latest.has_action("prompt.next"))

    def test_late_addressed_end_preserves_replacement_with_same_dialog_type(self):
        controller, _ = self.controller_with_navigation()
        controller._handle_gcode_output("\n".join((
            "// action:prompt_begin A", "// action:prompt_show",
            "// action:prompt_begin B", "// action:prompt_show")))
        visible = controller._current_dialog_instance()

        controller._handle_gcode_output("// action:prompt_end A")

        self.assertIs(controller._current_dialog_instance(), visible)
        self.assertEqual(visible.content["title"], "B")

    def test_visible_prompt_survives_print_start_and_returns_to_current_print_page(self):
        controller, shown = self.controller_with_navigation()
        controller._handle_gcode_output("\n".join((
            "// action:prompt_begin Timelapse video in progress",
            "// action:prompt_text Wait for video generation",
            "// action:prompt_show")))

        controller.print_stats.status["state"] = "printing"
        controller._change_print_state(FEATHER.PrintState.PREPARING, "printing")
        controller.print_stats.status["state"] = "paused"
        controller._change_print_state(FEATHER.PrintState.PAUSED, "paused")

        self.assertEqual(controller.page, FEATHER.ScreenPage.PAUSED)
        self.assertEqual(controller._current_dialog(),
                         FEATHER.ScreenDialog.ACTION_PROMPT)
        self.assertEqual(shown, [FEATHER.ScreenDialog.ACTION_PROMPT] * 3)

        controller.print_stats.status["state"] = "printing"
        controller._change_print_state(FEATHER.PrintState.PRINTING, "printing")
        controller._handle_gcode_output("// action:prompt_end")
        self.assertEqual(controller.page, FEATHER.ScreenPage.PRINTING)
        self.assertEqual(shown[-1], FEATHER.ScreenPage.PRINTING)

    def test_background_navigation_preserves_prompt_until_dismissal(self):
        controller, shown = self.controller_with_navigation()
        controller._handle_gcode_output("\n".join((
            "// action:prompt_begin Choose material",
            "// action:prompt_show")))

        controller._show_page(FEATHER.ScreenPage.MAIN_MENU)
        controller._show_page(FEATHER.ScreenPage.CONTROL_HOME)

        self.assertEqual(controller.page, FEATHER.ScreenPage.CONTROL_HOME)
        self.assertEqual(shown, [FEATHER.ScreenDialog.ACTION_PROMPT] * 3)

        controller._handle_gcode_output("// action:prompt_end")
        self.assertEqual(controller.page, FEATHER.ScreenPage.CONTROL_HOME)
        self.assertEqual(shown[-1], FEATHER.ScreenPage.CONTROL_HOME)

    def test_same_page_refresh_preserves_the_visible_prompt_without_paint(self):
        controller, shown = self.controller_with_navigation()
        controller._handle_gcode_output("\n".join((
            "// action:prompt_begin Choose material",
            "// action:prompt_show")))
        controller._show_page(FEATHER.ScreenPage.CONTROL_HOME)
        shown.clear()

        controller._show_page(controller.page)

        self.assertEqual(shown, [])
        self.assertEqual(controller.page, FEATHER.ScreenPage.CONTROL_HOME)

    def test_prompt_dims_existing_page_then_reveals_latest_page_on_close(self):
        controller, _ = self.controller_with_navigation()
        renderer = controller.renderer
        renderer.footer("NOZZLE 25C", "IDLE")
        renderer._batch_queue.get(timeout=0)
        controller._render_main_menu = lambda: renderer.send(
            renderer.begin_page("Menu"))
        controller._render_dialog = (
            lambda: FEATHER.FeatherScreen._render_dialog(controller))
        controller._render_action_prompt = lambda: renderer.send(
            renderer.dialog("Wait", (), (
                ("prompt.button.0", "OK", "enabled"),)))
        controller._show_page(FEATHER.ScreenPage.MAIN_MENU)
        renderer._batch_queue.get(timeout=0)
        controller._handle_gcode_output("\n".join((
            "// action:prompt_begin Wait",
            "// action:prompt_show")))

        frame = renderer._batch_queue.get(timeout=0)
        self.assertIn(renderer.modal_scrim(), frame.commands)
        self.assertTrue(any('"WAIT"' in command for command in frame.commands))
        self.assertFalse(renderer._footer_drawn)
        self.assertIsNone(renderer._batch_queue.get(timeout=0))
        self.assertEqual(controller.page, FEATHER.ScreenPage.MAIN_MENU)
        self.assertEqual(controller._current_dialog(),
                         FEATHER.ScreenDialog.ACTION_PROMPT)

        controller._render_control_home = lambda: renderer.send(
            renderer.begin_page("Control menu") + renderer.button(
                "nav.move", 20, 80, 200, 60, "MOVE"))
        controller._show_page(FEATHER.ScreenPage.CONTROL_HOME)

        covered = renderer._batch_queue.get(timeout=0)
        self.assertTrue(any('"CONTROL MENU"' in command for command in covered.commands))
        self.assertTrue(any('"WAIT"' in command for command in covered.commands))
        self.assertEqual(set(renderer._buttons), {"prompt.button.0"})

        controller._handle_gcode_output("// action:prompt_end")

        frame = renderer._batch_queue.get(timeout=0)
        self.assertTrue(any('"CONTROL MENU"' in command
                            for command in frame.commands))
        self.assertFalse(any('"WAIT"' in command
                             for command in frame.commands))
        self.assertTrue(any('"NOZZLE 25C"' in command
                            for command in frame.commands))
        self.assertTrue(renderer._footer_drawn)
        self.assertIsNone(controller._current_dialog())
        self.assertEqual(set(renderer._buttons), {"nav.move"})

    def test_message_remains_over_new_page_then_reveals_its_actions(self):
        controller, _ = self.controller_with_navigation()
        renderer = controller.renderer
        controller._render_dialog = (
            lambda: FEATHER.FeatherScreen._render_dialog(controller))
        controller._render_main_menu = lambda: renderer.send(
            renderer.begin_page("Main menu"))
        controller._render_control_home = lambda: renderer.send(
            renderer.begin_page("Control menu") + renderer.button(
                "nav.move", 20, 80, 200, 60, "MOVE"))

        controller._show_page(FEATHER.ScreenPage.MAIN_MENU)
        first = renderer._batch_queue.get(timeout=0)
        self.assertTrue(any('"MAIN MENU"' in command
                            for command in first.commands))
        controller._show_message(
            "Page transition check", FEATHER.ScreenPage.MAIN_MENU)
        covered = renderer._batch_queue.get(timeout=0)
        self.assertIn(renderer.modal_scrim(), covered.commands)
        self.assertEqual(controller._current_dialog(),
                         FEATHER.ScreenDialog.MESSAGE)

        controller._show_page(FEATHER.ScreenPage.CONTROL_HOME)
        updated = renderer._batch_queue.get(timeout=0)
        self.assertTrue(any('"CONTROL MENU"' in command for command in updated.commands))
        self.assertTrue(any('"Page transition check"' in command for command in updated.commands))
        self.assertEqual(set(renderer._buttons), {"message.ok"})

        controller._close_dialog(FEATHER.ScreenDialog.MESSAGE)
        revealed = renderer._batch_queue.get(timeout=0)
        self.assertTrue(any('"CONTROL MENU"' in command
                            for command in revealed.commands))
        self.assertFalse(any('"Page transition check"' in command
                             for command in revealed.commands))
        self.assertEqual(controller.page, FEATHER.ScreenPage.CONTROL_HOME)
        self.assertIsNone(controller._current_dialog())
        self.assertEqual(set(renderer._buttons), {"nav.move"})

    def test_visual_dialog_scenarios_reveal_latest_background_page(self):
        from feather_ui_test.scenarios import ScenarioCatalog

        controller, _ = self.controller_with_navigation()
        controller._show_page(FEATHER.ScreenPage.MAIN_MENU)
        scenarios = ScenarioCatalog(type("Run", (), {"host": controller})())

        scenarios._open_navigation_message()
        scenarios._change_page_under_dialog(
            FEATHER.ScreenPage.CONTROL_HOME, FEATHER.ScreenDialog.MESSAGE)
        controller._close_dialog(FEATHER.ScreenDialog.MESSAGE)
        self.assertEqual(controller.page, FEATHER.ScreenPage.CONTROL_HOME)
        self.assertIsNone(controller._current_dialog())

        controller._show_page(FEATHER.ScreenPage.MAIN_MENU)
        scenarios._open_navigation_prompt()
        scenarios._change_page_under_dialog(
            FEATHER.ScreenPage.CONTROL_HOME,
            FEATHER.ScreenDialog.ACTION_PROMPT)
        scenarios._dismiss_navigation_prompt()
        self.assertEqual(controller.page, FEATHER.ScreenPage.CONTROL_HOME)
        self.assertIsNone(controller._current_dialog())

    def test_new_message_replaces_prompt_on_current_page(self):
        controller, shown = self.controller_with_navigation()
        controller._handle_gcode_output("\n".join((
            "// action:prompt_begin Wait",
            "// action:prompt_show")))
        controller._show_page(FEATHER.ScreenPage.CONTROL_HOME)
        controller._show_dialog(FEATHER.ScreenDialog.MESSAGE)
        self.assertEqual([layer.kind for layer in controller.dialogs], [
            FEATHER.ScreenDialog.ACTION_PROMPT, FEATHER.ScreenDialog.MESSAGE])
        shown.clear()

        controller._close_dialog(FEATHER.ScreenDialog.MESSAGE)

        self.assertEqual(shown, [FEATHER.ScreenDialog.ACTION_PROMPT])
        self.assertEqual(controller.page, FEATHER.ScreenPage.CONTROL_HOME)
        controller._handle_gcode_output("// action:prompt_end")

        self.assertEqual(shown[-1], FEATHER.ScreenPage.CONTROL_HOME)
        self.assertEqual(controller.page, FEATHER.ScreenPage.CONTROL_HOME)
        self.assertIsNone(controller._current_dialog())

    def test_replacing_prompt_repaints_one_message_over_the_page(self):
        controller, _ = self.controller_with_navigation()
        renderer = controller.renderer
        controller._render_home = lambda: renderer.send(
            renderer.begin_page("Home"))
        controller._render_dialog = (
            lambda: FEATHER.FeatherScreen._render_dialog(controller))
        controller._render_action_prompt = lambda: renderer.send(
            renderer.dialog("First prompt", (), (
                ("prompt.button.0", "OK", "enabled"),)))
        controller._show_page(FEATHER.ScreenPage.IDLE_HOME)
        renderer._batch_queue.get(timeout=0)
        controller._show_dialog(FEATHER.ScreenDialog.ACTION_PROMPT)
        renderer._batch_queue.get(timeout=0)

        controller._show_message(
            "Second message", FEATHER.ScreenPage.IDLE_HOME,
            title="Replacement")

        frame = renderer._batch_queue.get(timeout=0)
        self.assertEqual([layer.kind for layer in controller.dialogs], [
            FEATHER.ScreenDialog.ACTION_PROMPT, FEATHER.ScreenDialog.MESSAGE])
        self.assertTrue(any('"HOME"' in command for command in frame.commands))
        self.assertTrue(any('"REPLACEMENT"' in command
                            for command in frame.commands))
        self.assertFalse(any('"FIRST PROMPT"' in command
                             for command in frame.commands))
        self.assertIsNone(renderer._batch_queue.get(timeout=0))

    def test_replacing_large_message_with_small_message_clears_old_panel(self):
        controller, _ = self.controller_with_navigation()
        renderer = controller.renderer
        controller._render_home = lambda: renderer.send(
            renderer.begin_page("Home"))
        controller._render_dialog = (
            lambda: FEATHER.FeatherScreen._render_dialog(controller))
        controller._show_page(FEATHER.ScreenPage.IDLE_HOME)
        renderer._batch_queue.get(timeout=0)

        controller._show_message("OLD MESSAGE " * 12, controller.page)
        renderer._batch_queue.get(timeout=0)
        previous_generation = renderer.generation
        controller._show_message("New", controller.page)

        frame = renderer._batch_queue.get(timeout=0)
        self.assertGreater(renderer.generation, previous_generation)
        self.assertTrue(any('"HOME"' in command for command in frame.commands))
        self.assertTrue(any('"New"' in command for command in frame.commands))
        self.assertFalse(any('"OLD MESSAGE' in command
                             for command in frame.commands))

    def test_prompt_blocks_page_action_while_page_changes_underneath(self):
        controller = base_controller("printing")
        controller.page = FEATHER.ScreenPage.PRINTING
        controller.dialogs = [FEATHER.ScreenDialog.ACTION_PROMPT]
        controller.last_action_time = -1.0
        controller.feature_manager = None
        controller._blocking_operation_active = lambda: False

        controller._dispatch_action("print.pause")

        self.assertEqual(controller.gcode.commands, [])

    def test_prompt_button_can_replace_dialog_with_next_menu(self):
        controller, shown = self.controller()
        controller._handle_gcode_output("\n".join([
            "// action:prompt_begin First",
            "// action:prompt_button Next|OPEN_NEXT",
            "// action:prompt_show",
        ]))
        commands = []

        def run(command):
            commands.append(command)
            controller._handle_gcode_output("\n".join([
                "// action:prompt_begin Second",
                "// action:prompt_text This is the next menu",
                "// action:prompt_show",
            ]))

        controller._run_script = run
        controller._handle_action_prompt_action("prompt.button.0")

        self.assertEqual(commands, ["OPEN_NEXT"])
        self.assertEqual(controller.action_prompt["title"], "Second")
        self.assertEqual(shown, [
            FEATHER.ScreenDialog.ACTION_PROMPT, FEATHER.ScreenDialog.ACTION_PROMPT])

    def test_cold_pull_prompt_uses_its_commands_and_shared_context(self):
        controller, shown = self.controller()
        controller.renderer = FEATHER.FeatherRenderer()
        rendering = RenderCapture(controller.renderer)
        controller.extruder = StatusObject({
            "temperature": 87.5, "target": 100.0})
        commands = []
        controller._run_script = commands.append

        controller._handle_gcode_output("\n".join([
            "// action:prompt_begin Cold Pull",
            "// action:prompt_text Choose the material to clean the nozzle.",
            "// action:prompt_button PLA|_COLDPULL_LOAD_MATERIAL "
            "MATERIAL=PLA TEMP=220 COLD=100 PROMPT=1|primary",
            "// action:prompt_footer_button Cancel|"
            "_COLDPULL_LOAD_MATERIAL_END|secondary",
            "// action:prompt_show",
        ]))

        self.assertEqual(shown, [FEATHER.ScreenDialog.ACTION_PROMPT])
        self.assertIn(FEATHER.ScreenDialog.ACTION_PROMPT,
                      [layer.kind for layer in controller.dialogs])
        self.assertTrue(rendering.latest.has_action("prompt.button.0"))

        controller._handle_action_prompt_action("prompt.button.0")
        self.assertEqual(commands, [
            "_COLDPULL_LOAD_MATERIAL MATERIAL=PLA TEMP=220 COLD=100 "
            "PROMPT=1"])

        controller.operation_context.status.update(
            context_path=("Cold Pull",), context_types=("cold_pull",),
            current_state="COOLING NOZZLE", cancel_available=True,
            cancel_target_type="cold_pull", cancel_target_name="Cold Pull",
            cancel_target_mode="cancelable", revision=1)
        controller._handle_gcode_output("\n".join([
            "// action:prompt_begin Cold Pull",
            "// action:prompt_text Cold pull for PLA is in progress.",
            "// action:prompt_footer_button Cancel|_CONTEXT_CANCEL|secondary",
            "// action:prompt_show",
        ]))

        frame = rendering.latest
        self.assertIn(FEATHER.ScreenDialog.ACTION_PROMPT,
                      [layer.kind for layer in controller.dialogs])
        self.assertTrue(frame.has_text(
            controller.operation_context.status["current_state"]))
        self.assertTrue(frame.has_action("coldpull.cancel"))
        self.assertEqual(len(commands), 1)

    def test_cold_pull_status_refresh_preserves_crossing_taps(self):
        controller, _ = self.controller_with_navigation()
        renderer = controller.renderer
        rendering = RenderCapture(renderer)
        controller.extruder = StatusObject({"temperature": 190.0, "target": 100.0})
        controller.operation_context.status.update(
            context_types=("cold_pull",), current_state="COOLING NOZZLE",
            cancel_available=True, cancel_pending=False)
        controller._handle_gcode_output("\n".join((
            "// action:prompt_begin Cold Pull", "// action:prompt_show")))
        tap = renderer._wire_action("coldpull.cancel")
        generation = renderer.generation
        controller.extruder.status["temperature"] = 180.0
        controller._render_dialog()
        controller.operation_context.status["current_state"] = "PULLING"
        controller._render_dialog()
        self.assertEqual(renderer.generation, generation)
        self.assertEqual(renderer.decode_action(tap), "coldpull.cancel")
        self.assertTrue(rendering.latest.has_text("PULLING"))
        self.assertTrue(any("180.0" in text.value for text in rendering.latest.texts))
        controller.operation_context.status["cancel_pending"] = True
        controller._render_dialog()
        self.assertIsNone(renderer.decode_action(tap))
        self.assertNotIn("coldpull.cancel", renderer._buttons)

    def test_cold_pull_prompt_end_closes_cancel_page(self):
        controller, shown = self.controller()
        controller._handle_gcode_output("\n".join([
            "// action:prompt_begin Cold Pull",
            "// action:prompt_text Cold pull for PLA is in progress.",
            "// action:prompt_footer_button Cancel|_CONTEXT_CANCEL|secondary",
            "// action:prompt_show",
        ]))
        controller.operation_context.status.update(
            context_path=("Cold Pull",), context_types=("cold_pull",),
            current_state="HEATING NOZZLE", cancel_available=True,
            cancel_target_type="cold_pull", cancel_target_name="Cold Pull",
            cancel_target_mode="cancelable", revision=1)

        controller._handle_touch_action("coldpull.cancel")
        self.assertEqual(controller.page, FEATHER.ScreenPage.OPERATION_CANCEL)
        self.assertIsNone(controller._current_dialog())
        self.assertEqual(controller.gcode.commands, [])

        controller._handle_gcode_output("// action:prompt_show")
        self.assertEqual(controller.page, FEATHER.ScreenPage.OPERATION_CANCEL)
        self.assertIsNone(controller._current_dialog())

        controller._handle_operation_cancel_action(
            "operation.cancel.confirm")
        self.assertTrue(controller.operation_context.status["cancel_pending"])

        controller._handle_gcode_output("// action:prompt_end")
        self.assertEqual(shown[-1], FEATHER.ScreenPage.IDLE_HOME)
        self.assertNotIn(FEATHER.ScreenDialog.ACTION_PROMPT,
                         [layer.kind for layer in controller.dialogs])
        self.assertIsNone(controller.action_prompt)
        self.assertIsNone(controller.cancel_mode)

    def test_cold_pull_cancel_page_goes_back_to_prompt(self):
        controller, _ = self.controller()
        controller._handle_gcode_output("\n".join((
            "// action:prompt_begin Cold Pull",
            "// action:prompt_text Cold pull is in progress.",
            "// action:prompt_show")))
        controller.operation_context.status.update(
            context_types=("cold_pull",), cancel_available=True,
            cancel_target_name="Cold Pull")

        controller._open_operation_cancel(controller.page)
        self.assertEqual(controller.page, FEATHER.ScreenPage.OPERATION_CANCEL)
        self.assertIsNone(controller._current_dialog())

        controller._handle_operation_cancel_action("operation.cancel.back")

        self.assertEqual(controller.page, FEATHER.ScreenPage.IDLE_HOME)
        self.assertEqual(controller._current_dialog(),
                         FEATHER.ScreenDialog.ACTION_PROMPT)

    def test_cold_pull_prompt_end_during_m108_does_not_redraw_cancel(self):
        controller, shown = self.controller()
        controller._handle_gcode_output("\n".join([
            "// action:prompt_begin Cold Pull",
            "// action:prompt_text Cold pull for PLA is in progress.",
            "// action:prompt_show",
        ]))
        controller.operation_context.status.update(
            context_path=("Cold Pull",), context_types=("cold_pull",),
            current_state="HEATING NOZZLE", cancel_available=True,
            cancel_target_type="cold_pull", cancel_target_name="Cold Pull",
            cancel_target_mode="cancelable", revision=1)
        controller.temperature_wait.variables["active"] = True
        renders = []
        controller._render_cancel_confirm = lambda: renders.append("cancel")
        controller._run_immediate_command = lambda command: (
            controller._handle_gcode_output("// action:prompt_end"))

        controller._handle_touch_action("coldpull.cancel")
        controller._handle_operation_cancel_action(
            "operation.cancel.confirm")

        self.assertEqual(shown[-1], FEATHER.ScreenPage.IDLE_HOME)
        self.assertEqual(renders, [])
        self.assertIsNone(controller.cancel_mode)

    def test_fluidd_cold_pull_cancel_waits_for_prompt_end(self):
        controller, shown = self.controller()
        controller._handle_gcode_output("\n".join([
            "// action:prompt_begin Cold Pull",
            "// action:prompt_text Cold pull for PLA is in progress.",
            "// action:prompt_footer_button Cancel|_CONTEXT_CANCEL|secondary",
            "// action:prompt_show",
        ]))
        controller.operation_context.status.update(
            context_path=("Cold Pull",), context_types=("cold_pull",),
            current_state="PULLING", cancel_available=True,
            cancel_target_type="cold_pull", cancel_target_name="Cold Pull",
            cancel_target_mode="cancelable", revision=1)

        result = controller.operation_context.request_cancel()

        self.assertTrue(result["accepted"])
        self.assertIn(FEATHER.ScreenDialog.ACTION_PROMPT,
                      [layer.kind for layer in controller.dialogs])
        self.assertEqual(controller.page, FEATHER.ScreenPage.IDLE_HOME)
        self.assertEqual(shown, [FEATHER.ScreenDialog.ACTION_PROMPT])

        controller._handle_gcode_output("// action:prompt_end")
        self.assertEqual(shown[-1], FEATHER.ScreenPage.IDLE_HOME)
        self.assertNotIn(FEATHER.ScreenDialog.ACTION_PROMPT,
                         [layer.kind for layer in controller.dialogs])

    def test_cold_pull_message_replaces_prompt_and_survives_prompt_end(self):
        controller, shown = self.controller()
        controller._handle_gcode_output("\n".join([
            "// action:prompt_begin Cold Pull",
            "// action:prompt_text Cold pull for PLA is in progress.",
            "// action:prompt_show",
        ]))
        controller._show_dialog(FEATHER.ScreenDialog.MESSAGE)

        controller._handle_gcode_output("// action:prompt_end")

        self.assertEqual(controller.page, FEATHER.ScreenPage.IDLE_HOME)
        self.assertEqual(controller._current_dialog(), FEATHER.ScreenDialog.MESSAGE)
        self.assertNotIn(FEATHER.ScreenDialog.ACTION_PROMPT,
                         [layer.kind for layer in controller.dialogs])

    def test_prompt_end_closes_recovery_page_without_prompt_buffer(self):
        controller, shown = self.controller()
        controller.page = FEATHER.ScreenPage.RECOVERY_CONFIRM
        controller.recovery_action = "restore"
        controller.print_stats.status["state"] = "standby"

        controller._handle_gcode_output("// action:prompt_end")

        self.assertEqual(shown, [FEATHER.ScreenPage.IDLE_HOME])
        self.assertIsNone(controller.recovery_action)

    def test_resurrection_prompt_uses_specialized_recovery_page(self):
        controller, shown = self.controller()
        controller.resurrection = StatusObject({
            "state": "resurrection", "available": True})

        controller._handle_gcode_output("\n".join([
            "// action:prompt_begin Resurrection",
            "// action:prompt_text Recovery is available",
            "// action:prompt_show",
        ]))

        self.assertEqual(shown, [FEATHER.ScreenPage.RECOVERY_PROMPT])
        self.assertNotIn(FEATHER.ScreenDialog.ACTION_PROMPT,
                         [layer.kind for layer in controller.dialogs])


if __name__ == "__main__":
    unittest.main()
