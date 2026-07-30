# Testing and change guide

## Validation posture

The repository now contains small host-side tests for Feather utility/state helpers and the C++ interactive hitbox layer. They validate parsing and pure logic but cannot emulate framebuffer, touchscreen, boot, network, or physical motion. The effective validation model remains **focused host tests plus controlled on-device testing**.

## Feather on-printer regression runner

Feather provides a hidden, opt-in G-code harness whose implementation remains a
cold lazy feature until a run actually starts. It is Feather-only; Headless has
no display plugin and does not register the command.

After deploying a changed Feather Python module, use a hard Klipper process
restart before testing. A normal Klipper `RESTART` rebuilds printer state in the
existing interpreter and can retain an already imported module in `sys.modules`.

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
before restoring the live state. The filament trace renders a synthetic
`130.4 / 250C` heating and `260.4 / 250C` cooling states without changing the
heater target or fan, captures the declarative action layout, follows the real Back hitbox to material selection,
and fails if that navigation changes the live nozzle target. Synthetic taps are resolved from the
renderer’s button, toggle, and declarative action-hitbox registries, so clickable
text/panels are covered without importing development-only page modules into
Feather’s normal startup path.

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
calibration stages/results. Framebuffer reads, stability hashing, BMP encoding,
log copying, CSV/JSON updates, and retention execute in the artifact worker
thread. The reactor only schedules steps and receives completion callbacks.
Retention keeps at most ten completed runs and 512 MiB, never deletes the active
run, and preserves the newest failure for inspection.

## Development-only semantic screenshot checks

Semantic screenshot review is a separate Mac host-side tool under
`tests/visual_checks/`. It is not imported, registered, configured, or executed
by Feather, Klipper, printer startup, or the on-printer UI test runner.
`sync.sh` excludes the complete `tests/` directory, so the checker is not
included in printer synchronization archives. It uses only Python's standard
library and adds no project, build, or printer dependency.

The tool is disabled unless the developer explicitly passes `--enable`. It
accepts either an already downloaded UI-test artifact directory (using its
`manifest.json`) or explicit BMP/PNG/JPEG/WebP files. It then uses the common
OpenAI-compatible `/models` and `/chat/completions` contract; there is no
provider-specific discovery, model download, or service management.
Feather's uncompressed 24/32-bit BMP frames are converted in memory to PNG
with the Python standard library before submission because some compatible
servers reject BMP vision input. The saved source artifact, its hash, and its
byte count remain unchanged. Responses use the OpenAI-compatible
`json_schema` format and are independently validated again by the checker.

Connection settings are host-local:

- `FF5M_VISUAL_BASE_URL` supplies the OpenAI-compatible base URL;
- `--model` or `FF5M_VISUAL_MODEL` supplies exactly one loaded model name;
- `FF5M_VISUAL_API_KEY` is optional and is never logged or serialized;
- `--timeout` / `FF5M_VISUAL_TIMEOUT` bound each HTTP request;
- `--mode advisory|strict` / `FF5M_VISUAL_MODE` select enforcement in the
  image-only command; the regression command uses `--check-mode`.

A safe invocation shape, intentionally omitting endpoint and credentials, is:

```bash
python3 -m tests.visual_checks.run \
  /path/to/saved-ui-test-artifacts \
  --enable \
  --model loaded-vision-model \
  --mode advisory
```

The generated `visual-checks.json` keeps, for every screenshot and its selected model, the
verdict, non-pass reasons, JSON-validation status, elapsed request time, and
normalized errors. It also distinguishes a disabled checker, unavailable
endpoint, absent configured model, rejected vision input, malformed response,
and other request failures. The endpoint address, API key, and raw error bodies
are not stored.

`advisory` is the default: warnings, model failures, and unavailable services
are recorded but return a successful process status. `strict` is honored only
when explicitly selected and returns a failure for any non-pass verdict or
integration error. Even in strict mode, semantic review supplements the
existing deterministic contracts and UI tests; it is never the sole source of
truth.

Host-side tests use an in-memory fake OpenAI-compatible endpoint. They open no
socket, invoke no model, and cover image payloads, fixed-schema validation,
error mapping, disabled/advisory/strict behavior, and two-image parity
payloads.

### Hybrid regression orchestration

`tests.visual_checks.regression` is the higher-level development command. It
validates the FF5M project with Feather UI Designer, automatically renders
every discovered module-level `DeclarativePage`, and creates a default case
for every discovered stable `PageKey`. The checked-in `scenarios.json` adds
only meaningful non-default typed states; it is not a page registry.
The current automatic/default plus explicit matrix contains 23 cases over
seven discovered pages, including unhomed/homed movement, joystick feedback,
fine/coarse steps, paper-test positioning/probing/ready states, Safe Z
probing/result, measured summary results, warnings, and dialogs. Runtime
read-only values are installed into an isolated Designer-host checkpoint for
rendering; the product state declarations and controllers are not changed.

The modes are:

- `designer`: local Designer frames only. While legacy pages remain, a clean
  result is reported as `partial`, not as a complete release gate.
- `hybrid`: Designer frames plus every frame from the existing printer
`SUITE=UI` whose `semantic_page_id` was not rendered by Designer. A printer
  frame with no semantic ID, or an unknown ID, stays in the corpus.
- `parity`: the hybrid corpus plus paired Designer/real-renderer checks for
  the same default and additional typed-state cases captured by the cold
  `SUITE=COMPONENT` harness.

Before a Designer scenario is accepted, the scenario adapter applies mutable
values through the Designer host state API so simulator-owned roles (for
example homing, position, inertia, and movement step) cannot overwrite the
requested typed state. It then verifies every requested value against the
rendered scene metadata. A silently ignored or normalized-away state fails
before capture and before any model request.

In `hybrid` and `parity`, the runner reads the active theme from the downloaded
printer artifact and uses that exact theme for Designer capture. UI and
COMPONENT artifacts must report the same theme. `--theme` remains an explicit
override for targeted diagnostics; Designer-only mode falls back to `DEFAULT`
when no override is supplied.

The default invocation shape is:

```bash
python3 -m tests.visual_checks.regression \
  --mode hybrid \
  --designer-root /path/to/feather-ui-designer \
  --printer-host <printer-host> \
  --confirm-printer-idle \
  --model <loaded-vision-model> \
  --enable
```

#### First local run and result review

Start with the local-only Designer corpus. It does not contact the printer and
is the normal first check after UI changes:

```bash
python3 -m tests.visual_checks.regression \
  --mode designer \
  --designer-root /path/to/feather-ui-designer \
  --enable
```

The default local `.env` may provide the single selected model, base URL,
timeout, and optional API key. Do not print, commit, or copy that file. An
explicit `--model loaded-vision-model` overrides only the model selection for
that run. The command creates an ignored timestamped directory below
`tests/artifacts/ui-regression/` and prints the absolute paths to `report.html`
and `report.json`.

Open `report.html` for the normal human review. It is an offline report with no
external scripts, fonts, services, or network requests. Keep it together with
the surrounding timestamped artifact directory because its images use safe
relative paths.

The report is screenshot-first:

- **Screenshot overview** is a dense grid of Designer pages and retained
  real-printer screens;
- **Designer ↔ real printer** is a separate continuation grid whose tiles show
  both renderer outputs side by side;
- warning/failure tiles use prominent yellow/red borders and markers;
- clicking any tile opens a large modal with the images, textual baseline,
  model summary, reasons, JSON-validation evidence, timings, and checklist;
- run stages and collection/model evidence stay collapsed until requested.

The first parity image is always the Designer frame and the second is always
the real Typer/framebuffer frame. Hybrid/parity theme synchronization removes
normal theme-color differences. Footer-only live status (temperatures, network
address, preview/standby label) may still differ; page titles, controls,
dialogs, selections, and typed-state values must remain equivalent.

Use the compact toolbar to filter by outcome or source. `report.json` remains
the machine-readable source of truth, while `report.md` is a short
terminal-friendly summary.

The runner also writes all three reports when discovery, printer collection,
fingerprint validation, image handling, or model setup fails before ordinary
review. In that case `report.html` starts with an infrastructure-failure
banner, and any images already collected inside the run directory remain
visible with `not_run` status. This makes every failed invocation reviewable
without bypassing a safety check or losing the evidence gathered before it.

Read the result in this order:

1. Open `report.html` and check the status banner and source-coverage cards.
2. `status` is `pass`, `review`, or `fail` for a complete hybrid/parity
   corpus. A Designer-only run intentionally reports `partial` after a clean
   review because legacy printer screens are absent.
3. `coverage` shows captured printer frames, retained legacy printer frames,
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

After a successful local Designer run, a real hybrid or parity run still needs
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
python3 -m tests.visual_checks.compare_reports \
  /path/to/model-a/report.json \
  /path/to/model-b/report.json
```

The comparison reports JSON-validity, verdict counts, review rate, mean
latency, and normalized error counts. Endpoint addresses, API keys, and raw
secret-bearing errors are excluded from all regression artifacts.

This is not a gap to hide: Forge-X changes early boot, services, printer motion, calibration, and low-memory behavior on specific hardware. A unit-like syntax check cannot prove the crucial outcomes.

## Minimum checks by change area

| Change area | Static review | On-device / integration validation |
|---|---|---|
| `.shell/S00init`, `.shell/S55boot`, `.shell/S99root`, mounts | Trace normal, soft-skip, hard-failure, and first-run branches; verify every referenced deployed path | Cold boot default stock mode; a controlled skip/fallback; first-run DB/bootstrap where feasible; retain logs |
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
3. **Exercise stock mode before alternate modes.** Stock is the default and fallback path.
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

An advanced pressure-advance patch was added and then reverted in nearby history. This reinforces the rule: do not retain a patch merely because it applies; prove it is safe and compatible in this target stack.

## Documentation expectations for future changes

Update this wiki when a change alters one of the following:

- boot/fallback/recovery topology;
- persistent-state ownership or migration;
- supported firmware/hardware envelope;
- externally visible service endpoint, updater, or authentication posture;
- display-mode, slicer, calibration, or macro contract;
- required validation or recovery procedure.

Keep the detailed operator instructions in `docs/` current as well. If validation cannot be performed on hardware, state that limitation in the change/release notes rather than implying coverage.
