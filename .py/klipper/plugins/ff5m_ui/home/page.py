## Imperative rendering for the Feather home dashboard.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from ui import Page, ThemeColor, ThemeRole

from .state import collect_dashboard


CLOCK_LEFT = 18
CLOCK_TOP = 7
CLOCK_WIDTH = 142
CLOCK_HEIGHT = 46
CLOCK_TEXT_X = 28
CLOCK_TEXT_Y = 29
CLOCK_TEXT_MAX_WIDTH = CLOCK_LEFT + CLOCK_WIDTH - CLOCK_TEXT_X


def render(host):
    commands = host.renderer.begin_page("FORGE-X // FEATHER")
    commands += host.renderer.button(
        "nav.menu", 648, 9, 132, 38, "MENU",
        font="JetBrainsMono Bold 8pt")
    panels = (
        (25, 72, 235, 132, "NOZZLE", ThemeRole.TEMPERATURE_NOZZLE),
        (282, 72, 235, 132, "BED", ThemeRole.TEMPERATURE_BED),
        (539, 72, 236, 132, "NETWORK", ThemeColor.PRIMARY),
    )
    for x, y, width, height, label, color in panels:
        commands += [
            host.renderer.fill(x, y, width, height, ThemeColor.PANEL),
            host.renderer.stroke(x, y, width, height, color, 2),
            host.renderer.text(
                x + width // 2, y + 28, label, color,
                "JetBrainsMono 8pt", "center", "middle"),
        ]
    commands += [
        host.renderer.fill(25, 220, 750, 112, ThemeColor.PANEL),
        host.renderer.stroke(25, 220, 750, 112, ThemeColor.BORDER, 2),
        host.renderer.text(
            44, 240, "JOB STATUS", ThemeColor.PRIMARY,
            "JetBrainsMono 8pt", "left", "middle"),
        host.renderer.fill(25, 345, 750, 1, ThemeColor.BORDER),
        host.renderer.text(
            28, 365, "LAST JOB", ThemeColor.DIM,
            "JetBrainsMono 8pt", "left", "middle"),
        host.renderer.text(
            300, 365, "MATERIAL", ThemeColor.DIM,
            "JetBrainsMono 8pt", "left", "middle"),
        host.renderer.text(
            570, 365, "TOOLHEAD", ThemeColor.DIM,
            "JetBrainsMono 8pt", "left", "middle"),
        host.renderer.fill(282, 353, 1, 74, ThemeColor.BORDER),
        host.renderer.fill(542, 353, 1, 74, ThemeColor.BORDER),
        host.renderer.action_hitbox("nav.heat", 25, 72, 492, 132),
        host.renderer.action_hitbox("nav.network", 539, 72, 236, 132),
        host.renderer.action_hitbox("nav.job", 25, 220, 750, 112),
        host.renderer.action_hitbox("nav.filament", 283, 345, 259, 97),
        host.renderer.action_hitbox("nav.move", 543, 345, 232, 97),
    ]
    host.renderer.send(commands)
    host._last_dashboard = None
    host._update_dashboard(host.reactor.monotonic())


def update(host, eventtime):
    if host.page != Page.IDLE_HOME:
        return
    current = collect_dashboard(host, eventtime)
    if current == host._last_dashboard:
        return
    previous = host._last_dashboard
    host._last_dashboard = current
    commands = []

    if (previous is None
            or (current.nozzle, current.nozzle_target)
            != (previous.nozzle, previous.nozzle_target)):
        commands += [
            host.renderer.fill(28, 112, 229, 87, ThemeColor.PANEL),
            host.renderer.text(
                142, 139, "%d / %d C" % (
                    current.nozzle, current.nozzle_target), ThemeColor.TEXT,
                "JetBrainsMono 12pt", "center", "middle"),
            host.renderer.text(
                142, 181,
                "HEATING" if current.nozzle_target > 0 else "OFF",
                (ThemeRole.TEMPERATURE_NOZZLE
                 if current.nozzle_target > 0 else ThemeColor.DIM),
                "JetBrainsMono 8pt", "center", "middle"),
        ]

    if (previous is None
            or (current.bed, current.bed_target)
            != (previous.bed, previous.bed_target)):
        commands += [
            host.renderer.fill(285, 112, 229, 87, ThemeColor.PANEL),
            host.renderer.text(
                399, 139, "%d / %d C" % (
                    current.bed, current.bed_target), ThemeColor.TEXT,
                "JetBrainsMono 12pt", "center", "middle"),
            host.renderer.text(
                399, 181,
                "HEATING" if current.bed_target > 0 else "OFF",
                (ThemeRole.TEMPERATURE_BED
                 if current.bed_target > 0 else ThemeColor.DIM),
                "JetBrainsMono 8pt", "center", "middle"),
        ]

    if (previous is None
            or (current.network_name, current.network_address)
            != (previous.network_name, previous.network_address)):
        commands += [
            host.renderer.fill(542, 112, 230, 87, ThemeColor.PANEL),
            host.renderer.text(
                657, 139, current.network_name, ThemeColor.TEXT,
                "JetBrainsMono 8pt", "center", "middle", max_width=210,
                truncate=True),
            host.renderer.text(
                657, 181, current.network_address, ThemeColor.PRIMARY,
                "JetBrainsMono 8pt", "center", "middle", max_width=210,
                truncate=True),
        ]

    if previous is None or current.job != previous.job:
        job = current.job
        commands += [
            host.renderer.fill(29, 252, 742, 76, ThemeColor.PANEL),
            host.renderer.text(
                44, 270, job.filename if job.active else "NO ACTIVE JOB",
                ThemeColor.TEXT if job.active else ThemeColor.PRIMARY,
                "JetBrainsMono Bold 8pt", "left", "middle",
                max_width=560, truncate=True),
            host.renderer.text(
                756, 270, job.state if job.active else "READY",
                (ThemeColor.WARNING
                 if job.state == "PAUSED" else ThemeColor.PRIMARY),
                "JetBrainsMono 8pt", "right", "middle"),
        ]
        if job.active:
            commands += [
                host.renderer.text(
                    44, 307, job.detail, ThemeColor.DIM,
                    "JetBrainsMono 8pt", "left", "middle",
                    max_width=330, truncate=True),
                host.renderer.text(
                    756, 307, "%d%% // %s / %s" % (
                        job.progress, job.elapsed, job.remaining),
                    ThemeColor.TEXT, "JetBrainsMono 8pt", "right", "middle",
                    max_width=350, truncate=True),
            ]

    if (previous is None
            or (current.last_job, current.material, current.homed_axes)
            != (previous.last_job, previous.material, previous.homed_axes)):
        commands += [
            host.renderer.fill(25, 393, 750, 34, ThemeColor.BACKGROUND),
            host.renderer.text(
                28, 410, current.last_job, ThemeColor.TEXT,
                "JetBrainsMono 8pt", "left", "middle", max_width=240,
                truncate=True),
            host.renderer.text(
                300, 410, current.material, ThemeColor.TEXT,
                "JetBrainsMono 8pt", "left", "middle", max_width=220,
                truncate=True),
            host.renderer.text(
                570, 410, current.homed_axes,
                (ThemeColor.PRIMARY
                 if current.homed_axes == "XYZ" else ThemeColor.WARNING),
                "JetBrainsMono 8pt", "left", "middle"),
        ]

    if previous is None or current.clock != previous.clock:
        commands += clock_commands(host.renderer, current.clock)

    host.renderer.send(commands)


def clock_commands(renderer, clock):
    """Draw the clock using the header contrast and a stable-width face."""
    return [
        renderer.fill(
            CLOCK_LEFT, CLOCK_TOP, CLOCK_WIDTH, CLOCK_HEIGHT,
            ThemeRole.HEADER_BACKGROUND),
        renderer.text(
            CLOCK_TEXT_X, CLOCK_TEXT_Y, clock, ThemeRole.HEADER_TEXT,
            "JetBrainsMono 16pt", "left", "middle",
            max_width=CLOCK_TEXT_MAX_WIDTH, truncate=True),
    ]
