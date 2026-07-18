# Screen Configuration

The stock screen implementation on the Flashforge AD5M (Pro) is not optimized for direct interaction with Fluidd or Moonraker. 
This is because FlashForge's firmware is designed to work exclusively with its own services and does not handle external control well.

For example, you can't just do `RESTART` or `SAVE_CONFIG` via Klipper's console — it freezes the screen, and you have to reboot the printer afterward because you can't do anything with a frozen firmware application.

Also, the screen consumes a lot of RAM — about 7-15 MiB—and with the printer's limited memory of just 128 MiB, it's a dealbreaker.

However, we don't have to use the stock screen. To free up resources, we can run the printer headless (in early mod builds) or use the alternative Feather screen implementation.

## Alternative Screens

If you want to reduce memory usage and don't need the full functionality of the stock screen, you can switch to one of the alternative screens included in the mod. These options use much less memory and focus on displaying the most important information, which is helpful for larger prints or when using extra modifications.

Here's what each option does:

### Feather Screen

Feather is Forge-X's lightweight interactive screen. It displays print status,
temperatures, and estimated time while also supporting:

- browsing uploaded G-code files and starting a print;
- pausing, resuming, and cancelling a print;
- progress, elapsed/estimated time, layer metadata and macro status;
- guided PLA/PETG/ABS/ABS-PC filament loading during idle or pause;
- homing and safe XYZ movement while idle, using either fixed jog steps or
  acceleration-limited XY/Z joystick controls;
- preheat presets plus heater and part-fan control while idle;
- first-layer Z adjustment, bed-screw guidance and an `auto` bed-mesh workflow;
- brightness, ECO brightness and sound settings;
- browsing and editing Forge-X mod parameters with switches, option selectors,
  and numeric or text input;
- Wi-Fi scanning/password entry and Ethernet DHCP selection;
- local Restore/Cleanup/Later handling for Power Loss Recovery.

Feather deliberately remains smaller than a full desktop-style UI. File deletion,
static IP configuration, enterprise/hidden Wi-Fi, PID/input-shaper tuning and
unrestricted G-code remain
available through Fluidd/Mainsail or the documented console workflows.

Every page has a compact footer with actual/target nozzle and bed temperatures,
network/IP and current printer state. After 60 seconds without touch the panel
uses the persisted `backlight_eco` value. The first touch after dimming only wakes
the panel; it never activates the button underneath.

Filament extrusion buttons remain disabled until Klipper reports
`min_extrude_temp`. Live Z adjustment is intentionally unavailable without layer
metadata and closes after layer 1. Bed mesh always replaces profile `auto`; once
the existing leveling macro starts, Feather does not offer an unsafe partial
cancel.

The dashboard remembers the latest material selected by Feather, `LOAD_MATERIAL`,
`PREHEAT_MATERIAL`, or `LOAD_FILAMENT MATERIAL=...`. Until a material is selected
it displays `n/a`. The value is stored with the other Forge-X parameters and
survives Klipper and printer restarts.

Touch, dim/wake, actions, rejected input, backlight changes, and long Feather
operations are written to Klipper's normal `printer.log` with the
`[feather_screen]` prefix. The same log is available from Fluidd for diagnostics.

### Guppy Screen

Guppy is also resource-friendly, but in addition to showing print information, it allows you to control the printer directly from the screen. You can pause, resume, or cancel prints using Guppy, making it a good choice if you want some interactive features while still saving memory.

### Switching to Alternative Screens / Headless

**Disabling the stock screen completely disables FlashForge's additional software.**  

However, the printer still operates via Klipper, so the actual printing process remains largely unchanged, though some workflows may behave differently.

Since FlashForge's software won’t start, you can no longer upload G-code or control the printer through FlashPrint or FlashForge Orca, as the required services will not be running.

Instead, you must use the **Moonraker protocol** for uploading files and managing print jobs. Learn more in the [Slicing](/docs/SLICING.md) section.

The printer will also **no longer load** the `MESH_DATA` bed profile and will instead use the `auto` profile. **Be sure** to save your bed mesh using the name `auto`.

FlashForge’s software also handled **Z-Offset** — a feature not native to Klipper. After switching, you’ll need to manage Z-Offset manually. See the [Printing](/docs/PRINTING.md#z-offset) section for details.

The stock screen also controls the camera, so you’ll need to use Forge-X’s camera controls instead. Learn more in the [Camera](/docs/CAMERA.md) section.


To enable the Feather screen and free up system resources, set the following mod parameter:

```bash
SET_MOD PARAM="display" VALUE="FEATHER"
```

To enable the Guppy screen, set the following mod parameter:

```bash
SET_MOD PARAM="display" VALUE="GUPPY"
```

This will disable the stock screen and activate the selected alternative screen immediately. **Make sure to wait until the current print finishes before doing this! :)**

To switch back to the stock screen, run this command:

```bash
SET_MOD PARAM="display" VALUE="STOCK"
```

If you want to free up more resources (usually you don't need this) or run a custom screen implementation yourself, run this command:

```bash
SET_MOD PARAM="display" VALUE="HEADLESS"
```

> [!NOTE]
> Feather can boot and control the printer without a network connection. Use its
> **Network** page to scan for a WPA/WPA2-PSK Wi-Fi network or select Ethernet.
> Existing stock Wi-Fi configuration is still reused until Feather saves a new
> network. The active Wi-Fi configuration remains `/etc/wpa_supplicant.conf`.

> [!WARNING]
> Only DHCP mode is supported!

**If you lose access** to the printer after disabling the screen, flash this image:  
- [Adventurer5M-ForgeX-feather-off.tgz](https://github.com/DrA1ex/ff5m/releases/download/1.2.0/Adventurer5M-ForgeX-feather-off.tgz)   

Rename it to match your printer version.

**Alternatively**, you can temporarily prevent the mod from booting using the [Dual Boot](/docs/DUAL_BOOT.md) option.   
Then edit the `variables.cfg` file to disable the `display` parameter manually:

```bash
# Enable stock screen using script
/opt/config/mod/.shell/commands/zdisplay.sh stock

# Enable feather screen using script
/opt/config/mod/.shell/commands/zdisplay.sh feather

# Enable guppy screen using script
/opt/config/mod/.shell/commands/zdisplay.sh guppy

# Enable headless mode using script
/opt/config/mod/.shell/commands/zdisplay.sh headless

# Or change parameter in variables.cfg using this script
/opt/config/mod/.shell/commands/zconf.sh /opt/config/mod_data/variables.cfg --set "display='STOCK'"

# Or edit manually
nano /opt/config/mod_data/variables.cfg
```


### Extending Screen Functionality

The Feather screen is not a monolithic application but rather a flexible system that can be extended to display additional information.
To customize or extend the screen's functionality, you can use the typer tool, located at `/root/printer_data/bin/typer`.
This tool allows you to draw or print information on the screen.

To see usage instructions, run:
```bash
/root/printer_data/bin/typer --help
```

Feather adds interaction without turning `typer` into a printer-control service:
`typer` only renders and reports named touch hitboxes. The Klipper plugin owns UI
state, validates printer state, and executes reviewed macros.

Documentation for `typer` is available here: [link](/docs/TYPER.md)   

For examples you can view [feather.cfg](/config/feather.cfg) for macros and [screen.sh](/.shell/screen.sh) script.
Implementation of Feather itself you can find in [feather_screen.py](/.py/klipper/plugins/feather_screen.py)
with page groups in
[feather_screen_pages.py](/.py/klipper/plugins/feather_screen_pages.py),
[feather_screen_controls.py](/.py/klipper/plugins/feather_screen_controls.py).
Renderer/layout helpers remain in
[feather_ui.py](/.py/klipper/plugins/feather_ui.py).
The lightweight mod-parameter editor helpers live in
[feather_mod_settings.py](/.py/klipper/plugins/feather_mod_settings.py).
Joystick normalization, inertia and boundary braking are kept in
[feather_joystick.py](/.py/klipper/plugins/feather_joystick.py); motion remains
inside the Klipper process and does not add an idle service.

### Custom Loading and Splash Screens

Set any image as your splash/loading screen.

The checked-in Feather screens are generated reproducibly from
`.bin/src/splash/generate.py`. Pillow is needed only on the development machine;
it is not installed on the printer:

```sh
python3 -m pip install Pillow
python3 .bin/src/splash/generate.py \
  --font /path/to/monospace.ttf \
  --bold-font /path/to/monospace-bold.ttf \
  --preview-dir docs/images
```

This updates `splash.img.xz` and `load.img.xz` in the repository root and
optionally writes the `forge-x-splash.png`, `forge-x-loading.png`, and separate
`feather-splash.png` previews. The first two are system-wide Forge-X screens;
Feather branding is intentionally kept separate. The generator validates the
required `800×480×4` BGRA framebuffer size before XZ compression. The lower
130 pixels of the loading image stay empty because `screen.sh` uses them for
five boot-log rows and uptime.

For a custom externally-created image:

- Create PNG image (800×480)
- Convert to raw bgra with xz compression:

#### Example of Conversion (ImageMagick)

```sh
convert -size 800x480 xc:none ./splash.png -geometry +0+0 -composite -depth 8 bgra:- | xz -c > "splash.img.xz"
```

#### Installation

Place in `Fluidd Config → mod_data`:   
- Loading screen: `load.img.xz`   
- Splash screen: `splash.img.xz`   
