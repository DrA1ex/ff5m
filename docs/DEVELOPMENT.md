# Development, testing, and release validation

Forge-X changes a real printer: motion, heaters, probing, configuration, recovery, storage, and the local UI. Testing is part of every change and every release. CI alone is not enough for code that can move or heat the printer.

- Commands, test formats, telemetry, and individual printer suites: [Testing and change guide](/openwiki/testing-and-change-guide.md).
- Requirements for pull requests: [Contributor guidelines](/CONTRIBUTING.md).
- User-level customization (`user.cfg` overrides, own macros, dialogs, services): [Customizing and extending Forge-X](EXTENDING.md).

## Stock firmware integration and lifecycle safety

Forge-X is a reversible layer on top of the FlashForge firmware. The stock firmware stays available as a fallback, and the mod adds its chroot runtime, Moonraker, Klipper extensions, display modes, and printer configuration on top.

**Boot guard.** The installed `/etc/init.d/S00init` is the last-known-good boot guard. An update replaces the mod files, but not the guard:

1. the update installs the new Forge-X runtime;
2. the next boot starts it through the old guard;
3. the new initializer reports that it is ready;
4. only then is the new guard committed.

Failure markers are written before optional mod work starts. If normal initialization is interrupted or never reports ready, the next boot starts the stock firmware instead of repeating the broken start. An interrupted Recovery session also falls back to Stock.

**Configuration.** Changes go through [`cfg_backup.py`](/.py/cfg_backup.py), the same tool used for backup and restore, not through ad-hoc edits to `printer.cfg`. Each display mode (Stock, Feather, Guppy, Headless) has a configuration delta in `.cfg/init.display.*.cfg` and a restore profile, so mode switching, boot repair, and uninstall use the same files.

**Rollback.**

- Replaced stock Klipper modules keep `.bak` copies, and uninstall restores them.
- Display and configuration changes have restore profiles.
- Settings can be backed up and restored.
- The Recovery menu can check the filesystem, create backups, reset settings, uninstall Forge-X, and flash firmware images.
- Uninstall restores the printer configuration and Klipper files before it removes the mod.

Boot recovery, firmware installation, configuration migration, and uninstall are covered by host tests. In normal failure cases, a Stock or Recovery boot stays available without UART or FEL. The exact steps and source files are in the [engineering facts](#engineering-facts-and-where-to-verify-them) table below. See also the [Architecture overview](/openwiki/architecture.md), [Operations and recovery](/openwiki/workflows/operations-and-recovery.md), and the [Firmware Recovery guide](/docs/RECOVERY.md).

## Testing

Every pull request goes through the same checks, and the [pull request template](/.github/pull_request_template.md) asks for their results:

- **every change:** [host tests](#host-tests);
- **motion, heating, probing, bed mesh, Z-offset, filament, pause/resume/cancel, Power Loss Recovery, Klipper timing, boot, installation, update, or recovery:** [printer regression](#printer-regression), attached as an archive;
- **anything visible on the Feather screen:** [visual regression](#visual-regression), attached as an archive;
- **a new mechanism, or behavior that no existing test covers:** [new coverage](#adding-test-coverage).

If you cannot run a check (for example, you have no printer), say so in the pull request. A maintainer runs it before the change is merged.

### Host tests

The `tests/` directory contains dozens of host test modules for Forge-X's own code:

- G-code macros;
- Klipper overlays, backports, and reactor patches;
- Power Loss Recovery state handling;
- boot recovery, configuration migration, backup, and restore;
- firmware installation and update paths;
- Feather state, actions, workflows, safety checks, rendering, and input;
- operation contexts;
- network and Moonraker integration;
- resource monitoring and the printer-regression tooling.

GitHub Actions runs them on every push. Run them locally with:

```bash
python tests/run_host_tests.py --verbose
```

Macros are not considered tested just because the template parses. The tests render them against Klipper status snapshots and check the generated commands.

Passing host tests do not replace a printer run for changes that affect motion, heaters, probing, the display, or timing.

### Printer regression

The printer regression runs real workflows on an Adventurer 5M through Klipper, Moonraker, and Feather:

- homing and reversible XYZ movement;
- heating and cooling;
- bed screw tuning and a full bed mesh;
- probe-based Z-offset calibration;
- KAMP and nozzle cleaning;
- mesh validation;
- a real sliced print, with UI pause, resume, and cancel;
- creating and restoring a Power Loss Recovery checkpoint;
- filament load, purge, unload, and cold pull.

During the run it records motion-buffer data, MCU statistics, temperatures, print state, operation context, memory, CPU, scheduler data, and per-process usage. A workflow that finishes but causes reactor stalls or memory pressure is still a regression.

The maintainer runs it before every release. Pull requests that touch hardware-related code must include a run as well.

#### Before you start

- The printer is calibrated, idle, in Feather mode, and the bed is empty.
- Someone watches the printer for the whole run. The tests move and heat it.
- Your changes are deployed to the printer. After changing Feather Python modules, restart the Klipper process completely (a normal `RESTART` can keep the old module loaded):

  ```sh
  /opt/config/mod/.shell/restart_klipper.sh --hard
  ```

- The computer that runs the command has Python 3, `ssh`, and `scp`, and can log in to the printer over SSH.

#### Run without video (enough for a pull request)

A video recording is not required for a pull request. Run from the repository root:

```bash
python3 -m tests.printer_regression \
  --printer <printer-host> \
  --suite core \
  --no-video \
  --confirm-unattended-physical-test
```

`--no-video` skips camera recording and video assembly, so FFmpeg is not needed. The report, telemetry, resource data, screenshots, and printer logs are still collected.

Choose the suites that match your change:

- `core`: the main physical suite (homing, movement, heating, bed screws, mesh, Z-offset). This is the default for most changes.
- `all`: `core`, then a real test print with pause, resume, cancel, and a Power Loss Recovery checkpoint. Use it for changes that can affect printing or recovery. The printed model stays on the bed.
- `material`: filament load, purge, unload, and cold pull. Run it separately, on a clear bed.
- Single parts: `motion`, `heat`, `screws`, `mesh`, `z`, or the screen-only `ui`, `component`, and `render`.

The command prints the path of the run directory, by default `tests/artifacts/printer-runs/<timestamp>/`. Pack the whole directory and attach it to the pull request:

```bash
cd tests/artifacts/printer-runs
zip -r printer-regression.zip <timestamp>
```

If the archive is too large to attach, upload it elsewhere and add the link.

#### Run from the printer console

If you cannot run the command from a computer, start the main suite from the Fluidd or Mainsail console:

```gcode
_FEATHER_UI_TEST ACTION=RUN SUITE=FULL CONFIRM=1
```

Single phases: `SUITE=UI`, `RENDER`, `MOTION`, `HEAT`, `SCREWS`, `MESH`, or `Z`. Check progress with `_FEATHER_UI_TEST ACTION=STATUS`, stop with `_FEATHER_UI_TEST ACTION=ABORT`.

The results are saved on the printer in `/data/feather-ui-tests/<timestamp>-<suite>/`. Download that directory (for example with `scp -r` or from Fluidd), pack it as a `.zip`, and attach it. This run does not collect the telemetry and per-process resource data that the computer run adds, so the computer run is preferred.

All options, safety checks, and output formats are in the [Testing and change guide](/openwiki/testing-and-change-guide.md).

### Visual regression

Feather changes are also checked on screenshots: every stable page, plus the important non-default states listed in [`tests/visual_checks/scenarios.json`](/tests/visual_checks/scenarios.json). A vision model compares each screenshot with its text description in [`tests/visual_checks/expectations.json`](/tests/visual_checks/expectations.json) and reports layout and content problems. A screen that looks right can still behave wrongly, so visual checks complement the logic tests rather than replace them.

Any change that is visible on the Feather screen needs a visual regression run. You need:

- a checkout of the `feather-ui-designer` repository, the source of Feather's UI framework (see [Feather runtime](/openwiki/workflows/feather-runtime.md#framework-dependency-and-updates));
- for the model review, a local OpenAI-compatible server with a vision model (for example LM Studio). Without it, the run still collects the screenshots, and the maintainer runs the review.

Run against the printer (idle, Feather mode, your changes deployed):

```bash
python3 -m tests.visual_checks.regression \
  --mode hybrid \
  --designer-root /path/to/feather-ui-designer \
  --printer-host <printer-host> \
  --confirm-printer-idle \
  --model <loaded-vision-model> \
  --enable
```

Without a vision model, leave out `--model` and `--enable`. Without a printer, use `--mode designer` and leave out `--printer-host` and `--confirm-printer-idle`.

The command prints the run directory, by default `tests/artifacts/ui-regression/<timestamp>/`. Pack the whole directory and attach it to the pull request:

```bash
cd tests/artifacts/ui-regression
zip -r visual-regression.zip <timestamp>
```

How to read the report is described in the [Testing and change guide](/openwiki/testing-and-change-guide.md#development-only-semantic-screenshot-checks).

### Adding test coverage

A new mechanism, or behavior that no existing test covers, needs new tests in the same pull request:

- **Logic:** add a host test in `tests/` that fails without your change. Name it `tests/test_<area>.py` so `python tests/run_host_tests.py` picks it up, and use the existing modules for the same area as examples.
- **Hardware workflow:** if the change adds a workflow the printer regression does not exercise, add a phase or scenario to the Feather test runner in [`.py/klipper/plugins/feather_ui_test/`](/.py/klipper/plugins/feather_ui_test/). Keep the existing safety checks: idle printer, explicit confirmation, and cleanup of heaters, motors, and temporary state.
- **New Feather page:** the visual regression finds it automatically, but it has no description yet. The run stops with `needs_baseline` and writes `expectations.candidate.json` to the run directory. Copy the new case into [`tests/visual_checks/expectations.json`](/tests/visual_checks/expectations.json) and edit it so that it describes what must be on the screen:

  ```json
  "default-my-page": {
    "description": "One sentence about the page and its state.",
    "required": [
      "title and BACK action",
      "the main values or controls the page must show"
    ],
    "forbidden": [
      "blank frame",
      "overlapping controls",
      "clipped important text"
    ],
    "allowed_variations": [
      "theme colors",
      "font rasterization"
    ]
  }
  ```

  The model checks the screenshot against `required` and `forbidden`. Write each item as something visible on the screen, not as an implementation detail.

- **New state of a page** (a dialog, a warning, an active or finished workflow): add a case to [`tests/visual_checks/scenarios.json`](/tests/visual_checks/scenarios.json) with the page key and the state values, then add its description to `expectations.json` as above.

## Release validation

Before a release, the maintainer runs:

1. the host test suite;
2. visual regression for UI changes;
3. full printer regression;
4. a real print for changes that can affect printing or recovery;
5. a review of failures and collected logs.

Printer and visual regression need a real printer, so they run outside GitHub Actions.

This reduces the chance that users are the first to hit a known class of bug, but it does not prove that every printer, filament, slicer profile, or firmware combination works. Forge-X is still an unofficial mod.

## Engineering facts and where to verify them

This table lists how Forge-X handles the risky parts of changing a printer's firmware, and where each item can be checked in the repository.

| Topic | What Forge-X does | Where to check |
| --- | --- | --- |
| Boot after a failed update | The installed early boot guard stays in place until the new runtime reports that it is ready. Markers are written before initialization starts. If initialization is interrupted or never becomes ready, the next boot starts the stock firmware. | [`.shell/S00init`](/.shell/S00init), [`tests/test_boot_recovery.py`](/tests/test_boot_recovery.py) |
| Replacing the boot guard | The new guard is checked with `bash -n`, copied to a temporary file, checked again, synced, moved into place with an atomic `mv`, and synced again. | [`.shell/S00init`](/.shell/S00init) |
| Changes to printer configuration | A parser changes the Klipper configuration. It understands sections, parameters, includes, and deferred includes. Display modes are declarative profiles, and backup and restore write through a temporary file and rename. A verify mode checks the configuration without changing it. `avoid_writes` skips writing when nothing changes. | [`.py/cfg_backup.py`](/.py/cfg_backup.py), [`.cfg/`](/.cfg/), [`tests/test_cfg_backup.py`](/tests/test_cfg_backup.py) |
| Klipper patches | Every replaced Klipper file keeps a `.bak` copy, plugins are linked into `extras/`, and uninstall reverses both. | [`.shell/klipper_overlay.sh`](/.shell/klipper_overlay.sh), [`tests/test_klipper_overlay.py`](/tests/test_klipper_overlay.py), [Klipper fixes](/docs/KLIPPER.md) |
| Klipper patch history | The replaced Klipper files are published as Git history on top of Klipper `v0.11.0` and the stock FlashForge changes. A manifest records the SHA-256 of every shipped file and the commit of the history repository. | [`DrA1ex/klipper-ad5m`](https://github.com/DrA1ex/klipper-ad5m), [`docs/klipper-ad5m-manifest.json`](/docs/klipper-ad5m-manifest.json), [`tests/verify_klipper_fork.py`](/tests/verify_klipper_fork.py), [`tests/test_klipper_fork_manifest.py`](/tests/test_klipper_fork_manifest.py) |
| MCU firmware | Not reflashed. | [Klipper fixes](/docs/KLIPPER.md) |
| Parking and travel limits | One macro, `MOVE_SAFE`, holds the allowed X/Y/Z limits and clamps moves. KAMP Smart Park uses it. | [`macros/base.cfg`](/macros/base.cfg), [`KAMP/Smart_Park.cfg`](/KAMP/Smart_Park.cfg) |
| Power Loss Recovery | The implementation is Python source in the repository, with host tests and a physical regression check. | [`.py/klipper/plugins/resurrection.py`](/.py/klipper/plugins/resurrection.py), [`resurrection_state.py`](/.py/klipper/plugins/resurrection_state.py), [`tests/test_resurrection.py`](/tests/test_resurrection.py) |
| Automated tests | Dozens of host test modules cover boot recovery, installation, Klipper patches, macros, Feather, and Power Loss Recovery. GitHub Actions runs `python tests/run_host_tests.py`. | [`tests/`](/tests/), [`.github/workflows/tests.yml`](/.github/workflows/tests.yml) |
| Real-printer tests | A regression runner executes real workflows on an AD5M. It runs before every release and for pull requests that touch hardware-related code, not as a cloud CI job. | [`tests/printer_regression.py`](/tests/printer_regression.py), [Release validation](#release-validation) |
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
- Real-printer testing is done by the maintainer before releases. Not every combination of printer, stock firmware version, and configuration can be covered.

## Related engineering documentation

- [Testing and change guide](/openwiki/testing-and-change-guide.md): commands and details of the host, visual, and printer tests.
- [Contributor guidelines](/CONTRIBUTING.md)
- [Architecture overview](/openwiki/architecture.md)
- [Source map](/openwiki/source-map.md)
- [Built-in Klipper patching](/openwiki/workflows/klipper-patching.md)
- [Forge-X Klipper extensions](/openwiki/workflows/klipper-extensions.md)
- [Operations and recovery](/openwiki/workflows/operations-and-recovery.md)
- [Power Loss Recovery](/docs/POWER_LOSS_RECOVERY.md)
