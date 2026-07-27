## Tests for Feather screen workflows.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import os
import pathlib
import tempfile
import unittest
from unittest import mock

try:
    from tests.test_feather_screen import (
        FEATHER, RESURRECTION, GCodeRecorder, Reactor, StatusObject)
except ImportError:
    from test_feather_screen import (
        FEATHER, RESURRECTION, GCodeRecorder, Reactor, StatusObject)

from ff5m_ui.move import runtime as MOVE_UI
from ff5m_ui.heat import runtime as HEAT_UI
from ff5m_ui.filament import runtime as FILAMENT_UI
from ff5m_ui.filament.actions import select as select_filament
from ff5m_ui.z_offset import runtime as Z_OFFSET_UI
from feather_feature_filament import FilamentFeature

FILES = __import__("feather_files")
PAGES = __import__("feather_screen_pages")


class VirtualSD:
    def __init__(self, root=None, active=False, file_path=None):
        self.sdcard_dirname = root
        self.active = active
        self.current_path = file_path

    def is_active(self):
        return self.active

    def file_path(self):
        return self.current_path


class FinishedProcess:
    def __init__(self, output, returncode=0):
        self.output = output.encode("utf-8")
        self.returncode = returncode
        self.terminated = False

    def poll(self):
        return self.returncode

    def communicate(self):
        return (self.output, None)

    def terminate(self):
        self.terminated = True


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
        return self.messages.pop(0)

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


def base_controller(state="idle"):
    controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
    controller.reactor = Reactor()
    controller.reactor.register_callback = lambda callback, waketime=None: None
    controller.gcode = GCodeRecorder()
    controller.print_stats = StatusObject(
        {"state": state, "info": {"current_layer": 1, "total_layer": 10}})
    controller.virtual_sdcard = VirtualSD(active=state in ("printing", "paused"))
    controller.pending_action = None
    controller.pending_until = 0
    controller.cancel_requested = False
    controller.cancel_waiting_for_heat = False
    controller.cancel_mode = None
    controller.cancel_phase = None
    controller._filament_request_token = 0
    controller.busy_phase = 0
    controller.print_flow = type("Flow", (), {"variables": {
        "active": False, "cancel_requested": False,
        "cancel_dispatched": False, "phase": "IDLE"}})()
    controller.start_print_macro = type("Start", (), {"variables": {
        "print_started": state in ("printing", "paused")}})()
    controller.page = FEATHER.Page.IDLE_HOME
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
    return controller


class FileWorkflowTest(unittest.TestCase):
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
                {entry["name"] for entry in controller.file_entries},
                {"root.gcode", "models/level-one.gco",
                 "models/project/level-two.g"})
            self.assertTrue(all(
                not entry["directory"] for entry in controller.file_entries))

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

            controller = base_controller()
            controller.virtual_sdcard = VirtualSD(root)
            controller.print_history = FEATHER.PrintHistory(history_path)
            controller._load_file_entries()

            self.assertEqual(
                [entry["name"] for entry in controller.file_entries],
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

            with mock.patch("feather_screen_pages.time.time",
                            return_value=1234.0):
                controller._change_print_state(
                    FEATHER.PrintState.PRINTING, "printing")

            reloaded = FEATHER.PrintHistory(history_path)
            self.assertEqual(
                reloaded.last_printed("models/part.gcode"), 1234.0)
            self.assertEqual(controller.last_job_name, "part.gcode")

    def test_start_file_rechecks_path_and_escapes_filename(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, 'part "one".gcode')
            pathlib.Path(path).write_text("G28\n", encoding="utf-8")
            controller = base_controller()
            controller.virtual_sdcard = VirtualSD(root)
            controller.selected_file = {"path": path}
            controller._start_selected_file()
            self.assertEqual(
                controller.gcode.commands,
                ['SDCARD_PRINT_FILE FILENAME="part \\"one\\".gcode"'])
            os.unlink(path)
            with self.assertRaisesRegex(RuntimeError, "no longer available"):
                controller._start_selected_file()

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

            self.assertEqual(controller.file_entries[0]["name"], "USB")
            self.assertTrue(controller.file_entries[0]["directory"])
            self.assertEqual(
                [entry["name"] for entry in controller.file_entries[1:]],
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
                [entry["name"] for entry in controller.file_entries],
                ["models/old.gcode", "new.gcode"])
            controller.selected_file = controller.file_entries[0]
            controller._start_selected_file()
            self.assertEqual(controller.gcode.commands, [
                'SDCARD_PRINT_FILE FILENAME="USB/models/old.gcode"'])

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

        controller._handle_file_action("file.refresh")
        self.assertEqual(rendered, ["usb", "usb"])

        controller.page = FEATHER.Page.FILE_BROWSER
        shown = []
        controller._show_page = lambda page: shown.append(page)
        controller._go_back()
        self.assertEqual(controller.file_source, "internal")
        self.assertEqual(shown, [FEATHER.Page.FILE_BROWSER])

        controller.file_source = "usb"
        controller.page = FEATHER.Page.FILE_BROWSER
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
                PAGES, "scan_gcode_files",
                side_effect=RuntimeError("device disappeared")):
            controller._load_file_entries()

        self.assertEqual(controller.file_source, "usb")
        self.assertEqual(controller.file_entries, [])

    def test_usb_removal_from_confirmation_shows_message(self):
        controller = base_controller()
        controller.file_source = "usb"
        controller.page = FEATHER.Page.FILE_CONFIRM
        controller.selected_file = {"path": "/data/USB/job.gcode"}
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
        self.assertEqual(messages, [
            ("USB drive removed", FEATHER.Page.FILE_BROWSER)])


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

        with mock.patch("feather_files.os.killpg") as killpg:
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

        with mock.patch("feather_files.os.killpg") as killpg:
            monitor.tick(FILES.USB_HELPER_TIMEOUT)

        killpg.assert_called_once_with(running.pid, FEATHER.signal.SIGTERM)
        self.assertFalse(monitor.available)

    def test_stop_signals_active_helper_and_starts_detach(self):
        running = UsbProcess("", running=True)
        detached = UsbProcess("DETACHED\n")
        monitor, calls, reactor, events = self._monitor([detached], [False])
        monitor.resume(0.0)
        monitor.process = running

        with mock.patch("feather_files.os.killpg") as killpg:
            monitor.stop()

        killpg.assert_called_once_with(running.pid, FEATHER.signal.SIGTERM)
        self.assertEqual(calls[-1][1], "detach")
        self.assertTrue(monitor.stopped)
        self.assertTrue(events.closed)
        self.assertEqual(reactor.unregistered, reactor.registered)


class PrintWorkflowTest(unittest.TestCase):
    def test_filament_is_rejected_during_start_print_preparation(self):
        controller = base_controller("printing")
        controller.print_flow.variables.update(active=True, phase="HEATING")
        controller.start_print_macro.variables["print_started"] = False
        notices = []
        controller._toast = notices.append

        controller._handle_print_action("print.filament")

        self.assertEqual(controller.gcode.commands, [])
        self.assertEqual(notices, ["Available after print preparation"])

    def test_cancel_invalidates_filament_request_waiting_on_pause(self):
        controller = base_controller("printing")
        controller.page = FEATHER.Page.PRINTING
        opened = []
        controller._open_filament = lambda from_pause: opened.append(from_pause)
        commands = []

        def run(command):
            commands.append(command)
            if command == "PAUSE":
                controller.page = FEATHER.Page.CANCEL_CONFIRM
                controller._handle_print_action("print.cancel.confirm")
            elif command == "CANCEL_PRINT":
                controller.print_stats.status["state"] = "cancelled"

        controller._run_script = run
        controller._handle_print_action("print.filament")

        self.assertEqual(commands, ["PAUSE", "CANCEL_PRINT"])
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
        self.assertEqual(controller.page, FEATHER.Page.CANCEL_CONFIRM)

    def test_cancel_requires_confirmation_before_macro(self):
        controller = base_controller("paused")
        pages = []
        controller._show_page = pages.append
        controller._handle_print_action("print.cancel")
        self.assertEqual(pages, [FEATHER.Page.CANCEL_CONFIRM])
        self.assertEqual(controller.gcode.commands, [])
        controller._handle_print_action("print.cancel.confirm")
        self.assertEqual(controller.gcode.commands, ["CANCEL_PRINT"])

    def test_started_print_uses_direct_cancel_even_with_active_flow(self):
        controller = base_controller("printing")
        controller.print_flow.variables.update(active=True, phase="PRINTING")
        controller.start_print_macro.variables["print_started"] = True
        controller._handle_print_action("print.cancel.confirm")
        self.assertEqual(controller.gcode.commands, ["CANCEL_PRINT"])
        self.assertEqual(controller.cancel_mode, "direct")

    def test_cancel_during_heat_uses_cooperative_abort_only(self):
        controller = base_controller("printing")
        controller.print_flow.variables.update(active=True, phase="HEATING")
        controller.start_print_macro.variables["print_started"] = False
        controller.temperature_wait = type("Wait", (), {
            "variables": {"active": True, "cancel": False}})()
        recorder = GCodeRecorder()
        def run(command):
            recorder.commands.append(command)
            if command == "FEATHER_ABORT":
                controller.print_flow.variables["cancel_requested"] = True
                controller.temperature_wait.variables["cancel"] = True
        controller.gcode.run_script_from_command = run
        controller._handle_print_action("print.cancel.confirm")
        self.assertEqual(recorder.commands, ["FEATHER_ABORT"])
        self.assertTrue(controller.cancel_requested)
        self.assertTrue(controller.cancel_waiting_for_heat)
        self.assertNotIn("CANCEL_PRINT", recorder.commands)

    def test_unaccepted_cooperative_abort_never_dispatches_cancel_print(self):
        controller = base_controller("printing")
        controller.print_flow.variables.update(active=True, phase="HOMING")
        controller.start_print_macro.variables["print_started"] = False
        with self.assertRaisesRegex(RuntimeError, "did not accept"):
            controller._handle_print_action("print.cancel.confirm")
        self.assertEqual(controller.gcode.commands, ["FEATHER_ABORT"])
        self.assertNotIn("CANCEL_PRINT", controller.gcode.commands)

    def test_cancel_during_homing_bypasses_mutex_runner(self):
        controller = base_controller("printing")
        controller.print_flow.variables.update(active=True, phase="HOMING")
        controller.start_print_macro.variables["print_started"] = False
        calls = []

        def immediate(command):
            calls.append(("immediate", command))
            controller.print_flow.variables["cancel_requested"] = True

        def serialized(command):
            calls.append(("serialized", command))

        controller.gcode.run_script_from_command = immediate
        controller.gcode.run_script = serialized
        controller._handle_print_action("print.cancel.confirm")
        self.assertEqual(calls, [("immediate", "FEATHER_ABORT")])
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

    def test_pending_cancel_page_keeps_only_global_abort_hitbox(self):
        controller = base_controller("paused")
        controller.pending_action = "print.cancel.confirm"
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.renderer.set_emergency_stop_visible(True)
        FEATHER.FeatherScreen._render_cancel_confirm(controller)
        drawing = "\n".join(batches[0])
        self.assertIn("CANCELLING PRINT", drawing)
        self.assertIn("REQUEST ACCEPTED // CONTROLS LOCKED", drawing)
        self.assertNotIn("print.cancel.confirm", drawing)
        self.assertNotIn("nav.back", drawing)
        self.assertIn("global.abort", drawing)

    def test_print_state_transition_selects_correct_page(self):
        controller = base_controller("idle")
        controller._progress_floor = 0.75
        controller._m73_active = True
        pages = []
        controller._show_page = pages.append
        controller._change_print_state(FEATHER.PrintState.PRINTING, "printing")
        controller._change_print_state(FEATHER.PrintState.PAUSED, "paused")
        self.assertEqual(pages, [FEATHER.Page.PRINTING, FEATHER.Page.PAUSED])
        self.assertEqual(controller._progress_floor, 0.0)
        self.assertFalse(controller._m73_active)

    def test_dashboard_remains_visible_after_explicit_print_home(self):
        controller = base_controller("printing")
        controller.page = FEATHER.Page.IDLE_HOME
        controller.home_during_print = True
        pages = []
        controller._show_page = pages.append

        controller._change_print_state(FEATHER.PrintState.PAUSED, "paused")
        controller._change_print_state(FEATHER.PrintState.PRINTING, "printing")

        self.assertEqual(pages, [])

    def test_filament_back_uses_live_terminal_state(self):
        controller = base_controller("paused")
        controller.page = FEATHER.Page.FILAMENT_MATERIAL
        controller.filament_from_pause = True
        controller.print_stats.status["state"] = "cancelled"
        pages = []
        controller._show_page = pages.append

        controller._go_back()

        self.assertEqual(pages, [FEATHER.Page.IDLE_HOME])

    def test_print_state_change_does_not_drop_accepted_cancel(self):
        controller = base_controller("printing")
        controller.pending_action = "print.cancel.confirm"
        controller.cancel_requested = True
        controller.page = FEATHER.Page.CANCEL_CONFIRM
        controller._show_page = lambda page: None
        controller._change_print_state(FEATHER.PrintState.PAUSED, "paused")
        self.assertEqual(controller.pending_action, "print.cancel.confirm")
        self.assertTrue(controller.cancel_requested)

    def test_start_print_macro_has_cooperative_checks_after_long_phases(self):
        root = pathlib.Path(__file__).parents[1]
        macros = (root / "macros" / "base.cfg").read_text(encoding="utf-8")
        start = macros.split("[gcode_macro _START_PRINT]", 1)[1].split(
            "[gcode_macro _WAIT_TEMPERATURE]", 1)[0]
        self.assertIn("G28\n    _PRINT_FLOW_CHECK", start)
        self.assertIn("_PRINT_FLOW_PHASE PHASE=HEATING", start)
        self.assertIn("_PRINT_FLOW_PHASE PHASE=PRIMING", start)
        self.assertGreaterEqual(start.count("_PRINT_FLOW_CHECK"), 5)
        flow_check = macros.split("[gcode_macro _PRINT_FLOW_CHECK]", 1)[1].split(
            "[gcode_macro _START_PRINT]", 1)[0]
        self.assertEqual(flow_check.count("CANCEL_PRINT"), 1)

    def test_feather_abort_is_an_immediate_gcode_command(self):
        root = pathlib.Path(__file__).parents[1]
        gcode = (root / ".py" / "klipper" / "patches" /
                 "gcode.py").read_text(encoding="utf-8")
        screen = (root / ".py" / "klipper" / "plugins" /
                  "feather_screen.py").read_text(encoding="utf-8")
        pages = (root / ".py" / "klipper" / "plugins" /
                 "feather_screen_pages.py").read_text(encoding="utf-8")
        self.assertNotIn("FEATHER_ABORT", gcode)
        self.assertIn('register_immediate_command("FEATHER_ABORT")', screen)
        self.assertIn("def cmd_FEATHER_ABORT", pages)
        self.assertIn('flow.variables["cancel_requested"] = True', pages)

    def test_feather_abort_sets_flow_and_temperature_wait_flags(self):
        controller = base_controller("printing")
        controller.print_flow.variables.update({
            "active": True, "cancel_requested": False})
        controller.temperature_wait = type("Wait", (), {"variables": {
            "active": True, "cancel": False}})()
        responses = []
        gcmd = type("GCmd", (), {
            "error": RuntimeError,
            "respond_raw": responses.append,
        })()

        controller.cmd_FEATHER_ABORT(gcmd)

        self.assertTrue(controller.print_flow.variables["cancel_requested"])
        self.assertTrue(controller.temperature_wait.variables["cancel"])
        self.assertEqual(responses, ["Feather cancellation requested"])

    def test_screw_tune_cleans_or_uses_cooldown_and_repeat_only_probes(self):
        root = pathlib.Path(__file__).parents[1]
        macros = (root / "macros" / "base.cfg").read_text(encoding="utf-8")
        tune = macros.split("[gcode_macro BED_LEVEL_SCREWS_TUNE]", 1)[1].split(
            "[gcode_macro BED_LEVEL_SCREWS_PROBE]", 1)[0]
        probe = macros.split("[gcode_macro BED_LEVEL_SCREWS_PROBE]", 1)[1].split(
            "[gcode_macro _CHECK_BED_MESH]", 1)[0]
        self.assertIn(
            "CLEAR_NOZZLE EXTRUDER_TEMP={extruder_temp} BED_TEMP={bed_temp}",
            tune)
        self.assertNotIn("M140", tune)
        self.assertIn("M104 S{cooldown_t}", tune)
        self.assertNotIn(
            "_WAIT_TEMPERATURE CMD=M140 VALUE={bed_temp}", tune)
        self.assertIn(
            "_WAIT_TEMPERATURE CMD=M104 VALUE={cooldown_t} BELOW=2 ABOVE=3",
            tune)
        self.assertIn("BED_LEVEL_SCREWS_PROBE", tune)
        self.assertIn("LOAD_CELL_TARE", probe)
        self.assertIn("SCREWS_TILT_CALCULATE", probe)
        self.assertNotIn("_WAIT_TEMPERATURE", probe)
        self.assertNotIn("CLEAR_NOZZLE", probe)
        self.assertNotIn("G28", probe)

    def test_cancelled_temperature_wait_only_reports_abort(self):
        root = pathlib.Path(__file__).parents[1]
        macros = (root / "macros" / "base.cfg").read_text(encoding="utf-8")
        final = macros.split(
            "[gcode_macro _WAIT_TEMPERATURE_FINAL_CHECK]", 1)[1].split(
                "[gcode_macro _RAISE_WITH_PRINT_CANCEL]", 1)[0]
        self.assertIn(
            '"Temperature waiting cancelled." if cancelled', final)
        self.assertIn("_RAISE_WITH_PRINT_CANCEL", final)
        self.assertNotIn("TURN_OFF_HEATERS", final)
        self.assertNotIn("M104 S0", final)

    def test_clear_nozzle_reports_homing_before_g28(self):
        root = pathlib.Path(__file__).parents[1]
        macros = (root / "macros" / "base.cfg").read_text(encoding="utf-8")
        clear_nozzle = macros.split(
            "[gcode_macro CLEAR_NOZZLE]", 1)[1].split(
                "[gcode_macro _CLEAR_NOZZLE_PROBE]", 1)[0]
        homing = clear_nozzle.index('_PRINT_STATUS S="HOMING..."')
        home = clear_nozzle.index("G28")
        self.assertLess(homing, home)

class MotionHeatSettingsTest(unittest.TestCase):
    def test_manual_control_pages_cancel_delayed_tasks_on_entry(self):
        cases = (
            ("nav.move", FEATHER.Page.CONTROL_HOME,
             FEATHER.Page.CONTROL_MOVE),
            ("nav.move", FEATHER.Page.IDLE_HOME,
             FEATHER.Page.CONTROL_MOVE),
            ("nav.heat", FEATHER.Page.IDLE_HOME,
             FEATHER.Page.CONTROL_HEAT),
            ("nav.calibration", FEATHER.Page.CONTROL_HOME,
             FEATHER.Page.CALIBRATION_HOME),
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

    def test_dashboard_material_opens_filament_and_returns_home(self):
        controller = base_controller()
        controller.page = FEATHER.Page.IDLE_HOME
        controller.last_action_time = -1.0
        pages = []
        controller._show_page = pages.append
        controller._require_idle = lambda: None
        controller.extruder = StatusObject({"target": 0.0})

        controller._dispatch_action("nav.filament")

        self.assertEqual(pages, [FEATHER.Page.FILAMENT_MATERIAL])
        self.assertEqual(controller.filament_return_page, FEATHER.Page.IDLE_HOME)

        controller.page = FEATHER.Page.FILAMENT_MATERIAL
        controller.filament_from_pause = False
        controller._go_back()
        self.assertEqual(pages[-1], FEATHER.Page.IDLE_HOME)

    def test_dashboard_move_returns_home(self):
        controller = base_controller()
        controller.page = FEATHER.Page.IDLE_HOME
        controller.last_action_time = -1.0
        pages = []
        controller._show_page = pages.append

        controller._dispatch_action("nav.move")

        self.assertEqual(pages, [FEATHER.Page.CONTROL_MOVE])
        self.assertEqual(controller.move_return_page, FEATHER.Page.IDLE_HOME)
        controller.page = FEATHER.Page.CONTROL_MOVE
        controller._go_back()
        self.assertEqual(pages[-1], FEATHER.Page.IDLE_HOME)

    def test_calibration_start_cancels_delayed_tasks_again(self):
        controller = base_controller()
        controller.calibration_kind = "screws"
        controller.calibration_repeat_probe = False
        pages = []
        controller._show_page = pages.append

        controller._start_calibration()

        self.assertEqual(
            controller.gcode.commands, ["_CANCEL_DELAYED_COMMANDS"])
        self.assertEqual(pages, [FEATHER.Page.CALIBRATION_PROGRESS])

    def test_continuous_touch_updates_planner_and_release(self):
        controller = base_controller()
        controller.page = FEATHER.Page.CONTROL_MOVE
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
        controller.page = FEATHER.Page.CONTROL_MOVE
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
        controller.page = FEATHER.Page.CONTROL_MOVE
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
        controller.page = FEATHER.Page.CONTROL_MOVE
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
        controller.page = FEATHER.Page.CONTROL_MOVE
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
        self.assertEqual(notices, ["TOOLHEAD BUSY"])

    def test_joystick_uses_feather_limits_and_actual_z_limits(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
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
        batches = []
        controller.renderer.send = batches.append
        controller.extruder = StatusObject({"temperature": 21.5, "target": 220})
        controller.heater_bed = StatusObject({"temperature": 24.0, "target": 60})
        controller.fan = StatusObject({"speed": 0.25})

        controller._render_heat()

        initial = "\n".join(batches[0])
        self.assertIn("21.5 / 220 C", initial)
        self.assertIn("24.0 / 60 C", initial)
        self.assertIn("25%", initial)
        for material in controller.heating_materials:
            self.assertIn('-t "%s"' % material, initial)
        actions = tuple(
            action for action in HEAT_UI.get_page(
                controller.heating_materials).actions.values()
            if action.key == HEAT_UI.HeatCommand.PREHEAT)
        self.assertEqual(
            set(action.payload for action in actions),
            set(controller.heating_materials))
        controller.fan.status["speed"] = 0.5
        controller._update_heat_status(101)
        self.assertIn("50%", "\n".join(batches[-1]))

    def test_empty_heating_keeps_manual_heat_controls_without_preset_hitboxes(self):
        controller = base_controller()
        controller.heating_materials = ()
        controller.heating_profiles = {}
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.extruder = StatusObject({"temperature": 21.5, "target": 0})
        controller.heater_bed = StatusObject({"temperature": 24.0, "target": 0})
        controller.fan = StatusObject({"speed": 0.0})

        controller._render_heat()

        drawing = "\n".join(batches[-1])
        actions = tuple(
            action.key for action in HEAT_UI.get_page(()).actions.values())
        self.assertIn("NO MATERIALS ENABLED", drawing)
        self.assertIn(HEAT_UI.HeatCommand.NOZZLE_PLUS, actions)
        self.assertIn(HEAT_UI.HeatCommand.BED_PLUS, actions)
        self.assertIn(HEAT_UI.HeatCommand.COOLDOWN, actions)
        self.assertNotIn(HEAT_UI.HeatCommand.PREHEAT, actions)

    def test_move_offers_only_combined_homing_commands(self):
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.jog_step = 1.0
        controller._require_idle = lambda: None
        blocking = []
        controller._run_blocking_gcode = (
            lambda command, message: blocking.append((command, message)))
        controller._toast = lambda message: None

        controller._handle_move_command(MOVE_UI.HOME_ALL)
        controller._handle_move_command(MOVE_UI.HOME_XY)

        self.assertEqual(blocking, [("G28", "HOMING..."),
                                    ("G28 X Y", "HOMING...")])

    def test_move_requires_homed_axis_and_uses_conservative_speed(self):
        controller = base_controller()
        controller.jog_step = 10.0
        controller.toolhead = StatusObject({
            "homed_axes": "y", "position": (0.0, 0.0, 0.0)})
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
        notices = []
        controller._toast = notices.append

        controller._handle_move_command(MOVE_UI.Z_PLUS)
        controller.toolhead.status["position"] = (0.0, 0.0, 210.0)
        controller._handle_move_command(MOVE_UI.Z_PLUS)

        self.assertEqual(controller.gcode.commands,
                         ["MOVE_SAFE Z=210 ABSOLUTE=1 F=600"])
        self.assertEqual(notices, ["Moved Z 5 mm", "Z LIMIT REACHED"])

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
        notices = []
        controller._toast = notices.append

        controller._handle_move_command(MOVE_UI.X_PLUS)
        controller._handle_move_command(MOVE_UI.X_MINUS)
        controller._handle_move_command(MOVE_UI.Z_PLUS)
        controller._handle_move_command(MOVE_UI.Z_MINUS)

        self.assertEqual(controller.gcode.commands,
                         ["MOVE_SAFE X=110 ABSOLUTE=1 F=6000",
                          "MOVE_SAFE Z=220 ABSOLUTE=1 F=600"])
        self.assertEqual(notices, [
            "X LIMIT REACHED", "Moved X -10 mm",
            "Z LIMIT REACHED", "Moved Z -10 mm",
        ])

    def test_low_z_warning_blocks_step_xy_but_keeps_step_z_available(self):
        controller = base_controller()
        controller.jog_step = 1.0
        controller.move_caution_signature = (True, "available")
        controller.toolhead = StatusObject({
            "homed_axes": "xyz", "position": (0.0, 0.0, 20.0)})

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
            "SET_LED LED=chamber_light WHITE=0.5 SYNC=0"])
        self.assertEqual(backlight, [100])

    def test_settings_render_reflects_chamber_light_status(self):
        controller = base_controller()
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append
        controller.params = type("Params", (), {
            "variables": {
                "backlight": 50, "backlight_eco": 10, "sound": 1,
                "chamber_light": 42}})()
        controller.chamber_light = StatusObject({
            "color_data": [(0.0, 0.0, 0.0, 0.0)]})

        controller._render_settings()

        drawing = "\n".join(batches[0])
        self.assertIn('-t "42%"', drawing)
        self.assertIn("settings.led.minus",
                      dict(controller.renderer._buttons))
        self.assertIn("settings.led.plus",
                      dict(controller.renderer._buttons))

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
        batches = []
        controller.renderer.send = batches.append
        controller.extruder = type("Extruder", (), {
            "heater": type(
                "Heater", (), {"min_temp": 0, "max_temp": 300})()})()
        controller.heater_bed = type(
            "Bed", (), {"min_temp": 0, "max_temp": 130})()

        FilamentFeature(controller).render(FEATHER.Page.FILAMENT_MATERIAL)

        drawing = "\n".join(batches[-1])
        actions = tuple(
            action for action in FILAMENT_UI.get_material_page(
                tuple((material, controller._limited_preheat(material)[0])
                      for material in controller.heating_materials)).actions.values()
            if action.key == FILAMENT_UI.FilamentCommand.SELECT)
        self.assertEqual(
            set(action.payload for action in actions),
            set(controller.heating_materials))
        self.assertIn('"ABS-PC"', drawing)
        self.assertIn('"270C"', drawing)
        self.assertNotIn('"NOZZLE 270C"', drawing)
        self.assertNotIn('"ABS-PC  270C"', drawing)

    def test_empty_heating_filament_page_has_only_empty_state_and_back(self):
        controller = base_controller()
        controller.heating_materials = ()
        controller.heating_profiles = {}
        controller.renderer = FEATHER.FeatherRenderer()
        batches = []
        controller.renderer.send = batches.append

        FilamentFeature(controller).render(FEATHER.Page.FILAMENT_MATERIAL)

        drawing = "\n".join(batches[-1])
        self.assertIn("NO MATERIALS ENABLED", drawing)
        self.assertIn("nav.back", drawing)
        self.assertFalse(FILAMENT_UI.get_material_page(()).actions)

    def test_filament_action_back_preserves_heat_and_returns_to_materials(self):
        controller = base_controller()
        controller.page = FEATHER.Page.FILAMENT_ACTION
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

        self.assertEqual(pages, [FEATHER.Page.FILAMENT_MATERIAL])
        self.assertEqual(controller.gcode.commands, [])
        self.assertEqual(controller.extruder.status["target"], 250.0)

    def test_filament_cooling_fan_is_bounded_and_survives_action_back(self):
        controller = base_controller()
        controller.page = FEATHER.Page.FILAMENT_MATERIAL
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
        feature.back(FEATHER.Page.FILAMENT_ACTION)

        self.assertEqual(commands, [
            "SET_FAN_SPEED FAN=fanM106 SPEED=1.00"])
        self.assertTrue(feature._cooling_fan_active)
        self.assertEqual(pages, [FEATHER.Page.FILAMENT_MATERIAL])

        feature.back(FEATHER.Page.FILAMENT_MATERIAL)

        self.assertEqual(commands[-1],
                         "SET_FAN_SPEED FAN=fanM106 SPEED=0.00")
        self.assertFalse(feature._cooling_fan_active)
        self.assertIsNone(feature._selected_target)

    def test_filament_cooling_fan_stops_at_five_degree_threshold(self):
        controller = base_controller()
        controller.page = FEATHER.Page.FILAMENT_MATERIAL
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
        controller.page = FEATHER.Page.FILAMENT_MATERIAL
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
            FEATHER.Page.FILAMENT_MATERIAL, select_filament("PETG"))

        self.assertEqual(handled, ["filament.PETG"])
        self.assertEqual(commands, [
            "SET_FAN_SPEED FAN=fanM106 SPEED=1.00"])
        self.assertEqual(feature._selected_target, 250.0)
        self.assertTrue(feature._cooling_fan_active)

    def test_safe_z_stage_positions_probes_adjusts_saves_then_continues(self):
        controller = base_controller()
        controller.params = type("Params", (), {
            "variables": {"safe_z": 8.0}})()
        controller.z_calibration = FEATHER.ZCalibrationSession()
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
            ("G28\n"
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
        self.assertEqual(pages, [FEATHER.Page.SAFE_Z_CALIBRATION,
                                 FEATHER.Page.CALIBRATION_PROGRESS])
        self.assertEqual(callbacks,
                         [controller._run_z_calibration_preparation])

    def test_safe_z_stage_can_be_skipped_without_changing_setting(self):
        controller = base_controller()
        controller.z_calibration = FEATHER.ZCalibrationSession()
        controller.z_calibration.begin(
            0.0, None, "", -0.25, False, safe_z=9.0)
        pages = []
        callbacks = []
        controller.reactor.register_callback = callbacks.append
        controller._show_page = pages.append

        controller._handle_z_offset_command(Z_OFFSET_UI.SAFE_SKIP)

        self.assertEqual(controller.z_calibration.safe_z, 9.0)
        self.assertEqual(controller.gcode.commands, [])
        self.assertEqual(pages, [FEATHER.Page.CALIBRATION_PROGRESS])
        self.assertEqual(callbacks,
                         [controller._run_z_calibration_preparation])

    def test_zone_selection_back_returns_to_saved_safe_z_without_repreparing(self):
        controller = base_controller()
        controller.page = FEATHER.Page.Z_OFFSET_SUMMARY
        controller.z_calibration = FEATHER.ZCalibrationSession()
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
        controller._render_safe_z = lambda: None

        controller._go_back()

        self.assertEqual(moves, [(
            "G28\nMOVE_SAFE Z=12 ABSOLUTE=1 F=600\n"
            "MOVE_SAFE X=0 Y=0 ABSOLUTE=1 F=6000\n"
            "LOAD_CELL_TARE\nMOVE_SAFE Z=6.000000 ABSOLUTE=1 F=300",
            "POSITIONING HEAD...")])
        self.assertEqual(pages, [FEATHER.Page.SAFE_Z_CALIBRATION])
        self.assertTrue(controller.z_calibration.safe_z_ready)

        controller._handle_z_offset_command(Z_OFFSET_UI.SAFE_HIGHER)
        controller._handle_z_offset_command(Z_OFFSET_UI.SAFE_SAVE)

        self.assertEqual(controller.gcode.commands[-2:], [
            "MOVE_SAFE Z=7.000000 ABSOLUTE=1 F=300",
            "SET_MOD PARAM=safe_z VALUE=7.000",
        ])
        self.assertEqual(pages[-1], FEATHER.Page.Z_OFFSET_SUMMARY)
        self.assertEqual(callbacks, [])

    def test_filament_page_cannot_replace_cancel_confirmation(self):
        controller = base_controller("paused")
        controller.page = FEATHER.Page.CANCEL_CONFIRM
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
        self.assertEqual(pages, [FEATHER.Page.FILAMENT_ACTION])

    def test_paused_filament_done_restores_target_and_resumes(self):
        controller = base_controller("paused")
        controller.filament_from_pause = True
        controller.filament_original_target = 215
        pages = []
        controller._show_page = pages.append
        controller._finish_filament(True)
        self.assertEqual(controller.gcode.commands, ["M104 S215", "RESUME"])
        self.assertEqual(pages, [FEATHER.Page.PAUSED])

    def test_cancelled_filament_flow_does_not_reheat_or_return_to_print(self):
        controller = base_controller("paused")
        controller.filament_from_pause = True
        controller.filament_original_target = 215
        controller.print_stats.status["state"] = "cancelled"
        pages = []
        controller._show_page = pages.append

        controller._finish_filament(False)

        self.assertEqual(controller.gcode.commands, [])
        self.assertEqual(pages, [FEATHER.Page.IDLE_HOME])
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

    def test_material_macros_support_fluidd_and_persist_through_mod_params(self):
        root = pathlib.Path(__file__).parents[1]
        macros = (root / "config" / "material.cfg").read_text(encoding="utf-8")
        self.assertIn("[gcode_macro SET_MATERIAL]", macros)
        self.assertIn("SET_MOD PARAM=current_material", macros)
        self.assertIn("[gcode_macro PREHEAT_MATERIAL]", macros)
        self.assertIn('SET_MATERIAL MATERIAL="{params.MATERIAL}"', macros)
        self.assertIn("for slot in config.heating_slots", macros)
        self.assertIn("_LOAD_MATERIAL_HEATUP MATERIAL={name}", macros)

    def test_saved_z_adjust_uses_homing_origin_not_base_position(self):
        root = pathlib.Path(__file__).parents[1]
        macros = (root / "macros" / "base.cfg").read_text(encoding="utf-8")
        wrapper = macros.split("[gcode_macro SET_GCODE_OFFSET]", 1)[1].split(
            "[gcode_macro LOAD_GCODE_OFFSET]", 1)[0]
        self.assertIn("printer.gcode_move.homing_origin.z + z_adj", wrapper)
        self.assertNotIn("base_position", wrapper)

    def test_paper_closer_moves_to_smaller_local_z_without_loader(self):
        controller = base_controller()
        controller.z_calibration = FEATHER.ZCalibrationSession()
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
        controller.z_calibration = FEATHER.ZCalibrationSession()
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
        controller.page = FEATHER.Page.LIVE_Z_OFFSET
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
        controller.page = FEATHER.Page.LIVE_Z_OFFSET
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
        controller.page = FEATHER.Page.LIVE_Z_OFFSET
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
        controller.z_calibration = FEATHER.ZCalibrationSession()
        controller.toolhead = StatusObject({
            "homed_axes": "", "position": (20.0, 30.0, 0.0, 0.0)})
        pages = []
        controller._show_page = pages.append
        controller._current_material = lambda: "ABS-PC"

        controller._handle_calibration_action("cal.z")

        self.assertEqual(controller.gcode.commands, [])
        self.assertEqual(controller.calibration_material, "ABS-PC")
        self.assertEqual(pages, [FEATHER.Page.CALIBRATION_CONFIRM])
        self.assertFalse(controller.z_calibration.active)

    def test_z_offset_bed_point_uses_configured_safe_z_before_xy_move(self):
        controller = base_controller()
        controller.params = type("Params", (), {
            "variables": {"safe_z": 12.5}})()
        controller.z_calibration = FEATHER.ZCalibrationSession()
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
        self.assertEqual(pages, [FEATHER.Page.Z_OFFSET_PAPER_BRIEFING])

        controller._handle_z_offset_command(Z_OFFSET_UI.ENTER_ZONE)

        self.assertEqual(moves, [(
            "MOVE_SAFE Z=12.5 ABSOLUTE=1 F=600\n"
            "MOVE_SAFE X=94.0 Y=94.0 ABSOLUTE=1 F=6000",
            "POSITIONING HEAD...")])
        self.assertEqual(pages, [FEATHER.Page.Z_OFFSET_PAPER_BRIEFING,
                                 FEATHER.Page.Z_OFFSET_PAPER])

    def test_zone_selection_opens_paper_briefing_for_each_zone(self):
        controller = base_controller()
        controller.z_calibration = FEATHER.ZCalibrationSession()
        controller.z_calibration.begin(0.2, None, "", -0.25, False)
        pages = []
        moves = []
        controller._show_page = pages.append
        controller._move_z_offset_head = (
            lambda x, y: moves.append((x, y)))

        controller._handle_z_offset_command(Z_OFFSET_UI.ZONE_ACTIONS["front_left"])
        self.assertEqual(pages, [FEATHER.Page.Z_OFFSET_PAPER_BRIEFING])
        self.assertEqual(moves, [])

        controller._handle_z_offset_command(Z_OFFSET_UI.ENTER_ZONE)
        self.assertEqual(moves, [(-94.0, -94.0)])
        self.assertEqual(pages[-1], FEATHER.Page.Z_OFFSET_PAPER)

        controller._handle_z_offset_command(Z_OFFSET_UI.ZONE_ACTIONS["center"])
        self.assertEqual(moves, [(-94.0, -94.0)])
        self.assertEqual(pages[-1], FEATHER.Page.Z_OFFSET_PAPER_BRIEFING)

        controller._handle_z_offset_command(Z_OFFSET_UI.ENTER_ZONE)
        self.assertEqual(moves[-1], (0.0, 0.0))
        self.assertEqual(pages.count(FEATHER.Page.Z_OFFSET_PAPER_BRIEFING), 2)

    def test_z_offset_reset_moves_to_zero_candidate_position(self):
        controller = base_controller()
        controller.z_calibration = FEATHER.ZCalibrationSession()
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
        controller.z_calibration = FEATHER.ZCalibrationSession()
        controller.z_calibration.begin(0.2, None, "", -0.25, False)
        controller.z_calibration.choose_zone("front_right")
        controller.probe = StatusObject({"last_z_result": -0.625})
        controller._render_z_paper = lambda: None
        controller._check_z_pressure = lambda eventtime: False
        probing = []
        controller._run_blocking_gcode = (
            lambda command, message: probing.append((command, message)))

        controller._probe_z_zone()

        self.assertEqual(probing, [("PROBE SAMPLES=2", "PROBING...")])
        self.assertEqual(controller.gcode.commands, [
            "MOVE_SAFE Z=-0.125000 ABSOLUTE=1 F=300"])
        self.assertAlmostEqual(controller.z_calibration.trigger_z, -0.625)
        self.assertAlmostEqual(controller.z_calibration.local_z, 0.5)
        self.assertAlmostEqual(controller.z_calibration.candidate, 0.25)

    def test_manual_paper_start_moves_to_half_safe_z_and_enables_controls(self):
        controller = base_controller()
        controller.z_calibration = FEATHER.ZCalibrationSession()
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
        controller.z_calibration = FEATHER.ZCalibrationSession()
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
        controller.z_calibration = FEATHER.ZCalibrationSession()
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
        controller.page = FEATHER.Page.CALIBRATION_PROGRESS
        controller._handle_gcode_output(
            "rear : x=1, y=2, z=0.1 : adjust CW 00:05")
        self.assertEqual(controller.calibration_results[0]["turns"], "00:05")
        controller.page = FEATHER.Page.IDLE_HOME
        controller._handle_gcode_output(
            "front : x=1, y=2, z=0.1 : adjust CCW 00:10")
        self.assertEqual(len(controller.calibration_results), 1)


class NetworkWorkflowTest(unittest.TestCase):
    def test_background_status_refresh_updates_dashboard_without_navigation(self):
        controller = base_controller()
        controller.network_process = FinishedProcess(
            "MODE=ETHERNET\nSSID=\nSIGNAL=\nIP=192.168.2.124\n")
        controller.network_operation = "status-background"
        controller.network_deadline = 200
        controller.network_credentials = None
        controller.network_status = {"mode": "OFFLINE", "ssid": "",
                                     "signal": "", "ip": ""}
        controller.page = FEATHER.Page.IDLE_HOME
        updates = []
        controller._update_dashboard = updates.append
        controller._show_page = lambda page: self.fail(
            "background status must not navigate")
        controller._poll_network_process(101)
        self.assertEqual(controller.network_status["mode"], "ETHERNET")
        self.assertEqual(controller.network_status["ip"], "192.168.2.124")
        self.assertEqual(updates, [101])

    def test_network_helper_uses_an_isolated_process_group(self):
        controller = base_controller()
        controller.network_process = None
        process = FinishedProcess("")
        with mock.patch("subprocess.Popen", return_value=process) as popen:
            controller._show_page = lambda page: None
            controller._start_network_process(
                "status", ["znetwork.sh", "status"],
                FEATHER.Page.NETWORK_HOME)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_stopping_network_helper_signals_its_whole_process_group(self):
        controller = base_controller()
        controller.network_stopping = []
        process = FinishedProcess("")
        process.pid = 123
        with mock.patch("os.getpgid", return_value=456), \
                mock.patch("os.killpg") as killpg:
            controller._retire_network_process(process)
        killpg.assert_called_once_with(456, FEATHER.signal.SIGTERM)
        self.assertEqual(controller.network_stopping[0][2], 456)

    def test_scan_deduplicates_and_filters_unsupported_networks(self):
        output = "\n".join([
            "NETWORK\tShop\t-70\t[WPA2-PSK-CCMP][ESS]",
            "NETWORK\tShop\t-45\t[WPA2-PSK-CCMP][ESS]",
            "NETWORK\tOpen\t-20\t[ESS]",
            "NETWORK\tLab\t-55\t[WPA-PSK-TKIP][ESS]",
            "NETWORK\tBroken\tn/a\t[WPA2-PSK-CCMP][ESS]",
        ])
        controller = base_controller()
        controller.network_process = FinishedProcess(output)
        controller.network_operation = "scan"
        controller.network_return_page = FEATHER.Page.NETWORK_HOME
        pages = []
        controller._show_page = pages.append
        controller._poll_network_process(100)
        self.assertEqual([(item["ssid"], item["signal"])
                          for item in controller.networks],
                         [("Shop", -45), ("Lab", -55)])
        self.assertEqual(pages, [FEATHER.Page.WIFI_SCAN])

    def test_network_error_uses_helper_message(self):
        controller = base_controller()
        controller.network_process = FinishedProcess("ERROR=Wrong password\n", 1)
        controller.network_operation = "wifi"
        controller.network_return_page = FEATHER.Page.WIFI_SCAN
        messages = []
        controller._show_message = lambda message, page: messages.append((message, page))
        controller._poll_network_process(100)
        self.assertEqual(messages,
                         [("Wrong password", FEATHER.Page.WIFI_SCAN)])

    def test_cancel_terminates_process_and_returns_message(self):
        controller = base_controller()
        process = FinishedProcess("")
        controller.network_process = process
        controller.network_operation = "scan"
        controller.network_return_page = FEATHER.Page.NETWORK_HOME
        messages = []
        controller._show_message = lambda message, page: messages.append((message, page))
        controller._cancel_network_process("Cancelled")
        self.assertTrue(process.terminated)
        self.assertIsNone(controller.network_process)
        self.assertEqual(messages, [("Cancelled", FEATHER.Page.NETWORK_HOME)])

    def test_starting_print_stops_idle_only_network_operation(self):
        controller = base_controller()
        controller.network_stopping = []
        controller.network_credentials = None
        process = FinishedProcess("")
        controller.network_process = process
        controller.network_operation = "scan"
        controller.network_deadline = 115

        controller._change_print_state(FEATHER.PrintState.PRINTING, "printing")

        self.assertTrue(process.terminated)
        self.assertIsNone(controller.network_process)
        self.assertIsNone(controller.network_operation)

    def test_wifi_credentials_use_private_file_and_not_process_arguments(self):
        controller = base_controller()
        controller.selected_network = {"ssid": "Workshop"}
        controller.password = "secret123"
        started = []
        controller._start_network_process = (
            lambda operation, args, page: started.append((operation, args, page)))
        controller._connect_wifi()
        operation, args, page = started[0]
        credentials = args[-1]
        try:
            self.assertEqual(operation, "wifi")
            self.assertEqual(page, FEATHER.Page.WIFI_SCAN)
            self.assertNotIn("secret123", " ".join(args))
            self.assertEqual(os.stat(credentials).st_mode & 0o777, 0o600)
            self.assertEqual(pathlib.Path(credentials).read_text(encoding="utf-8"),
                             "Workshop\nsecret123\n")
            self.assertEqual(controller.password, "")
        finally:
            if os.path.exists(credentials):
                os.unlink(credentials)

    def test_wifi_credentials_are_removed_when_operation_is_cancelled(self):
        controller = base_controller()
        controller.network_stopping = []
        controller.network_credentials = None
        controller.selected_network = {"ssid": "Workshop"}
        controller.password = "secret123"
        controller._start_network_process = lambda operation, args, page: None
        controller._connect_wifi()
        credentials = controller.network_credentials
        self.assertTrue(os.path.exists(credentials))

        controller.network_process = None
        controller.network_return_page = FEATHER.Page.WIFI_SCAN
        controller._show_message = lambda message, page: None
        controller._cancel_network_process("Cancelled")

        self.assertFalse(os.path.exists(credentials))
        self.assertIsNone(controller.network_credentials)


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

        self.assertEqual(pages, [FEATHER.Page.MAIN_MENU])

    def test_feedback_from_replaced_page_is_not_restored_or_dispatched(self):
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
        controller.touch_feedback_pending = False
        controller._dispatch_action = lambda action: events.append(("action", action))
        controller._handle_touch_action("nav.control")
        controller.page = FEATHER.Page.CONTROL_HOME
        controller.renderer.generation = 4

        callbacks[0](controller.reactor.monotonic())

        self.assertEqual(events, [("down", "nav.control")])
        self.assertFalse(controller.touch_feedback_pending)
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
        self.assertEqual(notices, ["PLEASE WAIT"])
        controller._handle_touch_action("print.cancel")
        self.assertEqual(actions, ["print.cancel"])
        controller.page = FEATHER.Page.CANCEL_CONFIRM
        controller._handle_touch_action("print.cancel.back")
        self.assertEqual(actions, ["print.cancel", "print.cancel.back"])
        controller._handle_touch_action("nav.back")
        self.assertEqual(actions, [
            "print.cancel", "print.cancel.back", "nav.back"])

    def test_blocking_loader_rejects_current_and_delayed_back(self):
        controller = base_controller()
        controller.page = FEATHER.Page.CONTROL_MOVE
        controller.busy_message = "HOMING..."
        controller.command_depth = 0
        controller.touch_feedback_pending = False
        controller.renderer = type("Renderer", (), {"generation": 4})()
        pages = []
        controller._go_back = lambda: pages.append("back")

        # The direct touch gate protects raw/stale events which bypass Typer's
        # now-cleared loader hitboxes.
        controller._handle_touch_action("nav.back")
        # The dispatch gate protects an action whose 80 ms button feedback was
        # scheduled immediately before the loader appeared.
        controller.touch_feedback_pending = True
        controller._finish_touch_action(
            0, "nav.back", source_page=FEATHER.Page.CONTROL_MOVE,
            generation=4)

        self.assertEqual(pages, [])
        self.assertFalse(controller.touch_feedback_pending)

    def test_calibration_progress_rejects_back_during_dispatcher_macro(self):
        controller = base_controller()
        controller.page = FEATHER.Page.CALIBRATION_PROGRESS
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
            "busy_notice": lambda self, label: events.append(("busy", label)),
            "clear_busy_notice": lambda self: events.append(("clear", None)),
        })()
        controller.gcode.run_script = lambda command: events.append(
            ("run", command, controller.command_depth,
             controller._ensure_safety_registry().lease_count))
        controller._run_script("G28")
        self.assertEqual(events, [("busy", "KLIPPER BUSY"),
                                  ("run", "G28", 1, 0), ("clear", None)])
        self.assertEqual(controller.command_depth, 0)
        self.assertEqual(controller.safety.lease_count, 0)

        events[:] = []
        def fail(command):
            raise RuntimeError("failed")
        controller.gcode.run_script = fail
        with self.assertRaisesRegex(RuntimeError, "failed"):
            controller._run_script("M84")
        self.assertEqual(events, [("busy", "KLIPPER BUSY"), ("clear", None)])
        self.assertEqual(controller.command_depth, 0)
        self.assertEqual(controller.safety.lease_count, 0)

    def test_blocking_gcode_owns_stable_abort_lease(self):
        controller = base_controller()
        controller.page = FEATHER.Page.CONTROL_MOVE
        controller.busy_message = None
        controller.toolhead = StatusObject({"homed_axes": ""})
        controller.renderer = FEATHER.FeatherRenderer()
        controller.renderer.send = lambda commands: None
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
        self.assertEqual(controller.page, FEATHER.Page.CONTROL_MOVE)
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

    def test_touch_feedback_precedes_deferred_action(self):
        controller = base_controller()
        callbacks = []
        controller.reactor.register_callback = (
            lambda callback, waketime=None: callbacks.append(callback))
        rendered = []
        controller.renderer = type("Renderer", (), {
            "flash_button": lambda self, action: rendered.append(("down", action)) or True,
            "restore_button": lambda self, action: rendered.append(("up", action)) or True,
        })()
        controller.touch_feedback_pending = False
        actions = []
        def dispatch(action):
            actions.append(action)
        controller._dispatch_action = dispatch
        controller._handle_touch_action("nav.control")
        controller._handle_touch_action("nav.files")
        self.assertEqual(rendered, [("down", "nav.control")])
        self.assertEqual(actions, [])
        callbacks[0](controller.reactor.monotonic())
        self.assertEqual(rendered, [("down", "nav.control"),
                                    ("up", "nav.control")])
        self.assertEqual(actions, ["nav.control"])
        self.assertEqual(len(callbacks), 1)
        self.assertFalse(controller.touch_feedback_pending)
        # A valid tap on the newly rendered page is accepted immediately;
        # stale taps are rejected by the renderer generation instead of a
        # global 350 ms dead period.
        controller._handle_touch_action("nav.files")
        self.assertEqual(len(callbacks), 2)
        self.assertEqual(rendered[-1], ("down", "nav.files"))


class RecoveryRobustnessTest(unittest.TestCase):
    def test_corrupt_recovery_file_is_not_advertised(self):
        with tempfile.NamedTemporaryFile(mode="w") as stream:
            stream.write("not-json")
            stream.flush()
            resurrector = RESURRECTION.Resurrector.__new__(RESURRECTION.Resurrector)
            resurrector.state = RESURRECTION.ResurrectorState.RESURRECTION
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


class ActionPromptProtocolTest(unittest.TestCase):
    @staticmethod
    def controller():
        controller = base_controller()
        controller.action_prompt = None
        controller.action_prompt_visible = False
        controller.action_prompt_return_page = FEATHER.Page.IDLE_HOME
        controller.action_prompt_page = 0
        controller.recovery_action = None
        controller.resurrection = None
        shown = []

        def show_page(page):
            controller.page = page
            shown.append(page)

        controller._show_page = show_page
        return controller, shown

    def test_builtin_prompts_do_not_mislabel_cancel_as_emergency_abort(self):
        root = pathlib.Path(__file__).parents[1]
        sources = (
            root / "macros" / "base.cfg",
            root / ".py" / "klipper" / "plugins" / "resurrection.py",
        )
        for source in sources:
            text = source.read_text(encoding="utf-8")
            self.assertNotIn("prompt_footer_button Abort|", text, source)

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

        self.assertEqual(shown, [FEATHER.Page.ACTION_PROMPT])
        self.assertTrue(controller.action_prompt_visible)
        self.assertEqual(controller.action_prompt["title"], "Choose material")
        self.assertEqual(len(controller.action_prompt["rows"]), 1)
        self.assertEqual(
            [button["label"] for button in controller.action_prompt["rows"][0]],
            ["PLA", "PETG"])
        self.assertEqual(
            controller.action_prompt["footer"][0]["command"],
            "RESPOND TYPE=command MSG=action:prompt_end")

        controller._handle_gcode_output("// action:prompt_end")

        self.assertEqual(shown[-1], FEATHER.Page.IDLE_HOME)
        self.assertFalse(controller.action_prompt_visible)
        self.assertIsNone(controller.action_prompt)

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
        self.assertEqual(
            controller.action_prompt_return_page, FEATHER.Page.IDLE_HOME)
        self.assertEqual(shown, [
            FEATHER.Page.ACTION_PROMPT, FEATHER.Page.ACTION_PROMPT])

    def test_prompt_end_closes_recovery_page_without_prompt_buffer(self):
        controller, shown = self.controller()
        controller.page = FEATHER.Page.RECOVERY_CONFIRM
        controller.recovery_action = "restore"
        controller.print_stats.status["state"] = "standby"

        controller._handle_gcode_output("// action:prompt_end")

        self.assertEqual(shown, [FEATHER.Page.IDLE_HOME])
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

        self.assertEqual(shown, [FEATHER.Page.RECOVERY_PROMPT])
        self.assertTrue(controller.action_prompt_visible)


if __name__ == "__main__":
    unittest.main()
