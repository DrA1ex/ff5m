# Compatibility: printers, stock firmware and screens

**Short answer**

- **Printers:** the Flashforge Adventurer 5M and the Adventurer 5M Pro.
- **Stock firmware:** every version from **2.6.5 up to 5.1.7**, the newest version checked so far. You do not need to update or downgrade the stock firmware to use Forge-X, and you can leave an installed firmware as it is.
- **If you do not use the Stock screen, the stock firmware version does not matter.** Install Forge-X on whatever version the printer has and do not think about it.

**Contents:**
[Why the firmware version rarely matters](#why-the-firmware-version-rarely-matters) ·
[What does depend on the firmware version](#what-does-depend-on-the-firmware-version) ·
[Which firmware to use with the Stock screen](#which-firmware-to-use-with-the-stock-screen) ·
[Adventurer 5M and 5M Pro](#adventurer-5m-and-5m-pro) ·
[Supported combinations](#supported-combinations)

## Why the firmware version rarely matters

Forge-X runs on the system that is already installed on the printer. That system does not change between stock firmware versions:

- The Linux system, the kernel and the base libraries are put on the printer at the factory. The AD5M kernel is byte-identical across stock firmware 2.6.5–5.1.x (the Forge-X zram modules are built for it and load on every one of these versions, see [`.shell/boot/zram/README.md`](/.shell/boot/zram/README.md)). The MCU firmware is the same in all supported versions (confirmed), so a stock update does not change what Forge-X talks to. For 5.1.x, the kernel, the MCU firmware and the partition layout are also byte-identical to 3.1.3 ([Installation → Prerequisites](INSTALL.md#prerequisites)).
- A stock firmware update mainly replaces the **Stock screen application**. A `-Factory` image differs from a regular image only in that it also flashes both MCUs (with the same MCU firmware) and resets the printer configuration.
- Forge-X uses what is preinstalled. It does not use the parts that change from one firmware version to the next.

So a newer stock firmware brings a newer Stock screen, not a different platform for Forge-X. A version being new, or being old, is not a compatibility problem by itself.

2.6.5 is the oldest version that has been checked. Versions up to 5.1.7 are checked, including 5.0.3, 5.0.4 and 5.1.2–5.1.7. Firmware 5.1.x was test-flashed on hardware: the mod installs and runs.

## What does depend on the firmware version

Only things that belong to the stock firmware itself:

- **The Stock screen.** Its look, its workflows and its bugs change with the firmware version. For example, some `3.1.*` versions can freeze on the initialization screen if the printer cannot connect to a network ([FAQ](FAQ.md#my-printer-is-stuck-on-the-stock-initialization-screen-how-can-i-fix-it)). Feather, Guppy Screen and Headless mode do not use the Stock screen, so they are not affected. See [Screens](SCREEN.md).
- **Stock cloud services.** The optional `block_cloud` setting blocks the vendor cloud hosts by name. The cloud hosts appeared in 5.0.x and were changed in 5.1.4, and the list in Forge-X covers both the old and the new hosts ([`.shell/init-main.sh`](/.shell/init-main.sh)).
- **Printer configuration values.** A stock update or a `-Factory` image can reset some parameters in `printer.base.cfg`. Forge-X has a [backup and restore](CONFIGURATION.md) for this.

### Which firmware to use with the Stock screen

If you use the Stock screen, **3.1.x is the recommended version**, because it is the most stable one. If you do not use the FlashForge services (the vendor cloud, video streaming and model sharing), you do not need a version newer than 3.1.x. The cloud services come with the 5.0.x and 5.1.x versions. Newer versions are supported, but for Forge-X they offer nothing beyond those services.

If you use Feather, Guppy Screen or Headless mode, ignore this: the version does not matter.

If a problem appears after a stock firmware update, report the firmware version together with the problem.

## Adventurer 5M and 5M Pro

The two printers share the same platform. The Forge-X default `printer.base.cfg` and `printer.cfg` are byte-identical for the 5M and the 5M Pro (`.cfg/default/printer/Adventurer5M/` and `.cfg/default/printer/Adventurer5MPro/`).

According to the maintainer, the hardware differs in a couple of fans, the enclosure panels and a TVOC sensor, and is otherwise practically the same.

The only difference when installing is the file name: for the Pro the image is renamed to `Adventurer5MPro-ForgeX-x.x.x.tgz` ([Installation](INSTALL.md#flashing-the-firmware-image)).

## Supported combinations

| | Supported |
| --- | --- |
| Printers | Adventurer 5M, Adventurer 5M Pro |
| Stock firmware | Every version from 2.6.5 up to 5.1.7 (the newest checked) |
| Screens | Feather (default), Guppy Screen, Headless, and the Stock screen. The Stock screen is the vendor application, so its behavior follows the stock firmware version |

The automated host tests run without a printer and do not depend on the firmware version. Real-printer testing is a maintainer step before a release, see [Release validation](DEVELOPMENT.md#release-validation).

A report from a user is useful for any combination, and especially if something does not work. Include the printer model, the stock firmware version, the display mode and the Forge-X version, and send it to the [Telegram support group](https://t.me/+ihE2Ry8kBNkwYzhi) or the [Discord server](https://discord.gg/K7MH4hAfeX).
