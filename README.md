<p align="center">
  <picture>
    <source
      media="(prefers-color-scheme: dark)"
      srcset="https://github.com/user-attachments/assets/74920f34-bb4e-4629-8f35-1b8cf4b8a49f">
    <source
      media="(prefers-color-scheme: light)"
      srcset="https://github.com/user-attachments/assets/16a8c4cb-cd6f-4567-a90a-40401c543924">
    <img alt="Forge-X"
      src="https://github.com/user-attachments/assets/16a8c4cb-cd6f-4567-a90a-40401c543924"
      width="700">
  </picture>
</p>

# Firmware Mod for Flashforge Adventurer 5M / 5M Pro

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

## Features

- **Stock** Screen with option to disable it completely and switch to one of alternative screen versions to reduce resource consumption
- **Klipper** with many patches/fixes/plugins specially adapted for AD5M
- **Moonraker**
- **Fluidd** & **Mainsail**
- Adapted **Guppy** and custom **Feather** screens
- Originally developed **Power Loss Recovery** specially for AD5M
- **OTA** updates for Firmware, Fluidd, Mainsall, Guppyscreen.
- **Root** access (with zsh/.oh-my-zsh)
- **Buzzer** with ability to play monotonic melodies (midi / notes)
- Patched **video streamer** with dramatically reduced memory usage
- **Timelapse** support via [Moonraker Telegram bot](https://github.com/nlef/moonraker-telegram-bot) installed on external host
- Adaptive bed meshing with **KAMP** with Smart Parking.
- Built-in **MD5** checks for gcode files.
- **Backup** and **Restore** mechanism for printer's configuration
- Fix for the **Move queue overflow (E0017)** error.
- Fix for the **Communication Timeout (E0011)** error.
- **Failsafe** mechanism to prevent nozzle collisions.
- Better **Clear Nozzle** algorithm.
- Enhanced **Shaper Calibration** with automatic plot generation.
- Easy **Bed Level Screw Tuning**.
- Customized dedicated Linux environment based on **Buildroot**
- **Entware** package manager for additional software installation
- **Dual boot** with stock Flashforge software / Klipper Mod

## Public Beta of Upcoming 1.4.2 Release

**Forge-X 1.4.2 is now available as a public beta.** This is a major update with a fully interactive **Feather** touchscreen and a heavily reworked Forge-X experience — much closer to what I originally wanted Forge-X to be from the beginning.

The beta is already considered stable enough for regular use, but it is still a beta: some bugs, rough edges, or cosmetic issues may remain.

If you want to try the new Forge-X early, help find the remaining issues, and take part in shaping the upcoming release, check the [**Releases**](https://github.com/DrA1ex/ff5m/releases) page for the latest `1.4.2` beta.

Full details, installation notes, and beta-specific warnings are included in the release notes.

## TL;DR

> [!CAUTION]
> Releases before **1.4.1-11** contain a Smart Park / `MOVE_SAFE` bug. Do **not** enable or use KAMP, or call `MOVE_SAFE`, while relative positioning (`G91`) is active. Upgrade to **1.4.1-11 or later** first.

1. Uninstall any other installed mods first (⚠️ make a backup!).   
2. [Install](/docs/INSTALL.md#flashing-the-firmware-image) the mod.   
3. Update slicer [Start and End G-code](/docs/SLICING.md#for-stock-screen).   
4. Update slicer [Host Type](/docs/SLICING.md#configuring-moonraker--klipper-connection).
5. Enable [LAN-mode](/docs/PRINTING.md#using-stock-firmware-with-mod)
6. Enable [MD5 check](/docs/SLICING.md#enabling-md5-checksum-validation) for G-code files.
7. Update the mod to new versions using [OTA](/docs/INSTALL.md#ota-updates).
8. **⚠️ Mandatory**: Read about [How bed meshing works](/docs/CALIBRATION.md#before-you-start)
9. **Recommended**: Enable [Klipper tuning](/docs/CONFIGURATION.md#configuration-macros) to avoid typical MCU errors: `SET_MOD PARAM=tune_klipper VALUE=1`
10. **Recommended**: Enable [config tuning](/docs/CONFIGURATION.md#configuration-macros) for a better first layer: `SET_MOD PARAM=tune_config VALUE=1` (⚠️ requires recalibration afterward).   
11. ⚠️ [Recalibrate](/docs/PRINTING.md#calibration) Bed Mesh, shaper, and Z-offset.
12. **Optional**: Learn about [Z-Offset](/docs/PRINTING.md#z-offset)
13. **Optional**: Enable the mod’s [Camera](/docs/CAMERA.md#step-3-enable-mods-camera) implementation.   
14. **Optional**: Configure your [LED lighting](/docs/PRINTING.md#led-light-control)     
15. **Optional**: Enable [Feather/Guppy Screen](/docs/SCREEN.md#switching-to-feather-screen).   
16. **Optional**: Enable [Bed Collision Protection](/docs/PRINTING.md#bed-collision-protection).   
17. **Optional**: Enable [Bed Mesh Validation](/docs/PRINTING.md#bed-mesh-validation).
18. **Optional**: Enable [Power Loss Recovery](docs/PRINTING.md#power-loss-recovery-resurrection).   


## Get Started

To begin, follow the instructions on the [Installation page](/docs/INSTALL.md). After the installation, you will need to update your slicer's starting and finishing G-code. Refer to the [Slicing page](/docs/SLICING.md) for guidance.

> [!WARNING]   
> **Important:** Make sure to review your printer settings and recalibrate the bed mesh and Z-offset. Some settings may change during installation, and failure to recalibrate could potentially damage your printer.

This modification also includes additional features. It is highly recommended that you thoroughly read the [Printing](/docs/PRINTING.md) and [Configuration](/docs/CONFIGURATION.md) pages before getting started.

This article might also be useful: [Reducing resource usage](https://github.com/DrA1ex/ff5m/blob/dev/docs/PRINTING.md#reducing-resource-usage).   
For additional help, check out the [F.A.Q.](/docs/FAQ.md).

You can reach services using these addresses:  
- **Moonraker**: `http://<printer_ip>:7125/`  
- **Fluidd**: `http://<printer_ip>/fluidd/`  
- **Mainsail**: `http://<printer_ip>/mainsail/`
- **SSH credentials**: `root` / `root`  

If you encounter issues:  
1. First, consult the documentation.
2. If the problem persists:
   - Join the [Telegram Support](https://t.me/+ihE2Ry8kBNkwYzhi) group
   - Or, Join the [Discord server](https://discord.gg/K7MH4hAfeX)    
     → Navigate to: Forums → mods-and-projects → Forge-X
   - Only open a [GitHub issue](https://github.com/DrA1ex/ff5m/issues) if you're **absolutely certain** this is a bug.

## Why Forge-X and how it is built

The AD5M hardware can produce good prints, but its software limits it. The host has only **128 MiB of RAM**, the bundled Klipper is from the older 0.11 generation, and the stock firmware exposes only a small part of what users expect from a Klipper-based printer.

Forge-X is built around these limits instead of assuming the resources of a newer printer. Memory, CPU load, background services, the screen, the camera, and Klipper timing are treated as one reliability problem: a service that takes too much memory or CPU can stop a print just as a Klipper bug can. Newer software is adapted to fit the hardware rather than added as it is.

Other problems are not about performance: missing Klipper workflows, long-standing AD5M-specific bugs, few ways to recover a printer that fails to boot, little diagnostic information, and no clean way to extend the system. Forge-X treats testing, failure handling, recovery, and documentation as part of the work itself. The goal is a system that can be maintained over time, where every change can be undone, and where others can add integrations or ports without relying on undocumented internals.


## Documentation
- [Installation](/docs/INSTALL.md)
- [Configuration](/docs/CONFIGURATION.md)
- [Slicing](/docs/SLICING.md)
- [Printing](/docs/PRINTING.md)
- [Macros](/docs/MACROS.md)
- [Calibration](/docs/CALIBRATION.md)
- [F.A.Q](/docs/FAQ.md)
- [Alternative Screen](/docs/SCREEN.md)
- [Camera](/docs/CAMERA.md)
- [Telegram Bot and Timelapse](/docs/TELEGRAM.md)
- [Dual boot](/docs/DUAL_BOOT.md)
- [Uninstall](/docs/UNINSTALL.md)
- [Recovery guide](/docs/RECOVERY.md)

### Engineering documentation

The [OpenWiki](openwiki/quickstart.md) provides a code-oriented guide for contributors and advanced users. It complements the operator documentation above; follow the operator guides for installation, calibration, and recovery procedures.

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

If you encounter any issues, feel free to join Telegram group for support: [Join here](https://t.me/+ihE2Ry8kBNkwYzhi).
You can also join FlashForge community in [Discord](https://discord.gg/K7MH4hAfeX) (Navigate to: Forums → mods-and-projects → Forge-X)

## TODO

- [x] Feather Screen: Ultra lightweight screen implementation with essential information
- [x] Klipper bugfixes related to processing of G-Code containing Unicode symbols (specific for non-English symbols in object names)
- [x] Mainsail OTA: Fixed and patched implementation to work correctly with navigation, with OTA updates
- [x] Power-loss recovery for non-Stock screens
- [x] Integration and adaptation of GuppyScreen for AD5M
- [ ] Port Forge-X to FlashForge AD5X / Creator 5 (possible with your support)

## Support Forge-X

Forge-X is free, and it is built for the community. Developing new features, writing documentation, testing on real hardware, and answering questions all take a lot of time. If Forge-X is useful to you, you can support the project with a donation. It helps keep the mod maintained and improving.

- **[Lava (Donate)](https://app.lava.top/1535722070?tabId=donate)**
- **[Boosty (Donate)](https://boosty.to/dra1ex/donate)**
- **[Boosty (Subscribe)](https://boosty.to/dra1ex)**
- **[Cryptocurrency donations](https://telegra.ph/FORGE-X-10-24)**

## Credits

Forge-X builds on the work of many people and projects:

- Thanks to the [Klipper Mod](https://github.com/xblax/flashforge_ad5m_klipper_mod) developers for their excellent work.
- The integration with the Stock screen is based on the implementation from [ZMod](https://github.com/ghzserg/zmod) by [ghzserg](https://github.com/ghzserg).
- Thanks to the Klipper and Moonraker communities for their ongoing development.

Thanks to the authors of these open-source fonts:
- [Roboto Font](https://fonts.google.com/specimen/Roboto)
- [JetBrains Mono Font](https://www.jetbrains.com/lp/mono)
- [Typicons Icons Font](https://www.s-ings.com/typicons/)

### Special Thanks

Thank you to everyone who has supported this project with a donation. Your support gives the project time to grow, and every contribution, whatever its size, is appreciated.

Supporters:
**906Prints, MattArmfield, Stormage, Spud, Luigisvc, slydog43, Никита (motionpix), CHaucke, Andrew Popow, K3D // Dmitry Sorkin, D T, Kurt LaRue, jollyroger1789, Jules Woods**

#### Anonymous donations

Thank you also to everyone who supported the project anonymously with cryptocurrency.
