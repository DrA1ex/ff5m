# Forge-X repository quickstart

Forge-X is an unofficial firmware modification for Flashforge Adventurer 5M and 5M Pro printers. It runs an adapted Klipper/Moonraker stack with Fluidd and Mainsail while retaining the stock screen by default; Feather, Guppy, and headless modes are alternatives. The project exists to make the printer's constrained stock platform more stable and configurable and to add printing, calibration, monitoring, and recovery capabilities. See [`README.md`](../README.md) and the user-facing docs in [`docs/`](../docs/).

> **Safety boundary:** this repository changes boot-time behavior and motion/calibration logic on physical printers. Installation, uninstallation, display-mode changes, configuration tuning, and macro changes require checking printer settings and recalibrating bed mesh and Z offset before printing. The project explicitly warns that incorrect use can damage or brick a printer ([`README.md`](../README.md), [`docs/INSTALL.md`](../docs/INSTALL.md)).

## Start here

| If you need to… | Read | Then inspect |
|---|---|---|
| Understand boot, service, and persistence boundaries | [Source map](source-map.md) | `.shell/S00init`, `.shell/S99root`, `.root/start.sh` |
| Change the chroot runtime, services, Moonraker, Fluidd, Mainsail, or web/API exposure | [Chroot environment and web runtime](workflows/chroot-and-web-runtime.md) | `.shell/S99root`, `.root/start.sh`, `moonraker.conf` |
| Change a parameter, display mode, slicer behavior, macros, or calibration flow | [Configuration and printing](workflows/configuration-and-printing.md) | `mod_params.json`, `config/*.cfg`, `macros/base.cfg` |
| Select, diagnose, or develop a screen mode—especially Forge-X’s built-in Feather display | [Screen modes and Feather](workflows/screens-and-feather.md) | `config/*.cfg`, `.shell/commands/zdisplay.sh`, `.py/klipper/plugins/feather_screen.py` |
| Change, diagnose, or safely remove built-in stock-Klipper replacements/plugins | [Built-in Klipper patching](workflows/klipper-patching.md) | `.shell/S00init`, `.shell/uninstall.sh`, `.py/klipper/` |
| Use or change Forge-X Klipper extensions (settings, tare, checksum, audio, recovery, Feather) | [Forge-X Klipper extensions](workflows/klipper-extensions.md) | `.py/klipper/plugins/`, `macros/base.cfg`, `macros/headless.cfg` |
| Install, update, diagnose a boot, fall back to stock, or recover a device | [Operations and recovery](workflows/operations-and-recovery.md) | `docs/INSTALL.md`, `docs/DUAL_BOOT.md`, `docs/UNINSTALL.md`, `docs/RECOVERY.md` |
| Change a UI, camera, Telegram, SSH, OTA, or cloud-related path | [Integrations](integrations.md) | `moonraker.conf`, `.shell/S98camera`, `.shell/S98zssh`, `telegram/` |
| Find the right source or plan a safe code change | [Source map](source-map.md) and [Testing and change guide](testing-and-change-guide.md) | recent Git history and affected hardware workflow |

## Repository model

- **Stock-side hooks:** `.shell/` installs and runs early init scripts in the printer's stock environment.
- **Isolated runtime:** those hooks mount and start a Buildroot-based chroot; `.root/` owns the runtime services inside it.
- **Klipper behavior:** `config/`, `macros/`, `KAMP/`, and `.py/klipper/` select display-specific configs, define G-code workflows, and supply targeted Klipper patches/plugins.
- **Persistent operator state:** the deployed runtime uses `/opt/config/mod_data` for variables, user overrides, logs, and Moonraker data. Configuration metadata lives in `mod_params.json`; the backing mutable file is intentionally outside the source tree.
- **User-facing guidance:** `docs/` remains the installation, slicing, printing, calibration, screen, and recovery authority. This wiki is an engineering map, not a replacement for procedural safety docs.

## Engineering rules of thumb

1. **Preserve safe fallback.** `SKIP_MOD` paths and stock-screen fallback are a recovery feature, not incidental plumbing.
2. **Treat display choice as a workflow change.** Non-stock modes bring up networking and Klipper differently and alter the operator's calibration/printing path.
3. **Keep parameter declaration, macro use, and shell reaction aligned.** A user-facing setting can span `mod_params.json`, `[mod_params]` in `macros/base.cfg`, and `.shell/commands/zchanges.sh`.
4. **Treat the Klipper overlay as a reversible deployment, not a fork.** Replacement modules are symlinked into stock Klipper with `.bak` originals; plugins are linked into `extras/`, and `tune_klipper` is a separate in-place setting. Follow [Built-in Klipper patching](workflows/klipper-patching.md) before changing any of them.
5. **Validate on the actual supported firmware/hardware combination.** The supported stock-firmware range and compatibility analysis are documented in [`docs/INSTALL.md`](../docs/INSTALL.md) and [`docs/FIRMWARE_5x_COMPAT.md`](../docs/FIRMWARE_5x_COMPAT.md).
6. **Do not store secrets in repository docs.** Mutable SSH and user override material belongs to the printer-side `mod_data` paths; this wiki deliberately does not inspect it.

## Current change context

The working tree contains generated OpenWiki files, including the local instructions brief, but source changes are clean relative to `HEAD`. Recent commits concentrated on firmware 5.x compatibility, ZRAM/low-memory operation, safe nozzle-cleaning behavior, and a shaper-calibration hang fix. See the [Source map](source-map.md) and [Testing and change guide](testing-and-change-guide.md) for source locations and validation implications.

## Backlog

- **Release/update-channel policy** — anchors: `version.txt`, `moonraker.conf`; `version.txt` is 1.4.1 while the Forge-X Moonraker updater selects `dev`. The intended release-management policy is not established by the inspected source.
- **External runtime helpers** — anchors: macro calls and `/opt/config/mod_data`; helper implementations such as `zchanges.sh`, `zsend.sh`, and `zprint.sh` are not all tracked in this repository, so their full on-device side effects need source evidence before further documentation.
