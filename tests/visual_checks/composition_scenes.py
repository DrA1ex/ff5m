## Retained-frame scenarios using the FF5M screen composition lifecycle.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Produce drawing histories, not independent full-frame snapshots."""

from collections import Counter
from contextlib import ExitStack
import pathlib
import shlex
import sys
import time
from unittest import mock


ROOT = pathlib.Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / ".py/klipper/plugins"))

from feather_screen import FeatherScreen
from feather.screen.composition import DialogInstance, PaintSurface
from ui import (
    Button, Column, Command, CommandKey, FeatherRenderer, Fill, Frame, Node,
    Overlay, PageKey, Rect, Row, ScreenRoot, StateKey, Text, ThemeColor,
    bind, state,
)
from ui.layout import PageTree


class Pages(PageKey):
    COMPOSITION = "test.composition"


class Values(StateKey):
    VALUE = state(str, default="OLD DATA")


class Commands(CommandKey):
    FIRST = "background.first"
    SECOND = "background.second"
    THIRD = "background.third"


def _option(tokens, name, count=1, default=None):
    if name not in tokens:
        if default is not None:
            return default
        raise ValueError("Missing Typer option: " + name)
    start = tokens.index(name) + 1
    values = tokens[start:start + count]
    if len(values) != count:
        raise ValueError("Incomplete Typer option: " + name)
    return values[0] if count == 1 else values


def _color(value):
    if len(value) == 6:
        return "#" + value
    if len(value) == 8:
        # Typer colors are ARGB; Canvas accepts CSS RGBA.
        alpha, red, green, blue = (int(value[i:i + 2], 16) for i in range(0, 8, 2))
        return "rgba(%d,%d,%d,%.8f)" % (red, green, blue, alpha / 255)
    raise ValueError("Unsupported Typer color: " + value)


def drawing_operations(commands):
    """Translate only the explicit drawing protocol exercised by this suite.

    Native composition and output admission run unchanged. This adapter is
    solely the test transport into the existing Designer canvas primitives.
    Unknown commands fail instead of silently making an incomplete picture.
    """
    operations = []
    for command in commands:
        tokens = shlex.split(command)
        if len(tokens) < 2 or tokens[0] != "--batch":
            raise ValueError("Unsupported drawing command: " + command)
        kind = tokens[1]
        if kind in ("hitbox", "clear-hitboxes", "flush"):
            continue
        if kind not in ("fill", "stroke", "text", "button"):
            raise ValueError("Unsupported drawing command: " + kind)
        x, y = map(int, _option(tokens, "-p", 2))
        if kind == "text":
            operations.append({
                "type": "text", "x": x, "y": y,
                "value": _option(tokens, "-t"),
                "color": _color(_option(tokens, "-c")),
                "font": _option(tokens, "-f"),
                "horizontal": _option(tokens, "-ha"),
                "vertical": _option(tokens, "-va"),
                "max_width": int(_option(tokens, "--max-width"))
                if "--max-width" in tokens else None,
                "max_height": int(_option(tokens, "--max-height"))
                if "--max-height" in tokens else None,
                "wrap": "--wrap" in tokens, "truncate": "--truncate" in tokens,
            })
            continue
        width, height = map(int, _option(tokens, "-s", 2))
        bounds = dict(x=x, y=y, width=width, height=height)
        if kind == "button":
            operations.extend([
                dict(bounds, type="fill", color=_color(_option(tokens, "--background"))),
                dict(bounds, type="stroke", color=_color(_option(tokens, "--border")),
                     line_width=int(_option(tokens, "-lw"))),
                dict(type="text", x=x + width // 2, y=y + height // 2,
                     value=_option(tokens, "-t"), color=_color(_option(tokens, "--text-color")),
                     font=_option(tokens, "-f"), horizontal="center", vertical="middle",
                     max_width=int(_option(tokens, "--max-width")),
                     max_height=None, wrap=False, truncate="--truncate" in tokens),
            ])
        else:
            operation = dict(bounds, type=kind, color=_color(_option(tokens, "-c")))
            if kind == "stroke":
                operation["line_width"] = int(_option(tokens, "-lw"))
            operations.append(operation)
    return operations


def _name(node):
    return str(node.key or type(node).__name__)


def _tree(node, layout):
    return {
        "node": _name(node), "type": type(node).__name__,
        "bounds": list(layout.rect(node)),
        "children": [_tree(child, layout) for child in node.render_children()],
    }


def _background(layout, renderer, data):
    if layout == "imperative":
        def paint():
            commands = [renderer.fill(0, 60, 800, 380, ThemeColor.BACKGROUND)]
            for index in range(6):
                x, y = 24 + (index % 3) * 254, 78 + (index // 3) * 174
                commands += renderer.panel(x, y, 244, 156)
                commands += [renderer.text(x + 122, y + 40, "TILE %d" % (index + 1)),
                             renderer.text(x + 122, y + 88, data["value"], font="JetBrainsMono 8pt")]
            renderer.send(commands)
        background = PaintSurface(paint)
        background.key = "background"
        return background

    def card(index):
        action = (Commands.FIRST, Commands.SECOND, Commands.THIRD)[index - 1]
        body = Column(
            Text("CARD %d" % index).height(46),
            Text(bind(Values.VALUE), font="JetBrainsMono 8pt").height(46),
            Button(Command(action), "ACTION %d" % index).height(52),
            gap=8,
        ).padding(12)
        return Frame(body, key="card-%d" % index)

    if layout == "cards":
        body = Row(*(card(index).grow() for index in range(1, 4)), gap=16).padding(16)
    else:
        body = Column(*(
            Frame(Row(Text("ROW %d" % index).width(150),
                      Text(bind(Values.VALUE)), gap=12).padding(12),
                  key="row-%d" % index).height(94)
            for index in range(1, 4)
        ), gap=16).padding(16)
    return Overlay(Fill(ThemeColor.BACKGROUND), body, key="background")


def run_sequence(layout, theme="DEFAULT"):
    """Keep one native renderer, root and framebuffer history for all steps."""
    renderer = FeatherRenderer()
    renderer.set_theme(theme)
    renderer.footer("STATUS: OLD DATA", "READY", paint=False)
    screen = FeatherScreen.__new__(FeatherScreen)
    screen.renderer = renderer
    data = {"value": "OLD DATA"}
    root = ScreenRoot(renderer, _background(layout, renderer, data),
                      Rect(0, 60, 800, 380), Pages.COMPOSITION,
                      title="COMPOSITION / " + layout.upper())
    dialog_data = dict(title="PROGRESS", lines=("Heating: 100 C", "Waiting for homing"),
                       width=500, height=260, buttons=(("test.cancel", "CANCEL", "enabled"),))

    def dialog(spec):
        def paint(instance):
            value = instance.content
            renderer.send(renderer.dialog(
                value["title"], value["lines"], value["buttons"],
                x=(800 - value["width"]) // 2, y=(480 - value["height"]) // 2,
                width=value["width"], height=value["height"], custom_body=True,
                tone="info", modal=True))
        layer = DialogInstance("composition", spec, screen._paint_dialog, paint, (), 0)
        layer.node.key = "dialog"
        return layer

    layer = dialog(dialog_data)
    history = []
    steps = []
    calls = []
    passes = []
    stack = []

    def traced_render(original):
        def render(node, target, values, geometry):
            event = dict(node=_name(node), type=type(node).__name__,
                         method=original.__qualname__,
                         bounds=list(geometry.rect(node)), depth=len(stack), commands=0)
            calls.append(event)
            stack.append(node)
            started = time.perf_counter()
            try:
                commands = original(node, target, values, geometry)
                event["commands"] = len(commands)
                return commands
            finally:
                event["milliseconds"] = round((time.perf_counter() - started) * 1000, 3)
                stack.pop()
        return render

    original_full = PageTree._render_full

    def full(tree, target, arrange, refresh_actions, render_from=None):
        passes.append(dict(arrange=arrange, refresh_actions=refresh_actions,
                           render_from=None if render_from is None else [_name(node) for node in render_from]))
        return original_full(tree, target, arrange, refresh_actions, render_from)

    def checkpoint(name, description, action, expected):
        calls.clear()
        passes.clear()
        started = time.perf_counter()
        action()
        elapsed = (time.perf_counter() - started) * 1000
        batches = []
        while True:
            batch = renderer._batch_queue.get(timeout=0)
            if batch is None:
                break
            batches.append(dict(kind=batch.kind, generation=batch.generation, commands=list(batch.commands)))
        commands = [command for batch in batches for command in batch["commands"]]
        operations = drawing_operations(commands)
        history.extend(operations)
        counts = dict(background=sum(call["node"] == "background" for call in calls),
                      dialog=sum(call["node"] == "dialog" for call in calls),
                      scrim=sum(command == renderer.modal_scrim() for command in commands),
                      batches=len(batches), passes=len(passes),
                      layout_passes=sum(bool(value["arrange"]) for value in passes),
                      operations=len(operations))
        failures = ["%s: expected %s, got %s" % (key, value, counts[key])
                    for key, value in expected.items() if counts[key] != value]
        steps.append(dict(
            id=layout + "-" + name, label=layout + " / " + name,
            description=description, counts=counts, expected_counts=expected, failures=failures,
            milliseconds=round(elapsed, 3), render_calls=list(calls), passes=list(passes),
            tree=_tree(root._tree.root, root._tree.layout), batches=batches,
            dirty_operations=operations,
            operation_types=dict(Counter(operation["type"] for operation in operations)),
            dialog_bounds=list(renderer._dialog_bounds),
            controls=sorted({region.action for region in renderer.hitboxes}),
            # Replaying the entire accepted history on the Designer canvas
            # preserves old pixels and accumulated alpha between checkpoints.
            composition_fixture=dict(title=layout + " / " + name, operations=list(history)),
        ))

    def refresh(**changes):
        layer.content.update(changes)
        root.invalidate(layer.node)
        root.paint()

    def value(text):
        data["value"] = text
        renderer.footer("STATUS: " + text, "READY", paint=False)
        if Values.VALUE in root._tree.state_schema:
            root.update({Values.VALUE: text})
        else:
            root.invalidate()
            root.paint()

    with ExitStack() as patches:
        patches.enter_context(mock.patch.object(Node, "render", traced_render(Node.render)))
        patches.enter_context(mock.patch.object(PaintSurface, "render", traced_render(PaintSurface.render)))
        patches.enter_context(mock.patch.object(PageTree, "_render_full", full))
        checkpoint("background", "Initial layout; all cards/rows/tiles should be visible.", root.paint,
                   dict(background=1, dialog=0, scrim=0, batches=1))
        checkpoint("open", "Dialog over the existing background, darkened once.", lambda: root.open(layer),
                   dict(background=0, dialog=1, scrim=1, batches=1))
        checkpoint("text", "Update dialog text; the surrounding background must stay unchanged.",
                   lambda: refresh(lines=("Heating: 150 C", "Waiting for homing")),
                   dict(background=0, dialog=1, scrim=0, batches=1))
        checkpoint("background-held", "Hidden data changed to NEW DATA; visible background still says OLD DATA.",
                   lambda: value("NEW DATA"), dict(background=0, dialog=0, scrim=0, batches=0))
        checkpoint("button-label", "Change the button label without touching the background.",
                   lambda: refresh(buttons=(("test.cancel", "STOP", "warning"),)),
                   dict(background=0, dialog=1, scrim=0, batches=1))
        checkpoint("button-busy", "Disable the button; one dialog preparation and no backdrop paint.",
                   lambda: refresh(buttons=(("test.cancel", "WAIT", "busy"),)),
                   dict(background=0, dialog=1, scrim=0, batches=1))
        checkpoint("shrink", "Smaller panel: old panel edges disappear; background now shows NEW DATA.",
                   lambda: refresh(width=360, height=220, lines=("Compact dialog",)),
                   dict(background=1, scrim=1, batches=1))
        checkpoint("grow", "Larger panel: restore the backdrop and apply one scrim.",
                   lambda: refresh(width=680, height=330, lines=("Expanded dialog", "Second line", "Third line")),
                   dict(background=1, scrim=1, batches=1))

        def replace_background():
            root.replace_content(_background("list" if layout != "list" else "cards", renderer, data))
            root.update({Values.VALUE: data["value"]})
        checkpoint("replace-background", "New background component appears immediately beneath the existing dialog.",
                   replace_background, dict(background=1, dialog=1, scrim=1, batches=1))
        checkpoint("suspend", "Suspend dialog: reveal current background with no scrim or panel remnants.",
                   lambda: root.suspend(layer), dict(background=1, dialog=0, scrim=0, batches=1))
        checkpoint("resume", "Resume the same dialog over the retained backdrop; darken once.",
                   lambda: root.suspend(layer, False), dict(background=0, dialog=1, scrim=1, batches=1))
        checkpoint("force", "Output recovery redraws the complete composition with one scrim.",
                   lambda: root.paint(force=True), dict(background=1, dialog=1, scrim=1, batches=1))

        replacement = dialog(dict(title="NEXT DIALOG", lines=("Previous large panel must disappear",),
                                  width=440, height=240, buttons=(("test.ok", "OK", "enabled"),)))
        checkpoint("replace-dialog", "Atomic large-to-small dialog replacement; no old panel edges remain.",
                   lambda: root.open(replacement, replace=layer),
                   dict(background=1, dialog=1, scrim=1, batches=1))
        layer = replacement
        checkpoint("latest-held", "Change hidden data to LATEST DATA; visible background still says NEW DATA.",
                   lambda: value("LATEST DATA"), dict(background=0, dialog=0, scrim=0, batches=0))
        checkpoint("close", "Close dialog: expose LATEST DATA and restore the background controls.",
                   lambda: root.close(layer), dict(background=1, dialog=0, scrim=0, batches=1))
        layer = dialog(dict(title="AFTER CLOSE", lines=("A separate dialog after the close",),
                            width=560, height=270, buttons=(("test.ok", "DONE", "enabled"),)))
        checkpoint("open-after-close", "Sequential close/open: only this new panel remains, scrim applied once.",
                   lambda: root.open(layer), dict(background=0, dialog=1, scrim=1, batches=1))
        checkpoint("final-close", "Final clean background with LATEST DATA; no dialog pixels or dimming.",
                   lambda: root.close(layer), dict(background=1, dialog=0, scrim=0, batches=1))
    return steps
