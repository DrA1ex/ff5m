## Local object-exclusion fixture using the production screen controller.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from types import SimpleNamespace

from tests.feather_render_test_helper import RenderCapture
from tests.test_feather_print_preview import _controller
from tests.test_feather_screen import StatusObject
from feather_ui_test.resources import load_exclude_models
from tests.test_gcode_patch import GCODE, RecordingMutex


def make_screen():
    fixture = load_exclude_models()
    screen = _controller("/local/fixture/multi_part_demo.gcode")
    screen.capture = RenderCapture(screen.renderer)
    screen.objects = StatusObject(fixture)
    x0, y0, x1, y1 = fixture["bed"]
    screen.move_safe = StatusObject(dict(x_min=x0, y_min=y0, x_max=x1, y_max=y1))
    sources = {"exclude_object": screen.objects, "gcode_macro MOVE_SAFE": screen.move_safe}
    screen.printer = SimpleNamespace(lookup_object=lambda name, default=None: sources.get(name, default))
    screen.toolhead.status.update(axis_minimum=(x0, y0, 0, 0), axis_maximum=(x1, y1, 220, 0))
    screen.virtual_sdcard.is_active = lambda: True
    screen.cancel_requested = False
    screen.last_action_time = 0
    screen.debug = False
    screen.busy_message = None
    screen.toast_until = 0
    screen.toast_message = ""
    screen.gcode = SimpleNamespace(commands=[], parameters=[])

    dispatch = GCODE.GCodeDispatch.__new__(GCODE.GCodeDispatch)
    dispatch.printer = SimpleNamespace(send_event=lambda *args: None,
                                      config_error=RuntimeError)
    dispatch.mutex = RecordingMutex()
    dispatch.is_fileinput = False
    dispatch.output_callbacks = []
    dispatch.base_gcode_handlers = {}
    dispatch.gcode_handlers = dispatch.ready_gcode_handlers = {}
    dispatch.gcode_help = {}

    def exclude(gcmd):
        parameters = dict(gcmd.get_command_parameters())
        if set(parameters) != {"NAME"}:
            raise gcmd.error("Unexpected exclusion parameters")
        screen.gcode.parameters.append(parameters)
        name = gcmd.get("NAME")
        if name not in screen.objects.status["excluded_objects"]:
            screen.objects.status["excluded_objects"].append(name)

    dispatch.register_command("EXCLUDE_OBJECT", exclude)

    def run(command):
        screen.gcode.commands.append(command)
        dispatch.run_script(command)

    screen.gcode.run_script = run
    screen.renderer.footer("NOZZLE 220/220C | BED 60/60C", "LOCAL UX FIXTURE", paint=False)
    screen._apply_safety_visibility()
    screen._render_screen()
    return screen


def tap(screen, action):
    screen.reactor.now += 1
    screen._handle_touch_action(action)


def hit_at(screen, x, y):
    # Typer uses the last registered overlapping hitbox.
    return next((region.action for region in reversed(screen.renderer.hitboxes)
                 if region.x <= x < region.x + region.width
                 and region.y <= y < region.y + region.height), None)
