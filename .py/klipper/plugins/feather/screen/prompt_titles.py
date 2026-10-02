## Klipper prompt titles recognized by Feather.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

COLD_PULL = "cold pull"
RESURRECTION = "resurrection"
PREVIOUS_TIMELAPSE = "previous timelapse"


def is_cold_pull(prompt):
    return prompt.get("title", "").strip().casefold() == COLD_PULL
