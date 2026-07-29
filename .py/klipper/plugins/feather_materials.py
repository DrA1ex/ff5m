## Material profile loading and selector rendering for Feather.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import re


MATERIAL_NAME = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)*$")
MAX_MATERIAL_SLOTS = 5


class MaterialConfigError(ValueError):
    pass


class MaterialCatalog:
    """Validated view of the merged Klipper _MATERIAL_CONFIG macro."""

    def __init__(self, heating, cold_pull):
        self.heating_materials = tuple(item[0] for item in heating)
        self.heating_profiles = {
            item[0]: (float(item[1]), float(item[2])) for item in heating
        }
        self.cold_pull_materials = tuple(item[0] for item in cold_pull)
        self.cold_pull_profiles = {
            item[0]: (float(item[1]), float(item[2])) for item in cold_pull
        }

    @classmethod
    def from_macro(cls, macro, extruder, heater_bed):
        variables = dict(getattr(macro, "variables", {}) or {})
        nozzle_heater = getattr(extruder, "heater", extruder)
        bed_heater = getattr(heater_bed, "heater", heater_bed)
        nozzle_bounds = (
            float(getattr(nozzle_heater, "min_temp", 0.0)),
            float(getattr(nozzle_heater, "max_temp", 300.0)),
        )
        bed_bounds = (
            float(getattr(bed_heater, "min_temp", 0.0)),
            float(getattr(bed_heater, "max_temp", 130.0)),
        )
        min_extrude = float(getattr(extruder, "min_extrude_temp", 0.0))
        heating = cls._workflow(
            variables, "heating", ("nozzle", "bed"),
            (nozzle_bounds, bed_bounds))
        cold_pull = cls._workflow(
            variables, "cold_pull", ("hot", "cold"),
            (nozzle_bounds, nozzle_bounds), min_extrude=min_extrude)
        return cls(heating, cold_pull)

    @classmethod
    def _workflow(cls, variables, workflow, fields, bounds,
                  min_extrude=None):
        slots_key = "%s_slots" % workflow
        if slots_key not in variables:
            raise MaterialConfigError(
                "_MATERIAL_CONFIG is missing %s" % slots_key)
        slots = variables[slots_key]
        if not isinstance(slots, (list, tuple)):
            raise MaterialConfigError("%s must be a list" % slots_key)
        if len(slots) > MAX_MATERIAL_SLOTS:
            raise MaterialConfigError(
                "%s may contain at most %d slots" %
                (slots_key, MAX_MATERIAL_SLOTS))
        seen_slots = set()
        seen_names = set()
        profiles = []
        for slot in slots:
            if isinstance(slot, bool) or not isinstance(slot, int) or slot <= 0:
                raise MaterialConfigError(
                    "%s must contain positive integer slots" % slots_key)
            if slot in seen_slots:
                raise MaterialConfigError(
                    "%s contains duplicate slot %d" % (slots_key, slot))
            seen_slots.add(slot)
            name_key = "material_%d" % slot
            profile_key = "%s_%s" % (name_key, workflow)
            if name_key not in variables:
                raise MaterialConfigError(
                    "active slot %d is missing %s" % (slot, name_key))
            name = variables[name_key]
            if (not isinstance(name, str) or name == "n/a"
                    or MATERIAL_NAME.match(name) is None):
                raise MaterialConfigError(
                    "%s must match %s and must not be n/a" %
                    (name_key, MATERIAL_NAME.pattern))
            if name in seen_names:
                raise MaterialConfigError(
                    "%s contains duplicate material name %s" %
                    (slots_key, name))
            seen_names.add(name)
            if profile_key not in variables:
                raise MaterialConfigError(
                    "active slot %d is missing %s" % (slot, profile_key))
            profile = variables[profile_key]
            if not isinstance(profile, dict):
                raise MaterialConfigError("%s must be a dictionary" % profile_key)
            values = []
            for field, limits in zip(fields, bounds):
                if field not in profile:
                    raise MaterialConfigError(
                        "%s is missing %s" % (profile_key, field))
                value = profile[field]
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise MaterialConfigError(
                        "%s.%s must be numeric" % (profile_key, field))
                value = float(value)
                if not limits[0] <= value <= limits[1]:
                    raise MaterialConfigError(
                        "%s.%s %.3f is outside hardware limits %.3f..%.3f" %
                        (profile_key, field, value, limits[0], limits[1]))
                values.append(value)
            if workflow == "cold_pull":
                hot, cold = values
                if hot <= cold:
                    raise MaterialConfigError(
                        "%s hot temperature must be above cold" % profile_key)
                if hot < min_extrude:
                    raise MaterialConfigError(
                        "%s hot temperature must permit extrusion (>= %.3f)" %
                        (profile_key, min_extrude))
            profiles.append((name,) + tuple(values))
        return profiles


def load_material_catalog(printer, extruder, heater_bed):
    macro = printer.lookup_object("gcode_macro _MATERIAL_CONFIG", None)
    if macro is None:
        raise MaterialConfigError("gcode_macro _MATERIAL_CONFIG is not loaded")
    return MaterialCatalog.from_macro(macro, extruder, heater_bed)


def material_actions(prefix, materials):
    return tuple("%s%s" % (prefix, material) for material in materials)


def adaptive_grid_columns(count):
    if count >= 5:
        return 3
    if count >= 2:
        return 2
    return 1


def render_material_selector(renderer, action_prefix, x, y,
                             button_width, button_height,
                             columns=None, column_gap=0, row_gap=0,
                             materials=(), selected=None, label=None,
                             font=None, area_width=None):
    """Render an ordered selector and center every incomplete row."""
    materials = tuple(materials)
    if not materials:
        return []
    if columns is None:
        columns = len(materials)
    if columns <= 0:
        raise ValueError("Material selector columns must be positive")
    columns = min(columns, len(materials))
    if area_width is None:
        area_width = columns * button_width + (columns - 1) * column_gap
    commands = []
    for row_start in range(0, len(materials), columns):
        row = materials[row_start:row_start + columns]
        row_width = len(row) * button_width + max(0, len(row) - 1) * column_gap
        row_x = x + (area_width - row_width) // 2
        for column, material in enumerate(row):
            button_label = label(material) if label is not None else material
            options = {
                "state": "selected" if material == selected else "enabled",
            }
            if font is not None:
                options["font"] = font
            commands += renderer.button(
                "%s%s" % (action_prefix, material),
                row_x + column * (button_width + column_gap),
                y + (row_start // columns) * (button_height + row_gap),
                button_width, button_height, button_label, **options)
    return commands
