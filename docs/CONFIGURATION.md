## Mod Configuration

The mod uses its own global configuration management system. The configuration file is located in Fluidd under Configuration -> mod_data -> variables.cfg.

However, manually editing this file is not recommended. Parameters alone don't directly control behavior; special scripts handle parameter changes and perform the necessary actions. Instead, use the provided macros to modify settings.

### Configuration Macros
- `LIST_MOD_PARAMS`: Lists all available mod parameters and their current values. Use this to explore the full range of configurable settings.
- `GET_MOD PARAM=<name>`: Retrieves the current value of a specific parameter.
- `SET_MOD PARAM=<name> VALUE=<value>`: Sets a new value for a specific parameter.

### Parameters Overview
The mod supports a wide range of parameters to customize printer behavior. Below is a summary of key parameters. For a complete and up-to-date list, always use the `LIST_MOD_PARAMS` macro.

#### Key Parameters
- **`auto_reboot`**: Configures automatic restart behavior.  
  - `OFF`: Disabled  
  - `SIMPLE_90`: Restarts after 1.5 minutes  
  - `FIRMWARE_90`: Firmware restarts after 1.5 minutes  

- **`close_dialogs`**: Controls dialog timeout behavior.   
  - `OFF`: Dialogs remain open   
  - `SLOW`: Closes after 20s (GDB method, may not work in all firmware versions)     
  - `FAST`: Closes after 20s (API method, requires [LAN-mode](/docs/PRINTING.md#using-stock-firmware-with-mod))    

- **`disable_priming`**: Disables nozzle cleaning by line if set to `1`.  

- **`disable_screen_led`**: Allows the mod to control the screen LED if set to `1`.  

- **`disable_skew`**: Disables skew correction if set to `1`.  

- **`block_cloud`**: Blocks the stock FlashForge cloud / telemetry endpoints (Flash Studio / MQTT, VoxelShare, stock OTA, NetEase IM) if set to `1`. Default `0` (off) — leave disabled if you use FlashForge cloud, video streaming or model sharing. Blocking is done at name-resolution level (`/etc/hosts`) only. See [FIRMWARE_5x_COMPAT.md](/docs/FIRMWARE_5x_COMPAT.md#hosts-blocked) for the host list and what each one does. Full air-gap additionally requires router-level blocking of AlibabaCloud OSS upload URLs.  

- **`tune_klipper`**: Enables a fix for Communication Timeout (E0011) / Move Queue Overflow (EO017) errors if set to `1`.  

- **`klipper_rt`**: Runs the Klipper host under real-time scheduling (`SCHED_RR`, priority 5) if set to `1`. Default `0` (off). On the dual-core T113, Klipper otherwise competes with Moonraker, the web UI and the camera for CPU and can be preempted long enough to fall behind feeding the MCU, causing *Timer too close* / MCU timeout underruns under load. With this enabled the klippy process and all its threads preempt normal tasks. Safe by design: the low priority plus the kernel RT throttle (95% of a core) prevents a runaway from locking the system. Targets CPU-contention underruns, not memory-pressure stalls.  

- **`check_md5`**: Enables MD5 checksum verification for G-code files.  
  **Note**: Requires a [post-processing script](/docs/SLICING.md#md5-checksum-validation) in your slicer. Scripts are available in *Configuration → mod* (`addMD5.sh` or `addMD5.bat`).  

- **`use_kamp`**: Enables KAMP (Klipper Adaptive Meshing and Purging) if set to `1`.  

- **`camera`**: Enables the alternative camera implementation if set to `1`.  

- **`filament_switch_sensor`**: Enables pause on filament runout if set to `1`.  
  **Note**: Only works with Feather/Headless screen.   

- **`weight_check`**: Enables bed collision protection if set to `1`.  

- **`bed_mesh_validation`**: Enables bed mesh validation protection if set to `1`.  

- **`tune_config`**: Enables firmware parameter tuning for optimized settings (motors, extruder rotation distance, probing, Z-parking, etc.).  
  **Warning**: After enabling, recreate the bed mesh, adjust Z-offset, and optionally recalibrate flow and Pressure Advance.  
  See changed parameters here: [tuning.cfg](/tuning.cfg)

## Backup Management

The mod includes a backup and restore system for the printer's configuration (`printer.base.cfg`). This is essential because stock firmware updates can alter critical parameters, such as `rotation_distance` for steppers, which may lead event to printer damage if not corrected.
To prevent this, the mod provides a backup management system.

### Configuring Backup Parameters

You can define which parameters or sections to include in the backup by editing the file located in Fluidd under **Configuration -> mod_data -> backup.params.cfg**.  
The syntax is straightforward:

```cfg
[include /path/to/conf.cfg]        # Add an include if it's missing
[include /path/to/conf.cfg] defer  # Add an include at the end of the file if it's missing
[section name]                     # Backup the entire section with all its parameters
[section name] parameter_name      # Backup only this specific parameter within the section

-[include /path/to/conf.cfg]       # Remove an include if it's present
-[section name]                    # Remove the entire section from the config
-[section name] parameter_name     # Remove a specific parameter from the section
```

### Backup & Restore

#### Klipper configuration Backup & Restore:

##### Features
- **Automatic Restore**: The most recent backup is automatically restored during the printer's boot process.
- **Manual Backup**: Backups are not created automatically. To create a backup, you must manually run the `CONFIG_BACKUP` macro.

##### Purpose
This feature is useful if you’ve tuned parameters in `printer.base.cfg`. When updating the stock firmware, some of these parameters may revert to their defaults. By using this backup and restore system, you can ensure your custom changes are not lost and are restored during boot.

##### Macros
The following macros control this functionality:
- **`CONFIG_BACKUP`**: Creates a backup of the current Klipper configuration.
- **`CONFIG_RESTORE`**: Restores the Klipper configuration from the most recent backup.
- **`CONFIG_VERIFY`**: Checks if the Klipper configuration has changed since the last backup.

#### Mod & Klipper configuration Backup & Restore

To preserve all your custom parameters and configurations within the mod, you can create a full backup archive. This allows you to restore the mod to its last state, even if it has been completely removed.

##### Macro
- **`TAR_BACKUP`**: Creates a `.tar` archive containing all printer and mod configuration files for easy storage, transfer, and restoration.

##### Backup Process
1. Run the `TAR_BACKUP` macro.
2. Download the generated archive from:  
   **Fluidd → Configuration → mod_data → `debug_<date>.tar.gz`**

##### Restore Process
1. Unpack the `.tar` archive on your computer.
2. Drag and drop all extracted files as-is into:  
   **Fluidd → Configuration**
3. Reboot the printer to apply the restored configuration.

## User-Defined Parameters

The mod allows you to customize and extend functionality by defining your own macros or overriding existing printer parameters. This override any printer configuration, including `tuning.cfg`. Additionally, you can adjust Moonraker-specific parameters.

> [!NOTE]
> Changes to `user.cfg` and `user.moonraker.conf` are applied after a restart or configuration reload.

### Customizing Printer Parameters

To add or override printer parameters, edit the file located in Fluidd under **Configuration -> mod_data -> user.cfg**.  
This file allows you to:

- Define new macros.
- Override existing parameters (e.g., from tuning.cfg).
- Add custom configurations.

**Example:**

```cfg
[gcode_macro MY_CUSTOM_MACRO]
description: My custom macro
gcode:
  M117 Hello, World!

[stepper_x]
rotation_distance: 40  # Override the rotation_distance for the X stepper
```

### Customizing Moonraker Parameters
To modify Moonraker-specific settings, edit the file located in Fluidd under **Configuration -> mod_data -> user.moonraker.conf**.  
This file allows you to adjust Moonraker behavior, such as API settings, notifications, or plugins.

**Example:**

```cfg
[authorization]
force_logins: true  # Require login for all users

[notifier]
url: http://example.com/notify  # Custom notification endpoint
```
