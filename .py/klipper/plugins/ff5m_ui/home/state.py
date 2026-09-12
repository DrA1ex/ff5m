## Typed state collection for the Feather home dashboard.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import os
import time
from collections import namedtuple

from ui.bindings import state
from ui.identity import StateKey

from ..print_state import PrintState


class HomeState(StateKey):
    __key_namespace__ = "ui.pages.home.state.HomeState"

    NOZZLE = state(int, default=25, unit="C", category="temperature")
    NOZZLE_TARGET = state(
        int, default=0, minimum=0, unit="C", category="temperature")
    BED = state(int, default=24, unit="C", category="temperature")
    BED_TARGET = state(
        int, default=0, minimum=0, unit="C", category="temperature")
    NETWORK_NAME = state(
        str, default="WIFI / FORGE-X", category="network")
    NETWORK_ADDRESS = state(
        str, default="192.168.2.124", category="network")
    LAST_JOB = state(str, default="BENCHY.GCODE", category="job")
    MATERIAL = state(str, default="PLA", category="filament")
    HOMED_AXES = state(str, default="XYZ", category="motion")
    JOB_ACTIVE = state(bool, default=False, category="job")
    JOB_STATE = state(str, default="READY", category="job")
    JOB_FILENAME = state(str, default="NO ACTIVE JOB", category="job")
    JOB_PROGRESS = state(
        int, default=0, minimum=0, maximum=100, category="job")
    JOB_ELAPSED = state(str, default="--:--:--", category="job")
    JOB_REMAINING = state(str, default="--:--:--", category="job")
    JOB_DETAIL = state(str, default="", category="job")
    CLOCK = state(str, default="12:34", category="system")


DashboardJob = namedtuple(
    "DashboardJob",
    ("active", "state", "filename", "progress", "elapsed", "remaining",
     "detail"))

DashboardState = namedtuple(
    "DashboardState",
    ("nozzle", "nozzle_target", "bed", "bed_target", "network_name",
     "network_address", "last_job", "material", "homed_axes", "job",
     "clock"))


def dashboard_values(snapshot):
    """Translate one controller snapshot into typed declarative page state."""
    job = snapshot.job
    return {
        HomeState.NOZZLE: int(snapshot.nozzle),
        HomeState.NOZZLE_TARGET: int(snapshot.nozzle_target),
        HomeState.BED: int(snapshot.bed),
        HomeState.BED_TARGET: int(snapshot.bed_target),
        HomeState.NETWORK_NAME: str(snapshot.network_name),
        HomeState.NETWORK_ADDRESS: str(snapshot.network_address),
        HomeState.LAST_JOB: str(snapshot.last_job),
        HomeState.MATERIAL: str(snapshot.material),
        HomeState.HOMED_AXES: str(snapshot.homed_axes),
        HomeState.JOB_ACTIVE: bool(job.active),
        HomeState.JOB_STATE: str(job.state),
        HomeState.JOB_FILENAME: str(job.filename),
        HomeState.JOB_PROGRESS: int(job.progress),
        HomeState.JOB_ELAPSED: str(job.elapsed),
        HomeState.JOB_REMAINING: str(job.remaining),
        HomeState.JOB_DETAIL: str(job.detail),
        HomeState.CLOCK: str(snapshot.clock),
    }


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
    connection_state = (host.network_status.get("state") or
                        ("CONNECTED" if host.network_status.get("ip") else
                         "DISCONNECTED")).upper()
    ssid = host.network_status.get("ssid") or ""
    address = host.network_status.get("ip") or "NO LINK"
    if connection_state == "CONNECTED":
        if mode.upper() == "WIFI" and ssid:
            network_name = "Wi-Fi / %s" % ssid
        elif mode.upper() == "ETHERNET":
            network_name = "ETHERNET"
        else:
            network_name = mode.title()
    else:
        network_name = connection_state.title()
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
    state_name = str(stats.get("state", "")).lower()
    active = (state_name in ("printing", "paused")
              or bool(virtual_sdcard is not None
                      and virtual_sdcard.is_active()))
    if not active:
        return DashboardJob(
            False, "READY", host.last_job_name, 0,
            "--:--:--", "--:--:--", "")

    if getattr(host, "print_state", None) == PrintState.PREPARING:
        label = "PREPARING"
    else:
        label = "PAUSED" if state_name == "paused" else "PRINTING"
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
    operation = host._operation_context_status(eventtime)
    detail = (host._operation_context_text(status=operation)
              if "print" in operation.get("context_types", ()) else label)
    detail = detail or label
    return DashboardJob(
        True, label, filename, int(progress_value * 100),
        host._clock_duration(elapsed), host._clock_duration(remaining), detail)
