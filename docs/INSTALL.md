## Installation

This mod is designed to be uninstalled at any time, completely and effortlessly.  
If the mod encounters issues or you simply prefer to use the stock firmware, you can easily switch back without any complex procedures.   
Additionally, the mod includes a **dual-boot** feature as a failsafe measure. This ensures that you can restore full functionality even if any part of the mod does not work as intended.  
Lastly, in difficult cases, the mod includes recovery and uninstall firmware images, along with an advanced recovery guide, to revert anything that may not be working properly. So, it's relatively safe to try.


> [!CAUTION]
> *After mod installation or uninstallation, always run all calibrations, as the mod can affect certain parameters, invalidating previous calibration settings.*
> *Printing without recalibration may damage the printer, the bed surface, or negatively impact print quality.*
> *Proceed at your own risk!*


> [!WARNING]
> Uninstall other mods, if any are installed.   
> You can leave the Klipper Mod, but make sure to read [this FAQ section](/docs/FAQ.md#do-i-need-to-uninstall-the-earlier-klipper-mod-before-installing-the-new-mod) first.

> [!TIP]
> It is not necessary to uninstall Forge-X before updating minor versions
> e.g. 1.3.x to 1.4.x

### Prerequisites

- A USB flash drive formatted to FAT32.
- At least 512MB free space in the `/data` partition.
- At least 128MB free space in the `/` partition.

### Flashing the firmware image

The mod uses the same installation mechanism as the stock firmware:   
1. Uninstall other mods first - if you have any.
2. Download the Forge-X image from the Release [page](https://github.com/drA1ex/ff5m/releases) onto a USB flash drive (⚠️ **Do NOT unpack it!**).  
3. Rename the file to match your printer version. For Pro, rename the file to `Adventurer5MPro-ForgeX-x.x.x.tgz`. For non-Pro, it should remain named `Adventurer5M-ForgeX-x.x.x.tgz`.  
4. Insert the USB flash drive into the printer before powering it on.  
5. The printer will automatically install the update. After the installation is finished, you will see a message at the end of the screen.  
6. Eject the USB drive and reboot the printer.  

**Note**: The mod installer currently requires the printer to be updated to at least version **2.6.5** of the stock Flashforge firmware.  
After installation, the printer will boot into the modified firmware by default.

From this point onward, you will receive OTA updates from this repository.

You can reach services using these addresses:  
- **Moonraker**: `http://<printer_ip>:7125/`  
- **Fluidd**: `http://<printer_ip>/fluidd/`  
- **Mainsail**: `http://<printer_ip>/mainsail/`  

### OTA Updates

You can update over-the-air to any version that matches your major version. For example, if you have `1.2.0`, you can update to any `1.2.x` version, but not to `1.3.x`.  
To do an OTA update, navigate to **Configuration -> Software Update**.  

OTA updates are supported for the mod itself and for Fluidd.
