# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
# This file may be distributed under the terms of the GNU GPLv3 license.

from ui import FLEX, Button, Column, Flex, Frame, Grid, ListView, PageTree, Rect, Spacer, Template, Text, ThemeColor, When
from ui.bindings import bind, derived, item, state
from ui.identity import StateKey

from ..keys import AppPage
from ..styles import UI_STYLES, UiStyle
from .actions import CANCEL


class ProgressState(StateKey):
    __key_namespace__ = "ff5m_ui.shaper.progress"
    TITLE = state(str, default="MEASURING X RESONANCE")
    DETAIL = state(str, default="SWEEP 43 Hz / 5..100 Hz")
    TELEMETRY = state(str, default="0:40 ELAPSED / 32000 RECORDED SAMPLES")
    CANCELLING = state(bool, default=False)
    STEPS = state(list, default=[
        dict(id=axis + step, kind="stage", label=axis + " " + step,
             color=ThemeColor.SECONDARY if axis == "X" and step == "SWEEP" else ThemeColor.DIM,
             background=ThemeColor.SECONDARY_DARK if axis == "X" and step == "SWEEP" else ThemeColor.PANEL)
        for axis in ("X", "Y") for step in ("SWEEP", "FIT")])


STAGE = Template("stage", Frame(
    Text(item("label"), color=item("color"), font="JetBrainsMono Bold 10pt", truncate=True),
    border=None, background=item("background"), line_width=0,
), sample=dict(id="x-sweep", kind="stage", label="X SWEEP", color=ThemeColor.SECONDARY,
               background=ThemeColor.SECONDARY_DARK))

root = Column(
    Column(
        Text(bind(ProgressState.TITLE), font="JetBrainsMono Bold 12pt", color=ThemeColor.SECONDARY,
             truncate=True).height(34).ref("shaper.progress.title"),
        Text(bind(ProgressState.DETAIL), truncate=True).style(UiStyle.TEXT)
            .height(24).ref("shaper.progress.detail"),
        Text(bind(ProgressState.TELEMETRY), truncate=True).style(UiStyle.DIM)
            .height(24).ref("shaper.progress.telemetry"),
        gap=6,
    ).height("content"),
    Spacer(),
    ListView(bind(ProgressState.STEPS), item_key=item("id"), template_key=item("kind"),
             templates=(STAGE,), direction="horizontal", gap=12).height(52).ref("shaper.progress.stages"),
    Spacer(),
    Column(
        When(bind(ProgressState.CANCELLING),
             Text("Waiting for the next safe sweep boundary").style(UiStyle.DIM))
            .height(24).ref("shaper.progress.cancelling"),
        Grid(matrix=((Spacer(), Button(CANCEL,
               derived(lambda cancelling: "CANCELLING..." if cancelling else "CANCEL", bind(ProgressState.CANCELLING)),
               state=derived(lambda cancelling: "busy" if cancelling else "danger", bind(ProgressState.CANCELLING)))
            .style(UiStyle.BUTTON_BOLD_12).ref("shaper.progress.cancel"), Spacer()),),
             columns=(FLEX, Flex(2), FLEX), rows=(FLEX,)).height(64),
        gap=12,
    ).height("content"),
).padding(left=24, right=24, top=18, bottom=18).ref("shaper.progress.root")

PAGE = PageTree(root, Rect(0, 56, 800, 386), page_id=AppPage.SHAPER_PROGRESS,
                state_schema=tuple(ProgressState), styles=UI_STYLES)
PAGE.title = "Input shaper calibration"
PAGE.show_back = False


def snapshot(status):
    """Retain small scalar telemetry only; never import acquisition or numerics."""
    result = {name: status[name] for name in (
        "job_id", "state", "axis", "requested_axes", "completed_axes",
        "frequency", "freq_start", "freq_end", "samples", "cancel_requested"
    )}
    result["elapsed"] = int(status["elapsed"])
    return result


def values(status):
    axis, phase = status["axis"], status["state"]
    cancelling = status["cancel_requested"]
    title = {"preparing": "PREPARING CALIBRATION", "positioning": "POSITIONING FOR %s AXIS",
             "measuring": "MEASURING %s RESONANCE", "processing": "PROCESSING %s MEASUREMENTS"}.get(
                 phase, "CALIBRATING %s AXIS")
    if "%s" in title:
        title %= axis.upper()
    detail = "Preparing the resonance test"
    if phase == "positioning":
        detail = "Moving to the measurement point"
    elif phase == "measuring":
        frequency = status["frequency"]
        detail = ("SWEEP %.0f Hz  /  %.0f..%.0f Hz" % (
            frequency, status["freq_start"], status["freq_end"])) if frequency is not None else "STARTING SWEEP"
    elif phase == "processing":
        detail = "Calculating spectrum and tuning input shapers"
    steps = []
    for a in status["requested_axes"]:
        for step in ("SWEEP", "FIT"):
            active = a == axis and step == ("FIT" if phase == "processing" else "SWEEP") and phase != "preparing"
            complete = a in status["completed_axes"] or (a == axis and step == "SWEEP" and phase == "processing")
            steps.append(dict(id=a + step, kind="stage", label=a.upper() + " " + step,
                              color=ThemeColor.SECONDARY if active else ThemeColor.PRIMARY if complete else ThemeColor.DIM,
                              background=ThemeColor.SECONDARY_DARK if active else ThemeColor.PANEL))
    return {
        ProgressState.TITLE: "CANCELLING..." if cancelling else title,
        ProgressState.DETAIL: detail,
        ProgressState.TELEMETRY: "%d:%02d ELAPSED / %d RECORDED SAMPLES" % (
            status["elapsed"] // 60, status["elapsed"] % 60, status["samples"]),
        ProgressState.CANCELLING: cancelling, ProgressState.STEPS: steps,
    }


def render(renderer, status):
    return renderer.begin_page(PAGE.title) + PAGE.draw(renderer, values(status))
