## Declarative Feather home dashboard.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from enum import Enum

from ui import ThemeColor, ThemeRole
from ui.bindings import bind, derived
from ui.components import Button, Fill, Hitbox, Panel, Text
from ui.layout import Overlay, PageTree, Rect

from ..keys import AppPage
from ..screen import ScreenPage
from .actions import FILAMENT, HEAT, JOB, LAST_JOB, MENU, MOVE, NETWORK
from .state import HomeState, collect_dashboard, dashboard_values


PAGE_TITLE = "FORGE-X // FEATHER"
PAGE_BOUNDS = Rect(0, 0, 800, 442)
FONT = "JetBrainsMono 8pt"
VALUE_FONT = "JetBrainsMono 12pt"
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


def _placed(node, x, y, width, height):
    return node.size(width, height).offset(x, y)


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


def _card(x, width, label, border, dynamic, key):
    return _placed(
        Overlay(
            Panel(border=border, background=ThemeColor.PANEL, line_width=2),
            _placed(Text(label, color=border, font=FONT),
                    0, 8, width, 40),
            _placed(dynamic, 3, 40, width - 6, 87),
        ).ref(key), x, 72, width, 132)


def _temperature_value(value, target, status_color):
    return Overlay(
        Fill(ThemeColor.PANEL),
        _placed(Text(
            derived(_temperature, bind(value), bind(target)),
            color=ThemeColor.TEXT, font=VALUE_FONT), 0, 10, 229, 34),
        _placed(Text(
            derived(_heat_status, bind(target)),
            color=derived(status_color, bind(target)), font=FONT),
            0, 52, 229, 34),
    ).repaint_boundary()


def _network_value():
    return Overlay(
        Fill(ThemeColor.PANEL),
        _placed(Text(
            bind(HomeState.NETWORK_NAME), color=ThemeColor.TEXT, font=FONT,
            max_width=210, truncate=True), 0, 10, 230, 34),
        _placed(Text(
            bind(HomeState.NETWORK_ADDRESS), color=ThemeColor.PRIMARY,
            font=FONT, max_width=210, truncate=True), 0, 52, 230, 34),
    ).repaint_boundary()


def _job_panel():
    active = bind(HomeState.JOB_ACTIVE)
    state = bind(HomeState.JOB_STATE)
    dynamic = _placed(
        Overlay(
            Fill(ThemeColor.PANEL),
            _placed(Text(
                derived(_job_filename, active, bind(HomeState.JOB_FILENAME)),
                color=derived(_job_title_color, active),
                font="JetBrainsMono Bold 8pt", horizontal="left",
                max_width=560, truncate=True), 15, 6, 560, 24),
            _placed(Text(
                derived(_job_state, active, state),
                color=derived(_job_state_color, state), font=FONT,
                horizontal="right"), 590, 6, 137, 24),
            _placed(Text(
                derived(_job_detail, active, bind(HomeState.JOB_DETAIL)),
                color=ThemeColor.DIM, font=FONT, horizontal="left",
                max_width=330, truncate=True), 15, 43, 330, 24),
            _placed(Text(
                derived(
                    _job_progress, active, bind(HomeState.JOB_PROGRESS),
                    bind(HomeState.JOB_ELAPSED),
                    bind(HomeState.JOB_REMAINING)),
                color=ThemeColor.TEXT, font=FONT, horizontal="right",
                max_width=350, truncate=True), 377, 43, 350, 24),
        ).repaint_boundary(), 4, 32, 742, 76)
    return _placed(
        Overlay(
            Panel(border=ThemeColor.BORDER, background=ThemeColor.PANEL,
                  line_width=2),
            _placed(Text(
                "JOB STATUS", color=ThemeColor.PRIMARY, font=FONT,
                horizontal="left"), 19, 8, 200, 24),
            dynamic,
        ).ref(HomeRef.JOB), 25, 220, 750, 112)


def _bottom_value(value, color, x, width, text_x, max_width, key):
    return _placed(
        Overlay(
            Fill(ThemeColor.BACKGROUND),
            _placed(Text(
                value, color=color, font=FONT, horizontal="left",
                max_width=max_width, truncate=True),
                text_x, 5, max_width, 24),
        ).repaint_boundary().ref(key), x, 393, width, 34)


def create_page():
    nozzle = _temperature_value(
        HomeState.NOZZLE, HomeState.NOZZLE_TARGET, _nozzle_color)
    bed = _temperature_value(
        HomeState.BED, HomeState.BED_TARGET, _bed_color)
    root = Overlay(
        _placed(
            Overlay(
                Fill(ThemeRole.HEADER_BACKGROUND),
                _placed(Text(
                    bind(HomeState.CLOCK), color=ThemeRole.HEADER_TEXT,
                    font=CLOCK_FONT, horizontal="left",
                    max_width=132, truncate=True), 10, 0, 132, 44),
            ).repaint_boundary().ref(HomeRef.CLOCK), 18, 8, 142, 46),
        _placed(Button(
            MENU, "MENU", font="JetBrainsMono Bold 8pt").ref(HomeRef.MENU),
            650, 11, 132, 38),
        _card(25, 235, "NOZZLE", ThemeRole.TEMPERATURE_NOZZLE,
              nozzle, HomeRef.NOZZLE),
        _card(282, 235, "BED", ThemeRole.TEMPERATURE_BED,
              bed, HomeRef.BED),
        _card(539, 236, "NETWORK", ThemeColor.PRIMARY,
              _network_value(), HomeRef.NETWORK),
        _job_panel(),
        _placed(Fill(ThemeColor.BORDER), 25, 345, 750, 1),
        _placed(Text(
            "LAST JOB", color=ThemeColor.DIM, font=FONT,
            horizontal="left"), 28, 353, 240, 24),
        _placed(Text(
            "MATERIAL", color=ThemeColor.DIM, font=FONT,
            horizontal="left"), 300, 353, 220, 24),
        _placed(Text(
            "TOOLHEAD", color=ThemeColor.DIM, font=FONT,
            horizontal="left"), 570, 353, 180, 24),
        _placed(Fill(ThemeColor.BORDER), 282, 353, 1, 74),
        _placed(Fill(ThemeColor.BORDER), 542, 353, 1, 74),
        _bottom_value(
            bind(HomeState.LAST_JOB), ThemeColor.TEXT,
            25, 257, 3, 240, HomeRef.LAST_JOB),
        _bottom_value(
            bind(HomeState.MATERIAL), ThemeColor.TEXT,
            283, 259, 17, 220, HomeRef.MATERIAL),
        _bottom_value(
            bind(HomeState.HOMED_AXES),
            derived(_homed_color, bind(HomeState.HOMED_AXES)),
            543, 232, 27, 160, HomeRef.TOOLHEAD),
        _placed(Hitbox(HEAT), 25, 72, 492, 132),
        _placed(Hitbox(NETWORK), 539, 72, 236, 132),
        _placed(Hitbox(JOB), 25, 220, 750, 112),
        _placed(Hitbox(LAST_JOB), 25, 345, 257, 97),
        _placed(Hitbox(FILAMENT), 283, 345, 259, 97),
        _placed(Hitbox(MOVE), 543, 345, 232, 97),
    ).ref(HomeRef.ROOT)
    page = PageTree(root, PAGE_BOUNDS, page_id=AppPage.HOME)
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
