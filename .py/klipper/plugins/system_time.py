## System timezone selection and change notifications.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import os
from pathlib import Path
import re
import struct
import tempfile
import time


ZONEINFO_ROOT = Path("/usr/share/zoneinfo")
LOCALTIME_PATH = Path("/etc/localtime")
# Midday on 2000-01-01 stays inside 32-bit time_t in every zone.
TZ_RELOAD_TIME = 946728000


def set_timezone(zone):
    """Select a named zone as a symlink into the database, or a fixed offset as a file of its own."""
    offset = re.fullmatch(r"UTC([+-])([0-9]{1,2})(?::([0-9]{2}))?", zone)
    if offset is None:
        _symlink_atomic(LOCALTIME_PATH, _named_zone(zone))
        return

    sign, hours, minutes = offset.group(1), int(offset.group(2)), int(offset.group(3) or 0)
    if hours > 23 or minutes > 59:
        raise ValueError("UTC offset must be between -23:59 and +23:59")

    if hours or minutes:
        _write_atomic(LOCALTIME_PATH, _fixed_offset_tzif(sign, hours, minutes))
    else:
        _symlink_atomic(LOCALTIME_PATH, _named_zone("UTC"))


def current_zone():
    try:
        target = LOCALTIME_PATH.parent / os.readlink(str(LOCALTIME_PATH))
        if ZONEINFO_ROOT in target.parents and ".." not in target.parts:
            return str(target.relative_to(ZONEINFO_ROOT))
        # Preserve the selected alias, even if the database directory is itself a link.
        root = ZONEINFO_ROOT.resolve(strict=True)
        path = target.parent.resolve(strict=True) / target.name
        if root not in path.parents:
            path = target.resolve(strict=True)
        return str(path.relative_to(root))
    except (OSError, ValueError, RuntimeError):
        pass

    # Some installations copy the TZif and keep its name in /etc/timezone.
    # A previous name can outlive a change to a fixed offset.
    try:
        name = LOCALTIME_PATH.with_name("timezone").read_text(encoding="utf-8").strip()
        if _named_zone(name).read_bytes() == LOCALTIME_PATH.read_bytes():
            return name
    except (OSError, ValueError, RuntimeError):
        pass

    offset = time.strftime("%z")
    return "UTC%s:%s" % (offset[:3], offset[3:])


def canonical_timezone(name):
    match = re.fullmatch(r"UTC([+-])([0-9]{1,2})(?::([0-9]{2}))?", name)
    if match is None:
        return name
    hours, minutes = int(match.group(2)), int(match.group(3) or 0)
    if not hours and not minutes:
        return "UTC"
    return "UTC%s%d%s" % (match.group(1), hours, ":%02d" % minutes if minutes else "")


def _location_names():
    names = set()
    try:
        with (ZONEINFO_ROOT / "zone.tab").open(encoding="utf-8") as stream:
            for line in stream:
                if line.strip() and not line.startswith("#"):
                    names.add(line.split()[2])
    except FileNotFoundError:
        # Directory names are enough for browsing; SET_TIMEZONE validates the selected TZif file.
        for path in ZONEINFO_ROOT.rglob("*"):
            relative = path.relative_to(ZONEINFO_ROOT)
            if relative.parts[0] not in ("right", "posix", "Etc") and len(relative.parts) > 1 and path.is_file():
                names.add(str(relative))
    current = current_zone()
    if "/" in current:
        names.add(current)
    return sorted(names)


def timezone_regions():
    """The continents the database knows, as the picker's first level."""
    return sorted({name.split("/", 1)[0] for name in _location_names() if "/" in name})


def timezone_cities(region, prefix=None):
    """The cities of one region, optionally limited to a letter range.

    Browsing never opens a TZif file: only the zones actually shown are asked
    for their offset, and libc answers that question when they are.
    """
    cities = []
    for name in _location_names():
        if not name.startswith(region + "/"):
            continue
        label = name.rsplit("/", 1)[1].replace("_", " ")
        if prefix is None or prefix[0] <= label[:1].upper() <= prefix[-1]:
            cities.append((name, label))
    return sorted(cities, key=lambda entry: entry[1].upper())


def timezone_letter_ranges(region, bucket, maximum=None):
    """Split a long region into letter ranges, each small enough to browse.

    Both limits are the picker's: a range should fill at most one page of
    cities, and the ranges themselves should stay on one page of their own.
    """
    counts = {}
    for _name, label in timezone_cities(region):
        counts[label[:1].upper()] = counts.get(label[:1].upper(), 0) + 1
    while True:
        ranges, first, total = [], None, 0
        for letter in sorted(counts):
            if first is not None and total + counts[letter] > bucket:
                ranges.append((first, last))
                first, total = None, 0
            if first is None:
                first = letter
            last, total = letter, total + counts[letter]
        if first is not None:
            ranges.append((first, last))
        if maximum is None or len(ranges) <= maximum:
            return ranges
        bucket += max(1, bucket // 4)


def fixed_offsets():
    """Offer offsets from IANA tzdb's etcetera and zonenow.tab, including seasonal offsets."""
    fractional = ((-9, 30), (-3, 30), (-2, 30), (3, 30), (4, 30), (5, 30), (5, 45),
                  (6, 30), (8, 45), (9, 30), (10, 30), (12, 45), (13, 45))
    offsets = list(range(-12 * 60, 14 * 60 + 1, 60))
    offsets.extend(hours * 60 + (-minutes if hours < 0 else minutes) for hours, minutes in fractional)
    return ["UTC" if offset == 0 else "UTC%s%02d:%02d" % (
        "-" if offset < 0 else "+", abs(offset) // 60, abs(offset) % 60) for offset in sorted(offsets)]


def zone_offsets(names):
    """Read the current offsets for the visible cities, including DST transitions."""
    offsets = {}
    try:
        for name in names:
            try:
                path = _named_zone(name)
            except (OSError, ValueError):
                offsets[name] = None
                continue
            os.environ["TZ"] = ":" + str(path)
            time.mktime(time.gmtime(TZ_RELOAD_TIME))
            offset = time.strftime("%z")
            offsets[name] = "UTC%s:%s" % (offset[:3], offset[3:]) if len(offset) == 5 else None
    finally:
        if names:
            reload_timezone()
    return offsets


def reload_timezone():
    # The printer's Python is built without time.tzset, but POSIX requires
    # mktime() to act as if tzset() were called. libc rereads zone data only
    # when TZ changes, so pass through UTC before pointing TZ back at the
    # selection. Explicit TZ also overrides one that Klipper may have inherited.
    os.environ["TZ"] = "UTC0"
    time.mktime(time.gmtime(TZ_RELOAD_TIME))
    os.environ["TZ"] = ":" + str(LOCALTIME_PATH)
    time.mktime(time.gmtime(TZ_RELOAD_TIME))


def _named_zone(name):
    """Return the database path for a name; the link keeps that name, so it is what SET_TIMEZONE reports."""
    if not re.fullmatch(r"[A-Za-z0-9_+./-]+", name) or ".." in Path(name).parts:
        raise ValueError("Invalid timezone name")

    root = ZONEINFO_ROOT.resolve(strict=True)
    path = ZONEINFO_ROOT / name
    try:
        data = path.resolve(strict=True)
    except FileNotFoundError:
        raise ValueError("Unknown timezone: %s" % name) from None
    if root not in data.parents:
        raise ValueError("Timezone resolves outside the timezone database")
    # right/ zones count leap seconds, which would put the clock about 27 s off.
    if data.relative_to(root).parts[0] == "right":
        raise ValueError("Leap-second zones under right/ are not supported")
    if not data.is_file():
        raise ValueError("Unknown timezone: %s" % name)

    with data.open("rb") as stream:
        if stream.read(4) != b"TZif":
            raise ValueError("Not a timezone data file: %s" % name)
    return path


def _fixed_offset_tzif(sign, hours, minutes):
    seconds = (hours * 60 + minutes) * 60 * (1 if sign == "+" else -1)
    abbreviation = ("%s%02d%02d" % (sign, hours, minutes)).encode("ascii") + b"\0"
    # TZif version 1 with one standard-time type and no transitions is a fixed offset.
    return (b"TZif" + bytes(16) + struct.pack(">6I", 0, 0, 0, 0, 1, len(abbreviation))
            + struct.pack(">iBB", seconds, 0, 0) + abbreviation)


def _write_atomic(path, data):
    fd, temporary = tempfile.mkstemp(prefix=".timezone-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            os.fchmod(stream.fileno(), 0o644)
        os.replace(temporary, str(path))
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def _symlink_atomic(path, target):
    temporary = path.with_name(".timezone-link")
    if os.path.lexists(str(temporary)):
        os.unlink(str(temporary))
    os.symlink(str(target), str(temporary))
    try:
        os.replace(str(temporary), str(path))
    except OSError:
        os.unlink(str(temporary))
        raise


HELP = """SET_TIMEZONE ZONE=&lt;Area/City or UTC offset&gt;

Omit ZONE to display the current time zone and this help message without changing anything.

Named time zones automatically follow daylight saving time rules:
  SET_TIMEZONE ZONE=Europe/Berlin
  SET_TIMEZONE ZONE=Europe/Moscow
  SET_TIMEZONE ZONE=America/Los_Angeles
  SET_TIMEZONE ZONE=Asia/Tokyo

Fixed UTC offsets do not adjust for daylight saving time (+ is ahead of UTC, - is behind):
  SET_TIMEZONE ZONE=UTC
  SET_TIMEZONE ZONE=UTC+3
  SET_TIMEZONE ZONE=UTC-8
  SET_TIMEZONE ZONE=UTC+5:30
  SET_TIMEZONE ZONE=UTC-3:30

Offsets can be specified in whole hours or H:MM / HH:MM format, ranging from UTC-23:59 to UTC+23:59."""


class SystemTime:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object("gcode")
        self.gcode.register_command("SET_TIMEZONE", self.cmd_SET_TIMEZONE,
                                    desc="Show or change the system timezone; omit ZONE for help")
        self.printer.register_event_handler("klippy:ready", reload_timezone)

    def cmd_SET_TIMEZONE(self, gcmd):
        zone = gcmd.get("ZONE", None)
        if zone is not None:
            try:
                set_timezone(zone)
            except (OSError, ValueError) as error:
                raise gcmd.error("Unable to set timezone: %s" % error)
            reload_timezone()
            self.printer.send_event("system_time:timezone_changed")

        gcmd.respond_info("Timezone: %s\nLocal time: %s" % (
            current_zone(), time.strftime("%Y-%m-%d %H:%M:%S %Z (UTC%z)")))
        if zone is None:
            gcmd.respond_info(HELP)


def load_config(config):
    return SystemTime(config)
