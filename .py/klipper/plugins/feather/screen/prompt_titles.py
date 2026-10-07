## Klipper prompt titles and Feather prompt kinds recognized by Feather.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

RESURRECTION = "resurrection"
PREVIOUS_TIMELAPSE = "previous timelapse"

# Our macros mark their prompts with ``action:prompt_feather_kind`` because a
# user macro may reuse the same title.
COLD_PULL = "cold_pull"
HEATING_NOZZLE = "heating_nozzle"
FILAMENT_CHANGE = "filament_change"


def is_cold_pull(prompt):
    return prompt.get("kind") == COLD_PULL


def has_temperature_status(prompt):
    return prompt.get("kind") in (COLD_PULL, HEATING_NOZZLE)
