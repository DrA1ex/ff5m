# Printing

Forge-X uses the normal print macros: `START_PRINT` starts a job, `END_PRINT` finishes it, and `PAUSE`, `RESUME`, and `CANCEL_PRINT` control an active print. If a temperature wait created by Forge-X is active, `M108` stops that wait.

For the required slicer start/end G-code and upload configuration, see [Slicing](SLICING.md).

> [!WARNING]
> After installing or updating Forge-X, changing the nozzle or build plate, or enabling motion/config tuning, inspect the bed mesh and Z offset before the next real print. Incorrect values can damage the nozzle or bed.

## Using stock Firmware with mod

- With the Stock screen, enable **Settings → Network → Network Mode → LAN-mode**. Some Web UI, slicer, and API interactions are unreliable without it.
- With Feather, Guppy, or Headless, upload and control jobs through Fluidd/Mainsail or the supported local UI.
- Confirm the persistent bed-mesh profile:
  - Stock screen: `MESH_DATA`
  - Feather, Guppy, or Headless: `auto`
- Enable MD5 validation in the slicer if you use the Forge-X G-code integrity check.
- Verify the first layer with a small test before starting a long print.

## Calibration

Use the Stock screen's supported workflow, Feather's **Control → Calibration** pages, or the Forge-X macros exposed in Fluidd/Mainsail:

- `BED_LEVEL_SCREWS_TUNE` — fully prepares the printer and calculates screw adjustments.
- `BED_LEVEL_SCREWS_PROBE` — repeats only the probing stage after the initial preparation.
- `AUTO_FULL_BED_LEVEL` — creates a bed mesh.
- `PID_TUNE_BED` — bed PID calibration.
- `PID_TUNE_EXTRUDER` — hotend PID calibration.
- `ZSHAPER` — input-shaper calibration.

> [!CAUTION]
> Read [Before you start](CALIBRATION.md#before-you-start) before changing the mesh or Z geometry.

> [!WARNING]
> The Stock screen can freeze when given `SAVE_CONFIG` or `RESTART`. Use `NEW_SAVE_CONFIG` instead. See the [FAQ](FAQ.md#stock-screen-freezes-i-cant-print-anything) if it has already frozen.

Do not substitute generic Klipper probing macros. The AD5M requires Forge-X preparation such as load-cell tare and printer-specific movement handling. See the full [Calibration guide](CALIBRATION.md).

## Bed Mesh

The printer uses different profile names depending on the display workflow:

- Stock screen loads `MESH_DATA`.
- Feather, Guppy, and Headless load `auto`.
- KAMP and forced leveling use the temporary `default` profile.

A temporary `default` profile must not be saved. A persistent profile should be measured at a bed temperature close to the intended print temperature and recreated after changes to the plate, nozzle, screws, or relevant motion settings.

If the required profile is missing, the selected print workflow may create a mesh automatically. Do not rely on that fallback without checking the result.

## KAMP

> [!CAUTION]
> Releases before **1.4.1-11** contain a Smart Park / `MOVE_SAFE` bug. Do not enable or use KAMP, or call `MOVE_SAFE` while relative positioning (`G91`) is active, until the printer is updated to **1.4.1-11 or later**.

To configure KAMP (Klipper Adaptive Meshing and Purging):

1. Enable it:

   ```gcode
   SET_MOD PARAM=use_kamp VALUE=1
   ```

   Or enable it for one print through `START_PRINT`:

   ```gcode
   START_PRINT EXTRUDER_TEMP=[nozzle_temperature_initial_layer] BED_TEMP=[bed_temperature_initial_layer_single] FORCE_KAMP=1
   ```

2. Enable object labels/exclusion in the slicer:
   - OrcaSlicer: **Process Profile → Other → Exclude objects**
   - PrusaSlicer: **Print Settings → Output options → Label objects**

3. Add the area definition before `START_PRINT`:

   **OrcaSlicer:**

   ```gcode
   KAMP_DEFINE_AREA MIN={first_layer_print_min[0]},{first_layer_print_min[1]} MAX={first_layer_print_max[0]},{first_layer_print_max[1]}
   ```

   **PrusaSlicer:**

   ```gcode
   KAMP_DEFINE_AREA MIN={min_x},{min_y} MAX={max_x},{max_y}
   ```

4. Do not add another purge algorithm to the start G-code. KAMP uses `LINE_PURGE`, and another cleaning path may leave the adaptively meshed area.

To disable priming entirely:

```gcode
SET_MOD PARAM=disable_priming VALUE=1
```

## Bed Collision Protection

Forge-X can stop a print when the load cell detects unexpected force. The related [configuration parameters](CONFIGURATION.md) are:

- `weight_check` — enables collision detection;
- `weight_check_max` — maximum tolerated load in grams.

For reliable protection, the load cell must report sensible values both cold and hot. Some degraded sensors drift by several kilograms as the bed warms.

Read the related troubleshooting notes before enabling or tightening the threshold:

- [Bed pressure detected](FAQ.md#why-am-i-getting-a-bed-pressure-detected-error)
- [Sensor value exceeding the limit](FAQ.md#why-am-i-getting-shutdown-due-to-sensor-value-exceeding-the-limit)
- [Endstop response or Timer too close](FAQ.md#why-am-i-getting-mcu-shutdown-with-unable-to-obtain-endstop_state-response-or-timer-too-close-during-start_print)

> [!WARNING]
> Do not set `weight_check_max` too low. The model's weight, normal nozzle contact, or an over-extruded area can otherwise cause false stops.

## Power Loss Recovery (Resurrection)

Forge-X can periodically save print state and attempt to resume after an unexpected power loss, reboot, system crash, or MCU shutdown.

### Important Limitations and Considerations:

Treat recovery as a last-resort salvage feature, not as reliable protection:

- exact position restoration, especially Z, is not guaranteed;
- the part may have detached or shifted;
- the bed may have cooled enough to lose adhesion;
- the resumed layer may have visible artifacts or weak bonding;
- the nozzle may have cooled with solidified material inside it.

> [!WARNING]
> Stable power or a UPS is safer than recovery. Inspect the part, bed, nozzle, and printer position before accepting a recovery prompt, and watch the first resumed movements closely.

### How to Enable:

```gcode
SET_MOD PARAM=power_loss_recovery VALUE=1
```

Optional `user.cfg` configuration:

```ini
[resurrection]
dump_time: 3.0
```

### Configuration Parameters:

- `power_loss_recovery` — enables the feature; default `0`.
- `dump_time` — interval between state saves; default `3.0` seconds.

### Available G-code Commands:

- `RESURRECT` — manually starts recovery from saved state.
- `RESURRECT_ABORT` — cancels pending recovery and removes the saved state.

### How it works:

During printing, Forge-X stores the current file position and important runtime state. After a restart, it detects the saved state and offers to resume or discard it. The recovery procedure restores temperatures and relevant settings, approaches the saved position, and continues from the stored file location.

### Recovery Process:

1. Forge-X detects a valid saved state during startup.
2. Feather, Guppy, or Fluidd/Mainsail presents a recovery choice where supported.
3. The user verifies the physical print and accepts or rejects recovery.
4. Forge-X restores temperatures and runtime state.
5. The printer moves back to the saved area and resumes the file.

The Stock screen uses its own power-loss recovery system; Forge-X Resurrection is for Feather, Guppy, and Headless workflows.

### State Information Saved:

The saved state includes information such as:

- XYZ position and feed rates;
- hotend and bed temperatures;
- fans, pressure advance, and speed limits;
- active bed mesh and Z offset;
- G-code file position and progress.

## Bed Mesh Validation

Bed mesh validation checks the current geometry before printing and can cancel a job if the measured position differs from the expected mesh by too much.

Relevant [configuration parameters](CONFIGURATION.md):

- `bed_mesh_validation` — enables validation;
- `bed_mesh_validation_clear` — cleans the nozzle before validation;
- `bed_mesh_validation_tolerance` — maximum allowed difference in millimetres; default `0.2`.

This can catch scenarios such as the wrong plate, a missing plate, a stale mesh, or motion parameters changed after calibration.

> [!NOTE]
> A dirty nozzle can create a false validation failure. Keep the nozzle clean, and do not set the tolerance so low that normal measurement variation cancels valid prints.

### How it works:

Before the print begins, Forge-X probes the expected position and compares it with the active mesh. If the difference exceeds `bed_mesh_validation_tolerance`, the print is cancelled and the user is asked to inspect the setup or recreate the mesh.

## Z-Offset

The Stock screen manages and persists its own Z offset.

Feather provides two workflows:

- **Control → Calibration → Z Offset** calibrates and saves the normal nozzle height while idle.
- **Z Adjust** on the print screen applies a live correction during the current Klipper session.

The Feather screens show the saved value, current value, and unsaved difference. Live changes take effect immediately but are not persisted until **Save** is pressed. If automatic loading is disabled, Feather can offer to enable it while saving.

On the AD5M, decreasing Z offset moves the bed closer to the nozzle; increasing it moves the bed farther away.

Enable automatic loading for non-stock modes:

```gcode
SET_MOD PARAM=load_zoffset VALUE=1
```

Then adjust through Fluidd/Mainsail controls or `SET_GCODE_OFFSET`. Forge-X stores the chosen value and loads it before a print after Klipper restarts.

To apply the saved offset during nozzle cleaning, enable `load_zoffset_cleaning`. This may prevent contact on setups where the default cleaning offset is too low, but residual material on the nozzle can affect later probing.

### Macros

- [`SET_GCODE_OFFSET`](https://www.klipper3d.org/G-Codes.html#set_gcode_offset) — applies an offset; the Forge-X wrapper also stores the selected value.
- `LOAD_GCODE_OFFSET` — loads and applies the last saved Forge-X Z offset.

### Example

```gcode
# Enable automatic loading
SET_MOD PARAM=load_zoffset VALUE=1

# Apply and save an offset
SET_GCODE_OFFSET Z=-0.2

# Apply without saving through the Forge-X wrapper
_SET_GCODE_OFFSET Z=0.25

# Change the saved value without applying it immediately
SET_MOD PARAM=z_offset VALUE=0.25
```

## Sound

Sound indications can be changed or disabled. MIDI files are stored in **Configuration → mod_data → midi**, and custom files can be uploaded to the same directory.

Related [configuration parameters](CONFIGURATION.md):

- `sound` — set to `0` to disable sound indications;
- `midi_on` — MIDI played at boot;
- `midi_start` — MIDI played when printing starts;
- `midi_end` — MIDI played when printing finishes.

### Playing MIDI Files

#### Usage

```gcode
PLAY_MIDI FILE=For_Elise.mid
```

`PLAY_MIDI` works only while `sound` is enabled.

## LED light Control

Set chamber-light brightness with:

```gcode
LED S=75
```

Use `LED_ON` and `LED_OFF` to toggle it. The selected brightness is saved in Forge-X variables and restored after Klipper restarts. New installations default to 50%.

For an inverted or non-standard LED connection, add an override to `user.cfg`:

```ini
[led chamber_light]
invert: False
initial_WHITE: 0.2
```

The Stock firmware normally controls the LED. To give Forge-X exclusive control:

```gcode
SET_MOD PARAM=disable_screen_led VALUE=1
```

When this is enabled, Stock-screen LED controls no longer work.

## Automation

Relevant [configuration parameters](CONFIGURATION.md):

- `stop_motor` — disables motors after inactivity;
- `auto_reboot` — controls automatic restart after a print;
- `close_dialogs` — dismisses Stock firmware dialogs according to the selected mode.

## Nozzle Cleaning

Forge-X provides several priming and cleaning controls:

- `zclear` — selects the purge-line algorithm;
- `disable_priming` — disables the purge line when set to `1`;
- `disable_cleaning` — disables nozzle cleaning before bed probing when set to `1`.

These are described in [Configuration](CONFIGURATION.md).

## Fixing Communication Timeout (E0011) / Move Queue Overflow (EO017) Error

The Stock Klipper build uses communication and move-queue values that can trigger E0011 or E0017 under some workloads. Enable the Forge-X patch with:

```gcode
SET_MOD PARAM=tune_klipper VALUE=1
```

Changing this parameter restarts the relevant printer services. Recreate the bed mesh and verify Z offset if other motion/config tuning was enabled at the same time.

## Reducing Resource Usage

Start by running `MEM` after boot and again while reproducing the problem. Try to keep memory use below roughly 75–80%. A *Timer too close*, E0011, or E0017 error is not automatically a memory problem: memory pressure, CPU contention, and MCU workload require different fixes.

### Built-in optimizations

Recent Forge-X versions apply baseline memory optimizations automatically:

- Moonraker and GuppyScreen limit glibc to two malloc arenas, avoiding several megabytes of unused per-thread heaps.
- The Forge-X camera implementation uses substantially less memory than the Stock camera service.

These changes reduce the baseline but cannot make an overloaded camera, browser UI, or third-party service free.

#### Switch to Feather Screen

The Stock screen normally uses around 10–20 MB of RAM, while Feather uses roughly 1–2 MB. Switching to Feather is usually the simplest way to reduce the baseline without losing local control.

#### Reduce Camera Resource Usage

Disable the camera, lower its resolution or frame rate, or use the Forge-X camera implementation. The Stock camera service is often one of the largest optional consumers.

#### Disable Moonraker

Moonraker can use around 30 MB. It is not required by the Stock screen or Feather during printing, but disabling it removes Fluidd/Mainsail and also stops integrations that depend on Moonraker.

To stop it before a print and restart it after a successful finish:

**First line of start G-code:**

```gcode
STOP_MOD
```

**Last line of end G-code:**

```gcode
START_MOD
```

Test the complete start, finish, cancel, and failure paths before relying on this for unattended printing. A cancelled or failed print may not execute the normal end G-code, leaving Moonraker stopped.

### Klipper timing and CPU contention

Use these settings for timing/MCU errors, not merely because memory usage is high:

- `tune_klipper` adjusts the Stock communication timeout and move-queue behavior for E0011/E0017:

  ```gcode
  SET_MOD PARAM=tune_klipper VALUE=1
  ```

- `klipper_rt` starts Klipper with low-priority real-time scheduling:

  ```gcode
  SET_MOD PARAM=klipper_rt VALUE=1
  ```

  Leave this off unless CPU contention is the suspected cause of *Timer too close* or MCU timeouts. It does not solve out-of-memory failures and cannot make an excessive MCU motion workload safe.

#### Disable SWAP (Only if Moonraker is Disabled)

This was the earlier recommendation for very small Stock/Feather-only setups. Current Forge-X supports selectable `MMC`, `USB`, `ZRAM`, and `OFF` modes, so disabling swap is no longer recommended as a general resource optimization. Use `OFF` only after verifying the complete workload, including calibration and recovery paths.

### Swap and compressed memory

Swap is a safety net, not additional free RAM. Available `use_swap` modes are:

- `MMC` — eMMC swapfile; the predictable default for memory-intensive or latency-sensitive Klipper operations.
- `USB` — swapfile on a prepared USB drive.
- `ZRAM` — compressed RAM-backed swap with lower-priority eMMC overflow; it can reduce slow storage I/O but consumes CPU and RAM for compression.
- `OFF` — disables swap.

Change the mode with, for example:

```gcode
SET_MOD PARAM=use_swap VALUE=MMC
SET_MOD PARAM=use_swap VALUE=ZRAM
SET_MOD PARAM=use_swap VALUE=OFF
```

Do not disable swap as a general optimization. A minimal print may work without it, while input-shaper calibration, camera use, or a larger web workload can still trigger an out-of-memory failure.

Feather automatically detects supported USB storage and exposes it as a separate file source. You can browse folders and start G-code directly from the drive. Avoid removing the drive while Feather is browsing it or while a print from USB is active.

To prepare a USB drive for Forge-X storage or swap, connect exactly one intended drive and run:

```gcode
PREPARE_USB
```

The preparation workflow is unavailable while printing. It erases and reformats the selected drive, so read both confirmation screens and verify the device before accepting them.
