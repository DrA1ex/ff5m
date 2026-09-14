## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from ui import ThemeColor
from ui.bindings import resolve
from ui.components import Component

from .. import calibration_icons


class BedHardware(Component):
    """Draw a raised plate with mounting points linked to nuts below it."""

    def __init__(self, front_left, front_right, rear_right, rear_left, key=None):
        super().__init__(key=key)
        self.directions = {
            "front_left": front_left, "front_right": front_right,
            "rear_right": rear_right, "rear_left": rear_left,
        }

    def state_signature(self, state):
        return tuple(resolve(value, state) for value in self.directions.values())

    @staticmethod
    def _line(renderer, start, end):
        start_x, start_y = start
        end_x, end_y = end
        if start_y == end_y:
            return [renderer.fill(
                min(start_x, end_x), start_y - 1,
                abs(end_x - start_x) + 1, 2, ThemeColor.BRIGHT)]
        commands = []
        steps = max(abs(end_x - start_x), abs(end_y - start_y))
        run = None
        for step in range(0, steps + 1, 2):
            ratio = step / float(steps)
            x = int(round(start_x + (end_x - start_x) * ratio))
            y = int(round(start_y + (end_y - start_y) * ratio))
            if run is not None and x == run[0] and abs(y - run[2]) <= 3:
                run = (x, min(run[1], y), y, max(run[3], y))
                continue
            if run is not None:
                commands.append(renderer.fill(
                    run[0] - 1, run[1] - 1, 3, run[3] - run[1] + 3,
                    ThemeColor.BRIGHT))
            run = (x, y, y, y)
        if run is not None:
            commands.append(renderer.fill(
                run[0] - 1, run[1] - 1, 3, run[3] - run[1] + 3,
                ThemeColor.BRIGHT))
        return commands

    def draw(self, renderer, state, bounds):
        directions = {corner: resolve(value, state)
                      for corner, value in self.directions.items()}
        commands = []
        plate = bounds.inset(
            max(28, bounds.width // 12), 36,
            max(28, bounds.width // 12), 65)
        surface_height = plate.height - 9
        perspective_inset = min(42, plate.width // 8)
        # Merge identical scanlines to keep the complete frame within the
        # real renderer's bounded queue without changing its pixels.
        run_start = 0
        last_inset = perspective_inset
        for row in range(surface_height + 2):
            inset = round(perspective_inset *
                          (1 - row / float(surface_height)))
            if inset == last_inset and row <= surface_height:
                continue
            commands.append(renderer.fill(
                plate.x + last_inset, plate.y + run_start,
                plate.width - 2 * last_inset, row - run_start, ThemeColor.MUTED))
            run_start, last_inset = row, inset
        commands.append(renderer.fill(
            plate.x, plate.y + surface_height + 1,
            plate.width, 8, ThemeColor.PANEL))
        outline = (
            (plate.x + perspective_inset, plate.y),
            (plate.right - perspective_inset - 1, plate.y),
            (plate.right - 1, plate.y + surface_height),
            (plate.right - 1, plate.bottom),
            (plate.x, plate.bottom),
            (plate.x, plate.y + surface_height),
            (plate.x + perspective_inset, plate.y),
        )
        for start, end in zip(outline, outline[1:]):
            commands += self._line(renderer, start, end)
        commands += self._line(
            renderer,
            (plate.x, plate.y + surface_height),
            (plate.right - 1, plate.y + surface_height))

        nut_inset = min(24, bounds.width // 8)
        mounts = {
            "rear_left": (
                plate.x + perspective_inset + 12, plate.y + 12,
                bounds.x + nut_inset, bounds.y + 101),
            "rear_right": (
                plate.right - perspective_inset - 13, plate.y + 12,
                bounds.right - nut_inset - 1, bounds.y + 101),
            "front_left": (
                plate.x + 15, plate.y + surface_height - 12,
                bounds.x + nut_inset, bounds.bottom - 30),
            "front_right": (
                plate.right - 16, plate.y + surface_height - 12,
                bounds.right - nut_inset - 1, bounds.bottom - 30),
        }
        for corner, (mount_x, mount_y, nut_x, nut_y) in mounts.items():
            color = (ThemeColor.BRIGHT
                     if directions[corner] == "BASE"
                     else ThemeColor.PRIMARY)
            commands += renderer.filled_circle(mount_x, mount_y, 6, color)
            commands += renderer.filled_circle(mount_x, mount_y, 3, ThemeColor.MUTED)
            direction = directions[corner]
            if direction not in ("BASE", "CW", "CCW"):
                continue
            outward = -1 if nut_x < mount_x else 1
            for dash_x in range(mount_x + outward * 9, nut_x, outward * 7):
                commands.append(renderer.fill(
                    min(dash_x, dash_x + outward * 3), mount_y,
                    4, 2, color))
            # Stop before the rotation arc so the callout does not cross it.
            callout_end = nut_y - (15 if direction == "BASE" else 26)
            for dash_y in range(mount_y, callout_end, 7):
                commands.append(renderer.fill(
                    nut_x - 1, dash_y, 2, min(4, callout_end - dash_y), color))
            commands.append(calibration_icons.nut(renderer, nut_x, nut_y))
            if direction != "BASE":
                commands.append(calibration_icons.turn_arrow(
                    renderer, nut_x, nut_y, direction, color))
        return commands
