# Small parser/encoder for the netd line protocol.
#
# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
#
# This file may be distributed under the terms of the GNU GPLv3 license

"""Encode commands and parse values exchanged with netd.

The protocol is deliberately small and explicit. Unknown snapshot fields and
unknown line shapes are ignored so a newer daemon can add information without
breaking an older Feather build.
"""

import base64


SNAPSHOT_KEYS = (
    "mode", "state", "ssid", "signal", "ip", "reason", "progress",
    "attempt")

OFFLINE_STATUS = {
    "mode": "OFFLINE", "state": "DISCONNECTED", "ssid": "",
    "signal": "", "ip": "", "reason": "", "progress": "",
    "attempt": ""}


def blank_status():
    return dict(OFFLINE_STATUS)


def reset_status(status):
    """Reset the one caller-owned snapshot in place."""
    status.clear()
    status.update(OFFLINE_STATUS)


def encode_field(value):
    return base64.b64encode(str(value).encode("utf-8")).decode("ascii")


def decode_field(value):
    try:
        return base64.b64decode(value.encode("ascii"), validate=True).decode(
            "utf-8", "replace")
    except Exception:
        return ""


def connect_wifi_command(ssid, password=None):
    command = "CONNECT_WIFI ssid=" + encode_field(ssid)
    if password is not None:
        command += " psk=" + encode_field(password)
    return command


def parse_status_field(line):
    """Return ``(key, value)`` for a snapshot line, otherwise ``None``."""
    separator = line.find("=")
    if separator < 0:
        return None
    key = line[:separator].strip().lower()
    if key not in SNAPSHOT_KEYS:
        return None
    value = line[separator + 1:].strip()
    if key == "ssid":
        value = decode_field(value)
    return key, value


def parse_status(text):
    status = blank_status()
    for line in str(text).splitlines():
        field = parse_status_field(line.strip())
        if field is not None:
            status[field[0]] = field[1]
    return status


def parse_scan_row(line):
    """Parse one scan row, or return ``None`` when the line is not one."""
    if not line.startswith("FREQUENCY="):
        return None
    marker = line.find(" NETWORK=")
    if marker < 0:
        return None

    fields = {}
    for token in line[:marker].split(" "):
        key, separator, value = token.partition("=")
        if separator:
            fields[key] = value

    ssid = decode_field(line[marker + 9:])
    if not ssid:
        return None

    try:
        frequency = int(fields.get("FREQUENCY", ""))
        signal = int(fields.get("SIGNAL", ""))
    except ValueError:
        return None

    return {
        "ssid": ssid,
        "frequency": frequency,
        "signal": signal,
        "security": fields.get("SECURITY", ""),
        "saved": fields.get("SAVED") == "1",
    }


def parse_message(line):
    """Parse one complete daemon line into ``(kind, value)``.

    Kinds are ``ok``, ``error``, ``scan`` and ``status``. Unknown lines return
    ``None``. The status payload is the ``(key, value)`` pair returned by
    :func:`parse_status_field`.
    """
    line = str(line).strip()
    if not line:
        return None
    if line == "OK" or line.startswith("OK "):
        return "ok", line[2:].strip()
    if line.startswith("ERR "):
        return "error", line[4:].strip()

    row = parse_scan_row(line)
    if row is not None:
        return "scan", row

    field = parse_status_field(line)
    if field is not None:
        return "status", field
    return None
