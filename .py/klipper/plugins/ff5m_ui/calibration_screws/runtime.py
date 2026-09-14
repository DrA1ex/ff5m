## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from copy import deepcopy

from ui import PageTree

from . import page
from .actions import REPEAT, DONE, ScrewResultAction
from .page import PAGE, PAGE_BOUNDS, ScrewResultRef
from .state import ScrewResultState, result_values


def create_page(bounds=PAGE_BOUNDS):
    """Create an isolated tree, adapting the declared content for narrow screens."""
    root = deepcopy(PAGE.root)
    if bounds.width < 770:
        nodes = {node.key: node for node in root.walk()}
        legend = nodes[ScrewResultRef.LEGEND]
        legend.replace_preview_children((legend.items[0], deepcopy(page.compact_legend)))
        for ref in (ScrewResultRef.REAR_LEFT_VALUE, ScrewResultRef.REAR_RIGHT_VALUE,
                    ScrewResultRef.FRONT_LEFT_VALUE, ScrewResultRef.FRONT_RIGHT_VALUE):
            nodes[ref].font = "Roboto Bold 8pt"
    result = PageTree(root, bounds, page_id=PAGE.page_key)
    result.title = PAGE.title
    result.show_back = PAGE.show_back
    return result


def render(renderer, results, reference_name=None):
    return PAGE.draw(renderer, result_values(results, reference_name))
