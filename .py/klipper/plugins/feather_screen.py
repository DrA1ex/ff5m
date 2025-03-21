## Feather screen support macros
##
## Copyright (C) 2025, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import enum
import logging
import os.path
import subprocess
import time
from typing import List

PIPE_NAME = "/tmp/typer"
REFRESH_TIME = 1


class FeatherScreenHelper:
    def __init__(self, debug=False):
        self.debug = debug
        self._process = None
        self._pipe = None

        self._last_status_bar = None
        self._last_file_caption = None
        self._last_print_status = None
        self._last_progress = None
        self._last_left_panel = None

    def start(self):
        os.system("killall typer")

        self._process = subprocess.Popen(
            [
                "/root/printer_data/bin/typer",
                *(["--debug"] if self.debug else []),
                "--double-buffered",
                "batch", "--pipe", PIPE_NAME
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT
        )

        self._pipe = open(PIPE_NAME, "w")

    def stop(self):
        if self._process:
            self._pipe.close()
            self._process.terminate()
            self._process = None
            self._pipe = None

    icon_extruder = '\ue119'
    icon_bed = '\ue003'
    icon_wifi = '\uE146'
    icon_servo = '\ue050'
    icon_active = '\ue076'
    icon_camera = '\ue03b'

    toolbar_y = 25

    def draw_toolbar(self, *, wifi: bool, camera: bool, motors: bool, idle: bool, extruder_temp: float, bed_temp: float):
        if not self._process or self._process.poll() is not None:
            raise RuntimeError("Screen is not running")

        extruder_temp_str = "%0.1f" % extruder_temp
        bed_temp_str = "%0.1f" % bed_temp

        status_bar_data = [wifi, camera, motors, idle, extruder_temp_str, bed_temp_str]
        if self._last_status_bar == status_bar_data: return
        self._last_status_bar = status_bar_data

        extruder_color = "ff0000" if extruder_temp >= 50 else "ffffff"
        bed_color = "ff0000" if bed_temp >= 40 else "ffffff"

        wifi_color = "ffffff" if wifi else "606060"
        active_color = "ea00ff"
        servo_color = "ff9000"
        camera_color = "ffffff"

        offset_x = 770
        icon_width = 40
        icons = [
            f'--batch text -p {offset_x} {self.toolbar_y} -c {wifi_color}  -ha right '
            + f'-va middle -f  "Typicons 12pt" -t "{self.icon_wifi}"',
        ]

        if camera:
            offset_x -= icon_width
            icons.append(
                f'--batch text -p {offset_x} {self.toolbar_y} -c {camera_color} -ha right '
                + f'-va middle -f  "Typicons 12pt" -t "{self.icon_camera}"'
            )

        if not idle:
            offset_x -= icon_width
            icons.append(
                f'--batch text -p {offset_x} {self.toolbar_y} -c {active_color} -ha right '
                + f'-va middle -f  "Typicons 12pt" -t "{self.icon_active}"'
            )

        if motors:
            offset_x -= icon_width
            icons.append(
                f'--batch text -p {offset_x} {self.toolbar_y} -c {servo_color} -ha right '
                + f'-va middle -f  "Typicons 12pt" -t "{self.icon_servo}"'
            )

        self._send_commands([
            '--batch fill -p 0 0 -s 800 40',
            f'--batch text -p 30 {self.toolbar_y}',
            f'--batch text -c {bed_color}      -ha right -va middle -f  "Typicons 12pt"  -t "{self.icon_bed}"',
            f'--batch text -c {bed_color}      -ha left  -va middle -f  "Roboto 12pt"    -t " {bed_temp_str}  "',
            f'--batch text -c {extruder_color} -ha left  -va middle -f  "Typicons 12pt"  -t "{self.icon_extruder}"',
            f'--batch text -c {extruder_color} -ha left  -va middle -f  "Roboto 12pt"    -t " {extruder_temp_str}"',
            *icons
        ])

    def print_caption(self, caption: str):
        if self._last_file_caption == caption: return
        self._last_file_caption = caption

        self._send_commands([
            f'--batch fill -p 0 370 -s 800 50 -c 0',
            f'--batch text -ha center -p 400 400 -c 00f0f0 -f "Roboto 12pt" -t "{caption}"'
        ])

    def print_status(self, status: str):
        self._last_print_status = status
        self._send_commands([
            f'--batch fill -p 205 425 -s 390 30 -c 0',
            f'--batch text -p 400 440 -ha center -va middle -c 00f0f0 -f "JetBrainsMono 8pt" -b 0 -t "{status}"'
        ])

    def print_progress(self, value: int):
        value = max(0, min(100, value))

        if self._last_progress == value: return
        self._last_progress = value

        progress_width = round(value * 380 / 100)
        self._send_commands([
            f'--batch fill    -c 0         -p 200 420 -s 400 40',
            f'--batch stroke  -c 872187    -p 200 420 -s 400 40 -lw 4 -sd inner',
            f'--batch fill    -c 872187    -p 210 430 -s {progress_width} 20',
            f'--batch fill    -c 0         -p 610 420 -s 100 60',
            f'--batch text     -c 00f0f0    -p 620 440 -va middle -b 0 -t "{value}%"'
        ])

        if value == 0 and self._last_print_status:
            self.print_status(self._last_print_status)

    def print_left_panel(self, text: str):
        if self._last_left_panel == text: return
        self._last_left_panel = text

        self._send_commands([
            f'--batch fill -c 0 -p 0 400 -s 200 80',
            f'--batch text -p 180 440 -va middle -ha right -c 00f0f0 -b 0 -t "{text}"'
        ])

    def clear(self):
        self._last_status_bar = None
        self._last_file_caption = None
        self._last_print_status = None
        self._last_left_panel = None
        self._last_progress = None
        self._send_commands([
            f'--batch fill -p 0 360 -s 800 120 -c 0',
        ])

    def _send_commands(self, commands: List[str]):
        if not self._pipe: return

        self._pipe.write('\n'.join([
            *commands,
            "--batch flush",
            "--end\n",
        ]))

        self._pipe.flush()


class ScreenState(enum.Enum):
    INACTIVE = 0
    IDLE = 1
    PREPARING = 2
    PRINTING = 3
    PAUSED = 4
    FINISHED = 5

    DESTROYED = 100


class FeatherScreen:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')

        self.debug = config.getboolean("debug", False)
        self.feather = FeatherScreenHelper(self.debug)
        self._toolbar_timer = None

        self.state_time = self.reactor.monotonic()
        self.state = ScreenState.INACTIVE

        self.printer.register_event_handler("klippy:ready", self._init)
        self.printer.register_event_handler("klippy:shutdown", self._shutdown)
        self.printer.register_event_handler("klippy:disconnect", self._shutdown)

        self.gcode.register_command("FEATHER_PRINT_STATUS", self.cmd_FEATHER_PRINT_STATUS)

    def _init(self):
        self.vcard = self.printer.lookup_object("virtual_sdcard")
        self.params = self.printer.lookup_object("mod_params")

        self.extruder = self.printer.lookup_object("extruder")
        self.heater_bed = self.printer.lookup_object("heater_bed")
        self.toolhead = self.printer.lookup_object("toolhead")

        self.idle_timeout = self.printer.lookup_object("idle_timeout")
        self.pause_resume = self.printer.lookup_object("pause_resume")

        self.display_status = self.printer.lookup_object("display_status")
        self.print_stats = self.printer.lookup_object("print_stats")

        self.virtual_sdcard = self.printer.lookup_object("virtual_sdcard")

        self.feather.start()

        self._change_state(ScreenState.IDLE, self.reactor.monotonic())
        self._toolbar_timer = self.reactor.register_timer(self._update_status_bar, self.reactor.NOW)

    def _shutdown(self):
        self._change_state(ScreenState.DESTROYED, self.reactor.NOW)
        if self._toolbar_timer is not None:
            self.reactor.unregister_timer(self._toolbar_timer)
            self._toolbar_timer = None

        self.feather.stop()

    def cmd_FEATHER_PRINT_STATUS(self, gcmd):
        status = gcmd.get("S")

        if self.feather:
            self.feather.print_status(status)

    def _change_state(self, new_state: ScreenState, eventtime):
        if self.state == ScreenState.DESTROYED: return
        if self.state == new_state: return

        old_state = self.state
        self.state_time = self.reactor.monotonic()
        self.state = new_state

        if self.debug:
            logging.info(f"[feather_screen] {old_state.name} -> {new_state.name}")

        if new_state in {ScreenState.PRINTING, ScreenState.PREPARING}:
            if new_state == ScreenState.PREPARING:
                self.feather.print_progress(0)

            filename = self.virtual_sdcard.file_path() or "Unknown"
            filename = filename.split("/")[-1].split(".gcode")[0]

            self.feather.print_caption(filename)
            estimate_time = self._get_time_estimation_str(eventtime)
            self.feather.print_left_panel(estimate_time)
        elif new_state == ScreenState.PAUSED:
            self.feather.print_status("PAUSED")
        elif new_state == ScreenState.FINISHED:
            stats = self.print_stats.get_status(eventtime)
            stats_state = stats["state"]
            if stats_state == "complete":
                self.feather.print_progress(100)
                self.feather.print_left_panel(self._convert_duration(stats["print_duration"], 2))
                self.feather.print_caption("Finished")
            elif stats_state == "cancelled":
                self.feather.print_caption("Cancelled")
            elif stats_state == "error":
                self.feather.print_caption("Failed")
        elif new_state == ScreenState.IDLE:
            self.feather.clear()

    def _update_status_bar(self, eventtime):
        if self.state == ScreenState.DESTROYED: return None

        t = time.time()

        idle_state = self.idle_timeout.get_status(eventtime)["state"]
        self.feather.draw_toolbar(
            wifi=os.path.exists("/tmp/net_connected_f"),
            camera=os.path.exists("/tmp/camera_f"),
            idle=idle_state == "Idle",
            motors=len(self.toolhead.get_status(eventtime)["homed_axes"]) > 0,
            extruder_temp=self.extruder.get_status(eventtime)["temperature"],
            bed_temp=self.heater_bed.get_status(eventtime)["temperature"],
        )

        stats = self.print_stats.get_status(eventtime)
        stats_state = stats["state"]
        print_duration = stats["print_duration"]

        if stats_state == "printing":
            if print_duration == 0:
                self._change_state(ScreenState.PREPARING, eventtime)
            else:
                self._change_state(ScreenState.PRINTING, eventtime)
        elif stats_state == "paused":
            self._change_state(ScreenState.PAUSED, eventtime)
        elif stats_state in {"complete", "cancelled", "error"}:
            self._change_state(ScreenState.FINISHED, eventtime)
        elif stats_state == "standby":
            self._change_state(ScreenState.IDLE, eventtime)

        if self.state == ScreenState.PRINTING:
            progress = int(self.display_status.get_status(eventtime)["progress"] * 100)
            self.feather.print_progress(progress)
            self.feather.print_left_panel(self._get_time_estimation_str(eventtime))

        if self.debug:
            logging.info(f"[feather_screen] Loop time: {time.time() - t:0.3f}")

        return eventtime + REFRESH_TIME

    def _get_time_estimation_str(self, eventtime):
        stats = self.print_stats.get_status(eventtime)

        print_duration = stats["print_duration"]
        estimate_duration = self.virtual_sdcard.estimate_print_time

        if not estimate_duration:
            estimate_duration = self.toolhead.get_status(eventtime)["estimated_print_time"]

            if not estimate_duration:
                current_layer = stats["info"]["current_layer"]
                total_layer = stats["info"]["total_layer"]

                if current_layer and total_layer:
                    estimate_duration = print_duration / max(current_layer, 1) * total_layer
                else:
                    estimate_duration = stats["total_duration"]

        if print_duration > estimate_duration:
            estimate_duration = print_duration

        if self.state == ScreenState.PRINTING:
            return f"{self._convert_duration(print_duration)} / {self._convert_duration(estimate_duration)}"

        return f"~ {self._convert_duration(estimate_duration, 2)}"

    @staticmethod
    def _convert_duration(t: float, digits=1):
        units = [
            {"unit": "d", "exp": 60 * 60 * 24},
            {"unit": "h", "exp": 60 * 60},
            {"unit": "m", "exp": 60},
            {"unit": "s", "exp": 1},
        ]

        t = round(t)

        values = []
        for entry in units:
            if t >= entry["exp"]:
                value = t // entry["exp"]
                t %= entry["exp"]
                values.append(f"{value}{entry['unit']}")

        if not values:
            return "0s"

        return " ".join(values[:digits])


def load_config(config):
    return FeatherScreen(config)
