## Declarative object-exclusion page and its item geometry.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from ui import ThemeColor
from ui.actions import Command, ItemCommand
from ui.bindings import bind, derived, item, resolve, state
from ui.components import Button, Frame, Hitbox, Text
from ui.identity import CommandKey, StateKey
from ui.layout import Column, Dirty, Equal, FLEX, Grid, ListView, Overlay, PageTree, Rect, Spacer, StateCase, Template
from ui.properties import EditorSpec, Invalidation, PropertySpec, SourceSpec, property_schema

from .keys import AppPage
from .printing.actions import PrintingAction, PrintingRoute


class ExcludeState(StateKey):
    __key_namespace__ = "ui.pages.exclude_model.state.ExcludeState"
    OBJECTS = state(list, default=[])
    ROWS = state(list, default=[])
    COUNT = state(int, default=0)
    EMPTY = state(bool, default=False)
    NO_GEOMETRY = state(bool, default=False)
    PAGINATED = state(bool, default=False)
    PAGE = state(str, default="1 / 1")
    PREVIOUS = state(str, default="disabled")
    NEXT = state(str, default="disabled")
    REQUEST = state(str, default="disabled")
    REQUEST_LABEL = state(str, default="EXCLUDE SELECTED")


class ExcludeCommand(CommandKey):
    SELECT = "exclude.select"


def object_action(name):
    return Command(ExcludeCommand.SELECT, name).wire_id


class ObjectFootprint(Frame):
    """Place a normal editable Frame at supplied normalized bed bounds."""

    property_schema = property_schema(*Frame.property_schema, PropertySpec(
        "rectangle", (list, tuple), default=(0, 0, 1, 1),
        editor=EditorSpec("collection_binding", label="Normalized bounds", group="Data"),
        bindings=("item", "derived"), invalidation=Invalidation.LAYOUT,
        source=SourceSpec(name="rectangle")))

    def __init__(self, child=None, border=ThemeColor.PRIMARY,
                 background=ThemeColor.BACKGROUND, line_width=2, key=None,
                 *, rectangle=(0, 0, 1, 1)):
        super().__init__(child, border, background, line_width, key)
        self.rectangle = rectangle
        self._rectangle = (0, 0, 1, 1)

    def update(self, state, initialize=False):
        rectangle = tuple(resolve(self.rectangle, state))
        if rectangle != self._rectangle:
            self._rectangle = rectangle
            self.invalidate(Dirty.LAYOUT)
        super().update(state, initialize)

    def arrange(self, bounds, result):
        x, y, width, height = self._rectangle
        super().arrange(Rect(bounds.x + round(x * bounds.width),
                             bounds.y + round(y * bounds.height),
                             max(1, round(width * bounds.width)),
                             max(1, round(height * bounds.height))), result)


SELECT = ItemCommand(ExcludeCommand.SELECT, payload=item("name"))
SAMPLE = dict(name="Sample_Model", kind="object", label="01  Sample Model",
              note="TAP TO SELECT", marker="1", selected=False, excluded=False,
              rectangle=[0.15, 0.2, 0.4, 0.35])

OBJECT_CARD = Template(
    "object",
    Frame(
        Overlay(
            Column(
                Text(item("label"), font="JetBrainsMono Bold 8pt",
                     color=derived(lambda selected, excluded: ThemeColor.DIM if excluded else
                                   ThemeColor.SECONDARY if selected else ThemeColor.PRIMARY,
                                   item("selected"), item("excluded")), truncate=True).height(20).ref("name"),
                Text(item("note"), font="JetBrainsMono 8pt", color=ThemeColor.DIM,
                     truncate=True).height(20).ref("status"),
                gap=8,
            ).height(48).align(vertical="center").margin(left=12, right=12).ref("caption"),
            Hitbox(SELECT).ref("select"),
        ),
        border=derived(lambda selected, excluded: ThemeColor.SECONDARY if selected else
                       ThemeColor.DIM if excluded else ThemeColor.PRIMARY,
                       item("selected"), item("excluded")),
        background=derived(lambda excluded: ThemeColor.PANEL if excluded else ThemeColor.BACKGROUND,
                           item("excluded")),
        line_width=2,
    ).height(60).ref("card"),
    sample=SAMPLE,
)

FOOTPRINT = Template(
    "object",
    ObjectFootprint(
        Overlay(
            Text(item("marker"), font="JetBrainsMono Bold 10pt", truncate=True, fit=True,
                 color=derived(lambda selected, excluded: ThemeColor.DIM if excluded else
                               ThemeColor.SECONDARY if selected else ThemeColor.PRIMARY,
                               item("selected"), item("excluded"))).ref("number"),
            Hitbox(SELECT).ref("select"),
        ),
        rectangle=item("rectangle"),
        border=derived(lambda selected, excluded: ThemeColor.SECONDARY if selected else
                       ThemeColor.DIM if excluded else ThemeColor.PRIMARY,
                       item("selected"), item("excluded")),
        background=derived(lambda selected, excluded: ThemeColor.PANEL if selected or excluded
                           else ThemeColor.BACKGROUND, item("selected"), item("excluded")),
        line_width=derived(lambda selected: 4 if selected else 2, item("selected")),
    ).ref("footprint"),
    sample=SAMPLE,
)

PAGE = PageTree(
    Column(
        Grid(matrix=((Button(PrintingAction(PrintingRoute.EXCLUDE_BACK), "BACK",
                            font="JetBrainsMono Bold 8pt"), Spacer()),),
             columns=(146, FLEX), rows=Equal(1), gap=12)
        .padding(left=14, top=6, right=14, bottom=2).height(56).ref("exclude.header"),
        Grid(
            matrix=((
                Frame(Overlay(
                    StateCase(
                        ListView(items=bind(ExcludeState.OBJECTS), item_key=item("name"),
                                 template_key=item("kind"), templates=(FOOTPRINT,), direction="overlay")
                        .ref("exclude.footprints"),
                        selector=bind(ExcludeState.NO_GEOMETRY), expected=False,
                    ),
                    StateCase(Text("Geometry unavailable", color=ThemeColor.DIM,
                                   font="JetBrainsMono 8pt", truncate=True),
                              selector=bind(ExcludeState.NO_GEOMETRY), expected=True),
                ), border=ThemeColor.BORDER, background=ThemeColor.BACKGROUND)
                .ref("exclude.bed"),
                Column(
                    Text(derived(lambda count: "OBJECTS  %d" % count, bind(ExcludeState.COUNT)),
                         font="JetBrainsMono Bold 8pt", horizontal="left")
                    .height(20).ref("exclude.count"),
                    Overlay(
                        StateCase(
                            ListView(items=bind(ExcludeState.ROWS), item_key=item("name"),
                                     template_key=item("kind"), templates=(OBJECT_CARD,), gap=8)
                            .ref("exclude.rows"),
                            selector=bind(ExcludeState.EMPTY), expected=False,
                        ),
                        StateCase(Text("No objects defined in this file", color=ThemeColor.DIM,
                                       truncate=True), selector=bind(ExcludeState.EMPTY), expected=True),
                    ),
                    StateCase(
                        Grid(matrix=((
                            Button(PrintingAction(PrintingRoute.EXCLUDE_PREV), "PREV",
                                   state=bind(ExcludeState.PREVIOUS), font="JetBrainsMono Bold 8pt"),
                            Text(bind(ExcludeState.PAGE), color=ThemeColor.DIM),
                            Button(PrintingAction(PrintingRoute.EXCLUDE_NEXT), "NEXT",
                                   state=bind(ExcludeState.NEXT), font="JetBrainsMono Bold 8pt"),
                        ),), columns=(90, FLEX, 90), rows=Equal(1), gap=8),
                        selector=bind(ExcludeState.PAGINATED), expected=True,
                    ).height(32).ref("exclude.pagination"),
                    Button(PrintingAction(PrintingRoute.EXCLUDE_REQUEST), bind(ExcludeState.REQUEST_LABEL),
                           state=bind(ExcludeState.REQUEST), font="JetBrainsMono Bold 9pt")
                    .height(56).ref("exclude.request"),
                    gap=12,
                ).ref("exclude.controls"),
            ),), columns=(350, FLEX), rows=Equal(1), gap=24,
        ).margin(left=20, right=20, top=18, bottom=18).ref("exclude.body"),
        gap=0,
    ).ref("exclude.root"), Rect(0, 0, 800, 442), page_id=AppPage.EXCLUDE_MODEL,
)
PAGE.title = "Exclude Model"
