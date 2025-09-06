# Printing

Printing starts and ends with `START_PRINT` and `END_PRINT` macros.  
You can pause and resume printing using the `PAUSE` and `RESUME` macros.  
To cancel a print, use the `CANCEL_PRINT` macro.    

If there is a pending temperature change operation (initiated by the mod only, Klipper's M109 and M190 won't work), you can cancel the wait using the `M108` macro.   
This cancels any active wait and execute `CANCEL_PRINT` if printing is active.

For detailed instructions on configuring your slicer, refer to the [Slicing](../docs/SLICING.md) section.

> [!WARNING]
> After installing the mod, some printer parameters may revert to stock or change. This can affect settings like Z-Offset and Mesh Bed Leveling. It is **strongly recommended** to review and recalibrate these settings to avoid potential damage.

## Using stock Firmware with mod

To use this mod with a stock screen, enable "LAN-mode". Some features require this setting, and WebUI/slicer interactions may be unstable without it.

Enable it on the stock screen: _Settings → Network → Network Mode → **LAN-mode**_

## Calibration

To calibrate the printer, use only these macros (or the Stock screen).  

> [!CAUTION]
> Read about [how bed mesh works](/docs/CALIBRATION.md#before-you-start) before trying to calibrate the printer.

> [!WARNING]  
> The Stock Screen doesn’t support the `SAVE_CONFIG` macro, which will cause freezing. A reboot is required afterward.
> Learn how to work around this [here](/docs/FAQ.md#stock-screen-freezes-i-cant-print-anything).

All of these macros are available in the Fluidd/Mainsail main screen in the section **Calibration**:


- `BED_LEVEL_SCREWS_TUNE`: Adjusts bed leveling screws (calculates adjustments for **nuts under the bed**).   
  ⚠️ **Recalibrate** the bed mesh after making changes.
  - `EXTRUDER_TEMP` temperature of the nozzle (default `130`)   
  - `BED_TEMP` temperature of the bed (default `80`)

- `AUTO_FULL_BED_LEVEL`: Bed meshing.  
  - `EXTRUDER_TEMP` temperature of the nozzle (default `240`)  
  - `BED_TEMP` temperature of the bed (default `80`)  
  - `PROFILE` profile to save (default `auto`)  

- `PID_TUNE_BED`: Bed PID calibration.  
  - `TEMPERATURE` temperature of the bed (default `80`)  

- `PID_TUNE_EXTRUDER`: Extruder PID calibration.  
  - `TEMPERATURE` temperature of the nozzle (default `245`)  

- `ZSHAPER`: Shaper calibration  

You can read more about Klipper calibration in the Klipper documentation: [https://www.klipper3d.org/](https://www.klipper3d.org/)

> [!NOTE]  
> You can't use the standard Klipper macro for calibration, since AD5M uses non-standard features, which need special preparation steps, and the default macro will not work as expected.  
> For example: the standard Klipper macro `BED_MESH_CALIBRATE` doesn’t perform the weight sensor reset, as it’s a non-standard step specific to AD5M, which may lead to weight exceed warnings or incorrect bed meshing altogether.

## Bed Mesh

The printer uses different bed meshes depending on the scenario:

- When using the Stock UI, the firmware will load the `MESH_DATA` profile.
- When using the Feather Screen, the mod will load the `auto` profile.
- When using the option to [force leveling](https://github.com/DrA1ex/ff5m/blob/main/docs/SLICING.md#parameters), the mod will save the mesh to the `default` profile. After the print is completed, the profile will be deleted.

> [!NOTE]  
> If no profile with the required name exists, the printer will perform leveling before the print begins.    
> Make sure to use the `SAVE_CONFIG` command after leveling to save the mesh properly.

## KAMP

Follow these steps to set up KAMP (Klipper Adaptive Meshing and Purging):

1. **Enable the Mod Parameter**  
   ```
   SET_MOD PARAM=use_kamp VALUE=1
   ```   
   Optionally, temporarily enable it via `START_PRINT`:  
   ```
   START_PRINT EXTRUDER_TEMP=[nozzle_temperature_initial_layer] BED_TEMP=[bed_temperature_initial_layer_single] FORCE_KAMP=1
   ```

2. **Enable "Exclude Objects" in Slicer**  
   - **Orca Slicer**: *Process Profile → Other → Exclude objects*  
   - **Prusa Slicer**: Go to *Print Settings → Output options → Label objects*, check the "Label objects"

3. **Modify Starting G-Code in Slicer**  
   Add this before the START_PRINT macro. Without it, you'll experience leveling and purging problems when printing supports, skirts, or other non-model objects:  
   - **For Orca**:  
     ```
     KAMP_DEFINE_AREA MIN={first_layer_print_min[0]},{first_layer_print_min[1]} MAX={first_layer_print_max[0]},{first_layer_print_max[1]}
     ```   
   - **For Prusa**: 
     ```
     KAMP_DEFINE_AREA MIN={min_x},{min_y} MAX={max_x},{max_y}
     ```

4. **Purging Notes**  
   *KAMP* defaults to `LINE_PURGE` instead of other cleaning algorithms. Avoid adding alternative algorithms (e.g., directly in starting G-code), as KAMP meshes a limited bed region, and default cleaning methods may damage the bed.  
   To disable priming entirely *(optional)*:  
   ```
   SET_MOD PARAM=disable_priming VALUE=1
   ```   

## Bed Collision Protection

To avoid bed scratching caused by the nozzle hitting the bed, the mod includes a collision detection feature.  
It is controlled by the following mod's [parameters](/docs/CONFIGURATION.md):
- `weight_check`: Enables or disables collision detection.
- `weight_check_max`: Sets the maximum tolerable weight (in grams).

For protection to work correctly without false triggers, ensure your bed’s weight sensor isn’t defective and shows accurate values when the bed is cold and after it’s warmed up.   
Some users experience weight sensor degradation, where the difference between a cold and warm bed can be 2-3 kg (2000-3000 g).  
Read this before enabling: [About bed pressure error](/docs/FAQ.md#why-am-i-getting-a-bed-pressure-detected-error), [About MCU shutdown](/docs/FAQ.md#why-am-i-getting-shutdown-due-to-sensor-value-exceeding-the-limit), [About 'endstop_state' error / Timer too close](/docs/FAQ.md#why-am-i-getting-mcu-shutdown-with-unable-to-obtain-endstop_state-response-or-timer-too-close-during-start_print)

> [!WARNING]
> Don’t set `weight_check_max` too low. Legitimate situations, such as the nozzle scratching an overextruded model or the weight of the model itself, can trigger false stops.  
> Over time, the bed's weight may also increase during long prints (weight of the model itself).

## Power Loss Recovery (Resurrection)

The mod includes a **Power Loss Recovery** feature that automatically saves print progress and can resume printing after an unexpected power loss or printer shutdown. This feature continuously monitors the print status and saves critical information to restore the exact position, temperatures, and settings.

Common scenarios where this is useful include:
- Unexpected power outages during long prints.
- Accidental printer shutdown or reboot.
- System crashes or MCU errors during printing.
- Voluntary pause and resume across reboots.

### Important Limitations and Considerations:

Due to the inherent limitations of how Klipper works and the mechanical nature of 3D printers, **perfect position restoration is not guaranteed**. This is especially true for Z-height positioning, which may not match exactly.

**Realistic Expectations:**
- This feature should be considered a **last resort** to salvage long prints rather than a reliable solution
- Position accuracy, especially Z-height, may be compromised
- The print may have visible artifacts at the recovery point
- Layer adhesion at the resume point may be weaker

**Mechanical Risks:**
- The part may have **detached from the bed** during power loss
- The part may have **shifted or moved** on the build plate
- Bed temperature changes may have affected adhesion
- Nozzle may have cooled and hardened, affecting first layer after recovery

**Safety Recommendations:**
- **Always monitor the recovery process closely**
- Be prepared to **stop the print immediately** if something goes wrong
- Consider the **part may be ruined** and recovery may not be worth the risk
- For reliable printing, **invest in a UPS (Uninterruptible Power Supply)** instead of relying on recovery

> [!CAUTION]
> Ensure your printer's mechanical state (bed level, nozzle cleanliness) hasn't changed between the power loss and recovery attempt. Manual intervention may be required. Always monitor this process, and if something goes wrong - power off the printer immediately!

> [!WARNING]
> **This is not a replacement for stable power!** The best solution is to prevent power loss with a UPS. Recovery should only be used as an emergency measure for very long prints where the time investment justifies the risk.

### How to Enable:
Enable the Power Loss Recovery feature using the mod parameter:
```bash
SET_MOD PARAM=power_loss_recovery VALUE=1
```

Optionally, you can customize the behavior by adding configuration to your `user.cfg` file:
```ini
[resurrection]
dump_time: 3.0
```

### Configuration Parameters:
- `power_loss_recovery`: Enable or disable the resurrection feature (mod parameter, default: 0)
- `dump_time`: How often to save the print state in seconds (user.cfg, default: 3.0)

### Available G-code Commands:
- `RESURRECT`: Manually trigger print recovery from the saved state
- `RESURRECT_ABORT`: Cancel any pending resurrection and delete the saved state file

### How it works:
- During printing, the mod continuously saves the current position, temperatures, feed rates, and other critical state information.
- The state is saved every `dump_time` seconds to minimize data loss.
- After a power loss, the mod detects the saved state file and offers to resume the print.
- A recovery dialog appears in Fluidd/Mainsail and GuppyScreen interfaces, allowing you to choose whether to resume or cancel the recovery.
- The recovery process restores extruder and bed temperatures, moves to the last known position, and continues printing.
- The mod intelligently handles various G-code commands and maintains compatibility with advanced features like bed mesh and KAMP.

### Recovery Process:
1. **Detection**: On startup, the mod checks for an existing resurrection state file.
2. **Dialog**: If a saved state is found, a recovery dialog will appear in Fluidd/Mainsail and GuppyScreen with options to resume or cancel.
3. **Verification**: The mod verifies that the saved state is valid and matches the last print.
4. **Preparation**: Temperatures are restored, and the printer moves to the saved position.
5. **Resume**: Printing continues from the exact point where it was interrupted.

> [!NOTE]
> Resurrection feature is only available for Feather Screen, Headless Mode, and Guppy Screen. Stock Screen has its own built-in power loss recovery system that the mod cannot override.

### State Information Saved:
- Current XYZ position and feed rates
- Extruder and bed temperatures
- Fans, Presure Advance, Speed limits 
- Active bed mesh and Z-offset
- G-code file position and progress

## Bed Mesh Validation

To prevent printing issues caused by an invalid bed mesh, the mod includes a **Bed Mesh Validation** feature. This feature checks the bed mesh before starting a print and ensures it matches the current printer configuration.   
Common scenarios where this is useful include:
- Using a bed mesh created for a different bed plate.
- Printing without a bed plate installed.
- Accidentally changing essential kinematics parameters that affect Z movement.

It is controlled by the following mod's [parameters](/docs/CONFIGURATION.md):
- `bed_mesh_validation`: Enable or disable bed mesh validation. Set to 1 to enable.
- `bed_mesh_validation_clear`: Enable or disable nozzle cleaning before bed mesh validation. Set to 1 to enable.
- `bed_mesh_validation_tolerance`: Set the maximum allowed Z-offset tolerance (in mm). The default value is 0.2.

> [!NOTE]
> Ensure the `bed_mesh_validation_tolerance` is set appropriately for your setup. A value too low may trigger false negatives, while a value too high may miss critical issues.

### How it works:
- Before starting a print, the mod compares the current bed mesh with the printer's expected Z movement.
- If the Z-offset exceeds the configured tolerance (`bed_mesh_validation_tolerance`), the print is canceled to prevent potential damage.
- A warning is logged, and the user is notified to recalibrate the bed mesh or check the printer configuration.

> [!NOTE]
> Bed Mesh Validation may produce false negatives if your nozzle is very dirty, as this can affect the accuracy of probing and the correct Z-offset position. Always ensure your nozzle is clean before starting a print.

## Z-Offset

In stock screen mode, Z-Offset is managed via the firmware’s screen. It’s automatically saved and loaded for the next print.

For the Feather screen, you can control Z-Offset using standard macros or Fluidd/Mainsail controls. It will be saved but not loaded automatically after a reboot.   
Enable the `load_zoffset` mod [parameter](/docs/CONFIGURATION.md) to make the mod automatically save and load Z-Offset after a reboot, like the stock firmware do.

Once `load_zoffset` is enabled, adjust Z-Offset through Fluidd or Mainsail’s standard controls (which use `SET_GCODE_OFFSET`). The mod will then save the Z-Offset to the configuration and load it automatically after a reboot, right before the print starts.

To use Z-offset during nozzle cleaning, set the `load_zoffset_cleaning` parameter.   
This can help prevent bed scratches if the default (0.0) offset is too low for your setup.

### Macros
- **[SET_GCODE_OFFSET](https://www.klipper3d.org/G-Codes.html#set_gcode_offset)**: Standard Klipper macro to apply Z-Offset; also saves the value to the mod’s parameter.  
- **`LOAD_GCODE_OFFSET`**: Loads and applies the last-saved Z-Offset from the mod’s parameter.


### Example
```
# Enable Z-offset loading
SET_MOD PARAM="load_zoffset" VALUE=1

# Set Z-offset (will be saved to `z_offset` mod parameter)
SET_GCODE_OFFSET Z=-0.2

# Set Z-offset (will NOT be saved to `z_offset` mod parameter)
_SET_GCODE_OFFSET Z=0.25

# Set saved Z-offset value (will not be applied immediately but will be loaded before print if `load_zoffset` is enabled)
SET_MOD PARAM="z_offset" VALUE=0.25
```

## Sound
You can customize sound indications or completely disable them. Additionally, you can configure MIDI playback for specific events. Available MIDI files are located in **Configuration -> mod_data -> midi**. You can also add your own MIDI files by uploading them to the **midi** folder.

It is controlled by the following mod's [parameters](/docs/CONFIGURATION.md):
- `sound`: Disable all sound indication by setting this parameter to 0.
- `midi_on`: Play MIDI when the printer boots.
- `midi_start`: Play MIDI when a print starts.
- `midi_end`: Play MIDI when a print finishes.

### Playing MIDI Files
You can play specific MIDI files using the **PLAY_MIDI** macro. This macro allows you to specify a MIDI file from the **midi** folder.

#### Usage
- Macro: `PLAY_MIDI`
- Parameter: `FILE` (string, default: `For_Elise.mid`)
- Example:
  ```plaintext
  PLAY_MIDI FILE=For_Elise.mid
  ```
> [!NOTE]
> The `PLAY_MIDI` macro will only function if the `sound` parameter is enabled.

## LED light Control

Use the `LED S=<PERCENT>` macro to set LED brightness (e.g., LED S=75).
Use `LED_ON` and `LED_OFF` to toggle the LED.

The mod also includes a LED klipper's plugin, which allows inverting LED controls in cases where the LED is connected using a non-standard scheme.   
To enable this feature, you need to add a parameter in the `user.cfg` file (see [Configuration](/docs/CONFIGURATION.md)).  

```ini
[led chamber_light]  
invert: False       ; Use inverted control when set to True (Default: False).
initial_WHITE: 0.2  ; Optional: Set the initial brightness value.
```  

> [!NOTE]
> The stock firmware controls the LED by default. You can disable this behavior by configuring the mod (as described in [Configuration](/docs/CONFIGURATION.md)).  
> - If left enabled, you won’t be able to manage the initial brightness using user.cfg.  
> - If disabled, LED control from the stock screen will no longer work.  


```bash
SET_MOD PARAM="disable_screen_led" VALUE=1
```

## Automation

It is controlled by the following mod's [parameters](/docs/CONFIGURATION.md):
- `stop_motor`: Automatically disables motors after inactivity.   
- `auto_reboot`: Reboots the printer after a print finishes.   
- `close_dialogs`: Automatically dismiss stock firmware dialogs after 20 seconds.   

## Nozzle Cleaning

The mod provides several options for priming line and nozzle cleaning before a print.   
These are controlled by the following [parameters](/docs/CONFIGURATION.md):
- `zclear`: Configure the purge line algorithm (e.g., `ORCA` - like Orca Slicer do).
- `disable_priming`: Set to 1 to disable nozzle priming before print.
- `disable_cleaning`: Set to 1 to disable nozzle cleaning before bed mesh calibration.

## Fixing Communication Timeout (E0011) / Move Queue Overflow (EO017) Error
In stock firmware, some internal Klipper parameters controlling **MCU Communication** and the **Move Queue**  are not optimally configured, which can cause the **E0011** and the **E0017** error.   

To fix this, enable the mod [parameter](/docs/CONFIGURATION.md):
```bash
SET_MOD PARAM="tune_klipper" VALUE=1
```

## Reducing Resource Usage

If you’re planning a long or complex print, it’s a waste of filament if it stops due to low resources.    
You can reduce resource usage to the bare minimum while ensuring printing still works correctly.

#### Switch to Feather Screen
The stock screen consumes 10-20 MB of RAM, while Feather uses only 1-2 MB.

#### Reduce Camera Resource Usage
Disable the camera, lower its resolution to the minimum, or switch to the mod’s camera implementation.   
The camera (especially controlled by stock firmware) uses significant memory.   
Switching to the mod’s camera can reduce usage by about 4x.

#### Disable Moonraker
Moonraker consumes around 30 MB of RAM. It’s not required for the stock screen or Feather, but disabling it means losing access to Fluidd/Mainsail.  

To disable Moonraker before printing and re-enable it afterward, modify your G-code:  
- **Starting G-code** (add as the first line):  
  ```
  STOP_MOD
  ```   
- **Ending G-code** (add as the last line):  
  ```
  START_MOD
  ```   

This stops Moonraker (and related services like Telegram bots or Discord notifications) before the print and restarts it after a successful finish. If you do this, consider disabling SWAP too (see below).

#### Disable SWAP (Only if Moonraker is Disabled)
You can disable *SWAP* completely if Moonraker is off — there’s enough memory for basic operations without it. However, printing might still trigger an out-of-memory error, and shaper calibration will likely be impossible without *SWAP*. Only disable *SWAP* alongside Moonraker, not as a standalone optimization.  
- **Disable SWAP until next reboot**:  
  ```
  SHELL CMD='swapoff -a'
  ```  
- **Disable SWAP permanently**:  
  ```
  SET_MOD PARAM=use_swap VALUE=OFF
  ```
