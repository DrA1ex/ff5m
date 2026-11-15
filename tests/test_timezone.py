## System timezone command and notification contracts.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import calendar
from datetime import datetime, timezone
import importlib.util
import os
import pathlib
import shutil
import stat
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo


ROOT = pathlib.Path(__file__).parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SYSTEM = load_module("tested_system_time", ROOT / ".py/klipper/plugins/system_time.py")
GCODE = load_module("timezone_gcode", ROOT / ".py/klipper/patches/gcode.py")
WINTER = calendar.timegm((2026, 1, 15, 12, 0, 0))
SUMMER = calendar.timegm((2026, 7, 15, 12, 0, 0))


class Printer:
    config_error = RuntimeError

    def __init__(self):
        self.events = {}
        self.messages = []
        self.reactor = SimpleNamespace(mutex=threading.RLock)
        self.gcode = GCODE.GCodeDispatch(self)
        self.gcode.register_output_handler(self.messages.append)

    def get_start_args(self):
        return {}

    def get_reactor(self):
        return self.reactor

    def lookup_object(self, name, default=None):
        if name != "gcode":
            return default
        return self.gcode

    def register_event_handler(self, name, callback):
        self.events.setdefault(name, []).append(callback)

    def send_event(self, name):
        for callback in self.events.get(name, ()):
            callback()

    def command(self, text):
        self.messages.clear()
        self.gcode.run_script_from_command(text)
        return "\n".join(self.messages)


class SystemTimeTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = pathlib.Path(directory.name)
        self.zoneinfo = self.root / "zoneinfo"
        for name in ("Europe/Berlin", "Europe/Moscow", "America/Los_Angeles", "Asia/Kathmandu", "UTC"):
            destination = self.zoneinfo / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(pathlib.Path("/usr/share/zoneinfo") / name, destination)
        self.localtime = self.root / "localtime"
        self.localtime.symlink_to(self.zoneinfo / "Europe/Berlin")
        self.addCleanup(time.tzset)
        for patch in (mock.patch.dict(os.environ, {"TZ": "UTC0"}),
                      mock.patch.object(SYSTEM, "ZONEINFO_ROOT", self.zoneinfo),
                      mock.patch.object(SYSTEM, "LOCALTIME_PATH", self.localtime)):
            patch.start()
            self.addCleanup(patch.stop)
        time.tzset()
        self.printer = Printer()
        self.service = SYSTEM.load_config(SimpleNamespace(get_printer=lambda: self.printer))
        self.printer.send_event("klippy:ready")
        self.notifications = []
        self.printer.register_event_handler("system_time:timezone_changed", lambda: self.notifications.append(
            (self.localtime.resolve(), self.wall_time(WINTER))))

    @staticmethod
    def wall_time(epoch):
        return time.strftime("%H:%M %z", time.localtime(epoch))

    def test_named_zones_quotes_dst_and_repeated_switches(self):
        for name, winter, summer in (
                ('"America/Los_Angeles"', "04:00 -0800", "05:00 -0700"),
                ("Europe/Berlin", "13:00 +0100", "14:00 +0200"),
                ("Asia/Kathmandu", "17:45 +0545", "17:45 +0545"),
                ("America/Los_Angeles", "04:00 -0800", "05:00 -0700")):
            with self.subTest(zone=name):
                output = self.printer.command("SET_TIMEZONE ZONE=" + name)
                self.assertIn(name.strip('"'), output)
                self.assertEqual(self.wall_time(WINTER), winter)
                self.assertEqual(self.wall_time(SUMMER), summer)
                self.assertEqual(self.notifications[-1][1], winter)
        self.assertEqual(len(self.notifications), 4)

    def test_fixed_offsets_have_intuitive_sign_minutes_and_no_dst(self):
        database = {path: path.is_file() and path.read_bytes() for path in self.zoneinfo.rglob("*")}
        for zone, expected in (("UTC+3", "15:00 +0300"), ("UTC-8", "04:00 -0800"),
                               ("UTC+05:30", "17:30 +0530"), ("UTC-3:30", "08:30 -0330"),
                               ("UTC+5:45", "17:45 +0545"), ("UTC-0:30", "11:30 -0030"),
                               ("UTC+0", "12:00 +0000"), ("UTC-0", "12:00 +0000"),
                               ("UTC", "12:00 +0000"), ("UTC+3", "15:00 +0300")):
            with self.subTest(zone=zone):
                self.printer.command("SET_TIMEZONE ZONE=" + zone)
                self.assertEqual(self.wall_time(WINTER), expected)
                self.assertEqual(self.wall_time(SUMMER), expected)
                with self.localtime.open("rb") as stream:
                    independent = ZoneInfo.from_file(stream)
                actual = datetime.fromtimestamp(WINTER, timezone.utc).astimezone(independent)
                self.assertEqual(actual.strftime("%H:%M %z"), expected)
                self.assertTrue(self.localtime.stat().st_mode & stat.S_IROTH)
        self.assertEqual({path: path.is_file() and path.read_bytes() for path in self.zoneinfo.rglob("*")}, database)
        self.assertIn("Timezone: UTC\n", self.printer.command("SET_TIMEZONE ZONE=UTC-0"))

    def test_no_zone_shows_current_selection_and_help_without_changes(self):
        self.printer.command("SET_TIMEZONE ZONE=America/Los_Angeles")
        before = self.localtime.lstat()
        output = self.printer.command("SET_TIMEZONE")
        self.assertIn("America/Los_Angeles", output)
        self.assertIn("Local time:", output)
        for format_example in ("ZONE=Europe/", "ZONE=UTC+", "ZONE=UTC-"):
            self.assertIn(format_example, output)
        self.assertEqual(self.localtime.lstat(), before)
        self.assertEqual(len(self.notifications), 1)
        self.assertEqual(self.wall_time(WINTER), "04:00 -0800")

    def test_aliases_work_but_metadata_and_escaping_symlinks_do_not(self):
        (self.zoneinfo / "Alias").symlink_to("America/Los_Angeles")
        self.assertIn("Timezone: Alias\n", self.printer.command("SET_TIMEZONE ZONE=Alias"))
        self.assertEqual(self.wall_time(WINTER), "04:00 -0800")
        (self.zoneinfo / "zone.tab").write_text("not timezone data")
        shutil.copyfile(self.zoneinfo / "UTC", self.root / "outside")
        (self.zoneinfo / "Escape").symlink_to("../outside")
        (self.zoneinfo / "right").mkdir()
        shutil.copyfile(self.zoneinfo / "UTC", self.zoneinfo / "right/UTC")
        for name in ("zone.tab", "Escape", "America", "Missing/City", "right/UTC"):
            with self.subTest(zone=name), self.assertRaises(GCODE.CommandError):
                self.printer.command("SET_TIMEZONE ZONE=" + name)
        self.assertEqual(self.wall_time(WINTER), "04:00 -0800")
        self.assertEqual(len(self.notifications), 1)

    def test_bad_input_preserves_selection_time_and_notifications(self):
        before = self.localtime.resolve()
        for zone in ('""', "../../etc/passwd", "/etc/passwd", "UTC+24", "UTC-24:00", "UTC+5:60", "UTC+5:3", "UTC+5.5", "UTC+530", '"UTC +3"'):
            with self.subTest(zone=zone), self.assertRaises(GCODE.CommandError):
                self.printer.command("SET_TIMEZONE ZONE=" + zone)
            self.assertEqual(self.localtime.resolve(), before)
            self.assertEqual(self.wall_time(WINTER), "13:00 +0100")
        self.assertEqual(self.notifications, [])

    def test_failed_replacement_preserves_old_zone_and_next_command_recovers(self):
        before = set(self.root.iterdir())
        with mock.patch.object(SYSTEM.os, "replace", side_effect=PermissionError("read only")):
            with self.assertRaises(GCODE.CommandError):
                self.printer.command("SET_TIMEZONE ZONE=America/Los_Angeles")
        self.assertEqual(self.wall_time(WINTER), "13:00 +0100")
        self.assertEqual(self.localtime.resolve(), (self.zoneinfo / "Europe/Berlin").resolve())
        self.assertEqual(set(self.root.iterdir()), before)
        self.assertEqual(self.notifications, [])
        self.printer.command("SET_TIMEZONE ZONE=UTC-8")
        self.assertEqual(self.wall_time(WINTER), "04:00 -0800")

    def test_zones_switch_on_a_python_built_without_time_tzset(self):
        tzset = time.tzset
        del time.tzset
        self.addCleanup(setattr, time, "tzset", tzset)
        self.printer.send_event("klippy:ready")
        self.assertEqual(self.wall_time(WINTER), "13:00 +0100")
        for zone, expected in (("America/Los_Angeles", "04:00 -0800"), ("UTC+5:30", "17:30 +0530"),
                               ("UTC-3", "09:00 -0300"), ("Europe/Berlin", "13:00 +0100")):
            with self.subTest(zone=zone):
                self.printer.command("SET_TIMEZONE ZONE=" + zone)
                self.assertEqual(self.wall_time(WINTER), expected)

    def test_ready_restores_selected_zone_over_inherited_environment(self):
        os.environ["TZ"] = "UTC0"
        time.tzset()
        self.assertEqual(self.wall_time(WINTER), "12:00 +0000")
        self.printer.send_event("klippy:ready")
        self.assertEqual(self.wall_time(WINTER), "13:00 +0100")
        self.assertEqual(self.notifications, [])

    def test_offset_bounds_and_persistence_across_a_new_klipper_instance(self):
        for zone, expected in (("UTC+23:59", "2026-01-16 11:59 +2359"),
                               ("UTC-23:59", "2026-01-14 12:01 -2359")):
            with self.subTest(zone=zone):
                self.printer.command("SET_TIMEZONE ZONE=" + zone)
                self.assertEqual(time.strftime("%Y-%m-%d %H:%M %z", time.localtime(WINTER)), expected)
        self.printer.command("SET_TIMEZONE ZONE=UTC+5:30")
        os.environ["TZ"] = "UTC0"
        time.tzset()
        restarted = Printer()
        SYSTEM.load_config(SimpleNamespace(get_printer=lambda: restarted))
        restarted.send_event("klippy:ready")
        self.assertEqual(self.wall_time(WINTER), "17:30 +0530")
        self.assertIn("Timezone: UTC+05:30", restarted.command("SET_TIMEZONE"))

    def test_fixed_offset_remains_valid_after_2038(self):
        self.printer.command("SET_TIMEZONE ZONE=UTC+5:30")
        self.assertEqual(self.wall_time(calendar.timegm((2040, 7, 15, 12, 0, 0))), "17:30 +0530")

    def test_feather_clock_updates_on_the_event_without_waiting_for_a_timer(self):
        from tests.test_feather_screen import FEATHER, DeferredReactor, StatusObject
        from tests.feather_render_test_helper import RenderCapture, RenderFrame

        reactor = self.printer.reactor = DeferredReactor()
        reactor.mutex = threading.RLock
        reactor.register_async_callback = reactor.register_callback
        config = SimpleNamespace(
            get_printer=lambda: self.printer,
            get=lambda name, default=None: str(self.root / "history.json") if name == "print_history_path" else default,
            getboolean=lambda name, default: default,
            getfloat=lambda name, default, **kwargs: default)
        screen = FEATHER.FeatherScreen(config)
        self.addCleanup(screen.file_worker.stop)
        screen.print_state = FEATHER.PrintState.IDLE
        screen.extruder = StatusObject({"temperature": 20., "target": 0.})
        screen.heater_bed = StatusObject({"temperature": 21., "target": 0.})
        screen.toolhead = StatusObject({"homed_axes": ""})
        screen.network_status = {}
        screen.last_job_name = "NONE"
        screen._current_material = lambda: "PLA"
        screen._last_dashboard = None
        capture = RenderCapture(screen.renderer)
        real_strftime = time.strftime

        # Only freeze the wall clock. libc performs the real zone conversion.
        with mock.patch.object(time, "strftime", side_effect=lambda fmt, when=None: real_strftime(
                fmt, time.localtime(WINTER) if when is None else when)):
            screen._render_screen()
            self.assertEqual(screen._last_dashboard.clock, "13:00")
            self.printer.command('SET_TIMEZONE ZONE="America/Los_Angeles"')
            self.assertEqual(screen._last_dashboard.clock, "04:00")
            self.assertTrue(RenderFrame(capture.batches[-1], screen.renderer).has_text("04:00"))
            self.printer.command("SET_TIMEZONE ZONE=UTC+5:30")
            self.assertEqual(screen._last_dashboard.clock, "17:30")
            self.assertTrue(RenderFrame(capture.batches[-1], screen.renderer).has_text("17:30"))

            screen.page = FEATHER.ScreenPage.MAIN_MENU
            frame_count = len(capture.batches)
            self.printer.command("SET_TIMEZONE ZONE=Europe/Berlin")
            self.assertEqual(len(capture.batches), frame_count)
