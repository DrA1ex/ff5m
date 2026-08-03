## Declarative joystick movement page for Feather.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from enum import Enum

from ui import ThemeColor, ThemeRole

from ui.actions import Navigate
from ui.bindings import bind, derived
from ui.components import (
    Button, CornerMarks, Crosshair, Dialog, DotGrid, Hitbox, JoystickKnob,
    Metric, Panel, Section, Text, VerticalScale,
)
from ui.layout import (
    Column, Equal, Flex, Grid, Overlay, PageTree as Page, Row, Spacer,
    WrapPanel,
)
from ...keys import AppPage
from ..actions import (
    DISABLE_MOTORS, HOME_ALL, HOME_XY, HOME_Z, JOYSTICK_XY, JOYSTICK_Z,
)
from ..common import FONT, MOVE_CONTENT, caution_overlay, compact
from ..state import MoveState, ToolheadState


PAGE_ID = AppPage.MOVE_JOYSTICK


class JoystickRef(Enum):
    ROOT = "root"
    XY_PANEL = "xy.panel"
    XY_SECTION = "xy.section"
    XY_LAYOUT = "xy.layout"
    XY_PAD = "xy.pad"
    XY_PAD_PANEL = "xy.pad.panel"
    XY_GRID = "xy.grid"
    XY_CROSSHAIR = "xy.crosshair"
    XY_MARKS = "xy.marks"
    XY_LABEL_Y_PLUS = "xy.label.y_plus"
    XY_LABEL_Y_MINUS = "xy.label.y_minus"
    XY_LABEL_X_MINUS = "xy.label.x_minus"
    XY_LABEL_X_PLUS = "xy.label.x_plus"
    XY_KNOB = "xy.knob"
    XY_HITBOX = "xy.hitbox"
    XY_ACTIONS = "xy.actions"
    MOTORS = "motors"
    MODE = "mode"
    Z_PANEL = "z.panel"
    Z_SECTION = "z.section"
    Z_HITBOX = "z.hitbox"
    Z_TRACK = "z.track"
    Z_TRACK_PANEL = "z.track.panel"
    Z_SCALE = "z.scale"
    Z_KNOB = "z.knob"
    STATUS_PANEL = "status.panel"
    STATUS_SECTION = "status.section"
    STATUS_LAYOUT = "status.layout"
    STATUS_CARDS = "status.cards"
    STATUS_SPACER = "status.spacer"
    POSITION_CARD = "position.card"
    POSITION_PANEL = "position.panel"
    POSITION_METRICS = "position.metrics"
    POSITION_X = "position.x"
    POSITION_Y = "position.y"
    POSITION_Z = "position.z"
    INERTIA_CARD = "inertia.card"
    INERTIA_PANEL = "inertia.panel"
    INERTIA_METRIC = "inertia.metric"
    HOME_BUTTONS = "home.buttons"
    HOME_ALL = "home.all"
    HOME_XY = "home.xy"
    HOME_Z = "home.z"


def _position_border(homed_x, homed_y, homed_z):
    return ThemeColor.PRIMARY if homed_x and homed_y and homed_z else ThemeColor.WARNING


def _position_card():
    border = derived(
        _position_border,
        bind(ToolheadState.HOMED_X),
        bind(ToolheadState.HOMED_Y),
        bind(ToolheadState.HOMED_Z),
    )
    metrics = Column(
        Metric(
            "X", derived(lambda value: "%6.1f" % value,
                         bind(ToolheadState.X)),
            "mm", label_color=border,
        ).ref(JoystickRef.POSITION_X),
        Metric(
            "Y", derived(lambda value: "%6.1f" % value,
                         bind(ToolheadState.Y)),
            "mm", label_color=border,
        ).ref(JoystickRef.POSITION_Y),
        Metric(
            "Z", derived(lambda value: "%6.1f" % value,
                         bind(ToolheadState.Z)),
            "mm", label_color=border,
        ).ref(JoystickRef.POSITION_Z),
    ).padding(left=12, top=10, right=12, bottom=10) \
     .ref(JoystickRef.POSITION_METRICS)
    return Overlay(
        Panel(border=border, line_width=1).ref(JoystickRef.POSITION_PANEL),
        metrics,
    ).ref(JoystickRef.POSITION_CARD).repaint_boundary()


def _inertia_card():
    return Overlay(
        Panel(border=ThemeColor.BORDER, line_width=1).ref(JoystickRef.INERTIA_PANEL),
        Metric(
            "INERTIA",
            derived(lambda value: "%5.1f" % value, bind(MoveState.INERTIA)),
        ).margin(left=12, right=12).ref(JoystickRef.INERTIA_METRIC),
    ).ref(JoystickRef.INERTIA_CARD).repaint_boundary()


def _xy_pad():
    return Overlay(
        Panel(border=ThemeColor.PRIMARY, line_width=1).ref(JoystickRef.XY_PAD_PANEL),
        DotGrid(columns=11, rows=7)
        .margin(left=40, top=24, right=40, bottom=24)
        .ref(JoystickRef.XY_GRID),
        Crosshair()
        .margin(left=40, top=24, right=40, bottom=24)
        .ref(JoystickRef.XY_CROSSHAIR),
        CornerMarks(length=11)
        .margin(left=20, top=18, right=20, bottom=18)
        .ref(JoystickRef.XY_MARKS),
        Text("+Y").size(60, 18).margin(top=1)
        .align(horizontal="center", vertical="top")
        .ref(JoystickRef.XY_LABEL_Y_PLUS),
        Text("-Y").size(60, 18).margin(bottom=1)
        .align(horizontal="center", vertical="bottom")
        .ref(JoystickRef.XY_LABEL_Y_MINUS),
        Text("-X").size(34, 24).margin(left=1)
        .align(horizontal="left", vertical="center")
        .ref(JoystickRef.XY_LABEL_X_MINUS),
        Text("+X").size(34, 24).margin(right=1)
        .align(horizontal="right", vertical="center")
        .ref(JoystickRef.XY_LABEL_X_PLUS),
        JoystickKnob(
            "xy", position=bind(MoveState.CURSOR),
            active_action=JOYSTICK_XY,
            surface_ref=JoystickRef.XY_GRID,
        ).ref(JoystickRef.XY_KNOB),
        Hitbox(JOYSTICK_XY, continuous=True)
        .ref(JoystickRef.XY_HITBOX),
        caution_overlay(),
    ).ref(JoystickRef.XY_PAD)


def _z_track():
    return Overlay(
        Panel(border=ThemeColor.PRIMARY, line_width=1).ref(JoystickRef.Z_TRACK_PANEL),
        VerticalScale(depth=3).ref(JoystickRef.Z_SCALE),
        JoystickKnob(
            "z", position=bind(MoveState.CURSOR),
            active_action=JOYSTICK_Z, edge_padding=2,
        ).ref(JoystickRef.Z_KNOB),
    ).ref(JoystickRef.Z_TRACK)


def _xy_panel():
    xy_actions = compact(Row(
        Button(DISABLE_MOTORS, "DISABLE MOTORS")
        .width(190).ref(JoystickRef.MOTORS),
        Button(Navigate(AppPage.MOVE_STEP), "STEP MODE").ref(JoystickRef.MODE),
        gap=10,
    )).width(340).height(44).align(horizontal="left") \
     .ref(JoystickRef.XY_ACTIONS)
    xy_layout = Column(
        _xy_pad(),
        xy_actions,
        gap=12,
    ).padding(left=18, top=32, right=18, bottom=10) \
     .ref(JoystickRef.XY_LAYOUT)
    return Overlay(
        Section("XY POSITION").ref(JoystickRef.XY_SECTION),
        xy_layout,
    ).ref(JoystickRef.XY_PANEL)


def _z_panel():
    return Overlay(
        Section("Z AXIS").ref(JoystickRef.Z_SECTION),
        Hitbox(JOYSTICK_Z, continuous=True)
        .margin(left=8, top=32, right=8, bottom=3)
        .ref(JoystickRef.Z_HITBOX),
        _z_track().width(10).margin(top=39, right=27, bottom=10)
        .align(horizontal="right"),
    ).ref(JoystickRef.Z_PANEL)


def _status_panel():
    home_buttons = compact(WrapPanel(
        Button(HOME_ALL, "HOME ALL").ref(JoystickRef.HOME_ALL),
        Button(HOME_XY, "HOME XY").ref(JoystickRef.HOME_XY),
        Button(HOME_Z, "HOME Z").ref(JoystickRef.HOME_Z),
        orientation="vertical", item_width=172, item_height=42,
        vertical_gap=6,
    )).height(138).ref(JoystickRef.HOME_BUTTONS)
    cards = Column(
        _position_card().height(92),
        _inertia_card().height(48),
        gap=12,
    ).height(152).ref(JoystickRef.STATUS_CARDS)
    status_layout = Column(
        cards,
        Spacer().ref(JoystickRef.STATUS_SPACER),
        home_buttons,
        gap=7,
    ).padding(left=14, top=32, right=14, bottom=28) \
     .ref(JoystickRef.STATUS_LAYOUT)
    return Overlay(
        Section("POSITION").ref(JoystickRef.STATUS_SECTION),
        status_layout,
    ).ref(JoystickRef.STATUS_PANEL)


def _content():
    return Grid(
        matrix=((_xy_panel(), _z_panel(), _status_panel()),),
        columns=(Flex(456), Flex(100), Flex(200)),
        rows=Equal(1), gap=(10, 0),
    ).ref(JoystickRef.ROOT)


PAGE = Page(content=_content(), bounds=MOVE_CONTENT, page_id=PAGE_ID)


def render(renderer, values):
    return PAGE.draw(renderer, values)


def update(renderer, values):
    return PAGE.update(renderer, values)
