<p align="center">
   <img width="600" src="https://github.com/user-attachments/assets/4e443f9c-02a7-483a-a9da-9afa6db6be2a" />
</p>

# Flashforge Adventurer 5M (Pro) Firmware Mod

This is an _unofficial_ mod to run Moonraker, Klipper (with essential patches), Mainsail, and Fluidd on the Flashforge AD5M (Pro) 3D printers.

> [!CAUTION]
> *If you choose to install this mod on your AD5M (Pro), be aware that you risk voiding your warranty or damaging your printer.*
> *After installation or uninstallation, ensure that you check all printer parameters and perform a full recalibration. Failing to do so may result in damage to your printer.*
> *Proceed at your own risk!*

## DISCLAIMER

The printer has Linux, but it’s not the Linux you’re used to.  
**It’s not** like Ubuntu, Debian, Fedora, or other Linux distributions.  
The printer **isn’t a desktop**. It uses firmware with a Linux core as its base.

So, **read the documentation** before doing anything. Because you risk **completely bricking** your printer.   
It’s restorable though, but **it requires additional hardware** or soldering in exceptional cases.

**Don’t flash different mod's firmware over another** unless you clearly understand what you are doing.   
**Don’t delete** installation, uninstallation, or recovery **logs** — it’s may help to restore your printer.

## Features

<p align="center">
<img width="400" src="https://github.com/user-attachments/assets/7837ec81-6a88-4a1b-81d1-2266a87025bf" />
<img width="400" src="https://github.com/user-attachments/assets/48b40175-1928-439c-baa6-78ae157efaf3" />
<img width="400" src="https://github.com/user-attachments/assets/51d381fd-e98c-44c4-9518-c4a64c88bcf4" />
<img width="400" src="https://github.com/user-attachments/assets/2e0fafd5-15b3-4e7e-ad26-6cf7c67613fe" />
<img width="400" src="https://github.com/user-attachments/assets/28a0ef3f-e7cf-4648-aff8-7f273d2b055b" />
</p>

- Fully interactive **Feather** screen
- **Stock** Screen with option to disable it completely and switch to one of alternative screen versions to reduce resource consumption
- **Klipper** with [bug fixes and hardening specially adapted for AD5M](/docs/KLIPPER.md)
- **Regression-tested development and release process** with automated host tests, visual UI regression, and real-printer physical regression before releases ([details](/docs/DEVELOPMENT.md))
- **Moonraker**
- **Fluidd** & **Mainsail**
- Adapted **Guppy** screen
- Local and USB G-code browsing and print control from Feather
- GuidedZ-offset, bed, extruder, PID, and Input Shaper calibration
- Originally developed **[Power Loss Recovery](/docs/POWER_LOSS_RECOVERY.md)** specially for AD5M
- **OTA** updates for Firmware, Fluidd, Mainsall, Guppyscreen
- **Last-known-good boot guard and Stock fallback**: updated runtime is accepted before the early boot guard is replaced, with Recovery/rollback paths for failed initialization ([details](/docs/DEVELOPMENT.md#stock-firmware-integration-and-lifecycle-safety))
- **Root** access (with zsh/.oh-my-zsh)
- **Buzzer** with ability to play monotonic melodies (midi / notes)
- Dedicated low-memory **[ForgeXstream](/docs/CAMERA.md)** camera service with bounded buffering, device autodetection, automatic camera reconnect, live V4L2 image controls, and source-level regression tests
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

## TL;DR

> [!CAUTION]
> Releases before **1.4.1-11** contain a Smart Park / `MOVE_SAFE` bug.   
> Do **not** enable or use KAMP.   
> Do **not** `MOVE_SAFE`, while relative positioning (`G91`) is active.   
> Upgrade to **1.4.1-11 or later** first.

1. Uninstall any other installed mods first (⚠️ make a backup!).   
2. [Install](/docs/INSTALL.md#flashing-the-firmware-image) the mod.   
3. Update slicer [Start and End G-code](/docs/SLICING.md#for-stock-screen).   
4. Update slicer [Host Type](/docs/SLICING.md#configuring-moonraker--klipper-connection).
5. Enable [LAN-mode](/docs/PRINTING.md#using-stock-firmware-with-mod)
6. Enable [MD5 check](/docs/SLICING.md#enabling-md5-checksum-validation) for G-code files.
7. Update the mod to new versions using [OTA](/docs/INSTALL.md#ota-updates).
8. **⚠️ Mandatory**: Read about [bed mesh calibration](/docs/CALIBRATION.md#before-you-start)
9. **Recommended**: Enable [Klipper tuning](/docs/CONFIGURATION.md#configuration-macros) to avoid typical MCU errors: `SET_MOD PARAM=tune_klipper VALUE=1`
10. **Recommended**: Enable [config tuning](/docs/CONFIGURATION.md#configuration-macros) for a better first layer: `SET_MOD PARAM=tune_config VALUE=1` (⚠️ requires recalibration afterward).   
11. ⚠️ [Recalibrate](/docs/PRINTING.md#calibration) the bed mesh, input shaper, and Z offset.
12. **Optional**: Learn about [Z-Offset](/docs/PRINTING.md#z-offset)
13. **Optional**: Enable the mod’s [Camera](/docs/CAMERA.md#step-3-enable-mods-camera) implementation.   
14. **Optional**: Configure your [LED lighting](/docs/PRINTING.md#led-light-control)
15. **Optional**: Enable [Feather/Guppy Screen](/docs/SCREEN.md#switching-to-feather-screen).
16. **Optional**: Enable [Bed Collision Protection](/docs/PRINTING.md#bed-collision-protection).
17. **Optional**: Enable [Bed Mesh Validation](/docs/PRINTING.md#bed-mesh-validation).
18. **Optional**: Review and enable [Power Loss Recovery](/docs/POWER_LOSS_RECOVERY.md).

## Get Started

To begin, follow the instructions on the [Installation page](/docs/INSTALL.md). After the installation, you will need to update your slicer's starting and finishing G-code. Refer to the [Slicing page](/docs/SLICING.md) for guidance.

> [!WARNING]   
> **Important:** Make sure to review your printer settings and recalibrate the bed mesh and Z-offset. Some settings may change during installation, and failure to recalibrate could potentially damage your printer.

This modification also includes additional features. It is highly recommended that you thoroughly read the [Printing](/docs/PRINTING.md) and [Configuration](/docs/CONFIGURATION.md) pages before getting started.

> [!NOTE]
> **Advanced: resource and stability tuning.** If the printer works reliably, do not change resource settings. For recurring memory-pressure, E0011/E0017, or *Timer too close* errors, see [Reliability and resources](/docs/PRINTING.md#reducing-resource-usage). It explains when to reduce running services, tune Klipper, or use ZRAM instead of relying on eMMC swap, along with the trade-offs.

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

## Why Forge-X Was Developed

Forge-X was developed to provide a more reliable, maintainable, and complete software environment for the Adventurer 5M while preserving compatibility with the printer's existing hardware and firmware base.

The AD5M hardware is capable of producing good print results, but the platform has several practical limitations. The host has only **128 MiB of RAM**, the bundled Klipper stack is based on the older 0.11 generation, and the stock firmware exposes only a limited subset of the functionality and integrations commonly expected from Klipper-based printers.

Forge-X was designed around those constraints rather than assuming the printer has the resources of a newer platform. Memory usage, CPU load, background services, UI components, camera streaming, and Klipper behavior are all treated as part of the reliability problem. Where newer software or upstream behavior is useful, it is adapted with the available hardware budget in mind.

The project also aims to address software limitations that are unrelated to raw hardware performance: missing Klipper workflows, long-standing platform-specific bugs, limited recovery options, insufficient diagnostics, and the lack of an extensible environment for users who want to customize or build on top of the printer.

For that reason, Forge-X treats testing, predictable failure handling, recovery, documentation, and maintainability as part of the implementation itself. The code is structured around common software-engineering practices, accompanied by user and engineering documentation, and designed so that additional integrations or platform-specific ports can be implemented without depending on undocumented project internals.

In short, Forge-X exists to make better use of the hardware already present in the AD5M, while explicitly accounting for its resource limits and replacing software limitations with a more open, testable, and maintainable environment.

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
- [Power Loss Recovery](/docs/POWER_LOSS_RECOVERY.md)
- [Firmware Recovery guide](/docs/RECOVERY.md)
- [Development, testing, and release validation](/docs/DEVELOPMENT.md)
- [Contributing](CONTRIBUTING.md)

### Engineering documentation

- [Development and release validation](/docs/DEVELOPMENT.md)
- [Contributor guidelines](CONTRIBUTING.md)
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

- [x] Feather Screen: Ultra-lightweight interactive UI for essential local control
- [x] Klipper bugfixes related to processing of G-Code containing Unicode symbols (specific for non-English symbols in object names)
- [x] Mainsail OTA: Fixed and patched implementation to work correctly with navigation, with OTA updates
- [x] Power-loss recovery for non-Stock screens
- [x] Integration and adaptation of GuppyScreen for AD5M
- [x] A custom interactive Feather screen built specifically for the AD5M printer running Forge-X.

## Support Forge-X

Forge-X is an open-source, free project built for the community, and everyone is welcome to use it without cost. However, developing new features, writing detailed documentation, and providing ongoing support through the community demands a significant amount of time and dedication. If you enjoy using Forge-X and appreciate the effort behind it, consider supporting the project with a donation. Your contributions help ensure the time needed to keep improving the mod, adding new features and maintaining active support.

- **[Boosty (Donate)](https://boosty.to/dra1ex/donate)**

- **[Boosty (Subscribe)](https://boosty.to/dra1ex)**

- **[Cryptocurrency Donations](https://telegra.ph/FORGE-X-10-24)**:

## Credits

Thanks [Klipper Mod](https://github.com/xblax/flashforge_ad5m_klipper_mod) developers for their great work.

Thanks to the Klipper and Moonraker communities for their ongoing development.

Thanks to the Russian FlashForge Adventurer 5M Telegram Community: [@FF_5M_5M_Pro](https://t.me/FF_5M_5M_Pro)

Big thanks [@Zero](https://www.youtube.com/@zerodotcmd) for the awesome logo! 

This mod is based on ZMod by [ghzserg](https://github.com/ghzserg).

Thanks for the great open-source fonts:
- [Roboto Font](https://fonts.google.com/specimen/Roboto)
- [JetBrains Mono Font](https://www.jetbrains.com/lp/mono)
- [Typicons Icons Font](https://www.s-ings.com/typicons/)

### Special Thanks

I’m truly thankful for everyone who has supported this project with their donations. Your contributions mean a lot to me and help make this work possible.
Every contribution, no matter the size, makes a huge difference and shows the strength of our community.

Here are some of the wonderful people:   
**906Prints, MattArmfield, Stormage, Spud, Luigisvc, slydog43, Никита (motionpix), CHaucke, Andrew Popow, K3D // Dmitry Sorkin, D T, Kurt LaRue, jollyroger1789**

#### Anonymous donations

I also want to express my heartfelt gratitude to those who have supported the project anonymously through cryptocurrency.
