## Dual Boot and Recovery Menu

The mod provides an early recovery menu before its normal services start. You
can use it to start the stock firmware, check system files, download or flash a
recovery image, or enable SSH access.

If the recovery menu itself cannot start, the printer continues into stock
firmware. If recovery is interrupted by a power loss or restart, the next boot
starts stock firmware once. After a completed stock boot, the next reboot
returns directly to the normal Forge-X boot path.

The stock boot path temporarily restores the stock display configuration before
Klipper starts. Your selected Forge-X display mode is applied again on the next
normal Forge-X boot. If this restoration fails or times out, startup still
continues with stock firmware.

To use this feature, you have several options:

### Using the Boot Skip Option

When you see this message during startup:   

```
REBOOT THE PRINTER NOW TO ENTER RECOVERY!
```

Reboot during this message. On the next startup, the **Forge-X Recovery** menu opens.

The menu offers:

- **Stock + SSH** — starts stock firmware without Forge-X services while preserving SSH access.
- **Stock Only** — starts stock firmware without any Forge-X services, including recovery SSH.
- **Firmware Images** — downloads Forge-X releases, factory images, or
  file-recovery images into `/data/forge-x-recovery`. Downloaded images can be
  flashed after two confirmations. Use **Clear All Downloads** in this menu to
  remove every downloaded file. To remove only one file, delete it from
  `/data/forge-x-recovery` in Fluidd or Mainsail.
- **Check System Files** — compares system files with the bundled MD5 list and
  offers the full recovery image if files are missing or changed.
- **Network / SSH** — uses Ethernet or a visible saved Wi-Fi profile and can
  start an SSH-only recovery shell. Configure a new Wi-Fi password later in
  stock firmware if no saved network is available.

The release list includes stable and beta releases. Keep the printer powered
on until any firmware installation reports that it has finished.

### Using USB Drive

Format a USB drive to FAT32 and place an empty file in the root directory:  
- Name the file `SKIP_MOD` for **Stock Only**.
- Name the file `SKIP_MOD_SOFT` for **Stock + SSH**.

Insert the USB drive before turning on the printer. The mod will automatically recognize the USB drive and load in the selected mode.

### Run Macro via Fluidd/Mainsail

Run the macro in Klipper's console:  

- `SKIP_MOD` for **Stock Only**.
- `SKIP_MOD_SOFT` for **Stock + SSH**.

The mod will automatically reboot and load in the selected mode.
