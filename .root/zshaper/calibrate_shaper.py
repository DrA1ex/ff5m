#!/usr/bin/env python3
#
# Shaper plot generation script
#
# Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
#
# Based on Klipper's shaper calculation script
#
# Copyright (C) 2020-2024  Dmitry Butyugin <dmbutyugin@google.com>
# Copyright (C) 2020  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import json
import optparse
import os
import sys
import matplotlib
import numpy as np

from textwrap import wrap

MAX_TITLE_LENGTH = 65


######################################################################
# Plot frequency response and suggested input shapers
######################################################################

def plot_freq_response(data):
    calibration_data = data.calibration_data
    max_freq = data.max_freq

    freqs = calibration_data.freq_bins
    psd = calibration_data.psd_sum[freqs <= max_freq]
    px = calibration_data.psd_x[freqs <= max_freq]
    py = calibration_data.psd_y[freqs <= max_freq]
    pz = calibration_data.psd_z[freqs <= max_freq]
    freqs = freqs[freqs <= max_freq]

    fontP = matplotlib.font_manager.FontProperties()
    fontP.set_size('x-small')

    fig, ax = matplotlib.pyplot.subplots()
    ax.set_xlabel('Frequency, Hz')
    ax.set_xlim([0, max_freq])
    ax.set_ylabel('Power spectral density')

    ax.plot(freqs, psd, label='X+Y+Z', color='purple')
    ax.plot(freqs, px, label='X', color='red')
    ax.plot(freqs, py, label='Y', color='green')
    ax.plot(freqs, pz, label='Z', color='blue')

    title = "Frequency response and shapers (%s, scv: %0.f)" % (data.axis, data.scv)
    ax.set_title("\n".join(wrap(title, MAX_TITLE_LENGTH)))
    ax.xaxis.set_minor_locator(matplotlib.ticker.MultipleLocator(5))
    ax.yaxis.set_minor_locator(matplotlib.ticker.AutoMinorLocator())
    ax.ticklabel_format(axis='y', style='scientific', scilimits=(0, 0))
    ax.grid(which='major', color='grey')
    ax.grid(which='minor', color='lightgrey')

    ax2 = ax.twinx()
    ax2.set_ylabel('Shaper vibration reduction (ratio)')
    best_shaper_vals = None
    for shaper in data.shapers:
        label = "%s (%.1f Hz, vibr=%.1f%%, sm~=%.2f, accel<=%.f)" % (
            shaper.name.upper(), shaper.freq,
            shaper.vibrs * 100., shaper.smoothing,
            round(shaper.max_accel / 100.) * 100.)
        linestyle = 'dotted'
        if shaper.name == data.best_shaper:
            linestyle = 'dashdot'
            best_shaper_vals = shaper.vals
        ax2.plot(freqs, shaper.vals, label=label, linestyle=linestyle)
    ax.plot(freqs, psd * best_shaper_vals,
            label='After\nshaper', color='cyan')
    # A hack to add a human-readable shaper recommendation to legend
    ax2.plot([], [], ' ',
             label="Recommended shaper: %s" % (data.best_shaper.upper()))

    ax.legend(loc='upper left', prop=fontP)
    ax2.legend(loc='upper right', prop=fontP)

    fig.tight_layout()
    return fig


######################################################################
# Startup
######################################################################


def setup_matplotlib():
    global matplotlib

    matplotlib.rcParams.update({'figure.autolayout': True})
    matplotlib.use('Agg')

    import matplotlib.pyplot, matplotlib.dates, matplotlib.font_manager
    import matplotlib.ticker


def load_shapers(filename):
    with open(filename, "r") as f:
        return plot_data(json.load(f))


def plot_data(data):
    calibration_data = lambda: None
    calibration_data.freq_bins = np.array(data["calibration_data"]["freq_bins"])
    calibration_data.psd_sum = np.array(data["calibration_data"]["psd_sum"])
    calibration_data.psd_z = np.array(data["calibration_data"]["psd_z"])

    # Switch psd_x and psd_y since FF AD5M calibrate with messed up parameters
    calibration_data.psd_x = np.array(data["calibration_data"]["psd_y"])
    calibration_data.psd_y = np.array(data["calibration_data"]["psd_x"])

    best_shaper = data["best_shaper"]

    shapers = list()
    for shaper in data["all_shapers"]:
        res = lambda: None
        res.name = shaper["name"]
        res.freq = shaper["freq"]
        res.vibrs = shaper["vibrs"]
        res.smoothing = shaper["smoothing"]
        res.max_accel = shaper["max_accel"]
        res.vals = np.array(shaper["vals"])
        res.score = shaper["score"]

        shapers.append(res)

    scv = data['scv']
    axis = data["axis"].upper()

    result = lambda: None
    result.calibration_data = calibration_data
    result.shapers = shapers
    result.best_shaper = best_shaper
    result.scv = scv
    result.axis = axis
    result.max_freq = 200

    return result


def history_graphs(directory, result_id, axes, output):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, os.path.join(root, ".py", "klipper", "plugins"))
    from shaper_storage import ResultHistory
    results, saved_at = ResultHistory(directory).load(result_id)
    if axes not in ("x", "y", "xy") or any(axis not in results for axis in axes):
        raise ValueError("Requested axes are not present in this shaper result")
    for axis in axes:
        result = results[axis]
        data = dict(axis=axis, scv=result["scv"], calibration_data=result, best_shaper=result["recommended"],
                    all_shapers=[dict(fit, vals=fit["response"]) for fit in result["shapers"]
                                 if not fit.get("reference_only", False)])
        metadata = {"Description": "Forge-X shaper result %s; saved_at=%.6f; axis=%s" % (result_id, saved_at, axis)}
        yield os.path.join(output, axis + ".png"), plot_data(data), metadata


def main():
    usage = "%prog -d <json-data_path> -o <output_image_path>"
    opts = optparse.OptionParser(usage)
    opts.add_option("-o", "--output", type="string", dest="output",
                    help="filename of output graph")
    opts.add_option("-d", "--data", type="string", dest="data",
                    help="filename of json file with precalculated shaper data")
    opts.add_option("--history", action="store_true", help="export binary history: DIRECTORY RESULT_ID AXES OUTPUT_DIR")
    options, args = opts.parse_args()

    if options.history:
        if len(args) != 4:
            opts.error("--history requires DIRECTORY RESULT_ID AXES OUTPUT_DIR")
        graphs = history_graphs(*args)
    else:
        if not options.data or not os.path.isfile(options.data):
            opts.error("Input JSON file does not exist")
        graphs = [(options.output, load_shapers(options.data), {})]
    setup_matplotlib()
    for output, result, metadata in graphs:
        fig = plot_freq_response(result)
        try:
            fig.set_size_inches(8, 6)
            fig.savefig(output, metadata=metadata)
        finally:
            matplotlib.pyplot.close(fig)


if __name__ == '__main__':
    main()
