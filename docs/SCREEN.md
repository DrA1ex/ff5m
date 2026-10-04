# Screens

Forge-X supports four display modes:

| Mode | Use it when |
| --- | --- |
| `FEATHER` (default) | You want Forge-X's own lightweight touchscreen controls. |
| `STOCK` | You want the original FlashForge screen, upload path, and vendor workflow. |
| `GUPPY` | You prefer the separate Guppy touchscreen interface. |
| `HEADLESS` | You control the printer remotely or provide your own display process. |

HelixScreen is also available for the AD5M and the Pro, but it is not an internal Forge-X display mode. The [official HelixScreen project](https://github.com/prestonbrown/helixscreen) publishes a ready-made AD5M image based on Forge-X and documents a manual [installation](https://github.com/prestonbrown/helixscreen/blob/main/docs/user/INSTALL.md) on an existing Forge-X setup.

> [!WARNING]
> Do not change display mode during a print. Before switching, make sure you have a working network connection or a recovery route through [Dual Boot](DUAL_BOOT.md).

## Which screen to use

| | Feather | Stock | Guppy | Headless |
| --- | --- | --- | --- | --- |
| RAM used | roughly 1–2 MB | roughly 10–20 MB | less than Stock | none |
| Needs the FlashForge services | no | yes (LAN-mode for Fluidd, Mainsail, and upload) | no | no |
| Local controls | main everyday workflows, guided calibration | the full vendor interface | its own interface | none |
| Power Loss Recovery | Forge-X | FlashForge's own | Forge-X | Forge-X |
| Bed-mesh profile | `auto` | `MESH_DATA` | `auto` | `auto` |

### Why Feather is the default

- **It uses little memory.** The printer has 128 MiB of RAM, and Moonraker alone takes roughly 30 MB. Feather needs roughly 1–2 MB, the Stock screen roughly 10–20 MB. The memory that Feather does not use stays available to Klipper, the camera, and your own additions. See [Reducing resource usage](PRINTING.md#reducing-resource-usage).
- **It does not depend on the vendor application.** The Stock screen is tightly coupled to the FlashForge services: direct Klipper commands such as `SAVE_CONFIG` or `RESTART` can freeze it, and its behavior and bugs change with the stock firmware version. Feather runs inside Klipper and is not affected by them.
- **It is part of Forge-X.** Feather is a Klipper extension that draws with the small [Typer](TYPER.md) renderer. It has no separate UI application or second copy of the printer state, and its calibration, material presets, Wi-Fi setup, and update notifications work directly with Forge-X.
- **It works without a network** once it is configured.

Feather covers the everyday workflows. Unrestricted G-code, file deletion, static or enterprise Wi-Fi, and detailed diagnostics need Fluidd or Mainsail. The list of what it can do is in the [Feather guide](FEATHER.md).

Choose Stock if you want the original FlashForge workflow, Guppy if you prefer its interface, and Headless if you control the printer remotely or run your own display process.

## Quick start

Switch the display mode from the console in Fluidd or Mainsail:

```gcode
SET_MOD PARAM=display VALUE=FEATHER
```

Use `FEATHER`, `GUPPY`, `HEADLESS`, or `STOCK`. Switching away from Stock stops the FlashForge companion services, so slicer upload through the vendor path stops working and you upload through Moonraker instead. If the printer becomes unreachable, boot through [Dual Boot](DUAL_BOOT.md) and switch back to `STOCK`.

**Details:**
[Stock](#stock-screen) ·
[Feather](#feather-screen) ·
[Guppy](#guppy-screen) ·
[Headless](#headless-mode) ·
[Switching modes and recovery](#switching-to-alternative-screens--headless) ·
[Custom screens](#extending-screen-functionality)

## Alternative Screens

### Stock screen

The Stock screen keeps the original FlashForge services and workflows. If you use it, stock firmware 3.1.x is the recommended version, the most stable one while printing; a newer version is only needed for the FlashForge cloud services (see [Compatibility](COMPATIBILITY.md#which-firmware-to-use-with-the-stock-screen)). Enable **Settings → Network → Network Mode → LAN-mode** if you use Fluidd, Mainsail, slicer upload, or Forge-X features that communicate with the vendor API.

When using the Stock screen:

- the persistent bed-mesh profile is `MESH_DATA`;
- Z offset is managed by the vendor workflow;
- use `NEW_SAVE_CONFIG`, not `SAVE_CONFIG` or `RESTART`.

### Feather Screen

Feather is the default screen and the first-party touchscreen of Forge-X, written for the 128 MiB system of the AD5M. It shows the current job and printer state, browses local and USB G-code, controls prints, moves and heats the printer, loads filament, runs the guided calibrations, and sets up Wi-Fi and Ethernet. See the [Feather guide](FEATHER.md) for details.

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

You can show your own dialogs on the Feather screen with Klipper `action:prompt_*` messages. See [Your own dialogs on the Feather screen](EXTENDING.md#your-own-dialogs-on-the-feather-screen).

Feather uses the `typer` renderer at `/root/printer_data/bin/typer`. The renderer supports text, shapes, buffered batches, and touch regions; see the [Typer documentation](TYPER.md).

Do not start a second Typer process while Feather is active. Concurrent framebuffer or pipe access can corrupt the UI. For a custom full-screen implementation, switch to `HEADLESS` and test it while the printer is idle.

Useful implementation references in the full repository include:

- `/.py/klipper/plugins/feather_screen.py` — Feather controller and UI integration;
- `/config/feather.cfg` — related macros and configuration;
- `/.shell/screen.sh` — display startup integration.
