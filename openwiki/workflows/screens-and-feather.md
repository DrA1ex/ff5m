# Screen modes and first-party Feather

# Screen modes and first-party Feather

Forge-X supports four mutually exclusive display modes: **STOCK**, **FEATHER**, **GUPPY**, and **HEADLESS**. The `display` setting changes more than what appears on the 800×480 panel: it rewrites the active Klipper configuration include, chooses who starts networking and Klipper, and changes the supported control/upload workflow.

**Feather is Forge-X’s built-in display implementation.** It is developed in this repository as a small Klipper extension plus the bundled `typer` renderer—not an externally managed touchscreen application. Its purpose is dependable, very-low-resource local monitoring while Moonraker, Fluidd, and Mainsail remain the interactive control surfaces.

> **Mode change boundary:** do not change display mode during a print. Non-stock modes disable the FlashForge application and its companion services. Configure Wi-Fi or Ethernet while stock mode is available and retain a documented recovery route before switching.

## Mode comparison

| Mode | Active root config | Local panel behavior | Main interactive control path | Runtime implications |
|---|---|---|---|---|
| `STOCK` (default) | [`config/stock.cfg`](../../config/stock.cfg) | Vendor FlashForge application and touch UI | Stock UI/apps, plus Forge-X/Moonraker where compatible | Vendor app starts Klipper and owns stock-screen-specific behavior. It consumes more RAM and can freeze when unsupported direct Klipper actions such as `RESTART` or `SAVE_CONFIG` are used. |
| `FEATHER` | [`config/feather.cfg`](../../config/feather.cfg) | Forge-X read-only status display | Fluidd/Mainsail/Moonraker for upload and control | First-party, low-resource display. Forge-X starts networking, MCU, and Klipper; vendor app is stopped. |
| `GUPPY` | [`config/guppy.cfg`](../../config/guppy.cfg) | Interactive Guppy touchscreen | Guppy plus Fluidd/Mainsail/Moonraker | Separate Guppy/tslib processes are started in the chroot. |
| `HEADLESS` | [`config/headless.cfg`](../../config/headless.cfg) | No normal local display UI | Fluidd/Mainsail/Moonraker | Lowest UI footprint; intended for remote operation or a custom display implementation. |

All alternative configs include shared `base.cfg`, `headless.cfg`, and `client.cfg` as appropriate. Thus they retain Forge-X macros and most print behavior, but they deliberately do not retain the stock application’s upload, camera, Z-offset, and mesh-management assumptions.

## Selecting and applying a mode

The supported operator interface is the persisted mod parameter:

```gcode
SET_MOD PARAM=display VALUE=FEATHER
SET_MOD PARAM=display VALUE=GUPPY
SET_MOD PARAM=display VALUE=HEADLESS
SET_MOD PARAM=display VALUE=STOCK
```

`mod_params` persists the selection, then its `parameter_changed` shell hook invokes [`.shell/commands/zdisplay.sh`](../../.shell/commands/zdisplay.sh). That script applies one of [`.cfg/init.display.*.cfg`](../../.cfg/) configuration-delta files to `/opt/config/printer.cfg`: it includes the selected `config/*.cfg` root and removes competing mode includes. This is why changing `display` is a configuration migration, not merely a process toggle.

For non-stock modes, `zdisplay.sh` stops `firmwareExe`/`ffstartup-arm`, stops Guppy if present, retains or re-establishes network state, shows the splash/backlight sequence, reloads init, and hard-restarts Klipper. Guppy then starts its own chroot services. Returning to stock restores `stock.cfg` and schedules a reboot. The direct script commands—`stock`, `feather`, `guppy`, and `headless`—are recovery/maintenance mechanisms; prefer `SET_MOD` in normal operation.

## Boot and network consequences

At boot, [`.shell/boot/boot.sh`](../../.shell/boot/boot.sh) checks whether the selected mode is non-stock.

- **Stock:** it leaves the vendor boot/application path active.
- **Feather, Guppy, Headless:** it brings up Wi-Fi or Ethernet from previously saved stock configuration, using DHCP; on success it marks custom boot, initializes required kernel interfaces, boots the MCU with `boot_mcu`, and starts Klipper directly.
- **Failure to obtain network:** the boot script changes the config back to stock mode and continues into stock firmware. This fallback exists because Feather/headless operation is not useful without a remote control path.

Before switching away from stock, provision Wi-Fi or Ethernet in the vendor UI. The documented Wi-Fi source is `/etc/wpa_supplicant.conf`; the implementation supports DHCP only. In non-stock modes, upload and control through FlashPrint/FlashForge Orca are no longer available because their required vendor services are not running. Use the Moonraker workflow described in [`docs/SLICING.md`](../../docs/SLICING.md).

The operator-facing configuration implications also change: use an `auto` bed mesh rather than stock `MESH_DATA`, manage Z-offset through Forge-X/Klipper workflows, and use Forge-X camera controls rather than the stock screen’s camera path. See [`docs/SCREEN.md`](../../docs/SCREEN.md), [`docs/PRINTING.md`](../../docs/PRINTING.md), and [`docs/CAMERA.md`](../../docs/CAMERA.md).

## Feather: built-in Forge-X monitoring display

### Design goal and boundaries

Feather trades interaction for a small runtime footprint. It displays printer state but provides **no touch input or on-panel print controls**. Use Fluidd/Mainsail, Moonraker APIs, or configured remote tooling to upload, start, pause, resume, and cancel jobs. This constrained scope is intentional: it avoids carrying a full vendor UI or a separate interactive screen application while keeping essential local feedback.

The feature consists of three repository-owned pieces:

```text
config/feather.cfg
  └─ [feather_screen] enables the Forge-X Klipper extension
       └─ feather_screen.py reads Klipper status and writes draw batches
            └─ FIFO /tmp/typer → bundled typer renderer → physical framebuffer
```

1. [`config/feather.cfg`](../../config/feather.cfg) includes common/headless/client macro layers and declares `[feather_screen]`.
2. [`feather_screen.py`](../../.py/klipper/plugins/feather_screen.py) is installed as a Forge-X-added Klipper `extras` plugin. On Klippy ready it starts `typer` with double buffering, manages a FIFO at `/tmp/typer`, and refreshes its status state about once per second.
3. The bundled `typer` executable is exposed as `/root/printer_data/bin/typer`. The plugin sends batched drawing commands rather than rendering a heavy GUI itself.

The plugin reads Klipper state for extruder/bed temperatures, homed axes, idle and pause state, virtual-SD file, print stats, and display-status progress. It also observes Forge-X runtime markers for Wi-Fi, Ethernet, camera, and discovered IP address. It presents a toolbar, file caption, progress bar, print status, estimated/elapsed time, and a bounded error/disconnect panel.

### How print status reaches Feather

`config/feather.cfg` overrides the internal `_PRINT_STATUS` macro. It emits the normal response message and, while the Forge-X print-preparation state is active, calls:

```gcode
FEATHER_PRINT_STATUS S="PREPARING..."
```

`FEATHER_PRINT_STATUS` is the plugin’s only registered command. It is an internal UI bridge used by Forge-X print macros—not a slicer contract. Continue using normal `START_PRINT`, `PAUSE`, `RESUME`, and `CANCEL_PRINT` flows; do not build external workflows around manual status injection. The extension reference in [Forge-X Klipper extensions](klipper-extensions.md) documents the plugin command boundary.

### Extending Feather safely

For a deliberately small custom indicator or status overlay, use the shipped `typer` tool and its documented batch interface; [`docs/TYPER.md`](../../docs/TYPER.md) is the operator/developer reference. The established Feather plugin is the canonical example of safe FIFO ownership, double-buffered drawing, and state refresh.

Do **not** start a second process that writes arbitrary concurrent data to `/tmp/typer` without designing an ownership/serialization scheme. Do not turn Feather into a second control plane: commands affecting printer motion or service state should remain in reviewed Klipper macros or the Moonraker workflow.

## Guppy versus Feather

Guppy is an optional interactive screen integration, not the implementation base for Feather. On `GUPPY`, the chroot runtime starts tslib and [`.root/S80guppyscreen`](../../.root/S80guppyscreen), which launches `/root/guppyscreen/guppyscreen` at reduced CPU priority and caps allocator arenas. Feather does not use these services: its renderer is launched from the Klipper plugin and has no touchscreen input path.

Choose Guppy when local touch control is required and validate its independent service lifecycle. Choose Feather when the priority is a built-in Forge-X monitoring display and low resource usage; plan remote control before enabling it.

## Diagnosis and recovery

1. **Confirm selected mode:** run `GET_MOD PARAM=display`; check the active include in `/opt/config/printer.cfg` only when diagnosing configuration routing.
2. **Confirm the remote path first:** for non-stock modes, verify a DHCP address and reach Moonraker/Fluidd/Mainsail before treating the panel as the primary diagnostic surface.
3. **Feather-specific checks:** inspect Klipper logs for plugin/renderer errors, verify `/tmp/typer` ownership, and avoid manually killing `typer` while Klippy owns the display.
4. **Recover safely:** boot through the documented dual-boot/skip path or use the documented display-off image if access is lost. From a recovered/stock-capable context, restore `STOCK` through `SET_MOD` or `zdisplay.sh stock`; use manual `variables.cfg` editing only as the documented last-resort recovery procedure.
5. **Validate after changes:** exercise stock mode plus each affected alternative mode; verify DHCP fallback, MCU/Klipper startup, browser control, status/error display, and a safe print lifecycle. Do not test mode changes during production prints.

## Change guidance

- Preserve the one-root-config invariant: every new mode must have a matching `config` root and `.cfg/init.display.*.cfg` selection delta that removes incompatible roots.
- Treat `zdisplay.sh`, `boot.sh`, active Klipper config, and documentation as one change surface. A mode is broken if any one of selection, startup, remote control, or recovery is missing.
- Feather code runs in Klippy’s process and accesses shared screen/runtime resources. Keep refresh work bounded, avoid blocking calls, and validate on the constrained target hardware.
- Keep first-party Feather distinct from optional external screen integrations in docs and code. Feather’s no-input limitation is an explicit product contract.

## Source entry points

- Operator behavior and recovery: [`docs/SCREEN.md`](../../docs/SCREEN.md)
- Selection/config deltas: [`.shell/commands/zdisplay.sh`](../../.shell/commands/zdisplay.sh), [`config/`](../../config/), and [`.cfg/init.display.*.cfg`](../../.cfg/)
- Non-stock boot/fallback: [`.shell/boot/boot.sh`](../../.shell/boot/boot.sh)
- Feather implementation: [`config/feather.cfg`](../../config/feather.cfg), [`.py/klipper/plugins/feather_screen.py`](../../.py/klipper/plugins/feather_screen.py), and [`docs/TYPER.md`](../../docs/TYPER.md)
- Guppy lifecycle: [`.root/guppyscreen`](../../.root/guppyscreen), [`.root/S80guppyscreen`](../../.root/S80guppyscreen)
