# Frequently Asked Questions

To quickly find answers, use the GitHub navigation button at the top-right corner (as shown in the image below).

<p align="center"> <img width="250" src="https://github.com/user-attachments/assets/b9d8e8bd-fcb2-4d9c-afaf-c75306573c55"> </p>

---

## General Questions

### What is the Forge-X mod?
The Forge-X mod is an advanced firmware modification for the Flashforge Adventurer 5M (AD5M) 3D printer, built upon the Klipper firmware and extending the ZMod project. It enhances printer functionality with features like improved macros, optimized resource usage, and support for custom screens (e.g., Feather). The mod aims to provide a stable and extensible platform for advanced 3D printing, including tools for recovery, firmware management, and performance optimization.

### How do I install the Forge-X mod?
To install the Forge-X mod:
1. **Download the Latest Release**: Visit the [Forge-X GitHub releases page](https://github.com/DrA1ex/ff5m/releases) to download the latest firmware image (e.g., Forge-X 1.3.3).
2. **Prepare a USB Drive**: Format a USB drive to FAT32 and copy the firmware image (e.g., `.tgz` file) to the root directory. Ensure files like `klipper_mod_skip` or `SKIP_MOD_SOFT` are included if needed.
3. **Flash the Mod**: Insert the USB drive into the printer, power it on, and follow the on-screen prompts to flash the firmware. The process typically takes a few minutes.
4. **Verify Installation**: After rebooting, check if the printer boots into the expected interface (e.g., GuppyScreen or Feather). If issues occur, refer to recovery steps.
5. **Update Configurations**: Configure settings via Mainsail, Fluidd, or the printer’s interface. Check the [Forge-X documentation](https://github.com/DrA1ex/ff5m) for macro and configuration details.

### What should I do if I encounter issues with the mod?
If you encounter issues:
1. **Check Logs**: Review logs like `clean.log` or `recovery.log` generated during flashing.
2. **Use Recovery Tools**: Try the uninstaller or recovery images to restore the printer.
3. **Consult Documentation**: Refer to the [Forge-X GitHub documentation](https://github.com/DrA1ex/ff5m) for troubleshooting guides.
4. **Ask for Support**: Post in the Forge-X Telegram support group with details like error messages, logs, or photos.
5. **Update Firmware**: Ensure both the stock firmware and Forge-X mod are updated to the latest versions.
6. **Monitor Resources**: Run the `MEM` macro to check memory usage and free up resources if needed.

### Do I need to uninstall the earlier Klipper mod before installing the new mod?
It is recommended to uninstall the earlier Klipper mod before installing the new mod. However, the two mods can work together in a dual-boot setup. By default, the printer boots into the Klipper mod. To boot into the Forge-X mod, insert a USB drive containing the file `klipper_mod_skip`, as described in the Klipper mod documentation under [Dual Boot](https://github.com/xblax/flashforge_ad5m_klipper_mod/blob/master/docs/INSTALL.md#dual-boot).

### Can I install the Klipper Mod over the Forge-X mod?
Yes, you can. The Klipper Mod does not interfere with the Forge-X mod, and you can use it alongside the Dual Boot feature. To boot into the Forge-X mod, insert a USB drive containing the file `klipper_mod_skip`, as described in the Klipper Mod documentation under [Dual Boot](https://github.com/xblax/flashforge_ad5m_klipper_mod/blob/master/docs/INSTALL.md#dual-boot).

---

## Firmware and Installation Issues

### Why does my printer show a frozen spinner or fail to boot after installing a mod?
A frozen spinner or boot failure typically indicates corrupted system files or configuration issues, often caused by:   
- **Temporary Flickering**: Reboot the printer — this usually resolves the issue.   
- **File Corruption**: A corrupted firmware image or incomplete flash.   
- **Configuration Corruption**: Mod-related changes conflicting with the stock firmware.   

**Solutions**:
- **Re-flash the Firmware**: Use a verified factory image or the Forge-X recovery image.
- **Run the Uninstaller**: The uninstaller removes mod-related files and restores the original configuration.
- **Use Recovery Image**: The recovery image restores system files. Run a dry run first to verify compatibility, then perform a full recovery.

### How can I restore my printer if it’s bricked?
A bricked printer (unable to boot or stuck on a frozen spinner) can be restored using:
1. **Uninstaller**: Flash the uninstaller image to remove Forge-X/ZMod files and restore the original configuration.
2. **Recovery Image**: Use the full recovery image (`Adventurer5M-3.x.x-2.2.3-recovery-full.tgz`) to restore system files. Start with a dry run (`Adventurer5M-3.x.x-2.2.3-recovery-dry.tgz`) to verify compatibility.
3. **Factory Firmware**: Flash a verified Flashforge factory image to reset the printer to its original state.
4. **Debugging Port**: As a last resort, use the motherboard’s debugging port to revive the printer (not detailed in the log).

**Steps**:
- Download the recovery or uninstaller images from the support group or GitHub.
- Flash the image via USB, following the same process as mod installation.
- Check logs (`recovery.log`) to confirm the process completed successfully.
- Reboot and verify the printer boots into the stock UI.
- If issues persist, contact the support group with detailed logs and error messages.

### What is the difference between the recovery image and the uninstaller?
- **Uninstaller**: Removes Forge-X and ZMod files, restoring the printer’s original configuration without modifying system files. It’s useful for resolving mod-related issues or configuration corruption.
- **Recovery Image**: Restores corrupted system files, including those critical to the printer’s operation. It’s a more comprehensive fix for bricked printers or severe file corruption. It includes a dry run option to verify compatibility without making changes.

**When to Use**:
- Use the uninstaller for mod-related issues or to revert to stock firmware.
- Use the recovery image for severe issues like corrupted system files or persistent boot failures.

### How do I update the stock firmware?

Stock firmware updates for the Flashforge Adventurer 5M (AD5M) can be performed via the Stock screen interface using Over-The-Air (OTA) updates or by flashing a firmware image via USB.

**Updating via OTA**:

1. Temporarily switch to the Stock screen if using Feather or Headless mode (refer to Screen Switching Guide).
2. Navigate to **Settings** &gt; **Check for Updates** on the Stock screen and follow the prompts to download and install the update.
3. After the update, boot into the stock firmware at least once to ensure the update is applied correctly.
4. Switch back to Feather or Headless mode if desired.

**Updating via USB**:

1. Temporarily switch to the Stock screen if using Feather or Headless mode.
2. Download the latest Flashforge firmware from their official support channels (e.g., Flashforge website or support portal).
3. Copy the firmware file (e.g., `.tgz`) to a USB drive formatted to FAT32.
4. Insert the USB drive into the printer and power on the printer.
5. Follow the on-screen prompts on the Stock screen to flash the firmware.
6. After the update, boot into the stock firmware at least once to confirm the update.
7. Switch back to Feather or Headless mode if desired.

### My printer is stuck on the Stock initialization screen. How can I fix it?
This issue is likely caused by a bug in the newer Flashforge firmware (version `3.1.*`), where the firmware freezes if it fails to connect to a network during initialization. Reboot the printer by powering it off and back on. To avoid this issue, downgrade to firmware version `2.7.*` or switch to the Feather screen, which operates independently of the problematic firmware.

### My printer is stuck on the screen with a black-and-white Flashforge logo. How can I fix it?
This indicates a hardware or software issue preventing proper booting, not related to the mod. Try flashing the [Factory image](https://github.com/DrA1ex/ff5m/blob/main/docs/UNINSTALL.md#flashing-factory-firmware). If unsuccessful, flash the [Uninstall image](https://github.com/DrA1ex/ff5m/blob/main/docs/UNINSTALL.md#using-uninstall-image), then the [Recovery image](https://github.com/DrA1ex/ff5m/blob/main/docs/RECOVERY.md#recovery-using-flashing-image), and finally the Factory image again. Refer to the [Recovery Guide](https://github.com/DrA1ex/ff5m/blob/main/docs/RECOVERY.md). If the issue persists, contact Flashforge support without mentioning mods to avoid warranty issues.

### My printer is stuck on the screen with the Forge-X logo. How can I fix it?
If the printer displays loading information and is stuck, it may be a Wi-Fi issue or a mod-related problem. Skip the mod during boot using the [Dual Boot instructions](https://github.com/DrA1ex/ff5m/blob/main/docs/DUAL_BOOT.md) or uninstall the mod following the [Uninstall guide](https://github.com/DrA1ex/ff5m/blob/main/docs/UNINSTALL.md). Avoid flashing over a different firmware version without removing the mod to prevent bricking. Refer to the [Recovery Guide](https://github.com/DrA1ex/ff5m/blob/main/docs/RECOVERY.md) if needed.

### My printer won’t boot. I can’t skip the mod, flash firmware, or do anything.
This indicates a severe issue. Follow the [Recovery Guide](https://github.com/DrA1ex/ff5m/blob/main/docs/RECOVERY.md) to unbrick the printer using recovery or uninstaller images. If unsuccessful, contact Flashforge support, avoiding mention of mods.

### Why do my custom settings persist in printer.cfg after uninstalling Forge-X and flashing stock firmware?

Custom settings in `printer.cfg` or `printer.base.cfg` may persist after uninstalling Forge-X or flashing stock firmware because Forge-X does not reset user-modified configurations unless you flash the specific Flashforge Factory firmware.

**Solutions**:
- **Flash Factory Firmware**: To fully reset all configurations, including user-edited settings in `printer.cfg`, flash the Factory firmware from Flashforge, not just any stock firmware. Find the Factory firmware link in the [Uninstall Guide](/docs/UNINSTALL.md#flashing-factory-firmware).
- **Use user.cfg for Changes**: Avoid modifying `printer.cfg` directly. Instead, make all Klipper-related changes in `mod_data/user.cfg` to keep custom settings separate and easier to reset.

### Why can't I do an OTA update for Forge-X?

OTA updates are only supported for minor versions (e.g., 1.3.1 → 1.3.4, but not 1.3.4 → 1.4.0). To update to the next major version, you need to flash the latest Forge-X image over your current installation, just like you did when installing Forge-X for the first time.  
This won’t change any of your current settings, and you won’t need to recalibrate.

### I can't install Forge-X because there isn’t enough free space.

You need to free up space depending on which specific partition the error mentions.  
- For `/data`: remove old logs or G-code files.  
- For `/root`: remove old calibration images/logs in **Configuration → mod_data**. If that doesn’t help, use `REMOVE_MOD_SOFT`. After flashing the image, your configuration will persist and you won’t lose anything.

---

## Network and Connectivity Issues

### Why do I get a timeout error when trying to upload G-Code via Slicer?   
Read the [next article](#why-cant-i-access-mainsail-or-fluidd).   

### Why can’t I access Mainsail or Fluidd?

Issues accessing Mainsail or Fluidd on the Flashforge Adventurer 5M (AD5M) with Forge-X are often caused by Wi-Fi connectivity problems, hardware limitations, IP address changes, or resource constraints. The printer’s design, with its Wi-Fi antenna located inside the motor section behind a metal plate, can weaken the signal and lead to unstable connections.

**Causes**:
- **Weak Wi-Fi Signal**: The metal plate covering the motor section blocks Wi-Fi signals, causing silent disconnections or failure to connect without warnings.
- **IP Address Change**: The printer’s IP address may change after installing Forge-X or rebooting, breaking access to Mainsail/Fluidd.
- **Clear browser cache**: An outdated or corrupted browser cache can prevent Mainsail/Fluidd from loading correctly.   
- **Network Configuration**: The mod may fail to connect to Wi-Fi, especially if not configured for a 2.4GHz network.
- **Resource Issues**: High memory or CPU usage can prevent Mainsail/Fluidd from loading properly.
- **Wi-Fi Module Hardware**: Faulty or underperforming Wi-Fi hardware may contribute to connectivity issues.

**Solutions**:
- **Check Wi-Fi Connection**: Verify Wi-Fi status via the printer’s touchscreen settings. Reconnect if disconnected, ensuring a 2.4GHz network is selected.
- **Improve Signal Strength**:
  - Move the printer closer to your router to reduce signal interference.
  - Alternatively, use an Ethernet cable for a stable connection, bypassing Wi-Fi issues.
- **Verify IP Address**: Check the printer’s current IP address on the touchscreen or in your router’s device list. Update your browser’s URL or router’s static IP settings if the address has changed.
- **Clear Browser Cache***: Clear your browser’s cache to resolve issues caused by outdated or corrupted cache data (refer to your browser’s documentation for instructions). Alternatively, try accessing Mainsail/Fluidd using a different browser.   
- **Configure Wi-Fi Manually**:  If your router uses the same SSID for 5GHz and 2.4GHz bands, manually edit `/etc/wpa_supplicant.conf` to [force 2.4 GHz network](#the-mod-isnt-loading-and-is-stuck-at-the-network-connection-step). Restart the printer after editing.   
- **Monitor Resources**: Run the `MEM` macro in Fluidd/Mainsail to check memory usage. If usage is high (e.g., >75%), reduce resource-intensive features like camera streaming or Spoolman (see [Resource Usage Reduction Guide](https://github.com/DrA1ex/ff5m/blob/main/docs/PRINTING.md#reducing-resource-usage)).
- **Ensure Mod Installation**: Confirm Forge-X is fully installed and running (e.g., GuppyScreen or Feather screen is active). Re-flash the mod if necessary (see [Installation Guide](https://github.com/DrA1ex/ff5m/blob/main/docs/INSTALL.md)).
- **Restart Printer**: Reboot the printer to reset network services and clear potential resource bottlenecks.

**Note**: If issues persist, consider testing with a different router or Wi-Fi channel to rule out interference. For hardware-related Wi-Fi module problems, contact Flashforge support, avoiding mention of mods to preserve warranty eligibility.

### Why can’t I connect via SSH?
SSH connection issues may arise because:
- The Klipper and Forge-X mods use different SSH private keys, causing certificate mismatches.
- The printer’s IP address may have changed.
- Resource constraints may prevent the SSH service from running.

**Solutions**:
- **Remove Old SSH Keys**:
  - **Windows**: Use Regedit to remove the printer’s IP entry from `HKEY_CURRENT_USER\Software\SimonTatham\PuTTY\SshHostKeys`.
  - **Linux/macOS**: Edit `~/.ssh/known_hosts` and delete the line corresponding to the printer’s IP.
- **Verify IP Address**: Ensure you’re using the correct IP address.
- **Check Credentials**: Use the default credentials `root/root` for Forge-X.
- **Restart SSH Service**: Reboot the printer or restart the SSH service via the console if accessible.
- **Check Resources**: Run the `MEM` macro to ensure sufficient memory for SSH.

### The mod isn’t loading and is stuck at the network connection step.

This can happen when you are using the Feather screen, and the mod cannot connect to the network.  
Since the mod requires a network connection to function, it will keep attempting to connect until successful.

Printers are often metal-shielded, meaning Wi-Fi signals may struggle to reach the antenna.  
Consider switching to a 2.4GHz Wi-Fi network. You can do this from the stock screen or by manually editing the `/etc/wpa_supplicant.conf` configuration file by adding `freq_list=2412 2417 2422 2427 2432 2437 2442 2447 2452 2457 2462` to the network section, example below.
```bash
network={
        ssid="example"
        psk="password"
        freq_list=2412 2417 2422 2427 2432 2437 2442 2447 2452 2457 2462
        key_mgmt=WPA-PSK WPA-EAP NONE
        disabled=1
}
```

If the mod still cannot connect within 5 minutes, the stock screen will load instead.

If the mod doesn’t load at all, refer to [this instruction](https://github.com/DrA1ex/ff5m/blob/main/docs/SCREEN.md#switching-to-feather-screen) to switch back to the original stock screen.

### Why did the Wi-Fi credentials get forgotten?

This isn’t a case of forgotten Wi-Fi credentials. They’re saved and preserved after every boot.

The actual issue is with the connection - the Wi-Fi module on the Flashforge is weak. Sometimes, the printer can’t connect in time, and the connection menu doesn’t support reconnecting to known networks. Instead, it always tries to establish a new connection, which is why your credentials don’t auto-fill.

To reconnect, you’ll need to disable Wi-Fi and then enable it again. This will force the printer to connect to the last known network with the saved credentials.

---

## Resource and Performance Issues

### What causes “Timer Too Close” or MCU errors (E0011)?
Complex prints (not necessarily large, but with intricate movement patterns like gyroid infill or fuzzy skin) can overload the printer and cause MCU shutdowns. This isn't fixable - it's an MCU processing overload issue, not memory or CPU-related.    

Flashforge explicitly advises against such prints, so this isn't covered under warranty.    
The mod (especially Moonraker) may increase resource overhead, potentially making these shutdowns more frequent.   

Some units experience this more often due to defective components (like toolhead electronics). The only fixes are replacing the toolhead circuit or motherboard - but even this doesn't guarantee anything.   

“Timer Too Close” or MCU errors occur due to:
- **Resource Exhaustion**: High memory or CPU usage, often from running resource-intensive features like Spoolman, or KAMP with “exclude objects” in the slicer.
- **MCU Issues**: Internal sensor read/write issues or loose wiring.
- **Overheating**: Malfunctioning driver fan on the motherboard.
- **Complex G-Code**: Features like Fuzzy Skin or advanced infill patterns (e.g., Gyroid) can significantly increase resource usage, potentially causing indirect errors.

**Solutions**:
- **Check Memory Usage**: Run the `MEM` macro after boot to monitor memory consumption. Aim for usage below 75–80%.
- **Enable `tune_klipper`**: This [mod parameter](/docs/CONFIGURATION.md) optimizes Klipper's internal configuration, which may reduce MCU usage and thereby decrease error rates..
- **Reduce Resource Usage**: Disable features like `weight_check`, `filament_switch_sensor`, or camera streaming. Switch to the Feather screen or Headless mode for lower resource usage (10–15x less than Stock screen).
- **Update Firmware**: Ensure the stock firmware (e.g., 3.1.4 or later) and Forge-X mod are updated.
- **Check Hardware**: Inspect and reattach wiring, especially for the toolhead. Verify the driver fan is operational by removing the printer’s back plate.
- **Optimize G-Code**: Avoid complex infill patterns like Gyroid and Fuzzy Skin option if errors persist. Test simpler infills or print single objects to isolate issues.

### How can I reduce memory usage on my printer?
To reduce memory usage:
- **Run the `MEM` Macro**: Execute the `MEM` macro in the console after boot to check memory usage.
- **Switch to Feather or Headless Mode**: The Feather screen uses ~10MB less memory, and Headless mode uses ~12MB less than the Stock screen.
- **Disable Resource-Intensive Features**: Spoolman, or KAMP’s “exclude objects” feature.
- **Optimize Camera Settings**: Use the mod’s camera implementation for lower resource usage.
- **Follow the Resource Guide**: Refer to the [Resource Usage Reduction Guide](https://github.com/DrA1ex/ff5m/blob/main/docs/PRINTING.md#reducing-resource-usage).

### Can I use KlipperScreen or other resource-heavy features with Forge-X?
KlipperScreen can be used with Forge-X by moving configs/binaries from Klipper Mod, as they are binary compatible. However, it consumes significant resources (75–80% memory usage when running with Mainsail, Fluidd, and a camera). This may lead to “Timer Too Close” or MCU errors, especially on resource-constrained AD5M printers.

**Recommendations**:
- Test KlipperScreen to evaluate usability, but monitor memory usage with the `MEM` macro.
- Consider switching to Feather or Headless mode for better performance.
- Alternatively, use a Raspberry Pi with a BTT screen for a richer interface without overloading the printer.

### What is the Feather screen, and how does it help?
The Feather screen is a lightweight interface for the Forge-X mod, designed to minimize resource usage (10–15x less than the Stock screen). It displays essential printing information and is ideal for users prioritizing performance over UI features.

**Benefits**:
- Reduces memory usage by ~10-15MB compared to the Stock screen.
- Eliminates issues like the Stock screen freezing during Moonraker commands (e.g., `SAVE_CONFIG`).
- Avoids firmware bugs in Flashforge 3.1.* versions (e.g., network-related freezes).
- Supports displaying information with minimal overhead.

**Limitations**:
- Lacks some UI features of KlipperScreen or GuppyScreen (e.g., advanced macros like chamber LED control).

Switch to Feather using the [Screen Switching Guide](https://github.com/DrA1ex/ff5m/blob/main/docs/SCREEN.md#switching-to-feather-screen).

### Why am I getting MCU shutdown with “Unable to obtain ‘endstop_state’ response” or “Timer too close” during START_PRINT?
This occurs when the printer’s weight sensor fails to respond within the requested time due to insufficient system resources or loose wiring.

**Solutions**:
- **Check Connections**: Reattach all wiring, especially for the weight sensor.
- **Reduce Resource Usage**: Follow the [Resource Usage Reduction Guide](https://github.com/DrA1ex/ff5m/blob/main/docs/PRINTING.md#reducing-resource-usage) and use Feather or Headless mode.
- **Update Firmware**: Use stock firmware 3.1.4+ or later and Forge-X 1.3.3+ for optimized performance.


### Why am I Getting "Shutdown due to sensor value exceeding the limit"?

This indicates that Bed Collision Protection was triggered due to excessive pressure on the bed.  
It’s normal if the nozzle hits the bed forcefully, but it could also be a false trigger.

For details on false triggers, see the next article.

### Why am I Getting a "Bed pressure detected" Error?

This safety feature is designed to prevent damage to the printer’s bed caused by the nozzle impacting it. By default, the weight limit is set to **1.2 kg**, but this can be adjusted using the `weight_check_max` parameter.

The system has two stages:  
1. **Warning**: Triggered if the weight exceeds `700g`. This serves as a precautionary notice.  
2. **Error**: Triggered if the weight exceeds a more critical threshold (e.g., `1.2 kg`). At this stage, printing will stop to protect the printer.

To customize the warning limit, you can modify the `user.cfg` file by adding the following:

```cfg
[temperature_sensor weight_value]
trigger_value: 700
```

Be careful: setting values greater than weight_check_max will increase the actual weight value when the error is triggered.

For more information, refer to the [Printing Page](https://github.com/DrA1ex/ff5m/blob/main/docs/PRINTING.md) and the [Configuration Page](https://github.com/DrA1ex/ff5m/blob/main/docs/CONFIGURATION.md).  


#### Possible causes of this error include:  
- **Weight cell calibration issues**: If you manually leveled the bed, it might require recalibration.  
- **Hardware problems**: Printer-related hardware issues may also cause this error.  

#### Resolving the Issue by Calibrating the Load Cell  

To resolve the issue, recalibrate the load cell by following Flashforge's support instructions. You can access the detailed guide via the following [link](https://docs.google.com/document/d/1Oou4A56g5HTrxBAMoH-bTnTZZ3IZyGr_3jL9tUYYiow/edit?usp=drivesdk).

You may need to calibrate the weight cell at the typical temperature you are using during printing (e.g., 70ºC).

#### Temporarily Disabling the Check  

You can either increase the threshold or disable the weight check feature using these commands:

```bash
# Increase threshold
SET_MOD PARAM="weight_check_max" VALUE=1800

# Disable the feature
SET_MOD PARAM="weight_check" VALUE=0
```

*Note:* It is strongly advised to investigate and fix any underlying hardware or kinematic issues after implementing these changes.

### Print/Shaper calibration stops with “MCU ‘mcu’ Shutdown: Timer Too Close”
This error may result from memory limitations, MCU issues, or overheating.

**Solutions**:
- **Memory Issues**:
  - Run the `MEM` macro to check memory usage.
  - Switch to the mod’s [Camera Implementation](https://github.com/DrA1ex/ff5m/blob/main/docs/CAMERA.md) for lower resource usage.
  - Follow the [Resource Usage Reduction Guide](https://github.com/DrA1ex/ff5m/blob/main/docs/PRINTING.md#reducing-resource-usage).
- **MCU Issues**:
  - Disable `weight_check`, `filament_switch_sensor`, or similar parameters.
  - Avoid changing fan or LED settings during printing.
- **Overheating**:
  - Verify the driver fan on the motherboard is operational by removing the back plate and checking for obstructions.

### How do I fix the “VIDIOC_G_FMT: failed: Invalid argument” error when reloading the camera?

This error occurs when the camera streamer encounters an invalid configuration or format issue, often related to memory settings or stock firmware handling.

**Solutions**:
- **Run CAMERA_RESTART**: Execute the `CAMERA_RESTART` macro to reset the camera service, which typically resolves the error.
- **Disable REDUCE_MEMORY**: Verify that the `REDUCE_MEMORY` option is disabled in `camera.conf` (see Camera Configuration Guide). This option can cause format errors.

### How do I fix camera issues without rebooting the printer?

If the camera stream is unstable or stops working, you can restart the camera service without rebooting the printer.

**Solutions**:
- **Run CAMERA_RESTART Macro**: Execute the `CAMERA_RESTART` macro in Fluidd/Mainsail or add it to your slicer’s start G-code to reset the camera before printing. This resolves most camera issues without a reboot.
- **Check Camera Configuration**: Ensure the `REDUCE_MEMORY` option is disabled in `camera.conf` to avoid compatibility issues (see Camera Configuration Guide).

---

## Print and Configuration Issues

### How do I change the parking Z position after a print?
Currently, the parking Z position is hardcoded in Feather mode (50mm down after printing). To change it use `park_dz` mod parameter:
```
SET_MOD PARAM=park_dz VALUE=100
```

### Why do I get errors when printing certain objects or using specific infill patterns?
Errors during printing, especially with complex objects or infill patterns like Gyroid, are typically due to resource exhaustion rather than G-code issues. The same G-code may work multiple times but fail after a reboot due to memory constraints.

**Solutions**:
- **Check Memory Usage**: Run the `MEM` macro to ensure memory usage is below 75–80%.
- **Simplify G-Code**: Use simpler infill patterns (e.g., Grid instead of Gyroid) or print single objects to reduce resource demands.
- **Update Firmware**: Use stock firmware 3.1.4 or later and Forge-X 1.3.3 for optimizations.
- **Switch to Feather**: Reduces resource usage significantly.

### How do I use macros for calibration or other tasks?
Forge-X provides various macros for calibration and management, accessible via Fluidd or Mainsail:
- **Calibration Macros**: Check the “calibration macros group” in Fluidd for macros like auto bed leveling.
- **MEM Macro**: Run in the console to check memory usage.
- **CAMERA_RELOAD**: Applies camera settings manually.
- **NEW_SAVE_CONFIG**: Saves configurations without freezing the Stock screen (compatibility varies).
- **SET_MOD**: Adjusts parameters like `weight_check` or `weight_check_max`.

For a complete list, refer to the [Macro Documentation](https://github.com/DrA1ex/ff5m/blob/main/docs/MACROS.md). Update to Forge-X 1.3.3 to ensure all macros are available in both Fluidd and Mainsail.

### What changes are needed to the G-code start and end commands when migrating from the old Klipper mod?
For the Stock screen, update the G-code start commands as outlined in [Slicing Documentation](https://github.com/DrA1ex/ff5m/blob/main/docs/SLICING.md):
```gcode
START_PRINT EXTRUDER_TEMP=[nozzle_temperature_initial_layer] BED_TEMP=[bed_temperature_initial_layer_single]
M190 S[bed_temperature_initial_layer_single]
M104 S[nozzle_temperature_initial_layer]
```
No changes are required for the Feather screen.

### Can I calibrate the printer using the Stock screen (Fluidd)? Will it work?
Yes, calibration via the Stock screen works, as it interacts directly with Klipper. Changes are reflected in both the stock firmware and the mod. The mod allows setting the calibration temperature, which the Stock screen does not support.

### Unable to access Stock Debug Console for load cell calibration
Use a stylus or thin object to press the console button more precisely.

### Stock screen freezes: I can’t print anything
The Stock screen does not support Moonraker external control, causing freezes when running `SAVE_CONFIG`, `RESTART`, or `FIRMWARE_RESTART`. Reboot the printer to resolve. Use the `NEW_SAVE_CONFIG` macro for graceful configuration saving. Consider switching to the Feather/Guppy screen to avoid this issue, as detailed in the [Screen Guide](https://github.com/DrA1ex/ff5m/blob/main/docs/SCREEN.md).

### Feather screen stuck on “Finishing boot...”
This occurs if Klipper or the MCU fails to become ready, often due to a broken configuration or an unreset MCU after a `reboot` command. Perform a `FIRMWARE_RESET` or power cycle the printer. Access Fluidd or SSH to diagnose the issue.

### Why does the printer boot in Failsafe mode but still show Feather?
Failsafe mode skips all mod code execution to prevent bricking but may still display Feather if the mod partially loads. Use [Dual Boot](https://github.com/DrA1ex/ff5m/blob/main/docs/DUAL_BOOT.md) to skip the mod gracefully and boot into the stock system.

### #Why is my nozzle gouging the build plate during the first layer?

Nozzle gouging, where the nozzle scrapes or digs into the build plate during the first layer, often occurs due to an incorrect Z-offset or bed mesh after installing Forge-X.

**Solutions**:
- **Recalibrate Z-Offset**: After installing Forge-X, recalibrate the Z-offset to ensure the nozzle is at the correct height above the bed. Follow the [Z-Offset Calibration Guide](/docs/PRINTING.md#z-offset). Use `SET_GCODE_OFFSET Z_ADJUST=+0.05 MOVE=1` to raise the nozzle if it’s too close.
- **Run Bed Mesh Calibration**: Perform a full bed mesh calibration with `AUTO_FULL_BED_LEVEL` to ensure the bed mesh reflects the current plate and printer state. Save the mesh with `NEW_SAVE_CONFIG` (Stock screen) or `SAVE_CONFIG` (Feather screen).
- **Check Weight Sensor**: Recalibrate the load cell following Flashforge’s guide.
- **Verify Settings**: Ensure no old settings (e.g., Stock bed mesh) are being used. Flash the Factory firmware to reset all configurations if needed (see Uninstall Guide).
- Refer to the [Printing Page](/docs/PRINTING.md) for calibration details.

### How do I disable nozzle wiping to prevent scratching the build plate?

Nozzle wiping, part of the cleaning procedure before printing, can sometimes scratch build plates.

**Solution**   
**Lower Nozzle Temperature**: Set the nozzle temperature below 120°C during bed leveling to skip the cleaning procedure. For example:
```gcode
AUTO_FULL_BED_LEVEL EXTRUDER_TEMP=100 BED_TEMP=70
```

### How do I customize the KAMP purge line length?

The KAMP purge line length can be adjusted to suit your preferences, such as increasing it for better filament priming.

**Solutions**:
- **Modify user.cfg**: Override the purge amount in `mod_data/user.cfg` by adding:
  ```ini
  [gcode_macro _KAMP_Settings]
  variable_purge_amount: 60  # Increase to desired length (e.g., 60mm)
  ```
- **Set in Slicer G-Code**: Add the following to your slicer’s starting G-code before `START_PRINT`:
  ```gcode
  SET_GCODE_VARIABLE MACRO='_KAMP_Settings' VARIABLE='purge_amount' VALUE=60
  ```
- Refer to the [KAMP Configuration](https://github.com/DrA1ex/ff5m/blob/main/KAMP/KAMP_Settings.cfg#L27) for additional parameters.

---

###  I've changed to a longer nozzle, and now the nozzle hits the bed every time

By default, the Z-axis is calibrated so the standard nozzle just clears the bed at its zero position, enabling prints up to 220mm tall.
A longer nozzle effectively lowers this zero point, shifting the entire Z-axis reference.

Since the printer's movements are based on this zero, it causes collisions. You must recalibrate the Z-axis in the firmware to account for the offset, and adjust limits to prevent exceeding the reduced build height.

Here’s how to update your configuration (example for a 20mm longer nozzle):

1. **Modify the Printer configuration**  
   Update the Z-axis settings in your `user.cfg` file to reflect the new nozzle length. For a 20mm offset, adjust the `position_endstop` and `position_max` as follows:

   ```ini
   [stepper_z]
   position_endstop: 200   ; Default is 220, reduced by 20mm for the longer nozzle
   position_max: 210       ; Default is 230, reduced by 20mm to maintain safe travel
   ```

2. **Update Slicer Settings**  
   In your slicer software, reduce the maximum Z-height to match the new `position_max` (e.g., 210mm). This ensures the slicer doesn’t generate toolpaths that exceed the printer’s adjusted limits.

3. **Recalibrate the Bed Mesh**  
  After updating the configuration, home the Z-axis and verify the new zero point. Ensure the nozzle is at the correct height above the bed to avoid collisions during printing.   
  Clear all existing bed meshes first (as they are now invalid). Then perform a new bed mesh calibration.

---

## Community Contributions and Updates

### What is the thumbnail display feature, and how do I use it?
The thumbnail display feature, contributed by the community, shows print previews on the Feather screen, adding ~1 second to print start time. Download the script and instructions from the provided zip file in the support group. Follow the setup guide to enable it: https://t.me/FF_ForgeX/1906

### How do I stay updated on new Forge-X releases?
- Monitor the [Forge-X GitHub releases page](https://github.com/DrA1ex/ff5m/releases) for updates (e.g., Forge-X 1.3.3).
- Join the Forge-X Telegram support group for announcements and support.
- Check the [Macro Documentation](https://github.com/DrA1ex/ff5m/blob/main/docs/MACROS.md) and [Feather Drawing Utility Documentation](https://github.com/DrA1ex/ff5m/blob/main/docs/TYPER.md) for new features.

### Can I use third-party tools like Obico with Forge-X?
Obico, a Python-based tool, may work as a standalone application (not a Moonraker plugin) but consumes 5–10MB of memory. Test it with the `MEM` macro to ensure it doesn’t cause resource issues. Consider using Feather or Headless mode to free up resources.

---

## Additional Configuration and Troubleshooting

### How do I adjust the camera settings?
Edit the `camera.conf` file or use the web control panel at `http://printer_IP:8080/control.htm`. Manually transfer web panel adjustments to `E_<parameter>` properties in `camera.conf`, as changes are not saved automatically. Apply settings manually with the `CAMERA_RELOAD` macro.

### I adjusted the camera settings, but they are not applied after a reboot
Some camera settings are applied only when a print starts. Use the `CAMERA_RELOAD` macro to apply changes manually at any time.

### What should I do about the warning after an SSH connection: `wtmp_write: problem writing /dev/null/wtmp: Not a directory`?
This warning is harmless and indicates an incomplete core system configuration in the firmware. It does not affect functionality and can be ignored.

### How do I upload files to the printer?
Use `scp` with the `-O` flag for legacy mode:
```bash
# Upload files
scp -O path/to/files/file root@<printer-ip>:/path/to/directory

# Download files
scp -O root@<printer-ip>:/path/to/file /path/to/directory
```
If the `-O` flag is unsupported, upload files via Fluidd’s web interface to `/data`. To download, move files to `/data` and use Fluidd.
