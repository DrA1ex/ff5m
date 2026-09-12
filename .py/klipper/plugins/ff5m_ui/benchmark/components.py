## Feature-local declarative components for the render benchmark.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import math

from ui import (
    CreationContract, CreationFieldSpec, EditorSpec, PropertySpec, ThemeColor,
    ValidationSpec, property_schema,
)
from ui.bindings import resolve
from ui.components import Component

from .constants import BENCHMARK_MODES


_TEXT_CUBE_SCHEMA = property_schema(
    PropertySpec(
        "angle_x", (int, float), default=0.0,
        editor=EditorSpec(
            "number", label="X angle", group="Animation", step=0.01)),
    PropertySpec(
        "angle_y", (int, float), default=0.0,
        editor=EditorSpec(
            "number", label="Y angle", group="Animation", step=0.01)),
    PropertySpec(
        "angle_z", (int, float), default=0.0,
        editor=EditorSpec(
            "number", label="Z angle", group="Animation", step=0.01)),
    PropertySpec(
        "mode", str, default=BENCHMARK_MODES[0],
        validation=ValidationSpec(choices=BENCHMARK_MODES),
        editor=EditorSpec(
            "select", label="Mode", group="Benchmark",
            choices=BENCHMARK_MODES)),
)


def _creation_field(spec):
    return CreationFieldSpec(
        spec.name, spec.runtime_type, required=False, default=spec.default,
        nullable=spec.nullable, validation=spec.validation,
        editor=spec.editor, bindings=spec.bindings,
        invalidation=spec.invalidation, live=spec.live, source=spec.source,
    )


class TextCube(Component):
    """Render a rotating cube in one of several benchmark-specific modes."""

    property_schema = _TEXT_CUBE_SCHEMA
    creation_contract = CreationContract(
        "Benchmark",
        fields=tuple(_creation_field(spec) for spec in _TEXT_CUBE_SCHEMA),
    )

    _COLUMNS = 39
    _ROWS = 13
    _EDGES = (
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    )
    _VERTICES = (
        (-1.0, -1.0, -1.0), (1.0, -1.0, -1.0),
        (1.0, 1.0, -1.0), (-1.0, 1.0, -1.0),
        (-1.0, -1.0, 1.0), (1.0, -1.0, 1.0),
        (1.0, 1.0, 1.0), (-1.0, 1.0, 1.0),
    )
    _GLYPHS = "FEATHERX"
    # Keep animated primitives away from the repaint-boundary border.  The
    # asymmetric vertical inset also moves the cube slightly down: the text
    # raster extends farther above its anchor than below it on the device.
    _CONTENT_INSETS = (8, 12, 8, 4)
    # Keep colors attached to physical edge groups instead of changing them
    # with depth or time.  The first four edges belong to the rear face, the
    # next four to the front face, and the final four connect both faces.
    _EDGE_GROUPS = (
        0, 0, 0, 0,
        1, 1, 1, 1,
        2, 2, 2, 2,
    )
    _GROUP_COLORS = (
        ThemeColor.PRIMARY,
        ThemeColor.SECONDARY,
        ThemeColor.BRIGHT,
    )
    _FILL_COLOR = ThemeColor.SECONDARY
    _FILL_BAND_HEIGHT = 2
    _DOT_RATIOS = (
        0.0, 0.125, 0.25, 0.375, 0.5,
        0.625, 0.75, 0.875, 1.0,
    )

    def __init__(self, angle_x, angle_y, angle_z, mode, key=None):
        super().__init__(key=key)
        self.angle_x = angle_x
        self.angle_y = angle_y
        self.angle_z = angle_z
        self.mode = mode

    @classmethod
    def _projected_vertices(cls, angle_x, angle_y, angle_z):
        # Trigonometric functions are relatively expensive on the printer.
        # Compute the six values once per frame instead of once per vertex.
        cx, sx = math.cos(angle_x), math.sin(angle_x)
        cy, sy = math.cos(angle_y), math.sin(angle_y)
        cz, sz = math.cos(angle_z), math.sin(angle_z)
        center_x = (cls._COLUMNS - 1) * 0.5
        center_y = (cls._ROWS - 1) * 0.5
        projected = []
        for x, y, z in cls._VERTICES:
            rotated_y = y * cx - z * sx
            rotated_z = y * sx + z * cx
            rotated_x = x * cy + rotated_z * sy
            rotated_z = -x * sy + rotated_z * cy
            x = rotated_x * cz - rotated_y * sz
            y = rotated_x * sz + rotated_y * cz
            perspective = 3.0 / (4.3 - rotated_z)
            projected.append((
                center_x + x * perspective * 10.2,
                center_y + y * perspective * 5.2,
                rotated_z,
            ))
        return tuple(projected)

    @classmethod
    def _content_bounds(cls, bounds):
        x, y, width, height = bounds
        left, top, right, bottom = cls._CONTENT_INSETS
        return (
            x + left, y + top,
            max(1, width - left - right),
            max(1, height - top - bottom),
        )

    @classmethod
    def _rows(cls, projected):
        # The cube is a fixed 39x13 character surface. Flat buffers avoid the
        # nested-list, set and tuple churn that was visible in device profiles.
        columns = cls._COLUMNS
        rows_count = cls._ROWS
        cell_count = columns * rows_count
        depth = [-1000.0] * cell_count
        glyph = [" "] * cell_count
        group = [-1] * cell_count

        for edge_index, (start_index, end_index) in enumerate(cls._EDGES):
            start = projected[start_index]
            end = projected[end_index]
            delta_x = end[0] - start[0]
            delta_y = end[1] - start[1]
            delta_z = end[2] - start[2]
            span = max(abs(delta_x), abs(delta_y))
            steps = max(2, int(math.ceil(span * 2.0)))
            inverse_steps = 1.0 / steps
            edge_group = cls._EDGE_GROUPS[edge_index]
            for step in range(steps + 1):
                ratio = step * inverse_steps
                column = int(round(start[0] + delta_x * ratio))
                row = int(round(start[1] + delta_y * ratio))
                if not (0 <= column < columns and 0 <= row < rows_count):
                    continue
                index = row * columns + column
                value_depth = start[2] + delta_z * ratio
                if value_depth < depth[index]:
                    continue
                depth[index] = value_depth
                value = cls._GLYPHS[
                    (edge_index + step) % len(cls._GLYPHS)]
                glyph[index] = (
                    value if value_depth >= 0.0 else value.lower())
                group[index] = edge_group

        for vertex_index, (x, y, z) in enumerate(projected):
            column, row = int(round(x)), int(round(y))
            if 0 <= column < columns and 0 <= row < rows_count:
                index = row * columns + column
                depth[index] = max(depth[index], z + 0.01)
                glyph[index] = "+"
                # Vertices stay attached to their physical rear/front face.
                group[index] = 0 if vertex_index < 4 else 1

        shadow = [" "] * cell_count
        for index, value in enumerate(glyph):
            if value == " ":
                continue
            row, column = divmod(index, columns)
            shadow_column = column + 2
            shadow_row = row + 1
            if shadow_column >= columns or shadow_row >= rows_count:
                continue
            shadow_index = shadow_row * columns + shadow_column
            if glyph[shadow_index] == " ":
                shadow[shadow_index] = ":"

        result = []
        for row in range(rows_count):
            beginning = row * columns
            ending = beginning + columns
            row_glyph = glyph[beginning:ending]
            row_group = group[beginning:ending]
            layers = tuple(
                "".join(
                    value if row_group[column] == group_id else " "
                    for column, value in enumerate(row_glyph)
                ).rstrip()
                for group_id in range(len(cls._GROUP_COLORS))
            )
            result.append((
                "".join(shadow[beginning:ending]).rstrip(),
                layers,
            ))
        return tuple(result)

    @classmethod
    def _edge_color(cls, edge_index):
        return cls._GROUP_COLORS[cls._EDGE_GROUPS[edge_index]]

    @staticmethod
    def _line(renderer, start_x, start_y, end_x, end_y, color, line_width=1):
        return "--batch line -s %d %d -e %d %d -c %s -lw %d" % (
            int(round(start_x)), int(round(start_y)),
            int(round(end_x)), int(round(end_y)),
            renderer.color(color), int(line_width))

    @classmethod
    def _draw_text_mode(cls, renderer, bounds, projected):
        x, y, width, height = bounds
        advance_x = max(
            renderer.font_advance("JetBrainsMono 8pt"),
            renderer.font_advance("JetBrainsMono Bold 8pt"),
        )
        line_height = 21
        text_width = cls._COLUMNS * advance_x
        text_height = cls._ROWS * line_height
        text_x = x + max(8, (width - text_width) // 2)
        text_y = y + max(8, (height - text_height) // 2)
        commands = []
        for row, (shadow, layers) in enumerate(cls._rows(projected)):
            row_y = text_y + row * line_height
            if shadow:
                commands.append(renderer.text(
                    text_x, row_y, shadow, ThemeColor.MUTED,
                    "JetBrainsMono 8pt", "left", "top"))
            for color, text in zip(cls._GROUP_COLORS, layers):
                if text:
                    commands.append(renderer.text(
                        text_x, row_y, text, color,
                        "JetBrainsMono Bold 8pt", "left", "top"))
        return commands

    @classmethod
    def _draw_line_mode(cls, renderer, bounds, projected):
        # Keep this mode intentionally close to the primitive under test:
        # one native line command per cube edge, with no text-like decoration.
        x, y, width, height = bounds
        center_x = x + width * 0.5
        center_y = y + height * 0.5
        scale_x = max(1.0, width / float(cls._COLUMNS + 6))
        scale_y = max(1.0, height / float(cls._ROWS + 5))
        points = tuple((
            center_x + (point[0] - (cls._COLUMNS - 1) * 0.5) * scale_x,
            center_y + (point[1] - (cls._ROWS - 1) * 0.5) * scale_y,
            point[2],
        ) for point in projected)
        commands = []
        edges = sorted(
            enumerate(cls._EDGES),
            key=lambda item: (
                points[item[1][0]][2] + points[item[1][1]][2]) * 0.5)
        for edge_index, (start_index, end_index) in edges:
            start = points[start_index]
            end = points[end_index]
            depth = (start[2] + end[2]) * 0.5
            commands.append(cls._line(
                renderer, start[0], start[1], end[0], end[1],
                cls._edge_color(edge_index),
                2 if depth > 0.25 else 1))
        return commands

    @classmethod
    def _draw_dots_mode(cls, renderer, bounds, projected):
        x, y, width, height = bounds
        center_x = x + width * 0.5
        center_y = y + height * 0.5
        scale_x = max(1.0, width / float(cls._COLUMNS + 6))
        scale_y = max(1.0, height / float(cls._ROWS + 5))

        def canvas(point):
            return (
                center_x + (point[0] - (cls._COLUMNS - 1) * 0.5) * scale_x,
                center_y + (point[1] - (cls._ROWS - 1) * 0.5) * scale_y,
                point[2],
            )

        points = tuple(canvas(point) for point in projected)
        occupied = {}
        shadow = set()
        for edge_index, (start_index, end_index) in enumerate(cls._EDGES):
            start = points[start_index]
            end = points[end_index]
            delta_x = end[0] - start[0]
            delta_y = end[1] - start[1]
            delta_z = end[2] - start[2]
            for ratio in cls._DOT_RATIOS:
                sample_x = start[0] + delta_x * ratio
                sample_y = start[1] + delta_y * ratio
                sample_z = start[2] + delta_z * ratio
                key = (int(round(sample_x)), int(round(sample_y)))
                current = occupied.get(key)
                if current is None or sample_z >= current[0]:
                    occupied[key] = (
                        sample_z, cls._edge_color(edge_index))
        for point_x, point_y, point_z in points:
            key = (int(round(point_x)), int(round(point_y)))
            occupied[key] = (point_z + 0.02, ThemeColor.BRIGHT)
        for point_x, point_y in occupied:
            shadow.add((point_x + 2, point_y + 1))
        commands = []
        for point_x, point_y in shadow:
            if (point_x, point_y) not in occupied:
                commands.append(renderer.fill(
                    point_x, point_y, 1, 1, ThemeColor.MUTED))
        for (point_x, point_y), (_depth, color) in occupied.items():
            commands.append(renderer.fill(point_x, point_y, 2, 2, color))
        return commands

    @classmethod
    def _draw_fill_mode(cls, renderer, bounds, angle):
        """Rasterize one rotating square into coarse horizontal bands.

        The benchmark is intentionally fill-heavy, but one one-pixel command
        per scanline makes Python command generation dominate too strongly on
        the target SoC.  Two-pixel bands keep the staircase subtle while still
        cutting polygon sampling and fill-command count roughly in half.
        """
        x, y, width, height = bounds
        center_x = x + width * 0.5
        center_y = y + height * 0.5
        half_side = min(width, height) * 0.34
        cosine, sine = math.cos(angle), math.sin(angle)
        corners = tuple((
            center_x + local_x * cosine - local_y * sine,
            center_y + local_x * sine + local_y * cosine,
        ) for local_x, local_y in (
            (-half_side, -half_side),
            (half_side, -half_side),
            (half_side, half_side),
            (-half_side, half_side),
        ))
        top = max(y, int(math.floor(min(point[1] for point in corners))))
        bottom = min(
            y + height - 1,
            int(math.ceil(max(point[1] for point in corners))))
        commands = []
        band_height = cls._FILL_BAND_HEIGHT
        for row in range(top, bottom + 1, band_height):
            height_px = min(band_height, bottom - row + 1)
            sample_y = row + height_px * 0.5
            intersections = []
            for index, start in enumerate(corners):
                end = corners[(index + 1) % len(corners)]
                if ((start[1] <= sample_y < end[1])
                        or (end[1] <= sample_y < start[1])):
                    ratio = ((sample_y - start[1])
                             / (end[1] - start[1]))
                    intersections.append(
                        start[0] + (end[0] - start[0]) * ratio)
            if len(intersections) != 2:
                continue
            left = max(x, int(math.ceil(min(intersections))))
            right = min(
                x + width - 1, int(math.floor(max(intersections))))
            if right >= left:
                commands.append(renderer.fill(
                    left, row, right - left + 1, height_px,
                    cls._FILL_COLOR))
        return commands

    def draw(self, renderer, state, bounds):
        angle_x = float(resolve(self.angle_x, state))
        angle_y = float(resolve(self.angle_y, state))
        angle_z = float(resolve(self.angle_z, state))
        mode = str(resolve(self.mode, state)).lower()
        commands = []
        content_bounds = self._content_bounds(bounds)
        if mode == "fills":
            commands += self._draw_fill_mode(
                renderer, content_bounds, angle_z)
        else:
            projected = self._projected_vertices(
                angle_x, angle_y, angle_z)
            if mode == "lines":
                commands += self._draw_line_mode(
                    renderer, content_bounds, projected)
            elif mode == "dots":
                commands += self._draw_dots_mode(
                    renderer, content_bounds, projected)
            else:
                commands += self._draw_text_mode(
                    renderer, content_bounds, projected)
        return commands
