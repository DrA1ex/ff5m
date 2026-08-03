## Typed state collection for the Feather home dashboard.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import os
import time
from collections import namedtuple

from ui import PrintState


DashboardJob = namedtuple(
    "DashboardJob",
    ("active", "state", "filename", "progress", "elapsed", "remaining",
     "detail"))

DashboardState = namedtuple(
    "DashboardState",
    ("nozzle", "nozzle_target", "bed", "bed_target", "network_name",
     "network_address", "last_job", "material", "homed_axes", "job",
     "clock"))


def collect_dashboard(host, eventtime, clock=None):
    """Collect one immutable dashboard snapshot from the controller."""
    host._refresh_local_timezone()
    material = host._current_material()
    host.filament_material = material
    extruder = host.extruder.get_status(eventtime)
    bed = host.heater_bed.get_status(eventtime)
    toolhead = host.toolhead.get_status(eventtime)
    homed = str(toolhead.get("homed_axes", "")).upper()
    mode = host.network_status.get("mode") or "OFFLINE"
    ssid = host.network_status.get("ssid") or ""
    address = (host.network_status.get("ip")
               or host._read_text("/tmp/net_ip") or "NO LINK")
    network_name = "%s%s" % (
        mode.upper(), " / " + ssid if ssid else "")
    clock_value = clock() if clock is not None else time.strftime("%H:%M")
    return DashboardState(
        nozzle=round(extruder["temperature"]),
        nozzle_target=round(extruder["target"]),
        bed=round(bed["temperature"]),
        bed_target=round(bed["target"]),
        network_name=network_name,
        network_address=address,
        last_job=host.last_job_name,
        material=material,
        homed_axes=homed or "NOT HOMED",
        job=host._dashboard_job(eventtime),
        clock=clock_value,
    )


def dashboard_job(host, eventtime):
    """Build the print-status portion of a dashboard snapshot."""
    stats_object = getattr(host, "print_stats", None)
    virtual_sdcard = getattr(host, "virtual_sdcard", None)
    stats = (stats_object.get_status(eventtime)
             if stats_object is not None else {})
    state = str(stats.get("state", "")).lower()
    active = (state in ("printing", "paused")
              or bool(virtual_sdcard is not None
                      and virtual_sdcard.is_active()))
    if not active:
        return DashboardJob(
            False, "READY", host.last_job_name, 0,
            "--:--:--", "--:--:--", "")

    if getattr(host, "print_state", None) == PrintState.PREPARING:
        label = "PREPARING"
    else:
        label = "PAUSED" if state == "paused" else "PRINTING"
    path = (virtual_sdcard.file_path()
            if virtual_sdcard is not None
            and hasattr(virtual_sdcard, "file_path") else "")
    filename = os.path.basename(path or host.last_job_name or "UNKNOWN")
    try:
        progress_value = host._print_progress(eventtime, stats)
        elapsed, remaining = host._print_time_values(
            eventtime, stats, progress_value)
    except (AttributeError, TypeError, ValueError):
        progress_value = 0.0
        elapsed = stats.get("print_duration")
        remaining = None
    detail = getattr(host, "print_status_text", "") or label
    return DashboardJob(
        True, label, filename, int(progress_value * 100),
        host._clock_duration(elapsed), host._clock_duration(remaining), detail)
