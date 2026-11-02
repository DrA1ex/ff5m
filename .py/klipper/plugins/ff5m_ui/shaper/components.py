# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
# This file may be distributed under the terms of the GNU GPLv3 license.

import math
from collections import OrderedDict

from ui import Overlay, ThemeColor
from ui.bindings import resolve
from ui.components import Component
from ui.properties import EditorSpec, Invalidation, PropertySpec, SourceSpec, ValidationSpec, property_schema

from .plot import plot_commands
from .state import METHODS

CACHE_BUDGET_BYTES = 384 * 1024


def spectrum_scale(psd, mode):
    """Common absolute calibration-PSD axis, independent of the selected fit.

    Saved values have Klipper's frequency normalization. They are not divided
    by their peak here, and no raw physical PSD units are invented for them.
    """
    peak = max(psd) or 1.
    if mode == "log":
        upper = math.ceil(math.log10(peak))
        lower = upper - 6
        return {"top": 1., "floor": 10. ** lower, "lower_log": lower,
                "ticks": tuple("1e%d" % exponent for exponent in (lower, upper - 3, upper)),
                "title": "CAL PSD (LOG)"}
    exponent = math.floor(math.log10(peak))
    unit = 10. ** exponent
    top = math.ceil(peak / unit * 2.) / 2. * unit
    return {"top": top, "ticks": tuple("%.2g" % (top * f / unit) for f in (0., .5, 1.)),
            "title": "CAL PSD x1e%d" % exponent}


def spectrum_commands(renderer, result, method, scale, bounds, before_color=ThemeColor.PRIMARY,
                      after_color=ThemeColor.SECONDARY, line_width=2, reference=None):
    selected = next(s for s in result["shapers"] if s["name"] == method)
    frequencies, psd = result["freq_bins"], result["psd_sum"]
    # A transfer-function amplitude becomes power only after squaring.
    after = tuple(p * response * response for p, response in zip(psd, selected["response"]))
    curves = []
    if reference:
        curves.append((reference["freq_bins"], reference["psd_sum"], ThemeColor.DIM))
    curves.extend(((frequencies, psd, before_color), (frequencies, after, after_color)))
    axis = spectrum_scale(list(psd) + (reference["psd_sum"] if reference else []), scale)
    if scale == "log":
        def transform(values):
            return tuple((math.log10(max(p, axis["floor"])) - axis["lower_log"]) / 6. for p in values)
        curves = [(xs, transform(ys), color) for xs, ys, color in curves]
    return plot_commands(renderer, bounds, tuple(curves), y_max=axis["top"], line_width=line_width)


class MethodRow(Overlay):
    """Editable button and cells, painted together for touch feedback."""

    def render(self, renderer, state, layout):
        button, cells = self.children
        bounds = layout.rect(button)
        foreground = cells.render(renderer, state, layout)
        action = resolve(button.action, state)
        normal = renderer.composite_button(
            "", *bounds, resolve(button.label, state), resolve(button.state, state),
            resolve(button.font, state), include_hitbox=False)
        pressed = renderer.composite_button(
            "", *bounds, resolve(button.label, state), "pressed",
            resolve(button.font, state), include_hitbox=False)
        return renderer.button_surface(action, *bounds, normal + foreground, pressed + foreground)


class SpectrumPlot(Component):
    """Only the spectrum raster is custom; surrounding labels belong to layout."""

    covers_bounds = True
    property_schema = property_schema(
        PropertySpec("result", (dict, type(None)), default=None, nullable=True,
                     editor=EditorSpec("collection_binding", group="Data"),
                     bindings=("direct", "derived"), source=SourceSpec(name="result", position=0)),
        PropertySpec("reference", (dict, type(None)), default=None, nullable=True,
                     editor=EditorSpec("collection_binding", group="Data"),
                     bindings=("direct", "derived"), source=SourceSpec(name="reference")),
        PropertySpec("method", str, default="mzv", bindings=("direct", "derived"),
                     editor=EditorSpec("select", choices=METHODS), validation=ValidationSpec(choices=METHODS),
                     source=SourceSpec(name="method", position=1)),
        PropertySpec("scale", str, default="linear", bindings=("direct", "derived"),
                     validation=ValidationSpec(choices=("linear", "log")),
                     source=SourceSpec(name="scale", position=2)),
        *(PropertySpec(name, (ThemeColor, str), default=default,
                       editor=EditorSpec("theme_color", group="Appearance", catalog="theme_tokens"),
                       bindings=("direct", "derived"), source=SourceSpec(name=name))
          for name, default in (("before_color", ThemeColor.PRIMARY), ("after_color", ThemeColor.SECONDARY))),
        PropertySpec("line_width", int, default=2, editor=EditorSpec("number", group="Appearance"),
                     validation=ValidationSpec(minimum=1, maximum=6), bindings=("direct",),
                     invalidation=Invalidation.PAINT, source=SourceSpec(name="line_width")),
    )

    def __init__(self, result=None, method="mzv", scale="linear", before_color=ThemeColor.PRIMARY,
                 after_color=ThemeColor.SECONDARY, line_width=2, key=None, reference=None):
        super().__init__(key=key)
        self.result, self.method, self.scale = result, method, scale
        self.reference = reference
        self.before_color, self.after_color, self.line_width = before_color, after_color, line_width
        self._cache_key, self._commands = None, ()
        self._bounds = None
        self._cache = OrderedDict()
        self._cache_bytes = 0

    @property
    def commands(self):
        return self._commands

    def clear_cache(self):
        self._cache.clear()
        self._cache_bytes = 0
        self._cache_key = None
        self._commands = ()
        self._bounds = None

    def state_signature(self, state):
        return (resolve(self.result, state), resolve(self.method, state), resolve(self.scale, state),
                resolve(self.before_color, state), resolve(self.after_color, state), resolve(self.line_width, state),
                resolve(self.reference, state))

    def draw(self, renderer, state, bounds):
        before, after = resolve(self.before_color, state), resolve(self.after_color, state)
        signature = self.state_signature(state)
        result = signature[0] or {}
        reference = signature[6] or {}
        fit = next((s for s in result.get("shapers", ()) if s["name"] == signature[1]), {})
        data_key = (tuple(result.get("freq_bins", ())), tuple(result.get("psd_sum", ())), tuple(fit.get("response", ())),
                    tuple(reference.get("freq_bins", ())), tuple(reference.get("psd_sum", ())))
        key = (data_key, signature[1:6], tuple(bounds), *(renderer.color(color) for color in
               (before, after, ThemeColor.DIM, ThemeColor.BACKGROUND, ThemeColor.BORDER)))
        if key != self._cache_key:
            cached = self._cache.pop(key, None)
            if cached is not None:
                self._commands, size = cached
                self._cache[key] = cached
            else:
                if result and fit:
                    self._commands = spectrum_commands(renderer, result, signature[1], signature[2],
                                                       bounds, before, after, signature[5], reference=reference)
                else:
                    self._commands = plot_commands(renderer, bounds, ())
                size = sum(len(c) + len(getattr(c, "payload", b"")) + 64 for c in self._commands)
                size += sum(len(part) * 8 for part in data_key) + 256
                while self._cache and self._cache_bytes + size > CACHE_BUDGET_BYTES:
                    _, (_, old_size) = self._cache.popitem(last=False)
                    self._cache_bytes -= old_size
                if size <= CACHE_BUDGET_BYTES:
                    self._cache[key] = (self._commands, size)
                    self._cache_bytes += size
            self._cache_key, self._bounds = key, bounds
        return self._commands
