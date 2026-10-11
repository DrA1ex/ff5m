## Local screenshots of the production Feather print and exclusion UI.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Replay accepted Feather drawing commands in the local Designer renderer."""

import argparse
import json
import pathlib

from tests.feather_exclude_test_helper import make_screen, tap
from feather.screen.pages.exclude_objects import object_action
from .composition_scenes import drawing_operations
from .hybrid import DesignerCapture


ROOT = pathlib.Path(__file__).parents[2]


def build_cases():
    screen = make_screen()
    cases, history = [], []

    def capture(identity, label):
        for batch in screen.capture.batches:
            history.extend(drawing_operations(batch))
        screen.capture.batches.clear()
        cases.append(dict(
            id=identity, label=label,
            semantic_page_id="ui.pages.keys.AppPage.PRINTING",
            state={}, actions=[], width=800, height=480, theme="DEFAULT",
            composition_fixture=dict(title=label, operations=list(history))))

    capture("printing-more-closed", "Printing / MORE closed")
    tap(screen, "print.more")
    capture("print-more-open", "Printing / MORE open")
    tap(screen, "more.exclude")
    tap(screen, object_action("Gear_Cover"))
    capture("exclude-model-selected", "Exclude Model / known fixture geometry")
    tap(screen, object_action("Bracket_Main"))
    capture("exclude-model-multiple", "Exclude Model / two selected models")
    tap(screen, "exclude.request")
    capture("exclude-model-multiple-confirm", "Exclude Model / confirm two models")
    tap(screen, "message.ok")
    tap(screen, object_action("Bracket_Main"))
    screen.objects.status["objects"].append(screen.objects.status["tiny_object"])
    screen._render_screen()
    capture("exclude-model-small", "Exclude Model / tiny footprint label")
    screen.objects.status["objects"] = screen.objects.status["objects"][:1]
    screen.exclude_list_page = 0
    screen._render_screen()
    capture("exclude-model-single", "Exclude Model / one compact object row")
    screen = make_screen()
    history.clear()
    screen.objects.status["objects"] = screen.objects.status["objects"][:2]
    names = ("Крышка шестерни", "Левая стойка")
    for model, name in zip(screen.objects.status["objects"], names):
        model["name"] = name
    tap(screen, "print.more")
    tap(screen, "more.exclude")
    for name in names:
        tap(screen, object_action(name))
    capture("exclude-model-unicode", "Exclude Model / Unicode names and multiple selection")
    tap(screen, "exclude.request")
    capture("exclude-model-unicode-confirm", "Exclude Model / confirm Unicode names")
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--designer-root", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("Choose an empty output directory")
    cases = build_cases()
    records = DesignerCapture(args.designer_root, ROOT).capture(cases, args.output, workers=1)
    (args.output / "fixture-note.json").write_text(json.dumps({
        "source": ".py/klipper/plugins/feather_ui_test/fixtures/exclude_models.json",
        "geometry": "Supplied polygons only; screenshot objects are a local UX fixture",
        "screenshots": records,
    }, indent=2, default=str) + "\n")
    for record in records:
        print(args.output / record["file"])


if __name__ == "__main__":
    main()
