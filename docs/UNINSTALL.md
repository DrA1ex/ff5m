## Uninstall

> [!CAUTION]
> After uninstalling the mod, some printer parameters will revert to stock defaults. This can affect settings like Z-Offset, Mesh Bed Leveling, and others.
> It is **strongly recommended** to review all settings and recalibrate the printer to avoid potential damage.

> [!WARNING]
> Uninstalling the mod will remove all custom configurations and settings. If you plan to reinstall the mod later, consider using the `REMOVE_MOD_SOFT` option to preserve root access and configurations.


To remove the mod, you have several options:

### Run Macro via Fluidd/Mainsail

Run the macro in Klipper's console:  
- `REMOVE_MOD` to completely remove the mod.  
- `REMOVE_MOD_SOFT` to remove the mod but preserve the root, audio, and internal mod's configuration (in case you want to install it again).  

The mod will automatically reboot and uninstall itself.

### Using USB Drive

Format a USB drive to FAT32 and place an empty file in the root directory:  
- Named `REMOVE_MOD` to completely remove the mod.  
- Named `REMOVE_MOD_SOFT` to remove the mod but preserve the root, audio, and internal mod's configuration (in case you want to install it again).  

Insert USB drive and reboot the printer. The mod will uninstall itself during boot.

### Using Uninstall Image

This image is meant to restore printer functionality as a last resort; consider using other methods instead.  

> [!NOTE]  
> Uninstalling the mod using this image reverts the configuration to stock, removing all custom configurations, settings, and calibration data.
 
Download the appropriate image for your printer model:  
- **Non-Pro printer version:** [Adventurer5M-ForgeX-uninstall.tgz](https://github.com/DrA1ex/ff5m/releases/download/1.2.0/Adventurer5M-ForgeX-uninstall.tgz)  
- **Pro printer version:** [Adventurer5MPro-ForgeX-uninstall.tgz](https://github.com/DrA1ex/ff5m/releases/download/1.2.0/Adventurer5MPro-ForgeX-uninstall.tgz)   

Next, you can proceed to flash the uninstall image:
1. Format a USB drive to FAT32 and place the Uninstall Image in the root directory (as you do when installing the mod).  
2. Insert the USB drive and reboot the printer. The mod will be uninstalled (this also removes the ZMod files, but it's a last-resort measure. It's better to use the ZMod uninstallation macro instead).  
3. When the process is finished, a message will be displayed on the screen. You will also find a log file in your USB drive with information about the uninstallation.

## Flashing Factory Firmware  
If you encounter uninstallation issues or firmware not working correctly after removing the mod, you **can** try to restore it by flashing the factory firmware.  
It's **recommended** to flash the uninstall image first, to ensure there are no mod files left on the printer.

1. Download the Factory Image:   
   Make sure to select the correct version for your printer (Pro or Non-Pro) based on your specific model. You can access the factory firmware here:  
   - **2.7.8 Factory images**
     - Non-Pro: [Adventurer5M-2.7.8-2.2.3-20241213-Factory.tgz](https://github.com/DrA1ex/ff5m/releases/download/1.2.0/Adventurer5M-2.7.8-2.2.3-20241213-Factory.tgz)
     - Pro: [Adventurer5MPro-2.7.8-2.2.3-20241213-Factory.tgz](https://github.com/DrA1ex/ff5m/releases/download/1.2.0/Adventurer5MPro-2.7.8-2.2.3-20241213-Factory.tgz)   
   - **3.1.3 Factory images**
     - Non-Pro: [Adventurer5M-3.1.3-2.2.3-20250107-Factory.tgz](https://github.com/DrA1ex/ff5m/releases/download/1.2.0/Adventurer5M-3.1.3-2.2.3-20250107-Factory.tgz)   
     - Pro: [Adventurer5MPro-3.1.3-2.2.3-20250107-Factory.tgz](https://github.com/DrA1ex/ff5m/releases/download/1.2.0/Adventurer5MPro-3.1.3-2.2.3-20250107-Factory.tgz)
> [!NOTE]
> The official 3.1.5 firmware available on the Flashforge website does not include printer config files. To install 3.1.5 while restoring those files it's necessary to first flash the above 3.1.3 firmware and then flash the 3.1.5 firmware.
2. (Optional) Before flashing, check the integrity of the firmware file using the MD5 checksum:  
   - **Version 2.7.8**:  
     - Non-Pro: `608cb3830e69d1ff946bf699d69c491f`  
     - Pro: `5470a03d8dd7d5bc15140b0922b6e4fe`  
   - **Version 3.1.3**:  
     - Non-Pro: `bda2f882433d57ef0a0c9808b96aaf00`  
     - Pro: `ae83c3b5fcb9181aec8fc9e5e821d5f4`  
3. Copy it to a USB drive.  
4. Insert the USB drive **before powering up** the printer.  
5. Wait **until** the firmware is installed. Don't reboot the printer **until the installation process is finished**.  
6. Your printer's firmware should be restored to the stock version.  
7. Don't forget to review the settings (or reset them using the Screen) and recalibrate the printer if you are planning to use the stock firmware.  

In **hard** cases, refer to the [Recovery Guide](/docs/RECOVERY.md) or join the [Telegram group](https://t.me/+ihE2Ry8kBNkwYzhi) to get help.  
