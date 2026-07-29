# Calibration

Calibrate only on an idle printer. A wrong mesh or Z offset can damage the
nozzle or bed. After changing the nozzle, build plate, motion settings, or bed
screws, recheck the mesh and first layer.

Use the Stock screen for its normal calibration flow. In Feather, open
**Control → Calibration**. Headless and Guppy users can run the Forge-X macros
from Fluidd or Mainsail. Do not use generic Klipper probing macros: Forge-X
macros include the preparation required by this printer.

> [!WARNING]
> On the Stock screen, use `NEW_SAVE_CONFIG`, never `SAVE_CONFIG` or `RESTART`.
> The latter commands can freeze the vendor application.

## Bed screws and mesh

1. Clean the nozzle and run bed-screw tuning:

   ```gcode
   BED_LEVEL_SCREWS_TUNE EXTRUDER_TEMP=220 BED_TEMP=60
   ```

2. Adjust the screws as directed and repeat the measurement until satisfied.
   In Feather, use **Repeat**. From the console, use
   `BED_LEVEL_SCREWS_PROBE` only while the printer is still homed and warm.
3. Create a mesh at the temperature used for the material:

   ```gcode
   AUTO_FULL_BED_LEVEL EXTRUDER_TEMP=220 BED_TEMP=60 PROFILE=auto
   NEW_SAVE_CONFIG
   ```

Use profile `MESH_DATA` with the Stock screen and `auto` with Feather, Guppy,
or Headless. A temporary KAMP mesh is `default`; do not save it.

## Z offset

The Stock screen manages its own Z-offset workflow. In Feather use
**Control → Calibration → Z Offset**: follow the on-screen preparation and
paper-test steps, then save the selected result. Re-run Safe Z after changing
the nozzle or bed setup.

For Fluidd/Mainsail, adjust carefully in small steps and save the chosen value:

```gcode
SET_GCODE_OFFSET Z=<value>
SET_MOD PARAM=load_zoffset VALUE=1
```

Confirm the result with a small single-layer test print. Feather's **Live Z
Offset** is for a small correction during a print; save it deliberately if it
should become the normal value.

## Extruder, PID, and input shaper

- Feather: **Control → Calibration → Extruder** guides the 100 mm extruder
  measurement and saves the result. Then calibrate flow and pressure advance.
- From the console, use the normal measurement procedure and put the calculated
  `rotation_distance` in `user.cfg`.
- PID tuning:

  ```gcode
  PID_TUNE_EXTRUDER TEMPERATURE=220
  PID_TUNE_BED TEMPERATURE=60
  NEW_SAVE_CONFIG
  ```

- Input shaper:

  ```gcode
  ZSHAPER
  NEW_SAVE_CONFIG
  ```

Inspect the generated graphs before accepting input-shaper results.

## Axis and skew calibration

Use a suitable calibration model, measure it accurately, and update only the
needed `rotation_distance` values in `user.cfg`. Recheck the bed mesh after
changing motion dimensions. Save a skew profile as `skew_profile` and enable it
with `SET_MOD PARAM=disable_skew VALUE=0`.

## After calibration

Print a small test model, check the first layer, and keep a backup of the
working configuration. For macro behavior and implementation details, see the
[engineering calibration notes](../openwiki/workflows/configuration-and-printing.md#calibration-and-safety).
