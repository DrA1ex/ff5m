# Source map

Use this map to start at the behavior boundary, then follow includes/calls rather than scanning the full repository.

## Primary areas

| Area | Start here | Follow next | Why it matters |
|---|---|---|---|
| Project/product entry | [`README.md`](../README.md) | `docs/INSTALL.md`, `docs/PRINTING.md`, `docs/FIRMWARE_5x_COMPAT.md` | Scope, supported devices, safety posture, endpoints. |
| Stock init and chroot setup | [`.shell/S00init`](../.shell/S00init) | `.shell/common.sh`, `.shell/S55boot`, `.shell/S99root` | Boot modes, mounts, patching, configuration repair, DB migration. |
| Chroot runtime and service lifecycle | [Chroot environment and web runtime](workflows/chroot-and-web-runtime.md) | `.shell/S00init`, `.shell/S99root`, `.root/start.sh`, `.root/S65moonraker`, `.root/S70httpd` | Shared mounts, service ownership, command/tool boundary, and Moonraker/UI topology. |
| API and OTA defaults | [`moonraker.conf`](../moonraker.conf) | `.root/config.json`, `docs/INSTALL.md` | Moonraker listener, machine scripts, web clients, updater sources, user include. |
| Display modes and first-party Feather | [Screen modes and Feather](workflows/screens-and-feather.md) | `config/*.cfg`, `.cfg/init.display.*.cfg`, `.shell/commands/zdisplay.sh`, `.py/klipper/plugins/feather_screen.py` | Selects configuration/boot path; explains Feather’s renderer/status implementation and recovery constraints. |
| Shared G-code behavior | [`macros/base.cfg`](../macros/base.cfg) | `macros/shell.cfg`, `KAMP/KAMP_Settings.cfg`, included macro files | Parameters, safety/motion macros, MD5, KAMP, shell-command bridge. |
| Parameter schema and reactions | [`mod_params.json`](../mod_params.json) | `.shell/commands/zchanges.sh`, `docs/CONFIGURATION.md` | Defaults/options and side effects of supported settings. |
| Klipper patch/plugin overlay | [`.py/klipper/`](../.py/klipper/) | [Built-in Klipper patching](workflows/klipper-patching.md), [Forge-X Klipper extensions](workflows/klipper-extensions.md), `.shell/S00init`, `.shell/uninstall.sh` | Symlinked stock-module replacements with `.bak` rollback plus six configured extra plugins; not a full source fork. |
| Persistence/backup | [`.py/cfg_backup.py`](../.py/cfg_backup.py) | `.shell/commands/zbackup.sh`, `zchanges.sh` | Managed configuration backup/restore and tuning application. |
| Resource/network peripherals | [`.shell/boot/init_swap.sh`](../.shell/boot/init_swap.sh) | `.shell/boot/zram/`, `.shell/S98camera`, `.shell/S98zssh` | Low-memory path, camera, remote access. |
| Packaging/maintenance helpers | `sync.sh`, `sync_remote.sh`, `addMD5.sh`, `addMD5.bat` | release/docs references | Synchronization and slicer checksum tooling. |

## Documentation map

`docs/` is organized by operator task, not internal layer:

- **Getting running:** `INSTALL.md`, `SLICING.md`, `PRINTING.md`, `CONFIGURATION.md`.
- **Safety/calibration:** `CALIBRATION.md`, `MACROS.md`, `FAQ.md`.
- **UI/peripherals:** `SCREEN.md`, `CAMERA.md`, `TELEGRAM.md`, `TYPER.md`.
- **Lifecycle/recovery:** `DUAL_BOOT.md`, `UNINSTALL.md`, `RECOVERY.md`.
- **Support/compatibility:** `FIRMWARE_5x_COMPAT.md`, `MOD_COMPARISON.md`.

When code and a doc disagree, prefer current source for implemented behavior and flag the documentation gap. For calibration and macro behavior specifically, verify claims in the active config/macro chain before recommending hardware operations.

## High-risk boundaries

- **`macros/shell.cfg` → `.shell/commands/*`:** privileged command execution from G-code. Inspect parameter handling and caller macros together.
- **Display config → boot path:** alternate display modes need network/bootstrap behavior not used by default stock mode.
- **`mod_params.json` → mutable `variables.cfg`:** schema/default change may affect existing printers, not merely fresh installs.
- **`.py/klipper/patches` → stock `/opt/klipper`:** compatibility depends on the version/layout documented in `FIRMWARE_5x_COMPAT.md`.
- **`moonraker.conf` → network:** default listener is all interfaces and API-key auth is disabled; changes have deployment/security consequences.

## What is not a product test suite

The GitHub workflow at [`.github/workflows/stale.yml`](../.github/workflows/stale.yml) only marks inactive issues; it is not CI. Vendored `.zsh` test files do not provide Forge-X application coverage. See [Testing and change guide](testing-and-change-guide.md) for the actual validation posture.
