# Forge-X Klipper extensions

# Forge-X Klipper extensions

Forge-X deploys six **added Klipper `extras` modules** from [`.py/klipper/plugins/`](../../.py/klipper/plugins/) into stock Klipper’s `extras/` directory at boot. They are extensions—not the replacement stock-module patches described in [Built-in Klipper patching](klipper-patching.md). A plugin runs only when its matching configuration section is included by the active Klipper configuration.

The normal shared configuration, [`macros/base.cfg`](../../macros/base.cfg), loads `load_cell_tare`, `md5_check`, `tone_player`, and `mod_params`. The alternative-display configuration [`macros/headless.cfg`](../../macros/headless.cfg) loads `resurrection`; Feather additionally loads `feather_screen` through [`config/feather.cfg`](../../config/feather.cfg).

> **Usage boundary:** use the documented top-level macros and settings for normal operation. Some plugin commands are internal plumbing called by Forge-X macros; invoking them directly can bypass preparation or safety checks. Settings are persisted in printer-side `mod_data`, so do not edit that state as a substitute for its G-code interface.

## At a glance

| Extension | Loaded by | Main public interface | Purpose |
|---|---|---|---|
| `mod_params` | `base.cfg` | `LIST_MOD_PARAMS`, `GET_MOD`, `SET_MOD` | Validated persistent Forge-X settings and their change hooks. |
| `load_cell_tare` | `base.cfg` | `LOAD_CELL_TARE` | Safely zeroes the AD5M load cell before probing/print operations that depend on it. |
| `md5_check` | `base.cfg` | `CHECK_MD5` | Checks a slicer-written G-code checksum before a print and cancels corrupt jobs. |
| `tone_player` | `base.cfg` | `TONE`; macro wrappers `M300`, `BEEP`, `ALARM` | Plays PWM audio sequences through the printer’s audio hardware. |
| `resurrection` | `headless.cfg` | `RESURRECT`, `RESURRECT_ABORT` | Optional power-loss state capture and assisted print resume. |
| `feather_screen` | `feather.cfg` | `FEATHER_PRINT_STATUS` (internal) | Drives the minimal Feather display/status UI. |

## `mod_params`: persistent Forge-X settings

[`mod_params.py`](../../.py/klipper/plugins/mod_params.py) reads the declared parameter schema from [`mod_params.json`](../../mod_params.json), loads/saves values in `/opt/config/mod_data/variables.cfg`, applies defaults and deprecation migrations, and exposes values as `printer.mod_params.variables` to Jinja macros. It validates type and enum values before saving.

Use these commands from the Klipper console:

```gcode
LIST_MOD_PARAMS
GET_MOD PARAM=tune_klipper
SET_MOD PARAM=tune_klipper VALUE=1
```

`GET_MOD_PARAM`/`SET_MOD_PARAM` are equivalent longer command names; `RELOAD_MOD_PARAMS` reloads the persisted values. On a successful change, the configured `changes_gcode` invokes the shell `parameter_changed` hook. That hook owns immediate effects such as rebooting after `tune_klipper`, restarting services, selecting a display, or enabling peripherals. Therefore, edit settings through `SET_MOD`, not by hand-editing `variables.cfg`.

Use [`docs/CONFIGURATION.md`](../../docs/CONFIGURATION.md) for operator-facing parameter guidance. `LIST_MOD_PARAMS` is the authoritative live inventory because the schema evolves.

## `load_cell_tare`: prepare the load-cell probe

[`load_cell_tare.py`](../../.py/klipper/plugins/load_cell_tare.py) provides:

```gcode
LOAD_CELL_TARE
```

The extension checks the current `temperature_sensor weightValue` reading against `cell_weight`. When a tare is needed, it verifies/reset-confirmation state, rejects bed pressure that would make zeroing unsafe, toggles the printer’s level-control pins to request tare, waits for confirmation, and checks the resulting reading. If it cannot complete safely, it raises a Klipper error; when a Forge-X print is active, the error path cancels that print.

Forge-X macros already call it before workflows that depend on accurate load-cell readings, including screw leveling, bed-mesh validation, start/print preparation, and resurrection recovery. Normal users should use those higher-level workflows. Run `LOAD_CELL_TARE` manually only to diagnose or prepare the sensor when the bed/nozzle state is known safe: remove pressure from the bed and be prepared for Z homing or a Z move if the plugin detects contact.

The `cell_weight` mod parameter governs the threshold. This is physical-printer safety logic—not a generic sensor reset—so test any change with a clean bed and conservative motion conditions.

## `md5_check`: reject corrupt G-code

[`md5_check.py`](../../.py/klipper/plugins/md5_check.py) registers:

```gcode
CHECK_MD5
CHECK_MD5 FILENAME="/path/to/file.gcode"
CHECK_MD5 DELETE=False
```

Without `FILENAME`, it checks the current virtual-SD file. The expected checksum is the first line in the format `; MD5:<digest>`; the plugin hashes the rest of the file. A file with no MD5 header is allowed with a warning. On a mismatch, the default behavior deletes the G-code and same-basename `.bmp`, cancels the current print, and raises an error. `DELETE=False` is useful for diagnosis when preserving the invalid file is intentional.

The shared `START_PRINT` flow invokes `CHECK_MD5` before preparation when the `check_md5` parameter is enabled (default enabled) and preparation has not already completed. It is effective only for G-code whose checksum was added by the slicer post-processing helper. Configure `addMD5.sh` or `addMD5.bat` as documented in [`docs/SLICING.md`](../../docs/SLICING.md#md5-checksum-validation); see [`docs/CONFIGURATION.md`](../../docs/CONFIGURATION.md) for the setting.

## `tone_player`: PWM sound

[`tone_player.py`](../../.py/klipper/plugins/tone_player.py) registers `TONE`, which accepts a space-separated `NOTES` sequence. Each token is `frequency:duration-ms`; a token containing only a duration is a silent pause:

```gcode
TONE NOTES="1000:100 50 1500:200"
M300 S1000 P100
```

The extension drives the printer PWM audio output while yielding through Klipper’s reactor between notes. Forge-X’s `TONE` macro wraps the extension command and honors the persistent `sound` setting; macro wrappers also provide `M300`, `BEEP`, `ALARM`, and a fixed `M356` melody. Prefer the wrappers for user-visible notifications so the sound preference is respected. [`docs/MACROS.md`](../../docs/MACROS.md) lists their user-facing forms.

## `resurrection`: optional power-loss recovery

[`resurrection.py`](../../.py/klipper/plugins/resurrection.py) is configured only through `headless.cfg`, which is also included by Feather. It stores recovery state in `/opt/config/mod_data/resurrection.json`. It remains inactive unless:

```gcode
SET_MOD PARAM=power_loss_recovery VALUE=1
```

While an eligible print is running, it periodically saves the G-code path/position, machine position, temperature targets, mesh, and Z offset; `dump_time` in a user `[resurrection]` section controls the interval (default 3 seconds). On startup, a valid saved state makes a recovery prompt available in Fluidd/Mainsail and Guppy, and the extension exposes:

```gcode
RESURRECT
RESURRECT_ABORT
```

`RESURRECT` validates the file and saved state, loads the saved mesh and virtual-SD position, restores selected motion/fan/pressure-advance state found in the G-code, homes, tares the load cell, returns to position, and resumes. `RESURRECT_ABORT` performs cleanup instead. This procedure can move a hot printer and is explicitly emergency recovery rather than a substitute for stable power. Follow the warnings and monitoring procedure in [`docs/PRINTING.md`](../../docs/PRINTING.md#power-loss-recovery); a UPS remains the reliable solution.

## `feather_screen`: alternative display integration

[`feather_screen.py`](../../.py/klipper/plugins/feather_screen.py) is loaded only in the Feather configuration (`SET_MOD PARAM=display VALUE=FEATHER`). It starts the bundled `typer` renderer, sends drawing commands through `/tmp/typer`, and receives named touch actions through `/tmp/feather-events`. The plugin owns the UI state machine and validates printer state before starting files, invoking pause/resume/cancel macros, moving homed axes, controlling heaters/fan, or starting an asynchronous network operation.

Its only registered command is:

```gcode
FEATHER_PRINT_STATUS S="PREPARING..."
```

Forge-X’s `_PRINT_STATUS` macro calls this command for the Feather workflow. Treat it as an internal UI-status bridge, not a stable slicer macro. Interactive controls use the same normal `SDCARD_PRINT_FILE`, `PAUSE`, `RESUME`, `CANCEL_PRINT`, `G28`, and `MOVE_SAFE` paths exposed elsewhere by Forge-X. The held joystick is the narrow exception: it queues short native toolhead segments so release latency stays bounded, while reusing `MOVE_SAFE` boundaries and half of the configured acceleration. See [Screen modes and Feather](screens-and-feather.md) for the architecture and safety gates.

## Change and validation guidance

1. **Add configuration with the plugin.** A file under `plugins/` does nothing until an active config includes its `[module_name]` section; decide which display modes should load it.
2. **Keep public contracts explicit.** If a new command is meant for operators/slicers, document syntax, state dependencies, and failure behavior. Keep private status or lifecycle commands out of public examples.
3. **Preserve hardware safety.** Load-cell, recovery, and audio plugins reach shared device/MCU resources. Exercise their actual feature on supported hardware, not merely import/startup.
4. **Check mode and setting gates.** Validate stock, headless/Feather as applicable; test disabled settings as well as enabled behavior. `resurrection` in particular is absent from stock-only config paths.
5. **Use the deployment lifecycle.** Plugins are symlinked into stock `klippy/extras/` at boot and removed by uninstall. Follow [Built-in Klipper patching](klipper-patching.md) for compatibility and rollback requirements.

## Source entry points

- Plugin sources: [`.py/klipper/plugins/`](../../.py/klipper/plugins/)
- Shared extension declarations and callers: [`macros/base.cfg`](../../macros/base.cfg)
- Alternative-screen declarations: [`macros/headless.cfg`](../../macros/headless.cfg), [`config/feather.cfg`](../../config/feather.cfg)
- Parameter schema and shell reactions: [`mod_params.json`](../../mod_params.json), [`.shell/commands/zchanges.sh`](../../.shell/commands/zchanges.sh)
- Deployment/reversal: [Built-in Klipper patching](klipper-patching.md)
