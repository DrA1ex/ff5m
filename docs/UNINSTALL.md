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

## Flashing Factory Firmware  
If you encounter uninstallation issues or firmware not working correctly after removing the mod, you **can** try to restore it by flashing the factory firmware.  

1. Download the Factory Image:   
   Open the [factory firmware release](https://github.com/DrA1ex/ff5m/releases/tag/factory) and download the file for your model: `Adventurer5M-<version>-Factory.tgz` for the 5M or `Adventurer5MPro-<version>-Factory.tgz` for the 5M Pro. Regular (non-factory) images are in the [stock firmware release](https://github.com/DrA1ex/ff5m/releases/tag/Stock).
> [!NOTE]
> The official 3.1.5 firmware available on the Flashforge website does not include printer config files. To install 3.1.5 while restoring those files it's necessary to first flash a factory image and then flash the 3.1.5 firmware.
2. (Optional) Before flashing, compare the file's SHA-256 checksum with the one GitHub shows next to it in the release.
3. Copy it to a USB drive.  
4. Insert the USB drive **before powering up** the printer.  
5. Wait **until** the firmware is installed. Don't reboot the printer **until the installation process is finished**.  
6. Your printer's firmware should be restored to the stock version.  
7. Don't forget to review the settings (or reset them using the Screen) and recalibrate the printer if you are planning to use the stock firmware.  

In **hard** cases, refer to the [Recovery Guide](/docs/RECOVERY.md) or join the [Telegram group](https://t.me/+ihE2Ry8kBNkwYzhi) to get help.  
