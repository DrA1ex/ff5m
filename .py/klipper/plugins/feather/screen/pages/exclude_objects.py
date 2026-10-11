## Print menu and object exclusion for Feather.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import math
import shlex

from ff5m_ui.screen import ScreenDialog, ScreenPage


OBJECT_ROWS = 3


def exclude_command(name):
    # Klipper strips these delimiters before shlex handles quoted parameters.
    if any(character in "#*;" or ord(character) < 32 or ord(character) == 127 for character in name):
        raise RuntimeError("This object name contains characters unsupported by Klipper")
    return "EXCLUDE_OBJECT NAME=" + shlex.quote(name)


def polygon_bounds(value):
    """Use supplied polygon extents only; a center cannot establish size."""
    try:
        if not isinstance(value, (list, tuple)) or len(value) < 3:
            return None
        points = [(float(x), float(y)) for x, y in value]
        if not all(math.isfinite(v) for point in points for v in point):
            return None
        xs, ys = zip(*points)
        bounds = min(xs), min(ys), max(xs), max(ys)
        return bounds if bounds[0] < bounds[2] and bounds[1] < bounds[3] else None
    except (TypeError, ValueError, OverflowError):
        return None


def object_action(name):
    from ff5m_ui.exclude_model import object_action as action
    return action(name)


def bed_object_rect(bounds, bed):
    if bounds is None or bed is None:
        return None
    x0, y0, x1, y1 = bed
    if not (x0 <= bounds[0] < bounds[2] <= x1 and y0 <= bounds[1] < bounds[3] <= y1):
        return None
    scale = 1.0 / max(x1 - x0, y1 - y0)
    left = (1 - (x1 - x0) * scale) / 2
    top = (1 - (y1 - y0) * scale) / 2
    return [left + (bounds[0] - x0) * scale, top + (y1 - bounds[3]) * scale,
            (bounds[2] - bounds[0]) * scale, (bounds[3] - bounds[1]) * scale]


def exclude_values(objects, excluded, bed, selected=None, page=0):
    """Produce one UI snapshot from supplied object definitions, without effects."""
    from ff5m_ui.exclude_model import ExcludeState
    selected = selected or set()
    pending = selected - excluded
    page_count = max(1, (len(objects) + OBJECT_ROWS - 1) // OBJECT_ROWS)
    items = []
    for index, item in enumerate(objects):
        name = item["name"]
        rectangle = bed_object_rect(polygon_bounds(item.get("polygon")), bed)
        note = "SELECTED" if name in selected else "TAP TO SELECT"
        if name in excluded:
            note = "SELECTED / EXCLUDED" if name in selected else "EXCLUDED"
        if rectangle is None:
            note += " / NO GEOMETRY"
        items.append(dict(
            name=name, kind="object", label="%02d  %s" % (index + 1, name.replace("_", " ")),
            note=note, marker=str(index + 1),
            selected=name in selected, excluded=name in excluded, rectangle=rectangle,
        ))
    mapped = [item for item in items if item["rectangle"] is not None]
    return {
        ExcludeState.OBJECTS: mapped,
        ExcludeState.ROWS: items[page * OBJECT_ROWS:(page + 1) * OBJECT_ROWS],
        ExcludeState.COUNT: len(items), ExcludeState.EMPTY: not items,
        ExcludeState.NO_GEOMETRY: not mapped, ExcludeState.PAGINATED: page_count > 1,
        ExcludeState.PAGE: "%d / %d" % (page + 1, page_count),
        ExcludeState.PREVIOUS: "enabled" if page else "disabled",
        ExcludeState.NEXT: "enabled" if page + 1 < page_count else "disabled",
        ExcludeState.REQUEST: "danger" if pending else "disabled",
        ExcludeState.REQUEST_LABEL: ("EXCLUDE SELECTED (%d)" % len(pending) if pending else
                                     "ALREADY EXCLUDED" if selected else "EXCLUDE SELECTED"),
    }


class ExcludeObjectsPagesMixin:
    def _open_print_more(self):
        if self._print_controls_ready():
            self._show_dialog(ScreenDialog.PRINT_MORE, content={})

    def _render_print_more(self, instance):
        from ff5m_ui.printing.more import PAGE
        commands = PAGE.draw(self.renderer)
        self.renderer.send(commands)

    def _handle_more_action(self, action):
        if self._current_dialog() != ScreenDialog.PRINT_MORE:
            return
        self._close_dialog(ScreenDialog.PRINT_MORE)
        if action == "more.filament":
            self._handle_print_action("print.filament")
        elif action == "more.exclude":
            self.exclude_selected = set()
            self.exclude_list_page = 0
            self._show_page(ScreenPage.EXCLUDE_MODEL)

    def _exclude_status(self):
        source = self.printer.lookup_object("exclude_object", None)
        return source.get_status(self.reactor.monotonic()) if source is not None else {}

    def _exclude_objects(self):
        status = self._exclude_status()
        objects = []
        names = set()
        for item in status.get("objects", ()):
            name = item.get("name") if isinstance(item, dict) else None
            if isinstance(name, str) and name and name not in names:
                objects.append(item)
                names.add(name)
        return objects, set(status.get("excluded_objects", ()))

    def _exclude_bed(self):
        # Object polygons and MOVE_SAFE use the same print coordinates.
        # ToolHead limits include parking travel beyond the printable bed.
        source = self.printer.lookup_object("gcode_macro MOVE_SAFE", None)
        if source is None:
            return None
        status = source.get_status(self.reactor.monotonic())
        try:
            bounds = tuple(float(status[key]) for key in ("x_min", "y_min", "x_max", "y_max"))
            if all(math.isfinite(v) for v in bounds) and bounds[0] < bounds[2] and bounds[1] < bounds[3]:
                return bounds
        except (KeyError, TypeError, ValueError, OverflowError):
            pass
        return None

    def _render_exclude_model(self):
        objects, excluded = self._exclude_objects()
        self.exclude_selected = selected = getattr(self, "exclude_selected", set()) & {item["name"] for item in objects}
        page_count = max(1, (len(objects) + OBJECT_ROWS - 1) // OBJECT_ROWS)
        self.exclude_list_page = page = min(getattr(self, "exclude_list_page", 0), page_count - 1)
        bed = self._exclude_bed()
        from ff5m_ui.exclude_model import PAGE
        commands = self.renderer.begin_page("Exclude Model")
        commands += PAGE.draw(self.renderer, exclude_values(objects, excluded, bed, selected, page))
        self.renderer.send(commands)

    def _check_exclude_selection(self, names, job):
        objects, excluded = self._exclude_objects()
        if (job != self.virtual_sdcard.file_path()
                or self.print_stats.get_status(self.reactor.monotonic())["state"] not in ("printing", "paused")
                or not set(names) <= {item["name"] for item in objects}):
            raise RuntimeError("The selected models are no longer part of the active print")
        return excluded

    def _handle_exclude_action(self, action):
        if action == "exclude.confirm":
            instance = self._current_dialog_instance()
            if instance is None or "exclude_names" not in instance.content:
                return
            names, job = instance.content["exclude_names"], instance.content["exclude_job"]
            self._close_dialog(instance)
            excluded = self._check_exclude_selection(names, job)
            # Validate the complete batch before applying any exclusion. Commands
            # can yield, so re-observe the job and external exclusions each time.
            commands = [(name, exclude_command(name)) for name in names if name not in excluded]
            for name, command in commands:
                if name not in self._check_exclude_selection(names, job):
                    self._run_script(command, show_notice=False)
            self._render_screen()
            return
        if self.page != ScreenPage.EXCLUDE_MODEL:
            return
        objects, excluded = self._exclude_objects()
        if action == "exclude.back":
            self._show_page(self.page_for_print_state())
        elif action.startswith("exclude.select."):
            from ff5m_ui.exclude_model import PAGE, ExcludeCommand
            from ui.actions import Command
            selected_action = PAGE.resolve_action(action)
            if not isinstance(selected_action, Command) or selected_action.key != ExcludeCommand.SELECT:
                return
            name = selected_action.payload
            if name in {item["name"] for item in objects}:
                self.exclude_selected.symmetric_difference_update((name,))
                self.exclude_list_page = next(i for i, item in enumerate(objects) if item["name"] == name) // OBJECT_ROWS
                self._render_screen()
        elif action in ("exclude.prev", "exclude.next"):
            last = max(0, (len(objects) - 1) // OBJECT_ROWS)
            self.exclude_list_page = max(0, min(last, self.exclude_list_page + (-1 if action.endswith("prev") else 1)))
            self._render_screen()
        elif action == "exclude.request":
            selected = getattr(self, "exclude_selected", set())
            names = tuple(item["name"] for item in objects if item["name"] in selected and item["name"] not in excluded)
            if not names:
                return
            count = len(names)
            self._show_dialog(ScreenDialog.MESSAGE, content={
                "title": "Exclude this model?" if count == 1 else "Exclude %d models?" % count,
                "message": ("\n".join(name.replace("_", " ") for name in names) + "\n"
                            + ("This model will stop printing." if count == 1 else "These models will stop printing.")
                            + " Other models continue."),
                "actions": (("message.ok", "GO BACK", "enabled"),
                            ("exclude.confirm", "EXCLUDE MODEL" if count == 1 else "EXCLUDE MODELS", "danger")),
                "exclude_names": names,
                "exclude_job": self.virtual_sdcard.file_path(),
            })
