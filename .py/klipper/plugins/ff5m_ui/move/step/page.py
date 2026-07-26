## Declarative step movement page for Feather.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from enum import Enum

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


def _all_homed(homed_x, homed_y, homed_z):
    return homed_x and homed_y and homed_z


def _status_label(homed_x, homed_y, homed_z):
    missing = "".join(
        axis for axis, homed in (("X", homed_x), ("Y", homed_y),
                                 ("Z", homed_z)) if not homed)
    return "HOMED: XYZ" if not missing else "NOT HOMED: %s" % missing


def _homed_color(*values):
    return "35d9e6" if all(values) else "f2c94c"


def _homed_label(*values):
    return "HOMED" if all(values) else "HOME"


def _axis_status(label, homed_bindings, refs):
    color = derived(_homed_color, *homed_bindings)
    state_label = derived(_homed_label, *homed_bindings)
    return Overlay(
        Fill("050c0f").ref(refs[0]),
        Stroke(color, 2).ref(refs[1]),
        Text(label, color=color).height(44).align(vertical="top").ref(refs[2]),
        Text(state_label, color=color)
        .margin(top=34, bottom=4).ref(refs[3]),
    ).ref(refs[4]).repaint_boundary()


def _status_card():
    homed = (
        bind(ToolheadState.HOMED_X),
        bind(ToolheadState.HOMED_Y),
        bind(ToolheadState.HOMED_Z),
    )
    return Overlay(
        Fill("030607").ref(StepRef.STATUS_BACKGROUND),
        Text(
            derived(_status_label, *homed),
            color=derived(
                lambda x, y, z: "35d9e6" if _all_homed(x, y, z)
                else "f2c94c",
                *homed),
        ).height(34).align(vertical="top").ref(StepRef.STATUS_STATE),
        Text(
            derived(
                lambda x, y: "X %7.2f   Y %7.2f" % (x, y),
                bind(ToolheadState.X), bind(ToolheadState.Y)),
            color="d9e4e8",
        ).height(46).margin(top=34).align(vertical="top") \
         .ref(StepRef.STATUS_XY),
        Text(
            derived(lambda z: "Z %7.2f" % z, bind(ToolheadState.Z)),
            color="d9e4e8",
        ).height(26).margin(top=70).align(vertical="top").allow_overflow() \
         .ref(StepRef.STATUS_Z),
    ).ref(StepRef.STATUS_CARD).repaint_boundary()


def _axis_layout():
    xy_homed = (
        bind(ToolheadState.HOMED_X), bind(ToolheadState.HOMED_Y))
    z_homed = (bind(ToolheadState.HOMED_Z),)
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
                derived(lambda value: "%g MM" % value,
                        bind(MoveState.JOG_STEP)),
                color="d9e4e8",
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
                    "selected" if current == expected else "enabled",
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
            (Fill("295c66").ref(StepRef.DIVIDER),),
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
        matrix=((EMPTY, Fill("295c66").ref(StepRef.SEPARATOR), EMPTY),),
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
