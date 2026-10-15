## FF5M compact, theme-colored persistent status bar.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
## This file may be distributed under the terms of the GNU GPLv3 license

import math

from ui import FeatherRenderer, ThemeColor, ThemeRole
from ui.renderer import FOOTER_HEIGHT, FOOTER_Y, FRAME_WIDTH, FRAME_X, SCREEN_HEIGHT, SCREEN_WIDTH


SEPARATOR = " · "
FONT = "JetBrainsMono 8pt"


def footer_values(extruder, bed, cpu_percent, memory_kib, network, status, style="METRICS"):
    if style == "VANILLA":
        return ("NOZZLE %.0f/%.0fC | BED %.0f/%.0fC" % (
            extruder["temperature"], extruder["target"], bed["temperature"], bed["target"]),
                "%s | %s" % (network, status.upper()))
    if style == "COLORFUL":
        return (SEPARATOR.join((
            "NOZZLE %.0f/%.0fC" % (extruder["temperature"], extruder["target"]),
            "BED %.0f/%.0fC" % (bed["temperature"], bed["target"]))),
                SEPARATOR.join((network, status.upper())))
    memory = "--"
    if memory_kib is not None and math.isfinite(memory_kib) and memory_kib >= 0:
        whole, fraction = divmod(memory_kib, 1024)
        # Display the nearest whole MiB, with an exact half rounded down.
        memory = str(int(whole) + (fraction > 512))
    cpu = "--" if cpu_percent is None else str(cpu_percent)
    left = SEPARATOR.join((
        "N %.0f/%.0fC" % (extruder["temperature"], extruder["target"]),
        "B %.0f/%.0fC" % (bed["temperature"], bed["target"]),
        "CPU %s%%" % cpu, "FREE %sMB" % memory))
    return left, SEPARATOR.join((network, status.upper()))


class PrinterRenderer(FeatherRenderer):
    """Keep the shared footer lifecycle and specialize only FF5M's drawing."""

    def _footer_commands(self, values):
        left, right = values
        left_fields, right_fields = left.split(SEPARATOR), right.split(SEPARATOR)
        if len(left_fields) not in (2, 4) or len(right_fields) != 2:
            return super()._footer_commands(values)
        fields = left_fields + right_fields
        if len(left_fields) == 4:
            colors = (ThemeRole.TEMPERATURE_NOZZLE, ThemeRole.TEMPERATURE_BED,
                      ThemeColor.TEXT, ThemeColor.TEXT, ThemeColor.SUCCESS, ThemeColor.PRIMARY)
        else:
            colors = (ThemeColor.SECONDARY, ThemeColor.WARNING, ThemeColor.TEXT, ThemeColor.PRIMARY)
        widths = [self.text_width(value, FONT) for value in fields]
        dot_width = self.text_width("·", FONT)
        available_gap = SCREEN_WIDTH - 40 - sum(widths)
        if len(fields) == 4:
            # Retain the original left/right groups, with full labels.
            gaps = (3 * dot_width, available_gap - 6 * dot_width, 3 * dot_width)
        else:
            gaps = (available_gap / 5,) * 5
        commands = [
            self.fill(0, FOOTER_Y - 2, SCREEN_WIDTH,
                      SCREEN_HEIGHT - FOOTER_Y + 2, ThemeColor.BACKGROUND),
            self.fill(10, FOOTER_Y, SCREEN_WIDTH - 20, FOOTER_HEIGHT - 1, ThemeColor.PANEL),
            self.stroke(FRAME_X, FOOTER_Y - 2, FRAME_WIDTH,
                        SCREEN_HEIGHT - FOOTER_Y - 2, ThemeColor.BORDER, 1),
        ]
        x = 20.0
        for index, (value, color, width) in enumerate(zip(fields, colors, widths)):
            commands.append(self.text(round(x), FOOTER_Y + 16, value, color, FONT))
            if index < len(gaps):
                gap = gaps[index]
                if len(fields) != 4 or index != 1:
                    commands.append(self.text(round(x + width + (gap - dot_width) / 2),
                                              FOOTER_Y + 16, "·", ThemeColor.DIM, FONT))
                x += width + gap
        return commands
