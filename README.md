<p align="center">
   <img width="600" src="https://github.com/user-attachments/assets/1e1e0b03-a424-4da3-8285-d62dd74470de" />
</p>

# Flashforge Adventurer 5M (Pro) Firmware Mod

This is an _unofficial_ mod to run Moonraker, Klipper (with essential patches), Mainsail, and Fluidd on the Flashforge AD5M (Pro) 3D printers.
The mod is based on ZMod, which itself is derived from Klipper-mod.

> [!CAUTION]
> *If you choose to install this mod on your AD5M (Pro), be aware that you risk voiding your warranty or damaging your printer.*
> *After installation or uninstallation, ensure that you check all printer parameters and perform a full recalibration. Failing to do so may result in damage to your printer.*
> *Proceed at your own risk!*

## DISCLAIMER

The printer has Linux, but it’s not the Linux you’re used to.  
**It’s not** like Ubuntu, Debian, Fedora, or other Linux distributions.  
The printer **isn’t a desktop**. It uses firmware with a Linux core as its base. It’s more like a smart microwave.   

So, **read the documentation** before doing anything. Because you risk **completely bricking** your printer.   
It’s restorable, but **it requires additional hardware** or soldering in exceptional cases.

**Don’t flash one firmware over another** unless you clearly understand what you are doing.   
**Don’t delete** installation, uninstallation, or recovery **logs** — it’s may help to restore your printer.   

**If nothing’s working** and you **don’t understand why** or what you can do, you’re not an experienced user and not a professional.    
—- **It’s better to ask for help** before you do anything that **completely bricks** your printer.    
**You don’t need that**, it takes your time and mine. So better carefully **read the docs first**.

There’s also a tiny chance **you’d have to buy a new motherboard** — probability’s near zero, but it’s not impossible.  
So don’t do anything if you not clearly understand what you are doing.

## Features
<p align="center">
<img width="400" src="https://github.com/user-attachments/assets/c7ff5d09-3786-4b69-b8d5-1f254c809de7" />
<img width="400" src="https://github.com/user-attachments/assets/6f3b9343-d3d1-4f0e-b4cf-9ac1041502b2" />
</p>

- **Stock** Screen with option to disable it completely and switch to Feather screen version to reduce resource consumption
- **Klipper** with many patches/fixes/plugins specially adapted for AD5M
- **Moonraker**
- **Fluidd** & **Mainsail**
- **GuppyScreen** available as an [add-on](https://t.me/FF_ForgeX/2181)
- **OTA** updates
- **Root** access (with zsh/.oh-my-zsh)
- **Buzzer** with ability to play monotonic melodies (midi / notes)
- Patched **mjpg-streamer** with dramatically reduced memory usage
- **Timelapse** support via [Moonraker Telegram bot](https://github.com/nlef/moonraker-telegram-bot) installed on external host
- Adaptive bed meshing with **KAMP**.
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
- **Dual boot** with stock Flashforge software or Klipper Mod

## TL;DR

1. Uninstall any other installed mods first (⚠️ make a backup!).   
2. [Install](/docs/INSTALL.md#flashing-the-firmware-image) the mod.   
3. Update slicer [Start and End G-code](/docs/SLICING.md#for-stock-screen).   
4. Update slicer [Host Type](/docs/SLICING.md#configuring-moonraker--klipper-connection).
5. Enable [LAN-mode](/docs/PRINTING.md#using-stock-firmware-with-mod)
6. Enable [MD5 check](/docs/SLICING.md#enabling-md5-checksum-validation) for G-code files.
7. Update the mod to new versions using [OTA](/docs/INSTALL.md#ota-updates).   
8. **Recommended**: Enable [Klipper tuning](/docs/CONFIGURATION.md#configuration-macros) to avoid typical MCU errors: `SET_MOD PARAM=tune_klipper VALUE=1`
9. **Recommended**: Enable [config tuning](/docs/CONFIGURATION.md#configuration-macros) for a better first layer: `SET_MOD PARAM=tune_config VALUE=1` (⚠️ requires recalibration afterward).   
10. ⚠️ [Recalibrate](/docs/PRINTING.md#calibration) Bed Mesh, shaper, and Z-offset.
11. **Optional**: Learn about [Z-Offset](/docs/PRINTING.md#z-offset)
12. **Optional**: Enable the mod’s [Camera](/docs/CAMERA.md#step-3-enable-mods-camera) implementation.   
13. **Optional**: Configure your [LED lighting](/docs/PRINTING.md#led-light-control)     
14. **Optional**: Enable [Feather Screen](/docs/SCREEN.md#switching-to-feather-screen).   
15. **Optional**: Enable [Bed Collision Protection](/docs/PRINTING.md#bed-collision-protection).   
16. **Optional**: Enable [Bed Mesh Validation](/docs/PRINTING.md#bed-mesh-validation).   

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

## Why This Mod Was Developed
This mod was created to address several critical limitations and challenges faced by users of the Flashforge AD5M (Pro) 3D printer. Here are the key reasons behind its development:

**Instability and Resource Issues in Existing Klipper Mods:**
The previous Klipper mod for the AD5M has stopped development and is no really stable. It consumes excessive RAM, leading to issues such as "Timer too close" errors and other performance problems. And some of typical AD5M issues not fixed at all. This mod aims to resolve these issues by optimizing resource usage and ensuring a stable, reliable experience.

**Closed and Inflexible Development in ZMOD:**
While ZMOD introduced significant improvements, it is not user-friendly for further enhancements or fixes. Its closed nature and unique development approach make it difficult to modify without deep knowledge of the entire system. This mod focuses on rewriting the foundation to provide advanced users with more control, making it easier to apply patches, additions, and customizations.

**Lack of Essential Functionality in Stock Firmware:**
The stock firmware lacks many essential features that modern 3D printing enthusiasts expect. For example:
The camera functionality is poorly optimized, consuming excessive RAM and delivering subpar performance.
Users are unable to perform standard tasks that Klipper users typically rely on, such as advanced calibration, macros, and real-time monitoring.
This mod addresses these shortcomings by integrating modern tools and features.

**Outdated Klipper with Unresolved Bugs:**
The existing Klipper implementation for the AD5M is outdated and plagued with bugs. This mod focuses on fixing these long-standing issues, modifying Klipper plugins, and enhancing core functionality to better suit the specific requirements of the AD5M (Pro) printer. The goal is to provide a stable, feature-rich platform tailored to this printer's unique hardware and user needs.


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

If you encounter any issues, feel free to join Telegram group for support: [Join here](https://t.me/+ihE2Ry8kBNkwYzhi).
You can also join FlashForge community in [Discord](https://discord.gg/K7MH4hAfeX) (Navigate to: Forums → mods-and-projects → Forge-X)

## TODO

- [x] Feather Screen: Ultra lightweight screen implementation with essential information
- [x] Klipper bugfixes related to processing of G-Code containing Unicode symbols (specific for non-English symbols in object names)
- [x] Mainsail OTA: Fixed and patched implementation to work correctly with navigation, with OTA updates
- [ ] Power-loss recovery for non-Stock screens
- [ ] Integration and adoption of GuppyScreen for AD5M

## Support Forge-X

Forge-X is an open-source, free project built for the community, and everyone is welcome to use it without cost. However, developing new features, writing detailed documentation, and providing ongoing support through the community demands a significant amount of time and dedication. If you enjoy using Forge-X and appreciate the effort behind it, consider supporting the project with a donation. Your contributions help ensure the time needed to keep improving the mod, adding new features and maintaining active support.

- **[Boosty (Donate)](https://boosty.to/dra1ex/donate)**

- **[Boosty (Subscribe)](https://boosty.to/dra1ex)**

- **Cryptocurrency Donations**:
  - **BTC**: `17igL1Y1gHSK2FFsn8TQgVKkaVXXJ33Mu6`
  - **TON**: `UQAa8-8q3GrZVVlZWbQgM80l9hol8OacOGfaQ68jVRU_uRbK`
  - **USDT (TRC20)**: `TUmBppbp5vhhpwozzYzYmd9T3GefJsbX5K`

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
**906Prints, MattArmfield, Stormage, Spud, Luigisvc, slydog43, Никита (motionpix), CHaucke, Andrew Popow**

#### Anonymous donations

I also want to express my heartfelt gratitude to those who have supported the project anonymously through cryptocurrency.

