## Shared network selection pages for Feather.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from ui import Command, ThemeColor
from ui.actions import ItemCommand
from ui.bindings import bind, derived, item, state
from ui.components import Button, Frame, Hitbox, Text
from ui.identity import CommandKey, StateKey
from ui.layout import Column, Flex, FLEX, Grid, ListView, Overlay, PageTree, Row, Spacer, StateCase, Template, When

from .keys import AppPage
from .setup.common import ACTION_HEIGHT, CONTENT, ROW_GAP, page_controls, page_root
from .styles import UI_STYLES, UiStyle


class NetworkCommand(CommandKey):
    SCAN = "network.scan"
    ETHERNET = "network.ethernet"
    SELECT = "network.select"
    CONTINUE = "network.continue"


class NetworkState(StateKey):
    STATUS = state(str, default="NOT CONNECTED")
    DETAIL = state(str, default="Connect your printer for updates and browser access.")
    CONNECTED = state(bool, default=False)
    WIFI = state(str, default="enabled")
    ETHERNET = state(str, default="enabled")
    ACTION = state(str, default="CONTINUE OFFLINE")


class ScanState(StateKey):
    ROWS = state(list, default=[
        dict(index=0, ssid="Workshop", band="5 GHz", signal="-42 dBm", saved=True),
        dict(index=1, ssid="Home Wi-Fi", band="2.4 GHz", signal="-67 dBm", saved=False),
    ])
    EMPTY = state(bool, default=False)
    PAGE = state(int, default=0)
    PAGE_COUNT = state(int, default=1)
    PREVIOUS = state(str, default="disabled")
    NEXT = state(str, default="disabled")


HOME_PAGE = PageTree(
    page_root(
        Column(
            Frame(Column(
                Text(bind(NetworkState.STATUS),
                     color=derived(lambda connected: ThemeColor.SUCCESS if connected else ThemeColor.PRIMARY,
                                   bind(NetworkState.CONNECTED)),
                     font="JetBrainsMono Bold 12pt", horizontal="left").height(30),
                Text(bind(NetworkState.DETAIL), wrap=True).style(UiStyle.TEXT_LEFT).grow(),
                gap=8,
            )).style(UiStyle.FRAME_PANEL).height(114).padding(left=20, right=20, top=16, bottom=16),
            Row(
                Button(Command(NetworkCommand.SCAN), "WI-FI", subtitle="FIND A NETWORK",
                       state=bind(NetworkState.WIFI)).style(UiStyle.BUTTON_BOLD_12),
                Button(Command(NetworkCommand.ETHERNET), "ETHERNET", subtitle="USE A NETWORK CABLE",
                       state=bind(NetworkState.ETHERNET)).style(UiStyle.BUTTON_BOLD_12),
                gap=12,
            ).height(88),
            gap=18,
        ).height("content").align(vertical="center"),
        Row(Spacer(), When(bind(NetworkState.ACTION),
                          Button(Command(NetworkCommand.CONTINUE), bind(NetworkState.ACTION), state="selected")
                          .style(UiStyle.BUTTON_BOLD_11)), gap=12),
    ),
    CONTENT, page_id=AppPage.NETWORK_HOME, styles=UI_STYLES, state_schema=tuple(NetworkState),
)

WIFI_ENTRY_TEMPLATE = Overlay(
    Frame(
        border=ThemeColor.PRIMARY,background=ThemeColor.PANEL,line_width=2
    ),
    Grid(
        ((
            Overlay(
                When(
                    item("saved"),
                    Column(
                        Text(item("band")).style(UiStyle.DIM_LEFT).height("content"),
                        Text("SAVED").style(UiStyle.DIM_LEFT).height("content"),
                    ).height("content").align(vertical="center"),
                ),
                StateCase(
                    Text(item("band")).style(UiStyle.DIM_LEFT),
                    selector=item("saved"), expected=False,
                ),
            ),
            Text(item("ssid"), truncate=True).style(UiStyle.VALUE),
            Text(item("signal"), horizontal="right").style(UiStyle.DIM),
        ),),
        columns=(FLEX, Flex(3), FLEX),
    ).padding(left=24, right=24),
    Hitbox(ItemCommand(NetworkCommand.SELECT, item("index"))),
).height(54)

SCAN_BODY = Overlay(
    ListView(
        bind(ScanState.ROWS),
        item_key=item("index"),
        templates=(
            Template("network", WIFI_ENTRY_TEMPLATE),
        ),
        gap=ROW_GAP,
    ).height("content").align(vertical="top"),
    When(
        bind(ScanState.EMPTY),
        Text(
            "No supported Wi-Fi networks found.",
            color=ThemeColor.WARNING,
            font="JetBrainsMono 12pt",
            horizontal="center",
            vertical="center",
        ),
    ),
)

SCAN_ACTIONS = Row(
    page_controls(ScanState),
    Button(
        Command(NetworkCommand.SCAN),
        "RESCAN",
        state="selected",
    ).style(UiStyle.BUTTON_BOLD_11),
    gap=12,
)

SCAN_PAGE = PageTree(
    Grid(
        matrix=((SCAN_BODY,), (SCAN_ACTIONS,)), columns=(FLEX,),
        rows=(FLEX, ACTION_HEIGHT), gap=12
    ).padding(left=22, right=22, top=8, bottom=8),
    CONTENT,
    page_id=AppPage.WIFI_SCAN,
    styles=UI_STYLES,
    state_schema=tuple(ScanState),
)
