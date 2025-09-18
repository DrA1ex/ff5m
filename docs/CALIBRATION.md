# Calibration Guide for Flashforge Adventurer 5M (Pro) with Forge-X Firmware

This concise guide provides instructions for calibrating the axes, extruder, bed mesh, input shaper, and PID settings of your Flashforge Adventurer 5M or 5M Pro running the Forge-X firmware mod. For advanced calibration, use the Calilantern model.

## Disclaimer
This AI-generated guide is based on Forge-X documentation and general 3D printing practices. Verify settings with official Forge-X resources for your setup.

## Prerequisites
- **Belt Tension**: Ensure belts are tensioned (check YouTube/Flashforge guides).
- **Klipper Tuning**: Enable with `SET_MOD PARAM=tune_klipper VALUE=1`.
- **Config Tuning**: Enable with `SET_MOD PARAM=tune_config VALUE=1`.
- **Tools**: Caliper/Ruler, Fluidd/Mainsail access.

## Configuration Overrides
Edit `user.cfg` via Fluidd/Mainsail (port 80) or manually. Backup config using Forge-X Backup and Restore before changes.

**Notice**: For STOCK screen users, use `NEW_SAVE_CONFIG` instead of `SAVE_CONFIG` or `RESTART` to save changes.

## BEFORE YOU START

First, it’s important to understand how calibration works. This is crucial because misunderstanding can lead to printer damage.

If you’re using the Stock screen, you can perform calibrations directly through the screen as usual — Forge-X adds new features without altering existing ones.

However, if you want to go further, here’s what you need to know:

- The printer uses a bed mesh to know where to print. If the mesh doesn’t match your setup (e.g., servo configuration or nozzle height), the printer may print in mid-air, scrape the bed, or even cause hardware damage (broken nozzle, burnt servos or drivers, mechanical issues).
- The bed mesh only reflects the state when it was taken. If you change anything affecting movement — such as the bed plate, nozzle, or Klipper motion parameters (like `tune_klipper`) — you must redo the mesh.
- The printer’s build plate is metal and expands when heated. Always take the mesh at the temperature you plan to print with. For example:
  - 60°C for PLA or 70°C for PETG: a mesh at 65°C may work, but for 40°C or 80°C, you’ll need separate meshes.
- Bed meshes are stored with specific names. It’s not enough to create a mesh — you must save it with the correct name. Saving with the wrong name after changes can lead to printer damage.
  - For Stock screens, name the mesh **`MESH_DATA`**
  - For non-Stock screens (Feather, Guppy, Headless), name it **`auto`**
- If no mesh is found, the printer will generate one before printing, but I strongly recommend managing this yourself.
- Temporary meshes (like from KAMP or force-leveling) are saved as **`default`**. You don’t need to save these. If Fluidd or Mainsail prompts you to save, ignore it — the firmware will delete this profile automatically.

To calibrate the bed mesh using Klipper, run:

**For Stock screen:**
```bash
AUTO_FULL_BED_LEVEL PROFILE=MESH_DATA BED_TEMP=65
```

**For other screens:**
```bash
AUTO_FULL_BED_LEVEL PROFILE=auto BED_TEMP=65
```

### Using different meshes for different materials:
Create slicer profiles with different `START_PRINT` g-code variations.

**Example (using `MESH` parameter):**

```gcode
START_PRINT EXTRUDER_TEMP=[nozzle_temperature_initial_layer] BED_TEMP=[bed_temperature_initial_layer_single] MESH=PLA_profile
```

**Or use Orca’s variables to set the mesh name by filament type:**   
The printer will look for a profile named **`<filament_type>_profile`** (e.g., `PLA_profile`, `ABS_profile`):

```gcode
START_PRINT EXTRUDER_TEMP=[nozzle_temperature_initial_layer] BED_TEMP=[bed_temperature_initial_layer_single] MESH={filament_type[0]}_profile
```

---

## Bed Leveling Screws
1. **Prepare**:
   - Run `CLEAR_NOZZLE` to ensure the nozzle is clean.
2. **Run**:
   ```
   BED_LEVEL_SCREWS_TUNE EXTRUDER_TEMP=130 BED_TEMP=60
   ```
   - Adjust screws per instructions.
   - Repeat `BED_LEVEL_SCREWS_TUNE` until values are adequate.
3. **Check Load Cell**:
   - If your bed height variation exceeds 1 mm, you must perform load cell tare calibration after adjusting the bed screws (see [Forge-X FAQ](https://github.com/DrA1ex/ff5m/blob/main/docs/FAQ.md#resolving-the-issue-by-calibrating-the-load-cell)).
4. **Recalibrate Load Cells**: Follow the official Flashforge [guide](https://docs.google.com/document/d/1Oou4A56g5HTrxBAMoH-bTnTZZ3IZyGr_3jL9tUYYiow/edit?usp=drivesdk). If you're not using the Stock screen, temporarily reload it with `SKIP_MOD`.   
5. **Recalibrate Mesh** ⚠️
6. **Save**: `NEW_SAVE_CONFIG`.

---

## Bed Mesh Calibration
1. **Run**:
   ```
   AUTO_FULL_BED_LEVEL EXTRUDER_TEMP=220 BED_TEMP=60 PROFILE=auto
   ```
   - Adjust `EXTRUDER_TEMP` (e.g., 220°C for PLA), `BED_TEMP` (e.g., 60°C).
   - Use correct mesh `PROFILE` for your setup (`MESH_DATA` - for Stock or `auto` - for others)
2. **Save**:
   ```
   NEW_SAVE_CONFIG
   ```

---

## Extruder Calibration
1. **Extrude Filament**:
   - Heat nozzle:
     ```
     M104 S220; Set nozzle to 220°C (adjust for filament)
     ```
   - Extrude 100 mm:
     ```
     G1 E100 F100
     ```
   - Mark 100 mm filament, measure actual extrusion (e.g., 98 mm).
2. **Calculate**:
   - Get current `rotation_distance` (e.g., `4.7`).
   - Formula: `new_rotation_distance = old_rotation_distance * (measured_distance / expected_distance)`
     - E.g., `4.7 * (98 / 100) ≈ 4.6`
3. **Update**:
   - Add to `user.cfg`:
     ```
     [extruder]
     rotation_distance: 4.6
     ```
   - Run `NEW_SAVE_CONFIG`.
4. **Note**: `tuning.cfg` has a near-accurate baseline.

---

## Input Shaper Calibration
1. **Run**:
   ```
   ZSHAPER
   ```
   - Plots generated in Fluidd/Mainsail.
2. **Save**:
   ```
   NEW_SAVE_CONFIG
   ```
3. **Verify** (Optionally): Print ringing test model.

---

## Axis Calibration Models

You have two calibration options:   
1. Simple method: Follow this basic guide   
2. Advanced method: Use calibration models that measure both axis dimensions and skew in a single print   

Follow these steps for manual calibration if you don't have (or prefer not to use) the Calilantern model:   

1. **X/Y Calibration**:
   - In slicer (e.g., PrusaSlicer/OrcaSlicer), create a 200x200x0.2 mm flat square (1-2 layers).
   - Export G-code, print via Fluidd/Mainsail.
2. **Z Calibration**:
   - In Slicer, cylinder a 20x20x200 mm hollow rectangle (no infill, no top layers, 1 wall).
   - Export G-code and print via Fluidd/Mainsail.
  
**Note:** If your measurement tools (e.g., calipers or ruler) cannot accommodate this size, scale the model (X/Y and Z axes accordingly) to match your equipment's capacity.

### Axis Calibration Steps
1. **Measure**:
   - X/Y: Measure printed square (e.g., 201x201 mm).
   - Z: Measure height (e.g., 199 mm).
2. **Calculate Rotation Distance**:
   - Get current `rotation_distance` from `printer.base.cfg`/`user.cfg`.
   - Formula: `new_distance = current_distance * (actual_size / expected_size)`
     - E.g., X/Y: `40 * (201 / 200) ≈ 40.2`, Z: `8 * (199 / 200) ≈ 7.96`
3. **Update**:
   - Add to `user.cfg`:
     ```
     [stepper_x]
     rotation_distance: 40.2
     [stepper_y]
     rotation_distance: 40.2
     [stepper_z]
     rotation_distance: 7.96
     ```
   - Run `NEW_SAVE_CONFIG` (or `SAVE_CONFIG` for non-STOCK screens).

### Skew Distortion
- Use the Calilantern model (or similar) to measure and correct skew. Upload the model via Fluidd/Mainsail, print, and follow its instructions for skew compensation. The printer will load a profile named `skew_profile` automatically, so save the profile with this name:
  ```
  SET_SKEW XY=140.4,142.8,99.8 XZ=141.6,141.4,99.8 YZ=142.4,140.5,99.5
  SKEW_PROFILE SAVE=skew_profile
  NEW_SAVE_CONFIG
  ```
- Alternatively, add a `[skew_correction]` section in `user.cfg` with the skew values, then run `NEW_SAVE_CONFIG` to save the configuration.
- Set `disable_skew` mod parameter to '0', to automatically apply `skew_profile` before print:
  ```
  SET_MOD PARAM="disable_skew" VALUE=0
  ```

---

## PID Calibration
1. **Hotend**:
   ```
   PID_TUNE_EXTRUDER TEMPERATURE=220
   ```
   - Adjust `TEMPERATURE` (e.g., 220°C for PLA).
2. **Bed**:
   ```
   PID_TUNE_BED TEMPERATURE=60
   ```
   - Adjust `TEMPERATURE` (e.g., 60°C for PLA).
3. **Save**: `NEW_SAVE_CONFIG`.
4. **Verify**: Check temperature stability in Fluidd/Mainsail.

---


## Z-Offset Calibration
Calibrate Z-offset to ensure proper first-layer adhesion. For STOCK screen, Z-offset is managed via the firmware’s screen and auto-saved/loaded. For Feather/Headless/Guppy screen, use the following steps with Fluidd/Mainsail.

1. **Verify Z-Offset**:
   - Print a 200x200x0.2 mm single-layer square (create in slicer, export G-code, print via Fluidd/Mainsail).
   - Compare to online reference images (e.g., Klipper documentation or 3D printing forums). If first-layer quality is good, no adjustment needed.
2. **Adjust Z-Offset**:
   - If lines are too squished (over-extruded), increase Z-offset (e.g., `SET_GCODE_OFFSET Z=0.05` for +0.05 mm).
   - If lines are too loose (under-extruded), decrease Z-offset (e.g., `SET_GCODE_OFFSET Z=-0.05` for -0.05 mm).
3. **Test Smaller Model**:
   - Print a 50x50x0.2 mm single-layer square.
   - Check first-layer quality and adjust Z-offset again if needed.
4. **Repeat**:
   - Repeat steps 2-3 until first-layer quality is satisfactory.
5. **Save Z-Offset**:
   - Apply and save Z-offset:
     ```
     SET_GCODE_OFFSET Z=<value>
     ```
     - E.g., `SET_GCODE_OFFSET Z=-0.2` for -0.2 mm.
6. **Enable Auto-Load**:
   - Enable automatic Z-offset loading:
     ```
     SET_MOD PARAM="load_zoffset" VALUE=1
     ```
   - This ensures Z-offset is loaded before prints and after reboots, similar to STOCK screen behavior.
7. **Optional**: For nozzle cleaning, enable `load_zoffset_cleaning` it may prevent bed scratches:
   ```
   SET_MOD PARAM="load_zoffset_cleaning" VALUE=1
   ```
   - After cleaning with Z-Offset, ensure the nozzle is thoroughly clean - residual material may affect subsequent bed meshing   
8. **Verify**: Print another 50x50x0.2 mm square to confirm.

---

## Post-Calibration
1. **Verify**: Print Calilantern/50x50x50 mm cube.
2. **Recalibrate Mesh** after changes.
3. **Backup**: Use Forge-X Backup and Restore.
4. **Maintenance**: Recheck belt tension periodically.
