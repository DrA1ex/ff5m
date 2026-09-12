# Screen Configuration

The FlashForge Stock screen is tightly coupled to the vendor services. It is convenient for the original workflow, but direct Klipper commands such as `SAVE_CONFIG` or `RESTART` can freeze the vendor application, and the screen consumes considerably more memory than the lightweight alternatives.

Forge-X supports four display modes:

| Mode | Use it when |
| --- | --- |
| `STOCK` | You want the original FlashForge screen, upload path, and vendor workflow. |
| `FEATHER` | You want Forge-X's lightweight local touchscreen controls. |
| `GUPPY` | You prefer the separate Guppy touchscreen interface. |
| `HEADLESS` | You control the printer remotely or provide your own display process. |

> [!WARNING]
> Do not change display mode during a print. Before switching, make sure you have a working network connection or a recovery route through [Dual Boot](DUAL_BOOT.md).

## Alternative Screens

### Stock screen

The Stock screen keeps the original FlashForge services and workflows. Enable **Settings → Network → Network Mode → LAN-mode** if you use Fluidd, Mainsail, slicer upload, or Forge-X features that communicate with the vendor API.

When using the Stock screen:

- the persistent bed-mesh profile is `MESH_DATA`;
- Z offset is managed by the vendor workflow;
- use `NEW_SAVE_CONFIG`, not `SAVE_CONFIG` or `RESTART`.

### Feather Screen

Feather is Forge-X's built-in low-resource touchscreen. Unlike the early display-only implementation, current Feather versions provide the main local workflows needed for everyday printing.

#### Home screen and navigation

The home screen shows the current job, nozzle and bed state, material, toolhead, network, and the previous print. The main menu provides direct access to printing, printer controls, filament handling, calibration, networking, and settings.

The first touch after the panel dims only wakes the display; it does not activate the control under the finger.

#### Local files and print control

Feather can browse G-code stored on the printer or on a connected USB drive. It supports folders, multi-page file lists, refresh, file information, print confirmation, and a recent-print list.

During a print, Feather shows progress, elapsed and remaining time, layer and height information. It provides pause, resume, filament change, live Z adjustment, and guarded cancellation. Preparation reports the separate context path and current state, for example `PRINT PREP -> MESH VALIDATION -> HEATING NOZZLE`, instead of relying on a caller-provided `CONTEXT`/`STAGE` string.

Cancellation is also available while the printer is preparing a job. Normal **Cancel** accepts `interruptible` work and uses the nearest explicit `cancelable` cleanup domain when one exists; homing, probing, and motion remain atomic until the next context boundary. Managed temperature waits are interrupted immediately through `M108`. A pending request offers **Continue Operation** or immediate `M112`, while `non_interruptible` work offers only Continue or `M112`.

#### Movement, heating, and lighting

Feather provides both step movement and joystick movement. The printer must be in an appropriate idle state, and movement is kept within the configured limits. Separate controls are available for homing, nozzle and bed heating, material preheat, cooldown, the part-cooling fan, and chamber-light brightness. Hardware-active pages provide emergency-stop access.

#### Filament and calibration

Material presets are shared with the Forge-X filament macros. Feather remembers the selected material and provides guided loading, unloading, purging, and filament-change actions, including during a paused print.

The Calibration page includes guided workflows for bed screws, bed mesh, Safe Z, Z offset, extruder feed, PID, and Input Shaper. Follow the instructions on the screen and review the result before saving it.

#### Update notifications

When Moonraker reports that a newer Forge-X version is available, Feather can show the new version and a short, scrollable list of changes while the printer is idle. Select **UPDATE** to start the normal Forge-X OTA update, or **LATER** to hide that version until the printer or Klipper restarts. If a print starts, the notification closes immediately and may return after the printer is idle again.

#### Network, settings, and themes

Feather can scan for 2.4 and 5 GHz Wi-Fi networks, enter normal WPA/WPA2-PSK credentials with the on-screen keyboard, configure DHCP Ethernet, and show the live connection state, signal, and IP address. Its Wi-Fi list presents the band, SSID, and signal in separate columns. A saved network is marked in the scan list and reconnects immediately when selected; use **RESET PASSWORD** to replace its credential.

Feather starts without waiting for the saved network. While that startup connection is active, the dashboard shows **CONNECTING** and the Network page can either keep waiting or cancel it before choosing another network. The screen keeps the connection state current while the printer reconnects. Any network change may briefly take the printer offline. If a Wi-Fi change fails, Feather returns to the previous saved Wi-Fi network when possible.

Static addressing and enterprise Wi-Fi still require advanced configuration outside Feather.

Feather Settings provides display brightness, chamber-light level, sound feedback, Forge-X parameters, and theme selection. Settings that require a Klipper or printer restart are identified before they are applied.

Advanced operations such as unrestricted G-code, file deletion, static or enterprise Wi-Fi, and detailed diagnostics remain in Fluidd or Mainsail.

Feather can operate without a network after configuration. It uses the `auto` bed-mesh profile. Recreate or rename the persistent mesh after switching from Stock mode.

### Guppy Screen

Guppy provides a separate interactive touchscreen interface with lower resource usage than the Stock screen. It can control printing and common printer functions, but its workflows and feature coverage differ from Feather.

Guppy uses the `auto` bed-mesh profile and relies on Moonraker-compatible upload and control paths rather than the FlashForge vendor services.

### Headless mode

Headless mode disables the local UI and is intended for remote control or a custom display implementation. Make sure networking and remote access work before enabling it.

Headless mode also uses the `auto` bed-mesh profile. Z offset, camera, and print control must be handled through Forge-X, Fluidd/Mainsail, or your own integration.

### Switching to Alternative Screens / Headless

#### Switching to Feather Screen

Run one of these commands from the console:

```gcode
SET_MOD PARAM=display VALUE=FEATHER
SET_MOD PARAM=display VALUE=GUPPY
SET_MOD PARAM=display VALUE=HEADLESS
SET_MOD PARAM=display VALUE=STOCK
```

Switching away from Stock stops the FlashForge companion services. As a result:

- FlashPrint and FlashForge Orca vendor upload/control paths are unavailable;
- upload and control jobs through the [Moonraker slicing workflow](SLICING.md);
- the persistent mesh changes from `MESH_DATA` to `auto`;
- use the non-stock [Z-offset workflow](CALIBRATION.md#z-offset-calibration);
- use Forge-X camera control instead of the Stock screen camera service.

For Guppy or Headless, configure Wi-Fi or Ethernet before disabling the Stock screen. Feather can configure normal DHCP Ethernet and WPA/WPA2-PSK Wi-Fi itself. Static addressing and enterprise Wi-Fi require advanced configuration outside Feather.

If the selected mode leaves the printer inaccessible:

1. Boot through the [Dual Boot recovery route](DUAL_BOOT.md).
2. Restore `STOCK` mode with the console or configuration script.

From SSH or a recovery shell, the mode can also be changed directly:

```sh
# Enable Stock
/opt/config/mod/.shell/commands/zdisplay.sh stock

# Enable Feather
/opt/config/mod/.shell/commands/zdisplay.sh feather

# Enable Guppy
/opt/config/mod/.shell/commands/zdisplay.sh guppy

# Enable Headless
/opt/config/mod/.shell/commands/zdisplay.sh headless
```

As a last resort, update the stored parameter:

```sh
/opt/config/mod/.shell/commands/zconf.sh /opt/config/mod_data/variables.cfg --set "display='STOCK'"
```

### Extending Screen Functionality

Feather uses the `typer` renderer at `/root/printer_data/bin/typer`. The renderer supports text, shapes, buffered batches, and touch regions; see the [Typer documentation](TYPER.md).

Do not start a second Typer process while Feather is active. Concurrent framebuffer or pipe access can corrupt the UI. For a custom full-screen implementation, switch to `HEADLESS` and test it while the printer is idle.

Useful implementation references in the full repository include:

- `/.py/klipper/plugins/feather_screen.py` — Feather controller and UI integration;
- `/config/feather.cfg` — related macros and configuration;
- `/.shell/screen.sh` — display startup integration.
