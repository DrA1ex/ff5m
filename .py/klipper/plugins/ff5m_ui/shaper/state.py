# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
# This file may be distributed under the terms of the GNU GPLv3 license.

from ui.bindings import state
from ui.identity import StateKey

APPLICABLE_METHODS = ("zv", "mzv", "ei", "2hump_ei", "3hump_ei")
METHODS = APPLICABLE_METHODS + ("zvd",)


class ShaperState(StateKey):
    __key_namespace__ = "ff5m_ui.shaper.results"
    AXIS = state(str, default="x", choices=("x", "y"))
    METHOD = state(str, default="mzv", choices=METHODS)
    METHOD_LABEL = state(str, default='MZV 63.4Hz *')
    METRICS = state(str, default='VIB 2.3% / SM 0.062')
    ACCEL = state(str, default='ACCEL ~11800 mm/s2')
    RECOMMENDED = state(str, default='* RECOMMENDED / SCV 9.0')
    SCALE = state(str, default="linear", choices=("linear", "log"))
    SCALE_TITLE = state(str, default='CAL PSD x1e4')
    TICK_LOW = state(str, default='0')
    TICK_MIDDLE = state(str, default='0.75')
    TICK_HIGH = state(str, default='1.5')
    CAN_SAVE = state(bool, default=False)
    SAVE_LABEL = state(str, default="SAVE")
    IS_SELECTED = state(bool, default=False)
    CAN_APPLY = state(bool, default=True)
    REFERENCE = state(dict, default={})
    REFERENCE_LABEL = state(str, default="")
    X_ENABLED = state(bool, default=True)
    Y_ENABLED = state(bool, default=True)
    RESULT = state(dict, default={
        "freq_bins": [0., 20., 40., 60., 80., 100., 150., 200.],
        "psd_sum": [0., 80., 300., 14000., 1600., 80., 30., 20.],
        "recommended": "mzv", "scv": 9.,
        "shapers": [dict(name=name, freq=63.4, vibrs=.023, smoothing=.062,
                        max_accel=11800., response=[1., .9, .6, .06, .15, .3, .7, .9])
                    for name in METHODS],
    })
    HAS_ZVD = state(bool, default=True)
    TABLE = state(dict, default={name: dict(name=name.upper() + (" [REF]" if name == "zvd" else ""),
                                            freq="63.4", vibrs="2.3", smoothing="0.062", max_accel="11800")
                                 for name in METHODS})


def comparison_values(result):
    """UI cells contain formatted metrics, never spectra copied once per cell."""
    return {s["name"]: dict(
        name=s["name"].upper() + (" [REF]" if s["name"] not in APPLICABLE_METHODS else
                                  (" *" if s["name"] == result["recommended"] else "")),
        freq="%.1f" % s["freq"], vibrs="%.1f" % (s["vibrs"] * 100),
        smoothing="%.3f" % s["smoothing"], max_accel="%.0f" % (round(s["max_accel"] / 100) * 100))
        for s in result["shapers"]}
