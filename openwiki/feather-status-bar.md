# Feather status bar telemetry

The compact 32 px status bar displays nozzle and bed temperatures, total CPU
usage, available RAM, the network address, and print state. It uses JetBrains
Mono 8pt, middle-dot separators, and the active theme's existing temperature
roles and palette tokens. FF5M owns this product presentation in
`ff5m_ui/status_bar.py`; the shared framework and Designer contracts are unchanged.

## Layout selection and preview

The `feather_status_bar` mod parameter lists `METRICS` (default), `COLORFUL`,
and `VANILLA` in that order under Print & Interface. Metrics is the compact
six-field layout, arranged as three columns. Temperatures and network/state
take their content widths at the left and right edges. CPU and RAM center in
the middle column, which fills the remaining space. Both values stay unpadded;
changes in their widths do not move the outer groups. The shared `ui.layout.split`
primitive allocates the columns without constructing a component tree per update.
It loads lazily with the existing UI modules, preserving the cold-start import contract.
Colorful retains full nozzle/bed labels and left/right groups, uses theme colors,
and replaces pipes with middle dots. Vanilla keeps the previous single-color
labels and pipe separators.

In Metrics, nozzle and bed use the theme's `temperature_nozzle` and
`temperature_bed` roles. CPU and RAM use `text`, the IP address uses `success`,
and the print state uses `primary`. Each theme supplies its own colors; no new
palette entry is required. Colorful retains its secondary/warning temperatures,
ordinary text color for IP, and primary accent for print state. It has one middle dot
between nozzle and bed and one between IP and state; the central space between
the left and right groups has no separator. Metrics also uses middle dots within
each pair, leaving the space between its three groups clear.

`mod_params` owns the persisted mode. The existing `mod_params:changed` event
refreshes the footer immediately after a committed setting or parameter reload;
it does not run a full status tick or create another timer. Reading the current
print label is shared with print-state reconciliation but performs no state
transition itself.

While its option picker is open, the settings feature's existing selected
option is the effective preview mode. Selection immediately refreshes the
footer, and periodic telemetry uses that same computed preview. No second
mode variable or saved original is retained. Save commits through the normal
parameter API. Cancel, Back, and feature deactivation render the latest
persisted mode; a failed save leaves the preview cancelable. Other parameter
edits have their existing behavior. A modal keeps footer painting suppressed
until the canonical close path restores it.

## Ownership and update flow

`CpuUsageSampler` is a periodic service on the existing `FileWorker`. Once per
second it reads only the aggregate CPU line from `/proc/stat`. The worker owns
the previous counters and publishes one immutable `(sample_time, percent)`
snapshot. Reactor readers copy that reference without acquiring a worker lock,
performing I/O, or submitting another task. This follows the CPython runtime's
reference publication semantics. No new thread, process, dependency, or callback
queue is introduced.

Worker scheduling uses the worker's `time.monotonic()` clock. Snapshot
timestamps use the injected reactor clock, because Klipper may use
`CLOCK_MONOTONIC_RAW`. Freshness checks must never subtract different clocks.

CPU is calculated from the change in busy ticks divided by the change in total
ticks. Idle includes `idle` and `iowait`; guest counters are excluded from the
total because user/nice already include them. Aggregate counters include every
core, so one fully occupied core out of two yields 50%, and both yield 100%.
There is no fixed two-core divisor. Invalid counter deltas show an unknown
value and establish a new baseline.

The existing one-second Feather update reads the cached CPU percentage and
`system_stats.get_status()`'s existing `memavail`. Klipper remains the owner of
available-memory collection; Feather does not add another memory-file read.
RAM is displayed as whole MiB under the compact `MB` label, rounded to nearest
with exact halves down: 1.5 becomes 1, and 1.6 becomes 2.

## Failure, recovery, and lifecycle

CPU shows `--%` before a second valid sample, after a read failure, after an
invalid delta, or when the last sample is more than three seconds old. File
tasks may delay sampling; the UI never waits for them. A read failure clears
the baseline, and ordinary later polls recover it. Closing the worker closes
the service; screen deactivation drops its reference before stopping the
worker. Available RAM follows Klipper's existing cached-memory behavior;
missing or invalid data displays `--MB`.

The product renderer specializes footer drawing while retaining the inherited
footer cache, modal suppression, frame restoration, and queue admission. The
formatter's four left fields and two right fields are separated by ` · `;
the renderer paints those six display fields in their theme colors. Colorful
uses two left fields and two right fields with the original anchored groups. Legacy
two-string footer values retain the shared renderer's original presentation.
All field, separator, background, and frame commands are submitted as one
`state` batch with the `footer` key. Unchanged rounded values submit nothing.

## Scope and verification

The concrete problem is persistent CPU/RAM visibility without I/O or waiting
in the reactor. Sampling and display formatting have separate owners; the
only retained telemetry is the worker's baseline and latest snapshot. Existing
page and dialog lifecycles, temperature refresh, and renderer transport are
preserved. Per-process CPU, histories, graphs, new daemons, framework contract
changes, and printer deployment are outside this change.

Behavioral tests cover aggregate two-core normalization, old and invalid proc
input, stale samples, recovery, worker shutdown, reads during blocked worker
I/O, memory rounding, one-batch rendering, theme colors, maximum field widths,
cached unchanged values, footer restoration, content-sized side columns, and
centered metrics across changes in CPU, RAM, temperature, IP and state widths.
Mode tests cover parameter validation and persistence, immediate commit events, live option preview,
periodic refresh during preview, cancel/back/deactivation, failed save, and
an external persisted change while previewing. Local timings describe the
host only; a strict printer-side latency bound requires measurement on that
device with explicit permission.

## Local layout timing

The three-column layout was compared with the original status-bar renderer
(`a3322761`) on the development Mac with the repository's Python 3.14 virtual
environment. Each scenario used 5,000 samples after 200 warm-up calls, with GC
disabled during measurement. Timings include `footer()` command preparation
and queue admission; the encoding scenario also serializes the submitted batch.
No worker, display transport, or printer was involved. Values and fonts were
identical between versions, and unchanged calls exercised the normal footer cache.

| Scenario | Before median / p95, us | After median / p95, us |
| --- | ---: | ---: |
| First footer on a fresh renderer | 27.417 / 47.167 | 27.375 / 43.667 |
| Updates with unchanged text widths | 16.167 / 16.667 | 16.041 / 16.625 |
| Updates crossing text-width boundaries | 16.333 / 16.958 | 16.167 / 16.750 |
| Changed updates including frame encoding | 19.250 / 19.875 | 18.958 / 19.792 |
| Cached unchanged footer | 0.083 / 0.125 | 0.083 / 0.125 |

Repeated update medians differed by less than 2%, with no observed preparation
slowdown. The first-footer scenario uses a fresh renderer with process-wide
modules already loaded. These host measurements do not establish cold-process
startup time or printer-side rendering latency.
