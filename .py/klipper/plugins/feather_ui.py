## Lightweight renderer and layout primitives for Feather.
##
## This module deliberately contains no Klipper state or business logic.  Keeping
## it separate makes the 800x480 layout testable without starting another
## process on the printer.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import enum
import json
import logging
import math
import os
import errno
import re
import stat
import subprocess
import time
from collections import deque


DRAW_PIPE = "/tmp/typer"
EVENT_PIPE = "/tmp/feather-events"
TOUCH_DEVICE = "/dev/input/guppy"
SCREEN_WIDTH = 800
SCREEN_HEIGHT = 480
FRAME_X = 8
FRAME_Y = 4
FRAME_WIDTH = 784
FRAME_HEIGHT = 472
HEADER_BOTTOM = 55
FOOTER_Y = 444
FOOTER_HEIGHT = 32
CONTENT_BOTTOM = FOOTER_Y - 2
MAX_PENDING_DRAW = 256 * 1024
# Keep each FIFO write below Linux PIPE_BUF.  An atomic frame is either fully
# accepted or retried, so a page cannot remain half-rendered until a later UI
# update happens to drain the tail.
MAX_ATOMIC_DRAW = 3584
COLOR_BG = "030607"
COLOR_PANEL = "050c0f"
COLOR_CYAN = "35d9e6"
COLOR_VIOLET = "b47aff"
COLOR_AMBER = "f2c94c"
COLOR_RED = "ff4d5a"
COLOR_TEXT = "d9e4e8"
COLOR_DIM = "56656c"

DEFAULT_THEME = "DEFAULT"
THEME_NAME_ALIASES = {
    "CYBERPANK_RED": "CYBERPUNK_RED",
    "CYBERPANK_YELLOW": "CYBERPUNK_YELLOW",
}
THEME_DIRECTORY = os.path.join(
    os.path.dirname(os.path.realpath(__file__)), "feather_themes")
USER_THEME_DIRECTORY = "/opt/config/mod_data/themes"
THEME_SCHEMA_VERSION = 1
COLOR_ROLES = {
    "030607": "background",
    "050c0f": "panel",
    "35d9e6": "primary",
    "00f0f0": "primary",
    "b47aff": "secondary",
    "872187": "secondary_dark",
    "f2c94c": "warning",
    "ffb000": "warning",
    "ff9000": "warning",
    "ff4d5a": "danger",
    "ff3030": "danger",
    "d9e4e8": "text",
    "ffffff": "bright",
    "56656c": "dim",
    "606060": "dim",
    "295c66": "border",
    "263238": "muted",
    "56c596": "success",
    "244c66": "primary_dark",
    "120708": "danger_background",
    "103238": "pressed_background",
    "010203": "overlay",
    "ff00ff": "secondary",
}
FALLBACK_THEME = {
    "background": "030607", "panel": "050c0f", "primary": "35d9e6",
    "primary_dark": "244c66", "secondary": "b47aff",
    "secondary_dark": "872187", "warning": "f2c94c", "danger": "ff4d5a",
    "danger_background": "120708", "text": "d9e4e8", "bright": "ffffff",
    "dim": "56656c", "border": "295c66", "muted": "263238",
    "success": "56c596", "pressed_background": "103238",
    "overlay": "010203",
}
# Component colors are optional additions to the version 1 theme format.
# Resolving them through existing palette roles keeps every older user theme
# valid while allowing themes with a distinct physical "shell" to style
# controls independently from their display surfaces.
OPTIONAL_THEME_ROLE_FALLBACKS = {
    "button_background": "panel",
    "button_border": "primary",
    "button_text": "primary",
    "button_selected_background": "panel",
    "button_selected_border": "secondary",
    "button_selected_text": "secondary",
    "header_background": "panel",
    "header_text": "primary",
    "header_border": "border",
}


class Page(enum.Enum):
    IDLE_HOME = 1
    MAIN_MENU = 25
    CONTROL_HOME = 2
    FILE_BROWSER = 3
    FILE_CONFIRM = 4
    PRINTING = 5
    PAUSED = 6
    CANCEL_CONFIRM = 7
    CONTROL_MOVE = 8
    CONTROL_HEAT = 9
    FILAMENT_MATERIAL = 10
    FILAMENT_ACTION = 11
    CALIBRATION_HOME = 12
    CALIBRATION_Z = 13
    CALIBRATION_CONFIRM = 14
    CALIBRATION_PROGRESS = 15
    CALIBRATION_RESULT = 16
    SETTINGS = 17
    NETWORK_HOME = 18
    WIFI_SCAN = 19
    WIFI_PASSWORD = 20
    NETWORK_PROGRESS = 21
    RECOVERY_PROMPT = 22
    RECOVERY_CONFIRM = 23
    MESSAGE = 24
    MOD_SETTINGS = 26
    MOD_ENUM = 27
    MOD_VALUE = 28
    ERROR = 29
    LIVE_Z_OFFSET = 30
    Z_OFFSET_SUMMARY = 31
    Z_OFFSET_BRIEFING = 32
    Z_OFFSET_PAPER = 33
    Z_OFFSET_PAPER_BRIEFING = 34


class PrintState(enum.Enum):
    INACTIVE = 0
    IDLE = 1
    PREPARING = 2
    PRINTING = 3
    PAUSED = 4
    FINISHED = 5
    DESTROYED = 100


class FeatherRenderer:
    """Translate small UI primitives into typer display-list commands."""

    BUTTON_COLORS = {
        "enabled": ("button_background", "button_border", "button_text"),
        "disabled": ("button_background", "263238", COLOR_DIM),
        "selected": (
            "button_selected_background",
            "button_selected_border",
            "button_selected_text"),
        "warning": ("button_background", COLOR_AMBER, COLOR_AMBER),
        "danger": ("120708", COLOR_RED, COLOR_RED),
        "busy": ("button_background", COLOR_AMBER, COLOR_AMBER),
        "pressed": ("103238", "ffffff", "ffffff"),
    }
    FONT_SIZES = (8, 12, 16, 20, 28)
    # Exact horizontal advances from the compiled JetBrains Mono fonts.  Text
    # which merely fits mathematically still looks cramped on the 5" panel, so
    # buttons reserve a visible margin on both sides.
    FONT_ADVANCE = {8: 11, 12: 16, 16: 22, 20: 28, 28: 38}
    BUTTON_TEXT_PADDING = 16
    HINT_TEXT_PADDING = 20
    DIALOG_TEXT_PADDING = 28
    FONT_PATTERN = re.compile(
        r"^(Roboto(?: Bold| Thin)?|JetBrainsMono(?: Bold| Thin)?) (\d+)pt$")

    def __init__(self, debug=False, theme_directories=None):
        self.debug = debug
        self._theme_directories = tuple(
            theme_directories or (THEME_DIRECTORY, USER_THEME_DIRECTORY))
        self._themes = {}
        self._theme_descriptions = {}
        self._theme_name = DEFAULT_THEME
        self._palette = self._with_optional_theme_roles(FALLBACK_THEME)
        self.reload_themes()
        self.process = None
        self.draw_fd = None
        self.event_fd = None
        self._last_footer = None
        self._footer_values = None
        self._footer_drawn = False
        self._buttons = {}
        self._toggles = {}
        self._generation = 0
        self._pending_draw = bytearray()
        self._pending_frames = deque()
        self._retry_scheduled = False
        self._retry_scheduler = None
        self._busy_label = None
        self._output_frozen = False

    def set_retry_scheduler(self, scheduler):
        """Schedule non-blocking FIFO retries on the Klipper reactor."""
        self._retry_scheduler = scheduler

    @property
    def active(self):
        return self.process is not None and self.process.poll() is None

    @property
    def output_frozen(self):
        return self._output_frozen

    def discard_pending_output(self):
        """Drop complete frames not yet accepted by Typer's FIFO."""
        self._pending_draw = bytearray()
        self._pending_frames.clear()
        self._retry_scheduled = False

    def freeze_output(self):
        """Keep the last submitted safety screen as the sole display owner."""
        self._output_frozen = True

    def thaw_output(self):
        self._output_frozen = False

    @staticmethod
    def _make_fifo(path):
        if os.path.exists(path) and not stat.S_ISFIFO(os.stat(path).st_mode):
            os.unlink(path)
        if not os.path.exists(path):
            os.mkfifo(path, 0o666)

    @staticmethod
    def _typer_is_running():
        try:
            entries = os.listdir("/proc")
        except OSError:
            return False
        for entry in entries:
            if not entry.isdigit():
                continue
            try:
                with open("/proc/%s/comm" % entry, "r") as stream:
                    if stream.read().strip() == "typer":
                        return True
            except OSError:
                continue
        return False

    @classmethod
    def _wait_for_typer_exit(cls, timeout):
        deadline = time.monotonic() + timeout
        while cls._typer_is_running() and time.monotonic() < deadline:
            time.sleep(0.02)
        return not cls._typer_is_running()

    @staticmethod
    def quote(value):
        value = str(value).replace("\r", " ").replace("\n", " ")
        value = value.replace("\\", "\\\\").replace('"', '\\"')
        return '"%s"' % value

    def start(self):
        self._output_frozen = False
        subprocess.call(["killall", "typer"], stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL)
        if not self._wait_for_typer_exit(1.0):
            subprocess.call(["killall", "-9", "typer"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
            self._wait_for_typer_exit(0.25)
        # The typer daemon unlinks its FIFOs during a graceful exit. Recreate
        # them only after it is gone so Python and C++ cannot open different
        # inodes under the same path.
        for path in (DRAW_PIPE, EVENT_PIPE):
            try:
                os.unlink(path)
            except OSError as error:
                if error.errno != errno.ENOENT:
                    raise
        self._make_fifo(DRAW_PIPE)
        self._make_fifo(EVENT_PIPE)
        self.event_fd = os.open(EVENT_PIPE, os.O_RDWR | os.O_NONBLOCK)
        args = ["/root/printer_data/bin/typer"]
        if self.debug:
            args.append("--debug")
        args += ["--double-buffered", "--touch-device", TOUCH_DEVICE,
                 "--event-pipe", EVENT_PIPE, "batch", "--pipe", DRAW_PIPE]
        self.process = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        self.draw_fd = os.open(DRAW_PIPE, os.O_RDWR | os.O_NONBLOCK)
        self._pending_draw = bytearray()
        self._pending_frames.clear()
        self._retry_scheduled = False
        self._busy_label = None
        self._last_footer = None
        self._footer_drawn = False
        # Typer's double buffer initially contains whatever was left in the
        # framebuffer. Clear the complete panel before the first partial page
        # render so neither the persistent footer nor the outer margins can
        # expose pixels from the previous screen owner.
        self.send([
            "--batch clear-hitboxes",
            "--batch clear -c %s" % self.color(COLOR_BG),
        ])
        logging.info("[feather_screen] typer started with touch input")

    def stop(self):
        if self.draw_fd is not None:
            os.close(self.draw_fd)
            self.draw_fd = None
        if self.event_fd is not None:
            os.close(self.event_fd)
            self.event_fd = None
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
            self.process = None
        self._pending_draw = bytearray()
        self._pending_frames.clear()
        self._retry_scheduled = False
        self._busy_label = None
        self._last_footer = None
        self._footer_drawn = False
        self._output_frozen = False
        logging.info("[feather_screen] typer stopped")

    def send(self, commands):
        if self._output_frozen or self.draw_fd is None or not commands:
            return
        payloads = self._encode_frames(commands)
        pending_size = (len(self._pending_draw)
                        + sum(len(frame) for frame in self._pending_frames))
        if pending_size + sum(len(payload) for payload in payloads) > MAX_PENDING_DRAW:
            logging.error("[feather_screen] pending draw data exceeded %d bytes; "
                          "restarting renderer", MAX_PENDING_DRAW)
            self._pending_draw = bytearray()
            self._pending_frames.clear()
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
            return
        self._pending_frames.extend(payloads)
        self._drain_draw_queue()

    @staticmethod
    def _encode_frames(commands):
        suffix = ["--batch flush", "--end", ""]
        frames = []
        current = []
        size = len("\n".join(suffix).encode("utf-8"))
        for command in commands:
            line_size = len((str(command) + "\n").encode("utf-8"))
            if current and size + line_size > MAX_ATOMIC_DRAW:
                frames.append(bytearray(
                    "\n".join(current + suffix).encode("utf-8")))
                current = []
                size = len("\n".join(suffix).encode("utf-8"))
            current.append(str(command))
            size += line_size
        if current:
            frames.append(bytearray(
                "\n".join(current + suffix).encode("utf-8")))
        return frames

    def _schedule_draw_retry(self):
        if self._retry_scheduled or self._retry_scheduler is None:
            return
        self._retry_scheduled = True
        self._retry_scheduler(self._retry_draw)

    def _retry_draw(self, eventtime):
        self._retry_scheduled = False
        self._drain_draw_queue()

    def _drain_draw_queue(self):
        if self.draw_fd is None:
            return
        while self._pending_draw or self._pending_frames:
            if not self._pending_draw:
                self._pending_draw = self._pending_frames.popleft()
            try:
                written = os.write(self.draw_fd, self._pending_draw)
                if written == 0:
                    raise OSError("typer draw pipe closed")
                del self._pending_draw[:written]
                if not self._pending_draw:
                    # Do not retain a large allocation after an earlier write.
                    self._pending_draw = bytearray()
            except OSError as error:
                if error.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    self._schedule_draw_retry()
                    return
                logging.exception("[feather_screen] unable to draw")
                return

    def decode_action(self, action):
        """Reject taps emitted for a page that has already been replaced."""
        prefix, separator, logical = str(action).partition(":")
        if not separator:
            return action  # Compatibility with tests and older typer builds.
        try:
            generation = int(prefix)
        except ValueError:
            return None
        return logical if generation == self._generation else None

    def _wire_action(self, action):
        return "%d:%s" % (self._generation, action)

    @property
    def generation(self):
        return self._generation

    @property
    def theme_name(self):
        return self._theme_name

    def reload_themes(self):
        themes = {
            DEFAULT_THEME: self._with_optional_theme_roles(FALLBACK_THEME)}
        descriptions = {DEFAULT_THEME: "cyan Forge-X palette"}
        for directory in self._theme_directories:
            if not os.path.isdir(directory):
                continue
            for filename in sorted(os.listdir(directory)):
                lowered = filename.lower()
                if (not lowered.endswith(".json")
                        or lowered.endswith(".schema.json")):
                    continue
                path = os.path.join(directory, filename)
                try:
                    with open(path, "r", encoding="utf-8") as stream:
                        data = json.load(stream)
                    name, description, colors = self._validate_theme(data)
                    themes[name] = colors
                    descriptions[name] = description
                except Exception as exc:
                    logging.warning("[feather_screen] invalid theme %s: %s",
                                    path, exc)
        self._themes = themes
        self._theme_descriptions = descriptions
        if self._theme_name not in themes:
            self._theme_name = DEFAULT_THEME
        self._palette = themes[self._theme_name]
        return tuple(themes)

    def ensure_user_theme_directory(self):
        if USER_THEME_DIRECTORY not in self._theme_directories:
            return
        try:
            os.makedirs(USER_THEME_DIRECTORY, exist_ok=True)
        except OSError as exc:
            logging.warning("[feather_screen] unable to create theme directory: %s",
                            exc)

    @staticmethod
    def _with_optional_theme_roles(colors):
        expanded = dict(colors)
        for role, fallback_role in OPTIONAL_THEME_ROLE_FALLBACKS.items():
            expanded.setdefault(role, expanded[fallback_role])
        return expanded

    @classmethod
    def _validate_theme(cls, data):
        if not isinstance(data, dict):
            raise ValueError("root must be an object")
        if data.get("schema_version") != THEME_SCHEMA_VERSION:
            raise ValueError("unsupported schema_version")
        name = str(data.get("name", "")).strip().upper()
        if re.match(r"^[A-Z][A-Z0-9_]{0,31}$", name) is None:
            raise ValueError("invalid theme name")
        description = str(data.get("description", "")).strip()
        if not description or len(description) > 80:
            raise ValueError("description must contain 1-80 characters")
        colors = data.get("colors")
        if not isinstance(colors, dict):
            raise ValueError("colors must be an object")
        missing = sorted(set(FALLBACK_THEME) - set(colors))
        if missing:
            raise ValueError("missing colors: %s" % ", ".join(missing))
        normalized = {}
        for role in FALLBACK_THEME:
            value = str(colors[role]).strip().lower()
            if re.match(r"^[0-9a-f]{6}$", value) is None:
                raise ValueError("invalid %s color" % role)
            normalized[role] = value
        for role in OPTIONAL_THEME_ROLE_FALLBACKS:
            if role not in colors:
                continue
            value = str(colors[role]).strip().lower()
            if re.match(r"^[0-9a-f]{6}$", value) is None:
                raise ValueError("invalid %s color" % role)
            normalized[role] = value
        return name, description, cls._with_optional_theme_roles(normalized)

    def theme_names(self, reload=False):
        if reload:
            self.reload_themes()
        return tuple(self._themes)

    def theme_description(self, name):
        return self._theme_descriptions.get(str(name).upper(), "")

    def set_theme(self, name):
        normalized = str(name or DEFAULT_THEME).strip().upper()
        normalized = normalized.replace("-", "_").replace(" ", "_")
        normalized = THEME_NAME_ALIASES.get(normalized, normalized)
        if normalized not in self._themes:
            self.reload_themes()
        if normalized not in self._themes:
            logging.warning("[feather_screen] unknown theme %r; using DEFAULT",
                            name)
            normalized = DEFAULT_THEME
        changed = normalized != self._theme_name
        self._theme_name = normalized
        self._palette = self._themes[normalized]
        if changed:
            # The footer normally survives page redraws and is skipped while
            # its values stay unchanged. A palette change is also a content
            # change, so force begin_page() to repaint it in the new colors.
            self._last_footer = None
            self._footer_drawn = False
        return changed

    def color(self, value):
        normalized = str(value).lower()
        role = (normalized if normalized in self._palette
                else COLOR_ROLES.get(normalized))
        return self._palette.get(role, normalized) if role else normalized

    def fill(self, x, y, width, height, color=COLOR_BG):
        return "--batch fill -p %d %d -s %d %d -c %s" % (
            x, y, width, height, self.color(color))

    def stroke(self, x, y, width, height, color=COLOR_CYAN, line_width=2):
        return "--batch stroke -p %d %d -s %d %d -c %s -lw %d -sd inner" % (
            x, y, width, height, self.color(color), line_width)

    def panel(self, x, y, width, height, border=COLOR_CYAN,
              background=COLOR_PANEL, line_width=2):
        """Draw a reusable filled panel with an optional inner border."""
        commands = [self.fill(x, y, width, height, background)]
        if border is not None and line_width > 0:
            commands.append(
                self.stroke(x, y, width, height, border, line_width))
        return commands

    def filled_circle(self, center_x, center_y, radius, color=COLOR_CYAN):
        """Draw a compact filled circle using horizontal one-pixel spans."""
        radius = max(1, int(radius))
        commands = []
        for offset_y in range(-radius, radius + 1):
            half_width = int(math.sqrt(
                max(0, radius * radius - offset_y * offset_y)))
            commands.append(self.fill(
                int(center_x) - half_width, int(center_y) + offset_y,
                half_width * 2 + 1, 1, color))
        return commands

    def hint_box(self, message, center_x, y, max_width=740, min_width=180,
                 height=44, border=COLOR_VIOLET,
                 background=COLOR_BG, font="JetBrainsMono 8pt"):
        """Draw a centered one-line hint with guaranteed inner padding."""
        font = self.normalize_font(font)
        available = max(1, int(max_width) - 2 * self.HINT_TEXT_PADDING)
        label = self.truncate_text(str(message).upper(), available, font)
        width = min(
            int(max_width),
            max(int(min_width),
                self.text_width(label, font) + 2 * self.HINT_TEXT_PADDING))
        x = int(center_x) - width // 2
        commands = self.panel(
            x, int(y), width, int(height), border=border,
            background=background, line_width=2)
        commands.append(self.text(
            int(center_x), int(y) + int(height) // 2, label, COLOR_TEXT,
            font, "center", "middle"))
        return commands

    def section_panel(self, title, x, y, width, height, border="295c66"):
        """Draw a titled content panel with consistent Feather spacing."""
        commands = self.panel(
            x, y, width, height, border=border, background=COLOR_PANEL,
            line_width=1)
        commands.append(self.text(
            x + 18, y + 21, str(title).upper(), COLOR_CYAN,
            "JetBrainsMono 8pt", "left", "middle"))
        return commands

    def dot_grid(self, x, y, width, height, columns=11, rows=7,
                 color="263238", clip=None):
        """Draw a sparse point grid, optionally limited to a redraw region."""
        commands = []
        if columns < 2 or rows < 2:
            return commands
        if clip is not None:
            clip_x, clip_y, clip_width, clip_height = clip
            clip_right = clip_x + clip_width
            clip_bottom = clip_y + clip_height
        for row in range(rows):
            point_y = y + row * height // (rows - 1)
            for column in range(columns):
                point_x = x + column * width // (columns - 1)
                if (clip is None
                        or (clip_x <= point_x < clip_right
                            and clip_y <= point_y < clip_bottom)):
                    commands.append(self.fill(point_x, point_y, 1, 1, color))
        return commands

    def corner_marks(self, x, y, width, height, length=12, color=COLOR_CYAN):
        """Draw four open targeting corners around a control surface."""
        right = x + width - 1
        bottom = y + height - 1
        commands = []
        for horizontal_x, vertical_x in ((x, x), (right - length + 1, right)):
            commands.append(self.fill(horizontal_x, y, length, 1, color))
            commands.append(self.fill(vertical_x, y, 1, length, color))
            commands.append(self.fill(horizontal_x, bottom, length, 1, color))
            commands.append(self.fill(
                vertical_x, bottom - length + 1, 1, length, color))
        return commands

    def metric_row(self, x, y, width, label, value, unit="",
                   label_color=COLOR_CYAN, value_color=COLOR_TEXT):
        """Draw a compact label/value/unit row for status cards."""
        commands = [
            self.text(x, y, label, label_color, "JetBrainsMono 8pt",
                      "left", "middle"),
            self.text(x + width - (34 if unit else 0), y, value, value_color,
                      "JetBrainsMono 8pt", "right", "middle"),
        ]
        if unit:
            commands.append(self.text(
                x + width, y, unit, label_color, "JetBrainsMono 8pt",
                "right", "middle"))
        return commands

    def vertical_gauge(self, x, y, width, height, title, value,
                       minimum, maximum, initial=None,
                       value_color=COLOR_CYAN):
        """Draw an auto-scaled vertical gauge with a movable start marker."""
        value = float(value)
        minimum = float(minimum)
        maximum = float(maximum)
        if maximum <= minimum:
            padding = max(1.0, abs(value) * 0.05)
            minimum = value - padding
            maximum = value + padding
        track_top = y + 78
        track_bottom = y + height - 28
        track_height = max(1, track_bottom - track_top)
        track_x = x + (width - 16) // 2
        track_width = 16

        def gauge_y(sample):
            ratio = ((float(sample) - minimum) / (maximum - minimum))
            ratio = max(0.0, min(1.0, ratio))
            return track_bottom - int(round(ratio * track_height))

        def number(sample):
            sample = float(sample)
            if abs(sample) < 10000:
                return "%+.1f" % sample
            return "%+.0e" % sample

        value_y = gauge_y(value)
        commands = self.panel(
            x, y, width, height, border="295c66",
            background=COLOR_PANEL, line_width=1)
        commands += [
            self.text(x + width // 2, y + 20, str(title).upper(),
                      COLOR_CYAN, "JetBrainsMono 8pt", "center", "middle"),
            self.text(x + width // 2, y + 48, number(value),
                      COLOR_TEXT, "JetBrainsMono Bold 10pt",
                      "center", "middle"),
        ]
        commands += [
            self.fill(track_x, track_top, track_width, track_height,
                      "263238"),
            self.stroke(track_x, track_top, track_width, track_height,
                        "295c66", 1),
            self.fill(track_x + 2, value_y,
                      max(1, track_width - 4),
                      max(1, track_bottom - value_y), value_color),
        ]
        if initial is not None:
            marker_y = gauge_y(initial)
            commands += [
                self.fill(x + 6, marker_y, max(1, width - 12), 2,
                          COLOR_VIOLET),
                self.fill(x + 3, marker_y - 2, 4, 6, COLOR_VIOLET),
            ]
        return commands

    def joystick_knob(self, x, y, axis="xy", size=25, color=COLOR_CYAN):
        """Draw a centered square joystick knob without font alignment drift."""
        size = max(9, int(size))
        if size % 2 == 0:
            size += 1
        left = int(x) - size // 2
        top = int(y) - size // 2
        commands = self.panel(
            left, top, size, size, border=color, background=COLOR_PANEL,
            line_width=2)
        center_x = int(x)
        center_y = int(y)
        if axis == "z":
            commands += [
                self.fill(center_x - 6, center_y - 3, 13, 1, color),
                self.fill(center_x - 6, center_y + 3, 13, 1, color),
            ]
        else:
            commands += [
                self.fill(center_x - 6, center_y, 13, 1, color),
                self.fill(center_x, center_y - 6, 1, 13, color),
            ]
        return commands

    def text(self, x, y, value, color=COLOR_CYAN,
             font="JetBrainsMono 12pt", h_align="left", v_align="middle"):
        font = self.normalize_font(font)
        value = str(value)
        # argparse treats a text value beginning with '-' as another option.
        # Keep the protective space out of the visible alignment calculation,
        # otherwise centered labels such as -Y appear half a glyph off-center.
        if value.startswith("-"):
            value = " " + value
            match = self.FONT_PATTERN.match(font)
            advance = self.FONT_ADVANCE[int(match.group(2))]
            if h_align == "center":
                x -= advance // 2
            elif h_align == "left":
                x -= advance
        return "--batch text -p %d %d -c %s -f %s -ha %s -va %s -t %s" % (
            x, y, self.color(color), self.quote(font), h_align, v_align,
            self.quote(value))

    @classmethod
    def normalize_font(cls, font):
        """Map UI font requests to sizes actually compiled into typer."""
        match = cls.FONT_PATTERN.match(str(font))
        if match is None:
            logging.warning("[feather_screen] unsupported font %r; using Roboto 12pt",
                            font)
            return "Roboto 12pt"
        family, requested = match.group(1), int(match.group(2))
        if family.startswith("Roboto"):
            family = family.replace("Roboto", "JetBrainsMono", 1)
        size = min(cls.FONT_SIZES, key=lambda candidate: abs(candidate - requested))
        return "%s %dpt" % (family, size)

    @classmethod
    def font_advance(cls, font):
        normalized = cls.normalize_font(font)
        match = cls.FONT_PATTERN.match(normalized)
        return cls.FONT_ADVANCE[int(match.group(2))]

    @classmethod
    def text_width(cls, value, font):
        """Return the exact monospaced advance used by typer for ASCII UI text."""
        return len(str(value)) * cls.font_advance(font)

    @classmethod
    def truncate_text(cls, value, width, font, ellipsis="..."):
        """Shorten monospaced text to pixels without changing its font size."""
        value = str(value)
        advance = cls.font_advance(font)
        max_chars = max(0, int(width) // advance)
        if len(value) <= max_chars:
            return value
        if max_chars <= len(ellipsis):
            return ellipsis[:max_chars]
        return value[:max_chars - len(ellipsis)] + ellipsis

    @staticmethod
    def hitbox(action, x, y, width, height, continuous=False):
        command = "--batch hitbox --id %s -p %d %d -s %d %d" % (
            action, x, y, width, height)
        return command + (" --continuous" if continuous else "")

    def action_hitbox(self, action, x, y, width, height, continuous=False):
        return self.hitbox(self._wire_action(action), x, y, width, height,
                           continuous)

    def _toggle_commands(self, x, y, width, height, thumb_x, enabled):
        border = COLOR_CYAN if enabled else "263238"
        thumb_color = COLOR_CYAN if enabled else COLOR_DIM
        inset = 5
        thumb_size = max(1, height - 2 * inset)
        return [
            self.fill(x, y, width, height, COLOR_PANEL),
            self.stroke(x, y, width, height, border, 2),
            self.fill(thumb_x, y + inset, thumb_size, thumb_size, thumb_color),
        ]

    def toggle(self, action, x, y, width, height, active, enabled=True):
        """Draw a rectangular switch with a centered square thumb and no text."""
        inset = 5
        thumb_size = max(1, height - 2 * inset)
        half = width // 2
        left = x + (half - thumb_size) // 2
        right = x + half + (half - thumb_size) // 2
        thumb_x = right if active else left
        self._toggles[action] = (x, y, width, height, bool(active), enabled,
                                 left, right)
        commands = self._toggle_commands(
            x, y, width, height, thumb_x, enabled)
        if enabled:
            commands.append(self.action_hitbox(action, x, y, width, height))
        return commands

    def animate_toggle(self, action, active, scheduler, duration=0.12):
        spec = self._toggles.get(action)
        if spec is None or not spec[5]:
            return False
        x, y, width, height, current, enabled, left, right = spec
        if current == bool(active):
            return False
        start, finish = (left, right) if active else (right, left)
        generation = self._generation
        frames = 4
        self._toggles[action] = (x, y, width, height, bool(active), enabled,
                                 left, right)

        def draw_frame(_eventtime, index):
            if self._generation != generation:
                return
            thumb_x = start + (finish - start) * index // frames
            self.send(self._toggle_commands(
                x, y, width, height, thumb_x, enabled))

        draw_frame(None, 1)
        for index in range(2, frames + 1):
            scheduler(lambda eventtime, step=index:
                      draw_frame(eventtime, step),
                      duration * (index - 1) / (frames - 1))
        return True

    def block_input(self):
        self.send(["--batch clear-hitboxes"])

    def _button_colors(self, state):
        return tuple(self.color(color) for color in self.BUTTON_COLORS[state])

    def composite_button(self, action, x, y, width, height, label, state, font,
                         include_hitbox=True):
        background, border, text_color = self._button_colors(state)
        display_label = str(label)
        if display_label.startswith("-"):
            display_label = " " + display_label
        command = ("--batch button -p %d %d -s %d %d --background %s "
                   "--border %s --text-color %s -lw 2 -f %s -t %s" %
                   (x, y, width, height, background, border, text_color,
                    self.quote(self.normalize_font(font)),
                    self.quote(display_label)))
        if include_hitbox and state not in ("disabled", "busy"):
            command += " --id %s" % action
        return [command]

    def _button_commands(self, action, x, y, width, height, label, state,
                         font, subtitle=None, include_hitbox=True,
                         layout="center"):
        background, border, text_color = self._button_colors(state)
        if layout == "center" and subtitle is None:
            return self.composite_button(action, x, y, width, height, label,
                                         state, font, include_hitbox)
        commands = [
            self.fill(x, y, width, height, background),
            self.stroke(x, y, width, height, border, 2),
        ]
        if layout == "row":
            commands.append(self.text(x + 24, y + height // 2, label,
                                      text_color, font, "left", "middle"))
            if subtitle is not None:
                lines = subtitle if isinstance(subtitle, (tuple, list)) else (subtitle,)
                start_y = y + height // 2 - (13 if len(lines) > 1 else 0)
                for index, line in enumerate(lines[:2]):
                    commands.append(self.text(
                        x + 285, start_y + index * 26, line,
                        COLOR_TEXT, "JetBrainsMono 8pt", "left", "middle"))
            commands.append(self.text(x + width - 24, y + height // 2, ">",
                                      text_color, "JetBrainsMono 16pt",
                                      "right", "middle"))
        else:
            label_y = y + height // 2 if subtitle is None else y + height // 2 - 14
            commands.append(self.text(x + width // 2, label_y, label,
                                      text_color, font, "center", "middle"))
            if subtitle is not None:
                commands.append(self.text(
                    x + width // 2, y + height // 2 + 24, subtitle, COLOR_DIM,
                    "JetBrainsMono 8pt", "center", "middle"))
        if include_hitbox and state not in ("disabled", "busy"):
            commands.append(self.hitbox(action, x, y, width, height))
        return commands

    def button(self, action, x, y, width, height, label, active=None,
               state="enabled", font="JetBrainsMono 12pt", subtitle=None,
               layout="center"):
        # active is retained for compatibility with the first Feather release.
        if active is not None:
            state = "enabled" if active else "disabled"
        if state not in self.BUTTON_COLORS:
            state = "enabled"
        if layout == "center":
            font = self.normalize_font(font)
            label = self.truncate_text(
                label, width - 2 * self.BUTTON_TEXT_PADDING, font)
        if state not in ("disabled", "busy"):
            self._buttons[action] = (x, y, width, height, label, state, font,
                                     subtitle, layout)
        return self._button_commands(self._wire_action(action), x, y, width,
                                     height, label, state, font, subtitle,
                                     True, layout)

    def dialog(self, title, lines, buttons, x=160, y=130, width=480,
               height=220, tone="warning", modal=True):
        """Build a modal dialog from standard panel, text, and button primitives.

        ``buttons`` contains ``(action, label, state)`` tuples. Clearing all
        existing hitboxes makes a dialog genuinely modal even when it only
        covers one control region visually. Set ``modal`` to false for a
        localized overlay whose caller will explicitly re-register the
        controls that remain available.
        """
        tones = {
            "warning": COLOR_AMBER,
            "danger": COLOR_RED,
            "info": COLOR_CYAN,
        }
        border = tones.get(tone, COLOR_CYAN)
        commands = []
        if modal:
            self._buttons = {}
            self._toggles = {}
            commands.append("--batch clear-hitboxes")
        commands += self.panel(
            x, y, width, height, border=border, background=COLOR_PANEL)
        commands.append(self.text(
            x + width // 2, y + 34, str(title).upper(), border,
            "JetBrainsMono Bold 16pt", "center", "middle"))
        for index, line in enumerate(tuple(lines)[:4]):
            line = self.truncate_text(
                str(line), width - 2 * self.DIALOG_TEXT_PADDING,
                "JetBrainsMono 8pt")
            commands.append(self.text(
                x + width // 2, y + 78 + index * 24, line, COLOR_TEXT,
                "JetBrainsMono 8pt", "center", "middle"))
        button_specs = tuple(buttons)
        if button_specs:
            gap = 12
            margin = 18
            button_width = max(
                1, (width - 2 * margin - gap * (len(button_specs) - 1))
                // len(button_specs))
            button_y = y + height - 58
            for index, (action, label, state) in enumerate(button_specs):
                commands += self.button(
                    action, x + margin + index * (button_width + gap),
                    button_y, button_width, 42, label, state=state,
                    font="JetBrainsMono 8pt")
        return commands

    def flash_button(self, action):
        spec = self._buttons.get(action)
        if spec is None:
            return False
        x, y, width, height, label, _state, font, subtitle, layout = spec
        self.send(self._button_commands(action, x, y, width, height, label,
                                        "pressed", font, subtitle, False, layout))
        return True

    def restore_button(self, action):
        spec = self._buttons.get(action)
        if spec is None:
            return False
        x, y, width, height, label, state, font, subtitle, layout = spec
        self.send(self._button_commands(action, x, y, width, height, label,
                                        state, font, subtitle, False, layout))
        return True

    def begin_page(self, title, back=False):
        self._generation += 1
        self._buttons = {}
        self._toggles = {}
        commands = [
            "--batch clear-hitboxes",
            # Preserve the footer framebuffer. It is a persistent status area
            # and is updated independently only when one of its values changes.
            self.fill(0, 0, SCREEN_WIDTH, FOOTER_Y - 2, COLOR_BG),
            # Keep the dirty rectangle above the persistent footer. A full
            # height outer stroke would make TextDrawer.flush() copy the whole
            # footer even though none of its pixels changed.
            self.stroke(FRAME_X, FRAME_Y, FRAME_WIDTH,
                        FOOTER_Y - FRAME_Y - 1,
                        "header_border", 1),
            self.fill(10, 6, 780, HEADER_BOTTOM - 6, "header_background"),
        ]
        if back:
            commands += self.button("nav.back", 14, 7, 146, 46, "< BACK",
                                    font="JetBrainsMono Bold 8pt")
        title = self.truncate_text(str(title).upper(), 440,
                                   "JetBrainsMono 12pt")
        commands += [
            self.text(400, 29, title, "header_text",
                      "JetBrainsMono 12pt",
                      "center", "middle"),
            self.fill(18, HEADER_BOTTOM, 764, 1, "header_border"),
            self.fill(18, FOOTER_Y - 2, 764, 1, "295c66"),
        ]
        if self._busy_label is not None:
            busy_label = self.truncate_text(
                self._busy_label, 132, "JetBrainsMono Bold 8pt")
            commands += [
                self.fill(622, 9, 160, 38, "header_background"),
                self.stroke(622, 9, 160, 38, COLOR_AMBER, 2),
                self.text(702, 28, busy_label, COLOR_AMBER,
                          "JetBrainsMono Bold 8pt", "center", "middle"),
            ]
        if self._footer_values is not None and not self._footer_drawn:
            commands += self._footer_commands(self._footer_values)
            self._last_footer = self._footer_values
            self._footer_drawn = True
        return commands

    def _footer_commands(self, values):
        left = "NOZZLE %.0f/%.0fC | BED %.0f/%.0fC" % values[:4]
        right = "%s | %s" % (values[4], str(values[5]).upper())
        return [
            self.fill(10, FOOTER_Y, 780, FOOTER_HEIGHT - 1, COLOR_PANEL),
            self.stroke(FRAME_X, FOOTER_Y - 2, FRAME_WIDTH,
                        SCREEN_HEIGHT - (FOOTER_Y - 2) - 4, "295c66", 1),
            self.text(20, FOOTER_Y + 16, left, COLOR_CYAN,
                      "JetBrainsMono 8pt"),
            self.text(780, FOOTER_Y + 16, right, COLOR_CYAN,
                      "JetBrainsMono 8pt", "right"),
        ]

    def footer(self, nozzle, nozzle_target, bed, bed_target, network, state):
        values = (round(nozzle, 1), round(nozzle_target), round(bed, 1),
                  round(bed_target), network, state)
        self._footer_values = values
        if values == self._last_footer:
            return
        self._last_footer = values
        self._footer_drawn = True
        self.send(self._footer_commands(values))

    def toast(self, message):
        self.send(self.hint_box(
            message, 400, 397, max_width=740, min_width=180, height=44,
            border=COLOR_VIOLET, background=COLOR_BG,
            font="JetBrainsMono 8pt"))

    def busy_notice(self, label="KLIPPER BUSY"):
        label = str(label).upper()
        if label == self._busy_label:
            return
        self._busy_label = label
        label = self.truncate_text(label, 132, "JetBrainsMono Bold 8pt")
        self.send([
            self.fill(622, 9, 160, 38, "header_background"),
            self.stroke(622, 9, 160, 38, COLOR_AMBER, 2),
            self.text(702, 28, label, COLOR_AMBER,
                      "JetBrainsMono Bold 8pt", "center", "middle"),
        ])

    def clear_busy_notice(self):
        if self._busy_label is None:
            return
        self._busy_label = None
        menu = self._buttons.get("nav.menu")
        if menu is not None:
            x, y, width, height, label, state, font, subtitle, layout = menu
            self.send(self._button_commands(
                "nav.menu", x, y, width, height, label, state, font,
                subtitle, False, layout))
        else:
            self.send([
                self.fill(622, 9, 160, 38, "header_background")])

    def loader(self, message, phase=0):
        """Draw a non-interactive busy overlay for a yielding G-code call."""
        self._buttons = {}
        commands = [
            "--batch clear-hitboxes",
            self.fill(0, HEADER_BOTTOM + 1, SCREEN_WIDTH,
                      CONTENT_BOTTOM - HEADER_BOTTOM - 1, COLOR_BG),
            self.text(400, 190, message, COLOR_TEXT, "JetBrainsMono Bold 16pt",
                      "center", "middle"),
            self.text(400, 235, "PLEASE WAIT", COLOR_DIM, "JetBrainsMono 12pt",
                      "center", "middle"),
        ]
        for index in range(5):
            color = COLOR_CYAN if index == phase % 5 else "263238"
            commands.append(self.fill(290 + index * 48, 280, 32, 12, color))
        self.send(commands)

    def startup_modal(self, phase=0, restarting=False):
        """Draw the pre-ready Klipper loading modal and its pulse frame."""
        self._buttons = {}
        self._toggles = {}
        detail = ("RESTART IN PROGRESS - DISPLAY MAY PAUSE"
                  if restarting else "INITIALIZING PRINTER SERVICES")
        commands = [
            "--batch clear-hitboxes",
            self.fill(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT, "010203"),
        ]
        commands += self.panel(
            150, 120, 500, 250, border=COLOR_CYAN,
            background=COLOR_PANEL, line_width=2)
        commands += [
            self.text(400, 170, "INITIALIZING KLIPPER", COLOR_CYAN,
                      "JetBrainsMono Bold 16pt", "center", "middle"),
        ]
        commands += self.startup_pulse(phase)
        commands += [
            self.text(400, 300, "PLEASE WAIT", COLOR_TEXT,
                      "JetBrainsMono 12pt", "center", "middle"),
            self.text(400, 335, detail, COLOR_DIM,
                      "JetBrainsMono 8pt", "center", "middle"),
        ]
        self.send(commands)

    def startup_pulse(self, phase=0):
        """Redraw only the animated startup indicator's small dirty area."""
        pulse = (8, 12, 16, 12)[int(phase) % 4]
        commands = [self.fill(374, 206, 53, 53, COLOR_PANEL)]
        commands += self.filled_circle(400, 232, pulse, COLOR_VIOLET)
        return commands

    def applying_modal(self, message="APPLYING CHANGES"):
        """Dim the page and draw a non-interactive modal progress panel."""
        self._buttons = {}
        self._toggles = {}
        commands = [
            "--batch clear-hitboxes",
            self.fill(0, HEADER_BOTTOM + 1, SCREEN_WIDTH,
                      CONTENT_BOTTOM - HEADER_BOTTOM - 1, "010203"),
        ]
        commands += self.panel(160, 145, 480, 180)
        commands += [
            self.text(400, 205, str(message).upper(), COLOR_TEXT,
                      "JetBrainsMono Bold 16pt", "center", "middle"),
            self.text(400, 260, "PLEASE WAIT", COLOR_DIM,
                      "JetBrainsMono 12pt", "center", "middle"),
            self.fill(310, 292, 180, 8, COLOR_CYAN),
        ]
        self.send(commands)


def rectangles_overlap(first, second):
    """Test helper used to keep page hitboxes away from the footer."""
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah
