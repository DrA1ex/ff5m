## Lazy entry point for the Feather on-printer regression runner.
##
## This module is deliberately small so normal Feather startup does not import
## the runner package. It is loaded only by the hidden _FEATHER_UI_TEST command.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from feather_ui_test.feature import UITestFeature


__all__ = ("UITestFeature",)
