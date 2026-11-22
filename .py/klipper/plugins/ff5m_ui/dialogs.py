## Product dialogs as declarative screen layers.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""A dialog is a small tree built once when it opens, from data already known,
and drawn by the core. Its buttons carry one semantic action per meaning; state
holds only what changes while it shows."""

from dataclasses import dataclass
from enum import Enum

from ui.actions import Action, Command, Increment
from ui.bindings import bind, state
from ui.identity import CommandKey, StateKey

from .keys import AppPage


class DialogState(StateKey):
    __key_namespace__ = "ui.pages.dialogs.DialogState"
    PAGE = state(int, default=0, minimum=0)
    TITLE = state(str, default="")
    DETAIL = state(str, default="")
    # Whether a status dialog's tree holds its cancel button, and the button's look.
    CANCELLABLE = state(bool, default=False)
    CANCEL_LABEL = state(str, default="")
    CANCEL_STATE = state(str, default="danger")


class DialogRoute(Enum):
    OK = "message.ok"
    RENDER_HOME = "error.screen_home"
    SAVE_MESH = "mesh.save"
    EXCLUDE_MODELS = "exclude.confirm"
    CANCEL_HELD_PRINT = "timelapse.wait.cancel"
    SAVE_SHAPER = "shaper.save.confirm"
    LEAVE_SHAPER = "shaper.leave.confirm"
    RESET_WIFI_PASSWORD = "net.reset.saved"
    KEEP_WAITING_FOR_NETWORK = "net.keep"
    CANCEL_NETWORK = "net.cancel"
    RESTART = "error.restart"
    FIRMWARE_RESTART = "error.firmware_restart"
    CANCEL_COLD_PULL = "coldpull.cancel"
    SETUP_APPLY = "setup.apply.confirm"
    SETUP_FINISH = "setup.finish.confirm"
    SETUP_SKIP = "setup.skip.confirm"


@dataclass(frozen=True)
class DialogAction(Action):
    """Typed dialog button action preserving the established wire identifier."""

    route: DialogRoute
    kind = "dialog_action"

    def __post_init__(self):
        if not isinstance(self.route, DialogRoute):
            raise TypeError("DialogAction route must be a DialogRoute member")

    @property
    def wire_id(self):
        return self.route.value

    def as_dict(self):
        return {"kind": self.kind, "route": self.route.value}


OK = DialogAction(DialogRoute.OK)
RENDER_HOME = DialogAction(DialogRoute.RENDER_HOME)
SAVE_MESH = DialogAction(DialogRoute.SAVE_MESH)
EXCLUDE_MODELS = DialogAction(DialogRoute.EXCLUDE_MODELS)
CANCEL_HELD_PRINT = DialogAction(DialogRoute.CANCEL_HELD_PRINT)
SAVE_SHAPER = DialogAction(DialogRoute.SAVE_SHAPER)
LEAVE_SHAPER = DialogAction(DialogRoute.LEAVE_SHAPER)
RESET_WIFI_PASSWORD = DialogAction(DialogRoute.RESET_WIFI_PASSWORD)
KEEP_WAITING_FOR_NETWORK = DialogAction(DialogRoute.KEEP_WAITING_FOR_NETWORK)
CANCEL_NETWORK = DialogAction(DialogRoute.CANCEL_NETWORK)
RESTART = DialogAction(DialogRoute.RESTART)
FIRMWARE_RESTART = DialogAction(DialogRoute.FIRMWARE_RESTART)
CANCEL_COLD_PULL = DialogAction(DialogRoute.CANCEL_COLD_PULL)
SETUP_APPLY = DialogAction(DialogRoute.SETUP_APPLY)
SETUP_FINISH = DialogAction(DialogRoute.SETUP_FINISH)
SETUP_SKIP = DialogAction(DialogRoute.SETUP_SKIP)

PREVIOUS_PAGE = Increment(DialogState.PAGE, -1)
NEXT_PAGE = Increment(DialogState.PAGE, 1)


class Prompt(CommandKey):
    __key_namespace__ = "ui.pages.dialogs.Prompt"
    RUN = "prompt.run"


def prompt_action(gcode):
    """The action of a Klipper prompt's button: the reaction is its G-code."""
    return Command(Prompt.RUN, payload=gcode)


def dialog_page(title, lines, buttons, *, button_groups=(), width=480, height=220, tone="warning"):
    """The page of a modal dialog: centred on the screen, it grows from this size to fit its content."""
    from ui.components import Dialog
    from ui.layout import PageTree, Rect

    return PageTree(
        Dialog(title, tuple(lines), tuple(buttons), button_groups=button_groups, tone=tone, modal=True,
               page=bind(DialogState.PAGE), page_actions=(PREVIOUS_PAGE, NEXT_PAGE)).size(width, height),
        Rect(0, 0, 800, 480), page_id=AppPage.DIALOG)


def status_page(values, *, buttons=(), cancel_action=None, tone="warning", description=None,
                description_font=None, preserve_header_action=True):
    """The page of a modal status dialog, showing DialogState values, which the screen writes while it shows. A
    tree holds the cancel button or not: when CANCELLABLE changes the dialog opens again."""
    from ui.components import StatusDialog
    from ui.layout import PageTree, Rect
    from ui.layout_helpers import DIALOG_MIN_HEIGHT, DIALOG_MIN_WIDTH

    if cancel_action is not None and values.get(DialogState.CANCELLABLE):
        buttons += ((cancel_action, bind(DialogState.CANCEL_LABEL), bind(DialogState.CANCEL_STATE)),)
    return PageTree(
        StatusDialog(bind(DialogState.TITLE), bind(DialogState.DETAIL), buttons, tone=tone, modal=True,
                     description=description, description_font=description_font,
                     preserve_header_action=preserve_header_action)
        .size(DIALOG_MIN_WIDTH, DIALOG_MIN_HEIGHT),
        Rect(0, 0, 800, 480), state=values, page_id=AppPage.DIALOG, state_schema=tuple(DialogState))


def touch_unavailable_page():
    """Keep the warning visible until touch returns, with the header action covered."""
    return status_page(
        {DialogState.TITLE: "Touch input unavailable"},
        description="THE TOUCH DEVICE IS NOT AVAILABLE. WAITING FOR AUTOMATIC RECONNECTION.",
        description_font="JetBrainsMono 12pt",
        preserve_header_action=False)
