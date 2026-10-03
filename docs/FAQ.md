# Frequently Asked Questions

To quickly find answers, use the GitHub navigation button at the top-right corner (as shown in the image below).

<p align="center"> <img width="250" src="https://github.com/user-attachments/assets/b9d8e8bd-fcb2-4d9c-afaf-c75306573c55"> </p>

## Quick answers

- [What is Forge-X, and which printers does it support?](#what-is-forge-x)
- [Why doesn't Forge-X use Klipper 0.13?](#why-doesnt-forge-x-use-klipper-013)
- [Can I add my own macros, plugins, or software?](#can-i-add-my-own-macros-plugins-or-software)
- [Can I go back to the stock firmware?](#is-forge-x-reversible-can-i-go-back-to-the-stock-firmware)
- [The printer is stuck on the Forge-X logo](#my-printer-is-stuck-on-the-screen-with-the-forge-x-logo-how-can-i-fix-it)
- [The printer will not boot at all](#my-printer-wont-boot-i-cant-skip-the-mod-flash-firmware-or-do-anything)
- [I can't open Fluidd or Mainsail](#why-cant-i-access-mainsail-or-fluidd)
- [The Stock screen freezes](#stock-screen-freezes-i-cant-print-anything)
- [`Timer too close` or MCU errors](#what-causes-timer-too-close-or-mcu-errors-e0011)
- [Memory usage is too high](#how-can-i-reduce-memory-usage-on-my-printer)
- ["Bed pressure detected" error](#why-am-i-getting-a-bed-pressure-detected-error)
- [Camera settings and problems](#how-do-i-adjust-the-camera-settings)
- [Wi-Fi password was forgotten](#why-did-the-wi-fi-credentials-get-forgotten)

All questions are grouped below: [General](#general-questions) · [Firmware and installation](#firmware-and-installation-issues) · [Network](#network-and-connectivity-issues) · [Resources and performance](#resource-and-performance-issues) · [Printing and configuration](#print-and-configuration-issues) · [Community and updates](#community-contributions-and-updates) · [Camera and other topics](#additional-configuration-and-troubleshooting)

---

## General Questions

### What is Forge-X?
Forge-X is a free, open-source (GPL-3.0), unofficial firmware mod for the **Flashforge Adventurer 5M and 5M Pro** (AD5M) 3D printers. It runs on top of the stock firmware and adds:

- **Klipper** with [fixes and hardening adapted for the AD5M](KLIPPER.md), plus **Moonraker**, **Fluidd**, and **Mainsail**;
- **Feather**, a built-in lightweight touchscreen interface, along with Stock, Guppy, and Headless display modes;
- guided calibration (bed screws, bed mesh, Z offset, extruder, PID, Input Shaper), KAMP adaptive meshing, and print-safety features such as bed collision protection and bed mesh validation;
- [Power Loss Recovery](POWER_LOSS_RECOVERY.md) for non-Stock screens;
- [forge-x-streamer](CAMERA.md), a camera service designed for the printer's 128 MiB of RAM;
- OTA updates, backup and restore, and a [recovery system](RECOVERY.md) with a boot guard, a recovery menu, and fallback to the stock firmware.

The project focuses on reliability on limited hardware: memory and CPU use, handling of failures, recovery, and regression testing. See [Why Forge-X Was Developed](../README.md#why-forge-x-was-developed) and [Development, testing, and release validation](DEVELOPMENT.md).

### Which printers and firmware versions does Forge-X support?
Forge-X supports only the **Flashforge Adventurer 5M** and **Adventurer 5M Pro**. Other Flashforge models are not supported.

The supported stock firmware range is **2.6.5 up to 5.1.x**. See [Installation → Prerequisites](INSTALL.md#prerequisites) for the exact verified versions and notes.

### Is Forge-X reversible? Can I go back to the stock firmware?
Yes. Forge-X is installed as a layer on top of the stock firmware and is designed to be removed at any time:

- the `REMOVE_MOD` / `REMOVE_MOD_SOFT` macros or USB files uninstall it (see [Uninstall](UNINSTALL.md));
- every stock Klipper file that Forge-X replaces keeps a `.bak` copy, which uninstall restores;
- the [Dual Boot and recovery menu](DUAL_BOOT.md) lets you start the stock firmware without Forge-X services;
- if an update or initialization is interrupted, the next boot falls back to the stock firmware instead of repeating a broken start;
- recovery, uninstall, and factory images are available for severe cases (see the [Recovery guide](RECOVERY.md)).

After installing or uninstalling, always recalibrate the bed mesh and Z offset.

### Which Klipper version does Forge-X use?
Forge-X keeps the Klipper host that FlashForge ships with the AD5M (0.11 generation) and backports selected fixes and features from newer upstream Klipper. The MCU firmware is not reflashed. This keeps the stack compatible with the printer's hardware and its 128 MiB of RAM. The complete list of backports and AD5M-specific changes is in [Klipper fixes and AD5M-specific hardening](KLIPPER.md).

### Which web interfaces and screens are available?
- **Web:** Fluidd at `http://<printer_ip>/fluidd/`, Mainsail at `http://<printer_ip>/mainsail/`, and the Moonraker API at `http://<printer_ip>:7125/`.
- **Local screen:** Feather (default after a fresh installation), Stock, Guppy, or Headless. See [Screen Configuration](SCREEN.md). HelixScreen is available from its own project and is not an internal Forge-X display mode.

### Which slicers can I use with Forge-X?
Forge-X uploads G-code through Moonraker, so slicers that support a Moonraker/Klipper host work, such as OrcaSlicer. The start and end G-code, and the Moonraker host settings, are described in [Slicing](SLICING.md). KAMP requires object labels, with examples for OrcaSlicer and PrusaSlicer in [Printing](PRINTING.md#kamp).

### Why does Forge-X support only the AD5M?
Forge-X is built for one printer family, so each decision can be based on its real hardware: 128 MiB of RAM, the FlashForge Klipper 0.11 base, two MCUs, the load-cell bed sensor, and the stock boot process. Memory use, recovery, and testing are designed and verified on the AD5M and AD5M Pro. Other printers would need their own port, design work, and hardware testing.

### Why doesn't Forge-X use Klipper 0.13?
Forge-X keeps the Klipper host and MCU firmware that FlashForge ships with the printer and backports newer fixes and features into it. The reasons:

- the MCU firmware is not reflashed, so there is no host/MCU version mismatch to manage and the stock firmware keeps working;
- uninstall and recovery stay simple, because every replaced Klipper file has a `.bak` original;
- the printer has only 128 MiB of RAM, so a larger Klipper host and its services leave less room for printing;
- each backport is reviewed and covered by tests before a release.

Backported items include the `Timer too close` starvation fix, multi-MCU homing fixes, Adaptive Pressure Advance (so recent OrcaSlicer adaptive PA profiles can be used), and fixes for buttons, heaters, servos, and input shaper calibration. The full list, with upstream commit links, is in [Klipper fixes and AD5M-specific hardening](KLIPPER.md).

### Can I add my own macros, plugins, or software?
Yes. You can customize Forge-X without editing the mod's own files:

- **Klipper:** add your own macros and sections, or override Forge-X macros and settings, in `mod_data/user.cfg`.
- **Feather dialogs:** show your own dialogs on the Feather screen with standard Klipper `action:prompt_*` messages.
- **Programs:** run your own scripts from G-code with `RUN_SHELL_COMMAND`.
- **Services:** put a start/stop script named `S<number><name>` into `/etc/init.d` of the Forge-X environment, and Forge-X starts it at boot.
- **Moonraker:** add your own configuration to `mod_data/user.moonraker.conf`.
- **Linux software:** install packages with Entware.
- **Display:** use `HEADLESS` mode with your own display process, draw with [Typer](TYPER.md), or use a third-party screen such as HelixScreen.

Details and examples are in [Customizing and extending Forge-X](EXTENDING.md). Every added service uses some of the printer's 128 MiB of RAM, so check the result with the `MEM` macro.

### How are Forge-X updates delivered?
Forge-X releases contain changes to Forge-X itself: fixes, features, and reviewed Klipper patches. Updates are installed from **Configuration → Software Update** (Moonraker Update Manager):

- **Forge-X** updates within the same major version (for example, 1.4.1 → 1.4.2) over OTA. A new major version is flashed over the existing installation, and your settings and calibration are kept.
- **Fluidd, Mainsail, and Guppy Screen** have their own update entries and update independently, so a new web interface version does not need a new Forge-X release.

### Is Forge-X free?
Yes. Forge-X is open source under the GPL-3.0 license and free to use. Donations are optional; see [Support Forge-X](../README.md#support-forge-x).

### Where can I get help?
Read this FAQ and the documentation first. If that does not solve the problem, ask in the [Telegram support group](https://t.me/+ihE2Ry8kBNkwYzhi) or in the [Discord server](https://discord.gg/K7MH4hAfeX) (Forums → mods-and-projects → Forge-X). Open a [GitHub issue](https://github.com/DrA1ex/ff5m/issues) only if you are sure it is a bug.

### How do I install Forge-X?
In short:
1. **Uninstall any other mods** first and back up your settings.
2. **Download the image** from the [Forge-X releases page](https://github.com/DrA1ex/ff5m/releases). Do not unpack it.
3. **Prepare a USB drive** formatted as FAT32 and copy the image to its root. For the Pro model, rename the file to `Adventurer5MPro-ForgeX-x.x.x.tgz`.
4. **Insert the USB drive before powering on** the printer. The printer installs the update automatically and shows a message when it finishes.
5. **Eject the drive, reboot, and recalibrate** the bed mesh and Z offset.

The full procedure, prerequisites, and OTA update rules are in the [Installation guide](INSTALL.md).

### What should I do if I run into problems with the mod?
1. **Check the logs.** Review logs such as `clean.log` or `recovery.log` that are written to the USB drive during flashing.
2. **Use the recovery tools.** Use the [recovery menu](DUAL_BOOT.md), the uninstaller, or the recovery images to restore the printer.
3. **Read the documentation.** Start with the [Recovery guide](RECOVERY.md) and the troubleshooting sections below.
4. **Ask for help.** Post in the Forge-X Telegram group with error messages, logs, or photos.
5. **Keep everything up to date.** Use the latest Forge-X release and a supported stock firmware version.
6. **Monitor resources.** Run the `MEM` macro to check memory usage and free up resources if needed.

### Do I need to uninstall another mod before installing Forge-X?
Yes, uninstall other mods first and make a backup. The one exception is Klipper Mod's dual-boot feature: Klipper Mod stays the default, and a USB drive with a `klipper_mod_skip` file boots Forge-X instead. See the [Klipper Mod dual-boot documentation](https://github.com/xblax/flashforge_ad5m_klipper_mod/blob/master/docs/INSTALL.md#dual-boot). Do not flash different mods over each other unless you clearly understand what you are doing.

### Can I install Klipper Mod over Forge-X?
Yes. Klipper Mod does not interfere with Forge-X, and the two can be used together through Klipper Mod's dual-boot feature. To boot into Forge-X, insert a USB drive that contains a `klipper_mod_skip` file, as described in the [Klipper Mod dual-boot documentation](https://github.com/xblax/flashforge_ad5m_klipper_mod/blob/master/docs/INSTALL.md#dual-boot).

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
4. **Debugging port (UART/FEL)**: As a last resort, use the motherboard’s debugging port to revive the printer. See the [Recovery guide](RECOVERY.md#recovery-using-uart).

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

### How can I disable FlashForge's firmware update notifications?

You can disable network access to the update servers. See how to do it [here](https://t.me/FF_ForgeX/4255) (Use WinSCP / PuTTY / nano via SSH to edit the file).

After uninstalling Forge‑X, this file will revert automatically, so you won’t need to change it back yourself.

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
- **Monitor Resources**: Run the `MEM` macro in Fluidd/Mainsail to check memory usage. If usage is high (e.g., >75%), reduce resource-intensive features like camera streaming or Spoolman (see [Reliability and resources](PRINTING.md#reducing-resource-usage)).
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

On the **Stock Screen**, the boot workflow can wait for its configured network and eventually return to the stock application if the mod cannot establish it. The troubleshooting below remains applicable to that workflow.

The **Feather Screen** does not wait for networking before loading. Its dashboard shows **CONNECTING** while the saved connection is being restored. Open **Network** to see the current connection step. You can keep waiting or press **CANCEL** to remain offline and return to the Network page. If the attempt has already ended, Feather remains usable offline and allows a new Wi-Fi or Ethernet connection.

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

In the blocking Stock workflow, the stock screen loads if the mod still cannot connect within the configured retry period.

If the mod doesn’t load at all, use [screen-mode recovery](SCREEN.md#switching-to-feather-screen) to switch back to the original stock screen.

### Why did the Wi-Fi credentials get forgotten?

On the **Stock Screen**, this is not a case of forgotten credentials: they are saved and preserved after every boot. The actual issue is usually the connection. The Stock connection menu does not reconnect a known network directly and may ask for its password again. Disable and re-enable Wi-Fi to make Stock reconnect with the saved credentials.

The **Feather Screen** behaves differently. A saved network is marked in its scan list and reconnects without asking for the password. Use **RESET PASSWORD** when the credential really has changed.

---

## Resource and Performance Issues

### What causes “Timer Too Close” or MCU errors (E0011)?
`Timer too close` is a generic Klipper shutdown reason: the MCU received work scheduled too close to its current clock because the host or the MCU connection fell behind. Complex prints (not necessarily large, but with intricate movement patterns like gyroid infill or fuzzy skin) can overload the printer and cause MCU shutdowns.   

Forge-X 1.4.2 fixes the reproducible case in which very dense G-code starves Klipper's host-side scheduling (see [Klipper fixes and AD5M-specific hardening](KLIPPER.md#the-timer-too-close-problem)). Other causes, such as hardware faults, remain possible.   
The mod (especially Moonraker) adds some resource overhead, which can make shutdowns more likely on a printer that is already close to its limits.   

Some units experience this more often because of defective components (like toolhead electronics). In that case, the only fixes are replacing the toolhead board or the motherboard, and even that is not guaranteed to help.   

“Timer Too Close” or MCU errors occur due to:
- **Resource Exhaustion**: High memory or CPU usage, often from running resource-intensive features like Spoolman, or KAMP with “exclude objects” in the slicer.
- **MCU Issues**: Internal sensor read/write issues or loose wiring.
- **Overheating**: Malfunctioning driver fan on the motherboard.
- **Complex G-Code**: Features like Fuzzy Skin or advanced infill patterns (e.g., Gyroid) can significantly increase resource usage, potentially causing indirect errors.

**Solutions**:
- **Check Memory Usage**: Run the `MEM` macro after boot to monitor memory consumption. Aim for usage below 75–80%.
- **Enable `tune_klipper`**: This [mod parameter](/docs/CONFIGURATION.md) optimizes Klipper's internal configuration, which can reduce MCU load and lower the error rate.
- **Reduce Resource Usage**: Disable features like `weight_check`, `filament_switch_sensor`, or camera streaming. Switch to the Feather screen or Headless mode for lower resource usage (Feather typically needs around a tenth of the memory of the Stock screen).
- **Update Firmware**: Make sure the stock firmware (for example, 3.1.4 or later) and Forge-X are up to date.
- **Check Hardware**: Inspect and reattach wiring, especially for the toolhead. Verify the driver fan is operational by removing the printer’s back plate.
- **Optimize G-Code**: Avoid complex infill patterns like Gyroid and Fuzzy Skin option if errors persist. Test simpler infills or print single objects to isolate issues.

### How can I reduce memory usage on my printer?
To reduce memory usage:
- **Run the `MEM` Macro**: Execute the `MEM` macro in the console after boot to check memory usage.
- **Switch to Feather or Headless Mode**: The Stock screen normally uses roughly 10–20 MB of RAM, while Feather uses roughly 1–2 MB and Headless mode uses none. See [Reducing resource usage](PRINTING.md#reducing-resource-usage).
- **Disable Resource-Intensive Features**: Spoolman, or KAMP’s “exclude objects” feature.
- **Optimize Camera Settings**: Use the mod’s camera implementation for lower resource usage.
- **Follow the Resource Guide**: Refer to [Reliability and resources](PRINTING.md#reducing-resource-usage).

### Can I use KlipperScreen or other resource-heavy features with Forge-X?
KlipperScreen can be used with Forge-X by moving configs/binaries from Klipper Mod, as they are binary compatible. However, it consumes significant resources (75–80% memory usage when running with Mainsail, Fluidd, and a camera). This may lead to “Timer Too Close” or MCU errors, especially on resource-constrained AD5M printers.

**Recommendations**:
- Test KlipperScreen to evaluate usability, but monitor memory usage with the `MEM` macro.
- Consider switching to Feather or Headless mode for better performance.
- Alternatively, use a Raspberry Pi with a BTT screen for a richer interface without overloading the printer.

### What is the Feather screen, and how does it help?
Feather is Forge-X's lightweight interactive touchscreen. It uses much less memory than the Stock screen while still covering the main local workflows needed for normal printing.

From Feather you can browse and print local or USB files, control an active print, move and home the printer, manage temperatures and cooling, change filament, run supported calibrations, configure normal Wi-Fi or Ethernet, adjust lighting and sound, and change Forge-X settings. It also presents Forge-X power-loss recovery when a valid recovery state is available. See the [Power Loss Recovery guide](POWER_LOSS_RECOVERY.md) for limitations and safe use.

Fluidd or Mainsail is still required for unrestricted G-code, file deletion, advanced networking, and detailed diagnostics. See the [Screen Configuration guide](SCREEN.md#feather-screen) for the current feature list and switching instructions.

### Why am I getting MCU shutdown with “Unable to obtain ‘endstop_state’ response” or “Timer too close” during START_PRINT?
This occurs when the printer’s weight sensor fails to respond within the requested time due to insufficient system resources or loose wiring.

**Solutions**:
- **Check Connections**: Reattach all wiring, especially for the weight sensor.
- **Reduce Resource Usage**: Follow [Reliability and resources](PRINTING.md#reducing-resource-usage) and use Feather or Headless mode.
- **Update Firmware**: Use stock firmware 3.1.4 or later and the latest Forge-X release.


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
[temperature_sensor weightValue]
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
  - Follow [Reliability and resources](PRINTING.md#reducing-resource-usage).
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
Use the `park_dz` mod parameter to change the relative Z lift after a completed print:
```
SET_MOD PARAM=park_dz VALUE=100
```

This is separate from `safe_z`, which controls the absolute safe height used before lateral parking, cleaning, and calibration moves.

### Why do I get errors when printing certain objects or using specific infill patterns?
Errors during printing, especially with complex objects or infill patterns like Gyroid, are typically due to resource exhaustion rather than G-code issues. The same G-code may work multiple times but fail after a reboot due to memory constraints.

**Solutions**:
- **Check Memory Usage**: Run the `MEM` macro to ensure memory usage is below 75–80%.
- **Simplify G-Code**: Use simpler infill patterns (e.g., Grid instead of Gyroid) or print single objects to reduce resource demands.
- **Update Firmware**: Use stock firmware 3.1.4 or later and the latest Forge-X release.
- **Switch to Feather**: Reduces resource usage significantly.

### How do I use macros for calibration or other tasks?
Forge-X provides various macros for calibration and management, accessible via Fluidd or Mainsail:
- **Calibration Macros**: Check the “calibration macros group” in Fluidd for macros like auto bed leveling.
- **MEM Macro**: Run in the console to check memory usage.
- **CAMERA_RELOAD**: Applies camera settings manually.
- **NEW_SAVE_CONFIG**: Saves configurations without freezing the Stock screen (compatibility varies).
- **SET_MOD**: Adjusts parameters like `weight_check` or `weight_check_max`.

For a complete list, see the [Macro Documentation](MACROS.md). Use the latest Forge-X release to make sure all macros are available in both Fluidd and Mainsail.

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

### Why is my nozzle gouging the build plate during the first layer?

Nozzle gouging, where the nozzle scrapes or digs into the build plate during the first layer, often occurs due to an incorrect Z-offset or bed mesh after installing Forge-X.

**Solutions**:
- **Recalibrate Z-Offset**: After installing Forge-X, recalibrate the Z-offset to ensure the nozzle is at the correct height above the bed. Follow the [Z-Offset guidance](CALIBRATION.md#z-offset-calibration). On the FF5M, increase Z-Offset to move the bed farther from the nozzle when the first layer is too close.
- **Run Bed Mesh Calibration**: Perform a full bed mesh calibration with `AUTO_FULL_BED_LEVEL` to ensure the bed mesh reflects the current plate and printer state. Save the mesh with `NEW_SAVE_CONFIG` (Stock screen) or through the supported Forge-X workflow (or use SAVE_CONFIG).
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
   In your slicer software, reduce the maximum Z-height to match the new `Z-Position Max` (e.g., 200mm). This ensures the slicer doesn’t generate toolpaths that exceed the printer’s adjusted limits.

3. **Recalibrate the Bed Mesh**  
  After updating the configuration, home the Z-axis and verify the new zero point. Ensure the nozzle is at the correct height above the bed to avoid collisions during printing.   
  Clear all existing bed meshes first (as they are now invalid). Then perform a new bed mesh calibration.

---

## Community Contributions and Updates

### What is the thumbnail display feature, and how do I use it?
The thumbnail display feature, contributed by the community, shows print previews on the Feather screen, adding ~1 second to print start time. Download the script and instructions from the provided zip file in the support group. Follow the setup guide to enable it: https://t.me/FF_ForgeX/1906

### How do I stay updated on new Forge-X releases?
- Watch the [Forge-X GitHub releases page](https://github.com/DrA1ex/ff5m/releases) for new versions.
- Join the Forge-X Telegram support group for announcements and help.
- Check the [Macro Documentation](https://github.com/DrA1ex/ff5m/blob/main/docs/MACROS.md) and [Feather Drawing Utility Documentation](https://github.com/DrA1ex/ff5m/blob/main/docs/TYPER.md) for new features.

### Can I use third-party tools like Obico with Forge-X?
Obico, a Python-based tool, may work as a standalone application (not as a Moonraker plugin), but it uses 5–10 MB of memory. Test it with the `MEM` macro to ensure it doesn’t cause resource issues. Consider using Feather or Headless mode to free up resources.

---

## Additional Configuration and Troubleshooting

### How do I adjust the camera settings?
Open `http://printer_IP:8080/control.htm` for a continuously updating preview
and the supported image controls. Each edit is applied to the running camera
automatically. The panel reads ranges and menu entries from the camera, and
**Save** atomically writes the visible values to `camera.conf` so they survive a
restart. If you edit `camera.conf` manually, run `CAMERA_RELOAD` to apply it
without restarting the HTTP stream service. The panel uses the existing camera
HTTP server and MJPEG endpoint; it does not start a second server.

For numeric selectors whose camera-reported minimum is greater than zero, the
panel adds `0 (special)` because some drivers still accept zero as an
undocumented off value (for example, Gain). `Shift` + arrow changes numeric
controls by ten steps instead of one. Holding an arrow key applies an
intermediate value once per second, then applies the final value shortly after
the key is released.

### I adjusted the camera settings, but they are not applied after a reboot
Make sure `POST_PROCESSING=1` and the desired `E_<parameter>` lines are
uncommented in `camera.conf`, or use **Save** in the control panel.
Saved controls are loaded on service start and applied after the camera's first
completed frame. `CAMERA_RELOAD` can be used to reread the file manually.

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
