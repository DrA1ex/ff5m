## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Optional local Designer integration; source edits happen only in a temp copy."""

import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch


DESIGNER_ROOT = os.environ.get("FEATHER_DESIGNER_ROOT")
PLUGINS = Path(__file__).resolve().parents[1] / ".py/klipper/plugins"


def walk(node):
    yield node
    for child in node.get("children", ()):
        yield from walk(child)


@unittest.skipUnless(DESIGNER_ROOT, "set FEATHER_DESIGNER_ROOT for local Designer integration")
class ScrewDesignerAuthoringTest(unittest.TestCase):
    def test_instruction_offset_can_be_added_removed_and_reopened(self):
        with patch.object(sys, "path", [DESIGNER_ROOT, *sys.path]):
            from feather_preview.host_client import ProjectHostClient

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            shutil.copytree(PLUGINS, root,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            client = ProjectHostClient(root, timeout=60, data_root=Path(directory) / "data")
            try:
                groups = client.call("host.screens")["groups"]
                screen = next(item["id"] for group in groups for item in group["screens"]
                              if item["id"].endswith(".CALIBRATION_SCREWS_RESULT"))
                for offset in ([3, 2], [0, 0], [2, 3]):
                    scene = client.call("page.render", {"screen": screen})
                    node = next(node for node in walk(scene["tree"])
                                if node.get("type") == "Text" and
                                "ADJUST FROM BELOW" in str(node.get("properties", {})))
                    self.assertEqual(node["layout_sources"]["offset"]["status"], "editable")
                    client.call("editor.update", {
                        "screen": screen, "target": node["editor"]["target"],
                        "patch": {"layout": {"offset": offset}},
                    })
                    scene = client.call("editor.apply", {"screen": screen, "scope": "page"})
                    node = next(node for node in walk(scene["tree"])
                                if node.get("type") == "Text" and
                                "ADJUST FROM BELOW" in str(node.get("properties", {})))
                    self.assertEqual(node["layout"]["offset"], offset)
                node = next(node for node in walk(scene["tree"])
                            if node.get("properties", {}).get("value") == "CW = CLOCKWISE")
                client.call("editor.update", {
                    "screen": screen, "target": node["editor"]["target"],
                    "patch": {"properties": {"value": "CW = RIGHT"}},
                })
                scene = client.call("editor.apply", {"screen": screen, "scope": "page"})
                labels = [node.get("properties", {}).get("value") for node in walk(scene["tree"])]
                self.assertIn("CW = RIGHT", labels)
                self.assertNotIn("CW = CLOCKWISE", labels)
                self.assertIn("CCW = COUNTERCLOCKWISE", labels)
            finally:
                client.close()
