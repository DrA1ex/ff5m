## Low-level Typer renderer for Feather UI object trees.
##
## This module deliberately contains no Klipper state or business logic. The
## declarative layout engine and components live next to it in the ui package.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import enum
import logging
import math

from .actions import Action, action_wire_id
from .font_metrics import (
    get_font_metrics, load_runtime_metrics, set_font_metrics,
)
from .numeric_input import NumericInputSpec
from .theme_catalog import (
    DEFAULT_THEME, FALLBACK_THEME, OPTIONAL_THEME_ROLE_FALLBACKS,
    THEME_DIRECTORY, THEME_NAME_ALIASES, USER_THEME_DIRECTORY, ThemeCatalog,
    normalize_theme_name, with_optional_theme_roles,
)
from .render_worker import (
    MAX_BATCH_CHARS, MAX_BATCHES, RenderBatch, RenderBatchQueue,
    TyperRenderWorker,
)


DRAW_PIPE = "/tmp/typer"
EVENT_PIPE = "/tmp/feather-events"
TOUCH_DEVICE = "/dev/input/guppy"
TYPER_BINARY = "/root/printer_data/bin/typer"
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
MAX_PENDING_DRAW = MAX_BATCH_CHARS
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

# Theme constants remain importable from this module for compatibility. Their
# source of truth and lifecycle now live in ui.theme_catalog.


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
    PARAMETER_OPTIONS = 27
    MOD_VALUE = 28
    ERROR = 29
    LIVE_Z_OFFSET = 30
    Z_OFFSET_SUMMARY = 31
    Z_OFFSET_PAPER = 32
    Z_OFFSET_PAPER_BRIEFING = 33
    ACTION_PROMPT = 34
    CALIBRATION_GUIDE = 35
    SAFE_Z_BRIEFING = 36
    SAFE_Z_CALIBRATION = 37
    EXTRUDER_CALIBRATION = 38


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
        "keypad": ("primary_dark", "primary", "bright"),
        "keypad_aux": ("panel", "secondary", "secondary"),
        "keypad_confirm": ("primary_dark", "primary", "bright"),
    }
    BUTTON_TEXT_PADDING = 16
    HINT_TEXT_PADDING = 20
    DIALOG_TEXT_PADDING = 28
    def __init__(self, debug=False, theme_directories=None):
        self.debug = debug
        self._theme_directories = tuple(
            theme_directories or (THEME_DIRECTORY, USER_THEME_DIRECTORY))
        self._theme_catalog = ThemeCatalog.from_directories(
            self._theme_directories)
        self._themes = {}
        self._theme_descriptions = {}
        self._user_theme_issues = ()
        self._theme_name = DEFAULT_THEME
        self._palette = with_optional_theme_roles(FALLBACK_THEME)
        self.reload_themes()
        self.event_fd = None
        self._last_footer = None
        self._footer_values = None
        self._footer_drawn = False
        self._buttons = {}
        self._toggles = {}
        self._hitboxes = {}
        self._generation = 0
        self._batch_queue = RenderBatchQueue(MAX_BATCHES, MAX_BATCH_CHARS)
        self._worker = None
        self._async_scheduler = None
        self._event_fd_handler = None
        self._restart_handler = None
        self._last_submitted_generation = -1
        self._next_batch_kind = None
        self._next_batch_key = None
        self._busy_label = None
        self._emergency_stop_visible = False
        self._menu_suppressed = False
        self._loader_active = False
        self._output_frozen = False
        self._font_manifest_loaded = False
        self._semantic_page_id = None

    def configure_worker(self, async_scheduler, event_fd_handler,
                         restart_handler=None):
        """Connect worker callbacks before its deferred reactor start."""
        self._async_scheduler = async_scheduler
        self._event_fd_handler = event_fd_handler
        self._restart_handler = restart_handler

    @property
    def active(self):
        return self._worker is not None and self._worker.active

    @property
    def output_frozen(self):
        return self._output_frozen

    def discard_pending_output(self):
        """Drop untouched ordinary batches, preserving critical screens."""
        self._batch_queue.discard_noncritical()

    def freeze_output(self):
        """Keep the last submitted safety screen as the sole display owner."""
        self._output_frozen = True

    def thaw_output(self):
        self._output_frozen = False

    @staticmethod
    def quote(value):
        value = str(value).replace("\r", " ").replace("\n", " ")
        value = value.replace("\\", "\\\\").replace('"', '\\"')
        return '"%s"' % value

    def start(self):
        """Start only the daemon worker; Typer is launched in that thread."""
        self._output_frozen = False
        if self._worker is not None:
            return self._worker.start()
        if self._async_scheduler is None or self._event_fd_handler is None:
            raise RuntimeError("Typer worker callbacks are not configured")

        def load_fonts():
            if not self._font_manifest_loaded:
                set_font_metrics(load_runtime_metrics(TYPER_BINARY))
                self._font_manifest_loaded = True

        self._worker = TyperRenderWorker(
            self._batch_queue, self._encode_frames, self.debug,
            (TYPER_BINARY, DRAW_PIPE, EVENT_PIPE, TOUCH_DEVICE),
            self._async_scheduler, self._worker_event_fd_changed,
            self._worker_restarted, load_fonts)
        started = self._worker.start()
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
        ], kind="critical", key="worker-clear")
        return started

    def _worker_event_fd_changed(self, old_fd, new_fd):
        self.event_fd = new_fd
        self._event_fd_handler(old_fd, new_fd)

    def _worker_restarted(self):
        if self._restart_handler is not None:
            self._restart_handler()

    def stop(self):
        """Signal shutdown without waiting for worker or Typer."""
        if self._worker is not None:
            self._worker.request_stop()
        self._busy_label = None
        self._last_footer = None
        self._footer_drawn = False
        self._output_frozen = False

    def restart(self):
        if self._worker is not None:
            return self._worker.request_restart()
        return False

    def send(self, commands, kind=None, key=None, generation=None):
        """Publish one immutable batch; never perform IO or lifecycle work."""
        if self._output_frozen or not commands:
            return False
        immutable = tuple(str(command) for command in commands)
        batch_generation = (self._generation if generation is None
                            else int(generation))
        if kind is None and self._next_batch_kind is not None:
            kind, key = self._next_batch_kind, self._next_batch_key
        self._next_batch_kind = None
        self._next_batch_key = None
        if kind is None:
            kind = ("surface" if batch_generation !=
                    self._last_submitted_generation else "state")
        if kind not in ("critical", "surface", "state", "animation"):
            raise ValueError("unknown render batch kind: %s" % kind)
        character_count = sum(len(command) for command in immutable)
        batch = RenderBatch(
            immutable, kind, key, batch_generation, character_count, None)
        accepted = self._batch_queue.put_nowait(batch)
        if accepted:
            self._last_submitted_generation = max(
                self._last_submitted_generation, batch_generation)
        return accepted

    def prioritize_next_batch(self, kind, key=None):
        """Classify the following legacy one-argument send() call."""
        self._next_batch_kind = kind
        self._next_batch_key = key

    def send_animation(self, commands, key):
        return self.send(commands, kind="animation", key=key)

    def get_status(self):
        if self._worker is None:
            status = self._batch_queue.snapshot()
            status.update({
                "worker_state": "stopped", "typer_restarts": 0,
                "worker_last_error": "",
            })
        else:
            status = self._worker.snapshot()
        status["semantic_page_id"] = self._semantic_page_id
        return status

    def set_semantic_page(self, page_id):
        self._semantic_page_id = str(page_id)

    @staticmethod
    def _encode_frames(commands):
        suffix = ["--batch flush", "--end", ""]
        frames = []
        current = []
        size = len("\n".join(suffix).encode("utf-8"))
        for command in commands:
            line_size = len((str(command) + "\n").encode("utf-8"))
            if size + line_size > MAX_ATOMIC_DRAW and not current:
                raise ValueError(
                    "single Typer command exceeds MAX_ATOMIC_DRAW")
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
        logical = action_wire_id(action) if isinstance(action, Action) else str(action)
        return "%d:%s" % (self._generation, logical)

    @property
    def generation(self):
        return self._generation

    @property
    def theme_name(self):
        return self._theme_name

    def _sync_theme_catalog(self):
        previous_name = self._theme_name
        previous_palette = self._palette
        self._themes = self._theme_catalog.themes
        self._theme_descriptions = self._theme_catalog.descriptions
        self._user_theme_issues = self._theme_catalog.user_issues
        if self._theme_name not in self._themes:
            self._theme_name = DEFAULT_THEME
        self._palette = self._themes[self._theme_name]
        if (self._theme_name != previous_name
                or self._palette != previous_palette):
            self._last_footer = None
            self._footer_drawn = False
        return tuple(self._themes)

    def reload_themes(self):
        """Reload the complete catalog at construction and klippy:ready."""
        self._theme_catalog.reload_all()
        return self._sync_theme_catalog()

    def reload_user_themes(self):
        """Refresh writable user themes without rescanning bundled files."""
        self._theme_catalog.reload_user_themes()
        return self._sync_theme_catalog()

    def ensure_user_theme_directory(self):
        self._theme_catalog.ensure_user_directories()

    def theme_names(self, reload=False):
        # ``reload=True`` is retained for callers outside this package. A UI
        # refresh must never rescan immutable bundled themes.
        if reload:
            self.reload_user_themes()
        return tuple(self._themes)

    def theme_description(self, name):
        return self._theme_catalog.description(name)

    def user_theme_issues(self):
        return tuple(self._user_theme_issues)

    def set_theme(self, name):
        normalized = normalize_theme_name(name)
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
        label = str(message).upper()
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
            font, "center", "middle", max_width=available, truncate=True))
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

    def numeric_keypad(self, x, y, width, height, title, value, actions,
                       subtitle="", mode="decimal", minimum=None,
                       maximum=None, max_length=10, fraction_digits=None,
                       confirm_label="CONFIRM", border="border",
                       background="panel", title_color="text",
                       subtitle_color="dim", input_border="secondary",
                       value_color="bright"):
        """Draw a self-contained numeric entry window inside page chrome."""
        spec = (mode if isinstance(mode, NumericInputSpec) else
                NumericInputSpec(mode, minimum, maximum, max_length,
                                 fraction_digits))
        actions = dict(actions or {})
        x, y, width, height = map(int, (x, y, width, height))
        padding = max(5, min(10, width // 80, height // 45))
        gap = max(6, min(10, width // 90))
        inner_x = x + padding
        inner_width = width - padding * 2
        title_height = 44
        input_height = 50
        input_gap = 10
        confirm_height = 54
        confirm_gap = 10
        row_gap = 7
        keys_top = y + padding + title_height + input_height + input_gap
        keys_height = (height - padding * 2 - title_height - input_height
                       - input_gap - confirm_gap - confirm_height)
        key_height = max(1, (keys_height - row_gap * 3) // 4)
        confirm_y = y + height - padding - confirm_height
        key_width = max(1, (inner_width - gap * 2) // 3)
        commands = self.panel(
            x, y, width, height, border=border, background=background,
            line_width=1)
        commands.append(self.text(
            x + width // 2,
            y + padding + (12 if subtitle else title_height // 2),
            str(title).upper(), title_color, "JetBrainsMono Bold 8pt",
            "center", "middle",
            max_width=inner_width - 24, truncate=True))
        if subtitle:
            commands.append(self.text(
                x + width // 2, y + padding + 31,
                str(subtitle).upper(), subtitle_color,
                "JetBrainsMono 8pt", "center", "middle",
                max_width=inner_width - 24, truncate=True))
        input_y = y + padding + title_height
        commands += self.panel(
            inner_x, input_y, inner_width, input_height,
            border=input_border, background=background, line_width=2)
        sign_action = (actions.get("sign")
                       if spec.allows_negative and spec.allows_decimal
                       else None)
        sign_width = 68 if sign_action is not None else 0
        commands.append(self.text(
            inner_x + 16, input_y + input_height // 2, value or "_",
            value_color,
            "JetBrainsMono 12pt", "left", "middle",
            max_width=max(1, inner_width - 32 - sign_width), truncate=True))
        if sign_action is not None:
            commands += self.button(
                sign_action, inner_x + inner_width - sign_width, input_y,
                sign_width, input_height, "+/-", state="keypad_aux",
                font="JetBrainsMono 8pt")
        auxiliary = "decimal" if spec.allows_decimal else (
            "sign" if spec.allows_negative else None)
        rows = (("1", "2", "3"), ("4", "5", "6"),
                ("7", "8", "9"), (auxiliary, "0", "backspace"))
        for row, keys in enumerate(rows):
            key_y = keys_top + row * (key_height + row_gap)
            for column, token in enumerate(keys):
                key_x = inner_x + column * (key_width + gap)
                label = ({"decimal": ".", "sign": "+/-",
                          "backspace": "BACK"}.get(token, token) or "-")
                action = actions.get(token) if token is not None else None
                if action is None:
                    state = "disabled"
                elif token in ("decimal", "sign", "backspace"):
                    state = "keypad_aux"
                else:
                    state = "keypad"
                commands += self.button(
                    action or "numeric.disabled.%d.%d" % (row, column),
                    key_x, key_y, key_width, key_height, label,
                    state=state,
                    font=("JetBrainsMono 8pt" if token == "backspace"
                          else "JetBrainsMono 12pt"))
        confirm_action = actions.get("confirm")
        commands += self.button(
            confirm_action or "numeric.disabled.confirm", inner_x, confirm_y,
            inner_width, confirm_height, confirm_label,
            state=("keypad_confirm" if confirm_action is not None
                   and spec.is_valid(value) else "disabled"),
            font="JetBrainsMono Bold 10pt")
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
             font="JetBrainsMono 12pt", h_align="left", v_align="middle",
             max_width=None, max_height=None, wrap=False, truncate=False,
             proportional=False):
        font = self.normalize_font(font, proportional)
        value = str(value)
        if (wrap or truncate) and (max_width is None or int(max_width) <= 0):
            raise ValueError("wrap and truncate require max_width")
        if wrap and (max_height is None or int(max_height) <= 0):
            raise ValueError("wrap requires max_height")
        # argparse treats a text value beginning with '-' as another option.
        # Keep the protective space out of the visible alignment calculation,
        # otherwise centered labels such as -Y appear half a glyph off-center.
        if value.startswith("-"):
            value = " " + value
            advance = get_font_metrics().metric(font).character_advance(" ")
            if h_align == "center":
                x -= advance // 2
            elif h_align == "left":
                x -= advance
        command = "--batch text -p %d %d -c %s -f %s -ha %s -va %s" % (
            x, y, self.color(color), self.quote(font), h_align, v_align)
        if max_width is not None:
            command += " --max-width %d" % int(max_width)
        if max_height is not None:
            command += " --max-height %d" % int(max_height)
        if wrap:
            command += " --wrap"
        if truncate:
            command += " --truncate"
        return command + " -t %s" % self.quote(value)

    @classmethod
    def available_fonts(cls):
        """Return the exact font names compiled into Typer."""
        return get_font_metrics().available_fonts()

    @classmethod
    def normalize_font(cls, font, allow_proportional=False):
        """Map UI font requests to sizes actually compiled into typer."""
        return get_font_metrics().normalize_font(font, allow_proportional)

    @classmethod
    def font_advance(cls, font):
        metric = get_font_metrics().metric(font)
        if not metric.monospaced:
            raise ValueError("font is not monospaced: %s" % metric.name)
        return metric.advance_x

    @classmethod
    def text_width(cls, value, font):
        """Return the exact monospaced advance used by typer for ASCII UI text."""
        return get_font_metrics().text_width(value, font)

    @staticmethod
    def hitbox(action, x, y, width, height, continuous=False):
        command = "--batch hitbox --id %s -p %d %d -s %d %d" % (
            action, x, y, width, height)
        return command + (" --continuous" if continuous else "")

    def action_hitbox(self, action, x, y, width, height, continuous=False):
        logical_action = (
            action_wire_id(action) if isinstance(action, Action) else str(action))
        self._hitboxes[logical_action] = (
            x, y, width, height, bool(continuous))
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
        logical_action = action_wire_id(action) if isinstance(action, Action) else str(action)
        inset = 5
        thumb_size = max(1, height - 2 * inset)
        half = width // 2
        left = x + (half - thumb_size) // 2
        right = x + half + (half - thumb_size) // 2
        thumb_x = right if active else left
        self._toggles[logical_action] = (
            x, y, width, height, bool(active), enabled, left, right)
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
            self.prioritize_next_batch("animation", "toggle:%s" % action)
            self.send(self._toggle_commands(
                x, y, width, height, thumb_x, enabled))

        draw_frame(None, 1)
        for index in range(2, frames + 1):
            scheduler(lambda eventtime, step=index:
                      draw_frame(eventtime, step),
                      duration * (index - 1) / (frames - 1))
        return True

    def block_input(self):
        self._buttons = {}
        self._toggles = {}
        self._hitboxes = {}
        self.send([
            "--batch clear-hitboxes",
            self._wake_hitbox(),
        ])

    def _wake_hitbox(self):
        # Typer gives later overlapping hitboxes precedence.  Register the
        # background first so an otherwise empty-area tap is observable while
        # every real button added afterwards keeps its normal action.
        return self.action_hitbox(
            "global.wake", 0, 0, SCREEN_WIDTH, SCREEN_HEIGHT)

    def _button_colors(self, state):
        return tuple(self.color(color) for color in self.BUTTON_COLORS[state])

    def composite_button(self, action, x, y, width, height, label, state, font,
                         include_hitbox=True):
        background, border, text_color = self._button_colors(state)
        display_label = str(label)
        if display_label.startswith("-"):
            display_label = " " + display_label
        max_width = max(1, width - 2 * self.BUTTON_TEXT_PADDING)
        command = ("--batch button -p %d %d -s %d %d --background %s "
                   "--border %s --text-color %s -lw 2 -f %s "
                   "--max-width %d --truncate -t %s" %
                   (x, y, width, height, background, border, text_color,
                    self.quote(self.normalize_font(font)),
                    max_width, self.quote(display_label)))
        if include_hitbox and state not in ("disabled", "busy"):
            command += " --id %s" % action
        return [command]

    def _button_commands(self, action, x, y, width, height, label, state,
                         font, subtitle=None, include_hitbox=True,
                         layout="center", subtitle_font="JetBrainsMono 8pt",
                         subtitle_color=COLOR_DIM):
        if isinstance(subtitle, (tuple, list)):
            subtitle_lines = tuple(
                str(line) for line in subtitle if str(line).strip())[:2]
        elif subtitle is None or not str(subtitle).strip():
            subtitle_lines = ()
        else:
            subtitle_lines = (str(subtitle),)
        subtitle = subtitle_lines or None
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
                subtitle_x = x + min(285, max(24, width // 2 - 15))
                subtitle_width = max(1, x + width - 58 - subtitle_x)
                for index, line in enumerate(lines[:2]):
                    commands.append(self.text(
                        subtitle_x, start_y + index * 26, line,
                        subtitle_color, subtitle_font, "left", "middle",
                        max_width=subtitle_width, truncate=True))
            commands.append(self.text(x + width - 24, y + height // 2, ">",
                                      text_color, "JetBrainsMono 16pt",
                                      "right", "middle"))
        else:
            label_y = y + height // 2 if subtitle is None else y + height // 2 - 14
            commands.append(self.text(x + width // 2, label_y, label,
                                      text_color, font, "center", "middle",
                                      max_width=width - 2 * self.BUTTON_TEXT_PADDING,
                                      truncate=True))
            if subtitle is not None:
                start_y = y + height // 2 + (18 if len(subtitle) > 1 else 24)
                for index, line in enumerate(subtitle):
                    commands.append(self.text(
                        x + width // 2, start_y + index * 18, line,
                        subtitle_color, subtitle_font, "center", "middle"))
        if include_hitbox and state not in ("disabled", "busy"):
            commands.append(self.hitbox(action, x, y, width, height))
        return commands

    def _arrow_button_commands(self, action, x, y, width, height, direction,
                               state, include_hitbox=True):
        """Draw a full geometric arrow without relying on font glyphs."""
        background, border, arrow_color = self._button_colors(state)
        commands = [
            self.fill(x, y, width, height, background),
            self.stroke(x, y, width, height, border, 2),
        ]
        center_x = x + width // 2
        center_y = y + height // 2
        if direction == "up":
            commands.append(self.fill(
                center_x - 3, center_y - 1, 7, 12, arrow_color))
            widths = (3, 7, 11, 15, 19)
            head_y = center_y - 11
        elif direction == "down":
            commands.append(self.fill(
                center_x - 3, center_y - 11, 7, 12, arrow_color))
            widths = (19, 15, 11, 7, 3)
            head_y = center_y + 1
        else:
            raise ValueError("Arrow direction must be 'up' or 'down'")
        for index, arrow_width in enumerate(widths):
            commands.append(self.fill(
                center_x - arrow_width // 2, head_y + index * 2,
                arrow_width, 2, arrow_color))
        if include_hitbox and state not in ("disabled", "busy"):
            commands.append(self.hitbox(action, x, y, width, height))
        return commands

    def button(self, action, x, y, width, height, label, active=None,
               state="enabled", font="JetBrainsMono 12pt", subtitle=None,
               layout="center", subtitle_font="JetBrainsMono 8pt",
               subtitle_color=COLOR_DIM):
        logical_action = action_wire_id(action) if isinstance(action, Action) else str(action)
        # active is retained for compatibility with the first Feather release.
        if active is not None:
            state = "enabled" if active else "disabled"
        if state not in self.BUTTON_COLORS:
            state = "enabled"
        if layout == "center":
            font = self.normalize_font(font)
        if state not in ("disabled", "busy"):
            self._buttons[logical_action] = (
                x, y, width, height, label, state, font, subtitle, layout,
                subtitle_font, subtitle_color)
        if (logical_action == "nav.menu"
                and (self._busy_label is not None
                     or self._emergency_stop_visible)):
            self._menu_suppressed = True
            return []
        if logical_action == "nav.menu":
            self._menu_suppressed = False
        return self._button_commands(self._wire_action(action), x, y, width,
                                     height, label, state, font, subtitle,
                                     True, layout, subtitle_font,
                                     subtitle_color)

    def arrow_button(self, action, x, y, width, height, direction,
                     active=None, state="enabled"):
        """Build an up/down button with a geometric, theme-aware arrow."""
        logical_action = (action_wire_id(action)
                          if isinstance(action, Action) else str(action))
        if active is not None:
            state = "enabled" if active else "disabled"
        if state not in self.BUTTON_COLORS:
            state = "enabled"
        direction = str(direction).lower()
        if direction not in ("up", "down"):
            raise ValueError("Arrow direction must be 'up' or 'down'")
        if state not in ("disabled", "busy"):
            self._buttons[logical_action] = (
                x, y, width, height, direction, state, None, None,
                "arrow-" + direction, None, None)
        return self._arrow_button_commands(
            self._wire_action(action), x, y, width, height, direction, state)

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
        preserve_emergency = self._emergency_stop_visible
        if modal:
            self._buttons = {}
            self._toggles = {}
            self._hitboxes = {}
            commands += ["--batch clear-hitboxes", self._wake_hitbox()]
        commands += self.panel(
            x, y, width, height, border=border, background=COLOR_PANEL)
        commands.append(self.text(
            x + width // 2, y + 34, str(title).upper(), border,
            "JetBrainsMono Bold 16pt", "center", "middle",
            max_width=width - 2 * self.DIALOG_TEXT_PADDING, truncate=True))
        for index, line in enumerate(tuple(lines)[:4]):
            commands.append(self.text(
                x + width // 2, y + 78 + index * 24, str(line), COLOR_TEXT,
                "JetBrainsMono 8pt", "center", "middle",
                max_width=width - 2 * self.DIALOG_TEXT_PADDING,
                truncate=True))
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
        if modal and preserve_emergency:
            commands += self._emergency_stop_commands()
        return commands

    def flash_button(self, action):
        spec = self._buttons.get(action)
        if spec is None:
            return False
        (x, y, width, height, label, _state, font, subtitle, layout,
         subtitle_font, subtitle_color) = spec
        self.prioritize_next_batch("animation", "button:%s" % action)
        if layout in ("arrow-up", "arrow-down"):
            self.send(self._arrow_button_commands(
                action, x, y, width, height, layout[6:], "pressed", False))
        else:
            self.send(self._button_commands(
                action, x, y, width, height, label, "pressed", font,
                subtitle, False, layout, subtitle_font, subtitle_color))
        return True

    def restore_button(self, action):
        spec = self._buttons.get(action)
        if spec is None:
            return False
        (x, y, width, height, label, state, font, subtitle, layout,
         subtitle_font, subtitle_color) = spec
        self.prioritize_next_batch("state", "button:%s" % action)
        if layout in ("arrow-up", "arrow-down"):
            self.send(self._arrow_button_commands(
                action, x, y, width, height, layout[6:], state, False))
        else:
            self.send(self._button_commands(
                action, x, y, width, height, label, state, font, subtitle,
                False, layout, subtitle_font, subtitle_color))
        return True

    def set_emergency_stop_visible(self, visible):
        visible = bool(visible)
        if visible == self._emergency_stop_visible:
            return False
        self._emergency_stop_visible = visible
        if not visible:
            self._buttons.pop("global.abort", None)
        return True

    def _emergency_stop_commands(self):
        return self.button(
            "global.abort", 648, 7, 132, 46, "ABORT",
            state="danger", font="JetBrainsMono Bold 8pt")

    def begin_page(self, title, back=False):
        self._loader_active = False
        self._semantic_page_id = None
        self._generation += 1
        self._buttons = {}
        self._toggles = {}
        self._hitboxes = {}
        self._menu_suppressed = False
        show_abort = self._emergency_stop_visible
        commands = [
            "--batch clear-hitboxes",
            self._wake_hitbox(),
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
        title = str(title).upper()
        commands += [
            self.text(400, 29, title, "header_text",
                      "JetBrainsMono 12pt",
                      "center", "middle", max_width=440, truncate=True),
            self.fill(18, HEADER_BOTTOM, 764, 1, "header_border"),
            self.fill(18, FOOTER_Y - 2, 764, 1, "295c66"),
        ]
        if self._busy_label is not None and not show_abort:
            busy_label = self._busy_label
            commands += [
                self.fill(622, 9, 160, 38, "header_background"),
                self.stroke(622, 9, 160, 38, COLOR_AMBER, 2),
                self.text(702, 28, busy_label, COLOR_AMBER,
                          "JetBrainsMono Bold 8pt", "center", "middle",
                          max_width=132, truncate=True),
            ]
        if show_abort:
            commands += self._emergency_stop_commands()
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
                      "JetBrainsMono 8pt", max_width=380, truncate=True),
            self.text(780, FOOTER_Y + 16, right, COLOR_CYAN,
                      "JetBrainsMono 8pt", "right", max_width=340,
                      truncate=True),
        ]

    def footer(self, nozzle, nozzle_target, bed, bed_target, network, state):
        values = (round(nozzle, 1), round(nozzle_target), round(bed, 1),
                  round(bed_target), network, state)
        self._footer_values = values
        if values == self._last_footer:
            return
        self._last_footer = values
        self._footer_drawn = True
        self.prioritize_next_batch("state", "footer")
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
        if self._emergency_stop_visible:
            return
        self.send([
            self.fill(622, 9, 160, 38, "header_background"),
            self.stroke(622, 9, 160, 38, COLOR_AMBER, 2),
            self.text(702, 28, label, COLOR_AMBER,
                      "JetBrainsMono Bold 8pt", "center", "middle",
                      max_width=132, truncate=True),
        ])

    def clear_busy_notice(self):
        if self._busy_label is None:
            return
        self._busy_label = None
        if self._emergency_stop_visible:
            return
        menu = self._buttons.get("nav.menu")
        if menu is not None:
            (x, y, width, height, label, state, font, subtitle, layout,
             subtitle_font, subtitle_color) = menu
            self.send(self._button_commands(
                "nav.menu", x, y, width, height, label, state, font,
                subtitle, self._menu_suppressed, layout, subtitle_font,
                subtitle_color))
            self._menu_suppressed = False
        else:
            self.send([
                self.fill(622, 9, 160, 38, "header_background")])

    def loader(self, message, phase=0):
        """Replace the page with a non-interactive yielding-operation view."""
        # A loader is a new interaction surface, not a partial repaint. Bump
        # the generation so already queued Back/page events cannot target the
        # page underneath it.
        first_frame = not self._loader_active
        if first_frame:
            self._generation += 1
            self._loader_active = True
        preserve_emergency = self._emergency_stop_visible
        self._buttons = {}
        self._toggles = {}
        self._hitboxes = {}
        commands = [
            "--batch clear-hitboxes",
            self._wake_hitbox(),
            # Clear the header too: leaving the old Back button painted made
            # it look usable even though its hitbox had been removed.
            self.fill(0, 0, SCREEN_WIDTH, CONTENT_BOTTOM, COLOR_BG),
            self.stroke(FRAME_X, FRAME_Y, FRAME_WIDTH,
                        FOOTER_Y - FRAME_Y - 1, "header_border", 1),
            self.fill(10, 6, 780, HEADER_BOTTOM - 6, "header_background"),
            self.text(400, 29, "OPERATION IN PROGRESS", "header_text",
                      "JetBrainsMono 12pt", "center", "middle",
                      max_width=440, truncate=True),
            self.fill(18, HEADER_BOTTOM, 764, 1, "header_border"),
            self.fill(18, FOOTER_Y - 2, 764, 1, "295c66"),
            self.text(400, 190, message, COLOR_TEXT, "JetBrainsMono Bold 16pt",
                      "center", "middle"),
            self.text(400, 235, "PLEASE WAIT", COLOR_DIM, "JetBrainsMono 12pt",
                      "center", "middle"),
        ]
        for index in range(5):
            color = COLOR_CYAN if index == phase % 5 else "263238"
            commands.append(self.fill(290 + index * 48, 280, 32, 12, color))
        if preserve_emergency:
            commands += self._emergency_stop_commands()
        self.prioritize_next_batch(
            "surface" if first_frame else "animation", "loader")
        self.send(commands)

    def startup_modal(self, phase=0, restarting=False):
        """Draw the pre-ready Klipper loading modal and its pulse frame."""
        # This full-screen modal owns the framebuffer until Klipper is ready.
        # Invalidate animations scheduled by the page underneath it so a late
        # toggle/button frame cannot be painted over the restart screen.
        self._generation += 1
        self._loader_active = True
        self._buttons = {}
        self._toggles = {}
        self._hitboxes = {}
        detail = ("RESTART IN PROGRESS - DISPLAY MAY PAUSE"
                  if restarting else "INITIALIZING PRINTER SERVICES")
        commands = [
            "--batch clear-hitboxes",
            self._wake_hitbox(),
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
        self.prioritize_next_batch(
            "critical" if restarting else "surface", "startup")
        self.send(commands)

    def startup_pulse(self, phase=0):
        """Redraw only the animated startup indicator's small dirty area."""
        pulse = (8, 12, 16, 12)[int(phase) % 4]
        commands = [self.fill(374, 206, 53, 53, COLOR_PANEL)]
        commands += self.filled_circle(400, 232, pulse, COLOR_VIOLET)
        return commands

    def applying_modal(self, message="APPLYING CHANGES"):
        """Dim the page and draw a non-interactive modal progress panel."""
        self._generation += 1
        self._loader_active = True
        self._buttons = {}
        self._toggles = {}
        self._hitboxes = {}
        commands = [
            "--batch clear-hitboxes",
            self._wake_hitbox(),
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
        self.prioritize_next_batch("surface", "applying-modal")
        self.send(commands)


def rectangles_overlap(first, second):
    """Test helper used to keep page hitboxes away from the footer."""
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah
