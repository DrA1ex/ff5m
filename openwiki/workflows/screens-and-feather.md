# Screen modes and first-party Feather

Forge-X supports four mutually exclusive display modes: **STOCK**, **FEATHER**, **GUPPY**, and **HEADLESS**. The `display` setting changes more than what appears on the 800×480 panel: it rewrites the active Klipper configuration include, chooses who starts networking and Klipper, and changes the supported control/upload workflow.

**Feather is Forge-X’s built-in display implementation.** It is developed in this repository as a Klipper extension plus the bundled `typer` renderer—not an externally managed touchscreen application. It provides a bounded local control surface while keeping file management, state validation, and printer actions inside the existing Klipper stack. For process ownership, boot/restart sequencing, FIFO protocol, temporary runtime files, and the Typer/tslib input path, see [Feather runtime, Typer, and Klipper plugin wiring](feather-runtime.md).

> **Mode change boundary:** do not change display mode during a print. Non-stock modes disable the FlashForge application and its companion services. Feather can configure DHCP networking locally, but retain a documented recovery route before switching modes.

## Mode comparison

| Mode | Active root config | Local panel behavior | Main interactive control path | Runtime implications |
|---|---|---|---|---|
| `STOCK` (default) | [`config/stock.cfg`](../../config/stock.cfg) | Vendor FlashForge application and touch UI | Stock UI/apps, plus Forge-X/Moonraker where compatible | Vendor app starts Klipper and owns stock-screen-specific behavior. It consumes more RAM and can freeze when unsupported direct Klipper actions such as `RESTART` or `SAVE_CONFIG` are used. |
| `FEATHER` | [`config/feather.cfg`](../../config/feather.cfg) | Forge-X interactive status/control display | Feather for essential local actions; Fluidd/Mainsail for advanced work | First-party, low-resource display. Forge-X starts touchscreen, MCU, and Klipper even while offline; vendor app is stopped. |
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
- **Feather, Guppy, Headless:** it starts saved Wi-Fi or Ethernet configuration in the background, initializes required kernel interfaces, boots the MCU with `boot_mcu`, and starts Klipper directly without waiting for DHCP.
- **Feather touch:** `S35tslib` starts before Klipper so `/dev/input/guppy` exists when the plugin launches `typer`. It is reused by Guppy but not started for Headless.
- **Network failure:** Feather remains available offline and exposes its Network page. It no longer changes the selected display back to stock merely because DHCP or Wi-Fi failed.

Saved stock network configuration remains compatible, but Feather can now scan for a WPA/WPA2-PSK network, enter its password, or select Ethernet DHCP locally. The helper stores the chosen transport under `mod_data`, updates Wi-Fi configuration atomically, and never places credentials in process arguments or logs. Static addressing, hidden SSIDs, WPA-Enterprise, and multi-profile management are outside this UI. In non-stock modes, upload through FlashPrint/FlashForge Orca remains unavailable; use the Moonraker workflow in [`docs/SLICING.md`](../../docs/SLICING.md).

The operator-facing configuration implications also change: use an `auto` bed mesh rather than stock `MESH_DATA`, manage Z-offset through Forge-X/Klipper workflows, and use Forge-X camera controls rather than the stock screen’s camera path. See [`docs/SCREEN.md`](../../docs/SCREEN.md), [`docs/PRINTING.md`](../../docs/PRINTING.md), and [`docs/CAMERA.md`](../../docs/CAMERA.md).

## Feather: built-in Forge-X interactive display

### Design goal and boundaries

Feather deliberately limits interaction to operations that can reuse reviewed Forge-X paths: browsing/starting virtual-SD files, pause/resume/cancel, guided filament handling, idle homing and bounded movement, heater/fan control, safe Z/screws/mesh workflows, local power-loss recovery, and DHCP network setup. File deletion, PID/input-shaper tuning, and unrestricted G-code remain in Fluidd/Mainsail or documented console workflows.

The feature consists of three repository-owned pieces:

```text
config/feather.cfg
  └─ [feather_screen] enables the Forge-X Klipper extension
       ├─ feather_screen.py owns pages, validation, and actions
       ├─ feather_ui.py owns drawing and fixed 800x480 layout primitives
       ├─ feather_joystick.py owns touch normalization and motion ramps
       │    ├─ draw FIFO /tmp/typer → typer → framebuffer
       │    └─ event FIFO /tmp/feather-events ← tap or held-pointer actions
       └─ /dev/input/guppy ← ts_uinput ← physical touchscreen
```

1. [`config/feather.cfg`](../../config/feather.cfg) includes common/headless/client macro layers and declares `[feather_screen]`.
2. [`feather_screen.py`](../../.py/klipper/plugins/feather_screen.py) is installed as a Forge-X-added Klipper `extras` plugin. On Klippy ready it starts `typer`, registers the nonblocking event FIFO with the Klipper reactor, and redraws complete pages only on navigation/state changes.
3. The bundled `typer` executable is exposed as `/root/printer_data/bin/typer`. Its interactive module polls the draw FIFO and calibrated Linux input fd in one thread, maintains only the current page’s hitboxes, and emits opaque action IDs rather than executing printer commands.
4. [`.shell/commands/znetwork.sh`](../../.shell/commands/znetwork.sh) owns bounded asynchronous Wi-Fi/Ethernet operations. The plugin polls the child process from its existing one-second timer instead of blocking Klipper’s reactor. Scan, Wi-Fi connect and Ethernet DHCP are terminated after 15, 45 and 30 seconds respectively.

The plugin reads Klipper state for extruder/bed temperatures, homed axes, idle and pause state, virtual-SD file, print stats, layer metadata, filament sensor, resurrection state, fan and display-status progress. It presents a persistent footer, file caption, progress bar, macro status, estimated/elapsed time, guided workflows and a bounded error/disconnect panel. Full pages redraw on navigation/state changes; footer, temperatures and progress update from the existing one-second timer. The Move page can also register continuous hitboxes: `typer` supplies coordinates and heartbeats while Feather queues short native toolhead segments with the configured velocity, half acceleration, a bounded queue horizon, and `MOVE_SAFE`-equivalent limits.

Feather stores the selected material in `mod_params` as `current_material`. Selection through Feather, the interactive `LOAD_MATERIAL` prompt, `PREHEAT_MATERIAL`, or `LOAD_FILAMENT MATERIAL=...` updates the same value, so Fluidd actions and the local dashboard remain consistent after restarts. Touch, dim/wake, action and long-operation diagnostics use the `[feather_screen]` prefix in the normal Klipper log.

### How print status reaches Feather

`config/feather.cfg` overrides the internal `_PRINT_STATUS` macro. It emits the normal response message and, while the Forge-X print-preparation state is active, calls:

```gcode
FEATHER_PRINT_STATUS S="PREPARING..."
```

`FEATHER_PRINT_STATUS` is the plugin’s only registered command. It is an internal UI bridge used by Forge-X print macros—not a slicer contract. Continue using normal `START_PRINT`, `PAUSE`, `RESUME`, and `CANCEL_PRINT` flows; do not build external workflows around manual status injection. The extension reference in [Forge-X Klipper extensions](klipper-extensions.md) documents the plugin command boundary.

### Extending Feather safely

For a deliberately small custom indicator or status overlay, use the shipped `typer` tool and its documented batch interface; [`docs/TYPER.md`](../../docs/TYPER.md) is the operator/developer reference. The established Feather plugin is the canonical example of safe FIFO ownership, double-buffered drawing, and state refresh.

Do **not** start a second process that writes arbitrary concurrent data to `/tmp/typer`. New actions must be registered as hitboxes and revalidate current `print_stats`/homing state in Python. Normal actions dispatch through reviewed Klipper macros or a bounded system helper. Direct toolhead access is reserved for the joystick planner, which must retain its acceleration, queue-horizon, watchdog, homing, and boundary tests.

## Guppy versus Feather

Guppy is an optional interactive screen integration, not the implementation base for Feather. Both modes reuse the small `ts_uinput` service and stable `/dev/input/guppy` link. Guppy additionally launches its separate multi-threaded application; Feather reads the normalized input directly from `typer` and keeps semantics in the existing Klipper process.

Choose Guppy for its broader independent UI. Choose Feather when the priority is essential local control with the smallest additional runtime and continued operation without a network.

## Diagnosis and recovery

1. **Confirm selected mode:** run `GET_MOD PARAM=display`; check the active include in `/opt/config/printer.cfg` only when diagnosing configuration routing.
2. **Check the panel and network independently:** Feather can be healthy while offline. Inspect its Network page and `/tmp/net_ip` before diagnosing Moonraker/web access.
3. **Feather-specific checks:** inspect Klipper logs, verify `/dev/input/guppy`, `/tmp/typer`, and `/tmp/feather-events`, and avoid manually killing `typer` while Klippy owns the display.
4. **Recover safely:** boot through the documented dual-boot/skip path or use the documented display-off image if access is lost. From a recovered/stock-capable context, restore `STOCK` through `SET_MOD` or `zdisplay.sh stock`; use manual `variables.cfg` editing only as the documented last-resort recovery procedure.
5. **Validate after changes:** exercise stock mode plus each affected alternative mode; verify offline startup, DHCP recovery, MCU/Klipper startup, browser control, status/error display, and a safe print lifecycle. Do not test mode changes during production prints.

## Change guidance

- Preserve the one-root-config invariant: every new mode must have a matching `config` root and `.cfg/init.display.*.cfg` selection delta that removes incompatible roots.
- Treat `zdisplay.sh`, `boot.sh`, active Klipper config, and documentation as one change surface. A mode is broken if any one of selection, startup, remote control, or recovery is missing.
- Feather code runs in Klippy’s process and accesses shared screen/runtime resources. Keep refresh work bounded, run network work asynchronously, avoid full redraws in the one-second status timer, and validate RSS/swap on the constrained target hardware.
- Keep first-party Feather distinct from optional external screen integrations. Its reviewed action set and idle/printing safety gates are the product boundary.

## Source entry points

- Operator behavior and recovery: [`docs/SCREEN.md`](../../docs/SCREEN.md)
- Selection/config deltas: [`.shell/commands/zdisplay.sh`](../../.shell/commands/zdisplay.sh), [`config/`](../../config/), and [`.cfg/init.display.*.cfg`](../../.cfg/)
- Non-stock boot/fallback: [`.shell/boot/boot.sh`](../../.shell/boot/boot.sh)
- Feather implementation: [`config/feather.cfg`](../../config/feather.cfg), [`.py/klipper/plugins/feather_screen.py`](../../.py/klipper/plugins/feather_screen.py), and [`docs/TYPER.md`](../../docs/TYPER.md)
- Guppy lifecycle: [`.root/guppyscreen`](../../.root/guppyscreen), [`.root/S80guppyscreen`](../../.root/S80guppyscreen)
