## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
## This file may be distributed under the terms of the GNU GPLv3 license

"""Product component identities remain stable when pages are reconstructed."""

import pathlib
import sys
import unittest

PLUGINS = pathlib.Path(__file__).parents[1] / ".py" / "klipper" / "plugins"
sys.path.insert(0, str(PLUGINS))

from ff5m_ui.home.page import create_page as home_page
from ff5m_ui.heat.page import create_page as heat_page
from ff5m_ui.printing.page import create_page as printing_page
from ui.reflection import reflect_page


def walk(node):
    yield node
    for child in node.get("children", ()):
        yield from walk(child)


class ComponentIdentityTest(unittest.TestCase):
    def test_rebuilding_product_pages_preserves_component_instance_scopes(self):
        for factory in (home_page, heat_page, printing_page):
            with self.subTest(page=factory.__module__):
                snapshots = []
                for _ in range(2):
                    tree = reflect_page(factory())["tree"]
                    instances = [node["component_template_instance"] for node in walk(tree)
                                 if (node.get("component_template_instance") or {}).get("root")]
                    self.assertTrue(instances)
                    scopes = {}
                    for instance in instances:
                        self.assertIsNotNone(instance["instance_key"])
                        identity = (instance["template"], instance["instance_key"])
                        self.assertNotIn(identity, scopes)
                        scopes[identity] = instance["instance_scope"]
                    snapshots.append(scopes)
                self.assertEqual(snapshots[0], snapshots[1])


if __name__ == "__main__":
    unittest.main()
