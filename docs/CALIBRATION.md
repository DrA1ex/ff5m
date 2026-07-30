# Calibration Guide for Flashforge Adventurer 5M (Pro) with Forge-X Firmware

This guide covers bed screws, bed mesh, Safe Z, Z offset, extruder rotation distance, PID, input shaper, axis dimensions, and skew correction.

## Disclaimer

Calibrate only while the printer is idle. An incorrect mesh, Z offset, or motion value can make the nozzle print in mid-air, scrape the build plate, or damage the printer. Recheck the relevant calibration after changing the nozzle, build plate, bed screws, motion settings, or any other part that changes the nozzle-to-bed geometry.

## Prerequisites

- Check belt tension and the printer's mechanical condition first.
- Clean the nozzle before probing the bed.
- Use accurate measuring tools for extruder and axis calibration.
- Back up the working configuration before changing values.
- If you enable `tune_klipper` or `tune_config`, do so before calibration. Recreate the bed mesh and verify Z offset afterward.
- Use the Stock screen for its normal calibration workflow. In Feather, open **Control → Calibration**. Guppy and Headless users can run the Forge-X macros from Fluidd or Mainsail.

> [!WARNING]
> On the Stock screen, use `NEW_SAVE_CONFIG`, not `SAVE_CONFIG` or `RESTART`. The latter commands can freeze the vendor application.

## Configuration Overrides

Store calibration overrides in `user.cfg` and back up the working configuration before changing them. Avoid editing generated or base configuration files when an override can be used instead.

## BEFORE YOU START

Forge-X uses printer-specific preparation around probing and calibration. Do not substitute generic Klipper probing macros such as `BED_MESH_CALIBRATE`: they do not perform all preparation required by the AD5M load-cell system.

The bed mesh represents the printer only in the state in which it was measured:

- Recreate it after changing the build plate, nozzle, bed screws, Z geometry, or relevant Klipper motion parameters.
- Measure it at a bed temperature close to the intended print temperature. The metal plate changes shape as it heats.
- Use the correct profile name:
  - Stock screen: `MESH_DATA`
  - Feather, Guppy, or Headless: `auto`
  - Temporary KAMP or forced-leveling mesh: `default`
- Do not save the temporary `default` profile. Forge-X removes it when it is no longer needed.

If a required persistent profile is missing, the print workflow may create a mesh automatically, depending on the selected settings. Treat that as a fallback rather than a replacement for verifying the mesh yourself.

To create the normal persistent mesh:

**Stock screen:**

```gcode
AUTO_FULL_BED_LEVEL PROFILE=MESH_DATA BED_TEMP=60 EXTRUDER_TEMP=220
NEW_SAVE_CONFIG
```

**Feather, Guppy, or Headless:**

```gcode
AUTO_FULL_BED_LEVEL PROFILE=auto BED_TEMP=60 EXTRUDER_TEMP=220
NEW_SAVE_CONFIG
```

Adjust the temperatures for the material and nozzle condition.

### Using different meshes for different materials:

You can create separate persistent profiles and select them through the `MESH` parameter of `START_PRINT`.

```gcode
START_PRINT EXTRUDER_TEMP=[nozzle_temperature_initial_layer] BED_TEMP=[bed_temperature_initial_layer_single] MESH=PLA_profile
```

Or use the OrcaSlicer filament type to select a profile such as `PLA_profile` or `ABS_profile`:

```gcode
START_PRINT EXTRUDER_TEMP=[nozzle_temperature_initial_layer] BED_TEMP=[bed_temperature_initial_layer_single] MESH={filament_type[0]}_profile
```

Make sure every referenced profile actually exists before relying on this setup.

## Bed Leveling Screws

1. Clean the nozzle and start the full preparation workflow:

   ```gcode
   BED_LEVEL_SCREWS_TUNE EXTRUDER_TEMP=220 BED_TEMP=60
   ```

2. Adjust the nuts under the bed as instructed.
3. Repeat the measurement until the remaining adjustment is acceptable:
   - In Feather, press **Repeat**.
   - From the console, use `BED_LEVEL_SCREWS_PROBE` only while the printer remains homed and at the required temperatures.
4. Recreate the bed mesh after changing the screws.
5. Save the result with `NEW_SAVE_CONFIG` when required.

If the bed-height variation was greater than about 1 mm, recalibrate the load-cell tare after the mechanical adjustment. See the [load-cell instructions in the FAQ](FAQ.md#resolving-the-issue-by-calibrating-the-load-cell). If the Stock screen is not active, you can temporarily boot it through `SKIP_MOD` to use the official FlashForge workflow.

## Bed Mesh Calibration

Run the Forge-X mesh workflow with the correct profile and realistic temperatures:

```gcode
AUTO_FULL_BED_LEVEL EXTRUDER_TEMP=220 BED_TEMP=60 PROFILE=auto
NEW_SAVE_CONFIG
```

Use `PROFILE=MESH_DATA` for the Stock screen. Inspect the resulting mesh in Fluidd or Mainsail and verify the first layer with a small test print.

## Safe Z calibration

`safe_z` is the absolute Z height used before lateral parking, cleaning, and calibration moves. It is not the same as Z offset or `park_dz`.

The default is 10 mm, but the real nozzle-to-bed clearance can be smaller after bed leveling or when a longer nozzle is installed. A value that exceeds the printer's real clearance can make a supposedly safe lateral move unsafe.

In Feather, **Control → Calibration → Z Offset** includes the guided Safe Z step. It probes the current geometry, lets you review the calculated clearance, and saves the selected value. Re-run it after changing the nozzle or bed setup.

From the console, set only a value you have physically verified:

```gcode
SET_MOD PARAM=safe_z VALUE=10
```

After changing Safe Z, verify a parking or cleaning move while ready to stop the printer.

## Z-Offset Calibration

The Stock screen manages and persists its own Z offset. In Feather, use **Control → Calibration → Z Offset** while the printer is idle. Follow the preparation and paper-test steps, review the selected result, and save it explicitly.

Feather also provides **Z Adjust** during a print. This changes the current runtime offset immediately but does not become the normal saved value until you press **Save**. The screen shows the saved value, current value, and unsaved difference.

On the AD5M:

- decreasing Z offset moves the bed closer to the nozzle;
- increasing Z offset moves the bed farther from the nozzle.

For Fluidd or Mainsail:

1. Print a small single-layer test.
2. Adjust in small increments with the standard controls or `SET_GCODE_OFFSET`.
3. Save the chosen value and enable automatic loading:

   ```gcode
   SET_GCODE_OFFSET Z=<value>
   SET_MOD PARAM=load_zoffset VALUE=1
   ```

4. Print another small first-layer test to confirm the result.

Optional: enable `load_zoffset_cleaning` if applying the saved offset during nozzle cleaning is necessary to prevent contact with your bed setup. Make sure the nozzle is clean before later probing.

## Extruder Calibration

Feather provides a guided 100 mm measurement under **Control → Calibration → Extruder**. It calculates the candidate `rotation_distance`, shows the change before applying it, and writes the accepted value to `user.cfg`.

For manual calibration:

1. Heat the nozzle to a suitable temperature and use relative extrusion:

   ```gcode
   M83
   M104 S220
   ```

2. Mark the filament, command 100 mm of extrusion, and measure the actual movement:

   ```gcode
   G1 E100 F100
   ```

3. Calculate:

   ```text
   new_rotation_distance = old_rotation_distance × measured_distance / 100
   ```

   Example: `4.38 × 98 / 100 = 4.292`.

4. Add the result to `user.cfg`:

   ```ini
   [extruder]
   rotation_distance: 4.292
   ```

5. Restart Klipper through the supported workflow and verify the result. Then calibrate slicer flow and pressure advance separately.

## PID Calibration

In Feather, open **Control → Calibration → PID** and follow the guided preparation, tuning, and confirmation steps. For manual use, choose temperatures representative of normal printing:

```gcode
PID_TUNE_EXTRUDER TEMPERATURE=220
PID_TUNE_BED TEMPERATURE=60
NEW_SAVE_CONFIG
```

After saving, check that both temperatures remain stable.

## Input Shaper Calibration

In Feather, open **Control → Calibration → Input Shaper** and follow the guided measurement workflow. For manual use, run:

```gcode
ZSHAPER
```

Inspect the generated graphs before accepting the result, then save it:

```gcode
NEW_SAVE_CONFIG
```

A ringing test print is useful for confirming the selected values.

## Axis Calibration Models

For a basic manual check, print large models that expose dimensional error:

- X/Y: a 200 × 200 × 0.2 mm square, one or two layers.
- Z: a tall hollow model, for example 20 × 20 × 200 mm with one wall, no infill, and no top layers.

Scale the models down if your measuring tool cannot accommodate them. A Calilantern or similar calibration model can combine dimensional and skew measurements in one print.

### Axis Calibration Steps

1. Measure the printed dimensions accurately.
2. Read the current `rotation_distance` from `printer.base.cfg` or `user.cfg`.
3. Calculate each required correction:

   ```text
   new_rotation_distance = current_rotation_distance × actual_size / expected_size
   ```

4. Add only the required overrides to `user.cfg`:

   ```ini
   [stepper_x]
   rotation_distance: 40.2

   [stepper_y]
   rotation_distance: 40.2

   [stepper_z]
   rotation_distance: 7.96
   ```

5. Restart through the supported workflow, recreate the bed mesh, and verify Z offset.

### Skew Distortion

Use a Calilantern or another suitable model to measure skew. Save the correction as `skew_profile`, because Forge-X expects that name:

```gcode
SET_SKEW XY=140.4,142.8,99.8 XZ=141.6,141.4,99.8 YZ=142.4,140.5,99.5
SKEW_PROFILE SAVE=skew_profile
NEW_SAVE_CONFIG
```

Enable automatic loading of the profile:

```gcode
SET_MOD PARAM=disable_skew VALUE=0
```

You can alternatively define the correction in a `[skew_correction]` section in `user.cfg`.

## Post-Calibration

1. Print a small first-layer test and a dimensional test model.
2. Recreate the bed mesh after any later change that affects motion or bed geometry.
3. Back up the confirmed working configuration.
4. Recheck belt tension and first-layer behavior periodically.
