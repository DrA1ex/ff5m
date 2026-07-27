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

During a print, Feather shows progress, elapsed and remaining time, layer and height information. It provides pause, resume, filament change, live Z adjustment, and guarded cancellation. Cancellation is also available while the printer is preparing a job, including homing, leveling, heating, and priming.

#### Movement, heating, and lighting

Feather provides both step movement and joystick movement. The printer must be in an appropriate idle state, and movement is kept within the configured limits. Separate controls are available for homing, nozzle and bed heating, material preheat, cooldown, the part-cooling fan, and chamber-light brightness. Hardware-active pages provide emergency-stop access.

#### Filament and calibration

Material presets are shared with the Forge-X filament macros. Feather remembers the selected material and provides guided loading, unloading, purging, and filament-change actions, including during a paused print.

The Calibration page includes guided workflows for bed screws, bed mesh, Safe Z, Z offset, extruder feed, PID, and Input Shaper. Follow the instructions on the screen and review the result before saving it.

#### Network, settings, and themes

Feather can scan for Wi-Fi networks, enter normal WPA/WPA2-PSK credentials with the on-screen keyboard, configure DHCP Ethernet, and show the active connection, signal, and IP address. Static addressing and enterprise Wi-Fi still require advanced configuration outside Feather.

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

### Custom Loading and Splash Screens

Choose a bundled Feather theme from Feather Settings. Custom theme JSON files can be placed in:

```text
/opt/config/mod_data/themes
```

Use a bundled theme file as the schema reference.

For a custom boot image:

1. Create an 800 × 480 PNG.

#### Example of Conversion (ImageMagick)

2. Convert it to compressed BGRA, for example with ImageMagick:

   ```sh
   convert -size 800x480 xc:none splash.png -geometry +0+0 -composite -depth 8 bgra:- | xz -c > splash.img.xz
   ```

#### Installation

3. Upload the file to `mod_data` in Fluidd:
   - `splash.img.xz` — splash image;
   - `load.img.xz` — loading image.

Keep the lower area of the loading image clear so boot messages remain readable.
