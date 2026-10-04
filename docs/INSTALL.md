## Installation

Forge-X can be removed at any time, and the stock firmware stays available:

- [Uninstall](/docs/UNINSTALL.md) restores the original Klipper files and printer configuration.
- [Dual Boot](/docs/DUAL_BOOT.md) starts the stock firmware without the mod.
- If the mod fails to start, the next boot falls back to the stock firmware.
- For harder cases there are recovery and uninstall images and a [recovery guide](/docs/RECOVERY.md).


> [!CAUTION]
> - Uninstall any other mod before installing Forge-X, and make a backup first. Installing over another mod can cause conflicts, so this is not supported.
> - After installing or uninstalling, run all calibrations again. The mod changes some parameters, so the previous calibration may no longer be valid. Printing without recalibration can damage the printer or the bed surface, or reduce print quality.
> - Proceed at your own risk.


> [!TIP]
> It is not necessary to uninstall Forge-X before updating major versions
> e.g. 1.3.x to 1.4.x

## Prerequisites

* Stock firmware version: any from **2.6.5** to **5.1.x**; you do not need to update or downgrade it (see [Compatibility](COMPATIBILITY.md)). Checked versions include **5.0.3/5.0.4** and **5.1.2–5.1.7**: 5.1.x was installed and tested on hardware, and its kernel, MCU firmware, and partition layout are identical to the 3.1.3 baseline.
  * Follow the instructions [here](/docs/UNINSTALL.md#flashing-factory-firmware) to downgrade to a verified version if needed before proceeding.
  * Note: the official **3.1.5** image does not include printer config files. If needed, flash a `-Factory` image first, then update to the target firmware.
* A USB flash drive formatted to FAT32.
* At least 512MB free space in the `/data` partition.
* At least 128MB free space in the `/` partition.


### Flashing the firmware image

The mod uses the same installation mechanism as the stock firmware:   
1. Uninstall other mods first - if you have any.
2. Download the Forge-X image from the Release [page](https://github.com/drA1ex/ff5m/releases) onto a USB flash drive (⚠️ **Do NOT unpack it!**).  
3. Rename the file to match your printer version. For Pro, rename the file to `Adventurer5MPro-ForgeX-x.x.x.tgz`. For non-Pro, it should remain named `Adventurer5M-ForgeX-x.x.x.tgz`.  
4. Insert the USB flash drive into the printer before powering it on.  
5. The printer will automatically install the update. After the installation is finished, you will see a message at the end of the screen.  
6. Eject the USB drive and reboot the printer.  

After a fresh installation, the printer will boot into the modified firmware with Feather as its default local screen. An update preserves an existing explicit display choice.

From this point onward, you will receive OTA updates from this repository.

You can reach services using these addresses:  
- **Moonraker**: `http://<printer_ip>:7125/`  
- **Fluidd**: `http://<printer_ip>/fluidd/`  
- **Mainsail**: `http://<printer_ip>/mainsail/`  

### OTA Updates

You can update over-the-air to any version that matches your major version. For example, if you have `1.2.0`, you can update to any `1.2.x` version, but not to `1.3.x`.  
To do an OTA update, navigate to **Configuration -> Software Update**.  

OTA updates are supported for Forge-X itself, Fluidd, Mainsail, and Guppy Screen. The web interfaces and Guppy Screen update independently of Forge-X releases.
