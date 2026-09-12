## Declarative step movement page for Feather.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from enum import Enum

from ui import ThemeColor, ThemeRole

from ui.actions import Increment, Navigate, SetValue
from ui.bindings import bind, derived
from ui.components import Button, Fill, Stroke, Text
from ui.layout import (
    EMPTY, FLEX, Column, Equal, Flex, Grid, Overlay,
    PageTree as Page, Row, WrapPanel,
)
from ...keys import AppPage
from ..actions import (
    DISABLE_MOTORS, HOME_ALL, HOME_XY, X_MINUS, X_PLUS, Y_MINUS, Y_PLUS,
    Z_MINUS, Z_PLUS,
)
from ..common import (
    CAUTION_HEIGHT, CAUTION_WIDTH, FONT, MOVE_CONTENT, STEP_VALUES,
    caution_overlay, compact,
)
from ..state import MoveState, ToolheadState


PAGE_ID = AppPage.MOVE_STEP


class StepRef(Enum):
    ROOT = "root"
    AXIS = "axis"
    AXIS_LAYOUT = "axis.layout"
    AXIS_ACTIONS = "axis.actions"
    XY_GRID = "xy.grid"
    XY_UP = "xy.up"
    XY_LEFT = "xy.left"
    XY_STATUS = "xy.status"
    XY_STATUS_BACKGROUND = "xy.status.background"
    XY_STATUS_BORDER = "xy.status.border"
    XY_STATUS_LABEL = "xy.status.label"
    XY_STATUS_STATE = "xy.status.state"
    XY_RIGHT = "xy.right"
    XY_DOWN = "xy.down"
    Z_GRID = "z.grid"
    Z_DOWN = "z.down"
    Z_STATUS = "z.status"
    Z_STATUS_BACKGROUND = "z.status.background"
    Z_STATUS_BORDER = "z.status.border"
    Z_STATUS_LABEL = "z.status.label"
    Z_STATUS_STATE = "z.status.state"
    Z_UP = "z.up"
    MOTORS = "motors"
    MODE = "mode"
    SEPARATOR_LAYOUT = "separator.layout"
    SEPARATOR = "separator"
    CONTROL = "control"
    HOME_LAYOUT = "home.layout"
    STATUS_CARD = "status.card"
    STATUS_BACKGROUND = "status.background"
    STATUS_STATE = "status.state"
    STATUS_XY = "status.xy"
    STATUS_Z = "status.z"
    HOME_BUTTONS = "home.buttons"
    HOME_ALL = "home.all"
    HOME_XY = "home.xy"
    DIVIDER = "step.divider"
    STEP_LAYOUT = "step.layout"
    STEP_TITLE = "step.title"
    STEP_ADJUST = "step.adjust"
    STEP_MINUS = "step.minus"
    STEP_VALUE = "step.value"
    STEP_PLUS = "step.plus"
    PRESET_TITLE = "preset.title"
    PRESETS = "presets"
    PRESET_0 = "preset.0"
    PRESET_1 = "preset.1"
    PRESET_2 = "preset.2"


STEP_REFS = (StepRef.PRESET_0, StepRef.PRESET_1, StepRef.PRESET_2)


def _status_label(homed_x, homed_y, homed_z):
    return ("HOMED: XYZ" if homed_x and homed_y and homed_z else
            "NOT HOMED: %s%s%s" % (
                "" if homed_x else "X",
                "" if homed_y else "Y",
                "" if homed_z else "Z"))


def _xy_position_label(x, y):
    return "X %7.2f   Y %7.2f" % (x, y)


def _z_position_label(z):
    return "Z %7.2f" % z


def _step_value_label(value):
    return "%g MM" % value


def _step_preset_selected(current, preset):
    active = 10.0 if current >= 10.0 else (1.0 if current >= 1.0 else 0.1)
    return "selected" if active == preset else "enabled"


def _axis_status(label, homed, refs):
    color = derived(
        lambda ready: ThemeColor.PRIMARY if ready else ThemeColor.WARNING,
        homed)
    state_label = derived(
        lambda ready: "HOMED" if ready else "HOME",
        homed)
    return Overlay(
        Fill(ThemeColor.PANEL).ref(refs[0]),
        Stroke(color, 2).ref(refs[1]),
        Text(label, color=color).height(44).align(vertical="top").ref(refs[2]),
        Text(state_label, color=color)
        .margin(top=34, bottom=4).ref(refs[3]),
    ).ref(refs[4]).repaint_boundary()


def _status_card():
    homed_x = bind(ToolheadState.HOMED_X)
    homed_y = bind(ToolheadState.HOMED_Y)
    homed_z = bind(ToolheadState.HOMED_Z)
    return Overlay(
        Fill(ThemeColor.BACKGROUND).ref(StepRef.STATUS_BACKGROUND),
        Text(
            derived(_status_label, homed_x, homed_y, homed_z),
            color=derived(
                lambda x, y, z: ThemeColor.PRIMARY if x and y and z
                else ThemeColor.WARNING,
                homed_x, homed_y, homed_z),
        ).height(34).align(vertical="top").ref(StepRef.STATUS_STATE),
        Text(
            derived(_xy_position_label,
                    bind(ToolheadState.X), bind(ToolheadState.Y)),
            color=ThemeColor.TEXT,
        ).height(46).margin(top=34).align(vertical="top") \
         .ref(StepRef.STATUS_XY),
        Text(
            derived(_z_position_label, bind(ToolheadState.Z)),
            color=ThemeColor.TEXT,
        ).height(26).margin(top=70).align(vertical="top").allow_overflow() \
         .ref(StepRef.STATUS_Z),
    ).ref(StepRef.STATUS_CARD).repaint_boundary()


def _axis_layout():
    xy_homed = derived(
        lambda x, y: x and y,
        bind(ToolheadState.HOMED_X), bind(ToolheadState.HOMED_Y))
    z_homed = bind(ToolheadState.HOMED_Z)
    xy_grid = Grid(
        matrix=(
            (EMPTY, Button(Y_PLUS, "Y+").ref(StepRef.XY_UP), EMPTY),
            (Button(X_MINUS, "X-").ref(StepRef.XY_LEFT),
             _axis_status("X / Y", xy_homed, (
                 StepRef.XY_STATUS_BACKGROUND, StepRef.XY_STATUS_BORDER,
                 StepRef.XY_STATUS_LABEL, StepRef.XY_STATUS_STATE,
                 StepRef.XY_STATUS)),
             Button(X_PLUS, "X+").ref(StepRef.XY_RIGHT)),
            (EMPTY, Button(Y_MINUS, "Y-").ref(StepRef.XY_DOWN), EMPTY),
        ),
        columns=Equal(3), rows=Equal(3), gap=(10, 12),
    ).ref(StepRef.XY_GRID)
    z_grid = Grid(
        matrix=(
            (Button(Z_MINUS, "Z-").ref(StepRef.Z_DOWN),),
            (_axis_status("Z", z_homed, (
                StepRef.Z_STATUS_BACKGROUND, StepRef.Z_STATUS_BORDER,
                StepRef.Z_STATUS_LABEL, StepRef.Z_STATUS_STATE,
                StepRef.Z_STATUS)),),
            (Button(Z_PLUS, "Z+").ref(StepRef.Z_UP),),
        ),
        columns=Equal(1), rows=Equal(3), gap=(0, 12),
    ).ref(StepRef.Z_GRID)
    return Grid(
        matrix=((xy_grid, z_grid),),
        columns=(Flex(320), Flex(65)), rows=Equal(1), gap=(15, 0),
    ).ref(StepRef.AXIS_LAYOUT)


def _axis_actions():
    return compact(Row(
        Button(DISABLE_MOTORS, "DISABLE MOTORS")
        .width(190).ref(StepRef.MOTORS),
        Button(Navigate(AppPage.MOVE_JOYSTICK), "JOY MODE").ref(StepRef.MODE),
        gap=15,
    )).height(58).ref(StepRef.AXIS_ACTIONS)


def _control_layout():
    home_buttons = compact(WrapPanel(
        Button(HOME_ALL, "HOME ALL").ref(StepRef.HOME_ALL),
        Button(HOME_XY, "HOME XY").ref(StepRef.HOME_XY),
        item_width=145, item_height=50, horizontal_gap=15,
    )).height(50).ref(StepRef.HOME_BUTTONS)
    home_layout = Column(
        _status_card().height(95),
        home_buttons,
        gap=10,
    ).ref(StepRef.HOME_LAYOUT)

    step_adjust = Grid(
        matrix=((
            Button(Increment(MoveState.JOG_STEP, -1), "-").ref(StepRef.STEP_MINUS),
            Text(
                derived(_step_value_label, bind(MoveState.JOG_STEP)),
                color=ThemeColor.TEXT,
            ).ref(StepRef.STEP_VALUE),
            Button(Increment(MoveState.JOG_STEP, 1), "+").ref(StepRef.STEP_PLUS),
        ),),
        columns=(80, FLEX, 80), rows=Equal(1),
    ).ref(StepRef.STEP_ADJUST)
    presets = compact(WrapPanel(
        *tuple(
            Button(
                SetValue(MoveState.JOG_STEP, value), "%g" % value,
                state=derived(
                    lambda current, expected=value:
                    _step_preset_selected(current, expected),
                    bind(MoveState.JOG_STEP)),
            ).ref(STEP_REFS[index])
            for index, value in enumerate(STEP_VALUES)),
        item_width=95, item_height=50, horizontal_gap=10,
    )).ref(StepRef.PRESETS)
    step_layout = Grid(
        matrix=(
            (Text("STEP SIZE").ref(StepRef.STEP_TITLE),),
            (EMPTY,),
            (step_adjust,),
            (EMPTY,),
            (Text("PRESET STEPS").ref(StepRef.PRESET_TITLE),),
            (EMPTY,),
            (presets,),
        ),
        columns=Equal(1),
        rows=(28, Flex(2), 48, Flex(7), 26, Flex(7), 50),
    ).ref(StepRef.STEP_LAYOUT)

    return Grid(
        matrix=(
            (home_layout,),
            (EMPTY,),
            (Fill(ThemeColor.BORDER).ref(StepRef.DIVIDER),),
            (EMPTY,),
            (step_layout,),
        ),
        columns=Equal(1), rows=(155, 15, 1, 4, FLEX),
    ).padding(bottom=17).ref(StepRef.CONTROL)


def _content():
    axis_content = Column(
        _axis_layout(),
        _axis_actions(),
        gap=44,
    ).padding(top=13, bottom=17)
    warning = caution_overlay().size(CAUTION_WIDTH, CAUTION_HEIGHT) \
        .margin(top=31).align(horizontal="left", vertical="top") \
        .allow_overflow()
    axis = Overlay(axis_content, warning).ref(StepRef.AXIS)
    separator = Grid(
        matrix=((EMPTY, Fill(ThemeColor.BORDER).ref(StepRef.SEPARATOR), EMPTY),),
        columns=(15, 1, 19), rows=Equal(1),
    ).ref(StepRef.SEPARATOR_LAYOUT)
    return Grid(
        matrix=((axis, separator, _control_layout()),),
        columns=(Flex(400), 35, Flex(305)), rows=Equal(1),
    ).padding(left=18, top=1, right=18, bottom=3).ref(StepRef.ROOT)


PAGE = Page(content=_content(), bounds=MOVE_CONTENT, page_id=PAGE_ID)


def render(renderer, values):
    return PAGE.draw(renderer, values)


def update_status(renderer, values, axes=False):
    del axes
    return PAGE.update(renderer, values)
