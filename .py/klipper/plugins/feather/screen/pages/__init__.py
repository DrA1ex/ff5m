## Feather page composition.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from feather.network.pages import FeatherNetworkPagesMixin

from .action_prompt import ActionPromptPagesMixin
from .files import FILE_ROWS, FileBrowserPagesMixin
from .home import HomePagesMixin
from .mod_editor import ModEditorPagesMixin
from .mod_settings import ModSettingsPagesMixin
from .print_cancel import PrintCancelPagesMixin
from .printing import PrintingPagesMixin
from .recovery import RecoveryPagesMixin
from .settings import SettingsPagesMixin


class FeatherPagesMixin(
        HomePagesMixin,
        FileBrowserPagesMixin,
        PrintingPagesMixin,
        PrintCancelPagesMixin,
        SettingsPagesMixin,
        ModSettingsPagesMixin,
        ModEditorPagesMixin,
        RecoveryPagesMixin,
        ActionPromptPagesMixin,
        FeatherNetworkPagesMixin):
    """Compose the concrete page groups used by the main Feather screen."""


__all__ = (
    "ActionPromptPagesMixin",
    "FILE_ROWS",
    "FeatherPagesMixin",
    "FileBrowserPagesMixin",
    "HomePagesMixin",
    "ModEditorPagesMixin",
    "ModSettingsPagesMixin",
    "PrintCancelPagesMixin",
    "PrintingPagesMixin",
    "RecoveryPagesMixin",
    "SettingsPagesMixin",
)
