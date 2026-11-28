## The product screen drawn by the renderer's frame loop.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import fcntl
import json
import os
import select
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from types import SimpleNamespace

# Puts the plugins on the import path first.
from tests.test_feather_screen import FEATHER, GCodeRecorder, Reactor, StatusObject
from tests.test_feather_screen_controller import ScenarioController
from ff5m_ui import state_screens as state_ui
from tests.feather_render_test_helper import prompt_wire, refresh_prompt, shown_actions, touchable_actions
from tests.feather_typer_loop import TyperLoop, wait_for
from tests.feather_exclude_test_helper import make_screen, tap
from feather.screen.pages.exclude_objects import object_action
from ff5m_ui.move import runtime as MOVE_UI
from feather_ui_test import runner as UI_TEST
from ff5m_ui import dialogs as dialog_ui
from ff5m_ui.benchmark.actions import NEXT_MODE
from ff5m_ui.print_state import PrintState
from ff5m_ui.shaper.actions import ShaperAction
from ff5m_ui.z_offset import actions as Z_ACTIONS
from ff5m_ui.z_offset import runtime as Z_OFFSET_PAGES
from ui.actions import DismissToast, SetValue
from ui.render_receipts import RenderReceipt
from ui.components import Component, Fill
from ui.layout import PageTree
from ui import PageKey, Rect, ThemeColor


class TyperLogTest(unittest.TestCase):
    def test_incomplete_log_record_is_read_only_after_its_newline(self):
        with tempfile.TemporaryDirectory() as directory:
            typer = TyperLoop.__new__(TyperLoop)
            typer.log_path = os.path.join(directory, "log")
            first = {"start": True, "pid": 1, "launch": 0}
            second = {"commands": ["draw"], "pid": 1, "launch": 0}
            encoded = json.dumps(second)
            with open(typer.log_path, "w") as target:
                target.write(json.dumps(first) + "\n" + encoded[:-1])

            self.assertEqual(typer.entries(), [first])
            self.assertEqual(typer.history(), [])
            with open(typer.log_path, "a") as target:
                target.write(encoded[-1:])
            self.assertEqual(typer.entries(), [first])
            with open(typer.log_path, "a") as target:
                target.write("\n")
            self.assertEqual(typer.entries(), [first, second])
            self.assertEqual(typer.history(), [["draw"]])

    def test_log_reader_waits_for_the_writer_to_publish_the_record(self):
        with tempfile.TemporaryDirectory() as directory:
            typer = TyperLoop.__new__(TyperLoop)
            typer.log_path = os.path.join(directory, "log")
            entry = {"commands": ["draw"], "pid": 1, "launch": 0}
            encoded = json.dumps(entry)

            with ThreadPoolExecutor(max_workers=1) as pool:
                with open(typer.log_path, "w") as target:
                    fcntl.flock(target, fcntl.LOCK_EX)
                    target.write(encoded[:-1])
                    target.flush()
                    result = pool.submit(typer.entries)
                    with self.assertRaises(TimeoutError):
                        result.result(timeout=0.05)
                    target.write(encoded[-1:] + "\n")
                self.assertEqual(result.result(timeout=5), [entry])

    def test_malformed_complete_log_record_is_not_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            typer = TyperLoop.__new__(TyperLoop)
            typer.log_path = os.path.join(directory, "log")
            with open(typer.log_path, "w") as target:
                target.write('{"commands":\n')
            with self.assertRaises(json.JSONDecodeError):
                typer.entries()


class MoveAndExcludeOnTheLoopTest(unittest.TestCase):
    """Every state of the pages the screen's frames draw from values: a frame
    that needed the reactor would fail the screen."""

    def _settle(self, controller, typer):
        typer.take(lambda events: False, timeout=0.05)
        self.assertEqual(controller.renderer.get_status()["screen_errors"], 0)

    def _move(self, mode):
        controller = home_controller()
        controller.page = FEATHER.ScreenPage.CONTROL_MOVE
        controller.move_mode = mode
        controller.jog_step = 1.0
        controller.move_caution_acknowledged = False
        controller.joystick_cursor = None
        controller.params = SimpleNamespace(variables={"safe_z": 12.0})
        controller.joystick = SimpleNamespace(xy_speed=600.0, z_speed=25.0)
        controller.bed_mesh = StatusObject({"profile_name": "", "profiles": {}})
        controller._require_idle = lambda: None
        controller.toolhead = StatusObject({"position": (1.0, 2.0, 30.0, 0.0), "homed_axes": "xyz"})
        typer = TyperLoop(controller.renderer)
        self.addCleanup(typer.close)
        typer.start()
        return controller, typer

    def _show_move(self, controller, typer, z, homed, profile="", profiles=(), acknowledged=False, cursor=None):
        controller.toolhead.status.update(position=(1.0, 2.0, z, 0.0), homed_axes=homed)
        controller.bed_mesh.status.update(profile_name=profile, profiles={name: {"points": []} for name in profiles})
        controller.move_caution_acknowledged = acknowledged
        controller.joystick_cursor = cursor
        previous_frames = len(typer.history())
        controller._render_move()
        if controller.move_mode == "step" and "z" not in homed:
            self.assertTrue(wait_for(lambda: len(typer.history()) > previous_frames))
            self._settle(controller, typer)
            drawing = "\n".join(str(command) for batch in typer.history()[previous_frames:]
                                for command in batch)
            self.assertNotIn("%.1f" % z, drawing)
            self.assertNotIn(MOVE_UI.Z_PLUS.wire_id, controller.renderer.shown_actions())
            self.assertNotIn(MOVE_UI.Z_MINUS.wire_id, controller.renderer.shown_actions())
        else:
            self.assertTrue(typer.drew("%.1f" % z, after=previous_frames), "position %s not drawn" % z)
            self._settle(controller, typer)

    def test_both_move_modes_draw_every_state(self):
        for mode in ("step", "joystick"):
            controller, typer = self._move(mode)
            states = (
                dict(z=30.1, homed="xyz"), dict(z=30.2, homed="xy"), dict(z=30.3, homed=""),
                dict(z=30.4, homed="z"), dict(z=1.1, homed="xyz"),
                dict(z=1.2, homed="xyz", profiles=("auto",)),
                dict(z=1.3, homed="xyz", profile="auto", profiles=("auto",)),
                dict(z=1.4, homed="xyz", profile="default", profiles=("default",)),
                dict(z=1.5, homed="xyz", acknowledged=True))
            with self.assertNoLogs(level="WARNING"):
                for state in states:
                    with self.subTest(mode=mode, **state):
                        self._show_move(controller, typer, **state)

    def test_the_joystick_draws_a_cursor_on_each_axis_at_and_past_its_ends(self):
        controller, typer = self._move("joystick")
        xy, z = MOVE_UI.JOYSTICK_XY.wire_id, MOVE_UI.JOYSTICK_Z.wire_id
        pad_x, pad_y = FEATHER.JOYSTICK_XY_CENTER
        rail_x, rail_y = FEATHER.JOYSTICK_Z_CENTER
        cursors = ((xy, pad_x + 20, pad_y - 20), (xy, 0, 0), (xy, 799, 479),
                   (z, rail_x, rail_y + 30), (z, rail_x, 0), (z, rail_x, 479), None)
        with self.assertNoLogs(level="WARNING"):
            for index, cursor in enumerate(cursors):
                with self.subTest(cursor=cursor):
                    self._show_move(controller, typer, 40.1 + index, "xyz", cursor=cursor)

    def test_status_and_feedback_write_values_on_the_loop(self):
        for mode in ("step", "joystick"):
            controller, typer = self._move(mode)
            with self.assertNoLogs(level="WARNING"):
                self._show_move(controller, typer, 20.5, "xyz")
                controller.toolhead.status["position"] = (5.0, 6.0, 21.5, 0.0)
                controller._update_move_status(1.0)
                self.assertTrue(typer.drew("21.5"))
                controller.toolhead.status["position"] = (5.0, 6.0, 4.5, 0.0)
                controller._update_move_status(2.0)
                self.assertTrue(typer.drew("CAUTION"))
                self._settle(controller, typer)

    def test_the_ui_test_answers_the_caution_the_loop_drew(self):
        controller, typer = self._move("step")
        tapped = []
        controller._start_touch_action = tapped.append
        run = UI_TEST.UITestRun(controller)
        for z, profile, profiles, expected in (
                (1.2, "", ("auto",), MOVE_UI.CAUTION_AUTO),
                (1.3, "auto", ("auto",), MOVE_UI.CAUTION_UNLOAD),
                (1.4, "", (), SetValue(MOVE_UI.MoveState.CAUTION_ACKNOWLEDGED, True))):
            with self.subTest(profile=profile, profiles=profiles):
                self._show_move(controller, typer, z, "xyz", profile, profiles)
                self.assertIn(expected.wire_id, controller.renderer.shown_actions())
                tapped.clear()
                run.scenarios._dismiss_move_caution()
                self.assertEqual(tapped, [expected.wire_id])

    def test_step_size_changes_the_value_shown_by_the_loop(self):
        controller, typer = self._move("step")
        with self.assertNoLogs(level="WARNING"):
            self._show_move(controller, typer, 20.5, "xyz")
            for step, label in ((0.1, "0.1 MM"), (5.0, "5 MM")):
                controller.jog_step = step
                controller._render_move()
                self.assertTrue(typer.drew(label))
                self._settle(controller, typer)

    def _exclude(self):
        screen = make_screen()
        del screen.renderer._submit  # the capture steps aside for the real loop
        typer = TyperLoop(screen.renderer)
        self.addCleanup(typer.close)
        typer.start()
        tap(screen, "print.more")
        tap(screen, "more.exclude")
        self.assertEqual(screen.page, FEATHER.ScreenPage.EXCLUDE_MODEL)
        return screen, typer

    def _shown_objects(self, screen, typer, marker):
        screen._render_screen()
        self.assertTrue(typer.drew(marker), "%s not drawn" % marker)
        self._settle(screen, typer)

    def test_exclude_objects_draws_every_state(self):
        screen, typer = self._exclude()
        objects = screen.objects.status["objects"]
        name = objects[0]["name"]
        with self.assertNoLogs(level="WARNING"):
            self._shown_objects(screen, typer, name.replace("_", " "))
            tap(screen, object_action(name))
            self._shown_objects(screen, typer, "SELECTED")
            screen.objects.status["excluded_objects"].append(objects[1]["name"])
            self._shown_objects(screen, typer, "EXCLUDED")
            for number in range(12):
                objects.append(dict(name="Extra_%02d" % number, polygon=[[1, 1], [2, 1], [2, 2]]))
            screen._render_screen()
            self._settle(screen, typer)
            while "exclude.next" in shown_actions(screen._screen_root):
                tap(screen, "exclude.next")
                screen._render_screen()
                self._settle(screen, typer)
            self._shown_objects(screen, typer, "Extra")
            for item in objects:
                item.pop("polygon", None)
            self._shown_objects(screen, typer, "NO GEOMETRY")
            del objects[:]
            screen._render_screen()
            self._settle(screen, typer)


def home_controller():
    controller = ScenarioController.__new__(ScenarioController)
    controller._last_dashboard = None
    controller.page = FEATHER.ScreenPage.IDLE_HOME
    controller.renderer = FEATHER.FeatherRenderer()
    controller.reactor = Reactor()
    controller.extruder = StatusObject({"temperature": 20.0, "target": 0.0})
    controller.heater_bed = StatusObject({"temperature": 21.0, "target": 0.0})
    controller.toolhead = StatusObject({"homed_axes": "xyz"})
    controller.network_status = {"mode": "ETHERNET", "ssid": "", "ip": "192.168.2.124"}
    controller.last_job_name = "NONE"
    controller._current_material = lambda: "PLA"
    controller._read_text = lambda _path: ""
    return controller


class _Clock(Reactor):
    """The time the benchmark's animation runs on; its timer is driven by the test."""

    NEVER = float("inf")

    def monotonic(self):
        return time.monotonic()

    def register_timer(self, callback, when):
        return callback

    def update_timer(self, timer, when):
        pass


class _Pages(PageKey):
    PYTHON_RENDER = "tests.python_render"


class _PythonRender(Component):
    """A class overriding render: only a page painter can run it."""

    def render(self, renderer, state, bounds):
        return []


class ScreenLoopTest(unittest.TestCase):
    def _network_on_loop(self):
        from tests.test_feather_workflows import attach_network

        controller = home_controller()
        controller.print_state = PrintState.IDLE
        controller.last_action_time = controller.last_touch_time = 0
        controller.pending_action, controller.dimmed = None, False
        controller._init_network_ui()
        controller.network_status.update(mode="ETHERNET", state="CONNECTED", ip="192.0.2.14")
        socket = attach_network(controller)
        controller.renderer.set_screen_error_handler(controller._renderer_screen_failed)
        typer = TyperLoop(controller.renderer)
        self.addCleanup(typer.close)
        typer.start()
        controller._render_screen()
        self.assertTrue(typer.drew("192.0.2.14"))
        return controller, typer, socket

    def _tap_on_loop(self, controller, typer, action, page):
        controller.reactor.now += 1
        typer.tell("tap %s" % typer.wire(action))
        self.assertTrue(wait_for(lambda: controller._process_touch_events(controller.reactor.now)
                                or controller.page == page))

    def test_wifi_scan_results_draw_and_select_a_network(self):
        from feather.network.protocol import encode_field
        from ff5m_ui.network import NetworkCommand
        from ff5m_ui.setup.state import SetupCommand
        from ui import Command

        controller, typer, socket = self._network_on_loop()
        with self.assertNoLogs(level="WARNING"):
            self._tap_on_loop(controller, typer, "nav.network", FEATHER.ScreenPage.NETWORK_HOME)
            self.assertTrue(typer.drew("FIND A NETWORK"))
            self._tap_on_loop(controller, typer, Command(NetworkCommand.SCAN).wire_id,
                              FEATHER.ScreenPage.NETWORK_PROGRESS)
            self.assertTrue(typer.drew("SCANNING WI-FI"))
            self.assertIn("SCAN", socket.sent)
            rows = [(5180, -41, 1, "Workshop"), (2437, -59, 0, "Home Wi-Fi"),
                    (2412, -63, 0, "Office"), (2412, -65, 0, "Printer Lab"),
                    (2412, -71, 0, "First page last network"), (2412, -76, 0, "Second page network")]
            socket.pending.append("".join(
                "FREQUENCY=%d SIGNAL=%d SECURITY=[WPA2-PSK-CCMP][ESS] SAVED=%d NETWORK=%s\n"
                % (frequency, signal, saved, encode_field(ssid))
                for frequency, signal, saved, ssid in rows) + "OK\n")
            controller.network_client._on_readable(controller.reactor.now)
            self.assertTrue(typer.drew("First page last network"))
            self.assertTrue(typer.drew("-41 dBm"))
            self.assertTrue(typer.drew("5 GHz"))
            self.assertTrue(typer.drew("2.4 GHz"))
            self.assertTrue(typer.drew("SAVED"))
            controller.reactor.now += 1
            typer.tell("tap %s" % typer.wire(Command(SetupCommand.NEXT).wire_id))
            self.assertTrue(wait_for(lambda: controller._process_touch_events(controller.reactor.now)
                                    or controller.network_page == 1))
            self.assertTrue(typer.drew("Second page network"))
            self._tap_on_loop(controller, typer, Command(NetworkCommand.SELECT, 0).wire_id,
                              FEATHER.ScreenPage.WIFI_PASSWORD)
            self.assertTrue(typer.drew("WI-FI PASSWORD"))
        self.assertEqual(controller.selected_network["ssid"], "Second page network")

    def test_a_failed_page_logs_geometry_and_has_a_working_home_button(self):
        controller, typer, _socket = self._network_on_loop()
        self._declare_oversized_network_page(controller)

        with self.assertLogs(level="WARNING") as logs:
            self._tap_on_loop(controller, typer, "nav.network", FEATHER.ScreenPage.NETWORK_HOME)
            self.assertTrue(wait_for(lambda: controller._process_touch_events(controller.reactor.now)
                                    or controller._current_dialog() == FEATHER.ScreenDialog.RENDER_ERROR))
        logged = "\n".join(logs.output)
        for value in ("IDLE_HOME", "NETWORK_HOME", "layout", "oversized", "900x130", "800"):
            self.assertIn(value, logged)
        self.assertTrue(typer.drew("UNABLE TO DISPLAY PAGE"))
        self.assertTrue(typer.drew("HOME"))
        home = dialog_ui.RENDER_HOME.wire_id
        self.assertIn(home, controller.renderer.shown_actions())
        controller.reactor.now += 1
        typer.tell("tap %s" % typer.wire(home))
        self.assertTrue(wait_for(lambda: controller._process_touch_events(controller.reactor.now)
                                or controller._current_dialog() is None))
        self.assertEqual(controller.page, FEATHER.ScreenPage.IDLE_HOME)
        self.assertTrue(wait_for(lambda: "nav.network" in controller.renderer.shown_actions()))
        self.assertEqual(controller.renderer.get_status()["typer_restarts"], 0)

    @staticmethod
    def _declare_oversized_network_page(controller):
        fill = Fill(ThemeColor.PRIMARY, key="oversized")
        oversized = PageTree(fill, Rect(0, 56, 800, 386), page_id=_Pages.PYTHON_RENDER)
        fill.size(900, 130)
        controller._network_page_content = lambda page: (oversized, "Network", True)
        return fill

    def test_a_failed_frame_cannot_recover_over_a_newer_page(self):
        controller, typer, _socket = self._network_on_loop()
        self._declare_oversized_network_page(controller)
        controller._open_network_page()
        event_fd = controller.renderer.event_fd
        self.assertTrue(select.select([event_fd], [], [], 5)[0])

        controller._show_page(FEATHER.ScreenPage.IDLE_HOME)
        with self.assertLogs(level="ERROR") as logs:
            controller._process_touch_events(controller.reactor.now)

        self.assertIn("superseded frame failed", logs.output[0])
        self.assertIn("oversized", logs.output[0])
        self.assertIsNone(controller._current_dialog())
        self.assertTrue(typer.drew("192.0.2.14"))

    def test_a_failed_frame_cannot_recover_over_a_good_frame_of_the_same_page(self):
        controller, typer, _socket = self._network_on_loop()
        fill = self._declare_oversized_network_page(controller)
        controller._open_network_page()
        self.assertTrue(select.select([controller.renderer.event_fd], [], [], 5)[0])
        generation = controller.renderer.generation

        frames = controller._screen_root.frames
        frames.begin()
        fill.size(100, 130)
        frames.receipt("repaired")
        frames.end(controller.renderer)
        self.assertTrue(wait_for(lambda: any(entry.get("token") == "repaired" for entry in typer.entries())))
        self.assertTrue(wait_for(lambda: controller.renderer.get_status()["in_flight"] == 0))
        self.assertEqual(controller.renderer.generation, generation)
        with self.assertLogs(level="ERROR") as logs:
            controller._process_touch_events(controller.reactor.now)

        self.assertIn("superseded frame failed", logs.output[0])
        self.assertIn("oversized", logs.output[0])
        self.assertEqual(controller.renderer.get_status()["screen_errors"], 1)
        self.assertEqual(controller.page, FEATHER.ScreenPage.NETWORK_HOME)
        self.assertIsNone(controller._current_dialog())
        self.assertIn("nav.back", controller.renderer.shown_actions())

    def test_a_queued_ok_tap_cannot_dismiss_the_recovery_dialog(self):
        controller, typer, _socket = self._network_on_loop()
        controller._show_message("Previous dialog", FEATHER.ScreenPage.IDLE_HOME)
        self.assertTrue(typer.drew("Previous dialog"))
        typer.tell("tap %s" % typer.wire(dialog_ui.OK.wire_id))
        self.assertTrue(select.select([controller.renderer.event_fd], [], [], 5)[0])

        self._declare_oversized_network_page(controller)
        controller._open_network_page()
        controller.renderer.request_presented_receipt("after-failure")
        self.assertTrue(wait_for(lambda: any(entry.get("token") == "after-failure" for entry in typer.entries())))
        with self.assertLogs(level="ERROR"):
            controller._process_touch_events(controller.reactor.now)

        self.assertEqual(controller._current_dialog(), FEATHER.ScreenDialog.RENDER_ERROR)
        self.assertTrue(typer.drew("UNABLE TO DISPLAY PAGE"))

    def test_the_loop_draws_home_and_its_updates_without_the_reactor(self):
        controller = home_controller()
        typer = TyperLoop(controller.renderer)
        self.addCleanup(typer.close)
        typer.start()

        with self.assertNoLogs(level="WARNING"):
            controller._render_screen()
            root = controller._screen_root
            self.assertTrue(root.frames.attached)
            self.assertTrue(typer.drew("192.168.2.124"))
            controller.extruder.status["temperature"] = 187.0
            controller._render_screen()
            self.assertTrue(typer.drew("187"))
            typer.take(lambda events: False, timeout=0.2)

        shown = shown_actions(root)
        for action in ("nav.menu", "nav.heat", "nav.network", "nav.move"):
            self.assertIn(action, shown)

    def test_the_ui_test_taps_the_buttons_the_loop_drew(self):
        controller = home_controller()
        typer = TyperLoop(controller.renderer)
        self.addCleanup(typer.close)
        typer.start()
        controller._render_screen()
        self.assertTrue(typer.drew("192.168.2.124"))
        typer.take(lambda events: False, timeout=0.2)
        tapped = []
        controller._start_touch_action = tapped.append
        run = UI_TEST.UITestRun(controller)

        run._tap("nav.menu")

        self.assertEqual(tapped, ["nav.menu"])
        # Home shows no back button.
        with self.assertRaisesRegex(RuntimeError, "absent"):
            run._tap("nav.back")
        self.assertEqual(tapped, ["nav.menu"])

    def test_the_loop_draws_the_heat_page(self):
        controller = home_controller()
        controller.page = FEATHER.ScreenPage.CONTROL_HEAT
        controller.heating_materials = ()
        controller._last_heat = None
        controller.fan = None
        typer = TyperLoop(controller.renderer)
        self.addCleanup(typer.close)
        typer.start()

        with self.assertNoLogs(level="WARNING"):
            controller._render_screen()
            controller.extruder.status["target"] = 215.0
            controller._render_screen()
            self.assertTrue(typer.drew("215"))
            typer.take(lambda events: False, timeout=0.2)

    def test_the_ui_test_toast_is_gone_before_the_next_capture(self):
        from feather_ui_test import runner as UI_TEST

        controller = home_controller()
        controller.print_state = PrintState.IDLE
        typer = TyperLoop(controller.renderer)
        self.addCleanup(typer.close)
        typer.start()
        steps = {step["label"]: step for step in
                 UI_TEST.ScenarioCatalog(UI_TEST.UITestRun(controller)).build_steps("UI") if step["kind"] == "call"}
        dismiss = DismissToast().wire_id

        controller._render_screen()
        root = controller._screen_root
        self.assertTrue(typer.drew("192.168.2.124"))
        steps["ui-toast"]["callback"]()
        self.assertTrue(typer.drew("SETTINGS UPDATED"))
        self.assertTrue(wait_for(lambda: dismiss in shown_actions(root)))

        previous_frames = len(typer.history())
        steps["ui-toast-hide"]["callback"]()
        self.assertTrue(wait_for(lambda: dismiss not in shown_actions(root)))

        self.assertTrue(typer.drew("192.168.2.124", after=previous_frames,
                                  without=("SETTINGS UPDATED",)))

    def test_a_toast_shows_over_home_until_it_expires_or_is_tapped(self):
        controller = home_controller()
        controller.print_state = PrintState.IDLE
        controller.last_action_time = controller.last_touch_time = 0
        controller.pending_action, controller.dimmed = None, False
        typer = TyperLoop(controller.renderer)
        self.addCleanup(typer.close)
        typer.start()
        dismiss = DismissToast().wire_id

        with self.assertNoLogs(level="WARNING"):
            controller._render_screen()
            root = controller._screen_root
            self.assertTrue(typer.drew("192.168.2.124"))
            for hide in ("expiry", "tap", "page"):
                with self.subTest(hide=hide):
                    previous_frames = len(typer.history())
                    controller._toast("Load filament")
                    self.assertTrue(typer.drew("LOAD FILAMENT", after=previous_frames))
                    # A new toast takes the old one's place.
                    previous_frames = len(typer.history())
                    controller._toast("Fan: 50%")
                    self.assertTrue(typer.drew("FAN: 50%", after=previous_frames,
                                              without=("LOAD FILAMENT",)))
                    self.assertTrue(wait_for(lambda: dismiss in shown_actions(root)))
                    # The page's own update keeps the toast over it.
                    previous_frames = len(typer.history())
                    controller.extruder.status["temperature"] += 1.0
                    controller._render_screen()
                    self.assertTrue(typer.drew("%.0f" % controller.extruder.status["temperature"],
                                              after=previous_frames))
                    typer.take(lambda events: False, timeout=0.1)
                    self.assertIn(dismiss, shown_actions(root))

                    previous_frames = len(typer.history())
                    if hide == "expiry":
                        controller.reactor.now = controller.toast_until
                        controller._hide_toast()
                    elif hide == "page":
                        controller._show_page(FEATHER.ScreenPage.IDLE_HOME)
                    else:
                        controller.reactor.now += 1
                        typer.tell("tap %s" % typer.wire(dismiss))
                        self.assertTrue(wait_for(lambda: controller._process_touch_events(controller.reactor.now)
                                                 or not controller.toast_message))
                    self.assertTrue(wait_for(lambda: dismiss not in shown_actions(root)))
                    self.assertTrue(typer.drew("192.168.2.124", after=previous_frames,
                                              without=("FAN: 50%",)))
                    self.assertEqual(controller.page, FEATHER.ScreenPage.IDLE_HOME)

    def test_the_loop_draws_the_filament_material_page(self):
        controller = home_controller()
        controller.page = FEATHER.ScreenPage.FILAMENT_MATERIAL
        controller.feature_manager = FEATHER.LazyFeatureManager(controller, FEATHER.FEATURE_SPECS)
        controller.heating_materials = ("PLA", "PETG")
        controller.heating_profiles = {"PLA": (210, 60), "PETG": (240, 80)}
        controller.extruder.heater = controller.heater_bed
        typer = TyperLoop(controller.renderer)
        self.addCleanup(typer.close)
        typer.start()
        with self.assertNoLogs(level="WARNING"):
            controller._render_screen()
            # Each material's button with its preheat target under the name.
            self.assertTrue(typer.drew("PETG"))
            self.assertTrue(typer.drew("240C"))
            typer.take(lambda events: False, timeout=0.2)
        shown = shown_actions(controller._screen_root)
        self.assertIn("nav.back", shown)
        self.assertEqual(len([action for action in shown if action.startswith("filament.select.")]), 2)

    def test_the_loop_draws_the_filament_action_page_and_its_updates(self):
        controller = home_controller()
        controller.page = FEATHER.ScreenPage.FILAMENT_ACTION
        controller.feature_manager = FEATHER.LazyFeatureManager(controller, FEATHER.FEATURE_SPECS)
        controller.filament_material = "PETG"
        controller.filament_from_pause = False
        controller.extruder = StatusObject({"temperature": 200.0, "target": 240.0})
        controller.extruder.min_extrude_temp = 170.0
        typer = TyperLoop(controller.renderer)
        self.addCleanup(typer.close)
        typer.start()
        with self.assertNoLogs(level="WARNING"):
            controller._render_screen()
            self.assertTrue(typer.drew("HEATING"))
            self.assertTrue(typer.drew("FEED FILAMENT"))
            # The material row and the new temperature, written by the feature's update.
            controller.extruder.status["temperature"] = 239.0
            controller.feature_manager.get("filament").update(controller.reactor.monotonic())
            self.assertTrue(typer.drew("239 / 240C"))
            self.assertTrue(typer.drew("READY"))
            typer.take(lambda events: False, timeout=0.2)
        shown = shown_actions(controller._screen_root)
        self.assertIn("nav.back", shown)
        self.assertEqual(len([action for action in shown if action.startswith(("filament.load", "filament.purge"))]), 2)

    def _z_controller(self, page):
        """A home controller on a Z calibration page with a measured zone, over the loop."""
        controller = home_controller()
        controller.page = page
        controller.gcode = GCodeRecorder()
        controller.feature_manager = FEATHER.LazyFeatureManager(controller, FEATHER.FEATURE_SPECS)
        feature = controller.feature_manager.get("z")
        typer = TyperLoop(controller.renderer)
        self.addCleanup(typer.close)
        typer.start()
        return controller, feature, typer

    def test_the_loop_draws_the_z_summary_and_its_exit_dialog(self):
        controller, feature, typer = self._z_controller(FEATHER.ScreenPage.Z_OFFSET_SUMMARY)
        session = feature.z_calibration
        session.begin(0.2, None, "", -0.25, True)
        session.choose_zone("center")
        session.set_trigger(-0.5)
        session.accept()
        zones = {action.wire_id for action in Z_ACTIONS.ZONE_ACTIONS.values()}
        with self.assertNoLogs(level="WARNING"):
            controller._render_screen()
            # A zone's caption derived from the results the page's state holds.
            self.assertTrue(typer.drew("CENTER  +0.250"))
            typer.take(lambda events: False, timeout=0.2)
            self.assertTrue(zones <= shown_actions(controller._screen_root))

            feature.back(controller.page)
            self.assertTrue(typer.drew("EXIT Z CALIBRATION?"))
            typer.take(lambda events: False, timeout=0.2)
            shown = shown_actions(controller._screen_root)
            # The modal dialog covers the zones and the header's back; its buttons and wake take taps.
            self.assertTrue({Z_ACTIONS.SAVE.wire_id, Z_ACTIONS.DISCARD_CONFIRM.wire_id, "global.wake"} <= shown)
            self.assertFalse(zones & shown)
            self.assertNotIn("nav.back", shown)

            feature.handle_semantic_action(controller.page, SetValue(Z_OFFSET_PAGES.SummaryState.DIALOG, None))
            typer.take(lambda events: False, timeout=0.2)
        shown = shown_actions(controller._screen_root)
        self.assertTrue(zones | {"nav.back"} <= shown)
        self.assertNotIn(Z_ACTIONS.DISCARD_CONFIRM.wire_id, shown)

    def test_the_loop_draws_the_paper_test_its_gauge_and_the_pressure_dialog_with_abort(self):
        controller, feature, typer = self._z_controller(FEATHER.ScreenPage.Z_OFFSET_PAPER)
        controller.weight_sensor = StatusObject({"temperature": 20.0})
        controller.renderer.set_header_action("global.abort", "ABORT")
        session = feature.z_calibration
        session.begin(0.2, None, "", -0.25, False)
        session.choose_zone("center")
        with self.assertNoLogs(level="WARNING"):
            controller._render_screen()
            self.assertTrue(typer.drew("LOAD"))
            typer.take(lambda events: False, timeout=0.2)
            self.assertIn(Z_ACTIONS.MOVE_SAFE_HALF.wire_id, shown_actions(controller._screen_root))

            # A new weight, read by the feature's update: the gauge is one of the page's values.
            controller.weight_sensor.status["temperature"] = 150.0
            feature.update(controller.reactor.monotonic())
            self.assertTrue(typer.drew("150"))

            controller.weight_sensor.status["temperature"] = 1000.0
            feature.update(controller.reactor.monotonic())
            self.assertTrue(typer.drew("TOO MUCH PRESSURE"))
            typer.take(lambda events: False, timeout=0.2)
        shown = shown_actions(controller._screen_root)
        # The pressure dialog covers the page's controls and keeps the header's ABORT.
        self.assertTrue({"global.abort", "global.wake"} <= shown)
        self.assertNotIn(Z_ACTIONS.MOVE_SAFE_HALF.wire_id, shown)

    def test_the_loop_draws_the_printing_page_and_its_ready_preview(self):
        from tests.test_feather_print_preview import DeferredPreviewWorker, PreviewHelper, _controller, _write_gcode
        with PreviewHelper():
            path = _write_gcode()
            self.addCleanup(os.unlink, path)
            worker = DeferredPreviewWorker()
            controller = _controller(path, worker)
            controller.renderer = FEATHER.FeatherRenderer()
            typer = TyperLoop(controller.renderer)
            self.addCleanup(typer.close)
            typer.start()

            with self.assertNoLogs(level="WARNING"):
                controller._render_screen()
                self.assertTrue(typer.drew("PREVIEW"))
                # The loop arranged the page: what follows still finds the preview's geometry.
                controller._render_screen()
                controller.reactor.flush_callbacks()
                worker.finish(0)
                self.assertTrue(typer.drew("--batch image "))
                typer.take(lambda events: False, timeout=0.2)

        self.assertIn("print.cancel", shown_actions(controller._screen_root))

    def test_the_loop_draws_more_over_the_printing_page_with_abort(self):
        from tests.test_feather_print_preview import DeferredPreviewWorker, PreviewHelper, _controller, _write_gcode
        with PreviewHelper():
            path = _write_gcode()
            self.addCleanup(os.unlink, path)
            controller = _controller(path, DeferredPreviewWorker())
            controller.renderer = FEATHER.FeatherRenderer()
            # As the fixture's renderer: a print in progress, the header shows ABORT.
            controller.renderer.set_header_action("global.abort", "ABORT")
            typer = TyperLoop(controller.renderer)
            self.addCleanup(typer.close)
            typer.start()

            with self.assertNoLogs(level="WARNING"):
                controller._render_screen()
                self.assertTrue(typer.drew("PREVIEW"))
                controller._open_print_more()
                self.assertTrue(typer.drew("Change Filament"))
                typer.take(lambda events: False, timeout=0.2)
            shown = shown_actions(controller._screen_root)
            # Over the printing page, whose controls it covers, with the header's ABORT kept.
            self.assertTrue({"more.close", "more.filament", "more.exclude", "global.abort"} <= set(shown))
            self.assertNotIn("print.cancel", shown)
            controller._handle_more_action("more.close")
            typer.take(lambda events: False, timeout=0.2)
            self.assertIn("print.cancel", shown_actions(controller._screen_root))

    def _component_run(self):
        """A UI test run on a home controller over the loop: the component suite's pages are shown there."""
        controller = home_controller()
        controller.feature_manager = FEATHER.LazyFeatureManager(controller, FEATHER.FEATURE_SPECS)
        typer = TyperLoop(controller.renderer)
        self.addCleanup(typer.close)
        typer.start()
        controller._render_screen()
        self.assertTrue(typer.drew("192.168.2.124"))
        typer.take(lambda events: False, timeout=0.2)
        feature = controller.feature_manager.get("ui_test")
        feature.current_run = run = UI_TEST.UITestRun(controller)
        run.renderer_baseline = controller.renderer.get_status()
        return controller, typer, run

    @staticmethod
    def _component_page(key):
        return next(page for page in UI_TEST.ScenarioCatalog._component_pages() if page.page_id.endswith(key))

    def test_a_message_of_any_text_draws_on_the_loop(self):
        for text in ("Сеть «Домашняя» недоступна", "プリンタ 打印机", "Hot 🔥 end\t100%", 'quote " and \\ end',
                     "caf\u00e9 \ufffd\u200b \x01 line", "x" * 300):
            with self.subTest(text=text):
                controller = home_controller()
                typer = TyperLoop(controller.renderer)
                self.addCleanup(typer.close)
                typer.start()
                controller._render_screen()
                self.assertTrue(typer.drew("192.168.2.124"))
                with self.assertNoLogs(level="WARNING"):
                    controller._show_message(text, FEATHER.ScreenPage.IDLE_HOME, title=text)
                    self.assertTrue(typer.drew("OK"))
                    typer.take(lambda events: False, timeout=0.05)
                self.assertIn("message.ok", shown_actions(controller._screen_root))

    def _on_loop(self, controller):
        typer = TyperLoop(controller.renderer)
        self.addCleanup(typer.close)
        typer.start()
        controller._render_screen()
        self.assertTrue(typer.drew("192.168.2.124"))
        return typer

    def _tap(self, controller, action):
        controller.print_state = FEATHER.PrintState.IDLE
        controller.pending_action = controller.busy_message = None
        controller.last_action_time = float("-inf")
        controller._dispatch_action(action)

    def test_the_loop_turns_a_messages_pages_and_shows_home_again_when_it_closes(self):
        controller = home_controller()
        typer = self._on_loop(controller)
        lines = "\n".join("Row number %02d" % index for index in range(30))

        with self.assertNoLogs(level="WARNING"):
            controller._show_message(lines, FEATHER.ScreenPage.IDLE_HOME, title="Long notice")
            self.assertTrue(typer.drew("LONG NOTICE"))
            self.assertTrue(typer.drew("Row number 00"))
            self.assertFalse(typer.drew("Row number 29"))
            shown = shown_actions(controller._screen_root)
            self.assertIn(dialog_ui.NEXT_PAGE.wire_id, shown)
            self.assertNotIn("nav.heat", shown)
            for _ in range(10):
                if dialog_ui.NEXT_PAGE.wire_id not in shown_actions(controller._screen_root):
                    break
                self._tap(controller, dialog_ui.NEXT_PAGE.wire_id)
                typer.take(lambda events: False, timeout=0.05)
            self.assertTrue(typer.drew("Row number 29"))
            self.assertIn(dialog_ui.PREVIOUS_PAGE.wire_id, shown_actions(controller._screen_root))
            self._tap(controller, "message.ok")
            typer.take(lambda events: False, timeout=0.05)

        self.assertIsNone(controller._current_dialog())
        self.assertIn("nav.heat", shown_actions(controller._screen_root))

    def test_a_frozen_error_turns_its_pages_on_the_loop(self):
        controller = home_controller()
        controller.touch_available = True
        typer = self._on_loop(controller)
        message = "Сбой МК: " + " ".join("причина %02d" % index for index in range(120))

        with self.assertNoLogs(level="WARNING"):
            controller._show_terminal_error(message, "shutdown", "firmware_restart")
            self.assertTrue(typer.drew("причина 00"))
            self.assertTrue(controller.renderer.output_frozen)
            self.assertIn(dialog_ui.NEXT_PAGE.wire_id, shown_actions(controller._screen_root))
            self._tap(controller, dialog_ui.NEXT_PAGE.wire_id)
            typer.take(lambda events: False, timeout=0.05)

        self.assertTrue(controller.renderer.output_frozen)
        self.assertIn(dialog_ui.PREVIOUS_PAGE.wire_id, shown_actions(controller._screen_root))
        self.assertIn("error.firmware_restart", shown_actions(controller._screen_root))

    def test_the_loop_draws_a_klipper_prompt_and_its_buttons_run_their_gcode(self):
        controller = home_controller()
        controller._prompt_draft = None
        controller.recovery_action = controller.resurrection = None
        commands = []
        controller._run_script = commands.append
        typer = self._on_loop(controller)

        with self.assertNoLogs(level="WARNING"):
            controller._handle_gcode_output("\n".join((
                "// action:prompt_begin Выбор материала",
                "// action:prompt_text Выберите профиль",
                "// action:prompt_button_group_start",
                "// action:prompt_button PLA|SET_MATERIAL MATERIAL=PLA|primary",
                "// action:prompt_button PETG|SET_MATERIAL MATERIAL=PETG|warning",
                "// action:prompt_button_group_end",
                "// action:prompt_footer_button Cancel|RESPOND TYPE=command MSG=action:prompt_end|secondary",
                "// action:prompt_show")))
            self.assertTrue(typer.drew("ВЫБОР МАТЕРИАЛА"))
            self.assertTrue(typer.drew("PETG"))
            typer.take(lambda events: False, timeout=0.05)
            self._tap(controller, prompt_wire(controller, 1))

        self.assertEqual(commands, ["SET_MATERIAL MATERIAL=PETG"])
        shown = shown_actions(controller._screen_root)
        self.assertTrue({prompt_wire(controller, index) for index in range(3)} <= shown)
        self.assertNotIn("nav.heat", shown)

    def test_the_loop_draws_a_status_prompt_and_its_live_values(self):
        controller = home_controller()
        controller._prompt_draft = None
        controller.recovery_action = controller.resurrection = None
        operation = dict(controller._operation_context_status(), current_state="heating")
        controller._operation_context_status = lambda eventtime=None: operation
        cancels = []
        controller._open_operation_cancel = cancels.append
        typer = self._on_loop(controller)

        with self.assertNoLogs(level="WARNING"):
            controller._handle_gcode_output("\n".join((
                "// action:prompt_begin Cold Pull", "// action:prompt_feather_kind cold_pull",
                "// action:prompt_show")))
            self.assertTrue(typer.drew("HEATING"))
            self.assertTrue(typer.drew("NOZZLE 20 / 0 C"))
            controller.extruder.status["temperature"] = 150.0
            refresh_prompt(controller)
            self.assertTrue(typer.drew("NOZZLE 150 / 0 C"))
            operation["cancel_available"] = True
            refresh_prompt(controller)
            self.assertTrue(typer.drew("CANCEL"))
            typer.take(lambda events: False, timeout=0.05)
            self._tap(controller, "coldpull.cancel")
            operation["cancel_pending"] = True
            refresh_prompt(controller)
            self.assertTrue(typer.drew("CANCELLING..."))
            typer.take(lambda events: False, timeout=0.05)

        self.assertEqual(cancels, [controller.page])
        self.assertNotIn("coldpull.cancel", touchable_actions(controller.renderer))

    def test_the_loop_draws_the_loader_and_turns_it_by_a_state_write(self):
        controller = home_controller()
        controller.renderer.set_header_action("global.abort", "ABORT")
        typer = self._on_loop(controller)

        with self.assertNoLogs(level="WARNING"):
            controller.busy_message, controller.busy_phase = "HOMING...", 0
            controller._render_screen()
            self.assertTrue(typer.drew("HOMING..."))
            self.assertTrue(typer.drew("OPERATION IN PROGRESS"))
            typer.take(lambda events: False, timeout=0.05)
            self.assertEqual(touchable_actions(controller.renderer), {"global.abort"})

            shown = len(typer.history())
            controller.busy_phase = 1
            controller._ensure_screen_root().paint()
            self.assertTrue(typer.drew("-p 338 280 -s 32 12", after=shown))
            turn = "\n".join(str(command) for batch in typer.history()[shown:] for command in batch)
            self.assertIn("-p 338 280 -s 32 12", turn)
            self.assertNotIn("HOMING", turn)

            controller.busy_message = None
            controller._render_screen()
            typer.take(lambda events: False, timeout=0.05)

        self.assertIn("nav.heat", shown_actions(controller._screen_root))

    def test_the_loop_draws_the_startup_screen_over_the_footers_strip(self):
        controller = home_controller()
        typer = self._on_loop(controller)
        controller.renderer.footer("LEFT TEXT", "RIGHT TEXT")
        controller._render_screen()
        self.assertTrue(typer.drew("LEFT TEXT"))
        typer.take(lambda events: False, timeout=0.05)

        with self.assertNoLogs(level="WARNING"):
            controller.print_state = FEATHER.PrintState.INACTIVE
            controller.startup_phase, controller.startup_restarting = 0, False
            controller._render_screen()
            self.assertTrue(typer.drew("INITIALIZING KLIPPER"))
            typer.take(lambda events: False, timeout=0.05)
            self.assertEqual(touchable_actions(controller.renderer), set())

            shown = len(typer.history())
            controller.startup_phase = 2
            controller._ensure_screen_root().paint()
            self.assertTrue(typer.drew("-p 384 232 -s 33 1", after=shown))
            pulse = "\n".join(str(command) for batch in typer.history()[shown:] for command in batch)
            self.assertIn("-p 384 232 -s 33 1", pulse)
            self.assertNotIn("-s 800", pulse)

            controller.renderer.footer("NEW LEFT", "NEW RIGHT")
            typer.take(lambda events: False, timeout=0.05)
            self.assertNotIn("NEW LEFT", str(typer.history()))

            controller.print_state = FEATHER.PrintState.IDLE
            controller._render_screen()
            self.assertTrue(typer.drew("NEW LEFT"))

    def test_a_pending_restart_shows_the_startup_screen_whatever_the_print_state(self):
        controller = home_controller()
        typer = self._on_loop(controller)
        controller.print_state, controller.restart_pending = FEATHER.PrintState.IDLE, True
        controller.startup_phase, controller.startup_restarting = 0, True

        with self.assertNoLogs(level="WARNING"):
            controller._render_screen()
            self.assertTrue(typer.drew("RESTART IN PROGRESS - DISPLAY MAY PAUSE"))
            typer.take(lambda events: False, timeout=0.05)

        self.assertEqual(touchable_actions(controller.renderer), set())

    def test_a_scan_shows_only_on_the_file_browser(self):
        controller = home_controller()
        typer = self._on_loop(controller)
        controller.file_scan_loading, controller.file_scan_source, controller.file_source = True, "usb", "usb"
        shown = len(typer.history())

        with self.assertNoLogs(level="WARNING"):
            controller._render_screen()
            typer.take(lambda events: False, timeout=0.05)

        self.assertNotIn("OPERATION IN PROGRESS", str(typer.history()[shown:]))
        self.assertIn("nav.heat", shown_actions(controller._screen_root))

    def test_a_loader_holds_the_touch_warning_back_even_with_its_header_action(self):
        controller = home_controller()
        controller.touch_available = True
        controller.system_shutdown_active = False
        controller.renderer.set_header_action("global.abort", "ABORT")
        typer = self._on_loop(controller)
        controller.busy_message, controller.busy_phase = "HOMING", 0

        with self.assertNoLogs(level="WARNING"):
            controller._render_screen()
            self.assertTrue(typer.drew("OPERATION IN PROGRESS"))
            controller._handle_touch_device_status(False)
            typer.take(lambda events: False, timeout=0.05)

        self.assertIsNone(controller._find_dialog(FEATHER.ScreenDialog.TOUCH_UNAVAILABLE))
        self.assertEqual(touchable_actions(controller.renderer), {"global.abort"})

    def test_the_loop_draws_the_applying_screen(self):
        controller = home_controller()
        typer = self._on_loop(controller)
        applying = state_ui.applying_page()
        controller._screen_root._select_content = lambda: (applying, None, False)

        with self.assertNoLogs(level="WARNING"):
            controller._render_screen()
            self.assertTrue(typer.drew("APPLYING CHANGES"))
            typer.take(lambda events: False, timeout=0.05)

        self.assertEqual(touchable_actions(controller.renderer), set())

    def test_the_loop_draws_the_touch_warning_and_nothing_on_it_takes_input(self):
        controller = home_controller()
        controller.touch_available = True
        controller.system_shutdown_active = False
        controller.renderer.set_header_action("global.abort", "ABORT")
        typer = self._on_loop(controller)

        with self.assertNoLogs(level="WARNING"):
            controller._handle_touch_device_status(False)
            self.assertTrue(typer.drew("TOUCH INPUT UNAVAILABLE"))
            self.assertTrue(typer.drew("WAITING FOR AUTOMATIC RECONNECTION"))
            typer.take(lambda events: False, timeout=0.05)
            self.assertEqual(touchable_actions(controller.renderer), set())
            controller._handle_touch_device_status(True)
            typer.take(lambda events: False, timeout=0.05)

        self.assertIn("nav.heat", shown_actions(controller._screen_root))

    def test_a_message_over_an_imperative_page_keeps_the_header_action(self):
        controller = home_controller()
        controller.page = FEATHER.ScreenPage.MAIN_MENU
        controller.renderer.set_header_action("global.abort", "ABORT")
        controller._paint_page = lambda: controller.renderer.send(
            controller.renderer.begin_page("Menu") + controller.renderer.button(
                "nav.heat", 220, 300, 360, 100, "HEAT"))
        typer = TyperLoop(controller.renderer)
        self.addCleanup(typer.close)
        typer.start()
        controller._render_screen()

        with self.assertNoLogs(level="WARNING"):
            controller._show_message("Over the menu", FEATHER.ScreenPage.MAIN_MENU)
            self.assertTrue(typer.drew("Over the menu"))
            typer.take(lambda events: False, timeout=0.05)

        shown = shown_actions(controller._screen_root)
        self.assertTrue({"message.ok", "global.abort"} <= shown)
        self.assertNotIn("nav.heat", shown)

    def test_the_component_suite_shows_a_page_as_the_screens_content(self):
        controller, typer, run = self._component_run()
        page = self._component_page(".SAFE_Z_BRIEFING")

        with self.assertNoLogs(level="WARNING"):
            run.scenarios._render_component_default(page)
            # The page under its title, which the suite takes from its key.
            self.assertTrue(typer.drew("Z OFFSET / SAFE BRIEFING"))
            typer.take(lambda events: False, timeout=0.2)

        self.assertEqual(controller.page, FEATHER.ScreenPage.UI_TEST_PREVIEW)
        self.assertTrue(set(page.actions) <= shown_actions(controller._screen_root))
        # A page that declares its title is shown under it.
        with self.assertNoLogs(level="WARNING"):
            run.scenarios._render_component_default(self._component_page(".SHAPER_START"))
            self.assertTrue(typer.drew("INPUT SHAPER"))
            typer.take(lambda events: False, timeout=0.2)
        self.assertEqual(run._health_failures(), [])

    def test_a_component_case_shows_its_state_over_the_pages_defaults(self):
        from ff5m_ui.z_offset.safe_briefing.page import SafeBriefingState
        controller, typer, run = self._component_run()
        page = self._component_page(".SAFE_Z_BRIEFING")

        run.scenarios._render_component_case({"page": page, "state": {SafeBriefingState.CURRENT: 12.5}})
        self.assertTrue(typer.drew("SAVED SAFE Z: 12.5 MM"))
        run.scenarios._render_component_default(page)
        self.assertTrue(typer.drew("SAVED SAFE Z: 10.0 MM"))

    def test_the_ui_test_shows_the_calibration_catalogs_next_page(self):
        controller, typer, run = self._component_run()
        feature = controller.feature_manager.get("calibration")
        controller._show_page(FEATHER.ScreenPage.CALIBRATION_HOME)
        self.assertTrue(typer.drew("BED SCREWS"))

        run.scenarios._render_calibration_variants()

        self.assertEqual(feature.calibration_page, 1)
        self.assertTrue(typer.drew("EXTRUDER"))
        feature.calibration_page = 2  # the last page has no next one
        controller._show_page(FEATHER.ScreenPage.CALIBRATION_HOME)
        self.assertTrue(typer.drew("HOTEND PID"))
        with self.assertRaisesRegex(RuntimeError, "no next page"):
            run.scenarios._render_calibration_variants()

    def test_the_ui_test_finds_the_mesh_results_discard_the_loop_drew(self):
        controller, typer, run = self._component_run()
        feature = controller.feature_manager.get("calibration")
        feature.calibration_kind, feature.calibration_error, feature.calibration_cancelled = "mesh", None, False
        feature.calibration_mesh = [[0.01, -0.02, 0.03], [0.0, 0.015, -0.01]]
        controller._show_page(FEATHER.ScreenPage.CALIBRATION_RESULT)
        self.assertTrue(typer.drew("DON'T SAVE"))

        run.scenarios._validate_mesh()

        self.assertEqual(run.scenarios.test_results["mesh"], feature.calibration_mesh)

    def test_the_screw_results_draw_on_the_loop_with_their_python_graphic(self):
        controller, typer, _run = self._component_run()
        feature = controller.feature_manager.get("calibration")
        feature.calibration_kind, feature.calibration_error, feature.calibration_cancelled = "screws", None, False
        feature.calibration_results = [
            dict(name=corner + " screw", direction="CW", turns="00:05")
            for corner in ("rear left", "rear right", "front left", "front right")]
        controller.page = FEATHER.ScreenPage.CALIBRATION_RESULT

        with self.assertNoLogs(level="WARNING"):
            controller._render_screen()
            self.assertTrue(typer.drew("ADJUST FROM BELOW"))
            typer.take(lambda events: False, timeout=0.2)
            feature.calibration_results[0] = dict(name="rear left screw", direction="CCW", turns="00:45")
            controller._render_screen()
            self.assertTrue(typer.drew("00:45"))
            typer.take(lambda events: False, timeout=0.2)
        self.assertEqual(controller.renderer.get_status()["screen_errors"], 0)
        self.assertIn("cal.done", controller.renderer.shown_actions())

    def test_the_benchmarks_run_on_the_loop(self):
        for page, label in ((FEATHER.ScreenPage.RENDER_BENCHMARK, "Click to switch"),
                            (FEATHER.ScreenPage.COMPONENT_BENCHMARK, "RENDER FPS")):
            with self.subTest(page=page):
                controller = home_controller()
                controller.reactor = _Clock()
                controller.print_state = PrintState.IDLE
                controller.feature_manager = FEATHER.LazyFeatureManager(controller, FEATHER.FEATURE_SPECS)
                typer = TyperLoop(controller.renderer)
                self.addCleanup(typer.close)
                typer.start()
                controller._render_screen()
                self.assertTrue(typer.drew("192.168.2.124"))
                feature = controller.feature_manager.get("benchmark")
                controller.renderer.set_sent_handler(feature.on_render_sent)
                delivered = []

                def deliver(events):
                    for event in events[len(delivered):]:
                        delivered.append(event)
                        if isinstance(event, RenderReceipt):
                            feature.on_render_receipt(event, time.monotonic())
                    return feature.get_status()["completed_frames"] >= 5

                with self.assertNoLogs(level="WARNING"):
                    controller.page = page
                    controller._render_screen()
                    self.assertTrue(typer.drew(label))
                    feature._tick(time.monotonic())
                    typer.take(deliver)

                self.assertGreaterEqual(feature.get_status()["completed_frames"], 5)
                self.assertFalse(controller.renderer.synced)
                self.assertEqual(controller.renderer.get_status()["screen_errors"], 0)
                self.assertIn(NEXT_MODE, {feature.resolve_semantic_action(page, action)
                                          for action in controller.renderer.shown_actions()})

    def test_the_shaper_pages_draw_on_the_loop(self):
        with open(os.path.join(os.path.dirname(__file__), "fixtures", "shaper_reference.json")) as source:
            results = {"x": json.load(source)["cases"][0]}
        native = SimpleNamespace(
            state="complete", busy=False, revision=2, job=SimpleNamespace(id=3), error="", reference=None,
            record=SimpleNamespace(id="latest", saved_at=1., results=results),
            get_history=lambda: [dict(id="latest", saved_at=1., axes=["x"], latest=True)],
            get_status=lambda _eventtime: dict(
                job_id=4, state="measuring", axis="x", requested_axes=("x",), completed_axes=(), frequency=43.,
                freq_start=5., freq_end=100., samples=32000, elapsed=50., cancel_requested=False))
        controller, typer, _run = self._component_run()
        controller.printer = SimpleNamespace(lookup_object=lambda name, default=None: (
            native if name == "forge_shaper" else default))
        feature = controller.feature_manager.get("calibration")
        feature.calibration_kind, feature.calibration_error, feature.calibration_cancelled = "shaper", None, False
        session = feature._active_shaper()
        result_page = FEATHER.ScreenPage.CALIBRATION_RESULT

        with self.assertNoLogs(level="WARNING"):
            controller.page = FEATHER.ScreenPage.CALIBRATION_CONFIRM
            controller._render_screen()
            self.assertTrue(typer.drew("START CALIBRATION"))
            session.job_id = 3
            native.busy, native.job.id = True, 4
            controller.page = FEATHER.ScreenPage.CALIBRATION_PROGRESS
            controller._render_screen()
            self.assertTrue(typer.drew("MEASURING X RESONANCE"))
            native.busy = False
            controller.page = result_page
            controller._render_screen()
            self.assertTrue(typer.drew("MEASURED"))
            self.assertTrue(feature.handle_semantic_action(result_page, ShaperAction("view", "table")))
            self.assertTrue(typer.drew("VIB %"))
            typer.take(lambda events: False, timeout=0.2)

        self.assertEqual(controller.renderer.get_status()["screen_errors"], 0)
        self.assertIn(ShaperAction("method", "ei").wire_id, controller.renderer.shown_actions())

    def test_a_component_page_the_loop_cannot_draw_fails_the_run(self):
        controller, typer, run = self._component_run()

        with self.assertLogs(level="WARNING") as logs:
            run.scenarios._render_component_default(PageTree(_PythonRender(), Rect(0, 60, 800, 360), page_id=_Pages.PYTHON_RENDER))
            typer.take(lambda events: controller.renderer.get_status()["screen_errors"])

        self.assertIn("needs Python", logs.output[0])
        (error,) = [failure["error"] for failure in run._health_failures()]
        self.assertIn("1 screen frame(s) not drawn", error)
        self.assertIn("paint needs Python", error)
        self.assertIn("_PythonRender", error)

    def test_a_restarted_typer_shows_home_again(self):
        controller = home_controller()
        restarted = []

        def handler():
            restarted.append(True)
            controller._renderer_restarted()

        typer = TyperLoop(controller.renderer, {"exit_at": 1}, {}, restart_handler=handler)
        self.addCleanup(typer.close)
        typer.start()
        controller._render_screen()

        typer.take(lambda events: restarted)

        self.assertTrue(typer.drew("192.168.2.124", launch=1))
        self.assertIn("nav.heat", shown_actions(controller._screen_root))

    def test_a_restarted_typer_shows_a_frozen_terminal_error_with_its_buttons(self):
        controller = home_controller()
        controller.touch_available = True
        restarted = []

        def handler():
            restarted.append(True)
            controller._renderer_restarted()

        typer = TyperLoop(controller.renderer, {}, {}, restart_handler=handler)
        self.addCleanup(typer.close)
        typer.start()
        controller._render_screen()
        controller._show_terminal_error("MCU shutdown", "shutdown", "firmware_restart")
        self.assertTrue(typer.drew("MCU shutdown"))

        controller.renderer.restart()
        typer.take(lambda events: restarted)

        self.assertTrue(typer.drew("MCU shutdown", launch=1))
        self.assertIn("error.firmware_restart", shown_actions(controller._screen_root))
        self.assertTrue(controller.renderer.output_frozen)


if __name__ == "__main__":
    unittest.main()
