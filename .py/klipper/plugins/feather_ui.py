## Lightweight renderer and layout primitives for Feather.
##
## This module deliberately contains no Klipper state or business logic.  Keeping
## it separate makes the 800x480 layout testable without starting another
## process on the printer.

import enum
import logging
import os
import errno
import re
import stat
import subprocess
import time


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
COLOR_BG = "030607"
COLOR_PANEL = "050c0f"
COLOR_CYAN = "35d9e6"
COLOR_VIOLET = "b47aff"
COLOR_AMBER = "f2c94c"
COLOR_RED = "ff4d5a"
COLOR_TEXT = "d9e4e8"
COLOR_DIM = "56656c"


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
        "enabled": (COLOR_PANEL, COLOR_CYAN, COLOR_CYAN),
        "disabled": (COLOR_PANEL, "263238", COLOR_DIM),
        "selected": (COLOR_PANEL, COLOR_VIOLET, COLOR_VIOLET),
        "warning": (COLOR_PANEL, COLOR_AMBER, COLOR_AMBER),
        "danger": ("120708", COLOR_RED, COLOR_RED),
        "busy": (COLOR_PANEL, COLOR_AMBER, COLOR_AMBER),
        "pressed": ("103238", "ffffff", "ffffff"),
    }
    FONT_SIZES = (8, 12, 16, 20, 28)
    # Exact horizontal advances from the compiled JetBrains Mono fonts.  Text
    # which merely fits mathematically still looks cramped on the 5" panel, so
    # buttons reserve a visible margin on both sides.
    FONT_ADVANCE = {8: 11, 12: 16, 16: 22, 20: 28, 28: 38}
    BUTTON_TEXT_PADDING = 16
    FONT_PATTERN = re.compile(
        r"^(Roboto(?: Bold| Thin)?|JetBrainsMono(?: Bold| Thin)?) (\d+)pt$")

    def __init__(self, debug=False):
        self.debug = debug
        self.process = None
        self.draw_fd = None
        self.event_fd = None
        self._last_footer = None
        self._footer_values = None
        self._footer_drawn = False
        self._buttons = {}
        self._generation = 0
        self._pending_draw = bytearray()
        self._retry_scheduled = False
        self._retry_scheduler = None
        self._busy_label = None

    def set_retry_scheduler(self, scheduler):
        """Schedule non-blocking FIFO retries on the Klipper reactor."""
        self._retry_scheduler = scheduler

    @property
    def active(self):
        return self.process is not None and self.process.poll() is None

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
        self._retry_scheduled = False
        self._busy_label = None
        self._last_footer = None
        self._footer_drawn = False
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
        self._retry_scheduled = False
        self._busy_label = None
        self._last_footer = None
        self._footer_drawn = False
        logging.info("[feather_screen] typer stopped")

    def send(self, commands):
        if self.draw_fd is None or not commands:
            return
        payload = "\n".join(commands + ["--batch flush", "--end", ""]).encode("utf-8")
        if len(self._pending_draw) + len(payload) > MAX_PENDING_DRAW:
            logging.error("[feather_screen] pending draw data exceeded %d bytes; "
                          "restarting renderer", MAX_PENDING_DRAW)
            self._pending_draw = bytearray()
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
            return
        self._pending_draw.extend(payload)
        self._drain_draw_queue()

    def _schedule_draw_retry(self):
        if self._retry_scheduled or self._retry_scheduler is None:
            return
        self._retry_scheduled = True
        self._retry_scheduler(self._retry_draw)

    def _retry_draw(self, eventtime):
        self._retry_scheduled = False
        self._drain_draw_queue()

    def _drain_draw_queue(self):
        if self.draw_fd is None or not self._pending_draw:
            return
        try:
            while self._pending_draw:
                written = os.write(self.draw_fd, self._pending_draw)
                if written == 0:
                    raise OSError("typer draw pipe closed")
                del self._pending_draw[:written]
            # Do not retain a large allocation after an earlier partial write.
            self._pending_draw = bytearray()
        except OSError as error:
            if error.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                self._schedule_draw_retry()
                return
            logging.exception("[feather_screen] unable to draw")

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

    @staticmethod
    def fill(x, y, width, height, color=COLOR_BG):
        return "--batch fill -p %d %d -s %d %d -c %s" % (
            x, y, width, height, color)

    @staticmethod
    def stroke(x, y, width, height, color=COLOR_CYAN, line_width=2):
        return "--batch stroke -p %d %d -s %d %d -c %s -lw %d -sd inner" % (
            x, y, width, height, color, line_width)

    @classmethod
    def text(cls, x, y, value, color=COLOR_CYAN,
             font="JetBrainsMono 12pt",
             h_align="left", v_align="middle"):
        font = cls.normalize_font(font)
        value = str(value)
        # argparse treats a text value beginning with '-' as another option.
        # A leading space is visually harmless with centered text and keeps
        # labels such as -5 visible.
        if value.startswith("-"):
            value = " " + value
        return "--batch text -p %d %d -c %s -f %s -ha %s -va %s -t %s" % (
            x, y, color, cls.quote(font), h_align, v_align, cls.quote(value))

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
    def text_width(cls, value, font):
        """Return the exact monospaced advance used by typer for ASCII UI text."""
        normalized = cls.normalize_font(font)
        match = cls.FONT_PATTERN.match(normalized)
        return len(str(value)) * cls.FONT_ADVANCE[int(match.group(2))]

    @classmethod
    def truncate_text(cls, value, width, font, ellipsis="..."):
        """Shorten monospaced text to pixels without changing its font size."""
        value = str(value)
        normalized = cls.normalize_font(font)
        match = cls.FONT_PATTERN.match(normalized)
        advance = cls.FONT_ADVANCE[int(match.group(2))]
        max_chars = max(0, int(width) // advance)
        if len(value) <= max_chars:
            return value
        if max_chars <= len(ellipsis):
            return ellipsis[:max_chars]
        return value[:max_chars - len(ellipsis)] + ellipsis

    @staticmethod
    def hitbox(action, x, y, width, height):
        return "--batch hitbox --id %s -p %d %d -s %d %d" % (
            action, x, y, width, height)

    @classmethod
    def composite_button(cls, action, x, y, width, height, label, state, font,
                         include_hitbox=True):
        background, border, text_color = cls.BUTTON_COLORS[state]
        display_label = str(label)
        if display_label.startswith("-"):
            display_label = " " + display_label
        command = ("--batch button -p %d %d -s %d %d --background %s "
                   "--border %s --text-color %s -lw 2 -f %s -t %s" %
                   (x, y, width, height, background, border, text_color,
                    cls.quote(cls.normalize_font(font)), cls.quote(display_label)))
        if include_hitbox and state not in ("disabled", "busy"):
            command += " --id %s" % action
        return [command]

    @classmethod
    def _button_commands(cls, action, x, y, width, height, label, state,
                         font, subtitle=None, include_hitbox=True,
                         layout="center"):
        background, border, text_color = cls.BUTTON_COLORS[state]
        if layout == "center" and subtitle is None:
            return cls.composite_button(action, x, y, width, height, label,
                                        state, font, include_hitbox)
        commands = [
            cls.fill(x, y, width, height, background),
            cls.stroke(x, y, width, height, border, 2),
        ]
        if layout == "row":
            commands.append(cls.text(x + 24, y + height // 2, label,
                                     text_color, font, "left", "middle"))
            if subtitle is not None:
                lines = subtitle if isinstance(subtitle, (tuple, list)) else (subtitle,)
                start_y = y + height // 2 - (13 if len(lines) > 1 else 0)
                for index, line in enumerate(lines[:2]):
                    commands.append(cls.text(
                        x + 285, start_y + index * 26, line,
                        COLOR_TEXT, "JetBrainsMono 8pt", "left", "middle"))
            commands.append(cls.text(x + width - 24, y + height // 2, ">",
                                     text_color, "JetBrainsMono 16pt",
                                     "right", "middle"))
        else:
            label_y = y + height // 2 if subtitle is None else y + height // 2 - 14
            commands.append(cls.text(x + width // 2, label_y, label, text_color,
                                     font, "center", "middle"))
            if subtitle is not None:
                commands.append(cls.text(x + width // 2, y + height // 2 + 24,
                                         subtitle, COLOR_DIM,
                                         "JetBrainsMono 8pt", "center", "middle"))
        if include_hitbox and state not in ("disabled", "busy"):
            commands.append(cls.hitbox(action, x, y, width, height))
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
                        "295c66", 1),
            self.fill(10, 6, 780, HEADER_BOTTOM - 6, COLOR_PANEL),
        ]
        if back:
            commands += self.button("nav.back", 14, 7, 146, 46, "< BACK",
                                    font="JetBrainsMono Bold 8pt")
        title = self.truncate_text(str(title).upper(), 440,
                                   "JetBrainsMono 12pt")
        commands += [
            self.text(400, 29, title, COLOR_CYAN,
                      "JetBrainsMono 12pt",
                      "center", "middle"),
            self.fill(18, HEADER_BOTTOM, 764, 1, "295c66"),
            self.fill(18, FOOTER_Y - 2, 764, 1, "295c66"),
        ]
        if self._busy_label is not None:
            commands += [
                self.fill(646, 9, 136, 38, COLOR_PANEL),
                self.stroke(646, 9, 136, 38, COLOR_AMBER, 2),
                self.text(714, 28, self._busy_label, COLOR_AMBER,
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
        self.send([
            self.fill(150, 401, 500, 40, COLOR_BG),
            self.stroke(150, 401, 500, 40, COLOR_VIOLET, 2),
            self.text(400, 421, str(message).upper(), COLOR_TEXT,
                      "JetBrainsMono 8pt", "center"),
        ])

    def busy_notice(self, label="KLIPPER BUSY"):
        label = str(label).upper()
        if label == self._busy_label:
            return
        self._busy_label = label
        self.send([
            self.fill(646, 9, 136, 38, COLOR_PANEL),
            self.stroke(646, 9, 136, 38, COLOR_AMBER, 2),
            self.text(714, 28, label, COLOR_AMBER,
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
            self.send([self.fill(646, 9, 136, 38, COLOR_PANEL)])

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


def rectangles_overlap(first, second):
    """Test helper used to keep page hitboxes away from the footer."""
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah
