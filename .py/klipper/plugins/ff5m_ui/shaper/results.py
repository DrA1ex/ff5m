# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
# This file may be distributed under the terms of the GNU GPLv3 license.

import time

from .components import spectrum_scale
from .page import CONTENT, ShaperRef, create_page
from .state import APPLICABLE_METHODS, ShaperState, comparison_values


class ShaperResults:
    """Presentation and graph cache; selected settings are supplied by Klipper."""

    def __init__(self, bounds=CONTENT):
        self.bounds = bounds
        self.results = None
        self.selections = {}
        self.show_table = False
        self.table_page = None
        self.graph_page = create_page(bounds)
        self.plot = next(node for node in self.graph_page.root.walk() if node.key == ShaperRef.PLOT)

    @property
    def page(self):
        return self.table_page if self.show_table else self.graph_page

    @property
    def axis(self):
        return self.graph_page.state[ShaperState.AXIS]

    @property
    def method(self):
        return self.graph_page.state[ShaperState.METHOD]

    @property
    def can_save(self):
        return bool(self.selections)

    @property
    def can_apply(self):
        return self.method in APPLICABLE_METHODS

    @property
    def available_methods(self):
        return tuple(s["name"] for s in self.results[self.axis]["shapers"])

    def write(self, results, selections=None, reference=None, reference_at=None, saved_at=None):
        state = self.graph_page.state
        if self.results is not results:
            self.plot.clear_cache()
            self.results, self.show_table = results, False
            self.table_page = None
            self.selections = {}
            axis = next(axis for axis in ("x", "y") if axis in results)
            state.update({ShaperState.AXIS: axis, ShaperState.METHOD: results[axis]["recommended"],
                          ShaperState.SCALE: "linear"})
        self.selections = dict(selections or {})
        result = results[self.axis]
        previous = (reference or {}).get(self.axis)
        reference_label = ""
        if reference_at:
            reference_label = "REF " + time.strftime("%Y-%m-%d %H:%M", time.localtime(reference_at))
            if not previous:
                reference_label += " / NO %s DATA" % self.axis.upper()
        if saved_at:
            reference_label += (" | " if reference_label else "") + "VIEW " + time.strftime(
                "%Y-%m-%d %H:%M", time.localtime(saved_at))
        fit = next(s for s in result["shapers"] if s["name"] == self.method)
        spectrum = spectrum_scale(list(result["psd_sum"]) + (list(previous["psd_sum"]) if previous else []),
                                  state[ShaperState.SCALE])
        state.update({
            ShaperState.REFERENCE: {"freq_bins": previous["freq_bins"], "psd_sum": previous["psd_sum"]}
                                   if previous else {},
            ShaperState.REFERENCE_LABEL: reference_label,
            ShaperState.CAN_SAVE: self.can_save,
            ShaperState.SAVE_LABEL: "SAVE" + (" " + " & ".join(a.upper() for a in sorted(self.selections))
                                             if self.selections else ""),
            ShaperState.IS_SELECTED: self.selections.get(self.axis) == {
                "name": self.method, "freq": round(fit["freq"], 1)},
            ShaperState.RESULT: {"freq_bins": result["freq_bins"], "psd_sum": result["psd_sum"],
                                 "shapers": [{"name": self.method, "response": fit["response"]}]},
            ShaperState.TABLE: comparison_values(result), ShaperState.HAS_ZVD: "zvd" in self.available_methods,
            ShaperState.X_ENABLED: "x" in results, ShaperState.Y_ENABLED: "y" in results,
            ShaperState.METHOD_LABEL: "%s %.1fHz%s" % (self.method.upper(), fit["freq"],
                                                     " [REF]" if not self.can_apply else
                                                     (" *" if self.method == result["recommended"] else "")),
            ShaperState.CAN_APPLY: self.can_apply,
            ShaperState.METRICS: "VIB %.1f%% / SM %.3f" % (fit["vibrs"] * 100., fit["smoothing"]),
            ShaperState.ACCEL: "ACCEL ~%.0f mm/s2" % (round(fit["max_accel"] / 100.) * 100.),
            ShaperState.RECOMMENDED: "* RECOMMENDED / SCV %.1f" % result["scv"],
            ShaperState.SCALE_TITLE: spectrum["title"], ShaperState.TICK_LOW: spectrum["ticks"][0],
            ShaperState.TICK_MIDDLE: spectrum["ticks"][1], ShaperState.TICK_HIGH: spectrum["ticks"][2],
        })
        if self.show_table:
            if self.table_page is None:
                from .compare import create_page
                self.table_page = create_page(self.bounds)
            self.table_page.state.update(state)
        return self.page

    def select(self, action):
        state = self.graph_page.state
        if action.route == "axis" and action.value in self.results:
            state.update({ShaperState.AXIS: action.value,
                          ShaperState.METHOD: self.results[action.value]["recommended"]})
        elif action.route == "method" and action.value in self.available_methods:
            state.update({ShaperState.METHOD: action.value})
        elif action.route == "step" and action.value in ("prev", "next"):
            methods = self.available_methods
            index = (methods.index(self.method) + (1 if action.value == "next" else -1)) % len(methods)
            state.update({ShaperState.METHOD: methods[index]})
        elif action.route == "scale":
            state.update({ShaperState.SCALE: "log" if state[ShaperState.SCALE] == "linear" else "linear"})
        elif action.route == "view" and action.value in ("table", "graph"):
            self.show_table = action.value == "table"
            if self.show_table:
                self.plot.clear_cache()
        else:
            return False
        return True
