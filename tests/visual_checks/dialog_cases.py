## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Dialog fixtures shared by layout assertions and native screenshot capture."""

import json
import pathlib

ROOT = pathlib.Path(__file__).parents[2]
CASES = json.loads((ROOT / "tests/fixtures/dialog_layout.json").read_text())


def render_case(renderer, case):
    labels = case.get("labels") or tuple(
        "Material profile %02d: PLA/PETG/ABS" % i
        for i in range(case["profiles"]))
    choices = tuple(("choice.%d" % i, label, "enabled")
                    for i, label in enumerate(labels))
    footer = tuple(("footer.%d" % i, label, "enabled")
                   for i, label in enumerate(case.get("footer", ())))
    is_footer = case.get("kind") == "footer"
    return renderer.dialog(
        "MMU Preset Layouts",
        tuple(case.get("lines", ("Choose your desired lane layout for this session:",))),
        choices if is_footer else footer,
        button_groups=() if is_footer else (choices,), width=700,
        height=case.get("height", 220), tone="info",
        page=case.get("page", 0), page_actions=("prev", "next"))
