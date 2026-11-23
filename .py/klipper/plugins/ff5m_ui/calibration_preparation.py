"""Shared confirmation layout for material-based and legacy Shaper calibrations."""

# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
# This file may be distributed under the terms of the GNU GPLv3 license.

from dataclasses import dataclass

from ui import Button, ButtonSurface, Column, Frame, PageTree, Row, Spacer, Text, ThemeColor, ThemeRole, When
from ui.actions import Action
from ui.bindings import bind, derived, state
from ui.identity import StateKey

from .calibration_layout import CONTENT, SIDE_INSET, VERTICAL_INSET, briefing
from .keys import AppPage
from .styles import UI_STYLES, UiStyle


OPTIONAL_CLEANING = frozenset(("z", "screws"))

# Each operation supplies its own briefing and temperature labels to the same layout.
BRIEFINGS = {
    "z": (AppPage.Z_OFFSET_PREPARATION, "Z offset preparation", "PAPER TEST",
          "Safe Z is set. Choose a material or skip cleaning.\n"
          "After preparation, choose a zone to test."),
    "screws": (AppPage.BED_SCREWS_PREPARATION, "Bed screw leveling", "BED PROBING",
               "Choose a material to clean the nozzle, or skip.\n"
               "The printer probes the bed and shows screw adjustments."),
    "mesh": (AppPage.BED_MESH_PREPARATION, "Auto bed leveling", "BEFORE YOU START",
             "Choose a material for heating and nozzle cleaning.\n"
             "The printer probes the bed and updates the 'auto' mesh."),
    "pid_bed": (AppPage.BED_PID_PREPARATION, "Bed PID calibration", "BEFORE YOU START",
                "The printer homes if needed before heating.\n"
                "Choose a material to set the tuning temperature."),
    "pid_extruder": (AppPage.HOTEND_PID_PREPARATION, "Hotend PID calibration", "BEFORE YOU START",
                     "Remove filament from the nozzle before starting.\n"
                     "Choose a material to set the tuning temperature."),
    "shaper": (AppPage.LEGACY_SHAPER_PREPARATION, "Input shaper calibration", "BEFORE YOU START",
               "Clear the bed and keep away from the printer.\n"
               "The toolhead homes if needed before measuring."),
}


class PreparationState(StateKey):
    __key_namespace__ = "ff5m_ui.calibration_preparation"
    MATERIAL = state(str, default="PLA")
    SKIP_CLEANING = state(bool, default=False)
    NOZZLE = state(str, default="205 C")
    BED = state(str, default="60 C")
    CAN_START = state(bool, default=True)


@dataclass(frozen=True)
class PreparationAction(Action):
    """Keep the existing calibration dispatch routes for the declarative controls."""

    route: str
    kind = "calibration_preparation"

    @property
    def wire_id(self):
        return self.route

    def as_dict(self):
        return {"kind": self.kind, "route": self.route}


START = PreparationAction("cal.confirm")
SKIP = PreparationAction("cal.clean.skip")


def _material_button(name):
    return Button(
        PreparationAction("cal.material." + name), name,
        state=derived(lambda material, skipped: "selected" if material == name and not skipped else "enabled",
                      bind(PreparationState.MATERIAL), bind(PreparationState.SKIP_CLEANING)),
    ).style(UiStyle.BUTTON_BOLD_8).ref("material." + name.lower())


def _skip_button():
    selected = bind(PreparationState.SKIP_CLEANING)
    font = "JetBrainsMono Bold 8pt"
    return ButtonSurface(
        SKIP,
        Frame(
            Text("SKIP", font=font, color=derived(
                lambda picked: ThemeRole.BUTTON_SELECTED_TEXT if picked else ThemeRole.BUTTON_TEXT, selected,
            )),
            background=derived(
                lambda picked: ThemeRole.BUTTON_SELECTED_BACKGROUND if picked else ThemeRole.BUTTON_BACKGROUND,
                selected,
            ),
            border=derived(lambda picked: ThemeRole.BUTTON_SELECTED_BORDER if picked else ThemeColor.DIM, selected),
            line_width=2,
        ),
        Frame(
            Text("SKIP", font=font, color=ThemeColor.BRIGHT),
            background=ThemeColor.PRESSED_BACKGROUND, border=ThemeColor.BRIGHT, line_width=2,
        ),
    ).ref("material.skip")


def _material_choices(materials, allow_skip):
    options = [_material_button(name) for name in materials]
    if not options:
        options.append(Text("NO MATERIALS ENABLED").style(UiStyle.DIM_LEFT))
    if allow_skip:
        options.append(_skip_button())
    return Column(
        Text("NOZZLE CLEANING" if allow_skip else "MATERIAL")
        .style(UiStyle.TEXT_LEFT).height("content"),
        Row(*options, gap=12).height(50),
        gap=8,
    ).height(80).ref("materials")


def _temperature_value(target):
    return Text(
        derived(lambda text: text or "--", bind(target)),
        color=ThemeColor.WARNING, font="JetBrainsMono Bold 8pt", horizontal="left",
    ).width("content")


def _temperature_details(kind):
    if kind in ("pid_bed", "pid_extruder"):
        bed = kind == "pid_bed"
        return Row(
            Text("BED TARGET" if bed else "NOZZLE TARGET").style(UiStyle.DIM_LEFT).width("content"),
            _temperature_value(PreparationState.BED if bed else PreparationState.NOZZLE),
            gap=10,
        ).ref("preparation.target")
    return Row(
        Text("NOZZLE").style(UiStyle.TEXT_LEFT).width("content"),
        _temperature_value(PreparationState.NOZZLE),
        When(bind(PreparationState.BED), Row(
            Text("BED").style(UiStyle.TEXT_LEFT).width("content"),
            _temperature_value(PreparationState.BED),
            gap=10,
        )).padding(left=14),
        gap=10,
    ).ref("preparation.targets")


def create_page(kind, materials):
    page_id, title, card_title, instructions = BRIEFINGS[kind]
    shaper = kind == "shaper"
    if shaper:
        choices = ()
        details = Text("STRONG VIBRATION EXPECTED", color=ThemeColor.WARNING,
                       font="JetBrainsMono Bold 8pt", horizontal="left")
    else:
        choices = (_material_choices(materials, kind in OPTIONAL_CLEANING), Spacer().height(18))
        details = _temperature_details(kind)

    page = PageTree(
        Column(
            briefing(card_title, instructions),
            Spacer().height(12 if shaper else 18),
            *choices,
            details.height("content"),
            Spacer(),
            Button(START, "START PREPARATION" if kind == "z" else "START CALIBRATION",
                   state=derived(lambda enabled: "enabled" if enabled else "disabled",
                                 bind(PreparationState.CAN_START)))
            .style(UiStyle.BUTTON_BOLD_12).height(64).ref("preparation.start"),
        ).padding(left=SIDE_INSET, right=SIDE_INSET, top=VERTICAL_INSET, bottom=VERTICAL_INSET)
        .ref("preparation.root"),
        CONTENT, page_id=page_id, state_schema=tuple(PreparationState), styles=UI_STYLES,
    )
    page.title = title
    page.show_back = True
    return page


def values(material, skip, nozzle, bed, can_start):
    return {
        PreparationState.MATERIAL: material,
        PreparationState.SKIP_CLEANING: skip,
        PreparationState.NOZZLE: "" if nozzle is None else "%.0f C" % nozzle,
        PreparationState.BED: "" if bed is None else "%.0f C" % bed,
        PreparationState.CAN_START: can_start,
    }


# Default declarations for Designer discovery. The screen uses its active material catalog.
EXAMPLE_MATERIALS = ("PLA", "PETG", "ABS", "TPU")
Z_OFFSET_PAGE = create_page("z", EXAMPLE_MATERIALS)
BED_SCREWS_PAGE = create_page("screws", EXAMPLE_MATERIALS)
BED_MESH_PAGE = create_page("mesh", EXAMPLE_MATERIALS)
BED_PID_PAGE = create_page("pid_bed", EXAMPLE_MATERIALS)
HOTEND_PID_PAGE = create_page("pid_extruder", EXAMPLE_MATERIALS)
LEGACY_SHAPER_PAGE = create_page("shaper", ())
