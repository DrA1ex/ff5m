## Klipper object identifier serialization shared by print controls and recovery.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import shlex


def object_name_parameter(name):
    if not isinstance(name, str) or not name:
        raise RuntimeError("Invalid object name")
    # Klipper strips these delimiters before shlex handles quoted parameters.
    if any(character in "#*;" or ord(character) < 32 or ord(character) == 127 for character in name):
        raise RuntimeError("This object name contains characters unsupported by Klipper")
    return "NAME=" + shlex.quote(name)


def exclude_command(name):
    return "EXCLUDE_OBJECT " + object_name_parameter(name)
