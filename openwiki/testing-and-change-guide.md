# Testing and change guide

## Validation posture

The [user Klipper package tests](../tests/test_klipper_plugins.py) exercise optional
components, 50-package batching, actual config parsing, collisions, patch priority,
file deletion and complete uninstall. They also execute config repair and stock
restoration with hardware operations stubbed, checking that a missing
`plugins.cfg` exists before its include is installed and that stock preparation
clears it even when display restoration fails. Write-error regressions check
that optional plugin preparation cannot block the core config batch or stock
display restoration. A repeated boot with unchanged packages must not write to
the filesystem, and simulated power loss after every switch step must keep modules
present and converge. Focused plugin checks use `.venv/bin/python -m unittest tests.test_klipper_plugins`. See [User Klipper plugins](workflows/user-klipper-plugins.md).

The repository now contains small host-side tests for Feather utility/state helpers and the C++ interactive hitbox layer. They validate parsing and pure logic but cannot emulate framebuffer, touchscreen, boot, network, or physical motion. The effective validation model remains **focused host tests plus controlled on-device testing**.

Create a local virtual environment and install the host-test dependency before
running the suite:

```bash
python3 -m venv .venv
.venv/bin/pip install -r tests/requirements.txt
.venv/bin/python -m unittest discover -s tests
```

The G-code macro tests use the same Jinja delimiters and config-comment rules
as Klipper. They render macros against explicit status snapshots and inspect
the resulting G-code. They do not emulate motion, heaters, or the MCU.

The bed mesh validation tests in `tests/test_bed_mesh_validation.py` execute
the shared templates with Klipper's shipped `GCodeMove` and the current
`mod_params` persistence implementation. They cover actions, signed averages,
ADJUST/REPLACE, rejection of changed bed shape and oversized offsets, probe-error
state preservation, canonical recalibration, repeated checks, cleanup with
validation disabled, and safe Z travel. Macro tests cover that Z-offset changes from any
client are not saved while compensation is active and are undone when it ends,
and that a print aborted without `_STOP` is restored by the next print. Feather
tests also cover transitive setting dependencies, the disabled Live Z Save
during compensation, and the following print without duplicate compensation. Recalibration breadcrumbs are included in the
visual context fixtures and checked against the print status text bounds. Recovery tests cover checkpoint persistence
and restoration of the current offset. These host tests do not simulate load-cell
accuracy or mechanics.

```bash
.venv/bin/python -m unittest tests.test_bed_mesh_validation tests.test_gcode_macros tests.test_resurrection tests.test_feather_screen tests.test_feather_screen_controller tests.test_feather_workflows tests.test_operation_context
```

## Interactive renderer benchmarks

On an idle Feather screen, tap the **Settings** title five times within two
seconds to open the existing render-command benchmark. Tap the **MAIN MENU**
title five times within two seconds to open the component benchmark. Back
returns to the page that opened each benchmark. Neither benchmark starts a
printer operation, and both stop if the print state ceases to be idle.

The component benchmark changes a card width from 300 to 510 pixels on every
frame. Wrapped text changes height, buttons change columns and stretch to fill
the available row while keeping a minimum width, and the sample
rows advance. Each frame runs the page's normal component update and full
layout pass. The **PYTHON** metric includes state updates, layout, and command
construction; frame median and p95 measure submission to render receipt, and
**COMMIT FPS** counts received frame receipts in the last second. The first
30 frames warm the benchmark before samples enter the rolling window.

The two benchmarks share receipt timing and stop/restart behavior, but their
results represent different workloads and should be compared within each page.

## Feather on-printer regression runner

Feather provides a hidden, opt-in G-code harness whose implementation remains a
cold lazy feature until a run actually starts. It is Feather-only; Headless has
no display plugin and does not register the command.

After deploying a changed Feather Python module, use a hard Klipper process
restart before testing. A normal Klipper `RESTART` rebuilds printer state in the
existing interpreter and can retain an already imported module in `sys.modules`.
On the printer, the explicit process restart is:

```sh
/opt/config/mod/.shell/restart_klipper.sh --hard
```

Run the complete unattended trace on an observed, idle printer with an empty
bed:

```gcode
_FEATHER_UI_TEST ACTION=RUN SUITE=FULL CONFIRM=1
```

`FULL` runs safe page traversal, render-worker restart/recovery, Home All plus reversible 1 mm XYZ moves,
material preheat/cooldown, `BED_LEVEL_SCREWS_TUNE CLEAN=0`, an unsaved
`AUTO_FULL_BED_LEVEL`, and a simulated center-zone Z-offset paper test. The Z
test uses the actual `PROBE`, ten 0.100 mm `FARTHER` actions to create 1 mm of
additional clearance, ten matching `CLOSER` actions, temporary Accept, and
mandatory Discard. It never saves the candidate. PID, input-shaper, and
extruder calibration are intentionally outside the physical suite.

Each hardware/UI phase is also independently runnable with `SUITE=UI`,
`RENDER`, `MOTION`, `HEAT`, `SCREWS`, `MESH`, or `Z`; pass an active material name through
`MATERIAL=PETG` when needed. Use these control commands while diagnosing a run:

```gcode
_FEATHER_UI_TEST ACTION=STATUS
_FEATHER_UI_TEST ACTION=ABORT
```

Every normal runner phase is also enclosed by an operation-context fixture.
The fixture records every `_changed` transition from the live
`operation_context` instance, removes only dynamic IDs/revisions, and compares
the complete ordered semantic trace. `SCREWS`, `MESH`, and `Z` validate their
expected contexts; the remaining phases validate that they create no context
and that no stack leaks into the next phase. Temperature waits have finite
`HEATING`, `COOLING`, and already-in-range variants, each of which is still an
exact full-trace comparison. A mismatch stops later physical steps but does not
skip cleanup.

Two deliberately separate, attended physical groups exercise workflows that
are too intrusive for `FULL`:

```gcode
_FEATHER_UI_TEST ACTION=RUN SUITE=CONTEXT_PRINT CONFIRM=2
_FEATHER_UI_TEST ACTION=RUN SUITE=CONTEXT_MATERIAL MATERIAL=PLA CONFIRM=2
```

`CONTEXT_PRINT` creates two uniquely named temporary virtual-SD files and runs
them in a fixed order dictated by physical state. The first forces KAMP with
nested nozzle cleaning and deposits no part. The second uses a loaded `auto`
mesh, runs mesh validation, and prints a fixed OrcaSlicer fixture: a 16 x 16 x
3 mm open box with three bottom layers, two walls, no sparse infill, and no
top. It exercises the UI pause/resume controls, then reaches a G-code `PAUSE`
after depositing layer 7 of 15, creates a normal resurrection checkpoint,
cancels the print, dismisses the cancellation dialog, and exercises Restore
without restarting Klipper. Recovery resumes at layer 8 and completes the
remaining model. OrcaSlicer's estimate for the extrusion portion is about two
and a half minutes. The model print is deliberately last: it leaves a part in
the bed centre, so every scenario that probes or wipes the bed must precede it.
Together the two files cover the `print`, `kamp`, `mesh_validation`,
`nozzle_clean`, `resume`, and `recovery` contexts plus the print pause,
resume, cancel, and terminal-dialog controls.

The group also starts a print while the previous timelapse is still being
encoded. Before the first print the runner confirms through the guard's own
`timelapse_render_status` command that Moonraker is idle, then creates
`/tmp/feather-ui-test-timelapse-hold`. While that flag exists,
[`timelapse_ffmpeg.sh`](../.root/timelapse_ffmpeg.sh) holds the render (at
most 10 minutes; `/tmp` is shared with Moonraker's chroot and cleared on
reboot), so the KAMP print's real render stays busy. Starting the second print
must then show `TIMELAPSE_WAIT` with a `busy` status and keep the file held.
The runner releases the flag, and the print must start by itself. At the end
the runner waits for the render to become idle and requires the KAMP print's
video. Timelapse must therefore be enabled; preflight refuses
`CONTEXT_PRINT` otherwise. Cleanup and interrupted-run recovery remove the
flag and every `timelapse_feather-context-*` video, which only runner files
can produce.

`CONTEXT_MATERIAL` drives the normal action-prompt protocol: it selects the
requested material, performs Load, Purge, Unload, and Done, then selects the
same active cold-pull profile and completes a cold pull. The cold-pull macro
keeps one generic action-prompt visible while its existing operation-context
states run; its cancel button requests `_CONTEXT_CANCEL`, so the common
context cancellation path is used by Feather and Fluidd alike. It covers
`filament` and `cold_pull`. The final absence of filament after the cold pull
is expected physical state and is not reversed automatically.

Both extended groups require an observed standby printer, zero heater targets,
inactive virtual SD, no pause state left by an earlier print, empty operation
stack, the required probe/mesh/material profiles, no pre-existing recovery
checkpoint, a prepared empty bed, and prepared filament. `CONFIRM=2` is an explicit acknowledgement of those physical
preconditions; it is intentionally different from the normal suites'
`CONFIRM=1`. Do not run either extended group unattended. No manual G-code is
needed after starting it. `CONTEXT_PRINT` leaves its small printed fixture on
the bed; remove it before any further physical run. `CONTEXT_MATERIAL` in
particular must not follow it with the part still in place: its cold pull
re-homes, travels to the bed centre, and purges 100 mm of filament there, so
with a part in the way it extrudes onto the part at whatever height homing
left.

The physical model is one neutral G-code template beside a test-only material
profile file. The runner resolves the explicit `MATERIAL`, or the printer's
last selected material when the argument is absent, before it creates the run.
The current fixture profiles cover PLA and PETG and supply the OrcaSlicer
temperatures, flow ratio, pressure advance, firmware-retraction values, and fan
limit. An inactive material or a material without an exact complete fixture
profile fails immediately, before artifacts, heating, or motion begin; unlike
the other suites, `CONTEXT_PRINT` never substitutes another active material,
because the fixed model would then be printed with the wrong temperatures,
flow, and tuning. The fixture G-code overwrites pressure advance, firmware
retraction, and flow; the runner captures all three before it creates the files
and replays them during cleanup, so an interrupted run leaves no tuning behind.
A run refuses to start when any of those values cannot be restored.

`SUITE=RENDER` is non-physical: on an observed idle printer it requests a
worker-owned Typer restart, waits for the touch FIFO handoff and surface redraw,
then captures the recovered screen. It does not home, move, or heat the printer.
Every capture records renderer queue/restart diagnostics and waits until all
batches submitted before that capture have rendered or been accounted for; an
unexpected dropped batch fails the step instead of silently photographing an
older stable framebuffer.

`SUITE=UI` is also non-physical. In addition to the normal page traversal it
renders a temporary worst-case home dashboard (maximum temperatures, long
network/job/material strings, full progress and long durations) and captures it
before restoring the live state. It synthetically taps every Home dashboard
action (menu, heat, network, job browser, last job, filament, and move), checks
the concrete destination page after each tap, and returns from every card
through the real Back action. The last-job check reuses the harmless G-code
entry already opened by the file-browser traversal and restores the previous
in-memory last-job fields during cleanup. It neither starts nor modifies that
file. The filament trace renders a synthetic
`130.4 / 250C` heating and `260.4 / 250C` cooling states without changing the
heater target or fan, captures the declarative action layout, follows the real Back hitbox to material selection,
and fails if that navigation changes the live nozzle target. Synthetic taps are resolved from the
renderer’s button, toggle, and declarative action-hitbox registries, so clickable
text/panels are covered without importing development-only page modules into
Feather’s normal startup path.

The same UI suite also walks every visible state of every registered
`operation_context` type. It feeds read-only context snapshots into the real
printing, paused, calibration, filament, and cold-pull render paths, including
the normal nested paths such as Print -> KAMP and Bed Level -> Bed Mesh ->
Nozzle Cleaning. Before the first frame, the suite compares the runtime
registry with the visual matrix; a new or removed context therefore fails the
run until its visible states are deliberately covered. The final
`ui-context-coverage-complete` frame is written only after every context frame
has succeeded, so a truncated artifact cannot satisfy host-side coverage.

`SUITE=COMPONENT` is a second non-physical, cold test path used only by the
extended parity regression. It discovers module-level declarative pages after
the explicitly requested test has started, renders their default typed state
plus bounded additional scenarios through the real Feather renderer/Typer
path, and captures them without dispatching product actions or G-code. An
additional scenario accepts only a discovered page ID and bounded JSON values
for that page's declared typed-state keys; unknown pages, invalid
types/choices, actions, and extra fields are rejected. Runtime read-only values
are injected only into the isolated state snapshot and never written back to
product controllers. Both `UI` and
`COMPONENT` manifests
record the renderer's passive `semantic_page_id`; imperative legacy screens
record no semantic ID.

The bed-screw result page is covered in three distinct ways. Automatic page
discovery renders its Page State example in both Designer and `COMPONENT`.
The checked-in `calibration-screws-adjustments` scenario renders one reference
corner and three adjustments in both paths, protecting the physical
front/rear mapping, side callouts, below-bed nuts, turn arrows, legend, and
Repeat/Done controls. `SUITE=SCREWS` separately captures the result produced by
a real screw calibration; it is a physical test and is not invoked by the
automatic visual regression.

The screw page opens with four example corners in Page State. Each corner has
separate `*_NAME`, `*_DIRECTION` and `*_MINUTES` fields. The ordinary typed
state contract supplies text, choice and numeric controls in Designer; no
page-specific editor is required. Direction choices are empty (no result),
`BASE`, `CW` and `CCW`. Minutes are nonnegative integers: 75 displays as
`01:15`. Preview overrides can change each field independently; reset them to
restore the example. Product `render()` converts Klipper strings once and
replaces all twelve fields, so empty or partial results cannot retain examples.
Malformed turn strings suppress the adjustment rather than display a false zero.

Keep screw-result labels and legend in the ordinary `Text`/`Row`/`Column`
tree, with `StateCase` branches for the reference and adjustment labels.
Do not construct text trees inside a custom component's `draw()`; Designer
cannot select or source-edit those hidden children. Each corner has separate
text declarations so editing one corner does not change all four. The module-level
tree in `ff5m_ui/calibration_screws/page.py` exposes source-editable constructors
without helper factories or artificial zero offsets. State conversion, actions,
and custom bed/hardware drawing live in adjacent modules. Free offsets apply to
Overlay children; use container alignment, spacing, and margins in rows/columns.

Designer owns the portable Review/Apply regression for adding and removing a
component offset, reopening the page, and editing neighboring text values
independently. Run
`.venv/bin/python -m unittest tests.test_new_framework_authoring` in the
Feather UI Designer checkout. The test builds a temporary generic project and
does not depend on FF5M page names or repository structure.

Designer capture does not prove that a frame can enter the printer's bounded
render queue. For command-heavy illustrations, also submit the complete frame
(including its header and controls) through the real `FeatherRenderer.send()`
in a local test and assert acceptance. The screw-result test covers this with
and without a reference corner. A rejected oversized frame leaves the previous
screen visible even though the calibration itself has finished.

The screw page's schematic nut and clockwise/counterclockwise arrows
are small raw FXI1 resources in `ff5m_ui/assets/calibration/`. Regenerate them locally with
`.venv/bin/python -m tests.visual_checks.generate_screw_icons`. The generator
owns the geometry and precomputes horizontal reflection. Runtime caches only
the three immutable source blobs and replaces their palette before sending
them with the existing binary `image` command. Nut palette slots are
transparent/bright/dim/panel;
arrow slots are transparent/current accent.
No decoded image or per-theme cache is retained in Python. Designer decodes
the same payload through its generic image renderer. Visual review must
include a theme change, BASE without an arrow, and both turn directions.
Both host and printer UI fingerprints include `.fxi1` resources.

The harness validates live page generations and hitboxes before synthetic taps,
blocks physical non-emergency input plus every persistent Save action during a
run, and stops later hardware phases on the first unsafe failure. Before a new
run it consumes `/data/feather-ui-tests/active.json`; same-Klipper-session
recovery turns off heaters, restores the recorded runtime Z-offset and named
mesh state, and disables motors. A changed session treats volatile state as
already cleared by Klipper restart and marks the previous trace interrupted.

Artifacts live under `/data/feather-ui-tests/<timestamp>-<suite>/`: top-down
32-bit BMP files, `manifest.json`, `environment.json`, `run.log`, a sliced
`printer.log`, `temperatures.csv`, `positions.csv`, and `summary.json` with
calibration stages/results. Before a step capture, the test harness waits for a
Typer `presented` receipt. Framebuffer reads use the active page reported by
`/sys/class/graphics/fb0/pan`, honor the reported stride, and retry if a page
flip occurs during the copy. Stability hashing, BMP encoding, log copying,
CSV/JSON updates, and retention execute in the artifact worker thread. The
reactor only schedules steps and receives completion callbacks.
calibration stages/results. Context-enabled runs also write
`operation_context.json` with each selected fixture variant, exact expected and
actual traces, and scenario result; `summary.json` contains the compact context
result.
Retention keeps at most ten completed runs and 512 MiB, never deletes the active
run, and preserves the newest failure for inspection.

The extended groups temporarily change only runner-owned in-memory branch
fixtures and restore them during cleanup. They do not save configuration. The
runner restores heater targets, part fan, selected material, mesh/runtime Z,
idle timeout, and the patched material persistence hook; it removes only its
own temporary G-code files and checkpoint. Before and after any live group,
independently verify standby, zero heater targets, and inactive virtual SD, and
retain the artifact directory plus deployed UI fingerprint.

Mesh calibration and KAMP write the new mesh into the in-memory `auto` and
`auto_prev` profiles and into Klipper's SAVE_CONFIG buffer even when the UI
answer is DON'T SAVE or LATER. Every run therefore snapshots that buffer, its
pending flags, and the bed-mesh profiles, and restores them during cleanup, so
a later unrelated SAVE_CONFIG cannot persist test data.

#### What a passed run guarantees

A step passing is not enough. The runner also fails a run, with its own
`failures` entry, when:

- `cleanup`: re-observation after restore finds a leaked value: the selected
  material, pending SAVE_CONFIG content, bed-mesh profiles, any mod parameter,
  and, for hardware suites, runtime Z, the active mesh, or heater targets. Mod
  parameters are only verified, never rewritten, because a rewrite would itself
  be persistent.
- `gcode-error`: Klipper sent any `!!` response during the run. Healthy runs
  print one such informational note (the screws "nuts under the bed" note);
  it is listed in `KNOWN_ERROR_NOTES` in `feather_ui_test/runner.py`. A new
  intentional `!!` note must be added there, otherwise use `//`. The
  responses are kept in `summary.json` as `gcode_errors` and logged in
  `run.log` as `GCODE_ERROR`.
- `renderer`: Typer restarted more often than the render scenario requested,
  reported a new worker error, or is not running at the end.
- `printer-log`: the run's own `printer.log` slice contains a Python traceback,
  an MCU shutdown, lost MCU communication, an internal command error, or a TMC
  driver error (`PRINTER_LOG_FAILURE` in `feather_ui_test/artifacts.py`). Such
  lines usually come from background timers or cleanup, which no step
  observes. `summary.json` lists up to 20 of them in `printer_log_failures`.

Scenario checks treat the result page as a destination, not as success: a
`wait` step fails as soon as an error dialog is visible, a screws or mesh
result must carry no calibration error or cancellation, a screws result needs
one entry per configured `screwN` with exactly one base screw, and a test
print is complete only when `print_stats.state` is `complete`.

### Host-orchestrated unattended runs

`tests.printer_regression` is the separate developer-machine entry point for
recording and collecting the real printer suites. It does not replace the
on-printer runner: Feather still owns each scenario, its safety preflight, state
snapshot/restore, semantic screenshots, and suite outcome. The host owns only
suite ordering, camera capture, verified artifact transfer, video assembly, and
the local report.

Run the existing core `FULL` suite from the repository root with:

```bash
python3 -m tests.printer_regression \
  --printer <printer-host> \
  --suite core \
  --confirm-unattended-physical-test
```

Pass several names after `--suite` to build one run in that exact order:

```bash
python3 -m tests.printer_regression \
  --printer <printer-host> \
  --suite ui component core \
  --confirm-unattended-physical-test
```

`--suite` may also be repeated. `all` expands in place to `core`, then `print`.
If a name appears more than once, directly or through `all`, only its first
position is kept. The host performs one preflight, recording lifecycle, report,
and final safety check around the combined ordered run. The host rejects a
physical suite after `print`, because the completed model remains on the bed,
and rejects combining `material` with another physical suite. Non-physical
suite names may still appear anywhere in the requested order.

The host-facing aggregate names deliberately differ from the printer's suite
names:

- `core` starts printer `FULL`;
- `print` starts printer `CONTEXT_PRINT`;
- `material` starts printer `CONTEXT_MATERIAL`;
- `all` runs `core`, then `print`.

That `all` order is a physical constraint, not a preference. `print` must follow
`core` because it leaves a real part in the bed centre and `core` probes the
bed. `material` is deliberately not part of `all`: the suites run back to back
with no operator stop, and `material` re-homes and then purges 100 mm of
filament above the bed centre - onto the part `print` just left there. Run
`material` as its own invocation once the bed has been cleared.

The existing individual `ui`, `component`, `render`, `motion`, `heat`,
`screws`, `mesh`, and `z` suites are also available. `full` is intentionally
not a host-facing alias. Use `--material PLA` only when an explicit profile is
needed; otherwise material selection remains owned by Feather.

The default recording rate is 10 FPS and the accepted range is 1-30 FPS. The
host discovers enabled webcams through Moonraker and records the camera
directly with FFmpeg. Because MJPEG does not provide a reliable media clock,
FFmpeg timestamps incoming frames by their host arrival time before normalizing
the recording to start at zero. A fluctuating or lower-than-declared camera FPS
therefore changes frame cadence without stretching the camera away from the
screen and telemetry timelines. Relative webcam URLs resolve to the printer's
HTTP port 80. Camera recording is required by default: host preflight rejects a
broken FFmpeg installation before contacting the printer, initial camera failure
stops the run before its first suite, and loss of the recorder aborts an active
suite. `--no-camera` is the explicit opt-out for a screen-only run.

The host also requests one coherent Feather framebuffer snapshot every 5
seconds by default. These periodic timeline records complement rather than
replace the settled semantic captures attached to meaningful test states, so
both small screen updates and unchanged holds remain visible in the assembled
video. A periodic snapshot reads the active framebuffer once and stores only
minimal timeline metadata; it does not wait for the UI to become visually
quiet. If the previous periodic snapshot is still pending, the next tick is
skipped instead of building an artifact backlog inside the Klipper process.
A periodic tick is also skipped while the toolhead has queued move time or an
unfinished lookahead, and while the operation context reports a state that
drives the toolhead against the bed: `HOMING`, `PROBING`, `LEVELING`,
`CHECKING MESH`, or `TARING`. Reading 1.5 MiB from the framebuffer during those
states competes with Klipper for the host and can end the run with an MCU
"Timer too close" shutdown. Every accepted semantic, stage, or periodic capture
first writes a `CAPTURE_QUEUED <label> eventtime=<t>` line to `run.log`, so a
post-mortem can distinguish a queued request from one the guard declined. The
reactor counters described below record whether the worker actually started it.
The worker writes each BMP as a small header plus the framebuffer payload and
publishes `manifest.json` once during finalization. It deliberately does not
fsync and rewrite the growing manifest after every periodic frame, because
that storage latency can delay the Klipper reactor during probing.
All reactor callbacks submit artifact work through an unbounded
`SimpleQueue`; only the single artifact thread opens the framebuffer or output
files. Each completed capture is appended immediately, without `fsync`, to
`artifact_timing.csv`. Its queued, worker-started, and worker-finished wall and
monotonic timestamps, queue delay, duration, kind, phase, and page survive a
later Klipper failure and distinguish scheduling delay from framebuffer and
file work. The kind separates the three request paths: `semantic` for a test
step's own capture, `stage` for one the printer's operation context triggered,
and `periodic` for the timeline interval. Stage capture follows every real
operation-context state change regardless of the current test phase. A long
action therefore updates the screen timeline while it remains active, and a
new workflow does not need to be added to a capture whitelist. A stage that
enters one of the same toolhead-against-bed states as the periodic guard is
still recorded in `calibration_stages` and in `run.log` as `STAGE ...
screenshot skipped during <state>`, but its framebuffer is not read: a
settled capture there caused an MCU "Timer too close" shutdown on homing. The
next stage after the motion carries the screenshot.

When Klipper shuts down during a run, Feather deactivates the runner before it
paints the shutdown screen. The runner then finalizes its artifacts as
`aborted` with reason `Klipper shutdown` and issues no G-code: G-code cannot
run, it could wait for the interrupted command's G-code lock and keep the
shutdown screen from appearing, the restart clears volatile state, and the
next run removes the owned files and timelapse flag.
Settled semantic and stage captures wait for a quiet framebuffer by comparing
the interpreter's own `hash()` of each sampled frame, not a digest: SHA-256 is
computed exactly once, on the frame the loop finally accepts. The printer's
Python is built without `zlib`, so `crc32` is unavailable, and hashing 1.5 MiB
per 50 ms sample on the Cortex-A7 is host time taken from Klipper.
Use `--screen-capture-interval <seconds>` to select an interval from 5 to 300
seconds, or `--screen-capture-interval 0` to keep semantic captures only.
Capture remains inside the lazy test runner and its existing background
artifact worker; normal Feather runtime and the printer never encode video.

While a suite is active, the host records `telemetry.jsonl` at 1 Hz by
default. Each timestamped sample contains the live XYZ position and velocity,
homed axes, nozzle and bed temperatures/targets/power, print state and
progress, Feather page/generation, current suite/phase/step, and semantic
operation context. Each sample also records the motion buffer margin
(`toolhead.print_time` minus `estimated_print_time`), Klipper's `stalls`
counter, and the MCU's own `last_stats` load (`mcu_awake`, `mcu_task_avg`,
`bytes_retransmit`, `srtt`). Those are the host-side numbers that describe how
close a run came to a "Timer too close" shutdown, and they survive it because
the recorder flushes every line. A printer that publishes no `[mcu]` status
records a `null` block rather than failing the sample.
`--telemetry-rate <hz>` accepts 1-10 Hz, while
`--telemetry-rate 0` disables collection. Physical suites reject rates above
1 Hz because every query executes object status callbacks in the Klipper
reactor; higher rates remain available for non-physical UI, component, and
render suites. Sampling uses one bounded Moonraker object query per tick, and
that same status snapshot carries the active `run_id`. The active wait does
not poll over SSH: startup, ownership, completion, and abort observation use
Feather's Moonraker status. SSH is used only for bounded lifecycle actions such
as preflight, starting/stopping the independent resource sampler, and artifact
transfer. The configured value is the requested upper rate; the report also
records the effective rate because Klipper/Moonraker status publication may be
slower. An intermittent or unavailable telemetry stream is reported as a
warning and never rewrites the printer suite outcome.
During an active suite, successful RT status samples are also the host's
printer-process heartbeat. If no new sample arrives for 30 seconds, the host
stops waiting on a possibly stale run and records an
infrastructure failure instead of waiting for the suite's physical timeout.

Each launched run publishes its deployed UI fingerprint in the Feather test
status. The host compares it with the local checkout as soon as the run is
observed and aborts it before its first phase on a mismatch, so a report never
describes a build other than the one under review. Sync the printer and run
again; there is no override. After every suite the host also requires
Moonraker's `klippy_state` to be `ready`, because a shut-down Klipper still
reports standby, zero heater targets, and inactive virtual SD.

One bounded printer-side shell process independently samples `/proc` once per
second into `resources.tsv`. It records system CPU ticks, load, available
memory, free swap, and per-process CPU ticks/RSS/state for Klipper, Moonraker,
Dropbear, and Typer. Per-process rows also expose thread count, voluntary and
involuntary context switches, minor and major faults, scheduler runtime/wait
and timeslices, I/O byte and syscall counters, and the current kernel wait
channel. This gives Typer telemetry without importing, tracing, or modifying
Typer itself. The sampler neither imports nor calls Klipper and therefore keeps
recording if the Klipper process or reactor fails. The host owns one persistent
foreground SSH transport for the sampler instead of repeatedly opening
Dropbear sessions, stops that exact local process after the physical suite,
copies the exact remote file, and removes it only after the local file is
verified to carry both the expected header and at least one sample row — a
header-only file means the printer-side pass never produced anything and is
reported as a failure rather than as an idle host.
Preflight, launch ownership, active waiting, and post-suite safety
observation use Moonraker and do not open additional SSH sessions.

Because this sampler observes host overload, its own cost is part of the
measurement. Each iteration therefore spawns one awk pass, one `tr` per Python
process it inspects, and `sleep`: the wall clock is anchored once with
`date` and advanced from the `/proc/uptime` delta, and every value in the row —
system memory and CPU ticks plus each process's `stat`, `status`, `schedstat`,
`io` and `wchan` — is produced by that awk pass in
`.shell/printer_resource_sample.awk`. That awk pass takes its tree from `root`,
so `tests/test_printer_resource_monitor.py` runs it against a `/proc` fixture;
the shell loop itself only gets a syntax check and a guard that no new command
substitution appears inside it. Classification stays in the shell because
busybox awk truncates strings at the NUL separators `/proc/<pid>/cmdline` uses,
which would make every Python process look identical to it.

`--no-resource-monitor` leaves the sampler out entirely and records the report's
resource status as `disabled`. Because the sampler is the only observer that
runs for the whole run — and even in its reduced form it reads the Klipper
process's `status` and `wchan` every second — excluding it is how a host
overload investigation isolates its cost from the capture and telemetry paths.

While the hidden test runner is active, a temporary 5 Hz reactor timer measures
its own scheduling lag. It aggregates one row per second into `reactor.csv`
through the existing artifact worker: average/max lag, longest callback
interval, exact eventtimes for both maxima, current phase/step, pending capture
state, sample count, and missed 200 ms deadlines. Each row also carries the
worker's cumulative `captures_queued`, `captures_started`, and
`captures_finished` counts. `queued > started` means a request was still
waiting in the artifact queue; `started > finished` means framebuffer work had
actually begun while the reactor was late. This is evidence
`artifact_timing.csv` cannot give for an interrupted capture, because its row
is written only after the capture returns. The timer is owned by the single
`UITestRun`, returns an explicit next wake time, and is unregistered on
completion, setup failure, or deactivation. It is not active during normal
Feather operation.

The inspection video uses a fixed 800x1080 vertical canvas: the native
800x480 Feather screen is above the printer camera, the complete camera image
is fitted without cropping into an 800x450 region, and an 800x150 RT panel is
below it. The panel is rendered from the timestamped JSONL timeline through a
raw in-memory bitmap stream; the host does not retain one uncompressed image
per telemetry sample. Its five compact rows show test/step, UI and printer
state, integer motion/heater values, operation context, and time-aligned
Klipper/Typer/Dropbear CPU plus RSS, available memory, and load from
`resources.tsv`. Screen transitions use printer-relative capture times
anchored only after the lazy runner is observed active; setup latency therefore
cannot advance the screen track ahead of the continuously recorded camera. Each
manifest timestamp describes when the artifact worker accepted the framebuffer,
not when the reactor queued the request. The screen area remains neutral until
the first observed frame instead of showing that future frame from time zero. A
missing screen or camera gets an explicit placeholder so the layout and timeline
remain stable.

Before any mutable printer command, the entry point verifies local FFmpeg,
SSH, SCP, artifact-root write access, disk capacity, Moonraker/Klipper
readiness, the Feather screen object, SSH access, idle print state, zero heater
targets, inactive virtual SD, and absence of another active UI-test run. The
confirmation option acknowledges an observed idle printer, a prepared empty
bed, and unattended motion/heating; it is translated into Feather's existing
`CONFIRM=1` or `CONFIRM=2` contract rather than bypassing it.

Each run gets a unique ignored directory under
`tests/artifacts/printer-runs/` unless `--output` selects another new directory.
It contains `report.html`, `report.json`, `recording.mp4` when media succeeds,
`telemetry.jsonl`, `resources.tsv`, and verified printer artifacts below
`suites/`. Each printer artifact can also contain `reactor.csv`. Large `work/`
intermediates are
removed only after successful media finalization and retained on failure.

`report.html` is the entry page of a multi-page offline review written by
`tests/printer_report.py`. The pages are rebuilt from the artifacts on disk
(`tests/printer_run_data.py` reads them; nothing contacts the printer), so any
earlier run directory can be regenerated with the newest report:

```bash
.venv/bin/python -m tests.printer_report tests/artifacts/printer-runs/<run>
```

Each page serves one review aspect:

- **Overview** is a dashboard that answers "did it pass, and is anything off?"
  without scrolling through detail. It shows the verdict, a **Needs attention**
  list, a per-test results table, and a **Health** scorecard. Every finding
  carries separate colored buttons into the recording, the last screen captured
  before it, the step, the log line, or the relevant chart. Failed steps,
  scenarios, and captures, renderer errors, and real log errors are always
  flagged. Performance numbers (reactor
  lag, late reactor ticks, capture time and queue delay, Typer restarts,
  telemetry gaps, MCU round trip, lowest free memory, run time) are judged
  against the same measurements from earlier **passing** runs of the same
  suites in the same artifact directory: a number is "unusual" only when it
  exceeds everything seen before and is more than 1.5 times the typical
  (median) value, and is shown without a verdict until three such runs exist.
  This matters because a healthy run is noisy; the render test restarts Typer
  on purpose and a few late reactor ticks are normal. The policy and thresholds
  live in `tests/printer_analysis.py`. A late reactor tick is a second in which
  the 5 Hz test timer missed a 200 ms deadline; `reactor.csv` stores that count
  per second, not cumulatively. Each phase row pairs its steps with its
  operation-context scenario; a scenario that matches no phase gets its own row.
- **Steps** lists every `STEP_START` with phase, kind, recording time,
  duration, and status, plus a per-phase summary and the slowest steps. Filter
  by suite, status, kind, phase, or text.
- **Screens** is the capture gallery with failure markers. Similar screens are
  grouped into collapsible cards (one per suite, phase, and purpose: periodic
  captures, operation stages, or a single page), with a preview strip while a
  card is closed; groups with a failure start open, and "All screens" switches
  to the flat grid. Active filters reveal matches inside closed groups. The
  modal shows the
  screen with optional touch-area overlays (Klipper-side hitboxes and button
  rectangles), position, temperatures, capture duration and queue delay, and
  the renderer queue and worker state recorded with the frame.
- **Telemetry** charts temperatures, heater power, position, velocity, motion
  buffer margin and stalls, and MCU round-trip time, load, and retransmits from
  `telemetry.jsonl`. Charts share test-phase bands, failure markers, and one
  hover cursor.
- **Performance** charts the harness (reactor lag and missed deadlines, capture
  duration and queue delay, render queue and dropped batches) and the printer
  system (load, memory, per-process CPU and RSS from `resources.tsv`; CPU
  assumes 100 clock ticks per second), with min/mean/p95/max statistics.
- **Checks** shows each operation-context scenario and, for a mismatch, a
  field-level diff of the first differing snapshot. It also shows the measured
  bed mesh heat map, Z-calibration values, and the operation-stage timeline.
- **Logs** shows the host run log and the printer log per suite, filtered by
  default to key lines; steps, captures, telemetry samples, and commands are one
  click away. Step, capture, and sample lines are never classified as errors
  from their labels.
- **Run** holds the recording (steps link to `report-run.html#t=SECONDS`),
  camera, telemetry, and resource status, per-suite environment, and every
  artifact file.

Every artifact is optional. A missing or malformed file is listed in the
overview instead of failing the report, and a page that cannot be built is
replaced by an explanation so the other pages and `report.json` stay usable.
All times share one axis, "recording seconds": printer epoch and monotonic
times are mapped through each suite's `timeline_start_seconds`. Seeking the
recording from a report opened over `file://` works in browsers; a web server
that serves it must support HTTP range requests.

For every launched suite the host observes the exact `run_id` and directory
from Feather's Moonraker status, copies that exact directory, verifies
`summary.json` ownership
and the screenshot/manifest contract, and only then removes the same remote
directory. It never cleans the printer artifact root globally. A copy or
verification failure preserves the remote source. If Klipper disappears before
finalization, the host attempts a non-destructive partial copy so `run.log`,
`reactor.csv`, screenshots, and other already-flushed evidence remain attached
to the infrastructure failure.

An ordinary printer test failure is reported and later selected suites still
run after host safety is re-observed. Lost connectivity, changed run ownership,
runner timeout/stall, cleanup failure, active print, non-zero heater target, or
active virtual SD stops later physical suites and records them as skipped. A
timeout uses Feather's existing explicit abort command; no service restart or
generic recovery ladder is attempted.

Normal host tests substitute Moonraker, SSH/SCP, clocks, camera processes, and
FFmpeg. They never require or contact a live printer:

```bash
python3 -m unittest tests.test_printer_regression
```

## Development-only semantic screenshot checks

Semantic screenshot review is a separate Mac host-side tool under
`tests/visual_checks/`. It is not imported, registered, configured, or executed
by Feather, Klipper, printer startup, or the on-printer UI test runner.
`sync.sh` excludes the complete `tests/` directory, so the checker is not
included in printer synchronization archives. It uses only Python's standard
library and adds no project, build, or printer dependency.

The tool is disabled unless the developer explicitly passes `--enable`. It
accepts either an already downloaded UI-test artifact directory (using its
`manifest.json`) or explicit BMP/PNG/JPEG/WebP files. Select `--backend codex`
to review them through the installed Codex CLI, or `--backend openai-compatible`
to use a local vision LLM through `/models` and `/chat/completions`. Without an
explicit backend, a configured base URL selects the HTTP backend; otherwise
Codex is selected. There is no model download or service management.
Feather's uncompressed 24/32-bit BMP frames are converted in memory to PNG
with the Python standard library before submission because some compatible
servers reject BMP vision input. The saved source artifact, its hash, and its
byte count remain unchanged. Both backends use the same prompts and JSON
schemas, and responses are independently validated again by the checker.

The Codex backend defaults to `gpt-6-luna` with reasoning effort `high`. It
starts `codex exec` in a temporary directory, attaches screenshots with
`--image`, supplies `--output-schema`, and reads the JSON final answer from
`--output-last-message`. Each standalone frame has a dedicated spacing audit
and a full review; parity audits each image independently before the paired
review. Codex uses its existing authentication; install the CLI and complete
`codex login` before enabling this backend. The CLI must support
`--ignore-user-config` and `--ephemeral` (check `codex exec --help`). The review
uses a read-only sandbox, disables approval prompts and project instructions,
and ignores user configuration while retaining Codex authentication. It asks
the model to inspect only attached images. CLI logs are discarded, temporary
files are removed, and a timed-out or cancelled child process is stopped and
reaped. Screenshots selected for Codex review are sent to the external model
service.

Codex's `app-server` uses JSON-RPC rather than the HTTP Chat Completions
contract. It cannot be substituted as the HTTP base URL without a separate
protocol adapter. See the [official app-server documentation](https://learn.chatgpt.com/docs/app-server)
and [CLI reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli).

Connection settings are host-local:

- `--backend` / `FF5M_VISUAL_BACKEND` selects `codex` or `openai-compatible`;
- `--base-url` / `FF5M_VISUAL_BASE_URL` supplies the HTTP base URL;
- `--model` / `FF5M_VISUAL_MODEL` selects one model (required for HTTP);
- `--reasoning-effort` / `FF5M_VISUAL_REASONING_EFFORT` overrides reasoning
  effort (`high` for Codex; omitted for HTTP unless explicitly supplied);
- `--codex-command` / `FF5M_VISUAL_CODEX_COMMAND` selects the CLI executable
  path (default `codex`);
- `--review-workers` / `FF5M_VISUAL_REVIEW_WORKERS` selects concurrent
  screenshot reviews (1–32; default 8 for Codex and 1 for HTTP). Use
  `--review-workers 1` for sequential review. This is independent of
  `--designer-workers`, which controls screenshot capture;
- `FF5M_VISUAL_API_KEY` is optional for HTTP and is never logged or serialized;
- `--timeout` / `FF5M_VISUAL_TIMEOUT` bounds each HTTP request or CLI invocation
  (default 30 seconds for HTTP, 180 for Codex; allowed range 1–300);
- `--mode advisory|strict` / `FF5M_VISUAL_MODE` select enforcement in the
  image-only command; the regression command uses `--check-mode`.

For a saved screenshot corpus with Codex:

```bash
.venv/bin/python -m tests.visual_checks.run \
  /path/to/saved-ui-test-artifacts \
  --enable --backend codex \
  --model gpt-6-luna --reasoning-effort high \
  --mode advisory
```

For a local OpenAI-compatible vision LLM:

```bash
.venv/bin/python -m tests.visual_checks.run \
  /path/to/saved-ui-test-artifacts \
  --enable --backend openai-compatible \
  --base-url http://127.0.0.1:1234/v1 \
  --model loaded-vision-model \
  --mode advisory
```

The generated `visual-checks.json` keeps, for every screenshot and its selected model, the
verdict, non-pass reasons, JSON-validation status, elapsed request time, and
normalized errors. Configuration records the backend, model, reasoning
effort, and review worker count. Reviews run concurrently up to the selected
limit, while each screenshot's spacing audit and verdict remain sequential.
The report retains input order even when reviews finish in a different order.
`summary.review_elapsed_seconds` and the HTML run page's `Review time` record
elapsed wall time; `Summed frame time` and `Mean per frame` describe individual
model durations, which overlap during parallel review.
Model preflight is shared across workers. Cancelling a parallel Codex run
stops active CLI processes and discards queued reviews before returning;
an active HTTP request finishes or reaches its configured timeout before
its worker exits. The report distinguishes a disabled checker, unavailable endpoint or
CLI, absent configured HTTP model, rejected vision input, malformed response,
and other request failures. A nonzero CLI exit is reported with its exit code
and a reminder to check Codex login and model access. The endpoint address,
API key, raw error bodies, and CLI logs are not stored. A backend failure does
not switch to another provider.

`advisory` is the default: warnings, model failures, and unavailable services
are recorded but return a successful process status. `strict` is honored only
when explicitly selected and returns a failure for any non-pass verdict or
integration error. Even in strict mode, semantic review supplements the
existing deterministic contracts and UI tests; it is never the sole source of
truth.

Host-side tests use an in-memory fake OpenAI-compatible endpoint and a fake
Codex executable. They open no socket, invoke no model, and cover image
payloads, fixed-schema validation, error mapping, disabled/advisory/strict
behavior, two-image parity payloads, CLI final-answer parsing and retry, and
process cleanup on timeout and cancellation.

### Screenshot sources and regression orchestration

`tests.visual_checks.regression` reviews one explicitly selected screenshot
source. `--source printer` is the default and `--source designer` is the local
alternative. `--mode` remains an alias for existing commands.

- `printer`: captures the complete existing non-physical `SUITE=UI` from
  Printer Regression and reviews every manifest frame. Pages also supported
  by Designer remain in this corpus, including all operation-state variants.
  It does not discover pages, load Designer scenarios, start Designer, or
  require `--designer-root`. `--printer-artifacts` can reuse saved UI captures;
  supplied COMPONENT captures are also retained. All artifacts must have the
  current UI fingerprint, and the UI set must be complete before model review.
- `designer`: automatically discovers every module-level `DeclarativePage`,
  renders its default state, and adds the meaningful typed state/action cases
  in `scenarios.json`. The current corpus has 32 frames over 15 pages. Native
  messages, errors, OTA notifications, and other imperative screens belong to
  the printer corpus; Designer scenarios cannot replace a page with drawing
  fixtures. A clean Designer result remains `partial` because native printer
  screens were not checked. Printer inputs are rejected in this mode.
- `hybrid`: retained for diagnostics. Combines Designer frames with printer
  frames whose semantic page was not rendered by Designer. Frames without a
  semantic ID and unmatched explicit `ui-context-*` states remain in the set.
- `parity`: retained for diagnostics. Adds paired Designer/real-renderer checks
  for the same default and typed-state cases from the cold `SUITE=COMPONENT`
  harness to the hybrid corpus.

The screenshot source is selected before rendering. Printer Regression's
existing captures are preserved. The UI suite also covers ten cases that
previously relied on manual Designer drawing fixtures: the second page of a
nine-line dialog, long remote-shutdown and MCU error explanations, print-result
messages with and without a cancellation reason, explicit two/three-line
test headings, and a paged OTA recovery-file list. Short/wrapped/paged dialogs,
the long action label, mesh-save confirmation, and short/long OTA release notes
already have printer captures. Their textual expectations remain in the
printer corpus. Live collection checks idle
state before the suite and again after downloading its artifacts. Incomplete
captures or a fingerprint mismatch stop before any model request and retain
an HTML failure report. Missing textual expectations produce `needs_baseline`.

Before a Designer scenario is accepted, the runner applies mutable
values through the Designer host state API so simulator-owned roles (for
example homing, position, inertia, and movement step) cannot overwrite the
requested typed state. It then verifies every requested value against the
rendered scene metadata. A silently ignored or normalized-away state fails
before capture and before any model request.

For the bed-screw result, confirm that the default and
`calibration-screws-adjustments` cases are both present. In parity mode, the
same explicit state must also appear as a `COMPONENT` capture and as a
Designer/real-renderer pair. Missing textual expectations stop the run with
`needs_baseline`; do not accept coverage based only on the default example.

In `hybrid` and `parity`, the runner reads the active theme from the downloaded
printer artifact and uses that exact theme for Designer capture. UI and
COMPONENT artifacts must report the same theme. `--theme` remains an explicit
override for targeted diagnostics; Designer-only mode falls back to `DEFAULT`
when no override is supplied.

To review the complete printer UI corpus:

```bash
.venv/bin/python -m tests.visual_checks.regression \
  --source printer \
  --printer-host <printer-host> \
  --confirm-printer-idle \
  --backend codex --model gpt-6-luna --reasoning-effort high \
  --enable
```

For saved captures, replace the printer host and idle-confirmation flags with
`--printer-artifacts /path/to/UI-run`. No printer connection is made. A stale
fingerprint still fails; do not rewrite artifact metadata to bypass it.

#### Local dialog composition suite

For retained dialog composition, run the standalone local suite:

```bash
.venv/bin/python -m tests.visual_checks.composition \
  --designer-root /path/to/feather-ui-designer
```

This suite runs the native FF5M renderer, `ScreenRoot`, and product dialog
paint lifecycle over three built-in layouts: declarative cards, declarative
rows, and an imperative tile painter. Each layout has 17 checkpoints covering
dialog opening, text and button updates, deferred background data, resizing,
background component replacement, suspension/resumption, forced recovery,
atomic dialog replacement, closing, and a subsequent dialog.

The Designer canvas replays the complete accepted command history for each
checkpoint. It does not draw an independent fresh frame: old pixels and
repeated alpha fills remain observable. Open `report.html` in the reported
artifact directory to inspect the screenshots manually. There are no reference
images, pixel comparisons, printer connections, or model requests.

`trace.json` and each report entry include render call order and timings,
arranged node bounds and tree, layout passes, accepted native batches, and
the current checkpoint's drawing operations. Counters automatically check
background paints, dialog preparations, scrims and accepted batches. Resizing
currently requires one discarded dialog size probe followed by one complete
accepted composition; the trace distinguishes preparation calls from output.
Timings are local preparation measurements, not printer performance results.
A zero exit status confirms draw-count checks and screenshot capture;
visual review remains manual. Failures retain the trace and completed captures.

Use `--layout cards`, `--layout list`, or `--layout imperative` for one sequence,
`--theme NAME` for a specific theme, and `--output DIRECTORY` for an empty
artifact destination. Existing run directories are preserved; use a new
directory for each run. `--trace-only` runs drawing contracts without a Designer
or browser. Artifacts default to the ignored `tests/artifacts/` directory.

#### First local run and result review

Start with the local-only Designer corpus. It does not contact the printer and
is the normal first check after UI changes:

```bash
.venv/bin/python -m tests.visual_checks.regression \
  --source designer \
  --designer-root /path/to/feather-ui-designer \
  --backend codex --model gpt-6-luna --reasoning-effort high \
  --enable
```

Before enabling model review, the same command without `--enable` is a quick
deterministic coverage check. It still discovers every declarative page,
applies all checked-in scenarios, renders the complete Designer corpus, and
validates that every frame has a textual expectation. A successful run reports
`disabled`; `needs_baseline` means a discovered default or explicit scenario
is missing from `expectations.json`.

For the same local corpus with a local LLM, replace the backend and model
flags with `--backend openai-compatible --base-url http://127.0.0.1:1234/v1
--model loaded-vision-model`.

The regression command's default local `.env` may provide the selected
backend, model, reasoning effort, review workers, base URL, timeout, CLI path, and optional
HTTP API key. Do not print, commit, or copy that file. Explicit CLI values
override the corresponding environment values. Backend selection does not
clear a configured model or effort: when switching from a locally configured
LLM to Codex, explicitly pass `--model gpt-6-luna --reasoning-effort high` as
shown above. The image-only command reads environment variables but does not
load `.env`. The regression command creates an ignored timestamped directory below
`tests/artifacts/ui-regression/` and prints the absolute paths to `report.html`
and `report.json`.

While it runs, the command prints its current pipeline stage and then one line
for every completed model review. The review line includes the completed and
total frame counts, case ID, last-frame time, total elapsed time, and an ETA
derived from the mean review duration and selected concurrency. Progress
arrives in completion order; a slow early frame does not hold up later results.
Before the first result,
the ETA is reported as `estimating`. Output is flushed immediately and remains
visible when the regression is launched by the LM Studio benchmark command.
For example:

```text
[stage 5/6] Reviewing 63 screenshots
[review 0/63] waiting for first result; ETA estimating
[review 17/63] move-ready; last 6.8s; elapsed 1m 55s; ETA 5m 11s
```

Open `report.html` for the normal human review. The report is a set of
static pages that share one `report.css` and one `report.js` (copied from
`tests/report_assets/`) and need no external scripts, fonts,
services, or network requests. Keep the pages together with the surrounding
timestamped artifact directory because their images use safe relative paths.
Each page works on one review aspect, and the tabs at the top move between
them:

- **Overview** (`report.html`) is the entry page: status, metrics, the
  outcome distribution, the first problem frames, and a card per aspect with
  its headline number. Infrastructure-failure and missing-baseline banners
  appear here.
- **Gallery** (`report-gallery.html`) is the screenshot-first grid: Designer
  pages and retained real-printer screens, then a separate grid whose tiles
  show both renderers side by side. Filter by outcome, source, page, or text,
  and resize the tiles. Clicking a tile opens a modal with the images, textual
  baseline, model summary, reasons, JSON-validation evidence, timings, and
  checklist; the arrow keys step through the frames the filters leave visible.
  Only this page carries the full per-frame detail, so other pages link to a
  frame as `report-gallery.html#frame-N`.
- **Problems** (`report-problems.html`) lists only warnings and failures with
  their reasons inline. Filter by outcome, model check, evidence class, error
  category, page, or text. Frames that were not reviewed by a model are not
  problems; the page links to them in the gallery.
- **Compare** (`report-compare.html`) shows one Designer/real-printer pair at
  a time as side by side, swipe, or difference view (black where both
  renderers match). The arrow keys switch pairs.
- **Coverage** (`report-coverage.html`) groups frames by semantic page with
  designer, printer, and parity counts and the worst outcome, and lists pages
  that Designer discovered but the run did not capture.
- **Baselines** (`report-baselines.html`) shows the textual expectation behind
  every case and the cases that still lack one.
- **Run** (`report-run.html`) holds the configuration, collection stages,
  timing, JSON-validation and error statistics, the slowest frames, and links
  to `report.json` and `report.md`.

Filters are reflected in the page URL (for example
`report-gallery.html?outcome=fail&source=parity`), so a specific view can be
bookmarked or shared. Desktop tiles deliberately start at a large inspection
scale (about 480 CSS pixels for a standalone frame and 720 for a pair) so small
visual differences remain visible; warning and failure tiles carry prominent
yellow or red borders and markers.

The first parity image is always the Designer frame and the second is always
the real Typer/framebuffer frame. Hybrid/parity theme synchronization removes
normal theme-color differences. Footer-only live status (temperatures, network
address, preview/standby label) may still differ; page titles, controls,
dialogs, selections, and typed-state values must remain equivalent.

The reviewer first performs a standalone quality audit of every supplied
image, including ordinary Designer-only and printer-only screenshots. It
checks clearance from headers, borders, controls, and neighboring text;
internal padding; spacing and vertical rhythm; alignment; proportions;
hierarchy; and balanced whitespace. Readable text with no literal overlap can
still fail when the layout is visibly cramped, uneven, awkwardly aligned, or
unbalanced. Those findings use `aesthetic_defect` evidence and always produce
`fail`. Structural, content, dialog, selection, typed-state, clipping, overlap,
missing-content, and explicitly constrained-value defects use
`product_semantic` evidence and also produce `fail`.

For a parity pair, the reviewer performs both standalone audits before it
compares visible geometry and presentation from header to footer. A visible
Designer/printer difference that does not reduce the quality of either frame
uses `design_mismatch` evidence and produces `warn`; if it also creates an
aesthetic or product defect, `fail` takes precedence. Live and mock values are
classified as `dynamic_runtime` and compared by their role, plausible format,
readability, and location rather than by their literal value. Exact or
approximate numeric equality is required only when the case expectation
explicitly constrains that value. Permitted anti-aliasing and rasterization
differences use `rendering_only`. Neither `dynamic_runtime` nor
`rendering_only` can produce a warning or failure.

The JSON validator enforces the evidence/severity relationship. A mismatched
combination receives the same single corrective retry used for malformed JSON.
Advisory mode records `warn` and `fail` results for review without failing the
runner process; strict mode preserves them as the explicit regression gate.

`report.json` remains the machine-readable source of truth, while `report.md` is a short
terminal-friendly summary.

The runner also writes all three reports when discovery, printer collection,
fingerprint validation, image handling, or model setup fails before ordinary
review. In that case the overview starts with an infrastructure-failure
banner, and any images already collected inside the run directory remain
visible with `not_run` status. This makes every failed invocation reviewable
without bypassing a safety check or losing the evidence gathered before it.

Read the result in this order:

1. Open `report.html` and check the status banner and the outcome bar, then
   follow the Problems tab.
2. `status` is `pass`, `review`, or `fail` for a complete printer/hybrid/parity
   corpus. A Designer-only run intentionally reports `partial` after a clean
   review because legacy printer screens are absent.
3. `coverage` shows captured printer frames, retained printer frames,
   replaced duplicates, and parity pairs. A real parity run must have a
   non-zero `parity_pairs` count.
4. Each `screenshots[]` record has `source`, `case_id`, source-artifact hash,
   textual-expectation references, and `case_result` with verdict, reasons,
   JSON-validation status, elapsed time, and normalized error.
5. `needs_baseline` means a newly discovered page or scenario has no approved
   textual expectation. The candidate file is written locally; add a reviewed
   text expectation before enabling model review again.

Use `--check-mode strict` only when an explicit CI/release gate is intended.
The usual local run is advisory; deterministic contract and UI tests remain the
primary checks in either mode.

If the report says `model_unavailable`, the local endpoint was reachable but
the selected model name did not exactly match its `/models` catalog. Load the
intended vision model in the local service, set its exact catalog ID through
`--model` or the ignored `.env`, and rerun. If it says `vision_unsupported`,
select a vision-capable model; do not weaken the image payload or JSON schema.
`invalid_response` means the model returned a response that failed independent
JSON validation. The checker makes one corrective retry with the same frame and
an explicit schema reminder; if it remains invalid, inspect the normalized
error, repeat the affected saved frame once, and rerun the complete corpus
before accepting a result. Repeated schema failures make that model unsuitable
for this regression gate.

After a successful local Designer run, a real printer or parity run still needs
separate explicit approval, an idle printer, and the command shown above with
`--confirm-printer-idle`. It must not be combined with synchronization or a
Klipper restart.

The live modes are deliberately blocked unless the printer host is explicit
and `--confirm-printer-idle` is present. Their preflight rejects printing,
paused, active virtual-SD, or non-zero heater-target states. They only invoke
the non-physical `UI`/`COMPONENT` suites and download their artifacts; they
never synchronize files or restart Klipper. Every printer artifact contains a
fingerprint of the deployed Feather UI/framework Python files; a missing or
different fingerprint stops the run before model review. A saved printer
artifact directory can be supplied with `--printer-artifacts` for a fully
offline rerun. The runner copies only its manifest, environment metadata, and
image files into the new timestamped run directory, so the HTML report remains
portable and never depends on the original artifact location.

Textual structured expectations and the fixed checklist are the only
checked-in baselines. Captured PNG/BMP files, model responses, reports, and
candidate baselines go under the ignored `tests/artifacts/` tree. If any
automatically discovered case lacks an expectation, no model request is made:
the run writes `expectations.candidate.json` and returns `needs_baseline`.

Only one model is accepted in a run. To choose between local models, rerun the
same saved corpus once per model, then compare reports without making requests:

```bash
.venv/bin/python -m tests.visual_checks.compare_reports \
  /path/to/model-a/report.json \
  /path/to/model-b/report.json
```

The comparison reports JSON-validity, verdict counts, review rate, mean
latency, and normalized error counts. Endpoint addresses, API keys, and raw
secret-bearing errors are excluded from all regression artifacts.

To benchmark every already downloaded LM Studio vision model with a declared
parameter count no greater than 12B, use the separate host-only command. It
uses the native LM Studio API only for catalog, load, and unload lifecycle;
each visual inference still uses the provider-neutral OpenAI-compatible
pipeline:

```bash
.venv/bin/python -m tests.visual_checks.lmstudio_benchmark \
  --designer-root /path/to/feather-ui-designer \
  --printer-artifacts /path/to/saved-ui-artifacts \
  --printer-artifacts /path/to/saved-component-artifacts
```

The command never contacts the printer and never downloads models or
dependencies. It excludes non-vision models, models above 12B, and models with
an unknown parameter count. Models run sequentially against the same saved
artifacts. Every loaded instance is unloaded and its absence verified before
the next model starts; an unload-verification failure stops the benchmark.

The ignored `benchmark.json` records structured-response validity, verdicts,
review rate, errors, model load time, complete corpus wall time, mean request
latency, model file size, and the LM Studio memory estimate when the local CLI
can provide one. Both LM Studio's reported load time and independently
measured load wall time are retained. Each model also has its own large-grid
HTML report. The API key and endpoint are not included in any report.

This is not a gap to hide: Forge-X changes early boot, services, printer motion, calibration, and low-memory behavior on specific hardware. A unit-like syntax check cannot prove the crucial outcomes.

## Minimum checks by change area

| Change area | Static review | On-device / integration validation |
|---|---|---|
| `.shell/S00init`, `.shell/init-main.sh`, `.shell/S55boot`, `.shell/S99root`, `.bin/exec/netd`, mounts | Trace normal, soft-skip, hard-failure, first-run, daemon-ready timeout, and Stock-inert branches; verify every referenced deployed path | Cold boot with empty variables selecting Feather; explicit Stock mode; non-Stock Ethernet/Wi-Fi and offline Feather; a controlled skip/fallback; first-run DB/bootstrap where feasible; retain logs |
| `.root/*` service scripts / `moonraker.conf` | Confirm chroot mount/path ownership, start-stop symmetry, PID handling, port/path consistency, config include ownership, and updater semantics; see [Chroot environment and web runtime](workflows/chroot-and-web-runtime.md) | Moonraker readiness on 7125, static UI reachability on 80, browser-to-Moonraker connection, service disable switches, display gates, and reboot persistence |
| `mod_params.json` / `zchanges.sh` | Confirm schema, defaults, migration/deprecation, macro declaration, and each keyed side effect | Toggle parameter, verify expected restart/reboot/service action and persistence; restore default |
| `config/` and `macros/` | Trace includes, renamed macros, shell command arguments, safe motion preconditions | Controlled homing, calibration, pause/resume/cancel, start/end print path using safe test conditions; recalibrate before production prints |
| `.py/klipper/patches` and `plugins` | Confirm target stock-module path/layout, `.bak` backup and stale-link cleanup behavior, plugin vs replacement classification, and optional tuning interactions; see [Built-in Klipper patching](workflows/klipper-patching.md) | Exercise the affected Klipper feature and inspect relevant logs; validate supported firmware version and uninstall/rollback path where the deployment changes |
| USB storage / boot media | Review sysfs ancestry, partition enumeration, mount ownership/access, destructive-device revalidation, and eMMC fallback | Test MBR/GPT-style kernel partitions, logical/multiple partitions, whole-disk filesystems, FAT/ext, existing ro/rw mounts, late device nodes, boot flags/firmware detection, and Fluidd prompt events; verify the actual USB device before destructive tests |
| Swap/ZRAM/memory tuning | Review defaults, bundled modules/scripts, and interactions with UI/services | Reboot, confirm selected swap mode and USB-to-eMMC fallback, observe service stability under representative load |
| Camera, screen, remote access | Review mode-specific config routing, process/pipe ownership, port boundaries, user-state ownership, and stock fallback; see [Screen modes and Feather](workflows/screens-and-feather.md) | Enable/disable cleanly between stock, Feather, headless, and affected Guppy modes; verify DHCP/network prerequisites, display status/error output, browser control, fallback, and recovery route |

Use the operator docs as test procedures where applicable—especially [`docs/PRINTING.md`](../docs/PRINTING.md), [`docs/CALIBRATION.md`](../docs/CALIBRATION.md), [`docs/SCREEN.md`](../docs/SCREEN.md), and [`docs/RECOVERY.md`](../docs/RECOVERY.md).

## Required safety gates

1. **Do not test new motion behavior on an uncalibrated printer.** Installation/uninstallation and config tuning can invalidate bed mesh/Z offset.
2. **Retain a recovery route before touching boot code.** Confirm the documented dual-boot/USB recovery route and preserve logs.
3. **Exercise default Feather and explicit Stock before other display modes.** Stock remains the fallback and recovery path.
4. **Treat calibration/macro edits as hardware-risk changes.** Review the full macro chain; test with conservative conditions.
5. **Avoid destructive broad tests.** Do not wipe Moonraker state, overwrite user-owned `mod_data`, or alter private SSH material simply to validate a source change.

## Recent history: why these checks matter

Recent Git history provides concrete regression themes:

- **Firmware compatibility:** `adfed5c` added stock firmware 5.0.x/5.1.x support, expanding `S00init`, docs, and parameter behavior. Validate against the supported firmware range rather than assuming all stock releases work.
- **Memory constraints:** `b9322c4` added compressed ZRAM swap; `e637aff` capped glibc malloc arenas for Moonraker and GuppyScreen. Resource changes must be tested with the relevant services/screens running.
- **Boot/UI stability:** `0acb2ae` suppresses a stock slicer-upgrade nag only in stock-screen use; `c327a47` made Dropbear arguments configurable. Changes often have mode-specific effects.
- **Print safety:** `c243f88` resets mesh before nozzle cleaning to avoid a bad measured Z/potential scratching when mesh validation and cleaning coexist. Earlier commits refined cooldown conditions and mesh-profile defaults. Macro changes need physical workflow validation.
- **Klipper correctness:** `de8e6ab` fixes a shaper-calibration hang by draining a child result pipe. Patch changes require exercising the exact affected action, not merely booting.
- **Scheduling:** `5426a14` adds optional `klipper_rt` (`SCHED_RR`) behavior through parameter and restart paths. Validate enable/disable and service recovery.

An earlier Python-only adaptive-pressure-advance patch was added and then reverted because the installed native helper still exposed the old API. The replacement must deploy the matching Python modules and native helper as one unit. This reinforces the rule: do not retain a patch merely because it applies; prove it is safe and compatible in this target stack.

## Documentation expectations for future changes

Update this wiki when a change alters one of the following:

- boot/fallback/recovery topology;
- persistent-state ownership or migration;
- supported firmware/hardware envelope;
- externally visible service endpoint, updater, or authentication posture;
- display-mode, slicer, calibration, or macro contract;
- required validation or recovery procedure.

Keep the detailed operator instructions in `docs/` current as well. If validation cannot be performed on hardware, state that limitation in the change/release notes rather than implying coverage.
