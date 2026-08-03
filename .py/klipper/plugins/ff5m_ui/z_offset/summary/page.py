## Declarative Z-offset summary page for Feather.

from enum import Enum

from ui.actions import SetValue, Toggle
from ui.bindings import bind, derived
from ui.components import Button, Dialog, Text
from ui.layout import Column, Equal, Flex, Grid, Overlay, PageTree as Page, Spacer, When
from ...keys import AppPage
from ..actions import DISCARD_CONFIRM, SAVE, SELECTION_NEXT, ZONE_ACTIONS
from ..common import CONTENT, FONT, compact
from .state import SummaryState
from ui import ThemeColor, ThemeRole


PAGE_ID = AppPage.Z_OFFSET_SUMMARY


class SummaryRef(Enum):
    ROOT = "summary.root"
    LAYOUT = "summary.layout"
    REAR = "summary.rear"
    REAR_LEFT = "summary.rear_left"
    CENTER = "summary.center"
    REAR_RIGHT = "summary.rear_right"
    FRONT = "summary.front"
    FRONT_LEFT = "summary.front_left"
    FRONT_RIGHT = "summary.front_right"
    STATUS = "summary.status"
    SPACER_1 = "summary.spacer.1"
    SPACER_2 = "summary.spacer.2"
    SPACER_3 = "summary.spacer.3"
    SPACER_4 = "summary.spacer.4"
    CHOICES = "summary.choices"
    SELECTION = "summary.selection"
    LOAD = "summary.load"
    SAVE_LAYOUT = "summary.save.layout"
    SAVE = "summary.save"
    DISCARD = "summary.discard"
    DISCARD_DIALOG = "summary.discard.dialog"


_ZONE_REFS = {
    "rear_left": SummaryRef.REAR_LEFT,
    "center": SummaryRef.CENTER,
    "rear_right": SummaryRef.REAR_RIGHT,
    "front_left": SummaryRef.FRONT_LEFT,
    "front_right": SummaryRef.FRONT_RIGHT,
}


def _zone_caption(labels, results, key):
    if key not in results:
        return labels[key]
    return "%s  %+.3f" % (labels[key], results[key])


def _zone_state(results, key):
    return "selected" if key in results else "enabled"


def _status(spread, positional_warning, results):
    if spread > positional_warning:
        return "POSITIONAL SPREAD %.3f MM - CHECK BED / PROBE" % spread
    count = len(results)
    if count:
        return "%d ZONE%s MEASURED" % (count, "" if count == 1 else "S")
    return "SELECT A POSITION TO START THE PAPER TEST"


def _status_color(spread, positional_warning):
    return ThemeColor.WARNING if spread > positional_warning else ThemeColor.DIM


def _selection_label(selected, average, labels, results):
    if selected == "average":
        return "USE AVERAGE  %+.3f" % average
    if selected in labels and selected in results:
        return "USE %s  %+.3f" % (labels[selected], results[selected])
    return "NO RESULT SELECTED"


def _selection_state(results):
    return "enabled" if results else "disabled"


def _zone_button(key):
    return Button(
        ZONE_ACTIONS[key],
        derived(
            lambda labels, results, zone=key:
            _zone_caption(labels, results, zone),
            bind(SummaryState.ZONE_LABELS), bind(SummaryState.RESULTS)),
        state=derived(
            lambda results, zone=key: _zone_state(results, zone),
            bind(SummaryState.RESULTS)),
        font=FONT,
    ).ref(_ZONE_REFS[key])


def _content():
    rear = Grid(
        matrix=((
            _zone_button("rear_left"),
            _zone_button("center"),
            _zone_button("rear_right"),
        ),),
        columns=Equal(3), rows=Equal(1), gap=(10, 0),
    ).padding(left=75, right=75).ref(SummaryRef.REAR)
    front = Grid(
        matrix=((_zone_button("front_left"), _zone_button("front_right")),),
        columns=Equal(2), rows=Equal(1), gap=(20, 0),
    ).padding(left=130, right=130).ref(SummaryRef.FRONT)
    status = Text(
        derived(
            _status,
            bind(SummaryState.SPREAD),
            bind(SummaryState.POSITIONAL_WARNING),
            bind(SummaryState.RESULTS)),
        color=derived(
            _status_color,
            bind(SummaryState.SPREAD),
            bind(SummaryState.POSITIONAL_WARNING)),
        font=FONT,
    ).height(30).margin(left=20, right=20) \
     .ref(SummaryRef.STATUS).repaint_boundary()
    selection_state = derived(
        _selection_state, bind(SummaryState.RESULTS))
    choices = compact(Grid(
        matrix=((
            Button(
                SELECTION_NEXT,
                derived(
                    _selection_label,
                    bind(SummaryState.SELECTED),
                    bind(SummaryState.AVERAGE),
                    bind(SummaryState.ZONE_LABELS),
                    bind(SummaryState.RESULTS)),
                state=selection_state,
            ).ref(SummaryRef.SELECTION),
            Button(
                Toggle(SummaryState.LOAD_ZOFFSET),
                derived(
                    lambda enabled: "AUTO LOAD: %s" % (
                        "ON" if enabled else "OFF"),
                    bind(SummaryState.LOAD_ZOFFSET)),
                state=derived(
                    lambda enabled: "selected" if enabled else "enabled",
                    bind(SummaryState.LOAD_ZOFFSET)),
            ).ref(SummaryRef.LOAD),
        ),),
        columns=(Flex(455), Flex(200)), rows=Equal(1), gap=(15, 0),
    )).padding(left=65, right=65).ref(SummaryRef.CHOICES)
    save = Grid(
        matrix=((Button(
            SAVE, "SAVE SELECTED Z OFFSET",
            state=selection_state, font="JetBrainsMono Bold 12pt",
        ).ref(SummaryRef.SAVE),),),
        columns=Equal(1), rows=Equal(1),
    ).height(82).padding(left=65, right=65).ref(SummaryRef.SAVE_LAYOUT)
    content = Column(
        rear.height(64),
        Spacer().grow(10).ref(SummaryRef.SPACER_1),
        front.height(64),
        Spacer().grow(5).ref(SummaryRef.SPACER_2),
        status,
        Spacer().grow(13).ref(SummaryRef.SPACER_3),
        choices.height(58),
        Spacer().grow(18).ref(SummaryRef.SPACER_4),
        save,
    ).padding(top=16, bottom=26).ref(SummaryRef.LAYOUT)
    discard = When(
        derived(lambda dialog: dialog == "discard",
                bind(SummaryState.DIALOG)),
        Dialog(
            "DISCARD Z CALIBRATION?",
            (
                "ALL MEASURED ZONE RESULTS WILL BE LOST.",
                "THE ORIGINAL MESH AND RUNTIME OFFSET WILL BE RESTORED.",
            ),
            (
                (SetValue(SummaryState.DIALOG, None), "KEEP", "enabled"),
                (DISCARD_CONFIRM, "DISCARD", "danger"),
            ),
            tone="danger", modal=True,
        ).size(630, 275).margin(top=49)
         .align(horizontal="center", vertical="top")
         .ref(SummaryRef.DISCARD_DIALOG),
    ).ref(SummaryRef.DISCARD)
    return Overlay(content, discard).ref(SummaryRef.ROOT)


PAGE = Page(_content(), CONTENT, page_id=PAGE_ID)


def render(renderer, values):
    return PAGE.draw(renderer, values)
