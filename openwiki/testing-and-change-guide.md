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
