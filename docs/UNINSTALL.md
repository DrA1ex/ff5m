# Uninstall

> [!CAUTION]
> Back up your configuration before uninstalling. Removing Forge-X restores stock
> settings, which can change Z offset and bed leveling. Review the settings and
> recalibrate before printing to avoid damage or poor print quality.

Choose the removal mode, then use either method below:

| Command or USB filename | Result |
| --- | --- |
| `REMOVE_MOD` | Remove Forge-X, its saved configuration, root access, and audio tools |
| `REMOVE_MOD_SOFT` | Remove Forge-X while keeping root access, audio tools, and saved mod configuration for reinstallation |

### Run Macro via Fluidd/Mainsail

Run the selected command in the Klipper console. The printer reboots and
uninstalls Forge-X during startup.

### Using USB Drive

Place an empty file named `REMOVE_MOD` or `REMOVE_MOD_SOFT` in the root of a
FAT32 USB drive. Insert it before rebooting the printer. Forge-X detects the
file and uninstalls itself during startup.

## Flashing Factory Firmware  
If uninstallation fails or the stock firmware does not work afterward, follow the factory-firmware steps below.

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

If the printer still cannot start, refer to the [Recovery Guide](/docs/RECOVERY.md) or join the [Telegram group](https://t.me/+ihE2Ry8kBNkwYzhi) to get help.
