# Slicing

To use the mod, you need to replace your slicer's default start and end G-code with the following configurations. These snippets are for _OrcaSlicer_. If you use a different slicer, adjust the placeholders accordingly.

### For Stock Screen

Start Gcode
```
START_PRINT EXTRUDER_TEMP=[nozzle_temperature_initial_layer] BED_TEMP=[bed_temperature_initial_layer_single]
M190 S[bed_temperature_initial_layer_single]
M104 S[nozzle_temperature_initial_layer]
```

End Gcode
```
END_PRINT
```

### For Alternative Screen (Feather Screen)

Start Gcode
```
START_PRINT EXTRUDER_TEMP=[nozzle_temperature_initial_layer] BED_TEMP=[bed_temperature_initial_layer_single]
```

End Gcode
```
END_PRINT
```

### Configuring Moonraker / Klipper connection

To connect to the printer, use the following physical printer settings:
* Host type: `moonraker`, `klipper` or `klipper (via moonraker)`
* Hostname, IP or URL: `<printer_ip>:7125`



## START_PRINT
The `START_PRINT` macro is used to initialize the printing process with customizable parameters. It allows you to configure the extruder temperature, bed temperature, and various leveling options to suit your specific needs.

### Parameters

- `EXTRUDER_TEMP`: Sets the extruder temperature for the print. This is typically configured by the slicer.  
  **Default**: 245  
  **Example**: `EXTRUDER_TEMP=230`  

- `BED_TEMP`: Sets the bed temperature for the print. This is also typically configured by the slicer.  
  **Default**: 80  
  **Example**: `BED_TEMP=60`  

- `FORCE_KAMP`: Forces the KAMP (Klipper Adaptive Meshing and Purging) bed leveling process if set to 1.  
  **Default**: 0  
  **Example**: `FORCE_KAMP=1`  

- `FORCE_LEVELING`: Forces the bed leveling process if set to 1.  
  **Default**: 0  
  **Example**: `FORCE_LEVELING=1`  

- `SKIP_LEVELING`: Skips the bed leveling process if set to 1.  
  **Default**: 0  
  **Example**: `SKIP_LEVELING=1`  

- `MESH`: Loads specific mesh instead of default one.
  **Default**: None  
  **Example**: `MESH=PLA_profile`

## MD5 Checksum Validation

One of the main reasons for print failures on the AD5M printer is corrupted G-code files after sending them over the network. To prevent this issue, the mod implements MD5 checksum validation before starting a print. If a file has a corrupted checksum, the print will be canceled automatically, and the G-code file will be deleted to prevent accidentally printing it again.

### Enabling MD5 Checksum Validation

To use MD5 checksum validation, you need to add an MD5 checksum to your G-code file. This can be done by adding a post-processing script in your slicer.

#### Downloading the Script

You can download the required script from the repository:
- For Windows: [addMD5.bat](/addMD5.bat)
- For Linux/Mac: [addMD5.sh](/addMD5.sh)

> [!NOTE]  
> Ensure the script has the correct **permissions** to execute (on *Linux/Mac*).   
> You may need to run `chmod +x ./addMD5.sh` to make it executable.   
> For macOS, if the script doesn’t run, remove the quarantine flag with:   
> `xattr -d com.apple.quarantine ./addMD5.sh`

#### Configuring the Script in OrcaSlicer

1. **Download the Script**: Save the appropriate script (addMD5.bat for Windows or addMD5.sh for Linux/Mac) to your computer.

2. **Open OrcaSlicer**: Launch OrcaSlicer and go to Print profile > Other.

3. **Add Post-Processing Script**:
   - Find the Post-Processing Scripts section.
   - Enter the full path to the downloaded script (e.g., C:\path\to\addMD5.bat for Windows or /path/to/addMD5.sh for Linux/Mac).
   - Save Profile settings for future prints.

4. **Slice and Export**: When you slice a model and export the G-code, the script will automatically add the MD5 checksum to the file.

### How It Works
- The script calculates the MD5 checksum of the G-code file and appends it to the file as a comment.
- Before starting a print, the mod verifies the checksum. If the checksum does not match, the file is considered corrupted, and the print is canceled.

### Notes
- If the checksum validation fails, the G-code file will be deleted to prevent accidental use in the future.
- This feature is enabled by default in the mod but requires the MD5 checksum to be added to the G-code file for it to work.
