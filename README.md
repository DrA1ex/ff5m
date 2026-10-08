<p align="center">
   <img width="600" src="https://github.com/user-attachments/assets/4e443f9c-02a7-483a-a9da-9afa6db6be2a" />
</p>

# Forge-X: Klipper Firmware Mod for Flashforge Adventurer 5M / 5M Pro

**Forge-X** is a free, _unofficial_ firmware mod for the **Flashforge Adventurer 5M and 5M Pro** (AD5M) 3D printers. It adds an adapted **Klipper** with **Moonraker**, **Fluidd**, and **Mainsail** on top of the stock firmware, together with a lightweight touchscreen (**Feather**), calibration tools, print-safety features, Power Loss Recovery, and recovery tools. The mod can be fully removed.

> [!CAUTION]
> *If you install this mod on your AD5M (Pro), you risk voiding your warranty or damaging your printer.*
> *After installation or uninstallation, check all printer parameters and perform a full recalibration. Skipping this step may damage your printer.*
> *Proceed at your own risk!*

## Before you install

The printer runs vendor firmware on a Linux kernel. It is **not** a general-purpose computer and not Ubuntu, Debian, or any other desktop or server distribution.

**Read the documentation** before changing anything. A mistake can leave the printer unable to boot. It can be restored, but in rare cases that **requires additional hardware** or soldering.

- Do not flash another mod's firmware over Forge-X (or Forge-X over another mod) unless you clearly understand what you are doing.
- Do not delete installation, uninstallation, or recovery logs. They can help you restore the printer.

## Quick facts

- **Printers**: Flashforge Adventurer 5M and Adventurer 5M Pro.
- **Stock firmware**: every version from 2.6.5 up to 5.1.7 (the newest checked). The version does not matter unless you use the Stock screen; see [Compatibility](/docs/COMPATIBILITY.md).
- **What it is**: a reversible mod on top of the stock firmware, not a replacement of the whole system.
- **Klipper**: the FlashForge host Klipper (0.11 generation) with [fixes and backports from newer upstream Klipper](/docs/KLIPPER.md). The stock MCU firmware is not reflashed.
- **Web interfaces**: Fluidd (`http://<printer_ip>/fluidd/`), Mainsail (`http://<printer_ip>/mainsail/`), and the Moonraker API on port `7125`. SSH: `root` / `root`.
- **Local screen**: Feather (default), Stock, Guppy Screen, or Headless. [HelixScreen](/docs/SCREEN.md) is available as an external option.
- **Recovery**: boot guard with fallback to Stock, recovery menu, uninstaller, and recovery images ([details](/docs/DEVELOPMENT.md#stock-firmware-integration-and-lifecycle-safety)).
- **License**: GPL-3.0 for Forge-X and its Klipper components. The native binary components have a [separate license](/LICENSE-BINARIES.md).

## Quick start

> [!CAUTION]
> Releases before **1.4.1-11** have a Smart Park / `MOVE_SAFE` bug. Do not use KAMP, and do not use `MOVE_SAFE` while relative positioning (`G91`) is active. Upgrade to **1.4.1-11 or later** first.

**Required**

1. Uninstall any other installed mods first (⚠️ make a backup).
2. [Install](/docs/INSTALL.md#flashing-the-firmware-image) the mod.
3. In the slicer, update the [Start and End G-code](/docs/SLICING.md#for-stock-screen) and the [Host Type](/docs/SLICING.md#configuring-moonraker--klipper-connection).
4. Stock screen only: enable [LAN-mode](/docs/PRINTING.md#using-stock-firmware-with-mod).
5. Enable the [MD5 check](/docs/SLICING.md#enabling-md5-checksum-validation) for G-code files.
6. ⚠️ Read about [bed mesh calibration](/docs/CALIBRATION.md#before-you-start), then [recalibrate](/docs/PRINTING.md#calibration) the bed mesh, input shaper, and Z offset. Some settings change during installation, and printing without recalibration can damage the printer.

**Recommended**

- `SET_MOD PARAM=tune_klipper VALUE=1`: [Klipper tuning](/docs/CONFIGURATION.md#configuration-macros) to avoid typical MCU errors.
- `SET_MOD PARAM=tune_config VALUE=1`: [config tuning](/docs/CONFIGURATION.md#configuration-macros) for a better first layer (⚠️ recalibrate afterward).

**Optional**

- [Z-Offset](/docs/PRINTING.md#z-offset), [LED lighting](/docs/PRINTING.md#led-light-control), [Bed Collision Protection](/docs/PRINTING.md#bed-collision-protection), [Bed Mesh Validation](/docs/PRINTING.md#bed-mesh-validation).
- The mod's [camera](/docs/CAMERA.md#step-3-enable-mods-camera).
- The [Feather or Guppy screen](/docs/SCREEN.md#switching-to-feather-screen).
- [Power Loss Recovery](/docs/POWER_LOSS_RECOVERY.md) is enabled by default; review its settings.

**Updates.** Forge-X, Fluidd, Mainsail, and Guppy Screen update over the air ([OTA](/docs/INSTALL.md#ota-updates)).

**Reading next.** The [Printing](/docs/PRINTING.md) and [Configuration](/docs/CONFIGURATION.md) pages cover the features beyond the basic setup. If the printer works reliably, do not change resource settings. If you see recurring memory pressure, E0011/E0017, or *Timer too close* errors, read [Reducing resource usage](/docs/PRINTING.md#reducing-resource-usage).

**If you run into problems:** check the [F.A.Q.](/docs/FAQ.md), then ask in the [Telegram support group](https://t.me/+ihE2Ry8kBNkwYzhi) or on the [Discord server](https://discord.gg/K7MH4hAfeX) (Forums → mods-and-projects → Forge-X). Open a [GitHub issue](https://github.com/DrA1ex/ff5m/issues) only if you are certain it is a bug.

## Features

<p align="center">
<img width="400" src="https://github.com/user-attachments/assets/7837ec81-6a88-4a1b-81d1-2266a87025bf" />
<img width="400" src="https://github.com/user-attachments/assets/48b40175-1928-439c-baa6-78ae157efaf3" />
<img width="400" src="https://github.com/user-attachments/assets/51d381fd-e98c-44c4-9518-c4a64c88bcf4" />
<img width="400" src="https://github.com/user-attachments/assets/2e0fafd5-15b3-4e7e-ad26-6cf7c67613fe" />
<img width="400" src="https://github.com/user-attachments/assets/28a0ef3f-e7cf-4648-aff8-7f273d2b055b" />
</p>

### Screens and interface

- **Feather**: local touchscreen with G-code browsing (local and USB) and print control
- **Stock** screen, which can be switched off in favor of a lighter one to save memory
- Adapted **Guppy Screen**
- **Fluidd** and **Mainsail** web interfaces, **Moonraker** API

### Klipper and reliability

- [Fixes and hardening for the AD5M](/docs/KLIPPER.md), including the E0017 (move queue overflow) and E0011 (communication timeout) mitigations and a fix for the `Timer too close` shutdowns caused by dense G-code
- [Dual boot](/docs/DUAL_BOOT.md) with the stock FlashForge software
- Backup and restore of the printer configuration

### Calibration and print quality

- Guided Z-offset, bed mesh, extruder, PID, and input shaper calibration, with plots for shaper calibration
- Bed level screw tuning
- Adaptive bed meshing with **KAMP**, including Smart Park
- Revised Clear Nozzle algorithm

### Print safety

- Bed collision protection and a nozzle contact check based on the load cell
- Bed mesh validation before printing
- MD5 checks for G-code files
- [Power Loss Recovery](/docs/POWER_LOSS_RECOVERY.md), written for the AD5M

### Connectivity and extras

- **[forge-x-streamer](/docs/CAMERA.md)**: a low-memory camera service with device autodetection, automatic reconnect, and live image controls
- Built-in **Timelapse**: photos during printing and a finished video ([setup](/docs/CAMERA.md#timelapse)).
- **Telegram bot** for remote printer control and timelapse videos ([setup](/docs/TELEGRAM.md)); runs on a separate computer or server.
- Root access with zsh and oh-my-zsh, and the Entware package manager
- Buzzer support, including melodies from MIDI files or notes
- Your own macros, dialogs, shell commands, and startup services ([details](/docs/EXTENDING.md))

## Why Forge-X and how it is built

The AD5M hardware can produce good prints, but its software limits it. The host has only **128 MiB of RAM**, the bundled Klipper is from the older 0.11 generation, and the stock firmware exposes only a small part of what users expect from a Klipper-based printer.

Forge-X is built around these limits instead of assuming the resources of a newer printer. Memory, CPU load, background services, the screen, the camera, and Klipper timing are treated as one reliability problem: a service that takes too much memory or CPU can stop a print just as a Klipper bug can. Newer software is adapted to fit the hardware rather than added as it is.

Other problems are not about performance: missing Klipper workflows, long-standing AD5M-specific bugs, few ways to recover a printer that fails to boot, little diagnostic information, and no clean way to extend the system. Forge-X treats testing, failure handling, recovery, and documentation as part of the work itself. The goal is a system that can be maintained over time, where every change can be undone, and where others can add integrations or ports without relying on undocumented internals.

In practice:

- **Klipper.** Fixes and features from newer upstream Klipper are backported into the FlashForge host code instead of moving to Klipper 0.13. See [Klipper fixes](/docs/KLIPPER.md) and [Why doesn't Forge-X use Klipper 0.13?](/docs/FAQ.md#why-doesnt-forge-x-use-klipper-013)
- **Failure handling.** If an update or configuration change fails, the next boot starts the stock firmware instead of repeating the failed start, and the recovery menu is available. Every patched Klipper file keeps a `.bak` original, and uninstall restores them. See [Stock firmware integration and lifecycle safety](/docs/DEVELOPMENT.md#stock-firmware-integration-and-lifecycle-safety).
- **Memory.** Feather uses roughly 1–2 MB, compared with roughly 10–20 MB for the Stock screen, and the camera service is written for the same limit. See [Reducing resource usage](/docs/PRINTING.md#reducing-resource-usage).
- **Testing.** Each release goes through automated host tests, visual UI tests, and physical tests on a printer. The tests are in [`tests/`](/tests), and the process is in [Development, testing, and release validation](/docs/DEVELOPMENT.md).
- **Extensibility.** Your own macros, dialogs, shell commands, and startup services can be added without editing Forge-X files, so they survive updates. See [Customizing and extending Forge-X](/docs/EXTENDING.md).

## Documentation

**Setup**

- [Installation](/docs/INSTALL.md) · [Compatibility](/docs/COMPATIBILITY.md) · [Slicing](/docs/SLICING.md) · [Configuration](/docs/CONFIGURATION.md)

**Using the printer**

- [Printing](/docs/PRINTING.md) · [Calibration](/docs/CALIBRATION.md) · [Macros](/docs/MACROS.md) · [Screens](/docs/SCREEN.md) · [Feather](/docs/FEATHER.md) · [Camera](/docs/CAMERA.md) · [Telegram bot and timelapse](/docs/TELEGRAM.md) · [Power Loss Recovery](/docs/POWER_LOSS_RECOVERY.md)

**Maintenance and recovery**

- [Dual boot and recovery menu](/docs/DUAL_BOOT.md) · [Firmware recovery guide](/docs/RECOVERY.md) · [Uninstall](/docs/UNINSTALL.md)

**Reference**

- [F.A.Q.](/docs/FAQ.md) · [Klipper fixes and AD5M-specific hardening](/docs/KLIPPER.md) · [Customizing and extending Forge-X](/docs/EXTENDING.md)

**For contributors**

- [Development, testing, and release validation](/docs/DEVELOPMENT.md) · [Contributor guidelines](CONTRIBUTING.md)
- [OpenWiki](openwiki/quickstart.md): a code-oriented guide ([architecture](openwiki/architecture.md), [source map](openwiki/source-map.md), [testing and change guide](openwiki/testing-and-change-guide.md)). For installation, calibration, and recovery, use the guides above.

## Community and support

- [Telegram support group](https://t.me/+ihE2Ry8kBNkwYzhi)
- [Discord](https://discord.gg/K7MH4hAfeX) (Forums → mods-and-projects → Forge-X)

## Support Forge-X

Forge-X is free, and it is built for the community. Developing new features, writing documentation, testing on real hardware, and answering questions all take a lot of time. If Forge-X is useful to you, you can support the project with a donation. It helps keep the mod maintained and improving.

- **[Boosty (Donate)](https://boosty.to/dra1ex/donate)**
- **[Boosty (Subscribe)](https://boosty.to/dra1ex)**
- **[Cryptocurrency donations](https://telegra.ph/FORGE-X-10-24)**

## Credits

Forge-X builds on the work of many people and projects:

- Thanks to the [Klipper Mod](https://github.com/xblax/flashforge_ad5m_klipper_mod) developers for their excellent work.
- The integration with the Stock screen is based on the implementation from [ZMod](https://github.com/ghzserg/zmod) by [ghzserg](https://github.com/ghzserg).
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
