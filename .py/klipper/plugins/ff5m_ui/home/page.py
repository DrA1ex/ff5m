## Declarative Feather home dashboard.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from enum import Enum

from ui import ThemeColor, ThemeRole
from ui.bindings import bind, derived
from ui.components import Button, Fill, Frame, Hitbox, Text
from ui.layout import Overlay, PageTree, Rect

from ..keys import AppPage
from ..screen import ScreenPage
from ..styles import UI_STYLES, UiStyle
from .actions import FILAMENT, HEAT, JOB, LAST_JOB, MENU, MOVE, NETWORK
from .components import BOTTOM_VALUE, NETWORK_CARD, TEMPERATURE_CARD
from .state import HomeState, collect_dashboard, dashboard_values


PAGE_TITLE = "FORGE-X // FEATHER"
PAGE_BOUNDS = Rect(0, 0, 800, 442)
CLOCK_FONT = "Roboto 16pt"


class HomeRef(Enum):
    ROOT = "home.root"
    CLOCK = "home.clock"
    MENU = "home.menu"
    NOZZLE = "home.nozzle"
    BED = "home.bed"
    NETWORK = "home.network"
    JOB = "home.job"
    LAST_JOB = "home.last_job"
    MATERIAL = "home.material"
    TOOLHEAD = "home.toolhead"


def _temperature(current, target):
    return "%d / %d C" % (current, target)


def _heat_status(target):
    return "HEATING" if target > 0 else "OFF"


def _nozzle_color(target):
    return ThemeRole.TEMPERATURE_NOZZLE if target > 0 else ThemeColor.DIM


def _bed_color(target):
    return ThemeRole.TEMPERATURE_BED if target > 0 else ThemeColor.DIM


def _job_filename(active, filename):
    return filename if active else "NO ACTIVE JOB"


def _job_state(active, value):
    return value if active else "READY"


def _job_title_color(active):
    return ThemeColor.TEXT if active else ThemeColor.PRIMARY


def _job_state_color(value):
    return ThemeColor.WARNING if value == "PAUSED" else ThemeColor.PRIMARY


def _job_detail(active, value):
    return value if active else ""


def _job_progress(active, progress, elapsed, remaining):
    if not active:
        return ""
    return "%d%% // %s / %s" % (progress, elapsed, remaining)


def _homed_color(value):
    return ThemeColor.PRIMARY if value == "XYZ" else ThemeColor.WARNING


def _job_panel():
    active = bind(HomeState.JOB_ACTIVE)
    state = bind(HomeState.JOB_STATE)
    dynamic = Overlay(
        Fill(ThemeColor.PANEL),
        Text(
            derived(_job_filename, active, bind(HomeState.JOB_FILENAME)),
            color=derived(_job_title_color, active),
            font="JetBrainsMono Bold 8pt", horizontal="left",
            max_width=560, truncate=True,
        ).size(560, 24).offset(15, 6),
        Text(
            derived(_job_state, active, state),
            color=derived(_job_state_color, state),
            horizontal="right",
        ).style(UiStyle.TEXT).size(137, 24).offset(590, 6),
        Text(
            derived(_job_detail, active, bind(HomeState.JOB_DETAIL)),
            max_width=290, truncate=True,
        ).style(UiStyle.DIM_LEFT).size(290, 24).offset(15, 43),
        Text(
            derived(
                _job_progress, active, bind(HomeState.JOB_PROGRESS),
                bind(HomeState.JOB_ELAPSED), bind(HomeState.JOB_REMAINING)),
            horizontal="right",
            max_width=390, truncate=True,
        ).style(UiStyle.TEXT).size(390, 24).offset(337, 43),
    ).size(742, 76).offset(4, 32).repaint_boundary()

    return Frame(
        Overlay(
            Text("JOB STATUS").style(UiStyle.PRIMARY_LEFT).size(200, 24).offset(19, 8),
            dynamic,
        ),
        border=ThemeColor.BORDER, background=ThemeColor.PANEL, line_width=2,
    ).size(750, 112).offset(25, 220).ref(HomeRef.JOB)


def create_page():
    nozzle_target = bind(HomeState.NOZZLE_TARGET)
    bed_target = bind(HomeState.BED_TARGET)
    root = Overlay(
        Overlay(
            Fill(ThemeRole.HEADER_BACKGROUND),
            Text(
                bind(HomeState.CLOCK), color=ThemeRole.HEADER_TEXT,
                font=CLOCK_FONT, horizontal="left", max_width=132, truncate=True,
            ).size(132, 44).offset(10, 0),
        ).size(142, 46).offset(18, 8).repaint_boundary().ref(HomeRef.CLOCK),
        Button(MENU, "MENU").style(UiStyle.BUTTON_BOLD_8)
        .size(132, 38).offset(650, 11).ref(HomeRef.MENU),
        TEMPERATURE_CARD(
            instance_key=HomeRef.NOZZLE,
            x=25, title="NOZZLE", border=ThemeRole.TEMPERATURE_NOZZLE,
            value=derived(_temperature, bind(HomeState.NOZZLE), nozzle_target),
            status=derived(_heat_status, nozzle_target),
            status_color=derived(_nozzle_color, nozzle_target), ref=HomeRef.NOZZLE),
        TEMPERATURE_CARD(
            instance_key=HomeRef.BED,
            x=282, title="BED", border=ThemeRole.TEMPERATURE_BED,
            value=derived(_temperature, bind(HomeState.BED), bed_target),
            status=derived(_heat_status, bed_target),
            status_color=derived(_bed_color, bed_target), ref=HomeRef.BED),
        NETWORK_CARD(
            instance_key=HomeRef.NETWORK,
            name=bind(HomeState.NETWORK_NAME),
            address=bind(HomeState.NETWORK_ADDRESS), ref=HomeRef.NETWORK),
        _job_panel(),
        Fill(ThemeColor.BORDER).size(750, 1).offset(25, 345),
        Text("LAST JOB").style(UiStyle.DIM_LEFT)
        .size(240, 24).offset(28, 353),
        Text("MATERIAL").style(UiStyle.DIM_LEFT)
        .size(220, 24).offset(300, 353),
        Text("TOOLHEAD").style(UiStyle.DIM_LEFT)
        .size(180, 24).offset(570, 353),
        Fill(ThemeColor.BORDER).size(1, 74).offset(282, 353),
        Fill(ThemeColor.BORDER).size(1, 74).offset(542, 353),
        BOTTOM_VALUE(
            instance_key=HomeRef.LAST_JOB,
            value=bind(HomeState.LAST_JOB), color=ThemeColor.TEXT,
            x=25, width=257, text_x=3, max_width=240, ref=HomeRef.LAST_JOB),
        BOTTOM_VALUE(
            instance_key=HomeRef.MATERIAL,
            value=bind(HomeState.MATERIAL), color=ThemeColor.TEXT,
            x=283, width=259, text_x=17, max_width=220, ref=HomeRef.MATERIAL),
        BOTTOM_VALUE(
            instance_key=HomeRef.TOOLHEAD,
            value=bind(HomeState.HOMED_AXES),
            color=derived(_homed_color, bind(HomeState.HOMED_AXES)),
            x=543, width=232, text_x=27, max_width=160, ref=HomeRef.TOOLHEAD),
        Hitbox(HEAT).size(492, 132).offset(25, 72),
        Hitbox(NETWORK).size(236, 132).offset(539, 72),
        Hitbox(JOB).size(750, 112).offset(25, 220),
        Hitbox(LAST_JOB).size(257, 97).offset(25, 345),
        Hitbox(FILAMENT).size(259, 97).offset(283, 345),
        Hitbox(MOVE).size(232, 97).offset(543, 345),
    ).ref(HomeRef.ROOT)
    page = PageTree(
        root, PAGE_BOUNDS, page_id=AppPage.HOME, styles=UI_STYLES,
        component_templates=(TEMPERATURE_CARD, NETWORK_CARD, BOTTOM_VALUE,))
    page.title = PAGE_TITLE
    page.show_back = False
    return page


PAGE = create_page()


def render(host):
    eventtime = host.reactor.monotonic()
    current = collect_dashboard(host, eventtime)
    commands = host.renderer.begin_page(PAGE_TITLE)
    commands += PAGE.draw(host.renderer, dashboard_values(current))
    host.renderer.send(commands)
    host._last_dashboard = current


def update(host, eventtime):
    if host.page != ScreenPage.IDLE_HOME:
        return
    current = collect_dashboard(host, eventtime)
    if current == host._last_dashboard:
        return
    previous = host._last_dashboard
    host._last_dashboard = current
    if previous is None:
        commands = PAGE.draw(host.renderer, dashboard_values(current))
    else:
        commands = PAGE.update(host.renderer, dashboard_values(current))
    if commands:
        host.renderer.send(commands)
