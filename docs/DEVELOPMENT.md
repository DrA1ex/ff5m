# Development, testing, and release validation

Forge-X modifies a real printer: changes may affect motion, heaters, probing,
configuration, recovery, storage, and the local UI. For that reason, testing is
treated as part of the implementation and release process rather than as a
separate cleanup step.

The project uses several validation layers together:

- automated host-side unit and regression tests;
- rendered G-code macro regression tests;
- visual UI regression;
- on-printer regression that executes real workflows on Adventurer 5M hardware;
- physical print regression, including pause/resume/cancel and power-loss
  recovery paths;
- release validation performed before publishing a release.

CI is important, but **CI alone is not considered sufficient release
validation** for code that can move or heat the printer.

For the implementation details, commands, artifact formats, telemetry, and
individual printer suites, see the
[Testing and change guide](/openwiki/testing-and-change-guide.md).

For user-level customization (`user.cfg` overrides, own macros, dialogs, services), see [Customizing and extending Forge-X](EXTENDING.md).

## Stock firmware integration and lifecycle safety

Forge-X is integrated into the FlashForge firmware as a reversible layer rather
than as an all-or-nothing replacement. The stock firmware remains a deliberate
fallback path, while the mod adds its chroot runtime, Moonraker, Klipper
extensions, display modes, and printer-specific configuration on top.

Configuration changes are applied through the same managed
[`cfg_backup.py`](/.py/cfg_backup.py) mechanism used for backup and restore.
Forge-X does not depend on ad-hoc edits to `printer.cfg` for its display
profiles. The repository carries explicit configuration deltas for Stock,
Feather, Guppy, and Headless modes under `.cfg/init.display.*.cfg`, together
with restore profiles used during uninstall and recovery. Mode switching,
normal boot repair, and uninstall therefore operate on the same reviewed
configuration model.

The early boot path is also intentionally fail-safe. The installed
`/etc/init.d/S00init` file is treated as the **last-known-good boot guard**.
An OTA/source update may replace the mod files, but routine runtime reload does
not replace this installed guard. During the next normal boot, the new
initializer must complete successfully and publish its readiness flag before
the guard is updated. The candidate guard is syntax-checked, copied to a
temporary file, synced, and atomically renamed into place only after that
successful initialization. If initialization is interrupted or returns without
readiness, Forge-X records the failed boot and the next boot bypasses the mod
and continues with the stock firmware instead of repeatedly executing the new
broken boot path.

This gives updates a two-stage lifecycle:

1. update the mutable Forge-X runtime;
2. boot it while the previous boot guard is still installed;
3. confirm that normal initialization reached its ready state;
4. only then commit the new boot guard.

The same boot boundary arms durable recovery/failure markers before optional
mod work begins. An interrupted normal initialization falls back to Stock; an
interrupted Recovery session also falls back to Stock rather than repeatedly
entering a half-completed recovery path.

Rollback exists at several additional layers:

- stock Klipper modules replaced by Forge-X keep `.bak` copies and are restored
  by uninstall;
- display/configuration mutations have explicit restore profiles;
- settings can be backed up and restored through the managed configuration
  tooling;
- the Recovery environment can diagnose the filesystem, create backups, reset
  settings, uninstall Forge-X, and invoke firmware restore paths;
- uninstall restores printer configuration and Klipper replacements before
  deleting the mod runtime.

These mechanisms are regression-tested along with boot recovery, firmware image
installation, configuration migration, and uninstall-sensitive behavior. The
goal is that a failed update or configuration change should normally leave a
stock or Recovery route available instead of requiring immediate UART/FEL
repair.

For the exact trust boundary and source flow, see the
[Architecture overview](/openwiki/architecture.md), the
[Operations and recovery guide](/openwiki/workflows/operations-and-recovery.md),
and the operator-facing [Firmware Recovery guide](/docs/RECOVERY.md).

## Validation layers

### Automated host tests

The 1.4.2 development tree contains dozens of dedicated host-side test modules
under `tests/`. They cover project-specific behavior rather than only upstream
dependencies.

Current coverage includes, among other areas:

- G-code macro rendering and behavior;
- Klipper overlays, backports, and reactor patches;
- Power Loss Recovery / Resurrection state handling;
- boot recovery, configuration migration, backup, and restore;
- firmware image installation and update paths;
- Feather state, actions, workflows, safety checks, rendering, and input;
- operation-context behavior;
- network and Moonraker integration;
- resource-monitoring helpers and printer-regression tooling.

The host suite is run by CI for the 1.4.2 development branch:

```bash
python tests/run_host_tests.py --verbose
```

A passing host suite means the covered behavior remains internally consistent.
It does **not** replace real-printer validation for changes that interact with
motion, heaters, probing, the display, or hardware timing.

### Macro regression is more than syntax checking

Forge-X does not treat a macro as validated merely because the Jinja template
parses or because the generated G-code looks plausible.

Host tests render macros against explicit Klipper status snapshots and inspect
the generated commands. In addition, the on-printer regression runner executes
representative workflows through the real Klipper/Moonraker/Feather stack.

That distinction is important for safety-sensitive behavior: movement bounds,
homing, probing, heating, parking, pause/resume/cancel, mesh workflows, and
recovery paths are exercised as actual printer operations during hardware
regression rather than being accepted on static review alone.

### Real-printer physical regression

Forge-X includes an opt-in printer regression runner for controlled testing on
real Adventurer 5M hardware.

The physical suites cover workflows such as:

- homing and reversible XYZ movement;
- heating and cooling;
- bed screw tuning;
- full bed mesh generation;
- probe-based Z-offset calibration flows;
- KAMP and nozzle-cleaning interaction;
- mesh validation;
- an actual sliced print fixture;
- UI-driven pause and resume;
- cancel and terminal-dialog behavior;
- creation and restoration of a Resurrection checkpoint;
- filament load, purge, unload, and cold-pull workflows.

The print regression deliberately performs a real print and then exercises the
recovery path so that release validation covers the interaction between G-code,
printer state, UI state, and the physical machine.

These suites have explicit preconditions and confirmation levels because they
can move or heat the printer. The detailed safety contract and exact commands
are documented in the
[Testing and change guide](/openwiki/testing-and-change-guide.md).

### Visual regression

Feather changes are also validated visually.

The test infrastructure can capture semantic screenshots from the real
framebuffer and produce a time-aligned record of UI state during printer
regression. A separate development-only visual-check pipeline covers stable
pages and meaningful non-default states.

Visual regression supplements deterministic tests; it does not replace them.
A screen that looks correct can still have incorrect behavior, and correct
logic can still produce a broken layout. Both classes of regression are
checked.

### Runtime and resource evidence

Physical regression can record printer telemetry and host resource data while
the suite is running. This includes motion-buffer information, MCU statistics,
temperatures, print state, operation context, memory, CPU, scheduler data, and
per-process resource usage.

This is particularly useful for AD5M-specific stability work: a change should
not be considered successful merely because the visible workflow completed if
it introduced reactor stalls, memory pressure, or scheduling regressions.

## Release validation

Before a Forge-X release is published, maintainers perform a full regression
pass appropriate to the release. The release gate includes:

1. the automated host regression suite;
2. visual regression for UI-affecting changes;
3. full physical regression on real printer hardware;
4. real print-flow validation for changes that can affect printing or recovery;
5. review of failures and collected artifacts before the release is accepted.

This process is intentionally broader than GitHub Actions. Hardware regression
and visual inspection are maintainer release-validation steps and are not
presented as cloud-CI jobs.

The purpose is to catch regressions before they reach normal users, especially
bugs that only appear when multiple systems interact on the actual printer.

## Contributor and maintainer expectations

Changes should include evidence proportional to the behavior they modify.

### Behavior changes require regression coverage

A pull request that fixes a bug or changes observable behavior should normally
add or update a regression test that would fail without the change.

When a bug can be reproduced in a host test, keep that reproduction in the
suite. A fix without a regression case makes it easier for the same failure to
return later.

### Safety-sensitive changes require hardware validation

Changes that can affect any of the following require real-printer validation
before release:

- toolhead or bed motion;
- homing or probing;
- Z-offset or bed mesh behavior;
- heaters, fans, or filament handling;
- pause, resume, cancel, or parking;
- Power Loss Recovery / Resurrection;
- Klipper scheduling or timing behavior;
- boot, update, recovery, or rollback paths that can leave the printer in an
  unsafe or unbootable state.

A contributor does not need to own compatible hardware to propose a change,
but the limitation must be stated in the pull request. Required hardware
validation must then be completed by a maintainer before release.

### UI changes require visual regression

Changes to Feather layout, navigation, dialogs, controls, or workflow state
must be checked with the relevant deterministic UI tests and visual regression.

For stateful workflows, test the meaningful states, not only the default page.

### Installation and migration changes must preserve recovery

Changes to installation, update, configuration migration, patching, backup, or
uninstall behavior must preserve a tested recovery/rollback path.

Where Forge-X hot-patches stock files, the original must remain recoverable.
A change that cannot be safely rolled back needs explicit design review before
it is accepted.

### Pull requests should report validation evidence

For non-trivial changes, include in the pull request:

- what behavior changed;
- which automated tests were added or updated;
- which host suites were run;
- whether real-printer validation was performed;
- whether visual regression was required and performed;
- any known validation gap or hardware path that was not exercised.

"Works for me" is useful information, but it is not a substitute for a
repeatable regression case.

### Do not weaken safety contracts casually

Bounds checks, preconditions, recovery guards, explicit confirmations, and
cleanup paths exist because the software controls real hardware.

A pull request that removes or relaxes one of these protections should explain
why the old constraint is incorrect and include regression coverage for the new
behavior.

## What this process does — and does not — guarantee

Extensive regression testing reduces the chance that users become the first
people to encounter a known class of bug. Real-printer testing also catches
failures that mocks and static analysis cannot reproduce.

It does **not** prove that every printer, filament, slicer configuration, or
future firmware combination is safe. Forge-X remains an unofficial firmware
mod, and hardware validation cannot eliminate all risk.

The engineering goal is narrower and practical: make behavior reproducible,
keep safety-critical changes reviewable, exercise important workflows on the
real machine, and preserve regression cases so fixed bugs stay fixed.

## Engineering facts and where to verify them

This table lists how Forge-X handles the risky parts of changing a printer's firmware, and where each item can be checked in the repository.

| Topic | What Forge-X does | Where to check |
| --- | --- | --- |
| Boot after a failed update | The installed early boot guard stays in place until the new runtime reports that it is ready. Markers are written before initialization starts. If initialization is interrupted or never becomes ready, the next boot starts the stock firmware. | [`.shell/S00init`](/.shell/S00init), [`tests/test_boot_recovery.py`](/tests/test_boot_recovery.py) |
| Replacing the boot guard | The new guard is checked with `bash -n`, copied to a temporary file, checked again, synced, moved into place with an atomic `mv`, and synced again. | [`.shell/S00init`](/.shell/S00init) |
| Changes to printer configuration | A parser changes the Klipper configuration. It understands sections, parameters, includes, and deferred includes. Display modes are declarative profiles, and backup and restore write through a temporary file and rename. A verify mode checks the configuration without changing it. `avoid_writes` skips writing when nothing changes. | [`.py/cfg_backup.py`](/.py/cfg_backup.py), [`.cfg/`](/.cfg/), [`tests/test_cfg_backup.py`](/tests/test_cfg_backup.py) |
| Klipper patches | Every replaced Klipper file keeps a `.bak` copy, plugins are linked into `extras/`, and uninstall reverses both. | [`.shell/klipper_overlay.sh`](/.shell/klipper_overlay.sh), [`tests/test_klipper_overlay.py`](/tests/test_klipper_overlay.py), [Klipper fixes](/docs/KLIPPER.md) |
| MCU firmware | Not reflashed. | [Klipper fixes](/docs/KLIPPER.md) |
| Parking and travel limits | One macro, `MOVE_SAFE`, holds the allowed X/Y/Z limits and clamps moves. KAMP Smart Park uses it. | [`macros/base.cfg`](/macros/base.cfg), [`KAMP/Smart_Park.cfg`](/KAMP/Smart_Park.cfg) |
| Power Loss Recovery | The implementation is Python source in the repository, with host tests and a physical regression check. | [`.py/klipper/plugins/resurrection.py`](/.py/klipper/plugins/resurrection.py), [`resurrection_state.py`](/.py/klipper/plugins/resurrection_state.py), [`tests/test_resurrection.py`](/tests/test_resurrection.py) |
| Automated tests | Dozens of host test modules cover boot recovery, installation, Klipper patches, macros, Feather, and Power Loss Recovery. GitHub Actions runs `python tests/run_host_tests.py`. | [`tests/`](/tests/), [`.github/workflows/tests.yml`](/.github/workflows/tests.yml) |
| Real-printer tests | A regression runner executes real workflows on an AD5M. It is a maintainer step before a release, not a cloud CI job. | [`tests/printer_regression.py`](/tests/printer_regression.py), [Release validation](#release-validation) |
| Firmware download (updater) | Download over HTTPS with the standard certificate checks, into a `.part` file, with a size check against the release asset, a check that the file is a valid firmware tar, and an atomic replace. | [`.py/zupdate.py`](/.py/zupdate.py) |

## Release policy

A Forge-X release is published when Forge-X itself changes: fixes, new features, reviewed Klipper patches, or a change to a dependency that Forge-X needs. A new upstream version of Moonraker, Klipper, Fluidd, or Mainsail is not a reason for a release by itself.

- **Fluidd, Mainsail, and Guppy Screen** have their own entries in Moonraker Update Manager and update independently of Forge-X.
- **Moonraker** is shipped in the version that has been tested with Forge-X. It changes in a Forge-X release when a fix or a needed feature requires it.
- **Klipper** stays at the version that FlashForge ships. Fixes and features from newer Klipper are backported, tested, and listed in [Klipper fixes and AD5M-specific hardening](/docs/KLIPPER.md).
- **Minor versions** (for example, 1.4.1 → 1.4.2) are installed over OTA. A new **major version** is flashed over the existing installation, and settings and calibration are kept.
- **Beta versions** are published as pre-releases on the GitHub releases page and receive updates in their own branch. The release notes of each beta describe how to move to the final release.

## Known limitations

- Only the Adventurer 5M and 5M Pro are supported. Other printers would need a separate port.
- The Klipper host is the FlashForge 0.11 generation. Newer Klipper features are available only after they have been backported.
- Feather covers the main local workflows. Unrestricted G-code, file deletion, static or enterprise Wi-Fi, and detailed diagnostics still need Fluidd or Mainsail.
- The Stock screen can freeze if `SAVE_CONFIG` or `RESTART` is sent directly. Use `NEW_SAVE_CONFIG` or another display mode.
- Power Loss Recovery is a salvage feature. It does not guarantee a seamless print.
- The firmware updater checks the download size and the archive format. It does not verify a SHA-256 digest or a signature.
- Real-printer testing is done by the maintainer on AD5M hardware. Not every combination of printer, stock firmware version, and configuration can be covered.

## Related engineering documentation

- [Testing and change guide](/openwiki/testing-and-change-guide.md) — detailed
  host, visual, and on-printer test tooling.
- [Architecture overview](/openwiki/architecture.md)
- [Source map](/openwiki/source-map.md)
- [Built-in Klipper patching](/openwiki/workflows/klipper-patching.md)
- [Forge-X Klipper extensions](/openwiki/workflows/klipper-extensions.md)
- [Operations and recovery](/openwiki/workflows/operations-and-recovery.md)
- [Power Loss Recovery](/docs/POWER_LOSS_RECOVERY.md)
