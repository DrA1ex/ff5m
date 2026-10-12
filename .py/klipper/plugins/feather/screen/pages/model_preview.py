## Model preview benchmark and interactive views before and during printing.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import logging
import math
import os
import threading
import time

from ui import ThemeColor
from ui.lazy import LazyModule
from ui.render_receipts import ReceiptTracker
from ff5m_ui.screen import ScreenPage
from ff5m_ui.print_state import PrintState
from ff5m_ui.printing import runtime as printing_ui
from .printing import _progress_state
from feather.files import FileEntry, FileTaskSuperseded
from feather.features.manager import FeatureHostProxy
from feather.previews import PreviewCancelled


model_runtime = LazyModule("feather.model_preview")
common_ui = LazyModule("ff5m_ui.model_preview.common")
preview_ui = LazyModule("ff5m_ui.model_preview.page")
print_preview_ui = LazyModule("ff5m_ui.model_preview.print_page")


MONITOR_PERIOD = 0.1
STATUS_PERIOD = 0.2
PREVIEW_PAGES = (ScreenPage.MODEL_PREVIEW, ScreenPage.PRINT_PREVIEW)
CAMERA_TOUCH_TIMEOUT = 1.0
CAMERA_DRAW_TIMEOUT = 1.0
SLIDER_YAW_LIMIT = 150.0
SLIDER_PITCH_LIMIT = 70.0


def _preview_rect():
    box = printing_ui.rect(printing_ui.PrintingRef.PREVIEW_BOX)
    pad = printing_ui.PREVIEW_IMAGE_PADDING
    # Exercise the raster size available to the actual print page.
    return (40, 118, min(512, max(16, box.width - 2 * pad)),
            min(512, max(16, box.height - 2 * pad)))


class ModelPreviewPagesMixin:
    def cmd_FEATHER_PREVIEW_LAB(self, gcmd):
        self._require_idle()
        action = str(gcmd.get("ACTION", "OPEN")).strip().lower()
        if action == "status":
            state = getattr(self, "model_preview", None)
            if state is None:
                gcmd.respond_info("Preview lab: closed")
            else:
                gcmd.respond_info("Preview lab: %s, FPS %.1f, GEN %.1f, render %.1f ms, "
                                  "draw %.1f ms, main %.1f%%, reactor avg/max %.1f/%.1f ms, cache=%s, error=%s" % (
                    model_runtime.MODEL_STYLES[state["settings"][0]], state["fps"], state["gen_fps"], state["render_ms"],
                    state["draw_ms"], state["main_cpu"], *state["lag"], state["use_cache"], state["error"]))
                layers = state["session"].layers if state.get("session") else 0
                gcmd.respond_info("Preview build: layers=%d/%d, active=%s, direction=%d" % (
                    state["printed"], layers, state["build"], state["build_direction"]))
                gcmd.respond_info("Preview scale: %.2fx" % state["scale"])
            gcmd.respond_info("Preview renderer: %s; generation=%s; ready=%s; accepted=%s; layers=%s" % (
                self.renderer.get_status(), self.renderer.generation,
                bool(state and state["blob"]), state.get("accepted") if state else None,
                state["session"].layers if state and state.get("session") else None))
            return
        if action == "open":
            filename = gcmd.get("FILE", None)
            if filename is None:
                self._open_model_preview_browser()
                return
            root = os.path.realpath(self.virtual_sdcard.sdcard_dirname)
            path = os.path.realpath(os.path.join(root, filename))
            if not path.startswith(root + os.sep) or not os.path.isfile(path):
                raise gcmd.error("Choose a file inside the G-code directory")
            self._open_model_preview(FileEntry(os.path.basename(path), path))
        elif action == "back":
            self._go_back()
        elif self.page == ScreenPage.MODEL_PREVIEW:
            wire_id = "model." + action
            if self.model_preview["page_tree"].resolve_action(wire_id) is None:
                raise gcmd.error("Unknown preview action")
            self._handle_model_preview_action(wire_id)
        else:
            raise gcmd.error("Open the preview lab first")

    def _model_preview_render_receipt(self, receipt):
        state = getattr(self, "model_preview", None)
        if state is None:
            return
        if receipt.token.startswith("mpc:"):
            if state["cursor_receipt"].resolve(receipt, self.reactor.monotonic()) is not None:
                if receipt.success:
                    self._model_preview_cursor_commands(state)
                else:
                    state["cursor_redraw"] = True
                    if self._model_preview_paint_allowed(ScreenPage.PRINT_PREVIEW):
                        self._render_model_preview()
            return
        if not receipt.token.startswith("mpl:%d:%d:" % (state["serial"], state["generation"])):
            return
        if receipt.success:
            state["display_frames"] += 1
            state["draw_ms"] = receipt.total_us / 1000.0
        else:
            state["render_key"] = None
            state["status_at"] = 0

    def _open_model_preview_browser(self):
        self._require_idle()
        self.model_preview_file_select = True
        self.file_page, self.file_source = 0, "internal"
        self._expire_file_entries_if_stale("internal")
        self._show_page(ScreenPage.FILE_BROWSER)

    def _open_model_preview(self, entry):
        self._require_idle()
        self._start_model_preview(entry, mode="lab")

    def _open_selected_model_preview(self):
        self._require_idle()
        if self.selected_file is None:
            raise RuntimeError("Choose a print file first")
        from feather.gcode_info import read_gcode_info

        self._start_model_preview(self.selected_file, mode="file")
        state = self.model_preview
        task = lambda: read_gcode_info(state["path"], state["cancel"])

        def ready(info, error):
            if getattr(self, "model_preview", None) is not state:
                return
            if isinstance(error, PreviewCancelled):
                return
            if isinstance(error, FileTaskSuperseded):
                if self.file_worker is not None and self.file_worker.submit(task, ready):
                    return
                error = RuntimeError("File worker unavailable")
            state["file_info"] = info or {}
            if error is not None:
                logging.info("[feather_screen] file details unavailable: %s", error)
            if self._model_preview_paint_allowed(ScreenPage.PRINT_PREVIEW):
                self.renderer.send(state["page_tree"].update_refs(
                    self.renderer, self._selected_model_preview_values(state), (common_ui.PreviewRef.INFO,)),
                    kind="state", key="file-preview-info")
            self.reactor.update_timer(state["render_timer"], self.reactor.monotonic())

        if self.file_worker is None or not self.file_worker.submit(
                task, ready):
            ready(None, RuntimeError("File worker unavailable"))

    def _open_print_model_preview(self):
        path = self.virtual_sdcard.file_path()
        if self.print_state not in (PrintState.PREPARING, PrintState.PRINTING, PrintState.PAUSED) or not path:
            self._toast("NO ACTIVE PRINT FILE")
            return
        self._cancel_gcode_preview()
        self._start_model_preview(FileEntry(os.path.basename(path), path), mode="print")

    def _model_preview_settings(self, mode):
        settings = model_runtime.model_settings(self._setting) or (4, 192, 0.0, 30.0, False, 24.0, 30, 1.0, 0)
        if mode != "lab":
            # Saved angles and reset must stay within the interactive rails.
            settings = settings[:2] + (
                min(SLIDER_YAW_LIMIT, max(-SLIDER_YAW_LIMIT, settings[2])),
                min(SLIDER_PITCH_LIMIT, max(-SLIDER_PITCH_LIMIT, settings[3])),
            ) + settings[4:]
        return settings

    def _start_model_preview(self, entry, mode):
        self._close_model_preview()
        settings = self._model_preview_settings(mode)
        now = self.reactor.monotonic()
        self._model_preview_serial = getattr(self, "_model_preview_serial", 0) + 1
        _x, _y, width, height = _preview_rect()
        self.model_preview = {
            "page_tree": (preview_ui.create_page(width, height) if mode == "lab" else
                          print_preview_ui.create_page(before_print=mode == "file")),
            "mode": mode, "cancel": threading.Event(), "file_info": None,
            "serial": self._model_preview_serial, "frame_number": 0,
            "path": entry.path, "settings": settings, "yaw": settings[2], "pitch": settings[3],
            "scale": settings[7],
            "touch_action": None, "touch_at": now,
            "cursor_receipt": ReceiptTracker(CAMERA_DRAW_TIMEOUT), "cursor_number": 0, "cursor_redraw": False,
            "use_cache": bool(self._setting("feather_preview_cache", True)),
            "printed": 0, "rotation": False, "build": False, "blob": None,
            "build_direction": 1, "build_tick": 0,
            "session": None, "pending": False, "render_key": None, "error": None,
            "generation": 0, "frames": 0, "display_frames": 0, "fps": 0.0, "gen_fps": 0.0,
            "render_ms": 0.0, "draw_ms": 0.0,
            "main_cpu": 0.0, "child_cpu": None, "cpu_sample": None,
            "lag_sum": 0.0, "lag_max": 0.0, "lag_count": 0,
            "lag": (0.0, 0.0), "metrics_at": now, "thread_cpu": time.thread_time(),
            "animation_at": now, "frame_at": now + 0.05, "status_at": now,
            "monitor_at": now + MONITOR_PERIOD,
        }
        self._show_page(ScreenPage.MODEL_PREVIEW if mode == "lab" else ScreenPage.PRINT_PREVIEW)
        state = self.model_preview
        state["render_timer"] = self.reactor.register_timer(self._model_preview_frame_tick, state["frame_at"])
        state["monitor_timer"] = self.reactor.register_timer(self._model_preview_monitor_tick, state["monitor_at"])

    def _close_model_preview(self):
        state = getattr(self, "model_preview", None)
        self.model_preview = None
        if state is None:
            return
        state["cancel"].set()
        state["cursor_receipt"].cancel()
        for name in ("render_timer", "monitor_timer"):
            if state.get(name) is not None:
                self.reactor.unregister_timer(state[name])
        self._retire_model_preview_session(state)

    def _model_preview_paint_allowed(self, *pages):
        return self._page_paint_allowed(*pages) and self._current_dialog() is None

    def _retire_model_preview_session(self, state):
        session = state.get("session")
        if session is not None:
            session.cancel()
            worker = getattr(self, "file_worker", None)
            if worker is None or not worker.submit_write(session.close):
                session.close()
            state["session"] = None

    def _model_preview_status_values(self, state):
        state["status_at"] = self.reactor.monotonic() + STATUS_PERIOD
        if state["mode"] != "lab":
            return {}
        layers = state["session"].layers if state.get("session") is not None else 0
        return {common_ui.PreviewState.STATUS:
                "YAW %.0f  PITCH %.0f  LAYERS %d/%d" % (
                    state["yaw"], state["pitch"], state["printed"], layers)}

    def _print_model_preview_values(self, eventtime):
        stats = self.print_stats.get_status(eventtime)
        progress, values = self._current_print_progress_values(eventtime, stats)
        result = _progress_state(progress, values)
        result[printing_ui.PrintingState.STATUS] = self._display_status_text(eventtime)
        return result

    def _selected_model_preview_values(self, state):
        keys = common_ui.PreviewState
        info = state["file_info"] or {}
        layers = info.get("layers")
        if layers is None and state.get("session") is not None:
            layers = state["session"].layers or None
        return {
            keys.FILE_TIME: self._clock_duration(info.get("seconds")),
            keys.FILE_LAYERS: str(layers) if layers is not None else "--",
            keys.FILE_SIZE: self._format_size(info.get("size", self.selected_file.size)),
        }

    def _update_print_model_preview(self, eventtime):
        state = getattr(self, "model_preview", None)
        if state is None or state["mode"] == "lab":
            return
        if state["mode"] == "file":
            if self.print_state != PrintState.IDLE:
                self._show_page(self.page_for_print_state())
            elif self.selected_file is None or self.selected_file.path != state["path"]:
                self._show_page(ScreenPage.FILE_BROWSER)
            return
        if (self.print_state not in (PrintState.PREPARING, PrintState.PRINTING, PrintState.PAUSED)
                or self.virtual_sdcard.file_path() != state["path"]):
            self._show_page(self.page_for_print_state())
            return
        if not self._model_preview_paint_allowed(ScreenPage.PRINT_PREVIEW):
            return
        commands = state["page_tree"].update_refs(
            self.renderer, self._print_model_preview_values(eventtime), (common_ui.PreviewRef.INFO,))
        if commands:
            self.renderer.send(commands, kind="state", key="print-preview-info")

    def _model_preview_camera_values(self, state):
        keys = common_ui.PreviewState
        return {keys.YAW: state["yaw"], keys.PITCH: state["pitch"]}

    def _model_preview_cursor_commands(self, state):
        # Keep one delta in flight: it restores the last submitted knob, so
        # later touch samples only replace the desired camera until commit.
        if state["cursor_receipt"].pending or not self._model_preview_paint_allowed(ScreenPage.PRINT_PREVIEW):
            return
        if state["cursor_redraw"]:
            self._render_model_preview()
            return
        commands = state["page_tree"].update_refs(
            self.renderer, self._model_preview_camera_values(state),
            (common_ui.PreviewRef.YAW_PAD, common_ui.PreviewRef.TILT_PAD))
        if not commands:
            return
        state["cursor_number"] += 1
        token = "mpc:%d:%d:%d" % (state["serial"], state["generation"], state["cursor_number"])
        state["cursor_receipt"].expect(token, self.reactor.monotonic())
        if not self.renderer.send(commands, kind="state", receipt=token):
            self._render_model_preview()

    def _coalesce_model_preview_moves(self, lines):
        # Repainting every queued sample can prevent frame completions and
        # timers from running. Keep gesture edges and other events in order.
        pending, pending_action = None, None
        for line in lines:
            action = None
            if line.startswith("touch ") and getattr(self, "page", None) == ScreenPage.PRINT_PREVIEW:
                fields = line.split()
                if len(fields) == 5 and fields[0] == "touch" and fields[2] == "move":
                    try:
                        int(fields[3]), int(fields[4])
                    except ValueError:
                        pass
                    else:
                        if self.renderer.decode_action(fields[1]) in ("model.yaw_joystick", "model.pitch_joystick"):
                            action = fields[1]
            if pending is not None and action != pending_action:
                yield pending
                pending = None
            if action is None:
                yield line
            else:
                pending, pending_action = line, action
        if pending is not None:
            yield pending

    def _handle_model_preview_touch(self, action, phase, x, y, now):
        if action not in ("model.yaw_joystick", "model.pitch_joystick"):
            return False
        state = getattr(self, "model_preview", None)
        if state is None or state["mode"] == "lab" or self.page != ScreenPage.PRINT_PREVIEW:
            return True
        manager = getattr(self, "feature_manager", None)
        if (self._current_dialog() is not None or self._blocking_operation_active()
                or getattr(self, "command_depth", 0) > 0 or (manager is not None and manager.input_blocked)):
            state["touch_action"] = None
            return True
        if phase != "begin" and state["touch_action"] != action:
            return True
        yaw = action == "model.yaw_joystick"
        page = state["page_tree"]
        ref = common_ui.PreviewRef.YAW_PAD if yaw else common_ui.PreviewRef.TILT_PAD
        state["yaw" if yaw else "pitch"] = page.node(ref).value_at(x, y, page.rect(ref), page.state)
        state["touch_action"] = None if phase == "end" else action
        state["touch_at"] = now
        self._model_preview_cursor_commands(state)
        # Manual input overrides cadence, including when a frame is in flight.
        # Completion will wake this timer immediately with the latest angles.
        state["frame_at"] = now
        if not state["pending"]:
            self.reactor.update_timer(state["render_timer"], now)
        return True

    def _model_preview_frame_commands(self, state):
        page = state["page_tree"]
        keys, refs = common_ui.PreviewState, common_ui.PreviewRef
        values = {keys.BANDS: state["blob"] or (), keys.ERROR: state["error"] or ""}
        targets = [refs.IMAGE]
        if state["mode"] != "lab":
            targets.append(refs.ERROR)
        if state["mode"] == "file":
            values.update(self._selected_model_preview_values(state))
            targets.append(refs.INFO)
        if state["mode"] == "lab" and self.reactor.monotonic() >= state["status_at"]:
            values.update(self._model_preview_status_values(state))
            targets.append(refs.STATUS)
            page.node(refs.STATUS).invalidate()
        # Uncached benchmarks must submit even a pixel-identical generated frame.
        # This also retries the full raster after queue rejection or failed receipts.
        page.node(refs.IMAGE).invalidate()
        return page.update_refs(self.renderer, values, targets)

    def _model_preview_metrics_values(self, state):
        child = "--" if state["child_cpu"] is None else "%.0f%%" % state["child_cpu"]
        text = "FPS %.1f  GEN %.1f  R %.1fMS  DRAW %.1fMS  CPU %s  MAIN %.0f%%" % (
            state["fps"], state["gen_fps"], state["render_ms"], state["draw_ms"], child, state["main_cpu"])
        return {
            common_ui.PreviewState.METRICS: text,
            common_ui.PreviewState.DETAIL:
                state["error"] or "REACTOR LAG AVG %.1f / MAX %.1f MS" % state["lag"],
        }

    def _render_model_preview(self):
        state = self.model_preview
        if state is None:
            return
        # Rebuilding pixels after queue pressure must not cancel a held drag.
        state["cursor_receipt"].cancel()
        keys = common_ui.PreviewState
        values = {
            keys.FILENAME: os.path.basename(state["path"]),
            keys.BANDS: state["blob"] or (), keys.ERROR: state["error"] or "",
            keys.SCALE: state["scale"],
        }
        if state["mode"] == "lab":
            values[keys.STYLE] = state["settings"][0]
        values.update(self._model_preview_status_values(state))
        if state["mode"] != "lab":
            values.update(self._model_preview_camera_values(state))
            values.update(self._selected_model_preview_values(state) if state["mode"] == "file"
                          else self._print_model_preview_values(self.reactor.monotonic()))
        else:
            values.update({keys.CACHE: state["use_cache"], keys.ROTATION: state["rotation"], keys.BUILD: state["build"]})
            values.update(self._model_preview_metrics_values(state))
        title = preview_ui.TITLE if state["mode"] == "lab" else print_preview_ui.TITLE
        commands = self.renderer.begin_page(title, back=True)
        commands += state["page_tree"].draw(self.renderer, values)
        state["cursor_redraw"] = not self.renderer.send(commands)

    def _restart_model_preview_session(self, state):
        state["generation"] += 1
        self._retire_model_preview_session(state)
        state["session"], state["pending"], state["error"] = None, False, None
        state["blob"], state["render_key"], state["cpu_sample"] = None, None, None

    def _handle_model_preview_action(self, action):
        state = self.model_preview
        if state is None:
            return
        if state["mode"] != "print":
            self._require_idle()
        if action == "model.settings":
            state["touch_action"] = None
            state["cursor_receipt"].cancel()
            self.feature_manager.get("settings").open_preview_settings()
            return
        if action == "model.print":
            if state["mode"] != "file" or self.selected_file is None or self.selected_file.path != state["path"]:
                return
            self._start_selected_file()
            if self.page == ScreenPage.PRINT_PREVIEW:
                # An accepted start can precede print_stats.note_start().
                self._show_page(ScreenPage.PRINTING)
            return
        session = state.get("session")
        layers = session.layers if session is not None else 0
        if action == "model.file":
            self._open_model_preview_browser()
            return
        if action.startswith("model.style."):
            style = int(action.rsplit(".", 1)[1])
            if style not in range(len(model_runtime.MODEL_STYLES)):
                return
            if style != state["settings"][0] or not state["use_cache"] or state["error"]:
                density = (int(self._setting("feather_preview_stacked_density", 100)) if style == 1 else
                           int(self._setting("feather_preview_points_density", 30)) if style == 2 else 0)
                state["settings"] = (style,) + state["settings"][1:8] + (density,)
                self._restart_model_preview_session(state)
                self.params.set_value("feather_preview_type", model_runtime.MODEL_STYLES[style])
        elif action == "model.cache":
            state["use_cache"] = not state["use_cache"]
            self.params.set_value("feather_preview_cache", int(state["use_cache"]))
            self._restart_model_preview_session(state)
        elif action in ("model.yaw_left", "model.yaw_right"):
            yaw = state["yaw"] + (-15 if action.endswith("left") else 15)
            state["yaw"] = ((yaw + 180) % 360 - 180 if state["mode"] == "lab"
                            else min(SLIDER_YAW_LIMIT, max(-SLIDER_YAW_LIMIT, yaw)))
        elif action in ("model.pitch_down", "model.pitch_up"):
            limit = 90 if state["mode"] == "lab" else SLIDER_PITCH_LIMIT
            state["pitch"] = min(limit, max(-limit, state["pitch"] + (-10 if action.endswith("down") else 10)))
        elif action in ("model.layer_down", "model.layer_up"):
            step = max(1, layers // 20)
            state["printed"] = min(layers, max(0, state["printed"] + (-step if action.endswith("down") else step)))
        elif action in ("model.scale_down", "model.scale_up"):
            step = -0.05 if action.endswith("down") else 0.05
            state["scale"] = min(3.0, max(0.25, round(state["scale"] + step, 2)))
        elif action == "model.rotation":
            state["rotation"] = not state["rotation"]
            state["animation_at"] = self.reactor.monotonic()
        elif action == "model.build":
            state["build"] = not state["build"]
            if state["build"]:
                state["build_tick"] = 0
                if layers and state["printed"] >= layers:
                    state["build_direction"] = -1
                elif state["printed"] == 0:
                    state["build_direction"] = 1
        elif action == "model.full":
            state["printed"] = layers
            state["build_direction"], state["build_tick"] = -1, 0
        elif action == "model.reset":
            state["yaw"], state["pitch"], state["printed"] = state["settings"][2], state["settings"][3], 0
            state["scale"] = state["settings"][7]
            state["build_direction"], state["build_tick"] = 1, 0
            state["error"], state["render_key"] = None, None
            if not state["use_cache"]:
                self._restart_model_preview_session(state)
        self._render_model_preview()
        if not state["pending"]:
            self.reactor.update_timer(state["render_timer"], max(state["frame_at"], self.reactor.monotonic()))

    def _apply_model_preview_setting(self, key):
        state = self.model_preview
        if state is None:
            return
        settings = self._model_preview_settings(state["mode"])
        restart = (settings[:2] != state["settings"][:2] or settings[8:] != state["settings"][8:]
                   or key == "feather_preview_cache" or state["error"])
        state["settings"] = settings
        state["use_cache"] = bool(self._setting("feather_preview_cache", True))
        for name, index in (("yaw", 2), ("pitch", 3), ("scale", 7)):
            if key == "feather_preview_" + name:
                state[name] = settings[index]
        if restart:
            self._restart_model_preview_session(state)
        else:
            state["render_key"] = None
        state["frame_at"] = self.reactor.monotonic()
        if not state["pending"]:
            self.reactor.update_timer(state["render_timer"], state["frame_at"])

    def _model_preview_frame_tick(self, eventtime):
        state = getattr(self, "model_preview", None)
        if state is None or self.page not in PREVIEW_PAGES:
            return self.reactor.NEVER
        settings = state["settings"]
        period = 1.0 / max(1, settings[6])
        now = self.reactor.monotonic()
        if state["pending"]:
            # The completion callback wakes this timer; do not poll a busy worker.
            return self.reactor.NEVER
        if state["mode"] == "file" and state["file_info"] is None:
            # One latest-request worker owns both reads; finish details first.
            return self.reactor.NEVER
        if now < state["frame_at"]:
            return state["frame_at"]
        # Preserve the original cadence and skip missed slots after a stall.
        state["frame_at"] += max(1, math.floor((now - state["frame_at"]) / period) + 1) * period
        if not self._model_preview_paint_allowed(*PREVIEW_PAGES) or state["error"]:
            return state["frame_at"]
        if state["mode"] != "lab":
            if state["touch_action"] and now - state["touch_at"] > CAMERA_TOUCH_TIMEOUT:
                state["touch_action"] = None
        else:
            elapsed = now - state["animation_at"]
            state["animation_at"] = now
        session = state.get("session")
        if session is None:
            image = state["page_tree"].rect(common_ui.PreviewRef.IMAGE)
            width, height = image.width, image.height
            session = model_runtime.ModelPreview(state["path"], settings, width, height, threading.Event(),
                                   use_cache=state["use_cache"])
            state["session"] = session
        if state["mode"] == "print":
            layer_state = self._gcode_preview_render_spec(self.print_stats.get_status(now))[1]
            # Capture progress before dispatch; the worker learns physical layers.
            layer_fraction = layer_state[2] if layer_state else 0.0
            printed = round(session.layers * layer_fraction)
        elif state["mode"] == "file":
            layer_fraction = 1.0
            printed = session.layers
        elif state["rotation"]:
            state["yaw"] = (state["yaw"] + elapsed * settings[5] + 180) % 360 - 180
        if state["build"] and session.layers:
            # Advance every second animation tick, including skipped cached frames.
            state["build_tick"] = 1 - state["build_tick"]
            if state["build_tick"] == 0:
                step = state["build_direction"] * max(1, session.layers // 80)
                state["printed"] = min(session.layers, max(0, state["printed"] + step))
                if state["printed"] == session.layers:
                    state["build_direction"] = -1
                elif state["printed"] == 0:
                    state["build_direction"] = 1
        colors = tuple(self.renderer.color(color) for color in
                       (ThemeColor.PRIMARY, ThemeColor.SECONDARY, ThemeColor.PANEL))
        if state["mode"] == "lab":
            printed = state["printed"]
        key = (state["yaw"], state["pitch"], printed, colors, state["scale"], settings[4])
        if key == state["render_key"] and (state["use_cache"] or not state["build"]):
            return state["frame_at"]
        state["pending"] = True
        session.settings = (settings[0], settings[1], settings[2], state["pitch"]) + settings[4:]
        generation = state["generation"]

        def ready(blob, error):
            if getattr(self, "model_preview", None) is not state or state["generation"] != generation:
                return
            state["pending"] = False
            self.reactor.update_timer(state["render_timer"], max(state["frame_at"], self.reactor.monotonic()))
            if isinstance(error, (FileTaskSuperseded, PreviewCancelled)):
                return
            if error is not None:
                logging.info("[feather_screen] model preview failed: %s", error)
                state["error"] = str(error)[:200]
                self._retire_model_preview_session(state)
                state["status_at"] = 0
            else:
                if state["blob"] is None:
                    # Static previews have no later frame to publish loaded layers.
                    state["status_at"] = 0
                if state["mode"] != "lab":
                    # Publish the actual first-frame count after the handshake.
                    state["printed"] = round(session.layers * layer_fraction)
                    state["render_key"] = (key[0], key[1], state["printed"]) + key[3:]
                else:
                    state["render_key"] = key
                state["blob"] = blob
                state["frames"] += 1
                state["render_ms"] = session.last_render_ms
            if self._model_preview_paint_allowed(*PREVIEW_PAGES):
                state["frame_number"] += 1
                token = "mpl:%d:%d:%d" % (state["serial"], generation, state["frame_number"])
                state["accepted"] = self.renderer.send(self._model_preview_frame_commands(state),
                                   kind="animation", key="model-preview-frame", receipt=token)
                if state["accepted"] is False:
                    state["render_key"] = None
                    state["status_at"] = 0

        if self.file_worker is None or not self.file_worker.submit(
                lambda: model_runtime.model_frame_bands(session.render(
                    key[0], key[2], *colors, scale=key[4], layer_fraction=layer_fraction if state["mode"] != "lab" else None)), ready):
            ready(None, RuntimeError("File worker unavailable"))
        return state["frame_at"]

    def _model_preview_monitor_tick(self, eventtime):
        state = getattr(self, "model_preview", None)
        if state is None or self.page not in PREVIEW_PAGES:
            return self.reactor.NEVER
        # Read the clock on entry: the reactor's eventtime can precede another
        # callback in the same dispatch and would hide that callback's delay.
        now = self.reactor.monotonic()
        if ((state["cursor_receipt"].expired(now) or state["cursor_redraw"])
                and self._model_preview_paint_allowed(ScreenPage.PRINT_PREVIEW)):
            # A lost commit leaves damage restoration uncertain. Rebuild the
            # current camera once instead of replaying an obsolete delta.
            self._render_model_preview()
        lag = max(0.0, now - state["monitor_at"]) * 1000
        state["lag_sum"] += lag
        state["lag_max"] = max(state["lag_max"], lag)
        state["lag_count"] += 1
        elapsed = now - state["metrics_at"]
        if elapsed >= 1.0:
            cpu = time.thread_time()
            state["fps"] = state["display_frames"] / elapsed
            state["gen_fps"] = state["frames"] / elapsed
            state["display_frames"] = 0
            state["main_cpu"] = (cpu - state["thread_cpu"]) / elapsed * 100
            session = state.get("session")
            child = session.cpu_time() if session is not None else None
            previous = state["cpu_sample"]
            state["child_cpu"] = ((child - previous) / elapsed * 100
                                  if child is not None and previous is not None else None)
            state["cpu_sample"] = child
            state["lag"] = (state["lag_sum"] / state["lag_count"], state["lag_max"])
            state["metrics_at"], state["thread_cpu"], state["frames"] = now, cpu, 0
            state["lag_sum"], state["lag_max"], state["lag_count"] = 0.0, 0.0, 0
            if self._model_preview_paint_allowed(ScreenPage.MODEL_PREVIEW):
                page = state["page_tree"]
                page.node(common_ui.PreviewRef.METRICS).invalidate()
                self.renderer.send(page.update_refs(
                    self.renderer, self._model_preview_metrics_values(state),
                    (common_ui.PreviewRef.METRICS,)),
                    kind="state", key="model-preview-metrics")
        # Skip overdue ticks instead of flooding the reactor after a stall.
        state["monitor_at"] += max(1, math.floor((now - state["monitor_at"]) / MONITOR_PERIOD) + 1) * MONITOR_PERIOD
        return state["monitor_at"]


class ModelPreviewFeature(ModelPreviewPagesMixin, FeatureHostProxy):
    name = "model_preview"

    def __init__(self, host):
        super().__init__(host)
        self.model_preview = None

    def on_page_changed(self, old_page, new_page):
        state = self.model_preview
        if state is None or new_page not in PREVIEW_PAGES:
            return
        now = self.reactor.monotonic()
        for timer, deadline in (("render_timer", "frame_at"), ("monitor_timer", "monitor_at")):
            if state.get(timer) is not None:
                state[deadline] = now if deadline == "frame_at" else now + MONITOR_PERIOD
                self.reactor.update_timer(state[timer], state[deadline])

    @property
    def file_page(self):
        return self._host.file_page

    @file_page.setter
    def file_page(self, value):
        self._host.file_page = value

    @property
    def file_source(self):
        return self._host.file_source

    @file_source.setter
    def file_source(self, value):
        self._host.file_source = value

    @property
    def model_preview_file_select(self):
        return getattr(self._host, "model_preview_file_select", False)

    @model_preview_file_select.setter
    def model_preview_file_select(self, value):
        self._host.model_preview_file_select = value

    def deactivate(self):
        self._close_model_preview()
