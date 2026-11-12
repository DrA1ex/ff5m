# Compatibility: printers, stock firmware and screens

**Short answer**

- **Printers:** the Flashforge Adventurer 5M and the Adventurer 5M Pro.
- **Stock firmware:** every version from **2.6.5 up to 5.1.7**, the newest version tested on hardware so far. You do not need to update or downgrade the stock firmware to use Forge-X, and you can leave an installed firmware as it is.
- **Within this range, the stock firmware version matters only for the Stock screen.** Install Forge-X on whatever supported version the printer has.

**Contents:**
[Why the firmware version rarely matters](#why-the-firmware-version-rarely-matters) ·
[What does depend on the firmware version](#what-does-depend-on-the-firmware-version) ·
[Which firmware to use with the Stock screen](#which-firmware-to-use-with-the-stock-screen) ·
[Adventurer 5M and 5M Pro](#adventurer-5m-and-5m-pro) ·
[Supported combinations](#supported-combinations)

## Why the firmware version rarely matters

Forge-X runs on the system that is already installed on the printer. In the compared firmware packages, the platform parts it depends on stay the same:

- A file-by-file comparison of the stock firmware packages ([firmware archive analysis](https://github.com/maci0/ff5adm-fw-archive/blob/main/analysis/stock-fw-changelog.md)) shows that the two MCU firmware files are byte-identical from 2.4.5 on, so a stock update in the supported range does not change what Forge-X talks to. The kernel (`uImage`, `sunxi.dtb` and the `8821cu.ko` Wi-Fi driver) is byte-identical from 3.1.3 on; the kernel package changed at 3.1.3, and versions before 3.1.3 were not compared. The Forge-X zram modules are built for this kernel (see [`.shell/boot/zram/README.md`](/.shell/boot/zram/README.md)).
- A stock firmware update mainly replaces the **Stock screen application**, but it can also change other parts, such as the vendor cloud library (changed at 3.2.5) or configuration files. A `-Factory` image differs from a regular image only in that it also flashes both MCUs (with the same MCU firmware) and resets the printer configuration.
- Forge-X runs on the platform parts above, not on the Stock screen application.

So a newer stock firmware mostly brings a newer Stock screen, not a different platform for Forge-X. A version being new, or being old, is not a compatibility problem by itself, but only the range below has been checked.

2.6.5 is the oldest version that has been checked. Versions up to 5.1.7 are checked, including 5.0.3, 5.0.4 and 5.1.2–5.1.7. Firmware 5.1.x up to 5.1.7 was test-flashed on hardware: the mod installs and runs. The newer Pro 5.1.8 and 5M 5.1.9 packages were only compared offline, not installed: their kernel and MCU files are the same as in 5.1.7, but they are not part of the checked range.

## What does depend on the firmware version

Only things that belong to the stock firmware itself:

- **The Stock screen.** Its look, its workflows and its bugs change with the firmware version. For example, some `3.1.*` versions can freeze on the initialization screen if the printer cannot connect to a network. This affects only the start of the printer, not printing ([FAQ](FAQ.md#my-printer-is-stuck-on-the-stock-initialization-screen-how-can-i-fix-it)). Feather, Guppy Screen and Headless mode do not use the Stock screen, so they are not affected. See [Screens](SCREEN.md).
- **Stock cloud services.** The optional `block_cloud` setting blocks the vendor cloud hosts by name. The cloud hosts appeared in 5.0.x and were changed in 5.1.4, and the list in Forge-X covers both the old and the new hosts ([`.shell/init-main.sh`](/.shell/init-main.sh)).
- **Printer configuration values.** A stock update or a `-Factory` image can reset some parameters in `printer.base.cfg`. Forge-X has a [backup and restore](CONFIGURATION.md) for this.

### Which firmware to use with the Stock screen

If you use the Stock screen, **3.1.x is the recommended version**, because it is the most stable one while printing. If you do not use the FlashForge services (the vendor cloud, video streaming and model sharing), you do not need a version newer than 3.1.x. The cloud services come with the 5.0.x and 5.1.x versions. Newer versions are supported, but for Forge-X they offer nothing beyond those services.

If you use Feather, Guppy Screen or Headless mode, ignore this: any version in the supported range works.

If a problem appears after a stock firmware update, report the firmware version together with the problem.

## Adventurer 5M and 5M Pro

The two printers share the same platform. The Forge-X default `printer.base.cfg` and `printer.cfg` are byte-identical for the 5M and the 5M Pro (`.cfg/default/printer/Adventurer5M/` and `.cfg/default/printer/Adventurer5MPro/`).

According to the maintainer, the hardware differs in a couple of fans, the enclosure panels and a TVOC sensor, and is otherwise practically the same.

The only difference when installing is the file name: for the Pro the image is renamed to `Adventurer5MPro-ForgeX-x.x.x.tgz` ([Installation](INSTALL.md#flashing-the-firmware-image)).

## Supported combinations

| | Supported |
| --- | --- |
| Printers | Adventurer 5M, Adventurer 5M Pro |
| Stock firmware | Every version from 2.6.5 up to 5.1.7 (the newest hardware-tested version) |
| Screens | Feather (default), Guppy Screen, Headless, and the Stock screen. The Stock screen is the vendor application, so its behavior follows the stock firmware version |

The automated host tests run without a printer and do not depend on the firmware version. Real-printer testing is run by the maintainer before a release and is required for pull requests that touch hardware-related code, see [Release validation](DEVELOPMENT.md#release-validation) and [Testing](DEVELOPMENT.md#testing).

A report from a user is useful for any combination, and especially if something does not work. Include the printer model, the stock firmware version, the display mode and the Forge-X version, and send it to the [Telegram support group](https://t.me/+ihE2Ry8kBNkwYzhi) or the [Discord server](https://discord.gg/K7MH4hAfeX).
