<p align="center">
   <img width="600" src="https://github.com/user-attachments/assets/4e443f9c-02a7-483a-a9da-9afa6db6be2a" />
</p>

# Forge-X: Klipper Firmware Mod for Flashforge Adventurer 5M / 5M Pro

**Forge-X** is a free, open-source (GPL-3.0), _unofficial_ firmware mod for the **Flashforge Adventurer 5M and 5M Pro** (AD5M) 3D printers. It adds an adapted **Klipper** stack with **Moonraker**, **Fluidd**, and **Mainsail** on top of the stock firmware, together with a built-in lightweight touchscreen (**Feather**), calibration tools, print-safety features, Power Loss Recovery, and recovery tools.

Forge-X is designed for this printer's limits: the host has only **128 MiB of RAM**. The mod can be fully removed, it is tested before each release, and it comes with documentation for users and contributors.

> [!CAUTION]
> *If you install this mod on your AD5M (Pro), you risk voiding your warranty or damaging your printer.*
> *After installation or uninstallation, check all printer parameters and perform a full recalibration. Skipping this step may damage your printer.*
> *Proceed at your own risk!*

## Quick facts

| | |
| --- | --- |
| **Supported printers** | Flashforge Adventurer 5M and Adventurer 5M Pro only |
| **Supported stock firmware** | 2.6.5 up to 5.1.x (see [Installation](/docs/INSTALL.md#prerequisites)) |
| **What it is** | A reversible mod layered on top of the stock firmware, not a replacement of the whole system |
| **Klipper** | The FlashForge host Klipper (0.11 generation) with [reviewed fixes and backports from newer upstream Klipper](/docs/KLIPPER.md); the stock MCU firmware is not reflashed |
| **Web interfaces** | Fluidd (`http://<printer_ip>/fluidd/`) and Mainsail (`http://<printer_ip>/mainsail/`), Moonraker API on port `7125` |
| **Local screen** | Feather (default, built in), Stock, Guppy Screen, or Headless; [HelixScreen](/docs/SCREEN.md) is available as an external option |
| **Camera** | [forge-x-streamer](/docs/CAMERA.md): a dedicated low-memory MJPEG service for the AD5M |
| **Recovery** | [Last-known-good boot guard, recovery menu, fallback to Stock, uninstaller and recovery images](/docs/DEVELOPMENT.md#stock-firmware-integration-and-lifecycle-safety) |
| **Updates** | OTA through Moonraker Update Manager for Forge-X (minor versions), Fluidd, Mainsail, and Guppy Screen; major versions are flashed over the existing installation without losing settings ([details](/docs/FAQ.md#how-are-forge-x-updates-delivered)) |
| **Customization** | Own macros, overrides, Feather dialogs, shell commands, startup services, and Entware packages ([details](/docs/EXTENDING.md)) |
| **Access** | SSH as `root` / `root` |
| **License** | GPL-3.0 |

**Jump to:** [Quick start](#tldr) · [Features](#features) · [How Forge-X is built](#how-forge-x-is-built) · [Why Forge-X was developed](#why-forge-x-was-developed) · [Documentation](#documentation) · [Support](#community-and-support)

## Disclaimer

The printer runs Linux, but not the Linux you may be used to. It is **not** Ubuntu, Debian, Fedora, or any other desktop or server distribution. The printer **is not a general-purpose computer**: it runs vendor firmware that uses a Linux kernel as its base.

**Read the documentation** before changing anything. A mistake can leave the printer unable to boot. It can be restored, but in rare cases that **requires additional hardware** or soldering.

- **Do not flash another mod's firmware over Forge-X** (or Forge-X over another mod) unless you clearly understand what you are doing.
- **Do not delete** installation, uninstallation, or recovery **logs**. They can help you restore your printer.

## TL;DR

> [!CAUTION]
> Releases before **1.4.1-11** contain a Smart Park / `MOVE_SAFE` bug.   
> Do **not** enable or use KAMP.   
> Do **not** use `MOVE_SAFE` while relative positioning (`G91`) is active.   
> Upgrade to **1.4.1-11 or later** first.

1. Uninstall any other installed mods first (⚠️ make a backup!).   
2. [Install](/docs/INSTALL.md#flashing-the-firmware-image) the mod.   
3. Update your slicer's [Start and End G-code](/docs/SLICING.md#for-stock-screen).   
4. Update your slicer's [Host Type](/docs/SLICING.md#configuring-moonraker--klipper-connection).
5. Enable [LAN-mode](/docs/PRINTING.md#using-stock-firmware-with-mod) (Stock screen only).
6. Enable the [MD5 check](/docs/SLICING.md#enabling-md5-checksum-validation) for G-code files.
7. Update the mod to new versions using [OTA](/docs/INSTALL.md#ota-updates).
8. **⚠️ Mandatory**: Read about [bed mesh calibration](/docs/CALIBRATION.md#before-you-start).
9. **Recommended**: Enable [Klipper tuning](/docs/CONFIGURATION.md#configuration-macros) to avoid typical MCU errors: `SET_MOD PARAM=tune_klipper VALUE=1`
10. **Recommended**: Enable [config tuning](/docs/CONFIGURATION.md#configuration-macros) for a better first layer: `SET_MOD PARAM=tune_config VALUE=1` (⚠️ requires recalibration afterward).   
11. ⚠️ [Recalibrate](/docs/PRINTING.md#calibration) the bed mesh, input shaper, and Z offset.
12. **Optional**: Learn about the [Z-Offset](/docs/PRINTING.md#z-offset).
13. **Optional**: Enable the mod's [camera](/docs/CAMERA.md#step-3-enable-mods-camera) implementation.   
14. **Optional**: Configure your [LED lighting](/docs/PRINTING.md#led-light-control).
15. **Optional**: Switch to the [Feather or Guppy screen](/docs/SCREEN.md#switching-to-feather-screen).
16. **Optional**: Enable [Bed Collision Protection](/docs/PRINTING.md#bed-collision-protection).
17. **Optional**: Enable [Bed Mesh Validation](/docs/PRINTING.md#bed-mesh-validation).
18. **Optional**: Review and enable [Power Loss Recovery](/docs/POWER_LOSS_RECOVERY.md).

## Get Started

To begin, follow the instructions on the [Installation page](/docs/INSTALL.md). After installation, update your slicer's start and end G-code. See the [Slicing page](/docs/SLICING.md) for details.

> [!WARNING]   
> **Important:** Review your printer settings and recalibrate the bed mesh and Z-offset. Some settings may change during installation, and printing without recalibrating can damage your printer.

Forge-X also includes many features beyond the basic setup. We recommend reading the [Printing](/docs/PRINTING.md) and [Configuration](/docs/CONFIGURATION.md) pages before you start.

> [!NOTE]
> **Advanced: resource and stability tuning.** If the printer works reliably, do not change resource settings. If you see recurring memory pressure, E0011/E0017, or *Timer too close* errors, read [Reducing resource usage](/docs/PRINTING.md#reducing-resource-usage). It explains when to stop optional services, tune Klipper, or use ZRAM instead of relying on eMMC swap, along with the trade-offs.

For additional help, see the [F.A.Q.](/docs/FAQ.md).

After installation, the services are available at:  
- **Moonraker**: `http://<printer_ip>:7125/`  
- **Fluidd**: `http://<printer_ip>/fluidd/`  
- **Mainsail**: `http://<printer_ip>/mainsail/`  
- **SSH credentials**: `root` / `root`  

If you run into problems:  
1. Check the documentation first.
2. If the problem persists, ask for help:
   - Join the [Telegram support group](https://t.me/+ihE2Ry8kBNkwYzhi), or
   - Join the [Discord server](https://discord.gg/K7MH4hAfeX) and go to Forums → mods-and-projects → Forge-X.
3. Open a [GitHub issue](https://github.com/DrA1ex/ff5m/issues) only if you are **certain** it is a bug.

## Features

<p align="center">
<img width="400" src="https://github.com/user-attachments/assets/7837ec81-6a88-4a1b-81d1-2266a87025bf" />
<img width="400" src="https://github.com/user-attachments/assets/48b40175-1928-439c-baa6-78ae157efaf3" />
<img width="400" src="https://github.com/user-attachments/assets/51d381fd-e98c-44c4-9518-c4a64c88bcf4" />
<img width="400" src="https://github.com/user-attachments/assets/2e0fafd5-15b3-4e7e-ad26-6cf7c67613fe" />
<img width="400" src="https://github.com/user-attachments/assets/28a0ef3f-e7cf-4648-aff8-7f273d2b055b" />
</p>

### Screens and interface

- Fully interactive **Feather** screen, with local and USB G-code browsing and print control
- **Stock** screen, with the option to disable it completely and switch to a lighter alternative to reduce resource consumption
- Adapted **Guppy Screen**
- **Fluidd** and **Mainsail** web interfaces
- **Moonraker** API

### Klipper and reliability

- **Klipper** with [bug fixes and hardening adapted specifically for the AD5M](/docs/KLIPPER.md)
- Fix for the **Move queue overflow (E0017)** error
- Fix for the **Communication Timeout (E0011)** error
- Fix for the heavy-G-code **Timer too close** starvation case
- **Last-known-good boot guard and Stock fallback**: an updated runtime is accepted before the early boot guard is replaced, and Recovery/rollback paths handle failed initialization ([details](/docs/DEVELOPMENT.md#stock-firmware-integration-and-lifecycle-safety))
- **Dual boot** with the stock FlashForge software, and an early recovery menu ([details](/docs/DUAL_BOOT.md))
- **Backup** and **Restore** of the printer's configuration

### Calibration and print quality

- Guided Z-offset, bed mesh, extruder, PID, and Input Shaper calibration
- Enhanced **Shaper Calibration** with automatic plot generation
- Easy **Bed Level Screw Tuning**
- Adaptive bed meshing with **KAMP**, including Smart Parking
- Improved **Clear Nozzle** algorithm

### Print safety

- **Bed collision protection** and a **nozzle contact check** based on the load cell
- **Bed mesh validation** before printing
- Built-in **MD5** checks for G-code files
- Original **[Power Loss Recovery](/docs/POWER_LOSS_RECOVERY.md)**, developed specifically for the AD5M

### Connectivity and extras

- Dedicated low-memory **[forge-x-streamer](/docs/CAMERA.md)** camera service with bounded buffering, device autodetection, automatic camera reconnect, live V4L2 image controls, and source-level regression tests
- **Timelapse** support through the [Moonraker Telegram bot](https://github.com/nlef/moonraker-telegram-bot) running on an external host
- **OTA** updates for the firmware, Fluidd, Mainsail, and Guppy Screen
- **Root** access (with zsh and oh-my-zsh)
- **Buzzer** support, including playing melodies from MIDI files or notes
- Customized, dedicated Linux environment based on **Buildroot**
- **Entware** package manager for installing additional software

### Engineering

- **Regression-tested development and release process**: automated host tests, visual UI regression, and real-printer physical regression before releases ([details](/docs/DEVELOPMENT.md))

## How Forge-X is built

- **Supported hardware.** Forge-X supports only the Adventurer 5M and 5M Pro. It is designed around this hardware: 128 MiB of RAM, the FlashForge Klipper 0.11 base, two MCUs, the load-cell bed sensor, and the stock boot process.
- **Failure handling.** If an update or configuration change fails, the next boot starts the stock firmware instead of repeating the failed start, and the Recovery menu is available. Every patched Klipper file keeps a `.bak` original, and uninstall restores the configuration and Klipper files before removing the runtime. See [Stock firmware integration and lifecycle safety](/docs/DEVELOPMENT.md#stock-firmware-integration-and-lifecycle-safety).
- **Testing.** Before each release Forge-X goes through automated host tests, rendered G-code macro tests, visual UI tests, on-printer tests, and physical print tests. The tests are in the [`tests/`](/tests) directory, and the process is described in [Development, testing, and release validation](/docs/DEVELOPMENT.md).
- **Memory.** Feather uses roughly 1–2 MB of RAM, compared with roughly 10–20 MB for the Stock screen. The camera service, the swap and ZRAM options, and Moonraker startup are also tuned for low memory. See [Reducing resource usage](/docs/PRINTING.md#reducing-resource-usage).
- **Klipper.** Forge-X keeps the Klipper host and MCU firmware that FlashForge ships and backports fixes and features from newer upstream Klipper into the host code: the `Timer too close` starvation fix, the E0011 / E0017 mitigations, Adaptive Pressure Advance, and correct file offsets for G-code with non-ASCII characters. See [Klipper fixes and AD5M-specific hardening](/docs/KLIPPER.md) and [Why doesn't Forge-X use Klipper 0.13?](/docs/FAQ.md#why-doesnt-forge-x-use-klipper-013)
- **Customization.** You can add your own macros, override Forge-X macros and settings, show your own dialogs on the Feather screen, run your own programs and startup services, and install Linux packages with Entware. See [Customizing and extending Forge-X](/docs/EXTENDING.md).
- **Documentation.** The user guides cover every workflow, and [OpenWiki](/openwiki/quickstart.md) describes the code for contributors.

## Why Forge-X Was Developed

Forge-X was developed to provide a more reliable, maintainable, and complete software environment for the Adventurer 5M, while staying compatible with the printer's existing hardware and firmware base.

The AD5M hardware can produce good prints, but the platform has practical limitations. The host has only **128 MiB of RAM**, the bundled Klipper is from the older 0.11 generation, and the stock firmware exposes only a small part of the functionality and integrations that users expect from a Klipper-based printer.

Forge-X is designed around those constraints instead of assuming the printer has the resources of a newer platform. Memory usage, CPU load, background services, UI components, camera streaming, and Klipper behavior are all treated as part of the reliability problem. When newer software or upstream behavior is useful, it is adapted to fit the available hardware budget.

The project also addresses limitations that have nothing to do with raw hardware performance: missing Klipper workflows, long-standing platform-specific bugs, limited recovery options, weak diagnostics, and the lack of an extensible environment for users who want to customize the printer or build on top of it.

For that reason, Forge-X treats testing, predictable failure handling, recovery, documentation, and maintainability as part of the implementation. The code follows common software-engineering practice and comes with user and engineering documentation, so additional integrations and platform-specific ports can be built without relying on undocumented project internals.

In short, Forge-X exists to make better use of the hardware already in the AD5M, while respecting its resource limits and replacing software limitations with a more open, testable, and maintainable environment.

## Documentation

### User guides

- [Installation](/docs/INSTALL.md)
- [Configuration](/docs/CONFIGURATION.md)
- [Customizing and extending Forge-X](/docs/EXTENDING.md)
- [Slicing](/docs/SLICING.md)
- [Printing](/docs/PRINTING.md)
- [Macros](/docs/MACROS.md)
- [Calibration](/docs/CALIBRATION.md)
- [F.A.Q.](/docs/FAQ.md)
- [Screens (Feather, Guppy, Stock, Headless)](/docs/SCREEN.md)
- [Camera](/docs/CAMERA.md)
- [Telegram Bot and Timelapse](/docs/TELEGRAM.md)
- [Power Loss Recovery](/docs/POWER_LOSS_RECOVERY.md)
- [Klipper fixes and AD5M-specific hardening](/docs/KLIPPER.md)
- [Dual boot and recovery menu](/docs/DUAL_BOOT.md)
- [Uninstall](/docs/UNINSTALL.md)
- [Firmware Recovery guide](/docs/RECOVERY.md)

### Engineering documentation

- [Development, testing, and release validation](/docs/DEVELOPMENT.md)
- [Engineering facts and where to verify them](/docs/DEVELOPMENT.md#engineering-facts-and-where-to-verify-them)
- [Release policy](/docs/DEVELOPMENT.md#release-policy)
- [Contributor guidelines](CONTRIBUTING.md)

The [OpenWiki](openwiki/quickstart.md) is a code-oriented guide for contributors and advanced users. It complements the user guides above; for installation, calibration, and recovery procedures, follow the user guides.

- [Architecture overview](openwiki/architecture.md)
- [Source map](openwiki/source-map.md)
- [Chroot environment and web runtime](openwiki/workflows/chroot-and-web-runtime.md)
- [Configuration and printing workflows](openwiki/workflows/configuration-and-printing.md)
- [Screen modes and Feather](openwiki/workflows/screens-and-feather.md)
- [Built-in Klipper patching](openwiki/workflows/klipper-patching.md)
- [Forge-X Klipper extensions](openwiki/workflows/klipper-extensions.md)
- [Operations and recovery](openwiki/workflows/operations-and-recovery.md)
- [Integrations](openwiki/integrations.md)
- [Testing and change guide](openwiki/testing-and-change-guide.md)

## Community and support

- [Telegram support group](https://t.me/+ihE2Ry8kBNkwYzhi)
- [Discord](https://discord.gg/K7MH4hAfeX) (Forums → mods-and-projects → Forge-X)

## Completed milestones

- [x] Feather Screen: an ultra-lightweight interactive UI for essential local control
- [x] Klipper fixes for G-code that contains Unicode characters (for example, non-English object names)
- [x] Mainsail OTA: a patched implementation that works correctly with navigation and OTA updates
- [x] Power Loss Recovery for non-Stock screens
- [x] Integration and adaptation of Guppy Screen for the AD5M
- [x] A custom interactive Feather screen built specifically for the AD5M running Forge-X

## Known limitations

- Only the Adventurer 5M and 5M Pro are supported.
- The Klipper host is the FlashForge 0.11 generation. Newer Klipper features are available after they have been backported ([list](/docs/KLIPPER.md)).
- Feather covers the main local workflows. Unrestricted G-code, file deletion, static or enterprise Wi-Fi, and detailed diagnostics need Fluidd or Mainsail.
- Power Loss Recovery is a salvage feature, not a guarantee of a seamless print.
- Real-printer testing is done on AD5M hardware by the maintainer, so not every combination of stock firmware and configuration can be covered.

More details are in [Known limitations](/docs/DEVELOPMENT.md#known-limitations) and the [release policy](/docs/DEVELOPMENT.md#release-policy).

## Support Forge-X

Forge-X is free and open source, and it is built for the community. Developing new features, writing documentation, testing on real hardware, and answering questions all take a lot of time. If Forge-X is useful to you, you can support the project with a donation. It helps keep the mod maintained and improving.

- **[Boosty (Donate)](https://boosty.to/dra1ex/donate)**
- **[Boosty (Subscribe)](https://boosty.to/dra1ex)**
- **[Cryptocurrency donations](https://telegra.ph/FORGE-X-10-24)**

## Credits

Forge-X builds on the work of many people and projects:

- Thanks to the [Klipper Mod](https://github.com/xblax/flashforge_ad5m_klipper_mod) developers for their excellent work.
- Forge-X is based on ZMod by [ghzserg](https://github.com/ghzserg).
- Thanks to the Klipper and Moonraker communities for their ongoing development.
- Thanks to the Russian-speaking FlashForge Adventurer 5M Telegram community: [@FF_5M_5M_Pro](https://t.me/FF_5M_5M_Pro).
- Thanks to [@Zero](https://www.youtube.com/@zerodotcmd) for the logo.

Thanks to the authors of these open-source fonts:
- [Roboto Font](https://fonts.google.com/specimen/Roboto)
- [JetBrains Mono Font](https://www.jetbrains.com/lp/mono)
- [Typicons Icons Font](https://www.s-ings.com/typicons/)

### Special Thanks

Thank you to everyone who has supported this project with a donation. Your support gives the project time to grow, and every contribution, whatever its size, is appreciated.

Supporters:   
**906Prints, MattArmfield, Stormage, Spud, Luigisvc, slydog43, Никита (motionpix), CHaucke, Andrew Popow, K3D // Dmitry Sorkin, D T, Kurt LaRue, jollyroger1789**

#### Anonymous donations

Thank you also to everyone who supported the project anonymously with cryptocurrency.
