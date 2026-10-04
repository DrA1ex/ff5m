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

## Validation layers

| Layer | What it checks | Who runs it |
| --- | --- | --- |
| Host tests | Macros, Klipper overlays and backports, Power Loss Recovery state, boot recovery, configuration migration, backup and restore, firmware installation, Feather logic, operation contexts, network and Moonraker integration. | GitHub Actions and developers |
| Macro rendering | Macros are rendered against Klipper status snapshots and the generated commands are checked, not only the template syntax. | GitHub Actions and developers |
| Visual regression | Feather screenshots of stable pages and important non-default states. A correct-looking screen can still behave wrongly, so it complements the logic tests. | Maintainer |
| Printer regression | Real workflows on an Adventurer 5M (list below). | Maintainer |

Run the host tests with:

```bash
python tests/run_host_tests.py --verbose
```

Passing host tests do not replace a printer run for changes that affect motion, heaters, probing, the display, or timing.

### Printer regression

An opt-in runner executes workflows through the real Klipper, Moonraker, and Feather stack:

- homing and reversible XYZ movement;
- heating and cooling;
- bed screw tuning and full bed mesh;
- probe-based Z-offset calibration;
- KAMP and nozzle cleaning;
- mesh validation;
- a real sliced print, with UI pause, resume, and cancel;
- creating and restoring a Power Loss Recovery checkpoint;
- filament load, purge, unload, and cold pull.

During the run it can record motion-buffer data, MCU statistics, temperatures, print state, operation context, memory, CPU, scheduler data, and per-process usage. A workflow that finishes but causes reactor stalls or memory pressure is still a regression.

The suites move and heat the printer, so they have explicit preconditions and confirmation levels. See the [Testing and change guide](/openwiki/testing-and-change-guide.md).

## Release validation

Before a release, the maintainer runs:

1. the host test suite;
2. visual regression for UI changes;
3. full printer regression;
4. a real print for changes that can affect printing or recovery;
5. a review of failures and collected logs.

Printer and visual regression are maintainer steps, not GitHub Actions jobs.

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
